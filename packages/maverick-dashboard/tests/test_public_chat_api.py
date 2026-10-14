"""Public website chat widget: gate, origins, keys, sessions, limits, answers, transcripts."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

SHOP = "https://shop.example"
EVIL = "https://evil.example"
OTHER = "https://store.example"
ON = "[public_chat]\nenabled = true\n"
OFF = "[public_chat]\nenabled = false\n"
WIDGET = Path(__file__).resolve().parents[1] / "maverick_dashboard" / "static" / "lightwork-public-chat-widget.js"


def _write_config(tmp_path, text):
    from maverick import config

    (tmp_path / "config.toml").write_text(text, encoding="utf-8")
    config.reset_config_cache()


@pytest.fixture
def chat(tmp_path, monkeypatch):
    from maverick import config, world_model
    from maverick.audit import writer as audit_writer
    from maverick_dashboard import app as dash_app
    from maverick_dashboard import public_chat_api as api
    from maverick_dashboard.consent_public_api import PublicConsentRateLimiter

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr(audit_writer, "_default", None)
    audit_writer._defaults.clear()
    _write_config(tmp_path, ON)
    clock = {"now": 1_800_000_000.0}
    monkeypatch.setattr(api, "_now", lambda: clock["now"])
    # The limiters run on the same injected clock as the visitor-session TTL.
    monkeypatch.setattr(api, "_SESSION_KEY_LIMITER", PublicConsentRateLimiter(
        limit_source=lambda: api.SESSION_KEY_PER_MINUTE, clock=lambda: clock["now"]))
    monkeypatch.setattr(api, "_SESSION_CLIENT_LIMITER", PublicConsentRateLimiter(
        limit_source=lambda: api.SESSION_CLIENT_PER_MINUTE, clock=lambda: clock["now"]))
    yield SimpleNamespace(
        client=TestClient(dash_app.app, headers={"Origin": "http://testserver"}),
        anon=TestClient(dash_app.app),
        clock=clock, home=tmp_path / "mh", api=api, tmp=tmp_path, monkeypatch=monkeypatch,
    )
    config.reset_config_cache()


@pytest.fixture
def model(monkeypatch):
    from maverick import llm as llm_module

    fake = SimpleNamespace(text="We are open 9 to 5, Monday to Friday.", dollars=0.01,
                           calls=[], error=None)

    class FakeLLM:
        def __init__(self, spec):
            self.spec = spec

        def complete(self, system, messages, tools=None, budget=None, max_tokens=0, **kwargs):
            fake.calls.append({"system": system, "messages": messages, "tools": tools,
                               "max_tokens": max_tokens, **kwargs})
            if fake.error is not None:
                raise fake.error
            budget.dollars = fake.dollars
            return SimpleNamespace(text=fake.text)

    monkeypatch.setattr(llm_module, "LLM", FakeLLM)
    monkeypatch.setattr(llm_module, "model_for_role", lambda role, **kwargs: "fake:model")
    return fake


def _site(chat, **overrides):
    body = {"display_name": "Acme shop", "origins": [SHOP], "specialist": "finance_ap",
            "greeting": "Hello, how can I help?", "daily_cap_dollars": 1.0,
            "messages_per_session": 10, "messages_per_minute": 6, "retention_days": 30}
    body.update(overrides)
    response = chat.client.post("/api/v1/public-chat/sites", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _start(chat, key, origin=SHOP):
    headers = {"Origin": origin, "Authorization": f"PublicChatKey {key}"} if origin else {
        "Authorization": f"PublicChatKey {key}"}
    return chat.anon.post("/public/chat/v1/sessions", headers=headers, content=b"{}")


def _say(chat, token, text, origin=SHOP):
    headers = {"Authorization": f"PublicChatSession {token}", "Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    return chat.anon.post("/public/chat/v1/messages", headers=headers,
                          content=json.dumps({"message": text}).encode("utf-8"))


def _session(chat, key):
    response = _start(chat, key)
    assert response.status_code == 201, response.text
    return response.json()["session_token"]


def test_disabled_gate_returns_404_and_stores_nothing(chat, model):
    _write_config(chat.tmp, OFF)
    assert chat.anon.get("/public/chat/v1/widget.js").status_code == 404
    assert _start(chat, "lwpk_" + "a" * 43).status_code == 404
    assert _say(chat, "lwps_" + "a" * 43, "hello").status_code == 404
    assert chat.anon.options("/public/chat/v1/sessions", headers={
        "Origin": SHOP, "Access-Control-Request-Method": "POST"}).status_code == 404
    admin = chat.client.post("/api/v1/public-chat/sites", json={"display_name": "x"})
    assert admin.status_code == 404
    assert chat.client.get("/api/v1/public-chat/sites").status_code == 404
    assert not (chat.home / "public_chat").exists()


def test_widget_is_static_cacheable_and_contains_no_secret(chat):
    response = chat.anon.get("/public/chat/v1/widget.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert "max-age=" in response.headers["cache-control"]
    # The prefix is public text; a key-shaped token (prefix plus 32 random bytes) is not.
    assert re.search(r"lwp[ks]_[A-Za-z0-9_-]{20,}", response.text) is None
    assert "data-key" in response.text


def test_widget_inserts_text_only_and_never_evaluates_markup():
    source = WIDGET.read_text(encoding="utf-8")
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                      "createContextualFragment", "DOMParser", "eval(", "Function(",
                      "setTimeout(\"", "setTimeout('", "setInterval(\"", "setInterval('",
                      "javascript:"):
        assert forbidden not in source, forbidden
    assert "textContent" in source and "attachShadow" in source


def test_admin_create_shows_key_once_and_stores_only_its_digest(chat, model):
    created = _site(chat)
    key = created["publishable_key"]
    assert key.startswith("lwpk_") and len(key) == len("lwpk_") + 43
    assert "publishable_key" not in chat.client.get("/api/v1/public-chat/sites").json()["sites"][0]
    index_bytes = (chat.home / "public_chat" / "index.sqlite3").read_bytes()
    assert hashlib.sha256(key.encode("utf-8")).hexdigest().encode("ascii") in index_bytes
    assert key.encode("utf-8") not in index_bytes


@pytest.mark.parametrize("bad", [
    {"daily_cap_dollars": 0},
    {"daily_cap_dollars": -1},
    {"messages_per_session": 31},
    {"messages_per_minute": 7},
    {"retention_days": 0},
    {"origins": ["http://shop.example"]},
    {"origins": ["https://*.example"]},
    {"origins": ["https://shop.example/path"]},
    {"origins": ["https://shop.example?x=1"]},
    {"origins": ["https://user@shop.example"]},
    {"origins": ["http://localhost:3000"]},
    {"origins": [f"https://s{i}.example" for i in range(21)]},
    {"specialist": "no_such_specialist"},
])
def test_admin_create_rejects_out_of_bounds_values(chat, model, bad):
    body = {"display_name": "Acme shop", "origins": [SHOP], "specialist": "finance_ap",
            "greeting": "Hi", "daily_cap_dollars": 1.0}
    body.update(bad)
    response = chat.client.post("/api/v1/public-chat/sites", json=body)
    # Each bad value is a validation refusal, not a missing route or a conflict.
    assert response.status_code == 422, response.text


def test_origin_normalization_is_exact_and_localhost_needs_the_dev_flag(chat):
    from maverick_dashboard import public_chat_store as store

    assert store.normalize_origin("https://Shop.Example/", allow_localhost=False) == SHOP
    for bad in ("http://shop.example", "https://shop.example:8443/x", "https://a b.example",
                "https://", "null", "https://127.0.0.1", "https://[::1]"):
        with pytest.raises(ValueError):
            store.normalize_origin(bad, allow_localhost=False)
    assert store.normalize_origin("http://localhost:3000", allow_localhost=True) == (
        "http://localhost:3000")
    with pytest.raises(ValueError):
        store.normalize_origin("http://localhost:3000", allow_localhost=False)


def test_bad_or_revoked_key_is_a_generic_401(chat, model):
    created = _site(chat)
    unknown = _start(chat, "lwpk_" + "b" * 43)
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == "invalid publishable key"
    assert chat.client.post(f"/api/v1/public-chat/sites/{created['site']['id']}/revoke").status_code == 200
    revoked = _start(chat, created["publishable_key"])
    assert revoked.status_code == 401
    assert revoked.json() == unknown.json()
    assert created["publishable_key"] not in revoked.text


def test_wrong_or_missing_origin_is_403(chat, model):
    key = _site(chat)["publishable_key"]
    assert _start(chat, key, origin=EVIL).status_code == 403
    assert _start(chat, key, origin=None).status_code == 403
    assert _start(chat, key, origin="null").status_code == 403


@pytest.mark.parametrize("origin", [EVIL, None, OTHER])
def test_message_with_wrong_or_missing_origin_is_refused_and_counted_nowhere(chat, model, origin):
    # OTHER is a listed origin of the same site: only the session's own origin binding refuses it.
    created = _site(chat, origins=[SHOP, OTHER], messages_per_session=2)
    token = _session(chat, created["publishable_key"])
    refused = _say(chat, token, "hello", origin=origin)
    assert refused.status_code == 403
    assert refused.json()["detail"] == "this website is not allowed to use this chat"
    assert "access-control-allow-origin" not in refused.headers
    assert model.calls == []
    # The refusal used no message from the session and wrote no transcript row.
    assert _say(chat, token, "hi").json()["remaining_messages"] == 1
    site_id = created["site"]["id"]
    viewed = chat.client.get(f"/api/v1/public-chat/sites/{site_id}/transcripts").json()
    assert [row["text"] for row in viewed["messages"]] == ["hi", model.text]


def test_cors_is_granted_only_to_the_sites_exact_origins(chat, model):
    key = _site(chat)["publishable_key"]
    preflight = chat.anon.options("/public/chat/v1/sessions", headers={
        "Origin": SHOP, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization, content-type"})
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == SHOP
    denied = chat.anon.options("/public/chat/v1/sessions", headers={
        "Origin": EVIL, "Access-Control-Request-Method": "POST"})
    assert denied.status_code == 403
    assert "access-control-allow-origin" not in denied.headers
    allowed = _start(chat, key)
    assert allowed.headers["access-control-allow-origin"] == SHOP
    refused = _start(chat, key, origin=EVIL)
    assert "access-control-allow-origin" not in refused.headers


def test_session_token_is_digest_only_bounded_and_expires(chat, model):
    key = _site(chat)["publishable_key"]
    body = _start(chat, key).json()
    token = body["session_token"]
    assert token.startswith("lwps_") and body["ttl_seconds"] <= 3600
    assert body["greeting"] == "Hello, how can I help?"
    assert token.encode("utf-8") not in (chat.home / "public_chat" / "index.sqlite3").read_bytes()
    assert _say(chat, token, "hello").status_code == 200
    chat.clock["now"] += body["ttl_seconds"] + 1
    expired = _say(chat, token, "hello again")
    assert expired.status_code == 401
    assert expired.json()["detail"] == "chat session expired or invalid"


def test_session_starts_are_rate_limited_per_key_and_per_client(chat, model):
    key = _site(chat)["publishable_key"]
    chat.monkeypatch.setattr(chat.api, "SESSION_CLIENT_PER_MINUTE", 2)
    assert _start(chat, key).status_code == 201
    assert _start(chat, key).status_code == 201
    limited = _start(chat, key)
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    chat.clock["now"] += 61
    assert _start(chat, key).status_code == 201
    chat.monkeypatch.setattr(chat.api, "SESSION_KEY_PER_MINUTE", 1)
    chat.clock["now"] += 61
    assert _start(chat, key).status_code == 201
    assert _start(chat, key).status_code == 429


def test_message_limits_per_session_and_per_minute(chat, model):
    key = _site(chat, messages_per_session=3, messages_per_minute=2)["publishable_key"]
    token = _session(chat, key)
    assert _say(chat, token, "one").status_code == 200
    assert _say(chat, token, "two").status_code == 200
    minute = _say(chat, token, "three")
    assert minute.status_code == 429
    chat.clock["now"] += 61
    assert _say(chat, token, "three").status_code == 200
    assert _say(chat, token, "four").status_code == 429


def test_message_body_is_capped_at_4_kib(chat, model):
    key = _site(chat)["publishable_key"]
    token = _session(chat, key)
    big = chat.anon.post("/public/chat/v1/messages", headers={
        "Origin": SHOP, "Authorization": f"PublicChatSession {token}",
        "Content-Type": "application/json"}, content=b"{" + b" " * 5000 + b"}")
    assert big.status_code == 413
    assert model.calls == []


def test_answer_path_is_tool_free_budgeted_and_never_reaches_the_agent(chat, model, monkeypatch):
    from maverick import agent as agent_module

    def _no_agent(self, *args, **kwargs):
        raise AssertionError("the agent loop must not be reached from a public visitor")

    monkeypatch.setattr(agent_module.Agent, "__init__", _no_agent)
    key = _site(chat)["publishable_key"]
    token = _session(chat, key)
    response = _say(chat, token, "What are your opening hours?")
    assert response.status_code == 200
    assert response.json()["answer"] == model.text
    call = model.calls[0]
    assert call["tools"] is None
    assert call["_no_failover"] is True and call["max_physical_attempts"] == 1
    assert callable(call["dispatch_guard"])
    assert call["max_tokens"] <= 300
    assert "answer only" in call["system"].lower()
    assert "untrusted" in call["system"].lower()
    assert "fictional test material" not in call["system"]
    assert call["messages"] == [{"role": "user", "content": "What are your opening hours?"}]


def test_daily_cap_stops_answers_politely_before_each_call(chat, model):
    key = _site(chat, daily_cap_dollars=0.10)["publishable_key"]
    token = _session(chat, key)
    model.dollars = 0.04
    assert _say(chat, token, "one").json()["answer"] == model.text
    assert _say(chat, token, "two").json()["answer"] == model.text
    polite = _say(chat, token, "three")
    assert polite.status_code == 200
    assert "not available right now" in polite.json()["answer"]
    assert len(model.calls) == 2


def test_failed_provider_call_keeps_its_reservation_and_is_not_retried(chat, model):
    # Cap 0.10 and a 0.05 reservation per answer: the failed call is treated as
    # possibly billed, so it keeps its 0.05 and only one more answer fits.
    key = _site(chat, daily_cap_dollars=0.10)["publishable_key"]
    token = _session(chat, key)
    model.error = RuntimeError("provider timeout")
    response = _say(chat, token, "one")
    assert response.status_code == 200
    assert "not available right now" in response.json()["answer"]
    assert "provider timeout" not in response.text
    assert len(model.calls) == 1
    model.error = None
    assert _say(chat, token, "two").json()["answer"] == model.text
    polite = _say(chat, token, "three")
    assert "not available right now" in polite.json()["answer"]
    assert len(model.calls) == 2


def test_revoked_site_ends_existing_sessions(chat, model):
    created = _site(chat)
    token = _session(chat, created["publishable_key"])
    chat.client.post(f"/api/v1/public-chat/sites/{created['site']['id']}/revoke")
    response = _say(chat, token, "still there?")
    assert response.status_code == 401
    assert response.json()["detail"] == "chat session expired or invalid"


def test_rotate_invalidates_the_old_key_and_returns_a_new_one_once(chat, model):
    created = _site(chat)
    old = created["publishable_key"]
    rotated = chat.client.post(f"/api/v1/public-chat/sites/{created['site']['id']}/rotate").json()
    new = rotated["publishable_key"]
    assert new != old and new.startswith("lwpk_")
    assert _start(chat, old).status_code == 401
    assert _start(chat, new).status_code == 201


def test_transcripts_are_sealed_tenant_scoped_and_purged_after_retention(chat, model, monkeypatch):
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    created = _site(chat, retention_days=1)
    site_id = created["site"]["id"]
    token = _session(chat, created["publishable_key"])
    question = "Can I get a refund for order 88412?"
    assert _say(chat, token, question).status_code == 200
    viewed = chat.client.get(f"/api/v1/public-chat/sites/{site_id}/transcripts").json()
    assert [row["text"] for row in viewed["messages"]] == [question, model.text]
    from maverick_dashboard import public_chat_store as store

    tenant_db = store.sites_path("tenant-a")
    assert tenant_db.exists() and tenant_db != store.sites_path("tenant-b")
    assert question.encode("utf-8") not in tenant_db.read_bytes()
    assert question.encode("utf-8") not in store.index_path().read_bytes()
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-b")
    assert chat.client.get("/api/v1/public-chat/sites").json()["sites"] == []
    assert chat.client.get(f"/api/v1/public-chat/sites/{site_id}/transcripts").status_code == 404
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    chat.clock["now"] += 2 * 86_400
    assert chat.client.get(f"/api/v1/public-chat/sites/{site_id}/transcripts").json()["messages"] == []


def test_auth_exemption_covers_only_the_public_prefix(chat):
    from maverick_dashboard import auth

    assert auth._self_authenticated_path("/public/chat/v1/sessions")
    assert auth._self_authenticated_path("/public/chat/v1/widget.js")
    assert not auth._self_authenticated_path("/api/v1/public-chat/sites")
    assert not auth._self_authenticated_path("/public/chat/v1x")


def test_admin_actions_are_audited_without_key_material(chat, model, monkeypatch):
    from maverick import audit

    recorded = []

    def _record(kind, **detail):
        recorded.append({"kind": str(kind), **detail})

    monkeypatch.setattr(audit, "audit_event", _record)
    created = _site(chat)
    site_id = created["site"]["id"]
    chat.client.post(f"/api/v1/public-chat/sites/{site_id}/rotate")
    chat.client.get(f"/api/v1/public-chat/sites/{site_id}/transcripts")
    chat.client.post(f"/api/v1/public-chat/sites/{site_id}/revoke")
    verbs = [row.get("verb") for row in recorded if str(row.get("verb", "")).startswith("public_chat")]
    assert verbs == ["public_chat_site_created", "public_chat_key_rotated",
                     "public_chat_transcripts_viewed", "public_chat_site_revoked"]
    assert all(created["publishable_key"] not in json.dumps(row, default=str) for row in recorded)


def test_admin_routes_need_the_admin_permission(chat, model, monkeypatch):
    from fastapi import HTTPException
    from maverick_dashboard import auth

    def _deny(request, permission, **kwargs):
        raise HTTPException(status_code=403, detail="not allowed")

    monkeypatch.setattr(auth, "require_permission", _deny)
    response = chat.client.post("/api/v1/public-chat/sites", json={"display_name": "x"})
    assert response.status_code == 403
    assert not (chat.home / "public_chat" / "index.sqlite3").exists()


def test_public_routes_are_mounted_in_the_real_app(chat):
    app_module = importlib.import_module("maverick_dashboard.app")
    assert "/api/v1/public-chat/sites" in app_module.app.openapi()["paths"]
    assert not any(path.startswith("/public/chat/") for path in app_module.app.openapi()["paths"])
    assert chat.anon.get("/public/chat/v1/widget.js").status_code == 200


def test_settings_catalog_offers_the_two_public_chat_switches():
    """Both switches are off by default, and the page reads them from the feature's own module."""
    from maverick.migrate import KNOWN_SECTIONS
    from maverick_dashboard import settings_catalog as catalog

    assert "public_chat" in KNOWN_SECTIONS
    enabled = catalog.BY_ID["public_chat.enabled"]
    assert enabled.default is False
    assert enabled.reader == "maverick_dashboard.public_chat_api"
    local = catalog.BY_ID["public_chat.allow_localhost_origins"]
    assert local.default is False and local.reader == enabled.reader
