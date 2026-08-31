"""Bounded identity-governance control-plane contracts and services.

The read-only provider inventory adapters live in
``ai_identity_governance_connectors``.  This module consumes only their
pseudonymous identifiers and scheduler digests.  It deliberately does not
accept provider tokens, email addresses, display names, or raw provider
identifiers.

All provider mutations are injected seams and are off by default.  A local
transport acknowledgement is not represented as tenant acceptance, policy
certification, or complete identity coverage.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from .privacy_ops import RecordConflict

Provider = Literal["entra", "okta", "purview"]
FederationProvider = Literal["entra", "okta"]
DeliverySinkName = Literal["dynamic_risk", "alert"]

PROVIDERS: tuple[Provider, ...] = ("entra", "okta", "purview")
FEDERATION_PROVIDERS: tuple[FederationProvider, ...] = ("entra", "okta")

NORMALIZED_CHANGE_SCHEMA = "lightwork.identity_governance.normalized-change.v1"
NORMALIZED_DELTA_SCHEMA = "lightwork.identity_governance.snapshot-delta.v1"
CHANGE_EVENT_SCHEMA = "lightwork.identity_governance.change-event.v1"
CHANGE_DELIVERY_ACK_SCHEMA = "lightwork.identity_governance.change-delivery-ack.v1"
CHANGE_STATE_SCHEMA = "lightwork.identity_governance.change-coupling-state.v1"
FEDERATION_OBSERVATION_SCHEMA = "lightwork.identity_governance.agent-principal-observation.v1"
CAPABILITY_MAPPING_SCHEMA = "lightwork.identity_governance.capability-mapping.v1"
CONDITIONAL_ACCESS_SCHEMA = "lightwork.identity_governance.conditional-access-observation.v1"
ENFORCEMENT_DECISION_SCHEMA = "lightwork.identity_governance.conditional-access-decision.v1"
PURVIEW_INTENT_SCHEMA = "lightwork.identity_governance.purview-writeback-intent.v1"
PURVIEW_REQUEST_SCHEMA = "lightwork.identity_governance.purview-writeback-request.v1"
PURVIEW_ACK_SCHEMA = "lightwork.identity_governance.purview-writeback-ack.v1"

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_PSEUDONYMOUS_RE = re.compile(r"(?:idn|sub|res|prm|grt|cls|ast|prn)_[0-9a-f]{32}\Z")
_CODE_RE = re.compile(r"[a-z][a-z0-9_.:-]{0,127}\Z")
_ERROR_TYPE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}\Z")
_RECORD_ID_RE = re.compile(r"(?:IGCC|IGPW)-[0-9a-f]{32}\Z")
_MAX_DELTA_DIGESTS = 512
_MAX_CHANGE_COUNT = 10_000
_MAX_CAPABILITIES = 128
_MAX_CLASSIFICATIONS = 128


class IdentityGovernanceControlError(RuntimeError):
    """Base error that never includes provider response or credential text."""


class IdentityGovernanceControlConfigError(IdentityGovernanceControlError):
    """A control is disabled, unavailable, or ambiguously configured."""


class IdentityGovernanceBindingError(IdentityGovernanceControlError):
    """Tenant, principal, provider, or source binding does not match."""


class IdentityGovernanceStaleBinding(IdentityGovernanceBindingError):
    """A revision or binding changed after the operation was prepared."""


class IdentityGovernanceAuthorizationError(IdentityGovernanceControlError):
    """The exact principal-to-capability mapping does not authorize a control."""


class IdentityGovernanceIdempotencyConflict(IdentityGovernanceControlError):
    """An idempotency identity is bound to another semantic operation."""


class IdentityGovernanceDeliveryError(IdentityGovernanceControlError):
    """An injected sink or provider transport failed closed."""


class IdentityGovernanceStateError(IdentityGovernanceControlError):
    """Durable control state is malformed or cannot converge safely."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ValueError("identity-governance value is not canonical JSON") from exc


def _sha256(value: object) -> str:
    material = value if isinstance(value, bytes) else _canonical(value)
    return hashlib.sha256(material).hexdigest()


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _positive_revision(value: object, label: str = "revision") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _timestamp(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite timestamp")
    selected = float(value)
    if not math.isfinite(selected) or selected <= 0:
        raise ValueError(f"{label} must be a finite timestamp")
    return selected


def _provider(value: object, *, federation_only: bool = False) -> str:
    allowed = FEDERATION_PROVIDERS if federation_only else PROVIDERS
    if not isinstance(value, str) or value not in allowed:
        raise ValueError("identity-governance provider is unsupported")
    return value


def _pseudonymous(value: object, label: str, *, prefixes: set[str]) -> str:
    if not isinstance(value, str) or not _PSEUDONYMOUS_RE.fullmatch(value):
        raise ValueError(f"{label} must be a pseudonymous identifier")
    if value.split("_", 1)[0] not in prefixes:
        raise ValueError(f"{label} has an unsupported pseudonymous namespace")
    return value


def _code(value: object, label: str) -> str:
    if not isinstance(value, str) or not _CODE_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def _codes(value: object, label: str, *, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise ValueError(f"{label} is invalid")
    selected = tuple(_code(item, label) for item in value)
    if selected != tuple(sorted(set(selected))):
        raise ValueError(f"{label} must be sorted and unique")
    return selected


def _digests(value: object, label: str, *, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise ValueError(f"{label} is invalid")
    selected = tuple(_digest(item, label) for item in value)
    if selected != tuple(sorted(set(selected))):
        raise ValueError(f"{label} must be sorted and unique")
    return selected


def _idempotency_sha256(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("idempotency_key is invalid")
    selected = value.strip()
    if len(selected) < 8 or len(selected) > 256 or any(ord(char) < 32 for char in selected):
        raise ValueError("idempotency_key is invalid")
    return hashlib.sha256(selected.encode("utf-8")).hexdigest()


def _safe_error_type(exc: BaseException) -> str:
    selected = type(exc).__name__
    return selected if _ERROR_TYPE_RE.fullmatch(selected) else "Exception"


def _record_revision(value: Mapping[str, Any]) -> int:
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise IdentityGovernanceStateError("identity-governance record revision is invalid")
    return revision


def _public(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if not str(key).startswith("_")}


@dataclass(frozen=True)
class AgentPrincipalAuthority:
    """Pseudonymous, revision-bound authority for one Lightwork agent."""

    tenant_sha256: str
    principal_ref: str
    authority_binding_sha256: str
    revision: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_sha256", _digest(self.tenant_sha256, "tenant_sha256"))
        object.__setattr__(
            self,
            "principal_ref",
            _pseudonymous(self.principal_ref, "principal_ref", prefixes={"prn"}),
        )
        object.__setattr__(
            self,
            "authority_binding_sha256",
            _digest(self.authority_binding_sha256, "authority_binding_sha256"),
        )
        object.__setattr__(self, "revision", _positive_revision(self.revision))

    @classmethod
    def from_principal(
        cls,
        *,
        tenant_sha256: str,
        principal: str,
        authority_binding_sha256: str,
        revision: int,
    ) -> AgentPrincipalAuthority:
        """Hash a transient ``agent:`` principal without retaining it."""

        tenant = _digest(tenant_sha256, "tenant_sha256")
        if (
            not isinstance(principal, str)
            or not principal.startswith("agent:")
            or len(principal) <= len("agent:")
            or len(principal) > 256
            or any(ord(char) < 33 for char in principal)
        ):
            raise ValueError("principal must be an authenticated agent principal")
        principal_ref = (
            "prn_"
            + _sha256(
                {
                    "schema": "lightwork.identity_governance.agent-principal-ref.v1",
                    "tenant_sha256": tenant,
                    "principal": principal,
                }
            )[:32]
        )
        return cls(
            tenant_sha256=tenant,
            principal_ref=principal_ref,
            authority_binding_sha256=authority_binding_sha256,
            revision=revision,
        )


@dataclass(frozen=True)
class AgentPrincipalFederationBinding:
    """Exact binding from a Lightwork agent to a provider-side subject."""

    tenant_sha256: str
    provider: FederationProvider | str
    principal_ref: str
    provider_subject_id: str
    authority_binding_sha256: str
    authority_revision: int
    binding_sha256: str

    def __post_init__(self) -> None:
        tenant = _digest(self.tenant_sha256, "tenant_sha256")
        provider = _provider(self.provider, federation_only=True)
        principal_ref = _pseudonymous(self.principal_ref, "principal_ref", prefixes={"prn"})
        subject_id = _pseudonymous(
            self.provider_subject_id,
            "provider_subject_id",
            prefixes={"sub", "idn"},
        )
        authority_binding = _digest(self.authority_binding_sha256, "authority_binding_sha256")
        authority_revision = _positive_revision(self.authority_revision, "authority_revision")
        expected = _sha256(
            {
                "schema": "lightwork.identity_governance.agent-federation-binding.v1",
                "tenant_sha256": tenant,
                "provider": provider,
                "principal_ref": principal_ref,
                "provider_subject_id": subject_id,
                "authority_binding_sha256": authority_binding,
                "authority_revision": authority_revision,
            }
        )
        if self.binding_sha256 != expected:
            raise ValueError("federation binding digest does not match")
        object.__setattr__(self, "tenant_sha256", tenant)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "principal_ref", principal_ref)
        object.__setattr__(self, "provider_subject_id", subject_id)
        object.__setattr__(self, "authority_binding_sha256", authority_binding)
        object.__setattr__(self, "authority_revision", authority_revision)

    @classmethod
    def bind(
        cls,
        authority: AgentPrincipalAuthority,
        *,
        provider: FederationProvider,
        provider_subject_id: str,
    ) -> AgentPrincipalFederationBinding:
        if not isinstance(authority, AgentPrincipalAuthority):
            raise TypeError("authority must be an AgentPrincipalAuthority")
        payload = {
            "schema": "lightwork.identity_governance.agent-federation-binding.v1",
            "tenant_sha256": authority.tenant_sha256,
            "provider": _provider(provider, federation_only=True),
            "principal_ref": authority.principal_ref,
            "provider_subject_id": _pseudonymous(
                provider_subject_id,
                "provider_subject_id",
                prefixes={"sub", "idn"},
            ),
            "authority_binding_sha256": authority.authority_binding_sha256,
            "authority_revision": authority.revision,
        }
        return cls(
            tenant_sha256=authority.tenant_sha256,
            provider=provider,
            principal_ref=authority.principal_ref,
            provider_subject_id=provider_subject_id,
            authority_binding_sha256=authority.authority_binding_sha256,
            authority_revision=authority.revision,
            binding_sha256=_sha256(payload),
        )


@dataclass(frozen=True)
class FederationObservationRequest:
    schema: str
    tenant_sha256: str
    provider: FederationProvider | str
    principal_ref: str
    provider_subject_id: str
    binding_sha256: str
    binding_revision: int


@dataclass(frozen=True)
class AgentPrincipalFederationObservation:
    """Closed provider result; arbitrary provider JSON is not admitted here."""

    schema: str
    tenant_sha256: str
    provider: FederationProvider | str
    principal_ref: str
    provider_subject_id: str
    binding_sha256: str
    binding_revision: int
    active: bool
    permission_ids: tuple[str, ...]
    source_sha256: str
    observed_at: float
    coverage: Literal["bounded_observation"] = "bounded_observation"
    live_acceptance_verified: bool = False

    def __post_init__(self) -> None:
        if self.schema != FEDERATION_OBSERVATION_SCHEMA:
            raise ValueError("federation observation schema is unsupported")
        object.__setattr__(self, "tenant_sha256", _digest(self.tenant_sha256, "tenant_sha256"))
        object.__setattr__(self, "provider", _provider(self.provider, federation_only=True))
        object.__setattr__(
            self,
            "principal_ref",
            _pseudonymous(self.principal_ref, "principal_ref", prefixes={"prn"}),
        )
        object.__setattr__(
            self,
            "provider_subject_id",
            _pseudonymous(
                self.provider_subject_id,
                "provider_subject_id",
                prefixes={"sub", "idn"},
            ),
        )
        object.__setattr__(self, "binding_sha256", _digest(self.binding_sha256, "binding_sha256"))
        object.__setattr__(self, "binding_revision", _positive_revision(self.binding_revision))
        if not isinstance(self.active, bool):
            raise ValueError("federation observation active must be boolean")
        permissions = tuple(
            _pseudonymous(item, "permission_id", prefixes={"prm"}) for item in self.permission_ids
        )
        if len(permissions) > _MAX_CAPABILITIES or permissions != tuple(sorted(set(permissions))):
            raise ValueError("permission_ids must be bounded, sorted, and unique")
        object.__setattr__(self, "permission_ids", permissions)
        object.__setattr__(self, "source_sha256", _digest(self.source_sha256, "source_sha256"))
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at, "observed_at"))
        if self.coverage != "bounded_observation":
            raise ValueError("federation observation coverage is unsupported")
        if self.live_acceptance_verified is not False:
            raise ValueError("federation observation cannot claim live acceptance")


class AgentPrincipalFederationTransport(Protocol):
    """Credential-owning provider seam; requests contain pseudonyms only."""

    def observe(
        self, request: FederationObservationRequest
    ) -> AgentPrincipalFederationObservation: ...


class _AgentPrincipalFederationAdapter:
    provider: FederationProvider

    def __init__(self, transport: AgentPrincipalFederationTransport) -> None:
        if transport is None or not callable(getattr(transport, "observe", None)):
            raise TypeError("federation transport must implement observe")
        self._transport = transport

    def observe(
        self,
        binding: AgentPrincipalFederationBinding,
        *,
        expected_binding_sha256: str,
        expected_revision: int,
    ) -> AgentPrincipalFederationObservation:
        if not isinstance(binding, AgentPrincipalFederationBinding):
            raise TypeError("binding must be an AgentPrincipalFederationBinding")
        if binding.provider != self.provider:
            raise IdentityGovernanceBindingError("federation_provider_mismatch")
        if binding.binding_sha256 != _digest(
            expected_binding_sha256, "expected_binding_sha256"
        ) or binding.authority_revision != _positive_revision(expected_revision):
            raise IdentityGovernanceStaleBinding("federation_binding_stale")
        request = FederationObservationRequest(
            schema="lightwork.identity_governance.agent-principal-query.v1",
            tenant_sha256=binding.tenant_sha256,
            provider=self.provider,
            principal_ref=binding.principal_ref,
            provider_subject_id=binding.provider_subject_id,
            binding_sha256=binding.binding_sha256,
            binding_revision=binding.authority_revision,
        )
        try:
            observation = self._transport.observe(request)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise IdentityGovernanceDeliveryError("federation_observation_unavailable") from None
        if not isinstance(observation, AgentPrincipalFederationObservation):
            raise IdentityGovernanceDeliveryError("federation_response_invalid")
        expected = (
            request.tenant_sha256,
            request.provider,
            request.principal_ref,
            request.provider_subject_id,
            request.binding_sha256,
            request.binding_revision,
        )
        actual = (
            observation.tenant_sha256,
            observation.provider,
            observation.principal_ref,
            observation.provider_subject_id,
            observation.binding_sha256,
            observation.binding_revision,
        )
        if actual != expected:
            raise IdentityGovernanceBindingError("federation_response_binding_mismatch")
        return observation

    def status(self) -> dict[str, Any]:
        return {
            "schema": "lightwork.identity_governance.federation-adapter-status.v1",
            "provider": self.provider,
            "mode": "bounded_observation",
            "provider_writes": False,
            "raw_identity_retained": False,
            "live_acceptance_verified": False,
            "complete_inventory_claimed": False,
        }


class EntraAgentPrincipalFederationAdapter(_AgentPrincipalFederationAdapter):
    provider: FederationProvider = "entra"


class OktaAgentPrincipalFederationAdapter(_AgentPrincipalFederationAdapter):
    provider: FederationProvider = "okta"


@dataclass(frozen=True)
class CapabilityRule:
    tenant_sha256: str
    provider: FederationProvider | str
    permission_id: str
    capabilities: tuple[str, ...]
    revision: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_sha256", _digest(self.tenant_sha256, "tenant_sha256"))
        object.__setattr__(self, "provider", _provider(self.provider, federation_only=True))
        object.__setattr__(
            self,
            "permission_id",
            _pseudonymous(self.permission_id, "permission_id", prefixes={"prm"}),
        )
        object.__setattr__(
            self,
            "capabilities",
            _codes(self.capabilities, "capabilities", maximum=_MAX_CAPABILITIES),
        )
        object.__setattr__(self, "revision", _positive_revision(self.revision))

    @property
    def rule_sha256(self) -> str:
        return _sha256(
            {
                "schema": "lightwork.identity_governance.capability-rule.v1",
                "tenant_sha256": self.tenant_sha256,
                "provider": self.provider,
                "permission_id": self.permission_id,
                "capabilities": self.capabilities,
                "revision": self.revision,
            }
        )


@dataclass(frozen=True)
class IdentityCapabilityMapping:
    schema: str
    tenant_sha256: str
    provider: FederationProvider | str
    principal_ref: str
    authority_binding_sha256: str
    authority_revision: int
    federation_binding_sha256: str
    capabilities: tuple[str, ...]
    unmapped_permission_count: int
    mapping_revision: int
    rules_sha256: str
    mapping_sha256: str
    source_sha256: str
    observed_at: float
    expires_at: float
    live_acceptance_verified: bool = False

    def __post_init__(self) -> None:
        if self.schema != CAPABILITY_MAPPING_SCHEMA:
            raise ValueError("capability mapping schema is unsupported")
        object.__setattr__(self, "tenant_sha256", _digest(self.tenant_sha256, "tenant_sha256"))
        object.__setattr__(self, "provider", _provider(self.provider, federation_only=True))
        object.__setattr__(
            self,
            "principal_ref",
            _pseudonymous(self.principal_ref, "principal_ref", prefixes={"prn"}),
        )
        object.__setattr__(
            self,
            "authority_binding_sha256",
            _digest(self.authority_binding_sha256, "authority_binding_sha256"),
        )
        object.__setattr__(self, "authority_revision", _positive_revision(self.authority_revision))
        object.__setattr__(
            self,
            "federation_binding_sha256",
            _digest(self.federation_binding_sha256, "federation_binding_sha256"),
        )
        object.__setattr__(
            self,
            "capabilities",
            _codes(self.capabilities, "capabilities", maximum=_MAX_CAPABILITIES),
        )
        if (
            isinstance(self.unmapped_permission_count, bool)
            or not isinstance(self.unmapped_permission_count, int)
            or not 0 <= self.unmapped_permission_count <= _MAX_CAPABILITIES
        ):
            raise ValueError("unmapped_permission_count is invalid")
        object.__setattr__(self, "mapping_revision", _positive_revision(self.mapping_revision))
        object.__setattr__(self, "rules_sha256", _digest(self.rules_sha256, "rules_sha256"))
        object.__setattr__(self, "mapping_sha256", _digest(self.mapping_sha256, "mapping_sha256"))
        object.__setattr__(self, "source_sha256", _digest(self.source_sha256, "source_sha256"))
        observed_at = _timestamp(self.observed_at, "observed_at")
        expires_at = _timestamp(self.expires_at, "expires_at")
        if expires_at <= observed_at or expires_at - observed_at > 3_600.0:
            raise ValueError("capability mapping validity window is invalid")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "expires_at", expires_at)
        material = {
            "schema": self.schema,
            "tenant_sha256": self.tenant_sha256,
            "provider": self.provider,
            "principal_ref": self.principal_ref,
            "authority_binding_sha256": self.authority_binding_sha256,
            "authority_revision": self.authority_revision,
            "federation_binding_sha256": self.federation_binding_sha256,
            "capabilities": self.capabilities,
            "unmapped_permission_count": self.unmapped_permission_count,
            "mapping_revision": self.mapping_revision,
            "rules_sha256": self.rules_sha256,
            "source_sha256": self.source_sha256,
            "observed_at": self.observed_at,
            "expires_at": self.expires_at,
        }
        if self.mapping_sha256 != _sha256(material):
            raise ValueError("capability mapping digest does not match")
        if self.live_acceptance_verified is not False:
            raise ValueError("capability mapping cannot claim live acceptance")


class IdentityCapabilityMapper:
    """Map exact provider permission pseudonyms to local capability codes."""

    def __init__(
        self,
        rules: Sequence[CapabilityRule],
        *,
        revision: int,
        clock: Callable[[], float] = time.time,
        max_observation_age_seconds: float = 300.0,
        max_future_skew_seconds: float = 30.0,
    ) -> None:
        selected = tuple(rules)
        if len(selected) > _MAX_CAPABILITIES or any(
            not isinstance(item, CapabilityRule) for item in selected
        ):
            raise ValueError("capability rules are invalid")
        keys = [(item.tenant_sha256, item.provider, item.permission_id) for item in selected]
        if keys != sorted(set(keys)):
            raise ValueError("capability rules must be sorted and unique")
        self._rules = selected
        self._revision = _positive_revision(revision, "mapping revision")
        self._rules_sha256 = _sha256([item.rule_sha256 for item in selected])
        for value, label, lower, upper in (
            (
                max_observation_age_seconds,
                "max_observation_age_seconds",
                1.0,
                3_600.0,
            ),
            (max_future_skew_seconds, "max_future_skew_seconds", 0.0, 300.0),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not lower <= float(value) <= upper
            ):
                raise ValueError(f"{label} is outside its finite bound")
        self._clock = clock
        self._max_observation_age_seconds = float(max_observation_age_seconds)
        self._max_future_skew_seconds = float(max_future_skew_seconds)

    def map(
        self,
        authority: AgentPrincipalAuthority,
        binding: AgentPrincipalFederationBinding,
        observation: AgentPrincipalFederationObservation,
        *,
        expected_binding_sha256: str,
        expected_authority_revision: int,
    ) -> IdentityCapabilityMapping:
        if not isinstance(authority, AgentPrincipalAuthority):
            raise TypeError("authority must be an AgentPrincipalAuthority")
        if not isinstance(binding, AgentPrincipalFederationBinding):
            raise TypeError("binding must be an AgentPrincipalFederationBinding")
        if not isinstance(observation, AgentPrincipalFederationObservation):
            raise TypeError("observation must be an AgentPrincipalFederationObservation")
        try:
            now = _timestamp(self._clock(), "clock")
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise IdentityGovernanceStateError("capability_mapping_clock_invalid") from None
        if (
            observation.observed_at > now + self._max_future_skew_seconds
            or now - observation.observed_at > self._max_observation_age_seconds
        ):
            raise IdentityGovernanceStaleBinding("federation_observation_stale")
        exact = (
            authority.tenant_sha256,
            authority.principal_ref,
            authority.authority_binding_sha256,
            authority.revision,
        )
        bound = (
            binding.tenant_sha256,
            binding.principal_ref,
            binding.authority_binding_sha256,
            binding.authority_revision,
        )
        if exact != bound:
            raise IdentityGovernanceBindingError("capability_authority_binding_mismatch")
        expected_binding = _digest(expected_binding_sha256, "expected_binding_sha256")
        expected_revision = _positive_revision(
            expected_authority_revision, "expected_authority_revision"
        )
        if binding.binding_sha256 != expected_binding or authority.revision != expected_revision:
            raise IdentityGovernanceStaleBinding("capability_binding_stale")
        observed = (
            observation.tenant_sha256,
            observation.provider,
            observation.principal_ref,
            observation.provider_subject_id,
            observation.binding_sha256,
            observation.binding_revision,
        )
        expected_observation = (
            binding.tenant_sha256,
            binding.provider,
            binding.principal_ref,
            binding.provider_subject_id,
            binding.binding_sha256,
            binding.authority_revision,
        )
        if observed != expected_observation:
            raise IdentityGovernanceBindingError("capability_observation_binding_mismatch")
        matched: set[str] = set()
        mapped_permissions: set[str] = set()
        if observation.active:
            permissions = set(observation.permission_ids)
            for rule in self._rules:
                if rule.provider == observation.provider and rule.permission_id in permissions:
                    if rule.tenant_sha256 != authority.tenant_sha256:
                        continue
                    matched.update(rule.capabilities)
                    mapped_permissions.add(rule.permission_id)
        capabilities = tuple(sorted(matched))
        material = {
            "schema": CAPABILITY_MAPPING_SCHEMA,
            "tenant_sha256": authority.tenant_sha256,
            "provider": observation.provider,
            "principal_ref": authority.principal_ref,
            "authority_binding_sha256": authority.authority_binding_sha256,
            "authority_revision": authority.revision,
            "federation_binding_sha256": binding.binding_sha256,
            "capabilities": capabilities,
            "unmapped_permission_count": len(set(observation.permission_ids) - mapped_permissions),
            "mapping_revision": self._revision,
            "rules_sha256": self._rules_sha256,
            "source_sha256": observation.source_sha256,
            "observed_at": observation.observed_at,
            "expires_at": observation.observed_at + self._max_observation_age_seconds,
        }
        return IdentityCapabilityMapping(
            **material,
            mapping_sha256=_sha256(material),
            live_acceptance_verified=False,
        )


@dataclass(frozen=True)
class ConditionalAccessObservationRequest:
    schema: str
    tenant_sha256: str
    provider: FederationProvider | str
    principal_ref: str
    provider_subject_id: str
    binding_sha256: str
    binding_revision: int


@dataclass(frozen=True)
class ConditionalAccessObservation:
    schema: str
    tenant_sha256: str
    provider: FederationProvider | str
    principal_ref: str
    provider_subject_id: str
    binding_sha256: str
    binding_revision: int
    outcome: Literal["allow", "block", "challenge", "not_evaluated"]
    policy_sha256: str
    reason_codes: tuple[str, ...]
    source_sha256: str
    observed_at: float
    observation_only: bool = True
    live_acceptance_verified: bool = False

    def __post_init__(self) -> None:
        if self.schema != CONDITIONAL_ACCESS_SCHEMA:
            raise ValueError("conditional-access observation schema is unsupported")
        object.__setattr__(self, "tenant_sha256", _digest(self.tenant_sha256, "tenant_sha256"))
        object.__setattr__(self, "provider", _provider(self.provider, federation_only=True))
        object.__setattr__(
            self,
            "principal_ref",
            _pseudonymous(self.principal_ref, "principal_ref", prefixes={"prn"}),
        )
        object.__setattr__(
            self,
            "provider_subject_id",
            _pseudonymous(
                self.provider_subject_id,
                "provider_subject_id",
                prefixes={"sub", "idn"},
            ),
        )
        object.__setattr__(self, "binding_sha256", _digest(self.binding_sha256, "binding_sha256"))
        object.__setattr__(self, "binding_revision", _positive_revision(self.binding_revision))
        if self.outcome not in {"allow", "block", "challenge", "not_evaluated"}:
            raise ValueError("conditional-access outcome is unsupported")
        object.__setattr__(self, "policy_sha256", _digest(self.policy_sha256, "policy_sha256"))
        object.__setattr__(
            self,
            "reason_codes",
            _codes(self.reason_codes, "reason_codes", maximum=32),
        )
        object.__setattr__(self, "source_sha256", _digest(self.source_sha256, "source_sha256"))
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at, "observed_at"))
        if self.observation_only is not True or self.live_acceptance_verified is not False:
            raise ValueError("conditional-access observation contains unsupported claims")


class ConditionalAccessObservationTransport(Protocol):
    def observe(
        self, request: ConditionalAccessObservationRequest
    ) -> ConditionalAccessObservation: ...


class ConditionalAccessObserver:
    """Provider-bound observation seam; it never mutates provider policy."""

    def __init__(
        self,
        provider: FederationProvider,
        transport: ConditionalAccessObservationTransport,
    ) -> None:
        self.provider = _provider(provider, federation_only=True)
        if transport is None or not callable(getattr(transport, "observe", None)):
            raise TypeError("conditional-access transport must implement observe")
        self._transport = transport

    def observe(
        self,
        binding: AgentPrincipalFederationBinding,
        *,
        expected_binding_sha256: str,
        expected_revision: int,
    ) -> ConditionalAccessObservation:
        if not isinstance(binding, AgentPrincipalFederationBinding):
            raise TypeError("binding must be an AgentPrincipalFederationBinding")
        if binding.provider != self.provider:
            raise IdentityGovernanceBindingError("conditional_access_provider_mismatch")
        if binding.binding_sha256 != _digest(
            expected_binding_sha256, "expected_binding_sha256"
        ) or binding.authority_revision != _positive_revision(expected_revision):
            raise IdentityGovernanceStaleBinding("conditional_access_binding_stale")
        request = ConditionalAccessObservationRequest(
            schema="lightwork.identity_governance.conditional-access-query.v1",
            tenant_sha256=binding.tenant_sha256,
            provider=binding.provider,
            principal_ref=binding.principal_ref,
            provider_subject_id=binding.provider_subject_id,
            binding_sha256=binding.binding_sha256,
            binding_revision=binding.authority_revision,
        )
        try:
            observation = self._transport.observe(request)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise IdentityGovernanceDeliveryError(
                "conditional_access_observation_unavailable"
            ) from None
        if not isinstance(observation, ConditionalAccessObservation):
            raise IdentityGovernanceDeliveryError("conditional_access_response_invalid")
        actual = (
            observation.tenant_sha256,
            observation.provider,
            observation.principal_ref,
            observation.provider_subject_id,
            observation.binding_sha256,
            observation.binding_revision,
        )
        expected = (
            request.tenant_sha256,
            request.provider,
            request.principal_ref,
            request.provider_subject_id,
            request.binding_sha256,
            request.binding_revision,
        )
        if actual != expected:
            raise IdentityGovernanceBindingError("conditional_access_binding_mismatch")
        return observation

    def status(self) -> dict[str, Any]:
        return {
            "schema": "lightwork.identity_governance.conditional-access-status.v1",
            "provider": self.provider,
            "observation_available": True,
            "decision_seam_available": True,
            "provider_policy_writes": False,
            "live_acceptance_verified": False,
        }


@dataclass(frozen=True)
class ConditionalAccessDecision:
    schema: str
    tenant_sha256: str
    provider: FederationProvider | str
    principal_ref: str
    binding_sha256: str
    binding_revision: int
    required_capability: str
    action: Literal["permit", "deny", "step_up"]
    reason_code: str
    idempotency_sha256: str
    decision_sha256: str
    provider_policy_mutated: bool = False
    live_acceptance_verified: bool = False

    def __post_init__(self) -> None:
        if self.schema != ENFORCEMENT_DECISION_SCHEMA:
            raise ValueError("conditional-access decision schema is unsupported")
        object.__setattr__(self, "tenant_sha256", _digest(self.tenant_sha256, "tenant_sha256"))
        object.__setattr__(self, "provider", _provider(self.provider, federation_only=True))
        object.__setattr__(
            self,
            "principal_ref",
            _pseudonymous(self.principal_ref, "principal_ref", prefixes={"prn"}),
        )
        object.__setattr__(self, "binding_sha256", _digest(self.binding_sha256, "binding_sha256"))
        object.__setattr__(self, "binding_revision", _positive_revision(self.binding_revision))
        object.__setattr__(
            self,
            "required_capability",
            _code(self.required_capability, "required_capability"),
        )
        if self.action not in {"permit", "deny", "step_up"}:
            raise ValueError("conditional-access action is unsupported")
        object.__setattr__(self, "reason_code", _code(self.reason_code, "reason_code"))
        object.__setattr__(
            self,
            "idempotency_sha256",
            _digest(self.idempotency_sha256, "idempotency_sha256"),
        )
        object.__setattr__(
            self, "decision_sha256", _digest(self.decision_sha256, "decision_sha256")
        )
        if self.provider_policy_mutated is not False:
            raise ValueError("conditional-access decision cannot claim provider mutation")
        if self.live_acceptance_verified is not False:
            raise ValueError("conditional-access decision cannot claim live acceptance")


class ConditionalAccessDecisionEngine:
    """Fail-closed local enforcement decision from exact normalized inputs."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        max_observation_age_seconds: float = 300.0,
        max_future_skew_seconds: float = 30.0,
    ) -> None:
        for value, label, lower, upper in (
            (
                max_observation_age_seconds,
                "max_observation_age_seconds",
                1.0,
                3_600.0,
            ),
            (max_future_skew_seconds, "max_future_skew_seconds", 0.0, 300.0),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not lower <= float(value) <= upper
            ):
                raise ValueError(f"{label} is outside its finite bound")
        self._clock = clock
        self._max_observation_age_seconds = float(max_observation_age_seconds)
        self._max_future_skew_seconds = float(max_future_skew_seconds)

    def decide(
        self,
        authority: AgentPrincipalAuthority,
        mapping: IdentityCapabilityMapping,
        observation: ConditionalAccessObservation,
        *,
        required_capability: str,
        expected_binding_sha256: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> ConditionalAccessDecision:
        if not isinstance(authority, AgentPrincipalAuthority):
            raise TypeError("authority must be an AgentPrincipalAuthority")
        if not isinstance(mapping, IdentityCapabilityMapping):
            raise TypeError("mapping must be an IdentityCapabilityMapping")
        if not isinstance(observation, ConditionalAccessObservation):
            raise TypeError("observation must be a ConditionalAccessObservation")
        try:
            now = _timestamp(self._clock(), "clock")
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise IdentityGovernanceStateError("conditional_access_clock_invalid") from None
        if (
            observation.observed_at > now + self._max_future_skew_seconds
            or now - observation.observed_at > self._max_observation_age_seconds
        ):
            raise IdentityGovernanceStaleBinding("conditional_access_observation_stale")
        if mapping.observed_at > now + self._max_future_skew_seconds or mapping.expires_at < now:
            raise IdentityGovernanceStaleBinding("capability_mapping_expired")
        expected_binding = _digest(expected_binding_sha256, "expected_binding_sha256")
        expected_revision_number = _positive_revision(expected_revision)
        common = (authority.tenant_sha256, authority.principal_ref)
        if common != (mapping.tenant_sha256, mapping.principal_ref) or common != (
            observation.tenant_sha256,
            observation.principal_ref,
        ):
            raise IdentityGovernanceBindingError("conditional_access_tenant_principal_mismatch")
        if (
            mapping.authority_binding_sha256 != authority.authority_binding_sha256
            or mapping.authority_revision != authority.revision
            or observation.binding_sha256 != mapping.federation_binding_sha256
            or observation.binding_sha256 != expected_binding
            or observation.binding_revision != expected_revision_number
            or authority.revision != expected_revision_number
        ):
            raise IdentityGovernanceStaleBinding("conditional_access_decision_binding_stale")
        capability = _code(required_capability, "required_capability")
        idempotency_sha = _idempotency_sha256(idempotency_key)
        if observation.outcome == "block":
            action, reason = "deny", "provider_policy_block"
        elif observation.outcome == "challenge":
            action, reason = "step_up", "provider_policy_challenge"
        elif observation.outcome != "allow":
            action, reason = "deny", "provider_policy_not_evaluated"
        elif capability not in mapping.capabilities:
            action, reason = "deny", "capability_not_mapped"
        else:
            action, reason = "permit", "policy_and_capability_allow"
        material = {
            "schema": ENFORCEMENT_DECISION_SCHEMA,
            "tenant_sha256": authority.tenant_sha256,
            "provider": mapping.provider,
            "principal_ref": authority.principal_ref,
            "binding_sha256": expected_binding,
            "binding_revision": expected_revision_number,
            "required_capability": capability,
            "action": action,
            "reason_code": reason,
            "idempotency_sha256": idempotency_sha,
            "mapping_sha256": mapping.mapping_sha256,
            "policy_sha256": observation.policy_sha256,
            "source_sha256": observation.source_sha256,
        }
        return ConditionalAccessDecision(
            **{
                key: value
                for key, value in material.items()
                if key not in {"mapping_sha256", "policy_sha256", "source_sha256"}
            },
            decision_sha256=_sha256(material),
            provider_policy_mutated=False,
            live_acceptance_verified=False,
        )


class IdentityGovernanceRecordStore(Protocol):
    def authoritative_time(self) -> float: ...

    def get(self, record_id: str) -> dict[str, Any] | None: ...

    def create(
        self,
        record: dict[str, Any],
        *,
        action: str,
        actor: str,
        max_records: int | None = None,
    ) -> dict[str, Any]: ...

    def update(
        self,
        record_id: str,
        mutate: Callable[[dict[str, Any]], None],
        *,
        expected_revision: int,
        action: str,
        actor: str,
    ) -> dict[str, Any] | None: ...


def _default_change_store() -> IdentityGovernanceRecordStore:
    from .governed_records import GovernedRecordStore

    return GovernedRecordStore(
        "identity_governance_change_coupling",
        "IGCC",
        "identity_governance_change_coupling_state",
    )


@dataclass(frozen=True)
class ChangeDeliveryAck:
    schema: str
    sink: DeliverySinkName | str
    event_id: str
    idempotency_sha256: str
    receipt_sha256: str
    accepted: bool
    live_acceptance_verified: bool = False

    def __post_init__(self) -> None:
        if self.schema != CHANGE_DELIVERY_ACK_SCHEMA:
            raise ValueError("change delivery acknowledgement schema is unsupported")
        if self.sink not in {"dynamic_risk", "alert"}:
            raise ValueError("change delivery sink is unsupported")
        if not isinstance(self.event_id, str) or not self.event_id.startswith("IGCE-"):
            raise ValueError("change delivery event id is invalid")
        object.__setattr__(
            self, "idempotency_sha256", _digest(self.idempotency_sha256, "idempotency_sha256")
        )
        object.__setattr__(self, "receipt_sha256", _digest(self.receipt_sha256, "receipt_sha256"))
        if self.accepted is not True or self.live_acceptance_verified is not False:
            raise ValueError("change delivery acknowledgement contains unsupported claims")


ChangeSink = Callable[[Mapping[str, Any]], ChangeDeliveryAck]


def _bounded_count(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_CHANGE_COUNT:
        raise ValueError(f"{label} is invalid")
    return value


def _validate_change(payload: Mapping[str, Any]) -> dict[str, Any]:
    expected_outer = {
        "schema",
        "tenant_sha256",
        "provider",
        "slot",
        "collected_at",
        "source_state",
        "source_complete",
        "live_acceptance_verified",
        "complete_inventory_claimed",
        "delta",
        "risk_generated",
        "change_sha256",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected_outer:
        raise ValueError("normalized identity change shape is unsupported")
    if payload.get("schema") != NORMALIZED_CHANGE_SCHEMA:
        raise ValueError("normalized identity change schema is unsupported")
    tenant = _digest(payload.get("tenant_sha256"), "tenant_sha256")
    provider = _provider(payload.get("provider"))
    slot = payload.get("slot")
    if isinstance(slot, bool) or not isinstance(slot, int) or slot < 0:
        raise ValueError("normalized identity change slot is invalid")
    collected_at = _timestamp(payload.get("collected_at"), "collected_at")
    if (
        payload.get("source_state") != "incomplete"
        or payload.get("source_complete") is not False
        or payload.get("live_acceptance_verified") is not False
        or payload.get("complete_inventory_claimed") is not False
        or payload.get("risk_generated") is not False
    ):
        raise ValueError("normalized identity change contains unsupported claims")
    delta = payload.get("delta")
    expected_delta = {
        "schema",
        "change_kind",
        "changed",
        "previous_snapshot_sha256",
        "current_snapshot_sha256",
        "configuration_changed",
        "provider_tenant_changed",
        "categories",
        "risk_generated",
    }
    if not isinstance(delta, Mapping) or set(delta) != expected_delta:
        raise ValueError("normalized identity delta shape is unsupported")
    if (
        delta.get("schema") != NORMALIZED_DELTA_SCHEMA
        or delta.get("change_kind") not in {"baseline", "comparison"}
        or delta.get("changed") is not True
        or not isinstance(delta.get("configuration_changed"), bool)
        or not isinstance(delta.get("provider_tenant_changed"), bool)
        or delta.get("risk_generated") is not False
    ):
        raise ValueError("normalized identity delta is unsupported")
    previous = delta.get("previous_snapshot_sha256")
    if previous is not None:
        previous = _digest(previous, "previous_snapshot_sha256")
    if delta.get("change_kind") == "baseline" and previous is not None:
        raise ValueError("baseline identity delta cannot have a previous snapshot")
    if delta.get("change_kind") == "comparison" and previous is None:
        raise ValueError("comparison identity delta requires a previous snapshot")
    current = _digest(delta.get("current_snapshot_sha256"), "current_snapshot_sha256")
    categories = delta.get("categories")
    names = {"identities", "grants", "data_classifications"}
    if not isinstance(categories, Mapping) or set(categories) != names:
        raise ValueError("normalized identity delta categories are unsupported")
    normalized_categories: dict[str, dict[str, Any]] = {}
    for name in sorted(names):
        category = categories.get(name)
        expected_category = {
            "added_count",
            "removed_count",
            "added_sha256",
            "removed_sha256",
            "digests_truncated",
        }
        if not isinstance(category, Mapping) or set(category) != expected_category:
            raise ValueError("normalized identity delta category shape is unsupported")
        added_count = _bounded_count(category.get("added_count"), "added_count")
        removed_count = _bounded_count(category.get("removed_count"), "removed_count")
        added = _digests(
            category.get("added_sha256"),
            "added_sha256",
            maximum=_MAX_DELTA_DIGESTS,
        )
        removed = _digests(
            category.get("removed_sha256"),
            "removed_sha256",
            maximum=_MAX_DELTA_DIGESTS,
        )
        truncated = category.get("digests_truncated")
        if not isinstance(truncated, bool):
            raise ValueError("digests_truncated is invalid")
        if len(added) > added_count or len(removed) > removed_count:
            raise ValueError("normalized identity delta digest count is invalid")
        if not truncated and (len(added) != added_count or len(removed) != removed_count):
            raise ValueError("normalized identity delta omitted untruncated digests")
        normalized_categories[name] = {
            "added_count": added_count,
            "removed_count": removed_count,
            "added_sha256": list(added),
            "removed_sha256": list(removed),
            "digests_truncated": truncated,
        }
    body = dict(payload)
    change_sha = body.pop("change_sha256", None)
    if _digest(change_sha, "change_sha256") != _sha256(body):
        raise ValueError("normalized identity change digest does not match")
    return {
        "tenant_sha256": tenant,
        "provider": provider,
        "slot": slot,
        "collected_at": collected_at,
        "change_sha256": change_sha,
        "change_kind": delta["change_kind"],
        "previous_snapshot_sha256": previous,
        "current_snapshot_sha256": current,
        "configuration_changed": delta["configuration_changed"],
        "provider_tenant_changed": delta["provider_tenant_changed"],
        "categories": normalized_categories,
    }


class NormalizedIdentityChangeCoupler:
    """Idempotently fan one scheduler change to risk and alert seams."""

    def __init__(
        self,
        *,
        enable: bool = False,
        tenant_sha256: str,
        risk_sink: ChangeSink | None = None,
        alert_sink: ChangeSink | None = None,
        store: IdentityGovernanceRecordStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(enable, bool):
            raise ValueError("change coupling enable must be boolean")
        self._enabled = enable
        self._tenant_sha256 = _digest(tenant_sha256, "tenant_sha256")
        self._sinks: dict[DeliverySinkName, ChangeSink] = {}
        for name, sink in (("dynamic_risk", risk_sink), ("alert", alert_sink)):
            if sink is not None and not callable(sink):
                raise TypeError(f"{name} sink must be callable")
            if sink is not None:
                self._sinks[name] = sink
        self._store = store
        self._clock = clock

    def _require_available(self) -> IdentityGovernanceRecordStore:
        if not self._enabled:
            raise IdentityGovernanceControlConfigError("change_coupling_disabled")
        if not self._sinks:
            raise IdentityGovernanceControlConfigError("change_coupling_sink_unavailable")
        if self._store is None:
            self._store = _default_change_store()
        return self._store

    def _event(self, change: Mapping[str, Any]) -> dict[str, Any]:
        categories = change["categories"]
        counts = {
            "added": sum(category["added_count"] for category in categories.values()),
            "removed": sum(category["removed_count"] for category in categories.values()),
        }
        event_id = (
            "IGCE-"
            + _sha256(
                {
                    "tenant_sha256": change["tenant_sha256"],
                    "change_sha256": change["change_sha256"],
                }
            )[:32]
        )
        return {
            "schema": CHANGE_EVENT_SCHEMA,
            "event_id": event_id,
            "tenant_sha256": change["tenant_sha256"],
            "provider": change["provider"],
            "change_sha256": change["change_sha256"],
            "slot": change["slot"],
            "observed_at": change["collected_at"],
            "change_kind": change["change_kind"],
            "previous_snapshot_sha256": change["previous_snapshot_sha256"],
            "current_snapshot_sha256": change["current_snapshot_sha256"],
            "counts": counts,
            "configuration_changed": change["configuration_changed"],
            "provider_tenant_changed": change["provider_tenant_changed"],
            "digests_truncated": any(
                category["digests_truncated"] for category in categories.values()
            ),
            "risk_assessment_required": True,
            "alert_candidate": True,
            "source_complete": False,
            "live_acceptance_verified": False,
        }

    def _admit(
        self,
        store: IdentityGovernanceRecordStore,
        event: Mapping[str, Any],
    ) -> dict[str, Any]:
        record_id = (
            "IGCC-"
            + _sha256(
                {
                    "tenant_sha256": event["tenant_sha256"],
                    "event_id": event["event_id"],
                }
            )[:32]
        )
        event_sha = _sha256(event)
        existing = store.get(record_id)
        if existing is not None:
            if (
                existing.get("tenant_sha256") != self._tenant_sha256
                or existing.get("event_sha256") != event_sha
                or existing.get("change_sha256") != event["change_sha256"]
            ):
                raise IdentityGovernanceIdempotencyConflict("change_coupling_idempotency_conflict")
            return existing
        deliveries = {
            name: {
                "status": "pending",
                "attempts": 0,
                "idempotency_sha256": _sha256({"event_id": event["event_id"], "sink": name}),
                "receipt_sha256": None,
                "last_error_type": None,
            }
            for name in sorted(self._sinks)
        }
        record = {
            "id": record_id,
            "schema": CHANGE_STATE_SCHEMA,
            "tenant_sha256": self._tenant_sha256,
            "provider": event["provider"],
            "event_id": event["event_id"],
            "event_sha256": event_sha,
            "change_sha256": event["change_sha256"],
            "current_snapshot_sha256": event["current_snapshot_sha256"],
            "status": "pending",
            "health": "healthy",
            "deliveries": deliveries,
            "live_acceptance_verified": False,
            "risk_score_generated": False,
        }
        try:
            return store.create(
                record,
                action="admit_identity_change_coupling",
                actor="system:identity-governance-change-coupler",
                max_records=10_000,
            )
        except RecordConflict as exc:
            concurrent = store.get(record_id)
            if concurrent is None:
                raise IdentityGovernanceStateError("change_coupling_admission_failed") from exc
            if concurrent.get("event_sha256") != event_sha:
                raise IdentityGovernanceIdempotencyConflict(
                    "change_coupling_idempotency_conflict"
                ) from exc
            return concurrent

    def _update(
        self,
        store: IdentityGovernanceRecordStore,
        record: Mapping[str, Any],
        mutate: Callable[[dict[str, Any]], None],
        *,
        action: str,
    ) -> dict[str, Any]:
        try:
            saved = store.update(
                str(record.get("id") or ""),
                mutate,
                expected_revision=_record_revision(record),
                action=action,
                actor="system:identity-governance-change-coupler",
            )
        except RecordConflict as exc:
            raise IdentityGovernanceStateError("change_coupling_revision_conflict") from exc
        if saved is None:
            raise IdentityGovernanceStateError("change_coupling_state_missing")
        return saved

    def _deliver(
        self,
        store: IdentityGovernanceRecordStore,
        record: Mapping[str, Any],
        event: Mapping[str, Any],
        name: DeliverySinkName,
    ) -> dict[str, Any]:
        deliveries = record.get("deliveries")
        state = deliveries.get(name) if isinstance(deliveries, Mapping) else None
        if not isinstance(state, Mapping):
            raise IdentityGovernanceStateError("change_coupling_delivery_state_invalid")
        if state.get("status") == "delivered":
            return dict(record)
        attempts = state.get("attempts")
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
            raise IdentityGovernanceStateError("change_coupling_attempt_state_invalid")
        idempotency_sha = _digest(state.get("idempotency_sha256"), "idempotency_sha256")

        def claim(current: dict[str, Any]) -> None:
            current_state = current["deliveries"][name]
            if current_state.get("status") == "delivered":
                return
            current_state["status"] = "delivering"
            current_state["attempts"] = attempts + 1
            current_state["last_error_type"] = None

        claimed = self._update(
            store,
            record,
            claim,
            action=f"claim_identity_change_{name}",
        )
        claimed_state = claimed["deliveries"][name]
        if claimed_state.get("status") == "delivered":
            return claimed
        try:
            acknowledgement = self._sinks[name](dict(event))
            if not isinstance(acknowledgement, ChangeDeliveryAck):
                raise ValueError("change sink acknowledgement is invalid")
            if (
                acknowledgement.sink != name
                or acknowledgement.event_id != event["event_id"]
                or acknowledgement.idempotency_sha256 != idempotency_sha
            ):
                raise ValueError("change sink acknowledgement binding does not match")
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            error_type = _safe_error_type(exc)

            def failed(current: dict[str, Any]) -> None:
                current_state = current["deliveries"][name]
                current_state["status"] = "failed"
                current_state["last_error_type"] = error_type
                current["status"] = "degraded"
                current["health"] = "degraded"

            self._update(
                store,
                claimed,
                failed,
                action=f"record_identity_change_{name}_failure",
            )
            raise IdentityGovernanceDeliveryError("change_coupling_degraded") from None

        def delivered(current: dict[str, Any]) -> None:
            current_state = current["deliveries"][name]
            current_state["status"] = "delivered"
            current_state["receipt_sha256"] = acknowledgement.receipt_sha256
            current_state["last_error_type"] = None
            statuses = [item.get("status") for item in current["deliveries"].values()]
            current["status"] = (
                "delivered" if all(item == "delivered" for item in statuses) else "pending"
            )
            current["health"] = "healthy"

        return self._update(
            store,
            claimed,
            delivered,
            action=f"record_identity_change_{name}_delivery",
        )

    def handle(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Validate, bind, and idempotently deliver one scheduler callback."""

        store = self._require_available()
        change = _validate_change(payload)
        if change["tenant_sha256"] != self._tenant_sha256:
            raise IdentityGovernanceBindingError("change_coupling_tenant_mismatch")
        event = self._event(change)
        record = self._admit(store, event)
        for name in sorted(self._sinks):
            record = self._deliver(store, record, event, name)
        return _public(record)

    __call__ = handle

    def status(self) -> dict[str, Any]:
        return {
            "schema": "lightwork.identity_governance.change-coupling-status.v1",
            "enabled": self._enabled,
            "configured_sinks": sorted(self._sinks),
            "runtime_available": self._enabled and bool(self._sinks),
            "idempotent_resume": True,
            "provider_writes": False,
            "risk_score_generated": False,
            "raw_identity_retained": False,
            "live_acceptance_verified": False,
        }


@dataclass(frozen=True)
class PurviewAssetBinding:
    tenant_sha256: str
    asset_id: str
    binding_sha256: str
    revision: int
    classification_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_sha256", _digest(self.tenant_sha256, "tenant_sha256"))
        object.__setattr__(
            self,
            "asset_id",
            _pseudonymous(self.asset_id, "asset_id", prefixes={"ast"}),
        )
        object.__setattr__(self, "binding_sha256", _digest(self.binding_sha256, "binding_sha256"))
        object.__setattr__(self, "revision", _positive_revision(self.revision))
        object.__setattr__(
            self,
            "classification_codes",
            _codes(
                self.classification_codes,
                "classification_codes",
                maximum=_MAX_CLASSIFICATIONS,
            ),
        )


@dataclass(frozen=True)
class PurviewWritebackRequest:
    schema: str
    operation: Literal["apply", "compensate"]
    tenant_sha256: str
    intent_id: str
    principal_ref: str
    asset_id: str
    source_binding_sha256: str
    source_revision: int
    classification_codes: tuple[str, ...]
    previous_classification_codes: tuple[str, ...]
    idempotency_sha256: str
    request_sha256: str

    def __post_init__(self) -> None:
        if self.schema != PURVIEW_REQUEST_SCHEMA or self.operation not in {
            "apply",
            "compensate",
        }:
            raise ValueError("Purview request schema or operation is unsupported")
        object.__setattr__(self, "tenant_sha256", _digest(self.tenant_sha256, "tenant_sha256"))
        if not isinstance(self.intent_id, str) or not _RECORD_ID_RE.fullmatch(self.intent_id):
            raise ValueError("Purview intent id is invalid")
        object.__setattr__(
            self,
            "principal_ref",
            _pseudonymous(self.principal_ref, "principal_ref", prefixes={"prn"}),
        )
        object.__setattr__(
            self, "asset_id", _pseudonymous(self.asset_id, "asset_id", prefixes={"ast"})
        )
        object.__setattr__(
            self,
            "source_binding_sha256",
            _digest(self.source_binding_sha256, "source_binding_sha256"),
        )
        object.__setattr__(self, "source_revision", _positive_revision(self.source_revision))
        object.__setattr__(
            self,
            "classification_codes",
            _codes(
                self.classification_codes,
                "classification_codes",
                maximum=_MAX_CLASSIFICATIONS,
            ),
        )
        object.__setattr__(
            self,
            "previous_classification_codes",
            _codes(
                self.previous_classification_codes,
                "previous_classification_codes",
                maximum=_MAX_CLASSIFICATIONS,
            ),
        )
        object.__setattr__(
            self, "idempotency_sha256", _digest(self.idempotency_sha256, "idempotency_sha256")
        )
        material = {
            "schema": self.schema,
            "operation": self.operation,
            "tenant_sha256": self.tenant_sha256,
            "intent_id": self.intent_id,
            "principal_ref": self.principal_ref,
            "asset_id": self.asset_id,
            "source_binding_sha256": self.source_binding_sha256,
            "source_revision": self.source_revision,
            "classification_codes": self.classification_codes,
            "previous_classification_codes": self.previous_classification_codes,
            "idempotency_sha256": self.idempotency_sha256,
        }
        if self.request_sha256 != _sha256(material):
            raise ValueError("Purview request digest does not match")


@dataclass(frozen=True)
class PurviewWritebackAck:
    schema: str
    operation: Literal["apply", "compensate"]
    tenant_sha256: str
    intent_id: str
    request_sha256: str
    idempotency_sha256: str
    result_binding_sha256: str
    result_revision: int
    receipt_sha256: str
    accepted: bool
    live_acceptance_verified: bool = False

    def __post_init__(self) -> None:
        if self.schema != PURVIEW_ACK_SCHEMA or self.operation not in {
            "apply",
            "compensate",
        }:
            raise ValueError("Purview acknowledgement schema or operation is unsupported")
        object.__setattr__(self, "tenant_sha256", _digest(self.tenant_sha256, "tenant_sha256"))
        if not isinstance(self.intent_id, str) or not _RECORD_ID_RE.fullmatch(self.intent_id):
            raise ValueError("Purview acknowledgement intent id is invalid")
        for field in (
            "request_sha256",
            "idempotency_sha256",
            "result_binding_sha256",
            "receipt_sha256",
        ):
            object.__setattr__(self, field, _digest(getattr(self, field), field))
        object.__setattr__(self, "result_revision", _positive_revision(self.result_revision))
        if self.accepted is not True or self.live_acceptance_verified is not False:
            raise ValueError("Purview acknowledgement contains unsupported claims")


class PurviewWritebackTransport(Protocol):
    """Credential-owning seam; requests are fixed and secret-free."""

    def apply(self, request: PurviewWritebackRequest) -> PurviewWritebackAck: ...

    def compensate(self, request: PurviewWritebackRequest) -> PurviewWritebackAck: ...


PrincipalResolver = Callable[[str, str], AgentPrincipalAuthority]
AssetBindingResolver = Callable[[str, str], PurviewAssetBinding]


def _default_purview_store() -> IdentityGovernanceRecordStore:
    from .governed_records import GovernedRecordStore

    return GovernedRecordStore(
        "identity_governance_purview_writeback",
        "IGPW",
        "identity_governance_purview_writeback_intent",
    )


class GovernedPurviewWritebackService:
    """Opt-in Purview mutation with CAS, replay, and compensation controls."""

    def __init__(
        self,
        *,
        enable: bool = False,
        tenant_sha256: str,
        principal_resolver: PrincipalResolver | None = None,
        asset_binding_resolver: AssetBindingResolver | None = None,
        transport: PurviewWritebackTransport | None = None,
        store: IdentityGovernanceRecordStore | None = None,
        max_attempts: int = 3,
        operation_lease_seconds: float = 60.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(enable, bool):
            raise ValueError("Purview writeback enable must be boolean")
        self._enabled = enable
        self._tenant_sha256 = _digest(tenant_sha256, "tenant_sha256")
        self._principal_resolver = principal_resolver
        self._asset_resolver = asset_binding_resolver
        self._transport = transport
        self._store = store
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or not 1 <= max_attempts <= 10
        ):
            raise ValueError("max_attempts must be between 1 and 10")
        self._max_attempts = max_attempts
        if (
            isinstance(operation_lease_seconds, bool)
            or not isinstance(operation_lease_seconds, (int, float))
            or not math.isfinite(float(operation_lease_seconds))
            or not 5.0 <= float(operation_lease_seconds) <= 300.0
        ):
            raise ValueError("operation_lease_seconds must be between 5 and 300")
        self._operation_lease_seconds = float(operation_lease_seconds)
        self._clock = clock

    def _now(self) -> float:
        try:
            return _timestamp(self._clock(), "clock")
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise IdentityGovernanceStateError("purview_writeback_clock_invalid") from None

    def _require_available(self) -> IdentityGovernanceRecordStore:
        if not self._enabled:
            raise IdentityGovernanceControlConfigError("purview_writeback_disabled")
        if (
            not callable(self._principal_resolver)
            or not callable(self._asset_resolver)
            or self._transport is None
            or not callable(getattr(self._transport, "apply", None))
            or not callable(getattr(self._transport, "compensate", None))
        ):
            raise IdentityGovernanceControlConfigError("purview_writeback_unavailable")
        if self._store is None:
            self._store = _default_purview_store()
        return self._store

    def _current_authority(
        self,
        authority: AgentPrincipalAuthority,
        mapping: IdentityCapabilityMapping,
        capability: str,
    ) -> AgentPrincipalAuthority:
        if not isinstance(authority, AgentPrincipalAuthority):
            raise TypeError("authority must be an AgentPrincipalAuthority")
        if not isinstance(mapping, IdentityCapabilityMapping):
            raise TypeError("mapping must be an IdentityCapabilityMapping")
        if authority.tenant_sha256 != self._tenant_sha256:
            raise IdentityGovernanceBindingError("purview_writeback_tenant_mismatch")
        now = self._now()
        if mapping.observed_at > now + 30.0 or mapping.expires_at < now:
            raise IdentityGovernanceStaleBinding("capability_mapping_expired")
        assert self._principal_resolver is not None
        try:
            current = self._principal_resolver(
                authority.tenant_sha256,
                authority.principal_ref,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise IdentityGovernanceDeliveryError("principal_binding_unavailable") from None
        if not isinstance(current, AgentPrincipalAuthority):
            raise IdentityGovernanceDeliveryError("principal_binding_unavailable")
        if current != authority:
            raise IdentityGovernanceStaleBinding("principal_binding_stale")
        exact = (
            mapping.tenant_sha256,
            mapping.principal_ref,
            mapping.authority_binding_sha256,
            mapping.authority_revision,
        )
        expected = (
            authority.tenant_sha256,
            authority.principal_ref,
            authority.authority_binding_sha256,
            authority.revision,
        )
        if exact != expected:
            raise IdentityGovernanceStaleBinding("capability_mapping_stale")
        required = _code(capability, "required capability")
        if required not in mapping.capabilities:
            raise IdentityGovernanceAuthorizationError("purview_capability_denied")
        return current

    def _current_asset(self, asset_id: str) -> PurviewAssetBinding:
        assert self._asset_resolver is not None
        try:
            current = self._asset_resolver(self._tenant_sha256, asset_id)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise IdentityGovernanceDeliveryError("purview_asset_binding_unavailable") from None
        if not isinstance(current, PurviewAssetBinding):
            raise IdentityGovernanceDeliveryError("purview_asset_binding_unavailable")
        if current.tenant_sha256 != self._tenant_sha256 or current.asset_id != asset_id:
            raise IdentityGovernanceBindingError("purview_asset_binding_mismatch")
        return current

    def prepare(
        self,
        authority: AgentPrincipalAuthority,
        mapping: IdentityCapabilityMapping,
        *,
        asset_id: str,
        classification_codes: Sequence[str],
        expected_binding_sha256: str,
        expected_source_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        store = self._require_available()
        self._current_authority(
            authority,
            mapping,
            "purview.classification.write",
        )
        asset = _pseudonymous(asset_id, "asset_id", prefixes={"ast"})
        desired = _codes(
            tuple(classification_codes),
            "classification_codes",
            maximum=_MAX_CLASSIFICATIONS,
        )
        expected_binding = _digest(expected_binding_sha256, "expected_binding_sha256")
        expected_revision = _positive_revision(expected_source_revision, "expected_source_revision")
        idempotency_sha = _idempotency_sha256(idempotency_key)
        intent_id = (
            "IGPW-"
            + _sha256(
                {
                    "tenant_sha256": self._tenant_sha256,
                    "principal_ref": authority.principal_ref,
                    "idempotency_sha256": idempotency_sha,
                }
            )[:32]
        )
        existing = store.get(intent_id)
        if existing is not None:
            replay_binding = (
                existing.get("schema"),
                existing.get("tenant_sha256"),
                existing.get("principal_ref"),
                existing.get("principal_binding_sha256"),
                existing.get("principal_revision"),
                existing.get("capability_mapping_sha256"),
                existing.get("capability_mapping_revision"),
                existing.get("capability_provider"),
                existing.get("federation_binding_sha256"),
                existing.get("asset_id"),
                existing.get("source_binding_sha256"),
                existing.get("source_revision"),
                tuple(existing.get("classification_codes") or ()),
                existing.get("idempotency_sha256"),
            )
            expected_replay = (
                PURVIEW_INTENT_SCHEMA,
                self._tenant_sha256,
                authority.principal_ref,
                authority.authority_binding_sha256,
                authority.revision,
                mapping.mapping_sha256,
                mapping.mapping_revision,
                mapping.provider,
                mapping.federation_binding_sha256,
                asset,
                expected_binding,
                expected_revision,
                desired,
                idempotency_sha,
            )
            if replay_binding != expected_replay:
                raise IdentityGovernanceIdempotencyConflict(
                    "purview_writeback_idempotency_conflict"
                )
            return _public(existing)
        current = self._current_asset(asset)
        if current.binding_sha256 != expected_binding or current.revision != expected_revision:
            raise IdentityGovernanceStaleBinding("purview_asset_binding_stale")
        if desired == current.classification_codes:
            raise ValueError("Purview writeback would not change classifications")
        payload = {
            "schema": PURVIEW_INTENT_SCHEMA,
            "tenant_sha256": self._tenant_sha256,
            "principal_ref": authority.principal_ref,
            "principal_binding_sha256": authority.authority_binding_sha256,
            "principal_revision": authority.revision,
            "capability_mapping_sha256": mapping.mapping_sha256,
            "capability_mapping_revision": mapping.mapping_revision,
            "capability_provider": mapping.provider,
            "federation_binding_sha256": mapping.federation_binding_sha256,
            "asset_id": asset,
            "source_binding_sha256": current.binding_sha256,
            "source_revision": current.revision,
            "classification_codes": desired,
            "previous_classification_codes": current.classification_codes,
            "idempotency_sha256": idempotency_sha,
        }
        request_sha = _sha256(payload)
        record = {
            "id": intent_id,
            **payload,
            "request_sha256": request_sha,
            "status": "prepared",
            "health": "healthy",
            "attempts": 0,
            "compensation_attempts": 0,
            "operation_claim": None,
            "last_error_type": None,
            "receipt": None,
            "compensation": None,
            "compensation_authorization": None,
            "provider_write_state": "not_attempted",
            "live_acceptance_verified": False,
        }
        try:
            saved = store.create(
                record,
                action="prepare_purview_writeback",
                actor=authority.principal_ref,
                max_records=10_000,
            )
        except RecordConflict as exc:
            concurrent = store.get(intent_id)
            if concurrent is None:
                raise IdentityGovernanceStateError("purview_writeback_admission_failed") from exc
            if concurrent.get("request_sha256") != request_sha:
                raise IdentityGovernanceIdempotencyConflict(
                    "purview_writeback_idempotency_conflict"
                ) from exc
            saved = concurrent
        return _public(saved)

    def _load_owned(
        self,
        store: IdentityGovernanceRecordStore,
        intent_id: str,
        authority: AgentPrincipalAuthority,
        mapping: IdentityCapabilityMapping,
        *,
        capability: str,
        expected_revision: int,
        require_prepared_mapping: bool = True,
    ) -> dict[str, Any]:
        self._current_authority(authority, mapping, capability)
        if not isinstance(intent_id, str) or not _RECORD_ID_RE.fullmatch(intent_id):
            raise ValueError("Purview intent id is invalid")
        record = store.get(intent_id)
        if record is None:
            raise IdentityGovernanceStateError("purview_writeback_intent_missing")
        if (
            record.get("schema") != PURVIEW_INTENT_SCHEMA
            or record.get("tenant_sha256") != self._tenant_sha256
            or record.get("principal_ref") != authority.principal_ref
            or record.get("principal_binding_sha256") != authority.authority_binding_sha256
            or record.get("principal_revision") != authority.revision
            or record.get("capability_provider") != mapping.provider
            or record.get("federation_binding_sha256") != mapping.federation_binding_sha256
        ):
            raise IdentityGovernanceBindingError("purview_writeback_binding_mismatch")
        if require_prepared_mapping and (
            record.get("capability_mapping_sha256") != mapping.mapping_sha256
            or record.get("capability_mapping_revision") != mapping.mapping_revision
        ):
            raise IdentityGovernanceBindingError("purview_writeback_mapping_mismatch")
        if _record_revision(record) != _positive_revision(expected_revision, "expected_revision"):
            raise IdentityGovernanceStaleBinding("purview_writeback_revision_stale")
        return record

    def get_intent(
        self,
        intent_id: str,
        authority: AgentPrincipalAuthority,
        mapping: IdentityCapabilityMapping,
    ) -> dict[str, Any] | None:
        """Read one owned intent so a crashed caller can resume by revision."""

        store = self._require_available()
        self._current_authority(
            authority,
            mapping,
            "purview.classification.write",
        )
        if not isinstance(intent_id, str) or not _RECORD_ID_RE.fullmatch(intent_id):
            raise ValueError("Purview intent id is invalid")
        record = store.get(intent_id)
        if record is None:
            return None
        if (
            record.get("schema") != PURVIEW_INTENT_SCHEMA
            or record.get("tenant_sha256") != self._tenant_sha256
            or record.get("principal_ref") != authority.principal_ref
            or record.get("principal_binding_sha256") != authority.authority_binding_sha256
            or record.get("principal_revision") != authority.revision
            or record.get("capability_mapping_sha256") != mapping.mapping_sha256
            or record.get("capability_mapping_revision") != mapping.mapping_revision
            or record.get("capability_provider") != mapping.provider
            or record.get("federation_binding_sha256") != mapping.federation_binding_sha256
        ):
            raise IdentityGovernanceBindingError("purview_writeback_binding_mismatch")
        _record_revision(record)
        return _public(record)

    def _update_intent(
        self,
        store: IdentityGovernanceRecordStore,
        record: Mapping[str, Any],
        mutate: Callable[[dict[str, Any]], None],
        *,
        action: str,
    ) -> dict[str, Any]:
        try:
            saved = store.update(
                str(record.get("id") or ""),
                mutate,
                expected_revision=_record_revision(record),
                action=action,
                actor=str(record.get("principal_ref") or "system:identity-governance"),
            )
        except RecordConflict as exc:
            raise IdentityGovernanceStaleBinding("purview_writeback_revision_stale") from exc
        if saved is None:
            raise IdentityGovernanceStateError("purview_writeback_intent_missing")
        return saved

    @staticmethod
    def _request(
        record: Mapping[str, Any], operation: Literal["apply", "compensate"]
    ) -> PurviewWritebackRequest:
        if operation == "apply":
            source_binding = record["source_binding_sha256"]
            source_revision = record["source_revision"]
            classifications = tuple(record["classification_codes"])
            previous = tuple(record["previous_classification_codes"])
            idempotency_sha = record["idempotency_sha256"]
        else:
            receipt = record.get("receipt")
            if not isinstance(receipt, Mapping):
                raise IdentityGovernanceStateError("purview_writeback_receipt_missing")
            source_binding = receipt["result_binding_sha256"]
            source_revision = receipt["result_revision"]
            classifications = tuple(record["previous_classification_codes"])
            previous = tuple(record["classification_codes"])
            idempotency_sha = _sha256(
                {
                    "apply_idempotency_sha256": record["idempotency_sha256"],
                    "operation": "compensate",
                }
            )
        material = {
            "schema": PURVIEW_REQUEST_SCHEMA,
            "operation": operation,
            "tenant_sha256": record["tenant_sha256"],
            "intent_id": record["id"],
            "principal_ref": record["principal_ref"],
            "asset_id": record["asset_id"],
            "source_binding_sha256": source_binding,
            "source_revision": source_revision,
            "classification_codes": classifications,
            "previous_classification_codes": previous,
            "idempotency_sha256": idempotency_sha,
        }
        return PurviewWritebackRequest(**material, request_sha256=_sha256(material))

    @staticmethod
    def _validate_ack(
        acknowledgement: object,
        request: PurviewWritebackRequest,
    ) -> PurviewWritebackAck:
        if not isinstance(acknowledgement, PurviewWritebackAck):
            raise ValueError("Purview acknowledgement is invalid")
        expected = (
            request.operation,
            request.tenant_sha256,
            request.intent_id,
            request.request_sha256,
            request.idempotency_sha256,
        )
        actual = (
            acknowledgement.operation,
            acknowledgement.tenant_sha256,
            acknowledgement.intent_id,
            acknowledgement.request_sha256,
            acknowledgement.idempotency_sha256,
        )
        if actual != expected:
            raise ValueError("Purview acknowledgement binding does not match")
        if (
            acknowledgement.result_revision <= request.source_revision
            or acknowledgement.result_binding_sha256 == request.source_binding_sha256
        ):
            raise ValueError("Purview acknowledgement did not advance the asset binding")
        return acknowledgement

    def _operation_in_progress(self, record: Mapping[str, Any], operation: str) -> bool:
        claim = record.get("operation_claim")
        if not isinstance(claim, Mapping) or claim.get("operation") != operation:
            return False
        expires_at = claim.get("expires_at")
        if (
            isinstance(expires_at, bool)
            or not isinstance(expires_at, (int, float))
            or not math.isfinite(float(expires_at))
        ):
            raise IdentityGovernanceStateError("purview_writeback_claim_invalid")
        return float(expires_at) > self._now()

    def _mark_stale(
        self,
        store: IdentityGovernanceRecordStore,
        record: Mapping[str, Any],
        *,
        operation: str,
    ) -> None:
        def stale(current: dict[str, Any]) -> None:
            current["status"] = "stale_binding"
            current["health"] = "degraded"
            current["last_error_type"] = "IdentityGovernanceStaleBinding"

        self._update_intent(
            store,
            record,
            stale,
            action=f"reject_stale_purview_{operation}",
        )

    def _run_transport(
        self,
        store: IdentityGovernanceRecordStore,
        record: Mapping[str, Any],
        *,
        operation: Literal["apply", "compensate"],
        authorization_mapping: IdentityCapabilityMapping | None = None,
    ) -> dict[str, Any]:
        attempt_field = "attempts" if operation == "apply" else "compensation_attempts"
        attempts = record.get(attempt_field)
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
            raise IdentityGovernanceStateError("purview_writeback_attempts_invalid")
        if attempts >= self._max_attempts:
            raise IdentityGovernanceDeliveryError("purview_writeback_attempts_exhausted")
        claim_status = "applying" if operation == "apply" else "compensating"

        def claim(current: dict[str, Any]) -> None:
            current["status"] = claim_status
            current[attempt_field] = attempts + 1
            current["provider_write_state"] = f"{operation}_in_progress"
            if operation == "compensate":
                if authorization_mapping is None:
                    raise IdentityGovernanceStateError("purview_compensation_authorization_missing")
                current["compensation_authorization"] = {
                    "provider": authorization_mapping.provider,
                    "federation_binding_sha256": (authorization_mapping.federation_binding_sha256),
                    "mapping_sha256": authorization_mapping.mapping_sha256,
                    "mapping_revision": authorization_mapping.mapping_revision,
                    "authorized_at": self._now(),
                }
            current["operation_claim"] = {
                "operation": operation,
                "expires_at": self._now() + self._operation_lease_seconds,
            }
            current["last_error_type"] = None

        claimed = self._update_intent(
            store,
            record,
            claim,
            action=f"claim_purview_{operation}",
        )
        request = self._request(claimed, operation)
        assert self._transport is not None
        try:
            raw_ack = (
                self._transport.apply(request)
                if operation == "apply"
                else self._transport.compensate(request)
            )
            acknowledgement = self._validate_ack(raw_ack, request)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            error_type = _safe_error_type(exc)
            failure_status = "failed" if operation == "apply" else "compensation_failed"

            def failed(current: dict[str, Any]) -> None:
                current["status"] = failure_status
                current["health"] = "degraded"
                current["last_error_type"] = error_type
                current["operation_claim"] = None
                current["provider_write_state"] = "unknown"

            self._update_intent(
                store,
                claimed,
                failed,
                action=f"record_purview_{operation}_failure",
            )
            raise IdentityGovernanceDeliveryError("purview_writeback_degraded") from None

        receipt = {
            "operation": operation,
            "request_sha256": acknowledgement.request_sha256,
            "idempotency_sha256": acknowledgement.idempotency_sha256,
            "result_binding_sha256": acknowledgement.result_binding_sha256,
            "result_revision": acknowledgement.result_revision,
            "receipt_sha256": acknowledgement.receipt_sha256,
            "accepted": True,
            "live_acceptance_verified": False,
        }

        def delivered(current: dict[str, Any]) -> None:
            if operation == "apply":
                current["receipt"] = receipt
                current["status"] = "applied"
            else:
                current["compensation"] = receipt
                current["status"] = "compensated"
            current["health"] = "healthy"
            current["last_error_type"] = None
            current["operation_claim"] = None
            current["provider_write_state"] = "applied" if operation == "apply" else "compensated"
            current["live_acceptance_verified"] = False

        return self._update_intent(
            store,
            claimed,
            delivered,
            action=f"record_purview_{operation}_receipt",
        )

    def execute(
        self,
        intent_id: str,
        authority: AgentPrincipalAuthority,
        mapping: IdentityCapabilityMapping,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        store = self._require_available()
        record = self._load_owned(
            store,
            intent_id,
            authority,
            mapping,
            capability="purview.classification.write",
            expected_revision=expected_revision,
        )
        status = record.get("status")
        if status == "applied":
            return _public(record)
        if status == "applying" and self._operation_in_progress(record, "apply"):
            raise IdentityGovernanceDeliveryError("purview_writeback_in_progress")
        if status not in {"prepared", "failed", "applying"}:
            raise IdentityGovernanceStateError("purview_writeback_state_invalid")
        if status != "applying":
            current = self._current_asset(str(record["asset_id"]))
            if current.binding_sha256 != record.get(
                "source_binding_sha256"
            ) or current.revision != record.get("source_revision"):
                self._mark_stale(store, record, operation="apply")
                raise IdentityGovernanceStaleBinding("purview_asset_binding_stale")
        return _public(self._run_transport(store, record, operation="apply"))

    def compensate(
        self,
        intent_id: str,
        authority: AgentPrincipalAuthority,
        mapping: IdentityCapabilityMapping,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        store = self._require_available()
        record = self._load_owned(
            store,
            intent_id,
            authority,
            mapping,
            capability="purview.classification.rollback",
            expected_revision=expected_revision,
            require_prepared_mapping=False,
        )
        status = record.get("status")
        if status == "compensated":
            return _public(record)
        if status == "compensating" and self._operation_in_progress(record, "compensate"):
            raise IdentityGovernanceDeliveryError("purview_compensation_in_progress")
        if status not in {"applied", "compensation_failed", "compensating"}:
            raise IdentityGovernanceStateError("purview_compensation_state_invalid")
        receipt = record.get("receipt")
        if not isinstance(receipt, Mapping):
            raise IdentityGovernanceStateError("purview_writeback_receipt_missing")
        if status != "compensating":
            current = self._current_asset(str(record["asset_id"]))
            if current.binding_sha256 != receipt.get(
                "result_binding_sha256"
            ) or current.revision != receipt.get("result_revision"):
                self._mark_stale(store, record, operation="compensation")
                raise IdentityGovernanceStaleBinding("purview_compensation_binding_stale")
        return _public(
            self._run_transport(
                store,
                record,
                operation="compensate",
                authorization_mapping=mapping,
            )
        )

    def status(self) -> dict[str, Any]:
        available = bool(
            self._enabled
            and callable(self._principal_resolver)
            and callable(self._asset_resolver)
            and self._transport is not None
            and callable(getattr(self._transport, "apply", None))
            and callable(getattr(self._transport, "compensate", None))
        )
        return {
            "schema": "lightwork.identity_governance.purview-writeback-status.v1",
            "enabled": self._enabled,
            "runtime_available": available,
            "default_mode": "off",
            "provider_writes_possible": available,
            "idempotency_bound": True,
            "tenant_principal_binding_required": True,
            "source_revision_required": True,
            "compensation_supported": True,
            "crash_recovery_idempotent": True,
            "raw_identity_retained": False,
            "secrets_retained": False,
            "live_acceptance_verified": False,
        }


__all__ = [
    "AgentPrincipalAuthority",
    "AgentPrincipalFederationBinding",
    "AgentPrincipalFederationObservation",
    "AgentPrincipalFederationTransport",
    "CapabilityRule",
    "ChangeDeliveryAck",
    "ConditionalAccessDecision",
    "ConditionalAccessDecisionEngine",
    "ConditionalAccessObservation",
    "ConditionalAccessObservationRequest",
    "ConditionalAccessObservationTransport",
    "ConditionalAccessObserver",
    "EntraAgentPrincipalFederationAdapter",
    "FederationObservationRequest",
    "GovernedPurviewWritebackService",
    "IdentityCapabilityMapper",
    "IdentityCapabilityMapping",
    "IdentityGovernanceAuthorizationError",
    "IdentityGovernanceBindingError",
    "IdentityGovernanceControlConfigError",
    "IdentityGovernanceControlError",
    "IdentityGovernanceDeliveryError",
    "IdentityGovernanceIdempotencyConflict",
    "IdentityGovernanceStaleBinding",
    "IdentityGovernanceStateError",
    "NormalizedIdentityChangeCoupler",
    "OktaAgentPrincipalFederationAdapter",
    "PurviewAssetBinding",
    "PurviewWritebackAck",
    "PurviewWritebackRequest",
    "PurviewWritebackTransport",
]
