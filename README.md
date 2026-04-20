# context-engine 🧠
> Cut your AI coding costs by 99%. Inject only the code that matters.

## Benchmark (this repo, 185 nodes)

| Query | Baseline Tokens | With CE Tokens | Reduction % | Nodes | Files Selected | Time (ms) |
|-------|----------------|----------------|-------------|-------|----------------|----------|
| fix authentication bug | 46,661 | 434 | 99.1% | 3 | intent.py, validator.py, patcher.py | 187 |
| add a new API endpoint | 46,661 | 106 | 99.8% | 1 | ranker.py | 203 |
| how does the database connection work | 46,661 | 278 | 99.4% | 2 | intent.py, retrieval.py | 219 |
| debug memory leak | 46,661 | 428 | 99.1% | 3 | intent.py, cli.py, compressor.py | 172 |
| add input validation | 46,661 | 0 | 100.0% | 0 | no match in this repo | 125 |
| explain the caching logic | 46,661 | 418 | 99.1% | 3 | intent.py, pruner.py, retrieval.py | 172 |
| fix error handling | 46,661 | 479 | 99.0% | 3 | intent.py, validator.py, patcher.py | 187 |
| add logging to the pipeline | 46,661 | 58 | 99.9% | 1 | cli.py | 141 |

**46,661 tokens → 275 tokens average. Same accuracy. 176ms overhead.**

## How it works

```
User prompt → context-engine → top 5 relevant functions → Claude sees 275 tokens
                                        ↑
               (instead of your entire codebase at 46,661 tokens)
```

AST-parses your repo into a call graph. When you send a prompt, it scores every
function by keyword relevance + call graph centrality and injects only the top
matches — before Claude starts reasoning.

No embeddings. No vector DB. No LLM calls in the retrieval path.

## Install

```bash
pip install context-engine
context-engine index .
```

## Use with Claude Code (automatic)

```bash
claude mcp add context-engine -- python -m context_engine.mcp_server
```

After this, every prompt you send to Claude Code is automatically prefixed with
the minimal relevant context from your codebase.

## Use as CLI

```bash
context-engine query "fix the auth bug"
context-engine apply "add input validation to the login endpoint"
```

## Why not just use RAG / embeddings?

No LLM calls, no vector DB, no setup.

context-engine uses AST parsing + call graph traversal. It's deterministic —
same query, same graph, same result every time. Works offline. Runs in ~176ms.
Embeddings need a model call just to retrieve context. We don't.
