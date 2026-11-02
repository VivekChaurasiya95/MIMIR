"""Inductive Miner: patch-level history scraping (MIMIR 3.0 Pillar 1).

Reads ``git log -p`` micro-deltas (actual before/after code states, not
commit-message strings) and distills structural style invariants from
them. Rule extraction prefers the local Ollama daemon and falls back to
a deterministic heuristic pass so mining works fully offline.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "MicroDelta",
    "extract_rules_heuristic",
    "extract_rules_via_ollama",
    "mine_recent_history",
    "mine_repo",
    "stream_micro_deltas",
]

_DIFF_FILE_RE = re.compile(r"^diff --git a/(.*) b/(.*)$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass(frozen=True)
class MicroDelta:
    """One hunk of one file changed by one commit."""

    commit_sha: str
    subject: str
    file_path: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    removed_lines: tuple[str, ...] = field(default_factory=tuple)
    added_lines: tuple[str, ...] = field(default_factory=tuple)


def _run_git(args: list[str], *, cwd: str, timeout_s: float) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        hint = ""
        if "dubious ownership" in stderr:
            hint = (
                " (fix: git config --global --add safe.directory "
                f"{cwd})"
            )
        raise RuntimeError(
            f"git {' '.join(args)} failed: {stderr[:300]}{hint}"
        )
    return completed.stdout


def _parse_show(sha: str, subject: str, text: str) -> list[MicroDelta]:
    deltas: list[MicroDelta] = []
    current_file = ""
    hunk_meta: Optional[tuple[int, int, int, int]] = None
    removed: list[str] = []
    added: list[str] = []

    def flush() -> None:
        if current_file and hunk_meta is not None:
            old_start, old_count, new_start, new_count = hunk_meta
            deltas.append(
                MicroDelta(
                    commit_sha=sha,
                    subject=subject,
                    file_path=current_file,
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    removed_lines=tuple(removed),
                    added_lines=tuple(added),
                )
            )
        removed.clear()
        added.clear()

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        diff_match = _DIFF_FILE_RE.match(line)
        if diff_match is not None:
            flush()
            hunk_meta = None
            current_file = diff_match.group(2)
            continue
        hunk_match = _HUNK_RE.match(line)
        if hunk_match is not None:
            flush()
            old_start = int(hunk_match.group(1))
            old_count = int(hunk_match.group(2) or "1")
            new_start = int(hunk_match.group(3))
            new_count = int(hunk_match.group(4) or "1")
            hunk_meta = (old_start, old_count, new_start, new_count)
            continue
        if hunk_meta is None or not current_file:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:])
    flush()
    return deltas


def stream_micro_deltas(
    repo: str, *, max_commits: int = 15, timeout_s: float = 30.0
) -> list[MicroDelta]:
    """Return patch-level hunks for the newest ``max_commits`` commits.

    Raises :class:`RuntimeError` when ``repo`` is not a git repository or
    git fails. Returns an empty list for repositories without commits.
    """
    if not isinstance(repo, str) or not repo.strip():
        raise ValueError("repo: expected a non-empty path string")
    if not isinstance(max_commits, int) or max_commits < 1:
        raise ValueError("max_commits: expected a positive int")
    listing = _run_git(
        ["log", "--pretty=format:%H%x1f%s%x1e", "-n", str(max_commits)],
        cwd=repo,
        timeout_s=timeout_s,
    )
    entries = [e for e in listing.split("\x1e") if e.strip()]
    deltas: list[MicroDelta] = []
    for entry in entries:
        sha, _, subject = entry.partition("\x1f")
        sha = sha.strip()
        if not sha:
            continue
        patch = _run_git(
            ["show", "--no-color", "-U3", "--pretty=format:", sha],
            cwd=repo,
            timeout_s=timeout_s,
        )
        deltas.extend(_parse_show(sha, subject.strip(), patch))
    return deltas


_MINER_SYSTEM_PROMPT = (
    "You extract structural coding invariants from git patch hunks. "
    "Reply with STRICT JSON only: "
    '{"rules": [{"invariant": str, "anti_pattern": str, '
    '"context_trigger": str, "category": str, "confidence": float}]}. '
    "An invariant is a reusable team habit (e.g. 'Wrap third-party HTTP "
    "calls in ServiceError with structured context'). The anti-pattern is "
    "the concrete bad shape it forbids. context_trigger names the file or "
    "module kind it applies to. category is one of: Error Handling, "
    "Typing, Testing, Style, Safety. confidence is 0.0-1.0. Extract at "
    "most 8 rules; extract 0 when the hunks show no reusable habit."
)


def _deltas_to_prompt(deltas: list[MicroDelta], *, limit: int = 40) -> str:
    chunks: list[str] = []
    for delta in deltas[:limit]:
        removed = "\n".join(f"-{line}" for line in delta.removed_lines[:20])
        added = "\n".join(f"+{line}" for line in delta.added_lines[:20])
        chunks.append(
            f"commit {delta.commit_sha[:8]} {delta.subject}\n"
            f"file {delta.file_path}\n{removed}\n{added}"
        )
    return "\n---\n".join(chunks)[:12000]


def extract_rules_via_ollama(
    deltas: list[MicroDelta],
    *,
    endpoint: str = "http://127.0.0.1:11434",
    model: str = "qwen2.5-coder",
    timeout_s: float = 60.0,
) -> tuple[list[dict[str, Any]], str]:
    """Ask local Ollama to distill invariants; never raises for LLM reasons.

    Returns ``(rules, note)`` where ``note`` explains an empty result
    (Ollama down, bad JSON, no hunks) for honest UI display.
    """
    if not deltas:
        return ([], "no micro-deltas to analyze")
    payload = {
        "model": model,
        "prompt": f"{_MINER_SYSTEM_PROMPT}\n\n{_deltas_to_prompt(deltas)}",
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 1024},
    }
    try:
        request = urllib.request.Request(
            endpoint.rstrip("/") + "/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _LOOPBACK_OPENER.open(request, timeout=timeout_s) as response:
            body = json.loads(response.read().decode("utf-8", errors="replace"))
        raw_rules = json.loads(body.get("response", "{}")).get("rules", [])
    except Exception as exc:
        return ([], f"Ollama unavailable ({exc}); heuristic rules used")
    rules: list[dict[str, Any]] = []
    for item in raw_rules:
        if not isinstance(item, dict):
            continue
        invariant = str(item.get("invariant", "")).strip()
        if not invariant:
            continue
        try:
            confidence = float(item.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        rules.append(
            {
                "invariant": invariant[:500],
                "anti_pattern": str(item.get("anti_pattern", ""))[:500],
                "context_trigger": str(
                    item.get("context_trigger", "general")
                )[:200],
                "category": str(item.get("category", "Style"))[:60],
                "confidence": min(1.0, max(0.0, confidence)),
            }
        )
    if not rules:
        return ([], "Ollama returned no reusable invariants")
    return (rules, f"{len(rules)} rule(s) distilled by {model}")


def _heuristic_scan(
    deltas: list[MicroDelta],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rules: list[dict[str, Any]] = []

    def add(
        invariant: str,
        anti_pattern: str,
        context_trigger: str,
        category: str,
        confidence: float,
    ) -> None:
        if invariant in seen:
            return
        seen.add(invariant)
        rules.append(
            {
                "invariant": invariant,
                "anti_pattern": anti_pattern,
                "context_trigger": context_trigger,
                "category": category,
                "confidence": confidence,
            }
        )

    for delta in deltas:
        text = "\n".join(delta.added_lines)
        if re.search(r"^\s*except\s*:", text, re.MULTILINE):
            add(
                "Never use bare except clauses; catch specific exceptions.",
                "Bare except: swallowing every failure silently.",
                "Python modules with error handling.",
                "Error Handling",
                0.75,
            )
        if "requests." in text and "timeout" not in text:
            add(
                "Always pass an explicit timeout to third-party HTTP calls.",
                "Raw requests calls with no timeout (hang forever).",
                "HTTP client modules, external services.",
                "Error Handling",
                0.8,
            )
        if re.search(r"^\s*print\(", text, re.MULTILINE):
            add(
                "Use structured logging instead of print in shipped code.",
                "print() debugging statements left in modules.",
                "Application modules.",
                "Style",
                0.6,
            )
        for line in delta.added_lines:
            match = re.match(r"\s*def\s+\w+\(.*\)\s*:", line)
            if match and "->" not in line:
                add(
                    "All public functions require return type annotations.",
                    "Untyped def signatures with no return type.",
                    "Python public APIs.",
                    "Typing",
                    0.65,
                )
                break
        if "self.assert" in text and "test" in delta.file_path:
            add(
                "Use plain pytest assertions, not unittest TestCase asserts.",
                "self.assert* inside pytest test modules.",
                "Pytest test modules.",
                "Testing",
                0.7,
            )
    return rules


def extract_rules_heuristic(
    deltas: list[MicroDelta],
) -> list[dict[str, Any]]:
    """Deterministic offline fallback: pattern-scan added lines."""
    return _heuristic_scan(list(deltas))


def mine_repo(
    repo: str,
    *,
    max_commits: int = 15,
    endpoint: str = "http://127.0.0.1:11434",
    model: str = "qwen2.5-coder",
) -> tuple[list[MicroDelta], list[dict[str, Any]], str]:
    """Stream micro-deltas and distill rules (Ollama first, heuristic fill).

    Returns ``(deltas, rules, note)``. Heuristic rules backfill whatever
    Ollama misses so the Principle Bank always has steerable content.
    """
    deltas = stream_micro_deltas(repo, max_commits=max_commits)
    llm_rules, note = extract_rules_via_ollama(
        deltas, endpoint=endpoint, model=model
    )
    known = {rule["invariant"] for rule in llm_rules}
    merged = list(llm_rules)
    for rule in extract_rules_heuristic(deltas):
        if rule["invariant"] not in known:
            known.add(rule["invariant"])
            merged.append(rule)
    if not merged:
        return (deltas, [], "no reusable habits found in recent commits")
    if not llm_rules:
        return (deltas, merged, f"{len(merged)} heuristic rule(s) ({note})")
    return (deltas, merged, note)


def mine_recent_history(
    repo: str,
    *,
    limit: int = 8,
    endpoint: str = "http://127.0.0.1:11434",
    model: str = "qwen2.5-coder",
) -> list[dict[str, Any]]:
    """Distill rule dicts from the newest ``limit`` commits.

    Thin convenience over :func:`mine_repo` for dashboard callers that
    only need the merged rules (Ollama first, heuristic backfill).
    """
    _, rules, _ = mine_repo(
        repo, max_commits=limit, endpoint=endpoint, model=model
    )
    return rules
