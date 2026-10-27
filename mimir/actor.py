"""Replication Actor: persona-aligned synthesis from retrieved principles.

Session B (MVP Day 6). Closes the loop from memory to execution: given a
novel task, the actor retrieves relevant principles from
:class:`~mimir.store.PrincipleStore`, compiles them into a behavioral
steering prompt (mandatory invariants + forbidden anti-patterns), and
synthesizes a solution through a pluggable LLM function. A dual-track
contrast baseline (unconditioned) proves genuine style transfer, and an
offline compliance check flags forbidden literal phrases in the output.

With ``llm_fn=None`` the actor stays fully offline, emitting a labeled
heuristic scaffold instead of an LLM synthesis. Compliance violations are
reported, never raised: callers decide how to respond.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from ._common import (
    check_bool,
    check_float,
    check_int,
    check_mapping,
    check_str,
)
from .principles import BehavioralPrinciple
from .store import PrincipleStore

__all__ = [
    "ActorError",
    "LLMFn",
    "ReplicationActor",
    "ReplicationRequest",
    "ReplicationResult",
    "build_baseline_prompt",
    "check_compliance",
]

#: Pluggable LLM completion: prompt string in, solution string out.
#: Same shape as :data:`mimir.detective.LLMFn`, but the output contract is
#: free-form solution text rather than JSON.
LLMFn = Callable[[str], str]

_LITERAL_SPAN_RE = re.compile(r"`([^`]+)`")
_MIN_PHRASE_LEN = 12
_MIN_LITERAL_LEN = 2


class ActorError(Exception):
    """Base error for replication synthesis failures."""


@dataclass(frozen=True)
class ReplicationRequest:
    """Input payload for reproducing behavior on a novel task."""

    task_description: str
    target_language: Optional[str] = None
    target_scope: Optional[str] = None
    min_confidence: float = 0.70
    top_k_principles: int = 5

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "task_description", check_str(self.task_description, field="task_description")
        )
        if self.target_language is not None:
            object.__setattr__(
                self,
                "target_language",
                check_str(self.target_language, field="target_language"),
            )
        if self.target_scope is not None:
            object.__setattr__(
                self,
                "target_scope",
                check_str(self.target_scope, field="target_scope"),
            )
        object.__setattr__(
            self,
            "min_confidence",
            check_float(self.min_confidence, field="min_confidence"),
        )
        object.__setattr__(
            self,
            "top_k_principles",
            check_int(self.top_k_principles, field="top_k_principles", minimum=1),
        )

    @classmethod
    def from_dict(cls, data: Any) -> "ReplicationRequest":
        check_mapping(
            data,
            cls="ReplicationRequest",
            required=("task_description",),
            optional=(
                "target_language",
                "target_scope",
                "min_confidence",
                "top_k_principles",
            ),
        )
        return cls(
            task_description=data["task_description"],
            target_language=data.get("target_language"),
            target_scope=data.get("target_scope"),
            min_confidence=data.get("min_confidence", 0.70),
            top_k_principles=data.get("top_k_principles", 5),
        )

    def to_dict(self) -> dict:
        return {
            "task_description": self.task_description,
            "target_language": self.target_language,
            "target_scope": self.target_scope,
            "min_confidence": self.min_confidence,
            "top_k_principles": self.top_k_principles,
        }


@dataclass(frozen=True)
class ReplicationResult:
    """Output containing replicated solution and comparative metrics."""

    task_description: str
    applied_principles: tuple[BehavioralPrinciple, ...]
    replicated_solution: str
    contrast_baseline: Optional[str]
    compliance_report: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "task_description", check_str(self.task_description, field="task_description")
        )
        if not isinstance(self.applied_principles, (list, tuple)) or any(
            not isinstance(p, BehavioralPrinciple) for p in self.applied_principles
        ):
            raise ValueError(
                "applied_principles: expected a list of BehavioralPrinciple"
            )
        object.__setattr__(self, "applied_principles", tuple(self.applied_principles))
        object.__setattr__(
            self,
            "replicated_solution",
            check_str(self.replicated_solution, field="replicated_solution"),
        )
        if self.contrast_baseline is not None:
            object.__setattr__(
                self,
                "contrast_baseline",
                check_str(self.contrast_baseline, field="contrast_baseline"),
            )
        if not isinstance(self.compliance_report, dict) or any(
            not isinstance(k, str) for k in self.compliance_report
        ):
            raise ValueError("compliance_report: expected a dict with str keys")
        object.__setattr__(
            self, "compliance_report", dict(self.compliance_report)
        )

    @classmethod
    def from_dict(cls, data: Any) -> "ReplicationResult":
        check_mapping(
            data,
            cls="ReplicationResult",
            required=(
                "task_description",
                "applied_principles",
                "replicated_solution",
                "contrast_baseline",
                "compliance_report",
            ),
        )
        if not isinstance(data["applied_principles"], (list, tuple)):
            raise ValueError("applied_principles: expected a list")
        return cls(
            task_description=data["task_description"],
            applied_principles=[
                BehavioralPrinciple.from_dict(p)
                for p in data["applied_principles"]
            ],
            replicated_solution=data["replicated_solution"],
            contrast_baseline=data["contrast_baseline"],
            compliance_report=data["compliance_report"],
        )

    def to_dict(self) -> dict:
        return {
            "task_description": self.task_description,
            "applied_principles": [p.to_dict() for p in self.applied_principles],
            "replicated_solution": self.replicated_solution,
            "contrast_baseline": self.contrast_baseline,
            "compliance_report": dict(self.compliance_report),
        }


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _checkable_spans(anti_pattern: str) -> list[str]:
    """Machine-checkable fragments of one anti-pattern (see below).

    Backtick-quoted literals (`` `except Exception:` ``) are checked
    verbatim; otherwise the full normalized phrase is checked when long
    enough to be distinctive. Authoring convention: quote code literals in
    anti-patterns to make them enforceable.
    """
    spans = [
        span.strip()
        for span in _LITERAL_SPAN_RE.findall(anti_pattern)
        if len(span.strip()) >= _MIN_LITERAL_LEN
    ]
    if spans:
        return spans
    normalized = _normalize(anti_pattern)
    if len(normalized) >= _MIN_PHRASE_LEN:
        return [normalized]
    return []


def check_compliance(
    solution: str,
    principles: Sequence[BehavioralPrinciple],
) -> dict[str, Any]:
    """Flag forbidden anti-pattern fragments present in ``solution``.

    Returns ``{"checked_principle_ids", "anti_patterns_checked",
    "violations", "compliant", "compliance_score"}`` where each violation
    is ``{"principle_id", "anti_pattern", "matched_text"}``. Matching is
    case-insensitive substring on normalized text. Never raises on
    violations; an empty principle list is vacuously compliant (score 1.0).
    """
    if not isinstance(solution, str):
        raise ValueError(
            f"solution: expected string, got {type(solution).__name__}"
        )
    if not isinstance(principles, (list, tuple)) or any(
        not isinstance(p, BehavioralPrinciple) for p in principles
    ):
        raise ValueError("principles: expected a list of BehavioralPrinciple")
    haystack = _normalize(solution)
    violations: list[dict[str, str]] = []
    checked = 0
    for principle in principles:
        for anti_pattern in principle.anti_patterns:
            checked += 1
            for span in _checkable_spans(anti_pattern):
                if _normalize(span) in haystack:
                    violations.append(
                        {
                            "principle_id": principle.id,
                            "anti_pattern": anti_pattern,
                            "matched_text": span,
                        }
                    )
                    break
    score = 1.0 if checked == 0 else max(0.0, 1.0 - len(violations) / checked)
    return {
        "checked_principle_ids": [p.id for p in principles],
        "anti_patterns_checked": checked,
        "violations": violations,
        "compliant": not violations,
        "compliance_score": score,
    }


def build_baseline_prompt(task: str) -> str:
    """Render the unconditioned contrast prompt (no principles attached)."""
    task = check_str(task, field="task")
    return "\n".join(
        [
            "You are a senior engineer. Solve the novel task below in a",
            "clean, conventional style.",
            "",
            "Novel task:",
            task,
            "",
            "Respond with the solution only.",
        ]
    )


class ReplicationActor:
    """Synthesizes solutions to novel problems conditioned on principles."""

    def __init__(
        self, store: PrincipleStore, llm_fn: Optional[LLMFn] = None
    ) -> None:
        if not isinstance(store, PrincipleStore):
            raise ValueError(
                f"store: expected PrincipleStore, got {type(store).__name__}"
            )
        if llm_fn is not None and not callable(llm_fn):
            raise ValueError("llm_fn: expected a callable(prompt) -> str")
        self.store = store
        self.llm_fn = llm_fn

    def compile_prompt(
        self,
        task: str,
        principles: Sequence[BehavioralPrinciple],
    ) -> str:
        """Compile a novel task + principles into a steering prompt."""
        task = check_str(task, field="task")
        if not isinstance(principles, (list, tuple)) or any(
            not isinstance(p, BehavioralPrinciple) for p in principles
        ):
            raise ValueError("principles: expected a list of BehavioralPrinciple")
        lines = [
            "You are MIMIR's replication engine. Solve the novel task below",
            "while strictly adhering to the user's behavioral principles.",
            "",
            "Novel task:",
            task,
            "",
            "Mandatory invariants (non-negotiable):",
        ]
        if principles:
            for index, principle in enumerate(principles, 1):
                lines.append(
                    f"{index}. [{principle.id}] {principle.latent_invariant} "
                    f"(applies to: {principle.context_trigger.scope})"
                )
        else:
            lines.append("(none retrieved -- solve in a clean, conventional style.)")
        lines.append("")
        lines.append("Forbidden anti-patterns (never emit these):")
        if any(p.anti_patterns for p in principles):
            counter = 0
            for principle in principles:
                for anti_pattern in principle.anti_patterns:
                    counter += 1
                    lines.append(f"{counter}. [{principle.id}] {anti_pattern}")
        else:
            lines.append("(none.)")
        lines.extend(
            [
                "",
                "Respond with the solution only. Do not explain the principles;",
                "embody them.",
            ]
        )
        return "\n".join(lines)

    def _scaffold(
        self, task: str, principles: Sequence[BehavioralPrinciple]
    ) -> str:
        """Deterministic offline solution scaffold (no LLM configured)."""
        lines = [
            "[MIMIR scaffold -- no llm_fn configured; embody these manually]",
            f"Task: {task}",
            "",
            "Mandatory invariants to embody:",
        ]
        if principles:
            for index, principle in enumerate(principles, 1):
                lines.append(
                    f"{index}. [{principle.id}] {principle.latent_invariant}"
                )
        else:
            lines.append("(none retrieved.)")
        lines.append("")
        lines.append("Forbidden anti-patterns to avoid:")
        if any(p.anti_patterns for p in principles):
            counter = 0
            for principle in principles:
                for anti_pattern in principle.anti_patterns:
                    counter += 1
                    lines.append(f"{counter}. [{principle.id}] {anti_pattern}")
        else:
            lines.append("(none.)")
        return "\n".join(lines)

    def _complete(self, prompt: str, *, kind: str) -> str:
        assert self.llm_fn is not None
        try:
            raw = self.llm_fn(prompt)
        except Exception as exc:
            raise ActorError(f"llm_fn failed during {kind}: {exc}") from exc
        if not isinstance(raw, str) or not raw.strip():
            raise ActorError(f"llm_fn returned an empty {kind}")
        return raw

    def replicate(
        self,
        request: ReplicationRequest,
        generate_contrast_baseline: bool = True,
    ) -> ReplicationResult:
        """Query the store, steer synthesis, and verify compliance."""
        if not isinstance(request, ReplicationRequest):
            raise ValueError(
                "request: expected ReplicationRequest, "
                f"got {type(request).__name__}"
            )
        generate_contrast_baseline = check_bool(
            generate_contrast_baseline, field="generate_contrast_baseline"
        )
        query_parts = [request.task_description]
        if request.target_language:
            query_parts.append(request.target_language)
        if request.target_scope:
            query_parts.append(request.target_scope)
        principles = tuple(
            self.store.query_relevant(
                "\n".join(query_parts),
                top_k=request.top_k_principles,
                min_confidence=request.min_confidence,
            )
        )
        prompt = self.compile_prompt(request.task_description, principles)
        if self.llm_fn is None:
            solution = self._scaffold(request.task_description, principles)
            baseline: Optional[str] = None
        else:
            solution = self._complete(prompt, kind="steered solution")
            baseline = None
            if generate_contrast_baseline:
                baseline = self._complete(
                    build_baseline_prompt(request.task_description),
                    kind="contrast baseline",
                )
        return ReplicationResult(
            task_description=request.task_description,
            applied_principles=principles,
            replicated_solution=solution,
            contrast_baseline=baseline,
            compliance_report=check_compliance(solution, principles),
        )
