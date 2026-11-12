import ast
import os
import unittest

from agent.egress import EgressError, PLANNER_BASE_URL, check_atom_url, check_planner_url
from agent import grounder, planner

AGENT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent")
NETWORK_MODULES = {"urllib", "http", "socket", "ssl", "httpx", "requests", "aiohttp", "websockets", "websocket",
                   "ftplib", "smtplib", "xmlrpc", "nearai_inference_sdk", "openai", "anthropic", "grpc"}
ALLOWED = {"grounder.py": {"urllib"}, "planner.py": {"nearai_inference_sdk"}}


def _read(path):
    with open(path) as stream:
        return stream.read()


class AtomUrl(unittest.TestCase):
    def test_loopback_ip_literals_pass(self):
        for url in ("http://127.0.0.1:8760", "http://127.0.0.1:8760/", "http://[::1]:8760", "http://127.5.5.5:1"):
            self.assertEqual(check_atom_url(url), url.rstrip("/"))

    def test_everything_else_fails(self):
        for url in ("http://localhost:8760", "http://0.0.0.0:8760", "http://192.168.1.5:8760", "https://127.0.0.1:8760",
                    "http://example.com", "http://127.0.0.1.evil.com", "http://user@evil.com:80@127.0.0.1", "file:///tmp/x", ""):
            with self.assertRaises(EgressError, msg=url):
                check_atom_url(url)

    def test_configured_atom_url_is_loopback(self):
        check_atom_url(grounder.ATOM_URL)


class NoSocketForBadAtomUrl(unittest.TestCase):
    def test_ground_raises_before_any_socket_is_opened(self):
        import socket
        from unittest import mock
        png = b"\x89PNG" + b"\0" * 32
        for url in ("http://example.com:8760", "http://localhost:8760", "http://192.168.1.5:8760"):
            with mock.patch.object(grounder, "ATOM_URL", url), \
                    mock.patch.object(socket.socket, "connect", side_effect=AssertionError("socket opened")) as connect, \
                    mock.patch.object(socket, "create_connection", side_effect=AssertionError("socket opened")) as create, \
                    mock.patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS lookup")) as dns:
                with self.assertRaises(EgressError, msg=url):
                    grounder.ground("click the button", png)
                with self.assertRaises(EgressError, msg=url):
                    grounder.health()
                connect.assert_not_called()
                create.assert_not_called()
                dns.assert_not_called()


class PlannerUrl(unittest.TestCase):
    def test_exact_url(self):
        self.assertEqual(PLANNER_BASE_URL, "https://cloud-api.near.ai/v1")
        self.assertEqual(check_planner_url("https://cloud-api.near.ai/v1/"), PLANNER_BASE_URL)

    def test_other_urls_fail(self):
        for url in ("http://cloud-api.near.ai/v1", "https://cloud-api.near.ai.evil.com/v1", "https://cloud-api.near.ai/v2",
                    "https://api.openai.com/v1", "http://127.0.0.1:8760"):
            with self.assertRaises(EgressError, msg=url):
                check_planner_url(url)
            with self.assertRaises(EgressError, msg=url):
                planner.Planner("k", base_url=url)


class NoOtherNetworkClients(unittest.TestCase):
    def test_only_grounder_and_planner_import_network_clients(self):
        offenders = []
        for name in sorted(os.listdir(AGENT_DIR)):
            if not name.endswith(".py"):
                continue
            tree = ast.parse(_read(os.path.join(AGENT_DIR, name)))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {alias.name.split(".")[0] for alias in node.names if alias.name != "urllib.parse"}
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0 and node.module != "urllib.parse":
                    roots = {node.module.split(".")[0]}
                else:
                    continue
                for root in roots & NETWORK_MODULES:
                    if root not in ALLOWED.get(name, set()):
                        offenders.append(f"{name} imports {root}")
        self.assertEqual(offenders, [])

    def test_no_dynamic_imports_or_subprocess_network_tools(self):
        for name in os.listdir(AGENT_DIR):
            if name.endswith(".py"):
                source = _read(os.path.join(AGENT_DIR, name))
                for needle in ("__import__(", "importlib", "curl", "wget"):
                    self.assertNotIn(needle, source, f"{name} contains {needle}")


if __name__ == "__main__":
    unittest.main()
