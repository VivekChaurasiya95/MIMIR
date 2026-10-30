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
    "filter_episodes_by_timeframe",
    "format_episode_detail",
    "format_episode_row",
    "format_memory_summary",
    "format_principle_detail",
    "format_replication_report",
    "greeting_for_hour",
    "home_suggestion",
    "rel_time_ago",
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
