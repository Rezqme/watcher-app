"""Message formatter — channel-aware message building for iMessage and Telegram."""

from typing import Any

# iMessage / Watch display constants
MAX_BANNER = 40
MAX_LINE = 35
MAX_LINES = 8
MAX_PATH = 28
MAX_TASK_ECHO = 55
MAX_FILES_IMESSAGE = 3
MAX_FILES_TELEGRAM = 8


def _truncate_path(path: str, max_len: int = MAX_PATH) -> str:
    """Truncate a file path to fit in max_len chars."""
    if len(path) <= max_len:
        return path
    return "…/" + path[-(max_len - 2):]


def _truncate_line(line: str, max_len: int = MAX_LINE) -> str:
    if len(line) <= max_len:
        return line
    return line[: max_len - 1] + "…"


def _format_duration(seconds: float) -> str:
    secs = int(seconds)
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    return f"{m}m {s}s"


# ─── iMessage builders ─────────────────────────────────────────────────────


def build_imessage(event: str, ctx: dict) -> str:
    """Build a plain-text iMessage / Watch message."""
    if event == "notification":
        return _imsg_notification(ctx)
    elif event == "start":
        return _imsg_start(ctx)
    elif event == "stop":
        return _imsg_stop(ctx)
    elif event == "parked":
        return _imsg_parked(ctx)
    elif event == "expired":
        return _imsg_expired(ctx)
    elif event == "confirm_dangerous":
        return _imsg_confirm_dangerous(ctx)
    elif event == "session_list":
        return _imsg_session_list(ctx)
    elif event == "ask_project":
        return _imsg_ask_project(ctx)
    elif event == "ask_task":
        return _imsg_ask_task(ctx)
    elif event == "notify_list":
        return _imsg_notify_list(ctx)
    elif event == "error":
        return _imsg_error(ctx)
    elif event == "stopped":
        return _imsg_stopped(ctx)
    elif event == "stop_confirm":
        return _imsg_stop_confirm(ctx)
    elif event == "fuzzy_confirm":
        return _imsg_fuzzy_confirm(ctx)
    elif event == "history":
        return _imsg_history(ctx)
    elif event == "recent":
        return _imsg_recent(ctx)
    return f"❓ Unknown event: {event}"


def _imsg_notification(ctx: dict) -> str:
    project = ctx.get("project_name", "?")
    message = ctx.get("message", "Needs input")
    tool = ctx.get("tool", "")

    banner = _truncate_line(f"🔔 {project} · Needs input", MAX_BANNER)
    lines = [banner, ""]

    if tool:
        lines.append(_truncate_line(f"Tool: {tool}"))

    path = ctx.get("path", "")
    if path:
        lines.append(_truncate_line(f"Editing: {_truncate_path(path)}"))

    msg_lines = message.split("\n")
    for ml in msg_lines[:3]:
        lines.append(_truncate_line(f"↳ {ml}"))

    lines.append("")
    lines.append("Reply yes / no / or give direction.")
    return "\n".join(lines[:MAX_LINES])


def _imsg_start(ctx: dict) -> str:
    project = ctx.get("project_name", "?")
    task = ctx.get("task", "")
    mode = ctx.get("mode", "skip")

    mode_suffix = " (plan mode)" if mode == "plan" else ""
    banner = _truncate_line(f"🚀 {project} · Started{mode_suffix}", MAX_BANNER)
    lines = [banner, ""]

    task_echo = task[:MAX_TASK_ECHO]
    if len(task) > MAX_TASK_ECHO:
        task_echo += "…"
    for tl in task_echo.split("\n")[:3]:
        lines.append(_truncate_line(tl))

    lines.append("")
    if mode == "plan":
        lines.append("Claude will send a plan before changes.")
    else:
        lines.append("I'll let you know if I need anything.")
    return "\n".join(lines[:MAX_LINES])


def _imsg_stop(ctx: dict) -> str:
    project = ctx.get("project_name", "?")
    duration = _format_duration(ctx.get("duration_seconds", 0))
    files = ctx.get("files_changed", [])
    issues = ctx.get("has_issues", False)

    if issues:
        banner = _truncate_line(f"⚠️ {project} · Finished with issues ({duration})", MAX_BANNER)
    else:
        banner = _truncate_line(f"✅ {project} · Done ({duration})", MAX_BANNER)

    lines = [banner, ""]

    shown = files[:MAX_FILES_IMESSAGE]
    for f in shown:
        lines.append(_truncate_line(f"· {_truncate_path(f)}"))

    remaining = len(files) - MAX_FILES_IMESSAGE
    if remaining > 0:
        lines.append(f"· …and {remaining} more")

    lines.append("")
    if issues:
        lines.append("Reply to continue or ignore.")
    else:
        lines.append("Reply to ask a follow-up.")
    return "\n".join(lines[:MAX_LINES])


def _imsg_parked(ctx: dict) -> str:
    project = ctx.get("project_name", "?")
    waited = ctx.get("waited_minutes", 0)
    remaining = ctx.get("remaining_minutes", 0)

    banner = _truncate_line(f"⏸ {project} · Waiting {waited}min", MAX_BANNER)
    lines = [
        banner, "",
        "Still waiting on input from earlier.",
        _truncate_line(f"Times out in {remaining}min."),
        "",
        "Reply to resume or let it expire.",
    ]
    return "\n".join(lines[:MAX_LINES])


def _imsg_expired(ctx: dict) -> str:
    project = ctx.get("project_name", "?")
    lines = [
        f"🗑 {project} · Expired", "",
        "No reply — session cleaned up.", "",
        _truncate_line(f"Reply 'watcher {project}: <task>' to start fresh."),
    ]
    return "\n".join(lines)


def _imsg_confirm_dangerous(ctx: dict) -> str:
    project = ctx.get("project_name", "?")
    task = ctx.get("task", "")
    mode = ctx.get("mode", "plan")

    lines = [
        "⚠️ Confirm before starting", "",
        _truncate_line(f"Project: {project}"),
        _truncate_line(f"Task: {task[:30]}"),
        f"Mode: {mode}",
        "",
        "Claude will show a plan first.",
        "Reply yes / no.",
    ]
    return "\n".join(lines[:MAX_LINES])


def _imsg_session_list(ctx: dict) -> str:
    sessions = ctx.get("sessions", [])
    lines = ["📋 Sessions", ""]
    for s in sessions[:5]:
        status_emoji = "🟢" if s.get("status") == "active" else "🟡"
        name = s.get("project_name", "?")
        mins = s.get("idle_minutes", 0)
        lines.append(_truncate_line(f"{status_emoji} {name} ({int(mins)}min)"))
    lines.append("")
    lines.append("Reply 'watcher <name>: <task>' for new.")
    return "\n".join(lines[:MAX_LINES])


def _imsg_ask_project(ctx: dict) -> str:
    task = ctx.get("task", "")
    projects = ctx.get("projects", {})
    lines = ["📂 Which project?", ""]
    for i, name in enumerate(list(projects.keys())[:5], 1):
        lines.append(f"  {i} · {name}")
    lines.append("")
    lines.append(_truncate_line(f"Task: {task[:30]}"))
    lines.append("Reply with number or name.")
    return "\n".join(lines[:MAX_LINES])


def _imsg_ask_task(ctx: dict) -> str:
    project = ctx.get("project_name", "?")
    lines = [
        f"❓ {project}", "",
        "What should Claude do?",
        "Reply with a task description.",
    ]
    return "\n".join(lines)


def _imsg_notify_list(ctx: dict) -> str:
    sessions = ctx.get("sessions", [])
    lines = ["🔔 Which session?", ""]
    for i, s in enumerate(sessions[:5], 1):
        name = s.get("project_name", "?")
        channels = s.get("channels", "muted")
        lines.append(_truncate_line(f"{i} · {name} — {channels}"))
    lines.append("")
    lines.append("Reply: <n> imessage|telegram|both on|off")
    return "\n".join(lines[:MAX_LINES])


def _imsg_error(ctx: dict) -> str:
    msg = ctx.get("message", "Unknown error")
    return f"❓ {_truncate_line(msg)}"


def _imsg_stopped(ctx: dict) -> str:
    return "✓ Watcher stopped.\n\nLocal notifications re-enabled."


def _imsg_stop_confirm(ctx: dict) -> str:
    active = ctx.get("active_count", 0)
    lines = ["⚠️ Stop watcher?", ""]
    if active:
        lines.append(f"{active} session(s) still running.")
        lines.append("They'll keep running locally")
        lines.append("but you won't get updates.")
    lines.append("")
    lines.append("Reply yes to stop.")
    return "\n".join(lines[:MAX_LINES])


def _imsg_fuzzy_confirm(ctx: dict) -> str:
    matched = ctx.get("matched_project", "?")
    input_name = ctx.get("input_name", "?")
    lines = [
        f"❓ Did you mean {matched}?",
        f"(matched from '{input_name}')",
        "",
        "Reply yes to confirm or give correct name.",
    ]
    return "\n".join(lines)


def _imsg_history(ctx: dict) -> str:
    entries = ctx.get("entries", [])
    project = ctx.get("project_name", "")
    header = f"📋 History{' · ' + project if project else ''}"
    lines = [header, ""]
    for e in entries[:5]:
        outcome_emoji = {"completed": "✓", "killed": "💀", "expired": "🗑"}.get(
            e.get("outcome", ""), "⚠️"
        )
        dur = _format_duration(e.get("duration_seconds", 0))
        task = e.get("task", "")[:25]
        lines.append(f"{outcome_emoji} {dur}  {task}")
    return "\n".join(lines[:MAX_LINES])


def _imsg_recent(ctx: dict) -> str:
    sessions = ctx.get("sessions", [])
    lines = ["📋 Recent sessions", ""]
    for i, s in enumerate(sessions[:5], 1):
        status = s.get("status", "?")
        emoji = {"active": "🟢", "parked": "🟡", "completed": "✓", "expired": "🗑"}.get(
            status, "❓"
        )
        name = s.get("project_name", "?")
        task = s.get("task", "")[:20]
        lines.append(f"{emoji} {name}")
        lines.append(f"   {task}")
    lines.append("")
    lines.append("Reply number to resume parked,")
    lines.append("or 'watcher <name>: <task>' for new.")
    return "\n".join(lines[:MAX_LINES])


# ─── Telegram builders ─────────────────────────────────────────────────────


def build_telegram(event: str, ctx: dict) -> dict:
    """Build a Telegram message dict with optional reply_markup."""
    builders = {
        "notification": _tg_notification,
        "start": _tg_start,
        "stop": _tg_stop,
        "parked": _tg_parked,
        "expired": _tg_expired,
        "confirm_dangerous": _tg_confirm_dangerous,
        "session_list": _tg_session_list,
        "ask_project": _tg_ask_project,
        "ask_task": _tg_ask_task,
        "notify_list": _tg_notify_list,
        "error": _tg_error,
        "stopped": _tg_stopped,
        "stop_confirm": _tg_stop_confirm,
        "fuzzy_confirm": _tg_fuzzy_confirm,
        "history": _tg_history,
        "recent": _tg_recent,
        "menu": _tg_menu,
    }
    builder = builders.get(event)
    if builder:
        return builder(ctx)
    return {"text": f"❓ Unknown event: {event}", "reply_markup": None}


def _kbd(buttons: list[list[tuple[str, str]]]) -> dict:
    """Build inline keyboard dict."""
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row]
            for row in buttons
        ]
    }


def _kbd_yes_no(session_id: str) -> dict:
    return _kbd([[("✅ Yes", f"yes:{session_id}"), ("❌ No", f"no:{session_id}")]])


def _kbd_allow_deny(session_id: str) -> dict:
    return _kbd([[("✅ Allow", f"allow:{session_id}"), ("❌ Deny", f"deny:{session_id}")]])


def _kbd_direction(session_id: str) -> dict:
    return _kbd([[
        ("✅ Yes", f"yes:{session_id}"),
        ("❌ No", f"no:{session_id}"),
        ("✏️ Direction", f"direction:{session_id}"),
    ]])


def _kbd_session_actions(session_id: str, name: str) -> dict:
    return _kbd([
        [("🔔 Notify on", f"notify_on:{session_id}"),
         ("🔕 Mute", f"notify_off:{session_id}")],
        [("▶️ Resume", f"resume:{session_id}"),
         ("💀 Kill", f"kill:{session_id}")],
    ])


def _tg_notification(ctx: dict) -> dict:
    project = ctx.get("project_name", "?")
    message = ctx.get("message", "Needs input")
    tool = ctx.get("tool", "")
    path = ctx.get("path", "")
    session_id = ctx.get("session_id", "")

    lines = [f"🔔 <b>{project}</b> · Needs input", ""]
    if path:
        lines.append(f"Editing: <code>{path}</code>")
    lines.append(f"↳ {message}")
    if tool:
        lines.append(f"<i>Tool: {tool}</i>")

    return {"text": "\n".join(lines), "reply_markup": _kbd_direction(session_id)}


def _tg_start(ctx: dict) -> dict:
    project = ctx.get("project_name", "?")
    task = ctx.get("task", "")
    mode = ctx.get("mode", "skip")

    mode_suffix = " (plan mode)" if mode == "plan" else ""
    lines = [f"🚀 <b>{project}</b> · Started{mode_suffix}", "", task, ""]
    if mode == "plan":
        lines.append("Claude will send a plan before making changes.")
    else:
        lines.append("I'll message you if I need anything.")

    return {"text": "\n".join(lines), "reply_markup": None}


def _tg_stop(ctx: dict) -> dict:
    project = ctx.get("project_name", "?")
    duration = _format_duration(ctx.get("duration_seconds", 0))
    files = ctx.get("files_changed", [])
    issues = ctx.get("has_issues", False)
    session_id = ctx.get("session_id", "")

    if issues:
        header = f"⚠️ <b>{project}</b> · Finished with issues ({duration})"
    else:
        header = f"✅ <b>{project}</b> · Done ({duration})"

    lines = [header, ""]
    shown = files[:MAX_FILES_TELEGRAM]
    for f in shown:
        lines.append(f"<code>{f}</code>")

    remaining = len(files) - MAX_FILES_TELEGRAM
    if remaining > 0:
        lines.append(f"…and {remaining} more")

    kbd = _kbd([[
        ("📋 Session info", f"session_info:{session_id}"),
        ("▶️ Follow-up", f"followup:{session_id}"),
    ]])
    return {"text": "\n".join(lines), "reply_markup": kbd}


def _tg_parked(ctx: dict) -> dict:
    project = ctx.get("project_name", "?")
    waited = ctx.get("waited_minutes", 0)
    remaining = ctx.get("remaining_minutes", 0)
    session_id = ctx.get("session_id", "")

    text = (
        f"⏸ <b>{project}</b> · Waiting {waited}min\n\n"
        f"Still waiting on your input.\n"
        f"Times out in {remaining}min."
    )
    kbd = _kbd([[("▶️ Resume", f"resume:{session_id}"), ("💀 Let expire", f"kill:{session_id}")]])
    return {"text": text, "reply_markup": kbd}


def _tg_expired(ctx: dict) -> dict:
    project = ctx.get("project_name", "?")
    text = (
        f"🗑 <b>{project}</b> · Expired\n\n"
        f"No reply after timeout.\n"
        f"Session cleaned up.\n\n"
        f"Type 'watcher {project}: &lt;task&gt;' to start fresh."
    )
    return {"text": text, "reply_markup": None}


def _tg_confirm_dangerous(ctx: dict) -> dict:
    project = ctx.get("project_name", "?")
    task = ctx.get("task", "")
    mode = ctx.get("mode", "plan")
    session_id = ctx.get("session_id", "")

    text = (
        f"⚠️ <b>Confirm before starting</b>\n\n"
        f"Project: <b>{project}</b>\n"
        f"Task: {task}\n"
        f"Mode: {mode} (deletion detected)\n\n"
        f"Claude will show plan first."
    )
    kbd = _kbd([[("✅ Yes, proceed", f"yes:{session_id}"), ("❌ Cancel", f"no:{session_id}")]])
    return {"text": text, "reply_markup": kbd}


def _tg_session_list(ctx: dict) -> dict:
    sessions = ctx.get("sessions", [])
    count = len(sessions)
    lines = [f"📋 <b>Sessions</b> · {count} active", ""]

    kbd_rows = []
    for s in sessions:
        status = s.get("status", "active")
        emoji = "🟢" if status == "active" else "🟡"
        name = s.get("project_name", "?")
        sid = s.get("session_id", "")
        mins = int(s.get("idle_minutes", 0))
        task = s.get("task", "")[:40]

        status_label = f"{mins}min" if status == "active" else f"parked {mins}min"
        lines.append(f"{emoji} <b>{name}</b> ({status_label})")
        lines.append(f"   {task}")
        lines.append("")

        kbd_rows.append([
            ("🔕 Mute " + name, f"notify_off:{sid}"),
            ("▶️ Resume " + name, f"resume:{sid}"),
        ])
        kbd_rows.append([("💀 Kill " + name, f"kill:{sid}")])

    return {"text": "\n".join(lines), "reply_markup": _kbd(kbd_rows) if kbd_rows else None}


def _tg_ask_project(ctx: dict) -> dict:
    task = ctx.get("task", "")
    projects = ctx.get("projects", {})
    session_id = ctx.get("session_id", "")

    lines = ["📂 <b>Which project?</b>", "", f"Task: {task}", ""]
    for i, name in enumerate(list(projects.keys()), 1):
        lines.append(f"  {i} · <b>{name}</b>")

    # Build numeric keyboard rows
    project_list = list(projects.items())
    kbd_rows = []
    row: list[tuple[str, str]] = []
    for i, (name, _) in enumerate(project_list, 1):
        row.append((f"{i} · {name}", f"project_select:{i}:{session_id}"))
        if len(row) == 2:
            kbd_rows.append(row)
            row = []
    if row:
        kbd_rows.append(row)

    return {"text": "\n".join(lines), "reply_markup": _kbd(kbd_rows) if kbd_rows else None}


def _tg_ask_task(ctx: dict) -> dict:
    project = ctx.get("project_name", "?")
    text = f"❓ <b>{project}</b>\n\nWhat should Claude do?\nReply with a task description."
    return {"text": text, "reply_markup": None}


def _tg_notify_list(ctx: dict) -> dict:
    sessions = ctx.get("sessions", [])
    lines = ["🔔 <b>Which session?</b>", ""]

    kbd_rows = []
    for i, s in enumerate(sessions, 1):
        name = s.get("project_name", "?")
        sid = s.get("session_id", "")
        channels = s.get("channels", "muted")
        mins = int(s.get("idle_minutes", 0))
        lines.append(f"  {i} · <b>{name}</b> ({mins}min) — {channels}")
        kbd_rows.append([
            ("🔔 " + name, f"notify_on:{sid}"),
            ("🔕 Mute", f"notify_off:{sid}"),
        ])

    lines.extend(["", "Or reply: &lt;number&gt; imessage|telegram|both on|off"])
    return {"text": "\n".join(lines), "reply_markup": _kbd(kbd_rows) if kbd_rows else None}


def _tg_error(ctx: dict) -> dict:
    msg = ctx.get("message", "Unknown error")
    return {"text": f"❓ {msg}", "reply_markup": None}


def _tg_stopped(ctx: dict) -> dict:
    return {
        "text": "✓ <b>Watcher stopped.</b>\n\nSessions still running locally.\nLocal notifications re-enabled.",
        "reply_markup": None,
    }


def _tg_stop_confirm(ctx: dict) -> dict:
    active = ctx.get("active_count", 0)
    active_names = ctx.get("active_names", [])
    session_id = ctx.get("session_id", "stop")

    lines = ["⚠️ <b>Stop watcher?</b>", ""]
    if active:
        lines.append(f"{active} session(s) still running:")
        for name in active_names:
            lines.append(f"  · {name}")
        lines.append("")
        lines.append("They'll keep running locally\nbut you won't get updates.")

    kbd = _kbd([[("✅ Stop", f"stop_confirm:{session_id}"), ("❌ Cancel", f"stop_cancel:{session_id}")]])
    return {"text": "\n".join(lines), "reply_markup": kbd}


def _tg_fuzzy_confirm(ctx: dict) -> dict:
    matched = ctx.get("matched_project", "?")
    input_name = ctx.get("input_name", "?")
    session_id = ctx.get("session_id", "")

    text = (
        f"❓ Did you mean <b>{matched}</b>?\n"
        f"(matched from '{input_name}')\n\n"
        f"Reply yes to confirm or give the correct project name."
    )
    kbd = _kbd([[("✅ Yes", f"fuzzy_yes:{session_id}"), ("❌ No", f"fuzzy_no:{session_id}")]])
    return {"text": text, "reply_markup": kbd}


def _tg_history(ctx: dict) -> dict:
    entries = ctx.get("entries", [])
    project = ctx.get("project_name", "")
    header = f"📋 <b>History{' · ' + project if project else ''}</b>"
    lines = [header, ""]
    for e in entries[:10]:
        outcome = e.get("outcome", "completed")
        emoji = {"completed": "✓", "killed": "💀", "expired": "🗑"}.get(outcome, "⚠️")
        dur = _format_duration(e.get("duration_seconds", 0))
        task = e.get("task", "")
        lines.append(f"{emoji} <b>{dur}</b>  {task}")
        changed = e.get("files_changed", [])
        if changed:
            lines.append(f"   Changed: {', '.join(changed[:3])}")
        lines.append("")
    return {"text": "\n".join(lines), "reply_markup": None}


def _tg_recent(ctx: dict) -> dict:
    sessions = ctx.get("sessions", [])
    lines = ["📋 <b>Recent sessions</b>", ""]
    kbd_rows = []
    for i, s in enumerate(sessions, 1):
        status = s.get("status", "?")
        emoji = {"active": "🟢", "parked": "🟡", "completed": "✓", "expired": "🗑"}.get(status, "❓")
        name = s.get("project_name", "?")
        task = s.get("task", "")[:40]
        sid = s.get("session_id", "")
        lines.append(f"{emoji} <b>{name}</b>")
        lines.append(f"   {task}")
        lines.append("")
        if status == "parked":
            kbd_rows.append([("▶️ Resume " + name, f"resume:{sid}")])

    lines.append("Or 'watcher &lt;name&gt;: &lt;task&gt;' for new.")
    return {"text": "\n".join(lines), "reply_markup": _kbd(kbd_rows) if kbd_rows else None}


def _tg_menu(ctx: dict) -> dict:
    text = "👋 <b>Watcher</b>"
    kbd = _kbd([
        [("🆕 New session", "menu:new"), ("📋 List sessions", "menu:list")],
        [("⏸ Resume parked", "menu:resume"), ("💀 Kill session", "menu:kill")],
        [("🛑 Stop watcher", "menu:stop")],
    ])
    return {"text": text, "reply_markup": kbd}


# ─── Main entry point ──────────────────────────────────────────────────────


def build_message(event: str, context: dict, channel: str) -> dict:
    """
    Build a channel-appropriate message.

    Returns {"text": str, "reply_markup": dict | None}
    """
    if channel == "imessage":
        return {"text": build_imessage(event, context), "reply_markup": None}
    else:
        return build_telegram(event, context)
