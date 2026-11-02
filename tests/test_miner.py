"""Real-disk miner tests: true git repos, true `git log -p` parsing."""

from __future__ import annotations

import subprocess

import pytest

from mimir import miner


def _git(repo: str, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture()
def history_repo(tmp_path):
    repo = str(tmp_path / "proj")
    (tmp_path / "proj").mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "miner@test.local")
    _git(repo, "config", "user.name", "Miner Test")
    (tmp_path / "proj" / "svc.py").write_text(
        "import requests\n\n\ndef call(url):\n    return requests.get(url)\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add unguarded service call")
    (tmp_path / "proj" / "svc.py").write_text(
        "import requests\n\n\ndef call(url):\n"
        "    try:\n"
        "        return requests.get(url, timeout=5.0)\n"
        "    except Exception:\n"
        "        raise\n",
        encoding="utf-8",
    )
    _git(repo, "commit", "-q", "-am", "harden service call")
    return repo


def test_stream_micro_deltas_parses_real_patches(history_repo):
    deltas = miner.stream_micro_deltas(history_repo, max_commits=5)
    assert len(deltas) >= 2
    paths = {d.file_path for d in deltas}
    assert paths == {"svc.py"}
    second = [d for d in deltas if "harden" in d.subject]
    assert second
    assert any("timeout=5.0" in line for line in second[0].added_lines)
    assert all(len(d.commit_sha) == 40 for d in deltas)


def test_stream_rejects_non_repo(tmp_path):
    with pytest.raises(RuntimeError):
        miner.stream_micro_deltas(str(tmp_path), max_commits=2)


def test_heuristic_rules_flag_missing_timeout(history_repo):
    deltas = miner.stream_micro_deltas(history_repo, max_commits=5)
    first_commit = [d for d in deltas if "unguarded" in d.subject]
    rules = miner.extract_rules_heuristic(first_commit)
    invariants = [r["invariant"] for r in rules]
    assert any("timeout" in inv for inv in invariants)
    assert all(0.0 <= r["confidence"] <= 1.0 for r in rules)


def test_ollama_down_returns_note_not_raise(history_repo):
    deltas = miner.stream_micro_deltas(history_repo, max_commits=2)
    rules, note = miner.extract_rules_via_ollama(
        deltas, endpoint="http://127.0.0.1:1", model="nope", timeout_s=3.0
    )
    assert rules == []
    assert "unavailable" in note


def test_mine_repo_merges_without_ollama(history_repo):
    deltas, rules, note = miner.mine_repo(
        history_repo, max_commits=5, endpoint="http://127.0.0.1:1"
    )
    assert deltas
    assert rules  # heuristic backfill keeps the bank steerable
    assert "heuristic" in note


def test_mine_recent_history_returns_rule_dicts(history_repo):
    rules = miner.mine_recent_history(
        history_repo, limit=5, endpoint="http://127.0.0.1:1"
    )
    assert rules
    for rule in rules:
        assert rule["invariant"].strip()
        assert 0.0 <= rule["confidence"] <= 1.0
