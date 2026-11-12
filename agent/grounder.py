"""Atom: the local grounding model. Screenshot and instruction in, click point out."""
import base64
import json
import os
import urllib.error
import urllib.request

from .egress import check_atom_url

ATOM_URL = os.environ.get("ATOM_URL", "http://127.0.0.1:8760")


class GroundingError(RuntimeError):
    pass


def _call(path: str, body: dict | None = None, timeout: float = 180) -> dict:
    url = check_atom_url(ATOM_URL) + path
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data, {"content-type": "application/json"})
    # No proxy: a system HTTP proxy must not see the screenshot.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            reply = json.load(response)
    except urllib.error.HTTPError as error:
        reply = json.load(error)
    except OSError as error:
        raise GroundingError(f"Atom is not reachable at {ATOM_URL}: {error}") from error
    if not reply.get("ok"):
        raise GroundingError(f"Atom error: {reply.get('error')}")
    return reply["result"]


def health() -> dict:
    return _call("/health", timeout=10)


def ground(instruction: str, png: bytes) -> dict:
    """Returns {"pixel": {"x", "y"}, "image": {"width", "height"}, "duration_ms", ...}."""
    result = _call("/ground", {"instruction": instruction, "image_base64": base64.b64encode(png).decode()})
    if result.get("action") != "click":
        raise GroundingError(f"Atom abstained on {instruction!r}: {result.get('reason', 'no point returned')}")
    return result
