"""Independent safety controller + E-stop (plan section 8, step 6).

The safety controller is a separate safety responsibility, not another
ordinary background task. It owns the global E-stop state, invalidates
outstanding actuation commands, rejects new physical operations while
stopped, and keeps an audit log that is independent of ordinary
application logs.

Independence: this module imports only the Python standard library. It
does not depend on Whisper transcription, Ollama/LLM inference, the
central event queue, PyQt6 rendering, ordinary telemetry processing, or
any actuator worker. Trip/reset/check therefore keep working when any
of those subsystems is blocked, broken, or absent. Dependencies run one
way only: consumers (:mod:`mimir.actions`, :mod:`mimir.actuator`,
:mod:`mimir.voice_action`) poll this controller; it never calls them,
never publishes to the event bus, and never invokes subscriber
callbacks (a trip must not block on a stuck listener). Consumers
announce trips best-effort on their own side (e.g. ``E_STOP_TRIGGERED``).

Fail-closed semantics:

- :meth:`SafetyController.trip` transitions to ``STOPPED`` immediately,
  bumps the invalidation generation (every outstanding token/pending
  action dies), and records the trip. It never raises.
- :meth:`SafetyController.check` raises :class:`SafetyTrippedError`
  while stopped, or when a pre-trip token is presented after any trip.
- :meth:`SafetyController.reset` requires a deliberate, non-empty
  ``authorization`` (operator identity + reason); blank reauth is
  rejected, and the generation is *not* rewound, so pre-trip tokens
  stay dead after a resume.
- If authorization, target validation, or safety state cannot be
  established, actuation must not proceed.

Timing note (plan section 8): a normal Python thread in a Windows
desktop application cannot provide a hard real-time guarantee. Trip
delivery here is a ``threading.Event`` plus fail-closed polls at
pre-execution and per-step/per-sample checkpoints, which halts motion,
typing, and subprocess execution within milliseconds in practice, but
for a genuine strict sub-10-ms safety requirement use a separately
scheduled native controller or an appropriate hardware-backed
mechanism.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Union

__all__ = [
    "SafetyController",
    "SafetyError",
    "SafetyRecord",
    "SafetyState",
    "SafetyTrippedError",
    "get_safety_controller",
    "reset_safety_controller",
    "set_safety_controller",
]

#: Default cap for the in-memory safety audit log (oldest dropped first).
DEFAULT_MAX_RECORDS = 1024


class SafetyError(Exception):
    """Base error for safety-controller misuse (bad reset auth, ...)."""


class SafetyTrippedError(SafetyError):
    """Raised fail-closed while the E-stop is active or a token is stale."""


class SafetyState(str, Enum):
    """Global safety state: nominal operation or E-stop latched."""

    NOMINAL = "nominal"
    STOPPED = "stopped"


@dataclass(frozen=True)
class SafetyRecord:
    """One safety audit entry (trip or deliberate resume)."""

    seq: int
    state: SafetyState
    source: str
    reason: str
    generation: int
    timestamp: float
    monotonic: float
    actor: str = ""
    authorization: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "state": self.state.value,
            "source": self.source,
            "reason": self.reason,
            "generation": self.generation,
            "timestamp": self.timestamp,
            "monotonic": self.monotonic,
            "actor": self.actor,
            "authorization": self.authorization,
        }


def _coerce_text(value: Any, *, field: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


class SafetyController:
    """Thread-safe E-stop latch with generation-based invalidation.

    :param log_path: optional append-only JSON-lines audit sink. Writes
        are best-effort: a failing sink never breaks a trip or reset.
    :param max_records: cap for the in-memory audit log (positive int).
    """

    def __init__(
        self,
        *,
        log_path: Optional[Union[str, bytes, Any]] = None,
        max_records: int = DEFAULT_MAX_RECORDS,
    ) -> None:
        if (
            isinstance(max_records, bool)
            or not isinstance(max_records, int)
            or max_records < 1
        ):
            raise SafetyError(
                f"max_records: expected a positive integer, "
                f"got {max_records!r}"
            )
        if log_path is not None and not isinstance(
            log_path, (str, bytes)
        ):
            path_str = _coerce_text(log_path, field="log_path")
            if not path_str:
                raise SafetyError(
                    f"log_path: expected a path or None, "
                    f"got {log_path!r}"
                )
            log_path = path_str
        self._log_path = log_path
        self._lock = threading.Lock()
        self._estop = threading.Event()
        self._state = SafetyState.NOMINAL
        self._generation = 0
        self._seq = 0
        self._records: deque[SafetyRecord] = deque(maxlen=max_records)
        self._last_trip: Optional[SafetyRecord] = None

    # -- state ---------------------------------------------------------
    @property
    def state(self) -> SafetyState:
        """Current safety state (:attr:`SafetyState.NOMINAL`/``STOPPED``)."""
        with self._lock:
            return self._state

    @property
    def estopped(self) -> bool:
        """True while the E-stop is latched (fail-closed reads)."""
        return self._estop.is_set()

    @property
    def generation(self) -> int:
        """Invalidation epoch: bumped by every trip, never rewound."""
        with self._lock:
            return self._generation

    @property
    def event(self) -> threading.Event:
        """The latch event (set while stopped) for hosts that wait on it."""
        return self._estop

    @property
    def last_trip(self) -> Optional[SafetyRecord]:
        """Most recent trip record, or None when never tripped."""
        with self._lock:
            return self._last_trip

    # -- transitions ---------------------------------------------------
    def trip(self, source: str = "", reason: str = "") -> SafetyRecord:
        """Latch the E-stop and invalidate everything outstanding.

        Never raises: argument coercion and the audit sink are
        best-effort so a trip always lands, even under misuse or I/O
        failure. Every call bumps the generation, so work proposed
        during a stop is invalidated by the next trip as well.
        """
        try:
            src = _coerce_text(source, field="source").strip() or "unknown"
            why = _coerce_text(reason, field="reason").strip()
        except Exception:
            src, why = "unknown", ""
        with self._lock:
            self._state = SafetyState.STOPPED
            self._estop.set()
            self._generation += 1
            record = SafetyRecord(
                seq=self._seq,
                state=SafetyState.STOPPED,
                source=src,
                reason=why,
                generation=self._generation,
                timestamp=time.time(),
                monotonic=time.monotonic(),
            )
            self._seq += 1
            self._records.append(record)
            self._last_trip = record
        self._append_to_sink(record)
        return record

    def reset(self, *, authorization: str, actor: str = "") -> bool:
        """Resume nominal operation after deliberate reauthorization.

        ``authorization`` must be a non-empty operator identity/reason;
        blank reauth raises :class:`SafetyError` and changes nothing.
        Returns True when a latched stop was cleared, False when the
        controller was already nominal (no audit entry for no-ops).
        The generation is preserved so pre-trip tokens stay invalid.
        """
        auth = _coerce_text(authorization, field="authorization").strip()
        if not auth:
            raise SafetyError(
                "reset requires deliberate reauthorization: pass a "
                "non-empty authorization (operator identity + reason)"
            )
        who = _coerce_text(actor, field="actor").strip()
        with self._lock:
            if self._state is SafetyState.NOMINAL:
                return False
            self._state = SafetyState.NOMINAL
            self._estop.clear()
            record = SafetyRecord(
                seq=self._seq,
                state=SafetyState.NOMINAL,
                source="deliberate-reset",
                reason="",
                generation=self._generation,
                timestamp=time.time(),
                monotonic=time.monotonic(),
                actor=who,
                authorization=auth,
            )
            self._seq += 1
            self._records.append(record)
        self._append_to_sink(record)
        return True

    # -- fail-closed gates ----------------------------------------------
    def token(self) -> int:
        """Current generation token; present it to :meth:`check` later."""
        with self._lock:
            return self._generation

    def check(self, token: Optional[int] = None) -> int:
        """Raise :class:`SafetyTrippedError` unless actuation may proceed.

        Fails when the E-stop is latched, or when ``token`` predates
        any trip (stale tokens never come back to life, not even after
        a deliberate reset). Returns the current generation on success.
        """
        if token is not None and (
            isinstance(token, bool) or not isinstance(token, int)
        ):
            raise SafetyError(
                f"token: expected an int generation or None, "
                f"got {token!r}"
            )
        with self._lock:
            state = self._state
            generation = self._generation
            last = self._last_trip
        if state is SafetyState.STOPPED:
            detail = ""
            if last is not None:
                detail = f" (source={last.source!r}"
                if last.reason:
                    detail += f", reason={last.reason!r}"
                detail += f", generation={last.generation})"
            raise SafetyTrippedError(f"safety stop active{detail}")
        if token is not None and token != generation:
            raise SafetyTrippedError(
                f"safety token invalidated by E-stop "
                f"(token generation {token}, current {generation})"
            )
        return generation

    # -- audit ----------------------------------------------------------
    def safety_log(self, limit: Optional[int] = None) -> tuple[SafetyRecord, ...]:
        """Independent safety audit log, oldest first (``limit`` = last N)."""
        if limit is not None and (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
        ):
            raise SafetyError(
                f"limit: expected a positive integer or None, "
                f"got {limit!r}"
            )
        with self._lock:
            records = tuple(self._records)
        if limit is not None:
            records = records[-limit:]
        return records

    def _append_to_sink(self, record: SafetyRecord) -> None:
        if self._log_path is None:
            return
        try:
            with open(self._log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.to_dict()) + "\n")
        except Exception:
            pass  # a failing sink must never break a trip or reset


_global_lock = threading.Lock()
_global_safety: Optional[SafetyController] = None


def get_safety_controller() -> SafetyController:
    """Process-global safety controller (created on first use)."""
    global _global_safety
    with _global_lock:
        if _global_safety is None:
            _global_safety = SafetyController()
        return _global_safety


def set_safety_controller(controller: SafetyController) -> None:
    """Install the process-global safety controller."""
    global _global_safety
    if not isinstance(controller, SafetyController):
        raise SafetyError(
            "controller: expected SafetyController, "
            f"got {type(controller).__name__}"
        )
    with _global_lock:
        _global_safety = controller


def reset_safety_controller() -> None:
    """Forget the process-global safety controller (tests re-isolate)."""
    global _global_safety
    with _global_lock:
        _global_safety = None
