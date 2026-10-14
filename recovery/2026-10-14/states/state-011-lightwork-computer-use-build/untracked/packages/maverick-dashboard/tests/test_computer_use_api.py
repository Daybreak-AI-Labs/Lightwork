from __future__ import annotations

import threading

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from maverick.computer_use_runtime import ComputerRuntimeStore
from maverick_dashboard import computer_use_api as api

BASE = "/api/v1/computer-use"


class _Runtime:
    def __init__(self, store: ComputerRuntimeStore, readiness: dict | None = None) -> None:
        self._store = store
        self._readiness = readiness or {
            "ready": True,
            "checked_at": "2026-09-04T12:00:00Z",
            "backend": "remote",
            "backend_ready": True,
            "execution_cell_enrolled": True,
            "approval_ready": True,
            "stop_path_ready": True,
            "image_tool_supported": True,
            "visual_evidence_required": True,
            "screenshot_custody_ready": True,
            "blockers": [],
            "warnings": [],
        }

    def readiness(self):
        return dict(self._readiness)

    def __getattr__(self, name: str):
        return getattr(self._store, name)


@pytest.fixture()
def console(tmp_path, monkeypatch):
    state = {
        "tenant": "tenant-a",
        "principal": "alice@example.test",
        "permissions": {"view", "operate"},
    }
    store = ComputerRuntimeStore(tmp_path / "runtime")
    runtime = _Runtime(store)

    def require(_request, permission: str) -> None:
        if permission not in state["permissions"]:
            raise HTTPException(status_code=403, detail="forbidden")

    monkeypatch.setattr(api, "require_permission", require)
    monkeypatch.setattr(
        api,
        "has_permission",
        lambda _request, permission: permission in state["permissions"],
    )
    monkeypatch.setattr(api, "caller_principal", lambda _request: state["principal"])
    monkeypatch.setattr(api, "_identity", lambda: (state["tenant"], state["principal"]))
    monkeypatch.setattr(api, "_require_same_origin", lambda _request: None)

    app = FastAPI()
    app.state.computer_use_runtime = runtime
    app.include_router(api.create_computer_use_router(), prefix="/api/v1")
    client = TestClient(app, headers={"Origin": "http://testserver"})
    return client, store, runtime, state


def test_read_routes_require_view_authority(console) -> None:
    client, _store, _runtime, state = console
    state["permissions"] = set()
    assert client.get(f"{BASE}/readiness").status_code == 403
    assert client.get(f"{BASE}/sessions").status_code == 403


@pytest.mark.parametrize("missing", ("approval_ready", "stop_path_ready"))
def test_readiness_refuses_safety_contract_drift(console, missing: str) -> None:
    client, _store, runtime, _state = console
    runtime._readiness.pop(missing)

    response = client.get(f"{BASE}/readiness")

    assert response.status_code == 503
    assert response.json()["detail"] == "readiness contract is invalid"


def test_tenant_and_owner_isolation_fail_closed(console) -> None:
    client, store, _runtime, state = console
    session = store.create_session("tenant-a", "alice@example.test")
    store.create_session("tenant-b", "other@example.test")

    tenant_a = client.get(f"{BASE}/sessions")
    assert tenant_a.status_code == 200
    assert [item["session_id"] for item in tenant_a.json()["items"]] == [
        session.session_id
    ]

    state["tenant"] = "tenant-b"
    assert client.get(f"{BASE}/sessions/{session.session_id}").status_code == 404

    state["tenant"] = "tenant-a"
    state["principal"] = "bob@example.test"
    state["permissions"] = {"view"}
    assert client.get(f"{BASE}/sessions/{session.session_id}").status_code == 404

    state["principal"] = "alice@example.test"
    allowed = client.get(f"{BASE}/sessions/{session.session_id}")
    assert allowed.status_code == 200
    assert allowed.json()["principal"] == "alice@example.test"


def test_create_requires_operate_live_readiness_and_matching_backend(console) -> None:
    client, store, runtime, state = console
    body = {
        "surface": "browser",
        "backend": "remote",
        "monitor_id": 2,
        "goal_id": None,
    }

    state["permissions"] = {"view"}
    assert client.post(f"{BASE}/sessions", json=body).status_code == 403

    state["permissions"] = {"view", "operate"}
    runtime._readiness = {
        **runtime._readiness,
        "ready": False,
        "blockers": [{"code": "cell", "message": "Execution cell is offline"}],
    }
    blocked = client.post(f"{BASE}/sessions", json=body)
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "Execution cell is offline"
    assert store.list_sessions("tenant-a") == []

    runtime._readiness = {**runtime._readiness, "ready": True, "blockers": []}
    mismatch = client.post(f"{BASE}/sessions", json={**body, "backend": "host"})
    assert mismatch.status_code == 409

    created = client.post(f"{BASE}/sessions", json=body)
    assert created.status_code == 201, created.text
    row = created.json()["session"]
    assert row["state"] == "ready"
    assert row["surface"] == "browser"
    assert row["monitor_id"] == 2
    assert row["principal"] == "alice@example.test"


def test_controls_use_revision_cas_and_record_state_transitions(console) -> None:
    client, store, _runtime, _state = console
    session = store.create_session("tenant-a", "alice@example.test")

    paused = client.post(
        f"{BASE}/sessions/{session.session_id}/pause",
        json={"expected_revision": session.revision},
    )
    assert paused.status_code == 200
    assert paused.json()["session"]["state"] == "paused"
    paused_revision = paused.json()["session"]["revision"]

    stale = client.post(
        f"{BASE}/sessions/{session.session_id}/resume",
        json={"expected_revision": session.revision},
    )
    assert stale.status_code == 409

    resumed = client.post(
        f"{BASE}/sessions/{session.session_id}/resume",
        json={"expected_revision": paused_revision},
    )
    assert resumed.status_code == 200
    assert resumed.json()["session"]["state"] == "running"

    stopped = client.post(
        f"{BASE}/sessions/{session.session_id}/stop",
        json={
            "expected_revision": resumed.json()["session"]["revision"],
            "reason": "operator observed unexpected credential prompt",
        },
    )
    assert stopped.status_code == 200
    assert stopped.json()["session"]["state"] == "stopped"
    assert stopped.json()["session"]["stop_reason"] == (
        "operator observed unexpected credential prompt"
    )


def test_cross_origin_mutations_are_refused_before_runtime_use(
    console, monkeypatch
) -> None:
    client, store, _runtime, _state = console
    session = store.create_session("tenant-a", "alice@example.test")
    monkeypatch.setattr(
        api,
        "_require_same_origin",
        lambda _request: (_ for _ in ()).throw(
            HTTPException(status_code=403, detail="cross-origin request refused")
        ),
    )

    response = client.post(
        f"{BASE}/sessions/{session.session_id}/stop",
        json={"expected_revision": session.revision},
    )

    assert response.status_code == 403
    assert store.get_session(session.session_id, "tenant-a").state.value == "ready"


def test_event_long_poll_returns_new_revision_without_cross_tenant_leak(console) -> None:
    client, store, _runtime, state = console
    session = store.create_session("tenant-a", "alice@example.test")
    timer = threading.Timer(
        0.05,
        lambda: store.heartbeat(session.session_id, "tenant-a", "alice@example.test"),
    )
    timer.start()
    try:
        response = client.get(
            f"{BASE}/sessions/{session.session_id}/events/wait",
            params={"after_revision": session.revision, "timeout_ms": 1500},
        )
    finally:
        timer.cancel()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["timed_out"] is False
    assert [event["kind"] for event in body["events"]] == ["session.heartbeat"]
    assert body["next_cursor"] > session.revision

    state["tenant"] = "tenant-b"
    assert client.get(
        f"{BASE}/sessions/{session.session_id}/events/wait",
        params={"after_revision": 0, "timeout_ms": 0},
    ).status_code == 404


def test_frame_projection_distinguishes_hash_from_custody_seal(console) -> None:
    client, store, _runtime, _state = console
    session = store.create_session("tenant-a", "alice@example.test")
    observation = store.record_observation(
        session.session_id,
        "tenant-a",
        "alice@example.test",
        image=b"not-a-real-png-but-retained-bytes",
        width=8,
        height=6,
        scale_factor=1.0,
        monitor_id=1,
        left=0,
        top=0,
        active_window="Test",
    )

    events = client.get(f"{BASE}/sessions/{session.session_id}/events").json()["events"]
    captured = next(event for event in events if event["kind"] == "observation.captured")
    media = captured["payload"]["media"]
    assert media["available"] is True
    assert media["content_integrity_check_available"] is True
    assert media["custody_sealed"] is (observation.seal_status == "sealed")
    if observation.seal_status != "sealed":
        assert "content hash" in media["custody_reason"]
        assert "no verified custody seal" in media["custody_reason"]

    frame = client.get(
        f"{BASE}/sessions/{session.session_id}/observations/"
        f"{observation.observation_id}/media"
    )
    assert frame.status_code == 200
    assert frame.content == b"not-a-real-png-but-retained-bytes"
    assert frame.headers["x-lightwork-content-integrity"] == "sha256-checked"
    assert frame.headers["x-lightwork-custody-seal"] == observation.seal_status
