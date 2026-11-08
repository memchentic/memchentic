# Memory Types

Memchentic stores memories in **spaces**. Each memory is either an **entry** or
a **guide**. Entries carry a closed **type** enum.

## Spaces

Memories live in a **space**: your **personal** space (private, created with
the account) or an optional shared **group**. Omit `space_id` on writes →
personal. Groups, membership, and write-target resolution:
{doc}`groups`.

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
