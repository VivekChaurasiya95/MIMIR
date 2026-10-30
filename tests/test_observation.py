"""Tests for continuous observation (plan section 3.1, step 2).

Covers the filesystem watcher (excludes, debounce, poll backend,
watchdog backend when installed), the incremental AST observer
(including invalid syntax), live git state + observer (subprocess and
GitPython backends), consent-based cadence telemetry, and the sensory
service composition. Qt-free and loop-free throughout.
"""

import importlib.util
import inspect
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from mimir.ast_delta import (
    AstObserver,
    diff_summaries,
    summarize_python_source,
    summarize_source,
)
from mimir.cadence import CadenceObserver
from mimir.events import Event, EventBus, EventType, create_event
from mimir.git_observe import GitObserver
from mimir.sensory import SensoryService
from mimir.telemetry import (
    GitState,
    NotARepositoryError,
    TelemetryBoundaryError,
    read_git_state,
)
from mimir.watch_fs import (
    Debouncer,
    FilesystemWatcher,
    diff_snapshots,
    scan_tree,
    should_exclude,
)

HAS_WATCHDOG = importlib.util.find_spec("watchdog") is not None
HAS_TREESITTER = importlib.util.find_spec("tree_sitter") is not None
HAS_GITPYTHON = importlib.util.find_spec("git") is not None


def _git(repo: Path, *args: str) -> str:
    cmd = [
        "git", "-C", str(repo),
        "-c", "user.name=Mimir Test",
        "-c", "user.email=mimir-test@example.com",
        "-c", "commit.gpgsign=false",
        *args,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout


class _Collector:
    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe(handler=self._record, name="test-collector")

    def _record(self, event: Event) -> None:
        self.events.append(event)

    def of_type(self, event_type: EventType) -> list[Event]:
        return [e for e in self.events if e.event_type is event_type]


class ExcludeTest(unittest.TestCase):
    def test_venv_build_and_deps_excluded(self) -> None:
        for excluded in (".venv", "venv", "node_modules", "__pycache__",
                         "build", "dist", "target", ".git", "mim-1.egg-info",
                         ".pytest_cache", ".mypy_cache", ".pyrefly"):
            with self.subTest(excluded=excluded):
                path = os.path.join("proj", excluded, "x.py")
                self.assertTrue(should_exclude(path), path)

    def test_source_files_included(self) -> None:
        for path in ("proj/app.py", "proj/src/mod.ts", "proj/README.md"):
            self.assertFalse(should_exclude(path), path)

    def test_editor_and_os_noise_excluded(self) -> None:
        for name in ("draft.tmp", "app.swp", "notes~", "~$lock.docx"):
            self.assertTrue(should_exclude(os.path.join("proj", name)), name)


class DebounceTest(unittest.TestCase):
    def test_matures_only_after_window(self) -> None:
        debouncer = Debouncer(debounce_s=0.5)
        debouncer.note("created", "/proj/a.py", "/proj", 100.0)
        self.assertEqual(debouncer.flush(100.4), [])
        matured = debouncer.flush(100.5)
        self.assertEqual(len(matured), 1)
        self.assertEqual(matured[0].kind, "created")
        self.assertEqual(matured[0].observed_at, 100.0)

    def test_coalescing_table(self) -> None:
        cases: list[
            tuple[str, str, str | None]
        ] = [
            ("created", "modified", "created"),
            ("created", "deleted", None),
            ("modified", "modified", "modified"),
            ("modified", "deleted", "deleted"),
            ("deleted", "created", "modified"),  # atomic-save pattern
            ("deleted", "modified", "modified"),
            ("modified", "created", "modified"),
        ]
        for first, second, expected in cases:
            with self.subTest(first=first, second=second):
                debouncer = Debouncer(debounce_s=10.0)
                debouncer.note(first, "/proj/a.py", "/proj", 0.0)  # type: ignore[arg-type]
                debouncer.note(second, "/proj/a.py", "/proj", 1.0)  # type: ignore[arg-type]
                if expected is None:
                    self.assertEqual(debouncer.pending_count(), 0)
                else:
                    matured = debouncer.flush(999.0)
                    self.assertEqual(len(matured), 1)
                    self.assertEqual(matured[0].kind, expected)

    def test_paths_are_independent(self) -> None:
        debouncer = Debouncer(debounce_s=0.0)
        debouncer.note("created", "/proj/a.py", "/proj", 0.0)
        debouncer.note("deleted", "/proj/b.py", "/proj", 0.0)
        self.assertEqual(debouncer.pending_count(), 2)
        kinds = {e.path: e.kind for e in debouncer.flush(0.0)}
        self.assertEqual(
            kinds, {os.path.normpath("/proj/a.py"): "created",
                    os.path.normpath("/proj/b.py"): "deleted"})

    def test_invalid_kind_rejected(self) -> None:
        debouncer = Debouncer()
        with self.assertRaises(ValueError):
            debouncer.note("renamed", "/proj/a.py", "/proj", 0.0)  # type: ignore[arg-type]

    def test_negative_window_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Debouncer(debounce_s=-1.0)


class SnapshotDiffTest(unittest.TestCase):
    def test_created_modified_deleted(self) -> None:
        old = {"/a.py": (10, 1), "/b.py": (5, 1), "/gone.py": (5, 1)}
        new = {"/a.py": (11, 2), "/b.py": (5, 1), "/new.py": (1, 2)}
        self.assertEqual(
            diff_snapshots(old, new),
            [("modified", "/a.py"), ("deleted", "/gone.py"),
             ("created", "/new.py")],
        )

    def test_identical_is_quiet(self) -> None:
        snap = {"/a.py": (10, 1)}
        self.assertEqual(diff_snapshots(snap, dict(snap)), [])


class PollWatcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()
        self.bus = EventBus()
        self.collector = _Collector(self.bus)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _watcher(self, **kwargs) -> FilesystemWatcher:  # type: ignore[no-untyped-def]
        params = {"backend": "poll", "debounce_s": 0.0,
                  "poll_interval_s": 60.0, "bus": self.bus,
                  "session_id": "sess_obs"}
        params.update(kwargs)
        return FilesystemWatcher([self.root], **params)  # type: ignore[arg-type]

    def test_create_modify_delete_cycle(self) -> None:
        watcher = self._watcher()
        self.assertEqual(watcher.poll_once(now=1000.0), [])
        target = self.root / "app.py"
        target.write_text("x = 1\n", encoding="utf-8")
        created = watcher.poll_once(now=1001.0)
        self.assertEqual([e.kind for e in created], ["created"])
        target.write_text("x = 1\ny = 2\n", encoding="utf-8")
        modified = watcher.poll_once(now=1002.0)
        self.assertEqual([e.kind for e in modified], ["modified"])
        target.unlink()
        deleted = watcher.poll_once(now=1003.0)
        self.assertEqual([e.kind for e in deleted], ["deleted"])
        self.assertEqual(watcher.poll_once(now=1004.0), [])

    def test_publishes_identity_only_events(self) -> None:
        watcher = self._watcher()
        watcher.poll_once(now=1000.0)
        (self.root / "secret_holder.py").write_text(
            'TOKEN = "tok_live_abc123"\n', encoding="utf-8")
        watcher.poll_once(now=1001.0)
        births = self.collector.of_type(EventType.FILE_CREATED)
        self.assertEqual(len(births), 1)
        payload = births[0].payload
        self.assertEqual(payload["kind"], "created")
        self.assertEqual(payload["root"], os.path.abspath(str(self.root)))
        self.assertTrue(payload["path"].endswith("secret_holder.py"))
        self.assertIn("observed_at", payload)
        self.assertEqual(births[0].session_id, "sess_obs")
        self.assertNotIn("tok_live_abc123", repr(payload))

    def test_excluded_dirs_are_pruned(self) -> None:
        venv = self.root / ".venv"
        venv.mkdir()
        (venv / "lib.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "ok.py").write_text("x = 1\n", encoding="utf-8")
        watcher = self._watcher()
        events = watcher.poll_once(now=1000.0)
        self.assertEqual([e.kind for e in events], ["created"])
        self.assertTrue(events[0].path.endswith("ok.py"))
        self.assertEqual(scan_tree(self.root / ".venv"), {})

    def test_start_stop_lifecycle(self) -> None:
        watcher = self._watcher()
        watcher.start()
        try:
            self.assertTrue(watcher.running)
            watcher.start()  # idempotent
            self.assertTrue(watcher.running)
        finally:
            watcher.stop()
        self.assertFalse(watcher.running)
        watcher.stop()  # idempotent

    def test_empty_roots_rejected(self) -> None:
        with self.assertRaises(ValueError):
            FilesystemWatcher([], backend="poll")


@unittest.skipUnless(HAS_WATCHDOG, "watchdog backend not installed")
class WatchdogBackendTest(unittest.TestCase):
    def test_live_create_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bus = EventBus()
            collector = _Collector(bus)
            watcher = FilesystemWatcher(
                [tmp], backend="watchdog", debounce_s=0.1,
                poll_interval_s=0.05, bus=bus)
            watcher.start()
            try:
                (Path(tmp) / "live.py").write_text("x = 1\n",
                                                   encoding="utf-8")
                deadline = time.time() + 10.0
                while (not collector.of_type(EventType.FILE_CREATED)
                       and time.time() < deadline):
                    time.sleep(0.05)
                    watcher.poll_once()
            finally:
                watcher.stop()
            births = collector.of_type(EventType.FILE_CREATED)
            self.assertTrue(
                any(e.payload.get("path", "").endswith("live.py")
                    for e in births),
                f"no FILE_CREATED for live.py in {[e.payload for e in collector.events]}")


@unittest.skipIf(HAS_WATCHDOG, "watchdog is installed")
class WatchdogMissingTest(unittest.TestCase):
    def test_explicit_backend_errors_helpfully(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ImportError):
                FilesystemWatcher([tmp], backend="watchdog")


class AstSummaryTest(unittest.TestCase):
    def test_functions_classes_imports(self) -> None:
        summary = summarize_python_source("m.py", (
            "import os\nfrom sys import path\n\n"
            "class Store:\n"
            "    def save(self, key):\n"
            "        return key\n\n"
            "async def fetch(url, retries=3):\n"
            "    return url\n"
        ))
        self.assertTrue(summary.syntax_valid)
        self.assertEqual(summary.language, "python")
        by_name = {f.qualified: f for f in summary.functions}
        self.assertEqual(
            sorted(by_name),
            ["Store.save", "fetch"])
        self.assertEqual(by_name["fetch"].signature, "(url, retries)")
        self.assertEqual(summary.classes[0].methods, ("save",))
        self.assertEqual(summary.imports, ("os", "sys"))

    def test_signatures_drop_default_values(self) -> None:
        summary = summarize_python_source(
            "m.py", 'def login(user, password="s3cret-hunter2"):\n'
                     "    return user\n")
        self.assertTrue(summary.syntax_valid)
        self.assertEqual(summary.functions[0].signature, "(user, password)")
        self.assertNotIn("s3cret-hunter2", repr(summary))

    def test_invalid_syntax_reported_not_raised(self) -> None:
        summary = summarize_python_source("m.py", "def broken(:\n    pass\n")
        self.assertFalse(summary.syntax_valid)
        self.assertIn("line 1", summary.syntax_error)
        self.assertEqual(summary.functions, ())

    def test_unsupported_language_is_explicit(self) -> None:
        summary = summarize_source("notes.md", "# hi\n", backend="ast")
        self.assertEqual(summary.backend, "unsupported")

    def test_invalid_backend_rejected(self) -> None:
        with self.assertRaises(ValueError):
            summarize_source("m.py", "x = 1\n", backend="nope")


class AstDiffTest(unittest.TestCase):
    def _summaries(self, before: str, after: str):  # type: ignore[no-untyped-def]
        return (summarize_python_source("m.py", before),
                summarize_python_source("m.py", after))

    def test_added_removed_modified(self) -> None:
        old, new = self._summaries(
            "def keep():\n    return 1\n\ndef drop():\n    return 2\n",
            "def keep():\n    return 10\n\ndef fresh(a):\n    return a\n",
        )
        delta = diff_summaries(old, new)
        self.assertEqual(delta.added_functions, ("fresh",))
        self.assertEqual(delta.removed_functions, ("drop",))
        self.assertEqual(delta.modified_functions, ("keep",))
        self.assertEqual(delta.signature_changes, ())
        self.assertFalse(delta.is_empty())

    def test_signature_change_flagged(self) -> None:
        old, new = self._summaries(
            "def run():\n    pass\n",
            "def run(extra):\n    pass\n",
        )
        delta = diff_summaries(old, new)
        self.assertEqual(delta.modified_functions, ("run",))
        self.assertEqual(delta.signature_changes, ("run",))

    def test_identical_is_empty(self) -> None:
        old, new = self._summaries("x = 1\n", "x = 1\n")
        self.assertTrue(diff_summaries(old, new).is_empty())

    def test_syntax_transitions(self) -> None:
        valid, broken = self._summaries("def f():\n    pass\n",
                                        "def f(:\n    pass\n")
        to_broken = diff_summaries(valid, broken)
        self.assertTrue(to_broken.syntax_changed)
        self.assertFalse(to_broken.syntax_valid)
        self.assertFalse(to_broken.is_empty())
        healed = diff_summaries(broken, valid)
        self.assertTrue(healed.syntax_changed)
        self.assertTrue(healed.syntax_valid)


class AstObserverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.collector = _Collector(self.bus)
        self.observer = AstObserver(bus=self.bus, session_id="sess_ast")

    def test_baseline_silent_then_delta_emitted(self) -> None:
        self.assertIsNone(
            self.observer.observe("/proj/m.py", "def a():\n    pass\n"))
        self.assertEqual(
            self.collector.of_type(EventType.AST_DELTA), [])
        self.assertIsNone(
            self.observer.observe("/proj/m.py", "def a():\n    pass\n"))
        delta = self.observer.observe(
            "/proj/m.py", "def a():\n    pass\n\ndef b():\n    pass\n")
        assert delta is not None
        self.assertEqual(delta.added_functions, ("b",))
        emitted = self.collector.of_type(EventType.AST_DELTA)
        self.assertEqual(len(emitted), 1)
        payload = emitted[0].payload
        self.assertEqual(payload["file"], os.path.normpath("/proj/m.py"))
        self.assertEqual(payload["added_functions"], ["b"])
        self.assertIn("modified_functions", payload)
        self.assertIn("removed_functions", payload)
        self.assertTrue(payload["syntax_valid"])
        self.assertEqual(emitted[0].session_id, "sess_ast")

    def test_invalid_syntax_delta_emitted(self) -> None:
        self.observer.observe("/proj/m.py", "def a():\n    pass\n")
        delta = self.observer.observe("/proj/m.py", "def a(:\n")
        assert delta is not None
        self.assertTrue(delta.syntax_changed)
        self.assertFalse(delta.syntax_valid)
        emitted = self.collector.of_type(EventType.AST_DELTA)
        self.assertEqual(len(emitted), 1)
        self.assertFalse(emitted[0].payload["syntax_valid"])

    def test_observe_file_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = root / ".env"
            secret.write_text("TOKEN=x\n", encoding="utf-8")
            self.assertIsNone(self.observer.observe_file(str(secret)))
            binary = root / "blob.py"
            binary.write_bytes(b"\x00\x01\x02")
            self.assertIsNone(self.observer.observe_file(str(binary)))
            notes = root / "notes.txt"
            notes.write_text("hi\n", encoding="utf-8")
            self.assertIsNone(self.observer.observe_file(str(notes)))
            tiny = AstObserver(bus=self.bus, max_bytes=4)
            big = root / "big.py"
            big.write_text("x = 1\n" * 100, encoding="utf-8")
            self.assertIsNone(tiny.observe_file(str(big)))
            missing = root / "missing.py"
            self.assertIsNone(self.observer.observe_file(str(missing)))
            good = root / "good.py"
            good.write_text("def a():\n    pass\n", encoding="utf-8")
            self.assertIsNone(self.observer.observe_file(str(good)))
            good.write_text("def a():\n    pass\n\ndef b():\n    pass\n",
                            encoding="utf-8")
            delta = self.observer.observe_file(str(good))
            assert delta is not None
            self.assertEqual(delta.added_functions, ("b",))

    def test_forget_and_reset(self) -> None:
        self.observer.observe("/proj/m.py", "x = 1\n")
        self.assertEqual(
            self.observer.tracked_paths(), [os.path.normpath("/proj/m.py")])
        self.observer.forget("/proj/m.py")
        self.assertEqual(self.observer.tracked_paths(), [])
        self.observer.observe("/proj/m.py", "x = 1\n")
        self.observer.reset()
        self.assertEqual(self.observer.tracked_paths(), [])

    def test_bad_max_bytes_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AstObserver(max_bytes=0)


@unittest.skipUnless(HAS_TREESITTER, "tree-sitter backend not installed")
class TreeSitterBackendTest(unittest.TestCase):
    def test_backend_never_raises(self) -> None:
        summary = summarize_source(
            "m.py", "def a():\n    pass\n", backend="tree-sitter")
        self.assertIn(summary.backend, ("tree-sitter", "unsupported",
                                        "ast"))


def _seed_repo(repo: Path) -> str:
    _git(repo, "init", "-b", "main")
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n",
                                 encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "add calculator")
    return _git(repo, "rev-parse", "HEAD").strip()


class GitStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "demo"
        self.repo.mkdir()
        self.head = _seed_repo(self.repo)
        self.roots = [str(Path(self._tmp.name))]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_clean_state(self) -> None:
        state = read_git_state(self.repo, allowed_roots=self.roots)
        self.assertIsInstance(state, GitState)
        self.assertEqual(state.branch, "main")
        self.assertEqual(state.head, self.head)
        self.assertFalse(state.dirty)
        self.assertEqual(state.unstaged_count, 0)

    def test_unstaged_staged_untracked(self) -> None:
        (self.repo / "app.py").write_text("def add(a, b):\n    return 0\n",
                                          encoding="utf-8")
        (self.repo / "new.txt").write_text("hello\n", encoding="utf-8")
        state = read_git_state(self.repo, allowed_roots=self.roots)
        self.assertTrue(state.dirty)
        self.assertEqual(state.unstaged, ("app.py",))
        self.assertEqual(state.untracked, ("new.txt",))
        _git(self.repo, "add", "app.py")
        staged = read_git_state(self.repo, allowed_roots=self.roots)
        self.assertEqual(staged.staged, ("app.py",))
        self.assertEqual(staged.unstaged, ())

    def test_no_diff_content_leaks(self) -> None:
        (self.repo / "app.py").write_text('KEY = "live-secret-999"\n',
                                          encoding="utf-8")
        state = read_git_state(self.repo, allowed_roots=self.roots)
        self.assertNotIn("live-secret-999", repr(state))

    def test_unborn_repo_has_empty_head(self) -> None:
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        _git(empty, "init", "-b", "main")
        state = read_git_state(empty, allowed_roots=self.roots)
        self.assertEqual(state.head, "")
        self.assertEqual(state.branch, "main")

    def test_detached_head_reports_head(self) -> None:
        _git(self.repo, "checkout", "--detach", "--quiet")
        state = read_git_state(self.repo, allowed_roots=self.roots)
        self.assertEqual(state.branch, "HEAD")

    def test_boundary_enforced(self) -> None:
        elsewhere = Path(self._tmp.name) / "elsewhere"
        elsewhere.mkdir()
        with self.assertRaises(TelemetryBoundaryError):
            read_git_state(self.repo, allowed_roots=[str(elsewhere)])

    def test_not_a_repo_rejected(self) -> None:
        plain = Path(self._tmp.name) / "plain"
        plain.mkdir()
        with self.assertRaises(NotARepositoryError):
            read_git_state(plain, allowed_roots=self.roots)


class GitObserverTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "demo"
        self.repo.mkdir()
        self.head = _seed_repo(self.repo)
        self.roots = [str(Path(self._tmp.name))]
        self.bus = EventBus()
        self.collector = _Collector(self.bus)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _observer(self) -> GitObserver:
        return GitObserver(self.repo, self.roots, bus=self.bus,
                           backend="subprocess", poll_interval_s=60.0,
                           session_id="sess_git")

    def test_baseline_silent_then_change_emits(self) -> None:
        observer = self._observer()
        baseline = observer.poll_once()
        assert baseline is not None
        self.assertEqual(baseline.head, self.head)
        self.assertEqual(self.collector.of_type(EventType.GIT_CHANGED), [])
        self.assertIsNone(observer.poll_once())  # no change: quiet
        (self.repo / "app.py").write_text("changed\n", encoding="utf-8")
        changed = observer.poll_once()
        assert changed is not None
        emitted = self.collector.of_type(EventType.GIT_CHANGED)
        self.assertEqual(len(emitted), 1)
        payload = emitted[0].payload
        self.assertEqual(payload["previous_head"], self.head)
        self.assertEqual(payload["head"], self.head)
        self.assertEqual(payload["unstaged"], ["app.py"])
        self.assertTrue(payload["dirty"])
        self.assertEqual(emitted[0].session_id, "sess_git")

    def test_commit_transition_emits(self) -> None:
        observer = self._observer()
        observer.poll_once()
        (self.repo / "app.py").write_text("v2\n", encoding="utf-8")
        _git(self.repo, "add", "app.py")
        _git(self.repo, "commit", "-m", "v2")
        changed = observer.poll_once()
        assert changed is not None
        self.assertNotEqual(changed.head, self.head)
        emitted = self.collector.of_type(EventType.GIT_CHANGED)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].payload["previous_head"], self.head)

    def test_non_repo_poll_raises_and_records(self) -> None:
        plain = Path(self._tmp.name) / "plain"
        plain.mkdir()
        observer = GitObserver(plain, self.roots, bus=self.bus,
                               backend="subprocess")
        with self.assertRaises(NotARepositoryError):
            observer.poll_once()
        self.assertTrue(observer.last_error)

    def test_start_stop_lifecycle(self) -> None:
        observer = self._observer()
        observer.start()
        try:
            self.assertTrue(observer.running)
        finally:
            observer.stop()
        self.assertFalse(observer.running)


@unittest.skipUnless(HAS_GITPYTHON, "GitPython backend not installed")
class GitPythonBackendTest(unittest.TestCase):
    def test_snapshot_parity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "demo"
            repo.mkdir()
            head = _seed_repo(repo)
            (repo / "dirty.py").write_text("x\n", encoding="utf-8")
            roots = [tmp]
            via_subprocess = read_git_state(repo, allowed_roots=roots)
            observer = GitObserver(repo, roots, backend="gitpython")
            via_library = observer.snapshot()
            self.assertEqual(via_library.head, head)
            self.assertEqual(via_library.branch, via_subprocess.branch)
            self.assertEqual(via_library.unstaged, ("dirty.py",))
            self.assertTrue(via_library.dirty)


@unittest.skipIf(HAS_GITPYTHON, "GitPython is installed")
class GitPythonMissingTest(unittest.TestCase):
    def test_explicit_backend_errors_helpfully(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ImportError):
                GitObserver(tmp, [tmp], backend="gitpython")


class CadenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.collector = _Collector(self.bus)

    def test_disabled_by_default_and_ticks_ignored(self) -> None:
        observer = CadenceObserver(bus=self.bus)
        self.assertFalse(observer.is_enabled)
        observer.tick(True, 3, now=1000.0)
        snapshot = observer.snapshot(now=1001.0)
        self.assertEqual(snapshot.samples, 0)
        self.assertEqual(snapshot.corrections, 0)
        self.assertIsNone(observer.maybe_emit(now=1002.0))
        self.assertEqual(
            self.collector.of_type(EventType.CADENCE_UPDATED), [])

    def test_aggregates_after_consent(self) -> None:
        observer = CadenceObserver(bus=self.bus, session_id="sess_cad")
        observer.enable()
        observer.tick(True, 0, now=0.0)
        observer.tick(True, 2, now=2.0)
        observer.tick(False, 0, now=5.0)
        observer.tick(False, 1, now=12.0)
        snapshot = observer.snapshot(now=12.0)
        self.assertEqual(snapshot.samples, 4)
        self.assertAlmostEqual(snapshot.active_s, 5.0)
        self.assertAlmostEqual(snapshot.idle_s, 7.0)
        self.assertEqual(snapshot.bursts, 1)
        self.assertEqual(snapshot.pauses, 1)
        self.assertAlmostEqual(snapshot.longest_pause_s, 7.0)
        self.assertEqual(snapshot.corrections, 3)
        self.assertAlmostEqual(snapshot.corrections_per_min, 15.0)

    def test_emit_interval_gating(self) -> None:
        observer = CadenceObserver(bus=self.bus, emit_interval_s=10.0)
        observer.enable()
        observer.tick(True, 0, now=0.0)
        self.assertIsNotNone(observer.maybe_emit(now=0.0))
        self.assertIsNone(observer.maybe_emit(now=5.0))
        self.assertIsNotNone(observer.maybe_emit(now=11.0))
        emitted = self.collector.of_type(EventType.CADENCE_UPDATED)
        self.assertEqual(len(emitted), 2)
        payload = emitted[0].payload
        for key in ("active_s", "idle_s", "bursts", "corrections"):
            self.assertIn(key, payload)

    def test_disable_discards_buffer(self) -> None:
        observer = CadenceObserver(bus=self.bus)
        observer.enable()
        observer.tick(True, 5, now=0.0)
        observer.disable()
        self.assertFalse(observer.is_enabled)
        self.assertEqual(observer.snapshot(now=1.0).samples, 0)

    def test_negative_corrections_rejected(self) -> None:
        observer = CadenceObserver(bus=self.bus)
        observer.enable()
        with self.assertRaises(ValueError):
            observer.tick(True, -1, now=0.0)

    def test_no_raw_keystroke_api(self) -> None:
        observer = CadenceObserver(bus=self.bus)
        for name in dir(observer):
            lowered = name.lower()
            self.assertFalse(
                any(token in lowered for token in
                    ("keystroke", "keylog", "password", "clipboard",
                     "char", "key_code", "keydown")),
                f"suspicious cadence API: {name}")
        params = inspect.signature(observer.tick).parameters
        self.assertEqual(set(params), {"active", "corrections", "now"})


class SensoryServiceTest(unittest.TestCase):
    def test_file_to_ast_to_state_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bus = EventBus()
            collector = _Collector(bus)
            service = SensoryService(
                [tmp], bus=bus, session_id="sess_flow",
                fs_backend="poll", poll_interval_s=60.0)
            service.start()
            try:
                service.watcher.stop()  # drive cycles deterministically
                target = Path(tmp) / "flow.py"
                target.write_text("def a():\n    pass\n", encoding="utf-8")
                service.watcher.poll_once(now=2000.0)
                service.watcher.poll_once(now=2001.0)
                target.write_text(
                    "def a():\n    pass\n\ndef b():\n    pass\n",
                    encoding="utf-8")
                service.watcher.poll_once(now=2002.0)
                service.watcher.poll_once(now=2003.0)
            finally:
                service.stop()
            kinds = [e.event_type for e in collector.events]
            self.assertIn(EventType.FILE_CREATED, kinds)
            ast_events = collector.of_type(EventType.AST_DELTA)
            self.assertTrue(
                any(e.payload.get("added_functions") == ["b"]
                    for e in ast_events),
                f"missing AST delta in {[e.payload for e in ast_events]}")
            state_events = collector.of_type(
                EventType.DEVELOPER_STATE_UPDATED)
            self.assertTrue(state_events, "engine never updated")
            self.assertTrue(
                any("focus_file" in e.payload["changed_fields"]
                    for e in state_events),
                "focus_file never reported as changed")
            self.assertTrue(
                service.engine.state.get("focus_file").endswith("flow.py"))

    def test_status_and_git_wiring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "demo"
            repo.mkdir()
            _seed_repo(repo)
            bus = EventBus()
            collector = _Collector(bus)
            service = SensoryService(
                [tmp], repo=repo, allowed_roots=[tmp], bus=bus,
                fs_backend="poll", git_backend="subprocess",
                poll_interval_s=60.0, git_poll_interval_s=60.0)
            status = service.status()
            self.assertEqual(status["fs_backend"], "poll")
            self.assertEqual(status["git_backend"], "subprocess")
            self.assertFalse(status["cadence_enabled"])
            service.start()
            try:
                service.watcher.stop()
                assert service.git is not None
                service.git.stop()
                service.git.poll_once()  # baseline
                (repo / "app.py").write_text("v2\n", encoding="utf-8")
                changed = service.git.poll_once()
                self.assertIsNotNone(changed)
            finally:
                service.stop()
            emitted = collector.of_type(EventType.GIT_CHANGED)
            self.assertEqual(len(emitted), 1)
            self.assertFalse(service.running)

    def test_cadence_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            off = SensoryService([tmp], fs_backend="poll")
            self.assertFalse(off.cadence.is_enabled)
            on = SensoryService([tmp], fs_backend="poll",
                                enable_cadence=True)
            self.assertTrue(on.cadence.is_enabled)


class EventContractTest(unittest.TestCase):
    def test_observation_event_round_trip(self) -> None:
        event = create_event(
            EventType.AST_DELTA, "sensory.ast_observer",
            {"file": "m.py", "added_functions": ["f"],
             "modified_functions": [], "removed_functions": []},
            session_id="sess_rt",
        )
        revived = Event.from_dict(event.to_dict())
        self.assertEqual(revived.event_type, EventType.AST_DELTA)
        self.assertEqual(revived.payload["added_functions"], ["f"])


if __name__ == "__main__":
    unittest.main()
