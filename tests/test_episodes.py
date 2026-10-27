"""Tests for the BehavioralEpisode contract."""

import unittest

from mimir.episodes import (
    BehavioralEpisode,
    EpisodeContext,
    EpisodeOutcome,
    FileTransition,
)


def _transition(**overrides):
    fields = {
        "path": "mimir/telemetry.py",
        "status": "modified",
        "diff": "@@ -1 +1 @@\n-old\n+new\n",
        "additions": 1,
        "deletions": 1,
    }
    fields.update(overrides)
    return FileTransition(**fields)


def _episode(**overrides):
    fields = {
        "id": "ep_git_abc123",
        "source": "git",
        "repo": "/repos/demo",
        "source_ref": "abc123def456",
        "observed_at": "2026-10-27T08:30:00+00:00",
        "actor": "Ada",
        "summary": "refactor telemetry parsing",
        "context": EpisodeContext(scope="mimir", language="python"),
        "transitions": [_transition()],
        "outcome": EpisodeOutcome(),
        "redactions_applied": 0,
        "redaction_kinds": [],
    }
    fields.update(overrides)
    return BehavioralEpisode(**fields)


class EpisodeContractTest(unittest.TestCase):
    def test_round_trip(self):
        episode = _episode()
        clone = BehavioralEpisode.from_dict(episode.to_dict())
        self.assertEqual(clone, episode)
        self.assertEqual(clone.observed_at, "2026-10-27T08:30:00Z")

    def test_rejects_unknown_or_missing_keys(self):
        good = _episode().to_dict()
        with self.assertRaises(ValueError):
            BehavioralEpisode.from_dict(dict(good, bogus=1))
        bad = dict(good)
        del bad["source_ref"]
        with self.assertRaises(ValueError):
            BehavioralEpisode.from_dict(bad)

    def test_rejects_bad_file_status(self):
        with self.assertRaises(ValueError):
            _transition(status="teleported")

    def test_rejects_bad_outcome(self):
        with self.assertRaises(ValueError):
            EpisodeOutcome(build="maybe")

    def test_skipped_transition_carries_no_content(self):
        with self.assertRaises(ValueError):
            _transition(skipped=True, skip_reason="denied", diff="leak")
        with self.assertRaises(ValueError):
            _transition(skipped=True, skip_reason="")  # reason required
        with self.assertRaises(ValueError):
            _transition(skip_reason="set without skip")
        ok = _transition(skipped=True, skip_reason="denylisted", diff="")
        self.assertTrue(ok.skipped)

    def test_binary_transition_carries_no_content(self):
        with self.assertRaises(ValueError):
            _transition(binary=True, diff="bytes")
        ok = _transition(binary=True, diff="")
        self.assertTrue(ok.binary)

    def test_redaction_count_and_kinds_must_agree(self):
        with self.assertRaises(ValueError):
            _episode(redactions_applied=2, redaction_kinds=[])
        with self.assertRaises(ValueError):
            _episode(redactions_applied=0, redaction_kinds=["assigned_secret"])
        ok = _episode(redactions_applied=1, redaction_kinds=["assigned_secret"])
        self.assertEqual(ok.redactions_applied, 1)

    def test_rejects_non_transition_items(self):
        with self.assertRaises(ValueError):
            _episode(transitions=[{"path": "x"}])

    def test_context_language_optional(self):
        self.assertIsNone(EpisodeContext(scope="").to_dict()["language"])
        with self.assertRaises(ValueError):
            EpisodeContext(scope="", language="")


if __name__ == "__main__":
    unittest.main()
