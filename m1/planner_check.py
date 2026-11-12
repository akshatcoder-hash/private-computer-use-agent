"""Milestone 1a: one planner call through NEAR AI Cloud with a verified signature.

    NEARAI_API_KEY=... uv run m1/planner_check.py

Passes only if the model's attestation verifies before the call and the
response signature is a `provider_tee` signature from that attested signer.
"""
import asyncio
import base64
import hashlib
import io
import json
import os
import sys

from nearai_inference_sdk import InferenceClient
from PIL import Image, ImageDraw

MODEL = os.environ.get("PLANNER_MODEL", "Qwen/Qwen3.8-27B")
E2EE = os.environ.get("PLANNER_E2EE", "1") == "1"


def test_image() -> bytes:
    """A synthetic screenshot, so this check sends nothing from the real screen."""
    image = Image.new("RGB", (640, 360), "#f4f4f6")
    draw = ImageDraw.Draw(image)
    draw.rectangle((220, 140, 420, 200), fill="#1f6feb")
    draw.text((270, 162), "Download PDF", fill="white")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


async def main() -> int:
    api_key = os.environ.get("NEARAI_API_KEY")
    if not api_key:
        print("NEARAI_API_KEY is not set.", file=sys.stderr)
        return 2
    png = test_image()
    async with InferenceClient(api_key, e2ee=E2EE) as client:
        evidence = await client.verify(MODEL)
        if not evidence.models:
            print(f"FAIL: {MODEL} returned no model attestation (gateway only).")
            return 1
        print(f"attestation ok: gateway TCB {evidence.gateway.tcb_status}, "
              f"{len(evidence.models)} model report(s), "
              f"GPU evidence {evidence.models[0].gpu_evidence}, "
              f"TLS binding {evidence.gateway.tls_binding.kind}")

        completion = await client.chat.completions.create(
            model=MODEL,
            max_completion_tokens=400,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "What is the label on the blue button? Reply with the label only."},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
            ]}],
        )
        verified = await client.verify_response(completion.id)

    answer = (completion.choices[0].message.content or "").strip()
    receipt = {
        "model": MODEL,
        "e2ee": E2EE,
        "completion_id": completion.id,
        "answer": answer,
        "image_sha256": hashlib.sha256(png).hexdigest(),
        "signature_kind": verified.signature_kind,
        "signer": verified.signature.signer.signing_address,
        "signing_algo": verified.signature.signer.signing_algo,
        "signed_text": verified.signature.signed_text,
        "signature": verified.signature.signature,
    }
    print(json.dumps(receipt, indent=2))
    saw_image = "download" in answer.lower()
    if verified.signature_kind != "provider_tee":
        print("FAIL: signature is from the gateway, not the model's enclave.")
        return 1
    if not saw_image:
        print("FAIL: signature verified, but the answer does not show the model read the image.")
        return 1
    print("PASS: verified provider_tee signature on a vision call.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
