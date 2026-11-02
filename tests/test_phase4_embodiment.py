"""Tests for Phase 4 bounded embodiment (mimir.embodiment).

Headless throughout: fake window/mouse/keyboard/failsafe backends, a
scripted ESC listener, temp dirs for the file tree, and injected test
runners + git readers. No hardware, network, Qt, or event loop needed.
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from mimir.actuator import (
    ActuationGateway,
    CobotInterruptedError,
    CursorPoint,
    DesktopActuator,
    EmbodimentError,
    ExecutionPlan,
    FocusLostError,
    OutcomeVerifier,
    TargetValidationError,
    TripleFailsafe,
)
from mimir.desktop import TargetSpec, WindowInfo
from mimir.embodiment import (
    EscHotkeyLatch,
    EstopTrip,
    FileSnapshot,
    RollbackReport,
    SPOKEN_STOP_PHRASES,
    VerificationReport,
    is_spoken_stop,
)
from mimir.events import Event, EventBus, EventType
from mimir.safety import SafetyController, SafetyTrippedError
from mimir.telemetry import GitState


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeMouse:
    def __init__(self, start=(0, 0)):
        self.pos = start
        self.moves: list[tuple[int, int]] = []
        self.clicks = 0

    def position(self):
        return self.pos

    def move_to(self, x, y):
        self.moves.append((x, y))
        self.pos = (x, y)

    def click(self):
        self.clicks += 1


class FakeKeyboard:
    def __init__(self):
        self.chars: list[str] = []

    def write_char(self, char):
        self.chars.append(char)


class FakeFailsafe:
    def __init__(self, fail_arm=False):
        self.armed_with = None
        self.disarmed = 0
        self.fail_arm = fail_arm

    def arm(self, on_trip):
        if self.fail_arm:
            raise RuntimeError("no listener backend")
        self.armed_with = on_trip

    def disarm(self):
        self.disarmed += 1

    def simulate_trip(self):
        self.armed_with()


class FakeWindows:
    """Scriptable WindowBackend (foreground queue overrides live state)."""

    def __init__(self, windows, foreground_handle=None, activate_ok=True):
        self._windows = {w.handle: w for w in windows}
        self._fg = foreground_handle
        self.activate_calls: list[int] = []
        self.foreground_queue: list = []
        if callable(activate_ok):
            self._accept = activate_ok
        else:
            self._accept = lambda h: bool(activate_ok)

    def list_windows(self):
        return [self._windows[h] for h in sorted(self._windows)]

    def foreground(self):
        if self.foreground_queue:
            handle = self.foreground_queue.pop(0)
            if handle is None:
                return None
            return self._windows.get(handle)
        if self._fg is None:
            return None
        return self._windows.get(self._fg)

    def is_valid(self, handle):
        return handle in self._windows

    def activate(self, handle):
        self.activate_calls.append(handle)
        if handle not in self._windows:
            return False
        if not self._accept(handle):
            return False
        self._fg = handle
        return True

    def close(self, handle):
        self._windows.pop(handle, None)
        if self._fg == handle:
            self._fg = None

    def retarget(self, handle, **fields):
        old = self._windows[handle]
        merged = dict(old.to_dict())
        merged.update(fields)
        self._windows[handle] = WindowInfo.from_dict(merged)


class FakeEscListener:
    def __init__(self, on_press):
        self.on_press = on_press
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def press(self, key):
        self.on_press(key)


class FakeEscFactory:
    def __init__(self):
        self.listeners: list[FakeEscListener] = []

    def __call__(self, on_press):
        listener = FakeEscListener(on_press)
        self.listeners.append(listener)
        return listener

    @property
    def last(self):
        return self.listeners[-1]


class Collector:
    def __init__(self, bus: EventBus):
        self.events: list[Event] = []
        bus.subscribe(handler=self._record, name="test-collector")

    def _record(self, event: Event):
        self.events.append(event)

    def of_type(self, event_type: EventType):
        return [e for e in self.events if e.event_type is event_type]


def _windows():
    return [
        WindowInfo(handle=11, process_id=100,
                   executable="C:\\Apps\\Companion.exe", title="MIMIR"),
        WindowInfo(handle=22, process_id=200,
                   executable="C:\\Apps\\Editor.exe", title="notes.txt"),
        WindowInfo(handle=33, process_id=300,
                   executable="C:\\Apps\\Browser.exe", title="Docs"),
    ]


def _actuator(windows=None, **overrides):
    parts = {"mouse": FakeMouse(), "keyboard": FakeKeyboard(),
             "failsafe": FakeFailsafe(),
             "windows": windows if windows is not None else FakeWindows(
                 _windows(), foreground_handle=11)}
    parts.update(overrides)
    actor = DesktopActuator(**parts)
    return actor, parts


def _clean_git_state(root):
    return GitState(repo=root, branch="main", head="abc123",
                    staged=(), unstaged=(), untracked=(),
                    staged_count=0, unstaged_count=0, untracked_count=0,
                    dirty=False)


def _dirty_git_state(root):
    return GitState(repo=root, branch="main", head="abc123",
                    staged=("a.py",), unstaged=(), untracked=(),
                    staged_count=1, unstaged_count=0, untracked_count=0,
                    dirty=True)


# ---------------------------------------------------------------------------
# ActuationGateway
# ---------------------------------------------------------------------------


class GatewayContractTest(unittest.TestCase):
    def test_rejects_bad_construction(self):
        actor, _ = _actuator()
        try:
            with self.assertRaises(ValueError):
                ActuationGateway("nope", TargetSpec(handle=22))
            with self.assertRaises(ValueError):
                ActuationGateway(actor, None)
            with self.assertRaises(ValueError):
                ActuationGateway(actor, "  ")
            with self.assertRaises(ValueError):
                ActuationGateway(actor, TargetSpec(handle=22),
                                 safety="nope")
            with self.assertRaises(ValueError):
                ActuationGateway(actor, TargetSpec(handle=22),
                                 focus_poll_sec=0)
            with self.assertRaises(ValueError):
                ActuationGateway(actor, TargetSpec(handle=22),
                                 pause_timeout_sec=-1)
            with self.assertRaises(ValueError):
                ActuationGateway(actor, TargetSpec(handle=22),
                                 max_pauses=-1)
            with self.assertRaises(ValueError):
                ActuationGateway(actor, TargetSpec(handle=22),
                                 on_pause="nope")
            with self.assertRaises(ValueError):
                ActuationGateway(actor, TargetSpec(handle=22),
                                 on_resume="nope")
        finally:
            actor.close()

    def test_rejects_empty_or_non_callable_steps(self):
        actor, _ = _actuator()
        try:
            gateway = ActuationGateway(actor, TargetSpec(handle=22))
            with self.assertRaises(ValueError):
                gateway.run_steps([])
            with self.assertRaises(ValueError):
                gateway.run_steps([lambda live: 1, "nope"])
            with self.assertRaises(ValueError):
                gateway.execute_plan("nope")
        finally:
            actor.close()


class GatewaySessionTest(unittest.TestCase):
    def test_session_focuses_target_and_restores_companion(self):
        actor, parts = _actuator()
        try:
            gateway = ActuationGateway(actor, TargetSpec(handle=22))
            with gateway.session() as live:
                self.assertEqual(live.handle, 22)
                self.assertEqual(live.process_id, 200)
                self.assertIn(22, parts["windows"].activate_calls)
            # Companion (11) restored best-effort on exit.
            self.assertEqual(parts["windows"].activate_calls[-1], 11)
        finally:
            actor.close()

    def test_session_accepts_str_and_window_info_targets(self):
        actor, _ = _actuator()
        try:
            with ActuationGateway(actor, "notes.txt").session() as live:
                self.assertEqual(live.handle, 22)
            info = WindowInfo(handle=22, process_id=200,
                              executable="C:\\Apps\\Editor.exe",
                              title="notes.txt")
            with ActuationGateway(actor, info).session() as live2:
                self.assertEqual(live2.handle, 22)
        finally:
            actor.close()

    def test_unauthorized_fails_closed_before_any_step(self):
        actor, parts = _actuator()
        try:
            gateway = ActuationGateway(actor, TargetSpec(handle=22),
                                       authorized=False)
            ran: list[str] = []
            with self.assertRaises(TargetValidationError) as ctx:
                gateway.run_steps([lambda live: ran.append("x")])
            self.assertEqual(ctx.exception.reason, "unauthorized")
            self.assertEqual(ran, [])
        finally:
            actor.close()

    def test_unknown_target_fails_closed(self):
        actor, _ = _actuator()
        try:
            gateway = ActuationGateway(actor, TargetSpec(handle=999))
            with self.assertRaises(TargetValidationError) as ctx:
                gateway.run_steps([lambda live: "x"])
            self.assertEqual(ctx.exception.reason, "unknown_handle")
        finally:
            actor.close()

    def test_ambiguous_target_fails_closed(self):
        actor, _ = _actuator()
        try:
            gateway = ActuationGateway(actor, TargetSpec(process_id=200))
            # Unique here; force ambiguity with a broad executable match.
            gateway2 = ActuationGateway(actor, TargetSpec(executable="C:\\Apps"))
            with self.assertRaises(TargetValidationError) as ctx:
                gateway2.run_steps([lambda live: "x"])
            self.assertEqual(ctx.exception.reason, "ambiguous")
        finally:
            actor.close()

    def test_safety_latch_blocks_before_any_focus(self):
        safety = SafetyController()
        safety.trip(source="test", reason="drill")
        actor, parts = _actuator()
        try:
            gateway = ActuationGateway(actor, TargetSpec(handle=22),
                                       safety=safety)
            with self.assertRaises(CobotInterruptedError):
                gateway.run_steps([lambda live: "x"])
            self.assertEqual(parts["windows"].activate_calls, [])
        finally:
            actor.close()

    def test_interrupted_actuator_blocks(self):
        actor, _ = _actuator()
        try:
            actor.abort()
            gateway = ActuationGateway(actor, TargetSpec(handle=22))
            with self.assertRaises(CobotInterruptedError):
                gateway.run_steps([lambda live: "x"])
        finally:
            actor.close()


class GatewayStepsTest(unittest.TestCase):
    def test_run_steps_returns_results_in_order(self):
        actor, _ = _actuator()
        try:
            gateway = ActuationGateway(actor, TargetSpec(handle=22))
            results = gateway.run_steps([
                lambda live: ("first", live.handle),
                lambda live: ("second", live.process_id),
            ])
            self.assertEqual(results, [("first", 22), ("second", 200)])
            self.assertFalse(gateway.paused)
            self.assertEqual(gateway.pause_count, 0)
        finally:
            actor.close()

    def test_mid_step_identity_change_fails_closed(self):
        windows = FakeWindows(_windows(), foreground_handle=11)
        actor, _ = _actuator(windows=windows)
        try:
            gateway = ActuationGateway(actor, TargetSpec(handle=22))

            def swap_pid(live):
                windows.retarget(22, process_id=999)
                return "stepped"

            with self.assertRaises(TargetValidationError) as ctx:
                gateway.run_steps([swap_pid])
            # Identity change is never a pause: it raises as-is.
            self.assertEqual(ctx.exception.reason, "target_changed")
            self.assertEqual(gateway.pause_count, 0)
        finally:
            actor.close()

    def test_focus_loss_pauses_then_resumes(self):
        windows = FakeWindows(_windows(), foreground_handle=11)
        # Reads: preserve=11, verify=22, session-validate=22,
        # pre-step=11 (loss), wait polls 11 then 22 (regain),
        # re-validate=22, post=22, restore-check=22.
        windows.foreground_queue = [11, 22, 22, 11, 11, 22, 22, 22, 22]
        actor, _ = _actuator(windows=windows)
        bus = EventBus()
        collector = Collector(bus)
        paused: list[int] = []
        resumed: list[int] = []
        try:
            gateway = ActuationGateway(
                actor, TargetSpec(handle=22), event_bus=bus,
                focus_poll_sec=0.001, pause_timeout_sec=5.0,
                on_pause=lambda live: paused.append(live.handle),
                on_resume=lambda live: resumed.append(live.handle),
            )
            results = gateway.run_steps([lambda live: "stepped"])
            self.assertEqual(results, ["stepped"])
            self.assertEqual(gateway.pause_count, 1)
            self.assertFalse(gateway.paused)
            self.assertEqual(paused, [22])
            self.assertEqual(resumed, [22])
            pauses = [e for e in collector.of_type(
                EventType.ACTUATION_CANCELLED)
                if e.payload.get("paused")]
            self.assertEqual(len(pauses), 1)
            resumes = [e for e in collector.of_type(EventType.ACTION_STARTED)
                       if e.payload.get("resumed")]
            self.assertEqual(len(resumes), 1)
        finally:
            actor.close()

    def test_pause_hooks_may_raise_without_breaking_resume(self):
        windows = FakeWindows(_windows(), foreground_handle=11)
        windows.foreground_queue = [11, 22, 22, 11, 22, 22, 22, 22]

        def _boom(live):
            raise RuntimeError("hook broken")

        actor, _ = _actuator(windows=windows)
        try:
            gateway = ActuationGateway(
                actor, TargetSpec(handle=22),
                focus_poll_sec=0.001, pause_timeout_sec=5.0,
                on_pause=_boom, on_resume=_boom,
            )
            self.assertEqual(gateway.run_steps([lambda live: 7]), [7])
            self.assertEqual(gateway.pause_count, 1)
        finally:
            actor.close()

    def test_pause_disabled_raises_focus_lost_immediately(self):
        windows = FakeWindows(_windows(), foreground_handle=11)
        windows.foreground_queue = [11, 22, 22, 11]
        actor, _ = _actuator(windows=windows)
        try:
            gateway = ActuationGateway(
                actor, TargetSpec(handle=22), pause_on_focus_loss=False,
                focus_poll_sec=0.001,
            )
            with self.assertRaises(FocusLostError) as ctx:
                gateway.run_steps([lambda live: "x"])
            self.assertEqual(ctx.exception.reason, "focus_lost")
            self.assertEqual(gateway.pause_count, 0)
        finally:
            actor.close()

    def test_focus_timeout_raises_focus_timeout(self):
        windows = FakeWindows(_windows(), foreground_handle=11)
        windows.foreground_queue = [11, 22, 22] + [11] * 5000
        actor, _ = _actuator(windows=windows)
        try:
            gateway = ActuationGateway(
                actor, TargetSpec(handle=22),
                focus_poll_sec=0.001, pause_timeout_sec=0.05,
            )
            started = time.monotonic()
            with self.assertRaises(FocusLostError) as ctx:
                gateway.run_steps([lambda live: "x"])
            self.assertEqual(ctx.exception.reason, "focus_timeout")
            self.assertLess(time.monotonic() - started, 5.0)
            self.assertEqual(gateway.pause_count, 1)
        finally:
            actor.close()

    def test_pause_budget_exhaustion_fails_closed(self):
        windows = FakeWindows(_windows(), foreground_handle=11)
        windows.foreground_queue = [11, 22, 22, 11]
        actor, _ = _actuator(windows=windows)
        try:
            gateway = ActuationGateway(
                actor, TargetSpec(handle=22), max_pauses=0,
                focus_poll_sec=0.001,
            )
            with self.assertRaises(FocusLostError) as ctx:
                gateway.run_steps([lambda live: "x"])
            self.assertEqual(ctx.exception.reason, "pause_budget_exhausted")
        finally:
            actor.close()

    def test_identity_change_during_pause_fails_closed(self):
        windows = FakeWindows(_windows(), foreground_handle=11)
        windows.foreground_queue = [11, 22, 22, 11, 22, 22, 22, 22, 22]
        actor, _ = _actuator(windows=windows)
        try:
            def swap_on_pause(live):
                windows.retarget(22, process_id=999)

            gateway = ActuationGateway(
                actor, TargetSpec(handle=22),
                focus_poll_sec=0.001, pause_timeout_sec=5.0,
                on_pause=swap_on_pause,
            )
            with self.assertRaises(TargetValidationError) as ctx:
                gateway.run_steps([lambda live: "x"])
            self.assertEqual(ctx.exception.reason, "target_changed")
        finally:
            actor.close()

    def test_abort_during_pause_raises_interrupted(self):
        windows = FakeWindows(_windows(), foreground_handle=11)
        windows.foreground_queue = [11, 22, 22, 11] + [11] * 5000
        actor, _ = _actuator(windows=windows)
        try:
            gateway = ActuationGateway(
                actor, TargetSpec(handle=22),
                focus_poll_sec=0.001, pause_timeout_sec=30.0,
            )
            timer = threading.Timer(0.05, actor.abort)
            timer.start()
            try:
                with self.assertRaises(CobotInterruptedError):
                    gateway.run_steps([lambda live: "x"])
            finally:
                timer.cancel()
        finally:
            actor.close()

    def test_focus_matches_never_raises(self):
        actor, _ = _actuator()
        try:
            gateway = ActuationGateway(actor, TargetSpec(handle=22))
            live = gateway.resolve()
            # Companion (11) owns the foreground until a session focuses.
            self.assertFalse(gateway.focus_matches(live))
            with gateway.session() as focused:
                self.assertTrue(gateway.focus_matches(focused))

            class _Broken:
                def foreground(self):
                    raise RuntimeError("no desktop")

            actor._windows = _Broken()
            self.assertFalse(gateway.focus_matches(live))
        finally:
            actor.close()


class GatewayConvenienceTest(unittest.TestCase):
    def test_move_type_click_execute_plan_through_gateway(self):
        actor, parts = _actuator()
        try:
            gateway = ActuationGateway(actor, TargetSpec(handle=22))
            gateway.move_cursor_smooth(CursorPoint(9, 4), duration_sec=0.01,
                                       steps=2)
            self.assertEqual(parts["mouse"].pos, (9, 4))
            gateway.type_natural("hi", base_wpm=100000)
            self.assertEqual(parts["keyboard"].chars, ["h", "i"])
            gateway.click_point(CursorPoint(3, 3), verify=False)
            self.assertEqual(parts["mouse"].clicks, 1)
            report = gateway.execute_plan(
                ExecutionPlan(actions=["audit only"]))
            self.assertEqual(report["actions"], ["audit only"])
            self.assertTrue(report["target_validated"])
            self.assertTrue(report["focus_verified"])
        finally:
            actor.close()

    def test_convenience_ops_inherit_gateway_safety_gate(self):
        safety = SafetyController()
        actor, parts = _actuator()
        try:
            gateway = ActuationGateway(actor, TargetSpec(handle=22),
                                       safety=safety)
            safety.trip(source="test", reason="drill")
            with self.assertRaises(CobotInterruptedError):
                gateway.click_point(CursorPoint(1, 1))
            self.assertEqual(parts["mouse"].clicks, 0)
        finally:
            actor.close()


# ---------------------------------------------------------------------------
# TripleFailsafe
# ---------------------------------------------------------------------------


def _triple(safety=None, bus=None, **overrides):
    parts = {"physical": FakeFailsafe(),
             "esc_listener": EscHotkeyLatch(
                 listener_factory=FakeEscFactory())}
    parts.update(overrides)
    failsafe = TripleFailsafe(safety=safety, event_bus=bus, **parts)
    return failsafe, parts


class SpokenStopTest(unittest.TestCase):
    def test_every_stop_phrase_trips(self):
        for phrase in SPOKEN_STOP_PHRASES:
            self.assertTrue(is_spoken_stop(phrase), phrase)
            self.assertTrue(
                is_spoken_stop(f"hey cobot, {phrase} right now"), phrase
            )
            self.assertTrue(is_spoken_stop(phrase.upper()), phrase)

    def test_non_stop_text_and_garbage_never_trip(self):
        self.assertFalse(is_spoken_stop("go ahead and push my code"))
        self.assertFalse(is_spoken_stop("what is the status"))
        self.assertFalse(is_spoken_stop(""))
        self.assertFalse(is_spoken_stop("   "))
        self.assertFalse(is_spoken_stop(None))
        self.assertFalse(is_spoken_stop(123))
        self.assertFalse(is_spoken_stop(["stop"]))


class TripleContractTest(unittest.TestCase):
    def test_rejects_bad_construction(self):
        with self.assertRaises(ValueError):
            TripleFailsafe(safety="nope")
        with self.assertRaises(ValueError):
            TripleFailsafe(actuator="nope")
        with self.assertRaises(ValueError):
            TripleFailsafe(on_trip="nope")
        with self.assertRaises(ValueError):
            EscHotkeyLatch(listener_factory="nope")
        latch = EscHotkeyLatch(listener_factory=FakeEscFactory())
        try:
            with self.assertRaises(ValueError):
                latch.arm("nope")
        finally:
            latch.disarm()

    def test_unknown_channel_is_programmer_error(self):
        failsafe, _ = _triple()
        try:
            with self.assertRaises(ValueError):
                failsafe.trip("telepathy")
        finally:
            failsafe.close()

    def test_reset_requires_deliberate_authorization(self):
        failsafe, _ = _triple()
        try:
            failsafe.trip("physical", "drill")
            with self.assertRaises(EmbodimentError):
                failsafe.reset(authorization="  ")
            self.assertTrue(failsafe.latched)
            self.assertTrue(failsafe.reset(authorization="op: drill over"))
            self.assertFalse(failsafe.latched)
            self.assertFalse(failsafe.reset(authorization="op: noop"))
        finally:
            failsafe.close()


class TripleChannelsTest(unittest.TestCase):
    def test_all_channels_armed_with_fakes(self):
        failsafe, _ = _triple()
        try:
            self.assertEqual(
                failsafe.armed_channels, ("physical", "esc", "spoken")
            )
            self.assertFalse(failsafe.latched)
            self.assertEqual(failsafe.tripped_channels, ())
            self.assertIsNone(failsafe.last_trip)
            self.assertIsNone(failsafe.trip_latency_ms("physical"))
            self.assertIsNone(failsafe.within_budget("esc"))
        finally:
            failsafe.close()

    def test_physical_trip_latches_safety_and_aborts_actuator(self):
        safety = SafetyController()
        actor, _ = _actuator()
        bus = EventBus()
        collector = Collector(bus)
        failsafe, parts = _triple(safety=safety, bus=bus, actuator=actor)
        try:
            parts["physical"].simulate_trip()
            self.assertTrue(failsafe.latched)
            self.assertEqual(failsafe.tripped_channels, ("physical",))
            self.assertTrue(safety.estopped)
            self.assertEqual(safety.last_trip.source, "estop-physical")
            self.assertTrue(actor.interrupted)
            last = failsafe.last_trip
            self.assertIsInstance(last, EstopTrip)
            self.assertEqual(last.channel, "physical")
            self.assertLess(last.latency_ms, 50.0)
            self.assertTrue(failsafe.within_budget("physical"))
            stops = collector.of_type(EventType.E_STOP_TRIGGERED)
            self.assertEqual(len(stops), 1)
            self.assertEqual(stops[0].payload["channel"], "physical")
            self.assertTrue(stops[0].payload["within_budget_ms"])
        finally:
            failsafe.close()
            actor.close()

    def test_esc_trip_fires_only_on_esc(self):
        safety = SafetyController()
        failsafe, parts = _triple(safety=safety)
        try:
            factory = parts["esc_listener"]._factory
            listener = factory.last
            self.assertTrue(listener.started)
            listener.press("a")
            listener.press("enter")
            self.assertFalse(failsafe.latched)
            listener.press("esc")
            self.assertEqual(failsafe.tripped_channels, ("esc",))
            self.assertTrue(safety.estopped)
            self.assertEqual(safety.last_trip.source, "estop-esc")
            # Latch fires once: repeat ESC does not double-record.
            listener.press("esc")
            listener.press("Key.ESC")
            self.assertEqual(failsafe.tripped_channels, ("esc",))
        finally:
            failsafe.close()
            self.assertTrue(factory.last.stopped)

    def test_spoken_transcript_trips_and_non_stop_ignores(self):
        safety = SafetyController()
        failsafe, _ = _triple(safety=safety)
        try:
            self.assertFalse(failsafe.notify_transcript("go ahead"))
            self.assertFalse(failsafe.notify_transcript(""))
            self.assertFalse(failsafe.notify_transcript(None))
            self.assertFalse(failsafe.latched)
            self.assertTrue(
                failsafe.notify_transcript("please STOP right now")
            )
            self.assertEqual(failsafe.tripped_channels, ("spoken",))
            self.assertTrue(safety.estopped)
            self.assertTrue(failsafe.within_budget("spoken"))
        finally:
            failsafe.close()

    def test_structured_stop_intent_trips_unconditionally(self):
        safety = SafetyController()
        failsafe, _ = _triple(safety=safety)
        try:
            trip = failsafe.notify_stop_intent("voice STOP intent")
            self.assertEqual(trip.channel, "spoken")
            self.assertTrue(safety.estopped)
        finally:
            failsafe.close()

    def test_channels_latch_independently(self):
        failsafe, parts = _triple()
        try:
            failsafe.trip("physical", "hand")
            failsafe.trip("spoken", "voice")
            self.assertEqual(
                failsafe.tripped_channels, ("physical", "spoken")
            )
            self.assertEqual(failsafe.last_trip.channel, "spoken")
            # First trip per channel wins its latency record.
            self.assertEqual(failsafe.last_trip.reason, "voice")
        finally:
            failsafe.close()

    def test_broken_hardware_degrades_to_spoken_only(self):
        broken = EscHotkeyLatch(
            listener_factory=lambda on_press: (_ for _ in ()).throw(
                RuntimeError("no pynput"))
        )
        degraded = TripleFailsafe(physical=FakeFailsafe(fail_arm=True),
                                  esc_listener=broken)
        try:
            self.assertEqual(degraded.armed_channels, ("spoken",))
            # Manual + spoken trips still work while disarmed.
            degraded.trip("physical", "manual abort")
            self.assertTrue(degraded.latched)
            self.assertTrue(degraded.notify_transcript("halt!"))
            self.assertIn("spoken", degraded.tripped_channels)
        finally:
            degraded.close()

    def test_reset_clears_latch_but_not_safety(self):
        safety = SafetyController()
        failsafe, _ = _triple(safety=safety)
        try:
            failsafe.trip("esc", "drill")
            failsafe.reset(authorization="op: drill over")
            self.assertFalse(failsafe.latched)
            self.assertTrue(safety.estopped)
            with self.assertRaises(SafetyTrippedError):
                safety.check()
        finally:
            failsafe.close()

    def test_on_trip_callback_best_effort(self):
        fired: list[tuple[str, str]] = []
        failsafe, _ = _triple(
            on_trip=lambda ch, why: fired.append((ch, why)))
        try:
            failsafe.trip("spoken", "hey")
            self.assertEqual(fired, [("spoken", "hey")])
        finally:
            failsafe.close()

        def _boom(channel, reason):
            raise RuntimeError("listener broken")

        failsafe2, _ = _triple(on_trip=_boom)
        try:
            trip = failsafe2.trip("esc", "still lands")
            self.assertEqual(trip.channel, "esc")
            self.assertTrue(failsafe2.latched)
        finally:
            failsafe2.close()

    def test_trip_without_safety_or_actuator_still_latches(self):
        bus = EventBus()
        collector = Collector(bus)
        failsafe, _ = _triple(bus=bus)
        try:
            failsafe.trip("physical", "standalone")
            self.assertTrue(failsafe.latched)
            self.assertEqual(
                len(collector.of_type(EventType.E_STOP_TRIGGERED)), 1
            )
        finally:
            failsafe.close()

    def test_end_to_end_abort_halts_motion_within_budget(self):
        # Physical trip -> actuator abort -> in-flight motion halts fast.
        actor, parts = _actuator()
        failsafe, _ = _triple(actuator=actor)
        try:
            timer = threading.Timer(0.02, lambda: failsafe.trip("physical"))
            timer.start()
            started = time.monotonic()
            try:
                with self.assertRaises(CobotInterruptedError):
                    actor.move_cursor_smooth(CursorPoint(500, 500),
                                            duration_sec=60.0, steps=30)
            finally:
                timer.cancel()
            self.assertLess((time.monotonic() - started) * 1000.0, 1000.0)
            self.assertLess(len(parts["mouse"].moves), 30)
        finally:
            failsafe.close()
            actor.close()


# ---------------------------------------------------------------------------
# OutcomeVerifier
# ---------------------------------------------------------------------------


def _seed_tree(root: str):
    Path(root, "main.py").write_text("print('hi')\n", encoding="utf-8")
    Path(root, "docs").mkdir(exist_ok=True)
    Path(root, "docs", "notes.md").write_text("# notes\n", encoding="utf-8")


def _verifier(root, safety=None, bus=None, **overrides):
    parts = {"run_command": lambda cmd, cwd, timeout: (0, "ok", ""),
             "read_state": lambda repo: _clean_git_state(repo)}
    parts.update(overrides)
    return OutcomeVerifier(root, safety=safety, event_bus=bus, **parts)


class OutcomeContractTest(unittest.TestCase):
    def test_rejects_bad_construction(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                OutcomeVerifier(os.path.join(tmp, "missing"))
            with self.assertRaises(ValueError):
                OutcomeVerifier(tmp, test_command=[])
            with self.assertRaises(ValueError):
                OutcomeVerifier(tmp, test_command=["ok", ""])
            with self.assertRaises(ValueError):
                OutcomeVerifier(tmp, safety="nope")
            with self.assertRaises(ValueError):
                OutcomeVerifier(tmp, run_command="nope")
            with self.assertRaises(ValueError):
                OutcomeVerifier(tmp, test_timeout_sec=0)
            verifier = _verifier(tmp)
            with self.assertRaises(ValueError):
                verifier.verify(expected_changes="nope")
            with self.assertRaises(EmbodimentError):
                verifier.rollback()
            with self.assertRaises(EmbodimentError):
                verifier.rollback_plan()

    def test_snapshot_budget_exceeded_refuses_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_tree(tmp)
            verifier = _verifier(tmp, max_snapshot_bytes=4)
            with self.assertRaises(EmbodimentError):
                verifier.snapshot()
            self.assertIsNone(verifier.baseline)


class OutcomeSnapshotTest(unittest.TestCase):
    def test_snapshot_captures_tree_deterministically(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_tree(tmp)
            verifier = _verifier(tmp)
            first = verifier.snapshot()
            self.assertIsInstance(first, FileSnapshot)
            self.assertEqual(
                sorted(e.path for e in first.files),
                ["docs/notes.md", "main.py"],
            )
            second = verifier.snapshot()
            self.assertEqual(
                [e.to_dict() for e in first.files],
                [e.to_dict() for e in second.files],
            )
            self.assertFalse(verifier.halted)
            self.assertIsNone(verifier.last_report)

    def test_snapshot_skips_excluded_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_tree(tmp)
            Path(tmp, ".git").mkdir(exist_ok=True)
            Path(tmp, ".git", "HEAD").write_text("ref\n", encoding="utf-8")
            Path(tmp, ".venv").mkdir(exist_ok=True)
            Path(tmp, ".venv", "lib.py").write_text("x\n", encoding="utf-8")
            try:
                os.symlink(Path(tmp, "main.py"), Path(tmp, "link.py"))
                have_link = True
            except OSError:
                have_link = False  # Windows without symlink privilege
            verifier = _verifier(tmp)
            taken = verifier.snapshot()
            paths = sorted(e.path for e in taken.files)
            self.assertEqual(paths, ["docs/notes.md", "main.py"])
            self.assertGreaterEqual(taken.skipped, 1 + have_link)

    def test_oversize_file_recorded_without_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "big.bin").write_bytes(b"x" * 100)
            Path(tmp, "small.txt").write_text("s", encoding="utf-8")
            verifier = _verifier(tmp, max_file_bytes=10)
            taken = verifier.snapshot()
            by_path = taken.by_path()
            self.assertEqual(by_path["big.bin"].sha256, "")
            self.assertEqual(by_path["big.bin"].size, 100)
            self.assertTrue(by_path["small.txt"].sha256)


class OutcomeVerifyTest(unittest.TestCase):
    def test_all_checks_pass_silently(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_tree(tmp)
            safety = SafetyController()
            bus = EventBus()
            collector = Collector(bus)
            verifier = _verifier(tmp, safety=safety, bus=bus,
                                 test_command=["echo", "tests"])
            verifier.snapshot()
            report = verifier.verify()
            self.assertIsInstance(report, VerificationReport)
            self.assertTrue(report.ok)
            self.assertFalse(report.halted)
            self.assertTrue(report.rollback_available)
            self.assertEqual(len(report.checks), 3)
            self.assertEqual(report.failed(), ())
            self.assertFalse(safety.estopped)
            self.assertFalse(verifier.halted)
            self.assertIs(verifier.last_report, report)
            self.assertEqual(
                collector.of_type(EventType.ACTION_FAILED), [])

    def test_no_test_command_skips_tests_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_tree(tmp)
            verifier = _verifier(tmp)
            verifier.snapshot()
            report = verifier.verify()
            self.assertTrue(report.ok)
            tests = [c for c in report.checks if c.name == "tests"][0]
            self.assertIn("skipped", tests.detail)

    def test_failing_tests_halt_and_emit(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_tree(tmp)
            safety = SafetyController()
            bus = EventBus()
            collector = Collector(bus)
            verifier = _verifier(
                tmp, safety=safety, bus=bus,
                test_command=["run", "tests"],
                run_command=lambda cmd, cwd, timeout: (
                    1, "", "FAILED test_x"),
            )
            verifier.snapshot()
            report = verifier.verify()
            self.assertFalse(report.ok)
            self.assertTrue(report.halted)
            self.assertTrue(report.rollback_available)
            self.assertEqual(
                [c.name for c in report.failed()], ["tests"])
            self.assertIn("FAILED test_x", report.failed()[0].detail)
            self.assertTrue(safety.estopped)
            self.assertEqual(safety.last_trip.source, "outcome-verifier")
            self.assertTrue(verifier.halted)
            failed = collector.of_type(EventType.ACTION_FAILED)
            self.assertEqual(len(failed), 1)
            self.assertIn("tests", failed[0].payload["reason"])

    def test_runner_exception_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_tree(tmp)
            safety = SafetyController()

            def _boom(cmd, cwd, timeout):
                raise RuntimeError("runner down")

            verifier = _verifier(
                tmp, safety=safety, test_command=["run"],
                run_command=_boom)
            verifier.snapshot()
            report = verifier.verify(run_tests=True, check_git=False,
                                     check_files=False)
            self.assertFalse(report.ok)
            self.assertTrue(safety.estopped)

    def test_git_unreadable_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_tree(tmp)
            safety = SafetyController()

            def _missing(repo):
                raise ValueError("not a git repository")

            verifier = _verifier(tmp, safety=safety, read_state=_missing)
            verifier.snapshot()
            report = verifier.verify(run_tests=False, check_git=True,
                                     check_files=False)
            self.assertFalse(report.ok)
            self.assertTrue(safety.estopped)

    def test_forbid_dirty_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_tree(tmp)
            safety = SafetyController()
            verifier = _verifier(
                tmp, safety=safety,
                read_state=lambda repo: _dirty_git_state(repo))
            verifier.snapshot()
            clean = verifier.verify(run_tests=False, check_git=True,
                                    check_files=False)
            self.assertTrue(clean.ok)
            dirty = verifier.verify(run_tests=False, check_git=True,
                                    check_files=False, forbid_dirty=True)
            self.assertFalse(dirty.ok)
            self.assertTrue(safety.estopped)

    def test_file_changes_detected_by_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_tree(tmp)
            safety = SafetyController()
            verifier = _verifier(tmp, safety=safety)
            verifier.snapshot()
            Path(tmp, "main.py").write_text("print('changed')\n",
                                            encoding="utf-8")
            Path(tmp, "new.txt").write_text("new\n", encoding="utf-8")
            Path(tmp, "docs", "notes.md").unlink()
            report = verifier.verify(run_tests=False, check_git=False)
            self.assertFalse(report.ok)
            detail = report.failed()[0].detail
            self.assertIn("main.py (modified)", detail)
            self.assertIn("new.txt (added)", detail)
            self.assertIn("docs/notes.md (deleted)", detail)
            self.assertTrue(safety.estopped)

    def test_expected_changes_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_tree(tmp)
            safety = SafetyController()
            verifier = _verifier(tmp, safety=safety)
            verifier.snapshot()
            Path(tmp, "main.py").write_text("print('v2')\n",
                                            encoding="utf-8")
            report = verifier.verify(run_tests=False, check_git=False,
                                     expected_changes=["main.py"])
            self.assertTrue(report.ok)
            self.assertFalse(safety.estopped)

    def test_missing_snapshot_fails_file_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_tree(tmp)
            safety = SafetyController()
            verifier = _verifier(tmp, safety=safety)
            report = verifier.verify(run_tests=False, check_git=False)
            self.assertFalse(report.ok)
            self.assertFalse(report.rollback_available)
            self.assertTrue(safety.estopped)

    def test_default_runner_executes_without_shell(self):
        from mimir.embodiment import _default_run_command

        code, out, err = _default_run_command(
            [sys.executable, "-c", "print('seam ok')"], None, 30.0)
        self.assertEqual(code, 0)
        self.assertIn("seam ok", out)

    def test_halt_standalone_trips_and_emits(self):
        with tempfile.TemporaryDirectory() as tmp:
            safety = SafetyController()
            bus = EventBus()
            collector = Collector(bus)
            verifier = _verifier(tmp, safety=safety, bus=bus)
            record = verifier.halt("manual drill")
            self.assertTrue(safety.estopped)
            self.assertIsNotNone(record)
            self.assertTrue(verifier.halted)
            self.assertEqual(
                len(collector.of_type(EventType.ACTION_FAILED)), 1
            )
            # No safety attached: still records halt, never raises.
            bare = _verifier(tmp)
            self.assertIsNone(bare.halt("drill"))
            self.assertTrue(bare.halted)


class OutcomeRollbackTest(unittest.TestCase):
    def test_plan_previews_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_tree(tmp)
            verifier = _verifier(tmp)
            verifier.snapshot()
            Path(tmp, "main.py").write_text("v2\n", encoding="utf-8")
            Path(tmp, "docs", "notes.md").unlink()
            Path(tmp, "added.txt").write_text("a\n", encoding="utf-8")
            plan = verifier.rollback_plan()
            by_path = {step.path: step.action for step in plan}
            self.assertEqual(by_path, {
                "added.txt": "delete",
                "docs/notes.md": "restore",
                "main.py": "restore",
            })
            # Preview wrote nothing.
            self.assertEqual(
                Path(tmp, "main.py").read_text(encoding="utf-8"), "v2\n")

    def test_rollback_restores_and_cleans_in_one_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_tree(tmp)
            safety = SafetyController()
            bus = EventBus()
            collector = Collector(bus)
            verifier = _verifier(tmp, safety=safety, bus=bus)
            verifier.snapshot()
            Path(tmp, "main.py").write_text("v2\n", encoding="utf-8")
            Path(tmp, "docs", "notes.md").unlink()
            Path(tmp, "added.txt").write_text("a\n", encoding="utf-8")
            report = verifier.verify(run_tests=False, check_git=False)
            self.assertFalse(report.ok)
            self.assertTrue(safety.estopped)
            # 1-click rollback works while the E-stop is latched ...
            rolled = verifier.rollback()
            self.assertIsInstance(rolled, RollbackReport)
            self.assertTrue(rolled.ok, rolled.errors)
            self.assertEqual(rolled.restored, 2)
            self.assertEqual(rolled.deleted, 1)
            self.assertEqual(
                Path(tmp, "main.py").read_text(encoding="utf-8"),
                "print('hi')\n",
            )
            self.assertEqual(
                Path(tmp, "docs", "notes.md").read_text(encoding="utf-8"),
                "# notes\n",
            )
            self.assertFalse(Path(tmp, "added.txt").exists())
            # ... and never clears the safety latch by itself.
            self.assertTrue(safety.estopped)
            done = collector.of_type(EventType.ACTION_COMPLETED)
            self.assertEqual(len(done), 1)
            self.assertTrue(done[0].payload["rollback"])
            # Tree verifies clean afterwards.
            again = verifier.verify(run_tests=False, check_git=False)
            self.assertTrue(again.ok)

    def test_rollback_noop_when_tree_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed_tree(tmp)
            verifier = _verifier(tmp)
            verifier.snapshot()
            self.assertEqual(verifier.rollback_plan(), ())
            rolled = verifier.rollback()
            self.assertTrue(rolled.ok)
            self.assertEqual(rolled.restored, 0)
            self.assertEqual(rolled.deleted, 0)

    def test_rollback_reports_unstored_oversize_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "big.bin").write_bytes(b"x" * 100)
            verifier = _verifier(tmp, max_file_bytes=10)
            verifier.snapshot()
            Path(tmp, "big.bin").write_bytes(b"y" * 120)
            plan = verifier.rollback_plan()
            self.assertEqual(len(plan), 1)
            self.assertIn("manual review", plan[0].detail)
            rolled = verifier.rollback()
            self.assertFalse(rolled.ok)
            self.assertTrue(any("manual review" in e
                                for e in rolled.errors))



