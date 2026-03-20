#!/usr/bin/env bash
# watch.sh — Claude Code slash command: /watch
# Usage: /watch [channel] on|off|status
# channel: imessage | telegram | both (default: both)
#
# Examples:
#   /watch on              — enable all notifications for this session
#   /watch telegram off    — disable Telegram for this session
#   /watch status          — show current notification state

SESSION_ID="${CLAUDE_SESSION_ID:-}"
SESSION_DIR="$HOME/.claude-watch/sessions/$SESSION_ID"

if [ -z "$SESSION_ID" ] || [ ! -d "$SESSION_DIR" ]; then
    echo "Error: session not found in ~/.claude-watch/sessions/"
    exit 1
fi

ARG1="${1:-status}"
ARG2="${2:-}"

# Normalize: if only one arg and it's on/off/status, treat as action on "both"
if [ -z "$ARG2" ] && [[ "$ARG1" =~ ^(on|off|status)$ ]]; then
    CHANNEL="both"
    ACTION="$ARG1"
else
    CHANNEL="$ARG1"
    ACTION="${ARG2:-status}"
fi

case "$ACTION" in
  on)
    if [ "$CHANNEL" = "imessage" ] || [ "$CHANNEL" = "both" ]; then
        touch "$SESSION_DIR/notify_imessage"
    fi
    if [ "$CHANNEL" = "telegram" ] || [ "$CHANNEL" = "both" ]; then
        touch "$SESSION_DIR/notify_telegram"
    fi
    echo "Notifications enabled: $CHANNEL"
    ;;
  off)
    if [ "$CHANNEL" = "imessage" ] || [ "$CHANNEL" = "both" ]; then
        rm -f "$SESSION_DIR/notify_imessage"
    fi
    if [ "$CHANNEL" = "telegram" ] || [ "$CHANNEL" = "both" ]; then
        rm -f "$SESSION_DIR/notify_telegram"
    fi
    echo "Notifications disabled: $CHANNEL"
    ;;
  status)
    IMSG=$([ -f "$SESSION_DIR/notify_imessage" ] && echo "ON" || echo "off")
    TG=$([ -f "$SESSION_DIR/notify_telegram" ] && echo "ON" || echo "off")
    echo "iMessage: $IMSG  Telegram: $TG"
    ;;
  *)
    echo "Usage: /watch [imessage|telegram|both] on|off|status"
    exit 1
    ;;
esac
