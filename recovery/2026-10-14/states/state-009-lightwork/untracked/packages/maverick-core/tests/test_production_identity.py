"""Adverse tests for the secret-free production identity/RBAC posture."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from maverick import config as config_module
from maverick.production_identity import (
    CREDENTIAL_FIELDS_EXCLUDED,
    POSTURE_CONTRACT_SCHEMA,
    POSTURE_DIGEST_ENV,
    POSTURE_SCHEMA,
    PROVIDER_ENV,
    ProductionIdentityError,
    runtime_identity_posture,
    validate_runtime_identity_posture,
)

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "release" / "production-identity-posture-contract.json"


@pytest.fixture(autouse=True)
def _isolate_identity_environment(monkeypatch):
    for name in (
        "MAVERICK_DASHBOARD_ADMINS",
        "MAVERICK_DASHBOARD_INVITES",
        "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_OIDC_ALGORITHMS",
        "MAVERICK_OIDC_AUDIENCE",
        "MAVERICK_OIDC_AUTHORIZATION_ENDPOINT",
        "MAVERICK_OIDC_CLIENT_ID",
        "MAVERICK_OIDC_CLIENT_SECRET",
        "MAVERICK_OIDC_ENABLED",
        "MAVERICK_OIDC_ISSUER",
        "MAVERICK_OIDC_JWKS_URI",
        "MAVERICK_OIDC_REDIRECT_URI",
        "MAVERICK_OIDC_SESSION_SECRET",
        "MAVERICK_OIDC_TOKEN_ENDPOINT",
        "MAVERICK_PROXY_AUTH",
        "MAVERICK_PROXY_AUTH_HEADER",
        "MAVERICK_SAML_ENABLED",
        POSTURE_DIGEST_ENV,
        PROVIDER_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def _oidc_config() -> dict:
    return {
        "auth": {
            "oidc": {
                "enabled": True,
                "issuer": "https://idp.example.test/",
                "audience": "lightwork",
                "jwks_uri": "https://idp.example.test/jwks.json",
                "algorithms": ["RS256", "ES256"],
                "client_id": "lightwork-dashboard",
                "redirect_uri": "https://lightwork.example.test/auth/callback",
                "client_secret": "first-client-secret",
                "session_secret": "first-session-secret",
            },
            "proxy": {"enabled": False},
        },
        "dashboard": {
            "default_role": "viewer",
            "admins": ["user:break-glass"],
            "default_suites": [],
            "group_roles": {"Platform Admins": "admin", "Operators": "operator"},
            "group_suites": {"Finance": ["finance"]},
        },
        "roles": {
            "restricted": {
                "allow_tools": ["knowledge_search"],
                "deny_tools": ["exec"],
                "max_risk": "low",
                "allow_paths": ["/workspace/**"],
                "allow_hosts": [],
            }
        },
        "role_assignments": {"default": "restricted"},
    }


def _install_config(monkeypatch, document: dict) -> None:
    monkeypatch.setattr(config_module, "load_config", lambda *args, **kwargs: document)


def test_oidc_posture_is_canonical_secret_free_and_secret_rotation_stable(
    monkeypatch,
) -> None:
    config = _oidc_config()
    _install_config(monkeypatch, config)
    first = runtime_identity_posture(config=config)
    encoded = first.canonical_bytes.decode("ascii")

    assert first.provider == "oidc"
    assert first.document["schema"] == POSTURE_SCHEMA
    assert first.document["identity_mapping"]["client_id"] == "lightwork-dashboard"
    assert first.document["rbac_policy_sha256"] == first.rbac_policy_sha256
    assert "first-client-secret" not in encoded
    assert "first-session-secret" not in encoded
    assert set(CREDENTIAL_FIELDS_EXCLUDED) == set(
        first.document["credential_fields_excluded"]
    )

    config["auth"]["oidc"]["client_secret"] = "rotated-client-secret"
    config["auth"]["oidc"]["session_secret"] = "rotated-session-secret"
    monkeypatch.setenv("MAVERICK_OIDC_CLIENT_SECRET", "environment-client-secret")
    monkeypatch.setenv("MAVERICK_OIDC_SESSION_SECRET", "environment-session-secret")
    rotated = runtime_identity_posture(config=config)
    assert rotated.document == first.document
    assert rotated.sha256 == first.sha256
    assert "rotated" not in rotated.canonical_bytes.decode("ascii")
    assert "environment" not in rotated.canonical_bytes.decode("ascii")


def test_provider_and_static_rbac_drift_change_the_posture(monkeypatch) -> None:
    config = _oidc_config()
    _install_config(monkeypatch, config)
    baseline = runtime_identity_posture(config=config)

    rbac_drift = copy.deepcopy(config)
    rbac_drift["dashboard"]["group_roles"]["Operators"] = "viewer"
    _install_config(monkeypatch, rbac_drift)
    changed_rbac = runtime_identity_posture(config=rbac_drift)
    assert changed_rbac.rbac_policy_sha256 != baseline.rbac_policy_sha256
    assert changed_rbac.sha256 != baseline.sha256

    proxy = copy.deepcopy(config)
    proxy["auth"]["oidc"]["enabled"] = False
    proxy["auth"]["proxy"] = {
        "enabled": True,
        "header": "X-Authenticated-User",
        "trusted_proxies": ["10.20.30.40", "10.20.31.0/24"],
        "trust_loopback": False,
    }
    _install_config(monkeypatch, proxy)
    changed_provider = runtime_identity_posture(config=proxy)
    assert changed_provider.provider == "trusted_proxy_sso"
    assert changed_provider.sha256 != baseline.sha256


def test_explicit_config_snapshot_is_authoritative_over_global_reload(monkeypatch) -> None:
    supplied = _oidc_config()
    globally_reloaded = copy.deepcopy(supplied)
    globally_reloaded["auth"]["oidc"].update(
        issuer="https://different-idp.example.test/",
        audience="different-audience",
        client_id="different-client",
    )
    _install_config(monkeypatch, globally_reloaded)

    posture = runtime_identity_posture(config=supplied)
    mapping = posture.document["identity_mapping"]
    assert mapping["issuer"] == "https://idp.example.test/"
    assert mapping["audience"] == "lightwork"
    assert mapping["client_id"] == "lightwork-dashboard"


@pytest.mark.parametrize(
    ("dashboard", "message"),
    (
        ({"admins": ["user:break-glass"]}, "default_role = viewer"),
        (
            {"default_role": "operator", "admins": ["user:break-glass"]},
            "must be viewer",
        ),
        ({"default_role": "viewer", "admins": []}, "bootstrap/break-glass"),
    ),
)
def test_production_rbac_requires_deny_by_default_and_break_glass_admin(
    monkeypatch,
    dashboard: dict,
    message: str,
) -> None:
    config = _oidc_config()
    config["dashboard"] = dashboard
    _install_config(monkeypatch, config)
    with pytest.raises(ProductionIdentityError, match=message):
        runtime_identity_posture(config=config)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda cfg: cfg["auth"]["oidc"].update(enabled=False), "exactly one"),
        (
            lambda cfg: cfg["auth"]["proxy"].update(
                enabled=True,
                trusted_proxies=["10.0.0.10"],
            ),
            "exactly one",
        ),
        (
            lambda cfg: cfg["auth"]["oidc"].update(issuer="http://idp.invalid"),
            "HTTPS URL",
        ),
        (
            lambda cfg: cfg["auth"]["oidc"].update(algorithms=["RS256", "HS256"]),
            "asymmetric",
        ),
        (
            lambda cfg: cfg["auth"].update(saml={"enabled": False}),
            "SAML",
        ),
    ),
)
def test_zero_both_malformed_and_saml_identity_fail_closed(
    monkeypatch,
    mutation,
    message: str,
) -> None:
    config = _oidc_config()
    mutation(config)
    _install_config(monkeypatch, config)
    with pytest.raises(ProductionIdentityError, match=message):
        runtime_identity_posture(config=config)


def test_static_and_malformed_proxy_identity_fail_closed(monkeypatch) -> None:
    config = _oidc_config()
    _install_config(monkeypatch, config)
    monkeypatch.setenv("MAVERICK_DASHBOARD_TOKEN", "shared-secret")
    with pytest.raises(ProductionIdentityError, match="static dashboard"):
        runtime_identity_posture(config=config)

    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN")
    config["auth"]["oidc"]["enabled"] = False
    config["auth"]["proxy"] = {"enabled": True, "trusted_proxies": []}
    with pytest.raises(ProductionIdentityError, match="trusted_proxies"):
        runtime_identity_posture(config=config)


def test_trusted_provider_and_posture_pins_reject_missing_unsupported_and_drift(
    monkeypatch,
) -> None:
    config = _oidc_config()
    _install_config(monkeypatch, config)
    posture = runtime_identity_posture(config=config)

    with pytest.raises(ProductionIdentityError, match=PROVIDER_ENV):
        validate_runtime_identity_posture(config=config)

    monkeypatch.setenv(PROVIDER_ENV, "saml")
    monkeypatch.setenv(POSTURE_DIGEST_ENV, posture.sha256)
    with pytest.raises(ProductionIdentityError, match=PROVIDER_ENV):
        validate_runtime_identity_posture(config=config)

    monkeypatch.setenv(PROVIDER_ENV, "trusted_proxy_sso")
    with pytest.raises(ProductionIdentityError, match="trusted runtime selection"):
        validate_runtime_identity_posture(config=config)

    monkeypatch.setenv(PROVIDER_ENV, "oidc")
    monkeypatch.setenv(POSTURE_DIGEST_ENV, "f" * 64)
    with pytest.raises(ProductionIdentityError, match="trusted runtime digest"):
        validate_runtime_identity_posture(config=config)

    monkeypatch.setenv(POSTURE_DIGEST_ENV, posture.sha256)
    assert validate_runtime_identity_posture(config=config).sha256 == posture.sha256

    # The provider remains OIDC, but its token-verification trust root changes.
    # A provider-only request gate would miss this authority-changing drift.
    issuer_drift = copy.deepcopy(config)
    issuer_drift["auth"]["oidc"]["issuer"] = "https://replacement-idp.example.test/"
    with pytest.raises(ProductionIdentityError, match="trusted runtime digest"):
        validate_runtime_identity_posture(config=issuer_drift)

    # Static group-to-role policy is equally live and must retain the same pin.
    rbac_drift = copy.deepcopy(config)
    rbac_drift["dashboard"]["group_roles"]["Operators"] = "admin"
    with pytest.raises(ProductionIdentityError, match="trusted runtime digest"):
        validate_runtime_identity_posture(config=rbac_drift)


def test_repository_posture_contract_is_provider_neutral_and_exact() -> None:
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert document["schema"] == POSTURE_CONTRACT_SCHEMA
    assert document["selection_cardinality"] == "exactly_one"
    assert document["supported_providers"] == ["oidc", "trusted_proxy_sso"]
    assert "selected_provider" not in document
    assert document["mutable_authorization_state"]["included_in_digest"] is False
    assert document["credential_fields_excluded"] == list(
        CREDENTIAL_FIELDS_EXCLUDED
    )
