"""Cron-scheduled flows: a saved flow carrying a ``schedule`` cron expression is
armed as a recurring ``flow_cron`` job that fires a fresh flow run each tick."""
from __future__ import annotations

import types


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


def _save_flow(fid="digest", schedule=""):
    from maverick.flow import store
    from maverick.flow.ir import Flow, FlowNode
    f = Flow(id=fid, name="Digest", start="a",
             nodes={"a": FlowNode(id="a", kind="agent", brief="daily digest")})
    f.schedule = schedule
    return store.save_flow(f)


def test_reconcile_arms_one_cron_per_scheduled_flow(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    _save_flow("digest", "0 9 * * *")
    _save_flow("manual", "")                    # no schedule -> no cron
    aq._reconcile_flow_crons()
    crons = aq._pending(aq.FLOW_CRON_KIND)
    assert len(crons) == 1
    assert crons[0].payload["flow_id"] == "digest"
    assert crons[0].payload["__cron__"] == "0 9 * * *"


def test_reconcile_is_idempotent(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    _save_flow("digest", "0 9 * * *")
    aq._reconcile_flow_crons()
    aq._reconcile_flow_crons()                  # second pass must not duplicate
    assert len(aq._pending(aq.FLOW_CRON_KIND)) == 1


def test_reconcile_cancels_cron_when_schedule_removed(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    _save_flow("digest", "0 9 * * *")
    aq._reconcile_flow_crons()
    assert len(aq._pending(aq.FLOW_CRON_KIND)) == 1
    _save_flow("digest", "")                    # schedule cleared
    aq._reconcile_flow_crons()
    assert aq._pending(aq.FLOW_CRON_KIND) == []


def test_reconcile_reseeds_when_expression_changes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    _save_flow("digest", "0 9 * * *")
    aq._reconcile_flow_crons()
    _save_flow("digest", "0 17 * * *")          # changed cadence
    aq._reconcile_flow_crons()
    crons = aq._pending(aq.FLOW_CRON_KIND)
    assert len(crons) == 1 and crons[0].payload["__cron__"] == "0 17 * * *"


def test_reconcile_skips_unarmable_schedule(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    _save_flow("digest", "0 0 31 2 *")
    aq._reconcile_flow_crons()
    assert aq._pending(aq.FLOW_CRON_KIND) == []


def test_handle_flow_cron_enqueues_a_fresh_run(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    _save_flow("digest", "0 9 * * *")
    job = types.SimpleNamespace(payload={"flow_id": "digest", "__cron__": "0 9 * * *"})
    aq._handle_flow_cron(job)
    runs = aq._pending(aq.FLOW_RUN_KIND)
    assert len(runs) == 1 and runs[0].payload["flow_id"] == "digest"


def test_handle_flow_cron_noops_on_stale_schedule(monkeypatch, tmp_path):
    # The flow's schedule changed since this cron was armed -> don't fire.
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    _save_flow("digest", "0 17 * * *")
    job = types.SimpleNamespace(payload={"flow_id": "digest", "__cron__": "0 9 * * *"})
    aq._handle_flow_cron(job)
    assert aq._pending(aq.FLOW_RUN_KIND) == []
