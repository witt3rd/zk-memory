"""Memory.retain_turn / retain_messages / process_candidate -- the retain
pipeline that used to live in the Hermes provider. Driven by injected
StructuredLLM stubs; corpus.search is monkeypatched for deterministic hit
sets. Corpus writes go through the real zk_memory.corpus functions."""

from __future__ import annotations

from pathlib import Path

import pytest

from zk_memory import Memory, corpus
from zk_memory.memory import Memory as MemoryCls


class _ScriptedLLM:
    """A StructuredLLM stub returning results in order, keyed by call name."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def __call__(self, messages, *, schema, name):
        self.calls.append({"name": name, "messages": messages, "schema": schema})
        return self._results.pop(0) if self._results else None


CONCEPT = {
    "kind": "concept",
    "topic": "atomic notes",
    "title": "Atomic Notes",
    "slug": "atomic-notes",
    "content": "One idea per note, own words.",
}


@pytest.fixture
def root(tmp_path):
    return tmp_path / "zk"


def _memory(root, stub):
    return MemoryCls(root=root, llm=stub)


def _md_files(root):
    if not root.exists():
        return []
    return list(root.glob("*.md"))


# ---------------------------------------------------------------------------
# retain_turn
# ---------------------------------------------------------------------------


def test_retain_turn_without_llm_returns_empty(root):
    m = Memory(root=root)  # no llm
    assert m.retain_turn("u", "a") == []
    assert _md_files(root) == []


def test_retain_turn_creates_new_note_when_no_hits(root, monkeypatch):
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [])
    stub = _ScriptedLLM([{"worth_retaining": True, "candidates": [CONCEPT]}])
    m = _memory(root, stub)

    labels = m.retain_turn("what's a zettelkasten?", "one idea per note")
    assert labels == ["Atomic Notes"]
    files = [f for f in _md_files(root) if "atomic-notes" in f.name]
    assert len(files) == 1
    text = files[0].read_text()
    assert "# Atomic Notes" in text
    assert "One idea per note, own words." in text
    # no judge call -- no hits to compare against
    assert [c["name"] for c in stub.calls] == ["record_candidates"]


def test_retain_turn_merges_when_judge_says_merge_valid_ref(root, monkeypatch):
    seed = Memory(root=root).write("judy", "Judy", "Judy is a colleague.")
    target_uuid = seed["uuid"]

    entity = {"kind": "entity_update", "topic": "Judy", "title": "", "slug": "",
              "content": "Judy is arriving in two weeks."}
    stub = _ScriptedLLM([
        {"worth_retaining": True, "candidates": [entity]},
        {"action": "merge", "merge_target_ref": target_uuid},
    ])
    m = _memory(root, stub)
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [{"uuid": target_uuid, "slug": "judy"}])

    labels = m.retain_turn("when's Judy arriving?", "in two weeks")
    assert labels == ["Judy"]

    # No new note created for the entity_update...
    new_notes = [f for f in _md_files(root) if "judy" not in f.name]
    assert new_notes == []
    # ...and the existing Judy note was appended to, not replaced.
    text = Path(seed["path"]).read_text()
    assert "Judy is a colleague." in text
    assert "Judy is arriving in two weeks." in text


def test_retain_turn_falls_back_to_create_when_merge_ref_invalid(root, monkeypatch):
    """judge_merge names a ref that wasn't among the fetched hits --
    never trust an unverified ref; fall back to create instead."""
    stub = _ScriptedLLM([
        {"worth_retaining": True, "candidates": [CONCEPT]},
        {"action": "merge", "merge_target_ref": "hallucinated-ref"},
    ])
    m = _memory(root, stub)
    monkeypatch.setattr(
        corpus, "search",
        lambda q, root, **kw: [{"uuid": "hit-1", "slug": "s"}],
    )
    monkeypatch.setattr(
        corpus, "read",
        lambda ref, root, **kw: {"found": True, "note": {"uuid": "hit-1", "title": "T", "body": "B"}},
    )

    labels = m.retain_turn("u", "a")
    assert labels == ["Atomic Notes"]  # fell back to create, not silently dropped
    files = [f for f in _md_files(root) if "atomic-notes" in f.name]
    assert len(files) == 1


def test_retain_turn_incomplete_candidate_skipped(root, monkeypatch):
    incomplete = {"kind": "concept", "topic": "x", "title": "", "slug": "", "content": "y"}
    stub = _ScriptedLLM([{"worth_retaining": True, "candidates": [incomplete]}])
    m = _memory(root, stub)
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [])

    assert m.retain_turn("u", "a") == []
    assert _md_files(root) == []


def test_retain_turn_multiple_candidates_processed_independently(root, monkeypatch):
    second = {"kind": "concept", "topic": "second idea", "title": "Second Idea",
              "slug": "second-idea", "content": "A different atomic thought."}
    stub = _ScriptedLLM([{"worth_retaining": True, "candidates": [CONCEPT, second]}])
    m = _memory(root, stub)
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [])

    labels = m.retain_turn("u", "a")
    assert labels == ["Atomic Notes", "Second Idea"]
    names = {f.name for f in _md_files(root)}
    assert any("atomic-notes" in n for n in names)
    assert any("second-idea" in n for n in names)


def test_retain_turn_never_raises_when_llm_raises(root, monkeypatch):
    class _BoomLLM:
        def __call__(self, messages, *, schema, name):
            raise RuntimeError("boom")

    m = MemoryCls(root=root, llm=_BoomLLM())
    assert m.retain_turn("u", "a") == []


# ---------------------------------------------------------------------------
# retain_messages
# ---------------------------------------------------------------------------


def test_retain_messages_without_llm_returns_empty(root):
    assert Memory(root=root).retain_messages([{"role": "user", "content": "hi"}]) == []


def test_retain_messages_skips_system_and_non_string(root, monkeypatch):
    captured = {}

    class _CaptureLLM:
        def __call__(self, messages, *, schema, name):
            captured["user_text"] = messages[1]["content"]
            return {"worth_retaining": False, "candidates": []}

    m = MemoryCls(root=root, llm=_CaptureLLM())
    messages = [
        {"role": "system", "content": "SECRET_SYSTEM_MARKER"},
        {"role": "user", "content": "hello there"},
        {"role": "assistant", "content": "hi back"},
        {"role": "assistant", "content": [{"type": "tool_use"}]},
        {"role": "tool", "content": None},
    ]
    m.retain_messages(messages)

    assert "SECRET_SYSTEM_MARKER" not in captured["user_text"]
    assert "USER: hello there" in captured["user_text"]
    assert "ASSISTANT: hi back" in captured["user_text"]


def test_retain_messages_returns_labels(root, monkeypatch):
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [])
    stub = _ScriptedLLM([{"worth_retaining": True, "candidates": [CONCEPT]}])
    m = _memory(root, stub)

    messages = [{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}]
    labels = m.retain_messages(messages)
    assert labels == ["Atomic Notes"]
    files = [f for f in _md_files(root) if "atomic-notes" in f.name]
    assert len(files) == 1


def test_retain_messages_never_raises_when_llm_raises(root):
    class _BoomLLM:
        def __call__(self, messages, *, schema, name):
            raise RuntimeError("boom")

    m = MemoryCls(root=root, llm=_BoomLLM())
    assert m.retain_messages([{"role": "user", "content": "hi"}]) == []


# ---------------------------------------------------------------------------
# process_candidate (direct)
# ---------------------------------------------------------------------------


def test_process_candidate_direct(root, monkeypatch):
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [])
    stub = _ScriptedLLM([])
    m = _memory(root, stub)
    from zk_memory.retain import process_candidate

    out = process_candidate(root, CONCEPT, stub, m._tracer)
    assert out == "Atomic Notes"
    files = [f for f in _md_files(root) if "atomic-notes" in f.name]
    assert len(files) == 1


def test_memory_exposes_corpus_ops(root):
    m = Memory(root=root)
    assert m.list_notes() == []
    w = m.write("hello", "Hello", "Hello body.")
    assert w["ok"]
    assert m.read(w["uuid"])["found"] is True
    # search works without an llm (never raises)
    assert isinstance(m.search("hello", limit=8), list)


# ---------------------------------------------------------------------------
# backend + source threading (shared/multi-host corpora)
# ---------------------------------------------------------------------------


def test_memory_backend_default_and_rg(root, monkeypatch):
    root.mkdir(parents=True)
    m = MemoryCls(root=root)
    assert m.backend == "auto"
    m2 = MemoryCls(root=root, backend="rg")
    assert m2.backend == "rg"
    calls = []
    monkeypatch.setattr(corpus, "_search_rg", lambda root, query, limit: calls.append(1) or [])
    m2.search("x")
    assert calls == [1]  # backend="rg" never touched lancedb


def test_memory_search_backend_override(root, monkeypatch):
    root.mkdir(parents=True)
    import zk_memory.corpus as corpus_mod
    calls = []
    monkeypatch.setattr(corpus_mod, "_search_rg", lambda root, query, limit: calls.append(1) or [])
    m = MemoryCls(root=root, backend="auto")
    m.search("x", backend="rg")  # per-call overrides the instance default
    assert calls == [1]


def test_memory_source_default_stamps_notes(root):
    m = MemoryCls(root=root, source="roger")
    w = m.write("authored", "Authored", "Body.")
    assert "author: roger" in Path(w["path"]).read_text()


def test_memory_retain_turn_stamps_source(root, monkeypatch):
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [])
    stub = _ScriptedLLM([{"worth_retaining": True, "candidates": [CONCEPT]}])
    m = MemoryCls(root=root, llm=stub, source="roger")
    labels = m.retain_turn("u", "a")
    assert labels == ["Atomic Notes"]
    files = [f for f in _md_files(root) if "atomic-notes" in f.name]
    assert files and "author: roger" in files[0].read_text()


def test_memory_retain_turn_source_env_default(root, monkeypatch):
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [])
    monkeypatch.setenv("ZK_MEMORY_SOURCE", "chef")
    stub = _ScriptedLLM([{"worth_retaining": True, "candidates": [CONCEPT]}])
    m = MemoryCls(root=root, llm=stub)
    m.retain_turn("u", "a")
    files = [f for f in _md_files(root) if "atomic-notes" in f.name]
    assert files and "author: chef" in files[0].read_text()