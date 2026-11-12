#!/usr/bin/env python3
"""Stub runtime for API tests: speaks the same JSON-lines protocol as
runtime/atom_runtime.py without loading a model."""
import json
import sys
from pathlib import Path

for line in sys.stdin:
    try:
        request = json.loads(line)
        method = request.get("method")
        if method == "health":
            result = {"model": "Atom", "stub": True, "loaded": False}
        elif method == "unload":
            result = {"loaded": False}
        elif method == "ground":
            params = request.get("params") or {}
            path = params.get("image_path")
            result = {
                "action": "click",
                "instruction": params.get("instruction"),
                "image_exists": bool(path) and Path(path).is_file(),
                "normalized": {"x": 500.0, "y": 250.0},
                "pixel": {"x": 10.0, "y": 5.0},
                "duration_ms": 1,
            }
        else:
            raise ValueError("unknown method")
        response = {"id": request.get("id"), "ok": True, "result": result}
    except Exception as error:
        response = {"id": None, "ok": False,
                    "error": {"type": type(error).__name__, "message": str(error)}}
    print(json.dumps(response), flush=True)
