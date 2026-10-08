"""Saved remote Knowledge sources: the refresh claim and the freshness window."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from maverick import knowledge_sources as native
from maverick.paths import tenant_scope

TENANT = "acme"
OWNER = "user:alice"
SHA = "a" * 64


class _Registry:
    @staticmethod
    def data_use_resolver(tenant, decision_id, decision_sha256):
        return SimpleNamespace(tenant=tenant, decision_id=decision_id, decision_sha256=decision_sha256, asset_id="asset-1",
                               action="analyze", purpose_codes=("support",), purpose="support",
                               expires_at=4_000_000_000.0, authority_sha256=SHA)


def _embedding():
    from maverick.knowledge_reindex_jobs import _configuration_revision

    return {"billing": None, "price_basis": "local", "configuration": _configuration_revision()}


def _plan(interval):
    return {"name": "Team drive", "binding": {"kind": "test"}, "decision_id": "dec-1", "decision_sha256": SHA,
            "asset_id": "asset-1", "purpose": "support", "interval_seconds": interval, "max_dollars": 0.0,
            "permission_evidence_sha256": SHA}


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setattr(native, "enabled", lambda: True)
    monkeypatch.setattr(native.readers, "account", lambda *args, **kwargs: None)
    with tenant_scope(tenant=TENANT):
        svc = native.KnowledgeSources(TENANT, OWNER, registry=_Registry(), authority=lambda: None)
        monkeypatch.setattr(svc, "_embedding_review", lambda plan: _embedding())
        yield svc


def _prepared(service, interval, *, due=False):
    saved = service.save(_plan(interval), expected_revision=0)["source"]
    row = service.sources.get(saved["id"])
    if due:  # the scheduler picks a source up once its next refresh has passed
        row = service.sources.update(row["id"], lambda current: current.update(next_refresh=time.time() - 5),
                                     expected_revision=row["revision"], action="test.due", actor=OWNER)
    return {"source": row["id"], "plan_sha256": row["plan_sha256"], "revision": row["revision"],
            "embedding": _embedding()}, row


def test_a_refresh_that_cannot_be_recorded_does_not_leave_the_source_claimed(service, monkeypatch):
    prepared, before = _prepared(service, 3600, due=True)
    # The job ledger is full: its capacity is a fixed bound with no pruning.
    monkeypatch.setattr(native, "MAX_JOBS", 1)
    native.JOB_STORE.create({"id": "KNJ-" + "0" * 32, "source": "other", "tenant": TENANT, "owner": OWNER, "state": "completed"},
                            action="knowledge_source.refresh_queued", actor=OWNER)

    with pytest.raises(Exception, match=r"."):
        service.start(prepared, "1" * 32)

    row = service.sources.get(before["id"])
    assert row["active_job"] is None
    assert (row["state"], row["projection_allowed"]) == (before["state"], before["projection_allowed"])
    # A scheduled source is not retried on the scheduler's very next cycle.
    assert row["next_refresh"] >= time.time() + 3600 - 60 and before["next_refresh"] < time.time()
    # The source is not stuck: once the ledger has room, a new refresh starts.
    monkeypatch.setattr(native, "MAX_JOBS", 100)
    current = service.sources.get(before["id"])
    again = dict(prepared, revision=current["revision"])
    assert service.start(again, "2" * 32)["state"] == "queued"


def test_a_started_refresh_still_claims_the_source(service):
    prepared, before = _prepared(service, 3600)
    job = service.start(prepared, "3" * 32)
    row = service.sources.get(before["id"])
    assert job["state"] == "queued" and row["active_job"] == "KNJ-" + "3" * 32 and row["state"] == "queued"


@pytest.mark.parametrize(("interval", "slack_seconds"), [(3600, 3600), (21600, 3600), (86400, 3600), (0, 0)])
def test_a_completed_refresh_stays_fresh_past_its_next_scheduled_refresh(service, interval, slack_seconds):
    prepared, before = _prepared(service, interval)
    job = service.start(prepared, "4" * 32)
    service._finish(service.jobs.get(job["id"]), "completed", "done", items=[], changed=0, unchanged=0, removed=0,
                    snapshot="KNV-" + "b" * 32)
    row = service.sources.get(before["id"])
    assert row["projection_allowed"] is True
    if interval:
        # Latency in the scheduler or the refresh itself must not open a gap: the
        # old snapshot stays usable for an hour after the refresh was due.
        assert row["fresh_until"] - row["next_refresh"] == pytest.approx(slack_seconds, abs=5)
    else:
        assert row.get("next_refresh") is None
        assert row["fresh_until"] - row["last_refresh"] == pytest.approx(86400, abs=5)
