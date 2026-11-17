#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${HOME}/gemini_py3_env/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3 2>/dev/null || command -v python 2>/dev/null)"
fi

SELF_PATH="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || realpath "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
REPO_DIR="$(cd "$(dirname "$SELF_PATH")/.." && pwd)"

exec "$PYTHON_BIN" "${REPO_DIR}/scripts/statusline_render.py"
