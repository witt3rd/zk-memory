"""tend_writes — the content gardener pass.

The write path is deliberately cheap (arbitrary writes); this pass is the
delayed integration. Recent writes are the highest-priority candidates:
each is merged (append-only fold + retired to .archive/), linked (out-links
to related notes), or kept. Decisions never merge. Drives the real
``zk_memory.corpus`` functions with stubbed search + StructuredLLM."""

from __future__ import annotations

import os

import pytest

from zk_memory import Memory, corpus


@pytest.fixture
def root(tmp_path):
    return tmp_path / "zk"


class _Judge:
    """StructuredLLM stub returning a fixed judge decision."""

    def __init__(self, decision):
        self._decision = decision

    def __call__(self, messages, *, schema, name):
        return self._decision


DECISION_CANDIDATE = {
    "kind": "decision",
    "topic": "rollback strategy",
    "title": "Rollback Decision",
    "slug": "rollback-decision",
    "choice": "adopt blue-green",
    "content": "We decided to adopt blue-green deploys.",
    "rationale": "minimize downtime",
}


def _write(root, slug, title, body):
    return Memory(root=root).write(slug, title, body)


# ---------------------------------------------------------------------------
# no-op / ordering
# ---------------------------------------------------------------------------


def test_tend_writes_without_llm_noop(root):
    assert Memory(root=root).tend_writes() == []


def test_tend_writes_candidates_are_recent_first(root, monkeypatch):
    _write(root, "a", "Note A", "Body A")
    _write(root, "b", "Note B", "Body B")
    _write(root, "c", "Note C", "Body C")
    base = 1_600_000_000
    for i, key in enumerate(["a", "b", "c"]):
        f = [f for f in root.glob("*.md") if f.name.endswith(f"-{key}.md")][0]
        os.utime(f, (base + i, base + i))
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [])

    m = Memory(root=root, llm=object())  # llm present, but no search hits -> no judge calls
    results = m.tend_writes(limit=10)
    assert all(r["action"] == "kept" for r in results)
    assert [r["slug"].rsplit("-", 1)[1] for r in results] == ["c", "b", "a"]


# ---------------------------------------------------------------------------
# merge + archive
# ---------------------------------------------------------------------------


def test_tend_writes_merges_duplicate_and_archives(root, monkeypatch):
    target = _write(root, "topic", "Topic", "The canonical topic body.")
    dup = _write(root, "topic-dup", "Topic Dup", "Duplicate body that should fold.")
    notes = {n["uuid"]: n for n in corpus.list_notes(root)}
    target_uuid, dup_uuid = target["uuid"], dup["uuid"]
    target_slug = notes[target_uuid]["slug"]

    def fake_search(query, root, **kw):
        if query == "Topic Dup":
            return [{"uuid": target_uuid, "slug": target_slug, "path": notes[target_uuid]["path"]}]
        return []

    monkeypatch.setattr(corpus, "search", fake_search)
    m = Memory(root=root, llm=_Judge({"action": "merge", "merge_target_ref": target_uuid}))

    results = m.tend_writes(limit=10)
    merged = [r for r in results if r["action"] == "merged"]
    assert len(merged) == 1
    assert merged[0]["slug"] == notes[dup_uuid]["slug"]
    assert merged[0]["target"] == target_uuid

    # target gained the duplicate's body (append-only fold)
    assert "Duplicate body that should fold." in (root / notes[target_uuid]["path"]).read_text()
    # duplicate retired to .archive/ (reversible, not deleted)
    assert not (root / notes[dup_uuid]["path"]).exists()
    assert (root / ".archive" / notes[dup_uuid]["path"]).exists()


# ---------------------------------------------------------------------------
# link kept notes (graph growth)
# ---------------------------------------------------------------------------


def test_tend_writes_links_kept_notes(root, monkeypatch):
    a = _write(root, "alpha", "Alpha", "About alpha things.")
    b = _write(root, "beta", "Beta", "About beta things.")
    notes = {n["uuid"]: n for n in corpus.list_notes(root)}
    a_uuid, b_uuid = a["uuid"], b["uuid"]
    b_slug = notes[b_uuid]["slug"]
    a_slug = notes[a_uuid]["slug"]

    def fake_search(query, root, **kw):
        if query == "Alpha":
            return [{"uuid": b_uuid, "slug": b_slug, "path": notes[b_uuid]["path"]}]
        return []

    monkeypatch.setattr(corpus, "search", fake_search)
    m = Memory(root=root, llm=_Judge({"action": "create"}))

    results = m.tend_writes(limit=10)
    alpha_res = [r for r in results if r["slug"] == a_slug][0]
    assert alpha_res["action"] == "linked"
    assert b_slug in alpha_res.get("links", [])

    text = (root / notes[a_uuid]["path"]).read_text()
    assert "## Related" in text
    assert f"]({b_slug}.md)" in text


# ---------------------------------------------------------------------------
# decisions never merge
# ---------------------------------------------------------------------------


def test_tend_writes_never_merges_decisions(root, monkeypatch):
    # Create a decision note (stamped kind=decision via retain).
    class _DistillDecision:
        def __call__(self, messages, *, schema, name):
            return {"worth_retaining": True, "candidates": [DECISION_CANDIDATE]}

    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [])
    Memory(root=root, llm=_DistillDecision()).retain_turn("we decided X", "yes")
    decision = corpus.list_notes(root)[0]
    assert decision["kind"] == "decision"

    # A concept note that the judge would "merge" into.
    concept = _write(root, "concept", "Concept", "A concept note.")
    cnotes = {n["uuid"]: n for n in corpus.list_notes(root)}
    c_uuid, c_slug = concept["uuid"], cnotes[concept["uuid"]]["slug"]

    def fake_search(query, root, **kw):
        if query == decision["title"]:
            return [{"uuid": c_uuid, "slug": c_slug, "path": cnotes[c_uuid]["path"]}]
        return []

    monkeypatch.setattr(corpus, "search", fake_search)
    m = Memory(root=root, llm=_Judge({"action": "merge", "merge_target_ref": c_uuid}))

    results = m.tend_writes(limit=10)
    dres = [r for r in results if r["slug"] == decision["slug"]][0]
    assert dres["action"] == "linked"  # never merged
    assert (root / decision["path"]).exists()  # still in the corpus
    assert c_slug in (root / decision["path"]).read_text()  # but linked out