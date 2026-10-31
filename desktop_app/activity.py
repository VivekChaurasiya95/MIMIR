"""Live activity understanding for the wandering cobot (Qt-free).

While Habi roams or follows the cursor, :func:`note_activity` samples
what the user is actually doing (focused window, recent files, recent
episodes) and asks the on-device Llama-3.2 foundation for a one-line
read of the task. Everything fails closed: no daemon, no pywin32, or
no context all yield ``None`` (stay silent) instead of a guess.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

__all__ = ["gather_activity_snapshot", "note_activity"]


def gather_activity_snapshot(state: Any) -> dict[str, Any]:
    """Sample live context from an :class:`AppState` (never raises)."""
    snapshot: dict[str, Any] = {
        "foreground": "",
        "repo": "",
        "recent_files": [],
        "recent_episodes": [],
        "watching": False,
    }
    try:
        from mimir.foundation import foreground_window_info

        info = foreground_window_info() or {}
        snapshot["foreground"] = str(info.get("title", "") or "")
    except Exception:
        pass
    try:
        last_repo = str(getattr(getattr(state, "prefs", None),
                                "last_repo", "") or "")
        if last_repo:
            try:
                snapshot["repo"] = Path(last_repo).name or last_repo
            except Exception:
                snapshot["repo"] = last_repo
    except Exception:
        pass
    try:
        snapshot["watching"] = bool(getattr(state, "watching", False))
    except Exception:
        pass
    try:
        episodes = list(getattr(state, "episodes", []) or [])[:4]
    except Exception:
        episodes = []
    summaries: list[str] = []
    files: list[str] = []
    for episode in episodes:
        try:
            summary = str(getattr(episode, "summary", "") or "").strip()
        except Exception:
            summary = ""
        if summary:
            summaries.append(summary[:160])
        try:
            transitions = list(getattr(episode, "transitions", []) or [])
        except Exception:
            transitions = []
        for transition in transitions[:3]:
            try:
                path = str(getattr(transition, "path", "") or "").strip()
            except Exception:
                path = ""
            if path and path not in files:
                files.append(Path(path).name or path)
    snapshot["recent_episodes"] = summaries[:4]
    snapshot["recent_files"] = files[:6]
    return snapshot


def note_activity(
    state: Any,
    snapshot: Optional[Mapping[str, Any]] = None,
    *,
    timeout_s: float = 3.0,
) -> Optional[str]:
    """One-line read of the user's current task (None when unsure).

    Pure orchestration over :func:`gather_activity_snapshot` +
    :func:`mimir.foundation.describe_activity`; safe to run on a
    worker thread. Returns ``None`` without context worth narrating
    (unknown window, no episodes, daemon down).
    """
    try:
        snap = dict(snapshot) if snapshot is not None \
            else gather_activity_snapshot(state)
    except Exception:
        return None
    if not (str(snap.get("foreground", "") or "").strip()
            or list(snap.get("recent_episodes", []) or [])
            or list(snap.get("recent_files", []) or [])):
        return None
    try:
        prefs = getattr(state, "prefs", None)
        endpoint = (getattr(prefs, "model_endpoint", "") or "").strip() \
            or "http://127.0.0.1:11434"
        model = (getattr(prefs, "model_name", "") or "").strip() or None
        from mimir.foundation import describe_activity

        return describe_activity(snap, endpoint=endpoint, model=model,
                                 timeout_s=timeout_s)
    except Exception:
        return None
