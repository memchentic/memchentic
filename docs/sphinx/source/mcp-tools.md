# MCP Tools

When an agent is connected to Memchentic, it can use a small set of tools to
save and find memories. You do not need these names day to day if you browse
the web UI — they describe what the agent is allowed to do.

## Where memory lives

```text
you
  ├── personal     private to you
  └── groups[]     shared spaces you belong to
```

If nothing else is specified, new memories go to **personal**.

## Who you are

| Tool | What it does |
| --- | --- |
| `me` | Shows who is connected, your personal space, and group memberships |

## Spaces

| Tool | What it does |
| --- | --- |
| `space_list` | Lists your personal space and groups |
| `space_create` | Creates a shared group (you become the creator) |
| `space_show` | Shows detail and members for a space you can see |

## Entries

| Tool | What it does |
| --- | --- |
| `remember` | Saves a typed entry |
| `find` | Searches entries and returns short summaries |
| `open` | Loads the full text of a memory |
| `revise` | Updates an existing memory |
| `forget` | Deletes a memory you can write |
| `browse` | Lists memories without a search query |
| `since` | Finds memories updated in a time window |
| `by_tag` | Finds memories by tags |
| `relocate` | Moves a memory to another space you can write |

Entry types are covered in {doc}`memory-types`.

## Guides

| Tool | What it does |
| --- | --- |
| `guide_write` | Saves or reinforces standing advice |
| `guide_find` | Searches guides (stronger advice ranks higher) |

## Extra helpers

| Tool | What it does |
| --- | --- |
| `about_path` | Finds memories related to a file or project path |
| `trace` | Shows where a memory came from, when available |
| `overlap_report` | Flags near-duplicates and similar housekeeping — nothing is deleted automatically |

## Signing in for tools

Agents authenticate as a real user — usually by signing in through the
harness (Authorize), or with a personal API key from the GUI. The admin API
key is break-glass only; prefer your own account for everyday use.

How to connect a harness: {doc}`connect-mcp`. What entries and guides mean:
{doc}`memory-types`.
