from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    from maverick import config, world_model
    from maverick_dashboard import api

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MAVERICK_HOME", str(home))
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    for name in (
        "MAVERICK_CONFIG",
        "MAVERICK_CONFIG_OVERLAY",
        "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_DASHBOARD_REQUIRE_AUTH",
        "MAVERICK_OIDC_ENABLED",
        "MAVERICK_PROXY_AUTH",
        "MAVERICK_TENANT",
        "MAVERICK_TENANT_BY_USER",
    ):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    api._world_cache.clear()
    yield
    config.reset_config_cache()
    api._world_cache.clear()


def _client() -> TestClient:
    from maverick_dashboard.app import app

    return TestClient(app, headers={"Origin": "http://testserver"})


def _remote_form(**overrides) -> dict[str, str]:
    values = {
        "backend": "remote",
        "execution_cell_url": "https://cell.example.test",
        "execution_cell_id": "cell-east-1",
        "execution_cell_token": "one-time-cell-secret",
        "monitor": "2",
        "device": "desktop-1080p",
        "consent_mode": "dashboard",
        "max_sessions": "5",
        "session_timeout_seconds": "1800",
        "heartbeat_timeout_seconds": "45",
        "require_evidence": "on",
        "evidence_retention_days": "60",
    }
    values.update(overrides)
    return values


def test_settings_store_seals_cell_and_generated_evidence_secrets() -> None:
    from maverick import config
    from maverick_dashboard import settings_store

    settings_store.set_computer_use_configuration(
        backend="remote",
        execution_cell_url="https://cell.example.test",
        execution_cell_id="cell-east-1",
        execution_cell_token="one-time-cell-secret",
        enroll_evidence_sealing_key=True,
        monitor=2,
        device="desktop-1080p",
        consent_mode="dashboard",
        max_sessions=5,
        session_timeout_seconds=1800,
        heartbeat_timeout_seconds=45,
        require_evidence=True,
        evidence_retention_days=60,
    )

    persisted = config.dashboard_overrides_path().read_text(encoding="utf-8")
    assert "one-time-cell-secret" not in persisted
    assert "MVKAR1:" in persisted
    assert 'mode = "remote"' in persisted
    assert "session_timeout = 1800" in persisted
    assert "heartbeat_timeout = 45" in persisted
    public = settings_store.computer_use_configuration()
    assert public["execution_cell_token_enrolled"] is True
    assert public["evidence_sealing_key_enrolled"] is True
    assert "execution_cell_token" not in public
    assert "evidence_sealing_key" not in public

    effective = config.load_config()["computer_use"]
    assert effective["execution_cell_token"] == "one-time-cell-secret"
    assert effective["evidence_sealing_key"]


def test_evidence_key_is_first_enrollment_only_and_blank_cell_secret_is_preserved() -> None:
    from maverick import config
    from maverick_dashboard import settings_store

    settings_store.set_computer_use_configuration(
        backend="remote",
        execution_cell_url="https://cell.example.test",
        execution_cell_id="cell-east-1",
        execution_cell_token="preserved-cell-secret",
        enroll_evidence_sealing_key=True,
    )
    first = config.load_config()["computer_use"]
    settings_store.set_computer_use_configuration(
        backend="remote",
        execution_cell_url="https://cell.example.test",
        execution_cell_id="cell-east-1",
        execution_cell_token="",
    )
    second = config.load_config()["computer_use"]
    assert second["execution_cell_token"] == "preserved-cell-secret"
    assert second["evidence_sealing_key"] == first["evidence_sealing_key"]

    with pytest.raises(ValueError, match="rotation is unavailable"):
        settings_store.set_computer_use_configuration(
            backend="remote",
            execution_cell_url="https://cell.example.test",
            execution_cell_id="cell-east-1",
            enroll_evidence_sealing_key=True,
        )


def test_host_switch_does_not_silently_delete_cell_secret() -> None:
    from maverick import config
    from maverick_dashboard import settings_store

    settings_store.set_computer_use_configuration(
        backend="remote",
        execution_cell_url="https://cell.example.test",
        execution_cell_id="cell-east-1",
        execution_cell_token="preserved-cell-secret",
    )
    settings_store.set_computer_use_configuration(
        backend="host",
        execution_cell_url="https://cell.example.test",
        execution_cell_id="cell-east-1",
        execution_cell_token="",
    )

    effective = config.load_config()["computer_use"]
    assert effective["mode"] == "host"
    assert effective["execution_cell_token"] == "preserved-cell-secret"
    with pytest.raises(ValueError, match="either a new execution-cell secret or removal"):
        settings_store.set_computer_use_configuration(
            backend="host",
            execution_cell_token="replacement",
            clear_execution_cell_token=True,
        )


@pytest.mark.parametrize(
    "url",
    (
        "http://cell.example.test",
        "http://127.0.0.1:8765",
        "https://user:password@cell.example.test",
        "https://cell.example.test/path",
        "https://cell.example.test?tenant=other",
    ),
)
def test_remote_cell_url_is_a_clean_https_origin(url: str) -> None:
    from maverick_dashboard import settings_store

    with pytest.raises(ValueError):
        settings_store.set_computer_use_configuration(
            backend="remote",
            execution_cell_url=url,
            execution_cell_id="cell-east-1",
        )


def test_settings_page_has_real_write_only_setup_form() -> None:
    response = _client().get("/settings")

    assert response.status_code == 200, response.text
    assert 'action="/settings/computer-use"' in response.text
    for field in (
        "backend",
        "execution_cell_url",
        "execution_cell_id",
        "execution_cell_token",
        "monitor",
        "device",
        "consent_mode",
        "max_sessions",
        "session_timeout_seconds",
        "heartbeat_timeout_seconds",
        "require_evidence",
        "evidence_retention_days",
    ):
        assert f'name="{field}"' in response.text
    assert 'name="execution_cell_token" maxlength="4096"\n                   value=""' in response.text
    assert "Generate and enroll custody key" in response.text
    assert "it does not activate computer use" in response.text


def test_settings_post_persists_setup_without_rendering_secrets() -> None:
    response = _client().post(
        "/settings/computer-use",
        data=_remote_form(enroll_evidence_sealing_key="1"),
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"].endswith("#computer-use-readiness")
    rendered = _client().get("/settings").text
    assert "one-time-cell-secret" not in rendered
    assert "A secret is enrolled and sealed" in rendered
    assert "A generated custody key is enrolled" in rendered
    assert "Rotation and removal are intentionally unavailable" in rendered


def test_computer_use_activation_refuses_before_any_toggle_write(
    monkeypatch,
) -> None:
    from maverick import config
    from maverick.computer_use_runtime import ComputerRuntimeStore

    monkeypatch.setattr(
        ComputerRuntimeStore,
        "readiness",
        lambda _self: {
            "ready": False,
            "blockers": [{"message": "Screenshot custody is not ready"}],
        },
    )
    response = _client().post(
        "/settings/capabilities",
        data={"computer_use": "on", "browser": "on"},
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert config.get_capabilities()["computer_use"] is False
    assert config.get_capabilities()["browser"] is False


def test_computer_use_activation_succeeds_only_after_live_readiness(
    monkeypatch,
) -> None:
    from maverick import config
    from maverick.computer_use_runtime import ComputerRuntimeStore

    monkeypatch.setattr(
        ComputerRuntimeStore,
        "readiness",
        lambda _self: {
            "ready": True,
            "backend_ready": True,
            "approval_ready": True,
            "stop_path_ready": True,
            "visual_evidence_required": True,
            "screenshot_custody_ready": True,
            "blockers": [],
        },
    )
    response = _client().post(
        "/settings/capabilities",
        data={"computer_use": "on"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert config.get_capabilities()["computer_use"] is True
