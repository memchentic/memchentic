# Marketplace Plugin

The **Memchentic** plugin teaches your coding agent when to recall memories,
when to save them, and how entries differ from guides. Install it from
`https://github.com/memchentic/memchentic`.

Install steps: {doc}`install-marketplace`. Connect the server first:
{doc}`connect-mcp`.

Install the plugin and connect the server. The agent recalls and captures on
its own (Cursor rules; Claude Code / Codex session-start and per-prompt hooks;
Copilot CLI session-start hook).
Do not load a skill or run `/memchentic` just to start that habit.

## What it teaches

- **Personal vs groups** — private memory by default; shared groups when you
  choose them
- **Projects** — optional labels inside a space when organising work
- **Entry vs guide** — records of what is true vs standing advice that can
  get stronger over time
- **Short results first** — search starts with summaries; open the full text
  when you need it
- **Hygiene** — spot overlaps and declared replacements; resolve carefully
- **Only when configured** — with no Memchentic server configured, or when a
  call fails, the plugin stays out of the way for that turn
- **No secrets** — do not store passwords, tokens, or private personal data in
  memories

## Pieces

| Piece | Role |
| --- | --- |
| **Rules** (Cursor) | Always-on recall and capture when a Memchentic server is configured |
| **Hooks** (Claude / Copilot / Codex) | Session-start injection of the same loop; Claude Code and Codex also get a one-line reminder on every prompt |
| **Skills** | Deeper playbooks for recall and capture |
| **Command** | `/memchentic` — optional quick entry point in the host |

Other general coding plugins (planners, style guides, and so on) are separate
installs if you want them — this plugin is only about Memchentic memory.
