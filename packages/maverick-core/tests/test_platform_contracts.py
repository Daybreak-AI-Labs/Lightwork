from __future__ import annotations

import copy

import pytest
from maverick.platform_contracts import (
    CONTRACT_SCHEMAS,
    PlatformContractError,
    build_contract,
    export_contract,
    read_contract,
    transition_work_item,
)

NOW = 1_800_000_000.0
TENANT = "customer-a"


def _common(kind: str, fields: dict) -> dict:
    return build_contract(
        kind,
        contract_id=f"{kind}-1",
        tenant_id=TENANT,
        owner_id="procurement",
        purpose="Evaluate one bounded workflow",
        created_at=NOW,
        updated_at=NOW,
        revision=4,
        fields=fields,
        provenance={"source": "synthetic-test", "version": "1"},
        policy_snapshot_ref={"id": "policy", "version": 1, "sha256": "a" * 64},
        authority_versions={"policy": "7"},
        credential_versions={"jira": "3"},
        idempotency={"state": "bound", "key": "operation-1"},
        budget={"state": "bounded", "currency": "USD", "limit": "1.25", "spent": "0.25"},
        cost={"state": "exact", "currency": "USD", "amount": "0.25"},
        fresh_until=NOW + 3600,
        human_intervention={"required": True, "count": 1},
        uncertainty={"state": "bounded", "basis": "authoritative-readback"},
        causal_status={"state": "unknown", "basis": "not_estimated"},
    )


@pytest.fixture(params=[
    ("work-item", {
        "requester": "reviewer@example.test",
        "process": {"id": "journey", "version": 1, "sha256": "b" * 64},
        "supported_deployment": {"topology": "single_replica"},
        "acceptance_contract": {"technical": "verified", "business": "human"},
        "out_of_scope": ["payment"],
        "lifecycle": "Awaiting approval",
        "next_action": "review",
        "technical_effect": "not_verified",
        "business_outcome": "pending",
        "target_scope": {"provider": "jira", "project": "VEND"},
        "deployment_profile": {"topology": "single_replica"},
        "admission_id": "work-item-1",
        "acceptance_contract_ref": {"id": "acceptance-1"},
        "parent_work_item_id": None,
    }),
    ("action-intent", {
        "work_item_id": "work-item-1",
        "action": "jira.create",
        "parameters_sha256": "c" * 64,
        "preconditions": {"source_version": "4"},
        "authority_ref": {"actor": "reviewer@example.test"},
        "approval_ref": {"id": "approval-1"},
        "dispatch_state": "approved",
        "reconciliation": {"mode": "read_before_retry"},
        "target": {"provider": "jira", "account": "customer-a", "resource": "VEND"},
        "source_versions": {"document": "4"},
        "declared_effects": ["create_one_task"],
        "irreversible_effects": [],
        "credential_ref": {"kind": "managed_connection", "id": "jira"},
        "credential_version": "3",
        "expires_at": "2027-01-15T09:00:00.000Z",
        "budget_reservation": {"currency": "USD", "amount": "1.00"},
        "idempotency_scope": {"tenant": "customer-a", "operation": "operation-1"},
    }),
    ("observation", {
        "subject_ref": {"id": "ticket-1"},
        "source_version": "5",
        "observed_at": "2027-01-15T08:00:00.000Z",
        "collection_method": "authoritative_readback",
        "facts": [{"name": "status", "value": "created"}],
        "artifact_refs": [{"sha256": "d" * 64}],
        "intent_id": "action-intent-1",
        "source_locator": {"provider": "jira", "key": "VEND-1"},
        "collected_at": "2027-01-15T08:00:01.000Z",
        "provider_request_id": "request-1",
        "provider_receipt_id": "receipt-1",
        "raw_artifact_ref": {"sha256": "d" * 64},
        "asserted_fact": {"status": "created"},
        "collector_ref": {"kind": "jira_readback"},
    }),
    ("outcome-record", {
        "work_item_id": "work-item-1",
        "verdict": "accepted",
        "evaluator_ref": {"kind": "authorized_human"},
        "evidence_refs": [{"sha256": "e" * 64}],
        "unintended_changes": [],
        "human_interventions": 1,
        "cost_refs": ["cost-1"],
        "reason_codes": ["customer_accepted"],
        "acceptance_contract_ref": {"id": "acceptance-1", "version": 1},
        "actor_ref": {"id": "reviewer@example.test"},
        "supersedes_ref": None,
    }),
    ("experience-record", {
        "origin_refs": ["outcome-1"],
        "label_kind": "human_outcome",
        "rights_basis": "customer_owned",
        "training_eligible": False,
        "retention_deadline": "2028-01-15T08:00:00.000Z",
        "correction_refs": [],
        "derivative_refs": [],
        "source_versions": {"outcome-1": "1"},
        "sensitivity": {"classification": "customer_confidential"},
        "training_expires_at": {"state": "not_applicable"},
        "deletion_state": {"state": "retained"},
    }),
    ("experiment-spec", {
        "hypothesis": "The candidate reduces correction rate",
        "baseline_refs": ["baseline-1"],
        "split_refs": ["split-1"],
        "seeds": [7, 11],
        "primary_endpoint": "correction_rate",
        "stopping_rule": {"max_samples": 1000},
        "analysis_plan": {"method": "fixed_holdout"},
        "permitted_queries": ["aggregate_metrics"],
        "question": "Does the candidate reduce correction rate?",
        "environment_ref": {"sha256": "1" * 64},
        "dataset_refs": [{"sha256": "2" * 64}],
        "data_rights": {"basis": "owned"},
        "family_split_ref": {"sha256": "3" * 64},
        "component_versions": {"evaluator": "1"},
        "secondary_endpoints": ["latency"],
        "replication_plan": {"runs": 2},
        "protocol_registration": {"sha256": "4" * 64, "registered_at": "2027-01-15T08:00:00.000Z"},
    }),
    ("candidate-manifest", {
        "artifact_refs": [{"sha256": "f" * 64}],
        "parent_refs": ["candidate-0"],
        "data_rights": {"basis": "owned"},
        "dependency_refs": ["runtime-1"],
        "evaluation_refs": ["evaluation-1"],
        "applicability": {"workflow": "vendor_onboarding"},
        "permissions": ["sandbox_only"],
        "compatibility": {"python": "3.12"},
        "restore_target": "candidate-0",
        "reviewer_decision": {"state": "pending"},
        "resource_use": {"state": "unknown"},
        "rollout_plan": {"state": "sandbox_only"},
        "supported_domain": {"workflow": "vendor_onboarding"},
    }),
], ids=lambda value: value[0])
def contract(request):
    kind, fields = request.param
    return kind, _common(kind, fields)


def test_every_platform_contract_has_one_versioned_envelope_and_round_trips(contract):
    kind, record = contract
    assert record["schema"] == CONTRACT_SCHEMAS[kind]
    assert record["schema_version"] == 1
    assert record["tenant_id"] == TENANT
    assert record["revision"] == 4
    assert record["created_at"].endswith("Z")
    assert read_contract(record, expected_kind=kind, expected_tenant_id=TENANT) == record


def test_legacy_v1_optional_fields_are_defaulted_without_changing_identity_or_meaning():
    current = _common("observation", {
        "subject_ref": {"id": "ticket-1"},
        "source_version": "5",
        "observed_at": "2027-01-15T08:00:00.000Z",
        "collection_method": "authoritative_readback",
        "facts": [{"name": "status", "value": "created"}],
        "artifact_refs": [],
        "intent_id": "action-intent-1",
        "source_locator": {"provider": "jira", "key": "VEND-1"},
        "collected_at": "2027-01-15T08:00:01.000Z",
        "provider_request_id": "request-1", "provider_receipt_id": "receipt-1",
        "raw_artifact_ref": {"sha256": "d" * 64},
        "asserted_fact": {"status": "created"},
        "collector_ref": {"kind": "jira_readback"},
    })
    legacy = copy.deepcopy(current)
    for optional in ("schema_version", "updated_at", "authority_versions", "credential_versions"):
        legacy.pop(optional)

    restored = read_contract(legacy, expected_kind="observation")
    assert restored["id"] == current["id"]
    assert restored["facts"] == current["facts"]
    assert restored["schema_version"] == 1
    assert restored["updated_at"] == current["created_at"]
    assert restored["authority_versions"] == {}
    assert restored["credential_versions"] == {}
    assert read_contract(restored) == restored


@pytest.mark.parametrize(("mutate", "code"), [
    (lambda value: value["budget"].update(limit=float("nan")), "invalid_budget"),
    (lambda value: value.update(schema="lightwork.contract.work-item.v999"), "unsupported_contract_schema"),
])
def test_malformed_budget_and_unknown_schema_fail_with_stable_codes(mutate, code):
    record = _common("work-item", {
        "requester": "requester",
        "process": {"id": "journey", "version": 1, "sha256": "b" * 64},
        "supported_deployment": {},
        "acceptance_contract": {},
        "out_of_scope": [],
        "lifecycle": "Draft",
        "next_action": "collect",
        "technical_effect": "not_verified",
        "business_outcome": "pending",
        "target_scope": {}, "deployment_profile": {}, "admission_id": "work-item-1",
        "acceptance_contract_ref": {}, "parent_work_item_id": None,
    })
    mutate(record)
    with pytest.raises(PlatformContractError) as raised:
        read_contract(record)
    assert raised.value.code == code


def test_tenant_mismatch_and_invalid_transition_fail_before_mutation():
    record = _common("work-item", {
        "requester": "requester",
        "process": {"id": "journey", "version": 1, "sha256": "b" * 64},
        "supported_deployment": {},
        "acceptance_contract": {},
        "out_of_scope": [],
        "lifecycle": "Draft",
        "next_action": "collect",
        "technical_effect": "not_verified",
        "business_outcome": "pending",
        "target_scope": {}, "deployment_profile": {}, "admission_id": "work-item-1",
        "acceptance_contract_ref": {}, "parent_work_item_id": None,
    })
    original = copy.deepcopy(record)
    with pytest.raises(PlatformContractError) as mismatch:
        read_contract(record, expected_tenant_id="customer-b")
    assert mismatch.value.code == "tenant_mismatch"
    with pytest.raises(PlatformContractError) as invalid:
        transition_work_item(record, "Accepted", expected_revision=4, updated_at=NOW + 1)
    assert invalid.value.code == "invalid_transition"
    assert record == original


def test_transition_requires_the_reviewed_revision_and_preserves_business_distinction():
    record = _common("work-item", {
        "requester": "requester",
        "process": {"id": "journey", "version": 1, "sha256": "b" * 64},
        "supported_deployment": {},
        "acceptance_contract": {},
        "out_of_scope": [],
        "lifecycle": "Running",
        "next_action": "wait",
        "technical_effect": "not_verified",
        "business_outcome": "pending",
        "target_scope": {}, "deployment_profile": {}, "admission_id": "work-item-1",
        "acceptance_contract_ref": {}, "parent_work_item_id": None,
    })
    observing = transition_work_item(
        record,
        "Awaiting observation",
        expected_revision=4,
        updated_at=NOW + 1,
        updates={"next_action": "observe"},
    )
    verified = transition_work_item(
        observing,
        "Verified",
        expected_revision=5,
        updated_at=NOW + 2,
        updates={"technical_effect": "verified", "next_action": "accept_or_reject"},
    )
    assert verified["revision"] == 6
    assert verified["technical_effect"] == "verified"
    assert verified["business_outcome"] == "pending"
    with pytest.raises(PlatformContractError) as conflict:
        transition_work_item(record, "Verified", expected_revision=3, updated_at=NOW + 1)
    assert conflict.value.code == "revision_conflict"


def test_export_redacts_secret_values_but_preserves_managed_references():
    record = _common("observation", {
        "subject_ref": {"id": "ticket-1"},
        "source_version": "5",
        "observed_at": "2027-01-15T08:00:00.000Z",
        "collection_method": "authoritative_readback",
        "facts": [],
        "artifact_refs": [],
        "intent_id": "action-intent-1",
        "source_locator": {"provider": "jira", "key": "VEND-1"},
        "collected_at": "2027-01-15T08:00:01.000Z",
        "provider_request_id": "request-1", "provider_receipt_id": "receipt-1",
        "raw_artifact_ref": {"sha256": "d" * 64},
        "asserted_fact": {"status": "created"},
        "collector_ref": {"kind": "jira_readback"},
        "diagnostic": {
            "api_key": "plaintext-secret-value",
            "credential_ref": "vault://customer-a/jira",
            "nested": {"password": "do-not-export"},
        },
    })
    exported = export_contract(record)
    rendered = str(exported)
    assert "plaintext-secret-value" not in rendered
    assert "do-not-export" not in rendered
    assert exported["diagnostic"]["api_key"] == "[redacted]"
    assert exported["diagnostic"]["credential_ref"] == "vault://customer-a/jira"
