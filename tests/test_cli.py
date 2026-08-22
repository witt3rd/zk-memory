"""CLI surface — mechanical ops + LLM-backed commands.

Tests invoke ``zk_memory.cli.__main__.main(argv)`` with ``ZK_MEMORY_ROOT``
pointed at a temp corpus. LLM-backed commands are tested by stubbing the
``CliLLM`` / the chat transport — never a real endpoint.
"""

from __future__ import annotations

import os
import sys

import pytest

from zk_memory import Memory

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture
def root(tmp_path, monkeypatch):
    corpus = tmp_path / "zk"
    monkeypatch.setenv("ZK_MEMORY_ROOT", str(corpus))
    return corpus


@pytest.fixture
def llm_env(monkeypatch):
    """Point the CLI's LLM resolution at a fake endpoint via env."""
    monkeypatch.setenv("ZK_MEMORY_LLM_MODEL", "m/model")
    monkeypatch.setenv("ZK_MEMORY_LLM_BASE", "http://fake")
    monkeypatch.setenv("ZK_MEMORY_LLM_KEY", "k")


def _run(argv, capsys):
    from zk_memory.cli import __main__ as cli
    rc = cli.main(argv)
    return rc, capsys.readouterr().out


def _stub_cli_llm(monkeypatch, result):
    """Make the CLI's CliLLM return a fixed StructuredLLM result."""
    from zk_memory.cli import llm as cli_llm

    class _Stub:
        def __call__(self, messages, *, schema, name):
            return result

    monkeypatch.setattr(cli_llm, "CliLLM", lambda **kw: _Stub())


def test_search(root, capsys):
    Memory(root=root).write("hello", "Hello", "hello world body")
    rc, out = _cli_main(["search", "hello"], capsys)
    assert rc == 0
    assert "hello" in out


def test_list_empty(root, capsys):
    rc, out = _cli_main(["list"], capsys)
    assert rc == 0
    assert "empty" in out


def test_write_then_read(root, capsys):
    from zk_memory import Memory
    rc, out = _cli_main(["write", "hello", "Hello", "hello world"], capsys)
    assert rc == 0
    assert "written" in out
    notes = Memory(root=root).list_notes()
    assert len(notes) == 1
    uuid = notes[0]["uuid"]
    rc, out = _cli_main(["read", uuid], capsys)
    assert rc == 0
    assert "hello world" in out


def test_merge(root, capsys):
    seed = Memory(root=root).write("judy", "Judy", "Judy is a colleague.")
    rc, out = _cli_main(["merge", seed["uuid"], "Judy arrives soon."], capsys)
    assert rc == 0
    body = Memory(root=root).read(seed["uuid"])["note"]["body"]
    assert "Judy arrives soon." in body


def test_split_candidates(root, capsys):
    Memory(root=root).write("small", "Small", "tiny")
    Memory(root=root).write("big", "Big", "x" * 5000)
    rc, out = _cli_main(["split-candidates"], capsys)
    assert rc == 0
    assert "big" in out  # largest surfaced


def test_retain_requires_llm(root, capsys):
    # no LLM env -> retain no-ops gracefully (library contract), not an error
    rc, out = _cli_main(["retain", "user text", "assistant text"], capsys)
    assert rc == 0
    assert "nothing" in out


def test_integrate_with_stub_llm_creates(root, monkeypatch, capsys):
    from zk_memory import corpus
    # No hits -> integrate creates
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [])
    _stub_cli_llm(monkeypatch, {"worth_retaining": True, "candidates": []})
    rc, out = _cli_main(["integrate", "one idea", "--topic", "t", "--title", "T", "--slug", "t"], capsys)
    assert rc == 0
    assert "created" in out


def test_integrate_merges_with_stub_llm(root, monkeypatch, capsys):
    from zk_memory import corpus
    seed = Memory(root=root).write("judy", "Judy", "Judy is a colleague.")
    monkeypatch.setattr(corpus, "search", lambda q, root, **kw: [{"uuid": seed["uuid"], "slug": "judy"}])
    _stub_cli_llm(monkeypatch, {"action": "merge", "merge_target_ref": seed["uuid"]})
    rc, out = _cli_main(["integrate", "Judy arrives soon.", "--topic", "Judy"], capsys)
    assert rc == 0
    assert "merged" in out
    body = Memory(root=root).read(seed["uuid"])["note"]["body"]
    assert "Judy arrives soon." in body


def test_split_with_stub_llm(root, monkeypatch, capsys):
    seed = Memory(root=root).write(
        "judy", "Judy",
        "Judy moved.\n---\nJudy joined analytics.\n---\nJudy decided blue-green.",
    )
    decision = {
        "split": True,
        "parent_summary": "Judy is a colleague.",
        "fragments": [
            {"kind": "entity_update", "title": "Moved", "slug": "judy-moved", "content": "moved."},
        ],
    }
    _stub_cli_llm(monkeypatch, decision)
    rc, out = _cli_main(["split", seed["uuid"]], capsys)
    assert rc == 0
    assert "split" in out
    assert not (root / seed["path"]).exists()
    assert (root / ".archive").exists()


def _cli_main(argv, capsys=None):
    from zk_memory.cli import __main__ as cli
    rc = cli.main(argv)
    out = capsys.readouterr().out if capsys else ""
    return rc, out


def _stub_cli_llm(monkeypatch, result):
    """Point the CLI's LLM resolution at a fake env + a fixed StructuredLLM
    result."""
    monkeypatch.setenv("ZK_MEMORY_LLM_MODEL", "m/model")
    monkeypatch.setenv("ZK_MEMORY_LLM_BASE", "http://fake")
    monkeypatch.setenv("ZK_MEMORY_LLM_KEY", "k")
    from zk_memory.cli import llm as cli_llm

    class _Stub:
        def __call__(self, messages, *, schema, name):
            return result

    monkeypatch.setattr(cli_llm, "CliLLM", lambda **kw: _Stub())