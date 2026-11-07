# memchentic — agent guide

Product truth lives in `docs/` (read `docs/DECISIONS.md` first). Original
product: do not fork, wrap, or copy other memory servers' tool names.

## Layout

| Path | Owns |
| --- | --- |
| `docker/` | All Docker artifacts (`docker-compose.yaml`, `Dockerfile`) |
| `api/` | Python API + MCP: `pyproject.toml`, `alembic/`, `src/memchentic/`, `tests/` |
| `gui/` | React + Tailwind (greyscale dark) + Font Awesome |
| `models/` | Local ONNX embedder files (gitignored) |
| `marketplace/` | In-repo agent plugin (`memchentic`); host catalogs at repo root |

Inside containers the code root is `/opt/memchentic`. Compose services are
`db`, `api`, `gui`.

## Run and verify

```bash
docker compose -f docker/docker-compose.yaml up -d --build
docker compose -f docker/docker-compose.yaml exec api pytest -q
docker compose -f docker/docker-compose.yaml exec api ruff check src tests
```

Host ports come from `docker/.env` (`MEMCHENTIC_API_PORT`, default 18080;
`MEMCHENTIC_GUI_PORT`, default 15173). Rebuild the api image only when
`api/pyproject.toml` or system packages change; source is bind-mounted.

## Rules

- ACL never trusts client-supplied user ids; principals come from Bearer JWT,
  per-user API key, or `MEMCHENTIC_ADMIN_API_KEY`.
- MCP tools and REST routes share `memchentic.services`; no divergent logic.
- Python: typed boundaries, Google-style docstrings, Ruff clean.
- Do not commit or push unless the user asks.
- Homelab deployment and the parked AgentMemory cutover are out of scope.
