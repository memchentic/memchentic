# memchentic plugin marketplace

Single-plugin marketplace for this repo. Plugin content lives under
`marketplace/plugins/memchentic/`. Host catalogs stay at the **repo root** so
Cursor / Claude / Copilot / Codex can import `nodadyoushutup/memchentic`
directly (Cursor’s marketplace add has no subdirectory path flag).

## Install — Cursor

1. Open **Dashboard → Settings → Plugins**.
2. Import: `https://github.com/nodadyoushutup/memchentic`
3. Install **memchentic**.

Or from the shell:

```shell
agent plugin marketplace add https://github.com/nodadyoushutup/memchentic.git
```

## Install — Claude Code

```shell
claude plugin marketplace add nodadyoushutup/memchentic
claude plugin install memchentic@memchentic
```

## Install — GitHub Copilot CLI

```shell
copilot plugin marketplace add nodadyoushutup/memchentic
copilot plugin install memchentic@memchentic
```

## Install — OpenAI Codex

```shell
codex plugin marketplace add nodadyoushutup/memchentic
```

Pair with public plugins from
[`nodadyoushutup/marketplace`](https://github.com/nodadyoushutup/marketplace)
(`global`, `code`, `business-analyst`, …). Connect the **memchentic** MCP
server in the host so recall/capture tools are available.

## Layout

| Path | Role |
| --- | --- |
| `marketplace/plugins/memchentic/` | Plugin payload (rules, skills, commands) |
| `.cursor-plugin/marketplace.json` | Cursor catalog |
| `.claude-plugin/marketplace.json` | Claude Code catalog |
| `.github/plugin/marketplace.json` | Copilot catalog |
| `.agents/plugins/marketplace.json` | Codex catalog |

## Source of truth

This tree is the live home of **`memchentic`**. Edit here.
Product behavior and tool contracts still live in `docs/` at the repo root.
