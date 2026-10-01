"""The public POST /form/{token} endpoint that records hosted-form submissions."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


def _client(host="testclient"):
    from maverick_dashboard.app import app
    return TestClient(app, headers={"Origin": "http://testserver"}, client=(host, 50000))


def _isolate(monkeypatch, tmp_path, triggers="1"):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", triggers)
    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")


def _register_form(token: str, *, tenant: str = "", name: str = "") -> None:
    from maverick.flow import store as flow_store
    from maverick.flow.ir import Flow, FlowNode
    from maverick.paths import reset_tenant, set_tenant
    from maverick_dashboard import event_triggers_store

    scope = set_tenant(tenant or None)
    try:
        published = flow_store.load_published_bundle("published-form-flow")
        if published is None:
            saved = flow_store.save_flow(Flow(
                id="published-form-flow", name="Published form", start="a",
                nodes={"a": FlowNode(id="a", kind="agent", brief="Review the submission")},
            ))
            release = flow_store.publish_flow(
                saved.id, expected_version=saved.version, expected_revision=saved.revision,
            )
        else:
            saved, release = published
    finally:
        reset_tenant(scope)
    event_triggers_store.set_trigger(
        name or f"accept-{token}", "", "form", config={"token": token},
        flow=saved.id, flow_owner=saved.owner,
        flow_revision=release["release_id"], tenant=tenant,
    )


def test_json_submission_is_recorded_and_pollable(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("signup")
    r = _client().post("/form/signup", json={"email": "a@b.com", "plan": "pro"})
    assert r.status_code == 201 and r.json()["seq"] == 1
    # The first poll records the existing submission without replaying it.
    from maverick import automation_events as ev
    base = ev.get_source("form").poll({"token": "signup"}, "")   # baseline
    assert base.events == []
    assert ev._decode_cursor(base.cursor, "form", base.cursor_format) == "1"


def test_form_submission_fires_after_baseline(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("lead")
    c = _client()
    c.post("/form/lead", json={"name": "first"})
    from maverick import automation_events as ev
    base = ev.get_source("form").poll({"token": "lead"}, "")
    c.post("/form/lead", json={"name": "second"})
    out = ev.get_source("form").poll({"token": "lead"}, base.cursor)
    assert [e["name"] for e in out.events] == ["second"]


def test_urlencoded_submission(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("contact")
    r = _client().post("/form/contact", data={"message": "hello"})
    assert r.status_code == 201
    from maverick import form_store
    events, _ = form_store.since("contact", "0")
    assert events and events[0]["message"] == "hello"


def test_small_multipart_text_submission(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("contact")
    response = _client().post("/form/contact", files={"message": (None, "hello")})
    assert response.status_code == 201
    from maverick import form_store

    events, _ = form_store.since("contact", "0")
    assert events[0]["message"] == "hello"


def test_form_disabled_when_triggers_off(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, triggers="0")
    r = _client().post("/form/x", json={"a": 1})
    assert r.status_code == 404


def test_invalid_json_is_400(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("x")
    r = _client().post("/form/x", content=b"{not json",
                       headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_unregistered_and_non_form_tokens_create_no_submission(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import event_triggers_store

    event_triggers_store.set_trigger(
        "other-source", "", "rss", config={"token": "rss-token"},
        flow="published-form-flow",
    )
    client = _client()
    assert client.post("/form/never-registered", json={"name": "stranger"}).status_code == 404
    assert client.post("/form/rss-token", json={"name": "stranger"}).status_code == 404
    assert not list(tmp_path.rglob("*.ndjson"))


def test_form_rate_limit_runs_before_body_parser(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("signup")
    from maverick_dashboard import app as dashboard_app

    def limited(request, *, source=None):
        assert source is None
        raise HTTPException(status_code=429, detail="slow down")

    async def never_parse(_request):
        raise AssertionError("request body was parsed before rate limiting")

    monkeypatch.setattr(dashboard_app, "check_goal_rate_limit", limited)
    monkeypatch.setattr(dashboard_app, "_read_limited_webhook_body", never_parse)
    response = _client().post("/form/signup", json={"name": "any"})
    assert response.status_code == 429
    assert not list(tmp_path.rglob("*.ndjson"))


def test_disabled_trigger_does_not_accept_new_submissions(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("signup")
    from maverick_dashboard import event_triggers_store

    assert event_triggers_store.delete_trigger("accept-signup", tenant="")
    response = _client().post("/form/signup", json={"name": "stranger"})
    assert response.status_code == 404
    assert not list(tmp_path.rglob("*.ndjson"))


def test_same_token_across_tenants_is_not_routed_arbitrarily(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("signup", tenant="one")
    _register_form("signup", tenant="two")
    response = _client().post("/form/signup", json={"name": "stranger"})
    assert response.status_code == 404
    assert not list(tmp_path.rglob("*.ndjson"))


def test_form_capacity_error_is_not_reported_as_success(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("signup")
    from maverick import form_store

    monkeypatch.setattr(form_store, "_MAX_LINES", 1)
    client = _client()
    assert client.post("/form/signup", json={"name": "first"}).status_code == 201
    response = client.post("/form/signup", json={"name": "second"})
    assert response.status_code == 507
    events, _ = form_store.since("signup", "0")
    assert [event["name"] for event in events] == ["first"]


def test_vendor_form_still_accepts_when_event_triggers_are_on(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import app as dashboard_app

    monkeypatch.setattr(dashboard_app, "_vendor_questionnaire_for", lambda token: {"token": token})
    response = _client().post("/form/vendor-token", data={"answer": "yes"})
    assert response.status_code == 201


def test_new_form_trigger_rejects_aliased_storage_token(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("signup")
    response = _client().post("/api/v1/event-triggers", json={
        "name": "new-form", "source": "form", "flow": "published-form-flow",
        "config": {"token": "my.form"},
    })
    assert response.status_code == 400


def test_existing_punctuation_token_keeps_its_legacy_form_file(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("lead.v1")
    response = _client().post("/form/lead.v1", json={"name": "first"})
    assert response.status_code == 201
    from maverick import form_store
    from maverick_dashboard.event_poll import admit_event_source_config

    assert (tmp_path / "forms" / "lead-v1.ndjson").exists()
    assert admit_event_source_config("form", {"token": "lead.v1"}) == {"token": "lead.v1"}
    events, _ = form_store.since("lead.v1", "0")
    assert [event["name"] for event in events] == ["first"]


def test_colliding_registered_form_tokens_are_both_refused(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("lead.v1")
    _register_form("lead-v1", name="second-form")
    client = _client()
    assert client.post("/form/lead.v1", json={"name": "first"}).status_code == 404
    assert client.post("/form/lead-v1", json={"name": "second"}).status_code == 404
    assert not list(tmp_path.rglob("*.ndjson"))


def test_oversized_multipart_is_rejected_before_parsing(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("signup")
    from starlette.requests import Request

    def never_parse(self, *args, **kwargs):
        raise AssertionError("multipart parser ran before size admission")

    monkeypatch.setattr(Request, "form", never_parse)
    response = _client().post(
        "/form/signup", files={"attachment": ("large.txt", b"x" * 300_000)},
    )
    assert response.status_code == 413
    assert not list(tmp_path.rglob("*.ndjson"))


def test_store_write_failure_is_not_reported_as_success(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("signup")
    from maverick import form_store

    monkeypatch.setattr(form_store, "append", lambda *args, **kwargs: 0)
    response = _client().post("/form/signup", json={"name": "first"})
    assert response.status_code == 503


def test_stale_flow_release_does_not_accept_a_form_submission(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("signup")
    from maverick.flow import store as flow_store

    published = flow_store.load_published_bundle("published-form-flow")
    assert published is not None
    flow_store.unpublish_flow(
        published[0].id,
        expected_version=published[0].version,
        expected_revision=published[0].revision,
    )
    response = _client().post("/form/signup", json={"name": "first"})
    assert response.status_code == 404
    assert not list(tmp_path.rglob("*.ndjson"))


def test_republished_flow_cannot_reuse_an_old_form_binding(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("signup")
    from maverick.flow import store as flow_store

    published = flow_store.load_published_bundle("published-form-flow")
    assert published is not None
    flow, old_release = published
    assert flow_store.unpublish_flow(
        flow.id, expected_version=flow.version, expected_revision=flow.revision,
    )
    new_release = flow_store.publish_flow(
        flow.id, expected_version=flow.version, expected_revision=flow.revision,
    )
    assert new_release["release_id"] != old_release["release_id"]
    response = _client().post("/form/signup", json={"name": "first"})
    assert response.status_code == 404
    assert not list(tmp_path.rglob("*.ndjson"))


def test_bound_template_form_still_accepts(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.templates import Template
    from maverick_dashboard import event_triggers_store
    from maverick_dashboard.triggers_store import snapshot_from_template

    template = Template(name="contact", title="Contact", body="Review {{name}}", params=["name"])
    event_triggers_store.set_trigger(
        "contact", "contact", "form", config={"token": "contact"},
        template_snapshot=snapshot_from_template(template),
    )
    response = _client().post("/form/contact", json={"name": "first"})
    assert response.status_code == 201


def test_public_form_rate_limit_is_per_ip_not_url_token(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("signup")
    monkeypatch.setenv("MAVERICK_DASHBOARD_MAX_GOALS_PER_MIN", "1")
    monkeypatch.setenv("MAVERICK_DASHBOARD_MAX_GOALS_GLOBAL_PER_MIN", "100")
    from maverick_dashboard import app as dashboard_app

    dashboard_app._goal_times.clear()
    dashboard_app._goal_times_global.clear()
    first = _client("192.0.2.1")
    second = _client("192.0.2.2")
    assert first.post("/form/signup", json={"name": "first"}).status_code == 201
    assert first.post("/form/forged-1", json={"name": "second"}).status_code == 429
    assert first.post("/form/forged-2", json={"name": "third"}).status_code == 429
    assert second.post("/form/signup", json={"name": "another"}).status_code == 201
    assert set(dashboard_app._goal_times) == {"ip:192.0.2.1", "ip:192.0.2.2"}


@pytest.mark.parametrize("method", ["get", "post"])
def test_questionnaire_migration_is_retryable_not_missing(monkeypatch, tmp_path, method):
    _isolate(monkeypatch, tmp_path, triggers="0")
    from maverick import vendor_management
    from maverick.governed_record_lookup import RecordLookupUnavailable

    def unavailable(_token):
        raise RecordLookupUnavailable("record lookup is rebuilding")
    monkeypatch.setattr(vendor_management, "questionnaire_for_token", unavailable)
    response = getattr(_client(), method)("/form/existing-link")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "2"


def test_disabled_questionnaire_register_still_returns_missing(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, triggers="0")
    from maverick import vendor_management
    monkeypatch.setattr(vendor_management, "enabled", lambda: False)
    assert _client().get("/form/unknown").status_code == 404
