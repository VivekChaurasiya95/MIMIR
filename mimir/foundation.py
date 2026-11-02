"""Llama-3.2 foundation layer: task understanding for the digital cobot.

MIMIR treats a local Llama-3.2 model (via the on-device Ollama daemon
at loopback ``http://127.0.0.1:11434``) as its base for understanding
what the user asks and does: classifying tasks, answering questions in
the user's own terms, and summarizing live activity while Habi wanders.
100% on-device, zero-cloud — every network call fails closed to ``None``
so callers degrade to deterministic offline behavior.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Mapping, Optional, Sequence

from .llama_intent import (
    DEFAULT_LLAMA_MODEL,
    DEFAULT_LOCAL_ENDPOINT,
    select_best_local_model,
)

__all__ = [
    "TASK_KINDS",
    "build_activity_context",
    "build_foundation_prompt",
    "classify_task_kind",
    "describe_activity",
    "foreground_window_info",
    "query_local_llama_text",
]

#: Task kinds understood by the foundation layer.
TASK_KINDS = ("question", "build", "refactor", "plan", "other")

_QUESTION_LEADS = (
    "who", "what", "when", "where", "why", "how", "which",
    "have you", "has ", "did you", "do you", "can you", "could you",
    "are you", "is there", "have i", "should i", "explain",
)

_BUILD_HINTS = (
    "implement", "create", "add", "build", "write", "generate",
    "draft", "make ", "setup", "set up", "scaffold",
)

_REFACTOR_HINTS = (
    "refactor", "rename", "restructure", "clean up", "cleanup",
    "simplify", "migrate", "reorganize", "re-organise",
)

_PLAN_HINTS = (
    "plan", "design", "architect", "roadmap", "brainstorm",
    "proposal", "outline",
)

_LOOPBACK_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}))


def classify_task_kind(task: str) -> str:
    """Classify a free-form request into one of :data:`TASK_KINDS`.

    Pure keyword logic: questions first (they must be *answered*, not
    coded), then build / refactor / plan markers, else ``"other"``.
    """
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task: expected a non-empty string")
    text = task.strip()
    low = re.sub(r"\s+", " ", text.lower())
    if text.endswith("?") or low.startswith(_QUESTION_LEADS):
        return "question"
    if any(hint in low for hint in _REFACTOR_HINTS):
        return "refactor"
    if any(hint in low for hint in _PLAN_HINTS):
        return "plan"
    if any(hint in low for hint in _BUILD_HINTS):
        return "build"
    return "other"


def build_foundation_prompt(
    task: str,
    principles: Sequence[Mapping[str, str] | Any] | None = None,
    task_kind: Optional[str] = None,
    memory_summary: str = "",
) -> str:
    """Render a Llama-3.2 prompt that undertakes ``task`` directly.

    ``principles`` accepts principle dicts (``id``/``invariant``/
    ``scope`` keys) or :class:`mimir.principles.BehavioralPrinciple`
    instances. Questions get an answer-first instruction; builds keep
    the solution-first framing.
    """
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task: expected a non-empty string")
    kind = (task_kind or classify_task_kind(task)).strip().lower()
    if kind not in TASK_KINDS:
        kind = "other"
    task = task.strip()
    lines = [
        "You are Habi, the MIMIR digital cobot: a 100% on-device "
        "developer companion. Undertake the user's request below "
        "directly and completely.",
        "",
        "User request:",
        task,
        "",
    ]
    items: list[str] = []
    for principle in list(principles or [])[:8]:
        try:
            if isinstance(principle, Mapping):
                pid = str(principle.get("id", "")).strip()
                invariant = str(
                    principle.get("invariant")
                    or principle.get("latent_invariant", "")).strip()
                scope = str(
                    principle.get("scope", "") or "").strip()
            else:
                pid = str(getattr(principle, "id", "")).strip()
                invariant = str(
                    getattr(principle, "latent_invariant", "")).strip()
                trigger = getattr(principle, "context_trigger", None)
                scope = str(getattr(trigger, "scope", "")
                            or "").strip()
        except Exception:
            continue
        if not invariant:
            continue
        label = f"[{pid}] " if pid else ""
        where = f" (applies to: {scope})" if scope else ""
        items.append(f"- {label}{invariant}{where}")
    if items:
        lines.append("The user's learned conventions (honor these):")
        lines.extend(items)
        lines.append("")
    elif memory_summary.strip():
        lines.append("Learned conventions:")
        lines.append(memory_summary.strip())
        lines.append("")
    if kind == "question":
        lines.append(
            "Answer the question directly and completely in the user's "
            "own terms. Ground the answer in the conventions above "
            "when they apply; never reply with a generic template.")
    else:
        lines.append(
            "Respond with the solution only, embodying the conventions "
            "above. Do not explain the conventions; embody them.")
    return "\n".join(lines)


def query_local_llama_text(
    prompt: str,
    *,
    endpoint: str = DEFAULT_LOCAL_ENDPOINT,
    model: Optional[str] = None,
    timeout_s: float = 8.0,
    num_predict: int = 512,
    temperature: float = 0.3,
) -> Optional[str]:
    """Complete ``prompt`` with the local Llama model (fail-closed).

    Returns the stripped completion, or ``None`` when the daemon is
    down, the model is missing, the call times out, or the reply is
    unusable. Only ``ValueError`` (bad caller input) escapes.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt: expected a non-empty string")
    if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise ValueError("timeout_s: expected a positive number")
    try:
        base = (endpoint or DEFAULT_LOCAL_ENDPOINT).rstrip("/")
        chosen = model or select_best_local_model(base, DEFAULT_LLAMA_MODEL)
        payload = {
            "model": chosen,
            "prompt": prompt.strip(),
            "stream": False,
            "options": {
                "temperature": float(temperature),
                "num_predict": int(num_predict),
            },
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            base + "/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _LOOPBACK_OPENER.open(request,
                                   timeout=float(timeout_s)) as response:
            raw = response.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        text = (data.get("response") or "").strip()
        return text or None
    except ValueError:
        raise
    except Exception:
        return None


def build_activity_context(snapshot: Mapping[str, Any]) -> str:
    """Render a live-activity snapshot as Llama-ready context (pure).

    ``snapshot`` keys: ``foreground`` (active window/file), ``repo``,
    ``recent_files``, ``recent_episodes`` (short summaries), ``watching``.
    """
    if not isinstance(snapshot, Mapping):
        raise ValueError("snapshot: expected a mapping")
    foreground = str(snapshot.get("foreground", "") or "").strip()
    repo = str(snapshot.get("repo", "") or "").strip()
    files = [str(f).strip() for f in
             list(snapshot.get("recent_files", []) or [])[:6] if str(f).strip()]
    episodes = [str(e).strip() for e in
                list(snapshot.get("recent_episodes", []) or [])[:4]
                if str(e).strip()]
    watching = bool(snapshot.get("watching", False))
    lines = ["Live developer activity:"]
    lines.append(f"- Focused window: {foreground or '(unknown)'}")
    if repo:
        lines.append(f"- Repository: {repo}")
    lines.append(f"- Observation: {'watching' if watching else 'idle'}")
    if files:
        lines.append("- Recently touched files: " + ", ".join(files))
    if episodes:
        lines.append("- Recent work:")
        lines.extend(f"  * {item}" for item in episodes)
    return "\n".join(lines)


def describe_activity(
    snapshot: Mapping[str, Any],
    *,
    endpoint: str = DEFAULT_LOCAL_ENDPOINT,
    model: Optional[str] = None,
    timeout_s: float = 3.0,
) -> Optional[str]:
    """Summarize what the user is doing in one short line (fail-closed).

    Used while Habi wanders so roaming is observant, not passive.
    Returns ``None`` when Llama is unreachable; the caller then stays
    silent instead of guessing.
    """
    try:
        context = build_activity_context(snapshot)
    except Exception:
        return None
    prompt = (
        "You watch a developer's screen while they work. "
        "Summarize what they are most likely doing right now in ONE "
        "short line (under 90 characters, no quotes, no preamble). "
        "Name the task, not the tools.\n\n"
        f"{context}\n\nOne-line activity:"
    )
    try:
        note = query_local_llama_text(
            prompt, endpoint=endpoint, model=model,
            timeout_s=timeout_s, num_predict=48, temperature=0.2)
    except ValueError:
        return None
    if not note:
        return None
    first = re.split(r"[\r\n]", note.strip(), maxsplit=1)[0].strip()
    first = first.strip("\"' ").strip()
    if not first or len(first) > 160:
        return None
    return first


def foreground_window_info() -> Optional[dict[str, str]]:
    """Best-effort focused-window snapshot (never raises).

    Returns ``{"title": ...}`` on hosts with ``pywin32`` installed,
    else ``None`` (headless / missing backend).
    """
    try:
        import win32gui  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        handle = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(handle) or ""
    except Exception:
        return None
    title = str(title).strip()
    if not title:
        return None
    return {"title": title[:200]}
