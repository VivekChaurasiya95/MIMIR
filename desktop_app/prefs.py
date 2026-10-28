"""Desktop preferences + recent-task log (Qt-free, JSON-backed).

Settings edited in the Settings surface persist here so the hero greeting,
workspace, synthesis defaults, and recent tasks survive restarts.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["DEFAULT_PREFS_PATH", "DesktopPrefs", "MAX_RECENT_TASKS"]

MAX_RECENT_TASKS = 20
DEFAULT_PREFS_PATH = str(Path.home() / ".mimir" / "desktop.json")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class DesktopPrefs:
    """Persisted desktop choices (all JSON-serializable)."""

    user_name: str = "Arin"
    assistant_name: str = "MIMIR"
    language: str = "English (US)"
    start_on_launch: bool = True
    show_in_tray: bool = True
    default_top_k: int = 3
    default_min_confidence: float = 0.0
    last_repo: str = ""
    last_roots: tuple[str, ...] = ()
    inactive_roots: tuple[str, ...] = ()
    recent_tasks: tuple[dict[str, str], ...] = ()

    def __post_init__(self) -> None:
        self.last_roots = tuple(self.last_roots)
        self.inactive_roots = tuple(self.inactive_roots)
        self.recent_tasks = tuple(self.recent_tasks)

    @classmethod
    def load(cls, path: str = DEFAULT_PREFS_PATH) -> "DesktopPrefs":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in raw.items() if k in known}
        try:
            return cls(**clean)
        except TypeError:
            return cls()

    def save(self, path: str = DEFAULT_PREFS_PATH) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        payload["last_roots"] = list(self.last_roots)
        payload["inactive_roots"] = list(self.inactive_roots)
        payload["recent_tasks"] = list(self.recent_tasks)
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def active_roots(self, fallback: list[str] | None = None) -> list[str]:
        roots = list(self.last_roots) or list(fallback or [])
        return [r for r in roots if r not in set(self.inactive_roots)]

    def record_task(self, title: str, project: str, verdict: str,
                    at: str | None = None) -> "DesktopPrefs":
        entry = {"title": title.strip() or "(untitled task)",
                 "project": project.strip() or "Inbox",
                 "verdict": verdict,
                 "at": at or _utc_now_iso()}
        tasks = (entry,) + tuple(t for t in self.recent_tasks
                                 if t.get("title") != entry["title"]
                                 or t.get("at") != entry["at"])
        return DesktopPrefs(**{**asdict(self),
                               "recent_tasks": tasks[:MAX_RECENT_TASKS]})

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["last_roots"] = list(self.last_roots)
        payload["inactive_roots"] = list(self.inactive_roots)
        payload["recent_tasks"] = list(self.recent_tasks)
        return payload
