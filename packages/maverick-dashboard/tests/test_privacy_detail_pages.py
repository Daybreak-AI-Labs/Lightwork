"""Vendor and DSAR detail pages: the drill-through from the estate lists.

What matters: the pages render the governed record without leaking
credentials (questionnaire form tokens, response-token hashes), 404 on
unknown ids, and 403 when the backing module is disabled.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape
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
    assert "Data processing agreement" in body
    # The hosted-form token addresses a public endpoint; it never renders.
    token = record["questionnaires"][0]["token"]
    assert token not in body
    # Every section has its button; no raw IDs or lowercase keys to read.
    for label in ("Add contract", "Send a questionnaire", "Link a record", "Add a note",
                  "Mark as under review", "Stop using this vendor"):
        assert label in body
    assert f'data-revision="{record["revision"]}"' in body
    assert "window.confirm" not in body
    from test_support.plain_language import developer_terms

    assert developer_terms(body) == []


def test_vendor_detail_shows_checked_reply_state_and_read_failure(monkeypatch):
    from maverick import form_store

    record = _vendor()
    token = record["questionnaires"][0]["token"]
    form_store.append(token, {"vr_q1": "yes"})
    answered = client.get(f"/privacy/vendors/{record['id']}")
    assert answered.status_code == 200
    assert "Answered</span>" in answered.text
    assert token not in answered.text

    def fail_read(_token, _cursor):
        raise OSError("private storage detail")

    monkeypatch.setattr(form_store, "since", fail_read)
    unavailable = client.get(f"/privacy/vendors/{record['id']}")
    assert unavailable.status_code == 200
    assert "can't check questionnaire replies right now" in unavailable.text
    assert "Replies unavailable</span>" in unavailable.text
    assert "Answered</span>" not in unavailable.text
    assert token not in unavailable.text
    assert "private storage detail" not in unavailable.text


def test_vendor_detail_404_and_403(tmp_path):
    from maverick import config

    assert client.get("/privacy/vendors/vend-nope").status_code == 404
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    assert client.get("/privacy/vendors/vend-nope").status_code == 403


@pytest.mark.parametrize("field", ["name", "category", "registered_by"])
@pytest.mark.parametrize("value", [
    '<xprobe title="quotes">Café & 東京</xprobe>',
    '</h1><svg data-probe="alternate">Résumé</svg>',
])
def test_vendor_detail_escapes_stored_hero_fields(field, value, monkeypatch):
    from maverick_dashboard import api
    from maverick_dashboard.identity_format import humanize, humanize_principal

    payload = {
        "name": "Synthetic vendor", "category": "analytics",
        "criticality": "high", "owner": "procurement",
    }
    if field == "registered_by":
        monkeypatch.setattr(api, "_request_actor", lambda request: value)
    else:
        payload[field] = value
    created = client.post("/api/v1/vendors", json=payload)
    assert created.status_code == 201, created.text
    record = created.json()
    assert record[field] == value

    page = client.get(f"/privacy/vendors/{record['id']}")
    assert page.status_code == 200
    displayed = humanize(value) if field == "category" else (
        str(humanize_principal(value)) if field == "registered_by" else value
    )
    assert str(escape(displayed)) in page.text
    assert "<xprobe" not in page.text.lower()
    assert '<svg data-probe="alternate">' not in page.text.lower()


def test_vendor_detail_preserves_unicode_and_literal_punctuation():
    value = 'Café & 東京 <Research> "Partners"'
    created = client.post("/api/v1/vendors", json={
        "name": value, "category": "analytics", "criticality": "low", "owner": "ops",
    })
    assert created.status_code == 201, created.text
    page = client.get(f"/privacy/vendors/{created.json()['id']}")
    assert page.status_code == 200
    assert f'id="page-hero-title">{escape(value)}</h1>' in page.text


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
    assert "Checked an ID document" in body and "carol" in body
    assert "document_check" not in body
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
    assert 'data-module-gate="dsar_fulfillment"' in page.text
    assert "[dsar_fulfillment]" not in page.text


def test_dsar_detail_offers_a_button_for_every_step():
    from maverick import privacy_ops

    request = privacy_ops.open_dsar("dana@example.com", "erasure", opened_by="ops")
    body = client.get(f"/privacy/dsar/{request['id']}").text
    assert 'id="dsar-start"' in body            # no fulfilment yet -> start button
    assert 'id="dsar-worksheet"' in body and 'id="dsar-close"' in body
    assert 'id="dsar-erase"' in body            # erasure request + admin
    assert "Delete their data: request from dana@example.com" in body
    for word in ("minted", "token", "subject ", "operator", "SHA", "awaiting_erasure"):
        assert word not in body.split('<main', 1)[-1].split("</main>", 1)[0], word

    from maverick import dsar_fulfillment as df

    record = df.open_fulfillment(request["id"], actor="ops")
    body = client.get(f"/privacy/dsar/{request['id']}").text
    assert 'id="dsar-identity"' in body and 'id="dsar-source"' in body
    assert f'data-revision="{record["revision"]}"' in body
    assert 'id="dsar-link"' not in body         # identity not confirmed yet


def test_microsoft365_collection_needs_operate_and_plain_errors(monkeypatch):
    from maverick import dsar_fulfillment as df
    from maverick import privacy_ops

    request = privacy_ops.open_dsar("erin@example.com", "access", opened_by="ops")
    record = df.open_fulfillment(request["id"], actor="ops")
    url = f"/api/v1/privacy/dsar/{request['id']}/collect-microsoft365"
    monkeypatch.delenv("MAVERICK_GRAPH_TOKEN", raising=False)
    r = client.post(url, json={"revision": record["revision"]})
    assert r.status_code == 503 and r.json()["detail"] == "Microsoft 365 is not connected."

    def fake(req, *, actor, expected_revision):
        return {"probes": [{"source": "graph:outlook_mail", "collected": True, "items": 3, "more": False}]}

    monkeypatch.setattr(df, "collect_graph_sources", fake)
    r = client.post(url, json={"revision": record["revision"]})
    assert r.status_code == 200 and "Outlook mailbox: 3 item(s)" in r.json()["message"]

    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    from maverick_dashboard import auth, rbac

    rbac.set_role("user:v", "viewer")
    monkeypatch.setattr(auth, "caller_principal", lambda request: "user:v")
    assert client.post(url, json={"revision": record["revision"]}).status_code == 403
