#!/usr/bin/env python3
"""Offline Atom grounding service. Stdout contains JSON lines exclusively.

Model notices: licenses/atom.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import sys
import time

# App resources are sealed by codesign; caches belong outside the bundle.
sys.dont_write_bytecode = True

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


class AtomRuntime:
    def __init__(self, model_dir: Path, manifest: Path):
        self.model_dir = model_dir.expanduser().resolve()
        self.manifest_path = manifest
        self.model = self.processor = None
        self.verified = False

    def verify(self):
        if not self.verified:
            from install_model import verify_directory
            verify_directory(self.model_dir, json.loads(self.manifest_path.read_text()))
            self.verified = True

    def load(self):
        if self.model is not None:
            return
        self.verify()
        if sys.platform != "darwin" or platform.machine() != "arm64":
            raise RuntimeError("Atom requires an Apple Silicon Mac")
        from mlx_vlm import load
        self.model, self.processor = load(str(self.model_dir))

    def health(self):
        self.verify()
        deps = {name: importlib.metadata.version(name) for name in ("mlx", "mlx-vlm", "pillow")}
        return {"model": "Atom", "model_version": "tinyclick-4bit-v1", "verified": True,
                "loaded": self.model is not None, "model_dir": str(self.model_dir),
                "architecture": platform.machine(), "dependencies": deps,
                "capability": "single-step click grounding"}

    def ground(self, params):
        image = Path(params["image_path"]).expanduser().resolve()
        instruction = params["instruction"]
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 4000:
            raise ValueError("instruction must contain 1 to 4000 characters")
        if not image.is_file():
            raise ValueError("image_path must identify a local screenshot")
        from PIL import Image
        with Image.open(image) as picture:
            width, height = picture.size
        started = time.monotonic()
        self.load()
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        prompt = f"what to do to execute the command? {instruction.strip()}".lower()
        prompt = apply_chat_template(self.processor, self.model.config, prompt, num_images=1)
        output = generate(self.model, self.processor, prompt, str(image), max_tokens=32,
                          temperature=0.0, verbose=False)
        raw = output.text if hasattr(output, "text") else str(output)
        locs = [int(value) for value in re.findall(r"<loc_(\d+)>", raw)]
        if len(locs) < 2 or any(value < 0 or value > 999 for value in locs):
            return {"action": "abstain", "raw": raw, "reason": "No valid point returned",
                    "duration_ms": round((time.monotonic() - started) * 1000)}
        x, y = ((locs[0] + locs[2]) / 2, (locs[1] + locs[3]) / 2) if len(locs) >= 4 else locs[:2]
        # Florence loc tokens are 0..999; image edges are finite pixel coordinates.
        return {"action": "click", "normalized": {"x": x, "y": y},
                "pixel": {"x": min(width - 1, x / 1000 * width),
                          "y": min(height - 1, y / 1000 * height)},
                "image": {"width": width, "height": height}, "raw": raw,
                "duration_ms": round((time.monotonic() - started) * 1000)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, default=Path(os.environ.get("ATOM_MODEL_DIR") or Path.home() / "Library/Application Support/PrivateAgent/Atom/Models/tinyclick-4bit"))
    parser.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parent / "model-manifest.json")
    args = parser.parse_args()
    runtime = AtomRuntime(args.model_dir, args.manifest)
    for line in sys.stdin:
        request_id = None
        try:
            if len(line) > 65536:
                raise ValueError("request exceeds 64 KiB")
            request = json.loads(line)
            request_id = request.get("id")
            with contextlib.redirect_stdout(sys.stderr):
                method = request.get("method")
                if method == "health":
                    result = runtime.health()
                elif method == "ground":
                    result = runtime.ground(request.get("params", {}))
                elif method == "unload":
                    runtime.model = runtime.processor = None
                    import gc
                    gc.collect()
                    result = {"loaded": False}
                else:
                    raise ValueError("Unknown method; use health, ground, or unload")
            response = {"id": request_id, "ok": True, "result": result}
        except Exception as error:
            response = {"id": request_id, "ok": False,
                        "error": {"type": type(error).__name__, "message": str(error)}}
        print(json.dumps(response, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
