#!/usr/bin/env bash
# stop.sh — Claude Code Stop hook
# Marks session as completed and sends stop notification via enabled channels.
set -euo pipefail

PAYLOAD=$(cat)
SESSION_ID=$(echo "$PAYLOAD" | jq -r '.session_id // empty')

SESSION_DIR="$HOME/.claude-watch/sessions/$SESSION_ID"
if [ -d "$SESSION_DIR" ]; then
    echo "completed" > "$SESSION_DIR/status"
    date +%s         > "$SESSION_DIR/completed_at"
    date +%s         > "$SESSION_DIR/last_activity"
fi

GLOBAL_ON="$HOME/.claude-watch/enabled"
ACTIVE_CHANNELS="$HOME/.claude-watch/active_channels"
IMSG_ON="$SESSION_DIR/notify_imessage"
TG_ON="$SESSION_DIR/notify_telegram"

SEND_PY="$HOME/.claude-watch/send.py"

SENT=0

IMESSAGE_ACTIVE=0
TELEGRAM_ACTIVE=0

if [ -f "$ACTIVE_CHANNELS" ]; then
    if grep -q '"imessage"' "$ACTIVE_CHANNELS" 2>/dev/null; then
        IMESSAGE_ACTIVE=1
    fi
    if grep -q '"telegram"' "$ACTIVE_CHANNELS" 2>/dev/null; then
        TELEGRAM_ACTIVE=1
    fi
else
    IMESSAGE_ACTIVE=1
    TELEGRAM_ACTIVE=1
fi

if { [ -f "$GLOBAL_ON" ] && [ "$IMESSAGE_ACTIVE" -eq 1 ]; } || [ -f "$IMSG_ON" ]; then
    "$SEND_PY" --channel imessage --event stop --session-id "$SESSION_ID" && SENT=1 || true
fi

if { [ -f "$GLOBAL_ON" ] && [ "$TELEGRAM_ACTIVE" -eq 1 ]; } || [ -f "$TG_ON" ]; then
    "$SEND_PY" --channel telegram --event stop --session-id "$SESSION_ID" && SENT=1 || true
fi

if [ "$SENT" -eq 0 ]; then
    if command -v osascript &>/dev/null; then
        osascript -e "display notification \"Task complete\" with title \"Claude Code\""
    fi
fi

exit 0
