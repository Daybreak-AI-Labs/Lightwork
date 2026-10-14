"""Persistent metadata-only quarantine for rejected knowledge ingestion.

Quarantine deliberately stores no document text.  It retains only bounded
provenance needed to reconcile an upload: who supplied it, where it came from,
which parser/policy handled it, the source digest, and the refusal verdict.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_MAX_COLLECTION = 256
_MAX_UPLOADER = 256
_MAX_SOURCE = 2048
_MAX_PARSER = 64
_MAX_VERDICT = 512
_MAX_POLICY = 256


class QuarantineIntegrityError(RuntimeError):
    """The quarantine ledger is unsigned, discontinuous, or tampered."""


@contextmanager
def _exclusive_file_lock(path: Path):
    """Serialize the SQLite transaction and authenticated-head update.

    SQLite's own writer lock ends at commit and cannot cover a second file.
    This OS-backed lock is shared by every ledger instance using the database,
    so no peer can observe or publish a stale head between those two writes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover - exercised on Linux CI
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _bounded(value: object, limit: int) -> str:
    return str(value or "").replace("\x00", "")[:limit]


@dataclass(frozen=True)
class QuarantineRecord:
    event_id: str
    created_at: float
    collection: str
    uploader: str
    source: str
    parser: str
    source_sha256: str
    verdict: str
    policy: str


class SqliteQuarantineLedger:
    """HMAC-chained append-only SQLite rejection ledger.

    SQLite is durable on one node, not a shared fleet ledger. Required mode may
    use it with a separately enforced single-writer deployment contract; a
    shared pgvector deployment otherwise needs a shared quarantine authority.
    """

    shared_durable = False

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        integrity_key: bytes | None = None,
        integrity_keys: dict[str, bytes] | None = None,
        active_integrity_key_id: str = "",
        required: bool = False,
        single_writer_contract: bool = False,
        head_path: str | Path | None = None,
    ) -> None:
        self._path = str(path)
        self.durable = self._path != ":memory:"
        self.externally_checkpointed = bool(head_path)
        self._head_path = (
            Path(head_path).expanduser()
            if head_path
            else (Path(self._path + ".head.json") if self.durable else None)
        )
        self._lock_path = (
            Path(self._path + ".transaction.lock") if self.durable else None
        )
        self.single_writer_contract = bool(single_writer_contract)
        keys: dict[str, bytes] = {}
        for key_id, raw_key in dict(integrity_keys or {}).items():
            key = bytes(raw_key)
            derived = hashlib.sha256(key).hexdigest()[:16] if len(key) >= 32 else ""
            if key_id != derived:
                raise ValueError(
                    "knowledge quarantine keyring contains an invalid key id"
                )
            keys[key_id] = key
        direct_key = bytes(integrity_key or b"")
        if direct_key:
            if len(direct_key) < 32:
                raise ValueError(
                    "knowledge quarantine integrity key must be at least 32 bytes"
                )
            keys.setdefault(hashlib.sha256(direct_key).hexdigest()[:16], direct_key)
        active_id = str(active_integrity_key_id or "")
        if not active_id and direct_key:
            active_id = hashlib.sha256(direct_key).hexdigest()[:16]
        if not active_id and keys:
            active_id = next(iter(keys))
        if active_id and active_id not in keys:
            raise ValueError(
                "knowledge quarantine active key id is absent from the keyring"
            )
        self._integrity_keys = keys
        self.integrity_key_id = active_id
        self._integrity_key = keys.get(active_id, b"")
        self.chain_integrity = bool(keys) and all(len(key) >= 32 for key in keys.values())
        self.tamper_evident = bool(
            self.chain_integrity and self.externally_checkpointed
        )
        self.tamper_evidence_scope = (
            "authenticated-external-head" if self.tamper_evident else "chain-only"
        )
        if required and not self.durable:
            raise ValueError("required knowledge quarantine must be file-backed")
        if required and not self.chain_integrity:
            raise ValueError(
                "required knowledge quarantine needs an HMAC integrity key"
            )
        if required and not self.externally_checkpointed:
            raise ValueError(
                "required knowledge quarantine needs an externally custodied "
                "authenticated head"
            )
        if required and self.durable and self._head_path is not None:
            database = Path(self._path).expanduser().resolve(strict=False)
            checkpoint = self._head_path.resolve(strict=False)
            if checkpoint == database or checkpoint.parent == database.parent:
                raise ValueError(
                    "required knowledge quarantine head must be externally "
                    "custodied outside the ledger directory"
                )
        self._required = bool(required)
        if self.durable:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = threading.Lock()
        try:
            self._initialize_schema_and_verify()
        except BaseException:
            self._db.close()
            self._db = None
            raise

    def _initialize_schema_and_verify(self) -> None:
        """Atomically migrate, verify, and restore append-only enforcement."""
        with self._transaction_lock():
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS knowledge_quarantine ("
                    "sequence INTEGER UNIQUE, event_id TEXT PRIMARY KEY, "
                    "created_at REAL NOT NULL, "
                    "collection TEXT NOT NULL, uploader TEXT NOT NULL, "
                    "source TEXT NOT NULL, parser TEXT NOT NULL, "
                    "source_sha256 TEXT NOT NULL, verdict TEXT NOT NULL, "
                    "policy TEXT NOT NULL, previous_hmac TEXT, record_hmac TEXT, "
                    "integrity_key_id TEXT)"
                )
                columns = {
                    str(row[1])
                    for row in self._db.execute(
                        "PRAGMA table_info(knowledge_quarantine)"
                    )
                }
                for column, declaration in (
                    ("sequence", "INTEGER"),
                    ("previous_hmac", "TEXT"),
                    ("record_hmac", "TEXT"),
                    ("integrity_key_id", "TEXT"),
                ):
                    if column not in columns:
                        self._db.execute(
                            "ALTER TABLE knowledge_quarantine "
                            f"ADD COLUMN {column} {declaration}"
                        )
                # Existing ledgers predate key identifiers. Annotate each
                # signed row with the unique configured key that verifies it.
                # Trigger removal and restoration are in this same locked
                # transaction, so no peer sees a mutable ledger window.
                self._db.execute(
                    "DROP TRIGGER IF EXISTS knowledge_quarantine_no_update"
                )
                self._db.execute(
                    "DROP TRIGGER IF EXISTS knowledge_quarantine_no_delete"
                )
                self._db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "knowledge_quarantine_sequence_idx "
                    "ON knowledge_quarantine(sequence)"
                )
                if self.chain_integrity:
                    sequence, record_hmac = self._verify_locked(
                        allow_legacy_key_id=True,
                    )
                    self._verify_head(
                        sequence,
                        record_hmac,
                        allow_legacy_key_id=True,
                    )
                self._db.execute(
                    "CREATE TRIGGER IF NOT EXISTS knowledge_quarantine_no_update "
                    "BEFORE UPDATE ON knowledge_quarantine BEGIN "
                    "SELECT RAISE(ABORT, 'knowledge quarantine is append-only'); END"
                )
                self._db.execute(
                    "CREATE TRIGGER IF NOT EXISTS knowledge_quarantine_no_delete "
                    "BEFORE DELETE ON knowledge_quarantine BEGIN "
                    "SELECT RAISE(ABORT, 'knowledge quarantine is append-only'); END"
                )
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    @contextmanager
    def _transaction_lock(self):
        if self._lock_path is None:
            yield
            return
        with _exclusive_file_lock(self._lock_path):
            yield

    def _record_hmac(
        self,
        sequence: int,
        record: QuarantineRecord,
        previous_hmac: str,
        key: bytes,
    ) -> str:
        payload = json.dumps(
            {
                "sequence": sequence,
                "event_id": record.event_id,
                "created_at": record.created_at,
                "collection": record.collection,
                "uploader": record.uploader,
                "source": record.source,
                "parser": record.parser,
                "source_sha256": record.source_sha256,
                "verdict": record.verdict,
                "policy": record.policy,
                "previous_hmac": previous_hmac,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return hmac.new(key, payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _head_hmac(sequence: int, record_hmac: str, key: bytes) -> str:
        payload = json.dumps(
            {"sequence": sequence, "record_hmac": record_hmac},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(key, payload, hashlib.sha256).hexdigest()

    def _write_head(self, sequence: int, record_hmac: str) -> None:
        if self._head_path is None:
            return
        body = json.dumps(
            {
                "sequence": sequence,
                "record_hmac": record_hmac,
                "integrity_key_id": self.integrity_key_id,
                "head_hmac": self._head_hmac(
                    sequence,
                    record_hmac,
                    self._integrity_key,
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._head_path.parent.mkdir(parents=True, exist_ok=True)
        temp_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=self._head_path.name + ".",
                dir=self._head_path.parent,
                delete=False,
            ) as handle:
                temp_name = handle.name
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self._head_path)
            if os.name != "nt":  # pragma: no cover - exercised on Linux CI
                directory_fd = os.open(
                    self._head_path.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass

    def _verify_head(
        self,
        sequence: int,
        record_hmac: str,
        *,
        allow_legacy_key_id: bool = False,
    ) -> None:
        if self._head_path is None:
            return
        if sequence == 0 and not self._head_path.exists():
            return
        try:
            raw = self._head_path.read_bytes()
            if len(raw) > 4096:
                raise ValueError("head is too large")
            head = json.loads(raw)
            if not isinstance(head, dict):
                raise ValueError("head is not an object")
            stored_sequence = head["sequence"]
            stored_record_hmac = head["record_hmac"]
            stored_head_hmac = head["head_hmac"]
            if (
                stored_sequence != sequence
                or stored_record_hmac != record_hmac
                or not isinstance(stored_head_hmac, str)
            ):
                raise ValueError("head mismatch")
            stored_key_id = head.get("integrity_key_id", "")
            key = self._integrity_keys.get(stored_key_id)
            if key is None and allow_legacy_key_id and not stored_key_id:
                candidates = [
                    key_id
                    for key_id, candidate in self._integrity_keys.items()
                    if hmac.compare_digest(
                        stored_head_hmac,
                        self._head_hmac(sequence, record_hmac, candidate),
                    )
                ]
                if len(candidates) != 1:
                    raise ValueError("legacy head key is ambiguous or unknown")
                stored_key_id = candidates[0]
                key = self._integrity_keys[stored_key_id]
                # Publish the migrated key identifier using the active key.
                self._write_head(sequence, record_hmac)
            if key is None:
                raise ValueError("head uses an unknown integrity key id")
            expected = self._head_hmac(sequence, record_hmac, key)
            if not hmac.compare_digest(stored_head_hmac, expected):
                raise ValueError("head mismatch")
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise QuarantineIntegrityError(
                "knowledge quarantine authenticated head verification failed"
            ) from exc

    def _verify_locked(
        self,
        *,
        allow_legacy_key_id: bool = False,
    ) -> tuple[int, str]:
        rows = self._db.execute(
            "SELECT sequence, event_id, created_at, collection, uploader, "
            "source, parser, source_sha256, verdict, policy, previous_hmac, "
            "record_hmac, integrity_key_id FROM knowledge_quarantine "
            "ORDER BY sequence"
        ).fetchall()
        previous = ""
        for expected_sequence, row in enumerate(rows, 1):
            sequence = row[0]
            if sequence != expected_sequence:
                raise QuarantineIntegrityError(
                    "knowledge quarantine sequence is discontinuous"
                )
            record = QuarantineRecord(*row[1:10])
            stored_previous = str(row[10] or "")
            stored_hmac = str(row[11] or "")
            stored_key_id = str(row[12] or "")
            key = self._integrity_keys.get(stored_key_id)
            if key is None and allow_legacy_key_id and not stored_key_id:
                candidates = [
                    key_id
                    for key_id, candidate in self._integrity_keys.items()
                    if hmac.compare_digest(
                        stored_hmac,
                        self._record_hmac(
                            sequence,
                            record,
                            previous,
                            candidate,
                        ),
                    )
                ]
                if len(candidates) != 1:
                    raise QuarantineIntegrityError(
                        "knowledge quarantine legacy row key is unknown or ambiguous"
                    )
                stored_key_id = candidates[0]
                key = self._integrity_keys[stored_key_id]
                self._db.execute(
                    "UPDATE knowledge_quarantine SET integrity_key_id = ? "
                    "WHERE sequence = ?",
                    (stored_key_id, sequence),
                )
            if key is None:
                raise QuarantineIntegrityError(
                    "knowledge quarantine row uses an unknown integrity key id"
                )
            expected_hmac = self._record_hmac(
                sequence,
                record,
                previous,
                key,
            )
            if (
                stored_previous != previous
                or len(stored_hmac) != 64
                or not hmac.compare_digest(stored_hmac, expected_hmac)
            ):
                raise QuarantineIntegrityError(
                    "knowledge quarantine HMAC chain verification failed"
                )
            previous = stored_hmac
        return len(rows), previous

    def verify(self) -> None:
        if not self.chain_integrity:
            raise QuarantineIntegrityError(
                "knowledge quarantine has no HMAC integrity key"
            )
        with self._lock, self._transaction_lock():
            self._db.execute("BEGIN IMMEDIATE")
            try:
                sequence, record_hmac = self._verify_locked()
                self._verify_head(sequence, record_hmac)
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def record(
        self,
        *,
        collection: str,
        uploader: str,
        source: str,
        parser: str,
        source_sha256: str,
        verdict: str,
        policy: str,
    ) -> QuarantineRecord:
        digest = str(source_sha256 or "").lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("knowledge quarantine requires a SHA-256 source digest")
        record = QuarantineRecord(
            event_id=uuid.uuid4().hex,
            created_at=time.time(),
            collection=_bounded(collection, _MAX_COLLECTION),
            uploader=_bounded(uploader, _MAX_UPLOADER),
            source=_bounded(source, _MAX_SOURCE),
            parser=_bounded(parser, _MAX_PARSER),
            source_sha256=digest,
            verdict=_bounded(verdict, _MAX_VERDICT),
            policy=_bounded(policy, _MAX_POLICY),
        )
        with self._lock, self._transaction_lock():
            try:
                self._db.execute("BEGIN IMMEDIATE")
                if self.chain_integrity:
                    current_sequence, current_hmac = self._verify_locked()
                    # Check the external checkpoint before deriving the next
                    # sequence. Tail deletion must never be "healed" by append.
                    self._verify_head(current_sequence, current_hmac)
                row = self._db.execute(
                    "SELECT sequence, record_hmac FROM knowledge_quarantine "
                    "ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                sequence = int(row[0]) + 1 if row else 1
                previous_hmac = str(row[1] or "") if row else ""
                record_hmac = (
                    self._record_hmac(
                        sequence,
                        record,
                        previous_hmac,
                        self._integrity_key,
                    )
                    if self.chain_integrity
                    else ""
                )
                self._db.execute(
                    "INSERT INTO knowledge_quarantine "
                    "(sequence, event_id, created_at, collection, uploader, "
                    "source, parser, source_sha256, verdict, policy, "
                    "previous_hmac, record_hmac, integrity_key_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        sequence,
                        record.event_id,
                        record.created_at,
                        record.collection,
                        record.uploader,
                        record.source,
                        record.parser,
                        record.source_sha256,
                        record.verdict,
                        record.policy,
                        previous_hmac,
                        record_hmac,
                        self.integrity_key_id,
                    ),
                )
                if self.chain_integrity:
                    # The OS lock remains held across both resources. Writing
                    # the head first makes a crash/commit failure fail closed
                    # as an ahead-of-DB checkpoint instead of erasing evidence.
                    self._write_head(sequence, record_hmac)
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise
        return record

    def records(self) -> list[QuarantineRecord]:
        with self._lock, self._transaction_lock():
            if self.chain_integrity:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    sequence, record_hmac = self._verify_locked()
                    self._verify_head(sequence, record_hmac)
                    rows = self._db.execute(
                        "SELECT event_id, created_at, collection, uploader, source, "
                        "parser, source_sha256, verdict, policy "
                        "FROM knowledge_quarantine ORDER BY sequence"
                    ).fetchall()
                    self._db.commit()
                except BaseException:
                    self._db.rollback()
                    raise
                return [QuarantineRecord(*row) for row in rows]
            rows = self._db.execute(
                "SELECT event_id, created_at, collection, uploader, source, "
                "parser, source_sha256, verdict, policy "
                "FROM knowledge_quarantine ORDER BY sequence"
            ).fetchall()
        return [QuarantineRecord(*row) for row in rows]

    def close(self) -> None:
        db = getattr(self, "_db", None)
        if db is not None:
            db.close()
            self._db = None
