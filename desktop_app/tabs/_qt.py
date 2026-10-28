"""Lazy Qt loader: PyQt6 preferred, PySide6 fallback."""

from __future__ import annotations

from typing import Any

__all__ = ["load_qt"]


def load_qt() -> Any:
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
    raise ImportError(
        "PyQt6 (or PySide6) is required for the desktop app "
        "(pip install mimir[cobot] or pip install PyQt6)"
    )
