from __future__ import annotations

import ast
import re

_STOPWORDS = frozenset({"a", "an", "the", "in", "to", "for", "of", "on", "at", "by", "is", "it"})

_ALIASES: dict[str, set[str]] = {
    "login": {"auth", "token", "password", "signin", "authenticate"},
    "auth": {"login", "token", "jwt", "authenticate", "signin"},
    "token": {"auth", "jwt", "access", "bearer", "refresh"},
    "user": {"account", "profile", "member"},
    "create": {"add", "insert", "post", "new"},
    "delete": {"remove", "drop", "destroy"},
    "get": {"fetch", "read", "retrieve", "find"},
    "update": {"patch", "edit", "modify"},
    "validate": {"check", "verify", "assert"},
    "hash": {"password", "bcrypt", "crypt"},
    "db": {"database", "session", "repo", "repository"},
}


def resolve_nodes(selected_ids: list[str], graph: dict) -> list[dict]:
    """
    Convert nodes_selected string IDs to enriched node dicts with
    name, calls, and callers fields populated from the graph.
    """
    by_id: dict[str, dict] = {n["id"]: dict(n) for n in graph.get("nodes", []) if "id" in n}

    calls_map: dict[str, list[str]] = {}
    callers_map: dict[str, list[str]] = {}
    for edge in graph.get("edges", []):
        if edge.get("type") == "calls":
            src, dst = edge["from"], edge["to"]
            calls_map.setdefault(src, []).append(dst.split(":")[-1])
            callers_map.setdefault(dst, []).append(src.split(":")[-1])

    result = []
    for nid in selected_ids:
        node = by_id.get(nid)
        if not node:
            continue
        node["name"] = nid.split(":")[-1]
        node["calls"] = calls_map.get(nid, [])
        node["callers"] = callers_map.get(nid, [])
        result.append(node)
    return result


def _tokens(text: str) -> set[str]:
    return set(re.split(r"[_\W]+", text.lower())) - _STOPWORDS - {""}


def _alias_tokens(query_tokens: set[str]) -> set[str]:
    expanded = set(query_tokens)
    for t in query_tokens:
        expanded |= _ALIASES.get(t, set())
    return expanded


def _short_path(fpath: str) -> str:
    parts = fpath.replace("\\", "/").split("/")
    return parts[-1] if parts else fpath


def _score(node: dict, query_tokens: set[str], alias_tokens: set[str]) -> int:
    if node.get("type") not in ("function", "method"):
        return 0
    name = node.get("name", "")
    fpath = node.get("file", "").lower()
    name_tokens = _tokens(name)

    score = 0
    if name.lower() in query_tokens or name_tokens == query_tokens:
        score += 3
    score += len(query_tokens & name_tokens) * 2
    score += len((alias_tokens - query_tokens) & name_tokens)
    if any(k in fpath for k in query_tokens):
        score += 2
    return score


def _strip_comments(code: str) -> str:
    lines = []
    for line in code.splitlines():
        cleaned = re.sub(r'\s*#.*$', "", line).rstrip()
        if cleaned.strip():
            lines.append(cleaned)
    return "\n".join(lines)


def _strip_docstrings(code: str) -> str:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body.pop(0)
    return code


def _compress(code: str, max_lines: int = 18) -> str:
    code = _strip_docstrings(code)
    code = _strip_comments(code)
    lines = [l for l in code.splitlines() if l.strip()]
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["    ..."]
    return "\n".join(lines)


def rank_and_select(nodes: list[dict], query: str, top_n: int = 3) -> list[dict]:
    query_tokens = _tokens(query)
    alias_tokens = _alias_tokens(query_tokens)

    scored = []
    for node in nodes:
        s = _score(node, query_tokens, alias_tokens)
        if s == 0:
            continue
        scored.append((s, node))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [n for _, n in scored[:top_n]]

    if not top:
        return []

    top_names = {n.get("name") for n in top}
    by_name = {n.get("name"): n for n in nodes if n.get("name")}
    additions: list[dict] = []
    caller_count = 0

    for node in top:
        for callee in node.get("calls", []):
            if callee and callee not in top_names and callee in by_name:
                if _score(by_name[callee], query_tokens, alias_tokens) >= 2:
                    additions.append(by_name[callee])
                    top_names.add(callee)
        for caller in node.get("callers", []):
            if caller_count >= 2:
                break
            if caller and caller not in top_names and caller in by_name:
                if _score(by_name[caller], query_tokens, alias_tokens) >= 2:
                    additions.append(by_name[caller])
                    top_names.add(caller)
                    caller_count += 1

    seen: set[str] = set()
    unique: list[dict] = []
    for n in top + additions:
        name = n.get("name")
        if name and name not in seen:
            seen.add(name)
            unique.append(n)

    re_scored = sorted(
        unique,
        key=lambda n: _score(n, query_tokens, alias_tokens),
        reverse=True,
    )
    return re_scored[:top_n]


def format_output(_query: str, nodes: list[dict]) -> str:
    if not nodes:
        return "NO_CONTEXT_FOUND"

    by_file: dict[str, list[dict]] = {}
    for node in nodes:
        key = _short_path(node.get("file", "unknown"))
        by_file.setdefault(key, []).append(node)

    blocks: list[str] = []
    total = 0

    for fname, fnodes in by_file.items():
        blocks.append(f"[{fname}]")
        for node in fnodes:
            compressed = _compress(node.get("code", ""))
            lines = compressed.splitlines()
            if total + len(lines) > 55:
                remaining = 55 - total
                if remaining <= 0:
                    break
                lines = lines[:remaining] + ["    ..."]
            blocks.extend(lines)
            blocks.append("")
            total += len(lines) + 1

    return "\n".join(l for l in blocks if l.strip()).strip()
