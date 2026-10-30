"""Developer State Engine (plan section 3.3).

Maintains an in-memory representation of the developer's active
working context, updated incrementally by observation events. Every
field carries provenance — :attr:`FieldProvenance.VERIFIED` for direct
observations (a function was modified) versus
:attr:`FieldProvenance.INFERRED` for conclusions (the developer is
debugging it) — so downstream inference never mistakes a guess for a
fact.

Updates are partial: only affected fields change per event, and
``DEVELOPER_STATE_UPDATED`` reports exactly the changed fields.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .events import Event, EventBus, EventType, Subscription, get_event_bus

__all__ = [
    "DEVELOPER_STATE_FIELDS",
    "DeveloperState",
    "DeveloperStateEngine",
    "FieldProvenance",
]

#: Canonical state fields (plan section 3.3).
DEVELOPER_STATE_FIELDS = (
    "active_project",
    "active_repo",
    "focus_file",
    "recent_edits",
    "recent_ast_changes",
    "git_branch",
    "git_head",
    "git_dirty",
    "staged_files",
    "unstaged_files",
    "cadence_summary",
    "activity",
    "conversation_context",
    "pending_proposals",
)

_RECENT_CAP = 20


class FieldProvenance(str, Enum):
    """Whether a state field is observed fact or inferred conclusion."""

    VERIFIED = "verified"
    INFERRED = "inferred"


def _default_state_values() -> dict[str, Any]:
    return {
        "active_project": "",
        "active_repo": "",
        "focus_file": "",
        "recent_edits": [],
        "recent_ast_changes": [],
        "git_branch": "",
        "git_head": "",
        "git_dirty": False,
        "staged_files": [],
        "unstaged_files": [],
        "cadence_summary": {},
        "activity": "unknown",
        "conversation_context": {},
        "pending_proposals": [],
    }


@dataclass
class DeveloperState:
    """Structured working-context snapshot with per-field provenance."""

    values: dict[str, Any] = field(
        default_factory=_default_state_values)
    provenance: dict[str, FieldProvenance] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    updated_at: dict[str, float] = field(default_factory=dict)

    def get(self, name: str) -> Any:
        if name not in DEVELOPER_STATE_FIELDS:
            raise KeyError(f"unknown developer-state field: {name!r}")
        return self.values.get(name)

    def get_provenance(self, name: str) -> Optional[FieldProvenance]:
        if name not in DEVELOPER_STATE_FIELDS:
            raise KeyError(f"unknown developer-state field: {name!r}")
        return self.provenance.get(name)

    def verified_fields(self) -> list[str]:
        return sorted(
            n for n, p in self.provenance.items()
            if p is FieldProvenance.VERIFIED
        )

    def inferred_fields(self) -> list[str]:
        return sorted(
            n for n, p in self.provenance.items()
            if p is FieldProvenance.INFERRED
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": dict(self.values),
            "provenance": {k: v.value for k, v in self.provenance.items()},
            "confidence": dict(self.confidence),
            "updated_at": dict(self.updated_at),
        }


class DeveloperStateEngine:
    """In-memory context reconstructor fed by observation events."""

    _OBSERVED_TYPES = frozenset({
        EventType.FILE_CREATED,
        EventType.FILE_MODIFIED,
        EventType.FILE_DELETED,
        EventType.AST_DELTA,
        EventType.GIT_CHANGED,
        EventType.CADENCE_UPDATED,
        EventType.INTENT_PARSED,
        EventType.ACTION_PROPOSED,
    })

    def __init__(
        self,
        *,
        bus: Optional[EventBus] = None,
        session_id: str = "",
    ) -> None:
        self.bus = bus
        self.session_id = session_id
        self.state = DeveloperState()
        self._lock = threading.Lock()
        self._subscriptions: list[Subscription] = []

    # -- binding ----------------------------------------------------------
    def bind(self, bus: Optional[EventBus] = None) -> list[Subscription]:
        """Subscribe to observation events (idempotent re-bind safe)."""
        target = bus if bus is not None else (
            self.bus if self.bus is not None else get_event_bus()
        )
        if self.bus is None:
            self.bus = target
        token = target.subscribe(
            *self._OBSERVED_TYPES,
            handler=self.apply_observation,
            name="devstate:observe",
        )
        with self._lock:
            self._subscriptions.append(token)
        return [token]

    def unbind(self) -> None:
        with self._lock:
            tokens, self._subscriptions = self._subscriptions, []
        for token in tokens:
            try:
                token.unsubscribe()
            except Exception:
                pass

    # -- partial updates ----------------------------------------------------
    def update(
        self,
        fields: dict[str, Any],
        *,
        provenance: FieldProvenance = FieldProvenance.VERIFIED,
        confidence: float = 1.0,
        now: Optional[float] = None,
    ) -> list[str]:
        """Apply a partial field update; returns changed field names.

        Only fields whose value actually differs are touched, stamped,
        and reported. Emits ``DEVELOPER_STATE_UPDATED`` when anything
        changed.
        """
        unknown = [n for n in fields if n not in DEVELOPER_STATE_FIELDS]
        if unknown:
            raise KeyError(
                f"unknown developer-state field(s): {sorted(unknown)!r}"
            )
        if not isinstance(confidence, (int, float)):
            raise ValueError(
                f"confidence: expected number, got {confidence!r}"
            )
        if not 0.0 < float(confidence) <= 1.0:
            raise ValueError(
                f"confidence: expected (0, 1], got {confidence!r}"
            )
        moment = time.time() if now is None else now
        changed: list[str] = []
        with self._lock:
            for name, value in fields.items():
                if self.state.values.get(name) == value:
                    continue
                self.state.values[name] = value
                self.state.provenance[name] = provenance
                self.state.confidence[name] = float(confidence)
                self.state.updated_at[name] = moment
                changed.append(name)
        if changed:
            self._publish(sorted(changed))
        return sorted(changed)

    def apply_inference(
        self, name: str, value: Any, confidence: float,
        now: Optional[float] = None,
    ) -> list[str]:
        """Record an inferred conclusion (always INFERRED provenance)."""
        return self.update({name: value}, provenance=FieldProvenance.INFERRED,
                           confidence=confidence, now=now)

    def reset(self) -> None:
        """Clear values and provenance (session boundary helper)."""
        with self._lock:
            self.state = DeveloperState()

    # -- observation reduction ----------------------------------------------
    def apply_observation(self, event: Event) -> list[str]:
        """Reduce one observation event into partial state (VERIFIED)."""
        if event.event_type not in self._OBSERVED_TYPES:
            return []
        payload = event.payload if isinstance(event.payload, dict) else {}
        handler = {
            EventType.FILE_CREATED: self._reduce_file,
            EventType.FILE_MODIFIED: self._reduce_file,
            EventType.FILE_DELETED: self._reduce_file,
            EventType.AST_DELTA: self._reduce_ast,
            EventType.GIT_CHANGED: self._reduce_git,
            EventType.CADENCE_UPDATED: self._reduce_cadence,
            EventType.INTENT_PARSED: self._reduce_intent,
            EventType.ACTION_PROPOSED: self._reduce_proposal,
        }[event.event_type]
        fields = handler(event, payload)
        if not fields:
            return []
        return self.update(fields, provenance=FieldProvenance.VERIFIED,
                           now=event.timestamp)

    def _reduce_file(
        self, event: Event, payload: dict[str, Any]
    ) -> dict[str, Any]:
        path = payload.get("path")
        if not isinstance(path, str) or not path:
            return {}
        kind = payload.get("kind") or event.event_type.value.lower().replace(
            "file_", "")
        with self._lock:
            recent = list(self.state.values.get("recent_edits") or [])
        recent.append({"path": path, "kind": kind,
                       "at": event.timestamp})
        fields: dict[str, Any] = {"recent_edits": recent[-_RECENT_CAP:]}
        root = payload.get("root")
        if isinstance(root, str) and root:
            fields["active_project"] = root
        if event.event_type is EventType.FILE_DELETED:
            with self._lock:
                if self.state.values.get("focus_file") == path:
                    fields["focus_file"] = ""
        else:
            fields["focus_file"] = path
        return fields

    def _reduce_ast(
        self, event: Event, payload: dict[str, Any]
    ) -> dict[str, Any]:
        path = payload.get("file")
        if not isinstance(path, str) or not path:
            return {}
        entry = {
            "file": path,
            "added": list(payload.get("added_functions") or []),
            "modified": list(payload.get("modified_functions") or []),
            "removed": list(payload.get("removed_functions") or []),
            "syntax_valid": bool(payload.get("syntax_valid", True)),
            "at": event.timestamp,
        }
        with self._lock:
            recent = list(self.state.values.get("recent_ast_changes") or [])
        recent.append(entry)
        return {
            "recent_ast_changes": recent[-_RECENT_CAP:],
            "focus_file": path,
        }

    def _reduce_git(
        self, event: Event, payload: dict[str, Any]
    ) -> dict[str, Any]:
        _ = event
        fields: dict[str, Any] = {}
        repo = payload.get("repo")
        if isinstance(repo, str) and repo:
            fields["active_repo"] = repo
        branch = payload.get("branch")
        if isinstance(branch, str) and branch:
            fields["git_branch"] = branch
        head = payload.get("head")
        if isinstance(head, str) and head:
            fields["git_head"] = head
        if "dirty" in payload:
            fields["git_dirty"] = bool(payload.get("dirty"))
        staged = payload.get("staged")
        if isinstance(staged, list):
            fields["staged_files"] = [str(p) for p in staged]
        unstaged = payload.get("unstaged")
        if isinstance(unstaged, list):
            fields["unstaged_files"] = [str(p) for p in unstaged]
        return fields

    def _reduce_cadence(
        self, event: Event, payload: dict[str, Any]
    ) -> dict[str, Any]:
        summary = {
            key: payload.get(key)
            for key in ("window_s", "active_s", "idle_s", "bursts",
                        "pauses", "longest_pause_s", "corrections",
                        "corrections_per_min")
            if key in payload
        }
        if not summary:
            return {}
        summary["at"] = event.timestamp
        return {"cadence_summary": summary}

    def _reduce_intent(
        self, event: Event, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return {"conversation_context": {
            "last_intent": payload.get("intent", ""),
            "last_utterance": payload.get("text", ""),
            "at": event.timestamp,
        }}

    def _reduce_proposal(
        self, event: Event, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            pending = list(self.state.values.get("pending_proposals") or [])
        pending.append({
            "task": payload.get("task", ""),
            "applied_ids": list(payload.get("applied_ids") or []),
            "at": event.timestamp,
        })
        return {"pending_proposals": pending[-_RECENT_CAP:]}

    # -- publishing -----------------------------------------------------------
    def _publish(self, changed: list[str]) -> None:
        bus = self.bus if self.bus is not None else get_event_bus()
        with self._lock:
            values = {n: self.state.values.get(n) for n in changed}
            provenance = {
                n: self.state.provenance[n].value
                for n in changed if n in self.state.provenance
            }
            confidence = {
                n: self.state.confidence.get(n, 1.0) for n in changed
            }
        try:
            bus.emit(
                EventType.DEVELOPER_STATE_UPDATED,
                "cognitive.state_engine",
                {
                    "changed_fields": changed,
                    "values": values,
                    "provenance": provenance,
                    "confidence": confidence,
                    "observed_at": time.time(),
                },
                session_id=self.session_id,
            )
        except Exception:
            pass
