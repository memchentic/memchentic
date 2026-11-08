# Marketplace Plugin

The **Memchentic** plugin teaches agent harnesses how to use the Memchentic
MCP: when to recall, when to capture, and which tools fit which jobs. It ships
inside this repository so hosts can import
`https://github.com/memchentic/memchentic` directly.

Install steps by harness: {doc}`install-marketplace`. Wire the MCP endpoint:
{doc}`connect-mcp`.

## Concepts

| Piece | Role |
| --- | --- |
| **Rules** (Cursor) | Always-on recall and capture gates when the MCP is connected; tools routing on demand |
| **Skills** | Playbooks: index skill, recall skill, capture skill — same posture on Claude / Codex (load the index skill at session start; those hosts do not apply Cursor rules) |
| **Command** | `/memchentic` — short operator entry for the host |

There is **no** `agents/` directory in this plugin. Portable planners and coding
craft come from [`nodadyoushutup/marketplace`](https://github.com/nodadyoushutup/marketplace)
(`global`, `code`, `business-analyst`, …). Install those separately; do not
fork them under `memchentic-*`.

### Mental model the plugin teaches

- **Personal vs groups** — omit `space_id` → personal; shared writes use
  `group`, `visibility="shared"`, or `space_id`. Admin key is a real user
  (personal default + can create groups); prefer per-user key/JWT for agents.
- **Entry vs guide** — durable record vs reusable advice with confidence.
- **Digests then `open`** — search returns short digests; load full text on
  purpose.
- **Gated** — if the Memchentic MCP is not connected, skip recall/capture
  rules entirely and continue normally.
- **No secrets** in memory writes.

## Layout

| Path | Role |
| --- | --- |
| `marketplace/plugins/memchentic/` | Plugin payload (rules, skills, commands) |
| `.cursor-plugin/marketplace.json` | Cursor catalog |
| `.claude-plugin/marketplace.json` | Claude Code catalog |
| `.github/plugin/marketplace.json` | Copilot catalog |
| `.agents/plugins/marketplace.json` | Codex catalog |
