"""Build an enriched call/dependency graph from parsed file results."""

import logging
from pathlib import Path
from typing import Any

from .parser import FileParseResult

logger = logging.getLogger(__name__)

Node = dict[str, Any]
Edge = dict[str, str]
Graph = dict[str, list[Node] | list[Edge]]

# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------

def _file_id(file_path: str) -> str:
    """Normalise a file path to a stable, forward-slash node ID."""
    return Path(file_path).as_posix()


def _symbol_id(file_path: str, symbol: str) -> str:
    """Return '<file>:<symbol>' — the canonical node ID for a symbol."""
    return f"{_file_id(file_path)}:{symbol}"


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_graph(results: list[FileParseResult]) -> Graph:
    """Convert parsed file results into a node/edge graph.

    Node types  : file, function, method, class
    Edge types  : contains, has_method, calls, imports
    """
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    seen_edges: set[tuple[str, str, str]] = set()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def add_node(node: Node) -> None:
        nid = node["id"]
        if nid not in nodes:
            nodes[nid] = node
        else:
            logger.debug("Duplicate node skipped: %s", nid)

    def add_edge(src: str, dst: str, edge_type: str) -> None:
        key = (src, dst, edge_type)
        if key not in seen_edges:
            seen_edges.add(key)
            edges.append({"from": src, "to": dst, "type": edge_type})

    # ------------------------------------------------------------------
    # Pass 1 — register every node; build lookup tables for edge resolution.
    # ------------------------------------------------------------------

    # Maps symbol_id → set of file-relative names defined in that file.
    # Used in pass 2 to decide whether a call edge is resolvable.
    file_local_names: dict[str, set[str]] = {}   # file_id → {symbol_name, ...}

    for result in results:
        fid = _file_id(result.path)
        local: set[str] = set()
        file_local_names[fid] = local

        # File node
        add_node({"id": fid, "type": "file"})

        # Function nodes
        for func in result.functions:
            sid = _symbol_id(result.path, func.name)
            add_node({
                "id": sid,
                "type": "function",
                "file": fid,
                "line": func.line,
                "code": func.code,
            })
            local.add(func.name)

        # Class + method nodes
        for cls in result.classes:
            cid = _symbol_id(result.path, cls.name)
            add_node({
                "id": cid,
                "type": "class",
                "file": fid,
                "line": cls.line,
            })
            local.add(cls.name)

            for method in cls.methods:
                qualified = f"{cls.name}.{method.name}"
                mid = _symbol_id(result.path, qualified)
                add_node({
                    "id": mid,
                    "type": "method",
                    "file": fid,
                    "line": method.line,
                    "code": method.code,
                })
                local.add(method.name)   # bare name for call resolution

    # ------------------------------------------------------------------
    # Pass 2 — emit edges.
    # ------------------------------------------------------------------

    # Build a reverse map: bare symbol name → list of node IDs that define it.
    # Lets us resolve cross-file calls when there is an explicit import.
    name_to_ids: dict[str, list[str]] = {}
    for node in nodes.values():
        if node["type"] in ("function", "method", "class"):
            bare = node["id"].split(":")[-1].split(".")[-1]
            name_to_ids.setdefault(bare, []).append(node["id"])

    for result in results:
        fid = _file_id(result.path)
        local_names = file_local_names[fid]

        # contains: file → function / class
        for func in result.functions:
            add_edge(fid, _symbol_id(result.path, func.name), "contains")

        for cls in result.classes:
            cid = _symbol_id(result.path, cls.name)
            add_edge(fid, cid, "contains")

            # has_method: class → method
            for method in cls.methods:
                qualified = f"{cls.name}.{method.name}"
                add_edge(cid, _symbol_id(result.path, qualified), "has_method")

        # imports: file → file  (resolve module name to a known file node)
        for imp in result.imports:
            # Try each candidate module path spelling.
            for candidate in _module_candidates(imp.module):
                if candidate in nodes:
                    add_edge(fid, candidate, "imports")
                    break

        # calls — only emit if callee is local OR explicitly imported.
        resolvable = local_names | result.imported_names

        for func in result.functions:
            caller_id = _symbol_id(result.path, func.name)
            for callee_name in func.calls:
                _maybe_add_call(
                    caller_id, callee_name, resolvable, fid,
                    result.path, local_names, name_to_ids, add_edge,
                )

        for cls in result.classes:
            for method in cls.methods:
                caller_id = _symbol_id(result.path, f"{cls.name}.{method.name}")
                for callee_name in method.calls:
                    _maybe_add_call(
                        caller_id, callee_name, resolvable, fid,
                        result.path, local_names, name_to_ids, add_edge,
                    )

    logger.info("Graph built: %d nodes, %d edges", len(nodes), len(edges))
    return {"nodes": list(nodes.values()), "edges": edges}


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------

def _module_candidates(module: str) -> list[str]:
    """Generate plausible file-node IDs for a dotted module name."""
    parts = module.strip().split(".")
    candidates = []
    # e.g. "auth.utils" → ["auth/utils.py", "auth/utils/__init__.py"]
    base = "/".join(parts)
    candidates.append(f"{base}.py")
    candidates.append(f"{base}/__init__.py")
    # Also try top-level module as a single file.
    if len(parts) > 1:
        candidates.append(f"{parts[-1]}.py")
    return candidates


def _maybe_add_call(
    caller_id: str,
    callee_name: str,
    resolvable: set[str],
    caller_file_id: str,
    caller_path: str,
    local_names: set[str],
    name_to_ids: dict[str, list[str]],
    add_edge_fn: Any,
) -> None:
    """Emit a calls edge only when the callee is verifiably resolvable."""
    if callee_name not in resolvable:
        return

    if callee_name in local_names:
        # Prefer same-file symbol by building the qualified ID.
        callee_id = _symbol_id(caller_path, callee_name)
        # Might be a bare method name like "save" — also check "ClassName.save".
        if callee_id in _name_to_ids_local(caller_file_id, callee_name, name_to_ids):
            add_edge_fn(caller_id, callee_id, "calls")
            return
        # Fall back to any node in this file sharing the bare name.
        for nid in name_to_ids.get(callee_name, []):
            if nid.startswith(caller_file_id + ":"):
                add_edge_fn(caller_id, nid, "calls")
                return
    else:
        # Imported name — resolve to whichever node carries that bare name.
        candidates = name_to_ids.get(callee_name, [])
        for nid in candidates:
            add_edge_fn(caller_id, nid, "calls")


def _name_to_ids_local(file_id: str, name: str, name_to_ids: dict[str, list[str]]) -> list[str]:
    return [nid for nid in name_to_ids.get(name, []) if nid.startswith(file_id + ":")]
