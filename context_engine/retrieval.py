"""Context retrieval engine: query → relevant code context."""

from __future__ import annotations

import re
from collections import deque
from typing import Any, TypedDict

from .compressor import compress_code
from .pruner import PruneResult, importance_score, prune

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
    categories: dict[str, str]
    inline_hints: dict[str, list[str]]
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
# Step 2: Entry point detection — fuzzy tokens + synonyms + fan-in
# ---------------------------------------------------------------------------

# Files whose names tend to host HTTP/CLI/UI entry points.
_ENTRY_FILE_TOKENS: frozenset[str] = frozenset({
    "auth", "routes", "route", "views", "view", "api", "handlers", "handler",
    "controllers", "controller", "endpoints", "endpoint", "server", "app",
    "main", "cli", "command", "commands", "resolvers", "resolver",
})

# Symmetric-ish synonym neighbourhoods. Keys are canonical; values expand
# to a small set of near-synonyms for fuzzy recall.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "login":         ("auth", "authenticate", "signin"),
    "signin":        ("auth", "authenticate", "login"),
    "auth":          ("login", "authenticate", "signin"),
    "authenticate":  ("login", "auth", "signin"),
    "logout":        ("signout", "auth"),
    "user":          ("account", "profile", "member"),
    "account":       ("user", "profile"),
    "profile":       ("user", "account"),
    "error":         ("fail", "exception", "err"),
    "fail":          ("error", "exception"),
    "exception":     ("error", "fail"),
    "delete":        ("remove", "destroy"),
    "remove":        ("delete", "destroy"),
    "create":        ("add", "insert", "make", "new"),
    "add":           ("create", "insert"),
    "update":        ("edit", "modify", "patch"),
    "edit":          ("update", "modify"),
    "fetch":         ("get", "retrieve", "load", "read"),
    "save":          ("store", "persist", "write"),
    "store":         ("save", "persist"),
    "query":         ("search", "find", "lookup"),
    "search":        ("query", "find", "lookup"),
    "parse":         ("decode", "deserialize"),
    "render":        ("format", "display"),
    "validate":      ("check", "verify"),
    "verify":        ("validate", "check"),
    "compress":      ("shrink", "reduce"),
    "prune":         ("trim", "cut", "filter"),
}

_FAN_IN_HIGH = 3                 # ≥N incoming call edges → high fan-in
_MAX_ENTRIES = 3                 # spec: "top 1–3 entry points"
_SCORE_KEYWORD = 3
_SCORE_SYNONYM = 2
_SCORE_FILE_HEURISTIC = 2
_SCORE_FAN_IN = 2

# Regex fragments for camelCase splitting.
_CAMEL_SPLIT = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
# Strip a language extension whenever it's followed by a separator or end
# of string — so `auth.py:login` loses the `py` noise token.
_EXT_STRIP = re.compile(
    r"\.(py|pyi|pyx|js|ts|tsx|jsx|rb|go|java|rs)(?=[:./]|$)",
    re.IGNORECASE,
)


def _tokenize(identifier: str) -> list[str]:
    """Split snake_case / camelCase / dotted / path-like strings into lower tokens."""
    identifier = _EXT_STRIP.sub("", identifier)
    tokens: list[str] = []
    for word in re.split(r"[^a-zA-Z0-9]+", identifier):
        if not word:
            continue
        parts = _CAMEL_SPLIT.findall(word) or [word]
        tokens.extend(p.lower() for p in parts)
    return tokens


def _expand_keywords(keywords: list[str]) -> tuple[set[str], set[str]]:
    """Return ``(primary, synonyms)`` token sets for a list of raw keywords.

    Keywords themselves are tokenised so multi-word names (``compress_code``)
    match against tokenised node identifiers (``["compress", "code"]``).
    """
    primary: set[str] = set()
    for kw in keywords:
        primary.update(_tokenize(kw))
    synonyms: set[str] = set()
    for kw in primary:
        for syn in _SYNONYMS.get(kw, ()):
            if syn not in primary:
                synonyms.add(syn)
    return primary, synonyms


def _compute_fan_in(edges: list[Edge]) -> dict[str, int]:
    """Count incoming ``calls`` edges per node — proxy for importance."""
    fan_in: dict[str, int] = {}
    for edge in edges:
        if edge.get("type") == "calls":
            fan_in[edge["to"]] = fan_in.get(edge["to"], 0) + 1
    return fan_in


def _score_node(
    node: Node,
    primary: set[str],
    synonyms: set[str],
    fan_in: dict[str, int],
) -> int:
    """Score a node's match quality. Zero = no signal, skip.

    A node must have at least one primary or synonym token hit to count.
    File-heuristic and fan-in are pure *boosts* on top of a real match —
    otherwise every node in an ``auth.py`` file would spuriously match any
    query, and the fallback path would never run.
    """
    if node["type"] not in ("function", "method", "class", "file"):
        return 0

    nid: str = node["id"]
    file_part, _, sym_part = nid.rpartition(":")
    if not file_part:          # file nodes have no ':' in ID
        file_part = nid
        sym_part = ""

    name_tokens = set(_tokenize(sym_part))
    file_tokens = set(_tokenize(file_part))
    all_tokens = name_tokens | file_tokens

    keyword_hits = len(all_tokens & primary)
    synonym_hits = len(all_tokens & synonyms)
    if keyword_hits == 0 and synonym_hits == 0:
        return 0

    score = _SCORE_KEYWORD * keyword_hits + _SCORE_SYNONYM * synonym_hits
    if file_tokens & _ENTRY_FILE_TOKENS:
        score += _SCORE_FILE_HEURISTIC
    if node["type"] in ("function", "method") and fan_in.get(nid, 0) >= _FAN_IN_HIGH:
        score += _SCORE_FAN_IN
    return score


def _type_preference(ntype: str) -> int:
    """Sort tiebreak: prefer concrete code symbols over files/classes."""
    return {"function": 0, "method": 0, "class": 1, "file": 2}.get(ntype, 3)


def _fallback_entries(
    nodes: list[Node],
    edges: list[Edge],
    max_entries: int,
) -> list[str]:
    """Return highest-degree function/method nodes when nothing matched."""
    degree: dict[str, int] = {}
    for edge in edges:
        degree[edge["from"]] = degree.get(edge["from"], 0) + 1
        degree[edge["to"]] = degree.get(edge["to"], 0) + 1

    code_nodes = [n for n in nodes if n["type"] in ("function", "method")]
    if not code_nodes:
        # Graph without any code — fall back to file nodes.
        code_nodes = [n for n in nodes if n["type"] == "file"]
    code_nodes.sort(key=lambda n: (-degree.get(n["id"], 0), n["id"]))
    return [n["id"] for n in code_nodes[:max_entries]]


def find_entry_points(
    keywords: list[str],
    nodes: list[Node],
    edges: list[Edge] | None = None,
    max_entries: int = _MAX_ENTRIES,
) -> list[str]:
    """Pick the 1-3 best entry nodes for a query.

    Scoring combines:
      * token-level fuzzy match (``+3`` per primary token hit)
      * synonym match (``+2`` per hit)
      * entry-file heuristic (``+2`` if file name resembles ``auth``/``routes``/…)
      * high fan-in (``+2`` when a symbol has many callers)

    When no node scores above zero — or no keywords are given — falls back
    to the highest-degree function/method nodes so the result is never
    empty on a non-empty graph.
    """
    edges_list = edges or []
    fan_in = _compute_fan_in(edges_list)
    primary, synonyms = _expand_keywords(keywords)

    if not primary:
        return _fallback_entries(nodes, edges_list, max_entries)

    scored: list[tuple[int, int, str]] = []   # (-score, type_pref, nid)
    for node in nodes:
        s = _score_node(node, primary, synonyms, fan_in)
        if s > 0:
            scored.append((-s, _type_preference(node["type"]), node["id"]))

    if not scored:
        return _fallback_entries(nodes, edges_list, max_entries)

    scored.sort()
    picked: list[str] = []
    seen: set[str] = set()
    for _, _, nid in scored:
        if nid in seen:
            continue
        seen.add(nid)
        picked.append(nid)
        if len(picked) >= max_entries:
            break
    return picked


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
    """Return up to *max_nodes* candidate nodes ordered by relevance.

    Primary key — structural priority (entry < direct code < direct struct
    < indirect code < indirect struct).  Secondary key — negative importance
    score, so within a tier the higher-signal nodes surface first.  The
    pruner then narrows the candidates further.
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

    def _sort_key(node_id: str) -> tuple[int, int]:
        node = node_map.get(node_id, {})
        return (_priority(node_id), -importance_score(node))

    ranked_ids = sorted(visited.keys(), key=_sort_key)
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
    inline_hints: dict[str, list[str]] | None = None,
) -> str:
    """Assemble code snippets ordered by entry → callees → callers.

    Each function/method snippet is passed through :func:`compress_code` so
    the emitted context contains only debug-relevant lines plus a
    ``# calls:`` dependency hint.  When *inline_hints* is provided, each
    entry-node block gets its helper hints appended (one per line).  File
    and class nodes without code contribute a comment stub only.
    """
    entry_set = set(entry_ids)
    kws = keywords or []
    hints = inline_hints or {}

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
            block = f"{header}\n{code}"
            if nid in hints:
                block += "\n" + "\n".join(hints[nid])
            parts.append(block)
        else:
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

    Pipeline: parse → entry-points → BFS traversal → rank (wide funnel) →
    **prune** (classify, dedup, inline) → compress → assemble.
    ``token_estimate_raw`` measures the pre-compression pre-prune baseline,
    so the CLI can report an end-to-end savings ratio.
    """
    parsed = parse_query(query)
    intent: str = parsed["intent"]
    keywords: list[str] = parsed["keywords"]

    nodes: list[Node] = graph["nodes"]
    edges: list[Edge] = graph["edges"]

    entry_ids = find_entry_points(keywords, nodes, edges)
    visited = traverse_graph(entry_ids, nodes, edges)
    ranked = rank_nodes(visited, entry_ids, nodes)

    # Narrow candidates into a minimal, categorised set.
    result: PruneResult = prune(ranked, entry_ids, visited)

    context_text = build_context(
        result.kept,
        entry_ids,
        keywords,
        compress=compress,
        inline_hints=result.inline_hints,
    )
    # Baseline: untouched candidates, no compression, no inlining.
    raw_text = build_context(ranked, entry_ids, keywords, compress=False)

    return QueryResult(
        intent=intent,
        keywords=keywords,
        entry_points=entry_ids,
        nodes_selected=[n["id"] for n in result.kept],
        categories=dict(result.categories),
        inline_hints=dict(result.inline_hints),
        token_estimate=estimate_tokens(context_text),
        token_estimate_raw=estimate_tokens(raw_text),
        context=context_text,
    )
