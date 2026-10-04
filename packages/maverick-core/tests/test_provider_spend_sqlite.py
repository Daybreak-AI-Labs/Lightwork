"""The local spend authority migrates without reopening a provider attempt."""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from maverick import provider_cost_cap as cap
from maverick import provider_spend_sqlite as indexed
from maverick import provider_spending_recovery as spending
from maverick import workspace_recovery
from maverick.paths import data_dir, tenant_scope


def test_indexed_migration_keeps_source_and_blocks_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    config.write_text("[budget.provider_caps]\nopenai = 20.0\n")
    monkeypatch.setenv("MAVERICK_CONFIG", str(config))
    monkeypatch.setattr(cap, "_INDEXED_MIGRATION_BYTES", 1)
    path = cap._ledger_path()
    cap._save_json(path, {cap.period_key(): {"openai": 1.25}})
    original = path.read_bytes()

    hold = cap.reserve(
        "openai", 1.0, operation_id="one-physical-attempt",
        category="inference", attempt_no=1,
    )
    cap.settle(hold, 0.75)

    assert indexed.is_active(path)
    assert path.read_bytes() == original
    assert cap.check("openai").spent == pytest.approx(2.0)
    assert cap.inspect_reservation(cap.reservation_reference(hold)).actual == 0.75
    with pytest.raises(cap.ProviderAttemptAlreadyAdmitted):
        cap.reserve(
            "openai", 1.0, operation_id="one-physical-attempt",
            category="inference", attempt_no=1,
        )


def test_missing_published_database_never_falls_back_to_old_json(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    config.write_text("[budget.provider_caps]\nopenai = 20.0\n")
    monkeypatch.setenv("MAVERICK_CONFIG", str(config))
    monkeypatch.setattr(cap, "_INDEXED_MIGRATION_BYTES", 1)
    path = cap._ledger_path()
    cap._save_json(path, {cap.period_key(): {"openai": 1.25}})
    assert cap.check("openai").spent == 1.25
    indexed.database_path(path).unlink()

    with pytest.raises(cap.ProviderAccountingUnavailable):
        cap.check("openai")
    with pytest.raises(cap.ProviderAccountingUnavailable):
        cap.reserve("openai", 1.0)
    assert cap._load_json(path)[cap.period_key()]["openai"] == 1.25


def test_racing_writers_do_not_update_retained_json_after_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setattr(cap, "_INDEXED_MIGRATION_BYTES", 1)
    path = cap._ledger_path()
    cap._save_json(path, {cap.period_key(): {"openai": 1.0}})
    original = path.read_bytes()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: cap.record("openai", 0.25), range(40)))

    assert cap.check("openai").spent == 11.0
    assert path.read_bytes() == original


def test_indexed_repair_keeps_failed_database_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setattr(cap, "_INDEXED_MIGRATION_BYTES", 1)
    monkeypatch.setattr(workspace_recovery, "_key", lambda: bytes(range(32)))
    monkeypatch.setattr(spending, "_isolated", lambda _isolated: None)
    with tenant_scope(tenant="repair-workspace"):
        path = cap._ledger_path()
        cap._save_json(path, {cap.period_key(): {"openai": 5.0}})
        assert cap.check("openai").spent == 5.0
        exported = spending.export_accounting()
        indexed.database_path(path).write_bytes(b"broken database")
        imported = spending.stage_import(
            exported, actor="owner", context="account", isolated=True,
        )
        review = spending.prepare_repair(
            imported["id"],
            [{"provider": "openai", "period": cap.period_key(), "confirmed": 7.0}],
            "Final provider invoice checked", actor="owner", context="account",
            isolated=True, receipts_complete=True,
        )
        assert spending.apply(
            review["id"], actor="owner", context="account", isolated=True,
            confirm=True,
        )["state"] == "completed"
        assert cap.check("openai").spent == 7.0
        assert data_dir("provider_accounting_reviews", review["id"] + ".failed").read_bytes() == b"broken database"


def test_off_target_receipt_tampering_blocks_hot_admission(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    config.write_text("[budget.provider_caps]\nopenai = 20.0\n")
    monkeypatch.setenv("MAVERICK_CONFIG", str(config))
    monkeypatch.setattr(cap, "_INDEXED_MIGRATION_BYTES", 1)
    path = cap._ledger_path()
    cap._save_json(path, {cap.period_key(): {"openai": 1.0}})
    hold = cap.reserve("openai", 1.0, request_id="original")
    cap.settle(hold, 0.5)
    with sqlite3.connect(indexed.database_path(path)) as connection:
        connection.execute(
            "UPDATE receipts SET payload=json_set(payload, '$.actual', 0) "
            "WHERE request_id='original'"
        )

    with pytest.raises(cap.ProviderAccountingUnavailable):
        cap.check("openai")
    with pytest.raises(cap.ProviderAccountingUnavailable):
        cap.reserve("openai", 0.1, request_id="different")


def test_read_waits_for_normal_commit_and_seal(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    config = tmp_path / "config.toml"
    config.write_text("[budget.provider_caps]\nopenai = 20.0\n")
    monkeypatch.setenv("MAVERICK_CONFIG", str(config))
    monkeypatch.setattr(cap, "_INDEXED_MIGRATION_BYTES", 1)
    path = cap._ledger_path()
    cap._save_json(path, {cap.period_key(): {"openai": 1.0}})
    hold = cap.reserve("openai", 1.0, request_id="physical")
    entered, release, reading = Event(), Event(), Event()
    original = indexed._publish_seal

    def delayed_seal(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(indexed, "_publish_seal", delayed_seal)
    def read():
        reading.set()
        return cap.check("openai")

    with ThreadPoolExecutor(max_workers=2) as pool:
        settled = pool.submit(cap.settle, hold, 0.5)
        assert entered.wait(5)
        reader = pool.submit(read)
        assert reading.wait(5)
        assert not reader.done()
        release.set()
        settled.result(timeout=5)
        assert reader.result(timeout=5).spent == 1.5


def test_online_sqlite_backup_retains_logical_seal(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setattr(cap, "_INDEXED_MIGRATION_BYTES", 1)
    path = cap._ledger_path()
    cap._save_json(path, {cap.period_key(): {"openai": 1.0}})
    cap.record("openai", 0.5)
    source = indexed.database_path(path)
    snapshot = tmp_path / "snapshot.sqlite3"
    original = sqlite3.connect(source)
    copy = sqlite3.connect(snapshot)
    try:
        original.backup(copy)
    finally:
        copy.close()
        original.close()
    assert source.read_bytes() != snapshot.read_bytes()
    assert indexed._digest_file(source) == indexed._digest_file(snapshot)
    changed = bytearray(snapshot.read_bytes())
    changed[32] ^= 1  # a nonvolatile SQLite header field remains sealed
    snapshot.write_bytes(changed)
    assert indexed._digest_file(source) != indexed._digest_file(snapshot)


def test_hot_status_reads_only_live_holds_after_large_settled_history(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setattr(cap, "_INDEXED_MIGRATION_BYTES", 1)
    path = cap._ledger_path()
    period = cap.period_key()
    receipts = {
        f"settled-{number}": {"projected": 0.01, "state": "settled", "actual": 0.01}
        for number in range(5_000)
    }
    receipts["live-hold"] = {"projected": 1.25, "state": "held", "actual": None}
    receipts["unknown-charge"] = {"projected": 0.5, "state": "unknown", "actual": None}
    cap._save_json(path, {
        period: {"openai": 51.75},
        cap._RECEIPTS: {period: {"openai": receipts}},
    })

    steps = 0
    original_connect = indexed._connect

    def counted_connect(database):
        connection = original_connect(database)

        def progress():
            nonlocal steps
            steps += 1
            return 0

        connection.set_progress_handler(progress, 1_000)
        return connection

    monkeypatch.setattr(indexed, "_connect", counted_connect)
    total, held, unresolved = indexed.read_status(path, period, "openai", cap)
    assert total == pytest.approx(51.75)
    assert held == pytest.approx(1.75)
    assert unresolved is True
    # SQLite calls this handler every 1,000 VM instructions. A scan of all
    # settled receipts in the hot path needs far more than this bound.
    assert steps < 10
