from __future__ import annotations

import ast
import re

_STOPWORDS = frozenset({"a", "an", "the", "in", "to", "for", "of", "on", "at", "by", "is", "it"})


def _tokens(text: str) -> set[str]:
    return set(re.split(r"[_\W]+", text.lower())) - _STOPWORDS - {""}


def _short_path(fpath: str) -> str:
    parts = fpath.replace("\\", "/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else fpath


def _score(node: dict, query_tokens: set[str], peer_files: set[str]) -> int:
    name = node.get("name", "")
    name_tokens = _tokens(name)
    score = 0
    if name.lower() in query_tokens or name_tokens == query_tokens:
        score += 3
    score += len(query_tokens & name_tokens) * 2
    if node.get("file") in peer_files:
        score += 1
    return score


def _select_top(nodes: list[dict], query_tokens: set[str], n: int = 5) -> list[dict]:
    peer_files: set[str] = set()
    scored = []
    for node in nodes:
        s = _score(node, query_tokens, peer_files)
        if s > 0:
            scored.append((s, node))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [node for _, node in scored[:n]]
    return top


def _expand_one_hop(
    top: list[dict],
    all_nodes: list[dict],
    query_tokens: set[str],
    max_callers: int = 2,
) -> list[dict]:
    top_names = {n.get("name") for n in top}
    by_name = {n.get("name"): n for n in all_nodes if n.get("name")}

    additions: list[dict] = []
    caller_count = 0

    for node in top:
        for callee in node.get("calls", []):
            if callee and callee not in top_names and callee in by_name:
                additions.append(by_name[callee])
                top_names.add(callee)
        for caller in node.get("callers", []):
            if caller_count >= max_callers:
                break
            if caller and caller not in top_names and caller in by_name:
                additions.append(by_name[caller])
                top_names.add(caller)
                caller_count += 1

    combined = top + additions
    peer_files = {n.get("file") for n in top}

    re_scored = sorted(
        combined,
        key=lambda n: _score(n, query_tokens, peer_files),  # type: ignore[arg-type]
        reverse=True,
    )
    return re_scored[:5]


def _strip_code(code: str) -> str:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return _strip_comments_regex(code)

    # Remove docstrings
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body.pop(0)

    lines = code.splitlines()
    out = _strip_comments_regex("\n".join(lines))
    return out


def _strip_comments_regex(code: str) -> str:
    lines = []
    for line in code.splitlines():
        stripped = line.rstrip()
        # Remove inline and full-line comments but keep string content
        cleaned = re.sub(r'\s*#[^"\']*$', "", stripped)
        if cleaned.strip():
            lines.append(cleaned)
    return "\n".join(lines)


def _truncate_body(code: str, max_lines: int = 20) -> str:
    lines = code.splitlines()
    if len(lines) <= max_lines:
        return code
    return "\n".join(lines[:max_lines]) + "\n    ..."


def rank_and_select(all_nodes: list[dict], query: str) -> list[dict]:
    query_tokens = _tokens(query)
    top = _select_top(all_nodes, query_tokens)
    if not top:
        return []
    return _expand_one_hop(top, all_nodes, query_tokens)


def format_output(_query: str, nodes: list[dict]) -> str:
    if not nodes:
        return "NO_CONTEXT_FOUND"

    by_file: dict[str, list[dict]] = {}
    for node in nodes:
        f = _short_path(node.get("file", "unknown"))
        by_file.setdefault(f, []).append(node)

    entry_lines = [f"* {n.get('name')} ({_short_path(n.get('file', ''))})" for n in nodes]

    code_blocks: list[str] = []
    total_lines = 0

    for fpath, fnodes in by_file.items():
        code_blocks.append(f"[{fpath}]")
        for node in fnodes:
            raw = node.get("code", "")
            stripped = _strip_code(raw)
            truncated = _truncate_body(stripped)
            block_lines = truncated.splitlines()
            if total_lines + len(block_lines) > 90:
                remaining = 90 - total_lines
                if remaining <= 0:
                    break
                block_lines = block_lines[:remaining] + ["    ..."]
            code_blocks.extend(block_lines)
            code_blocks.append("")
            total_lines += len(block_lines) + 1

    return (
        "### Entry Points\n"
        + "\n".join(entry_lines)
        + "\n\n### Relevant Code\n"
        + "\n".join(code_blocks).rstrip()
    )
