"""Vendor and DSAR detail pages: the drill-through from the estate lists.

What matters: the pages render the governed record without leaking
credentials (questionnaire form tokens, response-token hashes), 404 on
unknown ids, and 403 when the backing module is disabled.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})

_CONFIG = (
    "[vendor_management]\nenable = true\n\n"
    "[privacy_ops]\nenable = true\n\n"
    "[dsar_fulfillment]\nenable = true\n"
)


@pytest.fixture(autouse=True)
def _fresh_world(tmp_path, monkeypatch):
    from maverick import config, world_model
    from maverick.audit import writer as audit_writer

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    for name in (
        "MAVERICK_ENTERPRISE", "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_DASHBOARD_REQUIRE_AUTH", "MAVERICK_OIDC_ENABLED",
        "MAVERICK_PROXY_AUTH",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "config.toml").write_text(_CONFIG, encoding="utf-8")
    config.reset_config_cache()
    audit_writer._default = None
    audit_writer._defaults.clear()
    import maverick_dashboard.api as api
    api._world_cache.clear()
    yield
    config.reset_config_cache()
    audit_writer._default = None
    audit_writer._defaults.clear()
    api._world_cache.clear()


def _vendor():
    from maverick import vendor_management as vm

    record = vm.register_vendor(
        "Acme Analytics", category="analytics", criticality="high",
        owner="dpo@corp", actor="ops")
    record = vm.record_vendor_contract(
        record["id"], kind="dpa", reference="contracts/acme-dpa.pdf",
        renews_at=4102444800.0, actor="ops",
        expected_revision=record["revision"])
    record = vm.request_questionnaire(
        record["id"], assessment_type="vendor_risk", actor="ops",
        expected_revision=record["revision"])
    return record


def test_vendor_detail_renders_record_without_form_tokens():
    record = _vendor()
    page = client.get(f"/privacy/vendors/{record['id']}")
    assert page.status_code == 200
    body = page.text
    assert "Acme Analytics" in body
    assert "contracts/acme-dpa.pdf" in body
    assert "vendor_risk" in body
    # The hosted-form token addresses a public endpoint; it never renders.
    token = record["questionnaires"][0]["token"]
    assert token not in body


def test_vendor_detail_404_and_403(tmp_path):
    from maverick import config

    assert client.get("/privacy/vendors/vend-nope").status_code == 404
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    assert client.get("/privacy/vendors/vend-nope").status_code == 403


def test_dsar_detail_shows_fulfillment_trail_without_tokens():
    from maverick import dsar_fulfillment as df
    from maverick import privacy_ops

    request = privacy_ops.open_dsar("alice@example.com", "access",
                                    opened_by="ops")
    record = df.open_fulfillment(request["id"], actor="ops")
    record = df.record_identity_verification(
        record["id"], method="document_check", reference="passport ref",
        actor="carol", expected_revision=record["revision"])
    link = df.prepare_response(
        record["id"], actor="ops", expected_revision=record["revision"],
        max_reads=3)
    token = link["response_path"].rsplit("/", 1)[-1]

    page = client.get(f"/privacy/dsar/{request['id']}")
    assert page.status_code == 200
    body = page.text
    assert "alice@example.com" in body
    assert "document_check" in body and "carol" in body
    assert "0/3" in body                      # reads used against the cap
    assert token not in body                  # the link token never renders


def test_dsar_detail_404_and_disabled_fulfillment_note(tmp_path):
    from maverick import config, privacy_ops

    assert client.get("/privacy/dsar/DSAR-nope").status_code == 404
    request = privacy_ops.open_dsar("bob@example.com", "erasure",
                                    opened_by="ops")
    (tmp_path / "config.toml").write_text(
        "[privacy_ops]\nenable = true\n", encoding="utf-8")
    config.reset_config_cache()
    page = client.get(f"/privacy/dsar/{request['id']}")
    assert page.status_code == 200
    assert "not enabled" in page.text
