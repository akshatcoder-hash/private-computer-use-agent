# Reliability runs (WP5)

Date: 2026-11-12. Machine: Apple M4 Mac, 16 GB, macOS 27.0.1, 1920x1080 display, work window 1400x1050 below the menu bar.
Planner: `Qwen/Qwen3.8-27B` on NEAR AI Cloud (E2EE on, reasoning off). Grounder: Atom on `127.0.0.1:8760`.
Browser: Chrome for Testing 158 (work window and panel, separate profiles). Env for every run: `AGENT_ALLOW_OVERLAYS="Wispr Flow"` (a dictation app keeps an invisible overlay over the work area; the guard was not weakened for anything else).

Success is judged outside the agent, never by the agent's own "done":

- T1 and T2: the demo bank's event log (`demo/.events.jsonl`, served at `/events`).
- T3: the work browser's final page URL, read from Chromium's remote debugging port on loopback (`WORK_DEBUG_PORT=9333 ./run.sh ...`, opt-in, off by default).
- Every run also had to pass `uv run python -m agent verify <run>` with every step's action column `ok`.

## Result

| Task | Streak on the final prompt | Median planner latency / step | Median Atom latency / click | Median steps |
|---|---|---|---|---|
| T1 hero | 5 of 5 in a row | 1.68 s | 0.40 s | 4 |
| T2 transfer | 5 of 5 in a row | 1.23 s | 0.37 s | 8 |
| T3 Wikipedia | 5 of 5 in a row | 1.53 s | 0.48 s | 3 |
| T4 TextEdit | not run | | | |

Medians are over every step (planner) or every grounded click (Atom) of the final five runs of each task. Median wall time per run: T1 34.6 s, T2 45.4 s, T3 30.2 s. Tokens per run (mean): T1 7,863 prompt + 230 completion; T2 16,119 + 317; T3 5,861 + 137. All signatures were `provider_tee`.

Live runs with a planner call: 26 (T1 9, T2 11, T3 6), plus 2 T4 attempts that stopped at the guard before any call. Cap was 45.

## Commands and task strings

- T1: `./run.sh --demo` (task "Download the October statement as PDF", start URL the bank). Evidence: bank `/events` has `statement_downloaded`.
- T2: `./run.sh "Send \$250 to Jordan Reyes" --start-url http://127.0.0.1:8770/`. Evidence: `transfer_confirmed` with recipient `Jordan Reyes` and amount 250 (the bank logs `$250`); no other transfer_confirmed events.
- T3: `WORK_DEBUG_PORT=9333 ./run.sh "Search Wikipedia for trusted execution environment and open the article" --start-url https://en.wikipedia.org/`. Evidence: final URL is `https://en.wikipedia.org/wiki/Trusted_execution_environment`. Wikipedia screenshots go to the NEAR enclave.
- Per-run driver (not committed): ran the command, read the evidence, then `agent verify`.

## Every run

Run ids are directories in `receipts/` (gitignored). "PASS" means external evidence present and verify PASS with action `ok`.

### T1 (Qwen)

| Run | Outcome | Note |
|---|---|---|
| 20261112-211612 | PASS | First run, before the prompt tuning below. 4 steps. Not part of the streak. |
| 20261112-212345 | PASS | 4 steps, streak 1 |
| 20261112-212420 | PASS | streak 2 |
| 20261112-212454 | PASS | streak 3 |
| 20261112-212528 | PASS | streak 4 |
| 20261112-212603 | PASS | streak 5 |

### T2 (Qwen)

| Run | Outcome | Note |
|---|---|---|
| 20261112-211728 | FAIL | Before tuning. Typed "Jordan Reyes" six times; the field stayed on its placeholder in every following screenshot; step budget (12) used. Not reproduced in 8 later runs; cause not found (a diagnostic run of the same form with screenshots kept typed fine, so the click and the key events work). |
| 20261112-211937 | diagnostic | Different task ("Type Jordan Reyes in the To field", 3 steps). Typed correctly. Not counted. |
| 20261112-212012 | diagnostic | Same task with `--max-steps 5` to keep screenshots; stopped at the cap, as intended. Not a failure. |
| 20261112-212119, 212205, 212250 | PASS x3 | Before the final prompt. |
| 20261112-212642 | PASS | 8 steps, streak 1 |
| 20261112-212727 | PASS | streak 2 |
| 20261112-212813 | PASS | streak 3 |
| 20261112-212858 | PASS | streak 4 |
| 20261112-212943 | PASS | streak 5 |

### T3 (Qwen)

| Run | Outcome | Note |
|---|---|---|
| 20261112-211849 | PASS | Before the final prompt, 5 steps. |
| 20261112-213033 | PASS | 3 steps, streak 1 |
| 20261112-213104 | PASS | streak 2 |
| 20261112-213133 | PASS | streak 3 |
| 20261112-213202 | PASS | streak 4 |
| 20261112-213236 | PASS | streak 5 |

### T4 (TextEdit): not done

Two attempts, both "guard stop" (not task failures, no model call, nothing typed or clicked). TextEdit's first launch put a Finder Open panel on screen, and macOS then asked whether the terminal app may control TextEdit (an Automation permission). Only the user can grant that, and the guard correctly refuses to run while that dialog covers the work area. I clicked "Don't Allow" on the prompt I had caused and quit TextEdit. T4 needs that permission granted once, plus a launch method that avoids the Open panel, so it was skipped.

### Guard stops

2 (both T4, above). None during T1 to T3.

## Planner comparison on T1 (3 runs each)

| | Qwen/Qwen3.8-27B | z-ai/glm-5.3-flash |
|---|---|---|
| Success (external evidence) | 5/5 (final prompt) | 3/3 |
| Run ids | 212345, 212420, 212454, 212528, 212603 | 20261112-213350, 213430, 213505 |
| Median planner latency / step | 1.68 s (mean 1.85 s) | 1.99 s (mean 2.10 s) |
| Median steps | 4 | 4 |
| Median wall time per run | 34.6 s | 35.8 s |
| Prompt / completion tokens per run | 7,863 / 230 | 9,536 / 158 |
| Signature kind | provider_tee, every step | provider_tee, every step |
| verify | PASS, action ok | PASS, action ok |

GLM ran with `reasoning_effort: "low"` and a 2,500-token completion cap (it used 1 reasoning token per call). It is not clearly better on success or latency (slower per step, 21% more prompt tokens), so Qwen stays the default. `--model z-ai/glm-5.3-flash` works unchanged.

## What was changed to get here

- Screens: the capture and the guard region now start below the macOS menu bar (measured from the window list), so captures hold only the work window (1400x1050). Click mapping adds the region offset. The bank's pages fit in that viewport.
- Planner prompt: name fields by their label and buttons by their printed text; do not retype once the text is in the field; how to submit a search or form.
- `verify`: a step that the window guard stopped before any planner call is now shown as REFUSED instead of failing the whole receipt (it made a run with four verified steps and a later guard stop fail). A run with zero verified steps still fails.
- `run.sh`: `--model`, `--max-steps`, `--receipts-dir` and `--allow-overlay` now consume their value (before, `./run.sh --demo --model X` took X as the task); `WORK_DEBUG_PORT` opt-in prints the work browser's URL.

## Not verified

- T2's one early failure was not root-caused. After the prompt change it did not recur in 5 runs, but 3 runs also passed before the change, so the change is not shown to be the cause of anything.
- Five in a row is a small sample; it does not measure a rate. Wikipedia and Chrome can change.
- The streaks were taken with Wispr Flow's overlay allowed and nobody using the Mac. A notification banner or another app in front would give a guard stop, not a pass.
