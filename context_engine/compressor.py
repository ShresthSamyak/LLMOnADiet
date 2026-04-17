"""Compress code snippets to preserve debugging signal while dropping noise."""

from __future__ import annotations

import ast
import textwrap
from typing import Iterable

# Functions/attributes that signal a debug/log call we want to drop.
_PRINT_NAMES: frozenset[str] = frozenset({"print", "pprint", "pp"})
_LOG_ATTRS: frozenset[str] = frozenset({
    "debug", "info", "warning", "warn", "error", "exception", "log", "critical",
})


# ---------------------------------------------------------------------------
# AST predicates
# ---------------------------------------------------------------------------

def _is_debug_log(stmt: ast.stmt) -> bool:
    """Return True if *stmt* is a bare print/logging call at statement level."""
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return False
    func = stmt.value.func
    if isinstance(func, ast.Name):
        return func.id in _PRINT_NAMES
    if isinstance(func, ast.Attribute):
        return func.attr in _LOG_ATTRS
    return False


def _is_docstring(stmt: ast.stmt) -> bool:
    """Detect a bare string expression — typically a docstring."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _contains_keyword(node: ast.AST, keywords: set[str]) -> bool:
    """Check if any keyword appears as an identifier or string inside *node*."""
    if not keywords:
        return False
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id.lower() in keywords:
            return True
        if isinstance(child, ast.Attribute) and child.attr.lower() in keywords:
            return True
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if any(kw in child.value.lower() for kw in keywords):
                return True
    return False


def _has_call(node: ast.AST) -> bool:
    """Return True if *node*'s subtree contains any function call."""
    return any(isinstance(c, ast.Call) for c in ast.walk(node))


def _is_pure_constant_rhs(node: ast.AST) -> bool:
    """True when RHS is a literal constant / tuple / list / dict of constants."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(isinstance(e, ast.Constant) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            (k is None or isinstance(k, ast.Constant)) and isinstance(v, ast.Constant)
            for k, v in zip(node.keys, node.values)
        )
    return False


# ---------------------------------------------------------------------------
# Body filtering
# ---------------------------------------------------------------------------

def _should_keep(stmt: ast.stmt, keywords: set[str]) -> bool:
    """Decide whether a statement inside a function body survives compression."""
    # Drop prints / debug logs first.
    if _is_debug_log(stmt):
        return False

    # Drop docstrings unless they mention a keyword.
    if _is_docstring(stmt):
        return _contains_keyword(stmt, keywords)

    # Always keep control-flow, returns, raises, exit statements.
    if isinstance(stmt, (
        ast.Return, ast.Raise, ast.If, ast.Try, ast.For, ast.While,
        ast.With, ast.AsyncFor, ast.AsyncWith, ast.Assert,
        ast.Break, ast.Continue, ast.Pass,
    )):
        return True

    # Nested function/class definitions: keep their shape.
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return True

    # Imports inside a function body are deliberate — keep them.
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return True

    # Assignments: drop pure-constant boilerplate without keyword signal.
    if isinstance(stmt, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        rhs = getattr(stmt, "value", None)
        if rhs is None:
            return True
        if _contains_keyword(stmt, keywords):
            return True
        if _has_call(rhs):
            return True
        if _is_pure_constant_rhs(rhs):
            return False
        return True  # complex expression — keep by default

    # Expression statements: keep only if they carry a keyword.
    if isinstance(stmt, ast.Expr):
        return _contains_keyword(stmt, keywords)

    return True


def _filter_body(body: list[ast.stmt], keywords: set[str]) -> list[ast.stmt]:
    """Return a filtered copy of *body* keeping only relevant statements."""
    result: list[ast.stmt] = []
    for stmt in body:
        if not _should_keep(stmt, keywords):
            continue

        # Recurse into control-flow bodies so we also prune their interiors.
        if isinstance(stmt, ast.If):
            stmt.body = _filter_body(stmt.body, keywords) or [ast.Pass()]
            stmt.orelse = _filter_body(stmt.orelse, keywords)
        elif isinstance(stmt, (ast.For, ast.While, ast.AsyncFor)):
            stmt.body = _filter_body(stmt.body, keywords) or [ast.Pass()]
            stmt.orelse = _filter_body(stmt.orelse, keywords)
        elif isinstance(stmt, ast.Try):
            stmt.body = _filter_body(stmt.body, keywords) or [ast.Pass()]
            for handler in stmt.handlers:
                handler.body = _filter_body(handler.body, keywords) or [ast.Pass()]
            stmt.orelse = _filter_body(stmt.orelse, keywords)
            stmt.finalbody = _filter_body(stmt.finalbody, keywords)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            stmt.body = _filter_body(stmt.body, keywords) or [ast.Pass()]

        result.append(stmt)
    return result


# ---------------------------------------------------------------------------
# Callee collection (for the `# calls:` hint)
# ---------------------------------------------------------------------------

def _collect_callees(tree: ast.AST) -> list[str]:
    """Return deduplicated callee names found in *tree*, in AST order."""
    names: list[str] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name: str | None = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if not name:
            continue
        if name in _PRINT_NAMES or name in _LOG_ATTRS:
            continue
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compress_code(snippet: str, keywords: Iterable[str]) -> str:
    """Compress a function/method snippet, keeping only debug-relevant lines.

    Preserves:
      * the ``def``/``async def`` signature
      * return / raise / conditional / try-except statements
      * statements that contain a query keyword or a function call

    Drops:
      * print and logging statements
      * pure-constant assignments
      * docstrings without keyword matches

    Appends a ``# calls: a(), b()`` comment enumerating call dependencies.
    If the snippet can't be parsed as a function, it's returned unchanged.
    """
    if not snippet or not snippet.strip():
        return snippet

    kw_set = {k.lower() for k in keywords}
    dedented = textwrap.dedent(snippet).strip()

    try:
        tree = ast.parse(dedented)
    except SyntaxError:
        return snippet

    if not tree.body:
        return snippet

    top = tree.body[0]
    if not isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return snippet

    callees = _collect_callees(top)
    top.body = _filter_body(top.body, kw_set) or [ast.Pass()]

    try:
        compressed = ast.unparse(top)
    except Exception:
        return snippet

    if callees:
        hint = ", ".join(f"{name}()" for name in callees)
        compressed = f"{compressed}\n# calls: {hint}"

    return compressed
