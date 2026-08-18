"""zk-memory — thin CLI over the corpus operations.

Enough to use the library without Hermes. ``--root`` is required (or set
``ZK_MEMORY_ROOT``). The retain motion needs an LLM callable, which v0
does not wire into the CLI — corpus ops only.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from zk_memory import list_notes, merge, read, search, tend, write


def _resolve_root(args) -> Path:
    root = args.root or os.environ.get("ZK_MEMORY_ROOT")
    if not root:
        sys.exit("error: --root required (or set ZK_MEMORY_ROOT)")
    return Path(root)


def _cmd_search(args) -> int:
    root = _resolve_root(args)
    hits = search(args.query, root, limit=args.limit)
    if not hits:
        print(f"no notes found for {args.query!r}")
        return 1
    print(f"Found {len(hits)} note(s) for {args.query!r}:")
    for h in hits:
        title = h.get("title") or h.get("slug", "?")
        snippet = (h.get("snippet") or "").strip()
        print(f"- {title}  ({h.get('path', '')})  [ref: {h.get('uuid') or h.get('slug')}]")
        if snippet:
            print(f"    {snippet[:160]}")
    return 0


def _cmd_read(args) -> int:
    root = _resolve_root(args)
    result = read(args.ref, root)
    if not result["found"]:
        print(f"no note found for ref {args.ref!r}")
        return 1
    note = result["note"]
    print(f"# {note.get('title', note.get('slug'))}  [{note.get('uuid', '')}]  ({note.get('path')})")
    print()
    print(note.get("body", "").strip())
    if result["links"]:
        print()
        print("links:")
        for l in result["links"]:
            mark = "->" if l["resolved"] else "x"
            print(f"  {mark} {l['label']} -> {l['ref']}" + (f" ({l['title']})" if l["resolved"] else " (missing)"))
    return 0


def _cmd_write(args) -> int:
    root = _resolve_root(args)
    result = write(args.slug, args.title, args.body, root)
    if result.get("ok"):
        print(f"zettel written: {result['path']}  uuid={result.get('uuid', '')}")
        return 0
    print(f"error: {result.get('err', 'write failed')}")
    return 1


def _cmd_merge(args) -> int:
    root = _resolve_root(args)
    result = merge(args.ref, args.fragment, root)
    if result.get("ok"):
        print(f"merged into: {result['path']}")
        return 0
    print(f"error: {result.get('err', 'merge failed')}")
    return 1


def _cmd_tend(args) -> int:
    root = _resolve_root(args)
    if args.action not in ("repair", "check", "mint"):
        print("error: action must be one of repair, check, mint")
        return 2
    result = tend(args.action, root)
    head = "ok" if result.get("ok") else "FAILED"
    out = result.get("output") or result.get("err") or ""
    print(f"zk_tend {args.action}: {head}")
    print(out.strip()[:1000])
    return 0 if result.get("ok") else 1


def _cmd_list(args) -> int:
    root = _resolve_root(args)
    notes = list_notes(root)
    if not notes:
        print("(empty corpus)")
        return 0
    for n in notes:
        print(f"{n['slug']:40} {n['title']}  [{n['uuid']}]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zk-memory", description="Zettelkasten corpus operations.")
    parser.add_argument("--root", help="Corpus directory (or set ZK_MEMORY_ROOT).")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="Full-text search the corpus.")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(func=_cmd_search)

    p = sub.add_parser("read", help="Read one note by uuid / slug / path.")
    p.add_argument("ref")
    p.set_defaults(func=_cmd_read)

    p = sub.add_parser("write", help="Author a new zettel.")
    p.add_argument("slug")
    p.add_argument("title")
    p.add_argument("body")
    p.set_defaults(func=_cmd_write)

    p = sub.add_parser("merge", help="Append a fragment to an existing note.")
    p.add_argument("ref")
    p.add_argument("fragment")
    p.set_defaults(func=_cmd_merge)

    p = sub.add_parser("tend", help="Run a linlink maintenance action (repair/check/mint).")
    p.add_argument("action")
    p.set_defaults(func=_cmd_tend)

    p = sub.add_parser("list", help="List every note in the corpus.")
    p.set_defaults(func=_cmd_list)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())