"""Dashboard endpoints for importing external automations into Lightwork.

Hermetic like the other dashboard tests: HOME/MAVERICK_HOME isolated to tmp,
USER_TEMPLATES (import-time bound) monkeypatched, the world DB under tmp. The
import feature is gated, so the gate env is set on.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient


def _client():
    from maverick_dashboard.app import app
    return TestClient(app, headers={"Origin": "http://testserver"})


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setenv("MAVERICK_AUTOMATION_IMPORT", "1")
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    import maverick.templates as tpl
    monkeypatch.setattr(tpl, "USER_TEMPLATES", tmp_path / ".maverick" / "templates")


N8N_WF = {
    "id": "1", "name": "Lead to Slack", "active": True,
    "nodes": [
        {"name": "Hook", "type": "n8n-nodes-base.webhook", "parameters": {}},
        {"name": "Notify", "type": "n8n-nodes-base.slack", "parameters": {"operation": "post"}},
    ],
    "connections": {"Hook": {"main": [[{"node": "Notify"}]]}},
}


N8N_CRON_WF = {
    "id": "2", "name": "Daily digest", "active": True,
    "nodes": [
        {"name": "Every morning", "type": "n8n-nodes-base.cron",
         "parameters": {"cronExpression": "0 9 * * *"}},
        {"name": "Send email", "type": "n8n-nodes-base.emailSend",
         "parameters": {"operation": "send"}},
    ],
    "connections": {
        "Every morning": {"main": [[{"node": "Send email", "type": "main", "index": 0}]]},
    },
}


class _FakeQueue:
    last = None

    def __init__(self):
        self.enqueued = []
        type(self).last = self

    def enqueue(self, kind, payload, run_at=None):
        self.enqueued.append((kind, payload, run_at))
        return len(self.enqueued)


def test_sources_lists_modes():
    r = _client().get("/api/v1/import/sources")
    assert r.status_code == 200
    sources = {s["source"]: s["mode"] for s in r.json()["sources"]}
    assert sources["n8n"] == "definition-import"
    assert sources["zapier"] == "connect-and-trigger"


def test_graph_repair_refuses_whole_batch_before_materializing_or_arming(monkeypatch):
    from copy import deepcopy
    from unittest.mock import Mock

    from maverick import automation_import

    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    broken = deepcopy(N8N_WF)
    broken["id"] = "repair-needed"
    broken["nodes"][1] = {
        "name": "Notify", "type": "n8n-nodes-base.if", "parameters": {"conditions": {
            "conditions": [{"leftValue": "={{ $json.amount + 1 }}", "operator": "gt", "rightValue": 1}],
        }},
    }
    materialize = Mock(side_effect=AssertionError("batch preflight must precede every write"))
    monkeypatch.setattr(automation_import, "materialize", materialize)
    response = _client().post("/api/v1/import/run", json={
        "source": "n8n", "definitions": [N8N_WF, broken], "as_flows": True,
        "create_webhook_triggers": True, "activate_schedules": True,
    })
    assert response.status_code == 422, response.text
    assert "requires repair before execution" in response.json()["detail"]
    materialize.assert_not_called()


@pytest.mark.parametrize("expression", [
    {"equals": ["yes", "yes"]},
    {"equals": ["@triggerBody()?['customer.status']", "ready"]},
])
def test_wdl_reference_repair_refuses_batch_before_writes(monkeypatch, expression):
    from unittest.mock import Mock

    from maverick import automation_import

    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    good = {"name": "valid", "actions": {"Compose": {"type": "Compose", "inputs": "ready"}}}
    broken = {"name": "repair", "actions": {
        "Check": {"type": "If", "expression": expression,
                  "actions": {"Compose": {"type": "Compose", "inputs": "ready"}}},
    }}
    materialize = Mock(side_effect=AssertionError("batch preflight must precede every write"))
    monkeypatch.setattr(automation_import, "materialize", materialize)
    response = _client().post("/api/v1/import/run", json={
        "source": "power_automate", "definitions": [good, broken], "as_flows": True,
        "create_webhook_triggers": True, "activate_schedules": True,
    })
    assert response.status_code == 422, response.text
    assert "requires repair before execution" in response.json()["detail"]
    materialize.assert_not_called()


def test_run_creates_template_from_definitions():
    r = _client().post("/api/v1/import/run", json={
        "source": "n8n", "definitions": [N8N_WF],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is False
    assert len(body["imported"]) == 1
    item = body["imported"][0]
    assert item["template"].startswith("n8n-lead-to-slack-")
    assert item["created"] is True
    assert item["trigger"] == "webhook"
    # The template now exists.
    from maverick.templates import load_template
    assert load_template(item["template"]) is not None


def test_signed_in_importer_can_edit_own_workflow_but_another_user_cannot(monkeypatch):
    import maverick_dashboard.auth as auth
    from maverick.templates import load_template

    monkeypatch.setattr(auth, "proxy_auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "proxy_trusts", lambda _host: True)
    monkeypatch.setattr(auth, "proxy_header_name", lambda: "X-Forwarded-User")
    monkeypatch.setattr(auth, "oidc_enabled", lambda: False)
    client = _client()
    headers = {"X-Forwarded-User": "alice"}
    imported = client.post("/api/v1/import/run", headers=headers, json={
        "source": "n8n", "definitions": [N8N_WF],
    })
    assert imported.status_code == 200, imported.text
    name = imported.json()["imported"][0]["template"]
    original = load_template(name)
    assert original.owner == "user:alice"

    edit = {
        "name": name, "title": "Updated workflow", "body": "Review the new lead.",
        "overwrite": True, "expected_generation": original.generation,
    }
    saved = client.post("/api/v1/workflows", headers=headers, json=edit)
    assert saved.status_code == 201, saved.text
    updated = load_template(name)
    assert updated.owner == "user:alice"
    assert updated.body == "Review the new lead."

    denied = client.post("/api/v1/workflows", headers={"X-Forwarded-User": "bob"}, json={
        **edit, "body": "Replace Alice's workflow.", "expected_generation": updated.generation,
    })
    assert denied.status_code == 403, denied.text
    assert load_template(name).body == "Review the new lead."


def test_dry_run_writes_nothing():
    r = _client().post("/api/v1/import/run", json={
        "source": "n8n", "definitions": [N8N_WF], "dry_run": True,
    })
    assert r.status_code == 200
    item = r.json()["imported"][0]
    assert item["created"] is False
    from maverick.templates import load_template
    with pytest.raises(FileNotFoundError):
        load_template(item["template"])


def test_create_webhook_trigger_wires_it():
    r = _client().post("/api/v1/import/run", json={
        "source": "n8n", "definitions": [N8N_WF], "create_webhook_triggers": True,
    })
    assert r.status_code == 200
    item = r.json()["imported"][0]
    assert item["webhook_trigger"] == item["template"]
    from maverick_dashboard import triggers_store
    assert triggers_store.get_trigger(item["template"]) is not None


def test_webhook_trigger_respects_features_gate(monkeypatch):
    # When [features] triggers is off, the import must NOT create webhook
    # triggers (it would bypass the operator's deliberate decision).
    import maverick.config as cfg
    real = cfg.get_features
    monkeypatch.setattr(cfg, "get_features", lambda: {**real(), "triggers": False})
    r = _client().post("/api/v1/import/run", json={
        "source": "n8n", "definitions": [N8N_WF], "create_webhook_triggers": True,
    })
    assert r.status_code == 200
    item = r.json()["imported"][0]
    assert item["webhook_trigger"] is None
    assert any("triggers is off" in n for n in item["notes"])


def test_activate_schedules_preserves_request_identity(monkeypatch):
    import maverick.job_queue as job_queue
    import maverick_dashboard.api as api

    _FakeQueue.last = None
    monkeypatch.setattr(job_queue, "JobQueue", _FakeQueue)
    monkeypatch.setattr(api, "caller_principal", lambda request: "user:alice")
    monkeypatch.setattr(api, "execution_user_id_from_request", lambda request: "alice")

    r = _client().post("/api/v1/import/run", json={
        "source": "n8n",
        "definitions": [N8N_CRON_WF],
        "activate_schedules": True,
    })
    assert r.status_code == 200, r.text
    assert _FakeQueue.last is not None
    payload = _FakeQueue.last.enqueued[0][1]
    assert payload["owner"] == "user:alice"
    assert payload["channel"] == "api"
    assert payload["user_id"] == "alice"


def test_gate_blocks_when_disabled(monkeypatch):
    monkeypatch.setenv("MAVERICK_AUTOMATION_IMPORT", "0")
    r = _client().post("/api/v1/import/run", json={"source": "n8n", "definitions": [N8N_WF]})
    assert r.status_code == 403


def test_activate_schedules_falls_back_to_config_knob(monkeypatch):
    # With activate_schedules omitted (None), the [automation_import]
    # create_schedules knob decides -- previously a dead setting nothing read.
    import maverick.config as cfg
    import maverick.job_queue as job_queue
    _FakeQueue.last = None
    monkeypatch.setattr(job_queue, "JobQueue", _FakeQueue)
    monkeypatch.setattr(cfg, "get_automation_import",
                        lambda: {"enable": True, "create_schedules": True})
    r = _client().post("/api/v1/import/run", json={
        "source": "n8n", "definitions": [N8N_CRON_WF],   # no activate_schedules field
    })
    assert r.status_code == 200, r.text
    assert _FakeQueue.last is not None   # the config knob armed the schedule


def test_activate_schedules_off_by_default(monkeypatch):
    # Neither the request field nor the config knob set -> no schedule armed.
    import maverick.job_queue as job_queue
    _FakeQueue.last = None
    monkeypatch.setattr(job_queue, "JobQueue", _FakeQueue)
    r = _client().post("/api/v1/import/run", json={
        "source": "n8n", "definitions": [N8N_CRON_WF],
    })
    assert r.status_code == 200
    assert _FakeQueue.last is None


def test_partial_import_reports_skipped():
    # One good + one malformed definition: the good one imports, the malformed
    # one is dropped and surfaced via `skipped` (not silently lost).
    r = _client().post("/api/v1/import/run", json={
        "source": "n8n",
        "definitions": [N8N_WF, {"name": "broken", "nodes": "not-a-list"}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["imported"]) == 1
    assert body["skipped"] == 1


def test_unknown_source_400():
    r = _client().post("/api/v1/import/run", json={"source": "nope", "definitions": [{}]})
    assert r.status_code == 400


def test_connect_source_live_fetch_400():
    # zapier has no definitions to fetch; omitting definitions -> fetch() raises 400.
    r = _client().post("/api/v1/import/run", json={"source": "zapier"})
    assert r.status_code == 400
    assert "webhook" in r.json()["detail"].lower()


def test_automations_page_shows_import_section_when_enabled():
    r = _client().get("/automations")
    assert r.status_code == 200
    assert "Import from another platform" in r.text
    assert 'id="auto-import-form"' in r.text


def test_automations_page_hides_import_when_disabled(monkeypatch):
    monkeypatch.setenv("MAVERICK_AUTOMATION_IMPORT", "0")
    r = _client().get("/automations")
    assert r.status_code == 200
    assert "Import from another platform" not in r.text


def test_run_rejects_too_many_definitions():
    definitions = [dict(N8N_WF, id=str(i), name=f"WF {i}") for i in range(26)]
    r = _client().post("/api/v1/import/run", json={"source": "n8n", "definitions": definitions})
    assert r.status_code == 422


def test_run_rejects_oversized_definition():
    wf = dict(N8N_WF)
    wf["nodes"] = [
        {"name": "Hook", "type": "n8n-nodes-base.webhook", "parameters": {}},
        {
            "name": "Notify",
            "type": "n8n-nodes-base.slack",
            "parameters": {"operation": "post", "body": "x" * 65_000},
        },
    ]
    r = _client().post("/api/v1/import/run", json={"source": "n8n", "definitions": [wf]})
    assert r.status_code == 413


def test_import_as_flows_saves_graph_and_reports_fidelity(monkeypatch):
    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    r = _client().post("/api/v1/import/run", json={
        "source": "n8n", "definitions": [N8N_WF], "as_flows": True})
    assert r.status_code == 200, r.text
    item = r.json()["imported"][0]
    assert item["flow"]                                # a flow id came back
    assert item["fidelity"] and all(
        f["fidelity"] in ("preserved", "approximated") for f in item["fidelity"])
    from maverick.flow import store
    flow = store.load_flow(item["flow"])
    assert flow is not None and flow.validate() == []


def test_import_as_flows_requires_flow_engine(monkeypatch):
    monkeypatch.delenv("MAVERICK_FLOWS", raising=False)
    monkeypatch.setattr("maverick.config.get_flows", lambda: {"enable": False})
    r = _client().post("/api/v1/import/run", json={
        "source": "n8n", "definitions": [N8N_WF], "as_flows": True})
    assert r.status_code == 403


def test_import_as_flows_dry_run_previews_without_saving(monkeypatch):
    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    r = _client().post("/api/v1/import/run", json={
        "source": "n8n", "definitions": [N8N_WF], "as_flows": True, "dry_run": True})
    assert r.status_code == 200
    item = r.json()["imported"][0]
    assert item["flow"] and item["fidelity"]
    from maverick.flow import store
    assert store.load_flow(item["flow"]) is None       # previewed, not persisted
