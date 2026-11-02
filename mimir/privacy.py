"""Pre-publish privacy gate for the ambient sensory pipeline (Phase 1).

Every filesystem path and every payload string is screened *before* it
reaches the event bus:

- allowlist-only: paths outside ``allowed_roots`` are denied, so a
  symlink escape, ``..`` traversal, or stray watchdog report can never
  leak an out-of-scope file into telemetry;
- forbidden files: secret-bearing basenames (``.env``, ``*.pem``,
  ``*.key``, ``id_rsa*``, ...) are denied with metadata-only handling,
  matching :mod:`mimir.telemetry` ingestion policy;
- token redaction: payload strings pass through
  :func:`mimir.telemetry.redact_text` before publish, so embedded
  tokens never reach subscribers.

The gate is dependency-free, never raises for screening decisions
(deny-by-boolean), and never stores original secret values.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .telemetry import SECRET_FILENAME_GLOBS, RedactionResult, redact_text

__all__ = [
    "FORBIDDEN_EXTENSIONS",
    "FORBIDDEN_NAME_PREFIXES",
    "GateDecision",
    "PrivacyGate",
]

#: Extensions that are pure secret material (task contract: forbidden
#: extensions). Matched case-insensitively against the path suffix.
FORBIDDEN_EXTENSIONS = frozenset({
    ".pem",
    ".key",
    ".pfx",
    ".p12",
})

#: Basename prefixes that are secret material even without an
#: extension (task contract: ``id_rsa``; ``.env`` handled via globs).
FORBIDDEN_NAME_PREFIXES = ("id_rsa",)


def _canonical(text: str) -> str:
    """Canonical form for allowlist comparison (case-folded abspath)."""
    stripped = text
    if stripped.startswith("\\\\?\\UNC\\"):
        stripped = "\\\\" + stripped[len("\\\\?\\UNC\\"):]
    elif stripped.startswith("\\\\?\\"):
        stripped = stripped[len("\\\\?\\"):]
    return os.path.normcase(os.path.normpath(os.path.abspath(stripped)))


@dataclass(frozen=True)
class GateDecision:
    """Screening outcome for one path (never carries file content)."""

    allowed: bool
    reason: str


class PrivacyGate:
    """Allowlist + forbidden-file + redaction screen for live telemetry."""

    def __init__(self, allowed_roots: Sequence[str | Path]) -> None:
        roots = [_canonical(str(r)) for r in allowed_roots]
        roots = [r for r in roots if r]
        if not roots:
            raise ValueError(
                "allowed_roots: expected at least one allowlisted directory"
            )
        self._roots = tuple(roots)

    @property
    def allowed_roots(self) -> tuple[str, ...]:
        return self._roots

    # -- path screening -------------------------------------------------
    def is_within_allowlist(self, path: str | Path) -> bool:
        """True when *path* equals or sits under an allowlisted root."""
        candidate = _canonical(str(path))
        return any(
            candidate == root or candidate.startswith(root + os.sep)
            for root in self._roots
        )

    def is_forbidden_file(self, path: str | Path) -> bool:
        """True when the basename is secret-bearing material.

        Covers the task contract (``.env`` / ``.pem`` / ``.key`` /
        ``id_rsa``) plus the shared telemetry denylist
        (:data:`mimir.telemetry.SECRET_FILENAME_GLOBS`).
        """
        basename = os.path.basename(str(path).replace("\\", "/"))
        if any(
            fnmatch.fnmatchcase(basename, glob)
            for glob in SECRET_FILENAME_GLOBS
        ):
            return True
        lowered = basename.lower()
        _, dot, suffix = lowered.rpartition(".")
        if dot and ("." + suffix) in FORBIDDEN_EXTENSIONS:
            return True
        return lowered.startswith(FORBIDDEN_NAME_PREFIXES)

    def allows(self, path: str | Path) -> bool:
        """True only when allowlisted AND not a forbidden file."""
        return (
            self.is_within_allowlist(path)
            and not self.is_forbidden_file(path)
        )

    def decide(self, path: str | Path) -> GateDecision:
        """Explainable screening decision for one path."""
        if not self.is_within_allowlist(path):
            return GateDecision(False, "outside allowlisted roots")
        if self.is_forbidden_file(path):
            return GateDecision(False, "forbidden secret-bearing filename")
        return GateDecision(True, "allowed")

    # -- pre-publish redaction -------------------------------------------
    def redact(self, text: str) -> RedactionResult:
        """Redact secret patterns in *text* (delegates to telemetry)."""
        return redact_text(text)

    def redact_payload(
        self, payload: Any
    ) -> tuple[Any, tuple[str, ...], int]:
        """Deep-redact strings in a payload copy.

        Returns ``(redacted_copy, kinds, count)``. Dicts, lists, and
        tuples are rebuilt; every other value passes through
        untouched. The input is never mutated.
        """
        kinds: set[str] = set()
        count = 0

        def _walk(value: Any) -> Any:
            nonlocal count
            if isinstance(value, str):
                result = redact_text(value)
                kinds.update(result.kinds)
                count += result.count
                return result.text
            if isinstance(value, dict):
                return {key: _walk(item) for key, item in value.items()}
            if isinstance(value, list):
                return [_walk(item) for item in value]
            if isinstance(value, tuple):
                return tuple(_walk(item) for item in value)
            return value

        return _walk(payload), tuple(sorted(kinds)), count

    def gate_payload_for_path(
        self, path: str | Path, payload: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, GateDecision]:
        """Screen *path*, then redact *payload* pre-publish.

        Returns ``(None, decision)`` when the path is denied, else a
        redacted payload copy with an allow decision.
        """
        decision = self.decide(path)
        if not decision.allowed:
            return None, decision
        redacted, _, _ = self.redact_payload(payload)
        if not isinstance(redacted, dict):
            return dict(payload), decision
        return redacted, decision
