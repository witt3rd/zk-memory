# Why not X?

Why **zk-memory** (the host-agnostic library — not the `hermes-zk-memory` wrapper) is not some
other agentic-memory system. This file is the **application of `PRINCIPLES.md` to named systems**:
every entry cites the Z/P-principle it protects, so it can't drift from the design. It is not a
feature matrix — one decisive reason per system.

**Maintained by `skills/why-not/`.** Add a line when a system is proposed; update when one
materially changes. Last reviewed: 2026-08-18.

---

## QMD (`@tobilu/qmd`)

**What it is:** an on-device hybrid search engine for markdown — BM25 full-text + vector semantic
search (node-llama-cpp, GGUF models) + LLM re-ranking, fused with RRF. A TypeScript/Node/Bun CLI
and library with collections, context trees, and an MCP server.

**Why not:** it's a *search engine over documents you already have*, not a *memory you write to*.
Three decisive mismatches:

- **No write-side judgment (Z1).** QMD indexes whatever you point it at; it has no retain/distill
  pipeline, no concept/entity_update/decision kinds, no append-only merge, no gardener. It recalls;
  it doesn't curate. zk-memory's whole value is write-time judgment — QMD is recall-only.
- **Runtime + infra (P2, Z9).** QMD is Node/TypeScript and pulls a per-host embedding stack
  (GGUF model downloads, HyDE, reranking). zk-memory is Python, host-agnostic, and for shared
  corpora deliberately uses the stateless `rg` backend (Z9) to avoid exactly this kind of
  index/model infrastructure across hosts. Adopting QMD would be *more* infra, not less.
- **Different job (Z10).** QMD doesn't integrate or merge — it doesn't fold duplicates, grow a
  link graph, or reconcile recent writes. That's the gardener's job in zk-memory.

**What it'd cost us:** a Node bridge (subprocess/CLI) instead of an in-process Python API, a
per-host model/index stack that fights the shared-corpus design, and no gain on the write side —
which is the side that matters.

---

## How to add an entry

Follow `skills/why-not/SKILL.md`: one line for what it is, the decisive reason(s) anchored to a
Z/P-principle, what it'd cost us. If the reason you want isn't in `PRINCIPLES.md`, change the
principle first — the doc must not invent reasons the design doesn't own.
