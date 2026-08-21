"""zk_memory.split — the de-merge / re-atomicize spine (Z12).

The inverse of ``integrate.py``. Merge (Z2/Z3/Z10) grows an entity note
until it's a biography — no longer one atomic thought. This module restores
atomicity: a summary parent plus atomic child notes, the detail preserved
in the children.

Two discrete functions mirror the merge pair (``integrate.py``):

    decide_split_fragments(root, *, ref, llm, max_fragments)
        -> {split, parent_summary, fragments} | {split: False}   (pure decision)
    split_note(root, *, ref, decision, source, ...)
        -> {action, parent_path, children:[...]}                  (decision -> write)

``decide_split_fragments`` is the pure decision (judge, capped, prefer-not-
to-split). ``split_note`` performs the write: a new summary parent, new
atomic children, links between them, and the original biography retired to
``.archive/`` (reversible, never deleted).

Gardener contract: a split may happen ONLY for a note surfaced by the
mechanical sweep (descending file size) — never because the gardener decided
on its own, mid-pass, that a note should be split.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from zk_memory import corpus
from zk_memory.judge import StructuredLLM, judge_split

logger = logging.getLogger(__name__)

# A tracer is ``callable(event, root, **fields)`` — see zk_memory.probe.
Tracer = Callable[..., None]

DEFAULT_MAX_FRAGMENTS = 4
DEFAULT_ARCHIVE_DIR = ".archive"


def _note_for(root: Path, ref: str) -> Optional[dict[str, Any]]:
    """Fetch a full note (body included) by ref; None when missing."""
    result = corpus.read(ref, root, resolve_links=False)
    if not result["found"]:
        return None
    return result["note"]


def decide_split_fragments(
    root: Any,
    *,
    ref: str,
    llm: StructuredLLM,
    max_fragments: int = DEFAULT_MAX_FRAGMENTS,
    index: Any = None,
) -> dict[str, Any]:
    """The pure split decision: judge whether ``ref`` is a biography worth
    splitting, and if so return the parent summary + capped child fragments.

    Returns ``{"split": False}`` when the note should be left alone, or
    ``{"split": True, "parent_summary", "fragments"}``. ``fragments`` is
    defensively truncated to ``max_fragments`` even if the judge exceeds it
    (belt-and-suspenders; the schema also enforces the cap). Never raises;
    any failure returns ``{"split": False}``.
    """
    note = _note_for(root, ref)
    if note is None:
        return {"split": False}
    decision = judge_split(note, llm)
    if not decision or not decision.get("split"):
        return {"split": False}
    fragments = [
        f for f in (decision.get("fragments") or []) if isinstance(f, dict)
    ][:max_fragments]
    parent_summary = str(decision.get("parent_summary") or "").strip()
    if not fragments or not parent_summary:
        return {"split": False}
    return {
        "split": True,
        "parent_summary": parent_summary,
        "fragments": fragments,
        "_parent_title": note.get("title") or note.get("slug"),
        "_parent_kind": note.get("kind") or "concept",
        "_parent_slug": note.get("slug"),
        "_orig_path": note.get("path"),
        "_orig_ref": ref,
    }


def _child_body(fragment: dict[str, Any]) -> str:
    """Build a child body; decision fragments carry choice/rationale."""
    from zk_memory.integrate import _build_body
    return _build_body(
        str(fragment.get("content") or "").strip(),
        str(fragment.get("kind") or "concept"),
        choice=str(fragment.get("choice") or "").strip() or None,
        rationale=str(fragment.get("rationale") or "").strip() or None,
    )


def split_note(
    root: Any,
    *,
    ref: str,
    llm: StructuredLLM,
    source: Optional[str] = None,
    tracer: Optional[Tracer] = None,
    max_fragments: int = DEFAULT_MAX_FRAGMENTS,
    archive_dir: str = DEFAULT_ARCHIVE_DIR,
) -> dict[str, Any]:
    """The de-merge write: split one note into a summary parent + atomic
    children (using ``decide_split_fragments``), link them, and retire the
    original biography to ``.archive/``.

    Returns:
      - ``{"action": "not_split", "ref"}`` — the judge declined (prefer not
        to split), or the note is missing.
      - ``{"action": "split", "parent": <slug>, "children": [<slug>, ...]}``
        on success.
      - ``{"action": "error", "err"}`` on failure.

    The original is never deleted — moved to ``.archive/`` (reversible). A
    wrong split is recoverable. Callers: ``Memory.split_note`` and the
    gardener's split of a sweep-surfaced candidate (the gardener must NOT
    split a note that didn't come from the mechanical sweep).
    """
    root = Path(root)
    decision = decide_split_fragments(
        root, ref=ref, llm=llm, max_fragments=max_fragments
    )
    if not decision.get("split"):
        if tracer:
            tracer("split_note", root, ref=ref, action="not_split")
        return {"action": "not_split", "ref": ref}

    orig_path = root / str(decision.get("_orig_path", ""))
    if not orig_path.exists():
        return {"action": "error", "err": f"note not found: {ref}"}

    parent_kind = decision.get("_parent_kind", "concept")
    parent_title = decision.get("_parent_title") or ref
    parent_slug = str(decision.get("_parent_slug") or ref).strip()

    # 1. Write the summary parent (a new atomic note).
    parent_res = corpus.write(
        parent_slug, parent_title, str(decision.get("parent_summary") or "").strip(),
        root, source=source,
    )
    if not parent_res.get("ok"):
        return {"action": "error", "err": parent_res.get("err", "parent write failed")}
    parent_path = Path(parent_res["path"])
    corpus._ensure_field(parent_path, "kind", parent_kind)

    # 2. Write the atomic children.
    children: list[str] = []
    for frag in decision.get("fragments", []):
        slug = str(frag.get("slug") or "").strip()
        title = str(frag.get("title") or "").strip()
        body = _child_body(frag)
        if not slug or not title or not body:
            continue
        child_res = corpus.write(slug, title, body, root, source=source)
        if not child_res.get("ok"):
            logger.warning("zk-memory: split child write failed: %s", child_res.get("err"))
            continue
        cpath = Path(child_res["path"])
        kind = str(frag.get("kind") or "concept")
        if kind:
            corpus._ensure_field(cpath, "kind", kind)
        # child links back to the parent
        corpus.add_related_links(cpath, [(parent_title, parent_path.stem)])
        children.append(child_res.get("uuid") or child_res["path"])

    # 3. Parent links out to each child.
    corpus.add_related_links(
        parent_path,
        [(f.get("title") or f.get("slug"), str(f.get("slug") or "").strip())
         for f in decision.get("fragments", [])
         if str(f.get("slug") or "").strip()],
    )

    # 4. Retire the original biography (reversible, never deleted).
    arch = root / archive_dir
    arch.mkdir(parents=True, exist_ok=True)
    dest = arch / orig_path.name
    if dest.exists():
        dest = arch / f"{orig_path.stem}-{str(decision.get('_orig_ref', 'split'))[:8]}{orig_path.suffix}"
    shutil.move(str(orig_path), str(dest))

    if tracer:
        tracer("split_note", root, ref=ref, action="split",
               parent=parent_path.stem, children=len(children))
    return {
        "action": "split",
        "parent": parent_path.stem,
        "children": children,
    }
