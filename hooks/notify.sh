#!/usr/bin/env bash
# notify.sh — Claude Code Notification hook
# Sends notification via enabled channels, falling back to local macOS notification.
set -euo pipefail

PAYLOAD=$(cat)
SESSION_ID=$(echo "$PAYLOAD" | jq -r '.session_id // empty')
MESSAGE=$(echo "$PAYLOAD"   | jq -r '.message   // "Claude needs your input"')
TOOL=$(echo "$PAYLOAD"      | jq -r '.tool_name // empty')

SESSION_DIR="$HOME/.claude-watch/sessions/$SESSION_ID"
if [ -d "$SESSION_DIR" ]; then
    date +%s > "$SESSION_DIR/last_activity"
fi

GLOBAL_ON="$HOME/.claude-watch/enabled"
ACTIVE_CHANNELS="$HOME/.claude-watch/active_channels"
IMSG_ON="$SESSION_DIR/notify_imessage"
TG_ON="$SESSION_DIR/notify_telegram"

SEND_PY="$HOME/.claude-watch/send.py"

SENT=0

# Determine which channels are active globally
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
    # Default: both active if global flag is on
    IMESSAGE_ACTIVE=1
    TELEGRAM_ACTIVE=1
fi

# iMessage: (global on AND imessage active) OR session opted in
if { [ -f "$GLOBAL_ON" ] && [ "$IMESSAGE_ACTIVE" -eq 1 ]; } || [ -f "$IMSG_ON" ]; then
    "$SEND_PY" \
        --channel imessage \
        --event notification \
        --session-id "$SESSION_ID" \
        --message "$MESSAGE" \
        --tool "$TOOL" && SENT=1 || true
fi

# Telegram: (global on AND telegram active) OR session opted in
if { [ -f "$GLOBAL_ON" ] && [ "$TELEGRAM_ACTIVE" -eq 1 ]; } || [ -f "$TG_ON" ]; then
    "$SEND_PY" \
        --channel telegram \
        --event notification \
        --session-id "$SESSION_ID" \
        --message "$MESSAGE" \
        --tool "$TOOL" && SENT=1 || true
fi

# Neither — local macOS notification fallback
if [ "$SENT" -eq 0 ]; then
    if command -v osascript &>/dev/null; then
        osascript -e "display notification \"$MESSAGE\" with title \"Claude Code\""
    fi
fi

exit 0
