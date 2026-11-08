# Memory Types

Memchentic keeps long-lived knowledge in **spaces**. Every memory is one of
two kinds: an **entry** or a **guide**.

| Kind | In plain terms |
| --- | --- |
| **Entry** | Something worth *remembering* — a fact, preference, decision, or lesson |
| **Guide** | Something worth *following again* — standing advice for a recurring situation |

They live in the same places and share the same privacy rules. The difference
is the job each one is for.

The same idea can show up as both when you want the record *and* the playbook
(for example a decision you made, plus the habit you want next time).

## Spaces

Memories belong to a **space**: your private **personal** space, or an
optional shared **group**. Personal is the default. Shared groups are covered
in {doc}`groups`.

## Entries

An **entry** is a durable record. Save one when the important part is *what
is true*, *what you chose*, or *what you learned*.

Good entries tend to:

- Still matter after the current conversation ends
- Save someone from rediscovering the same hard-won detail later
- Name something concrete — a preference, a decision, a root cause, a quirk

Skip chatter, unfinished todos, raw dumps, guesses, and secrets. Skip things
that are already obvious without storing them.

### Entry types

Every entry has a **type**. The type is a label so you can filter and scan
memories more easily — it does not change how search works under the hood.

| Type | Meaning | Example |
| --- | --- | --- |
| `fact` | A stable truth | “Production publishes the GUI on port 18080.” |
| `preference` | How someone wants work done | “Prefer short answers unless detail is asked for.” |
| `decision` | A choice and why | “Docs ship as their own image, separate from the app.” |
| `instruction` | A standing always / never rule | “Never commit unless asked.” |
| `feedback` | A correction after a mistake | “Hard-cut API changes — no legacy shims.” |
| `insight` | A non-obvious lesson | “The test database is separate from the live one.” |
| `note` | Anything that does not fit cleanly above | A short durable scratch note |

A guide is its own kind of memory, not an entry type. If what you need is
reusable advice rather than a labeled record, use a guide.

## Guides

A **guide** is reusable advice. Save one when the important part is *what to
do next time* — especially “when this happens, do that, and avoid the other
thing.”

Good guides tend to:

- Apply to more than one incident
- Stay useful without rereading the original conversation
- Get stronger when the same advice is confirmed again, instead of spawning
  near-duplicate copies

A natural shape looks like:

```text
When this situation comes up:
do this
Avoid that

Optional context: why this advice stuck
```

When similar guide content is saved again, Memchentic updates the existing
guide and raises its **confidence**. Higher confidence means that advice ranks
more strongly when you search later.

## Choosing entry or guide

| If you mainly need… | Choose |
| --- | --- |
| A record of what is true or what was decided | **Entry** |
| A playbook for a situation that will come up again | **Guide** |
| Both the record and the playbook | **Both** |
| Something temporary, unverified, or secret | **Neither** |

## Finding memories later

Search looks at both the words you use and the meaning behind them. You
usually see a short summary first, then open the full text when you need it.
Guides can rank higher as their confidence grows. Memories can also be tied to
a file or project path so related work is easier to find again.
