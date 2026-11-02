"""Phase 1 ambient sensory pipeline: privacy gate, chunking, wiring.

Covers :mod:`mimir.privacy` (allowlist-only + forbidden files + token
redaction pre-publish), :mod:`mimir.chunking` (micro-actions grouped
into BehavioralEpisode), the gate plumbing in the filesystem watcher /
AST observer / sensory service, and the AppState background-daemon
wiring. Qt-free.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from mimir.chunking import (
    EpisodicChunkingEngine,
    MicroAction,
    micro_action_from_event,
)
from mimir.events import EventBus, EventType, create_event
from mimir.privacy import PrivacyGate


def _gate(tmp: str) -> PrivacyGate:
    return PrivacyGate([tmp])


class PrivacyGateTest(unittest.TestCase):
    def test_empty_allowlist_rejected(self):
        with self.assertRaises(ValueError):
            PrivacyGate([])

    def test_allowlist_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate(tmp)
            inside = os.path.join(tmp, "proj", "app.py")
            self.assertTrue(gate.is_within_allowlist(inside))
            self.assertTrue(gate.is_within_allowlist(tmp))
            self.assertFalse(gate.is_within_allowlist(tmp + "_sibling"))
            with tempfile.TemporaryDirectory() as other:
                self.assertFalse(
                    gate.is_within_allowlist(
                        os.path.join(other, "app.py")))

    def test_forbidden_secret_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate(tmp)
            for name in (".env", ".env.local", "cert.pem", "key.key",
                         "id_rsa", "id_rsa.pub", "bundle.pfx"):
                path = os.path.join(tmp, name)
                self.assertTrue(gate.is_forbidden_file(path), name)
                self.assertFalse(gate.allows(path), name)
                decision = gate.decide(path)
                self.assertFalse(decision.allowed)
            ok = os.path.join(tmp, "app.py")
            self.assertFalse(gate.is_forbidden_file(ok))
            self.assertTrue(gate.allows(ok))
            self.assertTrue(gate.decide(ok).allowed)

    def test_outside_allowlist_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate(tmp)
            with tempfile.TemporaryDirectory() as other:
                outsider = os.path.join(other, "app.py")
                self.assertFalse(gate.allows(outsider))
                self.assertEqual(
                    gate.decide(outsider).reason,
                    "outside allowlisted roots")

    def test_token_redaction_before_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate(tmp)
            result = gate.redact("key = ghp_abcdefghijklmnop1234")
            self.assertNotIn("ghp_abcdefghijklmnop1234", result.text)
            self.assertIn("github_token", result.kinds)

    def test_redact_payload_deep_and_pure(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate(tmp)
            payload = {
                "file": "a.py",
                "nested": {"note": 'password = "s3cret-value"'},
                "items": ["plain", "token = abc123"],
            }
            redacted, kinds, count = gate.redact_payload(payload)
            self.assertIn("assigned_secret", kinds)
            self.assertGreater(count, 0)
            self.assertIn("[REDACTED:assigned_secret]",
                          redacted["nested"]["note"])
            # Input untouched.
            self.assertIn("s3cret-value", payload["nested"]["note"])

    def test_gate_payload_for_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate(tmp)
            secret = {"note": "Bearer tok_live_abc123"}
            denied, decision = gate.gate_payload_for_path(
                os.path.join(tmp, ".env"), secret)
            self.assertIsNone(denied)
            self.assertFalse(decision.allowed)
            allowed, decision = gate.gate_payload_for_path(
                os.path.join(tmp, "app.py"), secret)
            self.assertTrue(decision.allowed)
            assert allowed is not None
            self.assertNotIn("tok_live_abc123", allowed["note"])


class ChunkingTest(unittest.TestCase):
    def _engine(self, **kwargs):
        kwargs.setdefault("id_fn", lambda: "ep_live_test01")
        return EpisodicChunkingEngine(**kwargs)

    def test_flush_groups_saves_into_episode(self):
        engine = self._engine(repo="/repo")
        engine.note(MicroAction("created", "/repo/a.py", 1000.0))
        engine.note(MicroAction("modified", "/repo/a.py", 1005.0))
        engine.note(MicroAction("modified", "/repo/b.py", 1010.0))
        episode = engine.flush()
        self.assertIsNotNone(episode)
        assert episode is not None
        self.assertEqual(episode.id, "ep_live_test01")
        self.assertEqual(episode.source, "live_chunk")
        self.assertEqual(episode.repo, "/repo")
        self.assertEqual(len(episode.transitions), 2)
        self.assertIn("3 action(s)", episode.summary)
        self.assertEqual(episode.redactions_applied, 0)
        self.assertEqual(episode.redaction_kinds, ())
        # Contract round-trip.
        from mimir.episodes import BehavioralEpisode
        revived = BehavioralEpisode.from_dict(episode.to_dict())
        self.assertEqual(revived.id, episode.id)

    def test_flush_empty_is_none(self):
        self.assertIsNone(self._engine().flush())

    def test_idle_gap_splits_bursts(self):
        engine = self._engine(idle_gap_s=60.0,
                              id_fn=lambda: "ep_live_gap")
        engine.note(MicroAction("modified", "/r/a.py", 1000.0))
        closed = engine.note(MicroAction("modified", "/r/a.py", 2000.0))
        self.assertIsNotNone(closed)
        assert closed is not None
        self.assertEqual(len(closed.transitions), 1)
        self.assertEqual(engine.pending_count, 1)

    def test_window_splits_long_bursts(self):
        engine = self._engine(window_s=100.0, idle_gap_s=1000.0)
        engine.note(MicroAction("modified", "/r/a.py", 1000.0))
        engine.note(MicroAction("modified", "/r/a.py", 1050.0))
        closed = engine.note(MicroAction("modified", "/r/a.py", 1200.0))
        self.assertIsNotNone(closed)
        self.assertEqual(engine.pending_count, 1)

    def test_max_actions_splits_buffer(self):
        engine = self._engine(max_actions=2, idle_gap_s=10000.0,
                              window_s=10000.0)
        engine.note(MicroAction("modified", "/r/a.py", 1000.0))
        engine.note(MicroAction("modified", "/r/a.py", 1001.0))
        closed = engine.note(MicroAction("modified", "/r/a.py", 1002.0))
        self.assertIsNotNone(closed)
        assert closed is not None
        self.assertIn("2 action(s)", closed.summary)

    def test_ast_detail_lines_use_names_only(self):
        engine = self._engine()
        engine.note(MicroAction(
            "ast_delta", "/r/m.py", 1000.0,
            detail={"added_functions": ["f"],
                    "modified_functions": ["g"],
                    "removed_functions": ["h"],
                    "added_classes": ["C"],
                    "removed_classes": []}))
        episode = engine.flush()
        assert episode is not None
        diff = episode.transitions[0].diff
        for token in ("+ def f(): ...", "~ def g(): [modified]",
                      "- def h(): ...", "+ class C: ..."):
            self.assertIn(token, diff)

    def test_gate_skips_forbidden_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate(tmp)
            engine = self._engine(repo=tmp, privacy_gate=gate)
            engine.note(MicroAction(
                "modified", os.path.join(tmp, ".env"), 1000.0))
            engine.note(MicroAction(
                "modified", os.path.join(tmp, "ok.py"), 1001.0))
            episode = engine.flush()
            assert episode is not None
            self.assertEqual(len(episode.transitions), 1)
            self.assertTrue(
                episode.transitions[0].path.endswith("ok.py"))
            self.assertEqual(episode.redactions_applied, 1)
            self.assertIn("secret_filename", episode.redaction_kinds)

    def test_all_denied_yields_no_episode(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate(tmp)
            engine = self._engine(repo=tmp, privacy_gate=gate)
            engine.note(MicroAction(
                "modified", os.path.join(tmp, "id_rsa"), 1000.0))
            self.assertIsNone(engine.flush())

    def test_invalid_config_rejected(self):
        with self.assertRaises(ValueError):
            MicroAction("keystroke", "/r/a.py", 1.0)
        with self.assertRaises(ValueError):
            EpisodicChunkingEngine(window_s=0)
        with self.assertRaises(ValueError):
            EpisodicChunkingEngine(idle_gap_s=-1)
        with self.assertRaises(ValueError):
            EpisodicChunkingEngine(max_actions=0)

    def test_micro_action_from_event(self):
        file_event = create_event(
            EventType.FILE_MODIFIED, "sensory.fs_watcher",
            {"path": "/r/a.py", "root": "/r", "kind": "modified"},
            session_id="s")
        action = micro_action_from_event(file_event)
        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual((action.kind, action.path),
                         ("modified", "/r/a.py"))
        ast_event = create_event(
            EventType.AST_DELTA, "sensory.ast_observer",
            {"file": "/r/m.py", "added_functions": ["f"],
             "modified_functions": [], "removed_functions": [],
             "added_classes": [], "removed_classes": [],
             "syntax_valid": True},
            session_id="s")
        action = micro_action_from_event(ast_event)
        assert action is not None
        self.assertEqual(action.kind, "ast_delta")
        self.assertEqual(action.detail["added_functions"], ["f"])
        other = create_event(EventType.ACTION_PROPOSED, "x", {},
                             session_id="s")
        self.assertIsNone(micro_action_from_event(other))


class WatcherGateTest(unittest.TestCase):
    def test_poll_watcher_drops_forbidden_files(self):
        from mimir.watch_fs import FilesystemWatcher
        with tempfile.TemporaryDirectory() as tmp:
            bus = EventBus()
            watcher = FilesystemWatcher(
                [tmp], backend="poll", debounce_s=0.0,
                poll_interval_s=60.0, bus=bus,
                allowed_roots=[tmp])
            watcher.poll_once(now=1000.0)
            Path(tmp, ".env").write_text("K=V\n", encoding="utf-8")
            Path(tmp, "ok.py").write_text("x = 1\n", encoding="utf-8")
            events = watcher.poll_once(now=1001.0)
            paths = [e.path for e in events]
            self.assertTrue(any(p.endswith("ok.py") for p in paths))
            self.assertFalse(any(p.endswith(".env") for p in paths))

    def test_note_many_drops_outside_allowlist(self):
        from mimir.watch_fs import FilesystemWatcher
        with tempfile.TemporaryDirectory() as tmp:
            bus = EventBus()
            watcher = FilesystemWatcher(
                [tmp], backend="poll", debounce_s=0.0,
                poll_interval_s=60.0, bus=bus,
                allowed_roots=[tmp])
            with tempfile.TemporaryDirectory() as other:
                outsider = os.path.join(other, "evil.py")
                watcher.note_many([("created", outsider, tmp)],
                                  now=1000.0)
            self.assertEqual(watcher.debouncer.pending_count(), 0)

    def test_ast_observer_gate_refuses_secrets(self):
        from mimir.ast_delta import AstObserver
        with tempfile.TemporaryDirectory() as tmp:
            gate = _gate(tmp)
            observer = AstObserver(bus=EventBus(), privacy_gate=gate)
            secret = os.path.join(tmp, "k.pem")
            Path(secret).write_text("x", encoding="utf-8")
            self.assertIsNone(observer.observe_file(secret))
            with tempfile.TemporaryDirectory() as other:
                outsider = os.path.join(other, "m.py")
                Path(outsider).write_text("x = 1\n", encoding="utf-8")
                self.assertIsNone(observer.observe_file(outsider))


class SensoryGateTest(unittest.TestCase):
    def test_service_reports_gated_status(self):
        from mimir.sensory import SensoryService
        with tempfile.TemporaryDirectory() as tmp:
            service = SensoryService(
                [tmp], allowed_roots=[tmp], bus=EventBus(),
                fs_backend="poll", poll_interval_s=60.0)
            status = service.status()
            self.assertTrue(status["privacy_gated"])
            self.assertEqual(len(status["allowed_roots"]), 1)
            ungated = SensoryService([tmp], bus=EventBus(),
                                     fs_backend="poll")
            self.assertFalse(ungated.status()["privacy_gated"])

    def test_service_file_to_ast_flow_stays_gated(self):
        from mimir.events import EventType
        from mimir.sensory import SensoryService
        with tempfile.TemporaryDirectory() as tmp:
            bus = EventBus()
            seen: list = []
            bus.subscribe(EventType.FILE_CREATED,
                          seen.append, name="t")
            service = SensoryService(
                [tmp], allowed_roots=[tmp], bus=bus,
                fs_backend="poll", poll_interval_s=60.0)
            service.start()
            try:
                service.watcher.stop()
                Path(tmp, ".env").write_text("K=V\n", encoding="utf-8")
                Path(tmp, "flow.py").write_text(
                    "def a():\n    pass\n", encoding="utf-8")
                service.watcher.poll_once(now=2000.0)
                service.watcher.poll_once(now=2001.0)
            finally:
                service.stop()
            paths = [e.payload.get("path", "") for e in seen]
            self.assertTrue(any(p.endswith("flow.py") for p in paths))
            self.assertFalse(any(p.endswith(".env") for p in paths))


class AppStateAmbientWiringTest(unittest.TestCase):
    def test_senses_daemon_is_gated_with_chunker(self):
        from desktop_app.state import AppState
        state = AppState(store_path=":memory:")
        with tempfile.TemporaryDirectory() as tmp:
            try:
                self.assertTrue(
                    state.start_senses(roots=[tmp], repo=tmp))
                status = state.live_senses
                assert status is not None
                self.assertTrue(status.get("running"))
                self.assertTrue(status.get("privacy_gated"))
                self.assertEqual(status.get("chunk_pending"), 0)
                self.assertIsNotNone(state._chunker)
            finally:
                state.stop_senses()
                self.assertIsNone(state.live_senses)
                state.close()

    def test_save_bursts_chunk_into_episode(self):
        from desktop_app.state import AppState
        bus = EventBus()
        state = AppState(store_path=":memory:", event_bus=bus)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                state.start_senses(roots=[tmp], repo=tmp)
                target = os.path.join(tmp, "w.py")
                bus.emit(EventType.FILE_CREATED, "sensory.fs_watcher",
                         {"path": target, "root": tmp,
                          "kind": "created", "observed_at": 1.0})
                bus.emit(EventType.FILE_MODIFIED, "sensory.fs_watcher",
                         {"path": target, "root": tmp,
                          "kind": "modified", "observed_at": 2.0})
                self.assertEqual(
                    state.live_senses.get("chunk_pending"), 2)
                episode = state.flush_episode_chunk()
                self.assertIsNotNone(episode)
                assert episode is not None
                self.assertEqual(episode.source, "live_chunk")
                self.assertEqual(state.episodes[0].id, episode.id)
                self.assertEqual(
                    state.live_senses.get("chunk_pending"), 0)
        finally:
            state.close()

    def test_ast_streaming_and_chunking_coexist(self):
        from desktop_app.state import AppState
        bus = EventBus()
        state = AppState(store_path=":memory:", event_bus=bus)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                state.start_senses(roots=[tmp], repo=tmp)
                bus.emit(EventType.AST_DELTA, "sensory.ast_observer", {
                    "file": os.path.join(tmp, "code.py"),
                    "language": "python",
                    "added_functions": ["new_func"],
                    "modified_functions": [],
                    "removed_functions": [],
                    "added_classes": [],
                    "removed_classes": [],
                    "syntax_valid": True,
                })
                self.assertTrue(
                    any(e.source == "live_ast"
                        for e in state.episodes))
                # AST deltas stream directly; the save chunker is
                # untouched by them.
                self.assertEqual(
                    state.live_senses.get("chunk_pending"), 0)
                self.assertIsNone(state.flush_episode_chunk())
        finally:
            state.close()


if __name__ == "__main__":
    unittest.main()
