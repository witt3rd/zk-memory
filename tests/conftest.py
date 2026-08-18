"""Shared fixtures for zk-memory tests.

``zk_module`` points at ``zk_memory.corpus`` (the module under test for
the corpus-operation tests). Corpus ops take an explicit ``root``; the
``zk_root`` fixture provides a throwaway corpus dir per test.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def zk_module():
    import zk_memory.corpus as corpus
    return corpus


@pytest.fixture(scope="session")
def judge_module():
    import zk_memory.judge as judge
    return judge


@pytest.fixture
def zk_root(tmp_path):
    """A throwaway corpus dir (not pre-created; write()/etc create it
    lazily, matching real usage)."""
    return tmp_path / "zk"


@pytest.fixture(autouse=True)
def _no_linlink_by_default(monkeypatch, zk_module):
    """Force corpus.write()/tend() down their no-linlink fallback path by
    default, regardless of whether linlink happens to be installed on the
    machine running the tests. Tests that specifically want to exercise
    the "linlink present" branch install their own fake linlink and
    monkeypatch zk_module.shutil.which again inside the test body, which
    takes precedence over this default (both use the same per-test
    monkeypatch fixture, and the later call wins)."""
    # zk_module.shutil IS the real (shared) shutil module -- capture the
    # original `which` before patching it, since patching in place would
    # otherwise make the "real" fallback call itself recursively.
    _real_which = zk_module.shutil.which

    def _which_no_linlink(name):
        if name == "linlink":
            return None
        return _real_which(name)

    monkeypatch.setattr(zk_module.shutil, "which", _which_no_linlink)