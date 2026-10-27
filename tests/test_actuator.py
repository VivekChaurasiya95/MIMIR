"""Tests for the compliant desktop actuator (Day 7).

Everything runs headless: injected fake backends record motion and keys,
real timing relies on interruptible waits (never wall-clock exactness),
and subprocess tests use the current Python interpreter for portability.
"""

import builtins
import os
import sys
import threading
import time
import unittest

from mimir.actuator import (
    ActuatorError,
    CobotInterruptedError,
    CursorPoint,
    DefaultFailsafe,
    DefaultKeyboard,
    DefaultMouse,
    DesktopActuator,
    ExecutionPlan,
    plan_keystroke_delays,
    plan_trajectory,
    split_command_line,
)


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
    def __init__(self):
        self.armed_with = None
        self.disarmed = 0
        self.commanded: list[tuple[int, int]] = []

    def arm(self, on_trip):
        self.armed_with = on_trip

    def disarm(self):
        self.disarmed += 1

    def note_commanded(self, x, y):
        self.commanded.append((x, y))

    def simulate_trip(self):
        self.armed_with()


def _actuator(**overrides):
    parts = {"mouse": FakeMouse(), "keyboard": FakeKeyboard(),
             "failsafe": FakeFailsafe()}
    parts.update(overrides)
    fired: list[bool] = []

    def on_override():
        fired.append(True)

    actor = DesktopActuator(on_override, **parts)
    return actor, parts, fired


class KinematicsTest(unittest.TestCase):
    def test_bezier_smooth_monotonic_easing(self):
        points = plan_trajectory(CursorPoint(0, 0), CursorPoint(100, 50), 10)
        self.assertEqual(len(points), 11)
        self.assertEqual(points[0], CursorPoint(0, 0))
        self.assertEqual(points[-1], CursorPoint(100, 50))
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        self.assertEqual(xs, sorted(xs))
        self.assertEqual(ys, sorted(ys))
        mid = points[5]
        self.assertTrue(abs(mid.x - 50) <= 2, mid)
        self.assertTrue(abs(mid.y - 25) <= 2, mid)
        jumps = [
            abs(b.x - a.x) + abs(b.y - a.y)
            for a, b in zip(points, points[1:])
        ]
        self.assertLessEqual(max(jumps), 16)

    def test_degenerate_and_single_step(self):
        same = plan_trajectory(CursorPoint(7, 7), CursorPoint(7, 7), 4)
        self.assertTrue(all(p == CursorPoint(7, 7) for p in same))
        two = plan_trajectory(CursorPoint(0, 0), CursorPoint(3, 4), 1)
        self.assertEqual(two, (CursorPoint(0, 0), CursorPoint(3, 4)))

    def test_contracts_round_trip_and_reject(self):
        point = CursorPoint.from_dict({"x": 3, "y": 4})
        self.assertEqual(point.to_dict(), {"x": 3, "y": 4})
        plan = ExecutionPlan.from_dict(
            {"actions": ["run tests"], "target_window": "term",
             "terminal_command": "git status"}
        )
        self.assertEqual(plan.to_dict()["actions"], ["run tests"])
        with self.assertRaises(ValueError):
            CursorPoint(-1, 0)
        with self.assertRaises(ValueError):
            plan_trajectory(CursorPoint(0, 0), CursorPoint(1, 1), 0)
        with self.assertRaises(ValueError):
            plan_trajectory("a", CursorPoint(1, 1), 2)
        with self.assertRaises(ValueError):
            ExecutionPlan(actions=["  "])


class RhythmTest(unittest.TestCase):
    def test_syntactic_pauses_ordered(self):
        delays = plan_keystroke_delays("a,b\nc(d", 70)
        self.assertEqual(len(delays), 7)
        base = 60.0 / (70 * 5.0)
        self.assertTrue(0.8 * base < delays[0] < 1.2 * base)
        self.assertGreater(delays[1], delays[0])  # after ','
        self.assertGreater(delays[3], delays[1])  # after '\n'
        self.assertGreater(delays[5], delays[0])  # after '('
        self.assertTrue(all(d > 0 for d in delays))

    def test_deterministic_and_empty(self):
        self.assertEqual(
            plan_keystroke_delays("hello, world", 70),
            plan_keystroke_delays("hello, world", 70),
        )
        self.assertEqual(plan_keystroke_delays("", 70), ())

    def test_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            plan_keystroke_delays(123, 70)
        with self.assertRaises(ValueError):
            plan_keystroke_delays("hi", 0)


class InterruptTest(unittest.TestCase):
    def test_preaborted_move_touches_nothing(self):
        actor, parts, _ = _actuator()
        actor.abort()
        with self.assertRaises(CobotInterruptedError):
            actor.move_cursor_smooth(CursorPoint(10, 10))
        self.assertEqual(parts["mouse"].moves, [])

    def test_midflight_move_halts_fast(self):
        actor, parts, _ = _actuator()
        timer = threading.Timer(0.02, actor.abort)
        timer.start()
        try:
            started = time.monotonic()
            with self.assertRaises(CobotInterruptedError):
                actor.move_cursor_smooth(
                    CursorPoint(500, 500), duration_sec=60.0, steps=30
                )
            elapsed = time.monotonic() - started
        finally:
            timer.cancel()
        # Interruptible waits wake in milliseconds; plain 2 s step sleeps
        # would take the full minute. Bound keeps 50x slack for loaded CI.
        self.assertLess(elapsed, 1.0)
        self.assertLess(len(parts["mouse"].moves), 30)

    def test_midflight_typing_halts_fast(self):
        actor, parts, _ = _actuator()
        timer = threading.Timer(0.02, actor.abort)
        timer.start()
        try:
            started = time.monotonic()
            with self.assertRaises(CobotInterruptedError):
                actor.type_natural("abcdefgh", base_wpm=1)
            elapsed = time.monotonic() - started
        finally:
            timer.cancel()
        self.assertLess(elapsed, 1.0)
        self.assertLessEqual(len(parts["keyboard"].chars), 2)

    def test_failsafe_trip_fires_callback_once(self):
        actor, parts, fired = _actuator()
        self.assertTrue(actor.failsafe_armed)
        parts["failsafe"].simulate_trip()
        self.assertTrue(actor.interrupted)
        self.assertEqual(len(fired), 1)
        actor.abort()
        self.assertEqual(len(fired), 1)
        actor.reset()
        self.assertFalse(actor.interrupted)
        actor.abort()
        self.assertEqual(len(fired), 2)

    def test_broken_failsafe_degrades_gracefully(self):
        class ExplodingFailsafe:
            def arm(self, on_trip):
                raise RuntimeError("no listener support")

            def disarm(self):
                pass

        actor, _, _ = _actuator(failsafe=ExplodingFailsafe())
        self.assertFalse(actor.failsafe_armed)
        actor.abort()  # manual trip always available
        self.assertTrue(actor.interrupted)
        with DesktopActuator() as defaulted:
            self.assertIsInstance(defaulted.failsafe_armed, bool)
            self.assertFalse(defaulted.interrupted)

    def test_click_point_sequence(self):
        actor, parts, _ = _actuator()
        actor.click_point(CursorPoint(20, 30))
        self.assertEqual(parts["mouse"].pos, (20, 30))
        self.assertEqual(parts["mouse"].clicks, 1)
        self.assertGreater(len(parts["mouse"].moves), 0)

    def test_cursor_position(self):
        actor, _, _ = _actuator()
        self.assertEqual(actor.cursor_position(), CursorPoint(0, 0))
        actor.move_cursor_smooth(
            CursorPoint(20, 30), duration_sec=0.01, steps=2
        )
        self.assertEqual(actor.cursor_position(), CursorPoint(20, 30))

    def test_backend_failures_raise_actuator_error(self):
        class ExplodingMouse(FakeMouse):
            def move_to(self, x, y):
                raise RuntimeError("usb unplugged")

        actor, _, _ = _actuator(mouse=ExplodingMouse())
        with self.assertRaises(ActuatorError):
            actor.move_cursor_smooth(CursorPoint(5, 5))

        class BadPositionMouse(FakeMouse):
            def position(self):
                return "somewhere"

        actor2, _, _ = _actuator(mouse=BadPositionMouse())
        with self.assertRaises(ActuatorError):
            actor2.move_cursor_smooth(CursorPoint(5, 5))

    def test_move_rejects_bad_inputs(self):
        actor, _, _ = _actuator()
        with self.assertRaises(ValueError):
            actor.move_cursor_smooth("elsewhere")
        with self.assertRaises(ValueError):
            actor.move_cursor_smooth(CursorPoint(1, 1), duration_sec=0)
        with self.assertRaises(ValueError):
            actor.type_natural("  ")


class OptionalDependencyTest(unittest.TestCase):
    def _block_optional_imports(self):
        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == "pyautogui" or name.startswith("pynput"):
                raise ImportError(f"blocked for test: {name}")
            return real_import(name, *args, **kwargs)

        return real_import, blocked

    def test_missing_backends_raise_with_install_hint(self):
        real_import, blocked = self._block_optional_imports()
        builtins.__import__ = blocked
        try:
            with self.assertRaisesRegex(ActuatorError, "pyautogui"):
                DefaultMouse().position()
            with self.assertRaisesRegex(ActuatorError, "pynput"):
                DefaultKeyboard().write_char("a")
            with self.assertRaisesRegex(ActuatorError, "pynput"):
                DefaultFailsafe().arm(lambda: None)
        finally:
            builtins.__import__ = real_import


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.actor, _, _ = _actuator()

    def test_captures_stdout_stderr_and_code(self):
        code, out, err = self.actor.run_safe_command(
            [sys.executable, "-c", "print('hi')"]
        )
        self.assertEqual((code, out, err), (0, "hi\n", ""))
        code, _, _ = self.actor.run_safe_command(
            [sys.executable, "-c", "import sys; sys.exit(3)"]
        )
        self.assertEqual(code, 3)
        _, _, err = self.actor.run_safe_command(
            [sys.executable, "-c", "import sys; sys.stderr.write('oops')"]
        )
        self.assertEqual(err, "oops")

    def test_timeout_kills_and_raises(self):
        started = time.monotonic()
        with self.assertRaises(ActuatorError):
            self.actor.run_safe_command(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                timeout_sec=0.2,
            )
        self.assertLess(time.monotonic() - started, 10.0)

    def test_interrupt_kills_and_raises(self):
        timer = threading.Timer(0.2, self.actor.abort)
        timer.start()
        try:
            started = time.monotonic()
            with self.assertRaises(CobotInterruptedError):
                self.actor.run_safe_command(
                    [sys.executable, "-c", "import time; time.sleep(30)"]
                )
            elapsed = time.monotonic() - started
        finally:
            timer.cancel()
        self.assertLess(elapsed, 10.0)

    def test_rejects_bad_commands(self):
        with self.assertRaises(ValueError):
            self.actor.run_safe_command([])
        with self.assertRaises(ValueError):
            self.actor.run_safe_command("not-a-list")
        with self.assertRaises(ValueError):
            self.actor.run_safe_command([sys.executable], timeout_sec=0)
        with self.assertRaises(ActuatorError):
            self.actor.run_safe_command(["mimir-no-such-binary-xyz"])
        with self.assertRaises(ActuatorError):
            self.actor.run_safe_command(
                [sys.executable, "-c", "pass"], cwd="/no/such/dir/xyz"
            )

    def test_split_command_line(self):
        self.assertEqual(
            split_command_line('"C:/Tools/run" --flag "a b"'),
            ["C:/Tools/run", "--flag", "a b"],
        )
        self.assertEqual(split_command_line("git status"), ["git", "status"])
        with self.assertRaises(ValueError):
            split_command_line('"unbalanced')
        if os.name == "nt":
            # Windows backslashes stay literal (POSIX shlex would escape).
            self.assertEqual(
                split_command_line('"C:\\Tools\\run.exe" --flag "a b"'),
                ["C:\\Tools\\run.exe", "--flag", "a b"],
            )

    def test_execute_plan(self):
        report = self.actor.execute_plan(
            ExecutionPlan(
                actions=["run tests", "stage commit"],
                target_window="terminal",
                terminal_command=f'"{sys.executable}" -c "print(40 + 2)"',
            )
        )
        self.assertEqual(report["terminal_exit"], 0)
        self.assertEqual(report["terminal_stdout"], "42\n")
        self.assertEqual(report["actions"], ["run tests", "stage commit"])
        self.assertEqual(report["target_window"], "terminal")
        bare = self.actor.execute_plan(ExecutionPlan(actions=["observe"]))
        self.assertIsNone(bare["terminal_exit"])
        with self.assertRaises(ValueError):
            self.actor.execute_plan({"actions": []})


if __name__ == "__main__":
    unittest.main()
