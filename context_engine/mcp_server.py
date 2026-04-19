from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .ranker import format_output, rank_and_select
from .retrieval import run_query

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
    all_nodes: list[dict] = graph.get("nodes", [])
    retrieved: list[dict] = result.get("nodes", [])

    nodes = rank_and_select(retrieved or all_nodes, query)
    if not nodes:
        return "NO_CONTEXT_FOUND"

    return format_output(query, nodes)


if __name__ == "__main__":
    mcp.run()
