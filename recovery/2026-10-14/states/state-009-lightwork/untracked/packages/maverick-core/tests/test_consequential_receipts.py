from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from maverick.consequential_receipts import (
    RECEIPT_SCHEMA,
    ZERO_SHA256,
    ConsequentialReceipt,
    ConsequentialReceiptError,
    ConsequentialToolResult,
    PolicyDecision,
    ProviderMutationReceipt,
    ReceiptExpectation,
    ReceiptReplayError,
    ReceiptReplayGuard,
    ReceiptState,
    canonical_request,
    derive_idempotency_key,
    issue_receipt,
    receipt_action,
    receipt_target,
    request_sha256,
    verify_receipt,
)
from maverick.governed_tools import wrap_connector_tool
from maverick.paths import reset_tenant, set_tenant
from maverick.production_scope import ProductionManifest, ProductionSurface
from maverick.production_tools import (
    ApprovalClass,
    AuditClass,
    CredentialBehavior,
    IdempotencyContract,
    ProductionToolPolicy,
    ReconciliationMethod,
    ResourceExtractor,
    SideEffectClass,
    TenantScope,
    ToolRisk,
)
from maverick.safety.consent import ConsentDecision
from maverick.tool_results import ToolResultState, classify_tool_result
from maverick.tools import Tool, ToolRegistry

pytestmark = pytest.mark.usefixtures("local_audit_key_custody")


_NOW = datetime(2026, 8, 3, 16, 0, tzinfo=timezone.utc)
_TENANT = "tenant-a"
_PRINCIPAL = "user:alice"
_TOOL = "records"
_ACTION = "records.post"
_TARGET = '{"hosts":[],"paths":["/records/7"]}'
_REQUEST = "1" * 64
_IDEMPOTENCY = "2" * 64
_OPERATION = "3" * 32
_RECONCILIATION = ReconciliationMethod.AUTHORITATIVE_READ_AFTER_WRITE.value
_EVIDENCE = "4" * 64


def _receipt(
    *,
    state: ReceiptState = ReceiptState.PREPARED,
    now: datetime = _NOW,
) -> ConsequentialReceipt:
    final = state in {ReceiptState.COMMITTED, ReceiptState.INDETERMINATE}
    return issue_receipt(
        state=state,
        tenant_id=_TENANT,
        principal_id=_PRINCIPAL,
        tool_name=_TOOL,
        action=_ACTION,
        target=_TARGET,
        request_digest=_REQUEST,
        idempotency_key=_IDEMPOTENCY,
        operation_id=_OPERATION,
        policy_decision=(
            PolicyDecision.DENIED
            if state is ReceiptState.REFUSED
            else PolicyDecision.GRANTED
        ),
        policy_source="dashboard",
        policy_actor="approver:bob",
        policy_evidence_sha256=_EVIDENCE,
        external_reference="provider:operation/123" if state is ReceiptState.COMMITTED else "",
        reconciliation_method=_RECONCILIATION,
        previous_receipt_sha256="5" * 64 if final else ZERO_SHA256,
        provenance_provider="provider-api",
        provenance_authority="provider-signed-response",
        provenance_subject="account:123",
        provenance_evidence_sha256="6" * 64,
        provenance_authenticated=True,
        now=now,
        ttl_seconds=60,
    )


def _expectation(
    receipt: ConsequentialReceipt,
    *,
    states: frozenset[ReceiptState] | None = None,
) -> ReceiptExpectation:
    return ReceiptExpectation(
        tenant_id=_TENANT,
        principal_id=_PRINCIPAL,
        tool_name=_TOOL,
        action=_ACTION,
        target=_TARGET,
        request_sha256=_REQUEST,
        idempotency_key=_IDEMPOTENCY,
        operation_id=_OPERATION,
        reconciliation_method=_RECONCILIATION,
        allowed_states=states or frozenset({receipt.state}),
        previous_receipt_sha256=receipt.previous_receipt_sha256,
    )


def test_exact_receipt_schema_rejects_malformed_records() -> None:
    receipt = _receipt()
    malformed = receipt.as_dict()
    malformed.pop("tenant_id")

    with pytest.raises(ConsequentialReceiptError, match="missing or unexpected"):
        ConsequentialReceipt.from_mapping(malformed)

    malformed = receipt.as_dict()
    malformed["unexpected"] = True
    with pytest.raises(ConsequentialReceiptError, match="missing or unexpected"):
        ConsequentialReceipt.from_mapping(malformed)


def test_tampered_receipt_fails_signature_and_digest_verification() -> None:
    receipt = _receipt()
    forged = receipt.as_dict()
    forged["target"] = '{"hosts":[],"paths":["/records/8"]}'

    with pytest.raises(ConsequentialReceiptError, match="payload digest"):
        verify_receipt(
            forged,
            expectation=_expectation(receipt),
            now=_NOW + timedelta(seconds=1),
        )


def test_stale_receipt_is_rejected() -> None:
    receipt = _receipt(now=_NOW - timedelta(minutes=10))

    with pytest.raises(ConsequentialReceiptError, match="stale"):
        verify_receipt(receipt, expectation=_expectation(receipt), now=_NOW)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("tenant_id", "tenant-b"),
        ("principal_id", "user:mallory"),
        ("tool_name", "other-tool"),
        ("action", "records.delete"),
        ("target", '{"hosts":[],"paths":["/records/8"]}'),
        ("idempotency_key", "9" * 64),
    ],
)
def test_receipt_cannot_be_substituted_across_dispatch_context(
    field: str,
    replacement: str,
) -> None:
    receipt = _receipt()
    expectation = replace(_expectation(receipt), **{field: replacement})

    with pytest.raises(ConsequentialReceiptError, match=f"{field} mismatches"):
        verify_receipt(
            receipt,
            expectation=expectation,
            now=_NOW + timedelta(seconds=1),
        )


def test_receipt_replay_claim_is_durable_and_one_use(tmp_path: Path) -> None:
    receipt = _receipt()
    guard = ReceiptReplayGuard(tmp_path / "claims")
    expectation = _expectation(receipt)

    guard.consume(receipt, expectation=expectation, now=_NOW + timedelta(seconds=1))

    with pytest.raises(ReceiptReplayError, match="already consumed"):
        ReceiptReplayGuard(tmp_path / "claims").consume(
            receipt,
            expectation=expectation,
            now=_NOW + timedelta(seconds=2),
        )


def test_typed_result_state_ignores_misleading_display_text() -> None:
    result = ConsequentialToolResult("OK: provider says success", _receipt())

    assert classify_tool_result(result) is ToolResultState.PREPARED


def _mutation_policy() -> ProductionToolPolicy:
    return ProductionToolPolicy(
        risk=ToolRisk.HIGH,
        side_effect=SideEffectClass.EXTERNAL_MUTATION,
        tenant_scope=TenantScope.CURRENT_TENANT,
        path_extractor=ResourceExtractor(
            argument_names=("path",),
            required=True,
            fallback_values=(),
        ),
        host_extractor=ResourceExtractor(
            argument_names=(),
            required=False,
            fallback_values=(),
        ),
        credential_behavior=CredentialBehavior.PRINCIPAL_SCOPED,
        approval_class=ApprovalClass.EXACT_ACTION,
        audit_class=AuditClass.CONSEQUENTIAL,
        idempotency_contract=IdempotencyContract.PROVIDER_OPERATION_KEY,
        reconciliation_method=ReconciliationMethod.AUTHORITATIVE_READ_AFTER_WRITE,
    )


def _mutation_tool(fn) -> Tool:
    return Tool(
        name=_TOOL,
        description="Mutate an external record",
        input_schema={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["post"]},
                "path": {"type": "string"},
                "confirm": {"type": "boolean"},
            },
            "required": ["op", "path"],
        },
        fn=fn,
        production_policy=_mutation_policy(),
    )


@pytest.fixture
def authorized_runtime(monkeypatch: pytest.MonkeyPatch):
    from maverick import governed_actions
    from maverick.safety import consent

    token = set_tenant(_TENANT)
    monkeypatch.setattr(
        consent,
        "require_consent",
        lambda *args, **kwargs: ConsentDecision(
            True,
            "dashboard",
            "high",
            time.time(),
            "approver:bob",
        ),
    )
    monkeypatch.setattr(governed_actions, "record_tool_lineage", lambda *a, **k: True)
    try:
        yield
    finally:
        reset_tenant(token)


def test_production_wrapper_accepts_only_typed_authenticated_provider_commit(
    authorized_runtime,
) -> None:
    seen: dict[str, object] = {}

    def provider(args: dict) -> ProviderMutationReceipt:
        seen.update(args)
        key = str(args["_maverick_idempotency_key"])
        return ProviderMutationReceipt(
            state=ReceiptState.COMMITTED,
            idempotency_key=key,
            provider="records-api",
            authority="signed-provider-response",
            subject="account:123",
            evidence_sha256="7" * 64,
            external_reference="records-api:operation/456",
            reconciliation_method=_RECONCILIATION,
            authenticated=True,
            display="record committed",
        )

    wrapped = wrap_connector_tool(
        _mutation_tool(provider),
        actor=_PRINCIPAL,
        production=True,
    )
    result = wrapped.fn({"op": "post", "path": "/records/7"})

    assert isinstance(result, ConsequentialToolResult)
    assert result.receipt.state is ReceiptState.COMMITTED
    assert result.receipt.tenant_id == _TENANT
    assert result.receipt.principal_id == _PRINCIPAL
    assert seen["confirm"] is True
    assert len(str(seen["_maverick_idempotency_key"])) == 64
    assert classify_tool_result(result) is ToolResultState.COMMITTED


def test_plain_provider_success_string_becomes_typed_indeterminate(
    authorized_runtime,
) -> None:
    wrapped = wrap_connector_tool(
        _mutation_tool(lambda _args: "COMMITTED: definitely succeeded"),
        actor=_PRINCIPAL,
        production=True,
    )

    result = wrapped.fn({"op": "post", "path": "/records/7"})

    assert isinstance(result, ConsequentialToolResult)
    assert result.receipt.state is ReceiptState.INDETERMINATE
    assert classify_tool_result(result) is ToolResultState.INDETERMINATE


def test_prepared_receipt_replay_failure_stops_provider_before_effect(
    authorized_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def replay(*args, **kwargs):
        raise ReceiptReplayError("already consumed")

    monkeypatch.setattr(ReceiptReplayGuard, "consume", replay)
    wrapped = wrap_connector_tool(
        _mutation_tool(lambda args: calls.append(args) or "should not run"),
        actor=_PRINCIPAL,
        production=True,
    )

    result = wrapped.fn({"op": "post", "path": "/records/7"})

    assert isinstance(result, ConsequentialToolResult)
    assert result.receipt.state is ReceiptState.REFUSED
    assert calls == []


def _manifest(*, enabled: bool) -> ProductionManifest:
    surface = ProductionSurface(
        surface_id=f"tool:{_TOOL}",
        category="tool",
        status="supported_beta",
        included=True,
    )
    return ProductionManifest(
        path=Path("production-surface-manifest.json"),
        sha256="8" * 64,
        profile_id="receipt-test",
        source_version="0.0.0-test",
        release_eligible=False,
        release_reason="test",
        constraints={"consequential_actions_enabled": enabled},
        surfaces={surface.surface_id: surface},
    )


def test_production_registry_validates_and_consumes_final_receipt(
    authorized_runtime,
) -> None:
    def provider(args: dict) -> ProviderMutationReceipt:
        return ProviderMutationReceipt(
            state=ReceiptState.COMMITTED,
            idempotency_key=str(args["_maverick_idempotency_key"]),
            provider="records-api",
            authority="signed-provider-response",
            subject="account:123",
            evidence_sha256="a" * 64,
            external_reference="records-api:operation/789",
            reconciliation_method=_RECONCILIATION,
            authenticated=True,
            display="committed",
        )

    registry = ToolRegistry(
        principal=_PRINCIPAL,
        production_manifest=_manifest(enabled=True),
    )
    registry.register(
        wrap_connector_tool(
            _mutation_tool(provider),
            actor=_PRINCIPAL,
            production=True,
        )
    )

    result = asyncio.run(
        registry.run(_TOOL, {"op": "post", "path": "/records/7"})
    )

    assert isinstance(result, ConsequentialToolResult)
    assert result.receipt.state is ReceiptState.COMMITTED


def test_registry_rejects_replayed_final_receipt(
    authorized_runtime,
) -> None:
    args = {"op": "post", "path": "/records/7"}
    frozen = canonical_request(args)
    policy = _mutation_policy()
    digest = request_sha256(frozen)
    action = receipt_action(_TOOL, frozen)
    target = receipt_target(policy, frozen)
    key = derive_idempotency_key(
        tenant_id=_TENANT,
        principal_id=_PRINCIPAL,
        tool_name=_TOOL,
        action=action,
        target=target,
        request_digest=digest,
    )
    prepared = issue_receipt(
        state=ReceiptState.PREPARED,
        tenant_id=_TENANT,
        principal_id=_PRINCIPAL,
        tool_name=_TOOL,
        action=action,
        target=target,
        request_digest=digest,
        idempotency_key=key,
        operation_id=_OPERATION,
        policy_decision=PolicyDecision.GRANTED,
        policy_source="dashboard",
        policy_actor="approver:bob",
        policy_evidence_sha256=_EVIDENCE,
        external_reference="",
        reconciliation_method=_RECONCILIATION,
        previous_receipt_sha256=ZERO_SHA256,
        provenance_provider="lightwork-consent",
        provenance_authority="dashboard",
        provenance_subject="approver:bob",
        provenance_evidence_sha256=_EVIDENCE,
        provenance_authenticated=True,
    )
    prepared_expectation = ReceiptExpectation(
        tenant_id=_TENANT,
        principal_id=_PRINCIPAL,
        tool_name=_TOOL,
        action=action,
        target=target,
        request_sha256=digest,
        idempotency_key=key,
        operation_id=_OPERATION,
        reconciliation_method=_RECONCILIATION,
        allowed_states=frozenset({ReceiptState.PREPARED}),
        previous_receipt_sha256=ZERO_SHA256,
    )
    ReceiptReplayGuard().consume(prepared, expectation=prepared_expectation)
    final = issue_receipt(
        state=ReceiptState.COMMITTED,
        tenant_id=_TENANT,
        principal_id=_PRINCIPAL,
        tool_name=_TOOL,
        action=action,
        target=target,
        request_digest=digest,
        idempotency_key=key,
        operation_id=_OPERATION,
        policy_decision=PolicyDecision.GRANTED,
        policy_source="dashboard",
        policy_actor="approver:bob",
        policy_evidence_sha256=_EVIDENCE,
        external_reference="records-api:operation/replay",
        reconciliation_method=_RECONCILIATION,
        previous_receipt_sha256=prepared.receipt_sha256,
        provenance_provider="records-api",
        provenance_authority="signed-provider-response",
        provenance_subject="account:123",
        provenance_evidence_sha256="b" * 64,
        provenance_authenticated=True,
    )
    result = ConsequentialToolResult("committed", final)
    tool = _mutation_tool(lambda _args: result)
    tool.receipt_contract = RECEIPT_SCHEMA
    registry = ToolRegistry(
        principal=_PRINCIPAL,
        production_manifest=_manifest(enabled=True),
    )
    registry.register(tool)

    first = asyncio.run(registry.run(_TOOL, args))
    second = asyncio.run(registry.run(_TOOL, args))

    assert first is result
    assert str(second).startswith("INDETERMINATE (production receipt policy)")


def test_current_disabled_manifest_still_rejects_consequential_tool() -> None:
    registry = ToolRegistry(
        principal=_PRINCIPAL,
        production_manifest=_manifest(enabled=False),
    )
    wrapped = wrap_connector_tool(
        _mutation_tool(lambda _args: "not called"),
        actor=_PRINCIPAL,
        production=True,
    )

    with pytest.raises(Exception, match="mutation is excluded"):
        registry.register(wrapped)
