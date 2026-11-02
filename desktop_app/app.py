"""PyQt6 main window: brand rail + scrollable production canvas."""

from __future__ import annotations

from typing import Any

from .prefs import DesktopPrefs
from .state import AppState, DEFAULT_STORE_PATH
from .tabs._qt import load_qt
from .theme import TAB_DEFS, normalize_accent, normalize_theme, qt_stylesheet

__all__ = ["SIDEBAR_WIDTH", "MainWindow", "NAV_ICONS", "run"]

#: Fixed rail width within the spec's 210-224px band.
SIDEBAR_WIDTH = 216

#: Text-safe rail glyphs (spec section 4 shell diagram).
NAV_ICONS: dict[str, str] = {
    "home": "⌂",
    "observe": "👁",
    "memory": "📖",
    "replicate": "⧉",
    "trust": "🛡",
    "settings": "⚙",
}


def load_application_fonts() -> None:
    """Register custom typography fonts (Caprasimo, Bebas Neue, Barlow) with Qt."""
    try:
        from pathlib import Path
        _, _, QtGui, _ = load_qt()
        fonts_dir = Path(__file__).resolve().parent / "assets" / "fonts"
        if fonts_dir.is_dir() and hasattr(QtGui, "QFontDatabase"):
            for font_file in fonts_dir.glob("*.ttf"):
                QtGui.QFontDatabase.addApplicationFont(str(font_file))
    except Exception:
        pass


class MainWindow:
    """Sidebar shell; TAB_IDS/navigate() API kept stable for tests."""

    TAB_IDS: tuple[str, ...] = tuple(tab_id for tab_id, *_ in TAB_DEFS)

    def __init__(self, state: AppState | None = None) -> None:
        self.state = state or AppState()
        self._window: Any = None
        self._stack: Any = None
        self._buttons: dict[str, Any] = {}
        self._index: dict[str, int] = {}
        self.visor_pill: Any = None
        self._closing = False

    def toggle_floating_cobot(self) -> bool:
        """Toggle the visibility of the floating desktop co-bot companion."""
        if self.visor_pill is None:
            try:
                from mimir.ui.visor import create_visor_widget
                self.visor_pill = create_visor_widget(
                    self.state.visor,
                    auto_roam=self.state.prefs.auto_roam,
                    follow_mouse=self.state.prefs.follow_mouse,
                )
                self.visor_pill.show()
                self.state.prefs.floating_cobot = True
                try:
                    self.state.prefs.save()
                except Exception:
                    pass
                return True
            except Exception:
                return False
        if self.visor_pill.isVisible():
            self.visor_pill.hide()
            self.state.prefs.floating_cobot = False
        else:
            self.visor_pill.show()
            self.state.prefs.floating_cobot = True
        try:
            self.state.prefs.save()
        except Exception:
            pass
        return self.state.prefs.floating_cobot

    def shutdown(self) -> None:
        """Stop workers and the floating companion (idempotent).

        Never closes the main window itself, so the close-event handler
        can call this without recursing into ``QWidget.close()``.
        """
        if self._closing:
            return
        self._closing = True
        try:
            self.state.prefs.save()
        except Exception:
            pass
        try:
            from .tray import hide_tray
            if self._window is not None:
                hide_tray(self._window)
        except Exception:
            pass
        # Release the single Qt-integrated event loop (if this app owns
        # it) so a later launch in the same process can reacquire it.
        try:
            from mimir.isolation import LoopOwner
            if LoopOwner.owner() == "desktop-app":
                LoopOwner.release("desktop-app")
        except Exception:
            pass
        if self._window is not None:
            try:
                _, QtCore, _, _ = load_qt()
                from .actuate_worker import DriveWorker
                from .observe_worker import CallableWorker, ObserveWorker
                from .replicate_worker import ReplicateWorker
                from .showcase import ShowcaseRunner
                from .voice_worker import ListenWorker, SpeakWorker
                kinds = (DriveWorker, ShowcaseRunner, ObserveWorker,
                         CallableWorker, ReplicateWorker, SpeakWorker,
                         ListenWorker)
            except (ImportError, AttributeError):
                kinds = ()
                QtCore = None
            if QtCore is not None:
                try:
                    for obj in self._window.findChildren(QtCore.QObject):
                        if kinds and isinstance(obj, kinds):
                            try:
                                obj.cancel()
                            except Exception:
                                pass
                    for thread in self._window.findChildren(QtCore.QThread):
                        attached = getattr(thread, "_mimir_runner", None)
                        if (kinds and attached is not None
                                and isinstance(attached, kinds)):
                            try:
                                attached.cancel()
                            except Exception:
                                pass
                        try:
                            thread.quit()
                        except Exception:
                            pass
                    for thread in self._window.findChildren(QtCore.QThread):
                        try:
                            thread.wait(3000)
                        except Exception:
                            pass
                except (RuntimeError, AttributeError):
                    pass
        if self.visor_pill is not None:
            try:
                self.visor_pill.close()
            except Exception:
                pass
            self.visor_pill = None

    def close(self) -> None:
        """Cleanly close window and floating co-bot companion."""
        self.shutdown()
        if self._window is not None:
            try:
                self._window.close()
            except Exception:
                pass

    def build(self) -> Any:
        load_application_fonts()
        _, _, _, QtWidgets = load_qt()
        import os
        from .tabs.home import build_home_tab
        from .tabs.memory import build_memory_tab
        from .tabs.observe import build_observe_tab
        from .tabs.replicate import build_replicate_tab
        from .tabs.settings import build_settings_tab
        from .tabs.trust import build_trust_tab

        from .tabs._layout import set_density
        set_density(getattr(self.state.prefs, "spacious", True))

        window = QtWidgets.QMainWindow()
        window.setWindowTitle("MIMIR — Intelligent Developer Companion")
        window.resize(1280, 800)
        window.setMinimumSize(1024, 640)
        window.setStyleSheet(qt_stylesheet(
            normalize_theme(getattr(self.state.prefs, "theme", "light")),
            normalize_accent(getattr(self.state.prefs, "accent", "Indigo")),
        ))

        status = window.statusBar()
        status.showMessage(f"MIMIR Memory: {self.state.store_path}", 6000)

        # System-tray companion (Settings → General owns the preference).
        try:
            from .tray import sync_tray
            sync_tray(window, bool(getattr(self.state.prefs,
                                           "show_in_tray", True)))
        except Exception:
            pass

        # Wire floating co-bot companion
        try:
            from mimir.ui.visor import create_visor_widget
            self.visor_pill = create_visor_widget(
                self.state.visor,
                auto_roam=self.state.prefs.auto_roam,
                follow_mouse=self.state.prefs.follow_mouse,
            )
            try:
                screen = QtWidgets.QApplication.primaryScreen()
                if screen is not None:
                    avail = screen.availableGeometry()
                    self.visor_pill.move(
                        avail.x() + avail.width() - self.visor_pill.width() - 40,
                        avail.y() + 60,
                    )
            except Exception:
                pass

            if self.state.prefs.floating_cobot and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
                self.visor_pill.show()
        except Exception:
            self.visor_pill = None

        orig_close = window.closeEvent

        def _on_close(event: Any) -> None:
            try:
                forced = bool(window.property("_mimir_force_quit"))
            except Exception:
                forced = False
            tray = None
            if not forced:
                try:
                    from .tray import sync_tray
                    tray = sync_tray(
                        window, bool(getattr(self.state.prefs,
                                             "show_in_tray", True)))
                except Exception:
                    tray = None
            if tray is not None and not forced:
                try:
                    event.ignore()
                except Exception:
                    pass
                try:
                    window.hide()
                except Exception:
                    pass
                try:
                    tray.showMessage(
                        "MIMIR",
                        "Still here — click the tray icon to reopen.")
                except Exception:
                    pass
                return
            self.shutdown()
            if callable(orig_close):
                orig_close(event)

        window.closeEvent = _on_close

        sidebar = QtWidgets.QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        side = QtWidgets.QVBoxLayout(sidebar)
        side.setContentsMargins(24, 24, 16, 16)
        side.setSpacing(8)

        from .tabs._layout import CobotVisorWidget
        brand_row = QtWidgets.QHBoxLayout()
        brand_row.setSpacing(10)
        brand_visor = CobotVisorWidget(width=38, height=32, on_click=self.toggle_floating_cobot)
        brand_visor.setToolTip("Click to toggle floating Habi companion")
        brand = QtWidgets.QLabel("MIMIR.")
        brand.setObjectName("brand")
        brand_row.addWidget(brand_visor)
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        side.addLayout(brand_row)
        side.addSpacing(10)

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

        def _on_activity(note: str) -> None:
            # Wandering narration: the floating pill's tooltip carries
            # Habi's live read of the user's task.
            try:
                pill = self.visor_pill
                if pill is not None and hasattr(pill, "set_activity_note"):
                    pill.set_activity_note(note)
            except Exception:
                pass

        watcher = WatchController(self.state, status,
                                  on_activity=_on_activity)

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


def _schedule_startup_probe(main: MainWindow, window: Any) -> None:
    """Bridge 1: Ollama health-check shortly after launch (background).

    Never blocks startup: a worker thread probes the loopback daemon
    1.5s after the window appears, then reports through the status
    bar. A fresh offline install auto-upgrades to the live Ollama
    engine when the daemon answers (explicit provider choices are
    never overridden).
    """
    try:
        _, QtCore, _, _ = load_qt()
        from .observe_worker import create_callable_worker
        from .ollama import ollama_endpoint, probe_ollama
    except ImportError:
        return
    state = main.state

    def _fire() -> None:
        try:
            endpoint = ollama_endpoint(getattr(state, "prefs", None))
        except Exception:
            endpoint = "http://127.0.0.1:11434"
        try:
            worker = create_callable_worker(
                probe_ollama, (endpoint,), {"timeout_s": 1.0},
                parent=window)
        except Exception:
            return
        try:
            status = window.statusBar()
        except Exception:
            status = None

        def _done(report: object) -> None:
            try:
                worker.deleteLater()
            except Exception:
                pass
            data = report if isinstance(report, dict) else {}
            if not data.get("available"):
                if status is not None:
                    status.showMessage(
                        "Ollama offline — using the offline scaffold "
                        "(Settings → Model → Start Ollama for live AI).",
                        8000)
                return
            selected = str(data.get("selected") or "llama3.2:1b")
            try:
                prefs = state.prefs
                provider = (
                    getattr(prefs, "model_provider", "") or "").strip()
                if provider in ("", "offline"):
                    prefs.model_provider = "ollama"
                    if not (getattr(prefs, "model_name", "")
                            or "").strip():
                        prefs.model_name = str(selected)
                    prefs.save()
                    if status is not None:
                        status.showMessage(
                            "Ollama live — engine switched to ollama "
                            f"({selected}).", 8000)
                    return
            except Exception:
                pass
            if status is not None:
                status.showMessage(f"Ollama live: {selected}.", 5000)

        def _failed(_message: str) -> None:
            try:
                worker.deleteLater()
            except Exception:
                pass

        try:
            worker.callable_finished.connect(_done)
            worker.callable_error.connect(_failed)
            worker.start()
        except Exception:
            pass

    try:
        QtCore.QTimer.singleShot(1500, _fire)
    except Exception:
        pass


def run(argv: list[str] | None = None, store: str = DEFAULT_STORE_PATH) -> int:
    """Launch the desktop app (returns a Qt exit code)."""
    _, _, _, QtWidgets = load_qt()
    import os
    import sys

    # Known Windows noise: Qt's DirectWrite backend warns while enumerating
    # legacy OEM raster fonts at startup. Silence only that category, and
    # only when the user has not configured logging rules themselves.
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts.warning=false")
    app = QtWidgets.QApplication(sys.argv if argv is None else argv)
    # Single event-loop ownership (plan section 6): prefer one
    # Qt-integrated qasync loop for future async subscribers; when qasync
    # is absent the runtime keeps working through the loop-free
    # EventBus.enqueue/pump bridge instead. Never fatal either way.
    try:
        from mimir.isolation import LoopOwner
        LoopOwner.acquire_qasync("desktop-app", app)
    except Exception as exc:
        print(f"[loop: qasync unavailable ({exc}); "
              "using loop-free event bridge]")
    state = AppState(store_path=store)
    state.prefs = DesktopPrefs.load()
    main = MainWindow(state)
    window = main.build()
    window.show()
    _schedule_startup_probe(main, window)
    try:
        return int(app.exec())
    finally:
        try:
            main.shutdown()
        except Exception:
            pass
        try:
            app.processEvents()
            _, _qc, _, _ = load_qt()
            if hasattr(_qc, "QCoreApplication") and hasattr(_qc, "QEvent"):
                _qc.QCoreApplication.sendPostedEvents(None, _qc.QEvent.Type.DeferredDelete)
            app.processEvents()
        except Exception:
            pass
        try:
            from mimir.isolation import LoopOwner
            if LoopOwner.owner() == "desktop-app":
                LoopOwner.release("desktop-app")
        except Exception:
            pass
