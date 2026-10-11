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


def test_anonymous_access_is_rechecked_at_every_gate(monkeypatch):
    # Principal-less access is never remembered: each gate rechecks current
    # local policy, so a policy change takes effect inside the same request.
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
    assert checks["n"] == 2

    allowed["v"] = False
    denied = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    with pytest.raises(HTTPException):
        auth._authorization_principal(denied)
    with pytest.raises(HTTPException):
        auth._authorization_principal(denied)
    assert checks["n"] == 4


def test_a_request_reads_the_sealed_configuration_once_and_still_sees_a_policy_saved_meanwhile(tmp_path, monkeypatch):
    # One page asked for the configuration about 350 times, and every read
    # decrypted the saved secrets again. Inside a request it is read once;
    # an authorization check after a save (fresh_configuration) still sees it.
    import os

    from maverick import config, crypto_at_rest

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_ENCRYPTION_KEY", raising=False)
    config.reset_config_cache()
    (tmp_path / "config.toml").write_text("[budget]\nmax_dollars = 5.0\n", encoding="utf-8")
    with crypto_at_rest.deployment_key_scope():
        token = crypto_at_rest.seal_to_str("sk-test-not-a-real-key")  # pragma: allowlist secret
    config.dashboard_overrides_path().write_text(f'[providers.anthropic]\napi_key = "{token}"\n', encoding="utf-8")
    opened = []
    real = crypto_at_rest.unseal_many_from_str
    monkeypatch.setattr(crypto_at_rest, "unseal_many_from_str", lambda values: opened.append(1) or real(values))

    app = FastAPI()
    app.add_middleware(_request_memo.RequestMemoMiddleware)

    def read_many():
        return [config.load_config()["providers"]["anthropic"]["api_key"] for _ in range(100)]

    @app.get("/x")
    async def x():
        keys = await run_in_threadpool(read_many)
        replacement = tmp_path / "config.toml.new"
        replacement.write_text("[budget]\nmax_dollars = 7.0\n", encoding="utf-8")
        os.replace(replacement, tmp_path / "config.toml")
        with _request_memo.fresh_configuration():
            budget = config.load_config()["budget"]["max_dollars"]
        return {"keys": sorted(set(keys)), "budget": budget}

    try:
        assert TestClient(app).get("/x").json() == {"keys": ["sk-test-not-a-real-key"], "budget": 7.0}  # pragma: allowlist secret
        assert len(opened) == 2  # once, then once more after the save
    finally:
        config.reset_config_cache()
