"""Continuous observation loop (Qt-only): polls git telemetry on a timer.

Owns the ``OBSERVING`` visor state for its whole lifetime; every pass
prepends only unseen episodes via :meth:`AppState.observe_new`.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .state import AppState
from .tabs._qt import load_qt

__all__ = ["WATCH_INTERVAL_MS", "WatchController"]

WATCH_INTERVAL_MS = 15_000


class WatchController:
    """Shell-level watcher shared by the Home and Observe screens."""

    def __init__(self, state: AppState, status: Any = None,
                 interval_ms: int = WATCH_INTERVAL_MS,
                 on_tick: Optional[Callable[[int], None]] = None) -> None:
        _, QtCore, _, _ = load_qt()
        self._timer = QtCore.QTimer()
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._poll)
        self.state = state
        self.status = status
        self.on_tick = on_tick
        self.repo = ""
        self.roots: list[str] = []
        self.max_commits = 10

    @property
    def watching(self) -> bool:
        return self._timer.isActive()

    def start(self, repo: str, roots: list[str],
              max_commits: int = 10) -> tuple[bool, str]:
        """Begin watching; runs one pass immediately. (ok, message)."""
        self.repo = repo
        self.roots = list(roots)
        self.max_commits = max_commits
        try:
            fresh = self.state.observe_new(repo, roots, max_commits)
        except Exception as exc:
            return (False, str(exc))
        self.state.set_watching(True)
        self._timer.start()
        self._announce(f"Observing {repo} — {len(fresh)} new episode(s).")
        return (True, f"Observing — {len(fresh)} new episode(s).")

    def stop(self) -> None:
        self._timer.stop()
        self.state.set_watching(False)
        self._announce("Observation stopped.")

    def toggle(self, repo: str, roots: list[str],
               max_commits: int = 10) -> tuple[bool, str]:
        if self.watching:
            self.stop()
            return (True, "Observation stopped.")
        return self.start(repo, roots, max_commits)

    def _poll(self) -> None:
        try:
            fresh = self.state.observe_new(self.repo, self.roots,
                                           self.max_commits)
        except Exception as exc:
            self._announce(f"Watch pass failed: {exc}")
            return
        if fresh and self.on_tick is not None:
            try:
                self.on_tick(len(fresh))
            except Exception:
                pass
        if fresh:
            self._announce(f"{len(fresh)} new episode(s) observed.")

    def _announce(self, message: str) -> None:
        if self.status is not None:
            try:
                self.status.showMessage(message, 5000)
            except Exception:
                pass
