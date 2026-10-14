"""Shared Postgres authority routing for governed flow state."""
from __future__ import annotations

import copy
import sys
import threading
import types
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from maverick.flow import Flow, FlowNode, reconciliation, store
from maverick.flow.postgres_authority import (
    FlowAuthorityIntegrityError,
    FlowAuthorityUnavailable,
    PostgresFlowAuthority,
)


class _MemoryAuthority:
    """Transactional behavioral double for the Postgres record contract."""

    def __init__(self):
        self.records: dict[tuple[str, str], dict] = {}
        self.transaction_mutex = threading.RLock()
        self.session_mutexes: defaultdict[str, threading.RLock] = defaultdict(
            threading.RLock
        )
        self.fail_definition_write = False
        self.fail_run_write = False

    @contextmanager
    def transaction_lock(self, _name):
        with self.transaction_mutex:
            before = copy.deepcopy(self.records)
            try:
                yield
            except BaseException:
                self.records = before
                raise

    @contextmanager
    def session_lock(self, name):
        with self.session_mutexes[name]:
            yield

    def get(self, namespace, key):
        value = self.records.get((namespace, key))
        return copy.deepcopy(value) if value is not None else None

    def exists(self, namespace, key):
        return (namespace, key) in self.records

    def put(self, namespace, key, document, *, immutable=False):
        if self.fail_definition_write and namespace == "definitions":
            raise RuntimeError("simulated Postgres disconnect")
        if self.fail_run_write and namespace == "runs":
            raise RuntimeError("simulated run projection disconnect")
        address = (namespace, key)
        if immutable and address in self.records:
            if self.records[address] != document:
                raise FlowAuthorityIntegrityError("immutable conflict")
            return
        self.records[address] = copy.deepcopy(document)

    def delete(self, namespace, key):
        return self.records.pop((namespace, key), None) is not None

    def move(self, source_namespace, source_key, target_namespace, target_key):
        source = (source_namespace, source_key)
        if source not in self.records:
            return False
        self.records[(target_namespace, target_key)] = self.records.pop(source)
        return True

    def list(self, namespace):
        return [
            (key, copy.deepcopy(value))
            for (record_namespace, key), value in sorted(self.records.items())
            if record_namespace == namespace
        ]

    def delete_namespace(self, namespace):
        targets = [address for address in self.records if address[0] == namespace]
        for address in targets:
            del self.records[address]
        return len(targets)


class _RecordCursor:
    def __init__(self):
        self.records = {}
        self.result = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        params = tuple(params or ())
        self.rowcount = 0
        if sql.startswith("INSERT INTO flow_authority_records"):
            address = params[:3]
            self.records[address] = params[3]
            self.rowcount = 1
            self.result = None
        elif sql.startswith("SELECT document FROM flow_authority_records"):
            value = self.records.get(params[:3])
            self.result = (value,) if value is not None else None
        elif sql.startswith("SELECT pg_advisory_xact_lock"):
            self.result = (None,)
        else:  # pragma: no cover - this double serves only put/get
            raise AssertionError(sql)

    def fetchone(self):
        return self.result


class _RecordWorld:
    _rls = True

    def __init__(self):
        self.cursor = _RecordCursor()

    @contextmanager
    def _tx(self):
        yield self.cursor


class _LockCursor:
    def __init__(self):
        self.statements = []
        self.result = None
        self.closed = False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        self.result = (True,)

    def fetchone(self):
        return self.result

    def close(self):
        self.closed = True


class _LockConnection:
    def __init__(self, cursor=None):
        self.cursor_value = cursor or _LockCursor()
        self.closed = False

    def cursor(self):
        return self.cursor_value

    def close(self):
        self.closed = True


@pytest.fixture
def shared_authority(monkeypatch, tmp_path):
    authority = _MemoryAuthority()
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / ".maverick"))
    monkeypatch.setattr(store, "_flow_authority", lambda: authority)
    return authority


def _flow(flow_id="billing", *, owner="user:alice", name="Billing"):
    node = FlowNode(id="draft", kind="agent", brief="Draft the controlled report")
    return Flow(
        id=flow_id,
        name=name,
        start=node.id,
        nodes={node.id: node},
        owner=owner,
    )


def test_every_authoritative_record_routes_to_one_shared_store(
    shared_authority, monkeypatch, tmp_path,
):
    child = store.save_flow(_flow("child"), expected_version=0)
    root = _flow()
    root.nodes["draft"].kind = "subflow"
    root.nodes["draft"].brief = ""
    root.nodes["draft"].flow_ref = child.id
    saved = store.save_flow(root, expected_version=0)
    store.record_flow_schema(
        saved.id,
        {"result": {"type": "object"}},
        revision=saved.revision,
    )
    updated = saved.copy()
    updated.name = "Billing close"
    saved = store.save_flow(updated, expected_version=saved.version)
    release = store.publish_flow(
        saved.id,
        expected_version=saved.version,
        expected_revision=saved.revision,
    )

    run = store.FlowRun(
        run_id="a" * 32,
        flow_id=saved.id,
        status="indeterminate",
        owner=saved.owner,
        cursor="draft",
        error="provider acknowledgement was lost",
        definition_digest=release["definition_digest"],
        release_digest=release["release_digest"],
        release_id=release["release_id"],
        definition_version=release["definition_version"],
        definition_revision=release["definition_revision"],
        subflow_digests=release["subflow_digests"],
        idem_key="invoice:1",
    )
    store.save_run(run)
    store.save_idempotency_claim(
        saved.id,
        run.idem_key,
        run.run_id,
        release_id=release["release_id"],
        owner=saved.owner,
    )
    monkeypatch.setattr(
        "maverick.audit.required_audit_event",
        lambda *_args, **_kwargs: True,
    )
    resolution = reconciliation.resolve_case(
        run.run_id,
        reconciliation.ResolutionRequest(
            resolution_id="b" * 32,
            disposition="not_committed",
            reconciliation_method="provider.lookup",
            reconciliation_summary="Provider has no matching operation.",
            evidence_references=("provider:ledger/invoice-1",),
            evidence_sha256="c" * 64,
            external_reference="",
            compensating_action="No compensation is required.",
            replacement_authorized=True,
        ),
        adjudicated_by="user:alice",
        authority_source="dashboard_rbac_admin",
    )

    namespaces = {namespace for namespace, _key in shared_authority.records}
    assert {
        "definitions",
        f"history:{store._safe_id(saved.id)}",
        "idempotency",
        "indeterminate-resolutions",
        "objects",
        "published",
        "runs",
        "schema",
    }.issubset(namespaces)
    assert store.load_published_bundle(saved.id)[1]["release_id"] == release["release_id"]
    assert store.find_run_by_idem(
        saved.id,
        run.idem_key,
        release_id=release["release_id"],
        owner=saved.owner,
    ).run_id == run.run_id
    assert reconciliation.load_resolution(run.run_id) == resolution
    assert not (tmp_path / ".maverick" / "flows").exists()


def test_definition_transaction_rolls_back_partial_history_write(shared_authority):
    original = store.save_flow(_flow(), expected_version=0)
    replacement = original.copy()
    replacement.name = "Changed"
    shared_authority.fail_definition_write = True

    with pytest.raises(RuntimeError, match="disconnect"):
        store.save_flow(replacement, expected_version=original.version)

    shared_authority.fail_definition_write = False
    assert store.load_flow(original.id).version == 1
    assert [flow.version for flow in store.list_versions(original.id)] == [1]


def test_node_transition_and_run_projection_roll_back_as_one_transaction(
    shared_authority,
):
    saved = store.save_flow(_flow("transition-atomic"), expected_version=0)
    definition_digest, definition_version, children = store.snapshot_flow_bundle(saved)
    release_digest = store.release_digest_for(definition_digest, children)
    run = store.FlowRun(
        run_id="d" * 32,
        flow_id=saved.id,
        status="running",
        definition_digest=definition_digest,
        release_digest=release_digest,
        release_id=release_digest,
        definition_version=definition_version,
        definition_revision=saved.revision,
        subflow_digests=children,
    )
    store.save_run(run)
    shared_authority.fail_run_write = True

    with pytest.raises(RuntimeError, match="projection disconnect"):
        store.append_run_transition(
            run,
            segment_id="initial",
            node_id="write",
            node_kind="action",
            tool_name="web_search",
            node_status="done",
            input_revision_sha256="1" * 64,
            receipt=None,
            output_sha256="2" * 64,
            next_cursor=None,
        )

    shared_authority.fail_run_write = False
    loaded = store.load_run(run.run_id)
    assert loaded is not None
    assert loaded.transition_sequence == 0
    assert loaded.transition_sha256 == ""
    assert not any(
        namespace == "run-transitions"
        for namespace, _key in shared_authority.records
    )


def test_concurrent_definition_cas_has_one_winner(shared_authority):
    original = store.save_flow(_flow(), expected_version=0)
    barrier = threading.Barrier(2)

    def update(name):
        candidate = original.copy()
        candidate.name = name
        barrier.wait()
        try:
            return store.save_flow(candidate, expected_version=1).name
        except store.FlowVersionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, ("First", "Second")))

    assert outcomes.count("conflict") == 1
    assert store.load_flow(original.id).version == 2
    assert [flow.version for flow in store.list_versions(original.id)] == [1, 2]


def test_authority_failure_never_reads_a_stale_local_definition(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / ".maverick"))
    local = tmp_path / ".maverick" / "flows" / "billing.json"
    local.parent.mkdir(parents=True)
    local.write_text("{}", encoding="utf-8")

    def unavailable():
        raise FlowAuthorityUnavailable("database unavailable")

    monkeypatch.setattr(store, "_flow_authority", unavailable)
    with pytest.raises(FlowAuthorityUnavailable, match="database unavailable"):
        store.load_flow("billing")


def test_explicit_production_refuses_local_flow_authority(monkeypatch):
    import maverick.production_scope as production_scope
    import maverick.world_model_backends.postgres as postgres

    monkeypatch.setattr(production_scope, "production_surface_required", lambda: True)
    monkeypatch.setattr(postgres, "is_postgres_configured", lambda: False)
    monkeypatch.setattr(store, "_authority_instance", None)

    with pytest.raises(FlowAuthorityUnavailable, match="require.*Postgres"):
        store._flow_authority()


def test_rls_authority_refuses_an_unbound_tenant(monkeypatch):
    import maverick.production_scope as production_scope
    import maverick.world_model_backends.postgres as postgres

    class _World:
        _rls = True

        @contextmanager
        def _tx(self):  # pragma: no cover - tenant refusal precedes a query
            yield None

    monkeypatch.setattr(production_scope, "production_surface_required", lambda: False)
    monkeypatch.setattr(postgres, "_active_tenant", lambda: None)
    authority = PostgresFlowAuthority(_World())

    with pytest.raises(FlowAuthorityUnavailable, match="unbound"):
        authority.get("definitions", "billing.json")


def test_postgres_documents_are_sealed_and_tenant_scoped(monkeypatch):
    import maverick.production_scope as production_scope
    import maverick.world_model_backends.postgres as postgres

    monkeypatch.setattr(production_scope, "production_surface_required", lambda: False)
    monkeypatch.setattr(postgres, "_active_tenant", lambda: "tenant-a")
    monkeypatch.setattr(postgres, "_seal", lambda value: f"sealed:{value}")
    monkeypatch.setattr(postgres, "_unseal", lambda value: str(value)[7:])
    world = _RecordWorld()
    authority = PostgresFlowAuthority(world)

    authority.put("definitions", "billing.json", {"id": "billing"})

    stored = world.cursor.records[("tenant-a", "definitions", "billing.json")]
    assert stored.startswith("sealed:")
    assert authority.get("definitions", "billing.json") == {"id": "billing"}

    with pytest.raises(store.FlowVersionConflict, match="changed"):
        with authority.transaction_lock("definitions"):
            raise store.FlowVersionConflict("definition changed")


def test_postgres_driver_failure_is_typed_as_authority_unavailable(monkeypatch):
    import maverick.production_scope as production_scope
    import maverick.world_model_backends.postgres as postgres

    driver_error = type("OperationalError", (RuntimeError,), {})
    driver_error.__module__ = "psycopg.errors"

    class _FailCursor:
        def execute(self, *_args, **_kwargs):
            raise driver_error("connection lost")

    class _FailWorld:
        _rls = True

        @contextmanager
        def _tx(self):
            yield _FailCursor()

    monkeypatch.setattr(production_scope, "production_surface_required", lambda: False)
    monkeypatch.setattr(postgres, "_active_tenant", lambda: "tenant-a")
    authority = PostgresFlowAuthority(_FailWorld())

    with pytest.raises(FlowAuthorityUnavailable, match="transaction") as caught:
        authority.get("definitions", "billing.json")
    assert isinstance(caught.value.__cause__, driver_error)


def test_session_lock_releases_connection_after_executor_crash(monkeypatch):
    import maverick.production_scope as production_scope
    import maverick.world_model_backends.postgres as postgres

    connections = []
    connect_kwargs = []

    def connect(*_args, **kwargs):
        connection = _LockConnection()
        connections.append(connection)
        connect_kwargs.append(kwargs)
        return connection

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=connect))
    monkeypatch.setattr(production_scope, "production_surface_required", lambda: False)
    monkeypatch.setattr(postgres, "_active_tenant", lambda: "tenant-a")
    monkeypatch.setattr(postgres, "_pool_size", lambda: 2)
    monkeypatch.setattr(postgres, "_connect_timeout_seconds", lambda: 1)
    world = types.SimpleNamespace(_tx=lambda: None, _dsn="postgres://db", _rls=True)
    authority = PostgresFlowAuthority(world)

    with pytest.raises(RuntimeError, match="executor crashed"):
        with authority.session_lock("run:abc"):
            with authority.session_lock("run:abc"):
                raise RuntimeError("executor crashed")

    assert len(connections) == 1
    assert connect_kwargs == [{
        "autocommit": True,
        "connect_timeout": 1,
        "options": "-c statement_timeout=5000",
    }]
    connection = connections[0]
    statements = [sql for sql, _params in connection.cursor_value.statements]
    assert statements == [
        "SELECT pg_try_advisory_lock(%s)",
        "SELECT pg_advisory_unlock(%s)",
    ]
    assert connection.cursor_value.closed is True
    assert connection.closed is True


def test_session_lock_cleans_up_when_unlock_reports_lost_ownership(monkeypatch):
    import maverick.production_scope as production_scope
    import maverick.world_model_backends.postgres as postgres

    class _LostLockCursor(_LockCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if sql.startswith("SELECT pg_advisory_unlock"):
                self.result = (False,)

    connection = _LockConnection(_LostLockCursor())
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        types.SimpleNamespace(connect=lambda *_args, **_kwargs: connection),
    )
    monkeypatch.setattr(production_scope, "production_surface_required", lambda: False)
    monkeypatch.setattr(postgres, "_active_tenant", lambda: "tenant-a")
    monkeypatch.setattr(postgres, "_pool_size", lambda: 1)
    monkeypatch.setattr(postgres, "_connect_timeout_seconds", lambda: 1)
    world = types.SimpleNamespace(_tx=lambda: None, _dsn="postgres://db", _rls=True)
    authority = PostgresFlowAuthority(world)

    with pytest.raises(FlowAuthorityUnavailable, match="ownership was lost"):
        with authority.session_lock("run:abc"):
            pass

    assert connection.cursor_value.closed is True
    assert connection.closed is True
    assert authority._lock_slots.acquire(blocking=False) is True
    authority._lock_slots.release()


def test_immutable_snapshot_conflict_is_never_overwritten(shared_authority):
    saved = store.save_flow(_flow(), expected_version=0)
    digest, _version, _children = store.snapshot_flow_bundle(saved)
    key = f"{digest}.json"

    with pytest.raises(FlowAuthorityIntegrityError, match="immutable"):
        shared_authority.put(
            "objects",
            key,
            {"id": "tampered"},
            immutable=True,
        )
    assert store.load_flow_snapshot(digest).id == saved.id
