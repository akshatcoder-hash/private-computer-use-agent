"""Offline receipt check: `python -m agent verify <run_dir>`. Reads files only; opens no connection.

For every step with a verified planner answer it checks:
  hashes   sha256 of step-N.request.bin and step-N.response.bin equal the ones inside signed_text
  model    signed_text starts with the model ID, which is the model the request names
  sig      the signature over signed_text verifies under the signer key (ed25519 or ecdsa)
  signer   the signer equals the attested model signer recorded in attestation.json
  action   step-N.reply_key.hex decrypts the signed response (the SDK's own decryption), the action is parsed
           exactly as the planner parses it, and must equal the action recorded in step-N.json
Under E2EE the .bin files are ciphertext; without the reply key a receipt only proves which bytes the enclave
signed ("action: not bound"). The reply key cannot decrypt the request, so the screenshot stays private.
"""
import hashlib
import json
import os
import re
import sys

import nacl.exceptions
import nacl.signing


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: str) -> bytes:
    with open(path, "rb") as stream:
        return stream.read()


def _verify_signature(algo: str, signer: str, signed_text: str, signature: str) -> str | None:
    """Returns None if valid, else a reason."""
    try:
        sig = bytes.fromhex(signature.removeprefix("0x"))
        key = bytes.fromhex(signer.removeprefix("0x"))
        if algo == "ed25519":
            nacl.signing.VerifyKey(key).verify(signed_text.encode(), sig)
            return None
        if algo == "ecdsa":
            from eth_account import Account
            from eth_account.messages import encode_defunct
            recovered = Account.recover_message(encode_defunct(text=signed_text), signature="0x" + sig.hex())
            return None if bytes.fromhex(recovered.removeprefix("0x")) == key else "recovered address differs from signer"
        return f"unsupported algo {algo}"
    except (nacl.exceptions.BadSignatureError, ValueError) as error:
        return f"bad signature ({type(error).__name__})"
    except Exception as error:  # malformed input of any kind is a failure, never a crash
        return f"{type(error).__name__}: {error}"


def check_step(run_dir: str, step: dict, attestation: dict) -> tuple[str, list[str]]:
    """Returns (status, problems). status is PASS, FAIL or REFUSED."""
    n = step["step"]
    if "verification" not in step:
        if "error" in step and "action_performed" not in step:
            return "REFUSED", []
        return "FAIL", ["no verification record, yet the step is not a recorded refusal"]
    v, problems = step["verification"], []
    try:
        request = _read(os.path.join(run_dir, f"step-{n}.request.bin"))
        response = _read(os.path.join(run_dir, f"step-{n}.response.bin"))
    except OSError as error:
        return "FAIL", [f"missing wire file: {error.filename}"]

    kind, signed_text = v.get("signature_kind"), v.get("signed_text", "")
    parts = signed_text.split(":")
    model_id = attestation.get("model_id") or step.get("planner", {}).get("model")
    if kind == "provider_tee":
        if len(parts) < 3:
            return "FAIL", ["signed_text malformed"]
        prefix, hashes = ":".join(parts[:-2]), parts[-2:]
        if prefix != model_id:
            problems.append(f"model prefix {prefix!r} != attested model {model_id!r}")
        try:
            if json.loads(request).get("model") != prefix:
                problems.append("request body names a different model than signed_text")
        except ValueError:
            problems.append("request body is not JSON")
        expected_signer = attestation.get("model", {}).get("signer", "")
    elif kind == "gateway":
        hashes = parts[-2:]
        expected_signer = attestation.get("gateway", {}).get("signer", "")
    else:
        return "FAIL", [f"unknown signature kind {kind!r}"]
    if len(hashes) != 2 or not all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes):
        return "FAIL", problems + ["signed_text hashes malformed"]
    if _sha256(request) != hashes[0]:
        problems.append("request.bin hash differs from signed_text")
    if _sha256(response) != hashes[1]:
        problems.append("response.bin hash differs from signed_text")
    reason = _verify_signature(v.get("signing_algo", ""), v.get("signer", ""), signed_text, v.get("signature", ""))
    if reason:
        problems.append(f"signature invalid: {reason}")
    if not expected_signer or v.get("signer", "").lower().removeprefix("0x") != expected_signer.lower().removeprefix("0x"):
        problems.append("signer is not the attested signer in attestation.json")
    return ("FAIL" if problems else "PASS"), problems


def check_action(run_dir: str, step: dict) -> tuple[str, str]:
    """Returns (state, detail): state is ok, FAIL or not bound."""
    n = step["step"]
    key_path = os.path.join(run_dir, f"step-{n}.reply_key.hex")
    if not os.path.exists(key_path):
        return "not bound", "no reply key in this receipt: the recorded action is not tied to the signed response"
    try:
        from .planner import action_from_wire
        decrypted = action_from_wire(_read(os.path.join(run_dir, f"step-{n}.response.bin")), _read(key_path).decode())
    except Exception as error:
        return "FAIL", f"cannot decrypt signed response with reply key: {type(error).__name__}"
    recorded = step.get("planner", {}).get("action")
    if decrypted != recorded:
        return "FAIL", "recorded action differs from the decrypted signed response"
    return "ok", ""


def verify_run(run_dir: str, out=sys.stdout) -> bool:
    try:
        attestation = json.loads(_read(os.path.join(run_dir, "attestation.json")))
    except (OSError, ValueError) as error:
        print(f"FAIL: cannot read attestation.json: {error}", file=out)
        return False
    steps = []
    for name in sorted(os.listdir(run_dir), key=lambda s: (len(s), s)):
        if re.fullmatch(r"step-\d+\.json", name):
            steps.append(json.loads(_read(os.path.join(run_dir, name))))
    steps.sort(key=lambda s: s["step"])
    if not steps:
        print("FAIL: no steps recorded", file=out)
        return False
    print(f"run {os.path.basename(os.path.abspath(run_dir))}  model {attestation.get('model_id')}  "
          f"attested signer {attestation.get('model', {}).get('signer', '?')[:16]}...", file=out)
    print(f"{'step':<5}{'result':<9}{'kind':<13}{'hashes':<8}{'signature':<10}{'signer':<8}{'action':<11}recorded", file=out)
    ok, verified_steps, unbound = True, 0, 0
    for step in steps:
        status, problems = check_step(run_dir, step, attestation)
        action_state = "-"
        if status != "REFUSED":
            action_state, detail = check_action(run_dir, step)
            if action_state == "FAIL":
                problems.append(f"action: {detail}")
                status = "FAIL"
            elif action_state == "not bound":
                unbound += 1
        v = step.get("verification", {})
        bad = lambda word: "FAIL" if any(word in p for p in problems) else "ok"
        action = step.get("planner", {}).get("action", {})
        label = action.get("action", "-") + (f" {action.get('target', '')}".rstrip() if action.get("action") == "click" else "")
        if status == "REFUSED":
            print(f"{step['step']:<5}{'REFUSED':<9}{'-':<13}{'-':<8}{'-':<10}{'-':<8}{'-':<11}agent stopped without acting: {step.get('error', '')[:60]}", file=out)
            continue
        print(f"{step['step']:<5}{status:<9}{v.get('signature_kind', '?'):<13}{bad('hash'):<8}{bad('signature'):<10}"
              f"{bad('signer'):<8}{action_state:<11}{label}", file=out)
        for problem in problems:
            print(f"        - {problem}", file=out)
        ok &= status == "PASS"
        verified_steps += status == "PASS"
    if verified_steps == 0:
        ok = False
    if unbound:
        print(f"note: {unbound} step(s) show 'action: not bound' (old receipt, no reply key): the signature covers the "
              f"encrypted response, but the recorded action is NOT proven to be what the enclave said", file=out)
    print(f"{'PASS' if ok else 'FAIL'}: {verified_steps} of {len(steps)} step(s) verified", file=out)
    return ok
