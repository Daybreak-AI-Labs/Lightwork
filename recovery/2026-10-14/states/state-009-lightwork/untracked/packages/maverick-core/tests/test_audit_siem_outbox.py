"""Durable/confidential SIEM forwarding adverse-path coverage."""
from __future__ import annotations

import sqlite3

import pytest
from click.testing import CliRunner
from maverick.audit import forwarder, siem_outbox
from maverick.crypto_at_rest import is_sealed_str


def _outbox(tmp_path) -> siem_outbox.SIEMOutbox:
    return siem_outbox.SIEMOutbox(tmp_path / "siem-outbox.db")


def _mark_dead(outbox: siem_outbox.SIEMOutbox, delivery_id: str) -> None:
    with sqlite3.connect(outbox.db_path) as conn:
        conn.execute(
            "UPDATE siem_outbox SET status='dead', attempts=12, "
            "next_attempt_at=999, lease_until=NULL, "
            "last_error_class='TimeoutError' WHERE delivery_id=?",
            (delivery_id,),
        )


def test_outbox_persists_only_authenticated_ciphertext(tmp_path):
    outbox = _outbox(tmp_path)

    ids, count = outbox.enqueue(
        ['{"secret":"customer-record"}'],
        "https://siem.example/raw",
        now=100.0,
    )

    assert len(ids) == 1
    assert count == 1
    with sqlite3.connect(outbox.db_path) as conn:
        payload = conn.execute(
            "SELECT sealed_payload FROM siem_outbox"
        ).fetchone()[0]
    assert is_sealed_str(payload)
    assert "customer-record" not in payload


def test_failed_delivery_stays_durable_with_backoff(monkeypatch, tmp_path):
    outbox = _outbox(tmp_path)
    outbox.enqueue(["event-1"], "https://siem.example/raw", now=100.0)
    monkeypatch.setattr(
        forwarder,
        "_forward",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("down")),
    )

    with pytest.raises(ConnectionError, match="down"):
        outbox.drain(now=100.0, raise_on_failure=True)

    snapshot = outbox.snapshot(now=105.0)
    assert snapshot.pending == 1
    assert snapshot.dead == 0
    assert snapshot.oldest_age_seconds == 5.0
    with sqlite3.connect(outbox.db_path) as conn:
        row = conn.execute(
            "SELECT attempts, next_attempt_at, last_error_class "
            "FROM siem_outbox"
        ).fetchone()
    assert row == (1, 105.0, "ConnectionError")


def test_max_attempts_moves_batch_to_visible_dead_letter(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_SIEM_OUTBOX_MAX_ATTEMPTS", "1")
    outbox = _outbox(tmp_path)
    outbox.enqueue(["event-1"], "https://siem.example/raw", now=100.0)
    monkeypatch.setattr(
        forwarder,
        "_forward",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("down")),
    )

    assert outbox.drain(now=100.0, raise_on_failure=False) == 0

    snapshot = outbox.snapshot(now=101.0)
    assert snapshot.backlog == 1
    assert snapshot.dead == 1


def test_capacity_limit_fails_closed_without_dropping_existing(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MAVERICK_SIEM_OUTBOX_MAX_RECORDS", "1")
    outbox = _outbox(tmp_path)
    outbox.enqueue(["first"], "https://siem.example/raw")

    with pytest.raises(siem_outbox.SIEMOutboxFull, match="record limit"):
        outbox.enqueue(["second"], "https://siem.example/raw")

    assert outbox.snapshot().backlog == 1


def test_durable_forward_acks_deletes_and_sends_stable_idempotency_key(
    monkeypatch, tmp_path
):
    db = tmp_path / "siem-outbox.db"
    monkeypatch.setattr(siem_outbox, "_default_db", lambda: db)
    monkeypatch.setenv("MAVERICK_SIEM_TOKEN", "collector-token")
    monkeypatch.delenv("MAVERICK_SECRETS_BACKEND", raising=False)
    requests = []

    class _Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    handlers = []

    class _Opener:
        def open(self, request, **_kwargs):
            requests.append(request)
            return _Response()

    def _build_opener(*configured):
        handlers.extend(configured)
        return _Opener()

    monkeypatch.setattr("urllib.request.build_opener", _build_opener)

    assert forwarder.durable_forward(
        ["event-1", "event-2"], "https://siem.example/raw"
    ) == 2
    assert siem_outbox.SIEMOutbox(db).snapshot().backlog == 0
    delivery_id = requests[0].get_header("X-lightwork-delivery-id")
    assert len(delivery_id) == 64
    assert requests[0].get_header("Idempotency-key") == delivery_id
    redirect_handler = next(
        handler
        for handler in handlers
        if type(handler).__name__ == "_NoRedirect"
    )
    assert redirect_handler.redirect_request(None) is None

    # A caller retry after the acknowledged local delete resolves from the
    # durable receipt and does not submit the same batch a second time.
    assert forwarder.durable_forward(
        ["event-1", "event-2"], "https://siem.example/raw"
    ) == 2
    assert len(requests) == 1


def test_durable_forward_requires_https_and_secret_provider_auth(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        siem_outbox, "_default_db", lambda: tmp_path / "siem-outbox.db"
    )
    monkeypatch.delenv("MAVERICK_SIEM_TOKEN", raising=False)
    monkeypatch.delenv("MAVERICK_SECRETS_BACKEND", raising=False)

    with pytest.raises(RuntimeError, match="requires an https"):
        forwarder.durable_forward(["event"], "tcp://siem.example:6514")
    with pytest.raises(RuntimeError, match="requires MAVERICK_SIEM_TOKEN"):
        forwarder.durable_forward(["event"], "https://siem.example/raw")


def test_tampered_destination_identity_is_never_delivered(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MAVERICK_SIEM_OUTBOX_MAX_ATTEMPTS", "1")
    outbox = _outbox(tmp_path)
    outbox.enqueue(["event"], "https://siem.example/raw", now=100.0)
    with sqlite3.connect(outbox.db_path) as conn:
        conn.execute(
            "UPDATE siem_outbox SET destination='https://attacker.example/raw'"
        )
    monkeypatch.setattr(
        forwarder,
        "_forward",
        lambda *_args, **_kwargs: pytest.fail("tampered event reached transport"),
    )

    with pytest.raises(siem_outbox.SIEMOutboxError, match="identity was altered"):
        outbox.drain(now=100.0, raise_on_failure=True)

    assert outbox.snapshot(now=100.0).dead == 1


def test_expired_sending_lease_is_reclaimed_after_worker_crash(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MAVERICK_SIEM_OUTBOX_LEASE_SECONDS", "10")
    outbox = _outbox(tmp_path)
    outbox.enqueue(["event"], "https://siem.example/raw", now=100.0)
    claim = outbox._claim(now=100.0)
    assert claim is not None
    delivered = []
    monkeypatch.setattr(
        forwarder,
        "_forward",
        lambda lines, *_args, **_kwargs: delivered.extend(lines) or len(lines),
    )

    assert outbox.drain(now=111.0) == 1
    assert delivered == ["event"]
    assert outbox.snapshot(now=111.0).backlog == 0


def test_snapshot_hook_exports_counts_only(monkeypatch, tmp_path):
    db = tmp_path / "siem-outbox.db"
    monkeypatch.setattr(siem_outbox, "_default_db", lambda: db)
    siem_outbox.SIEMOutbox(db).enqueue(
        ["private-event"], "https://private-siem.example/raw", now=100.0
    )

    snapshot = siem_outbox.outbox_snapshot()

    assert snapshot["backlog"] == 1
    assert snapshot["pending"] == 1
    assert "private" not in repr(snapshot)


def test_aggregate_snapshot_counts_default_and_tenant_spools(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    default_db = tmp_path / "audit" / "siem-outbox.db"
    tenant_db = tmp_path / "tenants" / "acme" / "audit" / "siem-outbox.db"
    siem_outbox.SIEMOutbox(default_db).enqueue(
        ["default-event"], "https://siem.example/raw", now=90.0
    )
    siem_outbox.SIEMOutbox(tenant_db).enqueue(
        ["tenant-event"], "https://siem.example/raw", now=100.0
    )

    snapshot = siem_outbox.aggregate_outbox_snapshot(now=110.0)

    assert snapshot["backlog"] == 2
    assert snapshot["pending"] == 2
    assert snapshot["sending"] == 0
    assert snapshot["dead"] == 0
    assert snapshot["bytes"] > 0
    assert snapshot["oldest_age_seconds"] == 20.0
    assert "acme" not in repr(snapshot)


def test_dead_letter_redrive_preserves_identity_and_requires_audit(
    monkeypatch, tmp_path
):
    from maverick import audit

    outbox = _outbox(tmp_path)
    ids, _count = outbox.enqueue(
        ["private-event"], "https://siem.example/raw", now=100.0
    )
    delivery_id = ids[0]
    _mark_dead(outbox, delivery_id)
    with sqlite3.connect(outbox.db_path) as conn:
        before = conn.execute(
            "SELECT destination, sealed_payload FROM siem_outbox "
            "WHERE delivery_id=?",
            (delivery_id,),
        ).fetchone()
    recorded = []
    monkeypatch.setattr(
        audit,
        "audit_event",
        lambda kind, **payload: recorded.append((kind, payload)) or True,
    )

    assert outbox.redrive_dead(delivery_ids=[delivery_id], now=200.0) == (
        delivery_id,
    )

    with sqlite3.connect(outbox.db_path) as conn:
        after = conn.execute(
            "SELECT destination, sealed_payload, status, attempts, "
            "next_attempt_at, last_error_class FROM siem_outbox "
            "WHERE delivery_id=?",
            (delivery_id,),
        ).fetchone()
    assert after[:2] == before
    assert after[2:] == ("pending", 0, 200.0, "")
    assert recorded == [
        (
            "siem.dead_letter_redrive_authorized",
            {
                "agent": "operator-cli",
                "delivery_ids": [delivery_id],
                "delivery_count": 1,
            },
        )
    ]


def test_dead_letter_redrive_is_all_or_nothing_and_audit_failure_rolls_back(
    monkeypatch, tmp_path
):
    from maverick import audit

    outbox = _outbox(tmp_path)
    ids, _count = outbox.enqueue(["event"], "https://siem.example/raw")
    delivery_id = ids[0]
    _mark_dead(outbox, delivery_id)

    with pytest.raises(siem_outbox.SIEMOutboxError, match="every requested"):
        outbox.redrive_dead(delivery_ids=[delivery_id, "0" * 64])
    assert outbox.snapshot().dead == 1

    monkeypatch.setattr(audit, "audit_event", lambda *_args, **_kwargs: False)
    with pytest.raises(siem_outbox.SIEMOutboxError, match="durably audited"):
        outbox.redrive_dead(delivery_ids=[delivery_id])
    assert outbox.snapshot().dead == 1


def test_dead_letter_redrive_rejects_ambiguous_or_unbounded_selection(tmp_path):
    outbox = _outbox(tmp_path)

    with pytest.raises(ValueError, match="lowercase SHA-256"):
        outbox.dead_delivery_ids(delivery_ids=["not-an-id"])
    with pytest.raises(ValueError, match="between 1 and 100"):
        outbox.dead_delivery_ids(limit=101)
    with pytest.raises(ValueError, match="exceed"):
        outbox.dead_delivery_ids(delivery_ids=["0" * 64, "1" * 64], limit=1)


def test_siem_outbox_cli_requires_confirmation_and_supports_automation(
    monkeypatch, tmp_path
):
    from maverick import audit
    from maverick.cli import main

    db = tmp_path / "siem-outbox.db"
    monkeypatch.setattr(siem_outbox, "_default_db", lambda: db)
    outbox = siem_outbox.SIEMOutbox(db)
    ids, _count = outbox.enqueue(["event"], "https://siem.example/raw")
    _mark_dead(outbox, ids[0])
    runner = CliRunner()

    status = runner.invoke(main, ["audit", "siem-outbox", "status"])
    assert status.exit_code == 0, status.output
    assert '"dead":1' in status.output
    failing_status = runner.invoke(
        main, ["audit", "siem-outbox", "status", "--fail-on-backlog"]
    )
    assert failing_status.exit_code == 1

    unconfirmed = runner.invoke(
        main, ["audit", "siem-outbox", "redrive", "--all-dead"]
    )
    assert unconfirmed.exit_code == 2
    assert "requires --yes" in unconfirmed.output
    preview = runner.invoke(
        main,
        ["audit", "siem-outbox", "redrive", "--all-dead", "--dry-run"],
    )
    assert preview.exit_code == 0, preview.output
    assert "would redrive 1" in preview.output
    assert siem_outbox.SIEMOutbox(db).snapshot().dead == 1

    monkeypatch.setattr(audit, "audit_event", lambda *_args, **_kwargs: True)
    applied = runner.invoke(
        main,
        ["audit", "siem-outbox", "redrive", "--all-dead", "--yes"],
    )
    assert applied.exit_code == 0, applied.output
    assert "redrove 1" in applied.output
    assert siem_outbox.SIEMOutbox(db).snapshot().pending == 1


def test_siem_outbox_cli_drain_keeps_tenant_scope_through_ack(
    monkeypatch, tmp_path
):
    from maverick.cli import main
    from maverick.paths import current_tenant_id

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    seen = []

    def _drain(_self, **kwargs):
        seen.append((current_tenant_id(), kwargs))
        return 3

    monkeypatch.setattr(siem_outbox.SIEMOutbox, "drain", _drain)

    result = CliRunner().invoke(
        main,
        [
            "audit",
            "siem-outbox",
            "drain",
            "--tenant",
            "acme",
            "--limit",
            "7",
            "--timeout",
            "4.5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output == "acknowledged 3 SIEM audit event(s)\n"
    assert seen == [
        (
            "acme",
            {"limit": 7, "timeout": 4.5, "raise_on_failure": True},
        )
    ]


def test_cli_tenant_selects_source_and_durable_delivery_namespace(monkeypatch):
    from maverick import billing
    from maverick.audit import export
    from maverick.cli import main
    from maverick.paths import current_tenant_id

    monkeypatch.setattr(billing, "feature_allowed", lambda *_a, **_k: True)
    monkeypatch.setattr(export, "iter_audit_events", lambda **_kwargs: iter([{}]))
    monkeypatch.setattr(export, "to_jsonl", lambda _event: "event")
    seen = []
    monkeypatch.setattr(
        forwarder,
        "forward",
        lambda lines, _dest: seen.append(
            (current_tenant_id(), list(lines))
        ) or 1,
    )

    result = CliRunner().invoke(
        main,
        [
            "audit",
            "forward",
            "--tenant",
            "acme",
            "--to",
            "https://siem.example/raw",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == [("acme", ["event"])]
