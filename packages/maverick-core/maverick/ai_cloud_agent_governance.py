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
import hmac
import json
import math
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, Protocol, cast

CloudProvider = Literal["aws_bedrock", "azure_ai_foundry", "google_vertex_ai"]
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
AdapterDisposition = Literal[
    "accepted",
    "ambiguous",
    "no_change",
    "rejected",
    "retryable",
]
IntentStatus = Literal[
    "adapter_accepted",
    "adapter_rejected",
    "adapter_reported_no_change",
    "dispatching",
    "prepared",
    "reconciled_applied",
    "reconciled_no_change",
    "reconciliation_required",
    "retryable",
]
ApplicabilityStatus = Literal["applicable", "needs_review", "not_applicable"]
ReviewDecision = Literal["approved", "rejected"]
ActivationAction = Literal["activate", "rollback"]
ReconciliationResolution = Literal["applied", "no_change", "retry"]

CLOUD_PROVIDERS: tuple[CloudProvider, ...] = (
    "aws_bedrock",
    "azure_ai_foundry",
    "google_vertex_ai",
)

_PROVIDER_CONNECTORS: dict[CloudProvider, str] = {
    "aws_bedrock": "bedrock",
    "azure_ai_foundry": "azure_ai_foundry",
    "google_vertex_ai": "vertex_ai",
}

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
_MAX_AGENTS_PER_TENANT = 1_000_000
_MAX_EVENTS_PER_TENANT = 100_000
_MAX_IDEMPOTENCY_RECORDS = 200_000
_MAX_DISPATCH_ATTEMPTS = 8
_MAX_FRAMEWORK_MAPPINGS = 2_000
_MAX_EVIDENCE_LINKS = 128
_MAX_ACTIVE_SCANS_PER_TENANT = 16
_MAX_CONFIGURED_RECORDS_PER_SYNC = 1_000_000
_MAX_CONFIGURED_EVENTS_PER_BATCH = 100_000
_MIN_RETENTION_SECONDS = 3_600
_MAX_RETENTION_SECONDS = 315_360_000
_DEFAULT_RECORDS_PER_SYNC = 10_000
_DEFAULT_EVENTS_PER_BATCH = 5_000
_DEFAULT_RETENTION_SECONDS = 31_536_000
_MAX_FRAMEWORK_DOCUMENT_BYTES = 1_048_576
_MAX_CLOCK_SKEW_SECONDS = 300


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


class CloudAgentAmbiguousEffect(CloudAgentAdapterError):
    """A dispatch may have reached a provider and requires reconciliation."""


class CloudAgentRevoked(CloudAgentBindingError):
    """The exact provider binding is revoked."""


class CloudAgentStateError(CloudAgentGovernanceError):
    """The bounded authority state cannot safely accept an operation."""


class CloudAgentConfigError(CloudAgentGovernanceError):
    """Cloud-agent governance configuration is absent, malformed, or incoherent."""


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


_MAX_REGISTER_ROWS = 500


def _register_limit(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_REGISTER_ROWS:
        raise ValueError("register limit is invalid")
    return value


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


def _bounded_integer(
    value: object,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise ValueError(f"{label} is outside its integer bound")
    return value


def _closed_mapping(
    value: object,
    label: str,
    *,
    allowed: frozenset[str],
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    if any(not isinstance(key, str) or key not in allowed for key in value):
        raise ValueError(f"{label} contains unsupported fields")
    return cast(Mapping[str, object], value)


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

    try:
        return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))
    except (UnicodeEncodeError, TypeError):
        return False


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
class CloudAgentGovernanceConfig:
    """Strict runtime policy derived from one already-selected config snapshot."""

    enable: bool = False
    enabled_providers: tuple[CloudProvider, ...] = ()
    databricks_framework_enable: bool = False
    max_records_per_sync: int = _DEFAULT_RECORDS_PER_SYNC
    max_events_per_batch: int = _DEFAULT_EVENTS_PER_BATCH
    retention_seconds: int = _DEFAULT_RETENTION_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.enable, bool):
            raise ValueError("ai_cloud_agent_governance.enable must be a boolean")
        if not isinstance(self.databricks_framework_enable, bool):
            raise ValueError(
                "ai_cloud_agent_governance.databricks_framework_enable must be a boolean"
            )
        if isinstance(self.enabled_providers, (str, bytes)):
            raise ValueError("enabled_providers must be a closed provider sequence")
        selected = tuple(_provider(provider) for provider in self.enabled_providers)
        if len(selected) != len(set(selected)):
            raise ValueError("enabled_providers must be unique")
        selected = tuple(sorted(selected))
        if self.enable and not selected:
            raise ValueError("enabled cloud-agent governance requires a provider")
        if not self.enable and self.databricks_framework_enable:
            raise ValueError("disabled cloud-agent governance cannot enable a sub-capability")
        object.__setattr__(self, "enabled_providers", selected)
        _bounded_integer(
            self.max_records_per_sync,
            "max_records_per_sync",
            minimum=1,
            maximum=_MAX_CONFIGURED_RECORDS_PER_SYNC,
        )
        _bounded_integer(
            self.max_events_per_batch,
            "max_events_per_batch",
            minimum=1,
            maximum=_MAX_CONFIGURED_EVENTS_PER_BATCH,
        )
        _bounded_integer(
            self.retention_seconds,
            "retention_seconds",
            minimum=_MIN_RETENTION_SECONDS,
            maximum=_MAX_RETENTION_SECONDS,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> CloudAgentGovernanceConfig:
        raw = _closed_mapping(
            value,
            "ai_cloud_agent_governance",
            allowed=frozenset(
                {
                    "enable",
                    "enabled_providers",
                    "databricks_framework_enable",
                    "max_records_per_sync",
                    "max_events_per_batch",
                    "retention_seconds",
                }
            ),
        )
        provider_value = raw.get("enabled_providers", ())
        if isinstance(provider_value, (str, bytes)) or not isinstance(provider_value, Sequence):
            raise ValueError("enabled_providers must be an array")
        providers = tuple(provider_value)
        return cls(
            enable=raw.get("enable", False),  # type: ignore[arg-type]
            enabled_providers=providers,  # type: ignore[arg-type]
            databricks_framework_enable=raw.get("databricks_framework_enable", False),  # type: ignore[arg-type]
            max_records_per_sync=raw.get("max_records_per_sync", _DEFAULT_RECORDS_PER_SYNC),  # type: ignore[arg-type]
            max_events_per_batch=raw.get("max_events_per_batch", _DEFAULT_EVENTS_PER_BATCH),  # type: ignore[arg-type]
            retention_seconds=raw.get("retention_seconds", _DEFAULT_RETENTION_SECONDS),  # type: ignore[arg-type]
        )


def load_cloud_agent_governance_config(
    config_root: Mapping[str, object] | None = None,
) -> CloudAgentGovernanceConfig:
    """Load exactly one tenant-selected config snapshot and fail closed on source errors."""

    if config_root is None:
        try:
            from .config import config_source_errors, load_config

            root = load_config()
            if config_source_errors():
                raise CloudAgentConfigError("cloud-agent governance configuration is unreadable")
        except CloudAgentConfigError:
            raise
        except Exception as exc:
            raise CloudAgentConfigError(
                "cloud-agent governance configuration is unavailable"
            ) from exc
    else:
        root = config_root
    if not isinstance(root, Mapping):
        raise CloudAgentConfigError("configuration root must be an object")
    raw = root.get("ai_cloud_agent_governance", {})
    if not isinstance(raw, Mapping):
        raise CloudAgentConfigError("ai_cloud_agent_governance must be an object")
    try:
        return CloudAgentGovernanceConfig.from_mapping(cast(Mapping[str, object], raw))
    except (TypeError, ValueError) as exc:
        raise CloudAgentConfigError(str(exc)) from exc


class TenantBoundReferencePseudonymizer:
    """Keyed, non-exporting reference authority for provider resource identifiers."""

    __slots__ = ("_key",)

    def __init__(self, key: bytes) -> None:
        if not isinstance(key, bytes) or not 32 <= len(key) <= 1_024:
            raise ValueError("reference pseudonymization requires an injected 32-byte key")
        self._key = key

    def __reduce__(self) -> object:
        raise TypeError("provider reference authority cannot be serialized")

    def digest(self, material: Mapping[str, object]) -> str:
        return hmac.new(self._key, _canonical(material), hashlib.sha256).hexdigest()

    def reference(self, prefix: str, material: Mapping[str, object]) -> str:
        if prefix not in {"cag", "cad", "cam", "cat"}:
            raise ValueError("provider resource reference namespace is unsupported")
        return f"{prefix}_{self.digest(material)[:32]}"


class ProviderRevisionAuthority(Protocol):
    @property
    def durability_mode(self) -> Literal["durable", "process_local"]: ...

    def resolve_revision(
        self,
        *,
        tenant_sha256: str,
        provider: CloudProvider,
        source_resource_sha256: str,
        configuration_sha256: str,
    ) -> int:
        """Return a monotonic local revision for one exact provider resource."""


class InMemoryProviderRevisionAuthority:
    """Thread-safe reference implementation; deployments may inject a durable store."""

    def __init__(self, *, max_resources: int = _MAX_AGENTS_PER_TENANT) -> None:
        self._max_resources = _bounded_integer(
            max_resources,
            "max_resources",
            minimum=1,
            maximum=_MAX_CONFIGURED_RECORDS_PER_SYNC,
        )
        self._records: dict[tuple[str, CloudProvider, str], tuple[str, int]] = {}
        self._lock = threading.RLock()

    @property
    def durability_mode(self) -> Literal["process_local"]:
        return "process_local"

    def resolve_revision(
        self,
        *,
        tenant_sha256: str,
        provider: CloudProvider,
        source_resource_sha256: str,
        configuration_sha256: str,
    ) -> int:
        tenant = _digest(tenant_sha256, "tenant_sha256")
        selected_provider = _provider(provider)
        resource = _digest(source_resource_sha256, "source_resource_sha256")
        configuration = _digest(configuration_sha256, "configuration_sha256")
        identity = (tenant, selected_provider, resource)
        with self._lock:
            prior = self._records.get(identity)
            if prior is None:
                if len(self._records) >= self._max_resources:
                    raise CloudAgentStateError("provider revision authority bound is exhausted")
                revision = 1
            elif secrets_compare_digest(prior[0], configuration):
                revision = prior[1]
            else:
                revision = prior[1] + 1
            self._records[identity] = (configuration, revision)
            return revision


class ProviderNativeAgentBinding:
    """Server-only native lookup record; never include this type in API output."""

    __slots__ = (
        "tenant_sha256",
        "provider",
        "binding_ref",
        "binding_revision",
        "agent_ref",
        "source_resource_sha256",
        "scope_sha256",
        "source_revision",
        "configuration_sha256",
        "resource_kind",
        "_native_resource_id",
    )

    def __init__(
        self,
        *,
        tenant_sha256: str,
        provider: CloudProvider,
        binding_ref: str,
        binding_revision: int,
        agent_ref: str,
        source_resource_sha256: str,
        scope_sha256: str,
        source_revision: int,
        configuration_sha256: str,
        resource_kind: str,
        native_resource_id: str,
    ) -> None:
        self.tenant_sha256 = tenant_sha256
        self.provider = provider
        self.binding_ref = binding_ref
        self.binding_revision = binding_revision
        self.agent_ref = agent_ref
        self.source_resource_sha256 = source_resource_sha256
        self.scope_sha256 = scope_sha256
        self.source_revision = source_revision
        self.configuration_sha256 = configuration_sha256
        self.resource_kind = resource_kind
        self._native_resource_id = native_resource_id
        self.verify()

    def __repr__(self) -> str:
        return "ProviderNativeAgentBinding(<server-only>)"

    def __reduce__(self) -> object:
        raise TypeError("server-only provider bindings cannot be serialized")

    @property
    def native_resource_id(self) -> str:
        return self._native_resource_id

    def verify(self) -> None:
        _digest(self.tenant_sha256, "tenant_sha256")
        _provider(self.provider)
        _reference(self.binding_ref, "binding_ref", prefixes={"cab"})
        _revision(self.binding_revision, "binding_revision")
        _reference(self.agent_ref, "agent_ref", prefixes={"cag"})
        _digest(self.source_resource_sha256, "source_resource_sha256")
        _digest(self.scope_sha256, "scope_sha256")
        _revision(self.source_revision, "source_revision")
        _digest(self.configuration_sha256, "configuration_sha256")
        _code(self.resource_kind, "resource_kind")
        if (
            not isinstance(self.native_resource_id, str)
            or not 1 <= len(self.native_resource_id) <= 256
            or any(ord(char) < 32 for char in self.native_resource_id)
        ):
            raise CloudAgentBindingError("native provider resource binding is invalid")


class ResolvedProviderDispatch:
    """Ephemeral exact resolution supplied only to a provider transport."""

    __slots__ = ("agent", "_native_target_id", "target_kind")

    def __init__(
        self,
        agent: ProviderNativeAgentBinding,
        native_target_id: str,
        target_kind: str,
    ) -> None:
        self.agent = agent
        self._native_target_id = native_target_id
        self.target_kind = target_kind
        self.verify()

    def __repr__(self) -> str:
        return "ResolvedProviderDispatch(<server-only>)"

    def __reduce__(self) -> object:
        raise TypeError("resolved provider dispatches cannot be serialized")

    @property
    def native_target_id(self) -> str:
        return self._native_target_id

    def verify(self) -> None:
        self.agent.verify()
        _code(self.target_kind, "target_kind")
        if (
            not isinstance(self.native_target_id, str)
            or not 1 <= len(self.native_target_id) <= 256
            or any(ord(char) < 32 for char in self.native_target_id)
        ):
            raise CloudAgentBindingError("native provider target binding is invalid")


class ProviderResourceRegistry(Protocol):
    @property
    def durability_mode(self) -> Literal["durable", "process_local"]: ...

    def register_agent(self, binding: ProviderNativeAgentBinding) -> None:
        """Persist one exact tenant/provider/native binding in a server-only store."""

    def register_agents(self, bindings: Sequence[ProviderNativeAgentBinding]) -> None:
        """Atomically persist a bounded normalized inventory batch."""

    def replace_scope(
        self,
        *,
        tenant_sha256: str,
        provider: CloudProvider,
        scope_sha256: str,
        bindings: Sequence[ProviderNativeAgentBinding],
    ) -> None:
        """Atomically replace one complete provider-scope binding snapshot."""

    def resolve_dispatch(self, request: EnforcementDispatchRequest) -> ResolvedProviderDispatch:
        """Resolve exact current native agent/target pins or fail closed."""


class InMemoryProviderResourceRegistry:
    """Bounded reference registry for tests/single-process deployments."""

    def __init__(self, *, max_resources: int = _MAX_AGENTS_PER_TENANT) -> None:
        self._max_resources = _bounded_integer(
            max_resources,
            "max_resources",
            minimum=1,
            maximum=_MAX_CONFIGURED_RECORDS_PER_SYNC,
        )
        self._agents: dict[tuple[str, CloudProvider, str], ProviderNativeAgentBinding] = {}
        self._relationships: dict[tuple[str, CloudProvider, str, str], tuple[str, str]] = {}
        self._max_relationships = min(
            self._max_resources * _MAX_RELATIONSHIPS,
            _MAX_CONFIGURED_RECORDS_PER_SYNC,
        )
        self._lock = threading.RLock()

    @property
    def durability_mode(self) -> Literal["process_local"]:
        return "process_local"

    def register_agent(self, binding: ProviderNativeAgentBinding) -> None:
        self.register_agents((binding,))

    def register_agents(self, bindings: Sequence[ProviderNativeAgentBinding]) -> None:
        if isinstance(bindings, (str, bytes)) or len(bindings) > self._max_resources:
            raise CloudAgentStateError("native provider binding batch is over bound")
        selected = tuple(bindings)
        identities: list[tuple[str, CloudProvider, str]] = []
        for binding in selected:
            if not isinstance(binding, ProviderNativeAgentBinding):
                raise CloudAgentBindingError("native provider binding contract is invalid")
            binding.verify()
            identities.append((binding.tenant_sha256, binding.provider, binding.agent_ref))
        if len(identities) != len(set(identities)):
            raise CloudAgentIdempotencyConflict(
                "native provider binding batch contains duplicate resources"
            )
        with self._lock:
            staged = dict(self._agents)
            for identity, binding in zip(identities, selected, strict=True):
                prior = staged.get(identity)
                if prior is not None:
                    if prior.source_resource_sha256 != binding.source_resource_sha256:
                        raise CloudAgentBindingError("native provider resource identity changed")
                    if binding.source_revision < prior.source_revision:
                        raise CloudAgentStaleRevision("native provider revision moved backwards")
                staged[identity] = binding
            if len(staged) > self._max_resources:
                raise CloudAgentStateError("native provider binding bound is exhausted")
            self._agents = staged

    def replace_scope(
        self,
        *,
        tenant_sha256: str,
        provider: CloudProvider,
        scope_sha256: str,
        bindings: Sequence[ProviderNativeAgentBinding],
    ) -> None:
        tenant = _digest(tenant_sha256, "tenant_sha256")
        selected_provider = _provider(provider)
        selected_scope = _digest(scope_sha256, "scope_sha256")
        if isinstance(bindings, (str, bytes)) or len(bindings) > self._max_resources:
            raise CloudAgentStateError("native provider binding batch is over bound")
        selected = tuple(bindings)
        identities: list[tuple[str, CloudProvider, str]] = []
        for binding in selected:
            if not isinstance(binding, ProviderNativeAgentBinding):
                raise CloudAgentBindingError("native provider binding contract is invalid")
            binding.verify()
            if (
                binding.tenant_sha256 != tenant
                or binding.provider != selected_provider
                or binding.scope_sha256 != selected_scope
            ):
                raise CloudAgentBindingError(
                    "native provider binding does not match replacement scope"
                )
            identities.append((tenant, selected_provider, binding.agent_ref))
        if len(identities) != len(set(identities)):
            raise CloudAgentIdempotencyConflict(
                "native provider binding batch contains duplicate resources"
            )
        with self._lock:
            removed_agent_refs = {
                agent_ref
                for (bound_tenant, bound_provider, agent_ref), binding in self._agents.items()
                if bound_tenant == tenant
                and bound_provider == selected_provider
                and binding.scope_sha256 == selected_scope
            }.difference(binding.agent_ref for binding in selected)
            staged = {
                identity: binding
                for identity, binding in self._agents.items()
                if not (
                    identity[0] == tenant
                    and identity[1] == selected_provider
                    and binding.scope_sha256 == selected_scope
                )
            }
            for identity, binding in zip(identities, selected, strict=True):
                prior = self._agents.get(identity)
                if prior is not None:
                    if prior.source_resource_sha256 != binding.source_resource_sha256:
                        raise CloudAgentBindingError("native provider resource identity changed")
                    if binding.source_revision < prior.source_revision:
                        raise CloudAgentStaleRevision("native provider revision moved backwards")
                staged[identity] = binding
            if len(staged) > self._max_resources:
                raise CloudAgentStateError("native provider binding bound is exhausted")
            relationships = {
                identity: value
                for identity, value in self._relationships.items()
                if not (
                    identity[0] == tenant
                    and identity[1] == selected_provider
                    and identity[2] in removed_agent_refs
                )
            }
            self._agents = staged
            self._relationships = relationships

    def register_relationship(
        self,
        *,
        tenant_sha256: str,
        provider: CloudProvider,
        agent_ref: str,
        target_ref: str,
        target_kind: str,
        native_target_id: str,
    ) -> None:
        """Deployment/future richer discovery seam for exact tool/data bindings."""

        tenant = _digest(tenant_sha256, "tenant_sha256")
        selected_provider = _provider(provider)
        agent = _reference(agent_ref, "agent_ref", prefixes={"cag"})
        target = _reference(
            target_ref,
            "target_ref",
            prefixes={"cad", "cam", "cat"},
        )
        kind = _code(target_kind, "target_kind")
        if (
            not isinstance(native_target_id, str)
            or not 1 <= len(native_target_id) <= 256
            or any(ord(char) < 32 for char in native_target_id)
        ):
            raise CloudAgentBindingError("native provider target binding is invalid")
        with self._lock:
            if (tenant, selected_provider, agent, target) not in self._relationships and len(
                self._relationships
            ) >= self._max_relationships:
                raise CloudAgentStateError("native provider relationship bound is exhausted")
            if (tenant, selected_provider, agent) not in self._agents:
                raise CloudAgentBindingError("native provider agent binding is unavailable")
            self._relationships[(tenant, selected_provider, agent, target)] = (
                kind,
                native_target_id,
            )

    def resolve_dispatch(self, request: EnforcementDispatchRequest) -> ResolvedProviderDispatch:
        identity = (request.tenant_sha256, request.provider, request.agent_ref)
        with self._lock:
            agent = self._agents.get(identity)
            if agent is None:
                raise CloudAgentBindingError("native provider agent binding is unavailable")
            agent.verify()
            if (
                agent.binding_ref != request.binding_ref
                or agent.binding_revision != request.binding_revision
                or agent.configuration_sha256 != request.configuration_sha256
            ):
                raise CloudAgentStaleRevision("native provider agent binding is stale")
            if request.target_ref == request.agent_ref:
                resolved = ResolvedProviderDispatch(
                    agent,
                    agent.native_resource_id,
                    "agent",
                )
            else:
                target = self._relationships.get((*identity, request.target_ref))
                if target is None:
                    raise CloudAgentBindingError(
                        "native provider relationship binding is unavailable"
                    )
                resolved = ResolvedProviderDispatch(agent, target[1], target[0])
            resolved.verify()
            return resolved


@dataclass(frozen=True)
class AgentRelationships:
    """Content-free agent-to-tool/model/data-source edges."""

    tool_refs: tuple[str, ...]
    model_refs: tuple[str, ...]
    data_source_refs: tuple[str, ...]
    tool_inventory_complete: bool
    model_inventory_complete: bool
    data_source_inventory_complete: bool
    relationship_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_refs", _refs(self.tool_refs, "tool_refs", prefixes={"cat"}))
        object.__setattr__(
            self, "model_refs", _refs(self.model_refs, "model_refs", prefixes={"cam"})
        )
        object.__setattr__(
            self,
            "data_source_refs",
            _refs(self.data_source_refs, "data_source_refs", prefixes={"cad"}),
        )
        if not all(
            isinstance(value, bool)
            for value in (
                self.tool_inventory_complete,
                self.model_inventory_complete,
                self.data_source_inventory_complete,
            )
        ):
            raise ValueError("relationship completeness pins must be booleans")
        _integrity(self.relationship_sha256, self.payload(), "relationship_sha256")

    @classmethod
    def build(
        cls,
        *,
        tool_refs: Sequence[str] = (),
        model_refs: Sequence[str] = (),
        data_source_refs: Sequence[str] = (),
        tool_inventory_complete: bool = False,
        model_inventory_complete: bool = False,
        data_source_inventory_complete: bool = False,
    ) -> AgentRelationships:
        tools = tuple(sorted(set(tool_refs)))
        models = tuple(sorted(set(model_refs)))
        sources = tuple(sorted(set(data_source_refs)))
        payload = {
            "schema": "lightwork.cloud-agent.relationships.v2",
            "tool_refs": list(tools),
            "model_refs": list(models),
            "data_source_refs": list(sources),
            "tool_inventory_complete": tool_inventory_complete,
            "model_inventory_complete": model_inventory_complete,
            "data_source_inventory_complete": data_source_inventory_complete,
        }
        return cls(
            tools,
            models,
            sources,
            tool_inventory_complete,
            model_inventory_complete,
            data_source_inventory_complete,
            _sha256(payload),
        )

    def payload(self) -> dict[str, object]:
        return {
            "schema": "lightwork.cloud-agent.relationships.v2",
            "tool_refs": list(self.tool_refs),
            "model_refs": list(self.model_refs),
            "data_source_refs": list(self.data_source_refs),
            "tool_inventory_complete": self.tool_inventory_complete,
            "model_inventory_complete": self.model_inventory_complete,
            "data_source_inventory_complete": self.data_source_inventory_complete,
        }

    def verify(self) -> None:
        _integrity(self.relationship_sha256, self.payload(), "relationship_sha256")


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


def _inventory_manifest_sha256(
    *,
    provider: CloudProvider,
    binding_ref: str,
    binding_revision: int,
    scan_ref: str,
    scope_sha256: str,
    source_snapshot_sha256: str,
    page_count: int,
    snapshot_complete: bool,
    incomplete_reason_codes: Sequence[str],
    captured_at: float,
    items: Sequence[NormalizedCloudAgent],
) -> str:
    return _sha256(
        {
            "schema": "lightwork.cloud-agent.inventory-manifest.v1",
            "provider": provider,
            "binding_ref": binding_ref,
            "binding_revision": binding_revision,
            "scan_ref": scan_ref,
            "scope_sha256": scope_sha256,
            "source_snapshot_sha256": source_snapshot_sha256,
            "page_count": page_count,
            "snapshot_complete": snapshot_complete,
            "incomplete_reason_codes": list(incomplete_reason_codes),
            "captured_at": float(captured_at),
            "item_sha256s": [item.item_sha256 for item in items],
        }
    )


@dataclass(frozen=True)
class InventoryPage:
    """One bounded page of a revision-pinned provider inventory scan."""

    provider: CloudProvider
    binding_ref: str
    binding_revision: int
    scan_ref: str
    scope_sha256: str
    source_snapshot_sha256: str
    manifest_sha256: str
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
        object.__setattr__(
            self,
            "manifest_sha256",
            _digest(self.manifest_sha256, "manifest_sha256"),
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
        if self.page_index < self.page_count - 1 and not self.items:
            raise ValueError("non-final inventory page cannot be empty")
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
        manifest_sha256: str,
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
            "schema": "lightwork.cloud-agent.inventory-page.v2",
            "provider": provider,
            "binding_ref": binding_ref,
            "binding_revision": binding_revision,
            "scan_ref": scan_ref,
            "scope_sha256": scope_sha256,
            "source_snapshot_sha256": source_snapshot_sha256,
            "manifest_sha256": manifest_sha256,
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
            manifest_sha256,
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
            "schema": "lightwork.cloud-agent.inventory-page.v2",
            "provider": self.provider,
            "binding_ref": self.binding_ref,
            "binding_revision": self.binding_revision,
            "scan_ref": self.scan_ref,
            "scope_sha256": self.scope_sha256,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "manifest_sha256": self.manifest_sha256,
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
        _digest(self.tenant_sha256, "tenant_sha256")
        _provider(self.provider)
        _reference(self.binding_ref, "binding_ref", prefixes={"cab"})
        _digest(self.adapter_identity_sha256, "adapter_identity_sha256")
        _revision(self.revision)
        if not isinstance(self.revoked, bool):
            raise CloudAgentIntegrityError("provider binding revoked flag is invalid")
        if self.revoked:
            _code(self.revocation_reason_code, "revocation_reason_code")
        elif self.revocation_reason_code is not None:
            raise CloudAgentIntegrityError(
                "active provider binding cannot include a revocation reason"
            )
        _integrity(self.state_sha256, self.payload(), "binding state_sha256")


@dataclass(frozen=True, repr=False)
class NormalizedInventoryBatch:
    """Server-only staged native bindings plus API-safe normalized pages."""

    pages: tuple[InventoryPage, ...]
    native_bindings: tuple[ProviderNativeAgentBinding, ...] = field(repr=False)


class ProviderInventoryContractAdapter:
    """Hash a validated read-only inventory result into cloud-agent contracts.

    Existing :mod:`maverick.ai_inventory` adapters own network, credential,
    pagination, SSRF, retry, and response-size handling.  This bridge accepts
    only their closed ``DiscoveryResult`` / ``DiscoveredAsset`` types and drops
    provider identifiers, names, URLs, owners, and metadata after deriving
    tenant-bound digests.
    """

    provider: CloudProvider
    source: str
    _lifecycle_states: Mapping[str, LifecycleState]

    def __init__(
        self,
        *,
        pseudonymizer: TenantBoundReferencePseudonymizer,
        revision_authority: ProviderRevisionAuthority,
        max_records_per_sync: int = _DEFAULT_RECORDS_PER_SYNC,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(pseudonymizer, TenantBoundReferencePseudonymizer):
            raise ValueError("an injected provider reference authority is required")
        if not callable(getattr(revision_authority, "resolve_revision", None)):
            raise ValueError("an injected provider revision authority is required")
        if not callable(clock):
            raise ValueError("inventory contract clock is required")
        self._pseudonymizer = pseudonymizer
        self._revision_authority = revision_authority
        self._clock = clock
        self.max_records_per_sync = _bounded_integer(
            max_records_per_sync,
            "max_records_per_sync",
            minimum=1,
            maximum=_MAX_CONFIGURED_RECORDS_PER_SYNC,
        )

    @staticmethod
    def _resource_material(
        authority: TenantAuthority,
        provider: CloudProvider,
        source: str,
        source_id: str,
        scope_sha256: str,
    ) -> dict[str, object]:
        return {
            "schema": "lightwork.cloud-agent.provider-resource.v1",
            "tenant_sha256": authority.tenant_sha256,
            "provider": provider,
            "source": source,
            "source_id": source_id,
            "scope_sha256": scope_sha256,
        }

    def _lifecycle(self, metadata: Mapping[str, object]) -> LifecycleState:
        value = metadata.get("status")
        if not isinstance(value, str):
            return "unknown"
        return self._lifecycle_states.get(value.strip().upper(), "unknown")

    def _resource_kind(self, _asset: object) -> str:
        return "agent"

    def _normalize_asset(
        self,
        authority: TenantAuthority,
        asset: object,
        scope_sha256: str,
    ) -> NormalizedCloudAgent:
        from .ai_inventory import DiscoveredAsset

        if not isinstance(asset, DiscoveredAsset):
            raise CloudAgentAdapterError("inventory result contains an invalid asset")
        if asset.asset_type != "agent" or asset.source != self.source:
            raise CloudAgentBindingError("inventory asset does not match provider contract")
        resource = self._resource_material(
            authority,
            self.provider,
            self.source,
            asset.source_id,
            scope_sha256,
        )
        model_values = {
            value
            for value in (asset.model, *asset.dependencies)
            if isinstance(value, str) and value and value != "unknown"
        }
        model_refs = tuple(
            sorted(
                self._pseudonymizer.reference(
                    "cam",
                    {
                        "schema": "lightwork.cloud-agent.relationship-resource.v1",
                        "tenant_sha256": authority.tenant_sha256,
                        "provider": self.provider,
                        "scope_sha256": scope_sha256,
                        "kind": "model",
                        "source_value": value,
                    },
                )
                for value in model_values
            )
        )
        relationships = AgentRelationships.build(
            model_refs=model_refs,
            tool_inventory_complete=False,
            model_inventory_complete=False,
            data_source_inventory_complete=False,
        )
        source_resource_sha256 = self._pseudonymizer.digest(resource)
        configuration_sha256 = self._pseudonymizer.digest(
            {
                "schema": "lightwork.cloud-agent.configuration-projection.v1",
                "tenant_sha256": authority.tenant_sha256,
                "provider": self.provider,
                "source_resource_sha256": source_resource_sha256,
                "inventory_version_sha256": asset.version_digest,
                "binding_strength": asset.binding_strength,
                "lifecycle_state": self._lifecycle(asset.metadata),
                "model_refs": list(model_refs),
            }
        )
        try:
            source_revision = self._revision_authority.resolve_revision(
                tenant_sha256=authority.tenant_sha256,
                provider=self.provider,
                source_resource_sha256=source_resource_sha256,
                configuration_sha256=configuration_sha256,
            )
        except CloudAgentGovernanceError:
            raise
        except Exception as exc:
            raise CloudAgentAdapterError("provider revision authority failed closed") from exc
        return NormalizedCloudAgent.build(
            provider=self.provider,
            agent_ref=self._pseudonymizer.reference("cag", resource),
            source_resource_sha256=source_resource_sha256,
            source_revision=_revision(source_revision, "source_revision"),
            configuration_sha256=configuration_sha256,
            lifecycle_state=self._lifecycle(asset.metadata),
            relationships=relationships,
            observed_at=asset.last_seen,
        )

    def normalize_discovery(
        self,
        result: object,
        *,
        authority: TenantAuthority,
        binding: ProviderBinding,
        scope_sha256: str,
    ) -> NormalizedInventoryBatch:
        """Return bounded pages from one fully-consumed provider discovery run."""

        from .ai_inventory import DiscoveredAsset, DiscoveryResult

        if not isinstance(authority, TenantAuthority):
            raise CloudAgentBindingError("tenant authority is invalid")
        if not isinstance(binding, ProviderBinding):
            raise CloudAgentBindingError("provider binding is invalid")
        binding.verify()
        if (
            binding.tenant_sha256 != authority.tenant_sha256
            or binding.provider != self.provider
            or binding.revoked
        ):
            raise CloudAgentBindingError("provider inventory binding is inactive or mismatched")
        selected_scope = _digest(scope_sha256, "scope_sha256")
        observed = _timestamp(self._clock(), "clock")
        if not isinstance(result, DiscoveryResult):
            raise CloudAgentAdapterError("inventory adapter returned an invalid result contract")
        _bounded_integer(result.pages, "discovery pages", minimum=1, maximum=10_000)
        if len(result.assets) > self.max_records_per_sync:
            raise CloudAgentStateError("inventory result exceeds configured record bound")
        agents: list[NormalizedCloudAgent] = []
        native_assets: list[DiscoveredAsset] = []
        for asset in result.assets:
            if not isinstance(asset, DiscoveredAsset) or asset.source != self.source:
                raise CloudAgentBindingError("inventory result mixes provider authorities")
            if asset.asset_type == "agent":
                agents.append(self._normalize_asset(authority, asset, selected_scope))
                native_assets.append(asset)
        paired = sorted(zip(agents, native_assets, strict=True), key=lambda item: item[0].agent_ref)
        agents = [item[0] for item in paired]
        native_assets = [item[1] for item in paired]
        refs = [item.agent_ref for item in agents]
        if len(refs) != len(set(refs)):
            raise CloudAgentIdempotencyConflict(
                "inventory result contains duplicate provider resources"
            )
        native_bindings = tuple(
            (
                ProviderNativeAgentBinding(
                    tenant_sha256=authority.tenant_sha256,
                    provider=self.provider,
                    binding_ref=binding.binding_ref,
                    binding_revision=binding.revision,
                    agent_ref=agent.agent_ref,
                    source_resource_sha256=agent.source_resource_sha256,
                    scope_sha256=selected_scope,
                    source_revision=agent.source_revision,
                    configuration_sha256=agent.configuration_sha256,
                    resource_kind=self._resource_kind(native_asset),
                    native_resource_id=native_asset.source_id,
                )
            )
            for agent, native_asset in zip(agents, native_assets, strict=True)
        )
        snapshot_sha256 = _sha256(
            {
                "schema": "lightwork.cloud-agent.provider-snapshot.v1",
                "tenant_sha256": authority.tenant_sha256,
                "provider": self.provider,
                "binding_ref": binding.binding_ref,
                "binding_revision": binding.revision,
                "scope_sha256": selected_scope,
                "provider_pages": result.pages,
                "item_sha256s": [item.item_sha256 for item in agents],
            }
        )
        selected_scan = _new_ref(
            "cas",
            {
                "tenant_sha256": authority.tenant_sha256,
                "provider": self.provider,
                "binding_ref": binding.binding_ref,
                "binding_revision": binding.revision,
                "source_snapshot_sha256": snapshot_sha256,
                "captured_at": observed,
            },
        )
        page_count = max(1, math.ceil(len(agents) / _MAX_PAGE_ITEMS))
        manifest_sha256 = _inventory_manifest_sha256(
            provider=self.provider,
            binding_ref=binding.binding_ref,
            binding_revision=binding.revision,
            scan_ref=selected_scan,
            scope_sha256=selected_scope,
            source_snapshot_sha256=snapshot_sha256,
            page_count=page_count,
            snapshot_complete=True,
            incomplete_reason_codes=(),
            captured_at=observed,
            items=agents,
        )
        pages: list[InventoryPage] = []
        for index in range(page_count):
            items = agents[index * _MAX_PAGE_ITEMS : (index + 1) * _MAX_PAGE_ITEMS]
            continuation = None
            if index < page_count - 1:
                continuation = _sha256(
                    {
                        "scan_ref": selected_scan,
                        "next_page_index": index + 1,
                        "next_item_sha256": agents[(index + 1) * _MAX_PAGE_ITEMS].item_sha256,
                    }
                )
            pages.append(
                InventoryPage.build(
                    provider=self.provider,
                    binding_ref=binding.binding_ref,
                    binding_revision=binding.revision,
                    scan_ref=selected_scan,
                    scope_sha256=selected_scope,
                    source_snapshot_sha256=snapshot_sha256,
                    manifest_sha256=manifest_sha256,
                    page_index=index,
                    page_count=page_count,
                    continuation_sha256=continuation,
                    snapshot_complete=True,
                    incomplete_reason_codes=(),
                    captured_at=observed,
                    items=items,
                )
            )
        return NormalizedInventoryBatch(tuple(pages), native_bindings)


class BedrockInventoryContractAdapter(ProviderInventoryContractAdapter):
    provider: CloudProvider = "aws_bedrock"
    source = "aws_bedrock"
    _lifecycle_states: Mapping[str, LifecycleState] = {
        "PREPARED": "active",
        "NOT_PREPARED": "draft",
        "CREATING": "draft",
        "PREPARING": "draft",
        "UPDATING": "draft",
        "VERSIONING": "draft",
    }

    def _resource_kind(self, _asset: object) -> str:
        return "bedrock_classic_agent"


class AzureAIFoundryInventoryContractAdapter(ProviderInventoryContractAdapter):
    provider: CloudProvider = "azure_ai_foundry"
    source = "azure_ai_foundry"
    _lifecycle_states: Mapping[str, LifecycleState] = {
        "ACTIVE": "active",
        "ENABLED": "active",
        "RUNNING": "active",
        "DISABLED": "disabled",
        "STOPPED": "disabled",
        "CREATING": "draft",
        "DRAFT": "draft",
        "UPDATING": "draft",
    }

    def _resource_kind(self, asset: object) -> str:
        from .ai_inventory import DiscoveredAsset

        if not isinstance(asset, DiscoveredAsset):
            return "azure_agent_unknown"
        kind = asset.metadata.get("kind")
        if isinstance(kind, str) and kind.replace("_", "").upper() == "HOSTEDAGENT":
            return "azure_hosted_agent"
        return "azure_agent_unknown"


class VertexAIInventoryContractAdapter(ProviderInventoryContractAdapter):
    provider: CloudProvider = "google_vertex_ai"
    source = "google_vertex_ai"
    _lifecycle_states: Mapping[str, LifecycleState] = {
        "ACTIVE": "active",
        "READY": "active",
        "RUNNING": "active",
        "DISABLED": "disabled",
        "STOPPED": "disabled",
        "CREATING": "draft",
        "PENDING": "draft",
        "UPDATING": "draft",
    }

    def _resource_kind(self, _asset: object) -> str:
        return "vertex_reasoning_engine"


def build_provider_inventory_contract_adapter(
    provider: CloudProvider,
    *,
    pseudonymizer: TenantBoundReferencePseudonymizer,
    revision_authority: ProviderRevisionAuthority,
    max_records_per_sync: int = _DEFAULT_RECORDS_PER_SYNC,
    clock: Callable[[], float] = time.time,
) -> ProviderInventoryContractAdapter:
    """Build a provider-specific bridge over the existing read-only adapters."""

    factories: dict[CloudProvider, type[ProviderInventoryContractAdapter]] = {
        "aws_bedrock": BedrockInventoryContractAdapter,
        "azure_ai_foundry": AzureAIFoundryInventoryContractAdapter,
        "google_vertex_ai": VertexAIInventoryContractAdapter,
    }
    selected = _provider(provider)
    return factories[selected](
        pseudonymizer=pseudonymizer,
        revision_authority=revision_authority,
        max_records_per_sync=max_records_per_sync,
        clock=clock,
    )


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
    reconciliation_evidence_ref: str | None
    reconciliation_evidence_sha256: str | None
    reconciled_by_ref: str | None
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
            "reconciliation_evidence_ref": self.reconciliation_evidence_ref,
            "reconciliation_evidence_sha256": self.reconciliation_evidence_sha256,
            "reconciled_by_ref": self.reconciled_by_ref,
        }

    def verify(self) -> None:
        _digest(self.tenant_sha256, "tenant_sha256")
        _provider(self.provider)
        _reference(self.intent_ref, "intent_ref", prefixes={"cei"})
        _reference(self.evaluation_ref, "evaluation_ref", prefixes={"cpe"})
        _digest(self.evaluation_sha256, "evaluation_sha256")
        _reference(self.binding_ref, "binding_ref", prefixes={"cab"})
        _revision(self.binding_revision, "binding_revision")
        _reference(self.agent_ref, "agent_ref", prefixes={"cag"})
        _revision(self.agent_revision, "agent_revision")
        _digest(self.configuration_sha256, "configuration_sha256")
        _revision(self.inventory_revision, "inventory_revision")
        _reference(
            self.target_ref,
            "target_ref",
            prefixes={"cag", "cad", "cam", "cat"},
        )
        if self.status not in {
            "adapter_accepted",
            "adapter_rejected",
            "adapter_reported_no_change",
            "dispatching",
            "prepared",
            "reconciled_applied",
            "reconciled_no_change",
            "reconciliation_required",
            "retryable",
        }:
            raise CloudAgentIntegrityError("enforcement intent status is unsupported")
        _revision(self.revision, "intent revision")
        _bounded_integer(
            self.attempts,
            "attempts",
            minimum=0,
            maximum=_MAX_DISPATCH_ATTEMPTS,
        )
        if self.last_receipt_sha256 is not None:
            _digest(self.last_receipt_sha256, "last_receipt_sha256")
        if self.reconciliation_evidence_ref is None:
            if (
                self.reconciliation_evidence_sha256 is not None
                or self.reconciled_by_ref is not None
            ):
                raise CloudAgentIntegrityError("reconciliation evidence binding is incomplete")
        else:
            _reference(
                self.reconciliation_evidence_ref,
                "reconciliation_evidence_ref",
                prefixes={"evd"},
            )
            _digest(
                self.reconciliation_evidence_sha256,
                "reconciliation_evidence_sha256",
            )
            _reference(self.reconciled_by_ref, "reconciled_by_ref", prefixes={"prn"})
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
    effect_sha256: str
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
            "effect_sha256": self.effect_sha256,
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
    effect_sha256: str
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
            "effect_sha256": self.effect_sha256,
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
        _digest(self.effect_sha256, "effect_sha256")
        _digest(self.request_sha256, "request_sha256")
        if self.disposition not in {
            "accepted",
            "ambiguous",
            "no_change",
            "rejected",
            "retryable",
        }:
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

    def __post_init__(self) -> None:
        if not isinstance(self.intent, EnforcementIntent):
            raise CloudAgentIntegrityError("dispatch receipt intent is invalid")
        self.intent.verify()
        if not isinstance(self.adapter_result, ProviderAdapterResult):
            raise CloudAgentIntegrityError("dispatch receipt adapter result is invalid")
        self.adapter_result.verify()
        if self.provider_write_verified is not False:
            raise CloudAgentIntegrityError("adapter receipt cannot claim a verified provider write")
        if self.limit_notice != CLOUD_AGENT_LIMIT_NOTICE:
            raise CloudAgentIntegrityError("adapter receipt limit notice is invalid")


ProviderOperation = Literal[
    "aws.bedrock_agent.disable_action_group",
    "aws.bedrock_agent.disassociate_knowledge_base",
    "azure.foundry_agents.disable",
]


@dataclass(frozen=True)
class ProviderMutationOutcome:
    """Content-free outcome from a deployment-owned resource/credential resolver."""

    tenant_sha256: str
    provider: CloudProvider
    binding_ref: str
    binding_revision: int
    intent_ref: str
    effect_sha256: str
    request_sha256: str
    operation: ProviderOperation
    disposition: AdapterDisposition
    receipt_sha256: str
    observed_at: float

    def verify(self) -> None:
        _digest(self.tenant_sha256, "tenant_sha256")
        _provider(self.provider)
        _reference(self.binding_ref, "binding_ref", prefixes={"cab"})
        _revision(self.binding_revision, "binding_revision")
        _reference(self.intent_ref, "intent_ref", prefixes={"cei"})
        _digest(self.effect_sha256, "effect_sha256")
        _digest(self.request_sha256, "request_sha256")
        _code(self.operation, "provider operation")
        if self.disposition not in {
            "accepted",
            "ambiguous",
            "no_change",
            "rejected",
            "retryable",
        }:
            raise CloudAgentAdapterError("provider mutation disposition is unsupported")
        _digest(self.receipt_sha256, "receipt_sha256")
        _timestamp(self.observed_at, "observed_at")


class ProviderMutationTransport(Protocol):
    def resolve_and_submit(
        self,
        *,
        operation: ProviderOperation,
        request: EnforcementDispatchRequest,
        resolved: ResolvedProviderDispatch,
    ) -> ProviderMutationOutcome:
        """Re-resolve current tenant resources/credentials and submit idempotently.

        The implementation must bind native identifiers to the exact tenant,
        provider binding, agent/configuration revision, target relationship,
        and stable ``effect_sha256`` before issuing a fixed provider operation.
        It must not return provider payload content.
        """


class AdapterReceiptSigner(Protocol):
    def sign(self, payload: bytes) -> str:
        """Return a lowercase hex signature without exposing signing material."""


class AdapterReceiptSignatureVerifier(Protocol):
    def verify(self, payload: bytes, signature: str) -> bool:
        """Verify one normalized adapter receipt signature."""


class HMACAdapterReceiptAuthenticator:
    """Injected-key reference signer for local deployments and contract tests."""

    __slots__ = ("_key",)

    def __init__(self, key: bytes) -> None:
        if not isinstance(key, bytes) or not 32 <= len(key) <= 1_024:
            raise ValueError("adapter receipt authentication requires a 32-byte key")
        self._key = key

    def __reduce__(self) -> object:
        raise TypeError("adapter receipt authenticator cannot be serialized")

    def sign(self, payload: bytes) -> str:
        if not isinstance(payload, bytes) or not payload:
            raise ValueError("adapter receipt signing payload is invalid")
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def verify(self, payload: bytes, signature: str) -> bool:
        if not isinstance(payload, bytes) or not isinstance(signature, str):
            return False
        expected = hmac.new(self._key, payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


def build_adapter_result_verifier(
    verifier: AdapterReceiptSignatureVerifier,
) -> AdapterResultVerifier:
    if not callable(getattr(verifier, "verify", None)):
        raise ValueError("adapter receipt signature verifier is required")

    def verify(_binding: ProviderBinding, result: ProviderAdapterResult) -> bool:
        try:
            return verifier.verify(_canonical(result.signed_payload()), result.signature) is True
        except Exception:
            return False

    return verify


class ContractSafeProviderEnforcementAdapter:
    """Fixed-operation adapter over a deployment-owned exact resource resolver."""

    provider: CloudProvider
    operations: Mapping[EnforcementAction, ProviderOperation]
    operation_resource_kinds: Mapping[ProviderOperation, frozenset[str]]
    operation_target_kinds: Mapping[ProviderOperation, frozenset[str]]

    def __init__(
        self,
        *,
        tenant_sha256: str,
        binding_ref: str,
        binding_revision: int,
        resource_registry: ProviderResourceRegistry,
        transport: ProviderMutationTransport,
        signer: AdapterReceiptSigner,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._tenant_sha256 = _digest(tenant_sha256, "tenant_sha256")
        self._binding_ref = _reference(binding_ref, "binding_ref", prefixes={"cab"})
        self._binding_revision = _revision(binding_revision, "binding_revision")
        if not callable(getattr(resource_registry, "resolve_dispatch", None)):
            raise ValueError("provider resource registry is required")
        if not callable(getattr(transport, "resolve_and_submit", None)):
            raise ValueError("provider mutation transport is required")
        if not callable(getattr(signer, "sign", None)):
            raise ValueError("adapter receipt signer is required")
        if not callable(clock):
            raise ValueError("adapter clock is required")
        self._resource_registry = resource_registry
        self._transport = transport
        self._signer = signer
        self._clock = clock

    def _validate_request(self, request: EnforcementDispatchRequest) -> None:
        if not isinstance(request, EnforcementDispatchRequest):
            raise CloudAgentAdapterError("provider dispatch request is invalid")
        if (
            request.tenant_sha256 != self._tenant_sha256
            or request.provider != self.provider
            or request.binding_ref != self._binding_ref
            or request.binding_revision != self._binding_revision
        ):
            raise CloudAgentBindingError("provider dispatch request binding is stale")
        _reference(request.intent_ref, "intent_ref", prefixes={"cei"})
        _digest(request.intent_sha256, "intent_sha256")
        _digest(request.effect_sha256, "effect_sha256")
        _reference(request.agent_ref, "agent_ref", prefixes={"cag"})
        _revision(request.agent_revision, "agent_revision")
        _digest(request.configuration_sha256, "configuration_sha256")
        _bounded_integer(
            request.attempt,
            "attempt",
            minimum=1,
            maximum=_MAX_DISPATCH_ATTEMPTS,
        )
        if not secrets_compare_digest(request.request_sha256, _sha256(request.signing_payload())):
            raise CloudAgentIntegrityError("provider dispatch request digest is invalid")

    def _signed_result(
        self,
        request: EnforcementDispatchRequest,
        *,
        disposition: AdapterDisposition,
        receipt_sha256: str,
        observed_at: float,
    ) -> ProviderAdapterResult:
        values: dict[str, object] = {
            "tenant_sha256": request.tenant_sha256,
            "provider": request.provider,
            "binding_ref": request.binding_ref,
            "binding_revision": request.binding_revision,
            "intent_ref": request.intent_ref,
            "effect_sha256": request.effect_sha256,
            "request_sha256": request.request_sha256,
            "disposition": disposition,
            "receipt_sha256": receipt_sha256,
            "observed_at": observed_at,
        }
        signed_payload = {
            "schema": "lightwork.cloud-agent.adapter-result.v1",
            **values,
        }
        try:
            signature = self._signer.sign(_canonical(signed_payload))
        except Exception as exc:
            raise CloudAgentAdapterError("adapter receipt signer failed closed") from exc
        result = ProviderAdapterResult.build(**values, signature=signature)
        result.verify()
        return result

    def submit(self, request: EnforcementDispatchRequest) -> ProviderAdapterResult:
        self._validate_request(request)
        operation = self.operations.get(request.action)
        if operation is None:
            return self._signed_result(
                request,
                disposition="rejected",
                receipt_sha256=_sha256(
                    {
                        "schema": "lightwork.cloud-agent.unsupported-operation.v1",
                        "provider": self.provider,
                        "effect_sha256": request.effect_sha256,
                        "action": request.action,
                    }
                ),
                observed_at=_timestamp(self._clock(), "clock"),
            )
        try:
            resolved = self._resource_registry.resolve_dispatch(request)
            resolved.verify()
            if resolved.agent.resource_kind not in self.operation_resource_kinds.get(
                operation, frozenset()
            ) or resolved.target_kind not in self.operation_target_kinds.get(
                operation, frozenset()
            ):
                raise CloudAgentBindingError("provider resource kind does not support operation")
        except (CloudAgentBindingError, CloudAgentStateError):
            return self._signed_result(
                request,
                disposition="rejected",
                receipt_sha256=_sha256(
                    {
                        "schema": "lightwork.cloud-agent.unresolved-operation.v1",
                        "provider": self.provider,
                        "effect_sha256": request.effect_sha256,
                        "operation": operation,
                    }
                ),
                observed_at=_timestamp(self._clock(), "clock"),
            )
        try:
            outcome = self._transport.resolve_and_submit(
                operation=operation,
                request=request,
                resolved=resolved,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise CloudAgentAdapterError(
                "provider mutation transport failed after dispatch"
            ) from exc
        if not isinstance(outcome, ProviderMutationOutcome):
            raise CloudAgentAdapterError("provider mutation returned an invalid contract")
        outcome.verify()
        if (
            outcome.tenant_sha256 != request.tenant_sha256
            or outcome.provider != request.provider
            or outcome.binding_ref != request.binding_ref
            or outcome.binding_revision != request.binding_revision
            or outcome.intent_ref != request.intent_ref
            or outcome.effect_sha256 != request.effect_sha256
            or outcome.request_sha256 != request.request_sha256
            or outcome.operation != operation
        ):
            raise CloudAgentBindingError("provider mutation outcome is forged or stale")
        return self._signed_result(
            request,
            disposition=outcome.disposition,
            receipt_sha256=outcome.receipt_sha256,
            observed_at=outcome.observed_at,
        )


class BedrockEnforcementAdapter(ContractSafeProviderEnforcementAdapter):
    provider: CloudProvider = "aws_bedrock"
    operations: Mapping[EnforcementAction, ProviderOperation] = {
        "detach_data_source": "aws.bedrock_agent.disassociate_knowledge_base",
        "restrict_tool": "aws.bedrock_agent.disable_action_group",
    }
    operation_resource_kinds: Mapping[ProviderOperation, frozenset[str]] = {
        "aws.bedrock_agent.disassociate_knowledge_base": frozenset({"bedrock_classic_agent"}),
        "aws.bedrock_agent.disable_action_group": frozenset({"bedrock_classic_agent"}),
    }
    operation_target_kinds: Mapping[ProviderOperation, frozenset[str]] = {
        "aws.bedrock_agent.disassociate_knowledge_base": frozenset({"data_source"}),
        "aws.bedrock_agent.disable_action_group": frozenset({"tool"}),
    }


class AzureAIFoundryEnforcementAdapter(ContractSafeProviderEnforcementAdapter):
    provider: CloudProvider = "azure_ai_foundry"
    operations: Mapping[EnforcementAction, ProviderOperation] = {
        "disable_agent": "azure.foundry_agents.disable",
    }
    operation_resource_kinds: Mapping[ProviderOperation, frozenset[str]] = {
        "azure.foundry_agents.disable": frozenset({"azure_hosted_agent"}),
    }
    operation_target_kinds: Mapping[ProviderOperation, frozenset[str]] = {
        "azure.foundry_agents.disable": frozenset({"agent"}),
    }


class VertexAIEnforcementAdapter(ContractSafeProviderEnforcementAdapter):
    """Vertex has no safe generic reversible mapping for the five core actions."""

    provider: CloudProvider = "google_vertex_ai"
    operations: Mapping[EnforcementAction, ProviderOperation] = {}
    operation_resource_kinds: Mapping[ProviderOperation, frozenset[str]] = {}
    operation_target_kinds: Mapping[ProviderOperation, frozenset[str]] = {}


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
        object.__setattr__(self, "relation_code", _code(self.relation_code, "relation_code"))

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


class DatabricksFrameworkContractAdapter:
    """Strict parser for licensed-text-free Databricks control mappings."""

    _ROOT_FIELDS = frozenset(
        {"schema", "framework_id", "version", "source_revision_sha256", "mappings"}
    )
    _MAPPING_FIELDS = frozenset({"control_id", "lightwork_control_ids", "applicability_tags"})

    @staticmethod
    def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("framework document contains duplicate fields")
            result[key] = value
        return result

    def parse_document(self, document: bytes) -> DatabricksAISecurityFrameworkPack:
        if (
            not isinstance(document, bytes)
            or not 1 <= len(document) <= _MAX_FRAMEWORK_DOCUMENT_BYTES
        ):
            raise ValueError("framework document is empty or over bound")
        try:
            value = json.loads(
                document.decode("utf-8"),
                object_pairs_hook=self._pairs,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    ValueError("non-finite JSON value")
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("framework document is not strict JSON") from exc
        if not isinstance(value, Mapping):
            raise ValueError("framework document root must be an object")
        return self.normalize_mapping(cast(Mapping[str, object], value))

    def normalize_mapping(self, value: Mapping[str, object]) -> DatabricksAISecurityFrameworkPack:
        raw = _closed_mapping(
            value,
            "framework document",
            allowed=self._ROOT_FIELDS,
        )
        if raw.get("schema") != "lightwork.databricks-ai-security-framework-map.v1":
            raise ValueError("framework document schema is unsupported")
        if raw.get("framework_id") != "databricks_ai_security_framework":
            raise ValueError("framework document authority is unsupported")
        rows = raw.get("mappings")
        if (
            isinstance(rows, (str, bytes))
            or not isinstance(rows, Sequence)
            or not rows
            or len(rows) > _MAX_FRAMEWORK_MAPPINGS
        ):
            raise ValueError("framework mappings are empty or over bound")
        mappings: list[FrameworkControlMapping] = []
        for row in rows:
            selected = _closed_mapping(
                row,
                "framework mapping",
                allowed=self._MAPPING_FIELDS,
            )
            controls = selected.get("lightwork_control_ids")
            tags = selected.get("applicability_tags")
            if (
                isinstance(controls, (str, bytes))
                or not isinstance(controls, Sequence)
                or isinstance(tags, (str, bytes))
                or not isinstance(tags, Sequence)
            ):
                raise ValueError("framework mapping arrays are invalid")
            mappings.append(
                FrameworkControlMapping(
                    selected.get("control_id"),  # type: ignore[arg-type]
                    tuple(controls),  # type: ignore[arg-type]
                    tuple(tags),  # type: ignore[arg-type]
                )
            )
        return DatabricksAISecurityFrameworkPack.build(
            version=raw.get("version"),  # type: ignore[arg-type]
            source_revision_sha256=raw.get("source_revision_sha256"),  # type: ignore[arg-type]
            mappings=mappings,
        )


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

    def __post_init__(self) -> None:
        self._validate_contract()

    def _validate_contract(self) -> None:
        _digest(self.tenant_sha256, "tenant_sha256")
        _reference(self.review_ref, "review_ref", prefixes={"fwr"})
        if not isinstance(self.pack_version, str) or not _VERSION_RE.fullmatch(self.pack_version):
            raise CloudAgentIntegrityError("framework review version is invalid")
        _digest(self.pack_sha256, "pack_sha256")
        _reference(self.reviewer_ref, "reviewer_ref", prefixes={"prn"})
        if self.decision not in {"approved", "rejected"}:
            raise CloudAgentIntegrityError("framework review decision is invalid")
        if not self.applicability or len(self.applicability) > _MAX_FRAMEWORK_MAPPINGS:
            raise CloudAgentIntegrityError("framework review applicability is invalid")
        keys: list[tuple[str, str]] = []
        for item in self.applicability:
            if not isinstance(item, FrameworkApplicability):
                raise CloudAgentIntegrityError("framework review applicability is invalid")
            item.__post_init__()
            keys.append((item.control_id, item.workload_ref))
        if keys != sorted(set(keys)):
            raise CloudAgentIntegrityError(
                "framework review applicability must be sorted and unique"
            )
        _timestamp(self.reviewed_at, "reviewed_at")
        _revision(self.revision, "framework review revision")

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
        self._validate_contract()
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

    def __post_init__(self) -> None:
        self._validate_contract()

    def _validate_contract(self) -> None:
        _digest(self.tenant_sha256, "tenant_sha256")
        _reference(self.attestation_ref, "attestation_ref", prefixes={"fwa"})
        _reference(self.review_ref, "review_ref", prefixes={"fwr"})
        _digest(self.review_sha256, "review_sha256")
        _reference(self.attestor_ref, "attestor_ref", prefixes={"prn"})
        if not self.evidence_links or len(self.evidence_links) > _MAX_EVIDENCE_LINKS:
            raise CloudAgentIntegrityError("framework attestation evidence is invalid")
        keys: list[tuple[str, str]] = []
        for link in self.evidence_links:
            if not isinstance(link, FrameworkEvidenceLink):
                raise CloudAgentIntegrityError("framework attestation evidence is invalid")
            link.__post_init__()
            keys.append((link.evidence_ref, link.relation_code))
        if keys != sorted(set(keys)):
            raise CloudAgentIntegrityError(
                "framework attestation evidence must be sorted and unique"
            )
        _timestamp(self.attested_at, "attested_at")
        _revision(self.revision, "framework attestation revision")
        if self.certification_claim is not False:
            raise CloudAgentIntegrityError("framework attestation cannot claim certification")
        if self.limit_notice != FRAMEWORK_LIMIT_NOTICE:
            raise CloudAgentIntegrityError("framework attestation limit notice is invalid")

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
        self._validate_contract()
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

    def __post_init__(self) -> None:
        self._validate_contract()

    def _validate_contract(self) -> None:
        _digest(self.tenant_sha256, "tenant_sha256")
        if not isinstance(self.version, str) or not _VERSION_RE.fullmatch(self.version):
            raise CloudAgentIntegrityError("framework activation version is invalid")
        _digest(self.pack_sha256, "pack_sha256")
        _reference(self.review_ref, "review_ref", prefixes={"fwr"})
        _digest(self.review_sha256, "review_sha256")
        _reference(self.attestation_ref, "attestation_ref", prefixes={"fwa"})
        _digest(self.attestation_sha256, "attestation_sha256")
        if self.prior_version is not None and (
            not isinstance(self.prior_version, str) or not _VERSION_RE.fullmatch(self.prior_version)
        ):
            raise CloudAgentIntegrityError("framework prior version is invalid")
        if self.action not in {"activate", "rollback"}:
            raise CloudAgentIntegrityError("framework activation action is invalid")
        _timestamp(self.activated_at, "activated_at")
        _revision(self.revision, "framework activation revision")
        if self.certification_claim is not False:
            raise CloudAgentIntegrityError("framework activation cannot claim certification")
        if self.limit_notice != FRAMEWORK_LIMIT_NOTICE:
            raise CloudAgentIntegrityError("framework activation limit notice is invalid")

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
        self._validate_contract()
        _integrity(self.activation_sha256, self.payload(), "activation_sha256")


@dataclass
class _ScanState:
    page_count: int
    metadata_sha256: str
    base_inventory_revision: int
    created_at: float
    updated_at: float
    item_count: int = 0
    pages: dict[int, InventoryPage] = field(default_factory=dict)


@dataclass(frozen=True)
class _FinalizedScan:
    manifest_sha256: str
    inventory_revision: int
    captured_at: float


@dataclass(frozen=True)
class _ScopeSnapshot:
    source_snapshot_sha256: str
    captured_at: float


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
    inventory_scopes: dict[CloudProvider, dict[str, str]] = field(default_factory=dict)
    inventory_metadata: dict[CloudProvider, InventoryMetadata] = field(default_factory=dict)
    scans: dict[tuple[CloudProvider, str], _ScanState] = field(default_factory=dict)
    finalized_scans: dict[tuple[CloudProvider, str], _FinalizedScan] = field(default_factory=dict)
    scope_snapshots: dict[tuple[CloudProvider, str], _ScopeSnapshot] = field(default_factory=dict)
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
        enabled_providers: Sequence[CloudProvider] = CLOUD_PROVIDERS,
        databricks_framework_enabled: bool = False,
        policy_evaluator: CloudAgentPolicyEvaluator | None = None,
        clock: Callable[[], float],
        max_tenants: int = 1_000,
        max_records_per_sync: int = _DEFAULT_RECORDS_PER_SYNC,
        max_events_per_batch: int = _DEFAULT_EVENTS_PER_BATCH,
        retention_seconds: int = _DEFAULT_RETENTION_SECONDS,
        max_active_scans_per_tenant: int = _MAX_ACTIVE_SCANS_PER_TENANT,
    ) -> None:
        self._enabled = enabled is True
        if isinstance(enabled_providers, (str, bytes)):
            raise ValueError("enabled_providers must be a closed provider sequence")
        selected_providers = tuple(_provider(item) for item in enabled_providers)
        if len(selected_providers) != len(set(selected_providers)):
            raise ValueError("enabled_providers must be unique")
        self._enabled_providers = frozenset(selected_providers)
        self._databricks_framework_enabled = databricks_framework_enabled is True
        self._policy_evaluator = policy_evaluator
        if not callable(clock):
            raise ValueError("cloud-agent authority clock is required")
        self._clock = clock
        self._max_tenants = _revision(max_tenants, "max_tenants")
        self._max_records_per_sync = _bounded_integer(
            max_records_per_sync,
            "max_records_per_sync",
            minimum=1,
            maximum=_MAX_CONFIGURED_RECORDS_PER_SYNC,
        )
        self._max_events_per_batch = _bounded_integer(
            max_events_per_batch,
            "max_events_per_batch",
            minimum=1,
            maximum=_MAX_CONFIGURED_EVENTS_PER_BATCH,
        )
        self._retention_seconds = _bounded_integer(
            retention_seconds,
            "retention_seconds",
            minimum=_MIN_RETENTION_SECONDS,
            maximum=_MAX_RETENTION_SECONDS,
        )
        self._scan_ttl_seconds = min(self._retention_seconds, 3_600)
        self._max_active_scans_per_tenant = _bounded_integer(
            max_active_scans_per_tenant,
            "max_active_scans_per_tenant",
            minimum=1,
            maximum=1_000,
        )
        self._tenants: dict[str, _TenantState] = {}
        self._agent_tenants: dict[str, str] = {}
        self._lock = threading.RLock()

    @classmethod
    def from_config(
        cls,
        config: CloudAgentGovernanceConfig,
        *,
        policy_evaluator: CloudAgentPolicyEvaluator | None = None,
        clock: Callable[[], float] = time.time,
        max_tenants: int = 1_000,
    ) -> CloudAgentGovernanceAuthority:
        if not isinstance(config, CloudAgentGovernanceConfig):
            raise CloudAgentConfigError("cloud-agent governance config is invalid")
        return cls(
            enabled=config.enable,
            enabled_providers=config.enabled_providers,
            databricks_framework_enabled=config.databricks_framework_enable,
            policy_evaluator=policy_evaluator,
            clock=clock,
            max_tenants=max_tenants,
            max_records_per_sync=config.max_records_per_sync,
            max_events_per_batch=config.max_events_per_batch,
            retention_seconds=config.retention_seconds,
        )

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
        if selected_provider not in self._enabled_providers:
            raise CloudAgentGovernanceDisabled(
                "cloud-agent provider is not enabled by deployment policy"
            )
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

    def resolve_provider_binding(
        self,
        authority: TenantAuthority,
        *,
        provider: CloudProvider,
        require_active: bool = True,
    ) -> ProviderBinding:
        """Resolve the exact current binding instead of trusting a caller snapshot."""

        selected_provider = _provider(provider)
        if not isinstance(require_active, bool):
            raise ValueError("require_active must be a boolean")
        with self._lock:
            state = self._tenant(authority)
            runtime = (
                self._active_runtime(state, selected_provider)
                if require_active
                else self._runtime(state, selected_provider)
            )
            runtime.binding.verify()
            return runtime.binding

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
            now = _timestamp(self._clock(), "clock")
            self._purge_expired_scans(state, now=now)
            if (
                page.captured_at < now - self._retention_seconds
                or page.captured_at > now + _MAX_CLOCK_SKEW_SECONDS
            ):
                raise CloudAgentStaleRevision("inventory snapshot time is outside policy")
            current_revision = self._inventory_revision(state, page.provider)
            if current_revision != expected:
                raise CloudAgentStaleRevision("inventory revision is stale")
            prior_scope = state.scope_snapshots.get((page.provider, page.scope_sha256))
            if prior_scope is not None:
                if page.source_snapshot_sha256 == prior_scope.source_snapshot_sha256:
                    raise CloudAgentIdempotencyConflict(
                        "provider source snapshot is already finalized"
                    )
                if page.captured_at < prior_scope.captured_at:
                    raise CloudAgentStaleRevision("inventory snapshot is older than current state")
                if (
                    page.captured_at == prior_scope.captured_at
                    and page.source_snapshot_sha256 != prior_scope.source_snapshot_sha256
                ):
                    raise CloudAgentStaleRevision("inventory snapshot order is ambiguous")
            scan_key = (page.provider, page.scan_ref)
            if scan_key in state.finalized_scans:
                raise CloudAgentIdempotencyConflict("finalized inventory scan cannot be replayed")
            scan = state.scans.get(scan_key)
            metadata_sha256 = self._scan_metadata_sha256(page)
            if scan is None:
                if len(state.scans) >= self._max_active_scans_per_tenant:
                    raise CloudAgentStateError("active inventory scan bound is exhausted")
                if page.page_count > self._max_records_per_sync + 1:
                    raise CloudAgentStateError("inventory scan page bound is exhausted")
                scan = _ScanState(
                    page.page_count,
                    metadata_sha256,
                    expected,
                    now,
                    now,
                )
                state.scans[scan_key] = scan
            else:
                if scan.base_inventory_revision != expected:
                    raise CloudAgentStaleRevision("inventory scan base revision is stale")
                if scan.page_count != page.page_count or not secrets_compare_digest(
                    scan.metadata_sha256, metadata_sha256
                ):
                    raise CloudAgentIdempotencyConflict("scan reference metadata collision")
            prior_page = scan.pages.get(page.page_index)
            if prior_page is not None and not secrets_compare_digest(
                prior_page.page_sha256, page.page_sha256
            ):
                raise CloudAgentIdempotencyConflict("scan page identity collision")
            if prior_page is None:
                if scan.item_count + len(page.items) > self._max_records_per_sync:
                    raise CloudAgentStateError("inventory scan exceeds configured record bound")
                scan.item_count += len(page.items)
            scan.pages[page.page_index] = page
            scan.updated_at = now
            finalized = len(scan.pages) == scan.page_count
            inventory_revision = current_revision
            if finalized:
                if len(state.finalized_scans) >= _MAX_IDEMPOTENCY_RECORDS:
                    raise CloudAgentStateError("finalized inventory scan bound is exhausted")
                inventory_revision = self._finalize_scan(state, page.provider, scan)
                state.finalized_scans[scan_key] = _FinalizedScan(
                    page.manifest_sha256,
                    inventory_revision,
                    page.captured_at,
                )
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
                selected_offset + len(page) if selected_offset + len(page) < len(records) else None
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
        expected = _revision(expected_stream_revision, "expected_stream_revision", allow_zero=True)
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

    def ingest_observability_batch(
        self,
        authority: TenantAuthority,
        events: Sequence[CloudAgentObservabilityEvent],
        *,
        expected_stream_revision: int,
        idempotency_key: str,
    ) -> tuple[ObservabilityReceipt, ...]:
        """Atomically admit one bounded, exact-revision observability batch."""

        if (
            isinstance(events, (str, bytes))
            or not events
            or len(events) > self._max_events_per_batch
        ):
            raise CloudAgentStateError("observability batch is empty or over bound")
        selected = tuple(events)
        for event in selected:
            if not isinstance(event, CloudAgentObservabilityEvent):
                raise ValueError("observability batch contains an invalid event")
            event.verify()
        event_ids = [event.event_sha256 for event in selected]
        if len(event_ids) != len(set(event_ids)):
            raise CloudAgentIdempotencyConflict(
                "observability batch contains duplicate event identities"
            )
        expected = _revision(
            expected_stream_revision,
            "expected_stream_revision",
            allow_zero=True,
        )
        semantic = {
            "event_integrity_sha256s": [event.integrity_sha256 for event in selected],
            "expected_stream_revision": expected,
        }
        with self._lock:
            state = self._tenant(authority)
            replay = self._replay(
                state,
                "ingest_observability_batch",
                idempotency_key,
                semantic,
            )
            if replay is not None:
                return replay  # type: ignore[return-value]
            if state.event_stream_revision != expected:
                raise CloudAgentStaleRevision("observability stream revision is stale")
            additions: list[CloudAgentObservabilityEvent] = []
            duplicate_flags: list[bool] = []
            for event in selected:
                runtime = self._active_runtime(state, event.provider)
                self._assert_event_binding(event, runtime.binding)
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
                    duplicate_flags.append(True)
                else:
                    additions.append(event)
                    duplicate_flags.append(False)
            if len(state.events) + len(additions) > _MAX_EVENTS_PER_TENANT:
                raise CloudAgentStateError("observability event bound is exhausted")
            next_revision = state.event_stream_revision
            receipts: list[ObservabilityReceipt] = []
            for event, duplicate in zip(selected, duplicate_flags, strict=True):
                if not duplicate:
                    state.events[event.event_sha256] = event
                    next_revision += 1
                payload = {
                    "schema": "lightwork.cloud-agent.observability-receipt.v1",
                    "event_sha256": event.event_sha256,
                    "stream_revision": next_revision,
                    "duplicate": duplicate,
                }
                receipts.append(
                    ObservabilityReceipt(
                        event.event_sha256,
                        next_revision,
                        duplicate,
                        _sha256(payload),
                    )
                )
            state.event_stream_revision = next_revision
            result = tuple(receipts)
            self._remember(
                state,
                "ingest_observability_batch",
                idempotency_key,
                semantic,
                result,
            )
            return result

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
        target = _reference(target_ref, "target_ref", prefixes={"cag", "cad", "cam", "cat"})
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
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise CloudAgentAdapterError("policy evaluator failed closed") from exc
        decision, selected_action, selected_target, reasons = self._validate_evaluation_result(
            result, request
        )

        with self._lock:
            state = self._tenant(authority)
            replay = self._replay(state, "evaluate_policy", idempotency_key, semantic)
            if replay is not None:
                return replay  # type: ignore[return-value]
            record = self._agent(state, authority.tenant_sha256, selected_agent)
            self._assert_agent_pins(
                record,
                agent_revision=request.agent_revision,
                configuration_sha256=request.configuration_sha256,
                inventory_revision=request.inventory_revision,
            )
            if record.agent.relationships.relationship_sha256 != request.relationship_sha256:
                raise CloudAgentStaleRevision(
                    "cloud-agent relationships changed during policy evaluation"
                )
            self._assert_target(record.agent, action, target)
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
                None,
                None,
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
            effect_sha256 = _sha256(
                {
                    "schema": "lightwork.cloud-agent.enforcement-effect.v1",
                    "tenant_sha256": authority.tenant_sha256,
                    "intent_sha256": intent.immutable_sha256,
                }
            )
            request_provisional = EnforcementDispatchRequest(
                tenant_sha256=authority.tenant_sha256,
                provider=intent.provider,
                binding_ref=intent.binding_ref,
                binding_revision=intent.binding_revision,
                intent_ref=intent.intent_ref,
                intent_sha256=intent.immutable_sha256,
                effect_sha256=effect_sha256,
                action=intent.action,
                target_ref=intent.target_ref,
                agent_ref=intent.agent_ref,
                agent_revision=intent.agent_revision,
                configuration_sha256=intent.configuration_sha256,
                attempt=attempt,
                request_sha256="0" * 64,
            )
            request = replace(
                request_provisional,
                request_sha256=_sha256(request_provisional.signing_payload()),
            )
            claimed = replace(
                intent,
                status="dispatching",
                revision=intent.revision + 1,
                attempts=attempt,
                last_receipt_sha256=None,
                state_sha256="0" * 64,
            )
            claimed = replace(
                claimed,
                state_sha256=_sha256(claimed.state_payload()),
            )
            claimed.verify()
            state.intents[selected_ref] = claimed

        try:
            result = runtime.adapter.submit(request)
            self._validate_adapter_result(runtime, request, result)
        except (KeyboardInterrupt, SystemExit):
            self._mark_dispatch_reconciliation(
                authority,
                intent_ref=selected_ref,
                claimed_revision=claimed.revision,
                request=request,
            )
            raise
        except Exception as exc:
            self._mark_dispatch_reconciliation(
                authority,
                intent_ref=selected_ref,
                claimed_revision=claimed.revision,
                request=request,
            )
            raise CloudAgentAmbiguousEffect("provider dispatch requires reconciliation") from exc

        status_map: dict[AdapterDisposition, IntentStatus] = {
            "accepted": "adapter_accepted",
            "ambiguous": "reconciliation_required",
            "no_change": "adapter_reported_no_change",
            "rejected": "adapter_rejected",
            "retryable": "retryable",
        }
        try:
            with self._lock:
                state = self._tenant(authority)
                current = self._bound_record(
                    state.intents,
                    selected_ref,
                    authority.tenant_sha256,
                    "intent",
                )
                if (
                    not isinstance(current, EnforcementIntent)
                    or current.revision != claimed.revision
                    or current.status != "dispatching"
                    or current.state_sha256 != claimed.state_sha256
                ):
                    raise CloudAgentStaleRevision(
                        "enforcement dispatch claim changed during provider call"
                    )
                current_runtime = self._active_runtime(state, current.provider)
                if (
                    current_runtime.binding.binding_ref != request.binding_ref
                    or current_runtime.binding.revision != request.binding_revision
                ):
                    raise CloudAgentStaleRevision("provider binding changed during provider call")
                updated = replace(
                    current,
                    status=status_map[result.disposition],
                    revision=current.revision + 1,
                    last_receipt_sha256=result.receipt_sha256,
                    state_sha256="0" * 64,
                )
                updated = replace(
                    updated,
                    state_sha256=_sha256(updated.state_payload()),
                )
                updated.verify()
                state.intents[selected_ref] = updated
                receipt = EnforcementDispatchReceipt(updated, result)
                self._remember(
                    state,
                    "dispatch_enforcement",
                    idempotency_key,
                    semantic,
                    receipt,
                )
                return receipt
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            self._mark_dispatch_reconciliation(
                authority,
                intent_ref=selected_ref,
                claimed_revision=claimed.revision,
                request=request,
            )
            raise CloudAgentAmbiguousEffect(
                "provider dispatch completion requires reconciliation"
            ) from exc

    def get_enforcement_intent(
        self,
        authority: TenantAuthority,
        *,
        intent_ref: str,
    ) -> EnforcementIntent:
        selected = _reference(intent_ref, "intent_ref", prefixes={"cei"})
        with self._lock:
            state = self._tenant(authority)
            intent = self._bound_record(
                state.intents,
                selected,
                authority.tenant_sha256,
                "intent",
            )
            if not isinstance(intent, EnforcementIntent):
                raise CloudAgentStateError("intent state is invalid")
            intent.verify()
            return intent

    def resolve_enforcement_reconciliation(
        self,
        authority: TenantAuthority,
        *,
        intent_ref: str,
        expected_revision: int,
        resolution: ReconciliationResolution,
        resolver_ref: str,
        evidence_ref: str,
        evidence_sha256: str,
        observed_inventory_revision: int,
        idempotency_key: str,
    ) -> EnforcementIntent:
        """Close an ambiguous dispatch only against fresh post-read inventory evidence."""

        selected_intent = _reference(intent_ref, "intent_ref", prefixes={"cei"})
        expected = _revision(expected_revision, "expected_revision")
        if resolution not in {"applied", "no_change", "retry"}:
            raise ValueError("reconciliation resolution is unsupported")
        resolver = _reference(resolver_ref, "resolver_ref", prefixes={"prn"})
        selected_evidence = _reference(evidence_ref, "evidence_ref", prefixes={"evd"})
        evidence_sha = _digest(evidence_sha256, "evidence_sha256")
        inventory_revision = _revision(
            observed_inventory_revision,
            "observed_inventory_revision",
        )
        semantic = {
            "intent_ref": selected_intent,
            "expected_revision": expected,
            "resolution": resolution,
            "resolver_ref": resolver,
            "evidence_ref": selected_evidence,
            "evidence_sha256": evidence_sha,
            "observed_inventory_revision": inventory_revision,
        }
        with self._lock:
            state = self._tenant(authority)
            replay = self._replay(
                state,
                "resolve_enforcement_reconciliation",
                idempotency_key,
                semantic,
            )
            if replay is not None:
                return replay  # type: ignore[return-value]
            intent = self._bound_record(
                state.intents,
                selected_intent,
                authority.tenant_sha256,
                "intent",
            )
            if not isinstance(intent, EnforcementIntent):
                raise CloudAgentStateError("intent state is invalid")
            intent.verify()
            if intent.revision != expected:
                raise CloudAgentStaleRevision("enforcement intent revision is stale")
            if intent.status not in {"dispatching", "reconciliation_required"}:
                raise CloudAgentStateError("enforcement intent does not require reconciliation")
            record = self._agent(state, authority.tenant_sha256, intent.agent_ref)
            if (
                record.inventory_revision != inventory_revision
                or inventory_revision <= intent.inventory_revision
            ):
                raise CloudAgentStaleRevision(
                    "reconciliation requires a fresh exact inventory observation"
                )
            relationships = record.agent.relationships
            if intent.action in {"disable_agent", "quarantine_agent"}:
                effect_observed = record.agent.lifecycle_state == "disabled"
            elif intent.action == "restrict_tool":
                if not relationships.tool_inventory_complete:
                    raise CloudAgentStateError("tool relationship inventory is incomplete")
                effect_observed = intent.target_ref not in relationships.tool_refs
            elif intent.action == "restrict_model":
                if not relationships.model_inventory_complete:
                    raise CloudAgentStateError("model relationship inventory is incomplete")
                effect_observed = intent.target_ref not in relationships.model_refs
            else:
                if not relationships.data_source_inventory_complete:
                    raise CloudAgentStateError("data-source relationship inventory is incomplete")
                effect_observed = intent.target_ref not in relationships.data_source_refs
            if resolution == "applied" and not effect_observed:
                raise CloudAgentBindingError(
                    "fresh inventory does not show the requested provider effect"
                )
            if resolution in {"no_change", "retry"} and effect_observed:
                raise CloudAgentBindingError(
                    "fresh inventory already shows the requested provider effect"
                )
            status: IntentStatus = {
                "applied": "reconciled_applied",
                "no_change": "reconciled_no_change",
                "retry": "retryable",
            }[resolution]  # type: ignore[assignment]
            updated = replace(
                intent,
                status=status,
                revision=intent.revision + 1,
                reconciliation_evidence_ref=selected_evidence,
                reconciliation_evidence_sha256=evidence_sha,
                reconciled_by_ref=resolver,
                state_sha256="0" * 64,
            )
            updated = replace(updated, state_sha256=_sha256(updated.state_payload()))
            updated.verify()
            state.intents[selected_intent] = updated
            self._remember(
                state,
                "resolve_enforcement_reconciliation",
                idempotency_key,
                semantic,
                updated,
            )
            return updated

    def register_framework_pack(
        self,
        authority: TenantAuthority,
        pack: DatabricksAISecurityFrameworkPack,
        *,
        expected_catalog_revision: int,
        idempotency_key: str,
    ) -> int:
        self._require_framework_enabled()
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
        self._require_framework_enabled()
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
        self._require_framework_enabled()
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
            attestation = replace(provisional, attestation_sha256=_sha256(provisional.payload()))
            attestation.verify()
            state.attestations[attestation_ref] = attestation
            self._remember(state, "attest_framework_review", idempotency_key, semantic, attestation)
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
        self._require_framework_enabled()
        expected = _revision(
            expected_activation_revision, "expected_activation_revision", allow_zero=True
        )
        if action not in {"activate", "rollback"}:
            raise ValueError("framework activation action is unsupported")
        selected_review = _reference(review_ref, "review_ref", prefixes={"fwr"})
        selected_attestation = _reference(attestation_ref, "attestation_ref", prefixes={"fwa"})
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
                or not secrets_compare_digest(attestation.review_sha256, review.review_sha256)
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
            activation = replace(provisional, activation_sha256=_sha256(provisional.payload()))
            activation.verify()
            state.activation = activation
            self._remember(state, "activate_framework_pack", idempotency_key, semantic, activation)
            return activation

    # ---- read-only registers for operator pickers -------------------------
    # These never widen authority: each read is bound to the caller's tenant
    # authority, verifies every record's integrity before returning it, and is
    # capped so a page can offer selectors instead of typed references.

    def list_policy_evaluations(
        self,
        authority: TenantAuthority,
        *,
        limit: int = _MAX_REGISTER_ROWS,
    ) -> tuple[PolicyEvaluationRecord, ...]:
        bound = _register_limit(limit)
        with self._lock:
            state = self._tenant(authority)
            rows = [
                record
                for record in state.evaluations.values()
                if isinstance(record, PolicyEvaluationRecord)
                and record.tenant_sha256 == authority.tenant_sha256
            ]
        for record in rows:
            record.verify()
        rows.sort(key=lambda record: (-record.evaluated_at, record.evaluation_ref))
        return tuple(rows[:bound])

    def list_enforcement_intents(
        self,
        authority: TenantAuthority,
        *,
        limit: int = _MAX_REGISTER_ROWS,
    ) -> tuple[EnforcementIntent, ...]:
        bound = _register_limit(limit)
        with self._lock:
            state = self._tenant(authority)
            rows = [
                intent
                for intent in state.intents.values()
                if isinstance(intent, EnforcementIntent)
                and intent.tenant_sha256 == authority.tenant_sha256
            ]
        for intent in rows:
            intent.verify()
        rows.sort(key=lambda intent: intent.intent_ref)
        return tuple(rows[:bound])

    def framework_register(self, authority: TenantAuthority) -> dict[str, object]:
        """Registered packs, reviews, attestations and the active version."""

        self._require_framework_enabled()
        with self._lock:
            state = self._tenant(authority)
            packs = list(state.packs.values())
            reviews = [
                review
                for review in state.reviews.values()
                if isinstance(review, FrameworkReview)
                and review.tenant_sha256 == authority.tenant_sha256
            ]
            attestations = [
                attestation
                for attestation in state.attestations.values()
                if isinstance(attestation, FrameworkAttestation)
                and attestation.tenant_sha256 == authority.tenant_sha256
            ]
            activation = state.activation
        for pack in packs:
            pack.verify()
        for review in reviews:
            review.verify()
        for attestation in attestations:
            attestation.verify()
        if activation is not None:
            activation.verify()
        return {
            "packs": sorted(packs, key=lambda pack: pack.version),
            "reviews": sorted(reviews, key=lambda review: (-review.reviewed_at, review.review_ref)),
            "attestations": sorted(
                attestations,
                key=lambda attestation: (-attestation.attested_at, attestation.attestation_ref),
            ),
            "activation": activation,
        }

    def policy_catalog(self) -> tuple[dict[str, object], ...]:
        """Policies the configured evaluator offers, when it publishes a catalog.

        The evaluator protocol only requires ``evaluate``; an evaluator may also
        expose ``catalog()`` returning ``policy_ref``/``revision``/``sha256`` and
        a display ``title``. Anything malformed is refused, never guessed.
        """

        self._require_enabled()
        evaluator = self._policy_evaluator
        catalog = getattr(evaluator, "catalog", None)
        if evaluator is None or not callable(catalog):
            return ()
        try:
            entries = catalog()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise CloudAgentAdapterError("policy catalog failed closed") from exc
        if isinstance(entries, (str, bytes, Mapping)) or not isinstance(entries, Sequence):
            raise CloudAgentAdapterError("policy catalog is invalid")
        selected: list[dict[str, object]] = []
        for entry in list(entries)[:_MAX_REGISTER_ROWS]:
            if not isinstance(entry, Mapping):
                raise CloudAgentAdapterError("policy catalog entry is invalid")
            title = entry.get("title")
            if not isinstance(title, str) or not title.strip() or len(title) > 200:
                raise CloudAgentAdapterError("policy catalog title is invalid")
            try:
                selected.append(
                    {
                        "policy_ref": _reference(
                            entry.get("policy_ref"), "policy_ref", prefixes={"pol"}
                        ),
                        "policy_revision": _revision(entry.get("revision"), "policy_revision"),
                        "policy_sha256": _digest(entry.get("sha256"), "policy_sha256"),
                        "title": title.strip(),
                    }
                )
            except ValueError as exc:
                raise CloudAgentAdapterError("policy catalog entry is invalid") from exc
        selected.sort(key=lambda item: (str(item["title"]).casefold(), str(item["policy_ref"])))
        return tuple(selected)

    def verify_tenant_integrity(self, authority: TenantAuthority) -> str:
        """Verify all canonical hashes and return a tenant state root."""

        with self._lock:
            state = self._tenant(authority)
            digests: list[str] = []
            for runtime in state.adapters.values():
                runtime.binding.verify()
                digests.append(runtime.binding.state_sha256)
            for provider, inventory in state.inventories.items():
                inventory_scopes = state.inventory_scopes.get(provider)
                if inventory_scopes is None or set(inventory_scopes) != set(inventory):
                    raise CloudAgentIntegrityError(
                        "inventory scope index does not match canonical inventory"
                    )
                for agent_ref, record in inventory.items():
                    record.verify()
                    scope_sha256 = _digest(inventory_scopes[agent_ref], "inventory scope digest")
                    if self._agent_tenants.get(agent_ref) != authority.tenant_sha256:
                        raise CloudAgentIntegrityError(
                            "agent tenant index does not match canonical inventory"
                        )
                    digests.append(record.state_sha256)
                    digests.append(
                        _sha256(
                            {
                                "schema": "lightwork.cloud-agent.inventory-scope.v1",
                                "provider": provider,
                                "agent_ref": agent_ref,
                                "scope_sha256": scope_sha256,
                            }
                        )
                    )
            unexpected_scope_providers = set(state.inventory_scopes).difference(state.inventories)
            if unexpected_scope_providers:
                raise CloudAgentIntegrityError("inventory scope index contains an unknown provider")
            for metadata in state.inventory_metadata.values():
                metadata.verify()
                digests.append(metadata.state_sha256)
            for (provider, scope_sha256), snapshot in state.scope_snapshots.items():
                digests.append(
                    _sha256(
                        {
                            "schema": "lightwork.cloud-agent.scope-snapshot.v1",
                            "provider": _provider(provider),
                            "scope_sha256": _digest(scope_sha256, "scope_sha256"),
                            "source_snapshot_sha256": _digest(
                                snapshot.source_snapshot_sha256,
                                "source_snapshot_sha256",
                            ),
                            "captured_at": _timestamp(snapshot.captured_at, "captured_at"),
                        }
                    )
                )
            for (provider, scan_ref), finalized in state.finalized_scans.items():
                digests.append(
                    _sha256(
                        {
                            "schema": "lightwork.cloud-agent.finalized-scan.v1",
                            "provider": _provider(provider),
                            "scan_ref": _reference(scan_ref, "scan_ref", prefixes={"cas"}),
                            "manifest_sha256": _digest(
                                finalized.manifest_sha256, "manifest_sha256"
                            ),
                            "inventory_revision": _revision(
                                finalized.inventory_revision,
                                "inventory_revision",
                            ),
                            "captured_at": _timestamp(finalized.captured_at, "captured_at"),
                        }
                    )
                )
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
                    "schema": "lightwork.cloud-agent.tenant-state-root.v2",
                    "tenant_sha256": authority.tenant_sha256,
                    "digests": sorted(digests),
                }
            )

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise CloudAgentGovernanceDisabled(
                "cloud-agent governance requires an explicit deployment enablement"
            )

    def _require_framework_enabled(self) -> None:
        self._require_enabled()
        if not self._databricks_framework_enabled:
            raise CloudAgentGovernanceDisabled(
                "Databricks framework support is not enabled by deployment policy"
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

    def _active_runtime(self, state: _TenantState, provider: CloudProvider) -> _AdapterRuntime:
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
                "manifest_sha256": page.manifest_sha256,
                "page_count": page.page_count,
                "snapshot_complete": page.snapshot_complete,
                "incomplete_reason_codes": list(page.incomplete_reason_codes),
                "captured_at": page.captured_at,
            }
        )

    def _purge_expired_scans(self, state: _TenantState, *, now: float) -> None:
        cutoff = now - self._scan_ttl_seconds
        expired = [identity for identity, scan in state.scans.items() if scan.updated_at < cutoff]
        for identity in expired:
            del state.scans[identity]
        finalized_cutoff = now - self._retention_seconds
        expired_finalized = [
            identity
            for identity, finalized in state.finalized_scans.items()
            if finalized.captured_at < finalized_cutoff
        ]
        for identity in expired_finalized:
            del state.finalized_scans[identity]

    @staticmethod
    def _inventory_revision(state: _TenantState, provider: CloudProvider) -> int:
        metadata = state.inventory_metadata.get(provider)
        return 0 if metadata is None else metadata.inventory_revision

    def _finalize_scan(self, state: _TenantState, provider: CloudProvider, scan: _ScanState) -> int:
        if self._inventory_revision(state, provider) != scan.base_inventory_revision:
            raise CloudAgentStaleRevision("inventory scan base revision is stale")
        pages = [scan.pages[index] for index in range(scan.page_count)]
        items = [item for page in pages for item in page.items]
        first = pages[0]
        for index, page in enumerate(pages):
            expected_continuation = None
            if index < len(pages) - 1:
                expected_continuation = _sha256(
                    {
                        "scan_ref": first.scan_ref,
                        "next_page_index": index + 1,
                        "next_item_sha256": pages[index + 1].items[0].item_sha256,
                    }
                )
            if page.continuation_sha256 != expected_continuation:
                raise CloudAgentIntegrityError(
                    "inventory continuation chain does not match scan pages"
                )
        expected_manifest = _inventory_manifest_sha256(
            provider=provider,
            binding_ref=first.binding_ref,
            binding_revision=first.binding_revision,
            scan_ref=first.scan_ref,
            scope_sha256=first.scope_sha256,
            source_snapshot_sha256=first.source_snapshot_sha256,
            page_count=first.page_count,
            snapshot_complete=first.snapshot_complete,
            incomplete_reason_codes=first.incomplete_reason_codes,
            captured_at=first.captured_at,
            items=items,
        )
        if not secrets_compare_digest(first.manifest_sha256, expected_manifest):
            raise CloudAgentIntegrityError("inventory manifest does not match scan pages")
        refs = [item.agent_ref for item in items]
        if len(refs) != len(set(refs)):
            raise CloudAgentIdempotencyConflict("agent appears in multiple inventory pages")
        current = state.inventories.get(provider, {})
        current_scopes = state.inventory_scopes.get(provider, {})
        selected = dict(current)
        selected_scopes = dict(current_scopes)
        incoming_refs = set(refs)
        removed: set[str] = set()
        if first.snapshot_complete:
            removed = {
                agent_ref
                for agent_ref, scope_sha256 in current_scopes.items()
                if scope_sha256 == first.scope_sha256 and agent_ref not in incoming_refs
            }
            for agent_ref in removed:
                selected.pop(agent_ref, None)
                selected_scopes.pop(agent_ref, None)
        new_inventory_revision = self._inventory_revision(state, provider) + 1
        for item in items:
            existing_tenant = self._agent_tenants.get(item.agent_ref)
            if existing_tenant is not None and existing_tenant != state.authority.tenant_sha256:
                raise CloudAgentBindingError("agent reference is already bound to another tenant")
            prior_scope = current_scopes.get(item.agent_ref)
            if prior_scope is not None and prior_scope != first.scope_sha256:
                raise CloudAgentBindingError("agent reference collides across provider scopes")
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
            selected_scopes[item.agent_ref] = first.scope_sha256
        other_agent_count = sum(
            len(inventory)
            for selected_provider, inventory in state.inventories.items()
            if selected_provider != provider
        )
        if other_agent_count + len(selected) > _MAX_AGENTS_PER_TENANT:
            raise CloudAgentStateError("cloud-agent inventory bound is exhausted")
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
        metadata = InventoryMetadata(
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
        metadata.verify()
        state.inventories[provider] = selected
        state.inventory_scopes[provider] = selected_scopes
        state.inventory_metadata[provider] = metadata
        state.scope_snapshots[(provider, first.scope_sha256)] = _ScopeSnapshot(
            first.source_snapshot_sha256,
            first.captured_at,
        )
        for agent_ref in incoming_refs:
            self._agent_tenants[agent_ref] = state.authority.tenant_sha256
        for agent_ref in removed:
            if self._agent_tenants.get(agent_ref) == state.authority.tenant_sha256:
                del self._agent_tenants[agent_ref]
        return new_inventory_revision

    def _agent(self, state: _TenantState, tenant_sha256: str, agent_ref: str) -> CloudAgentRecord:
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
            if not agent.relationships.tool_inventory_complete:
                raise CloudAgentStateError("tool relationship inventory is incomplete")
            allowed = set(agent.relationships.tool_refs)
        elif action == "restrict_model":
            if not agent.relationships.model_inventory_complete:
                raise CloudAgentStateError("model relationship inventory is incomplete")
            allowed = set(agent.relationships.model_refs)
        else:
            if not agent.relationships.data_source_inventory_complete:
                raise CloudAgentStateError("data-source relationship inventory is incomplete")
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
            or not secrets_compare_digest(result.effect_sha256, request.effect_sha256)
            or not secrets_compare_digest(result.request_sha256, request.request_sha256)
        ):
            raise CloudAgentBindingError("provider adapter result binding is forged or stale")
        try:
            verified = runtime.verifier(binding, result)
        except Exception as exc:
            raise CloudAgentAdapterError("provider adapter verifier failed closed") from exc
        if verified is not True:
            raise CloudAgentIntegrityError("provider adapter signature is not trusted")

    def _mark_dispatch_reconciliation(
        self,
        authority: TenantAuthority,
        *,
        intent_ref: str,
        claimed_revision: int,
        request: EnforcementDispatchRequest,
    ) -> None:
        with self._lock:
            state = self._tenant(authority)
            current = state.intents.get(intent_ref)
            if (
                not isinstance(current, EnforcementIntent)
                or current.revision != claimed_revision
                or current.status != "dispatching"
            ):
                return
            local_receipt = _sha256(
                {
                    "schema": "lightwork.cloud-agent.ambiguous-dispatch.v1",
                    "tenant_sha256": authority.tenant_sha256,
                    "intent_ref": intent_ref,
                    "effect_sha256": request.effect_sha256,
                    "request_sha256": request.request_sha256,
                }
            )
            updated = replace(
                current,
                status="reconciliation_required",
                revision=current.revision + 1,
                last_receipt_sha256=local_receipt,
                state_sha256="0" * 64,
            )
            updated = replace(updated, state_sha256=_sha256(updated.state_payload()))
            updated.verify()
            state.intents[intent_ref] = updated

    @staticmethod
    def _pack(state: _TenantState, version: str) -> DatabricksAISecurityFrameworkPack:
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


class CloudAgentInventoryRunner:
    """Server-side read-only discovery and exact-binding ingest coordinator."""

    def __init__(
        self,
        *,
        authority: CloudAgentGovernanceAuthority,
        config: CloudAgentGovernanceConfig,
        pseudonymizer: TenantBoundReferencePseudonymizer,
        revision_authority: ProviderRevisionAuthority,
        resource_registry: ProviderResourceRegistry,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(authority, CloudAgentGovernanceAuthority):
            raise ValueError("cloud-agent governance authority is required")
        if not isinstance(config, CloudAgentGovernanceConfig) or not config.enable:
            raise CloudAgentGovernanceDisabled(
                "cloud-agent inventory runner requires enabled runtime policy"
            )
        self._authority = authority
        self._config = config
        self._pseudonymizer = pseudonymizer
        self._revision_authority = revision_authority
        if not callable(getattr(resource_registry, "replace_scope", None)):
            raise ValueError("provider resource registry scope contract is required")
        self._resource_registry = resource_registry
        self._clock = clock

    def sync(
        self,
        tenant_authority: TenantAuthority,
        *,
        provider: CloudProvider,
        inventory_adapter: object,
        scope_sha256: str,
        expected_inventory_revision: int,
        idempotency_key: str,
    ) -> tuple[InventoryIngestReceipt, ...]:
        selected_provider = _provider(provider)
        if selected_provider not in self._config.enabled_providers:
            raise CloudAgentGovernanceDisabled(
                "cloud-agent provider is not enabled by deployment policy"
            )
        if not callable(getattr(inventory_adapter, "discover", None)):
            raise ValueError("read-only inventory adapter is required")
        binding = self._authority.resolve_provider_binding(
            tenant_authority,
            provider=selected_provider,
            require_active=True,
        )
        try:
            result = inventory_adapter.discover()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise CloudAgentAdapterError("provider inventory discovery failed closed") from exc
        normalizer = build_provider_inventory_contract_adapter(
            selected_provider,
            pseudonymizer=self._pseudonymizer,
            revision_authority=self._revision_authority,
            max_records_per_sync=self._config.max_records_per_sync,
            clock=self._clock,
        )
        batch = normalizer.normalize_discovery(
            result,
            authority=tenant_authority,
            binding=binding,
            scope_sha256=scope_sha256,
        )
        pages = batch.pages
        key_sha256 = _idempotency_sha256(idempotency_key)
        try:
            # Stage the exact native binding batch first. If this fails, the
            # canonical inventory remains unchanged and the sync can retry.
            # A later ingest failure leaves only an ahead-of-inventory mapping;
            # dispatch still fails closed because resolve_dispatch rechecks the
            # canonical agent/configuration revision held by the intent.
            self._resource_registry.replace_scope(
                tenant_sha256=tenant_authority.tenant_sha256,
                provider=selected_provider,
                scope_sha256=scope_sha256,
                bindings=batch.native_bindings,
            )
        except CloudAgentGovernanceError:
            raise
        except Exception as exc:
            raise CloudAgentAdapterError("provider resource registry failed closed") from exc
        receipts: list[InventoryIngestReceipt] = []
        for page in pages:
            receipts.append(
                self._authority.ingest_inventory_page(
                    tenant_authority,
                    page,
                    expected_inventory_revision=expected_inventory_revision,
                    idempotency_key=(f"cloud-inventory-{key_sha256[:32]}-{page.page_index}"),
                )
            )
        if not receipts or not receipts[-1].finalized:
            raise CloudAgentStateError("normalized inventory batch did not finalize")
        return tuple(receipts)

    def sync_configured(
        self,
        tenant_authority: TenantAuthority,
        *,
        provider: CloudProvider,
        expected_inventory_revision: int,
        idempotency_key: str,
    ) -> tuple[InventoryIngestReceipt, ...]:
        selected_provider = _provider(provider)
        if selected_provider not in self._config.enabled_providers:
            raise CloudAgentGovernanceDisabled(
                "cloud-agent provider is not enabled by deployment policy"
            )
        try:
            from .ai_inventory import build_configured_adapter

            adapter = build_configured_adapter(_PROVIDER_CONNECTORS[selected_provider])
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise CloudAgentConfigError(
                "configured provider inventory adapter is unavailable"
            ) from exc
        if selected_provider == "aws_bedrock":
            scope_projection = {"region": getattr(adapter, "region", None)}
        elif selected_provider == "azure_ai_foundry":
            scope_projection = {"endpoint": getattr(adapter, "base_url", None)}
        else:
            scope_projection = {
                "project": getattr(adapter, "project", None),
                "location": getattr(adapter, "location", None),
            }
        if any(not isinstance(value, str) or not value for value in scope_projection.values()):
            raise CloudAgentConfigError("configured provider inventory scope is unavailable")
        scope_sha256 = self._pseudonymizer.digest(
            {
                "schema": "lightwork.cloud-agent.configured-scope.v1",
                "provider": selected_provider,
                "connector": _PROVIDER_CONNECTORS[selected_provider],
                "scope": scope_projection,
            }
        )
        return self.sync(
            tenant_authority,
            provider=selected_provider,
            inventory_adapter=adapter,
            scope_sha256=scope_sha256,
            expected_inventory_revision=expected_inventory_revision,
            idempotency_key=idempotency_key,
        )


@dataclass(frozen=True)
class CloudAgentGovernanceRuntime:
    """Configured backend capability with an explicit persistence posture."""

    config: CloudAgentGovernanceConfig
    authority: CloudAgentGovernanceAuthority
    inventory_runner: CloudAgentInventoryRunner | None
    resource_registry: ProviderResourceRegistry | None = field(repr=False)
    revision_authority: ProviderRevisionAuthority | None = field(repr=False)
    store_mode: Literal["disabled", "process_local_test_only"]

    def __post_init__(self) -> None:
        if self.store_mode not in {"disabled", "process_local_test_only"}:
            raise CloudAgentConfigError("cloud-agent runtime persistence posture is invalid")
        if self.store_mode == "disabled":
            if self.config.enable or any(
                item is not None
                for item in (
                    self.inventory_runner,
                    self.resource_registry,
                    self.revision_authority,
                )
            ):
                raise CloudAgentConfigError("disabled cloud-agent runtime is incoherent")
        elif (
            not self.config.enable
            or self.inventory_runner is None
            or self.resource_registry is None
            or self.revision_authority is None
        ):
            raise CloudAgentConfigError("enabled cloud-agent runtime is incoherent")


def build_cloud_agent_governance_runtime(
    *,
    config_root: Mapping[str, object] | None = None,
    policy_evaluator: CloudAgentPolicyEvaluator | None = None,
    reference_key: bytes | None = None,
    resource_registry: ProviderResourceRegistry | None = None,
    revision_authority: ProviderRevisionAuthority | None = None,
    allow_process_local_stores: bool = False,
    clock: Callable[[], float] = time.time,
    max_tenants: int = 1_000,
) -> CloudAgentGovernanceRuntime:
    """Build a fail-closed runtime from one config snapshot.

    Enabled deployments must inject an independent pseudonymization key.  The
    canonical governance authority is presently process-local, so this builder
    refuses an enabled runtime unless ``allow_process_local_stores`` is the
    explicit test/pilot override. Injecting durable native-resource/revision
    stores does not turn the canonical authority into durable state.
    """

    config = load_cloud_agent_governance_config(config_root)
    authority = CloudAgentGovernanceAuthority.from_config(
        config,
        policy_evaluator=policy_evaluator,
        clock=clock,
        max_tenants=max_tenants,
    )
    if not config.enable:
        return CloudAgentGovernanceRuntime(config, authority, None, None, None, "disabled")
    if reference_key is None:
        raise CloudAgentConfigError(
            "enabled cloud-agent governance requires an injected reference key"
        )
    if not isinstance(allow_process_local_stores, bool):
        raise CloudAgentConfigError("allow_process_local_stores must be a boolean")
    if not allow_process_local_stores:
        raise CloudAgentConfigError(
            "durable canonical cloud-agent governance state is unavailable; "
            "production runtime fails closed"
        )
    supplied_store_modes = (
        None
        if revision_authority is None
        else getattr(revision_authority, "durability_mode", None),
        None if resource_registry is None else getattr(resource_registry, "durability_mode", None),
    )
    if any(mode not in {None, "durable", "process_local"} for mode in supplied_store_modes):
        raise CloudAgentConfigError("cloud-agent store durability contract is invalid")
    pseudonymizer = TenantBoundReferencePseudonymizer(reference_key)
    selected_revisions = revision_authority
    if selected_revisions is None:
        selected_revisions = InMemoryProviderRevisionAuthority(
            max_resources=min(config.max_records_per_sync, _MAX_AGENTS_PER_TENANT)
        )
    selected_resources = resource_registry
    if selected_resources is None:
        selected_resources = InMemoryProviderResourceRegistry(
            max_resources=min(config.max_records_per_sync, _MAX_AGENTS_PER_TENANT)
        )
    runner = CloudAgentInventoryRunner(
        authority=authority,
        config=config,
        pseudonymizer=pseudonymizer,
        revision_authority=selected_revisions,
        resource_registry=selected_resources,
        clock=clock,
    )
    return CloudAgentGovernanceRuntime(
        config,
        authority,
        runner,
        selected_resources,
        selected_revisions,
        "process_local_test_only",
    )


__all__ = [
    "ActivationAction",
    "AdapterReceiptSignatureVerifier",
    "AdapterReceiptSigner",
    "AdapterDisposition",
    "AdapterResultVerifier",
    "AgentRelationships",
    "ApplicabilityStatus",
    "AzureAIFoundryEnforcementAdapter",
    "AzureAIFoundryInventoryContractAdapter",
    "BedrockEnforcementAdapter",
    "BedrockInventoryContractAdapter",
    "CLOUD_AGENT_LIMIT_NOTICE",
    "CLOUD_PROVIDERS",
    "CloudAgentAdapterError",
    "CloudAgentAmbiguousEffect",
    "CloudAgentBindingError",
    "CloudAgentConfigError",
    "CloudAgentEnforcementAdapter",
    "CloudAgentGovernanceAuthority",
    "CloudAgentGovernanceConfig",
    "CloudAgentGovernanceDisabled",
    "CloudAgentGovernanceError",
    "CloudAgentGovernanceRuntime",
    "CloudAgentIdempotencyConflict",
    "CloudAgentIntegrityError",
    "CloudAgentInventoryRunner",
    "CloudAgentObservabilityEvent",
    "CloudAgentRecord",
    "CloudAgentRevoked",
    "CloudAgentStaleRevision",
    "CloudAgentStateError",
    "CloudAgentPolicyEvaluator",
    "CloudProvider",
    "ContractSafeProviderEnforcementAdapter",
    "DatabricksAISecurityFrameworkPack",
    "DatabricksFrameworkContractAdapter",
    "EnforcementDispatchReceipt",
    "EnforcementDispatchRequest",
    "EnforcementAction",
    "EnforcementIntent",
    "FRAMEWORK_LIMIT_NOTICE",
    "FrameworkActivation",
    "FrameworkApplicability",
    "FrameworkAttestation",
    "FrameworkControlMapping",
    "FrameworkEvidenceLink",
    "FrameworkReview",
    "HMACAdapterReceiptAuthenticator",
    "InMemoryProviderResourceRegistry",
    "InMemoryProviderRevisionAuthority",
    "InventoryIngestReceipt",
    "InventoryMetadata",
    "InventoryPage",
    "InventorySlice",
    "IntentStatus",
    "LifecycleState",
    "NormalizedInventoryBatch",
    "NormalizedCloudAgent",
    "ObservabilityReceipt",
    "ObservabilityKind",
    "PolicyDecision",
    "PolicyEvaluationRecord",
    "PolicyEvaluationRequest",
    "PolicyEvaluationResult",
    "ProviderAdapterResult",
    "ProviderBinding",
    "ProviderInventoryContractAdapter",
    "ProviderMutationOutcome",
    "ProviderMutationTransport",
    "ProviderOperation",
    "ProviderResourceRegistry",
    "ProviderRevisionAuthority",
    "ReconciliationResolution",
    "ReviewDecision",
    "TenantAuthority",
    "TenantBoundReferencePseudonymizer",
    "VertexAIEnforcementAdapter",
    "VertexAIInventoryContractAdapter",
    "build_adapter_result_verifier",
    "build_cloud_agent_governance_runtime",
    "build_provider_inventory_contract_adapter",
    "load_cloud_agent_governance_config",
]
