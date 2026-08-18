"""corpus.merge() -- appending a fragment to an EXISTING note (never rewrite)."""

from __future__ import annotations

import datetime as dt
import threading


def test_merge_appends_fragment_with_date(zk_module, zk_root, monkeypatch):
    write_result = zk_module.write("judy", "Judy", "Judy is a colleague.", zk_root)
    assert write_result["ok"]

    result = zk_module.merge(write_result["uuid"], "Judy is arriving in two weeks.", zk_root)
    assert result["ok"] is True
    assert result["path"] == write_result["path"]

    from pathlib import Path

    text = Path(result["path"]).read_text()
    assert "Judy is a colleague." in text
    assert "Judy is arriving in two weeks." in text
    today = dt.date.today().isoformat()
    assert today in text


def test_merge_never_replaces_existing_content(zk_module, zk_root, monkeypatch):
    write_result = zk_module.write("judy", "Judy", "Original fact one.", zk_root)

    zk_module.merge(write_result["uuid"], "New fact two.", zk_root)
    zk_module.merge(write_result["uuid"], "New fact three.", zk_root)

    from pathlib import Path

    text = Path(write_result["path"]).read_text()
    assert "Original fact one." in text
    assert "New fact two." in text
    assert "New fact three." in text
    # Append-only: content only grows, original title/body untouched.
    assert text.index("Original fact one.") < text.index("New fact two.") < text.index("New fact three.")


def test_merge_by_slug_also_resolves(zk_module, zk_root, monkeypatch):
    """merge() takes uuid/slug/path exactly like find_note -- not just uuid."""
    write_result = zk_module.write("judy", "Judy", "Judy is a colleague.", zk_root)

    from pathlib import Path

    stem = Path(write_result["path"]).stem  # write()'s date-prefixed slug, e.g. 20260814-judy
    result = zk_module.merge(stem, "A new fact.", zk_root)
    assert result["ok"] is True


def test_merge_returns_error_for_unknown_ref(zk_module, zk_root, monkeypatch):
    result = zk_module.merge("does-not-exist", "fragment", zk_root)
    assert result["ok"] is False
    assert "not found" in result["err"]


def test_merge_serializes_concurrent_writers(zk_module, zk_root, monkeypatch):
    """Two threads merging into the same note concurrently must not
    interleave or lose either fragment -- the flock around the append
    must actually serialize them."""
    write_result = zk_module.write("judy", "Judy", "Base fact.", zk_root)
    uuid = write_result["uuid"]

    errors = []

    def _merge_many(label, count):
        try:
            for i in range(count):
                r = zk_module.merge(uuid, f"{label}-{i}", zk_root)
                assert r["ok"]
        except Exception as e:  # pragma: no cover - surfaced via errors list
            errors.append(e)

    threads = [
        threading.Thread(target=_merge_many, args=("a", 20)),
        threading.Thread(target=_merge_many, args=("b", 20)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert not errors
    from pathlib import Path

    text = Path(write_result["path"]).read_text()
    for i in range(20):
        assert f"a-{i}" in text
        assert f"b-{i}" in text