# Vision

## One-liner

**memchentic** — self-hosted multi-user agent memory over MCP: hard isolation,
hybrid recall, and Cursor/Claude Authorize with your choice of OAuth provider
(or a built-in IdP).

## Goals

1. Personal continuity across sessions and tools  
2. Multi-user by construction (verified identity → ACL)  
3. Optional shared workspaces  
4. MCP-first (streamable HTTP + Authorize)  
5. Hybrid retrieval (keyword + vector)  
6. Operator-owned (Compose locally; Swarm later from homelab)  
7. Best of AgentMemory UX + Memlord tenancy/search shape  

## Non-goals (v1)

- AgentMemory orchestration zoo (mesh, actions, slots, …) — see TOOLS.md  
- Full document RAG  
- Homelab packaging / migration of old AgentMemory data  
- Depending on Memlord or AgentMemory as libraries  

## Success criteria (first useful release)

- [ ] Two users Authorize; neither sees the other’s personal memories  
- [ ] Shared workspace invite works; leave → lose access  
- [ ] v1 tool suite from TOOLS.md works with progressive disclosure  
- [ ] Compose bind-mount dev loop works on amd64 and arm64 hosts  
- [ ] OAuth presets: at least google, github, okta, authentik, internal  
- [ ] Optional API key path  
