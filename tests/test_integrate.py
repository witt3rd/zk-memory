"""Memory.integrate / decide_merge_target — the careful-write spine.

The same search → judge → verify → merge|create pipeline that retain
(process_candidate) and tend (_reconcile_note) compose over, exposed as a
public careful write. Driven by injected StructuredLLM stubs; corpus.search
is monkeypatched for deterministic hit sets.
"""

from __future__ import annotations

import pytest

from zk_memory import Memory, corpus
from zk_memory.integrate import decide_merge_target


class _ScriptedLLM:
    def __init__(self, results):
        self._results = list(results)

    def __call__(self, messages, *, schema, name):
        return self._results.pop(0) if self._results else None


@pytest.fixture
def root(tmp_path):
    return tmp_path / "zk"


def _write(root, slug, title, body):
    return Memory(root=root).write(slug, title, body)


def test_integrate_creates_when_no_hits(root, monkeypatch):
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [])
    m = Memory(root=root, llm=_ScriptedLLM([]))
    out = m.integrate("One idea per note.", topic="atomic notes", title="Atomic Notes", slug="atomic-notes")
    assert out["action"] == "created"
    assert out["uuid"]
    notes = [f for f in root.glob("*.md") if "atomic-notes" in f.name]
    assert len(notes) == 1
    assert "One idea per note." in notes[0].read_text()


def test_integrate_merges_into_verified_hit(root, monkeypatch):
    seed = _write(root, "judy", "Judy", "Judy is a colleague.")
    target_uuid = seed["uuid"]
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [{"uuid": target_uuid, "slug": "judy"}])
    m = Memory(root=root, llm=_ScriptedLLM([{"action": "merge", "merge_target_ref": target_uuid}]))
    out = m.integrate("Judy arrives in two weeks.", topic="Judy", kind="entity_update")
    assert out["action"] == "merged"
    assert out["target"] == target_uuid
    text = Memory(root=root).read(target_uuid)["note"]["body"]
    assert "Judy arrives in two weeks." in text
    # no new note created
    assert len(list(root.glob("*.md"))) == 1


def test_integrate_ignores_unverified_merge_ref(root, monkeypatch):
    """A judge-named ref not among the fetched hits is never honored —
    fall back to create instead."""
    _write(root, "judy", "Judy", "Judy is a colleague.")
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [{"uuid": "real-hit", "slug": "judy"}])
    m = Memory(root=root, llm=_ScriptedLLM([{"action": "merge", "merge_target_ref": "hallucinated"}]))
    out = m.integrate("Judy arrives soon.", topic="Judy", title="Judy Update", slug="judy-update")
    assert out["action"] == "created"  # never merged into the hallucinated ref


def test_integrate_decision_never_merges(root, monkeypatch):
    """decisions skip the merge path entirely and become a standalone note."""
    _write(root, "judy", "Judy", "Judy is a colleague.")
    called = []

    def _search(q, root, **kw):
        called.append(q)
        return []  # no hits -> decision create

    monkeypatch.setattr(corpus, "search", _search)
    m = Memory(root=root, llm=_ScriptedLLM([]))
    out = m.integrate(
        "we decided to adopt blue-green deploys",
        topic="deploy strategy",
        kind="decision",
        title="Adopt blue-green deploys",
        slug="adopt-blue-green",
        choice="blue-green deploys",
        rationale="rollback safety",
    )
    assert out["action"] == "created"
    body = (root / f"{next(f for f in root.glob('*.md') if 'adopt-blue-green' in f.name)}").read_text()
    assert "**Decision:** blue-green deploys" in body
    assert "**Rationale:** rollback safety" in body


def test_integrate_requires_llm(root):
    m = Memory(root=root)  # no llm
    out = m.integrate("x", topic="t", title="T", slug="t")
    assert out["action"] == "error"


def test_integrate_passes_source_and_index(root, monkeypatch):
    seen = {}
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [])
    marker = object()
    m = Memory(root=root, llm=_ScriptedLLM([]), source="roger", index=marker)
    m.integrate("body", topic="topic", title="Title", slug="slug")
    assert "roger" in (root / f"{next(f for f in root.glob('*.md'))}").read_text()


def test_decide_merge_target_returns_none_for_decision(root, monkeypatch):
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [{"uuid": "u", "slug": "s"}])
    llm = _ScriptedLLM([{"action": "merge", "merge_target_ref": "u"}])
    assert decide_merge_target(root, content="x", topic="t", kind="decision", llm=llm) is None


def test_decide_merge_target_verifies_ref(root, monkeypatch):
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [{"uuid": "u", "slug": "s"}])
    monkeypatch.setattr(
        corpus, "read",
        lambda ref, root, **kw: {"found": True, "note": {"uuid": "u", "slug": "s", "body": "b"}},
    )
    # judge names a uuid that IS among the hits -> honored
    assert decide_merge_target(root, content="x", topic="t", kind="concept",
                               llm=_ScriptedLLM([{"action": "merge", "merge_target_ref": "u"}])) == "u"
    # judge names a uuid NOT among the hits -> None
    assert decide_merge_target(root, content="x", topic="t", kind="concept",
                               llm=_ScriptedLLM([{"action": "merge", "merge_target_ref": "nope"}])) is None