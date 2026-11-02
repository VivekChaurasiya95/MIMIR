"""Non-blocking voice workers (Qt-only): mic capture and TTS speech.

Wraps :class:`mimir.voice.VoiceCoordinator` calls in ``QThread`` workers so
the desktop UI never freezes while listening or speaking. The ``on_start``
hook of :meth:`VoiceCoordinator.speak` fires ``speak_started`` first, which
the tabs use to flip the visor into its animated listening face for the
duration of the audio.

Phase 5 splits the monolithic :class:`ListenWorker` (capture +
transcribe in one blocking call) into :class:`AudioCaptureWorker`
(sounddevice-aware mic capture -> wav bytes) and
:class:`WhisperTranscribeWorker` (wav bytes -> transcript), with
:class:`SpeechSynthesisWorker` (Edge / Piper / SAPI speech) covering
TTS. Each stage runs on its own ``QThread`` and reports through Qt
signals, so the main thread never blocks on the microphone, Whisper
inference, or speech synthesis — no UI stutter. The legacy
:class:`ListenWorker` / :class:`SpeakWorker` stay as thin
single-call wrappers for existing hosts.

Every worker supports cooperative :meth:`cancel`: the blocking
microphone/TTS call runs to completion, but a cancelled result is
dropped and reported as ``*_cancelled`` instead of finishing, so
stale audio never overlaps fresh speech. Workers optionally publish
``VOICE_CAPTURED`` / ``TRANSCRIPTION_COMPLETED`` best-effort so the
visor binding (see :func:`mimir.events.bind_visor_to_bus`) animates
through LISTENING without the tabs polling.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from mimir.voice import VoiceCoordinator

from .tabs._qt import load_qt

__all__ = [
    "AudioCaptureWorker",
    "ListenWorker",
    "SpeakWorker",
    "SpeechSynthesisWorker",
    "WhisperTranscribeWorker",
    "create_audio_capture_worker",
    "create_listen_worker",
    "create_speak_worker",
    "create_speech_synthesis_worker",
    "create_whisper_transcribe_worker",
]

_, QtCore, _, _ = load_qt()

if hasattr(QtCore, "pyqtSignal"):
    _Signal = QtCore.pyqtSignal
else:  # PySide6
    _Signal = QtCore.Signal


class SpeakWorker(QtCore.QThread):
    """Speak one line in the background; never blocks the UI thread."""

    speak_started = _Signal()
    speak_finished = _Signal()
    speak_error = _Signal(str)
    speak_cancelled = _Signal()

    def __init__(self, voice: VoiceCoordinator, text: str,
                 parent: Any = None) -> None:
        super().__init__(parent)
        if not isinstance(voice, VoiceCoordinator):
            raise ValueError(
                "voice: expected VoiceCoordinator, "
                f"got {type(voice).__name__}"
            )
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text: expected a non-empty string")
        self._voice = voice
        self._text = text
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Ask the worker to drop its result and silence active speech."""
        self._cancel.set()
        try:
            self._voice.stop_speaking()
        except Exception:
            pass

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:  # Qt override (runs on the worker thread)
        if self._cancel.is_set():
            self.speak_cancelled.emit()
            return
        try:
            self._voice.speak(self._text,
                              on_start=lambda: self.speak_started.emit())
        except Exception as exc:
            if self._cancel.is_set():
                self.speak_cancelled.emit()
            else:
                self.speak_error.emit(str(exc))
        else:
            if self._cancel.is_set():
                self.speak_cancelled.emit()
            else:
                self.speak_finished.emit()


class ListenWorker(QtCore.QThread):
    """Capture one microphone phrase in the background."""

    #: (ok, transcript-or-reason), mirroring ``AppState.try_listen``.
    listen_finished = _Signal(bool, str)
    listen_cancelled = _Signal()

    def __init__(self, voice: VoiceCoordinator,
                 timeout_sec: float = 5.0,
                 parent: Any = None) -> None:
        super().__init__(parent)
        if not isinstance(voice, VoiceCoordinator):
            raise ValueError(
                "voice: expected VoiceCoordinator, "
                f"got {type(voice).__name__}"
            )
        self._voice = voice
        self._timeout = timeout_sec
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Ask the worker to drop its result (cooperative).

        The blocking microphone read itself cannot be preempted; the
        transcript is discarded and ``listen_cancelled`` is emitted
        instead of ``listen_finished``.
        """
        self._cancel.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:  # Qt override (runs on the worker thread)
        if self._cancel.is_set():
            self.listen_cancelled.emit()
            return
        try:
            heard = self._voice.listen_phrase(timeout_sec=self._timeout)
        except Exception as exc:  # missing mic/whisper stays in-app
            if self._cancel.is_set():
                self.listen_cancelled.emit()
            else:
                self.listen_finished.emit(False, str(exc))
            return
        if self._cancel.is_set():
            self.listen_cancelled.emit()
        elif not heard:
            self.listen_finished.emit(False, "No speech heard — try again.")
        else:
            self.listen_finished.emit(True, heard)


def create_speak_worker(voice: VoiceCoordinator, text: str,
                        parent: Optional[Any] = None) -> SpeakWorker:
    """Build (but do not start) a speech worker.

    Connect to ``speak_started`` / ``speak_finished`` / ``speak_error``
    / ``speak_cancelled`` first, keep a reference for the worker's
    lifetime, then call ``worker.start()``. Call ``worker.cancel()``
    to drop a stale line.
    """
    return SpeakWorker(voice, text, parent=parent)


def create_listen_worker(voice: VoiceCoordinator,
                         timeout_sec: float = 5.0,
                         parent: Optional[Any] = None) -> ListenWorker:
    """Build (but do not start) a microphone worker.

    Connect to ``listen_finished`` / ``listen_cancelled`` first, keep
    a reference for the worker's lifetime, then call ``worker.start()``.
    Call ``worker.cancel()`` to drop a stale capture.
    """
    return ListenWorker(voice, timeout_sec=timeout_sec, parent=parent)


def _emit_voice_event(bus: Any, event_type: Any, source: str,
                      payload: dict[str, Any], session_id: str = "",
                      correlation_id: str = "") -> None:
    """Best-effort runtime event from a worker thread (never raises)."""
    try:
        if bus is None:
            from mimir.events import get_event_bus
            bus = get_event_bus()
        kwargs: dict[str, Any] = {"session_id": session_id}
        if correlation_id:
            kwargs["correlation_id"] = correlation_id
        bus.emit(event_type, source, payload, **kwargs)
    except Exception:
        pass


class AudioCaptureWorker(QtCore.QThread):
    """Capture one microphone phrase to wav bytes in the background.

    Runs :meth:`VoiceCoordinator.capture_audio` on the worker thread —
    the Qt main thread never blocks on the microphone. ``backend``
    is ``"auto"`` (SpeechRecognition first, sounddevice fallback) or
    ``"sounddevice"`` (straight through sounddevice, for machines
    without PyAudio wheels). A ``VOICE_CAPTURED`` event is published
    best-effort so the visor binding animates through LISTENING.
    """

    #: wav bytes payload (``object`` carries bytes through Qt signals).
    capture_finished = _Signal(object)
    #: No speech within ``timeout_sec`` (silence, not an error).
    capture_silence = _Signal()
    capture_error = _Signal(str)
    capture_cancelled = _Signal()

    def __init__(self, voice: VoiceCoordinator,
                 timeout_sec: float = 5.0,
                 phrase_time_limit: Optional[float] = None,
                 backend: str = "auto",
                 parent: Any = None,
                 bus: Any = None,
                 session_id: str = "",
                 correlation_id: str = "") -> None:
        super().__init__(parent)
        if not isinstance(voice, VoiceCoordinator):
            raise ValueError(
                "voice: expected VoiceCoordinator, "
                f"got {type(voice).__name__}"
            )
        if not isinstance(backend, str) or backend.strip().lower() not in (
            "auto", "sounddevice",
        ):
            raise ValueError(
                "backend: expected 'auto' or 'sounddevice', "
                f"got {backend!r}"
            )
        self._voice = voice
        self._timeout = timeout_sec
        self._phrase_limit = phrase_time_limit
        self._backend = backend.strip().lower()
        self._bus = bus
        self._session_id = session_id
        self._correlation_id = correlation_id
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Ask the worker to drop its result (cooperative)."""
        self._cancel.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:  # Qt override (runs on the worker thread)
        if self._cancel.is_set():
            self.capture_cancelled.emit()
            return
        try:
            wav = self._voice.capture_audio(
                self._timeout,
                phrase_time_limit=self._phrase_limit,
                backend=self._backend,
            )
        except Exception as exc:  # missing mic stays in-app
            if self._cancel.is_set():
                self.capture_cancelled.emit()
            else:
                self.capture_error.emit(str(exc))
            return
        if self._cancel.is_set():
            self.capture_cancelled.emit()
            return
        try:
            from mimir.events import EventType
            _emit_voice_event(
                self._bus, EventType.VOICE_CAPTURED,
                "voice.capture_worker",
                {"heard": wav is not None,
                 "backend": self._backend,
                 "bytes": len(wav) if wav else 0},
                session_id=self._session_id,
                correlation_id=self._correlation_id,
            )
        except Exception:
            pass
        if wav is None:
            self.capture_silence.emit()
        else:
            self.capture_finished.emit(wav)


class WhisperTranscribeWorker(QtCore.QThread):
    """Transcribe wav bytes with Whisper in the background.

    Runs :meth:`VoiceCoordinator.transcribe_wav_bytes` on the worker
    thread — Whisper inference never stalls the Qt main thread. A
    ``TRANSCRIPTION_COMPLETED`` event is published best-effort so the
    visor binding stays in LISTENING until the transcript lands.
    """

    transcribe_finished = _Signal(str)
    #: Audio held no speech (transcriber returned None).
    transcribe_silence = _Signal()
    transcribe_error = _Signal(str)
    transcribe_cancelled = _Signal()

    def __init__(self, voice: VoiceCoordinator, audio: bytes,
                 parent: Any = None,
                 bus: Any = None,
                 session_id: str = "",
                 correlation_id: str = "") -> None:
        super().__init__(parent)
        if not isinstance(voice, VoiceCoordinator):
            raise ValueError(
                "voice: expected VoiceCoordinator, "
                f"got {type(voice).__name__}"
            )
        if not isinstance(audio, bytes) or not audio:
            raise ValueError("audio: expected non-empty wav bytes")
        self._voice = voice
        self._audio = audio
        self._bus = bus
        self._session_id = session_id
        self._correlation_id = correlation_id
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Ask the worker to drop its result (cooperative)."""
        self._cancel.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:  # Qt override (runs on the worker thread)
        if self._cancel.is_set():
            self.transcribe_cancelled.emit()
            return
        try:
            text = self._voice.transcribe_wav_bytes(self._audio)
        except Exception as exc:  # missing whisper stays in-app
            if self._cancel.is_set():
                self.transcribe_cancelled.emit()
            else:
                self.transcribe_error.emit(str(exc))
            return
        if self._cancel.is_set():
            self.transcribe_cancelled.emit()
            return
        try:
            from mimir.events import EventType
            _emit_voice_event(
                self._bus, EventType.TRANSCRIPTION_COMPLETED,
                "voice.whisper_worker",
                {"text": text or "",
                 "heard": bool(text and text.strip())},
                session_id=self._session_id,
                correlation_id=self._correlation_id,
            )
        except Exception:
            pass
        if not text or not text.strip():
            self.transcribe_silence.emit()
        else:
            self.transcribe_finished.emit(text)


class SpeechSynthesisWorker(QtCore.QThread):
    """Speak one line in the background (Edge / Piper / SAPI).

    Runs :meth:`VoiceCoordinator.speak` on the worker thread for
    whichever TTS backend the coordinator carries — ``"edge_tts"``
    neural, offline ``"piper"`` neural, ``"sapi"`` OS fallback, or an
    injected callable. The ``synthesis_started`` hook fires first so
    hosts can flip the visor into its animated face for the audible
    speech.
    """

    synthesis_started = _Signal()
    synthesis_finished = _Signal()
    synthesis_error = _Signal(str)
    synthesis_cancelled = _Signal()

    def __init__(self, voice: VoiceCoordinator, text: str,
                 parent: Any = None) -> None:
        super().__init__(parent)
        if not isinstance(voice, VoiceCoordinator):
            raise ValueError(
                "voice: expected VoiceCoordinator, "
                f"got {type(voice).__name__}"
            )
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text: expected a non-empty string")
        self._voice = voice
        self._text = text
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Ask the worker to drop its result and silence active speech."""
        self._cancel.set()
        try:
            self._voice.stop_speaking()
        except Exception:
            pass

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:  # Qt override (runs on the worker thread)
        if self._cancel.is_set():
            self.synthesis_cancelled.emit()
            return
        try:
            self._voice.speak(
                self._text,
                on_start=lambda: self.synthesis_started.emit(),
            )
        except Exception as exc:
            if self._cancel.is_set():
                self.synthesis_cancelled.emit()
            else:
                self.synthesis_error.emit(str(exc))
        else:
            if self._cancel.is_set():
                self.synthesis_cancelled.emit()
            else:
                self.synthesis_finished.emit()


def create_audio_capture_worker(
    voice: VoiceCoordinator,
    timeout_sec: float = 5.0,
    phrase_time_limit: Optional[float] = None,
    backend: str = "auto",
    parent: Optional[Any] = None,
    bus: Any = None,
    session_id: str = "",
    correlation_id: str = "",
) -> AudioCaptureWorker:
    """Build (but do not start) a mic-capture worker.

    Connect to ``capture_finished`` (wav bytes) / ``capture_silence``
    / ``capture_error`` / ``capture_cancelled`` first, keep a
    reference for the worker's lifetime, then call ``worker.start()``.
    Feed ``capture_finished`` payloads to
    :func:`create_whisper_transcribe_worker` for the split pipeline.
    """
    return AudioCaptureWorker(
        voice, timeout_sec=timeout_sec,
        phrase_time_limit=phrase_time_limit, backend=backend,
        parent=parent, bus=bus, session_id=session_id,
        correlation_id=correlation_id,
    )


def create_whisper_transcribe_worker(
    voice: VoiceCoordinator,
    audio: bytes,
    parent: Optional[Any] = None,
    bus: Any = None,
    session_id: str = "",
    correlation_id: str = "",
) -> WhisperTranscribeWorker:
    """Build (but do not start) a Whisper transcription worker.

    Connect to ``transcribe_finished`` (transcript) /
    ``transcribe_silence`` / ``transcribe_error`` /
    ``transcribe_cancelled`` first, keep a reference for the worker's
    lifetime, then call ``worker.start()``.
    """
    return WhisperTranscribeWorker(
        voice, audio, parent=parent, bus=bus, session_id=session_id,
        correlation_id=correlation_id,
    )


def create_speech_synthesis_worker(
    voice: VoiceCoordinator, text: str,
    parent: Optional[Any] = None,
) -> SpeechSynthesisWorker:
    """Build (but do not start) a speech-synthesis worker.

    Connect to ``synthesis_started`` / ``synthesis_finished`` /
    ``synthesis_error`` / ``synthesis_cancelled`` first, keep a
    reference for the worker's lifetime, then call ``worker.start()``.
    Call ``worker.cancel()`` to drop a stale line.
    """
    return SpeechSynthesisWorker(voice, text, parent=parent)
