"""Continuous observation loop (Qt-only): polls git telemetry on a timer.

Owns the ``OBSERVING`` visor state for its whole lifetime; every pass
prepends only unseen episodes via :meth:`AppState.observe_new`.

Computational isolation (plan section 6): timer ticks run the blocking
git pass on an ``ObserveWorker`` background thread — never on the Qt
main thread — with coalescing (a tick that arrives mid-pass becomes one
follow-up pass, never a pile-up). ``start()`` keeps one synchronous
first pass so it can report ``(ok, message)`` immediately; pass
``use_worker=False`` only when a synchronous poll is required (tests).
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from .state import AppState
from .tabs._qt import load_qt

__all__ = ["WATCH_INTERVAL_MS", "WatchController"]

WATCH_INTERVAL_MS = 15_000


class WatchController:
    """Shell-level watcher shared by the Home and Observe screens."""

    def __init__(self, state: AppState, status: Any = None,
                 interval_ms: int = WATCH_INTERVAL_MS,
                 on_tick: Optional[Callable[[int], None]] = None,
                 use_worker: bool = True) -> None:
        _, QtCore, _, _ = load_qt()
        # Parent the timer into the window tree when possible (the
        # status bar is a window child in production): background passes
        # are then discoverable via findChildren, so application
        # shutdown cancels and joins them instead of destroying a
        # running QThread (which aborts the process).
        timer_parent = status if isinstance(status, QtCore.QObject) else None
        self._timer = QtCore.QTimer(timer_parent)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._poll)
        self.state = state
        self.status = status
        self.on_tick = on_tick
        self.repo = ""
        self.roots: list[str] = []
        self.max_commits = 10
        self.last_error = ""
        self._use_worker = bool(use_worker)
        self._worker: Any = None
        self._pending = False
        # Retired passes: cancelled but still running. Held (never
        # orphaned) so a running QThread is never destroyed — that
        # aborts the process ("destroyed while thread is still
        # running"). Each self-cleans on its finished signal.
        self._retired: list[Any] = []
        self._idle = threading.Event()
        self._idle.set()

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
            self.last_error = str(exc)
            return (False, str(exc))
        self.last_error = ""
        self.state.set_watching(True)
        self._timer.start()
        self._announce(f"Observing {repo} — {len(fresh)} new episode(s).")
        self._chime()
        return (True, f"Observing — {len(fresh)} new episode(s).")

    def start_async(
        self,
        repo: str,
        roots: list[str],
        max_commits: int = 10,
        on_done: Optional[Callable[[bool, str], None]] = None,
    ) -> bool:
        """Begin watching with the first pass on a worker thread.

        Returns False immediately (and reports via ``on_done``) when a
        watch is already running. Otherwise the UI thread only flips
        the visor state; the blocking first git pass runs on an
        ``ObserveWorker``, and the timer starts once it succeeds. The
        ``on_done`` callback receives the same ``(ok, message)`` pair
        :meth:`start` returns. Falls back to :meth:`start` when the
        worker module is unavailable.
        """
        if self.watching or self._worker is not None:
            self._finish_start(False, "Observation is already running.",
                               on_done)
            return False
        try:
            from .observe_worker import create_observe_worker
        except ImportError:
            ok, message = self.start(repo, roots, max_commits)
            self._finish_start(ok, message, on_done)
            return ok
        self.repo = repo
        self.roots = list(roots)
        self.max_commits = max_commits
        self.last_error = ""
        self.state.set_watching(True)
        self._idle.clear()
        worker = create_observe_worker(
            self.state, repo, list(roots), max_commits,
            parent=self._timer,
        )
        worker.observe_finished.connect(
            lambda fresh: self._on_start_finished(worker, fresh, on_done))
        worker.observe_error.connect(
            lambda message: self._on_start_failed(worker, message, on_done))
        worker.observe_cancelled.connect(
            lambda: self._on_start_failed(worker, "", on_done,
                                          cancelled=True))
        self._worker = worker
        try:
            worker.start()
        except Exception:
            self._worker = None
            self._idle.set()
            ok, message = self.start(repo, roots, max_commits)
            self._finish_start(ok, message, on_done)
            return ok
        return True

    def _finish_start(
        self,
        ok: bool,
        message: str,
        on_done: Optional[Callable[[bool, str], None]],
    ) -> None:
        if on_done is not None:
            try:
                on_done(ok, message)
            except Exception:
                pass

    def _on_start_finished(self, worker: Any, fresh: Any,
                           on_done: Optional[Callable[[bool, str], None]]
                           ) -> None:
        if not self._release_worker(worker):
            return
        self._idle.set()
        try:
            count = len(fresh) if fresh is not None else 0
        except TypeError:
            count = 0
        message = f"Observing — {count} new episode(s)."
        self._announce(f"Observing {self.repo} — {count} new episode(s).")
        self._chime()
        self._timer.start()
        self._report_fresh(fresh)
        self._finish_start(True, message, on_done)

    def _on_start_failed(self, worker: Any, message: str,
                         on_done: Optional[Callable[[bool, str], None]],
                         cancelled: bool = False) -> None:
        if not self._release_worker(worker):
            return
        self._idle.set()
        self.state.set_watching(False)
        if cancelled:
            self._finish_start(False, "Observation start cancelled.",
                               on_done)
            return
        self.last_error = message
        self._announce(f"Watch start failed: {message}")
        self._finish_start(False, message, on_done)

    def stop(self) -> None:
        self._timer.stop()
        worker, self._worker = self._worker, None
        self._pending = False
        if worker is not None:
            try:
                worker.cancel()
            except Exception:
                pass
            self._retire(worker)
        self._idle.set()
        self.state.set_watching(False)
        self._announce("Observation stopped.")
        self._chime()

    def _retire(self, worker: Any) -> None:
        """Hold a cancelled worker until its thread finishes."""
        try:
            finished = bool(worker.isFinished())
        except Exception:
            finished = False
        if finished:
            try:
                worker.deleteLater()
            except Exception:
                pass
            return
        self._retired.append(worker)
        try:
            worker.finished.connect(
                lambda: self._cleanup_retired(worker))
        except Exception:
            pass

    def _cleanup_retired(self, worker: Any) -> None:
        try:
            self._retired.remove(worker)
        except ValueError:
            pass
        try:
            worker.deleteLater()
        except Exception:
            pass

    def toggle(self, repo: str, roots: list[str],
               max_commits: int = 10) -> tuple[bool, str]:
        if self.watching:
            self.stop()
            return (True, "Observation stopped.")
        return self.start(repo, roots, max_commits)

    @property
    def poll_running(self) -> bool:
        """True while a background watch pass is in flight."""
        return self._worker is not None

    def wait_for_idle(self, timeout_s: float = 10.0) -> bool:
        """Block until no watch pass is in flight (tests only).

        Pumps the Qt event loop while waiting so worker signals are
        delivered; returns False on timeout.
        """
        import time as _time

        deadline = _time.monotonic() + max(0.0, timeout_s)
        try:
            _, _, _, QtWidgets = load_qt()
            pump = QtWidgets.QApplication.processEvents
        except Exception:
            pump = None
        while _time.monotonic() < deadline:
            if self._idle.is_set() and self._worker is None:
                return True
            if pump is not None:
                try:
                    pump()
                except Exception:
                    pass
            _time.sleep(0.01)
        return self._idle.is_set() and self._worker is None

    def _poll(self) -> None:
        if not self._use_worker:
            self._poll_sync()
            return
        try:
            from .observe_worker import coalesce_poll
        except ImportError:
            self._poll_sync()
            return
        action = coalesce_poll(running=self._worker is not None,
                               pending=self._pending)
        if action == "run":
            self._poll_async()
        elif action == "coalesce":
            self._pending = True
        # "noop": a follow-up is already queued; the tick collapses.

    def _poll_sync(self) -> None:
        try:
            fresh = self.state.observe_new(self.repo, self.roots,
                                           self.max_commits)
        except Exception as exc:
            self._announce(f"Watch pass failed: {exc}")
            return
        self._report_fresh(fresh)

    def _report_fresh(self, fresh: Any) -> None:
        if fresh and self.on_tick is not None:
            try:
                self.on_tick(len(fresh))
            except Exception:
                pass
        if fresh:
            self._announce(f"{len(fresh)} new episode(s) observed.")

    def _poll_async(self) -> None:
        try:
            from .observe_worker import create_observe_worker
        except ImportError:
            self._poll_sync()
            return
        self._idle.clear()
        worker = create_observe_worker(
            self.state, self.repo, list(self.roots), self.max_commits,
            parent=self._timer,
        )
        worker.observe_finished.connect(
            lambda fresh: self._on_poll_finished(worker, fresh))
        worker.observe_error.connect(
            lambda message: self._on_poll_failed(worker, message))
        worker.observe_cancelled.connect(
            lambda: self._on_poll_failed(worker, "", cancelled=True))
        self._worker = worker
        try:
            worker.start()
        except Exception:
            self._worker = None
            self._idle.set()
            self._poll_sync()

    def _release_worker(self, worker: Any) -> bool:
        """Forget ``worker`` if it is still the current pass."""
        if self._worker is not worker:
            return False
        self._worker = None
        try:
            worker.deleteLater()
        except Exception:
            pass
        return True

    def _drain_pending(self) -> None:
        if self._pending and self.watching:
            self._pending = False
            self._poll_async()
        else:
            self._pending = False
            self._idle.set()

    def _on_poll_finished(self, worker: Any, fresh: Any) -> None:
        if not self._release_worker(worker):
            return
        try:
            self._report_fresh(fresh)
        finally:
            self._drain_pending()

    def _on_poll_failed(self, worker: Any, message: str,
                        cancelled: bool = False) -> None:
        if not self._release_worker(worker):
            return
        try:
            if not cancelled and message:
                self._announce(f"Watch pass failed: {message}")
        finally:
            self._drain_pending()

    def _announce(self, message: str) -> None:
        if self.status is not None:
            try:
                self.status.showMessage(message, 5000)
            except Exception:
                pass

    def _chime(self) -> None:
        """Ambient confirmation tone (Settings → General can mute it)."""
        try:
            if not getattr(self.state.prefs, "chimes", True):
                return
            _, _, _, QtWidgets = load_qt()
            QtWidgets.QApplication.beep()
        except Exception:
            pass
