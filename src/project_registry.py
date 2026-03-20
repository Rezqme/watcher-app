"""Project registry — maps short names to filesystem paths."""

from difflib import SequenceMatcher, get_close_matches
from pathlib import Path

from config import load_projects, save_projects


def all_projects() -> dict[str, str]:
    """Return dict of project_name -> absolute_path."""
    data = load_projects()
    return data.get("projects", {})


def get_default() -> str:
    data = load_projects()
    return data.get("default", "")


def add_project(name: str, path: str) -> None:
    data = load_projects()
    data.setdefault("projects", {})[name] = path
    save_projects(data)


def remove_project(name: str) -> bool:
    data = load_projects()
    projects = data.get("projects", {})
    if name in projects:
        del projects[name]
        save_projects(data)
        return True
    return False


def resolve_project(input_name: str) -> tuple[tuple[str, str] | None, bool]:
    """
    Returns ((name, path), requires_confirmation) or (None, False) if no match.

    requires_confirmation is True for any fuzzy match, False for exact.
    """
    projects = all_projects()
    if not projects:
        return None, False

    # Exact match
    if input_name in projects:
        return (input_name, projects[input_name]), False

    # Numeric selection
    if input_name.isdigit():
        idx = int(input_name) - 1
        keys = list(projects.keys())
        if 0 <= idx < len(keys):
            name = keys[idx]
            return (name, projects[name]), False
        return None, False

    # Fuzzy match
    best_match: str | None = None
    best_ratio = 0.0
    for name in projects:
        ratio = SequenceMatcher(None, input_name.lower(), name.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = name

    if best_match and best_ratio >= 0.6:
        return (best_match, projects[best_match]), True

    return None, False


def list_projects_text() -> str:
    """Return a numbered text list of projects."""
    projects = all_projects()
    if not projects:
        return "No projects registered. Run 'watcher install' to add projects."
    lines = []
    for i, (name, path) in enumerate(projects.items(), 1):
        lines.append(f"  {i} · {name}  ({path})")
    return "\n".join(lines)
