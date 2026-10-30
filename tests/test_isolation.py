"""Tests for computational isolation (plan sections 6-7, step 5).

Qt-free tests cover the bounded prioritizing/coalescing queues, the
per-runtime workers, the process offload, single loop ownership, and
the dedicated Bezier trajectory executor. Qt worker tests run
offscreen-or-skip following the tests/test_phase4.py pattern.
"""

import os
import threading
import time
import unittest
from unittest import mock

from mimir.actuator import (
    ActuatorError,
    CobotInterruptedError,
    CursorPoint,
    DesktopActuator,
    plan_trajectory,
)
from mimir.events import EventBus, EventType, create_event
from mimir.isolation import (
    DEFAULT_RUNTIME_QUEUE_SIZES,
    BoundedJobQueue,
    IsolationBackpressure,
    IsolationError,
    IsolationWorker,
    JobCancelledError,
    JobExpiredError,
    JobPriority,
    LoopOwner,
    ProcessOffload,
    RuntimeIsolation,
    RuntimeKind,
    get_runtime_isolation,
    reset_runtime_isolation,
    set_runtime_isolation,
)
from mimir.principles import (
    BehavioralPrinciple,
    ContextTrigger,
    EpistemicStatus,
)
from mimir.trajectory import (
    ScreenBounds,
    TrajectoryBoundsError,
    TrajectoryCancelled,
    TrajectoryExecutor,
)


def _probe_mul(left, right):
    """Module-level picklable probe for ProcessOffload tests."""
    return left * right


def _probe_nap(seconds):
    """Module-level picklable slow probe for timeout tests."""
    time.sleep(seconds)
    return "woke"


async def _noop_coro():
    return 1


class FakeClock:
    """Deterministic monotonic clock for scheduling tests."""

    def __init__(self, start=1000.0):
        self.now = float(start)
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            return self.now

    def advance(self, seconds):
        with self._lock:
            self.now += seconds
            return self.now


def _advancing_sleeper(clock):
    def _sleep(chunk, waiter):
        clock.advance(chunk)
        return bool(waiter is not None and waiter.is_set())

    return _sleep


def _principle(pid="bp_iso_001",
               invariant="Commit with a clear message."):
    return BehavioralPrinciple(
        id=pid,
        name="Isolation probe",
        category="workflow",
        context_trigger=ContextTrigger(scope="Isolation tests",
                                       language_agnostic=True),
        latent_invariant=invariant,
        anti_patterns=("Skipping the tests",),
        epistemic_status=EpistemicStatus.new(0.80, supporting=2),
        inspectable_traces=("iso_test",),
    )


class _Mouse:
    def __init__(self):
        self.moves = []
        self._pos = (0, 0)

    def position(self):
        return self._pos

    def move_to(self, x, y):
        self.moves.append((x, y))
        self._pos = (x, y)

    def click(self):
        pass


class _Keys:
    def __init__(self):
        self.chars = []

    def write_char(self, char):
        self.chars.append(char)


class _Safe:
    def __init__(self):
        self.noted = []

    def arm(self, callback):
        self._callback = callback

    def disarm(self):
        pass

    def note_commanded(self, x, y):
        self.noted.append((x, y))


class QueueValidationTest(unittest.TestCase):
    def test_bad_maxsize_rejected(self):
        for bad in (0, -2, True, "8", 2.5):
            with self.assertRaises(ValueError, msg=repr(bad)):
                BoundedJobQueue(bad)

    def test_bad_submit_rejected(self):
        queue = BoundedJobQueue(4)
        with self.assertRaises(ValueError):
            queue.submit("not-callable")
        with self.assertRaises(ValueError):
            queue.submit(lambda: None, args=[1])
        with self.assertRaises(ValueError):
            queue.submit(lambda: None, priority="high")
        with self.assertRaises(ValueError):
            queue.submit(lambda: None, coalesce_key=7)
        with self.assertRaises(ValueError):
            queue.submit(lambda: None, timeout_s=0)
        with self.assertRaises(ValueError):
            queue.submit(lambda: None, ttl_s=-1)

    def test_default_runtime_sizes_cover_every_runtime(self):
        self.assertEqual(set(DEFAULT_RUNTIME_QUEUE_SIZES), set(RuntimeKind))
        for size in DEFAULT_RUNTIME_QUEUE_SIZES.values():
            self.assertGreaterEqual(size, 1)


class QueuePriorityTest(unittest.TestCase):
    def test_critical_preempts_telemetry(self):
        queue = BoundedJobQueue(8)
        queue.submit(lambda: "low", priority=JobPriority.LOW)
        queue.submit(lambda: "normal", priority=JobPriority.NORMAL)
        queue.submit(lambda: "critical", priority=JobPriority.CRITICAL)
        queue.submit(lambda: "high", priority=JobPriority.HIGH)
        order = [queue.pop(timeout=0).fn() for _ in range(4)]
        self.assertEqual(order, ["critical", "high", "normal", "low"])

    def test_fifo_within_priority(self):
        queue = BoundedJobQueue(8)
        for index in range(3):
            queue.submit(lambda i=index: i)
        self.assertEqual(
            [queue.pop(timeout=0).fn() for _ in range(3)], [0, 1, 2]
        )

    def test_pop_timeout_returns_none(self):
        queue = BoundedJobQueue(2)
        self.assertIsNone(queue.pop(timeout=0))
        self.assertIsNone(queue.pop(timeout=0.01))


class QueueCoalesceTest(unittest.TestCase):
    def test_same_key_replaces_payload(self):
        queue = BoundedJobQueue(8)
        first = queue.submit(lambda: "stale", coalesce_key="watch")
        second = queue.submit(lambda: "fresh", coalesce_key="watch")
        self.assertEqual(first.job_id, second.job_id)
        self.assertEqual(queue.stats()["coalesced"], 1)
        self.assertEqual(queue.pending(), 1)
        self.assertEqual(queue.pop(timeout=0).fn(), "fresh")

    def test_started_job_does_not_coalesce(self):
        queue = BoundedJobQueue(8)
        first = queue.submit(lambda: "a", coalesce_key="watch")
        queue.pop(timeout=0)  # job starts; key released
        second = queue.submit(lambda: "b", coalesce_key="watch")
        self.assertNotEqual(first.job_id, second.job_id)
        self.assertEqual(queue.pop(timeout=0).fn(), "b")

    def test_distinct_keys_do_not_merge(self):
        queue = BoundedJobQueue(8)
        queue.submit(lambda: "a", coalesce_key="watch")
        queue.submit(lambda: "b", coalesce_key="voice")
        self.assertEqual(queue.pending(), 2)


class QueueBoundedTest(unittest.TestCase):
    def test_higher_priority_evicts_lowest(self):
        queue = BoundedJobQueue(2)
        low = queue.submit(lambda: "low", priority=JobPriority.LOW)
        queue.submit(lambda: "low2", priority=JobPriority.LOW)
        queue.submit(lambda: "critical", priority=JobPriority.CRITICAL)
        self.assertEqual(queue.stats()["dropped"], 1)
        with self.assertRaises(IsolationBackpressure):
            low.future.result(timeout=5)

    def test_equal_priority_rejects_newcomer(self):
        queue = BoundedJobQueue(1)
        queue.submit(lambda: "a", priority=JobPriority.NORMAL)
        with self.assertRaises(IsolationBackpressure):
            queue.submit(lambda: "b", priority=JobPriority.NORMAL)
        self.assertEqual(queue.stats()["rejected"], 1)

    def test_lower_priority_never_evicts(self):
        queue = BoundedJobQueue(1)
        queue.submit(lambda: "high", priority=JobPriority.HIGH)
        with self.assertRaises(IsolationBackpressure):
            queue.submit(lambda: "low", priority=JobPriority.LOW)
        self.assertEqual(queue.pop(timeout=0).fn(), "high")


class QueueCancelExpiryTest(unittest.TestCase):
    def test_cancel_pending_drops(self):
        queue = BoundedJobQueue(4)
        handle = queue.submit(lambda: "never")
        self.assertTrue(queue.cancel(handle.job_id))
        self.assertTrue(handle.future.cancelled())
        self.assertEqual(queue.pending(), 0)
        self.assertEqual(queue.stats()["cancelled"], 1)
        self.assertIsNone(queue.pop(timeout=0))

    def test_cancel_unknown_is_false(self):
        queue = BoundedJobQueue(4)
        self.assertFalse(queue.cancel("job_event_999"))

    def test_expired_job_never_runs(self):
        queue = BoundedJobQueue(4)
        handle = queue.submit(lambda: "stale", ttl_s=0.05)
        time.sleep(0.08)
        self.assertIsNone(queue.pop(timeout=0))
        with self.assertRaises(JobExpiredError):
            handle.future.result(timeout=5)
        self.assertEqual(queue.stats()["expired"], 1)

    def test_pre_cancelled_job_skipped_at_pop(self):
        queue = BoundedJobQueue(4)
        handle = queue.submit(lambda: "x")
        handle.cancel_event.set()  # external trip before start
        self.assertIsNone(queue.pop(timeout=0))
        self.assertEqual(queue.stats()["cancelled"], 1)


class WorkerOffThreadTest(unittest.TestCase):
    def test_job_runs_off_caller_thread(self):
        worker = IsolationWorker(RuntimeKind.OBSERVATION)
        try:
            main_ident = threading.get_ident()
            handle = worker.submit(threading.get_ident)
            self.assertNotEqual(handle.result(timeout=10), main_ident)
            self.assertEqual(worker.metrics()["completed"], 1)
        finally:
            worker.stop()

    def test_failure_isolation(self):
        worker = IsolationWorker(RuntimeKind.EVENT)
        try:
            def _boom():
                raise RuntimeError("job boom")

            bad = worker.submit(_boom)
            good = worker.submit(lambda: "alive")
            with self.assertRaises(RuntimeError):
                bad.result(timeout=10)
            self.assertEqual(good.result(timeout=10), "alive")
            metrics = worker.metrics()
            self.assertEqual(metrics["failed"], 1)
            self.assertEqual(metrics["completed"], 1)
            self.assertTrue(worker.running)
        finally:
            worker.stop()

    def test_timeout_recorded_with_late_value(self):
        worker = IsolationWorker(RuntimeKind.INFERENCE)
        try:
            handle = worker.submit(lambda: time.sleep(0.25) or "late",
                                   timeout_s=0.05)
            # The value still arrives (no preemption for threads), but
            # the miss is recorded so hosts can shed or escalate.
            self.assertEqual(handle.result(timeout=10), "late")
            self.assertEqual(worker.metrics()["timed_out"], 1)
        finally:
            worker.stop()

    def test_cooperative_cancel_raises(self):
        worker = IsolationWorker(RuntimeKind.VOICE)
        try:
            def _coop(cancel_event=None):
                assert cancel_event is not None
                if cancel_event.wait(5.0):
                    raise JobCancelledError("told to stop")
                return "ignored"

            handle = worker.submit(_coop, timeout_s=0.05)
            with self.assertRaises(JobCancelledError):
                handle.result(timeout=10)
            self.assertEqual(worker.metrics()["cancelled"], 1)
        finally:
            worker.stop()

    def test_cancel_running_sets_event(self):
        worker = IsolationWorker(RuntimeKind.ACTUATION)
        gate = threading.Event()
        release = threading.Event()
        observed = {}

        def _guarded(cancel_event=None):
            gate.set()
            release.wait(10.0)
            observed["cancelled"] = bool(
                cancel_event is not None and cancel_event.is_set()
            )
            return "done"

        try:
            handle = worker.submit(_guarded)
            self.assertTrue(gate.wait(10.0))
            self.assertTrue(handle.cancel())
            release.set()
            self.assertEqual(handle.result(timeout=10), "done")
            self.assertTrue(observed["cancelled"])
        finally:
            worker.stop()

    def test_queue_wait_and_run_percentiles_present(self):
        worker = IsolationWorker(RuntimeKind.SAFETY)
        try:
            worker.submit(lambda: None).result(timeout=10)
            metrics = worker.metrics()
            for key in ("queue_wait_ms_p50", "queue_wait_ms_p95",
                        "run_ms_p50", "run_ms_p95"):
                self.assertIn(key, metrics)
                self.assertGreaterEqual(metrics[key], 0.0)
        finally:
            worker.stop()


class RuntimeIsolationTest(unittest.TestCase):
    def test_submit_by_enum_and_name(self):
        runtime = RuntimeIsolation(autostart=True)
        try:
            first = runtime.submit(RuntimeKind.VOICE, lambda: "v")
            second = runtime.submit("inference", lambda: "i")
            self.assertEqual(first.result(timeout=10), "v")
            self.assertEqual(second.result(timeout=10), "i")
            with self.assertRaises(ValueError):
                runtime.submit("teleport", lambda: None)
        finally:
            runtime.shutdown()

    def test_safety_never_waits_behind_inference(self):
        runtime = RuntimeIsolation(autostart=True)
        gate = threading.Event()
        release = threading.Event()
        try:
            runtime.submit("inference",
                           lambda: gate.set() or release.wait(10.0))
            self.assertTrue(gate.wait(10.0))
            # Inference is saturated; safety must still answer promptly.
            started = time.monotonic()
            safety = runtime.submit("safety", lambda: "estop-ok",
                                    priority=JobPriority.CRITICAL)
            self.assertEqual(safety.result(timeout=10), "estop-ok")
            self.assertLess(time.monotonic() - started, 9.0)
        finally:
            release.set()
            runtime.shutdown()

    def test_cancel_searches_every_runtime(self):
        runtime = RuntimeIsolation(
            autostart=False,
            sizes={kind: 4 for kind in RuntimeKind},
        )
        try:
            # Autostart off: workers start lazily on submit, so a second
            # queued job stays pending behind a blocked first job.
            gate = threading.Event()
            release = threading.Event()
            runtime.submit("voice", lambda: gate.set() or release.wait(10))
            self.assertTrue(gate.wait(10.0))
            pending = runtime.submit("voice", lambda: "never")
            self.assertTrue(runtime.cancel(pending.job_id))
            self.assertFalse(runtime.cancel("job_voice_999"))
        finally:
            release.set()
            runtime.shutdown()

    def test_stats_cover_every_runtime(self):
        runtime = RuntimeIsolation(autostart=False)
        try:
            stats = runtime.stats()
            self.assertEqual(set(stats), {k.value for k in RuntimeKind})
            self.assertIn("submitted", stats["safety"])
        finally:
            runtime.shutdown()

    def test_global_runtime_lifecycle(self):
        first = get_runtime_isolation()
        self.assertIs(get_runtime_isolation(), first)
        custom = RuntimeIsolation(autostart=False)
        set_runtime_isolation(custom)
        self.assertIs(get_runtime_isolation(), custom)
        with self.assertRaises(ValueError):
            set_runtime_isolation("nope")
        reset_runtime_isolation()
        self.assertIsNot(get_runtime_isolation(), custom)
        reset_runtime_isolation()

    def test_event_bus_pump_from_worker_thread(self):
        # The Qt-safe bridge: loop-free enqueue + pump driven from an
        # isolation worker instead of the UI thread.
        bus = EventBus()
        received = []
        bus.subscribe(EventType.GIT_CHANGED,
                      handler=lambda event: received.append(event))
        runtime = RuntimeIsolation(autostart=True)
        try:
            def _publish():
                bus.enqueue(create_event(EventType.GIT_CHANGED,
                                         "sensory.git_observer",
                                         {"repo": "demo"}))
                bus.pump(max_events=10)
                return threading.get_ident()

            ident = runtime.submit("event", _publish).result(timeout=10)
            self.assertNotEqual(ident, threading.get_ident())
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0].payload["repo"], "demo")
        finally:
            runtime.shutdown()


class ProcessOffloadTest(unittest.TestCase):
    def test_offload_runs_in_process(self):
        pool = ProcessOffload(max_workers=1)
        if not pool.available:
            self.skipTest("multiprocessing unavailable")
        try:
            future = pool.submit(_probe_mul, (6, 7))
            self.assertEqual(pool.result(future, timeout_s=60), 42)
        finally:
            pool.shutdown()

    def test_offload_timeout_observed(self):
        pool = ProcessOffload(max_workers=1)
        if not pool.available:
            self.skipTest("multiprocessing unavailable")
        try:
            future = pool.submit(_probe_nap, (3.0,))
            with self.assertRaises(TimeoutError):
                pool.result(future, timeout_s=0.2)
        finally:
            pool.shutdown(wait=True)

    def test_bad_offload_rejected(self):
        with self.assertRaises(ValueError):
            ProcessOffload(max_workers=0)
        with self.assertRaises(ValueError):
            ProcessOffload(mp_context="pigeon")
        pool = ProcessOffload()
        try:
            with self.assertRaises(ValueError):
                pool.submit("not-callable")
        finally:
            pool.shutdown(wait=False)


class LoopOwnerTest(unittest.TestCase):
    def tearDown(self):
        for name in ("owner-a", "owner-b", "owner-c", "desktop-test",
                     "factory-boom"):
            try:
                LoopOwner.release(name)
            except IsolationError:
                pass
            except ValueError:
                pass

    def test_acquire_and_reacquire_same_owner(self):
        first = LoopOwner.acquire("owner-a",
                                  loop_factory=lambda: object())
        second = LoopOwner.acquire("owner-a")
        self.assertIs(first, second)
        self.assertTrue(LoopOwner.owned())
        self.assertEqual(LoopOwner.owner(), "owner-a")
        self.assertIs(LoopOwner.loop(), first)

    def test_second_owner_rejected(self):
        LoopOwner.acquire("owner-a", loop_factory=lambda: object())
        with self.assertRaises(IsolationError):
            LoopOwner.acquire("owner-b", loop_factory=lambda: object())
        self.assertEqual(LoopOwner.owner(), "owner-a")

    def test_release_rules(self):
        self.assertFalse(LoopOwner.release("owner-a"))
        LoopOwner.acquire("owner-a", loop_factory=lambda: object())
        with self.assertRaises(IsolationError):
            LoopOwner.release("owner-b")
        self.assertTrue(LoopOwner.release("owner-a"))
        self.assertFalse(LoopOwner.owned())
        self.assertFalse(LoopOwner.release("owner-a"))

    def test_bad_owner_and_factory(self):
        with self.assertRaises(ValueError):
            LoopOwner.acquire("  ", loop_factory=lambda: object())
        with self.assertRaises(ValueError):
            LoopOwner.release("")

        def _boom():
            raise RuntimeError("no loop here")

        with self.assertRaises(IsolationError):
            LoopOwner.acquire("factory-boom", loop_factory=_boom)
        self.assertFalse(LoopOwner.owned())

    def test_run_requires_ownership(self):
        coro = _noop_coro()
        try:
            with self.assertRaises(IsolationError):
                LoopOwner.run("owner-c", coro, timeout_s=1)
        finally:
            coro.close()  # run() raises before scheduling; nothing awaited

    def test_qasync_absent_hint(self):
        try:
            import qasync  # noqa: F401
        except ImportError:
            pass
        else:
            self.skipTest("qasync installed; hint path not exercised")
        with self.assertRaises(IsolationError) as ctx:
            LoopOwner.acquire_qasync("desktop-test")
        self.assertIn("qasync", str(ctx.exception))


class TrajectoryValidationTest(unittest.TestCase):
    def test_bad_inputs_rejected(self):
        executor = TrajectoryExecutor(lambda x, y: None)
        with self.assertRaises(ValueError):
            executor.execute([], 0.5)
        with self.assertRaises(ValueError):
            executor.execute([(0, 0)], 0)
        with self.assertRaises(ValueError):
            executor.execute([(0, 0)], 0.5, deadline_tolerance_ms=0)
        with self.assertRaises(ValueError):
            executor.execute([(0, 0)], 0.5, bounds="huge")
        with self.assertRaises(ValueError):
            executor.execute([("a", "b")], 0.5)
        with self.assertRaises(ValueError):
            executor.execute([(0, 0, 0)], 0.5)
        with self.assertRaises(ValueError):
            TrajectoryExecutor("not-callable")
        with self.assertRaises(ValueError):
            TrajectoryExecutor(lambda x, y: None, note_commanded=7)
        for bad in ((0, 100), (100, 0), (-5, 10)):
            with self.assertRaises(ValueError, msg=repr(bad)):
                ScreenBounds(width=bad[0], height=bad[1])

    def test_bounds_rejected_before_any_motion(self):
        moves = []
        noted = []
        executor = TrajectoryExecutor(
            lambda x, y: moves.append((x, y)),
            note_commanded=lambda x, y: noted.append((x, y)),
        )
        points = [(10, 10), (50, 50), (5000, 10)]
        with self.assertRaises(TrajectoryBoundsError):
            executor.execute(points, 0.5, bounds=(1920, 1080))
        self.assertEqual(moves, [])
        self.assertEqual(noted, [])

    def test_bounds_accept_tuple_and_screen(self):
        moves = []
        clock = FakeClock()
        executor = TrajectoryExecutor(
            lambda x, y: moves.append((x, y)),
            clock=clock, sleeper=_advancing_sleeper(clock),
        )
        report = executor.execute([(0, 0), (5, 5)], 0.1,
                                  bounds=ScreenBounds(10, 10))
        self.assertTrue(report.completed)
        self.assertEqual(len(moves), 2)
        with self.assertRaises(TrajectoryBoundsError):
            executor.execute([(0, 0), (10, 10)], 0.1,
                             bounds=(10, 10))  # edge is exclusive


class TrajectoryScheduleTest(unittest.TestCase):
    def test_monotonic_schedule_commands_in_order(self):
        clock = FakeClock()
        moves = []
        noted = []
        executor = TrajectoryExecutor(
            lambda x, y: moves.append((x, y)),
            note_commanded=lambda x, y: noted.append((x, y)),
            clock=clock, sleeper=_advancing_sleeper(clock),
        )
        points = [(0, 0), (3, 1), (6, 2), (9, 3), (12, 4)]
        report = executor.execute(points, 0.4, bounds=(1920, 1080))
        self.assertEqual(moves, points)
        self.assertEqual(noted, points)
        self.assertTrue(report.completed)
        self.assertEqual(report.points_commanded, 5)
        self.assertEqual(report.points_skipped, 0)
        self.assertEqual(report.missed_deadlines, 0)
        self.assertAlmostEqual(report.elapsed_ms, 400.0, delta=1.0)
        self.assertAlmostEqual(report.mean_abs_deviation_ms, 0.0, delta=1.0)

    def test_stale_samples_skipped_never_burst(self):
        clock = FakeClock()
        moves = []

        def _laggy_move(x, y):
            moves.append((x, y))
            clock.advance(0.5)  # fall catastrophically behind

        executor = TrajectoryExecutor(
            _laggy_move, clock=clock,
            sleeper=_advancing_sleeper(clock),
        )
        points = [(i, 0) for i in range(10)]
        report = executor.execute(points, 0.09, bounds=(1920, 1080),
                                  deadline_tolerance_ms=50.0)
        # First sample ships, then the clock jump makes the rest stale:
        # they are dropped, never replayed as a burst of old events.
        self.assertEqual(len(moves), report.points_commanded)
        self.assertGreater(report.points_skipped, 0)
        self.assertEqual(report.points_commanded + report.points_skipped,
                         len(points))
        for skipped_index in range(report.points_commanded,
                                   len(points)):
            self.assertNotIn(points[skipped_index], moves)
        self.assertGreaterEqual(
            report.missed_deadlines, report.points_skipped)

    def test_pre_cancel_aborts_without_motion(self):
        moves = []
        executor = TrajectoryExecutor(lambda x, y: moves.append((x, y)))
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(TrajectoryCancelled):
            executor.execute([(0, 0), (5, 5)], 0.2, cancel_event=cancel)
        self.assertEqual(moves, [])

    def test_mid_run_cancel_aborts_promptly(self):
        moves = []
        cancel = threading.Event()

        def _trip_after_two(x, y):
            moves.append((x, y))
            if len(moves) == 2:
                cancel.set()

        executor = TrajectoryExecutor(_trip_after_two)
        with self.assertRaises(TrajectoryCancelled):
            executor.execute([(i, 0) for i in range(20)], 2.0,
                             bounds=(1920, 1080), cancel_event=cancel)
        self.assertEqual(len(moves), 2)

    def test_interrupt_event_aborts(self):
        executor = TrajectoryExecutor(lambda x, y: None)
        interrupt = threading.Event()
        interrupt.set()
        with self.assertRaises(TrajectoryCancelled):
            executor.execute([(0, 0), (5, 5)], 0.2,
                             interrupt_event=interrupt)

    def test_report_metrics_shape(self):
        clock = FakeClock()
        executor = TrajectoryExecutor(
            lambda x, y: None, clock=clock,
            sleeper=_advancing_sleeper(clock),
        )
        report = executor.execute([(0, 0), (4, 4), (8, 8)], 0.3)
        payload = report.to_dict()
        for key in ("points_commanded", "points_skipped",
                    "missed_deadlines", "max_lateness_ms",
                    "mean_abs_deviation_ms", "p50_lateness_ms",
                    "p95_lateness_ms", "elapsed_ms", "completed"):
            self.assertIn(key, payload)
        self.assertEqual(payload["points_commanded"], 3)
        self.assertTrue(payload["completed"])

    def test_real_clock_smoke(self):
        moves = []
        executor = TrajectoryExecutor(lambda x, y: moves.append((x, y)))
        report = executor.execute([(0, 0), (2, 2), (4, 4)], 0.06,
                                  bounds=(1920, 1080))
        self.assertTrue(report.completed)
        self.assertEqual(report.points_commanded + report.points_skipped, 3)
        self.assertEqual(len(moves), report.points_commanded)


class ActuatorTrajectoryTest(unittest.TestCase):
    def _actuator(self):
        mouse = _Mouse()
        actuator = DesktopActuator(mouse=mouse, keyboard=_Keys(),
                                   failsafe=_Safe())
        return actuator, mouse

    def test_execute_trajectory_reports(self):
        actuator, mouse = self._actuator()
        try:
            start = CursorPoint(x=0, y=0)
            target = CursorPoint(x=9, y=3)
            points = plan_trajectory(start, target, 4)
            report = actuator.execute_trajectory(
                list(points), 0.05, bounds=(1920, 1080))
            self.assertTrue(report["completed"])
            self.assertEqual(report["points_commanded"], len(points))
            self.assertEqual(mouse.moves[-1], (9, 3))
        finally:
            actuator.close()

    def test_execute_trajectory_bounds_fail_closed(self):
        actuator, mouse = self._actuator()
        try:
            points = [CursorPoint(x=0, y=0), CursorPoint(x=5000, y=0)]
            with self.assertRaises(ActuatorError):
                actuator.execute_trajectory(points, 0.05,
                                            bounds=(1920, 1080))
            self.assertEqual(mouse.moves, [])
        finally:
            actuator.close()

    def test_execute_trajectory_cancel(self):
        actuator, mouse = self._actuator()
        try:
            cancel = threading.Event()
            cancel.set()
            points = list(plan_trajectory(CursorPoint(x=0, y=0),
                                          CursorPoint(x=9, y=9), 4))
            with self.assertRaises(CobotInterruptedError):
                actuator.execute_trajectory(points, 0.5,
                                            cancel_event=cancel)
            self.assertEqual(mouse.moves, [])
        finally:
            actuator.close()

    def test_execute_trajectory_abort(self):
        actuator, _mouse = self._actuator()
        try:
            actuator.abort()
            points = [CursorPoint(x=0, y=0), CursorPoint(x=1, y=1)]
            with self.assertRaises(CobotInterruptedError):
                actuator.execute_trajectory(points, 0.05)
        finally:
            actuator.close()

    def test_execute_trajectory_validation(self):
        actuator, _mouse = self._actuator()
        try:
            with self.assertRaises(ValueError):
                actuator.execute_trajectory([], 0.1)
            with self.assertRaises(ValueError):
                actuator.execute_trajectory([(0, 0)], 0.1)
            with self.assertRaises(ValueError):
                actuator.execute_trajectory([CursorPoint(x=0, y=0)],
                                            0.1, cancel_event="nope")
        finally:
            actuator.close()


class CoalescePollTest(unittest.TestCase):
    def _coalesce(self):
        try:
            from desktop_app.observe_worker import coalesce_poll
        except ImportError as exc:
            self.skipTest(f"Qt bindings unavailable: {exc}")
        return coalesce_poll

    def test_truth_table(self):
        coalesce_poll = self._coalesce()
        self.assertEqual(
            coalesce_poll(running=False, pending=False), "run")
        self.assertEqual(
            coalesce_poll(running=True, pending=False), "coalesce")
        self.assertEqual(
            coalesce_poll(running=True, pending=True), "noop")
        self.assertEqual(
            coalesce_poll(running=False, pending=True), "run")


class TrustRepoTest(unittest.TestCase):
    def _fn(self):
        try:
            from desktop_app.observe_worker import run_trust_repo
        except ImportError as exc:
            self.skipTest(f"Qt bindings unavailable: {exc}")
        return run_trust_repo

    def test_empty_repo_rejected(self):
        with self.assertRaises(ValueError):
            self._fn()("  ")

    def test_git_failure_raises_runtime(self):
        proc = mock.Mock()
        proc.returncode = 1
        proc.stderr = "dubious ownership"
        with mock.patch("subprocess.run", return_value=proc) as runner:
            with self.assertRaises(RuntimeError) as ctx:
                self._fn()("/tmp/demo")
            self.assertIn("dubious ownership", str(ctx.exception))
        runner.assert_called_once()
        argv = runner.call_args[0][0]
        self.assertEqual(argv[:5],
                         ["git", "config", "--global", "--add",
                          "safe.directory"])

    def test_git_success_returns_repo(self):
        proc = mock.Mock()
        proc.returncode = 0
        proc.stderr = ""
        with mock.patch("subprocess.run", return_value=proc):
            self.assertEqual(self._fn()("/tmp/demo"), "/tmp/demo")

    def test_git_timeout_raises_runtime(self):
        import subprocess as _subprocess

        with mock.patch("subprocess.run",
                        side_effect=_subprocess.TimeoutExpired("git", 30)):
            with self.assertRaises(RuntimeError):
                self._fn()("/tmp/demo")


def _needs_qt(test):
    """Skip the test when Qt bindings are unavailable (offscreen)."""
    try:
        from desktop_app.tabs._qt import load_qt
        load_qt()
    except ImportError:
        test.skipTest("PyQt6/PySide6 not installed")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from desktop_app.tabs._qt import load_qt
    _, QtCore, _, QtWidgets = load_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    try:
        test._qt_app_ref = app
    except AttributeError:
        pass
    return QtCore, QtWidgets, app


def _wait_for(signal, QtCore, timeout_ms=15000):
    """Run a local event loop until the signal fires (or timeout)."""
    loop = QtCore.QEventLoop()
    box = {}

    def _got(*args):
        box["args"] = args
        loop.quit()

    signal.connect(_got)
    QtCore.QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    try:
        signal.disconnect(_got)
    except (TypeError, RuntimeError):
        pass
    return box.get("args")


class _FakeState:
    """Duck-typed AppState for worker/watch tests (no git)."""

    def __init__(self, episodes=None, delay=0.0, error=None):
        self._episodes = list(episodes or [])
        self._delay = delay
        self._error = error
        self.calls = []
        self.thread_idents = []
        self.watching = False

        class _Prefs:
            chimes = False

        self.prefs = _Prefs()

    def observe_new(self, repo, roots, max_commits=10):
        return self._pass("new", repo)

    def observe(self, repo, roots, max_commits=10):
        return self._pass("full", repo)

    def _pass(self, kind, repo):
        self.calls.append((kind, repo))
        self.thread_idents.append(threading.get_ident())
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return list(self._episodes)

    def set_watching(self, watching):
        self.watching = watching


class ObserveWorkerQtTest(unittest.TestCase):
    def test_finished_off_ui_thread(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.observe_worker import create_observe_worker
        state = _FakeState(episodes=["ep_1", "ep_2"])
        worker = create_observe_worker(state, ".", ["."], 10)
        try:
            worker.start()
            fired = _wait_for(worker.observe_finished, QtCore)
            worker.wait(5000)
            self.assertEqual(fired, (["ep_1", "ep_2"],))
            self.assertEqual(state.calls, [("new", ".")])
            self.assertNotEqual(state.thread_idents[0],
                                threading.get_ident())
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def test_error_path(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.observe_worker import create_observe_worker
        state = _FakeState(error=RuntimeError("git exploded"))
        worker = create_observe_worker(state, ".", ["."], 10)
        try:
            worker.start()
            fired = _wait_for(worker.observe_error, QtCore)
            worker.wait(5000)
            self.assertIsNotNone(fired)
            self.assertIn("git exploded", fired[0])
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def test_cancel_before_start(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.observe_worker import create_observe_worker
        state = _FakeState(episodes=["ep_1"])
        worker = create_observe_worker(state, ".", ["."], 10)
        try:
            worker.cancel()
            self.assertTrue(worker.is_cancelled)
            worker.start()
            fired = _wait_for(worker.observe_cancelled, QtCore)
            worker.wait(5000)
            self.assertIsNotNone(fired)
            self.assertEqual(state.calls, [])
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def test_bad_worker_rejected(self):
        _needs_qt(self)
        from desktop_app.observe_worker import create_observe_worker
        with self.assertRaises(ValueError):
            create_observe_worker(object(), ".", ["."])
        with self.assertRaises(ValueError):
            create_observe_worker(_FakeState(), "  ", ["."])
        with self.assertRaises(ValueError):
            create_observe_worker(_FakeState(), ".", ".")
        with self.assertRaises(ValueError):
            create_observe_worker(_FakeState(), ".", ["."], 0)


class CallableWorkerQtTest(unittest.TestCase):
    def test_value_off_ui_thread(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.observe_worker import create_callable_worker
        main_ident = threading.get_ident()
        worker = create_callable_worker(
            lambda: (threading.get_ident(), 42))
        try:
            worker.start()
            fired = _wait_for(worker.callable_finished, QtCore)
            worker.wait(5000)
            self.assertIsNotNone(fired)
            ident, value = fired[0]
            self.assertEqual(value, 42)
            self.assertNotEqual(ident, main_ident)
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def test_error_and_cancel(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.observe_worker import create_callable_worker

        def _boom():
            raise RuntimeError("chore failed")

        worker = create_callable_worker(_boom)
        try:
            worker.start()
            fired = _wait_for(worker.callable_error, QtCore)
            worker.wait(5000)
            self.assertIn("chore failed", fired[0])
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass
        worker2 = create_callable_worker(lambda: "stale")
        try:
            worker2.cancel()
            worker2.start()
            fired = _wait_for(worker2.callable_cancelled, QtCore)
            worker2.wait(5000)
            self.assertIsNotNone(fired)
        finally:
            try:
                worker2.deleteLater()
            except RuntimeError:
                pass


class WatchAsyncQtTest(unittest.TestCase):
    def _controller(self, state, interval_ms=60000):
        from desktop_app.watch import WatchController
        controller = WatchController(state, status=None,
                                     interval_ms=interval_ms)
        controller.repo = "."
        controller.roots = ["."]
        controller.max_commits = 5
        return controller

    def test_poll_returns_without_blocking(self):
        _needs_qt(self)
        ticks = []
        state = _FakeState(episodes=["a", "b"], delay=0.4)
        controller = self._controller(state)
        controller.on_tick = ticks.append
        controller._timer.start()  # watching without the sync first pass
        try:
            started = time.monotonic()
            controller._poll()
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.35)  # 0.4s pass runs elsewhere
            self.assertTrue(controller.poll_running)
            self.assertTrue(controller.wait_for_idle(15.0))
            self.assertEqual(ticks, [2])
            self.assertFalse(controller.poll_running)
        finally:
            controller.stop()

    def test_ticks_coalesce_mid_pass(self):
        _needs_qt(self)
        state = _FakeState(episodes=["a"], delay=0.4)
        controller = self._controller(state)
        controller._timer.start()
        try:
            controller._poll()
            controller._poll()  # coalesces into one follow-up
            controller._poll()  # collapses (noop)
            self.assertTrue(controller.wait_for_idle(15.0))
            # One running pass plus exactly one drained follow-up.
            self.assertEqual(len(state.calls), 2)
        finally:
            controller.stop()

    def test_failed_pass_announces(self):
        _needs_qt(self)
        messages = []

        class _Status:
            def showMessage(self, text, _ms):
                messages.append(text)

        state = _FakeState(error=RuntimeError("repo gone"))
        controller = self._controller(state)
        controller.status = _Status()
        controller._timer.start()
        try:
            controller._poll()
            self.assertTrue(controller.wait_for_idle(15.0))
            self.assertTrue(any("repo gone" in m for m in messages))
        finally:
            controller.stop()

    def test_sync_mode_still_available(self):
        _needs_qt(self)
        from desktop_app.watch import WatchController
        ticks = []
        state = _FakeState(episodes=["a"])
        controller = WatchController(state, status=None,
                                     interval_ms=60000, use_worker=False)
        controller.on_tick = ticks.append
        controller.repo = "."
        controller.roots = ["."]
        try:
            controller._poll()
            self.assertEqual(ticks, [1])
            self.assertFalse(controller.poll_running)
        finally:
            controller.stop()

    def test_stop_cancels_in_flight(self):
        _needs_qt(self)
        ticks = []
        state = _FakeState(episodes=["a"], delay=0.5)
        controller = self._controller(state)
        controller.on_tick = ticks.append
        controller._timer.start()
        try:
            controller._poll()
            in_flight = controller._worker
            controller.stop()
            self.assertFalse(controller.poll_running)
            controller.wait_for_idle(15.0)
            self.assertEqual(ticks, [])  # late result dropped
            # Join the retired thread before teardown: destroying a
            # running QThread aborts the process.
            if in_flight is not None:
                in_flight.wait(15000)
        finally:
            controller.stop()


class VoiceCancelQtTest(unittest.TestCase):
    def test_speak_cancel_before_start(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.voice_worker import create_speak_worker
        from mimir.voice import VoiceCoordinator
        spoken = []
        voice = VoiceCoordinator(
            stt_backend=lambda timeout: None,
            tts_backend=spoken.append,
        )
        worker = create_speak_worker(voice, "stale line")
        try:
            worker.cancel()
            worker.start()
            fired = _wait_for(worker.speak_cancelled, QtCore)
            worker.wait(5000)
            self.assertIsNotNone(fired)
            self.assertEqual(spoken, [])
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def test_listen_cancel_before_start(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.voice_worker import create_listen_worker
        from mimir.voice import VoiceCoordinator
        voice = VoiceCoordinator(
            stt_backend=lambda timeout: "hello",
            tts_backend=lambda text: None,
        )
        worker = create_listen_worker(voice, timeout_sec=1.0)
        try:
            worker.cancel()
            worker.start()
            fired = _wait_for(worker.listen_cancelled, QtCore)
            worker.wait(5000)
            self.assertIsNotNone(fired)
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass


class ReplicateWorkerQtTest(unittest.TestCase):
    def test_offline_synthesis_off_ui_thread(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.replicate_worker import create_replicate_worker
        worker = create_replicate_worker(
            "Draft a commit helper", [_principle().to_dict()],
            llm_prefs={"model_provider": "offline",
                       "contrast_baseline": True},
        )
        try:
            worker.start()
            fired = _wait_for(worker.replicate_finished, QtCore,
                              timeout_ms=30000)
            worker.wait(10000)
            self.assertIsNotNone(fired)
            payload = fired[0]
            self.assertFalse(payload["fell_back"])
            result = payload["result"]
            self.assertTrue(result.replicated_solution.strip())
            # The private temp store was seeded from the snapshot.
            self.assertEqual(
                [p.id for p in result.applied_principles],
                ["bp_iso_001"])
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def test_live_failure_falls_back_offline(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.replicate_worker import create_replicate_worker

        def _dead_llm(prompt):
            raise RuntimeError("provider down")

        worker = create_replicate_worker(
            "Draft a commit helper", [_principle().to_dict()],
            llm_fn=_dead_llm,
        )
        try:
            worker.start()
            fired = _wait_for(worker.replicate_finished, QtCore,
                              timeout_ms=30000)
            worker.wait(10000)
            self.assertIsNotNone(fired)
            payload = fired[0]
            self.assertTrue(payload["fell_back"])
            self.assertIn("provider down", payload["live_error"])
            self.assertTrue(
                payload["result"].replicated_solution.strip())
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def test_fake_llm_solution_used(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.replicate_worker import create_replicate_worker
        main_ident = threading.get_ident()
        seen = {}

        def _probing_llm(prompt):
            seen["ident"] = threading.get_ident()
            return "CANNED LIVE SOLUTION"

        worker = create_replicate_worker(
            "Draft a commit helper", [_principle().to_dict()],
            llm_fn=_probing_llm,
        )
        try:
            worker.start()
            fired = _wait_for(worker.replicate_finished, QtCore,
                              timeout_ms=30000)
            worker.wait(10000)
            self.assertIsNotNone(fired)
            self.assertIn("CANNED LIVE SOLUTION",
                          fired[0]["result"].replicated_solution)
            self.assertFalse(fired[0]["fell_back"])
            # Synthesis (the LLM call) ran off the UI thread.
            self.assertNotEqual(seen.get("ident"), main_ident)
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def test_synthesis_timeout_errors(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.replicate_worker import create_replicate_worker

        def _slow_llm(prompt):
            time.sleep(5.0)
            return "too late"

        worker = create_replicate_worker(
            "Draft a commit helper", [], llm_fn=_slow_llm, timeout_s=0.3,
        )
        try:
            started = time.monotonic()
            worker.start()
            fired = _wait_for(worker.replicate_error, QtCore,
                              timeout_ms=15000)
            worker.wait(10000)
            self.assertIsNotNone(fired)
            self.assertIn("timed out", fired[0])
            self.assertLess(time.monotonic() - started, 12.0)
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def test_cancel_before_start(self):
        QtCore, _, _ = _needs_qt(self)
        from desktop_app.replicate_worker import create_replicate_worker
        worker = create_replicate_worker("Draft x", [],
                                         llm_fn=lambda p: "live")
        try:
            worker.cancel()
            self.assertTrue(worker.is_cancelled)
            worker.start()
            fired = _wait_for(worker.replicate_cancelled, QtCore)
            worker.wait(10000)
            self.assertIsNotNone(fired)
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def test_bad_worker_rejected(self):
        _needs_qt(self)
        from desktop_app.replicate_worker import create_replicate_worker
        with self.assertRaises(ValueError):
            create_replicate_worker("  ", [])
        with self.assertRaises(ValueError):
            create_replicate_worker("task", ["not-a-dict"])
        with self.assertRaises(ValueError):
            create_replicate_worker("task", [], top_k=0)
        with self.assertRaises(ValueError):
            create_replicate_worker("task", [], min_confidence=9.0)
        with self.assertRaises(ValueError):
            create_replicate_worker("task", [], timeout_s=0)


class ReplicateTabAsyncQtTest(unittest.TestCase):
    def test_generate_runs_off_ui_thread_and_renders(self):
        _needs_qt(self)
        from desktop_app.replicate_worker import ReplicateWorker
        from desktop_app.state import AppState
        from desktop_app.tabs.replicate import build_replicate_tab

        class _Status:
            def showMessage(self, *args):
                pass

        state = AppState(store_path=":memory:")
        try:
            state.store.save(_principle())
            tab = build_replicate_tab(state, _Status())
            try:
                from desktop_app.tabs._qt import load_qt
                _, _, _, QtWidgets = load_qt()
                edits = tab.findChildren(QtWidgets.QTextEdit)
                task_edit = next(
                    e for e in edits
                    if e.objectName() == "task_input")
                task_edit.setPlainText("Draft a commit helper")
                generate = next(
                    b for b in tab.findChildren(QtWidgets.QPushButton)
                    if b.text() == "Generate \u2192")
                outputs = tab.findChildren(QtWidgets.QPlainTextEdit)
                self.assertTrue(outputs)
                output = outputs[0]
                generate.click()
                # The click returns with synthesis in flight on a
                # worker thread — the UI thread only renders.
                workers = tab.findChildren(ReplicateWorker)
                self.assertEqual(len(workers), 1)
                self.assertFalse(generate.isEnabled())
                deadline = time.monotonic() + 30.0
                while (not output.toPlainText().strip()
                       and time.monotonic() < deadline):
                    QtWidgets.QApplication.processEvents()
                    time.sleep(0.02)
                QtWidgets.QApplication.processEvents()
                self.assertTrue(output.toPlainText().strip())
                self.assertTrue(generate.isEnabled())
            finally:
                tab.close()
                tab.deleteLater()
        finally:
            state.close()


if __name__ == "__main__":
    unittest.main()
