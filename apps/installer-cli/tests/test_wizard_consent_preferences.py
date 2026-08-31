"""Installer reachability for the consumer Consent & Preferences module."""

from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from maverick.config_lint import lint_config
from maverick_installer import wizard


def test_pick_assessments_can_enable_consent_preferences(monkeypatch):
    def confirm(prompt, *, default=False, **_kwargs):
        answers = {
            "Enable consumer Consent & Preferences": True,
            "Enable versioned property": True,
            "Enable the external consumer consent API": True,
            "Enable scheduled website tracker scanning": True,
            "Run the bounded consent scan scheduler": True,
            "Enable independently reviewed scan-to-cookie/privacy disclosure": True,
            "Enable GPC and DNT": True,
            "Enable advertising interoperability": False,
            "Enable downstream preference delivery": True,
            "Enable privacy-safe aggregate consent analytics": True,
            "Enable privacy-preserving consent A/B experiments": True,
            "Enable governed volunteered profile data": True,
            "Enable the unified consumer Trust Center": True,
            "Enable Privacy Automation regulatory research": False,
            "Enable the Data Use Governance": False,
        }
        return next(
            (answer for prefix, answer in answers.items() if prompt.startswith(prefix)),
            default,
        )

    monkeypatch.setattr(
        wizard,
        "_q_confirm",
        confirm,
    )
    monkeypatch.setattr(wizard, "_q_text", lambda *args, **kwargs: "")

    selected = wizard.pick_assessments()

    assert selected["consent_preferences"] == {
        "enable": True,
        "publishing": {
            "enable": True,
            "require_signed_publications": True,
        },
        "public_api": {"enable": True},
        "scanner": {
            "enable": True,
            "notice_proposals": {"enable": True},
            "scheduler": {
                "enable": True,
                "poll_seconds": 60,
                "enqueue_limit": 100,
                "claim_limit": 4,
                "tenant_limit": 1_000,
            },
        },
        "standards": {
            "gpc": True,
            "dnt": True,
            "gpp": False,
            "tcf": False,
            "google_consent_mode": False,
        },
        "delivery": {"enable": True},
        "analytics": {"enable": True},
        "experiments": {"enable": True},
        "profile_data": {"enable": True},
        "trust_center": {"enable": True},
    }
    parsed = tomllib.loads("\n".join(wizard._cfg_assessments(selected)))
    assert parsed["consent_preferences"]["scanner"]["scheduler"] == {
        "enable": True,
        "poll_seconds": 60,
        "enqueue_limit": 100,
        "claim_limit": 4,
        "tenant_limit": 1_000,
    }
    assert parsed["consent_preferences"]["scanner"]["notice_proposals"] == {"enable": True}
    assert parsed["consent_preferences"]["trust_center"] == {"enable": True}
    assert lint_config(parsed) == []


def test_consent_preferences_is_explicit_opt_in(monkeypatch):
    def confirm(prompt, *, default=False, **_kwargs):
        if prompt.startswith("Enable consumer Consent & Preferences"):
            return False
        if prompt.startswith("Enable Privacy Automation regulatory research"):
            return False
        if prompt.startswith("Enable the Data Use Governance"):
            return False
        return default

    monkeypatch.setattr(
        wizard,
        "_q_confirm",
        confirm,
    )
    monkeypatch.setattr(wizard, "_q_text", lambda *args, **kwargs: "")

    selected = wizard.pick_assessments()

    assert "consent_preferences" not in selected


def test_consent_identity_pairing_is_explicit_and_surfaces_existing_oidc_contract(
    monkeypatch,
):
    def confirm(prompt, *, default=False, **_kwargs):
        answers = {
            "Enable consumer Consent & Preferences": True,
            "Enable versioned property": True,
            "Enable the external consumer consent API": True,
            "Enable verified consumer identity federation": True,
            "Enable Privacy Automation regulatory research": False,
            "Enable the Data Use Governance": False,
        }
        return next(
            (answer for prefix, answer in answers.items() if prompt.startswith(prefix)),
            default,
        )

    def text(prompt, *, default=""):
        values = {
            "  Consumer OIDC issuer": "https://id.example.test",
            "  Consumer OIDC audience": "lightwork-consent",
            "  Pinned HTTPS JWKS origin": "https://keys.example.test",
        }
        return next(
            (answer for prefix, answer in values.items() if prompt.startswith(prefix)),
            default,
        )

    monkeypatch.setattr(wizard, "_q_confirm", confirm)
    monkeypatch.setattr(wizard, "_q_text", text)

    selected = wizard.pick_assessments()
    identity = selected["consent_preferences"]["identity"]
    assert identity == {
        "enable": True,
        "issuer": "https://id.example.test",
        "audience": "lightwork-consent",
        "jwks_origin": "https://keys.example.test",
        "token_ttl_seconds": 600,
    }
    parsed = tomllib.loads("\n".join(wizard._cfg_assessments(selected)))
    assert parsed["consent_preferences"]["identity"] == identity
    assert lint_config(parsed) == []


def test_config_writer_emits_separate_consent_preferences_section():
    rendered = wizard._cfg_assessments(
        {
            "consent_preferences": {
                "enable": True,
                "publishing": {
                    "enable": True,
                    "require_signed_publications": True,
                },
                "public_api": {"enable": False},
                "scanner": {"enable": False},
                "standards": {
                    "gpc": True,
                    "dnt": True,
                    "gpp": False,
                    "tcf": False,
                    "google_consent_mode": False,
                },
            }
        }
    )
    parsed = tomllib.loads("\n".join(rendered))

    assert parsed == {
        "consent_preferences": {
            "enable": True,
            "publishing": {
                "enable": True,
                "require_signed_publications": True,
            },
            "public_api": {"enable": False},
            "scanner": {"enable": False},
            "standards": {
                "gpc": True,
                "dnt": True,
                "gpp": False,
                "tcf": False,
                "google_consent_mode": False,
            },
        }
    }


def test_config_writer_keeps_legacy_boolean_consent_choice_compatible():
    rendered = wizard._cfg_assessments({"consent_preferences": True})
    parsed = tomllib.loads("\n".join(rendered))

    assert parsed == {"consent_preferences": {"enable": True}}
