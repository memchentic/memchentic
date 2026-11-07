# memchentic — agent guide

Product truth lives in `docs/` (read `docs/DECISIONS.md` first). Original
product: do not fork, wrap, or copy other memory servers' tool names.

## Layout

| Path | Owns |
| --- | --- |
| `docker/` | Compose + Dockerfile (`docker-compose.yaml` prod base; `local.yaml` overlay) |
| `api/` | Python API + MCP: `pyproject.toml`, `alembic/`, `src/memchentic/`, `tests/` |
| `gui/` | React + Tailwind (greyscale dark) + Font Awesome |
| `models/` | Host ONNX files for local.yaml bind mount (gitignored); image bakes MiniLM at build |
| `marketplace/` | In-repo agent plugin (`memchentic`); host catalogs at repo root |

Inside containers the code root is `/opt/memchentic`. Compose project name is
`memchentic`; services are `postgres` and `application` (FastAPI + FastMCP +
React SPA). `local.yaml` only adds bind mounts (and uvicorn `--reload`).

## Run and verify

From `docker/` (sticky `.env` sets `COMPOSE_FILE=docker-compose.yaml:local.yaml`):

```bash
cd docker
cp .env.example .env          # once
cp local.yaml.example local.yaml  # once; gitignored
docker compose up -d --build
docker compose exec application pytest -q
docker compose exec application ruff check src tests
```

Host port comes from `docker/.env` (`MEMCHENTIC_API_PORT`, default 18080).
Rebuild the application image when `api/pyproject.toml`, system packages, or
`gui/` change; with `local.yaml`, API source and `gui/dist` are bind-mounted.

Prod-shaped bring-up (no bind mounts):

```bash
cd docker && COMPOSE_FILE=docker-compose.yaml docker compose up -d --build
```

## Rules

- ACL never trusts client-supplied user ids; principals come from Bearer JWT,
  per-user API key, or `MEMCHENTIC_ADMIN_API_KEY`.
- MCP tools and REST routes share `memchentic.services`; no divergent logic.
- Python: typed boundaries, Google-style docstrings, Ruff clean.
- Do not commit or push unless the user asks.
- Homelab deployment and the parked AgentMemory cutover are out of scope.
