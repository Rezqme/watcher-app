# Watcher — Full System Specification
**Version:** 0.2.0-draft  
**Status:** Pre-implementation  
**Last updated:** 2026-03-19

---

## Table of Contents

1. [Overview](#1-overview)
2. [Goals & Non-Goals](#2-goals--non-goals)
3. [System Architecture](#3-system-architecture)
4. [Components](#4-components)
   - 4.1 [CLI — `watcher`](#41-cli--watcher)
   - 4.2 [Daemon — `watcherd`](#42-daemon--watcherd)
   - 4.3 [Hooks](#43-hooks)
   - 4.4 [Session Manager](#44-session-manager)
   - 4.5 [Conversation State Manager](#45-conversation-state-manager)
   - 4.6 [Wake Word Parser](#46-wake-word-parser)
   - 4.7 [Message Formatter](#47-message-formatter)
   - 4.8 [Project Registry](#48-project-registry)
   - 4.9 [Telegram Channel](#49-telegram-channel)
5. [Channel System](#5-channel-system)
6. [User Flows](#6-user-flows)
7. [iMessage Format Specification](#7-imessage-format-specification)
8. [Telegram Format Specification](#8-telegram-format-specification)
9. [File System Layout](#9-file-system-layout)
10. [Configuration](#10-configuration)
11. [Installation & Setup](#11-installation--setup)
12. [Security Model](#12-security-model)
13. [Failure Modes & Recovery](#13-failure-modes--recovery)
14. [Open Questions](#14-open-questions)

---

## 1. Overview

**Watcher** is a zero-infrastructure remote control system for Claude Code, built on iMessage and Telegram. It enables a developer to start, monitor, reply to, and manage Claude Code sessions from an Apple Watch, iPhone, or any Telegram client — from any network, without hosting, without a relay server.

### Core Insight

Two messaging platforms already on your devices — iMessage and Telegram — together cover every interaction pattern needed to fully remote control Claude Code. Neither requires a server you host, a relay, or a paid subscription beyond what you already have.

### The Three Interactions

| Direction | iMessage | Telegram |
|---|---|---|
| Mac → Device | Hook → iMessage to self | Hook → Bot API POST |
| Device → Mac (new) | Wake word in new message | Wake word to bot |
| Device → Mac (reply) | Thread reply (swipe-reply gesture) | Reply to message or tap inline button |

### Channel Comparison

| | iMessage | Telegram |
|---|---|---|
| Apple Watch delivery | ✅ Native, no app needed | ⚠️ Requires Telegram app on iPhone |
| Inline buttons | ❌ Type "yes" / "1" | ✅ Tappable inline keyboard |
| Reply routing | `thread_originator_guid` (fragile) | `reply_to_message.message_id` (reliable) |
| Setup complexity | High (Full Disk Access, AppleScript) | Low (BotFather token, 2 minutes) |
| Works without Apple ecosystem | ❌ Mac + Apple ID required | ✅ Any platform |
| Receiving mechanism | Poll `chat.db` via `imsg watch` | Long-poll `getUpdates` (no hosting) |

### Recommended Usage

- **iMessage** — when Apple Watch native delivery matters most. Wrist notification with no app required.
- **Telegram** — when interaction quality matters most. Buttons, reliable routing, easier setup.
- **Both simultaneously** — iMessage delivers to Watch natively, Telegram handles the interactive reply flow.

### What Makes It Different

Every other solution requires either a hosted relay server (Happy Coder, Claude-Code-Remote), LAN presence (taphaptic), a paid subscription (Remote Control), or a phone app (claude-push). Watcher requires none of these. The Mac is the server. iMessage and Telegram are the transports.

---

## 2. Goals & Non-Goals

### Goals

- **Zero infrastructure.** No relay server, no hosting, no cloud account beyond an Apple ID and/or Telegram account.
- **Apple Watch as primary interface.** Every message readable in a banner, every reply a short word or number.
- **Works anywhere.** As long as the Mac is online, distance is irrelevant.
- **Multiple concurrent sessions.** Each session gets its own message thread. Replies route correctly by thread ID.
- **Safe by default.** Watcher validates intent before spawning. Once spawned, sessions run freely (plan mode or skip-permissions). Watcher IS the permission system.
- **Clean on/off switch.** Hooks are always installed but check a flag file. Toggling watcher does not require editing Claude config.
- **Per-session per-channel notification control.** Individual sessions can have iMessage on, Telegram on, both, or neither, toggled at any time mid-session without disrupting Claude's context.
- **Session lifecycle management.** Timeout, cleanup, listing, and termination all handled automatically and on-demand.
- **Graceful degradation.** When watcher is off, hooks fall back to local macOS notifications silently.
- **Dual-channel.** iMessage and Telegram operate as peers. Either can spawn sessions, route replies, and receive notifications independently.

### Non-Goals

- Not a general-purpose iMessage or Telegram bot.
- Not a replacement for Claude Code's Remote Control feature.
- Not multi-user (single Apple ID / single Telegram chat ID, single developer).
- Not a GUI application.
- Not designed for sandboxed or App Store distribution (requires Full Disk Access for iMessage).

---

## 3. System Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                           MAC                                │
│                                                              │
│  Claude Code Session(s)                                      │
│    ↓ hook events (SessionStart, Notification, Stop)          │
│  Hook Scripts (~/.claude/hooks/)                             │
│    ↓ reads enabled flag + per-session channel flags          │
│  send.py                                                     │
│    ├── iMessage: imsg send --to self                         │
│    └── Telegram: POST api.telegram.org/bot<token>/sendMessage│
│                                                              │
│  watcherd (daemon) — asyncio event loop                      │
│    ├── iMessage loop: imsg watch --chat-id self --json       │
│    │     reads thread_originator_guid for routing            │
│    └── Telegram loop: getUpdates long-poll                   │
│          reads reply_to_message.message_id for routing       │
│          handles callback_query (button taps)                │
│                                                              │
│  Both loops feed into shared handle_message() router:        │
│    ├── thread/reply_to match → active session → resume       │
│    ├── thread/reply_to match → conversation → continue       │
│    ├── wake word → parse + assess + spawn                    │
│    └── management command → list/kill/clean/stop             │
│                                                              │
│  Session Registry (~/.claude-watch/sessions/)                │
│  Conversation State (~/.claude-watch/conversations/)         │
│  Project Registry (~/.claude-watch/projects.json)            │
└──────────────────────────────────────────────────────────────┘
                        ▲ ▼             ▲ ▼
                   iMessage (Apple)  Telegram API
                        ▲ ▼             ▲ ▼
              ┌──────────────┐  ┌────────────────────┐
              │ Apple Watch  │  │ Telegram (any       │
              │ iPhone       │  │ device — phone,     │
              │ iMessage     │  │ watch, tablet,      │
              │ native       │  │ desktop, Android)   │
              └──────────────┘  └────────────────────┘
```

### Key Technical Dependencies

| Dependency | Role | Source |
|---|---|---|
| `imsg` (steipete) | iMessage send/watch CLI | github.com/steipete/imsg |
| `jq` | JSON parsing in bash hooks | brew install jq |
| `claude` CLI | Session spawn and resume | Anthropic |
| `thread_originator_guid` | iMessage thread routing | chat.db field (not `reply_to_guid`) |
| `reply_to_message.message_id` | Telegram thread routing | Telegram Bot API |
| `callback_query.data` | Telegram button tap routing | Telegram Bot API |
| Full Disk Access | Required to read chat.db (iMessage only) | System Settings → Privacy |
| Automation permission | Required for Messages.app (iMessage only) | System Settings → Privacy |
| Telegram Bot Token | Bot API auth | @BotFather on Telegram |

### Why `thread_originator_guid` Not `reply_to_guid` (iMessage)

`reply_to_guid` in chat.db is unreliable — it frequently points to the most recent message rather than the actual reply target. `thread_originator_guid` correctly identifies the root message of a thread, matching iMessage's visual UI. Confirmed by analysis in steipete/imsg#39 and openclaw issues #10872 and #42266. All iMessage session routing uses `thread_originator_guid` exclusively.

---

## 4. Components

### 4.1 CLI — `watcher`

The user-facing command. Installed to `/usr/local/bin/watcher` or via homebrew.

#### Commands

```
watcher start                  Start the daemon, enable hooks
watcher stop                   Stop the daemon, disable hooks (local notifs resume)
watcher restart                Stop then start
watcher status                 Show daemon state + all active/parked sessions with channel info
watcher sessions               List sessions (alias for status)
watcher sessions list          Explicit list
watcher sessions kill <n>      Kill a named session (prompts confirmation)
watcher sessions clean         Remove all completed and expired sessions
watcher notify on <n>          Enable iMessage+Telegram for named session
watcher notify off <n>         Disable all notifications for named session
watcher notify imessage <n>    Toggle iMessage only for named session
watcher notify telegram <n>    Toggle Telegram only for named session
watcher recent                 Show sessions from last 24 hours
watcher config                 Open config in $EDITOR
watcher logs                   Tail daemon logs
watcher install                Run first-time setup wizard
watcher doctor                 Check permissions, dependencies, config validity
```

#### `watcher status` Output (terminal)

```
Watcher status: RUNNING (pid 4821)
Channels: iMessage (you@icloud.com)  Telegram (@yourbot)
Timeout: 30min

Sessions (3):
  🟢 payments-api   active  12m   🔔 iMsg + TG   Refactor stripe webhook
  🟡 frontend       parked   8m   🔔 TG only     Fix tailwind config
  🟢 my-api         active   2m   🔕 muted        Add rate limiting

Conversations (1 pending):
  ⏳ awaiting_project  "fix the login bug"  3m  [telegram]

Use "watcher sessions kill <n>" to terminate.
```

---

### 4.2 Daemon — `watcherd`

A Python process that runs persistently, managed by a LaunchAgent. Uses `asyncio` to run the iMessage and Telegram loops concurrently in a single process.

#### Main Loop

```python
import asyncio
import json
import subprocess
import httpx

async def imessage_loop():
    proc = await asyncio.create_subprocess_exec(
        "imsg", "watch", "--chat-id", "self", "--json",
        stdout=asyncio.subprocess.PIPE
    )
    async for line in proc.stdout:
        try:
            msg = json.loads(line.strip())
        except json.JSONDecodeError:
            continue
        if msg.get("is_from_me"):
            await handle_message(msg, channel="imessage")

async def telegram_loop():
    offset = 0
    async with httpx.AsyncClient() as client:
        while True:
            try:
                resp = await client.get(
                    f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                    params={"offset": offset, "timeout": 30},
                    timeout=35
                )
                updates = resp.json().get("result", [])
                for update in updates:
                    offset = update["update_id"] + 1
                    await handle_telegram_update(update)
            except Exception as e:
                log(f"Telegram poll error: {e}")
                await asyncio.sleep(5)

async def reaper_loop():
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        reap_sessions()
        reap_conversations()

async def main():
    tasks = [reaper_loop()]
    if config.get("imessage", {}).get("enabled"):
        tasks.append(imessage_loop())
    if config.get("telegram", {}).get("enabled"):
        tasks.append(telegram_loop())
    await asyncio.gather(*tasks)
```

#### Message Routing (shared, channel-aware)

```python
async def handle_message(msg: dict, channel: str):
    text        = msg.get("text", "").strip()
    thread_id   = get_thread_id(msg, channel)
    # iMessage: thread_originator_guid
    # Telegram text reply: reply_to_message.message_id
    # Telegram button: callback_query.data encodes session_id + action

    # 1. Button tap (Telegram only)
    if channel == "telegram" and msg.get("type") == "callback":
        await handle_callback(msg)
        return

    # 2. Thread reply → active Claude session
    if thread_id:
        session = session_manager.find_by_thread(thread_id, channel)
        if session:
            await resume_session(session, text, channel)
            return

    # 3. Thread reply → pending watcher conversation
    if thread_id:
        conv = conversation_manager.find(thread_id, channel)
        if conv:
            await continue_conversation(conv, text, channel)
            return

    # 4. Wake word → new message
    if text.lower().startswith("watcher"):
        await handle_wake_word(text, channel)
        return

async def handle_telegram_update(update: dict):
    # Button tap
    if cq := update.get("callback_query"):
        await handle_message(
            {"type": "callback", "data": cq["data"],
             "message": cq["message"], "id": cq["id"]},
            channel="telegram"
        )
        return
    # Text message
    if msg := update.get("message"):
        if msg.get("text"):
            await handle_message(msg, channel="telegram")
```

#### LaunchAgent Plist

```xml
<!-- ~/Library/LaunchAgents/com.watcher.daemon.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.watcher.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/watcherd</string>
  </array>
  <key>RunAtLoad</key>
  <false/>
  <key>KeepAlive</key>
  <dict>
    <key>PathState</key>
    <dict>
      <key>/Users/YOU/.claude-watch/enabled</key>
      <true/>
    </dict>
  </dict>
  <key>StandardOutPath</key>
  <string>/Users/YOU/.claude-watch/logs/watcherd.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/YOU/.claude-watch/logs/watcherd-error.log</string>
  <key>ThrottleInterval</key>
  <integer>5</integer>
</dict>
</plist>
```

`KeepAlive.PathState` means launchd automatically starts the daemon when `enabled` exists and stops it when `enabled` is removed. `watcher start/stop` just manages that file — launchd does the rest.

---

### 4.3 Hooks

Three hooks. All installed permanently in `~/.claude/settings.json`. All check the global `enabled` flag and per-session per-channel flags, degrading gracefully to local notifications when all are off.

#### Settings JSON

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "~/.claude-watch/hooks/session-start.sh",
        "timeout": 5
      }]
    }],
    "Notification": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "~/.claude-watch/hooks/notify.sh",
        "timeout": 10
      }]
    }],
    "Stop": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "~/.claude-watch/hooks/stop.sh",
        "timeout": 10
      }]
    }]
  }
}
```

#### `session-start.sh`

Always registers the session regardless of watcher state — tracking runs even when notifications are off.

```bash
#!/usr/bin/env bash
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
echo ""                   > "$SESSION_DIR/mode"
# Channel flags — absent by default, created by spawner or toggle command
# notify_imessage  ← touch to enable iMessage for this session
# notify_telegram  ← touch to enable Telegram for this session
```

#### `notify.sh`

Checks global flag and both per-session channel flags. Sends to whichever channels are enabled.

```bash
#!/usr/bin/env bash
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
IMSG_ON="$SESSION_DIR/notify_imessage"
TG_ON="$SESSION_DIR/notify_telegram"

SENT=0

# iMessage — global on OR session opted in
if [ -f "$GLOBAL_ON" ] || [ -f "$IMSG_ON" ]; then
    "$HOME/.claude-watch/send.py" \
        --channel imessage \
        --event notification \
        --session-id "$SESSION_ID" \
        --message "$MESSAGE" \
        --tool "$TOOL"
    SENT=1
fi

# Telegram — global on OR session opted in
if [ -f "$GLOBAL_ON" ] || [ -f "$TG_ON" ]; then
    "$HOME/.claude-watch/send.py" \
        --channel telegram \
        --event notification \
        --session-id "$SESSION_ID" \
        --message "$MESSAGE" \
        --tool "$TOOL"
    SENT=1
fi

# Neither — local macOS notification fallback
if [ "$SENT" -eq 0 ]; then
    osascript -e "display notification \"$MESSAGE\" with title \"Claude Code\""
fi
```

#### `stop.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

PAYLOAD=$(cat)
SESSION_ID=$(echo "$PAYLOAD" | jq -r '.session_id // empty')

SESSION_DIR="$HOME/.claude-watch/sessions/$SESSION_ID"
if [ -d "$SESSION_DIR" ]; then
    echo "completed" > "$SESSION_DIR/status"
    date +%s         > "$SESSION_DIR/completed_at"
fi

GLOBAL_ON="$HOME/.claude-watch/enabled"
IMSG_ON="$SESSION_DIR/notify_imessage"
TG_ON="$SESSION_DIR/notify_telegram"

SENT=0

if [ -f "$GLOBAL_ON" ] || [ -f "$IMSG_ON" ]; then
    "$HOME/.claude-watch/send.py" --channel imessage --event stop --session-id "$SESSION_ID"
    SENT=1
fi

if [ -f "$GLOBAL_ON" ] || [ -f "$TG_ON" ]; then
    "$HOME/.claude-watch/send.py" --channel telegram --event stop --session-id "$SESSION_ID"
    SENT=1
fi

if [ "$SENT" -eq 0 ]; then
    osascript -e "display notification \"Task complete\" with title \"Claude Code\""
fi
```

---

### 4.4 Session Manager

Manages session metadata on disk. Sessions persist across daemon restarts. Now tracks one thread GUID per channel.

#### Session Record

```
~/.claude-watch/sessions/<session_id>/
  session_id              TEXT   Claude's internal session UUID
  project_name            TEXT   "payments-api"
  project_dir             TEXT   "/Users/you/projects/payments-api"
  task                    TEXT   "Refactor the stripe webhook handler"
  mode                    TEXT   "plan" | "skip"
  status                  TEXT   "active" | "parked" | "completed" | "expired"
  started_at              INT    Unix epoch
  last_activity           INT    Unix epoch
  completed_at            INT    Unix epoch (if applicable)
  thread_guid_imessage    TEXT   iMessage thread_originator_guid
  thread_guid_telegram    INT    Telegram message_id of the first notification sent
  notify_imessage         FLAG   exists = iMessage notifications on
  notify_telegram         FLAG   exists = Telegram notifications on
```

#### Session States

```
                  spawned
                     │
              ┌──────▼───────┐
              │    active    │◄──── resume (reply received, either channel)
              └──────┬───────┘
                     │ idle > timeout
              ┌──────▼───────┐
              │    parked    │
              └──────┬───────┘
                     │ idle > 2x timeout
              ┌──────▼───────┐
              │   expired    │──── deleted from disk
              └─────────────┘

              active/parked → Stop hook → completed → deleted after reap
              any state → kill command → terminated → deleted immediately
```

#### Key Methods

```python
class SessionManager:
    def create(self, session_id, project_name, project_dir, task, mode,
               channels: list[str]) -> Session
    def find_by_id(self, session_id) -> Session | None
    def find_by_name(self, project_name) -> list[Session]
    def find_by_thread(self, thread_id, channel: str) -> Session | None
    def all(self) -> list[Session]
    def active(self) -> list[Session]
    def parked(self) -> list[Session]
    def recent(self, hours: int = 24) -> list[Session]
    def kill(self, session_id) -> bool
    def clean(self) -> int
    def set_channel(self, session_id, channel: str, enabled: bool)
```

---

### 4.5 Conversation State Manager

Manages in-progress watcher conversations — clarifying interactions before a session is spawned. Tracks which channel the conversation originated from so replies go back the right way.

#### Conversation Record

```python
@dataclass
class Conversation:
    thread_id:     str         # iMessage thread_originator_guid OR Telegram message_id
    channel:       str         # "imessage" | "telegram"
    state:         str         # awaiting_project | awaiting_confirm | awaiting_menu | awaiting_task
    partial_task:  str
    project_name:  str | None
    mode:          str | None
    created_at:    float       # 10-minute expiry
```

All replies to a conversation are sent back on the **same channel the conversation started on**.

---

### 4.6 Wake Word Parser

Identical grammar for both channels. The same message `watcher payments-api: fix tests` works whether sent via iMessage or Telegram.

#### Grammar

```
message     := wake_word [space target] [colon space task]
wake_word   := "watcher" (case-insensitive)
target      := project_name | management_command
project_name := word (matched against projects.json, fuzzy)
management_command := "list" | "status" | "kill" | "clean" | "stop" | "help" | "recent" | "notify"
task        := any text after ":"
```

#### Task Assessment

```python
VAGUE_PATTERNS = [
    r"^(fix|improve|update|change|make|do|check|clean\s*up)\s*\w*$",
    r"\b(stuff|things|improvements|updates|changes|it|them)\b",
    r"^.{1,12}$",
    r"^(some|general|various|a few)\b",
]

DANGEROUS_PATTERNS = [
    r"\b(delete|remove|drop|destroy|truncate|wipe|rm|purge)\b",
    r"\b(all|every|entire|whole)\b.*\b(file|folder|dir|record|entry|table)\b",
    r"rm\s+-",
    r"\b(reset|rollback|revert)\b.*\b(database|db|schema|migration)\b",
]

def assess_task(task: str) -> Literal["clear", "vague", "dangerous"]:
    t = task.lower()
    if any(re.search(p, t) for p in DANGEROUS_PATTERNS):
        return "dangerous"
    if any(re.search(p, t) for p in VAGUE_PATTERNS):
        return "vague"
    return "clear"

def choose_mode(assessment: str) -> str:
    return "plan" if assessment in ("dangerous", "vague") else "skip"
```

---

### 4.7 Message Formatter

Channel-aware. iMessage builds plain text within Watch display constraints. Telegram builds text plus an optional `reply_markup` inline keyboard dict for button-based replies.

```python
def build_message(event: str, context: dict, channel: str) -> dict:
    # Returns {"text": str, "reply_markup": dict | None}
    # reply_markup is None for iMessage (ignored), populated for Telegram
    if channel == "imessage":
        return {"text": build_imessage(event, context), "reply_markup": None}
    else:
        return build_telegram(event, context)
```

See Section 7 for iMessage format rules and Section 8 for Telegram format rules.

---

### 4.8 Project Registry

Unchanged from v0.1. A JSON file mapping short names to filesystem paths.

```json
{
  "default": "api",
  "projects": {
    "api":      "/Users/you/projects/my-api",
    "frontend": "/Users/you/projects/frontend",
    "payments": "/Users/you/projects/payments-api"
  }
}
```

Fuzzy matching via `difflib.get_close_matches` with 0.6 cutoff. Numeric selection supported from any list/menu.

---

### 4.9 Telegram Channel

#### Bot Setup

A Telegram bot is created via @BotFather — a conversation in Telegram that takes about 2 minutes. The result is a `token` string like `7294810234:AAF...`. No server, no webhook, no hosting required.

The bot receives messages via **long polling** — `getUpdates` with `timeout=30` holds the connection open until a message arrives or 30 seconds passes. This is how consumer Telegram bots work without a public endpoint.

#### Sending Messages

```python
async def telegram_send(text: str, reply_markup: dict | None = None,
                         reply_to: int | None = None) -> int:
    """Returns message_id of the sent message — stored as thread anchor."""
    payload = {
        "chat_id": config["telegram"]["chat_id"],
        "text": text,
        "parse_mode": "HTML",  # Telegram supports HTML, not markdown
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if reply_to:
        payload["reply_to_message_id"] = reply_to

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json=payload
        )
    return resp.json()["result"]["message_id"]
```

The returned `message_id` is stored in `thread_guid_telegram` in the session record. When the user replies to that message, `reply_to_message.message_id` matches it exactly — no ambiguity.

#### Inline Keyboard Builder

```python
def build_keyboard(buttons: list[list[tuple[str, str]]]) -> dict:
    """
    buttons: list of rows, each row a list of (label, callback_data) tuples
    callback_data format: "<action>:<session_id>"
    e.g. "yes:abc123", "no:abc123", "allow:abc123", "deny:abc123"
    """
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row]
            for row in buttons
        ]
    }

# Common keyboards
def keyboard_yes_no(session_id: str) -> dict:
    return build_keyboard([[
        ("✅ Yes", f"yes:{session_id}"),
        ("❌ No",  f"no:{session_id}"),
    ]])

def keyboard_allow_deny(session_id: str) -> dict:
    return build_keyboard([[
        ("✅ Allow", f"allow:{session_id}"),
        ("❌ Deny",  f"deny:{session_id}"),
    ]])

def keyboard_direction(session_id: str) -> dict:
    return build_keyboard([[
        ("✅ Yes",           f"yes:{session_id}"),
        ("❌ No",            f"no:{session_id}"),
        ("✏️ Give direction", f"direction:{session_id}"),
    ]])

def keyboard_session_actions(session_id: str, name: str) -> dict:
    return build_keyboard([
        [("🔔 Notify on",  f"notify_on:{session_id}"),
         ("🔕 Mute",       f"notify_off:{session_id}")],
        [("▶️ Resume",      f"resume:{session_id}"),
         ("💀 Kill",        f"kill:{session_id}")],
    ])
```

#### Callback Handler

```python
async def handle_callback(msg: dict):
    data       = msg["data"]           # e.g. "yes:abc123"
    message_id = msg["message"]["message_id"]
    
    action, session_id = data.split(":", 1)
    session = session_manager.find_by_id(session_id)

    # Answer the callback to remove the loading spinner on the button
    await telegram_answer_callback(msg["id"])

    if action in ("yes", "allow", "approve"):
        if session:
            await resume_session(session, "yes", channel="telegram")
        else:
            await telegram_send("❓ Session not found.")

    elif action in ("no", "deny", "reject"):
        if session:
            await resume_session(session, "no", channel="telegram")

    elif action == "direction":
        # User wants to type a custom reply — send a prompt
        await telegram_send(
            "✏️ Type your direction and reply to this message.",
            reply_to=message_id
        )
        # Register a transient conversation waiting for the next reply
        conversation_manager.create_direction_prompt(message_id, session_id)

    elif action == "notify_on":
        session_manager.set_channel(session_id, "telegram", True)
        await telegram_send(f"🔔 Telegram notifications on for {session.project_name}.")

    elif action == "notify_off":
        session_manager.set_channel(session_id, "telegram", False)
        await telegram_send(f"🔕 Telegram notifications off for {session.project_name}.")

    elif action == "resume":
        if session and session.status == "parked":
            await resume_session(session, "continue", channel="telegram")

    elif action == "kill":
        # Requires second confirmation — send a confirm keyboard
        await telegram_send(
            f"⚠️ Kill <b>{session.project_name}</b>?",
            reply_markup=build_keyboard([[
                ("💀 Yes, kill it", f"kill_confirm:{session_id}"),
                ("Cancel",         f"kill_cancel:{session_id}"),
            ]])
        )
```

#### Editing vs New Messages

When a callback button is tapped, Telegram best practice is to **edit the original message** to remove the keyboard and show a response inline, rather than sending a new message. This keeps the chat clean:

```python
async def telegram_edit(message_id: int, new_text: str):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{TOKEN}/editMessageText",
            json={
                "chat_id":    config["telegram"]["chat_id"],
                "message_id": message_id,
                "text":       new_text,
                "parse_mode": "HTML",
            }
        )
```

After a button tap — edit the notification message to show the reply was received, then send a new message with the result. This prevents orphaned inline keyboards cluttering the chat.

---

## 5. Channel System

### Per-Session Per-Channel Notification Flags

Each session independently controls which channels notify you. Flags are simple files — their existence means "on", absence means "off".

```
~/.claude-watch/sessions/<id>/
  notify_imessage    ← touch = iMessage on for this session
  notify_telegram    ← touch = Telegram on for this session
```

### Flag Priority Matrix

```
Global flag    Session imessage flag    Session telegram flag    Result
────────────────────────────────────────────────────────────────────────
OFF            absent                   absent                   local macOS only
OFF            present                  absent                   iMessage only
OFF            absent                   present                  Telegram only
OFF            present                  present                  both channels
ON             absent                   absent                   both channels (global default)
ON             absent                   present                  both channels
ON             present                  absent                   both channels
```

When global is ON and no session flags exist, both channels send if both are configured. Individual session flags can narrow this (by being explicit) but never go below what the global flag provides.

### Mid-Session Toggle

Toggling never touches the session's Claude context. It only writes or removes a file. The session continues running; the next hook fire picks up the new state.

**Four ways to toggle mid-session:**

1. **From Watch/phone via iMessage:** `watcher notify telegram on` → daemon writes flag
2. **From Telegram button:** tap `🔔 Notify on` / `🔕 Mute` in session list
3. **Slash command in Claude:** `/watch on` or `/watch telegram off`
4. **Terminal:** `watcher notify telegram on payments-api`

#### Slash Command Implementation

```bash
# .claude/commands/watch.sh
#!/bin/bash
# Usage: /watch [channel] on|off|status
# channel: imessage | telegram | (both if omitted)

SESSION_ID="$CLAUDE_SESSION_ID"
SESSION_DIR="$HOME/.claude-watch/sessions/$SESSION_ID"
CHANNEL="${1:-both}"
ACTION="${2:-status}"

case "$ACTION" in
  on)
    [ "$CHANNEL" = "imessage" ] || [ "$CHANNEL" = "both" ] && \
      touch "$SESSION_DIR/notify_imessage"
    [ "$CHANNEL" = "telegram" ] || [ "$CHANNEL" = "both" ] && \
      touch "$SESSION_DIR/notify_telegram"
    echo "Notifications enabled: $CHANNEL"
    ;;
  off)
    [ "$CHANNEL" = "imessage" ] || [ "$CHANNEL" = "both" ] && \
      rm -f "$SESSION_DIR/notify_imessage"
    [ "$CHANNEL" = "telegram" ] || [ "$CHANNEL" = "both" ] && \
      rm -f "$SESSION_DIR/notify_telegram"
    echo "Notifications disabled: $CHANNEL"
    ;;
  status)
    IMSG=$([ -f "$SESSION_DIR/notify_imessage" ] && echo "ON" || echo "off")
    TG=$([ -f "$SESSION_DIR/notify_telegram" ] && echo "ON" || echo "off")
    echo "iMessage: $IMSG  Telegram: $TG"
    ;;
esac
```

### Session Discovery — `watcher recent` and `watcher notify`

When a channel command doesn't include a session name, watcher lists active sessions and prompts for selection. You never need to remember a name.

```
You:      "watcher notify"
Watcher:  "🔔 Which session?

           1 · payments-api (active 4min) — iMsg + TG
           2 · frontend (active 22min) — TG only
           3 · my-api (parked 1hr) — muted

           Reply: <number> imessage|telegram|both on|off
           e.g. '1 both off' or '3 telegram on'"
```

When only one session is active, skip the menu and act directly.

```
You:     "watcher notify telegram off"
Watcher: "🔕 frontend — Telegram OFF
          (only active session)"
```

---

## 6. User Flows

### Flow 1: First-Time Setup

```
$ watcher install

Watcher — first-time setup

Checking dependencies...
  ✓ imsg, claude, jq found

iMessage setup:
  ✗ Full Disk Access: not granted
    → System Settings → Privacy → Full Disk Access → add Terminal
    → Press Enter when done: [enter]
  ✓ Full Disk Access granted
  ✓ Automation (Messages) granted
  Your Apple ID: you@icloud.com [enter]

Telegram setup (optional — better buttons, works on any device):
  Skip? [y/N]: N
  1. Open Telegram → message @BotFather → send /newbot
  2. Paste your bot token: 7294810234:AAF... [enter]
  ✓ Token saved
  3. Send any message to your bot now to register your chat ID...
  ✓ Chat ID detected: 847392011

Adding projects...
  Project name (or "done"): payments-api [enter]
  Path: /Users/you/projects/payments-api [enter]
  Project name (or "done"): frontend [enter]
  Path: /Users/you/projects/frontend [enter]
  Project name (or "done"): done [enter]

Installing hooks into ~/.claude/settings.json...
  ✓ SessionStart, Notification, Stop hooks added

Installing LaunchAgent...
  ✓ com.watcher.daemon.plist installed

Default channel: telegram [enter]  (or imessage / both)

Setup complete.
  watcher start   — go
  watcher doctor  — verify
```

---

### Flow 2: Clear Task — Telegram

```
[Telegram] You:     "watcher payments-api: fix the failing stripe tests"

[Telegram] Watcher: "🚀 payments-api · Started

                     Fix the failing stripe tests

                     I'll message you if I need anything."

[Mac] Claude spawns in /projects/payments-api, --dangerously-skip-permissions

      ... Claude works ...

[Telegram] Claude:  "🔔 payments-api · Needs input

                     Editing: src/webhooks/stripe.ts
                     ↳ Remove the legacy charge handler?
                       Referenced in 2 other files.

                     [ ✅ Yes ]  [ ❌ No ]  [ ✏️ Direction ]"

[Telegram] You tap: [ ✅ Yes ]

            Message edits to: "✅ Replied: yes"
            Claude resumes.

[Telegram] Watcher: "✅ payments-api · Done (3m 12s)

                     · src/webhooks/stripe.ts
                     · src/webhooks/__tests__/stripe.test.ts

                     [ 🔔 Notify on ]  [ ▶️ Follow-up ]"
```

---

### Flow 3: Clear Task — iMessage (Watch)

```
[Watch] You type:   "watcher payments-api: fix the failing stripe tests"
[Watch] Watcher:    "🚀 payments-api · Started

                     Fix the failing stripe tests

                     I'll let you know if I need anything."

[Watch] Claude:     "🔔 payments-api · Needs input

                     Editing: src/webhooks/stripe.ts
                     ↳ Remove the legacy charge handler?

                     Reply yes / no / or give direction."

[Watch] You swipe-reply: "yes, those refs are stale too"

         watcherd routes via thread_originator_guid → payments-api session
         claude --resume <id> -p "yes, those refs are stale too"

[Watch] Watcher:    "✅ payments-api · Done (3m 12s)

                     · src/webhooks/stripe.ts
                     · …and 1 more

                     Reply to ask a follow-up."
```

---

### Flow 4: Both Channels Active

```
[Mac] Session starts — spawned by Telegram, both channels enabled.

[iMessage Watch]: "🔔 payments-api · Needs input
                   ↳ Remove legacy handler?
                   Reply yes / no / or give direction."

[Telegram phone]:  "🔔 payments-api · Needs input
                    ↳ Remove legacy handler?
                    [ ✅ Yes ]  [ ❌ No ]  [ ✏️ Direction ]"

You tap Yes on Telegram. watcherd routes it.

iMessage thread remains open but stale — no action needed.
(A future reply to the iMessage thread would also work fine.)
```

---

### Flow 5: Dangerous Task — Plan Mode

```
[Telegram] You:     "watcher frontend: delete all the deprecated API routes"

[Telegram] Watcher: "⚠️ Confirm before starting

                     Project: frontend
                     Task: delete all the deprecated API routes
                     Mode: plan (deletion detected)

                     Claude will show a plan first.

                     [ ✅ Yes, proceed ]  [ ❌ Cancel ]"

[Telegram] You tap: [ ✅ Yes, proceed ]

[Telegram] Watcher: "🚀 frontend · Started (plan mode)

                     Claude will send plan before making changes."

[Telegram] Claude:  "🔔 frontend · Plan ready

                     Will remove:
                     · /api/v1/users (3 handlers)
                     · /api/v1/posts (2 handlers)
                     · …and 4 more routes

                     [ ✅ Approve ]  [ ❌ Reject ]  [ ✏️ Modify ]"

[Telegram] You tap: [ ✅ Approve ]

[Telegram] Watcher: "✅ frontend · Done (5m 41s)

                     · src/routes/index.ts
                     · src/routes/v1/users.ts
                     · …and 5 more"
```

---

### Flow 6: Toggle Notifications Mid-Session

```
[Telegram] Session "my-api" is running silently (no channel flags set).
           You want to start receiving updates.

[Telegram] You:     "watcher notify"
[Telegram] Watcher: "🔔 Which session?

                     1 · payments-api (active 12min) — iMsg + TG
                     2 · my-api (active 4min) — muted

                     Reply: <number> imessage|telegram|both on|off"

[Telegram] You:     "2 telegram on"
[Telegram] Watcher: "🔔 my-api — Telegram ON
                     Next hook fire will notify you here."

           watcherd touches ~/.claude-watch/sessions/<my-api-id>/notify_telegram
           No context impact on the running session.
```

---

### Flow 7: Recent Sessions + Resume by Number

```
[iMessage] You:     "watcher recent"
[iMessage] Watcher: "📋 Recent sessions

                     🟢 payments-api (active now)
                        Fix stripe tests · 4min ago

                     🟡 frontend (parked)
                        Delete deprecated routes · 22min ago

                     ✓  my-api (done 1hr ago)
                        Add rate limiting

                     Reply number to resume parked,
                     or 'watcher <name>: <task>' for new."

[iMessage] You swipe-reply: "2"
[iMessage] Watcher: "⏸ Resuming frontend...

                     Previous task:
                     Delete deprecated API routes

                     Continuing where it left off."

           claude --resume <frontend-session-id> -p "continue"
```

---

### Flow 8: Session Timeout, Park, Expire

```
[Session idle 30 minutes]

[Telegram] Watcher: "⏸ frontend · Waiting 30min

                     Still waiting on your input.
                     Times out in 30min.

                     [ ▶️ Resume ]  [ 💀 Let expire ]"

[No action — another 30 minutes pass]

[Telegram] Watcher: "🗑 frontend · Expired

                     No reply after 60min.
                     Session cleaned up.

                     Type 'watcher frontend: <task>' to start fresh."
```

---

### Flow 9: `watcher stop` with Active Sessions

```
[Telegram] You:     "watcher stop"
[Telegram] Watcher: "⚠️ Stop watcher?

                     2 sessions still running:
                     · payments-api (active)
                     · my-api (active)

                     They'll keep running locally
                     but you won't get updates.

                     [ ✅ Stop ]  [ ❌ Cancel ]"

[Telegram] You tap: [ ✅ Stop ]
[Telegram] Watcher: "✓ Watcher stopped.

                     Sessions still running locally.
                     Local notifications re-enabled."
```

---

## 7. iMessage Format Specification

### Watch Display Constraints

```
Notification banner:  ~40 chars — must be informative alone
Notification body:    ~8 lines — scanned not read
Reply input:          Dictation or tiny keyboard — short replies only
```

### Structural Rules

1. Line 1 is always the banner — emoji, project, status. Max 40 chars.
2. Line 2 is always blank.
3. No line exceeds 35 chars.
4. Reply hint is always the last line.
5. Max 8 lines total.
6. File paths truncated to 28 chars — `…/stripe.ts` not full path.
7. File lists: show 3 + `…and N more`.
8. Durations: `3m 12s` not `192s`.
9. No markdown. No code blocks. Plain text only.

### Display Constants

```python
MAX_BANNER    = 40
MAX_LINE      = 35
MAX_LINES     = 8
MAX_PATH      = 28
MAX_TASK_ECHO = 55
```

### Emoji Language

| Emoji | Meaning |
|---|---|
| 🔔 | Claude needs input |
| ⚡ | Permission needed |
| 🚀 | Session started |
| ✅ | Done successfully |
| ⚠️ | Warning / confirm needed |
| ⏸ | Parked / waiting |
| 🗑 | Expired / cleaned up |
| 💀 | Killed by user |
| ❓ | Watcher asking a question |
| 📋 | List / status |
| 👋 | Interactive menu |
| 📂 | Project selection |
| ❌ | Cancelled |
| 🟢 | Active session |
| 🟡 | Parked session |

### Message Templates

```
Notification:
🔔 {project} · Needs input

[Editing: {path}]
↳ {message}

Reply yes / no / or give direction.

---

Start:
🚀 {project} · Started[(plan mode)]

{task}

[Claude will send a plan before changes.]
[I'll let you know if I need anything.]

---

Done (success):
✅ {project} · Done ({duration})

[· file1]
[· file2]
[· …and N more]

Reply to ask a follow-up.

---

Done (issues):
⚠️ {project} · Finished with issues ({dur})

{summary}

Reply to continue or ignore.

---

Parked:
⏸ {project} · Waiting {N}min

Still waiting on input from earlier.
Times out in {remaining}min.

Reply to resume or let it expire.

---

Expired:
🗑 {project} · Expired

No reply — session cleaned up.

Reply "watcher {project}: <task>" to start fresh.
```

---

## 8. Telegram Format Specification

### Telegram Advantages Over iMessage

- **HTML formatting supported** — `<b>bold</b>`, `<code>monospace</code>`, `<i>italic</i>`
- **Inline keyboards** — tappable buttons replace typing "yes" / "1"
- **Message editing** — update a message after a button tap to show result inline
- **Longer messages acceptable** — phone screen, not Watch, so more content is fine
- **Reliable reply routing** — `reply_to_message.message_id` is always correct

### Formatting Rules

1. Use `<b>project name</b>` in the header for visual weight.
2. Use `<code>filename</code>` for file paths — monospace, no truncation needed.
3. Inline keyboard on every message that expects a yes/no/choice reply.
4. Edit message after button tap to show `✅ Replied: {action}` — remove keyboard.
5. File lists can show up to 8 (phone screen, not Watch).
6. No artificial line-length limit — Telegram wraps naturally.

### Message Templates

```
Notification (with buttons):
🔔 <b>payments-api</b> · Needs input

Editing: <code>src/webhooks/stripe.ts</code>
↳ Remove the legacy charge handler?
  Referenced in 2 other files.

[ ✅ Yes ]  [ ❌ No ]  [ ✏️ Direction ]

---

After button tap (edited):
🔔 <b>payments-api</b> · Needs input

↳ Remove the legacy charge handler?

✅ You replied: yes

---

Permission request:
⚡ <b>payments-api</b> · Permission needed

<code>bash: rm -rf ./legacy-handlers/</code>

[ ✅ Allow ]  [ ❌ Deny ]

---

Start:
🚀 <b>{project}</b> · Started{" (plan mode)" if plan}

{task}

{"Claude will send a plan before making changes." if plan}
{"I'll message you if I need anything." if skip}

---

Done (success):
✅ <b>{project}</b> · Done ({duration})

<code>{file1}</code>
<code>{file2}</code>
[…and N more]

[ 📋 Session info ]  [ ▶️ Follow-up ]

---

Session list:
📋 <b>Sessions</b> · {N} active

🟢 <b>payments-api</b> (12min)
   Fix stripe tests

🟡 <b>frontend</b> (parked 8min)
   Delete deprecated routes

[ 🔕 Mute api ]  [ ▶️ Resume frontend ]
[ 💀 Kill api ]  [ 💀 Kill frontend ]

---

Confirm dangerous:
⚠️ <b>Confirm before starting</b>

Project: <b>{project}</b>
Task: {task}
Mode: plan (deletion detected)

Claude will show plan first.

[ ✅ Yes, proceed ]  [ ❌ Cancel ]

---

Interactive menu:
👋 <b>Watcher</b>

[ 🆕 New session ]  [ 📋 List sessions ]
[ ⏸ Resume parked ] [ 💀 Kill session ]
[ 🛑 Stop watcher ]
```

### Callback Data Format

All inline keyboard buttons encode action and session ID in their `callback_data`:

```
"yes:{session_id}"
"no:{session_id}"
"allow:{session_id}"
"deny:{session_id}"
"direction:{session_id}"
"notify_on:{session_id}"
"notify_off:{session_id}"
"notify_imessage_on:{session_id}"
"notify_imessage_off:{session_id}"
"resume:{session_id}"
"kill:{session_id}"
"kill_confirm:{session_id}"
"kill_cancel:{session_id}"
"followup:{session_id}"
"menu:list"
"menu:new"
"menu:stop"
```

Max callback_data length: 64 bytes. Session IDs (UUIDs) are 36 chars + action prefix stays within limit.

---

## 9. File System Layout

```
~/.claude-watch/
│
├── enabled                          Global flag — existence = watcher on
├── config.json                      Main configuration
├── projects.json                    Project name → path registry
│
├── sessions/
│   └── <session_id>/
│       ├── session_id               Claude's UUID
│       ├── project_name             "payments-api"
│       ├── project_dir              Absolute path
│       ├── task                     Task string
│       ├── mode                     "plan" | "skip"
│       ├── status                   "active" | "parked" | "completed" | "expired"
│       ├── started_at               Unix epoch
│       ├── last_activity            Unix epoch
│       ├── completed_at             Unix epoch (if applicable)
│       ├── thread_guid_imessage     iMessage thread_originator_guid
│       ├── thread_guid_telegram     Telegram message_id (int) of first notification
│       ├── notify_imessage          FLAG — exists = iMessage on
│       └── notify_telegram          FLAG — exists = Telegram on
│
├── conversations/
│   └── <thread_id>/
│       ├── channel                  "imessage" | "telegram"
│       ├── state                    awaiting_project | awaiting_confirm | etc.
│       ├── partial_task
│       ├── project_name
│       ├── mode
│       └── created_at
│
├── hooks/
│   ├── session-start.sh
│   ├── notify.sh
│   └── stop.sh
│
├── send.py                          Channel-aware send entrypoint
├── watcherd.py                      Daemon entrypoint (asyncio)
│
└── logs/
    ├── watcherd.log
    └── watcherd-error.log

~/.claude/
└── settings.json                    Claude Code hooks config

~/Library/LaunchAgents/
└── com.watcher.daemon.plist         LaunchAgent
```

---

## 10. Configuration

#### `~/.claude-watch/config.json`

```json
{
  "imessage": {
    "enabled": true,
    "to": "you@icloud.com"
  },
  "telegram": {
    "enabled": true,
    "token": "7294810234:AAF...",
    "chat_id": "847392011"
  },
  "default_channels": ["telegram"],
  "timeout_minutes": 30,
  "conversation_timeout_minutes": 10,
  "max_sessions": 10,
  "log_level": "info",
  "default_mode": "skip",
  "spawn_flags": {
    "skip": ["--dangerously-skip-permissions"],
    "plan": ["--plan"]
  }
}
```

`default_channels` controls which channels are enabled for sessions spawned by watcher. Sessions started manually from the terminal get no channel flags by default (local notifications only, unless toggled later).

#### `~/.claude-watch/projects.json`

```json
{
  "default": "api",
  "projects": {
    "api":      "/Users/you/projects/my-api",
    "frontend": "/Users/you/projects/frontend",
    "payments": "/Users/you/projects/payments-api"
  }
}
```

---

## 11. Installation & Setup

### Prerequisites

- macOS 12+
- Claude Code CLI installed and authenticated
- `imsg`: `brew install steipete/tap/imsg` (iMessage only)
- `jq`: `brew install jq`
- `httpx`: `pip install httpx` (Telegram loop)
- iCloud account in Messages.app (iMessage only)
- Telegram account + @BotFather access (Telegram only)

### Permissions Required

| Permission | Channel | Why | How |
|---|---|---|---|
| Full Disk Access | iMessage only | Read `~/Library/Messages/chat.db` | System Settings → Privacy → Full Disk Access |
| Automation → Messages | iMessage only | Send via AppleScript | System Settings → Privacy → Automation |
| Telegram bot token | Telegram only | Bot API auth | @BotFather → /newbot |

### Install

```bash
# Via homebrew (future)
brew install watcher-claude

# Or from source
git clone https://github.com/you/watcher
cd watcher && ./install.sh
```

### First-Time Wizard

```bash
watcher install    # guided setup for both channels
watcher doctor     # verify everything
watcher start      # go
```

---

## 12. Security Model

### Threat Surface

Watcher has no open inbound ports. It sends to your own Apple ID or your own Telegram bot. The attack surface is:

1. Someone with access to your Apple Watch or Telegram account could spawn sessions.
2. Prompt injection via a crafted task string.
3. A compromised Mac could exfiltrate session data through either channel.

### Mitigations

1. **iMessage target is always self** — only messages from your own Apple ID processed.
2. **Telegram chat_id locked** — only messages from the configured chat_id processed. All others ignored.
3. **Task sanitisation** — `shlex.quote()` in Python, `printf '%q'` in bash before passing to `claude -p`.
4. **Project registry is explicit** — arbitrary filesystem paths cannot be passed via message. Only registered project names are accepted.
5. **Dangerous pattern detection** — destructive tasks forced to plan mode. A second explicit confirmation is required before Claude executes.
6. **No secrets in messages** — session IDs and task strings appear in messages; API keys and credentials never do.
7. **LaunchAgent runs as current user** — never root.
8. **Telegram callback data validated** — session ID extracted from callback data is verified against the session registry before any action is taken.

### What Watcher Does Not Protect Against

- Someone with access to your unlocked Telegram account or iCloud account.
- A sophisticated task string that passes heuristic assessment and then prompt-injects Claude. Mitigation: use plan mode as default (`"default_mode": "plan"` in config), review plans before approving.

---

## 13. Failure Modes & Recovery

| Failure | Detection | Recovery |
|---|---|---|
| `watcherd` crashes | LaunchAgent restarts | Automatic. Sessions persist on disk. |
| `imsg` watch exits | watcherd's iMessage loop exits → daemon exits → launchd restarts | Automatic within 5s |
| Telegram `getUpdates` fails | Exception caught in telegram_loop, sleeps 5s | Automatic retry. Missed updates recovered via offset. |
| Network drops | Both loops retry on reconnect | Automatic. iMessage queues; Telegram catches up via offset. |
| Mac sleeps mid-session | Claude continues. No messages during sleep. | Sessions remain registered. Reaper runs on wake. |
| Mac sleeps > timeout | Reaper runs on wake | Parked/expired messages sent to active channels. |
| `thread_originator_guid` missing | iMessage reply not routed | Silently ignored. User re-sends as new wake word. |
| Telegram `callback_data` session not found | Checked in callback handler | Sends "❓ Session not found" and dismisses button. |
| Full Disk Access revoked | `imsg watch` fails immediately | watcherd logs error. `watcher doctor` reports. Telegram continues working. |
| Telegram token invalid | 401 from Bot API | watcherd logs error. iMessage continues working. `watcher doctor` reports. |
| projects.json malformed | Parse error at startup | Daemon logs error. Management commands still work. New spawns fail gracefully with error message. |
| `claude --resume` fails | Non-zero exit | Watcher sends: "❓ Session not found. Use watcher {name}: {task} to start fresh." |
| Both channels misconfigured | send.py finds no valid channel | Falls back to local macOS notification. Logs warning. |

---


## 14. Resolved Design Decisions

The following questions from v0.1 have been answered and are now incorporated as requirements.

---

### 14.1 `watcher doctor` Auto-Fix

**Decision: Yes — auto-fix where safe, report where not.**

`watcher doctor` will attempt to fix the following automatically without prompting:
- Re-register missing hooks into `~/.claude/settings.json`
- Re-create missing session metadata directories
- Re-create missing `~/.claude-watch/` subdirectories
- Fix file permissions on hook scripts (`chmod +x`)

It will **report but not auto-fix**:
- Missing system permissions (Full Disk Access, Automation) — requires user action in System Settings
- Missing or invalid Telegram token — requires user to run `watcher install --telegram`
- Corrupted `projects.json` — shows diff and prompts before overwriting

Output format:
```
watcher doctor

  ✓ imsg found
  ✓ claude found
  ✓ jq found
  ✓ Full Disk Access granted
  ✗ Notification hook missing from settings.json
    → Auto-fixing... ✓ fixed
  ✗ sessions/ directory missing
    → Auto-fixing... ✓ fixed
  ✓ Telegram token valid
  ✓ All checks passed (1 auto-fixed)
```

---

### 14.2 Session Archiving

**Decision: Yes — archive completed sessions, never hard-delete.**

Completed and expired sessions are moved to `~/.claude-watch/archive/` rather than deleted. The archive is append-only. Sessions in the archive retain full metadata including task, files changed, duration, and outcome.

```
~/.claude-watch/archive/
  <session_id>/
    ... all fields from sessions/ ...
    archived_at    Unix epoch
    outcome        "completed" | "expired" | "killed"
```

The reaper moves sessions to archive rather than deleting. `watcher sessions clean` moves completed sessions to archive and reports count. `watcher clean --purge` hard-deletes the archive (with confirmation).

---

### 14.3 `watcher history` Command

**Decision: Yes — add `watcher history`.**

```
watcher history              Last 20 sessions across all projects
watcher history payments-api Last 20 for a specific project
watcher history --today      Today only
watcher history --limit 50   Custom limit
```

Terminal output:
```
watcher history payments-api

  ✓  2hr ago   3m 12s   Fix failing stripe tests
     Changed: stripe.ts, stripe.test.ts

  ✓  Yesterday  5m 41s  Delete deprecated API routes
     Changed: routes/index.ts (+6 more)

  💀 2d ago    1m 02s   Refactor auth flow
     Killed by user after 1min

  ⚠️  3d ago   12m 08s  Add rate limiting
     Finished with issues — 2 tests failing
```

Also accessible from iMessage/Telegram:
```
watcher history              → last 5 sessions, numbered for follow-up
watcher history payments     → last 5 for that project
```

---

### 14.4 `imsg` Version Check

**Decision: Include a version check at daemon startup.**

On startup, `watcherd` runs `imsg --version` and validates the output against a known-good schema version stored in `~/.claude-watch/config.json`:

```json
{
  "imsg_min_version": "1.0.0",
  "imsg_schema_hash": "sha256:abc123..."
}
```

If the version is below minimum or the JSON schema of a test `imsg history` call doesn't contain expected fields (`guid`, `reply_to_guid`, `thread_originator_guid`, `is_from_me`), the daemon:
1. Logs a warning
2. Disables the iMessage loop for this run
3. Sends a Telegram notification (if configured): `⚠️ Watcher: imsg version mismatch — iMessage disabled. Run 'watcher doctor' to fix.`
4. Continues running with Telegram only

`watcher doctor` checks the schema and updates `imsg_schema_hash` after the user confirms a new version is safe.

---

### 14.5 Voice Dictation Tolerance

**Decision: Normalise common misrecognitions + accessibility setting for custom aliases.**

#### Built-in Normalisations

The wake word parser applies these before matching:

```python
DICTATION_NORMALISATIONS = {
    "watch her":    "watcher",
    "watches":      "watcher",
    "watching":     "watcher",
    "watched":      "watcher",
    "watch err":    "watcher",
    "watcher,":     "watcher",   # trailing punctuation
}

def normalise_wake_word(text: str) -> str:
    lower = text.lower().strip()
    for pattern, replacement in DICTATION_NORMALISATIONS.items():
        if lower.startswith(pattern):
            return replacement + lower[len(pattern):]
    return lower
```

#### Disambiguation: "Watch Her" vs Watcher

Context makes this safe: "watch her" almost never precedes a colon and a technical task. The full-sentence check is:

```python
def is_wake_word_intent(text: str) -> bool:
    normalised = normalise_wake_word(text)
    # Only trigger if normalised form starts with "watcher"
    # AND (it's bare "watcher", OR followed by known project/command,
    #      OR followed by ":" which signals a task)
    if not normalised.startswith("watcher"):
        return False
    remainder = normalised[7:].strip()  # everything after "watcher"
    if not remainder:
        return True   # bare "watcher"
    if remainder.startswith(":"):
        return True   # "watcher: <task>"
    first_word = remainder.split()[0].rstrip(":")
    return (first_word in known_projects()
            or first_word in MANAGEMENT_COMMANDS)
```

"Watch her do the dishes" → normalised to "watcher do the dishes" → `remainder = "do the dishes"` → "do" is not a known project or command → **not triggered**. ✓

"Watcher payments-api: fix tests" → triggered. ✓

#### Accessibility Setting

Users can register custom aliases in config for their own dictation patterns:

```json
{
  "voice_aliases": {
    "water":         "watcher",
    "botcher":       "watcher",
    "launch claude": "watcher"
  }
}
```

`watcher install` includes a voice alias setup step:
```
Voice dictation aliases (optional):
If your device misrecognises "watcher", add aliases.
Alias (or enter to skip): water [enter]
Alias (or enter to skip): [enter]
✓ Alias saved: "water" → "watcher"
```

---

### 14.6 Fuzzy Match Confirmation Threshold

**Decision: Require confirmation for all fuzzy matches below 0.8 similarity.**

```python
from difflib import SequenceMatcher

def resolve_project(input_name: str) -> tuple[Project | None, bool]:
    """Returns (project, requires_confirmation)"""
    projects = load_projects()

    # Exact match — no confirmation
    if input_name in projects:
        return projects[input_name], False

    # Numeric selection from menu — no confirmation
    if input_name.isdigit():
        idx = int(input_name) - 1
        keys = list(projects.keys())
        if 0 <= idx < len(keys):
            return projects[keys[idx]], False

    # Fuzzy match
    best_match = None
    best_ratio = 0.0
    for name in projects:
        ratio = SequenceMatcher(None, input_name, name).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = name

    if best_ratio >= 0.8:
        # High confidence — proceed with confirmation message but single-step
        return projects[best_match], True   # still notify user of substitution
    elif best_ratio >= 0.6:
        # Low confidence — explicit yes/no required
        return projects[best_match], True
    else:
        return None, False   # no match found
```

At >= 0.8: send "Using **payments-api** (matched from 'paymants'). Reply yes to confirm or give correct name."  
At 0.6–0.8: same prompt but make it clearer the match is uncertain.  
Below 0.6: report unknown project and list all available.

---

### 14.7 Telegram Message Structure

**Decision: Private bot chat only — no group topics.**

Rationale: watcher is a single-developer tool. A private conversation with your own bot is the correct model. Telegram group topics add complexity (require supergroup, topic IDs to manage) for no benefit when the audience is always one person.

All watcher messages go to the single private chat between you and your bot, identified by `chat_id`. Sessions are distinguished by the message content and reply threading — not by chat topics.

**Message threading within the private chat:**

Each session's notification is sent as a standalone message. Replies to that specific message (via Telegram's reply feature, equivalent to iMessage's swipe-reply) carry `reply_to_message.message_id` which routes back to the correct session. The chat reads as a natural conversation log:

```
[Watcher]  🚀 payments-api · Started
[Watcher]  🔔 payments-api · Remove legacy handler? [Yes] [No]
[You]      ↩ Yes                          ← reply to the notification above
[Watcher]  ✅ payments-api · Done (3m 12s)
[Watcher]  🚀 frontend · Started
[Watcher]  🔔 frontend · Delete all v1 routes? [Approve] [Reject]
[You]      ↩ Approve                      ← reply to frontend's notification
[Watcher]  ✅ frontend · Done (5m 41s)
```

Multiple concurrent sessions interleave naturally. Reply threading keeps routing unambiguous without needing any separation.

---

### 14.8 Multiple Watcher Instances (Multi-Mac)

**Decision: Detect and resolve conflicts with user notification.**

When two Macs share the same Telegram bot token or iMessage Apple ID, both daemons will receive every incoming message and both will attempt to handle wake words and route replies — causing duplicate spawns and split routing.

#### Detection

On startup, `watcherd` writes a heartbeat record:

```
~/.claude-watch/instance.json   (on each Mac, local)

Telegram registry key: "watcher_instances" in the bot's chat
  → stored as a message the bot sends to itself and pins,
    updated on startup with machine hostname + pid + timestamp
```

The Telegram-based registry works across machines: on startup, each daemon fetches the pinned instance registry message, checks for other live instances (heartbeat within last 60 seconds), and raises a conflict if found.

#### Conflict Handling

```
Startup detects conflict:

⚠️ Multiple Watcher instances detected

  This machine:   MacBook-Pro (started just now)
  Other machine:  Mac-Mini (last seen 4min ago)

  Both machines share the same Telegram bot.
  Only one can handle messages reliably.

  [ 🖥 Use this Mac ]   [ 💻 Keep Mac Mini ]   [ ❓ Ask me later ]
```

Tapping **Use this Mac**: sends `SIGTERM` signal file to the other instance via a shared file in iCloud Drive (if available) or simply marks this machine as primary in the Telegram registry. The other machine's daemon detects it is no longer primary on next heartbeat and pauses its message handling loop (but keeps hooks running for local notifications).

Tapping **Keep Mac Mini**: this machine's daemon enters passive mode — hooks still register sessions locally, local notifications still fire, but no iMessage/Telegram messages are sent or received from this machine.

#### Heartbeat

```python
async def heartbeat_loop():
    while True:
        await update_instance_registry({
            "hostname": socket.gethostname(),
            "pid":      os.getpid(),
            "ts":       time.time(),
            "primary":  is_primary,
        })
        await asyncio.sleep(30)
```

If the primary machine goes offline (heartbeat > 90s old), the other machine automatically promotes itself to primary and sends:

```
📡 Watcher: Mac-Mini took over (MacBook-Pro offline > 90s)
Notifications now routing through Mac-Mini.
```

---

### 14.9 Channel Start Flags

**Decision: Channels are separate and independently configurable.**

```bash
watcher start                  # starts both configured channels
watcher start --imessage       # iMessage only, even if Telegram is configured
watcher start --telegram       # Telegram only, even if iMessage is configured
watcher start --no-imessage    # all channels except iMessage
watcher start --no-telegram    # all channels except Telegram
```

Channel state is persisted in `~/.claude-watch/active_channels`:

```json
["telegram"]
```

This file is read by hooks to determine which channels to send on, independently of the per-session flags. The hierarchy is:

```
active_channels (set at watcher start)
  └── global enabled flag (existence of ~/.claude-watch/enabled)
        └── per-session channel flags (notify_imessage, notify_telegram)
```

A channel not in `active_channels` never fires, regardless of session flags. This lets you run `watcher start --telegram` during the day and switch to `watcher start --imessage` at night when you want Watch-native delivery, without reconfiguring anything.

`watcher status` shows which channels are currently active:

```
Watcher status: RUNNING (pid 4821)
Active channels: Telegram only  (iMessage inactive)
```

---

### 14.10 Telegram `callback_data` Length Validation

**Decision: Validate length before sending; truncate or hash session ID if needed.**

Telegram enforces a hard 64-byte limit on `callback_data`. Current format is `action:session_id` where session IDs are Claude UUIDs (36 chars). Longest current action prefix is `notify_imessage_off:` (20 chars) + 36 = 56 bytes. Within limit today, but fragile.

#### Validation Before Send

```python
MAX_CALLBACK_DATA = 64  # Telegram hard limit

def safe_callback_data(action: str, session_id: str) -> str:
    data = f"{action}:{session_id}"
    if len(data.encode("utf-8")) <= MAX_CALLBACK_DATA:
        return data

    # Fallback: use short session reference instead of full UUID
    # Store mapping in ~/.claude-watch/callback_map.json
    short_ref = callback_map.register(session_id)  # e.g. "s1", "s2"
    data = f"{action}:{short_ref}"

    if len(data.encode("utf-8")) > MAX_CALLBACK_DATA:
        raise ValueError(f"callback_data still too long after shortening: {data}")

    return data

def build_keyboard(buttons: list[list[tuple[str, str]]]) -> dict:
    return {
        "inline_keyboard": [
            [{"text": label,
              "callback_data": safe_callback_data(*data.split(":", 1))}
             for label, data in row]
            for row in buttons
        ]
    }
```

`callback_map.json` maps short references back to full session IDs at callback time. Cleaned up when the session is archived.

#### Length Budget Table

| Action | Chars | + UUID (36) | Total | Status |
|---|---|---|---|---|
| `yes` | 3 | 40 | ✅ |
| `allow` | 5 | 42 | ✅ |
| `resume` | 6 | 43 | ✅ |
| `kill_confirm` | 12 | 49 | ✅ |
| `notify_on` | 9 | 46 | ✅ |
| `notify_off` | 10 | 47 | ✅ |
| `notify_imessage_on` | 19 | 56 | ✅ |
| `notify_imessage_off` | 20 | 57 | ✅ |
| `notify_telegram_off` | 20 | 57 | ✅ |

All current actions are within budget. The `safe_callback_data` guard ensures future additions don't silently break.

---

*End of Watcher System Specification v0.2.1-draft*
