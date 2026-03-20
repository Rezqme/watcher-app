#!/usr/bin/env python3
"""
watcherd — Watcher daemon.

Runs the iMessage and Telegram loops concurrently using asyncio.
Receives messages, routes them to active sessions or spawns new ones.
"""

import asyncio
import json
import logging
import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

# Ensure src directory is on path
sys.path.insert(0, str(Path(__file__).parent))

import httpx

from config import (
    CONVERSATIONS_DIR,
    ENABLED_FLAG,
    LOGS_DIR,
    SESSIONS_DIR,
    get_active_channels,
    is_enabled,
    load_config,
)
from conversation_manager import Conversation, conversation_manager
from message_formatter import build_message
from project_registry import all_projects, resolve_project
from session_manager import Session, session_manager
from wake_word_parser import (
    assess_task,
    choose_mode,
    is_wake_word_intent,
    parse_wake_word,
)

# ─── Logging ───────────────────────────────────────────────────────────────

LOGS_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "watcherd.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("watcherd")


def _config():
    return load_config()


# ─── Channel send helpers ──────────────────────────────────────────────────


async def imessage_send(text: str, thread_guid: str = "") -> bool:
    """Send an iMessage to self."""
    config = _config()
    to = config.get("imessage", {}).get("to", "")
    if not to:
        return False
    cmd = ["imsg", "send", "--to", to, "--message", text]
    if thread_guid:
        cmd += ["--thread-id", thread_guid]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0
    except (FileNotFoundError, OSError):
        log.warning("imsg not found or failed")
        return False


async def telegram_send(
    client: httpx.AsyncClient,
    text: str,
    reply_markup: dict | None = None,
    reply_to: int | None = None,
) -> int | None:
    """Send a Telegram message. Returns message_id on success."""
    config = _config()
    tg = config.get("telegram", {})
    token = tg.get("token", "")
    chat_id = str(tg.get("chat_id", ""))
    if not token or not chat_id:
        return None

    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if reply_to:
        payload["reply_to_message_id"] = reply_to

    try:
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=15,
        )
        result = resp.json()
        if result.get("ok"):
            return result["result"]["message_id"]
        log.warning(f"Telegram sendMessage failed: {result.get('description')}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")
    return None


async def telegram_edit(
    client: httpx.AsyncClient,
    message_id: int,
    new_text: str,
) -> None:
    """Edit a Telegram message (used after button tap)."""
    config = _config()
    tg = config.get("telegram", {})
    token = tg.get("token", "")
    chat_id = str(tg.get("chat_id", ""))
    if not token or not chat_id:
        return
    try:
        await client.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": new_text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
    except Exception as e:
        log.error(f"Telegram edit error: {e}")


async def telegram_answer_callback(client: httpx.AsyncClient, callback_id: str) -> None:
    """Acknowledge a callback query to remove the loading spinner."""
    config = _config()
    token = _config().get("telegram", {}).get("token", "")
    if not token:
        return
    try:
        await client.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            json={"callback_query_id": callback_id},
            timeout=5,
        )
    except Exception:
        pass


async def send_message_channel(
    client: httpx.AsyncClient | None,
    event: str,
    ctx: dict,
    channel: str,
    thread_guid: str = "",
    reply_to: int | None = None,
) -> int | None:
    """Build and send a message on the given channel."""
    msg = build_message(event, ctx, channel)
    text = msg["text"]
    reply_markup = msg.get("reply_markup")

    if channel == "imessage":
        await imessage_send(text, thread_guid)
        return None
    elif channel == "telegram" and client:
        return await telegram_send(client, text, reply_markup, reply_to)
    return None


def _get_session_channels(session: Session) -> list[str]:
    """Return active notification channels for a session."""
    active = get_active_channels()
    channels = []
    if "imessage" in active and session.notify_imessage:
        channels.append("imessage")
    if "telegram" in active and session.notify_telegram:
        channels.append("telegram")
    # If global is on and no session flags set, use both configured active channels
    if not channels and is_enabled():
        channels = active
    return channels


# ─── Session handling ──────────────────────────────────────────────────────


async def resume_session(
    client: httpx.AsyncClient,
    session: Session,
    message: str,
    channel: str,
) -> None:
    """Resume a session with a message from the user."""
    log.info(f"Resuming session {session.project_name} with: {message[:50]}")
    ok = session_manager.resume(session, message)
    if not ok:
        ctx = {"message": f"Session not found. Use 'watcher {session.project_name}: <task>' to start fresh."}
        await send_message_channel(client, "error", ctx, channel)


async def spawn_session(
    client: httpx.AsyncClient,
    project_name: str,
    project_dir: str,
    task: str,
    mode: str,
    channel: str,
) -> Session | None:
    """Spawn a new Claude Code session."""
    log.info(f"Spawning session: {project_name} / {task[:50]} / {mode}")

    config = _config()
    default_channels = config.get("default_channels", [channel])
    channels = default_channels if channel in default_channels else [channel]

    session = session_manager.spawn(
        project_dir=project_dir,
        task=task,
        mode=mode,
        channels=channels,
        project_name=project_name,
    )
    if not session:
        ctx = {"message": f"Failed to spawn session for {project_name}."}
        await send_message_channel(client, "error", ctx, channel)
        return None

    ctx = {
        "session_id": session.session_id,
        "project_name": project_name,
        "task": task,
        "mode": mode,
    }
    msg_id = await send_message_channel(client, "start", ctx, channel)

    # Store Telegram message_id as thread anchor
    if msg_id and channel == "telegram":
        session_manager.set_thread_guid(session.session_id, "telegram", str(msg_id))

    return session


# ─── Wake word handling ────────────────────────────────────────────────────


async def handle_wake_word(
    client: httpx.AsyncClient,
    text: str,
    channel: str,
    thread_id: str,
    tg_message_id: int | None = None,
) -> None:
    """Handle a wake word message."""
    parsed = parse_wake_word(text)
    msg_type = parsed.get("type")

    if msg_type == "management":
        await handle_management_command(client, parsed, channel, thread_id, tg_message_id)
        return

    if msg_type == "bare":
        # Show main menu
        ctx: dict = {}
        await send_message_channel(client, "menu", ctx, channel, reply_to=tg_message_id)
        return

    # Spawn request
    project_token = parsed.get("project")
    task = parsed.get("task", "")

    # No project specified — check for single default project
    if not project_token:
        projects = all_projects()
        if len(projects) == 1:
            project_token = list(projects.keys())[0]
        elif not projects:
            ctx = {"message": "No projects registered. Run 'watcher install' to add projects."}
            await send_message_channel(client, "error", ctx, channel, reply_to=tg_message_id)
            return
        else:
            # Ask which project
            conv = conversation_manager.create(
                thread_id=thread_id,
                channel=channel,
                state="awaiting_project",
                partial_task=task or "",
            )
            ctx = {"task": task or "", "projects": projects, "session_id": conv.thread_id}
            await send_message_channel(client, "ask_project", ctx, channel, reply_to=tg_message_id)
            return

    # Resolve project
    match, needs_confirm = resolve_project(project_token)

    if match is None:
        projects = all_projects()
        ctx = {
            "message": f"Unknown project '{project_token}'.",
            "projects": projects,
        }
        await send_message_channel(client, "ask_project", ctx, channel, reply_to=tg_message_id)
        return

    proj_name, proj_dir = match

    if needs_confirm:
        conv = conversation_manager.create(
            thread_id=thread_id,
            channel=channel,
            state="awaiting_confirm",
            partial_task=task or "",
            project_name=proj_name,
            project_dir=proj_dir,
        )
        ctx = {
            "matched_project": proj_name,
            "input_name": project_token,
            "session_id": conv.thread_id,
        }
        await send_message_channel(client, "fuzzy_confirm", ctx, channel, reply_to=tg_message_id)
        return

    # No task? Ask for one
    if not task:
        conv = conversation_manager.create(
            thread_id=thread_id,
            channel=channel,
            state="awaiting_task",
            project_name=proj_name,
            project_dir=proj_dir,
        )
        ctx = {"project_name": proj_name, "session_id": conv.thread_id}
        await send_message_channel(client, "ask_task", ctx, channel, reply_to=tg_message_id)
        return

    # Assess task
    assessment = assess_task(task)
    mode = choose_mode(assessment)

    if assessment in ("dangerous", "vague"):
        conv = conversation_manager.create(
            thread_id=thread_id,
            channel=channel,
            state="awaiting_confirm",
            partial_task=task,
            project_name=proj_name,
            project_dir=proj_dir,
            mode=mode,
        )
        ctx = {
            "project_name": proj_name,
            "task": task,
            "mode": mode,
            "session_id": conv.thread_id,
        }
        await send_message_channel(client, "confirm_dangerous", ctx, channel, reply_to=tg_message_id)
        return

    # Clear task — spawn directly
    await spawn_session(client, proj_name, proj_dir, task, mode, channel)


async def handle_management_command(
    client: httpx.AsyncClient,
    parsed: dict,
    channel: str,
    thread_id: str,
    tg_message_id: int | None = None,
) -> None:
    """Handle a management command like 'watcher list', 'watcher stop', etc."""
    command = parsed.get("command", "")
    args = parsed.get("args", [])

    if command in ("list", "status", "sessions"):
        sessions = session_manager.active() + session_manager.parked()
        ctx = {
            "sessions": [
                {
                    "session_id": s.session_id,
                    "project_name": s.project_name,
                    "status": s.status,
                    "task": s.task,
                    "idle_minutes": s.idle_minutes,
                    "channels": _channel_label(s),
                }
                for s in sessions
            ]
        }
        await send_message_channel(client, "session_list", ctx, channel, reply_to=tg_message_id)

    elif command == "recent":
        sessions = session_manager.recent(24)
        ctx = {
            "sessions": [
                {
                    "session_id": s.session_id,
                    "project_name": s.project_name,
                    "status": s.status,
                    "task": s.task,
                }
                for s in sessions
            ]
        }
        await send_message_channel(client, "recent", ctx, channel, reply_to=tg_message_id)

    elif command == "kill":
        name = args[0] if args else ""
        if not name:
            ctx = {"message": "Usage: watcher kill <project-name>"}
            await send_message_channel(client, "error", ctx, channel, reply_to=tg_message_id)
            return
        sessions = session_manager.find_by_name(name)
        if not sessions:
            ctx = {"message": f"No session found for '{name}'"}
            await send_message_channel(client, "error", ctx, channel, reply_to=tg_message_id)
            return
        for s in sessions:
            session_manager.kill(s.session_id)
        ctx = {"message": f"💀 Killed: {name}"}
        await send_message_channel(client, "error", ctx, channel, reply_to=tg_message_id)

    elif command == "clean":
        count = session_manager.clean()
        ctx = {"message": f"✓ Cleaned {count} completed session(s)."}
        await send_message_channel(client, "error", ctx, channel, reply_to=tg_message_id)

    elif command == "stop":
        active = session_manager.active()
        active_count = len(active)
        if active_count > 0:
            conv = conversation_manager.create(
                thread_id=thread_id,
                channel=channel,
                state="awaiting_confirm",
                mode="stop",
            )
            ctx = {
                "active_count": active_count,
                "active_names": [s.project_name for s in active],
                "session_id": conv.thread_id,
            }
            await send_message_channel(client, "stop_confirm", ctx, channel, reply_to=tg_message_id)
        else:
            ENABLED_FLAG.unlink(missing_ok=True)
            await send_message_channel(client, "stopped", {}, channel, reply_to=tg_message_id)

    elif command == "notify":
        if args and args[0].lower() in ("on", "off"):
            # Direct: "watcher notify on/off"
            on = args[0].lower() == "on"
            sessions_list = session_manager.active() + session_manager.parked()
            if len(sessions_list) == 1:
                s = sessions_list[0]
                session_manager.set_channel(s.session_id, channel, on)
                status = "ON" if on else "OFF"
                ctx = {"message": f"🔔 {s.project_name} — {channel} {status}"}
                await send_message_channel(client, "error", ctx, channel, reply_to=tg_message_id)
            else:
                ctx = {
                    "sessions": [
                        {
                            "session_id": s.session_id,
                            "project_name": s.project_name,
                            "idle_minutes": s.idle_minutes,
                            "channels": _channel_label(s),
                        }
                        for s in sessions_list
                    ]
                }
                await send_message_channel(client, "notify_list", ctx, channel, reply_to=tg_message_id)
        else:
            sessions_list = session_manager.active() + session_manager.parked()
            ctx = {
                "sessions": [
                    {
                        "session_id": s.session_id,
                        "project_name": s.project_name,
                        "idle_minutes": s.idle_minutes,
                        "channels": _channel_label(s),
                    }
                    for s in sessions_list
                ]
            }
            await send_message_channel(client, "notify_list", ctx, channel, reply_to=tg_message_id)

    elif command == "history":
        project = args[0] if args else ""
        await _send_history(client, project, channel, tg_message_id)

    elif command == "help":
        help_text = (
            "👋 <b>Watcher commands</b>\n\n"
            "watcher &lt;project&gt;: &lt;task&gt; — start session\n"
            "watcher list — show active sessions\n"
            "watcher recent — last 24h sessions\n"
            "watcher notify — toggle notifications\n"
            "watcher kill &lt;name&gt; — kill session\n"
            "watcher clean — clean completed\n"
            "watcher stop — stop daemon\n"
            "watcher history [project] — history\n"
            "watcher help — this message"
        )
        if channel == "imessage":
            help_text = help_text.replace("<b>", "").replace("</b>", "").replace("&lt;", "<").replace("&gt;", ">")
        await send_message_channel(client, "error", {"message": help_text}, channel, reply_to=tg_message_id)


async def _send_history(
    client: httpx.AsyncClient,
    project_name: str,
    channel: str,
    tg_message_id: int | None,
) -> None:
    """Send session history."""
    from config import ARCHIVE_DIR

    entries = []
    if ARCHIVE_DIR.exists():
        for d in sorted(ARCHIVE_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not d.is_dir():
                continue
            pname = (d / "project_name").read_text().strip() if (d / "project_name").exists() else ""
            if project_name and pname.lower() != project_name.lower():
                continue

            started_raw = (d / "started_at").read_text().strip() if (d / "started_at").exists() else "0"
            completed_raw = (d / "completed_at").read_text().strip() if (d / "completed_at").exists() else ""
            try:
                started = float(started_raw)
                completed = float(completed_raw) if completed_raw else started
                duration = completed - started
            except ValueError:
                duration = 0

            entries.append({
                "project_name": pname,
                "task": (d / "task").read_text().strip() if (d / "task").exists() else "",
                "outcome": (d / "outcome").read_text().strip() if (d / "outcome").exists() else "completed",
                "duration_seconds": duration,
                "files_changed": [],
            })
            if len(entries) >= 20:
                break

    ctx = {"entries": entries, "project_name": project_name}
    await send_message_channel(client, "history", ctx, channel, reply_to=tg_message_id)


# ─── Conversation continuation ─────────────────────────────────────────────


async def continue_conversation(
    client: httpx.AsyncClient,
    conv: Conversation,
    text: str,
    channel: str,
    tg_message_id: int | None = None,
) -> None:
    """Continue an in-progress watcher conversation."""
    state = conv.state

    if state == "awaiting_project":
        # User replied with project name or number
        match, needs_confirm = resolve_project(text.strip())
        if match is None:
            ctx = {"message": f"Unknown project '{text}'. Try again."}
            await send_message_channel(client, "error", ctx, channel, reply_to=tg_message_id)
            return

        proj_name, proj_dir = match

        if not conv.partial_task:
            # Now ask for task
            conv.project_name = proj_name
            conv.project_dir = proj_dir
            conv.state = "awaiting_task"
            conversation_manager.update(conv)
            ctx = {"project_name": proj_name, "session_id": conv.thread_id}
            await send_message_channel(client, "ask_task", ctx, channel, reply_to=tg_message_id)
            return

        task = conv.partial_task
        assessment = assess_task(task)
        mode = choose_mode(assessment)
        conversation_manager.delete(conv)
        await spawn_session(client, proj_name, proj_dir, task, mode, channel)

    elif state == "awaiting_task":
        task = text.strip()
        assessment = assess_task(task)
        mode = choose_mode(assessment)

        if assessment in ("dangerous", "vague"):
            conv.partial_task = task
            conv.mode = mode
            conv.state = "awaiting_confirm"
            conversation_manager.update(conv)
            ctx = {
                "project_name": conv.project_name,
                "task": task,
                "mode": mode,
                "session_id": conv.thread_id,
            }
            await send_message_channel(client, "confirm_dangerous", ctx, channel, reply_to=tg_message_id)
            return

        conversation_manager.delete(conv)
        await spawn_session(client, conv.project_name, conv.project_dir, task, mode, channel)

    elif state == "awaiting_confirm":
        affirmative = text.strip().lower() in ("yes", "y", "ok", "sure", "go", "proceed", "1")

        if conv.mode == "stop":
            # Confirming daemon stop
            conversation_manager.delete(conv)
            if affirmative:
                ENABLED_FLAG.unlink(missing_ok=True)
                await send_message_channel(client, "stopped", {}, channel)
            else:
                ctx = {"message": "❌ Cancelled."}
                await send_message_channel(client, "error", ctx, channel, reply_to=tg_message_id)
            return

        if not affirmative:
            conversation_manager.delete(conv)
            ctx = {"message": "❌ Cancelled."}
            await send_message_channel(client, "error", ctx, channel, reply_to=tg_message_id)
            return

        task = conv.partial_task
        mode = conv.mode or choose_mode(assess_task(task))
        conversation_manager.delete(conv)
        await spawn_session(client, conv.project_name, conv.project_dir, task, mode, channel)

    elif state == "awaiting_direction":
        # User typed a direction in response to a "✏️ Direction" button tap
        session = session_manager.find_by_id(conv.session_id)
        conversation_manager.delete(conv)
        if session:
            await resume_session(client, session, text, channel)
        else:
            ctx = {"message": "❓ Session not found."}
            await send_message_channel(client, "error", ctx, channel, reply_to=tg_message_id)


# ─── Telegram callback handling ────────────────────────────────────────────


async def handle_callback(client: httpx.AsyncClient, update: dict) -> None:
    """Handle Telegram inline keyboard button tap."""
    cq = update["callback_query"]
    callback_id = cq["id"]
    data = cq.get("data", "")
    message = cq.get("message", {})
    message_id = message.get("message_id")

    await telegram_answer_callback(client, callback_id)

    if ":" not in data:
        log.warning(f"Unexpected callback_data: {data}")
        return

    action, ref = data.split(":", 1)

    # Session lookup (ref may be a short ref or full UUID)
    session = session_manager.find_by_id(ref)
    channel = "telegram"

    if action in ("yes", "allow", "approve", "fuzzy_yes"):
        if session:
            if message_id:
                await telegram_edit(client, message_id, f"✅ Replied: yes")
            await resume_session(client, session, "yes", channel)
        else:
            # May be a conversation confirm
            conv = _find_conv_by_thread(str(message_id), channel) or _find_conv_by_session(ref)
            if conv:
                if message_id:
                    await telegram_edit(client, message_id, "✅ Confirmed")
                await continue_conversation(client, conv, "yes", channel)
            else:
                await telegram_send(client, "❓ Session not found.")

    elif action in ("no", "deny", "reject", "fuzzy_no"):
        if message_id:
            await telegram_edit(client, message_id, "❌ Cancelled")
        if session:
            await resume_session(client, session, "no", channel)
        else:
            conv = _find_conv_by_session(ref)
            if conv:
                conversation_manager.delete(conv)

    elif action == "direction":
        # Prompt user to type a direction
        prompt_msg_id = await telegram_send(
            client,
            "✏️ Type your direction and reply to this message.",
            reply_to=message_id,
        )
        if prompt_msg_id and session:
            conversation_manager.create_direction_prompt(str(prompt_msg_id), session.session_id)

    elif action == "notify_on":
        if session:
            session_manager.set_channel(session.session_id, "telegram", True)
            await telegram_send(client, f"🔔 Telegram notifications on for <b>{session.project_name}</b>.")

    elif action == "notify_off":
        if session:
            session_manager.set_channel(session.session_id, "telegram", False)
            await telegram_send(client, f"🔕 Telegram notifications off for <b>{session.project_name}</b>.")

    elif action == "resume":
        if session and session.status == "parked":
            if message_id:
                await telegram_edit(client, message_id, f"⏸ Resuming <b>{session.project_name}</b>…")
            await resume_session(client, session, "continue", channel)

    elif action == "kill":
        # Require second confirmation
        name = session.project_name if session else ref
        await telegram_send(
            client,
            f"⚠️ Kill <b>{name}</b>?",
            reply_markup={
                "inline_keyboard": [[
                    {"text": "💀 Yes, kill it", "callback_data": f"kill_confirm:{ref}"},
                    {"text": "Cancel", "callback_data": f"kill_cancel:{ref}"},
                ]]
            },
        )

    elif action == "kill_confirm":
        if session:
            session_manager.kill(session.session_id)
            if message_id:
                await telegram_edit(client, message_id, f"💀 <b>{session.project_name}</b> killed.")
        else:
            await telegram_send(client, "❓ Session not found.")

    elif action == "kill_cancel":
        if message_id:
            await telegram_edit(client, message_id, "✓ Kill cancelled.")

    elif action == "followup":
        if session:
            await telegram_send(
                client,
                f"What should Claude do next in <b>{session.project_name}</b>?\nReply to this message.",
                reply_to=message_id,
            )

    elif action == "stop_confirm":
        ENABLED_FLAG.unlink(missing_ok=True)
        if message_id:
            await telegram_edit(client, message_id, "✓ Watcher stopped.\n\nSessions still running locally.")

    elif action == "stop_cancel":
        if message_id:
            await telegram_edit(client, message_id, "✓ Stop cancelled. Watcher still running.")

    elif action == "menu":
        cmd = ref
        if cmd == "list":
            sessions = session_manager.active() + session_manager.parked()
            ctx = {
                "sessions": [
                    {
                        "session_id": s.session_id,
                        "project_name": s.project_name,
                        "status": s.status,
                        "task": s.task,
                        "idle_minutes": s.idle_minutes,
                        "channels": _channel_label(s),
                    }
                    for s in sessions
                ]
            }
            await send_message_channel(client, "session_list", ctx, channel)
        elif cmd == "stop":
            active = session_manager.active()
            if active:
                conv = conversation_manager.create("menu_stop", channel, "awaiting_confirm", mode="stop")
                ctx = {
                    "active_count": len(active),
                    "active_names": [s.project_name for s in active],
                    "session_id": conv.thread_id,
                }
                await send_message_channel(client, "stop_confirm", ctx, channel)
            else:
                ENABLED_FLAG.unlink(missing_ok=True)
                await send_message_channel(client, "stopped", {}, channel)
        elif cmd in ("new", "resume", "kill"):
            await telegram_send(
                client,
                f"Use: watcher &lt;project&gt;: &lt;task&gt; to start a new session.",
            )


def _find_conv_by_thread(thread_id: str, channel: str) -> Conversation | None:
    return conversation_manager.find(thread_id, channel)


def _find_conv_by_session(session_id: str) -> Conversation | None:
    """Find a conversation by session_id (for direction prompts)."""
    if not CONVERSATIONS_DIR.exists():
        return None
    for d in CONVERSATIONS_DIR.iterdir():
        if d.is_dir():
            meta = d / "meta.json"
            if meta.exists():
                try:
                    with open(meta) as f:
                        data = json.load(f)
                    if data.get("session_id") == session_id:
                        from conversation_manager import Conversation
                        return Conversation(**data)
                except Exception:
                    pass
    return None


# ─── Message routing ────────────────────────────────────────────────────────


async def handle_message(
    client: httpx.AsyncClient,
    msg: dict,
    channel: str,
) -> None:
    """Route an incoming message to the appropriate handler."""
    text = msg.get("text", "").strip()
    thread_id = _get_thread_id(msg, channel)
    tg_message_id = msg.get("message_id") if channel == "telegram" else None

    if not text and channel != "telegram":
        return

    # 1. Thread reply → active Claude session
    if thread_id:
        session = session_manager.find_by_thread(thread_id, channel)
        if session:
            await resume_session(client, session, text, channel)
            return

    # 2. Thread reply → pending watcher conversation
    if thread_id:
        conv = conversation_manager.find(thread_id, channel)
        if conv:
            await continue_conversation(client, conv, text, channel, tg_message_id)
            return

    # 3. Wake word → new message
    if is_wake_word_intent(text):
        await handle_wake_word(client, text, channel, thread_id or "", tg_message_id)
        return


def _get_thread_id(msg: dict, channel: str) -> str:
    """Extract thread routing ID from message."""
    if channel == "imessage":
        return msg.get("thread_originator_guid", "")
    elif channel == "telegram":
        reply_to = msg.get("reply_to_message", {})
        if reply_to:
            return str(reply_to.get("message_id", ""))
    return ""


def _channel_label(session: Session) -> str:
    parts = []
    if session.notify_imessage:
        parts.append("iMsg")
    if session.notify_telegram:
        parts.append("TG")
    return " + ".join(parts) if parts else "muted"


# ─── Main loops ─────────────────────────────────────────────────────────────


async def imessage_loop(client: httpx.AsyncClient) -> None:
    """Watch iMessage for incoming messages."""
    log.info("Starting iMessage loop")
    while True:
        try:
            proc = await asyncio.create_subprocess_exec(
                "imsg", "watch", "--chat-id", "self", "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            async for line in proc.stdout:  # type: ignore[union-attr]
                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Only process messages from self (we sent them from our device)
                if msg.get("is_from_me"):
                    await handle_message(client, msg, "imessage")
            await proc.wait()
        except FileNotFoundError:
            log.error("imsg not found — iMessage loop disabled")
            return
        except Exception as e:
            log.error(f"iMessage loop error: {e}")
        await asyncio.sleep(5)


async def telegram_loop(client: httpx.AsyncClient) -> None:
    """Long-poll Telegram getUpdates."""
    log.info("Starting Telegram loop")
    offset = 0
    config = _config()
    token = config.get("telegram", {}).get("token", "")
    if not token:
        log.error("No Telegram token — Telegram loop disabled")
        return

    while True:
        try:
            resp = await client.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=35,
            )
            updates = resp.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                await handle_telegram_update(client, update)
        except Exception as e:
            log.error(f"Telegram poll error: {e}")
            await asyncio.sleep(5)


async def handle_telegram_update(client: httpx.AsyncClient, update: dict) -> None:
    """Dispatch a single Telegram update."""
    # Button tap
    if update.get("callback_query"):
        await handle_callback(client, update)
        return
    # Text message
    if msg := update.get("message"):
        if msg.get("text"):
            await handle_message(client, msg, "telegram")


async def reaper_loop() -> None:
    """Periodically reap timed-out sessions and conversations."""
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        try:
            session_manager.reap()
            conversation_manager.reap()
        except Exception as e:
            log.error(f"Reaper error: {e}")


async def heartbeat_loop() -> None:
    """Write periodic heartbeat for multi-Mac detection."""
    from config import INSTANCE_PATH
    while True:
        try:
            with open(INSTANCE_PATH, "w") as f:
                json.dump({
                    "hostname": socket.gethostname(),
                    "pid": os.getpid(),
                    "ts": time.time(),
                    "primary": True,
                }, f)
        except OSError:
            pass
        await asyncio.sleep(30)


async def main() -> None:
    config = _config()
    active_channels = get_active_channels()

    log.info(f"watcherd starting — channels: {active_channels}")

    async with httpx.AsyncClient() as client:
        tasks: list[asyncio.Task] = [
            asyncio.create_task(reaper_loop()),
            asyncio.create_task(heartbeat_loop()),
        ]

        if "imessage" in active_channels and config.get("imessage", {}).get("enabled"):
            tasks.append(asyncio.create_task(imessage_loop(client)))

        if "telegram" in active_channels and config.get("telegram", {}).get("enabled"):
            tasks.append(asyncio.create_task(telegram_loop(client)))

        if len(tasks) == 2:
            log.warning("No channels configured — daemon running in reaper-only mode")

        await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
