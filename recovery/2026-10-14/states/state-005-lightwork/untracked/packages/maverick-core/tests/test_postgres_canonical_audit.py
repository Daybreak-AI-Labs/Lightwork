from __future__ import annotations

import copy
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import maverick.audit.postgres as pg_audit
import pytest
from maverick.audit.events import AuditEvent


@dataclass
class _Selection:
    kind: str = "postgres"
    dsn: str = "postgresql://audit-authority"
    shared_required: bool = False


class _Result:
    def __init__(self, row: Any = None, rows: list[Any] | None = None, rowcount: int = 0):
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _SharedDatabase:
    def __init__(self) -> None:
        self.heads: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.signer_state: dict[str, Any] | None = None
        self.signers: list[dict[str, Any]] = []
        self.recoveries: list[dict[str, Any]] = []
        self.fail_next_event_insert = False
        self.fail_next_pending_replacement = False
        self.lock = threading.RLock()

    def connect(self):
        return _Connection(self)


class _Connection:
    def __init__(self, database: _SharedDatabase) -> None:
        self.database = database
        self._locked = False
        self._snapshot: tuple[
            dict[str, dict[str, Any]],
            list[dict[str, Any]],
            dict[str, Any] | None,
            list[dict[str, Any]],
            list[dict[str, Any]],
        ] | None = None

    def _release(self) -> None:
        if self._locked:
            self._locked = False
            self.database.lock.release()

    def commit(self) -> None:
        self._snapshot = None
        self._release()

    def rollback(self) -> None:
        if self._snapshot is not None:
            (
                self.database.heads,
                self.database.events,
                self.database.signer_state,
                self.database.signers,
                self.database.recoveries,
            ) = self._snapshot
            self._snapshot = None
        self._release()

    def close(self) -> None:
        self._release()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:  # noqa: C901
        normalized = " ".join(sql.lower().split())
        if normalized == "begin":
            return _Result()
        if "select current_database(), current_schema(), current_user" in normalized:
            return _Result(("audit", "public", "audit_runtime"))
        if "set_config('maverick.tenant'" in normalized:
            return _Result((params[0],))
        if "pg_advisory_xact_lock" in normalized:
            self.database.lock.acquire()
            self._locked = True
            self._snapshot = (
                copy.deepcopy(self.database.heads),
                copy.deepcopy(self.database.events),
                copy.deepcopy(self.database.signer_state),
                copy.deepcopy(self.database.signers),
                copy.deepcopy(self.database.recoveries),
            )
            return _Result((None,))
        if normalized.startswith("insert into canonical_audit_heads"):
            tenant = str(params[0])
            self.database.heads.setdefault(
                tenant,
                {"next_sequence": 1, "head": None, "count": 0},
            )
            return _Result(rowcount=1)
        if normalized.startswith("select authority_id from canonical_audit_signer_state"):
            if self.database.signer_state is None:
                return _Result()
            return _Result((self.database.signer_state["authority_id"],))
        if normalized.startswith("select count(*) from canonical_audit_signers"):
            return _Result((len(self.database.signers),))
        if normalized.startswith(
            "select count(*) from canonical_audit_signer_recoveries"
        ):
            return _Result((len(self.database.recoveries),))
        if normalized.startswith("insert into canonical_audit_signers"):
            if len(params) == 4:
                key_id, public_key, transition, created_at = params
                row = {
                    "key_id": key_id,
                    "public_key": public_key,
                    "activated_epoch": 1,
                    "status": "active",
                    "deactivated_epoch": None,
                    "predecessor_key_id": None,
                    "predecessor_status": None,
                    "authorization_signature": None,
                    "activation_signature": None,
                    "transition_sha256": transition,
                    "created_at": created_at,
                }
            else:
                (
                    key_id, public_key, epoch, predecessor_key_id,
                    predecessor_status, authorization_signature,
                    transition, created_at,
                ) = params
                row = {
                    "key_id": key_id,
                    "public_key": public_key,
                    "activated_epoch": epoch,
                    "status": "pending",
                    "deactivated_epoch": None,
                    "predecessor_key_id": predecessor_key_id,
                    "predecessor_status": predecessor_status,
                    "authorization_signature": authorization_signature,
                    "activation_signature": None,
                    "transition_sha256": transition,
                    "created_at": created_at,
                }
            if any(
                item["key_id"] == row["key_id"]
                or item["public_key"] == row["public_key"]
                or item["activated_epoch"] == row["activated_epoch"]
                for item in self.database.signers
            ):
                return _Result(rowcount=0)
            self.database.signers.append(row)
            return _Result(rowcount=1)
        if normalized.startswith("insert into canonical_audit_signer_state"):
            if self.database.signer_state is not None:
                return _Result(rowcount=0)
            authority_id, key_id, root, history, recovery, updated_at = params
            self.database.signer_state = {
                "authority_id": authority_id,
                "current_epoch": 1,
                "active_key_id": key_id,
                "pending_key_id": None,
                "root_sha256": root,
                "history_sha256": history,
                "recovery_count": 0,
                "recovery_sha256": recovery,
                "updated_at": updated_at,
            }
            return _Result(rowcount=1)
        if normalized.startswith(
            "select current_epoch, active_key_id, pending_key_id, root_sha256"
        ):
            state = self.database.signer_state
            if state is None:
                return _Result()
            return _Result(tuple(state[key] for key in (
                "current_epoch", "active_key_id", "pending_key_id",
                "root_sha256", "history_sha256", "recovery_count",
                "recovery_sha256",
            )))
        if normalized.startswith("select key_id, public_key, activated_epoch, status"):
            keys = (
                "key_id", "public_key", "activated_epoch", "status",
                "deactivated_epoch", "predecessor_key_id", "predecessor_status",
                "authorization_signature", "activation_signature",
                "transition_sha256",
            )
            rows = sorted(
                self.database.signers, key=lambda item: item["activated_epoch"]
            )
            return _Result(rows=[tuple(row[key] for key in keys) for row in rows])
        if normalized.startswith(
            "select active_key_id, pending_key_id from canonical_audit_signer_state"
        ):
            state = self.database.signer_state
            return _Result(
                None if state is None else (
                    state["active_key_id"], state["pending_key_id"]
                )
            )
        if normalized.startswith(
            "select recovery_sequence, signer_epoch, active_key_id, history_sha256"
        ):
            keys = (
                "recovery_sequence", "signer_epoch", "active_key_id",
                "history_sha256", "cancelled_key_id", "cancelled_public_key",
                "cancelled_predecessor_status",
                "cancelled_authorization_signature",
                "cancelled_transition_sha256", "replacement_key_id",
                "replacement_public_key", "replacement_predecessor_status",
                "replacement_authorization_signature",
                "replacement_transition_sha256", "previous_recovery_sha256",
                "recovery_sha256", "recovery_signature", "created_at",
            )
            rows = sorted(
                self.database.recoveries,
                key=lambda item: item["recovery_sequence"],
            )
            return _Result(rows=[tuple(row[key] for key in keys) for row in rows])
        if normalized.startswith("insert into canonical_audit_signer_recoveries"):
            keys = (
                "recovery_sequence", "signer_epoch", "active_key_id",
                "history_sha256", "cancelled_key_id", "cancelled_public_key",
                "cancelled_predecessor_status",
                "cancelled_authorization_signature",
                "cancelled_transition_sha256", "replacement_key_id",
                "replacement_public_key", "replacement_predecessor_status",
                "replacement_authorization_signature",
                "replacement_transition_sha256", "previous_recovery_sha256",
                "recovery_sha256", "recovery_signature", "created_at",
            )
            row = dict(zip(keys, params, strict=True))
            if any(
                item["recovery_sequence"] == row["recovery_sequence"]
                or item["recovery_sha256"] == row["recovery_sha256"]
                for item in self.database.recoveries
            ):
                return _Result(rowcount=0)
            self.database.recoveries.append(row)
            return _Result(rowcount=1)
        if normalized.startswith(
            "update canonical_audit_signer_state set pending_key_id=null, recovery_count="
        ):
            (
                recovery_count, recovery_hash, updated_at, authority_id, epoch,
                active, pending, history, prior_count, prior_hash,
            ) = params
            state = self.database.signer_state
            if state is None or not (
                state["authority_id"] == authority_id
                and state["current_epoch"] == epoch
                and state["active_key_id"] == active
                and state["pending_key_id"] == pending
                and state["history_sha256"] == history
                and state["recovery_count"] == prior_count
                and state["recovery_sha256"] == prior_hash
            ):
                return _Result(rowcount=0)
            state.update(
                pending_key_id=None,
                recovery_count=recovery_count,
                recovery_sha256=recovery_hash,
                updated_at=updated_at,
            )
            return _Result(rowcount=1)
        if normalized.startswith(
            "update canonical_audit_signer_state set pending_key_id="
        ):
            (
                pending, updated_at, authority_id, epoch, active, history,
                recovery_count, recovery_hash,
            ) = params
            state = self.database.signer_state
            if state is None or not (
                state["authority_id"] == authority_id
                and state["current_epoch"] == epoch
                and state["active_key_id"] == active
                and state["pending_key_id"] is None
                and state["history_sha256"] == history
                and state["recovery_count"] == recovery_count
                and state["recovery_sha256"] == recovery_hash
            ):
                return _Result(rowcount=0)
            state.update(pending_key_id=pending, updated_at=updated_at)
            return _Result(rowcount=1)
        if normalized.startswith("update canonical_audit_signers set key_id="):
            if self.database.fail_next_pending_replacement:
                self.database.fail_next_pending_replacement = False
                raise OSError("injected pending replacement failure")
            (
                next_key, next_public, next_status, next_authorization,
                next_transition, created_at, old_key, old_public, epoch,
                predecessor_key, old_status, old_authorization, old_transition,
            ) = params
            row = next(
                (item for item in self.database.signers if item["key_id"] == old_key),
                None,
            )
            if row is None or not (
                row["public_key"] == old_public
                and row["activated_epoch"] == epoch
                and row["status"] == "pending"
                and row["deactivated_epoch"] is None
                and row["predecessor_key_id"] == predecessor_key
                and row["predecessor_status"] == old_status
                and row["authorization_signature"] == old_authorization
                and row["activation_signature"] is None
                and row["transition_sha256"] == old_transition
            ):
                return _Result(rowcount=0)
            if any(
                item is not row
                and (
                    item["key_id"] == next_key
                    or item["public_key"] == next_public
                )
                for item in self.database.signers
            ):
                return _Result(rowcount=0)
            row.update(
                key_id=next_key,
                public_key=next_public,
                predecessor_status=next_status,
                authorization_signature=next_authorization,
                transition_sha256=next_transition,
                created_at=created_at,
            )
            return _Result(rowcount=1)
        if normalized.startswith("update canonical_audit_signers set status=%s"):
            status, deactivated_epoch, key_id = params
            row = next(
                (item for item in self.database.signers if item["key_id"] == key_id),
                None,
            )
            if row is None or row["status"] != "active" or row["deactivated_epoch"] is not None:
                return _Result(rowcount=0)
            row.update(status=status, deactivated_epoch=deactivated_epoch)
            return _Result(rowcount=1)
        if normalized.startswith(
            "update canonical_audit_signers set status='active', activation_signature="
        ):
            activation_signature, key_id = params
            row = next(
                (item for item in self.database.signers if item["key_id"] == key_id),
                None,
            )
            if row is None or row["status"] != "pending" or row["activation_signature"] is not None:
                return _Result(rowcount=0)
            row.update(status="active", activation_signature=activation_signature)
            return _Result(rowcount=1)
        if normalized.startswith(
            "update canonical_audit_signer_state set current_epoch="
        ):
            (
                next_epoch, next_key, history, updated_at, authority_id,
                current_epoch, active_key, pending_key, prior_history,
                recovery_count, recovery_hash,
            ) = params
            state = self.database.signer_state
            if state is None or not (
                state["authority_id"] == authority_id
                and state["current_epoch"] == current_epoch
                and state["active_key_id"] == active_key
                and state["pending_key_id"] == pending_key
                and state["history_sha256"] == prior_history
                and state["recovery_count"] == recovery_count
                and state["recovery_sha256"] == recovery_hash
            ):
                return _Result(rowcount=0)
            state.update(
                current_epoch=next_epoch,
                active_key_id=next_key,
                pending_key_id=None,
                history_sha256=history,
                updated_at=updated_at,
            )
            return _Result(rowcount=1)
        if normalized.startswith(
            "select next_sequence, head_sha256, event_count from canonical_audit_heads"
        ):
            tenant = str(params[0])
            head = self.database.heads.get(tenant)
            if head is None:
                return _Result()
            return _Result((head["next_sequence"], head["head"], head["count"]))
        if normalized.startswith("select count(*), coalesce(min(sequence), 0)"):
            tenant = str(params[0])
            rows = [row for row in self.database.events if row["tenant_id"] == tenant]
            seqs = [row["sequence"] for row in rows]
            return _Result((len(rows), min(seqs, default=0), max(seqs, default=0)))
        if normalized.startswith("select event_sha256 from canonical_audit_events"):
            tenant, sequence = str(params[0]), int(params[1])
            row = next(
                (
                    item for item in self.database.events
                    if item["tenant_id"] == tenant and item["sequence"] == sequence
                ),
                None,
            )
            return _Result(None if row is None else (row["event_sha256"],))
        if normalized.startswith("select sequence, event_kind, event_day, event_ts"):
            tenant, event_id = str(params[0]), str(params[1])
            row = next(
                (
                    item for item in self.database.events
                    if item["tenant_id"] == tenant and item["event_id"] == event_id
                ),
                None,
            )
            if row is None:
                return _Result()
            return _Result(tuple(row[key] for key in (
                "sequence", "event_kind", "event_day", "event_ts",
                "idempotency_sha256", "payload_sha256", "prev_sha256",
                "event_sha256", "signature", "key_id", "public_key",
                "signer_epoch",
            )))
        if normalized.startswith("select event_sealed from canonical_audit_events"):
            tenant, event_id = str(params[0]), str(params[1])
            row = next(
                (
                    item for item in self.database.events
                    if item["tenant_id"] == tenant and item["event_id"] == event_id
                ),
                None,
            )
            return _Result(None if row is None else (row["event_sealed"],))
        if normalized.startswith("insert into canonical_audit_events"):
            if self.database.fail_next_event_insert:
                self.database.fail_next_event_insert = False
                raise OSError("injected append failure")
            keys = (
                "tenant_id", "sequence", "event_id", "event_kind", "event_day",
                "event_ts", "idempotency_sha256", "payload_sha256", "prev_sha256",
                "event_sha256", "signature", "key_id", "public_key", "event_sealed",
                "created_at", "signer_epoch",
            )
            row = dict(zip(keys, params, strict=True))
            conflict = any(
                existing["tenant_id"] == row["tenant_id"]
                and (
                    existing["sequence"] == row["sequence"]
                    or existing["event_id"] == row["event_id"]
                )
                for existing in self.database.events
            )
            if conflict:
                return _Result(rowcount=0)
            self.database.events.append(row)
            return _Result(rowcount=1)
        if normalized.startswith("update canonical_audit_heads set next_sequence"):
            (
                new_next, new_hash, new_count, _updated_at, tenant,
                expected_next, expected_count, previous_a, previous_b,
            ) = params
            assert previous_a == previous_b
            head = self.database.heads.get(str(tenant))
            if head is None or not (
                head["next_sequence"] == expected_next
                and head["count"] == expected_count
                and head["head"] == previous_a
            ):
                return _Result(rowcount=0)
            head.update(next_sequence=new_next, head=new_hash, count=new_count)
            return _Result(rowcount=1)
        if normalized.startswith("select sequence, event_id, event_kind, event_day"):
            tenant = str(params[0])
            rows = sorted(
                (row for row in self.database.events if row["tenant_id"] == tenant),
                key=lambda item: item["sequence"],
            )
            keys = (
                "sequence", "event_id", "event_kind", "event_day", "event_ts",
                "idempotency_sha256", "payload_sha256", "prev_sha256",
                "event_sha256", "signature", "key_id", "public_key",
                "signer_epoch",
            )
            return _Result(rows=[tuple(row[key] for key in keys) for row in rows])
        if normalized.startswith("select sequence, event_id, payload_sha256"):
            tenant = str(params[0])
            rows = [row for row in self.database.events if row["tenant_id"] == tenant]
            index = 1
            if "event_day=%s" in normalized:
                day = str(params[index])
                index += 1
                rows = [row for row in rows if row["event_day"] == day]
            if "event_day>=%s" in normalized:
                since = str(params[index])
                index += 1
                rows = [row for row in rows if row["event_day"] >= since]
            if "event_day<=%s" in normalized:
                until = str(params[index])
                index += 1
                rows = [row for row in rows if row["event_day"] <= until]
            descending = "order by sequence desc" in normalized
            rows.sort(key=lambda item: item["sequence"], reverse=descending)
            if "limit %s" in normalized:
                rows = rows[: int(params[index])]
            keys = (
                "sequence", "event_id", "payload_sha256", "event_sealed",
                "prev_sha256", "event_sha256", "signature", "key_id", "public_key",
                "signer_epoch",
            )
            return _Result(rows=[tuple(row[key] for key in keys) for row in rows])
        raise AssertionError(f"unhandled SQL: {normalized}")


@pytest.fixture
def authority(monkeypatch, tmp_path: Path):
    selection = _Selection()
    monkeypatch.setattr(pg_audit, "_selection", lambda: selection)
    monkeypatch.setattr(pg_audit, "_schema_status", lambda _conn: "ready")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    monkeypatch.setenv("MAVERICK_ENCRYPTION_KEY", "11" * 32)
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    signer = ed25519.Ed25519PrivateKey.from_private_bytes(b"\x22" * 32)
    public = signer.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = hashlib.sha256(public).hexdigest()[:16]
    monkeypatch.setenv(
        "MAVERICK_AUDIT_TRUST_ROOT_SHA256",
        pg_audit.canonical_audit_trust_root_digest(public.hex()),
    )
    monkeypatch.setattr(
        pg_audit,
        "_signing_material",
        lambda: (signer, key_id, public.hex()),
    )
    database = _SharedDatabase()
    backend = pg_audit._PostgresAuditBackend(
        selection.dsn,
        "acme",
        connect=database.connect,
    )
    assert backend.bootstrap_signer_trust() == key_id
    log = pg_audit.PostgresAuditLog(tenant="acme", backend=backend)
    return selection, database, log


def _event(number: int, *, event_id: str | None = None, value: str = "ok") -> AuditEvent:
    payload = {"number": number, "value": value}
    if event_id is not None:
        payload["event_id"] = event_id
    return AuditEvent(ts=1_700_000_000.0 + number, kind="security_change", payload=payload)


def _material(seed: int):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    signer = ed25519.Ed25519PrivateKey.from_private_bytes(bytes([seed]) * 32)
    public = signer.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return signer, hashlib.sha256(public).hexdigest()[:16], public.hex()


def test_epoch_one_preserves_v36_signed_body_while_rotations_bind_epoch():
    fields = {
        "tenant": "acme",
        "sequence": 1,
        "event_id": "event",
        "event_kind": "security_change",
        "event_day": "2023-11-14",
        "event_ts": 1_700_000_000.0,
        "idempotency_sha256": "1" * 64,
        "payload_sha256": "2" * 64,
        "prev_sha256": None,
        "key_id": "3" * 16,
        "public_key": "4" * 64,
    }
    assert "signer_epoch" not in pg_audit._chain_body(**fields, signer_epoch=1)
    assert pg_audit._chain_body(**fields, signer_epoch=2)["signer_epoch"] == 2


def test_concurrent_appends_have_one_gap_free_total_order(authority):
    _selection, database, log = authority
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(lambda number: log.record(_event(number)), range(40)))

    rows = list(log.iter_events(all_days=True))
    assert [row["sequence"] for row in rows] == list(range(1, 41))
    assert {row["number"] for row in rows} == set(range(40))
    assert database.heads["acme"]["count"] == 40


def test_duplicate_event_is_exactly_once_and_conflicting_reuse_is_refused(authority):
    _selection, database, log = authority
    first = _event(1, event_id="stable-event")
    retry = _event(1, event_id="stable-event")
    retry.ts += 30  # append-attempt time is deliberately outside identity
    assert log.record(first)
    assert log.record(retry)
    assert len(database.events) == 1
    with pytest.raises(pg_audit.AuditIdempotencyConflict):
        log.record(_event(1, event_id="stable-event", value="changed"))
    assert len(database.events) == 1


def test_duplicate_acknowledgement_refuses_corrupt_encrypted_body(authority):
    _selection, database, log = authority
    assert log.record(_event(1, event_id="stable-event"))
    database.events[0]["event_sealed"] = "MVKAR1:corrupt"
    with pytest.raises(pg_audit.AuditIntegrityError, match="encrypted payload"):
        log.record(_event(1, event_id="stable-event"))
    assert len(database.events) == 1


def test_outage_and_authority_drift_never_fall_back_to_local(authority, tmp_path):
    selection, _database, log = authority
    selection.dsn = "postgresql://different-authority"
    with pytest.raises(pg_audit.AuditAuthorityError, match="changed"):
        log.record(_event(1))
    assert not list((tmp_path / "home").rglob("*.ndjson"))

    selection.dsn = "postgresql://audit-authority"
    outage = pg_audit._PostgresAuditBackend(
        selection.dsn,
        "acme",
        connect=lambda: (_ for _ in ()).throw(OSError("offline")),
    )
    unavailable = pg_audit.PostgresAuditLog(tenant="acme", backend=outage)
    with pytest.raises(pg_audit.AuditAuthorityError, match="unavailable"):
        unavailable.record(_event(2))
    assert not list((tmp_path / "home").rglob("*.ndjson"))


def test_cached_writer_rechecks_shared_signing_custody(authority, monkeypatch):
    selection, database, log = authority
    from maverick.audit import signing

    selection.shared_required = True
    monkeypatch.setattr(signing, "active_key_is_offhost", lambda: False)
    with pytest.raises(pg_audit.AuditAuthorityError, match="off-host"):
        log.record(_event(1))
    assert database.events == []


def test_corrupt_shared_state_is_withheld(authority):
    _selection, database, log = authority
    assert log.record(_event(1))
    database.events[0]["event_kind"] = "rewritten"
    with pytest.raises(pg_audit.AuditIntegrityError, match="hash chain"):
        list(log.iter_events(all_days=True))


def test_local_override_and_live_legacy_cutover_are_refused(authority, monkeypatch):
    _selection, database, log = authority
    from maverick.audit.writer import AuditLog
    from maverick.paths import data_dir

    with pytest.raises(pg_audit.AuditAuthorityError, match="explicit local"):
        AuditLog(Path("unused"))

    legacy = data_dir("audit", tenant="acme")
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "2025-01-01.ndjson").write_text('{"kind":"legacy"}\n')
    with pytest.raises(pg_audit.AuditAuthorityError, match="migrate/import"):
        log.record(_event(1))
    assert database.events == []


def test_local_mode_remains_available(authority, tmp_path):
    selection, _database, _log = authority
    from maverick.audit.writer import AuditLog

    selection.kind = "sqlite"
    selection.dsn = ""
    local = AuditLog(tmp_path / "local-audit", sign=False)
    assert local.record(_event(1))
    assert list((tmp_path / "local-audit").glob("*.ndjson"))


def test_tenant_streams_are_independent(authority):
    selection, database, acme = authority
    globex_backend = pg_audit._PostgresAuditBackend(
        selection.dsn,
        "globex",
        connect=database.connect,
    )
    globex = pg_audit.PostgresAuditLog(tenant="globex", backend=globex_backend)

    assert acme.record(_event(1, event_id="acme-event"))
    assert globex.record(_event(2, event_id="globex-event"))
    assert [row["event_id"] for row in acme.iter_events(all_days=True)] == [
        "acme-event"
    ]
    assert [row["event_id"] for row in globex.iter_events(all_days=True)] == [
        "globex-event"
    ]
    assert database.heads["acme"]["count"] == 1
    assert database.heads["globex"]["count"] == 1


def test_payload_validation_rejects_non_finite_and_oversized_values(authority):
    _selection, database, log = authority
    invalid = _event(1)
    invalid.payload["score"] = float("nan")
    with pytest.raises(pg_audit.AuditAuthorityError, match="valid bounded JSON"):
        log.record(invalid)

    oversized = _event(2)
    oversized.payload["blob"] = "x" * (1024 * 1024)
    with pytest.raises(pg_audit.AuditAuthorityError, match="1 MiB"):
        log.record(oversized)
    assert database.events == []


def test_payload_tenant_cannot_contradict_rls_scope(authority):
    _selection, _database, log = authority
    event = _event(1)
    event.payload["tenant"] = "globex"
    with pytest.raises(pg_audit.AuditAuthorityError, match="contradicts"):
        log.record(event)


def test_default_writer_and_shared_reader_route_to_postgres(
    authority, monkeypatch,
):
    _selection, database, log = authority
    from maverick.audit import reader, writer
    from maverick.paths import reset_tenant, set_tenant

    monkeypatch.setattr(pg_audit, "PostgresAuditLog", lambda **_kwargs: log)
    writer._shared_defaults.clear()
    token = set_tenant("acme")
    try:
        assert writer.record(
            "security_change",
            event_id="routed-event",
            number=99,
        )
        rows = list(reader.iter_events(all_days=True))
    finally:
        reset_tenant(token)
        writer._shared_defaults.clear()
    assert len(database.events) == 1
    assert rows[0]["event_id"] == "routed-event"
    assert rows[0]["number"] == 99


def test_platform_hunter_integrity_adapter_uses_shared_authority(
    authority, monkeypatch,
):
    _selection, database, log = authority
    from maverick.platform_hunt.integrity import verify_audit_chain

    monkeypatch.setattr(pg_audit, "PostgresAuditLog", lambda **_kwargs: log)
    assert log.record(_event(1))
    status = verify_audit_chain(())
    assert status.intact
    assert status.paths_checked == ("canonical-postgres",)

    database.events[0]["event_kind"] = "rewritten"
    status = verify_audit_chain(())
    assert not status.intact
    assert status.breaks[0]["reason"] == "shared_integrity_failure"


def test_bootstrap_race_is_idempotent_and_explicit(authority, monkeypatch):
    selection, _database, _log = authority
    clean = _SharedDatabase()
    backends = [
        pg_audit._PostgresAuditBackend(
            selection.dsn, "acme", connect=clean.connect
        )
        for _ in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        key_ids = list(pool.map(lambda backend: backend.bootstrap_signer_trust(), backends))
    assert len(set(key_ids)) == 1
    assert len(clean.signers) == 1

    missing = _SharedDatabase()
    unbootstrapped = pg_audit.PostgresAuditLog(
        tenant="acme",
        backend=pg_audit._PostgresAuditBackend(
            selection.dsn, "acme", connect=missing.connect
        ),
    )
    with pytest.raises(pg_audit.AuditAuthorityError, match="not bootstrapped"):
        unbootstrapped.record(_event(400))


def test_rotation_is_staged_then_first_new_append_activates_atomically(
    authority, monkeypatch,
):
    selection, database, log = authority
    old = _material(0x22)
    new = _material(0x33)
    trust = pg_audit.PostgresAuditTrust(connect=database.connect)

    assert log.record(_event(1, event_id="before-rotation"))
    assert trust.rotate(new[2]) == new[1]
    assert database.signer_state is not None
    assert database.signer_state["active_key_id"] == old[1]
    assert database.signer_state["pending_key_id"] == new[1]

    # A failed/slow new-key rollout cannot strand the authority: old nodes keep
    # writing until the new private key proves possession on a real append.
    assert log.record(_event(2, event_id="while-pending"))
    monkeypatch.setattr(pg_audit, "_signing_material", lambda: new)
    assert log.record(_event(3, event_id="activates-new-key"))
    assert database.signer_state["active_key_id"] == new[1]
    assert database.signer_state["pending_key_id"] is None
    assert [row["signer_epoch"] for row in database.events] == [1, 1, 2]

    # Historical epoch-one rows still verify from shared trust after the node
    # has switched keys; the old private key is not needed.
    rows = list(log.iter_events(all_days=True))
    assert [row["event_id"] for row in rows] == [
        "before-rotation", "while-pending", "activates-new-key",
    ]
    monkeypatch.setattr(pg_audit, "_signing_material", lambda: old)
    with pytest.raises(pg_audit.AuditAuthorityError, match="stale"):
        log.record(_event(4, event_id="stale-old-writer"))
    assert selection.dsn == "postgresql://audit-authority"


def test_failed_activation_append_rolls_back_and_old_signer_remains_live(
    authority, monkeypatch,
):
    _selection, database, log = authority
    old = _material(0x22)
    new = _material(0x44)
    trust = pg_audit.PostgresAuditTrust(connect=database.connect)
    trust.rotate(new[2])
    monkeypatch.setattr(pg_audit, "_signing_material", lambda: new)
    database.fail_next_event_insert = True
    with pytest.raises(pg_audit.AuditAuthorityError, match="append failed"):
        log.record(_event(10, event_id="failed-activation"))
    assert database.signer_state is not None
    assert database.signer_state["active_key_id"] == old[1]
    assert database.signer_state["pending_key_id"] == new[1]
    assert database.events == []

    monkeypatch.setattr(pg_audit, "_signing_material", lambda: old)
    assert log.record(_event(11, event_id="rollback-old-key"))
    assert database.events[0]["signer_epoch"] == 1


def test_concurrent_rotation_stages_exactly_one_successor(authority):
    _selection, database, _log = authority
    first = _material(0x55)
    second = _material(0x66)
    trusts = [pg_audit.PostgresAuditTrust(connect=database.connect) for _ in range(2)]

    def stage(index: int):
        try:
            return trusts[index].rotate((first, second)[index][2])
        except pg_audit.AuditAuthorityError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(stage, range(2)))
    successes = {first[1], second[1]}.intersection(results)
    assert len(successes) == 1
    assert any("pending signer rotation" in result for result in results)
    assert database.signer_state is not None
    assert database.signer_state["pending_key_id"] in successes


def test_stranded_pending_signer_can_be_cas_replaced_with_signed_evidence(
    authority, monkeypatch,
):
    _selection, database, log = authority
    active = _material(0x22)
    stranded = _material(0x67)
    replacement = _material(0x68)
    trust = pg_audit.PostgresAuditTrust(connect=database.connect)

    assert trust.rotate(stranded[2]) == stranded[1]
    with pytest.raises(pg_audit.AuditAuthorityError, match="exact pending key_id"):
        trust.rotate(replacement[2])
    with pytest.raises(pg_audit.AuditAuthorityError, match="exact pending key_id"):
        trust.rotate(
            replacement[2], replace_pending_key_id="0" * 16
        )
    assert trust.rotate(
        replacement[2], replace_pending_key_id=stranded[1]
    ) == replacement[1]

    assert database.signer_state is not None
    assert database.signer_state["active_key_id"] == active[1]
    assert database.signer_state["pending_key_id"] == replacement[1]
    assert database.signer_state["recovery_count"] == 1
    assert len(database.recoveries) == 1
    evidence = database.recoveries[0]
    assert evidence["cancelled_key_id"] == stranded[1]
    assert evidence["replacement_key_id"] == replacement[1]
    assert evidence["recovery_sha256"] == database.signer_state["recovery_sha256"]

    # Recovery does not interrupt the old writer.  Only possession of the
    # replacement private key advances the signer epoch.
    assert log.record(_event(70, event_id="old-live-after-recovery"))
    monkeypatch.setattr(pg_audit, "_signing_material", lambda: replacement)
    assert log.record(_event(71, event_id="replacement-activates"))
    assert [row["signer_epoch"] for row in database.events] == [1, 2]
    assert [row["event_id"] for row in log.iter_events(all_days=True)] == [
        "old-live-after-recovery",
        "replacement-activates",
    ]

    monkeypatch.setattr(pg_audit, "_signing_material", lambda: stranded)
    with pytest.raises(pg_audit.AuditAuthorityError, match="stale"):
        log.record(_event(72, event_id="cancelled-key-refused"))


def test_pending_replacement_failure_rolls_back_evidence_and_remains_retryable(
    authority,
):
    _selection, database, _log = authority
    stranded = _material(0x69)
    replacement = _material(0x6A)
    trust = pg_audit.PostgresAuditTrust(connect=database.connect)
    trust.rotate(stranded[2])
    before_state = copy.deepcopy(database.signer_state)
    before_signers = copy.deepcopy(database.signers)

    database.fail_next_pending_replacement = True
    with pytest.raises(pg_audit.AuditAuthorityError, match="rotation failed"):
        trust.rotate(
            replacement[2], replace_pending_key_id=stranded[1]
        )
    assert database.signer_state == before_state
    assert database.signers == before_signers
    assert database.recoveries == []

    assert trust.rotate(
        replacement[2], replace_pending_key_id=stranded[1]
    ) == replacement[1]
    assert len(database.recoveries) == 1


def test_concurrent_pending_recovery_is_compare_and_swap(authority):
    _selection, database, _log = authority
    stranded = _material(0x6B)
    first = _material(0x6C)
    second = _material(0x6D)
    pg_audit.PostgresAuditTrust(connect=database.connect).rotate(stranded[2])
    trusts = [pg_audit.PostgresAuditTrust(connect=database.connect) for _ in range(2)]

    def replace(index: int):
        try:
            return trusts[index].rotate(
                (first, second)[index][2],
                replace_pending_key_id=stranded[1],
            )
        except pg_audit.AuditAuthorityError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(replace, range(2)))
    successes = {first[1], second[1]}.intersection(results)
    assert len(successes) == 1
    assert any("exact pending key_id" in result for result in results)
    assert database.signer_state is not None
    assert database.signer_state["pending_key_id"] in successes
    assert database.signer_state["recovery_count"] == 1
    assert len(database.recoveries) == 1


def test_recovery_evidence_corruption_is_withheld(authority):
    _selection, database, log = authority
    stranded = _material(0x6E)
    replacement = _material(0x6F)
    trust = pg_audit.PostgresAuditTrust(connect=database.connect)
    trust.rotate(stranded[2])
    trust.rotate(replacement[2], replace_pending_key_id=stranded[1])
    database.recoveries[0]["cancelled_transition_sha256"] = "0" * 64

    with pytest.raises(pg_audit.AuditIntegrityError, match="recovery transition"):
        list(log.iter_events(all_days=True))


def test_trust_history_rejects_key_identity_and_external_root_drift(
    authority, monkeypatch,
):
    _selection, database, log = authority
    assert log.record(_event(1))
    original_public = database.signers[0]["public_key"]
    database.signers[0]["public_key"] = _material(0x77)[2]
    with pytest.raises(pg_audit.AuditIntegrityError, match="trust row"):
        list(log.iter_events(all_days=True))
    database.signers[0]["public_key"] = original_public

    monkeypatch.setenv("MAVERICK_AUDIT_TRUST_ROOT_SHA256", "sha256:" + "00" * 32)
    with pytest.raises(pg_audit.AuditIntegrityError, match="trust state"):
        list(log.iter_events(all_days=True))


def test_historical_verification_never_reads_node_local_trust_files(
    authority, monkeypatch,
):
    _selection, _database, log = authority
    assert log.record(_event(1))
    from maverick.audit import signing

    def forbidden(*_args, **_kwargs):
        raise AssertionError("canonical Postgres trust must not read local key files")

    monkeypatch.setattr(signing, "_key_dir", forbidden)
    monkeypatch.setattr(signing, "_key_paths_for_id", forbidden)
    monkeypatch.setattr(signing, "_injected_marker_for_id", forbidden)
    assert [row["number"] for row in log.iter_events(all_days=True)] == [1]


def test_signer_ceremony_outage_and_selector_drift_fail_closed(
    authority,
):
    selection, database, _log = authority
    trust = pg_audit.PostgresAuditTrust(connect=database.connect)
    selection.dsn = "postgresql://drifted"
    with pytest.raises(pg_audit.AuditAuthorityError, match="changed"):
        trust.rotate(_material(0x77)[2])

    selection.dsn = "postgresql://audit-authority"
    outage = pg_audit.PostgresAuditTrust(
        connect=lambda: (_ for _ in ()).throw(OSError("offline"))
    )
    with pytest.raises(pg_audit.AuditAuthorityError, match="unavailable"):
        outage.rotate(_material(0x77)[2])
