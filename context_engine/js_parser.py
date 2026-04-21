"""Tree-sitter based parser for JavaScript / TypeScript files.

Produces FileParseResult objects compatible with graph_builder.build_graph,
mirroring the contract of parser.py for Python files.

Supported extensions: .js  .mjs  .cjs  .jsx  .ts  .tsx
"""
from __future__ import annotations

import logging
from pathlib import Path

from .parser import ClassInfo, FileParseResult, FunctionInfo, ImportInfo, MethodInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language loader — lazy, cached, graceful if tree-sitter is absent
# ---------------------------------------------------------------------------

_PARSERS: dict[str, object] = {}   # lang_name → Parser instance
_AVAILABLE = True


def _get_parser(lang: str):
    global _AVAILABLE
    if not _AVAILABLE:
        return None
    if lang in _PARSERS:
        return _PARSERS[lang]
    try:
        from tree_sitter_language_pack import get_parser  # type: ignore
        p = get_parser(lang)
        _PARSERS[lang] = p
        return p
    except Exception as exc:
        logger.debug("tree-sitter parser unavailable for %s: %s", lang, exc)
        _AVAILABLE = False
        return None


def _lang_for(path: Path) -> str:
    """Return the tree-sitter language name for a given file extension."""
    ext = path.suffix.lower()
    if ext in (".ts", ".tsx"):
        return "typescript"
    return "javascript"   # .js .mjs .cjs .jsx


JS_EXTENSIONS = frozenset({".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx"})


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _text(node) -> str:
    """Decode node bytes to str."""
    return (node.text or b"").decode("utf-8", errors="replace")


def _source_slice(source: str, node) -> str:
    """Return the exact source lines covered by *node*."""
    lines = source.splitlines(keepends=True)
    start_row, _ = node.start_point
    end_row, end_col = node.end_point
    if start_row == end_row:
        return lines[start_row][: end_col] if start_row < len(lines) else ""
    chunk = lines[start_row: end_row + 1]
    if chunk:
        chunk[-1] = chunk[-1][: end_col]
    return "".join(chunk)


def _child_by_type(node, *types: str):
    for child in node.children:
        if child.type in types:
            return child
    return None


def _named_children_by_type(node, *types: str):
    return [c for c in node.children if c.type in types]


# ---------------------------------------------------------------------------
# Call-site extraction
# ---------------------------------------------------------------------------

def _extract_calls(node) -> list[str]:
    """Walk subtree and collect deduplicated callee names."""
    seen: dict[str, None] = {}

    def walk(n):
        if n.type == "call_expression":
            fn = n.children[0] if n.children else None
            if fn is not None:
                if fn.type == "identifier":
                    seen[_text(fn)] = None
                elif fn.type == "member_expression":
                    # foo.bar() — record "bar" (the method name)
                    prop = fn.children[-1] if fn.children else None
                    if prop and prop.type in ("property_identifier", "identifier"):
                        seen[_text(prop)] = None
        for child in n.children:
            walk(child)

    walk(node)
    return list(seen)


# ---------------------------------------------------------------------------
# Import extraction
# ---------------------------------------------------------------------------

def _extract_imports(root) -> tuple[list[ImportInfo], set[str]]:
    imports: list[ImportInfo] = []
    imported_names: set[str] = set()

    for node in root.children:
        # ES6: import { foo, bar } from "./module"
        # ES6: import DefaultExport from "module"
        # ES6: import * as ns from "module"
        if node.type == "import_statement":
            module = ""
            names: list[str] = []

            # find the source string
            for child in node.children:
                if child.type == "string":
                    module = _text(child).strip("'\"")

            clause = _child_by_type(node, "import_clause")
            if clause:
                for child in clause.children:
                    if child.type == "identifier":
                        # default import: import Foo from "..."
                        names.append(_text(child))
                        imported_names.add(_text(child))
                    elif child.type == "named_imports":
                        for spec in child.children:
                            if spec.type == "import_specifier":
                                # grab the local name (last identifier in specifier)
                                idents = [c for c in spec.children if c.type == "identifier"]
                                if idents:
                                    local = _text(idents[-1])
                                    names.append(local)
                                    imported_names.add(local)
                    elif child.type == "namespace_import":
                        # import * as ns
                        idents = [c for c in child.children if c.type == "identifier"]
                        if idents:
                            ns = _text(idents[-1])
                            names.append(ns)
                            imported_names.add(ns)

            if module:
                imports.append(ImportInfo(module=module, names=names))

        # CommonJS: const x = require("module")
        elif node.type in ("lexical_declaration", "variable_declaration"):
            for decl in node.children:
                if decl.type != "variable_declarator":
                    continue
                rhs_nodes = [c for c in decl.children if c.type == "call_expression"]
                for call in rhs_nodes:
                    fn_node = call.children[0] if call.children else None
                    if fn_node and fn_node.type == "identifier" and _text(fn_node) == "require":
                        args = _child_by_type(call, "arguments")
                        if args:
                            strings = [c for c in args.children if c.type == "string"]
                            if strings:
                                module = _text(strings[0]).strip("'\"")
                                # local binding name
                                lhs = decl.children[0] if decl.children else None
                                name = _text(lhs) if lhs and lhs.type == "identifier" else ""
                                imports.append(ImportInfo(module=module, names=[name] if name else []))
                                if name:
                                    imported_names.add(name)

    return imports, imported_names


# ---------------------------------------------------------------------------
# Function / class extraction
# ---------------------------------------------------------------------------

def _collect_functions_and_classes(
    root,
    source: str,
    file_path: str,
) -> tuple[list[FunctionInfo], list[ClassInfo]]:
    functions: list[FunctionInfo] = []
    classes: list[ClassInfo] = []

    def fn_name_from_node(node) -> str | None:
        """Return function name for any of the function node shapes."""
        # function_declaration / function_expression with explicit name
        ident = _child_by_type(node, "identifier", "type_identifier")
        if ident:
            return _text(ident)
        return None

    def process_top_level(node):
        t = node.type

        # ── named function declaration ────────────────────────────────────
        if t == "function_declaration":
            name = fn_name_from_node(node)
            if name:
                functions.append(FunctionInfo(
                    name=name,
                    file=file_path,
                    line=node.start_point[0] + 1,
                    code=_source_slice(source, node),
                    calls=_extract_calls(node),
                ))

        # ── export statement — unwrap and recurse ─────────────────────────
        elif t == "export_statement":
            for child in node.children:
                if child.type in (
                    "function_declaration", "class_declaration",
                    "lexical_declaration", "variable_declaration",
                ):
                    process_top_level(child)

        # ── const/let/var foo = function / arrow ─────────────────────────
        elif t in ("lexical_declaration", "variable_declaration"):
            for decl in node.children:
                if decl.type != "variable_declarator":
                    continue
                lhs = decl.children[0] if decl.children else None
                if lhs is None or lhs.type not in ("identifier", "type_identifier"):
                    continue
                name = _text(lhs)
                rhs = next(
                    (c for c in decl.children if c.type in ("arrow_function", "function")),
                    None,
                )
                if rhs is not None:
                    functions.append(FunctionInfo(
                        name=name,
                        file=file_path,
                        line=decl.start_point[0] + 1,
                        code=_source_slice(source, decl),
                        calls=_extract_calls(rhs),
                    ))

        # ── class declaration ─────────────────────────────────────────────
        elif t == "class_declaration":
            name_node = _child_by_type(node, "identifier", "type_identifier")
            if name_node is None:
                return
            class_name = _text(name_node)
            methods: list[MethodInfo] = []

            body = _child_by_type(node, "class_body")
            if body:
                for item in body.children:
                    if item.type != "method_definition":
                        continue
                    mname_node = _child_by_type(item, "property_identifier", "identifier")
                    if mname_node is None:
                        continue
                    mname = _text(mname_node)
                    methods.append(MethodInfo(
                        name=mname,
                        class_name=class_name,
                        file=file_path,
                        line=item.start_point[0] + 1,
                        code=_source_slice(source, item),
                        calls=_extract_calls(item),
                    ))

            classes.append(ClassInfo(
                name=class_name,
                file=file_path,
                line=node.start_point[0] + 1,
                methods=methods,
            ))

        # ── TypeScript: interface / type alias (treated as class-like) ───
        elif t == "interface_declaration":
            name_node = _child_by_type(node, "type_identifier")
            if name_node:
                classes.append(ClassInfo(
                    name=_text(name_node),
                    file=file_path,
                    line=node.start_point[0] + 1,
                    methods=[],
                ))

    for child in root.children:
        process_top_level(child)

    return functions, classes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_js_file(path: Path) -> FileParseResult | None:
    """Parse a single JS/TS file and return a FileParseResult, or None on error."""
    lang = _lang_for(path)
    parser = _get_parser(lang)
    if parser is None:
        return None

    rel = str(path)
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Cannot read %s: %s", rel, exc)
        return None

    try:
        tree = parser.parse(source.encode("utf-8", errors="replace"))
    except Exception as exc:
        logger.warning("tree-sitter parse error in %s: %s", rel, exc)
        return None

    root = tree.root_node
    imports, imported_names = _extract_imports(root)
    functions, classes = _collect_functions_and_classes(root, source, rel)

    result = FileParseResult(
        path=rel,
        source=source,
        functions=functions,
        classes=classes,
        imports=imports,
        imported_names=imported_names,
    )
    return result
