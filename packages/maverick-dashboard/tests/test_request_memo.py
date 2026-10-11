"""Per-request memo for configuration-derived authorization predicates."""
from __future__ import annotations

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
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


def _sealed_home(tmp_path, monkeypatch):
    """A deployment with one secret saved from Settings (sealed at rest)."""
    from maverick import config, crypto_at_rest

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_ENCRYPTION_KEY", raising=False)
    config.reset_config_cache()
    (tmp_path / "config.toml").write_text(
        '[budget]\nmax_dollars = 5.0\n[probe]\nvalue = "${MAVERICK_PROBE_VALUE}"\n', encoding="utf-8",
    )
    with crypto_at_rest.deployment_key_scope():
        token = crypto_at_rest.seal_to_str("sk-test-not-a-real-key")  # pragma: allowlist secret
    config.dashboard_overrides_path().write_text(f'[providers.anthropic]\napi_key = "{token}"\n', encoding="utf-8")


def _api_key():
    from maverick import config

    return config.load_config().get("providers", {}).get("anthropic", {}).get("api_key")


def test_a_request_resolves_the_at_rest_key_once_and_again_at_each_authorization_boundary(tmp_path, monkeypatch):
    # One page read the configuration about 350 times, and each read re-secured
    # and re-read the at-rest key before opening the secrets saved in Settings.
    from maverick import config, crypto_at_rest

    _sealed_home(tmp_path, monkeypatch)
    resolved = []
    real = crypto_at_rest._load_or_create_key
    monkeypatch.setattr(crypto_at_rest, "_load_or_create_key", lambda: resolved.append(1) or real())
    later = []
    app = FastAPI()
    app.add_middleware(_request_memo.RequestMemoMiddleware)

    def after_response():
        later.extend([_api_key(), _api_key(), len(resolved)])

    @app.get("/x")
    async def x(tasks: BackgroundTasks):
        keys = await run_in_threadpool(lambda: {_api_key() for _ in range(100)})
        page = len(resolved)
        with _request_memo.fresh_configuration():
            boundary = {_api_key() for _ in range(3)}
        tasks.add_task(after_response)
        return {"keys": sorted(keys | boundary), "resolved": [page, len(resolved)]}

    try:
        body = TestClient(app).get("/x").json()
        assert body == {"keys": ["sk-test-not-a-real-key"], "resolved": [1, 2]}  # pragma: allowlist secret
        # After the response is sent, background work resolves on every read.
        assert later == ["sk-test-not-a-real-key", "sk-test-not-a-real-key", 4]  # pragma: allowlist secret
    finally:
        config.reset_config_cache()


def test_a_request_still_reads_policy_errors_and_variables_live(tmp_path, monkeypatch):
    import os

    from maverick import config, crypto_at_rest

    _sealed_home(tmp_path, monkeypatch)
    monkeypatch.setenv("MAVERICK_PROBE_VALUE", "one")
    real = crypto_at_rest._load_or_create_key
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise crypto_at_rest.EncryptionUnavailable("the key file is held by its writer")
        return real()

    monkeypatch.setattr(crypto_at_rest, "_load_or_create_key", flaky)
    app = FastAPI()
    app.add_middleware(_request_memo.RequestMemoMiddleware)

    @app.get("/x")
    def x():
        seen = {"key": [_api_key(), _api_key()]}  # a failed key read is retried
        seen["value"] = [config.load_config()["probe"]["value"]]
        os.environ["MAVERICK_PROBE_VALUE"] = "two"
        seen["value"].append(config.load_config()["probe"]["value"])
        replacement = tmp_path / "config.toml.new"
        replacement.write_text("[budget]\nmax_dollars = 7.0\n", encoding="utf-8")
        os.replace(replacement, tmp_path / "config.toml")
        seen["budget"] = config.load_config()["budget"]["max_dollars"]
        (tmp_path / "config.toml").write_text("[budget\n", encoding="utf-8")  # corrupt policy
        config.load_config()
        config.reset_config_cache()  # what several settings readers do first
        config.load_config()
        seen["errors"] = sorted(config.config_source_errors())
        return seen

    try:
        assert TestClient(app).get("/x").json() == {
            "key": [None, "sk-test-not-a-real-key"],  # pragma: allowlist secret
            "value": ["one", "two"],
            "budget": 7.0,
            "errors": [str(tmp_path / "config.toml")],
        }
    finally:
        config.reset_config_cache()
