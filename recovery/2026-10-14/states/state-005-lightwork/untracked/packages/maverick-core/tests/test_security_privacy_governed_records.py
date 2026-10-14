"""Security/GRC and privacy products use the shared governed-record seam."""

from __future__ import annotations

import itertools

import pytest
from maverick import privacy_ops, security_ops
from maverick.governed_records import (
    GovernedRecordBackendError,
    GovernedRecordStore,
    PostgresGovernedRecordBackend,
)
from maverick.paths import tenant_scope


@pytest.fixture(autouse=True)
def _isolated_governed_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "1")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    for name in (
        "MAVERICK_GOVERNED_RECORDS_BACKEND",
        "MAVERICK_PG_DSN",
        "MAVERICK_WORLD_BACKEND",
    ):
        monkeypatch.delenv(name, raising=False)
    import maverick.audit as audit

    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)


@pytest.mark.parametrize(
    ("collection", "namespace", "record_type"),
    [
        (privacy_ops._DPA, "dpa_reviews", "dpa_review"),
        (privacy_ops._AI, "ai_registry", "ai_system"),
        (privacy_ops._ROPA, "ropa", "ropa"),
        (privacy_ops._DSAR, "dsar_requests", "dsar"),
        (privacy_ops._INCIDENT, "privacy_incidents", "incident"),
        (security_ops._CONTROLS, "security_controls", "control"),
        (security_ops._EVIDENCE, "security_evidence", "evidence"),
        (security_ops._RISKS, "security_risks", "risk"),
        (security_ops._POAMS, "security_poam", "poam"),
        (security_ops._VENDORS, "security_vendors", "vendor"),
        (security_ops._POLICIES, "security_policies", "policy"),
        (security_ops._INCIDENTS, "security_incidents", "incident"),
        (security_ops._ENGAGEMENTS, "security_audits", "audit_engagement"),
        (security_ops._CLOCKS, "security_clock_packs", "regulatory_clock"),
    ],
)
def test_product_registers_resolve_exact_governed_namespaces(
    collection,
    namespace,
    record_type,
):
    governed = collection._governed()
    assert isinstance(governed, GovernedRecordStore)
    assert collection.namespace == governed.namespace == namespace
    assert collection.record_type == governed.record_type == record_type


def test_governed_products_preserve_ids_revisions_timestamps_and_order(monkeypatch):
    ticks = itertools.count(1_000_000)
    monkeypatch.setattr(privacy_ops.time, "time", lambda: float(next(ticks)))

    first = privacy_ops.register_ai_system("First", "Internal filter")
    second = privacy_ops.register_ai_system("Second", "Support chatbot")
    assert first["id"].startswith("AI-")
    assert second["id"].startswith("AI-")
    assert first["revision"] == second["revision"] == 1
    assert first["created_at"] == first["updated_at"]
    assert second["created_at"] == second["updated_at"]
    assert privacy_ops.list_ai_systems()[0]["id"] == second["id"]

    policy = security_ops.create_policy(
        "Access control",
        "security-owner",
        created_by="ciso",
    )
    created_at = policy["created_at"]
    updated = security_ops.transition_policy(
        policy["id"],
        "review",
        "reviewer",
        expected_revision=policy["revision"],
    )
    assert updated is not None
    assert updated["revision"] == policy["revision"] + 1
    assert updated["created_at"] == created_at
    assert updated["updated_at"] > policy["updated_at"]


def test_security_and_privacy_governed_records_are_tenant_isolated():
    with tenant_scope(tenant="tenant-a"):
        ai_a = privacy_ops.register_ai_system("Tenant A", "Internal filter")
        policy_a = security_ops.create_policy("A policy", "owner-a")

    with tenant_scope(tenant="tenant-b"):
        assert privacy_ops.get_ai_system(ai_a["id"]) is None
        assert security_ops.get_policy(policy_a["id"]) is None
        ai_b = privacy_ops.register_ai_system("Tenant B", "Support chatbot")
        policy_b = security_ops.create_policy("B policy", "owner-b")
        assert [row["id"] for row in privacy_ops.list_ai_systems()] == [ai_b["id"]]
        assert [row["id"] for row in security_ops.list_policies()] == [policy_b["id"]]

    with tenant_scope(tenant="tenant-a"):
        assert [row["id"] for row in privacy_ops.list_ai_systems()] == [ai_a["id"]]
        assert [row["id"] for row in security_ops.list_policies()] == [policy_a["id"]]


def test_shared_backend_failures_surface_without_local_fallback(monkeypatch):
    monkeypatch.setenv("MAVERICK_GOVERNED_RECORDS_BACKEND", "postgres")
    monkeypatch.setenv(
        "MAVERICK_PG_DSN",
        "postgresql://runtime:secret@database.invalid/lightwork",  # pragma: allowlist secret
    )

    def unavailable(self):
        raise OSError("provider detail must be redacted")

    monkeypatch.setattr(PostgresGovernedRecordBackend, "_connect", unavailable)
    with tenant_scope(tenant="tenant-a"):
        with pytest.raises(
            GovernedRecordBackendError,
            match="shared governed-record schema cannot be verified",
        ) as privacy_failure:
            privacy_ops.list_ai_systems()
        assert "provider detail" not in str(privacy_failure.value)
        with pytest.raises(
            GovernedRecordBackendError,
            match="shared governed-record schema cannot be verified",
        ) as security_failure:
            security_ops.list_policies()
        assert "provider detail" not in str(security_failure.value)
