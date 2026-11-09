---
name: memchentic-capture
description: >-
  memchentic capture playbook: remember vs guide_write, entry types, write
  targets (space_id / group / visibility / personal), space defaults, refuse
  list, and aggressive verified-insight capture when memchentic is connected.
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
| Durable record | `remember` | Pass a closed `type` (below); default is `note` |
| Reusable advice | `guide_write` | Near-dup **upserts** and raises confidence (no `force`) |
| Patch | `revise` | Title/body/type/tags/expiry/path hints |
| Move | `relocate` | Explicit destination `space_id` UUID only |
| Delete | `forget` | Writable entries or guides |

**Entry vs guide vs both**

- Durable record only → `remember`.
- Reusable “when X, do Y / avoid Z” → `guide_write`.
- Same insight is both a fact **and** a standing rule → write **both**.

## Entry types (closed)

Pick the best label on every `remember`. Do **not** leave the API default
`note` when a better type fits. Types are classification metadata — no
separate server pipeline per type.

| Type | Use when |
| --- | --- |
| `fact` | Stable truth about the world or codebase |
| `preference` | How someone wants work done |
| `decision` | A choice and its rationale |
| `instruction` | Standing directive the agent should follow |
| `feedback` | Correction after a mistake |
| `insight` | Learned non-obvious finding |
| `note` | Catch-all when nothing else fits |

**Must-save → type (pick one)**

| Insight | Prefer |
| --- | --- |
| Stable “this is how it is” | `fact` |
| How the user wants work done | `preference` |
| “We decided …” + rationale | `decision` |
| Standing always/never directive | `instruction` |
| Correction after the agent erred | `feedback` |
| Bug root cause + fix / hard-won quirk | `insight` |
| None of the above | `note` |

Guides are **not** an entry type — use `guide_write` for reusable advice.

## Write target

First match wins (case-insensitive exact group name; never invent a name):

1. `space_id` — only when you already know the UUID.
2. `group: "<name>"` — exact name of one of your groups (see `me` memberships).
3. `visibility: "shared"` — your default shared group, or your only group;
   otherwise error (save personal and mention it, or ask once).
4. Nothing → **personal**. When unsure who should see it, use personal.

Call `me` before a shared write when memberships or
`default_shared_space_id` are unknown. Default shared group is set via REST /
GUI (`PATCH /api/me`), not MCP.

`ADMIN_API_KEY` is a real `ADMIN_EMAIL` user: omit-space → that user’s
**personal** space; `space_create` is allowed. Prefer a **per-user API key or
JWT** for normal agent sessions — admin list/search is instance-wide.

## Remember-only knobs

- `path_hint` / `repo_hint` — path-scoped insights for `about_path` / `find`
  (not on `guide_write`).
- `force` — override near-dup refusal on **`remember` only**. `guide_write`
  near-dup upserts; do not invent `force` for guides.
- `tags`, `ttl_days` / `permanent` / `expires_at` — optional on `remember`
  (`ttl_days` / `permanent` also on `guide_write`); `revise` can `clear_expiry`.

## Refuse

- Transient status, todos, raw logs, guesses, code-obvious facts, chat dumps
- Secrets, credentials, tokens, private personal data, unredacted payloads
- Client-supplied user ids / impersonation for ACL

## Lifetime (sliding TTL)

New entries and guides expire after the server default (90 days) unless you
choose otherwise. Every recall (a `find` / `guide_find` hit or an `open`)
renews a live memory, so memories people keep using stay; unused ones fade
out of recall.

| Memory | Pass |
| --- | --- |
| Session scratch, short-lived workaround | `ttl_days: 7`–`14` |
| Ordinary fact, insight, decision | omit (default) |
| Quarter-scoped plan or decision | `ttl_days: 180` |
| Standing policy / always-true fact | `permanent: true` (prefer a guide for advice) |

## Hygiene

`overlap_report` is read-only (same-title and cosine near-dup pairs;
`conflicts` may be empty). Merge via `revise` / `forget` — never auto-delete.
Default recall hides past-due `expires_at` rows (they are not deleted); use
`include_expired: true` to audit, and clear or extend via `revise`.
