"""tend_writes — the content gardener pass.

The write path is deliberately cheap: arbitrary writes with mechanical
safety only (slug sanitize, no-overwrite, linlink mint). This pass is the
*delayed* integration — capture fast, integrate later. Recent writes are
the highest-priority candidates; each is reconciled against the corpus:

  - merged: the note folds into an existing note (append-only dated
    fragment) and the duplicate is **retired to .archive/** — reversible,
    never deleted.
  - linked: the note is kept and gains ``[label](slug.md)`` out-links to
    the related notes search surfaced, so the graph grows.
  - kept: genuinely standalone; left untouched.

``decision`` notes never merge (a decision is append-only history — a new
decision on the same topic is a new dated note); they only get out-links.

Nothing here imports hermes / agent.* — LLM is an injected
``StructuredLLM``; corpus ops are the plain ``zk_memory.corpus`` functions.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from zk_memory import corpus
from zk_memory.integrate import decide_merge_target
from zk_memory.judge import StructuredLLM
from zk_memory.split import split_note as _split_note

logger = logging.getLogger(__name__)

# A tracer is ``callable(event, root, **fields)`` — see zk_memory.probe.
Tracer = Callable[..., None]

DEFAULT_ARCHIVE_DIR = ".archive"


def tend_writes(
    root: Any,
    llm: Optional[StructuredLLM],
    tracer: Tracer,
    *,
    limit: int = 20,
    split_sweep: int = 0,
    source: Optional[str] = None,
    archive_dir: str = DEFAULT_ARCHIVE_DIR,
    index: Any = None,
) -> list[dict[str, Any]]:
    """Reconcile the most recent writes against the corpus, and split any
    notes the mechanical sweep surfaced.

    Candidates are the newest notes by mtime (writes and merges both touch
    it). Returns a list of ``{ref, slug, action, target?, links?}`` per
    candidate. No ``llm`` -> returns [] (no-op, same as retain without an
    LLM). Never raises.

    ``split_sweep`` (>0) authorizes the gardener to de-merge the top N notes
    from the mechanical ``split_candidates`` sweep (descending file size).
    This is the ONLY way a split happens during gardening: the gardener
    splits exactly the notes the sweep surfaced — never a note it decided on
    its own, mid-pass, to split. The split results are included in the
    returned list (action ``"split"``).

    ``index`` is the recall ``IndexProvider`` used for the related-notes
    search (None -> the provider resolved by :func:`corpus.search`).
    """
    if llm is None:
        return []
    root = Path(root)
    if not root.is_dir():
        return []

    results: list[dict[str, Any]] = []

    # 1. De-merge: split exactly the notes the mechanical sweep surfaced.
    #    The sweep is the sole authorization — the gardener never decides on
    #    its own which note to split (Z12).
    split_refs: set[str] = set()
    if split_sweep > 0:
        for cand in split_candidates(root, top=split_sweep):
            cand_ref = cand.get("ref")
            if not cand_ref:
                continue
            split_refs.add(str(cand_ref))
            out = _split_note(
                root, ref=str(cand_ref), llm=llm, source=source,
                tracer=tracer, archive_dir=archive_dir,
            )
            results.append({
                "ref": cand_ref,
                "slug": cand.get("slug"),
                "action": out.get("action"),
                "parent": out.get("parent"),
                "children": out.get("children"),
                "err": out.get("err"),
            })

    # 2. Reconcile the most recent writes, skipping ones already split away.
    notes = corpus.list_notes(root)
    notes = [n for n in notes if (n.get("path") not in split_refs
                                  and (n.get("uuid") or n.get("slug")) not in split_refs)]
    notes.sort(key=lambda n: (root / n["path"]).stat().st_mtime, reverse=True)
    for note in notes[:limit]:
        results.append(
            _reconcile_note(root, note, llm, tracer, source=source, archive_dir=archive_dir, index=index)
        )
    return results


def split_candidates(root: Any, *, top: int = 10) -> list[dict[str, Any]]:
    """Mechanical sweep: surface notes that *need* splitting, by descending
    file size — the cheap, deterministic, no-LLM gate (Z12).

    This is the ONLY source of split authorization. The gardener may split a
    note if and only if it appears here — it must never decide on its own,
    mid-pass, that some note should be split.

    Returns a list of ``{ref, slug, path, title, size}`` sorted largest
    first.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    notes = []
    for f in sorted(root.glob("*.md")):
        size = f.stat().st_size
        if size <= 0:
            continue
        note = corpus.read_note_meta(f)
        notes.append({
            "ref": note.get("uuid") or note.get("slug"),
            "slug": note.get("slug"),
            "path": f.name,
            "title": note.get("title") or note.get("slug"),
            "size": size,
        })
    notes.sort(key=lambda n: n["size"], reverse=True)
    return notes[:top]


def _reconcile_note(
    root: Path,
    note: dict[str, Any],
    llm: StructuredLLM,
    tracer: Tracer,
    *,
    source: Optional[str],
    archive_dir: str,
    index: Any = None,
) -> dict[str, Any]:
    ref = note.get("uuid") or note.get("slug")
    slug = note.get("slug", "")
    kind = note.get("kind", "")
    topic = (note.get("title") or slug or "").strip()
    body = (note.get("body") or "").strip()
    try:
        if not body:
            tracer("tend_writes", root, action="kept", ref=ref, slug=slug)
            return {"ref": ref, "slug": slug, "action": "kept"}

        # The shared judgment spine (search + filter + judge + verify).
        # exclude_ref/path keep this note from being compared to itself;
        # decisions never merge (decide_merge_target returns None for them).
        target_ref = decide_merge_target(
            root,
            content=body,
            topic=topic,
            kind=kind,
            llm=llm,
            index=index,
            limit=5,
            exclude_ref=ref,
            exclude_path=note.get("path"),
        )

        if target_ref:
            _fold_and_archive(root, note, target_ref, source, archive_dir)
            tracer(
                "tend_writes", root, action="merged", ref=ref, slug=slug,
                kind=kind, target=target_ref,
            )
            return {"ref": ref, "slug": slug, "action": "merged", "target": target_ref}

        # No verified merge target: fetch the related notes and link them so
        # the graph grows. Decisions (and near-duplicates the judge declined)
        # land here — decisions are append-only history, never folded.
        hits = corpus.search(topic, root, limit=5, index=index) if topic else []
        others = [
            h for h in hits
            if h.get("path") != note.get("path") and (h.get("uuid") or h.get("slug")) != ref
        ]
        other_notes: list[dict[str, Any]] = []
        for h in others:
            r = corpus.read(h.get("uuid") or h.get("slug"), root, resolve_links=False)
            if r["found"]:
                other_notes.append(r["note"])
        if not other_notes:
            tracer("tend_writes", root, action="kept", ref=ref, slug=slug)
            return {"ref": ref, "slug": slug, "action": "kept"}

        added = _link_related(root, note, other_notes)
        tracer("tend_writes", root, action="linked", ref=ref, slug=slug, kind=kind, links=added)
        return {"ref": ref, "slug": slug, "action": "linked", "links": added}
    except Exception:
        logger.warning("zk-memory: tend_writes failed for %s", ref, exc_info=True)
        tracer("tend_writes", root, action="failed", ref=ref, slug=slug)
        return {"ref": ref, "slug": slug, "action": "failed"}


def _fold_and_archive(
    root: Path,
    note: dict[str, Any],
    target_ref: str,
    source: Optional[str],
    archive_dir: str,
) -> None:
    """Append the note's body into the target (append-only), then retire
    the duplicate to ``.archive/`` — reversible, never deleted."""
    body = (note.get("body") or "").strip()
    if body:
        corpus.merge(target_ref, body, root, source=source)
    path = root / note["path"]
    arch = root / archive_dir
    arch.mkdir(parents=True, exist_ok=True)
    dest = arch / path.name
    if dest.exists():
        dest = arch / f"{path.stem}-{str(target_ref)[:8]}{path.suffix}"
    shutil.move(str(path), str(dest))


def _link_related(root: Path, note: dict[str, Any], other_notes: list[dict[str, Any]]) -> list[str]:
    """Append ``[label](slug.md)`` out-links to the related notes."""
    links = [
        (n.get("title") or n.get("slug"), n.get("slug"))
        for n in other_notes
        if n.get("slug")
    ]
    path = root / note["path"]
    try:
        return corpus.add_related_links(path, links)
    except Exception:
        logger.warning("zk-memory: tend_writes link failed for %s", note.get("slug"), exc_info=True)
        return []