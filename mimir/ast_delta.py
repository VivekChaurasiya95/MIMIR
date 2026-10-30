"""Incremental structural observation (plan section 3.1B).

Maintains a compact structural summary per monitored file and emits
``AST_DELTA`` runtime events carrying structural deltas — added,
removed, and modified functions, class changes, import changes —
rather than whole source files. Temporarily invalid code (the normal
state mid-edit) is reported as a syntax-validity transition, never a
crash.

Backends (plan section 9):

- Tree-sitter when it is importable (partial trees for invalid code).
- The stdlib :mod:`ast` module otherwise, so the core stays
  dependency-free and headless-testable.

Payloads never contain source text: signatures keep parameter *names*
only (default values and annotations are dropped, since defaults may
embed secrets), and body comparison uses hashes.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .events import Event, EventBus, EventType, get_event_bus
from .telemetry import SECRET_FILENAME_GLOBS

__all__ = [
    "SOURCE_EXTENSIONS",
    "AstDelta",
    "AstObserver",
    "ClassInfo",
    "FunctionSig",
    "SourceSummary",
    "diff_summaries",
    "summarize_python_source",
    "summarize_source",
]

#: Extensions eligible for structural observation (content is read only
#: for these; everything else is ignored by file-driven callers).
SOURCE_EXTENSIONS = frozenset({
    ".py", ".pyi",
    ".js", ".mjs", ".cjs", ".jsx",
    ".ts", ".tsx",
    ".java", ".go", ".rs", ".rb", ".php",
    ".cs", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp",
})

_LANGUAGE_BY_EXTENSION = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".java": "java", ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".php": "php", ".cs": "csharp", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
}

_MAX_FILE_BYTES = 1_000_000

_TREESITTER_SPEC = importlib.util.find_spec("tree_sitter")


def _body_hash(node: ast.AST) -> str:
    digest = hashlib.sha1(
        ast.dump(node, annotate_fields=True,
                 include_attributes=False).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def _param_names(args: ast.arguments) -> str:
    """Render parameter *names* only; defaults/annotations dropped."""
    names = [a.arg for a in list(args.posonlyargs) + list(args.args)]
    if args.vararg is not None:
        names.append("*" + args.vararg.arg)
    names.extend(a.arg for a in args.kwonlyargs)
    if args.kwarg is not None:
        names.append("**" + args.kwarg.arg)
    return "(" + ", ".join(names) + ")"


@dataclass(frozen=True)
class FunctionSig:
    """One function/method identity: names only, no source or values."""

    name: str
    qualified: str
    signature: str
    body_hash: str


@dataclass(frozen=True)
class ClassInfo:
    """One class identity: name plus member-method names."""

    name: str
    qualified: str
    methods: tuple[str, ...]


@dataclass(frozen=True)
class SourceSummary:
    """Compact structural snapshot of one file (no source text)."""

    path: str
    language: str
    functions: tuple[FunctionSig, ...] = ()
    classes: tuple[ClassInfo, ...] = ()
    imports: tuple[str, ...] = ()
    syntax_valid: bool = True
    syntax_error: str = ""
    backend: str = "ast"

    def function_map(self) -> dict[str, FunctionSig]:
        return {f.qualified: f for f in self.functions}

    def class_map(self) -> dict[str, ClassInfo]:
        return {c.qualified: c for c in self.classes}


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.functions: list[FunctionSig] = []
        self.classes: list[ClassInfo] = []
        self.imports: list[str] = []

    def _dotted(self, node: Optional[ast.expr]) -> str:
        parts: list[str] = []
        current: Optional[ast.expr] = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        parts.reverse()
        return ".".join(parts)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_function(node)
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_function(node)
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _record_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        qualified = ".".join([*self.stack, node.name])
        self.functions.append(FunctionSig(
            name=node.name,
            qualified=qualified,
            signature=_param_names(node.args),
            body_hash=_body_hash(node),
        ))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = ".".join([*self.stack, node.name])
        methods = tuple(
            item.name for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        self.classes.append(ClassInfo(node.name, qualified, methods))
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(alias.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.append(node.module.split(".")[0])


def _syntax_error_text(exc: SyntaxError) -> str:
    detail = exc.msg or "invalid syntax"
    if exc.lineno is not None:
        detail += f" (line {exc.lineno})"
    return detail


def summarize_python_source(path: str, text: str) -> SourceSummary:
    """Summarize Python source with stdlib :mod:`ast`.

    Invalid syntax yields ``syntax_valid=False`` with the parser
    message (no source); nothing is raised for malformed input.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return SourceSummary(
            path=path, language="python", syntax_valid=False,
            syntax_error=_syntax_error_text(exc), backend="ast",
        )
    except (ValueError, MemoryError, RecursionError) as exc:
        return SourceSummary(
            path=path, language="python", syntax_valid=False,
            syntax_error=f"unparseable: {exc}", backend="ast",
        )
    visitor = _PythonVisitor()
    visitor.visit(tree)
    return SourceSummary(
        path=path,
        language="python",
        functions=tuple(sorted(visitor.functions, key=lambda f: f.qualified)),
        classes=tuple(sorted(visitor.classes, key=lambda c: c.qualified)),
        imports=tuple(sorted(set(visitor.imports))),
        backend="ast",
    )


def _summarize_with_treesitter(
    path: str, text: str, language: str
) -> Optional[SourceSummary]:
    """Best-effort Tree-sitter summary; None when unavailable.

    Every import, version, and grammar mismatch degrades to None so
    callers always fall back to stdlib parsing. Only identifier names
    are collected — never source spans or literal values.
    """
    try:
        import tree_sitter  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        pack_name = "tree_sitter_" + language
        try:
            pack = __import__(pack_name)
        except ImportError:
            return None
        language_factory = getattr(pack, "language", None)
        if not callable(language_factory):
            return None
        grammar = language_factory()
        parser_cls = getattr(tree_sitter, "Parser", None)
        language_cls = getattr(tree_sitter, "Language", None)
        if parser_cls is None or language_cls is None:
            return None
        try:
            parser = parser_cls(language_cls(grammar))
        except Exception:
            parser = parser_cls()
            setter = getattr(parser, "set_language", None)
            if callable(setter):
                setter(language_cls(grammar))
            else:
                lang_attr = getattr(parser, "language", None)
                if lang_attr is None:
                    parser.language = grammar  # type: ignore[attr-defined]
        tree = parser.parse(text.encode("utf-8", "replace"))
        root = tree.root_node
        has_error = bool(getattr(root, "has_error", False))

        functions: list[FunctionSig] = []
        classes: list[ClassInfo] = []
        imports: set[str] = set()

        def node_text(node: Any) -> str:
            start = getattr(node, "start_byte", 0) or 0
            end = getattr(node, "end_byte", 0) or 0
            raw = text.encode("utf-8", "replace")[start:end]
            return raw.decode("utf-8", "replace")

        def identifiers(node: Any) -> list[str]:
            found: list[str] = []
            stack = [node]
            while stack:
                current = stack.pop()
                if getattr(current, "type", "") == "identifier":
                    name = node_text(current)
                    if name.isidentifier():
                        found.append(name)
                stack.extend(getattr(current, "children", []) or [])
            return found

        def body_hash(node: Any) -> str:
            return hashlib.sha1(
                getattr(node, "type", "").encode()
                + b"\x00"
                + str(getattr(node, "child_count", 0)).encode()
                + b"\x00"
                + node_text(node).encode("utf-8", "replace")
            ).hexdigest()[:16]

        def walk(node: Any, scope: list[str]) -> None:
            kind = getattr(node, "type", "")
            children = list(getattr(node, "children", []) or [])
            if kind in ("function_definition", "function_declaration",
                        "method_definition"):
                names = identifiers(children[1] if len(children) > 1
                                    else node)
                fname = names[0] if names else "<anonymous>"
                params = ""
                for child in children:
                    if "parameter" in getattr(child, "type", ""):
                        params = "(" + ", ".join(identifiers(child)) + ")"
                        break
                qualified = ".".join([*scope, fname])
                functions.append(FunctionSig(
                    fname, qualified, params, body_hash(node)))
                for child in children:
                    walk(child, [*scope, fname])
                return
            if kind in ("class_definition", "class_declaration"):
                names = identifiers(children[1] if len(children) > 1
                                    else node)
                cname = names[0] if names else "<anonymous>"
                qualified = ".".join([*scope, cname])
                methods = tuple(
                    m for child in children
                    for m in _method_names(child)
                )
                classes.append(ClassInfo(cname, qualified, methods))
                for child in children:
                    walk(child, [*scope, cname])
                return
            if kind in ("import_statement", "import_from_statement"):
                for name in identifiers(node):
                    imports.add(name.split(".")[0])
            for child in children:
                walk(child, scope)

        def _method_names(node: Any) -> list[str]:
            kind = getattr(node, "type", "")
            if kind in ("function_definition", "method_definition"):
                kids = list(getattr(node, "children", []) or [])
                names = identifiers(kids[1] if len(kids) > 1 else node)
                return [names[0]] if names else []
            found: list[str] = []
            for kid in list(getattr(node, "children", []) or []):
                found.extend(_method_names(kid))
            return found

        walk(root, [])
        return SourceSummary(
            path=path,
            language=language,
            functions=tuple(sorted(functions, key=lambda f: f.qualified)),
            classes=tuple(sorted(classes, key=lambda c: c.qualified)),
            imports=tuple(sorted(imports)),
            syntax_valid=not has_error,
            syntax_error="contains syntax errors" if has_error else "",
            backend="tree-sitter",
        )
    except Exception:
        return None


def summarize_source(
    path: str, text: str, language: Optional[str] = None,
    *, backend: str = "auto",
) -> SourceSummary:
    """Summarize *text* with the best available backend.

    ``backend`` is ``"auto"`` (Tree-sitter when importable, else
    stdlib), ``"tree-sitter"``, or ``"ast"``. Unsupported languages
    yield an empty, explicitly ``backend="unsupported"`` summary.
    """
    if backend not in ("auto", "tree-sitter", "ast"):
        raise ValueError(
            f"backend: expected auto/tree-sitter/ast, got {backend!r}"
        )
    suffix = os.path.splitext(path)[1].lower()
    resolved = language or _LANGUAGE_BY_EXTENSION.get(suffix, "unknown")
    if resolved == "unknown":
        return SourceSummary(path=path, language="unknown",
                             backend="unsupported")
    if backend in ("auto", "tree-sitter") and _TREESITTER_SPEC is not None:
        summary = _summarize_with_treesitter(path, text, resolved)
        if summary is not None:
            return summary
        if backend == "tree-sitter":
            return SourceSummary(path=path, language=resolved,
                                 backend="unsupported")
    if resolved == "python":
        return summarize_python_source(path, text)
    return SourceSummary(path=path, language=resolved,
                         backend="unsupported")


@dataclass(frozen=True)
class AstDelta:
    """Structural change between two summaries (names only)."""

    path: str
    language: str
    added_functions: tuple[str, ...] = ()
    removed_functions: tuple[str, ...] = ()
    modified_functions: tuple[str, ...] = ()
    signature_changes: tuple[str, ...] = ()
    added_classes: tuple[str, ...] = ()
    removed_classes: tuple[str, ...] = ()
    modified_classes: tuple[str, ...] = ()
    added_imports: tuple[str, ...] = ()
    removed_imports: tuple[str, ...] = ()
    syntax_valid: bool = True
    syntax_error: str = ""
    syntax_changed: bool = False

    def is_empty(self) -> bool:
        return not (
            self.added_functions or self.removed_functions
            or self.modified_functions or self.added_classes
            or self.removed_classes or self.modified_classes
            or self.added_imports or self.removed_imports
            or self.syntax_changed
        )


def diff_summaries(old: SourceSummary, new: SourceSummary) -> AstDelta:
    """Pure structural diff between two summaries of one path."""
    old_f, new_f = old.function_map(), new.function_map()
    old_names, new_names = set(old_f), set(new_f)
    added = tuple(sorted(new_names - old_names))
    removed = tuple(sorted(old_names - new_names))
    modified: list[str] = []
    signatures: list[str] = []
    for name in sorted(old_names & new_names):
        before, after = old_f[name], new_f[name]
        if before.body_hash != after.body_hash:
            modified.append(name)
            if before.signature != after.signature:
                signatures.append(name)
    old_c, new_c = old.class_map(), new.class_map()
    old_cn, new_cn = set(old_c), set(new_c)
    modified_c = tuple(sorted(
        n for n in old_cn & new_cn
        if old_c[n].methods != new_c[n].methods
    ))
    old_i, new_i = set(old.imports), set(new.imports)
    syntax_changed = (
        old.syntax_valid != new.syntax_valid
        or old.syntax_error != new.syntax_error
    )
    return AstDelta(
        path=new.path,
        language=new.language,
        added_functions=added,
        removed_functions=removed,
        modified_functions=tuple(modified),
        signature_changes=tuple(signatures),
        added_classes=tuple(sorted(new_cn - old_cn)),
        removed_classes=tuple(sorted(old_cn - new_cn)),
        modified_classes=modified_c,
        added_imports=tuple(sorted(new_i - old_i)),
        removed_imports=tuple(sorted(old_i - new_i)),
        syntax_valid=new.syntax_valid,
        syntax_error=new.syntax_error,
        syntax_changed=syntax_changed,
    )


def _is_secret_filename(path: str) -> bool:
    import fnmatch

    basename = path.replace("\\", "/").rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatchcase(basename, glob) for glob in SECRET_FILENAME_GLOBS
    )


class AstObserver:
    """Incremental per-file structural observer publishing AST_DELTA.

    The first observation of a path establishes a silent baseline;
    later observations emit only on real change. Secret-bearing
    filenames are never read (metadata-only, per telemetry policy).
    """

    def __init__(
        self,
        *,
        bus: Optional[EventBus] = None,
        session_id: str = "",
        backend: str = "auto",
        max_bytes: int = _MAX_FILE_BYTES,
    ) -> None:
        if max_bytes < 1:
            raise ValueError(
                f"max_bytes: expected positive int, got {max_bytes!r}"
            )
        self.bus = bus
        self.session_id = session_id
        self.backend = backend
        self.max_bytes = max_bytes
        self._summaries: dict[str, SourceSummary] = {}
        self._lock = threading.Lock()

    def observe(
        self, path: str, text: str, language: Optional[str] = None
    ) -> Optional[AstDelta]:
        """Observe new *text* for *path*; delta or None on no change."""
        key = os.path.normpath(path)
        summary = summarize_source(key, text, language,
                                   backend=self.backend)
        with self._lock:
            previous = self._summaries.get(key)
            self._summaries[key] = summary
        if previous is None:
            return None
        delta = diff_summaries(previous, summary)
        if delta.is_empty():
            return None
        self._publish(delta)
        return delta

    def observe_file(self, path: str) -> Optional[AstDelta]:
        """Read + observe a file (size-capped, secret-safe, text-only).

        Returns None for unreadable files, oversized files, secret
        filenames, and non-source extensions — all without raising.
        """
        key = os.path.normpath(path)
        if _is_secret_filename(key):
            return None
        if os.path.splitext(key)[1].lower() not in SOURCE_EXTENSIONS:
            return None
        try:
            if os.path.getsize(key) > self.max_bytes:
                return None
            with open(key, "rb") as handle:
                raw = handle.read(self.max_bytes + 1)
        except OSError:
            return None
        if len(raw) > self.max_bytes or b"\x00" in raw:
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return self.observe(key, text)

    def forget(self, path: str) -> None:
        """Drop baseline state for *path* (call on deletion)."""
        with self._lock:
            self._summaries.pop(os.path.normpath(path), None)

    def tracked_paths(self) -> list[str]:
        with self._lock:
            return sorted(self._summaries)

    def reset(self) -> None:
        with self._lock:
            self._summaries.clear()

    def _publish(self, delta: AstDelta) -> Optional[Event]:
        bus = self.bus if self.bus is not None else get_event_bus()
        try:
            return bus.emit(
                EventType.AST_DELTA,
                "sensory.ast_observer",
                {
                    "file": delta.path,
                    "language": delta.language,
                    "added_functions": list(delta.added_functions),
                    "modified_functions": list(delta.modified_functions),
                    "removed_functions": list(delta.removed_functions),
                    "signature_changes": list(delta.signature_changes),
                    "added_classes": list(delta.added_classes),
                    "removed_classes": list(delta.removed_classes),
                    "modified_classes": list(delta.modified_classes),
                    "added_imports": list(delta.added_imports),
                    "removed_imports": list(delta.removed_imports),
                    "syntax_valid": delta.syntax_valid,
                    "syntax_error": delta.syntax_error,
                    "observed_at": time.time(),
                },
                session_id=self.session_id,
            )
        except Exception:
            return None
