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

from .egress import PLANNER_BASE_URL, check_planner_url

MODEL = "Qwen/Qwen3.8-27B"
MAX_COMPLETION_TOKENS = 600

SYSTEM_PROMPT = """You operate a computer for the user by looking at screenshots.

The screenshot is the work area: the left 1400x1080 region of the screen. You choose ONE action per step. Reply with a single JSON object and nothing else.

Actions:
{"thought": "...", "action": "click",  "target": "the Statements link"}
{"thought": "...", "action": "type",   "text": "...", "enter": true}
{"thought": "...", "action": "key",    "key": "enter"}
{"thought": "...", "action": "scroll", "direction": "down"}
{"thought": "...", "action": "wait"}
{"thought": "...", "action": "done",   "summary": "..."}
{"thought": "...", "action": "fail",   "reason": "..."}

Rules:
- Describe every click target by its visible text, for example "the Download PDF button" or "the Settings link". Never give coordinates. Name exactly one thing, using the label as it is written on screen.
- "type" types into the field that already has focus, so click the field first, in an earlier step.
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
                "key": {"type": "string"},
                "direction": {"type": "string", "enum": ["up", "down"]},
                "summary": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["thought", "action"],
        },
    },
}

REQUIRED_FIELD = {"click": "target", "type": "text", "key": "key", "scroll": "direction", "done": "summary", "fail": "reason"}


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


class Planner:
    def __init__(self, api_key: str, model: str = MODEL, e2ee: bool = True, allow_gateway_signature: bool = False,
                 base_url: str = PLANNER_BASE_URL):
        self.base_url = check_planner_url(base_url)
        self.model = model
        self.e2ee = e2ee
        self.allow_gateway = allow_gateway_signature
        self._api_key = api_key
        self._client: InferenceClient | None = None
        self.attestation: dict | None = None
        self._model_signers: set[str] = set()
        self._gateway_signer: str = ""

    async def __aenter__(self):
        self._client = InferenceClient(self._api_key, e2ee=self.e2ee)
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

    async def plan(self, png: bytes, task: str, history: list[str], screen_note: str | None = None) -> PlanResult:
        if self.attestation is None:
            raise PlannerUnverified("planner attestation was not verified before the call")
        started = time.monotonic()
        try:
            completion = await self._client.chat.completions.create(
                model=self.model,
                messages=self._messages(task, history, screen_note, png),
                max_completion_tokens=MAX_COMPLETION_TOKENS,
                temperature=0,
                response_format=ACTION_SCHEMA,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
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

        message = completion.choices[0].message
        try:
            action = parse_action(message.content or "")
        except ValueError as error:
            # Verified, but unusable. Nothing is executed; the loop records it and stops.
            raise PlannerUnverified(f"verified reply was not a valid action: {error}") from error
        usage = completion.usage
        return PlanResult(
            action=action,
            thought=str(action.get("thought", "")),
            completion_id=completion.id,
            latency_ms=latency_ms,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            verification=verification,
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
