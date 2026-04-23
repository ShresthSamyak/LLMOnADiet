"""Shadow MCP server — intercepts read_file/list_directory calls.

Serves compressed call-graph representations for indexed files,
passes through unindexed files unchanged.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("llm-diet-shadow")

_GRAPH_PATH = Path(".cecl/graph.json")
_graph_cache: dict | None = None
_graph_loaded = False


def _load_graph() -> dict | None:
    global _graph_cache, _graph_loaded
    if _graph_loaded:
        return _graph_cache
    _graph_loaded = True
    if not _GRAPH_PATH.exists():
        return None
    try:
        _graph_cache = json.loads(_GRAPH_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return _graph_cache


def _resolve(file_path: str, graph: dict) -> list[dict]:
    """Return all function/method nodes belonging to file_path."""
    target = str(Path(file_path).resolve())
    return [
        n for n in graph.get("nodes", [])
        if n.get("type") in ("function", "method")
        and str(Path(n.get("file", "")).resolve()) == target
    ]


@mcp.tool()
def read_file(file_path: str) -> str:
    """Return compressed call-graph context for indexed files, raw content otherwise."""
    graph = _load_graph()

    if graph is not None:
        nodes = _resolve(file_path, graph)
        if nodes:
            lines = [
                f"# [compressed by llm-diet]",
                f"# file: {file_path}",
                f"# functions: {len(nodes)}",
                "",
            ]
            for node in sorted(nodes, key=lambda n: n.get("line", 0)):
                code = node.get("code", "").rstrip()
                if code:
                    lines.append(code)
                    lines.append("")
            return "\n".join(lines)

    # Passthrough — file not in graph or graph not built
    try:
        return Path(file_path).read_text(encoding="utf-8")
    except OSError as exc:
        return f"ERROR: {exc}"


@mcp.tool()
def list_directory(path: str) -> str:
    """List filenames in a directory (names only, no content)."""
    try:
        entries = sorted(os.listdir(path))
    except OSError as exc:
        return f"ERROR: {exc}"
    return "\n".join(entries)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
