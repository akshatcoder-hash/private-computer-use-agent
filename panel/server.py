#!/usr/bin/env python3
"""Live receipt panel server. Standard library only, loopback only.

  python panel/server.py --receipts receipts --port 8765

GET /                          the page
GET /events                    Server-Sent Events following the newest run dir
GET /receipt/<run_id>/<step>   <receipts>/<run_id>/step-<step>.json
"""
import argparse
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
RUN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
STEP_RE = re.compile(r"^[0-9]{1,6}$")
POLL = 0.15
CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
       "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'")


def newest_run(root: Path):
    """Newest run directory that has an events.jsonl (by file mtime), or None."""
    best = None
    try:
        entries = list(root.iterdir())
    except OSError:
        return None
    for d in entries:
        if not RUN_RE.match(d.name) or d.is_symlink() or not d.is_dir():
            continue
        try:
            m = (d / "events.jsonl").stat().st_mtime_ns
        except OSError:
            continue
        key = (m, d.name)
        if best is None or key > best[0]:
            best = (key, d)
    return best[1] if best else None


class Handler(BaseHTTPRequestHandler):
    server_version = "ReceiptPanel/1"
    root: Path = Path("receipts")
    port: int = 8765

    def log_message(self, *a):
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def _send(self, code, body: bytes, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                body = (HERE / "index.html").read_bytes()
            except OSError:
                return self._send(500, b"index.html missing", "text/plain")
            return self._send(200, body, "text/html; charset=utf-8")
        if path == "/events":
            return self._events()
        if path.startswith("/receipt/"):
            return self._receipt(path)
        return self._send(404, b"not found", "text/plain")

    def _receipt(self, path):
        parts = path.split("/")  # ['', 'receipt', run, step]
        if len(parts) != 4 or not RUN_RE.match(parts[2]) or not STEP_RE.match(parts[3]):
            return self._send(400, b"bad request", "text/plain")
        root = self.root.resolve()
        f = (root / parts[2] / f"step-{int(parts[3])}.json")
        try:
            f = f.resolve(strict=True)
            if f.parent.parent != root or not f.is_file():
                raise OSError
            body = f.read_bytes()
        except OSError:
            return self._send(404, b"not found", "text/plain")
        return self._send(200, body, "application/json")

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()

        def send(obj_or_line):
            s = obj_or_line if isinstance(obj_or_line, str) else json.dumps(obj_or_line)
            self.wfile.write(b"data: " + s.encode() + b"\n\n")
            self.wfile.flush()

        cur = None      # run dir currently followed
        pos = 0
        buf = b""
        state = None    # 'idle' | run name
        last_beat = time.time()
        try:
            send({"type": "_reset"})
            while True:
                run = newest_run(self.root)
                if run is None:
                    if state != "idle":
                        state, cur, pos, buf = "idle", None, 0, b""
                        send({"type": "_idle"})
                elif cur is None or run != cur:
                    cur, pos, buf, state = run, 0, b"", run.name
                    send({"type": "_reset", "run_id": run.name})
                if cur is not None:
                    try:
                        size = (cur / "events.jsonl").stat().st_size
                        if size < pos:  # truncated: start over
                            pos, buf = 0, b""
                            send({"type": "_reset", "run_id": cur.name})
                        if size > pos:
                            with open(cur / "events.jsonl", "rb") as fh:
                                fh.seek(pos)
                                chunk = fh.read(size - pos)
                            pos += len(chunk)
                            buf += chunk
                            *lines, buf = buf.split(b"\n")  # keep partial tail
                            for ln in lines:
                                ln = ln.strip()
                                if not ln:
                                    continue
                                try:
                                    json.loads(ln)
                                except ValueError:
                                    continue
                                send(ln.decode("utf-8", "replace"))
                    except OSError:
                        pass
                if time.time() - last_beat > 15:
                    self.wfile.write(b": hb\n\n")
                    self.wfile.flush()
                    last_beat = time.time()
                time.sleep(POLL)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--receipts", default="receipts")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    Handler.root = Path(a.receipts)
    Handler.port = a.port
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    srv.daemon_threads = True
    print(f"receipt panel on http://127.0.0.1:{a.port}  (receipts: {Handler.root})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
