#!/usr/bin/env python3
"""Static server for the mock bank plus a tiny event log. Stdlib only."""
import argparse, json, os, time, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from functools import partial

HERE = os.path.dirname(os.path.abspath(__file__))
BANK = os.path.join(HERE, "bank")
LOG = os.path.join(HERE, ".events.jsonl")
LOCK = threading.Lock()


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/events":
            with LOCK:
                try:
                    lines = [json.loads(l) for l in open(LOG) if l.strip()]
                except FileNotFoundError:
                    lines = []
            return self._json(200, lines)
        super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if path == "/event":
            try:
                ev = json.loads(raw or b"{}")
                if not isinstance(ev, dict):
                    raise ValueError
            except ValueError:
                return self._json(400, {"ok": False})
            ev["ts"] = time.time()
            with LOCK, open(LOG, "a") as f:
                f.write(json.dumps(ev) + "\n")
            return self._json(200, {"ok": True})
        if path == "/reset":
            with LOCK:
                open(LOG, "w").close()
            return self._json(200, {"ok": True})
        self._json(404, {"ok": False})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), partial(Handler, directory=BANK))
    print(f"mock bank on http://127.0.0.1:{a.port}/", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
