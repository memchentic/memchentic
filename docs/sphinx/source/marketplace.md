# Marketplace plugin

The **Memchentic** plugin teaches agent harnesses how to use the Memchentic
MCP: when to recall, when to capture, and which tools fit which jobs. It ships
inside this repository so hosts can import
`https://github.com/nodadyoushutup/memchentic` directly.

Product contracts still live in repo `docs/` (agent/product truth). The plugin
does not replace the server.

## Concepts

| Piece | Role |
| --- | --- |
| **Rules** (Cursor) | Always-on recall and capture gates when the MCP is connected; tools routing on demand |
| **Skills** | Playbooks: index skill, recall skill, capture skill — same posture on Claude / Codex |
| **Command** | `/memchentic` — short operator entry for the host |

There is **no** `agents/` directory in this plugin. Portable planners and coding
craft come from [`nodadyoushutup/marketplace`](https://github.com/nodadyoushutup/marketplace)
(`global`, `code`, `business-analyst`, …). Install those separately; do not
fork them under `memchentic-*`.

### Mental model the plugin teaches

- **Personal vs groups** — omit `space_id` → personal; admin API key must pass
  `space_id`.
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

## Install by harness

### Cursor

1. Dashboard → Settings → Plugins.
2. Import `https://github.com/nodadyoushutup/memchentic`.
3. Install **Memchentic**.

Or:

```shell
agent plugin marketplace add https://github.com/nodadyoushutup/memchentic.git
```

Cursor wires rules, skills, and commands.

### Claude Code

```shell
claude plugin marketplace add nodadyoushutup/memchentic
claude plugin install memchentic@memchentic
```

Skills + commands (no Cursor-style rules file surface).

### GitHub Copilot CLI

```shell
copilot plugin marketplace add nodadyoushutup/memchentic
copilot plugin install memchentic@memchentic
```

### OpenAI Codex

```shell
codex plugin marketplace add nodadyoushutup/memchentic
```

## Connect the MCP

After installing the plugin, configure the host MCP client for Memchentic
(`http://your-host:18082/mcp` or your edge URL) with a per-user API key or JWT.
Without a live MCP connection, the plugin's recall/capture gates stay idle.

Getting started end-to-end: {doc}`getting-started`. Tool list: {doc}`mcp-tools`.
