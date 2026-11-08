# MCP tools

Memchentic exposes **18** native MCP tools. REST routes share the same
`memchentic.services` layer — no divergent logic. Short verbs, one job per
tool, progressive disclosure (search → digests, `open` → full body).

Mount path: `/mcp` on the application listener (default host `MCP_PORT`
18082).

## Mental model

```text
you (me)
  ├── personal        private entries/guides
  └── groups[]        shared spaces you belong to
        └── entries / guides
```

## Identity

| Tool | Job |
| --- | --- |
| `me` | Subject, provider, personal space id, memberships |

## Spaces

| Tool | Job |
| --- | --- |
| `space_list` | Personal + groups I belong to |
| `space_create` | Create a **group**; caller becomes creator |
| `space_show` | Detail + members for a space I can see |

Default write target when space is omitted: **personal**.

## Entries

| Tool | Job |
| --- | --- |
| `remember` | Create an entry (type, title, body, tags, optional space, optional expiry). Refuses near-duplicates unless forced |
| `find` | Hybrid search → digests. Filters: type, tags, space, path/repo hint |
| `open` | Full entry by id (or stable title within a space) |
| `revise` | Patch title/body/type/tags/expiry |
| `forget` | Delete an entry I can write |
| `browse` | Paginated listing without a query |
| `since` | Time-window recall → digests |
| `by_tag` | Tag AND/OR → digests |
| `relocate` | Move an entry to another space I can write |

Entry types: `fact`, `preference`, `decision`, `instruction`, `feedback`,
`insight`, `note`. Default type for `remember` is `note`.

## Guides

| Tool | Job |
| --- | --- |
| `guide_write` | Upsert a guide; duplicate content strengthens confidence |
| `guide_find` | Search guides; rank by confidence × recency |

## Coding context

| Tool | Job |
| --- | --- |
| `about_path` | Digests tied to a file/dir path (or repo hint) |
| `trace` | Provenance for an entry or guide |

## Housekeeping

| Tool | Job |
| --- | --- |
| `overlap_report` | Read-only: near-duplicates, conflicts, soon-to-expire. No auto-delete |

## Suggested agent loop

1. Start meaningful work → `guide_find` + `find` (and `about_path` if in a tree).
2. Durable fact/decision → `remember`.
3. Reusable rule → `guide_write`.
4. Need full text → `open`.
5. Periodic hygiene → `overlap_report`, then human/agent merges via `revise` /
   `forget`.

## Auth notes for tools

Principals come from Bearer JWT (preferred; MCP Authorize / GUI sign-in),
per-user API key, or `ADMIN_API_KEY`. ACL never trusts client-supplied user
ids. The admin API key is the real `ADMIN_EMAIL` user (personal space +
`created_by`). Prefer OAuth/Authorize for agent sessions.

See also {doc}`memory-types`.
