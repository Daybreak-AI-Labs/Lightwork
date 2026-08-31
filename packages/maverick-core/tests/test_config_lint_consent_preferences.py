"""Configuration contract for the consumer consent data plane."""

from __future__ import annotations

from maverick.config_lint import lint_config
from maverick.migrate import KNOWN_SECTIONS


def test_consent_preferences_is_a_known_closed_section():
    assert "consent_preferences" in KNOWN_SECTIONS
    assert lint_config({"consent_preferences": {"enable": True}}) == []


def test_consent_preferences_rejects_non_boolean_enable():
    findings = lint_config({"consent_preferences": {"enable": "yes"}})
    assert any(
        item.section == "consent_preferences" and item.key == "enable" and item.severity == "error"
        for item in findings
    )


def test_consent_preferences_warns_on_unknown_authority_key():
    findings = lint_config({"consent_preferences": {"enabled": True}})
    assert any(
        item.section == "consent_preferences"
        and item.key == "enabled"
        and item.severity == "warning"
        for item in findings
    )


def test_consent_preferences_accepts_closed_product_subsystem_contract():
    findings = lint_config(
        {
            "consent_preferences": {
                "enable": True,
                "publishing": {
                    "enable": True,
                    "require_signed_publications": True,
                },
                "public_api": {
                    "enable": True,
                    "token_ttl_seconds": 900,
                    "max_request_bytes": 65_536,
                    "max_response_bytes": 262_144,
                    "requests_per_minute": 120,
                },
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
                    "interval_seconds": 86_400,
                    "max_pages": 100,
                    "max_depth": 4,
                    "page_timeout_seconds": 30,
                    "max_network_events": 5_000,
                    "allow_authenticated_scans": False,
                },
                "standards": {
                    "gpc": True,
                    "dnt": True,
                    "gpp": False,
                    "tcf": False,
                    "google_consent_mode": False,
                },
                "storage": {"backend": "sqlite"},
            }
        }
    )

    assert findings == []


def test_consent_scan_scheduler_requires_scanner_and_has_closed_bounds():
    findings = lint_config(
        {
            "consent_preferences": {
                "enable": True,
                "scanner": {
                    "enable": False,
                    "scheduler": {
                        "enable": True,
                        "poll_seconds": 4,
                        "enqueue_limit": 501,
                        "claim_limit": 0,
                        "tenant_limit": 10_001,
                        "raw_browser_state": True,
                    },
                },
            }
        }
    )

    assert any("requires consent scanner to be enabled" in item.message for item in findings)
    for key in ("poll_seconds", "enqueue_limit", "claim_limit", "tenant_limit"):
        assert any(item.key == key and item.severity == "error" for item in findings)
    assert any(item.key == "raw_browser_state" and item.severity == "warning" for item in findings)


def test_consent_scan_notice_proposals_require_scanner_and_closed_boolean():
    findings = lint_config(
        {
            "consent_preferences": {
                "enable": True,
                "scanner": {
                    "enable": False,
                    "notice_proposals": {
                        "enable": True,
                        "legal_copy": True,
                    },
                },
            }
        }
    )
    assert any(
        "notice proposals require consent scanner to be enabled" in item.message
        for item in findings
    )
    assert any(item.key == "legal_copy" and item.severity == "warning" for item in findings)

    wrong_type = lint_config(
        {
            "consent_preferences": {
                "enable": True,
                "scanner": {
                    "enable": True,
                    "notice_proposals": {"enable": "yes"},
                },
            }
        }
    )
    assert any(
        item.section == "consent_preferences.scanner.notice_proposals"
        and item.key == "enable"
        and item.severity == "error"
        for item in wrong_type
    )


def test_external_consent_subsystems_require_root_and_publishing_authority():
    findings = lint_config(
        {
            "consent_preferences": {
                "enable": False,
                "public_api": {"enable": True},
                "edge_cache": {"enable": True},
            }
        }
    )

    messages = {item.message for item in findings}
    assert any("requires [consent_preferences] enable = true" in item for item in messages)
    assert "public consent API requires consent publishing to be enabled" in messages


def test_consent_identity_and_postgres_require_exact_external_authority():
    findings = lint_config(
        {
            "consent_preferences": {
                "enable": True,
                "identity": {
                    "enable": True,
                    "issuer": "",
                    "audience": "consumers",
                    "jwks_origin": "http://identity.example.test/path",
                },
                "storage": {
                    "backend": "postgres",
                    "dsn_secret_ref": "plaintext-dsn",
                },
            }
        }
    )

    assert any(item.key == "issuer" and item.severity == "error" for item in findings)
    assert any(item.key == "jwks_origin" and item.severity == "error" for item in findings)
    assert any(
        "consent identity requires consent publishing and the public API" in item.message
        for item in findings
    )
    assert any(item.key == "dsn_secret_ref" and item.severity == "error" for item in findings)


def test_consent_nested_bounds_and_unknown_keys_fail_lint():
    findings = lint_config(
        {
            "consent_preferences": {
                "enable": True,
                "scanner": {
                    "enable": "yes",
                    "max_pages": 0,
                    "max_depth": 21,
                    "authenticated_scan_password": "secret",
                },
            }
        }
    )

    assert any(item.key == "enable" and item.severity == "error" for item in findings)
    assert any(item.key == "max_pages" and item.severity == "error" for item in findings)
    assert any(item.key == "max_depth" and item.severity == "error" for item in findings)
    assert any(
        item.key == "authenticated_scan_password" and item.severity == "warning"
        for item in findings
    )


def test_consent_profile_data_requires_exact_closed_boolean_gate():
    assert (
        lint_config(
            {
                "consent_preferences": {
                    "enable": True,
                    "profile_data": {"enable": True},
                }
            }
        )
        == []
    )

    findings = lint_config(
        {
            "consent_preferences": {
                "enable": False,
                "profile_data": {"enable": True, "raw_profile_values": True},
            }
        }
    )
    assert any(
        item.section == "consent_preferences.profile_data"
        and item.key == "enable"
        and "requires [consent_preferences] enable = true" in item.message
        for item in findings
    )
    assert any(
        item.section == "consent_preferences.profile_data"
        and item.key == "raw_profile_values"
        and item.severity == "warning"
        for item in findings
    )

    wrong_type = lint_config(
        {
            "consent_preferences": {
                "enable": True,
                "profile_data": {"enable": "yes"},
            }
        }
    )
    assert any(
        item.section == "consent_preferences.profile_data"
        and item.key == "enable"
        and item.severity == "error"
        for item in wrong_type
    )


def test_consumer_trust_center_requires_public_publishing_authority():
    assert (
        lint_config(
            {
                "consent_preferences": {
                    "enable": True,
                    "publishing": {"enable": True},
                    "public_api": {"enable": True},
                    "trust_center": {"enable": True},
                }
            }
        )
        == []
    )

    findings = lint_config(
        {
            "consent_preferences": {
                "enable": True,
                "trust_center": {"enable": True, "raw_identity": True},
            }
        }
    )
    assert any(
        item.section == "consent_preferences.trust_center"
        and item.key == "enable"
        and "requires consent publishing and the public API" in item.message
        for item in findings
    )
    assert any(
        item.section == "consent_preferences.trust_center"
        and item.key == "raw_identity"
        and item.severity == "warning"
        for item in findings
    )

    wrong_type = lint_config(
        {
            "consent_preferences": {
                "enable": True,
                "trust_center": {"enable": "yes"},
            }
        }
    )
    assert any(
        item.section == "consent_preferences.trust_center"
        and item.key == "enable"
        and item.severity == "error"
        for item in wrong_type
    )
