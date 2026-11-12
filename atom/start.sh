#!/bin/bash
# Start the Atom HTTP API on 127.0.0.1 (port from ATOM_PORT, default 8760).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export ATOM_RUNTIME_DIR="${ATOM_RUNTIME_DIR:-$HOME/Library/Application Support/PrivateAgent/Atom/Runtime}"
export ATOM_MODEL_DIR="${ATOM_MODEL_DIR:-$HOME/Library/Application Support/PrivateAgent/Atom/Models/tinyclick-4bit}"
PYTHON="$ATOM_RUNTIME_DIR/venv/bin/python"
if [[ ! -x "$PYTHON" || ! -d "$ATOM_MODEL_DIR" ]]; then
  "$HERE/install.sh"
fi
exec "$PYTHON" "$HERE/server/atom_api.py" --host 127.0.0.1 --port "${ATOM_PORT:-8760}" \
  --model-dir "$ATOM_MODEL_DIR" --python "$PYTHON"
