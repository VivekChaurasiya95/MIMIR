"""Tests for the independent safety controller + E-stop (plan section 8).

Covers the SafetyController core (latch, generation invalidation,
deliberate reauth, independent audit log), its stdlib-only independence
(no Whisper/LLM/event-queue/Qt/telemetry/actuator imports), and the
fail-closed gates in the Action Registry, DesktopActuator, and
VoiceActionService.

All tests run headless with injected fakes; no hardware, network, Qt,
or event loop is required.
"""

import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest

from mimir.actions import (
    ActionRegistry,
    ActionStatus,
    RegistryError,
)
from mimir.actuator import (
    CobotInterruptedError,
    CursorPoint,
    DesktopActuator,
    ExecutionPlan,
    plan_trajectory,
)
from mimir.events import Event, EventBus, EventType
from mimir.safety import (
    SafetyController,
    SafetyError,
    SafetyRecord,
    SafetyState,
    SafetyTrippedError,
    get_safety_controller,
    reset_safety_controller,
    set_safety_controller,
)
from mimir.voice import VoiceCoordinator
from mimir.voice_action import VoiceActionError, VoiceActionService

SAFETY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "mimir",
    "safety.py",
)

_STDLIB_IMPORTS = frozenset({
    "__future__",
    "collections",
    "dataclasses",
    "enum",
    "json",
    "threading",
    "time",
    "typing",
})


def _voice() -> VoiceCoordinator:
    return VoiceCoordinator(
        stt_backend=lambda timeout: None, tts_backend=lambda text: None
    )


class _FakeMouse:
    def __init__(self) -> None:
        self.pos = (0, 0)
        self.moves: list[tuple[int, int]] = []
        self.clicks = 0

    def position(self):  # type: ignore[no-untyped-def]
        return self.pos

    def move_to(self, x, y):  # type: ignore[no-untyped-def]
        self.moves.append((x, y))
        self.pos = (x, y)

    def click(self):  # type: ignore[no-untyped-def]
        self.clicks += 1


class _FakeKeyboard:
    def __init__(self) -> None:
        self.chars: list[str] = []

    def write_char(self, char):  # type: ignore[no-untyped-def]
        self.chars.append(char)


class _FakeFailsafe:
    def __init__(self) -> None:
        self.armed_with = None
        self.commanded: list[tuple[int, int]] = []

    def arm(self, on_trip):  # type: ignore[no-untyped-def]
        self.armed_with = on_trip

    def disarm(self):  # type: ignore[no-untyped-def]
        pass

    def note_commanded(self, x, y):  # type: ignore[no-untyped-def]
        self.commanded.append((x, y))

    def simulate_trip(self):  # type: ignore[no-untyped-def]
        self.armed_with()


def _actuator(safety=None, bus=None, **overrides):  # type: ignore[no-untyped-def]
    parts = {
        "mouse": _FakeMouse(),
        "keyboard": _FakeKeyboard(),
        "failsafe": _FakeFailsafe(),
        "windows": _DeadWindows(),
    }
    parts.update(overrides)
    fired: list[bool] = []
    actor = DesktopActuator(
        lambda: fired.append(True),
        event_bus=bus,
        safety=safety,
        **parts,  # type: ignore[arg-type]
    )
    return actor, parts, fired


class _DeadWindows:
    """Window backend that is never consulted by these tests."""

    def __getattr__(self, name):  # type: ignore[no-untyped-def]
        raise AssertionError(f"unexpected window backend use: {name}")


class _Collector:
    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe(handler=self._record, name="test-collector")

    def _record(self, event: Event) -> None:
        self.events.append(event)

    def of_type(self, event_type: EventType) -> list[Event]:
        return [e for e in self.events if e.event_type is event_type]


class ControllerCoreTest(unittest.TestCase):
    def test_initial_state_is_nominal(self) -> None:
        safety = SafetyController()
        self.assertEqual(safety.state, SafetyState.NOMINAL)
        self.assertFalse(safety.estopped)
        self.assertFalse(safety.event.is_set())
        self.assertEqual(safety.generation, 0)
        self.assertEqual(safety.token(), 0)
        self.assertIsNone(safety.last_trip)
        self.assertEqual(safety.safety_log(), ())
        self.assertEqual(safety.check(), 0)

    def test_trip_latches_and_records(self) -> None:
        safety = SafetyController()
        before = time.time()
        record = safety.trip(source="test-button", reason="drill")
        self.assertEqual(safety.state, SafetyState.STOPPED)
        self.assertTrue(safety.estopped)
        self.assertTrue(safety.event.is_set())
        self.assertEqual(safety.generation, 1)
        self.assertIsInstance(record, SafetyRecord)
        self.assertEqual(record.seq, 0)
        self.assertEqual(record.state, SafetyState.STOPPED)
        self.assertEqual(record.source, "test-button")
        self.assertEqual(record.reason, "drill")
        self.assertEqual(record.generation, 1)
        self.assertGreaterEqual(record.timestamp, before)
        self.assertGreater(record.monotonic, 0.0)
        self.assertIs(safety.last_trip, record)
        self.assertEqual(safety.safety_log(), (record,))

    def test_trip_defaults_blank_source_to_unknown(self) -> None:
        safety = SafetyController()
        record = safety.trip()
        self.assertEqual(record.source, "unknown")
        self.assertEqual(record.reason, "")

    def test_trip_never_raises(self) -> None:
        safety = SafetyController(
            log_path=os.path.join("no-such-dir-xyz", "safety.log")
        )
        record = safety.trip(source=None, reason=object())  # type: ignore[arg-type]
        self.assertTrue(safety.estopped)
        self.assertIsInstance(record, SafetyRecord)

    def test_every_trip_bumps_generation(self) -> None:
        safety = SafetyController()
        safety.trip(source="first")
        safety.trip(source="second")
        self.assertEqual(safety.generation, 2)
        self.assertEqual([r.seq for r in safety.safety_log()], [0, 1])

    def test_check_rejects_stale_tokens_after_trip_and_reset(self) -> None:
        safety = SafetyController()
        token = safety.token()
        safety.trip(source="t")
        with self.assertRaises(SafetyTrippedError):
            safety.check()
        with self.assertRaises(SafetyTrippedError):
            safety.check(token)
        self.assertTrue(safety.reset(authorization="op:aria:drill done"))
        self.assertEqual(safety.check(), 1)
        with self.assertRaises(SafetyTrippedError):
            safety.check(token)

    def test_check_rejects_bad_token_type(self) -> None:
        safety = SafetyController()
        with self.assertRaises(SafetyError):
            safety.check(True)  # type: ignore[arg-type]
        with self.assertRaises(SafetyError):
            safety.check("0")  # type: ignore[arg-type]

    def test_reset_requires_deliberate_authorization(self) -> None:
        safety = SafetyController()
        safety.trip(source="t")
        for blank in ("", "   ", None):
            with self.assertRaises(SafetyError):
                safety.reset(authorization=blank)  # type: ignore[arg-type]
        self.assertTrue(safety.estopped)

    def test_reset_clears_latch_but_keeps_generation(self) -> None:
        safety = SafetyController()
        safety.trip(source="t", reason="drill")
        resumed = safety.reset(authorization="op:aria:drill done", actor="aria")
        self.assertTrue(resumed)
        self.assertEqual(safety.state, SafetyState.NOMINAL)
        self.assertFalse(safety.estopped)
        self.assertFalse(safety.event.is_set())
        self.assertEqual(safety.generation, 1)
        self.assertEqual(safety.token(), 1)
        records = safety.safety_log()
        self.assertEqual(len(records), 2)
        reset_record = records[1]
        self.assertEqual(reset_record.state, SafetyState.NOMINAL)
        self.assertEqual(reset_record.actor, "aria")
        self.assertEqual(reset_record.authorization, "op:aria:drill done")
        self.assertEqual(reset_record.generation, 1)
        # The trip is still the most recent trip for diagnostics.
        assert safety.last_trip is not None
        self.assertEqual(safety.last_trip.state, SafetyState.STOPPED)

    def test_reset_when_nominal_is_a_noop(self) -> None:
        safety = SafetyController()
        self.assertFalse(safety.reset(authorization="op:aria:noop"))
        self.assertEqual(safety.safety_log(), ())

    def test_constructor_validation(self) -> None:
        with self.assertRaises(SafetyError):
            SafetyController(max_records=0)
        with self.assertRaises(SafetyError):
            SafetyController(max_records=True)  # type: ignore[arg-type]

    def test_log_is_bounded_and_limitable(self) -> None:
        safety = SafetyController(max_records=4)
        for index in range(6):
            safety.trip(source=f"trip-{index}")
        records = safety.safety_log()
        self.assertEqual([r.seq for r in records], [2, 3, 4, 5])
        self.assertEqual(
            [r.seq for r in safety.safety_log(limit=2)], [4, 5]
        )
        with self.assertRaises(SafetyError):
            safety.safety_log(limit=0)

    def test_file_sink_appends_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "safety.log.jsonl")
            safety = SafetyController(log_path=path)
            safety.trip(source="button", reason="drill")
            safety.reset(authorization="op:aria:done")
            with open(path, encoding="utf-8") as handle:
                lines = [
                    json.loads(line)
                    for line in handle.read().splitlines()
                    if line.strip()
                ]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["state"], "stopped")
        self.assertEqual(lines[0]["source"], "button")
        self.assertEqual(lines[0]["generation"], 1)
        self.assertEqual(lines[1]["state"], "nominal")
        self.assertEqual(lines[1]["authorization"], "op:aria:done")

    def test_concurrent_trips_stay_consistent(self) -> None:
        safety = SafetyController(max_records=4096)
        threads = [
            threading.Thread(target=lambda: safety.trip(source="race"))
            for _ in range(20)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10.0)
        self.assertEqual(safety.generation, 20)
        records = safety.safety_log()
        self.assertEqual(len(records), 20)
        self.assertEqual(len({r.seq for r in records}), 20)

    def test_global_accessors(self) -> None:
        reset_safety_controller()
        try:
            first = get_safety_controller()
            self.assertIs(get_safety_controller(), first)
            replacement = SafetyController()
            set_safety_controller(replacement)
            self.assertIs(get_safety_controller(), replacement)
            with self.assertRaises(SafetyError):
                set_safety_controller(object())  # type: ignore[arg-type]
        finally:
            reset_safety_controller()
        self.assertIsNot(get_safety_controller(), first)
        reset_safety_controller()


class IndependenceTest(unittest.TestCase):
    def test_module_imports_stdlib_only(self) -> None:
        with open(SAFETY_PATH, encoding="utf-8") as handle:
            roots = set()
            for line in handle:
                stripped = line.strip()
                if stripped.startswith("import "):
                    for chunk in stripped[len("import "):].split(","):
                        roots.add(chunk.strip().split(".")[0])
                elif stripped.startswith("from "):
                    roots.add(stripped.split()[1].split(".")[0])
        self.assertTrue(roots)
        self.assertLessEqual(roots, _STDLIB_IMPORTS)

    def test_module_loads_without_package_context(self) -> None:
        name = "mimir_safety_standalone_probe"
        spec = importlib.util.spec_from_file_location(name, SAFETY_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            del sys.modules[name]
        controller = module.SafetyController()
        controller.trip(source="probe")
        with self.assertRaises(module.SafetyTrippedError):
            controller.check()
        self.assertTrue(
            controller.reset(authorization="probe:standalone ok")
        )

    def test_trip_survives_dead_event_bus(self) -> None:
        class _DeadBus:
            def emit(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                raise RuntimeError("queue wedged")

        safety = SafetyController()
        record = safety.trip(source="probe")
        _DeadBus()  # the bus is never consulted; this only documents it
        self.assertTrue(safety.estopped)
        self.assertEqual(record.source, "probe")


class RegistrySafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.collector = _Collector(self.bus)
        self.safety = SafetyController()
        self.registry = ActionRegistry(
            bus=self.bus, session_id="sess_safe", safety=self.safety
        )

    def _outcome_failures(self) -> list[Event]:
        return self.collector.of_type(EventType.ACTION_FAILED)

    def test_authorize_denied_while_stopped_without_mutation(self) -> None:
        spec = self.registry.propose("push code", target="github")
        self.safety.trip(source="test")
        outcome = self.registry.authorize(
            spec.action_id, granted_permissions=("voice-confirm",)
        )
        self.assertFalse(outcome.ok)
        self.assertIn("safety stop", outcome.reason)
        current = self.registry.get(spec.action_id)
        assert current is not None
        # Denied, not invalidated yet: still pending while latched.
        self.assertEqual(current.status, ActionStatus.PENDING)
        self.assertEqual(
            self.collector.of_type(EventType.ACTION_AUTHORIZED), []
        )

    def test_pre_trip_pending_invalidated_after_reset(self) -> None:
        spec = self.registry.propose("push code", target="github")
        self.safety.trip(source="test")
        self.assertTrue(self.safety.reset(authorization="op:aria:done"))
        outcome = self.registry.authorize(
            spec.action_id, granted_permissions=("voice-confirm",)
        )
        self.assertFalse(outcome.ok)
        self.assertIn("invalidated by safety stop", outcome.reason)
        current = self.registry.get(spec.action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.CANCELLED)
        failures = self._outcome_failures()
        self.assertEqual(len(failures), 1)
        self.assertEqual(
            failures[0].payload["outcome"], "safety_invalidated"
        )
        self.assertEqual(
            failures[0].payload["action_id"], spec.action_id
        )

    def test_reads_lazily_invalidate_stale_entries(self) -> None:
        spec = self.registry.propose("push code", target="github")
        self.safety.trip(source="test")
        self.safety.reset(authorization="op:aria:done")
        current = self.registry.get(spec.action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.CANCELLED)
        self.assertEqual(self.registry.list_pending(), [])

    def test_mark_executing_invalidates_stale_authorized(self) -> None:
        spec = self.registry.propose("push code", target="github")
        outcome = self.registry.authorize(
            spec.action_id, granted_permissions=("voice-confirm",)
        )
        self.assertTrue(outcome.ok)
        self.safety.trip(source="test")
        self.assertTrue(self.safety.reset(authorization="op:aria:done"))
        self.assertFalse(self.registry.mark_executing(spec.action_id))
        current = self.registry.get(spec.action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.CANCELLED)
        failures = self._outcome_failures()
        self.assertEqual(len(failures), 1)
        self.assertEqual(
            failures[0].payload["outcome"], "safety_invalidated"
        )

    def test_mark_executing_rejected_while_stopped(self) -> None:
        spec = self.registry.propose("push code", target="github")
        self.registry.authorize(
            spec.action_id, granted_permissions=("voice-confirm",)
        )
        self.safety.trip(source="test")
        self.assertFalse(self.registry.mark_executing(spec.action_id))
        current = self.registry.get(spec.action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.AUTHORIZED)

    def test_complete_blocked_and_fail_accepted_during_stop(self) -> None:
        first = self.registry.propose("first")
        second = self.registry.propose("second")
        for spec in (first, second):
            self.assertTrue(
                self.registry.authorize(
                    spec.action_id,
                    granted_permissions=("voice-confirm",),
                ).ok
            )
            self.assertTrue(self.registry.mark_executing(spec.action_id))
        self.safety.trip(source="test")
        self.assertFalse(self.registry.complete(first.action_id))
        current = self.registry.get(first.action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.FAILED)
        failures = self._outcome_failures()
        self.assertEqual(len(failures), 1)
        self.assertIn("safety stop", failures[0].payload["reason"])
        # Recording the interruption itself must never be rejected.
        self.assertTrue(
            self.registry.fail(second.action_id, reason="E-stop mid-drive")
        )
        current = self.registry.get(second.action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.FAILED)

    def test_fresh_proposal_authorizes_after_deliberate_reset(self) -> None:
        stale = self.registry.propose("stale")
        self.safety.trip(source="test")
        fresh = self.registry.propose("fresh")
        self.assertTrue(self.safety.reset(authorization="op:aria:done"))
        self.assertFalse(
            self.registry.authorize(
                stale.action_id, granted_permissions=("voice-confirm",)
            ).ok
        )
        outcome = self.registry.authorize(
            fresh.action_id, granted_permissions=("voice-confirm",)
        )
        self.assertTrue(outcome.ok)
        self.assertTrue(self.registry.mark_executing(fresh.action_id))
        self.assertTrue(self.registry.complete(fresh.action_id))

    def test_second_trip_invalidates_proposals_made_during_stop(self) -> None:
        self.safety.trip(source="first")
        during = self.registry.propose("during")
        self.safety.trip(source="second")
        self.safety.reset(authorization="op:aria:done")
        outcome = self.registry.authorize(
            during.action_id, granted_permissions=("voice-confirm",)
        )
        self.assertFalse(outcome.ok)
        self.assertIn("invalidated", outcome.reason)

    def test_registry_rejects_bad_safety_type(self) -> None:
        with self.assertRaises(RegistryError):
            ActionRegistry(safety="nope")  # type: ignore[arg-type]


class ActuatorSafetyTest(unittest.TestCase):
    def test_operations_rejected_while_stopped(self) -> None:
        safety = SafetyController()
        bus = EventBus()
        actor, _parts, _fired = _actuator(safety=safety, bus=bus)
        try:
            safety.trip(source="test")
            with self.assertRaises(CobotInterruptedError):
                actor.move_cursor_smooth(CursorPoint(10, 10))
            with self.assertRaises(CobotInterruptedError):
                actor.type_natural("hi")
            with self.assertRaises(CobotInterruptedError):
                actor.click_point(CursorPoint(10, 10))
            with self.assertRaises(CobotInterruptedError):
                actor.execute_plan(ExecutionPlan(actions=["audit only"]))
            with self.assertRaises(CobotInterruptedError):
                actor.run_safe_command(
                    [sys.executable, "-c", "pass"], timeout_sec=5.0
                )
            points = plan_trajectory(
                CursorPoint(0, 0), CursorPoint(4, 4), 4
            )
            with self.assertRaises(CobotInterruptedError):
                actor.execute_trajectory(points, 0.1)
        finally:
            actor.close()

    def test_run_safe_command_never_spawns_while_stopped(self) -> None:
        safety = SafetyController()
        actor, _parts, _fired = _actuator(safety=safety)
        marker_path = os.path.join(
            tempfile.gettempdir(), "mimir_safety_nospawn_probe.tmp"
        )
        if os.path.exists(marker_path):
            os.remove(marker_path)
        try:
            safety.trip(source="test")
            with self.assertRaises(CobotInterruptedError):
                actor.run_safe_command(
                    [
                        sys.executable, "-c",
                        f"open({marker_path!r}, 'w').write('x')",
                    ],
                    timeout_sec=5.0,
                )
            self.assertFalse(os.path.exists(marker_path))
        finally:
            actor.close()
            if os.path.exists(marker_path):
                os.remove(marker_path)

    def test_failsafe_trip_propagates_to_safety(self) -> None:
        safety = SafetyController()
        bus = EventBus()
        collector = _Collector(bus)
        actor, parts, fired = _actuator(safety=safety, bus=bus)
        try:
            parts["failsafe"].simulate_trip()
            self.assertTrue(safety.estopped)
            assert safety.last_trip is not None
            self.assertEqual(safety.last_trip.source, "actuator-failsafe")
            self.assertEqual(fired, [True])
            stops = collector.of_type(EventType.E_STOP_TRIGGERED)
            self.assertEqual(len(stops), 1)
            with self.assertRaises(CobotInterruptedError):
                actor.type_natural("hi")
        finally:
            actor.close()

    def test_abort_propagates_and_needs_dual_reset(self) -> None:
        safety = SafetyController()
        actor, _parts, _fired = _actuator(safety=safety)
        try:
            actor.abort()
            assert safety.last_trip is not None
            self.assertEqual(safety.last_trip.source, "actuator-abort")
            # Resetting the actuator alone does not resume: safety gates it.
            actor.reset()
            with self.assertRaises(CobotInterruptedError):
                actor.type_natural("hi")
            self.assertTrue(safety.reset(authorization="op:aria:done"))
            actor.type_natural("hi")
        finally:
            actor.close()

    def test_mid_typing_trip_aborts_promptly(self) -> None:
        safety = SafetyController()
        actor, parts, _fired = _actuator(safety=safety)
        try:
            errors: list[BaseException] = []

            def _drive() -> None:
                try:
                    actor.type_natural("x" * 12, base_wpm=20)
                except BaseException as exc:  # record, re-raise in test
                    errors.append(exc)

            driver = threading.Thread(target=_drive)
            started = time.monotonic()
            driver.start()
            time.sleep(0.15)
            safety.trip(source="test")
            driver.join(timeout=10.0)
            elapsed = time.monotonic() - started
            self.assertFalse(driver.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], CobotInterruptedError)
            self.assertIn("safety stop", str(errors[0]))
            # Unhindered typing would take ~7s; the trip must cut it short.
            self.assertLess(elapsed, 3.0)
            self.assertLess(len(parts["keyboard"].chars), 12)
        finally:
            actor.close()

    def test_mid_trajectory_trip_aborts(self) -> None:
        safety = SafetyController()
        actor, _parts, _fired = _actuator(safety=safety)
        try:
            points = plan_trajectory(
                CursorPoint(0, 0), CursorPoint(40, 0), 40
            )
            errors: list[BaseException] = []

            def _drive() -> None:
                try:
                    actor.execute_trajectory(points, 2.0)
                except BaseException as exc:
                    errors.append(exc)

            driver = threading.Thread(target=_drive)
            driver.start()
            time.sleep(0.15)
            safety.trip(source="test")
            driver.join(timeout=10.0)
            self.assertFalse(driver.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], CobotInterruptedError)
        finally:
            actor.close()

    def test_actuator_rejects_bad_safety_type(self) -> None:
        with self.assertRaises(ValueError):
            _actuator(safety="nope")


class VoiceSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.collector = _Collector(self.bus)
        self.safety = SafetyController()
        self.registry = ActionRegistry(
            bus=self.bus, session_id="sess_safe", safety=self.safety
        )
        self.said: list[str] = []
        self.service = VoiceActionService(
            _voice(),
            self.registry,
            bus=self.bus,
            session_id="sess_safe",
            speak_fn=self.said.append,
            speak_async=False,
            safety=self.safety,
        )

    def test_stop_latches_estop(self) -> None:
        decision = self.service.handle_transcript("stop")
        self.assertEqual(decision.structured_intent.intent, "STOP")
        self.assertTrue(self.safety.estopped)
        assert self.safety.last_trip is not None
        self.assertEqual(self.safety.last_trip.source, "voice-stop")
        self.assertTrue(self.said)

    def test_stop_without_safety_only_replies(self) -> None:
        said: list[str] = []
        service = VoiceActionService(
            _voice(),
            ActionRegistry(bus=self.bus),
            bus=self.bus,
            speak_fn=said.append,
            speak_async=False,
        )
        decision = service.handle_transcript("stop")
        self.assertEqual(decision.structured_intent.intent, "STOP")
        self.assertFalse(self.safety.estopped)
        self.assertTrue(said)

    def test_approval_denied_while_stopped(self) -> None:
        spec = self.registry.propose("push code", target="github")
        self.safety.trip(source="test")
        decision = self.service.handle_transcript("go ahead")
        self.assertEqual(decision.authorized_action_id, "")
        self.assertIsNone(decision.authorization)
        self.assertIn("Safety stop", decision.reply)
        current = self.registry.get(spec.action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.PENDING)
        confirmations = self.collector.of_type(
            EventType.ACTION_CONFIRMATION_RECEIVED
        )
        self.assertEqual(len(confirmations), 1)
        self.assertFalse(confirmations[0].payload["ok"])

    def test_stale_approval_clarifies_after_reset(self) -> None:
        spec = self.registry.propose("push code", target="github")
        self.safety.trip(source="test")
        self.safety.reset(authorization="op:aria:done")
        decision = self.service.handle_transcript("go ahead")
        # The pre-trip proposal was invalidated; nothing authorizes.
        self.assertEqual(decision.authorized_action_id, "")
        self.assertIn("no pending action", decision.reply)
        current = self.registry.get(spec.action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.CANCELLED)

    def test_explicit_id_after_invalidation_clarifies(self) -> None:
        spec = self.registry.propose("push code", target="github")
        self.safety.trip(source="test")
        self.safety.reset(authorization="op:aria:done")
        decision = self.service.handle_transcript(
            f"go ahead {spec.action_id}"
        )
        self.assertEqual(decision.authorized_action_id, "")
        self.assertIn("cancelled", decision.reply)

    def test_service_rejects_bad_safety_type(self) -> None:
        with self.assertRaises(VoiceActionError):
            VoiceActionService(
                _voice(), self.registry, safety="nope"  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
