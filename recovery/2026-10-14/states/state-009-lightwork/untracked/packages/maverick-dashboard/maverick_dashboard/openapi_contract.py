"""Versioned, exact production OpenAPI compatibility gate."""
from __future__ import annotations

import copy
import hashlib
import json
from importlib import resources
from typing import Any

CONTRACT_SCHEMA = "lightwork.dashboard-openapi-contract.v1"
CONTRACT_VERSION = "5.0.0"
CONTRACT_RESOURCE = "contracts/openapi-production-v1.json"
LIFECYCLE_STATUS = "unpublished-release-candidate"
MIGRATION_GUIDE = "docs/api.md#api-compatibility-deprecation-and-migration"
DEPRECATION_POLICY = {
    "clock_starts": "first-supported-release-containing-the-deprecation-notice",
    "minimum_calendar_days": 90,
    "minimum_supported_minor_releases": 2,
    "removal_requires": [
        "reviewed-contract-version-bump",
        "migration-guide",
        "release-notes",
        "claim-evidence-ledger-refresh",
    ],
}


class ProductionOpenApiContractError(RuntimeError):
    """The generated production schema drifted from the reviewed contract."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def production_openapi_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the fixed first-profile production schema from a generated spec."""
    from .production_visibility import (
        _prune_unreferenced_schemas,
        _prune_unreviewed_openapi_operations,
    )

    narrowed = copy.deepcopy(schema)
    _prune_unreviewed_openapi_operations(narrowed)
    _prune_unreferenced_schemas(narrowed)
    return narrowed


def contract_summary(schema: dict[str, Any]) -> dict[str, Any]:
    """Build the review artifact fields from a generated OpenAPI document."""
    production = production_openapi_schema(schema)
    operations = sum(
        1
        for path_item in production.get("paths", {}).values()
        for method, operation in path_item.items()
        if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options"}
        and isinstance(operation, dict)
    )
    return {
        "schema": CONTRACT_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "api_version": str(production.get("info", {}).get("version") or ""),
        "openapi_version": str(production.get("openapi") or ""),
        "operation_count": operations,
        "production_schema_sha256": hashlib.sha256(_canonical(production)).hexdigest(),
        "compatibility_policy": "exact-until-reviewed-version-bump",
        "lifecycle_status": LIFECYCLE_STATUS,
        "deprecation_policy": copy.deepcopy(DEPRECATION_POLICY),
        "migration_guide": MIGRATION_GUIDE,
    }


def _load_artifact() -> dict[str, Any]:
    try:
        location = resources.files("maverick_dashboard").joinpath(CONTRACT_RESOURCE)
        parsed = json.loads(location.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ProductionOpenApiContractError(
            "production OpenAPI contract artifact is unavailable"
        ) from exc
    if not isinstance(parsed, dict):
        raise ProductionOpenApiContractError(
            "production OpenAPI contract artifact must be an object"
        )
    return parsed


def validate_production_openapi_contract(app: Any) -> dict[str, Any]:
    """Fail production startup on any unreviewed schema change."""
    actual = contract_summary(app.openapi())
    app.state.openapi_contract = actual
    try:
        from maverick.production_scope import production_surface_required

        required = production_surface_required()
    except Exception as exc:
        raise ProductionOpenApiContractError(
            "production OpenAPI posture is unavailable"
        ) from exc
    if not required:
        return actual
    expected = _load_artifact()
    if expected != actual:
        raise ProductionOpenApiContractError(
            "production OpenAPI schema drifted from the versioned compatibility artifact"
        )
    return actual


def main() -> int:
    """Print the current artifact JSON for an explicit compatibility review."""
    from .app import app

    print(json.dumps(contract_summary(app.openapi()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - operator helper
    raise SystemExit(main())


__all__ = [
    "CONTRACT_SCHEMA",
    "CONTRACT_VERSION",
    "DEPRECATION_POLICY",
    "LIFECYCLE_STATUS",
    "MIGRATION_GUIDE",
    "ProductionOpenApiContractError",
    "contract_summary",
    "production_openapi_schema",
    "validate_production_openapi_contract",
]
