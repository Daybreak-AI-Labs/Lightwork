"""Shared authority contract for hosted-form submissions."""
from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from maverick import form_store
from maverick.automation_events import EventSourceError, get_source


class _MemoryDocument:
    def __init__(self, document=None, *, backend: str = "postgres") -> None:
        self.document = copy.deepcopy(document or {})
        self.backend = backend
        self.lock = threading.Lock()

    @property
    def backend_kind(self) -> str:
        return self.backend

    def read(self):
        with self.lock:
            return copy.deepcopy(self.document)

    def mutate(self, callback, **_kwargs):
        with self.lock:
            candidate = copy.deepcopy(self.document)
            result = callback(candidate)
            self.document = candidate
            return result


def _isolate(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))


def _shared(monkeypatch):
    stores: dict[str, _MemoryDocument] = {}

    def factory(token: str):
        name = form_store._shared_document_name(token)
        return stores.setdefault(name, _MemoryDocument())

    monkeypatch.setattr(form_store, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(form_store, "_require_active_tenant", lambda: None)
    monkeypatch.setattr(form_store, "_new_shared_store", factory)
    return stores


def test_shared_append_is_atomic_pollable_and_never_writes_local(
    monkeypatch,
    tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    stores = _shared(monkeypatch)

    assert form_store.append("sales/lead", {"email": "one@example.com"}) == 1
    assert form_store.append("sales/lead", {"email": "two@example.com"}) == 2
    events, cursor = form_store.since("sales/lead", "0")

    assert [row["email"] for row in events] == [
        "one@example.com",
        "two@example.com",
    ]
    assert cursor == "2"
    assert not form_store._path("sales/lead").exists()
    assert len(stores) == 1


def test_shared_token_identity_does_not_inherit_local_filename_collisions(
    monkeypatch,
    tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    stores = _shared(monkeypatch)
    assert form_store._path("sales/lead") == form_store._path("sales-lead")

    assert form_store.append("sales/lead", {"source": "slash"}) == 1
    assert form_store.append("sales-lead", {"source": "dash"}) == 1

    assert len(stores) == 2
    slash, _ = form_store.since("sales/lead", "0")
    dash, _ = form_store.since("sales-lead", "0")
    assert slash[0]["source"] == "slash"
    assert dash[0]["source"] == "dash"


def test_real_shared_store_uses_a_valid_governed_record_id():
    from maverick.governed_records import _valid_record_id

    store = form_store._new_shared_store("a/token/with/a/long/raw/identity")
    assert len(store._record_id) <= 64
    assert _valid_record_id(store._record_id, "FRM") == store._record_id


def test_shared_concurrent_appends_receive_one_sequence_each(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _shared(monkeypatch)

    with ThreadPoolExecutor(max_workers=12) as pool:
        sequences = list(pool.map(
            lambda value: form_store.append("concurrent", {"value": value}),
            range(64),
        ))

    assert sorted(sequences) == list(range(1, 65))
    events, cursor = form_store.since("concurrent", "0")
    assert len(events) == 64
    assert cursor == "64"


def test_shared_admission_retry_is_idempotent_and_payload_bound(
    monkeypatch,
    tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    stores = _shared(monkeypatch)
    claim = "a" * 64

    assert form_store.append(
        "claimed",
        {"email": "same@example.com"},
        admission_id=claim,
    ) == 1
    assert form_store.append(
        "claimed",
        {"email": "same@example.com"},
        admission_id=claim,
    ) == 1
    with pytest.raises(form_store.FormStoreAdmissionConflict, match="different payload"):
        form_store.append(
            "claimed",
            {"email": "changed@example.com"},
            admission_id=claim,
        )
    document = next(iter(stores.values())).document
    assert document["high_water"] == 1 and len(document["rows"]) == 1


def test_v1_shared_history_is_readable_and_migrates_in_place_on_claimed_append(
    monkeypatch,
    tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(form_store, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(form_store, "_require_active_tenant", lambda: None)
    store = _MemoryDocument({
        "high_water": 1,
        "rows": [{"fields": {"value": "legacy"}, "seq": 1, "ts": 1.0}],
        "schema_version": 1,
    })
    monkeypatch.setattr(form_store, "_new_shared_store", lambda _token: store)

    events, cursor = form_store.since("migration", "0")
    assert cursor == "1" and events[0]["value"] == "legacy"
    assert form_store.append(
        "migration",
        {"value": "claimed"},
        admission_id="b" * 64,
    ) == 2
    assert store.document["schema_version"] == 2
    assert store.document["rows"][0]["admission_id"] == ""
    assert store.document["rows"][1]["admission_id"] == "b" * 64


def test_shared_outage_fails_closed_without_local_fallback(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(form_store, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(form_store, "_require_active_tenant", lambda: None)

    def unavailable(_token):
        raise OSError("database unavailable")

    monkeypatch.setattr(form_store, "_new_shared_store", unavailable)

    with pytest.raises(form_store.FormStoreAuthorityError, match="could not be recorded"):
        form_store.append("outage", {"email": "a@example.com"})
    with pytest.raises(form_store.FormStoreAuthorityError, match="could not be read"):
        form_store.since("outage", "0")
    assert not form_store._path("outage").exists()


def test_backend_drift_is_rejected(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(form_store, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(form_store, "_require_active_tenant", lambda: None)
    store = _MemoryDocument(backend="local")
    monkeypatch.setattr(form_store, "_new_shared_store", lambda _token: store)

    with pytest.raises(form_store.FormStoreAuthorityError, match="requires governed"):
        form_store.append("drift", {"field": "value"})
    assert store.document == {}


def test_nonempty_legacy_file_blocks_shared_cutover(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    stores = _shared(monkeypatch)
    path = form_store._path("legacy")
    path.parent.mkdir(parents=True)
    path.write_text("not-even-valid-json\n", encoding="utf-8")

    with pytest.raises(form_store.FormStoreAuthorityError, match="migrate"):
        form_store.append("legacy", {"new": "submission"})
    with pytest.raises(form_store.FormStoreAuthorityError, match="migrate"):
        form_store.since("legacy", "0")
    assert stores == {}


def test_malformed_shared_state_is_not_treated_as_empty(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(form_store, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(form_store, "_require_active_tenant", lambda: None)
    store = _MemoryDocument({
        "high_water": 4,
        "rows": [],
        "schema_version": 1,
    })
    monkeypatch.setattr(form_store, "_new_shared_store", lambda _token: store)

    with pytest.raises(form_store.FormStoreAuthorityError, match="inconsistent"):
        form_store.since("broken", "0")
    with pytest.raises(form_store.FormStoreAuthorityError, match="inconsistent"):
        form_store.append("broken", {"field": "value"})
    assert store.document["high_water"] == 4


def test_internal_sequence_gap_is_rejected_as_silent_loss(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(form_store, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(form_store, "_require_active_tenant", lambda: None)
    store = _MemoryDocument({
        "high_water": 12,
        "rows": [
            {"fields": {"value": "ten"}, "seq": 10, "ts": 1.0},
            {"fields": {"value": "twelve"}, "seq": 12, "ts": 2.0},
        ],
        "schema_version": 1,
    })
    monkeypatch.setattr(form_store, "_new_shared_store", lambda _token: store)

    with pytest.raises(form_store.FormStoreAuthorityError, match="internal gap"):
        form_store.since("gap", "10")


def test_duplicate_shared_admission_ids_are_rejected_as_ambiguous(
    monkeypatch,
    tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(form_store, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(form_store, "_require_active_tenant", lambda: None)
    claim = "d" * 64
    store = _MemoryDocument({
        "high_water": 2,
        "rows": [
            {
                "admission_id": claim,
                "fields": {"value": "one"},
                "seq": 1,
                "ts": 1.0,
            },
            {
                "admission_id": claim,
                "fields": {"value": "two"},
                "seq": 2,
                "ts": 2.0,
            },
        ],
        "schema_version": 2,
    })
    monkeypatch.setattr(form_store, "_new_shared_store", lambda _token: store)
    with pytest.raises(form_store.FormStoreAuthorityError, match="ambiguous"):
        form_store.since("duplicate", "0")


@pytest.mark.parametrize("cursor", ["broken", "-1", "01", " 1", "+1"])
def test_shared_poll_rejects_malformed_cursor(monkeypatch, tmp_path, cursor):
    _isolate(monkeypatch, tmp_path)
    _shared(monkeypatch)
    form_store.append("cursor", {"value": "retained"})

    with pytest.raises(form_store.FormStoreAuthorityError, match="cursor is invalid"):
        form_store.since("cursor", cursor)


def test_empty_shared_authority_rejects_ahead_cursor(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _shared(monkeypatch)

    with pytest.raises(form_store.FormStoreAuthorityError, match="ahead"):
        form_store.since("empty", "1")


def test_retention_gap_is_explicit_instead_of_silent_data_loss(
    monkeypatch,
    tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    _shared(monkeypatch)
    monkeypatch.setattr(form_store, "_MAX_SHARED_BYTES", 620)

    for index in range(12):
        form_store.append("bounded", {"payload": f"{index}-" + "x" * 160})

    with pytest.raises(form_store.FormStoreAuthorityError, match="predates"):
        form_store.since("bounded", "0")
    events, cursor = form_store.since("bounded", "11")
    assert [row["payload"].split("-", 1)[0] for row in events] == ["11"]
    assert cursor == "12"


def test_form_event_source_translates_authority_failure(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(form_store, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(form_store, "_require_active_tenant", lambda: None)
    monkeypatch.setattr(
        form_store,
        "_new_shared_store",
        lambda _token: (_ for _ in ()).throw(OSError("offline")),
    )

    with pytest.raises(EventSourceError, match="authority is unavailable"):
        get_source("form").poll({"token": "offline"}, "0")


def test_local_concurrent_append_is_sequence_safe(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(form_store, "_configured_backend", lambda: "local")

    with ThreadPoolExecutor(max_workers=8) as pool:
        sequences = list(pool.map(
            lambda value: form_store.append("local", {"value": value}),
            range(24),
        ))

    assert sorted(sequences) == list(range(1, 25))
    events, cursor = form_store.since("local", "0")
    assert len(events) == 24
    assert cursor == "24"


def test_local_claim_retry_rejects_different_payload(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(form_store, "_configured_backend", lambda: "local")
    claim = "c" * 64
    assert form_store.append("local-claim", {"x": 1}, admission_id=claim) == 1
    assert form_store.append("local-claim", {"x": 1}, admission_id=claim) == 1
    with pytest.raises(form_store.FormStoreAdmissionConflict):
        form_store.append("local-claim", {"x": 2}, admission_id=claim)


def test_shared_forms_require_an_active_tenant(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(form_store, "_configured_backend", lambda: "postgres")
    from maverick import paths
    monkeypatch.setattr(paths, "current_tenant_id_strict", lambda: None)

    with pytest.raises(form_store.FormStoreAuthorityError, match="active tenant"):
        form_store.append("unbound", {"value": "no tenant"})
