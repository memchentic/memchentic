# Marketplace Plugin

The **Memchentic** plugin teaches your coding agent when to recall memories,
when to save them, and how entries differ from guides. Install it from
`https://github.com/memchentic/memchentic`.

Install steps: {doc}`install-marketplace`. Connect the server first:
{doc}`connect-mcp`.

## What it teaches

- **Personal vs groups** — private memory by default; shared groups when you
  choose them
- **Entry vs guide** — records of what is true vs standing advice that can
  get stronger over time
- **Short results first** — search starts with summaries; open the full text
  when you need it
- **Only when connected** — if Memchentic is not connected, the plugin stays
  out of the way
- **No secrets** — do not store passwords, tokens, or private personal data in
  memories

## Pieces

| Piece | Role |
| --- | --- |
| **Rules** (Cursor) | Remind the agent to recall and capture when Memchentic is connected |
| **Skills** | Short playbooks for recall and capture (also used on Claude / Codex) |
| **Command** | `/memchentic` — a quick entry point in the host |

Other general coding plugins (planners, style guides, and so on) are separate
installs if you want them — this plugin is only about Memchentic memory.
