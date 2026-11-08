---
name: memchentic
description: >-
  Gated memchentic MCP index: skip when disconnected; otherwise recall early
  and often, capture verified durable insights into entries and guides, and
  respect personal+group spaces. Use at session start, on retries/corrections,
  or when unsure which memchentic skill to load. Deeper playbooks:
  memchentic-recall, memchentic-capture.
---

# memchentic

## Gate

If memchentic is **not** connected/ready → **no memchentic tools**; continue
the task. Do not invent calls.

When connected, prefer the smallest tool set:

| Need | Load |
| --- | --- |
| Search / guides / path / time / tags | `memchentic-recall` |
| Save entries / guides / refuse list | `memchentic-capture` |
| Full tool table (spaces, relocate, overlap, …) | rule `memchentic-tools` |

## Mental model

```text
you (me)
  ├── personal        private entries/guides
  └── groups[]         shared spaces you belong to
```

- **Entry** — keep with `remember` (typed).
- **Guide** — re-teach with `guide_write` (confidence rises on duplicates).
- **Find** → digests; **`open`** → full body.
- Omit `space_id` → **personal** for user principals. Admin API key must pass
  `space_id` (no personal default; cannot create groups).

## Session defaults

1. Meaningful work → parallel `guide_find` + `find` (and `about_path` when in
   a tree) with first investigation tools — and again on corrections, new
   subsystems, or failed approaches.
2. After a verified durable insight → `remember` and/or `guide_write`
   (usually 1–3/turn). Never secrets.
3. Prefer a connected memchentic MCP when ready; do not wrap it in ad-hoc
   REST/CLI side paths for the same job.

Cursor alwaysApply rules `memchentic-recall` and `memchentic-capture` already
encode the default loop; this skill is the Claude / explicit entry point.
