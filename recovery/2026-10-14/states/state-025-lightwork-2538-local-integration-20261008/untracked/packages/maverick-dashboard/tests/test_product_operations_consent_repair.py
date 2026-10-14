"""Copilot consent repair is reviewed, attributable and safe after a lost reply."""
from contextlib import nullcontext

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from maverick import config
from maverick import consent_registry as cr
from maverick.audit import writer as audit_writer
from maverick_dashboard import api
from maverick_dashboard import product_operations as operations
from maverick_dashboard import product_operations_consent_repair as repair

BASE = "/api/v1/copilot/product-operations"


def test_repair_is_registered_on_the_privacy_page():
    action = operations.adapters()["artifacts.consent_summary_repair"]
    assert action.pages == ("/privacy",)
    assert action.execute is not None and action.recover is not None


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    (tmp_path / "config.toml").write_text(
        '[consent_registry]\nenable = true\nsubject_salt = "test-consent-repair-salt-0123456789"\n',
        encoding="utf-8",
    )
    config.reset_config_cache()
    monkeypatch.setattr(audit_writer, "_default", None)
    monkeypatch.setattr(audit_writer, "_defaults", {})
    monkeypatch.setattr(audit_writer, "record", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(audit_writer, "record_global", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(operations, "_same_origin", lambda _request: None)
    monkeypatch.setattr(operations, "_read_gate", lambda _request: None)
    monkeypatch.setattr(operations, "_gate", lambda _request: None)
    monkeypatch.setattr(operations, "_context", lambda _request: "a" * 64)
    monkeypatch.setattr(operations, "enabled_families", lambda: {"artifacts"})
    codec = Fernet(Fernet.generate_key())
    monkeypatch.setattr(operations.crypto_at_rest, "seal_to_str",
                        lambda value: "sealed:" + codec.encrypt(value.encode()).decode())
    monkeypatch.setattr(operations.crypto_at_rest, "is_sealed_str",
                        lambda value: value.startswith("sealed:"))
    monkeypatch.setattr(operations.crypto_at_rest, "unseal_from_str",
                        lambda value: codec.decrypt(value[7:].encode()).decode())
    monkeypatch.setattr(operations, "adapters", lambda: {entry.id: entry for entry in repair.entries()})
    monkeypatch.setattr(repair, "context_for", lambda _request: "consent-workspace")
    monkeypatch.setattr(repair.auth, "require_permission", lambda *_args: None)
    monkeypatch.setattr(api, "_request_actor", lambda _request: "user:privacy-admin")
    from maverick import authority_effects
    monkeypatch.setattr(authority_effects, "authority_effect_fence", nullcontext)
    app = FastAPI()
    app.include_router(operations.router)
    return TestClient(app)


def test_repair_is_reviewed_and_exact_retry_reads_its_saved_receipt(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    original = cr.record_consent(
        "alice@example.com", purpose="marketing", status="withdrawn",
        policy_version="2026-02", channel="web", actor="importer",
    )
    bad = {**original["history"][-1], "status": "granted",
           "recorded_at": original["history"][-1]["recorded_at"] + 86400 * 365}

    def legacy(row):
        row["history"].append(bad)
        row["current_status"] = "granted"
        row["last_recorded_at"] = bad["recorded_at"]

    cr._RECEIPTS.update(
        original["id"], legacy, expected_revision=original["revision"],
        action="legacy_fixture", actor="test",
    )
    with client:
        catalog = client.post(BASE + "/catalog", json={"page": "/privacy", "selection": {}})
        assert catalog.status_code == 200, catalog.text
        action = next(item for item in catalog.json()["operations"]
                      if item["id"] == "artifacts.consent_summary_repair")
        assert action["available"] is True
        assert "alice@example.com" not in catalog.text

        body = {"page": "/privacy", "selection": {}, "operation": action["id"],
                "context": catalog.json()["context"], "revision": action["revision"],
                "arguments": {"subject_ref": "alice@example.com", "purpose": "marketing"}}
        prepared = client.post(BASE + "/prepare", json=body)
        assert prepared.status_code == 200, prepared.text
        assert "alice@example.com" not in prepared.text
        token = prepared.json()["token"]
        refused = client.post(BASE + "/execute", json={"token": token, "confirmed": False})
        assert refused.status_code == 400
        assert cr.subject_preferences("alice@example.com")["purposes"]["marketing"]["needs_repair"] is True

        done = client.post(BASE + "/execute", json={"token": token, "confirmed": True})
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "confirmed"
        assert done.json()["details"]["status"] == "withdrawn"
        assert done.json()["details"]["history_preserved"] is True
        saved = cr._RECEIPTS.get(original["id"])
        assert len(saved["history"]) == 2
        assert saved["current_status"] == "withdrawn"
        revision = saved["revision"]

        replay = client.post(BASE + "/execute", json={"token": token, "confirmed": True})
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "replayed"
        assert cr._RECEIPTS.get(original["id"])["revision"] == revision
