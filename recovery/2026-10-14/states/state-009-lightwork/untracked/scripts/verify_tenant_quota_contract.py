#!/usr/bin/env python3
"""Verify the fail-closed tenant quota contract for the first profile."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA = "lightwork.tenant-quota-contract.v1"
CONTRACT_PATH = Path("release/tenant-quota-contract.json")
SURFACE_PATH = Path("release/production-surface-manifest.json")
VERIFIER_PATH = Path("scripts/verify_tenant_quota_contract.py")
AUTHORITY_PATH = Path("packages/maverick-core/maverick/tenant_quota_authority.py")
WORLD_MODEL_PATH = Path("packages/maverick-core/maverick/world_model.py")
POSTGRES_PATH = Path(
    "packages/maverick-core/maverick/world_model_backends/postgres.py"
)
JOB_QUEUE_PATH = Path("packages/maverick-core/maverick/job_queue.py")
ORCHESTRATOR_PATH = Path("packages/maverick-core/maverick/orchestrator.py")
FLOW_EXECUTION_PATH = Path("packages/maverick-core/maverick/flow/execution.py")
HEALTH_PATH = Path(
    "packages/maverick-dashboard/maverick_dashboard/tenant_quota_health.py"
)
VALUES_PATH = Path("deploy/helm/maverick/values.yaml")
PRODUCTION_VALUES_PATH = Path("deploy/helm/maverick/values-production.yaml")
HELPERS_PATH = Path("deploy/helm/maverick/templates/_helpers.tpl")
DEPLOYMENT_PATH = Path("deploy/helm/maverick/templates/deployment.yaml")
HELM_README_PATH = Path("deploy/helm/README.md")
RUNBOOK_PATH = Path("docs/enterprise/tenant-quota-contract.md")

_TOP_KEYS = {
    "schema",
    "profile_id",
    "source_version",
    "deployment",
    "generation",
    "policy_sources",
    "authority",
    "resources",
    "unavailable_resources",
    "readiness",
    "qualification",
    "code_evidence",
    "source_bindings",
}
_DEPLOYMENT_KEYS = {
    "chart_path",
    "enforcement_env",
    "enforcement_value",
    "helm_values_root",
    "policy_source_rule",
    "production_required",
}
_GENERATION_KEYS = {
    "change_rule",
    "environment",
    "helm_value",
    "maximum",
    "minimum",
    "production_grammar",
    "same_generation_drift",
    "stale_generation",
}
_POLICY_SOURCE_KEYS = {"json", "scalars"}
_JSON_POLICY_KEYS = {
    "environment",
    "helm_value",
    "helm_environment_maximum_bytes",
    "helm_integer_maximum",
    "helm_numeric_values_required",
    "helm_spend_maximum",
    "maximum_depth",
    "maximum_nodes",
    "maximum_tenants",
    "production_top_level_fields",
    "requires_complete_default",
    "runtime_maximum_bytes",
    "runtime_schema",
    "strict_json",
}
_SCALAR_POLICY_KEYS = {
    "complete_set_required",
    "helm_values_root",
    "json_conflict",
}
_AUTHORITY_KEYS = {
    "backend",
    "fallback",
    "policy_fence_table",
    "reservation_table",
    "row_level_security",
    "same_transaction_admission",
    "maximum_reservation_ttl_seconds",
    "expiry_semantics",
    "settlement_semantics",
    "replay_semantics",
}
_RESOURCE_KEYS = {
    "id",
    "aggregation",
    "helm_value",
    "scalar_environment",
    "production_grammar",
    "value_type",
    "maximum",
    "coverage",
    "limitation",
}
_READINESS_KEYS = {
    "base_policy_checked_before_first_tenant",
    "complete_vector_required",
    "mutable_policy_overage",
    "policy_or_authority_error",
    "tenant_labels",
}
_QUALIFICATION_KEYS = {
    "repository_contract",
    "live_postgres_concurrency_and_failover",
    "target_cluster_helm_render_and_admission",
    "release_ready_claimed",
}

EXPECTED_DEPLOYMENT = {
    "chart_path": "deploy/helm/maverick",
    "enforcement_env": "MAVERICK_TENANT_QUOTA_ENFORCE",
    "enforcement_value": "1",
    "helm_values_root": "tenantQuota",
    "policy_source_rule": "exactly_one",
    "production_required": True,
}
EXPECTED_GENERATION = {
    "change_rule": "strictly_increase_for_every_policy_change",
    "environment": "MAVERICK_TENANT_QUOTA_POLICY_GENERATION",
    "helm_value": "tenantQuota.policyGeneration",
    "maximum": 999_999_999,
    "minimum": 1,
    "production_grammar": "^[1-9][0-9]{0,8}$",
    "same_generation_drift": "refuse",
    "stale_generation": "refuse",
}
EXPECTED_JSON_POLICY = {
    "environment": "MAVERICK_TENANT_QUOTA_LIMITS_JSON",
    "helm_value": "tenantQuota.limitsJSON",
    "helm_environment_maximum_bytes": 65_536,
    "helm_integer_maximum": 9_007_199_254_740_991,
    "helm_numeric_values_required": True,
    "helm_spend_maximum": 999_999_999.999999,
    "maximum_depth": 32,
    "maximum_nodes": 20_000,
    "maximum_tenants": 4096,
    "production_top_level_fields": ["default", "tenants"],
    "requires_complete_default": True,
    "runtime_maximum_bytes": 262_144,
    "runtime_schema": "lightwork.tenant-quota-policy.v2",
    "strict_json": True,
}
EXPECTED_SCALAR_POLICY = {
    "complete_set_required": True,
    "helm_values_root": "tenantQuota.limits",
    "json_conflict": "refuse",
}
EXPECTED_AUTHORITY = {
    "backend": "postgres",
    "fallback": "none",
    "policy_fence_table": "tenant_quota_usage",
    "reservation_table": "tenant_quota_reservations",
    "row_level_security": "forced",
    "same_transaction_admission": True,
    "maximum_reservation_ttl_seconds": 604_800,
    "expiry_semantics": (
        "charge_projected_cumulative_usage_once_and_release_occupancy"
    ),
    "settlement_semantics": "actual_must_not_exceed_projection",
    "replay_semantics": (
        "same_identity_returns_original_different_identity_refuses"
    ),
}
EXPECTED_RESOURCES = {
    "concurrent_runs": (
        "active_occupancy",
        "concurrentRuns",
        "MAVERICK_TENANT_QUOTA_MAX_CONCURRENT_RUNS",
        "^(0|[1-9][0-9]{0,5})$",
        "integer",
        999_999,
        "governed_goal_runs",
    ),
    "queued_jobs": (
        "active_durable_occupancy",
        "queuedJobs",
        "MAVERICK_TENANT_QUOTA_MAX_QUEUED_JOBS",
        "^(0|[1-9][0-9]{0,8})$",
        "integer",
        999_999_999,
        "durable_local_job_queue",
    ),
    "daily_tokens": (
        "utc_day_cumulative",
        "dailyTokens",
        "MAVERICK_TENANT_QUOTA_MAX_DAILY_TOKENS",
        "^(0|[1-9][0-9]{0,17})$",
        "integer",
        999_999_999_999_999_999,
        "governed_run_uncached_input_plus_output_tokens",
    ),
    "daily_spend_dollars": (
        "utc_day_cumulative",
        "dailySpendDollars",
        "MAVERICK_TENANT_QUOTA_MAX_DAILY_SPEND_DOLLARS",
        "^(0|[1-9][0-9]{0,8})([.][0-9]{1,6})?$",
        "decimal",
        999_999_999.999999,
        "governed_run_provider_spend",
    ),
    "tool_calls": (
        "utc_day_cumulative",
        "toolCalls",
        "MAVERICK_TENANT_QUOTA_MAX_TOOL_CALLS",
        "^(0|[1-9][0-9]{0,17})$",
        "integer",
        999_999_999_999_999_999,
        "governed_run_and_direct_flow_action_calls",
    ),
    "storage_bytes": (
        "cumulative",
        "storageBytes",
        "MAVERICK_TENANT_QUOTA_MAX_STORAGE_BYTES",
        "^(0|[1-9][0-9]{0,17})$",
        "integer",
        999_999_999_999_999_999,
        "structured_deliverable_artifact_utf8_bytes",
    ),
}
EXPECTED_UNAVAILABLE = [
    "connector_calls",
    "vector_store_bytes",
    "attachment_storage_bytes",
]
EXPECTED_READINESS = {
    "base_policy_checked_before_first_tenant": True,
    "complete_vector_required": True,
    "mutable_policy_overage": "not_ready",
    "policy_or_authority_error": "not_ready",
    "tenant_labels": "sha256_prefix_not_raw_identifier",
}
EXPECTED_QUALIFICATION = {
    "repository_contract": "implemented_and_locally_tested",
    "live_postgres_concurrency_and_failover": "required_before_release",
    "target_cluster_helm_render_and_admission": "required_before_release",
    "release_ready_claimed": False,
}
SOURCE_BINDING_PATHS = (
    AUTHORITY_PATH,
    WORLD_MODEL_PATH,
    POSTGRES_PATH,
    JOB_QUEUE_PATH,
    ORCHESTRATOR_PATH,
    FLOW_EXECUTION_PATH,
    HEALTH_PATH,
    VALUES_PATH,
    PRODUCTION_VALUES_PATH,
    HELPERS_PATH,
    DEPLOYMENT_PATH,
    VERIFIER_PATH,
)


class TenantQuotaContractError(ValueError):
    """The committed quota contract is incomplete or contradictory."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TenantQuotaContractError("JSON object has duplicate keys")
        result[key] = value
    return result


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TenantQuotaContractError(f"{label} must be an object")
    return value


def _exact(value: object, keys: set[str], label: str) -> Mapping[str, Any]:
    item = _mapping(value, label)
    if set(item) != keys:
        raise TenantQuotaContractError(f"{label} fields are not exact")
    return item


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise TenantQuotaContractError(f"{label} must be trimmed non-empty text")
    return value


def _json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
        if len(raw) > 1_048_576:
            raise TenantQuotaContractError(f"{label} exceeds 1 MiB")
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(
                TenantQuotaContractError(f"{label} contains {token}")
            ),
        )
    except TenantQuotaContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise TenantQuotaContractError(f"{label} is not valid UTF-8 JSON") from exc
    return _mapping(value, label)


def _read(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TenantQuotaContractError(f"{label} is unavailable") from exc


def _normalized_sha256(path: Path, label: str) -> str:
    """Hash reviewed UTF-8 source with platform newline differences removed."""

    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise TenantQuotaContractError(f"{label} is unavailable") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _literal_assignments(source: str) -> dict[str, object]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise TenantQuotaContractError("quota authority source is invalid") from exc
    values: dict[str, object] = {}

    def evaluate(node: ast.AST) -> object:
        try:
            return ast.literal_eval(node)
        except (ValueError, TypeError):
            pass
        if isinstance(node, ast.BinOp):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(left, int) and isinstance(right, int):
                if isinstance(node.op, ast.Mult):
                    return left * right
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.LShift):
                    return left << right
        raise ValueError("assignment is not a bounded literal")

    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        for target in targets:
            if isinstance(target, ast.Name) and value is not None:
                try:
                    values[target.id] = evaluate(value)
                except (ValueError, TypeError):
                    pass
    return values


def verify_tenant_quota_contract(  # noqa: C901 - exact cross-file contract
    root: Path,
) -> dict[str, int | str]:
    root = root.resolve()
    contract = _exact(
        _json(root / CONTRACT_PATH, "tenant quota contract"),
        _TOP_KEYS,
        "tenant quota contract",
    )
    if contract["schema"] != SCHEMA:
        raise TenantQuotaContractError("tenant quota schema is unsupported")
    surface = _json(root / SURFACE_PATH, "production surface")
    for field in ("profile_id", "source_version"):
        if _text(contract[field], field) != surface.get(field):
            raise TenantQuotaContractError(
                f"tenant quota {field} differs from the production surface"
            )

    deployment = _exact(contract["deployment"], _DEPLOYMENT_KEYS, "deployment")
    if deployment != EXPECTED_DEPLOYMENT:
        raise TenantQuotaContractError("deployment weakens quota enforcement")
    generation = _exact(contract["generation"], _GENERATION_KEYS, "generation")
    if generation != EXPECTED_GENERATION:
        raise TenantQuotaContractError("generation weakens the policy fence")
    policy_sources = _exact(
        contract["policy_sources"],
        _POLICY_SOURCE_KEYS,
        "policy sources",
    )
    json_policy = _exact(
        policy_sources["json"],
        _JSON_POLICY_KEYS,
        "JSON policy source",
    )
    scalar_policy = _exact(
        policy_sources["scalars"],
        _SCALAR_POLICY_KEYS,
        "scalar policy source",
    )
    if json_policy != EXPECTED_JSON_POLICY or scalar_policy != EXPECTED_SCALAR_POLICY:
        raise TenantQuotaContractError("policy-source semantics are not reviewed")
    authority = _exact(contract["authority"], _AUTHORITY_KEYS, "authority")
    if authority != EXPECTED_AUTHORITY:
        raise TenantQuotaContractError("authority semantics were weakened")

    rows = contract["resources"]
    if not isinstance(rows, list):
        raise TenantQuotaContractError("resource inventory must be an array")
    seen_ids: set[str] = set()
    seen_envs: set[str] = set()
    seen_helm: set[str] = set()
    for index, raw in enumerate(rows):
        row = _exact(raw, _RESOURCE_KEYS, f"resource row {index}")
        resource = _text(row["id"], f"resource row {index} id")
        expected = EXPECTED_RESOURCES.get(resource)
        if expected is None or resource in seen_ids:
            raise TenantQuotaContractError(
                f"unknown or duplicate quota resource: {resource}"
            )
        seen_ids.add(resource)
        actual = (
            row["aggregation"],
            row["helm_value"],
            row["scalar_environment"],
            row["production_grammar"],
            row["value_type"],
            row["maximum"],
            row["coverage"],
        )
        if actual != expected:
            raise TenantQuotaContractError(
                f"resource row {resource} differs from reviewed enforcement"
            )
        _text(row["limitation"], f"resource row {resource} limitation")
        if row["scalar_environment"] in seen_envs or row["helm_value"] in seen_helm:
            raise TenantQuotaContractError("quota resources must have unique wiring")
        seen_envs.add(row["scalar_environment"])
        seen_helm.add(row["helm_value"])
    if seen_ids != set(EXPECTED_RESOURCES):
        raise TenantQuotaContractError("quota resource inventory is incomplete")
    if contract["unavailable_resources"] != EXPECTED_UNAVAILABLE:
        raise TenantQuotaContractError("unavailable resource inventory is inaccurate")
    readiness = _exact(contract["readiness"], _READINESS_KEYS, "readiness")
    if readiness != EXPECTED_READINESS:
        raise TenantQuotaContractError("readiness semantics were weakened")
    qualification = _exact(
        contract["qualification"],
        _QUALIFICATION_KEYS,
        "qualification",
    )
    if qualification != EXPECTED_QUALIFICATION:
        raise TenantQuotaContractError("qualification overclaims production proof")

    source_bindings = _exact(
        contract["source_bindings"],
        {path.as_posix() for path in SOURCE_BINDING_PATHS},
        "source bindings",
    )
    for relative in SOURCE_BINDING_PATHS:
        expected_hash = source_bindings[relative.as_posix()]
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(character not in "0123456789abcdef" for character in expected_hash)
        ):
            raise TenantQuotaContractError(
                f"source binding is not a lowercase SHA-256: {relative.as_posix()}"
            )
        actual_hash = _normalized_sha256(
            root / relative,
            f"source binding {relative.as_posix()}",
        )
        if actual_hash != expected_hash:
            raise TenantQuotaContractError(
                f"source binding changed: {relative.as_posix()}"
            )

    evidence = contract["code_evidence"]
    if not isinstance(evidence, list) or not evidence or len(evidence) != len(set(evidence)):
        raise TenantQuotaContractError("code evidence is incomplete or duplicated")
    for raw_path in evidence:
        relative = Path(_text(raw_path, "code evidence path"))
        if relative.is_absolute() or ".." in relative.parts or not (root / relative).is_file():
            raise TenantQuotaContractError(f"code evidence is unavailable: {relative}")

    authority_text = _read(root / AUTHORITY_PATH, "quota authority")
    literals = _literal_assignments(authority_text)
    if tuple(literals.get("RESOURCE_NAMES", ())) != tuple(EXPECTED_RESOURCES):
        raise TenantQuotaContractError("runtime resource vector differs from contract")
    expected_envs = {
        name: details[2] for name, details in EXPECTED_RESOURCES.items()
    }
    if literals.get("_ENV_LIMITS") != expected_envs:
        raise TenantQuotaContractError("runtime scalar environment map differs")
    exact_runtime_limits = {
        "_MAX_POLICY_BYTES": EXPECTED_JSON_POLICY["runtime_maximum_bytes"],
        "_MAX_POLICY_DEPTH": EXPECTED_JSON_POLICY["maximum_depth"],
        "_MAX_POLICY_NODES": EXPECTED_JSON_POLICY["maximum_nodes"],
        "_MAX_POLICY_TENANTS": EXPECTED_JSON_POLICY["maximum_tenants"],
    }
    if any(literals.get(name) != value for name, value in exact_runtime_limits.items()):
        raise TenantQuotaContractError("runtime JSON bounds differ from contract")
    for marker in (
        "lightwork.tenant-quota-policy.v2",
        "validate_deployment_policy",
        "lacks base ceilings for",
        "actual usage exceeds reserved projection",
        "at most seven days",
    ):
        if marker not in authority_text:
            raise TenantQuotaContractError(
                f"quota authority lacks required control: {marker}"
            )

    postgres_text = _read(root / POSTGRES_PATH, "Postgres quota authority")
    for marker in (
        "tenant_quota_usage",
        "tenant_quota_reservations",
        "policy_generation",
        "policy_sha256",
        "FOR UPDATE",
        "tenant quota policy generation is stale",
        "tenant quota policy conflicts at this generation",
    ):
        if marker not in postgres_text:
            raise TenantQuotaContractError(
                f"Postgres quota authority lacks required control: {marker}"
            )
    health_text = _read(root / HEALTH_PATH, "quota readiness")
    preflight_position = health_text.find("validate_deployment_policy()")
    roster_position = health_text.find("_run_for_active_tenants(")
    if (
        preflight_position < 0
        or roster_position < 0
        or preflight_position > roster_position
    ):
        raise TenantQuotaContractError(
            "quota readiness does not validate the base policy before the roster"
        )
    for marker in ("over_limit", "sha256", "RESOURCE_NAMES"):
        if marker not in health_text:
            raise TenantQuotaContractError(
                f"quota readiness lacks required signal: {marker}"
            )

    values_text = _read(root / VALUES_PATH, "Helm values")
    production_values = _read(root / PRODUCTION_VALUES_PATH, "production values")
    helpers = _read(root / HELPERS_PATH, "Helm validation")
    deployment_text = _read(root / DEPLOYMENT_PATH, "Helm deployment")
    for document, label, markers in (
        (
            values_text,
            "Helm values",
            ("tenantQuota:", "enforce: false", 'policyGeneration: ""', 'limitsJSON: ""'),
        ),
        (
            production_values,
            "production values",
            ("tenantQuota:", "enforce: true", 'policyGeneration: ""', 'limitsJSON: ""'),
        ),
    ):
        for marker in markers:
            if marker not in document:
                raise TenantQuotaContractError(f"{label} lacks quota marker: {marker}")
        for helm_name in seen_helm:
            if f"{helm_name}: \"\"" not in document:
                raise TenantQuotaContractError(
                    f"{label} lacks quota value placeholder: {helm_name}"
                )
    helper_markers = (
        "tenantQuota.enforce=true",
        EXPECTED_GENERATION["production_grammar"],
        "exactly one tenant quota policy source",
        "mustFromJson",
        'kindIs "bool" .Values.tenantQuota.enforce',
        'kindIs "float64" $value',
        "65536",
        "9007199254740991.0",
        "999999999.999999",
        "4096",
        "exactly six resource ceilings",
        "all six scalar tenant quota ceilings",
        *[details[3] for details in EXPECTED_RESOURCES.values()],
    )
    for marker in helper_markers:
        if marker not in helpers:
            raise TenantQuotaContractError(
                f"Helm validation lacks quota control: {marker}"
            )
    deployment_markers = (
        "lightwork.daybreak.ai/tenant-quota-policy-generation",
        "name: MAVERICK_TENANT_QUOTA_ENFORCE",
        "name: MAVERICK_TENANT_QUOTA_POLICY_GENERATION",
        "name: MAVERICK_TENANT_QUOTA_LIMITS_JSON",
        *[f"name: {env}" for env in sorted(seen_envs)],
    )
    for marker in deployment_markers:
        if marker not in deployment_text:
            raise TenantQuotaContractError(
                f"Helm deployment lacks quota wiring: {marker}"
            )
    if ".Values.tenantQuota.limitsJSON" not in deployment_text:
        raise TenantQuotaContractError("Helm deployment does not select JSON policy mode")

    helm_readme = _read(root / HELM_README_PATH, "Helm runbook")
    runbook = _read(root / RUNBOOK_PATH, "tenant quota runbook")
    for label, document in (("Helm runbook", helm_readme), ("quota runbook", runbook)):
        for marker in (
            "tenantQuota.policyGeneration",
            "tenantQuota.limitsJSON",
            "MAVERICK_TENANT_QUOTA_MAX_STORAGE_BYTES",
            "live Postgres",
        ):
            if marker not in document:
                raise TenantQuotaContractError(f"{label} lacks required truth: {marker}")

    return {
        "schema": SCHEMA,
        "resources": len(rows),
        "scalar_environment_variables": len(seen_envs),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        summary = verify_tenant_quota_contract(args.root)
    except TenantQuotaContractError as exc:
        parser.exit(1, f"tenant quota contract refused: {exc}\n")
    print(
        f"verified {summary['resources']} tenant quota resources and "
        f"{summary['scalar_environment_variables']} scalar policy variables; "
        "live Postgres and target-cluster qualification remain required"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
