# Reliability runs (WP5)

Date: 2026-11-12 (re-measured on the current code, after the verifier and guard were hardened). Machine: Apple M4 Mac, 16 GB, macOS 27.0.1, 1920x1080 display, work window 1400x1050 below the menu bar.
Planner: `Qwen/Qwen3.8-27B` on NEAR AI Cloud (E2EE on, reasoning off). Grounder: Atom on `127.0.0.1:8760`.
Browser: Chrome for Testing 158 (work window and panel, separate profiles). Env for every run: `AGENT_ALLOW_OVERLAYS="Wispr Flow"` (a dictation app keeps an invisible overlay over the work area; the guard was not weakened for anything else).

Success is judged outside the agent, never by the agent's own "done":

- T1 and T2: the demo bank's event log (`demo/.events.jsonl`, served at `/events`).
- T3: the work browser's final page URL, read from Chromium's remote debugging port on loopback (`WORK_DEBUG_PORT=9333 ./run.sh ...`, opt-in, off by default).
- Every run also had to pass `uv run python -m agent verify <run>` with the default (strict) verifier, no `--allow-unbound`.

## Result

| Task | Streak | Median planner latency / step | Median Atom latency / click | Median steps | Median wall time / run | Tokens / run (mean) |
|---|---|---|---|---|---|---|
| T1 hero | 5 of 5 in a row | 8.19 s | 0.43 s | 4 | 74.8 s | 8,119 prompt + 236 completion |
| T2 transfer | 5 of 5 in a row | 7.33 s | 0.43 s | 8 | 113.2 s | 16,631 + 309 |
| T3 Wikipedia | 5 of 5 in a row | 6.52 s | 0.51 s | 3 | 42.8 s | 6,053 + 131 |

Medians are over every step (planner) or every grounded click (Atom) of the five runs in each final streak. Wall time is the whole `./run.sh` call, including window start-up and the attestation check. All signatures were `provider_tee`.

The planner latency is 4 to 5 times higher than in the earlier measurement (1.2 to 1.7 s). Individual steps ranged from 1.2 s to 40.7 s. The Atom numbers did not change. A follow-up probe right after the streaks sent nine identical one-word text calls (no screenshot) to the same model: completion times ranged from 0.58 s to 18.24 s, with spikes both with and without the new `user` binding field, so the variance comes from the planner service or the network at that time, not from this change.

Live runs: 20 (T1 8, T2 7, T3 5), of which 18 made a planner call. Cap was 30. The T1 count includes the recording dry run below.

## Final streaks (run ids in `receipts/`, gitignored)

- T1: 20261112-225953, 20261112-230045, 20261112-230245, 20261112-230429, 20261112-230544
- T2: 20261112-231351, 20261112-231541, 20261112-231724, 20261112-231920, 20261112-232113
- T3: 20261112-232232, 20261112-232331, 20261112-232414, 20261112-232505, 20261112-232543

## Commands and task strings

- T1: `./run.sh --demo` (task "Download the October statement as PDF", start URL the bank). Evidence: bank `/events` has `statement_downloaded`.
- T2: `./run.sh "Send \$250 to Jordan Reyes" --start-url http://127.0.0.1:8770/`. Evidence: exactly one `transfer_confirmed` event, recipient `Jordan Reyes`, amount `$250`.
- T3: `WORK_DEBUG_PORT=9333 ./run.sh "Search Wikipedia for trusted execution environment and open the article" --start-url https://en.wikipedia.org/`. Evidence: final URL is `https://en.wikipedia.org/wiki/Trusted_execution_environment`. Wikipedia screenshots go to the NEAR enclave.
- Env for every run: `AGENT_ALLOW_OVERLAYS="Wispr Flow"`. For T1 and T2 the demo bank (`demo/serve.py --port 8770`) was started beforehand and kept running, so its event log could be read after `run.sh` exited (`run.sh` resets it at the start of each run).
- Per-run driver (not committed): ran the command, read the evidence, then ran `agent verify` (`run.sh` also does). A run counts as PASS only with the evidence present and verify exit 0.

## Every run

"Guard stop" means the window guard refused to continue because another app was in front. It is not a task failure and did not reset the streak.

### T1

| Run | Outcome | Reason |
|---|---|---|
| 20261112-225910 | guard stop | Notification Center in front at step 1; no model call. |
| 20261112-225953 | PASS | 4 steps. Screen recording ran during this run (see below). Streak 1. |
| 20261112-230045 | PASS | 4 steps. Streak 2. |
| 20261112-230137 | guard stop | Notification Center in front at step 4. The statement had already been downloaded, but the run stopped, so it is not counted. |
| 20261112-230245 | PASS | 4 steps. Streak 3. |
| 20261112-230402 | guard stop | "MenuBarAgent" in front at step 2. |
| 20261112-230429 | PASS | 4 steps. Streak 4. |
| 20261112-230544 | PASS | 4 steps. Streak 5. |

### T2

| Run | Outcome | Reason |
|---|---|---|
| 20261112-231150 | PASS | 8 steps. Not in the final streak (the next run failed). |
| none (no receipt written) | FAIL | The agent crashed before its first capture: `subprocess.TimeoutExpired` from `screen.bring_to_front` (`osascript ... System Events` took longer than its 5 s timeout), uncaught in `agent/loop.py` `passes`. No click or key was sent. This is a bug: a slow System Events should end as a guard stop, not a traceback. |
| 20261112-231351 | PASS | Streak 1. |
| 20261112-231541 | PASS | Streak 2. |
| 20261112-231724 | PASS | Streak 3. |
| 20261112-231920 | PASS | Streak 4. |
| 20261112-232113 | PASS | Streak 5. |

### T3

| Run | Outcome | Reason |
|---|---|---|
| 20261112-232232 | PASS | 3 steps. Streak 1. |
| 20261112-232331 | PASS | Streak 2. |
| 20261112-232414 | PASS | Streak 3. |
| 20261112-232505 | PASS | Streak 4. |
| 20261112-232543 | PASS | Streak 5. |

### T4 (TextEdit)

Not run in this measurement. The earlier attempts were stopped by the guard (a TextEdit Open panel and a macOS Automation prompt) and T4 is not part of the results.

## Recording dry run (T1)

`screencapture -v -V 45 -x <file>` was started two seconds before `./run.sh --demo` on run 20261112-225953. The run passed (evidence, verify, no guard stop), so the recorder did not trip the guard. The 45 s file was 14.8 MB, about 20 MB per minute. The file was deleted. The QuickTime screen-recording control bar was not tested. As a recommendation, not a tested fact: it normally appears at the bottom centre of the screen, which is inside the work area (x under 1400), so prefer `screencapture -v` or a keyboard-shortcut recording over QuickTime's toolbar.

## Earlier measurements

The first streaks (2026-11-12) were recorded before the run, step and screenshot binding and the stricter verifier. Their receipts no longer pass `verify`, and their numbers are superseded by the table above.

## Planner comparison on T1 (earlier code)

Measured with the earlier code (before the binding and the stricter verifier); not repeated.

GLM ran with `reasoning_effort: "low"` and a 2,500-token completion cap (it used 1 reasoning token per call). It is not clearly better on success or latency (slower per step, 21% more prompt tokens), so Qwen stays the default. `--model z-ai/glm-5.3-flash` works unchanged.


## Not verified

- The latency rise is attributed to service-side variance by a small probe (nine calls), not by a controlled study.
- Five in a row is a small sample; it does not measure a rate. One T2 run in this round crashed on an `osascript` timeout, and 3 T1 runs were stopped by the guard when a notification banner or a menu bar app came forward.
- The streaks were taken with Wispr Flow's overlay allowed and nobody using the Mac. The recording check covers `screencapture -v` only.
- Wikipedia and Chrome can change.
