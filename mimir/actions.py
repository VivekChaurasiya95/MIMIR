"""Unified Action Registry (plan sections 3.4 + 4, step 3).

The registry is the single owner of pending-operation state: every
proposed desktop (or otherwise privileged) operation is recorded here
with its target, required permissions, expiry, and execution status.
Voice confirmations such as "go ahead" never authorize actuation
directly — they resolve to :data:`APPROVE_PENDING_ACTION` and bind to
one specific registry entry (plan section 4).

Fail-closed rules:

- ``authorize`` needs an explicit ``action_id``; it never picks "the
  latest" action on its own.
- Expired actions are lazily marked ``EXPIRED`` and can never be
  approved again.
- Already resolved actions (authorized, completed, failed, cancelled,
  denied, expired) cannot transition again.
- Missing permissions deny without mutating the entry, so the caller
  can ask for clarification instead of guessing.

Qt-free and loop-free: coordination flows through the event bus via
best-effort ``emit`` calls that never raise into the caller.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Collection, Optional

from .events import EventBus, EventType, get_event_bus
from .safety import SafetyController

__all__ = [
    "APPROVE_PENDING_ACTION",
    "ActionRegistry",
    "ActionSpec",
    "ActionStatus",
    "AuthorizationOutcome",
    "RegistryError",
    "VOICE_GRANTED_PERMISSIONS",
]

#: Structured intent name for a spoken confirmation (plan section 4).
APPROVE_PENDING_ACTION = "APPROVE_PENDING_ACTION"

#: Permissions an explicit voice confirmation confers. Low-risk actions
#: require exactly these; anything more (e.g. "desktop-control") needs a
#: host grant or stays denied with a clarification prompt.
VOICE_GRANTED_PERMISSIONS: tuple[str, ...] = ("voice-confirm",)


class RegistryError(ValueError):
    """Raised for invalid registry input (empty ids, bad TTL, ...)."""


class ActionStatus(str, Enum):
    """Lifecycle of one proposed action (terminal states are final)."""

    PENDING = "pending"
    AUTHORIZED = "authorized"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DENIED = "denied"
    EXPIRED = "expired"


_TERMINAL = frozenset({
    ActionStatus.COMPLETED,
    ActionStatus.FAILED,
    ActionStatus.CANCELLED,
    ActionStatus.DENIED,
    ActionStatus.EXPIRED,
})


def _new_action_id() -> str:
    return f"action_{uuid.uuid4().hex[:8]}"


@dataclass
class ActionSpec:
    """One proposed operation with target, permissions, and expiry."""

    action_id: str
    operation: str
    target: str = ""
    file_or_element: str = ""
    reason: str = ""
    supporting_observations: tuple[str, ...] = ()
    confidence: float = 1.0
    required_permissions: tuple[str, ...] = VOICE_GRANTED_PERMISSIONS
    expires_at: Optional[float] = None
    status: ActionStatus = ActionStatus.PENDING
    created_at: float = field(default_factory=time.time)
    correlation_id: str = ""
    session_id: str = ""

    def is_expired(self, now: Optional[float] = None) -> bool:
        if self.expires_at is None:
            return False
        return (time.time() if now is None else now) > self.expires_at

    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "operation": self.operation,
            "target": self.target,
            "file_or_element": self.file_or_element,
            "reason": self.reason,
            "supporting_observations": list(self.supporting_observations),
            "confidence": self.confidence,
            "required_permissions": list(self.required_permissions),
            "expires_at": self.expires_at,
            "status": self.status.value,
            "created_at": self.created_at,
            "correlation_id": self.correlation_id,
            "session_id": self.session_id,
        }


@dataclass(frozen=True)
class AuthorizationOutcome:
    """Result of :meth:`ActionRegistry.authorize` (never raises)."""

    ok: bool
    reason: str
    action_id: str = ""
    status: ActionStatus = ActionStatus.PENDING


class ActionRegistry:
    """Thread-safe store of pending operations with expiry + permissions."""

    def __init__(
        self,
        *,
        bus: Optional[EventBus] = None,
        session_id: str = "",
        default_ttl_s: float = 300.0,
        safety: Optional[SafetyController] = None,
    ) -> None:
        if (
            isinstance(default_ttl_s, bool)
            or not isinstance(default_ttl_s, (int, float))
            or not default_ttl_s > 0
        ):
            raise RegistryError(
                f"default_ttl_s: expected a positive number, "
                f"got {default_ttl_s!r}"
            )
        if safety is not None and not isinstance(safety, SafetyController):
            raise RegistryError(
                "safety: expected SafetyController or None, "
                f"got {type(safety).__name__}"
            )
        self.bus = bus
        self.session_id = session_id
        self.default_ttl_s = float(default_ttl_s)
        self.safety = safety
        self._lock = threading.Lock()
        self._actions: dict[str, ActionSpec] = {}
        # Safety generation stamped at propose time; entries whose stamp
        # predates any E-stop are invalid (fail-closed, even after reset).
        self._safety_gen: dict[str, int] = {}

    # -- helpers --------------------------------------------------------
    def _bus(self) -> EventBus:
        return self.bus if self.bus is not None else get_event_bus()

    def _emit(
        self,
        event_type: EventType,
        source: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            self._bus().emit(
                event_type, source, payload, session_id=self.session_id
            )
        except Exception:
            pass

    def _expire_locked(self, spec: ActionSpec, now: float) -> bool:
        """Mark PENDING entries past their expiry; True when transitioned."""
        if spec.status is ActionStatus.PENDING and spec.is_expired(now):
            spec.status = ActionStatus.EXPIRED
            return True
        return False

    def _stale_locked(self, action_id: str) -> bool:
        """True when the entry predates any E-stop (call with lock held)."""
        if self.safety is None:
            return False
        return self._safety_gen.get(action_id, -1) != self.safety.generation

    def _invalidate_locked(self, spec: ActionSpec) -> bool:
        """Move pre-E-stop PENDING/AUTHORIZED entries to CANCELLED.

        True when transitioned. Silent (like expiry): callers that need
        an audit emission snapshot the entry and emit outside the lock.
        EXECUTING entries are left for :meth:`fail`/:meth:`complete`
        so an interrupted drive still records its failure with a reason.
        While the E-stop is latched, entries are denied without
        mutation (the latch already blocks everything); invalidation
        materializes on the first post-reset touch.
        """
        if self.safety is not None and self.safety.estopped:
            return False
        if spec.status not in (ActionStatus.PENDING, ActionStatus.AUTHORIZED):
            return False
        if not self._stale_locked(spec.action_id):
            return False
        spec.status = ActionStatus.CANCELLED
        return True

    # -- lifecycle ------------------------------------------------------
    def propose(
        self,
        operation: str,
        *,
        target: str = "",
        file_or_element: str = "",
        reason: str = "",
        supporting_observations: Collection[str] = (),
        confidence: float = 1.0,
        required_permissions: Collection[str] = VOICE_GRANTED_PERMISSIONS,
        ttl_s: Optional[float] = None,
        expires_at: Optional[float] = None,
        action_id: Optional[str] = None,
        correlation_id: str = "",
        session_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> ActionSpec:
        """Record a new pending action; emits ``ACTION_PROPOSED``."""
        if not isinstance(operation, str) or not operation.strip():
            raise RegistryError("operation: expected a non-empty string")
        if not isinstance(confidence, (int, float)) or isinstance(
            confidence, bool
        ):
            raise RegistryError(
                f"confidence: expected a number, got {confidence!r}"
            )
        if not 0.0 < float(confidence) <= 1.0:
            raise RegistryError(
                f"confidence: expected (0, 1], got {confidence!r}"
            )
        required = tuple(
            p for p in (str(p).strip() for p in required_permissions) if p
        )
        moment = time.time() if now is None else now
        if expires_at is None:
            ttl = self.default_ttl_s if ttl_s is None else ttl_s
            if (
                isinstance(ttl, bool)
                or not isinstance(ttl, (int, float))
                or not ttl > 0
            ):
                raise RegistryError(
                    f"ttl_s: expected a positive number, got {ttl!r}"
                )
            expires_at = moment + float(ttl)
        spec = ActionSpec(
            action_id=(
                action_id.strip()
                if isinstance(action_id, str) and action_id.strip()
                else _new_action_id()
            ),
            operation=operation.strip(),
            target=target.strip() if isinstance(target, str) else "",
            file_or_element=(
                file_or_element.strip()
                if isinstance(file_or_element, str)
                else ""
            ),
            reason=reason.strip() if isinstance(reason, str) else "",
            supporting_observations=tuple(
                str(o) for o in supporting_observations
            ),
            confidence=float(confidence),
            required_permissions=required,
            expires_at=float(expires_at),
            status=ActionStatus.PENDING,
            created_at=moment,
            correlation_id=correlation_id,
            session_id=(
                self.session_id if session_id is None else session_id
            ),
        )
        stamp = self.safety.generation if self.safety is not None else 0
        with self._lock:
            self._actions[spec.action_id] = spec
            self._safety_gen[spec.action_id] = stamp
        self._emit(EventType.ACTION_PROPOSED, "cognitive.action_planner", {
            "action_id": spec.action_id,
            "operation": spec.operation,
            "target": spec.target,
            "file_or_element": spec.file_or_element,
            "confidence": spec.confidence,
            "required_permissions": list(spec.required_permissions),
            "expires_at": spec.expires_at,
        })
        return spec

    def get(
        self, action_id: str, *, now: Optional[float] = None
    ) -> Optional[ActionSpec]:
        """Fetch one entry, lazily expiring/invalidating it when dead."""
        if not isinstance(action_id, str) or not action_id.strip():
            raise RegistryError("action_id: expected a non-empty string")
        moment = time.time() if now is None else now
        with self._lock:
            spec = self._actions.get(action_id.strip())
            if spec is not None:
                self._expire_locked(spec, moment)
                self._invalidate_locked(spec)
            return spec

    def list_pending(
        self, *, now: Optional[float] = None
    ) -> list[ActionSpec]:
        """Return unexpired PENDING entries, oldest first."""
        moment = time.time() if now is None else now
        with self._lock:
            for spec in self._actions.values():
                self._expire_locked(spec, moment)
                self._invalidate_locked(spec)
            pending = [
                s for s in self._actions.values()
                if s.status is ActionStatus.PENDING
            ]
        pending.sort(key=lambda s: s.created_at)
        return pending

    def authorize(
        self,
        action_id: str,
        *,
        authorization_type: str = "explicit_voice_confirmation",
        granted_permissions: Collection[str] = (),
        now: Optional[float] = None,
    ) -> AuthorizationOutcome:
        """Bind a confirmation to one specific action (fail-closed).

        Never raises for unknown/expired/denied actions — the outcome
        carries the reason so the caller can ask for clarification.
        Exactly one transition to AUTHORIZED can succeed per entry: the
        bus one-shot claim ``action:<id>:authorize`` guards doubles.
        While the safety E-stop is latched, authorization is denied
        without mutating the entry; entries predating any E-stop are
        invalidated to CANCELLED and can never authorize again.
        """
        if not isinstance(action_id, str) or not action_id.strip():
            raise RegistryError("action_id: expected a non-empty string")
        key = action_id.strip()
        moment = time.time() if now is None else now
        with self._lock:
            spec = self._actions.get(key)
            if spec is None:
                return AuthorizationOutcome(
                    False, f"unknown action {key!r}", action_id=key
                )
            transitioned = self._expire_locked(spec, moment)
            expired_snapshot = spec.to_dict() if transitioned else None
            invalidated_snapshot: Optional[dict[str, Any]] = None
            if spec.status is ActionStatus.EXPIRED:
                outcome: Optional[AuthorizationOutcome] = (
                    AuthorizationOutcome(
                        False,
                        f"action {key!r} expired",
                        action_id=key,
                        status=ActionStatus.EXPIRED,
                    )
                )
            elif spec.status is not ActionStatus.PENDING:
                outcome = AuthorizationOutcome(
                    False,
                    f"action {key!r} is already {spec.status.value}",
                    action_id=key,
                    status=spec.status,
                )
            elif self.safety is not None and self.safety.estopped:
                outcome = AuthorizationOutcome(
                    False,
                    f"action {key!r} denied: safety stop active",
                    action_id=key,
                    status=spec.status,
                )
            elif self._invalidate_locked(spec):
                invalidated_snapshot = spec.to_dict()
                outcome = AuthorizationOutcome(
                    False,
                    f"action {key!r} invalidated by safety stop",
                    action_id=key,
                    status=ActionStatus.CANCELLED,
                )
            else:
                outcome = None
        # Bus emission happens outside the registry lock: subscribers may
        # call back into the registry (e.g. list_pending) without deadlock.
        if expired_snapshot is not None:
            self._emit(EventType.ACTION_FAILED, "voice.action_registry", {
                **expired_snapshot,
                "outcome": "expired",
            })
        if invalidated_snapshot is not None:
            self._emit(EventType.ACTION_FAILED, "voice.action_registry", {
                **invalidated_snapshot,
                "outcome": "safety_invalidated",
            })
        if outcome is not None:
            return outcome
        invalid_snapshot: Optional[dict[str, Any]] = None
        snapshot: Optional[dict[str, Any]] = None
        with self._lock:
            spec = self._actions.get(key)
            if spec is None or spec.status is not ActionStatus.PENDING:
                current = spec.status if spec else ActionStatus.PENDING
                return AuthorizationOutcome(
                    False,
                    f"action {key!r} is no longer pending",
                    action_id=key,
                    status=current,
                )
            if self.safety is not None and self.safety.estopped:
                return AuthorizationOutcome(
                    False,
                    f"action {key!r} denied: safety stop active",
                    action_id=key,
                    status=spec.status,
                )
            if self._invalidate_locked(spec):
                # A trip landed between the two locked sections: the entry
                # is dead. Snapshot under the lock, emit outside it.
                invalid_snapshot = spec.to_dict()
            else:
                granted = {str(p).strip() for p in granted_permissions}
                missing = [
                    p for p in spec.required_permissions if p not in granted
                ]
                if missing:
                    return AuthorizationOutcome(
                        False,
                        f"action {key!r} requires permission(s): "
                        + ", ".join(sorted(missing)),
                        action_id=key,
                        status=spec.status,
                    )
                try:
                    claimed = self._bus().claim_action(
                        f"action:{key}:authorize"
                    )
                except Exception:
                    claimed = True
                if not claimed:
                    return AuthorizationOutcome(
                        False,
                        f"action {key!r} was already authorized",
                        action_id=key,
                        status=spec.status,
                    )
                spec.status = ActionStatus.AUTHORIZED
                snapshot = spec.to_dict()
        if invalid_snapshot is not None:
            self._emit(EventType.ACTION_FAILED, "voice.action_registry", {
                **invalid_snapshot,
                "outcome": "safety_invalidated",
            })
            return AuthorizationOutcome(
                False,
                f"action {key!r} invalidated by safety stop",
                action_id=key,
                status=ActionStatus.CANCELLED,
            )
        assert snapshot is not None  # set in the authorize branch above
        self._emit(EventType.ACTION_AUTHORIZED, "voice.action_registry", {
            **snapshot,
            "authorization_type": authorization_type,
        })
        return AuthorizationOutcome(
            True,
            f"action {key!r} authorized",
            action_id=key,
            status=ActionStatus.AUTHORIZED,
        )

    def mark_executing(self, action_id: str) -> bool:
        """Move AUTHORIZED → EXECUTING; emits ``ACTION_STARTED``.

        Fail-closed: returns False while the safety E-stop is latched,
        and invalidates pre-E-stop entries to CANCELLED (emitted as
        ``ACTION_FAILED`` with ``outcome=safety_invalidated``).
        """
        key = action_id.strip() if isinstance(action_id, str) else ""
        invalid_snapshot: Optional[dict[str, Any]] = None
        snapshot: Optional[dict[str, Any]] = None
        with self._lock:
            spec = self._actions.get(key)
            if spec is None or spec.status is not ActionStatus.AUTHORIZED:
                return False
            if self.safety is not None and self.safety.estopped:
                return False
            if self._invalidate_locked(spec):
                invalid_snapshot = spec.to_dict()
            else:
                spec.status = ActionStatus.EXECUTING
                snapshot = spec.to_dict()
        if invalid_snapshot is not None:
            self._emit(EventType.ACTION_FAILED, "voice.action_registry", {
                **invalid_snapshot,
                "outcome": "safety_invalidated",
            })
            return False
        assert snapshot is not None  # set in the executing branch above
        self._emit(EventType.ACTION_STARTED, "execution.actuator", snapshot)
        return True

    def complete(self, action_id: str) -> bool:
        """Move AUTHORIZED/EXECUTING → COMPLETED; emits ``ACTION_COMPLETED``.

        Fail-closed: while the safety E-stop is latched (or the entry
        predates any trip) the entry moves to FAILED with a safety
        reason instead, and False is returned. Use :meth:`fail` to
        record an interruption: it is always accepted.
        """
        key = action_id.strip() if isinstance(action_id, str) else ""
        with self._lock:
            spec = self._actions.get(key)
            allowed = spec is not None and spec.status in (
                ActionStatus.AUTHORIZED, ActionStatus.EXECUTING,
            )
            if not allowed or spec is None:
                return False
            blocked = (
                self.safety is not None
                and (
                    self.safety.estopped
                    or self._stale_locked(key)
                )
            )
            if blocked:
                spec.status = ActionStatus.FAILED
                snapshot = spec.to_dict()
            else:
                spec.status = ActionStatus.COMPLETED
                snapshot = spec.to_dict()
        if blocked:
            self._emit(EventType.ACTION_FAILED, "execution.actuator", {
                **snapshot,
                "reason": "safety stop during execution",
            })
            return False
        self._emit(EventType.ACTION_COMPLETED, "execution.actuator", snapshot)
        return True

    def fail(self, action_id: str, reason: str = "") -> bool:
        """Move to FAILED from any non-terminal state; emits ``ACTION_FAILED``.

        Always accepted, including while the safety E-stop is latched:
        recording an interruption must never be rejected."""
        with self._lock:
            spec = self._actions.get(
                action_id.strip() if isinstance(action_id, str) else ""
            )
            if spec is None or spec.is_terminal():
                return False
            spec.status = ActionStatus.FAILED
            snapshot = spec.to_dict()
        self._emit(EventType.ACTION_FAILED, "execution.actuator", {
            **snapshot, "reason": reason,
        })
        return True

    def cancel(self, action_id: str, reason: str = "") -> bool:
        """Move to CANCELLED from any non-terminal state."""
        with self._lock:
            spec = self._actions.get(
                action_id.strip() if isinstance(action_id, str) else ""
            )
            if spec is None or spec.is_terminal():
                return False
            spec.status = ActionStatus.CANCELLED
            snapshot = spec.to_dict()
        self._emit(EventType.ACTION_FAILED, "voice.action_registry", {
            **snapshot, "outcome": "cancelled", "reason": reason,
        })
        return True


