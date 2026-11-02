"""Voice coordination engine: hands-free MIMIR interaction (Days 8-9).

Speech-to-text (local Whisper) and text-to-speech (Edge-TTS neural,
offline ``"piper"`` neural, with an offline ``"sapi"`` OS fallback)
sit behind the backend names ``"whisper_local"`` / ``"edge_tts"`` /
``"piper"`` / ``"sapi"``; either backend also accepts an injected
callable so coordination logic (timeouts, ``on_start`` hooks, intent
parsing) stays fully testable without a microphone or speakers.
:meth:`VoiceCoordinator.parse_intent` is pure keyword logic mapping
utterances to coordination intents.
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
import threading
from typing import Any, Callable, Optional, Union

from ._common import check_str

__all__ = [
    "INTENTS",
    "VoiceCoordinator",
    "VoiceError",
    "audio_backend_status",
    "explain_capture_error",
]

#: OS speech/audio subprocesses currently producing sound, across every
#: coordinator instance. Module-global (not per-instance) so any
#: :meth:`VoiceCoordinator.stop_speaking` call silences everything the
#: process started — including speech another coordinator instance
#: began. Guarded by :data:`_AUDIO_PROCS_LOCK`.
_AUDIO_PROCS_LOCK = threading.Lock()
_ACTIVE_AUDIO_PROCS: set[Any] = set()


def _track_audio_proc(proc: Any) -> None:
    """Register a sounding subprocess as stoppable (never raises)."""
    try:
        with _AUDIO_PROCS_LOCK:
            _ACTIVE_AUDIO_PROCS.add(proc)
    except Exception:
        pass


def _untrack_audio_proc(proc: Any) -> None:
    """Forget a sounding subprocess (never raises)."""
    try:
        with _AUDIO_PROCS_LOCK:
            _ACTIVE_AUDIO_PROCS.discard(proc)
    except Exception:
        pass

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


#: Install guidance used when no microphone capture backend is importable.
_NO_CAPTURE_BACKEND_HINT = (
    "Microphone capture needs an audio backend: pip install "
    "SpeechRecognition PyAudio (default path) or pip install sounddevice "
    "(fallback; also needs numpy) — or pip install mimir[cobot] for both."
)


def _sounddevice_input_names() -> Optional[list[str]]:
    """Input-device names via sounddevice, or None when unusable."""
    try:
        import sounddevice as sd
    except ImportError:
        return None
    try:
        devices = sd.query_devices()
    except Exception:
        return None
    names: list[str] = []
    try:
        items = list(devices)
    except TypeError:
        return None
    for device in items:
        if not hasattr(device, "get"):
            continue
        try:
            channels = device.get("max_input_channels", 0) or 0
            name = str(device.get("name", "") or "").strip()
        except Exception:
            continue
        if channels > 0 and name:
            names.append(name)
    return names


def audio_backend_status() -> dict[str, Any]:
    """Probe microphone capture backends without recording (never raises).

    Returns ``{"available", "backend", "microphones", "hint"}`` where
    ``backend`` is ``"speech_recognition"``, ``"sounddevice"``, or
    ``"none"``. The SpeechRecognition + PyAudio path is primary (it is
    what :meth:`VoiceCoordinator.capture_audio` tries first);
    ``sounddevice`` is the fallback when PyAudio is missing or reports
    no input device. ``hint`` is empty when available, otherwise plain
    user guidance naming the installs that would fix it.
    """
    try:
        return _audio_backend_status()
    except Exception as exc:  # a probe must never break its caller
        return {
            "available": False,
            "backend": "none",
            "microphones": [],
            "hint": f"Microphone probe failed ({exc}). "
            + _NO_CAPTURE_BACKEND_HINT,
        }


def _audio_backend_status() -> dict[str, Any]:
    try:
        import speech_recognition as sr
    except ImportError:
        sr = None  # type: ignore[assignment]
    if sr is not None:
        try:
            import pyaudio  # noqa: F401  (Microphone needs it at runtime)
        except ImportError:
            pass  # fall through to the sounddevice probe below
        else:
            try:
                names = [
                    str(name)
                    for name in sr.Microphone.list_microphone_names()
                    if str(name).strip()
                ]
            except Exception as exc:
                if "pyaudio" not in str(exc).lower():
                    return {
                        "available": False,
                        "backend": "none",
                        "microphones": [],
                        "hint": (
                            f"Microphone unavailable ({exc}). Check a mic "
                            "is plugged in, set as the system default, "
                            "and not held by another app."
                        ),
                    }
            else:
                if names:
                    return {
                        "available": True,
                        "backend": "speech_recognition",
                        "microphones": names,
                        "hint": "",
                    }
    fallback = _sounddevice_input_names()
    if fallback:
        return {
            "available": True,
            "backend": "sounddevice",
            "microphones": fallback,
            "hint": "",
        }
    if fallback is not None:
        return {
            "available": False,
            "backend": "none",
            "microphones": [],
            "hint": ("No microphone input device found. Check a mic is "
                     "plugged in, set as the system default, and not held "
                     "by another app."),
        }
    return {
        "available": False,
        "backend": "none",
        "microphones": [],
        "hint": _NO_CAPTURE_BACKEND_HINT,
    }


def _ensure_ffmpeg() -> Optional[str]:
    """Resolve an ``ffmpeg`` binary for Whisper (None when unavailable).

    Prefers a system ``ffmpeg`` on PATH; otherwise wires the static
    binary shipped by the ``imageio-ffmpeg`` wheel (whose executable is
    *not* named ``ffmpeg``) into a temp bin dir prepended to PATH, so
    Whisper's subprocess call resolves. Never raises.
    """
    import os
    import shutil

    try:
        found = shutil.which("ffmpeg")
    except Exception:
        found = None
    if found:
        return found
    try:
        import imageio_ffmpeg

        src = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
    try:
        if not src or not os.path.isfile(src):
            return None
        import hashlib
        import tempfile

        digest = hashlib.sha256(
            os.path.abspath(src).encode("utf-8")).hexdigest()[:12]
        bindir = os.path.join(tempfile.gettempdir(),
                              f"mimir-ffmpeg-{digest}")
        os.makedirs(bindir, exist_ok=True)
        name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        dest = os.path.join(bindir, name)
        if not os.path.isfile(dest):
            shutil.copyfile(src, dest)
            try:
                os.chmod(dest, 0o755)
            except OSError:
                pass
        if os.path.isfile(dest):
            path = os.environ.get("PATH", "")
            if bindir not in path.split(os.pathsep):
                os.environ["PATH"] = bindir + os.pathsep + path
            return shutil.which("ffmpeg") or dest
    except Exception:
        return None
    return None


def _transcription_status() -> tuple[bool, str]:
    """Check the Whisper transcription half of voice input (never raises).

    Returns ``(ok, hint)``; ``hint`` is empty when ready, otherwise
    plain guidance naming the missing piece (openai-whisper or ffmpeg).
    """
    try:
        import whisper  # noqa: F401 (on-device transcription)
    except ImportError:
        return (False, "openai-whisper is missing "
                "(pip install openai-whisper) — on-device "
                "transcription needs it.")
    except Exception as exc:
        return (False, f"openai-whisper is unusable ({exc}).")
    try:
        ready = _ensure_ffmpeg()
    except Exception:
        ready = None
    if ready is None:
        return (False, "ffmpeg is missing (pip install imageio-ffmpeg, "
                "or install FFmpeg and add it to PATH) — on-device "
                "transcription needs it.")
    return (True, "")


def _sounddevice_candidates(sd: Any) -> list[tuple[int, dict[str, Any]]]:
    """Order input-device candidates for recording (best first).

    Skips output-only and unnamed devices, prefers the system default
    input, and demotes mapper pseudo-devices (e.g. Microsoft Sound
    Mapper), which often refuse to open directly. Beyond the default,
    candidates interleave across host APIs (MME / DirectSound / WASAPI
    / WDM-KS / ...), so one broken API family cannot exhaust the
    probe budget before a working family is tried. Returns
    ``(device_index, {"name", "rate"})`` with the device's native
    sample rate, which is far more likely to open than an assumed
    rate. Never raises.
    """
    try:
        devices = list(sd.query_devices())
    except Exception:
        return []
    try:
        default = sd.default.device[0]
    except Exception:
        default = None
    first: list[tuple[int, dict[str, Any]]] = []
    rest: list[tuple[int, int, dict[str, Any]]] = []
    for index, device in enumerate(devices):
        if not hasattr(device, "get"):
            continue
        try:
            name = str(device.get("name", "") or "").strip()
            max_in = device.get("max_input_channels", 0) or 0
            rate = float(device.get("default_samplerate", 0) or 0)
            api = device.get("hostapi", None)
        except Exception:
            continue
        if max_in <= 0 or not name:
            continue
        try:
            api_key = int(api)
        except (TypeError, ValueError):
            api_key = -1
        low = name.lower()
        mapper = "mapper" in low or "primary sound capture" in low
        info = {"name": name, "rate": rate if rate > 0 else 44100.0}
        if index == default and not mapper:
            first.append((index, info))
        else:
            rest.append((2 if mapper else 1, api_key, (index, info)))
    # Round-robin across host APIs within each rank: mapper last.
    ordered: list[tuple[int, dict[str, Any]]] = list(first)
    for rank in (1, 2):
        groups: dict[int, list[tuple[int, dict[str, Any]]]] = {}
        for item_rank, api_key, candidate in rest:
            if item_rank != rank:
                continue
            groups.setdefault(api_key, []).append(candidate)
        buckets = [groups[key] for key in sorted(groups)]
        while any(buckets):
            for bucket in buckets:
                if bucket:
                    ordered.append(bucket.pop(0))
    return ordered


def explain_capture_error(exc: BaseException) -> str:
    """Rewrite a capture failure as plain user guidance (pure).

    Distinguishes a missing/busy microphone, a silent room (timeout),
    and missing audio dependencies — the three capture failure modes
    users can actually act on.
    """
    text = str(exc or "").strip()
    low = text.lower()
    if "no speech" in low:
        return text  # already plain guidance; keep it verbatim
    if isinstance(exc, TimeoutError) or "timed out" in low:
        return ("Listening timed out — no speech detected. Speak a "
                "little earlier and louder, then try again.")
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return _NO_CAPTURE_BACKEND_HINT
    if "speechrecognition" in low or "pyaudio" in low:
        return _NO_CAPTURE_BACKEND_HINT
    if "sounddevice" in low:
        return _NO_CAPTURE_BACKEND_HINT
    if "whisper" in low:
        return ("openai-whisper is missing (pip install openai-whisper) "
                "— on-device transcription needs it.")
    if "ffmpeg" in low:
        return ("ffmpeg is missing (pip install imageio-ffmpeg, or "
                "install FFmpeg and add it to PATH) — on-device "
                "transcription needs it.")
    if isinstance(exc, OSError) or "microphone" in low or "no device" in low:
        return (f"Microphone unavailable ({text or 'no input device'}). "
                "Check a mic is plugged in, set as the system default, "
                "and not held by another app.")
    detail = f": {text}" if text else ""
    return ("Microphone capture failed" + detail +
            " (check SpeechRecognition + PyAudio, or the sounddevice "
            "fallback, are installed and a default mic exists).")


STTBackend = Union[str, Callable[[float], Optional[str]]]
TTSBackend = Union[str, Callable[[str], None]]

_KNOWN_STT = ("whisper_local",)
# "sapi" is the offline OS speech fallback (Windows System.Speech, or
# espeak/spd-say on POSIX). Edge stays the default neural voice; when
# edge synthesis fails at runtime (offline), speak() falls back to it.
# "piper" is the offline neural voice (rhasspy/piper): needs the
# piper-tts package (or piper CLI) plus a voice model (.onnx).
_KNOWN_TTS = ("edge_tts", "sapi", "piper")


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
        elif self.tts_backend == "sapi":
            self._sapi_speak(text)
        elif self.tts_backend == "piper":
            self._piper_speak(text)
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

    def capture_audio(
        self,
        timeout_sec: float = 5.0,
        *,
        phrase_time_limit: Optional[float] = None,
        calibrate: bool = True,
        backend: str = "auto",
    ) -> Optional[bytes]:
        """Capture one microphone phrase; returns wav bytes or None.

        Split from :meth:`transcribe_wav_bytes` so the Whisper worker
        (see :mod:`mimir.voice_action`) can transcribe asynchronously
        instead of blocking the caller through capture + inference.
        ``timeout_sec`` bounds waiting for speech to *start*;
        ``phrase_time_limit`` (default: wait + 4s) bounds the utterance
        itself. A short ambient-noise calibration runs first unless
        ``calibrate`` is False, so quiet rooms and loud rooms both
        trigger reliably.

        The SpeechRecognition + PyAudio path runs first; when it is
        unusable (missing modules, missing PyAudio, or no input
        device) capture falls back to ``sounddevice`` (when
        installed), which ships its own PortAudio binaries and works
        where PyAudio wheels do not. Silence (no speech within
        ``timeout_sec``) returns None on either path without trying
        the other. ``backend="sounddevice"`` skips the primary path
        and records straight through sounddevice (the Phase-5
        ``AudioCaptureWorker`` default when PyAudio is unavailable).
        """
        if not isinstance(backend, str) or backend.strip().lower() not in (
            "auto", "sounddevice",
        ):
            raise ValueError(
                "backend: expected 'auto' or 'sounddevice', "
                f"got {backend!r}"
            )
        timeout_sec = self._positive_seconds(timeout_sec, field="timeout_sec")
        if phrase_time_limit is None:
            phrase_time_limit = timeout_sec + 4.0
        else:
            phrase_time_limit = self._positive_seconds(
                phrase_time_limit, field="phrase_time_limit")
        if backend.strip().lower() == "sounddevice":
            return self._sounddevice_capture(
                timeout_sec, phrase_time_limit, calibrate)
        try:
            return self._sr_capture(timeout_sec, phrase_time_limit, calibrate)
        except VoiceError as exc:
            try:
                import sounddevice  # noqa: F401 (fallback availability)
                import numpy  # noqa: F401 (fallback needs it)
            except ImportError:
                # Neither path is importable: one combined hint.
                raise VoiceError(
                    f"{exc} {_NO_CAPTURE_BACKEND_HINT}") from None
            # Fallback errors name the real problem (busy/missing device),
            # so they propagate instead of the primary-path failure.
            return self._sounddevice_capture(
                timeout_sec, phrase_time_limit, calibrate)

    def _sr_capture(
        self,
        timeout_sec: float,
        phrase_time_limit: float,
        calibrate: bool,
    ) -> Optional[bytes]:
        """Capture via SpeechRecognition + PyAudio (primary path)."""
        try:
            import speech_recognition as sr
        except ImportError:
            raise VoiceError(
                "SpeechRecognition is required for microphone capture "
                "(pip install SpeechRecognition; also needs PyAudio)"
            ) from None
        recognizer = sr.Recognizer()
        recognizer.dynamic_energy_threshold = True
        try:
            with sr.Microphone() as source:
                if calibrate:
                    try:
                        recognizer.adjust_for_ambient_noise(
                            source, duration=0.4)
                    except Exception:
                        pass  # a deaf room still gets a listen attempt
                audio = recognizer.listen(
                    source, timeout=timeout_sec,
                    phrase_time_limit=phrase_time_limit,
                )
        except sr.WaitTimeoutError:
            return None
        except (OSError, AttributeError) as exc:
            raise VoiceError(f"microphone unavailable: {exc}") from None
        return audio.get_wav_data()

    def _sounddevice_capture(
        self,
        timeout_sec: float,
        phrase_time_limit: float,
        calibrate: bool,
    ) -> Optional[bytes]:
        """Capture via sounddevice (fallback when PyAudio is unusable).

        Records mono in 0.1s blocks at the input device's native
        sample rate (Whisper resamples internally) with energy-based
        onset detection: silence within ``timeout_sec`` returns None,
        and recording stops after 0.8s of trailing silence or
        ``phrase_time_limit``. Devices that refuse to open (mapper
        pseudo-devices, busy or mismatched hardware) are skipped for
        the next candidate. Returns stdlib-encoded wav bytes, the
        same contract as :meth:`capture_audio`.
        """
        try:
            import sounddevice as sd
        except ImportError:
            raise VoiceError(_NO_CAPTURE_BACKEND_HINT) from None
        try:
            import numpy as np
        except ImportError:
            raise VoiceError(
                _NO_CAPTURE_BACKEND_HINT + " (numpy is also required)"
            ) from None
        import io
        import wave

        candidates = _sounddevice_candidates(sd)
        if not candidates:
            raise VoiceError(
                "microphone unavailable: no sounddevice input device "
                "found (check a mic is plugged in and set as the "
                "system default)")
        block_sec = 0.1

        def _record(frames: int, device: int,
                    rate: float) -> "np.ndarray":
            try:
                data = sd.rec(
                    int(frames), samplerate=int(rate), channels=1,
                    dtype="float32", device=device, blocking=True,
                )
            except Exception as exc:
                raise VoiceError(
                    f"microphone unavailable: {exc}") from None
            try:
                return np.asarray(data, dtype=np.float32).reshape(-1)
            except Exception as exc:
                raise VoiceError(
                    f"microphone capture failed: {exc}") from None

        def _rms(frames_data: "np.ndarray") -> float:
            if frames_data.size == 0:
                return 0.0
            # float64: some drivers emit unnormalized scales that would
            # overflow a float32 square.
            scaled = frames_data.astype(np.float64, copy=False)
            return float(np.sqrt(np.mean(scaled * scaled)))

        # Probe candidates in order: a short record proves the device
        # opens at its native rate before the real capture commits.
        # Failed opens return immediately, so a generous budget is
        # cheap; only the successful open costs one 0.1s block.
        chosen: Optional[tuple[int, float]] = None
        last_error: Optional[VoiceError] = None
        for device, info in candidates[:10]:
            rate = float(info["rate"])
            try:
                _record(int(rate * block_sec), device, rate)
            except VoiceError as exc:
                last_error = exc
                continue
            chosen = (device, rate)
            break
        if chosen is None:
            raise (last_error if last_error is not None else VoiceError(
                "microphone unavailable: no sounddevice input device "
                "could be opened"))
        device, sample_rate = chosen
        block_frames = int(sample_rate * block_sec)

        threshold = 0.02
        if calibrate:
            try:
                noise = _record(int(sample_rate * 0.4), device, sample_rate)
            except VoiceError:
                raise
            except Exception:
                noise = None
            if noise is not None:
                threshold = max(_rms(noise) * 3.0, 0.015)

        onset_blocks = max(1, int(timeout_sec / block_sec))
        heard: list["np.ndarray"] = []
        started = False
        for _ in range(onset_blocks):
            block = _record(block_frames, device, sample_rate)
            if _rms(block) > threshold:
                heard.append(block)
                started = True
                break
        if not started:
            return None

        max_blocks = max(1, int(phrase_time_limit / block_sec))
        quiet_needed = max(1, int(0.8 / block_sec))
        quiet_run = 0
        while len(heard) < max_blocks and quiet_run < quiet_needed:
            block = _record(block_frames, device, sample_rate)
            heard.append(block)
            quiet_run = quiet_run + 1 if _rms(block) <= threshold else 0

        pcm = np.concatenate(heard) if heard else np.zeros(0, np.float32)
        pcm = np.clip(pcm, -1.0, 1.0)
        pcm16 = (pcm * 32767).astype(np.int16)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(int(sample_rate))
            handle.writeframes(pcm16.tobytes())
        return buffer.getvalue()

    @staticmethod
    def list_microphones() -> list[str]:
        """Best-effort input-device names ([] when unavailable)."""
        try:
            import speech_recognition as sr
        except ImportError:
            sr = None  # type: ignore[assignment]
        if sr is not None:
            try:
                names = sr.Microphone.list_microphone_names()
            except Exception:
                names = []
            listed = [str(name) for name in names if str(name).strip()]
            if listed:
                return listed
        fallback = _sounddevice_input_names()
        return list(fallback) if fallback else []

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
        if _ensure_ffmpeg() is None:
            raise VoiceError(
                "ffmpeg is required for local transcription "
                "(pip install imageio-ffmpeg, or install FFmpeg and add "
                "it to PATH)"
            ) from None
        if self._whisper_model is None:
            self._whisper_model = whisper.load_model("tiny")
        try:
            kwargs: dict[str, Any] = {"fp16": False}
            if self.language:
                kwargs["language"] = self.language
            result = self._whisper_model.transcribe(path, **kwargs)
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
            # Offline at runtime (edge-tts needs network): fall back to
            # the OS speech synthesizer instead of going silent. A
            # missing edge-tts install still raises above with its hint.
            try:
                os.unlink(path)
            except OSError:
                pass
            try:
                self._sapi_speak(text)
                return
            except VoiceError:
                pass
            raise VoiceError(f"edge-tts synthesis failed: {exc}") from exc
        # Headless playback through the OS audio subsystem (no media
        # player windows). This call blocks until the utterance finishes
        # so a speech worker (and its UI stop button) spans the audible
        # speech, not just the synthesis; stop_speaking() releases the
        # wait immediately via MCI stop-all / killing the player.
        try:
            play_thread = _play_audio_headless(path)
        except Exception as exc:
            raise VoiceError(f"audio playback failed: {exc}") from exc
        try:
            play_thread.join()
        except Exception:
            pass

    def stop_speaking(self) -> None:
        """Immediately silence any active speech synthesis or playback.

        Kills every tracked OS speech/audio subprocess (SAPI voices,
        fallback players) started by any coordinator instance, then
        stops MCI playback (the Edge-TTS path on Windows). Idempotent
        and never raises, so a UI stop button can call it freely.
        """
        with _AUDIO_PROCS_LOCK:
            procs = list(_ACTIVE_AUDIO_PROCS)
            _ACTIVE_AUDIO_PROCS.clear()
        for proc in procs:
            for action in ("terminate", "kill"):
                try:
                    getattr(proc, action)()
                except Exception:
                    pass
        for attr in ("_active_proc", "_active_play_proc"):
            proc = getattr(self, attr, None)
            if proc is not None:
                for action in ("terminate", "kill"):
                    try:
                        getattr(proc, action)()
                    except Exception:
                        pass
                try:
                    setattr(self, attr, None)
                except Exception:
                    pass
        if sys.platform == "win32":
            try:
                import ctypes
                winmm = ctypes.windll.winmm
                winmm.mciSendStringW("stop all", None, 0, None)
                winmm.mciSendStringW("close all", None, 0, None)
            except Exception:
                pass

    def _sapi_speak(self, text: str) -> None:
        """Speak via the offline OS synthesizer (no network needed).

        Windows uses the built-in System.Speech voice through a hidden
        PowerShell host; POSIX tries ``espeak`` then ``spd-say``.
        Raises :class:`VoiceError` when no offline voice is available.
        """
        import os
        import tempfile

        if os.name == "nt":
            fd, path = tempfile.mkstemp(suffix=".txt",
                                        prefix="mimir_say_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(text)
                # The text travels via file so no quoting can break the
                # command line or inject PowerShell.
                script = (
                    "Add-Type -AssemblyName System.Speech; "
                    "$s = New-Object System.Speech.Synthesis."
                    "SpeechSynthesizer; "
                    "$s.Speak([System.IO.File]::ReadAllText('"
                    + path.replace("'", "''") + "'))"
                )
                try:
                    proc = subprocess.Popen(
                        ["powershell", "-NoProfile", "-NonInteractive",
                         "-Command", script],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=0x08000000,
                    )
                except FileNotFoundError:
                    raise VoiceError(
                        "sapi speech unavailable: powershell not found"
                    ) from None
                # Tracked (not run-and-waited) so stop_speaking() can
                # silence a long utterance mid-sentence.
                _track_audio_proc(proc)
                try:
                    proc.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    raise VoiceError("sapi speech timed out") from None
                finally:
                    _untrack_audio_proc(proc)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            if proc.returncode != 0:
                raise VoiceError(
                    "sapi speech failed "
                    f"(exit {proc.returncode})"
                )
            return
        for command in (["espeak", text], ["spd-say", text]):
            try:
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                continue
            _track_audio_proc(proc)
            try:
                proc.wait(timeout=90)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                raise VoiceError("offline speech timed out") from None
            finally:
                _untrack_audio_proc(proc)
            if proc.returncode == 0:
                return
        raise VoiceError(
            "offline speech unavailable (no sapi/espeak/spd-say)"
        )

    def _piper_speak(self, text: str) -> None:
        """Speak via offline neural piper voice (no network needed).

        Needs the ``piper-tts`` package (or the ``piper`` CLI) plus a
        voice model (``.onnx``). The model resolves from
        ``PIPER_MODEL``/``PIPER_VOICE`` env, or from ``voice`` when it
        names an existing ``.onnx`` file. Raises :class:`VoiceError`
        with install guidance when either piece is missing.
        """
        import os
        import shutil
        import tempfile

        model = _piper_model_path(self.voice)
        if model is None:
            raise VoiceError(
                "piper voice model not found (set PIPER_MODEL=/path/to/"
                "voice.onnx or pass voice=/path/to/voice.onnx; download "
                "a voice from https://github.com/rhasspy/piper/releases) "
                "— offline piper synthesis needs it."
            )
        fd, wav_path = tempfile.mkstemp(suffix=".wav",
                                        prefix="mimir_piper_")
        os.close(fd)
        synthesized = False
        piper_error: Optional[str] = None
        try:
            if _piper_synthesize_python(model, text, wav_path):
                synthesized = True
            else:
                exe = shutil.which("piper")
                if exe is None:
                    raise VoiceError(
                        "piper is required for offline neural speech "
                        "(pip install piper-tts; also needs a voice model "
                        ".onnx) — no piper package or CLI found."
                    )
                try:
                    proc = subprocess.Popen(
                        [exe, "--model", model,
                         "--output_file", wav_path],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except FileNotFoundError:
                    raise VoiceError(
                        "piper speech unavailable: piper CLI not found"
                    ) from None
                _track_audio_proc(proc)
                try:
                    assert proc.stdin is not None
                    proc.stdin.write(text.encode("utf-8"))
                    proc.stdin.close()
                    proc.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    raise VoiceError(
                        "piper speech timed out") from None
                finally:
                    _untrack_audio_proc(proc)
                if proc.returncode != 0:
                    raise VoiceError(
                        "piper speech failed "
                        f"(exit {proc.returncode})"
                    )
                synthesized = True
        except VoiceError:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
            raise
        except Exception as exc:
            piper_error = str(exc)
            try:
                os.unlink(wav_path)
            except OSError:
                pass
            raise VoiceError(
                f"piper synthesis failed: {exc}") from exc
        if not synthesized or not os.path.isfile(wav_path):
            try:
                os.unlink(wav_path)
            except OSError:
                pass
            raise VoiceError(
                "piper synthesis failed"
                + (f": {piper_error}" if piper_error else "")
                + " (no audio produced)"
            )
        try:
            play_thread = _play_audio_headless(wav_path)
        except Exception as exc:
            raise VoiceError(f"audio playback failed: {exc}") from exc
        try:
            play_thread.join()
        except Exception:
            pass


def _piper_model_path(voice_hint: str = "") -> Optional[str]:
    """Resolve a piper voice model (.onnx) path, or None (never raises)."""
    import os

    try:
        for env_key in ("PIPER_MODEL", "PIPER_VOICE"):
            candidate = (os.environ.get(env_key, "") or "").strip()
            if candidate and os.path.isfile(candidate):
                return os.path.abspath(candidate)
        hint = (voice_hint or "").strip()
        if hint.lower().endswith(".onnx") and os.path.isfile(hint):
            return os.path.abspath(hint)
    except Exception:
        return None
    return None


def _piper_synthesize_python(model: str, text: str,
                             wav_path: str) -> bool:
    """Synthesize via the piper Python package; False when unavailable.

    Returns True on success. Raises VoiceError only for a hard
    synthesis failure; returns False when the package is not
    importable so the caller can try the CLI next.
    """
    try:
        import piper  # type: ignore[import-not-found]
    except ImportError:
        try:
            import piper_tts as piper  # type: ignore[import-not-found]
        except ImportError:
            return False
    try:
        load = getattr(getattr(piper, "PiperVoice", None), "load", None)
        if not callable(load):
            return False
        voice = load(model)
        if hasattr(voice, "synthesize_wav"):
            voice.synthesize_wav(text, wav_path)
            return True
        synthesize = getattr(voice, "synthesize", None)
        if callable(synthesize):
            import wave

            chunks: list[bytes] = []

            def _cb(data: Any) -> None:
                try:
                    chunks.append(bytes(data))
                except Exception:
                    pass

            try:
                synthesize(text, _cb)
            except TypeError:
                with wave.open(wav_path, "wb") as handle:
                    synthesize(text, handle)
                return True
            if chunks:
                import io
                with wave.open(wav_path, "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(22050)
                    handle.writeframes(b"".join(chunks))
                return True
            return False
    except VoiceError:
        raise
    except Exception as exc:
        raise VoiceError(f"piper synthesis failed: {exc}") from exc
    return False


def _play_audio_headless(path: str) -> "threading.Thread":
    """Play audio without opening external media player windows.

    Playback runs on a daemon thread (returned to the caller) so the
    synthesizer can block on it: callers join the thread to span the
    full utterance, while stop_speaking() ends it early.
    """
    import os

    def _worker() -> None:
        try:
            if os.name == "nt":
                import ctypes
                import time

                winmm = ctypes.windll.winmm
                alias = f"habi_tts_{int(time.time() * 1000)}"
                safe_path = path.replace("/", "\\")
                mci_type = ("waveaudio" if safe_path.lower().endswith(".wav")
                            else "mpegvideo")
                res = winmm.mciSendStringW(
                    f'open "{safe_path}" type {mci_type} alias {alias}',
                    None, 0, None,
                )
                if res == 0:
                    winmm.mciSendStringW(f"play {alias} wait", None, 0, None)
                    winmm.mciSendStringW(f"close {alias}", None, 0, None)
                else:
                    # Fallback to powershell background player if MCI fails
                    proc = subprocess.Popen(
                        [
                            "powershell",
                            "-NoProfile",
                            "-NonInteractive",
                            "-Command",
                            f"$p = New-Object System.Media.SoundPlayer; $p.SoundLocation='{safe_path}'; $p.PlaySync()",
                        ],
                        creationflags=0x08000000,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    _track_audio_proc(proc)
                    try:
                        proc.wait()
                    finally:
                        _untrack_audio_proc(proc)
            elif sys.platform == "darwin":
                proc = subprocess.Popen(
                    ["afplay", path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                _track_audio_proc(proc)
                try:
                    proc.wait()
                finally:
                    _untrack_audio_proc(proc)
            else:
                for cmd in [
                    ["ffplay", "-nodisp", "-autoexit", path],
                    ["mpg123", "-q", path],
                    ["aplay", "-q", path],
                ]:
                    try:
                        proc = subprocess.Popen(
                            cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    except FileNotFoundError:
                        continue
                    _track_audio_proc(proc)
                    try:
                        proc.wait()
                    finally:
                        _untrack_audio_proc(proc)
                    if proc.returncode == 0:
                        break
        except Exception:
            pass
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread

