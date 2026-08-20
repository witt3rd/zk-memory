---
name: caretaker
description: Lived-experience caretaker skill for the zk-memory repo. Use for ANY work here — understanding the split from hermes-zk-memory, the retain/tend pipelines, the shared-corpus mode, test discipline, house git, and the caretaker loop. Triggers: zk-memory, zettelkasten, corpus, retain, tend, decision kind, gardener, memory library.
metadata:
  home: /home/dt/src/witt3rd/zk-memory
  scope: repo
---

# zk-memory — caretaker

Lived experience for stewarding `~/src/witt3rd/zk-memory`, the host-agnostic zettelkasten memory
library. The **charter is `AGENTS.md`** — read it first (goals, merits, concepts, mechanisms, Z
principles in `PRINCIPLES.md`). This skill is how the caretaker acts; the repo's own docs carry the
why.

## The split (context)

zk-memory is the **library half** of a split identical to `prospecta`/`hermes-prospecta`: a
host-agnostic zettelkasten memory (zero Hermes / `agent.*` / LLM-provider imports), with the Hermes
plugin `hermes-zk-memory` as a thin `MemoryProvider` adapter. It is tagged/pinned by the plugin
(`zk-memory @ ...@v<tag>` in the plugin's `pyproject.toml` + `plugin.yaml`).

## Package map

```
zk_memory/
  __init__.py   # exports Memory + module-level corpus functions
  memory.py     # Memory(root, llm=None, tracer=None, backend=..., source=...)
  corpus.py     # list/search/read/write/merge/tend + helpers
  fts.py        # LanceDB FTS backend (optional; search falls back to rg)
  retain.py     # retain_turn / retain_messages / process_candidate (write-time judgment)
  judge.py      # StructuredLLM protocol + distill/merge prompts & schemas
  tend.py       # tend_writes: the content gardener pass
  probe.py      # trace(event, root, **fields) -> .zk-memory-trace.jsonl
  cli/          # search/read/write/merge/tend/tend-writes/list (thin)
examples/       # worked examples (decision kind, tend_writes) + runnable demos
tests/          # pytest suite
```

## Key modes (see PRINCIPLES.md Z-principles)

- **Write-time judgment** (Z1): retain distills turns into `concept` / `entity_update` / `decision`
  candidates; `process_candidate` searches + `judge_merge`s (merge-vs-create) then writes/merges.
- **Decisions** (Z1/decision kind): first-class `decision` kind with `choice`/`alternatives`/
  `rationale`; always a standalone dated zettel, **never merged** (append-only history).
- **Shared corpora** (Z9): use `backend="rg"` on a NAS root (LanceDB is single-writer, unsafe to
  share); writes are collision-safe, merges append-only O_APPEND; `source=` attribution; one
  caretaker host owns `tend`.
- **Capture fast, integrate later** (Z10): the write path is cheap/arbitrary; `Memory.tend_writes`
  walks recent writes (mtime desc) and merges (append-only fold + retire duplicate to `.archive/`,
  reversible) or links (appends `[label](slug.md)`), decisions never merge.

## Test discipline

- `pytest` in the repo root. Judge tests use **`StructuredLLM` stubs**, never fake OpenAI clients.
- Force the rg fallback deterministically: install a fake `zk_memory.fts` in `sys.modules` whose
  `run_fts` raises `ImportError`, or pass `backend="rg"`.
- `conftest.py` `_no_linlink_by_default` patches `zk_memory.corpus.shutil.which` so writes take the
  own-uuid path (deterministic, no machine linlink).

## Hard-won gotchas

- **`Memory.search` arg order** — `corpus.search(query, root, ...)` takes `query` first; `Memory.search`
  wraps `corpus.search(query, self._root, ...)`. Flipped once, caught by test.
- **`judge.py` never swallows exceptions** — `StructuredLLM` is contracted "Never raise"; the pipeline
  safety net is `retain.py` / `tend.py` try/except. Don't add defensive catches in judge.
- **`linlink mint` strips frontmatter to just `uuid:`** — `corpus.write` re-ensures `title:`/`date:` after
  mint (and retain stamps `kind:`). If you add frontmatter fields, remember mint wipes them; re-ensure
  after.
- **Tend search is title-based** — `tend_writes` searches by a note's title; notes whose titles don't
  overlap won't be surfaced as duplicates. Known limitation, not a bug.
- **Vendored `os`/`shutil` in corpus** are the real shared modules — when patching `which`, capture the
  original first (see conftest) or the patch recurses.

## House git + caretaker loop

- **Worktrees only** (`git wt-new <branch>` under `zk-memory.wt/`); primary clone stays on `main`.
- Version bumps are real: feature → minor, tag + push the tag separately (`git tag -a vX.Y.Z -m ...`),
  then the plugin repins (`@vX.Y.Z`). Land the library, tag, then repin the plugin.
- **Caretaker loop**: orient via the signalling handoff (`scripts/agent state` / latest `H--`
  event in `.agent/log/`) → verify `pytest` green → leave the repo at the clean end-state (no
  stale worktrees, no leftover branches, main at origin tip). Write a handoff on sleep with
  `scripts/agent handoff <subject>` — **not** a HANDOFF.md (that convention is retired).
- Retire `examples/` demos stay runnable: `python examples/*_demo.py` from the venv.

## References

- `AGENTS.md` — charter. `PRINCIPLES.md` — Z-principles (the anchor).
- `examples/decision-kind.md`, `examples/tend-writes.md` — worked examples.
- Sibling: `hermes-zk-memory` caretaker skill + the `caretaker` machine skill.
