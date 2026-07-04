"""Polled event triggers: manage via /api/v1/event-triggers, drain via
/event-triggers/poll. Operate-gated + feature-knobbed + owner-scoped; the
built-in http_json source is driven with an injected fetch (no network)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _client():
    from maverick_dashboard.app import app
    return TestClient(app, headers={"Origin": "http://testserver"})


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", "1")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    import maverick.templates as tpl
    tdir = tmp_path / ".maverick" / "templates"
    tdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tpl, "USER_TEMPLATES", tdir)
    (tdir / "triage.md").write_text(
        "---\ntitle: Triage {{title}}\nparams:\n  - title\n---\nHandle {{title}}.\n",
        encoding="utf-8")


@pytest.fixture
def _no_real_run(monkeypatch):
    import maverick.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_goal_in_thread", lambda *a, **k: None)


def _feed(monkeypatch, box):
    import maverick.automation_events as ev
    monkeypatch.setattr(ev, "_http_get_json", lambda url, headers=None, timeout=15.0: box[0])


# ---- store round-trip -------------------------------------------------------

def test_store_round_trip_and_cursor(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import event_triggers_store as s
    rec = s.set_trigger("My Feed!", "triage", "http_json",
                        config={"url": "http://x"}, params={"title": "x"},
                        owner="user:alice")
    assert rec["name"] == "my-feed"
    got = s.get_trigger("my-feed")
    assert got["source"] == "http_json" and got["config"] == {"url": "http://x"}
    assert got["owner"] == "user:alice" and got["cursor"] == ""
    # cursor advances and re-save preserves it
    assert s.set_cursor("my-feed", "42") is True
    assert s.get_trigger("my-feed")["cursor"] == "42"
    s.set_trigger("My Feed!", "triage", "http_json", config={"url": "http://y"})
    assert s.get_trigger("my-feed")["cursor"] == "42"        # preserved on replace
    assert s.delete_trigger("my-feed", owner="user:bob") is False   # not bob's
    assert s.delete_trigger("my-feed", owner="user:alice") is True


# ---- management API ---------------------------------------------------------

def test_create_list_delete(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    r = c.post("/api/v1/event-triggers", json={
        "template": "triage", "source": "http_json",
        "config": {"url": "http://x/feed", "items_path": "items"}})
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "http_json"
    listed = c.get("/api/v1/event-triggers").json()
    assert any(t["name"] == "triage" for t in listed["triggers"])
    assert "http_json" in listed["sources"] and listed["enabled"] is True
    assert c.delete("/api/v1/event-triggers/triage").status_code == 200
    assert c.delete("/api/v1/event-triggers/triage").status_code == 404


def test_create_rejects_unknown_source_and_missing_template(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    assert c.post("/api/v1/event-triggers", json={
        "template": "triage", "source": "nope", "config": {}}).status_code == 400
    assert c.post("/api/v1/event-triggers", json={
        "template": "ghost", "source": "http_json", "config": {}}).status_code == 404


def test_feature_gate_off_403(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", "0")
    c = _client()
    assert c.post("/api/v1/event-triggers", json={
        "template": "triage", "source": "http_json", "config": {}}).status_code == 403
    assert c.post("/api/v1/event-triggers/poll").status_code == 403


# ---- poll runtime (end to end) ---------------------------------------------

def test_poll_baselines_then_fires_then_dedupes(monkeypatch, tmp_path, _no_real_run):
    _isolate(monkeypatch, tmp_path)
    box = [{"items": [{"id": "1", "title": "first"}]}]
    _feed(monkeypatch, box)
    c = _client()
    c.post("/api/v1/event-triggers", json={
        "template": "triage", "source": "http_json",
        "config": {"url": "http://x/feed", "items_path": "items", "id_field": "id"}})

    # first poll = baseline: cursor set to newest, nothing fires
    p0 = c.post("/api/v1/event-triggers/poll").json()
    assert p0["triggers"][0]["new_events"] == 0 and p0["triggers"][0]["fired"] == []

    # a new item appears -> it fires, the event field fills the {{title}} param
    box[0] = {"items": [{"id": "2", "title": "second"}, {"id": "1", "title": "first"}]}
    p1 = c.post("/api/v1/event-triggers/poll").json()
    assert p1["triggers"][0]["new_events"] == 1
    gid = p1["triggers"][0]["fired"][0]
    from maverick.world_model import DEFAULT_DB, WorldModel
    assert WorldModel(DEFAULT_DB).get_goal(gid).title == "Triage second"

    # polling again with no change fires nothing (cursor dedup)
    assert c.post("/api/v1/event-triggers/poll").json()["triggers"][0]["new_events"] == 0


def test_poll_reports_source_error_without_crashing(monkeypatch, tmp_path, _no_real_run):
    _isolate(monkeypatch, tmp_path)
    import maverick.automation_events as ev

    def _boom(url, headers=None, timeout=15.0):
        raise ev.EventSourceError("unreachable")
    monkeypatch.setattr(ev, "_http_get_json", _boom)
    c = _client()
    c.post("/api/v1/event-triggers", json={
        "template": "triage", "source": "http_json", "config": {"url": "http://x"}})
    out = c.post("/api/v1/event-triggers/poll").json()
    assert any("poll failed" in n for n in out["triggers"][0]["notes"])


def test_history_records_fires_and_errors(monkeypatch, tmp_path, _no_real_run):
    _isolate(monkeypatch, tmp_path)
    box = [{"items": [{"id": "1", "title": "first"}]}]
    _feed(monkeypatch, box)
    c = _client()
    c.post("/api/v1/event-triggers", json={
        "template": "triage", "source": "http_json",
        "config": {"url": "http://x/feed", "items_path": "items", "id_field": "id"}})
    c.post("/api/v1/event-triggers/poll")                      # baseline: no row
    assert c.get("/api/v1/event-triggers/history").json()["history"] == []

    box[0] = {"items": [{"id": "2", "title": "second"}, {"id": "1", "title": "first"}]}
    c.post("/api/v1/event-triggers/poll")                      # fires -> a row
    hist = c.get("/api/v1/event-triggers/history").json()["history"]
    assert len(hist) == 1 and hist[0]["kind"] == "fired" and hist[0]["new_events"] == 1

    # a source error is recorded too
    import maverick.automation_events as ev
    monkeypatch.setattr(ev, "_http_get_json",
                        lambda *a, **k: (_ for _ in ()).throw(ev.EventSourceError("down")))
    c.post("/api/v1/event-triggers/poll")
    kinds = [r["kind"] for r in c.get("/api/v1/event-triggers/history").json()["history"]]
    assert "poll_error" in kinds                               # newest-first, error on top


def test_history_is_owner_scoped(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import trigger_events_store as tes
    tes.record("mine", "user:alice", tes.FIRED, new_events=1, fired=[1])
    tes.record("theirs", "user:bob", tes.FIRED, new_events=1, fired=[2])
    from maverick_dashboard import api
    monkeypatch.setattr(api, "goal_owner_filter", lambda request: "user:alice")
    names = [r["name"] for r in _client().get("/api/v1/event-triggers/history").json()["history"]]
    assert names == ["mine"]


def test_automations_page_shows_event_section_when_enabled(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    t = _client().get("/automations").text
    assert "Event triggers" in t and 'id="auto-event-form"' in t


def test_automations_page_hides_event_section_when_disabled(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", "0")
    assert 'id="auto-event-form"' not in _client().get("/automations").text


def test_poll_and_fire_attributes_goal_to_trigger_owner(monkeypatch, tmp_path):
    # No request principal (the background path); the fired goal must still be
    # owned by the trigger's owner, so owner-scoped views see it.
    _isolate(monkeypatch, tmp_path)
    import maverick.automation_events as ev
    from maverick_dashboard import event_poll
    from maverick_dashboard import event_triggers_store as s
    owners = []

    class _FakeWorld:
        def create_goal(self, title, description, owner=""):
            owners.append(owner)
            return len(owners)

        def record_goal_origin(self, gid, kind, name):
            pass

    box = [{"items": [{"id": "1", "title": "a"}]}]
    monkeypatch.setattr(ev, "_http_get_json", lambda url, headers=None, timeout=15.0: box[0])
    s.set_trigger("feed", "triage", "http_json", owner="user:alice",
                  config={"url": "http://x", "items_path": "items", "id_field": "id"})
    event_poll.poll_and_fire(_FakeWorld(), [s.get_trigger("feed")], fire=lambda g: None)
    box[0] = {"items": [{"id": "2", "title": "b"}, {"id": "1", "title": "a"}]}
    event_poll.poll_and_fire(_FakeWorld(), [s.get_trigger("feed")], fire=lambda g: None)
    assert owners == ["user:alice"]


def _reset_aq(monkeypatch, aq):
    # The queue/worker are module singletons; reset them so each test gets a
    # fresh automation-jobs.db under its own tmp MAVERICK_HOME.
    monkeypatch.setattr(aq, "_queue", None)
    monkeypatch.setattr(aq, "_worker", None)
    monkeypatch.setattr(aq, "_worker_thread", None)


def test_tick_polls_due_triggers_and_enqueues_durable_run_goal(monkeypatch, tmp_path):
    # The JobQueue tick handler polls due triggers and enqueues a durable
    # run_goal job per fired goal (retry + dead-letter), not a bare thread.
    _isolate(monkeypatch, tmp_path)
    import maverick.automation_events as ev
    from maverick_dashboard import automation_queue as aq
    from maverick_dashboard import event_triggers_store as s
    _reset_aq(monkeypatch, aq)
    monkeypatch.setattr(aq, "_due", lambda name, interval: True)   # ignore cadence in-test
    box = [{"items": [{"id": "1", "title": "a"}]}]
    monkeypatch.setattr(ev, "_http_get_json", lambda url, headers=None, timeout=15.0: box[0])
    s.set_trigger("feed", "triage", "http_json",
                  config={"url": "http://x", "items_path": "items", "id_field": "id"})
    aq._handle_tick(None)                                     # baseline: nothing fires
    assert aq.queue().counts().get("pending", 0) == 0
    box[0] = {"items": [{"id": "2", "title": "b"}, {"id": "1", "title": "a"}]}
    aq._handle_tick(None)                                     # new item -> a run_goal job
    jobs = aq.queue().list(status="pending")
    assert len(jobs) == 1 and jobs[0].kind == "run_goal"


def test_tick_respects_per_trigger_interval(monkeypatch, tmp_path):
    # A trigger polled within its interval is skipped on the next tick.
    _isolate(monkeypatch, tmp_path)
    import maverick.automation_events as ev
    from maverick_dashboard import automation_queue as aq
    from maverick_dashboard import event_triggers_store as s
    _reset_aq(monkeypatch, aq)
    monkeypatch.setattr(ev, "_http_get_json",
                        lambda url, headers=None, timeout=15.0: {"items": [{"id": "1"}]})
    s.set_trigger("feed", "triage", "http_json", interval_seconds=3600,
                  config={"url": "http://x", "items_path": "items", "id_field": "id"})
    assert aq._due("feed", 3600) is True                     # never polled -> due
    aq._handle_tick(None)                                     # polls + marks
    assert aq._due("feed", 3600) is False                    # within interval -> not due


def test_worker_starts_only_when_a_feature_is_on(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)                          # MAVERICK_EVENT_TRIGGERS=1
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    assert aq.start() is True                                 # a feature is on
    aq.stop()

    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", "0")
    monkeypatch.delenv("MAVERICK_DREAMING", raising=False)
    _reset_aq(monkeypatch, aq)
    assert aq.start() is False                                # nothing on -> no worker


def test_fired_goal_runs_via_durable_queue(monkeypatch, tmp_path):
    # A run_goal job on the automation queue is drained through the worker's
    # built-in handler -- the durable path that gets retry + dead-letter.
    _isolate(monkeypatch, tmp_path)
    import maverick.runner as runner_mod
    from maverick.worker import Worker
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    ran = []
    monkeypatch.setattr(runner_mod, "run_goal_in_thread",
                        lambda gid, **k: ran.append(gid) or "done")
    aq.queue().enqueue("run_goal", {"goal_id": 7})
    Worker(queue=aq.queue()).drain()
    assert ran == [7]


def test_reconcile_seeds_one_tick_and_is_idempotent(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)                          # MAVERICK_EVENT_TRIGGERS=1
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    aq.reconcile()
    aq.reconcile()
    ticks = [j for j in aq.queue().list(status="pending") if j.kind == aq.TICK_KIND]
    assert len(ticks) == 1


def test_list_is_owner_scoped(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import api, event_triggers_store
    event_triggers_store.set_trigger("mine", "triage", "http_json", owner="user:alice")
    event_triggers_store.set_trigger("theirs", "triage", "http_json", owner="user:bob")
    monkeypatch.setattr(api, "goal_owner_filter", lambda request: "user:alice")
    names = {t["name"] for t in _client().get("/api/v1/event-triggers").json()["triggers"]}
    assert names == {"mine"}
