"""Fast telemetry bridge: observe real behavior from whitelisted git history.

MVP Day 2. Reads commits from an explicitly allow-listed repository and emits
:class:`~mimir.episodes.BehavioralEpisode` objects carrying real before/after
transition pairs.

Privacy is enforced at ingestion (README section 6):

- paths outside ``allowed_roots`` are refused with :class:`TelemetryBoundaryError`
- secret-bearing filenames (``.env``, key material, ...) are skipped: only
  file metadata is retained, never content
- secret patterns inside diffs, messages, and author names are redacted
- author emails are never collected (author name only)

No redaction bookkeeping ever stores the original secret value, only the kind.
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .episodes import (
    BehavioralEpisode,
    EpisodeContext,
    EpisodeOutcome,
    FileTransition,
)

__all__ = [
    "GitState",
    "NotARepositoryError",
    "STATUS_LIST_CAP",
    "RedactionResult",
    "TelemetryBoundaryError",
    "TelemetryError",
    "observe_git_repo",
    "read_git_state",
    "redact_text",
    "resolve_repo",
]

_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x1f"
_LOG_FORMAT = f"%H{_FIELD_SEP}%aN{_FIELD_SEP}%aI{_FIELD_SEP}%s{_RECORD_SEP}"
_GIT_TIMEOUT_S = 120

#: Basename globs for files that are pure secret material. Matching files are
#: skipped entirely (metadata only, no content). Source files that merely
#: *mention* secrets keep their diffs but pass through pattern redaction.
SECRET_FILENAME_GLOBS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.pfx",
    "*.p12",
    "id_rsa*",
    ".npmrc",
    ".pypirc",
)

_TRUNCATION_MARKER = "\n...[TRUNCATED: diff exceeds max_diff_chars]\n"


@dataclass(frozen=True)
class _RedactionRule:
    kind: str
    pattern: re.Pattern
    replacement: str


_REDACTION_RULES = (
    _RedactionRule(
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
            r"[\s\S]*?"
            r"-----END [A-Z0-9 ]*PRIVATE KEY-----"
        ),
        "[REDACTED:private_key]",
    ),
    _RedactionRule(
        "aws_access_key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "[REDACTED:aws_access_key]",
    ),
    _RedactionRule(
        "github_token",
        re.compile(
            r"\b(ghp_[A-Za-z0-9]{10,}"
            r"|gho_[A-Za-z0-9]{10,}"
            r"|github_pat_[A-Za-z0-9_]{10,})\b"
        ),
        "[REDACTED:github_token]",
    ),
    _RedactionRule(
        "slack_token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
        "[REDACTED:slack_token]",
    ),
    _RedactionRule(
        "bearer_token",
        re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*"),
        "[REDACTED:bearer_token]",
    ),
    _RedactionRule(
        "assigned_secret",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|passwd|password|token)\b"
            r"(\s*[:=]\s*['\"]?)"
            r"([^'\"\s;#]+)"
        ),
        r"\1\2[REDACTED:assigned_secret]",
    ),
    _RedactionRule(
        "url_password",
        # Greedy through the last "@" so passwords containing "@" leak nothing.
        re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^/\s:@]+:)([^/\s]+@)"),
        r"\1[REDACTED:url_password]@",
    ),
)


@dataclass(frozen=True)
class RedactionResult:
    """Outcome of :func:`redact_text` (never contains the original secret)."""

    text: str
    kinds: tuple[str, ...]
    count: int


def redact_text(text: str) -> RedactionResult:
    """Redact secret patterns in *text*; privacy-first, may over-redact code."""
    kinds: list[str] = []
    count = 0
    redacted = text
    for rule in _REDACTION_RULES:
        redacted, substitutions = rule.pattern.subn(rule.replacement, redacted)
        if substitutions:
            kinds.append(rule.kind)
            count += substitutions
    return RedactionResult(text=redacted, kinds=tuple(kinds), count=count)


def _is_secret_filename(repo_relative_path: str) -> bool:
    basename = repo_relative_path.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatchcase(basename, glob) for glob in SECRET_FILENAME_GLOBS
    )


class TelemetryError(Exception):
    """Base error for telemetry observation failures."""


class TelemetryBoundaryError(TelemetryError, PermissionError):
    """Raised when a path falls outside the whitelisted observation boundary."""


class NotARepositoryError(TelemetryError, ValueError):
    """Raised when the observed path is not a (non-bare) git repository."""


def _run_git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        raise TelemetryError("git executable not found on PATH") from None
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        first_line = detail[0] if detail else f"exit {proc.returncode}"
        raise TelemetryError(f"git {' '.join(args)} failed: {first_line}")
    return proc.stdout


def _strip_extended_prefix(text: str) -> str:
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[len("\\\\?\\UNC\\"):]
    if text.startswith("\\\\?\\"):
        return text[len("\\\\?\\"):]
    return text


def _canonical_path(value: str | Path) -> Path:
    """Resolve a path identically on all platforms (no ``\\\\?\\`` prefix)."""
    return Path(_strip_extended_prefix(str(Path(_strip_extended_prefix(str(value))).resolve())))


def resolve_repo(
    repo_path: str | Path, allowed_roots: Sequence[str | Path]
) -> Path:
    """Resolve *repo_path* to its canonical top-level inside the whitelist.

    :raises TelemetryBoundaryError: repo is outside ``allowed_roots``.
    :raises NotARepositoryError: path is not a non-bare git repository.
    """
    try:
        toplevel = _run_git(
            Path(repo_path), "rev-parse", "--show-toplevel"
        ).strip()
    except TelemetryError as exc:
        raise NotARepositoryError(
            f"{repo_path}: not a git repository ({exc})"
        ) from None
    if not toplevel:
        raise NotARepositoryError(f"{repo_path}: not a git repository")
    canonical = _canonical_path(toplevel)
    roots = [_canonical_path(r) for r in allowed_roots]
    if not any(canonical == root or root in canonical.parents for root in roots):
        raise TelemetryBoundaryError(
            f"{canonical}: outside whitelisted observation roots "
            f"{[str(r) for r in roots]}"
        )
    return canonical


def _resolve_repo(repo_path: str | Path, allowed_roots: Sequence[str | Path]) -> Path:
    return resolve_repo(repo_path, allowed_roots)


def _parse_log(output: str) -> list[tuple[str, str, str, str]]:
    records = []
    for chunk in output.split(_RECORD_SEP):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        fields = chunk.split(_FIELD_SEP)
        if len(fields) != 4 or not fields[0]:
            raise TelemetryError(f"unparseable git log record: {chunk[:120]!r}")
        records.append((fields[0], fields[1], fields[2], fields[3]))
    return records


def _parse_numstat(output: str) -> dict[str, tuple[int, int]]:
    counts: dict[str, tuple[int, int]] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        try:
            counts[path] = (int(added), int(deleted))
        except ValueError:
            counts[path] = (0, 0)  # binary files report "-\t-"
    return counts


def _parse_b_path(header: str) -> str:
    """Extract the b-side path from a ``diff --git a/... b/...`` header."""
    body = header.strip()[len("diff --git "):]
    if body.startswith('"'):
        parts = re.findall(r'"((?:[^"\\]|\\.)*)"', body)
        if not parts:
            raise TelemetryError(f"unparseable diff header: {header[:120]!r}")
        raw = parts[1] if len(parts) > 1 else parts[0]
        raw = raw.replace('\\"', '"').replace("\\\\", "\\")
        return raw[2:] if raw.startswith("b/") else raw
    remainder = body[2:] if body.startswith("a/") else body
    _, _, b_path = remainder.partition(" b/")
    if not b_path:
        raise TelemetryError(f"unparseable diff header: {header[:120]!r}")
    return b_path


def _split_patch(patch: str) -> list[tuple[str, str]]:
    """Split ``git show`` output into (b_path, file_block) pairs."""
    blocks: list[tuple[str, str]] = []
    current_path: Optional[str] = None
    current_lines: list[str] = []
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current_path is not None:
                blocks.append((current_path, "".join(current_lines)))
            current_path = _parse_b_path(line)
            current_lines = [line]
        elif current_path is not None:
            current_lines.append(line)
    if current_path is not None:
        blocks.append((current_path, "".join(current_lines)))
    return blocks


def _file_status(block: str) -> str:
    for line in block.splitlines():
        if line.startswith("new file"):
            return "added"
        if line.startswith("deleted file"):
            return "deleted"
        if line.startswith("rename from"):
            return "renamed"
        if line.startswith("copy from"):
            return "copied"
        if line.startswith("--- ") or line.startswith("Binary files "):
            break
    return "modified"


_LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".sh": "shell",
    ".ps1": "powershell",
    ".sql": "sql",
    ".html": "web",
    ".css": "web",
    ".md": "markdown",
    ".rst": "markdown",
    ".json": "config",
    ".yaml": "config",
    ".yml": "config",
    ".toml": "config",
    ".ini": "config",
}


def _guess_language(paths: Sequence[str]) -> Optional[str]:
    """Heuristic: most common known language; None on ties or unknown."""
    votes = Counter(
        _LANGUAGE_BY_EXTENSION[suffix]
        for p in paths
        if (suffix := posixpath.splitext(p)[1].lower()) in _LANGUAGE_BY_EXTENSION
    )
    if not votes:
        return None
    top = votes.most_common(2)
    if len(top) > 1 and top[0][1] == top[1][1]:
        return None
    return top[0][0]


def _common_scope(paths: Sequence[str]) -> str:
    if not paths:
        return ""
    try:
        common = posixpath.commonpath(
            [p for p in (posixpath.dirname(p) for p in paths) if p] or ["."]
        )
    except ValueError:
        return ""
    return "" if common == "." else common


def _observe_commit(
    repo: Path,
    sha: str,
    author: str,
    author_date: str,
    subject: str,
    max_diff_chars: int,
    include_authors: bool,
) -> BehavioralEpisode:
    patch = _run_git(
        repo, "show", "--format=", "--no-ext-diff", "--unified=3", "--no-color",
        sha, "--",
    )
    numstat = _run_git(repo, "show", "--numstat", "--format=", sha, "--")
    counts = _parse_numstat(numstat)

    transitions: list[FileTransition] = []
    kinds: set[str] = set()
    redaction_count = 0
    for path, block in _split_patch(patch):
        status = _file_status(block)
        if _is_secret_filename(path):
            kinds.add("secret_filename")
            redaction_count += 1
            transitions.append(
                FileTransition(
                    path=path,
                    status=status,
                    diff="",
                    additions=0,
                    deletions=0,
                    skipped=True,
                    skip_reason="denylisted secret-bearing filename",
                )
            )
            continue
        binary = "Binary files " in block
        redacted = redact_text("" if binary else block)
        kinds.update(redacted.kinds)
        redaction_count += redacted.count
        diff = redacted.text
        truncated = False
        if len(diff) > max_diff_chars:
            diff = diff[:max_diff_chars] + _TRUNCATION_MARKER
            truncated = True
        additions, deletions = counts.get(path, (0, 0))
        transitions.append(
            FileTransition(
                path=path,
                status=status,
                diff=diff,
                additions=additions,
                deletions=deletions,
                truncated=truncated,
                binary=binary,
            )
        )

    subject_red = redact_text(subject)
    kinds.update(subject_red.kinds)
    redaction_count += subject_red.count
    if include_authors:
        author_red = redact_text(author)
        kinds.update(author_red.kinds)
        redaction_count += author_red.count
        actor = author_red.text
    else:
        actor = "[withheld:author]"

    paths = [t.path for t in transitions]
    return BehavioralEpisode(
        id=f"ep_git_{sha[:12]}",
        source="git",
        repo=str(repo),
        source_ref=sha,
        observed_at=author_date,
        actor=actor,
        summary=subject_red.text,
        context=EpisodeContext(scope=_common_scope(paths), language=_guess_language(paths)),
        transitions=transitions,
        outcome=EpisodeOutcome(),
        redactions_applied=redaction_count,
        redaction_kinds=sorted(kinds),
    )


def observe_git_repo(
    repo_path: str | Path,
    *,
    allowed_roots: Sequence[str | Path],
    max_commits: int = 50,
    max_diff_chars: int = 20000,
    include_authors: bool = True,
) -> list[BehavioralEpisode]:
    """Observe the newest ``max_commits`` (non-merge) commits as episodes.

    :param repo_path: path inside the repository to observe.
    :param allowed_roots: whitelist; the repo top-level must fall under one.
    :param max_commits: newest-first cap on commits to convert.
    :param max_diff_chars: per-file diff cap before truncation.
    :param include_authors: keep author names (redacted) or withhold them.
    :raises TelemetryBoundaryError: repo is outside ``allowed_roots``.
    :raises NotARepositoryError: path is not a non-bare git repository.
    """
    if type(max_commits) is not int or max_commits < 1:
        raise ValueError(f"max_commits: expected positive int, got {max_commits!r}")
    if type(max_diff_chars) is not int or max_diff_chars < 1:
        raise ValueError(
            f"max_diff_chars: expected positive int, got {max_diff_chars!r}"
        )
    repo = _resolve_repo(repo_path, allowed_roots)
    log_output = _run_git(
        repo, "log", "--no-merges", f"--format={_LOG_FORMAT}", "-n", str(max_commits)
    )
    return [
        _observe_commit(repo, sha, author, date, subject, max_diff_chars, include_authors)
        for sha, author, date, subject in _parse_log(log_output)
    ]


#: Cap on paths carried per status list (counts always report totals).
STATUS_LIST_CAP = 200


@dataclass(frozen=True)
class GitState:
    """Live working-tree snapshot: branch, HEAD, and changed paths.

    Paths only — never diff content — so uncommitted work stays visible
    without leaking file contents into the event stream.
    """

    repo: str
    branch: str
    head: str
    staged: tuple[str, ...] = ()
    unstaged: tuple[str, ...] = ()
    untracked: tuple[str, ...] = ()
    staged_count: int = 0
    unstaged_count: int = 0
    untracked_count: int = 0
    dirty: bool = False

    def signature(
        self,
    ) -> tuple[str, str, frozenset[str], frozenset[str], frozenset[str],
               int, int, int]:
        """Equality key for change detection (paths as sets)."""
        return (
            self.branch,
            self.head,
            frozenset(self.staged),
            frozenset(self.unstaged),
            frozenset(self.untracked),
            self.staged_count,
            self.unstaged_count,
            self.untracked_count,
        )


def _parse_porcelain_z(output: str) -> tuple[list[str], list[str], list[str]]:
    """Split ``status --porcelain=v1 -z`` into staged/unstaged/untracked."""
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    records = output.split("\x00")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        code, path = record[:2], record[3:]
        if code[0] in ("R", "C"):
            # Rename/copy records are followed by the target path.
            if index < len(records) and records[index]:
                path = records[index]
                index += 1
        if code == "??":
            untracked.append(path)
            continue
        if code[0] not in (" ", "?", "!"):
            staged.append(path)
        if code[1] not in (" ", "?", "!"):
            unstaged.append(path)
    return staged, unstaged, untracked


def read_git_state(
    repo_path: str | Path,
    *,
    allowed_roots: Sequence[str | Path],
) -> GitState:
    """Read the live repository state (branch, HEAD, working tree).

    Complements :func:`observe_git_repo` history observation with the
    uncommitted present. ``head`` is ``""`` for an unborn (commit-less)
    repository; ``branch`` is ``"HEAD"`` when detached.

    :raises TelemetryBoundaryError: repo is outside ``allowed_roots``.
    :raises NotARepositoryError: path is not a non-bare git repository.
    """
    repo = _resolve_repo(repo_path, allowed_roots)
    try:
        branch = _run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except TelemetryError:
        try:
            branch = _run_git(
                repo, "symbolic-ref", "--quiet", "--short", "HEAD"
            ).strip()
        except TelemetryError:
            branch = "HEAD"
    try:
        head = _run_git(repo, "rev-parse", "HEAD").strip()
    except TelemetryError:
        head = ""
    porcelain = _run_git(
        repo, "status", "--porcelain=v1", "-z", "--untracked-files=normal"
    )
    staged, unstaged, untracked = _parse_porcelain_z(porcelain)
    staged.sort()
    unstaged.sort()
    untracked.sort()
    return GitState(
        repo=str(repo),
        branch=branch or "HEAD",
        head=head,
        staged=tuple(staged[:STATUS_LIST_CAP]),
        unstaged=tuple(unstaged[:STATUS_LIST_CAP]),
        untracked=tuple(untracked[:STATUS_LIST_CAP]),
        staged_count=len(staged),
        unstaged_count=len(unstaged),
        untracked_count=len(untracked),
        dirty=bool(staged or unstaged or untracked),
    )
