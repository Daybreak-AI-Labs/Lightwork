"""HTTP contract for the governed Model Risk assurance surface."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})


@pytest.fixture(autouse=True)
def _isolated_model_risk(tmp_path, monkeypatch):
    from maverick import audit, config
    from maverick.audit import writer as audit_writer

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """[evidence_graph]
enable = true

[model_risk_assurance]
enable = true
gate_promotions = true
""",
        encoding="utf-8",
    )
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
    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    config.reset_config_cache()
    audit_writer._default = None
    audit_writer._defaults.clear()
    yield
    config.reset_config_cache()
    audit_writer._default = None
    audit_writer._defaults.clear()


def _ok(response, expected=200):
    assert response.status_code == expected, response.text
    return response.json()


def _enable_portfolio() -> None:
    from maverick import config

    path = Path(config.config_path())
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n[portfolio_risk]\nenable = true\n",
        encoding="utf-8",
    )
    config.reset_config_cache()


def test_red_team_admin_campaign_lifecycle_is_actor_bound(monkeypatch):
    from maverick import red_team_assurance as rta

    calls: dict = {}
    monkeypatch.setattr(rta, "enabled", lambda: True)
    monkeypatch.setattr(
        rta,
        "starter_suite_manifest",
        lambda: {"suite_id": "lightwork-admission-starter"},
    )
    monkeypatch.setattr(
        rta,
        "list_campaigns",
        lambda **_kwargs: {
            "items": [{"id": "RTC-one", "status": "running"}],
            "count": 1,
            "truncated": False,
            "limit": 100,
        },
    )
    monkeypatch.setattr(
        rta,
        "get_campaign",
        lambda campaign_id: {"id": campaign_id, "status": "running"},
    )

    def create(**kwargs):
        calls["create"] = kwargs
        return {"id": "RTC-one", "status": "running", "revision": 1}

    def finalize(campaign_id, **kwargs):
        calls["finalize"] = (campaign_id, kwargs)
        return {"id": campaign_id, "status": "completed", "revision": 2}

    def cancel(campaign_id, **kwargs):
        calls["cancel"] = (campaign_id, kwargs)
        return {"id": campaign_id, "status": "cancelled", "revision": 2}

    monkeypatch.setattr(rta, "create_campaign", create)
    monkeypatch.setattr(rta, "finalize_campaign", finalize)
    monkeypatch.setattr(rta, "cancel_campaign", cancel)

    body = {
        "asset_id": "MAA-" + ("a" * 32),
        "external_agent_id": "external-one",
        "idempotency_key": "campaign-one",
        "safe_target_environment": True,
        "case_keys": ["prompt-injection-01"],
        "ttl_seconds": 3600,
    }
    created = _ok(client.post(
        "/api/v1/security/assurance/red-team/campaigns",
        json=body,
    ), 201)
    assert created["status"] == "running"
    assert calls["create"]["actor"] == "local:dashboard"
    assert _ok(client.get(
        "/api/v1/security/assurance/red-team/campaigns"
    ))["campaigns"][0]["id"] == "RTC-one"
    assert _ok(client.get(
        "/api/v1/security/assurance/red-team/suite"
    ))["suite_id"] == "lightwork-admission-starter"

    finalized = _ok(client.post(
        "/api/v1/security/assurance/red-team/campaigns/RTC-one/finalize",
        json={"force_incomplete": False},
    ))
    assert finalized["status"] == "completed"
    assert calls["finalize"] == (
        "RTC-one",
        {"actor": "local:dashboard", "force_incomplete": False},
    )

    cancelled = _ok(client.post(
        "/api/v1/security/assurance/red-team/campaigns/RTC-two/cancel",
        json={"expected_revision": 1, "rationale": "Human stop."},
    ))
    assert cancelled["status"] == "cancelled"
    assert calls["cancel"][1]["actor"] == "local:dashboard"


def test_portfolio_risk_report_and_snapshot_api(monkeypatch):
    from maverick import portfolio_risk

    calls: dict = {}
    monkeypatch.setattr(portfolio_risk, "enabled", lambda: True)
    monkeypatch.setattr(
        portfolio_risk,
        "current_report",
        lambda: {"portfolio_score": 75, "formula_version": "v1"},
    )
    monkeypatch.setattr(
        portfolio_risk,
        "list_snapshots",
        lambda: [{"id": "MRP-one", "period_id": "2026-Q3"}],
    )
    monkeypatch.setattr(
        portfolio_risk,
        "get_snapshot",
        lambda period_id: {"id": "MRP-one", "period_id": period_id},
    )

    def refresh(**kwargs):
        calls.update(kwargs)
        return {"id": "MRP-one", "period_id": kwargs["period_id"], "revision": 1}

    monkeypatch.setattr(portfolio_risk, "refresh_current_snapshot", refresh)

    report = _ok(client.get(
        "/api/v1/security/assurance/portfolio-risk/report"
    ))
    assert report["portfolio_score"] == 75
    snapshots = _ok(client.get(
        "/api/v1/security/assurance/portfolio-risk/snapshots"
    ))
    assert snapshots["snapshots"][0]["period_id"] == "2026-Q3"
    snapshot = _ok(client.get(
        "/api/v1/security/assurance/portfolio-risk/snapshots/2026-Q3"
    ))
    assert snapshot["period_id"] == "2026-Q3"
    refreshed = _ok(client.post(
        "/api/v1/security/assurance/portfolio-risk/snapshots",
        json={"expected_revision": 0, "period_id": "2026-Q3"},
    ))
    assert refreshed["revision"] == 1
    assert calls == {
        "actor": "local:dashboard",
        "expected_revision": 0,
        "period_id": "2026-Q3",
    }


def test_real_hierarchy_http_flow_rejects_cycles_and_supports_detach():
    now = time.time()
    source = "hierarchy-api"

    def observe(label, asset_type):
        return _ok(client.post(
            "/api/v1/security/assurance/model-risk/observations",
            json={
                "asset_type": asset_type,
                "source": source,
                "source_id": label,
                "display_name": label,
                "version_digest": hashlib.sha256(label.encode()).hexdigest(),
                "metadata": {},
                "dependencies": [],
            },
        ))

    system = observe("system-one", "system")
    component_a = observe("component-a", "component")
    component_b = observe("component-b", "component")

    def declare(row, parent=None):
        body = {
            "asset_id": row["asset_id"],
            "owner": "AI Risk Committee",
            "purpose": "Governed hierarchy API test.",
            "intended_use": "Decision support with human review.",
            "risk_tier": "medium",
            "risk_context": {"human_oversight": True},
            "eu_ai_act": {
                "category": "not_applicable",
                "asserted_at": now,
                "as_of": "2026-07-21",
                "source_ref": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj",
                "rationale": "Qualified human test assertion.",
            },
        }
        if parent is not None:
            body["parent_asset_id"] = parent["asset_id"]
        return _ok(client.post(
            "/api/v1/security/assurance/model-risk/declarations",
            json=body,
        ))

    declare(system)
    declaration_a = declare(component_a, system)
    declaration_b = declare(component_b, component_a)

    cycle = client.patch(
        "/api/v1/security/assurance/model-risk/declarations/"
        + component_a["asset_id"],
        json={
            "expected_revision": declaration_a["revision"],
            "parent_asset_id": component_b["asset_id"],
        },
    )
    assert cycle.status_code == 422
    assert "cycle" in cycle.json()["detail"]

    approved_b = _ok(client.post(
        "/api/v1/security/assurance/model-risk/declarations/"
        + component_b["asset_id"]
        + "/review",
        json={
            "decision": "approved",
            "rationale": "Exact hierarchy and intended use reviewed.",
            "expected_revision": declaration_b["revision"],
        },
    ))
    detached = _ok(client.patch(
        "/api/v1/security/assurance/model-risk/declarations/"
        + component_b["asset_id"],
        json={
            "expected_revision": approved_b["revision"],
            "parent_asset_id": None,
        },
    ))
    assert detached["parent_asset_id"] is None
    assert detached["status"] == "proposed"
    assert detached["review"] is None


def test_real_portfolio_api_snapshot_integrity_and_http_errors():
    _enable_portfolio()
    report = _ok(client.get(
        "/api/v1/security/assurance/portfolio-risk/report"
    ))
    period = report["period_id"]
    snapshot = _ok(client.post(
        "/api/v1/security/assurance/portfolio-risk/snapshots",
        json={"expected_revision": 0, "period_id": period},
    ))
    assert snapshot["report_sha256"] == snapshot["report"]["report_sha256"]
    loaded = _ok(client.get(
        f"/api/v1/security/assurance/portfolio-risk/snapshots/{period}"
    ))
    assert loaded["report_sha256"] == snapshot["report_sha256"]

    assert client.get(
        "/api/v1/security/assurance/portfolio-risk/snapshots/2099-Q1"
    ).status_code == 404
    assert client.get(
        "/api/v1/security/assurance/portfolio-risk/snapshots/2026-Q5"
    ).status_code == 422

    year, quarter_text = period.split("-Q")
    quarter = int(quarter_text)
    previous = (
        f"{int(year) - 1}-Q4" if quarter == 1 else f"{year}-Q{quarter - 1}"
    )
    closed = client.post(
        "/api/v1/security/assurance/portfolio-risk/snapshots",
        json={"expected_revision": 0, "period_id": previous},
    )
    assert closed.status_code == 409


def test_ai_inventory_api_requires_tenant_and_binds_admin_sync(monkeypatch):
    from maverick import ai_inventory

    calls: dict = {}
    monkeypatch.setattr(ai_inventory, "enabled", lambda: True)
    monkeypatch.setattr(
        ai_inventory,
        "available_connectors",
        lambda: ["bedrock", "vertex_ai"],
    )
    monkeypatch.setattr(
        ai_inventory,
        "get_ai_inventory_config",
        lambda: {
            "enable": True,
            "connectors": ["bedrock"],
            "limits": ai_inventory.DiscoveryLimits(
                page_cap=100,
                record_cap=5000,
                retry_attempts=3,
                timeout_seconds=30,
            ),
            "missing_grace_runs": 2,
        },
    )
    monkeypatch.setattr(
        ai_inventory,
        "list_discovered_assets",
        lambda **_kwargs: [{"asset_id": "MAA-" + ("a" * 32), "status": "present"}],
    )
    monkeypatch.setattr(
        ai_inventory,
        "list_discovery_runs",
        lambda **_kwargs: [{"id": "AIR-one", "status": "complete"}],
    )
    monkeypatch.setattr(
        ai_inventory,
        "connector_status",
        lambda name: {"connector": name, "state": "complete"},
    )

    assert client.get(
        "/api/v1/security/assurance/ai-inventory"
    ).status_code == 409
    monkeypatch.setenv("MAVERICK_TENANT", "alpha")
    overview = _ok(client.get(
        "/api/v1/security/assurance/ai-inventory"
    ))
    assert overview["configured_connectors"] == ["bedrock"]
    assert overview["limits"]["record_cap"] == 5000
    assert overview["assets"][0]["status"] == "present"

    def sync(connector, *, actor):
        calls.update({"connector": connector, "actor": actor})
        return {"id": "AIR-two", "status": "complete", "asset_count": 1}

    monkeypatch.setattr(ai_inventory, "sync_connector", sync)
    result = _ok(client.post(
        "/api/v1/security/assurance/ai-inventory/connectors/bedrock/sync"
    ))
    assert result["status"] == "complete"
    assert calls == {"connector": "bedrock", "actor": "local:dashboard"}
    assert client.post(
        "/api/v1/security/assurance/ai-inventory/connectors/unknown/sync"
    ).status_code == 422


def test_ai_inventory_configuration_failure_is_service_unavailable(monkeypatch):
    from maverick import ai_inventory

    monkeypatch.setenv("MAVERICK_TENANT", "alpha")
    monkeypatch.setattr(ai_inventory, "enabled", lambda: True)
    monkeypatch.setattr(
        ai_inventory,
        "available_connectors",
        lambda: ["bedrock"],
    )

    def unavailable_config():
        raise ai_inventory.AIInventoryConfigError(
            "required credential reference is unavailable"
        )

    monkeypatch.setattr(
        ai_inventory,
        "get_ai_inventory_config",
        unavailable_config,
    )
    response = client.get("/api/v1/security/assurance/ai-inventory")
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "required credential reference is unavailable"
    )


def test_policy_pack_http_lifecycle_requires_explicit_admin_actions(monkeypatch):
    from maverick import policy_packs

    monkeypatch.setenv("MAVERICK_TENANT", "alpha")
    calls: dict = {}
    bundled = {
        "pack_id": "nist-ai-rmf-1.0-starter",
        "version": "1.0.0",
        "content_sha256": "a" * 64,
        "framework": {"name": "NIST AI RMF", "version": "1.0"},
        "control_count": 0,
    }
    monkeypatch.setattr(policy_packs, "enabled", lambda: True)
    monkeypatch.setattr(
        policy_packs,
        "bundled_summaries",
        lambda: {bundled["pack_id"]: bundled},
    )
    monkeypatch.setattr(
        policy_packs,
        "list_catalog",
        lambda: {"items": [], "count": 0, "truncated": False, "limit": 100},
    )
    monkeypatch.setattr(
        policy_packs,
        "list_channels",
        lambda: {"items": [], "count": 0, "truncated": False, "limit": 100},
    )
    monkeypatch.setattr(
        policy_packs,
        "list_subscriptions",
        lambda: {"items": [], "count": 0, "truncated": False, "limit": 100},
    )
    channel = {
        "pack_id": bundled["pack_id"],
        "pack_version": bundled["version"],
        "content_sha256": bundled["content_sha256"],
        "revision": 1,
    }
    monkeypatch.setattr(
        policy_packs,
        "get_channel_summary",
        lambda _pack_id: channel,
    )
    monkeypatch.setattr(
        policy_packs,
        "get_subscription_summary",
        lambda _pack_id: (
            {
                "id": "pps-one",
                "pack_id": bundled["pack_id"],
                "revision": 2 if "refresh" in calls else 1,
                "needs_refresh": False,
                "current_count": 0,
                "stale_count": 0,
                "missing_count": 0,
                "retired_count": 0,
            }
            if "subscribe" in calls
            else None
        ),
    )

    overview = _ok(client.get(
        "/api/v1/security/assurance/policy-packs"
    ))
    assert bundled["pack_id"] in overview["bundled"]
    assert "controls" not in overview["bundled"][bundled["pack_id"]]
    assert overview["certification"] is False
    detail = _ok(client.get(
        "/api/v1/security/assurance/policy-packs/"
        + bundled["pack_id"]
    ))
    assert detail["active_channel"]["content_sha256"] == "a" * 64
    assert "requirements" not in detail

    def stage(pack_id, *, actor):
        calls["stage"] = (pack_id, actor)
        return {"id": "ppk-one", "pack_id": pack_id, "revision": 1}

    def activate(entry_id, **kwargs):
        calls["activate"] = (entry_id, kwargs)
        return {"id": "ppc-one", "pack_id": bundled["pack_id"], "revision": 1}

    def subscribe(pack_id, *, actor):
        calls["subscribe"] = (pack_id, actor)
        return {"id": "pps-one", "pack_id": pack_id, "revision": 1}

    def refresh(subscription_id, **kwargs):
        calls["refresh"] = (subscription_id, kwargs)
        return {
            "id": subscription_id,
            "pack_id": bundled["pack_id"],
            "revision": 2,
        }

    monkeypatch.setattr(policy_packs, "stage_bundled", stage)
    monkeypatch.setattr(policy_packs, "activate_pack", activate)
    monkeypatch.setattr(policy_packs, "subscribe", subscribe)
    monkeypatch.setattr(policy_packs, "refresh_subscription", refresh)
    monkeypatch.setattr(
        policy_packs,
        "get_catalog_summary",
        lambda entry_id: {
            "id": entry_id,
            "pack_id": bundled["pack_id"],
            "pack_version": bundled["version"],
            "content_sha256": bundled["content_sha256"],
            "revision": 1,
        },
    )

    staged = _ok(client.post(
        "/api/v1/security/assurance/policy-packs/bundled/"
        + bundled["pack_id"]
        + "/stage"
    ), 201)
    _ok(client.post(
        f"/api/v1/security/assurance/policy-packs/catalog/{staged['id']}/activate",
        json={"expected_revision": 0},
    ))
    _ok(client.post(
        "/api/v1/security/assurance/policy-packs/"
        + bundled["pack_id"]
        + "/subscribe"
    ), 201)
    _ok(client.post(
        "/api/v1/security/assurance/policy-packs/subscriptions/pps-one/refresh",
        json={"expected_revision": 1},
    ))
    assert calls["stage"][1] == "local:dashboard"
    assert calls["activate"][1]["admin"] is True
    assert calls["subscribe"][1] == "local:dashboard"
    assert calls["refresh"][1]["expected_revision"] == 1


def test_policy_subscription_http_requires_tenant_but_global_stage_does_not(
    monkeypatch,
):
    from maverick import policy_packs

    monkeypatch.setattr(policy_packs, "enabled", lambda: True)
    monkeypatch.setattr(
        policy_packs,
        "stage_bundled",
        lambda pack_id, *, actor: {
            "id": "ppk-global",
            "pack_id": pack_id,
            "revision": 1,
        },
    )
    monkeypatch.setattr(
        policy_packs,
        "get_catalog_summary",
        lambda entry_id: {
            "id": entry_id,
            "pack_id": "nist-ai-rmf-1.0-starter",
            "pack_version": "1.0.0",
            "content_sha256": "a" * 64,
            "revision": 1,
        },
    )

    for response in (
        client.get("/api/v1/security/assurance/policy-packs"),
        client.get(
            "/api/v1/security/assurance/policy-packs/nist-ai-rmf-1.0-starter",
        ),
        client.post(
            "/api/v1/security/assurance/policy-packs/"
            "nist-ai-rmf-1.0-starter/subscribe",
        ),
        client.post(
            "/api/v1/security/assurance/policy-packs/subscriptions/pps-one/refresh",
            json={"expected_revision": 1},
        ),
    ):
        assert response.status_code == 409
        assert "active tenant" in response.json()["detail"]

    staged = client.post(
        "/api/v1/security/assurance/policy-packs/bundled/"
        "nist-ai-rmf-1.0-starter/stage",
    )
    assert staged.status_code == 201
    assert staged.json()["id"] == "ppk-global"


def test_model_risk_http_flow_keeps_classification_and_review_human_governed():
    now = time.time()
    artifact_digest = hashlib.sha256(b"api-model-v1").hexdigest()
    observation = _ok(client.post(
        "/api/v1/security/assurance/model-risk/observations",
        json={
            "asset_type": "model",
            "source": "api_registry",
            "source_id": "customer-risk-model",
            "display_name": "Customer risk model",
            "version_digest": artifact_digest,
            "observed_at": now,
            "metadata": {"environment": "test"},
            "dependencies": [],
        },
    ))
    asset_id = observation["asset_id"]
    inventory = _ok(client.get(
        "/api/v1/security/assurance/model-risk/inventory"
    ))
    assert inventory["assets"][0]["asset_id"] == asset_id
    evaluator_digest = hashlib.sha256(b"api-evaluator-v1").hexdigest()
    evaluator = _ok(client.post(
        "/api/v1/security/assurance/model-risk/observations",
        json={
            "asset_type": "tool",
            "source": "api_registry",
            "source_id": "bounded-evaluator",
            "display_name": "Bounded evaluator",
            "version_digest": evaluator_digest,
            "observed_at": now,
            "metadata": {"environment": "test"},
            "dependencies": [],
        },
    ))

    declaration = _ok(client.post(
        "/api/v1/security/assurance/model-risk/declarations",
        json={
            "asset_id": asset_id,
            "owner": "AI Risk Committee",
            "purpose": "Decision support for trained reviewers.",
            "intended_use": "Advisory scoring with human review.",
            "risk_tier": "medium",
            "risk_context": {"human_oversight": True},
            "eu_ai_act": {"category": "undetermined"},
        },
    ))
    refused = client.post(
        f"/api/v1/security/assurance/model-risk/declarations/{asset_id}/review",
        json={
            "decision": "approved",
            "rationale": "This request has no human applicability assertion.",
            "expected_revision": declaration["revision"],
        },
    )
    assert refused.status_code == 422

    declaration = _ok(client.patch(
        f"/api/v1/security/assurance/model-risk/declarations/{asset_id}",
        json={
            "expected_revision": declaration["revision"],
            "eu_ai_act": {
                "category": "not_applicable",
                "asserted_at": now,
                "as_of": "2026-07-21",
                "source_ref": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj",
                "rationale": "A qualified human reviewed this test context.",
            },
        },
    ))
    declaration = _ok(client.post(
        f"/api/v1/security/assurance/model-risk/declarations/{asset_id}/review",
        json={
            "decision": "approved",
            "rationale": "Owner, use, risk context, and dated assertion reviewed.",
            "expected_revision": declaration["revision"],
        },
    ))
    assert declaration["status"] == "approved"
    assert declaration["legal_certification"] is False

    evidence = _ok(client.post(
        "/api/v1/security/assurance/model-risk/evidence",
        json={
            "asset_id": asset_id,
            "source_id": "evaluation-api-1",
            "evidence_kind": "evaluation",
            "result": "passed",
            "scope_digest": hashlib.sha256(b"evaluation-scope").hexdigest(),
            "artifact_digest": artifact_digest,
            "evaluator_digest": evaluator_digest,
            "evaluator_asset_id": evaluator["asset_id"],
            "summary": "Bounded evaluation receipt for the exact artifact.",
            "metrics": {"cases": 42, "pass_rate": 1.0},
            "observed_at": now,
            "valid_until": now + 3600,
        },
    ))
    approved = _ok(client.post(
        f"/api/v1/security/assurance/model-risk/evidence/{evidence['id']}/review",
        json={
            "decision": "approved",
            "rationale": "Scope, artifact binding, and result were reviewed.",
            "expected_revision": evidence["revision"],
        },
    ))
    assert approved["status"] == "approved"
    stale = client.post(
        f"/api/v1/security/assurance/model-risk/evidence/{evidence['id']}/review",
        json={
            "decision": "revoked",
            "rationale": "Stale browser state.",
            "expected_revision": evidence["revision"],
        },
    )
    assert stale.status_code == 409


def test_verified_training_receipt_route_uses_server_trust_and_active_tenant(
    monkeypatch,
):
    from maverick import model_risk_assurance
    from maverick.paths import current_tenant_id_strict

    monkeypatch.setenv("MAVERICK_TENANT", "alpha")
    calls = {}

    def _record(asset_id, **kwargs):
        calls["tenant_id"] = current_tenant_id_strict()
        calls["asset_id"] = asset_id
        calls.update(kwargs)
        return {
            "id": "MRE-training-receipt",
            "evidence_kind": "training_run",
            "status": "pending_review",
        }

    monkeypatch.setattr(
        model_risk_assurance,
        "record_verified_training_receipt_evidence",
        _record,
    )
    valid_until = time.time() + 600
    response = _ok(client.post(
        "/api/v1/security/assurance/model-risk/evidence/training-receipts",
        json={
            "asset_id": "MAA-" + ("a" * 32),
            "receipt_id": "tenant-private-receipt-1",
            "actor": "local:dashboard",
            "valid_until": valid_until,
        },
    ))

    assert response["evidence_kind"] == "training_run"
    assert calls == {
        "tenant_id": "alpha",
        "asset_id": "MAA-" + ("a" * 32),
        "receipt_id": "tenant-private-receipt-1",
        "actor": "local:dashboard",
        "valid_until": valid_until,
    }


@pytest.mark.parametrize(
    "field",
    [
        "trusted_receipt_pubkeys",
        "trusted_approver_pubkeys",
    ],
)
def test_training_receipt_route_rejects_caller_supplied_trust_registries(
    monkeypatch,
    field,
):
    from maverick import model_risk_assurance

    monkeypatch.setenv("MAVERICK_TENANT", "alpha")
    monkeypatch.setattr(
        model_risk_assurance,
        "record_verified_training_receipt_evidence",
        lambda *_args, **_kwargs: pytest.fail("invalid input reached the officer"),
    )
    body = {
        "asset_id": "MAA-" + ("a" * 32),
        "receipt_id": "tenant-private-receipt-1",
        "actor": "local:dashboard",
        "valid_until": time.time() + 600,
    }
    body[field] = {"attacker-chosen": "trust-anchor"}
    response = client.post(
        "/api/v1/security/assurance/model-risk/evidence/training-receipts",
        json=body,
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("receipt", {"public_key": "11" * 32, "private_training_content": "raw"}),
        ("public_key", "11" * 32),
        ("private_key", "22" * 32),
        ("tenant_id", "different-tenant"),
    ],
)
def test_training_receipt_route_rejects_raw_self_disclosed_or_tenant_fields(
    monkeypatch,
    field,
    value,
):
    from maverick import model_risk_assurance

    monkeypatch.setenv("MAVERICK_TENANT", "alpha")
    monkeypatch.setattr(
        model_risk_assurance,
        "record_verified_training_receipt_evidence",
        lambda *_args, **_kwargs: pytest.fail("forbidden input reached the officer"),
    )
    body = {
        "asset_id": "MAA-" + ("a" * 32),
        "receipt_id": "tenant-private-receipt-1",
        "actor": "local:dashboard",
        "valid_until": time.time() + 600,
        field: value,
    }
    response = client.post(
        "/api/v1/security/assurance/model-risk/evidence/training-receipts",
        json=body,
    )
    assert response.status_code == 422


def test_training_receipt_route_requires_auth_and_binds_actor(monkeypatch):
    from maverick import model_risk_assurance

    monkeypatch.setenv("MAVERICK_TENANT", "alpha")
    monkeypatch.setenv("MAVERICK_DASHBOARD_TOKEN", "training-operator-token")
    monkeypatch.setattr(
        model_risk_assurance,
        "record_verified_training_receipt_evidence",
        lambda _asset_id, **_kwargs: {"status": "pending_review"},
    )
    body = {
        "asset_id": "MAA-" + ("a" * 32),
        "receipt_id": "tenant-private-receipt-1",
        "actor": "auth:dashboard-token",
        "valid_until": time.time() + 600,
    }
    path = "/api/v1/security/assurance/model-risk/evidence/training-receipts"
    assert client.post(path, json=body).status_code == 401

    headers = {
        "Authorization": "Bearer training-operator-token",
        "Origin": "http://testserver",
    }
    mismatched = dict(body, actor="impersonated:operator")
    assert client.post(path, json=mismatched, headers=headers).status_code == 403
    assert client.post(path, json=body, headers=headers).status_code == 200


def test_generic_evidence_route_refuses_unverified_training_run():
    now = time.time()
    response = client.post(
        "/api/v1/security/assurance/model-risk/evidence",
        json={
            "asset_id": "MAA-" + ("a" * 32),
            "source_id": "caller-asserted-receipt",
            "evidence_kind": "training_run",
            "result": "passed",
            "scope_digest": hashlib.sha256(b"scope").hexdigest(),
            "artifact_digest": hashlib.sha256(b"adapter").hexdigest(),
            "observed_at": now,
            "valid_until": now + 600,
        },
    )
    assert response.status_code == 422




def test_model_risk_assurance_page_is_available():
    response = client.get("/security/assurance")
    assert response.status_code == 200
    assert "Model Risk &amp; AI Assurance Officer" in response.text


def test_red_team_schedule_routes_are_admin_and_actor_bound(monkeypatch):
    from maverick import red_team_assurance as rta

    calls: dict = {}
    monkeypatch.setattr(rta, "enabled", lambda: True)
    monkeypatch.setattr(
        rta,
        "list_schedules",
        lambda **_kwargs: {
            "items": [{"id": "RTS-one", "status": "active"}],
            "count": 1,
            "truncated": False,
            "limit": 100,
        },
    )
    monkeypatch.setattr(
        rta,
        "get_schedule",
        lambda schedule_id: (
            {"id": schedule_id, "status": "active"}
            if schedule_id == "RTS-one"
            else None
        ),
    )

    def create(**kwargs):
        calls["create"] = kwargs
        return {"id": "RTS-one", "status": "active", "revision": 1}

    def cancel(schedule_id, **kwargs):
        calls["cancel"] = (schedule_id, kwargs)
        return {"id": schedule_id, "status": "cancelled", "revision": 2}

    def run_due(**kwargs):
        calls["run_due"] = kwargs
        return {
            "campaigns": [],
            "failures": [],
            "count": 0,
            "failure_count": 0,
            "due_count": 0,
            "truncated": False,
            "limit": kwargs["limit"],
        }

    monkeypatch.setattr(rta, "create_schedule", create)
    monkeypatch.setattr(rta, "cancel_schedule", cancel)
    monkeypatch.setattr(rta, "run_due_schedules", run_due)

    body = {
        "asset_id": "MAA-" + ("a" * 32),
        "external_agent_id": "external-one",
        "idempotency_key": "schedule-one",
        "safe_target_environment": True,
        "cadence_seconds": 3600,
    }
    created = _ok(client.post(
        "/api/v1/security/assurance/red-team/schedules",
        json=body,
    ), 201)
    assert created["status"] == "active"
    assert calls["create"]["actor"] == "local:dashboard"
    assert calls["create"]["cadence_seconds"] == 3600
    assert calls["create"]["first_run_at"] is None

    page = _ok(client.get("/api/v1/security/assurance/red-team/schedules"))
    assert page["schedules"][0]["id"] == "RTS-one"
    assert _ok(client.get(
        "/api/v1/security/assurance/red-team/schedules/RTS-one"
    ))["status"] == "active"
    assert client.get(
        "/api/v1/security/assurance/red-team/schedules/RTS-missing"
    ).status_code == 404

    dispatched = _ok(client.post(
        "/api/v1/security/assurance/red-team/schedules/run-due",
        json={"limit": 5},
    ))
    assert dispatched["count"] == 0
    assert calls["run_due"] == {"actor": "local:dashboard", "limit": 5}

    cancelled = _ok(client.post(
        "/api/v1/security/assurance/red-team/schedules/RTS-one/cancel",
        json={"expected_revision": 1, "rationale": "Cadence retired."},
    ))
    assert cancelled["status"] == "cancelled"
    assert calls["cancel"][1]["actor"] == "local:dashboard"

    # Schema floors hold at the boundary: a sub-5-minute cadence never
    # reaches the module.
    too_fast = dict(body, cadence_seconds=60)
    assert client.post(
        "/api/v1/security/assurance/red-team/schedules",
        json=too_fast,
    ).status_code == 422


def test_red_team_schedule_routes_fail_closed_when_disabled():
    assert client.get(
        "/api/v1/security/assurance/red-team/schedules"
    ).status_code == 403
    assert client.post(
        "/api/v1/security/assurance/red-team/schedules/run-due",
        json={},
    ).status_code == 403
