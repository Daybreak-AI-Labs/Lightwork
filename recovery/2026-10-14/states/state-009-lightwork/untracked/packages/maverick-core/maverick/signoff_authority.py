"""Durable authority types for production deliverable sign-off decisions.

The SQLite and Postgres world-model backends own the transaction itself.  This
module keeps their validation, exact replay envelope, and typed failure
contract identical without importing either backend.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .api_idempotency import (
    DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    ApiIdempotencyConflict,
    ApiIdempotencyExpired,
    ApiIdempotencyIndeterminate,
    canonical_request,
)

SIGNOFF_RESULT_SCHEMA = "lightwork.idempotent-signoff-result.v2"
SIGNOFF_POLICY_SCHEMA = "lightwork.deliverable-signoff-policy.v1"
MAX_SIGNOFF_RESULT_BYTES = 16 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_VALID_GATES = frozenset({"", "approval", "review"})
_MAX_DOMAIN_CHARS = 128


class SignoffConflict(ApiIdempotencyConflict):
    """The key, deliverable generation, or prior decision conflicts."""


class SignoffExpired(ApiIdempotencyExpired):
    """The retained idempotency tombstone is past its replay window."""


class SignoffIndeterminate(ApiIdempotencyIndeterminate):
    """The durable decision or replay result cannot be proven complete."""


class SignoffPolicyUnavailable(SignoffIndeterminate):
    """The current domain release policy cannot be resolved exactly."""


@dataclass(frozen=True)
class SignoffPolicyBinding:
    """Exact release-policy snapshot governing one deliverable decision."""

    goal_domain: str
    governing_gate: str
    policy_sha256: str


@dataclass(frozen=True)
class SignoffAuditEvent:
    """One decision intent waiting for the signed audit chain."""

    event_id: str
    tenant_id: str
    goal_id: int
    deliverable_updated_at: float
    goal_domain: str
    governing_gate: str
    policy_sha256: str
    decision: str
    decided_by: str
    request_sha256: str
    note_sha256: str
    occurred_at: float
    delivered_at: float | None
    state: str

    @property
    def effective(self) -> bool:
        return self.state == "EFFECTIVE" and self.delivered_at is not None

    def matches(
        self,
        *,
        tenant_id: str,
        goal_id: int,
        deliverable_updated_at: float,
        goal_domain: str,
        governing_gate: str,
        policy_sha256: str,
        decision: str,
        decided_by: str,
        request_sha256: str,
        note_sha256: str,
    ) -> bool:
        return (
            self.tenant_id == tenant_id
            and self.goal_id == int(goal_id)
            and self.deliverable_updated_at == float(deliverable_updated_at)
            and self.goal_domain == goal_domain
            and self.governing_gate == governing_gate
            and hmac.compare_digest(self.policy_sha256, policy_sha256)
            and self.decision == decision
            and self.decided_by == decided_by
            and self.request_sha256 == request_sha256
            and self.note_sha256 == note_sha256
        )


@dataclass(frozen=True)
class IdempotentSignoffResult:
    """The exact public result plus its durable audit-delivery state."""

    event: SignoffAuditEvent
    response: dict[str, Any]
    replayed: bool
    expires_at: float


def validate_sha256(value: str, *, label: str) -> str:
    digest = str(value or "")
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError(f"{label} digest is invalid")
    return digest


def _exact_domain(value: str) -> str:
    domain = str(value or "")
    if (
        len(domain) > _MAX_DOMAIN_CHARS
        or not domain.isprintable() and domain != ""
        or domain != domain.strip()
    ):
        raise ValueError("sign-off goal domain is invalid")
    return domain


def _exact_gate(value: str | None) -> str:
    gate = "" if value is None else str(value)
    if gate not in _VALID_GATES:
        raise ValueError("sign-off governing gate is invalid")
    return gate


def signoff_policy_sha256(goal_domain: str, governing_gate: str | None) -> str:
    """Fingerprint only the policy facts that authorize deliverable release.

    The goal domain is carried separately as a queryable snapshot as well as in
    this digest.  The duplicate binding is intentional: neither a row rewrite
    nor a future digest-input change can silently transplant an approval to a
    different domain or gate.
    """
    domain = _exact_domain(goal_domain)
    gate = _exact_gate(governing_gate)
    policy = {
        "goal_domain": domain,
        "governing_gate": gate,
        "schema": SIGNOFF_POLICY_SCHEMA,
    }
    return hashlib.sha256(canonical_request(policy).encode("utf-8")).hexdigest()


def policy_binding(
    goal_domain: str,
    governing_gate: str | None,
) -> SignoffPolicyBinding:
    domain = _exact_domain(goal_domain)
    gate = _exact_gate(governing_gate)
    return SignoffPolicyBinding(
        goal_domain=domain,
        governing_gate=gate,
        policy_sha256=signoff_policy_sha256(domain, gate),
    )


def current_signoff_policy_binding(goal_domain: str) -> SignoffPolicyBinding:
    """Resolve the live policy for ``goal_domain`` or fail closed.

    Empty-domain goals are the legacy ungated local lane and still receive a
    deterministic non-authorizing snapshot.  A named domain whose pack is
    missing or unreadable is unavailable policy, never an implicit ungated
    profile.
    """
    domain = _exact_domain(goal_domain)
    if not domain:
        return policy_binding("", "")
    try:
        from .domain import available_domains, enforced_gate

        profile = available_domains().get(domain)
        if profile is None:
            raise SignoffPolicyUnavailable(
                "the goal's domain release policy is unavailable"
            )
        return policy_binding(domain, enforced_gate(profile))
    except SignoffPolicyUnavailable:
        raise
    except Exception as exc:
        raise SignoffPolicyUnavailable(
            "the goal's domain release policy is unavailable"
        ) from exc


def signoff_snapshot_matches(
    signoff: Mapping[str, Any] | None,
    *,
    goal_domain: str,
    governing_gate: str | None,
) -> bool:
    """Whether an approval is bound to the exact current release policy."""
    if not isinstance(signoff, Mapping):
        return False
    try:
        expected = policy_binding(goal_domain, governing_gate)
        stored_digest = validate_sha256(
            str(signoff.get("policy_sha256") or ""), label="policy"
        )
    except (TypeError, ValueError):
        return False
    return bool(
        signoff.get("decision") == "approved"
        and signoff.get("goal_domain") == expected.goal_domain
        and signoff.get("governing_gate") == expected.governing_gate
        and hmac.compare_digest(stored_digest, expected.policy_sha256)
    )


def signoff_event_id(
    *,
    tenant_id: str,
    operation: str,
    principal_id: str,
    idempotency_key: str,
    request_sha256: str,
    goal_domain: str,
    governing_gate: str,
    policy_sha256: str,
) -> str:
    """Derive the stable signed-audit identity for one idempotency claim."""
    parts = (
        str(tenant_id).encode("utf-8"),
        str(operation).encode("utf-8"),
        str(principal_id).encode("utf-8"),
        str(idempotency_key).encode("utf-8"),
        validate_sha256(request_sha256, label="request").encode("ascii"),
        _exact_domain(goal_domain).encode("utf-8"),
        _exact_gate(governing_gate).encode("ascii"),
        validate_sha256(policy_sha256, label="policy").encode("ascii"),
    )
    framed = b"".join(len(part).to_bytes(4, "big") + part for part in parts)
    return "deliverable-signoff-v2-" + hashlib.sha256(framed).hexdigest()


def encode_signoff_result(response: Mapping[str, Any]) -> str:
    """Encode a bounded, strict replay result for application sealing."""
    public = json.loads(canonical_request(response))
    envelope = {"response": public, "schema": SIGNOFF_RESULT_SCHEMA}
    encoded = json.dumps(
        envelope,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(encoded.encode("utf-8")) > MAX_SIGNOFF_RESULT_BYTES:
        raise ValueError("sign-off replay result exceeds 16 KiB")
    return encoded


def decode_signoff_result(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or ""))
    except (TypeError, ValueError, RecursionError) as exc:
        raise SignoffIndeterminate("sign-off replay result cannot be decoded") from exc
    if not isinstance(decoded, dict) or set(decoded) != {"response", "schema"}:
        raise SignoffIndeterminate("sign-off replay result schema is invalid")
    if decoded.get("schema") != SIGNOFF_RESULT_SCHEMA:
        raise SignoffIndeterminate("sign-off replay result schema is invalid")
    response = decoded.get("response")
    if not isinstance(response, dict):
        raise SignoffIndeterminate("sign-off replay result is not an object")
    return response


__all__ = [
    "DEFAULT_IDEMPOTENCY_TTL_SECONDS",
    "IdempotentSignoffResult",
    "MAX_SIGNOFF_RESULT_BYTES",
    "SIGNOFF_POLICY_SCHEMA",
    "SIGNOFF_RESULT_SCHEMA",
    "SignoffAuditEvent",
    "SignoffConflict",
    "SignoffExpired",
    "SignoffIndeterminate",
    "SignoffPolicyBinding",
    "SignoffPolicyUnavailable",
    "current_signoff_policy_binding",
    "decode_signoff_result",
    "encode_signoff_result",
    "policy_binding",
    "signoff_event_id",
    "signoff_policy_sha256",
    "signoff_snapshot_matches",
    "validate_sha256",
]
