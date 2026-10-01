"""Per-request memo for configuration-derived answers.

Several authorization predicates (``auth_genuinely_off``,
``non_static_auth_configured``) take no request argument and re-derive their
answer from the configuration files each time. One page render asked them a
dozen times (the shell's nav context, the permission checks and the bearer
middleware each ask again), each costing ~1 ms of config reads. Within one
request the configuration cannot meaningfully change, so a pure-ASGI
middleware opens a memo scope per request and :func:`memoized` serves repeat
questions from it. Outside a request scope (CLI, tests calling the functions
directly, background threads) nothing is cached.
"""
from __future__ import annotations

import contextvars
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

_SCOPE: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "maverick_dashboard_request_memo", default=None,
)


def memoized(key: str, compute: Callable[[], T]) -> T:
    """``compute()`` once per request scope for ``key``; uncached outside one."""
    memo = _SCOPE.get()
    if memo is None:
        return compute()
    try:
        return memo[key]
    except KeyError:
        value = memo[key] = compute()
        return value


def in_scope() -> bool:
    return _SCOPE.get() is not None


class RequestMemoMiddleware:
    """Pure ASGI: one fresh memo per HTTP request or WebSocket connection.

    The scope is a context variable, so it follows the request into FastAPI's
    thread pool (``def`` handlers, ``run_in_threadpool``) and is dropped with
    the request; nothing leaks between requests or into other tasks.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        token = _SCOPE.set({})
        try:
            await self.app(scope, receive, send)
        finally:
            _SCOPE.reset(token)
