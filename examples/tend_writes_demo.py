#!/usr/bin/env python3
"""Worked example: the tend_writes gardener pass.

Shows the capture-fast / integrate-later story: the write path accepts an
arbitrary, cheap write (no pre-flight judgment), and the tend pass treats
recent writes as the highest-priority candidates for the delayed,
careful integration — merging duplicates (append-only, retired to
.archive/) and linking kept notes into the graph.

Self-contained: a stub StructuredLLM drives the judge, no network/hermes.

    python examples/tend_writes_demo.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from zk_memory import Memory


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "zk"

        # Phase 1 — capture fast: the write path accepts a cheap, arbitrary
        # note with mechanical safety only. No pre-flight judgment.
        Memory(root=root).write(
            "rollback-strategy",
            "Rollback Strategy",
            "We adopted blue-green deploys. Keep last-deploy-k for rollback.",
        )
        Memory(root=root).write(
            "rollback-strategy-keep-k",
            "Rollback Strategy",
            "We should also keep last-deploy-k valid for rollback.",
        )

        # Phase 2 — integrate later: tend_writes treats the recent writes as
        # candidates. A stub judge folds the duplicate into the canonical note.
        class StubJudge:
            def __init__(self, target):
                self._target = target
            def __call__(self, messages, *, schema, name):
                return {"action": "merge", "merge_target_ref": self._target}

        canonical = next(f for f in root.glob("*.md") if f.name.endswith("-rollback-strategy.md"))
        target_uuid = _read_uuid(canonical)
        m = Memory(root=root, llm=StubJudge(target_uuid))

        results = m.tend_writes(limit=10)
        print("tend_writes results:")
        for r in results:
            print(f"  {r['slug']}: {r['action']}" + (f" -> {r['target'][:8]}" if r.get("target") else ""))

        print(f"\ncanonical note now contains the duplicate's content:\n")
        print(canonical.read_text())
        print("duplicate retired to .archive/:",
              any((root / ".archive").glob("*.md")))


def _read_uuid(path: Path) -> str:
    for line in path.read_text().splitlines():
        if line.startswith("uuid:"):
            return line.split(":", 1)[1].strip()
    return ""


if __name__ == "__main__":
    main()