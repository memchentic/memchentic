# memchentic Helm chart

Deploys the single application image (FastAPI + FastMCP + React SPA) and
optional in-cluster pgvector Postgres.

Image default: `ghcr.io/memchentic/memchentic:0.0.1` (org package).

## Install

```bash
helm upgrade --install memchentic ./helm \
  -n memchentic --create-namespace \
  -f my-values.yaml
```

## Secret modes

Set `secrets.mode` to exactly one of:

| Mode | Behavior |
| --- | --- |
| `inline` | Chart creates a Secret from `secrets.inline.*` (dev only). |
| `existing` | Use `secrets.existingSecret` (any sync tool). |
| `externalSecret` | Chart creates an ExternalSecret; cluster needs [External Secrets Operator](https://external-secrets.io/) and a SecretStore. |

Required Secret keys:

- `ADMIN_PASSWORD`
- `ADMIN_API_KEY`
- `POSTGRES_PASSWORD`
- `OAUTH_CLIENT_SECRET` (may be empty when `OAUTH_PROVIDER=internal`)

### Vault (homelab-style)

SecretStore points at Vault; chart ExternalSecret maps properties:

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

Vault KV v2 path example: `secret/k8s/memchentic`.

### AWS Secrets Manager

Use an ESO `SecretStore` / `ClusterSecretStore` with provider `aws`. Set each
`remoteRef.key` to the secret name or ARN and `property` to the JSON field.

### 1Password

Use an ESO store with provider `onepassword` (Connect). Set `remoteRef.key` to
the item path and `property` to the field name.

## External Postgres

```yaml
postgres:
  enabled: false
config:
  postgresHostname: my.db.example
```

`POSTGRES_PASSWORD` still comes from the resolved Secret.
