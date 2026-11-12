"""Milestone 1b: one Atom click on the real screen.

    uv run m1/click_check.py              # ground and click
    uv run m1/click_check.py --no-click   # ground only (no Accessibility needed)

Opens a local page with one button, screenshots the real screen, asks the
local Atom API where the button is, and clicks there. The page itself reports
where the button is on screen and whether it received the click, so the result
is checked without anyone looking at the screenshot. The screenshot goes to
127.0.0.1 only and is deleted afterwards.
"""
import argparse
import base64
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import Quartz

ATOM_URL = os.environ.get("ATOM_URL", "http://127.0.0.1:8760").rstrip("/")
INSTRUCTION = "click the Download PDF button"

PAGE = """<!doctype html><meta charset="utf-8"><title>Atom click check</title>
<body style="margin:0;background:#f4f4f6;font:20px -apple-system,sans-serif">
<p style="margin:40px">Statement for October 2026</p>
<button id="b" style="position:absolute;left:38%;top:42%;width:300px;height:80px;font-size:24px">Download PDF</button>
<script>
const b = document.getElementById("b");
const post = (path, body) => fetch(path, {method: "POST", body: JSON.stringify(body)});
function geometry() {
  const r = b.getBoundingClientRect();
  const left = window.screenX + (window.outerWidth - window.innerWidth) / 2 + r.left;
  const top = window.screenY + (window.outerHeight - window.innerHeight) + r.top;
  post("/geometry", {left, top, width: r.width, height: r.height});
}
window.addEventListener("load", () => setTimeout(geometry, 500));
b.addEventListener("click", (e) => { b.textContent = "Clicked"; post("/clicked", {x: e.screenX, y: e.screenY}); });
</script>"""

state: dict = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        state[self.path.strip("/")] = json.loads(self.rfile.read(int(self.headers["content-length"])))
        self.send_response(204)
        self.end_headers()


def wait_for(key: str, seconds: float):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if key in state:
            return state[key]
        time.sleep(0.1)
    return None


def ground(png: bytes) -> dict:
    body = json.dumps({"instruction": INSTRUCTION, "image_base64": base64.b64encode(png).decode()}).encode()
    request = urllib.request.Request(ATOM_URL + "/ground", body, {"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as response:
        reply = json.load(response)
    if not reply.get("ok") or reply["result"].get("action") != "click":
        raise SystemExit(f"FAIL: Atom did not return a click: {reply}")
    return reply["result"]


def click(x: float, y: float):
    for kind in (Quartz.kCGEventMouseMoved, Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        event = Quartz.CGEventCreateMouseEvent(None, kind, (x, y), Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.08)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-click", action="store_true")
    args = parser.parse_args()

    if not Quartz.CGPreflightScreenCaptureAccess():
        print("Screen Recording is not granted to this terminal (System Settings > Privacy & Security).")
        return 2
    services = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    if not args.no_click and not services.AXIsProcessTrusted():
        print("Accessibility is not granted to this terminal (System Settings > Privacy & Security). "
              "Run with --no-click to check grounding only.")
        return 2

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    subprocess.run(["open", f"http://127.0.0.1:{server.server_port}/"], check=True)
    box = wait_for("geometry", 15)
    if box is None:
        print("FAIL: the test page did not load in a browser.")
        return 1
    time.sleep(1.0)

    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "screen.png")
        subprocess.run(["screencapture", "-x", "-m", path], check=True)
        with open(path, "rb") as stream:
            png = stream.read()
    result = ground(png)

    # Atom answers in screenshot pixels; clicks and the page's geometry are in points.
    scale = result["image"]["width"] / Quartz.CGDisplayPixelsWide(Quartz.CGMainDisplayID())
    x, y = result["pixel"]["x"] / scale, result["pixel"]["y"] / scale
    inside = box["left"] <= x <= box["left"] + box["width"] and box["top"] <= y <= box["top"] + box["height"]
    print(f"Atom: ({x:.0f}, {y:.0f}) in {result['duration_ms']} ms; "
          f"button at ({box['left']:.0f}, {box['top']:.0f}) {box['width']:.0f}x{box['height']:.0f}; "
          f"{'inside' if inside else 'OUTSIDE'}")
    if args.no_click:
        print("PASS (grounding only)" if inside else "FAIL: point is outside the button")
        return 0 if inside else 1

    click(x, y)
    clicked = wait_for("clicked", 5)
    if clicked is None:
        print("FAIL: the button did not receive a click.")
        return 1
    print(f"PASS: the button received the click at ({clicked['x']}, {clicked['y']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
