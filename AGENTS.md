# memchentic — agent guide

User-facing docs live under `docs/sphinx/`. Original product: do not fork,
wrap, or copy other memory servers' tool names.

## Layout

| Path | Owns |
| --- | --- |
| `docker/` | Compose + Dockerfile (`docker-compose.yaml` prod base; `local.yaml` overlay; optional `docs.yaml`) |
| `helm/` | Kubernetes Helm chart (app Deployment + optional pgvector; `values.yaml` + secret modes) |
| `application/api/` | FastAPI + shared domain: `pyproject.toml`, `alembic/`, `src/memchentic/`, `tests/`, `models/` (MiniLM ONNX; baked in image, gitignored files) |
| `application/mcp/` | FastMCP tools/context (`src/memchentic/mcp/`); image + local overlay merge into API `src/` |
| `application/gui/` | React + Tailwind (greyscale dark) + Font Awesome |
| `marketplace/` | In-repo agent plugin (`memchentic`); host catalogs at repo root |
| `docs/sphinx/` | User-facing Sphinx (MyST) site; optional Compose overlay `docker/docs.yaml` |

Inside containers the code root is `/opt/memchentic` (API tree; MCP overlaid at
`src/memchentic/mcp`). Compose project name is `memchentic`; services are
`postgres` and `application` (FastAPI + FastMCP + React SPA). Prod base pulls
`APPLICATION_IMAGE` from GHCR; `local.yaml` adds `build`, bind mounts, and
uvicorn `--reload`.

## Run and verify

From `docker/` (sticky `.env` sets `COMPOSE_FILE=docker-compose.yaml:local.yaml`):

```bash
cd docker
cp .env.example .env          # once; set APPLICATION_IMAGE
cp local.yaml.example local.yaml  # once; gitignored
docker compose up -d --build  # --build only needed for local overlay rebuilds
docker compose exec application pytest -q
# Pytest always uses DB memchentic_test (never the live memchentic volume).
docker compose exec application ruff check src tests
```

Host publishes come from `docker/.env`: `GUI_PORT` (18080), `API_PORT`
(18081), `MCP_PORT` (18082) — all map to the same uvicorn `:8080` (path-routed
`/`, `/api`, `/mcp`). Rebuild the application image when
`application/api/pyproject.toml`, system packages, or `application/gui/`
change; with `local.yaml`, API + MCP source and `application/gui/dist` are
bind-mounted.

Prod-shaped bring-up (no bind mounts; pulls GHCR — `docker login ghcr.io` if private):

```bash
cd docker && COMPOSE_FILE=docker-compose.yaml docker compose up -d
```

Optional Sphinx docs overlay (`docker/docs.yaml`, not in `.env.example`):

```bash
cd docker && docker compose up -d --build docs
# default DOCS_PORT=18099 — nginx serves built HTML
```

Author user pages under `docs/sphinx/source/`.

## Rules

- ACL never trusts client-supplied user ids; principals come from Bearer JWT,
  per-user API key, or `ADMIN_API_KEY`.
- MCP tools and REST routes share `memchentic.services`; no divergent logic.
- Python: typed boundaries, Google-style docstrings, Ruff clean.
- Do not commit or push unless the user asks.
- Homelab deployment and the parked AgentMemory cutover are out of scope.
