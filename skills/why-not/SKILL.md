---
name: why-not
description: The discipline, basis, and operations for maintaining the root WHY-NOT.md in the zk-memory repo — the comparison of zk-memory (the library, NOT the hermes-zk-memory wrapper) against other agentic-memory systems. Use when adding or updating a "why not X" entry, when a new memory system is proposed, when a competitor materially changes, or when asked why we don't use some other tool (mem0, LangMem/Letta, LlamaIndex, Khoj, Chroma, prospecta, QMD, Obsidian, etc.). Triggers: why-not, why not X, compare to mem0/LangMem/Khoj/QMD, should we use X, what about X.
metadata:
  home: /home/dt/src/witt3rd/zk-memory
  scope: repo
---

# why-not — maintaining WHY-NOT.md

`WHY-NOT.md` is the repo's answer to "why don't you use X?" for **zk-memory** — the host-agnostic
library. It is **not** about `hermes-zk-memory` (a thin wrapper; its only job is Hermes plumbing).

The file is the **application of `PRINCIPLES.md` to named systems** — it exists so the merits
(Z-principles, P2/P3/P9) have a place to defend themselves against recurring suggestions, and so
the reasoning isn't re-derived from scratch every time someone asks.

## Discipline (the contract)

1. **Anchored, never free-floating.** Every "why not" cites the Z/P-principle(s) it protects
   (e.g. Z1 write-time judgment, Z9 shared corpus, Z10 capture-fast-integrate-later, P2 standalone,
   P3 LLM-as-injected-callable, P9 thin adapter). The doc must not *invent* reasons that aren't in
   PRINCIPLES — if you want a reason that isn't there, add/change the principle first. This is what
   stops the doc from rotting: the anchor can't drift from the design.
2. **One decisive reason per system** — not a feature matrix. Name what it is in one line, then the
   single reason(s) we're not it. No scorecards, no "X has this but we have that" tables.
3. **It is about the LIBRARY.** Don't compare wrappers/deployments; compare the core capability and
   design intent of zk-memory against the other system.
4. **Never duplicates PRINCIPLES.md.** PRINCIPLES is the anchor; WHY-NOT is its external-facing
   application. Two different jobs, one source of truth.
5. **Dated.** Header carries the date it was last reviewed/updated, matching PRINCIPLES.

## Basis (what a good entry looks like)

- **What it is** — one line, concrete (language/runtime + the one thing it does).
- **The decisive mismatch** — anchored to a principle. Prefer the strongest, most durable reason,
  not a list of minor ones. "It's Node and we're Python" (P2) beats "its CLI flags differ."
- **What it'd cost us** — the concrete tradeoff (infra, runtime, recall, write-side judgment).
- **Keep it current** — a competitor changing is a trigger to re-check the entry, not to delete it.

## Operations

- **Add an entry** when a system is *proposed* (in a review, an issue, a conversation). This skill's
  trigger is the queue: "why not X" / "should we use X" → add or update the entry.
- **Update an entry** when a competitor materially changes (new runtime, dropped feature, pivots).
  Re-verify the decisive reason still holds; if it does, refresh the date; if it doesn't, revise.
- **The maintenance rule lives in AGENTS.md** so future agents keep the file honest: "Why not X? See
  WHY-NOT.md — add a line when a system is proposed, update when one changes."
- **Linking:** AGENTS.md links to WHY-NOT.md; WHY-NOT.md links back to PRINCIPLES.md (the anchor).
- **Registering:** this skill should be reachable as `~/.agents/skills/zk-memory` (see the repo's
  `skills/caretaker` skill for fleet-ops registration of the repo's skills).

## References

- `WHY-NOT.md` — the file this skill maintains.
- `PRINCIPLES.md` — the anchor every entry cites.
- `AGENTS.md` — the charter; carries the maintenance rule and the link.
