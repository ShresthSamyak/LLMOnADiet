# Context Engine Benchmark

Repo: `C:\Users\HP\AppData\Local\Temp\fastapi-bench`

| Query | Baseline Tokens | With CE Tokens | Reduction % | Nodes | Files Selected | Time (ms) |
|-------|----------------|----------------|-------------|-------|----------------|----------|
| fix authentication bug | 946,210 | 87 | 100.0% | 1 | tutorial001_an_py310.py | 359 |
| add a new API endpoint | 946,210 | 120 | 100.0% | 1 | api_key.py | 359 |
| how does the database connection work | 946,210 | 130 | 100.0% | 2 | tutorial001_an_py310.py, param_functions.py | 391 |
| debug memory leak | 946,210 | 436 | 100.0% | 5 | applications.py, tutorial002_an_py310.py, test_arbitrary_types.py, test_openapi_cache_root_path.py, test_security_scopes.py | 1062 |
| add input validation | 946,210 | 244 | 100.0% | 3 | utils.py, tutorial004_py310.py, tutorial002_an_py310.py | 375 |
| explain the caching logic | 946,210 | 114 | 100.0% | 1 | test_security_scopes_sub_dependency.py | 313 |
| fix error handling | 946,210 | 221 | 100.0% | 3 | tutorial003_py310.py, tutorial002_py310.py, tutorial001_py310.py | 406 |
| add logging to the pipeline | 946,210 | 136 | 100.0% | 1 | tutorial002_an_py310.py | 328 |

**Baseline** (all .py tokens): 946,210
**Avg injected tokens**: 186
**Avg reduction**: 100.0%
**Avg hook latency**: 449 ms
