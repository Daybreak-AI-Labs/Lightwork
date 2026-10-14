#!/usr/bin/env python3
"""Verify the exact dependency-failure contract for the first profile."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA = "lightwork.dependency-failure-contract.v1"
CONTRACT_PATH = Path("release/dependency-failure-contract.json")
SURFACE_PATH = Path("release/production-surface-manifest.json")
DEPENDENCY_IDS = frozenset(
    {
        "audit-anchor",
        "audit-storage",
        "connectors",
        "dns",
        "identity-provider",
        "ingress-tls",
        "object-store",
        "postgres",
        "siem",
        "state-pvc",
        "vault-kms",
        "vector-store",
        "vllm",
    }
)
_TOP_KEYS = {"schema", "profile_id", "source_version", "response_contract", "dependencies"}
_RESPONSE_KEYS = {
    "external_drills_required",
    "identity_reachability_probed_by_readiness",
    "liveness_is_dependency_independent",
    "provider_reachability_probed_by_readiness",
    "readiness_failure_http_status",
}
_DEPENDENCY_KEYS = {
    "id",
    "profile_scope",
    "startup",
    "readiness",
    "retry_contract",
    "in_flight",
    "operator_response",
    "qualification",
    "code_evidence",
}
_SCOPES = {"required", "one_of_required", "optional_operational", "excluded"}
_STARTUP = {"fail_closed", "configuration_only", "not_probed", "not_applicable"}
_READINESS = {"fail_closed", "backlog_fail_closed", "not_probed", "not_applicable"}
_RETRY = {
    "idempotent_retry_after_recovery",
    "external_failover_only",
    "no_automatic_retry",
    "not_applicable",
}


class DependencyFailureContractError(ValueError):
    """The committed dependency contract is incomplete or contradictory."""


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DependencyFailureContractError(f"{label} must be an object")
    return value


def _exact(value: object, keys: set[str], label: str) -> Mapping[str, Any]:
    item = _mapping(value, label)
    if set(item) != keys:
        raise DependencyFailureContractError(f"{label} fields are not exact")
    return item


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DependencyFailureContractError(f"{label} must be non-empty")
    return value.strip()


def _json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), label)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DependencyFailureContractError(f"{label} is not valid UTF-8 JSON") from exc


def verify_dependency_failure_contract(  # noqa: C901 - exact contract cross-check
    root: Path,
) -> dict[str, int | str]:
    root = root.resolve()
    contract = _exact(_json(root / CONTRACT_PATH, "dependency contract"), _TOP_KEYS, "dependency contract")
    if contract["schema"] != SCHEMA:
        raise DependencyFailureContractError("dependency contract schema is unsupported")
    surface = _json(root / SURFACE_PATH, "production surface")
    for field in ("profile_id", "source_version"):
        if _text(contract[field], f"dependency contract {field}") != surface.get(field):
            raise DependencyFailureContractError(f"dependency contract {field} differs from production surface")

    response = _exact(contract["response_contract"], _RESPONSE_KEYS, "response contract")
    if response != {
        "external_drills_required": True,
        "identity_reachability_probed_by_readiness": False,
        "liveness_is_dependency_independent": True,
        "provider_reachability_probed_by_readiness": False,
        "readiness_failure_http_status": 503,
    }:
        raise DependencyFailureContractError("response contract weakens the reviewed failure semantics")

    rows = contract["dependencies"]
    if not isinstance(rows, list):
        raise DependencyFailureContractError("dependency inventory must be an array")
    seen: set[str] = set()
    excluded = 0
    for index, raw in enumerate(rows):
        item = _exact(raw, _DEPENDENCY_KEYS, f"dependency row {index}")
        dependency_id = _text(item["id"], f"dependency row {index} id")
        if dependency_id not in DEPENDENCY_IDS or dependency_id in seen:
            raise DependencyFailureContractError(f"unknown or duplicate dependency: {dependency_id}")
        seen.add(dependency_id)
        scope = item["profile_scope"]
        if scope not in _SCOPES or item["startup"] not in _STARTUP:
            raise DependencyFailureContractError(f"dependency {dependency_id} has invalid scope/startup")
        if item["readiness"] not in _READINESS or item["retry_contract"] not in _RETRY:
            raise DependencyFailureContractError(f"dependency {dependency_id} has invalid readiness/retry")
        _text(item["in_flight"], f"dependency {dependency_id} in_flight")
        _text(item["operator_response"], f"dependency {dependency_id} operator_response")
        evidence = item["code_evidence"]
        if not isinstance(evidence, list) or not evidence:
            raise DependencyFailureContractError(f"dependency {dependency_id} has no code evidence")
        for value in evidence:
            relative = Path(_text(value, f"dependency {dependency_id} evidence"))
            if relative.is_absolute() or ".." in relative.parts or not (root / relative).is_file():
                raise DependencyFailureContractError(f"dependency {dependency_id} evidence is unavailable")
        if len(evidence) != len(set(evidence)):
            raise DependencyFailureContractError(f"dependency {dependency_id} evidence is duplicated")
        if scope == "excluded":
            excluded += 1
            if (
                item["startup"] != "not_applicable"
                or item["readiness"] != "not_applicable"
                or item["retry_contract"] != "not_applicable"
                or item["qualification"] != "excluded"
            ):
                raise DependencyFailureContractError(f"excluded dependency {dependency_id} claims runtime behavior")
        elif item["qualification"] != "live_drill_required":
            raise DependencyFailureContractError(f"dependency {dependency_id} overclaims qualification")
    if seen != DEPENDENCY_IDS:
        raise DependencyFailureContractError(f"dependency inventory is incomplete: {sorted(DEPENDENCY_IDS - seen)}")

    constraints = _mapping(surface.get("constraints"), "production constraints")
    if constraints.get("world_model_backend") != "postgres":
        raise DependencyFailureContractError("Postgres dependency no longer matches the profile")
    if constraints.get("kms_providers") != ["vault"] or constraints.get("model_providers") != ["vllm"]:
        raise DependencyFailureContractError("KMS/model dependencies no longer match the profile")
    if constraints.get("connectors") != [] or constraints.get("knowledge_enabled") is not False:
        raise DependencyFailureContractError("excluded dependency scope no longer matches the profile")
    return {"dependencies": len(rows), "excluded": excluded, "schema": SCHEMA}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        summary = verify_dependency_failure_contract(args.root)
    except DependencyFailureContractError as exc:
        parser.exit(1, f"dependency failure contract refused: {exc}\n")
    print(
        f"verified {summary['dependencies']} dependency rows "
        f"({summary['excluded']} excluded); live drills remain required"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
