#!/usr/bin/env python3
"""
UserPromptSubmit hook — injects minimal codebase context before Claude sees the prompt.
Output: {"additionalContext": "<compressed code>"} — always exits 0, never crashes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

from context_engine.ranker import rank_and_select, resolve_nodes
from context_engine.retrieval import run_query

_GRAPH_PATH = Path(".cecl/graph.json")
_TOKEN_BUDGET = 800   # ~800 tokens max (chars // 4)
_MAX_NODES = 5
_BODY_LINES = 10      # lines per function body


def _load_graph() -> dict | None:
    if not _GRAPH_PATH.exists():
        return None
    try:
        return json.loads(_GRAPH_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _sig(code: str) -> str:
    for line in code.splitlines():
        s = line.strip()
        if s.startswith(("def ", "async def ")):
            return s.rstrip(":")
    return ""


def _body(code: str) -> list[str]:
    """Return non-empty, non-comment body lines with docstrings stripped."""
    lines = code.splitlines()

    # Find and skip the signature line
    sig_idx = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(("def ", "async def ")):
            sig_idx = i
            break

    body = lines[sig_idx + 1:]

    # Strip leading docstring (handles single-line and multi-line)
    i = 0
    while i < len(body) and not body[i].strip():
        i += 1
    if i < len(body):
        first = body[i].strip()
        if first.startswith(('"""', "'''")):
            marker = first[:3]
            # Single-line docstring
            if first.count(marker) >= 2 and len(first) > 3:
                i += 1
            else:
                i += 1
                while i < len(body) and marker not in body[i]:
                    i += 1
                i += 1
        body = body[i:]

    return [
        l for l in body
        if l.strip() and not l.strip().startswith("#")
    ]


def _format_node(node: dict, budget_chars: int) -> tuple[str, int]:
    """Return formatted node block and chars consumed."""
    fname = node.get("file", "").replace("\\", "/").split("/")[-1]
    code = node.get("code", "")

    signature = _sig(code) or node.get("name", "?")
    body_lines = _body(code)

    # Truncate body to fit budget
    snippet = body_lines[:_BODY_LINES]
    if len(body_lines) > _BODY_LINES:
        snippet.append("    ...")

    block = f"[{fname}]\n{signature}\n" + "\n".join(snippet)
    if len(block) > budget_chars:
        block = block[:budget_chars].rsplit("\n", 1)[0] + "\n    ..."

    return block, len(block)


def _build_context(nodes: list[dict]) -> str:
    blocks: list[str] = []
    remaining = _TOKEN_BUDGET * 4  # chars

    for node in nodes[:_MAX_NODES]:
        if remaining <= 0:
            break
        block, used = _format_node(node, remaining)
        blocks.append(block)
        remaining -= used + 1  # +1 for separator

    return "\n\n".join(blocks)


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    query: str = payload.get("prompt", "").strip()
    if not query:
        sys.exit(0)

    graph = _load_graph()
    if not graph:
        sys.exit(0)

    try:
        result = run_query(query, graph)
        selected: list[str] = result.get("nodes_selected", [])
        all_ids = [n["id"] for n in graph.get("nodes", []) if "id" in n]
        pool_ids = selected if selected else all_ids

        node_dicts = resolve_nodes(pool_ids, graph)
        ranked = rank_and_select(node_dicts, query, top_n=_MAX_NODES)
    except Exception:
        sys.exit(0)

    if not ranked:
        sys.exit(0)

    context = _build_context(ranked)
    if context.strip():
        sys.stdout.write(json.dumps({"additionalContext": context}))

    sys.exit(0)


if __name__ == "__main__":
    main()
