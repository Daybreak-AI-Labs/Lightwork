"""Fail-closed production onboarding readiness report.

The ordinary onboarding wizard and ``maverick preflight`` are evaluation and
developer conveniences.  They cannot establish a production deployment's
identity, isolation, custody, migration, or operational evidence.  This module
evaluates an exact release contract plus explicit, digest-addressed operator
observations and reports ``pass``, ``fail``, or ``not_checked`` for every
required production domain.  It performs no network requests and creates no
state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

CONTRACT_SCHEMA = "lightwork.production-onboarding-contract.v2"
OBSERVATIONS_SCHEMA = "lightwork.production-onboarding-observations.v2"
EVIDENCE_MANIFEST_SCHEMA = "lightwork.production-onboarding-evidence-manifest.v2"
REPORT_SCHEMA = "lightwork.production-onboarding-report.v2"
CONTRACT_ENV = "MAVERICK_PRODUCTION_ONBOARDING_CONTRACT"
CONTRACT_DIGEST_ENV = "MAVERICK_PRODUCTION_ONBOARDING_CONTRACT_SHA256"
RELEASE_STATE_ENV = "MAVERICK_PRODUCTION_RELEASE_STATE"
RELEASE_STATE_DIGEST_ENV = "MAVERICK_PRODUCTION_RELEASE_STATE_SHA256"
SOURCE_REVISION_ENV = "MAVERICK_SOURCE_REVISION"
AUTHORITY_KEY_ID_ENV = "MAVERICK_PRODUCTION_ONBOARDING_AUTHORITY_KEY_ID"
AUTHORITY_PUBLIC_KEY_ENV = "MAVERICK_PRODUCTION_ONBOARDING_AUTHORITY_PUBLIC_KEY"
DEPLOYMENT_ID_ENV = "MAVERICK_PRODUCTION_DEPLOYMENT_ID"
DEPLOYMENT_CONFIG_DIGEST_ENV = "MAVERICK_PRODUCTION_DEPLOYMENT_CONFIG_SHA256"
INSTALLER_PROFILE_ENV = "MAVERICK_INSTALLER_PROFILE"
OBSERVATIONS_ENV = "MAVERICK_PRODUCTION_ONBOARDING_OBSERVATIONS"
_REVISION_RE = re.compile(r"[0-9a-f]{40}")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_SIGNATURE_RE = re.compile(r"[0-9a-f]{128}")
_REFERENCE_RE = re.compile(r"urn:[A-Za-z0-9][A-Za-z0-9:._/-]{2,508}")
_STATUS_VALUES = frozenset({"pass", "fail", "not_checked"})
EVIDENCE_AUTHORITY_POLICY = {
    "manifest_schema": EVIDENCE_MANIFEST_SCHEMA,
    "signature_algorithm": "Ed25519",
    "authority_custody": "out-of-band-pinned-public-key",
    "contract_binding": "exact-byte-sha256",
    "release_state_binding": "exact-byte-sha256-release-candidate",
    "source_revision_binding": "trusted-runtime-revision",
    "deployment_identity_binding": (
        "out-of-band-pinned-deployment-id-and-config-sha256"
    ),
    "identity_posture_binding": (
        "signed-check-row-to-out-of-band-provider-and-posture-sha256"
    ),
    "requirement_coverage": "exactly-once-per-contract-requirement",
    "duplicate_replay_policy": (
        "reject-within-manifest-evidence-id-and-artifact-reuse"
    ),
}

Status = Literal["pass", "fail", "not_checked"]


class ProductionOnboardingError(ValueError):
    """The onboarding contract or supplied observations are ambiguous."""


@dataclass(frozen=True)
class EvidenceReference:
    evidence_id: str
    requirement: str
    reference: str
    sha256: str


@dataclass(frozen=True)
class CheckResult:
    id: str
    label: str
    status: Status
    required: bool
    detail: str
    checked_at: str | None
    expires_at: str | None
    evidence: tuple[EvidenceReference, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence"] = [asdict(item) for item in self.evidence]
        return value


@dataclass(frozen=True)
class ReadinessReport:
    schema: str
    contract_schema: str
    profile_id: str
    source_version: str
    source_revision: str | None
    deployment_id: str | None
    deployment_config_sha256: str | None
    contract_sha256: str
    release_state_sha256: str | None
    evidence_manifest_sha256: str | None
    authority_key_id: str | None
    generated_at: str
    release_eligible: bool
    ready: bool
    pass_count: int
    fail_count: int
    not_checked_count: int
    checks: tuple[CheckResult, ...]
    blockers: tuple[str, ...]
    boundary: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["checks"] = [item.to_dict() for item in self.checks]
        value["blockers"] = list(self.blockers)
        return value


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
            raise ProductionOnboardingError(
                f"{label} is missing or not a non-empty regular file"
            )
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except ProductionOnboardingError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProductionOnboardingError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProductionOnboardingError(f"{label} must contain one JSON object")
    return value


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProductionOnboardingError(
            "evidence manifest is not canonical JSON"
        ) from exc


def _file_sha256(path: Path, *, label: str) -> str:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise ProductionOnboardingError(
                f"{label} is missing or not a non-empty regular file"
            )
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except ProductionOnboardingError:
        raise
    except OSError as exc:
        raise ProductionOnboardingError(f"{label} digest could not be read") from exc


def _source_contract() -> Path | None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "release" / "production-onboarding-contract.json"
        if candidate.is_file():
            return candidate
    return None


def _source_release_state() -> Path | None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "release" / "release-state.json"
        if candidate.is_file():
            return candidate
    return None


def contract_path() -> Path:
    configured = os.environ.get(CONTRACT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    installed = Path("/etc/lightwork/production-onboarding-contract.json")
    if installed.is_file():
        return installed
    source = _source_contract()
    if source is not None:
        return source
    raise ProductionOnboardingError(
        f"production onboarding contract is missing; set {CONTRACT_ENV}"
    )


def release_state_path() -> Path:
    configured = os.environ.get(RELEASE_STATE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    installed = Path("/etc/lightwork/release-state.json")
    if installed.is_file():
        return installed
    source = _source_release_state()
    if source is not None:
        return source
    raise ProductionOnboardingError(
        f"production release state is missing; set {RELEASE_STATE_ENV}"
    )


def _validate_digest_custody(
    path: Path,
    *,
    environment: str,
    label: str,
    required: bool,
) -> str:
    expected = os.environ.get(environment, "").strip()
    production_required = os.environ.get(
        "MAVERICK_REQUIRE_PRODUCTION_SURFACE", ""
    ).strip().lower() in {"1", "true", "yes", "on", "enable", "enabled", "y", "t"}
    if not expected:
        if production_required or required:
            raise ProductionOnboardingError(
                f"{label} custody is missing {environment}"
            )
        return _file_sha256(path, label=label)
    if _DIGEST_RE.fullmatch(expected) is None:
        raise ProductionOnboardingError(
            f"{environment} must be 64 lowercase hex characters"
        )
    actual = _file_sha256(path, label=label)
    if actual != expected:
        raise ProductionOnboardingError(
            f"{label} digest does not match its custody assertion"
        )
    return actual


def _exact_mapping(
    value: object,
    expected: set[str],
    *,
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionOnboardingError(f"{label} must be an object")
    if set(value) != expected:
        raise ProductionOnboardingError(
            f"{label} fields differ: missing={sorted(expected - set(value))}, "
            f"unknown={sorted(set(value) - expected)}"
        )
    return value


def _timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ProductionOnboardingError(f"{label} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProductionOnboardingError(
            f"{label} must be an RFC3339 UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ProductionOnboardingError(f"{label} must be UTC")
    return parsed


def _trusted_authority() -> tuple[str, str]:
    key_id = os.environ.get(AUTHORITY_KEY_ID_ENV, "").strip()
    public_key = os.environ.get(AUTHORITY_PUBLIC_KEY_ENV, "").strip()
    if (
        not key_id
        or _REFERENCE_RE.fullmatch(key_id) is None
        or _DIGEST_RE.fullmatch(public_key) is None
        or public_key == "0" * 64
    ):
        raise ProductionOnboardingError(
            "production onboarding requires one out-of-band trusted Ed25519 authority"
        )
    return key_id, public_key


def _verify_authority_signature(
    manifest_bytes: bytes,
    authority: object,
) -> str:
    row = _exact_mapping(
        authority,
        {"algorithm", "key_id", "signature"},
        label="production onboarding authority",
    )
    trusted_key_id, trusted_public_key = _trusted_authority()
    signature = row.get("signature")
    if (
        row.get("algorithm") != "Ed25519"
        or row.get("key_id") != trusted_key_id
        or not isinstance(signature, str)
        or _SIGNATURE_RE.fullmatch(signature) is None
    ):
        raise ProductionOnboardingError(
            "onboarding evidence is not signed by the trusted authority"
        )
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        Ed25519PublicKey.from_public_bytes(bytes.fromhex(trusted_public_key)).verify(
            bytes.fromhex(signature),
            manifest_bytes,
        )
    except (ImportError, InvalidSignature, ValueError) as exc:
        raise ProductionOnboardingError(
            "onboarding evidence authority signature is invalid"
        ) from exc
    return trusted_key_id


def _contract_checks(contract: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    expected_keys = {
        "schema",
        "repository",
        "profile_id",
        "source_version",
        "release_eligible",
        "status",
        "max_observation_age_days",
        "checks",
        "repository_contracts",
        "source_bindings",
        "boundary",
        "evidence_authority",
    }
    _exact_mapping(contract, expected_keys, label="production onboarding contract")
    if contract.get("schema") != CONTRACT_SCHEMA:
        raise ProductionOnboardingError("unsupported production onboarding contract")
    if contract.get("evidence_authority") != EVIDENCE_AUTHORITY_POLICY:
        raise ProductionOnboardingError("onboarding evidence authority policy drifted")
    if not isinstance(contract.get("release_eligible"), bool):
        raise ProductionOnboardingError("contract release_eligible must be boolean")
    maximum = contract.get("max_observation_age_days")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 31:
        raise ProductionOnboardingError("max_observation_age_days must be 1..31")
    rows = contract.get("checks")
    if not isinstance(rows, list) or not rows:
        raise ProductionOnboardingError("contract checks must be a non-empty array")
    normalized: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        row = _exact_mapping(
            raw,
            {
                "id",
                "label",
                "required",
                "external_observation_required",
                "minimum_evidence_items",
                "repository_contract_refs",
                "evidence_requirements",
                "runtime_binding",
            },
            label=f"contract checks[{index}]",
        )
        check_id = row.get("id")
        refs = row.get("repository_contract_refs")
        requirements = row.get("evidence_requirements")
        minimum = row.get("minimum_evidence_items")
        runtime_binding = row.get("runtime_binding")
        if (
            not isinstance(check_id, str)
            or not check_id
            or check_id in seen
            or not isinstance(row.get("label"), str)
            or not str(row.get("label")).strip()
            or row.get("required") is not True
            or row.get("external_observation_required") is not True
            or not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or minimum < 1
            or not isinstance(refs, list)
            or not refs
            or any(not isinstance(item, str) or not item for item in refs)
            or not isinstance(requirements, list)
            or not requirements
            or any(not isinstance(item, str) or not item for item in requirements)
            or (
                check_id == "identity_auth"
                and runtime_binding != "identity-provider-and-posture-sha256"
            )
            or (check_id != "identity_auth" and runtime_binding is not None)
        ):
            raise ProductionOnboardingError(
                f"contract checks[{index}] is incomplete or malformed"
            )
        seen.add(check_id)
        normalized.append(row)
    return tuple(normalized)


def _observation_rows(
    observations: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    contract_sha256: str,
    release_state: Mapping[str, Any],
    release_state_sha256: str,
    now: datetime,
) -> tuple[str, str, str, str, str, dict[str, Mapping[str, Any]]]:
    _exact_mapping(
        observations,
        {
            "schema",
            "evidence_manifest",
            "evidence_manifest_sha256",
            "authority",
        },
        label="production onboarding observations",
    )
    manifest = _exact_mapping(
        observations.get("evidence_manifest"),
        {
            "schema",
            "profile_id",
            "source_version",
            "source_revision",
            "deployment_id",
            "deployment_config_sha256",
            "contract_sha256",
            "release_state_sha256",
            "release_state_source_state",
            "nonce",
            "issued_at",
            "expires_at",
            "checks",
        },
        label="production onboarding evidence manifest",
    )
    manifest_bytes = _canonical(manifest)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    claimed_manifest_digest = observations.get("evidence_manifest_sha256")
    if (
        not isinstance(claimed_manifest_digest, str)
        or _DIGEST_RE.fullmatch(claimed_manifest_digest) is None
        or claimed_manifest_digest != manifest_digest
    ):
        raise ProductionOnboardingError(
            "evidence manifest digest does not match its exact canonical bytes"
        )
    authority_key_id = _verify_authority_signature(
        manifest_bytes,
        observations.get("authority"),
    )
    revision = manifest.get("source_revision")
    trusted_revision = os.environ.get(SOURCE_REVISION_ENV, "").strip()
    deployment_id = manifest.get("deployment_id")
    trusted_deployment_id = os.environ.get(DEPLOYMENT_ID_ENV, "").strip()
    deployment_config_sha256 = manifest.get("deployment_config_sha256")
    trusted_deployment_config_sha256 = os.environ.get(
        DEPLOYMENT_CONFIG_DIGEST_ENV,
        "",
    ).strip()
    nonce = manifest.get("nonce")
    if (
        observations.get("schema") != OBSERVATIONS_SCHEMA
        or manifest.get("schema") != EVIDENCE_MANIFEST_SCHEMA
        or manifest.get("profile_id") != contract.get("profile_id")
        or manifest.get("source_version") != contract.get("source_version")
        or manifest.get("contract_sha256") != contract_sha256
        or manifest.get("release_state_sha256") != release_state_sha256
        or manifest.get("release_state_source_state")
        != release_state.get("source_state")
        or release_state.get("schema") != "lightwork.release-state.v1"
        or release_state.get("repository") != contract.get("repository")
        or release_state.get("source_version") != contract.get("source_version")
        or release_state.get("source_state") != "release_candidate"
        or not isinstance(revision, str)
        or _REVISION_RE.fullmatch(revision) is None
        or revision == "0" * 40
        or _REVISION_RE.fullmatch(trusted_revision) is None
        or trusted_revision == "0" * 40
        or revision != trusted_revision
        or not isinstance(deployment_id, str)
        or _REFERENCE_RE.fullmatch(deployment_id) is None
        or _REFERENCE_RE.fullmatch(trusted_deployment_id) is None
        or deployment_id != trusted_deployment_id
        or not isinstance(deployment_config_sha256, str)
        or _DIGEST_RE.fullmatch(deployment_config_sha256) is None
        or deployment_config_sha256 == "0" * 64
        or _DIGEST_RE.fullmatch(trusted_deployment_config_sha256) is None
        or trusted_deployment_config_sha256 == "0" * 64
        or deployment_config_sha256 != trusted_deployment_config_sha256
        or not isinstance(nonce, str)
        or _DIGEST_RE.fullmatch(nonce) is None
        or nonce == "0" * 64
    ):
        raise ProductionOnboardingError(
            "signed evidence does not bind exact contract, release state, revision, "
            "and deployment target"
        )
    issued = _timestamp(manifest.get("issued_at"), label="evidence_manifest.issued_at")
    expires = _timestamp(
        manifest.get("expires_at"),
        label="evidence_manifest.expires_at",
    )
    maximum = int(contract["max_observation_age_days"])
    if (
        issued > now
        or expires <= now
        or expires <= issued
        or (expires - issued).total_seconds() > maximum * 86400
    ):
        raise ProductionOnboardingError(
            "signed evidence manifest is future-dated, expired, or overlong"
        )
    rows = manifest.get("checks")
    if not isinstance(rows, list):
        raise ProductionOnboardingError("observation checks must be an array")
    result: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(rows):
        row = _exact_mapping(
            raw,
            {
                "id",
                "status",
                "checked_at",
                "expires_at",
                "evidence",
                "runtime_binding",
            },
            label=f"observation checks[{index}]",
        )
        check_id = row.get("id")
        status = row.get("status")
        if (
            not isinstance(check_id, str)
            or not check_id
            or check_id in result
            or status not in _STATUS_VALUES
        ):
            raise ProductionOnboardingError(
                f"observation checks[{index}] has an invalid identity or status"
            )
        binding = row.get("runtime_binding")
        if check_id == "identity_auth":
            identity = _exact_mapping(
                binding,
                {"provider", "posture_sha256"},
                label="identity_auth runtime binding",
            )
            try:
                from .production_identity import validate_runtime_identity_posture

                current_posture = validate_runtime_identity_posture()
            except Exception as exc:
                raise ProductionOnboardingError(
                    "current runtime identity/RBAC posture does not match its trusted binding"
                ) from exc
            if (
                identity.get("provider") != current_posture.provider
                or identity.get("posture_sha256") != current_posture.sha256
            ):
                raise ProductionOnboardingError(
                    "signed identity_auth binding differs from trusted runtime values"
                )
        elif binding is not None:
            raise ProductionOnboardingError(
                f"{check_id} carries an unsupported runtime binding"
            )
        result[check_id] = row
    return (
        revision,
        deployment_id,
        deployment_config_sha256,
        manifest_digest,
        authority_key_id,
        result,
    )


def _evidence(
    value: object,
    *,
    label: str,
    check_id: str,
    expected_requirements: set[str],
    seen_evidence_ids: set[str],
    seen_artifacts: set[tuple[str, str]],
) -> tuple[EvidenceReference, ...]:
    if not isinstance(value, list):
        raise ProductionOnboardingError(f"{label} must be an array")
    result: list[EvidenceReference] = []
    requirements: set[str] = set()
    for index, raw in enumerate(value):
        row = _exact_mapping(
            raw,
            {"evidence_id", "requirement", "reference", "sha256"},
            label=f"{label}[{index}]",
        )
        evidence_id = row.get("evidence_id")
        requirement = row.get("requirement")
        reference = row.get("reference")
        digest = row.get("sha256")
        identity = (str(reference), str(digest))
        expected_evidence_id = hashlib.sha256(
            "\0".join(
                (check_id, str(requirement), str(reference), str(digest))
            ).encode("utf-8")
        ).hexdigest()
        if (
            not isinstance(evidence_id, str)
            or _DIGEST_RE.fullmatch(evidence_id) is None
            or evidence_id != expected_evidence_id
            or evidence_id in seen_evidence_ids
            or not isinstance(requirement, str)
            or requirement not in expected_requirements
            or requirement in requirements
            or not isinstance(reference, str)
            or _REFERENCE_RE.fullmatch(reference) is None
            or not isinstance(digest, str)
            or _DIGEST_RE.fullmatch(digest) is None
            or digest == "0" * 64
            or identity in seen_artifacts
        ):
            raise ProductionOnboardingError(
                f"{label}[{index}] must uniquely cover one exact requirement"
            )
        seen_evidence_ids.add(evidence_id)
        seen_artifacts.add(identity)
        requirements.add(requirement)
        result.append(EvidenceReference(evidence_id, requirement, reference, digest))
    if result and requirements != expected_requirements:
        raise ProductionOnboardingError(
            f"{label} does not cover every exact contract requirement"
        )
    return tuple(result)


def _evaluate_row(
    contract_row: Mapping[str, Any],
    observation: Mapping[str, Any] | None,
    *,
    now: datetime,
    max_age_days: int,
    seen_evidence_ids: set[str],
    seen_artifacts: set[tuple[str, str]],
) -> CheckResult:
    check_id = str(contract_row["id"])
    label = str(contract_row["label"])
    if observation is None:
        return CheckResult(
            check_id,
            label,
            "not_checked",
            True,
            "Required deployment evidence was not supplied.",
            None,
            None,
            (),
        )
    status = observation["status"]
    checked_raw = observation.get("checked_at")
    expires_raw = observation.get("expires_at")
    evidence = _evidence(
        observation.get("evidence"),
        label=f"{check_id} evidence",
        check_id=check_id,
        expected_requirements=set(contract_row["evidence_requirements"]),
        seen_evidence_ids=seen_evidence_ids,
        seen_artifacts=seen_artifacts,
    )
    if status == "not_checked":
        if checked_raw is not None or expires_raw is not None or evidence:
            raise ProductionOnboardingError(
                f"{check_id} not_checked observation must carry no pseudo-evidence"
            )
        return CheckResult(
            check_id,
            label,
            "not_checked",
            True,
            "Operator explicitly recorded that the required check was not run.",
            None,
            None,
            (),
        )

    checked = _timestamp(checked_raw, label=f"{check_id}.checked_at")
    expires = _timestamp(expires_raw, label=f"{check_id}.expires_at")
    checked_text = str(checked_raw)
    expires_text = str(expires_raw)
    if checked > now:
        return CheckResult(
            check_id,
            label,
            "fail",
            True,
            "Observation timestamp is in the future.",
            checked_text,
            expires_text,
            evidence,
        )
    if expires <= checked or (expires - checked).total_seconds() > max_age_days * 86400:
        return CheckResult(
            check_id,
            label,
            "fail",
            True,
            "Observation validity window is invalid or exceeds the contract maximum.",
            checked_text,
            expires_text,
            evidence,
        )
    if expires <= now:
        return CheckResult(
            check_id,
            label,
            "fail",
            True,
            "Deployment evidence is expired.",
            checked_text,
            expires_text,
            evidence,
        )
    if status == "fail":
        return CheckResult(
            check_id,
            label,
            "fail",
            True,
            "The deployment observation failed this required production check.",
            checked_text,
            expires_text,
            evidence,
        )
    minimum = max(
        int(contract_row["minimum_evidence_items"]),
        len(contract_row["evidence_requirements"]),
    )
    if len(evidence) < minimum:
        return CheckResult(
            check_id,
            label,
            "fail",
            True,
            f"A pass requires at least {minimum} immutable evidence item(s).",
            checked_text,
            expires_text,
            evidence,
        )
    return CheckResult(
        check_id,
        label,
        "pass",
        True,
        "Signed observation is current and covers every exact evidence requirement.",
        checked_text,
        expires_text,
        evidence,
    )


def evaluate(
    *,
    contract_file: Path | None = None,
    observations_file: Path | None = None,
    release_state_file: Path | None = None,
    now: datetime | None = None,
) -> ReadinessReport:
    """Evaluate all production domains without network calls or state creation."""

    selected_contract = contract_file or contract_path()
    contract_sha256 = _validate_digest_custody(
        selected_contract,
        environment=CONTRACT_DIGEST_ENV,
        label="production onboarding contract",
        required=observations_file is not None,
    )
    contract = _read_json(
        selected_contract,
        label="production onboarding contract",
    )
    rows = _contract_checks(contract)
    reference_now = now or datetime.now(timezone.utc)
    if reference_now.tzinfo is None:
        raise ProductionOnboardingError("evaluation time must be timezone-aware")
    reference_now = reference_now.astimezone(timezone.utc)
    generated_at = reference_now.isoformat(timespec="seconds").replace("+00:00", "Z")

    source_revision: str | None = None
    deployment_id: str | None = None
    deployment_config_sha256: str | None = None
    release_state_sha256: str | None = None
    evidence_manifest_sha256: str | None = None
    authority_key_id: str | None = None
    supplied: dict[str, Mapping[str, Any]] = {}
    if observations_file is not None:
        selected_release_state = release_state_file or release_state_path()
        release_state_sha256 = _validate_digest_custody(
            selected_release_state,
            environment=RELEASE_STATE_DIGEST_ENV,
            label="production release state",
            required=True,
        )
        release_state = _read_json(
            selected_release_state,
            label="production release state",
        )
        observations = _read_json(
            observations_file,
            label="production onboarding observations",
        )
        (
            source_revision,
            deployment_id,
            deployment_config_sha256,
            evidence_manifest_sha256,
            authority_key_id,
            supplied,
        ) = _observation_rows(
            observations,
            contract=contract,
            contract_sha256=contract_sha256,
            release_state=release_state,
            release_state_sha256=release_state_sha256,
            now=reference_now,
        )
    expected_ids = {str(row["id"]) for row in rows}
    unknown = sorted(set(supplied) - expected_ids)
    if unknown:
        raise ProductionOnboardingError(
            f"observations contain unreviewed check IDs: {unknown}"
        )

    maximum = int(contract["max_observation_age_days"])
    seen_evidence_ids: set[str] = set()
    seen_artifacts: set[tuple[str, str]] = set()
    evaluated: list[CheckResult] = []
    for row in rows:
        evaluated.append(
            _evaluate_row(
                row,
                supplied.get(str(row["id"])),
                now=reference_now,
                max_age_days=maximum,
                seen_evidence_ids=seen_evidence_ids,
                seen_artifacts=seen_artifacts,
            )
        )
    results = tuple(evaluated)
    pass_count = sum(item.status == "pass" for item in results)
    fail_count = sum(item.status == "fail" for item in results)
    not_checked_count = sum(item.status == "not_checked" for item in results)
    release_eligible = bool(contract["release_eligible"])
    blockers = [item.id for item in results if item.required and item.status != "pass"]
    if not release_eligible:
        blockers.insert(0, "release-contract-not-eligible")
    ready = release_eligible and not blockers
    return ReadinessReport(
        schema=REPORT_SCHEMA,
        contract_schema=CONTRACT_SCHEMA,
        profile_id=str(contract["profile_id"]),
        source_version=str(contract["source_version"]),
        source_revision=source_revision,
        deployment_id=deployment_id,
        deployment_config_sha256=deployment_config_sha256,
        contract_sha256=contract_sha256,
        release_state_sha256=release_state_sha256,
        evidence_manifest_sha256=evidence_manifest_sha256,
        authority_key_id=authority_key_id,
        generated_at=generated_at,
        release_eligible=release_eligible,
        ready=ready,
        pass_count=pass_count,
        fail_count=fail_count,
        not_checked_count=not_checked_count,
        checks=results,
        blockers=tuple(blockers),
        boundary=(
            "This report verifies one trusted-authority-signed evidence manifest "
            "against exact contract, release-state, and revision custody; it is not "
            "legal certification or proof of unsupplied live facts. The evaluator is "
            "stateless and does not prove nonce consumption across evaluations; a "
            "deployment authority must enforce cross-evaluation replay policy."
        ),
    )


def render_json(report: ReadinessReport) -> str:
    return json.dumps(
        report.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def evaluate_installer_completion(
    *,
    contract_file: Path | None = None,
    observations_file: Path | None = None,
    release_state_file: Path | None = None,
    now: datetime | None = None,
) -> ReadinessReport:
    """Evaluate the one authority allowed to finish a production install.

    Installer front ends pass an explicit observations path when they have
    one.  Desktop/bootstrap front ends that cannot add command-line arguments
    may instead provide :data:`OBSERVATIONS_ENV`.  An absent path is
    intentional: :func:`evaluate` then emits an all-``not_checked`` report and
    the caller must exit nonzero.  This helper never turns smoke, preflight, or
    successful configuration writes into production evidence.
    """

    selected_observations = observations_file
    if selected_observations is None:
        configured = os.environ.get(OBSERVATIONS_ENV, "").strip()
        if configured:
            selected_observations = Path(configured).expanduser()
    return evaluate(
        contract_file=contract_file,
        observations_file=selected_observations,
        release_state_file=release_state_file,
        now=now,
    )


__all__ = [
    "CONTRACT_ENV",
    "CONTRACT_DIGEST_ENV",
    "CONTRACT_SCHEMA",
    "EVIDENCE_MANIFEST_SCHEMA",
    "EVIDENCE_AUTHORITY_POLICY",
    "OBSERVATIONS_SCHEMA",
    "REPORT_SCHEMA",
    "RELEASE_STATE_DIGEST_ENV",
    "RELEASE_STATE_ENV",
    "SOURCE_REVISION_ENV",
    "AUTHORITY_KEY_ID_ENV",
    "AUTHORITY_PUBLIC_KEY_ENV",
    "DEPLOYMENT_CONFIG_DIGEST_ENV",
    "DEPLOYMENT_ID_ENV",
    "INSTALLER_PROFILE_ENV",
    "OBSERVATIONS_ENV",
    "CheckResult",
    "EvidenceReference",
    "ProductionOnboardingError",
    "ReadinessReport",
    "contract_path",
    "evaluate",
    "evaluate_installer_completion",
    "release_state_path",
    "render_json",
]
