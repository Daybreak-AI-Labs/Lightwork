#!/usr/bin/env python3
"""Verify the first-profile data inventory against source-owned boundaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = Path("release/production-data-boundary.json")
SURFACE = Path("release/production-surface-manifest.json")

TOP_KEYS = {
    "schema",
    "profile_id",
    "source_version",
    "status",
    "principles",
    "classifications",
    "processors",
    "data_flows",
    "excluded_flows",
    "source_bindings",
    "live_qualification_requirements",
}
FLOW_KEYS = {
    "id",
    "classification",
    "purpose",
    "elements",
    "ingress",
    "processors",
    "storage",
    "egress",
    "tenant_boundary",
    "retention",
    "encryption_in_transit",
    "encryption_at_rest",
    "access",
    "deletion",
    "code_evidence",
    "live_qualification",
}
FLOW_IDS = {
    "identity-and-session",
    "goals-conversations-and-results",
    "governed-flow-state",
    "signed-audit-approvals-and-receipts",
    "operational-telemetry",
    "quota-cost-and-capacity",
    "secrets-and-key-material",
    "read-only-workspace-source",
    "authorized-support-and-evidence-artifacts",
}
EXCLUDED = {
    "connectors",
    "channels",
    "third-party plugins",
    "attachments",
    "knowledge/RAG",
    "notification egress",
    "external telemetry export from the application",
    "arbitrary command execution",
    "hosted-cloud model providers",
    "automated retention",
}
SEMANTIC_SHA256 = "4649b1e1571c30f10a8d9523a271e39b4fca0c06bb622460723e8fec3bba8c04"
SOURCE_BINDING_PATHS = (
    "packages/maverick-core/maverick/audit/evidence.py",
    "packages/maverick-core/maverick/audit/signing.py",
    "packages/maverick-core/maverick/audit/writer.py",
    "packages/maverick-core/maverick/audit_evidence_verify.py",
    "packages/maverick-core/maverick/flow/approvals.py",
    "packages/maverick-core/maverick/flow/execution.py",
    "packages/maverick-core/maverick/flow/postgres_authority.py",
    "packages/maverick-core/maverick/kms_backends.py",
    "packages/maverick-core/maverick/logging_config.py",
    "packages/maverick-core/maverick/orchestrator.py",
    "packages/maverick-core/maverick/pricing.py",
    "packages/maverick-core/maverick/production_observability.py",
    "packages/maverick-core/maverick/providers/vllm_provider.py",
    "packages/maverick-core/maverick/secret_provider.py",
    "packages/maverick-core/maverick/support_bundle.py",
    "packages/maverick-core/maverick/tenant_quota_authority.py",
    "packages/maverick-core/maverick/tools/fs.py",
    "packages/maverick-core/maverick/world_model_backends/pg_rls.py",
    "packages/maverick-core/maverick/world_model_backends/postgres.py",
    "packages/maverick-dashboard/maverick_dashboard/auth.py",
    "packages/maverick-dashboard/maverick_dashboard/auth_metrics.py",
    "packages/maverick-dashboard/maverick_dashboard/health_routes.py",
    "packages/maverick-dashboard/maverick_dashboard/oidc_login.py",
    "packages/maverick-dashboard/maverick_dashboard/service_credentials.py",
    "packages/maverick-dashboard/maverick_dashboard/tenant_quota_health.py",
)
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")


class DataBoundaryError(RuntimeError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _path_label(path: Path, *, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _load(path: Path, *, root: Path) -> dict[str, Any]:
    label = _path_label(path, root=root)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise DataBoundaryError(f"{label} is not a regular file")
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except DataBoundaryError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise DataBoundaryError(f"cannot read {label}") from exc
    if not isinstance(value, dict):
        raise DataBoundaryError(f"{label} must be an object")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _semantic_sha256(contract: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in contract.items()
        if key not in {"profile_id", "source_version", "source_bindings"}
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _source_sha256(path: Path, *, root: Path) -> str:
    resolved = path.resolve()
    if (
        root not in resolved.parents
        or path.is_symlink()
        or not resolved.is_file()
        or resolved.stat().st_size <= 0
    ):
        raise DataBoundaryError(f"source binding is missing or unsafe: {path}")
    try:
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DataBoundaryError(f"source binding is not UTF-8: {path}") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _verify_source_bindings(root: Path, value: object) -> None:
    if not isinstance(value, list) or len(value) != len(SOURCE_BINDING_PATHS):
        raise DataBoundaryError("data source-binding inventory is incomplete")
    actual: list[str] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != {
            "path",
            "sha256",
            "digest_mode",
        }:
            raise DataBoundaryError(f"source_bindings[{index}] schema drift")
        path = raw.get("path")
        digest = raw.get("sha256")
        if (
            not isinstance(path, str)
            or path not in SOURCE_BINDING_PATHS
            or raw.get("digest_mode") != "utf8-lf-sha256"
            or not isinstance(digest, str)
            or _DIGEST_RE.fullmatch(digest) is None
        ):
            raise DataBoundaryError(f"source_bindings[{index}] is malformed")
        actual.append(path)
        if _source_sha256(root / path, root=root) != digest:
            raise DataBoundaryError(f"data source binding drifted: {path}")
    if tuple(actual) != SOURCE_BINDING_PATHS:
        raise DataBoundaryError("data source bindings are not in reviewed order")


def _nonempty_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataBoundaryError(f"{label} must be non-empty text")
    return value


def _text_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise DataBoundaryError(f"{label} must be a non-empty list")
    result = [_nonempty_text(item, label) for item in value]
    if len(result) != len(set(result)):
        raise DataBoundaryError(f"{label} contains duplicates")
    return result


def verify(  # noqa: C901 - exact cross-file data contract
    root: Path = ROOT,
) -> dict[str, object]:
    root = root.resolve()
    contract = _load(root / CONTRACT, root=root)
    surface = _load(root / SURFACE, root=root)
    if set(contract) != TOP_KEYS:
        raise DataBoundaryError("data-boundary top-level schema drift")
    if contract["schema"] != "lightwork.production-data-boundary.v1":
        raise DataBoundaryError("unsupported data-boundary schema")
    if contract["status"] != "source_contract_complete_live_qualification_required":
        raise DataBoundaryError("data boundary must not assert live qualification")
    for key in ("profile_id", "source_version"):
        if contract[key] != surface.get(key):
            raise DataBoundaryError(f"data-boundary {key} does not match surface")
    _text_list(contract["principles"], "principles")
    _text_list(contract["live_qualification_requirements"], "live requirements")

    classifications = contract["classifications"]
    if not isinstance(classifications, dict) or not classifications:
        raise DataBoundaryError("classifications must be a non-empty object")
    for name, definition in classifications.items():
        _nonempty_text(name, "classification id")
        _nonempty_text(definition, f"classification {name}")

    processors = contract["processors"]
    if not isinstance(processors, list) or not processors:
        raise DataBoundaryError("processors must be a non-empty list")
    processor_ids: set[str] = set()
    for row in processors:
        if not isinstance(row, dict) or set(row) != {
            "id",
            "role",
            "provider",
            "region",
            "live_approval_required",
        }:
            raise DataBoundaryError("processor schema drift")
        processor_id = _nonempty_text(row["id"], "processor id")
        if processor_id in processor_ids or row["live_approval_required"] is not True:
            raise DataBoundaryError("processor identity/approval contract invalid")
        processor_ids.add(processor_id)
        for field in ("role", "provider", "region"):
            _nonempty_text(row[field], f"processor {processor_id} {field}")

    flows = contract["data_flows"]
    if not isinstance(flows, list):
        raise DataBoundaryError("data_flows must be a list")
    found_ids: set[str] = set()
    bound_evidence_paths: set[str] = set()
    evidence_count = 0
    for flow in flows:
        if not isinstance(flow, dict) or set(flow) != FLOW_KEYS:
            raise DataBoundaryError("data-flow schema drift")
        flow_id = _nonempty_text(flow["id"], "flow id")
        if flow_id in found_ids:
            raise DataBoundaryError(f"duplicate data flow {flow_id}")
        found_ids.add(flow_id)
        if flow["classification"] not in classifications:
            raise DataBoundaryError(f"{flow_id} uses unknown classification")
        for field in (
            "purpose",
            "tenant_boundary",
            "retention",
            "encryption_in_transit",
            "encryption_at_rest",
            "deletion",
        ):
            _nonempty_text(flow[field], f"{flow_id}.{field}")
        for field in (
            "elements",
            "ingress",
            "processors",
            "storage",
            "egress",
            "access",
            "code_evidence",
            "live_qualification",
        ):
            values = _text_list(flow[field], f"{flow_id}.{field}")
            if field == "processors" and not set(values).issubset(processor_ids):
                raise DataBoundaryError(f"{flow_id} names an unknown processor")
            if field == "code_evidence":
                for item in values:
                    target = (root / item).resolve()
                    if root not in target.parents or not target.is_file():
                        raise DataBoundaryError(f"missing/unsafe code evidence: {item}")
                    if not item.startswith("release/"):
                        bound_evidence_paths.add(item)
                    evidence_count += 1
    if found_ids != FLOW_IDS:
        raise DataBoundaryError("data-flow inventory is incomplete or expanded")
    if set(contract["excluded_flows"]) != EXCLUDED:
        raise DataBoundaryError("excluded data-flow inventory drift")
    if bound_evidence_paths != set(SOURCE_BINDING_PATHS):
        raise DataBoundaryError("data-flow evidence is not fully source bound")
    if _semantic_sha256(contract) != SEMANTIC_SHA256:
        raise DataBoundaryError(
            "data-boundary reviewed semantics drifted; encryption, retention, "
            "evidence, and live-qualification statements require verifier review"
        )
    _verify_source_bindings(root, contract["source_bindings"])

    runtime = _load(
        root / "release/production-runtime-contract.json",
        root=root,
    )
    if (
        runtime.get("schema") != "lightwork.production-runtime-contract.v1"
        or runtime.get("profile_id") != contract["profile_id"]
    ):
        raise DataBoundaryError("runtime evidence targets a different production profile")

    constraints = surface.get("constraints")
    if not isinstance(constraints, dict):
        raise DataBoundaryError("surface constraints are unavailable")
    source_exclusions = {
        "connectors": constraints.get("connectors") == [],
        "channels": constraints.get("channels") == [],
        "third-party plugins": constraints.get("plugins") == [],
        "attachments": constraints.get("attachments_enabled") is False,
        "knowledge/RAG": constraints.get("knowledge_enabled") is False,
        "arbitrary command execution": constraints.get("sandbox_backends") == ["disabled"],
        "hosted-cloud model providers": constraints.get("model_providers") == ["vllm"],
    }
    failed = sorted(name for name, ok in source_exclusions.items() if not ok)
    if failed:
        raise DataBoundaryError("manifest no longer proves exclusions: " + ", ".join(failed))
    surfaces = {row.get("id"): row for row in surface.get("surfaces", []) if isinstance(row, dict)}
    for identifier, exclusion in {
        "feature:notification-egress": "notification egress",
        "feature:external-telemetry-export": "external telemetry export from the application",
        "feature:automated-retention": "automated retention",
    }.items():
        row = surfaces.get(identifier)
        if not row or row.get("included") is not False or row.get("status") != "unsupported":
            raise DataBoundaryError(f"manifest no longer proves exclusion: {exclusion}")
    return {
        "schema": contract["schema"],
        "profile_id": contract["profile_id"],
        "source_version": contract["source_version"],
        "status": contract["status"],
        "data_flows": len(found_ids),
        "processors": len(processor_ids),
        "code_evidence_paths": evidence_count,
        "source_bindings": len(SOURCE_BINDING_PATHS),
        "live_qualification_required": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        result = verify(args.root)
    except DataBoundaryError as exc:
        print(f"production-data-boundary: REFUSED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
