"""Cross-replica contracts for durable task-graph authority."""
from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from maverick import authority_policy, governed_records, task_graph
from maverick.connections import bind_principal


class _Plane:
    def __init__(self) -> None:
        self.backend_kind = "postgres"
        self.documents: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.fail_after_commit = False

    def store(self, key: str):
        plane = self

        class _Store:
            @property
            def backend_kind(self):
                return plane.backend_kind

            def read(self):
                with plane.lock:
                    return copy.deepcopy(plane.documents.get(key, {}))

            def mutate(self, callback, **_kwargs):
                with plane.lock:
                    document = copy.deepcopy(plane.documents.get(key, {}))
                    result = callback(document)
                    plane.documents[key] = copy.deepcopy(document)
                    if plane.fail_after_commit:
                        plane.fail_after_commit = False
                        raise OSError("response lost after commit")
                    return result

        return _Store()


def _shared(monkeypatch, tmp_path):
    plane = _Plane()
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setattr(
        governed_records,
        "configured_governed_records_backend",
        lambda: "postgres",
    )
    monkeypatch.setattr(task_graph, "_new_shared_store", plane.store)
    return plane


def test_shared_concurrent_adds_merge_without_lost_updates(monkeypatch, tmp_path):
    plane = _shared(monkeypatch, tmp_path)

    def add(index: int) -> str:
        with bind_principal("user:alice"):
            return task_graph._run({
                "op": "add",
                "graph": "release",
                "task": f"task-{index}",
            })

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(add, range(64)))

    assert all(result.startswith("added task") for result in results)
    with bind_principal("user:alice"):
        loaded = json.loads(task_graph._run({"op": "list", "graph": "release"}))
    assert set(loaded) == {f"task-{index}" for index in range(64)}
    assert len(plane.documents) == 1
    assert not (tmp_path / "task_graphs").exists()


def test_shared_graphs_are_exact_principal_scoped(monkeypatch, tmp_path):
    plane = _shared(monkeypatch, tmp_path)
    with bind_principal("User:Alice"):
        assert "added" in task_graph._run({
            "op": "add", "graph": "roadmap", "task": "private",
        })
    with bind_principal("user:alice"):
        assert task_graph._run({"op": "list", "graph": "roadmap"}) == "(empty)"
        assert "added" in task_graph._run({
            "op": "add", "graph": "roadmap", "task": "other",
        })
    with bind_principal("User:Alice"):
        assert "private" in task_graph._run({"op": "list", "graph": "roadmap"})
        assert "other" not in task_graph._run({"op": "list", "graph": "roadmap"})
    assert len(plane.documents) == 2


def test_shared_response_loss_is_retryable_and_idempotent(monkeypatch, tmp_path):
    plane = _shared(monkeypatch, tmp_path)
    plane.fail_after_commit = True
    args = {"op": "add", "graph": "ops", "task": "recover"}

    with bind_principal("user:alice"):
        assert task_graph._run(args) == (
            "ERROR: shared task-graph authority is unavailable"
        )
        assert task_graph._run(args) == "added task 'recover'"
        tasks = json.loads(task_graph._run({"op": "list", "graph": "ops"}))
    assert list(tasks) == ["recover"]


def test_shared_cutover_and_backend_drift_fail_closed(monkeypatch, tmp_path):
    plane = _shared(monkeypatch, tmp_path)
    with bind_principal("user:alice"):
        legacy = task_graph._graph_path("legacy")
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"tasks": {}}', encoding="utf-8")
        assert task_graph._run({
            "op": "add", "graph": "legacy", "task": "must-not-land",
        }) == "ERROR: shared task-graph authority is unavailable"
        assert plane.documents == {}

        legacy.unlink()
        plane.backend_kind = "local"
        assert task_graph._run({
            "op": "add", "graph": "legacy", "task": "must-not-land",
        }) == "ERROR: shared task-graph authority is unavailable"
        assert plane.documents == {}


def test_shared_corruption_and_cycles_are_never_persisted(monkeypatch, tmp_path):
    plane = _shared(monkeypatch, tmp_path)
    with bind_principal("user:alice"):
        assert "added" in task_graph._run({
            "op": "add", "graph": "dag", "task": "a", "deps": ["b"],
        })
        assert task_graph._run({
            "op": "add", "graph": "dag", "task": "b", "deps": ["a"],
        }) == "ERROR: task graph has a dependency cycle"
        listing = json.loads(task_graph._run({"op": "list", "graph": "dag"}))
        assert set(listing) == {"a"}

        key = next(iter(plane.documents))
        plane.documents[key]["tasks"]["a"]["status"] = "forged"
        assert task_graph._run({"op": "list", "graph": "dag"}) == (
            "ERROR: shared task-graph authority is unavailable"
        )


def test_strict_mode_refuses_local_task_graph_authority(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setattr(
        governed_records,
        "configured_governed_records_backend",
        lambda: "local",
    )
    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)

    assert task_graph._run({"op": "add", "task": "must-not-land"}) == (
        "ERROR: shared task-graph authority is unavailable"
    )
    assert not (tmp_path / "task_graphs").exists()
