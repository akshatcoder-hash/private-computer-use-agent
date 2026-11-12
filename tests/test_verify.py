import io
import json
import os
import shutil
import tempfile
import unittest

import nacl.signing

from agent.verify import verify_run

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "run")


class VerifyRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "run")
        shutil.copytree(FIXTURE, self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self) -> tuple[bool, str]:
        out = io.StringIO()
        return verify_run(self.dir, out), out.getvalue()

    def edit_json(self, name, fn):
        path = os.path.join(self.dir, name)
        with open(path) as stream:
            data = json.load(stream)
        fn(data)
        with open(path, "w") as stream:
            json.dump(data, stream)

    def test_recorded_run_passes(self):
        ok, text = self.verify()
        self.assertTrue(ok, text)
        self.assertIn("3 of 3 step(s) verified", text)

    def test_fixture_has_no_secrets(self):
        for name in os.listdir(FIXTURE):
            with open(os.path.join(FIXTURE, name), "rb") as stream:
                data = stream.read()
            self.assertNotIn(b"NEARAI_API_KEY", data)
            self.assertNotIn(b"sk-", data)

    def flip(self, name):
        path = os.path.join(self.dir, name)
        with open(path, "rb") as stream:
            data = bytearray(stream.read())
        data[len(data) // 2] ^= 1
        with open(path, "wb") as stream:
            stream.write(data)

    def test_tampered_response_byte_fails(self):
        self.flip("step-1.response.bin")
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("response.bin hash differs", text)

    def test_tampered_request_byte_fails(self):
        self.flip("step-2.request.bin")
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("request.bin hash differs", text)

    def test_tampered_signature_fails(self):
        def tamper(step):
            sig = step["verification"]["signature"]
            step["verification"]["signature"] = sig[:-1] + ("0" if sig[-1] != "0" else "1")
        self.edit_json("step-1.json", tamper)
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("signature invalid", text)

    def test_tampered_signed_text_fails(self):
        self.edit_json("step-3.json", lambda s: s["verification"].update(signed_text=s["verification"]["signed_text"].replace("Qwen", "Qwex")))
        ok, text = self.verify()
        self.assertFalse(ok)

    def test_wrong_signer_fails(self):
        other = nacl.signing.SigningKey.generate().verify_key.encode().hex()
        self.edit_json("step-2.json", lambda s: s["verification"].update(signer=other))
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("signer is not the attested signer", text)

    def test_attestation_signer_mismatch_fails(self):
        other = nacl.signing.SigningKey.generate().verify_key.encode().hex()
        self.edit_json("attestation.json", lambda a: a["model"].update(signer=other))
        ok, text = self.verify()
        self.assertFalse(ok)

    def test_self_signed_forgery_with_attacker_key_fails(self):
        """A forger re-signs a tampered response with their own key and records that key as the signer."""
        import hashlib
        key = nacl.signing.SigningKey.generate()
        path = os.path.join(self.dir, "step-1.response.bin")
        with open(path, "rb") as stream:
            data = stream.read() + b" "
        with open(path, "wb") as stream:
            stream.write(data)
        def forge(step):
            v = step["verification"]
            model, req_hash = v["signed_text"].split(":")[0:2]
            text = f"{model}:{req_hash}:{hashlib.sha256(data).hexdigest()}"
            v.update(signed_text=text, signer=key.verify_key.encode().hex(), signature=key.sign(text.encode()).signature.hex())
        self.edit_json("step-1.json", forge)
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("signer is not the attested signer", text)

    def test_missing_wire_file_fails(self):
        os.remove(os.path.join(self.dir, "step-1.request.bin"))
        ok, _ = self.verify()
        self.assertFalse(ok)

    def test_wrong_model_prefix_fails(self):
        self.edit_json("attestation.json", lambda a: a.update(model_id="Other/Model"))
        ok, text = self.verify()
        self.assertFalse(ok)

    def test_edited_action_fails(self):
        def edit(step):
            step["planner"]["action"]["target"] = "the Delete account button"
        self.edit_json("step-1.json", edit)
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("recorded action differs", text)

    def test_edited_action_type_fails(self):
        self.edit_json("step-3.json", lambda s: s["planner"]["action"].update(action="fail", reason="x"))
        ok, text = self.verify()
        self.assertFalse(ok)

    def test_swapped_reply_key_fails(self):
        shutil.copy(os.path.join(self.dir, "step-2.reply_key.hex"), os.path.join(self.dir, "step-1.reply_key.hex"))
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("cannot decrypt", text)

    def test_garbage_reply_key_fails(self):
        with open(os.path.join(self.dir, "step-1.reply_key.hex"), "w") as stream:
            stream.write("ed25519:zz")
        ok, text = self.verify()
        self.assertFalse(ok)

    def test_old_receipt_without_key_is_labelled_not_bound(self):
        for n in (1, 2, 3):
            os.remove(os.path.join(self.dir, f"step-{n}.reply_key.hex"))
        ok, text = self.verify()
        self.assertTrue(ok, text)
        self.assertIn("not bound", text)

    def test_fixture_reports_action_bound(self):
        ok, text = self.verify()
        self.assertNotIn("not bound", text)

    def test_reply_key_cannot_decrypt_the_request(self):
        """Safety claim: the stored key decrypts only the reply; the screenshot in the request stays sealed."""
        from nearai_inference_sdk.core.e2ee import decrypt_e2ee_text
        from agent.planner import load_reply_key
        with open(os.path.join(self.dir, "step-1.request.bin"), "rb") as stream:
            body = json.load(stream)
        with open(os.path.join(self.dir, "step-1.reply_key.hex")) as stream:
            key = load_reply_key(stream.read())
        content = body["messages"][1]["content"]
        self.assertIsInstance(content, str)
        self.assertNotIn("data:image", content)
        with self.assertRaises(Exception):
            decrypt_e2ee_text(content, key, "messages[].content")


if __name__ == "__main__":
    unittest.main()
