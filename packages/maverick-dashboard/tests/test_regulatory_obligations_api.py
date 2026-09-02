"""HTTP contract of the Compliance -> Obligations console API: read-only, role
gated, and every answer comes from the compiled policy itself."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})
BASE = "/api/v1/regulatory"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from maverick import config, world_model
    from maverick_dashboard import api

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MAVERICK_HOME", str(home))
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    for name in ("MAVERICK_CONFIG", "MAVERICK_COMPLIANCE_PROFILES", "MAVERICK_DASHBOARD_TOKEN",
                 "MAVERICK_DASHBOARD_REQUIRE_AUTH", "MAVERICK_OIDC_ENABLED",
                 "MAVERICK_PROXY_AUTH", "MAVERICK_TENANT", "MAVERICK_TENANT_BY_USER"):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    api._world_cache.clear()
    yield
    config.reset_config_cache()
    api._world_cache.clear()


TOKEN = "test-dashboard-token"  # pragma: allowlist secret
authed = TestClient(app, headers={"Origin": "http://testserver",
                                  "Authorization": f"Bearer {TOKEN}"})


def _activate(monkeypatch, tmp_path, *keys):
    """Configure ``[compliance] profiles``. A regulated posture implies
    enterprise mode, which makes the dashboard require sign-in, so the
    tests below talk to it as an authenticated operator (``authed``)."""
    from maverick import config
    cfg = tmp_path / "config.toml"
    cfg.write_text("[compliance]\nprofiles = [" + ", ".join(f'"{k}"' for k in keys) + "]\n")
    monkeypatch.setenv("MAVERICK_CONFIG", str(cfg))
    monkeypatch.setenv("MAVERICK_DASHBOARD_TOKEN", TOKEN)
    monkeypatch.setenv("MAVERICK_CLIENT_ID", "acme-test")
    config.reset_config_cache()


def test_status_with_nothing_active_is_honest_about_it():
    r = client.get(f"{BASE}/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema"] == "lightwork.regulatory-obligations-status.v1"
    assert body["active_profiles"] == [] and body["obligations"] == []
    assert body["verification"] == {"inspected": 0, "problems": [], "sound": False}
    keys = {entry["key"] for entry in body["library"]}
    assert {"gdpr", "eu_ai_act", "hipaa"} <= keys
    assert all(entry["active"] is False for entry in body["library"])


def test_status_reflects_the_configured_profiles_and_verifies_them(monkeypatch, tmp_path):
    _activate(monkeypatch, tmp_path, "gdpr", "hipaa")
    r = authed.get(f"{BASE}/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active_profiles"] == ["gdpr", "hipaa"]
    ids = {o["id"] for o in body["obligations"]}
    assert "gdpr-art17-erasure-governed-path" in ids
    assert "hipaa-164-502b-minimum-necessary" in ids
    assert body["verification"]["sound"] is True
    assert body["verification"]["inspected"] == len(body["obligations"])
    assert "sql_query" in body["policy"]["require_human_actions"]
    assert "email" in body["attenuation"]["deny_tools"]
    active = {e["key"]: e["active"] for e in body["library"]}
    assert active["gdpr"] and active["hipaa"] and not active["eu_ai_act"]


def test_a_regulated_posture_requires_sign_in_even_for_reads(monkeypatch, tmp_path):
    """Enabling a regulation is an enterprise posture: the anonymous local
    session that works on a personal install is refused, by the dashboard
    itself, before this API is reached."""
    _activate(monkeypatch, tmp_path, "gdpr")
    assert client.get(f"{BASE}/status").status_code == 401
    assert authed.get(f"{BASE}/status").status_code == 200


def test_a_library_profile_can_be_previewed_without_enabling_it():
    r = client.get(f"{BASE}/profiles/eu_ai_act")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile"] == "eu_ai_act" and body["active"] is False
    assert body["policy"]["require_human_min_risk"] == "high"
    assert body["verification"]["sound"] is True
    assert client.get(f"{BASE}/profiles/sox").status_code == 404
    assert client.get(f"{BASE}/profiles/Not-Valid").status_code == 422


def test_check_answers_from_the_compiled_policy(monkeypatch, tmp_path):
    _activate(monkeypatch, tmp_path, "gdpr")
    r = authed.post(f"{BASE}/check", json={"action": "sql_query"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema"] == "lightwork.regulatory-obligations-check.v1"
    assert body["decision"] == "require_human"
    assert body["obligation"]["obligation_id"] == "gdpr-art17-erasure-governed-path"
    assert body["obligation"]["regulation"] == "GDPR"
    assert body["classified"] is True and body["risk"]
    assert "gdpr-art17-erasure-governed-path" in body["covered_by"]

    # An unclassified name is said to be unclassified, not silently allowed.
    body = authed.post(f"{BASE}/check", json={"action": "tool_that_does_not_exist"}).json()
    assert body["decision"] == "allow" and body["classified"] is False
    assert body["obligation"] is None and body["covered_by"] == []


def test_check_can_ask_about_the_whole_library_without_enabling_it():
    r = client.post(f"{BASE}/check", json={"action": "email", "profiles": ["hipaa"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profiles"] == ["hipaa"] and body["attenuated"] is True
    assert "hipaa-164-502b-minimum-necessary" in body["covered_by"]
    assert client.post(f"{BASE}/check", json={"action": "email", "profiles": ["sox"]}).status_code == 404


def _gateway_constraint_document(*, effect="deny", action="sql_query"):
    return {
        "schema": "lightwork.gateway-constraint-import.v1",
        "gateway": {"id": "airia_gateway", "revision_sha256": "b" * 64},
        "constraints": [{
            "id": "gateway-constraint",
            "effect": effect,
            "actions": [action],
            "min_risk": None,
            "deny_tools": [],
            "source_sha256": "c" * 64,
        }],
    }


def test_gateway_constraint_preview_is_read_only_and_never_claims_source_verification(
    monkeypatch, tmp_path,
):
    _activate(monkeypatch, tmp_path, "gdpr")
    response = authed.post(
        f"{BASE}/gateway-constraints/preview",
        json={"document": _gateway_constraint_document()},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runtime_enforcement_changed"] is False
    assert body["gateway_source_verified"] is False
    assert body["complete"] is True
    row = next(item for item in body["verdicts"] if item["action"] == "sql_query")
    assert row["compiler_decision"] == "require_human"
    assert row["effective_decision"] == "deny"
    assert row["gateway_constraint_ids"] == ["gateway-constraint"]


def test_gateway_constraint_preview_refuses_widening_or_unconfigured_scope(monkeypatch, tmp_path):
    _activate(monkeypatch, tmp_path, "gdpr")
    invalid = authed.post(
        f"{BASE}/gateway-constraints/preview",
        json={"document": _gateway_constraint_document(effect="allow")},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == "gateway constraint import was refused"

    monkeypatch.delenv("MAVERICK_CONFIG")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN")
    from maverick import config
    config.reset_config_cache()
    no_active = client.post(
        f"{BASE}/gateway-constraints/preview",
        json={"document": _gateway_constraint_document()},
    )
    assert no_active.status_code == 409
    assert no_active.json()["detail"] == "no active regulatory profiles are configured"


def test_tools_lists_the_classifiers_universe():
    body = client.get(f"{BASE}/tools").json()
    assert body["count"] == len(body["items"]) > 10
    assert "sql_query" in body["items"] and body["items"] == sorted(body["items"])


def test_bad_input_is_422():
    assert client.post(f"{BASE}/check", json={}).status_code == 422
    assert client.post(f"{BASE}/check", json={"action": "../x"}).status_code == 422
    assert client.post(f"{BASE}/check", json={"action": "x", "extra": 1}).status_code == 422
    assert client.post(f"{BASE}/check", json={"action": "x", "profiles": ["BAD KEY"]}).status_code == 422


def test_the_surface_is_read_only():
    """Nothing here may change enforcement: the only POST is a pure query."""
    found = {path: {m.upper() for m in ops} for path, ops in app.openapi()["paths"].items()
             if path.startswith(BASE + "/")}
    assert set(found) == {
        f"{BASE}/status",
        f"{BASE}/tools",
        f"{BASE}/profiles/{{key}}",
        f"{BASE}/check",
        f"{BASE}/gateway-constraints/preview",
    }
    assert set().union(*found.values()) <= {"GET", "POST"}
    assert found[f"{BASE}/check"] == {"POST"}
    assert found[f"{BASE}/gateway-constraints/preview"] == {"POST"}


def test_roles_gate_every_read(monkeypatch):
    from maverick_dashboard import regulatory_obligations_api as api_mod

    def _deny(request, permission):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="insufficient role")

    monkeypatch.setattr(api_mod, "require_permission", _deny)
    assert client.get(f"{BASE}/status").status_code == 403
    assert client.get(f"{BASE}/tools").status_code == 403
    assert client.post(f"{BASE}/check", json={"action": "email"}).status_code == 403
    assert client.post(
        f"{BASE}/gateway-constraints/preview",
        json={"document": _gateway_constraint_document()},
    ).status_code == 403
