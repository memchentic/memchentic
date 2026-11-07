# memchentic

Self-hosted **multi-user agent memory** for Cursor, Claude, and other MCP
harnesses. Python + FastMCP. MIT.

Combines the best ideas from [AgentMemory](https://github.com/rohitg00/agentmemory)
(coding-agent UX) and [Memlord](https://github.com/MyrikLD/memlord) (workspaces,
hybrid search, OAuth-shaped remote MCP) — without depending on either.

## Status

Planning / design. Implementation not started.

## Docs

| Doc | Purpose |
| --- | --- |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Locked product decisions |
| [docs/VISION.md](docs/VISION.md) | Goals and non-goals |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | Isolation, auth, platform |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System sketch |
| [docs/AUTH.md](docs/AUTH.md) | OAuth providers + env contract |
| [docs/TOOLS.md](docs/TOOLS.md) | MCP tool suite (v1 / later) |
| [docs/COMPETITIVE.md](docs/COMPETITIVE.md) | What we steal vs skip |
| [docs/DEV.md](docs/DEV.md) | Local Docker Compose workflow |

## Quick intent

- Hard per-user isolation + optional shared workspaces
- MCP streamable HTTP with **Authorize** (pluggable OIDC + optional built-in IdP)
- Hybrid keyword + vector recall (DB choice still open — see DEV / upcoming DB talk)
- Local Docker Compose with code bind-mount for fast iteration
