"""Adverse-path coverage for authoritative fleet halt control (RUN-16)."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import contextmanager, nullcontext

import pytest
from maverick import killswitch


class _World:
    def __init__(self, state: dict | None = None):
        self.state = dict(state) if state else None
        self.last_generation = int((state or {}).get("generation") or 0)
        self.arm_error: Exception | None = None
        self.disarm_error: Exception | None = None
        self.rearm_during_disarm = False

    def arm_halt(self, reason="", source="manual", armed_by=""):
        if self.arm_error:
            raise self.arm_error
        generation = self.last_generation + 1
        self.last_generation = generation
        self.state = {
            "reason": reason,
            "source": source,
            "armed_by": armed_by,
            "armed_at": float(generation),
            "generation": generation,
        }
        return dict(self.state)

    def active_halt(self):
        return dict(self.state) if self.state else None

    def disarm_halt(self, *, expected_generation=None):
        if self.disarm_error:
            raise self.disarm_error
        if self.rearm_during_disarm:
            self.last_generation += 1
            self.state = {
                "reason": "new emergency",
                "source": "peer",
                "armed_by": "operator:bob",
                "armed_at": 2.0,
                "generation": self.last_generation,
            }
            return False
        if self.state is None:
            return False
        if expected_generation != self.state["generation"]:
            return False
        self.state = None
        return True


@pytest.fixture(autouse=True)
def _isolated_halt(monkeypatch, tmp_path):
    halt_file = tmp_path / "HALT"
    monkeypatch.setenv("MAVERICK_HALT_FILE", str(halt_file))
    monkeypatch.setattr(killswitch, "_in_process_halt", None)
    monkeypatch.setattr(killswitch, "_last_file_check_ts", 0.0)
    monkeypatch.setattr(killswitch, "_last_file_present", False)
    monkeypatch.setattr(killswitch, "_last_shared_check_ts", 0.0)
    monkeypatch.setattr(killswitch, "_last_shared_halt", None)
    monkeypatch.setattr(killswitch, "_shared_world", None)
    monkeypatch.setattr(killswitch, "_page_halt", lambda *_args: None)
    monkeypatch.setattr(
        "maverick.ekko_control.control_barrier", lambda: nullcontext()
    )
    yield halt_file
    killswitch._in_process_halt = None


def _bind_world(monkeypatch, world: _World, *, shared: bool = True) -> None:
    monkeypatch.setattr(killswitch, "_open_control_world", lambda: (world, shared))
    monkeypatch.setattr(killswitch, "_close_control_world", lambda _world: None)


def _allow_audit(monkeypatch) -> None:
    monkeypatch.setattr(killswitch, "_required_control_audit", lambda **_kw: None)


def _armed_state(generation: int = 1) -> dict:
    return {
        "reason": "incident",
        "source": "dashboard",
        "armed_by": "user:alice",
        "armed_at": float(generation),
        "generation": generation,
    }


def test_arm_authority_failure_never_reports_success_and_local_stop_remains(
    monkeypatch, _isolated_halt,
):
    world = _World()
    world.arm_error = ConnectionError("postgres unavailable")
    _bind_world(monkeypatch, world)
    _allow_audit(monkeypatch)

    with pytest.raises(killswitch.HaltAuthorityUnavailable):
        killswitch.arm_fleet_halt(
            "incident", source="dashboard", armed_by="user:alice"
        )

    assert killswitch._in_process_halt == ("incident", "dashboard")
    assert _isolated_halt.read_text(encoding="utf-8") == "incident\n"


def test_sqlite_upgrade_backfills_generation_for_an_existing_armed_halt(tmp_path):
    from maverick.world_model import WorldModel

    path = tmp_path / "legacy-world.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
        INSERT INTO schema_version(version) VALUES(31);
        CREATE TABLE halt (
          scope TEXT PRIMARY KEY,
          reason TEXT,
          source TEXT,
          armed_by TEXT,
          armed_at REAL NOT NULL
        );
        INSERT INTO halt(scope, reason, source, armed_by, armed_at)
        VALUES('', 'legacy incident', 'dashboard', 'user:alice', 1.0);
        """
    )
    conn.commit()
    conn.close()

    world = WorldModel(path)
    try:
        assert world.active_halt()["generation"] == 1
    finally:
        world.close()


def test_arm_audit_failure_propagates_after_shared_commit_and_stays_armed(
    monkeypatch, _isolated_halt,
):
    world = _World()
    _bind_world(monkeypatch, world)

    def refuse(**_kwargs):
        raise RuntimeError("required audit unavailable")

    monkeypatch.setattr(killswitch, "_required_control_audit", refuse)
    with pytest.raises(RuntimeError, match="required audit"):
        killswitch.arm_fleet_halt(
            "incident", source="dashboard", armed_by="user:alice"
        )

    assert world.state and world.state["generation"] == 1
    assert killswitch._in_process_halt == ("incident", "dashboard")
    assert _isolated_halt.exists()


def test_successful_arm_hands_replica_local_fallback_to_shared_authority(
    monkeypatch, _isolated_halt,
):
    world = _World()
    _bind_world(monkeypatch, world)
    _allow_audit(monkeypatch)

    result = killswitch.arm_fleet_halt(
        "incident", source="dashboard", armed_by="user:alice"
    )

    assert result["generation"] == 1
    assert result["authority"] == "shared"
    assert world.state == {k: v for k, v in result.items() if k != "authority"}
    assert killswitch._in_process_halt is None
    assert not _isolated_halt.exists()


def test_successful_sqlite_arm_retains_file_authority_but_releases_process_latch(
    monkeypatch, _isolated_halt,
):
    world = _World()
    _bind_world(monkeypatch, world, shared=False)
    _allow_audit(monkeypatch)

    result = killswitch.arm_fleet_halt(
        "incident", source="dashboard", armed_by="user:alice"
    )

    assert result["generation"] == 1
    assert result["authority"] == "file"
    assert killswitch._in_process_halt is None
    assert _isolated_halt.read_text(encoding="utf-8") == "incident\n"
    with pytest.raises(killswitch.Halted) as exc:
        killswitch.check(force_refresh=True, fail_closed=False)
    assert exc.value.source == "file"
    status = killswitch.fleet_halt_status()
    assert status["authority"] == "file"
    assert status["generation"] == 1

    _isolated_halt.unlink()
    with pytest.raises(killswitch.HaltAuthorityUnavailable, match="file authority"):
        killswitch.fleet_halt_status()


def test_arm_handoff_failure_never_reports_success_and_retains_local_fallback(
    monkeypatch, _isolated_halt,
):
    world = _World()
    _bind_world(monkeypatch, world)
    _allow_audit(monkeypatch)

    def fail_after_local_release(**_kwargs):
        _isolated_halt.unlink()
        killswitch._in_process_halt = None
        raise killswitch.HaltAuthorityUnavailable("handoff unavailable")

    monkeypatch.setattr(
        killswitch,
        "_handoff_local_fallback_to_authority",
        fail_after_local_release,
    )

    with pytest.raises(killswitch.HaltAuthorityUnavailable, match="handoff"):
        killswitch.arm_fleet_halt(
            "incident", source="dashboard", armed_by="user:alice"
        )

    assert world.state and world.state["generation"] == 1
    assert killswitch._in_process_halt == ("incident", "dashboard")
    assert _isolated_halt.exists()


def test_clear_requires_explicit_authorization_before_any_state_change(
    monkeypatch, _isolated_halt,
):
    world = _World(_armed_state())
    _bind_world(monkeypatch, world)
    killswitch._set_local_halt("incident", "dashboard", persist_file=True)

    with pytest.raises(killswitch.HaltAuthorizationError):
        killswitch.clear_fleet_halt(
            cleared_by="user:alice", authorized=False, source="dashboard"
        )

    assert world.state is not None
    assert killswitch._in_process_halt is not None
    assert _isolated_halt.exists()


def test_clear_requires_the_exact_generation_operator_reviewed(
    monkeypatch, _isolated_halt,
):
    world = _World(_armed_state())
    _bind_world(monkeypatch, world)
    _allow_audit(monkeypatch)
    killswitch._set_local_halt("incident", "dashboard", persist_file=True)

    with pytest.raises(killswitch.HaltConflictError, match="generation"):
        killswitch.clear_fleet_halt(
            cleared_by="user:alice",
            authorized=True,
            source="dashboard",
        )

    assert world.state is not None
    assert killswitch._in_process_halt is not None
    assert _isolated_halt.exists()


def test_clean_sqlite_status_reports_file_authority(monkeypatch):
    world = _World()
    _bind_world(monkeypatch, world, shared=False)

    assert killswitch.fleet_halt_status() == {
        "active": False,
        "generation": 0,
        "authority": "file",
    }


def test_cli_unhalt_carries_the_reviewed_generation_into_clear(monkeypatch):
    from click.testing import CliRunner
    from maverick.cli import main

    captured = {}
    monkeypatch.setattr(
        killswitch,
        "fleet_halt_status",
        lambda: {"active": True, "generation": 7, "authority": "shared"},
    )

    def clear(**kwargs):
        captured.update(kwargs)
        return {"active": False, "generation": kwargs["expected_generation"]}

    monkeypatch.setattr(killswitch, "clear_fleet_halt", clear)

    result = CliRunner().invoke(main, ["unhalt"])

    assert result.exit_code == 0, result.output
    assert captured["expected_generation"] == 7


def test_clear_audit_or_authority_failure_never_clears_local_stop(
    monkeypatch, _isolated_halt,
):
    world = _World(_armed_state())
    _bind_world(monkeypatch, world)
    killswitch._set_local_halt("incident", "dashboard", persist_file=True)
    monkeypatch.setattr(
        killswitch,
        "_required_control_audit",
        lambda **_kw: (_ for _ in ()).throw(RuntimeError("audit refused")),
    )

    with pytest.raises(RuntimeError, match="audit refused"):
        killswitch.clear_fleet_halt(
            cleared_by="user:alice",
            authorized=True,
            source="dashboard",
            expected_generation=1,
        )
    assert world.state is not None
    assert killswitch._in_process_halt is not None
    assert _isolated_halt.exists()

    _allow_audit(monkeypatch)
    world.disarm_error = ConnectionError("postgres unavailable")
    with pytest.raises(killswitch.HaltAuthorityUnavailable):
        killswitch.clear_fleet_halt(
            cleared_by="user:alice",
            authorized=True,
            source="dashboard",
            expected_generation=1,
        )
    assert killswitch._in_process_halt is not None
    assert _isolated_halt.exists()


def test_clear_restores_local_stop_if_release_barrier_fails_after_unlink(
    monkeypatch, _isolated_halt,
):
    world = _World(_armed_state())
    _bind_world(monkeypatch, world)
    _allow_audit(monkeypatch)
    killswitch._set_local_halt("incident", "dashboard", persist_file=True)

    @contextmanager
    def fail_on_release():
        yield
        raise OSError("lock release failed")

    monkeypatch.setattr("maverick.ekko_control.control_barrier", fail_on_release)
    with pytest.raises(killswitch.HaltAuthorityUnavailable, match="local HALT"):
        killswitch.clear_fleet_halt(
            cleared_by="user:alice",
            authorized=True,
            source="dashboard",
            expected_generation=1,
        )

    assert world.state is None
    assert killswitch._in_process_halt == ("incident", "dashboard")
    assert _isolated_halt.exists()


def test_clear_compare_and_swap_refuses_concurrent_rearm(
    monkeypatch, _isolated_halt,
):
    world = _World(_armed_state())
    world.rearm_during_disarm = True
    _bind_world(monkeypatch, world)
    _allow_audit(monkeypatch)
    killswitch._set_local_halt("incident", "dashboard", persist_file=True)

    with pytest.raises(killswitch.HaltConflictError):
        killswitch.clear_fleet_halt(
            cleared_by="user:alice",
            authorized=True,
            source="dashboard",
            expected_generation=1,
        )

    assert world.state and world.state["generation"] == 2
    assert killswitch._in_process_halt is not None
    assert _isolated_halt.exists()


def test_clear_success_removes_shared_and_local_only_after_cas(
    monkeypatch, _isolated_halt,
):
    world = _World(_armed_state())
    _bind_world(monkeypatch, world)
    _allow_audit(monkeypatch)
    killswitch._set_local_halt("incident", "dashboard", persist_file=True)

    result = killswitch.clear_fleet_halt(
        cleared_by="user:alice",
        authorized=True,
        source="dashboard",
        expected_generation=1,
    )

    assert result["active"] is False
    assert result["generation"] == 1
    assert world.state is None
    assert killswitch._in_process_halt is None
    assert not _isolated_halt.exists()


def test_generation_zero_clears_only_a_local_fallback(monkeypatch, _isolated_halt):
    world = _World()
    _bind_world(monkeypatch, world)
    _allow_audit(monkeypatch)
    killswitch._set_local_halt(
        "shared arm was not confirmed", "shared-error", persist_file=True
    )

    result = killswitch.clear_fleet_halt(
        cleared_by="user:alice",
        authorized=True,
        source="dashboard",
        expected_generation=0,
    )

    assert result["active"] is False
    assert killswitch._in_process_halt is None
    assert not _isolated_halt.exists()


def test_production_critical_check_fails_closed_on_shared_authority_outage(
    monkeypatch,
):
    monkeypatch.setattr(killswitch, "_production_required", lambda: True)
    monkeypatch.setattr(
        "maverick.world_model_backends.is_postgres_configured", lambda: True
    )
    monkeypatch.setattr(
        "maverick.world_model.open_world",
        lambda: (_ for _ in ()).throw(ConnectionError("postgres unavailable")),
    )

    with pytest.raises(killswitch.Halted) as exc:
        killswitch.critical_check()
    assert exc.value.source == "shared-error"


def test_tool_registry_refuses_before_invocation(monkeypatch):
    from maverick.tools import Tool, ToolRegistry

    invoked = []
    registry = ToolRegistry(production_manifest=None)
    registry.register(
        Tool(
            name="read_file",
            description="read",
            input_schema={"type": "object", "properties": {}},
            fn=lambda _args: invoked.append(True) or "data",
        )
    )
    monkeypatch.setattr(
        killswitch,
        "critical_check",
        lambda: (_ for _ in ()).throw(killswitch.Halted("incident", "shared")),
    )

    with pytest.raises(killswitch.Halted):
        asyncio.run(registry.run("read_file", {}))
    assert invoked == []


def test_direct_governed_connector_wrapper_cannot_bypass_halt(monkeypatch):
    from maverick.governed_tools import wrap_connector_tool
    from maverick.tools import Tool

    invoked = []
    wrapped = wrap_connector_tool(
        Tool(
            name="connector_probe",
            description="probe",
            input_schema={"type": "object", "properties": {}},
            fn=lambda _args: invoked.append(True) or "data",
        ),
        enterprise=False,
    )
    monkeypatch.setattr(
        killswitch,
        "critical_check",
        lambda: (_ for _ in ()).throw(killswitch.Halted("incident", "shared")),
    )

    with pytest.raises(killswitch.Halted):
        wrapped.fn({"op": "get"})
    assert invoked == []


def test_halt_during_retry_backoff_prevents_next_provider_attempt(monkeypatch):
    from maverick import tool_reliability
    from maverick.tools import Tool, ToolRegistry

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(tool_reliability.asyncio, "sleep", no_sleep)
    checks = 0

    def halt_before_retry():
        nonlocal checks
        checks += 1
        if checks == 3:  # registry admission, attempt one, retry attempt two
            raise killswitch.Halted("incident", "shared")

    monkeypatch.setattr(killswitch, "critical_check", halt_before_retry)
    invoked = []

    def transient(_args):
        invoked.append(True)
        raise ConnectionError("connection refused by upstream")

    registry = ToolRegistry(production_manifest=None)
    registry.register(
        Tool(
            name="web_search",
            description="read",
            input_schema={"type": "object", "properties": {}},
            fn=transient,
        )
    )

    with pytest.raises(killswitch.Halted):
        asyncio.run(registry.run("web_search", {}))
    assert invoked == [True]


def test_worker_does_not_claim_while_halted(monkeypatch, tmp_path):
    from maverick.job_queue import JobQueue
    from maverick.worker import Worker

    queue = JobQueue(db_path=tmp_path / "jobs.db")
    job_id = queue.enqueue("observe", {})
    worker = Worker(queue=queue, idle_sleep=0)
    worker.register("observe", lambda _job: pytest.fail("handler must not run"))
    monkeypatch.setattr(
        killswitch,
        "critical_check",
        lambda: (_ for _ in ()).throw(killswitch.Halted("incident", "shared")),
    )

    assert worker.run_once() is False
    job = queue.get(job_id)
    assert job.status == "pending"
    assert job.attempts == 0


def test_worker_claim_race_is_typed_audited_and_deferred(monkeypatch, tmp_path):
    from maverick.job_queue import JobQueue
    from maverick.worker import Worker

    queue = JobQueue(db_path=tmp_path / "jobs.db")
    job_id = queue.enqueue("observe", {})
    worker = Worker(queue=queue, idle_sleep=0)
    invoked = []
    worker.register("observe", lambda _job: invoked.append(True))
    calls = 0

    def race_check():
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise killswitch.Halted("incident", "shared")

    audited = []
    monkeypatch.setattr(killswitch, "critical_check", race_check)
    monkeypatch.setattr(
        killswitch,
        "record_terminal_halt",
        lambda exc, **kwargs: audited.append((exc, kwargs)),
    )

    assert worker.run_once() is True
    job = queue.get(job_id)
    assert invoked == []
    assert audited and audited[0][1]["agent"] == "queue-worker"
    assert job.status == "pending"
    payload = json.loads(job.last_error)
    assert payload["execution_state"]["kind"] == "cancellation"
    assert payload["execution_state"]["terminal"] is True


def test_queue_submit_refuses_before_enqueue(monkeypatch):
    from maverick.queue_dispatcher import QueueDispatcher

    enqueued = []
    dispatcher = QueueDispatcher(
        lambda name, payload: enqueued.append((name, payload)), transport="local"
    )
    monkeypatch.setattr(
        killswitch,
        "critical_check",
        lambda: (_ for _ in ()).throw(killswitch.Halted("incident", "shared")),
    )

    with pytest.raises(killswitch.Halted):
        dispatcher.submit(1)
    assert enqueued == []


def test_flow_halt_is_typed_terminal_and_cannot_be_caught(monkeypatch):
    from maverick.failure_taxonomy import FailureKind
    from maverick.flow import runner
    from maverick.flow.ir import Flow, FlowNode

    body_node = FlowNode(
        id="body",
        kind="setvar",
        assignments={"effect": True},
    )
    body = Flow(
        id="body-flow",
        name="body",
        start="body",
        nodes={"body": body_node},
    )
    scope = FlowNode(
        id="scope",
        kind="scope",
        body=body,
        on_error="caught",
    )
    caught = FlowNode(
        id="caught",
        kind="setvar",
        assignments={"caught": True},
    )
    flow = Flow(
        id="fleet-halt",
        name="fleet halt",
        start="scope",
        nodes={"scope": scope, "caught": caught},
    )
    checks = 0

    def halt_inside_scope():
        nonlocal checks
        checks += 1
        if checks == 3:  # admission, scope node, then nested body node
            raise killswitch.Halted("incident", "shared")

    monkeypatch.setattr(runner, "critical_check", halt_inside_scope)
    result = runner.run_flow(
        flow,
        agent_fn=lambda *_args: ("unused", 1.0),
        action_fn=lambda *_args: ("unused", 1.0),
    )

    assert result.status == runner.STATUS_FAILED
    assert result.cursor == "body"
    assert result.failure is not None
    assert result.failure.kind is FailureKind.CANCELLATION
    assert result.failure.terminal is True
    assert "caught" not in result.data
