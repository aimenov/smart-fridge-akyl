"""Request correlation and safe logging previews for pipelines and HTTP (VLM) calls."""

from __future__ import annotations

import copy
import json
from contextvars import ContextVar, Token
from typing import Any, Optional

_trace_id: ContextVar[Optional[str]] = ContextVar("pipeline_trace_id", default=None)


def begin_trace(trace_id: str) -> Token:
    return _trace_id.set(trace_id)


def end_trace(token: Token) -> None:
    _trace_id.reset(token)


def current_trace_id() -> Optional[str]:
    return _trace_id.get()


def trace_prefix() -> str:
    tid = current_trace_id()
    return f"[trace={tid}] " if tid else ""


def redact_openai_style_body(body: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy JSON body and replace embedded base64 image data with size hints."""
    data = copy.deepcopy(body)
    messages = data.get("messages")
    if not isinstance(messages, list):
        return data
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url")
                if isinstance(url, str) and url.startswith("data:"):
                    part.setdefault("image_url", {})["url"] = (
                        f"<data-url base64 chars≈{len(url)}>"
                    )
    return data


def json_preview(obj: Any, *, max_chars: int) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except TypeError:
        s = str(obj)
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + f"... [truncated, total_chars={len(s)}]"

