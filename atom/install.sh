#!/bin/bash
# Install Atom: private Python runtime, then the verified model package.
# Idempotent. Overrides: ATOM_RUNTIME_DIR, ATOM_MODEL_DIR, ATOM_MODEL_SOURCE
# (https:// or file:// .tar.gz archive or file directory; default is the
# release archive pinned in runtime/model-manifest.json).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo "Atom needs an Apple Silicon Mac (macOS on arm64); this machine is $(uname -s)/$(uname -m)." >&2
  exit 1
fi
export ATOM_RUNTIME_DIR="${ATOM_RUNTIME_DIR:-$HOME/Library/Application Support/PrivateAgent/Atom/Runtime}"
export ATOM_MODEL_DIR="${ATOM_MODEL_DIR:-$HOME/Library/Application Support/PrivateAgent/Atom/Models/tinyclick-4bit}"
PYTHON="$("$HERE/runtime/bootstrap.sh")"
"$PYTHON" -I "$HERE/runtime/install_model.py" \
  --source "${ATOM_MODEL_SOURCE:-}" --destination "$ATOM_MODEL_DIR" >&2
echo "Atom installed. Runtime: $ATOM_RUNTIME_DIR  Model: $ATOM_MODEL_DIR" >&2
