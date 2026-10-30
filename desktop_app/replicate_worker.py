"""Inference worker (Qt): LLM synthesis off the UI thread.

``AppState.replicate`` can block for up to two live-LLM round trips
(60-120s socket timeouts each) plus store queries. Calling it from the
Replicate tab froze the whole companion. :class:`ReplicateWorker` runs
that synthesis on a ``QThread`` with progress-safe signals, cooperative
cancel, and a timeout.

Thread-safety: ``PrincipleStore`` is a single SQLite connection and is
documented NOT thread-safe, so the worker never touches the UI's
store. The tab snapshots ``state.list_principles()`` (a fast local
read) into plain dicts; the worker seeds those into a private temp-file
store on its own thread and deletes it afterwards. Snapshot semantics
also dodge ``:memory:`` stores, which cannot be shared across
connections at all.
"""

from __future__ import annotations

import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional, Sequence

from mimir.actor import ReplicationActor, ReplicationRequest
from mimir.principles import BehavioralPrinciple
from mimir.store import PrincipleStore

from .tabs._qt import load_qt

__all__ = ["ReplicateWorker", "create_replicate_worker"]

#: Default ceiling for one synthesis (live attempt + offline fallback).
DEFAULT_REPLICATE_TIMEOUT_S = 180.0

_, QtCore, _, _ = load_qt()

if hasattr(QtCore, "pyqtSignal"):
    _Signal = QtCore.pyqtSignal
else:  # PySide6
    _Signal = QtCore.Signal


class ReplicateWorker(QtCore.QThread):
    """Synthesize a styled draft on a background thread.

    :param principles: snapshot of ``BehavioralPrinciple.to_dict()``
        dicts seeding the worker's private store.
    :param llm_prefs: ``{"model_provider", "model_name",
        "model_endpoint", "api_key", "contrast_baseline"}`` snapshot
        used to rebuild the LLM client on the worker thread (never
        shared with the UI thread). Ignored when ``llm_fn`` is given.
    :param llm_fn: optional injected completion callable (tests and
        hosts with a custom client); ``None`` rebuilds from prefs.
    :param force_offline: skip the live attempt (offline scaffold).
    :param timeout_s: ceiling for the whole synthesis; expiry emits
        ``replicate_error`` and the late result is dropped.
    """

    #: ``{"result": ReplicationResult, "fell_back": bool,
    #: "live_error": str}`` — ``fell_back`` mirrors the tab's old
    #: live-then-offline retry, now inside the worker.
    replicate_finished = _Signal(object)
    replicate_error = _Signal(str)
    replicate_cancelled = _Signal()

    def __init__(
        self,
        task: str,
        principles: Sequence[dict[str, Any]],
        *,
        target_language: Optional[str] = None,
        target_scope: Optional[str] = None,
        min_confidence: float = 0.0,
        top_k: int = 5,
        llm_prefs: Optional[dict[str, Any]] = None,
        llm_fn: Optional[Callable[[str], str]] = None,
        force_offline: bool = False,
        timeout_s: float = DEFAULT_REPLICATE_TIMEOUT_S,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task: expected a non-empty string")
        if not isinstance(principles, (list, tuple)):
            raise ValueError("principles: expected a list of dicts")
        for item in principles:
            if not isinstance(item, dict):
                raise ValueError("principles: expected a list of dicts")
        if not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k: expected a positive integer")
        if (
            not isinstance(min_confidence, (int, float))
            or not (0.0 <= float(min_confidence) <= 1.0)
        ):
            raise ValueError("min_confidence: expected 0.0..1.0")
        if llm_fn is not None and not callable(llm_fn):
            raise ValueError("llm_fn: expected a callable or None")
        if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
            raise ValueError("timeout_s: expected a positive number")
        self._task = task.strip()
        self._principles = [dict(item) for item in principles]
        self._target_language = target_language
        self._target_scope = target_scope
        self._min_confidence = float(min_confidence)
        self._top_k = top_k
        self._llm_prefs = dict(llm_prefs) if llm_prefs else {}
        self._llm_fn = llm_fn
        self._force_offline = bool(force_offline)
        self._timeout = float(timeout_s)
        self._cancel = threading.Event()
        self._emitted = False
        self._emit_lock = threading.Lock()

    def cancel(self) -> None:
        """Ask the worker to stop: a queued wait aborts promptly; a
        mid-flight HTTP call is abandoned and its late result dropped
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
                self.replicate_cancelled.emit()
            return
        pool: Optional[ThreadPoolExecutor] = None
        try:
            pool = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="mimir-replicate"
            )
            future = pool.submit(self._synthesize)
            import time as _time

            deadline = _time.monotonic() + self._timeout
            while True:
                if self._cancel.is_set():
                    if self._mark_emitted():
                        self.replicate_cancelled.emit()
                    return
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    if self._mark_emitted():
                        self.replicate_error.emit(
                            "Synthesis timed out after "
                            f"{self._timeout:g}s; try again or go offline."
                        )
                    return
                try:
                    payload = future.result(timeout=min(0.05, remaining))
                except TimeoutError:
                    continue
                except Exception as exc:  # synthesis raised
                    if self._cancel.is_set():
                        if self._mark_emitted():
                            self.replicate_cancelled.emit()
                    elif self._mark_emitted():
                        self.replicate_error.emit(str(exc))
                    return
                if self._cancel.is_set():
                    if self._mark_emitted():
                        self.replicate_cancelled.emit()
                    return
                if self._mark_emitted():
                    self.replicate_finished.emit(payload)
                return
        finally:
            if pool is not None:
                try:
                    pool.shutdown(wait=False, cancel_futures=False)
                except Exception:
                    pass

    def _synthesize(self) -> dict[str, Any]:
        """Blocking synthesis on the nested thread (private store)."""
        if self._cancel.is_set():
            raise _Cancelled()
        path = ""
        try:
            handle, path = tempfile.mkstemp(
                prefix="mimir_replicate_", suffix=".db"
            )
            os.close(handle)
            store = PrincipleStore(path)
            try:
                for item in self._principles:
                    try:
                        store.save(BehavioralPrinciple.from_dict(item))
                    except Exception:
                        continue  # one bad snapshot entry never blocks
                effective = self._resolve_llm_fn()
                contrast = self._contrast_wanted() and effective is not None
                actor = ReplicationActor(store, llm_fn=effective)
                request = ReplicationRequest(
                    task_description=self._task,
                    target_language=self._target_language or None,
                    target_scope=self._target_scope or None,
                    min_confidence=self._min_confidence,
                    top_k_principles=self._top_k,
                )
                fell_back = False
                live_error = ""
                try:
                    result = actor.replicate(
                        request, generate_contrast_baseline=contrast
                    )
                except Exception as exc:
                    if effective is None or self._cancel.is_set():
                        raise
                    live_error = str(exc)
                    offline = ReplicationActor(store, llm_fn=None)
                    result = offline.replicate(
                        request, generate_contrast_baseline=False
                    )
                    fell_back = True
                return {
                    "result": result,
                    "fell_back": fell_back,
                    "live_error": live_error,
                }
            finally:
                try:
                    store.close()
                except Exception:
                    pass
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def _resolve_llm_fn(self) -> Optional[Callable[[str], str]]:
        if self._llm_fn is not None:
            return None if self._force_offline else self._llm_fn
        if self._force_offline:
            return None
        try:
            from .prefs import DesktopPrefs
            from .state import get_llm_fn

            prefs = DesktopPrefs()
            for key in (
                "model_provider",
                "model_name",
                "model_endpoint",
                "api_key",
                "contrast_baseline",
            ):
                if key in self._llm_prefs:
                    try:
                        setattr(prefs, key, self._llm_prefs[key])
                    except Exception:
                        pass
            return get_llm_fn(prefs)
        except Exception:
            return None  # prefs/client failure degrades to offline

    def _contrast_wanted(self) -> bool:
        value = self._llm_prefs.get("contrast_baseline", True)
        return bool(value) if isinstance(value, bool) else True


class _Cancelled(Exception):
    """Internal: synthesis noticed cancel before starting work."""


# _Cancelled must surface as cancellation, not error: translate at the
# boundary inside run() by checking the flag (already done above).


def create_replicate_worker(
    task: str,
    principles: Sequence[dict[str, Any]],
    **options: Any,
) -> ReplicateWorker:
    """Build (but do not start) an inference worker.

    Connect to ``replicate_finished`` / ``replicate_error`` /
    ``replicate_cancelled`` first, keep a reference for the worker's
    lifetime, then call ``worker.start()``. Call ``worker.cancel()``
    to abandon a stale synthesis.
    """
    return ReplicateWorker(task, principles, **options)
