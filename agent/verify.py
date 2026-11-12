"""Offline receipt check: `python -m agent verify <run_dir>`. Reads files only; opens no connection.

Fails (exit 1) unless EVERY step's signed reply is bound and consistent. For every step it checks:
  hashes   sha256 of step-N.request.bin and step-N.response.bin equal the ones inside signed_text
  model    signed_text starts with the model ID, which is the model the request names
  sig      the signature over signed_text verifies under the signer key (ed25519 or ecdsa)
  signer   the signer equals the attested model signer recorded in attestation.json
  bound    the request JSON's clear-text `user` field equals "<run_id>:<step>:<screenshot sha256>" taken from events.jsonl
  steps    steps are exactly 1..N, N = run_finished.steps = the step_started events; step files agree with events.jsonl
  action   step-N.reply_key.hex (REQUIRED unless --allow-unbound) decrypts the signed response (the SDK's own decryption), the action is parsed
           exactly as the planner parses it, and must equal the action recorded in step-N.json
Under E2EE the .bin files are ciphertext; without the reply key a receipt only proves which bytes the enclave
signed, so a step without one is FAIL ("action not bound"). The reply key cannot decrypt the request, so the
screenshot stays private. A REFUSED step (agent stopped before acting) is accepted only as the final step, with a
guard_blocked or planner_unverified event and no acted event.
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


def _norm(addr: str) -> str:
    return (addr or "").lower().removeprefix("0x")


def _events(run_dir: str) -> list[dict]:
    out = []
    for line in _read(os.path.join(run_dir, "events.jsonl")).decode("utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def check_step(run_dir: str, step: dict, attestation: dict, run_started: dict, started: dict | None,
               allow_gateway: bool, allow_unbound: bool) -> tuple[str, list[str]]:
    """Returns (status, problems) for a step that has a verification record. status is PASS or FAIL."""
    n = step["step"]
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
        # All attested model signers, like the live check (planner.py): instances share a key but the SDK may list several.
        signers = attestation.get("model_signers") or [attestation.get("model", {}).get("signer", "")]
        allowed_signers = {_norm(a) for a in signers if a}
    elif kind == "gateway":
        if not run_started.get("allow_gateway_signature"):
            problems.append("gateway signature, but this run did not opt in (run_started.allow_gateway_signature is not true)")
        hashes = parts[-2:]
        allowed_signers = {_norm(attestation.get("gateway", {}).get("signer", ""))} - {""}
    else:
        return "FAIL", [f"unknown signature kind {kind!r}"]
    try:
        body = json.loads(request)
    except ValueError:
        body = None
        problems.append("request body is not JSON")
    if isinstance(body, dict):
        if kind == "provider_tee" and body.get("model") != ":".join(parts[:-2]):
            problems.append("request body names a different model than signed_text")
        messages = body.get("messages")
        if not isinstance(messages, list) or any(not isinstance(m.get("content"), str) or "data:image" in m["content"]
                                                 for m in messages if isinstance(m, dict)):
            problems.append("request messages are not end-to-end encrypted ciphertext")
        want = f"{run_started.get('run_id')}:{n}:{(started or {}).get('screenshot', {}).get('sha256')}"
        got = body.get("user")
        if got is None:
            if not allow_unbound:
                problems.append("request is not bound to its run, step and screenshot (no `user` field)")
        elif got != want:
            problems.append(f"request binding {got!r} != {want!r} from events.jsonl")
    if len(hashes) != 2 or not all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes):
        return "FAIL", problems + ["signed_text hashes malformed"]
    if _sha256(request) != hashes[0]:
        problems.append("request.bin hash differs from signed_text")
    if _sha256(response) != hashes[1]:
        problems.append("response.bin hash differs from signed_text")
    reason = _verify_signature(v.get("signing_algo", ""), v.get("signer", ""), signed_text, v.get("signature", ""))
    if reason:
        problems.append(f"signature invalid: {reason}")
    if not allowed_signers or _norm(v.get("signer", "")) not in allowed_signers:
        problems.append("signer is not the attested signer in attestation.json")
    return ("FAIL" if problems else "PASS"), problems


def check_action(run_dir: str, step: dict, allow_unbound: bool) -> tuple[str, str]:
    """Returns (state, detail): state is ok, FAIL or not bound (accepted only with allow_unbound)."""
    n = step["step"]
    key_path = os.path.join(run_dir, f"step-{n}.reply_key.hex")
    if not os.path.exists(key_path):
        detail = "no reply key: action not bound to the signed response"
        return ("not bound", detail) if allow_unbound else ("FAIL", detail)
    try:
        from .planner import action_from_wire
        decrypted = action_from_wire(_read(os.path.join(run_dir, f"step-{n}.response.bin")), _read(key_path).decode())
    except Exception as error:
        return "FAIL", f"cannot decrypt signed response with reply key: {type(error).__name__}"
    recorded = step.get("planner", {}).get("action")
    if decrypted != recorded:
        return "FAIL", "recorded action differs from the decrypted signed response"
    return "ok", ""


def _check_consistency(step: dict, events: list[dict]) -> list[str]:
    """The step file must agree with the planner_verified event in events.jsonl."""
    n = step["step"]
    mine = [e for e in events if e.get("step") == n and e.get("type") == "planner_verified"]
    if len(mine) != 1:
        return [f"events.jsonl has {len(mine)} planner_verified events for this step, expected 1"]
    event = mine[0]
    problems = []
    if event.get("planner", {}).get("completion_id") != step.get("planner", {}).get("completion_id"):
        problems.append("completion_id differs from events.jsonl")
    if event.get("verification", {}).get("signed_text") != step.get("verification", {}).get("signed_text"):
        problems.append("signed_text differs from events.jsonl")
    if event.get("planner", {}).get("action") != step.get("planner", {}).get("action"):
        problems.append("action differs from events.jsonl")
    return problems


def verify_run(run_dir: str, out=sys.stdout, allow_unbound: bool = False) -> bool:
    def fail(message: str) -> bool:
        print(f"FAIL: {message}", file=out)
        return False

    try:
        attestation = json.loads(_read(os.path.join(run_dir, "attestation.json")))
    except (OSError, ValueError) as error:
        return fail(f"cannot read attestation.json: {error}")
    try:
        events = _events(run_dir)
    except (OSError, ValueError) as error:
        return fail(f"cannot read events.jsonl: {error}")
    starts = [e for e in events if e.get("type") == "run_started"]
    finishes = [e for e in events if e.get("type") == "run_finished"]
    if len(starts) != 1:
        return fail("events.jsonl needs exactly one run_started event")
    if len(finishes) != 1 or not isinstance(finishes[0].get("steps"), int):
        return fail("events.jsonl has no run_finished event (the run did not finish, or the log was cut)")
    run_started, total = starts[0], finishes[0]["steps"]
    if not run_started.get("e2ee"):
        return fail("run_started says end-to-end encryption was off")

    files: dict[int, dict] = {}
    extra: set[int] = set()
    for name in os.listdir(run_dir):
        m = re.fullmatch(r"step-(\d+)\.(json|request\.bin|response\.bin|reply_key\.hex)", name)
        if not m:
            continue
        n = int(m.group(1))
        if m.group(2) == "json":
            try:
                files[n] = json.loads(_read(os.path.join(run_dir, name)))
            except ValueError:
                return fail(f"{name} is not valid JSON")
        else:
            extra.add(n)
    expected = set(range(1, total + 1))
    if set(files) != expected or (extra - set(files)):
        missing = sorted(expected - set(files))
        unexpected = sorted((set(files) | extra) - expected)
        return fail(f"steps must be exactly 1..{total} (run_finished.steps): missing {missing}, unexpected {unexpected}")
    started_events = {e["step"]: e for e in events if e.get("type") == "step_started" and "step" in e}
    if total == 0:
        return fail("no steps recorded")
    for n, doc in files.items():
        if doc.get("step") != n:
            return fail(f"step-{n}.json says it is step {doc.get('step')}")
    if set(started_events) - expected:
        return fail(f"step_started events for steps outside 1..{total}: {sorted(set(started_events) - expected)}")

    print(f"run {run_started.get('run_id')}  model {attestation.get('model_id')}  "
          f"attested signer {attestation.get('model', {}).get('signer', '?')[:16]}...", file=out)
    print(f"{'step':<5}{'result':<9}{'kind':<13}{'hashes':<8}{'signature':<10}{'signer':<8}{'bound':<7}{'action':<10}recorded", file=out)
    ok, verified_steps, unbound, gateway_steps, refused = True, 0, 0, 0, 0
    for n in range(1, total + 1):
        step = files[n]
        step_events = [e for e in events if e.get("step") == n]
        types = {e.get("type") for e in step_events}
        problems: list[str] = []
        if "verification" not in step:
            # The agent stopped before acting. Only the final step may be a refusal, and only with its own event.
            if n != total:
                problems.append("a step without a verified reply that is not the final step")
            if not types & {"guard_blocked", "planner_unverified"}:
                problems.append("no guard_blocked or planner_unverified event explains why this step has no verified reply")
            if "acted" in types:
                problems.append("an action was performed (acted event) on a step with no verified reply")
            if "planner_verified" in types:
                problems.append("planner_verified event exists but the step file has no verification")
            if problems:
                print(f"{n:<5}{'FAIL':<9}{'-':<13}{'-':<8}{'-':<10}{'-':<8}{'-':<7}{'-':<10}", file=out)
                for problem in problems:
                    print(f"        - {problem}", file=out)
                ok = False
            else:
                refused += 1
                print(f"{n:<5}{'REFUSED':<9}{'-':<13}{'-':<8}{'-':<10}{'-':<8}{'-':<7}{'-':<10}agent stopped before acting "
                      f"({sorted(types & {'guard_blocked', 'planner_unverified'})[0]})", file=out)
            continue
        if types & {"planner_unverified"}:
            problems.append("planner_unverified event on a step that has a verified reply")
        started = started_events.get(n)
        if started is None:
            problems.append("no step_started event for this step")
        status, more = check_step(run_dir, step, attestation, run_started, started,
                                  bool(run_started.get("allow_gateway_signature")), allow_unbound)
        problems += more
        problems += _check_consistency(step, events)
        action_state, detail = check_action(run_dir, step, allow_unbound)
        if action_state == "FAIL":
            problems.append(f"action: {detail}")
        elif action_state == "not bound":
            unbound += 1
        if "acted" in types and step.get("planner", {}).get("action", {}).get("action") in ("done", "fail"):
            problems.append("an acted event exists for a terminal action")
        status = "FAIL" if problems else "PASS"
        v = step.get("verification", {})
        bad = lambda word: "FAIL" if any(word in p for p in problems) else "ok"
        action = step.get("planner", {}).get("action", {})
        label = action.get("action", "-") + (f" {action.get('target', '')}".rstrip() if action.get("action") == "click" else "")
        kind = v.get("signature_kind", "?")
        if kind == "gateway":
            kind = "gateway-only"
            gateway_steps += status == "PASS"
        print(f"{n:<5}{status:<9}{kind:<13}{bad('hash'):<8}{bad('signature'):<10}{bad('signer'):<8}{bad('bound'):<7}"
              f"{action_state:<10}{label}", file=out)
        for problem in problems:
            print(f"        - {problem}", file=out)
        ok &= status == "PASS"
        verified_steps += status == "PASS"
    if verified_steps == 0:
        ok = False
    notes = []
    if unbound:
        notes.append(f"--allow-unbound: {unbound} step(s) have no reply key, so the recorded action is NOT proven to be what the enclave said")
    if gateway_steps:
        notes.append(f"{gateway_steps} step(s) carry a gateway-only signature (not the model enclave's)")
    if refused:
        notes.append(f"{refused} final step stopped before acting")
    for note in notes:
        print(f"note: {note}", file=out)
    if ok and not unbound and not gateway_steps:
        meaning = "every step's signed reply is bound to its run, step and screenshot and is consistent with events.jsonl"
    elif ok:
        meaning = "UNBOUND/GATEWAY-ONLY steps accepted by explicit opt-in; weaker than a default PASS"
    else:
        meaning = "at least one step is not verified"
    print(f"{'PASS' if ok else 'FAIL'}: {verified_steps} of {total} step(s) verified; {meaning}", file=out)
    return ok
