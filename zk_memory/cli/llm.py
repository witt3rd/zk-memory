"""CLI LLM adapter — a StructuredLLM over an OpenAI-compatible chat endpoint.

The library never imports a provider SDK; the CLI is the one consumer that
needs to build a ``StructuredLLM`` so the judgment functions (retain,
tend-writes, split, integrate) are reachable from the command line. This
adapter talks to any OpenAI-compatible ``/v1/chat/completions`` (OmniRoute
gateway, OpenRouter, local) using the same forced-tool-call shape as the
Hermes adapter — reusing ``zk_memory.judge.TOOL_DESCRIPTIONS`` + schema.

httpx is imported lazily so the CLI still works for corpus-only commands
(search/read/write/merge/tend/list) without it; only the LLM-backed commands
require it.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from zk_memory.judge import TOOL_DESCRIPTIONS

logger = logging.getLogger(__name__)


def resolve_chat_args(args) -> Optional[dict[str, str]]:
    """Resolve chat endpoint config from CLI args, else env.

    Returns ``{"base", "key", "model"}`` or None when not configured. The
    model may also be supplied as a bare positional ``--llm``.
    """
    base = getattr(args, "llm_base", None) or os.environ.get("ZK_MEMORY_LLM_BASE") or \
        os.environ.get("OMNIROUTE_BASE")
    key = getattr(args, "llm_key", None) or os.environ.get("ZK_MEMORY_LLM_KEY") or \
        os.environ.get("OMNIROUTE_API_KEY")
    model = getattr(args, "llm_model", None) or os.environ.get("ZK_MEMORY_LLM_MODEL")
    if not base or not key or not model:
        return None
    return {"base_url": base.rstrip("/"), "api_key": key, "model": model}


class CliLLM:
    """A ``StructuredLLM`` for the CLI. Lazy httpx import."""

    name = "cli"

    def __init__(self, base_url: str, api_key: str, model: str, *, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def __call__(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict,
        name: str,
    ) -> Optional[dict[str, Any]]:
        try:
            import httpx
        except ImportError:
            logger.error("zk-memory: LLM commands need httpx — install `zk-memory[cli-llm]`")
            return None
        system_prompt = ""
        user_text = ""
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "system":
                system_prompt = content
            elif role == "user":
                user_text = content
        tool = {
            "type": "function",
            "function": {
                "name": name,
                "description": TOOL_DESCRIPTIONS.get(name, name),
                "parameters": schema,
            },
        }
        try:
            resp = httpx.post(
                f"{self.base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text},
                    ],
                    "tools": [tool],
                    "tool_choice": "required",
                    "max_tokens": 1500,
                },
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                logger.error("zk-memory: LLM endpoint HTTP %s: %s", resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            message = (data.get("choices") or [{}])[0].get("message", {})
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return None
            args_raw = tool_calls[0].get("function", {}).get("arguments")
            if not args_raw:
                return None
            return json.loads(args_raw)
        except Exception:
            logger.exception("zk-memory: LLM call failed")
            return None