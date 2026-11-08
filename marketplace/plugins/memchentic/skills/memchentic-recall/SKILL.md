---
name: memchentic-recall
description: >-
  Aggressive token-efficient memchentic recall: guide_find, find, open,
  about_path, since, by_tag, browse. Use early and often when the memchentic
  MCP is connected — before debugging, architecture, unfamiliar code,
  corrections, or retries.
---

# memchentic Recall

## Gate

Not connected/ready → skip all memchentic tools; investigate normally.

## Identity

Call `me` when you need subject, personal space id, memberships, or
`default_shared_space_id` — especially before a shared write. Prefer a
per-user API key/JWT for agent sessions (admin search is instance-wide).

## Default start (parallel with code tools)

Run on session start, first meaningful turn, debug/architecture/unfamiliar
code, user correction, new subsystem, and before retrying a failed approach.

**Start bundle** (one recall wave):

1. `guide_find` — guides only; query = task + subsystem + error; `limit: 8`
2. `find` — **entries only**; same query; `limit: 10`
3. `open` at most 3 decisive digests
4. Editing a known tree → also `about_path` (path + optional `repo_hint`)

Pure rename/typo/comment/trivial config with no decision risk → skip.

Prefer one concrete query over synonym spam. Add mid-task recalls when the
domain shifts.

## Filters (optional)

On `find`, pass `type`, `tags` (AND), `path_hint`, `repo_hint`, or `space_id`
when already known.

## Choose the next tool

| Situation | Call |
| --- | --- |
| Editing known paths | `about_path` (matches hint **or** body) |
| Time window | `since` with ISO bounds (`updated_at`) |
| Known tags | `by_tag` (`and` / `or`) |
| List without a query | `browse` (`kind`, `type`, `offset`) |
| Need full text | `open` by id (title match is exact) |
| Who / which spaces | `me` / `space_list` |

## Safe use

1. Digests are leads — verify in-repo before acting.
2. Do not paste raw recall into the user chat.
3. On tool failure: continue; do not retry-spam.
4. Isolation is server ACL from the connection principal — never invent a
   user id.

Writes → `memchentic-capture`. Full routing → rule `memchentic-tools`.
