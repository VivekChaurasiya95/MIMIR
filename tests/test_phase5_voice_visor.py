"""Tests for Phase 5: voice + visor integration.

Covers the split off-main-thread workers (AudioCaptureWorker /
WhisperTranscribeWorker / SpeechSynthesisWorker), the offline Piper
TTS backend, the Phase-5 five-state visor mapping
(RESTING / LISTENING / THINKING / ACTING / HALTED onto the live
six-state machine), and voice intents routed through the same
auth/planning pipeline as typed text.

Headless tests run anywhere; Qt worker tests run offscreen when a Qt
binding exists and skip otherwise. No microphone, Whisper model,
network TTS, or Piper voice model is required — all hardware paths
use scripted fakes.
"""

from __future__ import annotations

import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mimir.actions import ActionRegistry, ActionStatus
from mimir.events import Event, EventBus, EventType, PHASE5_VISOR_BY_EVENT
from mimir.ui.visor import (
    PHASE5_LABELS,
    PHASE5_STATES,
    PHASE5_TO_VISOR,
    VISOR_COLORS,
    VISOR_TO_PHASE5,
    VisorController,
    VisorState,
    phase5_of,
    set_phase5_state,
)
from mimir.voice import VoiceCoordinator, VoiceError
from mimir.voice_action import (
    VoiceActionError,
    VoiceActionService,
    parse_structured_intent,
)


def _noop_voice() -> VoiceCoordinator:
    return VoiceCoordinator(
        stt_backend=lambda timeout: None, tts_backend=lambda text: None
    )


class _Collector:
    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe(handler=self._record, name="test-collector")

    def _record(self, event: Event) -> None:
        self.events.append(event)

    def of_type(self, event_type: EventType) -> list[Event]:
        return [e for e in self.events if e.event_type is event_type]


class VisorPhase5Test(unittest.TestCase):
    def test_acting_and_halted_alias_live_faces(self) -> None:
        visor = VisorController()
        visor.set_state("acting")
        self.assertEqual(visor.state, VisorState.DRIVING)
        self.assertEqual(visor.color, "#F59E0B")
        visor.set_state("halted")
        self.assertEqual(visor.state, VisorState.ALERT)
        self.assertEqual(visor.color, "#EF4444")

    def test_aliases_tolerate_case_and_whitespace(self) -> None:
        visor = VisorController(initial=" ACTING ")
        self.assertEqual(visor.state, VisorState.DRIVING)
        visor.set_state(" HaLtEd ")
        self.assertEqual(visor.state, VisorState.ALERT)

    def test_unknown_states_still_rejected(self) -> None:
        visor = VisorController()
        with self.assertRaises(ValueError):
            visor.set_state("dancing")
        with self.assertRaises(ValueError):
            VisorController(initial="dancing")

    def test_live_colors_unchanged(self) -> None:
        self.assertEqual(
            VISOR_COLORS,
            {
                VisorState.RESTING: "#555555",
                VisorState.OBSERVING: "#3B82F6",
                VisorState.LISTENING: "#10B981",
                VisorState.THINKING: "#8B5CF6",
                VisorState.DRIVING: "#F59E0B",
                VisorState.ALERT: "#EF4444",
            },
        )

    def test_phase5_tables(self) -> None:
        self.assertEqual(
            PHASE5_STATES,
            ("resting", "listening", "thinking", "acting", "halted"),
        )
        self.assertEqual(
            PHASE5_TO_VISOR,
            {
                "resting": "resting",
                "listening": "listening",
                "thinking": "thinking",
                "acting": "driving",
                "halted": "alert",
            },
        )
        self.assertEqual(VISOR_TO_PHASE5["driving"], "acting")
        self.assertEqual(VISOR_TO_PHASE5["alert"], "halted")
        self.assertEqual(VISOR_TO_PHASE5["observing"], "resting")
        self.assertEqual(PHASE5_LABELS["acting"], "Acting")
        self.assertEqual(PHASE5_LABELS["halted"], "Halted")

    def test_phase5_of_matrix(self) -> None:
        cases = [
            (VisorState.RESTING, "resting"),
            (VisorState.OBSERVING, "resting"),
            (VisorState.LISTENING, "listening"),
            (VisorState.THINKING, "thinking"),
            (VisorState.DRIVING, "acting"),
            (VisorState.ALERT, "halted"),
            ("acting", "acting"),
            ("halted", "halted"),
            ("driving", "acting"),
        ]
        for given, expected in cases:
            with self.subTest(given=given):
                self.assertEqual(phase5_of(given), expected)

    def test_set_phase5_state_drives_controller(self) -> None:
        visor = VisorController()
        self.assertEqual(set_phase5_state(visor, "acting"), "driving")
        self.assertEqual(visor.state, VisorState.DRIVING)
        self.assertEqual(set_phase5_state(visor, " HALTED "), "alert")
        self.assertEqual(visor.state, VisorState.ALERT)
        with self.assertRaises(ValueError):
            set_phase5_state(visor, "dancing")
        with self.assertRaises(ValueError):
            set_phase5_state(visor, 42)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            set_phase5_state(object(), "acting")

    def test_controller_phase5_properties(self) -> None:
        visor = VisorController()
        self.assertEqual(visor.phase5, "resting")
        self.assertEqual(visor.phase5_label, "Resting")
        visor.set_phase5("acting")
        self.assertEqual(visor.state, VisorState.DRIVING)
        self.assertEqual(visor.phase5, "acting")
        self.assertEqual(visor.phase5_label, "Acting")
        visor.set_phase5("halted")
        self.assertEqual(visor.phase5, "halted")
        with self.assertRaises(ValueError):
            visor.set_phase5("dancing")


class VisorLiveBindingTest(unittest.TestCase):
    def test_events_drive_phase5_faces(self) -> None:
        from mimir.events import bind_visor_to_bus

        bus = EventBus()
        visor = VisorController()
        tokens = bind_visor_to_bus(visor, bus)
        self.addCleanup(lambda: [t.unsubscribe() for t in tokens])
        try:
            bus.emit(EventType.VOICE_CAPTURED, "test", {})
            self.assertEqual(visor.state, VisorState.LISTENING)
            self.assertEqual(visor.phase5, "listening")
            bus.emit(EventType.INTENT_PARSED, "test", {})
            self.assertEqual(visor.state, VisorState.THINKING)
            bus.emit(EventType.ACTION_STARTED, "test", {})
            self.assertEqual(visor.state, VisorState.DRIVING)
            self.assertEqual(visor.phase5, "acting")
            bus.emit(EventType.E_STOP_TRIGGERED, "test", {})
            self.assertEqual(visor.state, VisorState.ALERT)
            self.assertEqual(visor.phase5, "halted")
            bus.emit(EventType.ACTION_COMPLETED, "test", {})
            self.assertEqual(visor.state, VisorState.RESTING)
        finally:
            for token in tokens:
                token.unsubscribe()

    def test_phase5_event_table_covers_every_event(self) -> None:
        self.assertEqual(set(PHASE5_VISOR_BY_EVENT), set(EventType))
        self.assertEqual(PHASE5_VISOR_BY_EVENT[EventType.VOICE_CAPTURED],
                         "listening")
        self.assertEqual(PHASE5_VISOR_BY_EVENT[EventType.ACTION_STARTED],
                         "acting")
        self.assertEqual(PHASE5_VISOR_BY_EVENT[EventType.E_STOP_TRIGGERED],
                         "halted")
        for name in PHASE5_VISOR_BY_EVENT.values():
            self.assertIn(name, PHASE5_STATES)


class PiperBackendTest(unittest.TestCase):
    def test_piper_is_a_known_backend(self) -> None:
        voice = VoiceCoordinator(tts_backend="piper")
        self.assertEqual(voice.tts_backend, "piper")
        with self.assertRaises(VoiceError):
            VoiceCoordinator(tts_backend="festival")

    def test_missing_model_raises_with_guidance(self) -> None:
        voice = VoiceCoordinator(tts_backend="piper")
        saved = dict(os.environ)
        os.environ.pop("PIPER_MODEL", None)
        os.environ.pop("PIPER_VOICE", None)
        try:
            with self.assertRaisesRegex(VoiceError, "piper"):
                voice.speak("hello")
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def test_model_path_resolution(self) -> None:
        import tempfile

        from mimir.voice import _piper_model_path

        self.assertIsNone(_piper_model_path("en-US-AriaNeural"))
        self.assertIsNone(_piper_model_path(""))
        with tempfile.NamedTemporaryFile(
            suffix=".onnx", delete=False
        ) as handle:
            model_path = handle.name
        self.addCleanup(os.unlink, model_path)
        self.assertEqual(_piper_model_path(model_path),
                         os.path.abspath(model_path))
        saved = os.environ.get("PIPER_MODEL")
        os.environ["PIPER_MODEL"] = model_path
        try:
            self.assertEqual(_piper_model_path("whatever"),
                             os.path.abspath(model_path))
        finally:
            if saved is None:
                os.environ.pop("PIPER_MODEL", None)
            else:
                os.environ["PIPER_MODEL"] = saved

    def test_python_package_missing_returns_false(self) -> None:
        import builtins

        from mimir.voice import _piper_synthesize_python

        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name in ("piper", "piper_tts"):
                raise ImportError(f"blocked for test: {name}")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = blocked
        try:
            self.assertFalse(
                _piper_synthesize_python("m.onnx", "hi", "o.wav")
            )
        finally:
            builtins.__import__ = real_import

    def test_full_piper_path_with_fakes(self) -> None:
        import sys
        import tempfile
        import types
        import wave

        import mimir.voice as voices

        with tempfile.NamedTemporaryFile(
            suffix=".onnx", delete=False
        ) as handle:
            model_path = handle.name
        self.addCleanup(os.unlink, model_path)

        fake = types.ModuleType("piper")

        class _FakeVoice:
            @staticmethod
            def load(model):
                assert model == model_path
                return _FakeVoice()

            def synthesize_wav(self, text, wav_path):
                assert text == "hello piper"
                with wave.open(wav_path, "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(22050)
                    handle.writeframes(b"\x00\x00" * 100)

        fake.PiperVoice = _FakeVoice
        played: list[str] = []

        class _DoneThread:
            def join(self, timeout=None):
                return None

        real_play = voices._play_audio_headless
        voices._play_audio_headless = (  # type: ignore[assignment]
            lambda path: (played.append(path), _DoneThread())[1]
        )
        sys.modules["piper"] = fake
        try:
            voice = VoiceCoordinator(tts_backend="piper",
                                     voice=model_path)
            voice.speak("hello piper")
        finally:
            sys.modules.pop("piper", None)
            voices._play_audio_headless = real_play  # type: ignore[assignment]
        self.assertEqual(len(played), 1)
        self.assertTrue(played[0].endswith(".wav"))

    def test_capture_backend_validation(self) -> None:
        voice = _noop_voice()
        with self.assertRaises(ValueError):
            voice.capture_audio(timeout_sec=1.0, backend="bogus")


class VoicePlanningParityTest(unittest.TestCase):
    def _service(self, bus, registry, said, **kwargs):
        defaults: dict = {"speak_fn": said.append, "speak_async": False}
        defaults.update(kwargs)
        return VoiceActionService(
            _noop_voice(), registry, bus=bus,
            session_id="sess_p5", **defaults,
        )

    def test_push_code_plans_and_proposes_like_text(self) -> None:
        import mimir.planning as planning

        bus = EventBus()
        collector = _Collector(bus)
        registry = ActionRegistry(bus=bus, session_id="sess_p5")
        said: list[str] = []
        planner = planning.ActionPlanner(store=None, allowed_roots=[])
        service = self._service(
            bus, registry, said,
            plan_fn=lambda text: planner.plan(
                "push my code", operation="answer_question"),
            propose_fn=lambda plan: planning.ActionPlanner.propose(
                plan, registry),
        )
        decision = service.handle_transcript("push my code")
        self.assertTrue(decision.proposed_action_id)
        self.assertIn(decision.proposed_action_id, decision.reply)
        self.assertIn("go ahead", decision.reply)
        self.assertEqual(said, [decision.reply])
        spec = registry.get(decision.proposed_action_id)
        assert spec is not None
        self.assertEqual(spec.status, ActionStatus.PENDING)
        proposed = collector.of_type(EventType.ACTION_PROPOSED)
        self.assertEqual(len(proposed), 1)
        self.assertEqual(proposed[0].payload["action_id"],
                         decision.proposed_action_id)

    def test_voice_followup_approves_voice_proposal(self) -> None:
        import mimir.planning as planning

        bus = EventBus()
        registry = ActionRegistry(bus=bus, session_id="sess_p5")
        said: list[str] = []
        planner = planning.ActionPlanner(store=None, allowed_roots=[])
        service = self._service(
            bus, registry, said,
            plan_fn=lambda text: planner.plan(
                "push my code", operation="answer_question"),
            propose_fn=lambda plan: planning.ActionPlanner.propose(
                plan, registry),
        )
        first = service.handle_transcript("push my code")
        second = service.handle_transcript(
            f"go ahead with {first.proposed_action_id}")
        self.assertEqual(second.authorized_action_id,
                         first.proposed_action_id)
        fetched = registry.get(first.proposed_action_id)
        assert fetched is not None
        self.assertEqual(fetched.status, ActionStatus.AUTHORIZED)

    def test_medium_risk_voice_plan_needs_gui_checklist(self) -> None:
        import mimir.planning as planning

        bus = EventBus()
        registry = ActionRegistry(bus=bus, session_id="sess_p5")
        said: list[str] = []
        planner = planning.ActionPlanner(store=None, allowed_roots=[])
        service = self._service(
            bus, registry, said,
            plan_fn=lambda text: planner.plan(
                "push my code", operation="git_push"),
            propose_fn=lambda plan: planning.ActionPlanner.propose(
                plan, registry),
        )
        first = service.handle_transcript("push my code")
        self.assertTrue(first.proposed_action_id)
        self.assertIn("checklist", first.reply)
        # Same graduated autonomy as text: voice grants never cover
        # medium/high, so the spoken approval fails closed.
        second = service.handle_transcript(
            f"go ahead with {first.proposed_action_id}")
        self.assertEqual(second.authorized_action_id, "")
        self.assertIn("approve", second.reply)
        fetched = registry.get(first.proposed_action_id)
        assert fetched is not None
        self.assertEqual(fetched.status, ActionStatus.PENDING)
        # The GUI gesture (risk-appropriate grants) authorizes the
        # identical entry — one pipeline, two gestures.
        outcome = registry.authorize(
            first.proposed_action_id,
            granted_permissions=planning.GUI_GRANTED_PERMISSIONS,
        )
        self.assertTrue(outcome.ok)

    def test_planner_failure_falls_back_to_legacy_reply(self) -> None:
        bus = EventBus()
        registry = ActionRegistry(bus=bus)
        said: list[str] = []

        def _boom(text):
            raise RuntimeError("planner down")

        service = self._service(
            bus, registry, said, plan_fn=_boom,
            propose_fn=lambda plan: None,
        )
        decision = service.handle_transcript("push my code")
        self.assertEqual(decision.proposed_action_id, "")
        self.assertIn("Replicate", decision.reply)
        self.assertEqual(registry.list_pending(), [])

    def test_forbid_persists_and_never_blocks_reply(self) -> None:
        bus = EventBus()
        registry = ActionRegistry(bus=bus)
        said: list[str] = []
        persisted: list[str] = []
        service = self._service(
            bus, registry, said,
            forbid_fn=persisted.append,
        )
        decision = service.handle_transcript("never touch .env files")
        self.assertEqual(persisted, ["never touch .env files"])
        self.assertIn("Noted", decision.reply)

        def _boom(rule):
            raise RuntimeError("store down")

        failing = self._service(
            bus, registry, said, forbid_fn=_boom,
        )
        again = failing.handle_transcript("never touch .env files")
        self.assertIn("Noted", again.reply)

    def test_invalid_planner_callbacks_rejected(self) -> None:
        registry = ActionRegistry()
        with self.assertRaises(VoiceActionError):
            VoiceActionService(_noop_voice(), registry, plan_fn="x")
        with self.assertRaises(VoiceActionError):
            VoiceActionService(_noop_voice(), registry, propose_fn="x")
        with self.assertRaises(VoiceActionError):
            VoiceActionService(_noop_voice(), registry, forbid_fn="x")

    def test_state_voice_service_wires_text_pipeline(self) -> None:
        from desktop_app.state import AppState

        state = AppState(store_path=":memory:")
        try:
            said: list[str] = []
            service = state.voice_service(
                _noop_voice(), speak_fn=said.append, speak_async=False)
            self.assertIsNotNone(service._plan_fn)
            self.assertIsNotNone(service._propose_fn)
            self.assertIsNotNone(service._forbid_fn)
            # Opt-out restores the legacy reply-only path.
            legacy = state.voice_service(
                _noop_voice(), speak_fn=said.append, speak_async=False,
                plan_fn=None, propose_fn=None, forbid_fn=None,
            )
            decision = legacy.handle_transcript("push my code")
            self.assertEqual(decision.proposed_action_id, "")
        finally:
            state.close()

    def test_state_handle_voice_transcript_end_to_end(self) -> None:
        from desktop_app.state import AppState

        state = AppState(store_path=":memory:")
        try:
            bus = EventBus()
            collector = _Collector(bus)
            state.event_bus = bus
            assert state.action_registry is not None
            state.action_registry.bus = bus
            said: list[str] = []
            decision = state.handle_voice_transcript(
                "push my code", _noop_voice(),
                speak_fn=said.append, speak_async=False,
            )
            # Planner runs on the caller's thread; with an empty
            # :memory: store the plan still drafts (read-only low
            # risk) and proposes on the shared registry.
            self.assertTrue(decision.proposed_action_id)
            spec = state.action_registry.get(
                decision.proposed_action_id)
            assert spec is not None
            self.assertEqual(spec.status, ActionStatus.PENDING)
            self.assertEqual(
                len(collector.of_type(EventType.ACTION_PROPOSED)), 1)
            self.assertEqual(
                len(collector.of_type(EventType.INTENT_PARSED)), 1)
        finally:
            state.close()

    def test_forbid_through_state_persists_invariant(self) -> None:
        from desktop_app.state import AppState

        state = AppState(store_path=":memory:")
        try:
            said: list[str] = []
            decision = state.handle_voice_transcript(
                "never stage .env files", _noop_voice(),
                speak_fn=said.append, speak_async=False,
            )
            self.assertIn("Noted", decision.reply)
            found = [p for p in state.list_principles()
                     if "never stage .env files" in
                     (p.latent_invariant or "").lower()]
            self.assertTrue(found, "forbid persists a negative invariant")
        finally:
            state.close()


def _load_qt():  # raises SkipTest when bindings are missing
    try:
        from desktop_app.tabs._qt import load_qt

        _, QtCore, _, QtWidgets = load_qt()
    except ImportError:
        raise unittest.SkipTest("PyQt6/PySide6 not installed")
    app = (QtWidgets.QApplication.instance()
           or QtWidgets.QApplication([]))
    return QtCore, QtWidgets, app


def _pump_until(app, done, timeout_s=5.0):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        app.processEvents()
        if done():
            return True
        time.sleep(0.01)
    app.processEvents()
    return done()


class _ScriptedCaptureVoice(VoiceCoordinator):
    def __init__(self, wav, **kwargs):
        super().__init__(**kwargs)
        self._wav = wav
        self.thread_ids: list[int] = []
        self.backends: list[str] = []

    def capture_audio(self, timeout_sec=5.0, *, phrase_time_limit=None,
                      calibrate=True, backend="auto"):
        self.thread_ids.append(threading.get_ident())
        self.backends.append(backend)
        if isinstance(self._wav, Exception):
            raise self._wav
        return self._wav


class _ScriptedTranscribeVoice(VoiceCoordinator):
    def __init__(self, text, **kwargs):
        super().__init__(**kwargs)
        self._text = text
        self.thread_ids: list[int] = []

    def transcribe_wav_bytes(self, data):
        self.thread_ids.append(threading.get_ident())
        assert isinstance(data, bytes) and data
        if isinstance(self._text, Exception):
            raise self._text
        return self._text


class _ScriptedSpeakVoice(VoiceCoordinator):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.thread_ids: list[int] = []
        self.said: list[str] = []
        self.fail: Exception | None = None

    def speak(self, text, on_start=None):
        self.thread_ids.append(threading.get_ident())
        if on_start is not None:
            on_start()
        if self.fail is not None:
            raise self.fail
        self.said.append(text)


class SplitWorkersTest(unittest.TestCase):
    def test_audio_capture_runs_off_main_thread(self) -> None:
        _, QtWidgets, app = _load_qt()
        from desktop_app.voice_worker import create_audio_capture_worker

        main_id = threading.get_ident()
        bus = EventBus()
        collector = _Collector(bus)
        voice = _ScriptedCaptureVoice(
            b"RIFF-fake-wav",
            stt_backend=lambda t: None, tts_backend=lambda t: None,
        )
        worker = create_audio_capture_worker(
            voice, timeout_sec=2.0, backend="sounddevice",
            parent=None, bus=bus, session_id="sess_p5",
        )
        got: list[bytes] = []
        worker.capture_finished.connect(got.append)
        try:
            worker.start()
            self.assertTrue(
                _pump_until(app, lambda: bool(got)),
                "capture_finished never arrived",
            )
            worker.wait(5000)
        finally:
            worker.deleteLater()
        self.assertEqual(got, [b"RIFF-fake-wav"])
        self.assertEqual(len(voice.thread_ids), 1)
        self.assertNotEqual(voice.thread_ids[0], main_id)
        self.assertEqual(voice.backends, ["sounddevice"])
        captured = collector.of_type(EventType.VOICE_CAPTURED)
        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0].payload["heard"])

    def test_audio_capture_silence_error_cancel(self) -> None:
        _, QtWidgets, app = _load_qt()
        from desktop_app.voice_worker import create_audio_capture_worker

        silent = _ScriptedCaptureVoice(
            None, stt_backend=lambda t: None, tts_backend=lambda t: None)
        worker = create_audio_capture_worker(silent, timeout_sec=1.0)
        flags: list[str] = []
        worker.capture_silence.connect(lambda: flags.append("silence"))
        try:
            worker.start()
            self.assertTrue(_pump_until(app, lambda: bool(flags)))
            worker.wait(5000)
        finally:
            worker.deleteLater()
        self.assertEqual(flags, ["silence"])

        failing = _ScriptedCaptureVoice(
            RuntimeError("mic unplugged"),
            stt_backend=lambda t: None, tts_backend=lambda t: None)
        worker2 = create_audio_capture_worker(failing, timeout_sec=1.0)
        errors: list[str] = []
        worker2.capture_error.connect(errors.append)
        try:
            worker2.start()
            self.assertTrue(_pump_until(app, lambda: bool(errors)))
            worker2.wait(5000)
        finally:
            worker2.deleteLater()
        self.assertEqual(errors, ["mic unplugged"])

        worker3 = create_audio_capture_worker(silent, timeout_sec=1.0)
        cancelled: list[bool] = []
        worker3.capture_cancelled.connect(lambda: cancelled.append(True))
        worker3.cancel()
        try:
            worker3.start()
            self.assertTrue(_pump_until(app, lambda: bool(cancelled)))
            worker3.wait(5000)
        finally:
            worker3.deleteLater()
        self.assertEqual(cancelled, [True])

    def test_audio_capture_rejects_bad_inputs(self) -> None:
        _, _, _ = _load_qt()
        from desktop_app.voice_worker import create_audio_capture_worker

        with self.assertRaises(ValueError):
            create_audio_capture_worker("not-a-voice")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            create_audio_capture_worker(_noop_voice(), backend="sphinx")

    def test_whisper_transcribe_runs_off_main_thread(self) -> None:
        _, QtWidgets, app = _load_qt()
        from desktop_app.voice_worker import (
            create_whisper_transcribe_worker,
        )

        main_id = threading.get_ident()
        bus = EventBus()
        collector = _Collector(bus)
        voice = _ScriptedTranscribeVoice(
            "push my code",
            stt_backend=lambda t: None, tts_backend=lambda t: None,
        )
        worker = create_whisper_transcribe_worker(
            voice, b"RIFF-fake-wav", bus=bus, session_id="sess_p5")
        got: list[str] = []
        worker.transcribe_finished.connect(got.append)
        try:
            worker.start()
            self.assertTrue(
                _pump_until(app, lambda: bool(got)),
                "transcribe_finished never arrived",
            )
            worker.wait(5000)
        finally:
            worker.deleteLater()
        self.assertEqual(got, ["push my code"])
        self.assertEqual(len(voice.thread_ids), 1)
        self.assertNotEqual(voice.thread_ids[0], main_id)
        completed = collector.of_type(EventType.TRANSCRIPTION_COMPLETED)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].payload["text"], "push my code")

    def test_whisper_transcribe_silence_error_cancel(self) -> None:
        _, QtWidgets, app = _load_qt()
        from desktop_app.voice_worker import (
            create_whisper_transcribe_worker,
        )

        silent = _ScriptedTranscribeVoice(
            None, stt_backend=lambda t: None, tts_backend=lambda t: None)
        worker = create_whisper_transcribe_worker(silent, b"wav")
        flags: list[str] = []
        worker.transcribe_silence.connect(lambda: flags.append("silence"))
        try:
            worker.start()
            self.assertTrue(_pump_until(app, lambda: bool(flags)))
            worker.wait(5000)
        finally:
            worker.deleteLater()
        self.assertEqual(flags, ["silence"])

        failing = _ScriptedTranscribeVoice(
            RuntimeError("whisper offline"),
            stt_backend=lambda t: None, tts_backend=lambda t: None)
        worker2 = create_whisper_transcribe_worker(failing, b"wav")
        errors: list[str] = []
        worker2.transcribe_error.connect(errors.append)
        try:
            worker2.start()
            self.assertTrue(_pump_until(app, lambda: bool(errors)))
            worker2.wait(5000)
        finally:
            worker2.deleteLater()
        self.assertEqual(errors, ["whisper offline"])

        with self.assertRaises(ValueError):
            create_whisper_transcribe_worker(_noop_voice(), b"")
        with self.assertRaises(ValueError):
            create_whisper_transcribe_worker("nope", b"wav")  # type: ignore[arg-type]

    def test_speech_synthesis_runs_off_main_thread(self) -> None:
        _, QtWidgets, app = _load_qt()
        from desktop_app.voice_worker import (
            create_speech_synthesis_worker,
        )

        main_id = threading.get_ident()
        voice = _ScriptedSpeakVoice(
            stt_backend=lambda t: None, tts_backend=lambda t: None)
        worker = create_speech_synthesis_worker(voice, "On it.")
        events: list[str] = []
        worker.synthesis_started.connect(lambda: events.append("started"))
        worker.synthesis_finished.connect(lambda: events.append("finished"))
        try:
            worker.start()
            self.assertTrue(
                _pump_until(app, lambda: len(events) == 2),
                f"synthesis signals never arrived: {events}",
            )
            worker.wait(5000)
        finally:
            worker.deleteLater()
        self.assertEqual(events, ["started", "finished"])
        self.assertEqual(voice.said, ["On it."])
        self.assertEqual(len(voice.thread_ids), 1)
        self.assertNotEqual(voice.thread_ids[0], main_id)

    def test_speech_synthesis_error_cancel_and_validation(self) -> None:
        _, QtWidgets, app = _load_qt()
        from desktop_app.voice_worker import (
            create_speech_synthesis_worker,
        )

        failing = _ScriptedSpeakVoice(
            stt_backend=lambda t: None, tts_backend=lambda t: None)
        failing.fail = RuntimeError("speaker offline")
        worker = create_speech_synthesis_worker(failing, "hi")
        errors: list[str] = []
        worker.synthesis_error.connect(errors.append)
        try:
            worker.start()
            self.assertTrue(_pump_until(app, lambda: bool(errors)))
            worker.wait(5000)
        finally:
            worker.deleteLater()
        self.assertEqual(errors, ["speaker offline"])

        idle = _ScriptedSpeakVoice(
            stt_backend=lambda t: None, tts_backend=lambda t: None)
        worker2 = create_speech_synthesis_worker(idle, "hi")
        cancelled: list[bool] = []
        worker2.synthesis_cancelled.connect(
            lambda: cancelled.append(True))
        worker2.cancel()
        try:
            worker2.start()
            self.assertTrue(_pump_until(app, lambda: bool(cancelled)))
            worker2.wait(5000)
        finally:
            worker2.deleteLater()
        self.assertEqual(cancelled, [True])
        self.assertEqual(idle.said, [])

        with self.assertRaises(ValueError):
            create_speech_synthesis_worker(_noop_voice(), "   ")
        with self.assertRaises(ValueError):
            create_speech_synthesis_worker("nope", "hi")  # type: ignore[arg-type]


class StructuredIntentParityTest(unittest.TestCase):
    def test_push_code_resolves_without_registry_side_effects(self) -> None:
        registry = ActionRegistry()
        parsed = parse_structured_intent(
            "push my code", _noop_voice(), registry)
        self.assertEqual(parsed.intent, "PUSH_CODE")
        self.assertFalse(parsed.needs_clarification)
        self.assertEqual(registry.list_pending(), [])


if __name__ == "__main__":
    unittest.main()
