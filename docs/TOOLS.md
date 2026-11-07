# MCP tool suite

Sources reviewed:

- **AgentMemory** (~53 tools via our live MCP / marketplace routing) — rich, but
  many are multi-agent orchestration / ops, not everyday memory.
- **Memlord** (11 tools) — tight CRUD + hybrid search + workspaces + dream.

Goal: a **full functional memory ops suite** for real coding-agent use — not a
clone of AgentMemory’s orchestration graph, not as thin as “three tools.”

---

## v1 — ship these

### Identity & workspaces

| Tool | Purpose | Inspired by |
| --- | --- | --- |
| `whoami` | Authenticated user, provider, personal workspace id, memberships | Memlord / common MCP |
| `workspace_list` | Personal + shared workspaces you can access | Memlord |
| `workspace_create` | Create a **shared** workspace (you become owner) | Memlord UI → MCP |
| `workspace_describe` | Members/roles for a workspace you can see | — |

Active workspace for writes defaults to personal; tools accept optional
`workspace` name/id when the caller has write access.

### Core memory CRUD + retrieval

| Tool | Purpose | Inspired by |
| --- | --- | --- |
| `memory_store` | Save typed memory (`fact`, `preference`, `instruction`, `feedback`, `decision`, `insight`, `lesson`, …); tags; metadata; optional expiry; optional workspace | Memlord `store_memory` + AgentMemory `memory_save` |
| `memory_search` | Hybrid keyword + vector search; **snippets by default**; filters (type, tags, workspace, project/repo) | Memlord `retrieve_memory` + AgentMemory `memory_smart_search` |
| `memory_get` | Full body + metadata by id/name | Memlord |
| `memory_update` | Patch content/type/tags/metadata/expiry/rename | Memlord |
| `memory_delete` | Delete by id/name (workspace ACL) | Memlord + AgentMemory governance (lighter) |
| `memory_list` | Paginated list with filters (no query) | Memlord |
| `memory_recall` | Time-oriented recall (“last week”, since/until) | Memlord `recall_memory` + AgentMemory `memory_timeline` (simpler) |
| `memory_search_by_tag` | Tag AND/OR search | Memlord |
| `memory_move` | Move a memory between workspaces you can write | Memlord |

### Coding-agent affordances (from AgentMemory everyday set)

| Tool | Purpose | Inspired by |
| --- | --- | --- |
| `lesson_save` | Save reusable guidance with **confidence**; reinforce on duplicate | AgentMemory `memory_lesson_save` |
| `lesson_recall` | Search lessons ranked by confidence + recency | AgentMemory `memory_lesson_recall` |
| `memory_file_history` | Past notes tied to a file path / glob | AgentMemory `memory_file_history` |
| `memory_verify` | Light provenance: why this hit exists (source ids / session stub) | AgentMemory `memory_verify` (simplified) |

Lessons may be stored as `type=lesson` rows with confidence fields — separate
tools keep the agent UX clear (same as AgentMemory’s default loop).

### Consolidation (read-only first)

| Tool | Purpose | Inspired by |
| --- | --- | --- |
| `dream_report` | Near-duplicates, conflicts, expiring soon — **no writes** | Memlord |

Agent/LLM can propose merges; writes go through `memory_store` / `memory_update`
/ expiry — we do not auto-delete in v1.

**v1 count: ~18 tools** — enough for real memory ops; small enough to document
and test isolation against.

---

## Explicitly deferred (v1.1+)

| Area | AgentMemory / Memlord tools | Why defer |
| --- | --- | --- |
| Slots | `memory_slot_*` | Useful working notes; second product surface |
| Multi-agent graph | actions, frontier, next, lease, sketch, crystallize, routine | Not needed for personal/team memory store |
| Messaging / mesh | signals, mesh_sync, team_share/feed | Different product |
| Sentinels / checkpoints | sentinel_*, checkpoint | Workflow engine |
| Heavy LLM ops | reflect, consolidate (server-side LLM) | Cost/complexity; dream_report is enough first |
| Vision | vision_search | Optional later |
| Export bridges | obsidian_export, claude_bridge_sync, snapshot | Ops/nice-to-have |
| Diagnose/heal | diagnose, heal, audit (full) | After core is solid |
| Patterns / profile / relations / graph | patterns, profile, relations, graph_query | Can grow from search + metadata |
| Facets | facet_tag / facet_query | Tags cover v1 |
| Commits | commit_lookup / commits | Homelab-specific coupling |

---

## Agent default loop (recommended)

Same spirit as AgentMemory’s recall/capture rules:

1. Start of meaningful work → `lesson_recall` + `memory_search` (scoped).
2. Durable insight → `lesson_save` and/or `memory_store`.
3. Need full text → `memory_get`.
4. File-touched debugging → `memory_file_history`.

Harness plugins/skills can encode this later; the server just exposes the tools.
