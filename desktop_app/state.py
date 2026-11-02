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

#: Local-Llama structured intents -> legacy companion intent names.
_LLAMA_TO_LEGACY = {
    "STOP": "stop",
    "SLEEP": "sleep",
    "STATUS_INQUIRY": "status_inquiry",
    "PUSH_CODE": "push_code",
    "FORBID": "forbid",
    "APPROVE_PENDING_ACTION": "approve",
    "APPROVE": "approve",
}


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
    _sensory: Any = field(default=None, repr=False)
    _sensory_tokens: list[Any] = field(default_factory=list, repr=False)
    # Latest one-line activity insight while wandering ("Notices: ...").
    last_activity_note: str = ""

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
        self.stop_senses()
        if self._store is not None:
            self._store.close()
            self._store = None

    def reopen(self, path: str) -> None:
        self.close()
        self.store_path = path

    # -- Live Ambient Senses (Phase 4.1 & 4.2) ----------------------------
    @property
    def live_senses(self) -> Optional[dict[str, Any]]:
        """Introspection snapshot of background sensory watcher (or None)."""
        if self._sensory is None:
            return None
        try:
            return dict(self._sensory.status())
        except Exception:
            return None

    def start_senses(
        self,
        roots: Optional[Sequence[str]] = None,
        repo: Optional[str] = None,
        debounce_s: float = 0.3,
    ) -> bool:
        """Launch watchdog FS monitor & AST streaming inside AppState (Phase 4.1).

        Monitors workspace roots, computes 300ms-debounced AST deltas, streams
        structural changes to the Detective engine (Phase 4.2), and binds
        the Visor to the event bus for reactive micro-animations (Phase 4.3).
        """
        self.stop_senses()
        try:
            from pathlib import Path
            from mimir.ast_delta import ast_delta_to_episode
            from mimir.events import Event, bind_visor_to_bus, get_event_bus
            from mimir.sensory import SensoryService
        except ImportError:
            return False

        effective_roots = list(roots or [])
        if not effective_roots:
            effective_roots = list(self.default_roots())
        target_repo = repo or self.prefs.last_repo or ""
        if target_repo and target_repo not in effective_roots:
            effective_roots.append(target_repo)
        live = [r for r in effective_roots if Path(r).is_dir()]
        if not live:
            return False

        bus = self.event_bus if self.event_bus is not None else get_event_bus()
        try:
            service = SensoryService(
                live,
                repo=target_repo or None,
                allowed_roots=effective_roots,
                bus=bus,
                session_id=self.session_id,
                debounce_s=debounce_s,
            )
            service.start()
        except Exception:
            return False
        self._sensory = service

        tokens: list[Any] = []
        try:
            tokens.extend(bind_visor_to_bus(self.visor, bus))
        except Exception:
            pass

        # Stream AST structural modifications to Detective engine (Phase 4.2)
        def _on_ast_delta(event: Event) -> None:
            payload = event.payload if isinstance(event.payload, dict) else {}
            try:
                ep = ast_delta_to_episode(payload, repo=target_repo)
            except Exception:
                ep = None
            if ep is not None:
                # Add to state episodes cache for Detective inference
                seen = {e.id for e in self.episodes}
                if ep.id not in seen:
                    self.episodes.insert(0, ep)
                self._emit(EventType.PATTERN_DETECTED, "sensory.ast_stream", {
                    "episode_id": ep.id,
                    "summary": ep.summary,
                    "file": payload.get("file"),
                })

        try:
            tokens.append(
                bus.subscribe(
                    EventType.AST_DELTA,
                    _on_ast_delta,
                    name="state:ast_to_detective",
                )
            )
        except Exception:
            pass

        self._sensory_tokens = tokens
        return True

    def stop_senses(self) -> None:
        """Stop background senses daemon and unbind tokens (idempotent)."""
        service, self._sensory = self._sensory, None
        if service is not None:
            try:
                service.stop()
            except Exception:
                pass
        tokens, self._sensory_tokens = self._sensory_tokens, []
        for t in tokens:
            try:
                t.unsubscribe()
            except Exception:
                pass

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
        try:
            self.prefs.lifetime_episodes = (
                int(self.prefs.lifetime_episodes) + len(found))
        except (AttributeError, TypeError, ValueError):
            pass
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
            return self.commit_principles(
                principles, [e.id for e in used])
        finally:
            self.visor.set_state(VisorState.RESTING)

    def commit_principles(
        self,
        principles: Sequence[BehavioralPrinciple],
        episode_ids: Optional[Sequence[str]] = None,
    ) -> list[BehavioralPrinciple]:
        """Persist mined principles (main-thread half of worker learning).

        The Observe tab mines on a worker thread (sqlite connections
        are thread-bound) and commits here on the UI thread, keeping
        one persist + telemetry path with :meth:`learn`.
        """
        stored = list(principles)
        for principle in stored:
            self.store.save(principle)
        if episode_ids:
            self.learned_ids.update(episode_ids)
        self._emit(EventType.INFERENCE_COMPLETED,
                   "cognitive.inference_engine", {
                       "principle_ids": [p.id for p in stored],
                       "episode_ids": list(episode_ids or []),
                       "count": len(stored),
                   })
        return stored

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
        try:
            self.prefs.lifetime_episodes = (
                int(self.prefs.lifetime_episodes) + len(fresh))
        except (AttributeError, TypeError, ValueError):
            pass
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
        """Speech coordinator honoring the user's voice/language prefs.

        Cached per (voice, language) key so repeated calls reuse one
        coordinator, while a Settings change rebuilds it instead of
        serving a stale voice or transcription language.
        """
        language = (getattr(self.prefs, "language", "") or "").strip().lower()
        whisper_lang = ("hi" if "hindi" in language or "hinglish" in language
                        else "en")
        voice_name = ((getattr(self.prefs, "voice_persona", "") or "").strip()
                      or "en-US-AriaNeural")
        key = (voice_name, whisper_lang)
        inst = getattr(self, "_voice_coordinator_instance", None)
        if inst is None or getattr(self, "_voice_coordinator_key", None) != key:
            inst = VoiceCoordinator(voice=voice_name, language=whisper_lang)
            self._voice_coordinator_instance = inst
            self._voice_coordinator_key = key
        return inst

    def stop_speaking(self) -> None:
        """Immediately interrupt and silence any active speech synthesis or playback."""
        inst = getattr(self, "_voice_coordinator_instance", None)
        if inst is not None:
            try:
                inst.stop_speaking()
            except Exception:
                pass
        worker = getattr(self, "_active_speak_worker", None)
        if worker is not None:
            try:
                worker.cancel()
            except Exception:
                pass
            self._active_speak_worker = None

    def mic_status(self) -> dict[str, Any]:
        """Voice-input chain status for the Home mic button (never raises).

        Returns ``{"available", "backend", "microphones", "message"}``.
        Injected (callable) STT backends need no hardware and report
        available via ``"injected"``; the live Whisper path needs both
        a capture backend (see :func:`mimir.voice.audio_backend_status`)
        and importable ``openai-whisper``. ``message`` is empty when
        available, otherwise plain guidance naming the missing piece.
        """
        try:
            return self._mic_status()
        except Exception as exc:  # a probe must never break its caller
            return {
                "available": False,
                "backend": "none",
                "microphones": [],
                "message": f"Microphone unavailable ({exc}).",
            }

    def _mic_status(self) -> dict[str, Any]:
        coordinator = self.voice_coordinator()
        if callable(getattr(coordinator, "stt_backend", None)):
            return {
                "available": True,
                "backend": "injected",
                "microphones": [],
                "message": "",
            }
        from mimir.voice import (
            _transcription_status,
            audio_backend_status,
        )

        status = audio_backend_status()
        if not status.get("available"):
            return {
                "available": False,
                "backend": "none",
                "microphones": [],
                "message": str(status.get("hint") or "Microphone unavailable."),
            }
        ready, hint = _transcription_status()
        if not ready:
            return {
                "available": False,
                "backend": str(status.get("backend") or "none"),
                "microphones": list(status.get("microphones") or []),
                "message": hint,
            }
        return {
            "available": True,
            "backend": str(status.get("backend") or "none"),
            "microphones": list(status.get("microphones") or []),
            "message": "",
        }

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

    def parse_utterance_smart(
        self, text: str, *, timeout_s: float = 3.5
    ) -> dict[str, str]:
        """Parse an utterance, consulting local Llama when regex is unsure.

        Fast path: the deterministic keyword parser wins whenever it
        recognizes the text (it owns safety words like "stop"). Only an
        ``unknown`` verdict falls through to
        :func:`mimir.llama_intent.query_local_llama_intent`, whose
        structured intent is mapped back onto the legacy intent names
        :meth:`respond` understands. Any Llama failure (daemon down,
        timeout, unparseable reply) degrades to the regex verdict, so
        this never raises for backend reasons. May include a ``"reply"``
        key with Llama's speakable confirmation.
        """
        parsed = self.parse_utterance(text)
        if parsed.get("intent") not in ("unknown", None, ""):
            return parsed
        try:
            from mimir.llama_intent import query_local_llama_intent

            pending = None
            registry = self.action_registry
            if registry is not None:
                try:
                    pending = registry.list_pending()
                except Exception:
                    pending = None
            result = query_local_llama_intent(
                parsed.get("text", text),
                pending_actions=pending,
                timeout_s=timeout_s,
            )
        except Exception:
            return parsed
        if not result:
            return parsed
        legacy = _LLAMA_TO_LEGACY.get(
            str(result.get("intent", "UNKNOWN")).strip().upper())
        if legacy is None:
            return parsed
        smart = {"intent": legacy, "text": parsed.get("text", text)}
        reply = str(result.get("reply", "") or "").strip()
        if reply:
            smart["reply"] = reply
        return smart

    def answer_free_text(
        self, text: str, *, timeout_s: float = 8.0
    ) -> dict[str, str]:
        """Answer open-ended text via the Llama-3.2 foundation.

        Tries the on-device foundation model (grounded in the top
        memory principles) and fails closed to an offline
        memory-grounded answer — never raises for backend reasons, so
        the Home tab can call it on a worker and always render.
        Returns ``{"answer", "source"}`` with source ``"llama"`` or
        ``"memory"``.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            raise ValueError("text: expected a non-empty question")
        try:
            from mimir.foundation import (
                build_foundation_prompt,
                classify_task_kind,
                query_local_llama_text,
            )
            kind = classify_task_kind(cleaned)
            try:
                hits = self.search(cleaned, top_k=4, min_confidence=0.0)
            except Exception:
                hits = []
            prompt = build_foundation_prompt(
                cleaned, principles=hits, task_kind=kind)
            endpoint = (
                getattr(self.prefs, "model_endpoint", "") or "").strip()
            model = (
                getattr(self.prefs, "model_name", "") or "").strip()
            answer = query_local_llama_text(
                prompt,
                endpoint=endpoint or
                "http://127.0.0.1:11434",
                model=model or None,
                timeout_s=timeout_s,
            )
        except ValueError:
            raise
        except Exception:
            answer = None
        if answer:
            return {"answer": answer, "source": "llama"}
        return {"answer": self._offline_answer(cleaned),
                "source": "memory"}

    def _offline_answer(self, text: str) -> str:
        """Memory-grounded answer when Llama is unreachable."""
        try:
            hits = self.search(text, top_k=3, min_confidence=0.0)
        except Exception:
            hits = []
        lines = [f'You asked: "{text[:200]}"']
        if hits:
            names = "; ".join(
                f"{h.name} [{h.id}]" for h in hits)
            lines.append(f"Relevant habits in your memory: {names}.")
            lines.append("Head to the Replicate tab to apply them to "
                         "this task.")
        else:
            lines.append("Your Behavioral Memory has no matching habits "
                         "yet — Observe a repo and press Learn Principles "
                         "so Habi answers in your style.")
        lines.append("Tip: start the on-device Llama-3.2 engine "
                     "(Settings → Model → Start Ollama) for full "
                     "conversational answers.")
        return "\n".join(lines)

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



    def register_negative_invariant(self, rule: str) -> Any:
        """Register an instant negative invariant in memory (e.g. from E-stop correction)."""
        import uuid
        from mimir.principles import (
            BehavioralPrinciple,
            ContextTrigger,
            EpistemicStatus,
        )
        cleaned = rule.strip()
        p = BehavioralPrinciple(
            id=f"bp_forbid_{uuid.uuid4().hex[:8]}",
            name=f"Negative invariant: {cleaned[:32]}",
            category="safety",
            context_trigger=ContextTrigger(scope="safety", language_agnostic=True),
            latent_invariant=cleaned,
            anti_patterns=(cleaned,),
            epistemic_status=EpistemicStatus.new(0.99, supporting=1),
            inspectable_traces=("e_stop_correction",),
        )
        self.store.save(p)
        self._emit(EventType.INFERENCE_COMPLETED, "safety.invariant_learner", {
            "principle_ids": [p.id],
            "rule": cleaned,
        })
        return p

    def propose_git_push(self, repo_path: str = "") -> dict[str, Any]:
        """Inspect repository, verify safety invariants, and propose git push (Phase 5)."""
        import subprocess
        from pathlib import Path
        from mimir.actions import ActionSpec, ActionStatus

        target_dir = Path(repo_path).resolve() if repo_path else (
            Path(self.prefs.last_repo).resolve() if self.prefs.last_repo and Path(self.prefs.last_repo).is_dir()
            else Path.cwd().resolve()
        )

        branch = "main"
        remote = "origin"
        status_lines: list[str] = []
        is_git = False
        try:
            res_git = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(target_dir), capture_output=True, text=True, timeout=5
            )
            is_git = (res_git.returncode == 0 and "true" in res_git.stdout.strip())
        except Exception:
            is_git = False

        if is_git:
            try:
                b_res = subprocess.run(
                    ["git", "branch", "--show-current"],
                    cwd=str(target_dir), capture_output=True, text=True, timeout=5
                )
                if b_res.returncode == 0 and b_res.stdout.strip():
                    branch = b_res.stdout.strip()
            except Exception:
                pass
            try:
                r_res = subprocess.run(
                    ["git", "remote"],
                    cwd=str(target_dir), capture_output=True, text=True, timeout=5
                )
                if r_res.returncode == 0 and r_res.stdout.strip():
                    remote = r_res.stdout.strip().splitlines()[0]
            except Exception:
                pass
            try:
                s_res = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(target_dir), capture_output=True, text=True, timeout=5
                )
                if s_res.returncode == 0:
                    status_lines = [ln.strip() for ln in s_res.stdout.strip().splitlines() if ln.strip()]
            except Exception:
                pass

        safety_ok = True
        warnings: list[str] = []
        secret_patterns = (".env", "id_rsa", ".pem", ".key", "credentials", "secrets.")
        for line in status_lines:
            fname = line.split()[-1] if line.split() else ""
            if any(p in fname.lower() for p in secret_patterns):
                safety_ok = False
                warnings.append(f"Forbidden sensitive file detected: '{fname}'")

        try:
            negative_principles = [p for p in self.list_principles() if p.category == "safety" or "forbid" in p.name.lower()]
            for p in negative_principles:
                inv = (p.latent_invariant or "").lower()
                for line in status_lines:
                    fname = (line.split()[-1] if line.split() else "").lower()
                    if fname and fname in inv:
                        safety_ok = False
                        warnings.append(f"Conflicts with negative invariant: {p.name}")
        except Exception:
            pass

        spec = self.action_registry.propose(
            operation="git_push",
            target=f"{remote}/{branch}",
            file_or_element=str(target_dir),
            reason=f"Push branch '{branch}' to '{remote}' safely after verifying repository state and invariants.",
            required_permissions=("git_push", "workspace_write"),
        )

        return {
            "action_id": spec.action_id,
            "repo_dir": str(target_dir),
            "repo_name": target_dir.name,
            "branch": branch,
            "remote": remote,
            "is_git": is_git,
            "status_lines": status_lines,
            "safety_ok": safety_ok,
            "warnings": warnings,
            "spec": spec,
        }

    def execute_git_push(self, action_id: str, repo_dir: str = "", remote: str = "origin", branch: str = "main") -> tuple[bool, str]:
        """Safely execute an authorized git push action on the repository."""
        import subprocess
        from pathlib import Path
        from mimir.actions import ActionStatus

        if self.safety is not None and self.safety.estopped:
            return False, "Cannot execute: Safety controller is latched (E-stop active)."

        spec = self.action_registry.get(action_id)
        if spec is None:
            return False, f"Action '{action_id}' not found in registry."

        try:
            self.action_registry.mark_executing(action_id)
        except Exception:
            pass

        target_dir = repo_dir or spec.file_or_element or str(Path.cwd().resolve())

        try:
            res = subprocess.run(
                ["git", "push", remote, branch],
                cwd=target_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )
            out = (res.stdout + "\n" + res.stderr).strip()
            if res.returncode == 0:
                self.action_registry.complete(action_id)
                self._emit(EventType.ACTION_COMPLETED, "action_executor.git_push", {
                    "action_id": action_id, "output": out[:500]
                })
                return True, out or f"Branch '{branch}' up to date with '{remote}'."
            else:
                self.action_registry.fail(action_id, reason=out[:300])
                self._emit(EventType.ACTION_FAILED, "action_executor.git_push", {
                    "action_id": action_id, "error": out[:500]
                })
                return False, out or "git push returned a non-zero exit code."
        except Exception as exc:
            self.action_registry.fail(action_id, reason=str(exc))
            return False, str(exc)

    def _legacy_reply(self, intent: str, text: str) -> str:
        """Companion wording for a parsed intent (no side effects)."""
        from .presenters import format_memory_summary

        if intent == "stop":
            return "Powering down. Say the word if you need me."
        if intent == "sleep":
            return "Resting. Switch tabs or press Rest / Wake when ready."
        if intent == "status_inquiry":
            principles = self.list_principles()
            if not principles:
                return "Behavioral Memory is currently empty. Observe your workspace or run the showcase to learn your habits."
            count = len(principles)
            bullets = "\n".join(f"• {p.name}" for p in principles[:6])
            extra = f"\n• ...and {count - 6} more personalized habits" if count > 6 else ""
            return (
                f"Here is what I have learned from your coding style ({count} principles):\n"
                f"{bullets}{extra}\n\n"
                f"Tip: Explore individual principles in the Memory tab anytime.\n\n"
                f"[Extended Technical Breakdown]\n"
                f"{format_memory_summary(principles)}"
            )
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
