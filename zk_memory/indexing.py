"""zk_memory.indexing — the index-provider abstraction for recall.

Recall is a pluggable engine behind a single contract (``IndexProvider``).
``zk_memory.corpus.search`` resolves a provider from a ``backend`` string
("auto" | "rg" | "fts"), a config env var (``ZK_MEMORY_BACKEND``), or an
injected provider object, then delegates the query.

Built-ins:

  - ``RgProvider``      — ripgrep over live files. Stateless, single-writer
    safe on shared/multi-host corpora (a mutable index is a concurrency
    hazard there; recall just reads files).
  - ``LanceDBProvider`` — LanceDB FTS (the ``zk-memory[lancedb]`` extra).
    Returns [] when lancedb is absent — explicit fts-only recall never
    silently degrades to rg.
  - ``AutoProvider``    — composite: try LanceDB, fall back to rg on
    ImportError. The default.

Third parties can plug in their own engine two ways: pass an
``IndexProvider`` instance straight to ``Memory(index=...)`` /
``corpus.search(..., index=...)`` (true DI — e.g. a remote/vector/BM25
service), or ``register_backend(name, provider)`` so it is selectable by a
``backend="name"`` string / env var like the built-ins.

Recall never hard-fails: a missing tool or an unavailable backend degrades
to [] or to rg, never raises.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IndexProvider(Protocol):
    """A full-text recall engine over a flat corpus directory.

    ``search`` returns ranked note dicts ({uuid, title, slug, path, date,
    kind, score, ...}); it must not raise for a missing corpus or a missing
    tool (return [] instead).
    """

    name: str

    def search(
        self,
        root: Path,
        query: str,
        *,
        limit: int = 8,
        rebuild_index: bool = False,
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Maps text to dense vectors — the injectable embedder for vector recall.

    The counterpart to ``StructuredLLM``: the library never imports a
    provider SDK. A caller supplies an object (e.g. an OpenAI-compatible
    ``/v1/embeddings`` adapter, a local model) whose ``embed`` returns one
    vector per input text. ``embed`` must not raise on a transient failure;
    return a best-effort list (callers treat a short/failed result as a
    recall miss).
    """

    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


# ---------------------------------------------------------------------------
# Built-ins
# ---------------------------------------------------------------------------


class RgProvider:
    """Ripgrep over live files — stateless, shared-corpus safe."""

    name = "rg"

    def search(
        self,
        root: Path,
        query: str,
        *,
        limit: int = 8,
        rebuild_index: bool = False,
    ) -> list[dict[str, Any]]:
        rg = shutil.which("rg")
        if not rg:
            return []
        try:
            proc = subprocess.run(
                [rg, "-l", "-i", "--", query, str(root)],
                capture_output=True, text=True, timeout=15,
            )
        except (subprocess.TimeoutExpired, OSError):
            return []
        files = [Path(line) for line in proc.stdout.splitlines() if line]
        from zk_memory.corpus import read_note_meta
        hits: list[dict[str, Any]] = []
        for f in files[:limit]:
            note = read_note_meta(f)
            if note:
                body = (f.read_text(errors="replace") or "").lower()
                note["score"] = body.count(query.lower())
                hits.append(note)
        hits.sort(key=lambda n: n.get("score", 0), reverse=True)
        return hits


def _fts_or_none(
    root: Path,
    query: str,
    *,
    limit: int,
    rebuild: bool,
) -> list[dict[str, Any]] | None:
    """Run LanceDB FTS; return None (not []) when lancedb is unavailable so
    callers can tell "no results" from "no engine"."""
    try:
        from zk_memory.fts import run_fts
        return run_fts(root, query, limit=limit, rebuild=rebuild)
    except ImportError:
        return None


class LanceDBProvider:
    """LanceDB FTS (the ``zk-memory[lancedb]`` extra).

    Explicit fts-only recall: returns [] when lancedb is absent — it never
    silently degrades to ripgrep (callers that want the fallback use
    ``AutoProvider``).
    """

    name = "fts"

    def search(
        self,
        root: Path,
        query: str,
        *,
        limit: int = 8,
        rebuild_index: bool = False,
    ) -> list[dict[str, Any]]:
        res = _fts_or_none(root, query, limit=limit, rebuild=rebuild_index)
        return res if res is not None else []


class AutoProvider:
    """Composite: try LanceDB FTS, fall back to ripgrep on ImportError."""

    name = "auto"

    def __init__(self, rg: IndexProvider | None = None) -> None:
        self.rg: IndexProvider = rg if rg is not None else RgProvider()

    def search(
        self,
        root: Path,
        query: str,
        *,
        limit: int = 8,
        rebuild_index: bool = False,
    ) -> list[dict[str, Any]]:
        res = _fts_or_none(root, query, limit=limit, rebuild=rebuild_index)
        if res is not None:
            return res
        return self.rg.search(root, query, limit=limit, rebuild_index=rebuild_index)


class VectorProvider:
    """Vector-similarity recall over a corpus, backed by an injected
    ``EmbeddingProvider`` and a FAISS index.

    Not selectable by a ``backend="vector"`` string — it needs an embedder,
    so it is injected via ``Memory(index=VectorProvider(embedder))`` /
    ``corpus.search(..., index=...)`` (the DI seam). faiss is an optional
    extra (``zk-memory[faiss]``); a missing faiss or embedder degrades to []
    (never hard-fails).

    The index is rebuilt lazily and cached against a corpus signature
    (path + mtime + size per note), so a changed corpus re-embeds only the
    changed files on the next search. ``score`` is the metric distance
    (lower is better for L2); ``_rank`` carries the search position.
    """

    name = "vector"

    def __init__(self, embedder: Any, *, metric: str = "l2") -> None:
        self.embedder: Any = embedder
        self.metric = metric
        self._sig: Any = None
        self._rows: list[dict[str, Any]] = []
        self._index: Any = None

    def search(
        self,
        root: Path,
        query: str,
        *,
        limit: int = 8,
        rebuild_index: bool = False,
    ) -> list[dict[str, Any]]:
        if self.embedder is None:
            return []
        try:
            import faiss  # noqa: F401
            import numpy as np
        except ImportError:
            return []
        if not root.is_dir():
            return []
        qv = self._embed([query])
        if not qv:
            return []
        self._ensure_index(root, np, rebuild=rebuild_index)
        if self._index is None:
            return []
        query_vec = np.asarray(qv[0], dtype="float32").reshape(1, -1)
        distances, indices = self._index.search(query_vec, limit)
        out: list[dict[str, Any]] = []
        for rank, idx in enumerate(indices[0]):
            if idx < 0 or idx >= len(self._rows):
                continue
            note = dict(self._rows[idx])
            note["score"] = float(distances[0, rank])
            note["_rank"] = rank
            out.append(note)
        return out

    # -- internals ------------------------------------------------------

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Best-effort embed; returns [] on any failure (recall miss)."""
        try:
            return self.embedder.embed(texts)
        except Exception:
            return []

    def _corpus_signature(self, root: Path) -> tuple:
        """(path, mtime, size) per note — cheaply detects corpus changes."""
        sig = []
        for f in sorted(root.glob("*.md")):
            st = f.stat()
            sig.append((f.name, st.st_mtime, st.st_size))
        return tuple(sig)

    def _ensure_index(self, root: Path, np: Any, *, rebuild: bool) -> None:
        sig = self._corpus_signature(root)
        if not rebuild and sig == self._sig and self._index is not None:
            return
        from zk_memory.corpus import list_notes
        notes = list_notes(root)
        if not notes:
            self._rows, self._index, self._sig = [], None, sig
            return
        vecs = self._embed([n["body"] for n in notes])
        if len(vecs) != len(notes):
            # partial/failed embed — can't index cleanly
            self._rows, self._index, self._sig = [], None, sig
            return
        import faiss
        emb = np.asarray(vecs, dtype="float32")
        index = faiss.IndexFlatL2(emb.shape[1])
        index.add(emb)
        self._rows, self._index, self._sig = notes, index, sig


# ---------------------------------------------------------------------------
# Registry + resolution
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Any] = {
    "auto": AutoProvider,
    "rg": RgProvider,
    "fts": LanceDBProvider,
}


def register_backend(name: str, provider: Any) -> None:
    """Register a named provider (an ``IndexProvider`` instance or a
    zero-arg factory) so it is selectable via ``backend="name"`` or the
    ``ZK_MEMORY_BACKEND`` env var, like the built-ins."""
    _REGISTRY[name] = provider


def get_provider(backend_or_index: Any = None) -> IndexProvider:
    """Resolve a backend name / config env / injected provider to an
    ``IndexProvider``.

    - an object with ``.search`` -> returned as-is (injected provider)
    - a registered name -> its factory is built
    - None -> ``ZK_MEMORY_BACKEND`` env, else "auto"
    - an unrecognized name -> "auto" (never hard-fails)
    """
    if hasattr(backend_or_index, "search"):
        return backend_or_index
    if backend_or_index is None:
        backend_or_index = os.environ.get("ZK_MEMORY_BACKEND", "auto")
    name = backend_or_index if isinstance(backend_or_index, str) else str(backend_or_index)
    factory = _REGISTRY.get(name) or _REGISTRY["auto"]
    if hasattr(factory, "search") and not isinstance(factory, type):
        return factory
    return factory()
