# Worked example — the `decision` retain kind

The being's retain pipeline used to write a zettel for the *topic* of a decision but not
the *decision itself*. A future session recalling "what did we decide about rollback
strategy?" found 5 notes — all about rollback *tradeoffs* — and honestly reported "I
don't have a recorded decision." The mechanics worked; the retained unit was the wrong
shape.

This worked example shows the fix: a first-class **`decision`** kind, so a decision-shaped
turn is recorded as an authoritative, dated, recallable fact — *not* a generic concept
about its topic.

## The shape of a decision

A `decision` candidate carries the shared `kind/topic/title/slug/content` plus three
optional, decision-specific fields:

| field | what it holds |
|---|---|
| `choice` | the thing that was decided, in a few words |
| `alternatives` | what was considered and rejected |
| `rationale` | why this choice was made |

The distiller's content for a decision must record the choice verbatim-in-own-words so a
future session can answer "what did we decide?" without re-reading a blob.

## Run it

No network, no hermes, no provider — a stub `StructuredLLM` drives the whole pipeline:

```bash
python examples/decision_kind_demo.py
```

The resulting zettel leads with the decision:

```markdown
# Rollback Strategy: Blue-Green + Keep Last-Deploy-K

**Decision:** adopt blue-green deploys and keep last-deploy-k valid for rollback

We decided to adopt blue-green deploys and keep last-deploy-k valid for
rollback, rejecting warm-standby and cold-standby to minimize downtime and
make rollback a fast, safe reconnect.

**Rationale:** minimize downtime and make rollback a fast, safe reconnect
```

## Design decisions (and why)

1. **Standalone, dated decision zettel — not a merge into a concept/project note.**
   Merging an authoritative decision into a generic note would bury it. Each decision is
   its own note, named `YYYYMMDD-slug.md` by `corpus.write`, which is collision-safe: a
   later decision on the same topic becomes a *new* dated note, so **append-only + dated**
   falls out of the existing write discipline — old decisions are never overwritten.

2. **Decisions never route through `judge_merge`.** `process_candidate` special-cases
   `kind == "decision"` to skip the search-and-merge step entirely and go straight to a
   standalone create. This both saves an LLM call and guarantees a decision is never folded
   into an unrelated note.

3. **The body leads with the choice.** `process_candidate` composes the written body as
   `**Decision:** <choice>` → `content` → `**Rationale:** <rationale>`, so the choice is
   prominent in the note and surfaces in full-text recall.

## The surface that changed

- `zk_memory/judge.py` — `kind` enum gains `"decision"`; the candidate schema gains
  `choice`/`alternatives`/`rationale`; `_DISTILL_SYSTEM_PROMPT` teaches the distiller to
  tell a decision (a call made) from a concept (a tradeoff discussion) and to record the
  choice, not just the topic.
- `zk_memory/retain.py` — `process_candidate` routes `decision` candidates to standalone
  dated create and returns `None` (not a label) when a create/merge actually fails, so a
  duplicate isn't double-counted.

## Test evidence

`tests/test_decision_kind.py` (run `pytest` in the repo root):

- schema exposes the `decision` kind and the three decision fields
- a decision-shaped turn distills to `kind == "decision"` with `choice`/`rationale`
- a decision is created (not merged) even when a search hit exists, and `judge_merge` is
  never called for it
- the body leads with the choice; a same-day, same-slug duplicate is refused, never
  overwriting the original
- an end-to-end search of "rollback" surfaces a note whose body contains the choice

All 81 tests green (74 pre-existing + 7 new).