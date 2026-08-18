#!/usr/bin/env python3
"""Worked example: the `decision` retain kind.

A self-contained, dependency-free demo of zk-memory's first-class
`decision` kind — the fix for "we retain the topic of a decision but not
the decision itself."

It drives the pipeline with a stub ``StructuredLLM`` (no network, no
hermes, no provider), so it runs anywhere zk-memory is installed:

    python examples/decision_kind_demo.py

The takeaway: a decision-shaped turn ("we decided X, rejecting Y, because
Z") distills to ``kind == "decision"`` with ``choice``/``alternatives``/
``rationale``, and lands as a standalone, dated zettel whose body leads
with the decision — recallable by a future session as a fact, not just a
topic.

See ``examples/decision-kind.md`` for the full walkthrough.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from zk_memory import Memory
from zk_memory.judge import StructuredLLM


class StubStructuredLLM(StructuredLLM):
    """A canned StructuredLLM: returns the distilled decision, no LLM.

    In real use the Hermes adapter implements this via the auxiliary-task
    forced-tool-call path; a notebook implements it with JSON mode. Here
    we just short-circuit it to focus on the library surface.
    """

    def __call__(self, messages, *, schema, name):
        return {
            "worth_retaining": True,
            "candidates": [
                {
                    "kind": "decision",
                    "topic": "rollback strategy for the deploy pipeline",
                    "title": "Rollback Strategy: Blue-Green + Keep Last-Deploy-K",
                    "slug": "rollback-strategy-blue-green",
                    "choice": (
                        "adopt blue-green deploys and keep last-deploy-k "
                        "valid for rollback"
                    ),
                    "alternatives": ["warm-standby", "cold-standby"],
                    "rationale": (
                        "minimize downtime and make rollback a fast, safe reconnect"
                    ),
                    "content": (
                        "We decided to adopt blue-green deploys and keep "
                        "last-deploy-k valid for rollback, rejecting "
                        "warm-standby and cold-standby to minimize downtime "
                        "and make rollback a fast, safe reconnect."
                    ),
                }
            ],
        }


def main() -> None:
    # The decision-shaped turn from the live investigation.
    turn = (
        "Let's decide the rollback strategy for the deploy pipeline. I think "
        "we should adopt blue-green deploys and keep last-deploy-k valid for "
        "rollback."
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "zk"
        memory = Memory(root=root, llm=StubStructuredLLM())

        labels = memory.retain_turn(turn, "agreed — blue-green, keep last-deploy-k")

        print(f"retained labels: {labels}")
        note = next(root.glob("*.md"))
        print(f"\nwrote: {note.name}\n")
        print(note.read_text())

        # A future session asking "what did we decide about rollback?" finds
        # the note (rg-backed search), and its body carries the decision.
        hits = memory.search("what did we decide about rollback")
        if hits:
            ref = hits[0]["uuid"] or hits[0]["slug"]
            body = memory.read(ref)["note"]["body"]
            decided = "blue-green" in body and "last-deploy-k" in body
            print(f"search found {len(hits)} hit(s); decision present in body: {decided}")


if __name__ == "__main__":
    main()