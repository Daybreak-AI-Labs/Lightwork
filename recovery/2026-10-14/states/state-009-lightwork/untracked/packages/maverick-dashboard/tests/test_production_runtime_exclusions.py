"""Dashboard runtime gates for manifest-excluded production suites."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_MANIFEST = REPO_ROOT / "release" / "production-surface-manifest.json"


@pytest.fixture(autouse=True)
def _production(tmp_path, monkeypatch):
    from maverick import config

    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")
    monkeypatch.setenv("MAVERICK_PRODUCTION_MANIFEST", str(PRODUCTION_MANIFEST))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def _request(path: str = "/") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def _no_io(label: str):
    def blocked(*_args, **_kwargs):
        raise AssertionError(f"{label} was accessed")

    return blocked


def test_runtime_setting_mutators_refuse_before_overlay_io(monkeypatch) -> None:
    from maverick.production_exclusions import ProductionFeatureExcludedError
    from maverick_dashboard import settings_store

    monkeypatch.setattr(settings_store, "_locked", _no_io("settings lock"))
    monkeypatch.setattr(
        settings_store,
        "_load_overlay_for_update",
        _no_io("settings overlay"),
    )
    monkeypatch.setattr(settings_store, "_write", _no_io("settings write"))

    calls = (
        lambda: settings_store.set_provider("openai", api_key="legacy"),
        lambda: settings_store.clear_provider("openai"),
        lambda: settings_store.set_webhooks_secret("legacy"),
        lambda: settings_store.set_security_suite(
            security_ops=True,
            threat_hunt=True,
            env_hunt=True,
            response_execution=True,
            actor="admin",
            expected_revision=1,
        ),
    )
    for invoke in calls:
        with pytest.raises(ProductionFeatureExcludedError):
            invoke()


def test_scim_auth_store_and_access_resolution_stop_before_secret_or_file_io(
    monkeypatch,
) -> None:
    from maverick import secret_provider
    from maverick.production_exclusions import ProductionFeatureExcludedError
    from maverick_dashboard import scim, scim_groups

    monkeypatch.setattr(secret_provider, "get_secret", _no_io("SCIM secret"))
    monkeypatch.setattr(scim, "_read_store", _no_io("SCIM store"))
    monkeypatch.setattr(scim_groups, "_config_table", _no_io("SCIM policy"))

    assert scim.scim_enabled() is False
    assert scim._authorize(_request("/scim/v2/Users")).status_code == 404
    with pytest.raises(ProductionFeatureExcludedError, match="identity:scim"):
        scim._load()
    with pytest.raises(ProductionFeatureExcludedError, match="identity:scim"):
        scim._save({})
    with pytest.raises(ProductionFeatureExcludedError, match="identity:scim"):
        scim._locked_users()
    with pytest.raises(ProductionFeatureExcludedError, match="identity:scim"):
        scim._locked_groups()
    with pytest.raises(ProductionFeatureExcludedError, match="identity:scim"):
        scim._provision_tenant("legacy", "Legacy")
    with pytest.raises(ProductionFeatureExcludedError, match="identity:scim"):
        scim._save_groups({})
    with pytest.raises(ProductionFeatureExcludedError, match="identity:scim"):
        scim._flush_group_audit_outbox()

    assert scim_groups.active_for_principal("user:legacy") is None
    assert scim_groups.group_names_for_principal("user:legacy") == frozenset()
    assert scim_groups.role_for_principal("user:legacy") is None
    assert scim_groups.suites_for_principal("user:legacy") is None


def test_unsupported_background_suites_do_not_read_config_or_start_threads(
    monkeypatch,
) -> None:
    from maverick import config
    from maverick_dashboard import finance_scheduler, security_api

    monkeypatch.setattr(config, "load_config", _no_io("tenant config"))
    monkeypatch.setattr(config, "load_global_config", _no_io("global config"))
    monkeypatch.setattr(config, "config_source_errors", _no_io("config errors"))

    assert finance_scheduler.start_finance_scheduler() is False
    assert finance_scheduler.scheduler_cycle("owner") is False
    finance_scheduler._tenant_cycle("owner", {})
    assert security_api.start_hunter_scheduler() is False
    assert security_api._hunter_scheduler_cycle("owner") is None
    assert security_api._hunter_scheduler_tenant_cycle("owner", {}) is None


def test_voice_and_consequential_dashboard_calls_refuse_before_io(monkeypatch) -> None:
    from maverick import config
    from maverick_dashboard import api, security_api

    class UnreadableUpload:
        async def read(self, _size: int = -1) -> bytes:
            raise AssertionError("voice upload was read")

    monkeypatch.setenv("MAVERICK_VOICE_COMMANDS", "1")
    monkeypatch.setattr(config, "load_config", _no_io("voice config"))
    assert api._voice_commands_enabled() is False

    calls = (
        api.voice_transcribe(object(), UnreadableUpload()),
        api.voice_speak(object(), api.SpeakIn(text="legacy secret")),
        api.voice_captions(object()),
        security_api.update_security_config(object(), object()),
        security_api.execute_environment_response(object(), object()),
    )
    for call in calls:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(call)
        assert exc.value.status_code == 404


def test_required_speech_redaction_fails_closed_outside_production(
    monkeypatch,
) -> None:
    from maverick import config
    from maverick.safety import voice_safety
    from maverick.tools import voice
    from maverick_dashboard import api

    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)
    config.reset_config_cache()
    monkeypatch.setattr(api, "_voice_commands_enabled", lambda: True)
    monkeypatch.setattr(api, "require_permission", lambda *_args: None)
    monkeypatch.setattr(voice_safety, "redaction_required", lambda: True)
    monkeypatch.setattr(
        voice_safety,
        "redact_for_speech",
        _no_io("required speech redactor"),
    )
    monkeypatch.setattr(voice, "_tts_openai", _no_io("OpenAI TTS"))
    monkeypatch.setattr(voice, "_tts_elevenlabs", _no_io("ElevenLabs TTS"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.voice_speak(object(), api.SpeakIn(text="legacy secret")))
    assert exc.value.status_code == 503
    assert "redaction" in str(exc.value.detail)


def test_runtime_setting_mutators_remain_available_outside_production(
    monkeypatch,
) -> None:
    from maverick import config
    from maverick_dashboard import settings_store

    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)
    config.reset_config_cache()

    settings_store.set_provider("openai", api_key="test-only")
    settings_store.set_webhooks_secret("test-only")
    overlay = settings_store.load_overlay()
    assert overlay["providers"]["openai"]["api_key"] == "test-only"
    assert overlay["webhooks"]["secret"] == "test-only"
