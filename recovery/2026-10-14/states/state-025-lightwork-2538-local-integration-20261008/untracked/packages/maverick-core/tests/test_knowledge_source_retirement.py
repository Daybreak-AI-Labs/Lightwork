from __future__ import annotations

from copy import deepcopy

import pytest


class _Store:
    def __init__(self, records=()):
        self.rows = {row["id"]: deepcopy(row) for row in records}
        self.actions = []

    def get(self, identifier):
        row = self.rows.get(identifier)
        return deepcopy(row) if row is not None else None

    def create(self, value, *, action, actor, effect_guard=None, **_kwargs):
        if effect_guard:
            effect_guard()
        if value["id"] in self.rows:
            raise ValueError("duplicate")
        row = deepcopy(value)
        row["revision"] = 1
        self.rows[row["id"]] = row
        self.actions.append(action)
        return deepcopy(row)

    def update(self, identifier, mutate, *, expected_revision, action, actor, effect_guard=None, **_kwargs):
        if effect_guard:
            effect_guard()
        row = self.rows[identifier]
        if row["revision"] != expected_revision:
            raise ValueError("revision changed")
        updated = deepcopy(row)
        mutate(updated)
        updated["revision"] += 1
        self.rows[identifier] = updated
        self.actions.append(action)
        return deepcopy(updated)

    def list(self, *, limit=None):
        values = list(self.rows.values())
        return deepcopy(values if limit is None else values[:limit])

    @staticmethod
    def authoritative_time():
        import time

        return time.time()


def _source():
    return {"id": "KNS-" + "a" * 32, "revision": 4, "tenant": "workspace-a", "owner": "user:alice",
            "plan": {"name": "Policies", "interval_seconds": 3600}, "plan_sha256": "b" * 64,
            "state": "configured", "paused": False, "retired": False, "projection_allowed": True,
            "next_refresh": 1_900_000_000,
            "active_job": None, "snapshot": None}


def _service(monkeypatch, *, source=None, active_job=None):
    from maverick import knowledge_sources as native

    monkeypatch.setattr(native, "enabled", lambda: True)
    source = deepcopy(source or _source())
    source["active_job"] = active_job
    sources, jobs, snapshots, decisions = _Store([source]), _Store(), _Store(), _Store()
    monkeypatch.setattr(native, "DECISION_STORE", decisions)
    monkeypatch.setattr(native, "SOURCE_STORE", sources)
    service = native.KnowledgeSources("workspace-a", "user:alice", registry=object(), authority=lambda: None,
                                      sources=sources, jobs=jobs, snapshots=snapshots)
    return native, service, sources, jobs, decisions


def test_source_stop_blocks_retrieval_and_exact_retry_reads_original(monkeypatch):
    native, service, sources, _jobs, decisions = _service(monkeypatch)
    operation = "c" * 32

    first = service.retire(_source()["id"], expected_revision=4, operation=operation)

    row = sources.get(_source()["id"])
    assert first["state"] == "confirmed"
    assert row["retired"] is True and row["projection_allowed"] is False
    assert first["source"]["edition_available"] is False
    assert row["paused"] is True and row["next_refresh"] is None
    assert row["last_retire_operation"] == operation
    assert "knowledge_source.retired" in sources.actions

    revision = row["revision"]
    again = service.retire(_source()["id"], expected_revision=4, operation=operation)
    assert again["state"] == "confirmed"
    assert sources.get(_source()["id"])["revision"] == revision
    assert decisions.get("KNO-" + operation)["state"] == "confirmed"

    authority = type("Authority", (), {"action": "analyze", "purpose": "product_research", "principal": "user:alice"})()
    with pytest.raises(PermissionError, match="Current saved remote source rights"):
        native.require_remote_document_authority("workspace-a", "remote-" + _source()["id"][4:] + "-" + "d" * 16,
                                                 "e" * 64, authority)


def test_saved_source_without_a_fresh_approved_edition_is_not_shown_as_available(monkeypatch):
    _native, service, _sources, _jobs, _decisions = _service(monkeypatch)

    view = service.public(_source())

    assert view["edition_available"] is False


def test_saved_source_edition_requires_fresh_projection_current_permission_and_approval(monkeypatch):
    import time

    _native, service, _sources, _jobs, _decisions = _service(monkeypatch)
    row = _source()
    row.update(snapshot="KNV-" + "1" * 32, fresh_until=time.time() + 60)
    reference = {"kind": "dataset", "asset_id": "policy-source", "version": "v1", "manifest_sha256": "a" * 64}
    service.snapshots = _Store([{"id": row["snapshot"], "reference": reference, "items": []}])
    service._plan_guard = lambda _plan: None
    service.registry = type("Registry", (), {"inspect": lambda *_args: {"state": "approved"}})()

    assert service.public(row)["edition_available"] is True

    service.registry = type("Registry", (), {"inspect": lambda *_args: {"state": "revoked"}})()
    assert service.public(row)["edition_available"] is False

    def denied(_plan):
        raise PermissionError("permission changed")

    service._plan_guard = denied
    assert service.public(row)["edition_available"] is False


def test_source_stop_requires_the_reviewed_revision(monkeypatch):
    from maverick.privacy_ops import RecordConflict

    _native, service, sources, _jobs, decisions = _service(monkeypatch)

    with pytest.raises(RecordConflict, match="changed after review"):
        service.retire(_source()["id"], expected_revision=3, operation="d" * 32)

    assert sources.get(_source()["id"])["retired"] is False
    assert decisions.list() == []


def test_source_stop_does_not_race_a_live_refresh(monkeypatch):
    active_id = "KNJ-" + "e" * 32
    _native, service, sources, jobs, decisions = _service(monkeypatch, active_job=active_id)
    jobs.rows[active_id] = {"id": active_id, "state": "running", "revision": 1}

    with pytest.raises(ValueError, match="refresh is still in progress"):
        service.retire(_source()["id"], expected_revision=4, operation="f" * 32)

    assert sources.get(_source()["id"])["retired"] is False
    assert decisions.list() == []
