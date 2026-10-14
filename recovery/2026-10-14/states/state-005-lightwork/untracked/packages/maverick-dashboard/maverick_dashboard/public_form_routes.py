"""Deployment-global authority for anonymous hosted-form routing.

An anonymous ``POST /form/<token>`` has no principal from which tenant-by-user
middleware can derive a namespace.  In shared deployments this registry is the
only authority that may turn that bearer token into a tenant-local event
trigger.  It deliberately stores the full SHA-256 token digest, never the raw
token, in one deployment-global encrypted governed document.

Provisioning is a small fail-closed saga::

    prepare (pending digest claim) -> save tenant trigger -> activate

Pending claims are not routable.  Active claims are unique deployment-wide.
Revocation leaves an immutable tombstone, preventing a deleted bearer link
from later being reassigned to another tenant.  Every mutation is CAS-backed;
the deterministic operation id makes retries after a lost commit response
idempotent.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
import secrets
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, TypeVar

_SCHEMA_VERSION = 2
_LEGACY_SCHEMA_VERSION = 1
_MAX_ROUTES = 20_000
_MAX_TRANSITIONS = 20_000
_MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
_MAX_LEASE_GENERATION = 2**63 - 1
_MAX_CLAIMS_PER_ROUTE = 256
_MAX_CLAIMS = 20_000
_ADMITTED_CLAIM_TTL_SECONDS = 15 * 60
_COMMITTED_CLAIM_TTL_SECONDS = 60 * 60
_MUTATION_LEASE_TTL_SECONDS = 2 * 60
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{24,256}\Z")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_TRIGGER_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")
_OPERATION_RE = re.compile(r"[0-9a-f]{64}\Z")
_CLAIM_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDEMPOTENCY_RE = re.compile(r"[!-~]{1,256}\Z")
_STATES = frozenset({"pending", "active", "suspended", "revoked"})
_CLAIM_STATES = frozenset({"admitted", "committed"})
_TRANSITION_STATES = frozenset({"active", "released"})
_T = TypeVar("_T")
_TRIGGER_MUTATION_PERMIT: ContextVar[tuple[str, str, str, int] | None] = ContextVar(
    "public_form_trigger_mutation_permit",
    default=None,
)


class PublicFormRouteError(RuntimeError):
    """The public-form routing contract could not be preserved."""


class PublicFormRouteUnavailable(PublicFormRouteError):
    """The deployment-global routing authority cannot be trusted or reached."""


class PublicFormRouteConflict(PublicFormRouteError):
    """A token is already claimed or a provisioning transition is stale."""


@dataclass(frozen=True)
class PublicFormRoute:
    """Non-secret identity of one route; the bearer token is never retained."""

    token_digest: str
    tenant: str
    trigger: str
    operation: str
    epoch: int
    state: str


@dataclass(frozen=True)
class SubmissionClaim:
    """Durable authorization point for one tenant-local form append."""

    claim_id: str
    token_digest: str
    tenant: str
    trigger: str
    operation: str
    route_epoch: int
    status: str
    seq: int


class _GlobalRouteDocument:
    """Deployment-global governed document with a strict Postgres selector."""

    def __init__(self) -> None:
        from maverick.authority_documents import AuthorityDocumentStore

        self._document = AuthorityDocumentStore(
            "public_form_tenant_routing",
            "PFR",
            "public_form_tenant_routing",
            required_backend="postgres",
            authority_scope="deployment_global",
        )

    def _require_postgres(self) -> None:
        if self._document.backend_kind != "postgres":
            raise PublicFormRouteUnavailable(
                "public-form routing requires governed Postgres"
            )

    def read(self) -> dict[str, Any]:
        try:
            self._require_postgres()
            result = self._document.read()
            self._require_postgres()
            return result
        except PublicFormRouteError:
            raise
        except Exception as exc:
            raise PublicFormRouteUnavailable(
                "public-form routing authority read failed"
            ) from exc

    def mutate(
        self,
        callback: Callable[[dict[str, Any], float], _T],
        *,
        action: str,
    ) -> _T:
        try:
            self._require_postgres()
            result = self._document.mutate_with_authoritative_time(
                callback,
                action=action,
                # Token, tenant, and trigger identities stay inside the
                # encrypted record; the governed audit envelope is generic.
                actor="public-form-routing",
            )
            self._require_postgres()
            return result
        except (PublicFormRouteConflict, ValueError):
            raise
        except PublicFormRouteError:
            raise
        except Exception as exc:
            raise PublicFormRouteUnavailable(
                "public-form routing authority update failed"
            ) from exc

    def mutation_fence(
        self,
        callback: Callable[[dict[str, Any], float], None],
    ):
        """Return a deployment-global predicate for a tenant target write."""
        try:
            self._require_postgres()
            return self._document.mutation_fence(callback)
        except PublicFormRouteError:
            raise
        except Exception as exc:
            raise PublicFormRouteUnavailable(
                "public-form routing mutation fence is unavailable"
            ) from exc


_SHARED_DOCUMENT: _GlobalRouteDocument | None = None


def _shared_document() -> _GlobalRouteDocument:
    global _SHARED_DOCUMENT
    if _SHARED_DOCUMENT is None:
        _SHARED_DOCUMENT = _GlobalRouteDocument()
    return _SHARED_DOCUMENT


def routing_required() -> bool:
    """Whether anonymous forms need deployment-global tenant resolution."""
    try:
        from maverick.paths import tenant_by_user_enabled

        return bool(tenant_by_user_enabled())
    except Exception as exc:
        raise PublicFormRouteUnavailable(
            "public-form tenant-routing policy is unavailable"
        ) from exc


def validate_token(token: object) -> str:
    """Validate a newly provisioned bearer token without retaining it.

    Shared anonymous links require at least 24 URL-safe characters.  Legacy
    single-tenant forms remain unchanged; an old shared form with no route is
    deliberately inaccessible until an operator re-saves it with a qualifying
    token, which is the explicit cutover step.
    """
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        raise ValueError(
            "shared hosted-form tokens must be 24-256 URL-safe characters"
        )
    return token


def _token_digest(token: object, *, provision: bool) -> str:
    if provision:
        value = validate_token(token)
    elif isinstance(token, str) and 0 < len(token.encode("utf-8")) <= 512:
        value = token
    else:
        raise ValueError("hosted-form token is invalid")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_binding(
    token_digest: str,
    tenant: object,
    trigger: object,
) -> tuple[str, str, str]:
    from maverick.paths import canonical_tenant_id

    if not _DIGEST_RE.fullmatch(str(token_digest or "")):
        raise ValueError("hosted-form token digest is invalid")
    canonical_tenant = canonical_tenant_id(str(tenant or ""))
    canonical_trigger = str(trigger or "")
    if not _TRIGGER_RE.fullmatch(canonical_trigger):
        raise ValueError("hosted-form trigger name is invalid")
    return token_digest, canonical_tenant, canonical_trigger


def _require_active_tenant(tenant: str) -> None:
    try:
        from maverick.tenant.registry import assert_tenant_active

        assert_tenant_active(tenant)
    except Exception as exc:
        raise PublicFormRouteUnavailable(
            "public-form route tenant is inactive or unavailable"
        ) from exc


def _operation(token_digest: str, tenant: str, trigger: str) -> str:
    encoded = json.dumps(
        ["public-form-route-v1", token_digest, tenant, trigger],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def trigger_mutation_operation(
    tenant: str,
    trigger: str,
    intent: object,
) -> str:
    """Return a secret-free retry identity for one exact management intent."""
    _digest, tenant, trigger = _canonical_binding("0" * 64, tenant, trigger)
    try:
        encoded = json.dumps(
            ["public-form-trigger-mutation-v1", tenant, trigger, intent],
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (OverflowError, RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise ValueError("event-trigger mutation intent is invalid") from exc
    if len(encoded) > 256 * 1024:
        raise ValueError("event-trigger mutation intent exceeds its bound")
    return hashlib.sha256(encoded).hexdigest()


def _transition_key(tenant: str, trigger: str) -> str:
    encoded = json.dumps(
        ["public-form-trigger-lease-v1", tenant, trigger],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _valid_timestamp(value: object) -> bool:
    return bool(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _validated_claims(value: object, *, route_epoch: int) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or len(value) > _MAX_CLAIMS_PER_ROUTE:
        raise PublicFormRouteUnavailable(
            "public-form submission claims are invalid or unbounded"
        )
    claims: dict[str, dict[str, Any]] = {}
    expected = {
        "route_epoch",
        "status",
        "seq",
        "created_at",
        "updated_at",
    }
    for raw_claim_id, raw_claim in value.items():
        if (
            not isinstance(raw_claim_id, str)
            or not _CLAIM_RE.fullmatch(raw_claim_id)
            or raw_claim_id in claims
            or not isinstance(raw_claim, dict)
            or set(raw_claim) != expected
        ):
            raise PublicFormRouteUnavailable(
                "public-form submission claim schema is invalid"
            )
        claim_epoch = raw_claim.get("route_epoch")
        status = raw_claim.get("status")
        seq = raw_claim.get("seq")
        created_at = raw_claim.get("created_at")
        updated_at = raw_claim.get("updated_at")
        if (
            not isinstance(claim_epoch, int)
            or isinstance(claim_epoch, bool)
            or claim_epoch < 1
            or claim_epoch > route_epoch
            or status not in _CLAIM_STATES
            or not isinstance(seq, int)
            or isinstance(seq, bool)
            or seq < 0
            or (status == "admitted" and seq != 0)
            or (status == "committed" and seq < 1)
            or not _valid_timestamp(created_at)
            or not _valid_timestamp(updated_at)
            or float(updated_at) < float(created_at)
        ):
            raise PublicFormRouteUnavailable(
                "public-form submission claim is invalid"
            )
        claims[raw_claim_id] = {
            "route_epoch": claim_epoch,
            "status": status,
            "seq": seq,
            "created_at": float(created_at),
            "updated_at": float(updated_at),
        }
    return claims


def _validated_route(token_digest: str, value: object) -> dict[str, Any]:
    expected = {
        "tenant",
        "trigger",
        "operation",
        "state",
        "epoch",
        "created_at",
        "updated_at",
        "claims",
    }
    if (
        not _DIGEST_RE.fullmatch(token_digest)
        or not isinstance(value, dict)
        or set(value) != expected
    ):
        raise PublicFormRouteUnavailable(
            "public-form routing row has an invalid schema"
        )
    try:
        digest, tenant, trigger = _canonical_binding(
            token_digest,
            value.get("tenant"),
            value.get("trigger"),
        )
    except ValueError as exc:
        raise PublicFormRouteUnavailable(
            "public-form routing row has an invalid identity"
        ) from exc
    operation = value.get("operation")
    state = value.get("state")
    epoch = value.get("epoch")
    created_at = value.get("created_at")
    updated_at = value.get("updated_at")
    if (
        not isinstance(operation, str)
        or not _OPERATION_RE.fullmatch(operation)
        or not hmac.compare_digest(operation, _operation(digest, tenant, trigger))
        or state not in _STATES
        or not isinstance(epoch, int)
        or isinstance(epoch, bool)
        or epoch < 1
        or not _valid_timestamp(created_at)
        or not _valid_timestamp(updated_at)
        or float(updated_at) < float(created_at)
    ):
        raise PublicFormRouteUnavailable("public-form routing row is invalid")
    claims = _validated_claims(value.get("claims"), route_epoch=epoch)
    return {
        "tenant": tenant,
        "trigger": trigger,
        "operation": operation,
        "state": state,
        "epoch": epoch,
        "created_at": float(created_at),
        "updated_at": float(updated_at),
        "claims": claims,
    }


def _validated_transition(key: str, value: object) -> dict[str, Any]:
    expected = {
        "tenant",
        "trigger",
        "operation",
        "generation",
        "status",
        "created_at",
        "expires_at",
    }
    if (
        not _DIGEST_RE.fullmatch(key)
        or not isinstance(value, dict)
        or set(value) != expected
    ):
        raise PublicFormRouteUnavailable(
            "public-form trigger-mutation lease schema is invalid"
        )
    try:
        _digest, tenant, trigger = _canonical_binding(
            "0" * 64,
            value.get("tenant"),
            value.get("trigger"),
        )
    except ValueError as exc:
        raise PublicFormRouteUnavailable(
            "public-form trigger-mutation lease identity is invalid"
        ) from exc
    operation = value.get("operation")
    generation = value.get("generation")
    status = value.get("status")
    created_at = value.get("created_at")
    expires_at = value.get("expires_at")
    if (
        not hmac.compare_digest(key, _transition_key(tenant, trigger))
        or not isinstance(operation, str)
        or not _OPERATION_RE.fullmatch(operation)
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 1
        or generation > _MAX_LEASE_GENERATION
        or status not in _TRANSITION_STATES
        or not _valid_timestamp(created_at)
        or not _valid_timestamp(expires_at)
        or float(expires_at) <= float(created_at)
    ):
        raise PublicFormRouteUnavailable(
            "public-form trigger-mutation lease is invalid"
        )
    return {
        "tenant": tenant,
        "trigger": trigger,
        "operation": operation,
        "generation": generation,
        "status": status,
        "created_at": float(created_at),
        "expires_at": float(expires_at),
    }


def _validated_registry(
    value: object,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    if value == {}:
        return {}, {}
    if not isinstance(value, dict):
        raise PublicFormRouteUnavailable(
            "public-form routing registry schema is invalid"
        )
    schema = value.get("registry_schema")
    expected = (
        {"registry_schema", "routes"}
        if schema == _LEGACY_SCHEMA_VERSION
        else {"registry_schema", "routes", "transitions"}
    )
    if (
        schema not in {_LEGACY_SCHEMA_VERSION, _SCHEMA_VERSION}
        or set(value) != expected
        or not isinstance(value.get("routes"), dict)
        or len(value["routes"]) > _MAX_ROUTES
        or (
            schema == _SCHEMA_VERSION
            and (
                not isinstance(value.get("transitions"), dict)
                or len(value["transitions"]) > _MAX_TRANSITIONS
            )
        )
    ):
        raise PublicFormRouteUnavailable(
            "public-form routing registry schema is invalid"
        )
    routes: dict[str, dict[str, Any]] = {}
    claim_count = 0
    for raw_digest, raw_route in value["routes"].items():
        if not isinstance(raw_digest, str) or raw_digest in routes:
            raise PublicFormRouteUnavailable(
                "public-form routing registry key is invalid"
            )
        routes[raw_digest] = _validated_route(raw_digest, raw_route)
        claim_count += len(routes[raw_digest]["claims"])
    if claim_count > _MAX_CLAIMS:
        raise PublicFormRouteUnavailable(
            "public-form submission claim registry exceeds its bound"
        )
    transitions: dict[str, dict[str, Any]] = {}
    for raw_key, raw_transition in value.get("transitions", {}).items():
        if not isinstance(raw_key, str) or raw_key in transitions:
            raise PublicFormRouteUnavailable(
                "public-form trigger-mutation lease key is invalid"
            )
        transitions[raw_key] = _validated_transition(raw_key, raw_transition)
    try:
        encoded = json.dumps(
            {
                "registry_schema": _SCHEMA_VERSION,
                "routes": routes,
                "transitions": transitions,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (OverflowError, TypeError, ValueError) as exc:
        raise PublicFormRouteUnavailable(
            "public-form routing registry is not bounded JSON"
        ) from exc
    if len(encoded) > _MAX_DOCUMENT_BYTES:
        raise PublicFormRouteUnavailable(
            "public-form routing registry exceeds its storage bound"
        )
    return routes, transitions


def _validated_document(value: object) -> dict[str, dict[str, Any]]:
    routes, _transitions = _validated_registry(value)
    return routes


def _publish(
    document: dict[str, Any],
    routes: dict[str, dict[str, Any]],
    transitions: dict[str, dict[str, Any]] | None = None,
) -> None:
    if transitions is None:
        _old_routes, transitions = _validated_registry(document)
    candidate = {
        "registry_schema": _SCHEMA_VERSION,
        "routes": routes,
        "transitions": transitions,
    }
    _validated_registry(candidate)
    document.clear()
    document.update(candidate)


def _public(route: dict[str, Any], token_digest: str) -> PublicFormRoute:
    return PublicFormRoute(
        token_digest=token_digest,
        tenant=route["tenant"],
        trigger=route["trigger"],
        operation=route["operation"],
        epoch=route["epoch"],
        state=route["state"],
    )


def _same_binding(route: dict[str, Any], tenant: str, trigger: str, op: str) -> bool:
    return bool(
        route["tenant"] == tenant
        and route["trigger"] == trigger
        and hmac.compare_digest(route["operation"], op)
    )


def _required_mutation_operation(
    tenant: str,
    trigger: str,
) -> tuple[str, int] | None:
    """Return the current operation claim or reject an unfenced mutation."""
    if not routing_required():
        return None
    _digest, tenant, trigger = _canonical_binding("0" * 64, tenant, trigger)
    permit = _TRIGGER_MUTATION_PERMIT.get()
    if permit is None or permit[:2] != (tenant, trigger):
        raise PublicFormRouteUnavailable(
            "event-trigger mutation requires the public-form route coordinator"
        )
    return permit[2], permit[3]


def _fence_transition(
    transitions: dict[str, dict[str, Any]],
    *,
    tenant: str,
    trigger: str,
    permit: tuple[str, int] | None,
    now: float,
) -> None:
    if permit is None:
        return
    lease = _assert_transition(
        transitions,
        tenant=tenant,
        trigger=trigger,
        permit=permit,
        now=now,
    )
    key = _transition_key(tenant, trigger)
    lease["expires_at"] = max(float(now), lease["created_at"]) + (
        _MUTATION_LEASE_TTL_SECONDS
    )
    transitions[key] = _validated_transition(key, lease)


def _assert_transition(
    transitions: dict[str, dict[str, Any]],
    *,
    tenant: str,
    trigger: str,
    permit: tuple[str, int] | None,
    now: float,
) -> dict[str, Any]:
    if permit is None:
        return {}
    operation, generation = permit
    key = _transition_key(tenant, trigger)
    lease = transitions.get(key)
    if (
        lease is None
        or lease["status"] != "active"
        or lease["expires_at"] <= now
        or lease["generation"] != generation
        or not hmac.compare_digest(lease["operation"], operation)
    ):
        raise PublicFormRouteUnavailable(
            "event-trigger mutation lease expired or was superseded"
        )
    return lease


def _fenced_registry(
    document: dict[str, Any],
    now: float,
    *,
    tenant: str,
    trigger: str,
    permit: tuple[str, int] | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    routes, transitions = _validated_registry(document)
    _fence_transition(
        transitions,
        tenant=tenant,
        trigger=trigger,
        permit=permit,
        now=now,
    )
    return routes, transitions


def prepare_route(token: str, tenant: str, trigger: str) -> PublicFormRoute:
    """Reserve a token digest for one exact tenant/trigger binding."""
    digest = _token_digest(token, provision=True)
    digest, tenant, trigger = _canonical_binding(digest, tenant, trigger)
    _require_active_tenant(tenant)
    operation = _operation(digest, tenant, trigger)
    mutation_operation = _required_mutation_operation(tenant, trigger)

    def _prepare(document: dict[str, Any], now: float) -> PublicFormRoute:
        routes, transitions = _fenced_registry(
            document,
            now,
            tenant=tenant,
            trigger=trigger,
            permit=mutation_operation,
        )
        prior = routes.get(digest)
        if prior is not None:
            if not _same_binding(prior, tenant, trigger, operation):
                raise PublicFormRouteConflict(
                    "hosted-form token is already assigned"
                )
            if prior["state"] == "revoked":
                raise PublicFormRouteConflict(
                    "revoked hosted-form tokens cannot be reactivated"
                )
            return _public(prior, digest)
        route = {
            "tenant": tenant,
            "trigger": trigger,
            "operation": operation,
            "state": "pending",
            "epoch": 1,
            "created_at": float(now),
            "updated_at": float(now),
            "claims": {},
        }
        routes[digest] = _validated_route(digest, route)
        _publish(document, routes, transitions)
        return _public(route, digest)

    return _shared_document().mutate(_prepare, action="public_form_route_prepare")


def activate_route(prepared: PublicFormRoute) -> PublicFormRoute:
    """Activate an exact pending claim after its tenant trigger is durable."""
    digest, tenant, trigger = _canonical_binding(
        prepared.token_digest,
        prepared.tenant,
        prepared.trigger,
    )
    operation = _operation(digest, tenant, trigger)
    if not hmac.compare_digest(prepared.operation, operation):
        raise PublicFormRouteConflict("hosted-form route operation is stale")
    _require_active_tenant(tenant)
    mutation_operation = _required_mutation_operation(tenant, trigger)

    def _activate(document: dict[str, Any], now: float) -> PublicFormRoute:
        routes, transitions = _fenced_registry(
            document,
            now,
            tenant=tenant,
            trigger=trigger,
            permit=mutation_operation,
        )
        prior = routes.get(digest)
        if prior is None or not _same_binding(prior, tenant, trigger, operation):
            raise PublicFormRouteConflict("hosted-form route claim is unavailable")
        if prior["state"] == "revoked":
            raise PublicFormRouteConflict("hosted-form route has been revoked")
        if prior["state"] == "active":
            return _public(prior, digest)
        if (
            prior["state"] not in {"pending", "suspended"}
            or prior["state"] != prepared.state
            or prior["epoch"] != prepared.epoch
        ):
            raise PublicFormRouteConflict(
                "hosted-form route was superseded before activation"
            )
        prior["state"] = "active"
        prior["epoch"] += 1
        prior["updated_at"] = float(now)
        routes[digest] = _validated_route(digest, prior)
        _publish(document, routes, transitions)
        return _public(prior, digest)

    return _shared_document().mutate(_activate, action="public_form_route_activate")


def abort_pending_route(prepared: PublicFormRoute) -> bool:
    """Remove only the exact still-pending claim; never undo an active route."""
    digest, tenant, trigger = _canonical_binding(
        prepared.token_digest,
        prepared.tenant,
        prepared.trigger,
    )
    operation = _operation(digest, tenant, trigger)
    if not hmac.compare_digest(prepared.operation, operation):
        raise PublicFormRouteConflict("hosted-form route operation is stale")
    mutation_operation = _required_mutation_operation(tenant, trigger)

    def _abort(document: dict[str, Any], now: float) -> bool:
        routes, transitions = _fenced_registry(
            document,
            now,
            tenant=tenant,
            trigger=trigger,
            permit=mutation_operation,
        )
        prior = routes.get(digest)
        if prior is None:
            return True
        if not _same_binding(prior, tenant, trigger, operation):
            raise PublicFormRouteConflict("hosted-form route claim changed")
        if prior["state"] != "pending":
            return False
        del routes[digest]
        _publish(document, routes, transitions)
        return True

    return _shared_document().mutate(_abort, action="public_form_route_abort")


def revoke_route(token: str, tenant: str, trigger: str) -> bool:
    """Revoke one exact binding and retain a non-reusable digest tombstone."""
    digest = _token_digest(token, provision=False)
    digest, tenant, trigger = _canonical_binding(digest, tenant, trigger)
    operation = _operation(digest, tenant, trigger)
    mutation_operation = _required_mutation_operation(tenant, trigger)

    def _revoke(document: dict[str, Any], now: float) -> bool:
        routes, transitions = _fenced_registry(
            document,
            now,
            tenant=tenant,
            trigger=trigger,
            permit=mutation_operation,
        )
        prior = routes.get(digest)
        if prior is None or not _same_binding(prior, tenant, trigger, operation):
            # Another tenant's same-digest claim is not ours to mutate.  The
            # caller's trigger is already unroutable through that record.
            return False
        if prior["state"] == "revoked":
            return True
        prior["state"] = "revoked"
        prior["epoch"] += 1
        prior["updated_at"] = float(now)
        routes[digest] = _validated_route(digest, prior)
        _publish(document, routes, transitions)
        return True

    return _shared_document().mutate(_revoke, action="public_form_route_revoke")


def suspend_other_routes(
    tenant: str,
    trigger: str,
    *,
    keep_digest: str | None,
) -> int:
    """Stop old bindings from admitting work before a trigger replacement.

    This is the restrictive first half of the cross-document update saga.  A
    process that cannot prove whether the tenant-trigger save committed leaves
    old routes suspended; retrying the management request converges.  A caller
    that proves the old trigger is unchanged may use :func:`restore_route`.
    """
    _placeholder, tenant, trigger = _canonical_binding("0" * 64, tenant, trigger)
    if keep_digest is not None and not _DIGEST_RE.fullmatch(keep_digest):
        raise ValueError("hosted-form keep digest is invalid")
    _require_active_tenant(tenant)
    mutation_operation = _required_mutation_operation(tenant, trigger)

    def _suspend(document: dict[str, Any], now: float) -> int:
        routes, transitions = _fenced_registry(
            document,
            now,
            tenant=tenant,
            trigger=trigger,
            permit=mutation_operation,
        )
        changed = 0
        for digest, route in routes.items():
            if (
                route["tenant"] != tenant
                or route["trigger"] != trigger
                or digest == keep_digest
                or route["state"] in {"pending", "revoked", "suspended"}
            ):
                continue
            route["state"] = "suspended"
            route["epoch"] += 1
            route["updated_at"] = float(now)
            routes[digest] = _validated_route(digest, route)
            changed += 1
        _publish(document, routes, transitions)
        return changed

    return _shared_document().mutate(
        _suspend,
        action="public_form_route_suspend",
    )


@contextmanager
def trigger_mutation_guard(
    tenant: str,
    trigger: str,
    *,
    operation: str,
):
    """Hold a deployment-global operation lease through one trigger mutation.

    The lease and restrictive route suspension commit in one backend-time CAS.
    Identical retries resume the same operation after a lost response; a
    competing intent must wait for release or backend-clock expiry. Every later
    route and tenant-store mutation rechecks this operation claim.
    """
    if not routing_required():
        yield 0
        return
    _placeholder, tenant, trigger = _canonical_binding("0" * 64, tenant, trigger)
    if not isinstance(operation, str) or not _OPERATION_RE.fullmatch(operation):
        raise ValueError("event-trigger mutation operation is invalid")
    _require_active_tenant(tenant)
    transition_key = _transition_key(tenant, trigger)

    def _acquire(document: dict[str, Any], now: float) -> tuple[int, int]:
        routes, transitions = _validated_registry(document)
        prior = transitions.get(transition_key)
        active = bool(
            prior is not None
            and prior["status"] == "active"
            and prior["expires_at"] > now
        )
        if active:
            if not hmac.compare_digest(prior["operation"], operation):
                raise PublicFormRouteUnavailable(
                    "another event-trigger mutation is already in progress"
                )
            generation = int(prior["generation"])
            prior["expires_at"] = max(float(now), prior["created_at"]) + (
                _MUTATION_LEASE_TTL_SECONDS
            )
        else:
            generation = int((prior or {}).get("generation") or 0) + 1
            if generation > _MAX_LEASE_GENERATION:
                raise PublicFormRouteUnavailable(
                    "event-trigger mutation lease generation is exhausted"
                )
            prior = {
                "tenant": tenant,
                "trigger": trigger,
                "operation": operation,
                "generation": generation,
                "status": "active",
                "created_at": float(now),
                "expires_at": float(now) + _MUTATION_LEASE_TTL_SECONDS,
            }
        transitions[transition_key] = _validated_transition(
            transition_key,
            prior,
        )
        changed = 0
        for digest, route in routes.items():
            if (
                route["tenant"] != tenant
                or route["trigger"] != trigger
                or route["state"] in {"pending", "revoked", "suspended"}
            ):
                continue
            route["state"] = "suspended"
            route["epoch"] += 1
            route["updated_at"] = float(now)
            routes[digest] = _validated_route(digest, route)
            changed += 1
        _publish(document, routes, transitions)
        return changed, generation

    suspended, generation = _shared_document().mutate(
        _acquire,
        action="public_form_route_suspend",
    )
    context = _TRIGGER_MUTATION_PERMIT.set(
        (tenant, trigger, operation, generation)
    )

    def _release() -> None:
        def _drop(document: dict[str, Any], _now: float) -> bool:
            routes, transitions = _validated_registry(document)
            current = transitions.get(transition_key)
            if (
                current is None
                or current["status"] != "active"
                or current["generation"] != generation
                or not hmac.compare_digest(current["operation"], operation)
            ):
                return False
            current["status"] = "released"
            transitions[transition_key] = _validated_transition(
                transition_key,
                current,
            )
            _publish(document, routes, transitions)
            return True

        _shared_document().mutate(
            _drop,
            action="public_form_route_lease_release",
        )

    try:
        yield suspended
    except BaseException:
        try:
            _release()
        except PublicFormRouteError:
            # Preserve the primary exception. A remaining lease expires only
            # according to the authoritative backend clock.
            pass
        raise
    else:
        _release()
    finally:
        _TRIGGER_MUTATION_PERMIT.reset(context)


def assert_trigger_mutation_permitted(tenant: str, trigger: str) -> None:
    """Fence and renew the exact operation before a tenant-store mutation."""
    operation = _required_mutation_operation(tenant, trigger)
    if operation is None:
        return
    _placeholder, tenant, trigger = _canonical_binding("0" * 64, tenant, trigger)

    def _renew(document: dict[str, Any], now: float) -> None:
        routes, transitions = _fenced_registry(
            document,
            now,
            tenant=tenant,
            trigger=trigger,
            permit=operation,
        )
        _publish(document, routes, transitions)

    _shared_document().mutate(
        _renew,
        action="public_form_route_lease_fence",
    )


def trigger_mutation_fence(tenant: str, trigger: str):
    """Return the exact global permit check for a tenant trigger write.

    The governed Postgres backend evaluates this predicate while holding the
    deployment-global transition row in the tenant target's own transaction.
    Consequently an expiry takeover either waits for the target commit or
    causes that stale target write to roll back.
    """
    permit = _required_mutation_operation(tenant, trigger)
    if permit is None:
        return None
    _placeholder, tenant, trigger = _canonical_binding("0" * 64, tenant, trigger)

    def _validate(document: dict[str, Any], now: float) -> None:
        _routes, transitions = _validated_registry(document)
        _assert_transition(
            transitions,
            tenant=tenant,
            trigger=trigger,
            permit=permit,
            now=now,
        )

    return _shared_document().mutation_fence(_validate)


def retire_other_routes(
    tenant: str,
    trigger: str,
    *,
    keep_digest: str | None,
) -> int:
    """Tombstone every old binding after the tenant-trigger save is proven."""
    _placeholder, tenant, trigger = _canonical_binding("0" * 64, tenant, trigger)
    if keep_digest is not None and not _DIGEST_RE.fullmatch(keep_digest):
        raise ValueError("hosted-form keep digest is invalid")
    _require_active_tenant(tenant)
    mutation_operation = _required_mutation_operation(tenant, trigger)

    def _retire(document: dict[str, Any], now: float) -> int:
        routes, transitions = _fenced_registry(
            document,
            now,
            tenant=tenant,
            trigger=trigger,
            permit=mutation_operation,
        )
        changed = 0
        for digest, route in routes.items():
            if (
                route["tenant"] != tenant
                or route["trigger"] != trigger
                or digest == keep_digest
                or route["state"] == "revoked"
            ):
                continue
            route["state"] = "revoked"
            route["epoch"] += 1
            route["updated_at"] = float(now)
            routes[digest] = _validated_route(digest, route)
            changed += 1
        _publish(document, routes, transitions)
        return changed

    return _shared_document().mutate(
        _retire,
        action="public_form_route_retire",
    )


def restore_route(token: str, tenant: str, trigger: str) -> PublicFormRoute:
    """Restore only an exact suspended route after its old trigger is proven.

    The caller must re-read the tenant authority and prove that the attempted
    replacement did not commit.  Revoked tombstones are intentionally not
    restorable.
    """
    digest = _token_digest(token, provision=True)
    digest, tenant, trigger = _canonical_binding(digest, tenant, trigger)
    operation = _operation(digest, tenant, trigger)
    _require_active_tenant(tenant)
    mutation_operation = _required_mutation_operation(tenant, trigger)

    def _restore(document: dict[str, Any], now: float) -> PublicFormRoute:
        routes, transitions = _fenced_registry(
            document,
            now,
            tenant=tenant,
            trigger=trigger,
            permit=mutation_operation,
        )
        route = routes.get(digest)
        if route is None or not _same_binding(route, tenant, trigger, operation):
            raise PublicFormRouteConflict("hosted-form route cannot be restored")
        if route["state"] == "active":
            return _public(route, digest)
        if route["state"] != "suspended":
            raise PublicFormRouteConflict("hosted-form route is not restorable")
        route["state"] = "active"
        route["epoch"] += 1
        route["updated_at"] = float(now)
        routes[digest] = _validated_route(digest, route)
        _publish(document, routes, transitions)
        return _public(route, digest)

    return _shared_document().mutate(
        _restore,
        action="public_form_route_restore",
    )


def _prune_expired_claims(
    routes: dict[str, dict[str, Any]],
    now: float,
) -> None:
    for digest, route in routes.items():
        retained: dict[str, dict[str, Any]] = {}
        for claim_id, claim in route["claims"].items():
            ttl = (
                _COMMITTED_CLAIM_TTL_SECONDS
                if claim["status"] == "committed"
                else _ADMITTED_CLAIM_TTL_SECONDS
            )
            if float(claim["updated_at"]) + ttl > now:
                retained[claim_id] = claim
        if len(retained) != len(route["claims"]):
            route["claims"] = retained
            routes[digest] = _validated_route(digest, route)


def _claim_id(route: PublicFormRoute, idempotency_key: str | None) -> str:
    if idempotency_key is None:
        return secrets.token_hex(32)
    if not isinstance(idempotency_key, str) or not _IDEMPOTENCY_RE.fullmatch(
        idempotency_key
    ):
        raise ValueError(
            "Idempotency-Key must be 1-256 printable ASCII characters"
        )
    material = json.dumps(
        ["public-form-submission-v1", route.operation, idempotency_key],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(material).hexdigest()


def _public_claim(
    route: PublicFormRoute,
    claim_id: str,
    claim: dict[str, Any],
) -> SubmissionClaim:
    return SubmissionClaim(
        claim_id=claim_id,
        token_digest=route.token_digest,
        tenant=route.tenant,
        trigger=route.trigger,
        operation=route.operation,
        route_epoch=claim["route_epoch"],
        status=claim["status"],
        seq=claim["seq"],
    )


def admit_submission(
    route: PublicFormRoute,
    *,
    idempotency_key: str | None = None,
) -> SubmissionClaim:
    """Atomically authorize one append against an exact active route epoch.

    Whichever operation commits first defines the race: revocation first makes
    this fail; admission first creates a durable authorization that revocation
    preserves until the append completes or the claim expires.
    """
    digest, tenant, trigger = _canonical_binding(
        route.token_digest,
        route.tenant,
        route.trigger,
    )
    operation = _operation(digest, tenant, trigger)
    if (
        route.state != "active"
        or not hmac.compare_digest(route.operation, operation)
        or route.epoch < 1
    ):
        raise PublicFormRouteConflict("hosted-form route admission is stale")
    claim_id = _claim_id(route, idempotency_key)
    _require_active_tenant(tenant)

    def _admit(document: dict[str, Any], now: float) -> SubmissionClaim:
        routes = _validated_document(document)
        _prune_expired_claims(routes, now)
        current = routes.get(digest)
        if (
            current is None
            or not _same_binding(current, tenant, trigger, operation)
            or current["state"] != "active"
            or current["epoch"] != route.epoch
        ):
            raise PublicFormRouteConflict("hosted-form route is no longer active")
        prior = current["claims"].get(claim_id)
        if prior is not None:
            if prior["route_epoch"] != route.epoch:
                raise PublicFormRouteConflict(
                    "hosted-form submission claim belongs to an older route"
                )
            return _public_claim(route, claim_id, prior)
        if len(current["claims"]) >= _MAX_CLAIMS_PER_ROUTE:
            raise PublicFormRouteUnavailable(
                "public-form submission claim capacity is exhausted"
            )
        claim = {
            "route_epoch": route.epoch,
            "status": "admitted",
            "seq": 0,
            "created_at": float(now),
            "updated_at": float(now),
        }
        current["claims"][claim_id] = claim
        routes[digest] = _validated_route(digest, current)
        _publish(document, routes)
        return _public_claim(route, claim_id, claim)

    return _shared_document().mutate(
        _admit,
        action="public_form_submission_admit",
    )


def _finish_submission_claim(
    claim: SubmissionClaim,
    *,
    seq: int | None,
) -> bool:
    digest, tenant, trigger = _canonical_binding(
        claim.token_digest,
        claim.tenant,
        claim.trigger,
    )
    operation = _operation(digest, tenant, trigger)
    if (
        not _CLAIM_RE.fullmatch(claim.claim_id)
        or not hmac.compare_digest(claim.operation, operation)
        or claim.route_epoch < 1
    ):
        raise PublicFormRouteConflict("hosted-form submission claim is stale")
    if seq is not None and (
        not isinstance(seq, int) or isinstance(seq, bool) or seq < 1
    ):
        raise ValueError("form submission sequence is invalid")

    def _finish(document: dict[str, Any], now: float) -> bool:
        routes = _validated_document(document)
        current = routes.get(digest)
        if current is None or not _same_binding(current, tenant, trigger, operation):
            raise PublicFormRouteConflict("hosted-form submission route changed")
        stored = current["claims"].get(claim.claim_id)
        if stored is None or stored["route_epoch"] != claim.route_epoch:
            raise PublicFormRouteConflict("hosted-form submission claim expired")
        if seq is None:
            if stored["status"] == "committed":
                return False
            del current["claims"][claim.claim_id]
        else:
            if stored["status"] == "committed":
                if stored["seq"] != seq:
                    raise PublicFormRouteConflict(
                        "hosted-form submission sequence changed"
                    )
                return True
            stored["status"] = "committed"
            stored["seq"] = seq
            stored["updated_at"] = float(now)
            current["claims"][claim.claim_id] = stored
        routes[digest] = _validated_route(digest, current)
        _publish(document, routes)
        return True

    action = (
        "public_form_submission_commit"
        if seq is not None
        else "public_form_submission_abort"
    )
    return _shared_document().mutate(_finish, action=action)


def complete_submission(claim: SubmissionClaim, seq: int) -> bool:
    """Record the tenant-local sequence for a durably appended claim."""
    return _finish_submission_claim(claim, seq=seq)


def abort_submission(claim: SubmissionClaim) -> bool:
    """Release an admitted claim after the tenant-local append fails."""
    return _finish_submission_claim(claim, seq=None)


def resolve_route(token: str) -> PublicFormRoute | None:
    """Resolve an active route, hiding absent/pending/revoked distinctions."""
    digest = _token_digest(token, provision=True)
    routes = _validated_document(_shared_document().read())
    route = routes.get(digest)
    if route is None or route["state"] != "active":
        return None
    return _public(copy.deepcopy(route), digest)


def route_matches_trigger(
    route: PublicFormRoute,
    trigger: object,
    token: str,
) -> bool:
    """Constant-time exact binding check for the tenant trigger re-read."""
    if not isinstance(trigger, dict) or route.state != "active":
        return False
    config = trigger.get("config")
    if not isinstance(config, dict):
        return False
    raw = config.get("token")
    try:
        digest = _token_digest(raw, provision=True)
        request_digest = _token_digest(token, provision=True)
    except ValueError:
        return False
    return bool(
        hmac.compare_digest(route.token_digest, request_digest)
        and hmac.compare_digest(route.token_digest, digest)
        and trigger.get("source") == "form"
        and str(trigger.get("tenant") or "") == route.tenant
        and str(trigger.get("name") or "") == route.trigger
    )


def trigger_token(trigger: object, *, provision: bool = False) -> str | None:
    """Return a form trigger's raw token only to its lifecycle coordinator."""
    if not isinstance(trigger, dict) or trigger.get("source") != "form":
        return None
    config = trigger.get("config")
    if not isinstance(config, dict) or not isinstance(config.get("token"), str):
        if provision:
            raise ValueError("form event trigger requires config.token")
        return None
    token = config["token"]
    if provision:
        validate_token(token)
    elif not token or len(token.encode("utf-8")) > 512:
        return None
    return token


def has_revoked_trigger(tenant: str, trigger: str) -> bool:
    """Whether a deletion tombstone exists for this tenant/trigger pair."""
    _digest, tenant, trigger = _canonical_binding("0" * 64, tenant, trigger)
    routes = _validated_document(_shared_document().read())
    return any(
        route["state"] == "revoked"
        and route["tenant"] == tenant
        and route["trigger"] == trigger
        for route in routes.values()
    )


def has_trigger_binding(tenant: str, trigger: str) -> bool:
    """Whether any managed route or tombstone names this tenant trigger."""
    _digest, tenant, trigger = _canonical_binding("0" * 64, tenant, trigger)
    routes = _validated_document(_shared_document().read())
    return any(
        route["tenant"] == tenant and route["trigger"] == trigger
        for route in routes.values()
    )


__all__ = [
    "PublicFormRoute",
    "PublicFormRouteConflict",
    "PublicFormRouteError",
    "PublicFormRouteUnavailable",
    "SubmissionClaim",
    "abort_pending_route",
    "abort_submission",
    "activate_route",
    "admit_submission",
    "assert_trigger_mutation_permitted",
    "complete_submission",
    "has_revoked_trigger",
    "has_trigger_binding",
    "prepare_route",
    "resolve_route",
    "restore_route",
    "retire_other_routes",
    "revoke_route",
    "route_matches_trigger",
    "routing_required",
    "suspend_other_routes",
    "trigger_token",
    "trigger_mutation_fence",
    "trigger_mutation_guard",
    "trigger_mutation_operation",
    "validate_token",
]
