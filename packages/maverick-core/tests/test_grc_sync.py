"""Outbound GRC sync: idempotent delivery, round-trip links, honest failure.

The invariants that matter: a re-run with unchanged sources sends nothing; a
changed source becomes an update to the SAME remote record, never a duplicate
create; an unparseable-but-successful create is terminal rather than blindly
retried; and only bounded pointer content ever leaves the platform.
"""
from __future__ import annotations

import json

import pytest
from maverick import config, grc_sync
from maverick.paths import tenant_scope

_CONFIG = """[evidence_graph]
enable = true

[model_risk_assurance]
enable = true
gate_promotions = false

[grc_sync]
enable = true
connector = "servicenow"
findings_path = "/api/now/table/sn_grc_issue"
acceptances_path = "/api/now/table/sn_risk_acknowledgement"
incidents_path = "/api/now/table/incident"
backlink_base = "https://lightwork.example.com"
"""


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    from maverick import audit

    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(audit, "record_global", lambda *_args, **_kwargs: True)
    (tmp_path / "config.toml").write_text(_CONFIG, encoding="utf-8")
    config.reset_config_cache()
    yield tmp_path
    config.reset_config_cache()


def _sources(monkeypatch, *, findings=(), incidents=(), accepted_ids=()):
    from maverick import model_risk_assurance as mra

    monkeypatch.setattr(mra, "findings", lambda **_kwargs: list(findings))
    monkeypatch.setattr(mra, "list_incidents", lambda **_kwargs: list(incidents))
    monkeypatch.setattr(
        mra,
        "current_risk_acceptance",
        lambda finding, **_kwargs: (
            {"id": "MRX-1"} if finding.get("id") in accepted_ids else None
        ),
    )


def _finding(identifier="MRF-open", summary="Asset has no accountable owner."):
    return {
        "id": identifier,
        "asset_id": "MAA-" + "a" * 32,
        "severity": "high",
        "summary": summary,
        "finding_sha256": "f" * 64,
        "details": {"internal": "never leaves"},
        "citations": [{"record_id": "MRE-1", "relation": "supports"}],
    }


def _incident():
    return {
        "id": "MRI-one",
        "asset_id": "MAA-" + "b" * 32,
        "severity": "critical",
        "summary": "The triage model misrouted an emergency case.",
        "payload_sha256": "0" * 64,
    }


class _Writer:
    def __init__(self, responses=None):
        self.calls: list[tuple[str, str, dict]] = []
        self.responses = list(responses or [])

    def __call__(self, op, path, body):
        self.calls.append((op, path, dict(body)))
        if self.responses:
            return self.responses.pop(0)
        return json.dumps({"result": {"sys_id": f"sys{len(self.calls):04d}"}})


def test_config_gate_is_default_off_and_hostile_values_are_refused(tmp_path):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    assert grc_sync.enabled() is False
    with pytest.raises(grc_sync.GRCSyncConfigError, match="enable"):
        grc_sync.run_sync(actor="ops")

    (tmp_path / "config.toml").write_text(
        '[grc_sync]\nenable = true\nfindings_path = "/x"\nextra = 1\n',
        encoding="utf-8",
    )
    config.reset_config_cache()
    with pytest.raises(grc_sync.GRCSyncConfigError, match="unknown"):
        grc_sync.get_grc_sync_config()

    for hostile in ("no-slash", "/has?query", "/dot/../dot", "/frag#ment"):
        (tmp_path / "config.toml").write_text(
            f'[grc_sync]\nenable = true\nfindings_path = "{hostile}"\n',
            encoding="utf-8",
        )
        config.reset_config_cache()
        with pytest.raises(grc_sync.GRCSyncConfigError, match="path"):
            grc_sync.get_grc_sync_config()

    (tmp_path / "config.toml").write_text(
        '[grc_sync]\nenable = true\nfindings_path = "/x"\n'
        'backlink_base = "http://plain.example.com"\n',
        encoding="utf-8",
    )
    config.reset_config_cache()
    with pytest.raises(grc_sync.GRCSyncConfigError, match="HTTPS"):
        grc_sync.get_grc_sync_config()


def test_delivery_is_idempotent_and_carries_the_round_trip_link(monkeypatch):
    open_finding = _finding("MRF-open")
    accepted = _finding("MRF-accepted", "Residual risk accepted by the CRO.")
    _sources(
        monkeypatch,
        findings=[open_finding, accepted],
        incidents=[_incident()],
        accepted_ids={"MRF-accepted"},
    )
    writer = _Writer()

    result = grc_sync.run_sync(actor="ops", writer=writer)

    assert result["delivered_count"] == 3
    assert result["failure_count"] == 0
    paths = sorted(path for _op, path, _body in writer.calls)
    assert paths == [
        "/api/now/table/incident",
        "/api/now/table/sn_grc_issue",
        "/api/now/table/sn_risk_acknowledgement",
    ]
    body = next(b for _o, p, b in writer.calls if p.endswith("sn_grc_issue"))
    # Round-trip link and bounded pointer content only.
    assert body["correlation_id"] == "MRF-open"
    assert body["correlation_display"] == "Lightwork"
    assert body["u_lightwork_url"] == "https://lightwork.example.com/security/assurance"
    assert "f" * 64 in body["description"]
    assert "never leaves" not in json.dumps(body)
    assert "MRE-1" not in json.dumps(body)
    assert len(body["short_description"]) <= 160

    rows = grc_sync.list_outbound()
    assert {row["status"] for row in rows} == {"delivered"}
    assert all(row["remote_id"].startswith("sys") for row in rows)

    # Unchanged sources: nothing is sent again.
    again = grc_sync.run_sync(actor="ops", writer=writer)
    assert again["delivered_count"] == 0
    assert again["unchanged_count"] == 3
    assert len(writer.calls) == 3


def test_changed_source_updates_the_same_remote_record(monkeypatch):
    finding = _finding("MRF-open")
    _sources(monkeypatch, findings=[finding])
    writer = _Writer()
    first = grc_sync.run_sync(actor="ops", writer=writer)
    remote_id = first["delivered"][0]["remote_id"]

    changed = dict(finding, summary="Now remediated twice.", finding_sha256="e" * 64)
    _sources(monkeypatch, findings=[changed])
    second = grc_sync.run_sync(actor="ops", writer=writer)

    assert second["delivered_count"] == 1
    op, path, body = writer.calls[-1]
    assert op == "patch"
    assert path == f"/api/now/table/sn_grc_issue/{remote_id}"
    assert "Now remediated twice." in body["description"]
    row = grc_sync.list_outbound()[0]
    assert row["status"] == "delivered"
    assert row["remote_id"] == remote_id
    assert row["attempts"] == 2


def test_failed_delivery_records_a_bounded_error_and_retries(monkeypatch):
    _sources(monkeypatch, findings=[_finding()])
    writer = _Writer(responses=["ERROR: post (503): upstream request failed" + "x" * 900])

    first = grc_sync.run_sync(actor="ops", writer=writer)
    assert first["failure_count"] == 1
    row = grc_sync.list_outbound()[0]
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    assert len(row["last_error"]) <= 500

    second = grc_sync.run_sync(actor="ops", writer=writer)
    assert second["delivered_count"] == 1
    assert grc_sync.list_outbound()[0]["status"] == "delivered"


def test_unparseable_create_is_terminal_never_a_duplicate(monkeypatch):
    finding = _finding()
    _sources(monkeypatch, findings=[finding])
    writer = _Writer(responses=["created but not json"])

    first = grc_sync.run_sync(actor="ops", writer=writer)
    assert first["delivered_count"] == 1
    row = grc_sync.list_outbound()[0]
    assert row["status"] == "delivered_unconfirmed"
    assert row["remote_id"] is None

    # Even a changed source never re-creates: the remote id is unknown, and a
    # second POST would duplicate the record.
    changed = dict(finding, finding_sha256="d" * 64)
    _sources(monkeypatch, findings=[changed])
    second = grc_sync.run_sync(actor="ops", writer=writer)
    assert second["delivered_count"] == 0
    assert second["blocked_count"] == 1
    assert "resolve the outbound record manually" in second["blocked"][0]["reason"]
    assert len(writer.calls) == 1

    # A hostile remote id is treated as unparseable, not trusted.
    assert grc_sync._remote_id_from(  # noqa: SLF001
        json.dumps({"result": {"sys_id": "x" * 300}})
    ) is None
    assert grc_sync._remote_id_from(  # noqa: SLF001
        json.dumps({"result": {"sys_id": "id with spaces"}})
    ) is None


def test_outbound_ledger_is_tenant_scoped(monkeypatch):
    _sources(monkeypatch, findings=[_finding()])
    with tenant_scope(tenant="alpha"):
        grc_sync.run_sync(actor="ops", writer=_Writer())
        assert len(grc_sync.list_outbound()) == 1
    with tenant_scope(tenant="beta"):
        assert grc_sync.list_outbound() == []


def test_run_bounds_and_kind_gating(monkeypatch, tmp_path):
    _sources(monkeypatch, findings=[_finding()], incidents=[_incident()])
    with pytest.raises(ValueError, match="limit"):
        grc_sync.run_sync(actor="ops", writer=_Writer(), limit=0)

    # Disabling a kind's path keeps that kind local.
    (tmp_path / "config.toml").write_text(
        _CONFIG.replace('incidents_path = "/api/now/table/incident"\n', ""),
        encoding="utf-8",
    )
    config.reset_config_cache()
    writer = _Writer()
    result = grc_sync.run_sync(actor="ops", writer=writer)
    assert result["delivered_count"] == 1
    assert all("incident" not in path for _o, path, _b in writer.calls)
