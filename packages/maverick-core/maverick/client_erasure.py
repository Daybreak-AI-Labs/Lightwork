"""Signed offboarding receipts that survive removal of the client audit tree.

Only minimal operation metadata and the public verification key are retained
outside the client root. The existing audit signer is initialized before any
deletion and stays in memory for the final event; no private key is copied and
completion never recreates the erased tenant's key directory.

As with ordinary local audit chains, an independently held public key is needed
to detect replacement by somebody with access to the signing identity. A
request without a terminal event proves intent, never successful erasure.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .audit.events import AuditEvent
from .audit.signing import AuditSigner, trusted_audit_public_keys, verify_chain
from .file_lock import (
    atomic_create_text,
    atomic_read_text,
    cross_process_lock,
    ensure_private_directory,
)


class ClientErasureError(RuntimeError):
    """An erase was refused, incomplete, or could not confirm its final record."""

    def __init__(self, message: str, *, receipt_id: str = "", removed: int = 0):
        self.receipt_id = receipt_id
        self.removed = removed
        suffix = f" Activity receipt: {receipt_id}." if receipt_id else ""
        super().__init__(message + suffix)


class ErasureReceipt:
    """One private, verifiable intent, prepared-inventory and outcome chain."""

    def __init__(self, home: Path, detail: dict[str, Any]):
        self.id = uuid.uuid4().hex
        self.detail = detail
        directory = home / "client-erasure" / self.id
        self.path = directory / "events.ndjson"
        try:
            ensure_private_directory(home)
            ensure_private_directory(directory.parent)
            ensure_private_directory(directory)
            atomic_create_text(self.path, "")
            self.signer = AuditSigner(self.path)
            event = self._event("client_erase_requested", status="requested")
            if self.signer.write(event) is not True:
                raise OSError("the initial receipt was not saved")
            first = json.loads(atomic_read_text(self.path).splitlines()[0])
            self.public_key = trusted_audit_public_keys()[first["key_id"]]
            atomic_create_text(directory / "verification-key.hex", self.public_key + "\n")
            self._verify()
        # failure-policy: fail_closed
        except Exception as exc:
            raise ClientErasureError(
                "The signed activity receipt could not be saved and verified. Nothing was erased.",
                receipt_id=self.id,
            ) from exc

    def _event(self, kind: str, **result: Any) -> dict[str, Any]:
        return AuditEvent(
            ts=time.time(), kind=kind, agent="client-maintenance",
            payload={**self.detail, "event_id": self.id, **result},
        ).to_dict()

    def _verify(self) -> None:
        if verify_chain(self.path, self.public_key):
            raise ValueError("the operation receipt could not be verified")

    def _append(self, kind: str, **result: Any) -> None:
        with cross_process_lock(self.path, strict=True):
            self._verify()
            if self.signer.write(self._event(kind, **result)) is not True:
                raise OSError("the receipt event was not saved")
            self._verify()

    def prepare(self, targets: dict[str, Any]) -> None:
        """Commit the actual deletion inventory after signing is provisioned."""
        try:
            self._append("client_erase_ready", status="prepared", **targets)
            self.detail.update(targets)
        # failure-policy: fail_closed
        except Exception as exc:
            raise ClientErasureError(
                "The prepared deletion inventory could not be saved and verified. Nothing was erased.",
                receipt_id=self.id,
            ) from exc

    def finish(self, *, complete: bool, removed: int, failures: int) -> None:
        """Record the observed result, never inferring success from a request."""
        try:
            self._append(
                "client_erased" if complete else "client_erase_incomplete",
                status="completed" if complete else "incomplete",
                removed=removed, failures=failures,
            )
        # failure-policy: fail_closed
        except Exception as exc:
            raise ClientErasureError(
                "Deletion was attempted, but its final activity receipt could not be confirmed. "
                "Check the remaining data before trying again.",
                receipt_id=self.id, removed=removed,
            ) from exc


__all__ = ["ClientErasureError", "ErasureReceipt"]
