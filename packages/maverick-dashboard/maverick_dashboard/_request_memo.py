"""Per-request memo for configuration-derived answers.

Several authorization predicates (``auth_genuinely_off``,
``non_static_auth_configured``) take no request argument and re-derive their
answer from the configuration files each time. One page render asked them a
dozen times (the shell's nav context and bearer middleware ask again), each
costing ~1 ms of config reads. A pure-ASGI middleware opens a memo scope per
request and :func:`memoized` serves repeat display/configuration questions.
An authorization boundary uses :func:`fresh_configuration` because policy may
change while a request waits. Retained background workers explicitly discard
their copied HTTP memo. Outside a request scope nothing is cached.

An HTTP request also reuses the at-rest key that opens secrets saved from
Settings (``maverick.config.at_rest_key_scope``) while the key files are
unchanged. Every authorization boundary resolves it again, and the scope closes
when the response is sent; a retained worker discards it with the memo.
"""
from __future__ import annotations

import contextvars
from collections.abc import Callable
from contextlib import contextmanager, nullcontext
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


def discard_request_memo() -> None:
    """Remove a copied HTTP memo from a retained background-worker context.

    Tenant and owner context still travel with the worker. Configuration-derived
    grants belong to the originating HTTP request, not the worker's lifetime.
    Call this in the copied context, leaving the live request's memo intact.
    """
    from maverick.config import discard_at_rest_key_scope

    _SCOPE.set(None)
    discard_at_rest_key_scope()


@contextmanager
def fresh_configuration():
    """Recheck live policy at an authorization boundary without clearing a page."""
    from maverick.config import at_rest_key_scope

    in_request = _SCOPE.get() is not None
    token = _SCOPE.set(None)
    try:
        with at_rest_key_scope() if in_request else nullcontext():
            yield
    finally:
        _SCOPE.reset(token)


class RequestMemoMiddleware:
    """Pure ASGI: one fresh memo per HTTP request or WebSocket connection.

    The scope is a context variable, so it follows the request into FastAPI's
    thread pool (``def`` handlers, ``run_in_threadpool``) and is dropped with
    the request. Retained workers must discard the scope in their copied context.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        token = _SCOPE.set({})
        try:
            if scope["type"] == "http":
                await self._http(scope, receive, send)
            else:
                await self.app(scope, receive, send)
        finally:
            _SCOPE.reset(token)

    async def _http(self, scope, receive, send) -> None:
        from maverick.config import at_rest_key_scope

        with at_rest_key_scope() as keys:

            async def send_and_close(message) -> None:
                # Background tasks run after the last body; they resolve per read.
                if message["type"] == "http.response.body" and not message.get("more_body", False):
                    keys.close()
                await send(message)

            await self.app(scope, receive, send_and_close)
