import json
import sys

payload = json.load(sys.stdin)

# ALWAYS print tool name visibly
print(json.dumps({
    "permissionDecision": "deny",
    "permissionDecisionReason": f"BLOCKED TOOL: {payload.get('tool_name', 'UNKNOWN')}"
}))

sys.exit(0)
