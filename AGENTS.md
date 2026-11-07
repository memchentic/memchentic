# memchentic — agent guide

Product truth lives in `docs/` (read `docs/DECISIONS.md` first). Original
product: do not fork, wrap, or copy other memory servers' tool names.

## Layout

| Path | Owns |
| --- | --- |
| `docker/` | Compose + Dockerfile (`docker-compose.yaml` prod base; `local.yaml` overlay) |
| `api/` | Python API + MCP: `pyproject.toml`, `alembic/`, `src/memchentic/`, `tests/` |
| `gui/` | React + Tailwind (greyscale dark) + Font Awesome |
| `models/` | Local ONNX embedder files (gitignored) |
| `marketplace/` | In-repo agent plugin (`memchentic`); host catalogs at repo root |

Inside containers the code root is `/opt/memchentic`. Compose services are
`db`, `api`, and (with the local overlay) `gui`.

## Run and verify

From `docker/` (sticky `.env` sets `COMPOSE_FILE=docker-compose.yaml:local.yaml`):

```bash
cd docker
cp .env.example .env          # once
cp local.yaml.example local.yaml  # once; gitignored
docker compose up -d --build
docker compose exec api pytest -q
docker compose exec api ruff check src tests
```

Host ports come from `docker/.env` (`MEMCHENTIC_API_PORT`, default 18080;
`MEMCHENTIC_GUI_PORT`, default 15173). Rebuild the api image only when
`api/pyproject.toml` or system packages change; with `local.yaml`, source is
bind-mounted.

Prod-shaped bring-up (no bind mounts, no GUI service):

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
