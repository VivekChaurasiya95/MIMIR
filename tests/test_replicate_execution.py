"""Unit tests for Replicate tab execution, action execution, and code extraction."""

import os
import tempfile
import unittest
from pathlib import Path

from desktop_app.prefs import DesktopPrefs
from desktop_app.presenters import extract_code_block, infer_file_target
from desktop_app.state import AppState
from mimir.actions import ActionRegistry, ActionStatus
from mimir.events import EventBus
from mimir.planning import ActionPlan, ActionPlanner, PlannedStep, StepKind
from mimir.safety import SafetyController
from mimir.store import PrincipleStore


class CodeExtractionTest(unittest.TestCase):
    def test_extract_markdown_python_fence(self):
        text = "```python\ndef add(a, b):\n    return a + b\n```"
        self.assertEqual(extract_code_block(text), "def add(a, b):\n    return a + b")

    def test_extract_unlabelled_fence(self):
        text = "```\nconst x = 42;\n```"
        self.assertEqual(extract_code_block(text), "const x = 42;")

    def test_extract_plain_code_intact(self):
        text = "def hello():\n    return 'world'"
        self.assertEqual(extract_code_block(text), text)

    def test_infer_file_target_from_prompt(self):
        self.assertEqual(
            infer_file_target("Write a unit test in tests/test_auth.py"),
            "tests/test_auth.py",
        )
        self.assertEqual(
            infer_file_target("Refactor utils.py to support retries"),
            "utils.py",
        )
        self.assertEqual(
            infer_file_target("Create authentication service into src/auth/service.ts"),
            "src/auth/service.ts",
        )
        self.assertEqual(
            infer_file_target("How does the system work?"),
            "",
        )


class ExecuteActionPlanTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()
        self.state = AppState(store_path=":memory:")
        self.state.prefs = DesktopPrefs(
            last_roots=[str(self.workspace)],
            last_repo=str(self.workspace),
        )
        self.safety = self.state.safety
        self.registry = self.state.action_registry

    def tearDown(self):
        try:
            self.state.close()
        except Exception:
            pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_execute_successful_command(self):
        plan = self.state.plan_task(
            "Echo test",
            commands=['python -c "print(\'mimir_ok\')"'],
        )
        spec = self.state.propose_plan(plan)
        ok, msg = self.state.authorize_and_execute(
            spec.action_id,
            checklist_ack=True,
            executor=self.state.execute_action_plan,
        )
        self.assertTrue(ok)
        self.assertIn("mimir_ok", msg)
        self.assertEqual(self.registry.get(spec.action_id).status, ActionStatus.COMPLETED)

    def test_execute_failing_command(self):
        plan = self.state.plan_task(
            "Failing test",
            commands=['python -c "import sys; sys.stderr.write(\'err_msg\'); sys.exit(1)"'],
        )
        spec = self.state.propose_plan(plan)
        ok, msg = self.state.authorize_and_execute(
            spec.action_id,
            checklist_ack=True,
            executor=self.state.execute_action_plan,
        )
        self.assertFalse(ok)
        self.assertIn("failed (exit 1)", msg)
        self.assertEqual(self.registry.get(spec.action_id).status, ActionStatus.FAILED)

    def test_execute_file_write(self):
        target_rel = "helpers/math.py"
        code = "def multiply(x, y):\n    return x * y"
        plan = self.state.plan_task(
            f"Implement multiply in {target_rel}",
            file_or_element=target_rel,
            diff_preview=code,
            operation="apply_change",
        )
        spec = self.state.propose_plan(plan)
        ok, msg = self.state.authorize_and_execute(
            spec.action_id,
            checklist_ack=True,
            executor=self.state.execute_action_plan,
        )
        self.assertTrue(ok)
        self.assertIn("math.py", msg)
        # Verify file was written to disk inside workspace
        dest = self.workspace / target_rel
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_text(encoding="utf-8"), code)
        self.assertEqual(self.registry.get(spec.action_id).status, ActionStatus.COMPLETED)

    def test_execute_blocked_by_safety(self):
        plan = self.state.plan_task(
            "Safe task",
            commands=['python -c "print(1)"'],
        )
        spec = self.state.propose_plan(plan)
        # Latched safety stop
        self.safety.trip(source="test", reason="Emergency stop")
        ok, msg = self.state.authorize_and_execute(
            spec.action_id,
            checklist_ack=True,
            executor=self.state.execute_action_plan,
        )
        self.assertFalse(ok)
        self.assertIn("Safety stop", msg)


class ReplicateTabFlowTest(unittest.TestCase):
    def setUp(self):
        try:
            from desktop_app.tabs._qt import load_qt
            load_qt()
        except ImportError:
            self.skipTest("PyQt6/PySide6 not installed")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from desktop_app.tabs._qt import load_qt
        _, _, _, QtWidgets = load_qt()
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()
        self.state = AppState(store_path=":memory:")
        self.state.prefs = DesktopPrefs(
            last_roots=[str(self.workspace)],
            last_repo=str(self.workspace),
        )

    def tearDown(self):
        try:
            self.state.close()
        except Exception:
            pass
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_replicate_proposal_executes_file_write(self):
        from desktop_app.tabs.replicate import build_replicate_tab
        from desktop_app.tabs._qt import load_qt
        _, _, _, QtWidgets = load_qt()

        class _Status:
            def __init__(self):
                self.messages = []
            def showMessage(self, text, timeout=0):
                self.messages.append(text)

        status = _Status()
        root = build_replicate_tab(self.state, status)
        self.addCleanup(root.close)

        task_edit = root.findChildren(QtWidgets.QTextEdit)[0]
        buttons = {b.text(): b for b in root.findChildren(QtWidgets.QPushButton)}
        self.assertIn("Copy solution", buttons)
        self.assertIn("💾 Save to File…", buttons)
        self.assertIn("⚡ Apply with Habi", buttons)
        self.assertIn("Propose action", buttons)

        # Propose action with a target file in task
        target_file = "utils/token.py"
        task_edit.setPlainText(f"Implement token generator in {target_file}")
        buttons["Propose action"].click()
        self.app.processEvents()

        # Proposal card should have been built with Authorize & Execute
        auth_btns = [b for b in root.findChildren(QtWidgets.QPushButton) if b.text() == "Authorize & Execute"]
        self.assertEqual(len(auth_btns), 1)
        auth_btn = auth_btns[0]

        # Check all safety checkboxes if medium/high risk
        for cb in root.findChildren(QtWidgets.QCheckBox):
            cb.setChecked(True)
        self.app.processEvents()

        self.assertTrue(auth_btn.isEnabled())
        auth_btn.click()
        self.app.processEvents()

        # The action should have completed via state.execute_action_plan!
        pending = self.state.action_registry.list_pending()
        self.assertEqual(len(pending), 0)


if __name__ == "__main__":
    unittest.main()
