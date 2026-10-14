#!/usr/bin/env python3
"""Inventory and validate every repository pytest skip and expected failure."""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import fnmatch
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "lightwork.pytest-exceptions.v1"
INVENTORY_PATH = PurePosixPath("qa/pytest-exceptions.json")
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_TEMPORARY_DAYS = 120

PRODUCTION_CRITICAL_GLOBS = (
    "packages/maverick-core/tests/test_a2a*.py",
    "packages/maverick-core/tests/test_agent_bus*.py",
    "packages/maverick-core/tests/test_agent_edr.py",
    "packages/maverick-core/tests/test_agent_trust*.py",
    "packages/maverick-core/tests/test_approval*.py",
    "packages/maverick-core/tests/test_attestation*.py",
    "packages/maverick-core/tests/test_audit*.py",
    "packages/maverick-core/tests/test_browser_auth_vault.py",
    "packages/maverick-core/tests/test_bus*.py",
    "packages/maverick-core/tests/test_capability.py",
    "packages/maverick-core/tests/test_capability_path_enforcement.py",
    "packages/maverick-core/tests/test_control_plane*.py",
    "packages/maverick-core/tests/test_crypto_at_rest.py",
    "packages/maverick-core/tests/test_deployment*.py",
    "packages/maverick-core/tests/test_durable*.py",
    "packages/maverick-core/tests/test_egress_guard.py",
    "packages/maverick-core/tests/test_encryption*.py",
    "packages/maverick-core/tests/test_entitlements.py",
    "packages/maverick-core/tests/test_expiry_horizon.py",
    "packages/maverick-core/tests/test_failure_taxonomy.py",
    "packages/maverick-core/tests/test_fchmod_windows_safe.py",
    "packages/maverick-core/tests/test_federation*.py",
    "packages/maverick-core/tests/test_file_lock.py",
    "packages/maverick-core/tests/test_fleet_spine.py",
    "packages/maverick-core/tests/test_flow*.py",
    "packages/maverick-core/tests/test_grpc_tls.py",
    "packages/maverick-core/tests/test_golden_path.py",
    "packages/maverick-core/tests/test_handoff.py",
    "packages/maverick-core/tests/test_helm*.py",
    "packages/maverick-core/tests/test_hook*.py",
    "packages/maverick-core/tests/test_job_queue*.py",
    "packages/maverick-core/tests/test_kms*.py",
    "packages/maverick-core/tests/test_insight_exchange.py",
    "packages/maverick-core/tests/test_learning_store.py",
    "packages/maverick-core/tests/test_memory_plane.py",
    "packages/maverick-core/tests/test_multitenant*.py",
    "packages/maverick-core/tests/test_oauth*.py",
    "packages/maverick-core/tests/test_oidc*.py",
    "packages/maverick-core/tests/test_operating_record.py",
    "packages/maverick-core/tests/test_plugin*.py",
    "packages/maverick-core/tests/test_postgres*.py",
    "packages/maverick-core/tests/test_privacy*.py",
    "packages/maverick-core/tests/test_product_ops_wiring.py",
    "packages/maverick-core/tests/test_proof_pack.py",
    "packages/maverick-core/tests/test_production*.py",
    "packages/maverick-core/tests/test_queue*.py",
    "packages/maverick-core/tests/test_release*.py",
    "packages/maverick-core/tests/test_reclaim_orphan_encryption.py",
    "packages/maverick-core/tests/test_replay_evidence_hardening.py",
    "packages/maverick-core/tests/test_retention.py",
    "packages/maverick-core/tests/test_sandbox*.py",
    "packages/maverick-core/tests/test_secure_defaults.py",
    "packages/maverick-core/tests/test_secret*.py",
    "packages/maverick-core/tests/test_self_*.py",
    "packages/maverick-core/tests/test_semantic_recall.py",
    "packages/maverick-core/tests/test_shield*.py",
    "packages/maverick-core/tests/test_skill*.py",
    "packages/maverick-core/tests/test_soc2.py",
    "packages/maverick-core/tests/test_suite_grants.py",
    "packages/maverick-core/tests/test_tenant*.py",
    "packages/maverick-core/tests/test_tool_authz.py",
    "packages/maverick-core/tests/test_tool_token.py",
    "packages/maverick-core/tests/test_training_receipts.py",
    "packages/maverick-core/tests/test_worker*.py",
    "packages/maverick-core/tests/test_world_encryption*.py",
    "packages/maverick-dashboard/tests/test_*.py",
    "packages/maverick-shield/tests/test_*.py",
)
ALLOWED_ENVIRONMENT_PREFIXES = (
    "base-dependency:",
    "behavior-gap:",
    "configuration:",
    "dependency-state:",
    "external-service:",
    "fixture:",
    "host-capability:",
    "platform:",
    "python-extra:",
    "tool:",
    "workspace-package:",
)
DEPENDENCY_ENVIRONMENT_PREFIXES = (
    "base-dependency:",
    "python-extra:",
    "workspace-package:",
)
_TOP_LEVEL_KEYS = {"schema", "reviewed_on", "policy", "exceptions"}
_POLICY_KEYS = {
    "max_temporary_days",
    "production_critical_globs",
    "allowed_environment_prefixes",
}
_ENTRY_KEYS = {
    "id",
    "path",
    "line",
    "column",
    "kind",
    "source_sha256",
    "reason",
    "owner",
    "required_environment",
    "production_critical",
    "expires_on",
    "permanent_rationale",
    "enforced_by",
}
_ENFORCEMENT_KEYS = {"workflow", "job", "required_tokens"}
_OWNER = re.compile(r"@[A-Za-z0-9][A-Za-z0-9_-]{0,38}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ENTRY_ID = re.compile(r"QA03-[0-9]{4}\Z")
_JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


class PytestExceptionPolicyError(ValueError):
    """The repository pytest exception inventory is incomplete or unsafe."""


@dataclass(frozen=True, order=True)
class Occurrence:
    """One syntactic pytest exception in a repository Python source file."""

    path: str
    line: int
    column: int
    kind: str
    source_sha256: str
    reason: str
    strict: bool | None
    dependency_signal: bool


def _exact_dict(value: object, keys: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise PytestExceptionPolicyError(f"{label} fields are not the exact schema")
    return value


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PytestExceptionPolicyError(f"{label} must be a trimmed non-empty string")
    return value


def _date(value: object, *, label: str) -> dt.date:
    text = _text(value, label=label)
    try:
        parsed = dt.date.fromisoformat(text)
    except ValueError as exc:
        raise PytestExceptionPolicyError(f"{label} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != text:
        raise PytestExceptionPolicyError(f"{label} must be YYYY-MM-DD")
    return parsed


def _safe_repo_file(root: Path, relative: str, *, label: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or pure.as_posix() != relative:
        raise PytestExceptionPolicyError(f"{label} is not a canonical repository path")
    path = root.joinpath(*pure.parts)
    try:
        stat = path.lstat()
    except OSError as exc:
        raise PytestExceptionPolicyError(f"{label} is unavailable: {relative}") from exc
    if path.is_symlink() or not path.is_file() or stat.st_size > MAX_SOURCE_BYTES:
        raise PytestExceptionPolicyError(f"{label} is not a bounded regular file: {relative}")
    return path


def _read_text(root: Path, relative: str, *, label: str) -> str:
    path = _safe_repo_file(root, relative, label=label)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PytestExceptionPolicyError(f"{label} is not UTF-8 text: {relative}") from exc


def _repository_python_paths(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                "*.py",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PytestExceptionPolicyError("git repository-file inventory is unavailable") from exc
    try:
        paths = result.stdout.decode("utf-8").split("\0")
    except UnicodeDecodeError as exc:
        raise PytestExceptionPolicyError("git repository paths are not UTF-8") from exc
    canonical = sorted(path for path in paths if path)
    if not canonical or len(canonical) != len(set(canonical)):
        raise PytestExceptionPolicyError("git Python inventory is empty or duplicated")
    return canonical


def _pytest_bindings(tree: ast.AST) -> tuple[set[str], set[str], dict[str, str]]:
    modules = {"pytest"}
    marks: set[str] = set()
    direct: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pytest":
                    modules.add(alias.asname or "pytest")
        elif isinstance(node, ast.ImportFrom) and node.module == "pytest":
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name == "mark":
                    marks.add(local)
                elif alias.name in {"skip", "xfail", "importorskip"}:
                    direct[local] = alias.name
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if (
            not isinstance(value, ast.Attribute)
            or not isinstance(value.value, ast.Name)
            or value.value.id not in modules
            or value.attr not in {"skip", "xfail", "importorskip"}
        ):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                direct[target.id] = value.attr
    return modules, marks, direct


def _call_kind(
    function: ast.expr,
    *,
    modules: set[str],
    marks: set[str],
    direct: dict[str, str],
) -> str | None:
    if isinstance(function, ast.Name):
        return direct.get(function.id)
    if not isinstance(function, ast.Attribute):
        return None
    if isinstance(function.value, ast.Name):
        if function.value.id in modules and function.attr in {"skip", "xfail", "importorskip"}:
            return function.attr
        if function.value.id in marks and function.attr in {"skip", "skipif", "xfail"}:
            return f"mark.{function.attr}"
        return None
    owner = function.value
    if (
        isinstance(owner, ast.Attribute)
        and owner.attr == "mark"
        and isinstance(owner.value, ast.Name)
        and owner.value.id in modules
        and function.attr in {"skip", "skipif", "xfail"}
    ):
        return f"mark.{function.attr}"
    return None


def _literal_strings(tree: ast.Module) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in ast.walk(tree):
        targets: list[ast.expr]
        value: ast.expr | None
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if value is None:
            continue
        try:
            evaluated = ast.literal_eval(value)
        except (ValueError, TypeError):
            evaluated = None
        if isinstance(evaluated, str):
            rendered = evaluated.strip()
        elif isinstance(value, ast.JoinedStr):
            rendered = ast.unparse(value).strip()
        else:
            continue
        if not rendered:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                values[target.id] = rendered
    return values


def _render_reason(node: ast.expr | None, *, strings: dict[str, str]) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip()
    if isinstance(node, ast.Name) and node.id in strings:
        return strings[node.id]
    try:
        return ast.unparse(node).strip()
    except (ValueError, TypeError):
        return ""


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for item in call.keywords:
        if item.arg == name:
            return item.value
    return None


def _reason(kind: str, call: ast.Call, *, strings: dict[str, str]) -> str:
    keyword_reason = _keyword(call, "reason")
    if keyword_reason is not None:
        return _render_reason(keyword_reason, strings=strings)
    if kind in {"skip", "xfail", "mark.skip"}:
        return _render_reason(call.args[0] if call.args else None, strings=strings)
    if kind in {"mark.skipif", "mark.xfail"}:
        return _render_reason(call.args[1] if len(call.args) > 1 else None, strings=strings)
    if kind == "importorskip":
        module = _render_reason(call.args[0] if call.args else None, strings=strings)
        return f"module import unavailable: {module}" if module else ""
    return ""


def _strict(kind: str, call: ast.Call) -> bool | None:
    if kind not in {"xfail", "mark.xfail"}:
        return None
    value = _keyword(call, "strict")
    return isinstance(value, ast.Constant) and value.value is True


def _dependency_signal(kind: str, reason: str) -> bool:
    lowered = reason.lower()
    if kind == "importorskip":
        if any(token in lowered for token in ("fcntl", "msvcrt", "pwd", "winreg")):
            return False
        return True
    if any(token in lowered for token in ("git", "node", "helm", "ssh-keygen", "docker")):
        return False
    return any(
        token in lowered
        for token in (
            "not installed",
            "missing dependency",
            "dependency unavailable",
            "sdk unavailable",
            "sdk not available",
            "import unavailable",
        )
    )


def _occurrences_in_source(path: str, source: str) -> list[Occurrence]:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise PytestExceptionPolicyError(f"repository Python source does not parse: {path}") from exc
    modules, marks, direct = _pytest_bindings(tree)
    strings = _literal_strings(tree)
    # Bind every occurrence to the exact normalized module bytes.  AST dumps
    # are not portable across supported Python minors because the AST schema
    # can add fields even when the source has identical semantics.  Location,
    # kind, and reason separately bind the occurrence within this source file.
    normalized_source = source.replace("\r\n", "\n").replace("\r", "\n")
    source_sha256 = hashlib.sha256(normalized_source.encode("utf-8")).hexdigest()

    results: list[Occurrence] = []
    called_function_ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kind = _call_kind(node.func, modules=modules, marks=marks, direct=direct)
        if kind is None:
            continue
        called_function_ids.add(id(node.func))
        reason = _reason(kind, node, strings=strings)
        results.append(
            Occurrence(
                path=path,
                line=node.lineno,
                column=node.col_offset,
                kind=kind,
                source_sha256=source_sha256,
                reason=reason,
                strict=_strict(kind, node),
                dependency_signal=_dependency_signal(kind, reason),
            )
        )
    for node in ast.walk(tree):
        if id(node) in called_function_ids or not isinstance(node, ast.Attribute):
            continue
        kind = _call_kind(node, modules=modules, marks=marks, direct=direct)
        if kind not in {"mark.skip", "mark.skipif", "mark.xfail"}:
            continue
        results.append(
            Occurrence(
                path=path,
                line=node.lineno,
                column=node.col_offset,
                kind=kind,
                source_sha256=source_sha256,
                reason="",
                strict=False if kind == "mark.xfail" else None,
                dependency_signal=False,
            )
        )
    return sorted(results)


def scan_repository_occurrences(
    root: Path,
    *,
    paths: list[str] | None = None,
) -> list[Occurrence]:
    """Return all pytest exception syntax in repository Python files."""
    root = root.resolve()
    selected = _repository_python_paths(root) if paths is None else sorted(paths)
    occurrences: list[Occurrence] = []
    for path in selected:
        source = _read_text(root, path, label="repository Python source")
        occurrences.extend(_occurrences_in_source(path, source))
    return sorted(occurrences)


def _is_production_critical(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in PRODUCTION_CRITICAL_GLOBS)


def _workflow_job_block(root: Path, workflow: str, job: str) -> str:
    if not workflow.startswith(".github/workflows/") or not workflow.endswith((".yml", ".yaml")):
        raise PytestExceptionPolicyError("enforcement workflow must be a GitHub workflow path")
    source = _read_text(root, workflow, label="enforcement workflow")
    lines = source.splitlines()
    start = None
    header = re.compile(rf"^  {re.escape(job)}:\s*(?:#.*)?$")
    next_job = re.compile(r"^  [A-Za-z0-9_-]+:\s*(?:#.*)?$")
    for index, line in enumerate(lines):
        if header.fullmatch(line):
            if start is not None:
                raise PytestExceptionPolicyError(f"workflow job is duplicated: {workflow}#{job}")
            start = index
    if start is None:
        raise PytestExceptionPolicyError(f"workflow job is missing: {workflow}#{job}")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if next_job.fullmatch(lines[index]):
            end = index
            break
    return "\n".join(lines[start:end])


def _token_in_unconditional_run(block: str, token: str) -> bool:
    lines = block.splitlines()
    step_starts = [
        index for index, line in enumerate(lines) if re.match(r"^      - (?:name:|run:)", line)
    ]
    for position, start in enumerate(step_starts):
        end = step_starts[position + 1] if position + 1 < len(step_starts) else len(lines)
        step = lines[start:end]
        if not any(re.match(r"^(?:      - |        )run:", line) for line in step):
            continue
        if any(re.match(r"^        if:\s*", line) for line in step):
            continue
        if any(
            token in line
            for line in step
            if not line.lstrip().startswith("#")
            and not line.lstrip().startswith("- name:")
        ):
            return True
    return False


def _entry_key(entry: dict[str, Any]) -> tuple[str, int, int, str]:
    return (str(entry["path"]), int(entry["line"]), int(entry["column"]), str(entry["kind"]))


def _occurrence_key(occurrence: Occurrence) -> tuple[str, int, int, str]:
    return (
        occurrence.path,
        occurrence.line,
        occurrence.column,
        occurrence.kind,
    )


def validate_pytest_exception_policy(  # noqa: C901 - exact fail-closed policy gate
    root: Path,
    *,
    inventory_path: PurePosixPath = INVENTORY_PATH,
    paths: list[str] | None = None,
    as_of: dt.date | None = None,
) -> dict[str, object]:
    """Validate the exact inventory and return a stable machine summary."""
    root = root.resolve()
    try:
        inventory = json.loads(
            _read_text(root, inventory_path.as_posix(), label="pytest exception inventory")
        )
    except json.JSONDecodeError as exc:
        raise PytestExceptionPolicyError("pytest exception inventory is malformed JSON") from exc
    inventory = _exact_dict(inventory, _TOP_LEVEL_KEYS, label="pytest exception inventory")
    if inventory["schema"] != SCHEMA:
        raise PytestExceptionPolicyError("pytest exception inventory schema is unsupported")
    reviewed_on = _date(inventory["reviewed_on"], label="inventory review date")
    today = as_of or dt.datetime.now(dt.timezone.utc).date()
    if reviewed_on > today:
        raise PytestExceptionPolicyError("pytest exception inventory is future-reviewed")
    policy = _exact_dict(inventory["policy"], _POLICY_KEYS, label="pytest exception policy")
    if policy["max_temporary_days"] != MAX_TEMPORARY_DAYS:
        raise PytestExceptionPolicyError("temporary exception horizon drifted")
    if policy["production_critical_globs"] != list(PRODUCTION_CRITICAL_GLOBS):
        raise PytestExceptionPolicyError("production-critical test scope drifted")
    if policy["allowed_environment_prefixes"] != list(ALLOWED_ENVIRONMENT_PREFIXES):
        raise PytestExceptionPolicyError("required-environment vocabulary drifted")

    entries = inventory["exceptions"]
    if not isinstance(entries, list):
        raise PytestExceptionPolicyError("pytest exceptions must be an array")
    occurrences = scan_repository_occurrences(root, paths=paths)
    if len(entries) != len(occurrences):
        raise PytestExceptionPolicyError(
            f"pytest exception inventory count drifted: policy={len(entries)}, source={len(occurrences)}"
        )
    occurrences_by_key = {_occurrence_key(item): item for item in occurrences}
    if len(occurrences_by_key) != len(occurrences):
        raise PytestExceptionPolicyError("pytest source occurrence locations are duplicated")
    expected_ids = [f"QA03-{index:04d}" for index in range(1, len(entries) + 1)]
    actual_ids: list[str] = []
    inventoried_keys: set[tuple[str, int, int, str]] = set()
    enforcement_cache: dict[tuple[str, str], str] = {}
    for index, raw_entry in enumerate(entries):
        entry = _exact_dict(raw_entry, _ENTRY_KEYS, label=f"pytest exception {index}")
        entry_id = _text(entry["id"], label=f"pytest exception {index} id")
        if _ENTRY_ID.fullmatch(entry_id) is None:
            raise PytestExceptionPolicyError(f"pytest exception id is invalid: {entry_id}")
        actual_ids.append(entry_id)
        entry_key = _entry_key(entry)
        if entry_key in inventoried_keys:
            raise PytestExceptionPolicyError(
                f"pytest exception inventory locations are duplicated at {entry_id}"
            )
        inventoried_keys.add(entry_key)
        occurrence = occurrences_by_key.get(entry_key)
        if occurrence is None:
            raise PytestExceptionPolicyError(
                f"pytest exception location drifted at {entry_id}: "
                f"policy={entry_key!r}"
            )
        if entry["source_sha256"] != occurrence.source_sha256 or _SHA256.fullmatch(
            str(entry["source_sha256"])
        ) is None:
            raise PytestExceptionPolicyError(f"pytest exception source changed: {entry_id}")
        reason = _text(entry["reason"], label=f"pytest exception {entry_id} reason")
        if not occurrence.reason or reason != occurrence.reason:
            raise PytestExceptionPolicyError(f"pytest exception reason drifted or is absent: {entry_id}")
        if occurrence.kind in {"xfail", "mark.xfail"} and occurrence.strict is not True:
            raise PytestExceptionPolicyError(f"xfail is not explicitly strict: {entry_id}")
        owner = _text(entry["owner"], label=f"pytest exception {entry_id} owner")
        if _OWNER.fullmatch(owner) is None:
            raise PytestExceptionPolicyError(f"pytest exception owner is invalid: {entry_id}")
        environment = _text(
            entry["required_environment"],
            label=f"pytest exception {entry_id} required environment",
        )
        if not environment.startswith(ALLOWED_ENVIRONMENT_PREFIXES):
            raise PytestExceptionPolicyError(f"pytest exception environment is invalid: {entry_id}")
        critical = _is_production_critical(occurrence.path)
        if entry["production_critical"] is not critical:
            raise PytestExceptionPolicyError(f"production-critical classification drifted: {entry_id}")

        expiry = entry["expires_on"]
        rationale = entry["permanent_rationale"]
        if (expiry is None) == (rationale is None):
            raise PytestExceptionPolicyError(
                f"pytest exception needs exactly one expiry or permanent rationale: {entry_id}"
            )
        if expiry is not None:
            expires_on = _date(expiry, label=f"pytest exception {entry_id} expiry")
            if expires_on <= today:
                raise PytestExceptionPolicyError(f"pytest exception is expired: {entry_id}")
            if expires_on > reviewed_on + dt.timedelta(days=MAX_TEMPORARY_DAYS):
                raise PytestExceptionPolicyError(f"pytest exception expiry is too distant: {entry_id}")
        else:
            rationale_text = _text(
                rationale,
                label=f"pytest exception {entry_id} permanent rationale",
            )
            if len(rationale_text) < 40:
                raise PytestExceptionPolicyError(
                    f"pytest exception permanent rationale is not substantive: {entry_id}"
                )
            if occurrence.kind in {"xfail", "mark.xfail"} and not rationale_text.startswith(
                "PERMANENT:"
            ):
                raise PytestExceptionPolicyError(
                    f"xfail must expire unless explicitly permanently justified: {entry_id}"
                )

        enforcement = entry["enforced_by"]
        dependency_environment = environment.startswith(DEPENDENCY_ENVIRONMENT_PREFIXES)
        service_environment = environment.startswith("external-service:")
        tool_environment = environment.startswith("tool:")
        needs_enforcement = critical and (
            occurrence.dependency_signal
            or dependency_environment
            or service_environment
            or tool_environment
        )
        if needs_enforcement and enforcement is None:
            raise PytestExceptionPolicyError(
                f"production-critical dependency skip lacks an enforced lane: {entry_id}"
            )
        if enforcement is not None:
            enforcement = _exact_dict(
                enforcement,
                _ENFORCEMENT_KEYS,
                label=f"pytest exception {entry_id} enforcement",
            )
            workflow = _text(
                enforcement["workflow"],
                label=f"pytest exception {entry_id} workflow",
            )
            job = _text(enforcement["job"], label=f"pytest exception {entry_id} job")
            if _JOB_ID.fullmatch(job) is None:
                raise PytestExceptionPolicyError(f"pytest exception job id is invalid: {entry_id}")
            tokens = enforcement["required_tokens"]
            if (
                not isinstance(tokens, list)
                or len(tokens) < 2
                or len(tokens) != len(set(tokens))
                or any(not isinstance(token, str) or not token.strip() for token in tokens)
            ):
                raise PytestExceptionPolicyError(
                    f"pytest exception enforcement tokens are invalid: {entry_id}"
                )
            cache_key = (workflow, job)
            block = enforcement_cache.get(cache_key)
            if block is None:
                block = _workflow_job_block(root, workflow, job)
                enforcement_cache[cache_key] = block
            if re.search(r"^    if:\s*", block, flags=re.MULTILINE) or re.search(
                r"^\s+continue-on-error:\s*true\s*(?:#.*)?$",
                block,
                flags=re.MULTILINE | re.IGNORECASE,
            ):
                raise PytestExceptionPolicyError(
                    f"pytest exception enforcement job is conditional or soft-failing: {entry_id}"
                )
            missing = [token for token in tokens if not _token_in_unconditional_run(block, token)]
            if missing:
                raise PytestExceptionPolicyError(
                    f"pytest exception enforcement lane drifted at {entry_id}: missing={missing!r}"
                )
    if actual_ids != expected_ids:
        raise PytestExceptionPolicyError("pytest exception ids are not ordered and contiguous")
    if inventoried_keys != set(occurrences_by_key):
        raise PytestExceptionPolicyError("pytest exception location set drifted")
    return {
        "schema": SCHEMA,
        "reviewed_on": reviewed_on.isoformat(),
        "occurrence_count": len(occurrences),
        "production_critical_count": sum(_is_production_critical(item.path) for item in occurrences),
        "strict_xfail_count": sum(item.kind in {"xfail", "mark.xfail"} for item in occurrences),
        "enforced_dependency_count": sum(
            isinstance(entry, dict) and entry.get("enforced_by") is not None for entry in entries
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--inventory", type=PurePosixPath, default=INVENTORY_PATH)
    parser.add_argument("--as-of", type=dt.date.fromisoformat)
    parser.add_argument(
        "--scan",
        action="store_true",
        help="print repository source occurrences without changing the inventory",
    )
    args = parser.parse_args(argv)
    try:
        if args.scan:
            print(
                json.dumps(
                    [asdict(item) for item in scan_repository_occurrences(args.root.resolve())],
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0
        summary = validate_pytest_exception_policy(
            args.root,
            inventory_path=args.inventory,
            as_of=args.as_of,
        )
    except PytestExceptionPolicyError as exc:
        parser.exit(1, f"pytest-exceptions: ERROR: {exc}\n")
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
