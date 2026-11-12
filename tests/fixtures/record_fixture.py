"""Record a small REAL receipt for the verify tests: real NEAR calls, synthetic 400x300 pages, no real screen.

    uv run python tests/fixtures/record_fixture.py

Writes tests/fixtures/run/. The screen, Atom and input events are stubbed; the
planner call, attestation, E2EE and signature are real. Needs NEARAI_API_KEY.
"""
import asyncio
import io
import os
import shutil
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from agent import grounder, loop, screen  # noqa: E402
from agent.__main__ import load_key  # noqa: E402
from agent.planner import Planner  # noqa: E402
from agent.receipts import Run  # noqa: E402


def page(title: str, button: str | None) -> bytes:
    image = Image.new("RGB", (400, 300), "#f4f4f6")
    draw = ImageDraw.Draw(image)
    draw.text((20, 20), title, fill="black")
    if button:
        draw.rectangle((100, 120, 300, 180), fill="#1f6feb")
        draw.text((150, 144), button, fill="white")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


PAGES = iter([page("Account Home", "Settings"), page("Preferences", "Save changes"), page("Saved - changes saved", None),
              page("Saved - changes saved", None)])
screen.capture = lambda region: next(PAGES)
screen.click = lambda x, y: None
grounder.ground = lambda instruction, png: {"action": "click", "pixel": {"x": 200, "y": 150}, "image": {"width": 400, "height": 300}, "duration_ms": 1}
loop.SETTLE_SECONDS = 0

target = os.path.join(ROOT, "tests", "fixtures", "run")
shutil.rmtree(target, ignore_errors=True)


async def main():
    run = Run(os.path.dirname(target), "run")
    async with Planner(load_key()) as planner:
        # Until loop.py passes plan.reply_key to write_wire itself, hand the key over from here.
        real_plan, real_write = planner.plan, run.write_wire
        last = {}

        async def plan(*args, **kwargs):
            result = await real_plan(*args, **kwargs)
            last["key"] = result.reply_key
            return result

        planner.plan = plan
        run.write_wire = lambda step, req, resp, reply_key=None: real_write(step, req, resp, reply_key or last.get("key"))
        result = await loop.run_task("Open Settings and save the changes", planner, run, region=screen.Region(0, 0, 400, 300))
    print(result)

asyncio.run(main())
