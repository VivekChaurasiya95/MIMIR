"""Qt-free text formatting for desktop tabs (headless-testable)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mimir.episodes import BehavioralEpisode
from mimir.principles import BehavioralPrinciple

if TYPE_CHECKING:  # annotation-only names; never imported at runtime
    from datetime import datetime

__all__ = [
    "TIMEFRAMES",
    "confidence_badge",
    "extract_code_block",
    "filter_episodes_by_timeframe",
    "format_action_plan",
    "format_episode_detail",
    "format_episode_row",
    "format_memory_summary",
    "format_principle_detail",
    "format_proposal_summary",
    "format_provenance",
    "format_replication_report",
    "format_safety_checklist",
    "greeting_for_hour",
    "home_suggestion",
    "infer_file_target",
    "rel_time_ago",
    "review_queue",
    "risk_badge",
]

#: Timeframe selector options (spec 5.2) mapping to day windows.
TIMEFRAMES: dict[str, int | None] = {
    "Today": 1,
    "Last 7 days": 7,
    "Last 30 days": 30,
    "All time": None,
}


def confidence_badge(confidence: float) -> tuple[str, str]:
    """Map a confidence score to (badge label, badge QSS kind)."""
    if confidence >= 0.80:
        return ("Confirmed", "confirmed")
    if confidence >= 0.50:
        return ("Learning", "learning")
    return ("Unconfirmed", "unconfirmed")


def greeting_for_hour(hour: int) -> str:
    """Time-aware overline: GOOD MORNING / AFTERNOON / EVENING."""
    if hour < 12:
        return "GOOD MORNING,"
    if hour < 18:
        return "GOOD AFTERNOON,"
    return "GOOD EVENING,"


def _parse_moment(value: str) -> "datetime | None":
    from datetime import datetime, timezone

    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def filter_episodes_by_timeframe(
    episodes: "list[BehavioralEpisode]",
    timeframe: str,
    now: "datetime | None" = None,
) -> "list[BehavioralEpisode]":
    """Keep episodes observed inside the named window (spec 5.2)."""
    from datetime import datetime, timezone

    window = TIMEFRAMES.get(timeframe)
    if window is None:
        return list(episodes)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now.timestamp() - window * 86400
    kept = []
    for episode in episodes:
        moment = _parse_moment(episode.observed_at)
        if moment is not None and moment.timestamp() >= cutoff:
            kept.append(episode)
    return kept


def rel_time_ago(iso: str, now: "datetime | None" = None) -> str:
    """Human relative stamp ('Today', '2 days ago') for task feeds."""
    from datetime import datetime, timezone

    moment = _parse_moment(iso)
    if moment is None:
        return "recently"
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    days = (now - moment).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days} days ago"
    if days < 30:
        weeks = days // 7
        return f"{weeks} week{'s' if weeks > 1 else ''} ago"
    months = days // 30
    return f"{months} month{'s' if months > 1 else ''} ago"


def format_episode_row(episode: BehavioralEpisode) -> str:
    files = len(episode.transitions)
    scope = episode.context.scope or "repo root"
    lang = episode.context.language or "unknown lang"
    summary = episode.summary if len(episode.summary) <= 90 else episode.summary[:87] + "..."
    return f"{episode.id[:12]} · {files} file(s) · {scope} · {lang} — {summary}"


def format_memory_summary(principles: list[BehavioralPrinciple]) -> str:
    if not principles:
        return "Behavioral Memory is empty."
    lines = [f"Behavioral Memory: {len(principles)} principle(s)"]
    for principle in principles:
        status = principle.epistemic_status
        lines.append(
            f"- {principle.id} [conf {status.confidence_score:.2f}, "
            f"+{status.supporting_episodes_count}/"
            f"-{status.contradictions_count}]: {principle.name}"
        )
    return "\n".join(lines)


def format_principle_detail(principle: BehavioralPrinciple) -> str:
    status = principle.epistemic_status
    anti = "\n".join(f"  • {a}" for a in principle.anti_patterns) or "  (none)"
    traces = ", ".join(principle.inspectable_traces) or "(none)"
    return (
        f"{principle.name} [{principle.id}]\n"
        f"Category: {principle.category}\n"
        f"Scope: {principle.context_trigger.scope} "
        f"(language-agnostic: {principle.context_trigger.language_agnostic})\n\n"
        f"Invariant:\n{principle.latent_invariant}\n\n"
        f"Anti-patterns:\n{anti}\n\n"
        f"Confidence: {status.confidence_score:.2f} "
        f"(+{status.supporting_episodes_count}/-{status.contradictions_count})\n"
        f"First inferred: {status.first_inferred}\n"
        f"Last validated: {status.last_validated}\n"
        f"Traces: {traces}"
    )


def review_queue(
    principles: list[BehavioralPrinciple],
    min_confidence: float = 0.80,
) -> list[BehavioralPrinciple]:
    """Principles needing human calibration (Review & Calibration Deck).

    A principle needs review when its confidence sits below
    ``min_confidence`` or it carries any contradictions. The queue
    runs lowest-confidence first (ties: most contradictions, then id)
    so the shakiest rules surface at the top of the deck.
    """
    queued = [
        p for p in principles
        if (p.epistemic_status.confidence_score < min_confidence
            or p.epistemic_status.contradictions_count > 0)
    ]
    return sorted(
        queued,
        key=lambda p: (p.epistemic_status.confidence_score,
                       -p.epistemic_status.contradictions_count,
                       p.id),
    )


def format_provenance(
    principle: BehavioralPrinciple,
    episodes: list[BehavioralEpisode] | tuple[BehavioralEpisode, ...] = (),
) -> str:
    """Human-readable evidence provenance for one principle.

    Every inspectable trace renders on its own line; traces matching
    a session episode id resolve to that episode's summary and scope,
    while unknown ids (external evidence, older sessions) show raw.
    Never raises for odd inputs — provenance must not break the
    detail view.
    """
    status = principle.epistemic_status
    try:
        known = {e.id: e for e in (episodes or ())}
    except Exception:
        known = {}
    lines = [
        f"Provenance: {len(principle.inspectable_traces)} trace(s) · "
        f"+{status.supporting_episodes_count}/"
        f"-{status.contradictions_count} evidence",
    ]
    for trace in principle.inspectable_traces:
        episode = known.get(trace)
        if episode is None:
            lines.append(f"  • {trace} (evidence outside this session)")
            continue
        scope = episode.context.scope or "repo root"
        summary = (episode.summary or "(no summary)").strip()
        if len(summary) > 90:
            summary = summary[:87] + "..."
        lines.append(f"  • {trace} — {summary} [{scope}]")
    if not principle.inspectable_traces:
        lines.append("  (no traces recorded)")
    lines.append(
        f"First inferred: {status.first_inferred} · "
        f"Last validated: {status.last_validated}"
    )
    return "\n".join(lines)


def format_episode_detail(episode: BehavioralEpisode) -> str:
    files = []
    for transition in episode.transitions:
        tag = "skipped" if transition.skipped else transition.status
        files.append(
            f"  • {transition.path} [{tag}, "
            f"+{transition.additions}/-{transition.deletions}]"
        )
    kinds = ", ".join(episode.redaction_kinds) or "(none)"
    return (
        f"{episode.summary or '(no summary)'}\n"
        f"Commit {episode.source_ref} · by {episode.actor or 'unknown'} · "
        f"{episode.observed_at}\n"
        f"Scope: {episode.context.scope or 'repo root'} · "
        f"Language: {episode.context.language or 'unknown'}\n"
        f"Outcome: build={episode.outcome.build}, tests={episode.outcome.tests}\n"
        f"Redactions: {episode.redactions_applied} ({kinds})\n\n"
        f"Files ({len(files)}):\n" + ("\n".join(files) or "  (none)")
    )


def home_suggestion(
    principles: list[BehavioralPrinciple],
) -> tuple[str, str, str]:
    """Dashboard suggestion driven by real memory state.

    Returns (message, action_label, action_tab_id).
    """
    if not principles:
        return (
            "I haven't learned your style yet. Observe a repository and "
            "I'll distill your first principles from real commits.",
            "Observe a repo",
            "observe",
        )
    mean = sum(p.epistemic_status.confidence_score for p in principles)
    mean /= len(principles)
    return (
        f"I'm holding {len(principles)} principles "
        f"(avg confidence {mean:.2f}). Review them, or keep observing "
        "to sharpen me further.",
        "Review memory",
        "memory",
    )


def format_replication_report(result) -> str:
    names = ", ".join(p.id for p in result.applied_principles) or "(none)"
    report = result.compliance_report
    lines = [
        f"Applied principles ({len(result.applied_principles)}): {names}",
        f"Compliant: {report.get('compliant', 'n/a')} "
        f"· violations: {len(report.get('violations', []))}",
        "",
        "— Replicated solution —",
        result.replicated_solution,
    ]
    if result.contrast_baseline:
        lines += ["", "— Contrast baseline (unconditioned) —", result.contrast_baseline]
    return "\n".join(lines)


def _risk_value(risk) -> str:
    """Normalize a risk tier (RiskLevel, str, or dict) to low/med/high."""
    value = getattr(risk, "value", risk)
    if isinstance(value, dict):
        value = value.get("level", "low")
    text = str(value or "low").strip().lower()
    return text if text in ("low", "medium", "high") else "low"


def risk_badge(risk) -> tuple[str, str]:
    """Map a risk tier to (badge label, badge QSS kind)."""
    level = _risk_value(risk)
    if level == "high":
        return ("High risk", "unconfirmed")
    if level == "medium":
        return ("Medium risk", "learning")
    return ("Low risk", "confirmed")


def _plan_field(plan, name, default=""):
    if isinstance(plan, dict):
        return plan.get(name, default)
    return getattr(plan, name, default)


def format_safety_checklist(checklist) -> str:
    """Bullet rendering of a plan's safety checklist (never raises)."""
    try:
        items = [str(i) for i in (checklist or ())]
    except Exception:
        items = []
    if not items:
        return "Safety checklist: (none recorded)"
    lines = ["Safety checklist:"]
    lines.extend(f"  [ ] {item}" for item in items)
    return "\n".join(lines)


def format_action_plan(plan) -> str:
    """Multi-line rendering of an ActionPlan for cards and logs."""
    if plan is None:
        return "(no plan)"
    try:
        return _format_action_plan_inner(plan)
    except Exception as exc:
        return f"(unrenderable plan: {exc})"


def _format_action_plan_inner(plan) -> str:
    task = _plan_field(plan, "task", "")
    operation = _plan_field(plan, "operation", "")
    target = _plan_field(plan, "target", "")
    foe = _plan_field(plan, "file_or_element", "")
    reason = _plan_field(plan, "reason", "")
    confidence = _plan_field(plan, "confidence", 0.0)
    risk = _risk_value(_plan_field(plan, "risk", "low"))
    label, _kind = risk_badge(risk)
    reasons = _plan_field(plan, "risk_reasons", ()) or ()
    steps = _plan_field(plan, "steps", ()) or ()
    commands = _plan_field(plan, "commands", ()) or ()
    diff = _plan_field(plan, "diff_preview", "") or ""
    checklist = _plan_field(plan, "safety_checklist", ()) or ()
    ids = _plan_field(plan, "matched_principle_ids", ()) or ()
    names = _plan_field(plan, "matched_principle_names", ()) or ()
    perms = _plan_field(plan, "required_permissions", ()) or ()
    plan_id = _plan_field(plan, "plan_id", "")
    where = foe or target or "(no target)"
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.0
    lines = [
        f"Plan {plan_id} [{label}] {operation} (conf {conf:.2f})",
        f"Task: {task}",
        f"Target: {where}",
    ]
    if reason:
        lines.append(f"Why: {reason}")
    lines.append(f"Risk: {risk} — {'; '.join(reasons) or 'n/a'}")
    lines.append(f"Requires: {', '.join(perms) or '(no approval rule)'}")
    if ids:
        pairs = [f"{i} ({n})" if n else i
                 for i, n in zip(ids, list(names) + [""] * len(ids))]
        lines.append(f"Matched principles ({len(ids)}): "
                     + ", ".join(pairs))
    else:
        lines.append("Matched principles: (none — generic plan)")
    lines.append(f"Steps ({len(steps)}):")
    for index, step in enumerate(steps, 1):
        if isinstance(step, dict):
            kind = step.get("kind", "")
            title = step.get("title", "")
            detail = step.get("detail", "")
            command = step.get("command", "")
        else:
            kind = getattr(getattr(step, "kind", ""), "value",
                           getattr(step, "kind", ""))
            title = getattr(step, "title", "")
            detail = getattr(step, "detail", "")
            command = getattr(step, "command", "")
        lines.append(f"  {index}. [{kind}] {title}")
        if command:
            lines.append(f"       $ {command}")
        if detail:
            lines.append(f"       {detail}")
    if commands:
        lines.append("Commands:")
        lines.extend(f"  $ {c}" for c in commands)
    else:
        lines.append("Commands: (none)")
    if diff.strip():
        lines.append("Diff preview:")
        lines.append(diff)
    else:
        lines.append("Diff preview: (no file changes planned)")
    lines.append(format_safety_checklist(checklist))
    return "\n".join(lines)


def format_proposal_summary(spec) -> str:
    """One-line registry summary: id, operation, risk, status, target."""
    if spec is None:
        return "(no proposal)"
    try:
        action_id = _plan_field(spec, "action_id", "")
        operation = _plan_field(spec, "operation", "")
        risk = _risk_value(_plan_field(spec, "risk", "low"))
        status = _plan_field(spec, "status", "")
        status_text = getattr(status, "value", status)
        target = _plan_field(spec, "target", "")
        foe = _plan_field(spec, "file_or_element", "")
        reason = _plan_field(spec, "reason", "")
        where = target or foe or (str(reason)[:60] if reason else "")
        return (f"{action_id} · {operation} · {risk} risk · "
                f"{status_text}" + (f" — {where}" if where else ""))
    except Exception as exc:
        return f"(unrenderable proposal: {exc})"


def extract_code_block(text: str) -> str:
    """Extract clean code from LLM output, stripping markdown code fences if present."""
    if not text:
        return ""
    import re
    cleaned = text.strip()
    pattern = r"```(?:[a-zA-Z0-9_\-]+)?\r?\n(.*?)\r?\n```"
    match = re.search(pattern, cleaned, re.DOTALL)
    if match:
        return match.group(1).strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip()
    return cleaned


def infer_file_target(task: str) -> str:
    """Extract candidate file path from user task string."""
    if not task:
        return ""
    import re
    # Pattern 1: e.g. "in tests/test_auth.py", "into src/utils.py", "file mimir/state.py"
    match = re.search(
        r'(?:in|into|to|file|for|on)\s+([a-zA-Z0-9_\-/\\]+\.[a-zA-Z0-9]+)',
        task, re.IGNORECASE,
    )
    if match:
        return match.group(1).replace("\\", "/")
    # Pattern 2: e.g. "tests/test_foo.py" or "src/bar.js"
    match2 = re.search(
        r'\b([a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-/\\]+\.[a-zA-Z0-9]+)\b', task
    )
    if match2:
        return match2.group(1).replace("\\", "/")
    # Pattern 3: e.g. "auth.py" or "index.html"
    match3 = re.search(
        r'\b([a-zA-Z0-9_\-]+\.(?:py|js|ts|jsx|tsx|json|html|css|md|txt|yml|yaml|toml|sh))\b',
        task, re.IGNORECASE,
    )
    if match3:
        return match3.group(1)
    return ""
