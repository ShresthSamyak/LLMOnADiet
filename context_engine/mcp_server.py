"""MCP server entrypoint — exposes get_context(query) as an MCP tool."""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .ranker import format_output
from .retrieval import run_query

mcp = FastMCP("context-engine")

_GRAPH_PATH = Path(".cecl/graph.json")
_graph_cache: dict | None = None


def _load_graph() -> dict:
    global _graph_cache
    if _graph_cache is not None:
        return _graph_cache
    if not _GRAPH_PATH.exists():
        raise FileNotFoundError(
            f"Graph not found at {_GRAPH_PATH}. "
            "Run `context-engine index` in your project root first."
        )
    _graph_cache = json.loads(_GRAPH_PATH.read_text(encoding="utf-8"))
    return _graph_cache  # type: ignore[return-value]


@mcp.tool()
def get_context(query: str) -> str:
    """
    Return minimal compressed codebase context relevant to the given query.
    Deterministic — no LLM calls. Run `context-engine index` first.
    """
    graph = _load_graph()
    result = run_query(query, graph)
    nodes: list[dict] = result.get("nodes", [])
    if not nodes:
        return f"No relevant context found for: {query}"
    return format_output(query, nodes)


if __name__ == "__main__":
    mcp.run()
