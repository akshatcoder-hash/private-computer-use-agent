# Build plan and handoff

Private computer-use agent for the NEAR Hackathon, Privacy & Private AI track.
Deadline: **Nov 14 2026, 23:59 UTC**. This plan was written Nov 12, about
15:00 UTC, for handoff to Opus and Sonnet sessions. Each work package below
names its owner model, what it depends on, and how to tell it is done.

> Your screen is the most private data you have. Every pixel either stays on
> your Mac or goes only to an attested enclave.

## Status

Milestone 1 (go/no-go) **passed** on Nov 12:

- `m1/planner_check.py`: one vision call to `Qwen/Qwen3.8-27B` with E2EE on.
  Attestation verified before the call, the model read the test image, and
  the response carried a verified `provider_tee` ed25519 signature.
- `m1/click_check.py`: Atom grounded a button on a real screenshot and the
  click landed; the page itself confirmed receiving it.

Nothing else is built. `agent/egress.py`, `agent/screen.py` and
`agent/grounder.py` are **untested drafts**; WP1 should review them and keep
or rewrite.

## How it works

```
task ──► screenshot of the work area (stays on the Mac)
            │
            ├──► planner: Qwen/Qwen3.8-27B in NEAR AI Cloud's TEE
            │      before: gateway + model attestation verified, TLS pinned
            │      call:   screenshot encrypted to the attested model key
            │      after:  response signature verified BEFORE acting on it
            │      ◄── {"action": "click", "target": "the Download PDF button"}
            │
            ├──► Atom, local, 127.0.0.1:8760 ──► (x, y)
            │
            └──► click / type, then a receipt for the step
```

If the signature does not verify, the agent does not act and the run stops.

## Facts already established (do not re-derive)

**Machine.** Mac mini M4, 16 GB, macOS 27, one 1920x1080 display at 1x.
`uv` is installed; the project pins Python 3.12 to 3.13. No Google Chrome:
browsers are Safari, Arc, and Playwright's Chromium under
`~/Library/Caches/ms-playwright/chromium-1254` (plus the headless shell).

**Permissions.** Screen Recording and Accessibility are granted to the app
hosting the agent session. Shell commands that capture the screen or post
input events must run with the sandbox disabled.

**Atom.** The runtime is not at `~/atom-runtime`. Start it with:

```
"$HOME/Library/Application Support/<vendor>/Runtime/venv/bin/python" \
  ~/Developer/atom-inference/server/atom_api.py --port 8760
```

(`<vendor>` is the directory already on this machine; never write its name in
this repo.) Contract: `GET /health`; `POST /ground` with
`{"instruction", "image_base64"}` returns
`{"ok": true, "result": {"action": "click", "pixel": {x, y}, "image": {width, height}, "duration_ms"}}`;
any other `action` is an abstain. First call about 4 s, then about 350 ms.
It scored 10 of 10 on the five-target live-check page, run twice. The runtime
applies its own prompt wrapper and lowercases; send a short plain instruction
such as `click the Download PDF button`.

**NEAR.** `NEARAI_API_KEY` is in the gitignored `.env`. The account has $1 of
credit: Qwen 3.8 27B is $0.04 per million input tokens and $1.35 per million
output, so cap output tokens and keep reasoning off or the credit can go
quickly. Use the Python SDK, `nearai-inference-sdk==0.3.0`:

```python
async with InferenceClient(api_key, e2ee=True) as client:
    evidence = await client.verify(MODEL)          # gateway + model attestation
    completion = await client.chat.completions.create(model=MODEL, messages=[...])
    verified = await client.verify_response(completion.id)   # raises if not verified
```

- `evidence.models` is empty for models that do not run in NEAR's enclave.
  Verified non-empty for `Qwen/Qwen3.8-27B`,
  `deepseek-ai/DeepSeek-V4.1-Flash-1017`, `z-ai/glm-5.3-flash`,
  `Qwen/Qwen3-VL-30B-A3B-Instruct`. Empty for Anthropic, OpenAI, Google, Kimi.
- `verified.signature_kind` is `provider_tee` (model enclave) or `gateway`.
  Streaming and aliased model IDs produce `gateway`; use non-streaming calls
  with the canonical ID.
- `verified.signature` has `signer`, `signed_text`
  (`<model>:<sha256 request>:<sha256 response>`) and `signature`.
- The exact wire bytes are available before verification from the private
  `client._get_response_record(id)` (`.request_body`, and
  `await .response_body`); they are cleared after `verify_response`. Guard
  this with a fallback, since it is not public API.

**Not yet tested** (WP1 must check each before relying on it):

1. Under E2EE, is the screenshot really ciphertext on the wire? Check that the
   PNG's base64 does not appear in the request bytes.
2. Does `response_format` JSON schema work together with E2EE?
3. Does `chat_template_kwargs: {"enable_thinking": false}` turn reasoning off
   for Qwen 3.8? Docs confirm it only for Qwen 3.5 and 3.6.

## Shared contracts

All packages build to these. Change them only by editing this section.

**Planner action** (the model's JSON reply):

```
{"thought": "...", "action": "click",  "target": "the Statements link"}
{"thought": "...", "action": "type",   "text": "...", "enter": true}
{"thought": "...", "action": "key",    "key": "enter"}
{"thought": "...", "action": "scroll", "direction": "down"}
{"thought": "...", "action": "wait"}
{"thought": "...", "action": "done",   "summary": "..."}
{"thought": "...", "action": "fail",   "reason": "..."}
```

**Run directory** `receipts/<run_id>/` (gitignored): `events.jsonl`,
`step-N.json`, `step-N.request.bin`, `step-N.response.bin`, `attestation.json`.

**Events** (`events.jsonl`, one JSON object per line):

```
{"type":"run_started","run_id":"20261113-101502","task":"...","model":"Qwen/Qwen3.8-27B","e2ee":true,"time":"..."}
{"type":"attestation","attestation":{"verified_at":"...",
  "gateway":{"tcb_status","signing_algo","signer","tls_binding","tls_spki_fingerprint","compose_hash","os_image_hash"},
  "model":{"tcb_status","signing_algo","signer","gpu_evidence","compose_hash","os_image_hash"}}}
{"type":"step_started","step":1,"time":"...","screenshot":{"sha256","width","height","bytes"}}
{"type":"planner_verified","step":1,
  "planner":{"model","completion_id","e2ee","latency_ms","thought","action":{...}},
  "verification":{"verified":true,"checked_before_acting":true,"signature_kind","signing_algo","signer",
                  "signer_matches_attested_model":true,"signed_text","signature","request_sha256","response_sha256"}}
{"type":"planner_unverified","step":2,"error":"..."}
{"type":"grounded","step":1,"grounding":{"model":"Atom tinyclick-4bit-v1","where":"127.0.0.1:8760","instruction","pixel":{"x","y"},"duration_ms"}}
{"type":"acted","step":1,"result":"clicked (412, 88)"}
{"type":"run_finished","status":"done|failed|stopped","summary":"...","steps":3}
```

`step-N.json` is the same fields for one step, merged.

**Screen layout.** Work area is the left 1400 x 1080; the receipt panel is a
520 x 1080 window at the right edge. Only the work area is captured, so the
panel never appears in a screenshot.

**Ports.** Atom 8760, panel 8765, demo bank 8770. All bind 127.0.0.1.

## Work packages

### WP1. Agent core: Opus
Depends on: nothing. Everything else depends on its contracts, not its code.

- `agent/planner.py`: SDK client, attestation on start, one call per step with
  the current screenshot and a text history of earlier steps, JSON action
  reply, signature verified before returning. Requires `provider_tee` by
  default. Saves the wire bytes.
- `agent/loop.py`: capture, plan, verify, ground, act, record. Step budget of
  12. Tells the planner when the screen did not change after an action. One
  retry when Atom abstains.
- `agent/receipts.py`: writes the run directory and events.
- `python -m agent verify <run_dir>`: offline check. Recomputes both hashes
  from the saved bytes, checks them against `signed_text`, checks the ed25519
  signature, checks the signer equals the attested model signer recorded for
  the run. Exits non-zero on any mismatch.
- `tests/test_egress.py`: Atom URL must be a loopback IP literal, planner URL
  must be exactly `https://cloud-api.near.ai/v1`, and no other module in
  `agent/` imports a network client.
- Resolve the three untested items above and record the answers here.

Done when: a 3-step task on the m1 test page runs end to end from one
command, `verify` passes on the run, and `verify` fails after one byte of a
saved response is changed.

### WP2. Receipt panel: Sonnet
Depends on: the events contract only.

`panel/server.py` (standard library, serves the page and an SSE stream that
follows the newest run directory) and `panel/index.html`. No external
requests of any kind. Must be readable in a 1080p screen recording: large
type, dark theme. Shows, in order of importance:

1. Where screenshots went: N to the NEAR enclave (verified), N to Atom on
   this Mac, 0 anywhere else.
2. The enclave card: TDX status, GPU evidence, TLS binding, model signer,
   E2EE on or off.
3. One card per step, moving through captured, sent, verified, action,
   grounded, done. `planner_unverified` is red and unmissable. A `gateway`
   signature shows as a lesser, amber state.

Includes a sample `events.jsonl` and a replay script. Wording stays accurate:
"verified signature from the attested enclave key", never "proven secure".

Done when: screenshots at 520 x 1080 of a mid-run, a finished run and an
unverified run have been looked at and nothing overflows or is illegible.

### WP3. Mock bank site: Sonnet
Depends on: nothing.

`demo/bank/` static pages for an invented bank and `demo/serve.py` (standard
library; also records `POST /event` lines so test runs can confirm success).
Flow: Accounts page, "Statements" nav link, "View October 2026" button,
"Download PDF" button, then a visible confirmation banner. A transfers form
as a possible second task.

Rules that matter more than looks, because Atom is small: every control has
visible text at 16 px or larger; no two controls on a page share a label; no
near-duplicate labels side by side; nothing in the main flow below the fold
at 1400 x 1000; no modals, hover menus or animation.

Done when: the flow works by hand in a browser, the event log shows the
download, and Atom alone hits each of the three targets on screenshots of the
pages (use `POST /ground` directly).

### WP4. One-command run: Sonnet
Depends on: WP1, WP2, WP3.

`./run.sh "task"`: checks Apple silicon, permissions, Atom health and the API
key, saying exactly what is missing; starts the panel and the demo site;
opens the work-area browser at 0,0 sized 1400 x 1080 and the panel at the
right edge; runs the task. Browser choice: `BROWSER` if set, else Chrome if
present, else Playwright Chromium (`--app=`, `--window-position`,
`--window-size`). `./run.sh --demo` runs the hero task.

Done when: from a clean clone with Atom running, one command produces a
finished run with the panel live.

### WP5. Reliability: Opus
Depends on: WP1, WP3, WP4.

Run each demo task until it passes five times in a row. Tune the planner
prompt and step verification, not Atom. Compare Qwen 3.8 27B against
`z-ai/glm-5.3-flash` on the hero task and keep the better one. Record real
pass counts and latencies in `docs/RUNS.md`; the README quotes only those.

### WP6. README and claims: Sonnet drafts, Opus audits
Depends on: WP5.

Sonnet drafts: what it is, the one command, the NEAR tools used (NEAR AI
Cloud private inference, attestation, response signatures, E2EE, the
inference SDK), how a receipt is checked, limits. Then a **fresh** Opus
session reads the code and README cold and lists every claim the code does
not back; fix or cut each one.

Claims that must be exact:

- Atom: a 4-bit quantized deployment of Samsung TinyClick (Florence-2-base
  lineage), about 0.27B parameters, 169 MB, no additional gradient updates.
  75.9% on ScreenSpot v2 (966/1,272), the author's own evaluation, not on a
  public leaderboard. Link `akshatcoder-hash/atom-screenspot-v2`.
- NEAR documents that instances of a model share a signing key, so a receipt
  does not prove which serving instance answered.
- Offline `verify` checks the signature over the wire bytes. Under E2EE those
  are ciphertext, so the decrypted action in a receipt is as recorded by the
  client.
- Atom's runtime is not public, so the repo alone does not run the agent.
- Never the word that is banned from this repo, in files, commits or metadata.

### WP7. Video, post, submit: human, with Sonnet for copy
Depends on: WP5, WP6.

Video of about 90 seconds: the pitch line, the hero task with the panel
filling in, a quick second task, then one byte changed in a saved response
and `verify` failing. Post on X, then submit at nearhackathon.com/#apply
with the repo, the post and the README.

### WP8. Stretch, NEAR Intents payment step: Sonnet to research, Opus to build
Only after WP7 is submitted. Do not start early.

## Order and timing (UTC)

| When | What |
|---|---|
| Nov 12 evening | WP1, WP2, WP3 in parallel |
| Nov 13 by 06:00 | WP1 done; WP4 starts |
| Nov 13 by 14:00 | WP4 done; WP5 starts |
| Nov 13 by 22:00 | WP5 done; WP6 starts |
| Nov 14 by 08:00 | WP6 done, repo final |
| Nov 14 by 16:00 | WP7 submitted |
| Nov 14 16:00 to 23:59 | Slack, or WP8 |

## Demo tasks

1. **Download a bank statement** (hero): "Download the October statement as
   PDF". Three clicks on the mock bank site, sensitive data on screen.
2. **Real website**: search Wikipedia for a term and open the result.
3. **Native app**: type a short note in TextEdit. No grounding needed, so it
   is the low-risk third task.

Drop task 2 or 3 rather than ship one that passes less than five in a row.

## What could go wrong

| Risk | Plan |
|---|---|
| Signature comes back `gateway` | Non-streaming, canonical model ID. If it still happens the panel shows the lesser state and the README claims only that. |
| E2EE turns out not to cover images | README says images travel over TLS pinned to the attested gateway, and drops the model-key claim. |
| Structured output fails under E2EE | Ask for JSON in the prompt and parse defensively. |
| Qwen reasoning cannot be turned off | Cap output tokens; consider GLM 5.3 Flash with `reasoning_effort: "low"`. |
| Planner picks bad steps | Short tasks, text-labelled targets, screen-changed feedback, step budget. |
| Atom misses | Unique visible labels; one retry with a rephrased target. |
| $1 of credit runs out during WP5 | Watch usage per run; top up before the reliability runs. |
| Session loses Accessibility after an app restart | `run.sh` reports it; re-grant in System Settings. |
| NEAR outage near the deadline | Record the video by the morning of Nov 14. |

## Decisions for the author

1. Publish Atom's runtime, or leave judges with the video and receipts only?
2. Top up NEAR credit before WP5?
3. Which X account posts the demo?
