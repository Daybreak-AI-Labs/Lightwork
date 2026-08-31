"""Closed configuration authority for Privacy Automation data discovery."""

from __future__ import annotations

from maverick.config_lint import lint_config
from maverick.migrate import KNOWN_SECTIONS


def test_privacy_automation_discovery_is_known_and_valid_when_both_gates_are_true():
    assert "privacy_automation" in KNOWN_SECTIONS
    assert (
        lint_config(
            {
                "privacy_automation": {
                    "enable": True,
                    "data_discovery": {"enable": True},
                    "rights_orchestration": {"enable": True},
                }
            }
        )
        == []
    )


def test_privacy_discovery_requires_root_gate_and_closed_boolean_shape():
    findings = lint_config(
        {
            "privacy_automation": {
                "enable": False,
                "data_discovery": {"enable": True, "raw_samples": True},
            }
        }
    )
    assert any(
        item.key == "enable" and "requires [privacy_automation] enable = true" in item.message
        for item in findings
    )
    assert any(item.key == "raw_samples" and item.severity == "warning" for item in findings)

    wrong_type = lint_config(
        {
            "privacy_automation": {
                "enable": True,
                "data_discovery": {"enable": "yes"},
            }
        }
    )
    assert any(item.key == "enable" and item.severity == "error" for item in wrong_type)


def test_privacy_rights_orchestration_requires_exact_closed_boolean_gate():
    parent_off = lint_config(
        {
            "privacy_automation": {
                "enable": False,
                "rights_orchestration": {
                    "enable": True,
                    "raw_subjects": True,
                },
            }
        }
    )
    assert any(
        item.section == "privacy_automation.rights_orchestration"
        and item.key == "enable"
        and "requires [privacy_automation] enable = true" in item.message
        for item in parent_off
    )
    assert any(
        item.section == "privacy_automation.rights_orchestration"
        and item.key == "raw_subjects"
        and item.severity == "warning"
        for item in parent_off
    )
    wrong_type = lint_config(
        {
            "privacy_automation": {
                "enable": True,
                "rights_orchestration": {"enable": "yes"},
            }
        }
    )
    assert any(
        item.section == "privacy_automation.rights_orchestration"
        and item.key == "enable"
        and item.severity == "error"
        for item in wrong_type
    )


def test_rights_consumer_delivery_requires_orchestration_and_closed_gate():
    assert (
        lint_config(
            {
                "privacy_automation": {
                    "enable": True,
                    "rights_orchestration": {"enable": True},
                    "rights_consumer_delivery": {"enable": True},
                }
            }
        )
        == []
    )

    findings = lint_config(
        {
            "privacy_automation": {
                "enable": True,
                "rights_consumer_delivery": {
                    "enable": True,
                    "raw_response_bytes": True,
                },
            }
        }
    )
    assert any(
        item.section == "privacy_automation.rights_consumer_delivery"
        and item.key == "enable"
        and "requires rights orchestration" in item.message
        for item in findings
    )
    assert any(
        item.section == "privacy_automation.rights_consumer_delivery"
        and item.key == "raw_response_bytes"
        and item.severity == "warning"
        for item in findings
    )

    wrong_type = lint_config(
        {
            "privacy_automation": {
                "enable": True,
                "rights_consumer_delivery": {"enable": "yes"},
            }
        }
    )
    assert any(
        item.section == "privacy_automation.rights_consumer_delivery"
        and item.key == "enable"
        and item.severity == "error"
        for item in wrong_type
    )


def test_ott_ctv_rights_requires_every_identity_and_orchestration_authority():
    valid = {
        "consent_preferences": {
            "enable": True,
            "publishing": {"enable": True},
            "public_api": {"enable": True},
            "identity": {
                "enable": True,
                "issuer": "https://id.example.test",
                "audience": "lightwork-consent",
                "jwks_origin": "https://keys.example.test",
                "token_ttl_seconds": 300,
            },
            "trust_center": {"enable": True},
        },
        "privacy_automation": {
            "enable": True,
            "rights_orchestration": {"enable": True},
            "ott_ctv_rights": {"enable": True},
        },
    }
    assert lint_config(valid) == []

    missing = {
        "privacy_automation": {
            "enable": True,
            "ott_ctv_rights": {"enable": True, "raw_device_id": True},
        }
    }
    findings = lint_config(missing)
    assert any(
        item.section == "privacy_automation.ott_ctv_rights"
        and item.key == "enable"
        and "consent_preferences.identity" in item.message
        and "privacy_automation.rights_orchestration" in item.message
        for item in findings
    )
    assert any(
        item.section == "privacy_automation.ott_ctv_rights"
        and item.key == "raw_device_id"
        and item.severity == "warning"
        for item in findings
    )

    wrong_type = lint_config(
        {
            "privacy_automation": {
                "enable": True,
                "ott_ctv_rights": {"enable": "yes"},
            }
        }
    )
    assert any(
        item.section == "privacy_automation.ott_ctv_rights"
        and item.key == "enable"
        and item.severity == "error"
        for item in wrong_type
    )


def test_privacy_notice_governance_requires_exact_closed_boolean_gate():
    assert (
        lint_config(
            {
                "privacy_automation": {
                    "enable": True,
                    "notice_governance": {"enable": True},
                }
            }
        )
        == []
    )

    findings = lint_config(
        {
            "privacy_automation": {
                "enable": False,
                "notice_governance": {
                    "enable": True,
                    "raw_notice_content": True,
                },
            }
        }
    )
    assert any(
        item.section == "privacy_automation.notice_governance"
        and item.key == "enable"
        and "requires [privacy_automation] enable = true" in item.message
        for item in findings
    )
    assert any(
        item.section == "privacy_automation.notice_governance"
        and item.key == "raw_notice_content"
        and item.severity == "warning"
        for item in findings
    )

    wrong_type = lint_config(
        {
            "privacy_automation": {
                "enable": True,
                "notice_governance": {"enable": "yes"},
            }
        }
    )
    assert any(
        item.section == "privacy_automation.notice_governance"
        and item.key == "enable"
        and item.severity == "error"
        for item in wrong_type
    )
