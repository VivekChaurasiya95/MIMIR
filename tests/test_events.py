"""Tests for the central event runtime (plan section 3.2, step 1).

Covers the Pydantic event contract, sync dispatch, bounded priority
queues with safety preemption (via the loop-free enqueue/pump path),
idempotency/correlation, consumer-failure isolation, the audit log,
visor binding, and the wiring of AppState/CLI/actuator through the
bus. Qt-free and loop-free throughout.
"""

import asyncio
import contextlib
import io
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from desktop_app.state import AppState
from mimir.actuator import DesktopActuator
from mimir.cli import main
from mimir.episodes import (
    BehavioralEpisode,
    EpisodeContext,
    EpisodeOutcome,
    FileTransition,
)
from mimir.events import (
    DEFAULT_PRIORITY,
    EVENT_FAMILY,
    Event,
    EventBus,
    EventFamily,
    EventPriority,
    EventType,
    EventValidationError,
    bind_visor_to_bus,
    create_event,
    get_event_bus,
    reset_event_bus,
    set_event_bus,
)
from mimir.ui.visor import VisorController, VisorState


def _episode(eid="ep_evt_001", ref="aaa111"):
    return BehavioralEpisode(
        id=eid,
        source="git",
        repo="/repo",
        source_ref=ref,
        observed_at="2026-10-27T08:30:00Z",
        actor="Ada",
        summary="fix: isolate db errors",
        context=EpisodeContext(scope="payments", language="python"),
        transitions=[
            FileTransition(path="payments/db.py", status="modified",
                           diff="+raise DomainError", additions=1,
                           deletions=0)
        ],
        outcome=EpisodeOutcome(build="pass", tests="pass"),
        redactions_applied=0,
        redaction_kinds=[],
    )


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
    repo = Path(directory) / "watched"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "seed commit")
    return str(repo)


class _FakeMouse:
    def position(self):
        return (0, 0)

    def move_to(self, x, y):
        pass

    def click(self):
        pass


class _FakeKeyboard:
    def write_char(self, char):
        pass


class _FakeFailsafe:
    def arm(self, on_trip):
        self.armed_with = on_trip

    def disarm(self):
        pass

    def note_commanded(self, x, y):
        pass


class EventContractTest(unittest.TestCase):
    def test_all_plan_families_present(self):
        self.assertEqual(len(EventType), 23)
        counts: dict[EventFamily, int] = {}
        for family in EVENT_FAMILY.values():
            counts[family] = counts.get(family, 0) + 1
        self.assertEqual(counts[EventFamily.OBSERVATION], 6)
        self.assertEqual(counts[EventFamily.COGNITIVE], 4)
        self.assertEqual(counts[EventFamily.CONVERSATIONAL], 4)
        self.assertEqual(counts[EventFamily.EXECUTION], 5)
        self.assertEqual(counts[EventFamily.SAFETY], 4)
        self.assertEqual(set(EVENT_FAMILY), set(EventType))

    def test_plan_example_event_validates(self):
        event = Event.from_dict({
            "event_id": "evt_1042",
            "event_type": "AST_DELTA",
            "source": "sensory.ast_observer",
            "timestamp": 1793340000,
            "priority": "normal",
            "correlation_id": "session_01",
            "payload": {
                "file": "runtime/actuator.py",
                "added_functions": ["execute_action"],
                "modified_functions": ["validate_target"],
                "removed_functions": [],
            },
        })
        self.assertEqual(event.event_type, EventType.AST_DELTA)
        self.assertEqual(event.family, EventFamily.OBSERVATION)
        self.assertEqual(event.session_id, "")

    def test_defaults_generated(self):
        before = time.time()
        event = create_event(EventType.GIT_CHANGED, "sensory.git_observer")
        self.assertTrue(event.event_id.startswith("evt_"))
        self.assertTrue(event.correlation_id.startswith("corr_"))
        self.assertGreaterEqual(event.timestamp, before - 1.0)
        self.assertEqual(event.payload, {})
        self.assertIsNone(event.expires_at)

    def test_priority_defaults_follow_family(self):
        self.assertEqual(
            DEFAULT_PRIORITY[EventFamily.SAFETY], EventPriority.CRITICAL)
        cases = [
            (EventType.E_STOP_TRIGGERED, EventPriority.CRITICAL),
            (EventType.ACTION_STARTED, EventPriority.HIGH),
            (EventType.INTENT_PARSED, EventPriority.NORMAL),
            (EventType.INFERENCE_COMPLETED, EventPriority.NORMAL),
            (EventType.GIT_CHANGED, EventPriority.LOW),
            (EventType.FILE_MODIFIED, EventPriority.LOW),
        ]
        for event_type, expected in cases:
            with self.subTest(event_type=event_type):
                event = create_event(event_type, "test.source")
                self.assertEqual(event.priority, expected)

    def test_explicit_priority_overrides_default(self):
        event = create_event(
            EventType.GIT_CHANGED, "test.source",
            priority=EventPriority.CRITICAL)
        self.assertEqual(event.priority, EventPriority.CRITICAL)
        event = create_event(
            EventType.E_STOP_TRIGGERED, "test.source", priority="low")
        self.assertEqual(event.priority, EventPriority.LOW)

    def test_rejects_contract_violations(self):
        with self.assertRaises(EventValidationError):
            create_event("NOT_A_TYPE", "test.source")
        with self.assertRaises(EventValidationError):
            create_event(EventType.GIT_CHANGED, "   ")
        with self.assertRaises(EventValidationError):
            create_event(EventType.GIT_CHANGED, "test.source",
                         priority="urgent")
        with self.assertRaises(EventValidationError):
            create_event(EventType.GIT_CHANGED, "test.source",
                         timestamp=-1.0)
        with self.assertRaises(EventValidationError):
            create_event(EventType.GIT_CHANGED, "test.source",
                         expires_at="soon")
        with self.assertRaises(EventValidationError):
            Event.from_dict({
                "event_type": "GIT_CHANGED", "source": "s",
                "mystery": 1,
            })

    def test_round_trip(self):
        event = create_event(
            EventType.INTENT_PARSED, "voice.intent_parser",
            {"intent": "stop"}, session_id="sess_1",
            correlation_id="corr_1", event_id="evt_1")
        clone = Event.from_dict(event.to_dict())
        self.assertEqual(clone, event)

    def test_events_are_frozen(self):
        event = create_event(EventType.GIT_CHANGED, "test.source")
        with self.assertRaises(Exception):
            event.source = "mutated"  # type: ignore[misc]

    def test_child_inherits_correlation(self):
        parent = create_event(EventType.VOICE_CAPTURED, "voice.capture",
                              session_id="sess_9")
        child = parent.child(EventType.INTENT_PARSED, "voice.intent_parser",
                             {"intent": "stop"})
        self.assertEqual(child.correlation_id, parent.correlation_id)
        self.assertEqual(child.session_id, "sess_9")
        self.assertNotEqual(child.event_id, parent.event_id)

    def test_expiry(self):
        live = create_event(EventType.GIT_CHANGED, "s",
                            expires_at=time.time() + 60.0)
        dead = create_event(EventType.GIT_CHANGED, "s",
                            expires_at=time.time() - 1.0)
        self.assertFalse(live.is_expired())
        self.assertTrue(dead.is_expired())


class EventBusSyncTest(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()

    def test_publish_fans_out_to_matching_only(self):
        seen: list[Event] = []
        other: list[Event] = []
        self.bus.subscribe(EventType.GIT_CHANGED, seen.append)
        self.bus.subscribe(EventType.E_STOP_TRIGGERED, other.append)
        result = self.bus.publish(
            create_event(EventType.GIT_CHANGED, "s"))
        self.assertEqual(result.delivered, 1)
        self.assertEqual(len(seen), 1)
        self.assertEqual(other, [])

    def test_subscribe_all_and_unsubscribe(self):
        seen: list[Event] = []
        token = self.bus.subscribe(seen.append)
        self.bus.publish(create_event(EventType.GIT_CHANGED, "s"))
        token.unsubscribe()
        self.bus.publish(create_event(EventType.E_STOP_TRIGGERED, "s"))
        self.assertEqual(len(seen), 1)
        token.unsubscribe()  # idempotent

    def test_subscribe_family(self):
        seen: list[Event] = []
        self.bus.subscribe_family(EventFamily.SAFETY, seen.append)
        self.bus.publish(create_event(EventType.E_STOP_TRIGGERED, "s"))
        self.bus.publish(create_event(EventType.GIT_CHANGED, "s"))
        self.assertEqual(len(seen), 1)
        with self.assertRaises(ValueError):
            self.bus.subscribe_family("nope", seen.append)

    def test_duplicate_event_id_dispatched_once(self):
        seen: list[Event] = []
        self.bus.subscribe(seen.append)
        event = create_event(EventType.GIT_CHANGED, "s", event_id="evt_dup")
        first = self.bus.publish(event)
        second = self.bus.publish(event)
        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.bus.stats()["duplicates"], 1)

    def test_consumer_failure_isolated(self):
        def bad(_event: Event) -> None:
            raise RuntimeError("boom")

        good: list[Event] = []
        self.bus.subscribe(EventType.GIT_CHANGED, bad, name="bad")
        self.bus.subscribe(EventType.GIT_CHANGED, good.append, name="good")
        result = self.bus.publish(
            create_event(EventType.GIT_CHANGED, "s"))
        self.assertEqual(result.delivered, 1)
        self.assertEqual(result.failed, 1)
        self.assertEqual(len(good), 1)
        self.assertIn("boom", result.errors[0].error)
        actions = [r.action for r in self.bus.audit_log()]
        self.assertIn("error", actions)
        self.assertIn("dispatched", actions)

    def test_expired_event_skipped(self):
        seen: list[Event] = []
        self.bus.subscribe(seen.append)
        result = self.bus.publish(create_event(
            EventType.GIT_CHANGED, "s", expires_at=time.time() - 5.0))
        self.assertTrue(result.expired)
        self.assertEqual(seen, [])

    def test_emit_never_raises(self):
        self.assertIsNone(self.bus.emit("BOGUS", "s"))
        self.assertIsNone(self.bus.emit(EventType.GIT_CHANGED, "   "))
        event = self.bus.emit(EventType.GIT_CHANGED, "s", {"n": 1})
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.payload, {"n": 1})

    def test_claim_action_one_shot(self):
        self.assertTrue(self.bus.claim_action("action_104"))
        self.assertFalse(self.bus.claim_action("action_104"))
        with self.assertRaises(ValueError):
            self.bus.claim_action("  ")

    def test_correlation_tracked(self):
        parent = create_event(EventType.VOICE_CAPTURED, "s")
        child = parent.child(EventType.INTENT_PARSED, "s")
        self.bus.publish(parent)
        self.bus.publish(child)
        chain = self.bus.events_for_correlation(parent.correlation_id)
        self.assertEqual(chain, [parent.event_id, child.event_id])
        self.assertEqual(self.bus.events_for_correlation("missing"), [])

    def test_audit_and_stats(self):
        self.bus.subscribe(EventType.GIT_CHANGED, lambda _e: None,
                           name="sink")
        event = create_event(EventType.GIT_CHANGED, "s")
        self.bus.publish(event)
        stats = self.bus.stats()
        self.assertEqual(stats["published"], 1)
        self.assertEqual(stats["dispatched"], 1)
        self.assertEqual(stats["subscribers"], 1)
        records = self.bus.audit_for_event(event.event_id)
        self.assertGreaterEqual(len(records), 2)
        self.assertTrue(all(r.event_id == event.event_id for r in records))
        newest = self.bus.audit_log(limit=1, newest_first=True)
        self.assertEqual(len(newest), 1)

    def test_invalid_calls_rejected(self):
        with self.assertRaises(EventValidationError):
            self.bus.publish("not-an-event")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            self.bus.subscribe()  # type: ignore[call-arg]
        with self.assertRaises(ValueError):
            self.bus.subscribe(EventType.GIT_CHANGED)
        with self.assertRaises(ValueError):
            EventBus(queue_sizes={EventPriority.LOW: 0})

    def test_global_bus_helpers(self):
        first = get_event_bus()
        self.assertIs(get_event_bus(), first)
        fresh = reset_event_bus()
        self.assertIsNot(fresh, first)
        self.assertIs(get_event_bus(), fresh)
        custom = EventBus()
        set_event_bus(custom)
        self.assertIs(get_event_bus(), custom)
        with self.assertRaises(ValueError):
            set_event_bus("nope")  # type: ignore[arg-type]
        reset_event_bus()


class EventBusQueuedTest(unittest.TestCase):
    """Bounded priority queues + pump: fully loop-free, Qt-thread safe."""

    def setUp(self):
        self.bus = EventBus()

    def test_enqueue_pump_delivers(self):
        seen: list[str] = []
        self.bus.subscribe(EventType.GIT_CHANGED,
                           lambda e: seen.append(e.event_id))
        event = create_event(EventType.GIT_CHANGED, "s")
        result = self.bus.enqueue(event)
        self.assertTrue(result.queued)
        self.assertFalse(result.dropped_oldest)
        self.assertEqual(seen, [])
        pumped = self.bus.pump()
        self.assertEqual(len(pumped), 1)
        self.assertEqual(pumped[0].delivered, 1)
        self.assertEqual(seen, [event.event_id])
        self.assertEqual(self.bus.pump(), [])

    def test_safety_preempts_queued_telemetry(self):
        order: list[str] = []
        self.bus.subscribe(lambda e: order.append(e.event_type.value))
        for n in range(3):
            self.bus.enqueue(create_event(EventType.GIT_CHANGED,
                                          "telemetry", {"n": n}))
        self.bus.enqueue(create_event(EventType.E_STOP_TRIGGERED, "safety"))
        self.bus.enqueue(create_event(EventType.ACTION_STARTED, "exec"))
        results = self.bus.pump()
        self.assertEqual(len(results), 5)
        self.assertEqual(order[0], "E_STOP_TRIGGERED")
        self.assertEqual(order[1], "ACTION_STARTED")
        self.assertEqual(order[2:], ["GIT_CHANGED"] * 3)

    def test_bounded_queue_sheds_oldest_telemetry(self):
        bus = EventBus(queue_sizes={EventPriority.LOW: 2})
        first = create_event(EventType.GIT_CHANGED, "s", {"n": 0})
        bus.enqueue(first)
        bus.enqueue(create_event(EventType.GIT_CHANGED, "s", {"n": 1}))
        result = bus.enqueue(
            create_event(EventType.GIT_CHANGED, "s", {"n": 2}))
        self.assertTrue(result.dropped_oldest)
        self.assertEqual(bus.stats()["dropped"], 1)
        dropped = [r for r in bus.audit_log() if r.action == "dropped"]
        self.assertEqual(len(dropped), 1)
        self.assertIn(first.event_id, dropped[0].detail)
        seen: list[int] = []
        bus.subscribe(EventType.GIT_CHANGED,
                      lambda e: seen.append(e.payload["n"]))
        bus.pump()
        self.assertEqual(seen, [1, 2])

    def test_enqueue_duplicate_and_expiry(self):
        seen: list[Event] = []
        self.bus.subscribe(seen.append)
        event = create_event(EventType.GIT_CHANGED, "s", event_id="evt_a1")
        first = self.bus.enqueue(event)
        second = self.bus.enqueue(event)
        self.assertTrue(first.queued)
        self.assertTrue(second.duplicate)
        stale = self.bus.enqueue(create_event(
            EventType.GIT_CHANGED, "s", expires_at=time.time() - 1.0))
        self.assertTrue(stale.expired)
        self.bus.pump()
        self.assertEqual(seen, [event])

    def test_pump_failure_isolated(self):
        def bad(_event: Event) -> None:
            raise RuntimeError("pump boom")

        good: list[Event] = []
        self.bus.subscribe(EventType.GIT_CHANGED, bad, name="bad")
        self.bus.subscribe(EventType.GIT_CHANGED, good.append, name="good")
        self.bus.enqueue(create_event(EventType.GIT_CHANGED, "s"))
        results = self.bus.pump()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].delivered, 1)
        self.assertEqual(results[0].failed, 1)
        self.assertEqual(len(good), 1)
        self.assertEqual(self.bus.stats()["errors"], 1)

    def test_pump_max_events(self):
        seen: list[Event] = []
        self.bus.subscribe(seen.append)
        for _ in range(3):
            self.bus.enqueue(create_event(EventType.GIT_CHANGED, "s"))
        self.assertEqual(len(self.bus.pump(max_events=2)), 2)
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(self.bus.pump()), 1)
        with self.assertRaises(ValueError):
            self.bus.pump(max_events=0)

    def test_enqueue_rejects_non_event(self):
        with self.assertRaises(EventValidationError):
            self.bus.enqueue("not-an-event")  # type: ignore[arg-type]

    def test_apublish_is_async_alias(self):
        event = create_event(EventType.GIT_CHANGED, "s")
        coro = self.bus.apublish(event)
        self.assertTrue(asyncio.iscoroutine(coro))
        coro.close()
        self.assertEqual(self.bus.stats()["queued"]["low"], 0)


class VisorBindingTest(unittest.TestCase):
    def test_events_drive_visor_states(self):
        bus = EventBus()
        controller = VisorController()
        tokens = bind_visor_to_bus(controller, bus)
        try:
            self.assertEqual(len(tokens), 23)
            bus.publish(create_event(EventType.GIT_CHANGED, "s"))
            self.assertEqual(controller.state, VisorState.OBSERVING)
            bus.publish(create_event(EventType.VOICE_CAPTURED, "s"))
            self.assertEqual(controller.state, VisorState.LISTENING)
            bus.publish(create_event(EventType.INTENT_PARSED, "s"))
            self.assertEqual(controller.state, VisorState.THINKING)
            bus.publish(create_event(EventType.ACTION_STARTED, "s"))
            self.assertEqual(controller.state, VisorState.DRIVING)
            bus.publish(create_event(EventType.E_STOP_TRIGGERED, "s"))
            self.assertEqual(controller.state, VisorState.ALERT)
            bus.publish(create_event(EventType.ACTION_COMPLETED, "s"))
            self.assertEqual(controller.state, VisorState.RESTING)
        finally:
            for token in tokens:
                token.unsubscribe()


class WiringTest(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.events: list[Event] = []
        self.bus.subscribe(self.events.append)
        self._states: list[AppState] = []

    def tearDown(self):
        for state in self._states:
            state.close()

    def _state(self) -> AppState:
        state = AppState(store_path=":memory:", event_bus=self.bus)
        self._states.append(state)
        return state

    def _types(self) -> list[EventType]:
        return [e.event_type for e in self.events]

    def test_respond_emits_intent_parsed(self):
        state = self._state()
        state.respond("status_inquiry", "what have you learned?")
        self.assertIn(EventType.INTENT_PARSED, self._types())
        event = self.events[-1]
        self.assertEqual(event.payload["intent"], "status_inquiry")
        self.assertEqual(event.session_id, state.session_id)

    def test_learn_emits_inference_completed(self):
        state = self._state()
        state.learn([_episode()])
        self.assertIn(EventType.INFERENCE_COMPLETED, self._types())
        event = self.events[-1]
        self.assertEqual(event.payload["episode_ids"], ["ep_evt_001"])

    def test_replicate_emits_action_proposed(self):
        state = self._state()
        state.learn([_episode()])
        self.events.clear()
        state.replicate("push my code", force_offline=True)
        self.assertIn(EventType.ACTION_PROPOSED, self._types())
        self.assertIn("push my code", self.events[-1].payload["task"])

    def test_observe_emits_git_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _seed_repo(tmp)
            state = self._state()
            state.observe(repo, [repo], max_commits=5)
        self.assertIn(EventType.GIT_CHANGED, self._types())
        self.assertGreaterEqual(self.events[-1].payload["count"], 1)

    def test_cli_watch_once_emits_git_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _seed_repo(tmp)
            bus = reset_event_bus()
            seen: list[Event] = []
            bus.subscribe(EventType.GIT_CHANGED, seen.append)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    code = main(["watch", "--repo", repo, "--once"])
            finally:
                reset_event_bus()
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].payload["repo"], repo)

    def test_actuator_trip_emits_e_stop(self):
        actuator = DesktopActuator(
            None,
            mouse=_FakeMouse(),  # type: ignore[arg-type]
            keyboard=_FakeKeyboard(),  # type: ignore[arg-type]
            failsafe=_FakeFailsafe(),  # type: ignore[arg-type]
            event_bus=self.bus,
        )
        try:
            actuator.abort()
        finally:
            actuator.close()
        self.assertIn(EventType.E_STOP_TRIGGERED, self._types())

    def test_actuator_without_bus_still_trips(self):
        actuator = DesktopActuator(
            None,
            mouse=_FakeMouse(),  # type: ignore[arg-type]
            keyboard=_FakeKeyboard(),  # type: ignore[arg-type]
            failsafe=_FakeFailsafe(),  # type: ignore[arg-type]
        )
        try:
            actuator.abort()
            self.assertTrue(actuator.interrupted)
        finally:
            actuator.close()
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
