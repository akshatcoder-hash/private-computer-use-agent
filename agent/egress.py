"""The only two places a screenshot may be sent.

Every function that transmits screenshot bytes calls one of these checks
first. tests/test_egress.py asserts that nothing else in the package opens a
network connection.
"""
from ipaddress import ip_address
from urllib.parse import urlparse

PLANNER_BASE_URL = "https://cloud-api.near.ai/v1"


class EgressError(RuntimeError):
    """A screenshot was about to be sent somewhere it must not go."""


def check_planner_url(url: str) -> str:
    if url.rstrip("/") != PLANNER_BASE_URL:
        raise EgressError(f"planner must be {PLANNER_BASE_URL}, not {url}")
    return PLANNER_BASE_URL


def check_atom_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    try:
        loopback = ip_address(host).is_loopback
    except ValueError:
        loopback = False  # names, including "localhost", can be re-pointed; require an IP literal
    if parsed.scheme != "http" or not loopback:
        raise EgressError(f"Atom must be a loopback IP address, not {url}")
    return url.rstrip("/")
