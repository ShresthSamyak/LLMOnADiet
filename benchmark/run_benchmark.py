#!/usr/bin/env python3
"""
Benchmark: measures token injection cost of the context-engine hook vs. the
naive baseline of sending every .py file to the model.

Usage:
    python benchmark/run_benchmark.py --repo .
    python benchmark/run_benchmark.py --repo /path/to/other/repo
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

QUERIES = [
    "fix authentication bug",
    "add a new API endpoint",
    "how does the database connection work",
    "debug memory leak",
    "add input validation",
    "explain the caching logic",
    "fix error handling",
    "add logging to the pipeline",
]

_IGNORE_DIRS = frozenset({"venv", ".venv", "__pycache__", ".git", "node_modules", ".cecl"})

_HOOK = Path(__file__).resolve().parent.parent / "context_engine" / "hooks" / "user_prompt_submit.py"


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def index_repo(repo: Path) -> None:
    print(f"Indexing {repo} …", flush=True)
    result = subprocess.run(
        ["context-engine", "index", str(repo)],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  [warn] index exited {result.returncode}: {result.stderr.strip()}", file=sys.stderr)
    else:
        print("  done.", flush=True)


# ---------------------------------------------------------------------------
# Hook runner
# ---------------------------------------------------------------------------

def _parse_hook_output(raw: str) -> tuple[str, int, list[str]]:
    """Return (context, tokens, files_list) from raw hook stdout."""
    if not raw.strip():
        return "", 0, []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "", 0, []

    context: str = data.get("additionalContext", "")
    tokens = len(context) // 4

    # Parse [filename.py] markers that open each block
    files: list[str] = []
    for block in context.split("\n\n"):
        first = block.strip().splitlines()[0] if block.strip() else ""
        if first.startswith("[") and first.endswith("]"):
            fname = first[1:-1]
            if fname not in files:
                files.append(fname)

    return context, tokens, files


def run_hook(query: str, repo: Path) -> dict:
    payload = json.dumps({"prompt": query})
    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        cwd=repo,
    )
    elapsed_ms = (time.monotonic() - t0) * 1000

    context, tokens, files = _parse_hook_output(proc.stdout)
    nodes = len(files)

    return {
        "tokens": tokens,
        "nodes": nodes,
        "files": files,
        "elapsed_ms": round(elapsed_ms),
        "context": context,
    }


# ---------------------------------------------------------------------------
# Baseline: total tokens across all .py files
# ---------------------------------------------------------------------------

def baseline_tokens(repo: Path) -> int:
    total = 0
    for py_file in repo.rglob("*.py"):
        if any(part in _IGNORE_DIRS for part in py_file.parts):
            continue
        try:
            total += len(py_file.read_text(encoding="utf-8", errors="ignore")) // 4
        except OSError:
            pass
    return total


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _pct(baseline: int, with_ce: int) -> str:
    if baseline == 0:
        return "n/a"
    reduction = 100.0 * (baseline - with_ce) / baseline
    return f"{reduction:.1f}%"


def _files_cell(files: list[str]) -> str:
    return ", ".join(files) if files else "—"


def build_markdown_table(baseline: int, rows: list[dict]) -> str:
    header = (
        "| Query | Baseline Tokens | With CE Tokens | Reduction % "
        "| Nodes | Files Selected | Time (ms) |\n"
        "|-------|----------------|----------------|-------------|"
        "-------|----------------|----------|\n"
    )
    lines = [header]
    for r in rows:
        lines.append(
            f"| {r['query']} "
            f"| {baseline:,} "
            f"| {r['tokens']:,} "
            f"| {_pct(baseline, r['tokens'])} "
            f"| {r['nodes']} "
            f"| {_files_cell(r['files'])} "
            f"| {r['elapsed_ms']} |\n"
        )
    return "".join(lines)


def build_summary(baseline: int, rows: list[dict]) -> str:
    avg_tokens = sum(r["tokens"] for r in rows) / len(rows)
    avg_ms = sum(r["elapsed_ms"] for r in rows) / len(rows)
    avg_reduction = 100.0 * (baseline - avg_tokens) / baseline if baseline else 0
    return (
        f"\n**Baseline** (all .py tokens): {baseline:,}\n"
        f"**Avg injected tokens**: {avg_tokens:.0f}\n"
        f"**Avg reduction**: {avg_reduction:.1f}%\n"
        f"**Avg hook latency**: {avg_ms:.0f} ms\n"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark context-engine hook injection.")
    parser.add_argument("--repo", type=Path, default=Path("."), help="Repo to index and query (default: .)")
    parser.add_argument("--skip-index", action="store_true", help="Skip re-indexing (use existing .cecl/graph.json)")
    args = parser.parse_args()

    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"Error: {repo} is not a directory.", file=sys.stderr)
        sys.exit(1)

    if not args.skip_index:
        index_repo(repo)

    print(f"\nCounting baseline tokens in {repo} …", flush=True)
    baseline = baseline_tokens(repo)
    print(f"  Baseline: {baseline:,} tokens across all .py files\n")

    rows: list[dict] = []
    for query in QUERIES:
        print(f"  querying: {repr(query)}", flush=True)
        result = run_hook(query, repo)
        result["query"] = query
        rows.append(result)
        print(
            f"    -> {result['nodes']} node(s), {result['tokens']} tokens, "
            f"{_pct(baseline, result['tokens'])} reduction, {result['elapsed_ms']} ms"
        )

    table = build_markdown_table(baseline, rows)
    summary = build_summary(baseline, rows)

    print("\n" + "-" * 72)
    print(table)
    print(summary)

    # Save to benchmark/results.md
    out_dir = Path(__file__).parent
    results_path = out_dir / "results.md"
    results_path.write_text(
        f"# Context Engine Benchmark\n\nRepo: `{repo}`\n\n"
        + table
        + summary,
        encoding="utf-8",
    )
    print(f"Results saved to {results_path}")


if __name__ == "__main__":
    main()
