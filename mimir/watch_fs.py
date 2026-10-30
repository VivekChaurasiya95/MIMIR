"""Continuous filesystem observation (plan section 3.1A).

Watches explicitly selected project directories and publishes
``FILE_CREATED`` / ``FILE_MODIFIED`` / ``FILE_DELETED`` runtime events.
Every event carries file identity, kind, timestamp, and project
context — never file contents.

Backends:

- ``watchdog`` (plan section 9) when it is importable.
- A stdlib mtime/size polling snapshot otherwise, so the core stays
  dependency-free and headless-testable.

Raw backend notifications pass through :class:`Debouncer`, which
coalesces editor save bursts and OS event duplication (including the
atomic-save ``deleted + created == modified`` pattern).
"""

from __future__ import annotations

import fnmatch
import importlib.util
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional, Sequence

from .events import Event, EventBus, EventType, create_event, get_event_bus

__all__ = [
    "DEFAULT_DEBOUNCE_S",
    "DEFAULT_EXCLUDE_DIRS",
    "DEFAULT_POLL_INTERVAL_S",
    "Debouncer",
    "FileEvent",
    "FilesystemWatcher",
    "FsKind",
    "diff_snapshots",
    "scan_tree",
    "should_exclude",
]

#: Directory names that are never observed (venvs, build output,
#: dependency folders, VCS metadata, generated artifacts).
DEFAULT_EXCLUDE_DIRS = frozenset({
    ".git",
    ".hg",
    ".sl",
    ".eden",
    ".venv",
    "venv",
    "env",
    ".conda",
    "node_modules",
    "__pycache__",
    "build",
    "dist",
    ".eggs",
    "target",
    "out",
    ".tox",
    ".nox",
    ".pytest_cache",
    ".mypy_cache",
    ".pyrefly",
    ".ruff_cache",
})

#: Basename globs for editor/OS noise normalized away (never emitted).
_NOISE_BASENAME_GLOBS = ("*.tmp", "*.swp", "*.swo", "*~", "~$*")

DEFAULT_DEBOUNCE_S = 0.5
DEFAULT_POLL_INTERVAL_S = 1.0

FsKind = Literal["created", "modified", "deleted"]

_FILE_EVENT_TYPE = {
    "created": EventType.FILE_CREATED,
    "modified": EventType.FILE_MODIFIED,
    "deleted": EventType.FILE_DELETED,
}

_WATCHDOG_SPEC = importlib.util.find_spec("watchdog")


def _split_parts(path: str) -> list[str]:
    return [p for p in Path(path).parts if p not in (os.sep, "/")]


def should_exclude(
    path: str | Path,
    roots: Sequence[str | Path] = (),
    *,
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
) -> bool:
    """True when *path* must never be observed.

    Excluded: anything under an excluded directory name (venv, build,
    deps, VCS metadata, caches), ``*.egg-info`` trees, and editor/OS
    temporary files. *roots* is accepted for call-site symmetry and
    reserved for future root-scoped rules.
    """
    _ = roots
    text = os.path.normpath(str(path))
    parts = _split_parts(text)
    for part in parts:
        lowered = part.lower()
        if lowered in exclude_dirs or lowered.endswith(".egg-info"):
            return True
    basename = os.path.basename(text)
    return any(
        fnmatch.fnmatchcase(basename, glob) for glob in _NOISE_BASENAME_GLOBS
    )


@dataclass(frozen=True)
class FileEvent:
    """One debounced, normalized filesystem change (identity only)."""

    kind: FsKind
    path: str
    root: str
    observed_at: float = field(default_factory=time.time)


def _coalesce_kinds(buffered: FsKind, incoming: FsKind) -> Optional[FsKind]:
    """Merge an incoming notification into a buffered one.

    Returns None when the pair cancels out (created then deleted).
    """
    table: dict[tuple[FsKind, FsKind], Optional[FsKind]] = {
        ("created", "created"): "created",
        ("created", "modified"): "created",
        ("created", "deleted"): None,
        ("modified", "created"): "modified",
        ("modified", "modified"): "modified",
        ("modified", "deleted"): "deleted",
        ("deleted", "created"): "modified",
        ("deleted", "modified"): "modified",
        ("deleted", "deleted"): "deleted",
    }
    return table[(buffered, incoming)]


class Debouncer:
    """Per-path coalescing buffer with a maturation window.

    :meth:`note` buffers raw notifications; :meth:`flush` releases
    entries older than ``debounce_s``. All timestamps are caller
    supplied so tests stay deterministic.
    """

    def __init__(self, debounce_s: float = DEFAULT_DEBOUNCE_S) -> None:
        if not isinstance(debounce_s, (int, float)) or debounce_s < 0:
            raise ValueError(
                f"debounce_s: expected non-negative seconds, "
                f"got {debounce_s!r}"
            )
        self.debounce_s = float(debounce_s)
        self._pending: dict[str, FileEvent] = {}
        self._lock = threading.Lock()

    def note(
        self, kind: FsKind, path: str, root: str, now: float
    ) -> None:
        """Buffer one raw notification for (*path*)."""
        if kind not in ("created", "modified", "deleted"):
            raise ValueError(f"kind: expected created/modified/deleted, "
                             f"got {kind!r}")
        key = os.path.normpath(path)
        with self._lock:
            buffered = self._pending.get(key)
            if buffered is None:
                self._pending[key] = FileEvent(kind, key, root, now)
                return
            merged = _coalesce_kinds(buffered.kind, kind)
            if merged is None:
                del self._pending[key]
            else:
                self._pending[key] = FileEvent(
                    merged, key, buffered.root, buffered.observed_at
                )

    def flush(self, now: float) -> list[FileEvent]:
        """Release entries matured at or before *now* (oldest first)."""
        with self._lock:
            mature = sorted(
                (e for e in self._pending.values()
                 if now - e.observed_at >= self.debounce_s),
                key=lambda e: (e.observed_at, e.path),
            )
            for event in mature:
                self._pending.pop(os.path.normpath(event.path), None)
            return mature

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)


def _snapshot_file(path: str) -> Optional[tuple[int, int]]:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


def scan_tree(root: str | Path) -> dict[str, tuple[int, int]]:
    """Snapshot ``(size, mtime_ns)`` per file under *root*.

    Excluded directories are pruned before descent. Symlinked
    directories are not followed.
    """
    top = os.path.abspath(str(root))
    snapshot: dict[str, tuple[int, int]] = {}
    if should_exclude(top):
        return snapshot
    for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
        dirnames[:] = [
            d for d in dirnames
            if not should_exclude(os.path.join(dirpath, d))
        ]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if should_exclude(full):
                continue
            seen = _snapshot_file(full)
            if seen is not None:
                snapshot[os.path.normpath(full)] = seen
    return snapshot


def diff_snapshots(
    old: dict[str, tuple[int, int]],
    new: dict[str, tuple[int, int]],
) -> list[tuple[FsKind, str]]:
    """Pure snapshot diff, sorted by path for stable output."""
    kinds: list[tuple[FsKind, str]] = []
    for path in sorted(set(old) | set(new)):
        before = old.get(path)
        after = new.get(path)
        if before is None and after is not None:
            kinds.append(("created", path))
        elif before is not None and after is None:
            kinds.append(("deleted", path))
        elif before != after:
            kinds.append(("modified", path))
    return kinds


class FilesystemWatcher:
    """Continuous project-directory watcher publishing to the bus.

    With ``backend="auto"`` (default) the watchdog observer is used
    when importable, else the stdlib polling snapshot. ``poll_once``
    runs one synchronous scan cycle (poll backend) or flushes the
    debouncer (watchdog backend); the background thread just calls it
    on an interval.
    """

    def __init__(
        self,
        roots: Sequence[str | Path],
        *,
        bus: Optional[EventBus] = None,
        session_id: str = "",
        debounce_s: float = DEFAULT_DEBOUNCE_S,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
        backend: Literal["auto", "watchdog", "poll"] = "auto",
        sink: Optional[Callable[[FileEvent], None]] = None,
    ) -> None:
        if not roots:
            raise ValueError("roots: expected at least one directory")
        if poll_interval_s <= 0:
            raise ValueError(
                f"poll_interval_s: expected positive seconds, "
                f"got {poll_interval_s!r}"
            )
        resolved_backend = backend
        if backend == "auto":
            resolved_backend = (
                "watchdog" if _WATCHDOG_SPEC is not None else "poll"
            )
        if resolved_backend == "watchdog" and _WATCHDOG_SPEC is None:
            raise ImportError(
                "backend='watchdog' requires the watchdog package "
                "(pip install mimir[observation]); use backend='poll' "
                "for the stdlib fallback"
            )
        self.roots = [os.path.abspath(str(r)) for r in roots]
        self.bus = bus
        self.session_id = session_id
        self.debouncer = Debouncer(debounce_s)
        self.poll_interval_s = float(poll_interval_s)
        self.exclude_dirs = exclude_dirs
        self.backend: Literal["watchdog", "poll"] = resolved_backend  # type: ignore[assignment]
        self.sink = sink
        self._snapshots: dict[str, dict[str, tuple[int, int]]] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._observer: object = None
        self._lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------
    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Begin background observation (idempotent)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            if self.backend == "watchdog":
                self._start_watchdog_locked()
            else:
                for root in self.roots:
                    self._snapshots[root] = scan_tree(root)
            self._thread = threading.Thread(
                target=self._run, name="mimir-fs-watcher", daemon=True
            )
            self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        """End background observation (idempotent)."""
        with self._lock:
            thread = self._thread
            observer = self._observer
            self._thread = None
            self._observer = None
        self._stop.set()
        if observer is not None:
            try:
                stop = getattr(observer, "stop")
                stop()
                join = getattr(observer, "join", None)
                if callable(join):
                    join(timeout_s)
            except Exception:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout_s)

    def _run(self) -> None:
        while not self._stop.wait(self.poll_interval_s):
            try:
                self.poll_once()
            except Exception:
                continue

    # -- synchronous cycle (thread target body + test entry point) -------
    def poll_once(self, now: Optional[float] = None) -> list[FileEvent]:
        """Run one observation cycle; returns events published."""
        moment = time.time() if now is None else now
        if self.backend == "poll":
            for root in self.roots:
                if should_exclude(root):
                    continue
                current = scan_tree(root)
                previous = self._snapshots.get(root, {})
                for kind, path in diff_snapshots(previous, current):
                    self.debouncer.note(kind, path, root, moment)
                self._snapshots[root] = current
        matured = self.debouncer.flush(moment)
        for event in matured:
            self._publish(event)
        return matured

    def note_many(
        self, items: Sequence[tuple[FsKind, str, str]],
        now: Optional[float] = None,
    ) -> None:
        """Buffer raw ``(kind, path, root)`` triples (backend hook)."""
        moment = time.time() if now is None else now
        for kind, path, root in items:
            if should_exclude(path, exclude_dirs=self.exclude_dirs):
                continue
            self.debouncer.note(kind, os.path.abspath(path), root, moment)

    # -- watchdog backend -------------------------------------------------
    def _start_watchdog_locked(self) -> None:
        from watchdog.events import FileSystemEventHandler  # type: ignore[import-not-found]
        from watchdog.observers import Observer  # type: ignore[import-not-found]

        watcher = self

        class _Handler(FileSystemEventHandler):  # type: ignore[misc,valid-type]
            def _record(self, kind: FsKind, src: str) -> None:
                for root in watcher.roots:
                    if os.path.abspath(src).startswith(root + os.sep):
                        watcher.note_many([(kind, src, root)])
                        return
                watcher.note_many([(kind, src, watcher.roots[0])])

            def on_created(self, event: object) -> None:  # type: ignore[override]
                if not getattr(event, "is_directory", False):
                    self._record("created", str(getattr(event, "src_path")))

            def on_modified(self, event: object) -> None:  # type: ignore[override]
                if not getattr(event, "is_directory", False):
                    self._record("modified", str(getattr(event, "src_path")))

            def on_deleted(self, event: object) -> None:  # type: ignore[override]
                if not getattr(event, "is_directory", False):
                    self._record("deleted", str(getattr(event, "src_path")))

            def on_moved(self, event: object) -> None:  # type: ignore[override]
                if getattr(event, "is_directory", False):
                    return
                src = str(getattr(event, "src_path"))
                dest = str(getattr(event, "dest_path", ""))
                watcher._publish_move(src, dest)

        observer = Observer()
        handler = _Handler()
        for root in watcher.roots:
            if not should_exclude(root):
                observer.schedule(handler, root, recursive=True)
        observer.start()
        self._observer = observer

    # -- publishing --------------------------------------------------------
    def _bus(self) -> EventBus:
        return self.bus if self.bus is not None else get_event_bus()

    def _publish(self, event: FileEvent) -> Optional[Event]:
        if self.sink is not None:
            try:
                self.sink(event)
            except Exception:
                pass
        try:
            return self._bus().emit(
                _FILE_EVENT_TYPE[event.kind],
                "sensory.fs_watcher",
                {
                    "path": event.path,
                    "root": event.root,
                    "kind": event.kind,
                    "observed_at": event.observed_at,
                },
                session_id=self.session_id,
            )
        except Exception:
            return None

    def _publish_move(self, src: str, dest: str) -> None:
        """Model a rename/move as a correlated delete + create pair."""
        if not dest:
            self.note_many([("deleted", src, self.roots[0])])
            return
        if should_exclude(src, exclude_dirs=self.exclude_dirs) and should_exclude(
            dest, exclude_dirs=self.exclude_dirs
        ):
            return
        try:
            bus = self._bus()
            first = bus.emit(
                EventType.FILE_DELETED,
                "sensory.fs_watcher",
                {"path": os.path.abspath(src), "root": self.roots[0],
                 "kind": "deleted", "observed_at": time.time(),
                 "moved_to": os.path.abspath(dest)},
                session_id=self.session_id,
            )
            correlation = first.correlation_id if first else None
            second = create_event(
                EventType.FILE_CREATED,
                "sensory.fs_watcher",
                {"path": os.path.abspath(dest), "root": self.roots[0],
                 "kind": "created", "observed_at": time.time(),
                 "moved_from": os.path.abspath(src)},
                session_id=self.session_id,
                correlation_id=correlation,
            )
            bus.publish(second)
        except Exception:
            pass
