"""Contract, adversarial, and local-runtime tests for cloud-agent governance."""

from __future__ import annotations

import dataclasses
import hashlib
import pickle
import threading

import pytest
from maverick.ai_cloud_agent_governance import (
    CLOUD_AGENT_LIMIT_NOTICE,
    FRAMEWORK_LIMIT_NOTICE,
    AdapterDisposition,
    AzureAIFoundryEnforcementAdapter,
    CloudAgentAdapterError,
    CloudAgentAmbiguousEffect,
    CloudAgentBindingError,
    CloudAgentConfigError,
    CloudAgentGovernanceConfig,
    CloudAgentGovernanceDisabled,
    CloudAgentIdempotencyConflict,
    CloudAgentIntegrityError,
    CloudAgentStaleRevision,
    CloudAgentStateError,
    DatabricksFrameworkContractAdapter,
    EnforcementDispatchReceipt,
    FrameworkActivation,
    FrameworkApplicability,
    FrameworkAttestation,
    FrameworkEvidenceLink,
    HMACAdapterReceiptAuthenticator,
    InMemoryProviderResourceRegistry,
    InMemoryProviderRevisionAuthority,
    InventoryPage,
    PolicyEvaluationResult,
    ProviderAdapterResult,
    ProviderMutationOutcome,
    TenantAuthority,
    TenantBoundReferencePseudonymizer,
    VertexAIEnforcementAdapter,
    build_adapter_result_verifier,
    build_cloud_agent_governance_runtime,
    build_provider_inventory_contract_adapter,
)
from maverick.ai_inventory import DiscoveredAsset, DiscoveryResult


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ref(prefix: str, value: str) -> str:
    return f"{prefix}_{_digest(value)[:32]}"


class _Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float = 1.0) -> None:
        self.now += seconds


class _NoopAdapter:
    def submit(self, request):
        del request
        raise AssertionError("mutation transport must not be called")


class _AllowEvaluator:
    def __init__(self) -> None:
        self.requests = []

    def evaluate(self, request):
        self.requests.append(request)
        return PolicyEvaluationResult(
            "allow_intent",
            request.requested_action,
            request.target_ref,
            ("policy.approved",),
        )


class _BlockingEvaluator(_AllowEvaluator):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def evaluate(self, request):
        self.entered.set()
        assert self.release.wait(5)
        return super().evaluate(request)


class _InventoryAdapter:
    def __init__(self, result: DiscoveryResult) -> None:
        self.result = result

    def discover(self) -> DiscoveryResult:
        return self.result


class _MutationTransport:
    def __init__(
        self,
        dispositions: tuple[AdapterDisposition, ...] = ("accepted",),
        *,
        fail: bool = False,
    ) -> None:
        self.dispositions = list(dispositions)
        self.fail = fail
        self.calls = []

    def resolve_and_submit(self, *, operation, request, resolved):
        self.calls.append((operation, request, resolved))
        if self.fail:
            raise RuntimeError("provider response contained secret material")
        disposition = self.dispositions.pop(0)
        return ProviderMutationOutcome(
            tenant_sha256=request.tenant_sha256,
            provider=request.provider,
            binding_ref=request.binding_ref,
            binding_revision=request.binding_revision,
            intent_ref=request.intent_ref,
            effect_sha256=request.effect_sha256,
            request_sha256=request.request_sha256,
            operation=operation,
            disposition=disposition,
            receipt_sha256=_digest(f"receipt-{len(self.calls)}"),
            observed_at=1_800_000_000.0 + len(self.calls),
        )


def _config(
    *providers: str,
    databricks: bool = False,
    max_records: int = 10_000,
    max_events: int = 5_000,
) -> dict[str, object]:
    return {
        "ai_cloud_agent_governance": {
            "enable": True,
            "enabled_providers": list(providers or ("azure_ai_foundry",)),
            "databricks_framework_enable": databricks,
            "max_records_per_sync": max_records,
            "max_events_per_batch": max_events,
            "retention_seconds": 31_536_000,
        }
    }


def _runtime(
    *providers: str,
    evaluator=None,
    databricks: bool = False,
    clock: _Clock | None = None,
    max_records: int = 10_000,
):
    selected_clock = clock or _Clock()
    runtime = build_cloud_agent_governance_runtime(
        config_root=_config(
            *(providers or ("azure_ai_foundry",)),
            databricks=databricks,
            max_records=max_records,
        ),
        policy_evaluator=evaluator,
        reference_key=b"r" * 32,
        allow_process_local_stores=True,
        clock=selected_clock,
    )
    return runtime, selected_clock


def _tenant(name: str) -> TenantAuthority:
    return TenantAuthority(_digest(f"tenant-{name}"), _digest(f"authority-{name}"), 1)


def _bind(runtime, tenant: TenantAuthority, provider: str, adapter=None):
    authority = runtime.authority
    authority.enroll_tenant(tenant, idempotency_key=f"enroll-{tenant.tenant_sha256}")
    return authority.bind_provider(
        tenant,
        provider=provider,
        binding_ref=_ref("cab", f"{tenant.tenant_sha256}-{provider}"),
        adapter_identity_sha256=_digest(f"adapter-{provider}"),
        adapter=adapter or _NoopAdapter(),
        verifier=lambda _binding, _result: False,
        expected_revision=0,
        idempotency_key=f"bind-{provider}-{tenant.tenant_sha256}",
    )


def _asset(
    *,
    provider: str = "azure_ai_foundry",
    native_id: str = "agent-native-1",
    status: str = "Enabled",
    kind: str = "HostedAgent",
    model: str = "model-a",
    version: str = "v1",
    observed_at: float = 1_800_000_000.0,
) -> DiscoveredAsset:
    platform = {
        "aws_bedrock": "AWS Bedrock",
        "azure_ai_foundry": "Azure AI Foundry",
        "google_vertex_ai": "Google Vertex AI",
    }[provider]
    return DiscoveredAsset(
        asset_type="agent",
        source=provider,
        source_id=native_id,
        display_name="Secret customer agent name",
        version_digest=_digest(version),
        binding_strength="observed_snapshot",
        owner="private-owner@example.test",
        platform=platform,
        model=model,
        last_seen=observed_at,
        source_url="https://provider.example.test/private/agent",
        metadata={"resource_kind": "agent", "status": status, "kind": kind},
        dependencies=(model,),
    )


def _sync(
    runtime,
    tenant: TenantAuthority,
    asset: DiscoveredAsset,
    *,
    scope: str = "scope-a",
    expected_revision: int = 0,
    key: str = "inventory-sync-a",
):
    assert runtime.inventory_runner is not None
    return runtime.inventory_runner.sync(
        tenant,
        provider=asset.source,
        inventory_adapter=_InventoryAdapter(DiscoveryResult((asset,), 1)),
        scope_sha256=_digest(scope),
        expected_inventory_revision=expected_revision,
        idempotency_key=key,
    )


def _inventory_record(runtime, tenant: TenantAuthority, provider: str = "azure_ai_foundry"):
    page = runtime.authority.list_inventory(tenant, provider=provider)
    assert len(page.records) == 1
    return page.records[0]


def _bound_enforcement_runtime(
    *,
    evaluator=None,
    provider: str = "azure_ai_foundry",
    kind: str = "HostedAgent",
    dispositions: tuple[AdapterDisposition, ...] = ("accepted",),
    fail: bool = False,
    verifier_key: bytes = b"s" * 32,
):
    selected_evaluator = evaluator or _AllowEvaluator()
    runtime, clock = _runtime(provider, evaluator=selected_evaluator)
    tenant = _tenant("enforcement")
    registry = runtime.resource_registry
    assert registry is not None
    auth = HMACAdapterReceiptAuthenticator(b"s" * 32)
    transport = _MutationTransport(dispositions, fail=fail)
    binding_ref = _ref("cab", f"{tenant.tenant_sha256}-{provider}")
    adapter_type = (
        AzureAIFoundryEnforcementAdapter
        if provider == "azure_ai_foundry"
        else VertexAIEnforcementAdapter
    )
    adapter = adapter_type(
        tenant_sha256=tenant.tenant_sha256,
        binding_ref=binding_ref,
        binding_revision=1,
        resource_registry=registry,
        transport=transport,
        signer=auth,
        clock=clock,
    )
    runtime.authority.enroll_tenant(tenant, idempotency_key="enroll-enforcement")
    runtime.authority.bind_provider(
        tenant,
        provider=provider,
        binding_ref=binding_ref,
        adapter_identity_sha256=_digest("enforcement-adapter"),
        adapter=adapter,
        verifier=build_adapter_result_verifier(HMACAdapterReceiptAuthenticator(verifier_key)),
        expected_revision=0,
        idempotency_key="bind-enforcement",
    )
    _sync(runtime, tenant, _asset(provider=provider, kind=kind))
    return runtime, tenant, selected_evaluator, transport, clock


def _prepared_intent(runtime, tenant: TenantAuthority):
    record = _inventory_record(runtime, tenant)
    evaluation = runtime.authority.evaluate_policy(
        tenant,
        agent_ref=record.agent.agent_ref,
        expected_agent_revision=record.record_revision,
        policy_ref=_ref("pol", "disable-policy"),
        policy_revision=1,
        policy_sha256=_digest("disable-policy-v1"),
        requested_action="disable_agent",
        target_ref=record.agent.agent_ref,
        idempotency_key="evaluate-disable",
    )
    return runtime.authority.prepare_enforcement_intent(
        tenant,
        evaluation_ref=evaluation.evaluation_ref,
        idempotency_key="prepare-disable",
    )


def test_config_round_trip_aligns_vertex_and_dormant_default_off_allowlist() -> None:
    config = CloudAgentGovernanceConfig.from_mapping(
        {
            "enable": True,
            "enabled_providers": ["google_vertex_ai", "aws_bedrock"],
            "databricks_framework_enable": False,
        }
    )
    assert config.enabled_providers == ("aws_bedrock", "google_vertex_ai")
    dormant = CloudAgentGovernanceConfig.from_mapping(
        {
            "enable": False,
            "enabled_providers": [
                "aws_bedrock",
                "azure_ai_foundry",
                "google_vertex_ai",
            ],
        }
    )
    assert dormant.enable is False
    with pytest.raises(ValueError, match="unsupported"):
        CloudAgentGovernanceConfig.from_mapping(
            {"enable": True, "enabled_providers": ["google_vertex"]}
        )


def test_enabled_runtime_requires_key_and_durable_stores_by_default() -> None:
    with pytest.raises(CloudAgentConfigError, match="reference key"):
        build_cloud_agent_governance_runtime(config_root=_config("aws_bedrock"))
    with pytest.raises(CloudAgentConfigError, match="durable"):
        build_cloud_agent_governance_runtime(
            config_root=_config("aws_bedrock"),
            reference_key=b"k" * 32,
        )
    disabled = build_cloud_agent_governance_runtime(
        config_root={
            "ai_cloud_agent_governance": {
                "enable": False,
                "enabled_providers": ["aws_bedrock"],
            }
        }
    )
    assert disabled.inventory_runner is None
    assert disabled.store_mode == "disabled"
    with pytest.raises(CloudAgentGovernanceDisabled):
        disabled.authority.enroll_tenant(_tenant("disabled"), idempotency_key="disabled")
    local, _clock = _runtime("aws_bedrock")
    assert local.store_mode == "process_local_test_only"
    with pytest.raises(CloudAgentConfigError, match="durable"):
        build_cloud_agent_governance_runtime(
            config_root=_config("aws_bedrock"),
            reference_key=b"k" * 32,
            revision_authority=InMemoryProviderRevisionAuthority(),
            resource_registry=InMemoryProviderResourceRegistry(),
        )


def test_inventory_sync_hashes_raw_fields_and_registers_server_only_binding() -> None:
    runtime, _clock = _runtime("azure_ai_foundry")
    tenant = _tenant("inventory")
    _bind(runtime, tenant, "azure_ai_foundry")
    native_id = "customer-secret-native-agent"
    receipt = _sync(runtime, tenant, _asset(native_id=native_id))[-1]
    assert receipt.finalized is True
    record = _inventory_record(runtime, tenant)
    rendered = repr(record) + str(record.payload())
    assert native_id not in rendered
    assert "private-owner" not in rendered
    assert record.agent.relationships.tool_inventory_complete is False
    assert record.agent.relationships.data_source_inventory_complete is False
    registry = runtime.resource_registry
    assert isinstance(registry, InMemoryProviderResourceRegistry)
    native = next(iter(registry._agents.values()))
    assert native_id not in repr(native)
    with pytest.raises(TypeError):
        dataclasses.asdict(native)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(native)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(TenantBoundReferencePseudonymizer(b"p" * 32))
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(HMACAdapterReceiptAuthenticator(b"s" * 32))


def test_keyed_references_are_deterministic_tenant_and_scope_bound() -> None:
    pseudonymizer = TenantBoundReferencePseudonymizer(b"p" * 32)
    revisions = InMemoryProviderRevisionAuthority(max_resources=10)
    clock = _Clock()
    tenant_a = _tenant("a")
    tenant_b = _tenant("b")
    provider = "azure_ai_foundry"
    runtime, _ = _runtime(provider)
    binding_a = _bind(runtime, tenant_a, provider)
    binding_b = _bind(runtime, tenant_b, provider)
    normalizer = build_provider_inventory_contract_adapter(
        provider,
        pseudonymizer=pseudonymizer,
        revision_authority=revisions,
        clock=clock,
    )
    result = DiscoveryResult((_asset(native_id="low-entropy-id"),), 1)
    first = normalizer.normalize_discovery(
        result, authority=tenant_a, binding=binding_a, scope_sha256=_digest("scope-a")
    )
    again = normalizer.normalize_discovery(
        result, authority=tenant_a, binding=binding_a, scope_sha256=_digest("scope-a")
    )
    other_tenant = normalizer.normalize_discovery(
        result, authority=tenant_b, binding=binding_b, scope_sha256=_digest("scope-a")
    )
    other_scope = normalizer.normalize_discovery(
        result, authority=tenant_a, binding=binding_a, scope_sha256=_digest("scope-b")
    )
    refs = [
        batch.pages[0].items[0].agent_ref for batch in (first, again, other_tenant, other_scope)
    ]
    assert refs[0] == refs[1]
    assert len(set(refs)) == 3
    assert "low-entropy-id" not in "".join(refs)


def test_complete_scope_replacement_does_not_delete_another_scope() -> None:
    runtime, clock = _runtime("azure_ai_foundry")
    tenant = _tenant("scopes")
    _bind(runtime, tenant, "azure_ai_foundry")
    _sync(runtime, tenant, _asset(native_id="scope-a-agent"), scope="scope-a")
    clock.tick()
    _sync(
        runtime,
        tenant,
        _asset(native_id="scope-b-agent", observed_at=clock.now),
        scope="scope-b",
        expected_revision=1,
        key="inventory-sync-b",
    )
    assert runtime.authority.list_inventory(tenant, provider="azure_ai_foundry").total == 2
    clock.tick()
    assert runtime.inventory_runner is not None
    runtime.inventory_runner.sync(
        tenant,
        provider="azure_ai_foundry",
        inventory_adapter=_InventoryAdapter(DiscoveryResult((), 1)),
        scope_sha256=_digest("scope-a"),
        expected_inventory_revision=2,
        idempotency_key="inventory-sync-a-empty",
    )
    records = runtime.authority.list_inventory(tenant, provider="azure_ai_foundry").records
    assert len(records) == 1
    registry = runtime.resource_registry
    assert isinstance(registry, InMemoryProviderResourceRegistry)
    assert len(registry._agents) == 1


def test_native_registry_failure_does_not_commit_canonical_inventory() -> None:
    class _FailingRegistry(InMemoryProviderResourceRegistry):
        def replace_scope(self, **kwargs) -> None:
            del kwargs
            raise RuntimeError("secret native resource id")

    clock = _Clock()
    config = _config("azure_ai_foundry")
    runtime = build_cloud_agent_governance_runtime(
        config_root=config,
        policy_evaluator=_AllowEvaluator(),
        reference_key=b"r" * 32,
        resource_registry=_FailingRegistry(),
        revision_authority=InMemoryProviderRevisionAuthority(),
        allow_process_local_stores=True,
        clock=clock,
    )
    tenant = _tenant("registry-failure")
    _bind(runtime, tenant, "azure_ai_foundry")
    with pytest.raises(CloudAgentAdapterError, match="registry") as caught:
        _sync(runtime, tenant, _asset())
    assert "secret native resource id" not in str(caught.value)
    assert (
        runtime.authority.list_inventory(
            tenant,
            provider="azure_ai_foundry",
        ).total
        == 0
    )


def test_scan_base_revision_tombstone_manifest_and_source_replay_guards() -> None:
    runtime, clock = _runtime("azure_ai_foundry", max_records=1_000)
    tenant = _tenant("scan")
    binding = _bind(runtime, tenant, "azure_ai_foundry")
    pseudonymizer = TenantBoundReferencePseudonymizer(b"r" * 32)
    revisions = runtime.revision_authority
    assert revisions is not None
    normalizer = build_provider_inventory_contract_adapter(
        "azure_ai_foundry",
        pseudonymizer=pseudonymizer,
        revision_authority=revisions,
        max_records_per_sync=1_000,
        clock=clock,
    )
    many = DiscoveryResult(
        tuple(_asset(native_id=f"agent-{index}") for index in range(501)),
        6,
    )
    scan_a = normalizer.normalize_discovery(
        many,
        authority=tenant,
        binding=binding,
        scope_sha256=_digest("scope-a"),
    )
    assert len(scan_a.pages) == 2
    runtime.authority.ingest_inventory_page(
        tenant,
        scan_a.pages[0],
        expected_inventory_revision=0,
        idempotency_key="scan-a-page-0",
    )
    clock.tick()
    scan_b = normalizer.normalize_discovery(
        DiscoveryResult((_asset(native_id="winner", observed_at=clock.now),), 1),
        authority=tenant,
        binding=binding,
        scope_sha256=_digest("scope-b"),
    )
    runtime.authority.ingest_inventory_page(
        tenant,
        scan_b.pages[0],
        expected_inventory_revision=0,
        idempotency_key="scan-b-page-0",
    )
    with pytest.raises(CloudAgentStaleRevision, match="base revision"):
        runtime.authority.ingest_inventory_page(
            tenant,
            scan_a.pages[1],
            expected_inventory_revision=1,
            idempotency_key="scan-a-page-1",
        )
    with pytest.raises(CloudAgentIdempotencyConflict, match="finalized"):
        runtime.authority.ingest_inventory_page(
            tenant,
            scan_b.pages[0],
            expected_inventory_revision=1,
            idempotency_key="scan-b-replayed-under-new-key",
        )
    clock.tick()
    repeated_source = normalizer.normalize_discovery(
        DiscoveryResult((_asset(native_id="winner", observed_at=clock.now - 1),), 1),
        authority=tenant,
        binding=binding,
        scope_sha256=_digest("scope-b"),
    )
    with pytest.raises(CloudAgentIdempotencyConflict, match="source snapshot"):
        runtime.authority.ingest_inventory_page(
            tenant,
            repeated_source.pages[0],
            expected_inventory_revision=1,
            idempotency_key="same-source-new-scan",
        )
    bad_manifest = InventoryPage.build(
        provider=scan_a.pages[1].provider,
        binding_ref=binding.binding_ref,
        binding_revision=binding.revision,
        scan_ref=_ref("cas", "bad-manifest"),
        scope_sha256=_digest("bad-manifest-scope"),
        source_snapshot_sha256=_digest("bad-manifest-snapshot"),
        manifest_sha256=_digest("forged-manifest"),
        page_index=0,
        page_count=1,
        continuation_sha256=None,
        snapshot_complete=True,
        incomplete_reason_codes=(),
        captured_at=clock.now,
        items=scan_a.pages[1].items,
    )
    with pytest.raises(CloudAgentIntegrityError, match="manifest"):
        runtime.authority.ingest_inventory_page(
            tenant,
            bad_manifest,
            expected_inventory_revision=1,
            idempotency_key="bad-manifest-page",
        )


def test_provider_enforcement_accepts_signed_azure_hosted_disable() -> None:
    runtime, tenant, _evaluator, transport, _clock = _bound_enforcement_runtime()
    intent = _prepared_intent(runtime, tenant)
    receipt = runtime.authority.dispatch_enforcement_intent(
        tenant,
        intent_ref=intent.intent_ref,
        expected_revision=1,
        idempotency_key="dispatch-disable",
    )
    assert receipt.intent.status == "adapter_accepted"
    assert receipt.intent.revision == 3
    assert receipt.provider_write_verified is False
    assert receipt.limit_notice == CLOUD_AGENT_LIMIT_NOTICE
    assert transport.calls[0][0] == "azure.foundry_agents.disable"
    assert transport.calls[0][2].native_target_id == "agent-native-1"


def test_vertex_and_non_hosted_azure_reject_without_transport() -> None:
    vertex, tenant, _evaluator, transport, _clock = _bound_enforcement_runtime(
        provider="google_vertex_ai"
    )
    record = _inventory_record(vertex, tenant, "google_vertex_ai")
    evaluation = vertex.authority.evaluate_policy(
        tenant,
        agent_ref=record.agent.agent_ref,
        expected_agent_revision=record.record_revision,
        policy_ref=_ref("pol", "vertex-policy"),
        policy_revision=1,
        policy_sha256=_digest("vertex-policy"),
        requested_action="disable_agent",
        target_ref=record.agent.agent_ref,
        idempotency_key="vertex-evaluate",
    )
    intent = vertex.authority.prepare_enforcement_intent(
        tenant,
        evaluation_ref=evaluation.evaluation_ref,
        idempotency_key="vertex-prepare",
    )
    result = vertex.authority.dispatch_enforcement_intent(
        tenant,
        intent_ref=intent.intent_ref,
        expected_revision=1,
        idempotency_key="vertex-dispatch",
    )
    assert result.intent.status == "adapter_rejected"
    assert transport.calls == []


def test_registry_integrity_failure_is_not_downgraded_to_adapter_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, tenant, _evaluator, transport, _clock = _bound_enforcement_runtime()
    intent = _prepared_intent(runtime, tenant)
    registry = runtime.resource_registry
    assert registry is not None

    def corrupt_registry(_request):
        raise CloudAgentIntegrityError("native registry integrity failed")

    monkeypatch.setattr(registry, "resolve_dispatch", corrupt_registry)
    with pytest.raises(CloudAgentAmbiguousEffect, match="reconciliation"):
        runtime.authority.dispatch_enforcement_intent(
            tenant,
            intent_ref=intent.intent_ref,
            expected_revision=1,
            idempotency_key="dispatch-corrupt-registry",
        )
    current = runtime.authority.get_enforcement_intent(
        tenant,
        intent_ref=intent.intent_ref,
    )
    assert current.status == "reconciliation_required"
    assert transport.calls == []

    azure, tenant, _evaluator, transport, _clock = _bound_enforcement_runtime(
        kind="ClassicAssistant"
    )
    result = azure.authority.dispatch_enforcement_intent(
        tenant,
        intent_ref=_prepared_intent(azure, tenant).intent_ref,
        expected_revision=1,
        idempotency_key="classic-dispatch",
    )
    assert result.intent.status == "adapter_rejected"
    assert transport.calls == []


def test_retry_uses_stable_effect_identity_and_attempt_bound_request_digest() -> None:
    runtime, tenant, _evaluator, transport, _clock = _bound_enforcement_runtime(
        dispositions=("retryable", "accepted")
    )
    intent = _prepared_intent(runtime, tenant)
    first = runtime.authority.dispatch_enforcement_intent(
        tenant,
        intent_ref=intent.intent_ref,
        expected_revision=1,
        idempotency_key="dispatch-first",
    )
    assert first.intent.status == "retryable"
    second = runtime.authority.dispatch_enforcement_intent(
        tenant,
        intent_ref=intent.intent_ref,
        expected_revision=3,
        idempotency_key="dispatch-second",
    )
    assert second.intent.status == "adapter_accepted"
    requests = [call[1] for call in transport.calls]
    assert requests[0].effect_sha256 == requests[1].effect_sha256
    assert requests[0].request_sha256 != requests[1].request_sha256


def test_maybe_sent_or_bad_signature_becomes_reconciliation_required() -> None:
    runtime, tenant, _evaluator, _transport, _clock = _bound_enforcement_runtime(fail=True)
    intent = _prepared_intent(runtime, tenant)
    with pytest.raises(CloudAgentAmbiguousEffect, match="reconciliation") as caught:
        runtime.authority.dispatch_enforcement_intent(
            tenant,
            intent_ref=intent.intent_ref,
            expected_revision=1,
            idempotency_key="dispatch-outage",
        )
    assert "secret material" not in str(caught.value)
    current = runtime.authority.get_enforcement_intent(tenant, intent_ref=intent.intent_ref)
    assert current.status == "reconciliation_required"
    assert current.attempts == 1

    mismatched, tenant, _evaluator, transport, _clock = _bound_enforcement_runtime(
        verifier_key=b"v" * 32
    )
    intent = _prepared_intent(mismatched, tenant)
    with pytest.raises(CloudAgentAmbiguousEffect, match="reconciliation"):
        mismatched.authority.dispatch_enforcement_intent(
            tenant,
            intent_ref=intent.intent_ref,
            expected_revision=1,
            idempotency_key="dispatch-bad-signature",
        )
    assert len(transport.calls) == 1
    current = mismatched.authority.get_enforcement_intent(
        tenant,
        intent_ref=intent.intent_ref,
    )
    assert current.status == "reconciliation_required"


def test_ambiguous_dispatch_can_only_reconcile_against_fresh_exact_inventory() -> None:
    runtime, tenant, _evaluator, _transport, clock = _bound_enforcement_runtime(
        dispositions=("ambiguous",)
    )
    intent = _prepared_intent(runtime, tenant)
    receipt = runtime.authority.dispatch_enforcement_intent(
        tenant,
        intent_ref=intent.intent_ref,
        expected_revision=1,
        idempotency_key="dispatch-ambiguous",
    )
    assert receipt.intent.status == "reconciliation_required"
    with pytest.raises(CloudAgentStaleRevision, match="fresh"):
        runtime.authority.resolve_enforcement_reconciliation(
            tenant,
            intent_ref=intent.intent_ref,
            expected_revision=receipt.intent.revision,
            resolution="applied",
            resolver_ref=_ref("prn", "operator"),
            evidence_ref=_ref("evd", "provider-post-read"),
            evidence_sha256=_digest("provider-post-read"),
            observed_inventory_revision=1,
            idempotency_key="reconcile-stale",
        )
    clock.tick()
    _sync(
        runtime,
        tenant,
        _asset(status="Disabled", version="v2", observed_at=clock.now),
        expected_revision=1,
        key="inventory-post-read",
    )
    reconciled = runtime.authority.resolve_enforcement_reconciliation(
        tenant,
        intent_ref=intent.intent_ref,
        expected_revision=receipt.intent.revision,
        resolution="applied",
        resolver_ref=_ref("prn", "operator"),
        evidence_ref=_ref("evd", "provider-post-read"),
        evidence_sha256=_digest("provider-post-read"),
        observed_inventory_revision=2,
        idempotency_key="reconcile-applied",
    )
    assert reconciled.status == "reconciled_applied"
    assert reconciled.reconciliation_evidence_sha256 == _digest("provider-post-read")


def test_incomplete_relationships_block_relationship_mutation_before_evaluator() -> None:
    evaluator = _AllowEvaluator()
    runtime, tenant, evaluator, _transport, _clock = _bound_enforcement_runtime(evaluator=evaluator)
    record = _inventory_record(runtime, tenant)
    target = record.agent.relationships.model_refs[0]
    with pytest.raises(CloudAgentStateError, match="model relationship inventory"):
        runtime.authority.evaluate_policy(
            tenant,
            agent_ref=record.agent.agent_ref,
            expected_agent_revision=record.record_revision,
            policy_ref=_ref("pol", "restrict-model"),
            policy_revision=1,
            policy_sha256=_digest("restrict-model"),
            requested_action="restrict_model",
            target_ref=target,
            idempotency_key="evaluate-restrict-model",
        )
    assert evaluator.requests == []


def test_databricks_contract_parser_is_closed_duplicate_safe_and_gated() -> None:
    parser = DatabricksFrameworkContractAdapter()
    document = (
        '{"schema":"lightwork.databricks-ai-security-framework-map.v1",'
        '"framework_id":"databricks_ai_security_framework",'
        '"version":"2026.1","source_revision_sha256":"'
        + _digest("databricks-source")
        + '","mappings":[{"control_id":"DBAI-1",'
        '"lightwork_control_ids":["access.control"],'
        '"applicability_tags":["agent"]}]}'
    ).encode()
    pack = parser.parse_document(document)
    pack.verify()
    with pytest.raises(ValueError, match="unsupported fields"):
        parser.normalize_mapping({**pack.payload(), "licensed_text": "do not accept"})
    duplicate = document.replace(
        b'"version":"2026.1"',
        b'"version":"2026.1","version":"2026.2"',
    )
    with pytest.raises(ValueError, match="strict JSON"):
        parser.parse_document(duplicate)
    runtime, _clock = _runtime("aws_bedrock", databricks=False)
    tenant = _tenant("framework-disabled")
    _bind(runtime, tenant, "aws_bedrock")
    with pytest.raises(CloudAgentGovernanceDisabled, match="Databricks"):
        runtime.authority.register_framework_pack(
            tenant,
            pack,
            expected_catalog_revision=0,
            idempotency_key="register-disabled-pack",
        )


def test_databricks_review_attestation_activation_chain_is_separated_and_non_certifying() -> None:
    parser = DatabricksFrameworkContractAdapter()
    pack = parser.normalize_mapping(
        {
            "schema": "lightwork.databricks-ai-security-framework-map.v1",
            "framework_id": "databricks_ai_security_framework",
            "version": "2026.1",
            "source_revision_sha256": _digest("databricks-source"),
            "mappings": [
                {
                    "control_id": "DBAI-1",
                    "lightwork_control_ids": ["access.control"],
                    "applicability_tags": ["agent"],
                }
            ],
        }
    )
    runtime, _clock = _runtime("aws_bedrock", databricks=True)
    tenant = _tenant("framework-enabled")
    _bind(runtime, tenant, "aws_bedrock")
    assert (
        runtime.authority.register_framework_pack(
            tenant,
            pack,
            expected_catalog_revision=0,
            idempotency_key="register-framework-pack",
        )
        == 1
    )
    link = FrameworkEvidenceLink(
        _ref("evd", "framework-evidence"),
        _digest("framework-evidence"),
        "supports.control",
    )
    reviewer = _ref("prn", "reviewer")
    review = runtime.authority.review_framework_pack(
        tenant,
        pack_version=pack.version,
        reviewer_ref=reviewer,
        decision="approved",
        applicability=(
            FrameworkApplicability(
                "DBAI-1",
                _ref("wrk", "workload"),
                "applicable",
                "evidence.reviewed",
                (link,),
            ),
        ),
        idempotency_key="review-framework-pack",
    )
    with pytest.raises(CloudAgentBindingError, match="distinct"):
        runtime.authority.attest_framework_review(
            tenant,
            review_ref=review.review_ref,
            expected_review_revision=review.revision,
            attestor_ref=reviewer,
            evidence_links=(link,),
            idempotency_key="same-person-attestation",
        )
    attestation = runtime.authority.attest_framework_review(
        tenant,
        review_ref=review.review_ref,
        expected_review_revision=review.revision,
        attestor_ref=_ref("prn", "independent-attestor"),
        evidence_links=(link,),
        idempotency_key="independent-attestation",
    )
    activation = runtime.authority.activate_framework_pack(
        tenant,
        pack_version=pack.version,
        review_ref=review.review_ref,
        attestation_ref=attestation.attestation_ref,
        expected_activation_revision=0,
        idempotency_key="activate-framework-pack",
    )
    assert activation.version == pack.version
    assert activation.certification_claim is False
    assert activation.limit_notice == FRAMEWORK_LIMIT_NOTICE
    with pytest.raises(CloudAgentIntegrityError, match="certification"):
        dataclasses.replace(attestation, certification_claim=True)
    with pytest.raises(CloudAgentIntegrityError, match="limit notice"):
        dataclasses.replace(activation, limit_notice="certified")
    assert isinstance(attestation, FrameworkAttestation)
    assert isinstance(activation, FrameworkActivation)


def test_claim_fields_cannot_be_smuggled_through_public_receipts() -> None:
    runtime, tenant, _evaluator, _transport, _clock = _bound_enforcement_runtime()
    intent = _prepared_intent(runtime, tenant)
    fake_result = ProviderAdapterResult.build(
        tenant_sha256=tenant.tenant_sha256,
        provider="azure_ai_foundry",
        binding_ref=intent.binding_ref,
        binding_revision=intent.binding_revision,
        intent_ref=intent.intent_ref,
        effect_sha256=_digest("effect"),
        request_sha256=_digest("request"),
        disposition="rejected",
        receipt_sha256=_digest("receipt"),
        observed_at=1_800_000_000.0,
        signature="a" * 64,
    )
    with pytest.raises(CloudAgentIntegrityError, match="verified provider write"):
        EnforcementDispatchReceipt(
            intent,
            fake_result,
            provider_write_verified=True,  # type: ignore[arg-type]
        )


def test_slow_policy_evaluator_does_not_hold_global_tenant_lock() -> None:
    evaluator = _BlockingEvaluator()
    runtime, _clock = _runtime("azure_ai_foundry", evaluator=evaluator)
    tenant_a = _tenant("slow-a")
    tenant_b = _tenant("slow-b")
    _bind(runtime, tenant_a, "azure_ai_foundry")
    _bind(runtime, tenant_b, "azure_ai_foundry")
    _sync(runtime, tenant_a, _asset(native_id="agent-a"), key="sync-slow-a")
    _sync(runtime, tenant_b, _asset(native_id="agent-b"), key="sync-slow-b")
    record = _inventory_record(runtime, tenant_a)
    errors: list[BaseException] = []

    def evaluate() -> None:
        try:
            runtime.authority.evaluate_policy(
                tenant_a,
                agent_ref=record.agent.agent_ref,
                expected_agent_revision=record.record_revision,
                policy_ref=_ref("pol", "slow-policy"),
                policy_revision=1,
                policy_sha256=_digest("slow-policy"),
                requested_action="disable_agent",
                target_ref=record.agent.agent_ref,
                idempotency_key="slow-evaluation",
            )
        except BaseException as exc:  # pragma: no cover - assertion reports it
            errors.append(exc)

    worker = threading.Thread(target=evaluate)
    worker.start()
    assert evaluator.entered.wait(2)
    assert runtime.authority.list_inventory(tenant_b, provider="azure_ai_foundry").total == 1
    evaluator.release.set()
    worker.join(2)
    assert not worker.is_alive()
    assert errors == []


def test_cross_tenant_authority_and_idempotency_collision_fail_closed() -> None:
    runtime, _clock = _runtime("azure_ai_foundry")
    tenant_a = _tenant("isolation-a")
    tenant_b = _tenant("isolation-b")
    _bind(runtime, tenant_a, "azure_ai_foundry")
    with pytest.raises(CloudAgentBindingError):
        runtime.authority.list_inventory(tenant_b, provider="azure_ai_foundry")
    _sync(runtime, tenant_a, _asset())
    with pytest.raises(CloudAgentIdempotencyConflict):
        runtime.authority.bind_provider(
            tenant_a,
            provider="azure_ai_foundry",
            binding_ref=_ref("cab", "different-binding"),
            adapter_identity_sha256=_digest("different-adapter"),
            adapter=_NoopAdapter(),
            verifier=lambda _binding, _result: False,
            expected_revision=1,
            idempotency_key=f"bind-azure_ai_foundry-{tenant_a.tenant_sha256}",
        )
