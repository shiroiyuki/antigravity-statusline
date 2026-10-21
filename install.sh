#!/usr/bin/env bash
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
GEMINI_DIR="${HOME}/.gemini"
CONFIG_DIR="${GEMINI_DIR}/antigravity-cli"
SETTINGS_FILE="${CONFIG_DIR}/settings.json"

echo "=========================================================="
echo "  🚀 Installing Antigravity Statusline & Subagent Audit"
echo "=========================================================="

mkdir -p "$BIN_DIR" "$GEMINI_DIR" "$CONFIG_DIR"

# 1. Symlink statusline.sh
STATUSLINE_TARGET="${GEMINI_DIR}/statusline.sh"
echo "• Linking statusline.sh -> ${STATUSLINE_TARGET}"
ln -sf "${REPO_DIR}/bin/statusline.sh" "$STATUSLINE_TARGET"
chmod +x "$STATUSLINE_TARGET"

# 2. Symlink subagent-audit CLI
echo "• Linking subagent-audit -> ${BIN_DIR}/subagent-audit"
ln -sf "${REPO_DIR}/bin/subagent-audit" "${BIN_DIR}/subagent-audit"
ln -sf "${REPO_DIR}/bin/subagent-audit" "${BIN_DIR}/agent-audit"
chmod +x "${BIN_DIR}/subagent-audit" "${BIN_DIR}/agent-audit"

if [ -d "${HOME}/gemini_py3_env/bin" ]; then
    ln -sf "${REPO_DIR}/bin/subagent-audit" "${HOME}/gemini_py3_env/bin/subagent-audit"
    ln -sf "${REPO_DIR}/bin/subagent-audit" "${HOME}/gemini_py3_env/bin/agent-audit"
fi

# 3. Update settings.json if present
if [ -f "$SETTINGS_FILE" ]; then
    echo "• Updating Antigravity configuration in ${SETTINGS_FILE}..."
    python3 -c "
import json, os

path = os.path.expanduser('$SETTINGS_FILE')
try:
    with open(path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
except Exception:
    cfg = {}

cfg['statusLine'] = {
    'type': 'command',
    'command': os.path.expanduser('$STATUSLINE_TARGET')
}
cfg['stack_with_default'] = False

with open(path, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
print('  ✓ Antigravity settings.json updated successfully.')
"
fi

echo "=========================================================="
echo "  ✅ Installation Complete!"
echo "  • Statusline command: ${STATUSLINE_TARGET}"
echo "  • Audit CLI command : subagent-audit (or agent-audit)"
echo "=========================================================="
