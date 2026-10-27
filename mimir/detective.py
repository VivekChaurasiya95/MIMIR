"""Inductive Detective engine: distill episodes into behavioral principles.

MVP Day 3. Groups similar :class:`~mimir.episodes.BehavioralEpisode` objects
with unsupervised heuristic clustering, then distills each cluster into a
:class:`~mimir.principles.BehavioralPrinciple` via either the offline
heuristic generator or a pluggable LLM function.

The LLM hook (:data:`LLMFn`) keeps Day 3 backend-agnostic: any callable that
maps a prompt string to a JSON string can serve as the inductive reasoner
(README section 3.3). Bayesian confidence updates arrive on MVP Day 4; Day 3
stamps initial confidence from cluster support only.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from ._common import (
    check_float,
    check_int,
    check_mapping,
    check_str,
    utc_now_iso,
)
from .episodes import BehavioralEpisode
from .principles import BehavioralPrinciple, ContextTrigger, EpistemicStatus

__all__ = [
    "ClusteringResult",
    "DetectiveError",
    "EpisodeCluster",
    "LLMError",
    "LLMFn",
    "LLMOutputError",
    "build_inference_prompt",
    "cluster_episodes",
    "infer_from_cluster",
    "infer_from_cluster_with_llm",
    "infer_principles",
]

#: A pluggable inductive reasoner: prompt string in, JSON string out.
LLMFn = Callable[[str], str]

_DEFAULT_SIMILARITY_THRESHOLD = 0.5
_DEFAULT_MIN_CLUSTER_SIZE = 2
_MAX_PROMPT_EPISODES = 10
_MAX_PROMPT_DIFF_CHARS = 2000
_TRUNCATION_MARKER = "\n...[TRUNCATED: diff exceeds max_diff_chars]\n"


class DetectiveError(Exception):
    """Base error for Detective clustering/inference failures."""


class LLMError(DetectiveError):
    """Raised when the pluggable LLM function itself fails."""


class LLMOutputError(DetectiveError, ValueError):
    """Raised when LLM output is not parseable, contract-valid JSON."""


_ERROR_LINE_RE = re.compile(
    r"\b(try|except|catch|raise|throw|finally)\b|\b\w*(Error|Exception)\b"
)
_TEST_LINE_RE = re.compile(
    r"\b(assert|describe|it|pytest|unittest|TestCase|expect)\b"
)
_CONVENTIONAL_SUMMARY_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(\(.+\))?(!)?:\s+\S"
)
_TEST_BASENAME_RE = re.compile(r"(?:^|[_.])(test|spec)[_.]")
_DOC_EXTENSIONS = frozenset({".md", ".rst"})
_DEPENDENCY_BASENAMES = frozenset(
    {
        "requirements.txt",
        "pyproject.toml",
        "package.json",
        "package-lock.json",
        "go.mod",
        "Cargo.toml",
        "Gemfile",
        "pom.xml",
    }
)


def _is_test_path(path: str) -> bool:
    segments = path.lower().split("/")
    if any(seg in ("test", "tests", "spec", "__tests__") for seg in segments[:-1]):
        return True
    return _TEST_BASENAME_RE.search(segments[-1]) is not None


def _is_doc_path(path: str) -> bool:
    segments = path.lower().split("/")
    if segments[0] == "docs":
        return True
    return posixpath.splitext(segments[-1])[1] in _DOC_EXTENSIONS


def _added_lines(episode: BehavioralEpisode) -> str:
    """Added source lines across content-bearing transitions (no context)."""
    collected: list[str] = []
    for transition in episode.transitions:
        if transition.skipped or transition.binary or not transition.diff:
            continue
        for line in transition.diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                collected.append(line[1:])
    return "\n".join(collected)


def _detect_signals(episode: BehavioralEpisode) -> frozenset[str]:
    """Behavioral signals evidenced by one episode (possibly empty)."""
    signals: set[str] = set()
    added = _added_lines(episode)
    if _ERROR_LINE_RE.search(added):
        signals.add("error_handling")
    paths = [t.path for t in episode.transitions]
    if any(_is_test_path(p) for p in paths) or _TEST_LINE_RE.search(added):
        signals.add("testing")
    if _CONVENTIONAL_SUMMARY_RE.match(episode.summary.strip()):
        signals.add("conventional_commit")
    if any(_is_doc_path(p) for p in paths):
        signals.add("documentation")
    if any(p.rsplit("/", 1)[-1] in _DEPENDENCY_BASENAMES for p in paths):
        signals.add("dependency")
    if any(t.status in ("renamed", "copied") for t in episode.transitions):
        signals.add("restructure")
    return frozenset(signals)


@dataclass(frozen=True)
class _Features:
    language: str  # "" when the episode language is unknown
    scope: str
    extensions: frozenset[str]
    topdirs: frozenset[str]
    signals: frozenset[str]


def _features_of(episode: BehavioralEpisode) -> _Features:
    paths = [t.path for t in episode.transitions]
    extensions = frozenset(
        ext
        for p in paths
        if (ext := posixpath.splitext(p)[1].lower())
    )
    topdirs = frozenset(p.split("/", 1)[0] for p in paths if "/" in p)
    return _Features(
        language=episode.context.language or "",
        scope=episode.context.scope,
        extensions=extensions,
        topdirs=topdirs,
        signals=_detect_signals(episode),
    )


def _jaccard(first: frozenset[str], second: frozenset[str]) -> float:
    if not first and not second:
        return 0.0
    return len(first & second) / len(first | second)


def _pair_similarity(first: _Features, second: _Features) -> float:
    """Deterministic similarity in [0, 1] (stdlib-only, no embeddings)."""
    if first.language == second.language:
        language = 1.0 if first.language else 0.5
    elif not first.language or not second.language:
        language = 0.5
    else:
        language = 0.0
    if first.scope == second.scope:
        scope = 1.0 if first.scope else 0.5
    elif (
        first.scope
        and second.scope
        and (
            first.scope.startswith(second.scope + "/")
            or second.scope.startswith(first.scope + "/")
        )
    ):
        scope = 0.7
    elif not first.scope or not second.scope:
        scope = 0.3
    else:
        scope = 0.0
    paths = (
        _jaccard(first.extensions, second.extensions)
        + _jaccard(first.topdirs, second.topdirs)
    ) / 2
    signals = _jaccard(first.signals, second.signals)
    return 0.2 * language + 0.2 * scope + 0.3 * paths + 0.3 * signals


@dataclass(frozen=True)
class EpisodeCluster:
    """A group of similar episodes distilled into one principle."""

    id: str
    members: tuple[BehavioralEpisode, ...]
    language: Optional[str]
    scope: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", check_str(self.id, field="id", max_len=128))
        if (
            not isinstance(self.members, (list, tuple))
            or not self.members
            or any(not isinstance(m, BehavioralEpisode) for m in self.members)
        ):
            raise ValueError("members: expected a non-empty list of BehavioralEpisode")
        object.__setattr__(self, "members", tuple(self.members))
        if self.language is not None:
            object.__setattr__(
                self, "language", check_str(self.language, field="language")
            )
        object.__setattr__(
            self, "scope", check_str(self.scope, field="scope", allow_empty=True)
        )

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def member_ids(self) -> tuple[str, ...]:
        return tuple(m.id for m in self.members)

    @classmethod
    def from_dict(cls, data: Any) -> "EpisodeCluster":
        check_mapping(
            data,
            cls="EpisodeCluster",
            required=("id", "members", "language", "scope"),
        )
        if not isinstance(data["members"], (list, tuple)):
            raise ValueError("members: expected a list")
        return cls(
            id=data["id"],
            members=[BehavioralEpisode.from_dict(m) for m in data["members"]],
            language=data["language"],
            scope=data["scope"],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "members": [m.to_dict() for m in self.members],
            "language": self.language,
            "scope": self.scope,
        }


@dataclass(frozen=True)
class ClusteringResult:
    """Clustered episodes plus leftovers below the minimum cluster size."""

    clusters: tuple[EpisodeCluster, ...]
    unclustered: tuple[BehavioralEpisode, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.clusters, (list, tuple)) or any(
            not isinstance(c, EpisodeCluster) for c in self.clusters
        ):
            raise ValueError("clusters: expected a list of EpisodeCluster")
        object.__setattr__(self, "clusters", tuple(self.clusters))
        if not isinstance(self.unclustered, (list, tuple)) or any(
            not isinstance(e, BehavioralEpisode) for e in self.unclustered
        ):
            raise ValueError("unclustered: expected a list of BehavioralEpisode")
        object.__setattr__(self, "unclustered", tuple(self.unclustered))

    @classmethod
    def from_dict(cls, data: Any) -> "ClusteringResult":
        check_mapping(
            data, cls="ClusteringResult", required=("clusters", "unclustered")
        )
        if not isinstance(data["clusters"], (list, tuple)):
            raise ValueError("clusters: expected a list")
        if not isinstance(data["unclustered"], (list, tuple)):
            raise ValueError("unclustered: expected a list")
        return cls(
            clusters=[EpisodeCluster.from_dict(c) for c in data["clusters"]],
            unclustered=[BehavioralEpisode.from_dict(e) for e in data["unclustered"]],
        )

    def to_dict(self) -> dict:
        return {
            "clusters": [c.to_dict() for c in self.clusters],
            "unclustered": [e.to_dict() for e in self.unclustered],
        }


def _make_cluster(members: list[BehavioralEpisode]) -> EpisodeCluster:
    """Build a cluster from episodes already sorted by id (deterministic)."""
    digest = hashlib.sha1(
        "\n".join(m.id for m in members).encode("utf-8")
    ).hexdigest()[:12]
    languages = {m.context.language for m in members}
    language = next(iter(languages)) if len(languages) == 1 else None
    scopes = [m.context.scope for m in members if m.context.scope]
    if scopes and len(scopes) == len(members) and all(s == scopes[0] for s in scopes):
        scope = scopes[0]
    else:
        try:
            common = posixpath.commonpath(scopes) if scopes else "."
        except ValueError:
            common = "."
        scope = "" if common in ("", ".") else common
    return EpisodeCluster(
        id=f"cl_{digest}", members=tuple(members), language=language, scope=scope
    )


def cluster_episodes(
    episodes: Sequence[BehavioralEpisode],
    *,
    similarity_threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
    min_cluster_size: int = _DEFAULT_MIN_CLUSTER_SIZE,
) -> ClusteringResult:
    """Group similar episodes via thresholded single-linkage clustering.

    Episodes scoring at or above ``similarity_threshold`` join transitively
    (union-find); groups below ``min_cluster_size`` are returned as
    :attr:`ClusteringResult.unclustered`. Output order is deterministic:
    members sorted by episode id, clusters sorted by cluster id.
    """
    if not isinstance(episodes, (list, tuple)):
        raise ValueError("episodes: expected a list of BehavioralEpisode")
    for index, episode in enumerate(episodes):
        if not isinstance(episode, BehavioralEpisode):
            raise ValueError(
                f"episodes[{index}]: expected BehavioralEpisode, "
                f"got {type(episode).__name__}"
            )
    threshold = check_float(
        similarity_threshold,
        field="similarity_threshold",
        minimum=0.0,
        maximum=1.0,
    )
    if not 0.0 < threshold <= 1.0:
        raise ValueError("similarity_threshold: must be within (0.0, 1.0]")
    min_size = check_int(min_cluster_size, field="min_cluster_size", minimum=1)
    if not episodes:
        return ClusteringResult(clusters=(), unclustered=())

    ordered = sorted(episodes, key=lambda e: e.id)
    features = [_features_of(e) for e in ordered]
    parent = list(range(len(ordered)))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for left in range(len(ordered)):
        for right in range(left + 1, len(ordered)):
            if _pair_similarity(features[left], features[right]) >= threshold:
                parent[find(left)] = find(right)

    groups: dict[int, list[BehavioralEpisode]] = {}
    for index, episode in enumerate(ordered):
        groups.setdefault(find(index), []).append(episode)

    clusters: list[EpisodeCluster] = []
    unclustered: list[BehavioralEpisode] = []
    for members in groups.values():
        if len(members) >= min_size:
            clusters.append(_make_cluster(members))
        else:
            unclustered.extend(members)
    clusters.sort(key=lambda c: c.id)
    unclustered.sort(key=lambda e: e.id)
    return ClusteringResult(clusters=tuple(clusters), unclustered=tuple(unclustered))


_SIGNAL_TEMPLATES = {
    "error_handling": {
        "name": "Defensive Error Isolation",
        "category": "architecture",
        "invariant": (
            "Isolate failures at trust boundaries: catch low-level errors "
            "where they arise and re-raise them as typed domain errors "
            "instead of letting raw exceptions propagate to callers."
        ),
        "anti_patterns": (
            "Letting raw third-party exceptions propagate to callers",
            "Catching broad Exception without re-raising a typed error",
        ),
    },
    "testing": {
        "name": "Test-Backed Changes",
        "category": "testing",
        "invariant": (
            "Cover behavior changes with automated tests in the same change, "
            "keeping implementation and verification in lockstep."
        ),
        "anti_patterns": (
            "Merging behavior changes without accompanying tests",
            "Adding tests disconnected from the change they verify",
        ),
    },
    "conventional_commit": {
        "name": "Conventional Commit Hygiene",
        "category": "workflow",
        "invariant": (
            "Write conventional commit subjects (type: description) so every "
            "change announces its intent and scope at a glance."
        ),
        "anti_patterns": (
            "Committing with vague subjects such as 'fix stuff' or 'update'",
            "Mixing unrelated intents under one non-descriptive subject",
        ),
    },
    "documentation": {
        "name": "Docs-With-Change Discipline",
        "category": "documentation",
        "invariant": (
            "Update the affected documentation alongside the change it "
            "describes so prose and behavior never drift apart."
        ),
        "anti_patterns": (
            "Shipping behavior changes while leaving stale docs untouched",
            "Documenting intent in chat instead of the durable docs",
        ),
    },
    "dependency": {
        "name": "Explicit Dependency Declaration",
        "category": "architecture",
        "invariant": (
            "Declare dependency changes in the manifest (never by ambient "
            "environment drift) so every environment resolves identically."
        ),
        "anti_patterns": (
            "Relying on ambiently installed packages without manifesting them",
            "Pinning versions in prose instead of the dependency manifest",
        ),
    },
    "restructure": {
        "name": "Move-Then-Modify Restructuring",
        "category": "workflow",
        "invariant": (
            "Restructure via explicit moves/renames kept separable from "
            "behavior edits, preserving history across the relocation."
        ),
        "anti_patterns": (
            "Deleting and re-adding files instead of moving them",
            "Hiding behavior edits inside a bulk file relocation",
        ),
    },
}


def _initial_confidence(cluster_size: int) -> float:
    """Day 3 prior: grows with supporting episodes, capped at 0.85.

    Bayesian reinforcement/contradiction tracking arrives on MVP Day 4.
    """
    return min(0.55 + 0.05 * min(max(cluster_size, 1), 6), 0.85)


def _dominant_signal(cluster: EpisodeCluster) -> Optional[str]:
    """Most frequent signal across members; ties break alphabetically."""
    counts: Counter[str] = Counter()
    for member in cluster.members:
        counts.update(_detect_signals(member))
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _where(cluster: EpisodeCluster) -> str:
    if cluster.scope and cluster.language:
        return f"{cluster.scope} ({cluster.language})"
    if cluster.scope:
        return cluster.scope
    if cluster.language:
        return f"{cluster.language} code"
    return "the repository"


def _trigger_scope(cluster: EpisodeCluster) -> str:
    if cluster.scope and cluster.language:
        return f"{cluster.scope} ({cluster.language} code)"
    if cluster.scope:
        return cluster.scope
    if cluster.language:
        return f"{cluster.language} code"
    return "repository-wide changes"


def _fallback_invariant(cluster: EpisodeCluster) -> str:
    details: list[str] = []
    if cluster.language:
        details.append(f"language {cluster.language}")
    if cluster.scope:
        details.append(f"scope {cluster.scope}")
    extensions = sorted(
        {
            ext
            for m in cluster.members
            for t in m.transitions
            if (ext := posixpath.splitext(t.path)[1].lower())
        }
    )
    if extensions:
        details.append("extensions " + ", ".join(extensions))
    shape = f" ({'; '.join(details)})" if details else ""
    return (
        f"Repeat the recurring change shape{shape} consistently across "
        f"similar tasks. Observed across {cluster.size} episodes."
    )


def _principle_id(cluster: EpisodeCluster, signal: Optional[str]) -> str:
    stem = cluster.id[3:] if cluster.id.startswith("cl_") else cluster.id.lower()
    stem = re.sub(r"[^a-z0-9]+", "_", stem).strip("_") or "cluster"
    return f"bp_{signal or 'recurring'}_{stem}"[:128]


def _epistemic_status(cluster: EpisodeCluster, confidence: float) -> EpistemicStatus:
    now = utc_now_iso()
    return EpistemicStatus(
        confidence_score=confidence,
        supporting_episodes_count=cluster.size,
        contradictions_count=0,
        first_inferred=now,
        last_validated=now,
    )


def infer_from_cluster(cluster: EpisodeCluster) -> BehavioralPrinciple:
    """Distill one cluster into a principle with the offline heuristic.

    The dominant behavioral signal selects a template; cluster evidence
    (scope, language, size, member ids) fills the trigger, traces, and
    epistemic status. Clusters with no detectable signal yield a generic
    recurring-pattern principle describing the observed change shape.
    """
    if not isinstance(cluster, EpisodeCluster):
        raise ValueError(
            f"cluster: expected EpisodeCluster, got {type(cluster).__name__}"
        )
    signal = _dominant_signal(cluster)
    where = _where(cluster)
    if signal is None:
        name = f"Recurring Change Pattern in {where}"
        category = "workflow"
        invariant = _fallback_invariant(cluster)
        anti_patterns = (
            "Applying ad-hoc one-off edits where the recurring shape fits",
            "Leaving similar tasks inconsistent with the observed shape",
        )
    else:
        template = _SIGNAL_TEMPLATES[signal]
        name = f"{template['name']} in {where}"
        category = template["category"]
        invariant = (
            f"{template['invariant']} "
            f"Observed across {cluster.size} episodes in {where}."
        )
        anti_patterns = template["anti_patterns"]
    return BehavioralPrinciple(
        id=_principle_id(cluster, signal),
        name=name,
        category=category,
        context_trigger=ContextTrigger(
            scope=_trigger_scope(cluster),
            language_agnostic=cluster.language is None,
        ),
        latent_invariant=invariant,
        anti_patterns=anti_patterns,
        epistemic_status=_epistemic_status(
            cluster, _initial_confidence(cluster.size)
        ),
        inspectable_traces=tuple(sorted(cluster.member_ids)),
    )


def build_inference_prompt(
    cluster: EpisodeCluster,
    *,
    max_episodes: int = _MAX_PROMPT_EPISODES,
    max_diff_chars: int = _MAX_PROMPT_DIFF_CHARS,
) -> str:
    """Render the induction prompt for one cluster (pre-redacted content).

    Episode diffs arrive truncated per ``max_diff_chars``; only the first
    ``max_episodes`` members (sorted by id) are inlined. Skipped and binary
    transitions contribute a marker line only, never content.
    """
    if not isinstance(cluster, EpisodeCluster):
        raise ValueError(
            f"cluster: expected EpisodeCluster, got {type(cluster).__name__}"
        )
    max_episodes = check_int(max_episodes, field="max_episodes", minimum=1)
    max_diff_chars = check_int(max_diff_chars, field="max_diff_chars", minimum=1)
    lines = [
        "You are MIMIR's inductive inference engine (the Detective).",
        "Distill the observed behavioral episodes below into ONE reusable",
        "behavioral principle capturing the latent invariant behind them.",
        f"Cluster: {cluster.id} | size {cluster.size} | "
        f"language {cluster.language or 'unknown'} | "
        f"scope {cluster.scope or 'repository-wide'}",
        "",
        "Episodes (content is pre-redacted; skipped/binary carry no diff):",
    ]
    for episode in cluster.members[:max_episodes]:
        files = ",".join(t.path for t in episode.transitions) or "-"
        lines.append(f"--- {episode.id} | {episode.observed_at} | {episode.summary}")
        lines.append(
            f"scope={episode.context.scope or '-'} "
            f"language={episode.context.language or '-'} files={files}"
        )
        for transition in episode.transitions:
            if transition.skipped:
                lines.append(
                    f"[{transition.path} skipped: {transition.skip_reason}]"
                )
                continue
            if transition.binary:
                lines.append(f"[{transition.path} binary, no content]")
                continue
            diff = transition.diff
            if len(diff) > max_diff_chars:
                diff = diff[:max_diff_chars] + _TRUNCATION_MARKER
            lines.append(
                f"[{transition.path} {transition.status} "
                f"+{transition.additions}/-{transition.deletions}]"
            )
            lines.append(diff)
    if cluster.size > max_episodes:
        lines.append(
            f"... and {cluster.size - max_episodes} more episode(s) "
            "in this cluster (same shape, omitted for brevity)."
        )
    lines.extend(
        [
            "",
            "Respond with JSON ONLY, no prose, matching this schema:",
            '{"name": str, "category": str, '
            '"context_trigger": {"scope": str, "language_agnostic": bool}, '
            '"latent_invariant": str, "anti_patterns": [str], '
            '"confidence": number 0..1 (optional)}',
        ]
    )
    return "\n".join(lines)


def _extract_json(raw: str) -> Any:
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


def infer_from_cluster_with_llm(
    cluster: EpisodeCluster,
    llm_fn: LLMFn,
    *,
    max_episodes: int = _MAX_PROMPT_EPISODES,
    max_diff_chars: int = _MAX_PROMPT_DIFF_CHARS,
) -> BehavioralPrinciple:
    """Distill one cluster via a pluggable LLM function.

    The LLM proposes the semantic fields (name, category, trigger,
    invariant, anti-patterns, optional confidence); the id, epistemic
    status, and inspectable traces stay deterministic and evidence-bound.
    Unknown extra keys in the LLM payload are ignored; missing or
    contract-invalid fields raise :class:`LLMOutputError`.
    """
    if not isinstance(cluster, EpisodeCluster):
        raise ValueError(
            f"cluster: expected EpisodeCluster, got {type(cluster).__name__}"
        )
    if not callable(llm_fn):
        raise ValueError("llm_fn: expected a callable(prompt) -> str")
    prompt = build_inference_prompt(
        cluster, max_episodes=max_episodes, max_diff_chars=max_diff_chars
    )
    try:
        raw = llm_fn(prompt)
    except Exception as exc:
        raise LLMError(f"llm_fn failed: {exc}") from exc
    if not isinstance(raw, str) or not raw.strip():
        raise LLMOutputError("llm_fn: expected a non-empty JSON string")
    payload = _extract_json(raw)
    if not isinstance(payload, dict):
        raise LLMOutputError("unparseable LLM output: expected a JSON object")
    required = (
        "name",
        "category",
        "context_trigger",
        "latent_invariant",
        "anti_patterns",
    )
    missing = [key for key in required if key not in payload]
    if missing:
        raise LLMOutputError(f"LLM output missing keys: {missing}")
    try:
        confidence = check_float(
            payload.get("confidence", _initial_confidence(cluster.size)),
            field="confidence",
        )
        return BehavioralPrinciple.from_dict(
            {
                "id": _principle_id(cluster, _dominant_signal(cluster)),
                "name": payload["name"],
                "category": payload["category"],
                "context_trigger": payload["context_trigger"],
                "latent_invariant": payload["latent_invariant"],
                "anti_patterns": payload["anti_patterns"],
                "epistemic_status": _epistemic_status(cluster, confidence).to_dict(),
                "inspectable_traces": sorted(cluster.member_ids),
            }
        )
    except ValueError as exc:
        raise LLMOutputError(
            f"LLM output violates principle contract: {exc}"
        ) from None


def infer_principles(
    episodes: Sequence[BehavioralEpisode],
    *,
    llm_fn: Optional[LLMFn] = None,
    similarity_threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
    min_cluster_size: int = _DEFAULT_MIN_CLUSTER_SIZE,
    max_episodes: int = _MAX_PROMPT_EPISODES,
    max_diff_chars: int = _MAX_PROMPT_DIFF_CHARS,
) -> list[BehavioralPrinciple]:
    """Cluster episodes and distill one principle per qualifying cluster.

    With ``llm_fn=None`` (default) the offline heuristic generates every
    principle; otherwise each cluster prompt goes through ``llm_fn``.
    Clusters are processed in deterministic id order.
    """
    if llm_fn is not None and not callable(llm_fn):
        raise ValueError("llm_fn: expected a callable(prompt) -> str")
    result = cluster_episodes(
        episodes,
        similarity_threshold=similarity_threshold,
        min_cluster_size=min_cluster_size,
    )
    principles: list[BehavioralPrinciple] = []
    for cluster in result.clusters:
        if llm_fn is None:
            principles.append(infer_from_cluster(cluster))
        else:
            principles.append(
                infer_from_cluster_with_llm(
                    cluster,
                    llm_fn,
                    max_episodes=max_episodes,
                    max_diff_chars=max_diff_chars,
                )
            )
    return principles
