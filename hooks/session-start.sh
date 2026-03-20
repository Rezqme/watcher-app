#!/usr/bin/env bash
# session-start.sh — Claude Code SessionStart hook
# Registers the session in ~/.claude-watch/sessions/ regardless of watcher state.
set -euo pipefail

PAYLOAD=$(cat)
SESSION_ID=$(echo "$PAYLOAD" | jq -r '.session_id // empty')
[ -z "$SESSION_ID" ] && exit 0

SESSION_DIR="$HOME/.claude-watch/sessions/$SESSION_ID"
mkdir -p "$SESSION_DIR"

echo "$SESSION_ID"        > "$SESSION_DIR/session_id"
echo "$(date +%s)"        > "$SESSION_DIR/started_at"
echo "$(date +%s)"        > "$SESSION_DIR/last_activity"
echo "active"             > "$SESSION_DIR/status"
echo "$(basename "$PWD")" > "$SESSION_DIR/project_name"
echo "$PWD"               > "$SESSION_DIR/project_dir"
echo ""                   > "$SESSION_DIR/task"
echo ""                   > "$SESSION_DIR/thread_guid_imessage"
echo ""                   > "$SESSION_DIR/thread_guid_telegram"
echo "skip"               > "$SESSION_DIR/mode"

# Channel flags are absent by default.
# notify_imessage  ← touch to enable iMessage for this session
# notify_telegram  ← touch to enable Telegram for this session
# These are created by the spawner (watcherd) or toggle commands.

exit 0
