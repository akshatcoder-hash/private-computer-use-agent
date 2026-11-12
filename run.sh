#!/bin/bash
# One command: ./run.sh "<task>" [--start-url URL] [more agent flags]   or   ./run.sh --demo
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

ATOM_PORT="${ATOM_PORT:-8760}"; PANEL_PORT=8765; BANK_PORT=8770
ATOM_URL="http://127.0.0.1:$ATOM_PORT"; PANEL_URL="http://127.0.0.1:$PANEL_PORT"; BANK_URL="http://127.0.0.1:$BANK_PORT/"
HERO_TASK="Download the October statement as PDF"

die() { echo "run.sh: $*" >&2; exit 2; }
# with_timeout SECONDS cmd...: kills cmd after SECONDS (exit 142), so a stalled System Events cannot hang this script.
with_timeout() { local n="$1"; shift; perl -e 'alarm shift; exec @ARGV' "$n" "$@"; }

TASK=""; START_URL=""; DEMO=0; EXTRA=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --demo) DEMO=1; shift ;;
    --start-url) [[ $# -ge 2 ]] || die "--start-url needs a URL"; START_URL="$2"; shift 2 ;;
    --start-url=*) START_URL="${1#*=}"; shift ;;
    -h|--help) sed -n 2,2p "$0"; exit 0 ;;
    --model|--max-steps|--receipts-dir|--allow-overlay) [[ $# -ge 2 ]] || die "$1 needs a value"; EXTRA+=("$1" "$2"); shift 2 ;;
    --*) EXTRA+=("$1"); shift ;;
    *) if [[ -z "$TASK" ]]; then TASK="$1"; else EXTRA+=("$1"); fi; shift ;;
  esac
done
if [[ $DEMO -eq 1 ]]; then TASK="${TASK:-$HERO_TASK}"; START_URL="${START_URL:-$BANK_URL}"; fi
[[ -n "$TASK" ]] || die 'usage: ./run.sh "<task>" [--start-url URL]   or   ./run.sh --demo'
START_URL="${START_URL:-about:blank}"

# 1. Machine
[[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]] || die "this needs a Mac with Apple silicon (found $(uname -s) $(uname -m))."

# 2. uv
command -v uv >/dev/null 2>&1 || die 'uv is not installed. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh   (then open a new terminal)'

# 3. API key
if [[ -z "${NEARAI_API_KEY:-}" && -f .env ]]; then
  NEARAI_API_KEY="$(grep -E '^NEARAI_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d "\"'" )"
fi
[[ -n "${NEARAI_API_KEY:-}" ]] || die "NEARAI_API_KEY is not set. Create a key at https://cloud.near.ai, then put the line NEARAI_API_KEY=<your key> in $ROOT/.env (or export it)."
export NEARAI_API_KEY

# 4. Permissions (these belong to the terminal app that runs this script)
echo "checking Python environment and macOS permissions..."
PERM="$(uv run python -c 'from agent import screen; print(", ".join(screen.missing_permissions()))' 2>&1)" \
  || die "could not start the agent environment: $PERM"
[[ -z "$PERM" ]] || die "this terminal app lacks: $PERM. Open System Settings > Privacy & Security, switch this terminal on under each of those entries, then quit and reopen the terminal and run this again."

# macOS asks once whether this terminal may control "System Events" (used to bring our window to the front).
AUTO_ERR="$(with_timeout 60 osascript -e 'tell application "System Events" to get name of first process' 2>&1 >/dev/null)" \
  || die "this terminal is not allowed to control System Events (${AUTO_ERR:-no detail}). Open System Settings > Privacy & Security > Automation, find this terminal app, switch on \"System Events\", then run this again."

# The layout is the work area (1400 x 1080 points) plus the receipt panel (520 x 1080): 1920 x 1080 at least.
SIZE="$(uv run python -c 'from agent import screen; w, h = screen.display_size(); print(w, h)' 2>&1)" \
  || die "could not read the display size: $SIZE"
read -r DISPLAY_W DISPLAY_H <<<"$SIZE"
if [[ "${DISPLAY_W:-0}" -lt 1920 || "${DISPLAY_H:-0}" -lt 1080 ]]; then
  die "the main display is ${DISPLAY_W}x${DISPLAY_H} points; this needs at least 1920x1080 (work area 1400 + panel 520, 1080 high). Use a larger display or a larger scaled resolution in System Settings > Displays."
fi

# check_region ignores the Dock, so a visible one must not be able to sit over the bottom of the 1080-point work window.
DOCK_WHY="$(uv run python -m agent dock --region 0,0,1400,1080 2>&1)" \
  || die "$DOCK_WHY. Turn on 'Automatically hide and show the Dock' (System Settings > Desktop & Dock), the work window needs the full 1080-point height."

# Bookkeeping so we stop only what we started
PIDS=(); TMPS=()
ATOM_LOG="$HOME/Library/Logs/private-agent/atom.log"   # kept after the run, on purpose: it is how a failed start is diagnosed
mkdir -p "$(dirname "$ATOM_LOG")"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/private-agent.XXXXXX")"; TMPS+=("$TMP")
cleanup() {
  trap - EXIT INT TERM
  local pid
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do kill "$pid" 2>/dev/null; done
  sleep 0.5
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do kill -9 "$pid" 2>/dev/null; done
  wait 2>/dev/null
  rm -rf "${TMPS[@]}"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

up() { curl -fsS -m 2 "$1" >/dev/null 2>&1; }
logpath() { if [[ "$1" == atom ]]; then echo "$ATOM_LOG"; else echo "$TMP/$1.log"; fi; }
wait_up() { # url seconds pid label
  local i=0
  while ! up "$1"; do
    kill -0 "$3" 2>/dev/null || die "$4 exited before it was ready. Log: $(logpath "$4")"
    i=$((i + 1)); [[ $i -lt $(( $2 * 2 )) ]] || die "$4 did not become ready within $2 s. Log: $(logpath "$4")"
    sleep 0.5
  done
}

# 5. Atom
if up "$ATOM_URL/health"; then
  echo "Atom: already running on $ATOM_URL, reusing it."
else
  echo "Atom: starting it on $ATOM_URL. On the first run this downloads the runtime and about 150 MB of weights."
  echo "      That is a one-time step and can take several minutes; later runs start in seconds."
  echo "      Atom log: $ATOM_LOG"
  ATOM_PORT="$ATOM_PORT" atom/start.sh >"$ATOM_LOG" 2>&1 &
  PIDS+=("$!")
  wait_up "$ATOM_URL/health" 1800 "$!" atom
  echo "Atom: ready."
fi

# 6. Panel and demo bank
PY="$(uv run python -c 'import sys; print(sys.executable)')"  # the project's interpreter, run directly so cleanup kills the server itself
if up "$PANEL_URL/"; then echo "panel: already running on $PANEL_URL."; else
  uv run python panel/server.py --receipts receipts --port "$PANEL_PORT" >"$TMP/panel.log" 2>&1 &
  PIDS+=("$!"); wait_up "$PANEL_URL/" 30 "$!" panel
fi
if [[ $DEMO -eq 1 || "$START_URL" == "$BANK_URL"* ]]; then
  if up "$BANK_URL"; then echo "demo bank: already running on $BANK_URL."; else
    "$PY" demo/serve.py --port "$BANK_PORT" >"$TMP/bank.log" 2>&1 &
    PIDS+=("$!"); wait_up "$BANK_URL" 30 "$!" bank
  fi
  curl -fsS -m 5 -X POST "${BANK_URL}reset" >/dev/null || die "could not reset the demo bank's event log at $BANK_URL"
  BANK=1
else
  BANK=0
fi

# 7. Windows
BROWSER_BIN=""
if [[ -n "${BROWSER:-}" && -x "${BROWSER}" ]]; then BROWSER_BIN="$BROWSER"
elif [[ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]]; then BROWSER_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
else
  for c in "$HOME"/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/"Google Chrome for Testing.app"/Contents/MacOS/"Google Chrome for Testing"; do
    [[ -x "$c" ]] && BROWSER_BIN="$c"
  done
fi
[[ -n "$BROWSER_BIN" ]] || die "no Chromium-family browser found. Install Google Chrome, or run: uv run --with playwright python -m playwright install chromium   (or set BROWSER to a Chromium binary)."

DOWNLOADS="$TMP/downloads"; mkdir -p "$DOWNLOADS"
open_window() { # name x y w h url   (profile dir name = $1) ; sets LAST_PID
  local profile="$TMP/profile-$1"; mkdir -p "$profile/Default"
  if [[ "$1" == "work" ]]; then
    DL="$DOWNLOADS" uv run python -c '
import json, os
d = os.environ["DL"]
print(json.dumps({"download": {"default_directory": d, "prompt_for_download": False, "directory_upgrade": True},
                  "savefile": {"default_directory": d}, "profile": {"default_content_setting_values": {"automatic_downloads": 1}}}))' \
      >"$profile/Default/Preferences"
  fi
  local debug=(); [[ "$1" == "work" && -n "${WORK_DEBUG_PORT:-}" ]] && debug=("--remote-debugging-port=$WORK_DEBUG_PORT" "--remote-debugging-address=127.0.0.1")
  "$BROWSER_BIN" "--user-data-dir=$profile" ${debug[@]+"${debug[@]}"} --use-mock-keychain --password-store=basic --no-first-run --no-default-browser-check \
    "--window-position=$2,$3" "--window-size=$4,$5" "--app=$6" >/dev/null 2>&1 &
  LAST_PID=$!; PIDS+=("$LAST_PID")
}
front() { with_timeout 5 osascript -e "tell application \"System Events\" to set frontmost of (first process whose unix id is $1) to true" >/dev/null 2>&1; }

open_window panel 1400 0 520 1080 "$PANEL_URL/"; PANEL_WIN=$LAST_PID
sleep 2
open_window work 0 0 1400 1080 "$START_URL"; WORK_WIN=$LAST_PID
sleep 2.5
# The agent clicks and types on whatever is on screen: refuse to start unless our own work window is in front.
# Same function the agent uses before every capture and action (agent/screen.py): our window covers the work area and has focus.
is_front() { GUARD_WHY="$(uv run python -m agent guard --pid "$1" --region 0,0,1400,1080 2>&1)"; }
FRONT_OK=0
for _ in 1 2 3 4 5 6; do
  kill -0 "$WORK_WIN" 2>/dev/null || die "the work-area browser exited right after starting ($BROWSER_BIN)."
  front "$WORK_WIN"; sleep 0.8
  if is_front "$WORK_WIN"; then FRONT_OK=1; break; fi
done
[[ $FRONT_OK -eq 1 ]] || die "could not bring the work-area browser to the front (${GUARD_WHY:-unknown reason}), so nothing was run. Close or minimise other windows and try again."
echo "windows: panel at 1400,0 (520x1080); work area at 0,0 (1400x1080) on $START_URL"
echo "downloads from the work-area browser land in: $DOWNLOADS (temporary; removed on exit)"

# 8. Run
LOG="$TMP/agent.log"
echo
PYTHONUNBUFFERED=1 uv run python -m agent run "$TASK" --no-browser --work-pid "$WORK_WIN" --work-profile "$TMP/profile-work" --start-url "$START_URL" --receipts-dir receipts ${EXTRA[@]+"${EXTRA[@]}"} 2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}

# 9. Report
RUN_DIR="$(sed -n 's/^receipt: //p' "$LOG" | tail -1)"
RESULT="$RC"
echo
echo "downloaded files: $(ls -1 "$DOWNLOADS" 2>/dev/null | tr '\n' ' ')(in $DOWNLOADS, removed on exit)"
if [[ -n "$RUN_DIR" ]]; then
  echo "run dir: $RUN_DIR"
  echo "verify:  uv run python -m agent verify $RUN_DIR"
  if uv run python -m agent verify "$RUN_DIR" >"$TMP/verify.log" 2>&1; then echo "verify result: PASS (every step signed, bound to this run, and consistent)"; else echo "verify result: FAIL"; tail -5 "$TMP/verify.log"; RESULT=1; fi
fi
if [[ -n "${WORK_DEBUG_PORT:-}" ]]; then  # opt-in, for checking the outcome from outside the agent (loopback only)
  echo "work browser url: $(curl -fsS -m 3 "http://127.0.0.1:$WORK_DEBUG_PORT/json/list" 2>/dev/null | uv run python -c 'import json,sys; print(next((t["url"] for t in json.load(sys.stdin) if t.get("type")=="page"), ""))' 2>/dev/null)"
fi
if [[ $DEMO -eq 1 ]]; then
  if curl -fsS -m 5 "${BANK_URL}events" | grep -q '"statement_downloaded"'; then
    echo "DEMO PASS: the bank logged statement_downloaded."
  else
    echo "DEMO FAIL: the bank did not log statement_downloaded (whatever the agent claimed)."; RESULT=1
  fi
fi
exit "$RESULT"
