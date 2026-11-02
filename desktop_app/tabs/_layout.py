"""Shared page scaffolding for the production shell (docs/DESKTOP_DESIGN.md section 6)."""

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

            def closeEvent(self, event: Any) -> None:
                try:
                    self._timer.stop()
                    self._motion_timer.stop()
                except Exception:
                    pass
                super().closeEvent(event)

            def _tick_float(self) -> None:
                if not self.isVisible() and self.parent() is None:
                    return
                self._float_phase += 0.1047
                try:
                    self.update()
                except (RuntimeError, AttributeError):
                    pass

            def _bob_dy(self, phase: float) -> int:
                return int(round(self._float_amp * math.sin(phase)))

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
                scale = min(w / 80.0, h / 52.0)
                cx = w / 2.0
                cy = h / 2.0

                # 3D Pebble dimensions (wide rounded pebble from 3D reference)
                head_rx = 35.0 * scale
                head_ry = 22.0 * scale

                # 1. Soft 3D Ground Drop Shadow beneath the pebble
                shadow_y = cy + head_ry * 0.95
                shadow_rx = head_rx * 0.78
                shadow_ry = max(2.0, head_ry * 0.22)
                s_grad = QtGui.QRadialGradient(cx, shadow_y, shadow_rx)
                s_grad.setColorAt(0.0, QtGui.QColor(15, 23, 42, 55))
                s_grad.setColorAt(0.65, QtGui.QColor(15, 23, 42, 18))
                s_grad.setColorAt(1.0, QtGui.QColor(15, 23, 42, 0))
                painter.setBrush(QtGui.QBrush(s_grad))
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.drawEllipse(QtCore.QPointF(cx, shadow_y), shadow_rx, shadow_ry)

                # State color selection
                stroke_color = "#38BDF8" if self._base_state in ("resting", "observing", "active") else self._color

                # 2. Side Glowing Ear Light Slits (Visible on Left/Right outer curved edge)
                slit_w = max(2.5 * scale, 2.0)
                slit_h = head_ry * 0.75
                slit_y = cy - slit_h / 2.0
                for side in (-1, 1):
                    slit_x = cx + side * (head_rx * 0.96) - (slit_w if side == 1 else 0.0)
                    slit_rect = QtCore.QRectF(slit_x, slit_y, slit_w, slit_h)
                    # Outer cyan halo glow
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
                screen_w = 40.0 * scale
                screen_h = 28.0 * scale
                screen_r = screen_h * 0.44
                screen_rect = QtCore.QRectF(cx - screen_w / 2.0, cy - screen_h / 2.0 + 0.5 * scale, screen_w, screen_h)

                # Outer screen bevel rim / shadow
                painter.setPen(QtGui.QPen(QtGui.QColor("#94A3B8"), 1.2 * scale))
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(screen_rect, screen_r, screen_r)

                # Deep obsidian glass background
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

                # Determine if eyes are in closed/resting arc state
                is_sleeping = (self._current_expression in ("resting", "idle") and not self._is_hovered)
                is_blinking = (self._blink and not self._is_hovered)

                if is_sleeping or is_blinking:
                    # RIGHT IMAGE IN USER PROMPT: Downward gently curved glowing arcs!
                    arc_w = eye_r * 0.95
                    arc_h = eye_r * 0.48

                    # Soft neon bloom glow under arc
                    bloom_pen = QtGui.QPen(neon, max(5.0 * scale, 3.8))
                    bloom_col = QtGui.QColor(neon)
                    bloom_col.setAlpha(65)
                    bloom_pen.setColor(bloom_col)
                    bloom_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
                    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)

                    # Core bright neon line
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

                elif self._is_hovered or self._current_expression == "happy":
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
                        painter.drawEllipse(QtCore.QPointF(ecx, eye_y + pupil_offset_y), pupil_r, pupil_r)

                        # Specular reflections (Catchlights from image)
                        # 1. Primary large catchlight (Upper-Left)
                        s1_r = eye_r * 0.28
                        s1_x = ecx - eye_r * 0.22
                        s1_y = eye_y - eye_r * 0.28
                        painter.setBrush(QtGui.QBrush(QtGui.QColor("#FFFFFF")))
                        painter.drawEllipse(QtCore.QPointF(s1_x, s1_y), s1_r, s1_r)

                        # 2. Secondary small catchlight (Lower-Left accent)
                        s2_r = eye_r * 0.12
                        s2_x = ecx - eye_r * 0.12
                        s2_y = eye_y + eye_r * 0.22
                        painter.drawEllipse(QtCore.QPointF(s2_x, s2_y), s2_r, s2_r)

                painter.end()


        return _Visor()
