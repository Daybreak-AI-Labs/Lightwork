"""Cross-replica evidence-integrity fencing for Security/GRC records."""

from __future__ import annotations

import contextlib
import contextvars
import copy
import json
import threading
from collections import defaultdict
from types import SimpleNamespace

import pytest
from maverick import security_ops
from maverick.authority_documents import AuthorityDocumentStore
from maverick.governed_records import (
    GovernedMutationFence,
    PostgresGovernedRecordBackend,
    RecordConflict,
)

_REPLICA_ROOT = contextvars.ContextVar("test_security_replica_root", default="root-a")


class _SharedDatabase:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.now = 1_000.0
        self.authority: dict = {}
        self.authority_history: list[dict] = []
        self.rows: dict[str, dict[str, dict]] = defaultdict(dict)
        self.counters: dict[str, int] = defaultdict(int)


class _SharedAuthority:
    def __init__(self, database: _SharedDatabase) -> None:
        self.database = database
        self.calls: list[tuple[str, str]] = []
        self._lose_after_commit: dict[str, int] = defaultdict(int)

    def lose_next_response(self, action: str) -> None:
        self._lose_after_commit[action] += 1

    def mutate_with_authoritative_time(self, callback, *, action: str, actor: str):
        with self.database.lock:
            result = callback(self.database.authority, self.database.now)
            self.calls.append((action, actor))
            self.database.authority_history.append(
                copy.deepcopy(self.database.authority)
            )
            if self._lose_after_commit[action]:
                self._lose_after_commit[action] -= 1
                raise TimeoutError("committed authority response was lost")
            return result

    def mutation_fence(self, callback) -> GovernedMutationFence:
        def _validate(_record: dict, now: float) -> None:
            callback(copy.deepcopy(self.database.authority), now)

        return GovernedMutationFence(
            namespace="security_evidence_integrity",
            prefix="SEIF",
            record_id="SEIF-state",
            authority_scope="tenant",
            validate=_validate,
        )


class _SharedCollection:
    def __init__(self, database: _SharedDatabase, namespace: str, prefix: str) -> None:
        self.database = database
        self.namespace = namespace
        self.prefix = prefix
        self.before_transaction = None
        self.after_mutate_before_commit = None
        self.fenced_writes = 0

    def seed(self, record: dict) -> dict:
        value = copy.deepcopy(record)
        value.setdefault("revision", 1)
        value.setdefault("created_at", self.database.now)
        value.setdefault("updated_at", self.database.now)
        with self.database.lock:
            self.database.rows[self.namespace][value["id"]] = value
        return copy.deepcopy(value)

    def new_id(self) -> str:
        with self.database.lock:
            self.database.counters[self.prefix] += 1
            return f"{self.prefix}-{self.database.counters[self.prefix]}"

    def get(self, record_id: str) -> dict | None:
        with self.database.lock:
            record = self.database.rows[self.namespace].get(record_id)
            return None if record is None else copy.deepcopy(record)

    def records(self) -> list[dict]:
        with self.database.lock:
            return [
                copy.deepcopy(record)
                for record in self.database.rows[self.namespace].values()
            ]

    def create_record(
        self,
        record: dict,
        *,
        action: str,
        actor: str,
        audit: bool = True,
        fence=None,
    ) -> dict:
        del action, actor, audit
        if self.before_transaction is not None:
            self.before_transaction()
        with self.database.lock:
            if fence is not None:
                self.fenced_writes += 1
                fence.validate({}, self.database.now)
            value = copy.deepcopy(record)
            if value["id"] in self.database.rows[self.namespace]:
                raise RecordConflict("record already exists")
            value["revision"] = 1
            value.setdefault("created_at", self.database.now)
            value.setdefault("updated_at", self.database.now)
            if self.after_mutate_before_commit is not None:
                self.after_mutate_before_commit()
            self.database.rows[self.namespace][value["id"]] = value
            return copy.deepcopy(value)

    def update_record(
        self,
        record_id: str,
        mutate,
        *,
        action: str,
        actor: str,
        expected_revision: int | None = None,
        audit: bool = True,
        fence=None,
    ) -> dict | None:
        del action, actor, audit
        if self.before_transaction is not None:
            self.before_transaction()
        with self.database.lock:
            if fence is not None:
                self.fenced_writes += 1
                fence.validate({}, self.database.now)
            current = self.database.rows[self.namespace].get(record_id)
            if current is None:
                return None
            if expected_revision is not None and current["revision"] != expected_revision:
                raise RecordConflict("record changed")
            updated = copy.deepcopy(current)
            mutate(updated)
            updated["revision"] = current["revision"] + 1
            if self.after_mutate_before_commit is not None:
                self.after_mutate_before_commit()
            self.database.rows[self.namespace][record_id] = updated
            return copy.deepcopy(updated)


class _SecurityFixture:
    def __init__(self, monkeypatch) -> None:
        self.database = _SharedDatabase()
        self.authority = _SharedAuthority(self.database)
        self.controls = _SharedCollection(self.database, "security_controls", "CTL")
        self.evidence = _SharedCollection(self.database, "security_evidence", "EVD")
        self.risks = _SharedCollection(self.database, "security_risks", "RSK")
        self.engagements = _SharedCollection(self.database, "security_audits", "AUD")
        self.local_lock_roots: list[str] = []

        monkeypatch.setattr(
            security_ops,
            "_evidence_integrity_backend_kind",
            lambda: "postgres",
        )
        monkeypatch.setattr(
            security_ops,
            "_EVIDENCE_INTEGRITY_AUTHORITY",
            self.authority,
        )
        monkeypatch.setattr(security_ops, "_CONTROLS", self.controls)
        monkeypatch.setattr(security_ops, "_EVIDENCE", self.evidence)
        monkeypatch.setattr(security_ops, "_RISKS", self.risks)
        monkeypatch.setattr(security_ops, "_ENGAGEMENTS", self.engagements)

        def _independent_local_lock():
            self.local_lock_roots.append(_REPLICA_ROOT.get())
            return contextlib.nullcontext()

        monkeypatch.setattr(
            security_ops,
            "_local_evidence_integrity_lock",
            _independent_local_lock,
        )

    def approved_evidence(self, evidence_id: str = "EVD-approved") -> dict:
        return self.evidence.seed(
            {
                "id": evidence_id,
                "title": "reviewed observation",
                "status": "approved",
                "verdicts": [
                    {
                        "control_id": "CTRL-1",
                        "register_control_id": "CTL-1",
                        "status": "present",
                    }
                ],
                "decision": {"decision": "approved"},
            }
        )

    def control(self) -> dict:
        return self.controls.seed(
            {
                "id": "CTL-1",
                "canonical_id": "CTRL-1",
                "control_id": "CTRL-1",
                "title": "Test control",
                "implementation_status": "not_started",
                "evidence_ids": [],
            }
        )

    def engagement(self, *, with_request: bool = False) -> dict:
        requests = []
        if with_request:
            requests.append(
                {
                    "id": "REQ-1",
                    "status": "open",
                    "evidence_ids": [],
                }
            )
        return self.engagements.seed(
            {
                "id": "AUD-1",
                "name": "Audit",
                "status": "planned",
                "evidence_requests": requests,
                "control_tests": [],
                "findings": [],
            }
        )


@pytest.fixture
def shared_security(monkeypatch):
    fixture = _SecurityFixture(monkeypatch)
    token = security_ops._EVIDENCE_INTEGRITY_PERMIT.set(None)
    try:
        yield fixture
    finally:
        security_ops._EVIDENCE_INTEGRITY_PERMIT.reset(token)
    assert fixture.local_lock_roots == []


def _start(replica: str, function):
    outcome: dict[str, object] = {}
    done = threading.Event()

    def _run() -> None:
        token = _REPLICA_ROOT.set(replica)
        try:
            outcome["result"] = function()
        except BaseException as exc:  # noqa: BLE001 - asserted by the test
            outcome["error"] = exc
        finally:
            _REPLICA_ROOT.reset(token)
            done.set()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread, done, outcome


def _expire_active_lease(fixture: _SecurityFixture) -> None:
    # The holder is deliberately paused outside the target transaction, so a
    # second replica may take over using database time.
    fixture.database.now = float(fixture.database.authority["expires_at"]) + 1.0


def _reject(fixture: _SecurityFixture, evidence: dict):
    token = _REPLICA_ROOT.set("root-b")
    try:
        return security_ops.decide_evidence(
            evidence["id"],
            "rejected",
            "reviewer withdrew support",
            "reviewer-b",
            evidence["revision"],
        )
    finally:
        _REPLICA_ROOT.reset(token)


def test_apply_evidence_stale_replica_cannot_commit_after_revoke_takeover(
    shared_security,
):
    fixture = shared_security
    evidence = fixture.approved_evidence()
    control = fixture.control()
    checked = threading.Event()
    resume = threading.Event()

    def _pause_after_support_check() -> None:
        checked.set()
        assert resume.wait(5)

    fixture.controls.before_transaction = _pause_after_support_check
    thread, _done, outcome = _start(
        "root-a",
        lambda: security_ops.apply_evidence_to_control(
            control["id"],
            evidence["id"],
            "implemented",
            "operator-a",
            control["revision"],
        ),
    )
    assert checked.wait(5)
    _expire_active_lease(fixture)
    rejected = _reject(fixture, evidence)
    assert rejected is not None and rejected["status"] == "rejected"
    resume.set()
    thread.join(5)

    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), security_ops.SecurityStateError)
    stored = fixture.controls.get(control["id"])
    assert stored is not None
    assert stored["implementation_status"] == "not_started"
    assert stored["evidence_ids"] == []


def test_register_risk_stale_replica_cannot_commit_after_revoke_takeover(
    shared_security,
):
    fixture = shared_security
    evidence = fixture.approved_evidence()
    checked = threading.Event()
    resume = threading.Event()

    def _pause_after_support_check() -> None:
        checked.set()
        assert resume.wait(5)

    fixture.risks.before_transaction = _pause_after_support_check
    thread, _done, outcome = _start(
        "root-a",
        lambda: security_ops.register_risk(
            "Shared risk",
            4,
            5,
            "risk-owner",
            likelihood_rationale="Observed repeatedly",
            impact_rationale="Material service impact",
            evidence_ids=[evidence["id"]],
            created_by="analyst-a",
        ),
    )
    assert checked.wait(5)
    _expire_active_lease(fixture)
    rejected = _reject(fixture, evidence)
    assert rejected is not None and rejected["status"] == "rejected"
    resume.set()
    thread.join(5)

    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), security_ops.SecurityStateError)
    assert fixture.risks.records() == []


def test_record_control_test_holds_authority_through_support_check_and_commit(
    shared_security,
):
    fixture = shared_security
    evidence = fixture.approved_evidence()
    engagement = fixture.engagement()
    checked = threading.Event()
    resume = threading.Event()

    def _pause_after_mutator_support_check() -> None:
        checked.set()
        assert resume.wait(5)

    # The evidence check lives in the governed target mutator. The fake shared
    # transaction holds the authority-row lock while this barrier is stopped.
    fixture.engagements.after_mutate_before_commit = (
        _pause_after_mutator_support_check
    )
    thread_a, _done_a, outcome_a = _start(
        "root-a",
        lambda: security_ops.record_control_test(
            engagement["id"],
            "CTRL-1",
            "Inspect the reviewed configuration",
            "pass",
            evidence_ids=[evidence["id"]],
            tested_by="tester-a",
            expected_revision=engagement["revision"],
        ),
    )
    assert checked.wait(5)
    _expire_active_lease(fixture)
    thread_b, done_b, outcome_b = _start(
        "root-b",
        lambda: security_ops.decide_evidence(
            evidence["id"],
            "rejected",
            "reviewer withdrew support",
            "reviewer-b",
            evidence["revision"],
        ),
    )
    assert not done_b.wait(0.1)
    resume.set()
    thread_a.join(5)
    thread_b.join(5)

    assert not thread_a.is_alive() and not thread_b.is_alive()
    assert "error" not in outcome_a
    assert isinstance(outcome_b.get("error"), security_ops.SecurityTransitionError)
    stored = fixture.engagements.get(engagement["id"])
    assert stored is not None
    assert stored["control_tests"][0]["evidence_ids"] == [evidence["id"]]
    assert fixture.evidence.get(evidence["id"])["status"] == "approved"


def test_lease_takeover_rejects_stale_holder_and_stale_release(shared_security):
    fixture = shared_security
    first = security_ops._acquire_evidence_integrity_permit()
    _expire_active_lease(fixture)
    second = security_ops._acquire_evidence_integrity_permit()

    assert second.generation == first.generation + 1
    token = security_ops._EVIDENCE_INTEGRITY_PERMIT.set(first)
    try:
        stale_fence = security_ops._evidence_integrity_fence()
        assert stale_fence is not None
        with fixture.database.lock, pytest.raises(security_ops.SecurityStateError):
            stale_fence.validate({}, fixture.database.now)
    finally:
        security_ops._EVIDENCE_INTEGRITY_PERMIT.reset(token)

    security_ops._release_evidence_integrity_permit(first)
    assert fixture.database.authority["operation"] == second.operation
    assert fixture.database.authority["status"] == "active"
    security_ops._release_evidence_integrity_permit(second)
    assert fixture.database.authority["status"] == "released"


def test_lease_lost_responses_converge_without_raw_operation_data(shared_security):
    fixture = shared_security
    fixture.authority.lose_next_response("evidence_integrity_acquire")
    permit = security_ops._acquire_evidence_integrity_permit()
    assert permit.generation == 1
    assert [call[0] for call in fixture.authority.calls].count(
        "evidence_integrity_acquire"
    ) == 2

    fixture.authority.lose_next_response("evidence_integrity_release")
    security_ops._release_evidence_integrity_permit(permit)
    assert fixture.database.authority["status"] == "released"
    assert [call[0] for call in fixture.authority.calls].count(
        "evidence_integrity_release"
    ) == 2

    evidence = fixture.approved_evidence("EVD-customer-secret")
    control = fixture.control()
    applied = security_ops.apply_evidence_to_control(
        control["id"],
        evidence["id"],
        "implemented",
        "operator-sensitive",
        control["revision"],
    )
    assert applied is not None

    serialized = json.dumps(
        {
            "history": fixture.database.authority_history,
            "calls": fixture.authority.calls,
        },
        sort_keys=True,
    )
    assert "EVD-customer-secret" not in serialized
    assert "reviewed observation" not in serialized
    assert "operator-sensitive" not in serialized


def test_other_evidence_sensitive_writes_also_receive_transaction_fence(
    shared_security,
):
    fixture = shared_security
    evidence = fixture.approved_evidence()
    risk = fixture.risks.seed(
        {
            "id": "RSK-1",
            "title": "Existing risk",
            "status": "open",
            "evidence_ids": [evidence["id"]],
        }
    )
    engagement = fixture.engagement(with_request=True)

    security_ops.upsert_control(
        {
            "title": "New custom control",
            "framework": "custom",
            "owner": "control-owner",
        },
        updated_by="control-owner",
    )
    security_ops.set_risk_treatment(
        risk["id"],
        "mitigate",
        "Apply compensating control",
        2,
        3,
        "risk-owner",
        "risk-owner",
        risk["revision"],
        residual_likelihood_rationale="Control reduces recurrence",
        residual_impact_rationale="Blast radius is constrained",
        evidence_ids=[evidence["id"]],
    )
    accepted = security_ops.update_evidence_request(
        engagement["id"],
        "REQ-1",
        "accepted",
        evidence_ids=[evidence["id"]],
        updated_by="auditor",
        expected_revision=engagement["revision"],
    )
    assert accepted is not None
    completed = security_ops.update_audit_engagement_status(
        engagement["id"],
        "complete",
        "auditor",
        accepted["revision"],
    )
    assert completed is not None

    assert fixture.controls.fenced_writes >= 1
    assert fixture.risks.fenced_writes == 1
    assert fixture.engagements.fenced_writes == 2


def test_postgres_checks_global_authority_and_restores_tenant_in_target_transaction(
    monkeypatch,
):
    events: list[str] = []
    rows = {
        ("security_evidence_integrity", "SEIF-state"): {
            "id": "SEIF-state",
            "revision": 4,
            "schema_version": 1,
            "document": {"permit": "generation-4"},
            "created_at": 10.0,
            "updated_at": 20.0,
        },
        ("target_namespace", "TGT-1"): {
            "id": "TGT-1",
            "revision": 7,
            "value": "before",
            "created_at": 10.0,
            "updated_at": 20.0,
        },
    }

    class _Cursor:
        rowcount = 0

        def __init__(self) -> None:
            self.pending = None

        def execute(self, sql: str, params=None) -> None:
            normalized = " ".join(sql.split())
            if normalized.startswith("SELECT set_config"):
                assert params is not None
                scope = "global" if str(params[0]).startswith("G") else str(params[0])
                events.append(f"set-scope:{scope}")
                self.pending = (str(params[0]),)
                return
            if normalized.startswith("SELECT revision"):
                assert params is not None
                key = (str(params[1]), str(params[2]))
                events.append(f"select:{key[0]}")
                record = rows.get(key)
                if record is None:
                    self.pending = None
                    return
                self.pending = (
                    record["revision"],
                    json.dumps(record, sort_keys=True, separators=(",", ":")),
                    record["created_at"],
                    record["updated_at"],
                )
                return
            if normalized.startswith("SELECT extract"):
                events.append("database-time")
                self.pending = (1_005.0,)
                return
            if normalized.startswith("UPDATE governed_records"):
                events.append("target-update")
                self.rowcount = 1
                self.pending = None
                return
            if normalized.startswith("INSERT INTO governed_records"):
                events.append("target-insert")
                self.rowcount = 1
                self.pending = None
                return
            raise AssertionError(f"unexpected SQL: {normalized}")

        def fetchone(self):
            return self.pending

    cursor = _Cursor()

    @contextlib.contextmanager
    def _operation(_action: str):
        yield

    @contextlib.contextmanager
    def _transaction(tenant: str):
        assert tenant == "tenant-a"
        events.append("transaction-enter")
        try:
            yield cursor
        except BaseException:
            events.append("transaction-rollback")
            raise
        else:
            events.append("transaction-commit")

    backend = object.__new__(PostgresGovernedRecordBackend)
    backend.namespace = "target_namespace"
    backend.prefix = "TGT"
    backend._authority_scope = "tenant"
    backend._require_encryption = False
    backend._tenant_key = lambda: "tenant-a"
    backend._operation = _operation
    backend._transaction = _transaction
    monkeypatch.setattr(
        "maverick.crypto_at_rest.deployment_key_scope",
        contextlib.nullcontext,
    )

    authority = object.__new__(AuthorityDocumentStore)
    authority._records = SimpleNamespace(
        namespace="security_evidence_integrity",
        prefix="SEIF",
        authority_scope="deployment_global",
    )
    authority._record_id = "SEIF-state"

    def _validate(document: dict, now: float) -> None:
        events.append("validate-fence")
        assert document == {"permit": "generation-4"}
        assert now == 1_005.0

    fence = authority.mutation_fence(_validate)
    saved = backend.update(
        "TGT-1",
        lambda record: record.__setitem__("value", "after"),
        expected_revision=7,
        fence=fence,
    )

    assert saved is not None and saved["value"] == "after"
    assert events == [
        "transaction-enter",
        "set-scope:global",
        "select:security_evidence_integrity",
        "database-time",
        "validate-fence",
        "set-scope:tenant-a",
        "select:target_namespace",
        "target-update",
        "transaction-commit",
    ]

    events.clear()
    created = backend.save(
        {
            "id": "TGT-2",
            "value": "created",
            "created_at": 30.0,
            "updated_at": 30.0,
        },
        expected_revision=0,
        fence=fence,
    )
    assert created["value"] == "created"
    assert events == [
        "transaction-enter",
        "set-scope:global",
        "select:security_evidence_integrity",
        "database-time",
        "validate-fence",
        "set-scope:tenant-a",
        "select:target_namespace",
        "target-insert",
        "transaction-commit",
    ]

    events.clear()

    def _reject(_document: dict, _now: float) -> None:
        events.append("reject-stale-fence")
        raise security_ops.SecurityStateError("stale permit")

    rejected_fence = authority.mutation_fence(_reject)
    with pytest.raises(security_ops.SecurityStateError):
        backend.update(
            "TGT-1",
            lambda record: record.__setitem__("value", "forbidden"),
            expected_revision=7,
            fence=rejected_fence,
        )
    assert events == [
        "transaction-enter",
        "set-scope:global",
        "select:security_evidence_integrity",
        "database-time",
        "reject-stale-fence",
        "set-scope:tenant-a",
        "transaction-rollback",
    ]
