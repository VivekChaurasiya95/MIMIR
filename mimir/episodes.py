"""BehavioralEpisode data contract.

An episode is one observed behavioral transition: what the human changed,
in what context, with what outcome, and what privacy redaction was applied.
Episodes are the raw evidence that the Detective (MVP Day 3) distills into
:class:`~mimir.principles.BehavioralPrinciple` objects. MVP Day 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ._common import (
    check_bool,
    check_int,
    check_mapping,
    check_str,
    check_str_list,
    normalize_timestamp,
)

__all__ = [
    "BehavioralEpisode",
    "EpisodeContext",
    "EpisodeOutcome",
    "FileTransition",
    "FILE_STATUSES",
    "OUTCOME_STATES",
]

FILE_STATUSES = frozenset(
    {"added", "modified", "deleted", "renamed", "copied", "unknown"}
)
OUTCOME_STATES = frozenset({"unknown", "pass", "fail"})


@dataclass(frozen=True)
class FileTransition:
    """Before/after transition of a single file (redacted unified diff)."""

    path: str
    status: str
    diff: str
    additions: int
    deletions: int
    truncated: bool = False
    binary: bool = False
    skipped: bool = False
    skip_reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", check_str(self.path, field="path"))
        if self.status not in FILE_STATUSES:
            raise ValueError(
                f"status: expected one of {sorted(FILE_STATUSES)}, "
                f"got {self.status!r}"
            )
        object.__setattr__(
            self, "diff", check_str(self.diff, field="diff", allow_empty=True)
        )
        object.__setattr__(
            self, "additions", check_int(self.additions, field="additions")
        )
        object.__setattr__(
            self, "deletions", check_int(self.deletions, field="deletions")
        )
        object.__setattr__(
            self, "truncated", check_bool(self.truncated, field="truncated")
        )
        object.__setattr__(self, "binary", check_bool(self.binary, field="binary"))
        object.__setattr__(
            self, "skipped", check_bool(self.skipped, field="skipped")
        )
        if self.skipped:
            # Skipped files retain metadata only; no content may leak through.
            if self.diff:
                raise ValueError("diff: skipped transitions must not carry content")
            object.__setattr__(
                self,
                "skip_reason",
                check_str(self.skip_reason, field="skip_reason"),
            )
        else:
            if self.skip_reason:
                raise ValueError(
                    "skip_reason: set on a non-skipped transition"
                )
            object.__setattr__(self, "skip_reason", "")
        if self.binary and self.diff:
            raise ValueError("diff: binary transitions must not carry content")

    @classmethod
    def from_dict(cls, data: Any) -> "FileTransition":
        check_mapping(
            data,
            cls="FileTransition",
            required=("path", "status", "diff", "additions", "deletions"),
            optional=("truncated", "binary", "skipped", "skip_reason"),
        )
        return cls(
            path=data["path"],
            status=data["status"],
            diff=data["diff"],
            additions=data["additions"],
            deletions=data["deletions"],
            truncated=data.get("truncated", False),
            binary=data.get("binary", False),
            skipped=data.get("skipped", False),
            skip_reason=data.get("skip_reason", ""),
        )

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "status": self.status,
            "diff": self.diff,
            "additions": self.additions,
            "deletions": self.deletions,
            "truncated": self.truncated,
            "binary": self.binary,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


@dataclass(frozen=True)
class EpisodeContext:
    """Where the observed behavior happened."""

    scope: str
    language: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "scope", check_str(self.scope, field="scope", allow_empty=True)
        )
        if self.language is not None:
            object.__setattr__(
                self,
                "language",
                check_str(self.language, field="language"),
            )

    @classmethod
    def from_dict(cls, data: Any) -> "EpisodeContext":
        check_mapping(
            data, cls="EpisodeContext", required=("scope",), optional=("language",)
        )
        return cls(scope=data["scope"], language=data.get("language"))

    def to_dict(self) -> dict:
        return {"scope": self.scope, "language": self.language}


@dataclass(frozen=True)
class EpisodeOutcome:
    """Validation signals observed for the episode (MVP: usually unknown)."""

    build: str = "unknown"
    tests: str = "unknown"
    note: str = ""

    def __post_init__(self) -> None:
        if self.build not in OUTCOME_STATES:
            raise ValueError(
                f"build: expected one of {sorted(OUTCOME_STATES)}, "
                f"got {self.build!r}"
            )
        if self.tests not in OUTCOME_STATES:
            raise ValueError(
                f"tests: expected one of {sorted(OUTCOME_STATES)}, "
                f"got {self.tests!r}"
            )
        object.__setattr__(
            self, "note", check_str(self.note, field="note", allow_empty=True)
        )

    @classmethod
    def from_dict(cls, data: Any) -> "EpisodeOutcome":
        check_mapping(
            data,
            cls="EpisodeOutcome",
            required=(),
            optional=("build", "tests", "note"),
        )
        return cls(
            build=data.get("build", "unknown"),
            tests=data.get("tests", "unknown"),
            note=data.get("note", ""),
        )

    def to_dict(self) -> dict:
        return {"build": self.build, "tests": self.tests, "note": self.note}


@dataclass(frozen=True)
class BehavioralEpisode:
    """One observed behavioral transition with context and outcome."""

    id: str
    source: str
    repo: str
    source_ref: str
    observed_at: str
    actor: str
    summary: str
    context: EpisodeContext
    transitions: tuple
    outcome: EpisodeOutcome
    redactions_applied: int
    redaction_kinds: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", check_str(self.id, field="id", max_len=128))
        object.__setattr__(
            self, "source", check_str(self.source, field="source")
        )
        object.__setattr__(self, "repo", check_str(self.repo, field="repo"))
        object.__setattr__(
            self, "source_ref", check_str(self.source_ref, field="source_ref")
        )
        object.__setattr__(
            self,
            "observed_at",
            normalize_timestamp(self.observed_at, field="observed_at"),
        )
        object.__setattr__(
            self, "actor", check_str(self.actor, field="actor")
        )
        object.__setattr__(
            self, "summary", check_str(self.summary, field="summary", allow_empty=True)
        )
        if not isinstance(self.context, EpisodeContext):
            raise ValueError(
                "context: expected EpisodeContext, "
                f"got {type(self.context).__name__}"
            )
        if not isinstance(self.transitions, (list, tuple)) or any(
            not isinstance(t, FileTransition) for t in self.transitions
        ):
            raise ValueError("transitions: expected list of FileTransition")
        object.__setattr__(self, "transitions", tuple(self.transitions))
        if not isinstance(self.outcome, EpisodeOutcome):
            raise ValueError(
                f"outcome: expected EpisodeOutcome, got {type(self.outcome).__name__}"
            )
        object.__setattr__(
            self,
            "redactions_applied",
            check_int(self.redactions_applied, field="redactions_applied"),
        )
        object.__setattr__(
            self,
            "redaction_kinds",
            check_str_list(self.redaction_kinds, field="redaction_kinds"),
        )
        if (self.redactions_applied > 0) != (len(self.redaction_kinds) > 0):
            raise ValueError(
                "redactions_applied > 0 must coincide with "
                "non-empty redaction_kinds"
            )

    @classmethod
    def from_dict(cls, data: Any) -> "BehavioralEpisode":
        check_mapping(
            data,
            cls="BehavioralEpisode",
            required=(
                "id",
                "source",
                "repo",
                "source_ref",
                "observed_at",
                "actor",
                "summary",
                "context",
                "transitions",
                "outcome",
                "redactions_applied",
                "redaction_kinds",
            ),
        )
        if not isinstance(data["transitions"], (list, tuple)):
            raise ValueError("transitions: expected a list")
        return cls(
            id=data["id"],
            source=data["source"],
            repo=data["repo"],
            source_ref=data["source_ref"],
            observed_at=data["observed_at"],
            actor=data["actor"],
            summary=data["summary"],
            context=EpisodeContext.from_dict(data["context"]),
            transitions=[
                FileTransition.from_dict(item) for item in data["transitions"]
            ],
            outcome=EpisodeOutcome.from_dict(data["outcome"]),
            redactions_applied=data["redactions_applied"],
            redaction_kinds=data["redaction_kinds"],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "repo": self.repo,
            "source_ref": self.source_ref,
            "observed_at": self.observed_at,
            "actor": self.actor,
            "summary": self.summary,
            "context": self.context.to_dict(),
            "transitions": [t.to_dict() for t in self.transitions],
            "outcome": self.outcome.to_dict(),
            "redactions_applied": self.redactions_applied,
            "redaction_kinds": list(self.redaction_kinds),
        }
