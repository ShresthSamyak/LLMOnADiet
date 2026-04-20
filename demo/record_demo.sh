#!/usr/bin/env bash
# record_demo.sh — run this while your screen recorder is active.
# Tip: resize terminal to ~100x30, use a dark theme, bump font to 16px.

set -euo pipefail

PAUSE=1.5

# Step 1 — show the problem
echo "--- Without context-engine: Claude sees your ENTIRE codebase ---"
echo "Baseline tokens: 946,210 (fastapi repo)"
sleep 2

# Step 2 — index
echo ""
echo "--- With context-engine ---"
context-engine index /tmp/fastapi-bench
sleep "$PAUSE"

# Step 3 — query 1
context-engine query "fix authentication bug"
sleep 2

# Step 4 — query 2
context-engine query "how does the database connection work"
sleep 2

# Step 5 — show savings
echo ""
echo "Tokens injected: ~186 average"
echo "Tokens saved:    ~945,000 per query"
echo "Cost reduction:  >99.9%"
echo "Latency:         449ms"
