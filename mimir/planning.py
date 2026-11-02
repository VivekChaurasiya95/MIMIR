"""Typed action planning with graduated autonomy (Phase 3).

The cognitive layer proposes; it never executes. This module turns a
free-form task plus the user's learned
:class:`~mimir.principles.BehavioralPrinciple` objects into a typed,
risk-scored :class:`ActionPlan`:

- :class:`ActionPlanner` retrieves relevant principles, infers a typed
  operation, drafts :class:`PlannedStep` items (each with a
  :class:`StepKind`), shell commands, and a diff preview skeleton.
- :class:`RiskClassifier` scores every plan LOW / MEDIUM / HIGH with
  human-readable reasons. High wins over medium over low; unknown input
  fails toward the safer (higher) reading, never toward silent autonomy.
- :func:`build_safety_checklist` renders the per-risk checklist the
  Action Proposal Card shows before approval.

Graduated autonomy is enforced through ``required_permissions`` (see
:func:`required_permissions_for_risk`):

- LOW plans need ``("voice-confirm",)`` — a spoken "go ahead" or one
  button click authorizes them.
- MEDIUM plans need ``("approve:medium",)`` and HIGH plans need
  ``("approve:high",)``, which the voice pipeline never grants (it only
  confers ``VOICE_GRANTED_PERMISSIONS``). Medium/high plans therefore
  fail closed to clarification when approved by voice and require the
  explicit Authorize & Execute gesture plus a confirmed safety
  checklist. Zero execution without approval: executors must observe
  the registry status (AUTHORIZED before EXECUTING) and the safety
  latch; this module's :meth:`ActionPlanner.propose` only records the
  proposal.

Qt-free and offline: planning is deterministic keyword/type logic with
an optional :class:`~mimir.store.PrincipleStore` for retrieval. No LLM
call, no network, no subprocess. Generated previews are redacted with
:func:`mimir.telemetry.redact_text` before they are stored on the plan.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Collection, Optional, Sequence

from ._common import (
    check_float,
    check_int,
    check_mapping,
    check_str,
    check_str_list,
)
from .actions import (
    VOICE_GRANTED_PERMISSIONS,
    ActionRegistry,
    ActionSpec,
)

__all__ = [
    "GUI_GRANTED_PERMISSIONS",
    "HIGH_RISK_PERMISSIONS",
    "MEDIUM_RISK_PERMISSIONS",
    "ActionPlan",
    "ActionPlanner",
    "PlannedStep",
    "PlanningError",
    "RiskAssessment",
    "RiskClassifier",
    "RiskLevel",
    "StepKind",
    "build_safety_checklist",
    "describe_autonomy",
    "required_permissions_for_risk",
]

#: Permissions the GUI Authorize & Execute gesture confers. A superset
#: of every risk tier, so an explicit, checklist-confirmed click can
#: authorize any plan while voice (``VOICE_GRANTED_PERMISSIONS`` only)
#: stays confined to low-risk work.
GUI_GRANTED_PERMISSIONS: tuple[str, ...] = (
    "voice-confirm",
    "approve:medium",
    "approve:high",
    "gui-confirm",
)

#: Permissions a MEDIUM-risk plan requires (voice cannot grant these).
MEDIUM_RISK_PERMISSIONS: tuple[str, ...] = ("approve:medium",)

#: Permissions a HIGH-risk plan requires (voice cannot grant these).
HIGH_RISK_PERMISSIONS: tuple[str, ...] = ("approve:high",)


class PlanningError(ValueError):
    """Raised for invalid planning input (empty tasks, bad tiers, ...)."""


class RiskLevel(str, Enum):
    """Graduated autonomy tier: low, medium, or high risk."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StepKind(str, Enum):
    """Typed planning vocabulary: what one step is allowed to do."""

    INSPECT = "inspect"
    EDIT = "edit"
    COMMAND = "command"
    ACTUATE = "actuate"
    VERIFY = "verify"
    ANNOUNCE = "announce"


def _coerce_risk(value: Any, *, field_name: str = "risk") -> RiskLevel:
    text = getattr(value, "value", value)
    text = str(text or "").strip().lower()
    for level in RiskLevel:
        if text == level.value:
            return level
    raise PlanningError(
        f"{field_name}: expected one of "
        f"{[level.value for level in RiskLevel]}, got {value!r}"
    )


def required_permissions_for_risk(level: Any) -> tuple[str, ...]:
    """Permissions a plan of ``level`` requires before authorization."""
    resolved = _coerce_risk(level)
    if resolved is RiskLevel.HIGH:
        return HIGH_RISK_PERMISSIONS
    if resolved is RiskLevel.MEDIUM:
        return MEDIUM_RISK_PERMISSIONS
    return VOICE_GRANTED_PERMISSIONS


def describe_autonomy(level: Any) -> str:
    """One-line approval rule for a risk tier (proposal-card note)."""
    resolved = _coerce_risk(level)
    if resolved is RiskLevel.HIGH:
        return (
            "High-risk: explicit Authorize & Execute plus a confirmed "
            "safety checklist. Never voice-approved; never runs "
            "without approval."
        )
    if resolved is RiskLevel.MEDIUM:
        return (
            "Medium-risk: explicit Authorize & Execute plus a confirmed "
            "safety checklist. Never runs without approval."
        )
    return (
        "Low-risk: one approval (voice or button). Read-only unless "
        "the commands below say otherwise."
    )


def _new_plan_id() -> str:
    return f"plan_{uuid.uuid4().hex[:8]}"


_HIGH_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+(-[a-z]*r[a-z]*\s+-?[a-z]*f|\S*-[a-z]*rf)", "destructive removal (rm -rf)"),
    (r"\bdel\s+/[sq]", "destructive removal (del /s)"),
    (r"\brd\s+/s", "destructive removal (rd /s)"),
    (r"\bformat\s+[a-z]:", "disk format command"),
    (r"\bmkfs\b", "filesystem creation (mkfs)"),
    (r"\bdd\s+if=", "raw disk write (dd)"),
    (r"reset\s+--hard", "git reset --hard (destroys work)"),
    (r"clean\s+-fd", "git clean -fd (deletes untracked files)"),
    (r"push\s+--force|force-?push|--force-with-lease", "force push (rewrites history)"),
    (r"\bshutdown\b|\breboot\b|\bhalt\b|\bpoweroff\b", "machine power operation"),
    (r"\bkill\s+-9\b|\btaskkill\b|\bpkill\b", "forced process kill"),
    (r"\bdrop\s+(table|database)\b", "database drop"),
    (r"\bsudo\b|\brunas\b", "privilege escalation (sudo/runas)"),
    (r"curl\b.*\|\s*(sh|bash|powershell)|wget\b.*\|\s*(sh|bash)", "piped remote-code execution"),
    (r"\bchmod\s+(-R\s+)?777\b", "chmod 777 (world-writable)"),
    (r":\(\)\s*\{\s*:\|:\s*&\s*\}", "fork bomb"),
)

_HIGH_OPERATIONS = frozenset({
    "git_push", "push", "deploy", "publish", "release",
    "delete", "drop", "remove", "uninstall", "shutdown", "reboot",
})

_MEDIUM_OPERATIONS = frozenset({
    "git_commit", "commit", "merge", "checkout", "pull",
    "run_tests", "test", "apply_change", "edit", "write",
    "drive_text", "type", "click", "actuate", "install",
})

_LOW_OPERATIONS = frozenset({
    "answer_question", "explain", "search", "query", "show",
    "list", "status", "observe", "plan_only", "summarize", "review",
    "assist",
})

_WRITE_VERBS = (
    "write", "edit", "create", "modify", "refactor", "apply",
    "commit", "merge", "move", "rename", "copy", "install",
    "update", "fix", "implement",
)

_SENSITIVE_BASENAMES = (
    ".env", ".npmrc", ".pypirc", "credentials", "secrets",
)

_SENSITIVE_SUFFIXES = (".pem", ".key", ".pfx", ".p12")

_SECRET_FALLBACK_RES = (
    re.compile(r"(api[_-]?key|secret|passwd|password|"
               r"auth[_-]?token|access[_-]?token)\s*[:=]",
               re.IGNORECASE),
    re.compile(r"ghp_[A-Za-z0-9]{8,}|gho_[A-Za-z0-9]{8,}|"
               r"AKIA[0-9A-Z]{16}|xox[bap]-[A-Za-z0-9-]+|"
               r"sk-[A-Za-z0-9]{8,}|"
               r"-----BEGIN .*PRIVATE KEY-----"),
)


def _redact_preview(text: str) -> tuple[str, tuple[str, ...]]:
    """Redact one generated preview; returns (text, kinds)."""
    if not text:
        return ("", ())
    try:
        from .telemetry import redact_text

        result = redact_text(text)
        return (result.text, tuple(result.kinds or ()))
    except Exception:
        return (text, ())


def _contains_secret(text: str) -> bool:
    """True when ``text`` looks secret-bearing (redactor + fallback)."""
    if not text:
        return False
    try:
        from .telemetry import redact_text

        if redact_text(text).count > 0:
            return True
    except Exception:
        pass
    return any(pattern.search(text) for pattern in _SECRET_FALLBACK_RES)


def _sensitive_name(path: str) -> str:
    """Basename when ``path`` names a sensitive file, else ``""``."""
    base = (path or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not base:
        return ""
    lowered = base.lower()
    if lowered in _SENSITIVE_BASENAMES or lowered.startswith(
        (".env.", "id_rsa", "id_ed25519", "credentials", "secrets")
    ):
        return base
    if lowered.endswith(_SENSITIVE_SUFFIXES):
        return base
    return ""


def _escapes_allowlist(path: str, allowed_roots: Sequence[str]) -> bool:
    """True when an absolute ``path`` sits outside ``allowed_roots``."""
    if not path or not allowed_roots:
        return False
    if isinstance(allowed_roots, str) or not isinstance(
        allowed_roots, (list, tuple)
    ):
        return False
    try:
        candidate = os.path.abspath(os.path.expanduser(path))
    except Exception:
        return False
    if not os.path.isabs(candidate):
        return False
    for root in allowed_roots:
        try:
            anchor = os.path.abspath(os.path.expanduser(str(root)))
        except Exception:
            continue
        try:
            if os.path.commonpath([anchor, candidate]) == anchor:
                return False
        except ValueError:
            continue
    return True


@dataclass(frozen=True)
class RiskAssessment:
    """A :class:`RiskLevel` verdict with human-readable reasons."""

    level: RiskLevel
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "level", _coerce_risk(self.level))
        object.__setattr__(
            self, "reasons", check_str_list(self.reasons, field="reasons")
        )

    @classmethod
    def from_dict(cls, data: Any) -> "RiskAssessment":
        check_mapping(
            data, cls="RiskAssessment", required=("level", "reasons")
        )
        return cls(level=data["level"], reasons=data["reasons"])

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level.value, "reasons": list(self.reasons)}


class RiskClassifier:
    """Deterministic LOW / MEDIUM / HIGH scoring for plans and actions.

    Static-only: :meth:`classify` inspects the operation, target,
    commands, and diff preview. High-risk signals win over medium ones;
    anything unrecognized stays at the tier its concrete signals earn
    (read-only plans score LOW, shell/file/desktop effects score at
    least MEDIUM).
    """

    def __init__(self) -> None:  # pragma: no cover - static-only guard
        raise TypeError("RiskClassifier is static-only; call classify()")

    @staticmethod
    def classify(
        *,
        operation: str = "",
        target: str = "",
        file_or_element: str = "",
        commands: Sequence[str] = (),
        diff_preview: str = "",
        task: str = "",
        allowed_roots: Sequence[str] = (),
    ) -> RiskAssessment:
        """Score one action; never raises on odd input shapes."""
        op = operation if isinstance(operation, str) else ""
        tgt = target if isinstance(target, str) else ""
        foe = file_or_element if isinstance(file_or_element, str) else ""
        tsk = task if isinstance(task, str) else ""
        diff = diff_preview if isinstance(diff_preview, str) else ""
        if isinstance(commands, str):
            commands = (commands,)
        if isinstance(allowed_roots, str):
            allowed_roots = (allowed_roots,)
        try:
            cmd_iter = tuple(commands or ())
        except TypeError:
            cmd_iter = ()
        cmds = [c for c in cmd_iter if isinstance(c, str)]
        lowered = " ".join([op, tsk, tgt, foe, diff, *cmds]).lower()
        op_key = op.strip().lower()

        high: list[str] = []
        for pattern, reason in _HIGH_PATTERNS:
            if re.search(pattern, lowered):
                high.append(reason)
        if op_key in _HIGH_OPERATIONS:
            high.append(f"high-impact operation ({op.strip() or op_key})")
        # Destructive verbs aimed at a concrete scope in the task text.
        if re.search(
            r"\b(delete|remove|uninstall|destroy|wipe|drop)\b", lowered
        ) and re.search(
            r"\b(files?|branch(es)?|repos?|packages?|package-lock|envs?|"
            r"environments?|databases?|tables?|everything|all)\b",
            lowered,
        ):
            high.append("destructive task scope (delete/remove/wipe)")
        sensitive = _sensitive_name(foe) or _sensitive_name(tgt)
        if sensitive:
            high.append(f"touches sensitive file ({sensitive})")
        secret_blob = "\n".join(cmds + [diff, tsk])
        if _contains_secret(secret_blob):
            high.append("payload holds secret-like text (redacted)")
        if _escapes_allowlist(foe, allowed_roots) or _escapes_allowlist(
            tgt, allowed_roots
        ):
            high.append("target escapes the allow-listed workspace")
        if high:
            return RiskAssessment(
                RiskLevel.HIGH, _dedup(high)[:8],
            )

        medium: list[str] = []
        if cmds:
            medium.append(f"runs {len(cmds)} shell command(s)")
        if diff.strip():
            medium.append("modifies files (see diff preview)")
        if op_key in _MEDIUM_OPERATIONS:
            medium.append(f"state-changing operation ({op.strip()})")
        if op_key in {
            "drive_text", "type", "click", "actuate", "move_cursor",
        }:
            medium.append("drives the desktop (cursor/keyboard)")
        if any(
            re.search(rf"\b{verb}\b", lowered) for verb in _WRITE_VERBS
        ):
            medium.append("changes files or repository state")
        if re.search(r"\b(pytest|npm test|unittest|run tests)\b", lowered):
            medium.append("executes project code (tests)")
        if medium:
            return RiskAssessment(RiskLevel.MEDIUM, _dedup(medium)[:8])
        if op_key and op_key not in _LOW_OPERATIONS:
            return RiskAssessment(
                RiskLevel.MEDIUM,
                (f"unrecognized operation ({op.strip()}); "
                 "review before approval",),
            )
        return RiskAssessment(RiskLevel.LOW, ("read-only plan",))


def _dedup(items: Sequence[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def build_safety_checklist(
    level: Any,
    *,
    has_commands: bool = False,
    has_diff: bool = False,
    redaction_kinds: Sequence[str] = (),
) -> tuple[str, ...]:
    """Per-risk safety checklist for the proposal card (deterministic)."""
    resolved = _coerce_risk(level)
    items = [
        "E-stop available — ESC or a mouse nudge stops execution instantly",
        "Approval binds to this action only (no blanket authorization)",
        "Target verified before execution (window, repo, or file)",
    ]
    if has_commands or has_diff:
        items.append("Diff preview and commands reviewed above")
    kinds = [k for k in (redaction_kinds or ()) if k]
    if kinds:
        items.append(
            "Secrets redacted from the preview "
            f"({', '.join(sorted(set(kinds)))})"
        )
    if resolved in (RiskLevel.MEDIUM, RiskLevel.HIGH):
        items.append("Scope limited to the allow-listed workspace")
    if resolved is RiskLevel.HIGH:
        items.append("Backup or git checkpoint exists before acting")
        items.append(
            "High-risk scope explicitly confirmed (no voice approval)"
        )
    return tuple(items)


@dataclass(frozen=True)
class PlannedStep:
    """One typed step of an :class:`ActionPlan`."""

    kind: StepKind
    title: str
    detail: str = ""
    command: str = ""
    target: str = ""

    def __post_init__(self) -> None:
        kind = getattr(self.kind, "value", self.kind)
        text = str(kind or "").strip().lower()
        resolved = None
        for member in StepKind:
            if text == member.value:
                resolved = member
                break
        if resolved is None:
            raise PlanningError(
                "kind: expected one of "
                f"{[member.value for member in StepKind]}, "
                f"got {self.kind!r}"
            )
        object.__setattr__(self, "kind", resolved)
        object.__setattr__(
            self, "title", check_str(self.title, field="title")
        )
        object.__setattr__(
            self,
            "detail",
            check_str(self.detail, field="detail", allow_empty=True),
        )
        object.__setattr__(
            self,
            "command",
            check_str(self.command, field="command", allow_empty=True),
        )
        object.__setattr__(
            self,
            "target",
            check_str(self.target, field="target", allow_empty=True),
        )

    @classmethod
    def from_dict(cls, data: Any) -> "PlannedStep":
        check_mapping(
            data,
            cls="PlannedStep",
            required=("kind", "title"),
            optional=("detail", "command", "target"),
        )
        return cls(
            kind=data["kind"],
            title=data["title"],
            detail=data.get("detail", ""),
            command=data.get("command", ""),
            target=data.get("target", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "title": self.title,
            "detail": self.detail,
            "command": self.command,
            "target": self.target,
        }


@dataclass(frozen=True)
class ActionPlan:
    """A typed, risk-scored proposal: principles in, approval out."""

    task: str
    operation: str
    steps: tuple[PlannedStep, ...]
    plan_id: str = field(default_factory=_new_plan_id)
    target: str = ""
    file_or_element: str = ""
    reason: str = ""
    supporting_observations: tuple[str, ...] = ()
    confidence: float = 0.5
    matched_principle_ids: tuple[str, ...] = ()
    matched_principle_names: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    diff_preview: str = ""
    risk: RiskLevel = RiskLevel.LOW
    risk_reasons: tuple[str, ...] = ()
    safety_checklist: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = VOICE_GRANTED_PERMISSIONS
    redaction_kinds: tuple[str, ...] = ()
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "task", check_str(self.task, field="task")
        )
        object.__setattr__(
            self, "operation", check_str(self.operation, field="operation")
        )
        if not isinstance(self.steps, (list, tuple)) or not self.steps:
            raise PlanningError("steps: expected a non-empty list")
        for index, step in enumerate(self.steps):
            if not isinstance(step, PlannedStep):
                raise PlanningError(
                    f"steps[{index}]: expected PlannedStep, "
                    f"got {type(step).__name__}"
                )
        object.__setattr__(self, "steps", tuple(self.steps))
        object.__setattr__(
            self,
            "plan_id",
            check_str(self.plan_id, field="plan_id") or _new_plan_id(),
        )
        for name in ("target", "file_or_element", "reason", "diff_preview"):
            object.__setattr__(
                self,
                name,
                check_str(
                    getattr(self, name), field=name, allow_empty=True
                ),
            )
        for name in (
            "supporting_observations",
            "matched_principle_ids",
            "matched_principle_names",
            "commands",
            "risk_reasons",
            "safety_checklist",
            "required_permissions",
            "redaction_kinds",
        ):
            object.__setattr__(
                self,
                name,
                check_str_list(getattr(self, name), field=name),
            )
        if len(self.matched_principle_ids) != len(
            self.matched_principle_names
        ):
            raise PlanningError(
                "matched_principle_ids/names: expected aligned lists"
            )
        confidence = check_float(self.confidence, field="confidence")
        if not 0.0 < confidence <= 1.0:
            raise PlanningError(
                f"confidence: expected (0, 1], got {self.confidence!r}"
            )
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "risk", _coerce_risk(self.risk))
        created = check_float(
            self.created_at, field="created_at", maximum=float("inf")
        )
        object.__setattr__(self, "created_at", created)

    @classmethod
    def from_dict(cls, data: Any) -> "ActionPlan":
        check_mapping(
            data,
            cls="ActionPlan",
            required=("task", "operation", "steps"),
            optional=(
                "plan_id", "target", "file_or_element", "reason",
                "supporting_observations", "confidence",
                "matched_principle_ids", "matched_principle_names",
                "commands", "diff_preview", "risk", "risk_reasons",
                "safety_checklist", "required_permissions",
                "redaction_kinds", "created_at",
            ),
        )
        steps = data["steps"]
        if not isinstance(steps, (list, tuple)):
            raise PlanningError("steps: expected a list")
        return cls(
            task=data["task"],
            operation=data["operation"],
            steps=[PlannedStep.from_dict(s) for s in steps],
            plan_id=data.get("plan_id", _new_plan_id()),
            target=data.get("target", ""),
            file_or_element=data.get("file_or_element", ""),
            reason=data.get("reason", ""),
            supporting_observations=data.get(
                "supporting_observations", ()),
            confidence=data.get("confidence", 0.5),
            matched_principle_ids=data.get(
                "matched_principle_ids", ()),
            matched_principle_names=data.get(
                "matched_principle_names", ()),
            commands=data.get("commands", ()),
            diff_preview=data.get("diff_preview", ""),
            risk=data.get("risk", "low"),
            risk_reasons=data.get("risk_reasons", ()),
            safety_checklist=data.get("safety_checklist", ()),
            required_permissions=data.get(
                "required_permissions", VOICE_GRANTED_PERMISSIONS),
            redaction_kinds=data.get("redaction_kinds", ()),
            created_at=data.get("created_at", time.time()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task": self.task,
            "operation": self.operation,
            "target": self.target,
            "file_or_element": self.file_or_element,
            "reason": self.reason,
            "supporting_observations": list(self.supporting_observations),
            "confidence": self.confidence,
            "matched_principle_ids": list(self.matched_principle_ids),
            "matched_principle_names": list(self.matched_principle_names),
            "steps": [s.to_dict() for s in self.steps],
            "commands": list(self.commands),
            "diff_preview": self.diff_preview,
            "risk": self.risk.value,
            "risk_reasons": list(self.risk_reasons),
            "safety_checklist": list(self.safety_checklist),
            "required_permissions": list(self.required_permissions),
            "redaction_kinds": list(self.redaction_kinds),
            "created_at": self.created_at,
        }


def _task_kind(task: str) -> str:
    try:
        from .foundation import classify_task_kind

        return str(classify_task_kind(task) or "other")
    except Exception:
        return "other"


def _infer_operation(task: str, kind: str) -> str:
    lowered = task.lower()
    if re.search(r"\bdeploy\b|\brelease\b", lowered):
        return "deploy"
    if re.search(r"\bpush\b|\bpublish\b", lowered):
        return "git_push"
    if re.search(r"\bcommit\b", lowered):
        return "git_commit"
    if re.search(r"\b(pytest|unittest|run tests|run the tests)\b", lowered):
        return "run_tests"
    if re.search(
        r"\b(delete|remove|uninstall|destroy|wipe|drop)\b", lowered
    ) and re.search(
        r"\b(files?|branch(es)?|repos?|packages?|envs?|environments?|"
        r"databases?|tables?|everything|all)\b",
        lowered,
    ):
        return "delete"
    if re.search(r"\bdrive\b", lowered):
        return "drive_text"
    if kind == "question":
        return "answer_question"
    if kind == "plan":
        return "plan_only"
    if kind in ("build", "refactor") or re.search(
        r"\b(fix|implement|add|create|update|edit|refactor|change|"
        r"write|type|click)\b",
        lowered,
    ):
        return "apply_change"
    return "assist"


def _commands_for(
    operation: str, task: str, target_language: Optional[str]
) -> tuple[str, ...]:
    if operation == "git_push":
        return ("git status --porcelain", "git push")
    if operation == "git_commit":
        # The message is authored at approval time; only the state
        # probe is prefilled so the plan never invents commit text.
        return ("git status --porcelain",)
    if operation == "run_tests":
        language = (target_language or "").strip().lower()
        if language in ("javascript", "typescript", "js", "ts"):
            return ("npm test",)
        return ("pytest -q",)
    # High-impact and actuation operations carry no prefilled
    # commands: the human authors the exact text via Modify.
    return ()


def _diff_preview_for(
    operation: str, file_or_element: str, task: str
) -> str:
    if operation not in ("apply_change", "drive_text", "edit"):
        return ""
    if not file_or_element.strip():
        return ("(no target file yet — set one via Modify, or name it "
                "in the task)")
    base = file_or_element.replace("\\", "/").rsplit("/", 1)[-1]
    hint = " ".join(task.split())[:120]
    return "\n".join([
        f"--- a/{base}",
        f"+++ b/{base}",
        "@@ planned change (preview only — not applied) @@",
        f"# {hint}",
        "+<planned insertion following your matched principles>",
    ])


def _steps_for(
    kind: str,
    operation: str,
    task: str,
    target: str,
    file_or_element: str,
    principle_ids: Sequence[str],
    commands: Sequence[str],
) -> list[PlannedStep]:
    guidance = (
        f"Guided by: {', '.join(principle_ids)}."
        if principle_ids
        else "No principles matched — generic plan."
    )
    where = file_or_element or target or "the active target"
    if operation == "git_push":
        return [
            PlannedStep(
                StepKind.INSPECT, "Verify repository state",
                detail="git status --porcelain; confirm branch and "
                       "remote before pushing.",
            ),
            PlannedStep(
                StepKind.COMMAND, "Push to the remote",
                detail="Requires explicit approval; never voice-approved.",
                command="git push", target=target,
            ),
            PlannedStep(
                StepKind.VERIFY, "Confirm the remote updated",
                detail="Compare local and remote heads after the push.",
            ),
        ]
    if operation == "git_commit":
        return [
            PlannedStep(
                StepKind.INSPECT, "Review the changes to commit",
                detail="git status --porcelain plus diff review.",
            ),
            PlannedStep(
                StepKind.ANNOUNCE, "Author the commit message",
                detail="The exact message is written at approval time — "
                       "this plan never invents it.",
            ),
            PlannedStep(
                StepKind.VERIFY, "Confirm a clean tree",
                detail="Re-run git status after committing.",
            ),
        ]
    if operation == "run_tests":
        command = next(iter(commands), "")
        return [
            PlannedStep(
                StepKind.COMMAND, "Run the test suite",
                detail="Executes project code; needs approval.",
                command=command,
            ),
            PlannedStep(
                StepKind.VERIFY, "Parse the results",
                detail="Failures map back to fixes against your "
                       "principles.",
            ),
        ]
    if operation == "delete":
        return [
            PlannedStep(
                StepKind.INSPECT, "Confirm the destructive scope",
                detail=f"Scope: {where}. Backup or git checkpoint "
                       "required before acting.",
            ),
            PlannedStep(
                StepKind.ANNOUNCE, "Await explicit authorization",
                detail="High-risk: no prefilled commands; the exact "
                       "text is authored via Modify.",
            ),
            PlannedStep(
                StepKind.VERIFY, "Verify the outcome",
                detail="Confirm only the intended scope changed.",
            ),
        ]
    if operation == "deploy":
        return [
            PlannedStep(
                StepKind.INSPECT, "Confirm the release scope",
                detail=f"Target: {where}. Release notes and rollback "
                       "plan ready.",
            ),
            PlannedStep(
                StepKind.ANNOUNCE, "Await explicit authorization",
                detail="High-risk: no prefilled commands; the exact "
                       "release steps are authored via Modify.",
            ),
            PlannedStep(
                StepKind.VERIFY, "Verify the deployment",
                detail="Health checks pass on the new release.",
            ),
        ]
    if operation == "drive_text":
        return [
            PlannedStep(
                StepKind.INSPECT, "Resolve and validate the target",
                detail=f"Target window: {where}. Foreground ownership "
                       "is verified before any input lands.",
            ),
            PlannedStep(
                StepKind.ACTUATE, "Glide and type with human cadence",
                detail="ESC or a mouse nudge stops instantly. "
                       f"{guidance}",
                target=where,
            ),
            PlannedStep(
                StepKind.VERIFY, "Confirm the typed outcome",
                detail="The destination shows the intended text.",
            ),
        ]
    if operation == "answer_question":
        return [
            PlannedStep(
                StepKind.INSPECT, "Recall relevant memory",
                detail=guidance,
            ),
            PlannedStep(
                StepKind.ANNOUNCE, "Answer in your own terms",
                detail="Grounded in the invariants above; no file or "
                       "desktop effects.",
            ),
        ]
    if operation == "plan_only":
        return [
            PlannedStep(
                StepKind.INSPECT, "Break the goal into milestones",
                detail=f"Goal: {' '.join(task.split())[:120]}",
            ),
            PlannedStep(
                StepKind.ANNOUNCE, "Present the plan for approval",
                detail="Nothing is executed from a plan-only proposal.",
            ),
        ]
    if operation == "apply_change":
        return [
            PlannedStep(
                StepKind.INSPECT, "Locate the code under change",
                detail=f"Target: {where}. {guidance}",
            ),
            PlannedStep(
                StepKind.EDIT, "Draft the change per your invariants",
                detail="Small steps; behavior-preserving unless the "
                       "task says otherwise.",
                target=file_or_element,
            ),
            PlannedStep(
                StepKind.VERIFY, "Tests and diff review",
                detail="Re-run tests and review against the forbidden "
                       "anti-patterns.",
            ),
        ]
    return [
        PlannedStep(
            StepKind.INSPECT, "Clarify the smallest done-state",
            detail=f"Task: {' '.join(task.split())[:140]} {guidance}",
        ),
        PlannedStep(
            StepKind.ANNOUNCE, "Propose the next step",
            detail="General assistance proposes; you approve before "
                   "anything runs.",
        ),
        PlannedStep(
            StepKind.VERIFY, "Confirm with you before acting",
            detail="No side effects leave this plan without approval.",
        ),
    ]


class ActionPlanner:
    """Drafts typed, risk-scored :class:`ActionPlan` objects.

    :param store: optional :class:`~mimir.store.PrincipleStore` used to
        retrieve the principles each plan is grounded in. ``None``
        yields honest generic plans (empty match lists).
    :param allowed_roots: workspace allow-list forwarded to the risk
        classifier; absolute targets outside it score HIGH.
    """

    def __init__(
        self,
        store: Any = None,
        *,
        allowed_roots: Sequence[str] = (),
    ) -> None:
        if store is not None:
            try:
                from .store import PrincipleStore

                if not isinstance(store, PrincipleStore):
                    raise PlanningError(
                        "store: expected PrincipleStore or None, "
                        f"got {type(store).__name__}"
                    )
            except PlanningError:
                raise
            except Exception as exc:
                raise PlanningError(f"store: unusable ({exc})") from None
        if not isinstance(allowed_roots, (list, tuple)):
            raise PlanningError(
                "allowed_roots: expected a list of strings, "
                f"got {type(allowed_roots).__name__}"
            )
        self._store = store
        self._allowed_roots = tuple(str(r) for r in allowed_roots)

    @property
    def allowed_roots(self) -> tuple[str, ...]:
        return self._allowed_roots

    def _retrieve(
        self, task: str, *, min_confidence: float, top_k: int
    ) -> list[Any]:
        if self._store is None:
            return []
        try:
            found = self._store.query_relevant(
                task, top_k=top_k, min_confidence=min_confidence
            )
        except Exception:
            return []
        return [p for p in (found or []) if hasattr(p, "id")]

    def plan(
        self,
        task: str,
        *,
        operation: Optional[str] = None,
        target: str = "",
        file_or_element: str = "",
        target_language: Optional[str] = None,
        target_scope: Optional[str] = None,
        min_confidence: float = 0.0,
        top_k: int = 5,
        supporting_observations: Collection[str] = (),
        diff_preview: Optional[str] = None,
        commands: Optional[Sequence[str]] = None,
    ) -> ActionPlan:
        """Draft one plan for ``task`` (pure: never executes)."""
        cleaned = check_str(task, field="task")
        top_k = check_int(top_k, field="top_k", minimum=1)
        min_confidence = check_float(
            min_confidence, field="min_confidence"
        )
        if operation is not None:
            operation = check_str(operation, field="operation")
        target = check_str(target, field="target", allow_empty=True)
        file_or_element = check_str(
            file_or_element, field="file_or_element", allow_empty=True
        )
        scope = ""
        if target_scope is not None:
            scope = check_str(
                target_scope, field="target_scope", allow_empty=True
            )
        if not target:
            target = file_or_element or scope

        principles = self._retrieve(
            cleaned, min_confidence=min_confidence, top_k=top_k
        )
        kind = _task_kind(cleaned)
        resolved_op = operation or _infer_operation(cleaned, kind)
        if commands is None:
            cmd_list = _commands_for(
                resolved_op, cleaned, target_language
            )
        else:
            cmd_list = tuple(str(c) for c in commands)
        commands = cmd_list
        if diff_preview is None:
            raw_preview = _diff_preview_for(
                resolved_op, file_or_element, cleaned
            )
        else:
            raw_preview = check_str(
                diff_preview, field="diff_preview", allow_empty=True
            )

        redaction_kinds: list[str] = []
        clean_commands: list[str] = []
        for command in commands:
            redacted, kinds = _redact_preview(command)
            clean_commands.append(redacted)
            redaction_kinds.extend(kinds)
        preview, kinds = _redact_preview(raw_preview)
        redaction_kinds.extend(kinds)

        assessment = RiskClassifier.classify(
            operation=resolved_op,
            target=target,
            file_or_element=file_or_element,
            commands=tuple(clean_commands),
            diff_preview=preview,
            task=cleaned,
            allowed_roots=self._allowed_roots,
        )
        checklist = build_safety_checklist(
            assessment.level,
            has_commands=bool(clean_commands),
            has_diff=bool(preview.strip()),
            redaction_kinds=redaction_kinds,
        )
        matched_ids = [str(p.id) for p in principles]
        matched_names = [str(getattr(p, "name", p.id)) for p in principles]
        if principles:
            scores = [
                float(getattr(p.epistemic_status, "confidence_score", 0.5))
                for p in principles
                if hasattr(p, "epistemic_status")
            ]
            confidence = sum(scores) / len(scores) if scores else 0.5
            confidence = min(0.95, max(0.05, confidence))
            reason = (
                f"Planned {resolved_op} from {len(principles)} matched "
                f"principle(s): {', '.join(matched_ids)}."
            )
        else:
            confidence = 0.5
            reason = (
                f"Planned {resolved_op} with no matched principles — "
                "generic plan. Observe a repo and press Learn so "
                "future plans carry your style."
            )
        steps = _steps_for(
            kind, resolved_op, cleaned, target, file_or_element,
            matched_ids, clean_commands,
        )
        return ActionPlan(
            task=cleaned,
            operation=resolved_op,
            steps=steps,
            target=target,
            file_or_element=file_or_element,
            reason=reason,
            supporting_observations=tuple(
                str(o) for o in (supporting_observations or ())
            ),
            confidence=confidence,
            matched_principle_ids=tuple(matched_ids),
            matched_principle_names=tuple(matched_names),
            commands=tuple(clean_commands),
            diff_preview=preview,
            risk=assessment.level,
            risk_reasons=assessment.reasons,
            safety_checklist=checklist,
            required_permissions=required_permissions_for_risk(
                assessment.level
            ),
            redaction_kinds=tuple(sorted(set(redaction_kinds))),
        )

    @staticmethod
    def propose(
        plan: "ActionPlan",
        registry: ActionRegistry,
        *,
        ttl_s: Optional[float] = None,
        expires_at: Optional[float] = None,
        action_id: Optional[str] = None,
        correlation_id: str = "",
        session_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> ActionSpec:
        """Record ``plan`` in ``registry``; emits ``ACTION_PROPOSED``.

        Static so hosts holding only a plan (no planner) can propose it.
        """
        if not isinstance(plan, ActionPlan):
            raise PlanningError(
                "plan: expected ActionPlan, "
                f"got {type(plan).__name__}"
            )
        if not isinstance(registry, ActionRegistry):
            raise PlanningError(
                "registry: expected ActionRegistry, "
                f"got {type(registry).__name__}"
            )
        return registry.propose(
            plan.operation,
            target=plan.target,
            file_or_element=plan.file_or_element,
            reason=plan.reason or plan.task[:200],
            supporting_observations=plan.supporting_observations,
            confidence=plan.confidence,
            required_permissions=plan.required_permissions,
            risk=plan.risk.value,
            plan_id=plan.plan_id,
            commands=plan.commands,
            diff_preview=plan.diff_preview,
            matched_principle_ids=plan.matched_principle_ids,
            safety_checklist=plan.safety_checklist,
            ttl_s=ttl_s,
            expires_at=expires_at,
            action_id=action_id,
            correlation_id=correlation_id,
            session_id=session_id,
            now=now,
        )
