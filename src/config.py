"""Configuration management for Watcher."""

import json
import os
from pathlib import Path
from typing import Any

WATCH_DIR = Path.home() / ".claude-watch"
CONFIG_PATH = WATCH_DIR / "config.json"
PROJECTS_PATH = WATCH_DIR / "projects.json"
ENABLED_FLAG = WATCH_DIR / "enabled"
ACTIVE_CHANNELS_FILE = WATCH_DIR / "active_channels"
SESSIONS_DIR = WATCH_DIR / "sessions"
CONVERSATIONS_DIR = WATCH_DIR / "conversations"
ARCHIVE_DIR = WATCH_DIR / "archive"
HOOKS_DIR = WATCH_DIR / "hooks"
LOGS_DIR = WATCH_DIR / "logs"
CALLBACK_MAP_PATH = WATCH_DIR / "callback_map.json"
INSTANCE_PATH = WATCH_DIR / "instance.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "imessage": {
        "enabled": False,
        "to": "",
    },
    "telegram": {
        "enabled": False,
        "token": "",
        "chat_id": "",
    },
    "default_channels": ["telegram"],
    "timeout_minutes": 30,
    "conversation_timeout_minutes": 10,
    "max_sessions": 10,
    "log_level": "info",
    "default_mode": "skip",
    "spawn_flags": {
        "skip": ["--dangerously-skip-permissions"],
        "plan": ["--plan"],
    },
    "imsg_min_version": "1.0.0",
    "imsg_schema_hash": "",
    "voice_aliases": {},
}


def ensure_dirs() -> None:
    """Create all required directories."""
    for d in [WATCH_DIR, SESSIONS_DIR, CONVERSATIONS_DIR, ARCHIVE_DIR, HOOKS_DIR, LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    """Load config, returning defaults for missing keys."""
    ensure_dirs()
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        # Merge with defaults for any missing keys
        result = dict(DEFAULT_CONFIG)
        result.update(data)
        return result
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_CONFIG)


def save_config(config: dict[str, Any]) -> None:
    ensure_dirs()
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def load_projects() -> dict[str, Any]:
    if not PROJECTS_PATH.exists():
        return {"default": "", "projects": {}}
    try:
        with open(PROJECTS_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"default": "", "projects": {}}


def save_projects(projects: dict[str, Any]) -> None:
    ensure_dirs()
    with open(PROJECTS_PATH, "w") as f:
        json.dump(projects, f, indent=2)
        f.write("\n")


def is_enabled() -> bool:
    return ENABLED_FLAG.exists()


def set_enabled(enabled: bool) -> None:
    if enabled:
        ENABLED_FLAG.touch()
    else:
        ENABLED_FLAG.unlink(missing_ok=True)


def get_active_channels() -> list[str]:
    if not ACTIVE_CHANNELS_FILE.exists():
        config = load_config()
        return config.get("default_channels", ["telegram"])
    try:
        with open(ACTIVE_CHANNELS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def set_active_channels(channels: list[str]) -> None:
    ensure_dirs()
    with open(ACTIVE_CHANNELS_FILE, "w") as f:
        json.dump(channels, f)
        f.write("\n")
