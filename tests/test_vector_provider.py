"""VectorProvider — FAISS vector recall over an injected EmbeddingProvider.

These exercise the DI seam: a VectorProvider is injected (never a backend
string) with a stub EmbeddingProvider that maps text to fixed, deterministic
vectors. faiss is an optional extra; tests skip when it (or numpy) is absent,
mirroring the rg tests' skip-on-missing-tool pattern.
"""

from __future__ import annotations

import sys

import pytest

from zk_memory.indexing import VectorProvider


def _faiss_available() -> bool:
    try:
        import faiss  # noqa: F401
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _faiss_available(), reason="faiss/numpy not installed")


class _StubEmbedder:
    """Maps each text to a deterministic vector so retrieval is testable."""

    name = "stub"

    def __init__(self, dims: int = 4) -> None:
        self.dims = dims
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[float(len(t)) + i, 1.0, float(len(t) * 0.1), 0.0] for i, t in enumerate(texts)]


@pytest.fixture
def root(tmp_path):
    corpus = tmp_path / "zk"
    corpus.mkdir()
    for slug, body in [
        ("apple", "apple banana fruit"),
        ("zebra", "zebra stripes mammal"),
        ("ocean", "ocean waves water"),
    ]:
        (corpus / f"{slug}.md").write_text(f"# {slug}\n\n{body}\n")
    return corpus


def test_vector_provider_returns_ranked_hits(root):
    vp = VectorProvider(_StubEmbedder())
    hits = vp.search(root, "apple", limit=3)
    assert hits, "expected some hits"
    assert all("score" in h and "_rank" in h for h in hits)
    # ranked by distance ascending (lower = closer)
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores)


def test_vector_provider_without_embedder_returns_empty(root):
    assert VectorProvider(None).search(root, "apple") == []


def test_vector_provider_embeds_query_and_corpus(root):
    embedder = _StubEmbedder()
    vp = VectorProvider(embedder)
    vp.search(root, "apple", limit=2)
    # call 1 = the query (1 text), call 2 = the corpus build (3 bodies)
    assert len(embedder.calls) == 2
    assert len(embedder.calls[0]) == 1
    assert len(embedder.calls[1]) == 3


def test_vector_provider_caches_index_across_queries(root):
    embedder = _StubEmbedder()
    vp = VectorProvider(embedder)
    vp.search(root, "q1", limit=2)
    calls_after_first = len(embedder.calls)
    vp.search(root, "q2", limit=2)
    # corpus re-embed skipped (signature unchanged); only the query embedded
    assert len(embedder.calls) == calls_after_first + 1


def test_vector_provider_rebuilds_when_corpus_changes(root):
    embedder = _StubEmbedder()
    vp = VectorProvider(embedder)
    vp.search(root, "q1", limit=2)
    (root / "new.md").write_text("# new\n\nbrand new note about everything\n")
    vp.search(root, "q2", limit=2)
    # corpus signature changed -> re-embed all 4 notes
    assert any(len(c) == 4 for c in embedder.calls)


def test_vector_provider_missing_corpus_returns_empty():
    from pathlib import Path
    vp = VectorProvider(_StubEmbedder())
    assert vp.search(Path("/nonexistent/zk"), "q") == []


def test_vector_provider_embed_failure_returns_empty(root, monkeypatch):
    class _Boom:
        name = "boom"

        def embed(self, texts):
            raise RuntimeError("embed failed")

    assert VectorProvider(_Boom()).search(root, "q") == []