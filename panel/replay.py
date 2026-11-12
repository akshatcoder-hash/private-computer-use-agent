#!/usr/bin/env python3
"""Replay a sample run into a fresh run dir with realistic delays.

  python panel/replay.py panel/sample/sample-run receipts [--speed 1.0]
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# seconds to wait AFTER writing an event of this type, before the next one
DELAY = {"run_started": 0.4, "attestation": 0.8, "step_started": 2.5,
         "planner_verified": 0.35, "planner_unverified": 0.8,
         "grounded": 0.15, "acted": 0.7, "run_finished": 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sample")
    ap.add_argument("receipts")
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args()
    events = [json.loads(l) for l in (Path(a.sample) / "events.jsonl").read_text().splitlines() if l.strip()]
    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%d-%H%M%S")
    run = Path(a.receipts) / run_id
    n = 1
    while run.exists():
        n += 1
        run = Path(a.receipts) / f"{run_id}-{n}"
    run.mkdir(parents=True)
    steps = {}
    with open(run / "events.jsonl", "a", buffering=1) as out:
        for i, ev in enumerate(events):
            ev = dict(ev)
            if ev.get("type") == "run_started":
                ev["run_id"] = run.name
            if "time" in ev:
                ev["time"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            out.write(json.dumps(ev) + "\n")
            out.flush()
            if "step" in ev:
                d = steps.setdefault(ev["step"], {})
                d.update({k: v for k, v in ev.items() if k != "type"})
                (run / f"step-{ev['step']}.json").write_text(json.dumps(d, indent=2))
            print(ev["type"], flush=True)
            nxt = events[i + 1]["type"] if i + 1 < len(events) else None
            d = DELAY.get(ev["type"], 0.3)
            if ev["type"] == "planner_verified" and nxt == "run_finished":
                d = 0.6
            time.sleep(d / max(a.speed, 0.01))
    print("run dir:", run)


if __name__ == "__main__":
    sys.exit(main())
