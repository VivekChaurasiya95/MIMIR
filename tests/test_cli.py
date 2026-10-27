"""Tests for the unified CLI (Day 10).

Offline subcommands (inspect, replicate, watch --once) run for real
against temporary stores and this repository; the cobot loop runs with
a scripted voice double (Qt, when present, runs offscreen).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from mimir.cli import _run_cobot_cmd, build_parser, format_store_summary, main
from mimir.principles import (
    BehavioralPrinciple,
    ContextTrigger,
    EpistemicStatus,
)
from mimir.store import PrincipleStore
from mimir.ui.visor import VisorController, VisorState
from mimir.voice import VoiceCoordinator


def _git(repo: Path, *args: str) -> str:
    cmd = [
        "git", "-C", str(repo),
        "-c", "user.name=Mimir Test",
        "-c", "user.email=mimir-test@example.com",
        "-c", "commit.gpgsign=false",
        *args,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout


def _seed_repo(directory: str) -> str:
    """Fresh git repo owned by the test user (no ownership-guard issues)."""
    repo = Path(directory) / "watched"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "seed commit")
    return str(repo)


def _principle(id, name="Principle", confidence=0.85):
    return BehavioralPrinciple(
        id=id,
        name=name,
        category="workflow",
        context_trigger=ContextTrigger(
            scope="Git workflow", language_agnostic=True
        ),
        latent_invariant="Do the thing consistently.",
        anti_patterns=["Sloppy alternative"],
        epistemic_status=EpistemicStatus(
            confidence_score=confidence,
            supporting_episodes_count=2,
            contradictions_count=0,
            first_inferred="2026-10-12T10:00:00Z",
            last_validated="2026-10-27T08:30:00Z",
        ),
        inspectable_traces=["ep_seed_1"],
    )


def _store_with(directory, principles):
    path = str(Path(directory) / "mem.db")
    store = PrincipleStore(path)
    try:
        for principle in principles:
            store.save(principle)
    finally:
        store.close()
    return path


def _run(argv):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


class ScriptedVoice:
    """Voice double: scripted phrases in, recorded speech out."""

    def __init__(self, phrases):
        self.phrases = list(phrases)
        self.said: list[str] = []
        self._parser = VoiceCoordinator(
            stt_backend=lambda timeout: None, tts_backend=lambda text: None
        )

    def listen_phrase(self, timeout_sec=5.0):
        if self.phrases:
            return self.phrases.pop(0)
        return None

    def speak(self, text, on_start=None):
        if on_start is not None:
            on_start()
        self.said.append(text)

    def parse_intent(self, text):
        return self._parser.parse_intent(text)


class InspectTest(unittest.TestCase):
    def test_lists_principles(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _store_with(
                tmp, [_principle("bp_a_1"), _principle("bp_b_2")]
            )
            code, out = _run(["inspect", "--store", path])
        self.assertEqual(code, 0)
        self.assertIn("bp_a_1", out)
        self.assertIn("bp_b_2", out)
        self.assertIn("2 principle(s)", out)

    def test_missing_store_is_empty_not_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _run(
                ["inspect", "--store", str(Path(tmp) / "absent.db")]
            )
        self.assertEqual(code, 0)
        self.assertIn("no memory yet", out)

    def test_format_summary_empty(self):
        with PrincipleStore() as store:
            self.assertEqual(
                format_store_summary(store), "Behavioral Memory is empty."
            )


class ReplicateTest(unittest.TestCase):
    def test_offline_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _store_with(tmp, [_principle("bp_a_1")])
            code, out = _run(
                ["replicate", "--task", "sync the warehouse",
                 "--store", path]
            )
        self.assertEqual(code, 0)
        self.assertIn("sync the warehouse", out)
        self.assertIn("[MIMIR scaffold", out)
        self.assertIn("bp_a_1", out)
        self.assertIn("compliance:", out)


class WatchTest(unittest.TestCase):
    def test_once_observes_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _run(
                ["watch", "--repo", _seed_repo(tmp), "--once"]
            )
        self.assertEqual(code, 0)
        self.assertIn("episode", out.lower())
        self.assertIn("ep_git_", out)

    def test_non_repo_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _run(["watch", "--repo", tmp, "--once"])
        self.assertEqual(code, 1)
        self.assertIn("error", out.lower())


class CobotLoopTest(unittest.TestCase):
    def _cobot(self, phrases, principles=()):
        states: list[str] = []
        controller = VisorController(
            on_change=lambda old, new: states.append(new.value)
        )
        voice = ScriptedVoice(phrases)
        with tempfile.TemporaryDirectory() as tmp:
            path = _store_with(tmp, list(principles))
            args = build_parser().parse_args(["cobot", "--store", path])
            code = _run_cobot_cmd(
                args, voice=voice, controller=controller
            )
        return code, voice, controller, states

    def test_status_then_stop(self):
        code, voice, controller, states = self._cobot(
            ["what did you learn?", "stop"], [_principle("bp_a_1")]
        )
        self.assertEqual(code, 0)
        self.assertIn("bp_a_1", voice.said[0])
        self.assertIn("Powering down", voice.said[-1])
        self.assertIn(VisorState.LISTENING.value, states)
        self.assertIn(VisorState.THINKING.value, states)
        self.assertEqual(controller.state, VisorState.RESTING)

    def test_all_intents_respond(self):
        code, voice, _, _ = self._cobot(
            ["push my code", "go ahead", "never do that",
             "blabla wibble", "sleep"],
            [_principle("bp_push_1", "Push playbook")],
        )
        self.assertEqual(code, 0)
        joined = "\n".join(voice.said)
        self.assertIn("playbook", joined)
        self.assertIn("live demo", joined)
        self.assertIn("forbidden", joined)
        self.assertIn("didn't catch", joined)
        self.assertIn("Resting", voice.said[-1])

    def test_voice_error_exits_nonzero(self):
        from mimir.voice import VoiceError as RealVoiceError

        class RaisingVoice(ScriptedVoice):
            def listen_phrase(self, timeout_sec=5.0):
                raise RealVoiceError("mic unplugged")

        controller = VisorController()
        with tempfile.TemporaryDirectory() as tmp:
            args = build_parser().parse_args(
                ["cobot", "--store", str(Path(tmp) / "mem.db")]
            )
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = _run_cobot_cmd(
                    args, voice=RaisingVoice([]), controller=controller
                )
        self.assertEqual(code, 1)
        self.assertIn("error", buffer.getvalue().lower())

    def test_keyboard_interrupt_propagates(self):
        class QuittingVoice(ScriptedVoice):
            def listen_phrase(self, timeout_sec=5.0):
                raise KeyboardInterrupt

        controller = VisorController()
        with tempfile.TemporaryDirectory() as tmp:
            args = build_parser().parse_args(
                ["cobot", "--store", str(Path(tmp) / "mem.db")]
            )
            with self.assertRaises(KeyboardInterrupt):
                _run_cobot_cmd(
                    args, voice=QuittingVoice([]), controller=controller
                )


class ParserTest(unittest.TestCase):
    def test_missing_or_unknown_command_exits_2(self):
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            with self.assertRaises(SystemExit) as missing:
                main([])
            with self.assertRaises(SystemExit) as unknown:
                main(["frobnicate"])
        self.assertEqual(missing.exception.code, 2)
        self.assertEqual(unknown.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
