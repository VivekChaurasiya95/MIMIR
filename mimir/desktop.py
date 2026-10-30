"""Windows desktop target management: identify, validate, focus, inject.

Step 4 of the improvement plan (section 5): the actuator must not type
into whatever happens to have focus. This module provides the dedicated
desktop-control interface:

- :class:`WindowInfo` / :class:`TargetSpec` -- strict window contracts.
- :class:`WindowBackend` -- injectable handle/process/title/foreground
  contract, with a live :class:`PyWin32WindowBackend` (``pywin32`` is
  imported lazily per call, mirroring the actuator's backend style) and
  headless fakes in tests.
- :func:`resolve_target` / :func:`validate_target` -- fail-closed target
  validation (handle, process identity, application, foreground
  ownership, authorization, target-changed). Every failure emits
  ``TARGET_VALIDATION_FAILED`` best-effort and raises
  :class:`TargetValidationError`.
- :class:`FocusGuard` -- companion focus preserve / target activate /
  foreground verify / companion restore around one operation.
- :class:`SendInputMouse` / :class:`SendInputKeyboard` -- controlled
  Win32 ``SendInput`` input backends behind the existing
  :class:`MouseBackend` / :class:`KeyboardBackend` protocols.
- :func:`verify_cursor_at` / :func:`verify_foreground` /
  :func:`verify_completion` -- per-operation completion verification.

Qt-free and headless-testable: every live call funnels through small
injectable seams (backend objects, sender/metrics callables).
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol, Union

from ._common import check_bool, check_int, check_mapping, check_str
from .actuator import ActuatorError, TargetValidationError
from .events import EventBus, EventType

__all__ = [
    "FocusGuard",
    "PyWin32WindowBackend",
    "SendInputKeyboard",
    "SendInputMouse",
    "TargetSpec",
    "WindowBackend",
    "WindowInfo",
    "resolve_target",
    "validate_target",
    "verify_completion",
    "verify_cursor_at",
    "verify_foreground",
]

_VALIDATE_SOURCE = "actuation.target_validator"
_FOCUS_SOURCE = "actuation.focus_guard"
_VERIFY_SOURCE = "actuation.verifier"


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowInfo:
    """A snapshot of one top-level window (handle/process/app/title)."""

    handle: int
    process_id: int
    executable: str = ""
    title: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "handle", check_int(self.handle, field="handle", minimum=1)
        )
        object.__setattr__(
            self,
            "process_id",
            check_int(self.process_id, field="process_id", minimum=0),
        )
        if not isinstance(self.executable, str):
            raise ValueError(
                "executable: expected string, "
                f"got {type(self.executable).__name__}"
            )
        if not isinstance(self.title, str):
            raise ValueError(
                f"title: expected string, got {type(self.title).__name__}"
            )

    @classmethod
    def from_dict(cls, data: Any) -> "WindowInfo":
        check_mapping(
            data,
            cls="WindowInfo",
            required=("handle", "process_id"),
            optional=("executable", "title"),
        )
        return cls(
            handle=data["handle"],
            process_id=data["process_id"],
            executable=data.get("executable", ""),
            title=data.get("title", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "process_id": self.process_id,
            "executable": self.executable,
            "title": self.title,
        }


@dataclass(frozen=True)
class TargetSpec:
    """Selector for resolving the intended target window.

    At least one selector must be set. ``handle`` is exact; the other
    selectors filter case-insensitively (substring for ``executable`` and
    ``title_substring``, exact for ``process_id``).
    """

    handle: Optional[int] = None
    process_id: Optional[int] = None
    executable: Optional[str] = None
    title_substring: Optional[str] = None

    def __post_init__(self) -> None:
        if self.handle is not None:
            object.__setattr__(
                self,
                "handle",
                check_int(self.handle, field="handle", minimum=1),
            )
        if self.process_id is not None:
            object.__setattr__(
                self,
                "process_id",
                check_int(self.process_id, field="process_id", minimum=0),
            )
        if self.executable is not None:
            object.__setattr__(
                self,
                "executable",
                check_str(self.executable, field="executable"),
            )
        if self.title_substring is not None:
            object.__setattr__(
                self,
                "title_substring",
                check_str(self.title_substring, field="title_substring"),
            )
        if (
            self.handle is None
            and self.process_id is None
            and self.executable is None
            and self.title_substring is None
        ):
            raise ValueError(
                "TargetSpec: at least one selector "
                "(handle/process_id/executable/title_substring) is required"
            )

    @classmethod
    def from_dict(cls, data: Any) -> "TargetSpec":
        check_mapping(
            data,
            cls="TargetSpec",
            required=(),
            optional=("handle", "process_id", "executable", "title_substring"),
        )
        return cls(
            handle=data.get("handle"),
            process_id=data.get("process_id"),
            executable=data.get("executable"),
            title_substring=data.get("title_substring"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "process_id": self.process_id,
            "executable": self.executable,
            "title_substring": self.title_substring,
        }


class WindowBackend(Protocol):
    """Structural contract for window handle/process/title/foreground I/O."""

    def list_windows(self) -> list["WindowInfo"]:
        """Enumerate candidate top-level windows."""
        ...

    def foreground(self) -> Optional["WindowInfo"]:
        """Current foreground window, or None when it cannot be read."""
        ...

    def is_valid(self, handle: int) -> bool:
        """Whether ``handle`` still names a live window."""
        ...

    def activate(self, handle: int) -> bool:
        """Request foreground activation; False when the OS refuses."""
        ...


# ---------------------------------------------------------------------------
# Live pywin32 backend
# ---------------------------------------------------------------------------


class PyWin32WindowBackend:
    """Live window backend over ``pywin32`` (imported lazily per call)."""

    def _gui(self) -> Any:
        try:
            import win32gui
        except ImportError:
            raise ActuatorError(
                "pywin32 is required for window management "
                "(pip install pywin32)"
            ) from None
        return win32gui

    def _process_mod(self) -> Any:
        try:
            import win32process
        except ImportError:
            raise ActuatorError(
                "pywin32 is required for window management "
                "(pip install pywin32)"
            ) from None
        return win32process

    def _exe_of(self, pid: int) -> str:
        """Best-effort executable path for ``pid`` ("" when unreadable)."""
        try:
            import win32api
            import win32con
            import win32process
        except ImportError:
            return ""
        handle = None
        try:
            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            return str(win32process.GetModuleFileNameEx(handle, 0) or "")
        except Exception:
            return ""
        finally:
            if handle is not None:
                try:
                    win32api.CloseHandle(handle)
                except Exception:
                    pass

    def _info_of(self, hwnd: int) -> Optional[WindowInfo]:
        gui = self._gui()
        try:
            title = gui.GetWindowText(hwnd) or ""
        except Exception:
            return None
        try:
            _, pid = self._process_mod().GetWindowThreadProcessId(hwnd)
        except Exception:
            return None
        try:
            return WindowInfo(
                handle=int(hwnd),
                process_id=int(pid),
                executable=self._exe_of(int(pid)),
                title=str(title),
            )
        except ValueError:
            return None

    def list_windows(self) -> list[WindowInfo]:
        gui = self._gui()
        found: list[WindowInfo] = []

        def _collect(hwnd: int, _extra: Any) -> bool:
            info = self._info_of(hwnd)
            if info is not None:
                found.append(info)
            return True

        gui.EnumWindows(_collect, None)
        return found

    def foreground(self) -> Optional[WindowInfo]:
        try:
            hwnd = self._gui().GetForegroundWindow()
        except ActuatorError:
            raise
        except Exception:
            return None
        if not hwnd:
            return None
        return self._info_of(int(hwnd))

    def is_valid(self, handle: int) -> bool:
        handle = check_int(handle, field="handle", minimum=1)
        try:
            return bool(self._gui().IsWindow(handle))
        except ActuatorError:
            raise
        except Exception:
            return False

    def activate(self, handle: int) -> bool:
        """Request activation; False when Windows denies the switch.

        Foreground activation is a *request*: Windows may refuse it
        depending on input/process state, so callers must verify with
        :func:`verify_foreground` (or :class:`FocusGuard`) afterwards.
        """
        handle = check_int(handle, field="handle", minimum=1)
        gui = self._gui()
        try:
            try:
                if gui.IsIconic(handle):
                    gui.ShowWindow(handle, 9)  # SW_RESTORE
            except Exception:
                pass
            gui.SetForegroundWindow(handle)
        except ActuatorError:
            raise
        except Exception:
            return False
        return True


# ---------------------------------------------------------------------------
# Target validation
# ---------------------------------------------------------------------------


def _emit_validation_failed(
    event_bus: Optional[EventBus],
    *,
    reason: str,
    operation: str,
    expected: Optional[WindowInfo] = None,
    detail: str = "",
) -> None:
    if event_bus is None:
        return
    payload: dict[str, Any] = {"reason": reason, "operation": operation}
    if expected is not None:
        payload["expected"] = expected.to_dict()
    if detail:
        payload["detail"] = detail
    try:
        event_bus.emit(
            EventType.TARGET_VALIDATION_FAILED, _VALIDATE_SOURCE, payload
        )
    except Exception:
        pass  # telemetry must never break fail-closed validation


def _fail(
    reason: str,
    message: str,
    *,
    event_bus: Optional[EventBus] = None,
    operation: str = "",
    expected: Optional[WindowInfo] = None,
    detail: str = "",
) -> TargetValidationError:
    _emit_validation_failed(
        event_bus,
        reason=reason,
        operation=operation,
        expected=expected,
        detail=detail or message,
    )
    return TargetValidationError(message, reason=reason)


def resolve_target(
    backend: WindowBackend,
    spec: Union[TargetSpec, str],
    *,
    event_bus: Optional[EventBus] = None,
    operation: str = "resolve_target",
) -> WindowInfo:
    """Resolve ``spec`` to exactly one live window snapshot.

    Raises :class:`TargetValidationError` (fail-closed) when the backend
    is unavailable, nothing matches, several windows match, or the
    matched handle is no longer valid.
    """
    if isinstance(spec, str):
        spec = TargetSpec(title_substring=spec)
    if not isinstance(spec, TargetSpec):
        raise ValueError(
            f"spec: expected TargetSpec or str, got {type(spec).__name__}"
        )
    try:
        windows = backend.list_windows()
    except TargetValidationError:
        raise
    except Exception as exc:
        raise _fail(
            "backend_error",
            f"target resolution failed: window enumeration failed: {exc}",
            event_bus=event_bus,
            operation=operation,
        ) from exc

    candidates = list(windows)
    if spec.handle is not None:
        candidates = [w for w in candidates if w.handle == spec.handle]
        if not candidates:
            try:
                valid = backend.is_valid(spec.handle)
            except Exception as exc:
                raise _fail(
                    "backend_error",
                    "target resolution failed: "
                    f"handle validity check failed: {exc}",
                    event_bus=event_bus,
                    operation=operation,
                ) from exc
            if not valid:
                raise _fail(
                    "unknown_handle",
                    "target resolution failed: "
                    f"handle {spec.handle} names no live window",
                    event_bus=event_bus,
                    operation=operation,
                )
            raise _fail(
                "spec_mismatch",
                "target resolution failed: "
                f"handle {spec.handle} is live but not enumerable "
                "(child window or enumeration race); refusing to guess",
                event_bus=event_bus,
                operation=operation,
            )
    if spec.process_id is not None:
        candidates = [
            w for w in candidates if w.process_id == spec.process_id
        ]
    if spec.executable is not None:
        needle = spec.executable.lower()
        candidates = [
            w for w in candidates if needle in w.executable.lower()
        ]
    if spec.title_substring is not None:
        needle = spec.title_substring.lower()
        candidates = [w for w in candidates if needle in w.title.lower()]

    if spec.handle is None and not candidates:
        raise _fail(
            "no_match",
            f"target resolution failed: no window matches {spec.to_dict()}",
            event_bus=event_bus,
            operation=operation,
        )
    if spec.handle is not None and not candidates:
        raise _fail(
            "spec_mismatch",
            f"target resolution failed: handle {spec.handle} does not "
            f"match the remaining selectors in {spec.to_dict()}",
            event_bus=event_bus,
            operation=operation,
        )
    if len(candidates) > 1:
        raise _fail(
            "ambiguous",
            "target resolution failed: "
            f"{len(candidates)} windows match {spec.to_dict()}; "
            "narrow the selectors instead of guessing",
            event_bus=event_bus,
            operation=operation,
        )
    info = candidates[0]
    try:
        valid = backend.is_valid(info.handle)
    except Exception as exc:
        raise _fail(
            "backend_error",
            "target resolution failed: "
            f"handle validity check failed: {exc}",
            event_bus=event_bus,
            operation=operation,
        ) from exc
    if not valid:
        raise _fail(
            "unknown_handle",
            "target resolution failed: "
            f"handle {info.handle} died between enumeration and use",
            event_bus=event_bus,
            operation=operation,
        )
    return info


def _live_snapshot(
    backend: WindowBackend, handle: int
) -> Optional[WindowInfo]:
    """Fresh snapshot for ``handle`` from a re-enumeration (None if gone)."""
    for info in backend.list_windows():
        if info.handle == handle:
            return info
    return None


def validate_target(
    backend: WindowBackend,
    expected: WindowInfo,
    *,
    require_foreground: bool = True,
    authorized: bool = True,
    operation: str = "",
    event_bus: Optional[EventBus] = None,
) -> WindowInfo:
    """Re-validate a previously resolved target before a sensitive op.

    Checks, in order: authorization, handle validity, process identity,
    application identity, foreground ownership (when required). Title
    drift is recorded in the returned snapshot but is not fatal: window
    titles legitimately change (document names, tab switches) while the
    underlying target stays the same.

    Returns the fresh live snapshot. Raises
    :class:`TargetValidationError` fail-closed on any mismatch.
    """
    if not isinstance(expected, WindowInfo):
        raise ValueError(
            "expected: expected WindowInfo, "
            f"got {type(expected).__name__}"
        )
    require_foreground = check_bool(
        require_foreground, field="require_foreground"
    )
    authorized = check_bool(authorized, field="authorized")
    if not isinstance(operation, str):
        raise ValueError(
            f"operation: expected string, got {type(operation).__name__}"
        )

    if not authorized:
        raise _fail(
            "unauthorized",
            "target validation failed: operation "
            f"{operation or '<unnamed>'} is not authorized",
            event_bus=event_bus,
            operation=operation,
            expected=expected,
        )

    try:
        valid = backend.is_valid(expected.handle)
    except Exception as exc:
        raise _fail(
            "backend_error",
            f"target validation failed: handle check failed: {exc}",
            event_bus=event_bus,
            operation=operation,
            expected=expected,
        ) from exc
    if not valid:
        raise _fail(
            "unknown_handle",
            "target validation failed: "
            f"handle {expected.handle} names no live window",
            event_bus=event_bus,
            operation=operation,
            expected=expected,
        )

    try:
        live = _live_snapshot(backend, expected.handle)
    except Exception as exc:
        raise _fail(
            "backend_error",
            f"target validation failed: re-enumeration failed: {exc}",
            event_bus=event_bus,
            operation=operation,
            expected=expected,
        ) from exc
    if live is None:
        raise _fail(
            "target_changed",
            "target validation failed: "
            f"handle {expected.handle} no longer enumerates",
            event_bus=event_bus,
            operation=operation,
            expected=expected,
        )
    if live.process_id != expected.process_id:
        raise _fail(
            "target_changed",
            "target validation failed: "
            f"handle {expected.handle} now belongs to process "
            f"{live.process_id} (expected {expected.process_id})",
            event_bus=event_bus,
            operation=operation,
            expected=expected,
        )
    if expected.executable and live.executable.lower() != (
        expected.executable.lower()
    ):
        raise _fail(
            "target_changed",
            "target validation failed: "
            f"handle {expected.handle} now belongs to "
            f"{live.executable or '<unknown app>'} "
            f"(expected {expected.executable})",
            event_bus=event_bus,
            operation=operation,
            expected=expected,
        )

    if require_foreground:
        try:
            current = backend.foreground()
        except Exception as exc:
            raise _fail(
                "backend_error",
                "target validation failed: "
                f"foreground window is unreadable: {exc}",
                event_bus=event_bus,
                operation=operation,
                expected=expected,
            ) from exc
        if current is None:
            raise _fail(
                "no_foreground",
                "target validation failed: "
                "no foreground window is readable; refusing blind input",
                event_bus=event_bus,
                operation=operation,
                expected=expected,
            )
        if current.handle != expected.handle:
            raise _fail(
                "foreground_mismatch",
                "target validation failed: "
                f"foreground is handle {current.handle} "
                f"({current.title or '<untitled>'}) but the validated "
                f"target is handle {expected.handle} "
                f"({expected.title or '<untitled>'})",
                event_bus=event_bus,
                operation=operation,
                expected=expected,
            )
    return live


# ---------------------------------------------------------------------------
# Companion focus guard
# ---------------------------------------------------------------------------


class FocusGuard:
    """Preserve companion focus, activate + verify target, restore after.

    Use as a context manager around one validated operation::

        with FocusGuard(backend, target.handle):
            ...type / click into the verified target...

    ``__enter__`` preserves the current foreground (companion) handle,
    requests target activation, and verifies actual foreground ownership
    (raising :class:`TargetValidationError` when Windows denies the
    switch). ``__exit__`` best-effort restores the preserved companion
    and never raises. Hiding or minimizing the companion is *not* a
    substitute: foreground verification stays mandatory.
    """

    def __init__(
        self,
        backend: WindowBackend,
        target_handle: int,
        *,
        preserve: bool = True,
        verify: bool = True,
        event_bus: Optional[EventBus] = None,
        operation: str = "",
    ) -> None:
        self._backend = backend
        self._target = check_int(
            target_handle, field="target_handle", minimum=1
        )
        self._preserve = check_bool(preserve, field="preserve")
        self._verify = check_bool(verify, field="verify")
        if not isinstance(operation, str):
            raise ValueError(
                f"operation: expected string, got {type(operation).__name__}"
            )
        self._event_bus = event_bus
        self._operation = operation
        self._companion: Optional[int] = None
        self._preserved = False
        self._activated = False
        self._verified = False

    @property
    def target_handle(self) -> int:
        return self._target

    @property
    def companion_handle(self) -> Optional[int]:
        """Preserved foreground handle (None until :meth:`preserve`)."""
        return self._companion

    @property
    def activated(self) -> bool:
        return self._activated

    @property
    def verified(self) -> bool:
        return self._verified

    def _emit_failed(self, reason: str, detail: str) -> None:
        if self._event_bus is None:
            return
        try:
            self._event_bus.emit(
                EventType.TARGET_VALIDATION_FAILED,
                _FOCUS_SOURCE,
                {
                    "reason": reason,
                    "operation": self._operation,
                    "target_handle": self._target,
                    "detail": detail,
                },
            )
        except Exception:
            pass

    def preserve(self) -> Optional[int]:
        """Record the current foreground handle (best-effort, never raises)."""
        if self._preserved:
            return self._companion
        self._preserved = True
        if not self._preserve:
            return None
        try:
            current = self._backend.foreground()
        except Exception:
            return None
        if current is not None and current.handle != self._target:
            self._companion = current.handle
        return self._companion

    def activate(self) -> bool:
        """Request target activation; raise when the OS refuses it."""
        self.preserve()
        try:
            accepted = self._backend.activate(self._target)
        except TargetValidationError:
            raise
        except Exception as exc:
            detail = f"target activation failed: {exc}"
            self._emit_failed("backend_error", detail)
            raise TargetValidationError(detail, reason="backend_error") from exc
        if not accepted:
            detail = (
                "target activation refused: Windows denied the foreground "
                f"switch to handle {self._target}"
            )
            self._emit_failed("activation_denied", detail)
            raise TargetValidationError(detail, reason="activation_denied")
        self._activated = True
        return True

    def verify(self) -> WindowInfo:
        """Verify the target actually owns the foreground (raise if not)."""
        try:
            current = self._backend.foreground()
        except Exception as exc:
            detail = f"foreground verification failed: {exc}"
            self._emit_failed("backend_error", detail)
            raise TargetValidationError(detail, reason="backend_error") from exc
        if current is None or current.handle != self._target:
            actual = "unreadable" if current is None else str(current.handle)
            detail = (
                "foreground verification failed: "
                f"target handle {self._target} does not own the "
                f"foreground (actual: {actual})"
            )
            self._emit_failed("foreground_mismatch", detail)
            raise TargetValidationError(
                detail, reason="foreground_mismatch"
            )
        self._verified = True
        return current

    def restore(self) -> bool:
        """Best-effort companion restore; never raises.

        Returns True when a restore was attempted (or nothing needed
        restoring), False only when there is no preserved companion.
        """
        if not self._preserved or self._companion is None:
            return False
        try:
            current = self._backend.foreground()
        except Exception:
            current = None
        if current is not None and current.handle == self._companion:
            return True
        try:
            if not self._backend.is_valid(self._companion):
                return True  # companion is gone; nothing to restore
        except Exception:
            return True
        try:
            self._backend.activate(self._companion)
        except Exception:
            pass
        return True

    def __enter__(self) -> "FocusGuard":
        self.activate()
        if self._verify:
            self.verify()
        return self

    def __exit__(self, *exc_info: object) -> None:
        try:
            self.restore()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# SendInput backends (controlled Windows input injection)
# ---------------------------------------------------------------------------

_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1

_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_ABSOLUTE = 0x8000

_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004

_SM_CXSCREEN = 0
_SM_CYSCREEN = 1


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUT_UNION)]


def _default_send(events: list[_INPUT]) -> int:
    """Inject ``events`` via Win32 SendInput; returns the accepted count."""
    if os.name != "nt":
        raise ActuatorError(
            "SendInput backends require Windows "
            "(use DefaultMouse/DefaultKeyboard elsewhere)"
        )
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    except Exception:
        raise ActuatorError(
            "SendInput backends require the Win32 user32 library"
        ) from None
    array = (_INPUT * len(events))(*events)
    sent = user32.SendInput(
        len(events), ctypes.byref(array), ctypes.sizeof(_INPUT)
    )
    return int(sent)


def _default_metrics() -> tuple[int, int]:
    if os.name != "nt":
        raise ActuatorError(
            "SendInput backends require Windows "
            "(use DefaultMouse/DefaultKeyboard elsewhere)"
        )
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    except Exception:
        raise ActuatorError(
            "SendInput backends require the Win32 user32 library"
        ) from None
    return (
        int(user32.GetSystemMetrics(_SM_CXSCREEN)),
        int(user32.GetSystemMetrics(_SM_CYSCREEN)),
    )


def _mouse_event(
    x_norm: int, y_norm: int, flags: int, *, data: int = 0
) -> _INPUT:
    event = _INPUT()
    event.type = _INPUT_MOUSE
    event.u.mi = _MOUSEINPUT(
        dx=x_norm,
        dy=y_norm,
        mouseData=data,
        dwFlags=flags,
        time=0,
        dwExtraInfo=0,
    )
    return event


def _key_event(scan: int, flags: int) -> _INPUT:
    event = _INPUT()
    event.type = _INPUT_KEYBOARD
    event.u.ki = _KEYBDINPUT(
        wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0
    )
    return event


def _utf16_units(text: str) -> list[int]:
    units: list[int] = []
    for char in text:
        code = ord(char)
        if code > 0xFFFF:
            code -= 0x10000
            units.append(0xD800 + (code >> 10))
            units.append(0xDC00 + (code & 0x3FF))
        else:
            units.append(code)
    return units


class SendInputMouse:
    """Live cursor backend over Win32 ``SendInput`` (Windows only).

    Implements the existing :class:`MouseBackend` protocol so the input
    implementation can change without touching the cognitive
    architecture. ``sender``/``metrics`` seams keep every branch
    headless-testable; production callers leave them at their defaults.
    """

    def __init__(
        self,
        sender: Optional[Callable[[list[_INPUT]], int]] = None,
        metrics: Optional[Callable[[], tuple[int, int]]] = None,
    ) -> None:
        self._sender = sender or _default_send
        self._metrics = metrics or _default_metrics

    def _inject(self, events: list[_INPUT], what: str) -> None:
        try:
            sent = self._sender(events)
        except ActuatorError:
            raise
        except Exception as exc:
            raise ActuatorError(
                f"SendInput mouse {what} failed: {exc}"
            ) from exc
        if sent != len(events):
            raise ActuatorError(
                f"SendInput mouse {what} failed: injected {sent} of "
                f"{len(events)} events (input may be blocked)"
            )

    def position(self) -> tuple[int, int]:
        if os.name != "nt":
            raise ActuatorError(
                "SendInput backends require Windows "
                "(use DefaultMouse/DefaultKeyboard elsewhere)"
            )
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        except Exception:
            raise ActuatorError(
                "SendInput backends require the Win32 user32 library"
            ) from None

        class _POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        point = _POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            raise ActuatorError("SendInput mouse position read failed")
        return (int(point.x), int(point.y))

    def _normalized(self, x: int, y: int) -> tuple[int, int]:
        width, height = self._metrics()
        if width < 2 or height < 2:
            raise ActuatorError(
                "SendInput mouse move failed: "
                f"unusable screen metrics {width}x{height}"
            )
        return (
            min(65535, max(0, round(x * 65535 / (width - 1)))),
            min(65535, max(0, round(y * 65535 / (height - 1)))),
        )

    def move_to(self, x: int, y: int) -> None:
        x = check_int(x, field="x", minimum=0)
        y = check_int(y, field="y", minimum=0)
        nx, ny = self._normalized(x, y)
        self._inject(
            [_mouse_event(nx, ny, _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE)],
            "move",
        )

    def click(self) -> None:
        self._inject(
            [
                _mouse_event(0, 0, _MOUSEEVENTF_LEFTDOWN),
                _mouse_event(0, 0, _MOUSEEVENTF_LEFTUP),
            ],
            "click",
        )


class SendInputKeyboard:
    """Live keystroke backend over Win32 ``SendInput`` (Windows only).

    Implements the existing :class:`KeyboardBackend` protocol using
    ``KEYEVENTF_UNICODE`` scan codes, so any BMP character (plus
    surrogate pairs above it) types without layout-dependent virtual
    keys.
    """

    def __init__(
        self,
        sender: Optional[Callable[[list[_INPUT]], int]] = None,
    ) -> None:
        self._sender = sender or _default_send

    def write_char(self, char: str) -> None:
        if not isinstance(char, str) or not char:
            raise ValueError("char: expected a non-empty string")
        events: list[_INPUT] = []
        for unit in _utf16_units(char):
            events.append(_key_event(unit, _KEYEVENTF_UNICODE))
            events.append(
                _key_event(unit, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP)
            )
        try:
            sent = self._sender(events)
        except ActuatorError:
            raise
        except Exception as exc:
            raise ActuatorError(
                f"SendInput keyboard write failed: {exc}"
            ) from exc
        if sent != len(events):
            raise ActuatorError(
                "SendInput keyboard write failed: injected "
                f"{sent} of {len(events)} events (input may be blocked)"
            )


# ---------------------------------------------------------------------------
# Completion verification
# ---------------------------------------------------------------------------


def verify_foreground(
    backend: WindowBackend,
    handle: int,
    *,
    event_bus: Optional[EventBus] = None,
    operation: str = "",
) -> WindowInfo:
    """Verify ``handle`` owns the foreground right now (raise if not)."""
    handle = check_int(handle, field="handle", minimum=1)
    if not isinstance(operation, str):
        raise ValueError(
            f"operation: expected string, got {type(operation).__name__}"
        )
    try:
        current = backend.foreground()
    except Exception as exc:
        detail = f"foreground verification failed: {exc}"
        _emit_validation_failed(
            event_bus,
            reason="backend_error",
            operation=operation,
            detail=detail,
        )
        raise TargetValidationError(detail, reason="backend_error") from exc
    if current is None or current.handle != handle:
        actual = "unreadable" if current is None else str(current.handle)
        detail = (
            f"foreground verification failed: handle {handle} does not "
            f"own the foreground (actual: {actual})"
        )
        _emit_validation_failed(
            event_bus,
            reason="foreground_mismatch",
            operation=operation,
            detail=detail,
        )
        raise TargetValidationError(detail, reason="foreground_mismatch")
    return current


def verify_cursor_at(
    mouse: Any,
    expected_x: int,
    expected_y: int,
    *,
    tolerance: int = 2,
    event_bus: Optional[EventBus] = None,
    operation: str = "",
) -> tuple[int, int]:
    """Verify the cursor reached (``expected_x``, ``expected_y``).

    Successful injection is not proof of execution: this reads the
    cursor back through ``mouse`` and raises :class:`ActuatorError`
    when it is further than ``tolerance`` pixels away. Returns the
    observed position.
    """
    expected_x = check_int(expected_x, field="expected_x", minimum=0)
    expected_y = check_int(expected_y, field="expected_y", minimum=0)
    tolerance = check_int(tolerance, field="tolerance", minimum=0)
    try:
        raw = mouse.position()
        actual = (int(raw[0]), int(raw[1]))
    except ActuatorError:
        raise
    except Exception as exc:
        raise ActuatorError(
            f"cursor verification failed: position read failed: {exc}"
        ) from exc
    distance = (
        (actual[0] - expected_x) ** 2 + (actual[1] - expected_y) ** 2
    ) ** 0.5
    if distance > tolerance:
        detail = (
            f"cursor verification failed: at {actual}, expected "
            f"({expected_x}, {expected_y}) within {tolerance}px"
        )
        if event_bus is not None:
            try:
                event_bus.emit(
                    EventType.ACTION_FAILED, _VERIFY_SOURCE,
                    {
                        "operation": operation,
                        "check": "cursor_position",
                        "detail": detail,
                    },
                )
            except Exception:
                pass
        raise ActuatorError(detail)
    return actual


def verify_completion(ok: bool, *, operation: str = "", detail: str = "") -> None:
    """Raise :class:`ActuatorError` unless ``ok`` confirms the outcome."""
    if not isinstance(operation, str):
        raise ValueError(
            f"operation: expected string, got {type(operation).__name__}"
        )
    if not isinstance(detail, str):
        raise ValueError(
            f"detail: expected string, got {type(detail).__name__}"
        )
    if ok is not True:
        raise ActuatorError(
            "completion verification failed for "
            f"{operation or '<unnamed operation>'}"
            + (f": {detail}" if detail else "")
            + " (input was injected but the outcome is unconfirmed)"
        )
