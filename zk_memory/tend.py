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
from zk_memory.judge import StructuredLLM, judge_merge

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
    source: Optional[str] = None,
    archive_dir: str = DEFAULT_ARCHIVE_DIR,
) -> list[dict[str, Any]]:
    """Reconcile the most recent writes against the corpus.

    Candidates are the newest notes by mtime (writes and merges both touch
    it). Returns a list of ``{ref, slug, action, target?, links?}`` per
    candidate. No ``llm`` -> returns [] (no-op, same as retain without an
    LLM). Never raises.
    """
    if llm is None:
        return []
    root = Path(root)
    if not root.is_dir():
        return []
    notes = corpus.list_notes(root)
    notes.sort(key=lambda n: (root / n["path"]).stat().st_mtime, reverse=True)
    results: list[dict[str, Any]] = []
    for note in notes[:limit]:
        results.append(
            _reconcile_note(root, note, llm, tracer, source=source, archive_dir=archive_dir)
        )
    return results


def _reconcile_note(
    root: Path,
    note: dict[str, Any],
    llm: StructuredLLM,
    tracer: Tracer,
    *,
    source: Optional[str],
    archive_dir: str,
) -> dict[str, Any]:
    ref = note.get("uuid") or note.get("slug")
    slug = note.get("slug", "")
    kind = note.get("kind", "")
    topic = (note.get("title") or slug or "").strip()
    try:
        hits = corpus.search(topic, root, limit=5) if topic else []
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

        # Decisions are append-only history: never fold one into an existing
        # note; a new decision on the same topic is a new dated zettel.
        if kind == "decision":
            added = _link_related(root, note, other_notes)
            tracer("tend_writes", root, action="linked", ref=ref, slug=slug, kind=kind, links=added)
            return {"ref": ref, "slug": slug, "action": "linked", "links": added}

        decision = judge_merge(
            {"kind": kind or "concept", "content": note.get("body", "") or ""},
            other_notes,
            llm,
        )
        if decision and decision.get("action") == "merge":
            target_ref = (decision.get("merge_target_ref") or "").strip()
            valid = {n.get("uuid") for n in other_notes if n.get("uuid")}
            if target_ref and target_ref in valid:
                _fold_and_archive(root, note, target_ref, source, archive_dir)
                tracer(
                    "tend_writes", root, action="merged", ref=ref, slug=slug,
                    kind=kind, target=target_ref,
                )
                return {"ref": ref, "slug": slug, "action": "merged", "target": target_ref}
            if target_ref:
                logger.warning(
                    "zk-memory: tend_writes merge_target_ref %r not among hits; keeping",
                    target_ref,
                )

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