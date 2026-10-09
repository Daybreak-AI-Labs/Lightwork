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
    # The job ledger is full and its only receipt is another source's newest
    # history, so nothing can be removed to make room.
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


# The refresh ledger is bounded. Once full, the oldest finished receipts that
# nothing points at make room; each source keeps its newest history page.
PAGE = 50
OTHER = "KNS-" + "c" * 32


def _job_id(n):
    return f"KNJ-{n:032x}"


def _seed(n, source, state="completed", **values):
    return native.JOB_STORE.create({"id": _job_id(n), "source": source, "tenant": TENANT, "owner": OWNER, "state": state, **values},
                                   action="test.seed", actor=OWNER)


def _refresh(service, source, operation):
    row = service.sources.get(source)
    return service.start({"source": source, "plan_sha256": row["plan_sha256"], "revision": row["revision"], "embedding": _embedding()}, operation)


@pytest.fixture
def activity(monkeypatch):
    from maverick import audit

    log = SimpleNamespace(events=[], refuse=())

    def record(kind, **row):
        if row["action"] in log.refuse:
            return False
        log.events.append(row)
        return True
    monkeypatch.setattr(audit, "record", record)
    return log


def _pruned(activity):
    return [row["record_id"] for row in activity.events if row["action"] == "knowledge_source.refresh_pruned"]


def test_a_full_refresh_ledger_removes_its_oldest_finished_receipt_to_admit_a_refresh(service, monkeypatch, activity):
    _, before = _prepared(service, 3600)
    for n in range(PAGE + 2):  # two finished receipts older than the source's newest history page
        _seed(n, before["id"], "failed" if n % 2 else "completed", items=[{"name": "Q3 board plan.docx", "state": "imported"}])
    monkeypatch.setattr(native, "MAX_JOBS", PAGE + 2)
    monkeypatch.setattr(native, "PRUNE_BATCH", 1, raising=False)

    job = _refresh(service, before["id"], "e" * 32)

    assert job["state"] == "queued" and service.sources.get(before["id"])["active_job"] == job["id"]
    assert native.JOB_STORE.get(_job_id(0)) is None and native.JOB_STORE.get(_job_id(1)) is not None
    assert native.JOB_STORE.count(maximum=1000) == PAGE + 2
    # The removal is on the activity record first, as a digest only.
    assert _pruned(activity) == [_job_id(0)]
    event = next(row for row in activity.events if row["action"] == "knowledge_source.refresh_pruned")
    assert len(event["record_sha256"]) == 64 and "Q3 board plan" not in repr(event)
    # Refreshes keep working: the next one removes the next oldest receipt.
    service._finish(service.jobs.get(job["id"]), "completed", "done", items=[], changed=0, unchanged=0, removed=0, snapshot="KNV-" + "b" * 32)
    assert _refresh(service, before["id"], "f" * 32)["state"] == "queued"
    assert native.JOB_STORE.get(_job_id(1)) is None and _pruned(activity) == [_job_id(0), _job_id(1)]
    history = service.history(before["id"])
    assert len(history["items"]) == PAGE and history["items"][0]["id"] == "KNJ-" + "f" * 32


def test_active_uncertain_pending_and_referenced_refreshes_are_never_removed(service, monkeypatch, activity):
    from maverick.governed_records import GovernedRecordCapacityError

    _, before = _prepared(service, 3600)
    version = {"dataset_id": "asset-2", "version": "remote-" + "1" * 32}
    _seed(0, OTHER, reference=version)  # the receipt that recorded a retained snapshot's registry version
    _seed(1, OTHER)  # its source's active refresh
    for n, state in enumerate(("unconfirmed", "queued", "running", "publishing"), start=2):
        _seed(n, OTHER, state)
    activity.refuse = ("test.seed",)
    assert _seed(6, OTHER).get("_audit_pending")  # its activity record is not delivered yet
    activity.refuse = ()
    _seed(7, OTHER)  # a retained save decision points at it
    _seed(8, OTHER, "partial", unresolved_dollars=0.25)  # paid usage not yet resolved
    _seed(9, OTHER, reference=version)  # a later receipt of the same registry version: removable
    for n in range(10, 10 + PAGE):
        _seed(n, OTHER)
    native.SOURCE_STORE.create({"id": OTHER, "tenant": TENANT, "owner": OWNER, "plan": {"asset_id": "asset-2"}, "active_job": _job_id(1)},
                               action="test.seed", actor=OWNER)
    native.DECISION_STORE.create({"id": "KNO-" + "d" * 32, "tenant": TENANT, "owner": OWNER, "source": OTHER, "state": "confirmed",
                                  "result": {"id": OTHER, "active_job": {"id": _job_id(7)}}}, action="test.seed", actor=OWNER)
    monkeypatch.setattr(native, "MAX_JOBS", 10 + PAGE)

    job = _refresh(service, before["id"], "e" * 32)

    assert job["state"] == "queued"
    assert native.JOB_STORE.get(_job_id(9)) is None and _pruned(activity) == [_job_id(9)]
    assert all(native.JOB_STORE.get(_job_id(n)) is not None for n in [*range(9), *range(10, 10 + PAGE)])
    # Nothing else is removable: the full ledger refuses the next refresh exactly as before.
    service._finish(service.jobs.get(job["id"]), "completed", "done", items=[], changed=0, unchanged=0, removed=0, snapshot="KNV-" + "b" * 32)
    with pytest.raises(GovernedRecordCapacityError):
        _refresh(service, before["id"], "f" * 32)
    assert service.sources.get(before["id"])["active_job"] is None
    assert native.JOB_STORE.count(maximum=1000) == 10 + PAGE and _pruned(activity) == [_job_id(9)]


def test_each_source_keeps_its_newest_history_page(service, monkeypatch, activity):
    _, before = _prepared(service, 3600)
    for n in range(PAGE):  # the oldest receipts in the ledger, but all of them are OTHER's newest page
        _seed(n, OTHER)
    for n in range(PAGE, 2 * PAGE + 2):
        _seed(n, before["id"])
    monkeypatch.setattr(native, "MAX_JOBS", 2 * PAGE + 2)

    assert _refresh(service, before["id"], "e" * 32)["state"] == "queued"

    assert all(native.JOB_STORE.get(_job_id(n)) is not None for n in range(PAGE))
    assert sorted(_pruned(activity)) == [_job_id(PAGE), _job_id(PAGE + 1)]
    assert all(native.JOB_STORE.get(_job_id(n)) is not None for n in range(PAGE + 2, 2 * PAGE + 2))
    assert len(service.history(before["id"])["items"]) == PAGE
    assert native.JOB_STORE.count(maximum=1000) <= 2 * PAGE + 2


def test_a_receipt_is_not_removed_until_its_removal_is_on_the_activity_record(service, monkeypatch, activity):
    from maverick.governed_records import GovernedRecordCapacityError

    _, before = _prepared(service, 3600)
    for n in range(PAGE + 2):
        _seed(n, before["id"], items=[{"name": "Q3 board plan.docx", "state": "imported"}])
    monkeypatch.setattr(native, "MAX_JOBS", PAGE + 2)
    monkeypatch.setattr(native, "PRUNE_BATCH", 1, raising=False)
    activity.refuse = ("knowledge_source.refresh_pruned",)

    with pytest.raises(GovernedRecordCapacityError):
        _refresh(service, before["id"], "e" * 32)

    oldest = native.JOB_STORE.get(_job_id(0))
    assert oldest is not None and oldest["items"] and oldest.get("_audit_pending")
    assert native.JOB_STORE.get(_job_id(1)).get("_audit_pending") is None
    assert service.sources.get(before["id"])["active_job"] is None and _pruned(activity) == []
    # Once the activity record is delivered, that same receipt is removed and the refresh is admitted.
    activity.refuse = ()
    assert _refresh(service, before["id"], "f" * 32)["state"] == "queued"
    assert native.JOB_STORE.get(_job_id(0)) is None and native.JOB_STORE.get(_job_id(1)) is not None
    assert _pruned(activity) == [_job_id(0)]
