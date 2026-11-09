# Memchentic

```{raw} html
<div class="memchentic-brand">
  <img class="memchentic-mark" src="_static/mark-white.svg" alt="" width="112" height="112">
  <span class="memchentic-word">memchentic</span>
</div>
```

## The problem

AI coding assistants are powerful in the moment and forgetful the next. They
lose the preferences you already explained, the decisions your team already
made, and the hard-won lessons from last week's bug hunt. You end up repeating
yourself, pasting the same context into every chat, and watching the agent
reinvent choices you thought were settled.

Shared work makes it worse. One person's "how we do auth here" never reaches
the next person's session. There is no durable place for *your* memory and
*your team's* memory that agents can actually use.

## How Memchentic solves it

Memchentic is a self-hosted memory service built for agentic work. You run it.
Your agents connect to it. They remember what matters, find it again later, and
respect who is allowed to see what.

Personal memory stays private to you. Optional groups let a team share a
collection without turning every note into a free-for-all. A web UI is there
when you want to browse and manage memories yourself; day to day, the agents
do the remembering.

## Memory that fits how people think

Not everything is the same kind of knowledge. Memchentic stores different
shapes on purpose:

- **Facts** and **decisions** — what is true, and what you already chose
- **Preferences** and **instructions** — how you want the agent to work
- **Insights**, **feedback**, and **notes** — lessons and catch-alls
- **Guides** — reusable advice the agent should keep getting better at
  following, not a one-off scrap of text

Agents get a short digest first, then open the full memory when they need it —
so context stays useful instead of drowning the chat. Details live on
{doc}`memory`.

## Hybrid retrieval on a real database

Memchentic is not a prompt paste-bin or a vector-only store. Memory lives in
**Postgres**: ordinary relational rows (spaces, types, tags, ownership) plus
**full-text search** and **pgvector** embeddings in the same database.

When an agent calls `find` / `guide_find` over MCP, the server runs the same
hybrid path the API uses:

1. **Relational filters** — ACL, space, type, tags, path/repo hints as hard
   SQL constraints
2. **Lexical match** — Postgres FTS (with a safe fallback) for the words that
   matter
3. **Vector similarity** — cosine distance over embeddings for meaning when
   phrasing differs
4. **Fusion** — reciprocal rank fusion merges both ranked lists before digests
   are returned

That mix is why recall stays **scoped and current** (the row you filtered is
the row you get) while still catching **paraphrase and intent**. Performance
comes from indexed SQL and vectors in one place — not a second system to keep
in sync.

## An MCP server, not a side script

Memchentic *is* the MCP server. The same application that serves the UI also
exposes the tool surface your harness already knows how to call. Point Cursor,
Claude Code, Copilot, Codex, or another MCP client at it; authenticate; and
the agent can save and recall without a separate glue layer. Those recall tools
hit the hybrid Postgres path above — not a bolted-on side index.

You are not bolting memory onto the model with prompts alone. You are giving
the agent a real place to put knowledge, with isolation and a clear contract.

## Tools at a glance

The tool set is deliberately small and obvious:

- **Who am I / which spaces** — identity and the places memory can live
- **Remember and revise** — write durable entries; update or forget them
- **Find, browse, and open** — search or list, then load the full text
- **Guides** — capture and retrieve standing advice with confidence over time
- **Path-aware recall** — “what do we know about this part of the codebase?”
- **Housekeeping** — spot overlaps and stale items without silent auto-delete

A small set on purpose — enough to remember and find things, not a sprawling
tool catalog.

## Why people adopt it

- **Continuity** across sessions and tools, without pasting the same brief
  every time
- **Hybrid retrieval** — relational filters, full-text, and vectors fused for
  accurate, up-to-date recall
- **Multi-user by design** — personal isolation, optional shared groups
- **Operator-owned** — run it where you already run containers; your data stays
  yours
- **Agent-native** — MCP-first, with a polished UI when humans need it
- **Its own product** — built for this job, not a thin wrap of someone else's
  memory server

Ready to try it? Check {doc}`requirements`, then {doc}`docker` or
{doc}`helm`, then {doc}`connect-mcp` and {doc}`install-marketplace`. Deeper
pages cover {doc}`memory`, {doc}`groups`, {doc}`mcp-tools`, and the
{doc}`marketplace` plugin.

```{toctree}
:caption: Getting Started
:maxdepth: 1
:hidden:
:titlesonly:

requirements
docker
helm
environment-variables
connect-mcp
install-marketplace
```

```{toctree}
:caption: Concepts
:maxdepth: 1
:hidden:
:titlesonly:

memory
groups
mcp-tools
marketplace
```
