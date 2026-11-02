"""AST security sanitizer (MIMIR 3.0 Pillar 3).

Every synthesized change passes through :func:`check_source` before it
may touch the filesystem. Rejects fail-closed: syntax errors, raw
sockets, disk-wiping calls, process kills, shell execution, and dynamic
code evaluation. Ordinary application code (pathlib reads, requests,
logging, pytest) passes untouched.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

__all__ = ["SanitizerResult", "check_source"]

_MAX_SOURCE_CHARS = 200_000

#: Fully-qualified call targets that are always forbidden.
_FORBIDDEN_CALLS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "os.system",
        "os.popen",
        "os.spawnl",
        "os.spawnle",
        "os.spawnlp",
        "os.spawnlpe",
        "os.spawnv",
        "os.spawnve",
        "os.spawnvp",
        "os.spawnvpe",
        "os.execv",
        "os.execl",
        "os.execve",
        "os.execlp",
        "os.execvp",
        "os.execvpe",
        "os.kill",
        "os.killpg",
        "os.remove",
        "os.unlink",
        "os.rmdir",
        "os.removedirs",
        "shutil.rmtree",
        "socket.socket",
        "socket.create_connection",
        "socket.create_server",
        "socketserver.TCPServer",
        "socketserver.UDPServer",
        "pty.spawn",
    }
)

#: Modules whose direct import signals raw-socket or process tampering.
_FORBIDDEN_IMPORT_ROOTS = frozenset({"socket", "socketserver", "pty"})

#: Method names that wipe files when called on path-like objects.
_PATH_WIPE_METHODS = frozenset({"unlink", "rmdir"})
_PATHLIKE_HINTS = ("path", "file", "dir", "target", "root")


@dataclass(frozen=True)
class SanitizerResult:
    """Outcome of :func:`check_source`."""

    ok: bool
    violations: tuple[str, ...] = ()


def _dotted_name(node: ast.AST) -> str:
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    parts.reverse()
    return ".".join(parts)


class _GuardVisitor(ast.NodeVisitor):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: list[str] = []

    def _flag(self, lineno: int, message: str) -> None:
        self.violations.append(f"{self.filename}:{lineno}: {message}")

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in _FORBIDDEN_IMPORT_ROOTS:
                self._flag(
                    node.lineno, f"forbidden import '{alias.name}' (raw socket)"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        root = (node.module or "").split(".")[0]
        if root in _FORBIDDEN_IMPORT_ROOTS:
            self._flag(
                node.lineno,
                f"forbidden import 'from {node.module} import ...' (raw socket)",
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = _dotted_name(node.func)
        if name in _FORBIDDEN_CALLS:
            self._flag(node.lineno, f"forbidden call '{name}'")
        elif name.split(".")[-1] in _PATH_WIPE_METHODS and isinstance(
            node.func, ast.Attribute
        ):
            base = _dotted_name(node.func.value).lower()
            if any(hint in base for hint in _PATHLIKE_HINTS):
                self._flag(
                    node.lineno, f"forbidden disk-wipe call '{name}'"
                )
        if name.startswith("subprocess."):
            for keyword in node.keywords:
                if keyword.arg == "shell" and not (
                    isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is False
                ):
                    self._flag(
                        node.lineno,
                        f"forbidden '{name}' with shell=True (shell injection)",
                    )
        self.generic_visit(node)


def check_source(source: str, filename: str = "<proposal>") -> SanitizerResult:
    """Statically vet synthesized Python source.

    Returns ``ok=True`` with no violations when the code is safe to
    write; otherwise ``ok=False`` with one human-readable violation per
    line. Never raises for untrusted input shapes.
    """
    if not isinstance(source, str):
        return SanitizerResult(
            ok=False, violations=(f"{filename}:0: source must be text",)
        )
    if len(source) > _MAX_SOURCE_CHARS:
        return SanitizerResult(
            ok=False,
            violations=(
                f"{filename}:0: source exceeds "
                f"{_MAX_SOURCE_CHARS} chars; refusing blind write",
            ),
        )
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        line = exc.lineno or 0
        return SanitizerResult(
            ok=False,
            violations=(f"{filename}:{line}: syntax error: {exc.msg}",),
        )
    visitor = _GuardVisitor(filename)
    visitor.visit(tree)
    return SanitizerResult(
        ok=not visitor.violations, violations=tuple(visitor.violations)
    )
