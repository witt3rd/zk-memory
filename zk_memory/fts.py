"""LanceDB FTS search over a zettelkasten corpus.

The optional rich-recall backend: builds a full-text index over the flat
corpus of Markdown notes and returns ranked hits. Used by
``zk_memory.corpus.search`` when lancedb is available; otherwise recall
degrades to ripgrep. The index is a derived cache owned by the caller,
stored beside the corpus.

Uses the official ``lancedb`` PyPI package (not a fork).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

TEXT_FIELD = "text"


def _corpus_notes(root: Path) -> list[dict[str, Any]]:
    notes = []
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
            "path": f.name,
            "slug": f.stem,
            TEXT_FIELD: body,
        })
    return notes


def run_fts(
    root: Path,
    query: str,
    *,
    limit: int = 8,
    rebuild: bool = False,
) -> list[dict[str, Any]]:
    """Search the corpus with LanceDB full-text search.

    Lazily builds/opens the FTS index. Returns ranked note dicts with a
    ``score``; never raises on missing corpus (returns []).
    """
    if not root.is_dir():
        return []
    import lancedb
    import pyarrow as pa
    from lancedb.index import FTS

    index_dir = root.parent / ".zk-index"
    index_dir.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(index_dir))
    table_name = "notes"

    schema = pa.schema([
        pa.field("uuid", pa.string()),
        pa.field("title", pa.string()),
        pa.field("date", pa.string()),
        pa.field("path", pa.string()),
        pa.field("slug", pa.string()),
        pa.field(TEXT_FIELD, pa.string()),
    ])

    needs_rebuild = rebuild or not _table_exists(db, table_name)
    if needs_rebuild:
        notes = _corpus_notes(root)
        if not notes:
            return []
        table = pa.Table.from_pylist(notes, schema=schema)
        db.drop_table(table_name, ignore_missing=True)
        t = db.create_table(table_name, table, mode="overwrite")
        t.create_index(TEXT_FIELD, config=FTS())

    t = db.open_table(table_name)
    res = (
        t.search(query, query_type="fts")
        .select(["_score", "title", "path", "slug", "uuid", TEXT_FIELD])
        .limit(limit)
        .to_arrow()
    )
    cols = {name: res.column(name).to_pylist() for name in res.column_names}
    out = []
    for i in range(res.num_rows):
        out.append({
            "uuid": cols.get("uuid", [""] * res.num_rows)[i],
            "title": cols.get("title", [""] * res.num_rows)[i],
            "slug": cols.get("slug", [""] * res.num_rows)[i],
            "path": cols.get("path", [""] * res.num_rows)[i],
            "score": cols.get("_score", [0] * res.num_rows)[i],
            "snippet": _snippet(cols.get(TEXT_FIELD, [""] * res.num_rows)[i], query),
        })
    return out


def _table_exists(db: Any, name: str) -> bool:
    try:
        db.open_table(name)
        return True
    except Exception:
        return False


def _snippet(text: str, query: str, radius: int = 160) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    terms = [w for w in re.split(r"\W+", query.lower()) if len(w) > 2]
    if terms:
        low = text.lower()
        pos = min((low.find(t) for t in terms if t in low), default=-1)
        if pos >= 0:
            return text[max(0, pos - 60): pos + radius]
    return text[: radius * 2]