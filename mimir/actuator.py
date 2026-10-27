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
from typing import Any, Callable, Optional, Protocol, Sequence

from ._common import check_int, check_mapping, check_str

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
    "plan_keystroke_delays",
    "plan_trajectory",
    "split_command_line",
]

_TRIP_MESSAGE = "human override: cobot motion interrupted"
_COMMAND_POLL_SEC = 0.05
_MOUSE_JITTER_PX = 5


class ActuatorError(Exception):
    """Base error for physical actuation failures."""


class CobotInterruptedError(ActuatorError):
    """Raised immediately when the human touches the mouse or hits ESC."""


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
    ) -> None:
        if human_override_callback is not None and not callable(
            human_override_callback
        ):
            raise ValueError("human_override_callback: expected a callable")
        self.human_override_callback = human_override_callback
        self._mouse: MouseBackend = mouse if mouse is not None else DefaultMouse()
        self._keyboard: KeyboardBackend = (
            keyboard if keyboard is not None else DefaultKeyboard()
        )
        self._failsafe: FailsafeBackend = (
            failsafe if failsafe is not None else DefaultFailsafe()
        )
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

    def _trip(self) -> None:
        self._interrupted.set()
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

    def abort(self) -> None:
        """Trip the emergency killswitch manually."""
        self._trip()

    def reset(self) -> None:
        """Clear a trip so the cobot can resume after human feedback."""
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

    def _wait(self, seconds: float) -> None:
        # Event.wait wakes the instant abort() trips: halt latency stays
        # in the microsecond range no matter how long the pause is.
        if self._interrupted.wait(seconds):
            raise CobotInterruptedError(_TRIP_MESSAGE)

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
    ) -> None:
        """Glide along a cubic Bezier trajectory, polling the tripwire."""
        if not isinstance(target, CursorPoint):
            raise ValueError(
                f"target: expected CursorPoint, got {type(target).__name__}"
            )
        duration_sec = _positive_seconds(duration_sec, field="duration_sec")
        steps = check_int(steps, field="steps", minimum=1)
        start = self.cursor_position()
        per_step = duration_sec / steps
        for point in plan_trajectory(start, target, steps)[1:]:
            self._poll()
            note = getattr(self._failsafe, "note_commanded", None)
            if callable(note):
                note(point.x, point.y)
            self._mouse_call("move_to", point.x, point.y)
            self._wait(per_step)

    def type_natural(self, text: str, base_wpm: int = 70) -> None:
        """Type with organic cadence and syntactic micro-pauses."""
        text = check_str(text, field="text")
        delays = plan_keystroke_delays(text, base_wpm)
        for char, pause in zip(text, delays):
            self._poll()
            self._key_call(char)
            self._wait(pause)

    def click_point(self, point: CursorPoint) -> None:
        """Glide to the point, pause 80 ms, and click."""
        self.move_cursor_smooth(point)
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

    def execute_plan(self, plan: ExecutionPlan) -> dict[str, Any]:
        """Execute a plan: run its terminal command, audit its labels.

        Action labels are recorded verbatim for the audit trail; the
        terminal command (when present) executes via :meth:`run_safe_command`
        after shell-free tokenizing. Trips propagate as raised errors.
        """
        if not isinstance(plan, ExecutionPlan):
            raise ValueError(
                f"plan: expected ExecutionPlan, got {type(plan).__name__}"
            )
        report: dict[str, Any] = {
            "actions": list(plan.actions),
            "target_window": plan.target_window,
            "terminal_exit": None,
            "terminal_stdout": "",
            "terminal_stderr": "",
        }
        if plan.terminal_command is not None:
            tokens = split_command_line(plan.terminal_command)
            if not tokens:
                raise ValueError("terminal_command: tokenizes to nothing")
            code, out, err = self.run_safe_command(tokens)
            report["terminal_exit"] = code
            report["terminal_stdout"] = out
            report["terminal_stderr"] = err
        return report
