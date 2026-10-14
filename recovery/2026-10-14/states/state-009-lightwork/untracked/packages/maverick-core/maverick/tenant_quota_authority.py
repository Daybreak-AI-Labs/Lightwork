"""Shared tenant quota and queue-backpressure authority.

Per-run :class:`maverick.budget.Budget` objects and the legacy JSON usage
ledger are useful local guardrails, but neither is a fleet authority: replicas
can each admit against the same apparent remainder.  This module presents one
typed resource vector over the configured world-model transaction domain.

Production enables it whenever the admitted Postgres world is selected.  An
authority/configuration outage fails closed; it never falls back to a host
file.  Development can opt in with ``MAVERICK_TENANT_QUOTA_ENFORCE=1`` and use
the tenant-scoped SQLite world for compatibility.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RESOURCE_NAMES = (
    "concurrent_runs",
    "queued_jobs",
    "daily_tokens",
    "daily_spend_dollars",
    "tool_calls",
    "storage_bytes",
)
INTEGER_RESOURCES = frozenset(set(RESOURCE_NAMES) - {"daily_spend_dollars"})

# These requested inventory dimensions are not represented by an admitted
# production path today.  Reporting them explicitly is safer than publishing a
# zero that could be mistaken for enforcement.
UNAVAILABLE_RESOURCE_INVENTORY = (
    "connector_calls",
    "vector_store_bytes",
)

_ENV_LIMITS = {
    "concurrent_runs": "MAVERICK_TENANT_QUOTA_MAX_CONCURRENT_RUNS",
    "queued_jobs": "MAVERICK_TENANT_QUOTA_MAX_QUEUED_JOBS",
    "daily_tokens": "MAVERICK_TENANT_QUOTA_MAX_DAILY_TOKENS",
    "daily_spend_dollars": "MAVERICK_TENANT_QUOTA_MAX_DAILY_SPEND_DOLLARS",
    "tool_calls": "MAVERICK_TENANT_QUOTA_MAX_TOOL_CALLS",
    "storage_bytes": "MAVERICK_TENANT_QUOTA_MAX_STORAGE_BYTES",
}
_MAX_POLICY_BYTES = 256 * 1024
_MAX_POLICY_DEPTH = 32
_MAX_POLICY_NODES = 20_000
_MAX_POLICY_TENANTS = 4096
_MAX_BIGINT = (1 << 63) - 1
_MAX_EXACT_FLOAT_INTEGER = 1 << 53


class TenantQuotaError(RuntimeError):
    """Base class for an admission refusal."""


class TenantQuotaAuthorityUnavailable(TenantQuotaError):
    """The shared authority, tenant identity, or exact policy is unavailable."""


class TenantQuotaPolicyError(TenantQuotaError):
    """The configured policy is malformed or incomplete for requested work."""


class TenantQuotaExceeded(TenantQuotaError):
    """One tenant resource would exceed its hard ceiling."""

    def __init__(self, tenant_id: str, resource: str, current: float, requested: float,
                 limit: float) -> None:
        super().__init__(
            f"tenant {tenant_id!r} quota exceeded for {resource}: "
            f"{current:g} admitted + {requested:g} requested > {limit:g}"
        )
        self.tenant_id = tenant_id
        self.resource = resource
        self.current = current
        self.requested = requested
        self.limit = limit


@dataclass(frozen=True)
class TenantQuotaPolicy:
    tenant_id: str
    generation: int
    limits: dict[str, int | float | None]
    policy_sha256: str

    @property
    def available_resources(self) -> tuple[str, ...]:
        return tuple(name for name in RESOURCE_NAMES if self.limits[name] is not None)

    @property
    def unavailable_resources(self) -> tuple[str, ...]:
        return tuple(name for name in RESOURCE_NAMES if self.limits[name] is None)


@dataclass(frozen=True)
class TenantQuotaReservation:
    tenant_id: str
    principal_id: str
    request_id: str
    resources: dict[str, int | float]
    period_key: str
    expires_at: float
    policy_sha256: str
    state: str = "ACTIVE"

    @property
    def active(self) -> bool:
        return self.state == "ACTIVE"


def _bounded_identity(value: object, field: str, *, maximum: int = 512) -> str:
    # Identities cross durable idempotency and tenant-authority boundaries.
    # Coercing bytes/bools/custom objects makes canonical equality depend on an
    # attacker-controlled __str__ implementation, so require the wire type.
    if not isinstance(value, str):
        raise TenantQuotaAuthorityUnavailable(
            f"tenant quota {field} must be a string"
        )
    text = value
    if (
        not text
        or text != text.strip()
        or len(text.encode("utf-8")) > maximum
        or not text.isprintable()
    ):
        raise TenantQuotaAuthorityUnavailable(
            f"tenant quota {field} must be non-empty trimmed printable text"
        )
    return text


def _number(value: object, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TenantQuotaPolicyError(f"tenant quota {name} must be numeric")
    if isinstance(value, int):
        if value < 0:
            raise TenantQuotaPolicyError(
                f"tenant quota {name} must be finite and non-negative"
            )
        if name in INTEGER_RESOURCES:
            if value > _MAX_BIGINT:
                raise TenantQuotaPolicyError(
                    f"tenant quota {name} exceeds the durable integer range"
                )
            return value
        try:
            numeric = float(value)
        except OverflowError as exc:
            raise TenantQuotaPolicyError(
                f"tenant quota {name} must be finite and non-negative"
            ) from exc
    else:
        numeric = value
    if not math.isfinite(numeric) or numeric < 0:
        raise TenantQuotaPolicyError(
            f"tenant quota {name} must be finite and non-negative"
        )
    if name in INTEGER_RESOURCES:
        if not numeric.is_integer():
            raise TenantQuotaPolicyError(f"tenant quota {name} must be an integer")
        if numeric > _MAX_EXACT_FLOAT_INTEGER:
            raise TenantQuotaPolicyError(
                f"tenant quota {name} must use an exact integer literal above 2^53"
            )
        if numeric > _MAX_BIGINT:
            raise TenantQuotaPolicyError(
                f"tenant quota {name} exceeds the durable integer range"
            )
        return int(numeric)
    return numeric


def normalize_vector(value: object) -> dict[str, int | float]:
    """Return the exact six-dimensional non-negative resource vector."""
    if not isinstance(value, dict):
        raise TenantQuotaPolicyError("tenant quota resource vector must be an object")
    if any(not isinstance(name, str) for name in value):
        raise TenantQuotaPolicyError(
            "tenant quota resource vector keys must be strings"
        )
    unknown = set(value) - set(RESOURCE_NAMES)
    if unknown:
        raise TenantQuotaPolicyError(
            "tenant quota resource vector has unknown fields: "
            + ", ".join(sorted(str(item) for item in unknown))
        )
    return {name: _number(value.get(name, 0), name) for name in RESOURCE_NAMES}


def _truthy(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise TenantQuotaPolicyError(f"{name} must be a boolean")


def _postgres_configured() -> bool:
    from .world_model_backends import is_postgres_configured

    return bool(is_postgres_configured())


def _production_required() -> bool:
    from .production_scope import production_surface_required

    return bool(production_surface_required())


def authority_required() -> bool:
    """Whether admitted work must consult the authority.

    An admitted production surface always requires this boundary. A missing
    Postgres selection is consequently an admission failure, never a reason to
    skip quota enforcement. Once production is selected, an explicit false
    cannot turn the shared authority off.
    """
    if _production_required():
        return True
    explicit = _truthy("MAVERICK_TENANT_QUOTA_ENFORCE")
    if explicit is not None:
        return explicit
    if os.environ.get("MAVERICK_TENANT_QUOTA_LIMITS_JSON", "").strip():
        return True
    return any(os.environ.get(name, "").strip() for name in _ENV_LIMITS.values())


def _decode_policy_json(encoded: str) -> object:
    try:
        encoded_size = len(encoded.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise TenantQuotaPolicyError(
            "MAVERICK_TENANT_QUOTA_LIMITS_JSON is not valid UTF-8"
        ) from exc
    if encoded_size > _MAX_POLICY_BYTES:
        raise TenantQuotaPolicyError(
            "MAVERICK_TENANT_QUOTA_LIMITS_JSON exceeds 256 KiB"
        )

    def reject_duplicate_pairs(pairs):
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise TenantQuotaPolicyError(
                    f"tenant quota policy contains duplicate key {key!r}"
                )
            result[key] = item
        return result

    def reject_constant(value: str):
        raise TenantQuotaPolicyError(
            f"tenant quota policy contains non-finite number {value}"
        )

    try:
        return json.loads(
            encoded,
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=reject_constant,
        )
    except TenantQuotaPolicyError:
        raise
    except (TypeError, ValueError, RecursionError) as exc:
        raise TenantQuotaPolicyError(
            "MAVERICK_TENANT_QUOTA_LIMITS_JSON is not valid JSON"
        ) from exc


def _load_config_policy() -> object:
    try:
        from .config import config_source_errors, load_config

        cfg = load_config() or {}
        if config_source_errors():
            raise TenantQuotaPolicyError(
                "tenant quota policy unavailable: active config is invalid"
            )
        return cfg.get("tenant_quotas") or {}
    except TenantQuotaPolicyError:
        raise
    except Exception as exc:
        raise TenantQuotaPolicyError(
            "tenant quota policy could not be loaded"
        ) from exc


def _validate_policy_shape(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TenantQuotaPolicyError("[tenant_quotas] must be an object")
    nodes = 0

    def validate_shape(value: object, depth: int = 0) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_POLICY_NODES:
            raise TenantQuotaPolicyError("tenant quota policy is too complex")
        if depth > _MAX_POLICY_DEPTH:
            raise TenantQuotaPolicyError("tenant quota policy nesting is too deep")
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise TenantQuotaPolicyError(
                    "tenant quota policy object keys must be strings"
                )
            for item in value.values():
                validate_shape(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                validate_shape(item, depth + 1)

    validate_shape(raw)
    allowed = {"generation", "default", "tenants"}
    unknown = set(raw) - allowed
    if unknown:
        raise TenantQuotaPolicyError(
            "tenant quota policy has unknown fields: "
            + ", ".join(sorted(unknown))
        )
    return raw


def _raw_policy() -> dict[str, Any]:
    encoded = os.environ.get("MAVERICK_TENANT_QUOTA_LIMITS_JSON")
    raw = (
        _decode_policy_json(encoded)
        if encoded is not None and encoded.strip()
        else _load_config_policy()
    )
    return _validate_policy_shape(raw)


def _limits_table(value: object, field: str) -> dict[str, int | float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TenantQuotaPolicyError(f"tenant quota {field} must be an object")
    if any(not isinstance(name, str) for name in value):
        raise TenantQuotaPolicyError(
            f"tenant quota {field} keys must be strings"
        )
    unknown = set(value) - set(RESOURCE_NAMES)
    if unknown:
        raise TenantQuotaPolicyError(
            f"tenant quota {field} has unknown resources: "
            + ", ".join(sorted(str(item) for item in unknown))
        )
    return {str(name): _number(item, str(name)) for name, item in value.items()}


def _parsed_tenant_limits(raw: dict[str, Any]) -> dict[str, dict[str, int | float]]:
    tenants = raw.get("tenants")
    if tenants is None:
        tenants = {}
    if not isinstance(tenants, dict):
        raise TenantQuotaPolicyError("tenant quota tenants must be an object")
    if len(tenants) > _MAX_POLICY_TENANTS:
        raise TenantQuotaPolicyError("tenant quota policy has too many tenants")
    parsed: dict[str, dict[str, int | float]] = {}
    for configured_tenant, configured_limits in tenants.items():
        normalized_tenant = _bounded_identity(
            configured_tenant,
            "configured tenant_id",
            maximum=256,
        )
        parsed[normalized_tenant] = _limits_table(
            configured_limits,
            f"tenants.{normalized_tenant}",
        )
    return parsed


def _apply_scalar_overrides(
    limits: dict[str, int | float],
) -> dict[str, int | float]:
    # Scalar environment variables are deployment-level overrides and win over
    # the JSON/config document. A configured zero is a real deny-all ceiling,
    # distinct from a missing/unavailable dimension.
    for resource, env_name in _ENV_LIMITS.items():
        raw_value = os.environ.get(env_name)
        if raw_value is not None and raw_value.strip():
            try:
                parsed: object = (
                    float(raw_value)
                    if resource == "daily_spend_dollars"
                    else int(raw_value)
                )
            except (TypeError, ValueError, OverflowError) as exc:
                raise TenantQuotaPolicyError(f"{env_name} is invalid") from exc
            limits[resource] = _number(parsed, resource)
    return limits


def policy_for_tenant(tenant_id: str) -> TenantQuotaPolicy:
    tenant = _bounded_identity(tenant_id, "tenant_id", maximum=256)
    raw = _raw_policy()
    limits = _limits_table(raw.get("default"), "default")
    parsed_tenants = _parsed_tenant_limits(raw)
    if tenant in parsed_tenants:
        limits.update(parsed_tenants[tenant])
    _apply_scalar_overrides(limits)
    complete: dict[str, int | float | None] = {
        name: limits.get(name) for name in RESOURCE_NAMES
    }
    raw_generation: object = os.environ.get(
        "MAVERICK_TENANT_QUOTA_POLICY_GENERATION",
        raw.get("generation"),
    )
    if raw_generation is None or (
        isinstance(raw_generation, str) and not raw_generation.strip()
    ):
        if _production_required() or _postgres_configured():
            raise TenantQuotaPolicyError(
                "shared tenant quota policy requires a monotonic generation"
            )
        generation = 0
    else:
        try:
            if isinstance(raw_generation, bool):
                raise ValueError
            generation = int(raw_generation)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TenantQuotaPolicyError(
                "tenant quota policy generation must be an integer"
            ) from exc
        if str(generation) != str(raw_generation).strip() or not (
            0 <= generation <= _MAX_BIGINT
        ):
            raise TenantQuotaPolicyError(
                "tenant quota policy generation must be a non-negative integer"
            )
    material = json.dumps(
        {"schema": "lightwork.tenant-quota-policy.v2", "tenant": tenant,
         "generation": generation, "limits": complete},
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return TenantQuotaPolicy(
        tenant_id=tenant,
        generation=generation,
        limits=complete,
        policy_sha256=hashlib.sha256(material).hexdigest(),
    )


def validate_deployment_policy() -> dict[str, object]:
    """Validate the fleet-wide base policy before any tenant has been admitted.

    Readiness cannot wait for the first customer request to discover malformed
    JSON, an invalid tenant override, or a missing resource dimension. The base
    vector must therefore be complete on its own; tenant entries may only narrow
    or replace individual ceilings from that complete floor.
    """
    raw = _raw_policy()
    base_limits = _limits_table(raw.get("default"), "default")
    _parsed_tenant_limits(raw)
    _apply_scalar_overrides(base_limits)
    missing = [name for name in RESOURCE_NAMES if name not in base_limits]
    if missing:
        raise TenantQuotaPolicyError(
            "tenant quota deployment policy lacks base ceilings for: "
            + ", ".join(missing)
        )
    # Reuse the canonical generation parser and policy materialization. The
    # deployment result deliberately reports only the base vector; a configured
    # tenant cannot make an incomplete deployment floor appear healthy.
    parsed = policy_for_tenant("__lightwork_quota_deployment_preflight__")
    return {
        "generation": parsed.generation,
        "limits": {name: base_limits[name] for name in RESOURCE_NAMES},
    }


def _timestamp(now: float | None = None) -> float:
    try:
        stamp = time.time() if now is None else float(now)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TenantQuotaPolicyError("tenant quota clock must be finite") from exc
    if not math.isfinite(stamp):
        raise TenantQuotaPolicyError("tenant quota clock must be finite")
    return stamp


def period_key(now: float | None = None) -> str:
    stamp = _timestamp(now)
    try:
        return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError) as exc:
        raise TenantQuotaPolicyError("tenant quota clock is out of range") from exc


def _validate_scope(tenant_id: str, principal_id: str) -> tuple[str, str]:
    tenant = _bounded_identity(tenant_id, "tenant_id", maximum=256)
    principal = _bounded_identity(principal_id, "principal_id")
    try:
        from .paths import current_tenant_id

        active = current_tenant_id()
    except Exception as exc:
        raise TenantQuotaAuthorityUnavailable(
            "tenant quota active tenant identity is unavailable"
        ) from exc
    if active is not None and str(active) != tenant:
        raise TenantQuotaAuthorityUnavailable(
            "tenant quota tenant does not match the active tenant"
        )
    if (_production_required() or _postgres_configured()) and active is None:
        raise TenantQuotaAuthorityUnavailable(
            "shared tenant quota authority requires an active tenant"
        )
    return tenant, principal


def _open_authority(world: object | None):
    try:
        shared_postgres = _postgres_configured()
    except Exception as exc:
        raise TenantQuotaAuthorityUnavailable(
            "shared tenant quota backend selection is unavailable"
        ) from exc
    if _production_required() and not shared_postgres:
        raise TenantQuotaAuthorityUnavailable(
            "production tenant quota authority requires Postgres"
        )
    if world is not None:
        if _production_required() or shared_postgres:
            try:
                from .world_model_backends.postgres import PostgresWorldModel
            except Exception as exc:  # pragma: no cover - import ships with core
                raise TenantQuotaAuthorityUnavailable(
                    "Postgres tenant quota authority type is unavailable"
                ) from exc
            if not isinstance(world, PostgresWorldModel):
                raise TenantQuotaAuthorityUnavailable(
                    "shared tenant quota authority requires the Postgres world"
                )
        return world, False
    try:
        from .world_model import open_world

        return open_world(), True
    except Exception as exc:
        raise TenantQuotaAuthorityUnavailable(
            "shared tenant quota authority is unavailable"
        ) from exc


def _close_authority(world: object, owned: bool) -> None:
    if not owned:
        return
    try:
        from .world_model import close_world_if_owned

        close_world_if_owned(world)
    except Exception:
        pass


def reserve(
    tenant_id: str,
    principal_id: str,
    resources: object,
    request_id: str,
    ttl: float,
    *,
    now: float | None = None,
    world: object | None = None,
) -> TenantQuotaReservation | None:
    """Atomically reserve a tenant resource vector.

    Repeating the same ``(tenant_id, request_id, principal_id, vector)`` returns
    the original lifecycle row.  Reusing a request id with different immutable
    inputs fails closed.  Missing ceilings are reported as unavailable instead
    of being interpreted as unlimited when enforcement is required.
    """
    tenant, principal = _validate_scope(tenant_id, principal_id)
    request = _bounded_identity(request_id, "request_id", maximum=512)
    vector = normalize_vector(resources)
    try:
        duration = float(ttl)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TenantQuotaPolicyError("tenant quota ttl must be finite") from exc
    if not math.isfinite(duration) or duration <= 0 or duration > 7 * 86400:
        raise TenantQuotaPolicyError(
            "tenant quota ttl must be greater than zero and at most seven days"
        )
    policy = policy_for_tenant(tenant)
    required = authority_required()
    if not required and not policy.available_resources:
        return None
    shared_required = _production_required() or _postgres_configured()
    missing = [
        name
        for name, amount in vector.items()
        if policy.limits[name] is None
        and (shared_required or float(amount) > 0)
    ]
    if missing:
        raise TenantQuotaPolicyError(
            "tenant quota ceiling unavailable for requested resources: "
            + ", ".join(missing)
        )
    stamp = _timestamp(now)
    key = period_key(stamp)
    authority, owned = _open_authority(world)
    try:
        result = authority.reserve_tenant_quota(
            tenant,
            principal,
            request,
            vector,
            {name: cap for name, cap in policy.limits.items() if cap is not None},
            key,
            policy.generation,
            policy.policy_sha256,
            created_at=stamp,
            expires_at=stamp + duration,
        )
    except Exception as exc:
        if isinstance(exc, TenantQuotaError):
            raise
        raise TenantQuotaAuthorityUnavailable(
            "tenant quota reservation authority failed"
        ) from exc
    finally:
        _close_authority(authority, owned)
    if not bool(result.get("allowed")):
        raise TenantQuotaExceeded(
            tenant,
            str(result.get("denied_resource") or "unknown"),
            float(result.get("current") or 0),
            float(result.get("requested") or 0),
            float(result.get("limit") or 0),
        )
    return TenantQuotaReservation(
        tenant_id=tenant,
        principal_id=principal,
        request_id=request,
        resources=vector,
        period_key=str(result.get("period_key") or key),
        expires_at=float(result.get("expires_at") or (stamp + duration)),
        policy_sha256=str(result.get("policy_sha256") or policy.policy_sha256),
        state=str(result.get("state") or "ACTIVE"),
    )


def settle(
    reservation: TenantQuotaReservation,
    actual_resources: object,
    *,
    now: float | None = None,
    world: object | None = None,
) -> dict[str, Any]:
    actual = normalize_vector(actual_resources)
    projected = normalize_vector(reservation.resources)
    if actual["concurrent_runs"] or actual["queued_jobs"]:
        raise TenantQuotaPolicyError(
            "concurrent_runs and queued_jobs are occupancy holds and must settle to zero"
        )
    over_projection = [
        name
        for name in ("daily_tokens", "daily_spend_dollars", "tool_calls", "storage_bytes")
        if float(actual[name]) > float(projected[name])
    ]
    if over_projection:
        raise TenantQuotaPolicyError(
            "tenant quota actual usage exceeds reserved projection for: "
            + ", ".join(over_projection)
        )
    stamp = _timestamp(now)
    authority, owned = _open_authority(world)
    try:
        return authority.settle_tenant_quota(
            reservation.tenant_id,
            reservation.request_id,
            actual,
            period_key(stamp),
            completed_at=stamp,
        )
    except Exception as exc:
        raise TenantQuotaAuthorityUnavailable(
            "tenant quota settlement authority failed"
        ) from exc
    finally:
        _close_authority(authority, owned)


def release(
    reservation: TenantQuotaReservation,
    *,
    now: float | None = None,
    world: object | None = None,
) -> dict[str, Any]:
    stamp = _timestamp(now)
    authority, owned = _open_authority(world)
    try:
        return authority.release_tenant_quota(
            reservation.tenant_id,
            reservation.request_id,
            completed_at=stamp,
        )
    except Exception as exc:
        raise TenantQuotaAuthorityUnavailable(
            "tenant quota release authority failed"
        ) from exc
    finally:
        _close_authority(authority, owned)


def release_request(
    tenant_id: str,
    request_id: str,
    *,
    now: float | None = None,
    world: object | None = None,
) -> dict[str, Any]:
    """Release a persisted reservation when only its durable identity remains."""
    tenant = _bounded_identity(tenant_id, "tenant_id", maximum=256)
    request = _bounded_identity(request_id, "request_id", maximum=512)
    stamp = _timestamp(now)
    authority, owned = _open_authority(world)
    try:
        return authority.release_tenant_quota(
            tenant,
            request,
            completed_at=stamp,
        )
    except Exception as exc:
        raise TenantQuotaAuthorityUnavailable(
            "tenant quota release authority failed"
        ) from exc
    finally:
        _close_authority(authority, owned)


def reconcile(
    tenant_id: str,
    *,
    now: float | None = None,
    world: object | None = None,
) -> int:
    tenant = _bounded_identity(tenant_id, "tenant_id", maximum=256)
    stamp = _timestamp(now)
    authority, owned = _open_authority(world)
    try:
        return int(authority.reconcile_tenant_quota(tenant, now=stamp))
    except Exception as exc:
        raise TenantQuotaAuthorityUnavailable(
            "tenant quota expiry reconciliation failed"
        ) from exc
    finally:
        _close_authority(authority, owned)


def status(
    tenant_id: str,
    *,
    now: float | None = None,
    world: object | None = None,
) -> dict[str, Any]:
    tenant = _bounded_identity(tenant_id, "tenant_id", maximum=256)
    policy = policy_for_tenant(tenant)
    stamp = _timestamp(now)
    authority, owned = _open_authority(world)
    try:
        state = dict(
            authority.get_tenant_quota_status(
                tenant,
                period_key(stamp),
                policy.generation,
                policy.policy_sha256,
                {name: cap for name, cap in policy.limits.items() if cap is not None},
                now=stamp,
            )
        )
    except Exception as exc:
        if isinstance(exc, TenantQuotaError):
            raise
        raise TenantQuotaAuthorityUnavailable(
            "tenant quota status authority failed"
        ) from exc
    finally:
        _close_authority(authority, owned)
    state.update(
        {
            "tenant_id": tenant,
            "limits": dict(state.get("authority_limits") or policy.limits),
            "available_resources": list(policy.available_resources),
            "unavailable_resources": [
                *policy.unavailable_resources,
                *UNAVAILABLE_RESOURCE_INVENTORY,
            ],
            "policy_sha256": policy.policy_sha256,
            "policy_generation": policy.generation,
            "required": authority_required(),
        }
    )
    return state


def queue_request_id(
    db_path: Path | str,
    job_id: int,
    attempts: int = 0,
    *,
    reservation_nonce: str | None = None,
    now: float | None = None,
) -> str:
    """Return a path-private identity for one durable queue occupancy lease.

    New queue rows persist a random ``reservation_nonce`` and replay that exact
    identity while ACTIVE. The legacy epoch form remains only for compatibility
    with callers that have not yet stored their lease identity.
    """
    digest = hashlib.sha256(
        os.path.normcase(os.path.abspath(os.fspath(db_path))).casefold().encode("utf-8")
    ).hexdigest()[:24]
    if reservation_nonce is not None:
        nonce = _bounded_identity(
            reservation_nonce,
            "queue reservation nonce",
            maximum=128,
        )
        return f"queue:{digest}:{int(job_id)}:{nonce}"
    stamp = _timestamp(now)
    epoch = int(stamp // (7 * 86400.0))
    return f"queue:{digest}:{int(job_id)}:{int(attempts)}:{epoch}"


__all__ = [
    "RESOURCE_NAMES",
    "UNAVAILABLE_RESOURCE_INVENTORY",
    "TenantQuotaAuthorityUnavailable",
    "TenantQuotaError",
    "TenantQuotaExceeded",
    "TenantQuotaPolicy",
    "TenantQuotaPolicyError",
    "TenantQuotaReservation",
    "authority_required",
    "normalize_vector",
    "period_key",
    "policy_for_tenant",
    "queue_request_id",
    "reconcile",
    "release",
    "release_request",
    "reserve",
    "settle",
    "status",
    "validate_deployment_policy",
]
