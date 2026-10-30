"""Observation workers (Qt): keep git/telemetry off the UI thread.

The recurring 15-second watch poll used to run ``AppState.observe_new``
(a blocking git subprocess) directly on the Qt main thread. These
workers move that work onto ``QThread`` background threads with
coalescing (a timer tick that arrives while a pass is running collapses
into one follow-up pass instead of piling up) and cooperative cancel.

The pure helper :func:`coalesce_poll` is headless-testable;
:func:`run_trust_repo` is the blocking ``git config safe.directory``
call the trust dialog offloads through :class:`CallableWorker`.
"""

from __future__ import annotations

import subprocess
import threading
from typing import Any, Callable, Literal, Optional, Sequence

from .tabs._qt import load_qt

__all__ = [
    "CallableWorker",
    "ObserveWorker",
    "coalesce_poll",
    "create_callable_worker",
    "create_observe_worker",
    "run_trust_repo",
]

_, QtCore, _, _ = load_qt()

if hasattr(QtCore, "pyqtSignal"):
    _Signal = QtCore.pyqtSignal
else:  # PySide6
    _Signal = QtCore.Signal


def coalesce_poll(*, running: bool, pending: bool) -> Literal["run", "coalesce", "noop"]:
    """Decide what a watch tick should do (pure, headless-testable).

    - ``"run"``: idle, start a pass immediately.
    - ``"coalesce"``: a pass is running; remember one follow-up pass.
    - ``"noop"``: a pass is running and a follow-up is already queued;
      the tick collapses into it (bounded, no pile-up).
    """
    if not running and not pending:
        return "run"
    if running and not pending:
        return "coalesce"
    if not running and pending:
        return "run"
    return "noop"


def run_trust_repo(repo: str, *, timeout_sec: float = 30.0) -> str:
    """Mark ``repo`` a safe git directory (blocking; run on a worker).

    Returns the repo on success; raises :class:`RuntimeError` with the
    git stderr on failure. Never touches Qt.
    """
    if not isinstance(repo, str) or not repo.strip():
        raise ValueError("repo: expected a non-empty path")
    try:
        proc = subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory",
             repo.strip()],
            capture_output=True, text=True, timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git config timed out: {exc}") from exc
    except OSError as exc:
        raise RuntimeError(f"could not run git: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"Could not mark folder safe: {detail}")
    return repo.strip()


class ObserveWorker(QtCore.QThread):
    """Run one observation pass off the UI thread.

    ``full=False`` calls ``state.observe_new`` (watch tick semantics:
    visor untouched, fresh episodes only); ``full=True`` calls
    ``state.observe`` (replaces the episode cache).
    """

    observe_finished = _Signal(list)
    observe_error = _Signal(str)
    observe_cancelled = _Signal()

    def __init__(self, state: Any, repo: str, roots: Sequence[str],
                 max_commits: int = 10, *, full: bool = False,
                 parent: Any = None) -> None:
        super().__init__(parent)
        if not hasattr(state, "observe_new") or not hasattr(state, "observe"):
            raise ValueError(
                "state: expected AppState (observe/observe_new)"
            )
        if not isinstance(repo, str) or not repo.strip():
            raise ValueError("repo: expected a non-empty path")
        if isinstance(roots, str) or not isinstance(roots, Sequence):
            raise ValueError("roots: expected a sequence of paths")
        if not isinstance(max_commits, int) or max_commits < 1:
            raise ValueError("max_commits: expected a positive integer")
        self._state = state
        self._repo = repo
        self._roots = list(roots)
        self._max_commits = max_commits
        self._full = bool(full)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Ask the worker to drop its result (cooperative).

        The blocking git subprocess itself cannot be preempted; the
        result is discarded and ``observe_cancelled`` is emitted
        instead of ``observe_finished``.
        """
        self._cancel.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:  # Qt override (runs on the worker thread)
        if self._cancel.is_set():
            self.observe_cancelled.emit()
            return
        try:
            if self._full:
                episodes = self._state.observe(
                    self._repo, self._roots, self._max_commits
                )
            else:
                episodes = self._state.observe_new(
                    self._repo, self._roots, self._max_commits
                )
        except Exception as exc:
            if self._cancel.is_set():
                self.observe_cancelled.emit()
            else:
                self.observe_error.emit(str(exc))
            return
        if self._cancel.is_set():
            self.observe_cancelled.emit()
            return
        try:
            payload = list(episodes)
        except Exception:
            payload = []
        self.observe_finished.emit(payload)


class CallableWorker(QtCore.QThread):
    """Run one blocking callable off the UI thread (generic).

    Used for short blocking chores with no dedicated worker (trusting
    a repo, one-shot learning). Cooperative cancel: the callable runs
    to completion, but a cancelled result is dropped and
    ``callable_cancelled`` is emitted instead of ``callable_finished``.
    """

    callable_finished = _Signal(object)
    callable_error = _Signal(str)
    callable_cancelled = _Signal()

    def __init__(self, fn: Callable[..., Any],
                 args: tuple[Any, ...] = (),
                 kwargs: Optional[dict[str, Any]] = None,
                 parent: Any = None) -> None:
        super().__init__(parent)
        if not callable(fn):
            raise ValueError(
                f"fn: expected a callable, got {type(fn).__name__}"
            )
        self._fn = fn
        self._args = tuple(args) if args is not None else ()
        self._kwargs = dict(kwargs) if kwargs is not None else {}
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:  # Qt override (runs on the worker thread)
        if self._cancel.is_set():
            self.callable_cancelled.emit()
            return
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:
            if self._cancel.is_set():
                self.callable_cancelled.emit()
            else:
                self.callable_error.emit(str(exc))
            return
        if self._cancel.is_set():
            self.callable_cancelled.emit()
            return
        self.callable_finished.emit(result)


def create_observe_worker(state: Any, repo: str, roots: Sequence[str],
                          max_commits: int = 10, *, full: bool = False,
                          parent: Any = None) -> ObserveWorker:
    """Build (but do not start) an observation worker.

    Connect to ``observe_finished`` / ``observe_error`` /
    ``observe_cancelled`` first, keep a reference for the worker's
    lifetime, then call ``worker.start()``. Call ``worker.cancel()``
    to drop a stale result.
    """
    return ObserveWorker(state, repo, roots, max_commits,
                         full=full, parent=parent)


def create_callable_worker(fn: Callable[..., Any],
                           args: tuple[Any, ...] = (),
                           kwargs: Optional[dict[str, Any]] = None,
                           parent: Any = None) -> CallableWorker:
    """Build (but do not start) a generic blocking-call worker."""
    return CallableWorker(fn, args, kwargs, parent=parent)
