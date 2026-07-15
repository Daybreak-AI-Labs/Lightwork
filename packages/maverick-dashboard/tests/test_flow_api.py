"""Dashboard flow surface: CRUD + validation, the durable run/resume path through
the automation queue, owner-scoped runs, and self-rewrite proposals. Flow engine
gated (off by default)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _client():
    from maverick_dashboard.app import app
    return TestClient(app, headers={"Origin": "http://testserver"})


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")


def _reset_aq(monkeypatch, aq):
    monkeypatch.setattr(aq, "_queue", None)
    monkeypatch.setattr(aq, "_worker", None)
    monkeypatch.setattr(aq, "_worker_threads", [])


def _drain(aq):
    # A worker with the flow handlers registered (as automation_queue.start would).
    from maverick.worker import Worker
    w = Worker(queue=aq.queue())
    w.register(aq.FLOW_RUN_KIND, aq._handle_flow_run)
    w.register(aq.FLOW_RESUME_KIND, aq._handle_flow_resume)
    w.drain()


_FLOW = {
    "id": "demo", "name": "Demo", "start": "a",
    "nodes": [
        {"id": "a", "kind": "agent", "brief": "assess {{order}}", "next": "b", "output": "r"},
        {"id": "b", "kind": "approval", "prompt": "ship it?", "next": "c"},
        {"id": "c", "kind": "action", "tool": "notify", "params": {"m": "{{r}}"}},
    ],
}


def test_flows_gated_off_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_FLOWS", raising=False)
    monkeypatch.setattr("maverick.config.get_flows", lambda: {"enable": False})
    assert _client().get("/api/v1/flows").status_code == 403


_INPUT_FLOW = {
    "id": "typed", "name": "Typed", "start": "a",
    "nodes": [{"id": "a", "kind": "agent", "brief": "process {{amount}}"}],
    "inputs": [{"key": "amount", "type": "number", "required": True}],
}


def test_run_rejects_a_missing_required_input(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    c.post("/api/v1/flows", json={"flow": _INPUT_FLOW})
    r = c.post("/api/v1/flows/typed/run", json={"data": {}})
    assert r.status_code == 400 and "amount" in r.json()["detail"]


def test_run_coerces_a_typed_input(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    c.post("/api/v1/flows", json={"flow": _INPUT_FLOW})
    r = c.post("/api/v1/flows/typed/run", json={"data": {"amount": "42"}})
    assert r.status_code == 200
    from maverick.flow import store
    assert store.load_run(r.json()["run_id"]).data["amount"] == 42.0   # coerced to number


def test_run_idempotency_key_dedups_a_repeat_submit(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    c.post("/api/v1/flows", json={"flow": _FLOW})
    a = c.post("/api/v1/flows/demo/run", json={"data": {"order": "X"}, "idempotency_key": "k1"}).json()
    b = c.post("/api/v1/flows/demo/run", json={"data": {"order": "X"}, "idempotency_key": "k1"}).json()
    assert a["run_id"] == b["run_id"] and b.get("deduplicated") is True


def test_idempotency_key_does_not_alias_a_recreated_flow_generation(
    monkeypatch, tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import automation_queue as aq

    _reset_aq(monkeypatch, aq)
    c = _client()
    assert c.post("/api/v1/flows", json={"flow": _FLOW}).status_code == 201
    first, duplicate = aq.enqueue_flow_run_once(
        "demo", {}, idem_key="same-event")
    assert duplicate is False
    old = store.load_flow("demo")
    assert old is not None
    assert store.delete_flow(
        "demo", expected_version=old.version, expected_revision=old.revision)
    assert c.post("/api/v1/flows", json={"flow": _FLOW}).status_code == 201

    second, duplicate = aq.enqueue_flow_run_once(
        "demo", {}, idem_key="same-event")

    assert duplicate is False
    assert second != first
    assert store.load_run(second).definition_revision != store.load_run(first).definition_revision


def test_save_validate_get_delete(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    assert c.post("/api/v1/flows", json={"flow": _FLOW}).status_code == 201
    assert c.get("/api/v1/flows").json()["flows"][0]["id"] == "demo"
    assert len(c.get("/api/v1/flows/demo").json()["nodes"]) == 3
    assert c.delete("/api/v1/flows/demo").status_code == 204
    assert c.get("/api/v1/flows/demo").status_code == 404


def test_save_uses_client_cas_and_rejects_a_stale_editor(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    created = c.post("/api/v1/flows", json={"flow": _FLOW})
    assert created.status_code == 201
    first = c.get("/api/v1/flows/demo").json()

    winner = {**first, "name": "winner"}
    saved = c.post("/api/v1/flows", json={"flow": winner})
    assert saved.status_code == 201 and saved.json()["version"] == 2

    stale = {**first, "name": "stale overwrite"}
    rejected = c.post("/api/v1/flows", json={"flow": stale})
    assert rejected.status_code == 409
    assert c.get("/api/v1/flows/demo").json()["name"] == "winner"


def test_save_generation_token_closes_delete_recreate_aba(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    assert c.post("/api/v1/flows", json={"flow": _FLOW}).status_code == 201
    stale = c.get("/api/v1/flows/demo").json()
    assert c.delete("/api/v1/flows/demo").status_code == 204
    assert c.post("/api/v1/flows", json={"flow": _FLOW}).status_code == 201

    stale["name"] = "ABA overwrite"
    assert c.post("/api/v1/flows", json={"flow": stale}).status_code == 409
    assert c.get("/api/v1/flows/demo").json()["name"] == "Demo"


def test_new_flow_version_is_store_owned(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    hostile = {**_FLOW, "version": -99, "revision": "attacker-selected"}
    r = _client().post("/api/v1/flows", json={"flow": hostile})
    assert r.status_code == 201
    saved = _client().get("/api/v1/flows/demo").json()
    assert saved["version"] == 1
    assert saved["revision"] and saved["revision"] != "attacker-selected"


def test_invalid_flow_rejected(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    bad = {"id": "b", "name": "x", "start": "z", "nodes": [{"id": "a", "kind": "agent"}]}
    r = _client().post("/api/v1/flows", json={"flow": bad})
    assert r.status_code == 400 and "invalid flow" in r.json()["detail"]


def test_run_drains_through_queue_and_pauses_on_approval(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import execution
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    # fake live runners so no LLM/tool is needed
    def _fake_agent(world, **k):
        return lambda brief, d: (f"ran:{brief}", 1.0)

    def _fake_action(world, sandbox=None, **kwargs):
        return lambda t, p, d: ("sent", 1.0)

    monkeypatch.setattr(execution, "default_agent_runner", _fake_agent)
    monkeypatch.setattr(execution, "default_action_runner", _fake_action)
    c = _client()
    c.post("/api/v1/flows", json={"flow": _FLOW})
    started = c.post("/api/v1/flows/demo/run", json={"data": {"order": "X-9"}}).json()
    run_id = started["run_id"]
    assert started["status"] == "queued"
    # drain the queued flow_run job
    _drain(aq)
    run = c.get(f"/api/v1/flows/runs/{run_id}").json()
    assert run["status"] == "paused_approval" and run["cursor"] == "b"
    assert run["data"]["r"] == "ran:assess X-9"     # agent output threaded through
    # resume approved -> drains to completion, action fires
    c.post(f"/api/v1/flows/runs/{run_id}/resume", json={"decision": "approved"})
    _drain(aq)
    done = c.get(f"/api/v1/flows/runs/{run_id}").json()
    assert done["status"] == "completed"


def test_manual_run_and_resume_jobs_capture_the_active_tenant(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick.flow.ir import Flow, FlowNode
    from maverick.paths import reset_tenant, set_tenant
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)

    token = set_tenant("tenant-a")
    try:
        store.save_flow(Flow(
            id="tenant-manual", name="tenant manual", start="gate", nodes={
                "gate": FlowNode(id="gate", kind="approval", prompt="approve?"),
            }))
        run_id, deduplicated = aq.enqueue_flow_run_once(
            "tenant-manual", {"value": 1}, origin="manual")
        aq.enqueue_flow_resume("tenant-manual", run_id, "approved")
        pending = aq.queue().list(status="pending")
        run_job = next(j for j in pending if j.kind == aq.FLOW_RUN_KIND)
        resume_job = next(j for j in pending if j.kind == aq.FLOW_RESUME_KIND)
        placeholder = store.load_run(run_id)
    finally:
        reset_tenant(token)

    assert deduplicated is False
    assert run_job.payload["tenant"] == "tenant-a"
    assert resume_job.payload["tenant"] == "tenant-a"
    assert placeholder is not None and placeholder.definition_digest


def test_durable_flow_identity_survives_queueing_and_applies_revocation_floor(
    monkeypatch, tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import execution, store
    from maverick.flow.ir import Flow, FlowNode
    from maverick_dashboard import automation_queue as aq

    _reset_aq(monkeypatch, aq)
    store.save_flow(Flow(
        id="identity", name="identity", start="a", owner="user:alice",
        nodes={"a": FlowNode(id="a", kind="agent", brief="work")},
    ))
    grants = [frozenset({"finance", "legal"})]
    monkeypatch.setattr(
        "maverick.suite_grants.granted_suites", lambda principal: grants[0])
    run_id = aq.enqueue_flow_run(
        "identity", {}, owner="user:alice", channel="api", user_id="alice")
    run = store.load_run(run_id)
    assert run is not None
    assert run.execution_channel == "api"
    assert run.execution_user_id == "alice"
    assert run.allowed_suites == ["finance", "legal"]

    # A grant narrowed while the job waited. Dispatch intersects current policy
    # with the enqueue-time grant; it never widens from stale queue metadata.
    grants[0] = frozenset({"finance"})
    agent_kwargs = {}
    action_kwargs = {}
    monkeypatch.setattr(
        execution,
        "default_agent_runner",
        lambda world, **kwargs: agent_kwargs.update(kwargs) or (lambda *a: ("", 1.0)),
    )
    monkeypatch.setattr(
        execution,
        "default_action_runner",
        lambda world, sandbox=None, **kwargs: (
            action_kwargs.update(kwargs) or (lambda *a: ("", 1.0))
        ),
    )
    aq._flow_runners(run)

    assert agent_kwargs["channel"] == "api"
    assert agent_kwargs["user_id"] == "alice"
    assert agent_kwargs["allowed_suites"] == frozenset({"finance"})
    assert agent_kwargs["concurrency_principal"] == "user:alice"
    assert action_kwargs == {"channel": "api", "user_id": "alice"}


def test_queued_flow_fails_closed_when_operate_role_is_revoked(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from types import SimpleNamespace

    from maverick.flow import execution, store
    from maverick.flow.ir import Flow, FlowNode
    from maverick_dashboard import automation_queue as aq

    _reset_aq(monkeypatch, aq)
    store.save_flow(Flow(
        id="revoked", name="revoked", start="a", owner="user:alice",
        nodes={"a": FlowNode(id="a", kind="agent", brief="must not run")},
    ))
    run_id = aq.enqueue_flow_run(
        "revoked", {}, owner="user:alice", channel="api", user_id="alice")
    monkeypatch.setattr(
        "maverick_dashboard.auth.role_for_principal", lambda principal: "viewer")
    monkeypatch.setattr(
        execution,
        "default_agent_runner",
        lambda *args, **kwargs: pytest.fail("revoked run built a live runner"),
    )
    job = next(
        job for job in aq.queue().list(status="pending")
        if job.kind == aq.FLOW_RUN_KIND and job.payload.get("run_id") == run_id
    )

    aq._handle_flow_run(SimpleNamespace(payload=job.payload))

    run = store.load_run(run_id)
    assert run is not None
    assert run.status == "failed"
    assert "authorization was revoked" in run.error


@pytest.mark.parametrize("policy_failure", ["inactive", "unreadable"])
def test_queued_flow_fails_closed_on_scim_lifecycle_failure(
    monkeypatch, tmp_path, policy_failure,
):
    _isolate(monkeypatch, tmp_path)
    from types import SimpleNamespace

    from maverick.flow import execution, store
    from maverick.flow.ir import Flow, FlowNode
    from maverick_dashboard import automation_queue as aq

    _reset_aq(monkeypatch, aq)
    store.save_flow(Flow(
        id="scim-revoked",
        name="scim-revoked",
        start="a",
        owner="user:alice",
        nodes={"a": FlowNode(id="a", kind="agent", brief="must not run")},
    ))
    run_id = aq.enqueue_flow_run(
        "scim-revoked",
        {},
        owner="user:alice",
        channel="api",
        user_id="alice",
    )
    if policy_failure == "inactive":
        monkeypatch.setattr(
            "maverick_dashboard.scim_groups.active_for_principal", lambda _p: False
        )
    else:
        monkeypatch.setattr(
            "maverick_dashboard.scim_groups.active_for_principal",
            lambda _p: (_ for _ in ()).throw(OSError("policy store unavailable")),
        )
    monkeypatch.setattr(
        execution,
        "default_agent_runner",
        lambda *args, **kwargs: pytest.fail("revoked run built a live runner"),
    )
    job = next(
        job for job in aq.queue().list(status="pending")
        if job.kind == aq.FLOW_RUN_KIND and job.payload.get("run_id") == run_id
    )

    aq._handle_flow_run(SimpleNamespace(payload=job.payload))

    run = store.load_run(run_id)
    assert run is not None and run.status == "failed"
    assert "authorization was revoked" in run.error


def test_queued_run_outbox_repairs_a_missing_job_without_losing_dry_run(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import automation_queue as aq

    _reset_aq(monkeypatch, aq)
    assert _client().post("/api/v1/flows", json={"flow": _FLOW}).status_code == 201
    digest, version, subflows = store.snapshot_current_flow_bundle("demo")
    store.save_run(store.FlowRun(
        run_id="orphan", flow_id="demo", status="queued",
        input_data={"order": "X"}, data={"order": "X"}, dry_run=True,
        definition_digest=digest, definition_version=version,
        subflow_digests=subflows,
    ))

    aq._repair_queued_flow_outbox()
    jobs = [j for j in aq._pending(aq.FLOW_RUN_KIND)
            if j.payload.get("run_id") == "orphan"]
    assert len(jobs) == 1
    assert jobs[0].payload["dry_run"] is True
    # Idempotent startup reconciliation never publishes a duplicate.
    aq._repair_queued_flow_outbox()
    assert len([j for j in aq._pending(aq.FLOW_RUN_KIND)
                if j.payload.get("run_id") == "orphan"]) == 1


def test_legacy_run_without_immutable_plan_fails_closed(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import automation_queue as aq

    _reset_aq(monkeypatch, aq)
    assert _client().post("/api/v1/flows", json={"flow": _FLOW}).status_code == 201
    store.save_run(store.FlowRun(
        run_id="legacy-unpinned", flow_id="demo", status="queued",
    ))
    aq._repair_queued_flow_outbox()
    run = store.load_run("legacy-unpinned")
    assert run.status == "failed"
    assert "replacement run" in run.error
    assert not any(
        job.payload.get("run_id") == run.run_id
        for job in aq._pending(aq.FLOW_RUN_KIND)
    )


def test_idempotency_claim_survives_crash_before_queue_publish(monkeypatch, tmp_path):
    """A process death in the JSON-to-SQLite gap repairs, never duplicates."""
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import automation_queue as aq

    _reset_aq(monkeypatch, aq)
    assert _client().post("/api/v1/flows", json={"flow": _FLOW}).status_code == 201
    q = aq.queue()
    real_enqueue = q.enqueue

    def _crash(*args, **kwargs):
        raise SystemExit("simulated process death")

    monkeypatch.setattr(q, "enqueue", _crash)
    with pytest.raises(SystemExit):
        aq.enqueue_flow_run_once("demo", {"order": "X"}, idem_key="delivery-1")

    reserved = store.find_run_by_idem("demo", "delivery-1", within=0)
    assert reserved is not None and reserved.status == "queued"
    monkeypatch.setattr(q, "enqueue", real_enqueue)

    run_id, deduplicated = aq.enqueue_flow_run_once(
        "demo", {"order": "X"}, idem_key="delivery-1")
    assert deduplicated is True and run_id == reserved.run_id
    jobs = [j for j in aq._pending(aq.FLOW_RUN_KIND)
            if j.payload.get("run_id") == reserved.run_id]
    assert len(jobs) == 1


def test_ambiguous_post_commit_enqueue_error_never_duplicates_run(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import automation_queue as aq

    _reset_aq(monkeypatch, aq)
    assert _client().post("/api/v1/flows", json={"flow": _FLOW}).status_code == 201
    q = aq.queue()
    real_enqueue = q.enqueue

    def _commit_then_raise(*args, **kwargs):
        real_enqueue(*args, **kwargs)
        raise RuntimeError("driver lost acknowledgement after commit")

    monkeypatch.setattr(q, "enqueue", _commit_then_raise)
    with pytest.raises(RuntimeError, match="lost acknowledgement"):
        aq.enqueue_flow_run_once("demo", {}, idem_key="delivery-ambiguous")

    reserved = store.find_run_by_idem("demo", "delivery-ambiguous", within=0)
    assert reserved is not None and reserved.status == "queued"
    monkeypatch.setattr(q, "enqueue", real_enqueue)
    run_id, deduplicated = aq.enqueue_flow_run_once(
        "demo", {}, idem_key="delivery-ambiguous")
    assert deduplicated is True and run_id == reserved.run_id
    jobs = [j for j in aq._pending(aq.FLOW_RUN_KIND)
            if j.payload.get("run_id") == reserved.run_id]
    assert len(jobs) == 1


class _Job:
    def __init__(self, payload):
        self.payload = payload


def _singleton_flow(mc=1):
    from maverick.flow import store
    from maverick.flow.ir import Flow, FlowNode
    flow = Flow(id="s", name="S", start="a", max_concurrent=mc,
                nodes={"a": FlowNode(id="a", kind="agent", brief="go")})
    store.save_flow(flow)
    return store.snapshot_flow_bundle(flow)[:2]


def test_singleton_defers_a_run_while_another_is_in_flight(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    digest, version = _singleton_flow(mc=1)
    store.save_run(store.FlowRun(
        run_id="r_active", flow_id="s", status="running",
        definition_digest=digest, definition_version=version))
    store.save_run(store.FlowRun(
        run_id="r_new", flow_id="s", status="queued",
        definition_digest=digest, definition_version=version))
    aq._handle_flow_run(_Job({"flow_id": "s", "run_id": "r_new"}))
    # the slot is taken -> the new run is NOT executed, it's left queued (deferred)
    assert store.load_run("r_new").status == "queued"


def test_singleton_defers_while_another_run_waits_for_an_event(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    digest, version = _singleton_flow(mc=1)
    store.save_run(store.FlowRun(
        run_id="r_waiting", flow_id="s", status="paused_event", cursor="wait",
        definition_digest=digest, definition_version=version))
    store.save_run(store.FlowRun(
        run_id="r_new", flow_id="s", status="queued",
        definition_digest=digest, definition_version=version))

    aq._handle_flow_run(_Job({"flow_id": "s", "run_id": "r_new"}))

    # A wait-event run is live work and continues to own the singleton slot.
    assert store.load_run("r_new").status == "queued"


def test_singleton_defers_when_active_run_is_older_than_recent_window(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    digest, version = _singleton_flow(mc=1)
    now = [1_000.0]
    monkeypatch.setattr(store.time, "time", lambda: now[0])
    store.save_run(store.FlowRun(
        run_id="r_active", flow_id="s", status="running",
        definition_digest=digest, definition_version=version))
    now[0] = 2_000.0
    for i in range(201):
        store.save_run(store.FlowRun(run_id=f"r_done_{i}", flow_id="s", status="completed"))
    store.save_run(store.FlowRun(
        run_id="r_new", flow_id="s", status="queued",
        definition_digest=digest, definition_version=version))

    assert all(r.run_id != "r_active" for r in store.list_runs(flow_id="s", limit=200))

    aq._handle_flow_run(_Job({"flow_id": "s", "run_id": "r_new"}))

    # Even if many newer runs push the active run out of the recent list window,
    # the singleton cap still sees it and leaves the new run queued for retry.
    assert store.load_run("r_new").status == "queued"


def test_singleton_runs_when_the_slot_is_free(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import execution, store
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    monkeypatch.setattr(execution, "default_agent_runner",
                        lambda world, **k: (lambda brief, d, **kw: ("ok", 1.0)))
    monkeypatch.setattr(execution, "default_action_runner",
                        lambda world, sandbox=None, **kwargs: (lambda t, p, d: ("", None)))
    digest, version = _singleton_flow(mc=1)
    store.save_run(store.FlowRun(
        run_id="r_new", flow_id="s", status="queued",
        definition_digest=digest, definition_version=version))
    aq._handle_flow_run(_Job({"flow_id": "s", "run_id": "r_new"}))
    assert store.load_run("r_new").status == "completed"   # no sibling -> claimed + ran


def test_singleton_skips_once_the_defer_budget_is_spent(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    digest, version = _singleton_flow(mc=1)
    store.save_run(store.FlowRun(
        run_id="r_active", flow_id="s", status="paused_approval",
        definition_digest=digest, definition_version=version))
    store.save_run(store.FlowRun(
        run_id="r_new", flow_id="s", status="queued",
        definition_digest=digest, definition_version=version))
    # a run that has already been deferred to the limit is dropped, not re-queued forever
    aq._handle_flow_run(_Job({"flow_id": "s", "run_id": "r_new",
                              "_slot_defers": aq._SINGLETON_MAX_DEFERS}))
    assert store.load_run("r_new").status == "skipped_concurrency"


def test_unlimited_flow_ignores_the_slot_guard(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import execution, store
    from maverick.flow.ir import Flow, FlowNode
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    monkeypatch.setattr(execution, "default_agent_runner",
                        lambda world, **k: (lambda brief, d, **kw: ("ok", 1.0)))
    monkeypatch.setattr(execution, "default_action_runner",
                        lambda world, sandbox=None, **kwargs: (lambda t, p, d: ("", None)))
    flow = Flow(id="u", name="U", start="a", max_concurrent=0,
                nodes={"a": FlowNode(id="a", kind="agent", brief="go")})
    store.save_flow(flow)
    digest, version, _ = store.snapshot_flow_bundle(flow)
    store.save_run(store.FlowRun(
        run_id="ua", flow_id="u", status="running",
        definition_digest=digest, definition_version=version))   # sibling
    store.save_run(store.FlowRun(
        run_id="ub", flow_id="u", status="queued",
        definition_digest=digest, definition_version=version))
    aq._handle_flow_run(_Job({"flow_id": "u", "run_id": "ub"}))
    assert store.load_run("ub").status == "completed"   # 0 = unlimited -> runs anyway


def test_runs_are_owner_scoped(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import api
    store.save_run(store.FlowRun(run_id="r1", flow_id="demo", status="completed", owner="user:alice"))
    store.save_run(store.FlowRun(run_id="r2", flow_id="demo", status="completed", owner="user:bob"))
    monkeypatch.setattr(api, "goal_owner_filter", lambda request: "user:alice")
    ids = {r["run_id"] for r in _client().get("/api/v1/flows/runs").json()["runs"]}
    assert ids == {"r1"}


def test_runs_list_carries_origin_and_cost(monkeypatch, tmp_path):
    # The list endpoint surfaces how each run was triggered and its $ cost so the
    # automations UI can label cron/event/retry runs at a glance (not just manual).
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    store.save_run(store.FlowRun(run_id="r1", flow_id="demo", status="completed",
                                 origin="cron:demo", cost_dollars=0.42))
    row = _client().get("/api/v1/flows/runs").json()["runs"][0]
    assert row["origin"] == "cron:demo" and row["cost_dollars"] == 0.42


def test_resume_of_non_paused_run_is_409(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    store.save_run(store.FlowRun(run_id="rc", flow_id="demo", status="completed"))
    r = _client().post("/api/v1/flows/runs/rc/resume", json={"decision": "approved"})
    assert r.status_code == 409


def test_delay_sweep_reenqueues_only_due_runs(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    store.save_run(store.FlowRun(run_id="d1", flow_id="demo",
                                 status="paused_delay", resume_at=1.0))          # due
    store.save_run(store.FlowRun(run_id="d2", flow_id="demo",
                                 status="paused_delay", resume_at=9e18))         # not yet
    aq._handle_flow_sweep(None)
    jobs = [j for j in aq.queue().list(status="pending") if j.kind == aq.FLOW_RESUME_KIND]
    assert {j.payload["run_id"] for j in jobs} == {"d1"}


def test_sweep_rejects_expired_approvals_but_never_paused_events(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    store.save_run(store.FlowRun(run_id="ap1", flow_id="demo",
                                 status="paused_approval", resume_at=1.0))     # expired
    store.save_run(store.FlowRun(run_id="ap2", flow_id="demo",
                                 status="paused_approval", resume_at=9e18))    # still open
    store.save_run(store.FlowRun(run_id="ev1", flow_id="demo",
                                 status="paused_event", resume_at=1.0))        # event-only
    aq._handle_flow_sweep(None)
    jobs = [j for j in aq.queue().list(status="pending") if j.kind == aq.FLOW_RESUME_KIND]
    assert {j.payload["run_id"] for j in jobs} == {"ap1"}
    # expiry rides the out-of-band flag -> the runner routes on_expire or rejects
    assert jobs[0].payload["expired"] is True and "decision" not in jobs[0].payload


def test_sweep_visits_named_tenants_and_preserves_resume_namespace(
    monkeypatch, tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.setattr("maverick.client.client_id", lambda: None)
    from maverick.flow import store
    from maverick.paths import current_tenant_id, reset_tenant, set_tenant
    from maverick.tenant import registry
    from maverick_dashboard import automation_queue as aq

    _reset_aq(monkeypatch, aq)
    registry.create_tenant("acme")
    store.save_run(store.FlowRun(
        run_id="shared-delay",
        flow_id="demo",
        status="paused_delay",
        resume_at=1.0,
    ))
    token = set_tenant("acme")
    try:
        store.save_run(store.FlowRun(
            run_id="private-delay",
            flow_id="demo",
            status="paused_delay",
            resume_at=1.0,
        ))
        store.save_run(store.FlowRun(
            run_id="private-approval",
            flow_id="demo",
            status="paused_approval",
            resume_at=1.0,
        ))
    finally:
        reset_tenant(token)

    ambient = set_tenant("ambient-poison")
    try:
        aq._handle_flow_sweep(None)
        assert current_tenant_id() == "ambient-poison"
    finally:
        reset_tenant(ambient)

    jobs = {
        job.payload["run_id"]: job.payload
        for job in aq.queue().list(status="pending")
        if job.kind == aq.FLOW_RESUME_KIND
    }
    assert "tenant" not in jobs["shared-delay"]
    assert jobs["private-delay"]["tenant"] == "acme"
    assert jobs["private-approval"]["tenant"] == "acme"
    assert jobs["private-approval"]["expired"] is True


def test_retry_of_paused_event_run_is_409_not_a_duplicate(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    c = _client()
    assert c.post("/api/v1/flows", json={"flow": _FLOW}).status_code == 201
    store.save_run(store.FlowRun(run_id="pe", flow_id="demo", status="paused_event",
                                 cursor="w"))
    r = c.post("/api/v1/flows/runs/pe/retry")
    assert r.status_code == 409 and "still active" in r.json()["detail"]
    # no duplicate flow_run job was enqueued
    jobs = [j for j in aq.queue().list(status="pending") if j.kind == aq.FLOW_RUN_KIND]
    assert jobs == []


def test_paused_event_run_resumes_via_api_with_event_data(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import execution
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    monkeypatch.setattr(execution, "default_agent_runner",
                        lambda world, **k: lambda brief, d: (f"ran:{brief}", 1.0))
    monkeypatch.setattr(execution, "default_action_runner",
                        lambda world, sandbox=None, **kwargs: lambda t, p, d: ("", 1.0))
    c = _client()
    flow = {"id": "fev", "name": "f", "start": "w", "nodes": [
        {"id": "w", "kind": "wait_event", "prompt": "cb", "next": "a"},
        {"id": "a", "kind": "agent", "brief": "handle {{payload}}", "output": "r"},
    ]}
    assert c.post("/api/v1/flows", json={"flow": flow}).status_code == 201
    run_id = c.post("/api/v1/flows/fev/run", json={"data": {}}).json()["run_id"]
    _drain(aq)
    assert c.get(f"/api/v1/flows/runs/{run_id}").json()["status"] == "paused_event"
    r = c.post(f"/api/v1/flows/runs/{run_id}/resume",
               json={"inputs": {"payload": "evt-7"}})
    assert r.status_code == 200
    _drain(aq)
    done = c.get(f"/api/v1/flows/runs/{run_id}").json()
    assert done["status"] == "completed" and done["data"]["r"] == "ran:handle evt-7"


def test_choice_approval_verdict_flows_through_resume(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import execution
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    monkeypatch.setattr(execution, "default_agent_runner",
                        lambda world, **k: lambda brief, d: (f"ran:{brief}", 1.0))
    monkeypatch.setattr(execution, "default_action_runner",
                        lambda world, sandbox=None, **kwargs: lambda t, p, d: ("", 1.0))
    c = _client()
    flow = {"id": "fch", "name": "f", "start": "a", "nodes": [
        {"id": "a", "kind": "approval", "prompt": "ship or hold?",
         "choices": ["ship", "hold"], "output": "verdict", "next": "s"},
        {"id": "s", "kind": "switch", "condition": "verdict",
         "cases": [{"value": "ship", "to": "go"}], "next": "no"},
        {"id": "go", "kind": "agent", "brief": "shipping", "output": "r"},
        {"id": "no", "kind": "agent", "brief": "holding", "output": "r"},
    ]}
    assert c.post("/api/v1/flows", json={"flow": flow}).status_code == 201
    run_id = c.post("/api/v1/flows/fch/run", json={"data": {}}).json()["run_id"]
    _drain(aq)
    r = c.post(f"/api/v1/flows/runs/{run_id}/resume", json={"decision": "ship"})
    assert r.status_code == 200, r.text
    _drain(aq)
    done = c.get(f"/api/v1/flows/runs/{run_id}").json()
    assert done["status"] == "completed"
    assert done["data"]["verdict"] == "ship" and done["data"]["r"] == "ran:shipping"


def test_draft_endpoint_returns_a_flow(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    # bypass the provider-key guard + the real LLM
    import maverick.flow.draft as draft_mod
    from maverick.flow.ir import single_agent_flow
    monkeypatch.setattr("maverick_dashboard._shared.require_provider_or_400", lambda: None)
    monkeypatch.setattr(draft_mod, "draft_flow",
                        lambda desc, **k: (single_agent_flow(k.get("flow_id", ""), "Drafted", desc), ["note"]))
    r = _client().post("/api/v1/flows/draft", json={"description": "email me on new leads"})
    assert r.status_code == 200
    body = r.json()
    assert body["flow"]["nodes"][0]["kind"] == "agent"
    assert body["notes"] == ["note"]


def test_draft_endpoint_ranks_full_live_catalog_and_passes_contracts(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    import maverick.flow.draft as draft_mod
    from maverick.flow.ir import single_agent_flow
    from maverick_dashboard import api as api_mod
    from maverick_dashboard import app as app_mod

    monkeypatch.setattr("maverick_dashboard._shared.require_provider_or_400", lambda: None)
    monkeypatch.setattr(api_mod, "_TOOL_INDEX_CACHE", [
        {"name": "slack_bot", "description": "Post a team message",
         "params": ["channel", "text"], "category": "Communication"},
        {"name": "pagerduty_incidents",
         "description": "Create an alert for the on-call engineer",
         "params": ["summary"], "category": "Operations"},
        {"name": "image_resize", "description": "Resize an image",
         "params": ["path"], "category": "Other"},
    ])
    monkeypatch.setattr(api_mod, "_TOOL_SCHEMA_CACHE", {
        "pagerduty_incidents": {
            "type": "object", "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    })
    rate_sources = []
    monkeypatch.setattr(
        app_mod, "check_goal_rate_limit",
        lambda request, source=None, **kwargs: rate_sources.append(source),
    )
    seen = {}

    def fake_draft(desc, **kwargs):
        seen.update(kwargs)
        return single_agent_flow(kwargs.get("flow_id", ""), "Drafted", desc), []

    monkeypatch.setattr(draft_mod, "draft_flow", fake_draft)
    r = _client().post(
        "/api/v1/flows/draft",
        json={"description": "page the on-call engineer when production breaks"},
    )

    assert r.status_code == 200, r.text
    assert seen["tools"][0] == "pagerduty_incidents"
    assert "image_resize" not in seen["tools"]
    assert seen["tool_schemas"]["pagerduty_incidents"]["required"] == ["summary"]
    assert rate_sources == ["flow-authoring"]


def test_live_authoring_tool_cache_is_tenant_scoped(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from types import SimpleNamespace

    from maverick.paths import current_tenant_id, reset_tenant, set_tenant
    from maverick_dashboard import api as api_mod

    monkeypatch.setattr(api_mod, "_TOOL_INDEX_CACHE", None)
    monkeypatch.setattr(api_mod, "_TOOL_SCHEMA_CACHE", None)
    monkeypatch.setattr(api_mod, "_TOOL_CATALOG_CACHE", {})
    monkeypatch.setattr("maverick.flow.ir.action_tool_policy_error", lambda name: "")

    class _Registry:
        def all(self):
            tenant = current_tenant_id() or "shared"
            return [SimpleNamespace(
                name=f"{tenant}_private_tool",
                description=f"private to {tenant}",
                input_schema={"type": "object", "properties": {}},
            )]

    monkeypatch.setattr("maverick.tools.base_registry", lambda *args, **kwargs: _Registry())
    token = set_tenant("tenant-a")
    try:
        tenant_a = api_mod._live_tool_index()
    finally:
        reset_tenant(token)
    token = set_tenant("tenant-b")
    try:
        tenant_b = api_mod._live_tool_index()
    finally:
        reset_tenant(token)

    assert [item["name"] for item in tenant_a] == ["tenant-a_private_tool"]
    assert [item["name"] for item in tenant_b] == ["tenant-b_private_tool"]


def test_live_authoring_tool_cache_is_execution_identity_scoped(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from types import SimpleNamespace

    from maverick_dashboard import api as api_mod

    monkeypatch.setattr(api_mod, "_TOOL_INDEX_CACHE", None)
    monkeypatch.setattr(api_mod, "_TOOL_SCHEMA_CACHE", None)
    monkeypatch.setattr(api_mod, "_TOOL_CATALOG_CACHE", {})
    monkeypatch.setattr("maverick.flow.ir.action_tool_policy_error", lambda name: "")

    class _Registry:
        def __init__(self, user_id):
            self.user_id = user_id

        def all(self):
            return [SimpleNamespace(
                name=f"{self.user_id}_allowed_tool",
                description=f"private to {self.user_id}",
                input_schema={"type": "object", "properties": {}},
            )]

    seen = []

    def registry(*args, **kwargs):
        seen.append((kwargs.get("channel"), kwargs.get("user_id")))
        return _Registry(kwargs.get("user_id"))

    monkeypatch.setattr("maverick.tools.base_registry", registry)
    alice = api_mod._live_tool_index(channel="api", user_id="alice")
    bob = api_mod._live_tool_index(channel="api", user_id="bob")
    alice_cached = api_mod._live_tool_index(channel="api", user_id="alice")

    assert [item["name"] for item in alice] == ["alice_allowed_tool"]
    assert [item["name"] for item in bob] == ["bob_allowed_tool"]
    assert alice_cached == alice
    assert seen == [("api", "alice"), ("api", "bob")]


def test_draft_requires_provider(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr("maverick.config.any_provider_configured", lambda: False)
    r = _client().post("/api/v1/flows/draft", json={"description": "x"})
    assert r.status_code == 400


def test_chat_endpoint_returns_reply_and_patched_flow(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    import maverick.flow.chat as chat_mod
    from maverick.flow.chat import FlowChatResult
    from maverick.flow.ir import Flow
    monkeypatch.setattr("maverick_dashboard._shared.require_provider_or_400", lambda: None)
    seen = {}

    def fake_chat(message, *, flow=None, history=(), run=None, **k):
        seen.update(message=message, flow=flow, history=list(history), run=run)
        return FlowChatResult(reply="Added it.", flow=flow,
                              applied=["add delay node d1 after a"])
    monkeypatch.setattr(chat_mod, "chat_flow", fake_chat)
    r = _client().post("/api/v1/flows/chat", json={
        "message": "add a 5 minute delay",
        "flow": _FLOW,
        "history": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"] == "Added it."
    assert body["flow"]["id"] == "demo" and body["applied"]
    assert isinstance(seen["flow"], Flow) and seen["history"][0]["content"] == "hi"


def test_chat_endpoint_ranks_new_tools_and_keeps_live_existing_binding(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    import maverick.flow.chat as chat_mod
    from maverick.flow.chat import FlowChatResult
    from maverick_dashboard import api as api_mod
    from maverick_dashboard import app as app_mod

    monkeypatch.setattr("maverick_dashboard._shared.require_provider_or_400", lambda: None)
    monkeypatch.setattr(api_mod, "_TOOL_INDEX_CACHE", [
        {"name": "notify", "description": "Send a notification",
         "params": ["m"], "category": "Communication"},
        {"name": "rare_crm_sync", "description": "Sync a customer lead into the CRM",
         "params": ["lead"], "category": "Sales"},
        {"name": "image_resize", "description": "Resize an image",
         "params": ["path"], "category": "Other"},
    ])
    monkeypatch.setattr(api_mod, "_TOOL_SCHEMA_CACHE", {
        "notify": {"type": "object", "properties": {"m": {"type": "string"}}},
        "rare_crm_sync": {
            "type": "object", "properties": {"lead": {"type": "object"}},
            "required": ["lead"],
        },
    })
    rate_sources = []
    monkeypatch.setattr(
        app_mod, "check_goal_rate_limit",
        lambda request, source=None, **kwargs: rate_sources.append(source),
    )
    seen = {}

    def fake_chat(message, **kwargs):
        seen.update(kwargs)
        return FlowChatResult(reply="Ready.")

    monkeypatch.setattr(chat_mod, "chat_flow", fake_chat)
    r = _client().post("/api/v1/flows/chat", json={
        "message": "sync each customer lead into our CRM", "flow": _FLOW,
    })

    assert r.status_code == 200, r.text
    assert "rare_crm_sync" in seen["tools"]
    assert "notify" in seen["tools"]  # current action remains bindable
    assert "image_resize" not in seen["tools"]
    assert seen["tool_schemas"]["rare_crm_sync"]["required"] == ["lead"]
    assert rate_sources == ["flow-authoring"]


def test_chat_grounds_in_an_owned_run(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    import maverick.flow.chat as chat_mod
    from maverick.flow import store
    from maverick.flow.chat import FlowChatResult
    monkeypatch.setattr("maverick_dashboard._shared.require_provider_or_400", lambda: None)
    store.save_run(store.FlowRun(run_id="rf", flow_id="demo", status="failed",
                                 cursor="b", error="node 'b': boom"))
    seen = {}

    def fake_chat(message, *, run=None, **k):
        seen["run"] = run
        return FlowChatResult(reply="b failed: boom")
    monkeypatch.setattr(chat_mod, "chat_flow", fake_chat)
    r = _client().post("/api/v1/flows/chat", json={
        "message": "why did it fail?", "flow": _FLOW, "run_id": "rf"})
    assert r.status_code == 200
    assert seen["run"]["cursor"] == "b" and "boom" in seen["run"]["error"]
    # an unknown run id is a 404, not a silent no-context turn
    assert _client().post("/api/v1/flows/chat", json={
        "message": "x", "flow": _FLOW, "run_id": "nope"}).status_code == 404


def test_chat_requires_provider(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr("maverick.config.any_provider_configured", lambda: False)
    r = _client().post("/api/v1/flows/chat", json={"message": "x", "flow": _FLOW})
    assert r.status_code == 400


def test_retry_from_failure_resumes_at_failed_node(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import execution, store
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    calls = []

    def _fake_agent(world, **k):
        def run(brief, d):
            calls.append(brief)
            return (f"ran:{brief}", 1.0)
        return run
    monkeypatch.setattr(execution, "default_agent_runner", _fake_agent)
    monkeypatch.setattr(execution, "default_action_runner",
                        lambda world, sandbox=None, **kwargs: lambda t, p, d: ("sent", 1.0))
    c = _client()
    flow = {"id": "fret", "name": "f", "start": "a", "nodes": [
        {"id": "a", "kind": "agent", "brief": "prep", "next": "b", "output": "r1"},
        {"id": "b", "kind": "action", "tool": "web_search", "params": {}, "output": "r2"},
    ]}
    assert c.post("/api/v1/flows", json={"flow": flow}).status_code == 201
    digest, version, subflows = store.snapshot_flow_bundle(store.load_flow("fret"))
    # a run that failed AT node b, with node a's work already done
    store.save_run(store.FlowRun(run_id="rx", flow_id="fret", status="failed",
                                 cursor="b", data={"r1": "ok"},
                                 nodes={"a": {"status": "done", "outcome": 1.0}},
                                 definition_digest=digest, definition_version=version,
                                 subflow_digests=subflows))
    r = c.post("/api/v1/flows/runs/rx/retry?from_failure=1")
    assert r.status_code == 200, r.text
    assert r.json() == {"run_id": "rx", "status": "resuming", "from_node": "b"}
    _drain(aq)
    done = c.get("/api/v1/flows/runs/rx").json()
    assert done["status"] == "completed" and done["data"]["r2"] == "sent"
    assert calls == []                        # node a was NOT re-run
    # a completed run can't be resumed from failure
    assert c.post("/api/v1/flows/runs/rx/retry?from_failure=1").status_code == 409
    # a failed run with no recorded failure node can't either
    store.save_run(store.FlowRun(
        run_id="ry", flow_id="fret", status="failed",
        definition_digest=digest, definition_version=version,
        subflow_digests=subflows))
    assert c.post("/api/v1/flows/runs/ry/retry?from_failure=1").status_code == 409


def test_run_events_stream_ends_with_terminal_snapshot(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    store.save_run(store.FlowRun(run_id="sse1", flow_id="demo", status="completed",
                                 data={"r": "ok"}, nodes={"a": {"status": "done", "outcome": 1.0}}))
    c = _client()
    with c.stream("GET", "/api/v1/flows/runs/sse1/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = ""
        for chunk in r.iter_text():
            body += chunk
            if "event: terminal" in body:
                break
    assert "event: terminal" in body and '"status": "completed"' in body
    # unknown run 404s before a stream slot is held
    assert c.get("/api/v1/flows/runs/nope/events").status_code == 404


def test_indeterminate_run_is_terminal_and_cannot_be_blindly_retried(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store

    store.save_run(store.FlowRun(
        run_id="ambiguous", flow_id="demo", status="indeterminate",
        cursor="charge", error="external acknowledgement was lost",
    ))
    c = _client()
    retry = c.post("/api/v1/flows/runs/ambiguous/retry")
    assert retry.status_code == 409
    assert "reconcile" in retry.json()["detail"].lower()

    with c.stream("GET", "/api/v1/flows/runs/ambiguous/events") as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert "event: terminal" in body
    assert '"status": "indeterminate"' in body


def test_run_viewer_page_renders(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    r = _client().get("/flows/demo/runs/abc123")
    assert r.status_code == 200
    assert 'id="fr-steps"' in r.text and "open in designer" in r.text


def test_designer_page_has_copilot_and_toolbar(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    r = _client().get("/flows/designer")
    assert r.status_code == 200
    for needle in ('id="fd-chat"', 'id="fd-undo"', 'id="fd-redo"', 'id="fd-fit"',
                   'id="fd-triggers"', 'id="fd-export"', 'data-add="switch"',
                   'data-add="while"', 'data-add="wait_event"', 'data-add="scope"',
                   "/api/v1/flows/chat", "upstreamOutputs", "validateFlow"):
        assert needle in r.text, needle
    # A Copilot handoff is short-lived, consumed once, and drafts without saving.
    for needle in ("lightwork.authoring-handoff", "handoff.kind !== 'flow'",
                   "10 * 60 * 1000", "sessionStorage.removeItem", "draft();"):
        assert needle in r.text, needle


def test_flow_analytics_aggregates_runs(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    for i in range(3):
        store.save_run(store.FlowRun(run_id=f"c{i}", flow_id="demo", status="completed",
                                     nodes={"a": {"status": "done", "outcome": 1.0, "seconds": 2.0 + i}}))
    store.save_run(store.FlowRun(run_id="f1", flow_id="demo", status="failed",
                                 error="node 'b': connector down"))
    store.save_run(store.FlowRun(run_id="p1", flow_id="demo", status="paused_approval"))
    flows = _client().get("/api/v1/flows/analytics").json()["flows"]
    demo = next(f for f in flows if f["flow_id"] == "demo")
    assert demo["runs"] == 5 and demo["completed"] == 3 and demo["failed"] == 1 and demo["paused"] == 1
    assert demo["success_rate"] == 0.75          # 3 completed / 4 finished
    assert demo["p50_seconds"] >= 2.0 and demo["p95_seconds"] >= demo["p50_seconds"]
    assert demo["top_errors"][0]["error"].startswith("node 'b'")


def test_flow_pages_render_under_strict_undefined(monkeypatch, tmp_path):
    # A literal "{{key}}" in template TEXT (a CSS/JS comment, an example string)
    # is a stray Jinja expression: harmless under the default Undefined (renders
    # empty), but a 500 on any instance configured with StrictUndefined -- which
    # is exactly what broke the flow designer in the field. Render the flow
    # pages (and their included JS) under StrictUndefined so a stray "{{ }}"
    # doc-literal fails HERE instead of on a hardened deployment.
    from jinja2 import StrictUndefined
    from maverick_dashboard.app import templates
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(templates.env, "undefined", StrictUndefined)
    c = _client()
    for path in ("/flows/designer", "/flows/designer/demo", "/workflow-builder",
                 "/flows/analytics", "/connections", "/workflows",
                 "/flows/f/runs/r"):
        assert c.get(path).status_code == 200, path


def test_flow_analytics_page_renders(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    r = _client().get("/flows/analytics")
    assert r.status_code == 200 and 'id="fa-table"' in r.text


def test_flow_schema_endpoint_returns_learned_shapes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick.flow.ir import Flow, FlowNode
    store.save_flow(Flow(
        id="demo", name="demo", start="a",
        nodes={"a": FlowNode(id="a", kind="agent", brief="work")},
    ))
    store.record_flow_schema("demo", {"order": {"type": "object", "keys": ["total"]}})
    r = _client().get("/api/v1/flows/demo/schema")
    assert r.status_code == 200
    assert r.json()["schema"]["order"] == {"type": "object", "keys": ["total"]}
    assert _client().get("/api/v1/flows/nope/schema").status_code == 404


def test_flow_schema_is_owner_scoped(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick.flow.ir import Flow, FlowNode
    from maverick_dashboard import api as api_mod

    store.save_flow(Flow(
        id="private-schema", name="private", start="a", owner="user:alice",
        nodes={"a": FlowNode(id="a", kind="agent", brief="work")},
    ))
    store.record_flow_schema("private-schema", {"customer": {"type": "object"}})
    monkeypatch.setattr(api_mod, "caller_principal", lambda request: "user:bob")
    monkeypatch.setattr(api_mod, "is_dashboard_admin", lambda principal: False)
    monkeypatch.setattr(api_mod, "require_permission", lambda request, permission: None)
    assert _client().get("/api/v1/flows/private-schema/schema").status_code == 404


def test_gallery_returns_valid_starter_flows(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import Flow
    flows = _client().get("/api/v1/flows/gallery").json()["flows"]
    assert len(flows) >= 3
    for g in flows:
        f = Flow.from_dict(g)
        f.id = "gallery-check"
        assert f.validate() == [], (g["name"], f.validate())


def test_tools_search_reaches_the_full_registry(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    curated = c.get("/api/v1/flows/tools").json()["tools"]
    assert curated and any(t["name"] == "slack_bot" for t in curated)
    hits = c.get("/api/v1/flows/tools?q=http").json()["tools"]
    assert hits and all("http" in (t["name"] + t["description"]).lower() for t in hits)
    # name-prefix matches rank first
    assert hits[0]["name"].startswith("http")


def test_tools_catalog_carries_categories(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    body = _client().get("/api/v1/flows/tools").json()
    # Every response advertises the category list so the picker can build its filter.
    assert "Communication" in body["categories"] and body["categories"][-1] == "Other"
    # Curated tools each carry a category.
    assert body["tools"] and all("category" in t for t in body["tools"])


def test_tools_category_filter_narrows_the_registry(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    comms = c.get("/api/v1/flows/tools?category=Communication").json()["tools"]
    assert comms and all(t["category"] == "Communication" for t in comms)
    # A category + query intersect: only Communication tools matching the query.
    both = c.get("/api/v1/flows/tools?q=slack&category=Communication").json()["tools"]
    assert both and all(t["category"] == "Communication" for t in both)
    assert all("slack" in (t["name"] + t["description"]).lower() for t in both)


def test_designer_opens_a_template_as_a_flow(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    import maverick.templates as tpl
    tdir = tmp_path / ".maverick" / "templates"
    monkeypatch.setattr(tpl, "USER_TEMPLATES", tdir)
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "weekly-digest.md").write_text(
        "---\ntitle: Weekly digest\nparams: []\n---\nWrite the weekly digest.\n",
        encoding="utf-8")
    r = _client().get("/flows/designer?from_template=weekly-digest")
    assert r.status_code == 200
    assert "Write the weekly digest." in r.text          # seeded as an agent brief
    # an unknown template degrades to a blank canvas, not an error
    assert _client().get("/flows/designer?from_template=nope").status_code == 200


def test_proposals_endpoint(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import node_outcomes, store
    from maverick.flow.ir import Flow, FlowNode
    f = Flow(id="fp", name="f", start="a",
             nodes={"a": FlowNode(id="a", kind="agent", brief="b")})
    store.save_flow(f)
    for _ in range(12):
        node_outcomes.record("fp", "a", "agent", 1.0)   # reliable agent
    props = _client().get("/api/v1/flows/fp/proposals").json()["proposals"]
    assert props and props[0]["to_kind"] == "action"


def test_flow_crud_is_owner_scoped_for_authenticated_non_admin(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import api as api_mod

    current = {"principal": "user:alice"}
    monkeypatch.setattr(api_mod, "caller_principal", lambda request: current["principal"])
    monkeypatch.setattr(api_mod, "is_dashboard_admin", lambda principal: False)
    monkeypatch.setattr(api_mod, "require_permission", lambda request, permission: None)

    c = _client()
    assert c.post("/api/v1/flows", json={"flow": _FLOW}).status_code == 201

    current["principal"] = "user:bob"
    assert c.get("/api/v1/flows").json()["flows"] == []
    assert c.get("/api/v1/flows/demo").status_code == 404
    assert c.post("/api/v1/flows/demo/run", json={"data": {}}).status_code == 404
    assert c.delete("/api/v1/flows/demo").status_code == 404

    updated = {**_FLOW, "name": "Bob overwrite", "schedule": "* * * * *"}
    assert c.post("/api/v1/flows", json={"flow": updated}).status_code == 404

    current["principal"] = "user:alice"
    saved = c.get("/api/v1/flows/demo").json()
    assert saved["name"] == "Demo"
    assert saved["owner"] == "user:alice"


def test_flow_crud_admin_keeps_unscoped_access(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import api as api_mod

    current = {"principal": "user:alice", "admin": False}
    monkeypatch.setattr(api_mod, "caller_principal", lambda request: current["principal"])
    monkeypatch.setattr(api_mod, "is_dashboard_admin", lambda principal: current["admin"])
    monkeypatch.setattr(api_mod, "require_permission", lambda request, permission: None)

    c = _client()
    assert c.post("/api/v1/flows", json={"flow": _FLOW}).status_code == 201

    current.update(principal="user:admin", admin=True)
    current_flow = c.get("/api/v1/flows/demo").json()
    updated = {**current_flow, "name": "Admin edit", "schedule": "* * * * *"}
    assert c.post("/api/v1/flows", json={"flow": updated}).status_code == 201
    saved = c.get("/api/v1/flows/demo").json()
    assert saved["name"] == "Admin edit"
    assert saved["owner"] == "user:alice"
