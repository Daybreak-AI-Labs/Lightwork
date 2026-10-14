"""Ekko dashboard lifecycle, discovery, and preview-only authoring handoff."""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})


def _isolate(tmp_path, monkeypatch, *, enabled: bool = True):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "\n".join([
            "[ekko]",
            f"enable = {'true' if enabled else 'false'}",
            'capture_level = "application_metadata"',
            'allowed_apps = ["excel", "powerpoint"]',
            (
                'blocked_apps = ["email", "outlook", "gmail", "chat", "teams", '
                '"slack", "crm", "salesforce", "erp", "sap", "database"]'
            ),
            "retention_days = 14",
            "min_occurrences = 2",
            "min_distinct_days = 2",
            "poll_interval_seconds = 5",
            "provider_egress = false",
            "",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(cfg))
    for name in ("MAVERICK_EKKO", "MAVERICK_CONFIG_OVERLAY", "MAVERICK_TENANT"):
        monkeypatch.delenv(name, raising=False)
    from maverick.config import reset_config_cache

    reset_config_cache()
    return cfg


def _enroll(owner: str | None = None, device: str = "desk"):
    from maverick.config import get_ekko_policy
    from maverick.work_discovery_identity import local_os_principal
    from maverick.work_discovery_store import WorkDiscoveryStore

    store = WorkDiscoveryStore(owner or local_os_principal(), device)
    store.enroll(get_ekko_policy())
    return store


def _seed_recurring_evidence(store) -> None:
    from maverick.work_discovery import SessionState, WorkEvent

    policy = store.get_policy()
    assert policy is not None
    first_day = datetime.now(timezone.utc).date() - timedelta(days=2)
    base = datetime.combine(first_day, time(hour=12), tzinfo=timezone.utc).timestamp()
    for day in range(2):
        session = store.create_session(
            policy=policy,
            session_id=f"repeat-{day}",
            started_at=base + day * 86_400,
        )
        collector_id = f"collector-seed-{day}"
        store.claim_collector(
            session.session_id,
            collector_id,
            policy=policy,
            ttl_seconds=30,
        )
        store.append_event(WorkEvent(
            event_id=f"event-{day}-1",
            session_id=session.session_id,
            sequence=1,
            occurred_at=base + day * 86_400 + 10,
            app="excel",
            action="switch",
            object_type="none",
            duration_seconds=120,
        ), policy=policy, collector_id=collector_id)
        store.append_event(WorkEvent(
            event_id=f"event-{day}-2",
            session_id=session.session_id,
            sequence=2,
            occurred_at=base + day * 86_400 + 140,
            app="powerpoint",
            action="switch",
            object_type="none",
            duration_seconds=480,
        ), policy=policy, collector_id=collector_id)
        store.transition_session(
            session.session_id,
            SessionState.STOPPED,
            at=base + day * 86_400 + 700,
        )


def test_ekko_page_and_agent_factory_entry_explain_the_safe_boundary(
    tmp_path, monkeypatch,
):
    _isolate(tmp_path, monkeypatch, enabled=False)

    page = client.get("/ekko")
    assert page.status_code == 200
    for phrase in (
        "EKKO",
        "Work Discovery",
        "Ekko is off by default",
        "Never captures",
        "not employee productivity",
        "lightwork.authoring-handoff",
        'id="ekko-enroll-go"',
        'id="ekko-enroll-days"',
        '<select class="select" id="ekko-device"',
    ):
        assert phrase in page.text
    assert "collector online" not in page.text.lower()
    assert "recorder lost contact" in page.text.lower()
    from test_support.plain_language import developer_terms
    assert developer_terms(page.text) == []
    from test_support.plain_language import visible_text
    content = visible_text(page.text[page.text.index("<main"):])
    for gone in ("maverick ekko", "tenant", "egress", "DGM", "collector", "Device ID"):
        assert gone not in content
    assert "Request failed (" not in page.text

    factory = client.get("/agents")
    assert factory.status_code == 200
    assert 'id="ekko-work-discovery-card"' in factory.text
    assert 'href="/ekko"' in factory.text


@pytest.mark.parametrize("enabled", [False, True])
def test_ekko_initial_render_runs_after_shared_ui_helpers(tmp_path, monkeypatch, enabled):
    """The immediate status render needs mvEl before its client script executes."""
    _isolate(tmp_path, monkeypatch, enabled=enabled)
    page = client.get("/ekko")
    assert page.status_code == 200
    shared_helpers = page.text.index('<script src="/static/lightwork-ui.js')
    client_start = page.text.index("var initial = JSON.parse(document.getElementById('ekko-initial')")
    initial_render = page.text.index("renderStatus(initial);", client_start)
    assert shared_helpers < client_start < initial_render


def test_status_is_default_off_and_payload_cannot_inject_owner_or_tenant(
    tmp_path, monkeypatch,
):
    _isolate(tmp_path, monkeypatch, enabled=False)
    status = client.get("/api/v1/ekko/status?device_id=desk")
    assert status.status_code == 200
    assert status.json()["enabled"] is False
    assert status.json()["enrollment"] is None

    injected = client.post(
        "/api/v1/ekko/sessions",
        json={
            "device_id": "desk",
            "acknowledge_capture_scope": True,
            "owner": "user:mallory",
            "tenant": "other-client",
        },
    )
    assert injected.status_code == 422


def test_owner_bound_session_start_pause_resume_stop(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _enroll(device="desk")

    missing_ack = client.post(
        "/api/v1/ekko/sessions",
        json={"device_id": "desk", "acknowledge_capture_scope": False},
    )
    assert missing_ack.status_code == 400

    started = client.post(
        "/api/v1/ekko/sessions",
        json={"device_id": "desk", "acknowledge_capture_scope": True},
    )
    assert started.status_code == 201
    session_id = started.json()["session"]["session_id"]
    assert started.json()["session"]["state"] == "running"
    assert started.json()["unsaved"] is True

    for action, expected in (
        ("pause", "paused"),
        ("resume", "running"),
        ("stop", "stopped"),
    ):
        response = client.post(
            f"/api/v1/ekko/sessions/{session_id}/{action}",
            json={"device_id": "desk"},
        )
        assert response.status_code == 200
        assert response.json()["session"]["state"] == expected

    # A different device selector remains inside the same owner but cannot see
    # or mutate the enrolled device's session.
    hidden = client.get("/api/v1/ekko/status?device_id=other-device").json()
    assert hidden["session"] is None and hidden["event_count"] == 0
    denied = client.post(
        f"/api/v1/ekko/sessions/{session_id}/pause",
        json={"device_id": "other-device"},
    )
    assert denied.status_code == 404


def test_mining_and_handoff_are_evidence_backed_preview_only(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    _isolate(tmp_path, monkeypatch)
    store = _enroll(device="desk")
    _seed_recurring_evidence(store)

    mined = client.post(
        "/api/v1/ekko/candidates/mine",
        json={"device_id": "desk"},
    )
    assert mined.status_code == 200
    assert mined.json()["count"] >= 1
    candidate = mined.json()["candidates"][0]
    assert candidate["occurrences"] >= 2
    assert candidate["distinct_days"] == 2
    assert candidate["evidence"]

    handoff = client.post(
        f"/api/v1/ekko/candidates/{candidate['opportunity_id']}/handoff",
        json={"device_id": "desk", "kind": "flow"},
    )
    assert handoff.status_code == 200
    body = handoff.json()
    assert body["unsaved"] is True
    assert body["requires_human_approval"] is True
    assert body["redirect"] == "/flows/designer"
    assert body["context"] == client.get("/api/v1/templates/draft-context").json()["context"]
    assert body["flow"]["start"] == "human-review"
    assert body["flow"]["version"] == 0
    assert body["flow"]["revision"] == ""
    nodes = {node["id"]: node for node in body["flow"]["nodes"]}
    assert nodes["human-review"]["kind"] == "approval"
    assert nodes["human-review"]["next"] == "observed-1"
    assert nodes["observed-1"]["next"] == "observed-2"
    assert nodes["observed-2"].get("next") is None
    assert "approved, content-free application metadata" in body["brief"]
    assert "do not" in body["brief"].lower()

    saved = client.post("/api/v1/flows", json={"flow": body["flow"]})
    assert saved.status_code == 201, saved.text
    assert saved.json()["published"] is False
    again = client.post(
        f"/api/v1/ekko/candidates/{candidate['opportunity_id']}/handoff",
        json={"device_id": "desk", "kind": "flow"},
    )
    assert again.status_code == 200
    assert again.json()["flow"]["id"] != body["flow"]["id"]

    erased = client.delete("/api/v1/ekko/data?device_id=desk")
    assert erased.status_code == 200
    assert erased.json()["erased"]["sessions"] == 2
    assert erased.json()["erased"]["events"] == 4
    assert client.get(
        "/api/v1/ekko/candidates?device_id=desk"
    ).json()["candidates"] == []


def test_workflow_handoff_refuses_off_engine_before_unusable_editor(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_FLOWS", "0")
    _isolate(tmp_path, monkeypatch)
    store = _enroll(device="desk")
    _seed_recurring_evidence(store)
    status = client.get("/api/v1/ekko/status?device_id=desk").json()
    assert status["workflows_enabled"] is False
    assert status["workflows_control_managed"] is True
    opportunity_id = client.get("/api/v1/ekko/candidates?device_id=desk").json()["candidates"][0]["opportunity_id"]

    refused = client.post(
        f"/api/v1/ekko/candidates/{opportunity_id}/handoff",
        json={"device_id": "desk", "kind": "flow"},
    )
    assert refused.status_code == 409
    assert "installation" in refused.json()["detail"]
    assert "/learning#lrn-flows-on" not in client.get("/ekko?device_id=desk").text
    assert client.post(
        f"/api/v1/ekko/candidates/{opportunity_id}/handoff",
        json={"device_id": "desk", "kind": "agent"},
    ).status_code == 200

    monkeypatch.delenv("MAVERICK_FLOWS")
    status = client.get("/api/v1/ekko/status?device_id=desk").json()
    assert status["workflows_enabled"] is False
    assert status["workflows_control_managed"] is False
    assert "/learning#lrn-flows-on" in client.get("/ekko?device_id=desk").text
    refused = client.post(
        f"/api/v1/ekko/candidates/{opportunity_id}/handoff",
        json={"device_id": "desk", "kind": "flow"},
    )
    assert refused.status_code == 409
    assert "Learning" in refused.json()["detail"]


def test_forget_device_revokes_enrollment_and_erases_data(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    store = _enroll(device="desk")
    session = store.create_session(policy=store.get_policy())

    forgotten = client.delete("/api/v1/ekko/device?device_id=desk")

    assert forgotten.status_code == 200
    assert forgotten.json()["forgotten"]["enrollments"] == 1
    status = client.get("/api/v1/ekko/status?device_id=desk").json()
    assert status["enrollment"] is None
    assert status["session"] is None
    assert store.get_session(session.session_id) is None


def test_ekko_inline_script_runs_after_shared_ui_helpers_load(tmp_path, monkeypatch):
    # Regression: the page script calls renderStatus(initial) at parse time and
    # uses mvEl/mvToast/mvConfirm from /static/lightwork-ui.js, which base.html
    # loads after the content block. Emitting the script inside the content block
    # threw ``ReferenceError: mvEl is not defined`` on every page load.
    _isolate(tmp_path, monkeypatch, enabled=False)

    page = client.get("/ekko")
    assert page.status_code == 200
    html = page.text

    ui_tag = '<script src="/static/lightwork-ui.js'
    # The bootstrap must be a <script> element, not any element with these attributes.
    bootstrap = re.search(r'<script\b[^>]*\bid="ekko-initial"[^>]*type="application/json">', html)
    assert ui_tag in html
    assert bootstrap is not None

    first_mv_el = html.index("mvEl(")
    inline_script_open = html.rfind('<script nonce="', 0, first_mv_el)
    assert inline_script_open != -1
    assert inline_script_open > html.index(ui_tag)
    # The JSON bootstrap must still precede the code that reads it.
    assert bootstrap.start() < inline_script_open


def test_enroll_from_the_page_narrows_apps_and_is_audited(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    status = client.get("/api/v1/ekko/status?device_id=desk").json()
    assert status["app_choices"] == [
        {"value": "excel", "label": "Microsoft Excel"},
        {"value": "powerpoint", "label": "Microsoft PowerPoint"},
    ]
    assert status["enrollment_days"] == 30

    base = {"device_id": "desk", "apps": ["excel"], "days": 14}
    assert client.post("/api/v1/ekko/enroll", json=base).status_code == 400  # no consent tick
    outside = client.post("/api/v1/ekko/enroll", json={**base, "apps": ["slack"], "acknowledge_capture_scope": True})
    assert outside.status_code == 422
    assert "apps your administrator allows" in outside.json()["detail"]
    assert client.post("/api/v1/ekko/enroll", json={**base, "days": 365, "acknowledge_capture_scope": True}).status_code == 422
    injected = client.post("/api/v1/ekko/enroll", json={**base, "acknowledge_capture_scope": True, "owner": "user:mallory"})
    assert injected.status_code == 422

    kinds: list[tuple[str, dict]] = []
    from maverick_dashboard import ekko_routes

    real = ekko_routes._audit

    def spy(kind, **payload):
        kinds.append((str(kind), payload))
        return real(kind, **payload)

    monkeypatch.setattr(ekko_routes, "_audit", spy)
    enrolled = client.post("/api/v1/ekko/enroll", json={**base, "acknowledge_capture_scope": True})
    assert enrolled.status_code == 200, enrolled.text
    body = enrolled.json()
    assert body["status"]["enrollment"]["active"] is True
    assert body["status"]["allowed_apps"] == ["excel"]  # narrowed, never widened
    assert [p.get("state") for _, p in kinds] == ["enroll_authorized", "enrolled"]
    assert kinds[0][1]["required"] is True
    # enrolling only records consent; nothing is watched yet
    assert body["status"]["session"] is None

    started = client.post("/api/v1/ekko/sessions", json={"device_id": "desk", "acknowledge_capture_scope": True})
    assert started.status_code == 201
    revoked = client.post("/api/v1/ekko/revoke", json={"device_id": "desk"})
    assert revoked.status_code == 200 and revoked.json()["revoked"] is True
    after = revoked.json()["status"]
    assert not (after["enrollment"] or {}).get("active")
    assert after["session"]["state"] == "stopped"


def test_enroll_needs_ekko_on_and_operate(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch, enabled=False)
    off = client.post("/api/v1/ekko/enroll", json={"device_id": "desk", "apps": ["excel"],
                                                   "acknowledge_capture_scope": True})
    assert off.status_code == 409
    assert "turned off" in off.json()["detail"]
    from maverick_dashboard import auth

    def deny(request, permission):
        from fastapi import HTTPException
        assert permission == "operate"
        raise HTTPException(status_code=403, detail="no")

    monkeypatch.setattr(auth, "require_permission", deny)
    assert client.post("/api/v1/ekko/enroll", json={"device_id": "desk", "apps": ["excel"],
                                                    "acknowledge_capture_scope": True}).status_code == 403
    assert client.post("/api/v1/ekko/revoke", json={"device_id": "desk"}).status_code == 403
