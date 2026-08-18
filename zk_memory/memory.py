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

logger = logging.getLogger(__name__)


class Memory:
    """Zettelkasten memory: corpus operations plus LLM-judged retain.

    Args:
        root: the corpus directory (created lazily on write).
        llm: an optional ``StructuredLLM`` callable for the retain
            pipeline. None disables retain (corpus ops still work).
        tracer: an optional ``callable(event, root, **fields)`` diagnostic
            tracer. Defaults to ``zk_memory.probe.trace``.
    """

    def __init__(
        self,
        root,
        llm: Optional[StructuredLLM] = None,
        tracer: Any = None,
    ) -> None:
        self._root = Path(root)
        self._llm = llm
        self._tracer = tracer if tracer is not None else probe.trace

    @property
    def root(self) -> Path:
        return self._root

    @property
    def llm(self) -> Optional[StructuredLLM]:
        return self._llm

    # ------------------------------------------------------------------
    # Corpus operations — root-bound wrappers over zk_memory.corpus
    # ------------------------------------------------------------------

    def list_notes(self) -> list[dict[str, Any]]:
        return corpus.list_notes(self._root)

    def search(
        self, query: str, *, limit: int = 8, rebuild_index: bool = False
    ) -> list[dict[str, Any]]:
        return corpus.search(query, self._root, limit=limit, rebuild_index=rebuild_index)

    def read(self, ref: str, *, resolve_links: bool = True) -> dict[str, Any]:
        return corpus.read(ref, self._root, resolve_links=resolve_links)

    def write(self, slug: str, title: str, body: str) -> dict[str, Any]:
        return corpus.write(slug, title, body, self._root)

    def merge(self, ref: str, fragment: str) -> dict[str, Any]:
        return corpus.merge(ref, fragment, self._root)

    def tend(self, action: str, *args: str) -> dict[str, Any]:
        return corpus.tend(action, self._root, *args)

    # ------------------------------------------------------------------
    # Retain pipeline (LLM-judged; no-ops without an llm)
    # ------------------------------------------------------------------

    def retain_turn(
        self, user_content: str, assistant_content: str, *, session_id: str = ""
    ) -> list[str]:
        """Distill one turn and write/merge its candidates. Returns the
        list of retained labels (empty when nothing was retained or no
        LLM is configured)."""
        return retain_turn(
            self._root, self._llm, self._tracer,
            user_content, assistant_content, session_id=session_id,
        )

    def retain_messages(
        self, messages: list[dict[str, Any]], *, session_id: str = ""
    ) -> list[str]:
        """Distill a compaction batch and write/merge its candidates.
        Returns the list of retained labels."""
        return retain_messages(
            self._root, self._llm, self._tracer, messages, session_id=session_id,
        )