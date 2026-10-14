#!/usr/bin/env python3
"""Verify the fail-closed secret-rotation contract for the first profile."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA = "lightwork.secret-rotation-contract.v1"
CONTRACT_PATH = Path("release/secret-rotation-contract.json")
RUNTIME_PATH = Path("release/production-runtime-contract.json")
SURFACE_PATH = Path("release/production-surface-manifest.json")
DEPLOYMENT_PATH = Path("deploy/helm/maverick/templates/deployment.yaml")
HELPERS_PATH = Path("deploy/helm/maverick/templates/_helpers.tpl")
PRODUCTION_VALUES_PATH = Path("deploy/helm/maverick/values-production.yaml")
HELM_README_PATH = Path("deploy/helm/README.md")
PLAYBOOK_PATH = Path("docs/enterprise/deployment-playbook.md")

_TOP_KEYS = {
    "schema",
    "profile_id",
    "source_version",
    "deployment_contract",
    "required_phases",
    "secrets",
}
_DEPLOYMENT_KEYS = {
    "credential_values_in_helm",
    "external_drill_required",
    "generation_must_not_be_secret_derived",
    "old_material_revoked_only_after_validation",
    "planned_availability_interruption",
    "rollout_strategy",
    "rollout_trigger",
    "runtime_snapshot",
    "secret_source",
    "zero_downtime_claimed",
}
_ROW_KEYS = {
    "id",
    "names",
    "scope",
    "custody",
    "activation",
    "overlap",
    "rollback",
    "post_start_validation",
    "qualification",
    "code_evidence",
}
EXPECTED_PHASES = [
    "preflight_exact_scope",
    "prepare_new_provider_authority",
    "retain_old_generation_and_rollback",
    "stop_admission_and_drain",
    "publish_secret_and_bump_generation",
    "recreate_and_wait_for_readiness",
    "verify_new_authority_and_historical_access",
    "observe_and_reconcile",
    "revoke_old_authority",
    "remove_overlap_and_reverify",
]
EXPECTED_DEPLOYMENT = {
    "credential_values_in_helm": False,
    "external_drill_required": True,
    "generation_must_not_be_secret_derived": True,
    "old_material_revoked_only_after_validation": True,
    "planned_availability_interruption": True,
    "rollout_strategy": "Recreate",
    "rollout_trigger": "secret.rotationGeneration",
    "runtime_snapshot": "immutable_per_pod",
    "secret_source": "pre_provisioned_kubernetes_secret",
    "zero_downtime_claimed": False,
}
EXPECTED_ROWS = {
    "postgres-runtime-dsn": (
        ("MAVERICK_PG_DSN",),
        "required",
        "staged_file",
        "pod_recreate",
        "distinct_runtime_principal_or_managed_dual_password",
    ),
    "vault-auth-token": (
        ("VAULT_TOKEN",),
        "required",
        "staged_file",
        "pod_recreate",
        "two_live_tokens_or_auth_roles",
    ),
    "vault-wrapping-key": (
        ("MAVERICK_KMS_KEY_ID",),
        "required",
        "staged_file",
        "provider_then_pod_recreate",
        "old_key_decrypt_until_rewrap_restore_expiry",
    ),
    "vllm-gateway-token": (
        ("VLLM_API_KEY",),
        "required",
        "staged_file",
        "pod_recreate",
        "gateway_accepts_old_and_new",
    ),
    "audit-signing-key-wrapped": (
        ("MAVERICK_AUDIT_SIGNING_KEY_WRAPPED",),
        "required",
        "staged_file",
        "pod_recreate",
        "retain_all_historical_public_keys_and_old_wrapped_generation",
    ),
    "oidc-client-secret": (
        ("MAVERICK_OIDC_CLIENT_SECRET",),
        "oidc_only",
        "staged_file",
        "pod_recreate",
        "identity_provider_accepts_old_and_new",
    ),
    "oidc-session-secret": (
        ("MAVERICK_OIDC_SESSION_SECRET",),
        "oidc_only",
        "staged_file",
        "pod_recreate",
        "none_force_reauthentication",
    ),
    "metrics-scrape-bearer": (
        ("MAVERICK_METRICS_TOKEN", "MAVERICK_METRICS_TOKEN_PREVIOUS"),
        "required_with_optional_overlap",
        "kubernetes_secret_env",
        "pod_recreate",
        "application_current_previous_pair",
    ),
}


class SecretRotationContractError(ValueError):
    """The committed rotation contract is incomplete or contradictory."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SecretRotationContractError("JSON object has duplicate keys")
        result[key] = value
    return result


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SecretRotationContractError(f"{label} must be an object")
    return value


def _exact(value: object, keys: set[str], label: str) -> Mapping[str, Any]:
    item = _mapping(value, label)
    if set(item) != keys:
        raise SecretRotationContractError(f"{label} fields are not exact")
    return item


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SecretRotationContractError(f"{label} must be non-empty text")
    return value.strip()


def _json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(
                SecretRotationContractError(f"{label} contains {token}")
            ),
        )
    except SecretRotationContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise SecretRotationContractError(f"{label} is not valid UTF-8 JSON") from exc
    return _mapping(value, label)


def _read(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SecretRotationContractError(f"{label} is unavailable") from exc


def verify_secret_rotation_contract(  # noqa: C901 - exact cross-file contract
    root: Path,
) -> dict[str, int | str]:
    root = root.resolve()
    contract = _exact(
        _json(root / CONTRACT_PATH, "secret rotation contract"),
        _TOP_KEYS,
        "secret rotation contract",
    )
    if contract["schema"] != SCHEMA:
        raise SecretRotationContractError("secret rotation schema is unsupported")
    surface = _json(root / SURFACE_PATH, "production surface")
    for field in ("profile_id", "source_version"):
        if _text(contract[field], field) != surface.get(field):
            raise SecretRotationContractError(f"secret rotation {field} differs from the surface")

    deployment = _exact(
        contract["deployment_contract"],
        _DEPLOYMENT_KEYS,
        "deployment contract",
    )
    if deployment != EXPECTED_DEPLOYMENT:
        raise SecretRotationContractError("deployment contract weakens reviewed rotation semantics")
    if contract["required_phases"] != EXPECTED_PHASES:
        raise SecretRotationContractError("rotation phases are incomplete or out of order")

    rows = contract["secrets"]
    if not isinstance(rows, list):
        raise SecretRotationContractError("secret inventory must be an array")
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    staged_names: set[str] = set()
    for index, raw in enumerate(rows):
        row = _exact(raw, _ROW_KEYS, f"secret row {index}")
        row_id = _text(row["id"], f"secret row {index} id")
        expected = EXPECTED_ROWS.get(row_id)
        if expected is None or row_id in seen_ids:
            raise SecretRotationContractError(f"unknown or duplicate secret row: {row_id}")
        seen_ids.add(row_id)
        names = row["names"]
        if not isinstance(names, list) or tuple(names) != expected[0]:
            raise SecretRotationContractError(f"secret row {row_id} names differ from the reviewed inventory")
        if any(not isinstance(name, str) or not name for name in names):
            raise SecretRotationContractError(f"secret row {row_id} contains an invalid name")
        if seen_names.intersection(names):
            raise SecretRotationContractError("secret names must have one rotation owner")
        seen_names.update(names)
        actual_semantics = (
            row["scope"],
            row["custody"],
            row["activation"],
            row["overlap"],
        )
        if actual_semantics != expected[1:]:
            raise SecretRotationContractError(f"secret row {row_id} weakens reviewed semantics")
        if row["qualification"] != "live_drill_required":
            raise SecretRotationContractError(f"secret row {row_id} overclaims qualification")
        _text(row["rollback"], f"secret row {row_id} rollback")
        _text(row["post_start_validation"], f"secret row {row_id} validation")
        evidence = row["code_evidence"]
        if not isinstance(evidence, list) or not evidence or len(evidence) != len(set(evidence)):
            raise SecretRotationContractError(f"secret row {row_id} evidence is incomplete")
        for raw_path in evidence:
            relative = Path(_text(raw_path, f"secret row {row_id} evidence"))
            if relative.is_absolute() or ".." in relative.parts or not (root / relative).is_file():
                raise SecretRotationContractError(f"secret row {row_id} evidence is unavailable")
        if row["custody"] == "staged_file":
            staged_names.update(names)
    if seen_ids != set(EXPECTED_ROWS):
        raise SecretRotationContractError("secret rotation inventory is incomplete")

    runtime = _json(root / RUNTIME_PATH, "production runtime contract")
    filesystem = _mapping(runtime.get("filesystem"), "runtime filesystem")
    projected = set(filesystem.get("projected_secret_files", []))
    oidc = set(filesystem.get("oidc_secret_files", []))
    file_only = set(filesystem.get("file_only_secret_environment", []))
    if staged_names != projected | oidc or staged_names != file_only:
        raise SecretRotationContractError("staged rotation inventory differs from runtime custody")

    deployment_text = _read(root / DEPLOYMENT_PATH, "Helm deployment")
    helpers_text = _read(root / HELPERS_PATH, "Helm validation")
    values_text = _read(root / PRODUCTION_VALUES_PATH, "production values")
    helm_readme = _read(root / HELM_README_PATH, "Helm runbook")
    playbook = _read(root / PLAYBOOK_PATH, "deployment playbook")
    for required in (
        "type: Recreate",
        "stage-runtime-secrets",
        "lightwork.daybreak.ai/secret-rotation-generation",
        ".Values.secret.rotationGeneration",
        "name: MAVERICK_METRICS_TOKEN",
        "name: MAVERICK_METRICS_TOKEN_PREVIOUS",
        "secretKeyRef:",
    ):
        if required not in deployment_text:
            raise SecretRotationContractError(f"Helm deployment lacks rotation control: {required}")
    if "secret.rotationGeneration" not in helpers_text or "regexMatch" not in helpers_text:
        raise SecretRotationContractError("Helm validation does not require a bounded rotation generation")
    if 'rotationGeneration: ""' not in values_text or "create: false" not in values_text:
        raise SecretRotationContractError("production values no longer require external secret inputs")
    if "MAVERICK_AUDIT_SIGNING_KEY\n" in deployment_text:
        raise SecretRotationContractError("raw audit private key entered the production projection")
    for label, document in (("Helm runbook", helm_readme), ("deployment playbook", playbook)):
        if "secret.rotationGeneration" not in document:
            raise SecretRotationContractError(f"{label} omits the required rotation trigger")
        if "--from-literal=MAVERICK_AUDIT_SIGNING_KEY=" in document:
            raise SecretRotationContractError(f"{label} provisions a raw audit private key")
    return {"schema": SCHEMA, "secret_rows": len(rows), "staged_files": len(staged_names)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        summary = verify_secret_rotation_contract(args.root)
    except SecretRotationContractError as exc:
        parser.exit(1, f"secret rotation contract refused: {exc}\n")
    print(
        f"verified {summary['secret_rows']} secret rotation rows "
        f"({summary['staged_files']} staged files); live drills remain required"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
