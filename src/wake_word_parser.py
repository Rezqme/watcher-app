"""Wake word parser — parses incoming messages for watcher commands."""

import re
from typing import Literal

from config import load_config
from project_registry import all_projects

MANAGEMENT_COMMANDS = {
    "list", "status", "kill", "clean", "stop", "help",
    "recent", "notify", "history", "start", "restart",
}

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

TaskAssessment = Literal["clear", "vague", "dangerous"]


def _get_voice_aliases() -> dict[str, str]:
    config = load_config()
    return config.get("voice_aliases", {})


def normalise_wake_word(text: str) -> str:
    """Apply dictation normalisations to text."""
    DICTATION_NORMALISATIONS = {
        "watch her": "watcher",
        "watches": "watcher",
        "watching": "watcher",
        "watched": "watcher",
        "watch err": "watcher",
        "watcher,": "watcher",
    }
    lower = text.lower().strip()

    # Apply user-defined voice aliases first
    for alias, replacement in _get_voice_aliases().items():
        if lower.startswith(alias.lower()):
            lower = replacement.lower() + lower[len(alias):]
            break

    for pattern, replacement in DICTATION_NORMALISATIONS.items():
        if lower.startswith(pattern):
            return replacement + lower[len(pattern):]

    return lower


def is_wake_word_intent(text: str) -> bool:
    """Return True if the message is intended as a watcher command."""
    normalised = normalise_wake_word(text)
    if not normalised.startswith("watcher"):
        return False
    remainder = normalised[7:].strip()
    if not remainder:
        return True  # bare "watcher"
    if remainder.startswith(":"):
        return True  # "watcher: <task>"
    # If remainder contains ":", it's "watcher <project>: <task>" — always a spawn intent
    if ":" in remainder:
        return True
    first_word = remainder.split()[0].rstrip(":").lower()
    return (first_word in all_projects() or first_word in MANAGEMENT_COMMANDS)


def assess_task(task: str) -> TaskAssessment:
    """Assess whether a task is clear, vague, or dangerous."""
    t = task.lower()
    if any(re.search(p, t) for p in DANGEROUS_PATTERNS):
        return "dangerous"
    if any(re.search(p, t) for p in VAGUE_PATTERNS):
        return "vague"
    return "clear"


def choose_mode(assessment: TaskAssessment) -> str:
    """Choose spawn mode based on task assessment."""
    return "plan" if assessment in ("dangerous", "vague") else "skip"


def parse_wake_word(text: str) -> dict:
    """
    Parse a watcher message into components.

    Returns dict with keys:
      - type: "spawn" | "management" | "bare"
      - project: str | None
      - task: str | None
      - command: str | None  (for management type)
      - args: list[str]
      - raw: str
    """
    normalised = normalise_wake_word(text)

    if not normalised.startswith("watcher"):
        return {"type": "unknown", "raw": text}

    remainder = normalised[7:].strip()

    if not remainder:
        return {"type": "bare", "raw": text, "project": None, "task": None,
                "command": None, "args": []}

    # "watcher: <task>" — no project specified
    if remainder.startswith(":"):
        task = remainder[1:].strip()
        return {"type": "spawn", "project": None, "task": task,
                "command": None, "args": [], "raw": text}

    # Parse "watcher [target] [: task]"
    parts = remainder.split(":", 1)
    target_part = parts[0].strip()
    task_part = parts[1].strip() if len(parts) > 1 else None

    first_word = target_part.split()[0].lower()

    if first_word in MANAGEMENT_COMMANDS:
        args = target_part.split()[1:]
        if task_part:
            args.append(task_part)
        return {"type": "management", "command": first_word,
                "args": args, "project": None, "task": task_part, "raw": text}

    # Target is a project name (or attempt)
    project_token = first_word
    remaining_target = " ".join(target_part.split()[1:])

    # task may follow after colon, or be in remaining_target
    if task_part:
        task = task_part
    elif remaining_target:
        task = remaining_target
    else:
        task = None

    return {"type": "spawn", "project": project_token, "task": task,
            "command": None, "args": [], "raw": text}
