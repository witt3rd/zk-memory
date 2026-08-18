"""The `decision` retain kind: a commitment/choice recorded as an
authoritative, dated, standalone zettel — recallable as a fact, not just
as a topic. Decisions never merge into a generic concept note; a later
decision on the same topic becomes a new dated note (both kept as
history)."""

from __future__ import annotations

from pathlib import Path

import pytest

from zk_memory import Memory, corpus
from zk_memory.memory import Memory as MemoryCls


class _ScriptedLLM:
    """StructuredLLM stub returning results in order, keyed by call name."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def __call__(self, messages, *, schema, name):
        self.calls.append(name)
        return self._results.pop(0) if self._results else None


DECISION = {
    "kind": "decision",
    "topic": "rollback strategy for the deploy pipeline",
    "title": "Rollback Strategy: Blue-Green + Keep Last-Deploy-K",
    "slug": "rollback-strategy-blue-green",
    "choice": "adopt blue-green deploys and keep last-deploy-k valid for rollback",
    "alternatives": ["warm-standby", "cold-standby"],
    "rationale": "minimize downtime and make rollback a fast, safe reconnect",
    "content": (
        "We decided to adopt blue-green deploys and keep last-deploy-k "
        "valid for rollback, rejecting warm-standby and cold-standby to "
        "minimize downtime and make rollback a fast, safe reconnect."
    ),
}


@pytest.fixture
def root(tmp_path):
    return tmp_path / "zk"


def _md_files(root):
    if not root.exists():
        return []
    return list(root.glob("*.md"))


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def test_distill_schema_has_decision_kind(judge_module):
    props = judge_module._DISTILL_SCHEMA["properties"]["candidates"]["items"]["properties"]
    assert "decision" in props["kind"]["enum"]
    assert "choice" in props
    assert "alternatives" in props
    assert "rationale" in props


# ---------------------------------------------------------------------------
# distill
# ---------------------------------------------------------------------------


def test_distill_text_keeps_decision_candidate(judge_module):
    stub = _ScriptedLLM([{"worth_retaining": True, "candidates": [DECISION]}])
    result = judge_module.distill_text(
        "Let's decide the rollback strategy. I think we should adopt blue-green.",
        stub,
    )
    assert result == [DECISION]
    assert result[0]["kind"] == "decision"
    assert result[0]["choice"]
    assert result[0]["rationale"]


# ---------------------------------------------------------------------------
# retain routing: decisions are standalone, dated, never merged
# ---------------------------------------------------------------------------


def test_retain_turn_creates_decision_zettel_and_never_merges(root, monkeypatch):
    """Even with a search hit, a decision is created as its own note and
    judge_merge is never called — merging would bury the authoritative
    choice in a generic note."""
    monkeypatch.setattr(
        corpus, "search",
        lambda q, root, **kw: [{"uuid": "existing-concept", "slug": "rollback-tradeoffs"}],
    )
    stub = _ScriptedLLM([{"worth_retaining": True, "candidates": [DECISION]}])
    m = MemoryCls(root=root, llm=stub)

    labels = m.retain_turn("we decided to adopt blue-green deploys", "agreed")

    assert labels == ["Rollback Strategy: Blue-Green + Keep Last-Deploy-K"]
    # Only the distill call — no judge_merge call for a decision.
    assert stub.calls == ["record_candidates"]
    files = _md_files(root)
    assert len(files) == 1
    text = files[0].read_text()
    assert "blue-green" in text
    assert "last-deploy-k" in text
    assert "rejecting warm-standby" in text  # alternatives in the body


def test_decision_body_leads_with_choice(root, monkeypatch):
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [])
    stub = _ScriptedLLM([{"worth_retaining": True, "candidates": [DECISION]}])
    m = MemoryCls(root=root, llm=stub)
    m.retain_turn("u", "a")
    files = _md_files(root)
    assert len(files) == 1
    text = files[0].read_text()
    assert "**Decision:** adopt blue-green deploys" in text
    assert "**Rationale:** minimize downtime" in text


def test_decision_is_append_only_same_day_duplicate(root, monkeypatch):
    """A same-day, same-slug duplicate decision is refused (write is
    collision-safe), never overwriting the original — the first decision
    stays intact as history."""
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [])
    stub = _ScriptedLLM([
        {"worth_retaining": True, "candidates": [DECISION]},
        {"worth_retaining": True, "candidates": [DECISION]},
    ])
    m = MemoryCls(root=root, llm=stub)

    first = m.retain_turn("decide X", "yes")
    second = m.retain_turn("decide X again", "yes")

    assert first == ["Rollback Strategy: Blue-Green + Keep Last-Deploy-K"]
    assert second == []  # duplicate slug refused, not overwritten
    files = _md_files(root)
    assert len(files) == 1
    text = files[0].read_text()
    assert text.count("**Decision:** adopt blue-green") == 1


def test_decision_incomplete_skipped(root, monkeypatch):
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [])
    incomplete = {"kind": "decision", "topic": "x", "title": "", "slug": "", "content": ""}
    stub = _ScriptedLLM([{"worth_retaining": True, "candidates": [incomplete]}])
    m = MemoryCls(root=root, llm=stub)
    assert m.retain_turn("u", "a") == []
    assert _md_files(root) == []


def test_decision_searchable_by_choice(root):
    """Acceptance check: a later session asking about the decision finds a
    note whose body carries the choice, not just the topic. Uses the real
    rg-backed search (no lancedb in the test venv)."""
    import shutil
    if not shutil.which("rg"):
        pytest.skip("rg not on PATH")
    w = Memory(root=root).write(
        DECISION["slug"], DECISION["title"], DECISION["content"]
    )
    assert w["ok"]

    hits = Memory(root=root).search("rollback", limit=8)
    assert any(h.get("slug", "").startswith("2026") and "rollback" in h.get("slug", "") for h in hits)
    # read the top rollback hit and confirm the choice survived
    ref = hits[0]["uuid"] or hits[0]["slug"]
    note = Memory(root=root).read(ref)["note"]
    assert "blue-green" in note["body"]
    assert "last-deploy-k" in note["body"]