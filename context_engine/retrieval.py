"""Context retrieval engine: query → relevant code context."""

from __future__ import annotations

import re
from collections import deque
from typing import Any, TypedDict

from .compressor import compress_code

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Node = dict[str, Any]
Edge = dict[str, str]


class Graph(TypedDict):
    nodes: list[Node]
    edges: list[Edge]


class QueryResult(TypedDict):
    intent: str
    keywords: list[str]
    entry_points: list[str]
    nodes_selected: list[str]
    token_estimate: int
    token_estimate_raw: int
    context: str


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEBUG_TRIGGERS: frozenset[str] = frozenset({"fix", "bug", "error", "issue", "debug", "crash", "fail", "broken"})
_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "in", "on", "at", "to", "for", "of", "and", "or",
    "is", "it", "be", "do", "me", "my", "we", "us", "i", "with", "this",
    "that", "have", "has", "not", "are", "was", "but", "get", "set",
})

_MAX_NODES = 15
_MAX_DEPTH = 2


# ---------------------------------------------------------------------------
# Step 1: Query understanding
# ---------------------------------------------------------------------------

def parse_query(query: str) -> dict[str, Any]:
    """Return intent + keywords extracted from a raw query string.

    Intent is ``"debug"`` when the query contains a debugging trigger word,
    otherwise ``"lookup"``.  Keywords are lower-cased non-stop words that are
    likely symbol names (letters/digits/underscores only).
    """
    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query.lower())
    intent = "debug" if _DEBUG_TRIGGERS & set(tokens) else "lookup"
    keywords = [t for t in tokens if t not in _STOP_WORDS and t not in _DEBUG_TRIGGERS]
    # Deduplicate preserving order.
    seen: set[str] = set()
    unique_kw: list[str] = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique_kw.append(kw)
    return {"intent": intent, "keywords": unique_kw}


# ---------------------------------------------------------------------------
# Step 2: Entry point detection
# ---------------------------------------------------------------------------

def find_entry_points(keywords: list[str], nodes: list[Node]) -> list[str]:
    """Return node IDs whose name or file path contains any keyword."""
    entries: list[str] = []
    for node in nodes:
        if node["type"] not in ("function", "method", "class", "file"):
            continue
        nid: str = node["id"]
        # The part after the last ':' is the bare symbol; before it is the file.
        label = nid.split(":")[-1].lower()
        file_part = nid.split(":")[0].lower()
        for kw in keywords:
            if kw in label or kw in file_part:
                entries.append(nid)
                break
    return entries


# ---------------------------------------------------------------------------
# Step 3: Graph traversal
# ---------------------------------------------------------------------------

def _build_adjacency(edges: list[Edge]) -> tuple[dict[str, list[tuple[str, str]]], dict[str, list[tuple[str, str]]]]:
    """Build forward and reverse adjacency maps from the edge list.

    Returns ``(outgoing, incoming)`` where each value is a list of
    ``(neighbour_id, edge_type)`` tuples.
    """
    outgoing: dict[str, list[tuple[str, str]]] = {}
    incoming: dict[str, list[tuple[str, str]]] = {}
    for edge in edges:
        src, dst, etype = edge["from"], edge["to"], edge["type"]
        outgoing.setdefault(src, []).append((dst, etype))
        incoming.setdefault(dst, []).append((src, etype))
    return outgoing, incoming


def traverse_graph(
    entry_ids: list[str],
    nodes: list[Node],
    edges: list[Edge],
    max_depth: int = _MAX_DEPTH,
) -> dict[str, int]:
    """BFS from each entry node following calls, imports, and reverse-calls.

    Returns a mapping of ``node_id → minimum depth`` reached.  Depth 0 means
    the node is an entry point; depth 1 means one hop away, etc.
    """
    node_ids: set[str] = {n["id"] for n in nodes}
    outgoing, incoming = _build_adjacency(edges)

    # Edge types to follow in each direction.
    _follow_out: frozenset[str] = frozenset({"calls", "imports", "contains", "has_method"})
    _follow_in: frozenset[str] = frozenset({"calls"})   # reverse-calls for debugging

    visited: dict[str, int] = {}
    queue: deque[tuple[str, int]] = deque()

    for eid in entry_ids:
        if eid in node_ids:
            queue.append((eid, 0))
            visited[eid] = 0

    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue

        # Forward edges
        for neighbour, etype in outgoing.get(current, []):
            if etype in _follow_out and neighbour not in visited:
                visited[neighbour] = depth + 1
                queue.append((neighbour, depth + 1))

        # Reverse call edges (who calls *current*)
        for neighbour, etype in incoming.get(current, []):
            if etype in _follow_in and neighbour not in visited:
                visited[neighbour] = depth + 1
                queue.append((neighbour, depth + 1))

    return visited


# ---------------------------------------------------------------------------
# Step 4: Relevance ranking + filtering
# ---------------------------------------------------------------------------

def rank_nodes(
    visited: dict[str, int],
    entry_ids: list[str],
    all_nodes: list[Node],
    max_nodes: int = _MAX_NODES,
) -> list[Node]:
    """Return up to *max_nodes* nodes ordered by relevance.

    Priority (lower score = higher priority):
      0 — entry node
      1 — depth-1 function/method (direct callee/caller)
      2 — depth-1 file/class
      3 — depth-2 function/method
      4 — depth-2 file/class
    """
    entry_set = set(entry_ids)
    node_map: dict[str, Node] = {n["id"]: n for n in all_nodes}

    def _priority(node_id: str) -> int:
        if node_id in entry_set:
            return 0
        depth = visited.get(node_id, 99)
        ntype = node_map.get(node_id, {}).get("type", "")
        is_code = ntype in ("function", "method")
        if depth == 1:
            return 1 if is_code else 2
        if depth == 2:
            return 3 if is_code else 4
        return 10

    ranked_ids = sorted(visited.keys(), key=_priority)
    result: list[Node] = []
    for nid in ranked_ids:
        node = node_map.get(nid)
        if node is not None:
            result.append(node)
        if len(result) >= max_nodes:
            break
    return result


# ---------------------------------------------------------------------------
# Step 5: Context assembly
# ---------------------------------------------------------------------------

def build_context(
    ranked_nodes: list[Node],
    entry_ids: list[str],
    keywords: list[str] | None = None,
    compress: bool = True,
) -> str:
    """Assemble deduplicated code snippets ordered by entry → callees → callers.

    Each function/method snippet is passed through :func:`compress_code` so
    the emitted context contains only debug-relevant lines plus a
    ``# calls:`` dependency hint.  File/class nodes without code contribute a
    comment stub.
    """
    entry_set = set(entry_ids)
    kws = keywords or []

    # Split into ordered buckets.
    entries: list[Node] = []
    rest: list[Node] = []
    for node in ranked_nodes:
        (entries if node["id"] in entry_set else rest).append(node)

    ordered = entries + rest
    seen_ids: set[str] = set()
    parts: list[str] = []

    for node in ordered:
        nid = node["id"]
        if nid in seen_ids:
            continue
        seen_ids.add(nid)

        code: str = node.get("code", "").strip()
        ntype: str = node.get("type", "")

        if code:
            if compress and ntype in ("function", "method"):
                code = compress_code(code, kws).strip()
            if not code:
                continue
            header = f"# [{ntype}] {nid}"
            parts.append(f"{header}\n{code}")
        else:
            # file / class nodes without snippets: emit a comment stub.
            parts.append(f"# [{ntype}] {nid}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Step 6: Token estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Rough token count: characters / 4 (GPT-style approximation)."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_query(query: str, graph: Graph, compress: bool = True) -> QueryResult:
    """Full retrieval pipeline: query string + graph → QueryResult.

    When *compress* is True (default), every function/method snippet is
    passed through :func:`compress_code` to shrink the emitted context.
    ``token_estimate_raw`` reports what the context would cost without
    compression, enabling a savings comparison.
    """
    parsed = parse_query(query)
    intent: str = parsed["intent"]
    keywords: list[str] = parsed["keywords"]

    nodes: list[Node] = graph["nodes"]
    edges: list[Edge] = graph["edges"]

    entry_ids = find_entry_points(keywords, nodes)
    visited = traverse_graph(entry_ids, nodes, edges)
    ranked = rank_nodes(visited, entry_ids, nodes)

    context_text = build_context(ranked, entry_ids, keywords, compress=compress)
    raw_text = build_context(ranked, entry_ids, keywords, compress=False)

    return QueryResult(
        intent=intent,
        keywords=keywords,
        entry_points=entry_ids,
        nodes_selected=[n["id"] for n in ranked],
        token_estimate=estimate_tokens(context_text),
        token_estimate_raw=estimate_tokens(raw_text),
        context=context_text,
    )
