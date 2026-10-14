#!/usr/bin/env python3
"""Generate and verify the exact first-profile production API contract.

The dashboard package retains a compact startup artifact so every production
process can refuse schema drift before serving traffic.  A release needs more:
the complete narrowed OpenAPI document, exact lifecycle metadata, and evidence
that those bytes still come from the reviewed source profile.  This verifier
binds all three views without treating an unreleased source tree as a supported
customer API.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA = "lightwork.production-api-contract.v1"
REPOSITORY = "Daybreak-AI-Labs/Lightwork"
CONTRACT_PATH = Path("release/production-api-contract.json")
OPENAPI_PATH = Path("release/production-openapi-schema.json")
PACKAGED_PATH = Path(
    "packages/maverick-dashboard/maverick_dashboard/"
    "contracts/openapi-production-v1.json"
)
SURFACE_PATH = Path("release/production-surface-manifest.json")
SOURCE_BINDING_PATHS = (
    "docs/api.md",
    "packages/maverick-dashboard/maverick_dashboard/app.py",
    "packages/maverick-dashboard/maverick_dashboard/endpoint_policy.py",
    "packages/maverick-dashboard/maverick_dashboard/openapi_contract.py",
    "packages/maverick-dashboard/maverick_dashboard/production_visibility.py",
)
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_HTTP_METHODS = frozenset(
    {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
)
_CONTRACT_KEYS = {
    "schema",
    "repository",
    "profile_id",
    "source_version",
    "release_eligible",
    "contract_version",
    "lifecycle_status",
    "api_version",
    "openapi_version",
    "operation_count",
    "canonical_schema_sha256",
    "schema_artifact",
    "schema_artifact_sha256",
    "packaged_runtime_contract",
    "packaged_runtime_contract_sha256",
    "compatibility_policy",
    "deprecation_policy",
    "migration_guide",
    "startup_gate",
    "source_bindings",
    "release_boundary",
}
_STARTUP_KEYS = {
    "source",
    "lifespan_symbol",
    "validator_symbol",
    "failure_semantics",
}
_RELEASE_BOUNDARY_KEYS = {
    "compatibility_commitment_active",
    "clock_starts",
    "external_release_requirements",
}


class ProductionApiContractError(ValueError):
    """The production API artifact is malformed, stale, or overclaims support."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise ProductionApiContractError(
                f"{label} is missing or not a non-empty regular file"
            )
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except ProductionApiContractError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProductionApiContractError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProductionApiContractError(f"{label} must contain one JSON object")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_sha256(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ProductionApiContractError(
            f"source binding is not readable UTF-8: {path}"
        ) from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _exact_mapping(
    value: object,
    expected: set[str],
    *,
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionApiContractError(f"{label} must be an object")
    if set(value) != expected:
        raise ProductionApiContractError(
            f"{label} fields differ: missing={sorted(expected - set(value))}, "
            f"unknown={sorted(set(value) - expected)}"
        )
    return value


def _current_schema(root: Path) -> dict[str, Any]:
    """Generate in an isolated child so ambient editable installs cannot win."""

    package_paths = [
        root / "packages" / "maverick-core",
        root / "packages" / "maverick-dashboard",
        root / "packages" / "maverick-mcp",
        root / "packages" / "maverick-channels",
    ]
    if any(not path.is_dir() for path in package_paths):
        raise ProductionApiContractError(
            "checkout package paths required for OpenAPI generation are missing"
        )
    script = """
import json
import sys
for value in json.loads(sys.argv[1]):
    sys.path.insert(0, value)
from maverick_dashboard.app import app
from maverick_dashboard.openapi_contract import production_openapi_schema
print(json.dumps(
    production_openapi_schema(app.openapi()),
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=True,
    allow_nan=False,
))
"""
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("MAVERICK_"):
            environment.pop(name, None)
    try:
        with tempfile.TemporaryDirectory(prefix="lightwork-openapi-") as home:
            environment["MAVERICK_HOME"] = home
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    script,
                    json.dumps([str(path.resolve()) for path in package_paths]),
                ],
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProductionApiContractError(
            "could not generate the checkout OpenAPI schema"
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ProductionApiContractError(
            f"checkout OpenAPI generation failed: {detail[:1000]}"
        )
    try:
        value = json.loads(
            result.stdout,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProductionApiContractError(
            "checkout OpenAPI generator did not emit strict JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ProductionApiContractError("checkout OpenAPI schema is not an object")
    return value


def _operation_inventory(schema: Mapping[str, Any]) -> tuple[int, set[str]]:
    paths = schema.get("paths")
    if not isinstance(paths, Mapping) or not paths:
        raise ProductionApiContractError("OpenAPI schema has no paths")
    count = 0
    operation_ids: set[str] = set()
    for path, path_item in paths.items():
        if not isinstance(path, str) or not path.startswith("/"):
            raise ProductionApiContractError("OpenAPI path key is invalid")
        if not isinstance(path_item, Mapping):
            raise ProductionApiContractError(f"OpenAPI path {path!r} is not an object")
        for method, operation in path_item.items():
            if str(method).lower() not in _HTTP_METHODS:
                continue
            if not isinstance(operation, Mapping):
                raise ProductionApiContractError(
                    f"OpenAPI operation {method} {path} is not an object"
                )
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id.strip():
                raise ProductionApiContractError(
                    f"OpenAPI operation {method} {path} has no operationId"
                )
            if operation_id in operation_ids:
                raise ProductionApiContractError(
                    f"duplicate OpenAPI operationId: {operation_id}"
                )
            operation_ids.add(operation_id)
            count += 1
    return count, operation_ids


def _validate_refs(schema: Mapping[str, Any]) -> None:
    components = schema.get("components")
    schemas = components.get("schemas") if isinstance(components, Mapping) else None
    schemas = schemas if isinstance(schemas, Mapping) else {}
    prefix = "#/components/schemas/"
    pending: list[object] = [schema]
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            ref = value.get("$ref")
            if ref is not None:
                if not isinstance(ref, str) or not ref.startswith(prefix):
                    raise ProductionApiContractError(
                        f"external or unsupported OpenAPI reference: {ref!r}"
                    )
                if ref[len(prefix) :] not in schemas:
                    raise ProductionApiContractError(
                        f"unresolved OpenAPI component reference: {ref!r}"
                    )
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def _validate_startup_gate(root: Path, gate: Mapping[str, Any]) -> None:
    _exact_mapping(gate, _STARTUP_KEYS, label="startup_gate")
    expected = {
        "source": "packages/maverick-dashboard/maverick_dashboard/app.py",
        "lifespan_symbol": "_lifespan",
        "validator_symbol": "validate_production_openapi_contract",
        "failure_semantics": "startup-refuses-unreviewed-schema-drift",
    }
    if dict(gate) != expected:
        raise ProductionApiContractError("startup_gate differs from the reviewed gate")
    path = root / str(gate["source"])
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise ProductionApiContractError("startup gate source is not parseable") from exc
    lifespan = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == gate["lifespan_symbol"]
        ),
        None,
    )
    if lifespan is None:
        raise ProductionApiContractError("dashboard lifespan symbol is missing")
    calls = [
        node
        for node in ast.walk(lifespan)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == gate["validator_symbol"]
    ]
    yields = [
        node.lineno
        for node in ast.walk(lifespan)
        if isinstance(node, (ast.Yield, ast.YieldFrom))
    ]
    if len(calls) != 1 or not yields or calls[0].lineno >= min(yields):
        raise ProductionApiContractError(
            "OpenAPI validator must run exactly once before lifespan admission"
        )


def _validate_bindings(root: Path, value: object) -> None:
    if not isinstance(value, list) or len(value) != len(SOURCE_BINDING_PATHS):
        raise ProductionApiContractError("source_bindings inventory is incomplete")
    expected_paths = list(SOURCE_BINDING_PATHS)
    actual_paths: list[str] = []
    for index, row in enumerate(value):
        mapping = _exact_mapping(
            row,
            {"path", "sha256", "digest_mode"},
            label=f"source_bindings[{index}]",
        )
        path = mapping.get("path")
        digest = mapping.get("sha256")
        if (
            not isinstance(path, str)
            or path not in SOURCE_BINDING_PATHS
            or mapping.get("digest_mode") != "utf8-lf-sha256"
            or not isinstance(digest, str)
            or _DIGEST_RE.fullmatch(digest) is None
        ):
            raise ProductionApiContractError(
                f"source_bindings[{index}] is malformed"
            )
        actual_paths.append(path)
        if _source_sha256(root / path) != digest:
            raise ProductionApiContractError(f"source binding drifted: {path}")
    if actual_paths != expected_paths:
        raise ProductionApiContractError(
            "source_bindings must use the reviewed deterministic order"
        )


def build_contract(root: Path, schema: dict[str, Any]) -> dict[str, Any]:
    root = root.resolve()
    surface = _load_json(root / SURFACE_PATH, label="production surface")
    packaged = _load_json(root / PACKAGED_PATH, label="packaged OpenAPI contract")
    schema_path = root / OPENAPI_PATH
    operation_count, _ = _operation_inventory(schema)
    release_state = surface.get("release_state")
    if not isinstance(release_state, Mapping):
        raise ProductionApiContractError("production surface release_state is missing")
    return {
        "schema": SCHEMA,
        "repository": REPOSITORY,
        "profile_id": surface.get("profile_id"),
        "source_version": surface.get("source_version"),
        "release_eligible": release_state.get("eligible"),
        "contract_version": packaged.get("contract_version"),
        "lifecycle_status": packaged.get("lifecycle_status"),
        "api_version": packaged.get("api_version"),
        "openapi_version": packaged.get("openapi_version"),
        "operation_count": operation_count,
        "canonical_schema_sha256": hashlib.sha256(_canonical(schema)).hexdigest(),
        "schema_artifact": OPENAPI_PATH.as_posix(),
        "schema_artifact_sha256": _sha256(schema_path),
        "packaged_runtime_contract": PACKAGED_PATH.as_posix(),
        "packaged_runtime_contract_sha256": _sha256(root / PACKAGED_PATH),
        "compatibility_policy": packaged.get("compatibility_policy"),
        "deprecation_policy": packaged.get("deprecation_policy"),
        "migration_guide": packaged.get("migration_guide"),
        "startup_gate": {
            "source": "packages/maverick-dashboard/maverick_dashboard/app.py",
            "lifespan_symbol": "_lifespan",
            "validator_symbol": "validate_production_openapi_contract",
            "failure_semantics": "startup-refuses-unreviewed-schema-drift",
        },
        "source_bindings": [
            {
                "path": path,
                "sha256": _source_sha256(root / path),
                "digest_mode": "utf8-lf-sha256",
            }
            for path in SOURCE_BINDING_PATHS
        ],
        "release_boundary": {
            "compatibility_commitment_active": False,
            "clock_starts": (
                "first-supported-release-containing-the-deprecation-notice"
            ),
            "external_release_requirements": [
                "signed-exact-release-governance-packet",
                "published-supported-release",
                "release-specific-migration-notes",
                "qualified-client-and-browser-journeys",
            ],
        },
    }


def validate_contract(
    root: Path,
    contract: Mapping[str, Any],
    schema: Mapping[str, Any],
    current_schema: Mapping[str, Any],
) -> list[str]:
    root = root.resolve()
    _exact_mapping(contract, _CONTRACT_KEYS, label="production API contract")
    if contract.get("schema") != SCHEMA or contract.get("repository") != REPOSITORY:
        raise ProductionApiContractError("production API contract identity is invalid")

    surface = _load_json(root / SURFACE_PATH, label="production surface")
    release_state = surface.get("release_state")
    eligible = (
        release_state.get("eligible") if isinstance(release_state, Mapping) else None
    )
    if (
        contract.get("profile_id") != surface.get("profile_id")
        or contract.get("source_version") != surface.get("source_version")
        or not isinstance(eligible, bool)
        or contract.get("release_eligible") is not eligible
    ):
        raise ProductionApiContractError(
            "production API contract differs from the source profile/version/eligibility"
        )

    if contract.get("schema_artifact") != OPENAPI_PATH.as_posix():
        raise ProductionApiContractError("schema_artifact path is not exact")
    schema_path = root / OPENAPI_PATH
    artifact_digest = contract.get("schema_artifact_sha256")
    if (
        not isinstance(artifact_digest, str)
        or _DIGEST_RE.fullmatch(artifact_digest) is None
        or _sha256(schema_path) != artifact_digest
    ):
        raise ProductionApiContractError("OpenAPI schema artifact bytes drifted")
    canonical_digest = hashlib.sha256(_canonical(schema)).hexdigest()
    if contract.get("canonical_schema_sha256") != canonical_digest:
        raise ProductionApiContractError("OpenAPI canonical digest drifted")
    if schema != current_schema:
        raise ProductionApiContractError(
            "complete OpenAPI artifact differs from the checkout-generated schema"
        )
    operation_count, operation_ids = _operation_inventory(schema)
    _validate_refs(schema)
    if contract.get("operation_count") != operation_count:
        raise ProductionApiContractError("OpenAPI operation count drifted")
    if schema.get("openapi") != contract.get("openapi_version"):
        raise ProductionApiContractError("OpenAPI document version drifted")
    info = schema.get("info")
    if not isinstance(info, Mapping) or info.get("version") != contract.get("api_version"):
        raise ProductionApiContractError("OpenAPI info.version drifted")

    if contract.get("packaged_runtime_contract") != PACKAGED_PATH.as_posix():
        raise ProductionApiContractError("packaged runtime contract path is not exact")
    packaged_digest = contract.get("packaged_runtime_contract_sha256")
    if (
        not isinstance(packaged_digest, str)
        or _DIGEST_RE.fullmatch(packaged_digest) is None
        or _sha256(root / PACKAGED_PATH) != packaged_digest
    ):
        raise ProductionApiContractError("packaged runtime contract bytes drifted")
    packaged = _load_json(root / PACKAGED_PATH, label="packaged OpenAPI contract")
    expected_package_fields = {
        "contract_version": contract.get("contract_version"),
        "lifecycle_status": contract.get("lifecycle_status"),
        "api_version": contract.get("api_version"),
        "openapi_version": contract.get("openapi_version"),
        "operation_count": operation_count,
        "production_schema_sha256": canonical_digest,
        "compatibility_policy": contract.get("compatibility_policy"),
        "deprecation_policy": contract.get("deprecation_policy"),
        "migration_guide": contract.get("migration_guide"),
    }
    for field, expected in expected_package_fields.items():
        if packaged.get(field) != expected:
            raise ProductionApiContractError(
                f"packaged runtime OpenAPI field drifted: {field}"
            )
    if packaged.get("schema") != "lightwork.dashboard-openapi-contract.v1":
        raise ProductionApiContractError("packaged runtime OpenAPI schema is unsupported")

    deprecation = contract.get("deprecation_policy")
    if not isinstance(deprecation, Mapping) or dict(deprecation) != {
        "clock_starts": "first-supported-release-containing-the-deprecation-notice",
        "minimum_calendar_days": 90,
        "minimum_supported_minor_releases": 2,
        "removal_requires": [
            "reviewed-contract-version-bump",
            "migration-guide",
            "release-notes",
            "claim-evidence-ledger-refresh",
        ],
    }:
        raise ProductionApiContractError("deprecation policy is not the reviewed policy")
    if (
        contract.get("compatibility_policy")
        != "exact-until-reviewed-version-bump"
        or contract.get("lifecycle_status") != "unpublished-release-candidate"
        or contract.get("migration_guide")
        != "docs/api.md#api-compatibility-deprecation-and-migration"
    ):
        raise ProductionApiContractError("API lifecycle metadata is not conservative")

    boundary = _exact_mapping(
        contract.get("release_boundary"),
        _RELEASE_BOUNDARY_KEYS,
        label="release_boundary",
    )
    requirements = boundary.get("external_release_requirements")
    if (
        boundary.get("compatibility_commitment_active") is not False
        or boundary.get("clock_starts") != deprecation.get("clock_starts")
        or not isinstance(requirements, list)
        or len(requirements) < 4
        or any(not isinstance(item, str) or not item for item in requirements)
    ):
        raise ProductionApiContractError(
            "release boundary overclaims support or omits external qualification"
        )

    _validate_startup_gate(root, contract.get("startup_gate"))
    _validate_bindings(root, contract.get("source_bindings"))
    return [
        f"verified {operation_count} exact production OpenAPI operations",
        f"verified {len(operation_ids)} unique operation IDs",
        f"contract_version={contract['contract_version']}",
        f"lifecycle_status={contract['lifecycle_status']}",
        f"release_eligible={str(eligible).lower()}",
    ]


def verify(
    root: Path,
    *,
    current_schema: Mapping[str, Any] | None = None,
) -> list[str]:
    root = root.resolve()
    contract = _load_json(root / CONTRACT_PATH, label="production API contract")
    schema = _load_json(root / OPENAPI_PATH, label="production OpenAPI schema")
    generated = current_schema if current_schema is not None else _current_schema(root)
    return validate_contract(root, contract, schema, generated)


def write(root: Path) -> list[str]:
    root = root.resolve()
    schema = _current_schema(root)
    schema_path = root / OPENAPI_PATH
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    contract = build_contract(root, schema)
    (root / CONTRACT_PATH).write_text(
        json.dumps(contract, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return verify(root, current_schema=schema)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--write",
        action="store_true",
        help="regenerate the complete schema and reviewed metadata candidate",
    )
    args = parser.parse_args(argv)
    try:
        messages = write(args.root) if args.write else verify(args.root)
    except ProductionApiContractError as exc:
        print(f"production API contract: FAIL: {exc}", file=sys.stderr)
        return 1
    for message in messages:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
