"""Voice-to-action pipeline (plan section 4, step 3).

Connects Whisper transcription to the intent parser and the
:class:`~mimir.actions.ActionRegistry`, following the plan's processing
flow: capture audio → Whisper worker → async transcription → intent
parser → structured intent → resolution against the Developer State
Engine + Action Registry → authorized action request → TTS feedback.

Components:

- :class:`TranscriptionWorker`: background-thread Whisper worker with a
  bounded queue. ``submit`` never blocks the caller beyond an optional
  timeout, and every completed transcription publishes
  ``TRANSCRIPTION_COMPLETED`` best-effort.
- :func:`parse_structured_intent`: pure resolution of one transcript to
  a :class:`StructuredIntent`. ``APPROVE_PENDING_ACTION`` binds to one
  specific pending action (explicit id, text mention, or Developer
  State focus); zero or several candidates yield clarification.
- :class:`VoiceActionService`: Qt-free orchestration — publishes
  ``INTENT_PARSED`` / ``ACTION_CONFIRMATION_RECEIVED`` /
  ``ACTION_REQUESTED``, authorizes through the registry, and speaks the
  reply through the existing TTS subsystem.

Everything here is Qt-free and independent of the desktop UI state: the
service works headless with injected fakes, and the Qt
``ListenWorker``/``SpeakWorker`` remain thin thread wrappers hosts may
keep using.
"""

from __future__ import annotations

import queue
import re
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable, Collection, Optional

from .actions import (
    APPROVE_PENDING_ACTION,
    ActionRegistry,
    ActionSpec,
    AuthorizationOutcome,
    VOICE_GRANTED_PERMISSIONS,
)
from .devstate import DeveloperStateEngine
from .events import EventBus, EventType, get_event_bus
from .safety import SafetyController
from .voice import VoiceCoordinator

__all__ = [
    "STRUCTURED_INTENTS",
    "StructuredIntent",
    "TranscriptionWorker",
    "VoiceActionError",
    "VoiceActionService",
    "VoiceDecision",
    "parse_structured_intent",
]

#: Structured intent names produced by :func:`parse_structured_intent`.
STRUCTURED_INTENTS = (
    APPROVE_PENDING_ACTION,  # "approve"
    "STOP",  # "stop"
    "SLEEP",  # "sleep"
    "STATUS_INQUIRY",  # "status_inquiry"
    "PUSH_CODE",  # "push_code"
    "FORBID",  # "forbid"
    "UNKNOWN",  # "unknown"
)

_LEGACY_TO_STRUCTURED = {
    "approve": APPROVE_PENDING_ACTION,
    "stop": "STOP",
    "sleep": "SLEEP",
    "status_inquiry": "STATUS_INQUIRY",
    "push_code": "PUSH_CODE",
    "forbid": "FORBID",
    "unknown": "UNKNOWN",
}

_ACTION_ID_RE = re.compile(r"\baction_[A-Za-z0-9_-]{1,32}\b")

TranscribeFn = Callable[[Any], Optional[str]]
ReplyFn = Callable[["StructuredIntent"], str]
SpeakFn = Callable[[str], None]


class VoiceActionError(ValueError):
    """Raised for invalid voice-action input or backpressure."""


@dataclass(frozen=True)
class StructuredIntent:
    """One transcript resolved against registry + developer state."""

    intent: str
    text: str
    target_action_id: str = ""
    authorization_type: str = ""
    needs_clarification: bool = False
    clarification: str = ""
    candidates: tuple[str, ...] = ()
    legacy_intent: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "text": self.text,
            "target_action_id": self.target_action_id,
            "authorization_type": self.authorization_type,
            "needs_clarification": self.needs_clarification,
            "clarification": self.clarification,
            "candidates": list(self.candidates),
            "legacy_intent": self.legacy_intent,
        }


def _focus_hint(devstate: Optional[DeveloperStateEngine]) -> dict[str, str]:
    if devstate is None:
        return {}
    try:
        values = devstate.state.values
    except Exception:
        return {}
    hint: dict[str, str] = {}
    for name in ("focus_file", "active_project", "active_repo"):
        value = values.get(name)
        if isinstance(value, str) and value:
            hint[name] = value
    return hint


def _score_of(spec: ActionSpec, text: str, hint: dict[str, str]) -> int:
    points = 0
    blob = f"{spec.operation} {spec.target} {spec.file_or_element}".lower()
    for word in re.findall(r"[a-z0-9_.-]{3,}", text.lower()):
        if word in blob:
            points += 2
    focus = hint.get("focus_file", "")
    if focus and spec.file_or_element and (
        focus == spec.file_or_element
        or focus.endswith(spec.file_or_element)
        or spec.file_or_element.endswith(focus)
    ):
        points += 3
    project = hint.get("active_project", "") or hint.get("active_repo", "")
    if project and spec.target and project.lower() in spec.target.lower():
        points += 1
    return points


def _rank_candidates(
    candidates: list[ActionSpec],
    text: str,
    hint: dict[str, str],
) -> list[ActionSpec]:
    """Order pending actions by textual + developer-state relevance."""

    def score(spec: ActionSpec) -> tuple[int, float]:
        # Older proposals first on ties (stable, predictable binding).
        return (_score_of(spec, text, hint), -spec.created_at)

    return sorted(candidates, key=score, reverse=True)


def parse_structured_intent(
    text: str,
    coordinator: VoiceCoordinator,
    registry: ActionRegistry,
    *,
    devstate: Optional[DeveloperStateEngine] = None,
    now: Optional[float] = None,
) -> StructuredIntent:
    """Resolve one transcript to a structured, registry-bound intent.

    Pure with respect to the registry (read-only: ``get``/``list_pending``
    only; expired entries are lazily marked by those calls): never
    authorizes anything. ``APPROVE_PENDING_ACTION`` binds to exactly one
    pending action; ambiguous, expired, or missing actions produce
    ``needs_clarification`` with a speakable prompt instead of guessing.
    """
    if not isinstance(text, str) or not text.strip():
        raise VoiceActionError("text: expected a non-empty string")
    if not isinstance(coordinator, VoiceCoordinator):
        raise VoiceActionError(
            "coordinator: expected VoiceCoordinator, "
            f"got {type(coordinator).__name__}"
        )
    if not isinstance(registry, ActionRegistry):
        raise VoiceActionError(
            f"registry: expected ActionRegistry, got {type(registry).__name__}"
        )
    cleaned = text.strip()
    legacy = coordinator.parse_intent(cleaned)["intent"]
    structured = _LEGACY_TO_STRUCTURED.get(legacy, "UNKNOWN")
    if structured != APPROVE_PENDING_ACTION:
        if structured == "UNKNOWN":
            # Attempt intuitive natural language resolution via local Llama
            try:
                from .llama_intent import query_local_llama_intent
                pending = registry.list_pending(now=now)
                llama_res = query_local_llama_intent(
                    cleaned,
                    devstate=devstate,
                    pending_actions=pending,
                    timeout_s=5.0,
                )
                if llama_res and llama_res.get("intent") and llama_res["intent"] != "UNKNOWN":
                    l_intent = llama_res["intent"]
                    t_id = llama_res.get("target_action_id", "")
                    if l_intent in (APPROVE_PENDING_ACTION, "APPROVE"):
                        return _resolve_approval(cleaned, registry, devstate=devstate, now=now, target_id=t_id)
                    if l_intent in STRUCTURED_INTENTS:
                        return StructuredIntent(
                            intent=l_intent,
                            text=cleaned,
                            legacy_intent=l_intent.lower(),
                        )
            except Exception:
                pass
            return StructuredIntent(
                intent="UNKNOWN",
                text=cleaned,
                needs_clarification=True,
                clarification=(
                    "I didn't catch that. Try 'status', 'push my code', "
                    "'go ahead', 'sleep', or 'stop'."
                ),
                legacy_intent=legacy,
            )
        return StructuredIntent(
            intent=structured, text=cleaned, legacy_intent=legacy
        )
    return _resolve_approval(cleaned, registry, devstate=devstate, now=now)


def _resolve_approval(
    cleaned: str,
    registry: ActionRegistry,
    *,
    devstate: Optional[DeveloperStateEngine],
    now: Optional[float],
    target_id: str = "",
) -> StructuredIntent:
    moment = time.time() if now is None else now
    if target_id:
        return _resolve_explicit_id(target_id, registry, cleaned, moment)
    mentioned = _ACTION_ID_RE.findall(cleaned)
    if mentioned:
        return _resolve_explicit_id(mentioned[0], registry, cleaned, moment)
    pending = registry.list_pending(now=moment)
    if not pending:
        return StructuredIntent(
            intent=APPROVE_PENDING_ACTION,
            text=cleaned,
            needs_clarification=True,
            clarification=(
                "There is no pending action to approve. Describe what you "
                "want in the Replicate tab and I will propose it first."
            ),
            legacy_intent="approve",
        )
    hint = _focus_hint(devstate)
    ranked = _rank_candidates(pending, cleaned, hint)
    if len(ranked) == 1:
        only = ranked[0]
        return StructuredIntent(
            intent=APPROVE_PENDING_ACTION,
            text=cleaned,
            target_action_id=only.action_id,
            authorization_type="explicit_voice_confirmation",
            legacy_intent="approve",
        )
    top_score = _score_of(ranked[0], cleaned, hint)
    tied = [
        s for s in ranked[1:]
        if _score_of(s, cleaned, hint) == top_score
    ]
    if top_score > 0 and not tied:
        return StructuredIntent(
            intent=APPROVE_PENDING_ACTION,
            text=cleaned,
            target_action_id=ranked[0].action_id,
            authorization_type="explicit_voice_confirmation",
            legacy_intent="approve",
        )
    listing = "; ".join(
        f"{s.action_id} ({s.operation}"
        + (f" on {s.target}" if s.target else "")
        + ")"
        for s in ranked[:5]
    )
    return StructuredIntent(
        intent=APPROVE_PENDING_ACTION,
        text=cleaned,
        needs_clarification=True,
        clarification=(
            f"I found {len(ranked)} pending actions: {listing}. "
            "Which one should I approve?"
        ),
        candidates=tuple(s.action_id for s in ranked),
        legacy_intent="approve",
    )


def _resolve_explicit_id(
    action_id: str,
    registry: ActionRegistry,
    cleaned: str,
    moment: float,
) -> StructuredIntent:
    spec = registry.get(action_id, now=moment)
    if spec is None:
        return StructuredIntent(
            intent=APPROVE_PENDING_ACTION,
            text=cleaned,
            needs_clarification=True,
            clarification=(
                f"I don't know any action {action_id!r}. Say 'status' to "
                "hear what is pending, or name the action again."
            ),
            legacy_intent="approve",
        )
    if spec.is_expired(moment) or spec.status.value == "expired":
        return StructuredIntent(
            intent=APPROVE_PENDING_ACTION,
            text=cleaned,
            needs_clarification=True,
            clarification=(
                f"Action {action_id!r} has expired, so I can't approve it. "
                "Ask me to propose it again."
            ),
            legacy_intent="approve",
        )
    if spec.status.value != "pending":
        return StructuredIntent(
            intent=APPROVE_PENDING_ACTION,
            text=cleaned,
            needs_clarification=True,
            clarification=(
                f"Action {action_id!r} is already {spec.status.value}, so "
                "there is nothing to approve."
            ),
            legacy_intent="approve",
        )
    return StructuredIntent(
        intent=APPROVE_PENDING_ACTION,
        text=cleaned,
        target_action_id=spec.action_id,
        authorization_type="explicit_voice_confirmation",
        legacy_intent="approve",
    )


@dataclass
class _TranscriptionJob:
    audio: Any
    future: Future
    correlation_id: str


class TranscriptionWorker:
    """Background Whisper worker with a bounded queue (Qt-free).

    ``transcribe_fn`` maps one audio payload (wav bytes, a file path, or
    a host-defined handle) to a transcript or None. Completed jobs
    publish ``TRANSCRIPTION_COMPLETED`` best-effort; failures are
    delivered through the job's :class:`Future`, never raised into the
    worker thread.
    """

    def __init__(
        self,
        transcribe_fn: TranscribeFn,
        *,
        bus: Optional[EventBus] = None,
        session_id: str = "",
        max_queue: int = 16,
        source: str = "voice.whisper_worker",
    ) -> None:
        if not callable(transcribe_fn):
            raise VoiceActionError("transcribe_fn: expected a callable")
        if (
            isinstance(max_queue, bool)
            or not isinstance(max_queue, int)
            or max_queue < 1
        ):
            raise VoiceActionError(
                f"max_queue: expected a positive integer, got {max_queue!r}"
            )
        self._transcribe_fn = transcribe_fn
        self.bus = bus
        self.session_id = session_id
        self.source = source
        self._jobs: queue.Queue[_TranscriptionJob] = queue.Queue(
            maxsize=max_queue
        )
        self._thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def queued(self) -> int:
        return self._jobs.qsize()

    def start(self) -> None:
        """Start the worker thread (idempotent)."""
        if self.running:
            return
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._loop, name="mimir-whisper-worker", daemon=True
        )
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        """Stop the worker; queued jobs are failed fast, never stranded."""
        self._stopping.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout_s)
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            if not job.future.done():
                job.future.set_exception(
                    VoiceActionError("transcription worker stopped")
                )

    def submit(
        self,
        audio: Any,
        *,
        correlation_id: str = "",
        timeout_s: float = 0.0,
    ) -> Future:
        """Queue one audio payload; returns its transcript future.

        ``timeout_s`` bounds admission waiting (0 = fail fast when full).
        """
        if audio is None:
            raise VoiceActionError("audio: expected an audio payload")
        if not self.running:
            raise VoiceActionError(
                "transcription worker is not running (call start() first)"
            )
        future: Future = Future()
        job = _TranscriptionJob(
            audio=audio, future=future, correlation_id=correlation_id
        )
        try:
            if timeout_s <= 0:
                self._jobs.put_nowait(job)
            else:
                self._jobs.put(job, timeout=timeout_s)
        except queue.Full:
            raise VoiceActionError(
                "transcription queue is full; try again shortly"
            ) from None
        return future

    def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                job = self._jobs.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                text = self._transcribe_fn(job.audio)
            except Exception as exc:  # noqa: BLE001 - delivered via Future
                if not job.future.done():
                    job.future.set_exception(exc)
                continue
            if not job.future.done():
                job.future.set_result(text)
            self._publish(text, job.correlation_id)

    def _publish(self, text: Optional[str], correlation_id: str) -> None:
        try:
            bus = self.bus if self.bus is not None else get_event_bus()
            kwargs: dict[str, Any] = {"session_id": self.session_id}
            if correlation_id:
                kwargs["correlation_id"] = correlation_id
            bus.emit(
                EventType.TRANSCRIPTION_COMPLETED,
                self.source,
                {"text": text or "", "heard": bool(text and text.strip())},
                **kwargs,
            )
        except Exception:
            pass


@dataclass(frozen=True)
class VoiceDecision:
    """Outcome of handling one transcript (reply is the TTS feedback)."""

    structured_intent: StructuredIntent
    reply: str
    authorized_action_id: str = ""
    authorization: Optional[AuthorizationOutcome] = None
    speak_error: str = ""


def _default_reply(intent: StructuredIntent) -> str:
    if intent.needs_clarification:
        return intent.clarification
    name = intent.intent
    if name == "STOP":
        return "Powering down. Say the word if you need me."
    if name == "SLEEP":
        return "Resting. Say the word when you need me."
    if name == "STATUS_INQUIRY":
        return (
            "Here's what I have learned so far — "
            "check the Memory tab for the full list."
        )
    if name == "PUSH_CODE":
        return (
            "I'll draft your push playbook in the Replicate tab. "
            "Say 'go ahead' once I've proposed it."
        )
    if name == "FORBID":
        return f"Noted and avoided from now on: {intent.text.strip()}"
    return intent.clarification or "Done."


class VoiceActionService:
    """Qt-free voice → intent → registry → TTS orchestration.

    Owns no threads except short-lived daemon speak threads (when
    ``speak_async`` is true) and an optional :class:`TranscriptionWorker`
    passed in by the host. All runtime coordination flows through the
    event bus; ``speak_fn`` defaults to the coordinator's TTS backend.
    With a safety controller attached, a STOP intent latches the E-stop
    and approvals are denied while it is latched (voice is a trip
    source; the controller itself never depends on voice).
    """

    def __init__(
        self,
        coordinator: VoiceCoordinator,
        registry: ActionRegistry,
        *,
        bus: Optional[EventBus] = None,
        devstate: Optional[DeveloperStateEngine] = None,
        session_id: str = "",
        worker: Optional[TranscriptionWorker] = None,
        reply_fn: Optional[ReplyFn] = None,
        speak_fn: Optional[SpeakFn] = None,
        granted_permissions: Collection[str] = VOICE_GRANTED_PERMISSIONS,
        speak_async: bool = True,
        safety: Optional[SafetyController] = None,
    ) -> None:
        if not isinstance(coordinator, VoiceCoordinator):
            raise VoiceActionError(
                "coordinator: expected VoiceCoordinator, "
                f"got {type(coordinator).__name__}"
            )
        if not isinstance(registry, ActionRegistry):
            raise VoiceActionError(
                "registry: expected ActionRegistry, "
                f"got {type(registry).__name__}"
            )
        if reply_fn is not None and not callable(reply_fn):
            raise VoiceActionError("reply_fn: expected a callable")
        if speak_fn is not None and not callable(speak_fn):
            raise VoiceActionError("speak_fn: expected a callable")
        if safety is not None and not isinstance(safety, SafetyController):
            raise VoiceActionError(
                "safety: expected SafetyController or None, "
                f"got {type(safety).__name__}"
            )
        self.coordinator = coordinator
        self.registry = registry
        self.bus = bus
        self.devstate = devstate
        self.session_id = session_id
        self.safety = safety
        self.worker = worker
        self._reply_fn = reply_fn
        self._speak_fn = speak_fn
        self.granted_permissions = tuple(granted_permissions)
        self.speak_async = speak_async

    # -- orchestration --------------------------------------------------
    def handle_transcript(
        self,
        text: str,
        *,
        correlation_id: str = "",
        now: Optional[float] = None,
    ) -> VoiceDecision:
        """Parse, resolve, authorize, publish, and speak one transcript."""
        structured = parse_structured_intent(
            text, self.coordinator, self.registry,
            devstate=self.devstate, now=now,
        )
        payload = structured.to_dict()
        hint = _focus_hint(self.devstate)
        if hint:
            payload["developer_state"] = hint
        self._emit(
            EventType.INTENT_PARSED, "voice.intent_parser", payload,
            correlation_id=correlation_id,
        )
        reply: str
        authorized_id = ""
        authorization: Optional[AuthorizationOutcome] = None
        if structured.intent == APPROVE_PENDING_ACTION:
            reply, authorized_id, authorization = self._handle_approval(
                structured, correlation_id=correlation_id, now=now
            )
        elif structured.needs_clarification:
            reply = structured.clarification
        else:
            if structured.intent == "STOP" and self.safety is not None:
                try:
                    self.safety.trip(
                        source="voice-stop",
                        reason=structured.text.strip()[:200],
                    )
                except Exception:
                    pass  # trip never raises; stay fail-safe regardless
            reply_fn = self._reply_fn or _default_reply
            try:
                reply = reply_fn(structured)
            except Exception:
                reply = _default_reply(structured)
            if not isinstance(reply, str) or not reply.strip():
                reply = _default_reply(structured)
        speak_error = self._speak(reply)
        return VoiceDecision(
            structured_intent=structured,
            reply=reply,
            authorized_action_id=authorized_id,
            authorization=authorization,
            speak_error=speak_error,
        )

    def handle_audio(
        self,
        audio: Any,
        *,
        correlation_id: str = "",
        timeout_s: float = 30.0,
    ) -> VoiceDecision:
        """Transcribe one audio payload, then handle it as a transcript."""
        worker = self.worker
        if worker is None:
            raise VoiceActionError(
                "handle_audio: no TranscriptionWorker configured"
            )
        future = worker.submit(audio, correlation_id=correlation_id)
        try:
            text = future.result(timeout=timeout_s)
        except Exception as exc:  # noqa: BLE001 - surfaced as reply
            reply = f"Sorry, transcription failed: {exc}"
            self._speak(reply)
            return VoiceDecision(
                structured_intent=StructuredIntent(
                    intent="UNKNOWN",
                    text="",
                    needs_clarification=True,
                    clarification=reply,
                    legacy_intent="unknown",
                ),
                reply=reply,
            )
        if not text or not text.strip():
            reply = "No speech heard — try again."
            self._speak(reply)
            return VoiceDecision(
                structured_intent=StructuredIntent(
                    intent="UNKNOWN",
                    text="",
                    needs_clarification=True,
                    clarification=reply,
                    legacy_intent="unknown",
                ),
                reply=reply,
            )
        return self.handle_transcript(text, correlation_id=correlation_id)

    # -- approval ---------------------------------------------------------
    def _handle_approval(
        self,
        structured: StructuredIntent,
        *,
        correlation_id: str,
        now: Optional[float],
    ) -> tuple[str, str, Optional[AuthorizationOutcome]]:
        if structured.needs_clarification or not structured.target_action_id:
            self._emit(
                EventType.ACTION_CONFIRMATION_RECEIVED,
                "voice.intent_parser",
                {
                    "ok": False,
                    "reason": structured.clarification,
                    "candidates": list(structured.candidates),
                },
                correlation_id=correlation_id,
            )
            return structured.clarification, "", None
        if self.safety is not None and self.safety.estopped:
            reason = (
                "Safety stop is active. Resolve the stop and reauthorize "
                "before I can act."
            )
            self._emit(
                EventType.ACTION_CONFIRMATION_RECEIVED,
                "voice.intent_parser",
                {
                    "ok": False,
                    "action_id": structured.target_action_id,
                    "reason": reason,
                },
                correlation_id=correlation_id,
            )
            return reason, "", None
        outcome = self.registry.authorize(
            structured.target_action_id,
            authorization_type=(
                structured.authorization_type or
                "explicit_voice_confirmation"
            ),
            granted_permissions=self.granted_permissions,
            now=now,
        )
        self._emit(
            EventType.ACTION_CONFIRMATION_RECEIVED,
            "voice.intent_parser",
            {
                "ok": outcome.ok,
                "action_id": outcome.action_id,
                "authorization_type": (
                    structured.authorization_type
                    or "explicit_voice_confirmation"
                ),
                "reason": outcome.reason,
            },
            correlation_id=correlation_id,
        )
        if not outcome.ok:
            return outcome.reason, "", outcome
        spec = self.registry.get(outcome.action_id, now=now)
        detail = dict(spec.to_dict()) if spec is not None else {
            "action_id": outcome.action_id,
        }
        self._emit(
            EventType.ACTION_REQUESTED, "voice.action_service", detail,
            correlation_id=correlation_id,
        )
        where = ""
        if spec is not None:
            where = spec.target or spec.file_or_element
        reply = (
            f"Approved: {spec.operation if spec else outcome.action_id}"
            + (f" on {where}" if where else "")
            + "."
        )
        return reply, outcome.action_id, outcome

    # -- plumbing -----------------------------------------------------------
    def _emit(
        self,
        event_type: EventType,
        source: str,
        payload: dict[str, Any],
        *,
        correlation_id: str = "",
    ) -> None:
        try:
            bus = self.bus if self.bus is not None else get_event_bus()
            kwargs: dict[str, Any] = {"session_id": self.session_id}
            if correlation_id:
                kwargs["correlation_id"] = correlation_id
            bus.emit(event_type, source, payload, **kwargs)
        except Exception:
            pass

    def _speak(self, reply: str) -> str:
        """Speak TTS feedback; returns "" or the (sync) error message."""
        speak = self._speak_fn or self.coordinator.speak
        if not self.speak_async:
            try:
                speak(reply)
            except Exception as exc:
                return f"{type(exc).__name__}: {exc}"
            return ""
        errors: list[str] = []

        def _run() -> None:
            try:
                speak(reply)
            except Exception as exc:  # noqa: BLE001 - recorded, not raised
                errors.append(f"{type(exc).__name__}: {exc}")

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return ""
