# Worked example — the `tend_writes` gardener pass

The write path is deliberately **cheap**: arbitrary writes, mechanical safety only
(slug sanitize, no-overwrite, linlink mint). No pre-flight judgment. This is the
intake philosophy — capture fast.

The **gardener pass** (`Memory.tend_writes`) is the delayed integration. It treats the
**most recent writes as the highest-priority candidates** and reconciles each against the
corpus — this is where the careful work happens, later, not at write time.

## The model

1. **Writes are accepted as-is** — a `zk_write` or CLI write lands immediately.
2. **`tend_writes` walks recent writes** (notes sorted by mtime desc, top `limit`).
   Writes *and* merges both touch mtime, so the freshest activity is always the priority.
3. **Per candidate** (reusing the same machinery as the retain pipeline — `search`,
   `judge_merge`, `corpus.merge`):
   - **merged** — the note folds into an existing one (append-only dated fragment with
     `source` attribution), and the duplicate is **retired to `.archive/`** — reversible,
     never deleted. A wrong merge is un-doable by moving the file back.
   - **linked** — the note is kept and gains `[label](slug.md)` out-links to the related
     notes search surfaced, so the zettelkasten graph actually grows.
   - **kept** — genuinely standalone; left untouched.
   - `decision` notes **never merge** (a decision is append-only history; a new decision on
     the same topic is a new dated note) — they only get out-links.

## Run it

```bash
python examples/tend_writes_demo.py
```

Two cheap writes (a canonical note and a near-duplicate), then `tend_writes` with a stub
judge folds the duplicate into the canonical note and retires it:

```
tend_writes results:
  20260818-rollback-strategy-keep-k: merged -> 5d51e6e6
  20260818-rollback-strategy: kept

duplicate retired to .archive/: True
```

## Interface

- **Library:** `Memory(root, llm=...).tend_writes(limit=20)` — returns a list of
  `{ref, slug, action, target?, links?}` per candidate. No LLM → no-op (returns `[]`),
  same graceful path as `retain_*`.
- **CLI:** `zk-memory tend-writes --limit 20` (the bare CLI has no LLM, so it's a no-op
  there — a gardener agent drives `Memory.tend_writes(llm=...)` with its own
  `StructuredLLM`).
- **Distinct from `tend`:** `tend` (repair/check/mint) is linlink *structure* hygiene;
  `tend_writes` is *content* integration. Two different jobs, two ops.

## Why this is the right intake story

- **Volitional writes stay cheap** — the agent isn't taxed with a pre-flight judgment every
  time it wants to set something down (that's the `(b)`-everywhere you rejected).
- **Quality is bought later, recency-prioritized** — the newest, least-integrated content is
  always what the gardener looks at first.
- **Reversible-first** — merges are append-only and duplicates archive, never delete, so the
  worst case is a misplaced fragment you can undo, not a lost note.
- **Single-caretaker friendly** — this is the action a gardener agent / a scheduled pass on
  the shared fleet corpus runs (one host owns it, per Z9).

## Test evidence

`tests/test_tend_writes.py` (5 tests): no-LLM no-op, recent-first candidate ordering,
merge+archive (append-only fold, duplicate moved to `.archive/`, not deleted), kept notes
gain `## Related` out-links, and decisions never merge (linked only).

All **86 tests green** (81 pre-existing + 5 new).