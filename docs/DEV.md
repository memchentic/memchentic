# Local development

## Intent

Iterate without rebuilding images on every code change:

- Docker Compose runs app + (future) DB
- **Bind-mount** application source into the container
- Restart process/container picks up Python changes
- Rebuild image only when dependencies / system packages / models change

## Planned layout (when we implement)

```text
docker-compose.yml          # app + db services
Dockerfile                  # runtime + deps; code not baked as source of truth
.env.example                # OAUTH_* / provider / secrets template
src/memchentic/             # FastMCP app (bind-mounted)
```

Example compose shape (illustrative — DB image TBD after DB decision):

```yaml
services:
  app:
    build: .
    ports: ["8080:8080"]
    env_file: .env
    volumes:
      - ./src:/app/src:ro
    # command: watchfiles / uvicorn or fastmcp run …
  # db:
  #   image: <chosen>
  #   volumes: [db-data:/…]
```

## Out of scope here

Homelab Swarm, NPM, Cloudflare, GHCR publish pipelines — later, from the
homelab consumer repo once the product works.
