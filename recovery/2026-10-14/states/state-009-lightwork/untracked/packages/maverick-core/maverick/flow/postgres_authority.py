"""Shared Postgres authority for governed flow state.

The flow engine historically stored definitions, publication pointers, runs,
idempotency claims, and reconciliation records as independent JSON files.  A
single control-plane process made the file locks usable, but Postgres-backed
production still had two independent sources of durable authority.  This
adapter keeps the existing store semantics while moving every authoritative
flow document into one tenant-RLS table.

Documents are sealed with the same application-layer codec as the Postgres
world model.  Only bounded namespace/key metadata remains queryable.  Short
definition mutations use transaction-scoped advisory locks; long execution and
approval transitions use bounded session advisory locks so the durable
``running``/``resuming`` writes commit before an external effect is attempted.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


class FlowAuthorityUnavailable(RuntimeError):
    """The configured shared flow authority cannot be used safely."""


class FlowAuthorityIntegrityError(RuntimeError):
    """A shared authority record is malformed, corrupt, or contradictory."""


@dataclass(frozen=True)
class _Transaction:
    cursor: Any
    tenant_id: str


_ACTIVE_TX: contextvars.ContextVar[_Transaction | None] = contextvars.ContextVar(
    "maverick_flow_authority_tx", default=None,
)
_HELD_SESSION_LOCKS: contextvars.ContextVar[frozenset[int]] = (
    contextvars.ContextVar("maverick_flow_authority_session_locks", default=frozenset())
)

_MAX_NAMESPACE = 240
_MAX_KEY = 512
_LEGACY_TENANT = "__maverick_legacy_flow_authority__"


def _lock_id(material: str) -> int:
    digest = hashlib.sha256(material.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


def _canonical_document(document: dict) -> str:
    try:
        return json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise FlowAuthorityIntegrityError(
            "flow authority document is not canonical JSON"
        ) from exc


def _validate_address(namespace: str, key: str) -> tuple[str, str]:
    namespace = str(namespace or "")
    key = str(key or "")
    if (
        not namespace
        or len(namespace) > _MAX_NAMESPACE
        or any(ord(char) < 32 or ord(char) == 127 for char in namespace)
    ):
        raise FlowAuthorityIntegrityError("flow authority namespace is invalid")
    if (
        not key
        or len(key) > _MAX_KEY
        or any(ord(char) < 32 or ord(char) == 127 for char in key)
    ):
        raise FlowAuthorityIntegrityError("flow authority key is invalid")
    return namespace, key


def _is_postgres_driver_error(exc: BaseException) -> bool:
    """Identify driver/pool failures without importing optional dependencies.

    Exceptions raised by the caller while a transaction is open must retain
    their exact type (for example ``FlowVersionConflict``).  Psycopg and
    psycopg-pool exceptions have stable module roots, which lets this adapter
    translate only database failures into the public availability error even
    when the optional Postgres extra is absent at import time.
    """
    module = type(exc).__module__.split(".", 1)[0]
    return module in {"psycopg", "psycopg_pool"}


def _cleanup_session_lock(cursor: Any, connection: Any, lock: int, acquired: bool) -> None:
    """Release a session lock and close its resources on every outcome."""
    release_error: FlowAuthorityUnavailable | None = None
    cleanup_error: FlowAuthorityUnavailable | None = None
    if acquired and cursor is not None:
        try:
            cursor.execute("SELECT pg_advisory_unlock(%s)", (lock,))
            released = cursor.fetchone()
            if not released or released[0] is not True:
                raise FlowAuthorityUnavailable(
                    "Postgres flow authority lock ownership was lost"
                )
        except FlowAuthorityUnavailable as exc:
            release_error = exc
        except Exception as exc:
            release_error = FlowAuthorityUnavailable(
                "Postgres flow authority lock release failed"
            )
            release_error.__cause__ = exc
    if cursor is not None:
        try:
            cursor.close()
        except Exception as exc:
            cleanup_error = FlowAuthorityUnavailable(
                "Postgres flow authority lock cursor cleanup failed"
            )
            cleanup_error.__cause__ = exc
    if connection is not None:
        try:
            connection.close()
        except Exception as exc:
            cleanup_error = FlowAuthorityUnavailable(
                "Postgres flow authority lock connection cleanup failed"
            )
            cleanup_error.__cause__ = exc
    if release_error is not None:
        raise release_error
    if cleanup_error is not None:
        raise cleanup_error


class PostgresFlowAuthority:
    """Encrypted tenant-scoped document store over ``PostgresWorldModel``."""

    def __init__(self, world: Any):
        if world is None or not callable(getattr(world, "_tx", None)):
            raise FlowAuthorityUnavailable("Postgres flow authority has no transaction API")
        self._world = world
        try:
            from ..world_model_backends.postgres import _pool_size

            configured_pool = _pool_size()
        except Exception as exc:
            raise FlowAuthorityUnavailable(
                "Postgres flow authority connection budget is invalid"
            ) from exc
        # A long session lock cannot borrow from the world-model pool: with a
        # pool of one, the lock holder would consume the only connection and
        # deadlock its own durable run writes. A separate, equally bounded lock
        # budget keeps total connections finite while reserving the main pool
        # for transactions. Development's unpooled mode gets a small ceiling.
        self._lock_slots = threading.BoundedSemaphore(configured_pool or 8)

    @classmethod
    def open(cls) -> PostgresFlowAuthority:
        try:
            from ..world_model_backends.postgres import open_postgres_world

            return cls(open_postgres_world())
        except Exception as exc:
            raise FlowAuthorityUnavailable(
                "shared Postgres flow authority is unavailable"
            ) from exc

    def _tenant_id(self) -> str:
        try:
            from ..production_scope import production_surface_required
            from ..world_model_backends.postgres import _active_tenant

            tenant = _active_tenant()
            production = production_surface_required()
            rls = bool(getattr(self._world, "_rls", False))
        except Exception as exc:
            raise FlowAuthorityUnavailable(
                "flow authority tenant policy is unavailable"
            ) from exc
        if tenant is not None:
            return str(tenant)
        if production or rls:
            raise FlowAuthorityUnavailable(
                "refusing unbound access to the tenant-scoped Postgres flow authority"
            )
        return _LEGACY_TENANT

    @staticmethod
    def _seal(document: dict) -> str:
        from ..world_model_backends.postgres import _seal

        return str(_seal(_canonical_document(document)))

    @staticmethod
    def _unseal(value: object) -> dict:
        from ..world_model_backends.postgres import _unseal

        try:
            raw = json.loads(str(_unseal(value)))
        except Exception as exc:
            raise FlowAuthorityIntegrityError(
                "flow authority document is unreadable"
            ) from exc
        if not isinstance(raw, dict):
            raise FlowAuthorityIntegrityError(
                "flow authority document must be a JSON object"
            )
        return raw

    @contextmanager
    def _cursor(self) -> Iterator[tuple[Any, str]]:
        tenant = self._tenant_id()
        active = _ACTIVE_TX.get()
        if active is not None:
            if active.tenant_id != tenant:
                raise FlowAuthorityUnavailable(
                    "tenant changed during a flow authority transaction"
                )
            yield active.cursor, tenant
            return
        # Do not blanket-wrap exceptions crossing this yield: store-owned CAS
        # and integrity exceptions raised by the transaction body must retain
        # their exact type. Translate only driver/pool failures into the public
        # availability error; no failure grants permission to consult files.
        try:
            with self._world._tx() as cursor:
                token = _ACTIVE_TX.set(_Transaction(cursor, tenant))
                try:
                    yield cursor, tenant
                finally:
                    _ACTIVE_TX.reset(token)
        except Exception as exc:
            if _is_postgres_driver_error(exc):
                raise FlowAuthorityUnavailable(
                    "Postgres flow authority transaction is unavailable"
                ) from exc
            raise

    @contextmanager
    def transaction_lock(self, name: str) -> Iterator[None]:
        """Serialize a short multi-record mutation in one DB transaction."""
        tenant = self._tenant_id()
        lock = _lock_id(f"flow-authority\0{tenant}\0{name}")
        with self._cursor() as (cursor, _tenant):
            try:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
            except Exception as exc:
                raise FlowAuthorityUnavailable(
                    "Postgres flow authority transaction lock is unavailable"
                ) from exc
            yield

    @contextmanager
    def session_lock(self, name: str) -> Iterator[None]:
        """Hold a crash-released lock without keeping a long transaction open."""
        tenant = self._tenant_id()
        lock = _lock_id(f"flow-authority\0{tenant}\0{name}")
        held = _HELD_SESSION_LOCKS.get()
        if lock in held:
            yield
            return
        connection = None
        slot_acquired = False
        timeout_ms = max(
            100,
            int(getattr(self._world, "_lock_timeout", 5000) or 5000),
        )
        try:
            from ..world_model_backends.postgres import _connect_timeout_seconds

            connect_timeout = _connect_timeout_seconds()
            slot_acquired = self._lock_slots.acquire(
                timeout=float(connect_timeout or 30)
            )
            if not slot_acquired:
                raise FlowAuthorityUnavailable(
                    "Postgres flow authority lock connection budget is exhausted"
                )
            import psycopg

            kwargs: dict[str, object] = {
                "autocommit": True,
                # Apply this at connection startup so even the first advisory
                # query is server-bounded. A client-side deadline alone cannot
                # interrupt a stalled network/server round trip.
                "options": f"-c statement_timeout={timeout_ms}",
            }
            if connect_timeout:
                kwargs["connect_timeout"] = connect_timeout
            connection = psycopg.connect(self._world._dsn, **kwargs)
        except Exception as exc:
            if slot_acquired:
                self._lock_slots.release()
            raise FlowAuthorityUnavailable(
                "Postgres flow authority lock connection is unavailable"
            ) from exc
        acquired = False
        cursor = None
        try:
            try:
                cursor = connection.cursor()
                deadline = time.monotonic() + timeout_ms / 1000.0
                while time.monotonic() < deadline:
                    cursor.execute("SELECT pg_try_advisory_lock(%s)", (lock,))
                    row = cursor.fetchone()
                    if row and row[0] is True:
                        acquired = True
                        break
                    time.sleep(0.01)
                if not acquired:
                    raise FlowAuthorityUnavailable(
                        "Postgres flow authority lock acquisition timed out"
                    )
            except FlowAuthorityUnavailable:
                raise
            except Exception as exc:
                raise FlowAuthorityUnavailable(
                    "Postgres flow authority lock failed"
                ) from exc
            token = _HELD_SESSION_LOCKS.set(held | {lock})
            try:
                yield
            finally:
                _HELD_SESSION_LOCKS.reset(token)
        finally:
            try:
                _cleanup_session_lock(cursor, connection, lock, acquired)
            finally:
                if slot_acquired:
                    self._lock_slots.release()

    def get(self, namespace: str, key: str) -> dict | None:
        namespace, key = _validate_address(namespace, key)
        with self._cursor() as (cursor, tenant):
            cursor.execute(
                "SELECT document FROM flow_authority_records "
                "WHERE tenant_id = %s AND namespace = %s AND record_key = %s",
                (tenant, namespace, key),
            )
            row = cursor.fetchone()
        return self._unseal(row[0]) if row else None

    def exists(self, namespace: str, key: str) -> bool:
        namespace, key = _validate_address(namespace, key)
        with self._cursor() as (cursor, tenant):
            cursor.execute(
                "SELECT 1 FROM flow_authority_records "
                "WHERE tenant_id = %s AND namespace = %s AND record_key = %s",
                (tenant, namespace, key),
            )
            return cursor.fetchone() is not None

    def put(
        self,
        namespace: str,
        key: str,
        document: dict,
        *,
        immutable: bool = False,
    ) -> None:
        namespace, key = _validate_address(namespace, key)
        sealed = self._seal(document)
        now = time.time()
        with self._cursor() as (cursor, tenant):
            if immutable:
                cursor.execute(
                    "INSERT INTO flow_authority_records("
                    "tenant_id, namespace, record_key, document, updated_at) "
                    "VALUES(%s, %s, %s, %s, %s) "
                    "ON CONFLICT (tenant_id, namespace, record_key) DO NOTHING",
                    (tenant, namespace, key, sealed, now),
                )
                if int(getattr(cursor, "rowcount", 0) or 0) == 1:
                    return
                cursor.execute(
                    "SELECT document FROM flow_authority_records "
                    "WHERE tenant_id = %s AND namespace = %s AND record_key = %s",
                    (tenant, namespace, key),
                )
                row = cursor.fetchone()
                if not row or self._unseal(row[0]) != document:
                    raise FlowAuthorityIntegrityError(
                        "immutable flow authority record conflicts with existing state"
                    )
                return
            cursor.execute(
                "INSERT INTO flow_authority_records("
                "tenant_id, namespace, record_key, document, updated_at) "
                "VALUES(%s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, namespace, record_key) DO UPDATE SET "
                "document = EXCLUDED.document, updated_at = EXCLUDED.updated_at",
                (tenant, namespace, key, sealed, now),
            )

    def delete(self, namespace: str, key: str) -> bool:
        namespace, key = _validate_address(namespace, key)
        with self._cursor() as (cursor, tenant):
            cursor.execute(
                "DELETE FROM flow_authority_records "
                "WHERE tenant_id = %s AND namespace = %s AND record_key = %s",
                (tenant, namespace, key),
            )
            return int(getattr(cursor, "rowcount", 0) or 0) == 1

    def move(
        self,
        source_namespace: str,
        source_key: str,
        target_namespace: str,
        target_key: str,
    ) -> bool:
        source_namespace, source_key = _validate_address(source_namespace, source_key)
        target_namespace, target_key = _validate_address(target_namespace, target_key)
        with self._cursor() as (cursor, tenant):
            cursor.execute(
                "SELECT document FROM flow_authority_records "
                "WHERE tenant_id = %s AND namespace = %s AND record_key = %s "
                "FOR UPDATE",
                (tenant, source_namespace, source_key),
            )
            row = cursor.fetchone()
            if not row:
                return False
            cursor.execute(
                "INSERT INTO flow_authority_records("
                "tenant_id, namespace, record_key, document, updated_at) "
                "VALUES(%s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, namespace, record_key) DO UPDATE SET "
                "document = EXCLUDED.document, updated_at = EXCLUDED.updated_at",
                (tenant, target_namespace, target_key, row[0], time.time()),
            )
            cursor.execute(
                "DELETE FROM flow_authority_records "
                "WHERE tenant_id = %s AND namespace = %s AND record_key = %s",
                (tenant, source_namespace, source_key),
            )
            return True

    def list(self, namespace: str) -> list[tuple[str, dict]]:
        namespace, _dummy = _validate_address(namespace, "_")
        with self._cursor() as (cursor, tenant):
            cursor.execute(
                "SELECT record_key, document FROM flow_authority_records "
                "WHERE tenant_id = %s AND namespace = %s ORDER BY record_key",
                (tenant, namespace),
            )
            rows = list(cursor.fetchall())
        return [(str(key), self._unseal(document)) for key, document in rows]

    def delete_namespace(self, namespace: str) -> int:
        namespace, _dummy = _validate_address(namespace, "_")
        with self._cursor() as (cursor, tenant):
            cursor.execute(
                "DELETE FROM flow_authority_records "
                "WHERE tenant_id = %s AND namespace = %s",
                (tenant, namespace),
            )
            return int(getattr(cursor, "rowcount", 0) or 0)


__all__ = [
    "FlowAuthorityIntegrityError",
    "FlowAuthorityUnavailable",
    "PostgresFlowAuthority",
]
