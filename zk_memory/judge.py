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
       - "decision": a commitment or choice that was made — recorded as
         an authoritative, dated, recallable fact (choice, alternatives,
         rationale), written as a standalone decision zettel.

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
                        "enum": ["concept", "entity_update", "decision"],
                        "description": (
                            "'concept': a self-contained evergreen "
                            "idea, substantial enough to stand alone "
                            "as a new node. 'entity_update': a "
                            "temporal or attribute-level fact about "
                            "an existing entity/topic — would be a "
                            "useless orphan as its own note. "
                            "'decision': a commitment or choice that "
                            "was made — the thing decided, the "
                            "alternatives considered and rejected, "
                            "and the rationale. Recorded as an "
                            "authoritative, dated, recallable fact "
                            "(not just the topic it was about)."
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
                            "fragment, own words, not a transcript. "
                            "For 'decision': the decision itself, own "
                            "words — include the choice made, the "
                            "alternatives considered (and rejected), "
                            "and the rationale. Phrase it so a future "
                            "session can answer 'what did we decide?' "
                            "verbatim (e.g. 'we decided to adopt "
                            "blue-green deploys and keep "
                            "last-deploy-k valid for rollback')."
                        ),
                    },
                    "choice": {
                        "type": "string",
                        "description": (
                            "For 'decision': the thing that was "
                            "decided, in a few words (e.g. 'adopt "
                            "blue-green deploys, keep last-deploy-k "
                            "for rollback'). Optional for other kinds."
                        ),
                    },
                    "alternatives": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "For 'decision': the alternatives that "
                            "were considered and rejected, one string "
                            "each. Optional."
                        ),
                    },
                    "rationale": {
                        "type": "string",
                        "description": (
                            "For 'decision': why this choice was "
                            "made, own words. Optional."
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

There are three kinds of output, and conflating them ruins the corpus:

- CONCEPT: a self-contained, evergreen idea. It has enough conceptual weight \
to stand entirely on its own as a new node, ready to be linked to other \
ideas.
- ENTITY_UPDATE: a temporal or attribute-level data point (e.g. "Judy \
arriving in two weeks"). If made its own standalone note, it would be a \
useless orphan. It belongs appended to an existing entity/topic note \
instead.
- DECISION: a commitment or choice that was made. The turn records what was \
decided (not merely what was discussed) — a choice made, alternatives \
weighed and rejected, a rationale, a direction adopted. Record the DECISION \
itself, verbatim in your own words: the choice (e.g. "we decided to adopt \
blue-green deploys and keep last-deploy-k valid for rollback"), the \
alternatives considered, and the rationale. Do NOT reduce a decision to a \
generic concept about its topic — a decision must be recallable by a future \
session as an authoritative fact ('what did we decide?').

When in doubt: a general discussion of tradeoffs with no commitment is a \
CONCEPT; a turn that makes a call ("we should X", "we chose Y over Z", "let's \
commit to X") is a DECISION.

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


# ---------------------------------------------------------------------------
# Stage 3 — de-merge / split: a biography note into a summary parent + atomic
# children (the inverse of judge_merge; Z12).
# ---------------------------------------------------------------------------

_SPLIT_FRAGMENT_MAX = 4

_SPLIT_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["split"],
    "properties": {
        "split": {
            "type": "boolean",
            "description": (
                "True only if this note is a genuine biography — many "
                "distinct facts/timeline that are no longer one atomic "
                "thought. False if it's still a single dense idea (never "
                "over-split)."
            ),
        },
        "parent_summary": {
            "type": "string",
            "description": (
                "When split: a short summary of what this entity/topic is "
                "and its arc — the reduced parent spine. Own words, "
                "a few sentences. Ignored when split is false."
            ),
        },
        "fragments": {
            "type": "array",
            "maxItems": _SPLIT_FRAGMENT_MAX,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "title", "slug", "content"],
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["concept", "entity_update", "decision"],
                        "description": (
                            "The kind of this atomic child. A decision "
                            "fragment must be 'decision' and carries "
                            "choice/rationale — never folded or destroyed."
                        ),
                    },
                    "title": {"type": "string", "description": "Child title."},
                    "slug": {"type": "string", "description": "Child slug (no date prefix)."},
                    "content": {
                        "type": "string",
                        "description": (
                            "The child's atomic content, own words — one "
                            "distinct fact/thought from the original. Not a "
                            "transcript excerpt."
                        ),
                    },
                    "topic": {"type": "string", "description": "Child topic (for its own merge spine)."},
                    "choice": {
                        "type": "string",
                        "description": "For 'decision' fragments: the choice made.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "For 'decision' fragments: the rationale.",
                    },
                },
            },
        },
    },
}

_SPLIT_JUDGE_SYSTEM_PROMPT = """You are the split judge for a zettelkasten memory. \
Given a single note that has grown past atomicity (a biography rather than one \
thought), decide whether to split it, and if so how.

Split ONLY when the note is genuinely a biography: many distinct facts or a \
timeline that belong as separate atomic notes. When in doubt, prefer NOT to \
split — a single dense idea must never be carved into orphans. The split judge \
is at least as conservative as the merge judge.

If split:
- The PARENT becomes a short summary: what the entity/topic is and its arc. \
Reduced, less detailed — the detail lives in the children.
- Each CHILD is one atomic note preserving a distinct fact/thought from the \
original, in your own words (never a transcript excerpt). A decision fragment \
must be a 'decision' child with its choice and rationale.
- Return AT MOST 4 fragments. If the note has more distinct facts than that, \
group the most related ones so the count stays within 4 — it will be split \
again on a later pass if needed.
- The children are linked to from the parent; you are NOT creating a nested \
hierarchy — flat notes, linked."""


def judge_split(note: dict[str, Any], llm: StructuredLLM) -> Optional[dict[str, Any]]:
    """Judge whether an overgrown note should be split, and how.

    Returns the parsed decision dict (``{split, parent_summary, fragments}``),
    or None on any failure (callers treat None as "don't split"). Never raises.
    ``judge_split`` is the decision half of the de-merge pair — the analogue
    of ``judge_merge`` for the inverse direction.
    """
    body = (note.get("body") or "").strip()
    title = note.get("title") or note.get("slug") or "?"
    if not body:
        return None
    user_text = (
        f"Note title: {title}\n\nFull note body:\n{body}\n\n"
        f"Decide whether to split this note, and if so into a summary parent "
        f"plus at most {_SPLIT_FRAGMENT_MAX} atomic children."
    )
    return llm(
        [
            {"role": "system", "content": _SPLIT_JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        schema=_SPLIT_JUDGE_SCHEMA,
        name="record_split_decision",
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
    "record_split_decision": (
        "Decide whether an overgrown biography note should be split into a "
        "summary parent plus atomic child notes. Always call this tool "
        "exactly once."
    ),
}