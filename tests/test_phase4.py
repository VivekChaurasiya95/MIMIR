"""Tests for Phase IV live-embodiment integration (NEXT_SESSION_SPEC).

Qt-free tests cover the LLM provider adapters, ``get_llm_fn`` wiring, and
demo persona seeding. Qt worker/UI tests run offscreen only when PyQt6 or
PySide6 is installed, otherwise they skip.
"""

import io
import json
import os
import unittest
import urllib.error
from unittest import mock

from desktop_app.llm import (
    LLMError,
    build_anthropic_client,
    build_gemini_client,
    build_ollama_client,
    build_openai_client,
    normalize_provider,
)
from desktop_app.personas import (
    PERSONA_IDS,
    get_persona,
    get_persona_principles,
    list_personas,
    seed_persona,
)
from desktop_app.prefs import DesktopPrefs
from desktop_app.state import AppState, get_llm_fn
from mimir.store import PrincipleStore


class FakeResponse:
    """Minimal urlopen context manager returning a JSON payload."""

    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class NormalizeProviderTest(unittest.TestCase):
    def test_short_ids_roundtrip(self):
        for provider in ("offline", "openai", "anthropic", "gemini",
                         "ollama"):
            self.assertEqual(normalize_provider(provider), provider)

    def test_settings_labels_map_to_ids(self):
        self.assertEqual(normalize_provider(
            "Offline Heuristic Scaffold (Zero-API, 100% Local)"), "offline")
        self.assertEqual(normalize_provider(
            "OpenAI Bridge (Cloud - Needs API Key)"), "openai")
        self.assertEqual(normalize_provider(
            "Anthropic Claude Bridge (Cloud - Needs API Key)"), "anthropic")
        self.assertEqual(normalize_provider(
            "Gemini 2.5 Flash Bridge (Hybrid Cloud - Optional)"), "gemini")
        self.assertEqual(normalize_provider(
            "Local Ollama / Llama 3 Bridge (Self-Hosted)"), "ollama")

    def test_unknown_and_empty_fall_back_to_offline(self):
        self.assertEqual(normalize_provider(""), "offline")
        self.assertEqual(normalize_provider("  "), "offline")
        self.assertEqual(normalize_provider("mystery-llm"), "offline")


class GetLlmFnTest(unittest.TestCase):
    def test_offline_by_default(self):
        self.assertIsNone(get_llm_fn(DesktopPrefs()))

    def test_unknown_provider_is_offline(self):
        prefs = DesktopPrefs(model_provider="mystery", api_key="k")
        self.assertIsNone(get_llm_fn(prefs))

    def test_cloud_needs_api_key(self):
        for provider in ("openai", "anthropic", "gemini"):
            prefs = DesktopPrefs(model_provider=provider, api_key="")
            self.assertIsNone(get_llm_fn(prefs))

    def test_cloud_with_key_returns_callable(self):
        for provider in ("openai", "anthropic", "gemini"):
            prefs = DesktopPrefs(model_provider=provider, api_key="dummy")
            self.assertTrue(callable(get_llm_fn(prefs)))

    def test_ollama_needs_no_key(self):
        prefs = DesktopPrefs(model_provider="ollama")
        self.assertTrue(callable(get_llm_fn(prefs)))

    def test_replicate_uses_live_fn_from_prefs(self):
        state = AppState(store_path=":memory:")
        try:
            with mock.patch("desktop_app.state.get_llm_fn",
                            return_value=lambda prompt: "LIVE SOLUTION"):
                result = state.replicate("live task")
            self.assertIn("LIVE SOLUTION", result.replicated_solution)
            self.assertIsNotNone(result.contrast_baseline)
        finally:
            state.close()

    def test_force_offline_ignores_live_prefs(self):
        state = AppState(store_path=":memory:")
        try:
            with mock.patch("desktop_app.state.get_llm_fn",
                            return_value=lambda prompt: "LIVE SOLUTION"):
                result = state.replicate("live task", force_offline=True)
            self.assertIn("scaffold", result.replicated_solution)
            self.assertIsNone(result.contrast_baseline)
        finally:
            state.close()


class LlmClientTest(unittest.TestCase):
    def test_openai_parses_chat_completion(self):
        payload = {"choices": [{"message": {"content": "hello"}}]}
        with mock.patch("desktop_app.llm.urllib.request.urlopen",
                        return_value=FakeResponse(payload)):
            self.assertEqual(build_openai_client("k")("hi"), "hello")

    def test_ollama_parses_generate_response(self):
        payload = {"response": "hi there"}
        with mock.patch("desktop_app.llm.urllib.request.urlopen",
                        return_value=FakeResponse(payload)):
            self.assertEqual(build_ollama_client()("hi"), "hi there")

    def test_gemini_parses_candidate(self):
        payload = {"candidates": [{"content": {"parts": [
            {"text": "greetings"}]}}]}
        with mock.patch("desktop_app.llm.urllib.request.urlopen",
                        return_value=FakeResponse(payload)):
            self.assertEqual(build_gemini_client("k")("hi"), "greetings")

    def test_anthropic_joins_text_blocks(self):
        payload = {"content": [{"type": "text", "text": "a"},
                               {"type": "text", "text": "b"}]}
        with mock.patch("desktop_app.llm.urllib.request.urlopen",
                        return_value=FakeResponse(payload)):
            self.assertEqual(build_anthropic_client("k")("hi"), "ab")

    def test_http_error_becomes_llm_error(self):
        err = urllib.error.HTTPError(
            "https://x", 401, "Unauthorized", {}, io.BytesIO(b"bad key"))
        with mock.patch("desktop_app.llm.urllib.request.urlopen",
                        side_effect=err):
            with self.assertRaises(LLMError):
                build_openai_client("bad")("hi")

    def test_empty_inputs_rejected(self):
        with self.assertRaises(ValueError):
            build_openai_client("")
        with self.assertRaises(ValueError):
            build_openai_client("k", model="")
        with self.assertRaises(ValueError):
            build_gemini_client("  ")
        with self.assertRaises(ValueError):
            build_ollama_client(endpoint="")


class PersonaTest(unittest.TestCase):
    def test_three_personas_listed(self):
        personas = list_personas()
        self.assertEqual([p["id"] for p in personas], list(PERSONA_IDS))
        self.assertEqual(len(personas), 3)
        titles = [p["title"] for p in personas]
        self.assertIn("Senior Clean Architecture Dev", titles)
        self.assertIn("Rust Systems Engineer", titles)
        self.assertIn("Fast Web Prototyper", titles)

    def test_persona_content_matches_spec(self):
        clean = " ".join(p.latent_invariant
                         for p in get_persona_principles("clean_arch"))
        self.assertIn("unit tests", clean)
        self.assertIn("abstractions", clean)
        self.assertIn("conventional commit", clean)
        rust = " ".join(p.latent_invariant
                        for p in get_persona_principles("rust_systems"))
        self.assertIn("unsafe", rust)
        self.assertIn("Document every public item", rust)
        self.assertIn("Propagate errors explicitly", rust)
        web = " ".join(p.latent_invariant
                       for p in get_persona_principles("web_prototyper"))
        self.assertIn("semantic HTML", web)
        self.assertIn("Tailwind", web)
        self.assertIn("prop", web)

    def test_seed_persists_and_is_idempotent(self):
        store = PrincipleStore(":memory:")
        try:
            first = seed_persona(store, "clean_arch")
            self.assertEqual(len(first), 3)
            self.assertEqual(len(store.list_all()), 3)
            seed_persona(store, "clean_arch")
            self.assertEqual(len(store.list_all()), 3)
            ids = sorted(p.id for p in store.list_all())
            self.assertEqual(ids[0], "bp_persona_clean_arch_001")
        finally:
            store.close()

    def test_unknown_persona_rejected(self):
        store = PrincipleStore(":memory:")
        try:
            with self.assertRaises(ValueError):
                seed_persona(store, "nope")
            with self.assertRaises(ValueError):
                get_persona("nope")
        finally:
            store.close()


def _needs_qt(test):
    """Skip the test when Qt bindings are unavailable (offscreen)."""
    try:
        from desktop_app.tabs._qt import load_qt
        load_qt()
    except ImportError:
        test.skipTest("PyQt6/PySide6 not installed")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from desktop_app.tabs._qt import load_qt
    _, QtCore, _, QtWidgets = load_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # Keep the wrapper alive on the test: dropping the last Python ref to
    # the QApplication can crash later widget construction natively.
    try:
        test._qt_app_ref = app
    except AttributeError:
        pass
    return QtCore, QtWidgets, app


def _wait_for(signal, QtCore, timeout_ms=15000):
    """Run a local event loop until the signal fires (or timeout)."""
    loop = QtCore.QEventLoop()
    box = {}

    def _got(*args):
        box["args"] = args
        loop.quit()

    signal.connect(_got)
    QtCore.QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    try:
        signal.disconnect(_got)
    except (TypeError, RuntimeError):
        pass
    return box.get("args")


class ShowcaseTest(unittest.TestCase):
    def test_scene_values_match_spec(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.showcase import ShowcaseScene
        self.assertEqual(ShowcaseScene.OBSERVE.value, "1_observe")
        self.assertEqual(ShowcaseScene.VOICE_COORDINATE.value, "2_voice")
        self.assertEqual(ShowcaseScene.COMPLIANT_DRIVE.value, "3_drive")
        self.assertEqual(ShowcaseScene.ESTOP_CORRECTION.value, "4_estop")
        self.assertEqual(ShowcaseScene.COMPLETE.value, "complete")

    def test_run_all_simulated_registers_estop_invariant(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.showcase import ShowcaseRunner
        state = AppState(store_path=":memory:")
        try:
            # Bogus repo: Scene 1 must fall back to persona seeding so
            # the showcase stays deterministic without git/network.
            runner = ShowcaseRunner(
                state, repo="/nonexistent-showcase-repo-xyz")
            scenes: list[str] = []
            logs: list[str] = []
            runner.scene_changed.connect(
                lambda scene, _exp: scenes.append(scene))
            runner.log_message.connect(logs.append)
            ok = runner.run_all(simulate_hardware=True)
            self.assertTrue(ok)
            self.assertEqual(scenes, ["1_observe", "2_voice", "3_drive",
                                      "4_estop", "complete"])
            self.assertTrue(any("seeding demo persona" in line
                                for line in logs))
            ids = [p.id for p in state.list_principles()]
            self.assertIn("bp_showcase_forbid_env_001", ids)
            self.assertTrue(any("bp_persona_clean_arch" in pid
                                for pid in ids))
            self.assertEqual(state.visor.state.value, "resting")
        finally:
            state.close()


class ActuateWorkerTest(unittest.TestCase):
    def test_format_countdown(self):
        from desktop_app.actuate_worker import format_countdown
        self.assertEqual(
            format_countdown(3),
            "Starting cobot drive in 3s... "
            "Press ESC or touch mouse to stop")

    def test_drive_text_blocking_glides_and_types(self):
        from desktop_app.actuate_worker import drive_text_blocking
        from mimir.actuator import CursorPoint, DesktopActuator

        class _Mouse:
            def __init__(self):
                self.pos = (10, 10)
                self.moves: list[tuple[int, int]] = []

            def position(self):
                return self.pos

            def move_to(self, x, y):
                self.moves.append((x, y))
                self.pos = (x, y)

            def click(self):
                pass

        class _Keys:
            def __init__(self):
                self.chars: list[str] = []

            def write_char(self, char):
                self.chars.append(char)

        class _Safe:
            def arm(self, on_trip):
                pass

            def disarm(self):
                pass

        mouse, keys = _Mouse(), _Keys()
        actuator = DesktopActuator(mouse=mouse, keyboard=keys,
                                   failsafe=_Safe())
        try:
            report = drive_text_blocking(actuator, "hi", glide=True,
                                         base_wpm=1000)
        finally:
            actuator.close()
        self.assertEqual(report["typed_chars"], 2)
        self.assertTrue(report["glide"])
        self.assertEqual(keys.chars, ["h", "i"])
        self.assertTrue(mouse.moves)

    def test_drive_worker_cancel_during_countdown(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.actuate_worker import create_drive_worker
        worker = create_drive_worker("hello", countdown_sec=5,
                                     base_wpm=1000)
        try:
            QtCore.QTimer.singleShot(200, worker.cancel)
            worker.start()
            fired = _wait_for(worker.drive_interrupted, QtCore)
            worker.wait(5000)
            self.assertIsNotNone(fired)
            self.assertIn("countdown", fired[0])
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def test_drive_worker_finished_with_dry_backends(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.actuate_worker import create_drive_worker
        from mimir.actuator import DesktopActuator

        class _Mouse:
            def __init__(self):
                self.pos = (10, 10)

            def position(self):
                return self.pos

            def move_to(self, x, y):
                self.pos = (x, y)

            def click(self):
                pass

        class _Keys:
            def __init__(self):
                self.chars: list[str] = []

            def write_char(self, char):
                self.chars.append(char)

        class _Safe:
            def arm(self, on_trip):
                pass

            def disarm(self):
                pass

        keys = _Keys()
        worker = create_drive_worker(
            "hi", countdown_sec=0, base_wpm=1000,
            actuator_factory=lambda: DesktopActuator(
                mouse=_Mouse(), keyboard=keys, failsafe=_Safe()))
        try:
            worker.start()
            fired = _wait_for(worker.drive_finished, QtCore)
            worker.wait(5000)
            self.assertIsNotNone(fired)
            self.assertEqual(fired[0]["typed_chars"], 2)
            self.assertEqual(keys.chars, ["h", "i"])
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass


class VoiceWorkerTest(unittest.TestCase):
    def test_speak_worker_delivers_and_fires_on_start(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.voice_worker import create_speak_worker
        from mimir.voice import VoiceCoordinator
        spoken: list[str] = []
        voice = VoiceCoordinator(
            stt_backend=lambda timeout: None,
            tts_backend=spoken.append,
        )
        worker = create_speak_worker(voice, "hello cobot")
        try:
            started: list[bool] = []
            worker.speak_started.connect(lambda: started.append(True))
            worker.start()
            self.assertIsNotNone(_wait_for(worker.speak_finished, QtCore))
            worker.wait(5000)
            self.assertEqual(spoken, ["hello cobot"])
            self.assertEqual(started, [True])
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def test_speak_worker_reports_backend_failure(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.voice_worker import create_speak_worker
        from mimir.voice import VoiceCoordinator

        def _broken(text):
            raise RuntimeError("no speakers")

        voice = VoiceCoordinator(
            stt_backend=lambda timeout: None, tts_backend=_broken)
        worker = create_speak_worker(voice, "hello")
        try:
            worker.start()
            fired = _wait_for(worker.speak_error, QtCore)
            worker.wait(5000)
            self.assertIsNotNone(fired)
            self.assertIn("no speakers", fired[0])
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def test_listen_worker_returns_transcript(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.voice_worker import create_listen_worker
        from mimir.voice import VoiceCoordinator
        voice = VoiceCoordinator(
            stt_backend=lambda timeout: "push my code",
            tts_backend=lambda text: None,
        )
        worker = create_listen_worker(voice, timeout_sec=1.0)
        try:
            worker.start()
            fired = _wait_for(worker.listen_finished, QtCore)
            worker.wait(5000)
            self.assertEqual(fired, (True, "push my code"))
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass


class Phase4UiTest(unittest.TestCase):
    def _status(self):
        class _Status:
            def showMessage(self, *args):
                pass

        return _Status()

    def _button_texts(self, tab, QtWidgets):
        return [b.text()
                for b in tab.findChildren(QtWidgets.QPushButton)]

    def test_home_has_showcase_button(self):
        _, QtWidgets, _ = _needs_qt(self)
        from desktop_app.tabs.home import build_home_tab

        class _Watcher:
            watching = False

            def start(self, repo, roots):
                return (True, "ok")

            def stop(self):
                pass

        state = AppState(store_path=":memory:")
        try:
            tab = build_home_tab(state, lambda _t: None, self._status(),
                                 _Watcher())
            try:
                self.assertTrue(
                    any(b in self._button_texts(tab, QtWidgets)
                        for b in ("▶ Run Live Habi Showcase", "▶ Run Live Cobot Showcase")))
            finally:
                tab.close()
                tab.deleteLater()
        finally:
            state.close()

    def test_replicate_has_drive_button(self):
        _, QtWidgets, _ = _needs_qt(self)
        from desktop_app.tabs.replicate import build_replicate_tab
        state = AppState(store_path=":memory:")
        try:
            tab = build_replicate_tab(state, self._status())
            try:
                self.assertTrue(
                    any(b in self._button_texts(tab, QtWidgets)
                        for b in ("⚡ Apply with Habi", "⚡ Apply with Cobot")))
            finally:
                tab.close()
                tab.deleteLater()
        finally:
            state.close()

    def test_memory_has_seed_persona_button(self):
        _, QtWidgets, _ = _needs_qt(self)
        from desktop_app.tabs.memory import build_memory_tab
        state = AppState(store_path=":memory:")
        try:
            tab = build_memory_tab(state, self._status())
            try:
                self.assertIn("Seed Demo Persona",
                              self._button_texts(tab, QtWidgets))
            finally:
                tab.close()
                tab.deleteLater()
        finally:
            state.close()

    def test_settings_model_offers_five_providers(self):
        _, QtWidgets, _ = _needs_qt(self)
        from desktop_app.tabs.settings import build_settings_tab
        state = AppState(store_path=":memory:")
        try:
            tab = build_settings_tab(state, self._status())
            try:
                provider_combo = None
                for combo in tab.findChildren(QtWidgets.QComboBox):
                    items = [combo.itemText(i)
                             for i in range(combo.count())]
                    if any("Offline Heuristic Scaffold" in t
                           for t in items):
                        provider_combo = (combo, items)
                        break
                self.assertIsNotNone(provider_combo)
                combo, items = provider_combo
                self.assertEqual(len(items), 5)
                self.assertTrue(any("OpenAI" in t for t in items))
                self.assertTrue(any("Anthropic" in t for t in items))
                self.assertTrue(any("Ollama" in t for t in items))
            finally:
                tab.close()
                tab.deleteLater()
        finally:
            state.close()


class UiWiringRegressionTest(unittest.TestCase):
    """Every visible control must do its advertised job (no dead UI)."""

    def _status(self):
        class _Status:
            def showMessage(self, *args):
                pass

        return _Status()

    def _find_button(self, tab, QtWidgets, text):
        for button in tab.findChildren(QtWidgets.QPushButton):
            if button.text() == text:
                return button
        return None

    # -- Qt-free: theme, prefs, voice, contrast ---------------------------
    def test_theme_and_accent_normalize(self):
        from desktop_app.theme import normalize_accent, normalize_theme
        self.assertEqual(normalize_theme("Slate Midnight (Dark Mode)"),
                         "dark")
        self.assertEqual(
            normalize_theme("Porcelain Light (Signature Spec)"), "light")
        self.assertEqual(normalize_theme("nope"), "light")
        self.assertEqual(normalize_accent("Sage"), "Sage")
        self.assertEqual(normalize_accent("  sky "), "Sky")
        self.assertEqual(normalize_accent("unknown"), "Indigo")

    def test_stylesheet_variants_differ_but_default_is_spec(self):
        from desktop_app.theme import qt_stylesheet
        default = qt_stylesheet()
        for token in ("#F5F5F7", "#0F172A", "#6366F1", "#F0EFFF"):
            self.assertIn(token, default)
        self.assertNotEqual(qt_stylesheet("dark"), default)
        self.assertNotEqual(qt_stylesheet("light", "Sage"), default)
        self.assertNotEqual(qt_stylesheet("dark", "Amber"), default)
        self.assertIn("#0F172A", qt_stylesheet("dark"))
        self.assertIn("#059669", qt_stylesheet("light", "Sage"))

    def test_prefs_new_fields_roundtrip(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "desktop.json")
            prefs = DesktopPrefs(
                voice_persona="en-GB-SoniaNeural", theme="dark",
                accent="Sage", spacious=False, chimes=False,
                contrast_baseline=False)
            prefs.save(path)
            loaded = DesktopPrefs.load(path)
            self.assertEqual(loaded.voice_persona, "en-GB-SoniaNeural")
            self.assertEqual(loaded.theme, "dark")
            self.assertEqual(loaded.accent, "Sage")
            self.assertFalse(loaded.spacious)
            self.assertFalse(loaded.chimes)
            self.assertFalse(loaded.contrast_baseline)

    def test_voice_coordinator_honors_prefs(self):
        state = AppState(store_path=":memory:")
        try:
            state.prefs.voice_persona = "en-GB-SoniaNeural"
            state.prefs.language = "Hindi"
            hindi = state.voice_coordinator()
            self.assertEqual(hindi.voice, "en-GB-SoniaNeural")
            self.assertEqual(hindi.language, "hi")
            state.prefs.language = "English (US)"
            english = state.voice_coordinator()
            self.assertEqual(english.language, "en")
        finally:
            state.close()

    def test_contrast_baseline_pref_honored(self):
        state = AppState(store_path=":memory:")
        try:
            state.prefs.contrast_baseline = True
            on = state.replicate("cover retries with pytest",
                                 llm_fn=lambda prompt: "SYNTH",
                                 min_confidence=0.0)
            self.assertIsNotNone(on.contrast_baseline)
            state.prefs.contrast_baseline = False
            off = state.replicate("cover retries with pytest",
                                  llm_fn=lambda prompt: "SYNTH",
                                  min_confidence=0.0)
            self.assertIsNone(off.contrast_baseline)
        finally:
            state.close()

    def test_voice_params_stored(self):
        from mimir.voice import VoiceCoordinator
        default = VoiceCoordinator(stt_backend=lambda t: None,
                                   tts_backend=lambda t: None)
        self.assertEqual(default.voice, "en-US-AriaNeural")
        self.assertIsNone(default.language)
        custom = VoiceCoordinator(stt_backend=lambda t: None,
                                  tts_backend=lambda t: None,
                                  voice="en-US-GuyNeural", language="hi")
        self.assertEqual(custom.voice, "en-US-GuyNeural")
        self.assertEqual(custom.language, "hi")

    # -- Qt: panels, empty states, settings wiring, lifecycle -------------
    def test_trust_filter_panel_is_visible(self):
        _, QtWidgets, _ = _needs_qt(self)
        from desktop_app.tabs.trust import build_trust_tab
        state = AppState(store_path=":memory:")
        try:
            tab = build_trust_tab(state, self._status())
            try:
                button = self._find_button(tab, QtWidgets, "Redact preview")
                self.assertIsNotNone(button)
                widget: object = button
                while widget is not None and widget is not tab:
                    self.assertFalse(widget.isHidden())
                    widget = widget.parent()
                sample = self._find_button(tab, QtWidgets, "Insert sample")
                self.assertIsNotNone(sample)
                self.assertFalse(sample.isHidden())
            finally:
                tab.close()
                tab.deleteLater()
        finally:
            state.close()

    def test_observe_error_panel_shows_on_failure(self):
        _, QtWidgets, _ = _needs_qt(self)
        from desktop_app.tabs.observe import build_observe_tab

        class _Watcher:
            watching = False
            last_error = ""

            def start(self, repo, roots, max_commits=10):
                return (True, "ok")

            def stop(self):
                pass

        state = AppState(store_path=":memory:")
        try:
            tab = build_observe_tab(state, self._status(),
                                    lambda _t: None, _Watcher())
            try:
                edits = [e for e in tab.findChildren(QtWidgets.QLineEdit)
                         if "git checkout" in e.placeholderText()]
                self.assertTrue(edits)
                edits[0].setText("definitely-not-a-repo-xyz")
                once = self._find_button(tab, QtWidgets, "Observe now")
                self.assertIsNotNone(once)
                once.click()
                QtWidgets.QApplication.processEvents()
                dismiss = self._find_button(tab, QtWidgets, "Dismiss")
                self.assertIsNotNone(dismiss)
                panel = dismiss.parent()
                while panel is not None and not panel.isWidgetType():
                    panel = panel.parent()
                self.assertIsNotNone(panel)
                self.assertFalse(panel.isHidden())
                dismiss.click()
                QtWidgets.QApplication.processEvents()
                self.assertTrue(panel.isHidden())
            finally:
                tab.close()
                tab.deleteLater()
        finally:
            state.close()

    def test_observe_empty_feed_has_working_action(self):
        _, QtWidgets, _ = _needs_qt(self)
        from desktop_app.tabs.observe import build_observe_tab

        class _Watcher:
            watching = False
            last_error = ""

            def start(self, repo, roots, max_commits=10):
                return (True, "ok")

            def stop(self):
                pass

        state = AppState(store_path=":memory:")
        try:
            tab = build_observe_tab(state, self._status(),
                                    lambda _t: None, _Watcher())
            try:
                goto = self._find_button(tab, QtWidgets, "Choose workspace →")
                self.assertIsNotNone(goto)
                stacks = tab.findChildren(QtWidgets.QStackedWidget)
                self.assertEqual(len(stacks), 1)
                self.assertEqual(stacks[0].count(), 3)
                goto.click()
                QtWidgets.QApplication.processEvents()
                self.assertEqual(stacks[0].currentIndex(), 2)
            finally:
                tab.close()
                tab.deleteLater()
        finally:
            state.close()

    def test_memory_empty_state_seeds_persona(self):
        _, QtWidgets, _ = _needs_qt(self)
        from desktop_app.tabs.memory import build_memory_tab
        state = AppState(store_path=":memory:")
        try:
            tab = build_memory_tab(state, self._status())
            try:
                seeds = [b for b in tab.findChildren(QtWidgets.QPushButton)
                         if b.text() == "Seed Demo Persona"
                         and b.objectName() == "ink"]
                self.assertEqual(len(seeds), 1)
                seeds[0].click()
                QtWidgets.QApplication.processEvents()
                self.assertEqual(len(state.list_principles()), 3)
            finally:
                tab.close()
                tab.deleteLater()
        finally:
            state.close()

    def test_settings_controls_persist_and_apply(self):
        _, QtWidgets, _ = _needs_qt(self)
        from desktop_app.tabs.settings import build_settings_tab
        state = AppState(store_path=":memory:")
        try:
            with mock.patch.object(DesktopPrefs, "save", lambda self, p=None: None):
                tab = build_settings_tab(state, self._status())
                try:
                    sage = self._find_button(tab, QtWidgets, "  Sage  ")
                    self.assertIsNotNone(sage)
                    sage.click()
                    self.assertEqual(state.prefs.accent, "Sage")
                    combos = [c for c in tab.findChildren(QtWidgets.QComboBox)
                              if any("Slate Midnight" in c.itemText(i)
                                     for i in range(c.count()))]
                    self.assertEqual(len(combos), 1)
                    combos[0].setCurrentIndex(1)
                    self.assertEqual(state.prefs.theme, "dark")
                    labels = {}
                    for box in tab.findChildren(QtWidgets.QCheckBox):
                        frame = box.parent()
                        if frame is None:
                            continue
                        for label in frame.findChildren(QtWidgets.QLabel):
                            if label.objectName() == "setting_label":
                                labels[label.text()] = box
                    expected = {
                        "Ambient event chimes": "chimes",
                        "Contrastive baseline checking": "contrast_baseline",
                        "Spacious page margins": "spacious",
                    }
                    for label, field in expected.items():
                        self.assertIn(label, labels)
                        box = labels[label]
                        before = getattr(state.prefs, field)
                        box.setChecked(not before)
                        self.assertEqual(getattr(state.prefs, field),
                                         not before)
                    voices = [c for c in tab.findChildren(QtWidgets.QComboBox)
                              if any("AriaNeural" in c.itemText(i)
                                     for i in range(c.count()))]
                    self.assertEqual(len(voices), 1)
                    voices[0].setCurrentIndex(2)
                    self.assertEqual(state.prefs.voice_persona,
                                     "en-GB-SoniaNeural")
                finally:
                    tab.close()
                    tab.deleteLater()
        finally:
            state.close()

    def test_mainwindow_close_is_idempotent(self):
        _needs_qt(self)
        from desktop_app.app import MainWindow
        main = MainWindow(AppState(store_path=":memory:"))
        try:
            window = main.build()
            self.assertIsNotNone(window)
            main.close()
            main.close()
            main.shutdown()
            self.assertTrue(main._closing)
        finally:
            main.state.close()

    def test_showcase_cancel_stops_at_boundary(self):
        _needs_qt(self)
        from desktop_app.showcase import ShowcaseRunner
        state = AppState(store_path=":memory:")
        try:
            runner = ShowcaseRunner(state, speak_live=False)
            runner.cancel()
            self.assertFalse(runner.run_all(simulate_hardware=True))
        finally:
            state.close()

    def test_density_switches_page_geometry(self):
        _, QtWidgets, app = _needs_qt(self)
        from desktop_app.tabs._layout import (
            COMPACT_MARGINS,
            PAGE_MARGINS,
            page,
            set_density,
        )
        try:
            set_density(True)
            root, layout = page()
            try:
                margins = layout.contentsMargins()
                self.assertEqual((margins.left(), margins.top()),
                                 PAGE_MARGINS[:2])
                set_density(False)
                margins = layout.contentsMargins()
                self.assertEqual((margins.left(), margins.top()),
                                 COMPACT_MARGINS[:2])
            finally:
                root.close()
                root.deleteLater()
        finally:
            set_density(True)


if __name__ == "__main__":
    unittest.main()
