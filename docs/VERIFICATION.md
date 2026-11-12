# How a step is verified

This page follows one planner reply from the NEAR AI Cloud gateway to a line in `python -m agent verify`. References use function names in `agent/planner.py`, `agent/verify.py` and `agent/loop.py`, so they do not go stale when lines move.

NEAR's own description of the scheme: [response signatures](https://docs.near.ai/cloud/verification/cloud-api/response-signatures.md), [model attestations](https://docs.near.ai/cloud/verification/cloud-api/model-attestations.md), [E2EE chat completions](https://docs.near.ai/cloud/guides/e2ee-chat-completions.md).

## What `verify` proves, in one paragraph

It proves that the planner's reply, which is the decision the agent acted on, is the exact reply the model's enclave signed, and that it was signed for one specific run, one step and one screenshot hash. It does **not** prove what the agent's clicks and keystrokes did on screen. The `acted` events and `action_performed` in `step-N.json` are the agent's own record of that, and nothing signs them.

## 1. Before the run: attestation

`Planner.start()` calls the SDK's `InferenceClient.verify(model)`. The SDK checks the gateway and model attestation, including the Intel TDX quote, the NVIDIA GPU evidence, and that the TLS connection is bound to the attested gateway. The agent does not re-implement these checks. To do them, the SDK itself contacts Intel's PCCS (`api.trustedservices.intel.com`) and NVIDIA's attestation service (`nras.attestation.nvidia.com`). They receive attestation evidence, not screenshots; see "What leaves your Mac" in the [README](../README.md).

The agent adds two rules:

- If the SDK returns no model report, the run stops (`Planner.start` raises `PlannerUnverified`).
- It records the result in `attestation.json` (`summarize_attestation`): TCB status, signer address and algorithm, GPU evidence status, compose and OS image hashes, TLS binding kind, and the list of all attested model signers. The set of signers is kept in memory for step 3.

The attestation must come before the first call: `Planner.plan()` raises if `start()` has not run.

## 2. The call

`Planner.plan()` sends one non-streaming `chat.completions.create` with the canonical model ID. A streamed or aliased request would get a gateway signature instead, which this agent refuses by default. The planner only runs with E2EE: `Planner.__init__` raises if it is not on, and there is no flag to turn it off.

With E2EE on, the SDK encrypts `messages` to the attested model key. Each request gets a fresh client "reply" key pair, and the model encrypts its answer to it. In the recorded request (`tests/fixtures/run/step-1.request.bin`), `messages[].content` is hex ciphertext. `model`, `max_completion_tokens`, `temperature`, `response_format`, `chat_template_kwargs` and `user` are plaintext.

The `user` field is set by `request_binding()` to `<run_id>:<step>:<screenshot sha256>`. It is visible to the gateway in clear text. It holds the run id, the step number and the SHA-256 of the screenshot (a hash, not pixels). Because the enclave signs the request bytes, the signature covers it.

## 3. Before acting: the signature check and the decrypted action

Right after the call the agent reads the exact request and response bytes from the SDK's record, before `verify_response` clears them. This uses a private SDK method, `_get_response_record`. If the SDK changes, the step fails closed (`PlannerUnverified`), it does not skip the check.

Then `verify_response(completion.id)` makes the SDK fetch and verify the signature. The agent then runs its own checks, in `Planner._check` and the rest of `Planner.plan`:

| Check | Fails if |
|---|---|
| Signature kind is `provider_tee` (`_check`) | The signature is from the gateway, unless `--allow-gateway-signature` was given. |
| `signed_text` equals the expected string built from the hashes of the captured bytes (`_check`) | Either hash differs. |
| Signer is one of the attested model signers (`_check`) | The signer was not attested before the run. For a gateway signature, it must be the attested gateway signer. |
| The sent request's `user` equals this run, step and screenshot hash (`plan`) | The signed request does not carry the binding. |
| Exactly one E2EE reply key was captured for this call (`plan`) | Zero or several keys. |
| The signed response decrypts and parses (`action_from_wire`), and equals the SDK's parsed action (`plan`) | Decryption fails, or the two actions differ. |

The agent acts on the action decrypted from the signed response bytes. It does not trust the SDK's parsed object, and it refuses if the two disagree.

Any failure raises `PlannerUnverified`. `run_task` in `agent/loop.py` then writes a `planner_unverified` event and stops without acting.

### Signed text

For `provider_tee` (built in `Planner._check`):

```
<model id>:<sha256 hex of the exact request body bytes>:<sha256 hex of the exact response body bytes>
```

Example (from `tests/fixtures/run/step-1.json`): `Qwen/Qwen3.8-27B:<64 hex>:<64 hex>`. The model ID is the one named in the request, and `verify` checks that the request body names the same model (`check_step` in `agent/verify.py`).

For `gateway` signatures, which are only accepted with the opt-in flag, the text has no model ID: `<request sha256>:<response sha256>`. The hashes are of the bytes as sent and received, not of re-serialized JSON.

## 4. After acting: the saved files

For each step the loop writes (`run_task` in `agent/loop.py`, `Run.write_wire` and `Run.event` in `agent/receipts.py`):

- `step-N.request.bin`, `step-N.response.bin`: the exact wire bytes.
- `step-N.reply_key.hex`: the reply key for that call, as `<algo>:<hex secret>`.
- `step-N.json`: the verification record (signature, signer, signed text, both hashes), the planner's action, and the action performed.
- `events.jsonl`: `run_started`, `attestation`, `step_started` (with the screenshot's SHA-256), `planner_verified`, `acted`, `run_finished`, and any `guard_blocked` or `planner_unverified`.

Where does the reply key come from? The SDK makes it internally and does not return it. `install_reply_key_capture()` wraps the SDK's key-pair function and records each pair. This is the one place the agent patches SDK internals. If the SDK is restructured, install fails with an error.

Why saving the key does not expose the screenshot: the key can decrypt only the response. The request was encrypted to the model's attested key with a throwaway secret that the client never keeps (see the comment above `install_reply_key_capture`). `tests/test_verify.py` (`test_reply_key_cannot_decrypt_the_request`) checks that the saved key does not open the request.

## 5. `python -m agent verify <run_dir>`

`verify_run` in `agent/verify.py` reads files only and opens no connection. It exits 1 on any FAIL. For every step, `check_step`, `check_action` and `_check_consistency` do:

| Column | What it checks | Code |
|---|---|---|
| `hashes` | SHA-256 of `request.bin` and `response.bin` equals the two hashes at the end of `signed_text`. | `check_step` |
| (model) | `signed_text` starts with the attested model ID, and the request body's `model` field is the same. | `check_step` |
| `signature` | The signature over `signed_text` is valid: ed25519 with PyNaCl, or ecdsa by recovering the Ethereum (EIP-191) address. | `_verify_signature` |
| `signer` | The signer is one of `model_signers` in `attestation.json` (all attested signers, as in the live check). For a gateway signature it is the gateway signer. | `check_step` |
| `bound` | The request JSON's clear-text `user` field equals `<run_id>:<step>:<screenshot sha256>`, built from `run_started` and the step's `step_started` event in `events.jsonl`. A step spliced from another run, or paired with another screenshot, fails. | `check_step` |
| `action` | `reply_key.hex` decrypts the signed `response.bin` with the SDK's own decryption, the action is parsed the way the planner parses it (`action_from_wire`, `parse_action`), and it equals `planner.action` in `step-N.json`. | `check_action` |
| (events) | The step's `completion_id`, `signed_text` and action equal those in its `planner_verified` event. | `_check_consistency` |

Whole-run checks that also FAIL the run:

- `run_started.e2ee` is not true.
- There is no `run_finished` event, or its `steps` count does not match.
- The steps are not exactly `1..N`, where N is `run_finished.steps`: a missing step, an extra step, or `request.bin`, `response.bin` or `reply_key.hex` files with no step file.
- The ending does not match `run_finished.status`: `done` needs the last step's signed action to be `done`; `failed` needs `fail`; `stopped` needs the last step to be an earned REFUSED or to carry a `guard_blocked` or `planner_unverified` event, or N to equal `run_started.max_steps` (receipts without `max_steps` fail here unless `--allow-unbound`). A signed `done` or `fail` anywhere but the last step also fails. N itself is only what the run wrote in `run_finished.steps`; the ending check is what ties it to signed data.
- A `step_started` event falls outside `1..N`, or a step file's own `step` number is wrong.
- The request messages are not end-to-end encrypted ciphertext (a non-string content, or a `data:image` URL).
- The signature is from the gateway but `run_started.allow_gateway_signature` is not true.
- The reply key is missing, unless `--allow-unbound`. That flag also accepts a request with no `user` field. The summary then says the result is weaker.
- An `acted` event exists for a `done` or `fail` action.

Results per step: `PASS`, `FAIL`, or `REFUSED` (the agent stopped without acting). A REFUSED step is accepted only if it is the final step, has a `guard_blocked` or `planner_unverified` event, and has no `acted` event and no `planner_verified` event. A refusal that does not meet this is a FAIL. The run passes only if every step passes or is an earned refusal, and at least one step verified. With `--allow-unbound` or a gateway-signed step, the final line says the result is weaker than a default PASS.

Run on the recorded fixture:

```
$ uv run python -m agent verify tests/fixtures/run
run run  model Qwen/Qwen3.8-27B  attested signer 89154a43a4768474...
step result   kind         hashes  signature signer  bound  action    recorded
1    PASS     provider_tee ok      ok        ok      ok     ok        click the Settings button
2    PASS     provider_tee ok      ok        ok      ok     ok        click the Save changes button
3    PASS     provider_tee ok      ok        ok      ok     ok        done
PASS: 3 of 3 step(s) verified; every step's signed reply is bound to its run, step and screenshot and is consistent with events.jsonl
```

`tests/test_verify.py` covers these failures (among them a truncated run that still says `done`, a status that does not match the last action, and a `done` in the middle): a flipped byte in the request or response, a changed signature or signed text, a wrong signer, a signer outside the attested set, a wrong model prefix, an edited recorded action, a swapped or garbage reply key, a missing reply key, a missing wire file, a missing, extra or renumbered step, a wrong `run_finished` count, a step file that disagrees with `events.jsonl`, a spliced run id or screenshot hash, a missing `user` field, a plaintext request, a gateway signature without opt-in, an unearned or non-final refusal, a non-zero exit code on FAIL, and a response re-signed with an attacker's key.

## What this does not establish

- **What the agent did on screen.** The signature covers the planner's reply. The clicks and keystrokes that followed are recorded by the agent itself (`acted` events, `action_performed`) and are not signed. Atom runs locally and is not signed either; the receipt records its instruction and the pixel it returned (`grounded` event), as reported by the agent.
- **Which instance answered.** NEAR documents that instances of a model share one signing key. A matching signer does not show that the attested report and the completion came from the same serving instance.
- **That the run was not deleted or trimmed.** Trailing steps cannot be dropped from a run that ended in `done` or `failed`, because the new last step would need a signed `done` or `fail` that the enclave never produced for that position. They can be dropped if the run is relabelled `stopped`, since the events that justify a stop (`guard_blocked`, the step budget) are unsigned. A whole run folder can always be deleted.
- **That the TDX quote is valid, offline.** `verify` compares the receipt's signer with the ones recorded in `attestation.json`. It does not re-verify the quote or GPU evidence, and `attestation.json` and `events.jsonl` are written by the same run. A forger who can rewrite the whole folder can make a consistent fake. The run-time check by the SDK is the one that binds the signer to hardware evidence.
- **That the screenshot hash matches a real screenshot.** `verify` checks that the hash in the signed request equals the one in `events.jsonl`. You can compare it to a screenshot only if the run used `--keep-screenshots`.
- **That the answer is correct.** The signature says what the enclave produced, not that it is a good action.
- **The action, without the reply key.** Under E2EE the signed bytes are ciphertext. The recorded action is tied to them only through the saved reply key, which is why a missing key is a FAIL by default.
