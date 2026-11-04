"""Expressive visor avatar: floating six-state desktop presence.

Days 8-9. :class:`VisorController` is a pure state machine (fully
headless-testable) mapping MIMIR's cognitive states to colors;
:func:`create_visor_widget` renders it as an always-on-top frameless
Qt pill. Qt (PyQt6 preferred, PySide6 fallback) imports lazily inside
the factory so the controller works anywhere.

Phase 5 drives the same machine through the five-state voice model
(RESTING / LISTENING / THINKING / ACTING / HALTED): ACTING and HALTED
are aliases for the live DRIVING and ALERT faces (see
:data:`PHASE5_TO_VISOR`, :func:`phase5_of`, :func:`set_phase5_state`).
"""

from __future__ import annotations

import math
import random
from enum import Enum
from threading import Lock
from typing import Any, Callable, Optional

__all__ = [
    "PHASE5_LABELS",
    "PHASE5_STATES",
    "PHASE5_TO_VISOR",
    "VISOR_COLORS",
    "VISOR_LABELS",
    "VISOR_TO_PHASE5",
    "VisorController",
    "VisorError",
    "VisorState",
    "create_visor_widget",
    "phase5_of",
    "run_visor",
    "set_phase5_state",
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

#: Phase-5 five-state voice/visor model. The live six-state machine
#: stays the source of truth; these names map onto it so voice hosts
#: can drive RESTING / LISTENING / THINKING / ACTING / HALTED without
#: learning the legacy DRIVING / ALERT spellings.
PHASE5_STATES: tuple[str, ...] = (
    "resting",
    "listening",
    "thinking",
    "acting",
    "halted",
)

#: Phase-5 name -> live visor value. ACTING is the live DRIVING face
#: (amber, executing) and HALTED is the live ALERT face (red,
#: E-stopped); the other three are identity mappings.
PHASE5_TO_VISOR: dict[str, str] = {
    "resting": "resting",
    "listening": "listening",
    "thinking": "thinking",
    "acting": "driving",
    "halted": "alert",
}

#: Live visor value -> Phase-5 name. OBSERVING (idle watch) reads as
#: Phase-5 RESTING; DRIVING reads as ACTING and ALERT as HALTED.
VISOR_TO_PHASE5: dict[str, str] = {
    "resting": "resting",
    "observing": "resting",
    "listening": "listening",
    "thinking": "thinking",
    "driving": "acting",
    "alert": "halted",
}

#: Human labels for the Phase-5 five-state model.
PHASE5_LABELS: dict[str, str] = {
    "resting": "Resting",
    "listening": "Listening",
    "thinking": "Thinking",
    "acting": "Acting",
    "halted": "Halted",
}


def _coerce_state(state: VisorState | str) -> VisorState:
    if isinstance(state, VisorState):
        return state
    if isinstance(state, str):
        cleaned = state.strip().lower()
        # Phase-5 aliases: ACTING -> DRIVING, HALTED -> ALERT.
        mapped = PHASE5_TO_VISOR.get(cleaned, cleaned)
        try:
            return VisorState(mapped)
        except ValueError:
            pass
    valid = sorted(s.value for s in VisorState)
    raise ValueError(f"state: expected one of {valid}, got {state!r}")


def phase5_of(state: VisorState | str) -> str:
    """Phase-5 name for a live visor state (``acting``/``halted``...).

    Accepts a :class:`VisorState` or any string :func:`set_state`
    accepts (including the Phase-5 aliases themselves), so hosts can
    normalize either spelling to the five-state model. OBSERVING
    reads as ``resting`` (idle).
    """
    resolved = _coerce_state(state)
    return VISOR_TO_PHASE5[resolved.value]


def set_phase5_state(controller: Any, state: str) -> str:
    """Drive a visor controller with a Phase-5 state name.

    ``controller`` is duck-typed (only ``set_state`` is used) so the
    helper stays Qt-free and headless-testable. Returns the live
    visor value applied (``acting`` -> ``driving``, ...). Raises
    :class:`ValueError` for unknown Phase-5 names.
    """
    if not isinstance(state, str):
        raise ValueError(
            "state: expected one of "
            f"{sorted(PHASE5_STATES)}, got {state!r}"
        )
    cleaned = state.strip().lower()
    if cleaned not in PHASE5_TO_VISOR:
        raise ValueError(
            "state: expected one of "
            f"{sorted(PHASE5_STATES)}, got {state!r}"
        )
    target = PHASE5_TO_VISOR[cleaned]
    setter = getattr(controller, "set_state", None)
    if not callable(setter):
        raise ValueError(
            "controller: expected an object with set_state(), "
            f"got {type(controller).__name__}"
        )
    setter(target)
    return target


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

    @property
    def phase5(self) -> str:
        """This controller's state in the Phase-5 five-state model."""
        return VISOR_TO_PHASE5[self._state.value]

    @property
    def phase5_label(self) -> str:
        """Human label for :attr:`phase5` (Acting/Halted/...)."""
        return PHASE5_LABELS[VISOR_TO_PHASE5[self._state.value]]

    def set_phase5(self, state: str) -> str:
        """Transition via a Phase-5 name; returns the live value."""
        return set_phase5_state(self, state)

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
        import importlib
        pyside6 = importlib.import_module("PySide6")
        QtCore = getattr(pyside6, "QtCore")
        QtGui = getattr(pyside6, "QtGui")
        QtWidgets = getattr(pyside6, "QtWidgets")

        return ("PySide6", QtCore, QtGui, QtWidgets)
    except (ImportError, AttributeError):
        pass
    raise VisorError(
        "PyQt6 (or PySide6) is required for the visor widget "
        "(pip install PyQt6)"
    )


def create_visor_widget(
    controller: VisorController,
    *,
    hotkey: str = "ctrl+shift+m",
    auto_roam: bool = False,
    follow_mouse: bool = False,
) -> Any:
    """Build the frameless always-on-top pill bound to ``controller``.

    The pill renders the personified co-bot face (white oval with
    expressive eyes + smile in the state color), matching the desktop
    app avatar. It gently bobs in place, drags from anywhere, and can
    move itself: ``pill.glide_to(x, y)`` sends it gliding to a screen
    point, while ``pill.set_auto_roam(True)`` permits autonomous
    wandering, and ``pill.set_follow_mouse(True)`` allows it to smoothly
    glide across the window near the user's cursor to observe active work.
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
            self._color = controller.color
            self._state = controller.state
            self._blink = False
            self._is_hovered = False
            self._drag_offset = None
            self._dragging = False
            self._anchor = None
            self._move_target = None
            self._auto_roam = bool(auto_roam)
            self._follow_mouse = bool(follow_mouse)
            self._is_active_tracking = False
            self._float_phase = 0.0
            self._follow_offset_x = 75
            self._follow_offset_y = -45
            self._drag_start_pos = None
            self._has_dragged = False
            self._in_motion_tick = False
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setFixedSize(96, 96)
            self.setWindowTitle("MIMIR Visor")
            # Legacy dot/label kept as hidden shims: external readers and
            # the original widget test observe the spec color through them
            # while the pill itself paints the personified face.
            self._dot = QtWidgets.QLabel(self)
            self._dot.setFixedSize(44, 44)
            self._dot.move(10, 10)
            self._dot.hide()
            self._label = QtWidgets.QLabel(self)
            self._label.setGeometry(62, 10, 80, 44)
            self._label.hide()
            self._repaint.connect(self._apply_color)
            self._apply_color(controller.color)
            self._refresh_tooltip(controller.state)
            self._timer = QtCore.QTimer(self)
            self._timer.timeout.connect(self._toggle_blink)
            self._timer.start(3400)
            self._motion_timer = QtCore.QTimer(self)
            self._motion_timer.timeout.connect(self._tick_motion)
            self._motion_timer.start(30)
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(hotkey), self)
            shortcut.setContext(
                QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut
            )
            shortcut.activated.connect(controller.toggle_rest)

        def closeEvent(self, event: Any) -> None:
            try:
                self._timer.stop()
                self._motion_timer.stop()
            except Exception:
                pass
            try:
                if getattr(self, "_controller", None) is not None:
                    if getattr(self._controller, "_on_change", None) is getattr(self, "_chained_fn", None):
                        self._controller._on_change = getattr(self, "_prev_on_change", None)
            except Exception:
                pass
            super().closeEvent(event)

        def _apply_color(self, color: str) -> None:
            self._color = color
            self._state = self._controller.state
            try:
                self._dot.setStyleSheet(
                    f"background-color: {color}; border-radius: 22px;"
                )
                self._label.setText(VISOR_LABELS[self._controller.state])
                self._refresh_tooltip(self._controller.state)
                self.update()
            except (RuntimeError, AttributeError):
                pass


        def _refresh_tooltip(self, state: VisorState) -> None:
            try:
                note = getattr(self, "_activity_note", "") or ""
                suffix = f" — {note}" if note else ""
                self.setToolTip(f"Habi: {VISOR_LABELS[state]}{suffix}")
            except (RuntimeError, AttributeError):
                pass

        def set_activity_note(self, note: str) -> None:
            """Show a one-line activity read in the tooltip ("Notices").

            Called while Habi wanders so roaming narrates the task it
            sees; an empty note clears the suffix.
            """
            try:
                self._activity_note = str(note or "").strip()[:120]
            except Exception:
                self._activity_note = ""
            self._refresh_tooltip(self._controller.state)

        def _toggle_blink(self) -> None:
            if not self._is_hovered and self.isVisible():
                self._blink = True
                try:
                    self.update()
                    QtCore.QTimer.singleShot(160, self._unblink)
                except (RuntimeError, AttributeError):
                    pass

        def _unblink(self) -> None:
            self._blink = False
            try:
                if self.isVisible():
                    self.update()
            except (RuntimeError, AttributeError):
                pass

        def moveEvent(self, event: object) -> None:
            if not getattr(self, "_in_motion_tick", False) and not getattr(self, "_dragging", False):
                self._anchor = self.pos()

        def enterEvent(self, event: object) -> None:
            self._is_hovered = True
            if not self._dragging:
                self.setCursor(
                    QtGui.QCursor(QtCore.Qt.CursorShape.OpenHandCursor))
            self.update()

        def leaveEvent(self, event: object) -> None:
            self._is_hovered = False
            if not self._dragging:
                self.unsetCursor()
            self.update()

        def mousePressEvent(self, event: object) -> None:
            btn = getattr(event, "button", lambda: QtCore.Qt.MouseButton.LeftButton)()
            if btn == QtCore.Qt.MouseButton.RightButton:
                self._show_context_menu(event)
                return
            if btn == QtCore.Qt.MouseButton.LeftButton:
                self._dragging = True
                self._has_dragged = False
                self._drag_start_pos = self.pos()
                try:
                    self._drag_offset = event.globalPosition().toPoint() - self.pos()
                except AttributeError:
                    self._drag_offset = event.globalPos() - self.pos()
                self.setCursor(
                    QtGui.QCursor(QtCore.Qt.CursorShape.ClosedHandCursor))

        def mouseMoveEvent(self, event: object) -> None:
            if self._dragging and self._drag_offset is not None:
                try:
                    cur_pt = event.globalPosition().toPoint()
                except AttributeError:
                    cur_pt = event.globalPos()
                new_pos = cur_pt - self._drag_offset
                if self._drag_start_pos is not None and (new_pos - self._drag_start_pos).manhattanLength() > 4:
                    self._has_dragged = True
                self.move(new_pos)

        def mouseReleaseEvent(self, event: object) -> None:
            btn = getattr(event, "button", lambda: QtCore.Qt.MouseButton.LeftButton)()
            if btn == QtCore.Qt.MouseButton.LeftButton:
                was_drag = self._has_dragged
                self._dragging = False
                self._drag_offset = None
                self._anchor = self.pos()
                self.setCursor(
                    QtGui.QCursor(QtCore.Qt.CursorShape.OpenHandCursor))
                if was_drag:
                    try:
                        cpos = QtGui.QCursor.pos()
                        self._follow_offset_x = max(-350, min(350, self.pos().x() - cpos.x()))
                        self._follow_offset_y = max(-350, min(350, self.pos().y() - cpos.y()))
                    except Exception:
                        pass
                else:
                    # Single click: bring main desktop companion window to front
                    app = QtWidgets.QApplication.instance()
                    if app is not None:
                        for w in app.topLevelWidgets():
                            if w is not self and isinstance(w, QtWidgets.QMainWindow):
                                w.show()
                                w.raise_()
                                w.activateWindow()
                                break

        def _show_context_menu(self, event: object) -> None:
            menu = QtWidgets.QMenu(self)
            menu.setStyleSheet("""
                QMenu {
                    background-color: #0F172A;
                    color: #F8FAFC;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 8px;
                    padding: 6px;
                    font-family: 'Segoe UI', system-ui, sans-serif;
                    font-size: 12px;
                }
                QMenu::item {
                    padding: 6px 20px 6px 12px;
                    border-radius: 4px;
                }
                QMenu::item:selected {
                    background-color: #2563EB;
                    color: #FFFFFF;
                }
                QMenu::separator {
                    height: 1px;
                    background: rgba(255, 255, 255, 0.1);
                    margin: 4px 6px;
                }
            """)
            act_follow = menu.addAction("👁  Follow Mouse Cursor")
            act_follow.setCheckable(True)
            act_follow.setChecked(self._follow_mouse)

            act_roam = menu.addAction("🚶  Autonomous Roam")
            act_roam.setCheckable(True)
            act_roam.setChecked(self._auto_roam)

            menu.addSeparator()
            act_open = menu.addAction("✦  Open MIMIR Desktop")
            act_hide = menu.addAction("✕  Hide Habi")

            try:
                pt = event.globalPosition().toPoint()
            except AttributeError:
                pt = event.globalPos()
            chosen = menu.exec(pt)
            if chosen == act_follow:
                self.set_follow_mouse(not self._follow_mouse)
            elif chosen == act_roam:
                self.set_auto_roam(not self._auto_roam)
            elif chosen == act_open:
                app = QtWidgets.QApplication.instance()
                if app is not None:
                    for w in app.topLevelWidgets():
                        if w is not self and isinstance(w, QtWidgets.QMainWindow):
                            w.show()
                            w.raise_()
                            w.activateWindow()
                            break
            elif chosen == act_hide:
                self.hide()

        def set_auto_roam(self, enabled: bool) -> None:
            """Permit (or forbid) autonomous wandering across the screen."""
            self._auto_roam = bool(enabled)

        def set_follow_mouse(self, enabled: bool) -> None:
            """Permit (or forbid) following the mouse to observe user work."""
            self._follow_mouse = bool(enabled)
            if not enabled:
                self._is_active_tracking = False
                self.update()
            self._refresh_tooltip(self._controller.state)

        def set_follow_mouse(self, enabled: bool) -> None:
            """Toggle whether Habi smoothly tracks and follows the mouse cursor."""
            self._follow_mouse = bool(enabled)
            if self._follow_mouse:
                self._move_target = None
            try:
                self.update()
            except Exception:
                pass

        def set_auto_roam(self, enabled: bool) -> None:
            """Toggle whether Habi wanders autonomously across the screen."""
            self._auto_roam = bool(enabled)
            if not self._auto_roam:
                self._move_target = None
            else:
                self._move_target = self._pick_roam_target()
            try:
                self.update()
            except Exception:
                pass

        def glide_to(self, x: int, y: int) -> None:
            """Glide smoothly to a screen point, then stop there."""
            self._move_target = QtCore.QPoint(int(x), int(y))

        @staticmethod
        def _bob_dy(phase: float) -> int:
            return int(round(4.0 * math.sin(phase)))

        def _step_toward(self, current, target, step: int):
            dx = target.x() - current.x()
            dy = target.y() - current.y()
            return QtCore.QPoint(
                current.x() + max(-step, min(step, dx)),
                current.y() + max(-step, min(step, dy)),
            )

        def _pick_roam_target(self):
            try:
                screen = QtGui.QGuiApplication.primaryScreen()
            except Exception:
                return None
            if screen is None:
                return None
            avail = screen.availableGeometry()
            lo_x, lo_y = avail.x() + 8, avail.y() + 8
            hi_x = max(lo_x, avail.x() + avail.width() - self.width() - 8)
            hi_y = max(lo_y, avail.y() + avail.height() - self.height() - 8)
            return QtCore.QPoint(random.randint(lo_x, hi_x),
                                 random.randint(lo_y, hi_y))

        def _tick_motion(self) -> None:
            if self._anchor is None:
                self._anchor = self.pos()
            if self._dragging:
                return
            self._float_phase += 0.08

            if self._follow_mouse:
                try:
                    cpos = QtGui.QCursor.pos()
                    tx = cpos.x() + getattr(self, "_follow_offset_x", 75)
                    ty = cpos.y() + getattr(self, "_follow_offset_y", -45)
                    screen = QtGui.QGuiApplication.screenAt(cpos) or QtGui.QGuiApplication.primaryScreen()
                    if screen is not None:
                        geo = screen.availableGeometry()
                        if tx + self.width() > geo.x() + geo.width() - 8:
                            tx = cpos.x() - self.width() - 20
                        if ty < geo.y() + 8:
                            ty = cpos.y() + 35
                        tx = max(geo.x() + 8, min(tx, geo.x() + geo.width() - self.width() - 8))
                        ty = max(geo.y() + 8, min(ty, geo.y() + geo.height() - self.height() - 8))

                    dx = tx - self._anchor.x()
                    dy = ty - self._anchor.y()
                    dist = math.hypot(dx, dy)
                    if dist > 8:
                        speed = max(4, min(50, int(dist * 0.22)))
                        self._anchor = self._step_toward(self._anchor, QtCore.QPoint(tx, ty), speed)
                        if not self._is_active_tracking:
                            self._is_active_tracking = True
                            self.update()
                    else:
                        if self._is_active_tracking:
                            self._is_active_tracking = False
                            self.update()
                except Exception:
                    pass
            elif self._auto_roam and (self._move_target is None
                                     or self._anchor == self._move_target):
                self._move_target = self._pick_roam_target()

            if self._move_target is not None:
                self._anchor = self._step_toward(self._anchor,
                                                self._move_target, 4)
                if self._anchor == self._move_target and not self._auto_roam:
                    self._move_target = None

            self._in_motion_tick = True
            try:
                self.move(self._anchor.x(),
                          self._anchor.y()
                          + self._bob_dy(self._float_phase))
            finally:
                self._in_motion_tick = False

        def paintEvent(self, event: object) -> None:
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

            w = float(self.width())
            h = float(self.height())
            cx = w / 2.0
            cy = h / 2.0
            scale = min(w / 80.0, h / 52.0)

            # 3D Pebble dimensions (horizontal rounded pebble matching reference render)
            head_rx = 40.0
            head_ry = 38.0

            bob = float(self._bob_dy(self._float_phase))

            # 1. Soft 3D Ground Drop Shadow beneath the pebble
            shadow_y = cy + head_ry * 0.95
            shadow_rx = max(6.0, head_rx * 0.78 - (bob * 0.6))
            shadow_ry = max(1.5, head_ry * 0.22 - (bob * 0.25))
            s_grad = QtGui.QRadialGradient(cx, shadow_y, shadow_rx)
            s_grad.setColorAt(0.0, QtGui.QColor(15, 23, 42, 55))
            s_grad.setColorAt(0.65, QtGui.QColor(15, 23, 42, 18))
            s_grad.setColorAt(1.0, QtGui.QColor(15, 23, 42, 0))
            painter.setBrush(QtGui.QBrush(s_grad))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawEllipse(QtCore.QPointF(cx, shadow_y), shadow_rx, shadow_ry)

            # State color selection
            stroke_color = "#38BDF8" if (self._is_active_tracking or self._state in (VisorState.OBSERVING, VisorState.RESTING)) else self._color

            # 2. Side Glowing Ear Light Slits
            slit_w = max(2.8 * scale, 2.2)
            slit_h = head_ry * 0.75
            slit_y = cy - slit_h / 2.0
            for side in (-1, 1):
                slit_x = cx + side * (head_rx * 0.96) - (slit_w if side == 1 else 0.0)
                slit_rect = QtCore.QRectF(slit_x, slit_y, slit_w, slit_h)
                halo_col = QtGui.QColor(stroke_color)
                halo_col.setAlpha(60)
                painter.setPen(QtGui.QPen(halo_col, 3.5 * scale))
                painter.setBrush(QtGui.QBrush(QtGui.QColor(stroke_color)))
                painter.drawRoundedRect(slit_rect, slit_w / 2.0, slit_w / 2.0)

            # 3. Outer White 3D Ceramic/Porcelain Pebble Chassis
            chassis_grad = QtGui.QRadialGradient(cx - head_rx * 0.35, cy - head_ry * 0.40, head_rx * 1.5)
            chassis_grad.setColorAt(0.0, QtGui.QColor("#FFFFFF"))
            chassis_grad.setColorAt(0.45, QtGui.QColor("#FFFFFF"))
            chassis_grad.setColorAt(0.80, QtGui.QColor("#F1F5F9"))
            chassis_grad.setColorAt(0.94, QtGui.QColor("#E2E8F0"))
            chassis_grad.setColorAt(1.0, QtGui.QColor("#CBD5E1"))
            painter.setBrush(QtGui.QBrush(chassis_grad))
            painter.setPen(QtGui.QPen(QtGui.QColor("#CBD5E1"), max(1.2 * scale, 1.0)))
            painter.drawEllipse(QtCore.QPointF(cx, cy), head_rx, head_ry)

            # Subtle ambient rim light bounce at the bottom of the white pebble
            bounce_grad = QtGui.QRadialGradient(cx, cy + head_ry * 0.82, head_rx * 0.8)
            bounce_grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, 140))
            bounce_grad.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
            painter.setBrush(QtGui.QBrush(bounce_grad))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawEllipse(QtCore.QPointF(cx, cy), head_rx, head_ry)

            # 4. Inset Jet-Black Glossy Screen (Recessed Visor Glass from Image)
            screen_w = 42.0
            screen_h = 32.0
            screen_r = screen_h * 0.44
            screen_rect = QtCore.QRectF(cx - screen_w / 2.0, cy - screen_h / 2.0 + 0.5 * scale, screen_w, screen_h)

            painter.setPen(QtGui.QPen(QtGui.QColor("#94A3B8"), 1.2 * scale))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(screen_rect, screen_r, screen_r)

            screen_grad = QtGui.QRadialGradient(cx, cy - screen_h * 0.15, screen_w * 0.65)
            screen_grad.setColorAt(0.0, QtGui.QColor("#0F172A"))
            screen_grad.setColorAt(0.55, QtGui.QColor("#080D1A"))
            screen_grad.setColorAt(1.0, QtGui.QColor("#030712"))
            painter.setBrush(QtGui.QBrush(screen_grad))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawRoundedRect(screen_rect, screen_r, screen_r)

            # 5. Top Specular Glass Reflection (Curved Glossy Screen Highlight)
            glass_path = QtGui.QPainterPath()
            glass_h = screen_h * 0.38
            glass_rect = QtCore.QRectF(cx - screen_w * 0.44, cy - screen_h * 0.46, screen_w * 0.88, glass_h)
            glass_path.addRoundedRect(glass_rect, screen_r * 0.55, screen_r * 0.55)
            glass_grad = QtGui.QLinearGradient(0, cy - screen_h * 0.46, 0, cy - screen_h * 0.46 + glass_h)
            glass_grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, 55))
            glass_grad.setColorAt(0.7, QtGui.QColor(255, 255, 255, 12))
            glass_grad.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
            painter.setBrush(QtGui.QBrush(glass_grad))
            painter.drawPath(glass_path)

            # 6. Glowing Expressive Eyes Inside Black Glass
            eye_spacing = screen_w * 0.245
            eye_y = cy + 1.2 * scale
            eye_r = screen_h * 0.26
            neon = QtGui.QColor(stroke_color)

            # Interactive gaze calculation
            try:
                cpos = QtGui.QCursor.pos()
                cobot_center = self.mapToGlobal(QtCore.QPoint(int(cx), int(cy)))
                gaze_dx = cpos.x() - cobot_center.x()
                gaze_dy = cpos.y() - cobot_center.y()
                gaze_dist = math.hypot(gaze_dx, gaze_dy)
                if gaze_dist > 2.0:
                    look_x = max(-2.4, min(2.4, (gaze_dx / gaze_dist) * 2.4))
                    look_y = max(-2.4, min(2.4, (gaze_dy / gaze_dist) * 2.4))
                else:
                    look_x, look_y = 0.0, 0.0
            except Exception:
                look_x, look_y = 0.0, 0.0

            is_sleeping = (self._state == VisorState.RESTING and not self._is_active_tracking)
            is_blinking = (self._blink and not self._is_hovered)

            if is_sleeping or is_blinking:
                # RIGHT IMAGE IN USER PROMPT: Downward gently curved glowing arcs!
                arc_w = eye_r * 0.95
                arc_h = eye_r * 0.48

                bloom_pen = QtGui.QPen(neon, max(5.0 * scale, 3.8))
                bloom_col = QtGui.QColor(neon)
                bloom_col.setAlpha(65)
                bloom_pen.setColor(bloom_col)
                bloom_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)

                core_pen = QtGui.QPen(neon, max(3.0 * scale, 2.2))
                core_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)

                for ecx in (cx - eye_spacing, cx + eye_spacing):
                    arc_path = QtGui.QPainterPath()
                    arc_path.moveTo(ecx - arc_w, eye_y - arc_h * 0.2)
                    arc_path.quadTo(ecx, eye_y + arc_h, ecx + arc_w, eye_y - arc_h * 0.2)

                    painter.setPen(bloom_pen)
                    painter.strokePath(arc_path, bloom_pen)
                    painter.setPen(core_pen)
                    painter.strokePath(arc_path, core_pen)

            elif self._is_hovered:
                # Cute happy smiling eyes (^ ^)
                arc_w = eye_r * 0.90
                arc_h = eye_r * 0.55

                bloom_pen = QtGui.QPen(neon, max(5.0 * scale, 3.8))
                bloom_col = QtGui.QColor(neon)
                bloom_col.setAlpha(65)
                bloom_pen.setColor(bloom_col)
                bloom_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)

                core_pen = QtGui.QPen(neon, max(3.0 * scale, 2.2))
                core_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)

                for ecx in (cx - eye_spacing, cx + eye_spacing):
                    arc_path = QtGui.QPainterPath()
                    arc_path.moveTo(ecx - arc_w, eye_y + arc_h * 0.3)
                    arc_path.quadTo(ecx, eye_y - arc_h * 0.8, ecx + arc_w, eye_y + arc_h * 0.3)

                    painter.setPen(bloom_pen)
                    painter.strokePath(arc_path, bloom_pen)
                    painter.setPen(core_pen)
                    painter.strokePath(arc_path, core_pen)

            elif self._state == VisorState.ALERT:
                # Alert state: focused warning eyes
                eye_w, eye_h = eye_r * 1.5, eye_r * 1.9
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                halo_col = QtGui.QColor(neon)
                halo_col.setAlpha(60)
                painter.setBrush(QtGui.QBrush(halo_col))
                for ecx in (cx - eye_spacing, cx + eye_spacing):
                    painter.drawRoundedRect(
                        QtCore.QRectF(ecx - eye_w / 2.0 - 2, eye_y - eye_h / 2.0 - 2, eye_w + 4, eye_h + 4),
                        eye_w / 2.0, eye_w / 2.0)
                painter.setBrush(QtGui.QBrush(neon))
                for ecx in (cx - eye_spacing, cx + eye_spacing):
                    painter.drawRoundedRect(
                        QtCore.QRectF(ecx - eye_w / 2.0, eye_y - eye_h / 2.0, eye_w, eye_h),
                        eye_w / 2.0, eye_w / 2.0)
                painter.setBrush(QtGui.QBrush(QtGui.QColor("#030712")))
                for ecx in (cx - eye_spacing, cx + eye_spacing):
                    painter.drawEllipse(QtCore.QPointF(ecx, eye_y + 1.0), eye_r * 0.5, eye_r * 0.5)

            elif self._state == VisorState.THINKING:
                # Thinking state: asymmetric wondering gaze
                for idx, ecx in enumerate((cx - eye_spacing, cx + eye_spacing)):
                    er = eye_r if idx == 0 else eye_r * 0.75
                    ey = eye_y - 1.5 if idx == 0 else eye_y + 1.5
                    halo_col = QtGui.QColor(neon)
                    halo_col.setAlpha(55)
                    painter.setPen(QtCore.Qt.PenStyle.NoPen)
                    painter.setBrush(QtGui.QBrush(halo_col))
                    painter.drawEllipse(QtCore.QPointF(ecx, ey), er + 2.5 * scale, er + 2.5 * scale)

                    painter.setBrush(QtGui.QBrush(neon))
                    painter.drawEllipse(QtCore.QPointF(ecx, ey), er, er)

                    pupil_r = er * 0.72
                    painter.setBrush(QtGui.QBrush(QtGui.QColor("#030712")))
                    painter.drawEllipse(QtCore.QPointF(ecx + (0.5 if idx == 0 else -0.5), ey - er * 0.1), pupil_r, pupil_r)

                    painter.setBrush(QtGui.QBrush(QtGui.QColor("#FFFFFF")))
                    painter.drawEllipse(QtCore.QPointF(ecx - er * 0.2, ey - er * 0.25), er * 0.28, er * 0.28)

            else:
                # LEFT IMAGE IN USER PROMPT: Glowing circular eyes with large dark pupils and dual specular reflections!
                for ecx in (cx - eye_spacing, cx + eye_spacing):
                    # Outer cyan halo/bloom
                    halo_col = QtGui.QColor(neon)
                    halo_col.setAlpha(55)
                    painter.setPen(QtCore.Qt.PenStyle.NoPen)
                    painter.setBrush(QtGui.QBrush(halo_col))
                    painter.drawEllipse(QtCore.QPointF(ecx, eye_y), eye_r + 2.5 * scale, eye_r + 2.5 * scale)

                    # Luminous cyan eye base
                    painter.setBrush(QtGui.QBrush(neon))
                    painter.drawEllipse(QtCore.QPointF(ecx, eye_y), eye_r, eye_r)

                    # Inner dark pupil with crescent edge revealing the glowing bottom iris
                    pupil_r = eye_r * 0.76
                    pupil_offset_y = -eye_r * 0.08  # Leaves cyan glowing crescent at the bottom!
                    painter.setBrush(QtGui.QBrush(QtGui.QColor("#030712")))
                    painter.drawEllipse(
                        QtCore.QPointF(ecx + look_x * 0.4, eye_y + pupil_offset_y + look_y * 0.4),
                        pupil_r, pupil_r)

                    # Specular reflections (Catchlights from image)
                    # 1. Primary large catchlight (Upper-Left)
                    s1_r = eye_r * 0.28
                    s1_x = ecx - eye_r * 0.22 + look_x * 0.3
                    s1_y = eye_y - eye_r * 0.28 + look_y * 0.3
                    painter.setBrush(QtGui.QBrush(QtGui.QColor("#FFFFFF")))
                    painter.drawEllipse(QtCore.QPointF(s1_x, s1_y), s1_r, s1_r)

                    # 2. Secondary small catchlight (Lower-Left accent)
                    s2_r = eye_r * 0.12
                    s2_x = ecx - eye_r * 0.12 + look_x * 0.3
                    s2_y = eye_y + eye_r * 0.22 + look_y * 0.3
                    painter.drawEllipse(QtCore.QPointF(s2_x, s2_y), s2_r, s2_r)

            painter.end()

    pill = VisorPill()

    def forward_change(old: VisorState, new: VisorState) -> None:
        try:
            pill._repaint.emit(VISOR_COLORS[new])
        except (RuntimeError, AttributeError):
            pass

    # Chain with any pre-existing listener instead of replacing it.
    previous = controller._on_change

    def chained(old: VisorState, new: VisorState) -> None:
        forward_change(old, new)
        if previous is not None:
            try:
                previous(old, new)
            except Exception:
                pass

    pill._prev_on_change = previous
    pill._chained_fn = chained
    controller._on_change = chained
    forward_change(controller.state, controller.state)
    return pill



def run_visor(controller: VisorController, *,
              auto_roam: bool = False,
              follow_mouse: bool = False) -> tuple[Any, Any]:
    """Create (or reuse) the Qt app, build the pill, and show it.

    Returns ``(app, pill)``; the caller drives ``app.exec()``.
    Raises :class:`VisorError` when no Qt binding is installed.
    """
    _, _, _, QtWidgets = _load_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    pill = create_visor_widget(controller, auto_roam=auto_roam, follow_mouse=follow_mouse)
    pill.show()
    return (app, pill)
