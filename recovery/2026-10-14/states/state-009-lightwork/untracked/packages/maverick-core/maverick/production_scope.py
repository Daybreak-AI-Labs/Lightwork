"""Default-deny loader for the reviewed production surface manifest.

The source repository contains many research, experimental, generated, and
client artifacts.  Their presence is not production authority.  A production
artifact carries one explicit manifest, and every unlisted surface is treated
as unsupported.  Startup validation and packaging consume the same document.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "lightwork.production-surface.v1"
MANIFEST_ENV = "MAVERICK_PRODUCTION_MANIFEST"
EXPECTED_DIGEST_ENV = "MAVERICK_PRODUCTION_MANIFEST_SHA256"
REQUIRE_PRODUCTION_SURFACE_ENV = "MAVERICK_REQUIRE_PRODUCTION_SURFACE"
_TRUE_WORDS = frozenset({"1", "true", "yes", "on", "enable", "enabled", "y", "t"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off", "disable", "disabled", "n", "f"})
_STATUS_VALUES = frozenset(
    {
        "ga",
        "supported_beta",
        "experimental",
        "research_only",
        "internal_only",
        "unsupported",
    }
)
_TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "profile_id",
        "source_version",
        "release_state",
        "default_status",
        "allowed_statuses",
        "constraints",
        "surfaces",
    }
)
_SURFACE_KEYS = frozenset({"id", "category", "status", "included"})
_RELEASE_STATE_KEYS = frozenset(
    {"eligible", "reason", "latest_repository_tag", "github_release"}
)
_CONSTRAINT_KEYS = frozenset(
    {
        "control_plane_replicas",
        "world_model_backend",
        "world_model_rls_required",
        "tenancy_mode",
        "identity_providers",
        "sandbox_backends",
        "model_providers",
        "kms_providers",
        "connectors",
        "channels",
        "plugins",
        "operating_system",
        "architectures",
        "knowledge_enabled",
        "attachments_enabled",
        "code_dgm_enabled",
        "weights_dgm_enabled",
        "flow_auto_apply_enabled",
        "consequential_actions_enabled",
    }
)
_LIST_CONSTRAINT_KEYS = frozenset(
    {
        "identity_providers",
        "sandbox_backends",
        "model_providers",
        "kms_providers",
        "connectors",
        "channels",
        "plugins",
        "architectures",
    }
)
_BOOLEAN_CONSTRAINT_KEYS = frozenset(
    {
        "world_model_rls_required",
        "knowledge_enabled",
        "attachments_enabled",
        "code_dgm_enabled",
        "weights_dgm_enabled",
        "flow_auto_apply_enabled",
        "consequential_actions_enabled",
    }
)
_SURFACE_CATEGORIES = frozenset(
    {"package", "interface", "runtime", "identity", "provider", "feature", "client", "tool"}
)


class ProductionScopeError(RuntimeError):
    """The production surface manifest is absent, malformed, or mismatched."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one JSON object while refusing duplicate authority-bearing keys."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    """Reject Python's non-standard NaN/Infinity JSON extensions."""
    raise ValueError(f"non-finite JSON constant: {value}")


def production_surface_required() -> bool:
    """Return whether this process is an explicitly admitted production artifact.

    Merely finding the source-tree manifest does not make a developer checkout a
    production artifact.  Images and deployment profiles must set the dedicated
    requirement flag.  A malformed value is rejected instead of silently
    downgrading the startup boundary.
    """
    raw = os.environ.get(REQUIRE_PRODUCTION_SURFACE_ENV)
    if raw is None or not raw.strip():
        return False
    normalized = raw.strip().lower()
    if normalized in _TRUE_WORDS:
        return True
    if normalized in _FALSE_WORDS:
        return False
    raise ProductionScopeError(
        f"{REQUIRE_PRODUCTION_SURFACE_ENV} must be an explicit boolean value"
    )


@dataclass(frozen=True)
class ProductionSurface:
    surface_id: str
    category: str
    status: str
    included: bool


@dataclass(frozen=True)
class ProductionManifest:
    path: Path
    sha256: str
    profile_id: str
    source_version: str
    release_eligible: bool
    release_reason: str
    constraints: dict[str, Any]
    surfaces: dict[str, ProductionSurface]
    default_status: str = "unsupported"

    def status_for(self, surface_id: str) -> str:
        surface = self.surfaces.get(surface_id)
        return surface.status if surface is not None else self.default_status

    def includes(self, surface_id: str) -> bool:
        surface = self.surfaces.get(surface_id)
        return bool(surface is not None and surface.included)

    def included_packages(self) -> tuple[str, ...]:
        prefix = "package:"
        return tuple(
            surface_id[len(prefix):]
            for surface_id, surface in self.surfaces.items()
            if surface.category == "package"
            and surface.included
            and surface_id.startswith(prefix)
        )


def _source_checkout_manifest() -> Path | None:
    """Locate the repository manifest in editable/source checkouts only."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "release" / "production-surface-manifest.json"
        if candidate.is_file():
            return candidate
    return None


def manifest_path() -> Path:
    configured = os.environ.get(MANIFEST_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    installed = Path("/etc/lightwork/production-surface-manifest.json")
    if installed.is_file():
        return installed
    source = _source_checkout_manifest()
    if source is not None:
        return source
    raise ProductionScopeError(
        f"production surface manifest is missing; set {MANIFEST_ENV} to the "
        "reviewed manifest shipped with the release artifact"
    )


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProductionScopeError(f"production manifest {field} must be non-empty")
    return value.strip()


def _validate_string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ProductionScopeError(f"production manifest {field} must be an array")
    normalized: list[str] = []
    for item in value:
        normalized.append(_nonempty_string(item, field))
    if len(normalized) != len(set(normalized)):
        raise ProductionScopeError(f"production manifest {field} contains duplicates")
    return normalized


def _validate_constraints(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _CONSTRAINT_KEYS:
        raise ProductionScopeError("production manifest constraints schema is not exact")
    replicas = value.get("control_plane_replicas")
    if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas != 1:
        raise ProductionScopeError(
            "production manifest control_plane_replicas must be exactly 1"
        )
    _nonempty_string(value.get("world_model_backend"), "world_model_backend")
    if value.get("tenancy_mode") != "principal_isolated":
        raise ProductionScopeError(
            "production manifest tenancy_mode must be principal_isolated"
        )
    _nonempty_string(value.get("operating_system"), "operating_system")
    for field in _BOOLEAN_CONSTRAINT_KEYS:
        if not isinstance(value.get(field), bool):
            raise ProductionScopeError(
                f"production manifest {field} must be boolean"
            )
    for field in _LIST_CONSTRAINT_KEYS:
        items = _validate_string_list(value.get(field), field)
        if field in {
            "identity_providers",
            "sandbox_backends",
            "model_providers",
            "kms_providers",
            "architectures",
        } and not items:
            raise ProductionScopeError(
                f"production manifest {field} must not be empty"
            )
    return dict(value)


def _load_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ProductionScopeError(f"cannot read production manifest: {path}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    expected = os.environ.get(EXPECTED_DIGEST_ENV, "").strip().lower()
    if expected and expected != digest:
        raise ProductionScopeError(
            "production manifest digest does not match the release-pinned digest"
        )
    try:
        document = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ProductionScopeError("production manifest is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise ProductionScopeError("production manifest root must be an object")
    return document, digest


def load_manifest(path: str | Path | None = None) -> ProductionManifest:
    resolved = Path(path).expanduser() if path is not None else manifest_path()
    document, digest = _load_json(resolved)
    if set(document) != _TOP_LEVEL_KEYS:
        raise ProductionScopeError("production manifest top-level schema is not exact")
    if document.get("schema") != SCHEMA:
        raise ProductionScopeError("production manifest schema is unsupported")
    allowed = document.get("allowed_statuses")
    if (
        not isinstance(allowed, list)
        or len(allowed) != len(_STATUS_VALUES)
        or any(not isinstance(status, str) for status in allowed)
        or set(allowed) != _STATUS_VALUES
    ):
        raise ProductionScopeError("production manifest status vocabulary drifted")
    default_status = document.get("default_status")
    if default_status != "unsupported":
        raise ProductionScopeError("production manifest must default unknown surfaces to unsupported")

    release_state = document.get("release_state")
    if not isinstance(release_state, dict) or set(release_state) != _RELEASE_STATE_KEYS:
        raise ProductionScopeError(
            "production manifest release_state schema is not exact"
        )
    eligible = release_state.get("eligible")
    if not isinstance(eligible, bool):
        raise ProductionScopeError("production manifest release eligibility must be boolean")
    reason = _nonempty_string(release_state.get("reason"), "release_state.reason")
    latest_tag = release_state.get("latest_repository_tag")
    if latest_tag is not None:
        _nonempty_string(latest_tag, "release_state.latest_repository_tag")
    github_release = release_state.get("github_release")
    if github_release is not None:
        _nonempty_string(github_release, "release_state.github_release")

    constraints = _validate_constraints(document.get("constraints"))

    raw_surfaces = document.get("surfaces")
    if not isinstance(raw_surfaces, list) or not raw_surfaces:
        raise ProductionScopeError("production manifest surfaces must be a non-empty array")
    surfaces: dict[str, ProductionSurface] = {}
    for item in raw_surfaces:
        if not isinstance(item, dict) or set(item) != _SURFACE_KEYS:
            raise ProductionScopeError("production manifest surface schema is not exact")
        surface_id = _nonempty_string(item.get("id"), "surface.id")
        category = _nonempty_string(item.get("category"), "surface.category")
        status = _nonempty_string(item.get("status"), "surface.status")
        included = item.get("included")
        if surface_id in surfaces:
            raise ProductionScopeError(f"duplicate production surface: {surface_id}")
        if status not in _STATUS_VALUES:
            raise ProductionScopeError(f"unknown production status for {surface_id}")
        if category not in _SURFACE_CATEGORIES:
            raise ProductionScopeError(f"unknown production category for {surface_id}")
        prefix, separator, _ = surface_id.partition(":")
        if separator != ":" or prefix != category:
            raise ProductionScopeError(
                f"production surface category does not match id: {surface_id}"
            )
        if not isinstance(included, bool):
            raise ProductionScopeError(f"included must be boolean for {surface_id}")
        if included and status not in {"ga", "supported_beta"}:
            raise ProductionScopeError(
                f"non-supported surface cannot be included: {surface_id}"
            )
        surfaces[surface_id] = ProductionSurface(
            surface_id=surface_id,
            category=category,
            status=status,
            included=included,
        )

    return ProductionManifest(
        path=resolved.resolve(),
        sha256=digest,
        profile_id=_nonempty_string(document.get("profile_id"), "profile_id"),
        source_version=_nonempty_string(document.get("source_version"), "source_version"),
        release_eligible=eligible,
        release_reason=reason,
        constraints=constraints,
        surfaces=surfaces,
        default_status=default_status,
    )


__all__ = [
    "EXPECTED_DIGEST_ENV",
    "MANIFEST_ENV",
    "REQUIRE_PRODUCTION_SURFACE_ENV",
    "ProductionManifest",
    "ProductionScopeError",
    "ProductionSurface",
    "load_manifest",
    "manifest_path",
    "production_surface_required",
]
