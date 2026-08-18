"""zk_memory.corpus — zettelkasten corpus operations.

A flat tree of atomic Markdown notes (one thought per note, own words,
plain-markdown links, YAML frontmatter with a uuid). Four operations:

  - search  — full-text recall over the corpus (LanceDB FTS, fallback rg)
  - read    — read one note by uuid / slug / path, resolve its links
  - write   — author a new zettel (uuid minted via linlink; atomic note)
  - tend    — maintenance: linlink repair, integrity check, mint

These same four operations back both the volitional tool surface and the
automatic recall/retain motions — "auto" is an optional convenience over
the same corpus operations, not a separate code path.

Every function takes an explicit ``root`` (the corpus directory) — this
module has no notion of a default location; the caller resolves ``root``
once and passes it through.
"""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# List — every note as {uuid, title, slug, path, date}
# ---------------------------------------------------------------------------

def list_notes(root: Path) -> list[dict[str, Any]]:
    """Return every note as {uuid, title, slug, path, date}.

    Flat tree of Markdown files with optional YAML frontmatter (uuid/title/
    date). Survives notes without frontmatter.
    """
    notes: list[dict[str, Any]] = []
    if not root.is_dir():
        return notes
    for f in sorted(root.glob("*.md")):
        raw = f.read_text(encoding="utf-8", errors="replace")
        fm: dict[str, str] = {}
        body = raw
        m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    fm[k.strip()] = v.strip()
            body = raw[m.end():]
        notes.append({
            "uuid": fm.get("uuid", ""),
            "title": fm.get("title", f.stem),
            "date": fm.get("date", ""),
            "slug": f.stem,
            "path": f.name,
            "body": body,
        })
    return notes


# ---------------------------------------------------------------------------
# Search — full-text recall over the corpus
# ---------------------------------------------------------------------------

def search(
    query: str,
    root: Path,
    *,
    limit: int = 8,
    rebuild_index: bool = False,
) -> list[dict[str, Any]]:
    """Full-text search the corpus; returns ranked note hits.

    Primary path: LanceDB FTS (lazy import; a rich ranked recall). Falls
    back to ripgrep -l when lancedb is unavailable or no index exists
    (ranked by term density) so recall never hard-fails.
    """
    if not root.is_dir():
        return []

    try:
        from zk_memory.fts import run_fts
        return run_fts(root, query, limit=limit, rebuild=rebuild_index)
    except ImportError:
        pass
    return _search_rg(root, query, limit)


def _search_rg(root: Path, query: str, limit: int) -> list[dict[str, Any]]:
    """Ripgrep fallback: -l files, ranked by hit count / query terms."""
    rg = shutil.which("rg")
    if not rg:
        return []
    try:
        proc = subprocess.run(
            [rg, "-l", "-i", "--", query, str(root)],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    files = [Path(l) for l in proc.stdout.splitlines() if l]
    hits: list[dict[str, Any]] = []
    for f in files[:limit]:
        note = read_note_meta(f)
        if note:
            body = (f.read_text(errors="replace") or "").lower()
            note["score"] = body.count(query.lower())
            hits.append(note)
    hits.sort(key=lambda n: n.get("score", 0), reverse=True)
    return hits


# ---------------------------------------------------------------------------
# Read — one note, links resolved
# ---------------------------------------------------------------------------

def read_note_meta(path: Path) -> Optional[dict[str, Any]]:
    """Read a note's {uuid,title,slug,path,date} from a path on disk."""
    slug = path.stem
    raw = path.read_text(encoding="utf-8", errors="replace")
    fm: dict[str, str] = {}
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip()
    return {
        "uuid": fm.get("uuid", ""),
        "title": fm.get("title", slug),
        "slug": slug,
        "path": path.name,
        "date": fm.get("date", ""),
    }


def find_note(ref: str, root: Path) -> Optional[dict[str, Any]]:
    """Find a note by uuid, slug (filename stem), or path name.

    The canonical reference is the uuid (linlink-anchored); slug and path
    are convenience lookups.
    """
    if not root.is_dir():
        return None
    for note in list_notes(root):
        if note["uuid"] and note["uuid"] == ref:
            return note
    for note in list_notes(root):
        if note["slug"] == ref or note["path"] == ref:
            return note
    return None


def read(ref: str, root: Path, *, resolve_links: bool = True) -> dict[str, Any]:
    """Read one note in full; optionally resolve its out-links.

    Returns {found, note, links:[{ref,title,resolved}]} — never raises on
    a missing note; found=False with links=[].
    """
    note = find_note(ref, root)
    if not note:
        return {"found": False, "note": {}, "links": []}
    fpath = root / note["path"]
    note["body"] = fpath.read_text(encoding="utf-8", errors="replace")
    links: list[dict[str, Any]] = []
    if resolve_links:
        # Plain-markdown links [label](slug.md) — the zettelkasten form.
        for m in re.finditer(r"\[([^\]]*)\]\(([^)]+?\.md)\)", note["body"]):
            target = Path(m.group(2)).stem
            meta = find_note(target, root)
            links.append({
                "ref": target,
                "label": m.group(1),
                "resolved": bool(meta),
                "title": (meta or {}).get("title", ""),
            })
    return {"found": True, "note": note, "links": links}


# ---------------------------------------------------------------------------
# Write — author a new zettel
# ---------------------------------------------------------------------------

def write(slug: str, title: str, body: str, root: Path) -> dict[str, Any]:
    """Author a new atomic note into the corpus.

    Discipline: flat, YYYYMMDD-slug.md; uuid minted via linlink (never
    hand-written); plain-markdown links; one thought.

    Writes the note WITHOUT uuid frontmatter, then runs ``linlink mint`` so
    the uuid is minted canonically. Returns {ok, path, uuid?, err}.
    """
    import datetime as _dt

    root.mkdir(parents=True, exist_ok=True)

    safe_slug = re.sub(r"[^a-z0-9-]", "-", slug.lower()).strip("-")
    if not safe_slug:
        return {"ok": False, "path": "", "err": "invalid slug"}
    today = _dt.date.today().strftime("%Y%m%d")
    fname = f"{today}-{safe_slug}.md"
    fpath = root / fname
    if fpath.exists():
        return {"ok": False, "path": str(fpath), "err": f"note already exists: {fname}"}

    content_txt = f"# {title}\n\n{body.strip()}\n"
    fpath.write_text(content_txt, encoding="utf-8")

    linlink = shutil.which("linlink")
    uuid = ""
    if linlink:
        try:
            subprocess.run(
                [linlink, "mint", str(root), "--write"],
                capture_output=True, text=True, timeout=30,
            )
            raw = fpath.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"uuid:\s*([0-9a-f-]+)", raw)
            if m:
                uuid = m.group(1)
        except (subprocess.TimeoutExpired, OSError):
            pass
    if not uuid:
        # Fallback: no linlink — embed our own uuid (not ideal; the
        # authoring discipline insists linlink mints, but write must
        # never hard-fail).
        import uuid as _uuid
        uuid = str(_uuid.uuid4())
        front = f"---\nuuid: {uuid}\ntitle: {title}\ndate: {_dt.date.today().isoformat()}\n---\n\n"
        fpath.write_text(front + content_txt, encoding="utf-8")

    return {"ok": True, "path": str(fpath), "uuid": uuid}


# ---------------------------------------------------------------------------
# Merge — append a fragment to an EXISTING note (never rewrite/replace)
# ---------------------------------------------------------------------------

def merge(ref: str, fragment: str, root: Path) -> dict[str, Any]:
    """Append a dated fragment to an existing note.

    This is the one true read-modify-write on the corpus, and the one
    place a lock matters: ``write()`` is collision-safe by refusing to
    overwrite (no read-before-write), but appending to an existing file
    needs to keep two concurrent callers (two sync_turn threads, or a
    volitional zk_write racing an automatic retain) from interleaving.
    A corpus-wide flock is coarser than a per-note lock but simpler and
    plenty for this call frequency.

    Append-only by design: never rewrites existing prose, so a bad
    merge can at worst add a wrong fragment, never destroy content.
    Returns {ok, path, err}.
    """
    import datetime as _dt

    note = find_note(ref, root)
    if not note:
        return {"ok": False, "path": "", "err": f"note not found: {ref}"}
    fpath = root / note["path"]

    lock_path = root / ".zk.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        today = _dt.date.today().isoformat()
        addition = f"\n\n---\n*{today}:* {fragment.strip()}\n"
        try:
            with open(fpath, "a", encoding="utf-8") as f:
                f.write(addition)
        except OSError as e:
            return {"ok": False, "path": str(fpath), "err": str(e)}
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return {"ok": True, "path": str(fpath)}


# ---------------------------------------------------------------------------
# Tend — maintenance operations
# ---------------------------------------------------------------------------

def tend(action: str, root: Path, *args: str) -> dict[str, Any]:
    """Run a linlink maintenance action on the corpus.

    action in {"repair", "check", "mint"} — maps to the linlink CLI. Never
    raises; returns {ok, output, err}.
    """
    linlink = shutil.which("linlink")
    if not linlink:
        return {"ok": False, "output": "", "err": "linlink not on PATH"}
    cmd = [linlink, action, *args, "--", str(root)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return {"ok": proc.returncode == 0, "output": proc.stdout, "err": proc.stderr}
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"ok": False, "output": "", "err": str(e)}