"""Consent receipts: salted digests, self-checking receipts, projections.

The assertions that matter: the raw subject reference never appears in a
stored record or export; each event's receipt hash recomputes exactly; the
preference projection reflects the latest event per purpose; and the
OneTrust importer maps status vocabulary and skips what it cannot map.
"""
from __future__ import annotations

import hashlib
import json

import pytest
from maverick import config
from maverick import consent_registry as cr

_SALT = "unit-test-salt-0123456789abcdef"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    from maverick import audit

    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(audit, "record_global", lambda *_args, **_kwargs: True)
    (tmp_path / "config.toml").write_text(
        f'[consent_registry]\nenable = true\nsubject_salt = "{_SALT}"\n',
        encoding="utf-8",
    )
    config.reset_config_cache()
    yield tmp_path
    config.reset_config_cache()


def _grant(subject="alice@example.com", purpose="marketing emails"):
    return cr.record_consent(
        subject, purpose=purpose, status="granted",
        policy_version="2026-01", channel="web", actor="ops",
    )


def test_registry_is_off_by_default_and_salt_is_mandatory(tmp_path):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    assert cr.enabled() is False
    with pytest.raises(cr.ConsentRegistryConfigError, match="enable"):
        _grant()
    # Enabled but with a salt too short to pseudonymize anything: refused.
    (tmp_path / "config.toml").write_text(
        '[consent_registry]\nenable = true\nsubject_salt = "short"\n',
        encoding="utf-8",
    )
    config.reset_config_cache()
    assert cr.enabled() is False
    with pytest.raises(cr.ConsentRegistryConfigError, match="subject_salt"):
        _grant()


def test_subject_reference_is_stored_only_as_a_salted_digest():
    record = _grant()
    expected = hashlib.sha256(
        f"consent-subject:{_SALT}:alice@example.com".encode()
    ).hexdigest()[:32]
    assert record["subject_digest"] == expected
    assert "alice" not in json.dumps(record)
    for row in cr.export_receipts():
        assert "alice" not in json.dumps(row)


def test_receipt_hash_recomputes_exactly():
    record = _grant()
    event = dict(record["history"][0])
    claimed = event.pop("receipt_sha256")
    recomputed = hashlib.sha256(
        json.dumps(
            {
                "subject": record["subject_digest"],
                "purpose": record["purpose"],
                **event,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert claimed == recomputed


def test_preferences_reflect_the_latest_event_per_purpose():
    _grant()
    _grant(purpose="product analytics")
    cr.withdraw_consent(
        "alice@example.com", purpose="marketing emails",
        policy_version="2026-02", channel="web", actor="ops",
    )
    prefs = cr.subject_preferences("alice@example.com")
    assert prefs["purposes"]["marketing_emails"]["status"] == "withdrawn"
    assert prefs["purposes"]["product_analytics"]["status"] == "granted"
    posture = cr.consent_posture()
    assert posture["records"] == 2
    assert posture["by_purpose"]["marketing_emails"]["withdrawn"] == 1

    record = cr.record_consent(
        "alice@example.com", purpose="marketing emails", status="granted",
        policy_version="2026-03", channel="mobile", actor="ops",
    )
    assert len(record["history"]) == 3
    assert record["current_status"] == "granted"


def test_status_channel_and_time_are_validated():
    with pytest.raises(ValueError, match="status"):
        cr.record_consent(
            "bob", purpose="p", status="maybe",
            policy_version="v1", channel="web", actor="ops",
        )
    with pytest.raises(ValueError, match="channel"):
        cr.record_consent(
            "bob", purpose="p", status="granted",
            policy_version="v1", channel="carrier-pigeon", actor="ops",
        )
    with pytest.raises(ValueError, match="recorded_at"):
        cr.record_consent(
            "bob", purpose="p", status="granted",
            policy_version="v1", channel="web", actor="ops",
            recorded_at=-5.0,
        )


def test_onetrust_import_maps_statuses_and_skips_the_unmappable():
    csv_text = (
        "Data Subject Identifier,Purpose,Consent Status,"
        "Purpose Version,Transaction Date\n"
        "carol@example.com,Newsletters,Opt-In,3.1,2026-02-01T10:00:00Z\n"
        "dave@example.com,Newsletters,Withdrawn,3.1,2026-02-02\n"
        "erin@example.com,Newsletters,Pending,3.1,2026-02-03\n"
        ",Newsletters,Opt-In,3.1,2026-02-04\n"
    )
    result = cr.import_onetrust_consents(csv_text, imported_by="ops")
    assert result == {"imported": 2, "skipped": 2}
    granted = cr.subject_preferences("carol@example.com")
    assert granted["purposes"]["newsletters"]["status"] == "granted"
    withdrawn = cr.subject_preferences("dave@example.com")
    assert withdrawn["purposes"]["newsletters"]["status"] == "withdrawn"
    # The export timestamp survived the import (2026-02-01T10:00:00Z).
    record_time = granted["purposes"]["newsletters"]["last_recorded_at"]
    assert abs(record_time - 1769940000.0) < 1.0
