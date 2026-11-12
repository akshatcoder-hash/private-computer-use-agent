"""Tiny 3-page test site: Home -> Settings -> Save changes -> Saved. Standard library only.

    python tests/fixtures/site/server.py [--port 8780]

The server records every request in `.visited`, so a test can confirm the
"Saved" page was reached by a real click rather than by the planner's word.
"""
import argparse
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STYLE = ("body{margin:0;background:#f4f4f6;font:28px -apple-system,Helvetica,sans-serif;color:#111}"
         "main{padding:80px 120px}h1{font-size:48px}"
         ".btn{display:inline-block;margin-top:40px;padding:28px 56px;font-size:36px;background:#1f6feb;color:#fff;"
         "border:0;border-radius:12px;text-decoration:none;cursor:pointer}")

PAGES = {
    "/": ("Account Home", "<p>Welcome back. Your account is in good standing.</p>"
                          "<a class='btn' href='/settings'>Settings</a>"),
    "/settings": ("Preferences", "<p>Notification preferences are set to weekly.</p>"
                              "<form method='post' action='/save'><button class='btn' type='submit'>Save changes</button></form>"),
    "/saved": ("Saved", "<p>Your changes have been saved.</p>"),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, path: str):
        title, body = PAGES.get(path, PAGES["/"])
        html = f"<!doctype html><meta charset=utf-8><title>{title}</title><style>{STYLE}</style><main><h1>{title}</h1>{body}</main>"
        data = html.encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        self.server.visited.append(self.path)
        self._send(self.path)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        self.server.visited.append("POST " + self.path)
        self.send_response(303)
        self.send_header("location", "/saved")
        self.end_headers()


class Site(ThreadingHTTPServer):
    def __init__(self, port: int = 0):
        super().__init__(("127.0.0.1", port), Handler)
        self.visited: list[str] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/"

    @property
    def saved(self) -> bool:
        return "POST /save" in self.visited and "/saved" in self.visited


def start(port: int = 0) -> Site:
    site = Site(port)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    return site


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8780)
    site = Site(parser.parse_args().port)
    print(site.url, flush=True)
    site.serve_forever()
