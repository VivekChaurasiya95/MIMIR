"""Continuous observation service (plan sections 3.1 + 3.3).

Composes the four sensory observers — filesystem watcher, incremental
AST observer, git observer, cadence observer — with the Developer State
Engine. All coordination flows through the event bus: the service only
owns lifecycle (start/stop) and the file-change → AST-read bridge.

Threading: filesystem and git observers run their own daemon threads;
cadence has no thread (the host ticks it); AST observation runs on the
filesystem publisher's thread and is read-only + size-capped.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

from .ast_delta import AstObserver
from .cadence import CadenceObserver
from .devstate import DeveloperStateEngine
from .events import Event, EventBus, EventType, Subscription, get_event_bus
from .git_observe import GitObserver
from .watch_fs import FilesystemWatcher

__all__ = ["SensoryService"]


class SensoryService:
    """Lifecycle owner for continuous developer observation."""

    def __init__(
        self,
        roots: Sequence[str | Path],
        *,
        repo: Optional[str | Path] = None,
        allowed_roots: Optional[Sequence[str | Path]] = None,
        bus: Optional[EventBus] = None,
        session_id: str = "",
        fs_backend: Literal["auto", "watchdog", "poll"] = "auto",
        git_backend: Literal["auto", "gitpython", "subprocess"] = "auto",
        poll_interval_s: float = 1.0,
        git_poll_interval_s: float = 5.0,
        enable_cadence: bool = False,
    ) -> None:
        self.bus = bus if bus is not None else get_event_bus()
        self.session_id = session_id
        self.watcher = FilesystemWatcher(
            roots,
            bus=self.bus,
            session_id=session_id,
            backend=fs_backend,
            poll_interval_s=poll_interval_s,
        )
        self.ast = AstObserver(bus=self.bus, session_id=session_id)
        self.cadence = CadenceObserver(bus=self.bus, session_id=session_id)
        if enable_cadence:
            self.cadence.enable()
        self.engine = DeveloperStateEngine(bus=self.bus,
                                           session_id=session_id)
        self.git: Optional[GitObserver] = None
        if repo is not None:
            self.git = GitObserver(
                repo,
                list(allowed_roots) if allowed_roots is not None else [],
                bus=self.bus,
                session_id=session_id,
                backend=git_backend,
                poll_interval_s=git_poll_interval_s,
            )
        self._subscriptions: list[Subscription] = []
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        """Start observers and bus wiring (idempotent)."""
        with self._lock:
            if self._subscriptions:
                return
            self._subscriptions.append(
                self.bus.subscribe(
                    EventType.FILE_CREATED, EventType.FILE_MODIFIED,
                    handler=self._on_file_event, name="sensory:ast-bridge",
                )
            )
            self._subscriptions.append(
                self.bus.subscribe(
                    EventType.FILE_DELETED,
                    handler=self._on_file_deleted, name="sensory:ast-forget",
                )
            )
            self.engine.bind(self.bus)
        self.watcher.start()
        if self.git is not None:
            self.git.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        """Stop observers and detach bus wiring (idempotent)."""
        self.watcher.stop(timeout_s)
        if self.git is not None:
            self.git.stop(timeout_s)
        self.engine.unbind()
        with self._lock:
            tokens, self._subscriptions = self._subscriptions, []
        for token in tokens:
            try:
                token.unsubscribe()
            except Exception:
                pass

    @property
    def running(self) -> bool:
        return self.watcher.running or (
            self.git is not None and self.git.running
        )

    def status(self) -> dict[str, Any]:
        """Introspection snapshot (no file contents)."""
        return {
            "running": self.running,
            "fs_backend": self.watcher.backend,
            "roots": list(self.watcher.roots),
            "git_backend": self.git.backend if self.git else None,
            "cadence_enabled": self.cadence.is_enabled,
            "ast_tracked_files": len(self.ast.tracked_paths()),
            "state_provenance": {
                k: v.value for k, v in
                self.engine.state.provenance.items()
            },
        }

    # -- file-change → AST bridge --------------------------------------------
    def _on_file_event(self, event: Event) -> None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        path = payload.get("path")
        if not isinstance(path, str) or not path:
            return
        try:
            self.ast.observe_file(path)
        except Exception:
            pass

    def _on_file_deleted(self, event: Event) -> None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        path = payload.get("path")
        if not isinstance(path, str) or not path:
            return
        try:
            self.ast.forget(path)
        except Exception:
            pass
