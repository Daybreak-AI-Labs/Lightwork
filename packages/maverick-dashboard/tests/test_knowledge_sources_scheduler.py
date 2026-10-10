"""The saved Knowledge sources worker: refused queued refreshes are withheld,
schedules are checked every 30 seconds, and a queued refresh wakes it."""
from __future__ import annotations

import time
from contextlib import nullcontext

import pytest
from maverick_dashboard import knowledge_sources_scheduler as sched


def test_a_queued_refresh_refused_before_it_starts_is_withheld(monkeypatch):
    from maverick import connections
    from maverick import knowledge_sources as native

    job = {"id": "KNJ-1", "state": "queued", "source": "KNS-1"}
    row = {"id": "KNS-1", "tenant": "acme", "owner": "user:owner", "active_job": "KNJ-1"}
    withheld = []

    class Refusing:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, identifier):
            raise PermissionError("This refresh's current source permit is unavailable or expired.")

    monkeypatch.setattr(sched, "current_tenant_id_strict", lambda: "acme")
    monkeypatch.setattr(native, "enabled", lambda: True)
    monkeypatch.setattr(native.SOURCE_STORE, "list", lambda limit: [row])
    monkeypatch.setattr(native.JOB_STORE, "get", lambda identifier: dict(job))
    monkeypatch.setattr(native, "KnowledgeSources", Refusing)
    Refusing.withhold_queued = staticmethod(lambda queued, exc: withheld.append((queued["id"], type(exc))))
    monkeypatch.setattr(sched.research_workspace_api, "_registry_service", lambda: None)
    monkeypatch.setattr(connections, "bind_principal", lambda owner: nullcontext())
    sched._workspace_cycle()
    assert withheld == [("KNJ-1", PermissionError)]


def test_schedules_are_checked_every_30_seconds_and_a_queued_refresh_wakes_the_worker(monkeypatch):
    cycles = []
    monkeypatch.setattr(sched, "cycle", lambda: cycles.append(time.monotonic()))
    assert sched._POLL_SECONDS == 30.0
    assert sched.start_knowledge_sources_scheduler() is True
    try:
        deadline = time.monotonic() + 5
        while not cycles and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(cycles) == 1
        sched.wake()
        deadline = time.monotonic() + 5
        while len(cycles) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(cycles) == 2 and cycles[1] - cycles[0] < 5
    finally:
        started = time.monotonic()
        assert sched.stop_knowledge_sources_scheduler(timeout=5) is True
        assert time.monotonic() - started < 5


@pytest.fixture(autouse=True)
def _stopped():
    yield
    sched.stop_knowledge_sources_scheduler(timeout=5)
