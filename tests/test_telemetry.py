"""Tests for the git telemetry bridge, incl. live redaction proof.

Builds a real temporary git repository (fake secrets only, never pushed) and
asserts that observation emits contract-valid episodes with secrets removed.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from mimir.episodes import BehavioralEpisode
from mimir.telemetry import (
    NotARepositoryError,
    TelemetryBoundaryError,
    observe_git_repo,
    redact_text,
)

FAKE_API_KEY = "sk-test-fake-key-0000000000000000"
FAKE_GITHUB_TOKEN = "ghp_fakefiletoken0000000000000000000000"


def _git(repo: Path, *args: str) -> str:
    cmd = [
        "git", "-C", str(repo),
        "-c", "user.name=Mimir Test",
        "-c", "user.email=mimir-test@example.com",
        "-c", "commit.gpgsign=false",
        *args,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout


class TelemetryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "demo"
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "main")

        (self.repo / "app.py").write_text(
            f'API_KEY = "{FAKE_API_KEY}"\n\n\ndef add(a, b):\n    return a + b\n',
            encoding="utf-8",
        )
        _git(self.repo, "add", "app.py")
        _git(self.repo, "commit", "-m", "add calculator with api key")
        self.sha_key_commit = _git(self.repo, "rev-parse", "HEAD").strip()

        with (self.repo / "app.py").open("a", encoding="utf-8") as handle:
            handle.write("\n\ndef mul(a, b):\n    return a * b\n")
        (self.repo / "notes.txt").write_text("plain notes\n", encoding="utf-8")
        _git(self.repo, "add", "app.py", "notes.txt")
        _git(self.repo, "commit", "-m", "extend calculator")
        self.sha_normal_commit = _git(self.repo, "rev-parse", "HEAD").strip()

        (self.repo / ".env").write_text(
            f"GITHUB_TOKEN={FAKE_GITHUB_TOKEN}\n", encoding="utf-8"
        )
        _git(self.repo, "add", ".env")
        _git(self.repo, "commit", "-m", "add env file")
        self.sha_env_commit = _git(self.repo, "rev-parse", "HEAD").strip()

    def tearDown(self):
        self._tmp.cleanup()

    def _observe(self, **kwargs):
        kwargs.setdefault("allowed_roots", [self.repo.parent])
        return observe_git_repo(self.repo, **kwargs)

    def _by_ref(self, episodes, sha):
        matches = [e for e in episodes if e.source_ref == sha]
        self.assertEqual(len(matches), 1, f"expected one episode for {sha}")
        return matches[0]

    def test_observes_transitions_newest_first(self):
        episodes = self._observe()
        self.assertEqual(len(episodes), 3)
        self.assertEqual(episodes[0].source_ref, self.sha_env_commit)
        normal = self._by_ref(episodes, self.sha_normal_commit)
        self.assertEqual({t.path for t in normal.transitions}, {"app.py", "notes.txt"})
        app_transition = next(t for t in normal.transitions if t.path == "app.py")
        self.assertEqual(app_transition.status, "modified")
        self.assertIn("def mul", app_transition.diff)
        self.assertGreater(app_transition.additions, 0)
        self.assertEqual(normal.context.language, "python")
        self.assertEqual(normal.actor, "Mimir Test")

    def test_episodes_satisfy_contract(self):
        for episode in self._observe():
            clone = BehavioralEpisode.from_dict(json.loads(json.dumps(episode.to_dict())))
            self.assertEqual(clone, episode)

    def test_redacts_assigned_secret_in_diff(self):
        key_episode = self._by_ref(self._observe(), self.sha_key_commit)
        dumped = json.dumps(key_episode.to_dict())
        self.assertNotIn(FAKE_API_KEY, dumped)
        self.assertIn("[REDACTED:assigned_secret]", dumped)
        self.assertGreaterEqual(key_episode.redactions_applied, 1)
        self.assertIn("assigned_secret", key_episode.redaction_kinds)

    def test_skips_env_file_content(self):
        env_episode = self._by_ref(self._observe(), self.sha_env_commit)
        dumped = json.dumps(env_episode.to_dict())
        self.assertNotIn(FAKE_GITHUB_TOKEN, dumped)
        self.assertEqual(len(env_episode.transitions), 1)
        transition = env_episode.transitions[0]
        self.assertEqual(transition.path, ".env")
        self.assertTrue(transition.skipped)
        self.assertEqual(transition.diff, "")
        self.assertTrue(transition.skip_reason)
        self.assertIn("secret_filename", env_episode.redaction_kinds)

    def test_withholds_authors_on_request(self):
        episodes = self._observe(include_authors=False)
        self.assertTrue(all(e.actor == "[withheld:author]" for e in episodes))

    def test_max_commits_limits_output(self):
        episodes = self._observe(max_commits=2)
        self.assertEqual(len(episodes), 2)

    def test_rejects_invalid_limits(self):
        with self.assertRaises(ValueError):
            self._observe(max_commits=0)
        with self.assertRaises(ValueError):
            self._observe(max_diff_chars=0)

    def test_whitelist_rejects_outside_path(self):
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaises(TelemetryBoundaryError):
                observe_git_repo(self.repo, allowed_roots=[other])

    def test_rejects_non_repo(self):
        with tempfile.TemporaryDirectory() as plain:
            with self.assertRaises(NotARepositoryError):
                observe_git_repo(plain, allowed_roots=[plain])

    def test_redact_text_covers_secret_shapes(self):
        cases = [
            ("key = AKIAIOSFODNN7EXAMPLE", "aws_access_key"),
            ("token ghp_abcdefghij1234567890 done", "github_token"),
            ("use xoxb-123456789012-abcdefghij here", "slack_token"),
            ("Authorization: Bearer abcDEF123._-~/+=", "bearer_token"),
            ("password: s3cr3t-value", "assigned_secret"),
            ("api-key: hunter2-value", "assigned_secret"),
            ("postgres://admin:p@ssw0rd@db:5432/app", "url_password"),
            ("-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----",
             "private_key"),
        ]
        for original, kind in cases:
            with self.subTest(kind=kind):
                result = redact_text(f"prefix {original} suffix")
                self.assertIn(kind, result.kinds)
                self.assertNotIn(original, result.text)
                self.assertIn("[REDACTED", result.text)

    def test_redact_text_leaves_plain_text_alone(self):
        text = "def add(a, b):\n    return a + b\n"
        result = redact_text(text)
        self.assertEqual(result.text, text)
        self.assertEqual(result.count, 0)
        self.assertEqual(result.kinds, ())


if __name__ == "__main__":
    unittest.main()
