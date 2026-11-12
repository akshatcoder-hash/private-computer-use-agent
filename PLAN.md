# Build plan

Private computer-use agent for the NEAR Hackathon, Privacy & Private AI track.
Deadline: Nov 14 2026, 23:59 UTC. Plan written Nov 12, 13:30 UTC.

> Your screen is the most private data you have. Every pixel either stays on
> your Mac or goes only to an attested enclave.

## How it works

```
task ──► screenshot (stays on the Mac)
            │
            ├──► planner: Qwen/Qwen3.8-27B in NEAR AI Cloud's TEE
            │      • before the call: gateway + model attestation verified
            │        (Intel TDX quote, NVIDIA GPU evidence, fresh nonce),
            │        TLS pinned to the attested gateway key
            │      • screenshot encrypted to the attested model key (E2EE)
            │      • after the call: response signature checked against the
            │        exact request/response bytes and the attested model signer
            │      ◄── "click the Download PDF button"
            │
            ├──► Atom, local, 127.0.0.1:8760 ──► (x, y)
            │
            └──► click / type on the Mac, then a receipt for the step
```

Screenshots leave the process through one function with a two-entry allowlist:
loopback Atom and `cloud-api.near.ai`. Anything else raises. A test asserts it.

## What each model does

| Job | Model | Where it runs |
|---|---|---|
| Decide the next step from the screenshot | `Qwen/Qwen3.8-27B` (fallback `z-ai/glm-5.3-flash`) | NEAR AI Cloud TEE |
| Turn "click X" into coordinates | Atom (4-bit TinyClick / Florence-2-base, 169 MB) | This Mac, MLX |

Checked on Nov 12 with NEAR's SDK and no API key: `Qwen/Qwen3.8-27B`,
`deepseek-ai/DeepSeek-V4.1-Flash-1017`, `z-ai/glm-5.3-flash` and
`Qwen/Qwen3-VL-30B-A3B-Instruct` each return one model attestation report that
verifies (TDX TCB `UpToDate`, GPU evidence verified). The Anthropic, OpenAI,
Google and Kimi models return zero model reports, only the gateway's, so they
are ruled out as planners.

Qwen 3.8 27B is the first choice: vision, 262K context, structured outputs,
cheapest input. Milestone 2 runs it against GLM 5.3 Flash on the demo tasks and
keeps whichever finishes more of them.

## Milestones

| # | What | Done when | Target (UTC) |
|---|---|---|---|
| 1 | Go/no-go | `m1/planner_check.py` prints a verified `provider_tee` signature; `m1/click_check.py` lands one Atom click on the real screen | Nov 12 |
| 2 | Agent loop | One command runs a 3-step task end to end; each step writes a receipt JSON; egress allowlist test passes | Nov 13 06:00 |
| 3 | Live receipts | Side panel shows each step's receipt as it happens: attestation, signer, request/response hashes, where the screenshot went | Nov 13 14:00 |
| 4 | Demo tasks reliable | Each demo task passes 5 runs in a row | Nov 13 22:00 |
| 5 | README, one-command run, public repo | Fresh clone runs with one command; claims audit passes | Nov 14 08:00 |
| 6 | Video, X post, submit | Submitted at nearhackathon.com/#apply | Nov 14 16:00 |
| 7 | Stretch: NEAR Intents payment step | Only if 1 to 6 are finished | after 6 |

Eight hours of slack before the deadline.

## Demo tasks

Atom is strongest on labelled buttons and links, so every click target has
visible text. No icon-only targets.

1. **Download a bank statement** (hero). A local mock banking page with
   account numbers and balances on screen. "Download the October statement as
   PDF": Statements, October 2026, Download PDF. Shows sensitive pixels and a
   receipt for each of three steps.
2. **Real website.** Search Wikipedia for a term and open the result. Shows it
   is not confined to a page we built.
3. **Native app.** Type a short note in TextEdit. Typing needs no grounding, so
   it is the low-risk third task.

The video closes on a tampered receipt failing `verify`, to show the check is
real.

## Who does what in the build

- Main session: architecture, the NEAR verification path, the egress
  allowlist, integration, and every claim in the README.
- Subagents, in parallel once milestone 1 passes: the receipt panel, the mock
  banking page, and the demo-task runner.
- A fresh-context reviewer before submission: reads the code and README cold
  and lists any privacy claim the code does not back.

## What could go wrong

| Risk | Plan |
|---|---|
| Response signature comes back `gateway`, not `provider_tee` | Non-streaming calls with the canonical model ID and no aliasing. If it still comes back `gateway`, the receipt says so and the README claims only what was verified. |
| E2EE does not cover image content | Test in milestone 1. If not, images go over the attested-gateway-pinned TLS only, and the README says that. |
| The receipt cannot prove which serving instance answered | NEAR's docs say instances of a model share a signing key. The README states this limit. |
| Planner picks bad steps | Constrained JSON action schema, short step budget, GLM fallback, tasks chosen for few steps. |
| Atom misses a target | Text-labelled targets only; verify after each click; one retry with the planner's rephrasing. |
| macOS permissions | The terminal needs Screen Recording and Accessibility. The run command checks both and says which is missing. |
| Receipt panel pollutes the screenshot | Capture only the work area; the panel sits outside it. |
| Atom's runtime is not public | Judges cannot run the agent from the repo alone. The README says so and the video carries the demo. Publishing the runtime is the author's call. |
| NEAR API outage or rate limit near the deadline | Record the video by Nov 14 morning. |

## Claims the README will make

- Atom: a 4-bit quantized deployment of Samsung TinyClick (Florence-2-base
  lineage), about 0.27B parameters, 169 MB, no additional gradient updates.
  75.9% on ScreenSpot v2 (966/1,272), the author's own evaluation, not on a
  public leaderboard.
- Verification: exactly what the receipt checks, and the limits NEAR documents.
- Nothing about task success rates beyond what the repo's own runs show.
