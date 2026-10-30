"""Voice coordination engine: hands-free MIMIR interaction (Days 8-9).

Speech-to-text (local Whisper) and text-to-speech (Edge-TTS) sit behind
the backend names ``"whisper_local"`` / ``"edge_tts"``; either backend
also accepts an injected callable so coordination logic (timeouts,
``on_start`` hooks, intent parsing) stays fully testable without a
microphone or speakers. :meth:`VoiceCoordinator.parse_intent` is pure
keyword logic mapping utterances to coordination intents.
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
from typing import Callable, Optional, Union

from ._common import check_str

__all__ = [
    "INTENTS",
    "VoiceCoordinator",
    "VoiceError",
]

#: Intents recognized by :meth:`VoiceCoordinator.parse_intent`.
INTENTS = (
    "stop",
    "forbid",
    "sleep",
    "status_inquiry",
    "push_code",
    "approve",
    "unknown",
)

_QUESTION_STARTERS = (
    "who",
    "what",
    "when",
    "where",
    "why",
    "how",
    "have you",
    "has ",
    "did you",
    "do you",
    "can you",
    "could you",
    "are you",
    "is there",
    "have i",
)

# Ordered (intent, trigger phrases); earlier intents win. Safety first:
# stop beats everything, and corrections ("never ...") beat commands.
_INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "stop",
        (
            "stop",
            "halt",
            "freeze",
            "cancel",
            "abort",
            "emergency",
            "kill it",
            "never mind",
            "forget it",
            "quiet",
            "shut up",
        ),
    ),
    (
        "forbid",
        (
            "never ",
            "don't ",
            "do not ",
            "forbid",
            "not allowed",
        ),
    ),
    (
        "sleep",
        (
            "sleep",
            "rest",
            "go dormant",
            "stand by",
            "standby",
            "pause",
            "mute",
            "quiet down",
            "take a break",
            "go idle",
        ),
    ),
    (
        "push_code",
        (
            "push code",
            "push my code",
            "push the code",
            "push to github",
            "push it",
            "commit and push",
            "push my changes",
            "push the commit",
            "ship it",
        ),
    ),
    (
        "approve",
        (
            "go ahead",
            "yes",
            "do it",
            "approved",
            "proceed",
            "handle it",
            "take over",
            "go for it",
            "sounds good",
        ),
    ),
    (
        "status_inquiry",
        (
            "status",
            "what did you learn",
            "what have you learned",
            "learned",
            "learnt",
            "show principles",
            "inspect",
            "report",
            "summary",
            "what do you know",
        ),
    ),
)


class VoiceError(Exception):
    """Base error for voice coordination failures."""


STTBackend = Union[str, Callable[[float], Optional[str]]]
TTSBackend = Union[str, Callable[[str], None]]

_KNOWN_STT = ("whisper_local",)
_KNOWN_TTS = ("edge_tts",)


def _normalize(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9']+", " ", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _is_question(normalized: str, raw: str) -> bool:
    if raw.strip().endswith("?"):
        return True
    return normalized.startswith(_QUESTION_STARTERS)


def _matches(normalized: str, phrase: str) -> bool:
    return (
        re.search(r"\b" + re.escape(phrase.strip()) + r"\b", normalized)
        is not None
    )


class VoiceCoordinator:
    """Low-latency speech-to-text and text-to-speech coordination."""

    def __init__(
        self,
        stt_backend: STTBackend = "whisper_local",
        tts_backend: TTSBackend = "edge_tts",
        voice: str = "en-US-AriaNeural",
        language: Optional[str] = None,
    ) -> None:
        if isinstance(stt_backend, str):
            if stt_backend not in _KNOWN_STT:
                raise VoiceError(
                    f"unknown stt_backend: {stt_backend!r} "
                    f"(known: {list(_KNOWN_STT)})"
                )
        elif not callable(stt_backend):
            raise VoiceError("stt_backend: expected a known name or callable")
        if isinstance(tts_backend, str):
            if tts_backend not in _KNOWN_TTS:
                raise VoiceError(
                    f"unknown tts_backend: {tts_backend!r} "
                    f"(known: {list(_KNOWN_TTS)})"
                )
        elif not callable(tts_backend):
            raise VoiceError("tts_backend: expected a known name or callable")
        self.stt_backend = stt_backend
        self.tts_backend = tts_backend
        self.voice = check_str(voice, field="voice")
        self.language = (check_str(language, field="language")
                         if language is not None else None)
        self._whisper_model = None

    def parse_intent(self, text: str) -> dict[str, str]:
        """Map an utterance to ``{"intent", "text"}`` (pure logic).

        Question forms resolve to ``status_inquiry`` before command
        keywords, so *"have you learned how I push my code?"* inquires
        instead of pushing.
        """
        text = check_str(text, field="text")
        normalized = _normalize(text)
        if _is_question(normalized, text):
            return {"intent": "status_inquiry", "text": text.strip()}
        for intent, phrases in _INTENT_RULES:
            if intent == "status_inquiry":
                continue  # question gate above owns this intent family
            if any(_matches(normalized, phrase) for phrase in phrases):
                return {"intent": intent, "text": text.strip()}
        for phrase in _INTENT_RULES[-1][1]:
            if _matches(normalized, phrase):
                return {"intent": "status_inquiry", "text": text.strip()}
        return {"intent": "unknown", "text": text.strip()}

    def listen_phrase(self, timeout_sec: float = 5.0) -> Optional[str]:
        """Listen once; returns the transcript or None on silence."""
        timeout_sec = self._positive_seconds(timeout_sec, field="timeout_sec")
        if callable(self.stt_backend):
            try:
                result = self.stt_backend(timeout_sec)
            except Exception as exc:
                raise VoiceError(f"stt backend failed: {exc}") from exc
            if result is not None and not isinstance(result, str):
                raise VoiceError(
                    "stt backend: expected str or None, "
                    f"got {type(result).__name__}"
                )
            return result if result is None or result.strip() else None
        return self._whisper_listen(timeout_sec)

    def speak(
        self, text: str, on_start: Optional[Callable[[], None]] = None
    ) -> None:
        """Speak one response, firing ``on_start`` first (visor hook)."""
        text = check_str(text, field="text")
        if on_start is not None:
            if not callable(on_start):
                raise ValueError("on_start: expected a callable")
            try:
                on_start()
            except Exception:
                pass  # a UI hook must never silence the voice itself
        if callable(self.tts_backend):
            try:
                self.tts_backend(text)
            except Exception as exc:
                raise VoiceError(f"tts backend failed: {exc}") from exc
        else:
            self._edge_speak(text)

    @staticmethod
    def _positive_seconds(value: float, *, field: str) -> float:
        if (
            type(value) is bool
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{field}: expected a positive number of seconds")
        return float(value)

    def capture_audio(self, timeout_sec: float = 5.0) -> Optional[bytes]:
        """Capture one microphone phrase; returns wav bytes or None.

        Split from :meth:`transcribe_wav_bytes` so the Whisper worker
        (see :mod:`mimir.voice_action`) can transcribe asynchronously
        instead of blocking the caller through capture + inference.
        """
        timeout_sec = self._positive_seconds(timeout_sec, field="timeout_sec")
        try:
            import speech_recognition as sr
        except ImportError:
            raise VoiceError(
                "SpeechRecognition is required for microphone capture "
                "(pip install SpeechRecognition; also needs PyAudio)"
            ) from None
        recognizer = sr.Recognizer()
        try:
            with sr.Microphone() as source:
                audio = recognizer.listen(
                    source, timeout=timeout_sec, phrase_time_limit=timeout_sec
                )
        except sr.WaitTimeoutError:
            return None
        except OSError as exc:
            raise VoiceError(f"microphone unavailable: {exc}") from None
        return audio.get_wav_data()

    def transcribe_wav_bytes(self, data: bytes) -> Optional[str]:
        """Transcribe wav audio with the cached Whisper tiny model."""
        if not isinstance(data, bytes) or not data:
            raise ValueError("data: expected non-empty wav bytes")
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            handle.write(data)
            path = handle.name
        try:
            return self.transcribe_audio_file(path)
        finally:
            import os

            try:
                os.unlink(path)
            except OSError:
                pass

    def transcribe_audio_file(self, path: str) -> Optional[str]:
        """Transcribe one audio file; returns the text or None on silence."""
        path = check_str(path, field="path")
        try:
            import whisper
        except ImportError:
            raise VoiceError(
                "openai-whisper is required for local transcription "
                "(pip install openai-whisper)"
            ) from None
        if self._whisper_model is None:
            self._whisper_model = whisper.load_model("tiny")
        try:
            if self.language:
                result = self._whisper_model.transcribe(
                    path, language=self.language)
            else:
                result = self._whisper_model.transcribe(path)
        except Exception as exc:
            raise VoiceError(f"whisper transcription failed: {exc}") from exc
        text = (result.get("text") or "").strip()
        return text or None

    def _whisper_listen(self, timeout_sec: float) -> Optional[str]:
        wav = self.capture_audio(timeout_sec)
        if wav is None:
            return None
        return self.transcribe_wav_bytes(wav)

    def _edge_speak(self, text: str) -> None:
        try:
            import edge_tts
        except ImportError:
            raise VoiceError(
                "edge-tts is required for neural speech "
                "(pip install edge-tts; also needs network access)"
            ) from None
        import asyncio
        import os
        import tempfile

        async def synthesize(path: str) -> None:
            await edge_tts.Communicate(
                text, voice=self.voice
            ).save(path)

        fd, path = tempfile.mkstemp(suffix=".mp3", prefix="mimir_voice_")
        os.close(fd)
        try:
            asyncio.run(synthesize(path))
        except Exception as exc:
            raise VoiceError(f"edge-tts synthesis failed: {exc}") from exc
        # Fire-and-forget playback through headless OS audio subsystem (no media player windows).
        try:
            _play_audio_headless(path)
        except Exception as exc:
            raise VoiceError(f"audio playback failed: {exc}") from exc


def _play_audio_headless(path: str) -> None:
    """Play audio in the background without opening external media player windows."""
    import os
    import threading

    def _worker() -> None:
        try:
            if os.name == "nt":
                import ctypes
                import time

                winmm = ctypes.windll.winmm
                alias = f"habi_tts_{int(time.time() * 1000)}"
                safe_path = path.replace("/", "\\")
                res = winmm.mciSendStringW(
                    f'open "{safe_path}" type mpegvideo alias {alias}', None, 0, None
                )
                if res == 0:
                    winmm.mciSendStringW(f"play {alias} wait", None, 0, None)
                    winmm.mciSendStringW(f"close {alias}", None, 0, None)
                else:
                    # Fallback to powershell background player if MCI fails
                    subprocess.run(
                        [
                            "powershell",
                            "-NoProfile",
                            "-NonInteractive",
                            "-Command",
                            f"$p = New-Object System.Media.SoundPlayer; $p.SoundLocation='{safe_path}'; $p.PlaySync()",
                        ],
                        check=False,
                        creationflags=0x08000000,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            elif sys.platform == "darwin":
                subprocess.run(
                    ["afplay", path],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                for cmd in [
                    ["ffplay", "-nodisp", "-autoexit", path],
                    ["mpg123", "-q", path],
                    ["aplay", "-q", path],
                ]:
                    try:
                        res = subprocess.run(
                            cmd,
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        if res.returncode == 0:
                            break
                    except FileNotFoundError:
                        continue
        except Exception:
            pass
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

