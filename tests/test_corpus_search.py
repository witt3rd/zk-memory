"""corpus.search() -- full-text recall, forcing the ripgrep fallback path.

We never rely on lancedb actually being installed/uninstalled on the test
machine: to force search() through the rg-fallback branch
deterministically, we install a fake ``zk_memory.fts`` module in
sys.modules whose run_fts() always raises ImportError (mirroring what
happens for real when lancedb isn't installed -- ModuleNotFoundError is
an ImportError subclass, which search()'s except clause catches).
"""

from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture(autouse=True)
def _force_lancedb_unavailable(monkeypatch):
    """Make ``from zk_memory.fts import run_fts`` importable-but-unusable,
    always driving search() into the rg fallback -- regardless of whether
    the real lancedb package happens to be installed here."""

    def _always_raises(*args, **kwargs):
        raise ImportError("lancedb not installed (test double)")

    fake_module = types.ModuleType("zk_memory.fts")
    fake_module.run_fts = _always_raises
    monkeypatch.setitem(sys.modules, "zk_memory.fts", fake_module)
    yield


def _write(zk_module, zk_root, monkeypatch, slug, title, body):
    monkeypatch.setattr(zk_module.shutil, "which", lambda name: "/usr/bin/rg" if name == "rg" else None)
    return zk_module.write(slug, title, body, zk_root)


def test_search_missing_corpus_dir_returns_empty(zk_module, zk_root):
    assert not zk_root.exists()
    assert zk_module.search("anything", zk_root) == []


def test_search_finds_note_via_rg_fallback(zk_module, zk_root, monkeypatch):
    if not zk_module.shutil.which("rg"):
        pytest.skip("rg not on PATH on this machine")
    _write(zk_module, zk_root, monkeypatch, "zebra-note", "Zebra Note", "A note about zebras and stripes.")
    _write(zk_module, zk_root, monkeypatch, "other-note", "Other Note", "Unrelated content about oceans.")

    hits = zk_module.search("zebra", zk_root)
    assert len(hits) == 1
    assert hits[0]["slug"].endswith("zebra-note")
    assert hits[0]["title"] == "Zebra Note"


def test_search_rg_absent_from_path_returns_empty(zk_module, zk_root, monkeypatch):
    monkeypatch.setattr(zk_module.shutil, "which", lambda name: None)
    zk_root.mkdir(parents=True)
    (zk_root / "note.md").write_text("# Note\n\nsome content\n")
    assert zk_module.search("content", zk_root) == []


def test_search_rg_no_matches_returns_empty(zk_module, zk_root, monkeypatch):
    if not zk_module.shutil.which("rg"):
        pytest.skip("rg not on PATH on this machine")
    _write(zk_module, zk_root, monkeypatch, "some-note", "Some Note", "Body text.")
    hits = zk_module.search("nonexistent-term-xyz", zk_root)
    assert hits == []


def test_search_rg_directly_ranks_by_hit_count(zk_module, zk_root, monkeypatch):
    """Exercise _search_rg directly (bypassing search()'s lancedb try)."""
    if not zk_module.shutil.which("rg"):
        pytest.skip("rg not on PATH on this machine")
    _write(zk_module, zk_root, monkeypatch, "dense", "Dense", "cat cat cat cat")
    _write(zk_module, zk_root, monkeypatch, "sparse", "Sparse", "cat once")

    hits = zk_module._search_rg(zk_root, "cat", 8)
    assert [h["slug"].split("-", 1)[1] for h in hits] == ["dense", "sparse"]


def test_search_rg_absent_returns_empty_directly(zk_module, zk_root, monkeypatch):
    monkeypatch.setattr(zk_module.shutil, "which", lambda name: None)
    assert zk_module._search_rg(zk_root, "anything", 8) == []


# ---------------------------------------------------------------------------
# backend knob (shared/multi-host corpora)
# ---------------------------------------------------------------------------


def test_search_backend_rg_skips_lancedb(zk_module, zk_root, monkeypatch):
    """backend="rg" must never attempt the lancedb import path."""
    import zk_memory.indexing as indexing
    zk_root.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(indexing.RgProvider, "search",
                        lambda self, root, query, **kw: calls.append("rg") or [])
    assert zk_module.search("x", zk_root, backend="rg") == []
    assert calls == ["rg"]


def test_search_backend_fts_returns_empty_when_unavailable(zk_module, zk_root, monkeypatch):
    """backend="fts" with lancedb unavailable (autouse fixture) -> [] — no
    silent rg fallback when the caller explicitly asked for fts only."""
    zk_root.mkdir(parents=True)
    (zk_root / "note.md").write_text("# N\n\ncontent here\n")
    assert zk_module.search("content", zk_root, backend="fts") == []


def test_search_backend_env_rg_default(zk_module, zk_root, monkeypatch):
    import zk_memory.indexing as indexing
    zk_root.mkdir(parents=True)
    calls = []
    monkeypatch.setenv("ZK_MEMORY_BACKEND", "rg")
    monkeypatch.setattr(indexing.RgProvider, "search",
                        lambda self, root, query, **kw: calls.append("rg") or [])
    assert zk_module.search("x", zk_root) == []
    assert calls == ["rg"]


def test_search_backend_unrecognized_falls_back_to_auto(zk_module, zk_root, monkeypatch):
    """An unrecognized backend degrades to "auto" (try fts, fall back rg)."""
    import zk_memory.indexing as indexing
    zk_root.mkdir(parents=True)
    calls = []
    # fts import is unavailable via the autouse fixture, so auto falls to rg.
    monkeypatch.setattr(indexing.RgProvider, "search",
                        lambda self, root, query, **kw: calls.append("rg") or [])
    assert zk_module.search("x", zk_root, backend="bogus") == []
    assert calls == ["rg"]