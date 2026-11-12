"""capture -> plan -> verify -> ground -> act -> record. Nothing is executed unless the plan verified."""
import asyncio
import time
from dataclasses import dataclass

from . import grounder, screen
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


def _png_size(png: bytes) -> tuple[int, int]:
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


def _rephrase(target: str) -> str:
    return "click on " + target.removeprefix("the ").removeprefix("The ")


def _ground(target: str, png: bytes, step: int, run: Run) -> dict | str:
    """Atom, with one retry on a rephrased target. Returns the result or an error string."""
    last = ""
    for instruction in (f"click {target}", _rephrase(target)):
        try:
            result = grounder.ground(instruction, png)
        except grounder.GroundingError as error:
            last = str(error)
            continue
        run.event("grounded", step, grounding={
            "model": "Atom tinyclick-4bit-v1", "where": "127.0.0.1:8760", "instruction": instruction,
            "pixel": result["pixel"], "duration_ms": result.get("duration_ms")})
        return result
    return last


def _act(action: dict, png: bytes, step: int, run: Run, region: screen.Region) -> str:
    kind = action["action"]
    if kind == "click":
        found = _ground(action["target"], png, step, run)
        if isinstance(found, str):
            return f"FAILED: could not locate {action['target']!r} ({found}); nothing was clicked"
        width, _ = _png_size(png)
        scale = found["image"]["width"] / region.width if found.get("image") else width / region.width
        x, y = region.x + found["pixel"]["x"] / scale, region.y + found["pixel"]["y"] / scale
        screen.click(x, y)
        return f"clicked ({round(x)}, {round(y)})"
    if kind == "type":
        screen.type_text(action["text"])
        if action.get("enter"):
            screen.press("enter")
        return f"typed {len(action['text'])} characters" + (" and pressed enter" if action.get("enter") else "")
    if kind == "key":
        screen.press(action["key"])
        return f"pressed {action['key']}"
    if kind == "scroll":
        screen.scroll(action["direction"], region.x + region.width / 2, region.y + region.height / 2)
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
                   keep_screenshots: bool = False, region: screen.Region = WORK_AREA, log=print) -> Result:
    run.event("run_started", run_id=run.run_id, task=task, model=planner.model, e2ee=planner.e2ee,
              start_url=start_url, allow_gateway_signature=planner.allow_gateway)
    totals = {"prompt": 0, "completion": 0}

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

    history: list[str] = []
    previous_hash = None
    previous_action = ""
    for step in range(1, max_steps + 1):
        if step > 1:
            time.sleep(SETTLE_SECONDS)
        png = screen.capture(region)
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
            plan = await planner.plan(png, task, history, note)
        except PlannerUnverified as error:
            run.event("planner_unverified", step, error=str(error))
            log(f"step {step}: NOT VERIFIED, stopping: {error}")
            return finish("stopped", f"planner answer not verified: {error}", step)
        totals["prompt"] += plan.usage.get("prompt_tokens", 0)
        totals["completion"] += plan.usage.get("completion_tokens", 0)
        run.write_wire(step, plan.request_bytes, plan.response_bytes)
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

        result = _act(action, png, step, run, region)
        run.event("acted", step, result=result)
        run.update_step(step, action_performed=_describe(action))
        log(f"step {step}: {result}")
        history.append(f"Step {step}: you chose {_describe(action)} -> {result}.")
        previous_hash, previous_action = digest, _describe(action)
    return finish("stopped", f"step budget of {max_steps} used without finishing", max_steps)


def run_sync(*args, **kwargs):
    return asyncio.run(run_task(*args, **kwargs))
