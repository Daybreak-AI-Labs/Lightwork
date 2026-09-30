"""Internal preservation gates; authored for deferred execution."""
import hashlib
import json
import sqlite3

import pytest
from maverick.audit import preservation, retention


def test_preservation_reads_every_case_even_when_feature_disabled(tmp_path, monkeypatch):
    from maverick import paths
    path = tmp_path / "rights.sqlite3"
    monkeypatch.setattr(paths, "data_dir", lambda *a, **kw: path)
    monkeypatch.setattr(paths, "current_tenant_id", lambda: "workspace")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE rights_cases(tenant_id TEXT,case_id TEXT,payload_json TEXT,integrity_sha256 TEXT)")
        for n in range(1002):
            payload = {"tenant_id": "workspace", "legal_hold": n == 1001, "retention_blocks_deletion": False}
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            connection.execute("INSERT INTO rights_cases VALUES(?,?,?,?)", ("workspace", str(n), encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    snapshot = preservation.preservation_snapshot()
    assert snapshot["blocked"]
    assert snapshot["legal_holds"] == 1
    assert "case_id" not in snapshot


def test_core_purge_fails_closed_for_hold_and_unreadable_ledger(monkeypatch):
    calls = []
    monkeypatch.setattr(retention, "purge_world_messages", lambda **kw: calls.append(kw))
    monkeypatch.setattr(preservation, "preservation_snapshot", lambda: {"blocked": True})
    with pytest.raises(retention.RetentionScopeError):
        retention.enforce(config={"messages_days": 1}, _fleet_child=True)
    assert calls == []
    def unavailable():
        raise OSError("locked")
    monkeypatch.setattr(preservation, "preservation_snapshot", unavailable)
    with pytest.raises(retention.RetentionScopeError):
        retention.enforce(config={"messages_days": 1}, _fleet_child=True)
    assert calls == []
