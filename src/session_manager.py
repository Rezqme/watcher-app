"""Session manager — manages Claude Code session metadata on disk."""

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from config import ARCHIVE_DIR, SESSIONS_DIR, load_config

SessionStatus = Literal["active", "parked", "completed", "expired"]


@dataclass
class Session:
    session_id: str
    project_name: str
    project_dir: str
    task: str
    mode: str  # "plan" | "skip"
    status: SessionStatus
    started_at: float
    last_activity: float
    completed_at: float | None = None
    thread_guid_imessage: str = ""
    thread_guid_telegram: str = ""
    # channel flags — represented as booleans (backed by flag files)
    notify_imessage: bool = False
    notify_telegram: bool = False

    @property
    def path(self) -> Path:
        return SESSIONS_DIR / self.session_id

    @property
    def elapsed_seconds(self) -> float:
        end = self.completed_at or time.time()
        return end - self.started_at

    @property
    def elapsed_str(self) -> str:
        secs = int(self.elapsed_seconds)
        if secs < 60:
            return f"{secs}s"
        m, s = divmod(secs, 60)
        return f"{m}m {s}s"

    @property
    def idle_minutes(self) -> float:
        return (time.time() - self.last_activity) / 60


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _write(path: Path, value: str) -> None:
    path.write_text(value + "\n")


def _load_session(session_dir: Path) -> Session | None:
    if not session_dir.is_dir():
        return None
    sid = _read(session_dir / "session_id")
    if not sid:
        sid = session_dir.name
    try:
        started_at = float(_read(session_dir / "started_at") or "0")
        last_activity = float(_read(session_dir / "last_activity") or str(started_at))
        completed_raw = _read(session_dir / "completed_at")
        completed_at = float(completed_raw) if completed_raw else None
    except ValueError:
        started_at = 0.0
        last_activity = 0.0
        completed_at = None

    return Session(
        session_id=sid,
        project_name=_read(session_dir / "project_name"),
        project_dir=_read(session_dir / "project_dir"),
        task=_read(session_dir / "task"),
        mode=_read(session_dir / "mode") or "skip",
        status=_read(session_dir / "status") or "active",  # type: ignore[arg-type]
        started_at=started_at,
        last_activity=last_activity,
        completed_at=completed_at,
        thread_guid_imessage=_read(session_dir / "thread_guid_imessage"),
        thread_guid_telegram=_read(session_dir / "thread_guid_telegram"),
        notify_imessage=(session_dir / "notify_imessage").exists(),
        notify_telegram=(session_dir / "notify_telegram").exists(),
    )


class SessionManager:
    def create(
        self,
        session_id: str,
        project_name: str,
        project_dir: str,
        task: str,
        mode: str,
        channels: list[str],
    ) -> Session:
        session_dir = SESSIONS_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()

        _write(session_dir / "session_id", session_id)
        _write(session_dir / "project_name", project_name)
        _write(session_dir / "project_dir", project_dir)
        _write(session_dir / "task", task)
        _write(session_dir / "mode", mode)
        _write(session_dir / "status", "active")
        _write(session_dir / "started_at", str(now))
        _write(session_dir / "last_activity", str(now))
        _write(session_dir / "thread_guid_imessage", "")
        _write(session_dir / "thread_guid_telegram", "")

        if "imessage" in channels:
            (session_dir / "notify_imessage").touch()
        if "telegram" in channels:
            (session_dir / "notify_telegram").touch()

        return _load_session(session_dir)  # type: ignore[return-value]

    def find_by_id(self, session_id: str) -> Session | None:
        return _load_session(SESSIONS_DIR / session_id)

    def find_by_name(self, project_name: str) -> list[Session]:
        result = []
        for s in self.all():
            if s.project_name.lower() == project_name.lower():
                result.append(s)
        return result

    def find_by_thread(self, thread_id: str, channel: str) -> Session | None:
        if not thread_id:
            return None
        for s in self.all():
            if channel == "imessage" and s.thread_guid_imessage == thread_id:
                return s
            if channel == "telegram" and s.thread_guid_telegram == thread_id:
                return s
        return None

    def all(self) -> list[Session]:
        sessions = []
        if not SESSIONS_DIR.exists():
            return sessions
        for d in SESSIONS_DIR.iterdir():
            if d.is_dir():
                s = _load_session(d)
                if s:
                    sessions.append(s)
        return sorted(sessions, key=lambda s: s.started_at, reverse=True)

    def active(self) -> list[Session]:
        return [s for s in self.all() if s.status == "active"]

    def parked(self) -> list[Session]:
        return [s for s in self.all() if s.status == "parked"]

    def recent(self, hours: int = 24) -> list[Session]:
        cutoff = time.time() - (hours * 3600)
        return [s for s in self.all() if s.started_at >= cutoff]

    def update_status(self, session_id: str, status: SessionStatus) -> None:
        session_dir = SESSIONS_DIR / session_id
        if session_dir.exists():
            _write(session_dir / "status", status)
            if status == "completed":
                _write(session_dir / "completed_at", str(time.time()))

    def update_activity(self, session_id: str) -> None:
        session_dir = SESSIONS_DIR / session_id
        if session_dir.exists():
            _write(session_dir / "last_activity", str(time.time()))

    def set_thread_guid(self, session_id: str, channel: str, guid: str) -> None:
        session_dir = SESSIONS_DIR / session_id
        if session_dir.exists():
            fname = f"thread_guid_{channel}"
            _write(session_dir / fname, guid)

    def set_channel(self, session_id: str, channel: str, enabled: bool) -> None:
        session_dir = SESSIONS_DIR / session_id
        if not session_dir.exists():
            return
        flag_file = session_dir / f"notify_{channel}"
        if enabled:
            flag_file.touch()
        else:
            flag_file.unlink(missing_ok=True)

    def kill(self, session_id: str) -> bool:
        session = self.find_by_id(session_id)
        if not session:
            return False
        # Try to kill the process if we stored a PID
        session_dir = SESSIONS_DIR / session_id
        pid_file = session_dir / "pid"
        if pid_file.exists():
            try:
                pid = int(_read(pid_file))
                os.kill(pid, 15)  # SIGTERM
            except (ValueError, ProcessLookupError, PermissionError):
                pass
        self.archive(session_id, outcome="killed")
        return True

    def archive(self, session_id: str, outcome: str = "completed") -> None:
        session_dir = SESSIONS_DIR / session_id
        if not session_dir.exists():
            return
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        archive_dest = ARCHIVE_DIR / session_id
        _write(session_dir / "archived_at", str(time.time()))
        _write(session_dir / "outcome", outcome)
        if archive_dest.exists():
            shutil.rmtree(archive_dest)
        shutil.copytree(session_dir, archive_dest)
        shutil.rmtree(session_dir)

    def clean(self) -> int:
        """Archive all completed/expired sessions. Returns count."""
        count = 0
        for s in self.all():
            if s.status in ("completed", "expired"):
                self.archive(s.session_id, outcome=s.status)
                count += 1
        return count

    def reap(self) -> None:
        """Move timed-out sessions to parked/expired."""
        config = load_config()
        timeout_min = config.get("timeout_minutes", 30)
        for s in self.all():
            if s.status == "active":
                if s.idle_minutes > timeout_min:
                    self.update_status(s.session_id, "parked")
            elif s.status == "parked":
                if s.idle_minutes > timeout_min * 2:
                    self.update_status(s.session_id, "expired")
                    self.archive(s.session_id, outcome="expired")

    def spawn(
        self,
        project_dir: str,
        task: str,
        mode: str,
        channels: list[str],
        project_name: str,
    ) -> Session | None:
        """Spawn a new Claude Code session and register it."""
        config = load_config()
        spawn_flags = config.get("spawn_flags", {})
        mode_flags = spawn_flags.get(mode, [])

        cmd = ["claude"] + mode_flags + ["-p", task]

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=project_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            # Claude prints the session ID on startup — read first line
            # In practice, Claude may output session ID differently;
            # we'll use the PID as a fallback identifier
            import uuid
            session_id = str(uuid.uuid4())

            session = self.create(
                session_id=session_id,
                project_name=project_name,
                project_dir=project_dir,
                task=task,
                mode=mode,
                channels=channels,
            )
            # Store PID for potential kill
            _write(SESSIONS_DIR / session_id / "pid", str(proc.pid))
            return session
        except (OSError, FileNotFoundError):
            return None

    def resume(self, session: Session, message: str) -> bool:
        """Resume a parked session with a message."""
        cmd = ["claude", "--resume", session.session_id, "-p", message]
        try:
            subprocess.Popen(
                cmd,
                cwd=session.project_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.update_status(session.session_id, "active")
            self.update_activity(session.session_id)
            return True
        except (OSError, FileNotFoundError):
            return False


# Singleton
session_manager = SessionManager()
