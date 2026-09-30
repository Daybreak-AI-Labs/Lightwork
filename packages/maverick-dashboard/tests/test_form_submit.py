"""The public POST /form/{token} endpoint that records hosted-form submissions."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _client():
    from maverick_dashboard.app import app
    return TestClient(app, headers={"Origin": "http://testserver"})


def _isolate(monkeypatch, tmp_path, triggers="1"):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", triggers)
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")


def test_json_submission_is_recorded_and_pollable(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    r = _client().post("/form/signup", json={"email": "a@b.com", "plan": "pro"})
    assert r.status_code == 201 and r.json()["seq"] == 1
    # The first poll records the existing submission without replaying it.
    from maverick import automation_events as ev
    base = ev.get_source("form").poll({"token": "signup"}, "")   # baseline
    assert base.events == []
    assert ev._decode_cursor(base.cursor, "form", base.cursor_format) == "1"


def test_form_submission_fires_after_baseline(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    c.post("/form/lead", json={"name": "first"})
    from maverick import automation_events as ev
    base = ev.get_source("form").poll({"token": "lead"}, "")
    c.post("/form/lead", json={"name": "second"})
    out = ev.get_source("form").poll({"token": "lead"}, base.cursor)
    assert [e["name"] for e in out.events] == ["second"]


def test_urlencoded_submission(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    r = _client().post("/form/contact", data={"message": "hello"})
    assert r.status_code == 201
    from maverick import form_store
    events, _ = form_store.since("contact", "0")
    assert events and events[0]["message"] == "hello"


def test_form_disabled_when_triggers_off(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, triggers="0")
    r = _client().post("/form/x", json={"a": 1})
    assert r.status_code == 404


def test_invalid_json_is_400(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    r = _client().post("/form/x", content=b"{not json",
                       headers={"content-type": "application/json"})
    assert r.status_code == 400


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
