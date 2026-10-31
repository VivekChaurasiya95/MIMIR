"""Digital-cobot regression tests: live answers, mic, folders, roam.

Covers the reported Habitous gaps: Replicate answering the actual
request (not a generic template), Home mic capture + shutdown, watched
folder removal, and Llama-3.2-grounded activity understanding during
autonomous wandering. Qt-free except where marked.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _needs_qt(test):
    try:
        from desktop_app.tabs._qt import load_qt
        load_qt()
    except ImportError:
        test.skipTest("PyQt6/PySide6 not installed")
    from desktop_app.tabs._qt import load_qt
    _, _, _, QtWidgets = load_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    try:
        test._qt_app_ref = app
    except AttributeError:
        pass
    return app


class TaskKindTest(unittest.TestCase):
    def test_question_vs_build_classification(self):
        from mimir.foundation import classify_task_kind
        self.assertEqual(
            classify_task_kind("how do I push things to github?"),
            "question")
        self.assertEqual(
            classify_task_kind("Explain this code: def f(): pass"),
            "question")
        self.assertEqual(
            classify_task_kind("why does sync fail on retries?"),
            "question")
        self.assertEqual(
            classify_task_kind("Implement an authentication flow"),
            "build")
        self.assertEqual(
            classify_task_kind("Refactor the login handler"),
            "refactor")
        self.assertEqual(
            classify_task_kind("Help me plan a feature: offline sync"),
            "plan")

    def test_rejects_blank(self):
        from mimir.foundation import classify_task_kind
        with self.assertRaises(ValueError):
            classify_task_kind("   ")


class ScaffoldTaskSpecificTest(unittest.TestCase):
    def test_offline_draft_addresses_the_request(self):
        from mimir.actor import ReplicationActor, ReplicationRequest
        from mimir.store import PrincipleStore
        store = PrincipleStore()
        self.addCleanup(store.close)
        actor = ReplicationActor(store)
        result = actor.replicate(ReplicationRequest(
            task_description="how do I push things to github?"))
        solution = result.replicated_solution
        # Legacy markers stay (pinned by older suites).
        self.assertIn("[MIMIR scaffold", solution)
        self.assertIn("(none retrieved.)", solution)
        # The draft must undertake the actual request, not a template.
        self.assertIn("how do I push things to github?", solution)
        self.assertIn("Suggested plan", solution)
        self.assertIn("Next step", solution)

    def test_question_prompt_answers_instead_of_coding(self):
        from mimir.store import PrincipleStore
        from mimir.actor import ReplicationActor
        store = PrincipleStore()
        self.addCleanup(store.close)
        actor = ReplicationActor(store)
        prompt = actor.compile_prompt(
            "why does sync fail on retries?", [], task_kind="question")
        self.assertIn("why does sync fail on retries?", prompt)
        self.assertIn("Mandatory invariants", prompt)
        self.assertIn("Answer the question", prompt)
        self.assertNotIn("solution only", prompt)

    def test_default_prompt_unchanged_for_builds(self):
        from mimir.store import PrincipleStore
        from mimir.actor import ReplicationActor
        store = PrincipleStore()
        self.addCleanup(store.close)
        actor = ReplicationActor(store)
        prompt = actor.compile_prompt("sync the warehouse", [])
        self.assertIn("solution only", prompt)


class FoundationFailClosedTest(unittest.TestCase):
    def test_unreachable_daemon_returns_none(self):
        from mimir.foundation import query_local_llama_text
        self.assertIsNone(query_local_llama_text(
            "hello", endpoint="http://127.0.0.1:9", timeout_s=0.3))

    def test_blank_prompt_rejected(self):
        from mimir.foundation import query_local_llama_text
        with self.assertRaises(ValueError):
            query_local_llama_text("  ")

    def test_foundation_prompt_carries_task_and_memory(self):
        from mimir.foundation import build_foundation_prompt
        prompt = build_foundation_prompt(
            "how do I push to github?",
            principles=[{"id": "bp_push_1",
                         "invariant": "Sign every commit.",
                         "scope": "git workflow"}],
            task_kind="question")
        self.assertIn("how do I push to github?", prompt)
        self.assertIn("bp_push_1", prompt)
        self.assertIn("Sign every commit.", prompt)


class WatchRootsTest(unittest.TestCase):
    def test_remove_watch_root(self):
        from desktop_app.prefs import DesktopPrefs
        prefs = DesktopPrefs(last_roots=("/a", "/b"),
                             inactive_roots=("/b",))
        trimmed = prefs.remove_watch_root("/b")
        self.assertEqual(list(trimmed.last_roots), ["/a"])
        self.assertEqual(list(trimmed.inactive_roots), [])
        # Original untouched (record-style update).
        self.assertEqual(list(prefs.last_roots), ["/a", "/b"])

    def test_remove_unknown_root_is_noop(self):
        from desktop_app.prefs import DesktopPrefs
        prefs = DesktopPrefs(last_roots=("/a",))
        self.assertEqual(list(prefs.remove_watch_root("/zzz").last_roots),
                         ["/a"])

    def test_add_watch_root_dedupes(self):
        from desktop_app.prefs import DesktopPrefs
        prefs = DesktopPrefs(last_roots=("/a",))
        grown = prefs.add_watch_root("/b").add_watch_root("/b")
        self.assertEqual(list(grown.last_roots), ["/a", "/b"])


class CaptureExplainTest(unittest.TestCase):
    def test_missing_backend_guidance(self):
        from mimir.voice import explain_capture_error
        self.assertIn("SpeechRecognition",
                      explain_capture_error(RuntimeError("no module")))
        self.assertIn("Microphone",
                      explain_capture_error(OSError("no device")))
        self.assertIn("timed out",
                      explain_capture_error(TimeoutError("wait")))


class ActivityContextTest(unittest.TestCase):
    def test_context_builder_is_task_focused(self):
        from mimir.foundation import build_activity_context
        text = build_activity_context({
            "foreground": "FlowForgeArin - main.py - VS Code",
            "recent_files": ["main.py", "auth.py"],
            "recent_episodes": ["auth: add login handler"],
        })
        self.assertIn("VS Code", text)
        self.assertIn("auth.py", text)
        self.assertIn("add login handler", text)

    def test_describe_activity_fail_closed(self):
        from mimir.foundation import describe_activity
        self.assertIsNone(describe_activity(
            {"foreground": "VS Code"},
            endpoint="http://127.0.0.1:9", timeout_s=0.3))


class MicToggleRegressionTest(unittest.TestCase):
    def test_second_click_stops_listening(self):
        import threading
        import time
        import types

        app = _needs_qt(self)
        from desktop_app.state import AppState
        from desktop_app.tabs._qt import load_qt
        from desktop_app.tabs.home import build_home_tab
        from mimir.voice import VoiceCoordinator

        _, QtCore, _, QtWidgets = load_qt()
        gate = threading.Event()
        state = AppState(store_path=":memory:")
        orig = type(state).voice_coordinator
        type(state).voice_coordinator = (
            lambda self: VoiceCoordinator(
                stt_backend=lambda t: (gate.wait(timeout=3.0), None)[1],
                tts_backend=lambda text: None))
        self.addCleanup(setattr, type(state), "voice_coordinator", orig)
        self.addCleanup(state.close)
        status = types.SimpleNamespace(
            messages=[],
            showMessage=lambda *args: status.messages.append(args[0]),
        )
        watcher = types.SimpleNamespace(watching=False, last_error="")
        root = build_home_tab(state, lambda _t: None, status, watcher)
        mic = None
        try:
            mic = next(b for b in
                       root.findChildren(QtWidgets.QPushButton)
                       if b.objectName() == "mic_btn")
            mic.click()
            app.processEvents()
            self.assertEqual(mic.text(), "⏹")
            # Second click must stop (not crash) and restore the mic.
            mic.click()
            deadline = time.monotonic() + 5.0
            while mic.text() != "🎙" and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.02)
            self.assertEqual(mic.text(), "🎙")
        finally:
            gate.set()
            try:
                worker = getattr(mic, "_active_worker", None) \
                    if mic is not None else None
                if worker is not None:
                    try:
                        worker.cancel()
                    except Exception:
                        pass
                    try:
                        worker.wait(5000)
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                root.close()
            except Exception:
                pass
            try:
                root.deleteLater()
            except Exception:
                pass
            app.processEvents()


class ObserveRemoveFolderTest(unittest.TestCase):
    def test_watched_folder_can_be_removed(self):
        import tempfile
        import types
        from pathlib import Path

        app = _needs_qt(self)
        from unittest import mock
        from desktop_app.prefs import DesktopPrefs
        from desktop_app.state import AppState
        from desktop_app.tabs._qt import load_qt
        from desktop_app.tabs.observe import build_observe_tab

        _, _, _, QtWidgets = load_qt()
        with tempfile.TemporaryDirectory() as tmp:
            victim = str(Path(tmp, "watched").resolve())
            Path(victim).mkdir()
            keeper = str(Path(tmp, "keep").resolve())
            Path(keeper).mkdir()
            prefs = DesktopPrefs(last_repo=keeper,
                                 last_roots=(keeper, victim))
            state = AppState(store_path=":memory:", prefs=prefs)
            self.addCleanup(state.close)
            status = types.SimpleNamespace(
                messages=[],
                showMessage=lambda *args: status.messages.append(args[0]),
            )
            watcher = types.SimpleNamespace(watching=False,
                                            last_error="")
            root = build_observe_tab(state, status, lambda _t: None,
                                     watcher)
            try:
                removes = [b for b in
                           root.findChildren(QtWidgets.QPushButton)
                           if b.objectName() == "remove_root_btn"]
                self.assertTrue(removes, "no per-folder Remove control")
                target = next(b for b in removes
                              if b.property("root_path") == victim)
                with mock.patch.object(
                        QtWidgets.QMessageBox, "question",
                        return_value=(QtWidgets.QMessageBox
                                      .StandardButton.Yes)):
                    target.click()
                app.processEvents()
                self.assertNotIn(victim, state.prefs.last_roots)
                self.assertIn(keeper, state.prefs.last_roots)
            finally:
                try:
                    root.close()
                except Exception:
                    pass
                try:
                    root.deleteLater()
                except Exception:
                    pass
                app.processEvents()
