# llm-diet

[![PyPI version](https://img.shields.io/pypi/v/llm-diet)](https://pypi.org/project/llm-diet/)
[![Python 3.11+](https://img.shields.io/pypi/pyversions/llm-diet)](https://pypi.org/project/llm-diet/)
[![License: MIT](https://img.shields.io/github/license/ShresthSamyak/LLM_DIET)](LICENSE)
[![Downloads](https://img.shields.io/pypi/dm/llm-diet)](https://pypi.org/project/llm-diet/)

**Stop sending your entire codebase to Claude. Inject only what matters.**

Deterministic context retrieval for AI coding tools. Parses your repo into a call graph, scores every function against your query, and injects the top matches — before Claude starts reasoning. No embeddings, no vector DB, no LLM calls in the retrieval path.

## The Problem

Every prompt you send carries your entire codebase as dead weight:
```
Without llm-diet:  Your prompt + 946,210 tokens of codebase → Claude → answer
With llm-diet:     Your prompt +     186 tokens of context  → Claude → same answer
```

## Benchmark

**This repo (185 nodes, 46k tokens)**

| Query | Baseline | With llm-diet | Reduction | Time |
|-------|----------|---------------|-----------|------|
| fix authentication bug | 46,661 | 434 | 99.1% | 187ms |
| add a new API endpoint | 46,661 | 106 | 99.8% | 203ms |
| debug memory leak | 46,661 | 428 | 99.1% | 172ms |
| add logging to the pipeline | 46,661 | 58 | 99.9% | 141ms |

**46,661 → 275 tokens average. 176ms overhead.**

**FastAPI repo (946k tokens — repo never seen before)**

| Query | Baseline | With llm-diet | Reduction | Time |
|-------|----------|---------------|-----------|------|
| fix authentication bug | 946,210 | 87 | >99.9% | 359ms |
| add a new API endpoint | 946,210 | 120 | >99.9% | 359ms |
| how does the database connection work | 946,210 | 130 | >99.9% | 391ms |
| debug memory leak | 946,210 | 436 | >99.9% | 1062ms |
| add input validation | 946,210 | 244 | >99.9% | 375ms |
| explain the caching logic | 946,210 | 114 | >99.9% | 313ms |
| fix error handling | 946,210 | 221 | >99.9% | 406ms |
| add logging to the pipeline | 946,210 | 136 | >99.9% | 328ms |

**946,210 → 186 tokens average. 432ms overhead.**

## How It Works

```
repo files (.py, .js, .ts, .jsx, .tsx)
   ↓
AST parser  (no LLM — pure tree-sitter)
   ↓
call graph  (.cecl/graph.json)
   ↓
query  →  keyword expansion  →  BFS traversal  →  top 5 functions
   ↓
injected into Claude before reasoning starts
```
Same query + same graph = same result. Deterministic by design.

## Quick Start

```bash
pip install llm-diet
context-engine install    # indexes repo + configures your AI tool
# open Claude Code and start coding
```

## Commands

| Command | Description |
|---------|-------------|
| `context-engine install` | Index repo and configure Claude Code / Cursor / Windsurf |
| `context-engine index .` | (Re)build the call graph |
| `context-engine query "fix auth bug"` | See what would be injected for a query |
| `context-engine apply "add endpoint"` | Plan → diff → validate → patch (needs `ANTHROPIC_API_KEY`) |
| `context-engine watch .` | Auto-reindex on file save |

## Platform Support

| Platform | Integration | Token reduction |
|----------|-------------|-----------------|
| Claude Code | `UserPromptSubmit` hook — dynamic injection on every prompt | Full (186 tokens avg) |
| Cursor | Static rules file written to `.cursor/rules/` | Guides AI; no dynamic injection |
| Windsurf | Static rules file written to `.windsurf/rules/` | Guides AI; no dynamic injection |

Dynamic injection for Cursor and Windsurf is on the roadmap.

## Why Not RAG?

| | llm-diet | Embeddings / RAG | code-review-graph |
|-|----------|-----------------|-------------------|
| Retrieval method | AST + call graph | Vector similarity | AST + SQLite |
| LLM calls to retrieve | 0 | 1+ | 0 |
| Deterministic | Yes | No | Yes |
| Setup | `pip install` + `index` | Model + DB infra | `pip install` + `build` |
| Languages | Python, JS, TS, JSX, TSX | Any | 23 languages |
| Autonomous apply | Yes | No | No |
| Works offline | Yes | No | Yes |

We do less. What we do, we do surgically.

## Contributing

Good first issues:
- **Dynamic injection for Cursor/Windsurf** — extend beyond Claude Code's `UserPromptSubmit`
- **More language parsers** — add Go, Rust, Java following the `FileParseResult` interface in `parser.py`
- **Better keyword expansion** — improve domain-specific term mapping in `retrieval.py`

Open an issue or send a PR.

## License

MIT
