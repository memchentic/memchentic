#!/usr/bin/env bash
# SessionStart / sessionStart hook: inject memchentic recall+capture loop.
# No network. No secrets. Stdout shape depends on the host.
set -euo pipefail

# shellcheck disable=SC2016
CONTEXT='memchentic (always-on)

If the memchentic MCP is not connected/ready: do nothing. No invented tool calls.

When connected:
- Recall early: on the first meaningful turn, on corrections, on new subsystems, and after a failed approach, run guide_find (limit 8) and find (limit 10) in parallel. Use about_path when editing a known tree. open at most 3 decisive digests.
- After a verified durable insight: remember and/or guide_write (usually 1–3). Pick a real entry type (fact, preference, decision, instruction, feedback, insight, or note). Never store secrets.
- Omit space_id for personal. Shared writes use group, visibility="shared", or a known space_id.

Deeper tables stay in skills memchentic-recall, memchentic-capture, and memchentic-tools; this loop does not require loading them.'

export MEMCHENTIC_HOOK_CONTEXT="${CONTEXT}"

if [[ -n "${COPILOT_CLI:-}" ]]; then
  python3 -c 'import json, os; print(json.dumps({"additionalContext": os.environ["MEMCHENTIC_HOOK_CONTEXT"]}, ensure_ascii=False))'
else
  python3 -c 'import json, os; print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": os.environ["MEMCHENTIC_HOOK_CONTEXT"]}}, ensure_ascii=False))'
fi
