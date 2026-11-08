# Memory types

Memchentic stores memories in **spaces**. Each memory is either an **entry** or
a **guide**. Entries carry a closed **type** enum.

## Spaces

```text
you
  ├── personal     private; only this user; not a group
  └── groups[]     shared; every member can read and write memories
```

- **Personal** is created with the user. Omit `space_id` on writes → personal.
- **Groups** are optional. Any authenticated user can create a group and manage
  the roster on groups they created. Members who did not create the group can
  use its memories but not manage membership.
- There is **no** group role ladder (no owner/member/viewer flags beyond
  creator vs everyone else).
- **Admin** (`ADMIN_API_KEY` / admin login) is break-glass across users and
  groups. The admin key is a real `ADMIN_EMAIL` user with a personal space
  (omit-space → personal) and may create groups; prefer a per-user key or JWT
  for normal agent sessions because admin list/search is instance-wide.

A memory lives in exactly one space. Visibility is that space's roster.

## Write target resolution

`remember` / `guide_write` pick the target in this order:

1. `space_id` — that space (membership check).
2. `group` — exact, case-insensitive name among the caller's unarchived groups.
3. `visibility="shared"` — the user's default shared group, else their only
   group; otherwise error.
4. Otherwise → personal.

## Kind: entry vs guide

| Kind | Tool surface | Job |
| --- | --- | --- |
| `entry` | `remember`, `revise`, `forget`, `find`, … | Durable record worth keeping |
| `guide` | `guide_write`, `guide_find` | Reusable advice; duplicate content strengthens confidence |

Same store underneath; separate tools so the agent loop stays obvious.

## Entry types

Closed enum:

| Type | Typical use |
| --- | --- |
| `fact` | Stable truth about the world or codebase |
| `preference` | How someone wants work done |
| `decision` | A choice and its rationale |
| `instruction` | Standing directive the agent should follow |
| `feedback` | Correction after a mistake |
| `insight` | Learned non-obvious finding |
| `note` | Default catch-all |

Guides are not an entry type. Use `guide_write` for reusable advice.

## Search and embeddings

Vectors are derived from title + body on write (hardcoded MiniLM ONNX, 384-d).
`find` is hybrid: full-text search plus pgvector, fused for ranking. Search
tools return **digests** (id, title, score, snippet, type, tags, space). Call
`open` for the full body.

Path-scoped recall uses `about_path` with a path and optional repo hint.
