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

__all__ = ["AUTO_LLM", "AppState", "DEFAULT_STORE_PATH", "get_llm_fn"]

DEFAULT_STORE_PATH = "mimir_memory.db"

class _AutoLLM:
    """Sentinel: resolve the learn-time LLM from user prefs (Phase 0)."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "AUTO_LLM"


#: Default ``llm_fn`` for :meth:`AppState.learn` — resolve via
#: :func:`get_llm_fn` from the active prefs (offline prefs yield None,
#: i.e. the deterministic :class:`mimir.detective.FallbackOfflineStub`).
AUTO_LLM = _AutoLLM()

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

    Phase 2 delegates to the standardized :class:`desktop_app.llm.LLMProvider`
    (Ollama/Llama-3.2 default, cloud bridges optional). Returns None
    when the provider is offline (or unknown), or when a cloud provider
    is selected without an API key — the caller then falls back to the
    :class:`mimir.detective.FallbackOfflineStub` scaffold.
    """
    from .llm import build_llm_client

    try:
        provider = getattr(prefs, "model_provider", "offline") or "offline"
        model = getattr(prefs, "model_name", "") or ""
        endpoint = getattr(prefs, "model_endpoint", "") or ""
        api_key = getattr(prefs, "api_key", "") or ""
    except Exception:
        return None
    try:
        return build_llm_client(provider, model=model, endpoint=endpoint,
                                api_key=api_key)
    except Exception:
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
    _chunker: Any = field(default=None, repr=False)
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
            snapshot = dict(self._sensory.status())
        except Exception:
            return None
        try:
            snapshot["chunk_pending"] = (
                int(self._chunker.pending_count)
                if self._chunker is not None else 0
            )
        except Exception:
            snapshot["chunk_pending"] = 0
        return snapshot

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

        Phase 1 ambient pipeline: the daemon runs allowlist-only behind a
        :class:`~mimir.privacy.PrivacyGate` (forbidden secret files are
        never published, payloads are redacted pre-publish), and file
        saves are grouped into episodes by an
        :class:`~mimir.chunking.EpisodicChunkingEngine`.
        """
        self.stop_senses()
        try:
            from pathlib import Path
            from mimir.ast_delta import ast_delta_to_episode
            from mimir.chunking import EpisodicChunkingEngine
            from mimir.events import Event, bind_visor_to_bus, get_event_bus
            from mimir.privacy import PrivacyGate
            from mimir.sensory import SensoryService
        except ImportError:
            return False

        effective_roots = list(roots or [])
        if not effective_roots:
            effective_roots = list(self.default_roots())
        target_repo = repo or self.prefs.last_repo or ""
        if target_repo and target_repo not in effective_roots:
            effective_roots.append(target_repo)
        try:
            gate = PrivacyGate(effective_roots)
        except ValueError:
            return False
        live = [r for r in effective_roots
                if Path(r).is_dir() and gate.is_within_allowlist(r)]
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
                privacy_gate=gate,
            )
            service.start()
        except Exception:
            return False
        self._sensory = service
        self._chunker = EpisodicChunkingEngine(
            repo=target_repo or "local", privacy_gate=gate)

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

        # Group file-save micro-actions into burst episodes (Phase 1).
        chunker = self._chunker

        def _on_save_burst(event: Event) -> None:
            try:
                episode = chunker.observe_event(event)
            except Exception:
                episode = None
            if episode is not None:
                seen = {e.id for e in self.episodes}
                if episode.id not in seen:
                    self.episodes.insert(0, episode)

        try:
            tokens.append(
                bus.subscribe(
                    EventType.FILE_CREATED,
                    EventType.FILE_MODIFIED,
                    EventType.FILE_DELETED,
                    _on_save_burst,
                    name="state:chunk_saves",
                )
            )
        except Exception:
            pass

        self._sensory_tokens = tokens
        return True

    def flush_episode_chunk(self) -> Optional[Any]:
        """Close the open save-burst as one episode (None when empty).

        The chunker buffers file-save micro-actions while senses run;
        flushing groups them into a single ``BehavioralEpisode`` and
        prepends it to the episode cache for Detective inference.
        """
        chunker = self._chunker
        if chunker is None:
            return None
        try:
            episode = chunker.flush()
        except Exception:
            return None
        if episode is None:
            return None
        seen = {e.id for e in self.episodes}
        if episode.id not in seen:
            self.episodes.insert(0, episode)
        return episode

    def stop_senses(self) -> None:
        """Stop background senses daemon and unbind tokens (idempotent)."""
        service, self._sensory = self._sensory, None
        if service is not None:
            try:
                service.stop()
            except Exception:
                pass
        chunker, self._chunker = self._chunker, None
        if chunker is not None:
            try:
                chunker.reset()
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
        llm_fn: Any = AUTO_LLM,
    ) -> list[BehavioralPrinciple]:
        """Cluster episodes, infer principles, persist them to the store.

        ``llm_fn`` defaults to :data:`AUTO_LLM`, which resolves the
        active provider from prefs (local Ollama / Llama 3.2 when the
        startup probe enabled it, else the
        :class:`~mimir.detective.FallbackOfflineStub`). Pass an
        explicit callable to force live inference, or None to force the
        offline stub. Auto-resolved LLM failures (daemon stopped, bad
        output) fail closed to the offline stub; an explicit
        callable's errors propagate to the caller.
        """
        self.visor.set_state(VisorState.THINKING)
        try:
            used = (list(episodes) if episodes is not None
                    else list(self.episodes))
            auto = llm_fn is AUTO_LLM
            fn = get_llm_fn(self.prefs) if auto else llm_fn
            try:
                principles = infer_principles(
                    used, min_cluster_size=min_cluster_size, llm_fn=fn)
            except Exception:
                if not auto or fn is None:
                    raise
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

    def get_principle(self, principle_id: str) -> BehavioralPrinciple:
        """Fetch one principle by id (Review & Calibration Deck).

        Raises :class:`mimir.store.PrincipleNotFoundError` when absent.
        """
        return self.store.get(principle_id)

    def set_confidence(self, principle_id: str, value: float,
                       evidence_id: str) -> BehavioralPrinciple:
        """Set absolute confidence (deck slider) and return the refresh.

        ``value`` must lie within [0, 1]; the evidence id appends to the
        principle's inspectable traces. Unlike :meth:`adjust_confidence`
        (Bayesian evidence), this is a direct human calibration: the
        supporting/contradiction counts stay untouched.
        """
        self.store.set_confidence(principle_id, value, evidence_id)
        return self.store.get(principle_id)

    def update_principle(
        self,
        principle_id: str,
        *,
        name: Optional[str] = None,
        category: Optional[str] = None,
        scope: Optional[str] = None,
        language_agnostic: Optional[bool] = None,
        latent_invariant: Optional[str] = None,
        anti_patterns: Optional[Sequence[str]] = None,
    ) -> BehavioralPrinciple:
        """Edit a principle's human-readable fields (deck editor).

        Only the provided fields change; the id, epistemic status, and
        inspectable traces (episode linkage) are preserved. Saving
        re-embeds the principle so retrieval follows the new wording.
        Raises :class:`mimir.store.PrincipleNotFoundError` when absent
        and :class:`ValueError` when a new value violates the contract.
        """
        from mimir.principles import BehavioralPrinciple, ContextTrigger

        current = self.store.get(principle_id)
        trigger = current.context_trigger
        if scope is not None or language_agnostic is not None:
            trigger = ContextTrigger(
                scope=current.context_trigger.scope if scope is None else scope,
                language_agnostic=(
                    current.context_trigger.language_agnostic
                    if language_agnostic is None else language_agnostic
                ),
            )
        updated = BehavioralPrinciple(
            id=current.id,
            name=current.name if name is None else name,
            category=current.category if category is None else category,
            context_trigger=trigger,
            latent_invariant=(current.latent_invariant
                              if latent_invariant is None
                              else latent_invariant),
            anti_patterns=(current.anti_patterns
                           if anti_patterns is None else anti_patterns),
            epistemic_status=current.epistemic_status,
            inspectable_traces=current.inspectable_traces,
        )
        self.store.save(updated)
        return updated

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
        shared safety controller. Phase 5 text parity: ``plan_fn`` /
        ``propose_fn`` default to :meth:`plan_task` / :meth:`propose_plan`
        (the same planner + registry a typed Replicate proposal uses)
        and ``forbid_fn`` defaults to
        :meth:`register_negative_invariant`, so voice intents flow
        through the identical auth/planning pipeline as text. Pass
        explicit ``plan_fn=None`` (and ``propose_fn=None`` /
        ``forbid_fn=None``) to restore the legacy reply-only behavior.
        Extra keyword arguments pass through to
        :class:`VoiceActionService` (``speak_fn``, ``speak_async``,
        ``devstate``, ...).
        """
        assert self.action_registry is not None  # built in __post_init__
        # Note: kwargs.setdefault cannot express "default, but None
        # disables" for callers that pass plan_fn=None explicitly to
        # opt out — setdefault keeps the explicit None, which the
        # service treats as unbound. Planner callbacks run on the
        # caller's thread and fail closed to the spoken reply.
        kwargs.setdefault("plan_fn", self.plan_task)
        kwargs.setdefault("propose_fn", self.propose_plan)
        kwargs.setdefault("forbid_fn", self.register_negative_invariant)
        return VoiceActionService(
            coordinator if coordinator is not None else self.voice,
            self.action_registry,
            bus=self.event_bus,
            session_id=self.session_id,
            safety=self.safety,
            reply_fn=self._service_reply,
            **kwargs,
        )

    def handle_voice_transcript(
        self,
        text: str,
        coordinator: Optional[VoiceCoordinator] = None,
        correlation_id: str = "",
        now: Optional[float] = None,
        **service_kwargs: Any,
    ) -> Any:
        """Handle one voice transcript through the text auth pipeline.

        Builds :meth:`voice_service` (shared registry, shared safety,
        same planner as typed Replicate proposals) and handles
        ``text`` end to end: parse → resolve → authorize/propose →
        publish → speak. Returns the :class:`VoiceDecision` (reply is
        the TTS feedback; ``proposed_action_id`` names the new
        proposal for ``PUSH_CODE``). Planner failures fail closed to
        the legacy spoken reply — this never raises for backend
        reasons (only for invalid ``text``).
        """
        service = self.voice_service(
            coordinator if coordinator is not None else self.voice,
            **service_kwargs,
        )
        return service.handle_transcript(
            text, correlation_id=correlation_id, now=now,
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
            risk="high",
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

        # Zero execution without approval (Phase 3): a push runs only
        # from AUTHORIZED (fresh approval) or EXECUTING (retry after a
        # worker restart). Anything else — pending, expired, denied —
        # refuses before any subprocess is spawned.
        if spec.status is ActionStatus.AUTHORIZED:
            try:
                started = self.action_registry.mark_executing(action_id)
            except Exception:
                started = False
            if not started:
                return False, (
                    "Cannot execute: action was invalidated by a "
                    "safety stop."
                )
        elif spec.status is not ActionStatus.EXECUTING:
            return False, (
                f"Cannot execute: action '{action_id}' is not "
                f"authorized (status={spec.status.value}). Approve it "
                "first."
            )

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

    # -- Phase 3: typed planning + graduated autonomy -------------------
    def plan_task(self, task: str, **kwargs: Any) -> Any:
        """Draft a typed, risk-scored ActionPlan for ``task`` (no effects).

        Retrieves the user's relevant principles from the store and
        scores the plan LOW / MEDIUM / HIGH. Extra keyword arguments
        pass through to :meth:`mimir.planning.ActionPlanner.plan`
        (``operation``, ``target``, ``file_or_element``,
        ``target_scope``, ``min_confidence``, ``top_k``, ...).
        """
        from mimir.planning import ActionPlanner

        try:
            roots = list(self.prefs.last_roots) or self.default_roots()
        except Exception:
            roots = []
        planner = ActionPlanner(self.store, allowed_roots=roots)
        return planner.plan(task, **kwargs)

    def propose_plan(self, plan: Any, **kwargs: Any) -> Any:
        """Record an ActionPlan in the shared registry (PENDING).

        Emits ``ACTION_PROPOSED`` with the plan's risk tier, commands,
        and matched principles. Extra keyword arguments pass through
        to the registry (``ttl_s``, ``action_id``, ...).
        """
        from mimir.planning import ActionPlanner

        assert self.action_registry is not None  # built in __post_init__
        kwargs.setdefault("session_id", self.session_id)
        return ActionPlanner.propose(plan, self.action_registry, **kwargs)

    def authorize_proposal(
        self,
        action_id: str,
        *,
        checklist_ack: bool = False,
        authorization_type: str = "explicit_gui_confirmation",
    ) -> Any:
        """Authorize one proposal via the GUI gesture (graduated).

        LOW plans authorize with a single click; MEDIUM/HIGH plans
        additionally require ``checklist_ack`` (every safety-checklist
        box confirmed on the proposal card). The GUI grants the
        risk-appropriate permission; voice approvals keep flowing
        through the registry with voice-only grants and stay confined
        to low-risk work.
        """
        from mimir.actions import AuthorizationOutcome
        from mimir.planning import GUI_GRANTED_PERMISSIONS

        assert self.action_registry is not None  # built in __post_init__
        spec = self.action_registry.get(action_id)
        if spec is None:
            return AuthorizationOutcome(
                False, f"unknown action {action_id!r}",
                action_id=action_id,
            )
        risk = (getattr(spec, "risk", "low") or "low").strip().lower()
        if risk in ("medium", "high") and not checklist_ack:
            return AuthorizationOutcome(
                False,
                f"action {spec.action_id!r} needs safety-checklist "
                f"confirmation first (risk={risk})",
                action_id=spec.action_id,
                status=spec.status,
            )
        return self.action_registry.authorize(
            action_id,
            authorization_type=authorization_type,
            granted_permissions=GUI_GRANTED_PERMISSIONS,
        )

    def reject_proposal(
        self, action_id: str, reason: str = "rejected by user"
    ) -> bool:
        """Reject one proposal (PENDING -> CANCELLED). Never raises."""
        assert self.action_registry is not None  # built in __post_init__
        try:
            return self.action_registry.cancel(action_id, reason=reason)
        except Exception:
            return False

    def authorize_and_execute(
        self,
        action_id: str,
        *,
        checklist_ack: bool = False,
        executor: Any = None,
    ) -> tuple[bool, str]:
        """Authorize one proposal and execute it (fail-closed).

        Zero execution without approval: PENDING medium/high plans
        refuse unless ``checklist_ack`` confirms the safety checklist,
        and nothing runs while the safety E-stop is latched or the
        entry is not AUTHORIZED. ``executor`` (``spec ->
        (ok, message)``) performs the side effects; without one,
        read-only low-risk plans complete immediately while anything
        with commands or file effects stays AUTHORIZED with a
        "no executor bound" note so a linked runner (drive dialog,
        git push) can carry it out on the same registry entry.
        """
        from mimir.actions import ActionStatus

        assert self.action_registry is not None  # built in __post_init__
        try:
            spec = self.action_registry.get(action_id)
        except Exception as exc:
            return (False, str(exc))
        if spec is None:
            return (False, f"Unknown action {action_id!r}.")
        if self.safety is not None:
            try:
                latched = self.safety.estopped
            except Exception:
                latched = False
            if latched:
                return (False, "Safety stop is active — reauthorize "
                               "before executing.")
        if spec.status is ActionStatus.AUTHORIZED:
            pass  # approved earlier (e.g. proposal card, then runner)
        elif spec.status is ActionStatus.PENDING:
            outcome = self.authorize_proposal(
                action_id, checklist_ack=checklist_ack)
            if not outcome.ok:
                return (False, outcome.reason)
            spec = self.action_registry.get(action_id)
            assert spec is not None  # just authorized above
        else:
            return (False, f"Action '{action_id}' is already "
                           f"{spec.status.value}; nothing to execute.")
        self._emit(EventType.ACTION_REQUESTED, "cognitive.action_planner",
                   spec.to_dict())
        risk = (getattr(spec, "risk", "low") or "low").strip().lower()
        if executor is None:
            if (risk == "low" and not getattr(spec, "commands", ())
                    and not (getattr(spec, "diff_preview", "") or "")):
                try:
                    self.action_registry.mark_executing(action_id)
                except Exception:
                    pass
                self.action_registry.complete(action_id)
                return (True, "Authorized and completed (read-only — "
                              "no side effects).")
            return (True, f"Authorized {action_id} ({risk} risk); "
                          "execution deferred — no executor bound for "
                          f"{spec.operation}.")
        try:
            started = self.action_registry.mark_executing(action_id)
        except Exception:
            started = False
        if not started:
            return (False, "Could not start execution: the action was "
                           "invalidated by a safety stop.")
        try:
            result = executor(spec)
            ok = bool(result[0])
            message = str(result[1]) if len(result) > 1 else ""
        except Exception as exc:
            self.action_registry.fail(action_id, reason=str(exc))
            return (False, f"Execution failed: {exc}")
        if ok:
            self.action_registry.complete(action_id)
            return (True, message or "Executed successfully.")
        self.action_registry.fail(action_id, reason=message)
        return (False, message or "Execution failed.")

    def execute_action_plan(self, spec: Any) -> tuple[bool, str]:
        """Execute the side effects of an authorized ActionSpec.

        Handles:
        1. Executing shell commands in the project workspace (e.g. pytest, npm test).
        2. Applying code changes or new files into the project workspace safely.
        3. Git workflow actions (git status, commit, push).
        """
        from .presenters import extract_code_block

        if self.safety is not None:
            try:
                latched = self.safety.estopped
            except Exception:
                latched = False
            if latched:
                return (False, "Safety stop is active — execution blocked.")

        op = getattr(spec, "operation", "") or ""
        commands = list(getattr(spec, "commands", ()) or ())
        target_file = (
            getattr(spec, "file_or_element", "") or getattr(spec, "target", "")
        ).strip()
        diff_or_code = getattr(spec, "diff_preview", "") or ""

        # Determine working directory
        cwd = Path.cwd().resolve()
        if self.prefs.last_repo:
            try:
                repo_path = Path(self.prefs.last_repo).resolve()
                if repo_path.is_dir():
                    cwd = repo_path
            except Exception:
                pass

        results: list[str] = []

        # 1. File write/edit if target_file and code payload are present
        if target_file and diff_or_code and not diff_or_code.startswith("@@ planned change"):
            try:
                dest = Path(target_file)
                if not dest.is_absolute():
                    dest = (cwd / dest).resolve()

                # Verify within allowed roots if configured
                allowed = [
                    Path(r).resolve()
                    for r in (self.prefs.last_roots or self.default_roots())
                ]
                is_allowed = not allowed or any(
                    dest == root or root in dest.parents for root in allowed
                )
                if not is_allowed:
                    return (
                        False,
                        f"Target path '{dest}' is outside allowed workspace roots.",
                    )

                code_to_write = extract_code_block(diff_or_code)
                if code_to_write:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(code_to_write, encoding="utf-8")
                    line_count = len(code_to_write.splitlines())
                    results.append(
                        f"Wrote {line_count} line(s) to '{dest.name}'"
                    )
            except Exception as exc:
                return (False, f"Failed writing to '{target_file}': {exc}")

        # 2. Run commands if specified
        if commands:
            for cmd in commands:
                try:
                    import subprocess

                    proc = subprocess.run(
                        cmd,
                        cwd=str(cwd),
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    out = (
                        proc.stdout
                        + ("\n" + proc.stderr if proc.stderr else "")
                    ).strip()
                    if proc.returncode != 0:
                        return (
                            False,
                            f"Command '{cmd}' failed (exit {proc.returncode}):\n{out or 'No output'}",
                        )
                    first_line = out.splitlines()[0] if out else "OK"
                    results.append(f"$ {cmd} -> {first_line}")
                except subprocess.TimeoutExpired:
                    return (False, f"Command '{cmd}' timed out after 120s.")
                except Exception as exc:
                    return (False, f"Command '{cmd}' failed: {exc}")

        # 3. Handle git_push if no explicit commands were set
        if op in ("git_push", "push") and not commands:
            try:
                import subprocess

                subprocess.run(
                    "git status --porcelain",
                    cwd=str(cwd),
                    shell=True,
                    check=True,
                    capture_output=True,
                )
                push_res = subprocess.run(
                    "git push",
                    cwd=str(cwd),
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if push_res.returncode != 0:
                    return (
                        False,
                        f"git push failed: {push_res.stderr or push_res.stdout}",
                    )
                results.append("Git push completed successfully.")
            except Exception as exc:
                return (False, f"Git push failed: {exc}")

        if not results:
            results.append(
                f"Completed {op or 'action'} successfully (no file or command side effects)."
            )

        summary = " | ".join(results)
        self._emit(
            EventType.ACTION_COMPLETED,
            "desktop_app.state",
            {
                "action_id": getattr(spec, "action_id", ""),
                "operation": op,
                "summary": summary,
            },
        )
        return (True, summary)

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
