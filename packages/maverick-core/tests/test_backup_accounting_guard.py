"""Recovery must not rewind irreversible spend/holds or opaque DB accounting."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from maverick import backup, file_lock


@pytest.fixture
def recovery(tmp_path):
    root = file_lock.ensure_private_directory(tmp_path / "live")
    file_lock.atomic_write_text(root / "document.txt", "unchanged non-accounting fixture")
    archive = tmp_path / "selected.tgz"
    with backup.recovery_scope(root, "guard-workspace", bytes(range(32))):
        yield root, archive


@pytest.mark.parametrize("name", [
    "usage/ledger.json", "usage/reservations.json", "usage/future-settlements.json",
    "tenants/second/usage/ledger.json", "provider_spend.json",
    "llm_gateway_team_budget/team_budget.json", "mcp_sidecar/call_budget.json",
    "budget_receipts.jsonl", "budget_receipts.jsonl.head", "budget_receipts.required",
    "tenant_registry.json",
])
@pytest.mark.parametrize("mutation", ["changed", "added", "deleted"])
def test_guard_refuses_every_irreversible_accounting_path_without_changing_live_state(recovery, name, mutation):
    root, archive = recovery
    path = root / name
    file_lock.ensure_private_directory(path.parent)
    if mutation != "added":
        file_lock.atomic_write_text(path, "opaque old-format state")
    backup.create_backup(archive)
    if mutation == "deleted":
        path.unlink()
    else:
        file_lock.atomic_write_text(path, "opaque new-format state")
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(backup.BackupError, match="reconciliation"):
        backup.assert_accounting_unchanged(archive)
    assert (path.read_bytes() if path.exists() else None) == before
    assert backup.verify_backup(archive)["client_id"] == "guard-workspace"


def _database(path: Path, value: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS opaque_state (value TEXT)")
        connection.execute("INSERT INTO opaque_state VALUES (?)", (value,))


@pytest.mark.parametrize("name", ["world.db", "custom.sqlite", "consent.sqlite3", "opaque.store", "custom.lock"])
@pytest.mark.parametrize("mutation", ["changed", "added", "deleted"])
def test_all_sqlite_formats_are_protected_without_guessing_accounting_schema(recovery, name, mutation):
    root, archive = recovery
    path = root / name
    if mutation != "added":
        _database(path, "before")
    backup.create_backup(archive)
    if mutation == "deleted":
        path.unlink()
    else:
        _database(path, "after")
    with pytest.raises(backup.BackupError, match="reconciliation"):
        backup.assert_accounting_unchanged(archive)


def test_unchanged_accounting_allows_non_database_documents_and_lock_file_changes(recovery):
    root, archive = recovery
    usage = file_lock.ensure_private_directory(root / "usage")
    file_lock.atomic_write_text(usage / "ledger.json", '{"opaque": "accounting"}')
    file_lock.atomic_write_text(root / "document.txt", "before")
    backup.create_backup(archive)
    file_lock.atomic_write_text(root / "document.txt", "after")
    file_lock.atomic_write_text(usage / "ledger.json.lock", "different lock bytes")
    file_lock.atomic_write_text(root / "budget_receipts.jsonl.lock", "different lock bytes")
    backup.assert_accounting_unchanged(archive)
    assert (root / "document.txt").read_text() == "after"


def test_sqlite_wal_checkpoint_without_logical_changes_does_not_block(recovery):
    root, archive = recovery
    connection = sqlite3.connect(root / "custom.store")
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE usage_settlement_scopes (scope TEXT)")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("INSERT INTO usage_settlement_scopes VALUES ('settled')")
        connection.commit()
        backup.create_backup(archive)
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        backup.assert_accounting_unchanged(archive)
    finally:
        connection.close()


def test_guard_authenticates_snapshot_before_trusting_accounting(recovery):
    root, archive = recovery
    file_lock.atomic_write_text(root / "provider_spend.json", "current spend")
    backup.create_backup(archive)
    data = bytearray(archive.read_bytes())
    data[len(data) // 2] ^= 1
    archive.write_bytes(data)
    with pytest.raises(backup.BackupError):
        backup.assert_accounting_unchanged(archive)
    assert (root / "provider_spend.json").read_text() == "current spend"
