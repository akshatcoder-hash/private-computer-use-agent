import io
import json
import os
import shutil
import tempfile
import unittest

import nacl.signing

from agent.verify import _check_ending, verify_run

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
        self.edit_json("attestation.json", lambda a: (a["model"].update(signer=other), a.update(model_signers=[other])))
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

    def rewrite_events(self, fn):
        path = os.path.join(self.dir, "events.jsonl")
        with open(path) as stream:
            events = [json.loads(line) for line in stream if line.strip()]
        fn(events)
        with open(path, "w") as stream:
            for event in events:
                stream.write(json.dumps(event) + "\n")

    def add_final_step(self, *types, replace_done=True):
        """Append a final step that stopped before a verified reply, with the given event types. By default it
        takes the place of the fixture's step 3 (a signed done cannot be followed by another step)."""
        n = 3 if replace_done else 4
        if replace_done:
            for name in os.listdir(self.dir):
                if name.startswith("step-3."):
                    os.remove(os.path.join(self.dir, name))
        with open(os.path.join(self.dir, f"step-{n}.json"), "w") as stream:
            json.dump({"step": n, "events": list(types)}, stream)

        def go(events):
            finished = events.pop()
            self.assertEqual(finished["type"], "run_finished")
            if replace_done:
                events[:] = [e for e in events if e.get("step") != 3]
            for kind in types:
                events.append({"type": kind, "step": n, "reason": "covered by Other"})
            finished["steps"] = n
            finished["status"] = "stopped"
            events.append(finished)
        self.rewrite_events(go)

    def test_guard_stop_after_verified_steps_still_passes(self):
        self.add_final_step("guard_blocked")
        ok, text = self.verify()
        self.assertTrue(ok, text)
        self.assertIn("2 of 3 step(s) verified", text)
        self.assertIn("REFUSED", text)

    def test_planner_unverified_final_step_passes_as_refused(self):
        self.add_final_step("step_started", "planner_unverified")
        ok, text = self.verify()
        self.assertTrue(ok, text)

    def test_unverified_step_without_guard_event_fails(self):
        self.add_final_step("step_started")
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("no guard_blocked or planner_unverified", text)

    def test_refused_step_with_acted_event_fails(self):
        self.add_final_step("guard_blocked", "acted")
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("acted event", text)

    def test_refused_step_must_be_final(self):
        self.add_final_step("guard_blocked")
        # Turn the middle step into a refusal while a later step exists.
        def strip(step):
            del step["verification"]
        self.edit_json("step-2.json", strip)
        self.rewrite_events(lambda ev: ev.insert(-2, {"type": "guard_blocked", "step": 2}))
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("not the final step", text)

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

    def test_old_receipt_without_key_fails_by_default(self):
        for n in (1, 2, 3):
            os.remove(os.path.join(self.dir, f"step-{n}.reply_key.hex"))
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("action not bound", text)

    def test_allow_unbound_accepts_and_says_so(self):
        for n in (1, 2, 3):
            os.remove(os.path.join(self.dir, f"step-{n}.reply_key.hex"))
        out = io.StringIO()
        ok = verify_run(self.dir, out, allow_unbound=True)
        self.assertTrue(ok, out.getvalue())
        self.assertIn("--allow-unbound", out.getvalue())
        self.assertIn("NOT proven", out.getvalue())
        self.assertNotIn("every step's signed reply is bound", out.getvalue())

    def test_forged_receipt_from_the_review_now_fails(self):
        """Delete a reply key, change that step's action, delete the last step file: was 'PASS: 2 of 2'."""
        os.remove(os.path.join(self.dir, "step-2.reply_key.hex"))
        os.remove(os.path.join(self.dir, "step-3.json"))
        self.edit_json("step-2.json", lambda s: s["planner"]["action"].update(target="the Delete account button"))
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertTrue(text.startswith("FAIL"), text)
        out = io.StringIO()
        self.assertFalse(verify_run(self.dir, out, allow_unbound=True), out.getvalue())

    def truncate_to_two(self, status="done", max_steps=None):
        """Drop step 3 (files and events), keep run_finished consistent with 2 steps."""
        for name in os.listdir(self.dir):
            if name.startswith("step-3."):
                os.remove(os.path.join(self.dir, name))

        def go(events):
            events[:] = [e for e in events if e.get("step") != 3]
            for e in events:
                if e["type"] == "run_finished":
                    e.update(steps=2, status=status)
                if e["type"] == "run_started" and max_steps is not None:
                    e["max_steps"] = max_steps
        self.rewrite_events(go)

    def test_truncation_forgery_fails(self):
        """Drop step 3 and its events, set steps=2, keep status 'done': the last action is a click, not a signed done."""
        self.truncate_to_two()
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("not 'done'", text)

    def test_truncated_run_relabelled_stopped_without_budget_fails(self):
        self.truncate_to_two(status="stopped")
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("no max_steps", text)

    def test_budget_exhausted_stop_passes(self):
        self.truncate_to_two(status="stopped", max_steps=2)
        ok, text = self.verify()
        self.assertTrue(ok, text)

    def test_stopped_before_budget_without_refusal_fails(self):
        self.truncate_to_two(status="stopped", max_steps=12)
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("step budget", text)

    def test_status_failed_with_done_action_fails(self):
        self.rewrite_events(lambda ev: ev[-1].update(status="failed"))
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("not 'fail'", text)

    def test_unknown_status_fails(self):
        self.rewrite_events(lambda ev: ev[-1].update(status="ok"))
        ok, text = self.verify()
        self.assertFalse(ok)

    def test_status_done_but_last_step_refused_fails(self):
        self.add_final_step("guard_blocked")
        self.rewrite_events(lambda ev: ev[-1].update(status="done"))
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("not 'done'", text)

    def test_done_in_the_middle_fails(self):
        """The fixture's signed done at step 3, followed by a step 4: done may only be last."""
        self.add_final_step("guard_blocked", replace_done=False)
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("not the last step", text)

    def ending(self, actions, status, **started):
        files = {i + 1: ({"verification": {}, "planner": {"action": {"action": a}}} if a else {}) for i, a in enumerate(actions)}
        return _check_ending(files, len(actions), status, started, [], False)

    def test_signed_fail_with_status_failed_passes(self):
        self.assertEqual(self.ending(["click", "fail"], "failed"), [])

    def test_fail_with_status_done_fails(self):
        self.assertTrue(self.ending(["click", "fail"], "done"))

    def test_fail_in_the_middle_fails(self):
        self.assertTrue(self.ending(["fail", "click"], "stopped", max_steps=2))

    def test_missing_last_step_fails(self):
        os.remove(os.path.join(self.dir, "step-3.json"))
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("missing [3]", text)

    def test_missing_middle_step_fails(self):
        os.remove(os.path.join(self.dir, "step-2.json"))
        ok, text = self.verify()
        self.assertFalse(ok)

    def test_extra_step_fails(self):
        shutil.copy(os.path.join(self.dir, "step-3.json"), os.path.join(self.dir, "step-4.json"))
        self.edit_json("step-4.json", lambda s: s.update(step=4))
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("unexpected [4]", text)

    def test_extra_wire_files_without_step_file_fail(self):
        shutil.copy(os.path.join(self.dir, "step-3.request.bin"), os.path.join(self.dir, "step-4.request.bin"))
        ok, text = self.verify()
        self.assertFalse(ok)

    def test_run_finished_step_count_mismatch_fails(self):
        def go(events):
            events[-1]["steps"] = 2
        self.rewrite_events(go)
        ok, text = self.verify()
        self.assertFalse(ok)

    def test_missing_run_finished_fails(self):
        self.rewrite_events(lambda events: events.pop())
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("run_finished", text)

    def test_step_file_disagreeing_with_events_fails(self):
        def go(events):
            for e in events:
                if e["type"] == "planner_verified" and e["step"] == 1:
                    e["planner"]["completion_id"] = "other"
        self.rewrite_events(go)
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("completion_id differs", text)

    def test_action_disagreeing_with_events_fails(self):
        def go(events):
            for e in events:
                if e["type"] == "planner_verified" and e["step"] == 2:
                    e["planner"]["action"] = {"thought": "x", "action": "click", "target": "the Delete account button"}
        self.rewrite_events(go)
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("action differs from events.jsonl", text)

    def test_spliced_screenshot_binding_fails(self):
        def go(events):
            for e in events:
                if e["type"] == "step_started" and e["step"] == 1:
                    e["screenshot"]["sha256"] = "0" * 64
        self.rewrite_events(go)
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("request binding", text)

    def test_spliced_run_id_binding_fails(self):
        def go(events):
            events[0]["run_id"] = "another-run"
        self.rewrite_events(go)
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("request binding", text)

    # -- re-signing helpers: a forger who controls the attested signer (to isolate one check at a time)
    def resign(self, n, request=None, kind="provider_tee", gateway_ok=False):
        key = nacl.signing.SigningKey.generate()
        signer = key.verify_key.encode().hex()
        import hashlib
        req_path, resp_path = (os.path.join(self.dir, f"step-{n}.{x}.bin") for x in ("request", "response"))
        if request is not None:
            with open(req_path, "wb") as stream:
                stream.write(request)
        with open(req_path, "rb") as stream:
            rq = stream.read()
        with open(resp_path, "rb") as stream:
            rs = stream.read()
        hashes = f"{hashlib.sha256(rq).hexdigest()}:{hashlib.sha256(rs).hexdigest()}"
        text = f"Qwen/Qwen3.8-27B:{hashes}" if kind == "provider_tee" else hashes
        update = dict(signed_text=text, signer=signer, signature=key.sign(text.encode()).signature.hex(), signature_kind=kind)
        self.edit_json(f"step-{n}.json", lambda s: s["verification"].update(update))
        def go(events):
            for e in events:
                if e["type"] == "planner_verified" and e["step"] == n:
                    e["verification"].update(update)
                if e["type"] == "run_started" and gateway_ok:
                    e["allow_gateway_signature"] = True
        self.rewrite_events(go)
        return signer

    def attest(self, signer, kind="provider_tee"):
        def go(a):
            if kind == "provider_tee":
                a["model_signers"] = list(a.get("model_signers", [])) + [signer]
            else:
                a["gateway"]["signer"] = signer
        self.edit_json("attestation.json", go)

    def test_request_without_user_field_fails_unless_allow_unbound(self):
        with open(os.path.join(self.dir, "step-1.request.bin"), "rb") as stream:
            body = json.load(stream)
        del body["user"]
        signer = self.resign(1, json.dumps(body).encode())
        self.attest(signer)
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("no `user` field", text)
        out = io.StringIO()
        self.assertTrue(verify_run(self.dir, out, allow_unbound=True), out.getvalue())

    def test_any_attested_model_signer_is_accepted(self):
        signer = self.resign(1)
        self.attest(signer)  # appended to model_signers; model.signer (the first) still differs
        ok, text = self.verify()
        self.assertTrue(ok, text)

    def test_signer_outside_model_signers_fails(self):
        self.resign(1)
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("signer is not the attested signer", text)

    def test_gateway_signature_fails_without_opt_in(self):
        signer = self.resign(1, kind="gateway")
        self.attest(signer, "gateway")
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("did not opt in", text)

    def test_gateway_signature_with_opt_in_is_labelled(self):
        signer = self.resign(1, kind="gateway", gateway_ok=True)
        self.attest(signer, "gateway")
        ok, text = self.verify()
        self.assertTrue(ok, text)
        self.assertIn("gateway-only", text)
        self.assertNotIn("every step's signed reply is bound", text)

    def test_plaintext_request_fails(self):
        with open(os.path.join(self.dir, "step-1.request.bin"), "rb") as stream:
            body = json.load(stream)
        body["messages"][1]["content"] = "data:image/png;base64,AAAA"
        signer = self.resign(1, json.dumps(body).encode())
        self.attest(signer)
        ok, text = self.verify()
        self.assertFalse(ok)
        self.assertIn("not end-to-end encrypted", text)

    def test_cli_exit_code_is_nonzero_on_fail(self):
        import subprocess, sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        os.remove(os.path.join(self.dir, "step-3.json"))
        run = lambda *a: subprocess.run([sys.executable, "-m", "agent", "verify", *a, self.dir], cwd=root, capture_output=True).returncode
        self.assertEqual(run(), 1)
        self.assertEqual(subprocess.run([sys.executable, "-m", "agent", "verify", FIXTURE], cwd=root, capture_output=True).returncode, 0)

    def test_fixture_reports_action_bound(self):
        ok, text = self.verify()
        self.assertNotIn("not bound", text)
        self.assertIn("every step's signed reply is bound", text)

    def test_fixture_request_is_bound_in_clear_text(self):
        with open(os.path.join(self.dir, "step-1.request.bin"), "rb") as stream:
            body = json.load(stream)
        self.assertTrue(body["user"].startswith("run:1:"))

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
