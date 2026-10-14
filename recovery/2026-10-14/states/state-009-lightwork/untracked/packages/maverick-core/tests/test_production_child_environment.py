"""Production subprocesses inherit an allowlist, not ambient controller state."""

from __future__ import annotations

from maverick.sandbox.local import (
    PRODUCTION_CHILD_ENV_ALLOWLIST,
    scrub_env,
)


def test_production_child_environment_is_allowlist_only(monkeypatch):
    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")
    source = {
        "PATH": "/trusted/bin",
        "HOME": "/home/maverick",
        "LANG": "C.UTF-8",
        "AWS_PROFILE": "production",
        "CI": "true",
        "CUSTOM_BENIGN_NAME": "still ambient authority",
        "DOCKER_HOST": "tcp://controller.example:2376",
        "GITHUB_ACTIONS": "true",
        "HTTP_PROXY": "http://proxy.example",
        "MAVERICK_CONFIG": "/run/secrets/controller.toml",
        "SSH_AUTH_SOCK": "/run/agent.sock",
    }

    child = scrub_env(source)

    assert child == {
        "PATH": "/trusted/bin",
        "HOME": "/home/maverick",
        "LANG": "C.UTF-8",
    }
    assert set(child) <= PRODUCTION_CHILD_ENV_ALLOWLIST


def test_production_child_environment_still_strips_allowlisted_secret_shape(
    monkeypatch,
):
    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")
    source = {
        "PATH": "/trusted/bin",
        "SSL_CERT_FILE": "/etc/ssl/certs/ca.pem",
        "HOME": "/home/maverick",
    }

    child = scrub_env(source)

    assert child == source


def test_development_keeps_non_secret_compatibility_environment(monkeypatch):
    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)
    monkeypatch.delenv("MAVERICK_CHILD_ENV_ALLOWLIST_ONLY", raising=False)
    source = {
        "PATH": "/dev/bin",
        "CUSTOM_BENIGN_NAME": "kept for compatibility",
        "OPENAI_API_KEY": "must-not-leak",
    }

    child = scrub_env(source)

    assert child["CUSTOM_BENIGN_NAME"] == "kept for compatibility"
    assert "OPENAI_API_KEY" not in child


def test_malformed_allowlist_policy_fails_closed(monkeypatch):
    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)
    monkeypatch.setenv("MAVERICK_CHILD_ENV_ALLOWLIST_ONLY", "maybe")

    child = scrub_env({"PATH": "/bin", "CUSTOM": "ambient"})

    assert child == {"PATH": "/bin"}
