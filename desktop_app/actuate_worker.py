"""Compliant-drive worker (Qt): countdown, glide, type, E-stop.

The pure helpers (:func:`format_countdown`, :func:`drive_text_blocking`)
are headless-testable; :class:`DriveWorker` runs them on a ``QThread`` so
the Replicate tab stays responsive during the 3-second countdown and the
human-cadence typing. Any human touch (failsafe tripwire) or
:meth:`DriveWorker.cancel` raises :class:`CobotInterruptedError` inside
the drive, which surfaces as ``drive_interrupted`` — the tab then flashes
the visor to alert and offers instant negative-invariant creation.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from mimir.actuator import (
    ActuatorError,
    CobotInterruptedError,
    CursorPoint,
    DesktopActuator,
)
from mimir.desktop import TargetSpec, WindowInfo
from mimir.safety import SafetyController

from .tabs._qt import load_qt

__all__ = [
    "COUNTDOWN_SEC",
    "DriveWorker",
    "create_drive_worker",
    "drive_text_blocking",
    "format_countdown",
    "list_drive_targets",
]

#: Seconds of warned countdown before the cursor starts moving.
COUNTDOWN_SEC = 3

_, QtCore, _, _ = load_qt()

if hasattr(QtCore, "pyqtSignal"):
    _Signal = QtCore.pyqtSignal
else:  # PySide6
    _Signal = QtCore.Signal


def format_countdown(seconds_left: int) -> str:
    """Countdown notice shown while the drive can still be cancelled."""
    return (f"Starting cobot drive in {seconds_left}s... "
            "Press ESC or touch mouse to stop")


def list_drive_targets(
    backend: Any = None,
) -> tuple[list[tuple[int, str]], Optional[int]]:
    """Enumerate candidate drive targets: ``([(handle, label)], fg)``.

    Headless-safe: returns ``([], None)`` when window enumeration is
    unavailable (no pywin32, no interactive desktop, any backend
    error), in which case callers keep the legacy caller-owns-focus
    behavior. Otherwise ``fg`` is the current foreground handle (the
    suggested default) when it is among the listed windows.
    """
    if backend is None:
        try:
            from mimir.desktop import PyWin32WindowBackend
            backend = PyWin32WindowBackend()
        except Exception:
            return ([], None)
    try:
        windows = backend.list_windows()
    except Exception:
        return ([], None)
    items: list[tuple[int, str]] = []
    try:
        for info in windows or []:
            handle = int(getattr(info, "handle", 0) or 0)
            if handle <= 0:
                continue
            exe = (getattr(info, "executable", "") or "").strip()
            title = (getattr(info, "title", "") or "").strip()
            label = f"{exe or '(unknown app)'} — {title or '(no title)'}"
            items.append((handle, label[:160]))
    except Exception:
        return ([], None)
    if not items:
        return ([], None)
    try:
        foreground = backend.foreground()
        fg_handle = int(getattr(foreground, "handle", 0) or 0)
    except Exception:
        fg_handle = 0
    handles = {handle for handle, _ in items}
    selected = fg_handle if fg_handle in handles else None
    return (items, selected)


def drive_text_blocking(actuator: DesktopActuator, text: str, *,
                        glide: bool = True,
                        base_wpm: int = 70,
                        target: Optional[Any] = None,
                        authorized: bool = True,
                        verify_cursor: bool = False,
                        verify_fn: Optional[Callable[[], bool]] = None
                        ) -> dict[str, Any]:
    """Glide briefly, then type ``text`` with human cadence (blocking).

    Propagates :class:`CobotInterruptedError` on human override so the
    caller can flash the visor and offer invariant creation.

    With ``target`` (a :class:`TargetSpec`, :class:`WindowInfo`, or
    title-substring ``str``) the drive runs inside a validated focus
    session: resolve -> companion preserve -> target activate ->
    foreground verify -> glide/type -> target revalidation -> companion
    restore. Without ``target`` the legacy behavior is kept (the
    caller owns focus). ``verify_cursor`` reads the cursor back after
    the glide; ``verify_fn`` independently confirms the typed outcome.
    """
    if not isinstance(actuator, DesktopActuator):
        raise ValueError(
            "actuator: expected DesktopActuator, "
            f"got {type(actuator).__name__}"
        )
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text: expected a non-empty string")
    if verify_fn is not None and not callable(verify_fn):
        raise ValueError("verify_fn: expected a callable or None")
    report: dict[str, Any] = {
        "typed_chars": 0,
        "glide": bool(glide),
        "target_validated": False,
        "focus_verified": False,
        "completion_verified": False,
    }

    def _drive() -> None:
        if glide:
            start = actuator.cursor_position()
            actuator.move_cursor_smooth(
                CursorPoint(x=start.x + 60, y=start.y + 20),
                duration_sec=0.6, steps=12,
                verify=verify_cursor,
            )
        actuator.type_natural(text, base_wpm=base_wpm,
                              verify_fn=verify_fn)
        report["typed_chars"] = len(text)
        if verify_fn is not None:
            report["completion_verified"] = True

    if target is None:
        _drive()
        return report
    if isinstance(target, WindowInfo):
        expected = target
    elif isinstance(target, (TargetSpec, str)):
        expected = actuator.resolve_target(target, operation="drive_text")
    else:
        raise ValueError(
            "target: expected TargetSpec, WindowInfo, str, or None; "
            f"got {type(target).__name__}"
        )
    with actuator.focus_target(
        expected.handle, operation="drive_text"
    ) as guard:
        live = actuator.validate_target(
            expected, authorized=authorized, operation="drive_text"
        )
        report["target_validated"] = True
        report["focus_verified"] = guard.verified
        _drive()
        # A mid-drive target change invalidates the typed outcome.
        actuator.validate_target(
            live, authorized=authorized, operation="drive_text"
        )
    return report


class DriveWorker(QtCore.QThread):
    """Countdown-then-drive on a background thread with live cancel."""

    countdown_tick = _Signal(int)
    drive_started = _Signal()
    drive_finished = _Signal(dict)
    drive_interrupted = _Signal(str)
    drive_error = _Signal(str)

    def __init__(self, text: str, *,
                 countdown_sec: int = COUNTDOWN_SEC,
                 base_wpm: int = 70,
                 actuator_factory: Optional[
                     Callable[[], DesktopActuator]] = None,
                 target: Optional[Any] = None,
                 authorized: bool = True,
                 verify_cursor: bool = False,
                 safety: Optional[SafetyController] = None,
                 parent: Any = None) -> None:
        super().__init__(parent)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text: expected a non-empty string")
        if safety is not None and not isinstance(safety, SafetyController):
            raise ValueError(
                "safety: expected SafetyController or None, "
                f"got {type(safety).__name__}"
            )
        self._text = text
        self._countdown = max(0, int(countdown_sec))
        self._base_wpm = base_wpm
        self._factory = actuator_factory or (lambda: DesktopActuator())
        self._target = target
        self._authorized = authorized
        self._verify_cursor = verify_cursor
        self._safety = safety
        self._cancel = threading.Event()
        self._actuator: Optional[DesktopActuator] = None
        self._lock = threading.Lock()

    def cancel(self) -> None:
        """Request a stop: aborts the countdown or trips the E-stop."""
        self._cancel.set()
        with self._lock:
            actuator = self._actuator
        if actuator is not None:
            actuator.abort()

    def run(self) -> None:  # Qt override (runs on the worker thread)
        for left in range(self._countdown, 0, -1):
            self.countdown_tick.emit(left)
            if self._cancel.wait(1.0):
                self.drive_interrupted.emit(
                    "Drive cancelled during countdown.")
                return
        if self._safety is not None and self._safety.estopped:
            # Fail-closed pre-drive gate (plan section 8): no actuator
            # is even built while the E-stop is latched. Resuming needs
            # deliberate reauthorization in the hosting tab.
            self.drive_error.emit(
                "Safety stop is active — reauthorize before driving.")
            return
        self.drive_started.emit()
        actuator = self._factory()
        with self._lock:
            self._actuator = actuator
        if self._cancel.is_set():
            actuator.abort()
        try:
            report = drive_text_blocking(actuator, self._text,
                                         base_wpm=self._base_wpm,
                                         target=self._target,
                                         authorized=self._authorized,
                                         verify_cursor=self._verify_cursor)
        except CobotInterruptedError as exc:
            self.drive_interrupted.emit(str(exc))
        except (ActuatorError, ValueError) as exc:
            self.drive_error.emit(str(exc))
        except Exception as exc:  # hardware backends stay in-app
            self.drive_error.emit(f"drive failed: {exc}")
        else:
            self.drive_finished.emit(report)
        finally:
            with self._lock:
                self._actuator = None
            try:
                actuator.close()
            except Exception:
                pass


def create_drive_worker(text: str, *,
                        countdown_sec: int = COUNTDOWN_SEC,
                        base_wpm: int = 70,
                        actuator_factory: Optional[
                            Callable[[], DesktopActuator]] = None,
                        target: Optional[Any] = None,
                        authorized: bool = True,
                        verify_cursor: bool = False,
                        safety: Optional[SafetyController] = None,
                        parent: Optional[Any] = None) -> DriveWorker:
    """Build (but do not start) a compliant-drive worker.

    Connect to ``countdown_tick`` / ``drive_started`` /
    ``drive_finished`` / ``drive_interrupted`` / ``drive_error`` first,
    keep a reference for the worker's lifetime, then call
    ``worker.start()``. Call ``worker.cancel()`` to stop it.

    ``target`` runs the drive inside a validated focus session (see
    :func:`drive_text_blocking`); validation failures surface as
    ``drive_error`` (safe abort, never blind input). ``safety`` adds a
    fail-closed pre-drive gate: a latched E-stop surfaces as
    ``drive_error`` before any actuator is built.
    """
    return DriveWorker(text, countdown_sec=countdown_sec,
                       base_wpm=base_wpm,
                       actuator_factory=actuator_factory, parent=parent,
                       target=target, authorized=authorized,
                       verify_cursor=verify_cursor, safety=safety)
