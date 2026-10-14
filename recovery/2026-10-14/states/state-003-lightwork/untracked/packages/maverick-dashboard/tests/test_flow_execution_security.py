"""Security regressions for queued dashboard flow execution."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _client():
    from maverick_dashboard.app import app
    return TestClient(app, headers={"Origin": "http://testserver"})


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")


def test_dashboard_run_is_pinned_before_a_later_flow_edit(monkeypatch, tmp_path):
    """A queued manual run must use the definition its starter authorized."""
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import execution, store
    from maverick_dashboard import automation_queue as aq

    monkeypatch.setattr(aq, "_queue", None)
    seen = []
    monkeypatch.setattr(
        execution, "default_agent_runner",
        lambda world, **kwargs: lambda brief, data: (seen.append(brief) or "ok", 1.0),
    )
    monkeypatch.setattr(
        execution, "default_action_runner",
        lambda world, sandbox=None: lambda tool, params, data: ("", None),
    )
    client = _client()
    original = {
        "id": "pin", "name": "Pinned", "start": "a",
        "nodes": [{"id": "a", "kind": "agent", "brief": "safe work"}],
    }
    changed = {
        "id": "pin", "name": "Edited", "start": "a",
        "nodes": [{"id": "a", "kind": "agent", "brief": "unsafe replacement"}],
    }
    assert client.post("/api/v1/flows", json={"flow": original}).status_code == 201
    run_id = client.post("/api/v1/flows/pin/run", json={"data": {}}).json()["run_id"]
    queued = store.load_run(run_id)
    assert queued.flow_version == 1 and queued.flow_snapshot["nodes"][0]["brief"] == "safe work"
    run_view = client.get(f"/api/v1/flows/runs/{run_id}").json()
    assert "flow_snapshot" not in run_view and "flow_dependencies" not in run_view
    assert client.post("/api/v1/flows", json={"flow": changed}).status_code == 201

    jobs = [j for j in aq.queue().list(status="pending")
            if j.kind == aq.FLOW_RUN_KIND and j.payload.get("run_id") == run_id]
    assert len(jobs) == 1
    aq._handle_flow_run(jobs[0])

    assert seen == ["safe work"]
    assert store.load_run(run_id).status == "completed"


def test_dashboard_flow_runner_passes_the_owner_identity_to_action_registry(monkeypatch):
    """Flow actions keep the request owner's ACL context in the worker."""
    from maverick.flow import execution
    from maverick_dashboard import automation_queue as aq

    seen = {}
    monkeypatch.setattr(aq, "_world", lambda: object())
    monkeypatch.setattr(execution, "default_agent_runner", lambda *a, **k: object())

    def action_factory(world, sandbox=None, *, channel=None, user_id=None):
        seen["world"] = world
        seen["sandbox"] = sandbox
        seen["channel"] = channel
        seen["user_id"] = user_id
        return object()

    monkeypatch.setattr(execution, "default_action_runner", action_factory)

    aq._flow_runners("user:alice")

    assert seen["channel"] == "api"
    assert seen["user_id"] == "alice"
