"""Shared tenant lifecycle and quota authority.

The compatibility implementation in :mod:`maverick.tenant.registry` stores a
deployment roster plus tenant usage files on one private filesystem.  That is
safe for one process tree, but it is not an authority shared by independent
replicas.  This module supplies the governed-Postgres implementation:

* each portable tenant namespace has one deployment-global, digest-addressed
  claim record, so aliases contend on the same create-only identity;
* each tenant has one tenant-scoped CAS document containing lifecycle, its
  explicit daily cap, usage, reservations, and bounded settlement receipts;
* cap admission and settlement therefore linearize in one per-tenant record;
* deletion is a tombstone.  It never erases namespace, usage, settlement, or
  governed audit history.

The module is intentionally internal.  Public compatibility dispatch remains
in ``tenant.registry`` and ``quotas``.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import stat
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..authority_documents import AuthorityDocumentStore
from ..governed_records import GovernedMutationFence, GovernedRecordStore
from ..paths import (
    _tenant_namespace_key,
    _tenant_segment,
    canonical_tenant_id,
    maverick_home,
    reset_tenant,
    set_tenant,
)
from ..privacy_ops import RecordConflict

SCHEMA_VERSION = 1
CLAIM_SCHEMA_VERSION = 1
ACTIVE = "active"
SUSPENDED = "suspended"
DELETED = "deleted"

_MAX_PRINCIPALS = 100_000
_MAX_USAGE_BUCKETS = 500_000
_MAX_RESERVATIONS = 100_000
_MAX_SETTLEMENTS = 100_000
_MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
_MAX_RESERVATION_TTL_SECONDS = 7 * 24 * 3600.0
# Retain exact settlement identities long beyond the six-hour runtime hold, but
# keep the idempotency set bounded.  Aggregate usage remains after receipts age
# out; only the permission to replay a month-old run id expires.
SETTLEMENT_RETENTION_DAYS = 32
EXPIRED_RESERVATION_RETENTION_DAYS = 32

_CLAIMS: TenantNamespaceClaimStore | None = None
_CLAIMS_LOCK = threading.Lock()


class TenantAuthorityError(RuntimeError):
    """Shared tenant control state is unavailable or cannot be trusted."""


def _finite_nonnegative(value: object, *, label: str) -> float:
    if isinstance(value, bool):
        raise TenantAuthorityError(f"{label} must be finite and non-negative")
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise TenantAuthorityError(
            f"{label} must be finite and non-negative"
        ) from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise TenantAuthorityError(f"{label} must be finite and non-negative")
    return parsed


def _positive_time(value: object, *, label: str) -> float:
    parsed = _finite_nonnegative(value, label=label)
    if parsed <= 0:
        raise TenantAuthorityError(f"{label} must be positive")
    return parsed


def _day(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")


def _valid_day(value: object, *, label: str = "usage day") -> str:
    if not isinstance(value, str):
        raise TenantAuthorityError(f"{label} is invalid")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise TenantAuthorityError(f"{label} is invalid") from exc
    if parsed.strftime("%Y-%m-%d") != value:
        raise TenantAuthorityError(f"{label} is invalid")
    return value


def _bounded_json(value: object) -> None:
    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (OverflowError, RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise TenantAuthorityError(
            "shared tenant authority is not bounded JSON"
        ) from exc
    if len(payload) > _MAX_DOCUMENT_BYTES:
        raise TenantAuthorityError(
            "shared tenant authority exceeds its 16 MiB document bound"
        )


def _claim_record_id(namespace_key: str) -> str:
    """Full 256-bit address that still fits the governed 64-char id bound."""
    digest = hashlib.sha256(namespace_key.encode("ascii")).digest()
    encoded = base64.b32encode(digest).decode("ascii").rstrip("=").lower()
    return f"TNC-{encoded}"


def _claim_revision(record: object) -> int:
    if not isinstance(record, dict):
        raise TenantAuthorityError("tenant namespace claim has no valid generation")
    revision = record.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise TenantAuthorityError("tenant namespace claim has no valid generation")
    return revision


def _validate_claim(record: object) -> dict[str, Any]:
    if not isinstance(record, dict) or set(record).difference({
        "_audit_pending",
        "aborted_at",
        "activated_at",
        "claimed_at",
        "created_at",
        "deleted_at",
        "id",
        "namespace_key",
        "proposed",
        "revision",
        "schema_version",
        "state",
        "tenant_id",
        "updated_at",
    }):
        raise TenantAuthorityError("tenant namespace claim has an invalid schema")
    tenant_id = record.get("tenant_id")
    namespace_key = record.get("namespace_key")
    if not isinstance(tenant_id, str) or canonical_tenant_id(tenant_id) != tenant_id:
        raise TenantAuthorityError("tenant namespace claim has an invalid owner")
    if (
        not isinstance(namespace_key, str)
        or _tenant_namespace_key(tenant_id) != namespace_key
        or record.get("id") != _claim_record_id(namespace_key)
        or record.get("schema_version") != CLAIM_SCHEMA_VERSION
    ):
        raise TenantAuthorityError("tenant namespace claim identity is inconsistent")
    claimed_at = _positive_time(record.get("claimed_at"), label="claim timestamp")
    state = record.get("state")
    if state not in ("provisioning", "aborting", "active", "aborted", "deleted"):
        raise TenantAuthorityError("tenant namespace claim state is invalid")
    proposed = record.get("proposed")
    if not isinstance(proposed, dict) or set(proposed) != {
        "display_name", "max_daily_dollars", "plan"
    }:
        raise TenantAuthorityError("tenant namespace proposal is malformed")
    plan = proposed.get("plan")
    display_name = proposed.get("display_name")
    if (
        not isinstance(plan, str)
        or not plan
        or plan != plan.strip()
        or len(plan) > 256
        or not isinstance(display_name, str)
        or len(display_name) > 4096
    ):
        raise TenantAuthorityError("tenant namespace proposal metadata is invalid")
    clean_proposed = {
        "plan": plan,
        "display_name": display_name,
        "max_daily_dollars": _finite_nonnegative(
            proposed.get("max_daily_dollars"), label="proposed tenant daily cap"
        ),
    }
    activated_raw = record.get("activated_at")
    aborted_raw = record.get("aborted_at")
    activated_at = (
        None
        if activated_raw is None
        else _positive_time(activated_raw, label="claim activation timestamp")
    )
    aborted_at = (
        None
        if aborted_raw is None
        else _positive_time(aborted_raw, label="claim abort timestamp")
    )
    deleted_raw = record.get("deleted_at")
    deleted_at = None
    if deleted_raw is not None:
        deleted_at = _positive_time(deleted_raw, label="claim deletion timestamp")
        if deleted_at < claimed_at:
            raise TenantAuthorityError("tenant namespace tombstone predates its claim")
    if state in {"provisioning", "aborting"} and any(
        value is not None for value in (activated_at, aborted_at, deleted_at)
    ):
        raise TenantAuthorityError(
            "incomplete namespace claim has terminal timestamps"
        )
    if state == "active" and (
        activated_at is None or aborted_at is not None or deleted_at is not None
    ):
        raise TenantAuthorityError("active namespace claim has invalid lifecycle evidence")
    if state == "aborted" and (
        aborted_at is None or activated_at is not None or deleted_at is not None
    ):
        raise TenantAuthorityError("aborted namespace claim has invalid lifecycle evidence")
    if state == "deleted" and (
        activated_at is None or aborted_at is not None or deleted_at is None
    ):
        raise TenantAuthorityError("deleted namespace claim has invalid lifecycle evidence")
    if any(
        value is not None and value < claimed_at
        for value in (activated_at, aborted_at, deleted_at)
    ):
        raise TenantAuthorityError("tenant namespace lifecycle predates its claim")
    return {
        "id": str(record["id"]),
        "revision": _claim_revision(record),
        "tenant_id": tenant_id,
        "namespace_key": namespace_key,
        "claimed_at": claimed_at,
        "state": state,
        "proposed": clean_proposed,
        "activated_at": activated_at,
        "aborted_at": aborted_at,
        "deleted_at": deleted_at,
    }


class TenantNamespaceClaimStore:
    """One bounded governed record per portable namespace claim."""

    def __init__(self) -> None:
        self._records = GovernedRecordStore(
            "tenant_namespace_claims",
            "TNC",
            "tenant_namespace_claim",
            required_backend="postgres",
            authority_scope="deployment_global",
        )

    @property
    def backend_kind(self) -> str:
        return self._records.backend_kind

    def authoritative_time(self) -> float:
        return self._records.authoritative_time()

    def get(self, tenant_id: str) -> dict[str, Any] | None:
        namespace_key = _tenant_namespace_key(tenant_id)
        record = self._records.get(_claim_record_id(namespace_key))
        return None if record is None else _validate_claim(record)

    def list(self) -> list[dict[str, Any]]:
        return [_validate_claim(record) for record in self._records.list()]

    def mutation_fence(
        self,
        tenant_id: str,
        *,
        state: str,
        revision: int,
    ) -> GovernedMutationFence:
        """Bind one exact namespace generation to a tenant-document write."""
        namespace_key = _tenant_namespace_key(tenant_id)
        record_id = _claim_record_id(namespace_key)
        expected_state = str(state or "")
        if expected_state not in {
            "provisioning", "aborting", "active", "aborted", "deleted"
        }:
            raise ValueError("tenant namespace fence state is invalid")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError("tenant namespace fence generation is invalid")

        def _validate(record: dict[str, Any], _now: float) -> None:
            claim = _validate_claim(record)
            if (
                claim["tenant_id"] != tenant_id
                or claim["namespace_key"] != namespace_key
                or claim["state"] != expected_state
                or claim["revision"] != revision
            ):
                raise TenantAuthorityError(
                    "tenant control mutation lost its namespace generation fence"
                )

        return GovernedMutationFence(
            namespace=self._records.namespace,
            prefix=self._records.prefix,
            record_id=record_id,
            authority_scope=self._records.authority_scope,
            validate=_validate,
        )

    def claim(
        self,
        tenant_id: str,
        *,
        proposed: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        namespace_key = _tenant_namespace_key(tenant_id)
        record_id = _claim_record_id(namespace_key)
        # Never accept a caller/node timestamp for namespace identity.
        now = self._records.authoritative_time()
        proposed = {
            "id": record_id,
            "schema_version": CLAIM_SCHEMA_VERSION,
            "tenant_id": tenant_id,
            "namespace_key": namespace_key,
            "claimed_at": now,
            "state": "provisioning",
            "proposed": copy.deepcopy(proposed),
            "activated_at": None,
            "aborted_at": None,
            "deleted_at": None,
        }
        try:
            saved = self._records.create(
                proposed,
                action="tenant_namespace.claim",
                actor="tenant-operator",
            )
            return _validate_claim(saved), True
        except RecordConflict:
            existing = self._records.get(record_id)
            if existing is None:
                raise TenantAuthorityError(
                    "tenant namespace claim conflicted and then disappeared"
                ) from None
            return _validate_claim(existing), False

    def _transition(
        self,
        tenant_id: str,
        *,
        target: str,
        expected_revision: int | None = None,
    ) -> bool:
        namespace_key = _tenant_namespace_key(tenant_id)
        record_id = _claim_record_id(namespace_key)
        for _attempt in range(16):
            record = self._records.get(record_id)
            if record is None:
                return False
            claim = _validate_claim(record)
            if claim["tenant_id"] != tenant_id:
                raise TenantAuthorityError(
                    "tenant namespace is owned by a different canonical tenant"
                )
            if claim["state"] == target:
                return False
            if (
                expected_revision is not None
                and claim["revision"] != expected_revision
            ):
                raise TenantAuthorityError(
                    "tenant namespace lifecycle generation changed"
                )
            transition = {
                "active": (
                    "provisioning",
                    "only a provisioning namespace claim can activate",
                ),
                "aborting": (
                    "provisioning",
                    "only a provisioning namespace claim can begin abort",
                ),
                "aborted": (
                    "aborting",
                    "only an aborting namespace claim can finish abort",
                ),
                "deleted": (
                    "active",
                    "only an active namespace claim can tombstone",
                ),
            }.get(target)
            if transition is None:  # pragma: no cover - private caller invariant
                raise ValueError("unknown tenant claim transition")
            expected_state, invalid_transition = transition
            if claim["state"] != expected_state:
                raise TenantAuthorityError(invalid_transition)

            def _apply(
                current: dict[str, Any],
                *,
                expected_state: str = str(claim["state"]),
            ) -> None:
                checked = _validate_claim(current)
                if (
                    checked["tenant_id"] != tenant_id
                    or checked["state"] != expected_state
                ):
                    raise RecordConflict("tenant namespace lifecycle changed")
                # Runs after the backend has acquired and validated the row.
                # Each lost CAS invokes a new callback and therefore a fresh
                # database timestamp instead of reusing the caller's clock.
                now = self._records.authoritative_time()
                current["state"] = target
                if target == "active":
                    current["activated_at"] = now
                elif target == "aborted":
                    current["aborted_at"] = now
                elif target == "deleted":
                    current["deleted_at"] = now

            try:
                saved = self._records.update(
                    record_id,
                    _apply,
                    expected_revision=int(record.get("revision") or 0),
                    action=f"tenant_namespace.{target}",
                    actor="tenant-operator",
                )
            except RecordConflict:
                continue
            if saved is not None:
                _validate_claim(saved)
                return True
        raise TenantAuthorityError("tenant namespace changed too frequently")

    def activate(
        self,
        tenant_id: str,
        *,
        expected_revision: int | None = None,
    ) -> bool:
        return self._transition(
            tenant_id,
            target="active",
            expected_revision=expected_revision,
        )

    def begin_abort(self, tenant_id: str) -> tuple[dict[str, Any], bool]:
        """Claim the abort outcome before any tenant-document cleanup."""
        current = self.get(tenant_id)
        if current is None:
            raise TenantAuthorityError("tenant namespace claim is unavailable")
        if current["state"] in {"aborting", "aborted"}:
            return current, False
        if current["state"] != "provisioning":
            raise TenantAuthorityError("only incomplete provisioning can be aborted")
        changed = self._transition(
            tenant_id,
            target="aborting",
            expected_revision=current["revision"],
        )
        updated = self.get(tenant_id)
        if updated is None or updated["state"] not in {"aborting", "aborted"}:
            raise TenantAuthorityError("tenant namespace abort claim is unavailable")
        return updated, changed

    def finish_abort(self, tenant_id: str, *, expected_revision: int) -> bool:
        return self._transition(
            tenant_id,
            target="aborted",
            expected_revision=expected_revision,
        )

    def tombstone(self, tenant_id: str) -> bool:
        return self._transition(tenant_id, target="deleted")


def _new_claim_store() -> TenantNamespaceClaimStore:
    return TenantNamespaceClaimStore()


def _claim_store() -> TenantNamespaceClaimStore:
    global _CLAIMS
    with _CLAIMS_LOCK:
        if _CLAIMS is None:
            _CLAIMS = _new_claim_store()
        return _CLAIMS


def _new_tenant_store(_tenant_id: str) -> AuthorityDocumentStore[Any]:
    return AuthorityDocumentStore(
        "tenant_control_authority",
        "TCA",
        "tenant_control_authority",
        document_name="state",
        required_backend="postgres",
    )


@contextmanager
def _tenant_scope(tenant_id: str):
    """Bind an exact tenant for record I/O and always restore ambient scope."""
    token = set_tenant(tenant_id)
    try:
        yield
    finally:
        reset_tenant(token)


def _require_postgres(store: object) -> None:
    try:
        backend = store.backend_kind  # type: ignore[attr-defined]
    except Exception as exc:
        raise TenantAuthorityError("shared tenant authority is unavailable") from exc
    if backend != "postgres":
        raise TenantAuthorityError("shared tenant authority requires governed Postgres")


def _legacy_registry_path() -> Path:
    return maverick_home() / "tenant_registry.json"


def _refuse_path(path: Path, *, label: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise TenantAuthorityError(f"legacy {label} state cannot be inspected") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise TenantAuthorityError(f"legacy {label} state is not a regular file")
    # Even an empty file is ambiguous: it proves this deployment previously
    # selected the local persistence plane but supplies no import checkpoint.
    raise TenantAuthorityError(
        f"legacy {label} state exists; migrate/import it into Postgres and "
        "archive the local file before enabling shared tenant authority"
    )


def refuse_legacy_registry_state() -> None:
    _refuse_path(_legacy_registry_path(), label="tenant registry")


def _legacy_usage_paths(tenant_id: str) -> tuple[Path, Path]:
    segment = _tenant_segment(tenant_id)
    root = maverick_home() / "tenants" / segment / "usage"
    return root / "ledger.json", root / "reservations.json"


def refuse_legacy_usage_state(tenant_id: str) -> None:
    for path, label in zip(
        _legacy_usage_paths(tenant_id),
        ("tenant usage ledger", "tenant reservation"),
        strict=True,
    ):
        _refuse_path(path, label=label)


def _validate_principal(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 1024
    ):
        raise TenantAuthorityError("shared usage contains an invalid principal")
    return value


def _validate_usage(raw: object) -> dict[str, dict[str, dict[str, int | float]]]:
    if not isinstance(raw, dict) or len(raw) > _MAX_PRINCIPALS:
        raise TenantAuthorityError("shared tenant usage roster is invalid")
    usage: dict[str, dict[str, dict[str, int | float]]] = {}
    buckets = 0
    for raw_principal, raw_days in raw.items():
        principal = _validate_principal(raw_principal)
        if not isinstance(raw_days, dict):
            raise TenantAuthorityError("shared tenant usage days are invalid")
        clean_days: dict[str, dict[str, int | float]] = {}
        for raw_day, raw_cell in raw_days.items():
            buckets += 1
            if buckets > _MAX_USAGE_BUCKETS or not isinstance(raw_cell, dict):
                raise TenantAuthorityError("shared tenant usage exceeds its bucket bound")
            day = _valid_day(raw_day)
            if set(raw_cell) != {"dollars", "in_tokens", "out_tokens"}:
                raise TenantAuthorityError("shared tenant usage cell is malformed")
            dollars = _finite_nonnegative(raw_cell.get("dollars"), label="usage dollars")
            in_tokens = raw_cell.get("in_tokens")
            out_tokens = raw_cell.get("out_tokens")
            if (
                not isinstance(in_tokens, int)
                or isinstance(in_tokens, bool)
                or in_tokens < 0
                or not isinstance(out_tokens, int)
                or isinstance(out_tokens, bool)
                or out_tokens < 0
            ):
                raise TenantAuthorityError("shared tenant token usage is invalid")
            clean_days[day] = {
                "dollars": dollars,
                "in_tokens": in_tokens,
                "out_tokens": out_tokens,
            }
        usage[principal] = clean_days
    return usage


def _validate_reservation_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 1024
    ):
        raise TenantAuthorityError("shared tenant reservation id is invalid")
    return value


def _validate_reservations(raw: object) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or len(raw) > _MAX_RESERVATIONS:
        raise TenantAuthorityError("shared tenant reservations exceed their bound")
    reservations: dict[str, dict[str, Any]] = {}
    for raw_id, raw_item in raw.items():
        reservation_id = _validate_reservation_id(raw_id)
        if not isinstance(raw_item, dict) or set(raw_item) != {
            "created_at", "day", "dollars", "expires_at"
        }:
            raise TenantAuthorityError("shared tenant reservation is malformed")
        created_at = _positive_time(
            raw_item.get("created_at"), label="reservation creation timestamp"
        )
        expires_at = _positive_time(
            raw_item.get("expires_at"), label="reservation expiry timestamp"
        )
        if expires_at <= created_at or expires_at - created_at > _MAX_RESERVATION_TTL_SECONDS:
            raise TenantAuthorityError("shared tenant reservation TTL is invalid")
        dollars = _finite_nonnegative(
            raw_item.get("dollars"), label="reservation dollars"
        )
        if dollars <= 0:
            raise TenantAuthorityError("shared tenant reservation must be positive")
        reservations[reservation_id] = {
            "created_at": created_at,
            "day": _valid_day(raw_item.get("day"), label="reservation day"),
            "dollars": dollars,
            "expires_at": expires_at,
        }
    return reservations


def _validate_settlements(raw: object) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or len(raw) > _MAX_SETTLEMENTS:
        raise TenantAuthorityError("shared tenant settlements exceed their bound")
    settlements: dict[str, dict[str, Any]] = {}
    for raw_id, raw_item in raw.items():
        reservation_id = _validate_reservation_id(raw_id)
        if not isinstance(raw_item, dict) or set(raw_item) != {
            "day",
            "dollars",
            "in_tokens",
            "out_tokens",
            "overage_dollars",
            "policy_violation",
            "principal",
            "reserved_dollars",
            "settled_at",
        }:
            raise TenantAuthorityError("shared tenant settlement is malformed")
        in_tokens = raw_item.get("in_tokens")
        out_tokens = raw_item.get("out_tokens")
        if (
            not isinstance(in_tokens, int)
            or isinstance(in_tokens, bool)
            or in_tokens < 0
            or not isinstance(out_tokens, int)
            or isinstance(out_tokens, bool)
            or out_tokens < 0
        ):
            raise TenantAuthorityError("shared tenant settlement tokens are invalid")
        settlements[reservation_id] = {
            "day": _valid_day(raw_item.get("day"), label="settlement day"),
            "dollars": _finite_nonnegative(
                raw_item.get("dollars"), label="settlement dollars"
            ),
            "in_tokens": in_tokens,
            "out_tokens": out_tokens,
            "overage_dollars": _finite_nonnegative(
                raw_item.get("overage_dollars"), label="settlement overage"
            ),
            "policy_violation": raw_item.get("policy_violation"),
            "principal": _validate_principal(raw_item.get("principal")),
            "reserved_dollars": _finite_nonnegative(
                raw_item.get("reserved_dollars"), label="settlement reservation"
            ),
            "settled_at": _positive_time(
                raw_item.get("settled_at"), label="settlement timestamp"
            ),
        }
        violation = settlements[reservation_id]["policy_violation"]
        overage = float(settlements[reservation_id]["overage_dollars"])
        if violation not in (None, "reservation_overage") or (
            (overage > 0) != (violation == "reservation_overage")
        ):
            raise TenantAuthorityError(
                "shared tenant settlement policy evidence is invalid"
            )
    return settlements


def _validate_tenant_record(raw: object, *, tenant_id: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {
        "created_at",
        "deleted_at",
        "display_name",
        "id",
        "max_daily_dollars",
        "plan",
        "status",
        "updated_at",
    }:
        raise TenantAuthorityError("shared tenant lifecycle record is malformed")
    if raw.get("id") != tenant_id:
        raise TenantAuthorityError("shared tenant lifecycle identity is inconsistent")
    status = raw.get("status")
    plan = raw.get("plan")
    display_name = raw.get("display_name")
    if status not in (ACTIVE, SUSPENDED, DELETED):
        raise TenantAuthorityError("shared tenant lifecycle status is invalid")
    if (
        not isinstance(plan, str)
        or not plan
        or plan != plan.strip()
        or len(plan) > 256
        or not isinstance(display_name, str)
        or len(display_name) > 4096
    ):
        raise TenantAuthorityError("shared tenant lifecycle metadata is invalid")
    created_at = _positive_time(raw.get("created_at"), label="tenant creation timestamp")
    updated_at = _positive_time(raw.get("updated_at"), label="tenant update timestamp")
    if updated_at < created_at:
        raise TenantAuthorityError("tenant update timestamp predates creation")
    deleted_raw = raw.get("deleted_at")
    deleted_at = None
    if status == DELETED:
        deleted_at = _positive_time(deleted_raw, label="tenant deletion timestamp")
        if deleted_at < created_at:
            raise TenantAuthorityError("tenant deletion timestamp predates creation")
    elif deleted_raw is not None:
        raise TenantAuthorityError("live tenant unexpectedly carries a deletion tombstone")
    return {
        "id": tenant_id,
        "status": status,
        "plan": plan,
        "display_name": display_name,
        "max_daily_dollars": _finite_nonnegative(
            raw.get("max_daily_dollars"), label="tenant daily cap"
        ),
        "created_at": created_at,
        "updated_at": updated_at,
        "deleted_at": deleted_at,
    }


def validate_control_document(
    document: object,
    *,
    tenant_id: str,
    initialize: bool,
) -> tuple[
    dict[str, Any] | None,
    dict[str, dict[str, dict[str, int | float]]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    if not isinstance(document, dict):
        raise TenantAuthorityError("shared tenant control document must be an object")
    if not document:
        if not initialize:
            return None, {}, {}, {}
        document.update({
            "schema_version": SCHEMA_VERSION,
            "tenant": None,
            "usage": {},
            "reservations": {},
            "settlements": {},
        })
    if set(document) != {
        "reservations", "schema_version", "settlements", "tenant", "usage"
    } or document.get("schema_version") != SCHEMA_VERSION:
        raise TenantAuthorityError("shared tenant control document has an invalid schema")
    tenant_raw = document.get("tenant")
    tenant = (
        None
        if tenant_raw is None
        else _validate_tenant_record(tenant_raw, tenant_id=tenant_id)
    )
    usage = _validate_usage(document.get("usage"))
    reservations = _validate_reservations(document.get("reservations"))
    settlements = _validate_settlements(document.get("settlements"))
    normalized = {
        "schema_version": SCHEMA_VERSION,
        "tenant": tenant_raw,
        "usage": usage,
        "reservations": reservations,
        "settlements": settlements,
    }
    _bounded_json(normalized)
    return tenant, usage, reservations, settlements


def _read_control(tenant_id: str) -> tuple[
    dict[str, Any] | None,
    dict[str, dict[str, dict[str, int | float]]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    float,
]:
    refuse_legacy_usage_state(tenant_id)
    store = _new_tenant_store(tenant_id)
    with _tenant_scope(tenant_id):
        _require_postgres(store)
        document = store.read()
        now = store.authoritative_time()
        _require_postgres(store)
    tenant, usage, reservations, settlements = validate_control_document(
        document,
        tenant_id=tenant_id,
        initialize=False,
    )
    return tenant, usage, reservations, settlements, now


def _mutate_control(
    tenant_id: str,
    callback,
    *,
    action: str,
    fence: GovernedMutationFence | None = None,
    prune_retention: bool = True,
):
    refuse_legacy_usage_state(tenant_id)
    store = _new_tenant_store(tenant_id)
    with _tenant_scope(tenant_id):
        _require_postgres(store)

        def _apply(document: dict[str, Any], now: float):
            _tenant, _usage, reservations, settlements = validate_control_document(
                document,
                tenant_id=tenant_id,
                initialize=True,
            )
            # Apply bounded retention under the same authoritative-time CAS as
            # every write. This keeps dead run ids from bricking the document
            # while preserving a long late-settlement/idempotency window.
            if prune_retention:
                document["reservations"] = _prune_expired_reservations(
                    reservations,
                    now=now,
                )
                document["settlements"] = _prune_settlements(
                    settlements,
                    now=now,
                )
            result = callback(document, now)
            validate_control_document(
                document,
                tenant_id=tenant_id,
                initialize=False,
            )
            return result

        try:
            result = store.mutate_with_authoritative_time(
                _apply,
                action=action,
                actor="tenant-control-plane",
                fence=fence,
            )
        except TenantAuthorityError:
            raise
        except (KeyError, PermissionError, ValueError):
            raise
        except Exception as exc:
            raise TenantAuthorityError("shared tenant authority mutation failed") from exc
        _require_postgres(store)
        return result


def _claims() -> list[dict[str, Any]]:
    refuse_legacy_registry_state()
    store = _claim_store()
    _require_postgres(store)
    try:
        claims = store.list()
    except TenantAuthorityError:
        raise
    except Exception as exc:
        raise TenantAuthorityError("shared tenant namespace roster is unavailable") from exc
    _require_postgres(store)
    owners: dict[str, str] = {}
    for claim in claims:
        checked = _validate_claim(claim)
        prior = owners.get(checked["namespace_key"])
        if prior is not None and prior != checked["tenant_id"]:
            raise TenantAuthorityError("shared tenant namespace roster contains an alias")
        owners[checked["namespace_key"]] = checked["tenant_id"]
    return claims


def _claim_for(tenant_id: str) -> dict[str, Any] | None:
    refuse_legacy_registry_state()
    store = _claim_store()
    _require_postgres(store)
    try:
        claim = store.get(tenant_id)
    except TenantAuthorityError:
        raise
    except Exception as exc:
        raise TenantAuthorityError("shared tenant namespace roster is unavailable") from exc
    _require_postgres(store)
    return claim


def roster_exists() -> bool:
    return bool(_claims())


def list_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for claim in _claims():
        # Provisioning/aborted claims reserve their namespace but are not an
        # admitted tenant. One crashed saga therefore cannot wedge unrelated
        # roster listing or create an implicit RLS principal.
        if claim["state"] != "active":
            continue
        tenant, _usage, _reservations, _settlements, _now = _read_control(
            claim["tenant_id"]
        )
        if tenant is None:
            raise TenantAuthorityError(
                "tenant namespace is claimed but lifecycle initialization is incomplete"
            )
        if tenant["status"] != DELETED:
            records.append(tenant)
    return sorted(records, key=lambda item: item["id"])


def get_record(tenant_id: str) -> dict[str, Any] | None:
    tenant_id = canonical_tenant_id(tenant_id)
    claim = _claim_for(tenant_id)
    if (
        claim is None
        or claim["tenant_id"] != tenant_id
        or claim["state"] != "active"
    ):
        return None
    tenant, _usage, _reservations, _settlements, _now = _read_control(tenant_id)
    if tenant is None:
        raise TenantAuthorityError(
            "tenant namespace is claimed but lifecycle initialization is incomplete"
        )
    return None if tenant["status"] == DELETED else tenant


def create_record(
    tenant_id: str,
    *,
    plan: str,
    display_name: str,
    max_daily_dollars: float,
) -> dict[str, Any]:
    tenant_id = canonical_tenant_id(tenant_id)
    plan = str(plan or "free")
    display_name = str(display_name or "")
    cap = _finite_nonnegative(max_daily_dollars, label="tenant daily cap")
    if (
        not plan
        or plan != plan.strip()
        or len(plan) > 256
        or len(display_name) > 4096
    ):
        raise ValueError("tenant provisioning metadata is invalid")
    proposed = {
        "plan": plan,
        "display_name": display_name,
        "max_daily_dollars": cap,
    }
    refuse_legacy_registry_state()
    refuse_legacy_usage_state(tenant_id)
    claims = _claim_store()
    _require_postgres(claims)
    try:
        claim, created = claims.claim(
            tenant_id,
            proposed=proposed,
        )
    except TenantAuthorityError:
        raise
    except Exception as exc:
        raise TenantAuthorityError("tenant namespace could not be claimed") from exc
    _require_postgres(claims)
    if claim["tenant_id"] != tenant_id:
        raise ValueError(
            f"tenant id {tenant_id!r} aliases existing tenant {claim['tenant_id']!r}"
        )
    if claim["state"] in {"aborted", "deleted"}:
        raise ValueError(
            f"tenant {tenant_id!r} was deleted and its namespace is tombstoned"
        )
    if claim["state"] == "active":
        raise ValueError(f"tenant already exists: {tenant_id!r}")
    if claim["proposed"] != proposed:
        raise ValueError(
            "tenant provisioning is incomplete with different persisted metadata"
        )
    return resume_provisioning(tenant_id, _claim=claim, _created=created)


def resume_provisioning(
    tenant_id: str,
    *,
    _claim: dict[str, Any] | None = None,
    _created: bool = False,
) -> dict[str, Any]:
    """Deterministically finish a persisted claim -> control-document saga."""
    tenant_id = canonical_tenant_id(tenant_id)
    claim = _claim if _claim is not None else _claim_for(tenant_id)
    if claim is None or claim["tenant_id"] != tenant_id:
        raise TenantAuthorityError("tenant has no provisioning claim to resume")
    if claim["state"] == "active":
        current = get_record(tenant_id)
        if current is None:
            raise TenantAuthorityError("active tenant lifecycle record is unavailable")
        return current
    if claim["state"] != "provisioning":
        raise TenantAuthorityError("tenant provisioning claim is terminal")
    proposed = claim["proposed"]
    generation = _claim_revision(claim)
    claims = _claim_store()
    _require_postgres(claims)
    fence = claims.mutation_fence(
        tenant_id,
        state="provisioning",
        revision=generation,
    )

    def _initialize(document: dict[str, Any], now: float) -> dict[str, Any]:
        current, _usage, _reservations, _settlements = validate_control_document(
            document,
            tenant_id=tenant_id,
            initialize=True,
        )
        if current is not None:
            if current["status"] == DELETED or any(
                current[field] != proposed[field]
                for field in ("plan", "display_name", "max_daily_dollars")
            ):
                raise TenantAuthorityError(
                    "incomplete provisioning does not match its control document"
                )
            return copy.deepcopy(current)
        record = {
            "id": tenant_id,
            "status": ACTIVE,
            "plan": proposed["plan"],
            "display_name": proposed["display_name"],
            "max_daily_dollars": proposed["max_daily_dollars"],
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
        document["tenant"] = record
        return copy.deepcopy(record)

    record = _mutate_control(
        tenant_id,
        _initialize,
        action="tenant.create" if _created else "tenant.create_recovery",
        fence=fence,
    )
    try:
        claims.activate(tenant_id, expected_revision=generation)
    except Exception as exc:
        raise TenantAuthorityError(
            "tenant control initialized but namespace activation is incomplete"
        ) from exc
    _require_postgres(claims)
    return record


def abort_provisioning(tenant_id: str) -> bool:
    """Choose and finish the abort outcome for an incomplete provisioning saga."""
    tenant_id = canonical_tenant_id(tenant_id)
    claim = _claim_for(tenant_id)
    if claim is None or claim["tenant_id"] != tenant_id:
        return False
    if claim["state"] not in {"provisioning", "aborting", "aborted"}:
        raise TenantAuthorityError("only incomplete provisioning can be aborted")

    tenant, usage, reservations, settlements, _now = _read_control(tenant_id)
    if usage or reservations or settlements:
        raise TenantAuthorityError(
            "tenant provisioning has activity and cannot be aborted automatically"
        )
    proposed = claim["proposed"]
    if tenant is not None and (
        tenant["status"] != ACTIVE
        or any(
            tenant[field] != proposed[field]
            for field in ("plan", "display_name", "max_daily_dollars")
        )
    ):
        raise TenantAuthorityError(
            "tenant provisioning control does not match its namespace claim"
        )
    if claim["state"] == "aborted" and tenant is None:
        return False

    claims = _claim_store()
    _require_postgres(claims)
    try:
        started = False
        if claim["state"] != "aborted":
            claim, started = claims.begin_abort(tenant_id)
        state = str(claim["state"])
        if state not in {"aborting", "aborted"}:
            raise TenantAuthorityError("tenant provisioning abort outcome was lost")
        generation = _claim_revision(claim)

        def _clear_control(document: dict[str, Any], _now: float) -> bool:
            current, current_usage, current_reservations, current_settlements = (
                validate_control_document(
                    document,
                    tenant_id=tenant_id,
                    initialize=True,
                )
            )
            if current_usage or current_reservations or current_settlements:
                raise TenantAuthorityError(
                    "tenant provisioning has activity and cannot be aborted automatically"
                )
            if current is not None and (
                current["status"] != ACTIVE
                or any(
                    current[field] != proposed[field]
                    for field in ("plan", "display_name", "max_daily_dollars")
                )
            ):
                raise TenantAuthorityError(
                    "tenant provisioning control does not match its namespace claim"
                )
            document["tenant"] = None
            return current is not None

        fence = claims.mutation_fence(
            tenant_id,
            state=state,
            revision=generation,
        )
        try:
            cleared = bool(
                _mutate_control(
                    tenant_id,
                    _clear_control,
                    action="tenant.abort_cleanup",
                    fence=fence,
                    prune_retention=False,
                )
            )
        except TenantAuthorityError:
            # Another abort worker may have cleared control and committed the
            # terminal claim while this worker waited on the generation fence.
            current_claim = claims.get(tenant_id)
            if current_claim is None or current_claim["state"] != "aborted":
                raise
            current, current_usage, current_reservations, current_settlements, _ = (
                _read_control(tenant_id)
            )
            if current is not None or any(
                (current_usage, current_reservations, current_settlements)
            ):
                raise
            return bool(started)

        finished = False
        if state == "aborting":
            finished = bool(
                claims.finish_abort(
                    tenant_id,
                    expected_revision=generation,
                )
            )
        _require_postgres(claims)
        return bool(started or cleared or finished)
    except Exception as exc:
        if isinstance(exc, TenantAuthorityError):
            raise
        raise TenantAuthorityError("tenant provisioning abort failed") from exc


def mutate_record(tenant_id: str, **changes: object) -> dict[str, Any]:
    tenant_id = canonical_tenant_id(tenant_id)
    claim = _claim_for(tenant_id)
    if claim is None or claim["tenant_id"] != tenant_id or claim["state"] != "active":
        raise KeyError(tenant_id)
    allowed = {"display_name", "max_daily_dollars", "plan", "status"}
    if set(changes).difference(allowed):
        raise ValueError("unsupported shared tenant lifecycle mutation")

    def _mutate(document: dict[str, Any], now: float) -> dict[str, Any]:
        current, _usage, _reservations, _settlements = validate_control_document(
            document,
            tenant_id=tenant_id,
            initialize=False,
        )
        if current is None or current["status"] == DELETED:
            raise KeyError(tenant_id)
        updated = dict(current)
        if "status" in changes:
            status = changes["status"]
            if status not in (ACTIVE, SUSPENDED):
                raise ValueError("tenant status must be active or suspended")
            updated["status"] = status
        if "max_daily_dollars" in changes:
            updated["max_daily_dollars"] = _finite_nonnegative(
                changes["max_daily_dollars"], label="tenant daily cap"
            )
        if "plan" in changes:
            updated["plan"] = str(changes["plan"] or "free")
        if "display_name" in changes:
            updated["display_name"] = str(changes["display_name"] or "")
        updated["updated_at"] = now
        document["tenant"] = updated
        return copy.deepcopy(updated)

    return _mutate_control(tenant_id, _mutate, action="tenant.update")


def delete_record(tenant_id: str) -> bool:
    tenant_id = canonical_tenant_id(tenant_id)
    claim = _claim_for(tenant_id)
    if claim is None or claim["tenant_id"] != tenant_id:
        return False
    if claim["state"] == "deleted":
        return False
    if claim["state"] != "active":
        raise TenantAuthorityError("tenant provisioning is not active")

    def _delete(document: dict[str, Any], now: float) -> bool:
        current, _usage, _reservations, _settlements = validate_control_document(
            document,
            tenant_id=tenant_id,
            initialize=False,
        )
        if current is None:
            raise TenantAuthorityError("claimed tenant has no lifecycle record")
        if current["status"] == DELETED:
            return False
        tombstone = dict(current)
        tombstone.update({
            "status": DELETED,
            "updated_at": now,
            "deleted_at": now,
        })
        document["tenant"] = tombstone
        # Reservations are retained as evidence but immediately cease to grant
        # admission because every allocator checks the lifecycle in this row.
        return True

    changed = bool(_mutate_control(tenant_id, _delete, action="tenant.delete"))
    claims = _claim_store()
    _require_postgres(claims)
    try:
        claims.tombstone(tenant_id)
    except TenantAuthorityError:
        raise
    except Exception as exc:
        raise TenantAuthorityError("tenant namespace tombstone could not be recorded") from exc
    _require_postgres(claims)
    return changed


def _require_live_tenant(tenant: dict[str, Any] | None) -> dict[str, Any]:
    if tenant is None or tenant["status"] != ACTIVE:
        raise PermissionError("tenant is suspended, deleted, or unknown")
    return tenant


def usage_ledger(tenant_id: str) -> dict[str, dict[str, dict[str, int | float]]]:
    tenant_id = canonical_tenant_id(tenant_id)
    claim = _claim_for(tenant_id)
    if claim is None or claim["tenant_id"] != tenant_id:
        raise TenantAuthorityError("tenant usage authority has no namespace claim")
    tenant, usage, _reservations, _settlements, _now = _read_control(tenant_id)
    if tenant is None:
        raise TenantAuthorityError("tenant usage authority is uninitialized")
    return copy.deepcopy(usage)


def _spent(usage: dict[str, dict[str, dict[str, int | float]]], day: str) -> float:
    return sum(float((days.get(day) or {}).get("dollars", 0.0)) for days in usage.values())


def spend_today(tenant_id: str) -> float:
    tenant_id = canonical_tenant_id(tenant_id)
    tenant, usage, _reservations, _settlements, now = _read_control(tenant_id)
    if tenant is None:
        raise TenantAuthorityError("tenant usage authority is uninitialized")
    return _spent(usage, _day(now))


def authoritative_day(tenant_id: str) -> str:
    """Current UTC bucket from the selected tenant authority clock."""
    tenant_id = canonical_tenant_id(tenant_id)
    tenant, _usage, _reservations, _settlements, now = _read_control(tenant_id)
    if tenant is None:
        raise TenantAuthorityError("tenant usage authority is uninitialized")
    return _day(now)


def _live_reservations(
    reservations: dict[str, dict[str, Any]],
    *,
    now: float,
    day: str,
) -> dict[str, dict[str, Any]]:
    return {
        reservation_id: item
        for reservation_id, item in reservations.items()
        if item["expires_at"] > now and item["day"] == day
    }


def outstanding(tenant_id: str) -> float:
    tenant_id = canonical_tenant_id(tenant_id)
    tenant, _usage, reservations, _settlements, now = _read_control(tenant_id)
    if tenant is None:
        raise TenantAuthorityError("tenant reservation authority is uninitialized")
    return sum(
        item["dollars"]
        for item in _live_reservations(reservations, now=now, day=_day(now)).values()
    )


def remaining(tenant_id: str, *, effective_cap: float) -> float | None:
    tenant_id = canonical_tenant_id(tenant_id)
    cap = _finite_nonnegative(effective_cap, label="tenant daily cap")
    tenant, usage, reservations, _settlements, now = _read_control(tenant_id)
    current = _require_live_tenant(tenant)
    # The explicit cap lives in this same authority document. The caller value
    # is only the still-local plan-entitlement overlay when no explicit cap is
    # set; it must never override a concurrently changed explicit cap.
    cap = float(current["max_daily_dollars"]) or cap
    if cap <= 0:
        return None
    today = _day(now)
    held = sum(
        item["dollars"]
        for item in _live_reservations(reservations, now=now, day=today).values()
    )
    return max(0.0, cap - _spent(usage, today) - held)


def reserve_budget(
    tenant_id: str,
    *,
    requested_amount: float,
    ttl_seconds: float,
    reservation_id: str,
    effective_cap: float,
) -> float | None:
    tenant_id = canonical_tenant_id(tenant_id)
    reservation_id = _validate_reservation_id(reservation_id)
    requested = _finite_nonnegative(requested_amount, label="reservation amount")
    ttl = _finite_nonnegative(ttl_seconds, label="reservation TTL")
    cap = _finite_nonnegative(effective_cap, label="tenant daily cap")
    if ttl <= 0 or ttl > _MAX_RESERVATION_TTL_SECONDS:
        raise ValueError("reservation TTL must be positive and no more than seven days")
    def _reserve(document: dict[str, Any], now: float) -> float | None:
        tenant, usage, reservations, settlements = validate_control_document(
            document,
            tenant_id=tenant_id,
            initialize=False,
        )
        current = _require_live_tenant(tenant)
        attempt_cap = float(current["max_daily_dollars"]) or cap
        if attempt_cap <= 0:
            return None
        if reservation_id in reservations or reservation_id in settlements:
            raise TenantAuthorityError(
                f"tenant reservation already exists: {reservation_id!r}"
            )
        today = _day(now)
        live = _live_reservations(reservations, now=now, day=today)
        held = sum(item["dollars"] for item in live.values())
        granted = min(
            requested,
            max(0.0, attempt_cap - _spent(usage, today) - held),
        )
        if granted > 0:
            reservations[reservation_id] = {
                "created_at": now,
                "day": today,
                "dollars": granted,
                "expires_at": now + ttl,
            }
            document["reservations"] = reservations
        return granted

    return _mutate_control(tenant_id, _reserve, action="tenant_budget.reserve")


def reserve_dollars(
    tenant_id: str,
    *,
    amount: float,
    ttl_seconds: float,
    reservation_id: str,
) -> None:
    tenant_id = canonical_tenant_id(tenant_id)
    reservation_id = _validate_reservation_id(reservation_id)
    dollars = _finite_nonnegative(amount, label="reservation amount")
    ttl = _finite_nonnegative(ttl_seconds, label="reservation TTL")
    if dollars <= 0:
        return
    if ttl <= 0 or ttl > _MAX_RESERVATION_TTL_SECONDS:
        raise ValueError("reservation TTL must be positive and no more than seven days")

    def _reserve(document: dict[str, Any], now: float) -> None:
        tenant, _usage, reservations, settlements = validate_control_document(
            document,
            tenant_id=tenant_id,
            initialize=False,
        )
        _require_live_tenant(tenant)
        if reservation_id in reservations or reservation_id in settlements:
            raise TenantAuthorityError(
                f"tenant reservation already exists: {reservation_id!r}"
            )
        reservations[reservation_id] = {
            "created_at": now,
            "day": _day(now),
            "dollars": dollars,
            "expires_at": now + ttl,
        }
        document["reservations"] = reservations

    _mutate_control(tenant_id, _reserve, action="tenant_reservation.create")


def release(tenant_id: str, reservation_id: str) -> bool:
    tenant_id = canonical_tenant_id(tenant_id)
    reservation_id = _validate_reservation_id(reservation_id)

    def _release(document: dict[str, Any], _now: float) -> bool:
        tenant, _usage, reservations, _settlements = validate_control_document(
            document,
            tenant_id=tenant_id,
            initialize=False,
        )
        if tenant is None:
            raise TenantAuthorityError("tenant reservation authority is uninitialized")
        existed = reservation_id in reservations
        reservations.pop(reservation_id, None)
        document["reservations"] = reservations
        return existed

    return bool(_mutate_control(tenant_id, _release, action="tenant_reservation.release"))


def _prune_settlements(
    settlements: dict[str, dict[str, Any]],
    *,
    now: float,
) -> dict[str, dict[str, Any]]:
    cutoff = now - SETTLEMENT_RETENTION_DAYS * 24 * 3600.0
    return {
        reservation_id: receipt
        for reservation_id, receipt in settlements.items()
        if float(receipt["settled_at"]) > cutoff
    }


def _prune_expired_reservations(
    reservations: dict[str, dict[str, Any]],
    *,
    now: float,
) -> dict[str, dict[str, Any]]:
    cutoff = now - EXPIRED_RESERVATION_RETENTION_DAYS * 24 * 3600.0
    return {
        reservation_id: reservation
        for reservation_id, reservation in reservations.items()
        if float(reservation["expires_at"]) > cutoff
    }


def record_usage(
    tenant_id: str,
    principal: str,
    dollars: float,
    in_tokens: int,
    out_tokens: int,
    *,
    reservation_id: str | None,
) -> bool:
    """Record usage and settle its hold atomically.

    Returns ``True`` for the first settlement/record and ``False`` for an exact
    retry of a retained settlement receipt.  A retry with different measurement
    data fails closed instead of either double charging or silently accepting a
    conflicting account.
    """
    tenant_id = canonical_tenant_id(tenant_id)
    principal = _validate_principal(principal)
    amount = _finite_nonnegative(dollars, label="usage dollars")
    if (
        not isinstance(in_tokens, int)
        or isinstance(in_tokens, bool)
        or in_tokens < 0
        or not isinstance(out_tokens, int)
        or isinstance(out_tokens, bool)
        or out_tokens < 0
    ):
        raise ValueError("usage token counts must be non-negative integers")
    rid = None if reservation_id is None else _validate_reservation_id(reservation_id)

    def _record(document: dict[str, Any], now: float) -> bool:
        tenant, usage, reservations, settlements = validate_control_document(
            document,
            tenant_id=tenant_id,
            initialize=False,
        )
        if tenant is None:
            raise TenantAuthorityError("tenant usage authority is uninitialized")
        settlements = _prune_settlements(settlements, now=now)
        document["settlements"] = settlements
        if rid is not None:
            existing = settlements.get(rid)
            if existing is not None:
                expected = {
                    "day": existing["day"],
                    "dollars": amount,
                    "in_tokens": in_tokens,
                    "out_tokens": out_tokens,
                    "overage_dollars": existing["overage_dollars"],
                    "policy_violation": existing["policy_violation"],
                    "principal": principal,
                    "reserved_dollars": existing["reserved_dollars"],
                    "settled_at": existing["settled_at"],
                }
                if existing != expected:
                    raise TenantAuthorityError(
                        "tenant settlement retry conflicts with its retained receipt"
                    )
                return False
            reservation = reservations.get(rid)
            if reservation is None:
                raise TenantAuthorityError(
                    "tenant settlement has no matching reservation"
                )
            usage_day = str(reservation["day"])
        else:
            if tenant["status"] != ACTIVE:
                raise PermissionError("tenant is suspended, deleted, or unknown")
            if tenant["max_daily_dollars"] > 0 and amount > 0:
                raise TenantAuthorityError(
                    "capped shared tenant usage requires a matching reservation"
                )
            usage_day = _day(now)

        days = usage.setdefault(principal, {})
        cell = days.setdefault(
            usage_day,
            {"dollars": 0.0, "in_tokens": 0, "out_tokens": 0},
        )
        cell["dollars"] = float(cell["dollars"]) + amount
        cell["in_tokens"] = int(cell["in_tokens"]) + in_tokens
        cell["out_tokens"] = int(cell["out_tokens"]) + out_tokens
        document["usage"] = usage
        if rid is not None:
            reservation = reservations.pop(rid)
            document["reservations"] = reservations
            if len(settlements) >= _MAX_SETTLEMENTS:
                raise TenantAuthorityError(
                    "tenant settlement receipt window is at capacity"
                )
            reserved = float(reservation["dollars"])
            overage = max(0.0, amount - reserved)
            settlements[rid] = {
                "day": usage_day,
                "dollars": amount,
                "in_tokens": in_tokens,
                "out_tokens": out_tokens,
                "overage_dollars": overage,
                "policy_violation": (
                    "reservation_overage" if overage > 0 else None
                ),
                "principal": principal,
                "reserved_dollars": reserved,
                "settled_at": now,
            }
            document["settlements"] = settlements
        return True

    return bool(_mutate_control(tenant_id, _record, action="tenant_usage.settle"))


def _usage_prune_report(
    usage: dict[str, dict[str, dict[str, int | float]]],
    *,
    cutoff_day: str,
    mutate: bool,
) -> dict[str, Any]:
    removed_buckets = 0
    removed_principals = 0
    for principal in list(usage):
        days = usage[principal]
        expired = [day for day in days if day <= cutoff_day]
        removed_buckets += len(expired)
        if mutate:
            for day in expired:
                days.pop(day, None)
        if len(days) - (0 if mutate else len(expired)) <= 0:
            removed_principals += 1
            if mutate:
                usage.pop(principal, None)
    return {
        "removed_buckets": removed_buckets,
        "removed_principals": removed_principals,
        "cutoff_day": cutoff_day,
    }


def prune_usage(
    tenant_id: str,
    keep_days: int,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply explicit aggregate-usage retention under authority time and CAS."""
    tenant_id = canonical_tenant_id(tenant_id)
    if not isinstance(keep_days, int) or isinstance(keep_days, bool):
        raise ValueError("usage retention days must be an integer")
    if keep_days <= 0:
        return {
            "removed_buckets": 0,
            "removed_principals": 0,
            "reason": "disabled",
        }
    if dry_run:
        tenant, usage, _reservations, _settlements, now = _read_control(tenant_id)
        if tenant is None:
            raise TenantAuthorityError("tenant usage authority is uninitialized")
        cutoff_day = _day(now - max(1, keep_days) * 86400.0)
        return _usage_prune_report(
            usage,
            cutoff_day=cutoff_day,
            mutate=False,
        )

    def _prune(document: dict[str, Any], now: float) -> dict[str, Any]:
        tenant, usage, _reservations, _settlements = validate_control_document(
            document,
            tenant_id=tenant_id,
            initialize=False,
        )
        if tenant is None:
            raise TenantAuthorityError("tenant usage authority is uninitialized")
        cutoff_day = _day(now - max(1, keep_days) * 86400.0)
        report = _usage_prune_report(
            usage,
            cutoff_day=cutoff_day,
            mutate=True,
        )
        document["usage"] = usage
        return report

    return _mutate_control(tenant_id, _prune, action="tenant_usage.prune")


def reset_for_testing() -> None:
    global _CLAIMS
    with _CLAIMS_LOCK:
        _CLAIMS = None


__all__ = [
    "ACTIVE",
    "abort_provisioning",
    "DELETED",
    "SUSPENDED",
    "SETTLEMENT_RETENTION_DAYS",
    "TenantAuthorityError",
    "TenantNamespaceClaimStore",
    "create_record",
    "delete_record",
    "get_record",
    "list_records",
    "mutate_record",
    "outstanding",
    "authoritative_day",
    "prune_usage",
    "record_usage",
    "refuse_legacy_registry_state",
    "refuse_legacy_usage_state",
    "release",
    "remaining",
    "reserve_budget",
    "reserve_dollars",
    "resume_provisioning",
    "reset_for_testing",
    "roster_exists",
    "spend_today",
    "usage_ledger",
    "validate_control_document",
]
