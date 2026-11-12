"""Archive installer safety tests; synthetic archives, no model, no network."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runtime"))
from install_model import install  # noqa: E402

PAYLOAD = b"test-weights"


class ArchiveInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def build(self, extra=(), payload=PAYLOAD, with_model=True, pin=True, name="a.tar.gz"):
        archive = self.root / name
        with tarfile.open(archive, "w:gz") as tar:
            def add(info, data=None):
                tar.addfile(info, io.BytesIO(data) if data is not None else None)
            if with_model:
                info = tarfile.TarInfo("model.bin"); info.size = len(payload)
                add(info, payload)
            info = tarfile.TarInfo("licenses/atom/NOTICE.md"); info.size = 2
            add(info, b"ok")
            for member, data in extra:
                add(member, data)
        raw = archive.read_bytes()
        manifest = {"files": [{"path": "model.bin", "bytes": len(PAYLOAD),
                               "sha256": hashlib.sha256(PAYLOAD).hexdigest()}],
                    "bytesTotal": len(PAYLOAD), "hardware": {"minFreeBytes": 0},
                    "source": {"archiveBytes": len(raw),
                               "archiveSha256": hashlib.sha256(raw).hexdigest() if pin else "0" * 64,
                               "archiveLicensePaths": ["licenses/atom/NOTICE.md"]}}
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest))
        return archive, path

    def run_install(self, archive, manifest):
        destination = self.root / "out"
        result = install(manifest, archive.as_uri(), destination)
        return destination, result

    def test_good_archive(self):
        archive, manifest = self.build()
        destination, result = self.run_install(archive, manifest)
        self.assertFalse(result["reused"])
        self.assertEqual((destination / "model.bin").read_bytes(), PAYLOAD)
        self.assertTrue((destination / "licenses/atom/NOTICE.md").is_file())

    def test_archive_hash_mismatch_refused_before_extraction(self):
        archive, manifest = self.build(pin=False)
        with self.assertRaisesRegex(ValueError, "Archive checksum mismatch"):
            self.run_install(archive, manifest)
        self.assertFalse((self.root / "out").exists())

    def test_flipped_byte_refused(self):
        archive, manifest = self.build()
        data = bytearray(archive.read_bytes()); data[len(data) // 2] ^= 1
        archive.write_bytes(bytes(data))
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.run_install(archive, manifest)
        self.assertFalse((self.root / "out").exists())

    def test_unpinned_manifest_refused(self):
        archive, manifest = self.build()
        spec = json.loads(manifest.read_text()); del spec["source"]["archiveSha256"]
        manifest.write_text(json.dumps(spec))
        with self.assertRaisesRegex(ValueError, "does not pin"):
            self.run_install(archive, manifest)

    def _bad(self, member, data=None, pattern="."):
        archive, manifest = self.build(extra=[(member, data)])
        with self.assertRaisesRegex(ValueError, pattern):
            self.run_install(archive, manifest)
        self.assertFalse((self.root / "out").exists())

    def test_traversal(self):
        info = tarfile.TarInfo("../evil"); info.size = 1
        self._bad(info, b"x", "Unsafe")

    def test_absolute(self):
        info = tarfile.TarInfo("/etc/evil"); info.size = 1
        self._bad(info, b"x", "Unsafe")

    def test_symlink(self):
        info = tarfile.TarInfo("link"); info.type = tarfile.SYMTYPE; info.linkname = "/etc/passwd"
        self._bad(info, None, "links")

    def test_hardlink(self):
        info = tarfile.TarInfo("hard"); info.type = tarfile.LNKTYPE; info.linkname = "model.bin"
        self._bad(info, None, "links")

    def test_device(self):
        info = tarfile.TarInfo("dev"); info.type = tarfile.CHRTYPE
        self._bad(info, None, "regular")

    def test_unexpected_member(self):
        info = tarfile.TarInfo("extra.txt"); info.size = 1
        self._bad(info, b"x", "Unexpected")

    def test_duplicate_member(self):
        info = tarfile.TarInfo("model.bin"); info.size = len(PAYLOAD)
        self._bad(info, PAYLOAD, "Duplicate")

    def test_member_over_manifest_size(self):
        archive, manifest = self.build(payload=PAYLOAD + b"more")
        with self.assertRaisesRegex(ValueError, "exceeds"):
            self.run_install(archive, manifest)

    def test_missing_model_file(self):
        archive, manifest = self.build(with_model=False)
        with self.assertRaisesRegex(ValueError, "missing"):
            self.run_install(archive, manifest)

    def test_non_https_remote_refused(self):
        archive, manifest = self.build()
        with self.assertRaisesRegex(ValueError, "HTTPS or file"):
            install(manifest, "http://example.com/a.tar.gz", self.root / "out")


if __name__ == "__main__":
    unittest.main()
