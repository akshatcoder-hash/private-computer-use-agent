import asyncio
import json
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from agent import planner as planner_module
from agent.planner import Planner, PlannerUnverified, parse_action, request_binding, sha256_hex

MODEL = "Qwen/Qwen3.8-27B"
REQ, RESP = b"request", b"response"


def planner(allow_gateway=False):
    p = Planner("k", MODEL, allow_gateway_signature=allow_gateway)
    p._model_signers, p._gateway_signer = {"aa"}, "bb"
    return p


def verified(kind, signer, text=None):
    text = text or (f"{MODEL}:{sha256_hex(REQ)}:{sha256_hex(RESP)}" if kind == "provider_tee"
                    else f"{sha256_hex(REQ)}:{sha256_hex(RESP)}")
    return NS(signature_kind=kind, signature=NS(signer=NS(signing_address=signer, signing_algo="ed25519"), signed_text=text, signature="00"))


class Check(unittest.TestCase):
    def test_provider_tee_with_attested_signer_passes(self):
        v = planner()._check(verified("provider_tee", "AA"), REQ, RESP)
        self.assertTrue(v["verified"] and v["signer_matches_attested_model"])

    def test_gateway_signature_refused_by_default(self):
        with self.assertRaises(PlannerUnverified):
            planner()._check(verified("gateway", "bb"), REQ, RESP)

    def test_gateway_signature_allowed_only_with_opt_in(self):
        v = planner(True)._check(verified("gateway", "bb"), REQ, RESP)
        self.assertEqual(v["signature_kind"], "gateway")
        self.assertFalse(v["signer_matches_attested_model"])

    def test_unattested_signer_refused(self):
        with self.assertRaises(PlannerUnverified):
            planner()._check(verified("provider_tee", "cc"), REQ, RESP)

    def test_hash_mismatch_refused(self):
        with self.assertRaises(PlannerUnverified):
            planner()._check(verified("provider_tee", "aa"), REQ, RESP + b"x")


PNG = b"\x89PNG fake"
SIGNED = {"thought": "t", "action": "click", "target": "the Save button"}


class FakeClient:
    """Stands in for the SDK client: returns one completion whose parsed content is `sdk_text`."""

    def __init__(self, sdk_text, user_sent):
        self.sdk_text, self.user_sent = sdk_text, user_sent
        body = {"model": MODEL, "user": user_sent}
        self.request, self.response = json.dumps(body).encode(), b"wire-response"
        self.chat = NS(completions=NS(create=self.create))

    async def create(self, **kwargs):
        self.kwargs = kwargs
        planner_module._CAPTURED_KEYS.append(object())
        msg = NS(content=self.sdk_text)
        return NS(id="c1", choices=[NS(message=msg)], usage=None)

    def _get_response_record(self, id):
        async def body():
            return self.response
        return NS(request_body=self.request, response_body=body())

    async def verify_response(self, id):
        text = f"{MODEL}:{sha256_hex(self.request)}:{sha256_hex(self.response)}"
        return NS(signature_kind="provider_tee", signature=NS(
            signer=NS(signing_address="aa", signing_algo="ed25519"), signed_text=text, signature="00"))


def run_plan(sdk_text, wire_action=SIGNED, user_sent=None):
    p = planner()
    p.attestation = {}
    bind = request_binding("r1", 2, sha256_hex(PNG))
    p._client = FakeClient(sdk_text, user_sent or bind)
    with mock.patch.object(planner_module, "serialize_reply_key", return_value="ed25519:00"), \
            mock.patch.object(planner_module, "action_from_wire", return_value=wire_action):
        return p, asyncio.run(p.plan(PNG, "task", [], run_id="r1", step=2))


class ActOnSignedBytes(unittest.TestCase):
    def test_matching_action_is_returned_from_the_wire_bytes(self):
        p, result = run_plan(json.dumps(SIGNED))
        self.assertEqual(result.action, SIGNED)
        self.assertEqual(p._client.kwargs["user"], request_binding("r1", 2, sha256_hex(PNG)))

    def test_sdk_parsed_action_that_differs_from_the_signed_bytes_is_refused(self):
        evil = dict(SIGNED, target="the Delete account button")
        with self.assertRaises(PlannerUnverified) as caught:
            run_plan(json.dumps(evil))
        self.assertIn("differs from the action in the signed reply", str(caught.exception))

    def test_unparseable_sdk_content_is_refused(self):
        with self.assertRaises(PlannerUnverified):
            run_plan("not json")

    def test_undecryptable_wire_is_refused(self):
        p = planner()
        p.attestation = {}
        p._client = FakeClient(json.dumps(SIGNED), request_binding("r1", 2, sha256_hex(PNG)))
        with mock.patch.object(planner_module, "serialize_reply_key", return_value="ed25519:00"):
            with self.assertRaises(PlannerUnverified):
                asyncio.run(p.plan(PNG, "task", [], run_id="r1", step=2))

    def test_request_without_the_binding_is_refused(self):
        with self.assertRaises(PlannerUnverified) as caught:
            run_plan(json.dumps(SIGNED), user_sent="other:1:00")
        self.assertIn("binding", str(caught.exception))


class Parse(unittest.TestCase):
    def test_plain_fenced_and_wrapped(self):
        for text in ('{"thought":"t","action":"click","target":"the A button"}',
                     '```json\n{"thought":"t","action":"click","target":"the A button"}\n```',
                     'Sure: {"thought":"t","action":"click","target":"the A button"} done'):
            self.assertEqual(parse_action(text)["target"], "the A button")

    def test_invalid(self):
        for text in ("", "nope", '{"action":"click"}', '{"action":"dance"}'):
            with self.assertRaises(ValueError):
                parse_action(text)


if __name__ == "__main__":
    unittest.main()
