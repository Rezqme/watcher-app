"""Conversation state manager — tracks in-progress watcher conversations."""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from config import CONVERSATIONS_DIR, load_config

ConvState = Literal[
    "awaiting_project",
    "awaiting_confirm",
    "awaiting_menu",
    "awaiting_task",
    "awaiting_direction",
]


@dataclass
class Conversation:
    thread_id: str         # iMessage thread_originator_guid OR Telegram message_id (str)
    channel: str           # "imessage" | "telegram"
    state: ConvState
    partial_task: str = ""
    project_name: str = ""
    project_dir: str = ""
    mode: str = ""
    session_id: str = ""   # when waiting on direction for an existing session
    created_at: float = field(default_factory=time.time)

    @property
    def path(self) -> Path:
        safe = self.thread_id.replace("/", "_").replace(":", "_")
        return CONVERSATIONS_DIR / safe


def _load_conversation(conv_dir: Path) -> Conversation | None:
    meta = conv_dir / "meta.json"
    if not meta.exists():
        return None
    try:
        with open(meta) as f:
            data = json.load(f)
        return Conversation(**data)
    except (json.JSONDecodeError, TypeError, OSError):
        return None


def _save_conversation(conv: Conversation) -> None:
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    conv.path.mkdir(parents=True, exist_ok=True)
    with open(conv.path / "meta.json", "w") as f:
        json.dump(asdict(conv), f, indent=2)
        f.write("\n")


class ConversationManager:
    def create(
        self,
        thread_id: str,
        channel: str,
        state: ConvState,
        partial_task: str = "",
        project_name: str = "",
        project_dir: str = "",
        mode: str = "",
    ) -> Conversation:
        conv = Conversation(
            thread_id=thread_id,
            channel=channel,
            state=state,
            partial_task=partial_task,
            project_name=project_name,
            project_dir=project_dir,
            mode=mode,
        )
        _save_conversation(conv)
        return conv

    def create_direction_prompt(self, thread_id: str | int, session_id: str) -> Conversation:
        """Create a transient conversation waiting for user direction text."""
        conv = Conversation(
            thread_id=str(thread_id),
            channel="telegram",
            state="awaiting_direction",
            session_id=session_id,
        )
        _save_conversation(conv)
        return conv

    def find(self, thread_id: str, channel: str) -> Conversation | None:
        safe = str(thread_id).replace("/", "_").replace(":", "_")
        conv_dir = CONVERSATIONS_DIR / safe
        conv = _load_conversation(conv_dir)
        if conv and conv.channel == channel:
            return conv
        # Also search all conversations for matching thread_id
        for d in CONVERSATIONS_DIR.iterdir():
            if d.is_dir():
                c = _load_conversation(d)
                if c and c.thread_id == str(thread_id) and c.channel == channel:
                    return c
        return None

    def update(self, conv: Conversation) -> None:
        _save_conversation(conv)

    def delete(self, conv: Conversation) -> None:
        import shutil
        if conv.path.exists():
            shutil.rmtree(conv.path)

    def reap(self) -> None:
        """Remove expired conversations (10 min timeout)."""
        config = load_config()
        timeout_sec = config.get("conversation_timeout_minutes", 10) * 60
        now = time.time()
        for d in CONVERSATIONS_DIR.iterdir():
            if d.is_dir():
                c = _load_conversation(d)
                if c and (now - c.created_at) > timeout_sec:
                    import shutil
                    shutil.rmtree(d, ignore_errors=True)


# Singleton
conversation_manager = ConversationManager()
