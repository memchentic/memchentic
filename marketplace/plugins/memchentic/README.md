# memchentic

Agent craft for the **memchentic** MCP. Install when the memchentic MCP is (or
will be) connected. AlwaysApply rules stay lean; full tool routing loads on
demand.

## Rules (Cursor)

| Rule | Apply | Purpose |
| --- | --- | --- |
| `memchentic-recall` | always | Aggressive gated recall (`guide_find` / `find` / `about_path`) |
| `memchentic-capture` | always | Save entries + guides after verified insights |
| `memchentic-tools` | on demand | Full MCP tool routing by job |

## Skills (both)

| Skill | Purpose |
| --- | --- |
| `memchentic` | Index / gate (Claude + explicit entry) |
| `memchentic-recall` | Recall playbook |
| `memchentic-capture` | Capture playbook |

Claude Code does not load plugin rules — use the skills for the same posture.

## Commands

- `/memchentic` → gate, then recall and/or capture

## Mental model

```text
you (me)
  ├── personal        private entries/guides
  └── teams[]         shared spaces you belong to
        └── entries / guides
```

- **Entry** — something worth keeping (`remember` / `revise` / `forget`).
- **Guide** — something worth re-teaching next session (`guide_write` /
  `guide_find`; duplicate content strengthens confidence).
- **Find** returns digests; **`open`** loads the full body.
- Omit `space_id` on writes → **personal** (user principal). Admin API key
  has no personal default — pass `space_id` explicitly.

## Design notes

Everyday turns use a small loop: `guide_find` + `find` (and `about_path` when
editing a tree), then `remember` / `guide_write` after verified durable
insights. Prefer a connected memchentic MCP when it is ready; do not invent
tool calls when it is disconnected. Never store secrets.
