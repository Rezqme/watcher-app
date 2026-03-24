# Watcher

**Watcher** is a zero-infrastructure remote control system for [Claude Code](https://docs.anthropic.com/claude-code), built on iMessage and Telegram. It enables a developer to start, monitor, reply to, and manage Claude Code sessions from an Apple Watch, iPhone, or any Telegram client — from any network, without hosting, and without a relay server.

## Features

- **Zero infrastructure:** No relay server, no hosting, no cloud account beyond an Apple ID and/or Telegram account. The Mac is the server.
- **Apple Watch as primary interface:** Every message is readable in a banner, and every reply is a short word or number.
- **Dual-channel notifications:** Supports iMessage (native delivery) and Telegram (interactive buttons).
- **Session management:** Handles multiple concurrent sessions, separating them into active, parked, completed, or expired states.
- **Safe by default:** Validates intent before spawning Claude tasks.

## Requirements

- Python 3
- `imsg` (`brew install steipete/tap/imsg`)
- Claude Code CLI
- `jq` (`brew install jq`)

## Installation

1. Clone this repository or copy the contents.
2. Run the interactive installer:
   ```bash
   watcher install
   ```
   *The installer will guide you through setting up iMessage and/or Telegram and adding your projects to the registry.*

3. Start the daemon:
   ```bash
   watcher start
   ```

To verify your configuration and dependencies, you can run:
```bash
watcher doctor
```

## CLI Usage

The `watcher` CLI provides a set of commands to manage the daemon and your Claude Code sessions.

```bash
watcher start                  # Start the daemon, enable hooks
watcher stop                   # Stop the daemon, disable hooks (local notifs resume)
watcher restart                # Stop then start
watcher status                 # Show daemon state + all active/parked sessions with channel info
watcher sessions               # List sessions (alias for status)
watcher sessions kill <name>   # Kill a named session
watcher sessions clean         # Remove all completed and expired sessions
watcher notify on <name>       # Enable iMessage+Telegram for named session
watcher notify off <name>      # Disable all notifications for named session
watcher notify imessage <name> # Toggle iMessage only for named session
watcher notify telegram <name> # Toggle Telegram only for named session
watcher recent                 # Show sessions from the last 24 hours
watcher history [<project>]    # View session history
watcher config                 # Open config in $EDITOR
watcher logs                   # Tail daemon logs
watcher install                # Run first-time setup wizard
watcher doctor                 # Check permissions, dependencies, config validity
```

## How It Works

Watcher integrates with Claude Code using standard hooks (`SessionStart`, `Notification`, and `Stop`), injecting them into your `~/.claude/settings.json`.

- When Claude requires input or stops, the watcher daemon routes a message to you via your preferred channels.
- When you send a command like `watcher <project>: <task>` via iMessage or Telegram, the daemon invokes Claude Code and links the ongoing thread to that session.
- Both iMessage and Telegram run their own loops concurrently in a single `watcherd` background process.

For a comprehensive breakdown of the system architecture, formats, and design goals, refer to the [`watcher-spec.md`](watcher-spec.md).
