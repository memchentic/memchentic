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

## Skills (Claude / Codex / explicit)

| Skill | Purpose |
| --- | --- |
| `memchentic` | Index / gate (Claude + Codex + explicit entry) |
| `memchentic-recall` | Recall playbook |
| `memchentic-capture` | Capture playbook (types, write targets, refuse) |

Claude Code and Codex do not load Cursor alwaysApply rules — load the
**index skill** (`memchentic`) at session start, then recall/capture as needed.

## Commands

- `/memchentic` → gate, then recall and/or capture

## Mental model

```text
you (me)
  ├── personal        private entries/guides
  └── groups[]         shared spaces you belong to
        └── entries / guides
```

- **Entry** — something worth keeping (`remember` / `revise` / `forget`).
- **Guide** — something worth re-teaching next session (`guide_write` /
  `guide_find`; duplicate content strengthens confidence).
- **Find** returns entry digests; **`guide_find`** returns guides; **`open`**
  loads the full body.
- Omit `space_id` on writes → **personal**. Shared writes use `group`,
  `visibility="shared"`, or `space_id`. Admin key is a real user (personal
  default + can create groups); prefer per-user key/JWT for agents.

### Entry types

Closed enum on `remember` (default `note`). Labels for filter/recall — not
separate pipelines. Prefer the best fit; do not leave `note` when another type
matches (full pick map → capture rule/skill):

| Type | Use when |
| --- | --- |
| `fact` | Stable truth about the world or codebase |
| `preference` | How someone wants work done |
| `decision` | A choice and its rationale |
| `instruction` | Standing directive the agent should follow |
| `feedback` | Correction after a mistake |
| `insight` | Learned non-obvious finding |
| `note` | Catch-all |

Guides are not an entry type.

## Design notes

Everyday turns use a small loop: `guide_find` + `find` (and `about_path` when
editing a tree), then `remember` / `guide_write` after verified durable
insights. Prefer a connected memchentic MCP when it is ready; do not invent
tool calls when it is disconnected. Never store secrets.
