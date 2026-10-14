#!/usr/bin/env python3
"""Reconcile Lightwork's source, cohort, tag, release, and support state.

The default mode is deterministic and offline: it verifies the checked-out
files and local Git refs against ``release/release-state.json``.  A caller may
also pass an exported ``gh release list --json ...`` result to compare a fresh
remote observation without giving this verifier network authority.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))
from release_version import validate_tag  # noqa: E402
from verify_claim_evidence_ledger import (  # noqa: E402
    ClaimEvidenceLedgerError,
    verify_claim_evidence_ledger,
)
from verify_dependency_failure_contract import (  # noqa: E402
    DependencyFailureContractError,
    verify_dependency_failure_contract,
)
from verify_production_api_contract import (  # noqa: E402
    ProductionApiContractError,
)
from verify_production_api_contract import (  # noqa: E402
    verify as verify_production_api_contract,
)
from verify_production_code_inventory import (  # noqa: E402
    ProductionCodeInventoryError,
    verify_production_code_inventory,
)
from verify_production_data_boundary import (  # noqa: E402
    DataBoundaryError,
)
from verify_production_data_boundary import (  # noqa: E402
    verify as verify_production_data_boundary,
)
from verify_production_migration_contract import (  # noqa: E402
    ProductionMigrationContractError,
    verify_production_migration_contract,
)
from verify_production_model_policy import (  # noqa: E402
    verify_production_model_policy,
)
from verify_production_onboarding_contract import (  # noqa: E402
    ProductionOnboardingContractError,
)
from verify_production_onboarding_contract import (  # noqa: E402
    verify as verify_production_onboarding_contract,
)
from verify_production_operations_contract import (  # noqa: E402
    OperationsContractError,
)
from verify_production_operations_contract import (  # noqa: E402
    verify as verify_production_operations_contract,
)
from verify_production_qa_assurance import (  # noqa: E402
    ProductionQaAssuranceError,
)
from verify_production_qa_assurance import (  # noqa: E402
    verify as verify_production_qa_assurance,
)
from verify_production_readiness import (  # noqa: E402
    ProductionReadinessError,
    validate_production_readiness,
)
from verify_production_runtime_contract import (  # noqa: E402
    ProductionRuntimeContractVerificationError,
)
from verify_production_runtime_contract import (  # noqa: E402
    verify as verify_production_runtime_contract,
)
from verify_production_support_matrix import (  # noqa: E402
    SupportMatrixError,
)
from verify_production_support_matrix import (  # noqa: E402
    verify as verify_production_support_matrix,
)
from verify_pytest_exceptions import (  # noqa: E402
    PytestExceptionPolicyError,
    validate_pytest_exception_policy,
)
from verify_secret_rotation_contract import (  # noqa: E402
    SecretRotationContractError,
    verify_secret_rotation_contract,
)
from verify_service_objectives import (  # noqa: E402
    ServiceObjectiveError,
    validate_service_objectives,
)
from verify_tenant_quota_contract import (  # noqa: E402
    TenantQuotaContractError,
    verify_tenant_quota_contract,
)

SCHEMA = "lightwork.release-state.v1"
PRODUCTION_SCHEMA = "lightwork.production-surface.v1"
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_BEGIN_SUPPORT = "<!-- BEGIN GENERATED RELEASE SUPPORT -->"
_END_SUPPORT = "<!-- END GENERATED RELEASE SUPPORT -->"
_PRODUCTION_IDENTITY_POSTURE_CONTRACT = {
    "credential_fields_excluded": [
        "MAVERICK_OIDC_CLIENT_SECRET",
        "MAVERICK_OIDC_SESSION_SECRET",
        "auth.oidc.client_secret",
        "auth.oidc.session_secret",
    ],
    "digest": {
        "algorithm": "sha256",
        "canonicalization": "sorted-key-compact-ascii-json",
        "covers": [
            "selected_provider",
            "identity_mapping",
            "canonical_rbac_policy_bytes",
        ],
    },
    "identity_mapping_fields": {
        "oidc": [
            "issuer",
            "audience",
            "jwks_uri",
            "algorithms",
            "client_id",
            "redirect_uri",
            "authorization_endpoint",
            "token_endpoint",
            "claim_mapping",
        ],
        "trusted_proxy_sso": [
            "header",
            "trusted_proxies",
            "trust_loopback",
            "claim_mapping",
        ],
    },
    "mutable_authorization_state": {
        "included_in_digest": False,
        "paths": [
            "dashboard-users.json",
            "dashboard-tenant-roles.json",
            "dashboard-user-suites.json",
            "dashboard-tenant-suites.json",
        ],
        "requirement": "live evidence and authorized-change requalification",
    },
    "posture_schema": "lightwork.production-identity-rbac-posture.v1",
    "profile_id": "enterprise-single-control-plane-v1",
    "rbac_policy_inputs": [
        "dashboard.role_permissions",
        "dashboard.default_role",
        "dashboard.bootstrap_admins",
        "dashboard.default_suites",
        "dashboard.group_roles",
        "dashboard.group_suites",
        "kernel.roles",
        "kernel.role_assignments",
    ],
    "rbac_production_requirements": {
        "bootstrap_admins": "one_or_more_exact_user_principals",
        "dashboard_default_role": "viewer",
        "mutable_roster_assignments": (
            "live_evidence_and_authorized_change_requalification"
        ),
    },
    "schema": "lightwork.production-identity-posture-contract.v1",
    "selection_cardinality": "exactly_one",
    "supported_providers": ["oidc", "trusted_proxy_sso"],
}


class ReleaseStateError(ValueError):
    """The repository's declared and observed release facts disagree."""


class _StrictJsonError(ValueError):
    """Repository or supplied release JSON has ambiguous semantics."""


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _StrictJsonError(f"duplicate JSON key: {key!r}")
        value[key] = item
    return value


def _reject_json_constant(token: str) -> None:
    raise _StrictJsonError(f"non-finite JSON value: {token}")


def _mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReleaseStateError(f"{label} must be an object")
    return value


def _exact_keys(
    value: object,
    expected: set[str],
    *,
    label: str,
) -> Mapping[str, Any]:
    document = _mapping(value, label=label)
    if set(document) != expected:
        raise ReleaseStateError(
            f"{label} fields differ: "
            f"missing={sorted(expected - set(document))}, "
            f"unknown={sorted(set(document) - expected)}"
        )
    return document


def _read_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except OSError as exc:
        raise ReleaseStateError(f"cannot read {label}: {path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError, _StrictJsonError) as exc:
        raise ReleaseStateError(f"{label} is not valid UTF-8 JSON: {path}") from exc


def _repo_path(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ReleaseStateError(f"{label} must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReleaseStateError(f"{label} must stay inside the repository")
    path = root / relative
    if not path.is_file():
        raise ReleaseStateError(f"{label} does not exist: {relative.as_posix()}")
    return path


def _git(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseStateError(f"cannot inspect local Git state: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ReleaseStateError(f"git {' '.join(arguments)} failed: {detail}")
    return result.stdout.strip()


def _tag_inventory(root: Path) -> tuple[list[str], list[str]]:
    canonical: list[str] = []
    noncanonical: list[str] = []
    output = _git(root, "tag", "--list", "--sort=-version:refname")
    for tag in output.splitlines():
        if not tag:
            continue
        try:
            validate_tag(tag)
        except ValueError:
            if tag.startswith("v"):
                noncanonical.append(tag)
        else:
            canonical.append(tag)
    return canonical, sorted(noncanonical)


def _tag_commit(root: Path, tag: str) -> str:
    commit = _git(root, "rev-parse", f"{tag}^{{commit}}")
    if _COMMIT_RE.fullmatch(commit) is None:
        raise ReleaseStateError(f"tag {tag} did not resolve to a full commit SHA")
    return commit


def _literal_source_version(path: Path) -> str:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise ReleaseStateError(f"cannot inspect runtime version: {path}") from exc
    versions = {
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "__version__"
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    if len(versions) != 1:
        raise ReleaseStateError(f"{path} must contain one literal __version__")
    return versions.pop()


def _validate_cohort(
    root: Path,
    state: Mapping[str, Any],
) -> tuple[str, list[str]]:
    manifests = _exact_keys(
        state.get("manifests"),
        {
            "claim_evidence_ledger",
            "dependency_failure_contract",
            "release_cohort",
            "production_api_contract",
            "production_change_control",
            "production_code_inventory",
            "production_data_boundary",
            "production_identity_posture_contract",
            "production_migration_contract",
            "production_model_policy",
            "production_onboarding_contract",
            "production_openapi_schema",
            "production_operations_contract",
            "production_qa_assurance",
            "production_runtime_contract",
            "production_surface",
            "production_support_matrix",
            "production_readiness",
            "pytest_exceptions",
            "secret_rotation_contract",
            "tenant_quota_contract",
            "service_objectives",
        },
        label="release-state manifests",
    )
    cohort_path = _repo_path(
        root,
        manifests["release_cohort"],
        label="release cohort manifest",
    )
    try:
        cohort = tomllib.loads(cohort_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseStateError("release cohort is not valid UTF-8 TOML") from exc
    cohort = _exact_keys(
        cohort,
        {"schema_version", "version", "repository", "packages"},
        label="release cohort",
    )
    if cohort["schema_version"] != 1:
        raise ReleaseStateError("unsupported release-cohort schema")
    version = cohort["version"]
    if not isinstance(version, str) or not version:
        raise ReleaseStateError("release-cohort version must be non-empty")
    if version != state.get("source_version"):
        raise ReleaseStateError("source version differs from release cohort")
    if cohort["repository"] != state.get("repository"):
        raise ReleaseStateError("repository differs between state and cohort")
    packages = cohort["packages"]
    if not isinstance(packages, list) or len(packages) != 8:
        raise ReleaseStateError("release cohort must contain exactly eight packages")

    names: list[str] = []
    paths: list[str] = []
    for index, item_value in enumerate(packages):
        item = _exact_keys(
            item_value,
            {"distribution", "path", "runtime_module"},
            label=f"release cohort package {index}",
        )
        distribution = item["distribution"]
        package_path = item["path"]
        runtime_module = item["runtime_module"]
        if not all(
            isinstance(value, str) and value
            for value in (distribution, package_path, runtime_module)
        ):
            raise ReleaseStateError(f"release cohort package {index} has empty fields")
        metadata_path = _repo_path(
            root,
            f"{package_path}/pyproject.toml",
            label=f"{distribution} metadata",
        )
        try:
            project = tomllib.loads(metadata_path.read_text(encoding="utf-8"))["project"]
        except (OSError, KeyError, TypeError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ReleaseStateError(f"cannot read project metadata for {distribution}") from exc
        if project.get("name") != distribution:
            raise ReleaseStateError(f"{package_path} distribution name differs from cohort")
        if project.get("version") != version:
            raise ReleaseStateError(f"{distribution} version differs from cohort")
        runtime_version = _literal_source_version(
            root / package_path / runtime_module / "__init__.py"
        )
        if runtime_version != version:
            raise ReleaseStateError(f"{distribution} runtime version differs from cohort")
        names.append(distribution)
        paths.append(package_path)

    if len(set(names)) != len(names) or len(set(paths)) != len(paths):
        raise ReleaseStateError("release cohort contains duplicate names or paths")

    try:
        workspace = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        uv = workspace["tool"]["uv"]
    except (OSError, KeyError, TypeError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseStateError("workspace metadata is missing or malformed") from exc
    if set(uv["workspace"]["members"]) != set(paths):
        raise ReleaseStateError("uv workspace members differ from release cohort")
    if set(uv["sources"]) != set(names):
        raise ReleaseStateError("uv workspace sources differ from release cohort")
    if any(source != {"workspace": True} for source in uv["sources"].values()):
        raise ReleaseStateError("release cohort workspace sources must be local workspaces")
    return version, names


def _validate_independent_distributions(
    root: Path,
    state: Mapping[str, Any],
) -> list[tuple[str, str]]:
    records = state.get("independent_distributions")
    if not isinstance(records, list) or not records:
        raise ReleaseStateError("independent distribution inventory is empty")
    inventory: list[tuple[str, str]] = []
    for index, value in enumerate(records):
        record = _exact_keys(
            value,
            {
                "registry",
                "name",
                "path",
                "version",
                "status",
                "workflow",
                "environment",
                "publish_authorized",
            },
            label=f"independent distribution {index}",
        )
        registry = record["registry"]
        if registry not in {"npm", "pypi"}:
            raise ReleaseStateError("unsupported independent distribution registry")
        if record["status"] not in {
            "ga",
            "supported_beta",
            "experimental",
            "research_only",
            "internal_only",
            "unsupported",
        }:
            raise ReleaseStateError("independent distribution status is invalid")
        if not isinstance(record["publish_authorized"], bool):
            raise ReleaseStateError("publish_authorized must be a boolean")
        workflow = record["workflow"]
        environment = record["environment"]
        if (
            workflow not in {"publish.yml", "native.yml"}
            or environment != registry
        ):
            raise ReleaseStateError("independent distribution authority is invalid")
        _repo_path(
            root,
            f".github/workflows/{workflow}",
            label=f"{record['name']} release workflow",
        )

        metadata_path = f"{record['path']}/package.json"
        if registry == "pypi":
            metadata_path = f"{record['path']}/pyproject.toml"
            path = _repo_path(
                root,
                metadata_path,
                label=f"{record['name']} package metadata",
            )
            try:
                metadata = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
            except (
                OSError,
                KeyError,
                TypeError,
                UnicodeDecodeError,
                tomllib.TOMLDecodeError,
            ) as exc:
                raise ReleaseStateError(
                    f"cannot read package metadata for {record['name']}"
                ) from exc
            actual_name = metadata.get("name")
            actual_version = metadata.get("version")
        elif (root / metadata_path).is_file():
            path = _repo_path(
                root,
                metadata_path,
                label=f"{record['name']} package metadata",
            )
            metadata = _mapping(
                _read_json(path, label=f"{record['name']} package metadata"),
                label=f"{record['name']} package metadata",
            )
            actual_name = metadata.get("name")
            actual_version = metadata.get("version")
        else:
            path = _repo_path(
                root,
                f"{record['path']}/Cargo.toml",
                label=f"{record['name']} Cargo metadata",
            )
            workspace_path = _repo_path(
                root,
                "rust/Cargo.toml",
                label="native workspace metadata",
            )
            try:
                package = tomllib.loads(path.read_text(encoding="utf-8"))["package"]
                workspace = tomllib.loads(
                    workspace_path.read_text(encoding="utf-8")
                )["workspace"]["package"]
            except (
                OSError,
                KeyError,
                TypeError,
                UnicodeDecodeError,
                tomllib.TOMLDecodeError,
            ) as exc:
                raise ReleaseStateError("cannot read native package metadata") from exc
            actual_name = package.get("name")
            actual_version = workspace.get("version")
            if package.get("version") != {"workspace": True}:
                raise ReleaseStateError("native npm version must come from its workspace")
        if actual_name != record["name"] or actual_version != record["version"]:
            raise ReleaseStateError(f"{record['name']} metadata differs from release state")
        inventory.append((str(record["registry"]), str(record["name"])))
    if len(set(inventory)) != len(inventory):
        raise ReleaseStateError("independent distribution inventory contains duplicates")
    return inventory


def _normalize_release_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ReleaseStateError("GitHub release observation must be a list")
    releases: list[dict[str, object]] = []
    for index, item_value in enumerate(value):
        item = _mapping(item_value, label=f"GitHub release {index}")
        tag = item.get("tag", item.get("tagName", item.get("tag_name")))
        if not isinstance(tag, str) or not tag:
            raise ReleaseStateError(f"GitHub release {index} has no tag")
        if "state" in item:
            state = item["state"]
        elif item.get("isDraft") is True or item.get("draft") is True:
            state = "draft"
        else:
            state = "published"
        prerelease = item.get("prerelease", item.get("isPrerelease"))
        if state not in {"draft", "published"} or not isinstance(prerelease, bool):
            raise ReleaseStateError(f"GitHub release {index} has invalid state")
        releases.append({"tag": tag, "state": state, "prerelease": prerelease})
    tags = [str(item["tag"]) for item in releases]
    if len(tags) != len(set(tags)):
        raise ReleaseStateError("GitHub release observation contains duplicate tags")
    return sorted(releases, key=lambda item: str(item["tag"]))


def render_security_support(state: Mapping[str, Any]) -> str:
    """Render the exact support block consumed by ``SECURITY.md``."""
    latest = _mapping(state["latest_canonical_tag"], label="latest canonical tag")
    observation = _mapping(
        state["github_release_observation"],
        label="GitHub release observation",
    )
    source_version = state["source_version"]
    source_state = str(state["source_state"]).replace("_", " ")
    release_count = len(observation["releases"])
    return "\n".join(
        [
            _BEGIN_SUPPORT,
            "At the recorded observation time, there was **no supported production release**.",
            "",
            f"- Source version `{source_version}` is {source_state}.",
            f"- The latest canonical repository tag is `{latest['name']}`.",
            (
                f"- The GitHub Releases inventory contained {release_count} release(s) "
                f"when observed at `{observation['observed_at']}`."
            ),
            "- A repository tag by itself is not a supported release.",
            "",
            "| Surface | Security support |",
            "|---------|------------------|",
            f"| Unreleased source `{source_version}` | No |",
            f"| `{latest['name']}` and older tags | No |",
            "",
            "Support can begin only for a signed GitHub Release whose reviewed "
            "production-surface manifest marks it eligible. The machine-readable "
            "snapshot is `release/release-state.json`.",
            _END_SUPPORT,
        ]
    )


def _validate_support_policy(root: Path, state: Mapping[str, Any]) -> None:
    support = _exact_keys(
        state.get("security_support"),
        {"policy", "snapshot_status", "supported_versions"},
        label="security support state",
    )
    if support["policy"] != "latest-signed-github-release":
        raise ReleaseStateError("unsupported security-support policy")
    if support["snapshot_status"] != "none" or support["supported_versions"] != []:
        raise ReleaseStateError("unreleased source must declare no supported versions")
    security_path = root / "SECURITY.md"
    try:
        security = security_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReleaseStateError("cannot read SECURITY.md") from exc
    start = security.find(_BEGIN_SUPPORT)
    end = security.find(_END_SUPPORT)
    if start < 0 or end < start:
        raise ReleaseStateError("SECURITY.md has no generated release-support block")
    end += len(_END_SUPPORT)
    if security.find(_BEGIN_SUPPORT, start + 1) >= 0 or security.find(_END_SUPPORT, end) >= 0:
        raise ReleaseStateError("SECURITY.md has duplicate release-support markers")
    if security[start:end] != render_security_support(state):
        raise ReleaseStateError("SECURITY.md release-support block is stale")


def _validate_production_surface(
    root: Path,
    state: Mapping[str, Any],
    cohort_names: Sequence[str],
    independent: Sequence[tuple[str, str]],
) -> bool:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    path = _repo_path(
        root,
        manifests["production_surface"],
        label="production surface manifest",
    )
    manifest = _mapping(
        _read_json(path, label="production surface manifest"),
        label="production surface manifest",
    )
    if manifest.get("schema") != PRODUCTION_SCHEMA:
        raise ReleaseStateError("production-surface schema differs from release state")
    if manifest.get("source_version") != state["source_version"]:
        raise ReleaseStateError("production-surface version differs from release state")
    release = _mapping(manifest.get("release_state"), label="production release state")
    latest = _mapping(state["latest_canonical_tag"], label="latest canonical tag")
    if release.get("latest_repository_tag") != latest["name"]:
        raise ReleaseStateError("production-surface latest tag differs from release state")
    observation = _mapping(
        state["github_release_observation"],
        label="GitHub release observation",
    )
    if not observation["releases"] and release.get("github_release") is not None:
        raise ReleaseStateError("production-surface claims a nonexistent GitHub Release")
    if not isinstance(release.get("eligible"), bool) or not isinstance(release.get("reason"), str):
        raise ReleaseStateError("production-surface release eligibility is malformed")
    surfaces = manifest.get("surfaces")
    if not isinstance(surfaces, list):
        raise ReleaseStateError("production-surface inventory is missing")
    package_ids = [
        item.get("id")
        for item in surfaces
        if isinstance(item, Mapping) and item.get("category") == "package"
    ]
    if len(package_ids) != len(set(package_ids)):
        raise ReleaseStateError("production-surface package classifications are duplicated")
    required = {f"package:{name}" for name in cohort_names}
    required.update(f"package:{registry}:{name}" for registry, name in independent)
    missing = sorted(required - set(package_ids))
    if missing:
        raise ReleaseStateError(
            f"production-surface manifest leaves distributions unclassified: {missing}"
        )
    return bool(release["eligible"])


def _validate_production_readiness(
    root: Path,
    state: Mapping[str, Any],
    *,
    require_ready: bool,
) -> Mapping[str, Any]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    evidence_path = _repo_path(
        root,
        manifests["production_readiness"],
        label="production readiness evidence",
    )
    surface_path = _repo_path(
        root,
        manifests["production_surface"],
        label="production surface manifest",
    )
    try:
        return validate_production_readiness(
            root,
            evidence_path=evidence_path,
            production_surface_path=surface_path,
            require_ready=require_ready,
        )
    except ProductionReadinessError as exc:
        raise ReleaseStateError(f"production readiness evidence failed: {exc}") from exc


def _validate_pytest_exceptions(
    root: Path,
    state: Mapping[str, Any],
) -> Mapping[str, object]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    if manifests.get("pytest_exceptions") != "qa/pytest-exceptions.json":
        raise ReleaseStateError("release state does not bind pytest exceptions")
    try:
        return validate_pytest_exception_policy(root)
    except PytestExceptionPolicyError as exc:
        raise ReleaseStateError(f"pytest exception policy failed: {exc}") from exc


def _validate_production_model_policy(
    root: Path,
    state: Mapping[str, Any],
    *,
    release_candidate: bool,
) -> Mapping[str, object]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    if manifests.get("production_model_policy") != (
        "release/production-model-policy.json"
    ):
        raise ReleaseStateError("release state does not bind the model policy")
    try:
        return verify_production_model_policy(
            root,
            release_candidate=release_candidate,
        )
    except Exception as exc:
        raise ReleaseStateError(
            f"production model policy failed: {exc}"
        ) from exc


def _validate_production_support_matrix(
    root: Path,
    state: Mapping[str, Any],
) -> list[str]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    if manifests.get("production_support_matrix") != (
        "release/production-support-matrix.json"
    ):
        raise ReleaseStateError("release state does not bind the support matrix")
    try:
        return verify_production_support_matrix(root)
    except SupportMatrixError as exc:
        raise ReleaseStateError(f"production support matrix failed: {exc}") from exc


def _validate_dependency_failure_contract(
    root: Path,
    state: Mapping[str, Any],
) -> Mapping[str, int | str]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    if manifests.get("dependency_failure_contract") != (
        "release/dependency-failure-contract.json"
    ):
        raise ReleaseStateError("release state does not bind the dependency contract")
    try:
        return verify_dependency_failure_contract(root)
    except DependencyFailureContractError as exc:
        raise ReleaseStateError(f"dependency failure contract failed: {exc}") from exc


def _validate_service_objectives(
    root: Path,
    state: Mapping[str, Any],
    *,
    release_candidate: bool,
) -> Mapping[str, object]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    if manifests.get("service_objectives") != "release/service-objectives.json":
        raise ReleaseStateError("release state does not bind service objectives")
    try:
        return validate_service_objectives(
            root,
            release_candidate=release_candidate,
        )
    except ServiceObjectiveError as exc:
        raise ReleaseStateError(f"service objectives failed: {exc}") from exc


def _validate_claim_evidence_ledger(
    root: Path,
    state: Mapping[str, Any],
    *,
    release_candidate: bool,
) -> Mapping[str, object]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    if manifests.get("claim_evidence_ledger") != (
        "release/claim-evidence-ledger.json"
    ):
        raise ReleaseStateError("release state does not bind the claim/evidence ledger")
    try:
        return verify_claim_evidence_ledger(
            root,
            release_candidate=release_candidate,
        )
    except ClaimEvidenceLedgerError as exc:
        raise ReleaseStateError(f"claim/evidence ledger failed: {exc}") from exc


def _validate_production_code_inventory(
    root: Path,
    state: Mapping[str, Any],
) -> Mapping[str, object]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    if manifests.get("production_code_inventory") != (
        "release/production-code-inventory.json"
    ):
        raise ReleaseStateError("release state does not bind the code inventory")
    try:
        return verify_production_code_inventory(root)
    except ProductionCodeInventoryError as exc:
        raise ReleaseStateError(f"production code inventory failed: {exc}") from exc


def _validate_production_migration_contract(
    root: Path,
    state: Mapping[str, Any],
) -> Mapping[str, int | str]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    if manifests.get("production_migration_contract") != (
        "release/production-migration-contract.json"
    ):
        raise ReleaseStateError("release state does not bind the migration contract")
    try:
        return verify_production_migration_contract(root)
    except ProductionMigrationContractError as exc:
        raise ReleaseStateError(f"production migration contract failed: {exc}") from exc


def _validate_secret_rotation_contract(
    root: Path,
    state: Mapping[str, Any],
) -> Mapping[str, int | str]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    if manifests.get("secret_rotation_contract") != (
        "release/secret-rotation-contract.json"
    ):
        raise ReleaseStateError("release state does not bind the rotation contract")
    try:
        return verify_secret_rotation_contract(root)
    except SecretRotationContractError as exc:
        raise ReleaseStateError(f"secret rotation contract failed: {exc}") from exc


def _validate_tenant_quota_contract(
    root: Path,
    state: Mapping[str, Any],
) -> Mapping[str, int | str]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    if manifests.get("tenant_quota_contract") != (
        "release/tenant-quota-contract.json"
    ):
        raise ReleaseStateError("release state does not bind the tenant quota contract")
    try:
        return verify_tenant_quota_contract(root)
    except TenantQuotaContractError as exc:
        raise ReleaseStateError(f"tenant quota contract failed: {exc}") from exc


def _validate_production_api_contract(
    root: Path,
    state: Mapping[str, Any],
) -> Mapping[str, object]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    if manifests.get("production_api_contract") != (
        "release/production-api-contract.json"
    ) or manifests.get("production_openapi_schema") != (
        "release/production-openapi-schema.json"
    ):
        raise ReleaseStateError("release state does not bind the production API packet")
    try:
        checks = verify_production_api_contract(root)
    except ProductionApiContractError as exc:
        raise ReleaseStateError(f"production API contract failed: {exc}") from exc
    contract = _mapping(
        _read_json(
            root / manifests["production_api_contract"],
            label="production API contract",
        ),
        label="production API contract",
    )
    return {
        "checks": len(checks),
        "operations": int(contract["operation_count"]),
        "lifecycle_status": str(contract["lifecycle_status"]),
    }


def _validate_production_onboarding_contract(
    root: Path,
    state: Mapping[str, Any],
) -> Mapping[str, object]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    if manifests.get("production_onboarding_contract") != (
        "release/production-onboarding-contract.json"
    ):
        raise ReleaseStateError("release state does not bind onboarding readiness")
    try:
        checks = verify_production_onboarding_contract(root)
    except ProductionOnboardingContractError as exc:
        raise ReleaseStateError(f"production onboarding contract failed: {exc}") from exc
    contract = _mapping(
        _read_json(
            root / manifests["production_onboarding_contract"],
            label="production onboarding contract",
        ),
        label="production onboarding contract",
    )
    rows = contract.get("checks")
    if not isinstance(rows, list):
        raise ReleaseStateError("production onboarding checks are malformed")
    return {"verifier_checks": len(checks), "domains": len(rows)}


def _validate_production_identity_posture_contract(
    root: Path,
    state: Mapping[str, Any],
) -> Mapping[str, object]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    expected_path = "release/production-identity-posture-contract.json"
    if manifests.get("production_identity_posture_contract") != expected_path:
        raise ReleaseStateError(
            "release state does not bind the production identity posture contract"
        )
    contract_path = _repo_path(
        root,
        manifests["production_identity_posture_contract"],
        label="production identity posture contract",
    )
    contract = _mapping(
        _read_json(contract_path, label="production identity posture contract"),
        label="production identity posture contract",
    )
    if contract != _PRODUCTION_IDENTITY_POSTURE_CONTRACT:
        raise ReleaseStateError(
            "production identity posture contract differs from the reviewed boundary"
        )
    return {
        "provider_count": len(contract["supported_providers"]),
        "selection_cardinality": contract["selection_cardinality"],
    }


def _validate_production_data_boundary(
    root: Path,
    state: Mapping[str, Any],
) -> Mapping[str, object]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    if manifests.get("production_data_boundary") != (
        "release/production-data-boundary.json"
    ):
        raise ReleaseStateError("release state does not bind the production data boundary")
    try:
        return verify_production_data_boundary(root)
    except DataBoundaryError as exc:
        raise ReleaseStateError(f"production data boundary failed: {exc}") from exc


def _validate_production_operations_contract(
    root: Path,
    state: Mapping[str, Any],
) -> Mapping[str, object]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    if manifests.get("production_operations_contract") != (
        "release/production-operations-contract.json"
    ):
        raise ReleaseStateError("release state does not bind production operations")
    try:
        return verify_production_operations_contract(root)
    except OperationsContractError as exc:
        raise ReleaseStateError(f"production operations contract failed: {exc}") from exc


def _validate_production_qa_assurance(
    root: Path,
    state: Mapping[str, Any],
) -> Mapping[str, object]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    if manifests.get("production_qa_assurance") != (
        "release/production-qa-assurance.json"
    ):
        raise ReleaseStateError("release state does not bind production QA assurance")
    try:
        checks = verify_production_qa_assurance(root)
    except ProductionQaAssuranceError as exc:
        raise ReleaseStateError(f"production QA assurance failed: {exc}") from exc
    contract = _mapping(
        _read_json(
            root / manifests["production_qa_assurance"],
            label="production QA assurance",
        ),
        label="production QA assurance",
    )
    statuses = contract.get("gate_statuses")
    if not isinstance(statuses, Mapping):
        raise ReleaseStateError("production QA gate statuses are malformed")
    return {"verifier_checks": len(checks), "gate_statuses": len(statuses)}


def _validate_production_runtime_contract(
    root: Path,
    state: Mapping[str, Any],
) -> Mapping[str, object]:
    manifests = _mapping(state["manifests"], label="release-state manifests")
    if manifests.get("production_runtime_contract") != (
        "release/production-runtime-contract.json"
    ):
        raise ReleaseStateError("release state does not bind the runtime contract")
    try:
        return verify_production_runtime_contract(root)
    except ProductionRuntimeContractVerificationError as exc:
        raise ReleaseStateError(f"production runtime contract failed: {exc}") from exc


def _validate_latest_tag(state: Mapping[str, Any]) -> Mapping[str, Any]:
    latest = _exact_keys(
        state.get("latest_canonical_tag"),
        {"name", "commit"},
        label="latest canonical tag",
    )
    if not isinstance(latest["name"], str):
        raise ReleaseStateError("latest canonical tag name must be a string")
    try:
        validate_tag(latest["name"])
    except ValueError as exc:
        raise ReleaseStateError("latest canonical tag is not canonical") from exc
    if _COMMIT_RE.fullmatch(str(latest["commit"])) is None:
        raise ReleaseStateError("latest canonical tag commit must be a full SHA")
    return latest


def _validate_git_release_state(
    root: Path,
    state: Mapping[str, Any],
    *,
    version: str,
    latest: Mapping[str, Any],
    release_tag: str | None,
) -> None:
    canonical_tags, noncanonical_tags = _tag_inventory(root)
    known_noncanonical = state["known_noncanonical_tags"]
    if not isinstance(known_noncanonical, list) or any(
        not isinstance(tag, str) for tag in known_noncanonical
    ):
        raise ReleaseStateError("known noncanonical tags must be a string list")
    if noncanonical_tags != sorted(known_noncanonical):
        raise ReleaseStateError(
            "noncanonical repository tags differ from release state: "
            f"actual={noncanonical_tags}, declared={sorted(known_noncanonical)}"
        )

    if release_tag is None:
        if not canonical_tags or canonical_tags[0] != latest["name"]:
            raise ReleaseStateError("latest local canonical tag differs from release state")
        if state["source_state"] == "unreleased" and f"v{version}" in canonical_tags:
            raise ReleaseStateError("unreleased source version already has a canonical tag")
    else:
        try:
            release = validate_tag(release_tag)
        except ValueError as exc:
            raise ReleaseStateError("candidate release tag is not canonical") from exc
        if release.version != version:
            raise ReleaseStateError("candidate release tag differs from source version")
        if release_tag not in canonical_tags:
            raise ReleaseStateError("candidate release tag is absent from local Git")
        if _tag_commit(root, release_tag) != _git(root, "rev-parse", "HEAD"):
            raise ReleaseStateError("candidate release tag does not resolve to checkout HEAD")
        prior_tags = [tag for tag in canonical_tags if tag != release_tag]
        if not prior_tags or prior_tags[0] != latest["name"]:
            raise ReleaseStateError("latest prior canonical tag differs from release state")
    if _tag_commit(root, str(latest["name"])) != latest["commit"]:
        raise ReleaseStateError("latest canonical tag commit differs from release state")


def _validate_github_release_observation(
    state: Mapping[str, Any],
    *,
    github_releases: object | None,
    release_tag: str | None,
    require_candidate_release: bool,
) -> list[dict[str, object]]:
    observation = _exact_keys(
        state.get("github_release_observation"),
        {"observed_at", "releases"},
        label="GitHub release observation",
    )
    if not isinstance(observation["observed_at"], str) or not observation["observed_at"].endswith("Z"):
        raise ReleaseStateError("GitHub release observation timestamp must be UTC")
    declared_releases = _normalize_release_list(observation["releases"])
    if github_releases is not None:
        observed_releases = _normalize_release_list(github_releases)
        candidate: list[dict[str, object]] = []
        if release_tag is not None:
            candidate = [
                item for item in observed_releases if item["tag"] == release_tag
            ]
            observed_releases = [
                item for item in observed_releases if item["tag"] != release_tag
            ]
            if candidate:
                try:
                    expected_prerelease = validate_tag(release_tag).prerelease
                except ValueError as exc:
                    raise ReleaseStateError(
                        "candidate GitHub release tag is not canonical"
                    ) from exc
                if candidate[0]["prerelease"] is not expected_prerelease:
                    raise ReleaseStateError(
                        "candidate GitHub release prerelease state differs from its tag"
                    )
        if require_candidate_release and len(candidate) != 1:
            raise ReleaseStateError(
                "fresh GitHub inventory omits the exact candidate release"
            )
        if observed_releases != declared_releases:
            raise ReleaseStateError(
                "fresh GitHub release inventory differs from release-state snapshot"
            )
    if state["source_state"] == "unreleased" and declared_releases:
        raise ReleaseStateError("unreleased source state contradicts GitHub Releases")
    return declared_releases


def _require_release_ready(
    state: Mapping[str, Any],
    *,
    release_tag: str | None,
    production_eligible: bool,
) -> None:
    if release_tag is None:
        raise ReleaseStateError("release-ready verification requires --release-tag")
    if state["source_state"] != "release_candidate":
        raise ReleaseStateError("source is not declared as a release candidate")
    if not production_eligible:
        raise ReleaseStateError("production-surface manifest is not release eligible")


def verify_release_state(
    root: Path,
    *,
    release_tag: str | None = None,
    require_release_ready: bool = False,
    github_releases: object | None = None,
    require_candidate_release: bool = False,
) -> dict[str, object]:
    """Return a deterministic reconciliation summary or raise fail closed."""
    if require_candidate_release and (
        github_releases is None or release_tag is None
    ):
        raise ReleaseStateError(
            "candidate GitHub release verification requires a tag and fresh inventory"
        )
    root = root.resolve()
    state_path = root / "release" / "release-state.json"
    state = _exact_keys(
        _read_json(state_path, label="release state"),
        {
            "schema",
            "repository",
            "source_version",
            "source_state",
            "latest_canonical_tag",
            "known_noncanonical_tags",
            "github_release_observation",
            "security_support",
            "manifests",
            "independent_distributions",
        },
        label="release state",
    )
    if state["schema"] != SCHEMA:
        raise ReleaseStateError("unsupported release-state schema")
    if state["source_state"] not in {"unreleased", "release_candidate", "released"}:
        raise ReleaseStateError("invalid source release state")

    version, cohort_names = _validate_cohort(root, state)
    independent = _validate_independent_distributions(root, state)
    production_eligible = _validate_production_surface(
        root,
        state,
        cohort_names,
        independent,
    )
    readiness = _validate_production_readiness(
        root,
        state,
        require_ready=False,
    )
    pytest_exceptions = _validate_pytest_exceptions(root, state)
    model_policy = _validate_production_model_policy(
        root,
        state,
        release_candidate=False,
    )
    support_matrix = _validate_production_support_matrix(root, state)
    dependency_contract = _validate_dependency_failure_contract(root, state)
    service_objectives = _validate_service_objectives(
        root,
        state,
        release_candidate=False,
    )
    claim_ledger = _validate_claim_evidence_ledger(
        root,
        state,
        release_candidate=False,
    )
    code_inventory = _validate_production_code_inventory(root, state)
    migration_contract = _validate_production_migration_contract(root, state)
    rotation_contract = _validate_secret_rotation_contract(root, state)
    quota_contract = _validate_tenant_quota_contract(root, state)
    api_contract = _validate_production_api_contract(root, state)
    onboarding_contract = _validate_production_onboarding_contract(root, state)
    identity_contract = _validate_production_identity_posture_contract(root, state)
    data_boundary = _validate_production_data_boundary(root, state)
    operations_contract = _validate_production_operations_contract(root, state)
    qa_assurance = _validate_production_qa_assurance(root, state)
    runtime_contract = _validate_production_runtime_contract(root, state)
    _validate_support_policy(root, state)

    latest = _validate_latest_tag(state)
    _validate_git_release_state(
        root,
        state,
        version=version,
        latest=latest,
        release_tag=release_tag,
    )
    declared_releases = _validate_github_release_observation(
        state,
        github_releases=github_releases,
        release_tag=release_tag,
        require_candidate_release=require_candidate_release,
    )
    if require_release_ready:
        _require_release_ready(
            state,
            release_tag=release_tag,
            production_eligible=production_eligible,
        )
        readiness = _validate_production_readiness(
            root,
            state,
            require_ready=True,
        )
        model_policy = _validate_production_model_policy(
            root,
            state,
            release_candidate=True,
        )
        service_objectives = _validate_service_objectives(
            root,
            state,
            release_candidate=True,
        )
        claim_ledger = _validate_claim_evidence_ledger(
            root,
            state,
            release_candidate=True,
        )

    return {
        "repository": state["repository"],
        "source_version": version,
        "source_state": state["source_state"],
        "latest_canonical_tag": latest["name"],
        "latest_canonical_commit": latest["commit"],
        "github_release_count": len(declared_releases),
        "python_distribution_count": len(cohort_names),
        "independent_distribution_count": len(independent),
        "production_release_eligible": production_eligible,
        "production_readiness_status": readiness["status"],
        "pytest_exception_count": pytest_exceptions["occurrence_count"],
        "production_critical_pytest_exception_count": pytest_exceptions[
            "production_critical_count"
        ],
        "production_model_policy_status": model_policy["status"],
        "production_support_matrix_rows": int(
            support_matrix[0].split()[1]
        ),
        "dependency_failure_contract_rows": dependency_contract["dependencies"],
        "claim_evidence_ledger_status": claim_ledger["status"],
        "claim_evidence_ledger_claims": claim_ledger["claims"],
        "production_code_inventory_tests": code_inventory[
            "declared_test_function_count"
        ],
        "production_migration_contract_status": migration_contract["status"],
        "secret_rotation_contract_rows": rotation_contract["secret_rows"],
        "tenant_quota_contract_resources": quota_contract["resources"],
        "production_api_contract_operations": api_contract["operations"],
        "production_onboarding_domains": onboarding_contract["domains"],
        "production_identity_provider_count": identity_contract["provider_count"],
        "production_data_boundary_flows": data_boundary["data_flows"],
        "production_operations_alerts": operations_contract["alerts"],
        "production_qa_gate_statuses": qa_assurance["gate_statuses"],
        "production_runtime_contract_status": runtime_contract["status"],
        "service_objectives_status": service_objectives["status"],
        "production_unresolved_required_gates": readiness[
            "unresolved_required_gates"
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--release-tag",
        help="candidate tag at checkout HEAD; the recorded latest tag remains its predecessor",
    )
    parser.add_argument(
        "--require-release-ready",
        action="store_true",
        help="also require release-candidate state and production eligibility",
    )
    parser.add_argument(
        "--github-releases-json",
        type=Path,
        help="optional deterministic input exported by gh release list --json",
    )
    parser.add_argument(
        "--require-candidate-github-release",
        action="store_true",
        help=(
            "require the fresh GitHub inventory to contain the exact candidate tag; "
            "all other releases must still equal the reviewed snapshot"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    github_releases = None
    if args.github_releases_json is not None:
        github_releases = _read_json(
            args.github_releases_json,
            label="fresh GitHub release inventory",
        )
    if args.require_candidate_github_release and (
        args.github_releases_json is None or args.release_tag is None
    ):
        raise SystemExit(
            "--require-candidate-github-release requires --release-tag and "
            "--github-releases-json"
        )
    try:
        summary = verify_release_state(
            args.root,
            release_tag=args.release_tag,
            require_release_ready=args.require_release_ready,
            github_releases=github_releases,
            require_candidate_release=args.require_candidate_github_release,
        )
    except ReleaseStateError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
