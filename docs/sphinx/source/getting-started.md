# Getting started

Three pieces make a working setup: the Memchentic server, an agent harness with
the marketplace plugin, and an MCP connection to `/mcp`.

## 1. Run the server (Docker Compose)

From a checkout:

```bash
cd docker
cp .env.example .env
cp local.yaml.example local.yaml
# set strong ADMIN_PASSWORD and ADMIN_API_KEY (16+ characters each)
docker compose up -d --build
```

Sticky `.env` should set `COMPOSE_FILE=docker-compose.yaml:local.yaml` for
bind mounts and reload. Prod-shaped (pull GHCR image, no mounts):

```bash
cd docker && COMPOSE_FILE=docker-compose.yaml docker compose up -d
```

Default host publishes (all map to the same container `:8080`):

| Port env | Default | Path |
| --- | --- | --- |
| `GUI_PORT` | 18080 | `/` |
| `API_PORT` | 18081 | `/api` |
| `MCP_PORT` | 18082 | `/mcp` |

Open the GUI at `http://your-host:18080`, sign in with the admin email and
password from `.env`, then create a per-user API key for agents.

Details: {doc}`deploy-docker`.

## 2. Optional: Kubernetes (Helm)

```bash
helm upgrade --install memchentic ./helm \
  -n memchentic --create-namespace \
  -f my-values.yaml
```

Set `config.baseUrl` and `config.corsOrigins` to the public URL. Choose a
secret mode (`inline`, `existing`, or `externalSecret`). Details:
{doc}`deploy-helm`.

## 3. Install the marketplace plugin

Import this repo as a marketplace, then install **Memchentic**.

**Cursor** — Dashboard → Settings → Plugins → import
`https://github.com/nodadyoushutup/memchentic`, then install **Memchentic**.
Or:

```shell
agent plugin marketplace add https://github.com/nodadyoushutup/memchentic.git
```

**Claude Code**

```shell
claude plugin marketplace add nodadyoushutup/memchentic
claude plugin install memchentic@memchentic
```

**GitHub Copilot CLI**

```shell
copilot plugin marketplace add nodadyoushutup/memchentic
copilot plugin install memchentic@memchentic
```

**OpenAI Codex**

```shell
codex plugin marketplace add nodadyoushutup/memchentic
```

Plugin concepts and harness notes: {doc}`marketplace`.

## 4. Connect MCP

Point the host MCP client at `http://your-host:18082/mcp` (or your reverse
proxy URL that reaches `/mcp`). Authenticate with a per-user API key or a
Bearer JWT from GUI / MCP Authorize is preferred. The break-glass
`ADMIN_API_KEY` is the same admin user (personal space + `created_by`); use a
per-user key when acting as a non-admin account.

Suggested first calls: `me`, then `guide_find` / `find`, then `remember` or
`guide_write` when something is worth keeping. Tool reference:
{doc}`mcp-tools`. Memory model: {doc}`memory-types`.
