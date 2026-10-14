"""Adverse durability tests for flow run/resume operation idempotency."""
from __future__ import annotations

import time

import pytest


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import world_model
    from maverick_dashboard import automation_queue as aq

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr(aq, "_queue", None)
    monkeypatch.setattr(aq, "_worker", None)
    monkeypatch.setattr(aq, "_worker_threads", [])
    return aq


def _published_pin(flow_id: str, *, approval: bool = False) -> dict:
    from maverick.flow import store
    from maverick.flow.ir import Flow, FlowNode

    kind = "approval" if approval else "agent"
    node = FlowNode(id="gate", kind=kind)
    if approval:
        node.prompt = "continue?"
    else:
        node.brief = "work"
    saved = store.save_flow(Flow(
        id=flow_id,
        name=flow_id,
        start="gate",
        nodes={"gate": node},
    ))
    release = store.publish_flow(
        saved.id,
        expected_version=saved.version,
        expected_revision=saved.revision,
    )
    return {
        "definition_digest": release["definition_digest"],
        "release_digest": release["release_digest"],
        "release_id": release["release_id"],
        "definition_version": release["definition_version"],
        "definition_revision": release["definition_revision"],
        "subflow_digests": dict(release.get("subflow_digests") or {}),
    }


def _save_paused_run(
    run_id: str,
    *,
    flow_id: str = "resume-idem",
    status: str = "paused_approval",
    resume_at: float | None = None,
):
    from maverick.flow import store

    pin = _published_pin(flow_id, approval=status == "paused_approval")
    store.save_run(store.FlowRun(
        run_id=run_id,
        flow_id=flow_id,
        status=status,
        cursor="gate",
        resume_at=resume_at,
        human={"choices": [], "assignee": "", "form": []},
        **pin,
    ))
    return store.load_run(run_id)


def test_run_key_exact_replay_dedups_but_normalized_request_changes_conflict(
    monkeypatch, tmp_path,
):
    aq = _isolate(monkeypatch, tmp_path)
    pin = _published_pin("run-idem")
    kwargs = {
        "idem_key": "business-event-1",
        "definition_digest": pin["definition_digest"],
        "release_digest": pin["release_digest"],
        "release_id": pin["release_id"],
        "definition_version": pin["definition_version"],
        "definition_revision": pin["definition_revision"],
        "subflow_digests": pin["subflow_digests"],
    }

    run_id, duplicate = aq.enqueue_flow_run_once(
        "run-idem", {"b": 2, "a": 1}, origin="event:orders", **kwargs,
    )
    replay_id, replayed = aq.enqueue_flow_run_once(
        "run-idem", {"a": 1, "b": 2}, origin="event:orders", **kwargs,
    )

    assert duplicate is False
    assert replayed is True and replay_id == run_id
    for changed in (
        {"data": {"a": 9, "b": 2}, "origin": "event:orders", "dry_run": False},
        {"data": {"a": 1, "b": 2}, "origin": "event:refunds", "dry_run": False},
        {"data": {"a": 1, "b": 2}, "origin": "event:orders", "dry_run": True},
    ):
        with pytest.raises(aq.FlowOperationConflict, match="different request"):
            aq.enqueue_flow_run_once("run-idem", **changed, **kwargs)


def test_resume_exact_replay_returns_one_job_and_conflicting_payload_fails(
    monkeypatch, tmp_path,
):
    aq = _isolate(monkeypatch, tmp_path)
    _save_paused_run("paused-once")

    first = aq.enqueue_flow_resume(
        "resume-idem",
        "paused-once",
        decision="approved",
        decided_by="system:test",
        inputs={"note": "same"},
    )
    second = aq.enqueue_flow_resume(
        "resume-idem",
        "paused-once",
        decision="approved",
        decided_by="system:test",
        inputs={"note": "same"},
    )

    assert second == first
    jobs = [
        job for job in aq.queue().list(limit=100)
        if job.kind == aq.FLOW_RESUME_KIND
        and job.payload.get("run_id") == "paused-once"
    ]
    assert len(jobs) == 1
    for changed in (
        {"decision": "rejected", "inputs": {"note": "same"}},
        {"decision": "approved", "inputs": {"note": "changed"}},
        {
            "decision": "approved",
            "decided_by": "system:someone-else",
            "inputs": {"note": "same"},
        },
    ):
        with pytest.raises(aq.FlowOperationConflict, match="different resume"):
            changed.setdefault("decided_by", "system:test")
            aq.enqueue_flow_resume(
                "resume-idem",
                "paused-once",
                **changed,
            )
    from maverick.flow import store

    payload = jobs[0].payload
    claim = store.load_resume_claim(
        "paused-once", payload["pause_fingerprint"],
    )
    assert claim is not None and claim["job_id"] == first
    assert aq.queue().complete(first)
    assert aq.queue().purge(older_than_days=0) == 1
    assert aq.queue().get(first) is None
    advanced = store.load_run("paused-once")
    advanced.status = "completed"
    advanced.failure = None
    store.save_run(advanced)
    assert aq.enqueue_flow_resume(
        "resume-idem",
        "paused-once",
        decision="approved",
        decided_by="system:test",
        inputs={"note": "same"},
    ) == first
    assert aq.queue().list(limit=100) == []
    with pytest.raises(aq.FlowOperationConflict, match="advanced flow generation"):
        aq.enqueue_flow_resume(
            "resume-idem",
            "paused-once",
            decision="rejected",
            decided_by="system:test",
            inputs={"note": "same"},
        )


def test_resume_claim_repairs_crash_before_queue_and_survives_queue_reopen(
    monkeypatch, tmp_path,
):
    aq = _isolate(monkeypatch, tmp_path)
    _save_paused_run("resume-crash")
    real_enqueue = aq._enqueue_reviewed_job

    def _crash(*_args, **_kwargs):
        raise SystemExit("simulated process death before queue publication")

    monkeypatch.setattr(aq, "_enqueue_reviewed_job", _crash)
    with pytest.raises(SystemExit):
        aq.enqueue_flow_resume(
            "resume-idem",
            "resume-crash",
            decision="approved",
            decided_by="system:test",
        )
    monkeypatch.setattr(aq, "_enqueue_reviewed_job", real_enqueue)

    repaired = aq.enqueue_flow_resume(
        "resume-idem",
        "resume-crash",
        decision="approved",
        decided_by="system:test",
    )
    monkeypatch.setattr(aq, "_queue", None)
    reopened = aq.enqueue_flow_resume(
        "resume-idem",
        "resume-crash",
        decision="approved",
        decided_by="system:test",
    )

    assert reopened == repaired
    assert len([
        job for job in aq.queue().list(limit=100)
        if job.kind == aq.FLOW_RESUME_KIND
        and job.payload.get("run_id") == "resume-crash"
    ]) == 1


def test_resume_ambiguous_post_commit_error_reuses_and_binds_committed_job(
    monkeypatch, tmp_path,
):
    aq = _isolate(monkeypatch, tmp_path)
    from maverick.flow import store

    _save_paused_run("resume-ambiguous")
    real_enqueue = aq._enqueue_reviewed_job

    def _commit_then_raise(*args, **kwargs):
        real_enqueue(*args, **kwargs)
        raise RuntimeError("queue acknowledgement was lost")

    monkeypatch.setattr(aq, "_enqueue_reviewed_job", _commit_then_raise)
    with pytest.raises(RuntimeError, match="acknowledgement"):
        aq.enqueue_flow_resume(
            "resume-idem",
            "resume-ambiguous",
            decision="approved",
            decided_by="system:test",
        )
    monkeypatch.setattr(aq, "_enqueue_reviewed_job", real_enqueue)

    job_id = aq.enqueue_flow_resume(
        "resume-idem",
        "resume-ambiguous",
        decision="approved",
        decided_by="system:test",
    )
    job = aq.queue().get(job_id)
    assert job is not None
    claim = store.load_resume_claim(
        "resume-ambiguous", job.payload["pause_fingerprint"],
    )
    assert claim is not None and claim["job_id"] == job_id
    assert len([
        queued for queued in aq.queue().list(limit=100)
        if queued.kind == aq.FLOW_RESUME_KIND
        and queued.payload.get("run_id") == "resume-ambiguous"
    ]) == 1


def test_scheduled_delay_replay_ignores_delivery_time_but_not_resume_intent(
    monkeypatch, tmp_path,
):
    aq = _isolate(monkeypatch, tmp_path)
    due = time.time() + 600
    _save_paused_run(
        "scheduled-delay",
        flow_id="delay-idem",
        status="paused_delay",
        resume_at=due,
    )

    scheduled = aq.enqueue_flow_resume(
        "delay-idem", "scheduled-delay", run_at=due,
    )
    sweep_replay = aq.enqueue_flow_resume("delay-idem", "scheduled-delay")

    assert sweep_replay == scheduled
    job = aq.queue().get(scheduled)
    assert job is not None and job.run_at == due
    assert len([
        queued for queued in aq.queue().list(limit=100)
        if queued.kind == aq.FLOW_RESUME_KIND
        and queued.payload.get("run_id") == "scheduled-delay"
    ]) == 1


def test_human_decision_supersedes_only_a_not_yet_due_approval_expiry(
    monkeypatch, tmp_path,
):
    aq = _isolate(monkeypatch, tmp_path)
    expires_at = time.time() + 600
    _save_paused_run(
        "approval-expiry",
        flow_id="expiry-idem",
        resume_at=expires_at,
    )

    expiry_job = aq.enqueue_flow_resume(
        "expiry-idem",
        "approval-expiry",
        expired=True,
        run_at=expires_at,
    )
    human_job = aq.enqueue_flow_resume(
        "expiry-idem",
        "approval-expiry",
        decision="approved",
        decided_by="system:reviewer",
    )
    replay = aq.enqueue_flow_resume(
        "expiry-idem",
        "approval-expiry",
        decision="approved",
        decided_by="system:reviewer",
    )

    assert human_job != expiry_job
    assert aq.queue().get(expiry_job) is None
    assert replay == human_job
    with pytest.raises(aq.FlowOperationConflict, match="different resume"):
        aq.enqueue_flow_resume(
            "expiry-idem",
            "approval-expiry",
            expired=True,
            run_at=expires_at,
        )


def test_failed_generation_binds_from_failure_and_owner(monkeypatch, tmp_path):
    aq = _isolate(monkeypatch, tmp_path)
    from maverick.flow import store

    pin = _published_pin("failure-idem")
    store.save_run(store.FlowRun(
        run_id="failed-once",
        flow_id="failure-idem",
        status="failed",
        cursor="gate",
        owner="user:alice",
        **pin,
    ))

    first = aq.enqueue_flow_resume(
        "failure-idem",
        "failed-once",
        owner="user:alice",
        from_failure=True,
    )
    assert aq.enqueue_flow_resume(
        "failure-idem",
        "failed-once",
        owner="user:alice",
        from_failure=True,
    ) == first
    with pytest.raises(aq.FlowOperationConflict, match="owner"):
        aq.enqueue_flow_resume(
            "failure-idem",
            "failed-once",
            owner="user:bob",
            from_failure=True,
        )
    with pytest.raises(aq.FlowOperationConflict, match="different resume request"):
        aq.enqueue_flow_resume(
            "failure-idem",
            "failed-once",
            owner="user:alice",
            from_failure=False,
        )
