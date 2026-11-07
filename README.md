# memchentic

Self-hosted **multi-user agent memory** for Cursor, Claude, and other MCP
harnesses. Python + FastMCP. MIT.

Original product. Other memory systems were research context only.

## Status

Working Compose stack (db, api, gui). Run with
`docker compose -f docker/docker-compose.yaml up -d --build`.
API and MCP on `MEMCHENTIC_API_PORT` (default 18080, MCP at `/mcp`); GUI on
`MEMCHENTIC_GUI_PORT` (default 15173). Fetch the embedding model with
`scripts/fetch_minilm_onnx.sh`; re-embed with
`docker compose ... exec api python -m memchentic.embeddings.reembed`.

## Docs

| Doc | Purpose |
| --- | --- |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Locked decisions |
| [docs/VISION.md](docs/VISION.md) | Goals / non-goals |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | Isolation, auth, platform |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System sketch |
| [docs/AUTH.md](docs/AUTH.md) | OAuth + internal email/password |
| [docs/TENANCY.md](docs/TENANCY.md) | Personal + teams |
| [docs/GUI.md](docs/GUI.md) | React / Tailwind / dark UI |
| [docs/EMBEDDINGS.md](docs/EMBEDDINGS.md) | What vectors are for |
| [docs/TOOLS.md](docs/TOOLS.md) | MCP tools |
| [docs/COMPETITIVE.md](docs/COMPETITIVE.md) | Research notes |
| [docs/DEV.md](docs/DEV.md) | Compose bind-mount workflow |

## Intent

- Personal memories + optional shared teams (creator manages roster; env admin can do all)
- MCP Authorize + per-user API keys + `MEMCHENTIC_ADMIN_API_KEY`
- Hybrid keyword + **pgvector** on Postgres
- Full greyscale-dark React UI (browse/search) with Tailwind + Font Awesome
