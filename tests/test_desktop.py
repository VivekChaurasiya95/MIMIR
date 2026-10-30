"""Tests for desktop target management (improvement step 4).

Everything runs headless: a fake window backend stands in for pywin32,
SendInput tests inject fake sender/metrics callables (no real cursor or
keystroke injection ever happens here), and Qt worker tests run
offscreen-or-skip following the tests/test_phase4.py pattern.
"""

import os
import sys
import unittest

from mimir.actuator import (
    ActuatorError,
    CursorPoint,
    DesktopActuator,
    ExecutionPlan,
    TargetValidationError,
)
from mimir.desktop import (
    FocusGuard,
    PyWin32WindowBackend,
    SendInputKeyboard,
    SendInputMouse,
    TargetSpec,
    WindowInfo,
    resolve_target,
    validate_target,
    verify_completion,
    verify_cursor_at,
    verify_foreground,
)
from mimir.events import EventBus, EventType


class FakeWindowBackend:
    """In-memory WindowBackend with scripted activation/foreground."""

    def __init__(self, windows, foreground_handle=None, activate_ok=True):
        self._windows = {w.handle: w for w in windows}
        self._fg = foreground_handle
        self.activate_calls: list[int] = []
        if callable(activate_ok):
            self._accept = activate_ok
        else:
            self._accept = lambda h: bool(activate_ok)
        self.foreground_script: list = []

    def list_windows(self):
        return [self._windows[h] for h in sorted(self._windows)]

    def foreground(self):
        if self.foreground_script:
            handle = self.foreground_script.pop(0)
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


def _windows():
    return [
        WindowInfo(handle=11, process_id=100,
                   executable="C:\\Apps\\Companion.exe", title="MIMIR"),
        WindowInfo(handle=22, process_id=200,
                   executable="C:\\Apps\\Editor.exe", title="notes.txt"),
        WindowInfo(handle=33, process_id=300,
                   executable="C:\\Apps\\Browser.exe", title="Docs"),
    ]


def _backend(**overrides):
    parts = {"windows": _windows(), "foreground_handle": 11}
    parts.update(overrides)
    return FakeWindowBackend(**parts)


class FakeMouse:
    def __init__(self, start=(0, 0)):
        self.pos = start
        self.moves: list[tuple[int, int]] = []

    def position(self):
        return self.pos

    def move_to(self, x, y):
        self.moves.append((x, y))
        self.pos = (x, y)

    def click(self):
        pass


class FakeKeyboard:
    def __init__(self):
        self.chars: list[str] = []

    def write_char(self, char):
        self.chars.append(char)


class FakeFailsafe:
    def arm(self, on_trip):
        pass

    def disarm(self):
        pass


def _actuator(windows=None, **overrides):
    parts = {"mouse": FakeMouse(), "keyboard": FakeKeyboard(),
             "failsafe": FakeFailsafe(),
             "windows": windows if windows is not None else _backend()}
    parts.update(overrides)
    actor = DesktopActuator(**parts)
    return actor, parts


class ContractTest(unittest.TestCase):
    def test_window_info_round_trip(self):
        info = WindowInfo.from_dict(
            {"handle": 7, "process_id": 9,
             "executable": "a.exe", "title": "t"})
        self.assertEqual(info.to_dict()["handle"], 7)
        minimal = WindowInfo.from_dict({"handle": 1, "process_id": 0})
        self.assertEqual(minimal.executable, "")
        self.assertEqual(minimal.title, "")

    def test_window_info_rejects(self):
        with self.assertRaises(ValueError):
            WindowInfo(handle=0, process_id=1)
        with self.assertRaises(ValueError):
            WindowInfo(handle=1, process_id=-1)
        with self.assertRaises(ValueError):
            WindowInfo.from_dict({"handle": 1})
        with self.assertRaises(ValueError):
            WindowInfo.from_dict(
                {"handle": 1, "process_id": 2, "bogus": 3})

    def test_target_spec_round_trip(self):
        spec = TargetSpec.from_dict(
            {"executable": "Editor", "title_substring": "notes"})
        self.assertEqual(spec.to_dict()["executable"], "Editor")

    def test_target_spec_requires_a_selector(self):
        with self.assertRaises(ValueError):
            TargetSpec()
        with self.assertRaises(ValueError):
            TargetSpec.from_dict({})
        with self.assertRaises(ValueError):
            TargetSpec(handle=0)
        with self.assertRaises(ValueError):
            TargetSpec(title_substring="  ")

    def test_validation_error_is_actuator_error_with_reason(self):
        err = TargetValidationError("nope", reason="ambiguous")
        self.assertIsInstance(err, ActuatorError)
        self.assertEqual(err.reason, "ambiguous")
        self.assertEqual(TargetValidationError("x").reason,
                         "validation_failed")


class ResolveTest(unittest.TestCase):
    def test_by_handle(self):
        info = resolve_target(_backend(), TargetSpec(handle=22))
        self.assertEqual(info.title, "notes.txt")

    def test_by_title_substring_case_insensitive(self):
        info = resolve_target(_backend(), "NOTES")
        self.assertEqual(info.handle, 22)

    def test_by_executable_and_process_id(self):
        info = resolve_target(
            _backend(), TargetSpec(executable="browser.exe"))
        self.assertEqual(info.handle, 33)
        info = resolve_target(_backend(), TargetSpec(process_id=100))
        self.assertEqual(info.handle, 11)

    def test_no_match(self):
        with self.assertRaises(TargetValidationError) as ctx:
            resolve_target(_backend(), "no such window")
        self.assertEqual(ctx.exception.reason, "no_match")

    def test_ambiguous(self):
        backend = _backend(windows=[
            WindowInfo(handle=1, process_id=1, title="doc - a"),
            WindowInfo(handle=2, process_id=2, title="doc - b"),
        ])
        with self.assertRaises(TargetValidationError) as ctx:
            resolve_target(backend, "doc")
        self.assertEqual(ctx.exception.reason, "ambiguous")

    def test_unknown_handle(self):
        with self.assertRaises(TargetValidationError) as ctx:
            resolve_target(_backend(), TargetSpec(handle=999))
        self.assertEqual(ctx.exception.reason, "unknown_handle")

    def test_spec_mismatch_handle_vs_selectors(self):
        with self.assertRaises(TargetValidationError) as ctx:
            resolve_target(
                _backend(),
                TargetSpec(handle=22, executable="Browser.exe"))
        self.assertEqual(ctx.exception.reason, "spec_mismatch")

    def test_backend_error_wraps_enumeration_failure(self):
        class _Broken:
            def list_windows(self):
                raise RuntimeError("boom")

            def foreground(self):
                return None

            def is_valid(self, handle):
                return False

            def activate(self, handle):
                return False

        with self.assertRaises(TargetValidationError) as ctx:
            resolve_target(_Broken(), "x")
        self.assertEqual(ctx.exception.reason, "backend_error")

    def test_rejects_bad_spec_type(self):
        with self.assertRaises(ValueError):
            resolve_target(_backend(), 42)

    def test_failure_emits_event(self):
        bus = EventBus()
        seen = []
        bus.subscribe(EventType.TARGET_VALIDATION_FAILED,
                      handler=seen.append)
        with self.assertRaises(TargetValidationError):
            resolve_target(_backend(), "missing", event_bus=bus,
                           operation="op-1")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].payload["reason"], "no_match")
        self.assertEqual(seen[0].payload["operation"], "op-1")


class ValidateTest(unittest.TestCase):
    def test_ok_returns_live_snapshot(self):
        backend = _backend()
        expected = WindowInfo(handle=22, process_id=200,
                              executable="C:\\Apps\\Editor.exe",
                              title="notes.txt")
        backend.activate(22)
        live = validate_target(backend, expected)
        self.assertEqual(live.handle, 22)

    def test_title_drift_is_not_fatal(self):
        backend = _backend()
        backend.activate(22)
        backend.retarget(22, title="notes.txt*")
        live = validate_target(
            backend,
            WindowInfo(handle=22, process_id=200,
                       executable="C:\\Apps\\Editor.exe",
                       title="notes.txt"))
        self.assertEqual(live.title, "notes.txt*")

    def test_unauthorized_is_fail_closed(self):
        backend = _backend()
        backend.activate(22)
        with self.assertRaises(TargetValidationError) as ctx:
            validate_target(backend, _windows()[1], authorized=False,
                            operation="drive_text")
        self.assertEqual(ctx.exception.reason, "unauthorized")

    def test_unknown_handle(self):
        with self.assertRaises(TargetValidationError) as ctx:
            validate_target(_backend(), WindowInfo(handle=999,
                                                   process_id=1))
        self.assertEqual(ctx.exception.reason, "unknown_handle")

    def test_target_changed_process(self):
        backend = _backend()
        backend.activate(22)
        backend.retarget(22, process_id=201)
        with self.assertRaises(TargetValidationError) as ctx:
            validate_target(backend, _windows()[1])
        self.assertEqual(ctx.exception.reason, "target_changed")

    def test_target_changed_app(self):
        backend = _backend()
        backend.activate(22)
        backend.retarget(22, executable="C:\\Apps\\Other.exe")
        with self.assertRaises(TargetValidationError) as ctx:
            validate_target(backend, _windows()[1])
        self.assertEqual(ctx.exception.reason, "target_changed")

    def test_foreground_mismatch(self):
        backend = _backend()  # foreground stays on the companion (11)
        with self.assertRaises(TargetValidationError) as ctx:
            validate_target(backend, _windows()[1])
        self.assertEqual(ctx.exception.reason, "foreground_mismatch")

    def test_no_foreground(self):
        backend = _backend(foreground_handle=None)
        with self.assertRaises(TargetValidationError) as ctx:
            validate_target(backend, _windows()[0])
        self.assertEqual(ctx.exception.reason, "no_foreground")

    def test_require_foreground_false_skips_focus_check(self):
        live = validate_target(_backend(), _windows()[1],
                               require_foreground=False)
        self.assertEqual(live.handle, 22)

    def test_emits_failure_event_with_expected_snapshot(self):
        bus = EventBus()
        seen = []
        bus.subscribe(EventType.TARGET_VALIDATION_FAILED,
                      handler=seen.append)
        with self.assertRaises(TargetValidationError):
            validate_target(_backend(), _windows()[1], event_bus=bus,
                            operation="drive_text")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].payload["reason"], "foreground_mismatch")
        self.assertEqual(seen[0].payload["expected"]["handle"], 22)

    def test_no_bus_never_breaks_validation(self):
        backend = _backend()
        backend.activate(33)
        live = validate_target(backend, _windows()[2], event_bus=None)
        self.assertEqual(live.handle, 33)


class FocusGuardTest(unittest.TestCase):
    def test_full_cycle_restores_companion(self):
        backend = _backend()
        with FocusGuard(backend, 22) as guard:
            self.assertEqual(guard.companion_handle, 11)
            self.assertTrue(guard.activated)
            self.assertTrue(guard.verified)
            self.assertEqual(backend.foreground().handle, 22)
        self.assertEqual(backend.foreground().handle, 11)
        self.assertEqual(backend.activate_calls, [22, 11])

    def test_step_methods(self):
        backend = _backend()
        guard = FocusGuard(backend, 22)
        self.assertEqual(guard.preserve(), 11)
        self.assertTrue(guard.activate())
        self.assertEqual(guard.verify().handle, 22)
        self.assertTrue(guard.restore())
        self.assertEqual(backend.foreground().handle, 11)

    def test_activation_denied_raises_and_emits(self):
        bus = EventBus()
        seen = []
        bus.subscribe(EventType.TARGET_VALIDATION_FAILED,
                      handler=seen.append)
        backend = _backend(activate_ok=False)
        with self.assertRaises(TargetValidationError) as ctx:
            with FocusGuard(backend, 22, event_bus=bus):
                pass
        self.assertEqual(ctx.exception.reason, "activation_denied")
        self.assertEqual(seen[0].payload["reason"], "activation_denied")
        # Companion focus is untouched on a denied switch.
        self.assertEqual(backend.foreground().handle, 11)

    def test_verify_mismatch_raises(self):
        backend = _backend()
        backend.activate(22)
        backend.foreground_script = [33]  # focus slips away
        guard = FocusGuard(backend, 22)
        with self.assertRaises(TargetValidationError) as ctx:
            guard.verify()
        self.assertEqual(ctx.exception.reason, "foreground_mismatch")

    def test_no_companion_when_target_already_foreground(self):
        backend = _backend(foreground_handle=22)
        with FocusGuard(backend, 22) as guard:
            self.assertIsNone(guard.companion_handle)
        self.assertEqual(backend.foreground().handle, 22)

    def test_restore_when_companion_closed_never_raises(self):
        backend = _backend()
        guard = FocusGuard(backend, 22)
        guard.preserve()
        guard.activate()
        backend.close(11)
        self.assertTrue(guard.restore())

    def test_preserve_false_skips_restore(self):
        backend = _backend()
        with FocusGuard(backend, 22, preserve=False) as guard:
            self.assertIsNone(guard.companion_handle)
        self.assertEqual(backend.foreground().handle, 22)

    def test_exit_restores_even_on_body_error(self):
        backend = _backend()
        with self.assertRaises(RuntimeError):
            with FocusGuard(backend, 22):
                raise RuntimeError("body failed")
        self.assertEqual(backend.foreground().handle, 11)

    def test_exploding_backend_restore_never_raises(self):
        class _Exploding(FakeWindowBackend):
            def activate(self, handle):
                raise RuntimeError("nope")

            def foreground(self):
                raise RuntimeError("nope")

        backend = _Exploding(_windows(), 11)
        guard = FocusGuard(backend, 22)
        guard.preserve()
        self.assertFalse(guard.restore())


class SendInputTest(unittest.TestCase):
    def _recorder(self, metrics=(1920, 1080)):
        calls: list = []

        def sender(events):
            calls.append(list(events))
            return len(events)

        return sender, calls, (lambda: metrics)

    def test_move_normalizes_to_absolute_coordinates(self):
        from mimir.desktop import _MOUSEEVENTF_ABSOLUTE, _MOUSEEVENTF_MOVE
        sender, calls, metrics = self._recorder()
        mouse = SendInputMouse(sender=sender, metrics=metrics)
        mouse.move_to(960, 540)
        self.assertEqual(len(calls), 1)
        (event,) = calls[0]
        self.assertEqual(event.type, 0)
        self.assertEqual(event.u.mi.dx, round(960 * 65535 / 1919))
        self.assertEqual(event.u.mi.dy, round(540 * 65535 / 1079))
        self.assertEqual(event.u.mi.dwFlags,
                         _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE)

    def test_click_sends_down_then_up(self):
        from mimir.desktop import (
            _MOUSEEVENTF_LEFTDOWN,
            _MOUSEEVENTF_LEFTUP,
        )
        sender, calls, metrics = self._recorder()
        SendInputMouse(sender=sender, metrics=metrics).click()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]), 2)
        self.assertEqual(calls[0][0].u.mi.dwFlags, _MOUSEEVENTF_LEFTDOWN)
        self.assertEqual(calls[0][1].u.mi.dwFlags, _MOUSEEVENTF_LEFTUP)

    def test_write_char_sends_unicode_down_up(self):
        from mimir.desktop import _KEYEVENTF_KEYUP, _KEYEVENTF_UNICODE
        sender, calls, _ = self._recorder()
        SendInputKeyboard(sender=sender).write_char("A")
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]), 2)
        down, up = calls[0]
        self.assertEqual(down.type, 1)
        self.assertEqual(down.u.ki.wScan, ord("A"))
        self.assertEqual(down.u.ki.dwFlags, _KEYEVENTF_UNICODE)
        self.assertEqual(up.u.ki.dwFlags,
                         _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP)

    def test_write_char_supplementary_plane_uses_surrogates(self):
        sender, calls, _ = self._recorder()
        SendInputKeyboard(sender=sender).write_char("\U0001F600")
        self.assertEqual(len(calls[0]), 4)  # 2 units x down/up
        scans = [e.u.ki.wScan for e in calls[0][::2]]
        self.assertEqual(scans, [0xD83D, 0xDE00])

    def test_short_injection_count_raises(self):
        mouse = SendInputMouse(sender=lambda events: 0,
                               metrics=lambda: (1920, 1080))
        with self.assertRaises(ActuatorError):
            mouse.move_to(5, 5)
        with self.assertRaises(ActuatorError):
            mouse.click()
        keys = SendInputKeyboard(sender=lambda events: 0)
        with self.assertRaises(ActuatorError):
            keys.write_char("x")

    def test_sender_exception_wraps(self):
        def _boom(events):
            raise RuntimeError("blocked")

        mouse = SendInputMouse(sender=_boom, metrics=lambda: (8, 8))
        with self.assertRaises(ActuatorError):
            mouse.move_to(1, 1)

    def test_bad_metrics_raise(self):
        mouse = SendInputMouse(sender=lambda events: len(events),
                               metrics=lambda: (1, 1))
        with self.assertRaises(ActuatorError):
            mouse.move_to(0, 0)

    def test_write_char_rejects_empty(self):
        keys = SendInputKeyboard(sender=lambda events: len(events))
        with self.assertRaises(ValueError):
            keys.write_char("")

    def test_position_read_is_safe(self):
        try:
            pos = SendInputMouse(
                sender=lambda events: len(events)).position()
        except ActuatorError:
            self.skipTest("SendInput position requires Windows user32")
        self.assertIsInstance(pos[0], int)
        self.assertIsInstance(pos[1], int)


class PyWin32BackendTest(unittest.TestCase):
    def test_missing_pywin32_raises_install_hint(self):
        blocked = {"win32gui": None, "win32process": None}
        saved = {}
        for name in blocked:
            if name in sys.modules:
                saved[name] = sys.modules[name]
            sys.modules[name] = None  # type: ignore[assignment]
        try:
            backend = PyWin32WindowBackend()
            with self.assertRaises(ActuatorError) as ctx:
                backend.list_windows()
            self.assertIn("pywin32", str(ctx.exception))
            with self.assertRaises(ActuatorError):
                backend.foreground()
            with self.assertRaises(ActuatorError):
                backend.is_valid(1)
            with self.assertRaises(ActuatorError):
                backend.activate(1)
        finally:
            for name in blocked:
                sys.modules.pop(name, None)
            sys.modules.update(saved)

    def test_activate_validates_handle(self):
        with self.assertRaises(ValueError):
            PyWin32WindowBackend().activate(0)


class VerifyHelperTest(unittest.TestCase):
    def test_verify_foreground_ok(self):
        backend = _backend()
        backend.activate(22)
        info = verify_foreground(backend, 22)
        self.assertEqual(info.handle, 22)

    def test_verify_foreground_mismatch(self):
        with self.assertRaises(TargetValidationError) as ctx:
            verify_foreground(_backend(), 22)
        self.assertEqual(ctx.exception.reason, "foreground_mismatch")

    def test_verify_cursor_ok_and_tolerance(self):
        mouse = FakeMouse(start=(100, 100))
        self.assertEqual(verify_cursor_at(mouse, 101, 100), (100, 100))
        with self.assertRaises(ActuatorError):
            verify_cursor_at(mouse, 110, 100, tolerance=2)

    def test_verify_completion(self):
        verify_completion(True, operation="x")
        with self.assertRaises(ActuatorError):
            verify_completion(False, operation="x")
        with self.assertRaises(ValueError):
            verify_completion(True, operation=42)  # type: ignore[arg-type]


class ActuatorIntegrationTest(unittest.TestCase):
    def test_default_window_backend_is_pywin32(self):
        actor = DesktopActuator(mouse=FakeMouse(),
                                keyboard=FakeKeyboard(),
                                failsafe=FakeFailsafe())
        try:
            self.assertIsInstance(actor.window_backend,
                                  PyWin32WindowBackend)
        finally:
            actor.close()

    def test_resolve_validate_focus_round_trip(self):
        actor, parts = _actuator()
        try:
            expected = actor.resolve_target("notes")
            self.assertEqual(expected.handle, 22)
            with actor.focus_target(expected.handle):
                live = actor.validate_target(expected,
                                             operation="drive_text")
                self.assertEqual(live.handle, 22)
            backend = parts["windows"]
            self.assertEqual(backend.foreground().handle, 11)
        finally:
            actor.close()

    def test_move_verify_pass_and_fail(self):
        actor, _ = _actuator()
        try:
            actor.move_cursor_smooth(CursorPoint(30, 40), duration_sec=0.01,
                                     steps=2, verify=True)
        finally:
            actor.close()

        class _SlippingMouse(FakeMouse):
            def move_to(self, x, y):  # injection "succeeds", cursor slips
                self.moves.append((x, y))

        slipping = DesktopActuator(mouse=_SlippingMouse(),
                                   keyboard=FakeKeyboard(),
                                   failsafe=FakeFailsafe(),
                                   windows=_backend())
        try:
            with self.assertRaises(ActuatorError):
                slipping.move_cursor_smooth(CursorPoint(30, 40),
                                            duration_sec=0.01, steps=2,
                                            verify=True)
        finally:
            slipping.close()

    def test_type_verify_fn_matrix(self):
        actor, _ = _actuator()
        try:
            actor.type_natural("ab", base_wpm=60000,
                               verify_fn=lambda: True)
            with self.assertRaises(ActuatorError):
                actor.type_natural("ab", base_wpm=60000,
                                   verify_fn=lambda: False)

            def _boom():
                raise RuntimeError("reader down")

            with self.assertRaises(ActuatorError):
                actor.type_natural("ab", base_wpm=60000, verify_fn=_boom)
            with self.assertRaises(ValueError):
                actor.type_natural("ab", base_wpm=60000,
                                   verify_fn="yes")  # type: ignore[arg-type]
        finally:
            actor.close()

    def test_click_verify(self):
        actor, parts = _actuator()
        try:
            actor.click_point(CursorPoint(5, 5), verify=True)
            self.assertEqual(parts["mouse"].pos, (5, 5))
        finally:
            actor.close()

    def test_execute_plan_with_target_full_flow(self):
        import sys as _sys
        actor, parts = _actuator()
        try:
            plan = ExecutionPlan(
                actions=["open notes"],
                target_window="notes.txt",
                terminal_command=f"{_sys.executable} -c \"print('ok')\"",
            )
            report = actor.execute_plan(
                plan, target="notes", verify_fn=lambda: True)
            self.assertTrue(report["target_validated"])
            self.assertTrue(report["focus_verified"])
            self.assertTrue(report["completion_verified"])
            self.assertEqual(report["terminal_exit"], 0)
            self.assertIn("ok", report["terminal_stdout"])
            backend = parts["windows"]
            self.assertEqual(backend.foreground().handle, 11)
        finally:
            actor.close()

    def test_execute_plan_unauthorized_aborts_before_command(self):
        actor, _ = _actuator()
        try:
            plan = ExecutionPlan(
                actions=["x"], terminal_command="should-not-run")
            with self.assertRaises(TargetValidationError) as ctx:
                actor.execute_plan(plan, target="notes", authorized=False)
            self.assertEqual(ctx.exception.reason, "unauthorized")
        finally:
            actor.close()

    def test_execute_plan_activation_denied_aborts(self):
        backend = _backend(activate_ok=False)
        actor, _ = _actuator(windows=backend)
        try:
            with self.assertRaises(TargetValidationError) as ctx:
                actor.execute_plan(ExecutionPlan(actions=["x"]),
                                   target="notes")
            self.assertEqual(ctx.exception.reason, "activation_denied")
        finally:
            actor.close()

    def test_execute_plan_target_changed_mid_op_aborts(self):
        backend = _backend()
        # preserve -> 11, guard-verify -> 22, pre-validate -> 22, then
        # post-command revalidation sees the browser (33) instead.
        backend.foreground_script = [11, 22, 22, 33]
        actor, _ = _actuator(windows=backend)
        try:
            with self.assertRaises(TargetValidationError) as ctx:
                actor.execute_plan(ExecutionPlan(actions=["x"]),
                                   target="notes")
            self.assertEqual(ctx.exception.reason, "foreground_mismatch")
        finally:
            actor.close()

    def test_execute_plan_legacy_path_unchanged(self):
        actor, _ = _actuator()
        try:
            report = actor.execute_plan(
                ExecutionPlan(actions=["observe"],
                              target_window="terminal"))
            self.assertIsNone(report["terminal_exit"])
            self.assertEqual(report["target_window"], "terminal")
            self.assertFalse(report["target_validated"])
            self.assertFalse(report["focus_verified"])
            with self.assertRaises(ValueError):
                actor.execute_plan({"actions": []})  # type: ignore[arg-type]
        finally:
            actor.close()

    def test_validation_failure_reaches_event_bus(self):
        bus = EventBus()
        seen = []
        bus.subscribe(EventType.TARGET_VALIDATION_FAILED,
                      handler=seen.append)
        actor, _ = _actuator(event_bus=bus)
        try:
            with self.assertRaises(TargetValidationError):
                actor.validate_target(_windows()[1],
                                      operation="drive_text")
            self.assertEqual(len(seen), 1)
        finally:
            actor.close()


class DriveTextTargetTest(unittest.TestCase):
    def _drive_actuator(self, **overrides):
        mouse = FakeMouse(start=(10, 10))
        keys = FakeKeyboard()
        actor = DesktopActuator(mouse=mouse, keyboard=keys,
                               failsafe=FakeFailsafe(),
                               windows=overrides.pop("windows",
                                                     _backend()),
                               **overrides)
        return actor, mouse, keys

    def test_drive_with_target_validates_and_restores(self):
        from desktop_app.actuate_worker import drive_text_blocking
        actor, mouse, keys = self._drive_actuator()
        try:
            report = drive_text_blocking(actor, "hi", glide=True,
                                         base_wpm=60000, target="notes")
            self.assertEqual(report["typed_chars"], 2)
            self.assertEqual(keys.chars, ["h", "i"])
            self.assertTrue(report["target_validated"])
            self.assertTrue(report["focus_verified"])
            backend = actor.window_backend
            assert isinstance(backend, FakeWindowBackend)
            self.assertEqual(backend.foreground().handle, 11)
            self.assertTrue(mouse.moves)
        finally:
            actor.close()

    def test_drive_with_target_and_verification(self):
        from desktop_app.actuate_worker import drive_text_blocking
        actor, _, _ = self._drive_actuator()
        try:
            report = drive_text_blocking(
                actor, "hi", glide=True, base_wpm=60000, target="notes",
                verify_cursor=True, verify_fn=lambda: True)
            self.assertTrue(report["completion_verified"])
        finally:
            actor.close()

    def test_drive_unauthorized_aborts_without_typing(self):
        from desktop_app.actuate_worker import drive_text_blocking
        actor, _, keys = self._drive_actuator()
        try:
            with self.assertRaises(TargetValidationError):
                drive_text_blocking(actor, "hi", base_wpm=60000,
                                    target="notes", authorized=False)
            self.assertEqual(keys.chars, [])
        finally:
            actor.close()

    def test_drive_verify_fn_false_aborts(self):
        from desktop_app.actuate_worker import drive_text_blocking
        actor, _, _ = self._drive_actuator()
        try:
            with self.assertRaises(ActuatorError):
                drive_text_blocking(actor, "hi", base_wpm=60000,
                                    target="notes",
                                    verify_fn=lambda: False)
        finally:
            actor.close()

    def test_drive_rejects_bad_target_type(self):
        from desktop_app.actuate_worker import drive_text_blocking
        actor, _, _ = self._drive_actuator()
        try:
            with self.assertRaises(ValueError):
                drive_text_blocking(actor, "hi", target=42)
        finally:
            actor.close()

    def test_drive_legacy_path_keeps_working(self):
        from desktop_app.actuate_worker import drive_text_blocking
        actor, _, keys = self._drive_actuator()
        try:
            report = drive_text_blocking(actor, "hi", glide=False,
                                         base_wpm=60000)
            self.assertEqual(report["typed_chars"], 2)
            self.assertEqual(keys.chars, ["h", "i"])
            self.assertFalse(report["target_validated"])
        finally:
            actor.close()


def _needs_qt(test):
    try:
        from desktop_app.tabs._qt import load_qt
    except Exception as exc:
        test.skipTest(f"Qt bindings unavailable: {exc}")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _, QtCore, _, QtWidgets = load_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return QtCore, app


def _wait_for(signal, QtCore, timeout_ms=8000):
    import time as _time
    fired: list = []
    signal.connect(lambda *args: fired.append(args))
    deadline = _time.monotonic() + timeout_ms / 1000.0
    from desktop_app.tabs._qt import load_qt
    _, _, _, QtWidgets = load_qt()
    app = QtWidgets.QApplication.instance()
    while _time.monotonic() < deadline and not fired:
        app.processEvents()
        _time.sleep(0.02)
    app.processEvents()
    return fired[0] if fired else None


class DriveWorkerTargetTest(unittest.TestCase):
    def _factory(self, **overrides):
        def _make():
            return DesktopActuator(
                mouse=FakeMouse(start=(10, 10)),
                keyboard=FakeKeyboard(),
                failsafe=FakeFailsafe(),
                windows=overrides.pop("windows", _backend()),
                **overrides)
        return _make

    def test_worker_finished_with_target(self):
        QtCore, _ = _needs_qt(self)
        from desktop_app.actuate_worker import create_drive_worker
        worker = create_drive_worker(
            "hi", countdown_sec=0, base_wpm=60000, target="notes",
            actuator_factory=self._factory())
        try:
            worker.start()
            fired = _wait_for(worker.drive_finished, QtCore)
            worker.wait(5000)
            self.assertIsNotNone(fired)
            self.assertTrue(fired[0]["target_validated"])
            self.assertTrue(fired[0]["focus_verified"])
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

    def test_worker_unauthorized_target_is_drive_error(self):
        QtCore, _ = _needs_qt(self)
        from desktop_app.actuate_worker import create_drive_worker
        worker = create_drive_worker(
            "hi", countdown_sec=0, base_wpm=60000, target="notes",
            authorized=False, actuator_factory=self._factory())
        try:
            worker.start()
            fired = _wait_for(worker.drive_error, QtCore)
            worker.wait(5000)
            self.assertIsNotNone(fired)
        finally:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass


if __name__ == "__main__":
    unittest.main()
