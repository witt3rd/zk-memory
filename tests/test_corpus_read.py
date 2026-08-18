"""corpus.read() -- reading one note and resolving its links."""

from __future__ import annotations


def _write(zk_module, zk_root, monkeypatch, slug, title, body):
    monkeypatch.setattr(zk_module.shutil, "which", lambda name: None)
    return zk_module.write(slug, title, body, zk_root)


def test_read_found_note_returns_body(zk_module, zk_root, monkeypatch):
    w = _write(zk_module, zk_root, monkeypatch, "hello", "Hello", "Hello body content.")
    result = zk_module.read(w["uuid"], zk_root)

    assert result["found"] is True
    assert "Hello body content." in result["note"]["body"]
    assert result["note"]["slug"].endswith("hello")


def test_read_found_by_slug(zk_module, zk_root, monkeypatch):
    w = _write(zk_module, zk_root, monkeypatch, "by-slug", "By Slug", "Body.")
    from pathlib import Path
    slug = Path(w["path"]).stem
    result = zk_module.read(slug, zk_root)
    assert result["found"] is True
    assert result["note"]["slug"] == slug


def test_read_missing_note_returns_not_found(zk_module, zk_root):
    result = zk_module.read("nonexistent-ref", zk_root)
    assert result["found"] is False
    assert result["note"] == {}
    assert result["links"] == []


def test_read_resolves_links_between_notes(zk_module, zk_root, monkeypatch):
    target = _write(zk_module, zk_root, monkeypatch, "target-note", "Target Note", "The target body.")
    from pathlib import Path
    target_slug = Path(target["path"]).stem

    source = _write(
        zk_module,
        zk_root,
        monkeypatch,
        "source-note",
        "Source Note",
        f"See [the target]({target_slug}.md) and [a ghost](does-not-exist.md).",
    )

    result = zk_module.read(source["uuid"], zk_root)
    assert result["found"] is True
    assert len(result["links"]) == 2

    resolved = [l for l in result["links"] if l["ref"] == target_slug][0]
    assert resolved["resolved"] is True
    assert resolved["label"] == "the target"
    assert resolved["title"] == "Target Note"

    ghost = [l for l in result["links"] if l["ref"] == "does-not-exist"][0]
    assert ghost["resolved"] is False
    assert ghost["label"] == "a ghost"


def test_read_no_resolve_links_skips_link_extraction(zk_module, zk_root, monkeypatch):
    w = _write(zk_module, zk_root, monkeypatch, "no-links", "No Links", "See [x](y.md).")
    result = zk_module.read(w["uuid"], zk_root, resolve_links=False)
    assert result["found"] is True
    assert result["links"] == []