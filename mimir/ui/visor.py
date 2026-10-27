"""Expressive visor avatar: floating six-state desktop presence.

Days 8-9. :class:`VisorController` is a pure state machine (fully
headless-testable) mapping MIMIR's cognitive states to colors;
:func:`create_visor_widget` renders it as an always-on-top frameless
Qt pill. Qt (PyQt6 preferred, PySide6 fallback) imports lazily inside
the factory so the controller works anywhere.
"""

from __future__ import annotations

from enum import Enum
from threading import Lock
from typing import Any, Callable, Optional

__all__ = [
    "VISOR_COLORS",
    "VISOR_LABELS",
    "VisorController",
    "VisorError",
    "VisorState",
    "create_visor_widget",
    "run_visor",
]


class VisorError(Exception):
    """Base error for visor widget failures."""


class VisorState(str, Enum):
    """The six cognitive states of the desktop avatar."""

    RESTING = "resting"
    OBSERVING = "observing"
    LISTENING = "listening"
    THINKING = "thinking"
    DRIVING = "driving"
    ALERT = "alert"


VISOR_COLORS: dict[VisorState, str] = {
    VisorState.RESTING: "#555555",
    VisorState.OBSERVING: "#3B82F6",
    VisorState.LISTENING: "#10B981",
    VisorState.THINKING: "#8B5CF6",
    VisorState.DRIVING: "#F59E0B",
    VisorState.ALERT: "#EF4444",
}

VISOR_LABELS: dict[VisorState, str] = {
    VisorState.RESTING: "Resting",
    VisorState.OBSERVING: "Observing",
    VisorState.LISTENING: "Listening",
    VisorState.THINKING: "Thinking",
    VisorState.DRIVING: "Driving",
    VisorState.ALERT: "Alert",
}


def _coerce_state(state: VisorState | str) -> VisorState:
    if isinstance(state, VisorState):
        return state
    if isinstance(state, str):
        try:
            return VisorState(state.strip().lower())
        except ValueError:
            pass
    valid = sorted(s.value for s in VisorState)
    raise ValueError(f"state: expected one of {valid}, got {state!r}")


class VisorController:
    """Thread-safe visor state machine with change notifications."""

    def __init__(
        self,
        initial: VisorState | str = VisorState.RESTING,
        on_change: Optional[Callable[[VisorState, VisorState], None]] = None,
    ) -> None:
        self._state = _coerce_state(initial)
        if on_change is not None and not callable(on_change):
            raise ValueError("on_change: expected a callable")
        self._on_change = on_change
        self._lock = Lock()

    @property
    def state(self) -> VisorState:
        return self._state

    @property
    def color(self) -> str:
        return VISOR_COLORS[self._state]

    @property
    def label(self) -> str:
        return VISOR_LABELS[self._state]

    def set_state(self, state: VisorState | str) -> None:
        """Transition; notifies once, skipping no-op assignments."""
        target = _coerce_state(state)
        with self._lock:
            if target == self._state:
                return
            old, self._state = self._state, target
            callback = self._on_change
        if callback is not None:
            try:
                callback(old, target)
            except Exception:
                pass  # a UI hook must never break the state machine

    def toggle_rest(self) -> VisorState:
        """Hotkey behavior: RESTING <-> OBSERVING, else -> RESTING."""
        with self._lock:
            current = self._state
        target = (
            VisorState.OBSERVING
            if current == VisorState.RESTING
            else VisorState.RESTING
        )
        self.set_state(target)
        return target


def _load_qt() -> Any:
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets

        return ("PyQt6", QtCore, QtGui, QtWidgets)
    except ImportError:
        pass
    try:
        from PySide6 import QtCore, QtGui, QtWidgets

        return ("PySide6", QtCore, QtGui, QtWidgets)
    except ImportError:
        pass
    raise VisorError(
        "PyQt6 (or PySide6) is required for the visor widget "
        "(pip install PyQt6)"
    )


def create_visor_widget(
    controller: VisorController, *, hotkey: str = "ctrl+shift+m"
) -> Any:
    """Build the frameless always-on-top pill bound to ``controller``.

    The widget is returned unshown; the caller runs the Qt event loop and
    calls ``show()``. State repaints marshal onto the GUI thread via a
    signal, so worker threads may call ``controller.set_state`` freely.
    The hotkey toggles rest mode while the pill has focus (a global hook
    needs pynput; see :class:`mimir.actuator.DefaultFailsafe`).
    """
    if not isinstance(controller, VisorController):
        raise ValueError(
            "controller: expected VisorController, "
            f"got {type(controller).__name__}"
        )
    if not isinstance(hotkey, str) or not hotkey.strip():
        raise ValueError("hotkey: expected a non-empty key sequence")
    _, QtCore, QtGui, QtWidgets = _load_qt()

    if hasattr(QtCore, "pyqtSignal"):
        Signal = QtCore.pyqtSignal
    else:  # PySide6
        Signal = QtCore.Signal

    class VisorPill(QtWidgets.QWidget):
        _repaint = Signal(str)

        def __init__(self) -> None:
            super().__init__(
                None,
                QtCore.Qt.WindowType.WindowStaysOnTopHint
                | QtCore.Qt.WindowType.FramelessWindowHint
                | QtCore.Qt.WindowType.Tool,
            )
            self._controller = controller
            self._drag_offset = None
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setFixedSize(148, 64)
            self.setWindowTitle("MIMIR Visor")
            self._dot = QtWidgets.QLabel(self)
            self._dot.setFixedSize(44, 44)
            self._dot.move(10, 10)
            self._label = QtWidgets.QLabel(self)
            self._label.setGeometry(62, 10, 80, 44)
            font = QtGui.QFont("Segoe UI", 9)
            self._label.setFont(font)
            self._repaint.connect(self._apply_color)
            self._apply_color(controller.color)
            self._refresh_tooltip(controller.state)
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(hotkey), self)
            shortcut.setContext(
                QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut
            )
            shortcut.activated.connect(controller.toggle_rest)

        def _apply_color(self, color: str) -> None:
            self._dot.setStyleSheet(
                f"background-color: {color}; border-radius: 22px;"
            )
            self._label.setText(VISOR_LABELS[self._controller.state])
            self._refresh_tooltip(self._controller.state)

        def _refresh_tooltip(self, state: VisorState) -> None:
            self.setToolTip(f"MIMIR: {VISOR_LABELS[state]}")

        def mousePressEvent(self, event: object) -> None:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()  # type: ignore[union-attr]

        def mouseMoveEvent(self, event: object) -> None:
            if self._drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self._drag_offset)  # type: ignore[union-attr]

        def mouseReleaseEvent(self, event: object) -> None:
            self._drag_offset = None

    pill = VisorPill()

    def forward_change(old: VisorState, new: VisorState) -> None:
        pill._repaint.emit(VISOR_COLORS[new])

    # Chain with any pre-existing listener instead of replacing it.
    previous = controller._on_change

    def chained(old: VisorState, new: VisorState) -> None:
        forward_change(old, new)
        if previous is not None:
            previous(old, new)

    controller._on_change = chained
    forward_change(controller.state, controller.state)
    return pill


def run_visor(controller: VisorController) -> tuple[Any, Any]:
    """Create (or reuse) the Qt app, build the pill, and show it.

    Returns ``(app, pill)``; the caller drives ``app.exec()``.
    Raises :class:`VisorError` when no Qt binding is installed.
    """
    _, _, _, QtWidgets = _load_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    pill = create_visor_widget(controller)
    pill.show()
    return (app, pill)
