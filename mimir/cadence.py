"""Consent-based interaction-cadence telemetry (plan section 3.1D).

Aggregates broad interaction patterns — typing intervals, editing
pauses, correction frequency, interaction bursts, idle periods — as
timing statistics. By construction there is no API that accepts raw
keystrokes, passwords, clipboard contents, or per-key timing: hosts
report only coarse ``active`` ticks plus a correction *count*.

The observer is disabled by default; :meth:`CadenceObserver.enable`
records explicit consent, and :meth:`disable` discards all buffered
samples. Ticks received while disabled are ignored, never retained.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from .events import Event, EventBus, EventType, get_event_bus

__all__ = ["CadenceObserver", "CadenceSnapshot"]

_DEFAULT_WINDOW_S = 60.0
_DEFAULT_IDLE_THRESHOLD_S = 5.0
_DEFAULT_BURST_GAP_S = 2.0


@dataclass(frozen=True)
class CadenceSnapshot:
    """Aggregate timing statistics over the observation window."""

    window_s: float
    active_s: float = 0.0
    idle_s: float = 0.0
    bursts: int = 0
    pauses: int = 0
    longest_pause_s: float = 0.0
    corrections: int = 0
    corrections_per_min: float = 0.0
    samples: int = 0
    observed_at: float = field(default_factory=time.time)


@dataclass
class _Sample:
    at: float
    active: bool
    corrections: int


class CadenceObserver:
    """Aggregate-only cadence tracker with a consent gate."""

    def __init__(
        self,
        *,
        bus: Optional[EventBus] = None,
        session_id: str = "",
        window_s: float = _DEFAULT_WINDOW_S,
        idle_threshold_s: float = _DEFAULT_IDLE_THRESHOLD_S,
        burst_gap_s: float = _DEFAULT_BURST_GAP_S,
        emit_interval_s: float = 10.0,
    ) -> None:
        for name, value in (
            ("window_s", window_s),
            ("idle_threshold_s", idle_threshold_s),
            ("burst_gap_s", burst_gap_s),
            ("emit_interval_s", emit_interval_s),
        ):
            if not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(
                    f"{name}: expected positive seconds, got {value!r}"
                )
        self.bus = bus
        self.session_id = session_id
        self.window_s = float(window_s)
        self.idle_threshold_s = float(idle_threshold_s)
        self.burst_gap_s = float(burst_gap_s)
        self.emit_interval_s = float(emit_interval_s)
        self._enabled = False
        self._samples: list[_Sample] = []
        self._last_emit_at: Optional[float] = None
        self._lock = threading.Lock()

    # -- consent gate ----------------------------------------------------
    @property
    def is_enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def enable(self) -> None:
        """Record consent and start retaining aggregate samples."""
        with self._lock:
            self._enabled = True

    def disable(self) -> None:
        """Withdraw consent; buffered samples are discarded."""
        with self._lock:
            self._enabled = False
            self._samples.clear()
            self._last_emit_at = None

    # -- coarse input (counts and activity flags only) --------------------
    def tick(
        self, active: bool, corrections: int = 0,
        now: Optional[float] = None,
    ) -> None:
        """Record one coarse activity tick.

        :param active: whether the developer was interacting during
            this tick (host-defined, e.g. any input in the last second).
        :param corrections: how many corrections (e.g. backspace
            bursts) occurred — a count only, never which keys.
        """
        if type(corrections) is not int or corrections < 0:
            raise ValueError(
                f"corrections: expected non-negative int count, "
                f"got {corrections!r}"
            )
        moment = time.time() if now is None else now
        with self._lock:
            if not self._enabled:
                return
            self._samples.append(_Sample(moment, bool(active), corrections))
            cutoff = moment - self.window_s
            self._samples = [s for s in self._samples if s.at >= cutoff]

    # -- aggregates ---------------------------------------------------------
    def snapshot(self, now: Optional[float] = None) -> CadenceSnapshot:
        """Compute window statistics (zeros when disabled or empty)."""
        moment = time.time() if now is None else now
        with self._lock:
            samples = list(self._samples) if self._enabled else []
        if not samples:
            return CadenceSnapshot(window_s=self.window_s,
                                   observed_at=moment)
        samples.sort(key=lambda s: s.at)
        active_s = 0.0
        idle_s = 0.0
        longest_pause = 0.0
        pauses = 0
        bursts = 0
        in_burst = False
        idle_run = 0.0
        for previous, current in zip(samples, samples[1:]):
            span = max(0.0, current.at - previous.at)
            if previous.active:
                active_s += span
                if not in_burst:
                    bursts += 1
                    in_burst = True
                idle_run = 0.0
            else:
                idle_s += span
                idle_run += span
                if idle_run >= self.idle_threshold_s and in_burst:
                    in_burst = False
                if span >= self.idle_threshold_s:
                    pauses += 1
                    longest_pause = max(longest_pause, span)
        if samples[-1].active and not in_burst:
            bursts += 1
        corrections = sum(s.corrections for s in samples)
        span_total = max(0.0, samples[-1].at - samples[0].at)
        per_min = corrections / (span_total / 60.0) if span_total > 0 else 0.0
        return CadenceSnapshot(
            window_s=self.window_s,
            active_s=active_s,
            idle_s=idle_s,
            bursts=bursts,
            pauses=pauses,
            longest_pause_s=longest_pause,
            corrections=corrections,
            corrections_per_min=per_min,
            samples=len(samples),
            observed_at=moment,
        )

    def maybe_emit(self, now: Optional[float] = None) -> Optional[CadenceSnapshot]:
        """Emit ``CADENCE_UPDATED`` when due; None when silent.

        Emits at most once per ``emit_interval_s`` and never while
        disabled. Hosts call this from their own tick loop.
        """
        moment = time.time() if now is None else now
        with self._lock:
            if not self._enabled:
                return None
            if (self._last_emit_at is not None
                    and moment - self._last_emit_at < self.emit_interval_s):
                return None
            self._last_emit_at = moment
        snapshot = self.snapshot(moment)
        self._publish(snapshot)
        return snapshot

    def _publish(self, snapshot: CadenceSnapshot) -> Optional[Event]:
        bus = self.bus if self.bus is not None else get_event_bus()
        try:
            return bus.emit(
                EventType.CADENCE_UPDATED,
                "sensory.cadence_observer",
                {
                    "window_s": snapshot.window_s,
                    "active_s": snapshot.active_s,
                    "idle_s": snapshot.idle_s,
                    "bursts": snapshot.bursts,
                    "pauses": snapshot.pauses,
                    "longest_pause_s": snapshot.longest_pause_s,
                    "corrections": snapshot.corrections,
                    "corrections_per_min": (
                        snapshot.corrections_per_min
                    ),
                    "observed_at": snapshot.observed_at,
                },
                session_id=self.session_id,
            )
        except Exception:
            return None
