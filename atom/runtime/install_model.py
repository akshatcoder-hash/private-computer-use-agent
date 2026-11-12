#!/usr/bin/env python3
"""Install a frozen Atom package (release .tar.gz or per-file source) from HTTPS or file://, verifying every byte."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile
from urllib.parse import quote, urlparse, unquote
from urllib.request import urlopen


def verify_directory(directory: Path, manifest: dict):
    total = 0
    for entry in manifest["files"]:
        relative = PurePosixPath(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe model manifest path")
        path = directory / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing model file: {entry['path']}")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                digest.update(block)
        if size != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
            raise ValueError(f"Model checksum mismatch: {entry['path']}")
        total += size
    if total != manifest["bytesTotal"]:
        raise ValueError("Model total bytes do not match manifest")
    return total


LICENSE_CAP = 1024 * 1024
ARCHIVE_SUFFIXES = (".tar.gz", ".tgz")


def is_archive_source(source: str) -> bool:
    return urlparse(source).path.lower().endswith(ARCHIVE_SUFFIXES)


def _event(**fields):
    print(json.dumps({"event": "archive", **fields}), file=sys.stderr, flush=True)


def fetch_archive(source: str, parsed, manifest: dict, target: Path) -> str:
    """Copy or download the archive to target; enforce size cap; return SHA-256."""
    spec = manifest.get("source", {})
    expected_bytes = spec.get("archiveBytes")
    expected_sha = spec.get("archiveSha256")
    if not expected_sha or not expected_bytes:
        raise ValueError("Manifest does not pin the archive size and SHA-256")
    digest = hashlib.sha256()
    size = 0
    if parsed.scheme == "file":
        source_path = Path(unquote(parsed.path))
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError("Archive source must be a regular file")
        stream_cm = source_path.open("rb")
    else:
        stream_cm = urlopen(source, timeout=60)
    with stream_cm as response, target.open("wb") as output:
        if parsed.scheme == "https" and urlparse(response.url).scheme != "https":
            raise ValueError("Model source redirected away from HTTPS")
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > expected_bytes:
                raise ValueError("Archive download exceeds manifest size")
            digest.update(block)
            output.write(block)
    actual = digest.hexdigest()
    _event(sha256=actual, bytes=size, expected=expected_sha)
    if size != expected_bytes or actual != expected_sha:
        raise ValueError(f"Archive checksum mismatch: got sha256 {actual}, bytes {size}")
    return actual


def extract_archive(archive: Path, manifest: dict, staging: Path):
    """Extract only manifest files and license files as plain regular files."""
    allowed = {entry["path"]: entry["bytes"] for entry in manifest["files"]}
    for name in manifest.get("source", {}).get("archiveLicensePaths", []):
        allowed[name] = LICENSE_CAP
    seen = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            name = member.name
            relative = PurePosixPath(name)
            if (relative.is_absolute() or ".." in relative.parts or "\\" in name
                    or "\0" in name or name.startswith("./")):
                raise ValueError(f"Unsafe archive path: {name!r}")
            if member.issym() or member.islnk():
                raise ValueError(f"Archive links are not accepted: {name!r}")
            if not member.isreg():
                raise ValueError(f"Archive member is not a regular file: {name!r}")
            if name not in allowed:
                raise ValueError(f"Unexpected archive member: {name!r}")
            if name in seen:
                raise ValueError(f"Duplicate archive member: {name!r}")
            if member.size > allowed[name]:
                raise ValueError(f"Archive member exceeds manifest size: {name!r}")
            seen.add(name)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    missing = [entry["path"] for entry in manifest["files"] if entry["path"] not in seen]
    if missing:
        raise ValueError(f"Archive is missing files: {missing[0]}")


def install(manifest_path: Path, source: str, destination: Path):
    manifest = json.loads(manifest_path.read_text())
    if not source:
        source = manifest.get("source", {}).get("archiveURL", "")
    if not source:
        raise ValueError("No model source configured. Pass --source (https://, file://, directory or .tar.gz).")
    parsed = urlparse(source)
    if parsed.scheme not in ("https", "file"):
        raise ValueError("Model source must use HTTPS or file://")
    if parsed.scheme == "file" and parsed.netloc not in ("", "localhost"):
        raise ValueError("file:// source must be a local path")
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Keep an existing valid install intact; concurrent installers share a lock.
    with (destination.parent / ".atom-install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if destination.exists():
            return {"installed": True, "bytes": verify_directory(destination, manifest),
                    "directory": str(destination), "reused": True}
        if shutil.disk_usage(destination.parent).free < manifest["hardware"]["minFreeBytes"]:
            raise ValueError("At least 400 MB of free disk space is required")
        with tempfile.TemporaryDirectory(prefix=".atom-download-", dir=destination.parent) as temporary:
            staging = Path(temporary) / "model"
            staging.mkdir()
            complete = 0
            archive_mode = is_archive_source(source)
            if archive_mode:
                archive = Path(temporary) / "download.tar.gz"
                fetch_archive(source, parsed, manifest, archive)
                extract_archive(archive, manifest, staging)
                archive.unlink()
            for entry in ([] if archive_mode else manifest["files"]):
                relative = PurePosixPath(entry["path"])
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Unsafe model manifest path")
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if parsed.scheme == "file":
                    source_path = Path(unquote(parsed.path)) / relative
                    if source_path.is_symlink():
                        raise ValueError("Model source symlinks are not accepted")
                    shutil.copyfile(source_path, target)
                else:
                    url = source.rstrip("/") + "/" + quote(str(relative))
                    with urlopen(url, timeout=60) as response, target.open("wb") as output:
                        if urlparse(response.url).scheme != "https":
                            raise ValueError("Model source redirected away from HTTPS")
                        downloaded = 0
                        while block := response.read(1024 * 1024):
                            downloaded += len(block)
                            if downloaded > entry["bytes"]:
                                raise ValueError("Model download exceeds manifest size")
                            output.write(block)
                complete += entry["bytes"]
                print(json.dumps({"event": "progress", "bytes": complete,
                                  "total": manifest["bytesTotal"], "file": entry["path"]}),
                      file=sys.stderr, flush=True)
            verify_directory(staging, manifest)
            notices = Path(__file__).resolve().parent / "licenses/atom"
            if not (staging / "licenses/atom").is_dir():
                shutil.copytree(notices, staging / "licenses/atom")
            staging.rename(destination)
        return {"installed": True, "bytes": manifest["bytesTotal"], "directory": str(destination), "reused": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parent / "model-manifest.json")
    parser.add_argument("--source", default="",
                        help="https:// or file:// archive (.tar.gz) or file directory; "
                             "default is the archive URL pinned in the manifest")
    parser.add_argument("--destination", type=Path, default=Path(os.environ.get("ATOM_MODEL_DIR") or Path.home() / "Library/Application Support/PrivateAgent/Atom/Models/tinyclick-4bit"))
    args = parser.parse_args()
    try:
        result = install(args.manifest, args.source, args.destination)
        print(json.dumps({"ok": True, "result": result}))
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
