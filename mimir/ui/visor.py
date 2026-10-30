"""Expressive visor avatar: floating six-state desktop presence.

Days 8-9. :class:`VisorController` is a pure state machine (fully
headless-testable) mapping MIMIR's cognitive states to colors;
:func:`create_visor_widget` renders it as an always-on-top frameless
Qt pill. Qt (PyQt6 preferred, PySide6 fallback) imports lazily inside
the factory so the controller works anywhere.
"""

from __future__ import annotations

import math
import random
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

        def _apply_color(self, color: str) -> None:
            self._color = color
            self._state = self._controller.state
            self._dot.setStyleSheet(
                f"background-color: {color}; border-radius: 22px;"
            )
            self._label.setText(VISOR_LABELS[self._controller.state])
            self._refresh_tooltip(self._controller.state)
            self.update()

        def _refresh_tooltip(self, state: VisorState) -> None:
            self.setToolTip(f"Habi: {VISOR_LABELS[state]}")

        def _toggle_blink(self) -> None:
            if not self._is_hovered:
                self._blink = True
                self.update()
                QtCore.QTimer.singleShot(160, self._unblink)

        def _unblink(self) -> None:
            self._blink = False
            self.update()

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
            act_open = menu.addAction("✦  Open Habitous Desktop")
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

            # Strictly follow user consent:
            # The cobot ONLY moves across the screen / follows the cursor
            # when observation mode is active (user gave access)!
            # In resting/idle mode, it stays parked where placed and only bobs in place.
            can_move = (self._state != VisorState.RESTING)

            if self._follow_mouse and can_move:
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
            elif self._auto_roam and can_move and (self._move_target is None
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
            # Center of the spherical co-bot
            cy = h / 2.0 - 2.0
            r = min(w, h) * 0.40  # Sphere radius

            bob = float(self._bob_dy(self._float_phase))

            # 1. 3D Floating Ground Drop-Shadow beneath the sphere
            shadow_y = h - 6.0
            shadow_rx = max(6.0, r * 0.70 - (bob * 0.6))
            shadow_ry = max(1.5, 4.0 - (bob * 0.25))
            s_grad = QtGui.QRadialGradient(cx, shadow_y, shadow_rx)
            s_grad.setColorAt(0.0, QtGui.QColor(15, 23, 42, 50))
            s_grad.setColorAt(0.6, QtGui.QColor(15, 23, 42, 20))
            s_grad.setColorAt(1.0, QtGui.QColor(15, 23, 42, 0))
            painter.setBrush(QtGui.QBrush(s_grad))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawEllipse(QtCore.QPointF(cx, shadow_y), shadow_rx, shadow_ry)

            stroke = "#38BDF8" if self._is_active_tracking else self._color

            # 2. Outer Soft Ambient Cognitive Aura Glow
            if self._state != VisorState.RESTING or self._is_hovered or self._is_active_tracking:
                g_col = QtGui.QColor(stroke)
                g_col.setAlpha(42)
                painter.setPen(QtGui.QPen(g_col, 5.0))
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QtCore.QPointF(cx, cy), r + 2.0, r + 2.0)

            # 3. Cute Spherical Ear-Pods (Tiny round white nubs with glowing LED rings)
            ear_r = r * 0.22
            for side in (-1, 1):
                ear_cx = cx + side * (r + 1.5)
                ear_cy = cy - 2.0
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.setBrush(QtGui.QBrush(QtGui.QColor(stroke)))
                painter.drawEllipse(QtCore.QPointF(ear_cx, ear_cy), ear_r, ear_r)

                ear_grad = QtGui.QRadialGradient(ear_cx - side * 1.5, ear_cy - 1.5, ear_r)
                ear_grad.setColorAt(0.0, QtGui.QColor("#FFFFFF"))
                ear_grad.setColorAt(0.8, QtGui.QColor("#F1F5F9"))
                ear_grad.setColorAt(1.0, QtGui.QColor("#CBD5E1"))
                painter.setBrush(QtGui.QBrush(ear_grad))
                painter.drawEllipse(QtCore.QPointF(ear_cx, ear_cy), ear_r - 1.2, ear_r - 1.2)

            # 4. 3D Spherical Head Body with Porcelain Radial Lighting
            head_grad = QtGui.QRadialGradient(cx - r * 0.32, cy - r * 0.35, r * 1.35)
            head_grad.setColorAt(0.0, QtGui.QColor("#FFFFFF"))
            head_grad.setColorAt(0.38, QtGui.QColor("#FFFFFF"))
            head_grad.setColorAt(0.72, QtGui.QColor("#F1F5F9"))
            head_grad.setColorAt(0.90, QtGui.QColor("#E2E8F0"))
            head_grad.setColorAt(1.0, QtGui.QColor("#CBD5E1"))
            painter.setBrush(QtGui.QBrush(head_grad))
            painter.setPen(QtGui.QPen(QtGui.QColor("#CBD5E1"), 1.2))
            painter.drawEllipse(QtCore.QPointF(cx, cy), r, r)

            # Bottom ambient bounce reflection for spherical volume
            bounce = QtGui.QRadialGradient(cx, cy + r * 0.85, r * 0.6)
            bounce.setColorAt(0.0, QtGui.QColor(248, 250, 252, 120))
            bounce.setColorAt(1.0, QtGui.QColor(248, 250, 252, 0))
            painter.setBrush(QtGui.QBrush(bounce))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawEllipse(QtCore.QPointF(cx, cy), r, r)

            # 5. Inner Curved Glass Visor Dome
            visor_w = r * 1.42
            visor_h = r * 1.10
            visor_rect = QtCore.QRectF(cx - visor_w / 2.0, cy - visor_h / 2.0 + 1.0, visor_w, visor_h)

            visor_grad = QtGui.QLinearGradient(0, cy - visor_h / 2.0, 0, cy + visor_h / 2.0)
            visor_grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, 245))
            visor_grad.setColorAt(1.0, QtGui.QColor(241, 245, 249, 225))
            painter.setBrush(QtGui.QBrush(visor_grad))
            painter.setPen(QtGui.QPen(QtGui.QColor("#E2E8F0"), 1.0))
            painter.drawRoundedRect(visor_rect, visor_h * 0.44, visor_h * 0.44)

            # Specular curved glass arc reflection across top of visor
            glass_path = QtGui.QPainterPath()
            glass_path.addRoundedRect(QtCore.QRectF(cx - visor_w * 0.42, cy - visor_h * 0.42, visor_w * 0.84, visor_h * 0.35), visor_h * 0.22, visor_h * 0.22)
            glass_grad = QtGui.QLinearGradient(0, cy - visor_h * 0.42, 0, cy)
            glass_grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, 190))
            glass_grad.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
            painter.setBrush(QtGui.QBrush(glass_grad))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawPath(glass_path)

            # 6. Cute Rosy Pink Blush Cheeks
            eye_y = cy - 2.5
            eye_spacing = r * 0.44
            blush_y = eye_y + 8.5
            blush_r = max(3.5, r * 0.11)
            painter.setBrush(QtGui.QBrush(QtGui.QColor(244, 114, 182, 55)))
            painter.drawEllipse(QtCore.QPointF(cx - eye_spacing - 2.0, blush_y), blush_r, blush_r * 0.7)
            painter.drawEllipse(QtCore.QPointF(cx + eye_spacing + 2.0, blush_y), blush_r, blush_r * 0.7)

            # 7. Expressive LED Face on Pure White Porcelain Surface
            led = QtGui.QColor(stroke)
            pen = QtGui.QPen(
                led, 2.4,
                QtCore.Qt.PenStyle.SolidLine,
                QtCore.Qt.PenCapStyle.RoundCap,
                QtCore.Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(pen)
            painter.setBrush(QtGui.QBrush(led))

            def arc(cx_val: float, happy: bool) -> None:
                path = QtGui.QPainterPath()
                if happy:
                    a_w, a_h = 5.8, 5.2
                    path.moveTo(cx_val - a_w, eye_y + 1.2)
                    path.quadTo(cx_val, eye_y - a_h, cx_val + a_w, eye_y + 1.2)
                else:
                    a_w = 5.2
                    path.moveTo(cx_val - a_w, eye_y)
                    path.quadTo(cx_val, eye_y + 2.2, cx_val + a_w, eye_y)
                painter.strokePath(path, painter.pen())

            def smile(w_val: float, depth: float, dy: float) -> None:
                path = QtGui.QPainterPath()
                sy = eye_y + dy
                path.moveTo(cx - w_val, sy)
                path.quadTo(cx, sy + depth, cx + w_val, sy)
                painter.strokePath(path, painter.pen())

            if self._blink and not self._is_hovered:
                bl_w = 8.5
                for ecx in (cx - eye_spacing, cx + eye_spacing):
                    painter.drawLine(
                        QtCore.QPointF(ecx - bl_w / 2, eye_y),
                        QtCore.QPointF(ecx + bl_w / 2, eye_y))
                smile(4.0, 2.5, 7.5)
            elif self._is_hovered:
                arc(cx - eye_spacing, True)
                arc(cx + eye_spacing, True)
                smile(6.0, 5.0, 7.5)
            elif self._state == VisorState.RESTING and not self._is_active_tracking:
                arc(cx - eye_spacing, False)
                arc(cx + eye_spacing, False)
                smile(4.2, 3.2, 7.0)
            elif self._state == VisorState.ALERT:
                eye_w, eye_h = 8.5, 12.0
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                for ecx in (cx - eye_spacing, cx + eye_spacing):
                    painter.drawRoundedRect(
                        QtCore.QRectF(ecx - eye_w / 2.0, eye_y - eye_h / 2.0, eye_w, eye_h),
                        eye_w / 2.0, eye_w / 2.0)
                painter.setPen(pen)
                frown = QtGui.QPainterPath()
                sy = eye_y + 8.5
                frown.moveTo(cx - 5.5, sy + 3.5)
                frown.quadTo(cx, sy - 1.0, cx + 5.5, sy + 3.5)
                painter.strokePath(frown, painter.pen())
            elif self._state == VisorState.THINKING:
                eye_w, eye_h = 8.5, 12.0
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.drawRoundedRect(
                    QtCore.QRectF(cx - eye_spacing - eye_w / 2.0, eye_y - eye_h / 2.0 - 2, eye_w, eye_h),
                    eye_w / 2.0, eye_w / 2.0)
                painter.drawRoundedRect(
                    QtCore.QRectF(cx + eye_spacing - eye_w / 2.0, eye_y - eye_h / 2.0 + 1, eye_w, eye_h * 0.75),
                    eye_w / 2.0, eye_w / 2.0)
                painter.setBrush(QtGui.QBrush(QtGui.QColor("#FFFFFF")))
                painter.drawEllipse(QtCore.QPointF(cx - eye_spacing + 1.0, eye_y - 4.5), 1.8, 1.8)
                painter.drawEllipse(QtCore.QPointF(cx + eye_spacing + 1.0, eye_y - 0.5), 1.3, 1.3)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QtCore.QPointF(cx + 2, eye_y + 9.5), 2.8, 2.8)
            else:
                # Observing / Active: cute big twinkling anime eyes dynamically tracking user work
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

                eye_w, eye_h = 8.5, 12.0
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                for ecx in (cx - eye_spacing, cx + eye_spacing):
                    painter.drawRoundedRect(
                        QtCore.QRectF(ecx - eye_w / 2.0 + look_x * 0.4, eye_y - eye_h / 2.0 + look_y * 0.4, eye_w, eye_h),
                        eye_w / 2.0, eye_w / 2.0)
                painter.setBrush(QtGui.QBrush(QtGui.QColor("#FFFFFF")))
                for ecx in (cx - eye_spacing, cx + eye_spacing):
                    painter.drawEllipse(QtCore.QPointF(ecx + 1.2 + look_x, eye_y - 2.5 + look_y), 2.0, 2.0)
                    painter.drawEllipse(QtCore.QPointF(ecx - 1.8 + look_x, eye_y + 2.2 + look_y), 1.1, 1.1)
                painter.setPen(pen)
                smile(5.5, 4.2, 7.8)

            painter.end()

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
