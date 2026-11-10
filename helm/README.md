# memchentic Helm chart

Deploys the single application image (FastAPI + FastMCP + React SPA) and
optional in-cluster pgvector Postgres.

Image default: `registry.memchentic.ai/memchentic/memchentic:<Chart.appVersion>`
(pinned; mirrored to GHCR). Set `image.tag` to deploy another release. Do not
deploy `latest`.

## Required values

| Value | Why |
| --- | --- |
| `config.baseUrl` | Public https origin. The chart refuses to render without it |
| `secrets.existingSecret` | Name of the Secret, in the default mode `secrets.mode: existing` |

Set `config.corsOrigins` to the same origin as `config.baseUrl`. Every other
`config.*` value maps to one environment variable; `values.yaml` names it, and
`docker/README.md` describes it.

## Install

```bash
helm upgrade --install memchentic ./helm \
  -n memchentic --create-namespace \
  --set config.baseUrl=https://memchentic.example.com \
  --set config.corsOrigins=https://memchentic.example.com \
  --set secrets.existingSecret=memchentic-env
```

Put longer configuration in a values file and pass it with `-f my-values.yaml`.

Use `replicaCount: 1` (the default). More than one replica is not a
supported configuration: sign-in rate limits are kept in process memory, so
each pod counts them separately. Boot migrations are serialised by a
Postgres advisory lock, so pods that start together during a rolling update
wait for each other.

## Secret modes

Set `secrets.mode` to exactly one of:

| Mode | Behavior |
| --- | --- |
| `existing` | Default. Use `secrets.existingSecret` (any sync tool). |
| `externalSecret` | Chart creates an ExternalSecret; cluster needs [External Secrets Operator](https://external-secrets.io/) and a SecretStore. |
| `inline` | Chart creates a Secret from `secrets.inline.*` (dev only). |

Required Secret keys. The application refuses to start when one of the first
five is missing, a placeholder, or too short:

| Key | Rule |
| --- | --- |
| `ADMIN_PASSWORD` | 16 or more characters |
| `ADMIN_API_KEY` | 16 or more characters |
| `POSTGRES_PASSWORD` | 16 or more characters; password of the owner role (migrations) |
| `POSTGRES_APP_PASSWORD` | 16 or more characters; password of the app role `memchentic_app` (all request queries) |
| `ENCRYPTION_KEY` | 32 or more characters; after a change the stored signing keys cannot be read |
| `OAUTH_CLIENT_SECRET` | May be empty when `OAUTH_PROVIDER=internal` |

### Vault (homelab-style)

SecretStore points at Vault; chart ExternalSecret maps properties. Every key
under `secrets.externalSecret.data` needs a `key`, including
`OAUTH_CLIENT_SECRET`:

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
      POSTGRES_APP_PASSWORD:
        key: k8s/memchentic
        property: POSTGRES_APP_PASSWORD
      ENCRYPTION_KEY:
        key: k8s/memchentic
        property: ENCRYPTION_KEY
      OAUTH_CLIENT_SECRET:
        key: k8s/memchentic
        property: OAUTH_CLIENT_SECRET
```

Vault KV v2 path example: `secret/k8s/memchentic`.

### AWS Secrets Manager

Use an ESO `SecretStore` / `ClusterSecretStore` with provider `aws`. Set each
`secrets.externalSecret.data.<KEY>.key` to the secret name or ARN and
`property` to the JSON field.

### 1Password

Use an ESO store with provider `onepassword` (Connect). Set each
`secrets.externalSecret.data.<KEY>.key` to the item path and `property` to
the field name.

## External Postgres

```yaml
postgres:
  enabled: false
config:
  postgresHostname: my.db.example
```

`POSTGRES_PASSWORD` and `POSTGRES_APP_PASSWORD` still come from the resolved
Secret.

The owner role (`POSTGRES_USER`) must be a Postgres superuser, as it is in
the bundled Postgres. A deployment whose owner role is not a superuser is
not supported: row-level security then also applies inside the database
functions that sign-in uses, and sign-in with an API key fails.

To verify the server certificate, mount the CA bundle with `extraVolumes`
and `extraVolumeMounts`, and set `config.postgresSslmode: verify-full` and
`config.postgresSslrootcert` to the mounted path. `values.yaml` has an
example above `extraVolumes`.

## Migrations

By default the application applies schema migrations when it starts, and so
holds the owner-role password (`POSTGRES_PASSWORD`) for its whole lifetime.
Set `migrations.initContainer.enabled: true` to apply them in an init
container (`python -m memchentic.migrate`). The application container then
starts with `RUN_MIGRATIONS_ON_BOOT=false` and an empty `POSTGRES_PASSWORD`.
The init container can start before the bundled Postgres is ready; it then
exits with an error and Kubernetes retries it.

## Ingress

When the Ingress is enabled, set `config.forwardedAllowIps` to the address
range of the ingress controller; otherwise all clients share one rate-limit
bucket.

## Upgrading from chart 0.1.x

Chart 0.2.0 adds `app.kubernetes.io/component: application` to the application
selector. Kubernetes does not allow a Deployment selector to change, so delete
the Deployment once before the upgrade. The database and its volume are not
touched. The application is unavailable until the new pod is ready.

    kubectl delete deployment <deployment>
    helm upgrade <release> <chart> -f <your values>

`<deployment>` is `<release>-memchentic`. When the release name contains
`memchentic`, it is the release name alone (`helm upgrade --install memchentic ./helm`
creates the Deployment `memchentic`).

Chart 0.2.0 also adds a NetworkPolicy for the bundled Postgres
(`postgres.networkPolicy.enabled`, default `true`). Only the application
pods may connect. List other workloads that connect to the database (backup,
monitoring) in `postgres.networkPolicy.extraFrom`.

A release whose computed full name is longer than 43 characters gets shorter
object names. With an external database, Helm replaces the Deployment, the
Service and the ConfigMap under the shorter name on upgrade; no data is
involved.
