"""[license] is a real, wizard-written, kernel-read section — lint it clean.

``maverick.entitlements`` reads ``[license]`` (enforce / publisher_pubkeys /
license_file / api_url / api_token / refresh_interval_seconds) and the installer
wizard writes it (pick_license). It was missing from migrate.KNOWN_SECTIONS, so
a go-live operator pinning the license trust anchor got a false "unknown
section" warning from config-lint — exactly the stale-registry class the parity
tests exist to prevent. This pins the fix.
"""
from __future__ import annotations

from maverick.config_lint import lint_config
from maverick.migrate import KNOWN_SECTIONS


def test_license_is_a_known_section():
    assert "license" in KNOWN_SECTIONS


def test_wizard_written_license_table_lints_clean():
    # The exact keys apps/installer-cli pick_license writes.
    cfg = {"license": {
        "enforce": True,
        "publisher_pubkeys": ["aa" * 32],
        "api_url": "https://api.example/license",
        "api_token": "${MAVERICK_LICENSE_API_TOKEN}",
        "refresh_interval_seconds": 60,
    }}
    findings = [f for f in lint_config(cfg) if "unknown" in f.message.lower()]
    assert not findings, [f.message for f in findings]


def test_typo_key_under_license_is_still_flagged():
    # Fixed-key schema: a misspelled trust-anchor key must not slip through as a
    # silently-ignored setting.
    findings = lint_config({"license": {"publisher_pubkey": ["aa" * 32]}})
    assert any("publisher_pubkey" in f.message for f in findings)
