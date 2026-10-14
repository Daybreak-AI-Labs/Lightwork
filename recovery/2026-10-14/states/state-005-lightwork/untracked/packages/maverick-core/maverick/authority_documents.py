"""CAS documents for small mutable authority families.

Several Lightwork authorization stores are naturally one bounded document: a
revocation map, a trust roster, or a replay-claim set.  Reimplementing Postgres
selection, RLS, encryption, CAS, and audit delivery in every one of those
modules would create another control plane.  This adapter keeps the existing
single-replica formats while giving shared deployments one transactionally
governed document primitive over :mod:`maverick.governed_records`.

Callers remain responsible for validating their document schema.  This module
owns only defensive copying, monotonic revision CAS, bounded retry on races,
and the durable governed-record audit outbox.
"""
from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from .governed_records import (
    GovernedMutationFence,
    GovernedRecordBackend,
    GovernedRecordStore,
)
from .privacy_ops import RecordConflict

_SCHEMA_VERSION = 1
_DEFAULT_RETRIES = 16
_T = TypeVar("_T")


class AuthorityDocumentError(RuntimeError):
    """The shared authority document cannot preserve its CAS contract."""


@dataclass(frozen=True)
class AuthoritySnapshot:
    """One immutable caller-owned view of an authority document."""

    revision: int
    document: dict[str, Any]


class AuthorityDocumentStore(Generic[_T]):
    """One stable governed record updated through revision CAS.

    ``mutate`` callbacks can be retried after a concurrent writer wins, so they
    must be deterministic and side-effect free.  Their return value is emitted
    only from the successful attempt. ``required_backend`` pins the complete
    selector identity lazily on first I/O; shared authorities should require
    ``"postgres"`` so one CAS operation cannot cross persistence planes.
    """

    def __init__(
        self,
        namespace: str,
        prefix: str,
        record_type: str,
        *,
        document_name: str = "state",
        backend: GovernedRecordBackend | None = None,
        required_backend: str | None = None,
        authority_scope: str = "tenant",
    ) -> None:
        self._records = GovernedRecordStore(
            namespace,
            prefix,
            record_type,
            backend=backend,
            required_backend=required_backend,
            authority_scope=authority_scope,
        )
        self._record_id = f"{prefix}-{document_name}"

    @property
    def backend_kind(self) -> str:
        return self._records.backend_kind

    def authoritative_time(self) -> float:
        """Return the selected backend's clock for cross-replica decisions."""
        return self._records.authoritative_time()

    @staticmethod
    def _revision(record: dict[str, Any]) -> int:
        value = record.get("revision")
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise AuthorityDocumentError(
                "authority document has an invalid revision"
            )
        return value

    @staticmethod
    def _document(record: dict[str, Any]) -> dict[str, Any]:
        schema = record.get("schema_version")
        if schema != _SCHEMA_VERSION:
            raise AuthorityDocumentError(
                "authority document has an unsupported schema version"
            )
        document = record.get("document")
        if not isinstance(document, dict):
            raise AuthorityDocumentError(
                "authority document payload must be an object"
            )
        return copy.deepcopy(document)

    def snapshot(self) -> AuthoritySnapshot:
        record = self._records.get(self._record_id)
        if record is None:
            return AuthoritySnapshot(0, {})
        return AuthoritySnapshot(
            self._revision(record),
            self._document(record),
        )

    def read(self) -> dict[str, Any]:
        """Return a defensive copy of the current document, or an empty map."""
        return self.snapshot().document

    def mutation_fence(
        self,
        callback: Callable[[dict[str, Any], float], None],
    ) -> GovernedMutationFence:
        """Bind a document predicate to another row's shared transaction.

        The returned value is inert until supplied to a governed create/update.
        PostgreSQL then locks this authority row, obtains database time, and
        evaluates ``callback`` inside the same transaction as the target write.
        This closes the expiry/takeover window that a separate lease check
        cannot close.  Local backends reject the primitive; their caller must
        keep using a strict local critical section.
        """
        if not callable(callback):
            raise TypeError("authority document fence callback must be callable")

        def _validate(record: dict[str, Any], now: float) -> None:
            callback(self._document(record), now)

        return GovernedMutationFence(
            namespace=self._records.namespace,
            prefix=self._records.prefix,
            record_id=self._record_id,
            authority_scope=self._records.authority_scope,
            validate=_validate,
        )

    def mutate(
        self,
        callback: Callable[[dict[str, Any]], _T],
        *,
        action: str,
        actor: str = "system",
        max_retries: int = _DEFAULT_RETRIES,
        fence: GovernedMutationFence | None = None,
    ) -> _T:
        """Atomically mutate the document and return the callback result."""
        if not callable(callback):
            raise TypeError("authority document callback must be callable")
        if (
            not isinstance(max_retries, int)
            or isinstance(max_retries, bool)
            or max_retries < 1
            or max_retries > 128
        ):
            raise ValueError("max_retries must be an integer from 1 to 128")

        for _attempt in range(max_retries):
            record = self._records.get(self._record_id)
            if record is None:
                document: dict[str, Any] = {}
                result = callback(document)
                try:
                    value = {
                        "id": self._record_id,
                        "schema_version": _SCHEMA_VERSION,
                        "document": document,
                    }
                    if fence is None:
                        self._records.create(value, action=action, actor=actor)
                    else:
                        self._records.create(
                            value,
                            action=action,
                            actor=actor,
                            fence=fence,
                        )
                except RecordConflict:
                    continue
                return result

            revision = self._revision(record)
            result_box: list[_T] = []

            def _apply(
                current: dict[str, Any],
                *,
                _result_box: list[_T] = result_box,
                _callback: Callable[[dict[str, Any]], _T] = callback,
            ) -> None:
                document = self._document(current)
                _result_box.append(_callback(document))
                current["schema_version"] = _SCHEMA_VERSION
                current["document"] = document

            try:
                if fence is None:
                    saved = self._records.update(
                        self._record_id,
                        _apply,
                        expected_revision=revision,
                        action=action,
                        actor=actor,
                    )
                else:
                    saved = self._records.update(
                        self._record_id,
                        _apply,
                        expected_revision=revision,
                        action=action,
                        actor=actor,
                        fence=fence,
                    )
            except RecordConflict:
                continue
            if saved is None or not result_box:
                continue
            return result_box[0]

        raise AuthorityDocumentError(
            "authority document changed too frequently to commit safely"
        )

    def mutate_with_authoritative_time(
        self,
        callback: Callable[[dict[str, Any], float], _T],
        *,
        action: str,
        actor: str = "system",
        max_retries: int = _DEFAULT_RETRIES,
        fence: GovernedMutationFence | None = None,
    ) -> _T:
        """Atomically mutate with a fresh backend timestamp on every CAS try.

        Lease, expiry, and calendar-window decisions must not capture a node or
        database time once outside the optimistic retry loop.  If another
        writer wins, the next callback invocation receives a newly queried
        authority timestamp, so a retry that crosses an expiry or UTC midnight
        cannot commit the stale attempt's decision.

        The callback has the same retry/side-effect constraints as
        :meth:`mutate`; its second argument is the selected backend's epoch
        timestamp for that attempt.
        """
        if not callable(callback):
            raise TypeError("authority document callback must be callable")
        if (
            not isinstance(max_retries, int)
            or isinstance(max_retries, bool)
            or max_retries < 1
            or max_retries > 128
        ):
            raise ValueError("max_retries must be an integer from 1 to 128")

        for _attempt in range(max_retries):
            record = self._records.get(self._record_id)
            if record is None:
                document: dict[str, Any] = {}
                now = self._records.authoritative_time()
                result = callback(document, now)
                try:
                    value = {
                        "id": self._record_id,
                        "schema_version": _SCHEMA_VERSION,
                        "document": document,
                    }
                    if fence is None:
                        self._records.create(value, action=action, actor=actor)
                    else:
                        self._records.create(
                            value,
                            action=action,
                            actor=actor,
                            fence=fence,
                        )
                except RecordConflict:
                    continue
                return result

            revision = self._revision(record)
            result_box: list[_T] = []

            def _apply(
                current: dict[str, Any],
                *,
                _result_box: list[_T] = result_box,
                _callback: Callable[[dict[str, Any], float], _T] = callback,
            ) -> None:
                document = self._document(current)
                # This executes after the backend has obtained its row lock and
                # validated the expected revision. Querying here prevents time
                # spent waiting on that lock from aging the expiry decision.
                now = self._records.authoritative_time()
                _result_box.append(_callback(document, now))
                current["schema_version"] = _SCHEMA_VERSION
                current["document"] = document

            try:
                if fence is None:
                    saved = self._records.update(
                        self._record_id,
                        _apply,
                        expected_revision=revision,
                        action=action,
                        actor=actor,
                    )
                else:
                    saved = self._records.update(
                        self._record_id,
                        _apply,
                        expected_revision=revision,
                        action=action,
                        actor=actor,
                        fence=fence,
                    )
            except RecordConflict:
                continue
            if saved is None or not result_box:
                continue
            return result_box[0]

        raise AuthorityDocumentError(
            "authority document changed too frequently to commit safely"
        )


__all__ = [
    "AuthorityDocumentError",
    "AuthorityDocumentStore",
    "AuthoritySnapshot",
]
