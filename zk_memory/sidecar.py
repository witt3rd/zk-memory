"""The corpus state sidecar — every non-note artifact the library plants.

When someone points at a directory and says "this is where my zk corpus
lives", the library must keep ALL of its state under that one directory —
never scatter derived/diagnostic files beside it (a parent-directory leak
that breaks the "adopt = point me at the dir" contract on shared volumes).

Footprint:

    <root>/
      *.md               # the notes themselves
      .zk/               # the planted sidecar (library state)
        lock             # merge flock
        index/           # LanceDB FTS (derived, rebuildable)
        trace.jsonl       # diagnostic trace
      .archive/           # retired notes (reversible, never deleted)

This module is the single source of truth for sidecar paths, so no other
module hardcodes a `root.parent` or a dotfile name.
"""

from __future__ import annotations

from pathlib import Path

SIDECAR_DIRNAME = ".zk"
LOCK_FILENAME = "lock"
INDEX_DIRNAME = "index"
TRACE_FILENAME = "trace.jsonl"


def sidecar_dir(root: Path) -> Path:
    """The state sidecar directory inside the corpus root."""
    return Path(root) / SIDECAR_DIRNAME


def lock_path(root: Path) -> Path:
    return sidecar_dir(root) / LOCK_FILENAME


def index_dir(root: Path) -> Path:
    return sidecar_dir(root) / INDEX_DIRNAME


def trace_path(root: Path) -> Path:
    return sidecar_dir(root) / TRACE_FILENAME