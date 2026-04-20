"""Request correlation helpers (trace id)."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

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

