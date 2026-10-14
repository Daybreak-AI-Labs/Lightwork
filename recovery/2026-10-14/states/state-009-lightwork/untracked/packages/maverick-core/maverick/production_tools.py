"""Fail-closed admission contract for tools in a production artifact.

The ordinary Lightwork registry intentionally remains extensible: built-ins,
MCP servers, plugins, and generated tools can all register the historical
``Tool`` shape.  A production artifact is different.  Its reviewed surface
manifest must explicitly include each admitted ``tool:<name>`` and the tool
itself must carry complete, internally consistent policy metadata.

Consequential actions are deliberately disabled by the current production
surface.  A future manifest may enable them only for tools that carry and
return the signed typed receipt contract enforced at registration, dispatch,
and result admission; the legacy string result protocol never authorizes a
mutation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ProductionToolPolicyError(RuntimeError):
    """A production tool or manifest does not satisfy the admission contract."""


class ToolRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SideEffectClass(str, Enum):
    NONE = "none"
    WORKSPACE_MUTATION = "workspace_mutation"
    CONTROL_PLANE_MUTATION = "control_plane_mutation"
    EXTERNAL_MUTATION = "external_mutation"

    @property
    def is_mutation(self) -> bool:
        return self is not SideEffectClass.NONE


class TenantScope(str, Enum):
    DEPLOYMENT_SHARED = "deployment_shared"
    CURRENT_TENANT = "current_tenant"
    CURRENT_RUN = "current_run"
    RUN_WORKSPACE = "run_workspace"


class CredentialBehavior(str, Enum):
    NONE = "none"
    PRINCIPAL_SCOPED = "principal_scoped"
    TENANT_SERVICE_IDENTITY = "tenant_service_identity"
    OPERATOR_AMBIENT = "operator_ambient"


class ApprovalClass(str, Enum):
    NOT_REQUIRED = "not_required"
    EXACT_ACTION = "exact_action"
    QUORUM_EXACT_ACTION = "quorum_exact_action"


class AuditClass(str, Enum):
    STANDARD = "standard"
    DATA_ACCESS = "data_access"
    SECURITY_SENSITIVE = "security_sensitive"
    CONSEQUENTIAL = "consequential"


class IdempotencyContract(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    REQUIRED_CALLER_KEY = "required_caller_key"
    PROVIDER_OPERATION_KEY = "provider_operation_key"


class ReconciliationMethod(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    AUTHORITATIVE_READ_AFTER_WRITE = "authoritative_read_after_write"
    PROVIDER_STATUS_BY_OPERATION_ID = "provider_status_by_operation_id"
    OPERATOR_ADJUDICATION = "operator_adjudication"


@dataclass(frozen=True)
class ResourceExtractor:
    """Declarative extraction of paths or hosts from tool arguments.

    Every policy supplies separate path and host extractors.  ``argument_names``
    is empty only when that resource type is genuinely inapplicable.  Optional
    arguments must name their effective fallback so the policy still observes
    the resource the implementation will use.
    """

    argument_names: tuple[str, ...]
    required: bool
    fallback_values: tuple[str, ...]

    def validate_schema(
        self,
        *,
        tool_name: str,
        resource_name: str,
        input_schema: dict[str, Any],
    ) -> None:
        if not isinstance(self.argument_names, tuple) or any(
            not isinstance(name, str) or not name.strip()
            for name in self.argument_names
        ):
            raise ProductionToolPolicyError(
                f"{tool_name}: {resource_name} extractor argument names are invalid"
            )
        if len(set(self.argument_names)) != len(self.argument_names):
            raise ProductionToolPolicyError(
                f"{tool_name}: {resource_name} extractor arguments are duplicated"
            )
        if not isinstance(self.required, bool):
            raise ProductionToolPolicyError(
                f"{tool_name}: {resource_name} extractor required flag is invalid"
            )
        if not isinstance(self.fallback_values, tuple) or any(
            not isinstance(value, str) or not value.strip()
            for value in self.fallback_values
        ):
            raise ProductionToolPolicyError(
                f"{tool_name}: {resource_name} extractor fallback is invalid"
            )
        if not self.argument_names:
            if self.required:
                raise ProductionToolPolicyError(
                    f"{tool_name}: constant {resource_name} extractor cannot be required"
                )
            return
        if self.required and self.fallback_values:
            raise ProductionToolPolicyError(
                f"{tool_name}: required {resource_name} extractor cannot have a fallback"
            )
        properties = input_schema.get("properties")
        if not isinstance(properties, dict):
            raise ProductionToolPolicyError(
                f"{tool_name}: input schema has no properties for {resource_name} extraction"
            )
        missing = [name for name in self.argument_names if name not in properties]
        if missing:
            raise ProductionToolPolicyError(
                f"{tool_name}: {resource_name} extractor references unknown arguments"
            )
        if self.required:
            required = input_schema.get("required")
            required_names = set(required) if isinstance(required, list) else set()
            if any(name not in required_names for name in self.argument_names):
                raise ProductionToolPolicyError(
                    f"{tool_name}: required {resource_name} extractor is optional in schema"
                )

    def extract(self, args: dict[str, Any], *, resource_name: str) -> tuple[str, ...]:
        values: list[str] = []
        present = False
        for name in self.argument_names:
            if name not in args or args[name] is None:
                continue
            present = True
            raw = args[name]
            candidates = raw if isinstance(raw, (list, tuple)) else (raw,)
            if any(not isinstance(value, str) or not value.strip() for value in candidates):
                raise ProductionToolPolicyError(
                    f"{resource_name} extractor received a non-string resource"
                )
            values.extend(value.strip() for value in candidates)
        if not present:
            if self.required:
                raise ProductionToolPolicyError(
                    f"required {resource_name} argument is missing"
                )
            values.extend(self.fallback_values)
        return tuple(values)


@dataclass(frozen=True)
class ProductionToolPolicy:
    """Complete policy metadata required on an admitted production tool.

    There are intentionally no field defaults.  A tool author must make every
    decision explicitly instead of inheriting a permissive or guessed value.
    """

    risk: ToolRisk
    side_effect: SideEffectClass
    tenant_scope: TenantScope
    path_extractor: ResourceExtractor
    host_extractor: ResourceExtractor
    credential_behavior: CredentialBehavior
    approval_class: ApprovalClass
    audit_class: AuditClass
    idempotency_contract: IdempotencyContract
    reconciliation_method: ReconciliationMethod

    def validate(self, tool: Any) -> None:
        tool_name = str(getattr(tool, "name", "") or "<unnamed>")
        enum_fields = (
            ("risk", self.risk, ToolRisk),
            ("side_effect", self.side_effect, SideEffectClass),
            ("tenant_scope", self.tenant_scope, TenantScope),
            ("credential_behavior", self.credential_behavior, CredentialBehavior),
            ("approval_class", self.approval_class, ApprovalClass),
            ("audit_class", self.audit_class, AuditClass),
            ("idempotency_contract", self.idempotency_contract, IdempotencyContract),
            ("reconciliation_method", self.reconciliation_method, ReconciliationMethod),
        )
        for field_name, value, enum_type in enum_fields:
            if not isinstance(value, enum_type):
                raise ProductionToolPolicyError(
                    f"{tool_name}: production policy {field_name} is incomplete"
                )
        if not isinstance(self.path_extractor, ResourceExtractor):
            raise ProductionToolPolicyError(
                f"{tool_name}: production path extractor is incomplete"
            )
        if not isinstance(self.host_extractor, ResourceExtractor):
            raise ProductionToolPolicyError(
                f"{tool_name}: production host extractor is incomplete"
            )
        schema = getattr(tool, "input_schema", None)
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ProductionToolPolicyError(
                f"{tool_name}: production tool input schema must be an object"
            )
        self.path_extractor.validate_schema(
            tool_name=tool_name,
            resource_name="path",
            input_schema=schema,
        )
        self.host_extractor.validate_schema(
            tool_name=tool_name,
            resource_name="host",
            input_schema=schema,
        )
        if self.credential_behavior is CredentialBehavior.OPERATOR_AMBIENT:
            raise ProductionToolPolicyError(
                f"{tool_name}: operator-ambient credentials are forbidden in production"
            )
        if self.side_effect is SideEffectClass.NONE:
            if self.approval_class is not ApprovalClass.NOT_REQUIRED:
                raise ProductionToolPolicyError(
                    f"{tool_name}: side-effect-free tool has a mutation approval class"
                )
            if self.idempotency_contract is not IdempotencyContract.NOT_APPLICABLE:
                raise ProductionToolPolicyError(
                    f"{tool_name}: side-effect-free tool has a mutation idempotency contract"
                )
            if self.reconciliation_method is not ReconciliationMethod.NOT_APPLICABLE:
                raise ProductionToolPolicyError(
                    f"{tool_name}: side-effect-free tool has a reconciliation method"
                )
            return
        if self.risk is ToolRisk.LOW:
            raise ProductionToolPolicyError(
                f"{tool_name}: mutation cannot be classified as low risk"
            )
        if self.approval_class is ApprovalClass.NOT_REQUIRED:
            raise ProductionToolPolicyError(
                f"{tool_name}: mutation lacks exact-action approval"
            )
        if self.audit_class is not AuditClass.CONSEQUENTIAL:
            raise ProductionToolPolicyError(
                f"{tool_name}: mutation lacks consequential audit class"
            )
        if self.idempotency_contract is IdempotencyContract.NOT_APPLICABLE:
            raise ProductionToolPolicyError(
                f"{tool_name}: mutation lacks idempotency contract"
            )
        if self.reconciliation_method is ReconciliationMethod.NOT_APPLICABLE:
            raise ProductionToolPolicyError(
                f"{tool_name}: mutation lacks reconciliation method"
            )
        if not (
            self.path_extractor.argument_names
            or self.host_extractor.argument_names
        ):
            raise ProductionToolPolicyError(
                f"{tool_name}: mutation has no path or host extraction boundary"
            )

    def extract_resources(
        self,
        args: dict[str, Any],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if not isinstance(args, dict):
            raise ProductionToolPolicyError("tool arguments must be an object")
        return (
            self.path_extractor.extract(args, resource_name="path"),
            self.host_extractor.extract(args, resource_name="host"),
        )


@dataclass(frozen=True)
class ProductionToolContract:
    """Manifest-derived allowlist and dispatch rules for one registry."""

    profile_id: str
    manifest_sha256: str
    included_tools: frozenset[str]
    consequential_actions_enabled: bool

    @classmethod
    def from_manifest(cls, manifest: Any) -> ProductionToolContract:
        constraints = getattr(manifest, "constraints", None)
        if not isinstance(constraints, dict):
            raise ProductionToolPolicyError(
                "production manifest constraints are unavailable"
            )
        enabled = constraints.get("consequential_actions_enabled")
        if not isinstance(enabled, bool):
            raise ProductionToolPolicyError(
                "production manifest must explicitly set consequential_actions_enabled"
            )
        surfaces = getattr(manifest, "surfaces", None)
        if not isinstance(surfaces, dict):
            raise ProductionToolPolicyError(
                "production manifest tool surfaces are unavailable"
            )
        tool_names: set[str] = set()
        saw_tool_surface = False
        for surface_id, surface in surfaces.items():
            category = getattr(surface, "category", None)
            if category == "tool":
                saw_tool_surface = True
                if not isinstance(surface_id, str) or not surface_id.startswith("tool:"):
                    raise ProductionToolPolicyError(
                        "production tool surface id must use the tool:<name> namespace"
                    )
                name = surface_id.removeprefix("tool:").strip()
                if not name:
                    raise ProductionToolPolicyError(
                        "production tool surface name must be non-empty"
                    )
                if bool(getattr(surface, "included", False)):
                    tool_names.add(name)
            elif isinstance(surface_id, str) and surface_id.startswith("tool:"):
                raise ProductionToolPolicyError(
                    "production tool surface has a mismatched category"
                )
        if not saw_tool_surface or not tool_names:
            raise ProductionToolPolicyError(
                "production manifest must explicitly include at least one tool surface"
            )
        return cls(
            profile_id=str(getattr(manifest, "profile_id", "") or ""),
            manifest_sha256=str(getattr(manifest, "sha256", "") or ""),
            included_tools=frozenset(tool_names),
            consequential_actions_enabled=enabled,
        )

    def admits(self, tool: Any) -> bool:
        name = str(getattr(tool, "name", "") or "")
        if name not in self.included_tools:
            return False
        policy = getattr(tool, "production_policy", None)
        if not isinstance(policy, ProductionToolPolicy):
            raise ProductionToolPolicyError(
                f"{name}: included production tool lacks complete policy metadata"
            )
        policy.validate(tool)
        if policy.side_effect.is_mutation:
            if not self.consequential_actions_enabled:
                raise ProductionToolPolicyError(
                    f"{name}: mutation is excluded by the production surface"
                )
            from .consequential_receipts import RECEIPT_SCHEMA

            if getattr(tool, "receipt_contract", None) != RECEIPT_SCHEMA:
                raise ProductionToolPolicyError(
                    f"{name}: mutation lacks the typed receipt contract"
                )
        return True

    def validate_dispatch(
        self,
        tool: Any,
        args: dict[str, Any],
        *,
        principal: str | None = None,
    ) -> None:
        name = str(getattr(tool, "name", "") or "")
        if name not in self.included_tools:
            raise ProductionToolPolicyError(
                f"{name}: tool is outside the reviewed production surface"
            )
        policy = getattr(tool, "production_policy", None)
        if not isinstance(policy, ProductionToolPolicy):
            raise ProductionToolPolicyError(
                f"{name}: tool policy metadata is unavailable at dispatch"
            )
        policy.validate(tool)
        if policy.side_effect.is_mutation:
            if not self.consequential_actions_enabled:
                raise ProductionToolPolicyError(
                    f"{name}: mutation dispatch is disabled by the production surface"
                )
            from .consequential_receipts import RECEIPT_SCHEMA

            if getattr(tool, "receipt_contract", None) != RECEIPT_SCHEMA:
                raise ProductionToolPolicyError(
                    f"{name}: mutation dispatch lacks the typed receipt contract"
                )
            if not str(principal or "").strip():
                raise ProductionToolPolicyError(
                    f"{name}: mutation dispatch lacks an authenticated principal"
                )
            from .paths import current_tenant_id_strict

            try:
                tenant_id = str(current_tenant_id_strict() or "").strip()
            except Exception as exc:
                raise ProductionToolPolicyError(
                    f"{name}: mutation dispatch tenant is unavailable"
                ) from exc
            if not tenant_id:
                raise ProductionToolPolicyError(
                    f"{name}: mutation dispatch lacks an explicit tenant"
                )
        policy.extract_resources(args)

    def validate_result(
        self,
        tool: Any,
        args: dict[str, Any],
        result: Any,
        *,
        principal: str | None,
    ) -> None:
        """Admit only an authenticated, fresh, one-use mutation result."""
        policy = getattr(tool, "production_policy", None)
        if not isinstance(policy, ProductionToolPolicy):
            raise ProductionToolPolicyError(
                "production tool policy metadata is unavailable at result admission"
            )
        if not policy.side_effect.is_mutation:
            return
        from .consequential_receipts import (
            ZERO_SHA256,
            ConsequentialReceiptError,
            ConsequentialToolResult,
            ReceiptExpectation,
            ReceiptReplayGuard,
            ReceiptState,
            canonical_request,
            derive_idempotency_key,
            receipt_action,
            receipt_target,
            request_sha256,
        )
        from .paths import current_tenant_id_strict

        name = str(getattr(tool, "name", "") or "")
        principal_id = str(principal or "").strip()
        try:
            tenant_id = str(current_tenant_id_strict() or "").strip()
        except Exception as exc:
            raise ProductionToolPolicyError(
                f"{name}: result tenant is unavailable"
            ) from exc
        if not tenant_id or not principal_id:
            raise ProductionToolPolicyError(
                f"{name}: result lacks tenant or authenticated principal"
            )
        if not isinstance(result, ConsequentialToolResult):
            raise ProductionToolPolicyError(
                f"{name}: mutation result lacks an authoritative typed receipt"
            )
        try:
            frozen = canonical_request(args)
            digest = request_sha256(frozen)
            action = receipt_action(name, frozen)
            target = receipt_target(policy, frozen)
            idempotency_key = derive_idempotency_key(
                tenant_id=tenant_id,
                principal_id=principal_id,
                tool_name=name,
                action=action,
                target=target,
                request_digest=digest,
            )
            receipt = result.receipt
            expectation = ReceiptExpectation(
                tenant_id=tenant_id,
                principal_id=principal_id,
                tool_name=name,
                action=action,
                target=target,
                request_sha256=digest,
                idempotency_key=idempotency_key,
                operation_id=receipt.operation_id,
                reconciliation_method=policy.reconciliation_method.value,
                allowed_states=frozenset(
                    {
                        ReceiptState.COMMITTED,
                        ReceiptState.REFUSED,
                        ReceiptState.INDETERMINATE,
                    }
                ),
                previous_receipt_sha256=None,
            )
            guard = ReceiptReplayGuard()
            if receipt.previous_receipt_sha256 != ZERO_SHA256:
                guard.require_consumed(
                    receipt.previous_receipt_sha256,
                    tenant_id=tenant_id,
                    operation_id=receipt.operation_id,
                    state=ReceiptState.PREPARED,
                )
            guard.consume(receipt, expectation=expectation)
        except ConsequentialReceiptError as exc:
            raise ProductionToolPolicyError(
                f"{name}: mutation receipt was refused: {exc}"
            ) from exc


def configured_production_manifest() -> Any | None:
    """Load a manifest only for an explicitly admitted production artifact.

    Source checkouts and release tooling may point at a manifest for validation.
    Neither its presence nor a path override is production authority.  The same
    explicit mode predicate used by startup and dashboard endpoint policy owns
    this decision so the assurance boundary cannot split across components.
    """

    from .production_scope import load_manifest, production_surface_required

    if not production_surface_required():
        return None
    return load_manifest()


__all__ = [
    "ApprovalClass",
    "AuditClass",
    "CredentialBehavior",
    "IdempotencyContract",
    "ProductionToolContract",
    "ProductionToolPolicy",
    "ProductionToolPolicyError",
    "ReconciliationMethod",
    "ResourceExtractor",
    "SideEffectClass",
    "TenantScope",
    "ToolRisk",
    "configured_production_manifest",
]
