"""capture -> plan -> verify -> ground -> act -> record. Nothing is executed unless the plan verified."""
import asyncio
import time
from dataclasses import dataclass

from urllib.parse import urlparse

from . import grounder, screen
from .egress import PLANNER_BASE_URL, check_atom_url
from .planner import Planner, PlannerUnverified, sha256_hex
from .receipts import Run

SETTLE_SECONDS = 0.9
WORK_AREA = screen.Region(0, 0, 1400, 1080)


@dataclass
class Result:
    status: str
    summary: str
    steps: int
    run_dir: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class GuardBlocked(Exception):
    def __init__(self, what: str, reason: str):
        super().__init__(reason)
        self.what, self.reason = what, reason


class Guard:
    """Fail-closed checks that the work window, owned by work_pid, is what the agent sees and touches."""

    def __init__(self, work_pid: int, region: screen.Region, allow: frozenset[str] | None = None):
        self.pid, self.region, self.allow = work_pid, region, allow

    def check_region(self):
        return screen.guard_region(self.region, self.pid, self.allow)

    def check_point(self, x, y):
        return screen.guard_point(x, y, self.pid, self.allow)

    def check_keys(self):
        return screen.guard_keyboard(self.pid)

    def passes(self, what: str, check) -> None:
        """Run check; on failure bring the work window to the front once, wait, re-check; raise if still failing."""
        reason = check()
        if reason is None:
            return
        screen.bring_to_front(self.pid)
        time.sleep(0.5)
        reason = check()
        if reason is not None:
            raise GuardBlocked(what, reason)

    def strict(self, what: str, check):
        """A callable for the instant before an event is posted: no retry, raises if the check fails."""
        def go():
            reason = check()
            if reason is not None:
                raise GuardBlocked(what, reason)
        return go


def _png_size(png: bytes) -> tuple[int, int]:
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


def _rephrase(target: str) -> str:
    return "click on " + target.removeprefix("the ").removeprefix("The ")


def _ground(target: str, png: bytes, step: int, run: Run) -> dict | str:
    """Atom, with one retry on a rephrased target. Returns the result or an error string."""
    last = ""
    for instruction in (f"click {target}", _rephrase(target)):
        try:
            check_atom_url(grounder.ATOM_URL)  # raises EgressError before anything is sent
            run.event("screenshot_sent", step, to="atom-local", host=urlparse(grounder.ATOM_URL).netloc, bytes=len(png))
            result = grounder.ground(instruction, png)
        except grounder.GroundingError as error:
            last = str(error)
            continue
        run.event("grounded", step, grounding={
            "model": "Atom tinyclick-4bit-v1", "where": "127.0.0.1:8760", "instruction": instruction,
            "pixel": result["pixel"], "duration_ms": result.get("duration_ms")})
        return result
    return last


def _act(action: dict, png: bytes, step: int, run: Run, region: screen.Region, guard: Guard) -> str:
    kind = action["action"]
    if kind == "click":
        found = _ground(action["target"], png, step, run)
        if isinstance(found, str):
            return f"FAILED: could not locate {action['target']!r} ({found}); nothing was clicked"
        width, _ = _png_size(png)
        scale = found["image"]["width"] / region.width if found.get("image") else width / region.width
        x, y = region.x + found["pixel"]["x"] / scale, region.y + found["pixel"]["y"] / scale
        guard.passes("click", lambda: guard.check_point(x, y))
        screen.click(x, y, before_press=guard.strict("click", lambda: guard.check_point(x, y)))
        return f"clicked ({round(x)}, {round(y)})"
    if kind == "type":
        guard.passes("type", guard.check_keys)
        screen.type_text(action["text"], before_each=guard.strict("type", guard.check_keys))
        if action.get("enter"):
            screen.press("enter", before=guard.strict("type", guard.check_keys))
        return f"typed {len(action['text'])} characters" + (" and pressed enter" if action.get("enter") else "")
    if kind == "key":
        guard.passes("key", guard.check_keys)
        screen.press(action["key"], before=guard.strict("key", guard.check_keys))
        return f"pressed {action['key']}"
    if kind == "scroll":
        sx, sy = region.x + region.width / 2, region.y + region.height / 2
        guard.passes("scroll", lambda: guard.check_point(sx, sy))
        screen.scroll(action["direction"], sx, sy)
        return f"scrolled {action['direction']}"
    if kind == "wait":
        time.sleep(1.5)
        return "waited"
    raise ValueError(kind)


def _describe(action: dict) -> str:
    kind = action["action"]
    detail = {"click": action.get("target"), "type": action.get("text"), "key": action.get("key"),
              "scroll": action.get("direction")}.get(kind)
    return kind + (f" {detail!r}" if detail else "")


async def run_task(task: str, planner: Planner, run: Run, *, start_url: str | None = None, max_steps: int = 12,
                   keep_screenshots: bool = False, region: screen.Region = WORK_AREA, work_pid: int,
                   allow_overlays: frozenset[str] | None = None, log=print) -> Result:
    run.event("run_started", run_id=run.run_id, task=task, model=planner.model, e2ee=planner.e2ee,
              start_url=start_url, allow_gateway_signature=planner.allow_gateway)
    totals = {"prompt": 0, "completion": 0}
    region = screen.work_region(region)  # below the menu bar: the capture holds only the work window
    guard = Guard(work_pid, region, allow_overlays)

    def finish(status: str, summary: str, steps: int) -> Result:
        run.event("run_finished", status=status, summary=summary, steps=steps)
        return Result(status, summary, steps, run.dir, totals["prompt"], totals["completion"])

    try:
        attestation = await planner.start()
    except PlannerUnverified as error:
        run.event("planner_unverified", 0, error=str(error))
        return finish("stopped", f"planner attestation failed: {error}", 0)
    run.write_attestation(attestation)
    run.event("attestation", attestation={k: v for k, v in attestation.items() if k not in ("model_id", "e2ee", "model_signers")})

    def stopped_by_guard(blocked: GuardBlocked, step: int) -> Result:
        run.event("guard_blocked", step, action=blocked.what, reason=blocked.reason)
        log(f"step {step}: GUARD, stopping before {blocked.what}: work window not in front: {blocked.reason}")
        return finish("stopped", f"work window not in front: {blocked.reason}", step)

    history: list[str] = []
    previous_hash = None
    previous_action = ""
    for step in range(1, max_steps + 1):
        if step > 1:
            time.sleep(SETTLE_SECONDS)
        try:
            guard.passes("capture", guard.check_region)
            png = screen.capture(region)
            guard.strict("capture", guard.check_region)()  # covered while capturing: discard, nothing was sent
        except GuardBlocked as blocked:
            return stopped_by_guard(blocked, step)
        digest = sha256_hex(png)
        width, height = _png_size(png)
        run.event("step_started", step, screenshot={"sha256": digest, "width": width, "height": height, "bytes": len(png)})
        if keep_screenshots:
            run.write_screenshot(step, png)
        note = None
        if previous_hash is not None and digest == previous_hash:
            note = f"Note: the screen did not change after your last action ({previous_action}). It probably had no effect."
            history[-1] += " The screen did not change."
        try:
            run.event("screenshot_sent", step, to="near-enclave", host=urlparse(PLANNER_BASE_URL).netloc, bytes=len(png))
            plan = await planner.plan(png, task, history, note)
        except PlannerUnverified as error:
            run.event("planner_unverified", step, error=str(error))
            log(f"step {step}: NOT VERIFIED, stopping: {error}")
            return finish("stopped", f"planner answer not verified: {error}", step)
        totals["prompt"] += plan.usage.get("prompt_tokens", 0)
        totals["completion"] += plan.usage.get("completion_tokens", 0)
        run.write_wire(step, plan.request_bytes, plan.response_bytes, plan.reply_key)
        run.event("planner_verified", step, planner={
            "model": planner.model, "completion_id": plan.completion_id, "e2ee": planner.e2ee,
            "latency_ms": plan.latency_ms, "thought": plan.thought, "action": plan.action, "usage": plan.usage},
            verification=plan.verification)
        action = plan.action
        log(f"step {step}: planner {plan.latency_ms} ms, verified {plan.verification['signature_kind']}: "
            f"{_describe(action)}  ({plan.thought})")

        if action["action"] == "done":
            run.update_step(step, action_performed="done")
            return finish("done", action.get("summary", ""), step)
        if action["action"] == "fail":
            run.update_step(step, action_performed="fail")
            return finish("failed", action.get("reason", ""), step)

        try:
            result = _act(action, png, step, run, region, guard)
        except GuardBlocked as blocked:
            run.update_step(step, action_performed="blocked")
            return stopped_by_guard(blocked, step)
        run.event("acted", step, result=result)
        run.update_step(step, action_performed=_describe(action))
        log(f"step {step}: {result}")
        history.append(f"Step {step}: you chose {_describe(action)} -> {result}.")
        previous_hash, previous_action = digest, _describe(action)
    return finish("stopped", f"step budget of {max_steps} used without finishing", max_steps)


def run_sync(*args, **kwargs):
    return asyncio.run(run_task(*args, **kwargs))
