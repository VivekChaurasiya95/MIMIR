"""Shared desktop state: one store, visor, voice, and episode cache.

Qt-free so headless tests and the CLI can drive the same core-logic calls
the tabs use. Hardware backends stay lazy: constructing :class:`AppState`
never touches the microphone, speakers, cursor, or Qt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from uuid import uuid4

from mimir.actions import ActionRegistry, VOICE_GRANTED_PERMISSIONS
from mimir.actor import ReplicationActor, ReplicationRequest, ReplicationResult
from mimir.detective import infer_principles
from mimir.episodes import BehavioralEpisode
from mimir.events import EventBus, EventType, get_event_bus
from mimir.principles import BehavioralPrinciple
from mimir.safety import SafetyController
from mimir.store import PrincipleStore
from mimir.telemetry import observe_git_repo, redact_text
from mimir.ui.visor import VisorController, VisorState
from mimir.voice import VoiceCoordinator
from mimir.voice_action import StructuredIntent, VoiceActionService

from .prefs import DesktopPrefs

__all__ = ["AppState", "DEFAULT_STORE_PATH", "get_llm_fn"]

DEFAULT_STORE_PATH = "mimir_memory.db"


def get_llm_fn(prefs: DesktopPrefs) -> Optional[Callable[[str], str]]:
    """Build a pluggable LLM completion function from user preferences.

    Returns None when the provider is offline (or unknown), or when a
    cloud provider is selected without an API key — the caller then
    falls back to the offline heuristic scaffold.
    """
    from .llm import (
        DEFAULT_GEMINI_MODEL,
        DEFAULT_OLLAMA_ENDPOINT,
        DEFAULT_OLLAMA_MODEL,
        DEFAULT_OPENAI_MODEL,
        build_gemini_client,
        build_ollama_client,
        build_openai_client,
        normalize_provider,
    )

    provider = normalize_provider(
        getattr(prefs, "model_provider", "offline") or "offline")
    if provider == "offline":
        return None
    if provider == "ollama":
        endpoint = (getattr(prefs, "model_endpoint", "") or "").strip()
        model = (getattr(prefs, "model_name", "") or "").strip()
        return build_ollama_client(
            endpoint=endpoint or DEFAULT_OLLAMA_ENDPOINT,
            model=model or DEFAULT_OLLAMA_MODEL,
        )
    api_key = (getattr(prefs, "api_key", "") or "").strip()
    if not api_key:
        return None
    model = (getattr(prefs, "model_name", "") or "").strip()
    if provider == "openai":
        return build_openai_client(api_key=api_key,
                                   model=model or DEFAULT_OPENAI_MODEL)
    if provider == "gemini":
        return build_gemini_client(api_key=api_key,
                                   model=model or DEFAULT_GEMINI_MODEL)
    if provider == "anthropic":
        from .llm import DEFAULT_ANTHROPIC_MODEL, build_anthropic_client
        return build_anthropic_client(
            api_key=api_key, model=model or DEFAULT_ANTHROPIC_MODEL)
    return None  # Graceful fallback to offline scaffold


@dataclass
class AppState:
    """Single source of truth shared by every desktop tab."""

    store_path: str = DEFAULT_STORE_PATH
    episodes: list[BehavioralEpisode] = field(default_factory=list)
    learned_ids: set[str] = field(default_factory=set)
    watching: bool = False
    prefs: DesktopPrefs = field(default_factory=DesktopPrefs)
    visor: VisorController = field(default_factory=VisorController)
    voice: VoiceCoordinator = field(
        default_factory=lambda: VoiceCoordinator(
            stt_backend=lambda timeout: None,
            tts_backend=lambda text: None,
        )
    )
    _store: Optional[PrincipleStore] = field(default=None, repr=False)
    session_id: str = field(
        default_factory=lambda: f"sess_{uuid4().hex[:8]}")
    event_bus: Optional[EventBus] = field(default=None, repr=False)
    # Shared safety latch + pending-action registry for this desktop
    # session (plan sections 4 + 8). Every drive/showcase/voice path
    # built from this state gates on the same controller, so one E-stop
    # invalidates all outstanding work. Per-state (not process-global)
    # so headless tests stay isolated from each other.
    safety: SafetyController = field(default_factory=SafetyController)
    action_registry: Optional[ActionRegistry] = field(
        default=None, repr=False)

    def __post_init__(self) -> None:
        if self.action_registry is None:
            self.action_registry = ActionRegistry(
                bus=self.event_bus, session_id=self.session_id,
                safety=self.safety,
            )

    def _emit(self, event_type: EventType, source: str,
              payload: dict[str, Any]) -> None:
        """Best-effort runtime event; never breaks the calling flow."""
        try:
            bus = self.event_bus if self.event_bus is not None \
                else get_event_bus()
            bus.emit(event_type, source, payload,
                     session_id=self.session_id)
        except Exception:
            pass

    # -- store lifecycle -------------------------------------------------
    @property
    def store(self) -> PrincipleStore:
        if self._store is None:
            self._store = PrincipleStore(self.store_path)
        return self._store

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None

    def reopen(self, path: str) -> None:
        self.close()
        self.store_path = path

    # -- Observe tab (telemetry + detective) ------------------------------
    def observe(
        self,
        repo: str,
        allowed_roots: Sequence[str],
        max_commits: int = 10,
    ) -> list[BehavioralEpisode]:
        self.visor.set_state(VisorState.OBSERVING)
        try:
            found = observe_git_repo(
                repo, allowed_roots=list(allowed_roots),
                max_commits=max_commits,
            )
        finally:
            self.visor.set_state(VisorState.RESTING)
        self.episodes = list(found)
        self._emit(EventType.GIT_CHANGED, "sensory.git_observer", {
            "repo": repo,
            "episode_ids": [e.id for e in found],
            "count": len(found),
        })
        return self.episodes

    def learn(
        self,
        episodes: Optional[Sequence[BehavioralEpisode]] = None,
        min_cluster_size: int = 1,
    ) -> list[BehavioralPrinciple]:
        """Cluster episodes, infer principles, persist them to the store."""
        self.visor.set_state(VisorState.THINKING)
        try:
            used = (list(episodes) if episodes is not None
                    else list(self.episodes))
            principles = infer_principles(
                used, min_cluster_size=min_cluster_size)
            for principle in principles:
                self.store.save(principle)
            self.learned_ids.update(e.id for e in used)
            self._emit(EventType.INFERENCE_COMPLETED,
                       "cognitive.inference_engine", {
                           "principle_ids": [p.id for p in principles],
                           "episode_ids": [e.id for e in used],
                           "count": len(principles),
                       })
            return principles
        finally:
            self.visor.set_state(VisorState.RESTING)

    # -- Memory tab (store) ----------------------------------------------
    def list_principles(self) -> list[BehavioralPrinciple]:
        return self.store.list_all()

    def search(self, query: str, top_k: int = 5,
               min_confidence: float = 0.0) -> list[BehavioralPrinciple]:
        return self.store.query_relevant(
            query, top_k=top_k, min_confidence=min_confidence)

    def adjust_confidence(self, principle_id: str, delta: float,
                          evidence_id: str) -> None:
        self.store.update_confidence(principle_id, delta, evidence_id)

    def delete_principle(self, principle_id: str) -> bool:
        return self.store.delete(principle_id)

    def clear_memory(self) -> int:
        """Emergency wipe: delete every principle and drop episodes."""
        ids = [p.id for p in self.list_principles()]
        for principle_id in ids:
            self.store.delete(principle_id)
        self.episodes = []
        return len(ids)

    def observe_new(
        self,
        repo: str,
        allowed_roots: Sequence[str],
        max_commits: int = 10,
    ) -> list[BehavioralEpisode]:
        """One watcher pass: prepend only episodes not seen before.

        Unlike :meth:`observe`, the visor is left untouched — the watch
        loop owns the OBSERVING state for its whole lifetime.
        """
        found = observe_git_repo(
            repo, allowed_roots=list(allowed_roots),
            max_commits=max_commits,
        )
        seen = {episode.id for episode in self.episodes}
        fresh = [episode for episode in found if episode.id not in seen]
        self.episodes = fresh + self.episodes
        if fresh:
            self._emit(EventType.GIT_CHANGED, "sensory.git_observer", {
                "repo": repo,
                "episode_ids": [e.id for e in fresh],
                "count": len(fresh),
            })
        return fresh

    def set_watching(self, watching: bool) -> None:
        self.watching = watching
        self.visor.set_state(
            VisorState.OBSERVING if watching else VisorState.RESTING)

    def voice_coordinator(self) -> VoiceCoordinator:
        """Speech coordinator honoring the user's voice/language prefs."""
        language = (getattr(self.prefs, "language", "") or "").strip().lower()
        whisper_lang = "hi" if "hindi" in language or "hinglish" in language else "en"
        return VoiceCoordinator(
            voice=(getattr(self.prefs, "voice_persona", "") or "").strip()
            or "en-US-AriaNeural",
            language=whisper_lang,
        )

    def try_listen(self, timeout_sec: float = 5.0,
                   coordinator: VoiceCoordinator | None = None
                   ) -> tuple[bool, str]:
        """Attempt one microphone capture; (ok, transcript-or-reason)."""
        voice = (coordinator if coordinator is not None
                 else self.voice_coordinator())
        try:
            heard = voice.listen_phrase(timeout_sec=timeout_sec)
        except Exception as exc:  # missing mic/whisper stays in-app
            return (False, str(exc))
        if not heard:
            return (False, "No speech heard — try again.")
        return (True, heard)

    # -- Replicate tab (live LLM when configured, else offline) -----------
    def replicate(
        self,
        task: str,
        target_language: Optional[str] = None,
        target_scope: Optional[str] = None,
        min_confidence: float = 0.0,
        top_k: int = 5,
        llm_fn: Any = None,
        force_offline: bool = False,
    ) -> ReplicationResult:
        if force_offline:
            effective = None
        else:
            effective = (llm_fn if llm_fn is not None
                         else get_llm_fn(self.prefs))
        actor = ReplicationActor(self.store, llm_fn=effective)
        contrast = (effective is not None
                    and getattr(self.prefs, "contrast_baseline", True))
        self.visor.set_state(VisorState.THINKING)
        try:
            result = actor.replicate(
                ReplicationRequest(
                    task_description=task,
                    target_language=target_language or None,
                    target_scope=target_scope or None,
                    min_confidence=min_confidence,
                    top_k_principles=top_k,
                ),
                generate_contrast_baseline=contrast,
            )
            self._emit(EventType.ACTION_PROPOSED,
                       "cognitive.action_planner", {
                           "task": task[:500],
                           "applied_ids": [p.id for p in
                                           result.applied_principles],
                       })
            return result
        finally:
            self.visor.set_state(VisorState.RESTING)

    # -- Act & Voice tab ---------------------------------------------------
    def parse_utterance(self, text: str) -> dict[str, str]:
        return self.voice.parse_intent(text)

    def voice_service(
        self,
        coordinator: Optional[VoiceCoordinator] = None,
        **kwargs: Any,
    ) -> VoiceActionService:
        """Build a voice→registry→TTS service bound to this state.

        Replies reuse the legacy companion mapping below, so hosted
        flows (Home mic, CLI cobot) keep their exact wording while
        approvals resolve against the shared action registry and the
        shared safety controller. Extra keyword arguments pass through
        to :class:`VoiceActionService` (``speak_fn``, ``speak_async``,
        ``devstate``, ...).
        """
        assert self.action_registry is not None  # built in __post_init__
        return VoiceActionService(
            coordinator if coordinator is not None else self.voice,
            self.action_registry,
            bus=self.event_bus,
            session_id=self.session_id,
            safety=self.safety,
            reply_fn=self._service_reply,
            **kwargs,
        )

    def _service_reply(self, structured: StructuredIntent) -> str:
        """Companion reply for a structured intent (no re-emit)."""
        legacy = structured.legacy_intent or "unknown"
        return self._legacy_reply(legacy, structured.text)

    def _try_approve_pending(self, text: str) -> Optional[str]:
        """Bind "go ahead" to one pending action (None: no candidates).

        Returns the speakable outcome (approval, denial reason, or
        clarification) whenever the registry holds pending work, so a
        confirmation never authorizes an unrelated operation. Returns
        None only when nothing is pending (caller falls back to the
        legacy guidance) or the pipeline itself is unavailable.
        """
        from mimir.voice_action import parse_structured_intent

        registry = self.action_registry
        if registry is None:
            return None
        try:
            if not registry.list_pending():
                return None
            structured = parse_structured_intent(
                text, self.voice, registry)
        except Exception:
            return None
        if (structured.needs_clarification
                or not structured.target_action_id):
            return structured.clarification or None
        try:
            outcome = registry.authorize(
                structured.target_action_id,
                authorization_type=(
                    structured.authorization_type
                    or "explicit_voice_confirmation"
                ),
                granted_permissions=VOICE_GRANTED_PERMISSIONS,
            )
        except Exception:
            return None
        if not outcome.ok:
            return outcome.reason
        spec = registry.get(outcome.action_id)
        if spec is not None:
            self._emit(EventType.ACTION_REQUESTED,
                       "voice.action_service", spec.to_dict())
            where = spec.target or spec.file_or_element
            return (f"Approved: {spec.operation}"
                    + (f" on {where}" if where else "") + ".")
        return f"Approved: {outcome.action_id}."

    def respond(self, intent: str, text: str) -> str:
        """Companion reply for a parsed intent (mirrors the CLI cobot)."""
        self._emit(EventType.INTENT_PARSED, "voice.intent_parser", {
            "intent": intent,
            "text": text[:200],
        })
        if intent == "stop":
            # A spoken stop is an E-stop source (plan section 8): latch
            # the shared controller so outstanding drives die, then
            # answer with the usual farewell. Resuming needs deliberate
            # reauthorization (drive dialog / showcase correction).
            try:
                self.safety.trip(source="voice-stop",
                                 reason=text.strip()[:200])
            except Exception:
                pass
            return "Powering down. Say the word if you need me."
        if intent == "approve":
            approved = self._try_approve_pending(text)
            if approved is not None:
                return approved
        return self._legacy_reply(intent, text)

    def _legacy_reply(self, intent: str, text: str) -> str:
        """Companion wording for a parsed intent (no side effects)."""
        from .presenters import format_memory_summary

        if intent == "stop":
            return "Powering down. Say the word if you need me."
        if intent == "sleep":
            return "Resting. Switch tabs or press Rest / Wake when ready."
        if intent == "status_inquiry":
            return f"Here is what I have learned:\n{format_memory_summary(self.list_principles())}"
        if intent == "push_code":
            hits = self.search("commit push github workflow",
                               top_k=3, min_confidence=0.0)
            if not hits:
                return ("I have no push principles yet. Observe a repo where "
                        "you commit and push, press Learn, and I will pick up "
                        "the workflow.")
            names = ", ".join(p.name for p in hits)
            return (f"My push playbook ({len(hits)} principles: {names}). "
                    "Describe the task in Replicate and I will draft it in "
                    "your style.")
        if intent == "approve":
            return "Confirmed. Describe the task in the Replicate tab."
        if intent == "forbid":
            return f"Noted and avoided from now on: {text.strip()}"
        return ("I didn't catch that. Try 'status', 'push my code', "
                "'sleep', or 'stop'.")

    def memory_stats(self) -> dict[str, float]:
        """Dashboard figures: principle count and mean confidence."""
        principles = self.list_principles()
        if not principles:
            return {"count": 0, "avg_confidence": 0.0}
        mean = sum(p.epistemic_status.confidence_score
                   for p in principles) / len(principles)
        return {"count": len(principles), "avg_confidence": round(mean, 2)}

    def set_visor(self, state: str) -> VisorState:
        target = VisorState(state.strip().lower())
        self.visor.set_state(target)
        return target

    # -- Trust tab ---------------------------------------------------------
    @staticmethod
    def redact_preview(text: str) -> dict[str, Any]:
        result = redact_text(text)
        return {"text": result.text, "kinds": list(result.kinds),
                "count": result.count}

    @staticmethod
    def default_roots() -> list[str]:
        return [str(Path.cwd().resolve())]
