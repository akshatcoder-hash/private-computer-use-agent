#!/usr/bin/env python3
"""Real offline inference check against a generated, non-private screenshot."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="atom-smoke-") as directory:
        screenshot = Path(directory) / "screen.png"
        image = Image.new("RGB", (1000, 700), "#f5f4ef")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype("/System/Library/Fonts/SFNS.ttf", 24)
        draw.text((50, 45), "Atom runtime verification", font=font, fill="#111111")
        draw.rounded_rectangle((360, 280, 640, 360), radius=16, fill="#202321")
        draw.text((420, 300), "Continue", font=font, fill="white")
        image.save(screenshot)
        requests = [{"id": "health", "method": "health"},
                    {"id": "ground", "method": "ground", "params": {
                        "image_path": str(screenshot), "instruction": "Click Continue"}},
                    {"id": "unload", "method": "unload"}]
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("atom_runtime.py")),
                                 "--model-dir", args.model_dir],
                                input="".join(json.dumps(item) + "\n" for item in requests),
                                capture_output=True, text=True, timeout=180, check=True)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        if len(responses) != 3 or any(not item["ok"] for item in responses):
            raise RuntimeError(result.stdout + result.stderr)
        click = responses[1]["result"]
        if click["action"] != "click" or not (360 <= click["pixel"]["x"] <= 640 and 280 <= click["pixel"]["y"] <= 360):
            raise RuntimeError(f"Grounding missed generated Continue button: {click}")
        print(json.dumps({"passed": True, "responses": responses}, indent=2))


if __name__ == "__main__":
    main()
