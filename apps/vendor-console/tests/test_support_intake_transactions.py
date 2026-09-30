from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from vendor_console import audit, db, store, support
from vendor_console.util import hash_token

BUNDLE = {"correlation_id": "same-diagnostic-id", "customer_id": 999,
          "entitlement": {"customer": "Untrusted bundle name"}}


def test_two_authenticated_customers_never_share_ticket_or_comment(app):
    conn = app.state.conn
    ids = []
    with TestClient(app) as client:
        for index in range(2):
            token = f"synthetic-support-token-{index}"
            customer = store.create_customer(conn, name=f"Customer{index}",
                                             serve_token_hash=hash_token(token))
            response = client.post("/api/v1/support", json=BUNDLE,
                                   headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 201
            ticket_id = response.json()["ticket_id"]
            assert store.get_ticket(conn, ticket_id).customer_id == customer
            assert store.list_comments(conn, ticket_id) == []
            ids.append(ticket_id)
    assert ids[0] != ids[1]
    assert audit.verify_chain(conn) == (True, 0)


def test_same_customer_concurrent_resends_have_one_ticket_and_exact_notes(conn):
    customer = store.create_customer(conn, name="Acme")
    with ThreadPoolExecutor(max_workers=8) as executor:
        tickets = list(executor.map(lambda _: support.intake_bundle(
            conn, customer_id=customer, bundle=BUNDLE), range(24)))
    assert len({ticket.id for ticket in tickets}) == 1
    comments = store.list_comments(conn, tickets[0].id)
    assert len(comments) == 23
    assert len({comment.body for comment in comments}) == 1
    assert len(audit.recent(conn)) == 24
    assert audit.verify_chain(conn) == (True, 0)


@pytest.mark.parametrize("failure_stage", ["before_audit", "after_audit", "before_commit"])
@pytest.mark.parametrize("existing", [False, True])
def test_failure_does_not_leave_a_ticket_comment_or_unchained_audit(
    conn, monkeypatch, failure_stage, existing,
):
    customer = store.create_customer(conn, name="Acme")
    if existing:
        support.intake_bundle(conn, customer_id=customer, bundle=BUNDLE)
    old_tickets = store.list_tickets(conn)
    old_audit = audit.recent(conn)
    actual = audit.record_in_transaction

    def fail_audit(*args, **kwargs):
        if failure_stage == "after_audit":
            actual(*args, **kwargs)
        raise RuntimeError("injected transaction interruption")

    if failure_stage == "before_commit":
        def fail_commit(_self):
            raise RuntimeError("injected transaction interruption")
        monkeypatch.setattr(db._Connection, "commit", fail_commit)
    else:
        monkeypatch.setattr(audit, "record_in_transaction", fail_audit)
    with pytest.raises(RuntimeError, match="interruption"):
        support.intake_bundle(conn, customer_id=customer, bundle=BUNDLE)
    assert store.list_tickets(conn) == old_tickets
    assert audit.recent(conn) == old_audit
    assert all(store.list_comments(conn, ticket.id) == [] for ticket in old_tickets)
    assert audit.verify_chain(conn) == (True, 0)


def test_closed_ticket_is_historical_and_reopen_conflict_rolls_back(conn):
    customer = store.create_customer(conn, name="Acme")
    first = support.intake_bundle(conn, customer_id=customer, bundle=BUNDLE)
    support.set_status(conn, first.id, "resolved", actor="staff")
    second = support.intake_bundle(conn, customer_id=customer, bundle=BUNDLE)
    assert second.id != first.id
    before = audit.recent(conn)
    with pytest.raises(store.SupportIntakeConflict):
        support.set_status(conn, first.id, "open", actor="staff")
    assert store.get_ticket(conn, first.id).status == "resolved"
    assert audit.recent(conn) == before
    support.set_status(conn, second.id, "closed", actor="staff")
    support.set_status(conn, first.id, "open", actor="staff")
    assert support.intake_bundle(conn, customer_id=customer, bundle=BUNDLE).id == first.id
    assert audit.verify_chain(conn) == (True, 0)


def test_unassigned_staff_intake_is_explicit_and_separate(conn):
    with pytest.raises(ValueError, match="explicit staff"):
        support.intake_bundle(conn, customer_id=None, bundle=BUNDLE)
    unassigned = support.intake_bundle(
        conn, customer_id=None, bundle=BUNDLE, actor="staff", allow_unassigned=True)
    customer = store.create_customer(conn, name="Acme")
    assigned = support.intake_bundle(conn, customer_id=customer, bundle=BUNDLE)
    assert unassigned.customer_id is None
    assert assigned.customer_id == customer and assigned.id != unassigned.id


def legacy_duplicates(conn):
    conn.executescript(
        "DROP TRIGGER support_intake_insert; DROP TRIGGER support_intake_update; "
        "DROP TABLE support_intake_keys; UPDATE meta SET value='2' WHERE key='schema_version';")
    customer = store.create_customer(conn, name="Legacy customer")
    ids = [store.create_ticket(conn, customer_id=customer, correlation_id="legacy",
                               subject=f"Historical ticket {index}") for index in range(2)]
    return customer, ids


def test_legacy_migration_is_atomic_and_reports_preserved_duplicates(conn, monkeypatch):
    customer, ids = legacy_duplicates(conn)
    actual = db._migrate_support_intake

    def interrupted(tx):
        actual(tx)
        raise RuntimeError("migration interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(db, "_migrate_support_intake", interrupted)
        with pytest.raises(RuntimeError, match="migration interrupted"):
            db.init_db(conn)
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "2"
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='support_intake_keys'").fetchone() is None
    assert [ticket.id for ticket in store.list_tickets(conn)] == list(reversed(ids))
    db.init_db(conn)
    assert store.intake_conflicts(conn) == [{
        "customer_id": customer, "correlation_id": "legacy", "active_count": 2,
        "ticket_ids": list(reversed(ids)),
    }]
    selected = support.intake_bundle(conn, customer_id=customer, bundle={"correlation_id": "legacy"})
    assert selected.id == ids[-1]
    assert len(store.list_tickets(conn)) == 2
    support.set_status(conn, selected.id, "closed", actor="staff")
    resumed = support.intake_bundle(conn, customer_id=customer, bundle={"correlation_id": "legacy"})
    assert resumed.id == ids[0]  # Remaining canonical active legacy ticket is reconciled.


def test_legacy_conflicts_are_visible_in_staff_console(admin, app):
    customer, ids = legacy_duplicates(app.state.conn)
    db.init_db(app.state.conn)
    response = admin.get("/support")
    assert response.status_code == 200
    assert "Duplicate tickets to review" in response.text
    assert store.get_customer(app.state.conn, customer).name in response.text
    assert all(f"Ticket {ticket_id}" in response.text for ticket_id in ids)


def test_stale_cross_customer_mapping_cannot_authorize_an_append(conn):
    a = store.create_customer(conn, name="A")
    b = store.create_customer(conn, name="B")
    first = support.intake_bundle(conn, customer_id=a, bundle=BUNDLE)
    second = support.intake_bundle(conn, customer_id=b, bundle=BUNDLE)
    conn.execute("UPDATE support_intake_keys SET ticket_id=? WHERE customer_scope=?",
                 (first.id, f"customer:{b}"))
    conn.commit()
    with pytest.raises(store.SupportIntakeConflict):
        support.intake_bundle(conn, customer_id=b, bundle=BUNDLE)
    assert store.list_comments(conn, first.id) == []
    assert store.list_comments(conn, second.id) == []


def test_owned_transaction_supports_private_memory_db():
    conn = db.connect(":memory:")
    try:
        db.init_db(conn)
        customer = store.create_customer(conn, name="Acme")
        ticket = support.intake_bundle(conn, customer_id=customer, bundle=BUNDLE)
        assert store.get_ticket(conn, ticket.id).customer_id == customer
        assert audit.verify_chain(conn) == (True, 0)
    finally:
        conn.close()


def test_multiple_processes_share_one_intake_identity_and_audit_chain(conn):
    customer = store.create_customer(conn, name="Acme")
    app_path = str(Path(__file__).resolve().parents[1])
    script = (
        "import sys; sys.path.insert(0,sys.argv[1]); "
        "from vendor_console import db,support; c=db.connect(sys.argv[2]); "
        "[support.intake_bundle(c,customer_id=int(sys.argv[3]),bundle={'correlation_id':'processes'}) "
        "for _ in range(8)]; c.close()")
    workers = [subprocess.Popen(
        [sys.executable, "-I", "-c", script, app_path, conn.database_target, str(customer)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
    try:
        for worker in workers:
            _out, err = worker.communicate(timeout=30)
            assert worker.returncode == 0, err
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.communicate(timeout=5)
    tickets = store.list_tickets(conn)
    assert len(tickets) == 1
    assert len(store.list_comments(conn, tickets[0].id)) == 23
    assert len(audit.recent(conn)) == 24
    assert audit.verify_chain(conn) == (True, 0)


def test_shared_handler_commit_cannot_commit_an_intake_transaction(conn, monkeypatch):
    customer = store.create_customer(conn, name="Acme")

    def interrupt(tx, **_kwargs):
        assert tx is not conn and tx.in_transaction
        conn.commit()  # An unrelated shared-connection handler cannot publish our row.
        assert store.list_tickets(conn) == []
        raise RuntimeError("interrupted before audit")

    monkeypatch.setattr(audit, "record_in_transaction", interrupt)
    with pytest.raises(RuntimeError, match="before audit"):
        support.intake_bundle(conn, customer_id=customer, bundle=BUNDLE)
    assert store.list_tickets(conn) == []
    assert audit.recent(conn) == []


def test_worker_process_death_before_audit_does_not_publish_partial_intake(conn):
    customer = store.create_customer(conn, name="Acme")
    app_path = str(Path(__file__).resolve().parents[1])
    script = (
        "import os,sys; sys.path.insert(0,sys.argv[1]); "
        "from vendor_console import db,support,audit; c=db.connect(sys.argv[2]); "
        "audit.record_in_transaction=lambda *args,**kwargs:os._exit(17); "
        "support.intake_bundle(c,customer_id=int(sys.argv[3]),bundle={'correlation_id':'crash'})")
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, app_path, conn.database_target, str(customer)],
        capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 17, result.stderr
    assert store.list_tickets(conn) == [] and audit.recent(conn) == []
    ticket = support.intake_bundle(conn, customer_id=customer, bundle={"correlation_id": "crash"})
    assert ticket.customer_id == customer
    assert audit.verify_chain(conn) == (True, 0)
