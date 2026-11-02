"""Local reasoning: diff synthesis over Ollama (MIMIR 3.0 Pillar 2).

Turns a user task prompt + the enabled Principle Bank rules + the target
file's current content into a reviewable unified diff. Prefers the
loopback Ollama daemon (``qwen2.5-coder`` / ``llama3.2``) and degrades to
a deterministic template patcher so the Control Tower stays demonstrable
with Ollama offline. The proposal always carries its ``source`` so the
UI can badge it honestly.
"""

from __future__ import annotations

import difflib
import json
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

__all__ = [
    "Proposal",
    "generate_proposal",
    "render_unified_diff",
    "template_proposal",
]

_DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
_DEFAULT_MODEL = "qwen2.5-coder"

_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_SYNTH_SYSTEM_PROMPT = (
    "You are MIMIR, a careful local pair programmer. Rewrite the TARGET "
    "FILE to satisfy the TASK while honoring every ACTIVE RULE. Reply "
    "with STRICT JSON only: {\"new_content\": str (the COMPLETE rewritten "
    "file, never a snippet), \"summary\": str (one sentence), "
    "\"before_note\": str (current behavior, plain English), "
    "\"after_note\": str (improved behavior, plain English), "
    "\"confidence\": float 0.0-1.0}. Keep unrelated code byte-identical. "
    "Never add network listeners, file deletion, process kills, shell "
    "execution, or eval/exec."
)


@dataclass(frozen=True)
class Proposal:
    """One synthesized, unapplied change held safely in memory."""

    source: str  # "ollama:<model>" or "template"
    target_file: str
    unified_diff: str
    new_content: str
    summary: str
    before_note: str
    after_note: str
    confidence: float
    matched_rules: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""


def render_unified_diff(old: str, new: str, filename: str) -> str:
    """Color-ready unified diff between current and proposed content."""
    lines = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    )
    return "".join(lines)


def _rule_context(rules: Sequence[Any]) -> str:
    lines: list[str] = []
    for rule in rules:
        invariant = getattr(rule, "latent_invariant", None)
        if invariant is None and isinstance(rule, dict):
            invariant = rule.get("latent_invariant") or rule.get("invariant")
        scope = ""
        trigger = getattr(rule, "context_trigger", None)
        if trigger is not None:
            scope = getattr(trigger, "scope", "") or ""
        elif isinstance(rule, dict):
            scope = str(rule.get("context_trigger", "") or "")
        if invariant:
            lines.append(f"- [{scope or 'general'}] {invariant}")
    return "\n".join(lines) or "- (no active rules; follow the task only)"


def _rule_ids(rules: Sequence[Any]) -> tuple[str, ...]:
    ids: list[str] = []
    for rule in rules:
        value = getattr(rule, "id", None)
        if value is None and isinstance(rule, dict):
            value = rule.get("id")
        if value:
            ids.append(str(value))
    return tuple(ids)


def _wrap_requests_timeout(content: str) -> Optional[str]:
    """Add timeout + friendly fallback around a bare requests call."""
    if "requests." not in content or "timeout=" in content:
        return None
    pattern = re.compile(
        r"(?P<indent>^[ \t]*)resp\s*=\s*requests\.(?P<method>post|get|put|patch|delete)\("
        r"(?P<args>[^)]*)\)",
        re.MULTILINE,
    )
    match = pattern.search(content)
    if match is None:
        return None
    indent = match.group("indent")
    args = match.group("args").rstrip()
    sep = ", " if args and not args.endswith(",") else ""
    replacement = (
        f"{indent}try:\n"
        f"{indent}    resp = requests.{match.group('method')}("
        f"{args}{sep}timeout=5.0)\n"
        f"{indent}except requests.exceptions.Timeout:\n"
        f"{indent}    logger.warning(\"Gateway lag observed. "
        "Invoking safe fallback...\")\n"
        f"{indent}    return trigger_graceful_checkout_recovery(cart_id)"
    )
    return content[: match.start()] + replacement + content[match.end() :]


def template_proposal(
    task_prompt: str,
    target_filename: str,
    target_content: str,
    rules: Sequence[Any] = (),
) -> Proposal:
    """Deterministic offline patcher for the headline safeguard patterns."""
    patched = _wrap_requests_timeout(target_content)
    if patched is not None and patched != target_content:
        return Proposal(
            source="template",
            target_file=target_filename,
            unified_diff=render_unified_diff(
                target_content, patched, target_filename
            ),
            new_content=patched,
            summary="Wrap gateway call in a 5-second timeout shield.",
            before_note=(
                "Raw direct call to the bank server with no timeout. If the "
                "bank is slow, the customer spinner spins forever."
            ),
            after_note=(
                "5-second timeout shield with warm user messaging and an "
                "automatic graceful recovery before any frozen screen."
            ),
            confidence=0.82,
            matched_rules=_rule_ids(rules),
            note="Ollama offline; deterministic template applied",
        )
    return Proposal(
        source="template",
        target_file=target_filename,
        unified_diff="",
        new_content=target_content,
        summary="No confident offline edit for this task.",
        before_note="File unchanged.",
        after_note=(
            "Start Ollama ('ollama serve' + 'ollama pull qwen2.5-coder') to "
            "synthesize a change for this task."
        ),
        confidence=0.0,
        matched_rules=_rule_ids(rules),
        note="Ollama offline and no template matched",
    )


def generate_proposal(
    task_prompt: str,
    target_filename: str,
    target_content: str,
    rules: Sequence[Any] = (),
    *,
    endpoint: str = _DEFAULT_ENDPOINT,
    model: str = _DEFAULT_MODEL,
    timeout_s: float = 90.0,
) -> Proposal:
    """Synthesize a proposal via Ollama, else the template patcher.

    Never raises for LLM reasons: any daemon outage, timeout, or
    malformed reply degrades to :func:`template_proposal` with an honest
    ``note`` naming the cause.
    """
    if not isinstance(task_prompt, str) or not task_prompt.strip():
        raise ValueError("task_prompt: expected a non-empty string")
    if not isinstance(target_content, str):
        raise ValueError("target_content: expected a string")
    user_block = (
        f"TASK: {task_prompt.strip()}\n\n"
        f"ACTIVE RULES:\n{_rule_context(rules)}\n\n"
        f"TARGET FILE ({target_filename}):\n{target_content[:12000]}"
    )
    payload = {
        "model": model,
        "prompt": f"{_SYNTH_SYSTEM_PROMPT}\n\n{user_block}",
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 2048},
    }
    try:
        request = urllib.request.Request(
            endpoint.rstrip("/") + "/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _LOOPBACK_OPENER.open(request, timeout=timeout_s) as response:
            body = json.loads(
                response.read().decode("utf-8", errors="replace")
            )
        reply = json.loads(body.get("response", "{}"))
        new_content = reply.get("new_content", "")
        if not isinstance(new_content, str) or not new_content.strip():
            raise ValueError("empty new_content in model reply")
        try:
            confidence = float(reply.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        return Proposal(
            source=f"ollama:{model}",
            target_file=target_filename,
            unified_diff=render_unified_diff(
                target_content, new_content, target_filename
            ),
            new_content=new_content,
            summary=str(reply.get("summary", "Proposed change."))[:500],
            before_note=str(reply.get("before_note", ""))[:1000],
            after_note=str(reply.get("after_note", ""))[:1000],
            confidence=min(1.0, max(0.0, confidence)),
            matched_rules=_rule_ids(rules),
        )
    except Exception as exc:
        fallback = template_proposal(
            task_prompt, target_filename, target_content, rules
        )
        return Proposal(
            source=fallback.source,
            target_file=fallback.target_file,
            unified_diff=fallback.unified_diff,
            new_content=fallback.new_content,
            summary=fallback.summary,
            before_note=fallback.before_note,
            after_note=fallback.after_note,
            confidence=fallback.confidence,
            matched_rules=fallback.matched_rules,
            note=f"Ollama unavailable ({exc}); {fallback.note}",
        )
