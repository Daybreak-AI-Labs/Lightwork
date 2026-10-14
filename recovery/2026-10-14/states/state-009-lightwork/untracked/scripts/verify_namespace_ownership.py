#!/usr/bin/env python3
"""Fail closed unless every public package namespace has signed authority.

Public registry responses cannot prove who controls a project or which OIDC
publisher is configured.  This gate therefore requires a short-lived,
reviewer-signed statement backed by an SSH allowed-signers policy committed to
the release source.  It never interprets a registry 404 as ownership.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

SCHEMA = "lightwork.namespace-ownership-attestation.v1"
SIGNER_IDENTITY = "lightwork-release-namespace-owner"
SIGNATURE_NAMESPACE = "lightwork-release-namespace-v1"
_MAX_ATTESTATION_LIFETIME = timedelta(days=31)
_CLOCK_SKEW = timedelta(minutes=5)
_PLACEHOLDERS = {"", "UNVERIFIED", "REPLACE", "TODO", "UNKNOWN"}


class NamespaceOwnershipError(ValueError):
    """Namespace ownership or publisher authority is absent or malformed."""


def _mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NamespaceOwnershipError(f"{label} must be an object")
    return value


def _exact_keys(
    value: object,
    expected: set[str],
    *,
    label: str,
) -> Mapping[str, Any]:
    document = _mapping(value, label=label)
    if set(document) != expected:
        raise NamespaceOwnershipError(
            f"{label} fields differ: "
            f"missing={sorted(expected - set(document))}, "
            f"unknown={sorted(set(document) - expected)}"
        )
    return document


def _safe_file(path: Path, *, label: str) -> Path:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise NamespaceOwnershipError(f"{label} is missing or not a regular file: {path}")
    except OSError as exc:
        raise NamespaceOwnershipError(f"cannot inspect {label}: {path}") from exc
    return path


def _timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise NamespaceOwnershipError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise NamespaceOwnershipError(f"{label} is not a valid timestamp") from exc
    if parsed.tzinfo != timezone.utc or parsed.microsecond:
        raise NamespaceOwnershipError(f"{label} must use whole UTC seconds")
    return parsed


def _verified_text(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise NamespaceOwnershipError(f"{label} must be a string")
    result = value.strip()
    if result.upper() in _PLACEHOLDERS or len(result) < 3 or "\n" in result:
        raise NamespaceOwnershipError(f"{label} has no reviewed value")
    return result


def expected_namespaces(root: Path) -> dict[tuple[str, str], dict[str, object]]:
    """Derive the exact registry inventory from reviewed release manifests."""
    try:
        cohort = tomllib.loads((root / "release-cohort.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise NamespaceOwnershipError("cannot read release-cohort.toml") from exc
    if cohort.get("schema_version") != 1 or len(cohort.get("packages", [])) != 8:
        raise NamespaceOwnershipError("release cohort must contain exactly eight packages")
    expected: dict[tuple[str, str], dict[str, object]] = {}
    for package in cohort["packages"]:
        name = package.get("distribution")
        if not isinstance(name, str) or not name:
            raise NamespaceOwnershipError("release cohort has an invalid distribution")
        key = ("pypi", name)
        if key in expected:
            raise NamespaceOwnershipError("release cohort has duplicate distributions")
        expected[key] = {
            "project_url": f"https://pypi.org/project/{name}/",
            "publish_authorized": True,
            "workflow": "publish.yml",
            "environment": "pypi",
            "allowed_actions": ["publish"],
        }

    state_path = root / "release" / "release-state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NamespaceOwnershipError("cannot read release/release-state.json") from exc
    independent = state.get("independent_distributions")
    if not isinstance(independent, list):
        raise NamespaceOwnershipError("independent distribution inventory is missing")
    for record in independent:
        record = _mapping(record, label="independent distribution")
        registry = record.get("registry")
        name = record.get("name")
        workflow = record.get("workflow")
        environment = record.get("environment")
        if (
            registry not in {"npm", "pypi"}
            or not isinstance(name, str)
            or not name
            or workflow not in {"publish.yml", "native.yml"}
            or environment != registry
        ):
            raise NamespaceOwnershipError("unsupported independent distribution")
        key = (registry, name)
        if key in expected:
            raise NamespaceOwnershipError("distribution inventory contains duplicates")
        if registry == "pypi":
            project_url = f"https://pypi.org/project/{name}/"
        else:
            project_url = f"https://www.npmjs.com/package/{name}"
        expected[key] = {
            "project_url": project_url,
            "publish_authorized": bool(record.get("publish_authorized")),
            "workflow": workflow,
            "environment": environment,
            "allowed_actions": (
                ["publish"] if record.get("publish_authorized") is True else []
            ),
        }
    return expected


def _validate_attestation_header(
    root: Path,
    attestation: Mapping[str, Any],
    *,
    now: datetime,
) -> None:
    if attestation["schema"] != SCHEMA:
        raise NamespaceOwnershipError("unsupported namespace-attestation schema")
    if attestation["repository"] != "Daybreak-AI-Labs/Lightwork":
        raise NamespaceOwnershipError("namespace attestation names another repository")
    try:
        cohort = tomllib.loads((root / "release-cohort.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise NamespaceOwnershipError("cannot read release cohort") from exc
    if attestation["release_version"] != cohort.get("version"):
        raise NamespaceOwnershipError("namespace attestation version differs from cohort")
    if now.tzinfo is None:
        raise NamespaceOwnershipError("validation time must be timezone aware")
    now = now.astimezone(timezone.utc)
    attested_at = _timestamp(attestation["attested_at"], label="attested_at")
    expires_at = _timestamp(attestation["expires_at"], label="expires_at")
    if expires_at <= attested_at:
        raise NamespaceOwnershipError("namespace attestation expires before it starts")
    if expires_at - attested_at > _MAX_ATTESTATION_LIFETIME:
        raise NamespaceOwnershipError("namespace attestation lifetime exceeds 31 days")
    if attested_at > now + _CLOCK_SKEW:
        raise NamespaceOwnershipError("namespace attestation is from the future")
    if expires_at <= now:
        raise NamespaceOwnershipError("namespace attestation has expired")
    attestor = _exact_keys(
        attestation["attestor"],
        {"identity", "role"},
        label="attestor",
    )
    if attestor != {
        "identity": SIGNER_IDENTITY,
        "role": "registry-owner-verifier",
    }:
        raise NamespaceOwnershipError("namespace attestor identity or role is not authorized")


def _namespace_entries(entries: object) -> dict[tuple[str, str], Mapping[str, Any]]:
    if not isinstance(entries, list):
        raise NamespaceOwnershipError("namespace inventory must be a list")
    actual: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, value in enumerate(entries):
        entry = _exact_keys(
            value,
            {
                "registry",
                "package",
                "project_url",
                "publish_authorized",
                "ownership",
                "trusted_publisher",
            },
            label=f"namespace {index}",
        )
        registry = entry["registry"]
        package = entry["package"]
        if not isinstance(registry, str) or not isinstance(package, str):
            raise NamespaceOwnershipError(f"namespace {index} has invalid identity")
        key = (registry, package)
        if key in actual:
            raise NamespaceOwnershipError(f"duplicate namespace: {registry}:{package}")
        actual[key] = entry
    return actual


def _validate_namespace_authority(
    root: Path,
    key: tuple[str, str],
    policy: Mapping[str, object],
    entry: Mapping[str, Any],
) -> bool:
    label = f"{key[0]}:{key[1]}"
    if entry["project_url"] != policy["project_url"]:
        raise NamespaceOwnershipError(f"{label} project URL is not canonical")
    if entry["publish_authorized"] is not policy["publish_authorized"]:
        raise NamespaceOwnershipError(f"{label} publish authority differs from policy")
    ownership = _exact_keys(
        entry["ownership"],
        {"verified", "registry_account", "evidence_reference"},
        label=f"{label} ownership",
    )
    if ownership["verified"] is not True:
        raise NamespaceOwnershipError(f"{label} registry ownership is unverified")
    _verified_text(ownership["registry_account"], label=f"{label} registry account")
    _verified_text(ownership["evidence_reference"], label=f"{label} ownership evidence")
    publisher = _exact_keys(
        entry["trusted_publisher"],
        {
            "verified",
            "provider",
            "repository_owner",
            "repository",
            "workflow",
            "environment",
            "allowed_actions",
        },
        label=f"{label} trusted publisher",
    )
    expected_publisher = {
        "provider": "github-actions",
        "repository_owner": "Daybreak-AI-Labs",
        "repository": "Lightwork",
        "workflow": policy["workflow"],
        "environment": policy["environment"],
        "allowed_actions": policy["allowed_actions"],
    }
    if {field: publisher[field] for field in expected_publisher} != expected_publisher:
        raise NamespaceOwnershipError(f"{label} Trusted Publisher identity differs")
    workflow_path = root / ".github" / "workflows" / str(publisher["workflow"])
    if not workflow_path.is_file():
        raise NamespaceOwnershipError(f"{label} Trusted Publisher workflow is missing")
    if entry["publish_authorized"] is True and publisher["verified"] is not True:
        raise NamespaceOwnershipError(f"{label} Trusted Publisher is unverified")
    if entry["publish_authorized"] is False and publisher["verified"] is not False:
        raise NamespaceOwnershipError(
            f"{label} may not claim a publisher while publication is unauthorized"
        )
    return entry["publish_authorized"] is True


def validate_attestation_document(
    root: Path,
    document: object,
    *,
    now: datetime,
) -> dict[str, object]:
    """Validate signed statement semantics after signature verification."""
    attestation = _exact_keys(
        document,
        {
            "schema",
            "repository",
            "release_version",
            "attested_at",
            "expires_at",
            "attestor",
            "namespaces",
        },
        label="namespace attestation",
    )
    _validate_attestation_header(root, attestation, now=now)
    expected = expected_namespaces(root)
    actual = _namespace_entries(attestation["namespaces"])
    if set(actual) != set(expected):
        raise NamespaceOwnershipError(
            "namespace inventory differs from the release inventory: "
            f"missing={sorted(set(expected) - set(actual))}, "
            f"unknown={sorted(set(actual) - set(expected))}"
        )

    authorized = sum(
        _validate_namespace_authority(root, key, policy, actual[key])
        for key, policy in expected.items()
    )

    return {
        "repository": attestation["repository"],
        "release_version": attestation["release_version"],
        "namespace_count": len(actual),
        "publish_authorized_count": authorized,
        "expires_at": attestation["expires_at"],
    }


def _verify_ssh_signature(
    raw: bytes,
    *,
    signature_path: Path,
    allowed_signers_path: Path,
    ssh_keygen: str,
) -> None:
    _safe_file(signature_path, label="namespace attestation signature")
    _safe_file(allowed_signers_path, label="namespace allowed-signers policy")
    command = [
        ssh_keygen,
        "-Y",
        "verify",
        "-f",
        str(allowed_signers_path),
        "-I",
        SIGNER_IDENTITY,
        "-n",
        SIGNATURE_NAMESPACE,
        "-s",
        str(signature_path),
    ]
    try:
        result = subprocess.run(
            command,
            input=raw,
            check=False,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NamespaceOwnershipError(f"cannot verify namespace signature: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise NamespaceOwnershipError(
            f"namespace attestation signature verification failed: {detail}"
        )


def verify_namespace_ownership(
    root: Path,
    *,
    attestation_path: Path,
    signature_path: Path,
    allowed_signers_path: Path,
    now: datetime | None = None,
    ssh_keygen: str = "ssh-keygen",
) -> dict[str, object]:
    """Verify signature first, then validate the exact signed bytes."""
    root = root.resolve()
    attestation_path = _safe_file(
        attestation_path,
        label="namespace ownership attestation",
    )
    try:
        raw = attestation_path.read_bytes()
    except OSError as exc:
        raise NamespaceOwnershipError("cannot read namespace ownership attestation") from exc
    _verify_ssh_signature(
        raw,
        signature_path=signature_path,
        allowed_signers_path=allowed_signers_path,
        ssh_keygen=ssh_keygen,
    )
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NamespaceOwnershipError("signed namespace attestation is not valid JSON") from exc
    return validate_attestation_document(
        root,
        document,
        now=now or datetime.now(timezone.utc),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--attestation", type=Path)
    parser.add_argument("--signature", type=Path)
    parser.add_argument("--allowed-signers", type=Path)
    parser.add_argument("--ssh-keygen", default="ssh-keygen")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    attestation = args.attestation or root / "release" / "namespace-ownership-attestation.json"
    signature = args.signature or root / "release" / "namespace-ownership-attestation.json.sig"
    allowed_signers = (
        args.allowed_signers
        or root / "release" / "namespace-attestors.allowed-signers"
    )
    try:
        summary = verify_namespace_ownership(
            root,
            attestation_path=attestation,
            signature_path=signature,
            allowed_signers_path=allowed_signers,
            ssh_keygen=args.ssh_keygen,
        )
    except NamespaceOwnershipError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
