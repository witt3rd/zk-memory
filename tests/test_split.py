"""Memory.split_note / decide_split_fragments / split_candidates — the
de-merge / re-atomicize spine (Z12). Driven by StructuredLLM stubs."""

from __future__ import annotations

import pytest

from zk_memory import Memory, corpus
from zk_memory.split import decide_split_fragments
from zk_memory.tend import split_candidates


class _ScriptedLLM:
    def __init__(self, results):
        self._results = list(results)

    def __call__(self, messages, *, schema, name):
        return self._results.pop(0) if self._results else None


@pytest.fixture
def root(tmp_path):
    return tmp_path / "zk"


def _biography(root, title="Judy", body=None):
    """A long biography note (many facts) to split."""
    body = body or "\n".join(
        f"---\n*2026-01-0{i}:* Judy moved to Seattle.\n"
        f"*2026-02-0{i}:* Judy joined the analytics team.\n"
        f"*2026-03-0{i}:* Judy decided to adopt blue-green deploys.\n"
        for i in range(1, 5)
    )
    return Memory(root=root).write("judy", title, body)


_SPLIT_DECISION = {
    "split": True,
    "parent_summary": "Judy is a colleague in Seattle who joined analytics and drives deploy decisions.",
    "fragments": [
        {"kind": "entity_update", "title": "Judy moved", "slug": "judy-moved", "content": "Judy moved to Seattle.", "topic": "Judy"},
        {"kind": "entity_update", "title": "Judy team", "slug": "judy-team", "content": "Judy joined analytics.", "topic": "Judy"},
        {"kind": "decision", "title": "Blue-green deploys", "slug": "blue-green-deploys", "content": "Adopt blue-green deploys.", "choice": "blue-green deploys", "rationale": "rollback safety", "topic": "deploys"},
    ],
}


def test_split_note_splits_into_parent_and_children(root):
    seed = _biography(root)
    m = Memory(root=root, llm=_ScriptedLLM([_SPLIT_DECISION]))
    out = m.split_note(seed["uuid"])
    assert out["action"] == "split"
    assert out["parent"]
    assert len(out["children"]) == 3

    # parent is a summary note with kind preserved
    parent = Memory(root=root).read(out["parent"])
    assert parent["found"]
    assert "Judy is a colleague" in parent["note"]["body"]

    # children exist with correct kinds
    files = {f.name: f for f in root.glob("*.md")}
    assert any("judy-moved" in n for n in files)
    assert any("judy-team" in n for n in files)
    assert any("blue-green" in n for n in files)

    # decision child preserved choice/rationale
    for f in files.values():
        if "blue-green" in f.name:
            text = f.read_text()
            assert "**Decision:** blue-green deploys" in text
            assert "**Rationale:** rollback safety" in text

    # original biography retired to .archive, not deleted
    assert not (root / seed["path"]).exists()
    archived = list((root / ".archive").glob("*.md"))
    assert len(archived) == 1


def test_split_note_judge_declines_when_not_biography(root):
    seed = _biography(root)
    m = Memory(root=root, llm=_ScriptedLLM([{"split": False, "parent_summary": "", "fragments": []}]))
    out = m.split_note(seed["uuid"])
    assert out["action"] == "not_split"
    # original untouched, not archived
    assert (root / seed["path"]).exists()
    assert not (root / ".archive").exists()


def test_split_note_no_llm_errors(root):
    seed = _biography(root)
    m = Memory(root=root)
    out = m.split_note(seed["uuid"])
    assert out["action"] == "error"


def test_decide_split_fragments_truncates_to_cap(root):
    seed = _biography(root)
    many = {"split": True, "parent_summary": "S", "fragments": [
        {"kind": "concept", "title": f"c{i}", "slug": f"c{i}", "content": f"x{i}"} for i in range(8)
    ]}
    llm = _ScriptedLLM([many])
    out = decide_split_fragments(root, ref=seed["uuid"], llm=llm, max_fragments=4)
    assert out["split"] is True
    assert len(out["fragments"]) == 4  # belt-and-suspenders cap


def test_split_note_missing_ref_not_split(root):
    m = Memory(root=root, llm=_ScriptedLLM([]))
    out = m.split_note("nonexistent-uuid")
    assert out["action"] == "not_split"


def test_split_candidates_surfaces_largest_first(root):
    Memory(root=root).write("small", "Small", "tiny")
    big = Memory(root=root).write("big", "Big", "x" * 5000)
    Memory(root=root).write("med", "Med", "y" * 500)
    cands = split_candidates(root, top=2)
    assert cands[0]["slug"].endswith("-big")
    assert len(cands) == 2
    assert cands[0]["size"] > cands[1]["size"]


def test_split_candidates_empty_corpus(tmp_path):
    assert split_candidates(tmp_path / "empty") == []


def test_merge_guard_does_not_merge_split_parent_into_child(root):
    """A split-produced parent must not be folded back into a child."""
    seed = _biography(root)
    m = Memory(root=root, llm=_ScriptedLLM([_SPLIT_DECISION]))
    m.split_note(seed["uuid"])

    # Now reconcile the parent against the corpus: it must NOT merge into a child.
    parent_slug = [n for n in root.glob("*.md") if "judy" in n.name and n.name != seed["path"]]
    assert parent_slug  # parent exists

    # The parent links out to children; a naive merge judge would pick a child.
    # Verify decide_merge_target excludes parent/child relations.
    from zk_memory.integrate import decide_merge_target
    parent_path = parent_slug[0].name
    parent_ref = parent_slug[0].stem
    # Search will find the children; the guard should make decide return None
    # (no verified merge target), because every hit is a child of the parent.
    out = decide_merge_target(
        root, content="summary", topic="Judy", kind="concept",
        llm=_ScriptedLLM([{"action": "merge", "merge_target_ref": "child"}]),  # judge picks a child
        exclude_path=parent_path, exclude_ref=parent_ref,
    )
    assert out is None  # the child is excluded by the parent/child guard


def test_gardener_splits_sweep_surfaced_notes(root):
    """The gardener DOES split — but only notes the mechanical sweep surfaced."""
    # A big biography that the sweep will surface.
    big = _biography(root, title="Big Judy", body="x" * 3000 + "\n\n" + "y" * 3000)
    # A small note that should NOT be swept for splitting.
    small = Memory(root=root).write("small", "Small", "tiny")

    # The gardener scripted to split: first call (split judge) -> split decision;
    # subsequent reconcile passes see no hits (no LLM merge calls) -> kept.
    from zk_memory.tend import tend_writes
    results = tend_writes(
        root,
        llm=_ScriptedLLM([_SPLIT_DECISION]),
        tracer=lambda *a, **k: None,
        split_sweep=2,  # authorize splitting the top 2 sweep candidates
    )

    split_actions = [r for r in results if r.get("action") == "split"]
    # The big biography was swept and split by the gardener.
    assert any(r.get("ref") == big["uuid"] for r in split_actions)
    # The original biography was archived; a summary parent now exists.
    assert not (root / big["path"]).exists()
    assert (root / ".archive").exists()
    # The small note was NOT split (not a sweep candidate).
    small_split = [r for r in split_actions if r.get("ref") == small["uuid"]]
    assert not small_split

