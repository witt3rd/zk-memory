"""zk_memory.Memory — the public embeddable object.

A host-agnostic zettelkasten memory rooted at an explicit corpus
directory. ``llm`` (an injected ``StructuredLLM``) and ``tracer`` are
optional; without an LLM, the retain motions are no-ops but all corpus
operations (search / read / write / merge / tend) still work.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from zk_memory import corpus
from zk_memory import probe
from zk_memory.judge import StructuredLLM
from zk_memory.retain import retain_messages, retain_turn
from zk_memory.tend import tend_writes as _tend_writes

logger = logging.getLogger(__name__)


class Memory:
    """Zettelkasten memory: corpus operations plus LLM-judged retain.

    Args:
        root: the corpus directory (created lazily on write).
        llm: an optional ``StructuredLLM`` callable for the retain
            pipeline. None disables retain (corpus ops still work).
        tracer: an optional ``callable(event, root, **fields)`` diagnostic
            tracer. Defaults to ``zk_memory.probe.trace``.
        backend: default search backend — "auto" | "rg" | "fts". "rg"
            suits a shared/multi-host corpus (stateless, live reads).
        source: default attribution (host/agent name) stamped into notes
            this Memory writes/merges unless overridden per call.
    """

    def __init__(
        self,
        root,
        llm: Optional[StructuredLLM] = None,
        tracer: Any = None,
        *,
        backend: str = "auto",
        source: Optional[str] = None,
    ) -> None:
        self._root = Path(root)
        self._llm = llm
        self._tracer = tracer if tracer is not None else probe.trace
        self._backend = backend
        self._source = source

    @property
    def root(self) -> Path:
        return self._root

    @property
    def llm(self) -> Optional[StructuredLLM]:
        return self._llm

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def source(self) -> Optional[str]:
        return self._source

    # ------------------------------------------------------------------
    # Corpus operations — root-bound wrappers over zk_memory.corpus
    # ------------------------------------------------------------------

    def list_notes(self) -> list[dict[str, Any]]:
        return corpus.list_notes(self._root)

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        rebuild_index: bool = False,
        backend: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        backend = backend if backend is not None else self._backend
        return corpus.search(
            query, self._root, limit=limit, rebuild_index=rebuild_index, backend=backend
        )

    def read(self, ref: str, *, resolve_links: bool = True) -> dict[str, Any]:
        return corpus.read(ref, self._root, resolve_links=resolve_links)

    def write(
        self, slug: str, title: str, body: str, *, source: Optional[str] = None
    ) -> dict[str, Any]:
        if source is None:
            source = self._source
        return corpus.write(slug, title, body, self._root, source=source)

    def merge(
        self, ref: str, fragment: str, *, source: Optional[str] = None
    ) -> dict[str, Any]:
        if source is None:
            source = self._source
        return corpus.merge(ref, fragment, self._root, source=source)

    def tend(self, action: str, *args: str) -> dict[str, Any]:
        return corpus.tend(action, self._root, *args)

    # ------------------------------------------------------------------
    # Retain pipeline (LLM-judged; no-ops without an llm)
    # ------------------------------------------------------------------

    def retain_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        source: Optional[str] = None,
    ) -> list[str]:
        """Distill one turn and write/merge its candidates. Returns the
        list of retained labels (empty when nothing was retained or no
        LLM is configured)."""
        if source is None:
            source = self._source
        return retain_turn(
            self._root, self._llm, self._tracer,
            user_content, assistant_content, session_id=session_id, source=source,
        )

    def retain_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        session_id: str = "",
        source: Optional[str] = None,
    ) -> list[str]:
        """Distill a compaction batch and write/merge its candidates.
        Returns the list of retained labels."""
        if source is None:
            source = self._source
        return retain_messages(
            self._root, self._llm, self._tracer, messages, session_id=session_id,
            source=source,
        )

    def tend_writes(
        self,
        *,
        limit: int = 20,
        source: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """The content gardener pass: reconcile the most recent writes
        against the corpus (delayed integration of the cheap write path).

        Recent writes are the highest-priority candidates. Each is merged
        (append-only fold into an existing note + retired to .archive/),
        linked (out-links to related notes), or kept. ``decision`` notes
        never merge. No-op (returns []) without an ``llm``. Returns a list
        of ``{ref, slug, action, target?, links?}`` per candidate."""
        if source is None:
            source = self._source
        return _tend_writes(
            self._root, self._llm, self._tracer, limit=limit, source=source,
        )