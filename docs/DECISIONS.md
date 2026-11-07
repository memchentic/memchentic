# Locked decisions

Updated: 2026-11-07

| # | Topic | Decision |
| --- | --- | --- |
| 1 | Language / MCP | **Python** + **FastMCP** |
| 2 | Product name | **memchentic** (repo + folder) |
| 3 | License | **MIT** |
| 4 | Git hosting | **GitHub private** (`nodadyoushutup/memchentic`) |
| 5 | Scope of this repo | Product only. Homelab deploy later. No AgentMemory migration in scope. |
| 6 | Auth model | `OAUTH_PROVIDER` selects preset (`google`, `github`, `okta`, `authentik`, …) or **`internal`**. Shared-style env: `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, issuer/audience/JWKS as needed. See [AUTH.md](AUTH.md). |
| 7 | Dual credentials | OAuth Authorize **and** optional API keys (both may be enabled) |
| 8 | Tenancy | One deployment, many users; **workspace** is ACL unit; DB-enforced isolation preferred |
| 9 | Memory UX | Explicit typed stores + hybrid search; AgentMemory-like lessons; not full doc RAG |
| 10 | Tool suite | See [TOOLS.md](TOOLS.md) |
| 11 | Local dev | Docker Compose; **bind-mount source**; rebuild only when deps change |
| 12 | Multi-arch | linux/amd64 + linux/arm64 when we publish images |
| 13 | Database | **Still open** — discuss next (Postgres+pgvector is the leading candidate, not locked) |

## Explicitly out of scope (for now)

- Homelab Swarm / NPM / Authentik deploy in this repo
- Migrating existing AgentMemory `/data`
- Depending on Memlord or AgentMemory as runtime libraries
