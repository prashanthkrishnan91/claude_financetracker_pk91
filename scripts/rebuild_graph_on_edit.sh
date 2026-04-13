#!/usr/bin/env bash
# Called by PostToolUse Write|Edit hook.
# Rebuilds graphify-out/ only when a .py file is written.
set -euo pipefail

REPO=/home/user/claude_financetracker_pk91

# Hook stdin is JSON: read file_path from tool_input
input=$(cat)
fpath=$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_response.filePath // empty' 2>/dev/null || true)

if [[ "$fpath" == *.py ]]; then
    python3 "$REPO/scripts/build_code_graph.py" "$REPO"
fi
