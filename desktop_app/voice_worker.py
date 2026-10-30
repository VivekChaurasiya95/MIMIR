"""Non-blocking voice workers (Qt-only): mic capture and TTS speech.

Wraps :class:`mimir.voice.VoiceCoordinator` calls in ``QThread`` workers so
the desktop UI never freezes while listening or speaking. The ``on_start``
hook of :meth:`VoiceCoordinator.speak` fires ``speak_started`` first, which
the tabs use to flip the visor into its animated listening face for the
duration of the audio.

Both workers support cooperative :meth:`cancel`: the blocking
microphone/TTS call runs to completion, but a cancelled result is
dropped and reported as ``speak_cancelled`` / ``listen_cancelled``
instead of finishing, so stale audio never overlaps fresh speech.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from mimir.voice import VoiceCoordinator

from .tabs._qt import load_qt

__all__ = [
    "ListenWorker",
    "SpeakWorker",
    "create_listen_worker",
    "create_speak_worker",
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
        """Ask the worker to drop its result (cooperative).

        The blocking TTS call itself cannot be preempted and already
        queued audio still plays; the completion is reported as
        ``speak_cancelled`` instead of ``speak_finished`` so follow-up
        speech does not overlap stale lines.
        """
        self._cancel.set()

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
