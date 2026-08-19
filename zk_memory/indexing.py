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
