import unittest
from types import SimpleNamespace as NS

from agent.planner import Planner, PlannerUnverified, parse_action, sha256_hex

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
