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


if __name__ == "__main__":
    unittest.main()
