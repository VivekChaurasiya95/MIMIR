"""Tests for the desktop-app runtime integrations (Improvement Plan).

Covers the wiring that turns the completed runtime modules into one
coordinated system: the shared safety latch + action registry owned by
``AppState``, voice approvals bound to pending actions (desktop
``respond`` and the CLI cobot loop), the demo/showcase safety paths,
the drive target picker + pre-drive safety gate, the non-blocking
watch start, and the app-level qasync loop ownership.

Qt worker/UI tests run offscreen only when bindings are installed,
otherwise they skip (same pattern as ``test_phase4``).
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from desktop_app.state import AppState
from mimir.actions import ActionStatus


def _needs_qt(test):
    """Skip the test when Qt bindings are unavailable (offscreen)."""
    try:
        from desktop_app.tabs._qt import load_qt
        load_qt()
    except ImportError:
        test.skipTest("PyQt6/PySide6 not installed")
    from desktop_app.tabs._qt import load_qt
    _, QtCore, _, QtWidgets = load_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    try:
        test._qt_app_ref = app
    except AttributeError:
        pass
    return QtCore, QtWidgets, app


def _noop_voice():
    from mimir.voice import VoiceCoordinator
    return VoiceCoordinator(
        stt_backend=lambda timeout: None,
        tts_backend=lambda text: None,
    )


class _Status:
    def showMessage(self, *args):
        pass


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, timeout=60,
    )


def _seed_repo(path):
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "wiring@test.invalid")
    _git(path, "config", "user.name", "wiring")
    (Path(path) / "a.py").write_text("print(1)\n", encoding="utf-8")
    _git(path, "add", "a.py")
    _git(path, "commit", "-m", "feat: add a")


class AppStateWiringTest(unittest.TestCase):
    def setUp(self):
        self.state = AppState(store_path=":memory:")

    def tearDown(self):
        self.state.close()

    def test_state_owns_shared_safety_and_registry(self):
        from mimir.actions import ActionRegistry
        from mimir.safety import SafetyController
        self.assertIsInstance(self.state.safety, SafetyController)
        self.assertIsInstance(self.state.action_registry, ActionRegistry)
        self.assertIs(self.state.action_registry.safety, self.state.safety)
        other = AppState(store_path=":memory:")
        try:
            self.assertIsNot(other.safety, self.state.safety)
            self.assertIsNot(other.action_registry,
                             self.state.action_registry)
        finally:
            other.close()

    def test_approve_without_pending_keeps_legacy_reply(self):
        self.assertIn("Confirmed",
                      self.state.respond("approve", "go ahead"))

    def test_approve_with_pending_authorizes_it(self):
        spec = self.state.action_registry.propose(
            "drive_text", target="notes.txt")
        reply = self.state.respond("approve", "go ahead")
        self.assertIn("Approved", reply)
        self.assertIn("drive_text", reply)
        fetched = self.state.action_registry.get(spec.action_id)
        assert fetched is not None
        self.assertEqual(fetched.status, ActionStatus.AUTHORIZED)

    def test_approve_ambiguous_asks_instead_of_guessing(self):
        first = self.state.action_registry.propose(
            "drive_text", target="notes.txt")
        second = self.state.action_registry.propose(
            "drive_text", target="other.txt")
        reply = self.state.respond("approve", "go ahead")
        self.assertNotIn("Approved", reply)
        for spec_id in (first.action_id, second.action_id):
            fetched = self.state.action_registry.get(spec_id)
            assert fetched is not None
            self.assertEqual(fetched.status, ActionStatus.PENDING)

    def test_approve_while_latched_denies_without_mutating(self):
        spec = self.state.action_registry.propose(
            "drive_text", target="notes.txt")
        self.state.safety.trip(source="test", reason="latched")
        reply = self.state.respond("approve", "go ahead")
        self.assertIn("stop", reply.lower())
        fetched = self.state.action_registry.get(spec.action_id)
        assert fetched is not None
        self.assertEqual(fetched.status, ActionStatus.PENDING)

    def test_stop_latches_safety_and_keeps_farewell(self):
        reply = self.state.respond("stop", "stop")
        self.assertIn("Powering down", reply)
        self.assertTrue(self.state.safety.estopped)

    def test_voice_service_approves_pending_and_speaks(self):
        said: list[str] = []
        service = self.state.voice_service(
            _noop_voice(), speak_fn=said.append, speak_async=False)
        spec = self.state.action_registry.propose(
            "drive_text", target="notes.txt")
        decision = service.handle_transcript("go ahead")
        self.assertEqual(decision.authorized_action_id, spec.action_id)
        self.assertIn("Approved", decision.reply)
        self.assertEqual(said, [decision.reply])
        fetched = self.state.action_registry.get(spec.action_id)
        assert fetched is not None
        self.assertEqual(fetched.status, ActionStatus.AUTHORIZED)

    def test_voice_service_stop_latches_shared_controller(self):
        service = self.state.voice_service(
            _noop_voice(), speak_fn=lambda text: None, speak_async=False)
        decision = service.handle_transcript("stop")
        self.assertIn("Powering down", decision.reply)
        self.assertTrue(self.state.safety.estopped)


class CliWiringTest(unittest.TestCase):
    def test_approve_without_pending_keeps_legacy_reply(self):
        from mimir.cli import _run_cobot_cmd, build_parser
        from mimir.ui.visor import VisorController
        from mimir.voice import VoiceCoordinator

        class Scripted:
            def __init__(self, phrases):
                self.phrases = list(phrases)
                self.said: list[str] = []
                self._parser = VoiceCoordinator(
                    stt_backend=lambda timeout: None,
                    tts_backend=lambda text: None,
                )

            def listen_phrase(self, timeout_sec=5.0):
                return (self.phrases.pop(0) if self.phrases else None)

            def speak(self, text, on_start=None):
                self.said.append(text)

            def parse_intent(self, text):
                return self._parser.parse_intent(text)

        voice = Scripted(["go ahead", "sleep"])
        with tempfile.TemporaryDirectory() as tmp:
            args = build_parser().parse_args(
                ["cobot", "--store", str(Path(tmp) / "mem.db")])
            code = _run_cobot_cmd(
                args, voice=voice, controller=VisorController())
        self.assertEqual(code, 0)
        self.assertIn("Live execution", voice.said[0])

    def test_voice_loop_binds_approval_and_latches_stop(self):
        from mimir.actions import ActionRegistry
        from mimir.actuator import DesktopActuator
        from mimir.cli import _voice_loop
        from mimir.safety import SafetyController
        from mimir.store import PrincipleStore
        from mimir.ui.visor import VisorController
        from mimir.voice import VoiceCoordinator

        class Scripted:
            def __init__(self, phrases):
                self.phrases = list(phrases)
                self.said: list[str] = []
                self._parser = VoiceCoordinator(
                    stt_backend=lambda timeout: None,
                    tts_backend=lambda text: None,
                )

            def listen_phrase(self, timeout_sec=5.0):
                return (self.phrases.pop(0) if self.phrases else None)

            def speak(self, text, on_start=None):
                self.said.append(text)

            def parse_intent(self, text):
                return self._parser.parse_intent(text)

        class _Mouse:
            pos = (0, 0)

            def position(self):
                return self.pos

            def move_to(self, x, y):
                self.pos = (x, y)

            def click(self):
                pass

        class _Keys:
            def write_char(self, char):
                pass

        class _Safe:
            def arm(self, on_trip):
                pass

            def disarm(self):
                pass

        safety = SafetyController()
        registry = ActionRegistry(safety=safety)
        spec = registry.propose("demo_drive", target="repo")
        store = PrincipleStore(":memory:")
        actuator = DesktopActuator(mouse=_Mouse(), keyboard=_Keys(),
                                   failsafe=_Safe(), safety=safety)
        try:
            voice = Scripted(["go ahead", "stop"])
            code = _voice_loop(voice, VisorController(), store, actuator,
                               registry=registry, safety=safety)
        finally:
            actuator.close()
            store.close()
        self.assertEqual(code, 0)
        self.assertIn("Approved", voice.said[0])
        self.assertIn("Powering down", voice.said[-1])
        fetched = registry.get(spec.action_id)
        assert fetched is not None
        self.assertEqual(fetched.status, ActionStatus.AUTHORIZED)
        self.assertTrue(safety.estopped)


class DriveTargetTest(unittest.TestCase):
    def test_explicit_backend_lists_and_prefers_foreground(self):
        from desktop_app.actuate_worker import list_drive_targets
        from mimir.desktop import WindowInfo

        class _Backend:
            def list_windows(self):
                return [WindowInfo(handle=11, process_id=1,
                                   executable="code.exe", title="a.py"),
                        WindowInfo(handle=22, process_id=2,
                                   executable="notes.exe", title="notes")]

            def foreground(self):
                return WindowInfo(handle=22, process_id=2,
                                  executable="notes.exe", title="notes")

        items, selected = list_drive_targets(_Backend())
        self.assertEqual([h for h, _ in items], [11, 22])
        self.assertIn("notes", items[1][1])
        self.assertEqual(selected, 22)

    def test_unknown_foreground_selects_nothing(self):
        from desktop_app.actuate_worker import list_drive_targets
        from mimir.desktop import WindowInfo

        class _Backend:
            def list_windows(self):
                return [WindowInfo(handle=11, process_id=1,
                                   executable="code.exe", title="a.py")]

            def foreground(self):
                return WindowInfo(handle=99, process_id=9)

        items, selected = list_drive_targets(_Backend())
        self.assertEqual(len(items), 1)
        self.assertIsNone(selected)

    def test_broken_backend_falls_back_to_legacy(self):
        from desktop_app.actuate_worker import list_drive_targets

        class _Broken:
            def list_windows(self):
                raise RuntimeError("no desktop")

        self.assertEqual(list_drive_targets(_Broken()), ([], None))

    def test_default_probe_never_raises(self):
        from desktop_app.actuate_worker import list_drive_targets
        items, selected = list_drive_targets()
        self.assertIsInstance(items, list)
        self.assertTrue(selected is None or isinstance(selected, int))


class DemoWiringTest(unittest.TestCase):
    def _context(self, store):
        import demo_live_cobot as demo
        return demo.DemoContext(
            live=False,
            allow_write=False,
            store=store,
            voice=_noop_voice(),
            visor=__import__(
                "mimir.ui.visor", fromlist=["VisorController"]
            ).VisorController(),
            actuator=__import__(
                "mimir.actuator", fromlist=["DesktopActuator"]
            ).DesktopActuator(
                mouse=demo.DryMouse(),
                keyboard=demo.DryKeyboard(),
                failsafe=demo.DryFailsafe(),
            ),
        )

    def test_context_builds_shared_safety_and_registry(self):
        from mimir.store import PrincipleStore
        store = PrincipleStore(":memory:")
        try:
            ctx = self._context(store)
            ctx.actuator.close()
            self.assertIs(ctx.registry.safety, ctx.safety)
        finally:
            store.close()

    def test_drive_scene_runs_registry_lifecycle(self):
        import demo_live_cobot as demo
        from mimir.store import PrincipleStore
        with tempfile.TemporaryDirectory() as tmp:
            _seed_repo(tmp)
            store = PrincipleStore(":memory:")
            try:
                ctx = self._context(store)
                demo.scene_drive(ctx, tmp)
                self.assertFalse(ctx.safety.estopped)
                assert ctx.registry is not None
                pending = ctx.registry.list_pending()
                self.assertEqual(pending, [])
            finally:
                ctx.actuator.close()
                store.close()

    def test_estop_drill_ends_nominal_after_correction(self):
        import demo_live_cobot as demo
        from mimir.store import PrincipleStore
        store = PrincipleStore(":memory:")
        try:
            ctx = self._context(store)
            demo.scene_estop_drill(ctx)
            self.assertFalse(ctx.safety.estopped)
            ids = [p.id for p in store.list_all()]
            self.assertIn("bp_user_forbid_env_001", ids)
        finally:
            ctx.actuator.close()
            store.close()


class ShowcaseWiringTest(unittest.TestCase):
    def test_sim_showcase_ends_nominal(self):
        _needs_qt(self)
        from desktop_app.showcase import ShowcaseRunner
        state = AppState(store_path=":memory:")
        try:
            runner = ShowcaseRunner(state, speak_live=False)
            self.assertIs(runner._safety, state.safety)
            self.assertTrue(runner.run_all(simulate_hardware=True))
            self.assertFalse(state.safety.estopped)
        finally:
            state.close()

    def test_cancel_aborts_mid_scene_drive(self):
        _needs_qt(self)
        from desktop_app.showcase import ShowcaseRunner
        from mimir.actuator import DesktopActuator
        state = AppState(store_path=":memory:")
        try:
            runner = ShowcaseRunner(state, speak_live=False)
            actuator = DesktopActuator(
                mouse=type("M", (), {
                    "pos": (0, 0),
                    "position": lambda self: self.pos,
                    "move_to": lambda self, x, y: None,
                    "click": lambda self: None,
                })(),
                keyboard=type("K", (), {
                    "write_char": lambda self, c: None,
                })(),
                failsafe=type("F", (), {
                    "arm": lambda self, cb: None,
                    "disarm": lambda self: None,
                })(),
                safety=state.safety,
            )
            try:
                runner._track(actuator)
                runner.cancel()
                self.assertTrue(actuator.interrupted)
                self.assertTrue(state.safety.estopped)
            finally:
                actuator.close()
        finally:
            state.close()


class DriveSafetyTest(unittest.TestCase):
    def _run_worker(self, QtCore, QtWidgets, worker):
        loop = QtCore.QEventLoop()
        timer = QtCore.QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        errors: list[str] = []
        worker.drive_error.connect(
            lambda message: (errors.append(message), loop.quit()))
        worker.drive_finished.connect(lambda _report: loop.quit())
        worker.drive_interrupted.connect(lambda _reason: loop.quit())
        worker.start()
        timer.start(15000)
        loop.exec()
        timer.stop()
        worker.wait(5000)
        return errors

    def test_latched_safety_aborts_before_factory(self):
        _, QtWidgets, _ = _needs_qt(self)
        from desktop_app.actuate_worker import create_drive_worker
        from mimir.safety import SafetyController
        safety = SafetyController()
        safety.trip(source="test", reason="latched")
        calls: list[str] = []

        def _factory():
            calls.append("built")
            raise AssertionError("factory must not run while stopped")

        worker = create_drive_worker(
            "hello", countdown_sec=0, actuator_factory=_factory,
            safety=safety)
        try:
            from desktop_app.tabs._qt import load_qt
            _, QtCore, _, _ = load_qt()
            errors = self._run_worker(QtCore, QtWidgets, worker)
        finally:
            worker.deleteLater()
        self.assertEqual(calls, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("Safety stop", errors[0])

    def test_nominal_safety_drives_with_fakes(self):
        _, QtWidgets, _ = _needs_qt(self)
        from desktop_app.actuate_worker import create_drive_worker
        from mimir.actuator import DesktopActuator
        from mimir.safety import SafetyController

        class _Mouse:
            def __init__(self):
                self.pos = (10, 10)

            def position(self):
                return self.pos

            def move_to(self, x, y):
                self.pos = (x, y)

            def click(self):
                pass

        class _Keys:
            def __init__(self):
                self.chars: list[str] = []

            def write_char(self, char):
                self.chars.append(char)

        class _Safe:
            def arm(self, on_trip):
                pass

            def disarm(self):
                pass

        keys = _Keys()
        safety = SafetyController()
        worker = create_drive_worker(
            "hi", countdown_sec=0,
            actuator_factory=lambda: DesktopActuator(
                mouse=_Mouse(), keyboard=keys, failsafe=_Safe(),
                safety=safety),
            safety=safety)
        try:
            from desktop_app.tabs._qt import load_qt
            _, QtCore, _, _ = load_qt()
            errors = self._run_worker(QtCore, QtWidgets, worker)
        finally:
            worker.deleteLater()
        self.assertEqual(errors, [])
        self.assertEqual("".join(keys.chars), "hi")
        self.assertFalse(safety.estopped)

    def test_rejects_non_controller_safety(self):
        _needs_qt(self)
        from desktop_app.actuate_worker import DriveWorker
        with self.assertRaises(ValueError):
            DriveWorker("hello", safety="not-a-controller")


class WatchStartAsyncTest(unittest.TestCase):
    class _Prefs:
        chimes = False

    class _State:
        def __init__(self, episodes=None, error=None):
            self._episodes = list(episodes or [])
            self._error = error
            self.watching = False
            self.prefs = WatchStartAsyncTest._Prefs()

        def observe(self, repo, roots, max_commits=10):
            return self._pass()

        def observe_new(self, repo, roots, max_commits=10):
            return self._pass()

        def _pass(self):
            if self._error is not None:
                raise self._error
            return list(self._episodes)

        def set_watching(self, watching):
            self.watching = watching

    def test_async_start_watches_without_blocking(self):
        _needs_qt(self)
        from desktop_app.watch import WatchController
        fake = self._State(episodes=["ep1"])
        watcher = WatchController(fake, _Status(), interval_ms=60000)
        try:
            done: list[tuple[bool, str]] = []
            self.assertTrue(watcher.start_async(
                "repo", ["roots"], 10,
                lambda ok, msg: done.append((ok, msg))))
            self.assertTrue(watcher.wait_for_idle(timeout_s=10.0))
            self.assertEqual(len(done), 1)
            self.assertTrue(done[0][0])
            self.assertTrue(watcher.watching)
            self.assertTrue(fake.watching)
        finally:
            watcher.stop()

    def test_async_start_error_reports_and_stays_stopped(self):
        _needs_qt(self)
        from desktop_app.watch import WatchController
        fake = self._State(error=RuntimeError("no git here"))
        watcher = WatchController(fake, _Status(), interval_ms=60000)
        try:
            done: list[tuple[bool, str]] = []
            self.assertTrue(watcher.start_async(
                "repo", ["roots"], 10,
                lambda ok, msg: done.append((ok, msg))))
            self.assertTrue(watcher.wait_for_idle(timeout_s=10.0))
            self.assertEqual(len(done), 1)
            self.assertFalse(done[0][0])
            self.assertIn("no git here", done[0][1])
            self.assertFalse(watcher.watching)
            self.assertFalse(fake.watching)
        finally:
            watcher.stop()

    def test_double_start_reports_already_running(self):
        _needs_qt(self)
        from desktop_app.watch import WatchController
        fake = self._State(episodes=[])
        watcher = WatchController(fake, _Status(), interval_ms=60000)
        try:
            first: list[tuple[bool, str]] = []
            self.assertTrue(watcher.start_async(
                "repo", ["roots"], 10,
                lambda ok, msg: first.append((ok, msg))))
            self.assertTrue(watcher.wait_for_idle(timeout_s=10.0))
            second: list[tuple[bool, str]] = []
            self.assertFalse(watcher.start_async(
                "repo", ["roots"], 10,
                lambda ok, msg: second.append((ok, msg))))
            self.assertEqual(len(second), 1)
            self.assertFalse(second[0][0])
        finally:
            watcher.stop()


class LoopOwnerAppTest(unittest.TestCase):
    def test_shutdown_releases_desktop_loop(self):
        from desktop_app.app import MainWindow
        from mimir.isolation import LoopOwner
        name = "desktop-app"
        try:
            LoopOwner.release(name)
        except Exception:
            pass
        try:
            LoopOwner.acquire(name, loop_factory=lambda: object())
            MainWindow().shutdown()
            self.assertFalse(LoopOwner.owned())
        finally:
            try:
                LoopOwner.release(name)
            except Exception:
                pass

    def test_shutdown_keeps_foreign_loop(self):
        from desktop_app.app import MainWindow
        from mimir.isolation import LoopOwner
        try:
            LoopOwner.release("desktop-app")
        except Exception:
            pass
        try:
            LoopOwner.release("foreign-owner")
        except Exception:
            pass
        try:
            LoopOwner.acquire("foreign-owner",
                              loop_factory=lambda: object())
            MainWindow().shutdown()
            self.assertEqual(LoopOwner.owner(), "foreign-owner")
        finally:
            try:
                LoopOwner.release("foreign-owner")
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
