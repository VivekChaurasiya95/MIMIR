"""Day 10 grand live showcase: scripted end-to-end cobot demo.

Golden Script: observe -> infer -> voice coordinate -> compliant driving
-> E-stop correction. The cognitive core (telemetry, detective, store)
always runs for real. Hardware is simulated unless ``--live`` is passed
(requires pyautogui/pynput/microphone/speakers plus a typed GO
confirmation). Live git stays read-only unless ``--allow-write`` is
passed, and even then only the demo's own status/push commands run.

Usage:
    python demo_live_cobot.py [--repo .] [--store demo_memory.db]
    python demo_live_cobot.py --live [--allow-write]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional

from mimir.actions import VOICE_GRANTED_PERMISSIONS, ActionRegistry
from mimir.actuator import (
    ActuatorError,
    CobotInterruptedError,
    CursorPoint,
    DesktopActuator,
)
from mimir.detective import infer_principles
from mimir.principles import (
    BehavioralPrinciple,
    ContextTrigger,
    EpistemicStatus,
)
from mimir.safety import SafetyController
from mimir.store import PrincipleStore
from mimir.telemetry import TelemetryError, observe_git_repo
from mimir.ui.visor import VisorController, VisorState
from mimir.voice import VoiceCoordinator, VoiceError
from mimir.voice_action import parse_structured_intent

USER_QUESTION = "Hey MIMIR, have you learned how I commit and push my code?"
USER_APPROVAL = "Go ahead."
USER_CORRECTION = "Never stage .env files."


class DryMouse:
    """Recording cursor backend for simulated driving."""

    def __init__(self) -> None:
        self.pos = (400, 400)
        self.moves: list[tuple[int, int]] = []
        self.clicks = 0

    def position(self):
        return self.pos

    def move_to(self, x, y):
        self.moves.append((x, y))
        self.pos = (x, y)

    def click(self):
        self.clicks += 1


class TripAfterMouse(DryMouse):
    """Dry-run mouse that trips the E-stop mid-glide (Scene 4)."""

    def __init__(self, after: int = 3) -> None:
        super().__init__()
        self.trip: Optional[Callable[[], None]] = None
        self._after = after

    def move_to(self, x, y):
        super().move_to(x, y)
        if self.trip is not None and len(self.moves) == self._after:
            self.trip()


class DryKeyboard:
    def __init__(self) -> None:
        self.chars: list[str] = []

    def write_char(self, char):
        self.chars.append(char)


class DryFailsafe:
    def __init__(self) -> None:
        self.on_trip = None

    def arm(self, on_trip):
        self.on_trip = on_trip

    def disarm(self):
        pass


@dataclass
class DemoContext:
    live: bool
    allow_write: bool
    store: PrincipleStore
    voice: VoiceCoordinator
    visor: VisorController
    actuator: DesktopActuator
    log: list[str] = field(default_factory=list)
    # One safety latch + one pending-action registry per demo run,
    # shared by every scene's actuator (plan sections 4 + 8).
    safety: SafetyController = field(default_factory=SafetyController)
    registry: Optional[ActionRegistry] = None

    def __post_init__(self) -> None:
        if self.registry is None:
            self.registry = ActionRegistry(safety=self.safety)

    def say(self, text: str) -> None:
        line = f"MIMIR: {text}"
        print(line, flush=True)
        self.log.append(line)
        if self.live:
            self.voice.speak(text)

    def hear_user(self, scripted: str) -> str:
        if not self.live:
            print(f"USER (scripted): {scripted}", flush=True)
            return scripted
        print("Listening... (speak now, or type and press Enter)", flush=True)
        try:
            heard = self.voice.listen_phrase(timeout_sec=15.0)
        except VoiceError as exc:
            print(f"[mic unavailable ({exc}); using typed input]")
            heard = None
        if heard:
            print(f"USER: {heard}", flush=True)
            return heard
        typed = input("USER (type instead): ").strip()
        return typed or scripted


def _seed_principles() -> list[BehavioralPrinciple]:
    """Clearly labeled fallback when the repo yields no inference."""

    def seed(id, name, scope, invariant, anti, confidence):
        return BehavioralPrinciple(
            id=id,
            name=f"[demo seed] {name}",
            category="workflow",
            context_trigger=ContextTrigger(
                scope=scope, language_agnostic=True
            ),
            latent_invariant=invariant,
            anti_patterns=list(anti),
            epistemic_status=EpistemicStatus(
                confidence_score=confidence,
                supporting_episodes_count=2,
                contradictions_count=0,
                first_inferred="2026-10-27T08:00:00Z",
                last_validated="2026-10-27T08:00:00Z",
            ),
            inspectable_traces=["demo_seed"],
        )

    return [
        seed(
            "bp_demo_commits_001",
            "Conventional commits",
            "Git commit workflow",
            "Write conventional commit subjects (type: description).",
            ["Committing with vague one-word subjects"],
            0.85,
        ),
        seed(
            "bp_demo_tests_002",
            "Test-first pushing",
            "Python changes",
            "Run the test suite before committing.",
            ["Pushing without a green suite"],
            0.80,
        ),
    ]


def scene_observe(ctx: DemoContext, repo: str) -> None:
    print("\n=== Scene 1: Silent Observation ===", flush=True)
    ctx.visor.set_state(VisorState.OBSERVING)
    episodes = observe_git_repo(repo, allowed_roots=[repo], max_commits=10)
    print(f"ingested {len(episodes)} commit diff(s)", flush=True)
    ctx.visor.set_state(VisorState.THINKING)
    principles = infer_principles(episodes)
    if not principles:
        print("[no clusters inferred; using labeled demo seeds]", flush=True)
        principles = _seed_principles()
    for principle in principles:
        ctx.store.save(principle)
    print(
        f"confirmed {len(principles)} principle(s) into vector memory:",
        flush=True,
    )
    for principle in principles:
        print(f"  - {principle.id}: {principle.name}", flush=True)


def scene_coordinate(ctx: DemoContext) -> None:
    print("\n=== Scene 2: Voice Coordinate ===", flush=True)
    ctx.visor.set_state(VisorState.LISTENING)
    question = ctx.hear_user(USER_QUESTION)
    intent = ctx.voice.parse_intent(question)["intent"]
    print(f"[intent: {intent}]", flush=True)
    ctx.visor.set_state(VisorState.THINKING)
    hits = ctx.store.query_relevant(question, top_k=3, min_confidence=0.0)
    if hits:
        habits = "; ".join(h.latent_invariant for h in hits)
        ctx.say(
            f"Yes! I noticed: {habits} "
            "Would you like me to handle your current changes?"
        )
    else:
        ctx.say("I have not learned any habits yet.")
    ctx.visor.set_state(VisorState.LISTENING)


def _drive_square(ctx: DemoContext) -> None:
    start = ctx.actuator.cursor_position()
    corners = [(200, 0), (200, 200), (0, 200), (0, 0)]
    for dx, dy in corners:
        ctx.actuator.move_cursor_smooth(
            CursorPoint(x=start.x + dx, y=start.y + dy),
            duration_sec=0.4 if ctx.live else 0.01,
            steps=12,
        )


def scene_drive(ctx: DemoContext, repo: str) -> None:
    print("\n=== Scene 3: Compliant Driving ===", flush=True)
    assert ctx.registry is not None  # built in DemoContext.__post_init__
    spec = ctx.registry.propose(
        "demo_drive",
        target=repo,
        reason="scripted golden-script drive (cursor square + git status)",
        supporting_observations=("scene_observe", "scene_coordinate"),
    )
    print(f"[proposed {spec.action_id}: demo_drive on {repo}]", flush=True)
    approval = ctx.hear_user(USER_APPROVAL)
    print(f"[intent: {ctx.voice.parse_intent(approval)['intent']}]", flush=True)
    structured = parse_structured_intent(approval, ctx.voice, ctx.registry)
    if (structured.needs_clarification
            or not structured.target_action_id):
        print(f"[confirmation unclear ({structured.clarification}); "
              "drive skipped]", flush=True)
        ctx.registry.cancel(spec.action_id, reason="unclear confirmation")
        return
    outcome = ctx.registry.authorize(
        structured.target_action_id,
        authorization_type=(
            structured.authorization_type or "explicit_voice_confirmation"
        ),
        granted_permissions=VOICE_GRANTED_PERMISSIONS,
    )
    if not outcome.ok:
        print(f"[not authorized ({outcome.reason}); drive skipped]",
              flush=True)
        return
    print(f"[{outcome.reason}]", flush=True)
    ctx.registry.mark_executing(spec.action_id)
    ctx.visor.set_state(VisorState.DRIVING)
    try:
        _drive_square(ctx)
        print("[cursor glide complete]", flush=True)
        code, out, _ = ctx.actuator.run_safe_command(
            ["git", "-C", repo, "status", "--short"]
        )
        print(f"[git status exit={code}]\n{out.strip() or '(clean)'}",
              flush=True)
        if ctx.live and ctx.allow_write:
            push_code, push_out, push_err = ctx.actuator.run_safe_command(
                ["git", "-C", repo, "push"]
            )
            print(
                f"[git push exit={push_code}] "
                f"{push_out.strip()} {push_err.strip()}",
                flush=True,
            )
        else:
            print("[would run: git push (pass --allow-write to execute)]",
                  flush=True)
    except CobotInterruptedError:
        ctx.registry.fail(spec.action_id, reason="E-stop during demo drive")
        raise
    except ActuatorError as exc:
        ctx.registry.fail(spec.action_id, reason=str(exc))
        raise
    ctx.registry.complete(spec.action_id)
    print(f"[{spec.action_id}: completed]", flush=True)


def _record_correction(ctx: DemoContext, correction: str) -> None:
    principle = BehavioralPrinciple(
        id="bp_user_forbid_env_001",
        name="Forbid staging secret files",
        category="workflow",
        context_trigger=ContextTrigger(
            scope="Git staging", language_agnostic=True
        ),
        latent_invariant="Never stage .env files or other secret files.",
        anti_patterns=[
            "Running git add -A without checking status",
            "Staging .env files",
        ],
        epistemic_status=EpistemicStatus(
            confidence_score=0.90,
            supporting_episodes_count=1,
            contradictions_count=0,
            first_inferred="2026-10-27T09:00:00Z",
            last_validated="2026-10-27T09:00:00Z",
        ),
        inspectable_traces=["demo_scene_4_correction"],
    )
    ctx.store.save(principle)
    ctx.say(
        f"Understood. Invariant registered from '{correction}': "
        "forbid staging .env. It is now in your Behavioral Memory."
    )


def scene_estop_drill(ctx: DemoContext) -> None:
    print("\n=== Scene 4: E-Stop Live Correction ===", flush=True)
    if ctx.live:
        print(
            "Nudge your mouse or press ESC during the glide to trip the "
            "E-stop (or sit still to skip the drill).",
            flush=True,
        )
    trip_mouse = None if ctx.live else TripAfterMouse()
    tripping = DesktopActuator(
        human_override_callback=lambda: ctx.visor.set_state(VisorState.ALERT),
        mouse=trip_mouse,
        keyboard=None if ctx.live else DryKeyboard(),
        failsafe=None,
        safety=ctx.safety,
    )
    if trip_mouse is not None:
        trip_mouse.trip = tripping.abort
    previous, ctx.actuator = ctx.actuator, tripping
    try:
        _drive_square(ctx)
    except CobotInterruptedError:
        ctx.visor.set_state(VisorState.ALERT)
        ctx.say("Stopped. What was about to go wrong?")
        correction = ctx.hear_user(USER_CORRECTION)
        _record_correction(ctx, correction)
        # The operator's correction doubles as deliberate
        # reauthorization: clear the drill's latch so the run ends
        # nominal. Pre-trip pending actions stay invalid by design.
        tripping.reset()
        try:
            if ctx.safety.reset(
                authorization=f"demo-user correction: {correction[:120]}",
                actor="demo-user",
            ):
                print("[safety: stop cleared by user correction]", flush=True)
        except Exception as exc:
            print(f"[safety: resume refused ({exc})]", flush=True)
    else:
        print("[no trip: glide completed without interruption]", flush=True)
    finally:
        tripping.close()
        ctx.actuator = previous
    ctx.visor.set_state(VisorState.RESTING)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="demo_live_cobot",
        description="MIMIR Day 10 grand live showcase",
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--store", default="demo_memory.db")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allow-write", action="store_true")
    args = parser.parse_args(argv)

    if args.live:
        try:
            confirm = input(
                "LIVE MODE will move your cursor and speak aloud. "
                "Type GO to proceed: "
            )
        except EOFError:
            confirm = ""
        if confirm.strip() != "GO":
            print("live demo declined.")
            return 2
    if args.allow_write and not args.live:
        print("(note: --allow-write only affects --live mode)")

    visor = VisorController(
        on_change=lambda old, new: print(
            f"[visor: {old.value} -> {new.value}]", flush=True
        )
    )
    # One safety latch for the whole run; every scene's actuator gates
    # on it, and the Scene 4 correction reauthorizes the resume.
    safety = SafetyController()
    if args.live:
        voice = VoiceCoordinator()
        actuator = DesktopActuator(
            human_override_callback=lambda: visor.set_state(VisorState.ALERT),
            safety=safety,
        )
    else:
        voice = VoiceCoordinator(
            stt_backend=lambda timeout: None,
            tts_backend=lambda text: None,
        )
        actuator = DesktopActuator(
            human_override_callback=lambda: visor.set_state(VisorState.ALERT),
            mouse=DryMouse(),
            keyboard=DryKeyboard(),
            failsafe=DryFailsafe(),
            safety=safety,
        )
    store = PrincipleStore(args.store)
    ctx = DemoContext(
        live=args.live,
        allow_write=args.allow_write,
        store=store,
        voice=voice,
        visor=visor,
        actuator=actuator,
        safety=safety,
    )
    try:
        scene_observe(ctx, args.repo)
        scene_coordinate(ctx)
        try:
            scene_drive(ctx, args.repo)
        except CobotInterruptedError:
            print("[E-stop tripped during Scene 3; see Scene 4]", flush=True)
            ctx.visor.set_state(VisorState.ALERT)
        scene_estop_drill(ctx)
    except (TelemetryError, ActuatorError, VoiceError) as exc:
        print(f"demo error: {exc}")
        return 1
    finally:
        actuator.close()
        store.close()
    print("\nDemo complete. Behavioral Memory persisted at:", args.store)
    return 0


if __name__ == "__main__":
    sys.exit(main())
