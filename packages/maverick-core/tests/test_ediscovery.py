from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile

import pytest
from click.testing import CliRunner
from maverick import audit, ediscovery, ediscovery_export, records_gc
from maverick.audit import preservation, retention
from maverick.paths import tenant_scope
from maverick.world_model import WorldModel

ALICE = "user:cli:alice"
BOB = "user:cli:bob"
PAST = {"date_from": "2001-01-01", "date_to": "2001-01-02"}


@pytest.fixture()
def events(monkeypatch):
    recorded: list[tuple[str, dict]] = []

    def record(kind, *, agent="system", goal_id=None, **payload):
        recorded.append((payload.get("verb", kind), dict(payload)))
        return True

    monkeypatch.setattr(audit, "audit_event", lambda kind, **payload: record(kind, **payload))
    return recorded


@pytest.fixture()
def world(tmp_path):
    handle = WorldModel(tmp_path / "world.db")
    yield handle
    handle.close()


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """The matter ledger for the active workspace, kept under the test's temp directory."""
    from maverick import paths

    path = tmp_path / "ledger.sqlite3"
    monkeypatch.setattr(ediscovery, "store_path", lambda tenant=None: path)
    monkeypatch.setattr(paths, "current_tenant_id", lambda: "ws-a")
    return path



def _values(**overrides):
    values = {"name": "Acme dispute", "description": "Contract review",
              "custodians": [ALICE], "record_kinds": ["goals", "chats", "attachments", "approvals"]}
    values.update(overrides)
    return values


def _seed(world):
    alice_goal = world.create_goal("Alice task", owner=ALICE)
    bob_goal = world.create_goal("Bob task", owner=BOB)
    alice_conv = world.get_or_create_conversation("cli", "alice").id
    bob_conv = world.get_or_create_conversation("cli", "bob").id
    world.append_turn(alice_conv, "user", "alice private remark", goal_id=alice_goal)
    world.append_turn(bob_conv, "user", "bob private remark", goal_id=bob_goal)
    world.add_attachment(alice_goal, "alice.txt", "text/plain", 3, "a" * 64, "/tmp/alice.txt")
    world.add_attachment(bob_goal, "bob.txt", "text/plain", 3, "b" * 64, "/tmp/bob.txt")
    world.start_episode(alice_goal)
    world.create_approval("send_contract", requested_by=ALICE, detail="alice approval")
    world.create_approval("send_contract", requested_by=BOB, detail="bob approval")
    return {"alice_goal": alice_goal, "bob_goal": bob_goal, "alice_conv": alice_conv, "bob_conv": bob_conv}


# ---- configuration gate -------------------------------------------------------

def test_feature_is_off_unless_enable_is_strictly_true(monkeypatch):
    from maverick import config

    monkeypatch.setattr(config, "config_source_errors", list)
    for loaded, expected in [({}, False), ({"ediscovery": {"enable": "true"}}, False),
                             ({"ediscovery": {"enable": 1}}, False), ({"ediscovery": {"enable": True}}, True)]:
        monkeypatch.setattr(config, "load_config", lambda loaded=loaded: loaded)
        assert ediscovery.enabled() is expected, loaded
    monkeypatch.setattr(config, "load_config", dict)
    with pytest.raises(ediscovery.EdiscoveryDisabled):
        ediscovery.require_enabled()


def test_matter_recorded_while_enabled_keeps_binding_after_disable(monkeypatch, events):
    with tenant_scope(tenant="ws-a"):
        ediscovery.MatterStore(ediscovery.store_path()).create("ws-a", actor="auditor", values=_values())
        monkeypatch.setattr(ediscovery, "enabled", lambda: False)
        assert ediscovery.active_matter_count() == 1


# ---- validation ---------------------------------------------------------------

@pytest.mark.parametrize("values", [
    _values(name=""),
    _values(name="x" * 201),
    _values(name="bad\x07name"),
    _values(custodians=[]),
    _values(custodians=["user:cli:has space"]),
    _values(record_kinds=[]),
    _values(record_kinds=["goals", "goals"]),
    _values(record_kinds=["email"]),
    _values(date_from="2026-02-30"),
    _values(date_from="2026-03-02", date_to="2026-03-01"),
    _values(owner="someone"),
])
def test_invalid_matter_input_is_refused(tmp_path, values, events):
    store = ediscovery.MatterStore(tmp_path / "ledger.sqlite3")
    with pytest.raises(ediscovery.MatterValidationError):
        store.create("ws-a", actor="auditor", values=values)
    assert store.list("ws-a") == []


# ---- audited lifecycle ----------------------------------------------------------

def test_create_edit_release_are_audited_and_revision_checked(tmp_path, events):
    store = ediscovery.MatterStore(tmp_path / "ledger.sqlite3")
    matter = store.create("ws-a", actor="auditor", values=_values())
    assert matter["revision"] == 1 and matter["status"] == "active"

    edited = store.update("ws-a", matter["id"], actor="auditor", expected_revision=1,
                          values={"custodians": [ALICE, BOB]})
    assert edited["revision"] == 2 and edited["custodians"] == [ALICE, BOB]
    assert edited["date_from"] is None  # a partial edit never clears untouched fields

    with pytest.raises(ediscovery.MatterConflict):
        store.update("ws-a", matter["id"], actor="auditor", expected_revision=1, values={"name": "Old"})

    released = store.release("ws-a", matter["id"], actor="auditor", expected_revision=2)
    assert released["status"] == "released" and released["released_by"] == "auditor"
    with pytest.raises(ediscovery.MatterConflict):
        store.update("ws-a", matter["id"], actor="auditor", expected_revision=3, values={"name": "Later"})

    verbs = [verb for verb, _ in events]
    assert verbs == ["ediscovery_matter_created", "ediscovery_matter_edited", "ediscovery_matter_released"]
    created_detail = events[0][1]
    assert created_detail["custodian_count"] == 1
    assert ALICE not in json.dumps(events)  # audit names no custodian


def test_change_is_not_saved_when_its_audit_record_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "audit_event", lambda kind, **payload: False)
    store = ediscovery.MatterStore(tmp_path / "ledger.sqlite3")
    with pytest.raises(ediscovery.EdiscoveryError, match="audit record"):
        store.create("ws-a", actor="auditor", values=_values())
    assert store.list("ws-a") == []


def test_matters_are_tenant_scoped_and_tamper_evident(tmp_path, events):
    store = ediscovery.MatterStore(tmp_path / "ledger.sqlite3")
    matter = store.create("ws-a", actor="auditor", values=_values())
    with pytest.raises(ediscovery.MatterNotFound):
        store.get("ws-b", matter["id"])
    assert store.list("ws-b") == []
    with pytest.raises(ediscovery.MatterNotFound):
        store.release("ws-b", matter["id"], actor="auditor", expected_revision=1)

    with sqlite3.connect(tmp_path / "ledger.sqlite3") as connection:
        connection.execute("UPDATE matters SET payload_json=replace(payload_json,'Acme','Other')")
    with pytest.raises(ediscovery.EdiscoveryError, match="could not be verified"):
        store.get("ws-a", matter["id"])


def test_active_matters_are_read_per_workspace(tmp_path, events):
    with tenant_scope(tenant="ws-a"):
        ediscovery.MatterStore(ediscovery.store_path()).create("ws-a", actor="auditor", values=_values())
        assert ediscovery.active_matter_count() == 1
    with tenant_scope(tenant="ws-b"):
        assert ediscovery.active_matter_count() == 0


# ---- broad purge gates ----------------------------------------------------------

def test_active_matter_blocks_broad_purge_and_release_lifts_it(events, monkeypatch):
    purged = []
    monkeypatch.setattr(retention, "purge_world_messages", lambda **kw: purged.append(kw))
    with tenant_scope(tenant="ws-a"):
        store = ediscovery.MatterStore(ediscovery.store_path())
        matter = store.create("ws-a", actor="auditor", values=_values())
        snapshot = preservation.preservation_snapshot()
        assert snapshot["broad_purge_blocked"] is True and snapshot["blocked"] is False
        assert snapshot["active_matters"] == 1
        with pytest.raises(retention.RetentionScopeError):
            retention.enforce(config={"messages_days": 1}, _fleet_child=True)
        with pytest.raises(ediscovery.LegalHoldApplies):
            records_gc.prune_world(_FakeWorld(), days=1, events_days=1)
        assert purged == []

        store.release("ws-a", matter["id"], actor="auditor", expected_revision=1)
        assert preservation.preservation_snapshot()["broad_purge_blocked"] is False
        retention.enforce(config={"messages_days": 1}, _fleet_child=True)
        assert len(purged) == 1
        assert records_gc.prune_world(_FakeWorld(), days=1, events_days=1) == (0, 0, 0)


class _FakeWorld:
    def prune_conversations(self, **kwargs):
        return 0

    def prune_goal_events(self, **kwargs):
        return 0

    def prune_processed_messages(self, **kwargs):
        return 0


# ---- per-person erasure ---------------------------------------------------------

def test_erasure_refused_only_for_in_scope_records(world, ledger, events):
    seeded = _seed(world)
    ediscovery.MatterStore(ledger).create("ws-a", actor="auditor",
                                          values=_values(record_kinds=["goals", "chats", "attachments"]))

    message = ediscovery.erasure_refusal(world, conversation_ids=[seeded["alice_conv"]],
                                         goal_ids=[seeded["alice_goal"]])
    assert message == ediscovery.LEGAL_HOLD_MESSAGE
    assert "Acme" not in message and "custodian" not in message.lower()
    assert ediscovery.erasure_refusal(world, conversation_ids=[seeded["bob_conv"]],
                                      goal_ids=[seeded["bob_goal"]]) is None


def test_erasure_of_another_person_and_out_of_range_records_is_allowed(world, ledger, events):
    seeded = _seed(world)
    ediscovery.MatterStore(ledger).create("ws-a", actor="auditor", values=_values(**PAST))
    assert ediscovery.erasure_refusal(world, conversation_ids=[seeded["alice_conv"]],
                                      goal_ids=[seeded["alice_goal"]]) is None
    ediscovery.MatterStore(ledger).create("ws-a", actor="auditor", values=_values(custodians=[BOB]))
    assert ediscovery.erasure_refusal(world, conversation_ids=[seeded["bob_conv"]],
                                      goal_ids=[seeded["bob_goal"]]) == ediscovery.LEGAL_HOLD_MESSAGE


def test_matter_kinds_limit_what_is_held(world, ledger, events):
    seeded = _seed(world)
    ediscovery.MatterStore(ledger).create("ws-a", actor="auditor", values=_values(record_kinds=["approvals"]))
    assert ediscovery.erasure_refusal(world, conversation_ids=[seeded["alice_conv"]],
                                      goal_ids=[seeded["alice_goal"]]) is None


def test_closure_through_another_persons_chat_is_held_when_it_reaches_a_custodian_goal(world, ledger, events):
    seeded = _seed(world)
    world.append_turn(seeded["bob_conv"], "assistant", "linked to alice's task", goal_id=seeded["alice_goal"])
    ediscovery.MatterStore(ledger).create("ws-a", actor="auditor", values=_values())
    assert ediscovery.erasure_refusal(world, conversation_ids=[seeded["bob_conv"]],
                                      goal_ids=[seeded["alice_goal"]]) == ediscovery.LEGAL_HOLD_MESSAGE


def test_released_matter_no_longer_holds_erasure(world, ledger, events):
    seeded = _seed(world)
    store = ediscovery.MatterStore(ledger)
    matter = store.create("ws-a", actor="auditor", values=_values())
    store.release("ws-a", matter["id"], actor="auditor", expected_revision=1)
    assert ediscovery.erasure_refusal(world, conversation_ids=[seeded["alice_conv"]],
                                      goal_ids=[seeded["alice_goal"]]) is None


def test_cli_erase_stops_before_deleting_anything(world, ledger, events, tmp_path):
    from maverick import cli as cli_mod

    seeded = _seed(world)
    db = tmp_path / "world.db"
    ediscovery.MatterStore(ledger).create("ws-a", actor="auditor", values=_values())
    world.close()
    result = CliRunner().invoke(cli_mod.main, ["--db", str(db), "erase", "--channel", "cli",
                                               "--user", "alice", "--yes"])
    assert result.exit_code != 0
    assert ediscovery.LEGAL_HOLD_MESSAGE in result.output
    check = WorldModel(db)
    try:
        rows = check.conn.execute("SELECT COUNT(*) FROM turns WHERE conversation_id=?",
                                  (seeded["alice_conv"],)).fetchone()[0]
        assert rows == 1
    finally:
        check.close()


# ---- export ----------------------------------------------------------------------

def test_export_contains_exactly_the_scoped_records_with_verifiable_signature(world, ledger, events, tmp_path):
    _seed(world)
    store = ediscovery.MatterStore(ledger)
    matter = store.create("ws-a", actor="auditor", values=_values())

    result = ediscovery_export.export_matter(world, matter, actor="auditor", out_dir=tmp_path / "exports")
    archive_path = result["path"]
    assert archive_path.exists() and result["records"] > 0

    with zipfile.ZipFile(archive_path) as archive:
        assert set(archive.namelist()) == {"goals.jsonl", "chats.jsonl", "attachments.jsonl",
                                           "approvals.jsonl", "manifest.json"}
        raw = b"".join(archive.read(name) for name in archive.namelist())
        assert b"alice private remark" in raw and b"bob private remark" not in raw
        assert b"bob approval" not in raw and b"bob.txt" not in raw
        goals = [json.loads(line) for line in archive.read("goals.jsonl").splitlines()]
        assert {row["record_type"] for row in goals} == {"goal", "run"}
        assert all(row.get("owner", ALICE) == ALICE for row in goals if row["record_type"] == "goal")
        attachments = [json.loads(line) for line in archive.read("attachments.jsonl").splitlines()]
        assert [row["filename"] for row in attachments] == ["alice.txt"]
        approvals = [json.loads(line) for line in archive.read("approvals.jsonl").splitlines()]
        assert [row["detail"] for row in approvals] == ["alice approval"]
        manifest = json.loads(archive.read("manifest.json"))
        for entry in manifest["files"]:
            data = archive.read(entry["path"])
            assert len(data) == entry["bytes"]
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        assert manifest["matter"]["id"] == matter["id"]
        assert manifest["matter"]["custodians"] == [ALICE]

    from maverick.audit.signing import trusted_audit_public_keys

    verdict = ediscovery_export.verify_export(archive_path, trusted_keys=trusted_audit_public_keys())
    assert verdict["valid"] is True, verdict
    assert ediscovery_export.verify_export(archive_path, trusted_keys={})["valid"] is False

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive_path) as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "goals.jsonl":
                data = data.replace(b"Alice task", b"Edited task")
            target.writestr(name, data)
    from maverick.audit.signing import trusted_audit_public_keys as keys

    assert "goals.jsonl" in ediscovery_export.verify_export(tampered, trusted_keys=keys())["problems"]
    assert [event[0] for event in events][-1] == "ediscovery_matter_exported"


def test_export_is_bounded_and_leaves_no_partial_file(world, ledger, events, tmp_path):
    _seed(world)
    matter = ediscovery.MatterStore(ledger).create("ws-a", actor="auditor", values=_values())
    with pytest.raises(ediscovery.MatterExportTooLarge, match="too large"):
        ediscovery_export.export_matter(world, matter, actor="auditor", out_dir=tmp_path / "exports",
                                        max_bytes=10)
    assert list((tmp_path / "exports").glob("*.zip")) == []


def test_export_is_refused_when_its_audit_record_cannot_be_written(world, ledger, monkeypatch, tmp_path):
    _seed(world)
    monkeypatch.setattr(audit, "audit_event", lambda kind, **payload: True)
    matter = ediscovery.MatterStore(ledger).create("ws-a", actor="auditor", values=_values())
    monkeypatch.setattr(audit, "audit_event", lambda kind, **payload: False)
    with pytest.raises(ediscovery.MatterExportError, match="audit record"):
        ediscovery_export.export_matter(world, matter, actor="auditor", out_dir=tmp_path / "exports")
    assert list((tmp_path / "exports").glob("*.zip")) == []


def test_unenumerable_scope_fails_closed(world, ledger, events, monkeypatch):
    seeded = _seed(world)
    ediscovery.MatterStore(ledger).create("ws-a", actor="auditor", values=_values())
    monkeypatch.setattr(ediscovery, "MAX_SCOPE_ROWS", 0)
    with pytest.raises(ediscovery.LegalHoldApplies):
        ediscovery.assert_erasure_permitted(world, conversation_ids=[seeded["bob_conv"]], goal_ids=[])
