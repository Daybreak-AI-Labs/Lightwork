"""Whether the dashboard is serving requests right now.

Start-up work that holds the CPU for seconds (the OpenAPI schema build) waits
for a quiet moment instead of slowing the first people to use a fresh
process. A pure-ASGI middleware counts requests in flight and notes when the
last one started; nothing about the request itself is kept.
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_in_flight = 0
_last_start = 0.0


def started() -> None:
    global _in_flight, _last_start
    with _lock:
        _in_flight += 1
        _last_start = time.monotonic()


def finished() -> None:
    global _in_flight
    with _lock:
        _in_flight = max(0, _in_flight - 1)


def in_flight() -> int:
    return _in_flight


def quiet_for(seconds: float) -> bool:
    """No request in flight and none started in the last ``seconds``."""
    with _lock:
        return _in_flight == 0 and time.monotonic() - _last_start >= seconds


def idle_for(seconds: float) -> bool:
    """No request started in the last ``seconds``; requests still open (an
    event stream, a WebSocket) do not count."""
    with _lock:
        return time.monotonic() - _last_start >= seconds


class RequestActivityMiddleware:
    """Pure ASGI: counts HTTP requests and WebSocket connections in flight."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        started()
        try:
            await self.app(scope, receive, send)
        finally:
            finished()
