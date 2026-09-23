from __future__ import annotations

from types import SimpleNamespace


def test_run_goal_in_thread_propagates_execution_identity(monkeypatch):
    """HTTP adapters can pass an authenticated user through the runner."""
    from maverick import budget as budget_mod
    from maverick import llm as llm_mod
    from maverick import orchestrator, runner, world_model
    from maverick import sandbox as sandbox_mod

    captured: dict = {}

    class FakeWorld:
        def get_goal(self, goal_id):
            return SimpleNamespace(
                id=goal_id,
                status="done" if captured else "pending",
                execution_principal="user:alice",
            )

        def close(self):
            pass

    def fake_run_goal_sync(*args, **kwargs):
        captured.update(kwargs)
        return "DONE."

    monkeypatch.setattr(world_model, "open_world", lambda *a, **k: FakeWorld())
    monkeypatch.setattr(llm_mod, "LLM", lambda: object())
    monkeypatch.setattr(sandbox_mod, "build_sandbox", lambda: object())
    monkeypatch.setattr(budget_mod, "budget_from_config", lambda **_kwargs: budget_mod.Budget())
    monkeypatch.setattr(orchestrator, "run_goal_sync", fake_run_goal_sync)

    assert runner.run_goal_in_thread(
        42, max_depth=2, channel="api", user_id="alice", conversation_id=9,
        allowed_suites=frozenset({"finance"}),
    ) == "done"
    assert captured["channel"] == "api"
    assert captured["user_id"] == "alice"
    assert captured["conversation_id"] == 9
    assert captured["allowed_suites"] == frozenset({"finance"})
    assert captured["resume"] is True


def test_run_goal_in_thread_can_schedule_by_caller_principal(monkeypatch):
    """Fleet runs keep agent audit identity but share the caller's fair lane."""
    from maverick import budget as budget_mod
    from maverick import llm as llm_mod
    from maverick import orchestrator, runner, world_model
    from maverick import sandbox as sandbox_mod

    captured: dict = {}
    lanes: list[str | None] = []

    class FakeWorld:
        def get_goal(self, goal_id):
            return SimpleNamespace(
                id=goal_id,
                status="done" if captured else "pending",
                execution_principal="user:agent:acme.coder",
            )

        def close(self):
            pass

    class FakeSemaphore:
        def acquire(self, timeout=None):
            return True

        def release(self):
            pass

    def fake_run_goal_sync(*args, **kwargs):
        captured.update(kwargs)
        return "DONE."

    def fake_principal_semaphore(principal):
        lanes.append(principal)
        return FakeSemaphore()

    monkeypatch.setattr(runner, "_principal_semaphore", fake_principal_semaphore)
    monkeypatch.setattr(world_model, "open_world", lambda *a, **k: FakeWorld())
    monkeypatch.setattr(llm_mod, "LLM", lambda: object())
    monkeypatch.setattr(sandbox_mod, "build_sandbox", lambda: object())
    monkeypatch.setattr(budget_mod, "budget_from_config", lambda **_kwargs: budget_mod.Budget())
    monkeypatch.setattr(orchestrator, "run_goal_sync", fake_run_goal_sync)

    assert runner.run_goal_in_thread(
        42,
        max_depth=2,
        channel="fleet",
        user_id="agent:acme.coder",
        concurrency_principal="user:alice",
    ) == "done"
    assert lanes == ["user:alice"]
    assert captured["channel"] == "fleet"
    assert captured["user_id"] == "agent:acme.coder"


def test_runner_refuses_mismatched_principal_before_setup_or_trace(
    monkeypatch,
    tmp_path,
):
    from maverick import llm as llm_mod
    from maverick import runner, trace_pin, world_model
    from maverick import sandbox as sandbox_mod

    world = world_model.WorldModel(tmp_path / "world.db")
    goal_id = world.create_goal(
        "delegated work",
        owner="user:human-owner",
        execution_principal="agent:assigned",
    )
    called: list[str] = []

    monkeypatch.setattr(world_model, "open_world", lambda *a, **k: world)
    monkeypatch.setattr(world_model, "close_world_if_owned", lambda _world: None)
    monkeypatch.setattr(llm_mod, "LLM", lambda: called.append("llm"))
    monkeypatch.setattr(
        sandbox_mod,
        "build_sandbox",
        lambda: called.append("sandbox"),
    )
    monkeypatch.setattr(
        trace_pin,
        "pin_trace",
        lambda *_a, **_k: called.append("trace"),
    )

    assert runner.run_goal_in_thread(goal_id, user_id="attacker") == "blocked"
    assert called == []
    assert world.get_goal(goal_id).status == "pending"
    assert world.get_goal(goal_id).owner == "user:human-owner"
    assert world.get_goal(goal_id).execution_principal == "agent:assigned"
    assert world.goal_events(goal_id) == []
