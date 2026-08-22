"""probe.trace() -- diagnostic visibility, both logged and JSONL-traced."""

from __future__ import annotations

import json

from zk_memory import probe


def test_trace_with_no_root_only_logs(caplog):
    with caplog.at_level("INFO"):
        probe.trace("some_event", foo="bar")
    assert any("some_event" in r.message for r in caplog.records)


def test_trace_with_root_writes_jsonl_in_sidecar(tmp_path):
    root = tmp_path / "zk"
    probe.trace("initialized", root, session_id="abc123")

    trace_file = tmp_path / "zk" / ".zk" / "trace.jsonl"
    assert trace_file.is_file()
    line = json.loads(trace_file.read_text().strip())
    assert line["event"] == "initialized"
    assert line["session_id"] == "abc123"
    assert "ts" in line


def test_trace_appends_multiple_events(tmp_path):
    root = tmp_path / "zk"
    probe.trace("event_one", root)
    probe.trace("event_two", root, n=2)

    trace_file = tmp_path / "zk" / ".zk" / "trace.jsonl"
    lines = [json.loads(l) for l in trace_file.read_text().splitlines()]
    assert [l["event"] for l in lines] == ["event_one", "event_two"]
    assert lines[1]["n"] == 2


def test_trace_never_raises_on_unserializable_field(tmp_path):
    root = tmp_path / "zk"

    class Unserializable:
        def __str__(self):
            return "<unserializable>"

    # default=str in json.dumps should handle this rather than raising.
    probe.trace("weird_event", root, thing=Unserializable())
    trace_file = tmp_path / "zk" / ".zk" / "trace.jsonl"
    line = json.loads(trace_file.read_text().strip())
    assert line["thing"] == "<unserializable>"


def test_trace_never_raises_when_root_parent_is_unwritable(tmp_path, monkeypatch):
    root = tmp_path / "zk"

    def _boom(*a, **kw):
        raise OSError("disk is full")

    monkeypatch.setattr(probe.Path, "mkdir", _boom)
    # Must not raise even though the file write path is broken.
    probe.trace("event", root)