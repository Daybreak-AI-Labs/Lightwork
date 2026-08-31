"""Provider-neutral governance contracts for managed-cloud AI agents.

The module normalizes AWS Bedrock, Azure AI Foundry, and Google Vertex agent
inventory into content-free references and digests.  It intentionally has no
provider SDK dependency and no field for credentials, prompts, provider
payloads, display names, URLs, or user identifiers.

Provider enforcement is an injected, default-off transport seam.  A verified
adapter response records only that the adapter accepted, rejected, or could
not yet accept an intent; it is not evidence that a provider-side write took
effect.  A later, revision-pinned inventory observation is required to observe
state drift or convergence.

The Databricks AI Security Framework support in this module contains only
versioned control identifiers and mappings.  It includes no licensed framework
text and neither review, attestation, nor activation is a certification claim.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, Protocol

CloudProvider = Literal["aws_bedrock", "azure_ai_foundry", "google_vertex"]
LifecycleState = Literal["active", "disabled", "draft", "unknown"]
ObservabilityKind = Literal[
    "agent_invocation",
    "data_access",
    "lifecycle_change",
    "model_call",
    "policy_signal",
    "tool_call",
]
PolicyDecision = Literal["allow_intent", "deny", "observe"]
EnforcementAction = Literal[
    "detach_data_source",
    "disable_agent",
    "quarantine_agent",
    "restrict_model",
    "restrict_tool",
]
AdapterDisposition = Literal["accepted", "no_change", "rejected", "retryable"]
IntentStatus = Literal[
    "adapter_accepted",
    "adapter_rejected",
    "adapter_reported_no_change",
    "prepared",
    "retryable",
]
ApplicabilityStatus = Literal["applicable", "needs_review", "not_applicable"]
ReviewDecision = Literal["approved", "rejected"]
ActivationAction = Literal["activate", "rollback"]

CLOUD_PROVIDERS: tuple[CloudProvider, ...] = (
    "aws_bedrock",
    "azure_ai_foundry",
    "google_vertex",
)

CLOUD_AGENT_LIMIT_NOTICE = (
    "Normalized observations may be incomplete and do not establish complete cloud-agent "
    "discovery. Adapter acceptance does not establish that a provider write occurred."
)
FRAMEWORK_LIMIT_NOTICE = (
    "Content-free control mapping only. Review, attestation, and activation do not reproduce "
    "licensed framework text and do not establish compliance or certification."
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_CODE_RE = re.compile(r"[a-z][a-z0-9_.:-]{0,127}\Z")
_CONTROL_ID_RE = re.compile(r"DBAI-[A-Z0-9][A-Z0-9.-]{0,63}\Z")
_VERSION_RE = re.compile(r"[0-9]{4}\.[0-9]{1,3}(?:\.[0-9]{1,3})?\Z")
_REF_RE = re.compile(r"[a-z]{3}_[0-9a-f]{32}\Z")
_SIGNATURE_RE = re.compile(r"[0-9a-f]{64,512}\Z")
_MAX_RELATIONSHIPS = 256
_MAX_CODES = 64
_MAX_PAGE_ITEMS = 500
_MAX_PAGES_PER_SCAN = 10_000
_MAX_AGENTS_PER_TENANT = 100_000
_MAX_EVENTS_PER_TENANT = 100_000
_MAX_IDEMPOTENCY_RECORDS = 200_000
_MAX_DISPATCH_ATTEMPTS = 8
_MAX_FRAMEWORK_MAPPINGS = 2_000
_MAX_EVIDENCE_LINKS = 128


class CloudAgentGovernanceError(RuntimeError):
    """Base error whose messages contain no provider payload or secret text."""


class CloudAgentGovernanceDisabled(CloudAgentGovernanceError):
    """The deployment did not explicitly enable this authority."""


class CloudAgentBindingError(CloudAgentGovernanceError):
    """A tenant, provider, agent, or adapter binding does not match."""


class CloudAgentStaleRevision(CloudAgentBindingError):
    """A compare-and-swap or drift pin is stale."""


class CloudAgentIdempotencyConflict(CloudAgentGovernanceError):
    """An idempotency identity was reused for different semantics."""


class CloudAgentIntegrityError(CloudAgentGovernanceError):
    """A normalized record or adapter receipt failed integrity validation."""


class CloudAgentAdapterError(CloudAgentGovernanceError):
    """An injected evaluator, verifier, or provider adapter failed closed."""


class CloudAgentRevoked(CloudAgentBindingError):
    """The exact provider binding is revoked."""


class CloudAgentStateError(CloudAgentGovernanceError):
    """The bounded authority state cannot safely accept an operation."""


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
        raise ValueError("cloud-agent value is not canonical JSON") from exc


def _sha256(value: object) -> str:
    material = value if isinstance(value, bytes) else _canonical(value)
    return hashlib.sha256(material).hexdigest()


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _provider(value: object) -> CloudProvider:
    if value not in CLOUD_PROVIDERS:
        raise ValueError("cloud-agent provider is unsupported")
    return value  # type: ignore[return-value]


def _reference(value: object, label: str, *, prefixes: set[str]) -> str:
    if not isinstance(value, str) or not _REF_RE.fullmatch(value):
        raise ValueError(f"{label} must be a pseudonymous reference")
    if value.split("_", 1)[0] not in prefixes:
        raise ValueError(f"{label} has an unsupported namespace")
    return value


def _code(value: object, label: str) -> str:
    if not isinstance(value, str) or not _CODE_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def _codes(value: Sequence[str], label: str, *, maximum: int = _MAX_CODES) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or len(value) > maximum:
        raise ValueError(f"{label} is invalid")
    selected = tuple(_code(item, label) for item in value)
    if selected != tuple(sorted(set(selected))):
        raise ValueError(f"{label} must be sorted and unique")
    return selected


def _refs(
    value: Sequence[str],
    label: str,
    *,
    prefixes: set[str],
    maximum: int = _MAX_RELATIONSHIPS,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or len(value) > maximum:
        raise ValueError(f"{label} is invalid")
    selected = tuple(_reference(item, label, prefixes=prefixes) for item in value)
    if selected != tuple(sorted(set(selected))):
        raise ValueError(f"{label} must be sorted and unique")
    return selected


def _revision(value: object, label: str = "revision", *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{label} must be a {qualifier} integer")
    return value


def _timestamp(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite positive timestamp")
    selected = float(value)
    if not math.isfinite(selected) or selected <= 0:
        raise ValueError(f"{label} must be a finite positive timestamp")
    return selected


def _idempotency_sha256(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("idempotency_key is invalid")
    selected = value.strip()
    if len(selected) < 8 or len(selected) > 256 or any(ord(char) < 32 for char in selected):
        raise ValueError("idempotency_key is invalid")
    return hashlib.sha256(selected.encode("utf-8")).hexdigest()


def _integrity(expected: str, payload: Mapping[str, object], label: str) -> str:
    selected = _digest(expected, label)
    if not secrets_compare_digest(selected, _sha256(payload)):
        raise CloudAgentIntegrityError(f"{label} does not match canonical content")
    return selected


def secrets_compare_digest(left: str, right: str) -> bool:
    """Keep digest comparison constant-time without importing provider tooling."""

    return hashlib.sha256(left.encode("ascii")).digest() == hashlib.sha256(
        right.encode("ascii")
    ).digest()


def _new_ref(prefix: str, material: Mapping[str, object]) -> str:
    return f"{prefix}_{_sha256(material)[:32]}"


@dataclass(frozen=True)
class TenantAuthority:
    """Opaque tenant authority supplied by an independently authenticated caller."""

    tenant_sha256: str
    authority_sha256: str
    revision: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_sha256", _digest(self.tenant_sha256, "tenant_sha256"))
        object.__setattr__(
            self, "authority_sha256", _digest(self.authority_sha256, "authority_sha256")
        )
        object.__setattr__(self, "revision", _revision(self.revision))


@dataclass(frozen=True)
class AgentRelationships:
    """Content-free agent-to-tool/model/data-source edges."""

    tool_refs: tuple[str, ...]
    model_refs: tuple[str, ...]
    data_source_refs: tuple[str, ...]
    relationship_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "tool_refs", _refs(self.tool_refs, "tool_refs", prefixes={"cat"})
        )
        object.__setattr__(
            self, "model_refs", _refs(self.model_refs, "model_refs", prefixes={"cam"})
        )
        object.__setattr__(
            self,
            "data_source_refs",
            _refs(self.data_source_refs, "data_source_refs", prefixes={"cad"}),
        )
        _integrity(self.relationship_sha256, self.payload(), "relationship_sha256")

    @classmethod
    def build(
        cls,
        *,
        tool_refs: Sequence[str] = (),
        model_refs: Sequence[str] = (),
        data_source_refs: Sequence[str] = (),
    ) -> AgentRelationships:
        tools = tuple(sorted(set(tool_refs)))
        models = tuple(sorted(set(model_refs)))
        sources = tuple(sorted(set(data_source_refs)))
        payload = {
            "schema": "lightwork.cloud-agent.relationships.v1",
            "tool_refs": list(tools),
            "model_refs": list(models),
            "data_source_refs": list(sources),
        }
        return cls(tools, models, sources, _sha256(payload))

    def payload(self) -> dict[str, object]:
        return {
            "schema": "lightwork.cloud-agent.relationships.v1",
            "tool_refs": list(self.tool_refs),
            "model_refs": list(self.model_refs),
            "data_source_refs": list(self.data_source_refs),
        }


@dataclass(frozen=True)
class NormalizedCloudAgent:
    """One provider-neutral inventory item with no provider payload content."""

    provider: CloudProvider
    agent_ref: str
    source_resource_sha256: str
    source_revision: int
    configuration_sha256: str
    lifecycle_state: LifecycleState
    relationships: AgentRelationships
    observed_at: float
    item_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _provider(self.provider))
        object.__setattr__(
            self, "agent_ref", _reference(self.agent_ref, "agent_ref", prefixes={"cag"})
        )
        object.__setattr__(
            self,
            "source_resource_sha256",
            _digest(self.source_resource_sha256, "source_resource_sha256"),
        )
        object.__setattr__(self, "source_revision", _revision(self.source_revision))
        object.__setattr__(
            self,
            "configuration_sha256",
            _digest(self.configuration_sha256, "configuration_sha256"),
        )
        if self.lifecycle_state not in {"active", "disabled", "draft", "unknown"}:
            raise ValueError("lifecycle_state is unsupported")
        if not isinstance(self.relationships, AgentRelationships):
            raise ValueError("relationships must be normalized")
        self.relationships.verify()
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at, "observed_at"))
        _integrity(self.item_sha256, self.payload(), "item_sha256")

    @classmethod
    def build(
        cls,
        *,
        provider: CloudProvider,
        agent_ref: str,
        source_resource_sha256: str,
        source_revision: int,
        configuration_sha256: str,
        lifecycle_state: LifecycleState,
        relationships: AgentRelationships,
        observed_at: float,
    ) -> NormalizedCloudAgent:
        payload = {
            "schema": "lightwork.cloud-agent.inventory-item.v1",
            "provider": provider,
            "agent_ref": agent_ref,
            "source_resource_sha256": source_resource_sha256,
            "source_revision": source_revision,
            "configuration_sha256": configuration_sha256,
            "lifecycle_state": lifecycle_state,
            "relationships_sha256": relationships.relationship_sha256,
            "observed_at": float(observed_at),
        }
        return cls(
            provider,
            agent_ref,
            source_resource_sha256,
            source_revision,
            configuration_sha256,
            lifecycle_state,
            relationships,
            observed_at,
            _sha256(payload),
        )

    def payload(self) -> dict[str, object]:
        return {
            "schema": "lightwork.cloud-agent.inventory-item.v1",
            "provider": self.provider,
            "agent_ref": self.agent_ref,
            "source_resource_sha256": self.source_resource_sha256,
            "source_revision": self.source_revision,
            "configuration_sha256": self.configuration_sha256,
            "lifecycle_state": self.lifecycle_state,
            "relationships_sha256": self.relationships.relationship_sha256,
            "observed_at": self.observed_at,
        }

    def verify(self) -> None:
        self.relationships.verify()
        _integrity(self.item_sha256, self.payload(), "item_sha256")


def _verify_relationships(self: AgentRelationships) -> None:
    _integrity(self.relationship_sha256, self.payload(), "relationship_sha256")


AgentRelationships.verify = _verify_relationships  # type: ignore[attr-defined]


@dataclass(frozen=True)
class InventoryPage:
    """One bounded page of a revision-pinned provider inventory scan."""

    provider: CloudProvider
    binding_ref: str
    binding_revision: int
    scan_ref: str
    scope_sha256: str
    source_snapshot_sha256: str
    page_index: int
    page_count: int
    continuation_sha256: str | None
    snapshot_complete: bool
    incomplete_reason_codes: tuple[str, ...]
    captured_at: float
    items: tuple[NormalizedCloudAgent, ...]
    page_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _provider(self.provider))
        object.__setattr__(
            self,
            "binding_ref",
            _reference(self.binding_ref, "binding_ref", prefixes={"cab"}),
        )
        object.__setattr__(self, "binding_revision", _revision(self.binding_revision))
        object.__setattr__(
            self, "scan_ref", _reference(self.scan_ref, "scan_ref", prefixes={"cas"})
        )
        object.__setattr__(self, "scope_sha256", _digest(self.scope_sha256, "scope_sha256"))
        object.__setattr__(
            self,
            "source_snapshot_sha256",
            _digest(self.source_snapshot_sha256, "source_snapshot_sha256"),
        )
        page_index = _revision(self.page_index, "page_index", allow_zero=True)
        page_count = _revision(self.page_count, "page_count")
        if page_count > _MAX_PAGES_PER_SCAN or page_index >= page_count:
            raise ValueError("page bounds are invalid")
        if self.continuation_sha256 is not None:
            _digest(self.continuation_sha256, "continuation_sha256")
        if page_index < page_count - 1 and self.continuation_sha256 is None:
            raise ValueError("non-final inventory page requires a continuation digest")
        if page_index == page_count - 1 and self.continuation_sha256 is not None:
            raise ValueError("final inventory page cannot retain a continuation digest")
        reasons = _codes(self.incomplete_reason_codes, "incomplete_reason_codes")
        object.__setattr__(self, "incomplete_reason_codes", reasons)
        if self.snapshot_complete and reasons:
            raise ValueError("complete inventory cannot include incomplete reasons")
        if not self.snapshot_complete and not reasons:
            raise ValueError("incomplete inventory requires a reason code")
        object.__setattr__(self, "captured_at", _timestamp(self.captured_at, "captured_at"))
        if len(self.items) > _MAX_PAGE_ITEMS:
            raise ValueError("inventory page exceeds item bound")
        refs: list[str] = []
        for item in self.items:
            if not isinstance(item, NormalizedCloudAgent) or item.provider != self.provider:
                raise ValueError("inventory item provider does not match page")
            item.verify()
            refs.append(item.agent_ref)
        if refs != sorted(set(refs)):
            raise ValueError("inventory items must be sorted and unique")
        _integrity(self.page_sha256, self.payload(), "page_sha256")

    @classmethod
    def build(
        cls,
        *,
        provider: CloudProvider,
        binding_ref: str,
        binding_revision: int,
        scan_ref: str,
        scope_sha256: str,
        source_snapshot_sha256: str,
        page_index: int,
        page_count: int,
        continuation_sha256: str | None,
        snapshot_complete: bool,
        incomplete_reason_codes: Sequence[str],
        captured_at: float,
        items: Sequence[NormalizedCloudAgent],
    ) -> InventoryPage:
        selected = tuple(sorted(items, key=lambda item: item.agent_ref))
        payload = {
            "schema": "lightwork.cloud-agent.inventory-page.v1",
            "provider": provider,
            "binding_ref": binding_ref,
            "binding_revision": binding_revision,
            "scan_ref": scan_ref,
            "scope_sha256": scope_sha256,
            "source_snapshot_sha256": source_snapshot_sha256,
            "page_index": page_index,
            "page_count": page_count,
            "continuation_sha256": continuation_sha256,
            "snapshot_complete": snapshot_complete,
            "incomplete_reason_codes": list(incomplete_reason_codes),
            "captured_at": float(captured_at),
            "item_sha256s": [item.item_sha256 for item in selected],
        }
        return cls(
            provider,
            binding_ref,
            binding_revision,
            scan_ref,
            scope_sha256,
            source_snapshot_sha256,
            page_index,
            page_count,
            continuation_sha256,
            snapshot_complete,
            tuple(incomplete_reason_codes),
            captured_at,
            selected,
            _sha256(payload),
        )

    def payload(self) -> dict[str, object]:
        return {
            "schema": "lightwork.cloud-agent.inventory-page.v1",
            "provider": self.provider,
            "binding_ref": self.binding_ref,
            "binding_revision": self.binding_revision,
            "scan_ref": self.scan_ref,
            "scope_sha256": self.scope_sha256,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "page_index": self.page_index,
            "page_count": self.page_count,
            "continuation_sha256": self.continuation_sha256,
            "snapshot_complete": self.snapshot_complete,
            "incomplete_reason_codes": list(self.incomplete_reason_codes),
            "captured_at": self.captured_at,
            "item_sha256s": [item.item_sha256 for item in self.items],
        }

    def verify(self) -> None:
        for item in self.items:
            item.verify()
        _integrity(self.page_sha256, self.payload(), "page_sha256")


@dataclass(frozen=True)
class ProviderBinding:
    """Credential-free identity of one injected provider adapter runtime."""

    tenant_sha256: str
    provider: CloudProvider
    binding_ref: str
    adapter_identity_sha256: str
    revision: int
    revoked: bool
    revocation_reason_code: str | None
    state_sha256: str

    def payload(self) -> dict[str, object]:
        return {
            "schema": "lightwork.cloud-agent.provider-binding.v1",
            "tenant_sha256": self.tenant_sha256,
            "provider": self.provider,
            "binding_ref": self.binding_ref,
            "adapter_identity_sha256": self.adapter_identity_sha256,
            "revision": self.revision,
            "revoked": self.revoked,
            "revocation_reason_code": self.revocation_reason_code,
        }

    def verify(self) -> None:
        _integrity(self.state_sha256, self.payload(), "binding state_sha256")


@dataclass(frozen=True)
class CloudAgentRecord:
    """Tenant-bound normalized inventory state and drift evidence."""

    tenant_sha256: str
    provider: CloudProvider
    agent: NormalizedCloudAgent
    record_revision: int
    inventory_revision: int
    first_seen_at: float
    last_seen_at: float
    previous_configuration_sha256: str | None
    drifted: bool
    state_sha256: str

    def payload(self) -> dict[str, object]:
        return {
            "schema": "lightwork.cloud-agent.record.v1",
            "tenant_sha256": self.tenant_sha256,
            "provider": self.provider,
            "item_sha256": self.agent.item_sha256,
            "record_revision": self.record_revision,
            "inventory_revision": self.inventory_revision,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "previous_configuration_sha256": self.previous_configuration_sha256,
            "drifted": self.drifted,
        }

    def verify(self) -> None:
        self.agent.verify()
        _integrity(self.state_sha256, self.payload(), "agent state_sha256")


@dataclass(frozen=True)
class InventoryMetadata:
    """Completeness and revision metadata for one provider snapshot."""

    tenant_sha256: str
    provider: CloudProvider
    inventory_revision: int
    scan_ref: str
    scope_sha256: str
    source_snapshot_sha256: str
    snapshot_complete: bool
    incomplete_reason_codes: tuple[str, ...]
    captured_at: float
    agent_count: int
    state_sha256: str

    def payload(self) -> dict[str, object]:
        return {
            "schema": "lightwork.cloud-agent.inventory-metadata.v1",
            "tenant_sha256": self.tenant_sha256,
            "provider": self.provider,
            "inventory_revision": self.inventory_revision,
            "scan_ref": self.scan_ref,
            "scope_sha256": self.scope_sha256,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "snapshot_complete": self.snapshot_complete,
            "incomplete_reason_codes": list(self.incomplete_reason_codes),
            "captured_at": self.captured_at,
            "agent_count": self.agent_count,
        }

    def verify(self) -> None:
        _integrity(self.state_sha256, self.payload(), "inventory state_sha256")


@dataclass(frozen=True)
class InventoryIngestReceipt:
    scan_ref: str
    page_index: int
    pages_received: int
    page_count: int
    finalized: bool
    inventory_revision: int
    snapshot_complete: bool
    incomplete_reason_codes: tuple[str, ...]
    receipt_sha256: str


@dataclass(frozen=True)
class InventorySlice:
    records: tuple[CloudAgentRecord, ...]
    offset: int
    next_offset: int | None
    limit: int
    total: int
    metadata: InventoryMetadata | None
    slice_sha256: str


@dataclass(frozen=True)
class CloudAgentObservabilityEvent:
    """Content-free, revision-pinned cloud-agent observability event."""

    provider: CloudProvider
    binding_ref: str
    binding_revision: int
    event_sha256: str
    trace_ref: str
    agent_ref: str
    agent_revision: int
    configuration_sha256: str
    kind: ObservabilityKind
    related_refs: tuple[str, ...]
    outcome_code: str
    attribute_codes: tuple[str, ...]
    observed_at: float
    integrity_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _provider(self.provider))
        object.__setattr__(
            self,
            "binding_ref",
            _reference(self.binding_ref, "binding_ref", prefixes={"cab"}),
        )
        object.__setattr__(self, "binding_revision", _revision(self.binding_revision))
        object.__setattr__(self, "event_sha256", _digest(self.event_sha256, "event_sha256"))
        object.__setattr__(
            self, "trace_ref", _reference(self.trace_ref, "trace_ref", prefixes={"trc"})
        )
        object.__setattr__(
            self, "agent_ref", _reference(self.agent_ref, "agent_ref", prefixes={"cag"})
        )
        object.__setattr__(self, "agent_revision", _revision(self.agent_revision))
        object.__setattr__(
            self,
            "configuration_sha256",
            _digest(self.configuration_sha256, "configuration_sha256"),
        )
        if self.kind not in {
            "agent_invocation",
            "data_access",
            "lifecycle_change",
            "model_call",
            "policy_signal",
            "tool_call",
        }:
            raise ValueError("observability kind is unsupported")
        object.__setattr__(
            self,
            "related_refs",
            _refs(
                self.related_refs,
                "related_refs",
                prefixes={"cag", "cad", "cam", "cat"},
            ),
        )
        object.__setattr__(self, "outcome_code", _code(self.outcome_code, "outcome_code"))
        object.__setattr__(
            self,
            "attribute_codes",
            _codes(self.attribute_codes, "attribute_codes"),
        )
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at, "observed_at"))
        _integrity(self.integrity_sha256, self.payload(), "event integrity_sha256")

    @classmethod
    def build(cls, **values: object) -> CloudAgentObservabilityEvent:
        payload = {
            "schema": "lightwork.cloud-agent.observability-event.v1",
            **values,
            "related_refs": list(values.get("related_refs", ())),
            "attribute_codes": list(values.get("attribute_codes", ())),
        }
        return cls(**values, integrity_sha256=_sha256(payload))  # type: ignore[arg-type]

    def payload(self) -> dict[str, object]:
        return {
            "schema": "lightwork.cloud-agent.observability-event.v1",
            "provider": self.provider,
            "binding_ref": self.binding_ref,
            "binding_revision": self.binding_revision,
            "event_sha256": self.event_sha256,
            "trace_ref": self.trace_ref,
            "agent_ref": self.agent_ref,
            "agent_revision": self.agent_revision,
            "configuration_sha256": self.configuration_sha256,
            "kind": self.kind,
            "related_refs": list(self.related_refs),
            "outcome_code": self.outcome_code,
            "attribute_codes": list(self.attribute_codes),
            "observed_at": self.observed_at,
        }

    def verify(self) -> None:
        _integrity(self.integrity_sha256, self.payload(), "event integrity_sha256")


@dataclass(frozen=True)
class ObservabilityReceipt:
    event_sha256: str
    stream_revision: int
    duplicate: bool
    receipt_sha256: str


@dataclass(frozen=True)
class PolicyEvaluationRequest:
    tenant_sha256: str
    provider: CloudProvider
    agent_ref: str
    agent_revision: int
    configuration_sha256: str
    inventory_revision: int
    relationship_sha256: str
    policy_ref: str
    policy_revision: int
    policy_sha256: str
    requested_action: EnforcementAction
    target_ref: str
    request_sha256: str


@dataclass(frozen=True)
class PolicyEvaluationResult:
    """Closed result returned by an injected provider-neutral evaluator."""

    decision: PolicyDecision
    action: EnforcementAction | None
    target_ref: str | None
    reason_codes: tuple[str, ...]


class CloudAgentPolicyEvaluator(Protocol):
    def evaluate(self, request: PolicyEvaluationRequest) -> PolicyEvaluationResult:
        """Evaluate only normalized, content-free policy context."""


@dataclass(frozen=True)
class PolicyEvaluationRecord:
    tenant_sha256: str
    evaluation_ref: str
    request: PolicyEvaluationRequest
    decision: PolicyDecision
    action: EnforcementAction | None
    target_ref: str | None
    reason_codes: tuple[str, ...]
    evaluated_at: float
    evaluation_sha256: str

    def payload(self) -> dict[str, object]:
        return {
            "schema": "lightwork.cloud-agent.policy-evaluation.v1",
            "tenant_sha256": self.tenant_sha256,
            "evaluation_ref": self.evaluation_ref,
            "request_sha256": self.request.request_sha256,
            "decision": self.decision,
            "action": self.action,
            "target_ref": self.target_ref,
            "reason_codes": list(self.reason_codes),
            "evaluated_at": self.evaluated_at,
        }

    def verify(self) -> None:
        _integrity(self.evaluation_sha256, self.payload(), "evaluation_sha256")


@dataclass(frozen=True)
class EnforcementIntent:
    tenant_sha256: str
    provider: CloudProvider
    intent_ref: str
    evaluation_ref: str
    evaluation_sha256: str
    binding_ref: str
    binding_revision: int
    agent_ref: str
    agent_revision: int
    configuration_sha256: str
    inventory_revision: int
    action: EnforcementAction
    target_ref: str
    status: IntentStatus
    revision: int
    attempts: int
    last_receipt_sha256: str | None
    immutable_sha256: str
    state_sha256: str

    def immutable_payload(self) -> dict[str, object]:
        return {
            "schema": "lightwork.cloud-agent.enforcement-intent.v1",
            "tenant_sha256": self.tenant_sha256,
            "provider": self.provider,
            "intent_ref": self.intent_ref,
            "evaluation_ref": self.evaluation_ref,
            "evaluation_sha256": self.evaluation_sha256,
            "binding_ref": self.binding_ref,
            "binding_revision": self.binding_revision,
            "agent_ref": self.agent_ref,
            "agent_revision": self.agent_revision,
            "configuration_sha256": self.configuration_sha256,
            "inventory_revision": self.inventory_revision,
            "action": self.action,
            "target_ref": self.target_ref,
        }

    def state_payload(self) -> dict[str, object]:
        return {
            **self.immutable_payload(),
            "immutable_sha256": self.immutable_sha256,
            "status": self.status,
            "revision": self.revision,
            "attempts": self.attempts,
            "last_receipt_sha256": self.last_receipt_sha256,
        }

    def verify(self) -> None:
        _integrity(self.immutable_sha256, self.immutable_payload(), "immutable_sha256")
        _integrity(self.state_sha256, self.state_payload(), "intent state_sha256")


@dataclass(frozen=True)
class EnforcementDispatchRequest:
    tenant_sha256: str
    provider: CloudProvider
    binding_ref: str
    binding_revision: int
    intent_ref: str
    intent_sha256: str
    action: EnforcementAction
    target_ref: str
    agent_ref: str
    agent_revision: int
    configuration_sha256: str
    attempt: int
    request_sha256: str

    def signing_payload(self) -> dict[str, object]:
        return {
            "schema": "lightwork.cloud-agent.enforcement-dispatch.v1",
            "tenant_sha256": self.tenant_sha256,
            "provider": self.provider,
            "binding_ref": self.binding_ref,
            "binding_revision": self.binding_revision,
            "intent_ref": self.intent_ref,
            "intent_sha256": self.intent_sha256,
            "action": self.action,
            "target_ref": self.target_ref,
            "agent_ref": self.agent_ref,
            "agent_revision": self.agent_revision,
            "configuration_sha256": self.configuration_sha256,
            "attempt": self.attempt,
        }


@dataclass(frozen=True)
class ProviderAdapterResult:
    """Signed transport disposition; deliberately cannot assert an applied write."""

    tenant_sha256: str
    provider: CloudProvider
    binding_ref: str
    binding_revision: int
    intent_ref: str
    request_sha256: str
    disposition: AdapterDisposition
    receipt_sha256: str
    observed_at: float
    signature: str
    integrity_sha256: str

    @classmethod
    def build(cls, **values: object) -> ProviderAdapterResult:
        payload = {
            "schema": "lightwork.cloud-agent.adapter-result.v1",
            **values,
        }
        return cls(**values, integrity_sha256=_sha256(payload))  # type: ignore[arg-type]

    def payload(self) -> dict[str, object]:
        return {
            "schema": "lightwork.cloud-agent.adapter-result.v1",
            "tenant_sha256": self.tenant_sha256,
            "provider": self.provider,
            "binding_ref": self.binding_ref,
            "binding_revision": self.binding_revision,
            "intent_ref": self.intent_ref,
            "request_sha256": self.request_sha256,
            "disposition": self.disposition,
            "receipt_sha256": self.receipt_sha256,
            "observed_at": self.observed_at,
            "signature": self.signature,
        }

    def verify(self) -> None:
        _digest(self.tenant_sha256, "tenant_sha256")
        _provider(self.provider)
        _reference(self.binding_ref, "binding_ref", prefixes={"cab"})
        _revision(self.binding_revision, "binding_revision")
        _reference(self.intent_ref, "intent_ref", prefixes={"cei"})
        _digest(self.request_sha256, "request_sha256")
        if self.disposition not in {"accepted", "no_change", "rejected", "retryable"}:
            raise CloudAgentIntegrityError("adapter disposition is unsupported")
        _digest(self.receipt_sha256, "receipt_sha256")
        _timestamp(self.observed_at, "observed_at")
        if not isinstance(self.signature, str) or not _SIGNATURE_RE.fullmatch(self.signature):
            raise CloudAgentIntegrityError("adapter signature encoding is invalid")
        _integrity(self.integrity_sha256, self.payload(), "adapter integrity_sha256")

    def signed_payload(self) -> dict[str, object]:
        payload = self.payload()
        payload.pop("signature")
        return payload


class CloudAgentEnforcementAdapter(Protocol):
    def submit(self, request: EnforcementDispatchRequest) -> ProviderAdapterResult:
        """Submit an idempotent intent without returning provider response content."""


AdapterResultVerifier = Callable[[ProviderBinding, ProviderAdapterResult], bool]


@dataclass(frozen=True)
class EnforcementDispatchReceipt:
    intent: EnforcementIntent
    adapter_result: ProviderAdapterResult
    provider_write_verified: Literal[False] = False
    limit_notice: str = CLOUD_AGENT_LIMIT_NOTICE


@dataclass(frozen=True)
class FrameworkEvidenceLink:
    """Content-free link from a mapping decision to an evidence record."""

    evidence_ref: str
    evidence_sha256: str
    relation_code: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_ref",
            _reference(self.evidence_ref, "evidence_ref", prefixes={"evd"}),
        )
        object.__setattr__(
            self,
            "evidence_sha256",
            _digest(self.evidence_sha256, "evidence_sha256"),
        )
        object.__setattr__(
            self, "relation_code", _code(self.relation_code, "relation_code")
        )

    def payload(self) -> dict[str, str]:
        return {
            "evidence_ref": self.evidence_ref,
            "evidence_sha256": self.evidence_sha256,
            "relation_code": self.relation_code,
        }


@dataclass(frozen=True)
class FrameworkControlMapping:
    """Identifiers and mappings only; no framework prose is accepted."""

    control_id: str
    lightwork_control_ids: tuple[str, ...]
    applicability_tags: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.control_id, str) or not _CONTROL_ID_RE.fullmatch(self.control_id):
            raise ValueError("control_id must be a content-free Databricks control identifier")
        object.__setattr__(
            self,
            "lightwork_control_ids",
            _codes(self.lightwork_control_ids, "lightwork_control_ids"),
        )
        object.__setattr__(
            self,
            "applicability_tags",
            _codes(self.applicability_tags, "applicability_tags"),
        )

    def payload(self) -> dict[str, object]:
        return {
            "control_id": self.control_id,
            "lightwork_control_ids": list(self.lightwork_control_ids),
            "applicability_tags": list(self.applicability_tags),
        }


@dataclass(frozen=True)
class DatabricksAISecurityFrameworkPack:
    """Immutable, content-free Databricks framework mapping artifact."""

    version: str
    source_revision_sha256: str
    mappings: tuple[FrameworkControlMapping, ...]
    pack_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not _VERSION_RE.fullmatch(self.version):
            raise ValueError("framework version is invalid")
        object.__setattr__(
            self,
            "source_revision_sha256",
            _digest(self.source_revision_sha256, "source_revision_sha256"),
        )
        if not self.mappings or len(self.mappings) > _MAX_FRAMEWORK_MAPPINGS:
            raise ValueError("framework mappings are empty or over bound")
        ids = [mapping.control_id for mapping in self.mappings]
        if ids != sorted(set(ids)):
            raise ValueError("framework mappings must be sorted and unique")
        _integrity(self.pack_sha256, self.payload(), "pack_sha256")

    @classmethod
    def build(
        cls,
        *,
        version: str,
        source_revision_sha256: str,
        mappings: Sequence[FrameworkControlMapping],
    ) -> DatabricksAISecurityFrameworkPack:
        selected = tuple(sorted(mappings, key=lambda mapping: mapping.control_id))
        payload = {
            "schema": "lightwork.databricks-ai-security-framework-map.v1",
            "framework_id": "databricks_ai_security_framework",
            "version": version,
            "source_revision_sha256": source_revision_sha256,
            "mappings": [mapping.payload() for mapping in selected],
        }
        return cls(version, source_revision_sha256, selected, _sha256(payload))

    def payload(self) -> dict[str, object]:
        return {
            "schema": "lightwork.databricks-ai-security-framework-map.v1",
            "framework_id": "databricks_ai_security_framework",
            "version": self.version,
            "source_revision_sha256": self.source_revision_sha256,
            "mappings": [mapping.payload() for mapping in self.mappings],
        }

    def verify(self) -> None:
        _integrity(self.pack_sha256, self.payload(), "pack_sha256")


@dataclass(frozen=True)
class FrameworkApplicability:
    control_id: str
    workload_ref: str
    status: ApplicabilityStatus
    reason_code: str
    evidence_links: tuple[FrameworkEvidenceLink, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.control_id, str) or not _CONTROL_ID_RE.fullmatch(self.control_id):
            raise ValueError("applicability control_id is invalid")
        object.__setattr__(
            self,
            "workload_ref",
            _reference(self.workload_ref, "workload_ref", prefixes={"wrk"}),
        )
        if self.status not in {"applicable", "needs_review", "not_applicable"}:
            raise ValueError("applicability status is unsupported")
        object.__setattr__(self, "reason_code", _code(self.reason_code, "reason_code"))
        if len(self.evidence_links) > _MAX_EVIDENCE_LINKS:
            raise ValueError("applicability evidence exceeds bound")
        keys = [(link.evidence_ref, link.relation_code) for link in self.evidence_links]
        if keys != sorted(set(keys)):
            raise ValueError("applicability evidence links must be sorted and unique")

    def payload(self) -> dict[str, object]:
        return {
            "control_id": self.control_id,
            "workload_ref": self.workload_ref,
            "status": self.status,
            "reason_code": self.reason_code,
            "evidence_links": [link.payload() for link in self.evidence_links],
        }


@dataclass(frozen=True)
class FrameworkReview:
    tenant_sha256: str
    review_ref: str
    pack_version: str
    pack_sha256: str
    reviewer_ref: str
    decision: ReviewDecision
    applicability: tuple[FrameworkApplicability, ...]
    reviewed_at: float
    revision: int
    review_sha256: str

    def payload(self) -> dict[str, object]:
        return {
            "schema": "lightwork.databricks-framework-review.v1",
            "tenant_sha256": self.tenant_sha256,
            "review_ref": self.review_ref,
            "pack_version": self.pack_version,
            "pack_sha256": self.pack_sha256,
            "reviewer_ref": self.reviewer_ref,
            "decision": self.decision,
            "applicability": [item.payload() for item in self.applicability],
            "reviewed_at": self.reviewed_at,
            "revision": self.revision,
        }

    def verify(self) -> None:
        _integrity(self.review_sha256, self.payload(), "review_sha256")


@dataclass(frozen=True)
class FrameworkAttestation:
    tenant_sha256: str
    attestation_ref: str
    review_ref: str
    review_sha256: str
    attestor_ref: str
    evidence_links: tuple[FrameworkEvidenceLink, ...]
    attested_at: float
    revision: int
    attestation_sha256: str
    certification_claim: Literal[False] = False
    limit_notice: str = FRAMEWORK_LIMIT_NOTICE

    def payload(self) -> dict[str, object]:
        return {
            "schema": "lightwork.databricks-framework-attestation.v1",
            "tenant_sha256": self.tenant_sha256,
            "attestation_ref": self.attestation_ref,
            "review_ref": self.review_ref,
            "review_sha256": self.review_sha256,
            "attestor_ref": self.attestor_ref,
            "evidence_links": [link.payload() for link in self.evidence_links],
            "attested_at": self.attested_at,
            "revision": self.revision,
            "certification_claim": False,
            "limit_notice": FRAMEWORK_LIMIT_NOTICE,
        }

    def verify(self) -> None:
        _integrity(self.attestation_sha256, self.payload(), "attestation_sha256")


@dataclass(frozen=True)
class FrameworkActivation:
    tenant_sha256: str
    version: str
    pack_sha256: str
    review_ref: str
    review_sha256: str
    attestation_ref: str
    attestation_sha256: str
    prior_version: str | None
    action: ActivationAction
    activated_at: float
    revision: int
    activation_sha256: str
    certification_claim: Literal[False] = False
    limit_notice: str = FRAMEWORK_LIMIT_NOTICE

    def payload(self) -> dict[str, object]:
        return {
            "schema": "lightwork.databricks-framework-activation.v1",
            "tenant_sha256": self.tenant_sha256,
            "version": self.version,
            "pack_sha256": self.pack_sha256,
            "review_ref": self.review_ref,
            "review_sha256": self.review_sha256,
            "attestation_ref": self.attestation_ref,
            "attestation_sha256": self.attestation_sha256,
            "prior_version": self.prior_version,
            "action": self.action,
            "activated_at": self.activated_at,
            "revision": self.revision,
            "certification_claim": False,
            "limit_notice": FRAMEWORK_LIMIT_NOTICE,
        }

    def verify(self) -> None:
        _integrity(self.activation_sha256, self.payload(), "activation_sha256")


@dataclass
class _ScanState:
    page_count: int
    metadata_sha256: str
    pages: dict[int, InventoryPage] = field(default_factory=dict)


@dataclass(frozen=True)
class _IdempotencyEntry:
    semantic_sha256: str
    result: object


@dataclass
class _AdapterRuntime:
    binding: ProviderBinding
    adapter: CloudAgentEnforcementAdapter
    verifier: AdapterResultVerifier


@dataclass
class _TenantState:
    authority: TenantAuthority
    adapters: dict[CloudProvider, _AdapterRuntime] = field(default_factory=dict)
    inventories: dict[CloudProvider, dict[str, CloudAgentRecord]] = field(default_factory=dict)
    inventory_metadata: dict[CloudProvider, InventoryMetadata] = field(default_factory=dict)
    scans: dict[tuple[CloudProvider, str], _ScanState] = field(default_factory=dict)
    events: dict[str, CloudAgentObservabilityEvent] = field(default_factory=dict)
    event_stream_revision: int = 0
    evaluations: dict[str, PolicyEvaluationRecord] = field(default_factory=dict)
    intents: dict[str, EnforcementIntent] = field(default_factory=dict)
    packs: dict[str, DatabricksAISecurityFrameworkPack] = field(default_factory=dict)
    framework_catalog_revision: int = 0
    reviews: dict[str, FrameworkReview] = field(default_factory=dict)
    attestations: dict[str, FrameworkAttestation] = field(default_factory=dict)
    activation: FrameworkActivation | None = None
    idempotency: dict[tuple[str, str], _IdempotencyEntry] = field(default_factory=dict)


class CloudAgentGovernanceAuthority:
    """Thread-safe, bounded reference authority with injected external seams."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        policy_evaluator: CloudAgentPolicyEvaluator | None = None,
        clock: Callable[[], float],
        max_tenants: int = 1_000,
    ) -> None:
        self._enabled = enabled is True
        self._policy_evaluator = policy_evaluator
        self._clock = clock
        self._max_tenants = _revision(max_tenants, "max_tenants")
        self._tenants: dict[str, _TenantState] = {}
        self._agent_tenants: dict[str, str] = {}
        self._lock = threading.RLock()

    def enroll_tenant(self, authority: TenantAuthority, *, idempotency_key: str) -> None:
        self._require_enabled()
        key = _idempotency_sha256(idempotency_key)
        semantic = _sha256(
            {
                "operation": "enroll_tenant",
                "tenant_sha256": authority.tenant_sha256,
                "authority_sha256": authority.authority_sha256,
                "revision": authority.revision,
            }
        )
        with self._lock:
            existing = self._tenants.get(authority.tenant_sha256)
            if existing is not None:
                entry = existing.idempotency.get(("enroll_tenant", key))
                if entry is not None and secrets_compare_digest(entry.semantic_sha256, semantic):
                    return
                raise CloudAgentBindingError("tenant authority is already enrolled")
            if len(self._tenants) >= self._max_tenants:
                raise CloudAgentStateError("tenant authority bound is exhausted")
            state = _TenantState(authority=authority)
            state.idempotency[("enroll_tenant", key)] = _IdempotencyEntry(semantic, None)
            self._tenants[authority.tenant_sha256] = state

    def bind_provider(
        self,
        authority: TenantAuthority,
        *,
        provider: CloudProvider,
        binding_ref: str,
        adapter_identity_sha256: str,
        adapter: CloudAgentEnforcementAdapter,
        verifier: AdapterResultVerifier,
        expected_revision: int,
        idempotency_key: str,
    ) -> ProviderBinding:
        self._require_enabled()
        selected_provider = _provider(provider)
        selected_ref = _reference(binding_ref, "binding_ref", prefixes={"cab"})
        adapter_digest = _digest(adapter_identity_sha256, "adapter_identity_sha256")
        expected = _revision(expected_revision, "expected_revision", allow_zero=True)
        if not callable(getattr(adapter, "submit", None)) or not callable(verifier):
            raise ValueError("provider adapter and independent verifier are required")
        semantic = {
            "provider": selected_provider,
            "binding_ref": selected_ref,
            "adapter_identity_sha256": adapter_digest,
            "expected_revision": expected,
        }
        with self._lock:
            state = self._tenant(authority)
            replay = self._replay(state, "bind_provider", idempotency_key, semantic)
            if replay is not None:
                return replay  # type: ignore[return-value]
            current = state.adapters.get(selected_provider)
            current_revision = 0 if current is None else current.binding.revision
            if current_revision != expected:
                raise CloudAgentStaleRevision("provider binding revision is stale")
            revision = current_revision + 1
            payload = {
                "schema": "lightwork.cloud-agent.provider-binding.v1",
                "tenant_sha256": authority.tenant_sha256,
                "provider": selected_provider,
                "binding_ref": selected_ref,
                "adapter_identity_sha256": adapter_digest,
                "revision": revision,
                "revoked": False,
                "revocation_reason_code": None,
            }
            binding = ProviderBinding(
                authority.tenant_sha256,
                selected_provider,
                selected_ref,
                adapter_digest,
                revision,
                False,
                None,
                _sha256(payload),
            )
            state.adapters[selected_provider] = _AdapterRuntime(binding, adapter, verifier)
            self._remember(state, "bind_provider", idempotency_key, semantic, binding)
            return binding

    def revoke_provider_binding(
        self,
        authority: TenantAuthority,
        *,
        provider: CloudProvider,
        expected_revision: int,
        reason_code: str,
        idempotency_key: str,
    ) -> ProviderBinding:
        selected_provider = _provider(provider)
        expected = _revision(expected_revision, "expected_revision")
        reason = _code(reason_code, "reason_code")
        semantic = {
            "provider": selected_provider,
            "expected_revision": expected,
            "reason_code": reason,
        }
        with self._lock:
            state = self._tenant(authority)
            replay = self._replay(state, "revoke_provider", idempotency_key, semantic)
            if replay is not None:
                return replay  # type: ignore[return-value]
            runtime = self._runtime(state, selected_provider)
            if runtime.binding.revision != expected:
                raise CloudAgentStaleRevision("provider binding revision is stale")
            if runtime.binding.revoked:
                raise CloudAgentRevoked("provider binding is already revoked")
            payload = {
                **runtime.binding.payload(),
                "revision": expected + 1,
                "revoked": True,
                "revocation_reason_code": reason,
            }
            binding = ProviderBinding(
                authority.tenant_sha256,
                selected_provider,
                runtime.binding.binding_ref,
                runtime.binding.adapter_identity_sha256,
                expected + 1,
                True,
                reason,
                _sha256(payload),
            )
            runtime.binding = binding
            self._remember(state, "revoke_provider", idempotency_key, semantic, binding)
            return binding

    def ingest_inventory_page(
        self,
        authority: TenantAuthority,
        page: InventoryPage,
        *,
        expected_inventory_revision: int,
        idempotency_key: str,
    ) -> InventoryIngestReceipt:
        expected = _revision(
            expected_inventory_revision, "expected_inventory_revision", allow_zero=True
        )
        page.verify()
        semantic = {
            "page_sha256": page.page_sha256,
            "expected_inventory_revision": expected,
        }
        with self._lock:
            state = self._tenant(authority)
            replay = self._replay(state, "ingest_inventory_page", idempotency_key, semantic)
            if replay is not None:
                return replay  # type: ignore[return-value]
            runtime = self._active_runtime(state, page.provider)
            self._assert_page_binding(page, runtime.binding)
            current_revision = self._inventory_revision(state, page.provider)
            if current_revision != expected:
                raise CloudAgentStaleRevision("inventory revision is stale")
            scan_key = (page.provider, page.scan_ref)
            scan = state.scans.get(scan_key)
            metadata_sha256 = self._scan_metadata_sha256(page)
            if scan is None:
                scan = _ScanState(page.page_count, metadata_sha256)
                state.scans[scan_key] = scan
            elif scan.page_count != page.page_count or not secrets_compare_digest(
                scan.metadata_sha256, metadata_sha256
            ):
                raise CloudAgentIdempotencyConflict("scan reference metadata collision")
            prior_page = scan.pages.get(page.page_index)
            if prior_page is not None and not secrets_compare_digest(
                prior_page.page_sha256, page.page_sha256
            ):
                raise CloudAgentIdempotencyConflict("scan page identity collision")
            scan.pages[page.page_index] = page
            finalized = len(scan.pages) == scan.page_count
            inventory_revision = current_revision
            if finalized:
                inventory_revision = self._finalize_scan(state, page.provider, scan)
                del state.scans[scan_key]
            receipt_payload = {
                "schema": "lightwork.cloud-agent.inventory-ingest-receipt.v1",
                "scan_ref": page.scan_ref,
                "page_index": page.page_index,
                "pages_received": len(scan.pages),
                "page_count": page.page_count,
                "finalized": finalized,
                "inventory_revision": inventory_revision,
                "snapshot_complete": page.snapshot_complete,
                "incomplete_reason_codes": list(page.incomplete_reason_codes),
            }
            receipt = InventoryIngestReceipt(
                page.scan_ref,
                page.page_index,
                len(scan.pages),
                page.page_count,
                finalized,
                inventory_revision,
                page.snapshot_complete,
                page.incomplete_reason_codes,
                _sha256(receipt_payload),
            )
            self._remember(state, "ingest_inventory_page", idempotency_key, semantic, receipt)
            return receipt

    def list_inventory(
        self,
        authority: TenantAuthority,
        *,
        provider: CloudProvider,
        offset: int = 0,
        limit: int = 100,
    ) -> InventorySlice:
        selected_provider = _provider(provider)
        selected_offset = _revision(offset, "offset", allow_zero=True)
        selected_limit = _revision(limit, "limit")
        if selected_limit > _MAX_PAGE_ITEMS:
            raise ValueError("limit exceeds pagination bound")
        with self._lock:
            state = self._tenant(authority)
            records = tuple(
                sorted(
                    state.inventories.get(selected_provider, {}).values(),
                    key=lambda record: record.agent.agent_ref,
                )
            )
            page = records[selected_offset : selected_offset + selected_limit]
            next_offset = (
                selected_offset + len(page)
                if selected_offset + len(page) < len(records)
                else None
            )
            metadata = state.inventory_metadata.get(selected_provider)
            payload = {
                "schema": "lightwork.cloud-agent.inventory-slice.v1",
                "tenant_sha256": authority.tenant_sha256,
                "provider": selected_provider,
                "offset": selected_offset,
                "next_offset": next_offset,
                "limit": selected_limit,
                "total": len(records),
                "record_sha256s": [record.state_sha256 for record in page],
                "metadata_sha256": None if metadata is None else metadata.state_sha256,
            }
            return InventorySlice(
                page,
                selected_offset,
                next_offset,
                selected_limit,
                len(records),
                metadata,
                _sha256(payload),
            )

    def ingest_observability_event(
        self,
        authority: TenantAuthority,
        event: CloudAgentObservabilityEvent,
        *,
        expected_stream_revision: int,
        idempotency_key: str,
    ) -> ObservabilityReceipt:
        expected = _revision(
            expected_stream_revision, "expected_stream_revision", allow_zero=True
        )
        event.verify()
        semantic = {
            "event_integrity_sha256": event.integrity_sha256,
            "expected_stream_revision": expected,
        }
        with self._lock:
            state = self._tenant(authority)
            replay = self._replay(state, "ingest_observability", idempotency_key, semantic)
            if replay is not None:
                return replace(replay, duplicate=True)  # type: ignore[arg-type]
            runtime = self._active_runtime(state, event.provider)
            self._assert_event_binding(event, runtime.binding)
            if state.event_stream_revision != expected:
                raise CloudAgentStaleRevision("observability stream revision is stale")
            record = self._agent(state, authority.tenant_sha256, event.agent_ref)
            self._assert_agent_pins(
                record,
                agent_revision=event.agent_revision,
                configuration_sha256=event.configuration_sha256,
            )
            prior = state.events.get(event.event_sha256)
            if prior is not None:
                if not secrets_compare_digest(prior.integrity_sha256, event.integrity_sha256):
                    raise CloudAgentIdempotencyConflict("observability event digest collision")
                duplicate = True
            else:
                if len(state.events) >= _MAX_EVENTS_PER_TENANT:
                    raise CloudAgentStateError("observability event bound is exhausted")
                state.events[event.event_sha256] = event
                state.event_stream_revision += 1
                duplicate = False
            payload = {
                "schema": "lightwork.cloud-agent.observability-receipt.v1",
                "event_sha256": event.event_sha256,
                "stream_revision": state.event_stream_revision,
                "duplicate": duplicate,
            }
            receipt = ObservabilityReceipt(
                event.event_sha256,
                state.event_stream_revision,
                duplicate,
                _sha256(payload),
            )
            self._remember(state, "ingest_observability", idempotency_key, semantic, receipt)
            return receipt

    def evaluate_policy(
        self,
        authority: TenantAuthority,
        *,
        agent_ref: str,
        expected_agent_revision: int,
        policy_ref: str,
        policy_revision: int,
        policy_sha256: str,
        requested_action: EnforcementAction,
        target_ref: str,
        idempotency_key: str,
    ) -> PolicyEvaluationRecord:
        if self._policy_evaluator is None:
            raise CloudAgentAdapterError("policy evaluator is not configured")
        selected_agent = _reference(agent_ref, "agent_ref", prefixes={"cag"})
        expected = _revision(expected_agent_revision, "expected_agent_revision")
        selected_policy = _reference(policy_ref, "policy_ref", prefixes={"pol"})
        selected_policy_revision = _revision(policy_revision, "policy_revision")
        selected_policy_sha = _digest(policy_sha256, "policy_sha256")
        action = self._action(requested_action)
        target = _reference(
            target_ref, "target_ref", prefixes={"cag", "cad", "cam", "cat"}
        )
        semantic = {
            "agent_ref": selected_agent,
            "expected_agent_revision": expected,
            "policy_ref": selected_policy,
            "policy_revision": selected_policy_revision,
            "policy_sha256": selected_policy_sha,
            "requested_action": action,
            "target_ref": target,
        }
        with self._lock:
            state = self._tenant(authority)
            replay = self._replay(state, "evaluate_policy", idempotency_key, semantic)
            if replay is not None:
                return replay  # type: ignore[return-value]
            record = self._agent(state, authority.tenant_sha256, selected_agent)
            self._assert_agent_pins(record, agent_revision=expected)
            metadata = state.inventory_metadata.get(record.provider)
            if metadata is None:
                raise CloudAgentStateError("provider inventory metadata is unavailable")
            self._assert_target(record.agent, action, target)
            request_payload = {
                "schema": "lightwork.cloud-agent.policy-evaluation-request.v1",
                "tenant_sha256": authority.tenant_sha256,
                "provider": record.provider,
                "agent_ref": selected_agent,
                "agent_revision": expected,
                "configuration_sha256": record.agent.configuration_sha256,
                "inventory_revision": metadata.inventory_revision,
                "relationship_sha256": record.agent.relationships.relationship_sha256,
                "policy_ref": selected_policy,
                "policy_revision": selected_policy_revision,
                "policy_sha256": selected_policy_sha,
                "requested_action": action,
                "target_ref": target,
            }
            request = PolicyEvaluationRequest(
                authority.tenant_sha256,
                record.provider,
                selected_agent,
                expected,
                record.agent.configuration_sha256,
                metadata.inventory_revision,
                record.agent.relationships.relationship_sha256,
                selected_policy,
                selected_policy_revision,
                selected_policy_sha,
                action,
                target,
                _sha256(request_payload),
            )
            try:
                result = self._policy_evaluator.evaluate(request)
            except Exception as exc:
                raise CloudAgentAdapterError("policy evaluator failed closed") from exc
            decision, selected_action, selected_target, reasons = self._validate_evaluation_result(
                result, request
            )
            evaluated_at = _timestamp(self._clock(), "clock")
            evaluation_ref = _new_ref(
                "cpe",
                {
                    "tenant_sha256": authority.tenant_sha256,
                    "request_sha256": request.request_sha256,
                    "idempotency_sha256": _idempotency_sha256(idempotency_key),
                },
            )
            payload = {
                "schema": "lightwork.cloud-agent.policy-evaluation.v1",
                "tenant_sha256": authority.tenant_sha256,
                "evaluation_ref": evaluation_ref,
                "request_sha256": request.request_sha256,
                "decision": decision,
                "action": selected_action,
                "target_ref": selected_target,
                "reason_codes": list(reasons),
                "evaluated_at": evaluated_at,
            }
            evaluation = PolicyEvaluationRecord(
                authority.tenant_sha256,
                evaluation_ref,
                request,
                decision,
                selected_action,
                selected_target,
                reasons,
                evaluated_at,
                _sha256(payload),
            )
            state.evaluations[evaluation_ref] = evaluation
            self._remember(state, "evaluate_policy", idempotency_key, semantic, evaluation)
            return evaluation

    def prepare_enforcement_intent(
        self,
        authority: TenantAuthority,
        *,
        evaluation_ref: str,
        idempotency_key: str,
    ) -> EnforcementIntent:
        selected_ref = _reference(evaluation_ref, "evaluation_ref", prefixes={"cpe"})
        semantic = {"evaluation_ref": selected_ref}
        with self._lock:
            state = self._tenant(authority)
            replay = self._replay(state, "prepare_enforcement", idempotency_key, semantic)
            if replay is not None:
                return replay  # type: ignore[return-value]
            evaluation = self._bound_record(
                state.evaluations, selected_ref, authority.tenant_sha256, "evaluation"
            )
            if not isinstance(evaluation, PolicyEvaluationRecord):
                raise CloudAgentStateError("evaluation state is invalid")
            evaluation.verify()
            if evaluation.decision != "allow_intent":
                raise CloudAgentStateError("policy evaluation does not allow an intent")
            if evaluation.action is None or evaluation.target_ref is None:
                raise CloudAgentStateError("allowed evaluation is missing action binding")
            record = self._agent(state, authority.tenant_sha256, evaluation.request.agent_ref)
            self._assert_agent_pins(
                record,
                agent_revision=evaluation.request.agent_revision,
                configuration_sha256=evaluation.request.configuration_sha256,
                inventory_revision=evaluation.request.inventory_revision,
            )
            runtime = self._active_runtime(state, record.provider)
            intent_ref = _new_ref(
                "cei",
                {
                    "tenant_sha256": authority.tenant_sha256,
                    "evaluation_sha256": evaluation.evaluation_sha256,
                    "idempotency_sha256": _idempotency_sha256(idempotency_key),
                },
            )
            provisional = EnforcementIntent(
                authority.tenant_sha256,
                record.provider,
                intent_ref,
                evaluation.evaluation_ref,
                evaluation.evaluation_sha256,
                runtime.binding.binding_ref,
                runtime.binding.revision,
                record.agent.agent_ref,
                record.record_revision,
                record.agent.configuration_sha256,
                evaluation.request.inventory_revision,
                evaluation.action,
                evaluation.target_ref,
                "prepared",
                1,
                0,
                None,
                "0" * 64,
                "0" * 64,
            )
            immutable_sha256 = _sha256(provisional.immutable_payload())
            provisional = replace(provisional, immutable_sha256=immutable_sha256)
            intent = replace(provisional, state_sha256=_sha256(provisional.state_payload()))
            intent.verify()
            state.intents[intent_ref] = intent
            self._remember(state, "prepare_enforcement", idempotency_key, semantic, intent)
            return intent

    def dispatch_enforcement_intent(
        self,
        authority: TenantAuthority,
        *,
        intent_ref: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> EnforcementDispatchReceipt:
        selected_ref = _reference(intent_ref, "intent_ref", prefixes={"cei"})
        expected = _revision(expected_revision, "expected_revision")
        semantic = {"intent_ref": selected_ref, "expected_revision": expected}
        with self._lock:
            state = self._tenant(authority)
            replay = self._replay(state, "dispatch_enforcement", idempotency_key, semantic)
            if replay is not None:
                return replay  # type: ignore[return-value]
            intent = self._bound_record(
                state.intents, selected_ref, authority.tenant_sha256, "intent"
            )
            if not isinstance(intent, EnforcementIntent):
                raise CloudAgentStateError("intent state is invalid")
            intent.verify()
            if intent.revision != expected:
                raise CloudAgentStaleRevision("enforcement intent revision is stale")
            if intent.status not in {"prepared", "retryable"}:
                raise CloudAgentStateError("enforcement intent is terminal")
            if intent.attempts >= _MAX_DISPATCH_ATTEMPTS:
                raise CloudAgentStateError("enforcement retry bound is exhausted")
            record = self._agent(state, authority.tenant_sha256, intent.agent_ref)
            self._assert_agent_pins(
                record,
                agent_revision=intent.agent_revision,
                configuration_sha256=intent.configuration_sha256,
                inventory_revision=intent.inventory_revision,
            )
            runtime = self._active_runtime(state, intent.provider)
            if (
                runtime.binding.binding_ref != intent.binding_ref
                or runtime.binding.revision != intent.binding_revision
            ):
                raise CloudAgentStaleRevision("provider binding changed after intent preparation")
            attempt = intent.attempts + 1
            request_provisional = EnforcementDispatchRequest(
                authority.tenant_sha256,
                intent.provider,
                intent.binding_ref,
                intent.binding_revision,
                intent.intent_ref,
                intent.immutable_sha256,
                intent.action,
                intent.target_ref,
                intent.agent_ref,
                intent.agent_revision,
                intent.configuration_sha256,
                attempt,
                "0" * 64,
            )
            request = replace(
                request_provisional,
                request_sha256=_sha256(request_provisional.signing_payload()),
            )
            try:
                result = runtime.adapter.submit(request)
            except Exception as exc:
                raise CloudAgentAdapterError("provider adapter failed closed") from exc
            self._validate_adapter_result(runtime, request, result)
            status_map: dict[AdapterDisposition, IntentStatus] = {
                "accepted": "adapter_accepted",
                "no_change": "adapter_reported_no_change",
                "rejected": "adapter_rejected",
                "retryable": "retryable",
            }
            updated = replace(
                intent,
                status=status_map[result.disposition],
                revision=intent.revision + 1,
                attempts=attempt,
                last_receipt_sha256=result.receipt_sha256,
                state_sha256="0" * 64,
            )
            updated = replace(updated, state_sha256=_sha256(updated.state_payload()))
            updated.verify()
            state.intents[selected_ref] = updated
            receipt = EnforcementDispatchReceipt(updated, result)
            self._remember(state, "dispatch_enforcement", idempotency_key, semantic, receipt)
            return receipt

    def register_framework_pack(
        self,
        authority: TenantAuthority,
        pack: DatabricksAISecurityFrameworkPack,
        *,
        expected_catalog_revision: int,
        idempotency_key: str,
    ) -> int:
        pack.verify()
        expected = _revision(
            expected_catalog_revision, "expected_catalog_revision", allow_zero=True
        )
        semantic = {
            "pack_sha256": pack.pack_sha256,
            "expected_catalog_revision": expected,
        }
        with self._lock:
            state = self._tenant(authority)
            replay = self._replay(state, "register_framework_pack", idempotency_key, semantic)
            if replay is not None:
                return replay  # type: ignore[return-value]
            if state.framework_catalog_revision != expected:
                raise CloudAgentStaleRevision("framework catalog revision is stale")
            prior = state.packs.get(pack.version)
            if prior is not None and not secrets_compare_digest(
                prior.pack_sha256, pack.pack_sha256
            ):
                raise CloudAgentIdempotencyConflict("framework version content collision")
            if prior is None:
                state.packs[pack.version] = pack
                state.framework_catalog_revision += 1
            result = state.framework_catalog_revision
            self._remember(state, "register_framework_pack", idempotency_key, semantic, result)
            return result

    def review_framework_pack(
        self,
        authority: TenantAuthority,
        *,
        pack_version: str,
        reviewer_ref: str,
        decision: ReviewDecision,
        applicability: Sequence[FrameworkApplicability],
        idempotency_key: str,
    ) -> FrameworkReview:
        reviewer = _reference(reviewer_ref, "reviewer_ref", prefixes={"prn"})
        if decision not in {"approved", "rejected"}:
            raise ValueError("review decision is unsupported")
        selected = tuple(
            sorted(applicability, key=lambda item: (item.control_id, item.workload_ref))
        )
        keys = [(item.control_id, item.workload_ref) for item in selected]
        if not selected or keys != sorted(set(keys)):
            raise ValueError("applicability must be non-empty and unique")
        semantic = {
            "pack_version": pack_version,
            "reviewer_ref": reviewer,
            "decision": decision,
            "applicability": [item.payload() for item in selected],
        }
        with self._lock:
            state = self._tenant(authority)
            replay = self._replay(state, "review_framework_pack", idempotency_key, semantic)
            if replay is not None:
                return replay  # type: ignore[return-value]
            pack = self._pack(state, pack_version)
            control_ids = {mapping.control_id for mapping in pack.mappings}
            if any(item.control_id not in control_ids for item in selected):
                raise CloudAgentBindingError("applicability references an unmapped control")
            now = _timestamp(self._clock(), "clock")
            review_ref = _new_ref(
                "fwr",
                {
                    "tenant_sha256": authority.tenant_sha256,
                    "pack_sha256": pack.pack_sha256,
                    "idempotency_sha256": _idempotency_sha256(idempotency_key),
                },
            )
            provisional = FrameworkReview(
                authority.tenant_sha256,
                review_ref,
                pack.version,
                pack.pack_sha256,
                reviewer,
                decision,
                selected,
                now,
                1,
                "0" * 64,
            )
            review = replace(provisional, review_sha256=_sha256(provisional.payload()))
            review.verify()
            state.reviews[review_ref] = review
            self._remember(state, "review_framework_pack", idempotency_key, semantic, review)
            return review

    def attest_framework_review(
        self,
        authority: TenantAuthority,
        *,
        review_ref: str,
        expected_review_revision: int,
        attestor_ref: str,
        evidence_links: Sequence[FrameworkEvidenceLink],
        idempotency_key: str,
    ) -> FrameworkAttestation:
        selected_review = _reference(review_ref, "review_ref", prefixes={"fwr"})
        expected = _revision(expected_review_revision, "expected_review_revision")
        attestor = _reference(attestor_ref, "attestor_ref", prefixes={"prn"})
        links = tuple(
            sorted(evidence_links, key=lambda link: (link.evidence_ref, link.relation_code))
        )
        if not links or len(links) > _MAX_EVIDENCE_LINKS:
            raise ValueError("attestation evidence links are empty or over bound")
        semantic = {
            "review_ref": selected_review,
            "expected_review_revision": expected,
            "attestor_ref": attestor,
            "evidence_links": [link.payload() for link in links],
        }
        with self._lock:
            state = self._tenant(authority)
            replay = self._replay(state, "attest_framework_review", idempotency_key, semantic)
            if replay is not None:
                return replay  # type: ignore[return-value]
            review = self._bound_record(
                state.reviews, selected_review, authority.tenant_sha256, "review"
            )
            if not isinstance(review, FrameworkReview):
                raise CloudAgentStateError("framework review state is invalid")
            review.verify()
            if review.revision != expected:
                raise CloudAgentStaleRevision("framework review revision is stale")
            if review.decision != "approved":
                raise CloudAgentStateError("rejected framework review cannot be attested")
            if review.reviewer_ref == attestor:
                raise CloudAgentBindingError("reviewer and attestor must be distinct")
            now = _timestamp(self._clock(), "clock")
            attestation_ref = _new_ref(
                "fwa",
                {
                    "tenant_sha256": authority.tenant_sha256,
                    "review_sha256": review.review_sha256,
                    "idempotency_sha256": _idempotency_sha256(idempotency_key),
                },
            )
            provisional = FrameworkAttestation(
                authority.tenant_sha256,
                attestation_ref,
                review.review_ref,
                review.review_sha256,
                attestor,
                links,
                now,
                1,
                "0" * 64,
            )
            attestation = replace(
                provisional, attestation_sha256=_sha256(provisional.payload())
            )
            attestation.verify()
            state.attestations[attestation_ref] = attestation
            self._remember(
                state, "attest_framework_review", idempotency_key, semantic, attestation
            )
            return attestation

    def activate_framework_pack(
        self,
        authority: TenantAuthority,
        *,
        pack_version: str,
        review_ref: str,
        attestation_ref: str,
        expected_activation_revision: int,
        idempotency_key: str,
        action: ActivationAction = "activate",
    ) -> FrameworkActivation:
        expected = _revision(
            expected_activation_revision, "expected_activation_revision", allow_zero=True
        )
        if action not in {"activate", "rollback"}:
            raise ValueError("framework activation action is unsupported")
        selected_review = _reference(review_ref, "review_ref", prefixes={"fwr"})
        selected_attestation = _reference(
            attestation_ref, "attestation_ref", prefixes={"fwa"}
        )
        semantic = {
            "pack_version": pack_version,
            "review_ref": selected_review,
            "attestation_ref": selected_attestation,
            "expected_activation_revision": expected,
            "action": action,
        }
        with self._lock:
            state = self._tenant(authority)
            replay = self._replay(state, "activate_framework_pack", idempotency_key, semantic)
            if replay is not None:
                return replay  # type: ignore[return-value]
            current_revision = 0 if state.activation is None else state.activation.revision
            if current_revision != expected:
                raise CloudAgentStaleRevision("framework activation revision is stale")
            pack = self._pack(state, pack_version)
            review = self._bound_record(
                state.reviews, selected_review, authority.tenant_sha256, "review"
            )
            attestation = self._bound_record(
                state.attestations,
                selected_attestation,
                authority.tenant_sha256,
                "attestation",
            )
            if not isinstance(review, FrameworkReview) or not isinstance(
                attestation, FrameworkAttestation
            ):
                raise CloudAgentStateError("framework approval state is invalid")
            review.verify()
            attestation.verify()
            if (
                review.decision != "approved"
                or review.pack_version != pack.version
                or not secrets_compare_digest(review.pack_sha256, pack.pack_sha256)
                or attestation.review_ref != review.review_ref
                or not secrets_compare_digest(
                    attestation.review_sha256, review.review_sha256
                )
            ):
                raise CloudAgentBindingError("framework approval chain does not match pack")
            prior_version = None if state.activation is None else state.activation.version
            if action == "rollback" and prior_version is None:
                raise CloudAgentStateError("framework rollback requires an active version")
            now = _timestamp(self._clock(), "clock")
            provisional = FrameworkActivation(
                authority.tenant_sha256,
                pack.version,
                pack.pack_sha256,
                review.review_ref,
                review.review_sha256,
                attestation.attestation_ref,
                attestation.attestation_sha256,
                prior_version,
                action,
                now,
                current_revision + 1,
                "0" * 64,
            )
            activation = replace(
                provisional, activation_sha256=_sha256(provisional.payload())
            )
            activation.verify()
            state.activation = activation
            self._remember(
                state, "activate_framework_pack", idempotency_key, semantic, activation
            )
            return activation

    def verify_tenant_integrity(self, authority: TenantAuthority) -> str:
        """Verify all canonical hashes and return a tenant state root."""

        with self._lock:
            state = self._tenant(authority)
            digests: list[str] = []
            for runtime in state.adapters.values():
                runtime.binding.verify()
                digests.append(runtime.binding.state_sha256)
            for inventory in state.inventories.values():
                for record in inventory.values():
                    record.verify()
                    digests.append(record.state_sha256)
            for metadata in state.inventory_metadata.values():
                metadata.verify()
                digests.append(metadata.state_sha256)
            for event in state.events.values():
                event.verify()
                digests.append(event.integrity_sha256)
            for evaluation in state.evaluations.values():
                evaluation.verify()
                digests.append(evaluation.evaluation_sha256)
            for intent in state.intents.values():
                intent.verify()
                digests.append(intent.state_sha256)
            for pack in state.packs.values():
                pack.verify()
                digests.append(pack.pack_sha256)
            for review in state.reviews.values():
                review.verify()
                digests.append(review.review_sha256)
            for attestation in state.attestations.values():
                attestation.verify()
                digests.append(attestation.attestation_sha256)
            if state.activation is not None:
                state.activation.verify()
                digests.append(state.activation.activation_sha256)
            return _sha256(
                {
                    "schema": "lightwork.cloud-agent.tenant-state-root.v1",
                    "tenant_sha256": authority.tenant_sha256,
                    "digests": sorted(digests),
                }
            )

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise CloudAgentGovernanceDisabled(
                "cloud-agent governance requires an explicit deployment enablement"
            )

    def _tenant(self, authority: TenantAuthority) -> _TenantState:
        self._require_enabled()
        if not isinstance(authority, TenantAuthority):
            raise CloudAgentBindingError("tenant authority is invalid")
        state = self._tenants.get(authority.tenant_sha256)
        if state is None:
            raise CloudAgentBindingError("tenant authority is not enrolled")
        if (
            state.authority.authority_sha256 != authority.authority_sha256
            or state.authority.revision != authority.revision
        ):
            raise CloudAgentStaleRevision("tenant authority binding is stale")
        return state

    @staticmethod
    def _runtime(state: _TenantState, provider: CloudProvider) -> _AdapterRuntime:
        runtime = state.adapters.get(provider)
        if runtime is None:
            raise CloudAgentBindingError("provider adapter is not bound")
        runtime.binding.verify()
        return runtime

    def _active_runtime(
        self, state: _TenantState, provider: CloudProvider
    ) -> _AdapterRuntime:
        runtime = self._runtime(state, provider)
        if runtime.binding.revoked:
            raise CloudAgentRevoked("provider binding is revoked")
        return runtime

    @staticmethod
    def _assert_page_binding(page: InventoryPage, binding: ProviderBinding) -> None:
        if (
            page.provider != binding.provider
            or page.binding_ref != binding.binding_ref
            or page.binding_revision != binding.revision
        ):
            raise CloudAgentStaleRevision("inventory page binding is stale")

    @staticmethod
    def _assert_event_binding(
        event: CloudAgentObservabilityEvent, binding: ProviderBinding
    ) -> None:
        if (
            event.provider != binding.provider
            or event.binding_ref != binding.binding_ref
            or event.binding_revision != binding.revision
        ):
            raise CloudAgentStaleRevision("observability event binding is stale")

    @staticmethod
    def _scan_metadata_sha256(page: InventoryPage) -> str:
        return _sha256(
            {
                "provider": page.provider,
                "binding_ref": page.binding_ref,
                "binding_revision": page.binding_revision,
                "scan_ref": page.scan_ref,
                "scope_sha256": page.scope_sha256,
                "source_snapshot_sha256": page.source_snapshot_sha256,
                "page_count": page.page_count,
                "snapshot_complete": page.snapshot_complete,
                "incomplete_reason_codes": list(page.incomplete_reason_codes),
                "captured_at": page.captured_at,
            }
        )

    @staticmethod
    def _inventory_revision(state: _TenantState, provider: CloudProvider) -> int:
        metadata = state.inventory_metadata.get(provider)
        return 0 if metadata is None else metadata.inventory_revision

    def _finalize_scan(
        self, state: _TenantState, provider: CloudProvider, scan: _ScanState
    ) -> int:
        pages = [scan.pages[index] for index in range(scan.page_count)]
        items = [item for page in pages for item in page.items]
        refs = [item.agent_ref for item in items]
        if len(refs) != len(set(refs)):
            raise CloudAgentIdempotencyConflict("agent appears in multiple inventory pages")
        first = pages[0]
        current = state.inventories.get(provider, {})
        if first.snapshot_complete:
            selected: dict[str, CloudAgentRecord] = {}
        else:
            selected = dict(current)
        new_inventory_revision = self._inventory_revision(state, provider) + 1
        for item in items:
            existing_tenant = self._agent_tenants.get(item.agent_ref)
            if existing_tenant is not None and existing_tenant != state.authority.tenant_sha256:
                raise CloudAgentBindingError("agent reference is already bound to another tenant")
            prior = current.get(item.agent_ref)
            if prior is None:
                record_revision = 1
                first_seen = item.observed_at
                previous_config = None
                drifted = False
            else:
                prior.verify()
                changed = not secrets_compare_digest(prior.agent.item_sha256, item.item_sha256)
                record_revision = prior.record_revision + (1 if changed else 0)
                first_seen = prior.first_seen_at
                previous_config = (
                    prior.agent.configuration_sha256
                    if prior.agent.configuration_sha256 != item.configuration_sha256
                    else prior.previous_configuration_sha256
                )
                drifted = changed
            provisional = CloudAgentRecord(
                state.authority.tenant_sha256,
                provider,
                item,
                record_revision,
                new_inventory_revision,
                first_seen,
                item.observed_at,
                previous_config,
                drifted,
                "0" * 64,
            )
            record = replace(provisional, state_sha256=_sha256(provisional.payload()))
            record.verify()
            selected[item.agent_ref] = record
            self._agent_tenants[item.agent_ref] = state.authority.tenant_sha256
        if len(selected) > _MAX_AGENTS_PER_TENANT:
            raise CloudAgentStateError("cloud-agent inventory bound is exhausted")
        if first.snapshot_complete:
            removed = set(current) - set(selected)
            for agent_ref in removed:
                if self._agent_tenants.get(agent_ref) == state.authority.tenant_sha256:
                    del self._agent_tenants[agent_ref]
        state.inventories[provider] = selected
        metadata_payload = {
            "schema": "lightwork.cloud-agent.inventory-metadata.v1",
            "tenant_sha256": state.authority.tenant_sha256,
            "provider": provider,
            "inventory_revision": new_inventory_revision,
            "scan_ref": first.scan_ref,
            "scope_sha256": first.scope_sha256,
            "source_snapshot_sha256": first.source_snapshot_sha256,
            "snapshot_complete": first.snapshot_complete,
            "incomplete_reason_codes": list(first.incomplete_reason_codes),
            "captured_at": first.captured_at,
            "agent_count": len(selected),
        }
        state.inventory_metadata[provider] = InventoryMetadata(
            state.authority.tenant_sha256,
            provider,
            new_inventory_revision,
            first.scan_ref,
            first.scope_sha256,
            first.source_snapshot_sha256,
            first.snapshot_complete,
            first.incomplete_reason_codes,
            first.captured_at,
            len(selected),
            _sha256(metadata_payload),
        )
        return new_inventory_revision

    def _agent(
        self, state: _TenantState, tenant_sha256: str, agent_ref: str
    ) -> CloudAgentRecord:
        bound_tenant = self._agent_tenants.get(agent_ref)
        if bound_tenant is not None and bound_tenant != tenant_sha256:
            raise CloudAgentBindingError("agent is not bound to tenant authority")
        for inventory in state.inventories.values():
            record = inventory.get(agent_ref)
            if record is not None:
                record.verify()
                return record
        raise CloudAgentStateError("cloud-agent record is unavailable")

    @staticmethod
    def _assert_agent_pins(
        record: CloudAgentRecord,
        *,
        agent_revision: int,
        configuration_sha256: str | None = None,
        inventory_revision: int | None = None,
    ) -> None:
        if record.record_revision != agent_revision:
            raise CloudAgentStaleRevision("cloud-agent revision is stale")
        if (
            configuration_sha256 is not None
            and record.agent.configuration_sha256 != configuration_sha256
        ):
            raise CloudAgentStaleRevision("cloud-agent configuration drifted")
        if inventory_revision is not None and record.inventory_revision != inventory_revision:
            raise CloudAgentStaleRevision("cloud-agent inventory revision is stale")

    @staticmethod
    def _action(value: object) -> EnforcementAction:
        allowed = {
            "detach_data_source",
            "disable_agent",
            "quarantine_agent",
            "restrict_model",
            "restrict_tool",
        }
        if value not in allowed:
            raise ValueError("enforcement action is unsupported")
        return value  # type: ignore[return-value]

    @staticmethod
    def _assert_target(
        agent: NormalizedCloudAgent, action: EnforcementAction, target_ref: str
    ) -> None:
        allowed: set[str]
        if action in {"disable_agent", "quarantine_agent"}:
            allowed = {agent.agent_ref}
        elif action == "restrict_tool":
            allowed = set(agent.relationships.tool_refs)
        elif action == "restrict_model":
            allowed = set(agent.relationships.model_refs)
        else:
            allowed = set(agent.relationships.data_source_refs)
        if target_ref not in allowed:
            raise CloudAgentBindingError("enforcement target is not related to agent")

    def _validate_evaluation_result(
        self, result: object, request: PolicyEvaluationRequest
    ) -> tuple[PolicyDecision, EnforcementAction | None, str | None, tuple[str, ...]]:
        if not isinstance(result, PolicyEvaluationResult):
            raise CloudAgentAdapterError("policy evaluator returned an invalid contract")
        if result.decision not in {"allow_intent", "deny", "observe"}:
            raise CloudAgentAdapterError("policy evaluator decision is unsupported")
        reasons = _codes(result.reason_codes, "reason_codes")
        if not reasons:
            raise CloudAgentAdapterError("policy evaluator must return a reason code")
        if result.decision == "allow_intent":
            if result.action != request.requested_action or result.target_ref != request.target_ref:
                raise CloudAgentAdapterError("policy evaluator changed the requested binding")
            action: EnforcementAction | None = self._action(result.action)
            target = _reference(
                result.target_ref,
                "target_ref",
                prefixes={"cag", "cad", "cam", "cat"},
            )
        else:
            if result.action is not None or result.target_ref is not None:
                raise CloudAgentAdapterError("non-allow decision cannot create an action")
            action = None
            target = None
        return result.decision, action, target, reasons

    @staticmethod
    def _validate_adapter_result(
        runtime: _AdapterRuntime,
        request: EnforcementDispatchRequest,
        result: object,
    ) -> None:
        if not isinstance(result, ProviderAdapterResult):
            raise CloudAgentAdapterError("provider adapter returned an invalid contract")
        result.verify()
        binding = runtime.binding
        if (
            result.tenant_sha256 != request.tenant_sha256
            or result.provider != request.provider
            or result.binding_ref != binding.binding_ref
            or result.binding_revision != binding.revision
            or result.intent_ref != request.intent_ref
            or not secrets_compare_digest(result.request_sha256, request.request_sha256)
        ):
            raise CloudAgentBindingError("provider adapter result binding is forged or stale")
        try:
            verified = runtime.verifier(binding, result)
        except Exception as exc:
            raise CloudAgentAdapterError("provider adapter verifier failed closed") from exc
        if verified is not True:
            raise CloudAgentIntegrityError("provider adapter signature is not trusted")

    @staticmethod
    def _pack(
        state: _TenantState, version: str
    ) -> DatabricksAISecurityFrameworkPack:
        pack = state.packs.get(version)
        if pack is None:
            raise CloudAgentStateError("framework pack version is unavailable")
        pack.verify()
        return pack

    @staticmethod
    def _bound_record(
        records: Mapping[str, object],
        record_ref: str,
        tenant_sha256: str,
        label: str,
    ) -> object:
        record = records.get(record_ref)
        if record is None:
            raise CloudAgentStateError(f"{label} record is unavailable")
        if getattr(record, "tenant_sha256", None) != tenant_sha256:
            raise CloudAgentBindingError(f"{label} record is not bound to tenant")
        return record

    @staticmethod
    def _semantic_sha256(value: object) -> str:
        return _sha256(value)

    def _replay(
        self,
        state: _TenantState,
        operation: str,
        idempotency_key: str,
        semantic: object,
    ) -> object | None:
        key = _idempotency_sha256(idempotency_key)
        entry = state.idempotency.get((operation, key))
        if entry is None:
            return None
        semantic_sha = self._semantic_sha256(semantic)
        if not secrets_compare_digest(entry.semantic_sha256, semantic_sha):
            raise CloudAgentIdempotencyConflict(
                "idempotency key is bound to different operation semantics"
            )
        return entry.result

    def _remember(
        self,
        state: _TenantState,
        operation: str,
        idempotency_key: str,
        semantic: object,
        result: object,
    ) -> None:
        key = _idempotency_sha256(idempotency_key)
        identity = (operation, key)
        semantic_sha = self._semantic_sha256(semantic)
        prior = state.idempotency.get(identity)
        if prior is not None:
            if not secrets_compare_digest(prior.semantic_sha256, semantic_sha):
                raise CloudAgentIdempotencyConflict(
                    "idempotency key is bound to different operation semantics"
                )
            return
        if len(state.idempotency) >= _MAX_IDEMPOTENCY_RECORDS:
            raise CloudAgentStateError("idempotency record bound is exhausted")
        state.idempotency[identity] = _IdempotencyEntry(semantic_sha, result)


__all__ = [
    "AdapterDisposition",
    "AgentRelationships",
    "CLOUD_AGENT_LIMIT_NOTICE",
    "CLOUD_PROVIDERS",
    "CloudAgentAdapterError",
    "CloudAgentBindingError",
    "CloudAgentEnforcementAdapter",
    "CloudAgentGovernanceAuthority",
    "CloudAgentGovernanceDisabled",
    "CloudAgentGovernanceError",
    "CloudAgentIdempotencyConflict",
    "CloudAgentIntegrityError",
    "CloudAgentObservabilityEvent",
    "CloudAgentRecord",
    "CloudAgentRevoked",
    "CloudAgentStaleRevision",
    "CloudAgentStateError",
    "CloudAgentPolicyEvaluator",
    "CloudProvider",
    "DatabricksAISecurityFrameworkPack",
    "EnforcementDispatchReceipt",
    "EnforcementDispatchRequest",
    "EnforcementIntent",
    "FRAMEWORK_LIMIT_NOTICE",
    "FrameworkActivation",
    "FrameworkApplicability",
    "FrameworkAttestation",
    "FrameworkControlMapping",
    "FrameworkEvidenceLink",
    "FrameworkReview",
    "InventoryIngestReceipt",
    "InventoryMetadata",
    "InventoryPage",
    "InventorySlice",
    "NormalizedCloudAgent",
    "ObservabilityReceipt",
    "PolicyEvaluationRecord",
    "PolicyEvaluationRequest",
    "PolicyEvaluationResult",
    "ProviderAdapterResult",
    "ProviderBinding",
    "TenantAuthority",
]
