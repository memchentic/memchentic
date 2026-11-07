# Architecture (first pass)

Stack locked: **Python + FastMCP**. Database **not locked** yet.

```text
┌──────────────┐     Authorize / Bearer      ┌─────────────────────┐
│ Cursor/Claude│ ───────────────────────────▶│  FastMCP HTTP       │
│   harness    │                             │  + MCP Auth         │
└──────────────┘                             └──────────┬──────────┘
                                                        │
                                             ┌──────────▼──────────┐
                                             │ memchentic service  │
                                             │ tools / workspaces  │
                                             └───┬────────────┬────┘
                                                 │            │
                                          embedder      persistence
                                          (ONNX local)  (DB TBD)
                                                 │
                    ┌────────────────────────────┼────────────────────┐
                    │ OAUTH_PROVIDER=…           ▼                    │
                    │ google|github|okta|   token verify              │
                    │ authentik|…|generic|  (JWKS / internal AS)      │
                    │ internal                                        │
                    └─────────────────────────────────────────────────┘
```

## Core ideas

1. One deployment, many tenants — rows + policies, not one process per user  
2. Workspace = ACL unit (personal auto + shared)  
3. Gateway validates OAuth JWT or API key → principal → scoped queries  
4. Search = keyword + vector + fusion (Memlord-shaped) once DB supports it  
5. Tools = [TOOLS.md](TOOLS.md)  

## Open: database

Leading candidate remains Postgres + pgvector + RLS, but **not locked**. Next
conversation: pick backing store (and what that implies for FTS, vectors, RLS,
Compose services).
