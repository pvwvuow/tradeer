"""Trace context (spec E3): one session id per run and a trace id that follows a signal.

The context lives in `contextvars`, so it follows the current thread or asyncio task.
Work handed to another thread must carry it explicitly with `propagate()`.
"""

from __future__ import annotations

import contextvars
import functools
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

SESSION_ID = str(uuid.uuid4())

CONTEXT_FIELDS: tuple[str, ...] = ("signal_id", "trade_id", "ticket", "symbol", "strategy")

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)
_fields: contextvars.ContextVar[Mapping[str, str] | None] = contextvars.ContextVar(
    "trace_fields",
    default=None,
)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def current_trace_id() -> str | None:
    return _trace_id.get()


def current_fields() -> dict[str, str]:
    return dict(_fields.get() or {})


def snapshot() -> dict[str, str]:
    """The active context as flat log fields: session_id, trace_id, signal_id, ..."""
    data = {"session_id": SESSION_ID}
    trace_id = _trace_id.get()
    if trace_id is not None:
        data["trace_id"] = trace_id
    data.update(_fields.get() or {})
    return data


@contextmanager
def trace(trace_id: str | None = None, **fields: object) -> Iterator[str]:
    """Run a block inside a trace.

    Without `trace_id` the current trace continues (or a new one starts), so nested steps of
    one signal share its id. Fields such as `symbol` or `trade_id` are added for the block.
    """
    unknown = sorted(set(fields) - set(CONTEXT_FIELDS))
    if unknown:
        raise ValueError(f"unknown trace fields: {', '.join(unknown)}")
    active = trace_id or _trace_id.get() or new_trace_id()
    added = {name: str(value) for name, value in fields.items() if value is not None}
    trace_token = _trace_id.set(active)
    fields_token = _fields.set({**(_fields.get() or {}), **added})
    try:
        yield active
    finally:
        _fields.reset(fields_token)
        _trace_id.reset(trace_token)


def propagate(function: Callable[P, R]) -> Callable[P, R]:
    """Wrap `function` so that it runs with the caller's current context in any thread."""
    context = contextvars.copy_context()

    @functools.wraps(function)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        # A Context can be entered by one thread at a time, so every call gets a copy.
        return context.copy().run(function, *args, **kwargs)

    return run
