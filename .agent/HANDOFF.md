# HANDOFF — zk-memory

**Last updated:** 2026-08-17 — split complete; `v0.1.0` tagged; plugin pinned to it.

## State

- `main` at `origin/main` tip, tagged **`v0.1.0`** (pushed). Primary clone clean.
- **59 tests pass** (`.venv/bin/python -m pytest`). Venv: `uv venv .venv` + `uv pip install -e . pytest`.
- GitHub: `witt3rd/zk-memory` (public, matches sibling repos).

## What this is

The **host-agnostic library half** of the split (same shape as `prospecta` / `hermes-prospecta`):
a zettelkasten memory with zero Hermes / `agent.*` / LLM-provider imports. The Hermes plugin
`witt3rd/hermes-zk-memory` is a thin `MemoryProvider` adapter over it, pinned to `@v0.1.0`.

## What changed

- **`zk_memory/corpus.py`** — list/search/read/write/merge/tend (moved from `hermes-zk-memory`'s `zk.py`, `lancedb_fts.py` → `fts.py`).
- **`zk_memory/judge.py`** — `StructuredLLM` protocol + the distill/merge prompts & JSON schemas (formerly `hermes-zk-memory/llm.py`'s `_DISTILL_*` / `_MERGE_JUDGE_*`) + `TOOL_DESCRIPTIONS`.
- **`zk_memory/retain.py`** — `retain_turn` / `retain_messages` / `process_candidate` (moved out of the provider's `_process_candidate`).
- **`zk_memory/memory.py`** — `Memory(root, llm=None, tracer=None)` — the embeddable object.
- **`zk_memory/probe.py`** — `trace(event, root, **fields)` → `.zk-memory-trace.jsonl` beside the corpus.
- **`zk_memory/cli/`** — thin CLI: search / read / write / merge / tend / list (`--root` or `ZK_MEMORY_ROOT`).
- **`AGENTS.md`** (charter; `README.md` is a symlink to it), **`PRINCIPLES.md`** (P2/P3/P9 + zk-specific Z1–Z8).

## Where I left off

The plugin split (PR #2) is **merged**. `hermes-zk-memory` PR **#3** is open to pin its git dep to
this repo's `@v0.1.0` tag. No open work here; this repo is complete for the split.

## Gotchas

- **`Memory.search` arg order** — `corpus.search(query, root, ...)` takes `query` first; `Memory.search`
  wraps it as `corpus.search(query, self._root, ...)`. (Flipped once, caught by test.)
- **No exception-swallowing in `judge.py`** — `StructuredLLM` is contracted to "Never raise". The
  pipeline safety net is `retain.py`'s `retain_turn`/`retain_messages`/`process_candidate` try/except.
  Judge tests use `StructuredLLM` stubs, never fake OpenAI clients.
- **Search fallback test trick** — to force `corpus.search` down the `rg` fallback deterministically,
  install a fake `zk_memory.fts` in `sys.modules` whose `run_fts` raises `ImportError`.
- **CLI** works against a real corpus; `linlink` (on PATH) mints uuids. `read`/`merge` take the
  canonical ref: uuid, or full filename stem (`20260817-hello`), not the bare slug.
- **Tag hygiene** — `v0.1.0` is an annotated tag on the mainline tip. Future release bumps should
  tag + push the tag separately from the commit.

## Next

1. Merge plugin PR #3 (pin to `@v0.1.0`); nothing needed in this repo to support it.
2. Optional later (explicitly out of scope for the split): CLI `retain` (needs an `--llm`), PyPI publish.