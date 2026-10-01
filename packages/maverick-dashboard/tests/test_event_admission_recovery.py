"""Real goal/queue transactions at event delivery crash boundaries."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from maverick import automation_events, event_admission
from maverick.job_queue import JobQueue
from maverick.world_model import WorldModel
from maverick_dashboard import automation_queue, event_poll, event_triggers_store


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", "1")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick.paths import reset_tenant, set_tenant
    from maverick.templates import Template
    from maverick_dashboard.triggers_store import snapshot_from_template

    token = set_tenant(None)
    tpl = Template(name="recover", title="Handle {{title}}", body="Prepare {{title}}", params=["title"])
    trigger = event_triggers_store.set_trigger(
        "orders", "recover", "http_json", owner="user:synthetic",
        config={"url": "https://feed.invalid"}, cursor="old",
        template_snapshot=snapshot_from_template(tpl))
    world = WorldModel(tmp_path / "world.db")
    queue = JobQueue(tmp_path / "jobs.db")
    monkeypatch.setattr(automation_queue, "_world", lambda: world)
    monkeypatch.setattr(automation_queue, "queue", lambda: queue)
    monkeypatch.setattr("maverick_dashboard.auth.stored_automation_identity",
                        lambda owner: (owner, "synthetic", None))
    items = [{"id": "new", "title": "order"}, {"id": "old"}]
    monkeypatch.setattr(automation_events, "_http_get_json", lambda *a, **k: items)
    yield world, queue, trigger, items
    world.close()
    reset_tenant(token)


def poll(runtime, fire=None, *, durable=True):
    world, _, trigger, _ = runtime
    current = event_triggers_store.get_trigger(trigger["name"])
    return event_poll.poll_and_fire(
        world, [current], fire=fire or automation_queue.enqueue_event_goal,
        fire_is_durable=durable)[0]


def test_cursor_write_failure_after_dispatch_does_not_repeat_goal_or_queue_job(runtime, monkeypatch):
    original = event_triggers_store.set_cursor
    calls = []
    def fail_once(*args, **kwargs):
        if not calls:
            calls.append(True)
            raise OSError("synthetic cursor publication failure")
        return original(*args, **kwargs)
    monkeypatch.setattr(event_triggers_store, "set_cursor", fail_once)
    assert poll(runtime)["poll_error"]
    assert not poll(runtime)["poll_error"]
    world, queue, _, _ = runtime
    assert len(world.list_goals()) == len(queue.list()) == 1
    assert queue.list()[0].payload["goal_id"] == world.list_goals()[0].id


@pytest.mark.parametrize("committed", [False, True])
def test_failed_enqueue_repairs_from_outbox_after_event_leaves_feed(runtime, monkeypatch, committed):
    world, queue, _, items = runtime
    original = queue.enqueue_once
    calls = []
    def fail_once(*args, **kwargs):
        if not calls:
            calls.append(True)
            if committed:
                original(*args, **kwargs)
            raise OSError("synthetic queue acknowledgement failure")
        return original(*args, **kwargs)
    monkeypatch.setattr(queue, "enqueue_once", fail_once)
    assert poll(runtime)["poll_error"]
    goal_id = world.list_goals()[0].id
    items.clear()  # the external event is no longer discoverable
    assert not poll(runtime)["poll_error"]
    assert len(world.list_goals()) == len(queue.list()) == 1
    assert queue.list()[0].payload["goal_id"] == goal_id
    assert event_admission.pending(world, event_poll._trigger_key(runtime[2])) == []


def test_entire_observed_batch_survives_first_enqueue_failure_and_source_eviction(runtime):
    world, queue, _, items = runtime
    items.insert(0, {"id": "newer", "title": "second order"})
    def unavailable(_goal_id):
        raise OSError("synthetic first queue admission failure")
    assert poll(runtime, unavailable)["poll_error"]
    assert len(world.list_goals()) == 2 and queue.list() == []
    items.clear()
    assert not poll(runtime)["poll_error"]
    assert len(queue.list()) == len(world.list_goals()) == 2
    assert len({job.payload["goal_id"] for job in queue.list()}) == 2


def test_second_receipt_failure_rolls_back_whole_batch_before_dispatch(runtime):
    world, queue, _, items = runtime
    items.insert(0, {"id": "newer", "title": "second order"})
    world.conn.execute("CREATE TRIGGER synthetic_failure BEFORE INSERT ON event_goal_admissions "
                       "WHEN (SELECT COUNT(*) FROM event_goal_admissions)>0 "
                       "BEGIN SELECT RAISE(ABORT, 'synthetic second receipt failure'); END")
    assert poll(runtime)["poll_error"]
    assert world.list_goals() == [] and queue.list() == []
    world.conn.execute("DROP TRIGGER synthetic_failure")
    assert not poll(runtime)["poll_error"]
    assert len(world.list_goals()) == len(queue.list()) == 2


def test_erased_pending_goal_is_retired_without_blocking_future_events(runtime):
    world, queue, trigger, items = runtime
    def unavailable(_goal_id):
        raise OSError("synthetic queue unavailable")
    assert poll(runtime, unavailable)["poll_error"]
    erased_id = world.list_goals()[0].id
    with world._writing() as connection:
        connection.execute("DELETE FROM goal_origins WHERE goal_id=?", (erased_id,))
        connection.execute("DELETE FROM goals WHERE id=?", (erased_id,))
    items.insert(0, {"id": "future", "title": "later order"})
    result = poll(runtime)
    assert not result["poll_error"] and any("retired" in note for note in result["notes"])
    assert not poll(runtime)["poll_error"]  # next poll follows the completed retained batch
    assert len(world.list_goals()) == len(queue.list()) == 1
    assert queue.list()[0].payload["goal_id"] != erased_id
    assert event_admission.pending(world, event_poll._trigger_key(trigger)) == []


def test_unknown_effect_callback_is_not_automatically_replayed(runtime):
    fired = []
    def unknown(goal_id):
        fired.append(goal_id)
        raise ConnectionError("synthetic reply lost after dispatch")
    assert poll(runtime, unknown, durable=False)["poll_error"]
    retry = poll(runtime, unknown, durable=False)
    assert retry["poll_error"] and "unconfirmed" in " ".join(retry["notes"])
    assert len(fired) == 1
    assert len(runtime[0].list_goals()) == 1


def test_goal_origin_receipt_rollback_is_atomic_and_retry_is_possible(runtime):
    world = runtime[0]
    world.conn.execute("CREATE TRIGGER synthetic_failure BEFORE INSERT ON event_goal_admissions "
                       "BEGIN SELECT RAISE(ABORT, 'synthetic receipt failure'); END")
    assert poll(runtime)["poll_error"]
    assert world.list_goals() == []
    assert world.conn.execute("SELECT COUNT(*) FROM goal_origins").fetchone()[0] == 0
    world.conn.execute("DROP TRIGGER synthetic_failure")
    assert not poll(runtime)["poll_error"]
    assert len(world.list_goals()) == 1


def test_concurrent_connections_admit_one_goal_and_one_permanent_job(runtime, tmp_path):
    from maverick.paths import tenant_scope
    trigger = runtime[2]
    key = event_poll._event_key(event_poll._trigger_key(trigger), {"id": "same"})
    def admit(_):
        with tenant_scope(tenant=None), WorldModel(tmp_path / "world.db") as world:
            goal_id, _ = event_admission.admit(
                world, key, "one", "one", owner=trigger["owner"], origin=trigger["name"],
                trigger_key=event_poll._trigger_key(trigger))
            queue = JobQueue(tmp_path / "jobs.db")
            job_id = queue.enqueue_once("run_goal", {"goal_id": goal_id}, idempotency_key=key)
            return goal_id, job_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(admit, range(2)))
    assert rows[0] == rows[1]
    assert len(runtime[0].list_goals()) == len(runtime[1].list()) == 1


def test_queue_receipt_survives_terminal_job_purge_and_rejects_different_payload(runtime):
    queue = runtime[1]
    goal = {"goal_id": 1}
    job_id = queue.enqueue_once("run_goal", goal, idempotency_key="permanent")
    queue.complete(job_id)
    assert queue.purge(older_than_days=-1) == 1
    assert queue.enqueue_once("run_goal", goal, idempotency_key="permanent") == job_id
    assert queue.list() == []
    with pytest.raises(ValueError, match="different work"):
        queue.enqueue_once("run_goal", {"goal_id": 2}, idempotency_key="permanent")


def test_queue_insert_failure_rolls_back_job_and_receipt(runtime, monkeypatch):
    queue = runtime[1]
    original = queue._insert_job
    def fail_after_insert(*a, **k):
        original(*a, **k)
        raise OSError("synthetic insert fault")
    monkeypatch.setattr(queue, "_insert_job", fail_after_insert)
    with pytest.raises(OSError):
        queue.enqueue_once("run_goal", {"goal_id": 1}, idempotency_key="one")
    assert queue.list() == []
    monkeypatch.setattr(queue, "_insert_job", original)
    assert queue.enqueue_once("run_goal", {"goal_id": 1}, idempotency_key="one") == 1


def test_bootstrap_hold_blocks_new_admission_and_pending_repair(runtime, monkeypatch):
    from maverick.workspace_recovery import RecoveryError
    def hold():
        raise RecoveryError("synthetic restore reconciliation hold")
    monkeypatch.setattr("maverick.workspace_recovery.assert_bootstrap_start_allowed", hold)
    assert poll(runtime)["poll_error"]
    assert runtime[0].list_goals() == [] and runtime[1].list() == []


def test_admission_refuses_world_opened_for_another_tenant(runtime):
    from maverick.paths import tenant_scope
    with tenant_scope(tenant="other"):
        with pytest.raises(ValueError, match="different tenant"):
            event_admission.admit(runtime[0], "same", "one", "one", owner="user:synthetic",
                                  origin="orders", trigger_key="generation")
    assert runtime[0].list_goals() == []


def test_cursor_compare_and_swap_preserves_peer_progress_and_recreated_trigger(runtime):
    trigger = runtime[2]
    generation = event_poll._trigger_key(trigger)
    assert event_triggers_store.set_cursor("orders", "later", expected_cursor="old",
                                            expected_generation=generation)
    assert not event_triggers_store.set_cursor("orders", "earlier", expected_cursor="old",
                                                expected_generation=generation)
    assert event_triggers_store.get_trigger("orders")["cursor"] == "later"
    event_triggers_store.delete_trigger("orders")
    event_triggers_store.set_trigger("orders", "recover", "http_json", cursor="fresh")
    assert not event_triggers_store.set_cursor("orders", "stale", expected_generation=generation)
    assert event_triggers_store.get_trigger("orders")["cursor"] == "fresh"


def test_stale_concurrent_fetch_retires_delivered_batch_without_regressing_peer_cursor(runtime, monkeypatch):
    """A source fetch can finish after another poll has delivered and advanced."""
    calls = []
    first_cursor = automation_events._encode_cursor("feed", "e1")
    def interleaved_source(_source, config, cursor):
        calls.append((cursor, config["_lightwork_cursor_format"]))
        if len(calls) == 1:
            # Outer poll already read the old cursor/no inbox. Finish a peer
            # poll before returning the outer poll's newer observed batch.
            assert not poll(runtime)["poll_error"]
            ids = ["e1", "e2"]
        elif len(calls) == 2:
            ids = ["e1"]
        else:
            assert cursor == first_cursor
            assert config["_lightwork_cursor_format"] == 1
            ids = ["e2", "e3"]
        return automation_events.PollResult(
            [{"id": item, "title": item} for item in ids],
            automation_events._encode_cursor("feed", ids[-1]), event_ids=ids)
    monkeypatch.setattr(automation_events, "poll_source", interleaved_source)
    result = poll(runtime)
    assert not result["poll_error"]
    assert len(runtime[0].list_goals()) == len(runtime[1].list()) == 2
    stored = event_triggers_store.get_trigger("orders")
    assert stored["cursor"] == first_cursor and stored["cursor_format"] == 1
    assert event_admission.poll_batch(runtime[0], event_poll._trigger_key(stored)) is None
    assert not poll(runtime)["poll_error"]
    assert len(calls) == 3
    assert len(runtime[0].list_goals()) == len(runtime[1].list()) == 3
    assert event_triggers_store.get_trigger("orders")["cursor"] == automation_events._encode_cursor("feed", "e3")


def test_peer_progress_disposition_still_refuses_recreated_trigger(runtime):
    generation = event_poll._trigger_key(runtime[2])
    assert event_triggers_store.set_cursor("orders", "peer", cursor_format=1)
    event_triggers_store.delete_trigger("orders")
    event_triggers_store.set_trigger("orders", "recover", "http_json", cursor="fresh")
    assert not event_triggers_store.set_cursor(
        "orders", "stale", expected_cursor="old", expected_generation=generation,
        cursor_format=1, accept_peer_progress=True)
    stored = event_triggers_store.get_trigger("orders")
    assert stored["cursor"] == "fresh" and stored["cursor_format"] == 0


def test_exact_identity_and_trigger_generation_do_not_collapse_events():
    first = {"name": "orders", "created": 1, "tenant": "alpha", "source": "http_json"}
    same = {**first, "cursor": "advanced", "interval_seconds": 90}
    changed = {**first, "created": 2}
    assert event_poll._trigger_key(first) == event_poll._trigger_key(same)
    assert event_poll._trigger_key(first) != event_poll._trigger_key(changed)
    assert event_poll._trigger_key(first) != event_poll._trigger_key({**first, "tenant": "beta"})
    assert event_poll._event_key("scope", {}, "x" * 2500 + "a") != event_poll._event_key(
        "scope", {}, "x" * 2500 + "b")


def test_manual_poll_endpoint_publishes_durable_job_without_background_execution(runtime, monkeypatch):
    from fastapi.testclient import TestClient
    from maverick_dashboard import api
    from maverick_dashboard.app import app
    monkeypatch.setattr(api, "_world", lambda: runtime[0])
    from maverick.oidc import VerifiedPrincipal
    from maverick_dashboard import auth
    monkeypatch.setattr(auth, "oidc_enabled", lambda: True)
    monkeypatch.setattr(auth, "verify_oidc_token", lambda token, **kwargs: VerifiedPrincipal(
        sub=token, issuer="https://issuer.example", audience="maverick", claims={"sub": token}))
    # Deliberately no lifespan/worker startup; inspect actual durable admission.
    client = TestClient(app, headers={"Origin": "http://testserver", "Authorization": "Bearer synthetic"})
    response = client.post("/api/v1/event-triggers/poll?name=orders")
    assert response.status_code == 200, response.text
    result = response.json()["triggers"][0]
    assert not result["poll_error"] and len(result["fired"]) == 1
    job = runtime[1].list()[0]
    assert job.kind == "run_goal" and job.status == "pending" and job.attempts == 0
    assert job.payload["goal_id"] == result["fired"][0]
    assert job.payload["concurrency_principal"] == "user:synthetic"


def arm_flow(runtime, *, id_field="id"):
    from maverick.flow import store
    from maverick.flow.ir import Flow, FlowNode
    saved = store.save_flow(Flow(id="orders-flow", name="Orders", owner="user:synthetic", start="a",
                                 nodes={"a": FlowNode(id="a", kind="agent", brief="Prepare order")}))
    release = store.publish_flow(saved.id, expected_version=saved.version, expected_revision=saved.revision)
    return event_triggers_store.set_trigger(
        "orders", "", "http_json", owner="user:synthetic", flow=saved.id,
        flow_owner=saved.owner, flow_revision=release["release_id"], cursor="old",
        config={"url": "https://feed.invalid", "id_field": id_field})


def poll_flow(runtime, dispatcher=None):
    trigger = event_triggers_store.get_trigger("orders")
    return event_poll.poll_and_fire(
        runtime[0], [trigger], fire=automation_queue.enqueue_event_goal, fire_is_durable=True,
        fire_flow=dispatcher or automation_queue.enqueue_published_flow_run)[0]


def test_flow_source_ids_avoid_display_id_and_truncation_collisions(runtime):
    arm_flow(runtime, id_field="order.key")
    runtime[3][:] = [{"id": "display", "order": {"key": "x" * 2200 + suffix}}
                     for suffix in ("b", "a")]
    runtime[3].append({"order": {"key": "old"}})
    result = poll_flow(runtime)
    assert not result["poll_error"] and len(set(result["fired_flows"])) == 2
    assert len(runtime[1].list()) == 2
    assert not poll_flow(runtime)["poll_error"]
    assert len(runtime[1].list()) == 2


def test_flow_legacy_admission_is_reused_after_lost_cursor_write(runtime):
    trigger = arm_flow(runtime)
    existing = automation_queue.enqueue_published_flow_run(
        trigger["flow"], {"id": "new", "title": "order"}, "user:synthetic", "orders:new",
        expected_revision=trigger["flow_revision"])
    result = poll_flow(runtime)
    assert not result["poll_error"] and result["fired_flows"] == [existing]
    assert len(runtime[1].list()) == 1


def test_flow_legacy_display_collision_with_proven_different_source_id_creates_new_run(runtime):
    trigger = arm_flow(runtime, id_field="order.key")
    existing = automation_queue.enqueue_published_flow_run(
        trigger["flow"], {"id": "display", "order.key": "alpha"}, "user:synthetic", "orders:display",
        expected_revision=trigger["flow_revision"])
    runtime[3][:] = [{"id": "display", "order": {"key": "beta"}}, {"order": {"key": "old"}}]
    result = poll_flow(runtime)
    assert not result["poll_error"] and result["fired_flows"] != [existing]
    assert len(runtime[1].list()) == 2


def test_flow_legacy_truncated_identity_is_held_for_reconciliation(runtime):
    trigger = arm_flow(runtime, id_field="order.key")
    automation_queue.enqueue_published_flow_run(
        trigger["flow"], {"id": "display", "order.key": "x" * 2000}, "user:synthetic", "orders:display",
        expected_revision=trigger["flow_revision"])
    runtime[3][:] = [{"id": "display", "order": {"key": "x" * 2200 + "different"}}, {"order": {"key": "old"}}]
    result = poll_flow(runtime)
    assert result["poll_error"] and "reconcile" in " ".join(result["notes"])
    assert len(runtime[1].list()) == 1
    assert event_triggers_store.get_trigger("orders")["cursor"] == "old"


def test_flow_entire_batch_recovers_if_first_reservation_fails_then_source_evicts(runtime):
    trigger = arm_flow(runtime)
    runtime[3].insert(0, {"id": "newer", "title": "second"})
    def fail_before_reservation(*a, **k):
        raise OSError("synthetic reservation failure")
    assert poll_flow(runtime, fail_before_reservation)["poll_error"]
    assert runtime[1].list() == []
    batch = event_admission.poll_batch(runtime[0], event_poll._trigger_key(trigger))
    assert len(batch["events"]) == 2
    runtime[3].clear()
    result = poll_flow(runtime)
    assert not result["poll_error"] and len(set(result["fired_flows"])) == 2
    assert len(runtime[1].list()) == 2
    assert event_admission.poll_batch(runtime[0], event_poll._trigger_key(trigger)) is None


def test_pending_poll_batch_is_sealed_and_cursor_format_roundtrips(runtime):
    def fail_before_queue(_goal_id):
        raise OSError("synthetic unavailable")
    assert poll(runtime, fail_before_queue)["poll_error"]
    row = runtime[0].conn.execute("SELECT sealed_batch FROM event_trigger_batches").fetchone()
    from maverick.crypto_at_rest import at_rest_enabled
    if at_rest_enabled():
        assert '"title":"order"' not in row[0]
    assert not poll(runtime)["poll_error"]
    stored = event_triggers_store.get_trigger("orders")
    assert stored["cursor_format"] == 1
    assert event_admission.poll_batch(runtime[0], event_poll._trigger_key(stored)) is None
