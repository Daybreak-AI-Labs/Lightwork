"""Durable, confidential delivery outbox for SIEM audit forwarding.

Audit events must survive a collector outage without being written to a second
plaintext log.  This module stores bounded, at-rest-sealed delivery batches in
the active tenant's data namespace and removes them only after the configured
collector acknowledges the complete batch.  Delivery is at least once: a
process crash after a remote acknowledgement but before the local delete can
replay a batch, so every HTTP request carries a stable delivery id that a
collector can use as an idempotency key.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..crypto_at_rest import is_sealed_str, seal_to_str, unseal_from_str
from ..file_lock import ensure_private_directory, ensure_private_file
from ..paths import data_dir, maverick_home

_BATCH_PLAINTEXT_MAX = 1024 * 1024
_DEFAULT_MAX_BYTES = 100 * 1024 * 1024
_DEFAULT_MAX_RECORDS = 10_000
_DEFAULT_MAX_ATTEMPTS = 12
_DEFAULT_LEASE_SECONDS = 60.0
_DEFAULT_BACKOFF_BASE = 5.0
_DEFAULT_BACKOFF_MAX = 3600.0
_DEFAULT_RECEIPT_RETENTION = 30 * 86400.0
_DELIVERY_ID = re.compile(r"^[0-9a-f]{64}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS siem_outbox (
  delivery_id       TEXT PRIMARY KEY,
  destination       TEXT NOT NULL,
  sealed_payload    TEXT NOT NULL,
  payload_bytes     INTEGER NOT NULL,
  line_count        INTEGER NOT NULL,
  status            TEXT NOT NULL DEFAULT 'pending',
  attempts          INTEGER NOT NULL DEFAULT 0,
  next_attempt_at   REAL NOT NULL,
  lease_until       REAL,
  last_error_class  TEXT NOT NULL DEFAULT '',
  created_at        REAL NOT NULL,
  updated_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_siem_outbox_ready
  ON siem_outbox(status, next_attempt_at, created_at);

CREATE TABLE IF NOT EXISTS siem_delivery_receipts (
  delivery_id       TEXT PRIMARY KEY,
  destination_hash  TEXT NOT NULL,
  payload_hash      TEXT NOT NULL,
  line_count        INTEGER NOT NULL,
  acknowledged_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_siem_receipts_ack
  ON siem_delivery_receipts(acknowledged_at);
"""


class SIEMOutboxError(RuntimeError):
    """The durable SIEM delivery boundary could not preserve its contract."""


class SIEMOutboxFull(SIEMOutboxError):
    """The bounded spool is full; callers must stop rather than drop events."""


class SIEMDeliveryPending(SIEMOutboxError):
    """At least one requested batch remains unacknowledged."""


@dataclass(frozen=True)
class OutboxSnapshot:
    pending: int
    sending: int
    dead: int
    bytes: int
    oldest_age_seconds: float

    @property
    def backlog(self) -> int:
        return self.pending + self.sending + self.dead

    def as_dict(self) -> dict[str, int | float]:
        return {
            "backlog": self.backlog,
            "pending": self.pending,
            "sending": self.sending,
            "dead": self.dead,
            "bytes": self.bytes,
            "oldest_age_seconds": self.oldest_age_seconds,
        }


@dataclass(frozen=True)
class _Claim:
    delivery_id: str
    destination: str
    sealed_payload: str
    line_count: int
    attempts: int


def _positive_number(env_name: str, default: float) -> float:
    raw = os.environ.get(env_name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise SIEMOutboxError(f"{env_name} must be a positive number") from exc
    if not math.isfinite(value) or value <= 0:
        raise SIEMOutboxError(f"{env_name} must be a positive number")
    return value


def _positive_int(env_name: str, default: int) -> int:
    value = _positive_number(env_name, float(default))
    if not value.is_integer():
        raise SIEMOutboxError(f"{env_name} must be a positive integer")
    return int(value)


def _default_db() -> Path:
    return data_dir("audit", "siem-outbox.db")


def _payload_batches(lines: Iterable[str]) -> Iterator[list[str]]:
    batch: list[str] = []
    size = 2  # JSON list brackets
    for line in lines:
        if not isinstance(line, str):
            raise TypeError("rendered SIEM audit events must be strings")
        encoded_size = len(json.dumps(line, ensure_ascii=False).encode("utf-8"))
        if encoded_size > _BATCH_PLAINTEXT_MAX:
            raise SIEMOutboxError(
                "one rendered SIEM audit event exceeds the durable batch limit"
            )
        separator = 1 if batch else 0
        if batch and size + separator + encoded_size > _BATCH_PLAINTEXT_MAX:
            yield batch
            batch = []
            size = 2
            separator = 0
        batch.append(line)
        size += separator + encoded_size
    if batch:
        yield batch


class SIEMOutbox:
    """Tenant-scoped SQLite delivery spool with atomic claim/ack transitions."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or _default_db()).expanduser()
        ensure_private_directory(self.db_path.parent)
        self._lock = threading.RLock()
        self._ensure_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
        ensure_private_file(self.db_path)

    def enqueue(
        self,
        lines: Iterable[str],
        destination: str,
        *,
        now: float | None = None,
    ) -> tuple[list[str], int]:
        """Seal and atomically enqueue batches; return ids and input line count."""
        timestamp = time.time() if now is None else float(now)
        if not math.isfinite(timestamp):
            raise SIEMOutboxError("SIEM enqueue timestamp must be finite")
        prepared: list[tuple[str, str, int, int, str]] = []
        total_lines = 0
        for batch in _payload_batches(lines):
            plaintext = json.dumps(
                batch, ensure_ascii=False, separators=(",", ":")
            )
            payload_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
            delivery_id = hashlib.sha256(
                destination.encode("utf-8") + b"\x00" + payload_hash.encode("ascii")
            ).hexdigest()
            sealed = seal_to_str(plaintext)
            prepared.append(
                (delivery_id, sealed, len(sealed.encode("utf-8")), len(batch), payload_hash)
            )
            total_lines += len(batch)
        if not prepared:
            return [], 0

        max_bytes = _positive_int("MAVERICK_SIEM_OUTBOX_MAX_BYTES", _DEFAULT_MAX_BYTES)
        max_records = _positive_int(
            "MAVERICK_SIEM_OUTBOX_MAX_RECORDS", _DEFAULT_MAX_RECORDS
        )
        ids: list[str] = []
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(payload_bytes), 0) "
                    "FROM siem_outbox"
                ).fetchone()
                records = int(row[0])
                stored_bytes = int(row[1])
                existing = {
                    str(item[0])
                    for item in conn.execute(
                        "SELECT delivery_id FROM siem_outbox WHERE delivery_id IN "
                        f"({','.join('?' for _ in prepared)})",
                        tuple(item[0] for item in prepared),
                    ).fetchall()
                }
                existing.update(
                    str(item[0])
                    for item in conn.execute(
                        "SELECT delivery_id FROM siem_delivery_receipts "
                        "WHERE delivery_id IN "
                        f"({','.join('?' for _ in prepared)})",
                        tuple(item[0] for item in prepared),
                    ).fetchall()
                )
                additions = [item for item in prepared if item[0] not in existing]
                if records + len(additions) > max_records:
                    raise SIEMOutboxFull("SIEM outbox record limit reached")
                if stored_bytes + sum(item[2] for item in additions) > max_bytes:
                    raise SIEMOutboxFull("SIEM outbox byte limit reached")
                for delivery_id, sealed, size, count, _payload_hash in prepared:
                    if delivery_id in existing:
                        ids.append(delivery_id)
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO siem_outbox "
                        "(delivery_id, destination, sealed_payload, payload_bytes, "
                        "line_count, next_attempt_at, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            delivery_id,
                            destination,
                            sealed,
                            size,
                            count,
                            timestamp,
                            timestamp,
                            timestamp,
                        ),
                    )
                    ids.append(delivery_id)
                conn.commit()
            # failure-policy: fail_closed -- rollback and surface enqueue failure
            except Exception:
                conn.rollback()
                raise
        return ids, total_lines

    def _claim(self, *, now: float) -> _Claim | None:
        lease = _positive_number(
            "MAVERICK_SIEM_OUTBOX_LEASE_SECONDS", _DEFAULT_LEASE_SECONDS
        )
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "UPDATE siem_outbox SET status='pending', lease_until=NULL, "
                    "updated_at=? WHERE status='sending' AND lease_until <= ?",
                    (now, now),
                )
                row = conn.execute(
                    "SELECT delivery_id, destination, sealed_payload, line_count, "
                    "attempts FROM siem_outbox WHERE status='pending' "
                    "AND next_attempt_at <= ? ORDER BY created_at, delivery_id LIMIT 1",
                    (now,),
                ).fetchone()
                if row is None:
                    conn.commit()
                    return None
                updated = conn.execute(
                    "UPDATE siem_outbox SET status='sending', attempts=attempts+1, "
                    "lease_until=?, updated_at=? WHERE delivery_id=? "
                    "AND status='pending'",
                    (now + lease, now, row[0]),
                )
                if updated.rowcount != 1:
                    conn.rollback()
                    return None
                conn.commit()
            # failure-policy: fail_closed -- rollback and surface lease failure
            except Exception:
                conn.rollback()
                raise
        return _Claim(
            delivery_id=str(row[0]),
            destination=str(row[1]),
            sealed_payload=str(row[2]),
            line_count=int(row[3]),
            attempts=int(row[4]) + 1,
        )

    def _ack(self, claim: _Claim, *, now: float, payload_hash: str) -> bool:
        destination_hash = hashlib.sha256(
            claim.destination.encode("utf-8")
        ).hexdigest()
        retention = _positive_number(
            "MAVERICK_SIEM_RECEIPT_RETENTION_SECONDS",
            _DEFAULT_RECEIPT_RETENTION,
        )
        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = conn.execute(
                    "SELECT status, attempts FROM siem_outbox WHERE delivery_id=?",
                    (claim.delivery_id,),
                ).fetchone()
                if current is None or current[0] != "sending" or int(current[1]) != claim.attempts:
                    conn.rollback()
                    return False
                conn.execute(
                    "INSERT OR REPLACE INTO siem_delivery_receipts "
                    "(delivery_id, destination_hash, payload_hash, line_count, "
                    "acknowledged_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        claim.delivery_id,
                        destination_hash,
                        payload_hash,
                        claim.line_count,
                        now,
                    ),
                )
                conn.execute(
                    "DELETE FROM siem_outbox WHERE delivery_id=? AND status='sending' "
                    "AND attempts=?",
                    (claim.delivery_id, claim.attempts),
                )
                conn.execute(
                    "DELETE FROM siem_delivery_receipts WHERE acknowledged_at < ?",
                    (now - retention,),
                )
                conn.commit()
            # failure-policy: fail_closed -- rollback and surface ack failure
            except Exception:
                conn.rollback()
                raise
        return True

    def _fail(self, claim: _Claim, error: Exception, *, now: float) -> None:
        max_attempts = _positive_int(
            "MAVERICK_SIEM_OUTBOX_MAX_ATTEMPTS", _DEFAULT_MAX_ATTEMPTS
        )
        base = _positive_number(
            "MAVERICK_SIEM_OUTBOX_BACKOFF_BASE_SECONDS", _DEFAULT_BACKOFF_BASE
        )
        cap = _positive_number(
            "MAVERICK_SIEM_OUTBOX_BACKOFF_MAX_SECONDS", _DEFAULT_BACKOFF_MAX
        )
        delay = min(cap, base * (2 ** max(0, claim.attempts - 1)))
        status = "dead" if claim.attempts >= max_attempts else "pending"
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE siem_outbox SET status=?, next_attempt_at=?, "
                "lease_until=NULL, last_error_class=?, updated_at=? "
                "WHERE delivery_id=? AND status='sending' AND attempts=?",
                (
                    status,
                    now + delay,
                    type(error).__name__[:128],
                    now,
                    claim.delivery_id,
                    claim.attempts,
                ),
            )

    def drain(
        self,
        *,
        timeout: float = 10.0,
        now: float | None = None,
        limit: int = 100,
        raise_on_failure: bool = False,
    ) -> int:
        """Deliver ready batches and return the number of acknowledged lines."""
        timestamp = time.time() if now is None else float(now)
        if not math.isfinite(timestamp):
            raise SIEMOutboxError("SIEM drain timestamp must be finite")
        if limit <= 0:
            raise ValueError("SIEM drain limit must be positive")
        acknowledged = 0
        for _ in range(limit):
            claim = self._claim(now=timestamp)
            if claim is None:
                break
            try:
                if not is_sealed_str(claim.sealed_payload):
                    raise SIEMOutboxError(
                        "SIEM outbox payload is not authenticated ciphertext"
                    )
                plaintext = unseal_from_str(claim.sealed_payload)
                lines = json.loads(plaintext)
                if not isinstance(lines, list) or len(lines) != claim.line_count:
                    raise SIEMOutboxError("sealed SIEM outbox payload is malformed")
                if not all(isinstance(line, str) for line in lines):
                    raise SIEMOutboxError("sealed SIEM outbox payload is malformed")
                payload_hash = hashlib.sha256(
                    plaintext.encode("utf-8")
                ).hexdigest()
                expected_delivery_id = hashlib.sha256(
                    claim.destination.encode("utf-8")
                    + b"\x00"
                    + payload_hash.encode("ascii")
                ).hexdigest()
                if expected_delivery_id != claim.delivery_id:
                    raise SIEMOutboxError(
                        "SIEM outbox destination or payload identity was altered"
                    )
                from .forwarder import _forward

                sent = _forward(
                    lines,
                    claim.destination,
                    timeout=timeout,
                    delivery_id=claim.delivery_id,
                )
                if sent != claim.line_count:
                    raise SIEMOutboxError(
                        "SIEM transport did not acknowledge the complete batch"
                    )
                if not self._ack(claim, now=timestamp, payload_hash=payload_hash):
                    raise SIEMOutboxError("SIEM delivery lease was lost before ack")
                acknowledged += sent
            # failure-policy: fail_soft_with_audit -- durable retry/dead state records failure
            except Exception as exc:
                self._fail(claim, exc, now=timestamp)
                if raise_on_failure:
                    raise
        return acknowledged

    def contains_any(self, delivery_ids: Iterable[str]) -> bool:
        ids = tuple(dict.fromkeys(str(value) for value in delivery_ids))
        if not ids:
            return False
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM siem_outbox WHERE delivery_id IN "
                f"({','.join('?' for _ in ids)}) LIMIT 1",
                ids,
            ).fetchone()
        return row is not None

    @staticmethod
    def _requested_delivery_ids(
        delivery_ids: Iterable[str] | None,
    ) -> tuple[str, ...] | None:
        if delivery_ids is None:
            return None
        ids = tuple(dict.fromkeys(str(value).strip() for value in delivery_ids))
        if not ids:
            raise ValueError("at least one SIEM delivery id is required")
        if any(_DELIVERY_ID.fullmatch(value) is None for value in ids):
            raise ValueError("SIEM delivery ids must be lowercase SHA-256 values")
        return ids

    @staticmethod
    def _redrive_limit(limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("SIEM dead-letter redrive limit must be between 1 and 100")
        return limit

    @staticmethod
    def _select_dead(
        conn: sqlite3.Connection,
        *,
        delivery_ids: tuple[str, ...] | None,
        limit: int,
    ) -> tuple[str, ...]:
        if delivery_ids is None:
            rows = conn.execute(
                "SELECT delivery_id FROM siem_outbox WHERE status='dead' "
                "ORDER BY created_at, delivery_id LIMIT ?",
                (limit,),
            ).fetchall()
            return tuple(str(row[0]) for row in rows)
        if len(delivery_ids) > limit:
            raise ValueError(
                "requested SIEM delivery ids exceed the configured redrive limit"
            )
        rows = conn.execute(
            "SELECT delivery_id FROM siem_outbox WHERE status='dead' "
            f"AND delivery_id IN ({','.join('?' for _ in delivery_ids)})",
            delivery_ids,
        ).fetchall()
        found = {str(row[0]) for row in rows}
        if found != set(delivery_ids):
            raise SIEMOutboxError(
                "every requested SIEM delivery must exist in dead-letter state"
            )
        return delivery_ids

    def dead_delivery_ids(
        self,
        *,
        delivery_ids: Iterable[str] | None = None,
        limit: int = 100,
    ) -> tuple[str, ...]:
        """Return a bounded set of opaque dead-letter ids without decrypting data."""
        requested = self._requested_delivery_ids(delivery_ids)
        bounded_limit = self._redrive_limit(limit)
        with self._conn() as conn:
            return self._select_dead(
                conn,
                delivery_ids=requested,
                limit=bounded_limit,
            )

    def redrive_dead(
        self,
        *,
        delivery_ids: Iterable[str] | None = None,
        limit: int = 100,
        now: float | None = None,
    ) -> tuple[str, ...]:
        """Atomically return selected dead letters to the retry queue.

        The original sealed payload, destination, and stable delivery id remain
        unchanged.  A collector therefore sees the same idempotency key if the
        operator redrives a batch that was acknowledged remotely before the
        local acknowledgement was lost.
        """
        requested = self._requested_delivery_ids(delivery_ids)
        bounded_limit = self._redrive_limit(limit)
        timestamp = time.time() if now is None else float(now)
        if not math.isfinite(timestamp):
            raise SIEMOutboxError("SIEM redrive timestamp must be finite")

        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                selected = self._select_dead(
                    conn,
                    delivery_ids=requested,
                    limit=bounded_limit,
                )
                if not selected:
                    conn.commit()
                    return ()

                # Audit before mutating queue state.  A required audit refusal
                # rolls back the transaction; a best-effort write failure is
                # also treated as a refusal for this operator action.
                from . import audit_event

                recorded = audit_event(
                    "siem.dead_letter_redrive_authorized",
                    agent="operator-cli",
                    delivery_ids=list(selected),
                    delivery_count=len(selected),
                )
                if not recorded:
                    raise SIEMOutboxError(
                        "SIEM dead-letter redrive could not be durably audited"
                    )
                placeholders = ",".join("?" for _ in selected)
                updated = conn.execute(
                    "UPDATE siem_outbox SET status='pending', attempts=0, "
                    "next_attempt_at=?, lease_until=NULL, last_error_class='', "
                    f"updated_at=? WHERE status='dead' AND delivery_id IN ({placeholders})",
                    (timestamp, timestamp, *selected),
                )
                if updated.rowcount != len(selected):
                    raise SIEMOutboxError(
                        "SIEM dead-letter state changed during redrive"
                    )
                conn.commit()
            # failure-policy: fail_closed -- rollback and surface redrive failure
            except Exception:
                conn.rollback()
                raise
        return selected

    def snapshot(self, *, now: float | None = None) -> OutboxSnapshot:
        timestamp = time.time() if now is None else float(now)
        with self._conn() as conn:
            rows = {
                str(row[0]): int(row[1])
                for row in conn.execute(
                    "SELECT status, COUNT(*) FROM siem_outbox GROUP BY status"
                ).fetchall()
            }
            totals = conn.execute(
                "SELECT COALESCE(SUM(payload_bytes), 0), MIN(created_at) "
                "FROM siem_outbox"
            ).fetchone()
        oldest = 0.0 if totals[1] is None else max(0.0, timestamp - float(totals[1]))
        return OutboxSnapshot(
            pending=rows.get("pending", 0),
            sending=rows.get("sending", 0),
            dead=rows.get("dead", 0),
            bytes=int(totals[0]),
            oldest_age_seconds=oldest,
        )


def outbox_snapshot() -> dict[str, int | float]:
    """Aggregate-only hook for readiness/metrics; no tenant/event data leaks."""
    return SIEMOutbox().snapshot().as_dict()


def _read_existing_snapshot(
    db_path: Path,
    *,
    now: float,
) -> OutboxSnapshot:
    """Read one existing spool without creating, migrating, or decrypting it."""
    try:
        uri = db_path.resolve(strict=True).as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            rows = {
                str(row[0]): int(row[1])
                for row in conn.execute(
                    "SELECT status, COUNT(*) FROM siem_outbox GROUP BY status"
                ).fetchall()
            }
            totals = conn.execute(
                "SELECT COALESCE(SUM(payload_bytes), 0), MIN(created_at) "
                "FROM siem_outbox"
            ).fetchone()
    except (OSError, sqlite3.Error) as exc:
        raise SIEMOutboxError("SIEM outbox metrics snapshot is unavailable") from exc
    oldest = 0.0 if totals[1] is None else max(0.0, now - float(totals[1]))
    return OutboxSnapshot(
        pending=rows.get("pending", 0),
        sending=rows.get("sending", 0),
        dead=rows.get("dead", 0),
        bytes=int(totals[0]),
        oldest_age_seconds=oldest,
    )


def aggregate_outbox_snapshot(
    *, now: float | None = None,
) -> dict[str, int | float]:
    """Aggregate fixed counts across every local tenant SIEM spool.

    Metrics scrapes have no request tenant. Reading only ``SIEMOutbox()`` would
    therefore hide principal-isolated tenant backlogs. This enumerates only the
    fixed outbox filename under the private Lightwork data root, opens databases
    read-only, and returns no path, destination, tenant, delivery, or payload.
    """
    timestamp = time.time() if now is None else float(now)
    if not math.isfinite(timestamp):
        raise SIEMOutboxError("SIEM snapshot timestamp must be finite")
    home = maverick_home().resolve()
    candidates = [home / "audit" / "siem-outbox.db"]
    tenants = home / "tenants"
    try:
        if tenants.is_dir():
            candidates.extend(
                child / "audit" / "siem-outbox.db"
                for child in tenants.iterdir()
                if child.is_dir()
            )
    except OSError as exc:
        raise SIEMOutboxError("tenant SIEM outbox inventory is unavailable") from exc

    snapshots: list[OutboxSnapshot] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(home)
        except (OSError, ValueError) as exc:
            raise SIEMOutboxError(
                "SIEM outbox path escapes the private data root"
            ) from exc
        if resolved in seen:
            continue
        seen.add(resolved)
        snapshots.append(_read_existing_snapshot(resolved, now=timestamp))

    combined = OutboxSnapshot(
        pending=sum(item.pending for item in snapshots),
        sending=sum(item.sending for item in snapshots),
        dead=sum(item.dead for item in snapshots),
        bytes=sum(item.bytes for item in snapshots),
        oldest_age_seconds=max(
            (item.oldest_age_seconds for item in snapshots), default=0.0
        ),
    )
    return combined.as_dict()


__all__ = [
    "OutboxSnapshot",
    "SIEMDeliveryPending",
    "SIEMOutbox",
    "SIEMOutboxError",
    "SIEMOutboxFull",
    "aggregate_outbox_snapshot",
    "outbox_snapshot",
]
