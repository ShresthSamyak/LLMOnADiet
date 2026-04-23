# llm-diet

**Give Claude the right context upfront. Fewer turns, faster answers, lower cost.**

[![PyPI](https://img.shields.io/pypi/v/llm-diet)](https://pypi.org/project/llm-diet/)
[![License: MIT](https://img.shields.io/github/license/ShresthSamyak/LLM_DIET)](LICENSE)
[![Downloads](https://img.shields.io/badge/downloads-PyPI-brightgreen)](https://pypi.org/project/llm-diet/)

Deterministic context retrieval for AI coding tools. Parses your repo into a call graph, intercepts every file read Claude makes, and returns compressed versions — so Claude explores freely but cheaply.

---

## The Problem

Every Claude Code session starts blind. Claude explores your entire codebase before answering — reading files, listing directories, running commands. That exploration costs tokens and time.

```
Without llm-diet:
  Claude reads 10 files × 8,000 tokens = 80,000 tokens consumed
  Cost: $0.19 for a simple bug fix session

With llm-diet:
  Claude reads 10 files × 300 tokens  = 3,000 tokens consumed
  Cost: $0.025 for the same session
```

---

## How It Works

```
User prompt
│
▼
context-engine (call graph)
│  scores every function against your query
▼
Claude Code session opens
│
▼
Claude calls read_file("validators/amazon.py")
│
▼
llm-diet-shadow MCP server intercepts
│  returns compressed 872-token version
│  instead of raw 6,590-token file
▼
Claude answers — correctly — using compressed context
```

Claude thinks it explored. It did — but every read returned our compressed version, not the raw file.

---

## Benchmark

**Tested on coupon-hunter-poc (40-node Python project)**

| File | Original | Compressed | Reduction |
|------|----------|------------|-----------|
| validators/playwright_amazon.py | 6,590 chars | 872 chars | **86%** |
| orchestrator.py | 10,492 chars | 2,169 chars | **79%** |
| connectors/playwright_amazon.py | 3,067 chars | 631 chars | **79%** |
| openrouter_agent.py | 2,860 chars | 966 chars | **66%** |
| retailmenot_scraper.py | 2,705 chars | 960 chars | **64%** |
| normalizer.py | 1,074 chars | 555 chars | **48%** |

**Overall: 32,856 → 10,044 chars across all indexed files**
**69% reduction — ~5,700 tokens saved per full codebase read**

**Session cost comparison (same task, same codebase):**

| Mode | Cost | Tokens |
|------|------|--------|
| Plain `claude` (no llm-diet) | $0.19 | ~80,000 |
| `claude` with llm-diet | $0.035 | ~15,000 |
| `diet-run` (enforced mode) | $0.025 | ~10,000 |

**FastAPI benchmark (946-node project):**

| Query | Tokens without | Tokens injected | Reduction |
|-------|---------------|-----------------|-----------|
| fix error handling | 946,210 | 221 | >99.9% |
| add logging to pipeline | 946,210 | 136 | >99.9% |

---

## Quick Start

```bash
pip install llm-diet
cd your-project
mkdir .claude  # tells llm-diet Claude Code is present
context-engine install
```

Then open Claude Code normally:

```bash
claude
```

Or use enforced mode (blocks built-in Read tool, strict compression):

```bash
diet-run
```

---

## What context-engine install does

1. Builds a call graph of your entire codebase (Python, JS, TS, JSX, TSX)
2. Writes `.mcp.json` — registers the shadow MCP server
3. Writes `CLAUDE.md` — tells Claude to use Low Bandwidth Mode

One command. No config files to edit manually.

---

## The Shadow MCP Server

The shadow server is the core mechanism. It registers as an MCP server in `.mcp.json`. When Claude calls `read_file`, the shadow server intercepts it and returns:

- **HIT** (file is indexed): compressed call-graph version — signatures, first 8 lines of body, return/raise statements. Docstrings and comments stripped.
- **MISS** (file not indexed): raw file contents passed through unchanged
- **Binary files**: skipped with a one-line note
- **Large unindexed files** (>50k chars): truncated to 200 lines with an indexing suggestion

---

## diet-run (enforced mode)

```bash
diet-run                    # current directory
diet-run /path/to/project   # specific directory
```

Launches Claude Code with:
- `LLM_DIET_STRICT=1` — unindexed files return an error instead of raw content
- `--disallowed-tools Read,Bash,Glob,Grep` — filesystem exploration tools blocked
- Shadow server as the only file reader

Requires `.cecl/graph.json` and `.mcp.json` to exist (run `context-engine install` first).

---

## Platform Support

| Platform | Status |
|----------|--------|
| Claude Code | ✅ Full support — shadow MCP server, diet-run, CLAUDE.md |
| Cursor | ⚠️ Static rules file only (`.cursor/rules/`) |
| Windsurf | ⚠️ Static rules file only (`.windsurf/rules/`) |

---

## Why Not RAG?

RAG requires embeddings, a vector database, and an LLM call in the retrieval path. llm-diet uses AST call graph analysis — deterministic, zero LLM calls at retrieval time, same query always returns the same result.

---

## Commands

| Command | What it does |
|---------|-------------|
| `context-engine install` | Index codebase, write .mcp.json and CLAUDE.md |
| `context-engine index .` | Re-index after adding new files |
| `context-engine watch .` | Auto-reindex on file save |
| `diet-run` | Launch Claude Code in enforced Low Bandwidth Mode |
| `diet-mcp` | Start the shadow MCP server manually |
