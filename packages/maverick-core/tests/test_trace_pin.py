"""Trace pinning to commit: git-state stamp at run start + readback."""
from __future__ import annotations

import contextvars
import subprocess
import threading

from maverick.trace_pin import TRACE_META_KIND, pin_trace, trace_commit, workspace_git_state
from maverick.world_model import open_world


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "f.txt").write_text("one")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    return tmp_path


def test_workspace_git_state(tmp_path):
    repo = _git_repo(tmp_path)
    state = workspace_git_state(str(repo))
    assert state is not None
    assert len(state["commit"]) == 40 and state["dirty"] is False
    (repo / "f.txt").write_text("two")
    assert workspace_git_state(str(repo))["dirty"] is True


def test_workspace_git_state_disables_repo_fsmonitor(tmp_path):
    repo = _git_repo(tmp_path)
    payload = repo / "fsmonitor-payload.sh"
    marker = repo / "fsmonitor-ran"
    payload.write_text(f"#!/bin/sh\necho pwned > {marker}\nexit 0\n")
    payload.chmod(0o755)
    subprocess.run(["git", "-C", str(repo), "config", "core.fsmonitor", str(payload)], check=True)

    state = workspace_git_state(str(repo))

    assert state is not None
    assert state["dirty"] is True
    assert not marker.exists()


def test_not_a_repo_returns_none(tmp_path):
    assert workspace_git_state(str(tmp_path)) is None


def test_pin_and_readback(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    w = open_world(tmp_path / "world.db")
    gid = w.create_goal("g", "d", owner="")
    state = pin_trace(w, gid, cwd=str(repo))
    assert state is not None
    pinned = trace_commit(w, gid)
    assert pinned == state
    events = [e for e in w.goal_events(gid) if e.kind == TRACE_META_KIND]
    assert len(events) == 1


def test_pin_outside_repo_is_noop(tmp_path):
    w = open_world(tmp_path / "world.db")
    gid = w.create_goal("g", "d", owner="")
    assert pin_trace(w, gid, cwd=str(tmp_path)) is None
    assert trace_commit(w, gid) is None


def test_pin_never_raises(tmp_path):
    class _BrokenWorld:
        def append_event(self, *a, **kw):
            raise RuntimeError("db down")

    repo = _git_repo(tmp_path)
    assert pin_trace(_BrokenWorld(), 1, cwd=str(repo)) is None


_SCOPE = contextvars.ContextVar("trace_pin_test_scope", default=None)
_STATE = {"commit": "a" * 40, "branch": "main", "dirty": False}


def _stub_runner(monkeypatch, tmp_path, run):
    from maverick.budget import Budget
    from maverick.world_model import WorldModel

    db = tmp_path / "world.db"
    world = WorldModel(db)
    gid = world.create_goal("task", owner="user:alice", execution_principal="user:alice")
    world.close()

    class _Sandbox:
        def close(self):
            pass

    monkeypatch.setattr("maverick.world_model.open_world", lambda *a, **k: WorldModel(db))
    monkeypatch.setattr("maverick.llm.LLM", lambda *a, **k: object())
    monkeypatch.setattr("maverick.sandbox.build_sandbox", lambda *a, **k: _Sandbox())
    monkeypatch.setattr("maverick.budget.budget_from_config", lambda *a, **k: Budget())
    monkeypatch.setattr("maverick.orchestrator.run_goal_sync", run)
    return db, gid


def test_runner_stamps_trace_without_making_the_run_wait(monkeypatch, tmp_path):
    """The run starts while git is still reading the workspace; the stamp is
    recorded, in the caller's context, by the time the runner returns."""
    from maverick import runner, trace_pin
    from maverick.world_model import WorldModel

    run_started, git_returned = threading.Event(), threading.Event()
    seen = {}

    def slow_git_state(cwd=None):
        seen["scope"] = _SCOPE.get()
        run_started.wait(timeout=1.0)
        git_returned.set()
        return dict(_STATE)

    def run(llm, world, budget, goal_id, **kw):
        seen["git_done_when_the_run_started"] = git_returned.is_set()
        run_started.set()
        world.set_goal_status(goal_id, "done")

    monkeypatch.setattr(trace_pin, "workspace_git_state", slow_git_state)
    db, gid = _stub_runner(monkeypatch, tmp_path, run)
    token = _SCOPE.set("tenant-a")
    try:
        assert runner.run_goal_in_thread(gid, user_id="alice") == "done"
    finally:
        _SCOPE.reset(token)
    assert seen == {"git_done_when_the_run_started": False, "scope": "tenant-a"}
    w = WorldModel(db)
    try:
        assert trace_commit(w, gid) == _STATE
    finally:
        w.close()


def test_a_trace_pin_that_cannot_get_a_thread_pins_inline(monkeypatch, tmp_path):
    from maverick import trace_pin

    class _NoThread(threading.Thread):
        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(trace_pin, "workspace_git_state", lambda cwd=None: dict(_STATE))
    monkeypatch.setattr(trace_pin.threading, "Thread", _NoThread)
    w = open_world(tmp_path / "world.db")
    gid = w.create_goal("g", "d", owner="")
    assert trace_pin.pin_trace_in_background(w, gid) is None
    assert trace_commit(w, gid) == _STATE
