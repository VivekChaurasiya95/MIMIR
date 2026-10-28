"""PyQt6 main window: brand rail + scrollable production canvas."""

from __future__ import annotations

from typing import Any

from .prefs import DesktopPrefs
from .state import AppState, DEFAULT_STORE_PATH
from .tabs._qt import load_qt
from .theme import TAB_DEFS, qt_stylesheet

__all__ = ["SIDEBAR_WIDTH", "MainWindow", "NAV_ICONS", "run"]

#: Fixed rail width within the spec's 210-224px band.
SIDEBAR_WIDTH = 216

#: Text-safe rail glyphs (spec section 4 shell diagram).
NAV_ICONS: dict[str, str] = {
    "home": "⌂",
    "observe": "◉",
    "memory": "▤",
    "replicate": "⎘",
    "trust": "◈",
    "settings": "⚙",
}


class MainWindow:
    """Sidebar shell; TAB_IDS/navigate() API kept stable for tests."""

    TAB_IDS: tuple[str, ...] = tuple(tab_id for tab_id, *_ in TAB_DEFS)

    def __init__(self, state: AppState | None = None) -> None:
        self.state = state or AppState()
        self._window: Any = None
        self._stack: Any = None
        self._buttons: dict[str, Any] = {}
        self._index: dict[str, int] = {}

    def build(self) -> Any:
        _, _, _, QtWidgets = load_qt()
        from .tabs.home import build_home_tab
        from .tabs.memory import build_memory_tab
        from .tabs.observe import build_observe_tab
        from .tabs.replicate import build_replicate_tab
        from .tabs.settings import build_settings_tab
        from .tabs.trust import build_trust_tab

        window = QtWidgets.QMainWindow()
        window.setWindowTitle("MIMIR — desktop companion")
        window.resize(1280, 800)
        window.setMinimumSize(1024, 640)
        window.setStyleSheet(qt_stylesheet())

        status = window.statusBar()
        status.showMessage(f"Memory: {self.state.store_path}", 6000)

        sidebar = QtWidgets.QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        side = QtWidgets.QVBoxLayout(sidebar)
        side.setContentsMargins(24, 24, 16, 16)
        side.setSpacing(8)

        brand = QtWidgets.QLabel("M I M I R")
        brand.setObjectName("brand")
        side.addWidget(brand)
        side.addSpacing(8)

        group = QtWidgets.QButtonGroup(sidebar)
        group.setExclusive(True)
        main_ids = [t for t in self.TAB_IDS if t != "settings"]
        for position, tab_id in enumerate(main_ids, 1):
            title = next(t for i, t, _, _ in TAB_DEFS if i == tab_id)
            subtitle = next(s for i, _, s, _ in TAB_DEFS if i == tab_id)
            button = QtWidgets.QPushButton(
                f"{NAV_ICONS.get(tab_id, '·')}  {title}")
            button.setObjectName("nav_btn")
            button.setCheckable(True)
            button.setMinimumHeight(40)
            button.setToolTip(f"{subtitle}  (Ctrl+{position})")
            button.clicked.connect(lambda _=False, t=tab_id: self.navigate(t))
            group.addButton(button)
            self._buttons[tab_id] = button
            side.addWidget(button)

        side.addStretch(1)
        separator = QtWidgets.QFrame()
        separator.setObjectName("separator")
        separator.setFixedHeight(1)
        side.addWidget(separator)
        settings_btn = QtWidgets.QPushButton(
            f"{NAV_ICONS['settings']}  Settings")
        settings_btn.setObjectName("nav_btn")
        settings_btn.setCheckable(True)
        settings_btn.setMinimumHeight(40)
        settings_btn.setToolTip("Customize your experience.  (Ctrl+6)")
        settings_btn.clicked.connect(lambda _=False: self.navigate("settings"))
        group.addButton(settings_btn)
        self._buttons["settings"] = settings_btn
        side.addWidget(settings_btn)

        from .watch import WatchController
        watcher = WatchController(self.state, status)

        stack = QtWidgets.QStackedWidget()
        builders = {
            "home": lambda: build_home_tab(self.state, self.navigate, status,
                                           watcher),
            "observe": lambda: build_observe_tab(self.state, status,
                                                 self.navigate, watcher),
            "memory": lambda: build_memory_tab(self.state, status),
            "replicate": lambda: build_replicate_tab(self.state, status),
            "trust": lambda: build_trust_tab(self.state, status),
            "settings": lambda: build_settings_tab(self.state, status),
        }
        for tab_id in self.TAB_IDS:
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(_scroll_off())
            scroll.setWidget(builders[tab_id]())
            self._index[tab_id] = stack.addWidget(scroll)

        for position, tab_id in enumerate(self.TAB_IDS, 1):
            _bind_shortcut(window, position, tab_id, self.navigate)

        root = QtWidgets.QWidget()
        root.setObjectName("root")
        shell = QtWidgets.QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(sidebar)
        shell.addWidget(stack, 1)
        window.setCentralWidget(root)

        self._window = window
        self._stack = stack
        self.navigate("home")
        return window

    def navigate(self, tab_id: str) -> None:
        if tab_id in self._index:
            if self._stack is not None:
                self._stack.setCurrentIndex(self._index[tab_id])
                scroller = self._stack.widget(self._index[tab_id])
                widget = scroller.widget() if scroller is not None else None
                refresh = getattr(widget, "refresh_content", None)
                if callable(refresh):
                    try:
                        refresh()
                    except Exception:
                        pass  # a stale page must never break navigation
            button = self._buttons.get(tab_id)
            if button is not None:
                button.setChecked(True)

    @property
    def widget(self) -> Any:
        return self._window


def _bind_shortcut(window: Any, position: int, tab_id: str,
                   navigate: Any) -> None:
    _, _, QtGui, _ = load_qt()
    shortcut = QtGui.QShortcut(QtGui.QKeySequence(f"Ctrl+{position}"), window)
    shortcut.activated.connect(lambda t=tab_id: navigate(t))


def _scroll_off() -> Any:
    _, QtCore, _, _ = load_qt()
    try:
        from PyQt6.QtCore import Qt as _Qt
    except ImportError:
        from PySide6.QtCore import Qt as _Qt  # type: ignore
    del QtCore
    return _Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def run(argv: list[str] | None = None, store: str = DEFAULT_STORE_PATH) -> int:
    """Launch the desktop app (returns a Qt exit code)."""
    _, _, _, QtWidgets = load_qt()
    import sys

    app = QtWidgets.QApplication(sys.argv if argv is None else argv)
    state = AppState(store_path=store)
    state.prefs = DesktopPrefs.load()
    main = MainWindow(state)
    window = main.build()
    window.show()
    return int(app.exec())
