"""In-app live cobot showcase (Qt): the 4 golden scenes on a QThread.

:func:`demo_live_cobot` proves the golden script from the CLI; this module
brings it into the Home tab. :class:`ShowcaseRunner` is a ``QObject`` that
lives on a background ``QThread`` and drives the visor plus a log view
through signals, so the UI stays responsive while the scenes run.

Scenes mirror the CLI script: silent observation -> voice coordination ->
compliant driving -> E-stop correction. Hardware is simulated (fast,
deterministic, cursor-safe) unless the caller opts into live actuation;
speech uses the app state's voice by default, or the live Edge-TTS voice
when ``speak_live`` is set.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from mimir.actuator import (
    ActuatorError,
    CobotInterruptedError,
    CursorPoint,
    DesktopActuator,
)
from mimir.principles import (
    BehavioralPrinciple,
    ContextTrigger,
    EpistemicStatus,
)
from mimir.ui.visor import VisorState

from .state import AppState
from .tabs._qt import load_qt

__all__ = [
    "SHOWCASE_DRIVE_TEXT",
    "SHOWCASE_ESTOP_QUESTION",
    "SHOWCASE_VOICE_LINE",
    "ShowcaseRunner",
    "ShowcaseScene",
]

#: Canonical spoken line for Scene 2 (voice coordination).
SHOWCASE_VOICE_LINE = (
    "I noticed you write test-backed changes. "
    "Would you like me to handle your current changes?"
)

#: Text typed with human cadence during Scene 3 (compliant driving).
SHOWCASE_DRIVE_TEXT = "test: cover retries with pytest cases"

#: Spoken E-stop acknowledgement for Scene 4.
SHOWCASE_ESTOP_QUESTION = "Stopped. What was about to go wrong?"

#: Scripted user correction registered as an invariant in Scene 4.
_SHOWCASE_CORRECTION = "Never stage .env files."

_, QtCore, _, _ = load_qt()

if hasattr(QtCore, "pyqtSignal"):
    _Signal = QtCore.pyqtSignal
else:  # PySide6
    _Signal = QtCore.Signal


class ShowcaseScene(Enum):
    """The four golden scenes plus the terminal state."""

    OBSERVE = "1_observe"
    VOICE_COORDINATE = "2_voice"
    COMPLIANT_DRIVE = "3_drive"
    ESTOP_CORRECTION = "4_estop"
    COMPLETE = "complete"


class _DryMouse:
    """Recording cursor backend for simulated driving."""

    def __init__(self) -> None:
        self.pos = (400, 400)
        self.moves: list[tuple[int, int]] = []
        self.clicks = 0

    def position(self):  # type: ignore[no-untyped-def]
        return self.pos

    def move_to(self, x: int, y: int) -> None:
        self.moves.append((x, y))
        self.pos = (x, y)

    def click(self) -> None:
        self.clicks += 1


class _TripAfterMouse(_DryMouse):
    """Dry-run mouse that trips the E-stop mid-glide (Scene 4)."""

    def __init__(self, after: int = 3) -> None:
        super().__init__()
        self.trip: Optional[Callable[[], None]] = None
        self._after = after

    def move_to(self, x: int, y: int) -> None:
        super().move_to(x, y)
        if self.trip is not None and len(self.moves) == self._after:
            self.trip()


class _DryKeyboard:
    def __init__(self) -> None:
        self.chars: list[str] = []

    def write_char(self, char: str) -> None:
        self.chars.append(char)


class _DryFailsafe:
    def __init__(self) -> None:
        self.on_trip = None

    def arm(self, on_trip):  # type: ignore[no-untyped-def]
        self.on_trip = on_trip

    def disarm(self) -> None:
        pass


class ShowcaseRunner(QtCore.QObject):
    """Orchestrates the 4 live showcase scenes on a background QThread.

    Signals:
        scene_changed(scene_value, explanation)
        visor_state_requested(state_name)
        log_message(text)
        showcase_completed(success)
    """

    scene_changed = _Signal(str, str)
    visor_state_requested = _Signal(str)
    log_message = _Signal(str)
    showcase_completed = _Signal(bool)

    def __init__(self, state: AppState,
                 repo: Optional[str] = None,
                 speak_live: bool = False,
                 parent: Any = None,
                 safety: Any = None) -> None:
        super().__init__(parent)
        if not isinstance(state, AppState):
            raise ValueError(
                "state: expected AppState, "
                f"got {type(state).__name__}"
            )
        self._state = state
        self._repo = repo or state.prefs.last_repo or str(Path.cwd())
        self._speak_live = bool(speak_live)
        self._live_drive = False
        self._cancelled = False
        # Every scene actuator gates on the shared session controller
        # (plan section 8); an explicit override wins for tests.
        self._safety = (safety if safety is not None
                        else getattr(state, "safety", None))
        self._active: Optional[DesktopActuator] = None

    def cancel(self) -> None:
        """Request an early stop, interrupting a mid-scene drive too."""
        self._cancelled = True
        active, self._active = self._active, None
        if active is not None:
            try:
                active.abort()
            except Exception:
                pass

    def _track(self, actuator: DesktopActuator) -> DesktopActuator:
        """Mark ``actuator`` as the cancellable drive in flight."""
        self._active = actuator
        return actuator

    def _untrack(self, actuator: DesktopActuator) -> None:
        if self._active is actuator:
            self._active = None

    # -- signal helpers -------------------------------------------------
    def _announce(self, scene: ShowcaseScene, explanation: str) -> None:
        self.scene_changed.emit(scene.value, explanation)
        self.log_message.emit(f"[{scene.value}] {explanation}")

    def _visor(self, name: str) -> None:
        try:
            self._state.visor.set_state(VisorState(name))
        except Exception:
            pass
        self.visor_state_requested.emit(name)

    def _speak(self, line: str) -> None:
        if self._speak_live:
            try:
                self._state.voice_coordinator().speak(line)
                return
            except Exception as exc:
                self.log_message.emit(f"[tts unavailable: {exc}]")
        try:
            self._state.voice.speak(line)
        except Exception as exc:
            self.log_message.emit(f"[speech note: {exc}]")

    # -- scenes ----------------------------------------------------------
    def run_scene_1_observe(self) -> None:
        """Scene 1: ingest commits, infer principles into Memory."""
        self._announce(ShowcaseScene.OBSERVE,
                       "Silent observation — ingesting commits.")
        self._visor("observing")
        roots = list(self._state.prefs.active_roots(self._state.default_roots()))
        if self._repo and self._repo not in roots:
            roots.append(self._repo)
        try:
            episodes = self._state.observe(self._repo, roots)
            self.log_message.emit(f"ingested {len(episodes)} commit diff(s)")
            self._visor("thinking")
            principles = self._state.learn()
        except Exception as exc:
            self.log_message.emit(
                f"[observe note: {exc}; seeding demo persona instead]")
            from .personas import seed_persona
            principles = seed_persona(self._state.store, "clean_arch")
        self._visor("resting")
        self.log_message.emit(
            f"confirmed {len(principles)} principle(s) into vector memory")
        for principle in principles:
            self.log_message.emit(f"  - {principle.id}: {principle.name}")

    def run_scene_2_voice(self) -> None:
        """Scene 2: voice coordination over learned habits."""
        self._announce(ShowcaseScene.VOICE_COORDINATE,
                       "Voice coordination — reporting learned habits.")
        self._visor("listening")
        try:
            hits = self._state.search("test-backed changes commit",
                                      top_k=3, min_confidence=0.0)
        except Exception:
            hits = []
        if hits:
            habits = "; ".join(h.latent_invariant for h in hits)
            line = (f"Yes! I noticed: {habits} "
                    "Would you like me to handle your current changes?")
        else:
            line = SHOWCASE_VOICE_LINE
        cobot_name = getattr(self._state.prefs, "assistant_name", None) or "Habi"
        self.log_message.emit(f'{cobot_name}: "{line}"')
        self._speak(line)
        self._visor("resting")

    def _drive_square(self, actuator: DesktopActuator,
                      fast: bool) -> None:
        start = actuator.cursor_position()
        for dx, dy in ((200, 0), (200, 200), (0, 200), (0, 0)):
            actuator.move_cursor_smooth(
                CursorPoint(x=start.x + dx, y=start.y + dy),
                duration_sec=0.01 if fast else 0.4,
                steps=4 if fast else 12,
            )

    def _actuator_kwargs(self) -> dict[str, Any]:
        """Shared runtime attachments for every scene actuator."""
        return {
            "human_override_callback": lambda: self._visor("alert"),
            "event_bus": getattr(self._state, "event_bus", None),
            "safety": self._safety,
        }

    def _dry_actuator(self) -> DesktopActuator:
        return DesktopActuator(
            mouse=_DryMouse(),
            keyboard=_DryKeyboard(),
            failsafe=_DryFailsafe(),
            **self._actuator_kwargs(),
        )

    def run_scene_3_drive(self, simulate_hardware: bool = False) -> None:
        """Scene 3: compliant driving — glide, type, run status.

        Attempts live hardware by default; falls back to a simulated
        drive (with a log line) when the OS backends are unavailable.
        A human E-stop mid-scene is caught and logged so the showcase
        continues into the E-stop drill (Scene 4).
        """
        self._announce(ShowcaseScene.COMPLIANT_DRIVE,
                       "Compliant driving — gliding and typing.")
        self._visor("driving")
        if simulate_hardware:
            actuator = self._track(self._dry_actuator())
            try:
                self._drive_square(actuator, fast=True)
                actuator.type_natural(SHOWCASE_DRIVE_TEXT, base_wpm=240)
            finally:
                self._untrack(actuator)
                actuator.close()
            self.log_message.emit(
                f"[simulated glide + typed {len(SHOWCASE_DRIVE_TEXT)} chars]")
            self._visor("resting")
            return
        actuator = self._track(
            DesktopActuator(**self._actuator_kwargs()))
        try:
            try:
                self._drive_square(actuator, fast=False)
                actuator.type_natural(SHOWCASE_DRIVE_TEXT)
            except ActuatorError as exc:
                self.log_message.emit(
                    f"[live backends unavailable ({exc}); simulating]")
                actuator.close()
                simulated = self._track(self._dry_actuator())
                try:
                    self._drive_square(simulated, fast=True)
                    simulated.type_natural(SHOWCASE_DRIVE_TEXT,
                                           base_wpm=240)
                finally:
                    self._untrack(simulated)
                    simulated.close()
        except CobotInterruptedError:
            self._visor("alert")
            self.log_message.emit(
                "[E-stop tripped during Scene 3; continuing to Scene 4]")
        else:
            try:
                code, out, _ = actuator.run_safe_command(
                    ["git", "-C", self._repo, "status", "--short"])
                self.log_message.emit(
                    f"[git status exit={code}] "
                    f"{out.strip() or '(clean)'}")
            except Exception as exc:
                self.log_message.emit(f"[git status note: {exc}]")
            self._visor("resting")
        finally:
            self._untrack(actuator)
            try:
                actuator.close()
            except Exception:
                pass

    def run_scene_4_estop(self) -> None:
        """Scene 4: E-stop drill — trip, ask, register invariant."""
        self._announce(ShowcaseScene.ESTOP_CORRECTION,
                       "E-stop drill — human override correction.")
        if self._live_drive:
            actuator: DesktopActuator = self._track(
                DesktopActuator(**self._actuator_kwargs()))
            self.log_message.emit(
                "Nudge your mouse or press ESC during the glide to trip "
                "the E-stop (or sit still to skip the drill).")
        else:
            trip_mouse = _TripAfterMouse()
            actuator = self._track(DesktopActuator(
                mouse=trip_mouse,
                keyboard=_DryKeyboard(),
                failsafe=_DryFailsafe(),
                **self._actuator_kwargs(),
            ))
            trip_mouse.trip = actuator.abort
        try:
            self._visor("driving")
            self._drive_square(actuator, fast=not self._live_drive)
        except CobotInterruptedError:
            self._visor("alert")
            cobot_name = getattr(self._state.prefs, "assistant_name", None) or "Habi"
            self.log_message.emit(f'{cobot_name}: "{SHOWCASE_ESTOP_QUESTION}"')
            self._speak(SHOWCASE_ESTOP_QUESTION)
            self._register_correction(_SHOWCASE_CORRECTION)
            # The registered correction is the operator's deliberate
            # reauthorization: clear this drill's latches so the session
            # ends nominal. Pre-trip pending actions stay invalid.
            actuator.reset()
            safety = self._safety
            if safety is not None:
                try:
                    if safety.reset(
                        authorization="showcase-user correction: "
                                      + _SHOWCASE_CORRECTION,
                        actor="showcase-user",
                    ):
                        self.log_message.emit(
                            "[safety: stop cleared by user correction]")
                except Exception as exc:
                    self.log_message.emit(
                        f"[safety: resume refused ({exc})]")
        else:
            self.log_message.emit(
                "[no trip: glide completed without interruption]")
        finally:
            self._untrack(actuator)
            try:
                actuator.close()
            except Exception:
                pass
        self._visor("resting")

    def _register_correction(self, correction: str) -> None:
        principle = BehavioralPrinciple(
            id="bp_showcase_forbid_env_001",
            name="Forbid staging secret files",
            category="workflow",
            context_trigger=ContextTrigger(
                scope="Git staging", language_agnostic=True),
            latent_invariant="Never stage .env files or other secret files.",
            anti_patterns=(
                "Running git add -A without checking status",
                "Staging .env files",
            ),
            epistemic_status=EpistemicStatus.new(0.90, supporting=1),
            inspectable_traces=("showcase_scene_4_correction",),
        )
        self._state.store.save(principle)
        line = (f"Understood. Invariant registered from '{correction}': "
                "forbid staging .env. It is now in your Behavioral Memory.")
        cobot_name = getattr(self._state.prefs, "assistant_name", None) or "Habi"
        self.log_message.emit(f'{cobot_name}: "{line}"')
        self._speak(line)

    def run_all(self, simulate_hardware: bool = True) -> bool:
        """Run scenes 1-4 in order; emits ``showcase_completed``.

        Simulated hardware (cursor-safe, deterministic) is the default
        so the 1-click Home button never surprises; pass
        ``simulate_hardware=False`` for the full live actuation.
        """
        self._live_drive = not simulate_hardware
        try:
            for step in (self.run_scene_1_observe,
                         self.run_scene_2_voice,
                         lambda: self.run_scene_3_drive(
                             simulate_hardware=simulate_hardware),
                         self.run_scene_4_estop):
                if self._cancelled:
                    self.log_message.emit("Showcase cancelled.")
                    self._visor("resting")
                    self.showcase_completed.emit(False)
                    return False
                step()
        except Exception as exc:
            self.log_message.emit(f"Showcase failed: {exc}")
            self._visor("resting")
            self.showcase_completed.emit(False)
            return False
        self.scene_changed.emit(ShowcaseScene.COMPLETE.value,
                                "Showcase complete.")
        self.log_message.emit("Showcase complete.")
        self.showcase_completed.emit(True)
        return True
