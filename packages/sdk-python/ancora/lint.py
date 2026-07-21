"""Thin determinism lint for workflow code (RFC-0001a §1.5).

This is intentionally *not* a full static analyzer — the real guarantee is the
Temporal workflow sandbox plus mandatory replay tests. This is a fast, best-effort
warning that catches the obvious footguns (wall-clock reads, randomness, direct
I/O) in modules that define workflows. It never gates CI by default.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

# Dotted call names that are non-deterministic inside workflow code. We match on
# the *suffix* of the unparsed call target, so aliases like ``dt.datetime.now``
# and fully-qualified ``datetime.datetime.now`` are both caught.
_FORBIDDEN_CALL_SUFFIXES: tuple[str, ...] = (
    "datetime.now",
    "datetime.utcnow",
    "date.today",
    "time.time",
    "time.monotonic",
    "time.sleep",
    "uuid.uuid1",
    "uuid.uuid4",
    "os.urandom",
    "secrets.token_hex",
    "secrets.token_bytes",
)
_FORBIDDEN_CALL_PREFIXES: tuple[str, ...] = ("random.",)

# Import of these modules from workflow code is almost always a determinism bug
# (do the I/O in an activity instead).
_FORBIDDEN_IMPORTS: frozenset[str] = frozenset(
    {"requests", "httpx", "socket", "urllib.request", "aiohttp", "subprocess"}
)


@dataclass(frozen=True)
class LintIssue:
    filename: str
    line: int
    col: int
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.filename}:{self.line}:{self.col}: {self.code} {self.message}"


def _module_defines_workflow(tree: ast.Module) -> bool:
    """Heuristic: does this module contain a ``@workflow.defn`` class?"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "defn"
                    and _endswith_name(target, "workflow.defn")
                ):
                    return True
    return False


def _endswith_name(node: ast.AST, suffix: str) -> bool:
    try:
        unparsed = ast.unparse(node)
    except Exception:
        return False
    return unparsed == suffix or unparsed.endswith("." + suffix)


class _Visitor(ast.NodeVisitor):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.issues: list[LintIssue] = []

    def visit_Call(self, node: ast.Call) -> None:
        try:
            target = ast.unparse(node.func)
        except Exception:
            target = ""
        if any(target == s or target.endswith("." + s) for s in _FORBIDDEN_CALL_SUFFIXES) or any(
            target.startswith(p) or f".{p}" in target for p in _FORBIDDEN_CALL_PREFIXES
        ):
            self.issues.append(
                LintIssue(
                    self.filename,
                    node.lineno,
                    node.col_offset,
                    "AND001",
                    f"non-deterministic call `{target}()` in workflow code — move it into an activity",
                )
            )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in _FORBIDDEN_IMPORTS:
                self.issues.append(
                    LintIssue(
                        self.filename,
                        node.lineno,
                        node.col_offset,
                        "AND002",
                        f"import of `{alias.name}` in workflow code — do I/O in an activity",
                    )
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in _FORBIDDEN_IMPORTS:
            self.issues.append(
                LintIssue(
                    self.filename,
                    node.lineno,
                    node.col_offset,
                    "AND002",
                    f"import from `{node.module}` in workflow code — do I/O in an activity",
                )
            )
        self.generic_visit(node)


def check_source(source: str, filename: str = "<string>") -> list[LintIssue]:
    """Return determinism issues for a single source string.

    Only modules that actually define a workflow are checked; others return [].
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []
    if not _module_defines_workflow(tree):
        return []
    visitor = _Visitor(filename)
    visitor.visit(tree)
    return sorted(visitor.issues, key=lambda i: (i.line, i.col))


def check_paths(paths: list[str]) -> list[LintIssue]:
    """Recursively check ``.py`` files under the given paths."""
    issues: list[LintIssue] = []
    for raw in paths:
        p = Path(raw)
        files = p.rglob("*.py") if p.is_dir() else [p]
        for file in files:
            if file.suffix != ".py":
                continue
            issues.extend(check_source(file.read_text(encoding="utf-8"), str(file)))
    return issues
