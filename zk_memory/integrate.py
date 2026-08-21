"""zk_memory.integrate — the careful-write pipeline, as discrete functions.

The write path is deliberately cheap (mechanical safety only); judgment
happens here. This module owns the one true spine for "place this atomic
memory into the corpus so it perfectly integrates":

    decide_merge_target()   # search + filter + judge + verify  (pure decision)
    integrate()             # the public careful write: merge | create

Both the capture-time path (``retain.process_candidate``) and the gardener
pass (``tend._reconcile_note``) had copy-pasted this spine. Now they compose
over it, and ``integrate()`` is exposed to embedders as ``Memory.integrate`` —
a careful write that returns merge-or-create with the merge target verified
against the fetched hits.

The one place "perfect integration" is decided is ``judge_merge`` (see
``judge.py``): merge only when the existing note is truly the same entity,
prefer create. Everything here is mechanical around that judgment.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

from zk_memory import corpus
from zk_memory.judge import StructuredLLM, judge_merge

logger = logging.getLogger(__name__)

# A tracer is ``callable(event, root, **fields)`` — see zk_memory.probe.
Tracer = Callable[..., None]


def _parent_or_child(root: Path, a_path: Optional[str], b_path: Optional[str]) -> bool:
    """True when two notes are in a parent/child relation (one links to the
    other as a split artifact). Deterministic guard: split-produced notes
    must not be folded back into their own biography (Z12)."""
    if not a_path or not b_path or a_path == b_path:
        return False
    a = root / a_path
    b = root / b_path
    if not a.exists() or not b.exists():
        return False
    a_raw = a.read_text(errors="replace")
    b_raw = b.read_text(errors="replace")
    a_links = set(re.findall(r"\]\(([^)]+?\.md)\)", a_raw))
    b_links = set(re.findall(r"\]\(([^)]+?\.md)\)", b_raw))
    return b.name in a_links or a.name in b_links


def decide_merge_target(
    root: Any,
    *,
    content: str,
    topic: str,
    kind: str,
    llm: StructuredLLM,
    index: Any = None,
    limit: int = 3,
    exclude_ref: Optional[str] = None,
    exclude_path: Optional[str] = None,
) -> Optional[str]:
    """The shared judgment spine: returns a *verified* merge target uuid, or
    None when the memory should be created (or kept) instead.

    Stages:
      1. search the corpus for ``topic`` (no LLM).
      2. fetch the full bodies of the hits, dropping the note itself (when
         ``exclude_ref``/``exclude_path`` are given — the gardener case).
      3. decisions never merge -> return None (a decision is append-only
         history; it always becomes a new dated zettel).
      4. one ``judge_merge`` call across all hits at once.
      5. verify the judge's ``merge_target_ref`` is actually among the fetched
         uuids before trusting it — a hallucinated ref is never honored.

    ``judge_merge`` returns None on no hits / failure, which callers treat as
    "create". Never raises.
    """
    if kind == "decision" or not topic or not content:
        return None
    hits = corpus.search(topic, root, limit=limit, index=index)
    notes: list[dict[str, Any]] = []
    for h in hits:
        if exclude_ref and h.get("uuid") == exclude_ref:
            continue
        if exclude_ref and h.get("slug") == exclude_ref:
            continue
        if exclude_path and h.get("path") == exclude_path:
            continue
        ref = h.get("uuid") or h.get("slug")
        if not ref:
            continue
        result = corpus.read(ref, root, resolve_links=False)
        if not result["found"]:
            continue
        note = result["note"]
        # Never merge into a note that already has a parent/child relation
        # with the candidate note (split artifacts must not fold back into
        # their own biography — Z12). Uses the candidate's own path, passed
        # as exclude_path in the gardener case.
        if _parent_or_child(root, exclude_path, note.get("path")):
            continue
        notes.append(note)
    if not notes:
        return None
    decision = judge_merge({"kind": kind or "concept", "content": content}, notes, llm)
    if not decision or decision.get("action") != "merge":
        return None
    target = (decision.get("merge_target_ref") or "").strip()
    valid = {n.get("uuid") for n in notes if n.get("uuid")}
    if target and target in valid:
        return target
    if target:
        logger.warning(
            "zk-memory: merge_target_ref %r not among fetched hits; falling back to create",
            target,
        )
    return None


def _build_body(
    content: str,
    kind: str,
    choice: Optional[str] = None,
    rationale: Optional[str] = None,
) -> str:
    """Assemble a note body; decisions carry choice/rationale."""
    if kind != "decision":
        return content
    parts: list[str] = []
    if choice:
        parts.append(f"**Decision:** {choice}")
    parts.append(content)
    if rationale:
        parts.append(f"**Rationale:** {rationale}")
    return "\n\n".join(parts)


def integrate(
    root: Any,
    *,
    content: str,
    topic: str,
    kind: str = "concept",
    llm: StructuredLLM,
    source: Optional[str] = None,
    index: Any = None,
    limit: int = 3,
    title: Optional[str] = None,
    slug: Optional[str] = None,
    choice: Optional[str] = None,
    rationale: Optional[str] = None,
    tracer: Optional[Tracer] = None,
    post_merge: Optional[Callable[[dict[str, Any]], None]] = None,
    post_create: Optional[Callable[[dict[str, Any]], None]] = None,
) -> dict[str, Any]:
    """Careful write: merge into the right existing note, else create a new one.

    Returns ``{action, target?, path?, uuid?, err?}``:
      - ``{"action": "merged", "target": <uuid>}`` — appended into an existing
        note (append-only).
      - ``{"action": "created", "path", "uuid"}`` — a new atomic note.

    ``post_merge(outcome)`` / ``post_create(outcome)`` are continuations run
    after each outcome — the composition seam (e.g. the gardener archives the
    now-redundant note on merge, or links on keep). ``title``/``slug`` are
    required for create; ``choice``/``rationale`` build a decision body.
    Never raises; failures return ``{"action": "error", "err": ...}``.
    """
    target = decide_merge_target(
        root,
        content=content, topic=topic, kind=kind, llm=llm,
        index=index, limit=limit,
    )
    if target:
        result = corpus.merge(target, content, root, source=source)
        if not result.get("ok"):
            if tracer:
                tracer("integrate", root, action="merge_failed", target=target,
                       ok=False, err=result.get("err"))
            return {"action": "error", "err": result.get("err", "merge failed")}
        outcome = {"action": "merged", "target": target}
        if post_merge:
            post_merge(outcome)
        return outcome

    body = _build_body(content, kind, choice=choice, rationale=rationale)
    result = corpus.write(slug or "", title or "", body, root, source=source)
    if not result.get("ok"):
        if tracer:
            tracer("integrate", root, action="create_failed", slug=slug,
                   ok=False, err=result.get("err"))
        return {"action": "error", "err": result.get("err", "create failed")}
    if kind:
        corpus._ensure_field(Path(result["path"]), "kind", kind)
    outcome = {"action": "created", "path": result["path"], "uuid": result.get("uuid")}
    if post_create:
        post_create(outcome)
    return outcome
