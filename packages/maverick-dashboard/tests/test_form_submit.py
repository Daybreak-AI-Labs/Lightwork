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
    from maverick_dashboard import app as dashboard_app

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    dashboard_app._goal_times.clear()
    dashboard_app._goal_times_global.clear()


def _register_form(token: str, *, tenant: str = "", name: str = "", owner: str = "") -> None:
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
        flow_revision=release["release_id"], tenant=tenant, owner=owner,
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


def test_new_form_delivers_first_submission_before_first_poll(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store as flow_store
    from maverick.flow.ir import Flow, FlowNode

    saved = flow_store.save_flow(Flow(
        id="first-form-flow", name="First form", start="a",
        nodes={"a": FlowNode(id="a", kind="agent", brief="Review {{name}}")},
    ))
    flow_store.publish_flow(
        saved.id, expected_version=saved.version, expected_revision=saved.revision,
    )
    client = _client()
    created = client.post("/api/v1/event-triggers", json={
        "name": "first-form", "source": "form", "flow": saved.id,
        "config": {"token": "first-form-token"},
    })
    assert created.status_code == 201
    from maverick_dashboard import event_triggers_store

    assert event_triggers_store.get_trigger("first-form", tenant="")["cursor"] == "0"
    assert client.post("/form/first-form-token", json={"name": "first"}).status_code == 201
    result = client.post("/api/v1/event-triggers/poll?name=first-form")
    assert result.status_code == 200
    fired = result.json()["triggers"][0]["fired_flows"]
    assert len(fired) == 1
    assert flow_store.load_run(fired[0]).data["name"] == "first"


def test_form_backlog_drains_in_bounded_polls(monkeypatch, tmp_path):
    from collections import deque

    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("MAVERICK_DASHBOARD_MAX_GOALS_PER_MIN", "100")
    monkeypatch.setenv("MAVERICK_DASHBOARD_MAX_GOALS_GLOBAL_PER_MIN", "1000")
    _register_form("batch")
    from maverick_dashboard import app as dashboard_app
    from maverick_dashboard import automation_queue

    monkeypatch.setattr(dashboard_app, "_goal_times", {})
    monkeypatch.setattr(dashboard_app, "_goal_times_global", deque())
    dispatched = []

    def record_run(*args, **kwargs):
        dispatched.append(args)
        return f"run-{len(dispatched)}"

    monkeypatch.setattr(automation_queue, "enqueue_published_flow_run", record_run)
    client = _client()
    for n in range(26):
        response = client.post("/form/batch", json={"name": str(n)})
        assert response.status_code == 201
    first = client.post("/api/v1/event-triggers/poll?name=accept-batch")
    second = client.post("/api/v1/event-triggers/poll?name=accept-batch")
    assert first.status_code == second.status_code == 200
    first_batch = first.json()["triggers"][0]
    second_batch = second.json()["triggers"][0]
    assert first_batch["poll_error"] is second_batch["poll_error"] is False
    assert (first_batch["new_events"], second_batch["new_events"]) == (25, 1)
    assert [args[1]["name"] for args in dispatched] == [str(n) for n in range(26)]


def test_accepted_form_cannot_rebind_or_delete_before_original_flow_is_queued(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("lead", name="intake")
    from maverick.flow import store as flow_store
    from maverick.flow.ir import Flow, FlowNode
    from maverick_dashboard import event_triggers_store

    second = flow_store.save_flow(Flow(
        id="second-flow", name="Second flow", start="a",
        nodes={"a": FlowNode(id="a", kind="agent", brief="Review {{name}}")},
    ))
    flow_store.publish_flow(
        second.id, expected_version=second.version, expected_revision=second.revision,
    )
    client = _client()
    assert client.post("/form/lead", json={"name": "accepted for A"}).status_code == 201
    listed = client.get("/api/v1/event-triggers").json()["triggers"]
    assert next(t for t in listed if t["name"] == "intake")["pending_submissions"] == 1
    with pytest.raises(ValueError, match="managed automatically"):
        event_triggers_store.set_trigger(
            "intake", "", "form", config={"token": "lead"},
            flow="published-form-flow", cursor="1", tenant="",
        )
    assert not event_triggers_store.set_cursor("intake", "1", tenant="")
    rebind = client.post("/api/v1/event-triggers", json={
        "name": "intake", "source": "form", "flow": second.id,
        "config": {"token": "lead"},
    })
    assert rebind.status_code == 409 and "Check now" in rebind.json()["detail"]
    deletion = client.delete("/api/v1/event-triggers/intake")
    assert deletion.status_code == 409 and "Check now" in deletion.json()["detail"]
    assert event_triggers_store.get_trigger("intake", tenant="")["flow"] == "published-form-flow"

    polled = client.post("/api/v1/event-triggers/poll?name=intake")
    assert polled.status_code == 200
    run_id = polled.json()["triggers"][0]["fired_flows"][0]
    run = flow_store.load_run(run_id)
    assert run.flow_id == "published-form-flow" and run.data["name"] == "accepted for A"
    assert next(t for t in client.get("/api/v1/event-triggers").json()["triggers"]
                if t["name"] == "intake")["pending_submissions"] == 0
    with pytest.raises(ValueError, match="managed automatically"):
        event_triggers_store.set_trigger(
            "intake", "", "form", config={"token": "lead"},
            flow="published-form-flow", cursor="0", tenant="",
        )
    assert client.post("/api/v1/event-triggers", json={
        "name": "intake", "source": "form", "flow": second.id,
        "config": {"token": "lead"},
    }).status_code == 201
    assert client.post("/form/lead", json={"name": "accepted for B"}).status_code == 201
    polled_b = client.post("/api/v1/event-triggers/poll?name=intake")
    run_b = flow_store.load_run(polled_b.json()["triggers"][0]["fired_flows"][0])
    assert run_b.flow_id == "second-flow" and run_b.data["name"] == "accepted for B"
    assert client.delete("/api/v1/event-triggers/intake").status_code == 200


def test_form_template_parameters_cannot_change_while_a_submission_waits(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.templates import Template
    from maverick_dashboard import event_triggers_store
    from maverick_dashboard.triggers_store import snapshot_from_template

    template = Template(name="contact", title="Contact {{name}}", body="Review {{name}}", params=["name"])
    snapshot = snapshot_from_template(template)
    event_triggers_store.set_trigger(
        "contact", "contact", "form", config={"token": "contact"},
        params={"name": "original"}, template_snapshot=snapshot,
    )
    client = _client()
    assert client.post("/form/contact", json={"name": "accepted"}).status_code == 201
    with pytest.raises(event_triggers_store.FormTriggerPending):
        event_triggers_store.set_trigger(
            "contact", "contact", "form", config={"token": "contact"},
            params={"name": "changed"}, template_snapshot=snapshot,
        )
    assert event_triggers_store.get_trigger("contact", tenant="")["params"]["name"] == "original"
    import maverick.runner as runner_mod

    monkeypatch.setattr(runner_mod, "run_goal_in_thread", lambda *a, **k: None)
    polled = client.post("/api/v1/event-triggers/poll?name=contact")
    assert polled.status_code == 200
    goal_id = polled.json()["triggers"][0]["fired"][0]
    from maverick.world_model import WorldModel

    assert WorldModel(tmp_path / "world.db").get_goal(goal_id).title == "Contact accepted"
    assert event_triggers_store.pending_form_submissions(
        event_triggers_store.get_trigger("contact", tenant="")
    ) == 0


def test_unavailable_original_form_flow_keeps_accepted_submission_pending(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("stale", name="intake")
    from maverick.flow import store as flow_store
    from maverick_dashboard import event_triggers_store

    client = _client()
    assert client.post("/form/stale", json={"name": "accepted"}).status_code == 201
    published = flow_store.load_published_bundle("published-form-flow")
    assert published is not None
    flow = published[0]
    assert flow_store.unpublish_flow(
        flow.id, expected_version=flow.version, expected_revision=flow.revision,
    )
    polled = client.post("/api/v1/event-triggers/poll?name=intake")
    assert polled.status_code == 200
    assert polled.json()["triggers"][0]["fired_flows"] == []
    assert event_triggers_store.pending_form_submissions(
        event_triggers_store.get_trigger("intake", tenant="")
    ) == 1
    assert client.delete("/api/v1/event-triggers/intake").status_code == 409


def test_empty_form_can_be_changed_and_deleted_before_a_first_submission(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("empty", name="intake")
    from maverick_dashboard import event_triggers_store

    assert event_triggers_store.pending_form_submissions(
        event_triggers_store.get_trigger("intake", tenant="")
    ) == 0
    changed = event_triggers_store.set_trigger(
        "intake", "", "form", config={"token": "empty"},
        flow="published-form-flow", params={"department": "sales"}, tenant="",
    )
    assert changed["params"] == {"department": "sales"}
    assert _client().delete("/api/v1/event-triggers/intake").status_code == 200
    assert not list(tmp_path.rglob("*.ndjson"))


def test_missing_or_corrupt_form_history_blocks_changes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("history", name="intake")
    from maverick import form_store
    from maverick_dashboard import event_triggers_store

    client = _client()
    assert client.post("/form/history", json={"name": "accepted"}).status_code == 201
    file = form_store._path("history")
    with file.open("a", encoding="utf-8") as fh:
        fh.write("{damaged\n")
    assert next(t for t in client.get("/api/v1/event-triggers").json()["triggers"]
                if t["name"] == "intake")["pending_submissions"] is None
    assert client.delete("/api/v1/event-triggers/intake").status_code == 503
    with pytest.raises(form_store.FormStoreUnavailable):
        event_triggers_store.set_trigger(
            "intake", "", "form", config={"token": "history"},
            flow="other-flow", tenant="",
        )
    file.write_text('{"seq":1,"ts":0,"fields":{"name":"accepted"}}\n', encoding="utf-8")
    assert client.post("/api/v1/event-triggers/poll?name=intake").status_code == 200
    file.unlink()
    assert client.delete("/api/v1/event-triggers/intake").status_code == 503


def test_form_rebind_is_refused_after_restart_until_original_rows_are_drained(monkeypatch, tmp_path):
    import subprocess
    import sys

    _isolate(monkeypatch, tmp_path)
    _register_form("restart", name="intake")
    assert _client().post("/form/restart", json={"name": "saved"}).status_code == 201
    script = (
        "from maverick_dashboard import event_triggers_store as s\n"
        "t=s.get_trigger('intake', tenant='')\n"
        "assert s.pending_form_submissions(t)==1\n"
        "try:\n"
        " s.set_trigger('intake','', 'form', config={'token':'restart'}, "
        "flow='other-flow', tenant='')\n"
        "except s.FormTriggerPending:\n"
        " print('protected')\n"
        "else:\n"
        " raise AssertionError('rebind was accepted')\n"
    )
    child = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        timeout=30, check=False,
    )
    assert child.returncode == 0, child.stderr
    assert "protected" in child.stdout


def test_public_post_rechecks_binding_if_it_changes_during_body_read(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    _isolate(monkeypatch, tmp_path)
    _register_form("race", name="intake")
    from maverick import form_store
    from maverick_dashboard import app as dashboard_app
    from maverick_dashboard import event_triggers_store

    entered, resume = Event(), Event()
    original = dashboard_app._append_registered_form

    def paused(trigger, token, fields):
        entered.set()
        assert resume.wait(10)
        return original(trigger, token, fields)

    monkeypatch.setattr(dashboard_app, "_append_registered_form", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending_post = pool.submit(lambda: _client().post("/form/race", json={"name": "old"}))
        assert entered.wait(10)
        event_triggers_store.set_trigger(
            "intake", "", "form", config={"token": "race"},
            flow="different-flow", tenant="",
        )
        resume.set()
        assert pending_post.result(timeout=10).status_code == 404
    assert not list(tmp_path.rglob("*.ndjson"))
    assert form_store.pending("race", "0") == 0


def test_public_append_blocks_rebind_until_pending_row_is_visible(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    _isolate(monkeypatch, tmp_path)
    _register_form("race", name="intake")
    from maverick import form_store
    from maverick_dashboard import event_triggers_store

    entered, resume = Event(), Event()
    original = form_store.append

    def paused(*args, **kwargs):
        entered.set()
        assert resume.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(form_store, "append", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending_post = pool.submit(lambda: _client().post("/form/race", json={"name": "original"}))
        assert entered.wait(10)
        pending_rebind = pool.submit(
            event_triggers_store.set_trigger, "intake", "", "form",
            config={"token": "race"}, flow="different-flow", tenant="",
        )
        resume.set()
        assert pending_post.result(timeout=10).status_code == 201
        with pytest.raises(event_triggers_store.FormTriggerPending):
            pending_rebind.result(timeout=10)
    assert event_triggers_store.get_trigger("intake", tenant="")["flow"] == "published-form-flow"


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


def test_oversized_json_form_fields_are_rejected_without_truncation(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("sizing")
    client = _client()
    responses = [
        client.post("/form/sizing", json={str(n): "x" for n in range(51)}),
        client.post("/form/sizing", json={"answer": "x" * 2001}),
        client.post("/form/sizing", json={"n" * 201: "x"}),
    ]
    assert [response.status_code for response in responses] == [400, 400, 400]
    assert not list(tmp_path.rglob("*.ndjson"))


def test_revoked_form_owner_cannot_acknowledge_submission(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("owned", owner="user:departed")
    from maverick_dashboard import scim_groups

    monkeypatch.setattr(scim_groups, "active_for_principal", lambda _owner: False)
    response = _client().post("/form/owned", json={"name": "first"})
    assert response.status_code == 503
    assert response.json()["detail"] == "This form is not accepting submissions."
    assert not list(tmp_path.rglob("*.ndjson"))


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
    assert response.json()["detail"] == (
        "This form has reached its submission limit. Please contact the form owner."
    )
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


def test_second_form_cannot_reuse_the_same_storage_key(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _register_form("lead.v1")
    from maverick_dashboard import event_triggers_store

    with pytest.raises(event_triggers_store.FormTriggerConflict):
        _register_form("lead-v1", name="second-form")
    client = _client()
    assert client.post("/form/lead.v1", json={"name": "first"}).status_code == 201
    assert client.post("/form/lead-v1", json={"name": "second"}).status_code == 404
    assert len(list(tmp_path.rglob("*.ndjson"))) == 1


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
