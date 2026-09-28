"""Settings > All settings: page, save/reset round-trips, permissions, audit.

Values saved here land in the dashboard overlay and must be what the kernel's
``load_config`` returns; reset must give the default back; only a global admin
on the same origin may write; each change leaves one audit row without any
secret value; and a change that lowers a protection is refused by the server
unless the request confirms it.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})
API = "/api/v1/settings/catalog"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from maverick import config, world_model

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    for name in ("MAVERICK_DASHBOARD_TOKEN", "MAVERICK_CONFIG", "MAVERICK_CONFIG_OVERLAY",
                 "MAVERICK_AUDIT_SIGN", "MAVERICK_IFC_MODE", "MAVERICK_APPROVALS_REQUIRED"):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    yield
    config.reset_config_cache()


@pytest.fixture()
def audit_rows(monkeypatch):
    import maverick.audit as audit

    rows: list[dict] = []

    def capture(kind, **payload):
        rows.append({"kind": kind, **payload})
        return True

    monkeypatch.setattr(audit, "audit_event", capture)
    return rows


def _loaded():
    from maverick import config

    config.reset_config_cache()
    return config.load_config()


def _save(changes, confirm=False, http=client):
    return http.post(API, json={"changes": changes, "confirm": confirm})


def _row(setting_id):
    for group in client.get(API).json()["groups"]:
        for row in group["settings"]:
            if row["id"] == setting_id:
                return row
    raise AssertionError(setting_id)


# ------------------------------------------------------------------ page
def test_page_lists_groups_controls_and_speaks_plain_language():
    from test_support.plain_language import developer_terms

    r = client.get("/settings/all")
    assert r.status_code == 200
    page = r.text
    for group in ("Safety", "Security", "AI models", "Spending &amp; limits",
                  "Memory &amp; learning", "Privacy", "Data retention", "Integrations",
                  "Performance", "Modules"):
        assert group in page
    assert 'role="switch"' in page and "<select" in page and 'type="number"' in page
    assert "Reset to default" in page and "Save changes in Safety" in page
    assert 'id="settings-all-search"' in page
    assert "/static/lightwork-settings-all.js" in page
    assert "What can&#39;t be changed here" in page or "What can't be changed here" in page
    assert developer_terms(page) == []


def test_settings_page_links_to_all_settings():
    assert 'href="/settings/all"' in client.get("/settings").text


def test_page_shows_where_each_value_comes_from(tmp_path):
    from maverick import config

    config.config_path().parent.mkdir(parents=True, exist_ok=True)
    config.config_path().write_text('[safety]\nprofile = "strict"\n', encoding="utf-8")
    config.reset_config_cache()
    assert _row("safety.profile")["source_text"] == "Set by your installer"
    assert _row("safety.profile")["value"] == "strict"
    assert _row("ifc.mode")["source_text"] == "Default"
    assert _save([{"id": "ifc.mode", "value": "block"}]).status_code == 200
    assert _row("ifc.mode")["source_text"] == "Set here"
    assert "Set by your installer" in client.get("/settings/all").text


# ------------------------------------------------------------ round trip
def test_save_and_reset_round_trip_through_load_config(audit_rows):
    changes = [
        {"id": "ifc.mode", "value": "block"},
        {"id": "budget.max_tool_calls", "value": 40},
        {"id": "routing.allowed_providers", "value": ["openai", "anthropic"]},
        {"id": "webhooks.outbound", "value": ["https://hooks.example/run"]},
        {"id": "rate_limits.shell", "value": {"count": 5, "seconds": 60}},
        {"id": "billing.stripe.currency", "value": "EUR"},
        {"id": "sandbox.timeout", "value": 120},
        {"id": "memory_guard.min_recall_trust", "value": "2"},
    ]
    # Adding an address that receives task events is confirmed.
    assert _save(changes).status_code == 409
    r = _save(changes, confirm=True)
    assert r.status_code == 200, r.text
    cfg = _loaded()
    assert cfg["ifc"]["mode"] == "block"
    assert cfg["budget"]["max_tool_calls"] == 40
    assert cfg["routing"]["allowed_providers"] == ["anthropic", "openai"]
    assert cfg["webhooks"]["outbound"] == ["https://hooks.example/run"]
    assert cfg["rate_limits"]["shell"] == "5/60"
    assert cfg["billing"]["stripe"]["currency"] == "EUR"
    assert cfg["sandbox"]["timeout"] == 120
    assert cfg["memory_guard"]["min_recall_trust"] == 2
    from maverick import config

    assert config.get_ifc()["mode"] == "block"
    assert config.get_memory_guard()["min_recall_trust"] == 2

    resets = [{"id": c["id"], "reset": True} for c in changes]
    # Going back to the default loosens some of these, so it is confirmed too.
    assert _save(resets).status_code == 409
    r = _save(resets, confirm=True)
    assert r.status_code == 200, r.text
    cfg = _loaded()
    assert "ifc" not in cfg and "rate_limits" not in cfg and "budget" not in cfg
    assert config.get_ifc()["mode"] == "audit"
    assert _row("ifc.mode")["source"] == "default"


def test_reset_returns_to_the_default_even_over_an_installer_value(audit_rows):
    from maverick import config

    config.config_path().parent.mkdir(parents=True, exist_ok=True)
    config.config_path().write_text("[swarm]\nmax_total_spawns = 8\n", encoding="utf-8")
    config.reset_config_cache()
    assert _save([{"id": "swarm.max_total_spawns", "reset": True}]).status_code == 200
    assert config.get_swarm()["max_total_spawns"] == 64
    assert "max_total_spawns = 8" in config.config_path().read_text(encoding="utf-8")


def test_existing_overlay_settings_survive_a_catalog_save(audit_rows):
    from maverick import config
    from maverick_dashboard import settings_store

    settings_store.set_webhooks_secret("keep-me-secret")  # pragma: allowlist secret
    settings_store.set_sandbox_backend("docker")
    settings_store.set_toggle("features", "skills", False)
    assert _save([{"id": "sandbox.timeout", "value": 90},
                  {"id": "webhooks.outbound", "value": ["https://a.example/b"]}],
                 confirm=True).status_code == 200
    cfg = _loaded()
    assert cfg["sandbox"] == {"backend": "docker", "timeout": 90}
    assert cfg["features"]["skills"] is False
    from maverick.webhooks import inbound_secret

    assert inbound_secret() == "keep-me-secret"  # pragma: allowlist secret
    assert config.config_source_errors() == {}


def test_invalid_values_are_refused_with_a_plain_message(audit_rows):
    r = _save([{"id": "budget.max_tool_calls", "value": -3}])
    assert r.status_code == 400
    assert r.json()["message"].startswith("Actions per task:")
    assert _save([{"id": "no.such_setting", "value": 1}]).status_code == 400
    r = _save([{"id": "ai_cloud_agent_governance.enable", "value": True}])
    assert r.status_code == 400 and "Clouds to oversee" in r.json()["message"]
    assert "ai_cloud_agent_governance" not in _loaded()
    assert audit_rows == []


def test_deployment_controlled_settings_are_locked(monkeypatch, audit_rows):
    monkeypatch.setenv("MAVERICK_IFC_MODE", "block")
    row = _row("ifc.mode")
    assert row["locked"] is True and row["source"] == "deployment"
    r = _save([{"id": "ifc.mode", "value": "off"}], confirm=True)
    assert r.status_code == 409
    assert "ifc" not in _loaded()


# ---------------------------------------------------------- permissions
def test_cross_origin_writes_are_refused(audit_rows):
    bare = TestClient(app)
    r = _save([{"id": "ifc.mode", "value": "block"}], http=bare)
    assert r.status_code == 403
    assert "ifc" not in _loaded()


def test_non_admins_are_refused(monkeypatch, audit_rows):
    from maverick_dashboard import auth, rbac

    rbac.set_role("user:vera", "viewer")
    monkeypatch.setattr(auth, "caller_principal", lambda request: "user:vera")
    assert client.get("/settings/all").status_code == 403
    assert client.get(API).status_code == 403
    assert _save([{"id": "ifc.mode", "value": "block"}]).status_code == 403
    assert "ifc" not in _loaded()


def test_workspace_admins_cannot_change_installation_settings(monkeypatch, audit_rows):
    from maverick.paths import reset_tenant, set_tenant
    from maverick_dashboard import auth, rbac

    rbac.set_role("user:alice", "viewer")
    rbac.set_tenant_role("acme", "user:alice", "admin")
    monkeypatch.setattr(auth, "caller_principal", lambda request: "user:alice")
    token = set_tenant("acme")
    try:
        r = _save([{"id": "ifc.mode", "value": "block"}])
    finally:
        reset_tenant(token)
    assert r.status_code == 403


# ------------------------------------------------------------- safety
def test_weakening_needs_confirmation_on_the_server(audit_rows):
    r = _save([{"id": "audit.sign", "value": False}])
    assert r.status_code == 409
    assert r.json()["confirm"][0]["id"] == "audit.sign"
    assert "audit" not in _loaded()
    assert audit_rows == []
    r = _save([{"id": "audit.sign", "value": False}], confirm=True)
    assert r.status_code == 200
    assert _loaded()["audit"]["sign"] is False
    assert audit_rows[-1]["confirmed"] is True
    # Strengthening again needs no confirmation.
    assert _save([{"id": "audit.sign", "value": True}]).status_code == 200


def test_resetting_to_a_weaker_default_also_needs_confirmation(audit_rows):
    from maverick import config

    config.config_path().parent.mkdir(parents=True, exist_ok=True)
    config.config_path().write_text("[security]\nallow_self_approval = false\n"
                                    "approvals_required = 2\n", encoding="utf-8")
    config.reset_config_cache()
    r = _save([{"id": "security.approvals_required", "reset": True}])
    assert r.status_code == 409
    assert _save([{"id": "security.approvals_required", "reset": True}],
                 confirm=True).status_code == 200


def test_shortening_retention_needs_confirmation(audit_rows):
    assert _save([{"id": "retention.audit_days", "value": 30}]).status_code == 409
    assert _save([{"id": "retention.audit_days", "value": 30}], confirm=True).status_code == 200
    assert _save([{"id": "retention.audit_days", "value": 90}]).status_code == 200


# ----------------------------------------------------------------- audit
def test_one_audit_row_per_change(audit_rows):
    r = _save([{"id": "ifc.mode", "value": "block"},
               {"id": "persona.name", "value": "Ada"}])
    assert r.status_code == 200
    kinds = [row["kind"] for row in audit_rows]
    assert kinds == ["setting_changed", "setting_changed"]
    by_key = {(row["section"], row["key"]): row for row in audit_rows}
    assert by_key[("ifc", "mode")]["old"] == "audit"
    assert by_key[("ifc", "mode")]["new"] == "block"
    assert by_key[("persona", "name")]["new"] == "Ada"
    assert all(row["_global"] is True and row["actor"] for row in audit_rows)
    # Saving the same value again is not a change.
    _save([{"id": "ifc.mode", "value": "block"}])
    assert len(audit_rows) == 2


def test_secrets_are_sealed_never_shown_and_never_audited(audit_rows):
    from maverick import config

    secret = "ntfy-topic-do-not-show"  # pragma: allowlist secret
    r = _save([{"id": "notifications.ntfy_topic", "value": secret}])
    assert r.status_code == 200
    assert secret not in r.text
    stored = config.dashboard_overrides_path().read_text(encoding="utf-8")
    assert secret not in stored and "MVKAR1:" in stored
    assert _loaded()["notifications"]["ntfy_topic"] == secret
    row = _row("notifications.ntfy_topic")
    assert row["is_set"] is True and row["value"] is None
    assert secret not in client.get("/settings/all").text
    assert secret not in client.get(API).text
    assert audit_rows[-1]["old"] == "not set" and audit_rows[-1]["new"] == "set"
    assert secret not in repr(audit_rows)
    assert _save([{"id": "notifications.ntfy_topic", "reset": True}]).status_code == 200
    assert "ntfy_topic" not in (_loaded().get("notifications") or {})


def test_refused_audit_undoes_the_change(monkeypatch):
    import maverick.audit as audit

    def refuse(kind, **payload):
        raise audit.AuditRefused("custody refused")

    monkeypatch.setattr(audit, "audit_event", refuse)
    r = _save([{"id": "ifc.mode", "value": "block"}])
    assert r.status_code == 503
    assert "ifc" not in _loaded()


# ------------------------------------------------- settings file integrity
@pytest.mark.parametrize("bad", ["\ud800", "name\x7fhere", "name\x00here", "line\nbreak"])
def test_values_that_would_break_the_settings_file_are_refused(audit_rows, bad):
    from maverick_dashboard import settings_store

    assert _save([{"id": "sandbox.timeout", "value": 30}]).status_code == 200
    # Sent as escaped JSON, the way any script can send it.
    body = json.dumps({"changes": [{"id": "persona.name", "value": bad}]})
    r = client.post(API, content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    message = r.json()["message"]
    assert message.startswith("Name") or ":" in message
    assert "surrogate" not in message.lower() and "toml" not in message.lower()
    # The overlay still reads, so every other dashboard setting still applies.
    assert settings_store.load_overlay()["sandbox"]["timeout"] == 30
    assert "persona" not in settings_store.load_overlay()
    assert _save([{"id": "sandbox.timeout", "value": 31}]).status_code == 200


def test_list_entries_that_would_break_the_settings_file_are_refused(audit_rows):
    import dataclasses

    from maverick_dashboard import settings_catalog as catalog

    listed = next(s for s in catalog.SETTINGS if s.control == "list")
    free_text = dataclasses.replace(listed, pattern="")
    assert catalog.coerce(free_text, ["ok"]) == ["ok"]
    for bad in ("bad\ud800", "bad\x7f", "bad\x1b"):
        with pytest.raises(catalog.SettingError) as exc:
            catalog.coerce(free_text, ["ok", bad])
        assert str(exc.value).startswith(listed.label)


# ------------------------------------------- loosening spend, rate, egress
@pytest.mark.parametrize("change,setup", [
    ({"id": "quotas.enforce", "value": False}, {"id": "quotas.enforce", "value": True}),
    ({"id": "rate_limits.shell", "reset": True},
     {"id": "rate_limits.shell", "value": {"count": 5, "seconds": 60}}),
    ({"id": "rate_limits.shell", "value": {"count": 500, "seconds": 60}},
     {"id": "rate_limits.shell", "value": {"count": 5, "seconds": 60}}),
    ({"id": "budget.max_tool_calls", "value": None},
     {"id": "budget.max_tool_calls", "value": 50}),
    ({"id": "webhooks.outbound", "value": ["https://collector.example.net/x"]}, None),
    ({"id": "knowledge.url", "value": "http://10.0.0.5:6333"}, None),
])
def test_loosening_spending_rate_and_outbound_controls_needs_confirmation(
    audit_rows, change, setup,
):
    if setup is not None:
        assert _save([setup]).status_code == 200
    r = _save([change])
    assert r.status_code == 409, r.text
    assert r.json()["confirm"][0]["id"] == change["id"]
    assert r.json()["confirm"][0]["warning"]
    r = _save([change], confirm=True)
    assert r.status_code == 200, r.text
    assert audit_rows[-1]["confirmed"] is True


def test_tightening_spending_rate_and_outbound_controls_needs_no_confirmation(audit_rows):
    assert _save([{"id": "quotas.enforce", "value": True}]).status_code == 200
    assert _save([{"id": "rate_limits.shell",
                   "value": {"count": 5, "seconds": 60}}]).status_code == 200
    assert _save([{"id": "rate_limits.shell",
                   "value": {"count": 2, "seconds": 60}}]).status_code == 200
    assert _save([{"id": "budget.max_tool_calls", "value": 50}]).status_code == 200
    assert _save([{"id": "budget.max_tool_calls", "value": 20}]).status_code == 200
    assert _save([{"id": "knowledge.url", "value": "https://qdrant.example.net"}]).status_code == 200
    assert _save([{"id": "webhooks.outbound", "value": ["https://a.example.net/x"]}],
                 confirm=True).status_code == 200
    # Removing an address only sends less, so it needs no confirmation.
    assert _save([{"id": "webhooks.outbound", "value": []}]).status_code == 200
