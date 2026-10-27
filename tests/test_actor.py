"""Tests for the replication actor (Session B, Day 6).

All synthesis is offline: the LLM hook is exercised through scripted
fakes, and the no-LLM path emits a deterministic scaffold. Covers the
spec's five required areas: prompt compilation, empty-store fallback,
pluggable LLM integration, anti-pattern detection, and contrast output.
"""

import json
import unittest

from mimir.actor import (
    ActorError,
    ReplicationActor,
    ReplicationRequest,
    ReplicationResult,
    build_baseline_prompt,
    check_compliance,
)
from mimir.principles import (
    BehavioralPrinciple,
    ContextTrigger,
    EpistemicStatus,
)
from mimir.store import PrincipleStore

ERROR_SCOPE = "Third-party network I/O, database drivers"
ERROR_INVARIANT = (
    "Never let database driver exceptions leak into domain logic; "
    "catch and re-raise typed domain errors."
)
ERROR_ANTI = (
    "Catching `except Exception:` without re-raising a typed error",
    "Letting raw driver errors reach callers",
)
TEST_SCOPE = "Python test suites"
TEST_INVARIANT = "Cover every behavior change with automated pytest cases."
TEST_ANTI = ("Merging behavior changes without tests",)

STEERED_SOLUTION = (
    "try:\n    sync()\nexcept DriverError as exc:\n"
    "    raise SyncFailed(str(exc))"
)
BASELINE_SOLUTION = "result = sync()  # generic unconditioned attempt"


def _principle(id, scope, invariant, anti_patterns, confidence=0.9):
    return BehavioralPrinciple(
        id=id,
        name=f"Principle {id}",
        category="architecture",
        context_trigger=ContextTrigger(scope=scope, language_agnostic=True),
        latent_invariant=invariant,
        anti_patterns=list(anti_patterns),
        epistemic_status=EpistemicStatus(
            confidence_score=confidence,
            supporting_episodes_count=3,
            contradictions_count=0,
            first_inferred="2026-10-12T10:00:00Z",
            last_validated="2026-10-27T08:30:00Z",
        ),
        inspectable_traces=["ep_seed_1"],
    )


def _error_principle(confidence=0.9):
    return _principle("bp_error_001", ERROR_SCOPE, ERROR_INVARIANT, ERROR_ANTI, confidence)


def _test_principle(confidence=0.8):
    return _principle("bp_test_002", TEST_SCOPE, TEST_INVARIANT, TEST_ANTI, confidence)


class ScriptedLLM:
    """Fake backend: steered vs. baseline outputs by prompt kind."""

    def __init__(self):
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "replication engine" in prompt:
            return STEERED_SOLUTION
        return BASELINE_SOLUTION


class PromptCompilationTest(unittest.TestCase):
    def setUp(self):
        self.store = PrincipleStore()
        self.addCleanup(self.store.close)
        self.actor = ReplicationActor(self.store)

    def test_steering_prompt_formats_constraints(self):
        prompt = self.actor.compile_prompt(
            "sync the warehouse", [_error_principle(), _test_principle()]
        )
        self.assertIn("sync the warehouse", prompt)
        self.assertIn("Mandatory invariants", prompt)
        self.assertIn(ERROR_INVARIANT, prompt)
        self.assertIn(TEST_INVARIANT, prompt)
        self.assertIn("Forbidden anti-patterns", prompt)
        self.assertIn(ERROR_ANTI[0], prompt)
        self.assertIn("bp_error_001", prompt)
        self.assertIn(ERROR_SCOPE, prompt)

    def test_empty_principles_prompt_marks_none(self):
        prompt = self.actor.compile_prompt("sync the warehouse", [])
        self.assertIn("(none retrieved", prompt)
        self.assertIn("(none.)", prompt)

    def test_baseline_prompt_is_unconditioned(self):
        prompt = build_baseline_prompt("sync the warehouse")
        self.assertIn("sync the warehouse", prompt)
        self.assertNotIn("Mandatory invariants", prompt)
        self.assertNotIn(ERROR_INVARIANT, prompt)

    def test_compile_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            self.actor.compile_prompt("  ", [])
        with self.assertRaises(ValueError):
            self.actor.compile_prompt(123, [])
        with self.assertRaises(ValueError):
            self.actor.compile_prompt("task", [{"id": "x"}])


class ReplicationTest(unittest.TestCase):
    def setUp(self):
        self.store = PrincipleStore()
        self.addCleanup(self.store.close)
        self.store.save(_error_principle())
        self.llm = ScriptedLLM()
        self.actor = ReplicationActor(self.store, llm_fn=self.llm)

    def test_pluggable_llm_receives_conditioning(self):
        result = self.actor.replicate(
            ReplicationRequest(task_description="handle database driver failure")
        )
        self.assertEqual(
            [p.id for p in result.applied_principles], ["bp_error_001"]
        )
        self.assertEqual(result.replicated_solution, STEERED_SOLUTION)
        steering_prompt = self.llm.prompts[0]
        self.assertIn(ERROR_INVARIANT, steering_prompt)
        self.assertIn(ERROR_ANTI[0], steering_prompt)
        clone = ReplicationResult.from_dict(
            json.loads(json.dumps(result.to_dict()))
        )
        self.assertEqual(clone, result)

    def test_contrast_baseline_generated_and_distinct(self):
        result = self.actor.replicate(
            ReplicationRequest(task_description="handle database driver failure")
        )
        self.assertEqual(len(self.llm.prompts), 2)
        self.assertEqual(result.contrast_baseline, BASELINE_SOLUTION)
        self.assertNotEqual(
            result.replicated_solution, result.contrast_baseline
        )
        self.assertNotIn(ERROR_INVARIANT, self.llm.prompts[1])

    def test_baseline_opt_out_returns_none(self):
        result = self.actor.replicate(
            ReplicationRequest(task_description="handle database driver failure"),
            generate_contrast_baseline=False,
        )
        self.assertIsNone(result.contrast_baseline)
        self.assertEqual(len(self.llm.prompts), 1)

    def test_request_thresholds_reach_store(self):
        self.store.save(_test_principle(confidence=0.5))
        strict = self.actor.replicate(
            ReplicationRequest(
                task_description="handle database driver failure",
                min_confidence=0.95,
            )
        )
        self.assertEqual(strict.applied_principles, ())
        self.assertEqual(strict.replicated_solution, STEERED_SOLUTION)
        capped = self.actor.replicate(
            ReplicationRequest(
                task_description="handle database driver failure",
                min_confidence=0.0,
                top_k_principles=1,
            )
        )
        self.assertEqual(len(capped.applied_principles), 1)


class GracefulFallbackTest(unittest.TestCase):
    def test_empty_store_with_llm(self):
        store = PrincipleStore()
        self.addCleanup(store.close)
        actor = ReplicationActor(store, llm_fn=ScriptedLLM())
        result = actor.replicate(ReplicationRequest(task_description="new task"))
        self.assertEqual(result.applied_principles, ())
        self.assertEqual(result.replicated_solution, STEERED_SOLUTION)
        self.assertEqual(result.contrast_baseline, BASELINE_SOLUTION)
        report = result.compliance_report
        self.assertTrue(report["compliant"])
        self.assertEqual(report["anti_patterns_checked"], 0)
        self.assertEqual(report["compliance_score"], 1.0)

    def test_no_llm_scaffold_offline(self):
        store = PrincipleStore()
        self.addCleanup(store.close)
        store.save(_error_principle())
        actor = ReplicationActor(store)
        result = actor.replicate(
            ReplicationRequest(task_description="handle database driver failure")
        )
        self.assertIn("[MIMIR scaffold", result.replicated_solution)
        self.assertIn("handle database driver failure", result.replicated_solution)
        self.assertIn(ERROR_INVARIANT, result.replicated_solution)
        self.assertIsNone(result.contrast_baseline)
        self.assertEqual(
            [p.id for p in result.applied_principles], ["bp_error_001"]
        )
        # The scaffold quotes its own constraints, so the check runs but
        # may self-flag; it must never crash and must count honestly.
        self.assertEqual(result.compliance_report["anti_patterns_checked"], 2)

    def test_no_llm_empty_store(self):
        store = PrincipleStore()
        self.addCleanup(store.close)
        actor = ReplicationActor(store)
        result = actor.replicate(ReplicationRequest(task_description="new task"))
        self.assertIn("(none retrieved.)", result.replicated_solution)
        self.assertIsNone(result.contrast_baseline)


class ComplianceTest(unittest.TestCase):
    def test_catches_forbidden_literal(self):
        principle = _principle(
            "bp_eval_1",
            "untrusted input",
            "Never execute untrusted input as code.",
            (
                "Calling `eval(` on untrusted input",
                "Shipping code with `TODO:` markers",
            ),
        )
        report = check_compliance("result = eval(user_input)", [principle])
        self.assertFalse(report["compliant"])
        self.assertEqual(report["anti_patterns_checked"], 2)
        self.assertEqual(len(report["violations"]), 1)
        violation = report["violations"][0]
        self.assertEqual(violation["principle_id"], "bp_eval_1")
        self.assertEqual(violation["matched_text"], "eval(")
        self.assertAlmostEqual(report["compliance_score"], 0.5)
        self.assertEqual(report["checked_principle_ids"], ["bp_eval_1"])

    def test_clean_solution_passes(self):
        report = check_compliance(STEERED_SOLUTION, [_error_principle()])
        self.assertTrue(report["compliant"])
        self.assertEqual(report["violations"], [])
        self.assertEqual(report["compliance_score"], 1.0)

    def test_full_phrase_backstop_and_short_phrase_limit(self):
        principle = _principle(
            "bp_docs_9",
            "guides",
            "Keep guides accurate.",
            (
                "TODO leftover markers in shipped code",
                "bad code",
            ),
        )
        report = check_compliance(
            "Remove todo leftover markers in shipped code, it is bad code",
            [principle],
        )
        self.assertEqual(len(report["violations"]), 1)
        self.assertIn("TODO leftover", report["violations"][0]["anti_pattern"])

    def test_vacuous_pass_and_bad_inputs(self):
        report = check_compliance("anything at all", [])
        self.assertTrue(report["compliant"])
        self.assertEqual(report["compliance_score"], 1.0)
        with self.assertRaises(ValueError):
            check_compliance(123, [])
        with self.assertRaises(ValueError):
            check_compliance("solution", ["not-a-principle"])


class ContractAndErrorTest(unittest.TestCase):
    def test_request_round_trip_and_defaults(self):
        minimal = ReplicationRequest.from_dict({"task_description": "do it"})
        self.assertIsNone(minimal.target_language)
        self.assertIsNone(minimal.target_scope)
        self.assertEqual(minimal.min_confidence, 0.70)
        self.assertEqual(minimal.top_k_principles, 5)
        full = ReplicationRequest(
            task_description="do it",
            target_language="python",
            target_scope="mimir",
            min_confidence=0.9,
            top_k_principles=2,
        )
        self.assertEqual(ReplicationRequest.from_dict(full.to_dict()), full)
        for bad in ("  ", "", 123, None):
            with self.assertRaises(ValueError, msg=f"task={bad!r}"):
                ReplicationRequest(task_description=bad)
        with self.assertRaises(ValueError):
            ReplicationRequest(task_description="t", min_confidence=1.5)
        with self.assertRaises(ValueError):
            ReplicationRequest(task_description="t", top_k_principles=0)
        with self.assertRaises(ValueError):
            ReplicationRequest(task_description="t", target_language="")

    def test_actor_rejects_bad_construction_and_calls(self):
        store = PrincipleStore()
        self.addCleanup(store.close)
        with self.assertRaises(ValueError):
            ReplicationActor("not-a-store")
        with self.assertRaises(ValueError):
            ReplicationActor(store, llm_fn=42)
        actor = ReplicationActor(store, llm_fn=ScriptedLLM())
        with self.assertRaises(ValueError):
            actor.replicate("not-a-request")
        with self.assertRaises(ValueError):
            actor.replicate(
                ReplicationRequest(task_description="t"),
                generate_contrast_baseline="yes",
            )

    def test_llm_failures_raise_actor_error(self):
        store = PrincipleStore()
        self.addCleanup(store.close)

        def exploding(prompt):
            raise RuntimeError("backend down")

        with self.assertRaises(ActorError):
            ReplicationActor(store, llm_fn=exploding).replicate(
                ReplicationRequest(task_description="t")
            )
        with self.assertRaises(ActorError):
            ReplicationActor(store, llm_fn=lambda p: "   ").replicate(
                ReplicationRequest(task_description="t")
            )


if __name__ == "__main__":
    unittest.main()
