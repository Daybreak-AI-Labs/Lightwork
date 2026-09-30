"""Per-request memo for configuration-derived authorization predicates."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from maverick_dashboard import _request_memo, auth
from starlette.concurrency import run_in_threadpool


def test_memoized_is_uncached_outside_a_request_scope():
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return calls["n"]

    assert _request_memo.in_scope() is False
    assert _request_memo.memoized("k", compute) == 1
    assert _request_memo.memoized("k", compute) == 2


def test_memo_is_per_request_and_follows_the_threadpool():
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return calls["n"]

    app = FastAPI()
    app.add_middleware(_request_memo.RequestMemoMiddleware)

    @app.get("/x")
    async def x():
        first = _request_memo.memoized("k", compute)
        again = _request_memo.memoized("k", compute)
        pooled = await run_in_threadpool(_request_memo.memoized, "k", compute)
        return {"first": first, "again": again, "pooled": pooled}

    client = TestClient(app)
    assert client.get("/x").json() == {"first": 1, "again": 1, "pooled": 1}
    assert client.get("/x").json() == {"first": 2, "again": 2, "pooled": 2}
    assert _request_memo.in_scope() is False  # nothing leaks past the request


def test_auth_predicates_are_answered_once_per_request(monkeypatch):
    calls = {"n": 0}

    def _oidc_enabled():
        calls["n"] += 1
        return False

    monkeypatch.setattr(auth, "oidc_enabled", _oidc_enabled)
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    app = FastAPI()
    app.add_middleware(_request_memo.RequestMemoMiddleware)

    @app.get("/x")
    async def x():
        for _ in range(10):
            auth.auth_genuinely_off()
            auth.non_static_auth_configured()
        return {"calls": calls["n"]}

    client = TestClient(app)
    assert client.get("/x").json() == {"calls": 1}
    assert client.get("/x").json() == {"calls": 2}  # a new request asks again
    # Outside a request the predicate still reads live configuration.
    auth.non_static_auth_configured()
    auth.non_static_auth_configured()
    assert calls["n"] == 4


def test_anonymous_grant_is_remembered_on_request_state_but_refusal_is_not(monkeypatch):
    import pytest

    checks = {"n": 0}
    allowed = {"v": True}

    def _allowed():
        checks["n"] += 1
        return allowed["v"]

    monkeypatch.setattr(auth, "anonymous_local_access_allowed", _allowed)
    monkeypatch.setattr(auth, "_dashboard_token_authenticated", lambda request: False)
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}
    request = Request(scope)
    assert auth._authorization_principal(request) is None
    assert auth._authorization_principal(Request(scope)) is None  # same scope, same state
    assert checks["n"] == 1

    allowed["v"] = False
    denied = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    with pytest.raises(HTTPException):
        auth._authorization_principal(denied)
    with pytest.raises(HTTPException):
        auth._authorization_principal(denied)
    assert checks["n"] == 3
