"""Shared page scaffolding for the production shell (DESIGN.md section 6)."""

from __future__ import annotations

import math
from typing import Any, Callable, Optional

from ._qt import load_qt

__all__ = [
    "COMPACT_MARGINS",
    "COMPACT_SPACING",
    "PAGE_MARGINS",
    "PAGE_SPACING",
    "ClickableCard",
    "CobotVisorWidget",
    "SliderToggle",
    "actions_row",
    "badge",
    "card",
    "elevate",
    "functionality_banner",
    "overline",
    "page",
    "page_head",
    "section_head",
    "set_density",
    "setting_row",
    "settings_group_card",
    "stat_card",
    "toggle",
]

PAGE_MARGINS = (32, 28, 32, 28)
PAGE_SPACING = 14
COMPACT_MARGINS = (20, 16, 20, 16)
COMPACT_SPACING = 8

#: Live registry of page layouts so Settings → Appearance can switch
#: density without a restart (entries expire with their pages).
_PAGE_LAYOUTS: list[Any] = []
_SPACIOUS = True


def set_density(spacious: bool) -> None:
    """Apply spacious/compact geometry to every live page (Settings)."""
    global _SPACIOUS
    _SPACIOUS = bool(spacious)
    margins = PAGE_MARGINS if _SPACIOUS else COMPACT_MARGINS
    spacing = PAGE_SPACING if _SPACIOUS else COMPACT_SPACING
    alive: list[Any] = []
    for ref in _PAGE_LAYOUTS:
        layout = ref()
        if layout is None:
            continue
        alive.append(ref)
        try:
            layout.setContentsMargins(*margins)
            layout.setSpacing(spacing)
        except (RuntimeError, AttributeError):
            pass
    _PAGE_LAYOUTS[:] = alive


def page() -> tuple[Any, Any]:
    """Root widget + vertical layout with the standard page geometry."""
    import weakref

    _, _, _, QtWidgets = load_qt()
    root = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(root)
    margins = PAGE_MARGINS if _SPACIOUS else COMPACT_MARGINS
    layout.setContentsMargins(*margins)
    layout.setSpacing(PAGE_SPACING if _SPACIOUS else COMPACT_SPACING)
    _PAGE_LAYOUTS.append(weakref.ref(layout))
    return root, layout


def overline(text: str) -> Any:
    """11px bold uppercase section catcher (tracking via wide glyphs)."""
    _, _, _, QtWidgets = load_qt()
    label = QtWidgets.QLabel(text.upper())
    label.setObjectName("overline")
    return label


def page_head(tag: str, title: str, sub: str) -> Any:
    """Spec header block: overline + 22-26px title + muted subtitle."""
    _, _, _, QtWidgets = load_qt()
    box = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 2)
    lay.setSpacing(4)
    lay.addWidget(overline(tag))
    head = QtWidgets.QLabel(title)
    head.setObjectName("h1")
    head.setWordWrap(True)
    body = QtWidgets.QLabel(sub)
    body.setObjectName("subtle")
    body.setWordWrap(True)
    lay.addWidget(head)
    lay.addWidget(body)
    return box


def section_head(title: str, sub: str) -> Any:
    """H2 title + muted subtitle block shared by every functional tab."""
    _, _, _, QtWidgets = load_qt()
    box = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 4)
    lay.setSpacing(2)
    head = QtWidgets.QLabel(title)
    head.setObjectName("h2")
    body = QtWidgets.QLabel(sub)
    body.setObjectName("subtle")
    body.setWordWrap(True)
    lay.addWidget(head)
    lay.addWidget(body)
    return box


def card() -> tuple[Any, Any]:
    """Porcelain card widget + padded vertical layout."""
    _, _, _, QtWidgets = load_qt()
    box = QtWidgets.QWidget()
    box.setObjectName("card")
    lay = QtWidgets.QVBoxLayout(box)
    lay.setContentsMargins(20, 16, 20, 16)
    lay.setSpacing(10)
    return box, lay


def functionality_banner(
    icon: str,
    title: str,
    description: str,
    pills: Optional[list[tuple[str, str]]] = None,
    icon_cls: str = "icon_box_purple",
) -> Any:
    """Zero-height hidden stub to eliminate redundant text walls and keep pages breathable."""
    _, _, _, QtWidgets = load_qt()
    w = QtWidgets.QWidget()
    w.setFixedHeight(0)
    w.setVisible(False)
    return w


def settings_group_card(title: str, subtitle: str) -> tuple[Any, Any]:
    """Grouped porcelain card for settings sections with descriptive header."""
    _, _, _, QtWidgets = load_qt()
    box = QtWidgets.QWidget()
    box.setObjectName("card")
    elevate(box, radius=16, opacity=0.03)

    lay = QtWidgets.QVBoxLayout(box)
    lay.setContentsMargins(22, 18, 22, 18)
    lay.setSpacing(12)

    head = QtWidgets.QLabel(title)
    head.setObjectName("h2")
    sub = QtWidgets.QLabel(subtitle)
    sub.setObjectName("muted")
    sub.setWordWrap(True)

    lay.addWidget(head)
    lay.addWidget(sub)

    div = QtWidgets.QFrame()
    div.setObjectName("row_divider")
    lay.addWidget(div)

    content_box = QtWidgets.QVBoxLayout()
    content_box.setSpacing(12)
    lay.addLayout(content_box)

    return box, content_box


def setting_row(label: str, description: str, widget: Any) -> Any:
    """Clean, spacious row pairing setting label + help text with a control."""
    _, _, _, QtWidgets = load_qt()
    row_frame = QtWidgets.QFrame()
    row_frame.setObjectName("setting_row_box")
    rl = QtWidgets.QHBoxLayout(row_frame)
    rl.setContentsMargins(0, 4, 0, 4)
    rl.setSpacing(16)

    text_col = QtWidgets.QVBoxLayout()
    text_col.setSpacing(2)

    lbl = QtWidgets.QLabel(label)
    lbl.setObjectName("setting_label")
    lbl.setWordWrap(True)

    hlp = QtWidgets.QLabel(description)
    hlp.setObjectName("setting_help")
    hlp.setWordWrap(True)

    text_col.addWidget(lbl)
    text_col.addWidget(hlp)
    rl.addLayout(text_col, 1)

    if isinstance(widget, QtWidgets.QLayout):
        rl.addLayout(widget)
    else:
        rl.addWidget(widget)
        if isinstance(widget, QtWidgets.QAbstractButton):
            lbl.setCursor(_cursor_pointer())
            orig_mouse = lbl.mouseReleaseEvent
            def _on_lbl_click(e: Any) -> None:
                widget.toggle()
                if callable(orig_mouse):
                    orig_mouse(e)
            lbl.mouseReleaseEvent = _on_lbl_click
    return row_frame


def actions_row(*widgets: Any) -> Any:
    """Left-docked natural-size button row with trailing stretch."""
    _, _, _, QtWidgets = load_qt()
    row = QtWidgets.QHBoxLayout()
    row.setSpacing(10)
    for widget in widgets:
        row.addWidget(widget)
    row.addStretch(1)
    return row


def stat_card(title: str, value: str) -> Any:
    """Small dashboard stat: muted caption over a bold value."""
    _, _, _, QtWidgets = load_qt()
    box = QtWidgets.QWidget()
    box.setObjectName("card")
    lay = QtWidgets.QVBoxLayout(box)
    lay.setContentsMargins(18, 12, 18, 12)
    lay.setSpacing(2)
    caption = QtWidgets.QLabel(title)
    caption.setObjectName("muted")
    number = QtWidgets.QLabel(value)
    number.setObjectName("statvalue")
    lay.addWidget(caption)
    lay.addWidget(number)
    return box


def badge(text: str, kind: str) -> Any:
    """Status pill; ``kind`` selects the ``badge_<kind>`` QSS rule."""
    _, _, _, QtWidgets = load_qt()
    label = QtWidgets.QLabel(text)
    label.setObjectName(f"badge_{kind}")
    return label


class SliderToggle:
    """Factory creating an animated iOS/Fluent-style slider toggle button."""

    def __new__(cls, checked: bool = False, parent: Any = None) -> Any:
        _, QtCore, QtGui, QtWidgets = load_qt()

        class _SliderToggle(QtWidgets.QCheckBox):
            def __init__(self, is_on: bool = False, parent_widget: Any = None) -> None:
                super().__init__(parent_widget)
                self.setCheckable(True)
                self.setChecked(bool(is_on))
                self.setFixedSize(50, 26)
                self.setCursor(_cursor_pointer())
                self._thumb_pos = 27.0 if is_on else 3.0
                self._target_pos = self._thumb_pos
                self._anim_timer = QtCore.QTimer(self)
                self._anim_timer.timeout.connect(self._tick_anim)
                self.toggled.connect(self._on_toggled)

            def hitButton(self, pos: Any) -> bool:
                return self.rect().contains(pos)

            def _on_toggled(self, checked_state: bool) -> None:
                self._target_pos = 27.0 if checked_state else 3.0
                if not self.isVisible():
                    self._thumb_pos = self._target_pos
                    self.update()
                elif not self._anim_timer.isActive():
                    self._anim_timer.start(16)

            def _tick_anim(self) -> None:
                diff = self._target_pos - self._thumb_pos
                if abs(diff) < 0.8:
                    self._thumb_pos = self._target_pos
                    self._anim_timer.stop()
                else:
                    self._thumb_pos += diff * 0.35
                self.update()

            def sizeHint(self) -> Any:
                return QtCore.QSize(50, 26)

            def paintEvent(self, event: Any) -> None:
                painter = QtGui.QPainter(self)
                painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
                w = self.width()
                h = self.height()
                r = h / 2.0

                track_path = QtGui.QPainterPath()
                track_path.addRoundedRect(QtCore.QRectF(1.0, 1.0, w - 2.0, h - 2.0), r - 1.0, r - 1.0)

                t = (self._thumb_pos - 3.0) / 24.0
                t = max(0.0, min(1.0, t))

                off_r, off_g, off_b = 226, 232, 240
                on_r, on_g, on_b = 79, 70, 229
                cur_r = int(off_r + (on_r - off_r) * t)
                cur_g = int(off_g + (on_g - off_g) * t)
                cur_b = int(off_b + (on_b - off_b) * t)

                track_col = QtGui.QColor(cur_r, cur_g, cur_b)
                painter.setBrush(QtGui.QBrush(track_col))
                painter.setPen(QtGui.QPen(QtGui.QColor(15, 23, 42, 25), 1.0))
                painter.drawPath(track_path)

                thumb_d = 20.0
                thumb_y = 3.0
                thumb_x = self._thumb_pos

                shadow_rect = QtCore.QRectF(thumb_x - 0.5, thumb_y + 1.2, thumb_d + 1.0, thumb_d + 1.0)
                painter.setBrush(QtGui.QBrush(QtGui.QColor(15, 23, 42, 35)))
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.drawEllipse(shadow_rect)

                knob_grad = QtGui.QLinearGradient(thumb_x, thumb_y, thumb_x, thumb_y + thumb_d)
                knob_grad.setColorAt(0.0, QtGui.QColor("#FFFFFF"))
                knob_grad.setColorAt(1.0, QtGui.QColor("#F8FAFC"))
                painter.setBrush(QtGui.QBrush(knob_grad))
                painter.setPen(QtGui.QPen(QtGui.QColor(15, 23, 42, 20), 0.8))
                painter.drawEllipse(QtCore.QRectF(thumb_x, thumb_y, thumb_d, thumb_d))
                painter.end()

        return _SliderToggle(checked, parent)


def toggle(checked: bool = False,
           on_change: Optional[Callable[[bool], None]] = None) -> Any:
    """Modern iOS/Fluent-style animated slider toggle switch."""
    sw = SliderToggle(checked=checked)
    if on_change is not None:
        sw.toggled.connect(on_change)
    return sw


def elevate(widget: Any, radius: int = 24, opacity: float = 0.05) -> None:
    """Apply the ambient card shadow (spec 3.3) via a drop-shadow effect."""
    _, _, QtGui, QtWidgets = load_qt()
    shadow = QtWidgets.QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(radius)
    shadow.setOffset(0, 4 if radius > 12 else 1)
    shadow.setColor(QtGui.QColor(15, 23, 42, int(255 * opacity)))
    widget.setGraphicsEffect(shadow)


class ClickableCard:
    """Factory for clickable QFrame cards emitting ``clicked``.

    Implemented as a factory (not a subclass) so both PyQt6 and PySide6
    work through the lazy Qt loader.
    """

    def __new__(cls, object_name: str = "card",
                on_click: Optional[Callable[[], None]] = None) -> Any:
        _, _, _, QtWidgets = load_qt()

        class _Card(QtWidgets.QFrame):
            clicked = None  # replaced below with a real signal

        try:
            from PyQt6.QtCore import pyqtSignal as _signal
        except ImportError:
            from PySide6.QtCore import Signal as _signal  # type: ignore
        _Card.clicked = _signal()

        def _press(frame: Any, event: Any) -> None:
            frame.clicked.emit()  # type: ignore[attr-defined]
            QtWidgets.QFrame.mousePressEvent(frame, event)

        _Card.mousePressEvent = _press  # type: ignore[method-assign]
        card_widget = _Card()
        card_widget.setObjectName(object_name)
        card_widget.setCursor(_cursor_pointer())
        if on_click is not None:
            card_widget.clicked.connect(on_click)  # type: ignore[attr-defined]
        return card_widget


def _cursor_pointer() -> Any:
    _, QtCore, _, _ = load_qt()
    try:
        from PyQt6.QtCore import Qt as _Qt
    except ImportError:
        from PySide6.QtCore import Qt as _Qt  # type: ignore
    del QtCore
    try:
        from PyQt6.QtGui import QCursor as _QCursor
    except ImportError:
        from PySide6.QtGui import QCursor as _QCursor  # type: ignore
    return _QCursor(_Qt.CursorShape.PointingHandCursor)


class CobotVisorWidget:
    """Factory for the personified co-bot avatar face with smiles and expressive states."""

    def __new__(
        cls,
        parent: Optional[Any] = None,
        color: str = "#3B82F6",
        width: int = 80,
        height: int = 52,
        on_click: Optional[Callable[[], None]] = None,
    ) -> Any:
        _, QtCore, QtGui, QtWidgets = load_qt()

        class _Visor(QtWidgets.QWidget):
            def __init__(self) -> None:
                super().__init__(parent)
                self._face_w = float(width)
                self._face_h = float(height)
                self._float_amp = max(2.0, float(height) * 0.08)
                self._float_pad = int(round(self._float_amp))
                self._float_phase = 0.0
                self.setFixedSize(width, height + 2 * self._float_pad)
                self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
                self._base_state = "resting"
                self._current_expression = "resting"
                self._color = color
                self._blink = False
                self._is_hovered = False
                self._on_click = on_click
                if on_click is not None:
                    self.setCursor(_cursor_pointer())

                # Idle animation timer for realistic natural blinking
                self._timer = QtCore.QTimer(self)
                self._timer.timeout.connect(self._toggle_blink)
                self._timer.start(3400)
                self._motion_timer = QtCore.QTimer(self)
                self._motion_timer.timeout.connect(self._tick_float)
                self._motion_timer.start(50)

            def _tick_float(self) -> None:
                self._float_phase += 0.1047
                self.update()

            def _bob_dy(self, phase: float) -> int:
                return int(round(self._float_amp * math.sin(phase)))

            def _toggle_blink(self) -> None:
                if not self._is_hovered:
                    self._blink = True
                    self.update()
                    QtCore.QTimer.singleShot(160, self._unblink)

            def _unblink(self) -> None:
                self._blink = False
                self.update()

            def set_color(self, color: str) -> None:
                if self._color != color:
                    self._color = color
                    self.update()

            def set_state(self, state_name: str) -> None:
                self._base_state = str(state_name).lower()
                colors = {
                    "resting": "#94A3B8",
                    "observing": "#38BDF8",
                    "listening": "#34D399",
                    "thinking": "#C084FC",
                    "driving": "#FBBF24",
                    "alert": "#F87171",
                }
                self._color = colors.get(self._base_state, "#38BDF8")
                if not self._is_hovered:
                    self._current_expression = self._base_state
                self.update()

            def enterEvent(self, event: Any) -> None:
                self._is_hovered = True
                self._current_expression = "happy"
                self.update()
                super().enterEvent(event)

            def leaveEvent(self, event: Any) -> None:
                self._is_hovered = False
                self._current_expression = self._base_state
                self.update()
                super().leaveEvent(event)

            def mousePressEvent(self, event: Any) -> None:
                self._current_expression = "happy"
                self.update()
                if self._on_click is not None:
                    self._on_click()
                super().mousePressEvent(event)

            def paintEvent(self, event: Any) -> None:
                painter = QtGui.QPainter(self)
                painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

                w = self._face_w
                h = self._face_h
                painter.translate(
                    0.0, self._float_pad + self._bob_dy(self._float_phase))
                scale = min(w, h) / 52.0
                cx = w / 2.0
                cy = h / 2.0
                r = min(w, h) * 0.46

                # 1. Soft 3D Ground Drop Shadow
                shadow_y = cy + r * 0.90
                shadow_rx = r * 0.75
                shadow_ry = r * 0.22
                s_grad = QtGui.QRadialGradient(cx, shadow_y, shadow_rx)
                s_grad.setColorAt(0.0, QtGui.QColor(15, 23, 42, 45))
                s_grad.setColorAt(0.7, QtGui.QColor(15, 23, 42, 18))
                s_grad.setColorAt(1.0, QtGui.QColor(15, 23, 42, 0))
                painter.setBrush(QtGui.QBrush(s_grad))
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.drawEllipse(QtCore.QPointF(cx, shadow_y), shadow_rx, shadow_ry)

                stroke = "#38BDF8" if self._base_state in ("observing", "active") else self._color

                # 2. Outer Soft Ambient Aura Glow
                if self._base_state in ("observing", "listening", "thinking", "driving", "alert") or self._is_hovered:
                    g_col = QtGui.QColor(stroke)
                    g_col.setAlpha(42)
                    painter.setPen(QtGui.QPen(g_col, 4.0 * scale))
                    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(QtCore.QPointF(cx, cy), r + 2.0 * scale, r + 2.0 * scale)

                # 3. Cute Spherical Ear-Pods with glowing LED rings
                ear_r = max(5.0 * scale, r * 0.28)
                for side in (-1, 1):
                    ear_cx = cx + side * (r + ear_r * 0.2)
                    ear_cy = cy
                    painter.setPen(QtCore.Qt.PenStyle.NoPen)
                    painter.setBrush(QtGui.QBrush(QtGui.QColor(stroke)))
                    painter.drawEllipse(QtCore.QPointF(ear_cx, ear_cy), ear_r, ear_r)

                    ear_grad = QtGui.QRadialGradient(ear_cx - side * 1.5, ear_cy - 1.5, ear_r)
                    ear_grad.setColorAt(0.0, QtGui.QColor("#FFFFFF"))
                    ear_grad.setColorAt(0.8, QtGui.QColor("#F1F5F9"))
                    ear_grad.setColorAt(1.0, QtGui.QColor("#CBD5E1"))
                    painter.setBrush(QtGui.QBrush(ear_grad))
                    painter.drawEllipse(QtCore.QPointF(ear_cx, ear_cy), ear_r - 1.2 * scale, ear_r - 1.2 * scale)

                # 4. 3D Spherical Head Body with Porcelain Radial Lighting
                head_grad = QtGui.QRadialGradient(cx - r * 0.32, cy - r * 0.35, r * 1.35)
                head_grad.setColorAt(0.0, QtGui.QColor("#FFFFFF"))
                head_grad.setColorAt(0.38, QtGui.QColor("#FFFFFF"))
                head_grad.setColorAt(0.72, QtGui.QColor("#F1F5F9"))
                head_grad.setColorAt(0.90, QtGui.QColor("#E2E8F0"))
                head_grad.setColorAt(1.0, QtGui.QColor("#CBD5E1"))
                painter.setBrush(QtGui.QBrush(head_grad))
                painter.setPen(QtGui.QPen(QtGui.QColor("#CBD5E1"), max(1.2 * scale, 1.0)))
                painter.drawEllipse(QtCore.QPointF(cx, cy), r, r)

                # Bottom ambient bounce reflection
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

                # Specular curved glass arc reflection
                glass_path = QtGui.QPainterPath()
                glass_path.addRoundedRect(QtCore.QRectF(cx - visor_w * 0.42, cy - visor_h * 0.42, visor_w * 0.84, visor_h * 0.35), visor_h * 0.22, visor_h * 0.22)
                glass_grad = QtGui.QLinearGradient(0, cy - visor_h * 0.42, 0, cy)
                glass_grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, 190))
                glass_grad.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
                painter.setBrush(QtGui.QBrush(glass_grad))
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.drawPath(glass_path)

                # 6. Cute Rosy Pink Blush Cheeks
                eye_y = cy - 2.5 * scale
                eye_spacing = r * 0.44
                blush_y = eye_y + 7.5 * scale
                blush_r = max(3.0 * scale, r * 0.11)
                painter.setBrush(QtGui.QBrush(QtGui.QColor(244, 114, 182, 60)))
                painter.drawEllipse(QtCore.QPointF(cx - eye_spacing - 2.0 * scale, blush_y), blush_r, blush_r * 0.7)
                painter.drawEllipse(QtCore.QPointF(cx + eye_spacing + 2.0 * scale, blush_y), blush_r, blush_r * 0.7)

                # 7. Expressive Cute LED Face Elements
                led = QtGui.QColor(stroke)
                pen = QtGui.QPen(
                    led, max(2.2 * scale, 1.8),
                    QtCore.Qt.PenStyle.SolidLine,
                    QtCore.Qt.PenCapStyle.RoundCap,
                    QtCore.Qt.PenJoinStyle.RoundJoin,
                )
                painter.setPen(pen)
                painter.setBrush(QtGui.QBrush(led))

                def smile(w_val: float, depth: float, dy: float) -> None:
                    path = QtGui.QPainterPath()
                    sy = eye_y + dy * scale
                    path.moveTo(cx - w_val * scale, sy)
                    path.quadTo(cx, sy + depth * scale, cx + w_val * scale, sy)
                    painter.strokePath(path, painter.pen())

                if self._blink and not self._is_hovered:
                    bl_w = 7.5 * scale
                    for ecx in (cx - eye_spacing, cx + eye_spacing):
                        painter.drawLine(
                            QtCore.QPointF(ecx - bl_w / 2, eye_y),
                            QtCore.QPointF(ecx + bl_w / 2, eye_y))
                    smile(3.5, 2.2, 6.5)
                elif self._is_hovered or self._current_expression == "happy":
                    for ecx in (cx - eye_spacing, cx + eye_spacing):
                        path = QtGui.QPainterPath()
                        path.moveTo(ecx - 5.0 * scale, eye_y + 1.0 * scale)
                        path.quadTo(ecx, eye_y - 4.5 * scale, ecx + 5.0 * scale, eye_y + 1.0 * scale)
                        painter.strokePath(path, painter.pen())
                    smile(5.5, 4.5, 6.5)
                elif self._current_expression in ("resting", "idle"):
                    for ecx in (cx - eye_spacing, cx + eye_spacing):
                        path = QtGui.QPainterPath()
                        path.moveTo(ecx - 4.8 * scale, eye_y - 0.5 * scale)
                        path.quadTo(ecx, eye_y + 2.8 * scale, ecx + 4.8 * scale, eye_y - 0.5 * scale)
                        painter.strokePath(path, painter.pen())
                    smile(3.8, 2.8, 6.5)
                elif self._current_expression == "alert":
                    eye_w, eye_h = 7.5 * scale, 10.5 * scale
                    painter.setPen(QtCore.Qt.PenStyle.NoPen)
                    for ecx in (cx - eye_spacing, cx + eye_spacing):
                        painter.drawRoundedRect(
                            QtCore.QRectF(ecx - eye_w / 2.0, eye_y - eye_h / 2.0, eye_w, eye_h),
                            eye_w / 2.0, eye_w / 2.0)
                    painter.setPen(pen)
                    frown = QtGui.QPainterPath()
                    sy = eye_y + 7.5 * scale
                    frown.moveTo(cx - 5.0 * scale, sy + 3.0 * scale)
                    frown.quadTo(cx, sy - 1.0 * scale, cx + 5.0 * scale, sy + 3.0 * scale)
                    painter.strokePath(frown, painter.pen())
                else:
                    # Observing / Active: cute big twinkling anime eyes
                    eye_w, eye_h = 7.5 * scale, 10.5 * scale
                    painter.setPen(QtCore.Qt.PenStyle.NoPen)
                    for ecx in (cx - eye_spacing, cx + eye_spacing):
                        painter.drawRoundedRect(
                            QtCore.QRectF(ecx - eye_w / 2.0, eye_y - eye_h / 2.0, eye_w, eye_h),
                            eye_w / 2.0, eye_w / 2.0)
                    painter.setBrush(QtGui.QBrush(QtGui.QColor("#FFFFFF")))
                    for ecx in (cx - eye_spacing, cx + eye_spacing):
                        painter.drawEllipse(QtCore.QPointF(ecx + 1.0 * scale, eye_y - 2.2 * scale), 1.8 * scale, 1.8 * scale)
                        painter.drawEllipse(QtCore.QPointF(ecx - 1.5 * scale, eye_y + 1.8 * scale), 1.0 * scale, 1.0 * scale)
                    painter.setPen(pen)
                    smile(4.8, 3.8, 6.8)

                painter.end()

        return _Visor()
