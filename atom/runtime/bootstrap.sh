#!/bin/bash
# Installs private app runtime; does not touch shell startup files or system Python.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_DIR="${ATOM_RUNTIME_DIR:-$HOME/Library/Application Support/PrivateAgent/Atom/Runtime}"
UV_VERSION="0.12.0"
UV_SHA256="2b9e582af54f84fa50c115427451a6c13e80f43b52f8282b8af5791077317bbf"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo 'Atom requires an Apple Silicon Mac.' >&2
  exit 1
fi
mkdir -p "$RUNTIME_DIR/bin"
if [[ ! -x "$RUNTIME_DIR/bin/uv" ]]; then
  TEMP_DIR="$(mktemp -d "$RUNTIME_DIR/.bootstrap.XXXXXX")"
  trap 'rm -rf "$TEMP_DIR"' EXIT
  curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
    "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-aarch64-apple-darwin.tar.gz" \
    -o "$TEMP_DIR/uv.tar.gz"
  printf '%s  %s\n' "$UV_SHA256" "$TEMP_DIR/uv.tar.gz" | shasum -a 256 -c - >&2
  tar -xzf "$TEMP_DIR/uv.tar.gz" -C "$TEMP_DIR"
  cp "$TEMP_DIR/uv-aarch64-apple-darwin/uv" "$RUNTIME_DIR/bin/uv"
fi
export UV_PYTHON_INSTALL_DIR="$RUNTIME_DIR/python"
export UV_CACHE_DIR="$RUNTIME_DIR/cache"
export UV_PYTHON_PREFERENCE=only-managed
"$RUNTIME_DIR/bin/uv" python install --no-bin 3.11.14 >&2
if [[ ! -x "$RUNTIME_DIR/venv/bin/python" ]]; then
  "$RUNTIME_DIR/bin/uv" venv --python 3.11.14 "$RUNTIME_DIR/venv" >&2
fi
"$RUNTIME_DIR/bin/uv" pip sync --python "$RUNTIME_DIR/venv/bin/python" \
  --require-hashes "$SCRIPT_DIR/requirements.lock" >&2
"$RUNTIME_DIR/venv/bin/python" -c 'import mlx.core, mlx_vlm, PIL' >&2
printf '%s\n' "$RUNTIME_DIR/venv/bin/python"
