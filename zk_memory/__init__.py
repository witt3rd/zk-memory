"""zk_memory — a host-agnostic zettelkasten memory library.

A flat corpus of atomic Markdown notes (one thought per note, own words,
plain-markdown links, YAML frontmatter with a uuid). Judgment happens at
write time: an injected LLM decides whether a turn is worth a note and
drafts it; recall is full-text search. No Hermes / agent.* dependency —
this library is embeddable anywhere.

Two entry points:

  - Module-level corpus functions take an explicit ``root``::

        from zk_memory import search, read, write, merge, tend, list_notes
        search("judy", root)

  - ``Memory`` is the embeddable object, binding a root (and optionally
    an LLM and a tracer)::

        from zk_memory import Memory

        m = Memory(root=Path("./zk"), llm=my_llm)   # llm optional
        m.search("judy", limit=8)
        m.retain_turn(user, assistant)              # distill -> merge|create
"""

from __future__ import annotations

from zk_memory.memory import Memory
from zk_memory.corpus import (
    find_note,
    list_notes,
    merge,
    read,
    read_note_meta,
    search,
    tend,
    write,
)
from zk_memory.integrate import decide_merge_target, integrate
from zk_memory.indexing import (
    AutoProvider,
    EmbeddingProvider,
    IndexProvider,
    LanceDBProvider,
    RgProvider,
    VectorProvider,
    get_provider,
    register_backend,
)

__all__ = [
    "Memory",
    "find_note",
    "list_notes",
    "merge",
    "read",
    "read_note_meta",
    "search",
    "tend",
    "write",
    # careful-write pipeline
    "decide_merge_target",
    "integrate",
    # index-provider abstraction
    "AutoProvider",
    "EmbeddingProvider",
    "IndexProvider",
    "LanceDBProvider",
    "RgProvider",
    "VectorProvider",
    "get_provider",
    "register_backend",
]

__version__ = "0.4.0"