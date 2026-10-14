#!/usr/bin/env python3
"""Verify source-derived QA, supply-chain, and assurance process mappings.

This contract proves repository wiring only.  It deliberately keeps hosted-run
results, penetration work, certification, staffed response, and live exercises
outside what source files can establish.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

try:
    import yaml
except ImportError:  # pragma: no cover - enforced CI installs the exact lock
    yaml = None


if yaml is not None:
    class _StrictWorkflowLoader(yaml.SafeLoader):
        """SafeLoader that refuses duplicate and merged mapping semantics."""


    _StrictWorkflowLoader.yaml_implicit_resolvers = {
        key: [
            (tag, pattern)
            for tag, pattern in resolvers
            if tag != "tag:yaml.org,2002:bool"
        ]
        for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    _StrictWorkflowLoader.add_implicit_resolver(
        "tag:yaml.org,2002:bool",
        re.compile(r"^(?:true|false)$", re.IGNORECASE),
        list("tTfF"),
    )


    def _construct_strict_mapping(
        loader: _StrictWorkflowLoader,
        node: Any,
        deep: bool = False,
    ) -> dict[object, object]:
        if not isinstance(node, yaml.MappingNode):
            raise yaml.constructor.ConstructorError(
                None,
                None,
                "expected a workflow mapping",
                node.start_mark,
            )
        result: dict[object, object] = {}
        for key_node, value_node in node.value:
            if (
                key_node.tag == "tag:yaml.org,2002:merge"
                or key_node.value == "<<"
            ):
                raise yaml.constructor.ConstructorError(
                    "while constructing a workflow mapping",
                    node.start_mark,
                    "YAML merge keys are not admitted",
                    key_node.start_mark,
                )
            key = loader.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise yaml.constructor.ConstructorError(
                    "while constructing a workflow mapping",
                    node.start_mark,
                    "workflow mapping keys must use exact YAML 1.2 strings",
                    key_node.start_mark,
                )
            try:
                duplicate = key in result
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a workflow mapping",
                    node.start_mark,
                    "workflow mapping key is not hashable",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a workflow mapping",
                    node.start_mark,
                    f"duplicate workflow mapping key: {key!r}",
                    key_node.start_mark,
                )
            result[key] = loader.construct_object(value_node, deep=deep)
        return result


    _StrictWorkflowLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        _construct_strict_mapping,
    )
else:  # pragma: no cover - _workflow raises the enforced dependency error
    _StrictWorkflowLoader = None

SCHEMA = "lightwork.production-qa-assurance.v1"
REPOSITORY = "Daybreak-AI-Labs/Lightwork"
CONTRACT_PATH = Path("release/production-qa-assurance.json")
SURFACE_PATH = Path("release/production-surface-manifest.json")
CI_WORKFLOW = ".github/workflows/ci.yml"
CI_TEST_JOB = "test"
CI_TEST_STEP = "Test (bounded, with diagnostics)"
GATE_STATUSES = {
    "QA-05": "repository-complete-critical-durability-crash-injection-inventory",
    "QA-07": "repository-complete-admitted-parser-protocol-fuzz-inventory",
    "QA-08": "repository-supply-chain-gates-enforced-hosted-run-external",
    "QA-10": "draft-control-mappings-only-external-assessment-required",
    "QA-11": "repository-process-and-controls-only-live-staffing-and-drills-external",
    "QA-16": "repository-complete-admitted-state-machine-property-inventory",
}
EXTERNAL_LIMITATIONS = (
    "hosted CI results are not verified by this contract",
    "live penetration testing and production-like security exercises remain external",
    "compliance mappings are drafts and do not claim certification",
    "staffed response rotations notifications and completed drills remain external",
    "customer deployment responsibilities remain deployment-specific",
)

FUZZ_PROPERTY_MAPPINGS = (
    {
        "id": "production-input-parsers",
        "assurance_type": "deterministic-fuzz-and-round-trip-property",
        "scope": "admitted-production-http-replay-and-manifest-inputs",
        "sources": [
            {
                "path": "packages/maverick-dashboard/maverick_dashboard/stream_cursor.py",
                "symbols": ["numeric_cursor", "snapshot_cursor", "snapshot_id"],
            },
            {
                "path": "packages/maverick-core/maverick/api_idempotency.py",
                "symbols": [
                    "decode_goal_result",
                    "encode_goal_result",
                    "request_sha256",
                    "validate_request_sha256",
                ],
            },
            {
                "path": "packages/maverick-core/maverick/production_scope.py",
                "symbols": ["load_manifest"],
            },
        ],
        "tests": [
            {
                "path": "packages/maverick-dashboard/tests/test_production_boundary_fuzz.py",
                "symbols": [
                    "test_numeric_cursor_has_one_exact_monotonic_decimal_grammar",
                    "test_snapshot_cursor_is_canonical_and_content_addressed",
                    "test_production_manifest_parser_fuzz_fails_only_with_typed_error",
                    "test_idempotency_json_round_trip_property_is_canonical",
                    "test_idempotency_decoder_rejects_ambiguous_or_unbounded_records",
                    "test_request_digest_never_coerces_an_object_identity",
                ],
            }
        ],
        "ci": {"workflow": CI_WORKFLOW, "job": CI_TEST_JOB, "step": CI_TEST_STEP},
        "execution_status": "unconditional-repository-suite",
    },
    {
        "id": "capability-authority",
        "assurance_type": "deterministic-fuzz-and-monotonicity-property",
        "scope": "admitted-capability-and-delegation-authority",
        "sources": [
            {
                "path": "packages/maverick-core/maverick/capability.py",
                "symbols": ["Capability"],
            },
            {
                "path": "packages/maverick-core/maverick/capability_fuzzer.py",
                "symbols": ["fuzz", "fuzz_roundtrip", "generate_probes"],
            },
        ],
        "tests": [
            {
                "path": "packages/maverick-core/tests/test_capability_fuzzer.py",
                "symbols": [
                    "test_real_capability_has_zero_leaks",
                    "test_specific_adversarial_probes_denied_by_real_capability",
                    "test_broken_substring_matcher_leaks",
                    "test_roundtrip_catches_permissive_default",
                ],
            },
            {
                "path": "packages/maverick-core/tests/test_capability_attenuation_fuzz.py",
                "symbols": [
                    "test_attenuate_never_escalates",
                    "test_intersect_is_subset_of_both",
                    "test_disjoint_whitelists_deny_all_not_allow_all",
                ],
            },
        ],
        "ci": {"workflow": CI_WORKFLOW, "job": CI_TEST_JOB, "step": CI_TEST_STEP},
        "execution_status": "unconditional-repository-suite",
    },
    {
        "id": "identity-and-signed-input-parsers",
        "assurance_type": "deterministic-hostile-input-fuzz",
        "scope": "admitted-oidc-session-and-signed-input-boundaries",
        "sources": [
            {
                "path": "packages/maverick-core/maverick/oidc.py",
                "symbols": ["verify_oidc_token"],
            },
            {
                "path": "packages/maverick-core/maverick/web_session.py",
                "symbols": ["verify_session"],
            },
            {
                "path": "packages/maverick-core/maverick/webhooks.py",
                "symbols": ["verify_signature"],
            },
        ],
        "tests": [
            {
                "path": "packages/maverick-core/tests/test_parser_fuzz.py",
                "symbols": [
                    "test_fuzz_oidc_verify",
                    "test_fuzz_web_session",
                    "test_fuzz_webhook_signature",
                ],
            }
        ],
        "ci": {"workflow": CI_WORKFLOW, "job": CI_TEST_JOB, "step": CI_TEST_STEP},
        "execution_status": "unconditional-repository-suite-with-oidc-installed",
    },
    {
        "id": "secret-redaction-boundary",
        "assurance_type": "deterministic-fuzz-redos-and-idempotency-property",
        "scope": "admitted-log-audit-and-output-redaction",
        "sources": [
            {
                "path": "packages/maverick-core/maverick/secrets.py",
                "symbols": ["scrub"],
            },
            {
                "path": "packages/maverick-core/maverick/safety/secret_detector.py",
                "symbols": ["redact"],
            },
        ],
        "tests": [
            {
                "path": "packages/maverick-core/tests/test_secrets_scrub_fuzz.py",
                "symbols": [
                    "test_secret_redactors_do_not_redos_on_long_runs",
                    "test_scrub_never_raises_on_random_input",
                    "test_scrub_redacts_known_secrets_embedded_in_noise",
                    "test_scrub_is_idempotent",
                ],
            }
        ],
        "ci": {"workflow": CI_WORKFLOW, "job": CI_TEST_JOB, "step": CI_TEST_STEP},
        "execution_status": "unconditional-repository-suite",
    },
    {
        "id": "production-migration-state-machine",
        "assurance_type": "adverse-transition-and-reconciliation-properties",
        "scope": "admitted-postgres-migration-authority",
        "sources": [
            {
                "path": "packages/maverick-core/maverick/production_migration.py",
                "symbols": [
                    "MigrationAuthorization",
                    "MigrationState",
                    "classify_applied_versions",
                    "run_production_migration",
                ],
            }
        ],
        "tests": [
            {
                "path": "packages/maverick-core/tests/test_production_migration.py",
                "symbols": [
                    "test_applied_version_classifier_only_admits_exact_three_states",
                    "test_runner_revalidates_expiry_after_waiting_for_admission_lock",
                    "test_schema_failure_rolls_back_without_running_rls",
                    "test_commit_before_rls_report_is_reconciled_by_same_authorization",
                    "test_current_schema_refuses_a_different_upgrade_authorization",
                ],
            }
        ],
        "ci": {"workflow": CI_WORKFLOW, "job": CI_TEST_JOB, "step": CI_TEST_STEP},
        "execution_status": "unconditional-pure-repository-suite-live-postgres-external",
    },
    {
        "id": "tenant-quota-state-machine",
        "assurance_type": "concurrency-replay-expiry-and-fencing-properties",
        "scope": "admitted-tenant-quota-authority",
        "sources": [
            {
                "path": "packages/maverick-core/maverick/tenant_quota_authority.py",
                "symbols": [
                    "TenantQuotaPolicy",
                    "TenantQuotaReservation",
                    "reserve",
                    "settle",
                    "release",
                    "reconcile",
                ],
            }
        ],
        "tests": [
            {
                "path": "packages/maverick-core/tests/test_tenant_quota_authority.py",
                "symbols": [
                    "test_atomic_concurrent_reservation_has_one_winner",
                    "test_exact_replay_and_settlement_are_idempotent_without_double_charge",
                    "test_expiry_reclaims_crashed_hold_and_preserves_lifecycle",
                    "test_shared_policy_generation_fences_replica_drift",
                    "test_missing_dimension_and_authority_outage_fail_closed",
                    "test_terminal_job_cannot_be_resurrected_after_quota_release",
                ],
            }
        ],
        "ci": {"workflow": CI_WORKFLOW, "job": CI_TEST_JOB, "step": CI_TEST_STEP},
        "execution_status": "unconditional-pure-repository-suite-live-postgres-external",
    },
    {
        "id": "api-idempotency-state-machine",
        "assurance_type": "replay-conflict-retirement-and-delivery-properties",
        "scope": "admitted-production-http-mutations",
        "sources": [
            {
                "path": "packages/maverick-core/maverick/api_idempotency.py",
                "symbols": [
                    "ApiIdempotencyConflict",
                    "ApiIdempotencyExpired",
                    "ApiIdempotencyIndeterminate",
                    "IdempotentGoalResult",
                ],
            }
        ],
        "tests": [
            {
                "path": "packages/maverick-core/tests/test_api_idempotency.py",
                "symbols": [
                    "test_exact_replay_returns_original_durable_result",
                    "test_same_key_with_different_request_is_rejected",
                    "test_expired_key_is_a_tombstone_not_a_new_operation",
                    "test_goal_and_result_claim_roll_back_together",
                ],
            },
            {
                "path": "packages/maverick-core/tests/test_api_idempotency_retirement.py",
                "symbols": [
                    "test_sqlite_delivery_marker_retires_dispatch_from_every_exact_replay",
                    "test_erased_goal_retires_key_without_plaintext_or_result_retention",
                ],
            },
            {
                "path": "packages/maverick-dashboard/tests/test_idempotency_delivery_recovery.py",
                "symbols": [
                    "test_cancel_resume_then_original_retry_cannot_restore_create_delivery",
                    "test_goal_exact_replay_bypasses_provider_rate_and_mutable_template",
                ],
            },
        ],
        "ci": {"workflow": CI_WORKFLOW, "job": CI_TEST_JOB, "step": CI_TEST_STEP},
        "execution_status": "unconditional-repository-suite",
    },
    {
        "id": "signoff-authority-state-machine",
        "assurance_type": "concurrent-cas-replay-and-policy-drift-properties",
        "scope": "admitted-production-human-signoff",
        "sources": [
            {
                "path": "packages/maverick-core/maverick/signoff_authority.py",
                "symbols": [
                    "IdempotentSignoffResult",
                    "SignoffConflict",
                    "SignoffIndeterminate",
                    "SignoffPolicyBinding",
                ],
            }
        ],
        "tests": [
            {
                "path": "packages/maverick-core/tests/test_signoff_authority.py",
                "symbols": [
                    "test_claim_replay_and_audit_finalization_are_exact_and_ordered",
                    "test_concurrent_opposite_verdicts_admit_exactly_one",
                    "test_stale_generation_is_audited_but_never_becomes_effective",
                    "test_failed_generation_cas_rolls_back_claim_and_outbox",
                    "test_policy_drift_between_audit_append_and_finalization_fails_closed",
                    "test_tampered_replay_result_is_indeterminate",
                ],
            }
        ],
        "ci": {"workflow": CI_WORKFLOW, "job": CI_TEST_JOB, "step": CI_TEST_STEP},
        "execution_status": "unconditional-repository-suite",
    },
    {
        "id": "durable-job-state-machine",
        "assurance_type": "concurrent-dedup-terminal-and-fencing-properties",
        "scope": "admitted-durable-job-authority",
        "sources": [
            {
                "path": "packages/maverick-core/maverick/job_queue.py",
                "symbols": ["Job", "JobAuthority", "JobQueue"],
            }
        ],
        "tests": [
            {
                "path": "packages/maverick-core/tests/test_job_queue_dedup.py",
                "symbols": [
                    "test_exact_immediate_replay_returns_the_same_live_row",
                    "test_live_key_reuse_with_request_mismatch_fails_closed",
                    "test_terminal_row_releases_key_for_explicit_retry",
                    "test_concurrent_processes_return_the_exact_winning_live_row",
                    "test_production_invalid_dedup_fails_before_filesystem_or_schema",
                ],
            },
            {
                "path": "packages/maverick-core/tests/test_control_plane_lease.py",
                "symbols": [
                    "test_a_second_process_is_refused",
                    "test_the_fencing_token_advances_across_restarts",
                    "test_acquire_refuses_to_reset_a_corrupt_fencing_record",
                ],
            },
        ],
        "ci": {"workflow": CI_WORKFLOW, "job": CI_TEST_JOB, "step": CI_TEST_STEP},
        "execution_status": "unconditional-repository-suite",
    },
    {
        "id": "critical-durability-and-crash-recovery",
        "assurance_type": "deterministic-crash-window-restart-and-reconciliation-properties",
        "scope": (
            "admitted-checkpoint-child-job-flow-and-idempotency-"
            "persistence-boundaries"
        ),
        "sources": [
            {
                "path": "packages/maverick-core/maverick/crash_only_log.py",
                "symbols": ["CrashOnlyLog", "replay"],
            },
            {
                "path": "packages/maverick-core/maverick/checkpoint.py",
                "symbols": ["CheckpointDurabilityError", "Checkpointer"],
            },
            {
                "path": "packages/maverick-core/maverick/child_checkpoint.py",
                "symbols": ["ChildCheckpointStore", "DurableChildRuntime"],
            },
            {
                "path": "packages/maverick-core/maverick/job_queue.py",
                "symbols": ["JobAuthority", "JobQueue"],
            },
            {
                "path": "packages/maverick-core/maverick/flow/postgres_authority.py",
                "symbols": ["PostgresFlowAuthority"],
            },
            {
                "path": (
                    "packages/maverick-dashboard/maverick_dashboard/"
                    "automation_queue.py"
                ),
                "symbols": [
                    "enqueue_flow_resume",
                    "reconcile_goal_jobs",
                    "_handle_flow_run",
                ],
            },
            {
                "path": "packages/maverick-core/maverick/runner.py",
                "symbols": [
                    "begin_draining",
                    "drain_inflight",
                    "resume_admissions",
                    "work_admission",
                ],
            },
            {
                "path": "packages/maverick-core/maverick/worker.py",
                "symbols": ["Worker"],
            },
            {
                "path": (
                    "packages/maverick-dashboard/maverick_dashboard/"
                    "task_dispatch.py"
                ),
                "symbols": [
                    "dispatch_goal",
                    "dispatch_idempotent_goal",
                    "repair_idempotent_goal_dispatch",
                ],
            },
            {
                "path": (
                    "packages/maverick-dashboard/maverick_dashboard/"
                    "health_routes.py"
                ),
                "symbols": ["readiness_response"],
            },
        ],
        "tests": [
            {
                "path": "packages/maverick-core/tests/test_crash_only_log.py",
                "symbols": [
                    "test_reopen_seals_torn_tail_and_resumes_from_last_intact",
                    "test_constructor_closes_append_fd_when_recovery_raises",
                ],
            },
            {
                "path": "packages/maverick-core/tests/test_durable_checkpoint.py",
                "symbols": [
                    "test_required_resume_without_checkpoint_is_durability_failed",
                    "test_required_checkpoint_rejects_corrupt_record",
                    "test_required_checkpoint_save_refusal_propagates",
                    "test_resume_from_checkpoint_restores_state",
                ],
            },
            {
                "path": "packages/maverick-core/tests/test_durable_child_checkpoint.py",
                "symbols": [
                    "test_durable_reservation_survives_fresh_context",
                    "test_spawned_process_crash_recovery_is_resume_or_indeterminate",
                ],
            },
            {
                "path": "packages/maverick-core/tests/test_job_reclaim.py",
                "symbols": [
                    "test_reclaim_stale_requeues_orphaned_running",
                    "test_complete_does_not_clobber_self_reclaimed_job",
                    "test_worker_reclaims_peer_orphan_while_running",
                ],
            },
            {
                "path": (
                    "packages/maverick-dashboard/tests/"
                    "test_flow_operation_idempotency.py"
                ),
                "symbols": [
                    "test_resume_claim_repairs_crash_before_queue_and_survives_queue_reopen",
                    "test_resume_ambiguous_post_commit_error_reuses_and_binds_committed_job",
                ],
            },
            {
                "path": (
                    "packages/maverick-dashboard/tests/"
                    "test_production_task_lifecycle.py"
                ),
                "symbols": [
                    "test_pending_delivery_runs_after_worker_recreation",
                    "test_worker_loss_is_quarantined_without_replay",
                    "test_startup_distinguishes_queued_backlog_from_dispatch_crash",
                    "test_startup_repairs_transactional_goal_outbox_once",
                ],
            },
            {
                "path": (
                    "packages/maverick-core/tests/"
                    "test_flow_postgres_authority.py"
                ),
                "symbols": [
                    "test_session_lock_releases_connection_after_executor_crash"
                ],
            },
            {
                "path": "packages/maverick-core/tests/test_runner_drain.py",
                "symbols": [
                    "test_drain_atomically_refuses_new_dispatch_and_waits_for_accepted_work",
                    "test_admitted_work_can_finish_nested_runner_step_after_drain_begins",
                    "test_copied_stale_async_context_cannot_bypass_closed_admission",
                    "test_copied_live_async_context_keeps_outer_admission_counted",
                    "test_drain_waits_through_runner_resource_and_durable_settlement_cleanup",
                    "test_worker_shutdown_settles_claim_then_restart_resumes_without_duplicate",
                ],
            },
            {
                "path": (
                    "packages/maverick-dashboard/tests/"
                    "test_coordinated_shutdown.py"
                ),
                "symbols": [
                    "test_draining_withdraws_readiness_immediately",
                    "test_dashboard_dispatch_refuses_before_durable_enqueue_during_drain",
                    "test_idempotent_outbox_is_left_retryable_when_shutdown_wins_admission",
                ],
            },
        ],
        "ci": {"workflow": CI_WORKFLOW, "job": CI_TEST_JOB, "step": CI_TEST_STEP},
        "execution_status": "unconditional-repository-suite",
    },
    {
        "id": "governed-flow-document-parser",
        "assurance_type": "bounded-hostile-input-fuzz-and-round-trip-property",
        "scope": "admitted-governed-flow-ir-and-graph-documents",
        "sources": [
            {
                "path": "packages/maverick-core/maverick/flow/ir.py",
                "symbols": [
                    "Flow",
                    "FlowDefinitionError",
                    "FlowNode",
                    "_validate_flow_document",
                ],
            }
        ],
        "tests": [
            {
                "path": (
                    "packages/maverick-core/tests/"
                    "test_production_parser_state_properties.py"
                ),
                "symbols": [
                    "test_governed_flow_document_fuzz_fails_only_with_typed_error",
                    "test_governed_flow_round_trip_preserves_graph_and_validation",
                ],
            }
        ],
        "ci": {"workflow": CI_WORKFLOW, "job": CI_TEST_JOB, "step": CI_TEST_STEP},
        "execution_status": "unconditional-repository-suite",
    },
    {
        "id": "vllm-provider-tool-json",
        "assurance_type": "bounded-strict-json-and-authority-shape-property",
        "scope": "admitted-vllm-openai-compatible-tool-call-protocol",
        "sources": [
            {
                "path": (
                    "packages/maverick-core/maverick/providers/"
                    "openai_provider.py"
                ),
                "symbols": [
                    "OpenAIClient",
                    "ProviderResponseError",
                    "parse_tool_arguments",
                ],
            },
            {
                "path": "packages/maverick-core/maverick/providers/vllm_provider.py",
                "symbols": ["VLLMClient"],
            },
        ],
        "tests": [
            {
                "path": (
                    "packages/maverick-core/tests/"
                    "test_production_parser_state_properties.py"
                ),
                "symbols": [
                    "test_vllm_tool_argument_json_is_bounded_object_only",
                    "test_vllm_provider_response_rejects_malformed_tool_authority",
                ],
            },
            {
                "path": "packages/maverick-core/tests/test_provider_usage_required.py",
                "symbols": [
                    "test_unparseable_tool_args_warns_and_fails_closed",
                    "test_unparseable_tool_args_warning_never_logs_raw_arguments",
                ],
            },
        ],
        "ci": {"workflow": CI_WORKFLOW, "job": CI_TEST_JOB, "step": CI_TEST_STEP},
        "execution_status": "unconditional-repository-suite",
    },
    {
        "id": "tenant-and-workspace-path-normalization",
        "assurance_type": "deterministic-canonicalization-and-confinement-property",
        "scope": "admitted-principal-tenancy-and-read-only-workspace-tools",
        "sources": [
            {
                "path": "packages/maverick-core/maverick/paths.py",
                "symbols": [
                    "_tenant_segment",
                    "canonical_tenant_id",
                    "tenant_id_for_exact_subject",
                ],
            },
            {
                "path": "packages/maverick-core/maverick/agent.py",
                "symbols": ["_workspace_relative_path"],
            },
        ],
        "tests": [
            {
                "path": (
                    "packages/maverick-core/tests/"
                    "test_production_parser_state_properties.py"
                ),
                "symbols": [
                    "test_tenant_canonicalization_is_idempotent_and_path_injective",
                    "test_workspace_path_normalization_confines_every_admitted_spelling",
                ],
            }
        ],
        "ci": {"workflow": CI_WORKFLOW, "job": CI_TEST_JOB, "step": CI_TEST_STEP},
        "execution_status": "unconditional-repository-suite",
    },
    {
        "id": "operator-cli-parser",
        "assurance_type": "default-deny-command-inventory-and-parser-fuzz",
        "scope": "admitted-automation-safe-operator-cli",
        "sources": [
            {
                "path": "packages/maverick-core/maverick/operator_contract.py",
                "symbols": [
                    "OperatorCommand",
                    "_resolve_command",
                    "build_operator_contract",
                    "validate_operator_commands",
                ],
            },
            {
                "path": "packages/maverick-core/maverick/cli/__init__.py",
                "symbols": ["main"],
            },
        ],
        "tests": [
            {
                "path": (
                    "packages/maverick-core/tests/"
                    "test_production_parser_state_properties.py"
                ),
                "symbols": [
                    "test_operator_cli_parser_fuzz_has_only_click_usage_failures"
                ],
            },
            {
                "path": "packages/maverick-core/tests/test_operator_contract.py",
                "symbols": [
                    "test_contract_is_deterministic_canonical_and_manifest_bound",
                    "test_validator_rejects_duplicate_and_malformed_entries",
                    "test_validator_rejects_removed_click_option",
                    "test_production_help_exposes_contract_and_refuses_unadmitted_command",
                ],
            },
        ],
        "ci": {"workflow": CI_WORKFLOW, "job": CI_TEST_JOB, "step": CI_TEST_STEP},
        "execution_status": "unconditional-repository-suite",
    },
    {
        "id": "erasure-and-retention-state-machine",
        "assurance_type": "fuzz-retention-tamper-and-no-false-clean-properties",
        "scope": "admitted-privacy-erasure-receipt-and-retirement-authority",
        "sources": [
            {
                "path": "packages/maverick-core/maverick/erasure_receipts.py",
                "symbols": [
                    "build_manifest",
                    "retire_expired_receipts",
                    "validate_erasure_closure",
                    "validate_manifest",
                    "verify_signed_receipt",
                ],
            }
        ],
        "tests": [
            {
                "path": (
                    "packages/maverick-core/tests/"
                    "test_production_parser_state_properties.py"
                ),
                "symbols": [
                    "test_erasure_manifest_fuzz_is_typed_and_retention_is_invariant"
                ],
            },
            {
                "path": "packages/maverick-core/tests/test_erasure_receipts.py",
                "symbols": [
                    "test_plan_change_aborts_before_any_mutation",
                    "test_episode_added_after_receipt_aborts_before_any_mutation",
                    "test_unsigned_same_process_manifest_counts_but_never_certifies",
                    "test_signed_scope_receipt_without_auxiliary_closure_never_certifies",
                    "test_auxiliary_closure_is_subject_free_idempotent_and_tamper_evident",
                    "test_storage_immutability_retention_and_signature_tamper_detection",
                ],
            },
        ],
        "ci": {"workflow": CI_WORKFLOW, "job": CI_TEST_JOB, "step": CI_TEST_STEP},
        "execution_status": "unconditional-repository-suite",
    },
    {
        "id": "governed-flow-transition-state-machine",
        "assurance_type": "fuzz-hash-chain-crash-and-replay-properties",
        "scope": "admitted-governed-flow-run-transition-authority",
        "sources": [
            {
                "path": "packages/maverick-core/maverick/flow/store.py",
                "symbols": [
                    "_validate_run_transition",
                    "append_run_transition",
                    "list_run_transitions",
                ],
            },
            {
                "path": "packages/maverick-core/maverick/flow/execution.py",
                "symbols": ["execute"],
            },
        ],
        "tests": [
            {
                "path": (
                    "packages/maverick-core/tests/"
                    "test_production_parser_state_properties.py"
                ),
                "symbols": [
                    "test_flow_transition_parser_fuzz_fails_only_with_typed_error"
                ],
            },
            {
                "path": (
                    "packages/maverick-core/tests/"
                    "test_flow_execution_hardening.py"
                ),
                "symbols": [
                    "test_every_work_boundary_persists_a_hash_chained_transition",
                    "test_transition_persistence_failure_stops_before_next_work_boundary",
                    "test_tampered_transition_is_rejected_when_loading_its_run",
                    "test_missing_transition_predecessor_is_rejected_when_loading_run",
                    "test_recomputed_older_transition_digest_cannot_rewrite_history",
                    "test_extra_transition_beyond_run_head_is_rejected",
                    "test_concurrent_resume_executes_downstream_action_once",
                ],
            },
        ],
        "ci": {"workflow": CI_WORKFLOW, "job": CI_TEST_JOB, "step": CI_TEST_STEP},
        "execution_status": "unconditional-repository-suite",
    },
)

SUPPLY_CHAIN_CONTROLS = (
    {
        "id": "all-release-critical-actions-pinned",
        "workflow": "all-release-critical-workflows",
        "job": "all",
        "step": "all uses directives",
        "required_fragments": [],
        "status": "repository-enforced",
        "external_result": "hosted-run-required",
    },
    {
        "id": "secret-baseline",
        "workflow": CI_WORKFLOW,
        "job": "lint",
        "step": "Secret scan (detect-secrets, no new secrets vs audited baseline)",
        "required_fragments": ["detect-secrets==1.5.0", ".secrets.baseline"],
        "status": "repository-enforced",
        "external_result": "hosted-run-required",
    },
    {
        "id": "cross-ecosystem-osv",
        "workflow": CI_WORKFLOW,
        "job": "audit",
        "step": "OSV Scanner across every reviewed dependency surface",
        "required_fragments": [
            "v2.3.8/osv-scanner_linux_amd64",
            "bc98e15319ed0d515e3f9235287ba53cdc5535d576d24fd573978ecfe9ab92dc",
            "--lockfile rust/Cargo.lock",
        ],
        "status": "repository-enforced",
        "external_result": "hosted-run-required",
    },
    {
        "id": "python-cve-audit",
        "workflow": CI_WORKFLOW,
        "job": "audit",
        "step": "pip-audit (known CVEs in dependencies)",
        "required_fragments": ["pip-audit==2.10.0", "pip-audit"],
        "status": "repository-enforced",
        "external_result": "hosted-run-required",
    },
    {
        "id": "locked-optional-cve-audit",
        "workflow": CI_WORKFLOW,
        "job": "audit",
        "step": "pip-audit every reviewed optional-dependency lock entry",
        "required_fragments": ["requirements/ci.txt", "--strict", "--no-deps"],
        "status": "repository-enforced",
        "external_result": "hosted-run-required",
    },
    {
        "id": "high-severity-sast",
        "workflow": CI_WORKFLOW,
        "job": "audit",
        "step": "SAST (bandit, high-severity)",
        "required_fragments": ["bandit==1.9.4", "-lll", "-ii"],
        "status": "repository-enforced",
        "external_result": "hosted-run-required",
    },
    {
        "id": "license-policy",
        "workflow": CI_WORKFLOW,
        "job": "audit",
        "step": "License policy (no strong-copyleft — GPL / AGPL / SSPL)",
        "required_fragments": ["policy_check", "strong-copyleft"],
        "status": "repository-enforced",
        "external_result": "hosted-run-required",
    },
    {
        "id": "release-runtime-sbom",
        "workflow": CI_WORKFLOW,
        "job": "audit",
        "step": "SBOM (CycloneDX, supply-chain transparency)",
        "required_fragments": ["cyclonedx-bom==7.3.1", "release-runtime"],
        "status": "repository-enforced",
        "external_result": "hosted-run-required",
    },
    {
        "id": "python-artifact-reproducibility",
        "workflow": ".github/workflows/publish.yml",
        "job": "build",
        "step": "Validate the tag and build the complete release cohort",
        "required_fragments": ["SOURCE_DATE_EPOCH", "dist-first", "dist-second"],
        "status": "repository-enforced",
        "external_result": "exact-release-run-required",
    },
    {
        "id": "release-checksum-manifest",
        "workflow": ".github/workflows/release.yml",
        "job": "release",
        "step": "Generate a complete release checksum manifest",
        "required_fragments": ["sha256sum --check", "maverick-release-"],
        "status": "repository-enforced",
        "external_result": "exact-release-run-required",
    },
    {
        "id": "keyless-release-signing",
        "workflow": ".github/workflows/release.yml",
        "job": "release",
        "step": "Sign release artifacts (keyless, Sigstore)",
        "required_fragments": ["cosign sign-blob", "--output-certificate"],
        "status": "repository-enforced",
        "external_result": "exact-release-transparency-record-required",
    },
)

# Conditions are part of the reviewed control, not an arbitrary escape hatch.
# Most controls must be fully unconditional.  Publish/release jobs have exact
# lifecycle prerequisite conditions; any weaker, additional, or disabled
# condition is rejected even if the command text is still present.
_RELEASE_JOB_CONDITION = (
    "always() && needs.validate-release-tag.outputs.release-state != 'published' "
    "&& needs.docker-image.result == 'success' && needs.binaries.result == "
    "'success' && needs.standalone-sku-artifacts.result == 'success' && "
    "needs.release-runtime-sbom.result == 'success'"
)
SUPPLY_CHAIN_JOB_CONDITIONS: dict[str, str | None] = {
    "secret-baseline": None,
    "cross-ecosystem-osv": None,
    "python-cve-audit": None,
    "locked-optional-cve-audit": None,
    "high-severity-sast": None,
    "license-policy": None,
    "release-runtime-sbom": None,
    "python-artifact-reproducibility": (
        "github.event_name == 'workflow_dispatch' || "
        "github.event.workflow_run.conclusion == 'success'"
    ),
    "release-checksum-manifest": _RELEASE_JOB_CONDITION,
    "keyless-release-signing": _RELEASE_JOB_CONDITION,
}
SUPPLY_CHAIN_JOB_NEEDS: dict[str, tuple[str, ...] | None] = {
    "secret-baseline": None,
    "cross-ecosystem-osv": None,
    "python-cve-audit": None,
    "locked-optional-cve-audit": None,
    "high-severity-sast": None,
    "license-policy": None,
    "release-runtime-sbom": None,
    "python-artifact-reproducibility": None,
    "release-checksum-manifest": (
        "validate-release-tag",
        "docker-image",
        "binaries",
        "standalone-sku-artifacts",
        "release-runtime-sbom",
    ),
    "keyless-release-signing": (
        "validate-release-tag",
        "docker-image",
        "binaries",
        "standalone-sku-artifacts",
        "release-runtime-sbom",
    ),
}

# The command text is only meaningful inside its exact reviewed GitHub Actions
# execution context.  These digests cover all workflow metadata other than
# ``jobs``; all job metadata other than ``steps``; and all step metadata other
# than ``run``/``uses``.  Consequently event filters, permissions,
# concurrency, env/default-shell inheritance, needs/if/continue-on-error,
# runner, strategy/matrix, container/services, timeout, environment, outputs,
# shell, working-directory, and step env/with cannot drift independently of a
# deliberate review of this verifier.
CRITICAL_WORKFLOW_CONTEXT_SHA256 = {
    CI_WORKFLOW: "85546f94a0a4d5bc1176d74247e41f00c5c1edfd6bc0d90be2ad0034419dc2b5",
    ".github/workflows/publish.yml": (
        "8dd6a2a1935dcd69a296751fce1f2b178110958c783f4c1f0214c7ba8f86b53b"
    ),
    ".github/workflows/release.yml": (
        "85b9758e0d3bee9db91c9c1dc8e2c6d0eaf90986135e4274206a9cd244af98d1"
    ),
}
CRITICAL_JOB_CONTEXT_SHA256 = {
    (CI_WORKFLOW, "lint"): (
        "26309d895a6638e57270dd9ab3619efcc848b1e01bb6c10907a3c867e1282ead"
    ),
    (CI_WORKFLOW, "audit"): (
        "cb5a9626e21d8775c2fde2b8800ea675e9c3f60ce133c3e12f108e2c9bcfff16"
    ),
    (CI_WORKFLOW, CI_TEST_JOB): (
        "36ae620512fd6d19ed918766719a865a3c754a829345c120eab875bb0afd7690"
    ),
    (".github/workflows/publish.yml", "build"): (
        "aa04a5d09c0e66d8d6018a1ecc9a8994c3af8bc3e619895a099d92ff8909876e"
    ),
    (".github/workflows/release.yml", "validate-release-tag"): (
        "ce0a05894a2341e9be7adc9c3daaf8587c0a16ab9a2e7b717c9de3220aacd7d7"
    ),
    (".github/workflows/release.yml", "docker-image"): (
        "ca2dc4d548cd9220bd1051e1dd853f2a10d8a16728528d10e8e1cd4f0099ab18"
    ),
    (".github/workflows/release.yml", "binaries"): (
        "c4ba933dce36738d0f02365ef16d8a21f38e84073a377a6675c7e4212f28ceea"
    ),
    (".github/workflows/release.yml", "standalone-sku-artifacts"): (
        "d7ccabc00101e15791cb95209b334d4734c306630524f89e497ea04b09c61acd"
    ),
    (".github/workflows/release.yml", "release-runtime-sbom"): (
        "b087ad849ade615ada078f1a9d0cbb9ae5c57274942c8af2cd32572bcc43c51f"
    ),
    (".github/workflows/release.yml", "release"): (
        "2c9b84e16c4062ed4b65a338906ac27476cd2587f97fe13998569e837d05eca3"
    ),
}
CRITICAL_STEP_CONTEXT_SHA256 = {
    (CI_WORKFLOW, "lint", "Secret scan (detect-secrets, no new secrets vs audited baseline)"): (
        "8f41a827b3c769a93988a4e63a47b75d4ea44fb1ff72a270cbbd7d5a146dc1fd"
    ),
    (CI_WORKFLOW, "audit", "OSV Scanner across every reviewed dependency surface"): (
        "f75b6008ae316fa9b95012cf1ee41c288185d1c2211facfef3fdcc05c72856ed"
    ),
    (CI_WORKFLOW, "audit", "pip-audit (known CVEs in dependencies)"): (
        "98776bf42cf78af7e8382fd5158b90a794a8c3f5d3ce1b8a1d235114c97a8e27"
    ),
    (CI_WORKFLOW, "audit", "pip-audit every reviewed optional-dependency lock entry"): (
        "b3c5ef233e001b6e8a6451bdf82ddd8471e0808a7a0b35a39300244341afd7d8"
    ),
    (CI_WORKFLOW, "audit", "SAST (bandit, high-severity)"): (
        "82ba9293e44f7620eda4956d0a14f5815f999cf3aa348f72cbcc81ae78f484cb"
    ),
    (CI_WORKFLOW, "audit", "License policy (no strong-copyleft — GPL / AGPL / SSPL)"): (
        "66ced05ec7d2b84c65a33c0188a614b93c0ffa31c0bfed32dea1b33fa9c5dc15"
    ),
    (CI_WORKFLOW, "audit", "SBOM (CycloneDX, supply-chain transparency)"): (
        "5e4e18a021084fb1653af9c880fb28575798360869a820224017243d2bd58002"
    ),
    (
        ".github/workflows/publish.yml",
        "build",
        "Validate the tag and build the complete release cohort",
    ): "ac6fcff513524b8875799d754d731d26950b19d2b3a395a58e9330126fc861e4",
    (
        ".github/workflows/release.yml",
        "release",
        "Generate a complete release checksum manifest",
    ): "7b1e010d7e7521bc6554cee234620696d56b5c4dde00ef890e2bc75c1424e2f3",
    (
        ".github/workflows/release.yml",
        "release",
        "Sign release artifacts (keyless, Sigstore)",
    ): "7986260b8514145c277b2d832ebea268586bd020734d9b9b007cd31a0ba92feb",
    (CI_WORKFLOW, CI_TEST_JOB, CI_TEST_STEP): (
        "71f7f283d8f857b54d804ce43bb70d873b4df00273102115ae802a6e047cb911"
    ),
}
REVIEWED_WORKFLOW_SEMANTIC_SHA256 = {
    CI_WORKFLOW: "00d287153049cff4c7bb86c3cad55bf30f983c7556f0859133e6bdc63eae93a5",
    ".github/workflows/native.yml": (
        "751cfcb22cf238f427fdd780a695821532a310e0cf98aecade29797397afe461"
    ),
    ".github/workflows/publish.yml": (
        "f9c8c6f451732b6c60de0bab1a411904b6b19484f994940529020f0998ba9497"
    ),
    ".github/workflows/release.yml": (
        "6836026f5b5965d5547751dc8530cc97586995a74777abd7a3709661afbd72de"
    ),
    ".github/workflows/compliance-posture.yml": (
        "391fa6635c6d2d8b74f077c3fc002a1464f93e5afa7ef480c7e1ac5fe56aef0b"
    ),
}
CRITICAL_JOB_SEMANTIC_SHA256 = {
    (CI_WORKFLOW, "lint"): (
        "77267ea2656fb6f8387f18e240a197c2fea89dc64180bbc65d0afaa25f3d2b6d"
    ),
    (CI_WORKFLOW, "audit"): (
        "8cc046b4ca7158071bb4eaabc71dad3e4bbfaeaa668df2d8ec027f76db059174"
    ),
    (CI_WORKFLOW, CI_TEST_JOB): (
        "51b2d543867b6b0647e1ce5156c59ac11e94150ca2067e2739bff5c1959c4d76"
    ),
    (".github/workflows/publish.yml", "build"): (
        "c2d24f58b6774cdcc8e71b34bb1041f3fc4dde5c4e1ffb1df75c73863ba09544"
    ),
    (".github/workflows/release.yml", "validate-release-tag"): (
        "823265f1ec63988d3fc463e47214a861955254318571c376a592de3ddb07998c"
    ),
    (".github/workflows/release.yml", "docker-image"): (
        "74df493ec9aadb9c61a0fcd560c40924c36406f9c93a6652a2523b012bd6e41b"
    ),
    (".github/workflows/release.yml", "binaries"): (
        "a94d9c0feb5ca6ccc3ec3d605f117b55555c5b761a47c951816c63fde6c428f0"
    ),
    (".github/workflows/release.yml", "standalone-sku-artifacts"): (
        "945a1b4e8ba78172425ba7951afdd0f813ac9a33828b2680b1ec4fb868293850"
    ),
    (".github/workflows/release.yml", "release-runtime-sbom"): (
        "d464dd6b3f72af8b801d6eccbfd9aa02e33e7e6039e7a3a15cc688a211f885c6"
    ),
    (".github/workflows/release.yml", "release"): (
        "f38dac64f9beb60326fa830f6d27802ad5eae7c281a8a48562dbfe361c134316"
    ),
}
REVIEWED_PYTEST_SOURCE_SHA256 = {
    "conftest.py": "77c1ff67c03b38f2af9ad35092684ae6844d1112e27d30529048ac9c6047dc9b",
    "packages/maverick-core/tests/conftest.py": (
        "17943ce291cdc50be53759a9c60103828ec7f8f2f0dfb1da17b44c77599e9356"
    ),
    "packages/maverick-core/tests/test_api_idempotency.py": (
        "2f2a6f3aaf84c88aa8492823a2de0a1abb653dafdd175adfd5a2ad8dbe3e1416"
    ),
    "packages/maverick-core/tests/test_api_idempotency_retirement.py": (
        "95498fce3037e24de3745a3d1adba0a4d7695476d9fa18ed9ef6be70eb1b028a"
    ),
    "packages/maverick-core/tests/test_capability_attenuation_fuzz.py": (
        "04c09bbe958ec7bb0a83bc93a37af593edc91eef0db276e6c36c39f8976a14f4"
    ),
    "packages/maverick-core/tests/test_capability_fuzzer.py": (
        "735a46a51f42387a582e136f23b23fbdccdcbe5616448345d7e98da2bf6690f4"
    ),
    "packages/maverick-core/tests/test_control_plane_lease.py": (
        "c0423806144923de65e44c64af8be2312eca384c333024e25790c64f54427029"
    ),
    "packages/maverick-core/tests/test_crash_only_log.py": (
        "3e8639961f3deb2b01531f99654f8478813f7a1abbc2e5f6d28d4a3212172e22"
    ),
    "packages/maverick-core/tests/test_durable_checkpoint.py": (
        "dfd26dd547d50bc8cfd6251dbdb445d7959fe1822fc6fd5de1f5ac493d994c6f"
    ),
    "packages/maverick-core/tests/test_durable_child_checkpoint.py": (
        "84ec429c392a3e5225d766b180a53abf9cfbb6fb4ae3c0fa6d296714c56e1acb"
    ),
    "packages/maverick-core/tests/test_erasure_receipts.py": (
        "255d327666b267fa9b1bb508c3655d6a75456a8fbefe6bf1c2b4f9902aff885b"
    ),
    "packages/maverick-core/tests/test_flow_execution_hardening.py": (
        "ac21fc1d5cdbca3f00b4a94cff78dcb8c3e3edc2173a7d2b98aede5f61fe7917"
    ),
    "packages/maverick-core/tests/test_flow_postgres_authority.py": (
        "53e114231aec2c64ca93f7a6ebe1889d182fa0b186a356d9bf1bbcd3f1b82292"
    ),
    "packages/maverick-core/tests/test_job_reclaim.py": (
        "8f62d525863e2c6609be5f30a9f7e2c1a4497ee167cbbd18ba00ceb6adbade2a"
    ),
    "packages/maverick-core/tests/test_job_queue_dedup.py": (
        "0334f85e99c9482c1a34e06600dfc22d6d80c5a9f2844dc3355645fcd5452f94"
    ),
    "packages/maverick-core/tests/test_operator_contract.py": (
        "155ab6f2c9c87c3d4e036be8988af72e21d736e3dbf3e37878e4b8a86db5c5a8"
    ),
    "packages/maverick-core/tests/test_parser_fuzz.py": (
        "df447c074a8f4f88974235f4c0699428c743932023907313f00a3a9645cfbcae"
    ),
    "packages/maverick-core/tests/test_production_migration.py": (
        "e10cc2d2920fb611c2cf92eb6cc87052d007f253f63d065ca360c5b34a6796ba"
    ),
    "packages/maverick-core/tests/test_production_parser_state_properties.py": (
        "bd4f4c82ebb17713ca4247d6b142842e9901196f5a3515c787237c6dbfc3b344"
    ),
    "packages/maverick-core/tests/test_provider_usage_required.py": (
        "927fe2343b2026c0feb8b7bde5bc3e5121aa6b95eb994dd373241a922d0b50c8"
    ),
    "packages/maverick-core/tests/test_runner_drain.py": (
        "363e7ae32397d1273e3086273f4f57a12f92e871d96d1e2e4c79c9ab0261a306"
    ),
    "packages/maverick-core/tests/test_secrets_scrub_fuzz.py": (
        "6f71109ad448c2f1a29f4c410459f9059f1f28fc72c8049fa10d83701e31c02e"
    ),
    "packages/maverick-core/tests/test_signoff_authority.py": (
        "715eacd5c81109c8bed9781282c0715e282c3e8f1649c007228f7c733e56786c"
    ),
    "packages/maverick-core/tests/test_tenant_quota_authority.py": (
        "24d991b8be22814ebe5be9494f07a32ee1823debc700a6085ed0e232e678f3ee"
    ),
    "packages/maverick-dashboard/tests/test_idempotency_delivery_recovery.py": (
        "eefed8891d9ab0c3497676045af9e2827556486140e6cd6ca7d3ab992b536286"
    ),
    "packages/maverick-dashboard/tests/test_flow_operation_idempotency.py": (
        "41edcf3cf05f7c300563b361d29663af6c0765593a1e21a7d73fc760cf4232f7"
    ),
    "packages/maverick-dashboard/tests/test_production_boundary_fuzz.py": (
        "786e353309fbf62bbcd82a333673666617b89e8d4511047756ee4c71ed9e2f28"
    ),
    "packages/maverick-dashboard/tests/test_production_task_lifecycle.py": (
        "3b8807f7090b221b0af7812fdc4a46b4dcea18a21809b6d79843a471ec902be6"
    ),
    "packages/maverick-dashboard/tests/test_coordinated_shutdown.py": (
        "8e5de75d9d7c9938c1324a14e7876fb0762690fa4246738b66268dabcf283f14"
    ),
}
REVIEWED_TEST_SUPPORT_SOURCE_SHA256 = {
    "lightwork_test_support/__init__.py": (
        "2675f45d0f2693b8202784f93a59dc1e49dd0a6d1aff7b69bdef4f27eb6e78b2"
    ),
    "lightwork_test_support/spawn.py": (
        "c714992f2c36c116cc360124ac06edaf47f450b383adad35177bb13a979ee0a5"
    ),
}

# A substring can live in an echo, comment, or dead branch.  Each asserted
# control therefore also needs an anchored executable/code statement that
# actually invokes or evaluates the relevant gate.
SUPPLY_CHAIN_COMMAND_PATTERNS: dict[str, str] = {
    "secret-baseline": r"(?m)^\s*python\s+-m\s+detect_secrets\s+scan\b",
    "cross-ecosystem-osv": r"(?m)^\s*\"?\$scanner\"?\s+scan\s+source\b",
    "python-cve-audit": r"(?m)^\s*pip-audit\s*$",
    "locked-optional-cve-audit": r"(?m)^\s*python\s+-I\s+-m\s+pip_audit\b",
    "high-severity-sast": r"(?m)^\s*python\s+-m\s+bandit\b",
    "license-policy": r"(?m)^\s*violations\s*=.*\bpolicy_check\(scanned\)",
    "release-runtime-sbom": r"(?m)^\s*cyclonedx-py\s+environment\b",
    "python-artifact-reproducibility": r"(?m)^\s*\(cd\s+.*&&\s+python\s+-m\s+build\b",
    "release-checksum-manifest": r"(?m)^\s*\(cd\s+dist\s+&&\s+sha256sum\s+--check\b",
    "keyless-release-signing": r"(?m)^\s*cosign\s+sign-blob\b",
}

MAPPED_PYTEST_COMMAND_SHA256 = (
    "63feec12c69859bacb66256516667a61c5b5e39d4afb396ff67f6a996f6a2e8a"
)
SUPPLY_CHAIN_COMMAND_SHA256 = {
    "secret-baseline": "951b425609b149382c752ed6eb15c34246a38d71ab2380dcf79d72c7b84020bc",
    "cross-ecosystem-osv": "4fc0c5b7b052e0efb8159a1850e59ea7e27859f53c6773c0e6bf269ec5a939c0",
    "python-cve-audit": "c55e809268f5e4508b6817d970e628ad903cef3b38bd50529e2bc7209b688e96",
    "locked-optional-cve-audit": "8ade04d71958ce5d0c3449529e118b4bc6eaa19dd71101d6cc8b5e51ad509055",
    "high-severity-sast": "10ea32e516d938f0ab75c2d8aa5eac2732c3be481e0b471987bd181ea4ddd9c9",
    "license-policy": "75c74181701250e7b5d4e7eb4c8fffa860558dbdb5f2cedac9685c4d67fe8519",
    "release-runtime-sbom": "2e4afd648f6f03f893a70466a755a344532f7e4a1166c9e0acda658efd5c44b1",
    "python-artifact-reproducibility": "14f21a01d38a14ac46eefa4ea0f047cb974fef7c07e2423693dbb148909976a8",
    "release-checksum-manifest": "939d6b172faaeae59ec08c65a83ecb3e8e013cc1fd00a0ab92a2d97ed7f0e13d",
    "keyless-release-signing": "dcca414ed81a32561aba4271c83a992c184045c26e05b76cac4982197af0d77f",
}

COMPLIANCE_REGIMES = (
    {
        "id": "soc2",
        "claim_status": "draft-control-mapping-not-certification",
        "certified": False,
        "external_assessment_required": True,
        "owner": "security-and-compliance",
        "review_cadence_days": 90,
        "exception_process": "docs/compliance/risk-management-methodology.md",
        "documents": [
            "docs/compliance/README.md",
            "docs/compliance/control-crosswalk.md",
            "docs/compliance/soc2-controls.md",
            "docs/compliance/soc2/README.md",
        ],
        "customer_responsibilities": [
            "deployment configuration and access administration",
            "customer-controlled identity data retention and incident decisions",
            "independent reliance and auditor scoping",
        ],
    },
    {
        "id": "iso-27001",
        "claim_status": "draft-control-mapping-not-certification",
        "certified": False,
        "external_assessment_required": True,
        "owner": "security-and-compliance",
        "review_cadence_days": 90,
        "exception_process": "docs/compliance/risk-management-methodology.md",
        "documents": [
            "docs/compliance/iso-27001/README.md",
            "docs/compliance/iso-27001/statement-of-applicability.md",
        ],
        "customer_responsibilities": [
            "define the deployment ISMS scope",
            "operate customer-side controls and retain evidence",
            "obtain independent certification when claimed",
        ],
    },
    {
        "id": "iso-42001",
        "claim_status": "draft-control-mapping-not-certification",
        "certified": False,
        "external_assessment_required": True,
        "owner": "security-and-compliance",
        "review_cadence_days": 90,
        "exception_process": "docs/compliance/risk-management-methodology.md",
        "documents": [
            "docs/compliance/iso-42001/README.md",
            "docs/compliance/iso-42001/statement-of-applicability.md",
        ],
        "customer_responsibilities": [
            "define AI-system purpose context and affected parties",
            "complete customer impact risk and human-oversight decisions",
            "obtain independent certification when claimed",
        ],
    },
)

INCIDENT_PROCESSES = (
    {
        "id": "incident-response",
        "status": "repository-process-only",
        "owner": "security-incident-commander",
        "review_cadence_days": 90,
        "staffing_verified": False,
        "live_drill_required": True,
        "documents": [
            "SECURITY.md",
            "docs/compliance/policies/incident-response-policy.md",
            "docs/compliance/procedures/incident-response-runbook.md",
            "docs/compliance/templates/incident-report-template.md",
        ],
        "source_controls": [
            "packages/maverick-core/maverick/killswitch.py",
            "packages/maverick-core/maverick/security_ops.py",
            "packages/maverick-core/tests/test_security_ops.py",
        ],
        "customer_responsibilities": [
            "supply deployment contacts and escalation routing",
            "operate customer infrastructure containment and evidence retention",
        ],
    },
    {
        "id": "vulnerability-management",
        "status": "repository-process-only",
        "owner": "product-security",
        "review_cadence_days": 30,
        "staffing_verified": False,
        "live_drill_required": True,
        "documents": [
            "SECURITY.md",
            "docs/compliance/procedures/vulnerability-management-procedure.md",
        ],
        "source_controls": [
            ".github/workflows/ci.yml",
            "requirements/ci.txt",
            "osv-scanner.toml",
        ],
        "customer_responsibilities": [
            "monitor exact deployed versions and customer dependencies",
            "apply qualified remediations within customer risk windows",
        ],
    },
    {
        "id": "coordinated-disclosure",
        "status": "repository-process-only",
        "owner": "product-security",
        "review_cadence_days": 90,
        "staffing_verified": False,
        "live_drill_required": True,
        "documents": ["SECURITY.md"],
        "source_controls": [".github/CODEOWNERS"],
        "customer_responsibilities": [
            "protect deployment-specific reports and secrets",
            "coordinate customer-controlled notifications and legal duties",
        ],
    },
)

WORKFLOW_BINDINGS = (
    ".github/workflows/ci.yml",
    ".github/workflows/release.yml",
    ".github/workflows/publish.yml",
    ".github/workflows/native.yml",
    ".github/workflows/compliance-posture.yml",
)
LOCAL_TEST_DISTRIBUTION_PYPROJECTS = (
    "packages/maverick-core/pyproject.toml",
    "packages/maverick-shield/pyproject.toml",
    "packages/maverick-channels/pyproject.toml",
    "packages/maverick-evolve/pyproject.toml",
    "packages/maverick-dashboard/pyproject.toml",
    "packages/maverick-mcp/pyproject.toml",
    "packages/maverick-knowledge/pyproject.toml",
    "apps/installer-cli/pyproject.toml",
)

_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_PINNED_ACTION_RE = re.compile(r"(?:^|/)\S+@[0-9a-f]{40}$")


class ProductionQaAssuranceError(ValueError):
    """The QA assurance mapping is malformed, stale, or overclaims evidence."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise ProductionQaAssuranceError(
                f"{label} is missing or not a non-empty regular file"
            )
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except ProductionQaAssuranceError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProductionQaAssuranceError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProductionQaAssuranceError(f"{label} must be an object")
    return value


def _source_sha256(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ProductionQaAssuranceError(
            f"source binding is not readable UTF-8: {path}"
        ) from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _mapped_paths() -> tuple[str, ...]:
    paths = set(WORKFLOW_BINDINGS)
    paths.update(LOCAL_TEST_DISTRIBUTION_PYPROJECTS)
    paths.update(REVIEWED_TEST_SUPPORT_SOURCE_SHA256)
    paths.update(
        {
            ".github/CODEOWNERS",
            ".secrets.baseline",
            "SECURITY.md",
            "conftest.py",
            "osv-scanner.toml",
            "packages/maverick-core/tests/conftest.py",
            "pyproject.toml",
            "requirements/ci.txt",
        }
    )
    for row in FUZZ_PROPERTY_MAPPINGS:
        paths.update(item["path"] for item in row["sources"])
        paths.update(item["path"] for item in row["tests"])
    for row in COMPLIANCE_REGIMES:
        paths.update(row["documents"])
        paths.add(row["exception_process"])
    for row in INCIDENT_PROCESSES:
        paths.update(row["documents"])
        paths.update(row["source_controls"])
    return tuple(sorted(paths))


SOURCE_BINDING_PATHS = _mapped_paths()


def _exact_mapping(
    value: object,
    expected: set[str],
    *,
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionQaAssuranceError(f"{label} must be an object")
    if set(value) != expected:
        raise ProductionQaAssuranceError(
            f"{label} fields differ: missing={sorted(expected - set(value))}, "
            f"unknown={sorted(set(value) - expected)}"
        )
    return value


def _workflow(root: Path, relative: str) -> Mapping[str, Any]:
    if yaml is None:
        raise ProductionQaAssuranceError("PyYAML is required by the enforced QA lane")
    try:
        workflow_text = (root / relative).read_text(encoding="utf-8")
        if any(
            isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken))
            for token in yaml.scan(workflow_text)
        ):
            raise ProductionQaAssuranceError(
                f"workflow anchors and aliases are not admitted: {relative}"
            )
        document = yaml.load(
            workflow_text,
            Loader=_StrictWorkflowLoader,
        )
    except ProductionQaAssuranceError:
        raise
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ProductionQaAssuranceError(f"workflow is not parseable: {relative}") from exc
    if not isinstance(document, Mapping) or not isinstance(document.get("jobs"), Mapping):
        raise ProductionQaAssuranceError(f"workflow has no jobs mapping: {relative}")
    if "on" not in document:
        raise ProductionQaAssuranceError(
            f"workflow lacks the exact GitHub trigger key: {relative}"
        )
    return document


def _canonical_context(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_context(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_canonical_context(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ProductionQaAssuranceError(
        f"workflow context contains an unsupported value: {type(value).__name__}"
    )


def _context_sha256(value: Mapping[str, Any], *, omit: frozenset[str]) -> str:
    context = {
        key: item
        for key, item in value.items()
        if isinstance(key, str) and key not in omit
    }
    # PyYAML 1.1 parses GitHub's top-level ``on`` key as boolean ``True``.
    # Preserve non-string keys through the canonicalizer rather than silently
    # dropping the workflow trigger from the reviewed semantic digest.
    context.update(
        {
            key: item
            for key, item in value.items()
            if not isinstance(key, str)
        }
    )
    try:
        raw = json.dumps(
            _canonical_context(context),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProductionQaAssuranceError(
            "workflow context is not canonically serializable"
        ) from exc
    return hashlib.sha256(raw).hexdigest()


def _verify_workflow_context(
    document: Mapping[str, Any],
    *,
    workflow: str,
) -> None:
    expected = CRITICAL_WORKFLOW_CONTEXT_SHA256.get(workflow)
    if expected is None or _context_sha256(
        document,
        omit=frozenset({"jobs"}),
    ) != expected:
        raise ProductionQaAssuranceError(
            f"critical workflow execution context drifted: {workflow}"
        )


def _job_needs(job_value: Mapping[str, Any], *, label: str) -> tuple[str, ...]:
    value = job_value.get("needs")
    if value is None:
        return ()
    if isinstance(value, str) and value:
        return (value,)
    if (
        isinstance(value, list)
        and value
        and all(isinstance(item, str) and item for item in value)
        and len(value) == len(set(value))
    ):
        return tuple(value)
    raise ProductionQaAssuranceError(
        f"workflow job prerequisites are malformed: {label}"
    )


def _verify_job_context(
    document: Mapping[str, Any],
    *,
    workflow: str,
    job: str,
    include_steps: bool,
    visiting: frozenset[str] = frozenset(),
) -> None:
    if job in visiting:
        raise ProductionQaAssuranceError(
            f"critical workflow prerequisite cycle: {workflow}:{job}"
        )
    jobs = document["jobs"]
    job_value = jobs.get(job)
    if not isinstance(job_value, Mapping):
        raise ProductionQaAssuranceError(
            f"critical workflow prerequisite is missing: {workflow}:{job}"
        )
    key = (workflow, job)
    expected_context = CRITICAL_JOB_CONTEXT_SHA256.get(key)
    if expected_context is None or _context_sha256(
        job_value,
        omit=frozenset({"steps"}),
    ) != expected_context:
        raise ProductionQaAssuranceError(
            f"critical workflow job execution context drifted: {workflow}:{job}"
        )
    if include_steps:
        expected_full = CRITICAL_JOB_SEMANTIC_SHA256.get(key)
        if expected_full is None or _context_sha256(
            job_value,
            omit=frozenset(),
        ) != expected_full:
            raise ProductionQaAssuranceError(
                f"critical workflow full job semantics drifted: {workflow}:{job}"
            )
    next_visiting = visiting | {job}
    for prerequisite in _job_needs(
        job_value,
        label=f"{workflow}:{job}",
    ):
        _verify_job_context(
            document,
            workflow=workflow,
            job=prerequisite,
            include_steps=True,
            visiting=next_visiting,
        )


def _verify_full_job_semantics(root: Path, *, workflow: str, job: str) -> None:
    document = _workflow(root, workflow)
    expected = CRITICAL_JOB_SEMANTIC_SHA256.get((workflow, job))
    job_value = document["jobs"].get(job)
    if (
        expected is None
        or not isinstance(job_value, Mapping)
        or _context_sha256(job_value, omit=frozenset()) != expected
    ):
        raise ProductionQaAssuranceError(
            f"critical workflow full job semantics drifted: {workflow}:{job}"
        )


def _verify_full_workflow_semantics(
    document: Mapping[str, Any],
    *,
    workflow: str,
) -> None:
    expected = REVIEWED_WORKFLOW_SEMANTIC_SHA256.get(workflow)
    if expected is None or _context_sha256(
        document,
        omit=frozenset(),
    ) != expected:
        raise ProductionQaAssuranceError(
            f"release-critical workflow semantics drifted: {workflow}"
        )


_CONDITION_UNCHECKED = object()


def _workflow_step(
    root: Path,
    *,
    workflow: str,
    job: str,
    step: str,
    require_unconditional: bool = False,
    expected_job_needs: tuple[str, ...] | None | object = _CONDITION_UNCHECKED,
    expected_job_condition: str | None | object = _CONDITION_UNCHECKED,
    expected_step_condition: str | None | object = _CONDITION_UNCHECKED,
) -> str:
    document = _workflow(root, workflow)
    jobs = document["jobs"]
    job_value = jobs.get(job)
    if not isinstance(job_value, Mapping):
        raise ProductionQaAssuranceError(f"workflow job is missing: {workflow}:{job}")
    job_continue = job_value.get("continue-on-error")
    if job_continue is not None and job_continue is not False:
        raise ProductionQaAssuranceError(
            f"workflow gate job permits failure: {workflow}:{job}"
        )
    if require_unconditional and "if" in job_value:
        raise ProductionQaAssuranceError(
            f"mapped test job is conditional: {workflow}:{job}"
        )
    if expected_job_needs is not _CONDITION_UNCHECKED:
        if expected_job_needs is None:
            needs_match = "needs" not in job_value
        else:
            needs_match = job_value.get("needs") == list(expected_job_needs)
        if not needs_match:
            raise ProductionQaAssuranceError(
                "workflow gate job prerequisites drifted or are unreviewed: "
                f"{workflow}:{job}"
            )
    if expected_job_condition is not _CONDITION_UNCHECKED:
        if expected_job_condition is None:
            condition_matches = "if" not in job_value
        else:
            condition_matches = job_value.get("if") == expected_job_condition
        if not condition_matches:
            raise ProductionQaAssuranceError(
                f"supply-chain job condition drifted or is disabled: {workflow}:{job}"
            )
    _verify_workflow_context(document, workflow=workflow)
    _verify_job_context(
        document,
        workflow=workflow,
        job=job,
        include_steps=False,
    )
    steps = job_value.get("steps")
    if not isinstance(steps, list):
        raise ProductionQaAssuranceError(f"workflow job has no steps: {workflow}:{job}")
    matches = [item for item in steps if isinstance(item, Mapping) and item.get("name") == step]
    if len(matches) != 1:
        raise ProductionQaAssuranceError(
            f"workflow step must exist exactly once: {workflow}:{job}:{step}"
        )
    value = matches[0]
    step_continue = value.get("continue-on-error")
    if step_continue is not None and step_continue is not False:
        raise ProductionQaAssuranceError(
            f"workflow gate step permits failure: {workflow}:{job}:{step}"
        )
    if require_unconditional and "if" in value:
        raise ProductionQaAssuranceError(
            f"mapped test step is conditional: {workflow}:{job}:{step}"
        )
    if expected_step_condition is not _CONDITION_UNCHECKED:
        if expected_step_condition is None:
            condition_matches = "if" not in value
        else:
            condition_matches = value.get("if") == expected_step_condition
        if not condition_matches:
            raise ProductionQaAssuranceError(
                "supply-chain step condition drifted or is disabled: "
                f"{workflow}:{job}:{step}"
            )
    expected_step_context = CRITICAL_STEP_CONTEXT_SHA256.get(
        (workflow, job, step)
    )
    if expected_step_context is None or _context_sha256(
        value,
        omit=frozenset({"run", "uses"}),
    ) != expected_step_context:
        raise ProductionQaAssuranceError(
            f"critical workflow step execution context drifted: {workflow}:{job}:{step}"
        )
    run = value.get("run")
    uses = value.get("uses")
    if isinstance(run, str) and run.strip():
        return run
    if isinstance(uses, str) and uses.strip():
        return uses
    raise ProductionQaAssuranceError(
        f"workflow step is not executable: {workflow}:{job}:{step}"
    )


def _executable_text(command: str) -> str:
    """Remove comment-only and trailing-comment text from a workflow command."""
    executable: list[str] = []
    for line in command.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        executable.append(line.split(" #", 1)[0])
    return "\n".join(executable)


def _command_sha256(command: str) -> str:
    normalized = command.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _assert_no_statically_inert_shell(command: str, *, label: str) -> str:
    """Reject common shell constructs that make visible gate text unreachable."""

    executable = _executable_text(command)
    inert_patterns = (
        r"(?is)(?:^|[;\n]\s*)if\s+(?:false|!\s*true|0)\s*;?\s*then\b",
        r"(?m)^\s*false\s*&&",
        r"(?m)^\s*(?:exit|return)\s+0\s*(?:;|$)",
        r"(?m)^\s*:\s*<<[-]?\s*['\"]?\w+",
    )
    for pattern in inert_patterns:
        if re.search(pattern, executable):
            raise ProductionQaAssuranceError(
                f"supply-chain control is statically inert: {label}"
            )
    return executable


def _verify_action_pins(root: Path) -> int:
    count = 0
    for workflow in WORKFLOW_BINDINGS:
        document = _workflow(root, workflow)
        for job_id, job_value in document["jobs"].items():
            if not isinstance(job_value, Mapping):
                continue
            job_uses = job_value.get("uses")
            if job_uses is not None:
                if not isinstance(job_uses, str):
                    raise ProductionQaAssuranceError(
                        f"reusable workflow reference is malformed: {workflow}:{job_id}"
                    )
                if job_uses.startswith("./.github/workflows/"):
                    # No local reusable workflow is in the reviewed transitive
                    # inventory today.  Merely checking that a new file exists
                    # would skip its own unpinned actions, so additions fail
                    # closed until they are recursively inventoried here.
                    raise ProductionQaAssuranceError(
                        "local reusable workflow is outside the reviewed inventory: "
                        f"{workflow}:{job_uses!r}"
                    )
                elif _PINNED_ACTION_RE.fullmatch(job_uses) is None:
                    raise ProductionQaAssuranceError(
                        "release-critical reusable workflow is not commit-pinned: "
                        f"{workflow}:{job_uses!r}"
                    )
                count += 1
            steps = job_value.get("steps")
            if not isinstance(steps, list):
                continue
            for step in steps:
                uses = step.get("uses") if isinstance(step, Mapping) else None
                if uses is None:
                    continue
                if not isinstance(uses, str) or _PINNED_ACTION_RE.fullmatch(uses) is None:
                    raise ProductionQaAssuranceError(
                        f"release-critical action is not commit-pinned: {workflow}:{uses!r}"
                    )
                count += 1
        _verify_full_workflow_semantics(document, workflow=workflow)
    if count < 40:
        raise ProductionQaAssuranceError(
            f"unexpectedly small pinned action inventory: {count}"
        )
    return count


def _symbols(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise ProductionQaAssuranceError(f"mapped Python source is invalid: {path}") from exc
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _dotted_name(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                local = imported.asname or imported.name.split(".", 1)[0]
                aliases[local] = imported.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for imported in node.names:
                local = imported.asname or imported.name
                aliases[local] = f"{node.module}.{imported.name}"
    return aliases


def _resolved_expression_name(
    node: ast.AST,
    aliases: Mapping[str, str],
) -> str:
    if isinstance(node, ast.Call):
        called = _resolved_expression_name(node.func, aliases)
        if (
            called in {"getattr", "builtins.getattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            base = _resolved_expression_name(node.args[0], aliases)
            if base:
                return f"{base}.{node.args[1].value}"
        return called
    dotted = _dotted_name(node)
    if not dotted:
        return ""
    head, separator, tail = dotted.partition(".")
    resolved = aliases.get(head, head)
    return f"{resolved}.{tail}" if separator else resolved


def _is_skip_control(name: str) -> bool:
    return name in {
        "pytest.importorskip",
        "pytest.skip",
        "pytest.skip.Exception",
        "pytest.xfail",
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "pytest.mark.xfail",
        "request.applymarker",
        "unittest.SkipTest",
        "unittest.expectedFailure",
        "unittest.skip",
        "unittest.skipIf",
        "unittest.skipUnless",
    }


def _verify_mapped_tests_are_unskippable(
    path: Path,
    required: set[str],
    *,
    reviewed_path: str | None = None,
) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise ProductionQaAssuranceError(f"mapped test source is invalid: {path}") from exc
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    aliases = _import_aliases(tree)
    for item in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(item, ast.Assign):
            targets = list(item.targets)
        elif isinstance(item, ast.AnnAssign):
            targets = [item.target]
        elif isinstance(item, ast.AugAssign):
            targets = [item.target]
        if any(
            (isinstance(target, ast.Name) and target.id == "__test__")
            or (
                isinstance(target, ast.Attribute)
                and target.attr == "__test__"
                and isinstance(target.value, ast.Name)
                and target.value.id in required
            )
            for target in targets
        ):
            raise ProductionQaAssuranceError(
                f"mapped test can be hidden from collection with __test__: {path}"
            )
        if (
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
            and item.func.id == "setattr"
            and len(item.args) >= 2
            and isinstance(item.args[0], ast.Name)
            and item.args[0].id in required
            and isinstance(item.args[1], ast.Constant)
            and item.args[1].value == "__test__"
        ):
            raise ProductionQaAssuranceError(
                f"mapped test can be hidden from collection with setattr: {path}"
            )
        if (
            (isinstance(item, ast.Name) and item.id == "__test__")
            or (isinstance(item, ast.Attribute) and item.attr == "__test__")
            or (
                isinstance(item, ast.Constant)
                and item.value == "__test__"
            )
            or (isinstance(item, ast.keyword) and item.arg == "__test__")
        ):
            raise ProductionQaAssuranceError(
                f"mapped test can be hidden from collection with __test__: {path}"
            )
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if any(
                _resolved_expression_name(item.func, aliases)
                == "pytest.importorskip"
                for item in ast.walk(node)
                if isinstance(item, ast.Call)
            ):
                raise ProductionQaAssuranceError(
                    f"mapped test module can skip at import time: {path}"
                )
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets):
                value = node.value
                if any(
                    _is_skip_control(_resolved_expression_name(item, aliases))
                    for item in ast.walk(value)
                    if isinstance(item, (ast.Attribute, ast.Name))
                ):
                    raise ProductionQaAssuranceError(
                        f"mapped test module carries skip/xfail pytestmark: {path}"
                    )
    for name in required:
        function = functions.get(name)
        if function is None:
            continue
        decorators = {
            _resolved_expression_name(
                item.func if isinstance(item, ast.Call) else item,
                aliases,
            )
            for decorator in function.decorator_list
            for item in ast.walk(decorator)
            if isinstance(item, (ast.Call, ast.Attribute, ast.Name))
        }
        controls = {
            _resolved_expression_name(item, aliases)
            for item in ast.walk(function)
            if isinstance(item, (ast.Call, ast.Attribute, ast.Name))
        }
        raises = {
            _resolved_expression_name(
                item.exc.func if isinstance(item.exc, ast.Call) else item.exc,
                aliases,
            )
            for item in ast.walk(function)
            if isinstance(item, ast.Raise) and item.exc is not None
        }
        if (
            any(_is_skip_control(item) for item in decorators)
            or any(_is_skip_control(item) for item in controls)
            or any(_is_skip_control(item) for item in raises)
        ):
            raise ProductionQaAssuranceError(
                f"mapped test can skip or xfail: {path}:{name}"
            )
    if reviewed_path is not None:
        expected = REVIEWED_PYTEST_SOURCE_SHA256.get(reviewed_path)
        if expected is None or _source_sha256(path) != expected:
            raise ProductionQaAssuranceError(
                f"mapped test bytes differ from reviewed source: {reviewed_path}"
            )


def _verify_collection_controls(root: Path) -> None:
    """Bind and reject repository hooks that can silently deselect mapped tests."""

    for relative in (
        "pytest.ini",
        ".pytest.ini",
        "pytest.toml",
        ".pytest.toml",
        "tox.ini",
        "setup.cfg",
        "sitecustomize.py",
        "usercustomize.py",
    ):
        if (root / relative).exists():
            raise ProductionQaAssuranceError(
                f"unreviewed pytest or Python startup control is present: {relative}"
            )
    pytest_config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["pytest"]["ini_options"]
    if pytest_config != {
        "testpaths": ["packages", "apps", "benchmarks"],
        "python_files": "test_*.py",
        "addopts": "--import-mode=importlib",
        "pythonpath": ["."],
    }:
        raise ProductionQaAssuranceError(
            "pytest collection configuration drifted or can deselect mapped tests"
        )
    for relative in LOCAL_TEST_DISTRIBUTION_PYPROJECTS:
        try:
            package = tomllib.loads((root / relative).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ProductionQaAssuranceError(
                f"installed local package metadata is invalid: {relative}"
            ) from exc
        project = package.get("project")
        entry_points = project.get("entry-points") if isinstance(project, Mapping) else None
        if isinstance(entry_points, Mapping) and "pytest11" in entry_points:
            raise ProductionQaAssuranceError(
                f"installed local package declares an unreviewed pytest plugin: {relative}"
            )
        distribution_root = Path(relative).parent
        for startup_name in ("sitecustomize.py", "usercustomize.py"):
            startup = distribution_root / startup_name
            if (root / startup).exists():
                raise ProductionQaAssuranceError(
                    "editable-installed package exposes an unreviewed Python startup "
                    f"control: {startup.as_posix()}"
                )
    forbidden_functions = {
        "pytest_collection_modifyitems",
        "pytest_ignore_collect",
        "pytest_collect_file",
        "pytest_pycollect_makeitem",
        "pytest_collection_finish",
        "pytest_generate_tests",
        "pytest_fixture_setup",
        "pytest_runtest_protocol",
        "pytest_runtest_setup",
        "pytest_runtest_call",
        "pytest_runtest_teardown",
    }
    forbidden_assignments = {
        "collect_ignore",
        "collect_ignore_glob",
        "pytest_plugins",
    }
    ancestor_conftests = {"conftest.py"}
    for row in FUZZ_PROPERTY_MAPPINGS:
        for test in row["tests"]:
            parent = Path(test["path"]).parent
            while parent != Path("."):
                ancestor_conftests.add((parent / "conftest.py").as_posix())
                parent = parent.parent
    expected_conftests = {
        "conftest.py",
        "packages/maverick-core/tests/conftest.py",
    }
    actual_conftests = {
        relative
        for relative in ancestor_conftests
        if (root / relative).is_file()
    }
    if actual_conftests != expected_conftests:
        raise ProductionQaAssuranceError(
            "mapped tests have an unreviewed ancestor conftest: "
            f"missing={sorted(expected_conftests - actual_conftests)}, "
            f"extra={sorted(actual_conftests - expected_conftests)}"
        )
    forbidden_tokens = (
        forbidden_functions
        | forbidden_assignments
        | {
            "__test__",
            "applymarker",
            "importorskip",
            "skip",
            "skipif",
            "xfail",
            "SkipTest",
        }
    )
    for relative in sorted(expected_conftests):
        path = root / relative
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            raise ProductionQaAssuranceError(
                f"pytest collection control is invalid: {relative}"
            ) from exc
        aliases = _import_aliases(tree)
        names = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assigned = {
            target.id
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if isinstance(target, ast.Name)
        }
        calls = {
            _resolved_expression_name(node.func, aliases)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        raises = {
            _resolved_expression_name(
                node.exc.func if isinstance(node.exc, ast.Call) else node.exc,
                aliases,
            )
            for node in ast.walk(tree)
            if isinstance(node, ast.Raise) and node.exc is not None
        }
        dynamic_tokens = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in forbidden_tokens
        }
        if (
            names & forbidden_functions
            or assigned & (forbidden_assignments | forbidden_functions)
            or any(_is_skip_control(call) for call in calls)
            or any(_is_skip_control(raised) for raised in raises)
            or dynamic_tokens
        ):
            raise ProductionQaAssuranceError(
                "pytest collection/execution control can disable mapped tests: "
                f"{relative}"
            )
        expected_source = REVIEWED_PYTEST_SOURCE_SHA256.get(relative)
        if expected_source is None or _source_sha256(path) != expected_source:
            raise ProductionQaAssuranceError(
                f"pytest collection control differs from reviewed source: {relative}"
            )


def _verify_test_support_sources(root: Path) -> None:
    """Bind helper code that controls reviewed mapped-test execution."""

    for relative, expected in REVIEWED_TEST_SUPPORT_SOURCE_SHA256.items():
        if _source_sha256(root / relative) != expected:
            raise ProductionQaAssuranceError(
                f"mapped test support differs from reviewed source: {relative}"
            )


def _verify_fuzz_mappings(root: Path, value: object) -> tuple[int, int]:
    if value != list(FUZZ_PROPERTY_MAPPINGS):
        raise ProductionQaAssuranceError("fuzz/property mapping inventory drifted")
    _verify_collection_controls(root)
    _verify_test_support_sources(root)
    testpaths = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["pytest"]["ini_options"]["testpaths"]
    if testpaths != ["packages", "apps", "benchmarks"]:
        raise ProductionQaAssuranceError("pytest testpaths no longer collect mapped tests")
    test_count = 0
    symbol_count = 0
    for row in FUZZ_PROPERTY_MAPPINGS:
        ci = row["ci"]
        command = _workflow_step(
            root,
            workflow=ci["workflow"],
            job=ci["job"],
            step=ci["step"],
            require_unconditional=True,
            expected_job_needs=None,
        )
        if _command_sha256(command) != MAPPED_PYTEST_COMMAND_SHA256:
            raise ProductionQaAssuranceError(
                "mapped CI pytest command drifted from exact reviewed bytes"
            )
        if re.search(r"(?m)^\s*python\s+-m\s+pytest\b", _executable_text(command)) is None:
            raise ProductionQaAssuranceError(f"mapped CI step does not execute pytest: {row['id']}")
        _verify_full_job_semantics(
            root,
            workflow=ci["workflow"],
            job=ci["job"],
        )
        for kind in ("sources", "tests"):
            for binding in row[kind]:
                path = root / binding["path"]
                available = _symbols(path)
                missing = set(binding["symbols"]) - available
                if missing:
                    raise ProductionQaAssuranceError(
                        f"{row['id']} {kind} mapping is dead: {binding['path']} {sorted(missing)}"
                    )
                symbol_count += len(binding["symbols"])
                if kind == "tests":
                    _verify_mapped_tests_are_unskippable(
                        path,
                        set(binding["symbols"]),
                        reviewed_path=binding["path"],
                    )
                    if not binding["path"].startswith("packages/"):
                        raise ProductionQaAssuranceError(
                            f"mapped test is outside enforced pytest testpaths: {binding['path']}"
                        )
                    test_count += len(binding["symbols"])
    return len(FUZZ_PROPERTY_MAPPINGS), test_count


def _verify_supply_chain(root: Path, value: object) -> tuple[int, int]:
    if value != list(SUPPLY_CHAIN_CONTROLS):
        raise ProductionQaAssuranceError("supply-chain control inventory drifted")
    for row in SUPPLY_CHAIN_CONTROLS[1:]:
        command = _workflow_step(
            root,
            workflow=row["workflow"],
            job=row["job"],
            step=row["step"],
            expected_job_needs=SUPPLY_CHAIN_JOB_NEEDS[row["id"]],
            expected_job_condition=SUPPLY_CHAIN_JOB_CONDITIONS[row["id"]],
            expected_step_condition=None,
        )
        executable = _assert_no_statically_inert_shell(command, label=row["id"])
        if _command_sha256(command) != SUPPLY_CHAIN_COMMAND_SHA256[row["id"]]:
            raise ProductionQaAssuranceError(
                f"supply-chain command drifted from exact reviewed bytes: {row['id']}"
            )
        missing = [
            fragment
            for fragment in row["required_fragments"]
            if fragment not in executable
        ]
        if missing:
            raise ProductionQaAssuranceError(
                f"supply-chain control is dead or weakened: {row['id']} missing={missing}"
            )
        pattern = SUPPLY_CHAIN_COMMAND_PATTERNS[row["id"]]
        if re.search(pattern, executable) is None:
            raise ProductionQaAssuranceError(
                f"supply-chain control has no executable gate command: {row['id']}"
            )
        _verify_full_job_semantics(
            root,
            workflow=row["workflow"],
            job=row["job"],
        )
    pinned = _verify_action_pins(root)
    return len(SUPPLY_CHAIN_CONTROLS), pinned


def _verify_compliance(value: object) -> int:
    if value != list(COMPLIANCE_REGIMES):
        raise ProductionQaAssuranceError("compliance regime inventory drifted")
    for row in COMPLIANCE_REGIMES:
        if (
            row["claim_status"] != "draft-control-mapping-not-certification"
            or row["certified"] is not False
            or row["external_assessment_required"] is not True
            or not row["customer_responsibilities"]
        ):
            raise ProductionQaAssuranceError(
                f"compliance regime overclaims assurance: {row['id']}"
            )
    return len(COMPLIANCE_REGIMES)


def _verify_incident_processes(value: object) -> int:
    if value != list(INCIDENT_PROCESSES):
        raise ProductionQaAssuranceError("incident process inventory drifted")
    for row in INCIDENT_PROCESSES:
        if (
            row["status"] != "repository-process-only"
            or row["staffing_verified"] is not False
            or row["live_drill_required"] is not True
            or not row["customer_responsibilities"]
        ):
            raise ProductionQaAssuranceError(
                f"incident process overclaims operational evidence: {row['id']}"
            )
    return len(INCIDENT_PROCESSES)


def _verify_bindings(root: Path, value: object) -> None:
    if not isinstance(value, list) or len(value) != len(SOURCE_BINDING_PATHS):
        raise ProductionQaAssuranceError("QA source binding inventory is incomplete")
    actual: list[str] = []
    for index, raw in enumerate(value):
        row = _exact_mapping(
            raw,
            {"path", "sha256", "digest_mode"},
            label=f"source_bindings[{index}]",
        )
        path = row.get("path")
        digest = row.get("sha256")
        if (
            not isinstance(path, str)
            or path not in SOURCE_BINDING_PATHS
            or row.get("digest_mode") != "utf8-lf-sha256"
            or not isinstance(digest, str)
            or _DIGEST_RE.fullmatch(digest) is None
        ):
            raise ProductionQaAssuranceError(f"source_bindings[{index}] is malformed")
        actual.append(path)
        if _source_sha256(root / path) != digest:
            raise ProductionQaAssuranceError(f"QA source binding drifted: {path}")
    if tuple(actual) != SOURCE_BINDING_PATHS:
        raise ProductionQaAssuranceError("QA source bindings are not deterministic")


def build(root: Path) -> dict[str, Any]:
    root = root.resolve()
    surface = _read_json(root / SURFACE_PATH, label="production surface")
    release_state = surface.get("release_state")
    eligible = release_state.get("eligible") if isinstance(release_state, Mapping) else None
    return {
        "schema": SCHEMA,
        "repository": REPOSITORY,
        "profile_id": surface.get("profile_id"),
        "source_version": surface.get("source_version"),
        "release_eligible": eligible,
        "status": "repository-assurance-not-live-or-external-evidence",
        "gate_statuses": dict(GATE_STATUSES),
        "fuzz_property_mappings": list(FUZZ_PROPERTY_MAPPINGS),
        "supply_chain_controls": list(SUPPLY_CHAIN_CONTROLS),
        "compliance_regimes": list(COMPLIANCE_REGIMES),
        "incident_vulnerability_disclosure": list(INCIDENT_PROCESSES),
        "source_bindings": [
            {
                "path": path,
                "sha256": _source_sha256(root / path),
                "digest_mode": "utf8-lf-sha256",
            }
            for path in SOURCE_BINDING_PATHS
        ],
        "external_limitations": list(EXTERNAL_LIMITATIONS),
    }


def validate(root: Path, contract: Mapping[str, Any]) -> list[str]:
    root = root.resolve()
    _exact_mapping(
        contract,
        {
            "schema",
            "repository",
            "profile_id",
            "source_version",
            "release_eligible",
            "status",
            "gate_statuses",
            "fuzz_property_mappings",
            "supply_chain_controls",
            "compliance_regimes",
            "incident_vulnerability_disclosure",
            "source_bindings",
            "external_limitations",
        },
        label="production QA assurance contract",
    )
    surface = _read_json(root / SURFACE_PATH, label="production surface")
    release_state = surface.get("release_state")
    eligible = release_state.get("eligible") if isinstance(release_state, Mapping) else None
    if (
        contract.get("schema") != SCHEMA
        or contract.get("repository") != REPOSITORY
        or contract.get("profile_id") != surface.get("profile_id")
        or contract.get("source_version") != surface.get("source_version")
        or not isinstance(eligible, bool)
        or contract.get("release_eligible") is not eligible
        or contract.get("status") != "repository-assurance-not-live-or-external-evidence"
        or contract.get("gate_statuses") != GATE_STATUSES
    ):
        raise ProductionQaAssuranceError("QA assurance identity or gate boundary drifted")
    mappings, test_count = _verify_fuzz_mappings(
        root, contract.get("fuzz_property_mappings")
    )
    supply_controls, pinned_actions = _verify_supply_chain(
        root, contract.get("supply_chain_controls")
    )
    regimes = _verify_compliance(contract.get("compliance_regimes"))
    processes = _verify_incident_processes(
        contract.get("incident_vulnerability_disclosure")
    )
    _verify_bindings(root, contract.get("source_bindings"))
    limitations = contract.get("external_limitations")
    if limitations != list(EXTERNAL_LIMITATIONS):
        raise ProductionQaAssuranceError("QA external limitations drifted or weakened")
    return [
        f"verified {mappings} fuzz/property/state-machine mappings",
        f"verified {test_count} exact mapped test functions",
        f"verified {supply_controls} supply-chain controls and {pinned_actions} pinned actions",
        f"verified {regimes} non-certification compliance mappings",
        f"verified {processes} repository-only incident/vulnerability/disclosure processes",
        f"release_eligible={str(eligible).lower()}",
        "live_external_status=required-not-verified-by-this-tool",
    ]


def verify(root: Path) -> list[str]:
    root = root.resolve()
    return validate(
        root,
        _read_json(root / CONTRACT_PATH, label="production QA assurance contract"),
    )


def write(root: Path) -> list[str]:
    root = root.resolve()
    (root / CONTRACT_PATH).write_text(
        json.dumps(build(root), indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return verify(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    try:
        messages = write(args.root) if args.write else verify(args.root)
    except ProductionQaAssuranceError as exc:
        print(f"production QA assurance: FAIL: {exc}", file=sys.stderr)
        return 1
    for message in messages:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
