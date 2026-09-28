"""Read-only choice lists behind the /security/assurance guided forms."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})
BASE = "/api/v1/security/assurance/pickers"
DIGEST = "c" * 64


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from maverick import config

    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(config_path))
    for name in (
        "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_DASHBOARD_REQUIRE_AUTH",
        "MAVERICK_OIDC_ENABLED",
        "MAVERICK_PROXY_AUTH",
        "MAVERICK_TENANT",
        "MAVERICK_TENANT_BY_USER",
    ):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def _viewer_only(monkeypatch):
    from maverick_dashboard import auth

    monkeypatch.setattr(auth, "has_permission", lambda _request, permission: permission == "view")
    monkeypatch.setattr(auth, "has_global_permission", lambda *_args: False)


def test_package_form_fields_come_from_the_server_rules():
    from maverick import ai_bank_mrm, ai_conformity_workflow, ai_regulated_verticals

    response = client.get(f"{BASE}/package-forms")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["regulated"]["nyc_checks"] == list(ai_regulated_verticals.NYC_LL144_CHECKS)
    assert body["regulated"]["naic_checks"] == list(ai_regulated_verticals.NAIC_CHECKS)
    assert [section["key"] for section in body["bank_mrm"]["sections"]] == list(
        ai_bank_mrm._FIELD_SPECS
    )
    tier = next(
        field
        for field in body["bank_mrm"]["sections"][0]["fields"]
        if field["key"] == "model_tier"
    )
    assert tier["kind"] == "choice" and "tier-1" in tier["choices"]
    assert body["conformity"]["checklist_sections"]["fria"] == list(
        ai_conformity_workflow.CHECKLIST_SECTIONS["fria"]
    )
    assert body["insurability"]["measurements"][0] == "evaluation"


def test_package_form_fields_need_the_same_permission_as_the_page(monkeypatch):
    _viewer_only(monkeypatch)

    assert client.get(f"{BASE}/package-forms").status_code == 403


class _Officer:
    finding = {
        "id": "MRF-" + "1" * 24,
        "finding_type": "undeclared_asset",
        "asset_id": "MAA-" + "2" * 32,
        "severity": "high",
        "summary": "The observed AI asset has no accountable human declaration.",
        "finding_sha256": DIGEST,
    }
    acceptance = {
        "id": "MRX-" + "3" * 24,
        "record_kind": "risk_acceptance",
        "finding_id": finding["id"],
        "asset_id": finding["asset_id"],
        "severity": "high",
        "rationale": "Pilot only, reviewed weekly.",
        "reviewer": "user:reviewer",
        "decided_at": 1_800_000_000.0,
        "expires_at": 1_900_000_000.0,
        "status": "accepted",
        "revision": 2,
        "created_at": 1.0,
        "updated_at": 2.0,
    }
    incident = {
        "id": "MRI-" + "4" * 24,
        "asset_id": finding["asset_id"],
        "source_id": "helpdesk-42",
        "severity": "medium",
        "summary": "Assistant gave an out-of-date refund rule.",
        "occurred_at": 1_800_000_100.0,
        "resolution": "",
        "status": "open",
        "revision": 3,
        "created_at": 1.0,
        "updated_at": 2.0,
    }

    def findings(self):
        return [dict(self.finding)]

    def current_risk_acceptance(self, finding):
        return dict(self.acceptance) if finding["id"] == self.finding["id"] else None

    def list_incidents(self):
        return [dict(self.incident)]


def test_records_to_send_need_an_active_workspace(monkeypatch):
    assert client.get(f"{BASE}/grc-sources?source_kind=finding").status_code == 409


@pytest.mark.parametrize("kind", ["finding", "risk_acceptance", "ai_incident"])
def test_records_to_send_are_exact_and_accepted_by_the_send_form(monkeypatch, kind):
    from maverick_dashboard import assurance_pickers_api, model_risk_schemas

    monkeypatch.setenv("MAVERICK_TENANT", "alpha")
    officer = _Officer()
    monkeypatch.setattr(assurance_pickers_api, "_module", lambda *_args: officer)

    response = client.get(f"{BASE}/grc-sources?source_kind={kind}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 1
    source = body["sources"][0]["source"]
    assert source["source_kind"] == kind
    # Every candidate is exactly what the preview and send routes accept.
    model_risk_schemas.GRCOutboundSourceIn.model_validate(source)
    if kind == "finding":
        assert source["source_digest"] == DIGEST and source["source_revision"] == 1
    else:
        record = officer.acceptance if kind == "risk_acceptance" else officer.incident
        assert source["source_revision"] == record["revision"]
        assert len(source["source_digest"]) == 64
        again = client.get(f"{BASE}/grc-sources?source_kind={kind}").json()
        assert again["sources"][0]["source"]["source_digest"] == source["source_digest"]


def test_records_to_send_need_the_same_permission_as_the_page(monkeypatch):
    monkeypatch.setenv("MAVERICK_TENANT", "alpha")
    _viewer_only(monkeypatch)

    assert client.get(f"{BASE}/grc-sources?source_kind=finding").status_code == 403


def test_case_evidence_needs_an_active_workspace():
    assert client.get(f"{BASE}/conformity-evidence?case_id=EUC-1").status_code == 409


def test_case_evidence_lists_only_the_chosen_case_in_the_workspace(monkeypatch):
    from maverick_dashboard import assurance_pickers_api

    seen = {}

    class Service:
        def list_case_evidence(self, case_id):
            from maverick.paths import current_tenant_id_strict

            seen["tenant"] = current_tenant_id_strict()
            seen["case"] = case_id
            return [{
                "binding_id": "EUE-1",
                "evidence_type": "fria",
                "source_ref": "legal-drive",
                "asset_binding": {},
                "evidence_sha256": DIGEST,
                "binding_sha256": DIGEST,
            }]

    async def configured():
        return None, Service()

    monkeypatch.setenv("MAVERICK_TENANT", "alpha")
    monkeypatch.setattr(assurance_pickers_api, "_configured_conformity_workflow", configured)

    response = client.get(f"{BASE}/conformity-evidence?case_id=EUC-1")

    assert response.status_code == 200, response.text
    assert response.json()["evidence"] == [
        {"binding_id": "EUE-1", "evidence_type": "fria", "source_ref": "legal-drive"}
    ]
    assert seen == {"tenant": "alpha", "case": "EUC-1"}


class _Registry:
    def __init__(self):
        self.kinds = []

    def active_payloads(self, kind):
        self.kinds.append(kind)
        return {
            "federation_binding": [{
                "provider": "okta",
                "principal_ref": "prn_" + "5" * 32,
                "provider_subject_id": "sub_" + "6" * 32,
                "binding_sha256": DIGEST,
                "authority_revision": 2,
            }],
            "purview_asset_binding": [{
                "asset_id": "ast_" + "7" * 32,
                "binding_sha256": DIGEST,
                "revision": 4,
                "classification_codes": ["confidential"],
            }],
            "capability_rule": [{"capabilities": ["agent.execute"]}],
        }[kind]


def test_agent_identity_choices_are_for_administrators_only(monkeypatch):
    monkeypatch.setenv("MAVERICK_TENANT", "alpha")
    _viewer_only(monkeypatch)

    assert client.get(f"{BASE}/identity-controls").status_code == 403


def test_agent_identity_choices_need_an_active_workspace():
    assert client.get(f"{BASE}/identity-controls").status_code == 409


def test_agent_identity_choices_come_from_the_workspace_registry(monkeypatch):
    from maverick_dashboard import assurance_pickers_api

    monkeypatch.setenv("MAVERICK_TENANT", "alpha")
    monkeypatch.setattr(assurance_pickers_api, "_human_principal", lambda _request: "user:admin")
    registry = _Registry()
    calls = []

    def factory(tenant_id):
        calls.append(tenant_id)
        return registry

    monkeypatch.setattr(
        app.state, "identity_governance_authority_registry_factory", factory, raising=False
    )

    response = client.get(f"{BASE}/identity-controls")

    assert response.status_code == 200, response.text
    body = response.json()
    assert calls == ["alpha"]
    assert body["available"] is True
    assert body["capabilities"] == ["agent.execute"]
    assert body["federation_bindings"][0]["authority_revision"] == 2
    assert body["purview_assets"][0]["revision"] == 4
    # Prepared changes are listed only when the change control is turned on.
    assert body["purview_intents"] == [] and body["purview_intents_available"] is False


def test_agent_identity_choices_are_empty_without_an_enrolment_registry(monkeypatch):
    from maverick_dashboard import assurance_pickers_api

    monkeypatch.setenv("MAVERICK_TENANT", "alpha")
    monkeypatch.setattr(assurance_pickers_api, "_human_principal", lambda _request: "user:admin")
    monkeypatch.setattr(
        app.state, "identity_governance_authority_registry_factory", None, raising=False
    )

    body = client.get(f"{BASE}/identity-controls").json()

    assert body["available"] is False
    assert body["federation_bindings"] == [] and body["purview_assets"] == []
