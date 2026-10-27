"""BehavioralPrinciple data contract.

Implements the atomic unit of MIMIR learning defined in README section 4
("The Data Contract"). MVP Day 1.

A principle is a structured, transferable, falsifiable representation of one
human instinct: when it triggers, what invariant it asserts, what it forbids,
and the evidence history behind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._common import (
    check_bool,
    check_float,
    check_int,
    check_mapping,
    check_str,
    check_str_list,
    normalize_timestamp,
    utc_now_iso,
)

__all__ = ["BehavioralPrinciple", "ContextTrigger", "EpistemicStatus"]


@dataclass(frozen=True)
class ContextTrigger:
    """When a principle applies."""

    scope: str
    language_agnostic: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "scope", check_str(self.scope, field="context_trigger.scope")
        )
        object.__setattr__(
            self,
            "language_agnostic",
            check_bool(
                self.language_agnostic,
                field="context_trigger.language_agnostic",
            ),
        )

    @classmethod
    def from_dict(cls, data: Any) -> "ContextTrigger":
        check_mapping(
            data, cls="ContextTrigger", required=("scope", "language_agnostic")
        )
        return cls(scope=data["scope"], language_agnostic=data["language_agnostic"])

    def to_dict(self) -> dict:
        return {"scope": self.scope, "language_agnostic": self.language_agnostic}


@dataclass(frozen=True)
class EpistemicStatus:
    """Confidence and evidence history behind a principle."""

    confidence_score: float
    supporting_episodes_count: int
    contradictions_count: int
    first_inferred: str
    last_validated: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "confidence_score",
            check_float(
                self.confidence_score, field="epistemic_status.confidence_score"
            ),
        )
        object.__setattr__(
            self,
            "supporting_episodes_count",
            check_int(
                self.supporting_episodes_count,
                field="epistemic_status.supporting_episodes_count",
            ),
        )
        object.__setattr__(
            self,
            "contradictions_count",
            check_int(
                self.contradictions_count,
                field="epistemic_status.contradictions_count",
            ),
        )
        object.__setattr__(
            self,
            "first_inferred",
            normalize_timestamp(
                self.first_inferred, field="epistemic_status.first_inferred"
            ),
        )
        object.__setattr__(
            self,
            "last_validated",
            normalize_timestamp(
                self.last_validated, field="epistemic_status.last_validated"
            ),
        )

    @classmethod
    def new(
        cls, confidence: float, *, supporting: int = 0, contradictions: int = 0
    ) -> "EpistemicStatus":
        """Fresh epistemic status stamped at the current UTC time."""
        now = utc_now_iso()
        return cls(
            confidence_score=confidence,
            supporting_episodes_count=supporting,
            contradictions_count=contradictions,
            first_inferred=now,
            last_validated=now,
        )

    @classmethod
    def from_dict(cls, data: Any) -> "EpistemicStatus":
        check_mapping(
            data,
            cls="EpistemicStatus",
            required=(
                "confidence_score",
                "supporting_episodes_count",
                "contradictions_count",
                "first_inferred",
                "last_validated",
            ),
        )
        return cls(
            confidence_score=data["confidence_score"],
            supporting_episodes_count=data["supporting_episodes_count"],
            contradictions_count=data["contradictions_count"],
            first_inferred=data["first_inferred"],
            last_validated=data["last_validated"],
        )

    def to_dict(self) -> dict:
        return {
            "confidence_score": self.confidence_score,
            "supporting_episodes_count": self.supporting_episodes_count,
            "contradictions_count": self.contradictions_count,
            "first_inferred": self.first_inferred,
            "last_validated": self.last_validated,
        }


@dataclass(frozen=True)
class BehavioralPrinciple:
    """One reusable, inspectable unit of learned behavior (README section 4)."""

    id: str
    name: str
    category: str
    context_trigger: ContextTrigger
    latent_invariant: str
    anti_patterns: tuple[str, ...]
    epistemic_status: EpistemicStatus
    inspectable_traces: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", check_str(self.id, field="id", max_len=128))
        object.__setattr__(self, "name", check_str(self.name, field="name"))
        object.__setattr__(
            self, "category", check_str(self.category, field="category")
        )
        if not isinstance(self.context_trigger, ContextTrigger):
            raise ValueError(
                "context_trigger: expected ContextTrigger, "
                f"got {type(self.context_trigger).__name__}"
            )
        object.__setattr__(
            self,
            "latent_invariant",
            check_str(self.latent_invariant, field="latent_invariant"),
        )
        object.__setattr__(
            self,
            "anti_patterns",
            check_str_list(self.anti_patterns, field="anti_patterns"),
        )
        if not isinstance(self.epistemic_status, EpistemicStatus):
            raise ValueError(
                "epistemic_status: expected EpistemicStatus, "
                f"got {type(self.epistemic_status).__name__}"
            )
        object.__setattr__(
            self,
            "inspectable_traces",
            check_str_list(self.inspectable_traces, field="inspectable_traces"),
        )

    @classmethod
    def from_dict(cls, data: Any) -> "BehavioralPrinciple":
        check_mapping(
            data,
            cls="BehavioralPrinciple",
            required=(
                "id",
                "name",
                "category",
                "context_trigger",
                "latent_invariant",
                "anti_patterns",
                "epistemic_status",
                "inspectable_traces",
            ),
        )
        return cls(
            id=data["id"],
            name=data["name"],
            category=data["category"],
            context_trigger=ContextTrigger.from_dict(data["context_trigger"]),
            latent_invariant=data["latent_invariant"],
            anti_patterns=data["anti_patterns"],
            epistemic_status=EpistemicStatus.from_dict(data["epistemic_status"]),
            inspectable_traces=data["inspectable_traces"],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "context_trigger": self.context_trigger.to_dict(),
            "latent_invariant": self.latent_invariant,
            "anti_patterns": list(self.anti_patterns),
            "epistemic_status": self.epistemic_status.to_dict(),
            "inspectable_traces": list(self.inspectable_traces),
        }
