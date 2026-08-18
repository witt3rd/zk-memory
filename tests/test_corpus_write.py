"""corpus.write() -- authoring a new zettel."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path


def test_write_creates_named_file_with_title_and_body(zk_module, zk_root, monkeypatch):
    result = zk_module.write("My Slug", "My Title", "The body text.", zk_root)

    assert result["ok"] is True
    fpath = zk_root / f"{dt.date.today().strftime('%Y%m%d')}-my-slug.md"
    assert result["path"] == str(fpath)
    assert fpath.is_file()
    text = fpath.read_text()
    assert "# My Title" in text
    assert "The body text." in text


def test_write_sanitizes_slug(zk_module, zk_root, monkeypatch):
    result = zk_module.write("Weird Slug!! With Spaces_&*", "T", "B", zk_root)
    assert result["ok"] is True
    fname = Path(result["path"]).stem
    fname = re.sub(r"^\d{8}-", "", fname)
    # only lowercase alnum and hyphens, no leading/trailing hyphen
    assert re.fullmatch(r"[a-z0-9-]+", fname)
    assert not fname.startswith("-") and not fname.endswith("-")


def test_write_refuses_to_overwrite_existing_file(zk_module, zk_root, monkeypatch):
    first = zk_module.write("dupe", "First", "First body", zk_root)
    assert first["ok"] is True

    second = zk_module.write("dupe", "Second", "Second body", zk_root)
    assert second["ok"] is False
    assert "already exists" in second["err"]

    # original content untouched
    text = Path(first["path"]).read_text()
    assert "First body" in text
    assert "Second body" not in text


def test_write_without_linlink_embeds_own_uuid_and_frontmatter(zk_module, zk_root, monkeypatch):
    result = zk_module.write("no-linlink", "No Linlink", "Body without linlink.", zk_root)

    assert result["ok"] is True
    assert result.get("uuid")
    # a real uuid4 hex-dashed string
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        result["uuid"],
    )
    text = Path(result["path"]).read_text()
    assert f"uuid: {result['uuid']}" in text
    assert "title: No Linlink" in text
    assert text.startswith("---\n")


def test_write_invalid_slug_after_sanitization_fails(zk_module, zk_root, monkeypatch):
    # An all-punctuation slug sanitizes down to the empty string.
    result = zk_module.write("!!!---!!!", "T", "B", zk_root)
    assert result["ok"] is False
    assert result["err"] == "invalid slug"


def test_write_with_linlink_present_but_produces_no_uuid_line(zk_module, zk_root, monkeypatch, tmp_path):
    """When linlink IS on PATH, write() shells out to it. We fake a
    linlink stand-in that exits cleanly but doesn't touch the file (no
    uuid: line appears), so write() falls through to its own uuid
    fallback -- exercising the "linlink present but produced nothing
    usable" branch distinctly from the "linlink absent" branch above."""
    fake_linlink = tmp_path / "linlink"
    fake_linlink.write_text("#!/bin/sh\nexit 0\n")
    fake_linlink.chmod(0o755)

    def fake_which(name):
        return str(fake_linlink) if name == "linlink" else None

    monkeypatch.setattr(zk_module.shutil, "which", fake_which)
    result = zk_module.write("with-fake-linlink", "T", "B", zk_root)

    assert result["ok"] is True
    assert result.get("uuid")  # fell back to its own uuid since no uuid: line was written