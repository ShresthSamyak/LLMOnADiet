"""AST-based Python source file parser."""

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class FunctionInfo:
    name: str
    file: str
    line: int
    code: str                        # source snippet
    calls: list[str] = field(default_factory=list)   # bare callee names


@dataclass
class MethodInfo:
    name: str
    class_name: str
    file: str
    line: int
    code: str
    calls: list[str] = field(default_factory=list)


@dataclass
class ClassInfo:
    name: str
    file: str
    line: int
    methods: list[MethodInfo] = field(default_factory=list)


@dataclass
class ImportInfo:
    """Represents one import statement."""
    module: str                      # dotted module name
    names: list[str] = field(default_factory=list)   # symbols; empty = bare import


@dataclass
class FileParseResult:
    path: str                        # relative path used as stable ID prefix
    source: str                      # full source text (needed for snippet extraction)
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    imports: list[ImportInfo] = field(default_factory=list)
    # Flat set of names imported into this file — used for call-edge filtering.
    imported_names: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_snippet(source: str, node: ast.AST) -> str:
    """Return the exact source segment for *node*, with line-slice fallback."""
    snippet = ast.get_source_segment(source, node)
    if snippet:
        return snippet
    # Fallback: slice by line numbers when get_source_segment fails.
    lines = source.splitlines()
    start = node.lineno - 1  # type: ignore[attr-defined]
    end = getattr(node, "end_lineno", node.lineno)  # type: ignore[attr-defined]
    return "\n".join(lines[start:end])


def _extract_calls(node: ast.AST) -> list[str]:
    """Return deduplicated bare callee names found in *node*'s subtree."""
    calls: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            calls.append(func.id)
        elif isinstance(func, ast.Attribute):
            calls.append(func.attr)
    return list(dict.fromkeys(calls))


def _parse_import_stmt(node: ast.Import | ast.ImportFrom) -> ImportInfo:
    if isinstance(node, ast.Import):
        # `import a, b` → one ImportInfo per alias keeps things uniform
        return ImportInfo(
            module=", ".join(alias.name for alias in node.names),
            names=[],
        )
    module = node.module or ""
    names = [alias.name for alias in node.names]
    return ImportInfo(module=module, names=names)


def _collect_imported_names(imports: list[ImportInfo]) -> set[str]:
    """Build the flat set of names that are brought into scope by imports."""
    names: set[str] = set()
    for imp in imports:
        if imp.names:
            # `from x import a, b` → {a, b}
            names.update(imp.names)
        else:
            # `import a.b.c` → top-level name is a
            top = imp.module.split(",")[0].strip().split(".")[0]
            if top:
                names.add(top)
    return names


# ---------------------------------------------------------------------------
# Top-level function walker (skips methods defined inside classes)
# ---------------------------------------------------------------------------

def _top_level_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return only module-level functions (not methods inside classes)."""
    result = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.append(node)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_file(path: Path) -> FileParseResult | None:
    """Parse a single .py file and return structured metadata."""
    rel = str(path)
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:
        logger.warning("Syntax error in %s: %s", rel, exc)
        return None
    except Exception as exc:
        logger.warning("Cannot parse %s: %s", rel, exc)
        return None

    result = FileParseResult(path=rel, source=source)

    # Collect imports first so we can resolve names later.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            result.imports.append(_parse_import_stmt(node))

    result.imported_names = _collect_imported_names(result.imports)

    # Classes — iterate top-level nodes to preserve class scope.
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            methods: list[MethodInfo] = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(
                        MethodInfo(
                            name=item.name,
                            class_name=node.name,
                            file=rel,
                            line=item.lineno,
                            code=_get_snippet(source, item),
                            calls=_extract_calls(item),
                        )
                    )
            result.classes.append(
                ClassInfo(name=node.name, file=rel, line=node.lineno, methods=methods)
            )

    # Module-level functions only (skip methods already captured above).
    for node in _top_level_functions(tree):
        result.functions.append(
            FunctionInfo(
                name=node.name,
                file=rel,
                line=node.lineno,
                code=_get_snippet(source, node),
                calls=_extract_calls(node),
            )
        )

    return result
