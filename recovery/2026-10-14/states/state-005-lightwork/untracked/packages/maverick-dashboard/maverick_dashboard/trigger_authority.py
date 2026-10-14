"""Shared authority primitives for dashboard automation triggers.

Trigger definitions are tenant authority, not deployment-global configuration.
Postgres mode therefore stores one bounded governed document per active tenant.
The legacy dashboard TOML files remain the single-node compatibility plane, but
their live presence is a hard cutover blocker until an explicit importer has
published and archived the exact source bytes.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

_MAX_LEGACY_BYTES = 2 * 1024 * 1024
_MAX_LEGACY_ROWS = 512
_MAX_ACTIVE_TENANTS = 10_000
_T = TypeVar("_T")


class TriggerAuthorityError(RuntimeError):
    """Trigger authority cannot preserve its persistence contract."""


class TriggerAuthorityUnavailable(TriggerAuthorityError):
    """The selected trigger authority is unavailable or ambiguous."""


class TriggerAuthorityConflict(TriggerAuthorityError):
    """A trigger mutation lost a compare-and-swap or ownership race."""


def shared_selected() -> bool:
    """Resolve the process-sticky governed-record authority selection."""
    try:
        from maverick.governed_records import configured_governed_records_backend

        return configured_governed_records_backend() == "postgres"
    except Exception as exc:
        raise TriggerAuthorityUnavailable(
            "trigger authority selection is unavailable"
        ) from exc


def require_active_tenant() -> str:
    """Return the exact active tenant required for a shared trigger row."""
    try:
        from maverick.paths import canonical_tenant_id, current_tenant_id_strict
        from maverick.tenant.registry import assert_tenant_active

        raw = current_tenant_id_strict()
        if raw is None:
            raise TriggerAuthorityUnavailable(
                "shared trigger authority requires an active tenant"
            )
        tenant = canonical_tenant_id(raw)
        assert_tenant_active(tenant)
        return tenant
    except TriggerAuthorityError:
        raise
    except Exception as exc:
        raise TriggerAuthorityUnavailable(
            "shared trigger tenant authority is unavailable"
        ) from exc


def active_tenant_ids() -> tuple[str, ...]:
    """Enumerate a bounded, unique snapshot of currently active tenants."""
    try:
        from maverick.paths import canonical_tenant_id
        from maverick.tenant.registry import list_tenants

        records = list(list_tenants())
        if len(records) > _MAX_ACTIVE_TENANTS:
            raise TriggerAuthorityUnavailable(
                "active trigger tenant inventory exceeds its safety bound"
            )
        tenants: list[str] = []
        seen: set[str] = set()
        for record in records:
            if not bool(getattr(record, "active", False)):
                continue
            tenant = canonical_tenant_id(str(getattr(record, "id", "") or ""))
            if tenant in seen:
                raise TriggerAuthorityUnavailable(
                    "active trigger tenant inventory is ambiguous"
                )
            seen.add(tenant)
            tenants.append(tenant)
        tenants.sort()
        return tuple(tenants)
    except TriggerAuthorityError:
        raise
    except Exception as exc:
        raise TriggerAuthorityUnavailable(
            "active trigger tenants cannot be enumerated"
        ) from exc


class SharedTriggerDocument:
    """One tenant-local governed document with bounded CAS retries."""

    def __init__(self, namespace: str, prefix: str, record_type: str) -> None:
        from maverick.authority_documents import AuthorityDocumentStore

        self._document = AuthorityDocumentStore(
            namespace,
            prefix,
            record_type,
            required_backend="postgres",
        )

    def read(self) -> dict[str, Any]:
        require_active_tenant()
        try:
            return self._document.read()
        except TriggerAuthorityError:
            raise
        except Exception as exc:
            raise TriggerAuthorityUnavailable(
                "shared trigger registry read failed"
            ) from exc

    def mutate(
        self,
        callback: Callable[[dict[str, Any], float], _T],
        *,
        action: str,
        fence=None,
    ) -> _T:
        require_active_tenant()
        try:
            kwargs = {
                "action": action,
                # Never put a trigger name, owner, secret reference, or raw
                # tenant id into the governed-record audit envelope.
                "actor": "trigger-registry",
            }
            if fence is not None:
                kwargs["fence"] = fence
            return self._document.mutate_with_authoritative_time(
                callback,
                **kwargs,
            )
        except (TriggerAuthorityConflict, ValueError):
            raise
        except Exception as exc:
            raise TriggerAuthorityUnavailable(
                "shared trigger registry update failed"
            ) from exc


def read_legacy_tables(path: Path) -> tuple[dict[str, Any], str]:
    """Strictly parse a bounded legacy TOML registry and fingerprint it."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}, hashlib.sha256(b"").hexdigest()
    except OSError as exc:
        raise TriggerAuthorityUnavailable(
            "legacy trigger registry cannot be inspected"
        ) from exc
    if len(raw) > _MAX_LEGACY_BYTES:
        raise TriggerAuthorityUnavailable(
            "legacy trigger registry exceeds its migration bound"
        )
    digest = hashlib.sha256(raw).hexdigest()
    try:
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover - Python 3.10
            import tomli as tomllib  # type: ignore

        root = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise TriggerAuthorityUnavailable(
            "legacy trigger registry is unreadable"
        ) from exc
    if not isinstance(root, dict):
        raise TriggerAuthorityUnavailable(
            "legacy trigger registry root is malformed"
        )
    tables = root.get("trigger") or {}
    if not isinstance(tables, dict) or len(tables) > _MAX_LEGACY_ROWS:
        raise TriggerAuthorityUnavailable(
            "legacy trigger registry table is malformed or unbounded"
        )
    return dict(tables), digest


def assert_legacy_retired(path: Path) -> None:
    """Refuse Postgres while any local definitions remain authoritative."""
    tables, _digest = read_legacy_tables(path)
    if tables:
        raise TriggerAuthorityUnavailable(
            "legacy local trigger definitions block Postgres cutover; "
            "explicitly import and archive them first"
        )


def archive_legacy(path: Path, expected_sha256: str) -> Path | None:
    """Archive the exact inspected legacy bytes after a successful import."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        if expected_sha256 == hashlib.sha256(b"").hexdigest():
            return None
        raise TriggerAuthorityConflict(
            "legacy trigger registry disappeared during migration"
        ) from None
    except OSError as exc:
        raise TriggerAuthorityUnavailable(
            "legacy trigger registry cannot be archived"
        ) from exc
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise TriggerAuthorityConflict(
            "legacy trigger registry changed during migration"
        )
    archive = path.with_name(f"{path.stem}.migrated-{actual[:16]}{path.suffix}")
    if archive.exists():
        raise TriggerAuthorityConflict(
            "legacy trigger migration archive already exists"
        )
    try:
        path.replace(archive)
    except OSError as exc:
        raise TriggerAuthorityUnavailable(
            "legacy trigger registry could not be archived"
        ) from exc
    try:
        archived = archive.read_bytes()
    except OSError as exc:
        raise TriggerAuthorityUnavailable(
            "legacy trigger migration archive cannot be verified"
        ) from exc
    if hashlib.sha256(archived).hexdigest() != expected_sha256:
        raise TriggerAuthorityUnavailable(
            "legacy trigger migration archive verification failed"
        )
    return archive


def valid_timestamp(value: object) -> bool:
    return bool(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


__all__ = [
    "SharedTriggerDocument",
    "TriggerAuthorityConflict",
    "TriggerAuthorityError",
    "TriggerAuthorityUnavailable",
    "active_tenant_ids",
    "archive_legacy",
    "assert_legacy_retired",
    "read_legacy_tables",
    "require_active_tenant",
    "shared_selected",
    "valid_timestamp",
]
