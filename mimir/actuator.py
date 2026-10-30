"""Physical compliant desktop actuator: hands for the digital cobot.

Day 7. Kinematically smooth cursor motion (cubic Bezier ease-in-out),
organic typing cadence with syntactic micro-pauses, and backdrivable
compliance: any human touch (failsafe listener) or :meth:`DesktopActuator.abort`
trips an interrupt event that halts motion, typing, and subprocess
execution within milliseconds via interruptible waits.

Hardware sits behind injectable backends (mouse, keyboard, failsafe) so
the full stack runs headless in tests. Default backends lazily import
``pyautogui``/``pynput`` and raise :class:`ActuatorError` with install
hints when the package is absent; a missing failsafe backend degrades to
disarmed mode (``abort()`` stays available) instead of failing
construction.
"""

from __future__ import annotations

import hashlib
import math
import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol, Sequence

from ._common import check_bool, check_int, check_mapping, check_str
from .events import EventBus, EventType
from .safety import SafetyController, SafetyTrippedError

if TYPE_CHECKING:
    from .desktop import FocusGuard, TargetSpec, WindowBackend, WindowInfo

__all__ = [
    "ActuatorError",
    "CobotInterruptedError",
    "CursorPoint",
    "DefaultFailsafe",
    "DefaultKeyboard",
    "DefaultMouse",
    "DesktopActuator",
    "ExecutionPlan",
    "FailsafeBackend",
    "KeyboardBackend",
    "MouseBackend",
    "TargetValidationError",
    "plan_keystroke_delays",
    "plan_trajectory",
    "split_command_line",
]

_TRIP_MESSAGE = "human override: cobot motion interrupted"
_COMMAND_POLL_SEC = 0.05
_MOUSE_JITTER_PX = 5
# Slice for interruptible waits while a safety controller is attached: a
# latched E-stop is noticed within one slice even inside a long pause.
_SAFETY_WAIT_SLICE_SEC = 0.01


class ActuatorError(Exception):
    """Base error for physical actuation failures."""


class CobotInterruptedError(ActuatorError):
    """Raised immediately when the human touches the mouse or hits ESC."""


class TargetValidationError(ActuatorError):
    """Raised fail-closed when a desktop target cannot be verified.

    Carries a machine-readable ``reason`` (``unauthorized``,
    ``unknown_handle``, ``no_match``, ``ambiguous``, ``spec_mismatch``,
    ``target_changed``, ``no_foreground``, ``foreground_mismatch``,
    ``activation_denied``, ``backend_error``) alongside the message.
    """

    def __init__(self, message: str, *, reason: str = "validation_failed"):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class CursorPoint:
    """A pixel position on screen (origin at top-left)."""

    x: int
    y: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", check_int(self.x, field="x", minimum=0))
        object.__setattr__(self, "y", check_int(self.y, field="y", minimum=0))

    @classmethod
    def from_dict(cls, data: Any) -> "CursorPoint":
        check_mapping(data, cls="CursorPoint", required=("x", "y"))
        return cls(x=data["x"], y=data["y"])

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y}


@dataclass(frozen=True)
class ExecutionPlan:
    """A series of atomic action labels with an optional terminal command."""

    actions: tuple[str, ...]
    target_window: Optional[str] = None
    terminal_command: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.actions, (list, tuple)) or any(
            not isinstance(a, str) or not a.strip() for a in self.actions
        ):
            raise ValueError("actions: expected a list of non-empty strings")
        object.__setattr__(self, "actions", tuple(self.actions))
        if self.target_window is not None:
            object.__setattr__(
                self,
                "target_window",
                check_str(self.target_window, field="target_window"),
            )
        if self.terminal_command is not None:
            object.__setattr__(
                self,
                "terminal_command",
                check_str(self.terminal_command, field="terminal_command"),
            )

    @classmethod
    def from_dict(cls, data: Any) -> "ExecutionPlan":
        check_mapping(
            data,
            cls="ExecutionPlan",
            required=("actions",),
            optional=("target_window", "terminal_command"),
        )
        return cls(
            actions=data["actions"],
            target_window=data.get("target_window"),
            terminal_command=data.get("terminal_command"),
        )

    def to_dict(self) -> dict:
        return {
            "actions": list(self.actions),
            "target_window": self.target_window,
            "terminal_command": self.terminal_command,
        }


class MouseBackend(Protocol):
    """Structural contract for cursor hardware."""

    def position(self) -> tuple[int, int]:
        """Current cursor position as (x, y)."""
        ...

    def move_to(self, x: int, y: int) -> None:
        """Teleport the cursor to one trajectory waypoint."""
        ...

    def click(self) -> None:
        """Primary click at the current position."""
        ...


class KeyboardBackend(Protocol):
    """Structural contract for keystroke hardware."""

    def write_char(self, char: str) -> None:
        """Emit one character; timing is controlled by the caller."""
        ...


class FailsafeBackend(Protocol):
    """Structural contract for the human-override tripwire."""

    def arm(self, on_trip: Callable[[], None]) -> None:
        """Start watching for human touch; call ``on_trip`` once on detect."""
        ...

    def disarm(self) -> None:
        """Stop watching (idempotent)."""
        ...


def _bezier_point(
    start: CursorPoint, target: CursorPoint, t: float
) -> CursorPoint:
    """Cubic Bezier with control points at 1/3 and 2/3 (ease-in-out)."""
    c1x = start.x + (target.x - start.x) / 3.0
    c1y = start.y + (target.y - start.y) / 3.0
    c2x = start.x + 2.0 * (target.x - start.x) / 3.0
    c2y = start.y + 2.0 * (target.y - start.y) / 3.0
    u = 1.0 - t
    x = (
        u**3 * start.x
        + 3 * u * u * t * c1x
        + 3 * u * t * t * c2x
        + t**3 * target.x
    )
    y = (
        u**3 * start.y
        + 3 * u * u * t * c1y
        + 3 * u * t * t * c2y
        + t**3 * target.y
    )
    return CursorPoint(x=int(round(x)), y=int(round(y)))


def plan_trajectory(
    start: CursorPoint, target: CursorPoint, steps: int
) -> tuple[CursorPoint, ...]:
    """Plan ``steps`` smooth waypoints from ``start`` to ``target``.

    Returns ``steps + 1`` points including both endpoints. Control points
    lie between the endpoints, so each axis eases monotonically with no
    jumps or overshoot. Pure and deterministic.
    """
    if not isinstance(start, CursorPoint):
        raise ValueError(
            f"start: expected CursorPoint, got {type(start).__name__}"
        )
    if not isinstance(target, CursorPoint):
        raise ValueError(
            f"target: expected CursorPoint, got {type(target).__name__}"
        )
    steps = check_int(steps, field="steps", minimum=1)
    return tuple(
        _bezier_point(start, target, i / steps) for i in range(steps + 1)
    )


_DELAY_MULTIPLIERS = {
    "\n": 8.0,
    ".": 4.0,
    "?": 4.0,
    "!": 4.0,
    ",": 2.5,
    ";": 2.5,
    ":": 2.5,
    "(": 2.0,
    "{": 2.0,
    "[": 2.0,
    ")": 1.5,
    "}": 1.5,
    "]": 1.5,
}


def _jitter(index: int, char: str) -> float:
    """Deterministic organic jitter in [0.85, 1.15)."""
    digest = hashlib.sha1(f"{index}:{char}".encode("utf-8")).digest()
    return 0.85 + 0.30 * (digest[0] / 255.0)


def plan_keystroke_delays(
    text: str, base_wpm: int = 70
) -> tuple[float, ...]:
    """Plan one post-key pause per character (seconds, deterministic).

    Base rate follows the 5-chars-per-word convention; pauses stretch
    after newlines, sentence enders, and syntactic boundaries to mimic
    human hesitation. Pure and deterministic across calls.
    """
    if not isinstance(text, str):
        raise ValueError(
            f"text: expected string, got {type(text).__name__}"
        )
    base_wpm = check_int(base_wpm, field="base_wpm", minimum=1)
    base = 60.0 / (base_wpm * 5.0)
    return tuple(
        base * _DELAY_MULTIPLIERS.get(char, 1.0) * _jitter(index, char)
        for index, char in enumerate(text)
    )


class DefaultMouse:
    """Live cursor backend over ``pyautogui`` (imported lazily per call)."""

    def _lib(self):  # type: ignore[no-untyped-def]
        try:
            import pyautogui
        except ImportError:
            raise ActuatorError(
                "pyautogui is required for live cursor control "
                "(pip install pyautogui)"
            ) from None
        return pyautogui

    def position(self) -> tuple[int, int]:
        point = self._lib().position()
        return (int(point.x), int(point.y))

    def move_to(self, x: int, y: int) -> None:
        lib = self._lib()
        try:
            lib.moveTo(x, y)
        except Exception as exc:
            if type(exc).__name__ == "FailSafeException":
                raise CobotInterruptedError(
                    "pyautogui failsafe corner tripped by human hand"
                ) from None
            raise

    def click(self) -> None:
        self._lib().click()


class DefaultKeyboard:
    """Live keystroke backend over ``pynput`` (imported lazily)."""

    def __init__(self) -> None:
        self._controller = None

    def _lib(self):  # type: ignore[no-untyped-def]
        if self._controller is None:
            try:
                from pynput.keyboard import Controller
            except ImportError:
                raise ActuatorError(
                    "pynput is required for live typing "
                    "(pip install pynput)"
                ) from None
            self._controller = Controller()
        return self._controller

    def write_char(self, char: str) -> None:
        self._lib().type(char)


class DefaultFailsafe:
    """Global tripwire over ``pynput``: mouse displacement + ESC key.

    Self-motion suppression: moves landing exactly on the most recently
    commanded position are the cobot's own glide and never trip; any other
    displacement beyond the jitter radius (or ESC, always) trips.
    """

    def __init__(self) -> None:
        self._listeners: list = []
        self._commanded: Optional[tuple[int, int]] = None
        self._tripped = False

    def note_commanded(self, x: int, y: int) -> None:
        """Record a cobot-commanded position (excluded from tripping)."""
        self._commanded = (x, y)

    def arm(self, on_trip: Callable[[], None]) -> None:
        try:
            from pynput import keyboard, mouse
        except ImportError:
            raise ActuatorError(
                "pynput is required for the live failsafe tripwire "
                "(pip install pynput)"
            ) from None

        def fire_once() -> None:
            if not self._tripped:
                self._tripped = True
                on_trip()

        def on_move(x: int, y: int) -> None:
            if self._commanded is not None and (x, y) == self._commanded:
                return
            anchor = self._commanded or (x, y)
            if math.hypot(x - anchor[0], y - anchor[1]) > _MOUSE_JITTER_PX:
                fire_once()

        def on_press(key: object) -> None:
            if key == keyboard.Key.esc:
                fire_once()

        hover = mouse.Listener(on_move=on_move)
        keys = keyboard.Listener(on_press=on_press)
        hover.start()
        keys.start()
        self._listeners = [hover, keys]

    def disarm(self) -> None:
        for listener in self._listeners:
            try:
                listener.stop()
            except Exception:
                pass
        self._listeners = []


def _split_windows_command(text: str) -> list[str]:
    """Split a command line the way the Windows CRT does (simplified).

    Double quotes group (and are stripped); single quotes are literal;
    backslashes are always literal. Empty tokens are dropped: plan
    strings cannot express explicitly empty arguments.
    """
    tokens: list[str] = []
    current: list[str] = []
    in_quotes = False
    for char in text:
        if char == '"':
            in_quotes = not in_quotes
        elif char in (" ", "\t") and not in_quotes:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(char)
    if in_quotes:
        raise ValueError("terminal_command: unbalanced quote")
    if current:
        tokens.append("".join(current))
    return tokens


def split_command_line(text: str) -> list[str]:
    """Tokenize a plan command string without invoking a shell.

    POSIX uses :func:`shlex.split`; Windows uses CRT-style double-quote
    grouping so quoted interpreter paths survive intact.
    """
    text = check_str(text, field="text")
    if os.name == "nt":
        return _split_windows_command(text)
    return shlex.split(text, posix=True)


def _positive_seconds(value: float, *, field: str) -> float:
    if (
        type(value) is bool
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{field}: expected a positive number of seconds")
    return float(value)


class DesktopActuator:
    """Kinematically smooth and backdrivable OS actuator."""

    def __init__(
        self,
        human_override_callback: Optional[Callable[[], None]] = None,
        *,
        mouse: Optional[MouseBackend] = None,
        keyboard: Optional[KeyboardBackend] = None,
        failsafe: Optional[FailsafeBackend] = None,
        windows: Optional["WindowBackend"] = None,
        event_bus: Optional[EventBus] = None,
        safety: Optional[SafetyController] = None,
    ) -> None:
        if human_override_callback is not None and not callable(
            human_override_callback
        ):
            raise ValueError("human_override_callback: expected a callable")
        if safety is not None and not isinstance(safety, SafetyController):
            raise ValueError(
                "safety: expected SafetyController or None, "
                f"got {type(safety).__name__}"
            )
        self.human_override_callback = human_override_callback
        self._event_bus = event_bus
        self._safety = safety
        self._mouse: MouseBackend = mouse if mouse is not None else DefaultMouse()
        self._keyboard: KeyboardBackend = (
            keyboard if keyboard is not None else DefaultKeyboard()
        )
        self._failsafe: FailsafeBackend = (
            failsafe if failsafe is not None else DefaultFailsafe()
        )
        if windows is None:
            # Lazy import: mimir.desktop imports this module for its
            # error types, so the live backend is resolved at runtime.
            from .desktop import PyWin32WindowBackend

            windows = PyWin32WindowBackend()
        self._windows: "WindowBackend" = windows
        self._interrupted = threading.Event()
        self._failsafe_armed = False
        self._callback_lock = threading.Lock()
        self._callback_fired = False
        self._arm_failsafe()

    @property
    def interrupted(self) -> bool:
        return self._interrupted.is_set()

    @property
    def failsafe_armed(self) -> bool:
        return self._failsafe_armed

    @property
    def window_backend(self) -> "WindowBackend":
        """Injectable window handle/process/title/foreground backend."""
        return self._windows

    @property
    def safety(self) -> Optional[SafetyController]:
        """Attached safety controller, or None when ungated."""
        return self._safety

    def resolve_target(
        self, spec: "TargetSpec | str", *, operation: str = "resolve_target"
    ) -> "WindowInfo":
        """Resolve a target selector to exactly one live window snapshot."""
        from .desktop import resolve_target

        self._poll()
        return resolve_target(
            self._windows, spec, event_bus=self._event_bus,
            operation=operation,
        )

    def validate_target(
        self,
        expected: "WindowInfo",
        *,
        require_foreground: bool = True,
        authorized: bool = True,
        operation: str = "",
    ) -> "WindowInfo":
        """Re-validate a resolved target before a sensitive operation."""
        from .desktop import validate_target

        self._poll()
        return validate_target(
            self._windows,
            expected,
            require_foreground=require_foreground,
            authorized=authorized,
            operation=operation,
            event_bus=self._event_bus,
        )

    def focus_target(
        self,
        handle: int,
        *,
        preserve: bool = True,
        verify: bool = True,
        operation: str = "focus_target",
    ) -> "FocusGuard":
        """Build a guard that activates + verifies ``handle`` on entry.

        Use as ``with actuator.focus_target(info.handle): ...``; the
        preserved companion focus is restored best-effort on exit.
        """
        from .desktop import FocusGuard

        self._poll()
        return FocusGuard(
            self._windows,
            handle,
            preserve=preserve,
            verify=verify,
            event_bus=self._event_bus,
            operation=operation,
        )

    def _arm_failsafe(self) -> None:
        """Arm the tripwire; degrade to disarmed mode when unavailable."""
        try:
            self._failsafe.arm(self._trip)
        except Exception:
            # A missing/broken listener backend must never break
            # construction: abort() stays available as the manual trip.
            self._failsafe_armed = False
        else:
            self._failsafe_armed = True

    def _trip(self, *, source: str = "actuator-failsafe") -> None:
        self._interrupted.set()
        if self._safety is not None:
            try:
                self._safety.trip(source=source, reason=_TRIP_MESSAGE)
            except Exception:
                pass  # propagation must never break the trip itself
        with self._callback_lock:
            if self._callback_fired:
                return
            self._callback_fired = True
            callback = self.human_override_callback
        if callback is not None:
            try:
                callback()
            except Exception:
                pass  # override delivery must never break the trip itself
        if self._event_bus is not None:
            try:
                self._event_bus.emit(
                    EventType.E_STOP_TRIGGERED,
                    "safety.actuator_failsafe",
                    {"failsafe_armed": self._failsafe_armed},
                )
            except Exception:
                pass  # safety telemetry must never break the trip itself

    def abort(self) -> None:
        """Trip the emergency killswitch manually.

        Propagates to the attached safety controller (when any): the
        whole system latches stopped, and resuming needs both
        :meth:`SafetyController.reset` with a deliberate authorization
        and :meth:`reset` here.
        """
        self._trip(source="actuator-abort")

    def reset(self) -> None:
        """Clear a trip so the cobot can resume after human feedback.

        Clears only this actuator's latch: a latched safety controller
        still blocks every operation until
        :meth:`SafetyController.reset` reauthorizes it deliberately.
        """
        self._interrupted.clear()
        with self._callback_lock:
            self._callback_fired = False

    def close(self) -> None:
        """Disarm the failsafe listeners (idempotent)."""
        try:
            self._failsafe.disarm()
        except Exception:
            pass
        self._failsafe_armed = False

    def __enter__(self) -> "DesktopActuator":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _poll(self) -> None:
        if self._interrupted.is_set():
            raise CobotInterruptedError(_TRIP_MESSAGE)
        if self._safety is not None:
            try:
                self._safety.check()
            except SafetyTrippedError as exc:
                raise CobotInterruptedError(f"safety stop: {exc}") from exc

    def _wait(self, seconds: float) -> None:
        # Event.wait wakes the instant abort() trips: halt latency stays
        # in the microsecond range no matter how long the pause is.
        if self._safety is None:
            if self._interrupted.wait(seconds):
                raise CobotInterruptedError(_TRIP_MESSAGE)
            return
        # With a safety controller attached, slice long pauses so a
        # latched E-stop is noticed within ~10 ms, not at the next step.
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if self._interrupted.wait(
                min(_SAFETY_WAIT_SLICE_SEC, remaining)
            ):
                raise CobotInterruptedError(_TRIP_MESSAGE)
            try:
                self._safety.check()
            except SafetyTrippedError as exc:
                raise CobotInterruptedError(f"safety stop: {exc}") from exc

    def _mouse_call(self, name: str, *args: int) -> Any:
        try:
            return getattr(self._mouse, name)(*args)
        except CobotInterruptedError:
            raise
        except Exception as exc:
            raise ActuatorError(f"mouse backend {name} failed: {exc}") from exc

    def _key_call(self, char: str) -> None:
        try:
            self._keyboard.write_char(char)
        except CobotInterruptedError:
            raise
        except Exception as exc:
            raise ActuatorError(f"keyboard backend failed: {exc}") from exc

    def cursor_position(self) -> CursorPoint:
        """Current cursor position, validated through the mouse backend."""
        raw = self._mouse_call("position")
        try:
            return CursorPoint(x=int(raw[0]), y=int(raw[1]))
        except (TypeError, ValueError) as exc:
            raise ActuatorError(
                f"mouse backend returned a bad position: {raw!r}"
            ) from exc

    def move_cursor_smooth(
        self,
        target: CursorPoint,
        duration_sec: float = 0.6,
        steps: int = 30,
        *,
        verify: bool = False,
        tolerance: int = 2,
    ) -> None:
        """Glide along a cubic Bezier trajectory, polling the tripwire.

        With ``verify=True`` the cursor is read back afterwards and an
        :class:`ActuatorError` is raised when it missed ``target`` by
        more than ``tolerance`` pixels: successful injection is not
        proof of execution.
        """
        if not isinstance(target, CursorPoint):
            raise ValueError(
                f"target: expected CursorPoint, got {type(target).__name__}"
            )
        duration_sec = _positive_seconds(duration_sec, field="duration_sec")
        steps = check_int(steps, field="steps", minimum=1)
        verify = check_bool(verify, field="verify")
        tolerance = check_int(tolerance, field="tolerance", minimum=0)
        start = self.cursor_position()
        per_step = duration_sec / steps
        for point in plan_trajectory(start, target, steps)[1:]:
            self._poll()
            note = getattr(self._failsafe, "note_commanded", None)
            if callable(note):
                note(point.x, point.y)
            self._mouse_call("move_to", point.x, point.y)
            self._wait(per_step)
        if verify:
            from .desktop import verify_cursor_at

            self._poll()
            verify_cursor_at(
                self._mouse,
                target.x,
                target.y,
                tolerance=tolerance,
                event_bus=self._event_bus,
                operation="move_cursor_smooth",
            )

    def execute_trajectory(
        self,
        points: Sequence[CursorPoint],
        duration_sec: float = 0.6,
        *,
        bounds: Optional[tuple[int, int]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> dict[str, Any]:
        """Command a precomputed Bezier trajectory on a worker thread.

        Dedicated-executor path (plan section 7): monotonic scheduling,
        fail-closed screen-bounds validation, stale-sample skipping
        (no catch-up bursts), and deadline metrics. ``points`` usually
        comes from :func:`plan_trajectory`. ``cancel_event`` (plus this
        actuator's own interrupt) aborts via
        :class:`CobotInterruptedError`; bounds violations raise
        :class:`ActuatorError` before any motion. With a safety
        controller attached, a latched E-stop aborts before the first
        sample and at every subsequent sample. Returns the
        trajectory report as a dict (see
        :class:`mimir.trajectory.TrajectoryReport`).

        Run this on an actuation worker thread, never on the Qt main
        thread: it blocks for ``duration_sec`` by design.
        """
        from .trajectory import TrajectoryExecutor
        from .trajectory import TrajectoryBoundsError as _Bounds
        from .trajectory import TrajectoryCancelled as _Cancelled

        if not isinstance(points, (list, tuple)) or not points:
            raise ValueError("points: expected a non-empty sequence")
        for point in points:
            if not isinstance(point, CursorPoint):
                raise ValueError(
                    "points: expected CursorPoint entries, got "
                    f"{type(point).__name__}"
                )
        if cancel_event is not None and not isinstance(
            cancel_event, threading.Event
        ):
            raise ValueError("cancel_event: expected threading.Event or None")
        self._poll()
        note = getattr(self._failsafe, "note_commanded", None)
        move_to: Callable[[int, int], None] = (
            lambda x, y: self._mouse_call("move_to", x, y)
        )
        if self._safety is not None:
            unguarded = move_to

            def _guarded_move(x: int, y: int) -> None:
                # Per-sample safety checkpoint: the executor only watches
                # cancel/interrupt events during its 2 ms sleep slices, so
                # each commanded sample re-polls the E-stop latch.
                self._poll()
                unguarded(x, y)

            move_to = _guarded_move
        executor = TrajectoryExecutor(
            move_to=move_to,
            note_commanded=note if callable(note) else None,
        )
        try:
            report = executor.execute(
                list(points),
                duration_sec,
                bounds=bounds,
                cancel_event=cancel_event,
                interrupt_event=self._interrupted,
            )
        except _Cancelled as exc:
            raise CobotInterruptedError(_TRIP_MESSAGE) from exc
        except _Bounds as exc:
            raise ActuatorError(str(exc)) from exc
        return report.to_dict()

    def type_natural(
        self,
        text: str,
        base_wpm: int = 70,
        *,
        verify_fn: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Type with organic cadence and syntactic micro-pauses.

        ``verify_fn`` (when given) independently confirms the outcome
        after typing -- entering text is not evidence the intended
        document changed. A falsy return (or a raising check) becomes
        :class:`ActuatorError`.
        """
        text = check_str(text, field="text")
        if verify_fn is not None and not callable(verify_fn):
            raise ValueError("verify_fn: expected a callable or None")
        delays = plan_keystroke_delays(text, base_wpm)
        for char, pause in zip(text, delays):
            self._poll()
            self._key_call(char)
            self._wait(pause)
        if verify_fn is not None:
            from .desktop import verify_completion

            self._poll()
            try:
                ok = verify_fn()
            except ActuatorError:
                raise
            except Exception as exc:
                raise ActuatorError(
                    "completion verification failed for type_natural: "
                    f"verify_fn raised: {exc}"
                ) from exc
            verify_completion(bool(ok), operation="type_natural")

    def click_point(
        self,
        point: CursorPoint,
        *,
        verify: bool = False,
        tolerance: int = 2,
    ) -> None:
        """Glide to the point, pause 80 ms, and click."""
        self.move_cursor_smooth(
            point, verify=verify, tolerance=tolerance
        )
        self._wait(0.08)
        self._poll()
        self._mouse_call("click")

    def run_safe_command(
        self,
        command: Sequence[str],
        cwd: Optional[str] = None,
        timeout_sec: float = 30.0,
    ) -> tuple[int, str, str]:
        """Run a subprocess (never a shell), capturing stdout/stderr.

        Polls the tripwire while waiting: a human override kills the child
        and raises :class:`CobotInterruptedError`. A hit timeout kills the
        child and raises :class:`ActuatorError`.
        """
        if (
            not isinstance(command, (list, tuple))
            or not command
            or any(not isinstance(c, str) or not c for c in command)
        ):
            raise ValueError("command: expected a non-empty list of strings")
        if cwd is not None:
            cwd = check_str(cwd, field="cwd")
        timeout_sec = _positive_seconds(timeout_sec, field="timeout_sec")
        # Pre-execution gate: never spawn while interrupted or E-stopped.
        self._poll()
        try:
            proc = subprocess.Popen(
                list(command),
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except CobotInterruptedError:
            raise
        except Exception as exc:
            raise ActuatorError(f"cannot start {list(command)!r}: {exc}") from exc
        deadline = time.monotonic() + timeout_sec
        assert proc.stdout is not None and proc.stderr is not None
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        try:
            while True:
                self._poll()
                try:
                    out, err = proc.communicate(timeout=_COMMAND_POLL_SEC)
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        proc.kill()
                        proc.communicate()  # reap the killed child
                        raise ActuatorError(
                            f"command timed out after {timeout_sec}s: "
                            f"{list(command)!r}"
                        ) from None
                    continue
                stdout_chunks.append(out)
                stderr_chunks.append(err)
                break
        except CobotInterruptedError:
            proc.kill()
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            raise
        return (proc.returncode, "".join(stdout_chunks), "".join(stderr_chunks))

    def execute_plan(
        self,
        plan: ExecutionPlan,
        *,
        target: Optional["TargetSpec | WindowInfo | str"] = None,
        authorized: bool = True,
        verify_fn: Optional[Callable[[], bool]] = None,
    ) -> dict[str, Any]:
        """Execute a plan: run its terminal command, audit its labels.

        Action labels are recorded verbatim for the audit trail; the
        terminal command (when present) executes via :meth:`run_safe_command`
        after shell-free tokenizing. Trips propagate as raised errors.

        With ``target`` set, the plan runs inside a validated focus
        session: resolve -> companion preserve -> target activate ->
        foreground verify -> command -> target revalidation (a changed
        target invalidates the operation) -> optional ``verify_fn``
        completion check -> companion restore. Without ``target`` the
        legacy behavior is kept and ``plan.target_window`` stays an
        audit label only.
        """
        if not isinstance(plan, ExecutionPlan):
            raise ValueError(
                f"plan: expected ExecutionPlan, got {type(plan).__name__}"
            )
        if verify_fn is not None and not callable(verify_fn):
            raise ValueError("verify_fn: expected a callable or None")
        authorized = check_bool(authorized, field="authorized")
        # Pre-execution gate: a latched E-stop rejects the plan before any
        # step runs, even for command-less audit-only plans.
        self._poll()
        report: dict[str, Any] = {
            "actions": list(plan.actions),
            "target_window": plan.target_window,
            "terminal_exit": None,
            "terminal_stdout": "",
            "terminal_stderr": "",
            "target_validated": False,
            "focus_verified": False,
            "completion_verified": False,
        }
        if target is None:
            self._run_plan_command(plan, report)
            if verify_fn is not None:
                report["completion_verified"] = self._run_verify_fn(
                    verify_fn, "execute_plan"
                )
            return report

        from .desktop import TargetSpec, WindowInfo

        if isinstance(target, WindowInfo):
            expected = target
        elif isinstance(target, (TargetSpec, str)):
            expected = self.resolve_target(target, operation="execute_plan")
        else:
            raise ValueError(
                "target: expected TargetSpec, WindowInfo, str, or None; "
                f"got {type(target).__name__}"
            )
        with self.focus_target(
            expected.handle, operation="execute_plan"
        ) as guard:
            live = self.validate_target(
                expected, authorized=authorized, operation="execute_plan"
            )
            report["target_validated"] = True
            report["focus_verified"] = guard.verified
            self._run_plan_command(plan, report)
            # The target must still be the same window after the work:
            # a mid-operation target change invalidates the outcome.
            self.validate_target(
                live, authorized=authorized, operation="execute_plan"
            )
            if verify_fn is not None:
                report["completion_verified"] = self._run_verify_fn(
                    verify_fn, "execute_plan"
                )
        return report

    def _run_plan_command(
        self, plan: ExecutionPlan, report: dict[str, Any]
    ) -> None:
        if plan.terminal_command is None:
            return
        tokens = split_command_line(plan.terminal_command)
        if not tokens:
            raise ValueError("terminal_command: tokenizes to nothing")
        code, out, err = self.run_safe_command(tokens)
        report["terminal_exit"] = code
        report["terminal_stdout"] = out
        report["terminal_stderr"] = err

    def _run_verify_fn(
        self, verify_fn: Callable[[], bool], operation: str
    ) -> bool:
        from .desktop import verify_completion

        self._poll()
        try:
            ok = verify_fn()
        except ActuatorError:
            raise
        except Exception as exc:
            raise ActuatorError(
                f"completion verification failed for {operation}: "
                f"verify_fn raised: {exc}"
            ) from exc
        verify_completion(bool(ok), operation=operation)
        return True
