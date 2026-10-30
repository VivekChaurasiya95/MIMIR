"""Live repository-state observation (plan section 3.1C).

Polls branch, HEAD, and working-tree status and publishes
``GIT_CHANGED`` only on real transitions (commit creation, branch
changes, staged/unstaged modifications). Complements live filesystem
monitoring: uncommitted changes stay visible without shipping diff
content.

Backends (plan section 9):

- GitPython when it is importable.
- Controlled git subprocess operations via
  :func:`mimir.telemetry.read_git_state` otherwise.

Both backends enforce the telemetry ``allowed_roots`` boundary.
"""

from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import Path
from typing import Literal, Optional, Sequence

from .events import Event, EventBus, EventType, get_event_bus
from .telemetry import GitState, NotARepositoryError, TelemetryError, read_git_state

__all__ = ["GitObserver"]

_GITPYTHON_SPEC = importlib.util.find_spec("git")


class GitObserver:
    """Transition detector over live repository state.

    The first :meth:`poll_once` establishes a silent baseline; later
    polls publish only when the branch, HEAD, or status signature
    changes.
    """

    def __init__(
        self,
        repo: str | Path,
        allowed_roots: Sequence[str | Path],
        *,
        bus: Optional[EventBus] = None,
        session_id: str = "",
        backend: Literal["auto", "gitpython", "subprocess"] = "auto",
        poll_interval_s: float = 5.0,
    ) -> None:
        if poll_interval_s <= 0:
            raise ValueError(
                f"poll_interval_s: expected positive seconds, "
                f"got {poll_interval_s!r}"
            )
        resolved = backend
        if backend == "auto":
            resolved = (
                "gitpython" if _GITPYTHON_SPEC is not None else "subprocess"
            )
        if resolved == "gitpython" and _GITPYTHON_SPEC is None:
            raise ImportError(
                "backend='gitpython' requires the GitPython package "
                "(pip install mimir[observation]); use "
                "backend='subprocess' for the stdlib fallback"
            )
        self.repo = str(repo)
        self.allowed_roots = list(allowed_roots)
        self.bus = bus
        self.session_id = session_id
        self.backend: Literal["gitpython", "subprocess"] = resolved  # type: ignore[assignment]
        self.poll_interval_s = float(poll_interval_s)
        self._last: Optional[GitState] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.last_error = ""

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def last_state(self) -> Optional[GitState]:
        with self._lock:
            return self._last

    def start(self) -> None:
        """Begin background polling (idempotent)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="mimir-git-observer", daemon=True
            )
            self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        """End background polling (idempotent)."""
        with self._lock:
            thread = self._thread
            self._thread = None
        self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout_s)

    def _run(self) -> None:
        while not self._stop.wait(self.poll_interval_s):
            try:
                self.poll_once()
            except Exception as exc:
                self.last_error = str(exc)

    def snapshot(self) -> GitState:
        """Read the current state with the configured backend."""
        if self.backend == "gitpython":
            return self._snapshot_gitpython()
        return read_git_state(self.repo, allowed_roots=self.allowed_roots)

    def poll_once(self) -> Optional[GitState]:
        """Poll once; returns the state only when it changed.

        The first poll establishes the baseline silently (returns the
        state but publishes nothing); a changed state is published as
        ``GIT_CHANGED`` and returned; an unchanged state returns None.
        """
        try:
            current = self.snapshot()
        except Exception as exc:
            self.last_error = str(exc)
            raise
        with self._lock:
            previous = self._last
            self._last = current
        self.last_error = ""
        if previous is None:
            return current
        if current.signature() == previous.signature():
            return None
        self._publish(previous, current)
        return current

    def _snapshot_gitpython(self) -> GitState:
        # Boundary first: reuse the subprocess resolver so both
        # backends share one enforcement point, then read state via
        # GitPython for the live-status API.
        from .telemetry import resolve_repo  # noqa: PLC0415 (lazy, small)

        repo_path = resolve_repo(self.repo, self.allowed_roots)
        try:
            import git  # type: ignore[import-not-found]
        except Exception as exc:
            raise TelemetryError(
                f"GitPython backend unavailable: {exc}"
            ) from None
        try:
            repo = git.Repo(str(repo_path))
        except Exception as exc:
            raise NotARepositoryError(
                f"{repo_path}: not a git repository ({exc})"
            ) from None
        try:
            branch = repo.active_branch.name
        except Exception:
            branch = "HEAD"
        try:
            head = repo.head.commit.hexsha
        except Exception:
            head = ""
        try:
            staged = sorted({d.b_path for d in repo.index.diff("HEAD")})
            unstaged = sorted({d.a_path or d.b_path
                               for d in repo.index.diff(None)})
            untracked = sorted(repo.untracked_files)
        except Exception as exc:
            raise TelemetryError(
                f"GitPython status read failed: {exc}"
            ) from None
        staged_paths = [p for p in staged if p]
        unstaged_paths = [p for p in unstaged if p]
        return GitState(
            repo=str(repo_path),
            branch=branch or "HEAD",
            head=head,
            staged=tuple(staged_paths[:200]),
            unstaged=tuple(unstaged_paths[:200]),
            untracked=tuple(untracked[:200]),
            staged_count=len(staged_paths),
            unstaged_count=len(unstaged_paths),
            untracked_count=len(untracked),
            dirty=bool(staged_paths or unstaged_paths or untracked),
        )

    def _publish(self, previous: GitState, current: GitState) -> Optional[Event]:
        bus = self.bus if self.bus is not None else get_event_bus()
        try:
            return bus.emit(
                EventType.GIT_CHANGED,
                "sensory.git_observer",
                {
                    "repo": current.repo,
                    "branch": current.branch,
                    "head": current.head,
                    "previous_head": previous.head,
                    "branch_changed": current.branch != previous.branch,
                    "staged": list(current.staged),
                    "unstaged": list(current.unstaged),
                    "untracked": list(current.untracked),
                    "staged_count": current.staged_count,
                    "unstaged_count": current.unstaged_count,
                    "untracked_count": current.untracked_count,
                    "dirty": current.dirty,
                    "observed_at": time.time(),
                },
                session_id=self.session_id,
            )
        except Exception:
            return None
