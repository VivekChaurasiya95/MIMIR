"""Tests for Home mic input wiring (capture backend + UI plumbing).

The mic button takes voice input through ``VoiceCoordinator.capture_audio``
(local mic) and ``transcribe_*`` (on-device Whisper), then routes the
transcript through the Home ask pipeline (intent -> registry-bound reply
-> TTS). These tests pin the backend probe, the sounddevice fallback used
when SpeechRecognition/PyAudio is absent, and the Home tab preflight that
keeps the button idle with actionable guidance when no input chain exists.
"""

from __future__ import annotations

import io
import os
import sys
import types
import unittest
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _ModuleBlock:
    """Context manager: make ``import <name>`` raise ImportError."""

    def __init__(self, *names: str) -> None:
        self.names = names
        self.saved: dict[str, object] = {}

    def __enter__(self) -> "_ModuleBlock":
        for name in self.names:
            self.saved[name] = sys.modules.get(name, self)
            sys.modules[name] = None  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: object) -> None:
        for name in self.names:
            saved = self.saved.get(name, self)
            if saved is self:
                sys.modules.pop(name, None)
            else:
                if saved is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = saved  # type: ignore[assignment]


class _SwapModules:
    """Context manager: surgically swap sys.modules entries, then restore.

    Only the touched keys are restored; modules legitimately imported
    mid-test (e.g. numpy, whose C extension cannot reload) are left in
    place, so later tests never observe a wiped import cache.
    """

    _MISSING = object()

    def __init__(self, swaps: dict[str, object]) -> None:
        self.swaps = swaps
        self.saved: dict[str, object] = {}

    def __enter__(self) -> "_SwapModules":
        for name in self.swaps:
            self.saved[name] = sys.modules.get(name, self._MISSING)
        for name, module in self.swaps.items():
            if module is None:
                sys.modules[name] = None  # type: ignore[assignment]
            else:
                sys.modules[name] = module  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: object) -> None:
        for name, original in self.saved.items():
            if original is self._MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original  # type: ignore[assignment]


def _fake_sr(names: list[str]) -> types.ModuleType:
    mod = types.ModuleType("speech_recognition")

    class _Mic:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def __enter__(self) -> "_Mic":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        @staticmethod
        def list_microphone_names() -> list[str]:
            return list(names)

    mod.Microphone = _Mic

    class _WaitTimeout(Exception):
        pass

    mod.WaitTimeoutError = _WaitTimeout
    return mod


def _fake_sounddevice(
    script: list[str],
    *,
    devices: list[dict[str, object]] | None = None,
    fail_devices: tuple[int, ...] = (),
    default_device: int | None = None,
) -> types.ModuleType:
    """Fake sounddevice; script entries are 'quiet'/'loud' per rec() call."""
    import numpy as np

    mod = types.ModuleType("sounddevice")
    calls: list[tuple[int, object, object]] = []
    if devices is None:
        devices = [{
            "name": "Fake SD Mic",
            "max_input_channels": 1,
            "default_samplerate": 16000.0,
        }]

    def query_devices(*a: object, **k: object) -> list[dict[str, object]]:
        assert devices is not None
        return [dict(entry) for entry in devices]

    def rec(frames: int, **k: object) -> "np.ndarray":
        device = k.get("device", 0)
        calls.append((int(frames), device, k.get("samplerate")))
        if device in fail_devices:
            raise RuntimeError("Error opening InputStream: invalid device")
        kind = script[len(calls) - 1] if len(calls) <= len(script) else "quiet"
        if kind == "loud":
            return np.full((int(frames), 1), 0.5, dtype=np.float32)
        return np.zeros((int(frames), 1), dtype=np.float32)

    mod.query_devices = query_devices  # type: ignore[attr-defined]
    mod.rec = rec  # type: ignore[attr-defined]
    mod.calls = calls  # type: ignore[attr-defined]
    if default_device is not None:
        import types as _t

        mod.default = _t.SimpleNamespace(  # type: ignore[attr-defined]
            device=(default_device, None))
    return mod


def _fake_ffmpeg_exe(test: unittest.TestCase) -> str:
    """A tiny stand-in ffmpeg binary for imageio_ffmpeg fakes."""
    import tempfile

    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    exe = os.path.join(tmp.name, "ffmpeg-test.exe")
    with open(exe, "wb") as handle:
        handle.write(b"fake-ffmpeg-binary")
    return exe


def _fake_imageio_ffmpeg(exe_path: str) -> types.ModuleType:
    mod = types.ModuleType("imageio_ffmpeg")
    mod.get_ffmpeg_exe = lambda: exe_path  # type: ignore[attr-defined]
    return mod


class AudioBackendStatusTest(unittest.TestCase):
    def test_shape_and_never_raises_without_backends(self) -> None:
        from mimir.voice import audio_backend_status

        with _ModuleBlock("speech_recognition", "pyaudio", "sounddevice"):
            status = audio_backend_status()
        self.assertEqual(
            set(status), {"available", "backend", "microphones", "hint"})
        self.assertFalse(status["available"])
        self.assertEqual(status["backend"], "none")
        self.assertEqual(status["microphones"], [])
        self.assertIn("pip install", status["hint"])

    def test_prefers_speech_recognition_when_present(self) -> None:
        from mimir.voice import audio_backend_status

        with _SwapModules({
            "speech_recognition": _fake_sr(["Fake Mic"]),
            "pyaudio": types.ModuleType("pyaudio"),
        }):
            status = audio_backend_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["backend"], "speech_recognition")
        self.assertEqual(status["microphones"], ["Fake Mic"])

    def test_falls_back_to_sounddevice_listing(self) -> None:
        from mimir.voice import audio_backend_status

        with _SwapModules({
            "speech_recognition": None,
            "pyaudio": None,
            "sounddevice": _fake_sounddevice([]),
        }):
            status = audio_backend_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["backend"], "sounddevice")
        self.assertEqual(status["microphones"], ["Fake SD Mic"])


class CaptureFallbackTest(unittest.TestCase):
    def test_capture_uses_sounddevice_when_sr_missing(self) -> None:
        from mimir.voice import VoiceCoordinator

        # calibrate quiet, onset on 3rd block, speech, then silence.
        script = (["quiet"] * 3 + ["loud"] * 4 + ["quiet"] * 12)
        with _SwapModules({
            "speech_recognition": None,
            "pyaudio": None,
            "sounddevice": _fake_sounddevice(script),
        }):
            wav = VoiceCoordinator().capture_audio(
                timeout_sec=2.0, phrase_time_limit=4.0)
        self.assertIsInstance(wav, bytes)
        assert wav is not None
        with wave.open(io.BytesIO(wav), "rb") as handle:
            self.assertEqual(handle.getnchannels(), 1)
            self.assertEqual(handle.getframerate(), 16000)
            self.assertGreater(handle.getnframes(), 0)

    def test_capture_skips_failing_default_device(self) -> None:
        from mimir.voice import VoiceCoordinator

        devices = [
            {"name": "Broken Default Mic", "max_input_channels": 1,
             "default_samplerate": 44100.0},
            {"name": "Fake SD Mic", "max_input_channels": 1,
             "default_samplerate": 16000.0},
        ]
        fake = _fake_sounddevice(
            ["quiet"] * 3 + ["loud"] * 3 + ["quiet"] * 12,
            devices=devices, fail_devices=(0,), default_device=0)
        with _SwapModules({
            "speech_recognition": None,
            "pyaudio": None,
            "sounddevice": fake,
        }):
            wav = VoiceCoordinator().capture_audio(
                timeout_sec=2.0, phrase_time_limit=4.0)
        self.assertIsInstance(wav, bytes)
        attempted = [device for _, device, _ in fake.calls]  # type: ignore[attr-defined]
        self.assertEqual(attempted[0], 0, "default tried first")
        self.assertIn(1, attempted, "falls over to the next device")
        assert wav is not None
        with wave.open(io.BytesIO(wav), "rb") as handle:
            self.assertEqual(handle.getnchannels(), 1)
            self.assertEqual(handle.getframerate(), 16000)

    def test_candidates_interleave_host_apis(self) -> None:
        from mimir.voice import _sounddevice_candidates

        devices = [
            {"name": "Mic A0", "max_input_channels": 1,
             "default_samplerate": 44100.0, "hostapi": 0},
            {"name": "Mic B0", "max_input_channels": 1,
             "default_samplerate": 44100.0, "hostapi": 0},
            {"name": "Mic A1", "max_input_channels": 1,
             "default_samplerate": 48000.0, "hostapi": 1},
            {"name": "Mic B1", "max_input_channels": 1,
             "default_samplerate": 48000.0, "hostapi": 1},
            {"name": "Microsoft Sound Mapper - Input",
             "max_input_channels": 2, "default_samplerate": 44100.0,
             "hostapi": 0},
        ]
        fake = _fake_sounddevice([], devices=devices)
        order = [index for index, _ in _sounddevice_candidates(fake)]
        self.assertEqual(order, [0, 2, 1, 3, 4])

    def test_capture_silence_returns_none(self) -> None:
        from mimir.voice import VoiceCoordinator

        with _SwapModules({
            "speech_recognition": None,
            "pyaudio": None,
            "sounddevice": _fake_sounddevice([]),
        }):
            heard = VoiceCoordinator().capture_audio(
                timeout_sec=0.3, phrase_time_limit=1.0)
        self.assertIsNone(heard)

    def test_capture_without_any_backend_hints_both_installs(self) -> None:
        from mimir.voice import VoiceCoordinator, VoiceError

        with _ModuleBlock("speech_recognition", "pyaudio", "sounddevice"):
            with self.assertRaises(VoiceError) as ctx:
                VoiceCoordinator().capture_audio(timeout_sec=1.0)
        message = str(ctx.exception)
        self.assertIn("PyAudio", message)
        self.assertIn("sounddevice", message)


class MicStatusTest(unittest.TestCase):
    def test_reports_missing_chain_with_guidance(self) -> None:
        from desktop_app.state import AppState

        state = AppState(store_path=":memory:")
        try:
            with _ModuleBlock("speech_recognition", "pyaudio",
                              "sounddevice", "whisper"):
                status = state.mic_status()
        finally:
            state.close()
        self.assertFalse(status["available"])
        self.assertIn("pip install", status["message"])

    def test_injected_backend_needs_no_hardware(self) -> None:
        from desktop_app.state import AppState
        from mimir.voice import VoiceCoordinator

        state = AppState(store_path=":memory:")
        try:
            orig = type(state).voice_coordinator
            type(state).voice_coordinator = (
                lambda self: VoiceCoordinator(
                    stt_backend=lambda t: None,
                    tts_backend=lambda text: None))
            try:
                with _ModuleBlock("speech_recognition", "pyaudio",
                                  "sounddevice", "whisper"):
                    status = state.mic_status()
            finally:
                type(state).voice_coordinator = orig
        finally:
            state.close()
        self.assertTrue(status["available"])
        self.assertEqual(status["backend"], "injected")

    def test_live_chain_reports_backend(self) -> None:
        from desktop_app.state import AppState

        state = AppState(store_path=":memory:")
        saved_path = os.environ.get("PATH", "")
        self.addCleanup(os.environ.__setitem__, "PATH", saved_path)
        try:
            with _SwapModules({
                "speech_recognition": _fake_sr(["Fake Mic"]),
                "pyaudio": types.ModuleType("pyaudio"),
                "whisper": types.ModuleType("whisper"),
                "imageio_ffmpeg": _fake_imageio_ffmpeg(
                    _fake_ffmpeg_exe(self)),
            }):
                status = state.mic_status()
        finally:
            state.close()
        self.assertTrue(status["available"])
        self.assertEqual(status["backend"], "speech_recognition")

    def test_missing_ffmpeg_reported(self) -> None:
        from unittest import mock

        from desktop_app.state import AppState

        state = AppState(store_path=":memory:")
        try:
            with _SwapModules({
                "speech_recognition": _fake_sr(["Fake Mic"]),
                "pyaudio": types.ModuleType("pyaudio"),
                "whisper": types.ModuleType("whisper"),
                "imageio_ffmpeg": None,
            }), mock.patch("shutil.which", return_value=None):
                status = state.mic_status()
        finally:
            state.close()
        self.assertFalse(status["available"])
        self.assertIn("ffmpeg", status["message"])


class HomeMicWiringTest(unittest.TestCase):
    def _load_qt(self):  # raises SkipTest when bindings are missing
        try:
            from desktop_app.tabs._qt import load_qt
            _, QtCore, _, QtWidgets = load_qt()
        except ImportError:
            raise unittest.SkipTest("PyQt6/PySide6 not installed")
        app = (QtWidgets.QApplication.instance()
               or QtWidgets.QApplication([]))
        return QtCore, QtWidgets, app

    def _pump(self, app, ms: int = 300) -> None:
        import time

        end = time.monotonic() + ms / 1000.0
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    def _build(self, QtWidgets, state, status):
        import types as _t

        from desktop_app.tabs.home import build_home_tab

        watcher = _t.SimpleNamespace(watching=False, last_error="")
        return build_home_tab(state, lambda _t: None, status, watcher)

    def _mic_btn(self, QtWidgets, root):
        return next(
            b for b in root.findChildren(QtWidgets.QPushButton)
            if b.objectName() == "mic_btn")

    def test_mic_unavailable_stays_idle_with_guidance(self) -> None:
        _, QtWidgets, app = self._load_qt()
        from desktop_app.state import AppState
        from mimir.voice import VoiceCoordinator

        state = AppState(store_path=":memory:")
        orig = type(state).voice_coordinator
        type(state).voice_coordinator = lambda self: VoiceCoordinator()
        messages: list[str] = []

        class _Status:
            def showMessage(self, text, timeout=0):
                messages.append(text)

        try:
            with _ModuleBlock("speech_recognition", "pyaudio",
                              "sounddevice", "whisper"):
                root = self._build(QtWidgets, state, _Status())
                try:
                    mic = self._mic_btn(QtWidgets, root)
                    mic.click()
                    self._pump(app, 400)
                    self.assertEqual(mic.text(), "\U0001F399")
                    self.assertTrue(
                        any("pip install" in m or "microphone" in m.lower()
                            for m in messages),
                        f"guidance shown, got {messages}")
                finally:
                    root.close()
                    root.deleteLater()
        finally:
            type(state).voice_coordinator = orig
            state.close()

    def test_mic_transcript_fills_explore_and_answers(self) -> None:
        _, QtWidgets, app = self._load_qt()
        from desktop_app.state import AppState
        from mimir.voice import VoiceCoordinator

        state = AppState(store_path=":memory:")
        orig = type(state).voice_coordinator
        type(state).voice_coordinator = (
            lambda self: VoiceCoordinator(
                stt_backend=lambda t: "what did you learn?",
                tts_backend=lambda text: None))
        messages: list[str] = []

        class _Status:
            def showMessage(self, text, timeout=0):
                messages.append(text)

        try:
            root = self._build(QtWidgets, state, _Status())
            try:
                mic = self._mic_btn(QtWidgets, root)
                mic.click()
                self._pump(app, 2500)
                edits = [e for e in root.findChildren(QtWidgets.QLineEdit)
                         if e.objectName() == "explore_input"]
                self.assertEqual(len(edits), 1)
                self.assertEqual(edits[0].text(), "what did you learn?")
                replies = [
                    lbl.text() for lbl in
                    root.findChildren(QtWidgets.QLabel)
                    if lbl.text().startswith(
                        f"{state.prefs.assistant_name}:")]
                self.assertTrue(replies, "mic transcript gets an answer")
            finally:
                root.close()
                root.deleteLater()
        finally:
            type(state).voice_coordinator = orig
            state.close()


if __name__ == "__main__":
    unittest.main()
