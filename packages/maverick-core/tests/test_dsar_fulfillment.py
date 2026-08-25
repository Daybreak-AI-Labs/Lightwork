"""DSAR fulfillment: identity gate, token hygiene, delivery-time redaction.

The assertions that matter: no response link mints before a named human
records identity verification; the token is stored only as a digest and
never re-readable; expiry and wrong tokens read identically as None; the
delivered bundle is scrubbed of third-party identifiers while the
subject's own reference survives; the CLI's method choices cannot drift
from the module's vocabulary.
"""
from __future__ import annotations

import time

import pytest
from maverick import config
from maverick import dsar_fulfillment as df


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    from maverick import audit, world_model

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr(audit, "record", lambda *_a, **_k: True)
    monkeypatch.setattr(audit, "record_global", lambda *_a, **_k: True)
    (tmp_path / "config.toml").write_text(
        "[dsar_fulfillment]\nenable = true\n\n[privacy_ops]\nenable = true\n",
        encoding="utf-8",
    )
    config.reset_config_cache()
    yield tmp_path
    config.reset_config_cache()


def _request(subject="alice@example.com"):
    from maverick import privacy_ops

    return privacy_ops.open_dsar(subject, "access", opened_by="ops")


def test_off_by_default(tmp_path):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    assert df.enabled() is False
    with pytest.raises(df.DsarFulfillmentConfigError, match="enable"):
        df.open_fulfillment("DSAR-1", actor="ops")


def test_open_requires_a_real_request_and_is_idempotent():
    with pytest.raises(KeyError, match="no such DSAR"):
        df.open_fulfillment("DSAR-nope", actor="ops")
    request = _request()
    first = df.open_fulfillment(request["id"], actor="ops")
    again = df.open_fulfillment(request["id"], actor="ops")
    assert first["id"] == again["id"]
    assert first["identity"]["status"] == "unverified"


def test_identity_gates_the_response_and_vocabulary_is_fixed():
    request = _request()
    record = df.open_fulfillment(request["id"], actor="ops")
    with pytest.raises(df.DsarFulfillmentError, match="identity"):
        df.prepare_response(
            record["id"], actor="ops", expected_revision=record["revision"])
    with pytest.raises(ValueError, match="method"):
        df.record_identity_verification(
            record["id"], method="trust_me", reference="x",
            actor="ops", expected_revision=record["revision"])
    verified = df.record_identity_verification(
        record["id"], method="document_check", reference="passport scan ref",
        actor="carol", expected_revision=record["revision"])
    assert verified["identity"]["verified_by"] == "carol"


def test_token_lifecycle_and_delivery_time_redaction():
    request = _request()
    record = df.open_fulfillment(request["id"], actor="ops")
    record = df.record_identity_verification(
        record["id"], method="email_challenge", reference="challenge-1",
        actor="ops", expected_revision=record["revision"])
    link = df.prepare_response(
        record["id"], actor="ops", expected_revision=record["revision"])
    token = link["response_path"].rsplit("/", 1)[-1]
    # The stored record carries only the digest, and reads hide even that.
    status = df.get_fulfillment(request["id"])
    assert "token_sha256" not in (status["response"] or {})
    response = df.response_for_token(token)
    assert response is not None
    assert response["request_id"] == request["id"]
    # The subject's own reference survives the third-party scrub.
    assert response["subject_id"] == "alice@example.com"
    assert df.response_for_token("not-the-token") is None
    # Delivery is stamped on first read.
    assert df.get_fulfillment(request["id"])["response"]["delivered_at"]


def test_expired_tokens_read_as_none(monkeypatch):
    request = _request()
    record = df.open_fulfillment(request["id"], actor="ops")
    record = df.record_identity_verification(
        record["id"], method="account_login", reference="session-9",
        actor="ops", expected_revision=record["revision"])
    link = df.prepare_response(
        record["id"], actor="ops", expected_revision=record["revision"],
        expires_hours=1)
    token = link["response_path"].rsplit("/", 1)[-1]
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 2 * 3600)
    assert df.response_for_token(token) is None


def test_bundle_redaction_scrubs_third_parties_keeps_subject():
    scrubbed = df._redact_bundle(
        {"subject": "alice@example.com",
         "note": "escalated by bob@example.com from 10.0.0.7"},
        "alice@example.com",
    )
    assert scrubbed["subject"] == "alice@example.com"
    assert "bob@example.com" not in scrubbed["note"]
    assert "10.0.0.7" not in scrubbed["note"]


def test_collection_sources_are_bounded_and_logged():
    request = _request()
    record = df.open_fulfillment(request["id"], actor="ops")
    saved = df.record_collection_source(
        record["id"], source="vendor:acme", reference="ticket-1", items=2,
        actor="ops", expected_revision=record["revision"])
    assert saved["sources"][0]["items"] == 2
    with pytest.raises(ValueError, match="non-negative"):
        df.record_collection_source(
            record["id"], source="vendor:acme", reference="t", items=-1,
            actor="ops", expected_revision=saved["revision"])


def test_cli_method_choices_match_the_module_vocabulary():
    from maverick.cli import _compliance_groups as cli

    choices = None
    for param in cli.dsar_fulfill_verify.params:
        if param.name == "method":
            choices = tuple(param.type.choices)
    assert choices == df.IDENTITY_METHODS
