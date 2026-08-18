# HANDOFF — zk-memory

**Last updated:** 2026-08-18 — v0.4.0 (tend_writes gardener) landed; docs + caretaker skill current.

## State

- `main` at `origin/main` tip (`25304e1`), tagged **`v0.4.0`** (pushed). Primary clone clean.
- **86 tests pass** (`.venv/bin/python -m pytest`). Venv: `uv venv .venv` + `uv pip install -e . pytest`.
- `AGENTS.md` (charter; `README.md` is a symlink) + `PRINCIPLES.md` (Z-principles) + `skills/caretaker/` all current.
- GitHub: `witt3rd/zk-memory` (public).

## What this is

The **host-agnostic library half** of the split (same shape as `prospecta`/`hermes-prospecta`):
a zettelkasten memory with zero Hermes / `agent.*` / LLM-provider imports. The Hermes plugin
`hermes-zk-memory` is a thin `MemoryProvider` adapter over it, pinned to `@v0.4.0`.

## Version history (all landed)

- **v0.1.0** — the split (corpus/fts/judge/retain/memory/probe/cli).
- **v0.2.0** — shared/multi-host corpus: `backend="rg"` knob + `source` attribution (Z9).
- **v0.3.0** — first-class `decision` retain kind (choice/alternatives/rationale), never merges.
- **v0.4.0** — `Memory.tend_writes()`: the content gardener pass (capture fast, integrate later;
  recent writes = priority; merge + retire to `.archive/`, or link). Z10. Also: `write()` now
  re-ensures `title:`/`date:` after linlink mint (was stripping titles), and retain stamps `kind:`
  into notes.

## Where I left off

Nothing open in this repo. Plugin pinned to `@v0.4.0` (hermes-zk-memory PR #7 merged). The fleet
deployment (shared NAS corpus) is the next frontier but is fleet-ops work, not this repo.

## Gotchas

- **`Memory.search` arg order** — `corpus.search(query, root, ...)`; `Memory.search` wraps `corpus.search(query, self._root, ...)`.
- **`judge.py` never swallows exceptions** — `StructuredLLM` is contracted "Never raise"; pipeline try/except in `retain.py`/`tend.py` is the safety net.
- **`linlink mint` strips frontmatter to just `uuid:`** — re-ensure any field (title/date/kind/author) after mint.
- **Tend search is title-based** — notes whose titles don't overlap aren't surfaced as duplicates (known limitation, not a bug).
- **rg fallback test trick** — fake `zk_memory.fts` raising `ImportError`, or pass `backend="rg"`.
- `examples/*_demo.py` must stay runnable (`python examples/...` from the venv).

## Next

1. Optional: register `skills/caretaker/` in fleet-ops (`~/.agents/skills/zk-memory` → repo `skills/`) per the caretaker skill.
2. Fleet shared-corpus deployment (NAS mount, setgid perms, one caretaker host running `tend_writes` on a schedule) — fleet-ops work.
3. Optional later: CLI `retain`/`tend-writes` with an `--llm`; PyPI publish.
