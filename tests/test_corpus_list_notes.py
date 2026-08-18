"""corpus.list_notes() -- enumerate every note in the corpus."""

from __future__ import annotations


def test_list_notes_empty_dir_returns_empty_list(zk_module, zk_root):
    zk_root.mkdir(parents=True)
    assert zk_module.list_notes(zk_root) == []


def test_list_notes_missing_dir_returns_empty_list(zk_module, zk_root):
    assert not zk_root.exists()
    assert zk_module.list_notes(zk_root) == []


def test_list_notes_returns_metadata_for_each_note(zk_module, zk_root, monkeypatch):
    zk_module.write("first", "First Note", "First body.", zk_root)
    zk_module.write("second", "Second Note", "Second body.", zk_root)

    notes = zk_module.list_notes(zk_root)
    assert len(notes) == 2
    titles = {n["title"] for n in notes}
    assert titles == {"First Note", "Second Note"}
    for n in notes:
        assert n["uuid"]
        assert n["slug"]
        assert n["path"].endswith(".md")