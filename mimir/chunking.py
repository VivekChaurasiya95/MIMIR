"""Episodic chunking for the ambient sensory pipeline (Phase 1).

The filesystem watcher and AST observer emit *micro-actions* — one
file save, one structural delta. The :class:`EpisodicChunkingEngine`
groups those micro-actions into :class:`~mimir.episodes.BehavioralEpisode`
objects: one episode per burst of related edits, so the Detective
learns from coherent work sessions instead of isolated keystrokes.

Grouping is purely structural (paths, timestamps, function names) —
never source text. A :class:`~mimir.privacy.PrivacyGate` may be
attached so forbidden/out-of-scope paths are skipped before they can
enter an episode.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ._common import utc_now_iso
from .episodes import (
    BehavioralEpisode,
    EpisodeContext,
    EpisodeOutcome,
    FileTransition,
)

__all__ = [
    "DEFAULT_IDLE_GAP_S",
    "DEFAULT_MAX_ACTIONS",
    "DEFAULT_WINDOW_S",
    "EpisodicChunkingEngine",
    "MicroAction",
    "micro_action_from_event",
]

#: Default burst window: actions spanning longer than this start a new
#: episode.
DEFAULT_WINDOW_S = 300.0
#: Default idle split: a pause longer than this starts a new episode.
DEFAULT_IDLE_GAP_S = 60.0
#: Default buffer cap: the oldest buffered burst is emitted first.
DEFAULT_MAX_ACTIONS = 50

_MICRO_KINDS = frozenset({
    "created",
    "modified",
    "deleted",
    "ast_delta",
    "cadence",
    "git",
})

_LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
}


def _iso_from_epoch(moment: float) -> str:
    return datetime.fromtimestamp(
        moment, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class MicroAction:
    """One atomic observation: a save, a structural delta, a tick."""

    kind: str
    path: str = ""
    observed_at: float = field(default_factory=time.time)
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in _MICRO_KINDS:
            raise ValueError(
                f"kind: expected one of {sorted(_MICRO_KINDS)}, "
                f"got {self.kind!r}"
            )
        if not isinstance(self.path, str):
            raise ValueError(
                f"path: expected string, got {type(self.path).__name__}"
            )
        if not isinstance(self.observed_at, (int, float)):
            raise ValueError(
                f"observed_at: expected epoch float, "
                f"got {self.observed_at!r}"
            )
        if not isinstance(self.detail, dict):
            raise ValueError(
                "detail: expected dict, got "
                + type(self.detail).__name__
            )


def micro_action_from_event(event: Any) -> Optional[MicroAction]:
    """Convert a runtime event to a micro-action (None: not chunkable).

    Accepts :class:`~mimir.events.Event` for ``FILE_CREATED`` /
    ``FILE_MODIFIED`` / ``FILE_DELETED`` / ``AST_DELTA`` /
    ``CADENCE_UPDATED`` / ``GIT_CHANGED``. Anything else yields None.
    """
    raw_type = getattr(event, "event_type", "")
    event_type = str(getattr(raw_type, "value", raw_type))
    payload = getattr(event, "payload", None)
    if not isinstance(payload, dict):
        return None
    stamp = getattr(event, "timestamp", None)
    moment = (
        float(stamp) if isinstance(stamp, (int, float)) else time.time()
    )
    if event_type in ("FILE_CREATED", "FILE_MODIFIED", "FILE_DELETED"):
        path = payload.get("path")
        if not isinstance(path, str) or not path:
            return None
        kind = {
            "FILE_CREATED": "created",
            "FILE_MODIFIED": "modified",
            "FILE_DELETED": "deleted",
        }[event_type]
        return MicroAction(kind=kind, path=path, observed_at=moment)
    if event_type == "AST_DELTA":
        path = payload.get("file") or ""
        if not isinstance(path, str):
            return None
        detail = {
            key: list(payload.get(key) or [])
            for key in (
                "added_functions",
                "modified_functions",
                "removed_functions",
                "added_classes",
                "removed_classes",
                "modified_classes",
            )
        }
        detail["syntax_valid"] = bool(payload.get("syntax_valid", True))
        return MicroAction(
            kind="ast_delta", path=path, observed_at=moment,
            detail=detail)
    if event_type == "CADENCE_UPDATED":
        return MicroAction(
            kind="cadence", path="", observed_at=moment,
            detail={"summary": payload.get("summary", {})})
    if event_type == "GIT_CHANGED":
        repo = payload.get("repo") or ""
        return MicroAction(
            kind="git", path=str(repo), observed_at=moment,
            detail={"branch": payload.get("branch", "")})
    return None


class EpisodicChunkingEngine:
    """Buffers micro-actions and emits one episode per burst.

    A burst closes when the next action arrives after ``idle_gap_s`` of
    quiet, when the burst spans more than ``window_s``, or when the
    buffer reaches ``max_actions``. :meth:`flush` closes the open
    burst on demand.
    """

    def __init__(
        self,
        *,
        window_s: float = DEFAULT_WINDOW_S,
        idle_gap_s: float = DEFAULT_IDLE_GAP_S,
        max_actions: int = DEFAULT_MAX_ACTIONS,
        repo: str = "local",
        actor: str = "developer",
        source: str = "live_chunk",
        privacy_gate: Any = None,
        time_fn: Optional[Callable[[], float]] = None,
        id_fn: Optional[Callable[[], str]] = None,
    ) -> None:
        if not isinstance(window_s, (int, float)) or window_s <= 0:
            raise ValueError(
                f"window_s: expected positive seconds, got {window_s!r}"
            )
        if not isinstance(idle_gap_s, (int, float)) or idle_gap_s <= 0:
            raise ValueError(
                f"idle_gap_s: expected positive seconds, got {idle_gap_s!r}"
            )
        if type(max_actions) is not int or max_actions < 1:
            raise ValueError(
                f"max_actions: expected positive int, got {max_actions!r}"
            )
        self.window_s = float(window_s)
        self.idle_gap_s = float(idle_gap_s)
        self.max_actions = max_actions
        self.repo = repo
        self.actor = actor
        self.source = source
        self.privacy_gate = privacy_gate
        self._time_fn = time_fn or time.time
        self._id_fn = id_fn or (lambda: f"ep_live_{uuid.uuid4().hex[:8]}")
        self._buffer: list[MicroAction] = []
        self._lock = threading.Lock()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._buffer)

    @property
    def pending(self) -> list[MicroAction]:
        with self._lock:
            return list(self._buffer)

    def note(self, action: MicroAction) -> Optional[BehavioralEpisode]:
        """Buffer one micro-action; emits the closed burst, if any."""
        if not isinstance(action, MicroAction):
            raise ValueError(
                f"action: expected MicroAction, "
                f"got {type(action).__name__}"
            )
        with self._lock:
            if self._buffer and self._splits(action, self._buffer):
                burst, self._buffer = self._buffer, [action]
            else:
                self._buffer.append(action)
                if len(self._buffer) <= self.max_actions:
                    return None
                burst, self._buffer = (
                    self._buffer[:self.max_actions],
                    self._buffer[self.max_actions:],
                )
        return self._build_episode(burst)

    def observe_event(self, event: Any) -> Optional[BehavioralEpisode]:
        """Feed one runtime event; emits the closed burst, if any."""
        try:
            action = micro_action_from_event(event)
        except Exception:
            return None
        if action is None:
            return None
        try:
            return self.note(action)
        except Exception:
            return None

    def flush(self) -> Optional[BehavioralEpisode]:
        """Emit the open burst as one episode (None when empty)."""
        with self._lock:
            burst, self._buffer = self._buffer, []
        if not burst:
            return None
        return self._build_episode(burst)

    def reset(self) -> None:
        """Drop buffered micro-actions without emitting."""
        with self._lock:
            self._buffer = []

    # -- internals ------------------------------------------------------
    def _splits(
        self, incoming: MicroAction, buffered: list[MicroAction]
    ) -> bool:
        last = buffered[-1]
        if incoming.observed_at - last.observed_at > self.idle_gap_s:
            return True
        first = buffered[0]
        return incoming.observed_at - first.observed_at > self.window_s

    def _allowed(self, action: MicroAction) -> bool:
        gate = self.privacy_gate
        if gate is None or not action.path:
            return True
        try:
            return bool(gate.allows(action.path))
        except Exception:
            return False

    def _build_episode(
        self, burst: list[MicroAction]
    ) -> Optional[BehavioralEpisode]:
        actions = [a for a in burst if self._allowed(a)]
        skipped = len(burst) - len(actions)
        if not actions:
            return None
        redaction_kinds: set[str] = set()
        redaction_count = 0
        if skipped:
            redaction_kinds.add("secret_filename")
            redaction_count += skipped
        gate = self.privacy_gate

        def _clean(text: str) -> str:
            nonlocal redaction_count
            if gate is None:
                return text
            try:
                result = gate.redact(text)
            except Exception:
                return text
            redaction_kinds.update(result.kinds)
            redaction_count += result.count
            return result.text

        by_path: dict[str, list[MicroAction]] = {}
        for action in actions:
            by_path.setdefault(action.path or "(session)", []).append(action)
        transitions: list[FileTransition] = []
        for path, items in sorted(by_path.items()):
            status = "modified"
            for item in items:
                if item.kind == "created":
                    status = "added"
                elif item.kind == "deleted":
                    status = "deleted"
            lines: list[str] = []
            additions = 0
            deletions = 0
            for item in items:
                if item.kind == "ast_delta":
                    for name in item.detail.get("added_functions", []):
                        lines.append(f"+ def {_clean(str(name))}(): ...")
                        additions += 1
                    for name in item.detail.get("modified_functions", []):
                        lines.append(
                            f"~ def {_clean(str(name))}(): [modified]")
                        additions += 1
                        deletions += 1
                    for name in item.detail.get("removed_functions", []):
                        lines.append(f"- def {_clean(str(name))}(): ...")
                        deletions += 1
                    for name in item.detail.get("added_classes", []):
                        lines.append(f"+ class {_clean(str(name))}: ...")
                        additions += 1
                    for name in item.detail.get("removed_classes", []):
                        lines.append(f"- class {_clean(str(name))}: ...")
                        deletions += 1
                elif item.kind in ("created", "modified", "deleted"):
                    mark = {"created": "+", "modified": "~",
                            "deleted": "-"}[item.kind]
                    lines.append(
                        f"{mark} {_clean(os.path.basename(path) or path)} "
                        f"[{item.kind}]")
                    if item.kind == "created":
                        additions += 1
                    elif item.kind == "deleted":
                        deletions += 1
                    else:
                        additions += 1
                        deletions += 1
            diff_text = "\n".join(lines) if lines else "(no structural change)"
            transitions.append(FileTransition(
                path=path,
                status=status,
                diff=_clean(diff_text),
                additions=additions,
                deletions=deletions,
            ))
        files = sorted({a.path for a in actions if a.path})
        try:
            scope = os.path.commonpath(files) if len(files) > 1 else (
                os.path.dirname(files[0]) if files else "")
        except ValueError:
            scope = ""
        language: Optional[str] = None
        for path in files:
            suffix = os.path.splitext(path)[1].lower()
            if suffix in _LANGUAGE_BY_EXTENSION:
                language = _LANGUAGE_BY_EXTENSION[suffix]
                break
        first, last = actions[0].observed_at, actions[-1].observed_at
        names = ", ".join(
            os.path.basename(p) or p for p in files[:4])
        if len(files) > 4:
            names += f" +{len(files) - 4} more"
        summary = (
            f"Live edit burst: {len(actions)} action(s) across "
            f"{len(files)} file(s)"
            + (f": {names}" if names else "")
        )
        summary = _clean(summary)
        try:
            observed_at = _iso_from_epoch(last)
        except (OverflowError, OSError, ValueError):
            observed_at = utc_now_iso()
        episode_id = self._id_fn()
        return BehavioralEpisode(
            id=episode_id,
            source=self.source,
            repo=self.repo or "local",
            source_ref=f"chunk:{first:.0f}-{last:.0f}",
            observed_at=observed_at,
            actor=self.actor,
            summary=summary,
            context=EpisodeContext(scope=scope, language=language),
            transitions=transitions,
            outcome=EpisodeOutcome(),
            redactions_applied=redaction_count,
            redaction_kinds=sorted(redaction_kinds),
        )
