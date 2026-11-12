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

    run = Run(args.receipts_dir)
    browser = WorkBrowser(args.start_url) if args.start_url and not args.no_browser else None

    async def go():
        async with Planner(key, args.model, e2ee=not args.no_e2ee,
                           allow_gateway_signature=args.allow_gateway_signature) as planner:
            return await run_task(args.task, planner, run, start_url=args.start_url, max_steps=args.max_steps,
                                  keep_screenshots=args.keep_screenshots)
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
    run.set_defaults(func=cmd_run)
    verify = sub.add_parser("verify", help="check a run's receipt offline")
    verify.add_argument("run_dir")
    verify.set_defaults(func=cmd_verify)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
