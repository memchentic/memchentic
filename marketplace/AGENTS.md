# AGENTS.md — memchentic in-repo marketplace

Single-plugin marketplace embedded in `nodadyoushutup/memchentic`.

Host catalogs (repo root — required for Cursor GitHub import):

- `.cursor-plugin/marketplace.json`
- `.claude-plugin/marketplace.json`
- `.github/plugin/marketplace.json` (Copilot)
- `.agents/plugins/marketplace.json` (Codex)

Plugin payload: `marketplace/plugins/memchentic/`.

Marketplace name: `memchentic`.
Plugin id: `memchentic`.

## Ownership

This plugin teaches agents how to use the **memchentic** MCP (spaces, entries,
guides, hybrid find). Product contracts live in repo-root `docs/`. Portable
posture / coding / planning craft lives in
[`nodadyoushutup/marketplace`](https://github.com/nodadyoushutup/marketplace)
— install those plugins; do not fork them under `memchentic-*`.

| Keep here | Refuse / use public instead |
| --- | --- |
| memchentic tool loop, entry vs guide, space defaults | Standing posture, language standards, worktrees |
| Gated recall/capture when the memchentic MCP is connected | Portable planners / tech-lead / debugger agents |

## Plugin prefix rule

| Short key | Marketplace id | Allowed basenames |
| --- | --- | --- |
| `memchentic` | `memchentic` | `memchentic` or `memchentic-*` |

## Version bumps

Before every commit that changes shipped content under
`marketplace/plugins/memchentic/`, bump the plugin semver in all three host
manifests and all four root catalogs (default patch).
