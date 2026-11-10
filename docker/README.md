# memchentic — Docker Compose configuration

| File | Purpose |
| --- | --- |
| `docker-compose.yaml` | Base project: `postgres` and `application` (published image) |
| `local.yaml.example` | Development overlay: local build, development tools, bind mounts. Copy to `local.yaml` |
| `migrate.yaml` | Optional overlay: a one-shot service `migrate` applies the schema migrations, and `application` starts without the owner-role password |
| `docs.yaml` | Optional service `docs` (published docs image) |
| `Dockerfile` | Application image |
| `.env.example` | Template for `.env`. Copy it, then set the required secrets |

How to start the stack: `README.md` (published image) and `AGENTS.md`,
section "Run and verify" (development). The Helm chart reads the same
variables through `config.*` values and its Secret: `helm/README.md`.

## Variables read by Compose

| Variable | Default | Meaning |
| --- | --- | --- |
| `COMPOSE_FILE` | `docker-compose.yaml` | Files that form the project. Development: `docker-compose.yaml:local.yaml`. Migrations in a separate service: `docker-compose.yaml:migrate.yaml` (do not combine it with `local.yaml`). Append `:docs.yaml` for the docs service |
| `APPLICATION_IMAGE` | `registry.memchentic.ai/memchentic/memchentic:latest` | Application image |
| `BIND_ADDRESS` | a loopback address | Host interface of the published port. The default accepts connections from the same machine only; `0.0.0.0` accepts them from every interface |
| `GUI_PORT` | `18080` | The one published host port. Open it in a browser |
| `APPLICATION_MEMORY_LIMIT` | `2g` | Memory ceiling of the application container |
| `DOCS_IMAGE` | `registry.memchentic.ai/memchentic/docs:latest` | Docs image (`docs.yaml`) |
| `DOCS_PORT` | `18099` | Host port of the docs service |

The published port reaches one listener, which routes by path:
`/` (GUI), `/api` (REST), `/mcp` (MCP). MCP clients connect to
`<BASE_URL>/mcp`.

With the `migrate.yaml` overlay, Compose starts the service `migrate`
first. It runs `python -m memchentic.migrate` with the owner-role password
and exits. The service `application` then starts with
`RUN_MIGRATIONS_ON_BOOT=false` and an empty `POSTGRES_PASSWORD`. Without the
overlay the application applies the migrations when it starts and holds the
owner-role password for its whole lifetime.

## Required secrets

The application refuses to start while one of these is empty, a placeholder,
or too short. `openssl rand -hex 24` prints a value that satisfies each rule.

| Variable | Rule | Meaning |
| --- | --- | --- |
| `ADMIN_PASSWORD` | 16 or more characters | Password of the break-glass admin (`ADMIN_EMAIL`) |
| `ADMIN_API_KEY` | 16 or more characters | Bearer token with admin rights. Break-glass only; agents use per-user API keys |
| `POSTGRES_PASSWORD` | 16 or more characters | Password of the owner role. Postgres keeps the value it was first started with |
| `POSTGRES_APP_PASSWORD` | 16 or more characters | Password of the app role |
| `ENCRYPTION_KEY` | 32 or more characters | Encrypts the signing keys stored in Postgres. After a change the stored keys cannot be read |

## Origin, HTTP and sessions

| Variable | Default | Meaning |
| --- | --- | --- |
| `BASE_URL` | a loopback `http` origin on port 8080 | Public origin (scheme, host, port) that people and agents use. Token issuer, MCP resource URL `<BASE_URL>/mcp`, OAuth redirect URLs |
| `CORS_ORIGINS` | two loopback origins on port 5173 | Comma-separated origins that may call the API from a browser. Set it to the origin of `BASE_URL` |
| `LOG_LEVEL` | `INFO` | Python log level |
| `SESSION_REMEMBER_DAYS` | `30` | Lifetime of a "remember me" GUI session; renewed on use |
| `SESSION_BROWSER_HOURS` | `12` | Lifetime of every other GUI session |
| `SESSION_COOKIE_SECURE` | unset | `true` or `false` forces the `Secure` flag of the session cookie. Unset or blank: `Secure` unless the host of `BASE_URL` is a loopback host. Set `false` only for a plain `http` origin in development |
| `REGISTER_ENABLED` | `true` | Self-registration with the internal provider. Used as the initial value when the database has no stored value; after that, admins change it in System Settings |
| `ENABLE_API_DOCS` | `false` | Serves the generated REST reference at `/docs`, `/redoc` and `/openapi.json`. Keep it off on a reachable deployment |
| `FORWARDED_ALLOW_IPS` | unset | Read by uvicorn, not by the application settings. Comma-separated addresses or CIDR ranges of the reverse proxy that may set `X-Forwarded-For` and `X-Forwarded-Proto`. Set it when a reverse proxy is in front of the application; otherwise all clients share one rate-limit bucket |

## Sign-in provider

| Variable | Default | Meaning |
| --- | --- | --- |
| `OAUTH_PROVIDER` | `internal` | `internal` (built-in accounts; memchentic is the authorization server for MCP clients) or an external provider: `google`, `github`, `gitlab`, `entra`, `okta`, `authentik`, `keycloak`, `auth0`, `generic` |
| `OAUTH_CLIENT_ID` | empty | Client id at the external provider. Browser sign-in with the provider is offered only when it is set. Register the redirect URI `<BASE_URL>/api/auth/oauth/callback` at the provider |
| `OAUTH_CLIENT_SECRET` | empty | Client secret at the external provider |
| `OAUTH_ISSUER` | empty | Issuer URL. Required for `entra`, `okta`, `authentik`, `keycloak`, `auth0` and `generic`; replaces the built-in issuer of the other providers |
| `OAUTH_AUDIENCE` | empty | Expected audience of access tokens. Empty means `<BASE_URL>/mcp` |
| `OAUTH_JWKS_URI` | empty | Replaces the JWKS URL of the provider |
| `OAUTH_SCOPES` | blank | Comma-separated scopes requested at sign-in. Blank means the default scopes of the provider preset |
| `OAUTH_NATIVE_REDIRECT_SCHEMES` | `cursor` | Comma-separated private-use redirect schemes that native MCP clients may register |

## Admin account

| Variable | Default | Meaning |
| --- | --- | --- |
| `ADMIN_EMAIL` | `admin@example.com` | Sign-in e-mail of the break-glass admin |
| `ADMIN_DISPLAY_NAME` | `Admin` | Display name of the break-glass admin |

## Database

| Variable | Default | Meaning |
| --- | --- | --- |
| `POSTGRES_USER` | `memchentic` | Owner role: owns the tables and runs migrations |
| `POSTGRES_APP_USER` | `memchentic_app` | App role: serves every request; row-level security applies to it |
| `POSTGRES_HOSTNAME` | `postgres` | Database host |
| `POSTGRES_PORT` | `5432` | Database port |
| `POSTGRES_DB` | `memchentic` | Database name |
| `POSTGRES_SSLMODE` | `prefer` | `disable`, `prefer`, `require`, `verify-ca` or `verify-full`. Use `verify-full` for a database on another host |
| `POSTGRES_SSLROOTCERT` | empty | CA file for `verify-ca` and `verify-full`. Empty means the system CA store |
| `RUN_MIGRATIONS_ON_BOOT` | `true` | `false` stops the application from applying migrations at start. The `migrate.yaml` overlay sets it and applies them in the service `migrate` |

## Search and ranking

Search runs a full-text query and a vector query, merges the two result
lists by rank, and then multiplies each hit by the factors below.

| Variable | Default | Meaning |
| --- | --- | --- |
| `SEARCH_DEFAULT_LIMIT` | `20` | Result count of search tools and routes when the caller gives no limit |
| `BROWSE_DEFAULT_LIMIT` | `50` | Result count of listing tools and routes when the caller gives no limit |
| `RESULT_MAX_LIMIT` | `100` | Upper bound of every limit |
| `RRF_K` | `60` | Constant of the rank merge: each list adds `1 / (RRF_K + rank)` |
| `SEARCH_MIN_SIMILARITY` | `0.2` | Relevance floor: a memory that is not a keyword match is returned only when its cosine similarity to the query is at least this value. `-1` turns the floor off |
| `SEARCH_RECENCY_HALF_LIFE_DAYS` | `30` | Freshness `1 / (1 + age_in_days / value)`, measured from the last change |
| `SEARCH_RECENCY_WEIGHT` | `0.25` | Recency factor `1 + value * freshness`: a just-written memory scores at most `1 + value` times an old one. Range 0 to 1; `0` turns recency off |
| `SEARCH_PATH_BOOST` | `0.15` | Factor `1 + value` when the path hint given by the caller is contained in the path hint of the memory |
| `MEMORY_USAGE_SCORE_WEIGHT` | `0.05` | Factor `1 + ln(1 + recall_count) * value` |
| `NEAR_DUP_THRESHOLD` | `0.92` | Minimum cosine similarity at which two memories are near-duplicates |
| `NEAR_DUP_CANDIDATES` | `5` | Number of nearest memories checked for a near-duplicate on write |
| `OVERLAP_SCAN_LIMIT` | `2000` | Maximum number of memories that `overlap_report` compares |

## Memory lifetime

| Variable | Default | Meaning |
| --- | --- | --- |
| `MEMORY_DEFAULT_TTL_DAYS` | `90` | Lifetime of a new memory when the caller gives none. `0` means no default expiry |
| `MEMORY_TTL_EXTEND_DAYS` | `90` | A recall moves the expiry of a live memory to at least this many days from now |
| `MEMORY_TTL_TOUCH_DEBOUNCE_HOURS` | `6` | Minimum time between two renewals of one memory |
| `MEMORY_MAX_TTL_DAYS` | `730` | A renewal never moves the expiry past the creation time plus this many days. `0` means no ceiling |

## Development only

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALLOW_HASH_EMBEDDINGS` | `false` | Lets the application start with hash vectors when the MiniLM model files are missing. Set by `local.yaml.example`. Do not use it for data you want to keep |
