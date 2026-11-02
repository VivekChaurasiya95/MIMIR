"""Bounded embodiment: gated actuation, triple E-stop, outcome verification.

Phase 4 of the MIMIR roadmap. Three safety-first building blocks around
:class:`mimir.actuator.DesktopActuator`:

- :class:`ActuationGateway`: every physical operation runs inside a
  validated focus session (window focus + process identity via
  :mod:`mimir.desktop`). Focus loss *pauses* between steps instead of
  injecting blindly; identity change (process/executable) always fails
  closed immediately.
- :class:`TripleFailsafe`: three independent E-stop channels — physical
  touch (mouse displacement / keys), a global ESC hotkey latch, and
  spoken stop — all latching the shared :class:`SafetyController`.
  Abort-to-latch latency is measured against a 50 ms budget.
- :class:`OutcomeVerifier`: post-actuation checks (test command, git
  status, file-tree diff) that halt the system on any failure and offer
  1-click :meth:`OutcomeVerifier.rollback` recovery. Rollback works
  while the E-stop is latched: recovery must never need a resume first.

Headless-testable: hardware (mouse/keyboard/failsafe/window backends),
the test runner, and the git reader are all injectable seams. Live
backends degrade per-channel to a disarmed-but-manual mode instead of
breaking construction, mirroring :class:`DesktopActuator`.
"""

from __future__ import annotations

import hashlib
import math
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Sequence, Union

from ._common import check_bool, check_int, check_str
from .actuator import (
    ActuatorError,
    CobotInterruptedError,
    CursorPoint,
    DefaultFailsafe,
    DesktopActuator,
    ExecutionPlan,
    TargetValidationError,
)
from .desktop import TargetSpec, WindowInfo
from .events import EventBus, EventType
from .safety import SafetyController, SafetyRecord, SafetyTrippedError
from .watch_fs import should_exclude

__all__ = [
    "ActuationGateway",
    "ESTOP_LATENCY_BUDGET_MS",
    "EmbodimentError",
    "EscHotkeyLatch",
    "EstopTrip",
    "FileEntry",
    "FileSnapshot",
    "FocusLostError",
    "OutcomeVerifier",
    "RollbackAction",
    "RollbackReport",
    "SPOKEN_STOP_PHRASES",
    "TripleFailsafe",
    "VerificationCheck",
    "VerificationReport",
    "is_spoken_stop",
]

_GATEWAY_SOURCE = "actuation.gateway"
_ESTOP_SOURCE = "safety.triple_estop"
_VERIFY_SOURCE = "actuation.verifier"

#: Abort-to-latch latency budget (ms) for every E-stop channel. The trip
#: path is a threading.Event set + fail-closed polls (no I/O on the hot
#: path), so a healthy host latches in well under a millisecond; the
#: budget keeps generous slack for loaded CI while still catching a
#: genuinely slow (e.g. blocking-callback) trip path.
ESTOP_LATENCY_BUDGET_MS = 50.0

#: Spoken phrases that trip the spoken-stop channel. Mirrors the "stop"
#: triggers of :mod:`mimir.voice` (stop beats everything there too), so
#: the voice pipeline and the E-stop agree on what "stop" means.
SPOKEN_STOP_PHRASES = (
    "stop",
    "halt",
    "freeze",
    "cancel",
    "abort",
    "emergency",
    "kill it",
    "never mind",
    "forget it",
    "quiet",
    "shut up",
)

_TRIP_MESSAGE = "human override: cobot motion interrupted"


def is_spoken_stop(text: str) -> bool:
    """True when ``text`` carries a spoken-stop phrase (case-insensitive).

    Substring semantics, matching :meth:`VoiceCoordinator.parse_intent`:
    "stop" beats everything, so "please stop now" trips. Non-string
    input never trips (fail-closed for the detector itself: the voice
    pipeline trips explicitly via :meth:`TripleFailsafe.notify_stop_intent`
    for structured STOP intents instead).
    """
    if not isinstance(text, str) or not text.strip():
        return False
    lowered = text.lower()
    return any(phrase in lowered for phrase in SPOKEN_STOP_PHRASES)


class EmbodimentError(ActuatorError):
    """Base error for bounded-embodiment failures."""


class FocusLostError(EmbodimentError):
    """Raised fail-closed when the validated target loses foreground.

    Carries a machine-readable ``reason`` (``focus_lost``,
    ``focus_timeout``, ``pause_budget_exhausted``) alongside the message.
    """

    def __init__(self, message: str, *, reason: str = "focus_lost"):
        super().__init__(message)
        self.reason = reason


def _positive_seconds(value: Any, *, field: str) -> float:
    if (
        type(value) is bool
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{field}: expected a positive number of seconds")
    return float(value)


def _coerce_target(
    target: Union[TargetSpec, WindowInfo, str],
) -> Union[TargetSpec, WindowInfo, str]:
    if isinstance(target, (TargetSpec, WindowInfo, str)):
        if isinstance(target, str) and not target.strip():
            raise ValueError("target: expected a non-empty selector string")
        return target
    raise ValueError(
        "target: expected TargetSpec, WindowInfo, or str, "
        f"got {type(target).__name__}"
    )


# ---------------------------------------------------------------------------
# ActuationGateway: focus + identity gated actuation, pause on focus loss
# ---------------------------------------------------------------------------


class ActuationGateway:
    """Focus- and identity-gated front door for physical actuation.

    Every operation runs inside a validated focus session: resolve the
    ``target`` -> activate -> verify foreground -> validate process
    identity -> act -> re-validate (a mid-operation target change
    invalidates the outcome) -> restore companion focus. The wrapped
    :class:`DesktopActuator` is never driven directly by callers.

    Focus loss *pauses* between steps (``pause_on_focus_loss=True``):
    the gateway waits for the target to regain the foreground instead
    of injecting into whatever stole it. Process/executable identity
    change never pauses — it fails closed immediately, because a new
    process behind the same handle is a different target, not a blip.
    A latched :class:`SafetyController` rejects everything before any
    step runs.
    """

    def __init__(
        self,
        actuator: DesktopActuator,
        target: Union[TargetSpec, WindowInfo, str],
        *,
        safety: Optional[SafetyController] = None,
        event_bus: Optional[EventBus] = None,
        focus_poll_sec: float = 0.05,
        pause_on_focus_loss: bool = True,
        pause_timeout_sec: float = 30.0,
        max_pauses: int = 5,
        authorized: bool = True,
        on_pause: Optional[Callable[[WindowInfo], None]] = None,
        on_resume: Optional[Callable[[WindowInfo], None]] = None,
    ) -> None:
        if not isinstance(actuator, DesktopActuator):
            raise ValueError(
                "actuator: expected DesktopActuator, "
                f"got {type(actuator).__name__}"
            )
        if safety is not None and not isinstance(safety, SafetyController):
            raise ValueError(
                "safety: expected SafetyController or None, "
                f"got {type(safety).__name__}"
            )
        if event_bus is not None and not isinstance(event_bus, EventBus):
            raise ValueError(
                "event_bus: expected EventBus or None, "
                f"got {type(event_bus).__name__}"
            )
        if on_pause is not None and not callable(on_pause):
            raise ValueError("on_pause: expected a callable or None")
        if on_resume is not None and not callable(on_resume):
            raise ValueError("on_resume: expected a callable or None")
        self._actuator = actuator
        self._target = _coerce_target(target)
        self._safety = safety
        self._event_bus = event_bus
        self._focus_poll_sec = _positive_seconds(
            focus_poll_sec, field="focus_poll_sec"
        )
        self._pause_on_focus_loss = check_bool(
            pause_on_focus_loss, field="pause_on_focus_loss"
        )
        self._pause_timeout_sec = _positive_seconds(
            pause_timeout_sec, field="pause_timeout_sec"
        )
        self._max_pauses = check_int(max_pauses, field="max_pauses", minimum=0)
        self._authorized = check_bool(authorized, field="authorized")
        self._on_pause = on_pause
        self._on_resume = on_resume
        self._paused = False
        self._pause_count = 0

    @property
    def actuator(self) -> DesktopActuator:
        """The wrapped actuator (driven only through this gateway)."""
        return self._actuator

    @property
    def safety(self) -> Optional[SafetyController]:
        """Attached safety controller, or None when ungated."""
        return self._safety

    @property
    def paused(self) -> bool:
        """True while a focus-loss pause is in effect."""
        return self._paused

    @property
    def pause_count(self) -> int:
        """Pauses taken so far (across all runs on this gateway)."""
        return self._pause_count

    def _poll(self) -> None:
        if self._actuator.interrupted:
            raise CobotInterruptedError(_TRIP_MESSAGE)
        if self._safety is not None:
            try:
                self._safety.check()
            except SafetyTrippedError as exc:
                raise CobotInterruptedError(f"safety stop: {exc}") from exc

    def _emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        if self._event_bus is None:
            return
        try:
            self._event_bus.emit(event_type, _GATEWAY_SOURCE, payload)
        except Exception:
            pass  # telemetry must never break fail-closed gating

    def _expected(self, *, operation: str) -> WindowInfo:
        """Resolve the gateway target (WindowInfo passes through)."""
        if isinstance(self._target, WindowInfo):
            return self._target
        return self._actuator.resolve_target(self._target, operation=operation)

    def resolve(self, *, operation: str = "gateway.resolve") -> WindowInfo:
        """Resolve the gateway target to exactly one live window snapshot."""
        self._poll()
        return self._expected(operation=operation)

    @contextmanager
    def session(
        self,
        *,
        preserve: bool = True,
        verify: bool = True,
        operation: str = "gateway.session",
    ) -> Iterator[WindowInfo]:
        """Validated focus session: resolve -> focus -> verify -> restore.

        Yields the live target snapshot. Companion focus is preserved on
        entry and restored best-effort on exit. Any validation failure
        raises :class:`TargetValidationError` before the body runs.
        """
        self._poll()
        expected = self._expected(operation=operation)
        with self._actuator.focus_target(
            expected.handle,
            preserve=preserve,
            verify=verify,
            operation=operation,
        ):
            live = self._actuator.validate_target(
                expected,
                authorized=self._authorized,
                operation=operation,
            )
            yield live

    def focus_matches(self, expected: WindowInfo) -> bool:
        """Whether ``expected`` still owns the foreground (never raises).

        Best-effort read for the pause detector: any error, unreadable
        foreground, or handle mismatch reads as False. Authoritative
        validation still goes through :meth:`validate_target`.
        """
        try:
            current = self._actuator.window_backend.foreground()
        except Exception:
            return False
        return current is not None and current.handle == expected.handle

    def wait_for_focus(
        self,
        expected: WindowInfo,
        *,
        timeout_sec: Optional[float] = None,
        operation: str = "gateway.wait_for_focus",
    ) -> bool:
        """Wait until ``expected`` regains the foreground (or timeout).

        Returns True on regain, False on timeout. Interrupt / E-stop
        during the wait raises :class:`CobotInterruptedError`; the wait
        itself never injects anything.
        """
        _ = operation
        budget = (
            self._pause_timeout_sec if timeout_sec is None
            else _positive_seconds(timeout_sec, field="timeout_sec")
        )
        deadline = time.monotonic() + budget
        while True:
            self._poll()
            if self.focus_matches(expected):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(self._focus_poll_sec, remaining))

    def _guarded_validate(
        self, expected: WindowInfo, *, operation: str
    ) -> WindowInfo:
        """Validate ``expected``; pause through focus loss, never identity."""
        while True:
            try:
                return self._actuator.validate_target(
                    expected,
                    authorized=self._authorized,
                    operation=operation,
                )
            except TargetValidationError as exc:
                if exc.reason not in ("foreground_mismatch", "no_foreground"):
                    raise  # identity/authorization failure: fail closed now
                if not self._pause_on_focus_loss:
                    raise FocusLostError(
                        f"target lost foreground during {operation}: {exc}",
                        reason="focus_lost",
                    ) from exc
                if self._pause_count >= self._max_pauses:
                    raise FocusLostError(
                        f"target lost foreground during {operation}: pause "
                        f"budget exhausted ({self._max_pauses})",
                        reason="pause_budget_exhausted",
                    ) from exc
                self._pause_count += 1
                self._paused = True
                self._emit(
                    EventType.ACTUATION_CANCELLED,
                    {
                        "paused": True,
                        "operation": operation,
                        "reason": exc.reason,
                        "target_handle": expected.handle,
                    },
                )
                if self._on_pause is not None:
                    try:
                        self._on_pause(expected)
                    except Exception:
                        pass
                try:
                    regained = self.wait_for_focus(
                        expected, operation=operation
                    )
                finally:
                    self._paused = False
                if not regained:
                    raise FocusLostError(
                        f"target lost foreground during {operation}: no "
                        f"regain within {self._pause_timeout_sec}s",
                        reason="focus_timeout",
                    ) from exc
                self._emit(
                    EventType.ACTION_STARTED,
                    {
                        "resumed": True,
                        "operation": operation,
                        "target_handle": expected.handle,
                    },
                )
                if self._on_resume is not None:
                    try:
                        self._on_resume(expected)
                    except Exception:
                        pass
                # Loop: re-validate (process identity is re-checked on
                # the fresh snapshot, so a PID swap during the pause
                # still fails closed instead of resuming blindly).

    def run_steps(
        self,
        steps: Sequence[Callable[[WindowInfo], Any]],
        *,
        operation: str = "gateway.run",
    ) -> list[Any]:
        """Run guarded steps inside one validated focus session.

        Each step receives the live target snapshot. Focus is validated
        before every step and after every step (a mid-operation target
        change invalidates the outcome); focus loss pauses, identity
        change raises. Returns one result per step.
        """
        if not isinstance(steps, (list, tuple)) or not steps:
            raise ValueError("steps: expected a non-empty sequence")
        for step in steps:
            if not callable(step):
                raise ValueError("steps: expected callables only")
        self._poll()
        results: list[Any] = []
        with self.session(operation=operation) as live:
            expected = live
            for index, step in enumerate(steps):
                expected = self._guarded_validate(
                    expected, operation=f"{operation}.step{index}"
                )
                results.append(step(expected))
                self._poll()
                expected = self._guarded_validate(
                    expected, operation=f"{operation}.step{index}.post"
                )
        return results

    def move_cursor_smooth(
        self,
        target: CursorPoint,
        duration_sec: float = 0.6,
        steps: int = 30,
        *,
        verify: bool = False,
        tolerance: int = 2,
    ) -> None:
        """Gateway-guarded :meth:`DesktopActuator.move_cursor_smooth`."""
        self.run_steps(
            [
                lambda _live: self._actuator.move_cursor_smooth(
                    target,
                    duration_sec,
                    steps,
                    verify=verify,
                    tolerance=tolerance,
                )
            ],
            operation="gateway.move_cursor_smooth",
        )

    def type_natural(
        self,
        text: str,
        base_wpm: int = 70,
        *,
        verify_fn: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Gateway-guarded :meth:`DesktopActuator.type_natural`."""
        self.run_steps(
            [
                lambda _live: self._actuator.type_natural(
                    text, base_wpm, verify_fn=verify_fn
                )
            ],
            operation="gateway.type_natural",
        )

    def click_point(
        self,
        point: CursorPoint,
        *,
        verify: bool = False,
        tolerance: int = 2,
    ) -> None:
        """Gateway-guarded :meth:`DesktopActuator.click_point`."""
        self.run_steps(
            [
                lambda _live: self._actuator.click_point(
                    point, verify=verify, tolerance=tolerance
                )
            ],
            operation="gateway.click_point",
        )

    def execute_plan(
        self,
        plan: ExecutionPlan,
        *,
        verify_fn: Optional[Callable[[], bool]] = None,
    ) -> dict[str, Any]:
        """Gateway-guarded :meth:`DesktopActuator.execute_plan`.

        The plan runs against the gateway target (resolved once per
        run): the nested plan session re-validates the same handle, so
        a target swap between gateway validation and plan execution
        still fails closed.
        """
        if not isinstance(plan, ExecutionPlan):
            raise ValueError(
                f"plan: expected ExecutionPlan, got {type(plan).__name__}"
            )
        reports = self.run_steps(
            [
                lambda live: self._actuator.execute_plan(
                    plan,
                    target=live,
                    authorized=self._authorized,
                    verify_fn=verify_fn,
                )
            ],
            operation="gateway.execute_plan",
        )
        return reports[0]


# ---------------------------------------------------------------------------
# TripleFailsafe: physical + ESC latch + spoken stop, one shared E-stop
# ---------------------------------------------------------------------------


def _is_esc_key(key: Any) -> bool:
    """Whether a ``pynput`` key press is ESC (fake-friendly).

    Live path compares against ``pynput.keyboard.Key.esc``; the string
    forms ``"esc"``/``"Key.esc"`` exist so headless fakes can drive the
    latch without the native dependency.
    """
    if isinstance(key, str) and key.strip().lower() in ("esc", "key.esc"):
        return True
    try:
        from pynput.keyboard import Key
    except ImportError:
        return False
    try:
        return key == Key.esc
    except Exception:
        return False


class EscHotkeyLatch:
    """Global ESC hotkey listener (the dedicated ESC E-stop channel).

    Follows the :class:`FailsafeBackend` structural contract
    (``arm``/``disarm``) so it drops into :class:`TripleFailsafe` or
    tests interchangeably. ``listener_factory`` maps one
    ``on_press(key)`` callback to a listener with ``start()``/``stop()``;
    the default builds a ``pynput`` keyboard listener and raises
    :class:`ActuatorError` with an install hint when ``pynput`` is
    absent (the failsafe then degrades that channel to disarmed).
    """

    def __init__(
        self,
        listener_factory: Optional[Callable[[Callable[[Any], None]], Any]] = None,
    ) -> None:
        if listener_factory is not None and not callable(listener_factory):
            raise ValueError("listener_factory: expected a callable or None")
        self._factory = listener_factory or _default_esc_listener
        self._listener: Any = None
        self._tripped = False
        self._lock = threading.Lock()

    @property
    def tripped(self) -> bool:
        """True once ESC has been seen since the last :meth:`arm`."""
        with self._lock:
            return self._tripped

    def arm(self, on_trip: Callable[[], None]) -> None:
        """Start watching for ESC; call ``on_trip`` once on detect."""
        if not callable(on_trip):
            raise ValueError("on_trip: expected a callable")
        with self._lock:
            self._tripped = False

        def fire_once() -> None:
            with self._lock:
                if self._tripped:
                    return
                self._tripped = True
            on_trip()

        def on_press(key: Any) -> None:
            try:
                if _is_esc_key(key):
                    fire_once()
            except Exception:
                pass  # a bad key event must never break the listener

        listener = self._factory(on_press)
        try:
            listener.start()
        except Exception:
            raise
        self._listener = listener

    def disarm(self) -> None:
        """Stop watching (idempotent)."""
        listener, self._listener = self._listener, None
        if listener is None:
            return
        try:
            listener.stop()
        except Exception:
            pass


def _default_esc_listener(on_press: Callable[[Any], None]) -> Any:
    try:
        from pynput import keyboard
    except ImportError:
        raise ActuatorError(
            "pynput is required for the ESC hotkey latch (pip install pynput)"
        ) from None
    return keyboard.Listener(on_press=on_press)


@dataclass(frozen=True)
class EstopTrip:
    """One latched E-stop trip (channel + abort-to-latch latency)."""

    channel: str
    reason: str
    latency_ms: float
    monotonic: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "reason": self.reason,
            "latency_ms": self.latency_ms,
            "monotonic": self.monotonic,
        }


class TripleFailsafe:
    """Three independent E-stop channels over one shared safety latch.

    - ``physical``: human touch (mouse displacement / keys) via a
      :class:`FailsafeBackend` (default :class:`DefaultFailsafe`).
    - ``esc``: global ESC hotkey via :class:`EscHotkeyLatch`, independent
      of the physical backend so ESC still latches when the mouse
      tripwire backend is missing or disarmed.
    - ``spoken``: spoken stop via :meth:`notify_transcript` (free text
      matched against :data:`SPOKEN_STOP_PHRASES`) or
      :meth:`notify_stop_intent` (structured STOP from the voice
      pipeline, e.g. :class:`VoiceActionService`).

    Any channel trips the shared :class:`SafetyController` (when
    attached), aborts the attached actuator best-effort, records the
    abort-to-latch latency against :data:`ESTOP_LATENCY_BUDGET_MS`, and
    emits ``E_STOP_TRIGGERED``. :meth:`trip` never raises for downstream
    failures; resuming needs :meth:`reset` with a deliberate
    authorization *plus* :meth:`SafetyController.reset` when a
    controller is attached.
    """

    CHANNEL_PHYSICAL = "physical"
    CHANNEL_ESC = "esc"
    CHANNEL_SPOKEN = "spoken"

    CHANNELS = (CHANNEL_PHYSICAL, CHANNEL_ESC, CHANNEL_SPOKEN)

    def __init__(
        self,
        *,
        safety: Optional[SafetyController] = None,
        event_bus: Optional[EventBus] = None,
        actuator: Optional[DesktopActuator] = None,
        physical: Any = None,
        esc_listener: Any = None,
        on_trip: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        if safety is not None and not isinstance(safety, SafetyController):
            raise ValueError(
                "safety: expected SafetyController or None, "
                f"got {type(safety).__name__}"
            )
        if event_bus is not None and not isinstance(event_bus, EventBus):
            raise ValueError(
                "event_bus: expected EventBus or None, "
                f"got {type(event_bus).__name__}"
            )
        if actuator is not None and not isinstance(actuator, DesktopActuator):
            raise ValueError(
                "actuator: expected DesktopActuator or None, "
                f"got {type(actuator).__name__}"
            )
        if on_trip is not None and not callable(on_trip):
            raise ValueError("on_trip: expected a callable or None")
        self._safety = safety
        self._event_bus = event_bus
        self._actuator = actuator
        self._physical = physical if physical is not None else DefaultFailsafe()
        self._esc = esc_listener if esc_listener is not None else EscHotkeyLatch()
        self._on_trip = on_trip
        self._lock = threading.Lock()
        self._armed: dict[str, bool] = {
            self.CHANNEL_PHYSICAL: False,
            self.CHANNEL_ESC: False,
            self.CHANNEL_SPOKEN: True,  # software channel: always armed
        }
        self._trips: dict[str, EstopTrip] = {}
        self._last_trip: Optional[EstopTrip] = None
        self._arm_hardware()

    @property
    def safety(self) -> Optional[SafetyController]:
        """Attached safety controller, or None when standalone."""
        return self._safety

    @property
    def armed_channels(self) -> tuple[str, ...]:
        """Channels currently watching (spoken is always armed)."""
        with self._lock:
            return tuple(c for c in self.CHANNELS if self._armed.get(c))

    @property
    def latched(self) -> bool:
        """True once any channel has tripped since the last reset."""
        with self._lock:
            return bool(self._trips)

    @property
    def tripped_channels(self) -> tuple[str, ...]:
        """Channels that have tripped since the last reset."""
        with self._lock:
            return tuple(c for c in self.CHANNELS if c in self._trips)

    @property
    def last_trip(self) -> Optional[EstopTrip]:
        """Most recent trip, or None when nothing tripped yet."""
        with self._lock:
            return self._last_trip

    def trip_latency_ms(self, channel: str) -> Optional[float]:
        """Abort-to-latch latency for ``channel`` (None when untripped)."""
        with self._lock:
            trip = self._trips.get(channel)
            return trip.latency_ms if trip is not None else None

    def within_budget(self, channel: str) -> Optional[bool]:
        """Whether ``channel`` latched within the 50 ms budget (None untripped)."""
        latency = self.trip_latency_ms(channel)
        if latency is None:
            return None
        return latency <= ESTOP_LATENCY_BUDGET_MS

    def _arm_hardware(self) -> None:
        for channel, backend in (
            (self.CHANNEL_PHYSICAL, self._physical),
            (self.CHANNEL_ESC, self._esc),
        ):
            try:
                backend.arm(
                    lambda channel=channel: self.trip(channel, "")
                )
            except Exception:
                with self._lock:
                    self._armed[channel] = False
            else:
                with self._lock:
                    self._armed[channel] = True

    def trip(self, channel: str, reason: str = "") -> EstopTrip:
        """Latch the E-stop from ``channel`` (never raises downstream).

        Trips the shared safety controller, aborts the attached
        actuator, records latency, emits ``E_STOP_TRIGGERED``, and calls
        ``on_trip`` — every step best-effort after the latch itself.
        Unknown channels raise :class:`ValueError` (programmer error);
        everything else lands the trip.
        """
        if channel not in self.CHANNELS:
            raise ValueError(
                f"channel: expected one of {list(self.CHANNELS)}, "
                f"got {channel!r}"
            )
        started = time.monotonic()
        try:
            why = reason.strip() if isinstance(reason, str) else str(reason)
        except Exception:
            why = ""
        if self._safety is not None:
            try:
                self._safety.trip(source=f"estop-{channel}", reason=why)
            except Exception:
                pass  # SafetyController.trip never raises; stay safe anyway
        if self._actuator is not None:
            try:
                self._actuator.abort()
            except Exception:
                pass  # abort delivery must never break the trip itself
        latency_ms = (time.monotonic() - started) * 1000.0
        trip = EstopTrip(
            channel=channel,
            reason=why,
            latency_ms=latency_ms,
            monotonic=time.monotonic(),
        )
        with self._lock:
            if channel not in self._trips:
                self._trips[channel] = trip
            self._last_trip = trip
            armed = tuple(c for c in self.CHANNELS if self._armed.get(c))
        if self._event_bus is not None:
            try:
                self._event_bus.emit(
                    EventType.E_STOP_TRIGGERED,
                    _ESTOP_SOURCE,
                    {
                        "channel": channel,
                        "reason": why,
                        "latency_ms": latency_ms,
                        "within_budget_ms": latency_ms
                        <= ESTOP_LATENCY_BUDGET_MS,
                        "armed_channels": list(armed),
                    },
                )
            except Exception:
                pass
        if self._on_trip is not None:
            try:
                self._on_trip(channel, why)
            except Exception:
                pass
        return trip

    def notify_transcript(self, text: str) -> bool:
        """Trip the spoken channel when ``text`` carries a stop phrase.

        Returns True when the transcript tripped the E-stop. Never
        raises: unparseable input simply does not trip.
        """
        try:
            if not is_spoken_stop(text):
                return False
            snippet = text.strip()[:200] if isinstance(text, str) else ""
        except Exception:
            return False
        self.trip(self.CHANNEL_SPOKEN, snippet)
        return True

    def notify_stop_intent(self, reason: str = "") -> EstopTrip:
        """Trip the spoken channel from a structured STOP intent.

        Structured path for the voice pipeline (which already resolved
        the transcript to STOP): trips unconditionally.
        """
        return self.trip(self.CHANNEL_SPOKEN, reason)

    def reset(self, *, authorization: str, actor: str = "") -> bool:
        """Clear the local latch after deliberate reauthorization.

        ``authorization`` must be non-empty (operator identity + reason);
        blank reauth raises :class:`EmbodimentError` and changes nothing.
        Returns True when a latch was cleared. The shared safety
        controller is *not* reset here: resume needs its own deliberate
        :meth:`SafetyController.reset`.
        """
        _ = actor
        auth = authorization.strip() if isinstance(authorization, str) else ""
        if not auth:
            raise EmbodimentError(
                "reset requires deliberate reauthorization: pass a non-empty "
                "authorization (operator identity + reason)"
            )
        with self._lock:
            had = bool(self._trips)
            self._trips = {}
            self._last_trip = None
        return had

    def close(self) -> None:
        """Disarm hardware channels (idempotent; spoken stays available)."""
        for channel, backend in (
            (self.CHANNEL_PHYSICAL, self._physical),
            (self.CHANNEL_ESC, self._esc),
        ):
            try:
                backend.disarm()
            except Exception:
                pass
            with self._lock:
                self._armed[channel] = False

    def __enter__(self) -> "TripleFailsafe":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# OutcomeVerifier: tests + git status + file tree, halt + 1-click rollback
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileEntry:
    """One snapshotted file (relative path + identity)."""

    path: str
    sha256: str
    size: int
    mtime_ns: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
        }


@dataclass(frozen=True)
class FileSnapshot:
    """Baseline file-tree capture for change detection + rollback."""

    root: str
    files: tuple[FileEntry, ...]
    skipped: int
    taken_at: float

    def by_path(self) -> dict[str, FileEntry]:
        """Entries keyed by relative path."""
        return {entry.path: entry for entry in self.files}

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "files": [entry.to_dict() for entry in self.files],
            "skipped": self.skipped,
            "taken_at": self.taken_at,
        }


@dataclass(frozen=True)
class VerificationCheck:
    """One named check inside a :class:`VerificationReport`."""

    name: str
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class VerificationReport:
    """Outcome of :meth:`OutcomeVerifier.verify`."""

    ok: bool
    checks: tuple[VerificationCheck, ...]
    halted: bool
    rollback_available: bool

    def failed(self) -> tuple[VerificationCheck, ...]:
        """Checks that failed (empty when the outcome verified)."""
        return tuple(check for check in self.checks if not check.ok)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
            "halted": self.halted,
            "rollback_available": self.rollback_available,
        }


@dataclass(frozen=True)
class RollbackAction:
    """One planned/active rollback step (``restore`` or ``delete``)."""

    action: str
    path: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "path": self.path,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class RollbackReport:
    """Outcome of :meth:`OutcomeVerifier.rollback`."""

    ok: bool
    actions: tuple[RollbackAction, ...]
    restored: int
    deleted: int
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "actions": [action.to_dict() for action in self.actions],
            "restored": self.restored,
            "deleted": self.deleted,
            "errors": list(self.errors),
        }


def _default_run_command(
    command: Sequence[str],
    cwd: Optional[str],
    timeout_sec: float,
) -> tuple[int, str, str]:
    """Run ``command`` without a shell, capturing output (test seam)."""
    try:
        proc = subprocess.run(
            list(command),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise EmbodimentError(
            f"test command timed out after {timeout_sec}s: "
            f"{list(command)!r}"
        ) from exc
    return (proc.returncode, proc.stdout or "", proc.stderr or "")


class OutcomeVerifier:
    """Post-actuation verification with halt + 1-click rollback.

    Workflow: :meth:`snapshot` the tree (and git state) before acting,
    act through the :class:`ActuationGateway`, then :meth:`verify`:

    - ``tests``: run ``test_command`` (never a shell); pass iff exit 0.
    - ``git``: read live git status; pass iff readable (plus the
      ``forbid_dirty`` opt-in); records branch/HEAD/dirty paths only,
      never file content.
    - ``files``: re-hash the tree; pass iff every change is listed in
      ``expected_changes`` (anything else changed/added/deleted fails).

    Any failure halts the system (trips the shared safety controller
    and emits ``ACTION_FAILED``) and the baseline snapshot offers
    1-click recovery: :meth:`rollback_plan` previews, :meth:`rollback`
    restores. Rollback deliberately works while the E-stop is latched.
    """

    def __init__(
        self,
        root: Union[str, Path],
        *,
        safety: Optional[SafetyController] = None,
        event_bus: Optional[EventBus] = None,
        test_command: Optional[Sequence[str]] = None,
        allowed_roots: Optional[Sequence[Union[str, Path]]] = None,
        test_timeout_sec: float = 300.0,
        max_file_bytes: int = 1_000_000,
        max_snapshot_bytes: int = 50_000_000,
        run_command: Optional[
            Callable[
                [Sequence[str], Optional[str], float],
                tuple[int, str, str],
            ]
        ] = None,
        read_state: Optional[Callable[[str], Any]] = None,
    ) -> None:
        if isinstance(root, Path):
            root = str(root)
        root = check_str(root, field="root")
        resolved = Path(root).resolve()
        if not resolved.is_dir():
            raise ValueError(f"root: expected an existing directory: {root!r}")
        if safety is not None and not isinstance(safety, SafetyController):
            raise ValueError(
                "safety: expected SafetyController or None, "
                f"got {type(safety).__name__}"
            )
        if event_bus is not None and not isinstance(event_bus, EventBus):
            raise ValueError(
                "event_bus: expected EventBus or None, "
                f"got {type(event_bus).__name__}"
            )
        if test_command is not None:
            if (
                not isinstance(test_command, (list, tuple))
                or not test_command
                or any(
                    not isinstance(c, str) or not c for c in test_command
                )
            ):
                raise ValueError(
                    "test_command: expected a non-empty list of strings"
                )
            test_command = tuple(test_command)
        if run_command is not None and not callable(run_command):
            raise ValueError("run_command: expected a callable or None")
        if read_state is not None and not callable(read_state):
            raise ValueError("read_state: expected a callable or None")
        self._root = resolved
        self._safety = safety
        self._event_bus = event_bus
        self._test_command = test_command
        self._allowed_roots: tuple[Union[str, Path], ...] = (
            tuple(allowed_roots) if allowed_roots is not None else (root,)
        )
        self._test_timeout_sec = _positive_seconds(
            test_timeout_sec, field="test_timeout_sec"
        )
        self._max_file_bytes = check_int(
            max_file_bytes, field="max_file_bytes", minimum=1
        )
        self._max_snapshot_bytes = check_int(
            max_snapshot_bytes, field="max_snapshot_bytes", minimum=1
        )
        self._run_command = run_command or _default_run_command
        self._read_state = read_state
        self._snapshot: Optional[FileSnapshot] = None
        self._contents: dict[str, bytes] = {}
        self._snapshot_git: Any = None
        self._last_report: Optional[VerificationReport] = None
        self._halted = False
        self._lock = threading.Lock()

    @property
    def root(self) -> str:
        """Verified root directory under management."""
        return str(self._root)

    @property
    def baseline(self) -> Optional[FileSnapshot]:
        """Baseline snapshot, or None before :meth:`snapshot` runs."""
        with self._lock:
            return self._snapshot

    @property
    def last_report(self) -> Optional[VerificationReport]:
        """Most recent :meth:`verify` report, or None before any verify."""
        with self._lock:
            return self._last_report

    @property
    def halted(self) -> bool:
        """True once a failed verification halted the system."""
        with self._lock:
            return self._halted

    def _emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        if self._event_bus is None:
            return
        try:
            self._event_bus.emit(event_type, _VERIFY_SOURCE, payload)
        except Exception:
            pass  # telemetry must never break fail-closed verification

    def _contained(self, relpath: str) -> Optional[Path]:
        """Resolve ``relpath`` under the root (None on escape/absolute)."""
        try:
            candidate = (self._root / relpath).resolve()
        except Exception:
            return None
        try:
            if not candidate.is_relative_to(self._root):
                return None
        except Exception:
            return None
        return candidate

    def _walk(
        self, *, capture_contents: bool
    ) -> tuple[dict[str, FileEntry], dict[str, bytes], int]:
        """Hash the tree deterministically (sorted, no symlinks followed)."""
        entries: dict[str, FileEntry] = {}
        contents: dict[str, bytes] = {}
        skipped = 0
        total = 0
        stack: list[Path] = [self._root]
        while stack:
            directory = stack.pop()
            try:
                children = sorted(
                    directory.iterdir(), key=lambda p: p.name
                )
            except OSError:
                skipped += 1
                continue
            for child in children:
                try:
                    if child.is_symlink():
                        skipped += 1
                        continue
                except OSError:
                    skipped += 1
                    continue
                if should_exclude(child):
                    skipped += 1
                    continue
                if child.is_dir():
                    stack.append(child)
                    continue
                if not child.is_file():
                    skipped += 1
                    continue
                try:
                    rel = child.relative_to(self._root).as_posix()
                    stat = child.stat()
                    data = child.read_bytes()
                except OSError:
                    skipped += 1
                    continue
                if len(data) > self._max_file_bytes:
                    entries[rel] = FileEntry(
                        path=rel,
                        sha256="",
                        size=len(data),
                        mtime_ns=stat.st_mtime_ns,
                    )
                    skipped += 1
                    continue
                digest = hashlib.sha256(data).hexdigest()
                entries[rel] = FileEntry(
                    path=rel,
                    sha256=digest,
                    size=len(data),
                    mtime_ns=stat.st_mtime_ns,
                )
                if capture_contents:
                    total += len(data)
                    if total > self._max_snapshot_bytes:
                        raise EmbodimentError(
                            "snapshot exceeds max_snapshot_bytes "
                            f"({self._max_snapshot_bytes}): refusing a "
                            "baseline that cannot offer rollback"
                        )
                    contents[rel] = data
        return entries, contents, skipped

    def snapshot(self) -> FileSnapshot:
        """Capture the baseline tree (+ git state) before acting.

        Raises :class:`EmbodimentError` when the tree cannot offer a
        rollback baseline (snapshot budget exceeded). Git capture is
        best-effort context only; authoritative git checks run in
        :meth:`verify`.
        """
        entries, contents, skipped = self._walk(capture_contents=True)
        taken = FileSnapshot(
            root=str(self._root),
            files=tuple(entries[key] for key in sorted(entries)),
            skipped=skipped,
            taken_at=time.time(),
        )
        git_state: Any = None
        try:
            if self._read_state is not None:
                git_state = self._read_state(str(self._root))
            else:
                from .telemetry import read_git_state

                git_state = read_git_state(
                    str(self._root), allowed_roots=self._allowed_roots
                )
        except Exception:
            git_state = None  # baseline git is context, not a gate
        with self._lock:
            self._snapshot = taken
            self._contents = contents
            self._snapshot_git = git_state
            self._halted = False
            self._last_report = None
        return taken

    def _check_tests(self) -> VerificationCheck:
        if self._test_command is None:
            return VerificationCheck(
                name="tests",
                ok=True,
                detail="no test command configured (skipped)",
            )
        try:
            code, out, err = self._run_command(
                self._test_command, str(self._root), self._test_timeout_sec
            )
        except EmbodimentError as exc:
            return VerificationCheck(
                name="tests", ok=False, detail=str(exc)
            )
        except Exception as exc:
            return VerificationCheck(
                name="tests",
                ok=False,
                detail=f"test runner failed: {exc}",
            )
        if code != 0:
            tail = (err or out or "").strip().splitlines()
            hint = tail[-1][:200] if tail else f"exit {code}"
            return VerificationCheck(
                name="tests",
                ok=False,
                detail=f"test command exited {code}: {hint}",
            )
        return VerificationCheck(
            name="tests", ok=True, detail="test command exited 0"
        )

    def _check_git(self, *, forbid_dirty: bool) -> VerificationCheck:
        try:
            if self._read_state is not None:
                state = self._read_state(str(self._root))
            else:
                from .telemetry import read_git_state

                state = read_git_state(
                    str(self._root), allowed_roots=self._allowed_roots
                )
        except Exception as exc:
            return VerificationCheck(
                name="git",
                ok=False,
                detail=f"git status unreadable: {exc}",
            )
        try:
            branch = getattr(state, "branch", "")
            head = getattr(state, "head", "")
            dirty = bool(getattr(state, "dirty", False))
            staged = int(getattr(state, "staged_count", 0) or 0)
            unstaged = int(getattr(state, "unstaged_count", 0) or 0)
            untracked = int(getattr(state, "untracked_count", 0) or 0)
        except Exception as exc:
            return VerificationCheck(
                name="git",
                ok=False,
                detail=f"git state malformed: {exc}",
            )
        head_short = head[:12] if isinstance(head, str) else ""
        detail = (
            f"branch={branch or '?'} head={head_short or '?'} "
            f"dirty={dirty} staged={staged} "
            f"unstaged={unstaged} untracked={untracked}"
        )
        if forbid_dirty and dirty:
            return VerificationCheck(
                name="git",
                ok=False,
                detail=f"working tree is dirty (forbid_dirty): {detail}",
            )
        return VerificationCheck(name="git", ok=True, detail=detail)

    def _check_files(
        self, baseline: FileSnapshot, *, expected_changes: frozenset[str]
    ) -> VerificationCheck:
        try:
            current, _, _ = self._walk(capture_contents=False)
        except Exception as exc:
            return VerificationCheck(
                name="files",
                ok=False,
                detail=f"file-tree re-read failed: {exc}",
            )
        before = baseline.by_path()
        changed: list[str] = []
        for rel, entry in current.items():
            old = before.get(rel)
            if old is None:
                changed.append(f"{rel} (added)")
            elif old.sha256 != entry.sha256 or old.size != entry.size:
                if not old.sha256 and not entry.sha256:
                    if old.size == entry.size:
                        continue
                changed.append(f"{rel} (modified)")
        for rel in before:
            if rel not in current:
                changed.append(f"{rel} (deleted)")
        unexpected = [
            item for item in changed
            if item.split(" (", 1)[0] not in expected_changes
        ]
        if unexpected:
            shown = ", ".join(sorted(unexpected)[:10])
            more = (
                f" (+{len(unexpected) - 10} more)"
                if len(unexpected) > 10
                else ""
            )
            return VerificationCheck(
                name="files",
                ok=False,
                detail=f"unexpected file-tree changes: {shown}{more}",
            )
        if changed:
            return VerificationCheck(
                name="files",
                ok=True,
                detail=f"{len(changed)} expected change(s), nothing else",
            )
        return VerificationCheck(
            name="files", ok=True, detail="file tree unchanged"
        )

    def halt(
        self, reason: str, *, operation: str = "outcome.halt"
    ) -> Optional[SafetyRecord]:
        """Halt the system: latch the E-stop + emit ``ACTION_FAILED``.

        Never raises. Returns the safety record, or None when no
        safety controller is attached.
        """
        why = reason.strip()[:500] if isinstance(reason, str) else ""
        record: Optional[SafetyRecord] = None
        if self._safety is not None:
            try:
                record = self._safety.trip(
                    source="outcome-verifier", reason=why
                )
            except Exception:
                record = None
        with self._lock:
            self._halted = True
        self._emit(
            EventType.ACTION_FAILED,
            {"operation": operation, "reason": why or "outcome failed"},
        )
        return record

    def verify(
        self,
        *,
        run_tests: bool = True,
        check_git: bool = True,
        check_files: bool = True,
        expected_changes: Sequence[str] = (),
        forbid_dirty: bool = False,
        operation: str = "outcome.verify",
    ) -> VerificationReport:
        """Verify the post-actuation outcome; halt + arm rollback on fail.

        ``expected_changes`` lists relative paths the actuation intended
        to modify (everything else must be untouched). ``forbid_dirty``
        additionally fails when the git working tree is dirty. Any
        failure trips the shared safety controller and emits
        ``ACTION_FAILED``; success is silent (the report is the record).
        """
        run_tests = check_bool(run_tests, field="run_tests")
        check_git = check_bool(check_git, field="check_git")
        check_files = check_bool(check_files, field="check_files")
        forbid_dirty = check_bool(forbid_dirty, field="forbid_dirty")
        if not isinstance(expected_changes, (list, tuple)):
            raise ValueError("expected_changes: expected a list of strings")
        expected = frozenset(
            check_str(item, field="expected_changes[]")
            for item in expected_changes
        )
        with self._lock:
            baseline = self._snapshot
        checks: list[VerificationCheck] = []
        if run_tests:
            checks.append(self._check_tests())
        if check_git:
            checks.append(self._check_git(forbid_dirty=forbid_dirty))
        if check_files:
            if baseline is None:
                checks.append(
                    VerificationCheck(
                        name="files",
                        ok=False,
                        detail="no baseline snapshot (call snapshot() first)",
                    )
                )
            else:
                checks.append(
                    self._check_files(baseline, expected_changes=expected)
                )
        ok = all(check.ok for check in checks)
        report = VerificationReport(
            ok=ok,
            checks=tuple(checks),
            halted=(not ok),
            rollback_available=(baseline is not None),
        )
        with self._lock:
            self._last_report = report
        if not ok:
            failed = "; ".join(
                f"{check.name}: {check.detail}" for check in report.failed()
            )
            self.halt(failed or "outcome verification failed",
                      operation=operation)
        return report

    def rollback_plan(self) -> tuple[RollbackAction, ...]:
        """Preview the 1-click rollback (no disk writes).

        Raises :class:`EmbodimentError` when no baseline snapshot exists.
        """
        with self._lock:
            baseline = self._snapshot
        if baseline is None:
            raise EmbodimentError(
                "rollback unavailable: no baseline snapshot "
                "(call snapshot() first)"
            )
        current, _, _ = self._walk(capture_contents=False)
        before = baseline.by_path()
        actions: list[RollbackAction] = []
        for rel in sorted(set(before) | set(current)):
            old = before.get(rel)
            new = current.get(rel)
            if old is not None and new is None:
                actions.append(
                    RollbackAction(
                        action="restore",
                        path=rel,
                        detail="recreate deleted file from baseline",
                    )
                )
            elif old is None and new is not None:
                actions.append(
                    RollbackAction(
                        action="delete",
                        path=rel,
                        detail="remove file added after baseline",
                    )
                )
            elif (
                old is not None
                and new is not None
                and (old.sha256 != new.sha256 or old.size != new.size)
            ):
                if not old.sha256 and not new.sha256 and (
                    old.size == new.size
                ):
                    continue  # oversize-but-same-size: nothing stored
                if not old.sha256:
                    actions.append(
                        RollbackAction(
                            action="restore",
                            path=rel,
                            detail="oversize baseline content not stored; "
                            "manual review required",
                        )
                    )
                else:
                    actions.append(
                        RollbackAction(
                            action="restore",
                            path=rel,
                            detail="restore modified file from baseline",
                        )
                    )
        return tuple(actions)

    def rollback(
        self, *, operation: str = "outcome.rollback"
    ) -> RollbackReport:
        """Execute the 1-click rollback from the baseline snapshot.

        Restores modified/deleted files and removes added ones, all
        contained under the root (escapes are refused per-file and
        reported as errors). Works while the E-stop is latched and
        never resets the safety latch: recovery first, deliberate
        reauth after. Raises :class:`EmbodimentError` without a
        baseline snapshot.
        """
        with self._lock:
            baseline = self._snapshot
            contents = dict(self._contents)
        if baseline is None:
            raise EmbodimentError(
                "rollback unavailable: no baseline snapshot "
                "(call snapshot() first)"
            )
        plan = self.rollback_plan()
        errors: list[str] = []
        restored = 0
        deleted = 0
        for step in plan:
            target = self._contained(step.path)
            if target is None:
                errors.append(f"{step.path}: escapes the managed root")
                continue
            try:
                if target.is_symlink():
                    errors.append(f"{step.path}: is a symlink; refusing")
                    continue
            except OSError as exc:
                errors.append(f"{step.path}: unreadable: {exc}")
                continue
            if step.action == "delete":
                try:
                    if target.is_file():
                        target.unlink()
                        deleted += 1
                    elif target.exists():
                        errors.append(
                            f"{step.path}: not a file; refusing to delete"
                        )
                except OSError as exc:
                    errors.append(f"{step.path}: delete failed: {exc}")
            else:
                data = contents.get(step.path)
                if data is None:
                    errors.append(
                        f"{step.path}: baseline content not stored "
                        "(oversize); manual review required"
                    )
                    continue
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    restored += 1
                except OSError as exc:
                    errors.append(f"{step.path}: restore failed: {exc}")
        report = RollbackReport(
            ok=not errors,
            actions=plan,
            restored=restored,
            deleted=deleted,
            errors=tuple(errors),
        )
        self._emit(
            EventType.ACTION_COMPLETED,
            {
                "operation": operation,
                "rollback": True,
                "ok": report.ok,
                "restored": restored,
                "deleted": deleted,
                "errors": list(report.errors),
            },
        )
        return report



