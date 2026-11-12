"""Integrity and protocol regressions; no weights or network required."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from install_model import install, verify_directory


class ModelIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "model.bin").write_bytes(b"test-weights")
        self.manifest = {"files": [{"path": "model.bin", "bytes": 12,
                                   "sha256": hashlib.sha256(b"test-weights").hexdigest()}],
                         "bytesTotal": 12, "hardware": {"minFreeBytes": 0}}
        self.manifest_path = self.root / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest))

    def tearDown(self):
        self.temp.cleanup()

    def test_corrupt_download_never_activates(self):
        (self.source / "model.bin").write_bytes(b"wrong-weights")
        destination = self.root / "installed"
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            install(self.manifest_path, self.source.as_uri(), destination)
        self.assertFalse(destination.exists())

    def test_install_verifies_and_preserves_notices(self):
        destination = self.root / "installed"
        result = install(self.manifest_path, self.source.as_uri(), destination)
        self.assertEqual(verify_directory(destination, self.manifest), 12)
        self.assertFalse(result["reused"])
        self.assertTrue((destination / "licenses/atom/NOTICE.md").is_file())
        self.assertTrue(install(self.manifest_path, self.source.as_uri(), destination)["reused"])

    def test_reject_traversal(self):
        self.manifest["files"][0]["path"] = "../model.bin"
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            verify_directory(self.source, self.manifest)

    def test_reject_symlink(self):
        (self.source / "model.bin").unlink()
        (self.source / "model.bin").symlink_to(self.manifest_path)
        with self.assertRaisesRegex(ValueError, "Missing"):
            verify_directory(self.source, self.manifest)

    def test_bad_existing_model_is_not_silently_reused(self):
        with self.assertRaisesRegex(ValueError, "Missing"):
            install(self.manifest_path, self.source.as_uri(), self.root)


class ProtocolTests(unittest.TestCase):
    def test_errors_are_json_and_request_loop_recovers(self):
        script = Path(__file__).with_name("atom_runtime.py")
        process = subprocess.run([sys.executable, str(script)],
                                 input='bad-json\n{"id":2,"method":"unknown"}\n{"id":3,"method":"unload"}\n',
                                 text=True, capture_output=True, check=True)
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(len(replies), 3)
        self.assertFalse(replies[0]["ok"])
        self.assertEqual(replies[1]["id"], 2)
        self.assertTrue(replies[2]["ok"])


if __name__ == "__main__":
    unittest.main()
