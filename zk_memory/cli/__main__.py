"""zk-memory — CLI over the corpus operations and (with an LLM) the judgment
motions.

Enough to use the library without Hermes. ``--root`` is required (or set
``ZK_MEMORY_ROOT``). The mechanical corpus ops (search/read/write/merge/tend/
list) work with no LLM. The judgment commands (retain, tend-writes, split,
integrate) need an ``StructuredLLM``, built from an OpenAI-compatible chat
endpoint via ``--llm-model/--llm-base/--llm-key`` (or ``ZK_MEMORY_LLM_*`` /
``OMNIROUTE_*`` env).

House rule: every function and its configuration is reachable through CLI
arguments — no functionality is library-only.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from zk_memory import list_notes, merge, read, search, tend, write
from zk_memory.corpus import TEND_ACTIONS


def _resolve_root(args) -> Path:
    root = args.root or os.environ.get("ZK_MEMORY_ROOT")
    if not root:
        sys.exit("error: --root required (or set ZK_MEMORY_ROOT)")
    return Path(root)


def _make_llm(args):
    """Build a StructuredLLM from CLI args/env; exits with a clear error if
    an LLM command runs without one configured."""
    from zk_memory.cli.llm import CliLLM, resolve_chat_args

    cfg = resolve_chat_args(args)
    if cfg is None:
        sys.exit(
            "error: this command needs an LLM — pass --llm-model/--llm-base/--llm-key "
            "(or set ZK_MEMORY_LLM_MODEL/BASE/KEY / OMNIROUTE_BASE/API_KEY)"
        )
    return CliLLM(**cfg)


def _make_memory(args):
    """Build a Memory (root-bound) with an optional LLM + index backend."""
    from zk_memory import Memory

    llm = None
    from zk_memory.cli.llm import resolve_chat_args
    cfg = resolve_chat_args(args)
    if cfg is not None:
        from zk_memory.cli.llm import CliLLM
        llm = CliLLM(**cfg)
    backend = getattr(args, "backend", None) or os.environ.get("ZK_MEMORY_BACKEND")
    return Memory(root=_resolve_root(args), llm=llm, backend=backend or "auto")


def _cmd_search(args) -> int:
    hits = search(args.query, _resolve_root(args), limit=args.limit, backend=args.backend)
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
    result = read(args.ref, _resolve_root(args))
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
    result = write(args.slug, args.title, args.body, _resolve_root(args))
    if result.get("ok"):
        print(f"zettel written: {result['path']}  uuid={result.get('uuid', '')}")
        return 0
    print(f"error: {result.get('err', 'write failed')}")
    return 1


def _cmd_merge(args) -> int:
    result = merge(args.ref, args.fragment, _resolve_root(args))
    if result.get("ok"):
        print(f"merged into: {result['path']}")
        return 0
    print(f"error: {result.get('err', 'merge failed')}")
    return 1


def _cmd_tend(args) -> int:
    if args.action not in TEND_ACTIONS:
        print(f"error: action must be one of {', '.join(TEND_ACTIONS)}")
        return 2
    extra = ("--write",) if args.write else ()
    result = tend(args.action, _resolve_root(args), *extra)
    head = "ok" if result.get("ok") else "FAILED"
    out = result.get("output") or result.get("err") or ""
    print(f"zk_tend {args.action}: {head}")
    print(out.strip()[:1000])
    return 0 if result.get("ok") else 1


def _cmd_retain(args) -> int:
    """Retain one turn (distill -> merge|create) using the configured LLM."""
    m = _make_memory(args)
    labels = m.retain_turn(args.user, args.assistant, source=args.source)
    if not labels:
        print("retained: nothing")
        return 0
    print("retained:")
    for label in labels:
        print(f"- {label}")
    return 0


def _cmd_tend_writes(args) -> int:
    m = _make_memory(args)
    results = m.tend_writes(limit=args.limit, split_sweep=args.split_sweep, source=args.source)
    if not results:
        print("tend-writes: no candidates / no LLM")
        return 0
    for r in results:
        action = r.get("action")
        if action == "merged":
            print(f"- {r.get('slug')}: MERGED into {r.get('target')}")
        elif action == "linked":
            print(f"- {r.get('slug')}: linked -> {len(r.get('links', []))} note(s)")
        elif action == "split":
            print(f"- {r.get('slug')}: SPLIT -> parent {r.get('parent')}, {len(r.get('children', []))} child(ren)")
        else:
            print(f"- {r.get('slug')}: {action}")
    return 0


def _cmd_split(args) -> int:
    """De-merge one note into a summary parent + atomic children (Z12)."""
    m = _make_memory(args)
    out = m.split_note(args.ref, source=args.source, max_fragments=args.max_fragments)
    action = out.get("action")
    if action == "split":
        print(f"split: parent={out.get('parent')} children={len(out.get('children', []))}")
        return 0
    if action == "not_split":
        print(f"not split: {out.get('ref')} (judge declined or missing)")
        return 0
    print(f"error: {out.get('err', 'split failed')}")
    return 1


def _cmd_integrate(args) -> int:
    """The careful write: merge into the right existing note, else create."""
    m = _make_memory(args)
    out = m.integrate(
        args.content,
        topic=args.topic,
        kind=args.kind,
        title=args.title,
        slug=args.slug,
        choice=args.choice,
        rationale=args.rationale,
        source=args.source,
    )
    action = out.get("action")
    if action == "merged":
        print(f"merged into: {out.get('target')}")
        return 0
    if action == "created":
        print(f"created: {out.get('path')}  uuid={out.get('uuid', '')}")
        return 0
    print(f"error: {out.get('err', 'integrate failed')}")
    return 1


def _cmd_split_candidates(args) -> int:
    from zk_memory.tend import split_candidates
    cands = split_candidates(_resolve_root(args), top=args.top)
    if not cands:
        print("(no split candidates)")
        return 0
    for c in cands:
        print(f"{c['slug']:40} {c['size']:>8} bytes  [ref: {c['ref']}]")
    return 0


def _cmd_list(args) -> int:
    notes = list_notes(_resolve_root(args))
    if not notes:
        print("(empty corpus)")
        return 0
    for n in notes:
        print(f"{n['slug']:40} {n['title']}  [{n['uuid']}]")
    return 0


def _add_llm_args(parser, *, require=False) -> None:
    """Add shared LLM endpoint args. When ``require`` is set the command
    needs an LLM (its handler exits if none resolves)."""
    parser.add_argument("--llm-model", default=None, help="Chat model id for LLM-backed commands.")
    parser.add_argument("--llm-base", default=None, help="Chat endpoint base URL (or OMNIROUTE_BASE).")
    parser.add_argument("--llm-key", default=None, help="Chat API key (or OMNIROUTE_API_KEY).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zk-memory", description="Zettelkasten memory operations.")
    parser.add_argument("--root", help="Corpus directory (or set ZK_MEMORY_ROOT).")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="Full-text search the corpus.")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--backend", default=None,
                   help="Recall engine: auto|rg|fts (or a registered name). "
                        "Defaults to $ZK_MEMORY_BACKEND, else auto.")
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

    p = sub.add_parser(
        "tend",
        help="Run a linlink maintenance action (repair/check/mint/robustify).",
    )
    p.add_argument("action")
    p.add_argument(
        "--write",
        action="store_true",
        help="Apply mint/repair/robustify (default is a dry run).",
    )
    p.set_defaults(func=_cmd_tend)

    p = sub.add_parser("retain", help="Retain one turn (distill -> merge|create). Requires an LLM.")
    p.add_argument("user", help="The user turn text.")
    p.add_argument("assistant", help="The assistant turn text.")
    p.add_argument("--source", default=None, help="Host/agent attribution.")
    _add_llm_args(p)
    p.set_defaults(func=_cmd_retain)

    p = sub.add_parser("integrate", help="Careful write: merge into the right note, else create. Requires an LLM.")
    p.add_argument("content", help="The atomic memory content.")
    p.add_argument("--topic", required=True, help="Topic for the merge search.")
    p.add_argument("--kind", default="concept", choices=["concept", "entity_update", "decision"])
    p.add_argument("--title", default=None, help="Title if created.")
    p.add_argument("--slug", default=None, help="Slug if created.")
    p.add_argument("--choice", default=None, help="For kind=decision: the choice.")
    p.add_argument("--rationale", default=None, help="For kind=decision: the rationale.")
    p.add_argument("--source", default=None, help="Host/agent attribution.")
    _add_llm_args(p)
    p.set_defaults(func=_cmd_integrate)

    p = sub.add_parser("tend-writes", help="Gardener pass: reconcile recent writes (merge/link/split). Requires an LLM.")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--split-sweep", type=int, default=0,
                   help="Split the top N mechanical-sweep candidates (Z12).")
    p.add_argument("--source", default=None, help="Host/agent attribution.")
    _add_llm_args(p)
    p.set_defaults(func=_cmd_tend_writes)

    p = sub.add_parser("split", help="De-merge one biography note into a parent + atomic children (Z12). Requires an LLM.")
    p.add_argument("ref", help="The note to split (uuid / slug / path).")
    p.add_argument("--max-fragments", type=int, default=4)
    p.add_argument("--source", default=None, help="Host/agent attribution.")
    _add_llm_args(p)
    p.set_defaults(func=_cmd_split)

    p = sub.add_parser("split-candidates", help="List notes that need splitting (mechanical sweep, no LLM).")
    p.add_argument("--top", type=int, default=10)
    p.set_defaults(func=_cmd_split_candidates)

    p = sub.add_parser("list", help="List every note in the corpus.")
    p.set_defaults(func=_cmd_list)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())