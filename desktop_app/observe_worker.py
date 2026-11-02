"""Observation workers (Qt): keep git/telemetry/inference off the UI thread.

The recurring 15-second watch poll used to run ``AppState.observe_new``
(a blocking git subprocess) directly on the Qt main thread. These
workers move that work onto ``QThread`` background threads with
coalescing (a timer tick that arrives while a pass is running collapses
into one follow-up pass instead of piling up) and cooperative cancel.

Phase 2 added :class:`LearnWorker`: live LLM induction can block for
minutes (one model call per cluster), so the Observe tab mines on a
worker thread and commits on the UI thread (sqlite connections are
thread-bound) via ``AppState.commit_principles``.

The pure helpers :func:`coalesce_poll` and :func:`run_learn_pass` are
headless-testable; :func:`run_trust_repo` is the blocking ``git config
safe.directory`` call the trust dialog offloads through
:class:`CallableWorker`.
"""

from __future__ import annotations

import subprocess
import threading
from typing import Any, Callable, Literal, Optional, Sequence

from .tabs._qt import load_qt

__all__ = [
    "CallableWorker",
    "DEFAULT_LEARN_TIMEOUT_S",
    "LearnWorker",
    "ObserveWorker",
    "coalesce_poll",
    "create_callable_worker",
    "create_learn_worker",
    "create_observe_worker",
    "run_learn_pass",
    "run_trust_repo",
]

#: Default ceiling for one mining pass (one LLM call per cluster).
DEFAULT_LEARN_TIMEOUT_S = 300.0

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


def _prefs_from_snapshot(llm_prefs: Optional[dict[str, Any]]) -> Any:
    """Rebuild prefs from a worker-safe snapshot (never raises)."""
    from .prefs import DesktopPrefs

    prefs = DesktopPrefs()
    for key in ("model_provider", "model_name", "model_endpoint", "api_key"):
        try:
            if llm_prefs and key in llm_prefs:
                setattr(prefs, key, llm_prefs[key])
        except Exception:
            pass
    return prefs


def _resolve_learn_llm(
    llm_prefs: Optional[dict[str, Any]],
) -> Optional[Callable[[str], str]]:
    """Rebuild the active learn-time LLM on the worker thread.

    Mirrors ``AppState.learn`` AUTO_LLM semantics: prefs resolve to a
    live callable, or None for the offline stub. An unreachable Ollama
    daemon degrades to a fast actionable error (instead of a 120s
    stall per cluster) so the pass fails closed to the stub promptly.
    """
    from .state import get_llm_fn

    prefs = _prefs_from_snapshot(llm_prefs)
    try:
        from .llm import LLMError, normalize_provider

        if normalize_provider(prefs.model_provider) == "ollama":
            try:
                from .ollama import best_model_for, probe_ollama

                endpoint = (prefs.model_endpoint or "").strip()
                report = probe_ollama(
                    endpoint or "http://127.0.0.1:11434", timeout_s=0.6)
                if not report["available"]:
                    where = report["endpoint"]

                    def _ollama_down(prompt: str) -> str:
                        raise LLMError(
                            f"ollama unreachable at {where} "
                            "(start it via Settings → Model → "
                            "Start Ollama, or pick another provider)"
                        )

                    return _ollama_down
                if not (prefs.model_name or "").strip():
                    prefs.model_name = best_model_for(
                        None, report["endpoint"])
            except Exception:
                pass  # preflight failure degrades to lazy client
    except Exception:
        pass
    try:
        return get_llm_fn(prefs)
    except Exception:
        return None


def run_learn_pass(
    episode_dicts: Sequence[dict[str, Any]],
    *,
    min_cluster_size: int = 1,
    llm_prefs: Optional[dict[str, Any]] = None,
    llm_fn: Optional[Callable[[str], str]] = None,
    force_offline: bool = False,
    similarity_threshold: float = 0.5,
) -> dict[str, Any]:
    """Mine principles from episode dicts (Qt-free, no sqlite).

    The worker-thread half of learning: deserialize, resolve the
    active ``llm_fn``, run :func:`mimir.detective.infer_principles`,
    serialize. The UI thread commits the payload via
    ``AppState.commit_principles`` (sqlite is thread-bound).

    Returns ``{"principles": [...], "episode_ids": [...],
    "fell_back": bool, "live_error": str}``. An explicit ``llm_fn``
    forces live inference and its errors propagate; otherwise the
    prefs-resolved client is attempted and any failure fails closed
    to the :class:`mimir.detective.FallbackOfflineStub` (``fell_back``
    records that, with the cause in ``live_error``). ``force_offline``
    skips the live attempt entirely.
    """
    from mimir.detective import infer_principles
    from mimir.episodes import BehavioralEpisode

    if not isinstance(episode_dicts, (list, tuple)):
        raise ValueError("episode_dicts: expected a list of dicts")
    for item in episode_dicts:
        if not isinstance(item, dict):
            raise ValueError("episode_dicts: expected a list of dicts")
    if not isinstance(min_cluster_size, int) or min_cluster_size < 1:
        raise ValueError("min_cluster_size: expected a positive integer")
    if llm_fn is not None and not callable(llm_fn):
        raise ValueError("llm_fn: expected a callable or None")
    episodes = [BehavioralEpisode.from_dict(dict(item))
                for item in episode_dicts]
    episode_ids = [episode.id for episode in episodes]
    explicit = llm_fn is not None and not force_offline
    effective = None if force_offline else (
        llm_fn if explicit else _resolve_learn_llm(llm_prefs))
    fell_back = False
    live_error = ""
    try:
        principles = infer_principles(
            episodes,
            llm_fn=effective,
            min_cluster_size=min_cluster_size,
            similarity_threshold=similarity_threshold,
        )
    except Exception as exc:
        if explicit or effective is None:
            raise
        live_error = str(exc)
        principles = infer_principles(
            episodes,
            llm_fn=None,
            min_cluster_size=min_cluster_size,
            similarity_threshold=similarity_threshold,
        )
        fell_back = True
    return {
        "principles": [principle.to_dict() for principle in principles],
        "episode_ids": episode_ids,
        "fell_back": fell_back,
        "live_error": live_error,
    }


class LearnWorker(QtCore.QThread):
    """Mine behavioral principles on a background thread.

    :param episode_dicts: snapshot of ``BehavioralEpisode.to_dict()``
        dicts (the UI's episode objects never cross threads).
    :param min_cluster_size: clusters below this size are left
        unclustered (Observe tab uses 1: singletons still mine).
    :param llm_prefs: ``{"model_provider", "model_name",
        "model_endpoint", "api_key"}`` snapshot used to rebuild the
        active LLM client on the worker thread (never shared with the
        UI thread). Ignored when ``llm_fn`` is given.
    :param llm_fn: optional injected completion callable (tests and
        hosts with a custom client); ``None`` rebuilds from prefs.
    :param force_offline: skip the live attempt (offline stub only).
    :param timeout_s: ceiling for the whole pass; expiry emits
        ``learn_error`` and the late result is dropped.
    """

    #: ``{"principles": [...], "episode_ids": [...],
    #: "fell_back": bool, "live_error": str}`` — commit it on the UI
    #: thread via ``AppState.commit_principles``.
    learn_finished = _Signal(object)
    learn_error = _Signal(str)
    learn_cancelled = _Signal()

    def __init__(
        self,
        episode_dicts: Sequence[dict[str, Any]],
        *,
        min_cluster_size: int = 1,
        llm_prefs: Optional[dict[str, Any]] = None,
        llm_fn: Optional[Callable[[str], str]] = None,
        force_offline: bool = False,
        timeout_s: float = DEFAULT_LEARN_TIMEOUT_S,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(episode_dicts, (list, tuple)):
            raise ValueError("episode_dicts: expected a list of dicts")
        for item in episode_dicts:
            if not isinstance(item, dict):
                raise ValueError("episode_dicts: expected a list of dicts")
        if not isinstance(min_cluster_size, int) or min_cluster_size < 1:
            raise ValueError("min_cluster_size: expected a positive integer")
        if llm_fn is not None and not callable(llm_fn):
            raise ValueError("llm_fn: expected a callable or None")
        if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
            raise ValueError("timeout_s: expected a positive number")
        self._episodes = [dict(item) for item in episode_dicts]
        self._min_cluster_size = min_cluster_size
        self._llm_prefs = dict(llm_prefs) if llm_prefs else {}
        self._llm_fn = llm_fn
        self._force_offline = bool(force_offline)
        self._timeout = float(timeout_s)
        self._cancel = threading.Event()
        self._emitted = False
        self._emit_lock = threading.Lock()

    def cancel(self) -> None:
        """Ask the worker to stop: a queued wait aborts promptly; a
        mid-flight LLM call is abandoned and its late result dropped
        (the socket call itself cannot be preempted)."""
        self._cancel.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def _mark_emitted(self) -> bool:
        with self._emit_lock:
            if self._emitted:
                return False
            self._emitted = True
            return True

    def run(self) -> None:  # Qt override (runs on the worker thread)
        if self._cancel.is_set():
            if self._mark_emitted():
                self.learn_cancelled.emit()
            return
        pool = None
        try:
            from concurrent.futures import ThreadPoolExecutor

            pool = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="mimir-learn"
            )
            future = pool.submit(self._mine)
            import time as _time

            deadline = _time.monotonic() + self._timeout
            while True:
                if self._cancel.is_set():
                    if self._mark_emitted():
                        self.learn_cancelled.emit()
                    return
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    if self._mark_emitted():
                        self.learn_error.emit(
                            "Mining timed out after "
                            f"{self._timeout:g}s; try again or go offline."
                        )
                    return
                try:
                    payload = future.result(timeout=min(0.05, remaining))
                except TimeoutError:
                    continue
                except Exception as exc:  # mining raised
                    if self._cancel.is_set():
                        if self._mark_emitted():
                            self.learn_cancelled.emit()
                    elif self._mark_emitted():
                        self.learn_error.emit(str(exc))
                    return
                if self._cancel.is_set():
                    if self._mark_emitted():
                        self.learn_cancelled.emit()
                    return
                if self._mark_emitted():
                    self.learn_finished.emit(payload)
                return
        finally:
            if pool is not None:
                try:
                    pool.shutdown(wait=False, cancel_futures=False)
                except Exception:
                    pass

    def _mine(self) -> dict[str, Any]:
        """Blocking mining pass on the nested thread (no Qt, no sqlite)."""
        return run_learn_pass(
            self._episodes,
            min_cluster_size=self._min_cluster_size,
            llm_prefs=self._llm_prefs,
            llm_fn=self._llm_fn,
            force_offline=self._force_offline,
        )


def create_learn_worker(
    episode_dicts: Sequence[dict[str, Any]],
    **options: Any,
) -> LearnWorker:
    """Build (but do not start) a principle-mining worker.

    Connect to ``learn_finished`` / ``learn_error`` /
    ``learn_cancelled`` first, keep a reference for the worker's
    lifetime, then call ``worker.start()``. Call ``worker.cancel()``
    to abandon a stale pass.
    """
    return LearnWorker(episode_dicts, **options)
