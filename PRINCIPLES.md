# zk-memory — Principles

Load-bearing principles for the **zk-memory** library and **hermes-zk-memory** adapter. Each principle names a failure mode it prevents. The two P2/P3/P9 principles are shared with the prospecta split; the rest are zk-specific.

---

## P2 — Standalone first, plugin second

The library has no Hermes dependency. The Hermes plugin is one of N possible adapters. If a design move requires Hermes-specific knowledge inside the library, that's a code smell: the move belongs in the adapter.

**Prevents:** coupling that prevents scripts, notebooks, and future agents from using zk-memory directly.

## P3 — LLM as injected callable, not provider

The library takes a single `llm` callable from the caller. No `openai`, `anthropic`, `litellm`, `agent.auxiliary_client` imports. The Hermes adapter implements the `StructuredLLM` protocol via the auxiliary-task forced-tool-call path; a notebook implements it with whatever JSON mode it has. The library owns prompts, schemas, and orchestration — never the client.

**Prevents:** locking the library to one provider; turning it into a provider-routing layer it's not.

## P9 — Hermes plugin is a thin adapter, not a fork

`hermes-zk-memory`'s `__init__.py` constructs `Memory`, delegates every method, owns nothing of substance. If the plugin starts reimplementing merge-or-create or distill orchestration, the library is missing a feature — push it down.

**Prevents:** the plugin reimplementing library logic for Hermes-flavored reasons; divergence between standalone and plugin behavior.

## Z1 — Write-time judgment, not a transcript log

The corpus is curated atomic notes, judged at write time by an LLM. The plugin's `sync_turn` / `on_pre_compress` are an optional convenience over the same corpus operations as the tool surface, not a separate parallel code path. Retention is not deferred to recall-time ranking of everything.

**Prevents:** the corpus degrading into a transcript dump; "auto" and "volitional" diverging.

## Z2 — Append-only merge; collision-safe write

`merge` appends a dated fragment under a corpus-wide flock — never rewrites existing prose, so a bad merge can at worst add a wrong fragment. `write` refuses to overwrite an existing file. These are the only read-modify-write on the corpus, and the only place a lock matters.

**Prevents:** content destruction from a wrong merge; interleaved appends from concurrent writers.

## Z3 — Merge into the same entity, prefer create

The merge judge merges only when an existing note is truly the same entity/topic — not merely related. A `merge_target_ref` not among the fetched hits is never trusted (falls back to create). When in doubt, prefer create: a wrong merge pollutes an existing note; a missed merge is slight duplication — the safer failure.

**Prevents:** polluting a note with loosely-related content; hallucinated merge targets corrupting the corpus.

## Z4 — Concept vs entity_update split is load-bearing

`concept` is a self-contained evergreen idea worth its own note; `entity_update` is a temporal/attribute-level fact that would be a useless orphan on its own and belongs appended to an existing entity note. Conflating them ruins the corpus. The retain prompts and schemas own this split.

**Prevents:** orphaned fact-notes; entity updates silently lost because they didn't fit "new note" logic.

## Z5 — No LLM is a supported state, not a degraded one

`Memory(root)` without an `llm` is fully usable: corpus ops work; `retain_*` return empty/no-ops. The library never hard-requires an LLM. The Hermes adapter's `_resolve_client` miss degrades the same way.

**Prevents:** the library raising on a missing provider where a graceful no-op is correct; blocking file-level use behind an LLM dependency.

## Z6 — Prompts and schemas live in the library

All retain prompt text and JSON schemas live in `zk_memory.judge`. Adapters are mechanical: they route a `StructuredLLM` call through their forced-tool / JSON-mode path using the supplied schema and name, and look up the tool description from `judge.TOOL_DESCRIPTIONS`. The Hermes adapter does not fork prompt language.

**Prevents:** prompt drift between standalone and plugin behavior; the adapter shipping its own copy of the judgment logic.

## Z7 — Diagnostics never break the decision

`probe.trace` is best-effort: a trace failure must never break the retain/recall it's describing. Tracer is injected (`Memory(tracer=...)`) so a caller can substitute its own; the default writes `.zk-memory-trace.jsonl` beside the corpus.

**Prevents:** observability becoming a hard dependency or a failure point.

## Z8 — Tests over prose specifications

Library API is locked by tests, not docstrings. Corpus ops have direct tests against a real temp corpus; judge tests use `StructuredLLM` stubs — never fake OpenAI clients. The retain pipeline has end-to-end tests over `Memory.retain_turn` / `retain_messages` / `process_candidate`.

**Prevents:** API drift; "we changed the return shape and forgot to update three callers"; confident-sounding docs that don't match code.

## Z9 — Shared corpora are a first-class mode

A corpus can live on a shared volume (a NAS) that every host/agent reads and writes. The design must keep that safe without a central server:

- **Search uses the `rg` backend on a shared root** (`backend="rg"` / `ZK_MEMORY_BACKEND`). LanceDB is single-writer; a shared mutable index corrupts under concurrent hosts and goes stale. `rg` is stateless and reads live files.
- **Writes are collision-safe; merges are append-only O_APPEND.** Concurrent writers degrade to "a note already exists" or an interleaved fragment — never corruption. `flock` is best-effort only across hosts (NFS `local_lock=none`); the append atomicity is the real guarantee.
- **Attribution is optional but on by default for the caller** — `source=` (host/agent name, or `ZK_MEMORY_SOURCE`) lands in the note's frontmatter / append line, so a collective memory stays answerable ("who wrote this").
- **Maintenance is single-owner.** `tend`/`check`/`repair`/`mint` rewrite files; one caretaker host owns them, the rest only search/read/write/merge.

**Prevents:** a shared-memory deployment silently corrupting its index or content; "whose note is this" being unanswerable; two hosts racing `linlink repair`.

## Z10 — Capture fast, integrate later; recency is the priority

The write path is deliberately cheap — arbitrary writes with mechanical safety only (slug
sanitize, no-overwrite, linlink mint). Volitional writes are **not** taxed with a pre-flight
judgment at write time. Quality is bought later by the **gardener pass** (`Memory.tend_writes`),
which treats the most recent writes (by mtime) as the highest-priority candidates and
reconciles each against the corpus:

- **merge** — the note folds into an existing one (append-only dated fragment) and the
  duplicate retires to `.archive/`, **reversible, never deleted**.
- **link** — kept notes gain `[label](slug.md)` out-links to related notes, growing the graph.
- `decision` notes never merge (append-only history; a new decision on a topic is a new note).

Distinct from `tend` (linlink structure hygiene). The gardener is a single-caretaker job on a
shared corpus (Z9).

**Prevents:** write-time friction that discourages capture; duplicates and orphans accumulating
forever with no reconciliation; a "gardening" story that only fixes links/uuids and never
repairs content quality.

## Z11 — No free-form semantic tags

The only tag a note carries is `kind` (`concept` / `entity_update` / `decision`) — a **closed set
that drives one structural decision** (whether a note may merge). Free-form / LLM-invented tags
are **not allowed**: a tag is semantic only if some consumer acts on it, and nothing here reads
arbitrary tags — so they'd be dead frontmatter, a second crude copy of what the note's own
content + links already say. Maintaining two copies is where the noise comes from. Add a tag
only as a *bounded, closed* vocabulary paired with a real consumer (grouping, browse, recall
filter) — never "tags for future use."

**Prevents:** unbounded, unconsumed tag vocabularies becoming dead weight in every note; the
corpus carrying two divergent descriptions of the same thing (the tags and the prose).

## Z12 — De-merge to restore atomicity (the split discipline)

Merge is heavily engineered (Z2/Z3/Z10); the inverse is not. When an entity note has been merged
so many times that it's no longer atomic — more a biography than one thought — it must be
**de-merged**: the parent becomes a *summary* (less detailed, more arc) and the detail is
preserved in **atomic child notes** the parent links to. Flat corpus, no folders — "parent /
child" is expressed by links, not nesting.

Rules (mirroring the merge discipline, reversed):

- **Detect** the overgrown note — a "biography smell": disproportionately long body, or many
  appended dated fragments, vs. the corpus.
- **Summarize the parent** — the LLM reduces the note to a summary spine: what the entity is,
  its arc, onward links. The parent trades detail for atomicity.
- **De-merge into children** — each distinct temporal / attribute / decision fragment becomes
  its own atomic note, using the existing `kind` taxonomy (this is the *reverse* of
  `judge_merge`'s "belongs appended here"). **`decision` fragments always become standalone
  `decision` zettels** — never folded or destroyed.
- **Link, don't nest** — the parent links `[detail](child-slug.md)` out; children link back.
- **Append-only, reversible** — the original biography is **retired to `.archive/`**, never
  deleted; parent + children are new notes. A wrong split is recoverable (Z2/Z10).

**Prefer not to split** — split only when the note is genuinely a biography (many *distinct*
entities / a timeline), never carve a single dense idea into orphans (the failure Z4 warns
about). A split judge must be at least as conservative as the merge judge.

**Prevents:** the corpus degrading into long, un-navigable "biography" notes that defeat atomic
recall; the loss of detail when a note grows (the split preserves detail in children, only the
parent summarizes); never being able to recover atomicity once merging has overgrown a note.

---

*Authored 2026-08-17 as part of the split from hermes-zk-memory into a host-agnostic library plus thin Hermes adapter (same shape as the prospecta / hermes-prospecta split).*