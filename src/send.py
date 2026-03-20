#!/usr/bin/env python3
"""
send.py — channel-aware send entrypoint for watcher hooks.

Called by hook scripts:
  send.py --channel imessage --event notification --session-id <id> --message <msg> --tool <tool>
  send.py --channel telegram --event stop --session-id <id>
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Add src dir to path when called from hooks
sys.path.insert(0, str(Path(__file__).parent))

from config import SESSIONS_DIR, load_config
from message_formatter import build_message
from session_manager import session_manager


MAX_CALLBACK_DATA = 64  # Telegram hard limit


def _read_file(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _build_context(session_id: str, event: str, extra: dict) -> dict:
    """Build message context from session data and extra args."""
    session_dir = SESSIONS_DIR / session_id
    ctx = {
        "session_id": session_id,
        "project_name": _read_file(session_dir / "project_name") if session_dir.exists() else "?",
        "project_dir": _read_file(session_dir / "project_dir") if session_dir.exists() else "",
        "task": _read_file(session_dir / "task") if session_dir.exists() else "",
        "mode": _read_file(session_dir / "mode") if session_dir.exists() else "skip",
    }

    if event == "stop" and session_dir.exists():
        started_raw = _read_file(session_dir / "started_at")
        completed_raw = _read_file(session_dir / "completed_at")
        import time
        try:
            started = float(started_raw)
            completed = float(completed_raw) if completed_raw else time.time()
            ctx["duration_seconds"] = completed - started
        except ValueError:
            ctx["duration_seconds"] = 0

    ctx.update(extra)
    return ctx


def send_imessage(text: str, to: str, thread_guid: str = "") -> bool:
    """Send via imsg CLI."""
    cmd = ["imsg", "send", "--to", to, "--message", text]
    if thread_guid:
        cmd += ["--thread-id", thread_guid]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def send_imessage_self(text: str, thread_guid: str = "") -> bool:
    """Send iMessage to self."""
    config = load_config()
    to = config.get("imessage", {}).get("to", "")
    if not to:
        return False
    return send_imessage(text, to, thread_guid)


def telegram_send_sync(
    text: str,
    token: str,
    chat_id: str,
    reply_markup: dict | None = None,
    reply_to: int | None = None,
) -> int | None:
    """Synchronous Telegram send using urllib (no httpx dependency in hooks)."""
    import urllib.request
    import urllib.parse

    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    if reply_to:
        payload["reply_to_message_id"] = reply_to

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode(payload).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                return result["result"]["message_id"]
    except Exception:
        pass
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Watcher send entrypoint")
    parser.add_argument("--channel", required=True, choices=["imessage", "telegram"])
    parser.add_argument("--event", required=True)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--message", default="")
    parser.add_argument("--tool", default="")
    parser.add_argument("--path", default="")
    parser.add_argument("--files", default="")  # comma-separated
    parser.add_argument("--has-issues", action="store_true")
    args = parser.parse_args()

    config = load_config()
    session_id = args.session_id

    extra: dict = {}
    if args.message:
        extra["message"] = args.message
    if args.tool:
        extra["tool"] = args.tool
    if args.path:
        extra["path"] = args.path
    if args.files:
        extra["files_changed"] = [f.strip() for f in args.files.split(",") if f.strip()]
    if args.has_issues:
        extra["has_issues"] = True

    ctx = _build_context(session_id, args.event, extra)
    msg = build_message(args.event, ctx, args.channel)
    text = msg["text"]
    reply_markup = msg.get("reply_markup")

    if args.channel == "imessage":
        imsg_config = config.get("imessage", {})
        if not imsg_config.get("enabled") and not imsg_config.get("to"):
            return 1
        # Get thread GUID for this session
        session_dir = SESSIONS_DIR / session_id
        thread_guid = ""
        if session_dir.exists():
            thread_guid = _read_file(session_dir / "thread_guid_imessage")

        ok = send_imessage_self(text, thread_guid)

        # On first notification, capture the thread GUID if we don't have one
        # (imsg returns the thread ID in its output — we'd need to parse it)
        return 0 if ok else 1

    elif args.channel == "telegram":
        tg_config = config.get("telegram", {})
        token = tg_config.get("token", "")
        chat_id = str(tg_config.get("chat_id", ""))
        if not token or not chat_id:
            return 1

        # Get reply-to message ID for thread continuity
        session_dir = SESSIONS_DIR / session_id
        reply_to = None
        if session_dir.exists():
            tg_guid = _read_file(session_dir / "thread_guid_telegram")
            if tg_guid:
                try:
                    reply_to = int(tg_guid)
                except ValueError:
                    pass

        message_id = telegram_send_sync(text, token, chat_id, reply_markup, reply_to)

        # Store the first message_id as the thread anchor
        if message_id and session_dir.exists():
            tg_guid_file = session_dir / "thread_guid_telegram"
            if not _read_file(tg_guid_file):
                tg_guid_file.write_text(str(message_id) + "\n")

        return 0 if message_id else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
