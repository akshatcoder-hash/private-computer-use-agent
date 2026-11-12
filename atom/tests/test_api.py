#!/usr/bin/env python3
"""Tests for the Atom HTTP API layer.

These exercise the server against tests/stub_runtime.py so they run anywhere,
with no model, no MLX, and no Apple Silicon. They verify the HTTP contract,
request validation, and the stdio plumbing — not model accuracy.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import base64
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))

import atom_api  # noqa: E402

STUB = HERE / "stub_runtime.py"
# Point the API at the stub instead of the real MLX runtime.
atom_api.RUNTIME = STUB


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = atom_api.AtomProcess(None, None, sys.executable, timeout=30)
        atom_api.Handler.runtime = cls.runtime
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), atom_api.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.runtime.close()

    # -- helpers ---------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _get(self, path: str):
        try:
            with urllib.request.urlopen(self._url(path), timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def _post(self, path: str, payload):
        raw = json.dumps(payload).encode()
        request = urllib.request.Request(
            self._url(path), data=raw, method="POST",
            headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    # -- tests -----------------------------------------------------------
    def test_health_ok(self):
        status, body = self._get("/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["result"]["model"], "Atom")

    def test_ground_with_path(self):
        status, body = self._post("/ground", {
            "instruction": "click the submit button",
            "image_path": str(STUB),
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["result"]["action"], "click")
        self.assertTrue(body["result"]["image_exists"])

    def test_ground_with_base64(self):
        blob = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
        status, body = self._post("/ground", {
            "instruction": "click ok",
            "image_base64": blob,
        })
        self.assertEqual(status, 200)
        # the API must materialize the upload before handing it to the runtime
        self.assertTrue(body["result"]["image_exists"])

    def test_ground_accepts_data_url(self):
        blob = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
        status, body = self._post("/ground", {
            "instruction": "click ok",
            "image_base64": f"data:image/png;base64,{blob}",
        })
        self.assertEqual(status, 200)

    def test_missing_instruction_rejected(self):
        status, body = self._post("/ground", {"image_path": str(STUB)})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_missing_image_rejected(self):
        status, body = self._post("/ground", {"instruction": "click"})
        self.assertEqual(status, 400)

    def test_bad_base64_rejected(self):
        status, body = self._post("/ground", {
            "instruction": "click", "image_base64": "!!!not base64!!!"})
        self.assertEqual(status, 400)

    def test_long_instruction_rejected(self):
        status, body = self._post("/ground", {
            "instruction": "x" * 5000, "image_path": str(STUB)})
        self.assertEqual(status, 400)

    def test_non_json_body_rejected(self):
        request = urllib.request.Request(
            self._url("/ground"), data=b"not json", method="POST",
            headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        self.assertEqual(code, 400)

    def test_unknown_route_404(self):
        status, _ = self._get("/nope")
        self.assertEqual(status, 404)
        status, _ = self._post("/nope", {})
        self.assertEqual(status, 404)

    def test_unload_ok(self):
        status, body = self._post("/unload", {})
        self.assertEqual(status, 200)
        self.assertFalse(body["result"]["loaded"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
