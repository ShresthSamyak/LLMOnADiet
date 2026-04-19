from __future__ import annotations

import ast
import re

_STOPWORDS = frozenset({"a", "an", "the", "in", "to", "for", "of", "on", "at", "by", "is", "it"})

_ALIASES: dict[str, set[str]] = {
    "login": {"auth", "token", "password", "signin", "authenticate", "oauth", "bearer", "credential"},
    "auth": {"login", "token", "jwt", "authenticate", "signin", "oauth", "bearer", "permission"},
    "token": {"auth", "jwt", "access", "bearer", "refresh", "oauth", "session", "cookie"},
    "oauth": {"token", "auth", "bearer", "client", "scope", "grant"},
    "bug": {"fix", "error", "exception", "fail", "issue", "problem", "broken"},
    "fix": {"bug", "patch", "repair", "resolve", "correct"},
    "user": {"account", "profile", "member", "principal", "identity"},
    "create": {"add", "insert", "post", "new", "register"},
    "delete": {"remove", "drop", "destroy", "revoke"},
    "get": {"fetch", "read", "retrieve", "find", "load"},
    "update": {"patch", "edit", "modify", "refresh"},
    "validate": {"check", "verify", "assert", "sanitize"},
    "hash": {"password", "bcrypt", "crypt", "digest", "salt"},
    "db": {"database", "session", "repo", "repository", "store"},
    "error": {"exception", "failure", "traceback", "bug", "crash"},
}

_AUTH_MODULE_KEYWORDS = frozenset({
    "auth", "oauth", "token", "jwt", "security", "permission",
    "credential", "login", "session", "middleware",
})


def _is_test_file(fpath: str) -> bool:
    p = fpath.replace("\\", "/").lower()
    name = p.split("/")[-1]
    return name.startswith("test_") or name.endswith("_test.py") or "/test" in p or "/tests/" in p


def _enrich(nid: str, raw: dict, calls_map: dict, callers_map: dict) -> dict:
    node = dict(raw)
    node["name"] = nid.split(":")[-1]
    node["calls"] = [dst.split(":")[-1] for dst in calls_map.get(nid, [])]
    node["callers"] = [src.split(":")[-1] for src in callers_map.get(nid, [])]
    return node


def resolve_nodes(selected_ids: list[str], graph: dict) -> list[dict]:
    """
    Resolve node IDs to enriched dicts. When test-file nodes are in the pool,
    automatically pulls in the implementation nodes they call.
    """
    by_id: dict[str, dict] = {n["id"]: dict(n) for n in graph.get("nodes", []) if "id" in n}

    # Track full destination IDs (not just names) to enable cross-file expansion
    calls_map: dict[str, list[str]] = {}   # src_id -> [dst_id, ...]
    callers_map: dict[str, list[str]] = {} # dst_id -> [src_id, ...]
    for edge in graph.get("edges", []):
        if edge.get("type") == "calls":
            src, dst = edge["from"], edge["to"]
            calls_map.setdefault(src, []).append(dst)
            callers_map.setdefault(dst, []).append(src)

    resolved: dict[str, dict] = {}
    for nid in selected_ids:
        raw = by_id.get(nid)
        if not raw:
            continue
        resolved[nid] = _enrich(nid, raw, calls_map, callers_map)

    # For test-file nodes: follow calls into implementation files
    impl_ids: list[str] = []
    for nid, node in list(resolved.items()):
        if not _is_test_file(node.get("file", "")):
            continue
        for dst_id in calls_map.get(nid, []):
            if dst_id not in resolved and not _is_test_file(by_id.get(dst_id, {}).get("file", "")):
                impl_ids.append(dst_id)

    for nid in impl_ids:
        raw = by_id.get(nid)
        if raw:
            resolved[nid] = _enrich(nid, raw, calls_map, callers_map)

    # Narrowness fallback: if all nodes share one file, add their callees
    files = {n.get("file") for n in resolved.values()}
    if len(files) == 1:
        extra_ids = []
        for nid, node in list(resolved.items()):
            for dst_id in calls_map.get(nid, []):
                if dst_id not in resolved and by_id.get(dst_id, {}).get("type") in ("function", "method"):
                    extra_ids.append(dst_id)
                    if len(extra_ids) >= 4:
                        break
            if len(extra_ids) >= 4:
                break
        for nid in extra_ids:
            raw = by_id.get(nid)
            if raw:
                resolved[nid] = _enrich(nid, raw, calls_map, callers_map)

    return list(resolved.values())


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


def _extract_docstring(code: str) -> str:
    """Return first docstring text from a function, or empty string."""
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    return node.body[0].value.value.lower()
    except SyntaxError:
        pass
    return ""


def _score(node: dict, query_tokens: set[str], alias_tokens: set[str]) -> int:
    if node.get("type") not in ("function", "method"):
        return 0
    name = node.get("name", "").lower()
    fpath = node.get("file", "").lower()
    code = node.get("code", "").lower()
    name_tokens = _tokens(name)
    docstring = _extract_docstring(node.get("code", ""))

    score = 0
    # Exact / full token match
    if name in query_tokens or name_tokens == query_tokens:
        score += 3
    # Token overlap on name
    score += len(query_tokens & name_tokens) * 2
    # Alias token overlap on name
    score += len((alias_tokens - query_tokens) & name_tokens)
    # Substring: any alias token appears inside function name
    for t in alias_tokens:
        if len(t) >= 3 and t in name:
            score += 1
    # File path: query OR alias token in path
    if any(k in fpath for k in alias_tokens):
        score += 2
    # Auth-module priority boost
    if any(k in fpath for k in _AUTH_MODULE_KEYWORDS):
        score += 1
    # Docstring match against alias tokens (catches semantic descriptions)
    for t in alias_tokens:
        if len(t) >= 4 and t in docstring:
            score += 1
            break
    # Body match: alias tokens in code (covers "/token" endpoints, string literals)
    body_hits = sum(1 for t in alias_tokens if len(t) >= 4 and t in code)
    score += min(body_hits, 2)
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


_ENDPOINT_PATTERNS = re.compile(
    r'@\w*\.(get|post|put|patch|delete|route)\s*\(["\']([^"\']+)["\']'
    r'|@app\.(get|post|put|patch|delete)\s*\(["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_AUTH_VERBS = frozenset({
    "verify", "authenticate", "authorize", "validate", "decode",
    "check", "require", "permission", "login", "signin",
})


def _is_endpoint(node: dict) -> bool:
    code = node.get("code", "")
    return bool(_ENDPOINT_PATTERNS.search(code))


def _is_auth_fn(node: dict) -> bool:
    name_tokens = _tokens(node.get("name", ""))
    return bool(name_tokens & _AUTH_VERBS)


def _is_helper(node: dict) -> bool:
    name = node.get("name", "")
    return name.startswith("_") or any(
        t in name.lower() for t in ("helper", "util", "get_", "fetch_", "build_", "make_")
    )


def _coverage_select(scored: list[tuple[int, dict]], top_n: int) -> list[dict]:
    """
    Coverage-aware selection: guarantee at least one endpoint, one auth function,
    one helper/dependency. Fill remaining slots with top-scored diverse nodes.
    Max 2 nodes per file.
    """
    slots: dict[str, dict | None] = {"endpoint": None, "auth": None, "helper": None}
    remaining: list[tuple[int, dict]] = []

    for s, node in scored:
        if slots["endpoint"] is None and _is_endpoint(node):
            slots["endpoint"] = node
        elif slots["auth"] is None and _is_auth_fn(node):
            slots["auth"] = node
        elif slots["helper"] is None and _is_helper(node):
            slots["helper"] = node
        else:
            remaining.append((s, node))

    selected: list[dict] = [n for n in slots.values() if n is not None]
    seen_names = {n.get("name") for n in selected}
    file_counts: dict[str, int] = {}
    for n in selected:
        f = n.get("file", "")
        file_counts[f] = file_counts.get(f, 0) + 1

    for _, node in remaining:
        if len(selected) >= top_n:
            break
        name = node.get("name")
        f = node.get("file", "")
        if name in seen_names:
            continue
        if file_counts.get(f, 0) >= 2:
            continue
        selected.append(node)
        seen_names.add(name)
        file_counts[f] = file_counts.get(f, 0) + 1

    return selected


def _fallback_select(nodes: list[dict], top_n: int) -> list[dict]:
    fn_nodes = [n for n in nodes if n.get("type") in ("function", "method")]
    ranked = sorted(
        fn_nodes,
        key=lambda n: len(n.get("calls", [])) + len(n.get("callers", [])),
        reverse=True,
    )
    # Apply file diversity even in fallback
    seen_files: dict[str, int] = {}
    result = []
    for n in ranked:
        f = n.get("file", "")
        if seen_files.get(f, 0) < 2:
            result.append(n)
            seen_files[f] = seen_files.get(f, 0) + 1
        if len(result) >= top_n:
            break
    return result


def rank_and_select(nodes: list[dict], query: str, top_n: int = 4) -> list[dict]:
    query_tokens = _tokens(query)
    alias_tokens = _alias_tokens(query_tokens)

    scored: list[tuple[int, dict]] = []
    for node in nodes:
        s = _score(node, query_tokens, alias_tokens)
        if s == 0:
            continue
        scored.append((s, node))
    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return _fallback_select(nodes, top_n)

    # Expand pool by 1-hop before coverage selection
    top_names = {n.get("name") for _, n in scored[:top_n]}
    by_name = {n.get("name"): n for n in nodes if n.get("name")}
    additions: list[tuple[int, dict]] = []
    caller_count = 0

    for _, node in scored[:top_n]:
        for callee in node.get("calls", []):
            if callee and callee not in top_names and callee in by_name:
                s = _score(by_name[callee], query_tokens, alias_tokens)
                if s >= 2:
                    additions.append((s, by_name[callee]))
                    top_names.add(callee)
        for caller in node.get("callers", []):
            if caller_count >= 2:
                break
            if caller and caller not in top_names and caller in by_name:
                s = _score(by_name[caller], query_tokens, alias_tokens)
                if s >= 2:
                    additions.append((s, by_name[caller]))
                    top_names.add(caller)
                    caller_count += 1

    all_scored = sorted(scored + additions, key=lambda x: x[0], reverse=True)

    # Dedup
    seen: set[str] = set()
    deduped: list[tuple[int, dict]] = []
    for s, n in all_scored:
        name = n.get("name")
        if name and name not in seen:
            seen.add(name)
            deduped.append((s, n))

    return _coverage_select(deduped, top_n)


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
