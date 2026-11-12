#!/usr/bin/env python3
"""HTTP inference API for Atom.

Wraps the stdio JSON-lines runtime (runtime/atom_runtime.py) in a small HTTP
service. Standard library only, so it adds no dependencies to the pinned
runtime lock.

Endpoints
    GET  /health          -> runtime health (model version, verification, deps)
    POST /ground          -> single-step click grounding
    POST /unload          -> release the loaded model from memory

/ground accepts either a local path or an inline image:

    {"instruction": "click the submit button", "image_path": "/tmp/shot.png"}
    {"instruction": "click the submit button", "image_base64": "<png/jpeg>"}

Response:
    {"ok": true, "result": {"action": "click",
                            "normalized": {"x": .., "y": ..},
                            "pixel": {"x": .., "y": ..},
                            "image": {"width": .., "height": ..},
                            "raw": "click <loc_512><loc_233>",
                            "duration_ms": ..}}

Atom requires Apple Silicon; see docs/DEPLOYMENT.md before deploying anywhere
other than a Mac.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parent / "runtime" / "atom_runtime.py"
MAX_BODY = 32 * 1024 * 1024          # a screenshot, generously
MAX_INSTRUCTION = 4000


class RuntimeError_(RuntimeError):
    """Raised when the child runtime reports a failure."""


class AtomProcess:
    """One long-lived runtime child; requests are serialized because the
    protocol is a single stdin/stdout channel."""

    def __init__(self, model_dir: Path | None, manifest: Path | None,
                 python: str, timeout: float):
        self.timeout = timeout
        self.lock = threading.Lock()
        self.counter = 0
        command = [python, str(RUNTIME)]
        if model_dir:
            command += ["--model-dir", str(model_dir)]
        if manifest:
            command += ["--manifest", str(manifest)]
        self.child = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        # The runtime logs progress and tracebacks to stderr; drain it or a
        # full pipe will block the child mid-request.
        self.stderr_tail: list[str] = []
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        assert self.child.stderr is not None
        for line in self.child.stderr:
            self.stderr_tail.append(line.rstrip())
            del self.stderr_tail[:-40]

    def call(self, method: str, params: dict | None = None) -> dict:
        with self.lock:
            if self.child.poll() is not None:
                raise RuntimeError_(
                    f"runtime exited with code {self.child.returncode}: "
                    + " | ".join(self.stderr_tail[-3:]))
            self.counter += 1
            request_id = self.counter
            payload = {"id": request_id, "method": method}
            if params is not None:
                payload["params"] = params
            assert self.child.stdin is not None
            self.child.stdin.write(json.dumps(payload) + "\n")
            self.child.stdin.flush()

            assert self.child.stdout is not None
            # A single request may take a while (model load on first ground),
            # so wait on the process rather than a bare readline.
            line = self._readline()
            if line is None:
                raise RuntimeError_(
                    f"runtime produced no response (exit {self.child.poll()}): "
                    + " | ".join(self.stderr_tail[-3:]))
            response = json.loads(line)
            if not response.get("ok"):
                error = response.get("error") or {}
                raise RuntimeError_(f"{error.get('type', 'Error')}: "
                                    f"{error.get('message', 'unknown failure')}")
            return response["result"]

    def _readline(self) -> str | None:
        box: dict[str, str | None] = {}

        def read():
            box["line"] = self.child.stdout.readline()  # type: ignore[union-attr]

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        thread.join(self.timeout)
        if thread.is_alive():
            raise RuntimeError_(f"runtime exceeded {self.timeout:.0f}s timeout")
        line = box.get("line")
        return line or None

    def close(self):
        try:
            if self.child.stdin:
                self.child.stdin.close()
            self.child.wait(timeout=5)
        except Exception:
            self.child.kill()


class Handler(BaseHTTPRequestHandler):
    server_version = "AtomInference/1.0"
    runtime: AtomProcess
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("atom-api %s\n" % (fmt % args))

    def _send(self, code: int, body: dict):
        raw = json.dumps(body, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.split("?")[0] != "/health":
            return self._send(404, {"ok": False, "error": "not found"})
        try:
            return self._send(200, {"ok": True,
                                    "result": self.runtime.call("health")})
        except Exception as error:
            return self._send(503, {"ok": False, "error": str(error)})

    def do_POST(self):
        route = self.path.split("?")[0]
        if route not in ("/ground", "/unload"):
            return self._send(404, {"ok": False, "error": "not found"})
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            return self._send(400, {"ok": False, "error": "bad content-length"})
        if length <= 0 or length > MAX_BODY:
            return self._send(413, {"ok": False, "error": "body missing or too large"})
        try:
            request = json.loads(self.rfile.read(length))
        except Exception:
            return self._send(400, {"ok": False, "error": "body must be JSON"})

        try:
            if route == "/unload":
                return self._send(200, {"ok": True,
                                        "result": self.runtime.call("unload")})
            params = self._ground_params(request)
            return self._send(200, {"ok": True,
                                    "result": self.runtime.call("ground", params)})
        except ValueError as error:
            return self._send(400, {"ok": False, "error": str(error)})
        except Exception as error:
            return self._send(500, {"ok": False, "error": str(error)})

    def _ground_params(self, request: dict) -> dict:
        if not isinstance(request, dict):
            raise ValueError("body must be a JSON object")
        instruction = request.get("instruction")
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError("instruction is required")
        if len(instruction) > MAX_INSTRUCTION:
            raise ValueError(f"instruction exceeds {MAX_INSTRUCTION} characters")

        encoded = request.get("image_base64")
        if encoded is not None:
            if not isinstance(encoded, str):
                raise ValueError("image_base64 must be a string")
            if encoded.startswith("data:"):
                encoded = encoded.split(",", 1)[-1]
            try:
                blob = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("image_base64 is not valid base64")
            if not blob:
                raise ValueError("image_base64 decoded to nothing")
            handle, name = tempfile.mkstemp(prefix="atom-shot-", suffix=".png")
            with os.fdopen(handle, "wb") as stream:
                stream.write(blob)
            return {"instruction": instruction, "image_path": name}

        path = request.get("image_path")
        if not isinstance(path, str) or not path:
            raise ValueError("provide image_path or image_base64")
        return {"instruction": instruction, "image_path": path}


def main() -> int:
    parser = argparse.ArgumentParser(description="Atom HTTP inference API")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (default loopback)")
    parser.add_argument("--port", type=int, default=8760)
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--manifest", type=Path,
                        default=HERE.parent / "runtime" / "model-manifest.json")
    parser.add_argument("--python", default=sys.executable,
                        help="interpreter that has the Atom runtime installed")
    parser.add_argument("--timeout", type=float, default=180.0,
                        help="per-request seconds (first ground loads the model)")
    args = parser.parse_args()

    runtime = AtomProcess(args.model_dir, args.manifest, args.python, args.timeout)
    Handler.runtime = runtime
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    sys.stderr.write(f"atom-api listening on http://{args.host}:{args.port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
