"""Exact filesystem and resource contract for the first production profile.

The first profile is a control plane, not a general-purpose execution worker.
It admits ``MAVERICK_SANDBOX_BACKEND=disabled`` and therefore has no
model-generated process tree.  This module keeps the remaining workload
boundary explicit and machine-checkable:

* an immutable image/root with two application-writable mounts only;
* a fixed, read-only secret projection with no raw audit private key;
* POSIX process/file limits installed before the dashboard becomes PID 1; and
* per-goal limits which cannot be raised by a mutable tenant config file.

Kubernetes RuntimeClass, cgroup, storage-class, and NetworkPolicy enforcement
remain cluster controls.  The code can validate the pod inputs and its live
mount/rlimit view, but it deliberately does not claim to attest the external
cluster implementation.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import sys
import sysconfig
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "lightwork.production-runtime-contract.v1"
PROFILE_ID = "enterprise-single-control-plane-v1"
CONTRACT_ENV = "MAVERICK_PRODUCTION_RUNTIME_CONTRACT"
EXPECTED_DIGEST_ENV = "MAVERICK_PRODUCTION_RUNTIME_CONTRACT_SHA256"
REQUIRE_ENV = "MAVERICK_REQUIRE_PRODUCTION_RUNTIME_CONTRACT"

TOOL_WORKSPACE = "/workspace"
STATE_ROOT = "/home/maverick/.maverick"
TMP_ROOT = "/tmp"
SECRET_ROOT = "/run/secrets/lightwork"

CPU_LIMIT = "2"
MEMORY_LIMIT = "2Gi"
EPHEMERAL_STORAGE_LIMIT = "1Gi"
TMP_SIZE_LIMIT = "1Gi"
DURABLE_VOLUME_REQUEST = "10Gi"
SECRET_STAGING_LIMIT = "2Mi"
PID_LIMIT = 256
OPEN_FILE_LIMIT = 4096
SINGLE_FILE_BYTES = 8 * 1024 * 1024 * 1024
TOOL_RESULT_BYTES = 32_000
GOAL_WALL_SECONDS = 1800
TOOL_CALLS_PER_GOAL = 500
INPUT_TOKENS_PER_GOAL = 1_000_000
OUTPUT_TOKENS_PER_GOAL = 200_000
NESTED_AGENT_DEPTH = 3
TOTAL_CHILD_AGENTS = 32
CONCURRENT_GOALS = 8
CONCURRENT_GOALS_PER_PRINCIPAL = 2
TERMINATION_GRACE_SECONDS = 60
OPERATING_SYSTEM = "Linux"
RUN_AS_UID = 1000
RUN_AS_GID = 1000

BASE_SECRET_FILES = frozenset(
    {
        "MAVERICK_AUDIT_SIGNING_KEY_WRAPPED",
        "MAVERICK_KMS_KEY_ID",
        "MAVERICK_PG_DSN",
        "VAULT_TOKEN",
        "VLLM_API_KEY",
    }
)
EVIDENCE_FILES = frozenset(
    {
        "MAVERICK_PRODUCTION_ONBOARDING_OBSERVATIONS",
    }
)
OIDC_SECRET_FILES = frozenset(
    {
        "MAVERICK_OIDC_CLIENT_SECRET",
        "MAVERICK_OIDC_SESSION_SECRET",
    }
)
FILE_ONLY_SECRET_ENV = BASE_SECRET_FILES | OIDC_SECRET_FILES
FORBIDDEN_SECRET_FILES = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "ANTHROPIC_API_KEY",
        "AZURE_CLIENT_SECRET",
        "CODEX_HOME",
        "DOCKER_AUTH_CONFIG",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "KUBECONFIG",
        "MAVERICK_AUDIT_SIGNING_KEY",
        "MAVERICK_DASHBOARD_TOKEN",
        "OPENAI_API_KEY",
        "SSH_AUTH_SOCK",
    }
)
FORBIDDEN_PATHS = (
    "/home/maverick/.aws",
    "/home/maverick/.azure",
    "/home/maverick/.cache/huggingface",
    "/home/maverick/.codex",
    "/home/maverick/.config/gh",
    "/home/maverick/.config/gcloud",
    "/home/maverick/.config/huggingface",
    "/home/maverick/.docker",
    "/home/maverick/.kube",
    "/home/maverick/.ssh",
    "/run/containerd/containerd.sock",
    "/run/secrets/kubernetes.io/serviceaccount",
    "/root/.aws",
    "/root/.azure",
    "/root/.config/gcloud",
    "/root/.docker",
    "/root/.kube",
    "/root/.ssh",
    "/var/run/crio/crio.sock",
    "/var/run/docker.sock",
    "/run/secrets/lightwork-projected",
)
READ_ONLY_INPUTS = (
    "/app",
    "/etc/lightwork",
    SECRET_ROOT,
    TOOL_WORKSPACE,
)
WRITABLE_MOUNTS = (TMP_ROOT, STATE_ROOT)
PLATFORM_MOUNT_PREFIXES = ("/dev", "/proc", "/sys")
PLATFORM_MOUNT_POINTS = ("/etc/hostname", "/etc/hosts", "/etc/resolv.conf")

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})
_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")


class ProductionRuntimeContractError(RuntimeError):
    """The live process is outside the reviewed production runtime contract."""


@dataclass(frozen=True)
class Mount:
    """One decoded Linux ``/proc/self/mountinfo`` record."""

    mount_point: str
    options: frozenset[str]
    fs_type: str
    source: str


def contract_document() -> dict[str, Any]:
    """Return the canonical, code-derived production runtime manifest."""
    return {
        "schema": SCHEMA,
        "profile_id": PROFILE_ID,
        "execution": {
            "arbitrary_command_execution": False,
            "container_entrypoint_is_pid1": True,
            "installed_package_inputs_read_only": True,
            "operating_system": OPERATING_SYSTEM,
            "run_as_gid": RUN_AS_GID,
            "run_as_uid": RUN_AS_UID,
            "sandbox_backend": "disabled",
            "termination_grace_seconds": TERMINATION_GRACE_SECONDS,
            "termination_signal": "SIGTERM",
            "untrusted_process_tree": "none",
        },
        "filesystem": {
            "durable_volume_request": DURABLE_VOLUME_REQUEST,
            "forbidden_paths": list(FORBIDDEN_PATHS),
            "forbidden_secret_files": sorted(FORBIDDEN_SECRET_FILES),
            "file_only_secret_environment": sorted(FILE_ONLY_SECRET_ENV),
            "oidc_secret_files": sorted(OIDC_SECRET_FILES),
            "platform_mount_points": list(PLATFORM_MOUNT_POINTS),
            "platform_mount_prefixes": list(PLATFORM_MOUNT_PREFIXES),
            "projected_evidence_files": sorted(EVIDENCE_FILES),
            "projected_secret_files": sorted(BASE_SECRET_FILES),
            "read_only_inputs": list(READ_ONLY_INPUTS),
            "read_only_root": True,
            "secret_staging_limit": SECRET_STAGING_LIMIT,
            "tmp_size_limit": TMP_SIZE_LIMIT,
            "tool_workspace": TOOL_WORKSPACE,
            "writable_mounts": list(WRITABLE_MOUNTS),
        },
        "network": {
            "allow_public_egress": False,
            "control": "kubernetes-networkpolicy",
            "dns_and_approved_dependency_cidrs_only": True,
            "live_cluster_proof_required": True,
        },
        "resources": {
            "concurrent_goals": CONCURRENT_GOALS,
            "concurrent_goals_per_principal": CONCURRENT_GOALS_PER_PRINCIPAL,
            "cpu_limit": CPU_LIMIT,
            "ephemeral_storage_limit": EPHEMERAL_STORAGE_LIMIT,
            "goal_wall_seconds": GOAL_WALL_SECONDS,
            "input_tokens_per_goal": INPUT_TOKENS_PER_GOAL,
            "memory_limit": MEMORY_LIMIT,
            "nested_agent_depth": NESTED_AGENT_DEPTH,
            "open_files": OPEN_FILE_LIMIT,
            "output_tokens_per_goal": OUTPUT_TOKENS_PER_GOAL,
            "pids": PID_LIMIT,
            "single_file_bytes": SINGLE_FILE_BYTES,
            "tool_calls_per_goal": TOOL_CALLS_PER_GOAL,
            "tool_result_bytes": TOOL_RESULT_BYTES,
            "total_child_agents": TOTAL_CHILD_AGENTS,
        },
    }


def canonical_contract_bytes() -> bytes:
    """Stable bytes used by the committed artifact and release digest."""
    return (
        json.dumps(contract_document(), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _source_contract() -> Path | None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "release" / "production-runtime-contract.json"
        if candidate.is_file():
            return candidate
    return None


def contract_path() -> Path:
    configured = os.environ.get(CONTRACT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    installed = Path("/etc/lightwork/production-runtime-contract.json")
    if installed.is_file():
        return installed
    source = _source_contract()
    if source is not None:
        return source
    raise ProductionRuntimeContractError(
        "production runtime contract is missing"
    )


def load_contract(path: str | Path | None = None) -> dict[str, Any]:
    """Load an exact generated contract, rejecting any hand-edited drift."""
    target = Path(path).expanduser() if path is not None else contract_path()
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise ProductionRuntimeContractError(
            f"cannot read production runtime contract: {target}"
        ) from exc
    expected_digest = os.environ.get(EXPECTED_DIGEST_ENV, "").strip().lower()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_digest and digest != expected_digest:
        raise ProductionRuntimeContractError(
            "production runtime contract digest does not match the release pin"
        )
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductionRuntimeContractError(
            "production runtime contract is not valid UTF-8 JSON"
        ) from exc
    if document != contract_document() or raw != canonical_contract_bytes():
        raise ProductionRuntimeContractError(
            "production runtime contract does not match the code-derived manifest"
        )
    return document


def runtime_contract_required() -> bool:
    raw = os.environ.get(REQUIRE_ENV, "").strip().lower()
    if raw in _TRUE:
        return True
    if not raw or raw in _FALSE:
        return False
    raise ProductionRuntimeContractError(
        f"{REQUIRE_ENV} must be an explicit boolean value"
    )


def expected_secret_files(env: Mapping[str, str] | None = None) -> frozenset[str]:
    values = os.environ if env is None else env
    expected = set(BASE_SECRET_FILES | EVIDENCE_FILES)
    if values.get("MAVERICK_OIDC_ENABLED", "").strip().lower() in _TRUE:
        expected.update(OIDC_SECRET_FILES)
    return frozenset(expected)


def _decode_mount_field(value: str) -> str:
    return _MOUNT_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def parse_mountinfo(text: str) -> tuple[Mount, ...]:
    """Parse the subset of Linux mountinfo needed by the contract."""
    mounts: list[Mount] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        before, separator, after = line.partition(" - ")
        fields = before.split()
        tail = after.split()
        if not separator or len(fields) < 6 or len(tail) < 2:
            raise ProductionRuntimeContractError("malformed Linux mountinfo")
        mounts.append(
            Mount(
                mount_point=_decode_mount_field(fields[4]),
                options=frozenset(fields[5].split(",")),
                fs_type=tail[0],
                source=_decode_mount_field(tail[1]),
            )
        )
    if not mounts:
        raise ProductionRuntimeContractError("Linux mountinfo is empty")
    return tuple(mounts)


def _is_within(path: str, root: str) -> bool:
    candidate = PurePosixPath(path)
    boundary = PurePosixPath(root)
    return candidate == boundary or boundary in candidate.parents


def _normalized_posix_path(path: Any) -> str:
    """Normalize a Linux contract path without host-platform path semantics."""
    return str(PurePosixPath(str(path)))


def _mount_for(path: str, mounts: Iterable[Mount]) -> Mount | None:
    matches = [mount for mount in mounts if _is_within(path, mount.mount_point)]
    if not matches:
        return None
    return max(matches, key=lambda mount: len(PurePosixPath(mount.mount_point).parts))


def mount_contract_violations(mountinfo_text: str) -> tuple[str, ...]:
    """Check the live application's mount view without creating probe files."""
    try:
        mounts = parse_mountinfo(mountinfo_text)
    except ProductionRuntimeContractError as exc:
        return (str(exc),)
    violations: list[str] = []
    points = [mount.mount_point for mount in mounts]
    for path in {"/", *WRITABLE_MOUNTS, SECRET_ROOT}:
        if points.count(path) != 1:
            violations.append(f"production mount point is not unique: {path}")
    by_point = {mount.mount_point: mount for mount in mounts}
    root = by_point.get("/")
    if root is None or "ro" not in root.options or "rw" in root.options:
        violations.append("container root filesystem is not mounted read-only")

    for path in WRITABLE_MOUNTS:
        mount = by_point.get(path)
        if mount is None or "rw" not in mount.options or "ro" in mount.options:
            violations.append(f"required writable mount is unavailable: {path}")
    secret_mount = by_point.get(SECRET_ROOT)
    if (
        secret_mount is None
        or "ro" not in secret_mount.options
        or "rw" in secret_mount.options
    ):
        violations.append("staged secret custody is not an exact read-only mount")

    for path in READ_ONLY_INPUTS:
        mount = _mount_for(path, mounts)
        if mount is None or "ro" not in mount.options or "rw" in mount.options:
            violations.append(f"production input is not read-only: {path}")

    # Admit only the reviewed application mounts and the container platform's
    # virtual kernel trees/three Kubernetes identity files.  This prevents an
    # injected host repository, socket, credential directory, or arbitrary
    # read-only volume anywhere in the filesystem, not just below /workspace.
    approved = frozenset(
        {"/", *WRITABLE_MOUNTS, SECRET_ROOT, *PLATFORM_MOUNT_POINTS}
    )
    for mount in mounts:
        if mount.mount_point in approved:
            continue
        if any(
            _is_within(mount.mount_point, prefix)
            for prefix in PLATFORM_MOUNT_PREFIXES
        ):
            continue
        violations.append(f"unexpected production mount: {mount.mount_point}")
    for path in FORBIDDEN_PATHS:
        if path in by_point:
            violations.append(f"forbidden host/control-plane mount is present: {path}")
    return tuple(dict.fromkeys(violations))


def _expected_environment() -> dict[str, str]:
    return {
        "HOME": "/home/maverick",
        "MAVERICK_DEFAULT_MAX_DEPTH": str(NESTED_AGENT_DEPTH),
        "MAVERICK_DEFAULT_MAX_WALL_SECONDS": str(GOAL_WALL_SECONDS),
        "MAVERICK_HOME": STATE_ROOT,
        "MAVERICK_MAX_CONCURRENT_GOALS": str(CONCURRENT_GOALS),
        "MAVERICK_MAX_CONCURRENT_GOALS_PER_PRINCIPAL": str(
            CONCURRENT_GOALS_PER_PRINCIPAL
        ),
        "MAVERICK_MAX_TOOL_RESULT_BYTES": str(TOOL_RESULT_BYTES),
        "MAVERICK_MAX_TOTAL_SPAWNS": str(TOTAL_CHILD_AGENTS),
        "MAVERICK_PRODUCTION_MAX_TOOL_CALLS": str(TOOL_CALLS_PER_GOAL),
        "MAVERICK_RUNTIME_FILE_SIZE_BYTES": str(SINGLE_FILE_BYTES),
        "MAVERICK_RUNTIME_NOFILE": str(OPEN_FILE_LIMIT),
        "MAVERICK_RUNTIME_PIDS": str(PID_LIMIT),
        "MAVERICK_SANDBOX_BACKEND": "disabled",
        "TMPDIR": TMP_ROOT,
    }


def environment_contract_violations(
    env: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    values = os.environ if env is None else env
    violations: list[str] = []
    for name, expected in _expected_environment().items():
        if values.get(name) != expected:
            violations.append(f"runtime environment mismatch: {name}")
    for name in FORBIDDEN_SECRET_FILES:
        if str(values.get(name, "")).strip():
            violations.append(f"forbidden ambient credential is present: {name}")
    for name in FILE_ONLY_SECRET_ENV:
        if str(values.get(name, "")).strip():
            violations.append(f"file-custody secret is present in the environment: {name}")
    return tuple(violations)


def filesystem_path_violations(
    *,
    exists: Any = os.path.lexists,
    isdir: Any = os.path.isdir,
    islink: Any = os.path.islink,
    listdir: Any = os.listdir,
    realpath: Any = os.path.realpath,
    env: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Validate path absence/separation and the exact staged-secret roster."""
    violations: list[str] = []
    for path in (*WRITABLE_MOUNTS, *READ_ONLY_INPUTS):
        if not exists(path):
            violations.append(f"required production path is missing: {path}")
        elif islink(path):
            violations.append(f"production path must not be a symlink: {path}")
        elif not isdir(path):
            violations.append(f"production path must be a directory: {path}")
        elif _normalized_posix_path(realpath(path)) != path:
            violations.append(f"production path has a linked parent: {path}")
    for path in FORBIDDEN_PATHS:
        if exists(path):
            violations.append(f"forbidden host/control-plane path is visible: {path}")
    try:
        actual = frozenset(str(name) for name in listdir(SECRET_ROOT))
    except OSError:
        violations.append("staged secret custody cannot be enumerated")
    else:
        expected = expected_secret_files(env)
        if actual != expected:
            violations.append("staged secret custody does not match the exact allowlist")
        forbidden = actual.intersection(FORBIDDEN_SECRET_FILES)
        if forbidden:
            violations.append("staged secret custody contains a forbidden credential")
    workspace_real = _normalized_posix_path(realpath(TOOL_WORKSPACE))
    state_real = _normalized_posix_path(realpath(STATE_ROOT))
    secret_real = _normalized_posix_path(realpath(SECRET_ROOT))
    if (
        _is_within(state_real, workspace_real)
        or _is_within(secret_real, workspace_real)
        or _is_within(workspace_real, state_real)
        or _is_within(workspace_real, secret_real)
    ):
        violations.append("tool workspace overlaps protected control-plane state")
    return tuple(violations)


def _rlimit_resources() -> dict[str, tuple[int, int]]:
    try:
        import resource
    except ImportError as exc:  # pragma: no cover - production is Linux
        raise ProductionRuntimeContractError(
            "POSIX resource limits are unavailable"
        ) from exc
    return {
        "core": (resource.RLIMIT_CORE, 0),
        "file_size": (resource.RLIMIT_FSIZE, SINGLE_FILE_BYTES),
        "open_files": (resource.RLIMIT_NOFILE, OPEN_FILE_LIMIT),
        "processes": (resource.RLIMIT_NPROC, PID_LIMIT),
    }


def install_process_limits() -> None:
    """Lower soft and hard limits before replacing the PID-1 launcher."""
    import resource

    for label, (kind, limit) in _rlimit_resources().items():
        try:
            resource.setrlimit(kind, (limit, limit))
        except (OSError, ValueError) as exc:
            raise ProductionRuntimeContractError(
                f"could not install {label} resource limit"
            ) from exc


def rlimit_contract_violations() -> tuple[str, ...]:
    import resource

    violations: list[str] = []
    for label, (kind, expected) in _rlimit_resources().items():
        try:
            actual = resource.getrlimit(kind)
        except (OSError, ValueError):
            violations.append(f"{label} resource limit is unavailable")
            continue
        if actual != (expected, expected):
            violations.append(f"{label} resource limit is not exact")
    return tuple(violations)


def clamp_budget_for_production(budget: Any) -> None:
    """Lower a mutable/configured Budget to the immutable production maxima."""
    from .production_scope import production_surface_required

    if not production_surface_required():
        return
    ceilings = {
        "max_input_tokens": INPUT_TOKENS_PER_GOAL,
        "max_output_tokens": OUTPUT_TOKENS_PER_GOAL,
        "max_tool_calls": TOOL_CALLS_PER_GOAL,
        "max_wall_seconds": float(GOAL_WALL_SECONDS),
    }
    for field, ceiling in ceilings.items():
        current = getattr(budget, field, ceiling)
        try:
            numeric = float(current)
            if isinstance(current, bool) or not math.isfinite(numeric):
                raise ValueError("non-finite or boolean")
            value = min(current, ceiling)
        except (TypeError, ValueError) as exc:
            raise ProductionRuntimeContractError(
                f"production budget {field} is invalid"
            ) from exc
        setattr(budget, field, value)


def require_agent_depth(max_depth: int) -> int:
    """Refuse a caller that attempts to raise the immutable spawn depth."""
    from .production_scope import production_surface_required

    if not production_surface_required():
        return max_depth
    if isinstance(max_depth, bool) or not isinstance(max_depth, int):
        raise ProductionRuntimeContractError("production agent depth is invalid")
    if not 1 <= max_depth <= NESTED_AGENT_DEPTH:
        raise ProductionRuntimeContractError(
            f"production agent depth exceeds {NESTED_AGENT_DEPTH}"
        )
    return max_depth


def require_tool_workspace(path: str | Path) -> Path:
    """Bind read-only production tools to the image workspace, never state."""
    candidate = Path(path).expanduser()
    from .production_scope import production_surface_required

    if not production_surface_required():
        return candidate
    if os.path.normpath(os.fspath(candidate)) != TOOL_WORKSPACE:
        raise ProductionRuntimeContractError(
            f"production tool workspace must be exactly {TOOL_WORKSPACE}"
        )
    return candidate


def live_runtime_violations() -> tuple[str, ...]:
    """Return locally observable startup violations for the running pod."""
    violations: list[str] = []
    if platform.system() != OPERATING_SYSTEM:
        violations.append("production runtime contract requires Linux")
    if getattr(os, "geteuid", lambda: -1)() != RUN_AS_UID:
        violations.append("production runtime must run as uid 1000")
    if getattr(os, "getegid", lambda: -1)() != RUN_AS_GID:
        violations.append("production runtime must run as gid 1000")
    if os.getpid() != 1:
        violations.append("production launcher is not container PID 1")
    violations.extend(environment_contract_violations())
    violations.extend(rlimit_contract_violations())
    mountinfo: str | None = None
    try:
        mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    except OSError:
        violations.append("Linux mountinfo is unavailable")
    else:
        violations.extend(mount_contract_violations(mountinfo))
    violations.extend(filesystem_path_violations())
    purelib = sysconfig.get_paths().get("purelib")
    if not purelib:
        violations.append("installed package path is unavailable")
    elif mountinfo is not None:
        package_mount = _mount_for(purelib, parse_mountinfo(mountinfo))
        if package_mount is None or "ro" not in package_mount.options:
            violations.append("installed package inputs are not read-only")
    return tuple(dict.fromkeys(violations))


def enforce_live_runtime_contract() -> None:
    """Install and verify the exact boundary before any application import."""
    from .production_scope import production_surface_required

    if not production_surface_required():
        return
    if not runtime_contract_required():
        raise ProductionRuntimeContractError(
            f"{REQUIRE_ENV}=1 is required by the production artifact"
        )
    load_contract()
    install_process_limits()
    violations = live_runtime_violations()
    if violations:
        raise ProductionRuntimeContractError("; ".join(violations))


def main(argv: list[str] | None = None) -> int:
    """PID-1 launcher: preflight, then exec the supported operator CLI."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        enforce_live_runtime_contract()
    except ProductionRuntimeContractError as exc:
        print(f"production runtime preflight refused: {exc}", file=sys.stderr)
        return 78
    os.execvp("maverick", ["maverick", *args])
    return 127  # pragma: no cover - exec replaces the process


__all__ = [
    "BASE_SECRET_FILES",
    "CONCURRENT_GOALS",
    "CONCURRENT_GOALS_PER_PRINCIPAL",
    "CONTRACT_ENV",
    "EXPECTED_DIGEST_ENV",
    "EVIDENCE_FILES",
    "FILE_ONLY_SECRET_ENV",
    "FORBIDDEN_PATHS",
    "FORBIDDEN_SECRET_FILES",
    "GOAL_WALL_SECONDS",
    "INPUT_TOKENS_PER_GOAL",
    "NESTED_AGENT_DEPTH",
    "OIDC_SECRET_FILES",
    "OPEN_FILE_LIMIT",
    "OPERATING_SYSTEM",
    "OUTPUT_TOKENS_PER_GOAL",
    "PID_LIMIT",
    "PLATFORM_MOUNT_POINTS",
    "PLATFORM_MOUNT_PREFIXES",
    "ProductionRuntimeContractError",
    "REQUIRE_ENV",
    "RUN_AS_GID",
    "RUN_AS_UID",
    "SECRET_ROOT",
    "SINGLE_FILE_BYTES",
    "STATE_ROOT",
    "TMP_ROOT",
    "TOOL_CALLS_PER_GOAL",
    "TOOL_RESULT_BYTES",
    "TOOL_WORKSPACE",
    "TOTAL_CHILD_AGENTS",
    "canonical_contract_bytes",
    "clamp_budget_for_production",
    "contract_document",
    "enforce_live_runtime_contract",
    "environment_contract_violations",
    "expected_secret_files",
    "filesystem_path_violations",
    "install_process_limits",
    "live_runtime_violations",
    "load_contract",
    "main",
    "mount_contract_violations",
    "parse_mountinfo",
    "require_agent_depth",
    "require_tool_workspace",
    "rlimit_contract_violations",
    "runtime_contract_required",
]


if __name__ == "__main__":  # pragma: no cover - exercised by container startup
    raise SystemExit(main())
