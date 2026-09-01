"""In-app governance-module switches (/api/v1/features/modules).

Every tenant-facing module used to fail closed behind a config-file-only
[section] enable flag, with workbench banners telling the user to edit
config.toml. These endpoints make the gates switchable from the module's own
page: admin-gated, allowlisted, persisted through the settings overlay.
Turning a module ON applies its full requirement chain in one atomic write
(one click must open a WORKING workbench, never a child under a dead
parent); turning it OFF disables only the module itself.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from maverick import config

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def _module(section):
    rows = client.get("/api/v1/features/modules").json()["modules"]
    return next(m for m in rows if m["section"] == section)


def test_modules_list_defaults_off_with_labels():
    rows = client.get("/api/v1/features/modules").json()["modules"]
    assert rows, "registry must not be empty"
    by_section = {m["section"]: m for m in rows}
    assert "consent_preferences" in by_section
    assert "privacy_automation.data_discovery" in by_section
    for m in rows:
        assert m["label"]
        assert m["enabled"] is False  # fail-closed defaults


def test_enable_applies_the_full_requirement_chain_atomically():
    r = client.post(
        "/api/v1/features/modules",
        json={"section": "consent_preferences.analytics", "enabled": True},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"section": "consent_preferences.analytics", "enabled": True}
    # The parent gate came on with it -- the one click opened a working module.
    assert _module("consent_preferences")["enabled"] is True
    assert _module("consent_preferences.analytics")["enabled"] is True


def test_disable_turns_off_only_the_module_itself():
    client.post(
        "/api/v1/features/modules",
        json={"section": "consent_preferences.analytics", "enabled": True},
    )
    r = client.post(
        "/api/v1/features/modules",
        json={"section": "consent_preferences.analytics", "enabled": False},
    )
    assert r.json()["enabled"] is False
    # Siblings/parents keep running.
    assert _module("consent_preferences")["enabled"] is True


def test_a_child_under_a_disabled_parent_reads_as_off():
    client.post(
        "/api/v1/features/modules",
        json={"section": "consent_preferences.analytics", "enabled": True},
    )
    client.post(
        "/api/v1/features/modules",
        json={"section": "consent_preferences", "enabled": False},
    )
    # The child's own flag is still true in the overlay, but its effective
    # state honors the whole chain.
    assert _module("consent_preferences.analytics")["enabled"] is False


def test_unknown_module_is_400():
    r = client.post(
        "/api/v1/features/modules",
        json={"section": "totally_made_up", "enabled": True},
    )
    assert r.status_code == 400


def test_switching_requires_global_admin(monkeypatch):
    import maverick_dashboard.auth as auth

    monkeypatch.setattr(auth, "caller_principal", lambda request: "user:viewer")
    monkeypatch.setattr(auth, "role_for_principal", lambda principal: "viewer")
    monkeypatch.setattr(auth, "global_role_for_principal", lambda principal: "viewer")
    r = client.post(
        "/api/v1/features/modules",
        json={"section": "consent_preferences", "enabled": True},
    )
    assert r.status_code == 403
    # Reading module state stays open to any signed-in user.
    assert client.get("/api/v1/features/modules").status_code == 200


def test_switch_survives_other_overlay_writes():
    """_dump is an allowlist serializer; a module table it didn't know would
    be silently dropped by the NEXT save (the active_tenant bug class)."""
    from maverick_dashboard import settings_store

    client.post(
        "/api/v1/features/modules",
        json={"section": "third_party_management.lifecycle", "enabled": True},
    )
    settings_store.set_section_enable("dreaming", True)  # unrelated overlay write
    assert _module("third_party_management.lifecycle")["enabled"] is True


def test_enabling_in_app_unlocks_the_workbench_page():
    """The user-visible promise end to end: the /consent page loses its gate
    banner (and its in-page Turn on control) once the module is switched on."""
    page = client.get("/consent").text
    assert 'data-module-gate="consent_preferences"' in page

    r = client.post(
        "/api/v1/features/modules",
        json={"section": "consent_preferences", "enabled": True},
    )
    assert r.status_code == 200
    page = client.get("/consent").text
    assert 'data-module-gate="consent_preferences"' not in page
    # Analytics is independently switched and still off.
    assert 'data-module-gate="consent_preferences.analytics"' in page


def test_registry_matches_store_allowlist():
    import maverick_dashboard.api as api
    from maverick_dashboard import settings_store

    used: set[str] = set()
    for section, meta in api._MODULE_SWITCHES.items():
        used.add(section)
        used.update(meta["requires"])
        # Every requirement is itself a switchable row, so the UI can also
        # show/flip parents individually.
        for req in meta["requires"]:
            assert req in api._MODULE_SWITCHES
    assert used == set(settings_store.MODULE_ENABLE_PATHS)
