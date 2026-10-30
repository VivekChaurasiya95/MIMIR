"""Dedicated cursor-trajectory executor (plan section 7).

The cubic Bezier math stays in :func:`mimir.actuator.plan_trajectory`;
this module owns *execution*: monotonic scheduling, cancellation,
screen-bounds validation, stale-sample skipping (never a burst of old
mouse events), and deadline metrics.

The executor is thread-agnostic and Qt-free: hosts run
:meth:`TrajectoryExecutor.execute` on an actuation worker thread (see
:meth:`DesktopActuator.execute_trajectory`), never on the Qt main
thread, so inference stalls cannot distort cursor timing.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

__all__ = [
    "ScreenBounds",
    "TrajectoryCancelled",
    "TrajectoryError",
    "TrajectoryBoundsError",
    "TrajectoryExecutor",
    "TrajectoryReport",
]


class TrajectoryError(Exception):
    """Base error for trajectory execution failures."""


class TrajectoryBoundsError(TrajectoryError):
    """Raised fail-closed when a trajectory leaves the screen bounds.

    Raised before any motion happens: no partial glide is ever issued
    for an out-of-bounds plan.
    """


class TrajectoryCancelled(TrajectoryError):
    """Raised when a cancel/interrupt event fires mid-trajectory."""


@dataclass(frozen=True)
class ScreenBounds:
    """Valid cursor area: ``0 <= x < width``, ``0 <= y < height``."""

    width: int
    height: int

    def __post_init__(self) -> None:
        for name in ("width", "height"):
            value = getattr(self, name)
            if type(value) is bool or not isinstance(value, int) \
                    or value < 1:
                raise ValueError(
                    f"{name}: expected a positive pixel count"
                )

    def contains(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height


@dataclass(frozen=True)
class TrajectoryReport:
    """Deadline metrics for one executed trajectory (plan section 12).

    Deviations are measured against the monotonic schedule: positive
    means the sample shipped late. Skipped samples were stale past the
    deadline tolerance and deliberately dropped instead of burst.
    """

    points_commanded: int
    points_skipped: int
    missed_deadlines: int
    max_lateness_ms: float
    mean_abs_deviation_ms: float
    p50_lateness_ms: float
    p95_lateness_ms: float
    elapsed_ms: float
    completed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "points_commanded": self.points_commanded,
            "points_skipped": self.points_skipped,
            "missed_deadlines": self.missed_deadlines,
            "max_lateness_ms": self.max_lateness_ms,
            "mean_abs_deviation_ms": self.mean_abs_deviation_ms,
            "p50_lateness_ms": self.p50_lateness_ms,
            "p95_lateness_ms": self.p95_lateness_ms,
            "elapsed_ms": self.elapsed_ms,
            "completed": self.completed,
        }


def _point_xy(point: Any, index: int) -> tuple[int, int]:
    if hasattr(point, "x") and hasattr(point, "y"):
        x, y = point.x, point.y
    elif isinstance(point, (tuple, list)) and len(point) == 2:
        x, y = point
    else:
        raise ValueError(
            f"points[{index}]: expected CursorPoint or (x, y) pair"
        )
    if type(x) is bool or type(y) is bool \
            or not isinstance(x, int) or not isinstance(y, int):
        raise ValueError(
            f"points[{index}]: expected integer pixel coordinates"
        )
    return (x, y)


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 3)
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    low = int(rank)
    high = min(len(sorted_vals) - 1, low + 1)
    frac = rank - low
    return round(
        sorted_vals[low] * (1.0 - frac) + sorted_vals[high] * frac, 3
    )


class TrajectoryExecutor:
    """Executes precomputed Bezier trajectories on worker threads.

    :param move_to: ``(x, y) -> None`` input backend (mouse ``move_to``).
    :param note_commanded: optional failsafe self-motion suppression
        ``(x, y) -> None`` called before each commanded sample.
    :param clock: monotonic clock (defaults to :func:`time.monotonic`;
        injectable for deterministic tests).
    :param sleeper: ``(seconds, waiter) -> wake_by_event`` interruptible
        sleep (defaults to event-wait/``time.sleep``; tests driving a
        fake clock inject one that advances the fake).
    """

    def __init__(
        self,
        move_to: Callable[[int, int], None],
        *,
        note_commanded: Optional[Callable[[int, int], None]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Optional[Callable[[float, Any], bool]] = None,
    ) -> None:
        if not callable(move_to):
            raise ValueError("move_to: expected a callable")
        if note_commanded is not None and not callable(note_commanded):
            raise ValueError("note_commanded: expected a callable or None")
        if sleeper is not None and not callable(sleeper):
            raise ValueError("sleeper: expected a callable or None")
        self._move_to = move_to
        self._note = note_commanded
        self._clock = clock
        self._sleeper = sleeper

    def execute(
        self,
        points: Sequence[Any],
        duration_sec: float,
        *,
        bounds: Optional[Any] = None,
        cancel_event: Optional[threading.Event] = None,
        interrupt_event: Optional[threading.Event] = None,
        deadline_tolerance_ms: float = 50.0,
        sleep_slice_ms: float = 2.0,
    ) -> TrajectoryReport:
        """Command ``points`` over ``duration_sec`` (blocking).

        Scheduling uses only the monotonic clock. Samples that fall
        more than ``deadline_tolerance_ms`` behind are skipped (the
        cursor jumps to the trajectory's *current* position) instead
        of being replayed as a stale burst. Sleeps are interruptible:
        ``cancel_event``/``interrupt_event`` abort within one sleep
        slice via :class:`TrajectoryCancelled`.
        """
        planned = self._validate(points, duration_sec, bounds,
                                 deadline_tolerance_ms, sleep_slice_ms)
        resolved = self._resolve_bounds(bounds)
        for index, (x, y) in enumerate(planned):
            if resolved is not None and not resolved.contains(x, y):
                raise TrajectoryBoundsError(
                    f"points[{index}] ({x}, {y}) leaves screen bounds "
                    f"{resolved.width}x{resolved.height}: trajectory "
                    "rejected before any motion"
                )
        self._raise_if_set(cancel_event, interrupt_event)
        count = len(planned)
        per_step = duration_sec / (count - 1) if count > 1 else 0.0
        start = self._clock()
        deviations: list[float] = []  # signed ms per commanded sample
        commanded = 0
        skipped = 0
        for index, (x, y) in enumerate(planned):
            due = start + index * per_step
            now = self._clock()
            lateness_ms = (now - due) * 1000.0
            if lateness_ms > deadline_tolerance_ms:
                # Stale: drop it and re-sync to the schedule instead of
                # bursting old samples to "catch up".
                skipped += 1
                continue
            if lateness_ms < 0:
                self._sleep_until(due, cancel_event, interrupt_event,
                                  sleep_slice_ms)
            self._raise_if_set(cancel_event, interrupt_event)
            if self._note is not None:
                try:
                    self._note(x, y)
                except Exception:
                    pass  # suppression hints must never break motion
            self._move_to(x, y)
            commanded += 1
            deviations.append((self._clock() - due) * 1000.0)
        elapsed_ms = (self._clock() - start) * 1000.0
        latenesses = sorted(max(0.0, dev) for dev in deviations)
        missed = sum(
            1 for dev in latenesses if dev > deadline_tolerance_ms
        ) + skipped
        mean_abs = (
            round(sum(abs(dev) for dev in deviations) / len(deviations), 3)
            if deviations else 0.0
        )
        return TrajectoryReport(
            points_commanded=commanded,
            points_skipped=skipped,
            missed_deadlines=missed,
            max_lateness_ms=round(max(latenesses) if latenesses else 0.0, 3),
            mean_abs_deviation_ms=mean_abs,
            p50_lateness_ms=_percentile(latenesses, 50),
            p95_lateness_ms=_percentile(latenesses, 95),
            elapsed_ms=round(elapsed_ms, 3),
            completed=True,
        )

    def _validate(
        self,
        points: Sequence[Any],
        duration_sec: float,
        bounds: Optional[Any],
        deadline_tolerance_ms: float,
        sleep_slice_ms: float,
    ) -> list[tuple[int, int]]:
        if not isinstance(points, (list, tuple)) or not points:
            raise ValueError("points: expected a non-empty sequence")
        if (
            type(duration_sec) is bool
            or not isinstance(duration_sec, (int, float))
            or not (duration_sec == duration_sec)
            or duration_sec <= 0
        ):
            raise ValueError(
                "duration_sec: expected a positive number of seconds"
            )
        for name, value in (
            ("deadline_tolerance_ms", deadline_tolerance_ms),
            ("sleep_slice_ms", sleep_slice_ms),
        ):
            if (
                type(value) is bool
                or not isinstance(value, (int, float))
                or not (value == value)
                or value <= 0
            ):
                raise ValueError(
                    f"{name}: expected a positive number of milliseconds"
                )
        if bounds is not None and not isinstance(
            bounds, (ScreenBounds, tuple, list)
        ):
            raise ValueError(
                "bounds: expected ScreenBounds, (width, height), or None"
            )
        return [
            _point_xy(point, index) for index, point in enumerate(points)
        ]

    @staticmethod
    def _resolve_bounds(bounds: Optional[Any]) -> Optional[ScreenBounds]:
        if bounds is None:
            return None
        if isinstance(bounds, ScreenBounds):
            return bounds
        pair = tuple(bounds)
        if len(pair) != 2:
            raise ValueError("bounds: expected (width, height)")
        return ScreenBounds(width=int(pair[0]), height=int(pair[1]))

    @staticmethod
    def _raise_if_set(
        cancel_event: Optional[threading.Event],
        interrupt_event: Optional[threading.Event],
    ) -> None:
        for event in (cancel_event, interrupt_event):
            if event is not None and event.is_set():
                raise TrajectoryCancelled(
                    "trajectory cancelled: cobot motion interrupted"
                )

    def _sleep_until(
        self,
        due: float,
        cancel_event: Optional[threading.Event],
        interrupt_event: Optional[threading.Event],
        sleep_slice_ms: float,
    ) -> None:
        slice_s = sleep_slice_ms / 1000.0
        waiter = cancel_event if cancel_event is not None \
            else interrupt_event
        while True:
            self._raise_if_set(cancel_event, interrupt_event)
            remaining = due - self._clock()
            if remaining <= 0:
                return
            chunk = min(remaining, slice_s)
            if self._sleeper is not None:
                if self._sleeper(chunk, waiter):
                    self._raise_if_set(cancel_event, interrupt_event)
                    return
            elif waiter is not None and hasattr(waiter, "wait"):
                if waiter.wait(chunk):
                    self._raise_if_set(cancel_event, interrupt_event)
                    return
            else:
                time.sleep(chunk)
