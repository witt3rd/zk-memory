"""judge.distill_text() / judge.judge_merge() -- the two-stage write-time
judgment, driven by injected StructuredLLM stubs (never a real provider)."""

from __future__ import annotations

import pytest


class _StubLLM:
    """A StructuredLLM stub: returns a fixed result, records the call."""

    def __init__(self, result=None):
        self._result = result
        self.calls = []

    def __call__(self, messages, *, schema, name):
        self.calls.append({"messages": messages, "schema": schema, "name": name})
        return self._result


def _concept():
    return {
        "kind": "concept",
        "topic": "atomic notes",
        "title": "Atomic Notes",
        "slug": "atomic-notes",
        "content": "One idea per note.",
    }


# ---------------------------------------------------------------------------
# distill_text
# ---------------------------------------------------------------------------


def test_distill_text_returns_empty_when_none(judge_module):
    assert judge_module.distill_text("hi", _StubLLM(None)) == []


def test_distill_text_returns_empty_when_not_worth_retaining(judge_module):
    llm = _StubLLM({"worth_retaining": False, "candidates": []})
    assert judge_module.distill_text("u", llm) == []


def test_distill_text_parses_candidates(judge_module):
    candidates = [
        _concept(),
        {"kind": "entity_update", "topic": "Judy", "title": "", "slug": "",
         "content": "Judy is arriving in two weeks."},
    ]
    llm = _StubLLM({"worth_retaining": True, "candidates": candidates})
    result = judge_module.distill_text("user said something", llm)
    assert result == candidates

    call = llm.calls[0]
    assert call["name"] == "record_candidates"
    assert call["schema"]["required"] == ["worth_retaining", "candidates"]
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][1]["role"] == "user"
    assert call["messages"][1]["content"] == "user said something"


def test_distill_text_filters_non_dict_candidates(judge_module):
    llm = _StubLLM({"worth_retaining": True, "candidates": ["not-a-dict", {"kind": "concept"}]})
    assert judge_module.distill_text("u", llm) == [{"kind": "concept"}]


def test_distill_text_truncates_pathological_input(judge_module, monkeypatch):
    """The hard safety cap, not normal-path truncation -- only bites on
    an absurdly large input, and the call must still go through."""
    llm = _StubLLM({"worth_retaining": False, "candidates": []})
    monkeypatch.setattr(judge_module, "_MAX_INPUT_TOKENS", 10)  # 10 tokens ~= 40 chars

    huge = "x" * 100_000
    judge_module.distill_text(huge, llm)
    sent_text = llm.calls[0]["messages"][1]["content"]
    assert len(sent_text) <= 40


# ---------------------------------------------------------------------------
# judge_merge
# ---------------------------------------------------------------------------


def test_judge_merge_returns_none_with_no_hits(judge_module):
    # Should short-circuit before even calling the LLM.
    llm = _StubLLM()
    result = judge_module.judge_merge({"kind": "concept", "content": "x"}, [], llm)
    assert result is None
    assert llm.calls == []


def test_judge_merge_returns_none_when_llm_returns_none(judge_module):
    hit_notes = [{"uuid": "abc", "title": "Existing", "body": "..."}]
    llm = _StubLLM(None)
    assert judge_module.judge_merge({"kind": "concept", "content": "x"}, hit_notes, llm) is None


def test_judge_merge_parses_decision_and_includes_hit_bodies(judge_module):
    decision = {"action": "merge", "merge_target_ref": "note-uuid-1"}
    llm = _StubLLM(decision)

    hit_notes = [
        {"uuid": "note-uuid-1", "title": "Judy", "body": "Judy's travel plans."},
        {"uuid": "note-uuid-2", "title": "Unrelated", "body": "Nothing to do with Judy."},
    ]
    candidate = {"kind": "entity_update", "content": "Judy is arriving in two weeks."}

    result = judge_module.judge_merge(candidate, hit_notes, llm)
    assert result == decision

    call = llm.calls[0]
    assert call["name"] == "record_merge_decision"
    assert call["schema"]["required"] == ["action"]
    sent_text = call["messages"][1]["content"]
    # Both hit bodies were fetched and included -- a single comparison
    # call across all hits, not one call per hit.
    assert "note-uuid-1" in sent_text
    assert "note-uuid-2" in sent_text
    assert "Judy's travel plans." in sent_text
    assert "Nothing to do with Judy." in sent_text


def test_judge_merge_parses_create_decision(judge_module):
    decision = {"action": "create"}
    llm = _StubLLM(decision)
    hit_notes = [{"uuid": "note-uuid-1", "title": "Unrelated", "body": "..."}]
    result = judge_module.judge_merge({"kind": "concept", "content": "new idea"}, hit_notes, llm)
    assert result == decision


# ---------------------------------------------------------------------------
# TOOL_DESCRIPTIONS / StructuredLLM surface
# ---------------------------------------------------------------------------


def test_tool_descriptions_covers_both_tools(judge_module):
    assert "record_candidates" in judge_module.TOOL_DESCRIPTIONS
    assert "record_merge_decision" in judge_module.TOOL_DESCRIPTIONS
    for desc in judge_module.TOOL_DESCRIPTIONS.values():
        assert desc


def test_structured_llm_is_importable(judge_module):
    # The protocol is the public injection contract.
    assert hasattr(judge_module, "StructuredLLM")


def test_distill_schema_requires_worth_retaining_and_candidates(judge_module):
    assert set(judge_module._DISTILL_SCHEMA["required"]) == {"worth_retaining", "candidates"}


def test_merge_schema_requires_action(judge_module):
    assert judge_module._MERGE_JUDGE_SCHEMA["required"] == ["action"]