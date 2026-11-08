# Deploy with Docker Compose

Compose project name is **`memchentic`**. Two services:

| Service | Role |
| --- | --- |
| `postgres` | Postgres 16 + pgvector; data volume; not published by default |
| `application` | FastAPI + FastMCP + React SPA; one uvicorn on `:8080` |

Path routing on that listener: GUI `/`, API `/api`, MCP `/mcp`. Three host
ports republish the same process for reverse proxies.

## Files

| Path | Role |
| --- | --- |
| `docker/docker-compose.yaml` | Prod base (pulls `APPLICATION_IMAGE`) |
| `docker/local.yaml` | Operator overlay (copy from `local.yaml.example`) — build, bind mounts, `--reload` |
| `docker/.env` | Sticky Compose client config (copy from `.env.example`) |
| `docker/docs.yaml` | Optional docs overlay (Sphinx site); not listed in `.env.example` |

## Sticky env

```bash
cd docker
cp .env.example .env
cp local.yaml.example local.yaml
```

Set `APPLICATION_IMAGE`, admin secrets, and ports. For local iteration:

```text
COMPOSE_FILE=docker-compose.yaml:local.yaml
```

`.env.example` ships with `COMPOSE_FILE=docker-compose.yaml` only. Local
dev overlays are operator choice.

## Bring-up

Local overlay (rebuild when needed):

```bash
cd docker
docker compose up -d --build
```

Prod-shaped (no bind mounts; pulls GHCR — `docker login ghcr.io` if private):

```bash
cd docker && COMPOSE_FILE=docker-compose.yaml docker compose up -d
```

Default host ports: `GUI_PORT=18080`, `API_PORT=18081`, `MCP_PORT=18082`.
Set `BASE_URL` to the URL browsers and OAuth callbacks use
(`http://your-host:18080`).

The application refuses to start if `ADMIN_PASSWORD` or `ADMIN_API_KEY` is
empty, a placeholder, or shorter than 16 characters. Migrations run on
startup.

## Optional docs overlay

`docker/docs.yaml` adds only the Sphinx docs service (nginx serving built
HTML). It is **not** in `.env.example`. On an operator machine that wants it:

```text
COMPOSE_FILE=docker-compose.yaml:local.yaml:docs.yaml
DOCS_PORT=18099
```

Then:

```bash
cd docker && docker compose up -d --build docs
```

Docs do not need postgres. Open `http://your-host:18099/`.

## Verify

```bash
docker compose exec application pytest -q
docker compose exec application ruff check src tests
```

Pytest always uses database `memchentic_test`, never the live volume.
