"""Central event runtime: contracts, dispatcher, and bounded async queues.

Step 1 of the improvement plan (section 3.2): every subsystem publishes
structured events and subscribes only to the events relevant to its own
responsibilities, instead of calling each other synchronously.

Event families (plan section 3.2):

- Observation: FILE_CREATED, FILE_MODIFIED, FILE_DELETED, AST_DELTA,
  GIT_CHANGED, CADENCE_UPDATED.
- Cognitive: DEVELOPER_STATE_UPDATED, PATTERN_DETECTED,
  INFERENCE_COMPLETED, ACTION_PROPOSED.
- Conversational: VOICE_CAPTURED, TRANSCRIPTION_COMPLETED, INTENT_PARSED,
  ACTION_CONFIRMATION_RECEIVED.
- Execution: ACTION_REQUESTED, ACTION_AUTHORIZED, ACTION_STARTED,
  ACTION_COMPLETED, ACTION_FAILED.
- Safety: E_STOP_TRIGGERED, ACTUATION_CANCELLED,
  TARGET_VALIDATION_FAILED, SAFETY_STATE_CHANGED.

Runtime guarantees:

- Bounded queues (one :class:`asyncio.Queue` per priority).
- Safety (CRITICAL) events are always dispatched before ordinary
  telemetry or inference work, no matter when they arrive.
- Idempotency: an ``event_id`` is dispatched at most once, and
  :meth:`EventBus.claim_action` guards one-shot action execution.
- Correlation: ``correlation_id`` chains causally related events.
- Consumer-failure isolation: one failing subscriber never breaks the
  others or the bus; failures land in the audit log.
- Audit log: every receive/dispatch/duplicate/drop/error is recorded.
- Loop-free paths: :meth:`EventBus.publish`, :meth:`EventBus.enqueue`,
  and :meth:`EventBus.pump` need no running event loop, so Qt threads
  and plain scripts can participate without loop-ownership hazards;
  :meth:`EventBus.start` adds background workers where a loop exists.

No broker is required (plan section 9): asyncio + in-process queues only.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, Union

from pydantic import ConfigDict, Field, ValidationError, field_validator
from pydantic import BaseModel as _BaseModel

__all__ = [
    "AuditRecord",
    "ConsumerError",
    "DEFAULT_PRIORITY",
    "DEFAULT_QUEUE_SIZES",
    "DispatchResult",
    "EnqueueResult",
    "Event",
    "EventBus",
    "EventFamily",
    "EventPriority",
    "EventType",
    "EVENT_FAMILY",
    "EVENT_TYPES",
    "EventValidationError",
    "Subscription",
    "bind_visor_to_bus",
    "create_event",
    "get_event_bus",
    "reset_event_bus",
    "set_event_bus",
]


class EventValidationError(ValueError):
    """Raised when an event contract fails validation."""


class EventType(str, Enum):
    """Every event type of the plan section 3.2 contract."""

    # Observation events.
    FILE_CREATED = "FILE_CREATED"
    FILE_MODIFIED = "FILE_MODIFIED"
    FILE_DELETED = "FILE_DELETED"
    AST_DELTA = "AST_DELTA"
    GIT_CHANGED = "GIT_CHANGED"
    CADENCE_UPDATED = "CADENCE_UPDATED"
    # Cognitive events.
    DEVELOPER_STATE_UPDATED = "DEVELOPER_STATE_UPDATED"
    PATTERN_DETECTED = "PATTERN_DETECTED"
    INFERENCE_COMPLETED = "INFERENCE_COMPLETED"
    ACTION_PROPOSED = "ACTION_PROPOSED"
    # Conversational events.
    VOICE_CAPTURED = "VOICE_CAPTURED"
    TRANSCRIPTION_COMPLETED = "TRANSCRIPTION_COMPLETED"
    INTENT_PARSED = "INTENT_PARSED"
    ACTION_CONFIRMATION_RECEIVED = "ACTION_CONFIRMATION_RECEIVED"
    # Execution events.
    ACTION_REQUESTED = "ACTION_REQUESTED"
    ACTION_AUTHORIZED = "ACTION_AUTHORIZED"
    ACTION_STARTED = "ACTION_STARTED"
    ACTION_COMPLETED = "ACTION_COMPLETED"
    ACTION_FAILED = "ACTION_FAILED"
    # Safety events.
    E_STOP_TRIGGERED = "E_STOP_TRIGGERED"
    ACTUATION_CANCELLED = "ACTUATION_CANCELLED"
    TARGET_VALIDATION_FAILED = "TARGET_VALIDATION_FAILED"
    SAFETY_STATE_CHANGED = "SAFETY_STATE_CHANGED"


class EventFamily(str, Enum):
    """The five plan section 3.2 event families."""

    OBSERVATION = "observation"
    COGNITIVE = "cognitive"
    CONVERSATIONAL = "conversational"
    EXECUTION = "execution"
    SAFETY = "safety"


class EventPriority(str, Enum):
    """Dispatch priority; lower rank is served first."""

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


EVENT_TYPES: tuple[EventType, ...] = tuple(EventType)

EVENT_FAMILY: dict[EventType, EventFamily] = {
    EventType.FILE_CREATED: EventFamily.OBSERVATION,
    EventType.FILE_MODIFIED: EventFamily.OBSERVATION,
    EventType.FILE_DELETED: EventFamily.OBSERVATION,
    EventType.AST_DELTA: EventFamily.OBSERVATION,
    EventType.GIT_CHANGED: EventFamily.OBSERVATION,
    EventType.CADENCE_UPDATED: EventFamily.OBSERVATION,
    EventType.DEVELOPER_STATE_UPDATED: EventFamily.COGNITIVE,
    EventType.PATTERN_DETECTED: EventFamily.COGNITIVE,
    EventType.INFERENCE_COMPLETED: EventFamily.COGNITIVE,
    EventType.ACTION_PROPOSED: EventFamily.COGNITIVE,
    EventType.VOICE_CAPTURED: EventFamily.CONVERSATIONAL,
    EventType.TRANSCRIPTION_COMPLETED: EventFamily.CONVERSATIONAL,
    EventType.INTENT_PARSED: EventFamily.CONVERSATIONAL,
    EventType.ACTION_CONFIRMATION_RECEIVED: EventFamily.CONVERSATIONAL,
    EventType.ACTION_REQUESTED: EventFamily.EXECUTION,
    EventType.ACTION_AUTHORIZED: EventFamily.EXECUTION,
    EventType.ACTION_STARTED: EventFamily.EXECUTION,
    EventType.ACTION_COMPLETED: EventFamily.EXECUTION,
    EventType.ACTION_FAILED: EventFamily.EXECUTION,
    EventType.E_STOP_TRIGGERED: EventFamily.SAFETY,
    EventType.ACTUATION_CANCELLED: EventFamily.SAFETY,
    EventType.TARGET_VALIDATION_FAILED: EventFamily.SAFETY,
    EventType.SAFETY_STATE_CHANGED: EventFamily.SAFETY,
}

DEFAULT_PRIORITY: dict[EventFamily, EventPriority] = {
    EventFamily.SAFETY: EventPriority.CRITICAL,
    EventFamily.EXECUTION: EventPriority.HIGH,
    EventFamily.CONVERSATIONAL: EventPriority.NORMAL,
    EventFamily.COGNITIVE: EventPriority.NORMAL,
    EventFamily.OBSERVATION: EventPriority.LOW,
}

PRIORITY_ORDER: tuple[EventPriority, ...] = (
    EventPriority.CRITICAL,
    EventPriority.HIGH,
    EventPriority.NORMAL,
    EventPriority.LOW,
)

DEFAULT_QUEUE_SIZES: dict[EventPriority, int] = {
    EventPriority.CRITICAL: 1024,
    EventPriority.HIGH: 512,
    EventPriority.NORMAL: 512,
    EventPriority.LOW: 256,
}


def _new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:12]}"


def _new_correlation_id() -> str:
    return f"corr_{uuid.uuid4().hex[:12]}"


def _coerce_event_type(value: Any) -> EventType:
    if isinstance(value, EventType):
        return value
    try:
        return EventType(str(value).strip())
    except ValueError:
        valid = sorted(t.value for t in EventType)
        raise EventValidationError(
            f"event_type: expected one of {valid}, got {value!r}"
        ) from None


class Event(_BaseModel):
    """One validated runtime event (plan section 3.2 contract)."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", str_strip_whitespace=True
    )

    event_id: str = Field(default_factory=_new_event_id, min_length=1)
    event_type: EventType
    source: str = Field(min_length=1)
    timestamp: float = Field(default_factory=time.time)
    session_id: str = ""
    priority: EventPriority = EventPriority.NORMAL
    correlation_id: str = Field(
        default_factory=_new_correlation_id, min_length=1
    )
    payload: dict[str, Any] = Field(default_factory=dict)
    expires_at: Optional[float] = None

    @field_validator("event_type", mode="before")
    @classmethod
    def _validate_type(cls, value: Any) -> EventType:
        return _coerce_event_type(value)

    @field_validator("priority", mode="before")
    @classmethod
    def _default_priority(cls, value: Any, info: Any) -> EventPriority:
        if value is None:
            raw_type = (info.data or {}).get("event_type")
            try:
                family = EVENT_FAMILY[_coerce_event_type(raw_type)]
            except EventValidationError:
                return EventPriority.NORMAL
            return DEFAULT_PRIORITY[family]
        if isinstance(value, EventPriority):
            return value
        try:
            return EventPriority(str(value).strip().lower())
        except ValueError:
            valid = sorted(p.value for p in EventPriority)
            raise EventValidationError(
                f"priority: expected one of {valid}, got {value!r}"
            ) from None

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, value: float) -> float:
        if not isinstance(value, (int, float)) or value != value:
            raise EventValidationError(
                f"timestamp: expected a finite epoch float, got {value!r}"
            )
        if value < 0:
            raise EventValidationError(
                f"timestamp: expected a non-negative epoch, got {value!r}"
            )
        return float(value)

    @field_validator("expires_at")
    @classmethod
    def _validate_expiry(cls, value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EventValidationError(
                f"expires_at: expected an epoch float or null, got {value!r}"
            )
        return float(value)

    @property
    def family(self) -> EventFamily:
        return EVENT_FAMILY[self.event_type]

    def is_expired(self, now: Optional[float] = None) -> bool:
        if self.expires_at is None:
            return False
        return (time.time() if now is None else now) > self.expires_at

    def child(
        self,
        event_type: Union[EventType, str],
        source: str,
        payload: Optional[dict[str, Any]] = None,
        **overrides: Any,
    ) -> "Event":
        """Build a follow-up event inheriting this event's correlation."""
        return create_event(
            event_type,
            source,
            payload=payload,
            correlation_id=self.correlation_id,
            session_id=self.session_id,
            **overrides,
        )

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: Any) -> "Event":
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise EventValidationError(str(exc)) from exc


def create_event(
    event_type: Union[EventType, str],
    source: str,
    payload: Optional[dict[str, Any]] = None,
    *,
    event_id: Optional[str] = None,
    session_id: str = "",
    priority: Optional[Union[EventPriority, str]] = None,
    correlation_id: Optional[str] = None,
    timestamp: Optional[float] = None,
    expires_at: Optional[float] = None,
) -> Event:
    """Build a validated event, defaulting priority from its family.

    Raises :class:`EventValidationError` on any contract violation.
    """
    data: dict[str, Any] = {
        "event_type": _coerce_event_type(event_type),
        "source": source,
        "payload": dict(payload) if payload else {},
        "session_id": session_id,
        "priority": priority,
        "expires_at": expires_at,
    }
    if event_id is not None:
        data["event_id"] = event_id
    if correlation_id is not None:
        data["correlation_id"] = correlation_id
    if timestamp is not None:
        data["timestamp"] = timestamp
    try:
        return Event.model_validate(data)
    except ValidationError as exc:
        raise EventValidationError(str(exc)) from exc


SyncHandler = Callable[[Event], None]
AsyncHandler = Callable[[Event], Awaitable[None]]
Handler = Union[SyncHandler, AsyncHandler]


@dataclass(frozen=True)
class ConsumerError:
    consumer: str
    error: str


@dataclass(frozen=True)
class DispatchResult:
    event_id: str
    event_type: EventType
    delivered: int
    failed: int = 0
    errors: tuple[ConsumerError, ...] = ()
    duplicate: bool = False
    expired: bool = False


@dataclass(frozen=True)
class EnqueueResult:
    event_id: str
    queued: bool
    duplicate: bool = False
    expired: bool = False
    dropped_oldest: bool = False


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    timestamp: float
    event_id: str
    event_type: str
    action: str
    consumer: str = ""
    ok: bool = True
    detail: str = ""


class Subscription:
    """Handle returned by subscribe; call :meth:`unsubscribe` to detach."""

    def __init__(
        self,
        bus: "EventBus",
        types: frozenset[EventType],
        handler: Handler,
        name: str,
    ) -> None:
        self._bus = bus
        self.types = types
        self.handler = handler
        self.name = name
        self.active = True

    def matches(self, event: Event) -> bool:
        return not self.types or event.event_type in self.types

    def unsubscribe(self) -> None:
        self._bus._detach(self)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        kinds = sorted(t.value for t in self.types) or ["*"]
        return f"Subscription({self.name}, types={kinds})"


class EventBus:
    """In-process dispatcher with sync fan-out and bounded async queues."""

    def __init__(
        self,
        *,
        queue_sizes: Optional[dict[EventPriority, int]] = None,
        max_seen: int = 10_000,
        max_claims: int = 10_000,
        max_correlations: int = 1_000,
        audit_size: int = 1_000,
    ) -> None:
        sizes = dict(DEFAULT_QUEUE_SIZES)
        if queue_sizes:
            for priority, size in queue_sizes.items():
                if not isinstance(size, int) or size < 1:
                    raise ValueError(
                        "queue_sizes: expected a positive integer maxsize "
                        f"for {priority!r}, got {size!r}"
                    )
                sizes[EventPriority(priority)] = size
        self._queue_sizes = sizes
        self._queues: dict[EventPriority, asyncio.Queue[Event]] = {
            priority: asyncio.Queue(maxsize=size)
            for priority, size in sizes.items()
        }
        self._queued = asyncio.Event()
        self._lock = threading.Lock()
        self._subscriptions: list[Subscription] = []
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._max_seen = max(1, max_seen)
        self._claims: OrderedDict[str, None] = OrderedDict()
        self._max_claims = max(1, max_claims)
        self._correlations: OrderedDict[str, deque[str]] = OrderedDict()
        self._max_correlations = max(1, max_correlations)
        self._audit: deque[AuditRecord] = deque(maxlen=max(1, audit_size))
        self._seq = 0
        self._counters = {
            "published": 0,
            "dispatched": 0,
            "duplicates": 0,
            "expired": 0,
            "dropped": 0,
            "errors": 0,
        }
        self._workers: list[asyncio.Task[None]] = []
        self._stopping = False

    # -- subscriptions ---------------------------------------------------
    def subscribe(
        self,
        *event_types: Union[EventType, str, Handler],
        handler: Optional[Handler] = None,
        name: Optional[str] = None,
    ) -> Subscription:
        """Subscribe ``handler`` to event types (none = every event)."""
        resolved = handler
        types: set[EventType] = set()
        for item in event_types:
            if callable(item) and not isinstance(item, (str, EventType)):
                if resolved is not None and resolved is not item:
                    raise ValueError(
                        "subscribe: expected a single handler callable"
                    )
                resolved = item  # type: ignore[assignment]
            else:
                assert isinstance(item, (str, EventType))
                types.add(_coerce_event_type(item))
        if resolved is None or not callable(resolved):
            raise ValueError("subscribe: expected a handler callable")
        token = Subscription(
            self,
            frozenset(types),
            resolved,
            name or getattr(resolved, "__name__", "handler"),
        )
        with self._lock:
            self._subscriptions.append(token)
        return token

    def subscribe_family(
        self, family: Union[EventFamily, str], handler: Handler,
        name: Optional[str] = None,
    ) -> Subscription:
        if isinstance(family, EventFamily):
            wanted = family
        else:
            try:
                wanted = EventFamily(str(family).strip().lower())
            except ValueError:
                valid = sorted(f.value for f in EventFamily)
                raise ValueError(
                    f"family: expected one of {valid}, got {family!r}"
                ) from None
        types = [t for t, fam in EVENT_FAMILY.items() if fam == wanted]
        return self.subscribe(*types, handler=handler, name=name)

    def _detach(self, token: Subscription) -> None:
        with self._lock:
            if token in self._subscriptions:
                self._subscriptions.remove(token)
            token.active = False

    def _snapshot(self, event: Event) -> list[Subscription]:
        with self._lock:
            return [s for s in self._subscriptions if s.matches(event)]

    # -- idempotency and correlation --------------------------------------
    def _remember(self, event_id: str) -> bool:
        """Record an id; True when first seen, False for duplicates."""
        with self._lock:
            if event_id in self._seen:
                return False
            self._seen[event_id] = None
            while len(self._seen) > self._max_seen:
                self._seen.popitem(last=False)
            return True

    def claim_action(self, key: str) -> bool:
        """Claim a one-shot action key; False when already executed."""
        if not isinstance(key, str) or not key.strip():
            raise ValueError("claim_action: expected a non-empty key")
        with self._lock:
            if key in self._claims:
                return False
            self._claims[key] = None
            while len(self._claims) > self._max_claims:
                self._claims.popitem(last=False)
            return True

    def _track_correlation(self, event: Event) -> None:
        with self._lock:
            chain = self._correlations.get(event.correlation_id)
            if chain is None:
                chain = deque(maxlen=100)
                self._correlations[event.correlation_id] = chain
            else:
                self._correlations.move_to_end(event.correlation_id)
            chain.append(event.event_id)
            while len(self._correlations) > self._max_correlations:
                self._correlations.popitem(last=False)

    def events_for_correlation(self, correlation_id: str) -> list[str]:
        with self._lock:
            chain = self._correlations.get(correlation_id)
            return list(chain) if chain else []

    # -- audit ------------------------------------------------------------
    def _record(
        self,
        event: Event,
        action: str,
        *,
        consumer: str = "",
        ok: bool = True,
        detail: str = "",
    ) -> None:
        with self._lock:
            self._seq += 1
            self._audit.append(
                AuditRecord(
                    seq=self._seq,
                    timestamp=time.time(),
                    event_id=event.event_id,
                    event_type=event.event_type.value,
                    action=action,
                    consumer=consumer,
                    ok=ok,
                    detail=detail,
                )
            )

    def _bump(self, counter: str) -> None:
        with self._lock:
            self._counters[counter] += 1

    def audit_log(
        self, limit: Optional[int] = None, *, newest_first: bool = False
    ) -> list[AuditRecord]:
        with self._lock:
            records = list(self._audit)
        if newest_first:
            records.reverse()
        if limit is not None:
            records = records[: max(0, limit)]
        return records

    def audit_for_event(self, event_id: str) -> list[AuditRecord]:
        with self._lock:
            return [r for r in self._audit if r.event_id == event_id]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(self._counters)
            subscribers = len(self._subscriptions)
            seen = len(self._seen)
            claims = len(self._claims)
            correlations = len(self._correlations)
        queued = {p.value: q.qsize() for p, q in self._queues.items()}
        return {
            **counters,
            "subscribers": subscribers,
            "seen_ids": seen,
            "claimed_actions": claims,
            "correlations": correlations,
            "queued": queued,
            "workers": len(self._workers),
        }

    # -- sync dispatch ----------------------------------------------------
    def publish(self, event: Event) -> DispatchResult:
        """Validate + fan out immediately on the caller's thread.

        Async subscribers run inline via an isolated loop when no loop
        is running, otherwise they are scheduled on the running loop
        (counted as delivered; completion lands in the audit log).
        """
        if not isinstance(event, Event):
            raise EventValidationError(
                f"publish: expected Event, got {type(event).__name__}"
            )
        if event.is_expired():
            self._bump("expired")
            self._record(event, "expired", ok=False, detail="past expires_at")
            return DispatchResult(
                event.event_id, event.event_type, 0, expired=True
            )
        if not self._remember(event.event_id):
            self._bump("duplicates")
            self._record(event, "duplicate", ok=False,
                         detail="event_id already dispatched")
            return DispatchResult(
                event.event_id, event.event_type, 0, duplicate=True
            )
        self._track_correlation(event)
        self._bump("published")
        self._record(event, "received")
        delivered, errors = self._dispatch_sync(event)
        return DispatchResult(
            event.event_id, event.event_type, delivered,
            failed=len(errors), errors=tuple(errors),
        )

    def _dispatch_sync(
        self, event: Event
    ) -> tuple[int, list[ConsumerError]]:
        """Fan out on the caller's thread with failure isolation."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        delivered = 0
        errors: list[ConsumerError] = []
        for sub in self._snapshot(event):
            if asyncio.iscoroutinefunction(sub.handler) and loop is not None:
                self._schedule_async(sub, event, loop)
                delivered += 1
                self._record(event, "scheduled", consumer=sub.name)
                continue
            if asyncio.iscoroutinefunction(sub.handler):
                outcome = self._run_async_blocking(sub, event)
            else:
                outcome = self._run_sync_handler(sub, event)
            if outcome is None:
                delivered += 1
                self._bump("dispatched")
                self._record(event, "dispatched", consumer=sub.name)
            else:
                errors.append(outcome)
                self._bump("errors")
                self._record(event, "error", consumer=sub.name, ok=False,
                             detail=outcome.error)
        return delivered, errors

    def _run_sync_handler(
        self, sub: Subscription, event: Event
    ) -> Optional[ConsumerError]:
        try:
            result = sub.handler(event)  # type: ignore[operator]
        except Exception as exc:
            return ConsumerError(sub.name, f"{type(exc).__name__}: {exc}")
        if asyncio.iscoroutine(result):
            result.close()
            return ConsumerError(
                sub.name,
                "async handler returned a coroutine; use apublish/start "
                "or publish from a running event loop",
            )
        return None

    def _schedule_async(
        self,
        sub: Subscription,
        event: Event,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        async def _invoke() -> None:
            await sub.handler(event)  # type: ignore[misc]

        task = loop.create_task(_invoke())

        def _done(done: asyncio.Task[None]) -> None:
            try:
                done.result()
            except Exception as exc:
                self._bump("errors")
                self._record(
                    event, "error", consumer=sub.name, ok=False,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            else:
                self._bump("dispatched")
                self._record(event, "dispatched", consumer=sub.name)

        task.add_done_callback(_done)

    def _run_async_blocking(
        self, sub: Subscription, event: Event
    ) -> Optional[ConsumerError]:
        async def _invoke() -> None:
            await sub.handler(event)  # type: ignore[misc]

        try:
            asyncio.run(_invoke())
        except Exception as exc:
            return ConsumerError(sub.name, f"{type(exc).__name__}: {exc}")
        return None

    def emit(
        self,
        event_type: Union[EventType, str],
        source: str,
        payload: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Optional[Event]:
        """Publish best-effort; never raises (wiring-path helper)."""
        try:
            event = create_event(event_type, source, payload, **kwargs)
        except Exception:
            return None
        try:
            self.publish(event)
        except Exception:
            return None
        return event

    # -- async dispatch ---------------------------------------------------
    @property
    def running(self) -> bool:
        return bool(self._workers)

    def enqueue(self, event: Event) -> EnqueueResult:
        """Validate + enqueue for deferred dispatch (bounded, no loop).

        Admission never blocks: when the event's priority queue is full
        the oldest queued event of that priority is shed (recorded in
        the audit log) so the newest observation wins.
        """
        if not isinstance(event, Event):
            raise EventValidationError(
                f"enqueue: expected Event, got {type(event).__name__}"
            )
        if event.is_expired():
            self._bump("expired")
            self._record(event, "expired", ok=False, detail="past expires_at")
            return EnqueueResult(event.event_id, queued=False, expired=True)
        if not self._remember(event.event_id):
            self._bump("duplicates")
            self._record(event, "duplicate", ok=False,
                         detail="event_id already dispatched")
            return EnqueueResult(event.event_id, queued=False, duplicate=True)
        self._track_correlation(event)
        self._bump("published")
        queue = self._queues[event.priority]
        dropped = False
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                shed = queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - lost race
                shed = None
            dropped = True
            self._bump("dropped")
            self._record(
                event, "dropped", ok=False,
                detail=(f"shed oldest {shed.event_id} "
                        f"({event.priority.value} queue full)")
                if shed is not None else "queue full",
            )
            queue.put_nowait(event)
        self._record(event, "enqueued")
        self._queued.set()
        return EnqueueResult(event.event_id, queued=True,
                             dropped_oldest=dropped)

    async def apublish(self, event: Event) -> EnqueueResult:
        """Async alias for :meth:`enqueue` (async call-site symmetry)."""
        return self.enqueue(event)

    def pump(self, max_events: Optional[int] = None) -> list[DispatchResult]:
        """Dispatch queued events, highest priority first, on this thread.

        A loop-free alternative to :meth:`start` for hosts that must not
        own an event loop (e.g. Qt threads): call it from a timer or an
        idle handler. At most the events already queued are dispatched.
        """
        if max_events is not None and max_events < 1:
            raise ValueError("pump: expected max_events >= 1")
        budget = sum(q.qsize() for q in self._queues.values())
        if max_events is not None:
            budget = min(budget, max_events)
        results: list[DispatchResult] = []
        for _ in range(budget):
            event = self._pop_highest()
            if event is None:
                break
            if event.is_expired():
                self._bump("expired")
                self._record(event, "expired", ok=False,
                             detail="expired while queued")
                results.append(DispatchResult(
                    event.event_id, event.event_type, 0, expired=True))
                continue
            delivered, errors = self._dispatch_sync(event)
            results.append(DispatchResult(
                event.event_id, event.event_type, delivered,
                failed=len(errors), errors=tuple(errors)))
        return results

    async def start(self, worker_count: int = 1) -> None:
        """Start background dispatch workers (single = strict order)."""
        if worker_count < 1:
            raise ValueError("start: expected worker_count >= 1")
        if self._workers:
            return
        self._stopping = False
        loop = asyncio.get_running_loop()
        self._workers = [
            loop.create_task(self._worker_loop(), name=f"mimir-events-{i}")
            for i in range(worker_count)
        ]

    async def stop(self, drain: bool = True) -> None:
        """Stop workers, optionally draining queued events first."""
        if not self._workers:
            return
        if drain:
            await self.drain()
        self._stopping = True
        self._queued.set()
        workers, self._workers = self._workers, []
        for worker in workers:
            worker.cancel()
        for worker in workers:
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self._stopping = False

    async def drain(self, timeout: Optional[float] = 30.0) -> bool:
        """Wait until every queue is empty; False on timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while any(q.qsize() for q in self._queues.values()):
            if deadline is not None and time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.005)
        return True

    def _pop_highest(self) -> Optional[Event]:
        for priority in PRIORITY_ORDER:
            try:
                return self._queues[priority].get_nowait()
            except asyncio.QueueEmpty:
                continue
        return None

    async def _worker_loop(self) -> None:
        while True:
            if self._stopping and not any(
                q.qsize() for q in self._queues.values()
            ):
                return
            event = self._pop_highest()
            if event is None:
                self._queued.clear()
                event = self._pop_highest()
                if event is None:
                    try:
                        await asyncio.wait_for(self._queued.wait(), 0.05)
                    except asyncio.TimeoutError:
                        continue
                    continue
            await self._dispatch_async(event)

    async def _dispatch_async(self, event: Event) -> DispatchResult:
        if event.is_expired():
            self._bump("expired")
            self._record(event, "expired", ok=False,
                         detail="expired while queued")
            return DispatchResult(
                event.event_id, event.event_type, 0, expired=True
            )
        targets = self._snapshot(event)
        delivered = 0
        errors: list[ConsumerError] = []
        for sub in targets:
            try:
                result = sub.handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                errors.append(
                    ConsumerError(sub.name, f"{type(exc).__name__}: {exc}")
                )
                self._bump("errors")
                self._record(event, "error", consumer=sub.name, ok=False,
                             detail=f"{type(exc).__name__}: {exc}")
            else:
                delivered += 1
                self._bump("dispatched")
                self._record(event, "dispatched", consumer=sub.name)
        return DispatchResult(
            event.event_id, event.event_type, delivered,
            failed=len(errors), errors=tuple(errors),
        )

    # -- test helpers ------------------------------------------------------
    def reset_idempotency(self) -> None:
        """Clear seen ids, action claims, and correlation chains."""
        with self._lock:
            self._seen.clear()
            self._claims.clear()
            self._correlations.clear()


_VISOR_BY_EVENT: dict[EventType, str] = {
    EventType.FILE_CREATED: "observing",
    EventType.FILE_MODIFIED: "observing",
    EventType.FILE_DELETED: "observing",
    EventType.AST_DELTA: "observing",
    EventType.GIT_CHANGED: "observing",
    EventType.CADENCE_UPDATED: "observing",
    EventType.DEVELOPER_STATE_UPDATED: "observing",
    EventType.VOICE_CAPTURED: "listening",
    EventType.TRANSCRIPTION_COMPLETED: "listening",
    EventType.INTENT_PARSED: "thinking",
    EventType.PATTERN_DETECTED: "thinking",
    EventType.INFERENCE_COMPLETED: "thinking",
    EventType.ACTION_PROPOSED: "thinking",
    EventType.ACTION_CONFIRMATION_RECEIVED: "thinking",
    EventType.ACTION_REQUESTED: "thinking",
    EventType.ACTION_AUTHORIZED: "thinking",
    EventType.ACTION_STARTED: "driving",
    EventType.ACTION_COMPLETED: "resting",
    EventType.ACTION_FAILED: "resting",
    EventType.E_STOP_TRIGGERED: "alert",
    EventType.ACTUATION_CANCELLED: "alert",
    EventType.TARGET_VALIDATION_FAILED: "alert",
    EventType.SAFETY_STATE_CHANGED: "alert",
}


def bind_visor_to_bus(
    controller: Any, bus: Optional[EventBus] = None
) -> list[Subscription]:
    """Mirror runtime events onto a visor state machine (opt-in wiring).

    ``controller`` is duck-typed (only ``set_state`` is used) so the
    binding stays Qt-free and headless-testable.
    """
    target = bus if bus is not None else get_event_bus()
    tokens: list[Subscription] = []
    for event_type, state in _VISOR_BY_EVENT.items():
        def _follow(
            _event: Event, _state: str = state,
            _controller: Any = controller,
        ) -> None:
            try:
                _controller.set_state(_state)
            except Exception:
                pass

        tokens.append(
            target.subscribe(event_type, _follow,
                             name=f"visor:{state}")
        )
    return tokens


_global_lock = threading.Lock()
_global_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Process-wide bus used by default wiring (CLI, desktop, actuator)."""
    global _global_bus
    with _global_lock:
        if _global_bus is None:
            _global_bus = EventBus()
        return _global_bus


def set_event_bus(bus: EventBus) -> None:
    """Replace the process-wide bus (tests, embedding hosts)."""
    global _global_bus
    if not isinstance(bus, EventBus):
        raise ValueError(
            f"set_event_bus: expected EventBus, got {type(bus).__name__}"
        )
    with _global_lock:
        _global_bus = bus


def reset_event_bus() -> EventBus:
    """Install a fresh process-wide bus; returns it (test helper)."""
    global _global_bus
    with _global_lock:
        _global_bus = EventBus()
        return _global_bus
