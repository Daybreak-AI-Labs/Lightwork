#!/usr/bin/env python3
"""Verify the exact first-profile production migration safety contract."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

CONTRACT_PATH = Path("release/production-migration-contract.json")
RUNNER_PATH = Path("packages/maverick-core/maverick/production_migration.py")
LOCK_PATH = Path("packages/maverick-core/maverick/migrations.lock.json")
PACKAGE_PATH = Path("packages/maverick-core/pyproject.toml")
HELM_JOB_PATH = Path("deploy/helm/maverick/templates/migration-job.yaml")
HELM_VALUES_PATH = Path("deploy/helm/maverick/values-production.yaml")
HELM_DEFAULT_VALUES_PATH = Path("deploy/helm/maverick/values.yaml")
HELM_HELPERS_PATH = Path("deploy/helm/maverick/templates/_helpers.tpl")
DOC_PATH = Path("docs/enterprise/production-migration-safety.md")
HELM_CHART_PATH = Path("deploy/helm/maverick")

_TEMPLATE_COMMENT = re.compile(r"{{-?\s*/\*.*?\*/\s*-?}}", re.DOTALL)
_TEMPLATE_ACTION = re.compile(r"{{-?\s*(.*?)\s*-?}}", re.DOTALL)
_CONSTANT_BRANCH = re.compile(
    r"(?:if|with)\s+(?:true|false|\(?\s*not\s+(?:true|false)\s*\)?)\Z"
)
_HELM_RELEASE = "migration-contract"
_HELM_DIGEST = "sha256:" + ("a" * 64)
_HELM_AUTHORIZATION_SHA256 = "b" * 64
_HELM_SOURCE_REVISION = "c" * 40
_HELM_ONBOARDING_CONTRACT_SHA256 = "d" * 64
_HELM_RELEASE_STATE_SHA256 = "e" * 64
_HELM_AUTHORITY_PUBLIC_KEY = "f" * 64
_HELM_IDENTITY_POSTURE_SHA256 = "1" * 64
_PACKAGED_CONTRACT_MAX_BYTES = 4 * 1024 * 1024
_PACKAGED_CONTRACT_MAX_DEPTH = 32
_PACKAGED_CONTRACT_TIMEOUT_SECONDS = 60
_PACKAGED_CONTRACT_CHILD = r"""
import json
import sys
from pathlib import Path

core = Path(sys.argv[1]).resolve(strict=True)
expected_module = (core / "maverick" / "production_migration.py").resolve(strict=True)
sys.path.insert(0, str(core))
from maverick import production_migration

actual_module = Path(production_migration.__file__).resolve(strict=True)
if actual_module != expected_module:
    raise RuntimeError("migration contract builder imported a different checkout")
payload = production_migration.production_migration_contract()
sys.stdout.write(
    json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
)
"""


class ProductionMigrationContractError(ValueError):
    """The committed migration contract is missing, stale, or weakened."""


class _PackagedContractDecodeError(ValueError):
    """Internal marker normalized before it crosses the verifier boundary."""


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProductionMigrationContractError(
                f"production migration contract has duplicate key {key!r}"
            )
        result[key] = value
    return result


def _load_strict(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ProductionMigrationContractError(
                    f"production migration contract contains {value}"
                )
            ),
        )
    except ProductionMigrationContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProductionMigrationContractError(
            "production migration contract is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ProductionMigrationContractError(
            "production migration contract must be an object"
        )
    return value


def _packaged_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise _PackagedContractDecodeError
        result[key] = value
    return result


def _packaged_float(token: str) -> float:
    try:
        value = float(token)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _PackagedContractDecodeError from exc
    if not math.isfinite(value):
        raise _PackagedContractDecodeError
    return value


def _validate_packaged_json(value: Any, *, depth: int = 0) -> None:
    if depth > _PACKAGED_CONTRACT_MAX_DEPTH:
        raise _PackagedContractDecodeError
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise _PackagedContractDecodeError
        return
    if type(value) is list:
        for item in value:
            _validate_packaged_json(item, depth=depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise _PackagedContractDecodeError
            _validate_packaged_json(item, depth=depth + 1)
        return
    raise _PackagedContractDecodeError


def _invalid_packaged_contract() -> ProductionMigrationContractError:
    return ProductionMigrationContractError(
        "packaged migration ladder/lock returned invalid evidence"
    )


def _decode_packaged_contract(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > _PACKAGED_CONTRACT_MAX_BYTES:
        raise _invalid_packaged_contract() from None
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_packaged_pairs,
            parse_constant=lambda _token: (_ for _ in ()).throw(
                _PackagedContractDecodeError()
            ),
            parse_float=_packaged_float,
        )
        if type(value) is not dict:
            raise _PackagedContractDecodeError
        _validate_packaged_json(value)
    except (
        UnicodeError,
        json.JSONDecodeError,
        _PackagedContractDecodeError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        raise _invalid_packaged_contract() from None
    return value


def _build_packaged_contract(root: Path) -> dict[str, Any]:
    """Build the checkout's contract without trusting parent imports or config."""
    core = (root / "packages" / "maverick-core").resolve()
    env = dict(os.environ)
    for name in tuple(env):
        normalized = name.upper()
        if normalized.startswith(("LIGHTWORK_", "MAVERICK_")) or normalized in {
            "PYTHONHOME",
            "PYTHONPATH",
        }:
            env.pop(name, None)

    try:
        with (
            tempfile.TemporaryDirectory(
                prefix="lightwork-migration-verifier-"
            ) as home,
            tempfile.TemporaryFile(mode="w+b") as stdout_capture,
            tempfile.TemporaryFile(mode="w+b") as stderr_capture,
        ):
            env["MAVERICK_HOME"] = home
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-c",
                    _PACKAGED_CONTRACT_CHILD,
                    str(core),
                ],
                cwd=root,
                env=env,
                check=False,
                stdout=stdout_capture,
                stderr=stderr_capture,
                timeout=_PACKAGED_CONTRACT_TIMEOUT_SECONDS,
            )
            stdout_capture.flush()
            stderr_capture.flush()
            stdout_capture.seek(0, os.SEEK_END)
            stderr_capture.seek(0, os.SEEK_END)
            stdout_size = stdout_capture.tell()
            stderr_size = stderr_capture.tell()
            if (
                type(completed.returncode) is not int
                or completed.returncode != 0
                or stdout_size <= 0
                or stdout_size > _PACKAGED_CONTRACT_MAX_BYTES
                or stderr_size != 0
            ):
                raise ProductionMigrationContractError(
                    "packaged migration ladder/lock could not build the exact contract"
                )
            stdout_capture.seek(0)
            raw = stdout_capture.read(_PACKAGED_CONTRACT_MAX_BYTES + 1)
    except (OSError, subprocess.SubprocessError):
        raise ProductionMigrationContractError(
            "packaged migration ladder/lock could not build the exact contract"
        ) from None
    except ProductionMigrationContractError:
        raise
    # The child is checkout code running with the verifier's authority, not a
    # sandbox boundary; workflow/container limits own hostile-code resources.
    # File-backed capture avoids parent-memory amplification, and only bounded
    # stdout is ever read. Stderr can affect success but never diagnostics.
    return _decode_packaged_contract(raw)


def _require_file(root: Path, relative: Path) -> Path:
    path = root / relative
    if not path.is_file():
        raise ProductionMigrationContractError(
            f"required migration contract path is missing: {relative.as_posix()}"
        )
    return path


def _active_template_text(path: Path) -> str:
    """Remove comments so safety tokens cannot be satisfied by dead prose."""

    text = _TEMPLATE_COMMENT.sub("", path.read_text(encoding="utf-8"))
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _refuse_constant_dead_branches(path: Path, *, label: str) -> None:
    """Reject literal Go-template conditions that make one branch unreachable.

    Exact source hashes make every template change reviewable, but they do not
    prove that required tokens render. A constant ``if``/``with`` is never
    needed in the production contract templates and can otherwise hide all of
    the safety wiring while leaving raw-source fragment checks satisfied.
    """

    active = _active_template_text(path)
    for match in _TEMPLATE_ACTION.finditer(active):
        action = " ".join(match.group(1).strip().split())
        if _CONSTANT_BRANCH.fullmatch(action):
            raise ProductionMigrationContractError(
                f"{label} contains a constant dead Go-template branch: {action}"
            )


def _resolve_helm_binary(explicit: Path | None) -> Path | None:
    candidate = str(explicit) if explicit is not None else os.environ.get(
        "LIGHTWORK_HELM_BINARY", ""
    )
    if not candidate:
        candidate = shutil.which("helm") or ""
    if not candidate:
        return None
    path = Path(candidate).resolve()
    if not path.is_file():
        raise ProductionMigrationContractError(
            "production migration Helm renderer is not a regular file"
        )
    return path


def _helm_template_command(root: Path, helm: Path) -> list[str]:
    chart = root / HELM_CHART_PATH
    values = root / HELM_VALUES_PATH
    return [
        str(helm),
        "template",
        _HELM_RELEASE,
        str(chart),
        "-f",
        str(values),
        "--set",
        f"image.digest={_HELM_DIGEST}",
        "--set",
        "secret.name=maverick-secrets",
        "--set",
        "secret.rotationGeneration=g000001",
        "--set",
        "productionOnboarding.deploymentId=urn:lightwork:deployment:migration-contract",
        "--set",
        f"productionOnboarding.sourceRevision={_HELM_SOURCE_REVISION}",
        "--set",
        (
            "productionOnboarding.contractSha256="
            f"{_HELM_ONBOARDING_CONTRACT_SHA256}"
        ),
        "--set",
        f"productionOnboarding.releaseStateSha256={_HELM_RELEASE_STATE_SHA256}",
        "--set",
        "productionOnboarding.authorityKeyId=urn:lightwork:key:migration-contract",
        "--set",
        f"productionOnboarding.authorityPublicKey={_HELM_AUTHORITY_PUBLIC_KEY}",
        "--set",
        (
            "productionOnboarding.identityPostureSha256="
            f"{_HELM_IDENTITY_POSTURE_SHA256}"
        ),
        "--set",
        "identity.mode=oidc",
        "--set-string",
        "identity.config.oidc.issuer=https://idp.example.test/",
        "--set-string",
        "identity.config.oidc.audience=lightwork",
        "--set-string",
        "identity.config.oidc.jwksUri=https://idp.example.test/jwks.json",
        "--set",
        "identity.config.oidc.algorithms[0]=RS256",
        "--set",
        "identity.config.oidc.algorithms[1]=ES256",
        "--set-string",
        "identity.config.oidc.clientId=lightwork-dashboard",
        "--set-string",
        "identity.config.oidc.redirectUri=https://lightwork.example.test/auth/callback",
        "--set",
        "identity.config.dashboard.defaultRole=viewer",
        "--set",
        "identity.config.dashboard.admins[0]=user:break-glass",
        "--set",
        "identity.config.dashboard.defaultSuites[0]=finance",
        "--set",
        "identity.config.dashboard.groupRoles.PlatformAdmins=admin",
        "--set-json",
        'identity.config.dashboard.groupSuites.Finance=["finance"]',
        "--set-json",
        (
            'identity.config.kernel.roles.restricted={"allowTools":'
            '["knowledge_search"],"denyTools":["exec"],"maxRisk":"low",'
            '"allowPaths":["/workspace/**"],"allowHosts":[]}'
        ),
        "--set",
        "identity.config.kernel.roleAssignments.default=restricted",
        "--set",
        "migration.secretName=migration-contract-secret",
        "--set",
        f"migration.authorizationSha256={_HELM_AUTHORIZATION_SHA256}",
        "--set",
        "migration.allowedEgressCIDRs[0]=10.44.0.10/32",
        "--set",
        "model.name=approved-model",
        "--set",
        f"model.artifactDigest={_HELM_DIGEST}",
        "--set",
        "model.baseURL=https://vllm.example.test/v1",
        "--set",
        "model.region=us-east-1",
        "--set",
        "model.maxSpendUSD=100",
        "--set",
        "kms.address=https://vault.example.test",
        "--set",
        "indeterminateReconciliation.escalationTarget=incident-response",
        "--set",
        "indeterminateReconciliation.adjudicators[0]=user:incident-commander",
        "--set",
        "tenantQuota.policyGeneration=1",
        "--set",
        "tenantQuota.limits.concurrentRuns=8",
        "--set",
        "tenantQuota.limits.queuedJobs=250",
        "--set",
        "tenantQuota.limits.dailyTokens=10000000",
        "--set",
        "tenantQuota.limits.dailySpendDollars=250",
        "--set",
        "tenantQuota.limits.toolCalls=50000",
        "--set",
        "tenantQuota.limits.storageBytes=10737418240",
        "--set",
        "networkPolicy.allowedEgressCIDRs[0]=10.44.0.0/24",
        "--set",
        "networkPolicy.dnsCIDRs[0]=10.96.0.10/32",
        "--set",
        "networkPolicy.allowedIngressNamespaces[0]=ingress-nginx",
        "--set",
        "ingress.className=nginx",
        "--set",
        "ingress.hosts[0].host=lightwork.example.test",
        "--set",
        "ingress.tls[0].secretName=lightwork-tls",
        "--set",
        "ingress.tls[0].hosts[0]=lightwork.example.test",
    ]


def _run_helm(command: list[str], *, root: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProductionMigrationContractError(
            "production migration Helm semantic render could not run"
        ) from exc


def _verify_helm_semantics(root: Path, helm: Path) -> None:
    version = _run_helm([str(helm), "version", "--short"], root=root)
    if version.returncode != 0 or not version.stdout.strip().startswith("v3."):
        raise ProductionMigrationContractError(
            "production migration semantic verification requires Helm 3"
        )

    command = _helm_template_command(root, helm)
    rendered = _run_helm(command, root=root)
    if rendered.returncode != 0:
        raise ProductionMigrationContractError(
            "production migration Helm semantic fixture did not render"
        )
    documents = re.split(r"(?m)^---\s*$", rendered.stdout)
    jobs = [
        document
        for document in documents
        if re.search(r"(?m)^kind:\s*Job\s*$", document)
        and "maverick.production_migration" in document
    ]
    policies = [
        document
        for document in documents
        if re.search(r"(?m)^kind:\s*NetworkPolicy\s*$", document)
        and "app.kubernetes.io/name: maverick-migration" in document
    ]
    if len(jobs) != 1 or len(policies) != 1:
        raise ProductionMigrationContractError(
            "production migration Helm semantic render omitted the exact Job or NetworkPolicy"
        )
    job = jobs[0]
    policy = policies[0]
    _require_fragments(
        job,
        (
            "activeDeadlineSeconds: 1800",
            "ttlSecondsAfterFinished: 3600",
            "backoffLimit: 0",
            "automountServiceAccountToken: false",
            "maverick.production_migration",
            "--authorization-sha256",
            _HELM_AUTHORIZATION_SHA256,
            "--image-digest",
            _HELM_DIGEST,
            "--release-version",
            "MAVERICK_PG_MIGRATION_DSN",
            "MAVERICK_MIGRATION_AUTHORIZATION",
        ),
        "production migration Helm semantic Job is incomplete",
    )
    if re.search(r"(?m)^\s+affinity:\s*$", job):
        raise ProductionMigrationContractError(
            "production migration Helm semantic Job inherited serving affinity"
        )
    _require_fragments(
        policy,
        ("ingress: []", "10.44.0.10/32", "port: 5432", "protocol: TCP"),
        "production migration Helm semantic NetworkPolicy is incomplete",
    )
    if "10.44.0.0/24" in policy:
        raise ProductionMigrationContractError(
            "production migration NetworkPolicy inherited serving egress"
        )

    for override, expected_error in (
        ("migration.enabled=false", "migration.enabled"),
        ("migration.authorizationSha256=bad", "migration.authorizationSha256"),
        (
            "migration.ttlSecondsAfterFinished=0",
            "migration.ttlSecondsAfterFinished",
        ),
        ("migration.allowedEgressCIDRs[0]=0.0.0.0/0", "migration egress"),
    ):
        refused = _run_helm(command + ["--set", override], root=root)
        if refused.returncode == 0 or expected_error not in refused.stderr:
            raise ProductionMigrationContractError(
                "production migration Helm semantic guard is inactive: "
                f"{expected_error}"
            )


def _top_level_yaml_block(path: Path, key: str) -> str:
    """Return one exact top-level mapping block without accepting global matches."""

    lines = path.read_text(encoding="utf-8").splitlines()
    marker = f"{key}:"
    starts = [index for index, line in enumerate(lines) if line == marker]
    if len(starts) != 1:
        raise ProductionMigrationContractError(
            f"production values require exactly one top-level {marker} block"
        )
    block: list[str] = [marker]
    for line in lines[starts[0] + 1 :]:
        if line and not line[0].isspace() and not line.startswith("#"):
            break
        block.append(line)
    return "\n".join(block)


def _normalized_source_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _require_fragments(text: str, fragments: tuple[str, ...], label: str) -> None:
    for fragment in fragments:
        if fragment not in text:
            raise ProductionMigrationContractError(f"{label}: {fragment}")


def _verify_source_bindings(root: Path, committed: dict[str, Any]) -> None:
    bindings = committed.get("repository_source_sha256")
    if not isinstance(bindings, dict):
        raise ProductionMigrationContractError(
            "production migration contract omits exact repository source bindings"
        )
    required_bindings = {
        HELM_JOB_PATH.as_posix(),
        HELM_VALUES_PATH.as_posix(),
        HELM_DEFAULT_VALUES_PATH.as_posix(),
        HELM_HELPERS_PATH.as_posix(),
    }
    if set(bindings) != required_bindings:
        raise ProductionMigrationContractError(
            "production migration contract source bindings are incomplete"
        )
    for relative, digest in bindings.items():
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or _normalized_source_sha256(root / relative) != digest
        ):
            raise ProductionMigrationContractError(
                f"production migration source digest differs: {relative}"
            )


def verify_production_migration_contract(
    root: Path,
    *,
    helm_binary: Path | None = None,
    require_helm: bool = False,
) -> dict[str, int | str]:
    root = root.resolve()
    for relative in (
        CONTRACT_PATH,
        RUNNER_PATH,
        LOCK_PATH,
        PACKAGE_PATH,
        HELM_JOB_PATH,
        HELM_VALUES_PATH,
        HELM_DEFAULT_VALUES_PATH,
        HELM_HELPERS_PATH,
        DOC_PATH,
    ):
        _require_file(root, relative)

    expected = _build_packaged_contract(root)

    committed = _load_strict(root / CONTRACT_PATH)
    if committed != expected:
        raise ProductionMigrationContractError(
            "committed migration contract differs from the packaged ladder, lock, or policy"
        )

    try:
        package = tomllib.loads((root / PACKAGE_PATH).read_text(encoding="utf-8"))
        package_data = package["tool"]["setuptools"]["package-data"]["maverick"]
    except (KeyError, OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ProductionMigrationContractError(
            "maverick package-data policy is unavailable"
        ) from exc
    if "migrations.lock.json" not in package_data:
        raise ProductionMigrationContractError(
            "the reviewed migration lock is not included in the maverick wheel"
        )

    _refuse_constant_dead_branches(
        root / HELM_JOB_PATH,
        label="Helm migration job",
    )
    helm_job = _active_template_text(root / HELM_JOB_PATH)
    _require_fragments(
        helm_job,
        (
            '"helm.sh/hook": pre-install,pre-upgrade',
            '"helm.sh/hook-weight": "-20"',
            '"helm.sh/hook-delete-policy": before-hook-creation',
            "kind: NetworkPolicy",
            "ingress: []",
            'include "maverick.name" . }}-migration',
            "automountServiceAccountToken: false",
            'command: ["python", "-m", "maverick.secret_staging"]',
            'command: ["python", "-m", "maverick.production_migration"]',
            '"apply"',
            '"--authorization-sha256"',
            '"--image-digest"',
            '"--release-version"',
            "MAVERICK_PG_MIGRATION_DSN",
            "MAVERICK_MIGRATION_AUTHORIZATION",
            ".Values.migration.allowedEgressCIDRs",
            ".Values.migration.databasePort",
            "ttlSecondsAfterFinished: {{ .Values.migration.ttlSecondsAfterFinished }}",
            "{{- with .Values.migration.affinity }}",
            "restartPolicy: Never",
            "backoffLimit: 0",
        ),
        "Helm migration job is missing exact safety wiring",
    )
    if "{{- with .Values.affinity }}" in helm_job:
        raise ProductionMigrationContractError(
            "Helm migration job must not inherit serving-pod affinity"
        )

    _refuse_constant_dead_branches(
        root / HELM_HELPERS_PATH,
        label="Helm production validation",
    )
    helpers = _active_template_text(root / HELM_HELPERS_PATH)
    _require_fragments(
        helpers,
        (
            "{{- if .Values.production.enforce -}}",
            "{{- if not .Values.migration.enabled -}}",
            ".Values.migration.secretName",
            ".Values.migration.authorizationSha256",
            "{{- if ne (int .Values.migration.activeDeadlineSeconds) 1800 -}}",
            "{{- if ne (int .Values.migration.ttlSecondsAfterFinished) 3600 -}}",
            '{{- if ne (toString .Values.migration.stagingSizeLimit) "64Ki" -}}',
            "{{- if not .Values.migration.allowedEgressCIDRs -}}",
            "{{- range .Values.migration.allowedEgressCIDRs -}}",
            "{{- if or (eq . \"0.0.0.0/0\") (eq . \"::/0\") -}}",
            ".Values.migration.databasePort",
        ),
        "Helm production validation is missing migration invariant",
    )

    values = _top_level_yaml_block(root / HELM_VALUES_PATH, "migration")
    _require_fragments(
        values,
        (
            "migration:",
            "enabled: true",
            'secretName: ""',
            'authorizationSha256: ""',
            "activeDeadlineSeconds: 1800",
            "ttlSecondsAfterFinished: 3600",
            "stagingSizeLimit: 64Ki",
            "affinity: {}",
            "allowedEgressCIDRs: []",
            "databasePort: 5432",
        ),
        "production values omit migration input",
    )

    defaults = _top_level_yaml_block(root / HELM_DEFAULT_VALUES_PATH, "migration")
    _require_fragments(
        defaults,
        (
            "migration:",
            "enabled: false",
            "ttlSecondsAfterFinished: 3600",
            "affinity: {}",
        ),
        "default values omit migration isolation input",
    )

    _verify_source_bindings(root, committed)

    helm = _resolve_helm_binary(helm_binary)
    if helm is None:
        if require_helm:
            raise ProductionMigrationContractError(
                "production migration contract requires a Helm 3 semantic renderer"
            )
        helm_status = "not-run"
    else:
        _verify_helm_semantics(root, helm)
        helm_status = "verified"

    documentation = (root / DOC_PATH).read_text(encoding="utf-8")
    _require_fragments(
        documentation,
        (
            "adjacent predecessor",
            "Serving pods never receive the migration-owner DSN",
            "There are no SQL down migrations",
            "does not prove that a backup can be restored",
            "live qualification remains open",
        ),
        "migration runbook omits required conservative statement",
    )

    return {
        "schema_version": int(committed["schema_version"]),
        "target_head": int(committed["compatibility"]["target_head"]),
        "target_versions": len(committed["compatibility"]["target_versions"]),
        "external_qualification_items": len(
            committed["external_qualification_required"]
        ),
        "status": str(committed["artifact_status"]),
        "helm_semantic_render": helm_status,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--helm",
        type=Path,
        help="Path to the Helm 3 binary used for required semantic rendering.",
    )
    args = parser.parse_args(argv)
    try:
        summary = verify_production_migration_contract(
            args.root,
            helm_binary=args.helm,
            require_helm=True,
        )
    except ProductionMigrationContractError as exc:
        parser.exit(1, f"production migration contract refused: {exc}\n")
    print(
        "verified production migration contract at Postgres head "
        f"{summary['target_head']} across {summary['target_versions']} reviewed "
        "migration versions; live qualification remains required"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
