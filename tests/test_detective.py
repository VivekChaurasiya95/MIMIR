"""Tests for the Detective inference engine (MVP Day 3).

All inference here is offline and deterministic: clustering plus the
heuristic generator run with no network, and the LLM hook is exercised
through injected fakes, never a live backend.
"""

import json
import unittest

from mimir.detective import (
    ClusteringResult,
    EpisodeCluster,
    LLMError,
    LLMOutputError,
    build_inference_prompt,
    cluster_episodes,
    infer_from_cluster,
    infer_from_cluster_with_llm,
    infer_principles,
)
from mimir.episodes import (
    BehavioralEpisode,
    EpisodeContext,
    EpisodeOutcome,
    FileTransition,
)
from mimir.principles import BehavioralPrinciple

ERROR_DIFF = (
    "@@ -1 +1 @@\n"
    "-    risky()\n"
    "+    try:\n"
    "+        risky()\n"
    "+    except IOError as exc:\n"
    "+        raise DomainError(str(exc))\n"
)
TEST_DIFF = (
    "@@ -1 +1 @@\n"
    "-    return a + b\n"
    "+    assert isinstance(a, int)\n"
    "+    return a + b\n"
)
PLAIN_DIFF = "@@ -1 +1 @@\n-old line\n+echo rotated\n"


def _transition(path, diff, **overrides):
    fields = {
        "path": path,
        "status": "modified",
        "diff": diff,
        "additions": 1,
        "deletions": 1,
    }
    fields.update(overrides)
    return FileTransition(**fields)


def _episode(id, summary, scope, language, transitions):
    return BehavioralEpisode(
        id=id,
        source="git",
        repo="/repos/demo",
        source_ref=f"sha_{id}",
        observed_at="2026-10-27T08:30:00+00:00",
        actor="Ada",
        summary=summary,
        context=EpisodeContext(scope=scope, language=language),
        transitions=transitions,
        outcome=EpisodeOutcome(),
        redactions_applied=0,
        redaction_kinds=[],
    )


def _error_episode(tag):
    return _episode(
        f"ep_err_{tag}",
        "harden error handling",
        "mimir",
        "python",
        [_transition(f"mimir/module_{tag}.py", ERROR_DIFF)],
    )


def _web_episode(tag):
    return _episode(
        f"ep_web_{tag}",
        "verify input handling",
        "web",
        "python",
        [_transition(f"web/app_{tag}.py", TEST_DIFF)],
    )


def _docs_episode():
    return _episode(
        "ep_docs_1",
        "update user guide",
        "docs",
        "markdown",
        [_transition("docs/guide.md", PLAIN_DIFF)],
    )


def _plain_episode(tag):
    return _episode(
        f"ep_ops_{tag}",
        "rotate logs nightly",
        "ops",
        None,
        [_transition(f"ops/rotate_{tag}.sh", PLAIN_DIFF)],
    )


def _llm_payload(**overrides):
    payload = {
        "name": "LLM Inferred Principle",
        "category": "architecture",
        "context_trigger": {"scope": "test scope", "language_agnostic": False},
        "latent_invariant": "Always do X in the observed setting.",
        "anti_patterns": ["Doing Y instead"],
    }
    payload.update(overrides)
    return json.dumps(payload)


def _error_cluster():
    result = cluster_episodes([_error_episode(t) for t in ("a", "b", "c")])
    assert len(result.clusters) == 1
    return result.clusters[0]


class ClusteringTest(unittest.TestCase):
    def test_groups_similar_and_separates_dissimilar(self):
        episodes = [
            _error_episode("a"),
            _error_episode("b"),
            _error_episode("c"),
            _web_episode("a"),
            _docs_episode(),
        ]
        result = cluster_episodes(episodes)
        self.assertEqual(len(result.clusters), 1)
        cluster = result.clusters[0]
        self.assertEqual(
            set(cluster.member_ids), {"ep_err_a", "ep_err_b", "ep_err_c"}
        )
        self.assertEqual(cluster.language, "python")
        self.assertEqual(cluster.scope, "mimir")
        self.assertEqual(
            {e.id for e in result.unclustered}, {"ep_web_a", "ep_docs_1"}
        )

    def test_empty_and_singleton_inputs(self):
        empty = cluster_episodes([])
        self.assertEqual(empty.clusters, ())
        self.assertEqual(empty.unclustered, ())
        single = cluster_episodes([_error_episode("a")])
        self.assertEqual(single.clusters, ())
        self.assertEqual(len(single.unclustered), 1)

    def test_min_cluster_size_one_keeps_singletons(self):
        result = cluster_episodes([_docs_episode()], min_cluster_size=1)
        self.assertEqual(len(result.clusters), 1)
        self.assertEqual(result.unclustered, ())

    def test_clustering_is_deterministic(self):
        episodes = [
            _error_episode("a"),
            _error_episode("b"),
            _error_episode("c"),
            _web_episode("a"),
            _docs_episode(),
        ]
        first = cluster_episodes(episodes)
        second = cluster_episodes(list(reversed(episodes)))
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_result_round_trip(self):
        result = cluster_episodes([_error_episode("a"), _error_episode("b")])
        clone = ClusteringResult.from_dict(json.loads(json.dumps(result.to_dict())))
        self.assertEqual(clone, result)

    def test_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            cluster_episodes("nope")
        with self.assertRaises(ValueError):
            cluster_episodes([{"id": "x"}])
        for bad in (0, 0.0, 1.5, "high", True):
            with self.assertRaises(ValueError, msg=f"threshold={bad!r}"):
                cluster_episodes([_error_episode("a")], similarity_threshold=bad)
        with self.assertRaises(ValueError):
            cluster_episodes([_error_episode("a")], min_cluster_size=0)


class HeuristicInferenceTest(unittest.TestCase):
    def test_infers_error_handling_principle(self):
        principle = infer_from_cluster(_error_cluster())
        self.assertTrue(principle.id.startswith("bp_error_handling_"))
        self.assertIn("Defensive Error Isolation", principle.name)
        self.assertEqual(principle.category, "architecture")
        self.assertIn("Observed across 3 episodes", principle.latent_invariant)
        self.assertEqual(
            set(principle.inspectable_traces),
            {"ep_err_a", "ep_err_b", "ep_err_c"},
        )
        status = principle.epistemic_status
        self.assertAlmostEqual(status.confidence_score, 0.70)
        self.assertEqual(status.supporting_episodes_count, 3)
        self.assertEqual(status.contradictions_count, 0)
        self.assertTrue(status.first_inferred.endswith("Z"))
        self.assertFalse(principle.context_trigger.language_agnostic)
        clone = BehavioralPrinciple.from_dict(
            json.loads(json.dumps(principle.to_dict()))
        )
        self.assertEqual(clone, principle)

    def test_fallback_principle_stays_evidence_bound(self):
        result = cluster_episodes([_plain_episode("a"), _plain_episode("b")])
        self.assertEqual(len(result.clusters), 1)
        principle = infer_from_cluster(result.clusters[0])
        self.assertTrue(principle.id.startswith("bp_recurring_"))
        self.assertEqual(principle.category, "workflow")
        self.assertIn("ops", principle.latent_invariant)
        self.assertIn("Observed across 2 episodes", principle.latent_invariant)
        self.assertTrue(principle.context_trigger.language_agnostic)
        clone = BehavioralPrinciple.from_dict(principle.to_dict())
        self.assertEqual(clone, principle)

    def test_infer_principles_end_to_end(self):
        episodes = [
            _error_episode("a"),
            _error_episode("b"),
            _error_episode("c"),
            _web_episode("a"),
            _web_episode("b"),
            _docs_episode(),
        ]
        principles = infer_principles(episodes)
        self.assertEqual(len(principles), 2)
        self.assertEqual(len({p.id for p in principles}), 2)
        for principle in principles:
            clone = BehavioralPrinciple.from_dict(
                json.loads(json.dumps(principle.to_dict()))
            )
            self.assertEqual(clone, principle)

    def test_infer_principles_empty(self):
        self.assertEqual(infer_principles([]), [])

    def test_rejects_non_cluster(self):
        with self.assertRaises(ValueError):
            infer_from_cluster({"id": "cl_x"})
        with self.assertRaises(ValueError):
            infer_principles([_error_episode("a")], llm_fn=42)


class LLMHookTest(unittest.TestCase):
    def test_llm_fields_with_evidence_bound_metadata(self):
        seen: list[str] = []

        def fake_llm(prompt):
            seen.append(prompt)
            return _llm_payload()

        principle = infer_from_cluster_with_llm(_error_cluster(), fake_llm)
        self.assertEqual(principle.name, "LLM Inferred Principle")
        self.assertEqual(
            principle.latent_invariant, "Always do X in the observed setting."
        )
        self.assertTrue(principle.id.startswith("bp_error_handling_"))
        self.assertEqual(principle.epistemic_status.supporting_episodes_count, 3)
        self.assertEqual(
            set(principle.inspectable_traces),
            {"ep_err_a", "ep_err_b", "ep_err_c"},
        )
        self.assertEqual(len(seen), 1)
        self.assertIn("ep_err_a", seen[0])
        self.assertIn("JSON ONLY", seen[0])
        clone = BehavioralPrinciple.from_dict(principle.to_dict())
        self.assertEqual(clone, principle)

    def test_prompt_truncates_diffs_and_episode_list(self):
        big = _episode(
            "ep_big_1",
            "large change",
            "mimir",
            "python",
            [_transition("mimir/big.py", "+" + "x" * 5000)],
        )
        cluster = EpisodeCluster(
            id="cl_big",
            members=(big, _error_episode("a")),
            language="python",
            scope="mimir",
        )
        prompt = build_inference_prompt(
            cluster, max_episodes=1, max_diff_chars=100
        )
        self.assertIn("[TRUNCATED", prompt)
        self.assertNotIn("x" * 5000, prompt)
        self.assertIn("more episode(s)", prompt)
        self.assertIn("ep_big_1", prompt)
        self.assertNotIn("ep_err_a", prompt)

    def test_prompt_marks_skipped_without_content(self):
        skipped = _episode(
            "ep_skip_1",
            "add env file",
            "",
            None,
            [
                FileTransition(
                    path=".env",
                    status="added",
                    diff="",
                    additions=0,
                    deletions=0,
                    skipped=True,
                    skip_reason="denylisted secret-bearing filename",
                )
            ],
        )
        cluster = EpisodeCluster(
            id="cl_skip", members=(skipped,), language=None, scope=""
        )
        prompt = build_inference_prompt(cluster)
        self.assertIn("[.env skipped: denylisted secret-bearing filename]", prompt)

    def test_accepts_fenced_json_and_llm_confidence(self):
        def fake_llm(prompt):
            return "```json\n" + _llm_payload(confidence=0.99) + "\n```"

        principle = infer_from_cluster_with_llm(_error_cluster(), fake_llm)
        self.assertAlmostEqual(principle.epistemic_status.confidence_score, 0.99)

    def test_rejects_unparseable_output(self):
        for bad in ("not json {{{", "[1, 2]", "", "   "):
            with self.assertRaises(LLMOutputError, msg=f"output={bad!r}"):
                infer_from_cluster_with_llm(_error_cluster(), lambda p: bad)

    def test_rejects_contract_invalid_output(self):
        missing = json.loads(_llm_payload())
        del missing["latent_invariant"]
        with self.assertRaises(LLMOutputError):
            infer_from_cluster_with_llm(
                _error_cluster(), lambda p: json.dumps(missing)
            )
        with self.assertRaises(LLMOutputError):
            infer_from_cluster_with_llm(
                _error_cluster(), lambda p: _llm_payload(confidence=2.0)
            )
        with self.assertRaises(LLMOutputError):
            infer_from_cluster_with_llm(
                _error_cluster(), lambda p: _llm_payload(name="  ")
            )

    def test_wraps_llm_fn_failures(self):
        def boom(prompt):
            raise RuntimeError("backend down")

        with self.assertRaises(LLMError):
            infer_from_cluster_with_llm(_error_cluster(), boom)
        with self.assertRaises(ValueError):
            infer_from_cluster_with_llm(_error_cluster(), "not-callable")

    def test_infer_principles_routes_each_cluster_through_llm(self):
        calls: list[str] = []

        def fake_llm(prompt):
            calls.append(prompt)
            return _llm_payload()

        episodes = [
            _error_episode("a"),
            _error_episode("b"),
            _web_episode("a"),
            _web_episode("b"),
        ]
        principles = infer_principles(episodes, llm_fn=fake_llm)
        self.assertEqual(len(principles), 2)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(p.name == "LLM Inferred Principle" for p in principles))


if __name__ == "__main__":
    unittest.main()
