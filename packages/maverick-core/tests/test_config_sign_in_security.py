"""[auth.mfa], [auth.ip_allowlist] and [auth.sessions] knobs: defaults,
config values, environment overrides and fail-closed handling of bad values."""
from __future__ import annotations

import pytest
from maverick import config


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    for name in ("MAVERICK_MFA_REQUIRE", "MAVERICK_MFA_STEP_UP_MINUTES",
                 "MAVERICK_IP_ALLOWLIST"):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def _write(tmp_path, text: str) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")
    config.reset_config_cache()


def test_defaults_are_off_and_permissive():
    mfa = config.get_auth_mfa()
    assert mfa["require"] is False and mfa["step_up_minutes"] == 15
    assert mfa["amr_values"] == list(config.DEFAULT_MFA_AMR_VALUES)
    assert mfa["acr_values"] == []
    assert mfa["saml_authn_contexts"] == list(config.DEFAULT_MFA_SAML_CONTEXTS)
    assert config.get_ip_allowlist() == {"cidrs": [], "workspace_cidrs": []}
    assert config.get_auth_sessions() == {"retention_days": 30}


def test_config_values_are_read(tmp_path):
    _write(tmp_path, """
[auth.mfa]
require = true
step_up_minutes = 30
amr_values = ["otp", " fido "]
acr_values = ["urn:x:mfa"]
saml_authn_contexts = ["urn:x:saml"]

[auth.ip_allowlist]
cidrs = ["10.0.0.0/8", "192.0.2.1"]
workspace_cidrs = ["10.1.0.0/16"]

[auth.sessions]
retention_days = 7
""")
    mfa = config.get_auth_mfa()
    assert mfa == {"require": True, "step_up_minutes": 30, "amr_values": ["otp", "fido"],
                   "acr_values": ["urn:x:mfa"], "saml_authn_contexts": ["urn:x:saml"]}
    assert config.get_ip_allowlist() == {"cidrs": ["10.0.0.0/8", "192.0.2.1"],
                                         "workspace_cidrs": ["10.1.0.0/16"]}
    assert config.get_auth_sessions() == {"retention_days": 7}


def test_environment_wins(tmp_path, monkeypatch):
    _write(tmp_path, "[auth.mfa]\nrequire = true\nstep_up_minutes = 30\n"
                     "[auth.ip_allowlist]\ncidrs = [\"10.0.0.0/8\"]\n")
    monkeypatch.setenv("MAVERICK_MFA_REQUIRE", "false")
    monkeypatch.setenv("MAVERICK_MFA_STEP_UP_MINUTES", "3")
    monkeypatch.setenv("MAVERICK_IP_ALLOWLIST", "192.0.2.0/24, 198.51.100.7")
    mfa = config.get_auth_mfa()
    assert mfa["require"] is False and mfa["step_up_minutes"] == 3
    assert config.get_ip_allowlist()["cidrs"] == ["192.0.2.0/24", "198.51.100.7"]


def test_bad_values_fail_closed_or_fall_back(tmp_path):
    _write(tmp_path, "[auth.mfa]\nrequire = \"maybe\"\nstep_up_minutes = \"soon\"\n"
                     "amr_values = \"otp, sms\"\n[auth.sessions]\nretention_days = -4\n")
    mfa = config.get_auth_mfa()
    assert mfa["require"] is True            # a non-bool security switch counts as on
    assert mfa["step_up_minutes"] == 15      # nonsense: the documented default
    assert mfa["amr_values"] == ["otp", "sms"]  # a comma string is tolerated
    assert config.get_auth_sessions()["retention_days"] == 1
