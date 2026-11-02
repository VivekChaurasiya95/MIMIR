"""Real-disk Principle Bank tests: file SQLite, no `:memory:` mocks."""

from __future__ import annotations

import sqlite3

from mimir.memory import PrincipleBank


def _bank(tmp_path) -> PrincipleBank:
    return PrincipleBank(str(tmp_path / "bank.db"))


def test_add_list_and_vector_blobs_on_disk(tmp_path):
    bank = _bank(tmp_path)
    try:
        first = bank.add_rule(
            "Always pass timeout to HTTP calls.",
            "bare requests call",
            "HTTP clients",
            "Error Handling",
            0.85,
        )
        bank.add_rule(
            "All public functions require return types.",
            "untyped def",
            "Python APIs",
            "Typing",
            0.65,
        )
        rules = bank.list_rules()
        assert len(rules) == 2
        assert all(enabled for _, enabled in rules)
        assert first.latent_invariant.startswith("Always pass timeout")
    finally:
        bank.close()
    conn = sqlite3.connect(str(tmp_path / "bank.db"))
    try:
        blobs = conn.execute("SELECT embedding FROM principles").fetchall()
    finally:
        conn.close()
    assert len(blobs) == 2
    assert all(len(row[0]) == 4 * 256 for row in blobs)  # float32 x dim


def test_toggle_excludes_rule_from_search(tmp_path):
    bank = _bank(tmp_path)
    try:
        rule = bank.add_rule(
            "Always pass timeout to HTTP calls.",
            "",
            "HTTP clients",
            "Error Handling",
            0.9,
        )
        assert bank.search("http timeout requests", top_k=3)
        bank.set_enabled(rule.id, False)
        assert bank.search("http timeout requests", top_k=3) == []
        assert len(bank.search("http timeout", enabled_only=False)) == 1
        states = dict((p.id, e) for p, e in bank.list_rules())
        assert states[rule.id] is False
    finally:
        bank.close()


def test_audit_ledger_round_trip(tmp_path):
    bank = _bank(tmp_path)
    try:
        row_id = bank.log_action(
            "user", "apply_accepted", "auth/token.py", "ok", "passed"
        )
        bank.log_action("cobot", "apply_rolled_back", "x.py", "fail", "rolled_back")
        entries = bank.list_actions()
        assert len(entries) == 2
        assert entries[0].id == str(row_id + 1)  # newest first
        assert entries[1].status == "passed"
        assert entries[0].target_file == "x.py"
        assert entries[0].actor == "cobot"
        assert entries[0].action_type == "apply_rolled_back"
        assert entries[0].timestamp
        assert entries[1].details == "ok"
    finally:
        bank.close()


def test_upsert_principles_dedupes_by_invariant(tmp_path):
    bank = _bank(tmp_path)
    try:
        mined = [
            {
                "invariant": "Always pass timeout to HTTP calls.",
                "anti_pattern": "bare requests call",
                "context_trigger": "HTTP clients",
                "category": "Error Handling",
                "confidence": 0.85,
            },
            {
                "invariant": "Always pass timeout to HTTP calls.",  # dup
                "anti_pattern": "other",
                "context_trigger": "HTTP clients",
                "category": "Error Handling",
                "confidence": 0.5,
            },
            {"invariant": "  ", "category": "Style"},  # empty: skipped
        ]
        assert bank.upsert_principles(mined) == 1
        assert bank.upsert_principles(mined) == 0  # second pass: all known
        rules = bank.list_rules()
        assert len(rules) == 1
        assert rules[0][0].latent_invariant.startswith("Always pass timeout")
    finally:
        bank.close()


def test_bank_persists_across_reopen(tmp_path):
    db = str(tmp_path / "bank.db")
    bank = PrincipleBank(db)
    rule = bank.add_rule("Never use bare except.", "", "general", "Style", 0.7)
    bank.set_enabled(rule.id, False)
    bank.close()
    reopened = PrincipleBank(db)
    try:
        rules = reopened.list_rules()
        assert len(rules) == 1
        assert rules[0][1] is False
    finally:
        reopened.close()
