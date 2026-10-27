"""Tests for the principle memory store (Session A, Days 4 & 5).

All retrieval here is offline: the default hash embedder is deterministic
with no network, and API-style backends are exercised through injected
fakes. File-backed stores close before temporary directories clean up so
no SQLite lock survives on Windows.
"""

import tempfile
import unittest
from pathlib import Path

from mimir.principles import (
    BehavioralPrinciple,
    ContextTrigger,
    EpistemicStatus,
)
from mimir.store import (
    EmbeddingError,
    HashEmbeddingProvider,
    PrincipleNotFoundError,
    PrincipleStore,
    StoreError,
)

ERROR_SCOPE = "Third-party network I/O, database drivers, filesystem access"
ERROR_INVARIANT = (
    "Never let database driver exceptions leak into domain logic; "
    "catch and re-raise typed domain errors."
)
TEST_SCOPE = "Python test suites"
TEST_INVARIANT = (
    "Cover every behavior change with automated pytest cases in the same commit."
)
DOCS_SCOPE = "Markdown user guides"
DOCS_INVARIANT = (
    "Rewrite the affected guide section alongside each user-visible change."
)


def _principle(id, scope, invariant, confidence=0.8):
    return BehavioralPrinciple(
        id=id,
        name=f"Principle {id}",
        category="architecture",
        context_trigger=ContextTrigger(scope=scope, language_agnostic=True),
        latent_invariant=invariant,
        anti_patterns=["Sloppy alternative"],
        epistemic_status=EpistemicStatus(
            confidence_score=confidence,
            supporting_episodes_count=3,
            contradictions_count=0,
            first_inferred="2026-10-12T10:00:00Z",
            last_validated="2026-10-27T08:30:00Z",
        ),
        inspectable_traces=["ep_seed_1"],
    )


def _error_principle():
    return _principle("bp_error_001", ERROR_SCOPE, ERROR_INVARIANT, 0.90)


def _test_principle():
    return _principle("bp_test_002", TEST_SCOPE, TEST_INVARIANT, 0.50)


def _docs_principle():
    return _principle("bp_docs_003", DOCS_SCOPE, DOCS_INVARIANT, 0.80)


class KeywordProvider:
    """Two-topic fake backend: database vs. pytest affinity."""

    dimension = 2

    def embed_texts(self, texts):
        return [
            [
                1.0 if "database" in text.lower() else 0.0,
                1.0 if "pytest" in text.lower() else 0.0,
            ]
            for text in texts
        ]


class PrincipleStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = PrincipleStore(Path(self._tmp.name) / "memory.db")
        self.addCleanup(self.store.close)

    def test_save_and_list_all_round_trip(self):
        self.store.save(_test_principle())
        self.store.save(_error_principle())
        listed = self.store.list_all()
        self.assertEqual([p.id for p in listed], ["bp_error_001", "bp_test_002"])
        self.assertEqual(listed[0], _error_principle())

    def test_save_overwrites_and_preserves_applied_stamp(self):
        self.store.save(_error_principle())
        self.store.query_relevant("database driver exceptions", min_confidence=0.0)
        stamped = self.store.last_applied_at("bp_error_001")
        self.assertTrue(stamped.endswith("Z"))
        revised = _principle(
            "bp_error_001", ERROR_SCOPE, ERROR_INVARIANT + " Always.", 0.95
        )
        self.store.save(revised)
        self.assertEqual(self.store.last_applied_at("bp_error_001"), stamped)
        self.assertEqual(self.store.list_all()[0], revised)

    def test_delete(self):
        self.store.save(_error_principle())
        self.assertTrue(self.store.delete("bp_error_001"))
        self.assertFalse(self.store.delete("bp_error_001"))
        self.assertEqual(self.store.list_all(), [])
        with self.assertRaises(PrincipleNotFoundError):
            self.store.last_applied_at("bp_error_001")

    def test_persists_across_reopen(self):
        self.store.save(_error_principle())
        self.store.save(_docs_principle())
        self.store.close()
        reopened = PrincipleStore(Path(self._tmp.name) / "memory.db")
        self.addCleanup(reopened.close)
        self.assertEqual(
            [p.id for p in reopened.list_all()],
            ["bp_docs_003", "bp_error_001"],
        )

    def test_memory_store_default(self):
        with PrincipleStore() as memory:
            memory.save(_error_principle())
            hits = memory.query_relevant(
                "database driver exceptions", min_confidence=0.0
            )
            self.assertEqual([p.id for p in hits], ["bp_error_001"])

    def test_rejects_non_principle_save(self):
        with self.assertRaises(ValueError):
            self.store.save({"id": "bp_x"})
        with self.assertRaises(EmbeddingError):
            PrincipleStore(embedder="not-a-provider")


class RetrievalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = PrincipleStore(Path(self._tmp.name) / "memory.db")
        self.addCleanup(self.store.close)
        self.store.save(_error_principle())
        self.store.save(_test_principle())
        self.store.save(_docs_principle())

    def test_query_ranks_by_semantic_similarity(self):
        hits = self.store.query_relevant(
            "how should database driver exceptions be handled?",
            min_confidence=0.0,
        )
        self.assertEqual(hits[0].id, "bp_error_001")
        self.assertEqual(
            {p.id for p in hits},
            {"bp_error_001", "bp_docs_003", "bp_test_002"},
        )

    def test_query_respects_min_confidence_and_top_k(self):
        hits = self.store.query_relevant("database pytest guides")
        self.assertTrue(all(p.id != "bp_test_002" for p in hits))
        single = self.store.query_relevant(
            "database pytest guides", top_k=1, min_confidence=0.0
        )
        self.assertEqual(len(single), 1)

    def test_query_stamps_only_returned_principles(self):
        self.assertIsNone(self.store.last_applied_at("bp_error_001"))
        hits = self.store.query_relevant(
            "database driver exceptions", top_k=1, min_confidence=0.0
        )
        self.assertEqual([p.id for p in hits], ["bp_error_001"])
        self.assertTrue(self.store.last_applied_at("bp_error_001").endswith("Z"))
        self.assertIsNone(self.store.last_applied_at("bp_test_002"))

    def test_query_empty_store_returns_empty(self):
        with PrincipleStore() as empty:
            self.assertEqual(empty.query_relevant("anything"), [])

    def test_query_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            self.store.query_relevant("  ")
        with self.assertRaises(ValueError):
            self.store.query_relevant(123)
        with self.assertRaises(ValueError):
            self.store.query_relevant("database", top_k=0)
        with self.assertRaises(ValueError):
            self.store.query_relevant("database", min_confidence=1.5)


class ConfidenceAndProviderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = PrincipleStore(Path(self._tmp.name) / "memory.db")
        self.addCleanup(self.store.close)
        self.store.save(_error_principle())

    def _fetched(self, principle_id="bp_error_001"):
        matches = [p for p in self.store.list_all() if p.id == principle_id]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_update_confidence_reinforces(self):
        self.store.update_confidence("bp_error_001", 0.05, "ep_new_9")
        updated = self._fetched()
        self.assertAlmostEqual(updated.epistemic_status.confidence_score, 0.95)
        self.assertEqual(updated.epistemic_status.supporting_episodes_count, 4)
        self.assertEqual(updated.epistemic_status.contradictions_count, 0)
        self.assertIn("ep_new_9", updated.inspectable_traces)
        self.assertTrue(updated.epistemic_status.last_validated.endswith("Z"))
        # Re-applying the same evidence never duplicates the trace.
        self.store.update_confidence("bp_error_001", 0.01, "ep_new_9")
        self.assertEqual(
            list(self._fetched().inspectable_traces).count("ep_new_9"), 1
        )

    def test_update_confidence_contradiction_clamps(self):
        self.store.update_confidence("bp_error_001", -2.0, "ep_contra_1")
        updated = self._fetched()
        self.assertEqual(updated.epistemic_status.confidence_score, 0.0)
        self.assertEqual(updated.epistemic_status.contradictions_count, 1)
        self.assertEqual(updated.epistemic_status.supporting_episodes_count, 3)

    def test_update_confidence_rejects_bad_inputs(self):
        with self.assertRaises(PrincipleNotFoundError):
            self.store.update_confidence("bp_missing", 0.1, "ep_1")
        with self.assertRaises(ValueError):
            self.store.update_confidence("bp_error_001", 0.1, "  ")
        for bad in (float("nan"), float("inf"), "high", True, None):
            with self.assertRaises(ValueError, msg=f"delta={bad!r}"):
                self.store.update_confidence("bp_error_001", bad, "ep_1")

    def test_dimension_mismatch_raises(self):
        self.store.close()
        narrow = PrincipleStore(
            Path(self._tmp.name) / "memory.db",
            embedder=HashEmbeddingProvider(dim=64),
        )
        self.addCleanup(narrow.close)
        with self.assertRaises(StoreError):
            narrow.query_relevant("database", min_confidence=0.0)
        with self.assertRaises(StoreError):
            narrow.save(_docs_principle())

    def test_custom_provider_is_pluggable(self):
        with PrincipleStore(embedder=KeywordProvider()) as custom:
            custom.save(_error_principle())
            custom.save(_test_principle())
            hits = custom.query_relevant("database failure", min_confidence=0.0)
            self.assertEqual(hits[0].id, "bp_error_001")

    def test_provider_failures_raise_embedding_error(self):
        class ExplodingProvider:
            dimension = 4

            def embed_texts(self, texts):
                raise RuntimeError("backend down")

        class MisshapenProvider:
            dimension = 4

            def embed_texts(self, texts):
                return [["not", "vectors"]]

        with PrincipleStore(embedder=ExplodingProvider()) as broken:
            with self.assertRaises(EmbeddingError):
                broken.save(_error_principle())
        with PrincipleStore(embedder=MisshapenProvider()) as misshapen:
            with self.assertRaises(EmbeddingError):
                misshapen.save(_error_principle())


if __name__ == "__main__":
    unittest.main()
