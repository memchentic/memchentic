# memchentic — agent guide

User-facing docs are published from the repository `memchentic/docs`
(Sphinx sources under `sphinx/source/` there). In a working checkout `docs/`
is a clone of that repository (a nested git repo). This repository also
tracks a partial copy under `docs/`: 13 files, of which 3 are pages
(`index.md`, `mcp-tools.md`, `marketplace.md`). `docs/` is in `.gitignore`,
so git ignores new files there; new user pages belong in `memchentic/docs`.
The pitch site is the separate repository `memchentic/website` (cloned under
`website/`, which is ignored and not tracked here). Original product: do not
fork, wrap, or copy other memory servers' tool names.

## Layout

| Path | Owns |
| --- | --- |
| `docker/` | Compose + Dockerfile (`docker-compose.yaml` prod base; `local.yaml` overlay; optional `docs.yaml`) |
| `helm/` | Kubernetes Helm chart (app Deployment + optional pgvector; `values.yaml` + secret modes) |
| `application/api/` | FastAPI + shared domain: `pyproject.toml`, `alembic/`, `src/memchentic/`, `tests/`, `models/` (MiniLM ONNX; baked in image, gitignored files) |
| `application/mcp/` | FastMCP tools/context (`src/memchentic/mcp/`); image + local overlay merge into API `src/` |
| `application/gui/` | React + Tailwind (greyscale dark) + Font Awesome |
| `marketplace/` | In-repo agent plugin (`memchentic`); host catalogs at repo root |
| `docs/` | Nested clone of `memchentic/docs` (Sphinx + docs image). This repository tracks a partial copy: `docs/assets/` (logos used by `README.md`) and `docs/sphinx/` (`conf.py`, theme files, 3 of the pages) |
| `website/` | Nested clone of `memchentic/website` (Vite + React + Tailwind; GitHub Pages); not tracked here |

Inside containers the code root is `/opt/memchentic` (API tree; MCP overlaid at
`src/memchentic/mcp`). Compose project name is `memchentic`; services are
`postgres` and `application` (FastAPI + FastMCP + React SPA). Prod base pulls
`APPLICATION_IMAGE` from Zot (`registry.memchentic.ai`; GHCR remains the mirror); `local.yaml` adds `build`, bind mounts, and
uvicorn `--reload`.

## Run and verify

Development stack (local build, development tools, bind mounts). Run once
from the repository root:

```bash
cp docker/.env.example docker/.env              # gitignored; never commit or print it
cp docker/local.yaml.example docker/local.yaml  # gitignored
```

Edit `docker/.env` before the first start:

- Set `COMPOSE_FILE=docker-compose.yaml:local.yaml`. Without `local.yaml` in
  `COMPOSE_FILE`, Compose pulls `APPLICATION_IMAGE`, `--build` builds
  nothing, and the container has no pytest, no ruff and no `tests/`.
- Set the five required secrets. The application refuses to start while one
  of them is empty, a placeholder, or too short: `ADMIN_PASSWORD`,
  `ADMIN_API_KEY`, `POSTGRES_PASSWORD`, `POSTGRES_APP_PASSWORD` (16 or more
  characters each) and `ENCRYPTION_KEY` (32 or more characters). Generate
  each value with `openssl rand -hex 24`.
- Set `POSTGRES_PASSWORD` before the first `docker compose up`. Postgres
  stores it when it creates the volume `memchentic_db-data`. A later change
  in `.env` does not change the stored password.
- Set `BASE_URL` and `CORS_ORIGINS` to the origin you open in the browser,
  for example `http://<host>:<GUI_PORT>`.
- For a plain `http` origin, also set `SESSION_COOKIE_SECURE=false`.
  Otherwise the browser does not store the session cookie and the GUI
  session ends on every page reload.

Build the GUI on the host, then start the stack. Run from the repository
root:

```bash
(cd application/gui && npm ci && npm run build)  # creates application/gui/dist
cd docker
docker compose up -d --build
```

Quality gate. Run it before every commit; `.github/workflows/ci.yml` runs
the same checks on every pull request. Backend, from `docker/`:

```bash
docker compose exec application ruff format --check src tests
docker compose exec application ruff check src tests
docker compose exec application mypy
docker compose exec application pytest -q --cov --cov-report=term-missing
```

GUI, from `application/gui/`:

```bash
npm run typecheck
npm run lint
npm test
npm run build
```

Pytest always uses the database `memchentic_test`, never the live database
`memchentic`.

The overlay bind-mounts `application/api` over `/opt/memchentic`. This hides
the GUI build and the MiniLM model that the image contains:

- The GUI is served only when `application/gui/dist/index.html` exists on
  the host. Run `npm run build` again after a GUI change. A rebuild of the
  image does not change the served GUI while the overlay is active.
- The overlay sets `ALLOW_HASH_EMBEDDINGS=true`, so search uses hash vectors.
  For real embeddings run `scripts/fetch_minilm_onnx.sh` once on the host,
  then `docker compose restart application`. Do this before you store
  memories that you want to keep: hash vectors and MiniLM vectors cannot be
  compared with each other.
- API and MCP source changes reload automatically (uvicorn `--reload`).
  Rebuild the image (`docker compose up -d --build`) when
  `application/api/pyproject.toml`, a lock file
  (`application/api/requirements.lock`, `application/api/requirements-dev.lock`)
  or `docker/Dockerfile` changes. After a change to the dependency lists in
  `pyproject.toml`, first regenerate both lock files with the two commands
  in the header of `application/api/requirements-build.in`.

The host port comes from `docker/.env`. `GUI_PORT` (default 18080) is the
one published port. It reaches the uvicorn listener (container port 8080),
which routes by path: `/` (GUI), `/api` (REST), `/mcp` (MCP).
`BIND_ADDRESS` selects the host interface of the published port. The
default accepts connections from the same machine only; set
`BIND_ADDRESS=0.0.0.0` before you open the GUI from another machine.

MCP clients connect to `<BASE_URL>/mcp`. `BASE_URL` must be the origin that
clients really use (scheme, host and port), because OAuth sign-in for MCP
clients names `<BASE_URL>/mcp` as the protected resource.

Production-shaped start (published image from `registry.memchentic.ai`,
anonymous pull, no bind mounts, no pytest or ruff inside):

```bash
cd docker && COMPOSE_FILE=docker-compose.yaml docker compose up -d
```

It reads the same `docker/.env`, so the five secrets must be set. To apply
migrations in a one-shot service instead of at application start, use
`COMPOSE_FILE=docker-compose.yaml:migrate.yaml` (see `docker/README.md`).

Optional Sphinx docs service (`docker/docs.yaml`). It is not in the default
`COMPOSE_FILE`. Append `:docs.yaml` to `COMPOSE_FILE` in `docker/.env` first:

```bash
cd docker && docker compose up -d docs
# serves the published image DOCS_IMAGE
# (default registry.memchentic.ai/memchentic/docs:latest)
# at http://<host>:<DOCS_PORT> (DOCS_PORT defaults to 18099)
```

`docker compose up -d --build docs` works only when `docs/` is a clone of
`memchentic/docs`. The build needs `docs/docker/Dockerfile`, which is not
tracked in this repository.

When MCP tools or the plugin change, update `docs/sphinx/source/mcp-tools.md`
and `docs/sphinx/source/marketplace.md` in the same change, and commit them
in the nested `docs/` repo too. New user pages belong in `memchentic/docs`,
which publishes the docs image with its **Publish Docs Image** workflow.

## Runtime surfaces

Every environment variable is described in `docker/README.md` (Compose) and
`helm/README.md` (chart values).

**REST API.** All routes are under `/api`
(`application/api/src/memchentic/api/router.py` mounts the modules in
`application/api/src/memchentic/api/routes/`). Routes that need a caller
accept a Bearer token: a JWT, a per-user API key, or `ADMIN_API_KEY`. Route
groups: `/api/auth/*` (register, login, refresh, logout, browser sign-in with
a provider), `/api/me`, `/api/users*` (`/api/users/{user_id}/revoke-credentials`
revokes the sessions and keys of a user), `/api/spaces*` (spaces, members,
and `/api/spaces/{space_id}/accept` for a group invitation), `/api/memories*`
(entries and guides: list, search, create, read, change, delete),
`/api/guides`, `/api/api-keys*` (`/api/api-keys/cycle` revokes the active
keys of a user and issues one new key), `/api/provider`, `/api/health`
(liveness) and `/api/ready` (readiness). Set `ENABLE_API_DOCS=true` to serve
the generated reference at `/docs`, `/redoc` and `/openapi.json`. It is off by
default.

**Live updates.** `GET /api/events/memories` is a Server-Sent Events stream
of memory changes (`event: memory`) in the spaces that the caller can read.
It needs the `Authorization: Bearer` header, so the browser `EventSource` API
cannot open it; the GUI uses `fetch`. While the stream is open the server
checks the token again: before an event when the last check is 10 seconds
old or more, and at each idle keep-alive. It ends the stream when the token
is no longer valid. Clients reconnect.

**Metrics.** `GET /api/metrics/memories` returns aggregate numbers for the
Metrics page, limited to the spaces that the caller can read. Query
parameters: `range`, `space_id`, `space_scope`.

**Auth groups.** Two fixed auth groups, `administrators` and `users`, decide
who is an admin. An auth group is not a space. Admins manage them under
`/api/auth-groups*`. With the providers `okta`, `entra`, `authentik`,
`keycloak` and `auth0`, an auth group can be bound to a provider group
(`/api/auth-groups/{slug}/idp-bind`). A bound auth group is synchronised at
provider sign-in and cannot be edited by hand.

**Registration switch.** `GET` and `PUT /api/system/registration` (admin
only) read and set whether self-registration is open. It applies to the
internal provider. `REGISTER_ENABLED` supplies the initial value.

**OAuth for MCP clients.** With `OAUTH_PROVIDER=internal`, memchentic is its
own authorization server: `/.well-known/oauth-protected-resource`,
`/.well-known/oauth-authorization-server`, `/oauth/register` (dynamic client
registration), `/oauth/authorize`, `/oauth/token`, `/oauth/jwks`. The
protected resource is `<BASE_URL>/mcp`. With an external provider, the
metadata names that provider and its JWTs are accepted as Bearer tokens.

**Database roles and row-level security.** The owner role (`POSTGRES_USER`)
owns the tables and runs migrations: at application start by default, or
in a separate step (`python -m memchentic.migrate`) when
`RUN_MIGRATIONS_ON_BOOT=false` (the Compose overlay `docker/migrate.yaml`, or
the Helm value `migrations.initContainer.enabled`). The app role
(`POSTGRES_APP_USER`, default `memchentic_app`) serves every request.
Row-level security is enabled and forced on `spaces`, `space_members`,
`memories` and `api_keys`. Service code (`application/api/src/memchentic/services/`)
commits with `commit_with_rls`, because a plain commit clears the
row-level-security context of the session.

**Memory lifetime.** A new memory expires after `MEMORY_DEFAULT_TTL_DAYS`
unless the caller gives a lifetime. Each recall of a live memory (a search
hit or an `open`) moves its expiry to at least `MEMORY_TTL_EXTEND_DAYS` from
now, at most once in `MEMORY_TTL_TOUCH_DEBOUNCE_HOURS`, and never past the
creation time plus `MEMORY_MAX_TTL_DAYS`. An expired memory is hidden, not
deleted; `include_expired` shows it.

**Search ranking.** Search merges a full-text result list and a vector
result list by rank (`RRF_K`), then multiplies each hit by a bounded recency
factor (`SEARCH_RECENCY_WEIGHT`, `SEARCH_RECENCY_HALF_LIFE_DAYS`), a
path-match factor (`SEARCH_PATH_BOOST`), the confidence of a guide, and a
usage factor (`MEMORY_USAGE_SCORE_WEIGHT`). A memory that is not a keyword
match is returned only when its similarity to the query is at least
`SEARCH_MIN_SIMILARITY`.

## Versions

Four version lines exist. They are independent of each other.

| Version line | Single place where it is written | Rule |
| --- | --- | --- |
| Application release | The image tag, computed by the **Publish App Image** workflow from the registry tags. The only copy in this repository is `appVersion` in `helm/Chart.yaml` | The image tag names a release. Change `appVersion` in the same change that makes the chart deploy a new image |
| Python package | `[project].version` in `application/api/pyproject.toml` | Not a release number. Do not write it by hand in any other Python file (`memchentic.__version__` reads it from the installed package metadata) |
| Helm chart | `version` in `helm/Chart.yaml` | Increase it with every change to the chart |
| Plugin | `version` in the three files `marketplace/plugins/memchentic/.*-plugin/plugin.json` and in the four catalogs `.cursor-plugin/marketplace.json`, `.claude-plugin/marketplace.json`, `.github/plugin/marketplace.json`, `.agents/plugins/marketplace.json` | All seven values are equal. Change them together |

`metadata.version` in the catalogs is the version of the catalog file.
`version` in `marketplace/plugins/memchentic/hooks/hooks.json` is the format
version of that file. `version` in `application/gui/package.json` is used by
npm only. None of the three is a release number.

## Rules

- ACL never trusts client-supplied user ids; principals come from Bearer JWT,
  per-user API key, or `ADMIN_API_KEY`.
- MCP tools and REST routes share `memchentic.services`; no divergent logic.
- Python: typed boundaries, Google-style docstrings, Ruff clean.
- Do not commit or push unless the user asks.
- Homelab deployment and the parked AgentMemory cutover are out of scope.
