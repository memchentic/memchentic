# Deploy with Helm

The chart deploys the single application image (FastAPI + FastMCP + React SPA)
and optional in-cluster pgvector Postgres.

Default image: `ghcr.io/memchentic/memchentic` (tag pinned in `values.yaml`).

## Install

```bash
helm upgrade --install memchentic ./helm \
  -n memchentic --create-namespace \
  -f my-values.yaml
```

Set `config.baseUrl` and `config.corsOrigins` to the public URL the GUI and
OAuth callbacks use. Service is ClusterIP on port **8080**. Ingress is off by
default (`memchentic.local`, class `nginx`).

## Postgres

| Mode | Values |
| --- | --- |
| In-cluster | `postgres.enabled: true` (default) — image `pgvector/pgvector:pg16`, PVC |
| External | `postgres.enabled: false` and `config.postgresHostname: my.db.example` |

`POSTGRES_PASSWORD` always comes from the resolved Secret.

## Secret modes

Set `secrets.mode` to exactly one of:

| Mode | Behavior |
| --- | --- |
| `inline` | Chart creates a Secret from `secrets.inline.*` (dev only). |
| `existing` | Use `secrets.existingSecret` (any sync tool). |
| `externalSecret` | Chart creates an ExternalSecret; cluster needs External Secrets Operator and a SecretStore. |

Required Secret keys:

- `ADMIN_PASSWORD`
- `ADMIN_API_KEY`
- `POSTGRES_PASSWORD`
- `OAUTH_CLIENT_SECRET` (may be empty when `OAUTH_PROVIDER=internal`)

Admin password and API key must be at least 16 characters.

### External Secrets examples

Vault (KV v2 path example `secret/k8s/memchentic`):

```yaml
secrets:
  mode: externalSecret
  externalSecret:
    secretStoreRef:
      name: memchentic-vault
      kind: SecretStore
    data:
      ADMIN_PASSWORD:
        key: k8s/memchentic
        property: ADMIN_PASSWORD
      ADMIN_API_KEY:
        key: k8s/memchentic
        property: ADMIN_API_KEY
      POSTGRES_PASSWORD:
        key: k8s/memchentic
        property: POSTGRES_PASSWORD
      OAUTH_CLIENT_SECRET:
        key: k8s/memchentic
        property: OAUTH_CLIENT_SECRET
```

AWS Secrets Manager and 1Password follow the same shape with the matching ESO
provider and `remoteRef` fields. See `helm/README.md` in the repo.

## Config highlights

`config.*` mirrors Compose env: `baseUrl`, `oauthProvider` (default
`internal`), admin email, Postgres user/db, near-duplicate threshold, RRF `k`,
search/browse limits, session lifetimes, CORS, log level.

Chart path: `helm/` (`Chart.yaml` name **memchentic**).
