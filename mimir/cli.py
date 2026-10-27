"""Unified command-line orchestration for the digital cobot (Day 10).

Subcommands (``python -m mimir.cli <cmd>``):

- ``cobot``: voice coordination loop with visor presence (needs mic).
- ``watch``: passive Git observation, printing new episodes as they land.
- ``inspect``: print the principles stored in behavioral memory.
- ``replicate``: offline persona-aligned synthesis for a novel task.

``main()`` returns process exit codes and prints to stdout; hardware
failures surface as clear errors with nonzero codes, never tracebacks.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from typing import Any, Optional, Sequence

from .actor import ReplicationActor, ReplicationRequest
from .actuator import DesktopActuator
from .store import PrincipleStore
from .telemetry import TelemetryError, observe_git_repo
from .ui.visor import VisorController, VisorError, VisorState, run_visor
from .voice import VoiceCoordinator, VoiceError

__all__ = ["main"]

_DEFAULT_STORE = "mimir_memory.db"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return parsed


def _unit_float(value: str) -> float:
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("expected a number within [0, 1]")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mimir", description="MIMIR digital cobot orchestration"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    cobot = sub.add_parser("cobot", help="voice loop with visor presence")
    cobot.add_argument("--store", default=_DEFAULT_STORE)
    cobot.set_defaults(func=_run_cobot_cmd)

    watch = sub.add_parser("watch", help="passively observe a repository")
    watch.add_argument("--repo", required=True)
    watch.add_argument("--store", default=_DEFAULT_STORE)
    watch.add_argument("--interval-sec", type=float, default=60.0)
    watch.add_argument("--max-commits", type=_positive_int, default=10)
    watch.add_argument("--once", action="store_true")
    watch.set_defaults(func=_run_watch)

    inspect_cmd = sub.add_parser("inspect", help="list learned principles")
    inspect_cmd.add_argument("--store", default=_DEFAULT_STORE)
    inspect_cmd.set_defaults(func=_run_inspect)

    repl = sub.add_parser("replicate", help="synthesize a steered solution")
    repl.add_argument("--task", required=True)
    repl.add_argument("--store", default=_DEFAULT_STORE)
    repl.add_argument("--min-confidence", type=_unit_float, default=0.70)
    repl.add_argument("--top-k", type=_positive_int, default=5)
    repl.add_argument("--no-baseline", action="store_true")
    repl.set_defaults(func=_run_replicate)
    return parser


def format_store_summary(store: PrincipleStore) -> str:
    """One human-readable report of everything the store remembers."""
    principles = store.list_all()
    if not principles:
        return "Behavioral Memory is empty."
    lines = [f"Behavioral Memory: {len(principles)} principle(s)"]
    for principle in principles:
        status = principle.epistemic_status
        invariant = principle.latent_invariant
        if len(invariant) > 100:
            invariant = invariant[:97] + "..."
        lines.append(
            f"- {principle.id} [conf {status.confidence_score:.2f}, "
            f"+{status.supporting_episodes_count}/"
            f"-{status.contradictions_count}]: {principle.name} -- "
            f"{invariant}"
        )
    return "\n".join(lines)


def _open_store(path: str, *, create: bool) -> Optional[PrincipleStore]:
    if not create and not os.path.exists(path):
        return None
    return PrincipleStore(path)


def _cobot_respond(intent: str, store: PrincipleStore, text: str) -> str:
    if intent == "stop":
        return "Powering down. Say the word if you need me."
    if intent == "sleep":
        return "Resting. Relaunch the cobot to wake me."
    if intent == "status_inquiry":
        return f"Here is what I have learned:\n{format_store_summary(store)}"
    if intent == "push_code":
        hits = store.query_relevant(
            "commit push github workflow", top_k=3, min_confidence=0.0
        )
        if not hits:
            return (
                "I have no push principles yet. Show me once by committing "
                "and pushing, and I will learn the workflow."
            )
        names = ", ".join(p.name for p in hits)
        return (
            f"My push playbook ({len(hits)} principles: {names}). Say "
            "'go ahead' to confirm, then run the live demo to execute."
        )
    if intent == "approve":
        return "Confirmed. Live execution runs from demo_live_cobot.py --live."
    if intent == "forbid":
        return f"Noted and forbidden: {text.strip()}"
    return "I didn't catch that. Try 'status', 'push my code', 'sleep', or 'stop'."


def _voice_loop(
    voice: VoiceCoordinator,
    controller: VisorController,
    store: PrincipleStore,
    actuator: DesktopActuator,
) -> int:
    while True:
        if actuator.interrupted:
            controller.set_state(VisorState.ALERT)
            voice.speak("Stopped. What was about to go wrong?")
            actuator.reset()
            continue
        controller.set_state(VisorState.LISTENING)
        phrase = voice.listen_phrase()
        if phrase is None:
            continue
        intent = voice.parse_intent(phrase)["intent"]
        controller.set_state(VisorState.THINKING)
        response = _cobot_respond(intent, store, phrase)
        controller.set_state(VisorState.LISTENING)
        print(f"[heard:{intent}] {phrase}")
        voice.speak(response)
        if intent in ("stop", "sleep"):
            controller.set_state(VisorState.RESTING)
            return 0


def _run_cobot_cmd(
    args: argparse.Namespace,
    *,
    voice: Optional[VoiceCoordinator] = None,
    controller: Optional[VisorController] = None,
) -> int:
    """Voice loop; ``voice``/``controller`` seams exist for headless tests."""
    voice = voice if voice is not None else VoiceCoordinator()
    controller = controller if controller is not None else VisorController()
    actuator = DesktopActuator(
        human_override_callback=lambda: controller.set_state(VisorState.ALERT)
    )
    try:
        try:
            app, _pill = run_visor(controller)
        except VisorError as exc:
            print(f"[visor unavailable: {exc}]")
            app = None

        def run_loop() -> int:
            # The store connection lives in whichever thread runs the
            # loop: SQLite connections cannot cross threads.
            store = _open_store(args.store, create=False) or PrincipleStore(
                ":memory:"
            )
            try:
                return _voice_loop(voice, controller, store, actuator)
            finally:
                store.close()

        if app is None:
            try:
                return run_loop()
            except VoiceError as exc:
                print(f"error: {exc}")
                return 1
        outcome: dict[str, Any] = {}

        def loop_in_thread() -> None:
            try:
                outcome["code"] = run_loop()
            except VoiceError as exc:
                outcome["code"] = 1
                outcome["error"] = str(exc)
            except BaseException as exc:
                outcome["exc"] = exc

        worker = threading.Thread(target=loop_in_thread, daemon=True)
        worker.start()
        # Pump events until the loop thread finishes instead of app.exec():
        # a scripted loop can finish before exec() starts, which would
        # strand the quit signal and hang forever.
        while worker.is_alive():
            app.processEvents()
            worker.join(timeout=0.05)
        app.processEvents()
        if "exc" in outcome:
            raise outcome["exc"]
        if "error" in outcome:
            print(f"error: {outcome['error']}")
        return int(outcome.get("code", 0))
    finally:
        actuator.close()


def _run_watch(args: argparse.Namespace) -> int:
    if not args.once and args.interval_sec <= 0:
        print("error: --interval-sec must be positive")
        return 2
    seen: set[str] = set()
    first = True
    while True:
        try:
            episodes = observe_git_repo(
                args.repo,
                allowed_roots=[args.repo],
                max_commits=args.max_commits,
            )
        except TelemetryError as exc:
            print(f"error: {exc}")
            return 1
        fresh = [e for e in episodes if e.source_ref not in seen]
        for episode in episodes:
            seen.add(episode.source_ref)
        if first or fresh:
            print(
                f"observed {len(episodes)} episode(s), "
                f"{len(fresh)} new: "
                + (", ".join(e.id for e in fresh) if fresh else "(none new)")
            )
            first = False
        if args.once:
            return 0
        time.sleep(args.interval_sec)


def _run_inspect(args: argparse.Namespace) -> int:
    store = _open_store(args.store, create=False)
    if store is None:
        print(f"no memory yet (store not found: {args.store})")
        return 0
    try:
        print(format_store_summary(store))
    finally:
        store.close()
    return 0


def _run_replicate(args: argparse.Namespace) -> int:
    store = _open_store(args.store, create=True)
    assert store is not None
    try:
        actor = ReplicationActor(store)
        result = actor.replicate(
            ReplicationRequest(
                task_description=args.task,
                top_k_principles=args.top_k,
                min_confidence=args.min_confidence,
            ),
            generate_contrast_baseline=not args.no_baseline,
        )
    finally:
        store.close()
    print(f"task: {result.task_description}")
    applied = ", ".join(p.id for p in result.applied_principles) or "(none)"
    print(f"applied principles: {applied}")
    print("--- steered solution (offline scaffold; no LLM configured) ---")
    print(result.replicated_solution)
    report = result.compliance_report
    print(
        f"compliance: {'PASS' if report['compliant'] else 'FLAGGED'} "
        f"(score {report['compliance_score']:.2f}, "
        f"{len(report['violations'])} violation(s))"
    )
    if result.contrast_baseline is None:
        print("(no contrast baseline: no LLM configured or --no-baseline)")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\ninterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
