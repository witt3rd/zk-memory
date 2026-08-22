# zk-memory

**A host-agnostic zettelkasten memory library.** A flat corpus of atomic
Markdown notes — one thought per note, own words, plain-markdown links, YAML
frontmatter with a uuid. Judgment happens at **write time**: an injected LLM
decides whether a turn is worth a note and drafts it; recall is full-text
search. No Hermes / `agent.*` import anywhere in the package.

The Hermes plugin `hermes-zk-memory` is one thin adapter over this library
(a `MemoryProvider` that wires an injected `StructuredLLM` to Hermes'
auxiliary-task forced-tool-call machinery). This repo is the standalone
library the plugin wraps.

## Goals

- **Host-agnostic core.** `zk_memory` has zero knowledge of Hermes, `agent.*`,
  or any LLM provider. It is embeddable by a plugin, a notebook, a script,
  another agent.
- **Write-time judgment.** The LLM decides, per turn, whether anything is
  worth retaining and drafts it — not a raw transcript log, not
  recall-time-ranking of everything.
- **Append-only, collision-safe writes.** `merge` appends a dated fragment
  under a corpus-wide flock; `write` refuses to overwrite. A bad merge can at
  worst add a wrong fragment, never destroy content.
- **One code path for volitional and automatic recall.** The same corpus
  operations back both the tool surface and the `retain_*` motions.

## Merits

The thing worth protecting:

- **Atomic notes over transcripts.** One thought per note means the corpus
  stays navigable and linkable, and merge decisions stay local.
- **The concept / entity_update split.** `entity_update` is a temporal or
  attribute-level fact that would be a useless orphan as its own note — it
  belongs appended to an existing entity note. Conflating the two kinds ruins
  the corpus.
- **Merge only into the same entity, prefer create.** A wrong merge pollutes
  an existing note; a missed merge is just slight duplication — the safer
  failure. A `merge_target_ref` not among the fetched hits is never trusted.
- **`rg` fallback.** Search never hard-fails without lancedb.
- **No free-form semantic tags.** The only tag a note carries is `kind`
  (`concept` / `entity_update` / `decision`) — a **closed set that drives one
  structural decision** (whether a note may merge). Free-form / LLM-invented
  tags are **not allowed**: a tag is semantic only if some consumer acts on
  it, and nothing here reads arbitrary tags — so they'd be dead frontmatter,
  a second crude copy of what the note's own content + links already say.
  Maintaining two copies is where the noise comes from. If a future feature
  genuinely consumes a tag (grouping, browse, recall filter), add it as a
  *bounded, closed* vocabulary with that consumer — never "tags for future
  use."

## Layout

```
zk_memory/
  __init__.py     # exports Memory + module-level corpus functions
  memory.py       # Memory(root, llm=None, tracer=None) — the embeddable object
  corpus.py       # list/search/read/write/merge/tend (all take an explicit root)
  integrate.py    # the careful-write spine: decide_merge_target + integrate (functional pipeline)
  indexing.py     # IndexProvider + EmbeddingProvider protocols; Rg/LanceDB/Auto/Vector providers; registry
  fts.py          # LanceDB FTS engine (optional; search falls back to rg)
  retain.py       # retain_turn / retain_messages / process_candidate (composes over integrate)
  split.py        # the de-merge spine: decide_split_fragments + split_note (Z12)
  judge.py        # StructuredLLM protocol + distill/merge/split prompts & schemas
  probe.py        # trace(event, root, **fields) -> <root>/.zk/trace.jsonl
  sidecar.py      # the .zk/ sidecar paths (lock, index/, trace.jsonl) — single source of truth
  cli/            # thin CLI: search / read / write / merge / tend / list / retain / integrate / split / tend-writes / split-candidates
  cli/llm.py      # CLI StructuredLLM over an OpenAI-compatible chat endpoint (optional httpx)
tests/            # corpus ops, probe, judge (StructuredLLM stubs), retain, indexing, integrate, split, cli
```

## Concepts

### Two entry points

Module-level functions take an explicit `root` — for embedders that just want
files:

```python
from zk_memory import search, read, write, merge, tend, list_notes
search("judy", root)
```

`Memory` binds a root (and optionally an LLM and a tracer):

```python
from zk_memory import Memory
m = Memory(root=Path("./zk"), llm=my_llm)   # llm optional
m.search("judy", limit=8)
m.retain_turn(user, assistant)              # distill -> merge|create
m.retain_messages(messages)                 # same pipeline over a batch
```

No LLM → `retain_*` returns empty / no-ops; corpus ops still work.

### LLM as an injected callable (P3)

The library never imports `openai` / `anthropic` / `litellm` /
`agent.auxiliary_client`. `judge.StructuredLLM` is the contract:

```python
class StructuredLLM(Protocol):
    def __call__(self, messages: list[dict[str, str]], *,
                  schema: dict, name: str) -> dict | None: ...
```

`judge.py` owns the prompts, JSON schemas, and orchestration; it calls
`llm(messages, schema=..., name=...)`. The Hermes adapter implements this
with the auxiliary-task forced-tool-call path (so live retain behavior is
unchanged); a notebook implements it with whatever JSON mode it has.

### The retain pipeline

`retain_turn(user, assistant)` (and `retain_messages(messages)` for a
compaction batch):

1. **Distill** — one call, sees only the transcript, zero corpus visibility.
   Splits it into candidates tagged `concept` (an evergreen idea),
   `entity_update` (a temporal/attribute fact that belongs on an existing
   note), or `decision` (a commitment/choice made — recorded as an
   authoritative, dated, recallable fact with choice/alternatives/rationale).
2. **Per candidate** — `search` its topic (no LLM). No hits → straight to
   create. Hits → fetch full bodies and make **one** comparison call across
   all of them (`judge_merge`) deciding merge-into-existing vs. create.
   Decisions skip this step entirely (never merge) and always become a
   standalone dated zettel.

The **write path is deliberately cheap** (arbitrary writes, mechanical
safety only). Quality is bought later by the gardener pass:
`Memory.tend_writes()` walks the most recent writes (by mtime) as the
highest-priority candidates and reconciles each — merges a duplicate into
an existing note (append-only fold) and retires it to `.archive/`
(reversible, never deleted), or appends `[label](slug.md)` out-links so
the graph grows. `decision` notes never merge. This is distinct from
`tend` (linlink structure hygiene: repair/check/mint). Capture fast,
integrate later; recency is the priority.
3. **Write** — `merge` (append-only) or `write` (new note).

### The careful-write spine (`integrate.py`)

The merge-or-create judgment is a **single functional pipeline** that three
entry points compose over, so no path re-implements it:

```
decide_merge_target(root, *, content, topic, kind, llm, index, limit,
                    exclude_ref, exclude_path) -> uuid | None
integrate(root, *, content, topic, kind, llm, ...) -> {action, target?, path?, uuid?}
```

`decide_merge_target` is the pure decision: search (no LLM) → fetch full
bodies → drop the note itself (gardener case) → decisions never merge →
one `judge_merge` across all hits → **verify** the returned ref is among the
fetched uuids (a hallucinated ref is never honored). `integrate` wraps it
with the write: append-only `corpus.merge`, or `corpus.write` (building a
decision body from choice/rationale). Callers compose over it:

- `retain.process_candidate` — capture-time flavor (create; returns label).
- `tend._reconcile_note` — gardener flavor (keep + link, or fold + archive).
- `Memory.integrate(...)` — the **public careful write**: a caller hands an
  atomic memory and gets merge-or-create with full verification. Requires
  an `llm` (returns `{action:"error"}` without one).

### The de-merge spine (`split.py`, Z12)

The inverse of merge — restores atomicity when an entity note has grown into
a biography. Mirrors the merge pair:

```
decide_split_fragments(root, *, ref, llm, max_fragments=4)
    -> {split, parent_summary, fragments} | {split: False}   (pure decision)
split_note(root, *, ref, llm, ...) -> {action, parent, children}  (decision -> write)
```

`decide_split_fragments` is the pure decision: the split judge (prefer-not-to-
split, at least as conservative as the merge judge) returns a summary parent
+ atomic children, capped at 4 (schema + defensive truncation). `split_note`
performs the write: a new summary parent, new atomic children (decisions stay
standalone `decision` zettels with choice/rationale), links between them, and
the **original biography retired to `.archive/`** (reversible, never deleted).

- `Memory.split_note(ref)` — the **volitional** entry point: the caller names
  the note to split. Requires an `llm`.
- `split_candidates(root, top=)` — the **mechanical sweep**: surfaces notes
  that *need* splitting by descending file size (no LLM). This is the **sole
  authorization** to split during gardening.
- **The gardener splits** — `tend_writes(split_sweep=N)` runs the sweep and
  de-merges the top N surfaced notes. The gardener splits **only** notes that
  came from the sweep; it must never decide on its own, mid-pass, that a note
  should be split. (`split_note` is the shared split primitive.)
- **Parent/child merge guard** — `decide_merge_target` never merges into a
  note that already has a parent/child relation with the candidate, so a
  split artifact isn't folded back into its own biography.

## Mechanisms

- **Corpus discipline.** Flat `YYYYMMDD-slug.md`; uuid minted via `linlink`
  (never hand-written), with an own-uuid fallback when linlink is absent.
  Plain-markdown links `[label](slug.md)`.
- **State footprint — one `.zk/` sidecar inside the corpus.** Everything the
  library plants (beside the notes) lives under `<root>/.zk/`, never beside
  it in the parent: the merge lock (`.zk/lock`), the LanceDB FTS index
  (`.zk/index/`), and the diagnostic trace (`.zk/trace.jsonl`). Retired notes
  go to `<root>/.archive/` (reversible, never deleted). So "adopt = point at
  the corpus dir" is self-contained — nothing leaks into the parent
  directory. See `zk_memory/sidecar.py` (single source of path truth).
- **Diagnostics.** `probe.trace(event, root, **fields)` logs at INFO and
  appends one JSONL line to `<root>/.zk/trace.jsonl`. Never raises; a trace
  failure must never break the retain it describes.
- **Shared / multi-host corpora** (e.g. a NAS every agent reads and writes).
  Use the `rg` search backend (`Memory(backend="rg")`, `search(..., backend="rg")`,
  or env `ZK_MEMORY_BACKEND=rg`) — the LanceDB index is single-writer and unsafe
  to share. Writes are collision-safe and merges are append-only, so concurrent
  writers degrade gracefully; `flock` is best-effort only across hosts (the
  O_APPEND append is the real atomicity). Pass `source=` (host/agent name, or
  env `ZK_MEMORY_SOURCE`) to `write`/`merge`/`retain_*` for attribution. Give
  `tend`/`check`/`repair`/`mint` to **one** caretaker host, never concurrent
  across hosts.
- **Recall is a pluggable engine** (`indexing.IndexProvider`). `corpus.search`
  and `Memory` resolve it three ways, in precedence: an injected
  `index=`/`Memory(index=...)` provider object (the DI seam — embedders bring
  their own remote/vector/custom engine), a `backend=` name (built-ins `auto`
  / `rg` / `fts`, or any `register_backend(name, provider)`-ed name), else the
  `ZK_MEMORY_BACKEND` env var ("auto"). The chosen provider is threaded through
  the whole recall path — `Memory.search`, `retain_*`, and `tend_writes` — so a
  shared corpus configured for `rg` never silently touches lancedb during
  retain/merge (a real bug before the abstraction). lancedb is a build-time
  extra (`zk-memory[lancedb]`); "other" providers are necessarily caller-supplied
  at runtime, hence the DI seam. Recall never hard-fails: `auto`/`rg` fall back
  to ripgrep, `fts`-only returns [] when lancedb is absent.
- **Vector recall is a DI seam, not a backend string.** `indexing.VectorProvider`
  (`zk-memory[faiss]`) is a FAISS `IndexProvider` that needs an injected
  `EmbeddingProvider` (the vector counterpart to `StructuredLLM` — the library
  never imports a provider SDK). It's injected as `Memory(index=VectorProvider(embedder))`,
  never a `backend=` name, because the embedder is caller-supplied. The index is
  built lazily and cached against a corpus signature (path+mtime+size), so it
  re-embeds only changed files. A missing faiss or embedder degrades to []
  (never hard-fails).
- **Tests.** `pytest` in the repo root. Judge tests use `StructuredLLM`
  stubs — never fake OpenAI clients. To force search down the `rg` fallback,
  install a fake `zk_memory.fts` whose `run_fts` raises `ImportError`, or
  pass `backend="rg"`.
- **Release discipline — cut versions for the consumer, not for yourself.**
  Callers pin by tag (e.g. `hermes-zk-memory` pins `zk-memory @ ...@vX.Y.Z`),
  so **a feature is only real to a consumer once it's cut into a release
  they can pin**. The standing rule: when a feature lands on `main` that a
  consumer should reach, cut a new version promptly — bump
  `pyproject.toml` + `__init__.py.__version__` together (minor for additive
  features, patch for fixes) and tag `vX.Y.Z`. Don't let a pile of landed
  features accumulate behind an old tag — a consumer pinned to that tag
  silently misses them. What we do is land the feature and cut the release;
  **re-pinning a consumer (e.g. the plugin) to the new tag is that repo's own
  caretaker's job**, not ours to go do.

## House rules

- **House git (fleet_git, two modes).** **Mode 1 — active iteration:** work on
  `main`, commit small, revertable batches frequently, and never accumulate
  uncommitted work (commit roughly every 30 min). Push to a branch and open a
  PR when a change is ready; sync with origin/main before starting and before
  a PR. **Mode 2 — parallel feature work:** use linked worktrees under
  `zk-memory.wt/<branch>/`, mechanized by `git wt-new` / `git wt-rm` — never
  hand-run `git worktree add`. Branch `docs/<x>`/`fix/<x>`/`feat/<x>`/
  `task/<x>` → folder `docs--<x>`; the `.wt/` namespace is **flat** (one
  worktree per branch, never category subfolders). This repo has no prior
  mainline — the first commit *is* main.
  **Clean end-state (contract):** no stale worktrees, no leftover local
  branches beyond `main`, mainline at origin tip, primary clone clean. End
  every session by restoring it — or recording the deliberate deviation.
- **Handoff channel.** This repo is dotagent-inhabited (`scripts/agent`), so
  handoffs go through the event log: `scripts/agent handoff <subject>` and
  orient via `scripts/agent state`. Never maintain a `.agent/HANDOFF.md` —
  that convention is retired; the ledger replaces it.
- **AGENTS.md is the single source of truth.** `README.md` is a symlink to
  this file for GitHub; there is no separate human doc to keep in sync.
- **Reversible-first.** Prefer changes that are easy to revert; never leave
  the repo worse than you found it.
- **All functionality and configuration is reachable via CLI arguments.**
  Nothing is library-only. Every `Memory` method and corpus function has a
  `zk-memory <command>` counterpart, and every knob (root, backend, LLM
  endpoint, split cap, kinds, source) is a CLI argument or env var — not a
  code change. LLM-backed commands take `--llm-model/--llm-base/--llm-key`
  (or `ZK_MEMORY_LLM_*` / `OMNIROUTE_*` env). The library itself stays
  provider-free; the CLI's `cli/llm.py` is the one place an HTTP client is
  imported (lazily, via the `cli-llm` extra).

## Relationship to hermes-zk-memory

`hermes-zk-memory` (a separate repo, also in `witt3rd`) is the Hermes
`MemoryProvider` wrapper. Its `__init__.py` constructs `Memory` with an
adapter LLM, owns the tool text formatting / threading / config / auxiliary
task registration, and delegates every substantive op to this library. If
you find yourself reimplementing merge-or-create in the plugin, push it down
here instead (P9).

## Why not X?

`WHY-NOT.md` answers "why don't you use mem0 / LangMem / Khoj / QMD / ...?"
for this library, anchored to `PRINCIPLES.md`. **Maintenance rule:** add a
line when a system is proposed; update when one materially changes. See
`skills/why-not/` for the discipline.