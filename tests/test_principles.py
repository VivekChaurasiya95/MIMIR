"""Tests for the BehavioralPrinciple contract (README section 4)."""

import unittest

from mimir.principles import (
    BehavioralPrinciple,
    ContextTrigger,
    EpistemicStatus,
)

README_EXAMPLE = {
    "id": "bp_error_isolation_019",
    "name": "Defensive Domain Isolation on Untrusted Boundaries",
    "category": "architecture",
    "context_trigger": {
        "scope": "Third-party network I/O, database drivers, or filesystem access",
        "language_agnostic": True,
    },
    "latent_invariant": (
        "Never let third-party driver or HTTP exceptions leak into domain "
        "logic. Always catch, transform, and re-raise into typed domain "
        "exceptions."
    ),
    "anti_patterns": [
        "Catching raw Exception without re-raising",
        "Allowing framework-specific exceptions to bubble up to controllers",
    ],
    "epistemic_status": {
        "confidence_score": 0.94,
        "supporting_episodes_count": 18,
        "contradictions_count": 1,
        "first_inferred": "2026-10-12T10:00:00Z",
        "last_validated": "2026-10-27T08:30:00Z",
    },
    "inspectable_traces": [
        "commit_ref_a81f9b",
        "session_diff_payment_gateway",
    ],
}


class PrincipleContractTest(unittest.TestCase):
    def test_readme_example_round_trip(self):
        principle = BehavioralPrinciple.from_dict(README_EXAMPLE)
        self.assertEqual(principle.id, "bp_error_isolation_019")
        self.assertEqual(principle.epistemic_status.confidence_score, 0.94)
        self.assertTrue(principle.context_trigger.language_agnostic)
        self.assertEqual(principle.to_dict(), README_EXAMPLE)

    def test_rejects_unknown_keys(self):
        bad = dict(README_EXAMPLE, extra_field="drift")
        with self.assertRaises(ValueError):
            BehavioralPrinciple.from_dict(bad)

    def test_rejects_missing_keys(self):
        bad = dict(README_EXAMPLE)
        del bad["latent_invariant"]
        with self.assertRaises(ValueError):
            BehavioralPrinciple.from_dict(bad)

    def test_rejects_bad_confidence(self):
        for confidence in (-0.1, 1.5, "high", True, None):
            status = dict(README_EXAMPLE["epistemic_status"], confidence_score=confidence)
            bad = dict(README_EXAMPLE, epistemic_status=status)
            with self.assertRaises(ValueError, msg=f"confidence={confidence!r}"):
                BehavioralPrinciple.from_dict(bad)

    def test_rejects_bad_timestamps(self):
        for stamp in ("not-a-time", "2026-10-27 08:30:00", "", None, 12345):
            status = dict(README_EXAMPLE["epistemic_status"], last_validated=stamp)
            bad = dict(README_EXAMPLE, epistemic_status=status)
            with self.assertRaises(ValueError, msg=f"stamp={stamp!r}"):
                BehavioralPrinciple.from_dict(bad)

    def test_rejects_non_bool_language_flag(self):
        trigger = dict(README_EXAMPLE["context_trigger"], language_agnostic=1)
        bad = dict(README_EXAMPLE, context_trigger=trigger)
        with self.assertRaises(ValueError):
            BehavioralPrinciple.from_dict(bad)

    def test_rejects_empty_invariant(self):
        bad = dict(README_EXAMPLE, latent_invariant="  ")
        with self.assertRaises(ValueError):
            BehavioralPrinciple.from_dict(bad)

    def test_epistemic_new_helper(self):
        status = EpistemicStatus.new(0.5, supporting=2, contradictions=1)
        self.assertEqual(status.confidence_score, 0.5)
        self.assertEqual(status.supporting_episodes_count, 2)
        self.assertEqual(status.contradictions_count, 1)
        self.assertTrue(status.first_inferred.endswith("Z"))
        # Round-trips through the contract.
        clone = EpistemicStatus.from_dict(status.to_dict())
        self.assertEqual(clone, status)

    def test_trigger_round_trip(self):
        trigger = ContextTrigger.from_dict(README_EXAMPLE["context_trigger"])
        self.assertEqual(trigger.to_dict(), README_EXAMPLE["context_trigger"])


if __name__ == "__main__":
    unittest.main()
