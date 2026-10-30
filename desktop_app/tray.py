"""System-tray companion icon (Qt-only, headless-safe).

Settings → General → "Show in system tray" persists a preference; this
module gives that switch its real effect: a porcelain tray icon with a
Show/Hide + Quit menu, plus close-to-tray behavior owned by the main
window. Every entry point degrades to ``None`` when no system tray is
available (offscreen/headless), so callers never branch on platform.
"""

from __future__ import annotations

from typing import Any

__all__ = ["hide_tray", "sync_tray"]

_TRAY_ATTR = "_mimir_tray_icon"


def _tray_available() -> bool:
    try:
        from .tabs._qt import load_qt
        _, _, _, QtWidgets = load_qt()
    except ImportError:
        return False
    try:
        return bool(QtWidgets.QSystemTrayIcon.isSystemTrayAvailable())
    except Exception:
        return False


def _tray_icon() -> Any:
    """Paint a small porcelain visor badge (no asset files needed)."""
    from .tabs._qt import load_qt
    _, _, QtGui, _ = load_qt()
    size = 32
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtGui.QColor(0, 0, 0, 0))
    painter = QtGui.QPainter(pixmap)
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#FFFFFF")))
        painter.setPen(QtGui.QPen(QtGui.QColor("#CBD5E1"), 2))
        painter.drawEllipse(2, 2, size - 4, size - 4)
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#4F46E5")))
        painter.setPen(QtGui.QPen(QtGui.QColor("#4F46E5"), 1))
        painter.drawEllipse(9, 12, 14, 8)
    finally:
        painter.end()
    icon = QtGui.QIcon(pixmap)
    return icon


def sync_tray(window: Any, show_in_tray: bool) -> Any | None:
    """Create, show, or remove the tray icon to match the preference.

    Returns the live ``QSystemTrayIcon`` (also stored on the window), or
    ``None`` when the tray is disabled or unavailable. Idempotent: a
    second call with the same preference reuses the existing icon.
    """
    existing = getattr(window, _TRAY_ATTR, None)
    if not show_in_tray or not _tray_available():
        if existing is not None:
            try:
                existing.hide()
                existing.deleteLater()
            except Exception:
                pass
            try:
                delattr(window, _TRAY_ATTR)
            except AttributeError:
                pass
        return None
    if existing is not None:
        try:
            existing.show()
            return existing
        except Exception:
            pass
    from .tabs._qt import load_qt
    _, _, _, QtWidgets = load_qt()
    try:
        tray = QtWidgets.QSystemTrayIcon(window)
        tray.setIcon(_tray_icon())
        tray.setToolTip("Habitous — Intelligent Developer Companion")
        menu = QtWidgets.QMenu()
        toggle_action = menu.addAction("Show Habitous")
        quit_action = menu.addAction("Quit Habitous")

        def _toggle() -> None:
            try:
                if window.isVisible():
                    window.hide()
                    toggle_action.setText("Show Habitous")
                else:
                    window.show()
                    window.raise_()
                    window.activateWindow()
                    toggle_action.setText("Hide Habitous")
            except Exception:
                pass

        def _quit() -> None:
            try:
                window.setProperty("_mimir_force_quit", True)
            except Exception:
                pass
            try:
                window.close()
            except Exception:
                pass
            app = QtWidgets.QApplication.instance()
            if app is not None:
                try:
                    app.quit()
                except Exception:
                    pass

        try:
            toggle_action.triggered.connect(_toggle)
            quit_action.triggered.connect(_quit)
            tray.activated.connect(
                lambda reason: _toggle()
                if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger
                else None)
        except Exception:
            pass
        tray.setContextMenu(menu)
        tray.show()
    except Exception:
        return None
    try:
        setattr(window, _TRAY_ATTR, tray)
    except Exception:
        pass
    return tray


def hide_tray(window: Any) -> None:
    """Remove the tray icon during shutdown (idempotent)."""
    sync_tray(window, False)
