"""Shared desktop state: one store, visor, voice, and episode cache.

Qt-free so headless tests and the CLI can drive the same core-logic calls
the tabs use. Hardware backends stay lazy: constructing :class:`AppState`
never touches the microphone, speakers, cursor, or Qt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from mimir.actor import ReplicationActor, ReplicationRequest, ReplicationResult
from mimir.detective import infer_principles
from mimir.episodes import BehavioralEpisode
from mimir.principles import BehavioralPrinciple
from mimir.store import PrincipleStore
from mimir.telemetry import observe_git_repo, redact_text
from mimir.ui.visor import VisorController, VisorState
from mimir.voice import VoiceCoordinator

from .prefs import DesktopPrefs

__all__ = ["AppState", "DEFAULT_STORE_PATH"]

DEFAULT_STORE_PATH = "mimir_memory.db"


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
        return fresh

    def set_watching(self, watching: bool) -> None:
        self.watching = watching
        self.visor.set_state(
            VisorState.OBSERVING if watching else VisorState.RESTING)

    def try_listen(self, timeout_sec: float = 5.0,
                   coordinator: VoiceCoordinator | None = None
                   ) -> tuple[bool, str]:
        """Attempt one microphone capture; (ok, transcript-or-reason)."""
        voice = coordinator if coordinator is not None else VoiceCoordinator()
        try:
            heard = voice.listen_phrase(timeout_sec=timeout_sec)
        except Exception as exc:  # missing mic/whisper stays in-app
            return (False, str(exc))
        if not heard:
            return (False, "No speech heard — try again.")
        return (True, heard)

    # -- Replicate tab (actor, offline scaffold by default) ---------------
    def replicate(
        self,
        task: str,
        target_language: Optional[str] = None,
        target_scope: Optional[str] = None,
        min_confidence: float = 0.0,
        top_k: int = 5,
        llm_fn: Any = None,
    ) -> ReplicationResult:
        actor = ReplicationActor(self.store, llm_fn=llm_fn)
        self.visor.set_state(VisorState.THINKING)
        try:
            return actor.replicate(
                ReplicationRequest(
                    task_description=task,
                    target_language=target_language or None,
                    target_scope=target_scope or None,
                    min_confidence=min_confidence,
                    top_k_principles=top_k,
                ),
                generate_contrast_baseline=llm_fn is not None,
            )
        finally:
            self.visor.set_state(VisorState.RESTING)

    # -- Act & Voice tab ---------------------------------------------------
    def parse_utterance(self, text: str) -> dict[str, str]:
        return self.voice.parse_intent(text)

    def respond(self, intent: str, text: str) -> str:
        """Companion reply for a parsed intent (mirrors the CLI cobot)."""
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
