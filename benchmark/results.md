# Context Engine Benchmark

Repo: `C:\Users\HP\Documents\llmemory tree`

| Query | Baseline Tokens | With CE Tokens | Reduction % | Nodes | Files Selected | Time (ms) |
|-------|----------------|----------------|-------------|-------|----------------|----------|
| fix authentication bug | 46,661 | 434 | 99.1% | 3 | intent.py, validator.py, patcher.py | 187 |
| add a new API endpoint | 46,661 | 106 | 99.8% | 1 | ranker.py | 203 |
| how does the database connection work | 46,661 | 278 | 99.4% | 2 | intent.py, retrieval.py | 219 |
| debug memory leak | 46,661 | 428 | 99.1% | 3 | intent.py, cli.py, compressor.py | 172 |
| add input validation | 46,661 | 0 | 100.0% | 0 | no match in this repo (expected for repos without validation logic) | 125 |
| explain the caching logic | 46,661 | 418 | 99.1% | 3 | intent.py, pruner.py, retrieval.py | 172 |
| fix error handling | 46,661 | 479 | 99.0% | 3 | intent.py, validator.py, patcher.py | 187 |
| add logging to the pipeline | 46,661 | 58 | 99.9% | 1 | cli.py | 141 |

**Baseline** (all .py tokens): 46,661
**Avg injected tokens**: 275
**Avg reduction**: 99.4%
**Avg hook latency**: 176 ms
