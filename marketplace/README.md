# memchentic plugin marketplace

Single-plugin marketplace for this repo. Plugin content lives under
`marketplace/plugins/memchentic/`. Host catalogs stay at the **repo root** so
Cursor / Claude / Copilot / Codex can import `memchentic/memchentic`
directly (Cursor’s marketplace add has no subdirectory path flag).

Install the plugin and connect the **memchentic** MCP. Do not load a skill or
run `/memchentic` just to start the recall/capture habit.

- **Cursor** — alwaysApply rules inject the loop after install.
- **Claude Code / Copilot CLI** — the plugin session-start hook injects the
  loop after install.
- **Codex** — hooks are on by default for this plugin. Trust this plugin’s
  SessionStart hook once via `/hooks` or Settings → Hooks; after that, new
  sessions get the loop. A change to the hook script requires trust again.

## Install — Cursor

1. Open **Dashboard → Settings → Plugins**.
2. Import: `https://github.com/memchentic/memchentic`
3. Install **memchentic**.

Or from the shell:

```shell
agent plugin marketplace add https://github.com/memchentic/memchentic.git
```

## Install — Claude Code

```shell
claude plugin marketplace add memchentic/memchentic
claude plugin install memchentic@memchentic
```

## Install — GitHub Copilot CLI

```shell
copilot plugin marketplace add memchentic/memchentic
copilot plugin install memchentic@memchentic
```

## Install — OpenAI Codex

```shell
codex plugin marketplace add memchentic/memchentic
```

Codex loads `hooks/codex.json` from the plugin manifest. Trust the
SessionStart hook once (`/hooks` or Settings → Hooks). Re-trust after any
change to the hook script.

Pair with public plugins from
[`nodadyoushutup/marketplace`](https://github.com/nodadyoushutup/marketplace)
(`global`, `code`, `business-analyst`, …). Connect the **memchentic** MCP
server in the host so recall/capture tools are available.

## Layout

| Path | Role |
| --- | --- |
| `marketplace/plugins/memchentic/` | Plugin payload (rules, skills, hooks, commands) |
| `.cursor-plugin/marketplace.json` | Cursor catalog |
| `.claude-plugin/marketplace.json` | Claude Code catalog |
| `.github/plugin/marketplace.json` | Copilot catalog |
| `.agents/plugins/marketplace.json` | Codex catalog |

## Source of truth

This tree is the live home of **`memchentic`**. Edit here.
Tool contracts are the docstrings in
`application/mcp/src/memchentic/mcp/server.py`. The user-facing tool summary
is `docs/sphinx/source/mcp-tools.md`. The full user documentation lives in
the repository `memchentic/docs`.
