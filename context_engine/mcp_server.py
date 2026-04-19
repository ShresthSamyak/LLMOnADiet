from __future__ import annotations

import sys
import json
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP

from context_engine.ranker import format_output, rank_and_select, resolve_nodes
from context_engine.retrieval import run_query

mcp = FastMCP("context-engine")

_GRAPH_PATH = Path(".cecl/graph.json")
_graph_cache: dict | None = None


def _load_graph() -> dict | None:
    global _graph_cache
    if _graph_cache is not None:
        return _graph_cache
    if not _GRAPH_PATH.exists():
        return None
    _graph_cache = json.loads(_GRAPH_PATH.read_text(encoding="utf-8"))
    return _graph_cache


@mcp.tool()
def get_context(query: str) -> str:
    """Minimal codebase context for the given query. Deterministic, no LLM."""
    graph = _load_graph()
    if graph is None:
        return "ERROR: INDEX_NOT_FOUND"

    result = run_query(query, graph)
    selected: list[str] = result.get("nodes_selected", [])
    if not selected:
        return "NO_CONTEXT_FOUND"

    node_dicts = resolve_nodes(selected, graph)
    nodes = rank_and_select(node_dicts, query)
    if not nodes:
        return "NO_CONTEXT_FOUND"

    return format_output(query, nodes)


if __name__ == "__main__":
    mcp.run()
