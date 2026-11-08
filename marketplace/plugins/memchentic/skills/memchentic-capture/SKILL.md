---
name: memchentic-capture
description: >-
  memchentic capture playbook: remember vs guide_write, entry types, space
  defaults (personal vs admin API key), refuse list, and aggressive
  verified-insight capture when memchentic is connected.
---

# memchentic Capture

## Gate

Not connected/ready → skip saves; finish the primary task.

## When to save

Save autonomously after verification (usually **1–3**/turn when distinct
insights land) when any match:

- Non-obvious bug root cause + fix
- Architecture/decision + rationale + paths
- Failed approach + failure condition
- Stable cross-session preference or user correction
- Hard-won workflow or non-obvious host/MCP/env quirk
- Reusable “when X, do Y / avoid Z” guidance

Bias toward saving what you would otherwise rediscover. No save for routine
success narration or “task done” with no insight.

## Which tool

| Kind | Tool | Notes |
| --- | --- | --- |
| Durable record | `remember` | `type`: fact \| preference \| decision \| instruction \| feedback \| insight \| note |
| Reusable advice | `guide_write` | Duplicate content strengthens confidence |
| Patch | `revise` | Title/body/type/tags/expiry |
| Move | `relocate` | Target space must be writable |
| Delete | `forget` | Writable entries/guides only |

Where it lands (first match wins):

1. `space_id` — only when you already know the UUID.
2. `group: "<name>"` — exact name of one of your groups (see `me` memberships).
3. `visibility: "shared"` — your default shared group, or your only group.
4. Nothing → **personal**. When unsure who should see it, use personal.

Never invent a group name. On an ambiguous/unknown-group error, save personal
and mention it, or ask once. **Admin API key** has no personal default — pass
`space_id`. Prefer per-user API key or JWT for agent
sessions. Use `path_hint` / `repo_hint` for path-scoped insights. Near-
duplicates refuse unless `force`.

## Refuse

- Transient status, todos, raw logs, guesses, code-obvious facts, chat dumps
- Secrets, credentials, tokens, private personal data, unredacted payloads
- Client-supplied user ids / impersonation for ACL

## Hygiene

`overlap_report` is read-only. Merge via `revise` / `forget` — never
auto-delete.
