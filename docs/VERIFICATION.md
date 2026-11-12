# How a step is verified

This page follows one planner reply from the NEAR AI Cloud gateway to a line in `python -m agent verify`. Line numbers refer to the current `agent/planner.py` and `agent/verify.py`.

NEAR's own description of the scheme: [response signatures](https://docs.near.ai/cloud/verification/cloud-api/response-signatures.md), [model attestations](https://docs.near.ai/cloud/verification/cloud-api/model-attestations.md), [E2EE chat completions](https://docs.near.ai/cloud/guides/e2ee-chat-completions.md).

## 1. Before the run: attestation

`Planner.start()` (`planner.py:234-242`) calls the SDK's `InferenceClient.verify(model)`. The SDK checks the gateway and model attestation, including the Intel TDX quote, the NVIDIA GPU evidence, and that the TLS connection is bound to the attested gateway. The agent does not re-implement these checks.

The agent adds two rules:

- If the SDK returns no model report, the run stops (`planner.py:237-238`).
- It records the result in `attestation.json` (`summarize_attestation`, `planner.py:170-196`): TCB status, signer address and algorithm, GPU evidence status, compose and OS image hashes, TLS binding kind, and the list of attested model signers. The set of signers is kept in memory for step 3 (`planner.py:240`).

The attestation must come before the first call: `plan()` raises if `start()` has not run (`planner.py:263-264`).

## 2. The call

`Planner.plan()` (`planner.py:262-318`) sends one non-streaming `chat.completions.create` with the canonical model ID (`planner.py:268-275`). A streamed or aliased request would get a gateway signature instead, which this agent refuses by default.

With E2EE on, the SDK encrypts `messages` to the attested model key. Each request gets a fresh client "reply" key pair, and the model encrypts its answer to it. In the recorded request (`tests/fixtures/run/step-1.request.bin`), `messages[].content` is hex ciphertext. `model`, `max_completion_tokens`, `temperature`, `response_format` and `chat_template_kwargs` are plaintext.

## 3. Before acting: the signature check

Right after the call the agent reads the exact request and response bytes from the SDK's record, before `verify_response` clears them (`planner.py:280-287`). This uses a private SDK method, `_get_response_record`. If the SDK changes, the step fails closed (`PlannerUnverified`), it does not skip the check.

Then `verify_response(completion.id)` (`planner.py:289-292`) makes the SDK fetch and verify the signature. The agent then runs its own checks in `_check` (`planner.py:320-346`):

| Check | Line | Fails if |
|---|---|---|
| Signature kind is `provider_tee` | 322-324 | The signature is from the gateway, unless `--allow-gateway-signature` was given. |
| `signed_text` equals the expected string built from the hashes of the captured bytes | 326-329 | Either hash differs. |
| Signer is one of the attested model signers | 330-333 | The signer was not attested before the run. |

Any failure raises `PlannerUnverified`. The loop then writes a `planner_unverified` event and stops without acting (`loop.py:184-187`).

### Signed text

For `provider_tee` (`planner.py:327`):

```
<model id>:<sha256 hex of the exact request body bytes>:<sha256 hex of the exact response body bytes>
```

Example (from `tests/fixtures/run/step-1.json`): `Qwen/Qwen3.8-27B:<64 hex>:<64 hex>`. The model ID is the one named in the request, and `verify` checks that the request body names the same model (`verify.py:76-77`).

For `gateway` signatures, which are only accepted with the opt-in flag, the text has no model ID: `<request sha256>:<response sha256>`. The hashes are of the bytes as sent and received, not of re-serialized JSON.

## 4. After acting: the saved files

For each step the loop writes (`loop.py:190`, `receipts.py:48-56`):

- `step-N.request.bin`, `step-N.response.bin`: the exact wire bytes.
- `step-N.reply_key.hex`: the reply key for that call, as `<algo>:<hex secret>`.
- `step-N.json`: the verification record (signature, signer, signed text, both hashes) and the planner's action.

Where does the reply key come from? The SDK makes it internally and does not return it. `install_reply_key_capture()` (`planner.py:89-104`) wraps the SDK's key-pair function and records each pair. `plan()` requires exactly one key per call (`planner.py:295-298`). This is the one place the agent patches SDK internals. If the SDK is restructured, install fails with an error (`planner.py:94-96`).

Why saving the key does not expose the screenshot: the key can decrypt only the response. The request was encrypted to the model's attested key with a throwaway secret that the client never keeps (`planner.py:77-83`). `tests/test_verify.py` (`test_reply_key_cannot_decrypt_the_request`) checks that the saved key does not open the request.

## 5. `python -m agent verify <run_dir>`

`verify_run` (`verify.py:117-164`) reads files only and opens no connection. For every step, `check_step` (`verify.py:52-97`) and `check_action` (`verify.py:100-114`) do:

| Column | What it checks | Code |
|---|---|---|
| `hashes` | SHA-256 of `request.bin` and `response.bin` equals the two hashes at the end of `signed_text`. | `verify.py:86-91` |
| (model) | `signed_text` starts with the attested model ID, and the request body's `model` field is the same. | `verify.py:69-79` |
| `signature` | The signature over `signed_text` is valid: ed25519 with PyNaCl, or ecdsa by recovering the Ethereum (EIP-191) address. | `verify.py:32-49, 92-94` |
| `signer` | The signer equals `model.signer` in `attestation.json`. | `verify.py:80, 95-96` |
| `action` | `reply_key.hex` decrypts the signed `response.bin` with the SDK's own decryption, the action is parsed the way the planner parses it (`planner.py:128-133, 144-163`), and it equals `planner.action` in `step-N.json`. | `verify.py:100-114` |

Results: `PASS`, `FAIL`, or `REFUSED` (the agent stopped without acting, for example after an unverified reply). The run passes only if every non-refused step passes and at least one step verified (`verify.py:156-159`). A missing reply key gives `action: not bound`, which is called out in a note (`verify.py:160-162`).

Run on the recorded fixture:

```
$ uv run python -m agent verify tests/fixtures/run
run run  model Qwen/Qwen3.8-27B  attested signer 89154a43a4768474...
step result   kind         hashes  signature signer  action     recorded
1    PASS     provider_tee ok      ok        ok      ok         click the Settings button
2    PASS     provider_tee ok      ok        ok      ok         click the Save changes button
3    PASS     provider_tee ok      ok        ok      ok         done
PASS: 3 of 3 step(s) verified
```

`tests/test_verify.py` covers these failures: a flipped byte in the request or response, a changed signature or signed text, a wrong signer, a wrong model prefix, an edited recorded action, a swapped or garbage reply key, a missing wire file, and a response re-signed with an attacker's key.

## What this does not establish

- **Which instance answered.** NEAR documents that instances of a model share one signing key. A matching signer does not show that the attested report and the completion came from the same serving instance.
- **That the TDX quote is valid, offline.** `verify` compares the receipt's signer with the one recorded in `attestation.json`. It does not re-verify the quote or GPU evidence, and `attestation.json` is a summary written by the same run. A forger who can rewrite the whole folder, including `attestation.json`, can make a consistent fake. The run-time check by the SDK is the one that binds the signer to hardware evidence.
- **That the answer is correct.** The signature says what the enclave produced, not that it is a good action.
- **The action, without the reply key.** Under E2EE the signed bytes are ciphertext. The recorded action is tied to them only through the saved reply key.
- **What Atom did.** Atom runs locally and is not signed. The receipt records its instruction and the pixel it returned (`grounded` event), as reported by the agent.
