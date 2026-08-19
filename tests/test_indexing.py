"""index-provider abstraction — pluggable recall engines.

Covers the DI seam (injected ``IndexProvider`` objects), the named-backend
registry (``register_backend``), the env/string resolver, and threading of
a configured provider through the retain / tend_writes search paths (the
shared-corpus bug: retain/tend used to ignore a configured backend).
"""

from __future__ import annotations

import pytest

from zk_memory import Memory, corpus
from zk_memory import indexing
from zk_memory.indexing import AutoProvider, LanceDBProvider, RgProvider, get_provider, register_backend


class _StubProvider:
    """A minimal IndexProvider recording calls."""

    name = "stub"

    def __init__(self, hits=None):
        self.hits = hits if hits is not None else []
        self.calls = []

    def search(self, root, query, *, limit=8, rebuild_index=False):
        self.calls.append((query, limit, rebuild_index))
        return self.hits


@pytest.fixture
def root(tmp_path):
    return tmp_path / "zk"


def test_corpus_search_injected_provider(zk_root, monkeypatch):
    stub = _StubProvider(hits=[{"slug": "a"}])
    zk_root.mkdir(parents=True)
    assert corpus.search("q", zk_root, index=stub) == [{"slug": "a"}]
    assert stub.calls == [("q", 8, False)]


def test_injected_provider_takes_precedence_over_backend(zk_root, monkeypatch):
    stub = _StubProvider()
    zk_root.mkdir(parents=True)
    seen = []
    monkeypatch.setattr(indexing.RgProvider, "search",
                        lambda self, root, query, **kw: seen.append(1) or [])
    # index wins over backend="rg" — RgProvider must never run
    assert corpus.search("q", zk_root, backend="rg", index=stub) == []
    assert stub.calls and not seen


def test_get_provider_returns_injected_object():
    stub = _StubProvider()
    assert get_provider(stub) is stub


def test_get_provider_unrecognized_falls_back_to_auto():
    assert isinstance(get_provider("bogus"), AutoProvider)


def test_get_provider_none_uses_env(monkeypatch):
    monkeypatch.setenv("ZK_MEMORY_BACKEND", "rg")
    assert isinstance(get_provider(None), RgProvider)


def test_register_backend_selectable_by_name(zk_root):
    stub = _StubProvider(hits=[{"slug": "custom"}])
    register_backend("custom", stub)
    try:
        zk_root.mkdir(parents=True)
        assert corpus.search("q", zk_root, backend="custom") == [{"slug": "custom"}]
        assert stub.calls == [("q", 8, False)]
    finally:
        indexing._REGISTRY.pop("custom", None)


def test_register_backend_factory_instantiated():
    register_backend("factory-stub", _StubProvider)
    try:
        assert isinstance(get_provider("factory-stub"), _StubProvider)
    finally:
        indexing._REGISTRY.pop("factory-stub", None)


def test_memory_index_provider(root):
    stub = _StubProvider(hits=[{"slug": "s"}])
    m = Memory(root=root, index=stub)
    assert m.index is stub
    root.mkdir(parents=True)
    assert m.search("q") == [{"slug": "s"}]
    assert stub.calls == [("q", 8, False)]


def test_memory_index_resolved_from_backend_string(root):
    m = Memory(root=root, backend="rg")
    assert isinstance(m.index, RgProvider)


def test_memory_percall_backend_overrides_index(root, monkeypatch):
    stub = _StubProvider()
    m = Memory(root=root, index=stub)
    root.mkdir(parents=True)
    seen = []
    monkeypatch.setattr(
        indexing.RgProvider, "search",
        lambda self, r, q, **kw: seen.append(1) or [],
    )
    m.search("q", backend="rg")
    assert seen == [1] and not stub.calls


def test_retain_turn_threads_index(root, monkeypatch):
    """retain's merge search must use the provider configured on Memory."""
    stub = _StubProvider()
    m = Memory(root=root, index=stub, llm=_DistillLLM())

    captured = {}
    monkeypatch.setattr(corpus, "search",
                        lambda q, root, **kw: captured.update(kw) or [])
    m.retain_turn("u", "a")
    assert captured.get("index") is stub


def test_retain_messages_threads_index(root, monkeypatch):
    stub = _StubProvider()
    m = Memory(root=root, index=stub, llm=_DistillLLM())

    captured = {}
    monkeypatch.setattr(corpus, "search",
                        lambda q, root, **kw: captured.update(kw) or [])
    m.retain_messages([{"role": "user", "content": "hi"}])
    assert captured.get("index") is stub


def test_tend_writes_threads_index(root, monkeypatch):
    stub = _StubProvider()
    m = Memory(root=root, index=stub, llm=_DistillLLM())
    # a note must exist for tend_writes to reconcile one
    Memory(root=root).write("subject", "Subject", "A standalone note.")

    captured = {}
    monkeypatch.setattr(corpus, "search",
                        lambda q, root, **kw: captured.update(kw) or [])
    m.tend_writes()
    assert captured.get("index") is stub


def test_fts_only_returns_empty_when_unavailable(zk_root, monkeypatch):
    import types
    def _raises(*a, **kw):
        raise ImportError("lancedb not installed (test double)")
    fake = types.ModuleType("zk_memory.fts")
    fake.run_fts = _raises
    monkeypatch.setitem(__import__("sys").modules, "zk_memory.fts", fake)
    zk_root.mkdir(parents=True)
    (zk_root / "note.md").write_text("# N\n\ncontent\n")
    # explicit fts-only: no silent rg fallback
    assert LanceDBProvider().search(zk_root, "content") == []


class _DistillLLM:
    """A StructuredLLM that distills one concept candidate, so retain's
    merge search (process_candidate) actually runs and we can observe the
    index it was threaded."""

    _candidate = {
        "kind": "concept",
        "topic": "threading",
        "title": "Threading",
        "slug": "threading",
        "content": "A candidate body.",
    }

    def __call__(self, messages, *, schema, name):
        return {"worth_retaining": True, "candidates": [self._candidate]}
