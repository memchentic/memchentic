<p align="center">
  <img src="docs/assets/logo.png" alt="memchentic" width="96" height="96">
</p>

<h1 align="center">memchentic</h1>

<p align="center">
  Self-hosted multi-user agent memory for Cursor, Claude, and other MCP clients.
</p>

memchentic stores durable agent memory behind a FastAPI + FastMCP server and a
greyscale-dark React UI. Each user has a personal space; optional teams share
memory with a managed roster. Search is hybrid keyword + **pgvector** on
Postgres. Principals come from OAuth Authorize, per-user API keys, or a
break-glass admin API key — never from client-supplied user ids.

Original product. Other memory systems were research context only.

## Requirements

| Need | Notes |
| --- | --- |
| Docker Engine + Compose v2 | Used for `db`, `api`, and (locally) `gui` |
| Git | Clone and pull |
| Host ports | Defaults: API/MCP `18080`, Vite GUI `15173` (overridable) |
| Optional ONNX model | `scripts/fetch_minilm_onnx.sh` — hash embedder works without it |

Python 3.12+ and Node 22+ are only required if you run the API or GUI outside
Compose. The Compose stack builds and runs those inside containers.

## Getting started

1. **Clone and enter the Compose directory**

   ```bash
   git clone https://github.com/nodadyoushutup/memchentic.git
   cd memchentic/docker
   ```

2. **Configure env and the local overlay**

   ```bash
   cp .env.example .env
   cp local.yaml.example local.yaml
   ```

   Edit `.env`: set strong `MEMCHENTIC_ADMIN_PASSWORD` and
   `MEMCHENTIC_ADMIN_API_KEY` (16+ characters, not placeholders). Set
   `MEMCHENTIC_BASE_URL` and `CORS_ORIGINS` to the host address your browser
   and MCP clients use (not loopback).

   `COMPOSE_FILE=docker-compose.yaml:local.yaml` merges the production-shaped
   base with bind mounts, uvicorn reload, and the Vite GUI. For a prod-only
   bring-up (API + DB, no source mounts, no GUI service):

   ```bash
   COMPOSE_FILE=docker-compose.yaml
   ```

3. **Optional: fetch the embedding model**

   ```bash
   ../scripts/fetch_minilm_onnx.sh
   ```

4. **Start the stack**

   ```bash
   docker compose up -d --build
   ```

5. **Open the UI and wire MCP**

   - GUI: `http://<host>:15173` (port from `MEMCHENTIC_GUI_PORT`)
   - API health: `http://<host>:18080/api/health`
   - MCP (streamable HTTP): `http://<host>:18080/mcp`

   Sign in with the admin credentials from `.env`, or configure an OAuth
   provider. Issue per-user API keys in the UI for agents.

6. **Verify (optional)**

   ```bash
   docker compose exec api pytest -q
   docker compose exec api ruff check src tests
   ```

After adding the ONNX model, recompute stored vectors:

```bash
docker compose exec api python -m memchentic.embeddings.reembed
```

## Layout

| Path | Owns |
| --- | --- |
| `docker/docker-compose.yaml` | Prod-shaped Compose (DB + API, no source mounts) |
| `docker/local.yaml` | Local overlay (gitignored; copy from `local.yaml.example`) |
| `api/` | Python API + MCP |
| `gui/` | React + Tailwind UI |
| `models/` | Local ONNX embedder files (gitignored) |
| `marketplace/` | In-repo agent plugin |

## License

MIT
