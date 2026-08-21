"""Retain pipeline — distill then merge-or-create, all host-agnostic.

This is the write-side judgment that turns a turn (or a compaction batch)
into zettelkasten writes. It owns ``process_candidate`` (the per-candidate
route that used to live in the Hermes provider): search the candidate's
topic, judge merge-vs-create against the fetched hits, then write or
merge. The LLM is an injected ``StructuredLLM``; corpus operations are the
plain ``zk_memory.corpus`` functions; diagnostics go to an injected
``tracer`` callable. Nothing here imports hermes / agent.*.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

from zk_memory import corpus
from zk_memory.integrate import integrate
from zk_memory.judge import StructuredLLM, distill_text

logger = logging.getLogger(__name__)

# A tracer is ``callable(event, root, **fields)`` — see zk_memory.probe.
Tracer = Callable[..., None]


def retain_turn(
    root: Any,
    llm: Optional[StructuredLLM],
    tracer: Tracer,
    user_content: str,
    assistant_content: str,
    *,
    session_id: str = "",
    source: Optional[str] = None,
    index: Any = None,
) -> list[str]:
    """Distill one turn and process every candidate. Returns the list of
    retained labels (empty when nothing was retained). Never raises.

    ``index`` is the recall ``IndexProvider`` used for the merge-vs-create
    search (None -> the provider resolved by :func:`corpus.search`).
    """
    if llm is None:
        return []
    try:
        candidates = distill_text(
            f"USER: {user_content}\n\nASSISTANT: {assistant_content}", llm
        )
        tracer(
            "sync_turn_distilled",
            root,
            session_id=session_id,
            n_candidates=len(candidates),
        )
        labels: list[str] = []
        for candidate in candidates:
            label = process_candidate(root, candidate, llm, tracer, source=source, index=index)
            if label:
                labels.append(label)
        return labels
    except Exception:
        logger.warning("zk-memory: retain_turn failed", exc_info=True)
        tracer("sync_turn_failed", root, session_id=session_id)
        return []


def retain_messages(
    root: Any,
    llm: Optional[StructuredLLM],
    tracer: Tracer,
    messages: list[dict[str, Any]],
    *,
    session_id: str = "",
    source: Optional[str] = None,
    index: Any = None,
) -> list[str]:
    """Distill a batch of messages (the shape hermes hands
    ``MemoryProvider.on_pre_compress`` — turns about to be dropped by
    context compaction) and process every candidate. Returns the list of
    retained labels. System messages are skipped. Never raises.

    ``index`` is the recall ``IndexProvider`` used for the merge-vs-create
    search (None -> the provider resolved by :func:`corpus.search`).
    """
    if llm is None or not messages:
        return []
    try:
        lines = []
        for m in messages:
            role = (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) or ""
            if role == "system":
                continue
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
            if not content or not isinstance(content, str):
                continue
            lines.append(f"{role.upper()}: {content}")
        if not lines:
            return []
        candidates = distill_text("\n\n".join(lines), llm)
        labels: list[str] = []
        for candidate in candidates:
            label = process_candidate(root, candidate, llm, tracer, source=source, index=index)
            if label:
                labels.append(label)
        tracer(
            "pre_compress",
            root,
            n_messages=len(messages),
            n_candidates=len(candidates),
        )
        return labels
    except Exception:
        logger.warning("zk-memory: retain_messages failed", exc_info=True)
        tracer("pre_compress_failed", root, n_messages=len(messages))
        return []


def process_candidate(
    root: Any,
    candidate: dict[str, Any],
    llm: StructuredLLM,
    tracer: Tracer,
    *,
    source: Optional[str] = None,
    index: Any = None,
) -> Optional[str]:
    """Route one distilled candidate: merge into an existing note if the
    merge judge picks one of the search hits, otherwise create a new note.

    This is the capture-time flavor of the careful write — it composes over
    ``zk_memory.integrate.integrate`` (the shared search→judge→merge|create
    spine). ``source`` (host/agent attribution) is passed through; ``index``
    is the recall ``IndexProvider``. Returns the retained label (title or
    topic) on success, else None. Never raises — failures are logged and
    skipped so one bad candidate doesn't drop the rest of the turn's
    candidates.
    """
    try:
        topic = (candidate.get("topic") or candidate.get("title") or "").strip()
        kind = candidate.get("kind", "")

        # Decisions are first-class, dated, standalone zettels: they are
        # created as their own note (never merged into a generic concept
        # note, which would bury the authoritative choice). A later
        # decision on the same topic becomes a new dated note — both are
        # kept as history (write() refuses to overwrite by filename).
        content = (candidate.get("content") or "").strip()

        if not topic and not content:
            logger.warning("zk-memory: candidate incomplete (slug/title/content); skipping")
            tracer(
                "candidate_decision", root, kind=kind, topic=topic,
                action="create_skipped_incomplete",
            )
            return None

        outcome = integrate(
            root,
            content=content,
            topic=topic,
            kind=kind,
            llm=llm,
            source=source,
            index=index,
            title=(candidate.get("title") or "").strip(),
            slug=(candidate.get("slug") or "").strip(),
            choice=(candidate.get("choice") or "").strip() or None,
            rationale=(candidate.get("rationale") or "").strip() or None,
            tracer=tracer,
        )

        if outcome.get("action") == "error":
            tracer(
                "candidate_decision", root, kind=kind, topic=topic,
                action="failed", ok=False, err=outcome.get("err"),
            )
            return None

        action = outcome.get("action")
        if action == "merged":
            tracer(
                "candidate_decision", root, kind=kind, topic=topic,
                action="merge", target=outcome.get("target"), ok=True,
            )
        elif action == "created":
            tracer(
                "candidate_decision", root, kind=kind, topic=topic,
                action="create", slug=outcome.get("path"), ok=True,
            )
        return candidate.get("title") or candidate.get("topic") or None
    except Exception:
        logger.warning("zk-memory: candidate processing failed", exc_info=True)
        tracer("candidate_decision", root, action="failed")
        return None