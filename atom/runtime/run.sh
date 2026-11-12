#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_DIR="${ATOM_RUNTIME_DIR:-$HOME/Library/Application Support/PrivateAgent/Atom/Runtime}"
if [[ ! -x "$RUNTIME_DIR/venv/bin/python" ]]; then
  "$SCRIPT_DIR/bootstrap.sh" >/dev/null
fi
exec "$RUNTIME_DIR/venv/bin/python" "$SCRIPT_DIR/atom_runtime.py" "$@"
