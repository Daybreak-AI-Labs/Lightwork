"""CAS and isolation tests for the shared authority-document adapter."""
from __future__ import annotations

import copy
import hashlib
import threading
import uuid

import pytest
from maverick import governed_records
from maverick.authority_documents import (
    AuthorityDocumentError,
    AuthorityDocumentStore,
)
from maverick.governed_records import (
    GovernedRecordBackendError,
    GovernedRecordStore,
    PostgresGovernedRecordBackend,
)
from maverick.privacy_ops import RecordConflict


class _MemoryBackend:
    kind = "memory"
    shared = True

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.clock_time = 1_234.5

    def new_id(self) -> str:
        return uuid.uuid4().hex

    def authoritative_time(self) -> float:
        return self.clock_time

    def save(self, record, *, expected_revision=None):
        with self.lock:
            record_id = record["id"]
            current = self.records.get(record_id)
            revision = int((current or {}).get("revision", 0))
            if expected_revision is not None and expected_revision != revision:
                raise RecordConflict("changed")
            saved = copy.deepcopy(record)
            saved["revision"] = revision + 1
            saved.setdefault("created_at", 1.0)
            saved.setdefault("updated_at", 1.0)
            self.records[record_id] = saved
            return copy.deepcopy(saved)

    def load(self, record_id):
        with self.lock:
            value = self.records.get(record_id)
            return copy.deepcopy(value) if value is not None else None

    def list(self):
        with self.lock:
            return copy.deepcopy(list(self.records.values()))

    def iter_record_ids(self, *, start_after="", limit=100):
        with self.lock:
            values = sorted(self.records)
        for record_id in values[:limit]:
            yield record_id, record_id

    def update(
        self,
        record_id,
        mutate,
        *,
        expected_revision=None,
        metadata_only=False,
    ):
        with self.lock:
            current = self.records.get(record_id)
            if current is None:
                return None
            revision = int(current["revision"])
            if expected_revision is not None and expected_revision != revision:
                raise RecordConflict("changed")
            saved = copy.deepcopy(current)
            mutate(saved)
            saved["revision"] = revision if metadata_only else revision + 1
            self.records[record_id] = saved
            return copy.deepcopy(saved)


class _TracingBackend(_MemoryBackend):
    def __init__(self, *, kind: str, shared: bool) -> None:
        super().__init__()
        self.kind = kind
        self.shared = shared
        self.calls: list[str] = []
        self.after_load = None

    def save(self, record, *, expected_revision=None):
        self.calls.append("save")
        return super().save(record, expected_revision=expected_revision)

    def load(self, record_id):
        self.calls.append("load")
        value = super().load(record_id)
        hook = self.after_load
        if hook is not None:
            self.after_load = None
            hook()
        return value

    def list(self):
        self.calls.append("list")
        return super().list()

    def iter_record_ids(self, *, start_after="", limit=100):
        self.calls.append("iter")
        yield from super().iter_record_ids(start_after=start_after, limit=limit)

    def update(
        self,
        record_id,
        mutate,
        *,
        expected_revision=None,
        metadata_only=False,
    ):
        self.calls.append("update")
        return super().update(
            record_id,
            mutate,
            expected_revision=expected_revision,
            metadata_only=metadata_only,
        )


class _ConflictAfterTimedCallbackBackend(_MemoryBackend):
    """Postgres-shaped seam that loses once after invoking the callback."""

    kind = "postgres"
    shared = True

    def __init__(self) -> None:
        super().__init__()
        self.clock_time = 100.0
        self.conflict_once = False
        self.events: list[str] = []

    def authoritative_time(self) -> float:
        self.events.append("clock")
        return self.clock_time

    def update(
        self,
        record_id,
        mutate,
        *,
        expected_revision=None,
        metadata_only=False,
    ):
        with self.lock:
            current = self.records.get(record_id)
            if current is None:
                return None
            revision = int(current["revision"])
            if expected_revision is not None and expected_revision != revision:
                raise RecordConflict("changed")
            self.events.append("row_lock")
            saved = copy.deepcopy(current)
            mutate(saved)
            if self.conflict_once:
                self.conflict_once = False
                self.clock_time = 200.0
                self.events.append("conflict")
                raise RecordConflict("lost after callback")
            saved["revision"] = revision if metadata_only else revision + 1
            self.records[record_id] = saved
            self.events.append("commit")
            return copy.deepcopy(saved)


def _store(backend=None):
    return AuthorityDocumentStore(
        "test_authority_document",
        "TAD",
        "test_authority",
        backend=backend or _MemoryBackend(),
    )


def test_document_create_update_and_defensive_read(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    store = _store()

    assert store.read() == {}
    assert store.mutate(
        lambda document: document.setdefault("enabled", True),
        action="initialize",
    ) is True
    first = store.snapshot()
    assert first.revision == 1
    assert first.document == {"enabled": True}

    first.document["enabled"] = False
    assert store.read() == {"enabled": True}


def test_document_exposes_selected_backend_authoritative_time():
    backend = _MemoryBackend()
    backend.clock_time = 9_876.5

    assert _store(backend).authoritative_time() == 9_876.5


def test_timed_mutation_reclocks_after_conflict_and_commits_only_retry(
    monkeypatch,
):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    backend = _ConflictAfterTimedCallbackBackend()
    store = _store(backend)
    monkeypatch.setattr(store._records, "_local_records_exist", lambda: False)
    monkeypatch.setattr(
        store._records,
        "_require_application_encryption",
        lambda _required, **_kwargs: None,
    )
    store.mutate(
        lambda document: document.setdefault("attempts", 0),
        action="initialize",
    )
    backend.events.clear()
    backend.conflict_once = True
    observed: list[float] = []

    def _advance(document: dict, now: float) -> float:
        observed.append(now)
        document["attempts"] += 1
        return now

    result = store.mutate_with_authoritative_time(
        _advance,
        action="timed_update",
    )

    assert observed == [100.0, 200.0]
    assert result == 200.0
    assert store.read() == {"attempts": 1}
    # The clock read occurs only after the backend seam reports that the row
    # lock/current revision is held, on both the discarded and committed try.
    assert backend.events[:6] == [
        "row_lock",
        "clock",
        "conflict",
        "row_lock",
        "clock",
        "commit",
    ]


def test_deployment_global_document_uses_global_audit_outside_ambient_tenant(
    monkeypatch,
):
    from maverick import audit
    from maverick.paths import reset_tenant, set_tenant

    events: list[dict] = []
    monkeypatch.setattr(
        audit,
        "record_global",
        lambda _kind, **payload: events.append(payload) or True,
    )
    monkeypatch.setattr(
        audit,
        "record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("deployment-global audit used tenant routing")
        ),
    )
    store = AuthorityDocumentStore(
        "test_global_authority",
        "TGA",
        "test_global_authority",
        backend=_MemoryBackend(),
        authority_scope="deployment_global",
    )
    token = set_tenant("ambient-customer")
    try:
        store.mutate(
            lambda document: document.setdefault("enabled", True),
            action="initialize",
        )
    finally:
        reset_tenant(token)

    assert len(events) == 1
    assert events[0]["tenant"] == ""


def test_deployment_global_row_key_is_reserved_and_tenant_independent():
    from maverick import governed_records as governed
    from maverick.paths import MAX_TENANT_SEGMENT_LENGTH, reset_tenant, set_tenant

    global_backend = PostgresGovernedRecordBackend(
        "test_global",
        "TGL",
        dsn="postgresql://db/test",
        require_bound_tenant=True,
        authority_scope="deployment_global",
    )
    tenant_backend = PostgresGovernedRecordBackend(
        "test_tenant",
        "TTN",
        dsn="postgresql://db/test",
        require_bound_tenant=True,
    )

    alpha = set_tenant("alpha")
    try:
        global_alpha = global_backend._tenant_key()
        assert tenant_backend._tenant_key() == "alpha"
    finally:
        reset_tenant(alpha)
    beta = set_tenant("beta")
    try:
        global_beta = global_backend._tenant_key()
        assert tenant_backend._tenant_key() == "beta"
    finally:
        reset_tenant(beta)

    assert global_alpha == global_beta == governed._DEPLOYMENT_GLOBAL_TENANT_KEY
    assert len(global_alpha) == MAX_TENANT_SEGMENT_LENGTH + 1
    assert global_alpha != governed._UNBOUND_TENANT_KEY


def test_deployment_global_legacy_guard_path_ignores_ambient_tenant(
    monkeypatch,
    tmp_path,
):
    from maverick.governed_records import LocalGovernedRecordBackend
    from maverick.paths import reset_tenant, set_tenant

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    backend = LocalGovernedRecordBackend(
        "global_guard",
        "GLB",
        authority_scope="deployment_global",
    )
    alpha = set_tenant("alpha")
    try:
        alpha_path = backend._store._dir()
    finally:
        reset_tenant(alpha)
    beta = set_tenant("beta")
    try:
        beta_path = backend._store._dir()
    finally:
        reset_tenant(beta)

    assert alpha_path == beta_path == tmp_path / "global_guard"


def test_deployment_global_required_store_rejects_tenant_scoped_override(
    monkeypatch,
):
    dsn = "postgresql://db/test"
    monkeypatch.setattr(
        governed_records,
        "_selection",
        lambda: governed_records._BackendSelection("postgres", dsn, False),
    )
    backend = _TracingBackend(kind="postgres", shared=True)
    backend.authority_scope = "tenant"
    store = AuthorityDocumentStore(
        "test_global_override",
        "TGO",
        "test_global_override",
        backend=backend,
        required_backend="postgres",
        authority_scope="deployment_global",
    )

    with pytest.raises(
        GovernedRecordBackendError,
        match="deployment-global backend",
    ):
        store.read()
    assert backend.calls == []


def test_concurrent_cas_updates_lose_nothing(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    store = _store()
    store.mutate(
        lambda document: document.setdefault("count", 0),
        action="initialize",
    )

    def increment() -> None:
        def _increment(document):
            document["count"] = int(document.get("count", 0)) + 1

        store.mutate(_increment, action="increment", max_retries=128)

    threads = [threading.Thread(target=increment) for _ in range(32)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert store.read()["count"] == 32


def test_malformed_shared_record_is_rejected(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    backend = _MemoryBackend()
    store = _store(backend)
    backend.records["TAD-state"] = {
        "id": "TAD-state",
        "revision": 1,
        "schema_version": 999,
        "document": {},
        "created_at": 1.0,
        "updated_at": 1.0,
    }

    try:
        store.read()
    except AuthorityDocumentError as exc:
        assert "schema version" in str(exc)
    else:  # pragma: no cover - explicit fail keeps assertion message useful
        raise AssertionError("malformed authority document was accepted")


def test_retry_bound_is_validated():
    store = _store()
    for invalid in (0, -1, True, 129):
        try:
            store.mutate(lambda _document: None, action="x", max_retries=invalid)
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"accepted invalid retry bound {invalid!r}")


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
@pytest.mark.parametrize("drift", ["local", "dsn", "enforcement-floor"])
def test_required_backend_pin_blocks_mid_mutation_drift_before_second_io(
    monkeypatch,
    existing,
    drift,
):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    dsn_a = "postgresql://user:secret-a@db-a.internal/lightwork"  # pragma: allowlist secret
    dsn_b = "postgresql://user:secret-b@db-b.internal/lightwork"  # pragma: allowlist secret
    selected = {
        "value": governed_records._BackendSelection("postgres", dsn_a, False),
    }
    primary = _TracingBackend(kind="postgres", shared=True)
    local = _TracingBackend(kind="local", shared=False)
    second = _TracingBackend(kind="postgres", shared=True)
    if existing:
        primary.records["TDP-state"] = {
            "created_at": 1.0,
            "document": {"count": 1},
            "id": "TDP-state",
            "revision": 1,
            "schema_version": 1,
            "updated_at": 1.0,
        }

    store = AuthorityDocumentStore(
        "test_document_pin",
        "TDP",
        "test_document_pin",
        required_backend="postgres",
    )
    records = store._records
    records._postgres_backend = primary
    records._postgres_key = hashlib.sha256(dsn_a.encode("utf-8")).hexdigest()
    records._local_backend = local
    monkeypatch.setattr(records, "_local_records_exist", lambda: False)
    monkeypatch.setattr(
        records,
        "_require_application_encryption",
        lambda _required, **_kwargs: None,
    )
    monkeypatch.setattr(governed_records, "_selection", lambda: selected["value"])
    constructed: list[str] = []

    def _unexpected_postgres(*_args, dsn, **_kwargs):
        constructed.append(dsn)
        return second

    monkeypatch.setattr(
        governed_records,
        "PostgresGovernedRecordBackend",
        _unexpected_postgres,
    )

    def _drift() -> None:
        if drift == "local":
            selected["value"] = governed_records._BackendSelection(
                "local", "", False
            )
        elif drift == "dsn":
            selected["value"] = governed_records._BackendSelection(
                "postgres", dsn_b, False
            )
        else:
            selected["value"] = governed_records._BackendSelection(
                "postgres", dsn_a, True
            )

    primary.after_load = _drift
    callback_calls = 0

    def _mutate(document):
        nonlocal callback_calls
        callback_calls += 1
        document["count"] = int(document.get("count", 0)) + 1

    with pytest.raises(GovernedRecordBackendError, match="authority changed"):
        store.mutate(_mutate, action="test-pin")

    assert primary.calls == ["load"]
    assert local.calls == []
    assert second.calls == []
    assert constructed == []
    assert callback_calls == (0 if existing else 1)


def test_required_backend_selector_error_is_sanitized(monkeypatch):
    secret = "postgresql://admin:do-not-leak@db.internal/lightwork"  # pragma: allowlist secret
    store = GovernedRecordStore(
        "test_selector_error",
        "TSE",
        "test_selector_error",
        required_backend="postgres",
    )

    def _fail_selection():
        raise RuntimeError(secret)

    monkeypatch.setattr(governed_records, "_selection", _fail_selection)
    with pytest.raises(
        GovernedRecordBackendError,
        match="cannot be revalidated",
    ) as caught:
        store.get("TSE-state")
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("drift", ["local", "dsn", "enforcement-floor"])
def test_process_branch_selection_is_sticky_after_postgres_admission(
    monkeypatch,
    drift,
):
    dsn_a = "postgresql://user:secret-a@db-a.internal/lightwork"  # pragma: allowlist secret
    dsn_b = "postgresql://user:secret-b@db-b.internal/lightwork"  # pragma: allowlist secret
    selected = {
        "value": governed_records._BackendSelection("postgres", dsn_a, False),
    }
    monkeypatch.setattr(governed_records, "_selection", lambda: selected["value"])

    assert governed_records.configured_governed_records_backend() == "postgres"
    if drift == "local":
        selected["value"] = governed_records._BackendSelection("local", "", False)
    elif drift == "dsn":
        selected["value"] = governed_records._BackendSelection(
            "postgres", dsn_b, False
        )
    else:
        selected["value"] = governed_records._BackendSelection(
            "postgres", dsn_a, True
        )

    with pytest.raises(GovernedRecordBackendError, match="after process admission"):
        governed_records.configured_governed_records_backend()


def test_process_branch_selection_is_sticky_after_local_admission(monkeypatch):
    dsn = "postgresql://user:secret@db.internal/lightwork"  # pragma: allowlist secret
    selected = {
        "value": governed_records._BackendSelection("local", "", False),
    }
    monkeypatch.setattr(governed_records, "_selection", lambda: selected["value"])

    assert governed_records.configured_governed_records_backend() == "local"
    selected["value"] = governed_records._BackendSelection(
        "postgres",
        dsn,
        False,
    )

    with pytest.raises(GovernedRecordBackendError, match="after process admission"):
        governed_records.configured_governed_records_backend()


def test_first_resolved_process_authority_admission_is_atomic(monkeypatch):
    local = governed_records._BackendSelection("local", "", False)
    postgres = governed_records._BackendSelection(
        "postgres",
        "postgresql://user:secret@db.internal/lightwork",  # pragma: allowlist secret
        False,
    )
    token_barrier = threading.Barrier(2)
    original_token = governed_records._backend_authority_token

    def _synchronized_token(selection):
        token = original_token(selection)
        token_barrier.wait(timeout=5)
        return token

    monkeypatch.setattr(
        governed_records,
        "_backend_authority_token",
        _synchronized_token,
    )
    accepted = []
    rejected = []
    result_lock = threading.Lock()

    def _admit(selection):
        try:
            governed_records._admit_process_authority_selection(selection)
        except GovernedRecordBackendError as exc:
            with result_lock:
                rejected.append((selection, exc))
        else:
            with result_lock:
                accepted.append(selection)

    threads = [
        threading.Thread(target=_admit, args=(selection,))
        for selection in (local, postgres)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert "after process admission" in str(rejected[0][1])
    with governed_records._PROCESS_AUTHORITY_LOCK:
        pinned = governed_records._PROCESS_AUTHORITY_TOKEN
    assert pinned is not None
    assert governed_records._backend_authority_matches(
        pinned,
        original_token(accepted[0]),
    )


@pytest.mark.parametrize("kind", ["local", "postgres"])
def test_process_authority_is_admitted_before_any_backend_io(monkeypatch, kind):
    dsn = "postgresql://user:secret@db.internal/lightwork"  # pragma: allowlist secret
    selection = governed_records._BackendSelection(
        kind,
        dsn if kind == "postgres" else "",
        False,
    )
    local = _TracingBackend(kind="local", shared=False)
    postgres = _TracingBackend(kind="postgres", shared=True)
    store = GovernedRecordStore("test_admit_order", "TAO", "test_admit_order")
    store._local_backend = local
    store._postgres_backend = postgres
    store._postgres_key = hashlib.sha256(dsn.encode("utf-8")).hexdigest()
    monkeypatch.setattr(governed_records, "_selection", lambda: selection)

    def _refuse_before_io(_selection):
        raise GovernedRecordBackendError("admission sentinel")

    monkeypatch.setattr(
        governed_records,
        "_admit_process_authority_selection",
        _refuse_before_io,
    )

    with pytest.raises(GovernedRecordBackendError, match="admission sentinel"):
        store.get("TAO-missing")
    assert local.calls == []
    assert postgres.calls == []


def test_shared_branch_selector_failure_after_admission_is_sanitized(monkeypatch):
    secret = "postgresql://admin:do-not-leak@db.internal/lightwork"  # pragma: allowlist secret
    selection = governed_records._BackendSelection("postgres", secret, False)
    monkeypatch.setattr(governed_records, "_selection", lambda: selection)
    assert governed_records.configured_governed_records_backend() == "postgres"

    def _fail_selection():
        raise RuntimeError(secret)

    monkeypatch.setattr(governed_records, "_selection", _fail_selection)
    with pytest.raises(
        GovernedRecordBackendError,
        match="cannot be revalidated",
    ) as caught:
        governed_records.configured_governed_records_backend()
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("drift", ["local", "dsn", "enforcement-floor"])
def test_direct_record_store_cannot_change_plane_after_postgres_io(
    monkeypatch,
    drift,
):
    dsn_a = "postgresql://user:secret-a@db-a.internal/lightwork"  # pragma: allowlist secret
    dsn_b = "postgresql://user:secret-b@db-b.internal/lightwork"  # pragma: allowlist secret
    selected = {
        "value": governed_records._BackendSelection("postgres", dsn_a, False),
    }
    primary = _TracingBackend(kind="postgres", shared=True)
    local = _TracingBackend(kind="local", shared=False)
    second = _TracingBackend(kind="postgres", shared=True)
    store = GovernedRecordStore("test_direct_pin", "TDR", "test_direct_pin")
    store._postgres_backend = primary
    store._postgres_key = hashlib.sha256(dsn_a.encode("utf-8")).hexdigest()
    store._local_backend = local
    monkeypatch.setattr(store, "_local_records_exist", lambda: False)
    monkeypatch.setattr(
        store,
        "_require_application_encryption",
        lambda _required, **_kwargs: None,
    )
    monkeypatch.setattr(governed_records, "_selection", lambda: selected["value"])
    constructed: list[str] = []

    def _unexpected_postgres(*_args, dsn, **_kwargs):
        constructed.append(dsn)
        return second

    monkeypatch.setattr(
        governed_records,
        "PostgresGovernedRecordBackend",
        _unexpected_postgres,
    )

    assert store.get("TDR-missing") is None
    if drift == "local":
        selected["value"] = governed_records._BackendSelection("local", "", False)
    elif drift == "dsn":
        selected["value"] = governed_records._BackendSelection(
            "postgres", dsn_b, False
        )
    else:
        selected["value"] = governed_records._BackendSelection(
            "postgres", dsn_a, True
        )

    with pytest.raises(GovernedRecordBackendError, match="after process admission"):
        store.get("TDR-missing")
    assert primary.calls == ["load"]
    assert local.calls == []
    assert second.calls == []
    assert constructed == []


def test_direct_record_store_pins_local_first_use_until_restart(monkeypatch):
    dsn = "postgresql://user:secret@db.internal/lightwork"  # pragma: allowlist secret
    selected = {
        "value": governed_records._BackendSelection("local", "", False),
    }
    local = _TracingBackend(kind="local", shared=False)
    postgres = _TracingBackend(kind="postgres", shared=True)
    store = GovernedRecordStore("test_direct_cutover", "TDC", "test_direct_cutover")
    store._local_backend = local
    store._postgres_backend = postgres
    store._postgres_key = hashlib.sha256(dsn.encode("utf-8")).hexdigest()
    monkeypatch.setattr(store, "_local_records_exist", lambda: False)
    monkeypatch.setattr(
        store,
        "_require_application_encryption",
        lambda _required, **_kwargs: None,
    )
    monkeypatch.setattr(governed_records, "_selection", lambda: selected["value"])

    assert store.get("TDC-missing") is None
    selected["value"] = governed_records._BackendSelection("postgres", dsn, False)
    with pytest.raises(GovernedRecordBackendError, match="after process admission"):
        store.get("TDC-missing")

    assert local.calls == ["load"]
    assert postgres.calls == []


def test_required_postgres_accepts_fixed_shared_test_backend(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    dsn = "postgresql://user:secret@db.internal/lightwork"  # pragma: allowlist secret
    monkeypatch.setattr(
        governed_records,
        "_selection",
        lambda: governed_records._BackendSelection("postgres", dsn, False),
    )
    backend = _TracingBackend(kind="postgres", shared=True)
    store = AuthorityDocumentStore(
        "test_fixed_shared",
        "TFS",
        "test_fixed_shared",
        backend=backend,
        required_backend="postgres",
    )
    monkeypatch.setattr(store._records, "_local_records_exist", lambda: False)
    monkeypatch.setattr(
        store._records,
        "_require_application_encryption",
        lambda _required, **_kwargs: None,
    )

    assert store.mutate(
        lambda document: document.setdefault("enabled", True),
        action="initialize",
    ) is True
    assert store.read() == {"enabled": True}


@pytest.mark.parametrize(
    ("selection_kind", "override_kind"),
    [("local", "postgres"), ("postgres", "redis")],
)
def test_required_postgres_refuses_selector_or_explicit_backend_kind_mismatch(
    monkeypatch,
    selection_kind,
    override_kind,
):
    dsn = "postgresql://user:secret@db.internal/lightwork"  # pragma: allowlist secret
    selection = governed_records._BackendSelection(
        selection_kind,
        dsn if selection_kind == "postgres" else "",
        False,
    )
    backend = _TracingBackend(kind=override_kind, shared=True)
    store = AuthorityDocumentStore(
        "test_fixed_shared_mismatch",
        "TFM",
        "test_fixed_shared_mismatch",
        backend=backend,
        required_backend="postgres",
    )
    monkeypatch.setattr(governed_records, "_selection", lambda: selection)
    monkeypatch.setattr(store._records, "_local_records_exist", lambda: False)
    monkeypatch.setattr(
        store._records,
        "_require_application_encryption",
        lambda _required, **_kwargs: None,
    )

    with pytest.raises(GovernedRecordBackendError):
        store.read()

    assert backend.calls == []


def _selector_backed_record_store(monkeypatch, *, prefix: str):
    dsn = "postgresql://user:secret@db.internal/lightwork"  # pragma: allowlist secret
    primary = _TracingBackend(kind="postgres", shared=True)
    local = _TracingBackend(kind="local", shared=False)
    store = GovernedRecordStore(
        f"test_single_backend_{prefix.lower()}",
        prefix,
        "test_single_backend",
    )
    store._postgres_backend = primary
    store._postgres_key = hashlib.sha256(dsn.encode("utf-8")).hexdigest()
    store._local_backend = local
    monkeypatch.setattr(store, "_local_records_exist", lambda: False)
    monkeypatch.setattr(
        store,
        "_require_application_encryption",
        lambda _required, **_kwargs: None,
    )
    calls = 0

    def _selection():
        nonlocal calls
        calls += 1
        if calls == 1:
            return governed_records._BackendSelection("postgres", dsn, False)
        return governed_records._BackendSelection("local", "", False)

    monkeypatch.setattr(governed_records, "_selection", _selection)
    return store, primary, local, lambda: calls


def test_create_keeps_one_backend_through_audit_flush(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    store, primary, local, selection_calls = _selector_backed_record_store(
        monkeypatch,
        prefix="TSB",
    )

    saved = store.create(
        {"id": "TSB-one", "status": "open"},
        action="create",
        actor="tester",
    )

    assert saved["id"] == "TSB-one"
    assert selection_calls() == 1
    assert primary.calls == ["save", "update"]
    assert local.calls == []


def test_update_keeps_one_backend_through_audit_flush(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    store, primary, local, selection_calls = _selector_backed_record_store(
        monkeypatch,
        prefix="TSU",
    )
    primary.records["TSU-one"] = {
        "created_at": 1.0,
        "id": "TSU-one",
        "revision": 1,
        "status": "open",
        "updated_at": 1.0,
    }

    saved = store.update(
        "TSU-one",
        lambda record: record.update(status="closed"),
        expected_revision=1,
        action="close",
        actor="tester",
    )

    assert saved is not None and saved["status"] == "closed"
    assert selection_calls() == 1
    assert primary.calls == ["update", "update"]
    assert local.calls == []


def test_retry_pending_keeps_enumerated_backend_for_flush(monkeypatch):
    audit_available = {"value": False}
    monkeypatch.setattr(
        "maverick.audit.record",
        lambda *_a, **_k: audit_available["value"],
    )
    store, primary, local, _selection_calls = _selector_backed_record_store(
        monkeypatch,
        prefix="TSR",
    )
    store.create(
        {"id": "TSR-one", "status": "open"},
        action="create",
        actor="tester",
    )
    assert primary.records["TSR-one"].get("_audit_pending")
    primary.calls.clear()
    local.calls.clear()

    # Reset the selector seam: retry_pending must resolve only the enumerated
    # backend, then carry that concrete object into its per-record flush.
    dsn = "postgresql://user:secret@db.internal/lightwork"  # pragma: allowlist secret
    calls = 0

    def _selection():
        nonlocal calls
        calls += 1
        if calls == 1:
            return governed_records._BackendSelection("postgres", dsn, False)
        return governed_records._BackendSelection("local", "", False)

    monkeypatch.setattr(governed_records, "_selection", _selection)
    audit_available["value"] = True

    assert store.retry_pending() == 1
    assert calls == 1
    assert primary.calls == ["iter", "load", "update"]
    assert local.calls == []
