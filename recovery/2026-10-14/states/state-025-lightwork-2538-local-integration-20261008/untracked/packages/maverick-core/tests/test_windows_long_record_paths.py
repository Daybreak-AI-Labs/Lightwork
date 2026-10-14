"""A Windows installation in a deep folder must retain governed records."""

from __future__ import annotations

import hashlib
import os

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Win32 long-path behavior")
def test_governed_create_and_audit_flush_in_deep_installation(monkeypatch, tmp_path):
    from maverick.governed_records import GovernedRecordStore
    from maverick.paths import tenant_scope

    tenant = "long-path-release"
    namespace = "research-registry"
    suffix = f"tenants/{tenant}/{namespace}"
    padding = max(1, 205 - len(str(tmp_path)) - len(suffix) - 2)
    home = tmp_path / ("h" * padding)
    monkeypatch.setenv("MAVERICK_HOME", str(home))

    with tenant_scope(tenant=tenant):
        store = GovernedRecordStore(namespace, "research-asset", "research-registry-asset")
        record_id = "research-asset-d-" + "a" * 44
        assert len(str(home / suffix / (record_id + ".json"))) > 260
        saved = store.create(
            {"id": record_id, "status": "draft"},
            action="research.registry.dataset.register",
            actor="user:source-owner",
        )
        assert saved["revision"] == 1
        assert store.get(record_id)["id"] == record_id
        assert [row["id"] for row in store.list()] == [record_id]


@pytest.mark.skipif(os.name != "nt", reason="Win32 long-path behavior")
def test_recent_pages_survive_deep_installation(monkeypatch, tmp_path):
    from maverick import experience_preferences
    from maverick.paths import tenant_scope

    tenant = "long-path-release"
    principal = "user:release-reviewer"
    suffix = f"tenants/{tenant}/experience-preferences"
    padding = max(1, 205 - len(str(tmp_path)) - len(suffix) - 2)
    home = tmp_path / ("h" * padding)
    monkeypatch.setenv("MAVERICK_HOME", str(home))
    monkeypatch.setattr(experience_preferences, "enabled", lambda: True)
    filename = hashlib.sha256(principal.encode()).hexdigest() + "-recent.enc"
    assert len(str(home / suffix / filename)) > 260

    with tenant_scope(tenant=tenant):
        assert experience_preferences.record_visit(principal, "/savings") == ["/savings"]
        assert experience_preferences.recent_pages(principal) == ["/savings"]
