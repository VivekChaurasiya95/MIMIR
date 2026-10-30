"""Tests for the PyQt6 desktop scaffold (Qt-free core + Qt smoke).

Headless tests cover theme tokens, tab registry, AppState wiring to the
mimir core, and presenters. Qt widget construction runs only when PyQt6
or PySide6 is installed (offscreen), otherwise it skips.
"""

import os
import unittest

from desktop_app import AppState, DesktopPrefs, PORCELAIN, TAB_DEFS
from desktop_app.presenters import (
    confidence_badge,
    filter_episodes_by_timeframe,
    format_episode_detail,
    format_memory_summary,
    format_principle_detail,
    format_replication_report,
    greeting_for_hour,
    home_suggestion,
    rel_time_ago,
)
from desktop_app.theme import qt_stylesheet
from mimir.episodes import (
    BehavioralEpisode,
    EpisodeContext,
    EpisodeOutcome,
    FileTransition,
)
from mimir.principles import (
    BehavioralPrinciple,
    ContextTrigger,
    EpistemicStatus,
)


def _episode():
    return BehavioralEpisode(
        id="ep_desktop_001",
        source="git",
        repo="/repo",
        source_ref="abc123",
        observed_at="2026-10-27T08:30:00Z",
        actor="Ada",
        summary="fix: isolate db errors",
        context=EpisodeContext(scope="payments", language="python"),
        transitions=[
            FileTransition(path="payments/db.py", status="modified",
                           diff="+raise DomainError", additions=1,
                           deletions=0)
        ],
        outcome=EpisodeOutcome(build="pass", tests="pass"),
        redactions_applied=1,
        redaction_kinds=["assigned_secret"],
    )


def _principle(pid="bp_desktop_001", confidence=0.85):
    return BehavioralPrinciple(
        id=pid,
        name=f"Principle {pid}",
        category="architecture",
        context_trigger=ContextTrigger(scope="Python test suites",
                                       language_agnostic=True),
        latent_invariant="Cover every change with pytest cases in the same commit.",
        anti_patterns=["Skipping tests"],
        epistemic_status=EpistemicStatus(
            confidence_score=confidence,
            supporting_episodes_count=3,
            contradictions_count=0,
            first_inferred="2026-10-12T10:00:00Z",
            last_validated="2026-10-27T08:30:00Z",
        ),
        inspectable_traces=["ep_seed_1"],
    )


class ThemeTest(unittest.TestCase):
    def test_porcelain_matches_production_spec(self):
        self.assertEqual(PORCELAIN["bg_app"], "#F5F5F7")
        self.assertEqual(PORCELAIN["text_heading"], "#0F172A")
        self.assertEqual(PORCELAIN["accent_primary"], "#6366F1")
        self.assertEqual(PORCELAIN["bg_nav_active"], "#F0EFFF")
        css = qt_stylesheet()
        for token in ("#F5F5F7", "#0F172A", "#6366F1", "#F0EFFF",
                      "#ECFDF5", "#F3E8FF", "#EFF6FF"):
            self.assertIn(token, css)

    def test_sidebar_shell_contract(self):
        css = qt_stylesheet()
        self.assertIn("QWidget#sidebar", css)
        self.assertIn("QPushButton#nav_btn", css)
        self.assertIn("QStackedWidget", css)
        self.assertIn("QScrollArea", css)
        self.assertNotIn("QTabBar::tab", css)
        from desktop_app.app import NAV_ICONS, SIDEBAR_WIDTH, MainWindow
        self.assertEqual(MainWindow.TAB_IDS,
                         ("home", "observe", "memory", "replicate",
                          "trust", "settings"))
        self.assertEqual(set(NAV_ICONS), set(MainWindow.TAB_IDS))
        self.assertGreaterEqual(SIDEBAR_WIDTH, 210)
        self.assertLessEqual(SIDEBAR_WIDTH, 224)

    def test_polished_rounded_controls(self):
        css = qt_stylesheet()
        for fragment in ("border-radius: 16px",  # cards
                         "border-radius: 999px",  # pills
                         "QScrollBar::handle",  # slim rounded scrollbars
                         ":hover", ":pressed", ":disabled", ":focus",
                         "QComboBox QAbstractItemView",  # rounded popup
                         "#hero_name", "Georgia",  # serif greeting
                         "#toggle_switch", "#generate_btn", "#explore_bar",
                         "#badge_confirmed", "#badge_learning",
                         "#badge_unconfirmed", "#status_card", "#ok", "#err"):
            self.assertIn(fragment, css)

    def test_hybrid_tab_registry(self):
        ids = [tab_id for tab_id, *_ in TAB_DEFS]
        self.assertEqual(ids, ["home", "observe", "memory", "replicate",
                               "trust", "settings"])
        cores = dict((t[0], t[3]) for t in TAB_DEFS)
        self.assertIn("mimir.telemetry", cores["observe"])
        self.assertIn("mimir.store", cores["memory"])
        self.assertIn("mimir.actor", cores["replicate"])
        self.assertIn("mimir.store", cores["trust"])
        self.assertIn("desktop_app.prefs", cores["settings"])


class AppStateTest(unittest.TestCase):
    def setUp(self):
        self.state = AppState(store_path=":memory:")

    def tearDown(self):
        self.state.close()

    def test_memory_roundtrip(self):
        self.state.store.save(_principle())
        found = self.state.search("pytest suites", top_k=3, min_confidence=0.0)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].id, "bp_desktop_001")

    def test_calibrate_and_delete(self):
        self.state.store.save(_principle())
        self.state.adjust_confidence("bp_desktop_001", 0.05, "desktop_test")
        updated = self.state.list_principles()[0]
        self.assertAlmostEqual(updated.epistemic_status.confidence_score, 0.90)
        self.assertIn("desktop_test", updated.inspectable_traces)
        self.assertTrue(self.state.delete_principle("bp_desktop_001"))
        self.assertEqual(self.state.list_principles(), [])

    def test_replicate_offline_scaffold(self):
        self.state.store.save(_principle())
        result = self.state.replicate("Add pytest coverage for retries",
                                      min_confidence=0.0, top_k=5)
        self.assertEqual(len(result.applied_principles), 1)
        self.assertIn("scaffold", result.replicated_solution)
        self.assertIsNone(result.contrast_baseline)
        self.assertIn("compliant", result.compliance_report)

    def test_voice_intent_routing(self):
        self.assertEqual(self.state.parse_utterance("push my code")["intent"],
                         "push_code")
        self.assertEqual(
            self.state.parse_utterance("what have you learned?")["intent"],
            "status_inquiry")
        self.assertEqual(self.state.parse_utterance("stop")["intent"], "stop")

    def test_visor_transitions(self):
        self.state.set_visor("observing")
        self.assertEqual(self.state.visor.state.value, "observing")
        self.assertEqual(self.state.visor.color, "#3B82F6")
        self.state.set_visor("resting")
        self.assertEqual(self.state.visor.label, "Resting")

    def test_memory_stats(self):
        self.assertEqual(self.state.memory_stats(),
                         {"count": 0, "avg_confidence": 0.0})
        self.state.store.save(_principle("bp_a", 0.80))
        self.state.store.save(_principle("bp_b", 0.60))
        self.assertEqual(self.state.memory_stats(),
                         {"count": 2, "avg_confidence": 0.70})

    def test_respond_routes_intents(self):
        self.assertIn("Powering down", self.state.respond("stop", "stop"))
        self.assertIn("Resting", self.state.respond("sleep", "sleep"))
        self.assertIn("Replicate tab",
                      self.state.respond("approve", "go ahead"))
        self.assertIn("my password",
                      self.state.respond("forbid", "never touch my password"))
        self.assertIn("didn't catch",
                      self.state.respond("unknown", "blargh"))
        self.assertIn("empty",
                      self.state.respond("status_inquiry", "status?"))
        self.assertIn("no push principles",
                      self.state.respond("push_code", "push my code").lower())

    def test_respond_draws_on_memory(self):
        self.state.store.save(_principle())
        status = self.state.respond("status_inquiry", "what did you learn?")
        self.assertIn("bp_desktop_001", status)
        playbook = self.state.respond("push_code", "push my code")
        self.assertIn("playbook", playbook)
        self.assertIn("Principle bp_desktop_001", playbook)

    def test_clear_memory_wipes_store_and_episodes(self):
        self.state.store.save(_principle())
        self.state.episodes.append(_episode())
        removed = self.state.clear_memory()
        self.assertEqual(removed, 1)
        self.assertEqual(self.state.list_principles(), [])
        self.assertEqual(self.state.episodes, [])

    def test_set_watching_flips_visor(self):
        self.state.set_watching(True)
        self.assertTrue(self.state.watching)
        self.assertEqual(self.state.visor.state.value, "observing")
        self.state.set_watching(False)
        self.assertFalse(self.state.watching)
        self.assertEqual(self.state.visor.state.value, "resting")

    def test_try_listen_with_injected_coordinators(self):
        class _Heard:
            def listen_phrase(self, timeout_sec=5.0):
                return "push my code"

        class _Silent:
            def listen_phrase(self, timeout_sec=5.0):
                return None

        class _Broken:
            def listen_phrase(self, timeout_sec=5.0):
                raise RuntimeError("no microphone")

        self.assertEqual(self.state.try_listen(coordinator=_Heard()),
                         (True, "push my code"))
        ok, reason = self.state.try_listen(coordinator=_Silent())
        self.assertFalse(ok)
        ok, reason = self.state.try_listen(coordinator=_Broken())
        self.assertFalse(ok)
        self.assertIn("no microphone", reason)

    def test_redact_preview_never_leaks(self):
        preview = self.state.redact_preview("api_key = supersecret123")
        self.assertNotIn("supersecret123", preview["text"])
        self.assertGreaterEqual(preview["count"], 1)
        self.assertIn("assigned_secret", preview["kinds"])


class PresenterTest(unittest.TestCase):
    def test_memory_and_detail_formats(self):
        principles = [_principle()]
        summary = format_memory_summary(principles)
        self.assertIn("bp_desktop_001", summary)
        self.assertIn("0.85", summary)
        detail = format_principle_detail(principles[0])
        self.assertIn("Cover every change", detail)
        self.assertIn("ep_seed_1", detail)
        self.assertEqual(format_memory_summary([]), "Behavioral Memory is empty.")

    def test_episode_detail_format(self):
        detail = format_episode_detail(_episode())
        for fragment in ("abc123", "Ada", "payments/db.py", "modified",
                         "build=pass", "assigned_secret", "+1/-0"):
            self.assertIn(fragment, detail)

    def test_home_suggestion_follows_memory(self):
        message, label, tab = home_suggestion([])
        self.assertEqual((label, tab), ("Observe a repo", "observe"))
        self.assertIn("haven't learned", message)
        message, label, tab = home_suggestion(
            [_principle("bp_a", 0.80), _principle("bp_b", 0.60)])
        self.assertEqual((label, tab), ("Review memory", "memory"))
        self.assertIn("2 principles", message)
        self.assertIn("0.70", message)

    def test_confidence_badge_thresholds(self):
        self.assertEqual(confidence_badge(0.95), ("Confirmed", "confirmed"))
        self.assertEqual(confidence_badge(0.80), ("Confirmed", "confirmed"))
        self.assertEqual(confidence_badge(0.79), ("Learning", "learning"))
        self.assertEqual(confidence_badge(0.50), ("Learning", "learning"))
        self.assertEqual(confidence_badge(0.49), ("Unconfirmed", "unconfirmed"))

    def test_greeting_for_hour(self):
        self.assertEqual(greeting_for_hour(8), "GOOD MORNING,")
        self.assertEqual(greeting_for_hour(13), "GOOD AFTERNOON,")
        self.assertEqual(greeting_for_hour(21), "GOOD EVENING,")

    def test_timeframe_filter_and_rel_time(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
        old = _episode()
        fresh = BehavioralEpisode(
            id="ep_fresh", source="git", repo="/repo", source_ref="def456",
            observed_at="2026-10-28T11:00:00+00:00", actor="Ada",
            summary="today", context=EpisodeContext(scope="x"),
            transitions=[], outcome=EpisodeOutcome(),
            redactions_applied=0, redaction_kinds=[])
        episodes = [old, fresh]
        self.assertEqual(len(filter_episodes_by_timeframe(episodes, "All time")), 2)
        today = filter_episodes_by_timeframe(episodes, "Today", now=now)
        self.assertEqual([e.id for e in today], ["ep_fresh"])
        week = filter_episodes_by_timeframe(episodes, "Last 7 days", now=now)
        self.assertEqual([e.id for e in week], ["ep_desktop_001", "ep_fresh"])
        self.assertEqual(rel_time_ago("2026-10-28T11:00:00+00:00", now=now), "Today")
        self.assertEqual(rel_time_ago("2026-10-27T11:00:00+00:00", now=now), "Yesterday")
        self.assertEqual(rel_time_ago("2026-10-20T11:00:00+00:00", now=now), "1 week ago")
        self.assertEqual(rel_time_ago("not-a-date", now=now), "recently")

    def test_replication_report_format(self):
        state = AppState(store_path=":memory:")
        try:
            state.store.save(_principle())
            result = state.replicate("cover retries with pytest",
                                     min_confidence=0.0)
        finally:
            state.close()
        report = format_replication_report(result)
        self.assertIn("bp_desktop_001", report)
        self.assertIn("Replicated solution", report)


class PrefsTest(unittest.TestCase):
    def test_roundtrip_and_defaults(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "desktop.json")
            self.assertEqual(DesktopPrefs.load(path), DesktopPrefs())
            prefs = DesktopPrefs(user_name="Jo", last_roots=("/a", "/b"),
                                 inactive_roots=("/b",))
            prefs.save(path)
            loaded = DesktopPrefs.load(path)
            self.assertEqual(loaded.user_name, "Jo")
            self.assertEqual(loaded.active_roots(), ["/a"])
            self.assertEqual(DesktopPrefs.load(str(Path(tmp) / "missing.json")),
                             DesktopPrefs())

    def test_record_task_caps_and_orders(self):
        prefs = DesktopPrefs()
        for i in range(25):
            prefs = prefs.record_task(f"task {i}", "Proj", "Completed",
                                      at=f"2026-10-{(i % 28) + 1:02d}T10:00:00+00:00")
        self.assertEqual(len(prefs.recent_tasks), 20)
        self.assertEqual(prefs.recent_tasks[0]["title"], "task 24")
        self.assertEqual(prefs.recent_tasks[0]["verdict"], "Completed")


class VenvHopTest(unittest.TestCase):
    def _make_venv(self, root, with_qt=True):
        import os
        from pathlib import Path
        if os.name == "nt":
            exe = Path(root) / ".venv" / "Scripts" / "python.exe"
            qt = Path(root) / ".venv" / "Lib" / "site-packages" / "PyQt6"
        else:
            exe = Path(root) / ".venv" / "bin" / "python"
            qt = (Path(root) / ".venv" / "lib" / "python3" / "site-packages"
                  / "PyQt6")
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"fake")
        if with_qt:
            qt.mkdir(parents=True, exist_ok=True)
        return str(exe)

    def test_finds_venv_with_qt(self):
        import tempfile
        from desktop_app.__main__ import find_venv_python
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_venv_python(tmp))
            exe = self._make_venv(tmp, with_qt=True)
            self.assertEqual(find_venv_python(tmp), exe)

    def test_skips_venv_without_qt(self):
        import tempfile
        from desktop_app.__main__ import find_venv_python
        with tempfile.TemporaryDirectory() as tmp:
            self._make_venv(tmp, with_qt=False)
            self.assertIsNone(find_venv_python(tmp))

    def test_skips_current_interpreter(self):
        import sys
        import tempfile
        from unittest import mock
        from desktop_app.__main__ import find_venv_python
        with tempfile.TemporaryDirectory() as tmp:
            exe = self._make_venv(tmp, with_qt=True)
            with mock.patch.object(sys, "executable", exe):
                self.assertIsNone(find_venv_python(tmp))


class CobotVisorWidgetTest(unittest.TestCase):
    def test_white_oval_and_state(self):
        try:
            from desktop_app.tabs._qt import load_qt
            _, _, _, QtWidgets = load_qt()
        except ImportError:
            self.skipTest("PyQt6/PySide6 not installed")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from desktop_app.tabs._layout import CobotVisorWidget
        face = CobotVisorWidget(width=80, height=52)
        try:
            face._timer.stop()
            face.show()
            _app.processEvents()
            self.assertEqual(face._base_state, "resting")
            face.set_state("observing")
            self.assertEqual(face._base_state, "observing")
            self.assertEqual(face._color, "#38BDF8")
            face.set_state("thinking")
            self.assertEqual(face._color, "#C084FC")
            shot = face.grab().toImage()
            self.assertFalse(shot.isNull())
            self.assertEqual(shot.width(), face.width())
            self.assertEqual(shot.height(), face.height())
        finally:
            face.close()
            face.deleteLater()


    def test_white_oval_pixels_and_float(self):
        try:
            from desktop_app.tabs._qt import load_qt
            _, _, _, QtWidgets = load_qt()
        except ImportError:
            self.skipTest("PyQt6/PySide6 not installed")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from desktop_app.tabs._layout import CobotVisorWidget
        face = CobotVisorWidget(width=80, height=52)
        try:
            face._timer.stop()
            face._motion_timer.stop()
            w = face.width()
            pad, fh = face._float_pad, int(face._face_h)
            face.show()
            _app.processEvents()
            shot = face.grab().toImage()
            bg = shot.pixelColor(w // 5, pad + fh // 2)
            self.assertGreater(bg.red(), 200)
            self.assertGreater(bg.green(), 200)
            self.assertGreater(bg.blue(), 200)
            edge = shot.pixelColor(int(w * 0.16), pad + int(fh * 0.125))
            self.assertLess(edge.alpha(), 30)
            top = shot.pixelColor(w // 2, pad + int(fh * 0.125))
            self.assertGreater(top.alpha(), 200)
            self.assertGreater(top.red(), 200)
            self.assertEqual(face._bob_dy(0.0), 0)
            self.assertGreater(face._bob_dy(1.5), 0)
            self.assertLess(face._bob_dy(-1.5), 0)
        finally:
            face.close()
            face.deleteLater()


class QtSmokeTest(unittest.TestCase):
    def test_main_window_builds_offscreen(self):
        try:
            from desktop_app.tabs._qt import load_qt
            _, _, _, QtWidgets = load_qt()
        except ImportError:
            self.skipTest("PyQt6/PySide6 not installed")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from desktop_app.app import MainWindow
        main = MainWindow(AppState(store_path=":memory:"))
        try:
            window = main.build()
        finally:
            main.state.close()
        self.assertEqual(main.TAB_IDS,
                         ("home", "observe", "memory", "replicate",
                          "trust", "settings"))
        self.assertIsNotNone(window)


class CleanObserveErrorTest(unittest.TestCase):
    def test_dubious_ownership_mentions_trust_not_invalid(self):
        from desktop_app.tabs.observe import clean_observe_error
        msg = ("D:/repo: not a git repository (git rev-parse --show-toplevel "
               "failed: fatal: detected dubious ownership in repository)")
        cleaned = clean_observe_error(msg)
        self.assertIn("ownership", cleaned.lower())
        self.assertNotIn("not a valid", cleaned.lower())

    def test_non_git_repo_says_invalid(self):
        from desktop_app.tabs.observe import clean_observe_error
        cleaned = clean_observe_error("/tmp/x: not a git repository")
        self.assertIn("not a valid Git repository", cleaned)

    def test_boundary_points_at_roots(self):
        from desktop_app.tabs.observe import clean_observe_error
        cleaned = clean_observe_error("y: outside whitelisted observation roots []")
        self.assertIn("authorized", cleaned.lower())

    def test_generic_parenthetical_trims_detail(self):
        from desktop_app.tabs.observe import clean_observe_error
        self.assertEqual(
            clean_observe_error("Observation startup failed (git timeout)"),
            "Observation startup failed")
        self.assertEqual(clean_observe_error("boom"), "boom")


class AutostartPureTest(unittest.TestCase):
    def test_login_command_targets_desktop_module(self):
        from desktop_app.autostart import login_command
        cmd = login_command()
        self.assertIn("-m desktop_app", cmd)
        self.assertIn("python", cmd.lower())

    def test_desktop_entry_shape(self):
        from desktop_app.autostart import desktop_entry_text, login_command
        entry = desktop_entry_text()
        self.assertIn("[Desktop Entry]", entry)
        self.assertIn(login_command(), entry)


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from desktop_app.tabs._qt import load_qt
            _, cls.QtCore, _, cls.QtWidgets = load_qt()
        except ImportError:
            raise unittest.SkipTest("PyQt6/PySide6 not installed")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = (cls.QtWidgets.QApplication.instance()
                   or cls.QtWidgets.QApplication([]))

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        from desktop_app import DesktopPrefs
        self.addCleanup(setattr, DesktopPrefs, "save", DesktopPrefs.save)
        path = str(Path(self._tmp.name) / "desktop.json")
        orig = DesktopPrefs.save
        DesktopPrefs.save = lambda self, p=path: orig(self, p)
        self._roots = []

    def tearDown(self):
        for root in self._roots:
            try:
                root.close()
                root.deleteLater()
            except Exception:
                pass
        try:
            self.app.processEvents()
        except Exception:
            pass

    def _pump(self, ms=120):
        import time
        end = time.monotonic() + ms / 1000.0
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)
        # A live event loop delivers DeferredDelete continuously; flush
        # explicitly so re-rendered widgets read as production does.
        try:
            self.QtCore.QCoreApplication.sendPostedEvents(
                None, self.QtCore.QEvent.Type.DeferredDelete)
        except Exception:
            pass

    def _state(self, **prefs_kw):
        from desktop_app import AppState, DesktopPrefs
        state = AppState(store_path=":memory:")
        state.prefs = DesktopPrefs(**prefs_kw)
        self.addCleanup(state.close)
        return state

    def _status(self):
        class _Status:
            def __init__(self):
                self.messages = []

            def showMessage(self, text, timeout=0):
                self.messages.append(text)
        return _Status()

    def _watcher(self):
        import types
        return types.SimpleNamespace(watching=False, last_error="")

    def _is_connected(self, obj, signal):
        mo = obj.metaObject()
        idx = mo.indexOfSignal(signal)
        self.assertGreaterEqual(idx, 0, f"no signal {signal}")
        return bool(obj.isSignalConnected(mo.method(idx)))


class HomeExtrasTest(_QtCase):
    def _build(self, state):
        from desktop_app.tabs.home import build_home_tab
        from mimir.voice import VoiceCoordinator
        orig = type(state).voice_coordinator
        type(state).voice_coordinator = (
            lambda self: VoiceCoordinator(stt_backend=lambda t: None,
                                          tts_backend=lambda text: None))
        self.addCleanup(setattr, type(state), "voice_coordinator", orig)
        self._nav = []
        status = self._status()
        root = build_home_tab(state, self._nav.append, status,
                              self._watcher())
        self._roots.append(root)
        self._status_stub = status
        return root

    def _explore(self, root):
        for edit in root.findChildren(self.QtWidgets.QLineEdit):
            if "explore" in (edit.placeholderText() or "").lower():
                return edit
        self.fail("explore bar not found")

    def test_ask_button_answers(self):
        state = self._state()
        root = self._build(state)
        ask = None
        for btn in root.findChildren(self.QtWidgets.QPushButton):
            if btn.text().strip().lower().startswith("ask"):
                ask = btn
        self.assertIsNotNone(ask, "explicit Ask button beside explore bar")
        self._explore(root).setText("hello")
        ask.click()
        self._pump(400)
        replies = [lbl.text() for lbl in
                   root.findChildren(self.QtWidgets.QLabel)
                   if lbl.text().startswith(f"{state.prefs.assistant_name}:")]
        self.assertTrue(replies, "Ask shows a companion reply")

    def test_ask_empty_hints(self):
        state = self._state()
        root = self._build(state)
        ask = next(btn for btn in
                   root.findChildren(self.QtWidgets.QPushButton)
                   if btn.text().strip().lower().startswith("ask"))
        ask.click()
        self._pump()
        self.assertTrue(
            any("type something" in m.lower()
                for m in self._status_stub.messages),
            f"empty Ask hints, got {self._status_stub.messages}")

    def test_directory_cards_navigate(self):
        state = self._state()
        root = self._build(state)
        cards = root.findChildren(self.QtWidgets.QFrame, "dir_card")
        self.assertEqual(len(cards), 4, "spec 5.1 action directory")
        for card in cards:
            self.assertTrue(self._is_connected(card, "clicked()"))
            card.clicked.emit()
        self.assertEqual(self._nav,
                         ["observe", "memory", "replicate", "trust"])


class HomeFeedbackTest(_QtCase):
    def _build(self, state, watcher=None):
        from desktop_app.tabs.home import build_home_tab
        from mimir.voice import VoiceCoordinator
        orig = type(state).voice_coordinator
        type(state).voice_coordinator = (
            lambda self: VoiceCoordinator(stt_backend=lambda t: None,
                                          tts_backend=lambda text: None))
        self.addCleanup(setattr, type(state), "voice_coordinator", orig)
        self._nav = []
        status = self._status()
        root = build_home_tab(state, self._nav.append, status,
                              watcher or self._watcher())
        self._roots.append(root)
        self._status_stub = status
        return root

    def _failing_watcher(self):
        import types
        return types.SimpleNamespace(
            watching=False, last_error="",
            start_async=lambda repo, roots, n, cb: cb(
                False, "fatal: detected dubious ownership in repository"))

    def test_watch_failure_shows_inline_error_card(self):
        state = self._state()
        root = self._build(state, self._failing_watcher())
        watch = next(b for b in root.findChildren(self.QtWidgets.QPushButton)
                     if b.objectName() == "start_obs_btn")
        watch.click()
        self._pump(300)
        opener = next(
            (b for b in root.findChildren(self.QtWidgets.QPushButton)
             if b.text() == "Open Observe"),
            None)
        self.assertIsNotNone(opener, "inline error card with Open Observe")
        card = opener.parentWidget()
        while card is not None and card.objectName() != "card":
            card = card.parentWidget()
        self.assertIsNotNone(card, "error card widget found")
        self.assertTrue(card.isVisibleTo(root),
                        "error card must be visible after failure")
        opener.click()
        self._pump()
        self.assertEqual(self._nav, ["observe"])

    def test_showcase_progress_and_stop(self):
        state = self._state()
        root = self._build(state)
        start = next(b for b in root.findChildren(self.QtWidgets.QPushButton)
                     if "Showcase" in b.text())
        bars = root.findChildren(self.QtWidgets.QProgressBar)
        self.assertEqual(len(bars), 1, "showcase progress bar present")
        start.click()
        self._pump(200)
        if "Stop" in start.text():
            start.click()  # stop while running
            self._pump(2500)
        else:
            self._pump(8000)
        self.assertTrue(start.isEnabled())
        self.assertIn("Run Live Habi Showcase", start.text())
        self.assertLessEqual(bars[0].value(), bars[0].maximum())

    def test_reply_copy_button(self):
        state = self._state()
        root = self._build(state)
        explore = next(e for e in root.findChildren(self.QtWidgets.QLineEdit)
                       if "explore" in (e.placeholderText() or "").lower())
        explore.setText("hello")
        ask = next(b for b in root.findChildren(self.QtWidgets.QPushButton)
                   if b.text().strip().lower().startswith("ask"))
        ask.click()
        self._pump(400)
        copy_btn = next(
            (b for b in root.findChildren(self.QtWidgets.QPushButton)
             if b.text() == "Copy reply"),
            None)
        self.assertIsNotNone(copy_btn, "reply needs a Copy button")
        copy_btn.click()
        self._pump()
        clip = self.QtWidgets.QApplication.clipboard().text()
        self.assertIn(f"{state.prefs.assistant_name}:", clip)

    def test_live_refresh_timer_present(self):
        state = self._state()
        root = self._build(state)
        timers = [t for t in root.findChildren(self.QtCore.QTimer)
                  if t.interval() == 3000]
        self.assertTrue(timers, "home refreshes while watching")

    def test_no_tofu_glyphs(self):
        import unicodedata
        state = self._state()
        root = self._build(state)
        texts = []
        for lbl in root.findChildren(self.QtWidgets.QLabel):
            texts.append(lbl.text() or "")
        for btn in root.findChildren(self.QtWidgets.QPushButton):
            texts.append(btn.text() or "")
        joined = "\n".join(texts)
        risky = [ch for ch in joined
                 if unicodedata.category(ch) == "Lo"
                 and not unicodedata.name(ch, "").startswith(("LATIN", "CJK"))]
        self.assertEqual(risky, [], f"unrenderable glyphs: {risky!r}")


class ReplicateExtrasTest(_QtCase):
    def _build(self, state):
        from desktop_app.tabs.replicate import build_replicate_tab
        status = self._status()
        root = build_replicate_tab(state, status)
        self._roots.append(root)
        return root

    def test_task_row_click_reopens_task(self):
        state = self._state()
        state.store.save(_principle())
        state.prefs = state.prefs.record_task("Fix retries", "Proj",
                                              "Completed")
        root = self._build(state)
        rows = root.findChildren(self.QtWidgets.QFrame, "task_row")
        self.assertEqual(len(rows), 1)
        self.assertTrue(self._is_connected(rows[0], "clicked()"),
                        "task row click must reopen the task")
        rows[0].clicked.emit()
        self._pump()
        task = root.findChildren(self.QtWidgets.QTextEdit)[0]
        self.assertIn("Fix retries", task.toPlainText())

    def test_min_conf_spin_visible_and_persisted(self):
        state = self._state()
        root = self._build(state)
        spins = root.findChildren(self.QtWidgets.QDoubleSpinBox)
        visible = [s for s in spins if s.isVisibleTo(root)]
        self.assertTrue(visible, "Min conf control must be visible")
        visible[0].setValue(0.5)
        self._pump()
        self.assertAlmostEqual(state.prefs.default_min_confidence, 0.5)


class TrustScopesTest(_QtCase):
    def _build(self, state):
        from desktop_app.tabs.trust import build_trust_tab
        root = build_trust_tab(state, self._status())
        self._roots.append(root)
        return root

    def test_refresh_never_duplicates_scopes(self):
        state = self._state(last_roots=("/a", "/b"))
        root = self._build(state)
        boxes = root.findChildren(self.QtWidgets.QCheckBox)
        self.assertEqual(len(boxes), 2)
        root.refresh_content()
        root.refresh_content()
        self._pump()
        self.assertEqual(len(root.findChildren(self.QtWidgets.QCheckBox)), 2)

    def test_storage_and_retention_rows_wired(self):
        state = self._state(last_roots=("/a",))
        root = self._build(state)
        rows = root.findChildren(self.QtWidgets.QFrame, "data_row")
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertTrue(self._is_connected(row, "clicked()"))


class MemoryCategoryTest(_QtCase):
    def _cat_principle(self, pid, category, confidence=0.65):
        base = _principle(pid, confidence)
        return BehavioralPrinciple(
            id=base.id, name=base.name, category=category,
            context_trigger=base.context_trigger,
            latent_invariant=base.latent_invariant,
            anti_patterns=base.anti_patterns,
            epistemic_status=base.epistemic_status,
            inspectable_traces=base.inspectable_traces,
        )

    def test_selection_survives_new_category(self):
        from desktop_app.tabs.memory import build_memory_tab
        state = self._state()
        state.store.save(self._cat_principle("bp_cat_a", "architecture"))
        state.store.save(self._cat_principle("bp_cat_b", "testing"))
        root = build_memory_tab(state, self._status())
        self._roots.append(root)
        combos = [cb for cb in root.findChildren(self.QtWidgets.QComboBox)
                  if "All" in [cb.itemText(i) for i in range(cb.count())]]
        self.assertEqual(len(combos), 1)
        category = combos[0]
        category.setCurrentText("testing")
        self._pump()
        state.store.save(self._cat_principle("bp_cat_c", "workflow", 0.55))
        root.refresh_content()
        self._pump()
        self.assertEqual(category.currentText(), "testing")


class MemoryFeedbackTest(_QtCase):
    def _build(self, state):
        from desktop_app.tabs.memory import build_memory_tab
        status = self._status()
        root = build_memory_tab(state, status)
        self._roots.append(root)
        self._status_stub = status
        return root

    def _open_first(self, root):
        cards = root.findChildren(self.QtWidgets.QFrame, "principle_card")
        self.assertTrue(cards, "principle cards rendered")
        cards[0].clicked.emit()
        self._pump()
        return cards[0]

    def _btn(self, root, text):
        return next(b for b in root.findChildren(self.QtWidgets.QPushButton)
                    if b.text() == text)

    def test_calibrate_reports_before_after(self):
        state = self._state()
        state.store.save(_principle("bp_fb_1", 0.70))
        root = self._build(state)
        self._open_first(root)
        self._btn(root, "+ Reinforce").click()
        self._pump()
        self.assertTrue(
            any("0.70" in m and "0.75" in m
                for m in self._status_stub.messages),
            f"reinforce reports 0.70 -> 0.75, got {self._status_stub.messages}")
        self._btn(root, "− Contradict").click()
        self._pump()
        self.assertTrue(
            any("0.75" in m and "0.65" in m
                for m in self._status_stub.messages),
            f"contradict reports 0.75 -> 0.65, got {self._status_stub.messages}")

    def test_delete_confirms_and_reports(self):
        state = self._state()
        state.store.save(_principle("bp_fb_del", 0.70))
        root = self._build(state)
        self._open_first(root)
        box = self.QtWidgets.QMessageBox
        orig_exec = box.exec
        box.exec = lambda self_: box.StandardButton.Cancel
        self.addCleanup(setattr, box, "exec", orig_exec)
        self._btn(root, "Delete").click()
        self._pump()
        self.assertEqual(len(state.list_principles()), 1,
                         "cancel keeps the principle")
        box.exec = lambda self_: box.StandardButton.Ok
        self._btn(root, "Delete").click()
        self._pump()
        self.assertEqual(state.list_principles(), [])
        self.assertTrue(any("delet" in m.lower()
                            for m in self._status_stub.messages),
                        "delete reports")

    def test_no_matches_offers_clear_filters(self):
        state = self._state()
        state.store.save(_principle("bp_fb_s", 0.70))
        root = self._build(state)
        search = next(e for e in root.findChildren(self.QtWidgets.QLineEdit)
                      if "Search principles" in (e.placeholderText() or ""))
        search.setText("zzz-no-such-habit")
        self._pump()
        clear = next(
            (b for b in root.findChildren(self.QtWidgets.QPushButton)
             if b.text() == "Clear filters"),
            None)
        self.assertIsNotNone(clear, "no-match state offers Clear filters")
        self.assertTrue(clear.isVisibleTo(root))
        clear.click()
        self._pump()
        self.assertEqual(search.text(), "")
        self.assertTrue(root.findChildren(self.QtWidgets.QFrame,
                                          "principle_card"))

    def test_confidence_meters_track_scores(self):
        state = self._state()
        state.store.save(_principle("bp_fb_m", 0.70))
        root = self._build(state)
        cards = root.findChildren(self.QtWidgets.QFrame, "principle_card")
        self.assertEqual(len(cards), 1)
        meters = cards[0].findChildren(self.QtWidgets.QProgressBar)
        self.assertEqual(len(meters), 1, "card shows a confidence meter")
        self.assertEqual(meters[0].value(), 70)
        self._open_first(root)
        detail_meters = [m for m in root.findChildren(
            self.QtWidgets.QProgressBar) if m.isVisibleTo(root)]
        self.assertTrue(detail_meters, "detail shows a confidence meter")
        self.assertEqual(detail_meters[0].value(), 70)
        self._btn(root, "+ Reinforce").click()
        self._pump()
        detail_meters = [m for m in root.findChildren(
            self.QtWidgets.QProgressBar) if m.isVisibleTo(root)]
        self.assertEqual(detail_meters[0].value(), 75)

    def test_detail_refreshes_on_return(self):
        state = self._state()
        state.store.save(_principle("bp_fb_r", 0.70))
        root = self._build(state)
        self._open_first(root)
        state.adjust_confidence("bp_fb_r", 0.10, "external")
        root.refresh_content()
        self._pump()
        detail = root.findChildren(self.QtWidgets.QPlainTextEdit)[0]
        self.assertIn("0.80", detail.toPlainText())
        state.delete_principle("bp_fb_r")
        root.refresh_content()
        self._pump()
        stack = root.findChild(self.QtWidgets.QStackedWidget)
        self.assertEqual(stack.currentIndex(), 0,
                         "deleted detail falls back to the list")


class TrayBuilderTest(_QtCase):
    def test_offscreen_returns_none(self):
        from desktop_app.tray import sync_tray
        window = self.QtWidgets.QWidget()
        self._roots.append(window)
        from desktop_app import DesktopPrefs
        self.assertIsNone(sync_tray(window, DesktopPrefs()))


if __name__ == "__main__":
    unittest.main()
