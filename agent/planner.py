"""The planner: Qwen on NEAR AI Cloud, attested before the call and verified before its answer is used.

The only network client in this package besides Atom's loopback call. A
screenshot leaves the Mac only through `Planner.plan`.
"""
import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass, field

from nearai_inference_sdk import InferenceClient
from nearai_inference_sdk.core import e2ee as _sdk_e2ee
from nearai_inference_sdk.core import e2ee_request as _sdk_e2ee_request

from .egress import PLANNER_BASE_URL, check_planner_url
from .screen_keys import ALLOWED_KEYS

MODEL = "Qwen/Qwen3.8-27B"
MAX_COMPLETION_TOKENS = 600
GLM_MAX_COMPLETION_TOKENS = 2500  # headroom for its (low-effort) reasoning tokens

SYSTEM_PROMPT = """You operate a computer for the user by looking at screenshots.

The screenshot is the work window (a browser or app window), nothing else. You choose ONE action per step. Reply with a single JSON object and nothing else.

Actions:
{"thought": "...", "action": "click",  "target": "the Statements link"}
{"thought": "...", "action": "type",   "text": "...", "enter": true}
{"thought": "...", "action": "key",    "key": "enter"}
{"thought": "...", "action": "scroll", "direction": "down"}
{"thought": "...", "action": "wait"}
{"thought": "...", "action": "done",   "summary": "..."}
{"thought": "...", "action": "fail",   "reason": "..."}

Rules:
- "key" presses ONE key. Allowed keys only: enter, return, tab, escape, backspace, delete, up, down, left, right, pageup, pagedown, space, and the single chord cmd+a (select all). Any other key or shortcut is refused and nothing happens.
- Describe every click target by its visible text, for example "the Download PDF button" or "the Settings link". Never give coordinates. Name exactly one thing, using the label as it is written on screen.
- Name a text field by its visible label, for example "the To field" or "the Search Wikipedia box". Name a link or button by the exact words printed on it ("the View October 2026 button", not "the statement link").
- "type" types into the field that already has focus, so click the field first, in an earlier step. Once the screenshot shows your text in the field, do not type it again: go on to the next field or button.
- To submit a typed search or form, either set "enter": true in the same type action or click the submit button; then check the next screenshot.
- Choose "done" only when the goal is visibly complete on the current screenshot, and say what you saw in "summary".
- If the history says the screen did not change after your last action, that action probably did nothing: try something different.
- Keep "thought" to one short sentence."""

ACTION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "action",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "thought": {"type": "string"},
                "action": {"type": "string", "enum": ["click", "type", "key", "scroll", "wait", "done", "fail"]},
                "target": {"type": "string"},
                "text": {"type": "string"},
                "enter": {"type": "boolean"},
                "key": {"type": "string", "enum": list(ALLOWED_KEYS)},
                "direction": {"type": "string", "enum": ["up", "down"]},
                "summary": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["thought", "action"],
        },
    },
}

REQUIRED_FIELD = {"click": "target", "type": "text", "key": "key", "scroll": "direction", "done": "summary", "fail": "reason"}


# ---------------------------------------------------------------------------------------------
# Reply key capture.
#
# Under E2EE the SDK makes a fresh "reply" keypair for every request (e2ee_request.py
# prepare_e2ee_chat_request calls create_e2ee_client_key_pair) and closes over the private half;
# no public API returns it. This is the ONE place we touch SDK internals: we wrap the module-level
# name `create_e2ee_client_key_pair` as imported into e2ee_request, and record every key it makes.
# If the SDK is restructured, install fails loudly here instead of silently dropping the key.
#
# Why storing it in a receipt is safe: the reply key can only decrypt the RESPONSE fields (planner
# reply). The request, including the screenshot (messages[].content), is encrypted by
# encrypt_e2ee_text to the model's attested public key using a throwaway ephemeral secret that is
# never kept (e2ee.py encrypt_e2ee_text); the client never holds the model's private key. The
# client's public reply key only travels in a header and in the AAD. So the receipt still cannot
# reveal the screenshot or the prompt, but it lets anyone decrypt the signed reply and check that
# the recorded action is what the enclave produced.
# ---------------------------------------------------------------------------------------------
_CAPTURED_KEYS: list = []
_PATCHED = False


def install_reply_key_capture() -> None:
    global _PATCHED
    if _PATCHED:
        return
    original = getattr(_sdk_e2ee_request, "create_e2ee_client_key_pair", None)
    if original is None or not hasattr(_sdk_e2ee, "decrypt_e2ee_text"):
        raise RuntimeError("nearai_inference_sdk changed: cannot capture the E2EE reply key "
                           "(core.e2ee_request.create_e2ee_client_key_pair is gone); update agent/planner.py")

    def capturing(*args, **kwargs):
        pair = original(*args, **kwargs)
        _CAPTURED_KEYS.append(pair)
        return pair

    _sdk_e2ee_request.create_e2ee_client_key_pair = capturing
    _PATCHED = True


def serialize_reply_key(pair) -> str:
    """'<algo>:<hex secret>' for a Ed25519/Ecdsa E2eeClientKeyPair."""
    if pair.signing_algo == "ed25519":
        return "ed25519:" + bytes(pair.x25519_secret_key).hex()
    if pair.signing_algo == "ecdsa":
        return "ecdsa:" + pair.private_key.private_numbers().private_value.to_bytes(32, "big").hex()
    raise RuntimeError(f"unknown reply key algo {pair.signing_algo!r}")


def load_reply_key(text: str):
    algo, _, secret = text.strip().partition(":")
    raw = bytes.fromhex(secret)
    if algo == "ed25519":
        return _sdk_e2ee.Ed25519E2eeClientKeyPair(public_key="", x25519_secret_key=raw)
    if algo == "ecdsa":
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256K1())
        return _sdk_e2ee.EcdsaE2eeClientKeyPair(public_key="", private_key=key)
    raise ValueError(f"unknown reply key algo {algo!r}")


def action_from_wire(response_bytes: bytes, reply_key_text: str) -> dict:
    """Decrypt a recorded E2EE response with the SDK's own code and parse the action as the planner does."""
    from nearai_inference_sdk.core.e2ee_chat import decrypt_e2ee_chat_response, parse_e2ee_chat_response
    body = parse_e2ee_chat_response(json.loads(response_bytes.decode("utf-8")))
    decrypted = decrypt_e2ee_chat_response(body, load_reply_key(reply_key_text))
    return parse_action(decrypted["choices"][0]["message"].get("content") or "")


class PlannerUnverified(RuntimeError):
    """The planner's answer could not be verified, so it must not be acted on."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_action(text: str) -> dict:
    """Defensive parse of the model's reply into a planner action dict."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("action") in {"click", "type", "key", "scroll", "wait", "done", "fail"}:
            action = value["action"]
            need = REQUIRED_FIELD.get(action)
            if need and not value.get(need):
                raise ValueError(f"action {action!r} is missing {need!r}")
            return value
    raise ValueError(f"no valid action in the planner reply: {text[:200]!r}")


def request_binding(run_id: str, step: int, screenshot_sha256: str) -> str:
    """The clear-text `user` field of the chat request. The enclave signs the request bytes, so this ties the
    signature to one run, one step and one screenshot."""
    return f"{run_id}:{step}:{screenshot_sha256}"


def _identity(signer) -> dict:
    return {"signing_algo": signer.signing_algo, "signer": signer.signing_address}


def summarize_attestation(evidence, model: str, e2ee: bool) -> dict:
    gateway = evidence.gateway
    models = list(evidence.models)
    primary = models[0]
    return {
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(evidence.verified_at / 1000)),
        "model_id": model,
        "e2ee": e2ee,
        "gateway": {
            "tcb_status": str(gateway.tcb_status),
            "signing_algo": gateway.signer.signing_algo,
            "signer": gateway.signer.signing_address,
            "tls_binding": gateway.tls_binding.kind,
            "tls_spki_fingerprint": gateway.tls_binding.spki_fingerprint,
            "compose_hash": gateway.deployment.runtime_measurements.compose_hash,
            "os_image_hash": gateway.deployment.runtime_measurements.os_image_hash,
        },
        "model": {
            "tcb_status": str(primary.tcb_status),
            "signing_algo": primary.signer.signing_algo,
            "signer": primary.signer.signing_address,
            "gpu_evidence": str(primary.gpu_evidence),
            "compose_hash": primary.deployment.runtime_measurements.compose_hash,
            "os_image_hash": primary.deployment.runtime_measurements.os_image_hash,
        },
        "model_signers": [m.signer.signing_address for m in models],
    }


@dataclass
class PlanResult:
    action: dict
    thought: str
    completion_id: str
    latency_ms: int
    request_bytes: bytes
    response_bytes: bytes
    verification: dict
    usage: dict = field(default_factory=dict)
    reply_key: str | None = None  # decrypts only this reply; see the note above


class Planner:
    def __init__(self, api_key: str, model: str = MODEL, e2ee: bool = True, allow_gateway_signature: bool = False,
                 base_url: str = PLANNER_BASE_URL):
        if e2ee is not True:
            # Without E2EE the plaintext screenshot would go to the gateway and be stored in step-N.request.bin.
            raise ValueError("the planner only runs with end-to-end encryption (e2ee=True); there is no plaintext path")
        self.base_url = check_planner_url(base_url)
        self.model = model
        self.e2ee = True
        self.allow_gateway = allow_gateway_signature
        self._api_key = api_key
        self._client: InferenceClient | None = None
        self.attestation: dict | None = None
        self._model_signers: set[str] = set()
        self._gateway_signer: str = ""

    async def __aenter__(self):
        install_reply_key_capture()
        # The only URL the SDK may use is the one that passed the egress check.
        self._client = InferenceClient(self._api_key, base_url=self.base_url, e2ee=self.e2ee)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self._client.__aexit__(*exc)

    async def start(self) -> dict:
        """Verify gateway and model attestation. Raises PlannerUnverified if the model has no enclave report."""
        evidence = await self._client.verify(self.model)
        if not evidence.models:
            raise PlannerUnverified(f"{self.model} returned no model attestation (it does not run in an attested enclave)")
        self.attestation = summarize_attestation(evidence, self.model, self.e2ee)
        self._model_signers = {s.lower() for s in self.attestation["model_signers"]}
        self._gateway_signer = self.attestation["gateway"]["signer"].lower()
        return self.attestation

    def _model_options(self) -> dict:
        """Models that reason by default get their reasoning turned down; Qwen has it switched off."""
        if self.model.startswith("z-ai/"):
            return {"reasoning_effort": "low"}
        return {"chat_template_kwargs": {"enable_thinking": False}}

    def _messages(self, task: str, history: list[str], screen_note: str | None, png: bytes) -> list[dict]:
        lines = [f"Goal: {task}", ""]
        if history:
            lines.append("Your previous steps:")
            lines.extend(history)
        else:
            lines.append("This is the first step.")
        if screen_note:
            lines += ["", screen_note]
        lines += ["", "Current screenshot attached. Reply with the JSON for your next action."]
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "\n".join(lines)},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
            ]},
        ]

    async def plan(self, png: bytes, task: str, history: list[str], screen_note: str | None = None, *,
                   run_id: str, step: int) -> PlanResult:
        if self.attestation is None:
            raise PlannerUnverified("planner attestation was not verified before the call")
        bind = request_binding(run_id, step, sha256_hex(png))
        started = time.monotonic()
        _CAPTURED_KEYS.clear()
        try:
            completion = await self._client.chat.completions.create(
                model=self.model,
                messages=self._messages(task, history, screen_note, png),
                max_completion_tokens=MAX_COMPLETION_TOKENS if not self.model.startswith("z-ai/") else GLM_MAX_COMPLETION_TOKENS,
                temperature=0,
                response_format=ACTION_SCHEMA,
                user=bind,  # clear text under E2EE, so the enclave signs it with the request bytes
                extra_body=self._model_options(),
            )
        except Exception as error:
            raise PlannerUnverified(f"planner call failed: {type(error).__name__}: {error}") from error
        latency_ms = int((time.monotonic() - started) * 1000)

        # The exact wire bytes must be read BEFORE verify_response, which clears them.
        try:
            record = self._client._get_response_record(completion.id)
            request_bytes, response_bytes = bytes(record.request_body), bytes(await record.response_body)
        except Exception as error:
            raise PlannerUnverified(f"could not capture the wire bytes needed to verify: {error}") from error
        if not request_bytes or not response_bytes:
            raise PlannerUnverified("wire bytes were empty")

        try:
            verified = await self._client.verify_response(completion.id)
        except Exception as error:
            raise PlannerUnverified(f"signature verification failed: {type(error).__name__}: {error}") from error
        verification = self._check(verified, request_bytes, response_bytes)
        try:
            sent_user = json.loads(request_bytes.decode("utf-8")).get("user")
        except ValueError:
            sent_user = None
        if sent_user != bind:
            raise PlannerUnverified("the signed request does not carry this run, step and screenshot binding")
        if len(_CAPTURED_KEYS) != 1:
            raise PlannerUnverified(f"expected exactly one E2EE reply key for this call, saw {len(_CAPTURED_KEYS)}")
        reply_key = serialize_reply_key(_CAPTURED_KEYS[0])

        # Act on the signed bytes, not on the SDK's parsed object: decrypt the exact response that was signed
        # (the path `verify` uses) and require it to agree with what the SDK handed back.
        try:
            action = action_from_wire(response_bytes, reply_key)
        except Exception as error:
            raise PlannerUnverified(f"the signed reply could not be decrypted and parsed: {type(error).__name__}: {error}") from error
        try:
            parsed = parse_action(completion.choices[0].message.content or "")
        except ValueError:
            parsed = None
        if parsed != action:
            raise PlannerUnverified("the action parsed by the SDK differs from the action in the signed reply bytes")
        usage = completion.usage
        return PlanResult(
            action=action,
            thought=str(action.get("thought", "")),
            completion_id=completion.id,
            latency_ms=latency_ms,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            verification=verification,
            reply_key=reply_key,
            usage={"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
                   "reasoning_tokens": getattr(usage, "reasoning_tokens", None)} if usage else {},
        )

    def _check(self, verified, request_bytes: bytes, response_bytes: bytes) -> dict:
        """Our own checks on top of the SDK's: kind, hashes against signed_text, signer against attestation."""
        kind = verified.signature_kind
        if kind != "provider_tee" and not (kind == "gateway" and self.allow_gateway):
            raise PlannerUnverified(f"signature kind is {kind!r}, not 'provider_tee' (enclave of the attested model)")
        signature = verified.signature
        request_sha, response_sha = sha256_hex(request_bytes), sha256_hex(response_bytes)
        expected = f"{request_sha}:{response_sha}" if kind == "gateway" else f"{self.model}:{request_sha}:{response_sha}"
        if signature.signed_text != expected:
            raise PlannerUnverified("signed_text does not match the hashes of the captured wire bytes")
        signer = signature.signer.signing_address.lower()
        matches = signer == self._gateway_signer if kind == "gateway" else signer in self._model_signers
        if not matches:
            raise PlannerUnverified(f"{kind} signer {signer} does not match the attested signer")
        return {
            "verified": True,
            "checked_before_acting": True,
            "signature_kind": kind,
            "signing_algo": signature.signer.signing_algo,
            "signer": signature.signer.signing_address,
            "signer_matches_attested_model": matches if kind == "provider_tee" else False,
            "signer_matches_attested_gateway": matches if kind == "gateway" else False,
            "signed_text": signature.signed_text,
            "signature": signature.signature,
            "request_sha256": request_sha,
            "response_sha256": response_sha,
        }
