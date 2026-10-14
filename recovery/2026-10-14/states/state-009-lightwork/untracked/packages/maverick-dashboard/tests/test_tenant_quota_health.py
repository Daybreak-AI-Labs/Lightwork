"""Tenant-safe health and metrics projection for the shared quota authority."""
from __future__ import annotations

import json

import pytest


def test_quota_health_reports_mutable_policy_overage_without_raw_tenant(
    monkeypatch, tmp_path,
):
    from maverick import tenant_quota_authority as quota
    from maverick.paths import reset_tenant, set_tenant
    from maverick.world_model import WorldModel
    from maverick_dashboard import automation_queue
    from maverick_dashboard.tenant_quota_health import (
        collect_tenant_quota_statuses,
        quota_prometheus_lines,
    )

    limits = {
        "concurrent_runs": 1,
        "queued_jobs": 2,
        "daily_tokens": 100,
        "daily_spend_dollars": 10,
        "tool_calls": 20,
        "storage_bytes": 1000,
    }
    monkeypatch.setenv("MAVERICK_TENANT_QUOTA_ENFORCE", "1")
    monkeypatch.setenv("MAVERICK_TENANT_QUOTA_POLICY_GENERATION", "1")
    monkeypatch.setenv(
        "MAVERICK_TENANT_QUOTA_LIMITS_JSON",
        json.dumps({"default": limits}),
    )
    world = WorldModel(tmp_path / "quota-health.db")
    token = set_tenant("customer-secret-name")
    try:
        reservation = quota.reserve(
            "customer-secret-name",
            "user:alice",
            {"daily_tokens": 10},
            "settled-run",
            60,
            world=world,
        )
        quota.settle(
            reservation,
            {"daily_tokens": 10},
            world=world,
        )
        lowered = dict(limits)
        lowered["daily_tokens"] = 5
        monkeypatch.setenv(
            "MAVERICK_TENANT_QUOTA_LIMITS_JSON",
            json.dumps({"default": lowered}),
        )
        monkeypatch.setenv("MAVERICK_TENANT_QUOTA_POLICY_GENERATION", "2")

        def run_for_tenants(_operation, fn):
            fn()
            return True

        monkeypatch.setattr(
            automation_queue,
            "_run_for_active_tenants",
            run_for_tenants,
        )
        healthy, records = collect_tenant_quota_statuses(lambda: world)
        metrics = "\n".join(quota_prometheus_lines(records, healthy=healthy))
    finally:
        reset_tenant(token)
        world.close()

    assert healthy is False
    assert 'resource="daily_tokens",standing="over_limit"} 1' in metrics
    assert 'resource="daily_tokens",standing="remaining"} 0' in metrics
    assert "maverick_tenant_quota_policy_generation" in metrics
    assert '} 2' in metrics
    assert "customer-secret-name" not in metrics


def test_quota_health_refuses_incomplete_policy_before_first_tenant(
    monkeypatch,
):
    from maverick import tenant_quota_authority as quota
    from maverick_dashboard.tenant_quota_health import collect_tenant_quota_statuses

    monkeypatch.setenv("MAVERICK_TENANT_QUOTA_ENFORCE", "1")
    monkeypatch.setenv("MAVERICK_TENANT_QUOTA_POLICY_GENERATION", "1")
    monkeypatch.setenv(
        "MAVERICK_TENANT_QUOTA_LIMITS_JSON",
        json.dumps({"default": {"concurrent_runs": 1}}),
    )
    for env_name in quota._ENV_LIMITS.values():
        monkeypatch.delenv(env_name, raising=False)

    with pytest.raises(quota.TenantQuotaPolicyError, match="lacks base ceilings"):
        collect_tenant_quota_statuses(lambda: object())
