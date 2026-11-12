"""Writes receipts/<run_id>/: events.jsonl, step-N.json, step-N.{request,response}.bin, attestation.json."""
import json
import os
import time


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z"


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


class Run:
    def __init__(self, root: str = "receipts", run_id: str | None = None):
        self.run_id = run_id or new_run_id()
        self.dir = os.path.join(root, self.run_id)
        os.makedirs(self.dir, exist_ok=False)
        self._steps: dict[int, dict] = {}

    def _path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def event(self, kind: str, step: int | None = None, **fields) -> dict:
        record = {"type": kind}
        if step is not None:
            record["step"] = step
        record.update(fields)
        record["time"] = now()
        with open(self._path("events.jsonl"), "a") as stream:
            stream.write(json.dumps(record) + "\n")
        if step is not None:
            merged = self._steps.setdefault(step, {"step": step})
            merged.update({k: v for k, v in record.items() if k not in ("type", "step", "time")})
            merged.setdefault("events", []).append(kind)
            self._write_json(f"step-{step}.json", merged)
        return record

    def update_step(self, step: int, **fields):
        merged = self._steps.setdefault(step, {"step": step})
        merged.update(fields)
        self._write_json(f"step-{step}.json", merged)

    def write_attestation(self, attestation: dict):
        self._write_json("attestation.json", attestation)

    def write_wire(self, step: int, request: bytes, response: bytes):
        for suffix, data in (("request", request), ("response", response)):
            with open(self._path(f"step-{step}.{suffix}.bin"), "wb") as stream:
                stream.write(data)

    def write_screenshot(self, step: int, png: bytes):
        with open(self._path(f"step-{step}.png"), "wb") as stream:
            stream.write(png)

    def _write_json(self, name: str, value):
        tmp = self._path(name + ".tmp")
        with open(tmp, "w") as stream:
            json.dump(value, stream, indent=2)
        os.replace(tmp, self._path(name))
