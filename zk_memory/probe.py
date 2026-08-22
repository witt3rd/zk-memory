"""probe — lightweight diagnostic tracing for a zettelkasten memory.

Every retain/recall decision the library makes is otherwise invisible:
the retain pipeline only logs on FAILURE (``logger.warning``), so there is
no way to see "did retain actually fire", "what did the distiller
decide", or "did it merge or create" short of watching the corpus
directory for file changes. ``trace()`` is the fix: one call site, used
on every success-path decision, that both logs at INFO (so it shows up
in whatever the hosting process already captures) and appends a
structured JSONL line inside the corpus's state sidecar for offline
inspection —

    <corpus-root>/.zk/trace.jsonl

Inside the sidecar, not beside the corpus — the library keeps ALL of its
state under the one directory you pointed at (see ``zk_memory.sidecar``);
it never leaks derived/diagnostic files into the parent. Best-effort by
design: a trace failure must never break the retain/recall it's describing.
``root=None`` (e.g. called before the corpus is resolved) skips the file
write and only logs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _trace_path(root: Path) -> Path:
    from zk_memory.sidecar import trace_path
    return trace_path(root)


def trace(event: str, root: Optional[Path] = None, **fields: Any) -> None:
    """Record one diagnostic event. Never raises.

    Always logs at INFO (``zk-memory trace: <event> <fields>``). If
    ``root`` (the corpus root, e.g. ``Memory._root``) is given, also
    best-effort appends one JSON line to the trace file beside it.
    """
    try:
        logger.info("zk-memory trace: %s %s", event, fields)
    except Exception:
        pass

    if root is None:
        return
    try:
        line = json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "event": event,
                **fields,
            },
            ensure_ascii=False,
            default=str,
        )
        path = _trace_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        logger.debug("zk-memory: trace file write failed for event %s", event, exc_info=True)