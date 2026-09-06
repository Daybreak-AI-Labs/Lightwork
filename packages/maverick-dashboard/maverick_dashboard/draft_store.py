"""Private encrypted recovery copies; restoring never mutates a live resource."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
from pathlib import Path

from maverick.crypto_at_rest import is_sealed, seal, unseal
from maverick.file_lock import (
    _windows_extended_path,
    atomic_read_bytes,
    atomic_write_bytes,
    cross_process_lock,
    ensure_private_directory,
)
from maverick.paths import data_dir
from maverick.safety.secret_detector import scan

_LOCK = threading.RLock()
_SLOT = re.compile(r"[0-9a-f]{64}\Z")
_SECRET = re.compile(r"(?:^|_)(?:password|passwd|passphrase|secret|credentials|api_key|apikey|token|client_secret|private_key|authorization|cookie)(?:$|_)", re.IGNORECASE)
_ASSIGNMENT = re.compile(r"\b(?:password|passwd|api[_ -]?key|client[_ -]?secret|access[_ -]?token|authorization)\s*[=:]\s*\S+", re.IGNORECASE)
MAX_BYTES = 1024 * 1024


class DraftConflict(ValueError):
    """A newer recovery copy must be reviewed before replacing it."""


def validate_payload(payload: dict | None) -> str:
    """Refuse credentials even in nested objects or serialized JSON fields."""
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_BYTES:
        raise ValueError("This draft is too large for recovery storage (1 MiB maximum).")
    pending = [(payload, 0)]
    visited = 0
    while pending:
        value, depth = pending.pop()
        visited += 1
        if depth > 25 or visited > 20_000:
            raise ValueError("This draft is too complex for recovery storage.")
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = re.sub(r"([a-z])([A-Z])", r"\1_\2", key).lower().replace("-", "_")
                reference = normalized.endswith(("_ref", "_env", "_reference", "_id"))
                if _SECRET.search(normalized) and not reference and child not in (None, "", False):
                    raise ValueError("Recovery copies cannot contain passwords, tokens, or credentials. Use saved connection references.")
                pending.append((child, depth + 1))
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
        elif isinstance(value, str):
            if scan(value) or _ASSIGNMENT.search(value):
                raise ValueError("Recovery copies cannot contain credentials. Remove the credential or use a saved connection reference.")
            if value.lstrip().startswith(("{", "[")):
                try:
                    decoded = json.loads(value)
                except ValueError:
                    pass
                else:
                    pending.append((decoded, depth + 1))
    return encoded


class DraftStore:
    def __init__(self, context: str, *, directory: Path | None = None):
        if not _SLOT.fullmatch(context):
            raise ValueError("invalid draft context")
        self.context = context
        self.directory = directory or data_dir("draft-recovery", context)
        # Atomic publication already supports long Win32 paths. Reads, size
        # checks and existence-based CAS must resolve that exact same file.
        if os.name == "nt":
            self.directory = Path(_windows_extended_path(self.directory))

    def _path(self, slot: str) -> Path:
        if not _SLOT.fullmatch(slot):
            raise ValueError("invalid draft slot")
        return self.directory / (slot + ".draft")

    def _read(self, path: Path, slot: str) -> dict:
        if not path.exists():
            return {"context": self.context, "slot": slot, "revision": 0, "payload": None, "updated_at": None}
        if path.stat().st_size > MAX_BYTES * 2:
            raise ValueError("Recovery copy is too large to read safely.")
        raw = atomic_read_bytes(path)
        if not is_sealed(raw):
            raise ValueError("Recovery copy encryption could not be verified.")
        result = json.loads(unseal(raw))
        if (not isinstance(result, dict) or result.get("context") != self.context or result.get("slot") != slot
                or not isinstance(result.get("revision"), int) or result["revision"] < 1):
            raise ValueError("Recovery copy binding could not be verified.")
        return result

    @staticmethod
    def _public(record: dict) -> dict:
        return {key: record[key] for key in ("context", "slot", "revision", "payload", "updated_at")}

    def read(self, slot: str) -> dict:
        path = self._path(slot)
        ensure_private_directory(self.directory)
        with _LOCK, cross_process_lock(self.directory / "index", strict=True):
            return self._public(self._read(path, slot))

    def save(self, slot: str, payload: dict | None, *, expected_revision: int, operation_id: str) -> dict:
        encoded = validate_payload(payload)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        path = self._path(slot)
        ensure_private_directory(self.directory)
        with _LOCK, cross_process_lock(self.directory / "index", strict=True):
            record = self._read(path, slot)
            if record.get("operation_id") == operation_id:
                if expected_revision != record["revision"] - 1 or not hmac.compare_digest(record.get("payload_sha256", ""), digest):
                    raise DraftConflict("This retry reference was already used for different draft content.")
                return self._public(record)
            if record["revision"] != expected_revision:
                raise DraftConflict("A newer recovery copy exists. Review it before replacing it.")
            if not path.exists() and len(list(self.directory.glob("*.draft"))) >= 2000:
                raise ValueError("Recovery storage has reached its limit. Ask your administrator to archive old recovery records.")
            record = {"context": self.context, "slot": slot, "revision": record["revision"] + 1,
                      "payload": payload, "updated_at": time.time(), "operation_id": operation_id, "payload_sha256": digest}
            atomic_write_bytes(path, seal(json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")))
            return self._public(record)
