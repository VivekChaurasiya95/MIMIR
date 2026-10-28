"""Shared page scaffolding for the production shell (DESIGN.md section 6)."""

from __future__ import annotations

from typing import Any, Callable, Optional

from ._qt import load_qt

__all__ = [
    "PAGE_MARGINS",
    "PAGE_SPACING",
    "ClickableCard",
    "actions_row",
    "badge",
    "card",
    "elevate",
    "overline",
    "page",
    "page_head",
    "section_head",
    "stat_card",
    "toggle",
]

PAGE_MARGINS = (32, 28, 32, 28)
PAGE_SPACING = 14


def page() -> tuple[Any, Any]:
    """Root widget + vertical layout with the standard page geometry."""
    _, _, _, QtWidgets = load_qt()
    root = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(root)
    layout.setContentsMargins(*PAGE_MARGINS)
    layout.setSpacing(PAGE_SPACING)
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


def toggle(checked: bool = False,
           on_change: Optional[Callable[[bool], None]] = None) -> Any:
    """iOS-style permission switch (styled QCheckBox)."""
    _, _, _, QtWidgets = load_qt()
    box = QtWidgets.QCheckBox()
    box.setObjectName("toggle_switch")
    box.setChecked(checked)
    if on_change is not None:
        box.toggled.connect(on_change)
    return box


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
