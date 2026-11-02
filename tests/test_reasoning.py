"""Reasoning tests: diff rendering + honest offline fallback."""

from __future__ import annotations

from mimir import reasoning


def test_render_unified_diff_marks_add_remove():
    diff = reasoning.render_unified_diff(
        "a = 1\nb = 2\n", "a = 1\nb = 3\n", "mod.py"
    )
    assert "-b = 2" in diff
    assert "+b = 3" in diff
    assert "a/mod.py" in diff


def test_template_proposal_shields_bare_requests_call():
    content = "import requests\nresp = requests.post(URL, json=payload)\n"
    proposal = reasoning.template_proposal("add timeout", "svc.py", content)
    assert proposal.source == "template"
    assert "timeout=5.0" in proposal.new_content
    assert "except requests.exceptions.Timeout" in proposal.new_content
    assert proposal.unified_diff
    assert proposal.confidence > 0.5
    assert "timeout" in proposal.after_note.lower()


def test_template_proposal_no_match_stays_empty():
    proposal = reasoning.template_proposal(
        "do something exotic", "plain.py", "x = 1\n"
    )
    assert proposal.unified_diff == ""
    assert proposal.confidence == 0.0
    assert "Ollama" in proposal.after_note


def test_generate_proposal_falls_back_with_note():
    content = "import requests\nresp = requests.post(URL, json=payload)\n"
    proposal = reasoning.generate_proposal(
        "add timeout",
        "svc.py",
        content,
        endpoint="http://127.0.0.1:1",
        model="nope",
        timeout_s=3.0,
    )
    assert proposal.source == "template"
    assert "Ollama unavailable" in proposal.note
    assert "timeout=5.0" in proposal.new_content
