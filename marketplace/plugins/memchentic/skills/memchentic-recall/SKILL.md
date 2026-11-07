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

## Default start (parallel with code tools)

Run on session start, first meaningful turn, debug/architecture/unfamiliar
code, user correction, new subsystem, and before retrying a failed approach:

1. `guide_find` — query = task + subsystem + error; `limit: 8`
2. `find` — same query; `limit: 10`; `open` at most 3 decisive digests

Pure rename/typo/comment/trivial config with no decision risk → skip.

About **2–4** recall calls at task start is fine; add mid-task recalls when
the domain shifts. One concrete query beats several vague ones.

## Choose the next tool

| Situation | Call |
| --- | --- |
| Editing known paths | `about_path` with path (+ optional `repo_hint`) |
| Time window | `since` |
| Known tags | `by_tag` |
| List without a query | `browse` |
| Need full text | `open` by id (or title + `space_id`) |
| Who / which spaces | `me` / `space_list` |

## Safe use

1. Digests are leads — verify in-repo before acting.
2. Do not paste raw recall into the user chat.
3. On tool failure: continue; do not retry-spam.
4. Isolation is server ACL from the connection principal — never invent a
   user id.

Writes → `memchentic-capture`. Full routing → rule `memchentic-tools`.
