"""Tests for the voice coordination engine (Days 8-9).

Coordination logic runs through injected fake backends; live hardware
paths (microphone, Whisper, Edge-TTS) are covered only through their
missing-dependency errors, which are deterministic on any machine.
"""

import builtins
import unittest

from mimir.voice import INTENTS, VoiceCoordinator, VoiceError


def _coordinator(stt=None, tts=None):
    heard: list[float] = []
    said: list[str] = []

    def fake_stt(timeout):
        heard.append(timeout)
        return stt

    def fake_tts(text):
        said.append(text)

    coordinator = VoiceCoordinator(stt_backend=fake_stt, tts_backend=fake_tts)
    return coordinator, heard, said


class IntentTest(unittest.TestCase):
    def setUp(self):
        self.voice = VoiceCoordinator(
            stt_backend=lambda timeout: None, tts_backend=lambda text: None
        )

    def test_intent_matrix(self):
        cases = [
            ("Hey MIMIR, have you learned how I commit and push my code?",
             "status_inquiry"),
            ("What did you learn today?", "status_inquiry"),
            ("show me your status", "status_inquiry"),
            ("Push code to GitHub", "push_code"),
            ("commit and push", "push_code"),
            ("Go ahead.", "approve"),
            ("yes, do it", "approve"),
            ("Stop!", "stop"),
            ("STOP!!!", "stop"),
            ("freeze right now", "stop"),
            ("Go to sleep", "sleep"),
            ("take a break", "sleep"),
            ("Never stage .env files.", "forbid"),
            ("Don't push without tests", "forbid"),
            ("hello there", "unknown"),
            ("remind me later", "unknown"),
        ]
        for utterance, expected in cases:
            with self.subTest(utterance=utterance):
                parsed = self.voice.parse_intent(utterance)
                self.assertEqual(parsed["intent"], expected)
                self.assertEqual(set(parsed), {"intent", "text"})
                self.assertIn(expected, INTENTS)

    def test_rejects_bad_input(self):
        for bad in ("", "   ", 123, None):
            with self.assertRaises(ValueError, msg=f"text={bad!r}"):
                self.voice.parse_intent(bad)


class ListenTest(unittest.TestCase):
    def test_transcript_passthrough(self):
        voice, heard, _ = _coordinator(stt="push my code")
        self.assertEqual(voice.listen_phrase(timeout_sec=2.0), "push my code")
        self.assertEqual(heard, [2.0])

    def test_silence_maps_to_none(self):
        voice, _, _ = _coordinator(stt=None)
        self.assertIsNone(voice.listen_phrase())
        blank, _, _ = _coordinator(stt="   ")
        self.assertIsNone(blank.listen_phrase())

    def test_rejects_bad_timeout(self):
        voice, _, _ = _coordinator()
        for bad in (0, -1.0, float("nan"), "soon", True):
            with self.assertRaises(ValueError, msg=f"timeout={bad!r}"):
                voice.listen_phrase(timeout_sec=bad)

    def test_backend_failures_raise_voice_error(self):
        def exploding(timeout):
            raise RuntimeError("mic unplugged")

        voice = VoiceCoordinator(
            stt_backend=exploding, tts_backend=lambda text: None
        )
        with self.assertRaises(VoiceError):
            voice.listen_phrase()
        bad_shape = VoiceCoordinator(
            stt_backend=lambda timeout: 123, tts_backend=lambda text: None
        )
        with self.assertRaises(VoiceError):
            bad_shape.listen_phrase()


class SpeakTest(unittest.TestCase):
    def test_delivers_text_and_fires_hook_first(self):
        events: list[str] = []

        def fake_tts(text):
            events.append(f"said:{text}")

        voice = VoiceCoordinator(
            stt_backend=lambda timeout: None, tts_backend=fake_tts
        )
        voice.speak("On it.", on_start=lambda: events.append("start"))
        self.assertEqual(events, ["start", "said:On it."])

    def test_hook_failure_never_silences_voice(self):
        voice, _, said = _coordinator()
        voice.speak("Still speaking.", on_start=lambda: 1 / 0)
        self.assertEqual(said, ["Still speaking."])

    def test_rejects_bad_inputs(self):
        voice, _, _ = _coordinator()
        with self.assertRaises(ValueError):
            voice.speak("  ")
        with self.assertRaises(ValueError):
            voice.speak("hi", on_start=42)

    def test_backend_failure_raises_voice_error(self):
        def exploding(text):
            raise RuntimeError("speaker offline")

        voice = VoiceCoordinator(
            stt_backend=lambda timeout: None, tts_backend=exploding
        )
        with self.assertRaises(VoiceError):
            voice.speak("hello")


class BackendValidationTest(unittest.TestCase):
    def test_rejects_unknown_backends(self):
        with self.assertRaises(VoiceError):
            VoiceCoordinator(stt_backend="sphinx_remote")
        with self.assertRaises(VoiceError):
            VoiceCoordinator(tts_backend="festival")
        with self.assertRaises(VoiceError):
            VoiceCoordinator(stt_backend=42)

    def test_missing_live_deps_raise_with_hint(self):
        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name in ("speech_recognition", "whisper", "edge_tts"):
                raise ImportError(f"blocked for test: {name}")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = blocked
        try:
            voice = VoiceCoordinator()
            with self.assertRaisesRegex(VoiceError, "SpeechRecognition"):
                voice.listen_phrase()
            with self.assertRaisesRegex(VoiceError, "edge-tts"):
                voice.speak("hello")
        finally:
            builtins.__import__ = real_import


if __name__ == "__main__":
    unittest.main()
