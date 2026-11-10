# memchentic

Agent craft for the **memchentic** MCP. Install when the memchentic MCP is (or
will be) connected. AlwaysApply rules stay lean; full tool routing loads on
demand.

## Rules (Cursor)

| Rule | Apply | Purpose |
| --- | --- | --- |
| `memchentic-recall` | always | Aggressive gated recall (`guide_find` / `find` / `about_path`) |
| `memchentic-capture` | always | Save entries + guides after verified insights |

## Session hook (Claude Code / Copilot CLI / Codex)

Each host has its own hook file, so no host parses another host's keys:
`hooks/claude.json` (`SessionStart`, named in `.claude-plugin/plugin.json`),
`hooks/codex.json` (`SessionStart`, named in `.codex-plugin/plugin.json`) and
`hooks/hooks.json` (`sessionStart`, Copilot CLI). All three run
`hooks/session-start.sh`, which injects the recall + capture loop. Install the
plugin (and for Codex, enable hooks and trust this plugin’s SessionStart hook
once via `/hooks` or Settings → Hooks; a hook-script change requires trust
again). No per-session skill load and no `/memchentic` are required to start
the habit.

## Skills (deeper playbooks)

| Skill | Purpose |
| --- | --- |
| `memchentic` | Index / gate (explicit entry when unsure which playbook) |
| `memchentic-recall` | Recall playbook |
| `memchentic-capture` | Capture playbook (types, write targets, refuse) |
| `memchentic-tools` | Full MCP tool routing by job (all 18 tools) |

## Commands

- `/memchentic` → optional quick entry: gate, then recall and/or capture

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

Closed enum on `remember` (default `note`): `fact`, `preference`, `decision`,
`instruction`, `feedback`, `insight`, `note`. Labels for filter/recall — not
separate pipelines. Meanings and the pick map → skill `memchentic-capture`.

Guides are not an entry type.

## Design notes

Everyday turns use a small loop: `guide_find` + `find` (and `about_path` when
editing a tree), then `remember` / `guide_write` after verified durable
insights. Prefer a connected memchentic MCP when it is ready; do not invent
tool calls when it is disconnected. Never store secrets.
