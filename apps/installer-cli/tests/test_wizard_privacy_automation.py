"""Installer reachability for Privacy Automation data discovery."""

from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from maverick.config_lint import lint_config
from maverick_installer import wizard


def test_pick_assessments_can_enable_privacy_data_discovery(monkeypatch):
    def confirm(prompt, *, default=False, **_kwargs):
        if prompt.startswith("Enable consumer Consent & Preferences"):
            return False
        if prompt.startswith("Enable Privacy Automation"):
            return True
        if prompt.startswith("Enable Privacy Automation regulatory research"):
            return True
        if prompt.startswith("Enable provider-neutral Privacy Automation rights-request"):
            return True
        if prompt.startswith("Enable one-time consumer delivery"):
            return True
        if prompt.startswith("Enable governed Privacy Automation notice"):
            return True
        if prompt.startswith("Enable the Data Use Governance"):
            return False
        return default

    monkeypatch.setattr(wizard, "_q_confirm", confirm)
    monkeypatch.setattr(wizard, "_q_text", lambda *args, **kwargs: "")

    selected = wizard.pick_assessments()
    assert selected["privacy_automation"] == {
        "enable": True,
        "data_discovery": {"enable": True},
        "regulatory_intelligence": {"enable": True},
        "rights_orchestration": {"enable": True},
        "rights_consumer_delivery": {"enable": True},
        "rights_consumer_intake": {
            "enable": False,
            "request_ttl_seconds": 86_400,
            "retention_seconds": 31_536_000,
            "max_active_requests": 1_000,
            "max_active_requests_per_subject": 25,
        },
        "ott_ctv_rights": {"enable": False},
        "drop_management": {
            "enable": False,
            "retention_seconds": 31_536_000,
        },
        "notice_governance": {"enable": True},
    }
    rendered = wizard._cfg_assessments(selected)
    parsed = tomllib.loads("\n".join(rendered))
    assert parsed["privacy_automation"]["enable"] is True
    assert parsed["privacy_automation"]["data_discovery"]["enable"] is True
    assert parsed["privacy_automation"]["regulatory_intelligence"]["enable"] is True
    assert parsed["privacy_automation"]["rights_orchestration"]["enable"] is True
    assert parsed["privacy_automation"]["rights_consumer_delivery"]["enable"] is True
    assert parsed["privacy_automation"]["rights_consumer_intake"] == {
        "enable": False,
        "request_ttl_seconds": 86_400,
        "retention_seconds": 31_536_000,
        "max_active_requests": 1_000,
        "max_active_requests_per_subject": 25,
    }
    assert parsed["privacy_automation"]["ott_ctv_rights"]["enable"] is False
    assert parsed["privacy_automation"]["drop_management"] == {
        "enable": False,
        "retention_seconds": 31_536_000,
    }
    assert parsed["privacy_automation"]["notice_governance"]["enable"] is True
    assert "privacy_automation" not in parsed.get("assessments", {})
    assert lint_config(parsed) == []


def test_pick_assessments_can_enable_authenticated_consumer_rights_intake(monkeypatch):
    def confirm(prompt, *, default=False, **_kwargs):
        enabled_prefixes = (
            "Enable consumer Consent & Preferences",
            "Enable versioned property",
            "Enable the external consumer consent API",
            "Enable the unified consumer Trust Center",
            "Enable provider-neutral Privacy Automation rights-request",
            "Enable authenticated consumer privacy-rights intake",
        )
        if prompt.startswith(enabled_prefixes):
            return True
        if prompt.startswith("Enable the Data Use Governance"):
            return False
        return False

    monkeypatch.setattr(wizard, "_q_confirm", confirm)
    monkeypatch.setattr(wizard, "_q_text", lambda *args, **kwargs: "")

    selected = wizard.pick_assessments()

    assert selected["consent_preferences"]["publishing"]["enable"] is True
    assert selected["consent_preferences"]["public_api"]["enable"] is True
    assert selected["consent_preferences"]["trust_center"]["enable"] is True
    assert selected["privacy_automation"]["rights_orchestration"]["enable"] is True
    assert selected["privacy_automation"]["rights_consumer_intake"] == {
        "enable": True,
        "request_ttl_seconds": 86_400,
        "retention_seconds": 31_536_000,
        "max_active_requests": 1_000,
        "max_active_requests_per_subject": 25,
    }
    rendered = wizard._cfg_assessments(selected)
    parsed = tomllib.loads("\n".join(rendered))
    assert lint_config(parsed) == []


def test_privacy_data_discovery_is_explicit_opt_in(monkeypatch):
    def confirm(prompt, *, default=False, **_kwargs):
        if prompt.startswith("Enable consumer Consent & Preferences"):
            return False
        if prompt.startswith("Enable Privacy Automation"):
            return False
        if prompt.startswith("Enable Privacy Automation regulatory research"):
            return False
        if prompt.startswith("Enable provider-neutral Privacy Automation rights-request"):
            return False
        if prompt.startswith("Enable one-time consumer delivery"):
            return False
        if prompt.startswith("Enable governed Privacy Automation notice"):
            return False
        if prompt.startswith("Enable the Data Use Governance"):
            return False
        return default

    monkeypatch.setattr(wizard, "_q_confirm", confirm)
    monkeypatch.setattr(wizard, "_q_text", lambda *args, **kwargs: "")
    assert "privacy_automation" not in wizard.pick_assessments()


def test_ott_ctv_rights_is_reachable_only_with_consent_identity_authorities(monkeypatch):
    def confirm(prompt, *, default=False, **_kwargs):
        enabled_prefixes = (
            "Enable consumer Consent & Preferences",
            "Enable versioned property",
            "Enable the external consumer consent API",
            "Enable verified consumer identity federation",
            "Enable the unified consumer Trust Center",
            "Enable provider-neutral Privacy Automation rights-request",
            "Enable the selector-free OTT/CTV service-client rights bridge",
        )
        if prompt.startswith(enabled_prefixes):
            return True
        if prompt.startswith("Enable the Data Use Governance"):
            return False
        return default

    def text(prompt, *, default="", **_kwargs):
        if "OIDC issuer" in prompt:
            return "https://id.example.test"
        if "OIDC audience" in prompt:
            return "lightwork-consent"
        if "JWKS origin" in prompt:
            return "https://keys.example.test"
        return default

    monkeypatch.setattr(wizard, "_q_confirm", confirm)
    monkeypatch.setattr(wizard, "_q_text", text)

    selected = wizard.pick_assessments()
    assert selected["privacy_automation"]["ott_ctv_rights"] == {"enable": True}
    assert selected["consent_preferences"]["identity"]["enable"] is True
    assert selected["consent_preferences"]["trust_center"]["enable"] is True
    parsed = tomllib.loads("\n".join(wizard._cfg_assessments(selected)))
    assert parsed["privacy_automation"]["ott_ctv_rights"]["enable"] is True
    assert lint_config(parsed) == []


def test_drop_management_is_explicit_and_requires_rights_orchestration(monkeypatch):
    def confirm(prompt, *, default=False, **_kwargs):
        if prompt.startswith("Enable provider-neutral Privacy Automation rights-request"):
            return True
        if prompt.startswith("Enable California DROP"):
            return True
        if prompt.startswith("Enable the Data Use Governance"):
            return False
        return False

    monkeypatch.setattr(wizard, "_q_confirm", confirm)
    monkeypatch.setattr(wizard, "_q_text", lambda *args, **kwargs: "")

    selected = wizard.pick_assessments()
    assert selected["privacy_automation"]["rights_orchestration"]["enable"] is True
    assert selected["privacy_automation"]["drop_management"] == {
        "enable": True,
        "retention_seconds": 31_536_000,
    }
    parsed = tomllib.loads("\n".join(wizard._cfg_assessments(selected)))
    assert lint_config(parsed) == []
