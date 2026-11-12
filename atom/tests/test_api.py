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
        atom_api.Handler.allow_image_path = True
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

    def _post(self, path: str, payload, headers=None):
        raw = json.dumps(payload).encode()
        request = urllib.request.Request(
            self._url(path), data=raw, method="POST",
            headers={"content-type": "application/json", **(headers or {})})
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

    # -- hardening -------------------------------------------------------
    def _raw(self, method, path, headers, body=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for key, value in headers.items():
            conn.putheader(key, value)
        if body is not None:
            conn.putheader("content-length", str(len(body)))
        conn.endheaders(body)
        response = conn.getresponse()
        code = response.status
        conn.close()
        return code

    def test_bad_host_rejected(self):
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1",
                     f"localhost.evil.example:{self.port}"):
            self.assertEqual(self._raw("GET", "/health", {"Host": host}), 403, host)
            self.assertEqual(self._raw(
                "POST", "/unload",
                {"Host": host, "content-type": "application/json"}, b"{}"), 403)

    def test_localhost_host_accepted(self):
        self.assertEqual(self._raw(
            "GET", "/health", {"Host": f"localhost:{self.port}"}), 200)

    def test_origin_rejected(self):
        status, _ = self._post("/unload", {}, {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(self._raw("GET", "/health", {
            "Host": f"127.0.0.1:{self.port}", "Origin": "null"}), 403)

    def test_content_type_required(self):
        for ctype in ("text/plain", "application/x-www-form-urlencoded", None):
            headers = {"Host": f"127.0.0.1:{self.port}"}
            if ctype:
                headers["content-type"] = ctype
            self.assertEqual(self._raw("POST", "/unload", headers, b"{}"), 415, ctype)

    def test_json_charset_accepted(self):
        self.assertEqual(self._raw("POST", "/unload", {
            "Host": f"127.0.0.1:{self.port}",
            "content-type": "application/json; charset=utf-8"}, b"{}"), 200)

    def test_image_path_gated(self):
        atom_api.Handler.allow_image_path = False
        try:
            status, body = self._post("/ground", {
                "instruction": "click", "image_path": str(STUB)})
            self.assertEqual(status, 400)
            self.assertIn("disabled", body["error"])
            blob = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
            status, _ = self._post("/ground", {
                "instruction": "click", "image_base64": blob})
            self.assertEqual(status, 200)
        finally:
            atom_api.Handler.allow_image_path = True


class ScratchCleanupTest(unittest.TestCase):
    """The uploaded screenshot must be gone after success, error and timeout."""

    class Fake:
        def __init__(self, mode):
            self.mode = mode
            self.seen = None

        def call(self, method, params=None):
            self.seen = params["image_path"]
            assert Path(self.seen).is_file()
            if self.mode == "error":
                raise atom_api.RuntimeError_("boom")
            if self.mode == "timeout":
                raise atom_api.RuntimeError_("runtime exceeded 1s timeout")
            return {"action": "click"}

    def _run(self, mode):
        fake = self.Fake(mode)
        old = atom_api.Handler.runtime
        atom_api.Handler.runtime = fake
        server = ThreadingHTTPServer(("127.0.0.1", 0), atom_api.Handler)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            blob = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/ground", method="POST",
                data=json.dumps({"instruction": "x", "image_base64": blob}).encode(),
                headers={"content-type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=10) as r:
                    code = r.status
            except urllib.error.HTTPError as e:
                code = e.code
        finally:
            server.shutdown()
            server.server_close()
            atom_api.Handler.runtime = old
        return code, fake.seen

    def test_no_file_left(self):
        directory = Path(atom_api.private_dir())
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for mode, expected in (("ok", 200), ("error", 500), ("timeout", 500)):
            code, seen = self._run(mode)
            self.assertEqual(code, expected, mode)
            self.assertIsNotNone(seen)
            self.assertEqual(Path(seen).parent, directory)
            self.assertEqual(list(directory.iterdir()), [], mode)

    def test_remove_and_sweep(self):
        directory = Path(atom_api.private_dir())
        atom_api.remove_private_dir()
        self.assertFalse(directory.exists())
        import tempfile
        stale = Path(tempfile.mkdtemp(prefix=f"{atom_api.TMP_PREFIX}999999999-"))
        (stale / "shot.png").write_bytes(b"x")
        atom_api.sweep_stale_dirs()
        self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
