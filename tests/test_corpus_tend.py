"""corpus.tend() -- linlink-backed maintenance operations."""

from __future__ import annotations


def test_tend_returns_error_when_linlink_absent(zk_module, zk_root, monkeypatch):
    monkeypatch.setattr(zk_module.shutil, "which", lambda name: None)
    result = zk_module.tend("check", zk_root)
    assert result == {"ok": False, "output": "", "err": "linlink not on PATH"}


def test_tend_runs_linlink_and_reports_success(zk_module, zk_root, monkeypatch, tmp_path):
    fake_linlink = tmp_path / "linlink"
    fake_linlink.write_text("#!/bin/sh\necho all good\nexit 0\n")
    fake_linlink.chmod(0o755)
    monkeypatch.setattr(
        zk_module.shutil, "which", lambda name: str(fake_linlink) if name == "linlink" else None
    )

    result = zk_module.tend("check", zk_root)
    assert result["ok"] is True
    assert "all good" in result["output"]


def test_tend_reports_failure_on_nonzero_exit(zk_module, zk_root, monkeypatch, tmp_path):
    fake_linlink = tmp_path / "linlink"
    fake_linlink.write_text("#!/bin/sh\necho boom 1>&2\nexit 1\n")
    fake_linlink.chmod(0o755)
    monkeypatch.setattr(
        zk_module.shutil, "which", lambda name: str(fake_linlink) if name == "linlink" else None
    )

    result = zk_module.tend("repair", zk_root)
    assert result["ok"] is False
    assert "boom" in result["err"]