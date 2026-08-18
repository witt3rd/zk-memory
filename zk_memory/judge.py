"""Write-time judgment for a zettelkasten memory.

Prompts, JSON schemas, and orchestration for the two-stage retain
judgment — all host-agnostic, with the LLM supplied as an injected
``StructuredLLM`` callable (P3). The library never imports
``openai`` / ``anthropic`` / ``litellm`` / ``agent.auxiliary_client``;
an adapter implements the callable. The Hermes adapter routes it through
the plugin's own auxiliary-task forced-tool-call path; a notebook
implements it with whatever JSON mode it has.

Two stages:

  1. distill_text — sees the raw text only, no corpus visibility. Splits
     it into zero or more candidates, each tagged:
       - "concept": a self-contained evergreen idea — a new node.
       - "entity_update": a temporal/attribute-level fact (e.g. "Judy
         arriving in two weeks") that would be a useless orphan as its
         own note — it belongs appended to an existing entity note.

  2. judge_merge — one call per candidate, given the full body of every
     corpus hit for that candidate's topic (fetched, not just snippets).
     Decides: does this belong in one of these existing notes (merge),
     or is it genuinely new (create)? One call compares across all hits
     at once rather than one call per hit — cheaper and lets the model
     reason comparatively.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol


class StructuredLLM(Protocol):
    """Caller-supplied structured LLM. Library never imports providers.

    Returns a parsed object matching ``schema``, or None on any failure.
    Never raises. ``name`` is the intent label the adapter can use to
    route a forced tool call (e.g. the tool function name).
    """

    def __call__(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict,
        name: str,
    ) -> Optional[dict[str, Any]]: ...


# Hard safety cap only — NOT a normal-path truncation. At 1M context,
# ordinary turns never hit this; it exists solely so a pathological
# paste can't blow past provider limits or balloon cost unbounded.
_CHARS_PER_TOKEN = 4
_MAX_INPUT_TOKENS = 900_000


def _truncate_to_max_tokens(text: str, max_tokens: int) -> str:
    max_chars = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


# ---------------------------------------------------------------------------
# Stage 1 — distill
# ---------------------------------------------------------------------------

_DISTILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["worth_retaining", "candidates"],
    "properties": {
        "worth_retaining": {
            "type": "boolean",
            "description": "True if this turn contains anything worth retaining.",
        },
        "candidates": {
            "type": "array",
            "description": (
                "MUST be empty when worth_retaining is false; MUST "
                "contain at least one entry when true."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "topic", "title", "slug", "content"],
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["concept", "entity_update"],
                        "description": (
                            "'concept': a self-contained evergreen "
                            "idea, substantial enough to stand alone "
                            "as a new node. 'entity_update': a "
                            "temporal or attribute-level fact about "
                            "an existing entity/topic — would be a "
                            "useless orphan as its own note."
                        ),
                    },
                    "topic": {
                        "type": "string",
                        "description": (
                            "What this is about, in a few words — "
                            "used to search the corpus for a "
                            "possible existing home (e.g. an entity "
                            "name, a project, a recurring theme)."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "Title to use IF this becomes a new note.",
                    },
                    "slug": {
                        "type": "string",
                        "description": "Short hyphenated slug to use IF this becomes a new note (no date prefix).",
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "For 'concept': the full atomic thought, "
                            "own words, one idea. For "
                            "'entity_update': just the fact/update "
                            "fragment, own words, not a transcript."
                        ),
                    },
                },
            },
        },
    },
}

_DISTILL_SYSTEM_PROMPT = """You are the write-time distiller for a zettelkasten memory. \
Given a piece of conversation (a single turn, or a longer excerpt about to \
be dropped by context compaction), extract zero or more retain candidates.

There are two very different kinds of output, and conflating them ruins the \
corpus:

- CONCEPT: a self-contained, evergreen idea. It has enough conceptual weight \
to stand entirely on its own as a new node, ready to be linked to other \
ideas.
- ENTITY_UPDATE: a temporal or attribute-level data point (e.g. "Judy \
arriving in two weeks"). If made its own standalone note, it would be a \
useless orphan. It belongs appended to an existing entity/topic note \
instead.

Most turns yield nothing: routine questions, small talk, tool mechanics, and \
anything already obvious from context are not worth retaining at all — \
worth_retaining=false, empty candidates.

When something IS worth retaining, draft each candidate's content in your \
own words — never a transcript excerpt."""


def distill_text(text: str, llm: StructuredLLM) -> list[dict[str, Any]]:
    """Distill one piece of transcript text into retain candidates.

    Returns a (possibly empty) list of candidate dicts. Never raises;
    returns [] on any failure or if there's nothing to distill.
    """
    text = _truncate_to_max_tokens(text, _MAX_INPUT_TOKENS)
    parsed = llm(
        [
            {"role": "system", "content": _DISTILL_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        schema=_DISTILL_SCHEMA,
        name="record_candidates",
    )
    if not parsed or not parsed.get("worth_retaining"):
        return []
    candidates = parsed.get("candidates") or []
    return [c for c in candidates if isinstance(c, dict)]


# ---------------------------------------------------------------------------
# Stage 2 — judge merge vs. create (one call per candidate, all hits at once)
# ---------------------------------------------------------------------------

_MERGE_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action"],
    "properties": {
        "action": {
            "type": "string",
            "enum": ["merge", "create"],
            "description": (
                "'merge' if one of the existing notes is the right "
                "home for this information; 'create' if none is."
            ),
        },
        "merge_target_ref": {
            "type": "string",
            "description": (
                "The uuid of the existing note to merge into. "
                "Required when action is 'merge'; omit otherwise."
            ),
        },
    },
}

_MERGE_JUDGE_SYSTEM_PROMPT = """You are the merge judge for a zettelkasten memory. \
Given a piece of new information and a short list of existing notes, decide \
whether the new information belongs appended to one of those existing \
notes, or whether it's genuinely new and deserves its own note.

Merge only when the existing note is truly the same entity/topic — not \
merely related. When in doubt, prefer 'create': a wrong merge pollutes an \
existing note; a missed merge just means slight duplication, which is the \
safer failure."""


def judge_merge(
    candidate: dict[str, Any],
    hit_notes: list[dict[str, Any]],
    llm: StructuredLLM,
) -> Optional[dict[str, Any]]:
    """Judge merge-vs-create for one candidate against its fetched hits.

    Returns the parsed decision dict, or None on any failure (callers
    should treat None as "create"). Never raises. Short-circuits to None
    when there are no hits (nothing to compare against).
    """
    if not hit_notes:
        return None

    notes_text = "\n\n".join(
        f"[{n.get('uuid', '')}] {n.get('title', n.get('slug', '?'))}\n{n.get('body', '').strip()}"
        for n in hit_notes
    )
    kind = candidate.get("kind", "concept")
    content = candidate.get("content", "")
    user_text = (
        f"New information (kind={kind}):\n{content}\n\n"
        f"Existing notes found for this topic:\n{notes_text}"
    )
    return llm(
        [
            {"role": "system", "content": _MERGE_JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        schema=_MERGE_JUDGE_SCHEMA,
        name="record_merge_decision",
    )


# Tool descriptions for adapters that route these structured calls through
# a forced tool call. Kept here so all prompt text lives in the library;
# adapters (e.g. the Hermes one) look up the description by tool name.
TOOL_DESCRIPTIONS: dict[str, str] = {
    "record_candidates": (
        "Record zero or more retain candidates extracted from this "
        "turn. Always call this tool exactly once per invocation."
    ),
    "record_merge_decision": (
        "Decide whether new information belongs in one of the given "
        "existing notes, or is genuinely new. Always call this tool "
        "exactly once."
    ),
}