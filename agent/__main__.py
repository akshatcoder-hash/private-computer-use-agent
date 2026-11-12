"""python -m agent run "<task>" [--start-url URL] ... | python -m agent verify <run_dir>"""
import argparse
import asyncio
import os
import sys


def load_key() -> str | None:
    if os.environ.get("NEARAI_API_KEY"):
        return os.environ["NEARAI_API_KEY"]
    for path in (".env", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")):
        if os.path.exists(path):
            for line in open(path):
                if line.startswith("NEARAI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    return None


def cmd_run(args) -> int:
    from . import grounder, screen
    from .browser import WorkBrowser
    from .loop import run_task
    from .planner import Planner
    from .receipts import Run

    key = load_key()
    if not key:
        print("NEARAI_API_KEY is not set (environment or .env).", file=sys.stderr)
        return 2
    missing = screen.missing_permissions()
    if missing:
        print("Missing macOS permission(s) for this terminal: " + ", ".join(missing), file=sys.stderr)
        return 2
    try:
        grounder.health()
    except grounder.GroundingError as error:
        print(f"Atom check failed: {error}", file=sys.stderr)
        return 2

    browser = WorkBrowser(args.start_url) if args.start_url and not args.no_browser else None
    work_pid = browser.pid if browser else args.work_pid
    if work_pid is None:
        print("Cannot tell which window the agent owns: pass --start-url (the agent opens its own browser) "
              "or --work-pid <pid of the work browser>.", file=sys.stderr)
        return 2
    allow = screen.allowed_overlays_from_env() | frozenset(args.allow_overlay or [])
    run = Run(args.receipts_dir)

    async def go():
        async with Planner(key, args.model, e2ee=not args.no_e2ee,
                           allow_gateway_signature=args.allow_gateway_signature) as planner:
            return await run_task(args.task, planner, run, start_url=args.start_url, max_steps=args.max_steps,
                                  keep_screenshots=args.keep_screenshots, work_pid=work_pid, allow_overlays=allow)
    try:
        if browser:
            browser.wait_ready()
        result = asyncio.run(go())
    finally:
        if browser:
            browser.close()
    print(f"\n{result.status}: {result.summary}  ({result.steps} step(s), "
          f"{result.prompt_tokens} prompt + {result.completion_tokens} completion tokens)\nreceipt: {result.run_dir}")
    return 0 if result.status == "done" else 1


def cmd_guard(args) -> int:
    """Exit 0 if the work window is on top and focused; print the reason and exit 1 otherwise."""
    from . import screen
    allow = screen.allowed_overlays_from_env() | frozenset(args.allow_overlay or [])
    reason = screen.guard_region(screen.Region.parse(args.region), args.pid, allow) or screen.guard_keyboard(args.pid)
    print(reason or "ok")
    return 0 if reason is None else 1


def cmd_verify(args) -> int:
    from .verify import verify_run
    return 0 if verify_run(args.run_dir) else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m agent")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a task on the real screen")
    run.add_argument("task")
    run.add_argument("--start-url", help="open this URL in a 1400x1080 browser window at 0,0 first, and close it after")
    run.add_argument("--no-browser", action="store_true", help="do not open --start-url; the page is already open")
    run.add_argument("--model", default="Qwen/Qwen3.8-27B")
    run.add_argument("--max-steps", type=int, default=12)
    run.add_argument("--receipts-dir", default="receipts")
    run.add_argument("--keep-screenshots", action="store_true", help="save each screenshot in the run directory (off by default)")
    run.add_argument("--allow-gateway-signature", action="store_true",
                     help="accept a gateway signature instead of the model enclave's (weaker; off by default)")
    run.add_argument("--no-e2ee", action="store_true", help="turn end-to-end encryption to the model off")
    run.add_argument("--work-pid", type=int, help="pid of the work browser (with --no-browser); the agent only captures and acts while its window is on top")
    run.add_argument("--allow-overlay", action="append", metavar="APP", help="ignore this app's invisible overlay window (repeatable; also AGENT_ALLOW_OVERLAYS=a,b)")
    run.set_defaults(func=cmd_run)
    guard = sub.add_parser("guard", help="check that the work window (pid) is on top and focused")
    guard.add_argument("--pid", type=int, required=True)
    guard.add_argument("--region", default="0,0,1400,1080")
    guard.add_argument("--allow-overlay", action="append", metavar="APP")
    guard.set_defaults(func=cmd_guard)
    verify = sub.add_parser("verify", help="check a run's receipt offline")
    verify.add_argument("run_dir")
    verify.set_defaults(func=cmd_verify)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
