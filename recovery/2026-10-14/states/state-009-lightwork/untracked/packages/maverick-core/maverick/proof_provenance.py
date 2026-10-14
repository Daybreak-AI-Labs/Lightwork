"""Revision and runtime provenance for locally generated proof artifacts.

The proof harnesses intentionally make bounded claims.  This module does not
turn those claims into release evidence; it only makes an execution
attributable to the exact source tree and Python environment that produced it.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import unicodedata
import zlib
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# These files are deterministic governance views generated *after* the proof
# results.  They bind or route the proof artifacts, so including their bytes in
# the proof's source digest creates an impossible cycle:
#
#   proof -> production inventory -> claim ledger / release state -> proof
#
# Keep this allow-list file-specific.  In particular, do not exclude ``release``
# as a directory: contracts, verifier inputs, source, tests, and harnesses must
# continue to affect the proof source digest.
DOWNSTREAM_GOVERNANCE_ARTIFACTS = (
    "release/production-code-inventory.json",
    "release/claim-evidence-ledger.json",
    "release/release-state.json",
)

SOURCE_CONTENT_IDENTITY_SCHEMA = "lightwork.git-source-content.v2"
SOURCE_CONTENT_DIGEST_SCOPE = (
    "canonical JSON of the schema, exact exclusions, and sorted repository-relative "
    "kind/path/mode/symlink-target-kind/direct-SHA-256/byte-length records after "
    "deterministic Git clean normalization with core.autocrlf=true; Gitlinks "
    "recursively bind the same v2 identity"
)
_SOURCE_CONTENT_IDENTITY_KEYS = {
    "schema",
    "path_count",
    "byte_count",
    "sha256",
    "digest_algorithm",
    "digest_scope",
}

MATERIALIZED_TREE_IDENTITY_SCHEMA = "lightwork.materialized-source-tree.v1"
MATERIALIZED_TREE_DIGEST_SCOPE = (
    "canonical JSON of sorted repository-relative directory, regular-file, and safe "
    "symlink records; regular files bind exact bytes and executable semantics; symlinks "
    "bind normalized in-tree destinations and target kinds"
)
TRUSTED_GIT_MAX_CAPTURE_BYTES = 64 * 1024 * 1024
TRUSTED_GIT_MAX_TIMEOUT_SECONDS = 300.0
SYMLINK_MATERIALIZATION_POLICY = (
    "native-posix-copy-bound-direct-file-windows-v1"
)
SEMANTIC_EXECUTION_TREE_IDENTITY_SCHEMA = "lightwork.semantic-execution-tree.v1"
SEMANTIC_EXECUTION_TREE_DIGEST_SCOPE = (
    "canonical JSON of the portable v2 source-content identity and deterministic "
    "regular-file and symlink execution semantics"
)
_SEMANTIC_REGULAR_FILE_SEMANTICS = (
    "exact bound regular-file bytes and Git executable mode"
)
_SEMANTIC_SYMLINK_SEMANTICS = (
    "bound repository-relative target; POSIX native link; Windows exact-byte alias "
    "only for a direct bound non-executable regular file"
)
_TRUSTED_GIT_READ_ONLY_COMMANDS = frozenset(
    {"cat-file", "diff", "ls-files", "merge-base"}
)

_PROCESS_TREE_CONTROL_LOCK = threading.Lock()
_PROCESS_TREE_POLL_SECONDS = 0.005
_WINDOWS_JOB_ACCOUNTING_SETTLE_SECONDS = 0.25
_WINDOWS_CREATE_SUSPENDED = 0x00000004

# The source-content-bound fixture runner admits only operating-system variables that
# are necessary to locate subprocess executables on supported hosts.  Every
# other parent variable is discarded before fixed fixture values are applied.
# Values from this allow-list are digest-bound but are not serialized.
FIXTURE_SYSTEM_ENV_ALLOWLIST = (
    "COMSPEC",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "WINDIR",
)

# These names document known credential, endpoint, and tool-injection inputs.
# The construction rule is stronger than this list: *every* parent variable
# outside FIXTURE_SYSTEM_ENV_ALLOWLIST is discarded.
FIXTURE_DISCARDED_ENV_PREFIXES = (
    "MAVERICK_",
    "LIGHTWORK_",
    "PYTHON",
    "PYTEST_",
)
FIXTURE_KNOWN_DISCARDED_ENV_NAMES = (
    "ALL_PROXY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_CREDENTIALS",
    "AWS_ACCESS_KEY_ID",
    "AWS_CONFIG_FILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_ENDPOINT_URL",
    "AWS_PROFILE",
    "AWS_REGION",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AZURE_OPENAI_AD_TOKEN",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_AUTH",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_TOKEN_SCOPE",
    "BEDROCK_API_KEY",
    "CLOUDSDK_CONFIG",
    "CODEX_ACCESS_TOKEN",
    "COHERE_API_KEY",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GROK_API_KEY",
    "GROQ_API_KEY",
    "HF_TOKEN",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "HUGGINGFACEHUB_API_TOKEN",
    "MOONSHOT_API_KEY",
    "NO_PROXY",
    "OLLAMA_HOST",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_COMPATIBLE_API_KEY",
    "OPENAI_COMPATIBLE_BASE_URL",
    "OPENROUTER_API_KEY",
    "PYTEST_ADDOPTS",
    "PYTEST_PLUGINS",
    "TGI_API_KEY",
    "TGI_BASE_URL",
    "TOGETHER_API_KEY",
    "VERTEX_LOCATION",
    "VLLM_API_KEY",
    "VLLM_BASE_URL",
    "XAI_API_KEY",
)

_FIXTURE_FIXED_ENV_NAMES = (
    "GIT_CONFIG_NOSYSTEM",
    "GIT_TERMINAL_PROMPT",
    "HOME",
    "LANG",
    "LC_ALL",
    "MAVERICK_CLIENT_ID",
    "MAVERICK_CONFIG",
    "MAVERICK_HOME",
    "NO_COLOR",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONBREAKPOINT",
    "PYTHONHASHSEED",
    "PYTHONIOENCODING",
    "PYTHONNOUSERSITE",
    "PYTHONPATH",
    "PYTHONSAFEPATH",
    "PYTHONUTF8",
    "PYTHONWARNINGS",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    "TEMP",
    "TMP",
    "TMPDIR",
    "TZ",
    "USERPROFILE",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
)
_FIXTURE_ENV_POLICY_ID = "lightwork-proof-fixture-environment-v1"
_FIXTURE_ENV_DIGEST_SCOPE = (
    "sorted admitted variable names plus SHA-256 of each exact value; "
    "environment values are not serialized"
)
_FIXTURE_GIT_CONFIG = b"[core]\n\tautocrlf = true\n\tsafecrlf = false\n"


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a JSON value in the canonical form used by evidence digests."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def utc_timestamp(epoch: float) -> str:
    """Return an unambiguous UTC timestamp while retaining sub-second precision."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def command_record(argv: Sequence[str]) -> dict[str, Any]:
    """Retain exact argv tokens plus a human-readable, shell-escaped rendering."""
    tokens = [str(token) for token in argv]
    if os.name == "nt":
        display = subprocess.list2cmdline(tokens)
    else:
        display = shlex.join(tokens)
    return {"argv": tokens, "display": display}


def proof_source_exclusions(
    root: Path,
    *,
    generated_paths: Iterable[Path] = (),
) -> list[str]:
    """Return the exact generated-artifact exclusions for a proof source digest.

    ``generated_paths`` names the proof artifacts being written by the current
    command.  Paths outside the repository are not part of the worktree and are
    ignored.  The only additional exclusions are the three file-specific,
    deterministic downstream governance artifacts above.
    """
    repository_root = root.resolve()
    exclusions = set(DOWNSTREAM_GOVERNANCE_ARTIFACTS)
    for path in generated_paths:
        try:
            relative = path.resolve().relative_to(repository_root)
        except ValueError:
            continue
        exclusions.add(relative.as_posix().strip("/"))
    return list(_normalized_exclusions(exclusions))


def fixture_environment_policy() -> dict[str, Any]:
    """Return the non-secret, deterministic fixture environment policy."""
    return {
        "id": _FIXTURE_ENV_POLICY_ID,
        "parent_inherit_allowlist": list(FIXTURE_SYSTEM_ENV_ALLOWLIST),
        "fixed_names": list(_FIXTURE_FIXED_ENV_NAMES),
        "discarded_prefixes": list(FIXTURE_DISCARDED_ENV_PREFIXES),
        "known_discarded_names": list(FIXTURE_KNOWN_DISCARDED_ENV_NAMES),
        "isolated_home_files": [
            {
                "path": ".gitconfig",
                "sha256": sha256_bytes(_FIXTURE_GIT_CONFIG),
            }
        ],
        "unlisted_parent_variables": "discarded",
        "secret_values_serialized": False,
    }


def _fixture_fixed_environment(root: Path, runtime_home: Path) -> dict[str, str]:
    repository_root = root.resolve()
    isolated_home = runtime_home.resolve()
    temp_dir = isolated_home / "tmp"
    xdg_config = isolated_home / "xdg-config"
    xdg_cache = isolated_home / "xdg-cache"
    xdg_data = isolated_home / "xdg-data"
    for path in (isolated_home, temp_dir, xdg_config, xdg_cache, xdg_data):
        path.mkdir(parents=True, exist_ok=True)
    (isolated_home / ".gitconfig").write_bytes(_FIXTURE_GIT_CONFIG)
    return {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(isolated_home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "MAVERICK_CLIENT_ID": "proof-fixture-local",
        "MAVERICK_CONFIG": str(isolated_home / "config.toml"),
        "MAVERICK_HOME": str(isolated_home),
        "NO_COLOR": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONBREAKPOINT": "0",
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": os.pathsep.join(
            (
                str((repository_root / "packages" / "maverick-core").resolve()),
                str(repository_root),
            )
        ),
        "PYTHONSAFEPATH": "1",
        "PYTHONUTF8": "1",
        "PYTHONWARNINGS": "default",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "TEMP": str(temp_dir),
        "TMP": str(temp_dir),
        "TMPDIR": str(temp_dir),
        "TZ": "UTC",
        "USERPROFILE": str(isolated_home),
        "XDG_CACHE_HOME": str(xdg_cache),
        "XDG_CONFIG_HOME": str(xdg_config),
        "XDG_DATA_HOME": str(xdg_data),
    }


def _environment_value_records(environment: Mapping[str, str]) -> list[dict[str, str]]:
    return [
        {
            "name": name,
            "sha256": sha256_bytes(
                str(environment[name]).encode("utf-8", errors="surrogateescape")
            ),
        }
        for name in sorted(environment)
    ]


def fixture_execution_environment(
    root: Path,
    runtime_home: Path,
    *,
    parent_environment: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    """Build and provenance-bind the sanitized fixture child environment.

    Returns ``(child_environment, fixed_values, provenance_record)``.  The
    record contains names and hashes only for inherited system values; secret
    or configuration values discarded from the parent are never serialized.
    """
    parent = os.environ if parent_environment is None else parent_environment
    inherited = {
        name: str(parent[name])
        for name in FIXTURE_SYSTEM_ENV_ALLOWLIST
        if name in parent
    }
    fixed = _fixture_fixed_environment(root, runtime_home)
    if set(fixed) != set(_FIXTURE_FIXED_ENV_NAMES):  # pragma: no cover - invariant
        raise RuntimeError("fixture environment fixed-name policy drifted")
    environment = {**inherited, **fixed}
    policy = fixture_environment_policy()
    value_records = _environment_value_records(environment)
    record = {
        "policy": policy,
        "policy_sha256": sha256_bytes(canonical_json_bytes(policy)),
        "admitted_names": sorted(environment),
        "inherited_names": sorted(inherited),
        "value_sha256": value_records,
        "environment_sha256": sha256_bytes(canonical_json_bytes(value_records)),
        "environment_digest_scope": _FIXTURE_ENV_DIGEST_SCOPE,
    }
    return environment, fixed, record


def validate_fixture_environment_provenance(
    record: Any,
    fixed_values: Any,
) -> tuple[bool, str]:
    """Validate a fixture environment record without recovering any secret value."""
    expected_policy = fixture_environment_policy()
    if not isinstance(record, dict) or set(record) != {
        "policy",
        "policy_sha256",
        "admitted_names",
        "inherited_names",
        "value_sha256",
        "environment_sha256",
        "environment_digest_scope",
    }:
        return False, "fixture environment provenance schema drifted"
    if (
        record["policy"] != expected_policy
        or record["policy_sha256"]
        != sha256_bytes(canonical_json_bytes(expected_policy))
        or record["environment_digest_scope"] != _FIXTURE_ENV_DIGEST_SCOPE
    ):
        return False, "fixture environment policy drifted"
    if (
        not isinstance(fixed_values, dict)
        or set(fixed_values) != set(_FIXTURE_FIXED_ENV_NAMES)
        or not all(isinstance(value, str) for value in fixed_values.values())
    ):
        return False, "fixture environment fixed values drifted"
    admitted = record["admitted_names"]
    inherited = record["inherited_names"]
    values = record["value_sha256"]
    if (
        not isinstance(admitted, list)
        or admitted != sorted(set(admitted))
        or not all(isinstance(name, str) and name for name in admitted)
        or not isinstance(inherited, list)
        or inherited != sorted(set(inherited))
        or not set(inherited).issubset(FIXTURE_SYSTEM_ENV_ALLOWLIST)
        or admitted != sorted({*inherited, *fixed_values})
        or not isinstance(values, list)
        or len(values) != len(admitted)
    ):
        return False, "fixture environment admitted-name inventory drifted"
    expected_names = iter(admitted)
    value_map: dict[str, str] = {}
    for item in values:
        expected_name = next(expected_names)
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "sha256"}
            or item.get("name") != expected_name
            or not isinstance(item.get("sha256"), str)
            or len(item["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in item["sha256"])
        ):
            return False, "fixture environment value digest inventory drifted"
        value_map[expected_name] = item["sha256"]
    for name, value in fixed_values.items():
        if value_map.get(name) != sha256_bytes(
            value.encode("utf-8", errors="surrogateescape")
        ):
            return False, f"fixture environment fixed-value digest drifted: {name}"
    if record["environment_sha256"] != sha256_bytes(canonical_json_bytes(values)):
        return False, "fixture environment aggregate digest drifted"
    return True, "fixture environment policy, admitted names, and value digests are bound"


def _sanitized_git_environment(
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    admitted_system = (
        "COMSPEC",
        "OS",
        "PATHEXT",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "WINDIR",
    )
    environment = {
        name: str(os.environ[name]) for name in admitted_system if name in os.environ
    }
    environment.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
    )
    if overrides:
        environment.update({str(name): str(value) for name, value in overrides.items()})
    return environment


@dataclass(frozen=True)
class _GitRuntime:
    executable: Path
    version: str
    sha256: str

    def record(self) -> dict[str, Any]:
        return {
            "executable": str(self.executable),
            "version": self.version,
            "sha256": self.sha256,
            "digest_scope": "exact Git executable bytes; diagnostic runtime binding",
            "trust": "trusted-host",
            "hash_to_execution_atomic": False,
        }


@dataclass(frozen=True)
class ProcessTreeSeal:
    """Result of proving that one controlled subprocess tree is empty."""

    clean: bool
    descendant_survived: bool
    control_failure: str | None


def _linux_process_table() -> dict[int, tuple[int, int, str]]:
    """Return PID -> (PPID, start time, state) without trusting process names."""
    proc = Path("/proc")
    if not proc.is_dir():
        raise RuntimeError("Linux process-tree control requires /proc")
    table: dict[int, tuple[int, int, str]] = {}
    try:
        entries = list(proc.iterdir())
    except OSError as exc:
        raise RuntimeError("Linux process table could not be enumerated") from exc
    for entry in entries:
        if not entry.name.isascii() or not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text(encoding="utf-8", errors="strict")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        except (OSError, UnicodeError) as exc:
            raise RuntimeError("Linux process table could not be read") from exc
        close = raw.rfind(")")
        if close < 2:
            raise RuntimeError("Linux process table contains a malformed stat record")
        fields = raw[close + 2 :].split()
        if len(fields) < 20:
            raise RuntimeError("Linux process table contains a truncated stat record")
        try:
            pid = int(entry.name)
            parent_pid = int(fields[1])
            start_time = int(fields[19])
        except ValueError as exc:
            raise RuntimeError("Linux process table contains invalid numeric fields") from exc
        table[pid] = (parent_pid, start_time, fields[0])
    return table


def _linux_subreaper_state() -> int:
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.restype = ctypes.c_int
    value = ctypes.c_int()
    if prctl(37, ctypes.byref(value), 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if value.value not in {0, 1}:
        raise RuntimeError("Linux returned an invalid child-subreaper state")
    return int(value.value)


def _set_linux_subreaper(enabled: int) -> None:
    import ctypes

    if enabled not in {0, 1}:
        raise ValueError("child-subreaper state must be zero or one")
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.restype = ctypes.c_int
    if prctl(36, enabled, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _windows_create_kill_job() -> tuple[Any, Any]:
    import ctypes
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
    )
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    information = ExtendedLimitInformation()
    information.BasicLimitInformation.LimitFlags = 0x00002000
    if not kernel32.SetInformationJobObject(
        job,
        9,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise ctypes.WinError(error)
    return kernel32, job


def _windows_job_active_processes(job: tuple[Any, Any]) -> int:
    import ctypes

    kernel32, handle = job
    accounting = ctypes.create_string_buffer(48)
    if not kernel32.QueryInformationJobObject(
        handle,
        1,
        accounting,
        len(accounting),
        None,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(ctypes.c_ulong.from_buffer(accounting, 40).value)


def _settled_windows_job_active_processes(job: tuple[Any, Any]) -> int:
    deadline = time.monotonic() + _WINDOWS_JOB_ACCOUNTING_SETTLE_SECONDS
    while True:
        active = _windows_job_active_processes(job)
        if active == 0:
            return 0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return active
        time.sleep(min(_PROCESS_TREE_POLL_SECONDS, remaining))


class ProcessTreeController:
    """Own one subprocess tree and prove it empty before returning control.

    Linux control is process-wide because ``PR_SET_CHILD_SUBREAPER`` is
    process-wide.  Entry therefore refuses another controller, any pre-existing
    direct child, or more than one thread.  Source code executing under the same
    UID remains a disclosed trusted-host seam because it can attack ``/proc``
    observation or the verifier process itself.

    Windows starts the root process suspended, assigns it to a kill-on-close Job
    Object, and only then resumes it.  This removes the Popen-to-assignment escape
    window instead of attempting to repair that race after child code has run.
    """

    def __init__(self) -> None:
        self._entered = False
        self._attached = False
        self._process: subprocess.Popen[bytes] | None = None
        self._seal: ProcessTreeSeal | None = None
        self._tree_empty_verified = False
        self._linux_previous_subreaper: int | None = None
        self._linux_lock_held = False
        self._linux_root_identity: tuple[int, int] | None = None
        self._linux_tracked: dict[int, int] = {}
        self._linux_observation_failure: str | None = None
        self._linux_state_lock = threading.Lock()
        self._linux_stop = threading.Event()
        self._linux_monitor: threading.Thread | None = None
        self._windows_job: tuple[Any, Any] | None = None

    def __enter__(self) -> ProcessTreeController:
        if self._entered:
            raise RuntimeError("process-tree controller cannot be re-entered")
        if os.name == "nt":
            self._entered = True
            return self
        if not sys.platform.startswith("linux"):
            raise RuntimeError("process-tree control supports only Linux and Windows")
        if not _PROCESS_TREE_CONTROL_LOCK.acquire(blocking=False):
            raise RuntimeError("another process-tree controller is already active")
        self._linux_lock_held = True
        try:
            tasks = list(Path("/proc/self/task").iterdir())
            if len(tasks) != 1:
                raise RuntimeError(
                    "Linux process-tree control requires a single-threaded caller at entry"
                )
            table = _linux_process_table()
            if any(parent == os.getpid() for parent, _start, _state in table.values()):
                raise RuntimeError(
                    "Linux process-tree control requires no pre-existing direct children"
                )
            previous = _linux_subreaper_state()
            _set_linux_subreaper(1)
            if _linux_subreaper_state() != 1:
                raise RuntimeError("Linux child-subreaper state did not take effect")
            self._linux_previous_subreaper = previous
            self._entered = True
            return self
        except Exception:
            if self._linux_previous_subreaper is not None:
                try:
                    _set_linux_subreaper(self._linux_previous_subreaper)
                except OSError:
                    pass
            self._linux_previous_subreaper = None
            self._linux_lock_held = False
            _PROCESS_TREE_CONTROL_LOCK.release()
            raise

    @property
    def popen_kwargs(self) -> dict[str, object]:
        if not self._entered or self._attached:
            raise RuntimeError("process-tree Popen options are unavailable in this state")
        if os.name == "nt":
            return {
                "creationflags": (
                    _WINDOWS_CREATE_SUSPENDED
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            }
        return {"start_new_session": True}

    def _observe_linux(self) -> dict[int, tuple[int, int, str]]:
        try:
            table = _linux_process_table()
            process_pid = self._process.pid if self._process is not None else None
            with self._linux_state_lock:
                admitted: set[int] = {
                    pid
                    for pid, (parent, _start, _state) in table.items()
                    if parent == os.getpid()
                }
                if process_pid in table:
                    admitted.add(int(process_pid))
                admitted.update(
                    pid
                    for pid, start in self._linux_tracked.items()
                    if pid in table and table[pid][1] == start
                )
                changed = True
                while changed:
                    changed = False
                    for pid, (parent, _start, _state) in table.items():
                        if pid not in admitted and parent in admitted:
                            admitted.add(pid)
                            changed = True
                for pid in admitted:
                    self._linux_tracked[pid] = table[pid][1]
            return table
        except (OSError, RuntimeError) as exc:
            with self._linux_state_lock:
                self._linux_observation_failure = (
                    f"{type(exc).__name__}:{str(exc)[:200]}"
                )
            return {}

    def _monitor_linux(self) -> None:
        while not self._linux_stop.wait(_PROCESS_TREE_POLL_SECONDS):
            self._observe_linux()

    def _attach_windows(self, process: subprocess.Popen[bytes]) -> None:
        import ctypes
        from ctypes import wintypes

        job: tuple[Any, Any] | None = None
        try:
            job = _windows_create_kill_job()
            kernel32, handle = job
            process_handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
            if not kernel32.AssignProcessToJobObject(handle, process_handle):
                raise ctypes.WinError(ctypes.get_last_error())
            ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
            ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
            ntdll.NtResumeProcess.restype = ctypes.c_long
            status = int(ntdll.NtResumeProcess(process_handle))
            if status != 0:
                raise OSError(f"NtResumeProcess failed with NTSTATUS 0x{status & 0xFFFFFFFF:08x}")
            self._windows_job = job
        except Exception:
            if job is not None:
                kernel32, handle = job
                try:
                    kernel32.TerminateJobObject(handle, 1)
                finally:
                    kernel32.CloseHandle(handle)
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.SubprocessError:
                pass
            raise

    def attach(self, process: subprocess.Popen[bytes]) -> None:
        """Attach the process before callers start readers or wait for output."""
        if not self._entered or self._attached or self._seal is not None:
            raise RuntimeError("process-tree controller cannot attach in this state")
        if process.pid <= 0:
            raise RuntimeError("subprocess did not expose a valid process identifier")
        self._process = process
        if os.name == "nt":
            self._attach_windows(process)
        else:
            try:
                table = self._observe_linux()
                row = table.get(process.pid)
                if row is not None:
                    if row[0] != os.getpid():
                        raise RuntimeError("controlled Linux root is not a direct child")
                    self._linux_root_identity = (process.pid, row[1])
                elif process.poll() is None:
                    raise RuntimeError("controlled Linux root is absent from /proc")
                self._linux_monitor = threading.Thread(
                    target=self._monitor_linux,
                    name="proof-process-tree-monitor",
                    daemon=True,
                )
                self._linux_monitor.start()
            except Exception:
                self._attached = True
                self._seal = self._seal_linux(terminate=True, timeout_seconds=10.0)
                raise
        self._attached = True

    def _seal_windows(self, *, terminate: bool) -> ProcessTreeSeal:
        job = self._windows_job
        if job is None:
            return ProcessTreeSeal(False, False, "windows_job_unavailable")
        kernel32, handle = job
        descendant_survived = False
        control_failure: str | None = None
        try:
            if terminate:
                if not kernel32.TerminateJobObject(handle, 1):
                    control_failure = "windows_job_termination_failed"
            else:
                try:
                    active = _settled_windows_job_active_processes(job)
                except (AttributeError, OSError, TypeError, ValueError):
                    active = -1
                    control_failure = "windows_job_accounting_unavailable"
                if active > 0:
                    descendant_survived = True
                    if not kernel32.TerminateJobObject(handle, 1):
                        control_failure = "windows_job_termination_failed"
            if control_failure is None or descendant_survived:
                try:
                    remaining = _settled_windows_job_active_processes(job)
                except (AttributeError, OSError, TypeError, ValueError):
                    remaining = -1
                    control_failure = "windows_job_accounting_unavailable"
                if remaining != 0:
                    control_failure = "windows_job_not_empty_after_termination"
            self._tree_empty_verified = control_failure is None
        finally:
            kernel32.CloseHandle(handle)
            self._windows_job = None
        return ProcessTreeSeal(
            clean=not descendant_survived and control_failure is None,
            descendant_survived=descendant_survived,
            control_failure=control_failure,
        )

    def _seal_linux(self, *, terminate: bool, timeout_seconds: float) -> ProcessTreeSeal:
        process = self._process
        assert process is not None
        table = self._observe_linux()
        with self._linux_state_lock:
            observation_failure = self._linux_observation_failure
            tracked = dict(self._linux_tracked)
        root_identity = self._linux_root_identity
        live_descendants = [
            pid
            for pid, start in tracked.items()
            if (pid, start) != root_identity
            and pid in table
            and table[pid][1] == start
            and table[pid][2] != "Z"
        ]
        descendant_survived = bool(live_descendants) and not terminate
        deadline = time.monotonic() + timeout_seconds
        control_failure: str | None = None
        while True:
            table = self._observe_linux()
            with self._linux_state_lock:
                observation_failure = (
                    self._linux_observation_failure or observation_failure
                )
                tracked = dict(self._linux_tracked)
            targets = [
                (pid, start, table[pid][2])
                for pid, start in tracked.items()
                if pid in table
                and table[pid][1] == start
                and ((pid, start) != root_identity or terminate)
            ]
            for pid, _start, state in targets:
                if state == "Z":
                    if pid != process.pid and table[pid][0] == os.getpid():
                        try:
                            os.waitpid(pid, os.WNOHANG)
                        except (ChildProcessError, ProcessLookupError):
                            pass
                    continue
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except (OSError, PermissionError):
                    control_failure = "linux_descendant_termination_failed"
            if terminate:
                process.poll()
            table = self._observe_linux()
            with self._linux_state_lock:
                tracked = dict(self._linux_tracked)
            remaining = [
                (pid, start, table[pid][2])
                for pid, start in tracked.items()
                if pid in table
                and table[pid][1] == start
                and ((pid, start) != root_identity or terminate)
            ]
            if not remaining:
                self._tree_empty_verified = observation_failure is None
                break
            if time.monotonic() >= deadline:
                control_failure = control_failure or "linux_descendants_not_reaped"
                break
            time.sleep(_PROCESS_TREE_POLL_SECONDS)
        self._linux_stop.set()
        if self._linux_monitor is not None:
            self._linux_monitor.join(timeout=1)
            if self._linux_monitor.is_alive():
                control_failure = control_failure or "linux_monitor_did_not_stop"
        if observation_failure is not None:
            control_failure = control_failure or "linux_process_observation_failed"
        self._tree_empty_verified = self._tree_empty_verified and control_failure is None
        return ProcessTreeSeal(
            clean=not descendant_survived and control_failure is None,
            descendant_survived=descendant_survived,
            control_failure=control_failure,
        )

    def seal(
        self,
        process: subprocess.Popen[bytes],
        *,
        terminate: bool = False,
        timeout_seconds: float = 10.0,
    ) -> ProcessTreeSeal:
        """Seal the attached tree, killing every observed process fail-closed."""
        if (
            not self._entered
            or not self._attached
            or self._process is not process
        ):
            raise RuntimeError("process-tree controller is not attached to this process")
        if self._seal is not None:
            return self._seal
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not 0 < float(timeout_seconds) <= 60.0
        ):
            raise ValueError("process-tree seal timeout is outside its hard policy bound")
        if os.name == "nt":
            result = self._seal_windows(terminate=terminate)
        else:
            result = self._seal_linux(
                terminate=terminate,
                timeout_seconds=float(timeout_seconds),
            )
        self._seal = result
        return result

    def __exit__(self, exc_type, exc, traceback) -> bool:
        cleanup_error: Exception | None = None
        if self._attached and self._seal is None and self._process is not None:
            try:
                result = self.seal(self._process, terminate=True)
                if result.control_failure is not None:
                    cleanup_error = RuntimeError(result.control_failure)
            except Exception as cleanup_exc:  # pragma: no cover - last-resort cleanup
                cleanup_error = cleanup_exc
        if os.name != "nt" and self._linux_lock_held:
            try:
                if (
                    self._linux_previous_subreaper is not None
                    and (not self._attached or self._tree_empty_verified)
                ):
                    _set_linux_subreaper(self._linux_previous_subreaper)
            except OSError as restore_exc:
                cleanup_error = cleanup_error or restore_exc
            finally:
                self._linux_lock_held = False
                _PROCESS_TREE_CONTROL_LOCK.release()
        self._entered = False
        if cleanup_error is not None and exc_type is None:
            raise RuntimeError("process-tree controller cleanup failed") from cleanup_error
        return False


def process_tree_controller() -> ProcessTreeController:
    """Create one fail-closed Linux/Windows subprocess-tree controller."""
    return ProcessTreeController()


_ACTIVE_GIT_RUNTIME: ContextVar[_GitRuntime | None] = ContextVar(
    "lightwork_proof_git_runtime",
    default=None,
)


def _reject_linked_executable_path(path: Path) -> None:
    if not path.is_absolute():
        raise RuntimeError("Git executable did not resolve to an absolute path")
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        try:
            metadata = cursor.lstat()
        except FileNotFoundError as exc:
            raise RuntimeError("Git executable path disappeared during resolution") from exc
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(cursor):
            raise RuntimeError("Git executable path contains a symlink or reparse point")


def _resolve_git_runtime(root: Path) -> _GitRuntime:
    discovered = shutil.which("git")
    if not discovered:
        raise RuntimeError("Git executable is unavailable")
    raw_path = Path(os.path.abspath(discovered))
    _reject_linked_executable_path(raw_path)
    executable = raw_path.resolve(strict=True)
    _reject_linked_executable_path(executable)
    for untrusted_root in {
        root.resolve(),
        Path.cwd().resolve(),
        Path(tempfile.gettempdir()).resolve(),
    }:
        try:
            executable.relative_to(untrusted_root)
        except ValueError:
            continue
        raise RuntimeError("Git executable resolves inside the repository or cwd")
    metadata = executable.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or executable.is_symlink()
        or bool(reparse and attributes & reparse)
    ):
        raise RuntimeError("Git executable is not a regular non-reparse file")
    digest = hashlib.sha256()
    with executable.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    completed = subprocess.run(
        [str(executable), "--version"],
        cwd=root,
        env=_sanitized_git_environment(),
        check=False,
        capture_output=True,
    )
    version = completed.stdout.decode("utf-8", errors="strict").strip()
    if completed.returncode or not version.startswith("git version "):
        raise RuntimeError("resolved Git executable failed its version probe")
    return _GitRuntime(executable, version, digest.hexdigest())


@contextmanager
def _git_runtime_scope(root: Path):
    current = _ACTIVE_GIT_RUNTIME.get()
    if current is not None:
        yield current
        return
    runtime = _resolve_git_runtime(root)
    token = _ACTIVE_GIT_RUNTIME.set(runtime)
    try:
        yield runtime
    finally:
        _ACTIVE_GIT_RUNTIME.reset(token)


def _git_completed(
    root: Path,
    *args: str,
    input_bytes: bytes | None = None,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    runtime = _ACTIVE_GIT_RUNTIME.get() or _resolve_git_runtime(root)
    return subprocess.run(
        [
            str(runtime.executable),
            "-c",
            "core.quotepath=false",
            "-c",
            "core.fsmonitor=false",
            *args,
        ],
        cwd=root,
        input=input_bytes,
        env=_sanitized_git_environment(environment),
        check=False,
        capture_output=True,
    )


def _git(
    root: Path,
    *args: str,
    environment: Mapping[str, str] | None = None,
) -> bytes:
    completed = _git_completed(root, *args, environment=environment)
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"git {' '.join(args)} exited {completed.returncode}")
    return completed.stdout


def _git_with_input(
    root: Path,
    payload: bytes,
    *args: str,
    environment: Mapping[str, str] | None = None,
) -> bytes:
    completed = _git_completed(
        root,
        *args,
        input_bytes=payload,
        environment=environment,
    )
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"git {' '.join(args)} exited {completed.returncode}")
    return completed.stdout


def discover_repository_root(start: Path | None = None) -> Path | None:
    candidate = (start or Path.cwd()).resolve()
    try:
        raw = _git(candidate, "rev-parse", "--show-toplevel")
    except (OSError, RuntimeError):
        return None
    value = raw.decode("utf-8", errors="strict").strip()
    return Path(value).resolve() if value else None


def _is_excluded(relative: str, excluded: tuple[str, ...]) -> bool:
    clean = relative
    if os.name == "nt":
        clean = clean.replace("\\", "/")
    clean = clean.removeprefix("./").lstrip("/")
    return any(clean == item or clean.startswith(f"{item}/") for item in excluded)


def _normalized_exclusions(exclude_paths: Iterable[str]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for item in exclude_paths:
        raw = str(item)
        if os.name == "nt":
            raw = raw.replace("\\", "/")
        if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":"):
            raise ValueError(f"source exclusion is not repository-relative: {item}")
        clean = raw.removeprefix("./").rstrip("/")
        parts = clean.split("/")
        if ".." in parts:
            raise ValueError(f"source exclusion is not repository-relative: {item}")
        if not clean or any(part in {"", "."} for part in parts):
            raise ValueError(f"source exclusion is malformed: {item}")
        normalized.add(clean)
    return tuple(sorted(normalized))


_WINDOWS_RESERVED_BASENAMES = {
    "aux",
    "clock$",
    "con",
    "conin$",
    "conout$",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
    *(f"com{number}" for number in "\N{SUPERSCRIPT ONE}\N{SUPERSCRIPT TWO}\N{SUPERSCRIPT THREE}"),
    *(f"lpt{number}" for number in "\N{SUPERSCRIPT ONE}\N{SUPERSCRIPT TWO}\N{SUPERSCRIPT THREE}"),
}
_WINDOWS_RESERVED_CHARACTERS = frozenset('<>:"\\|?*')
_WINDOWS_SHORT_NAME_ALIAS = re.compile(
    r"^[^~.]{1,6}~[0-9]{1,4}(?:\.[^.]*)?$",
    flags=re.IGNORECASE,
)


def _validate_windows_git_path(relative: str) -> None:
    for component in relative.split("/"):
        basename = component.split(".", 1)[0].casefold()
        if (
            component.endswith((" ", "."))
            or basename in _WINDOWS_RESERVED_BASENAMES
            or _WINDOWS_SHORT_NAME_ALIAS.fullmatch(component) is not None
            or any(
                ord(character) < 32 or character in _WINDOWS_RESERVED_CHARACTERS
                for character in component
            )
        ):
            raise RuntimeError(
                f"Git path is not bijectively representable by Win32: {relative!r}"
            )


def _decode_git_path(encoded: bytes) -> str:
    try:
        relative = encoded.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise RuntimeError("non-UTF-8 Git paths cannot be source-bound") from exc
    if (
        not relative
        or relative.startswith("/")
        or "\0" in relative
        or "\r" in relative
        or "\n" in relative
        or any(part in {"", ".", ".."} for part in relative.rstrip("/").split("/"))
    ):
        raise RuntimeError(f"unsupported Git path name: {relative!r}")
    for component in relative.split("/"):
        if unicodedata.normalize("NFC", component) != component:
            raise RuntimeError(f"Git path is not canonically NFC-normalized: {relative!r}")
    _validate_windows_git_path(relative)
    return relative


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError):
        return False
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(marker and attributes & marker)


def _git_control_path(root: Path, name: str) -> Path:
    raw = _git(root, "rev-parse", "--git-path", name)
    path = Path(raw.decode("utf-8", errors="strict").strip())
    return path if path.is_absolute() else root / path


def _require_regular_git_control(root: Path, name: str) -> None:
    control = _git_control_path(root, name)
    if not control.exists() and not control.is_symlink():
        return
    metadata = control.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or _is_reparse_point(control)
    ):
        raise RuntimeError(f"Git {name} must be a regular non-reparse file")


def _config_name_inventory(root: Path, *, scope: str) -> list[str]:
    completed = _git_completed(
        root,
        "config",
        scope,
        "--includes",
        "--null",
        "--name-only",
        "--list",
    )
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "could not inspect effective Git configuration")
    return sorted(
        name.decode("utf-8", errors="strict")
        for name in completed.stdout.rstrip(b"\0").split(b"\0")
        if name
    )


def _forbidden_git_config_names(root: Path) -> list[str]:
    names = _config_name_inventory(root, scope="--local")
    if "extensions.worktreeconfig" in {name.casefold() for name in names}:
        names.extend(_config_name_inventory(root, scope="--worktree"))
        names = sorted(set(names))
    exact = {
        "core.attributesfile",
        "core.excludesfile",
        "core.fsmonitor",
        "core.hookspath",
        "core.worktree",
        "diff.external",
        "extensions.worktreeconfig",
        "init.templatedir",
    }

    def prohibited(name: str) -> bool:
        folded = name.casefold()
        return (
            folded.startswith(("filter.", "include.", "includeif."))
            or folded in exact
        )
    return [name for name in names if prohibited(name)]


def _active_git_info_lines(root: Path, name: str) -> list[bytes]:
    path = _git_control_path(root, name)
    if not path.exists() and not path.is_symlink():
        return []
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or _is_reparse_point(path)
    ):
        raise RuntimeError(f"Git {name} must be a regular non-reparse file")
    return [
        line
        for line in path.read_bytes().splitlines()
        if line.strip() and not line.lstrip().startswith(b"#")
    ]


def _hidden_effective_gitignores(
    root: Path,
    excluded: tuple[str, ...],
) -> list[str]:
    raw = _git(
        root,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "-z",
        "--",
        ".gitignore",
        ":(glob)**/.gitignore",
    )
    hidden: list[str] = []
    for encoded in raw.split(b"\0"):
        if not encoded:
            continue
        relative = _decode_git_path(encoded).rstrip("/")
        if relative.rsplit("/", 1)[-1] != ".gitignore":
            continue
        parent = relative.rpartition("/")[0]
        if _is_excluded(relative, excluded) or (
            parent and _is_excluded(parent, excluded)
        ):
            continue
        if parent:
            ignored_parent = _git_completed(root, "check-ignore", "-q", "--", parent)
            if ignored_parent.returncode == 0:
                continue
            if ignored_parent.returncode != 1:
                detail = ignored_parent.stderr.decode(
                    "utf-8", errors="replace"
                ).strip()
                raise RuntimeError(
                    detail or "could not classify hidden .gitignore parent"
                )
        hidden.append(relative)
    return sorted(hidden)


def _reject_external_git_influence(
    root: Path,
    excluded: tuple[str, ...] = (),
) -> None:
    for control_name in ("config", "config.worktree"):
        _require_regular_git_control(root, control_name)

    forbidden = _forbidden_git_config_names(root)
    if forbidden:
        raise RuntimeError(
            "local Git transform configuration is not allowed: "
            + ", ".join(forbidden)
        )
    if _active_git_info_lines(root, "info/attributes"):
        raise RuntimeError("Git info/attributes overrides are not allowed")
    if _active_git_info_lines(root, "info/exclude"):
        raise RuntimeError("Git info/exclude overrides are not allowed")
    hidden = _hidden_effective_gitignores(root, excluded)
    if hidden:
        raise RuntimeError(
            "ignored, unbound .gitignore controls are not allowed: " + ", ".join(hidden)
        )


def _reject_unsupported_index_state(root: Path) -> None:
    sparse = _git(
        root,
        "config",
        "--type=bool",
        "--default=false",
        "--get",
        "core.sparseCheckout",
    ).decode("ascii", errors="strict").strip()
    if sparse != "false":
        raise RuntimeError("sparse Git worktrees cannot be source-bound")
    raw = _git(root, "ls-files", "-v", "-z", "--cached")
    for encoded in raw.split(b"\0"):
        if not encoded:
            continue
        if len(encoded) < 3 or encoded[1:2] != b" ":
            raise RuntimeError("Git index flag inventory is malformed")
        tag = chr(encoded[0])
        relative = _decode_git_path(encoded[2:])
        if tag == "S" or tag.islower():
            raise RuntimeError(
                f"skip-worktree or assume-unchanged index state is not allowed: {relative}"
            )


def _drain_bounded_git_stream(
    name: str,
    pipe,
    buffer: bytearray,
    maximum: int,
    overflows: set[str],
    errors: list[Exception],
    lock: threading.Lock,
    state_changed: threading.Event,
) -> None:
    try:
        while True:
            remaining = maximum - len(buffer)
            chunk = pipe.read(min(64 * 1024, remaining + 1))
            if not chunk:
                return
            buffer.extend(chunk[:remaining])
            if len(chunk) > remaining:
                with lock:
                    overflows.add(name)
                state_changed.set()
                return
    except Exception as exc:  # pragma: no cover - platform pipe failure
        with lock:
            errors.append(exc)
        state_changed.set()
    finally:
        pipe.close()


def _bounded_git_capture(
    command: Sequence[str],
    *,
    cwd: Path,
    input_bytes: bytes | None,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[bytes]:
    stdin_handle = None
    if input_bytes is not None:
        stdin_handle = tempfile.TemporaryFile()
        stdin_handle.write(input_bytes)
        stdin_handle.seek(0)
    options: dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=_sanitized_git_environment(),
        stdin=stdin_handle if stdin_handle is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        **options,
    )
    if stdin_handle is not None:
        stdin_handle.close()
    if process.stdout is None or process.stderr is None:  # pragma: no cover - invariant
        process.kill()
        raise RuntimeError("trusted Git capture pipes were not created")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": max_stdout_bytes, "stderr": max_stderr_bytes}
    overflows: set[str] = set()
    errors: list[Exception] = []
    lock = threading.Lock()
    state_changed = threading.Event()

    readers = [
        threading.Thread(
            target=_drain_bounded_git_stream,
            args=(
                name,
                pipe,
                buffers[name],
                limits[name],
                overflows,
                errors,
                lock,
                state_changed,
            ),
            name=f"trusted-git-{name}",
            daemon=True,
        )
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr))
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout_seconds
    failure: str | None = None
    while process.poll() is None:
        with lock:
            if overflows:
                failure = "trusted Git output exceeded its hard byte limit"
            elif errors:
                failure = "trusted Git output capture failed"
        if failure is not None:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            failure = "trusted Git command timed out"
            break
        state_changed.wait(timeout=min(0.02, remaining))
    if failure is not None and process.poll() is None:
        process.kill()
    try:
        returncode = process.wait(timeout=10)
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - kill invariant
        process.kill()
        process.wait(timeout=10)
        raise RuntimeError("trusted Git process did not terminate") from exc
    for reader in readers:
        reader.join(timeout=5)
    if any(reader.is_alive() for reader in readers):
        raise RuntimeError("trusted Git output readers did not terminate")
    with lock:
        captured_errors = list(errors)
        captured_overflows = sorted(overflows)
    if failure is not None:
        raise RuntimeError(failure)
    if captured_errors:
        raise RuntimeError("trusted Git output capture failed") from captured_errors[0]
    if captured_overflows:
        raise RuntimeError("trusted Git output exceeded its hard byte limit")
    return subprocess.CompletedProcess(
        list(command),
        returncode,
        bytes(buffers["stdout"]),
        bytes(buffers["stderr"]),
    )


def trusted_git_completed(
    root: Path,
    args: Sequence[str],
    *,
    input_bytes: bytes | None = None,
    accepted_returncodes: Sequence[int] = (0,),
    max_stdout_bytes: int = 4 * 1024 * 1024,
    max_stderr_bytes: int = 1024 * 1024,
    timeout_seconds: float = 60.0,
) -> subprocess.CompletedProcess[bytes]:
    """Run one bounded local read-only Git query with the pinned trusted runtime."""
    normalized = [str(token) for token in args]
    command_index = 1 if normalized[:1] == ["--literal-pathspecs"] else 0
    if (
        command_index >= len(normalized)
        or normalized[command_index] not in _TRUSTED_GIT_READ_ONLY_COMMANDS
        or any(not token or "\0" in token for token in normalized)
        or any(
            token in {"-c", "--config-env", "--ext-diff", "--textconv", "--no-index"}
            for token in normalized
        )
        or (normalized[command_index] == "diff" and "--no-ext-diff" not in normalized)
    ):
        raise ValueError("trusted Git accepts only an approved local read-only query")
    accepted = tuple(int(code) for code in accepted_returncodes)
    if not accepted or len(accepted) != len(set(accepted)):
        raise ValueError("trusted Git accepted return codes are malformed")
    for name, value in (
        ("max_stdout_bytes", max_stdout_bytes),
        ("max_stderr_bytes", max_stderr_bytes),
    ):
        if not isinstance(value, int) or not 0 < value <= TRUSTED_GIT_MAX_CAPTURE_BYTES:
            raise ValueError(f"trusted Git {name} is outside the hard policy bound")
    if not 0 < float(timeout_seconds) <= TRUSTED_GIT_MAX_TIMEOUT_SECONDS:
        raise ValueError("trusted Git timeout is outside the hard policy bound")
    if input_bytes is not None and (
        not isinstance(input_bytes, bytes)
        or len(input_bytes) > TRUSTED_GIT_MAX_CAPTURE_BYTES
    ):
        raise ValueError("trusted Git input exceeds its hard policy bound")

    candidate = Path(os.path.abspath(root))
    with _git_runtime_scope(candidate) as runtime:
        repository = discover_repository_root(candidate)
        if repository != candidate.resolve(strict=True):
            raise RuntimeError("trusted Git root is not the exact repository root")
        control = repository / ".git"
        metadata = control.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or _is_reparse_point(control)
        ):
            raise RuntimeError("trusted Git requires a local regular .git directory")
        _reject_external_git_influence(repository)
        _reject_unsupported_index_state(repository)
        completed = _bounded_git_capture(
            [
                str(runtime.executable),
                "-c",
                "core.quotepath=false",
                "-c",
                "core.fsmonitor=false",
                *normalized,
            ],
            cwd=repository,
            input_bytes=input_bytes,
            max_stdout_bytes=max_stdout_bytes,
            max_stderr_bytes=max_stderr_bytes,
            timeout_seconds=float(timeout_seconds),
        )
    if completed.returncode not in accepted:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            detail or f"trusted Git query exited {completed.returncode}"
        )
    return completed


def _untracked_paths(root: Path, excluded: tuple[str, ...]) -> list[str]:
    raw = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    return sorted(
        relative.rstrip("/")
        for encoded in raw.split(b"\0")
        if encoded
        for relative in (_decode_git_path(encoded),)
        if not _is_excluded(relative, excluded)
    )


def _nested_repository_identity(path: Path) -> dict[str, str]:
    nested_root_raw = _git(path, "rev-parse", "--show-toplevel")
    nested_root = Path(
        nested_root_raw.decode("utf-8", errors="strict").strip()
    ).resolve()
    if nested_root != path.resolve():
        raise RuntimeError(f"source directory is not a nested Git root: {path}")
    status = _git(path, "status", "--porcelain=v1", "--untracked-files=all", "-z")
    if status:
        raise RuntimeError(f"nested Git repository is dirty: {path}")
    object_format = (
        _git(path, "rev-parse", "--show-object-format")
        .decode("ascii", errors="strict")
        .strip()
    )
    if object_format not in {"sha1", "sha256"}:
        raise RuntimeError(f"unsupported nested Git object format: {object_format}")
    revision = _validate_git_oid(
        _git(path, "rev-parse", "HEAD"),
        object_format=object_format,
    )
    return {"git_object_format": object_format, "revision": revision}


def _untracked_records(
    root: Path,
    excluded: tuple[str, ...],
    *,
    paths: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for relative in paths if paths is not None else _untracked_paths(root, excluded):
        path = root / relative
        if path.is_symlink():
            payload = os.readlink(path).encode("utf-8", errors="surrogateescape")
            kind = "symlink-target"
        elif path.is_file():
            payload = path.read_bytes()
            kind = "file-bytes"
        elif path.is_dir():
            payload = canonical_json_bytes(_nested_repository_identity(path))
            kind = "clean-nested-gitlink"
        else:
            raise RuntimeError(f"unsupported untracked filesystem entry: {relative}")
        records.append(
            {
                "path": relative,
                "kind": kind,
                "bytes": len(payload),
                "sha256": sha256_bytes(payload),
            }
        )
    return sorted(records, key=lambda item: item["path"])


def _tracked_entries(
    root: Path,
    excluded: tuple[str, ...],
    *,
    object_format: str,
) -> dict[str, tuple[str, str]]:
    raw = _git(root, "ls-files", "--stage", "-z")
    entries: dict[str, tuple[str, str]] = {}
    for encoded in raw.split(b"\0"):
        if not encoded:
            continue
        try:
            header, path_raw = encoded.split(b"\t", 1)
            mode_raw, oid_raw, stage_raw = header.split(b" ")
            relative = _decode_git_path(path_raw)
            mode = mode_raw.decode("ascii", errors="strict")
            stage = stage_raw.decode("ascii", errors="strict")
        except (UnicodeError, ValueError) as exc:
            raise RuntimeError("Git index entry is malformed") from exc
        if _is_excluded(relative, excluded):
            continue
        if stage != "0":
            raise RuntimeError(f"unmerged Git index entry cannot be bound: {relative}")
        if mode not in {"100644", "100755", "120000", "160000"}:
            raise RuntimeError(f"unsupported Git index mode {mode}: {relative}")
        if relative in entries:
            raise RuntimeError(f"duplicate Git index entry cannot be bound: {relative}")
        entries[relative] = (
            mode,
            _validate_git_oid(oid_raw, object_format=object_format),
        )
    return entries


def _git_config_bool(root: Path, name: str, *, default: bool) -> bool:
    raw = _git(
        root,
        "config",
        "--type=bool",
        f"--default={'true' if default else 'false'}",
        "--get",
        name,
    )
    value = raw.decode("ascii", errors="strict").strip()
    if value not in {"true", "false"}:
        raise RuntimeError(f"Git Boolean configuration is malformed: {name}")
    return value == "true"


def _blob_oid(root: Path, payload: bytes, *, object_format: str) -> str:
    raw = _git_with_input(root, payload, "hash-object", "--stdin")
    return _validate_git_oid(raw, object_format=object_format)


def _validate_git_oid(raw: bytes, *, object_format: str) -> str:
    oid = raw.decode("ascii", errors="strict").strip()
    length = 40 if object_format == "sha1" else 64
    if len(oid) != length or any(character not in "0123456789abcdef" for character in oid):
        raise RuntimeError(f"Git returned a malformed {object_format} object identity")
    return oid


def _filtered_blob_oids(
    root: Path,
    paths: Sequence[str],
    *,
    object_format: str,
) -> dict[str, str]:
    ordinary = [path for path in paths if "\n" not in path and "\r" not in path]
    identities: dict[str, str] = {}
    if ordinary:
        payload = b"".join(
            path.encode("utf-8", errors="surrogateescape") + b"\n"
            for path in ordinary
        )
        output = _git_with_input(root, payload, "hash-object", "--filters", "--stdin-paths")
        raw_oids = output.splitlines()
        if len(raw_oids) != len(ordinary):
            raise RuntimeError("Git returned an incomplete filtered blob inventory")
        identities.update(
            {
                path: _validate_git_oid(raw_oid, object_format=object_format)
                for path, raw_oid in zip(ordinary, raw_oids, strict=True)
            }
        )
    for path in paths:
        if path in identities:
            continue
        payload = (root / path).read_bytes()
        raw_oid = _git_with_input(
            root,
            payload,
            "hash-object",
            "--filters",
            f"--path={path}",
            "--stdin",
        )
        identities[path] = _validate_git_oid(raw_oid, object_format=object_format)
    return identities


@dataclass(frozen=True)
class _SourceEntry:
    path: str
    kind: str
    mode: str
    payload: bytes | None
    git_oid: str | None
    nested: _SourceSnapshot | None = None
    link_is_directory: bool = False


@dataclass(frozen=True)
class _SourceSnapshot:
    identity: dict[str, Any]
    entries: tuple[_SourceEntry, ...]
    git_object_format: str
    git_runtime: _GitRuntime


def _isolated_git_environment(control: Path, root: Path) -> dict[str, str]:
    return {
        "GIT_DIR": str((control / ".git").resolve()),
        "GIT_INDEX_FILE": str((control / ".git" / "proof-index").resolve()),
        "GIT_WORK_TREE": str(root.resolve()),
    }


def _validate_transform_attributes(
    root: Path,
    paths: Sequence[str],
    *,
    environment: Mapping[str, str],
) -> dict[str, tuple[str, ...]]:
    if not paths:
        return {}
    attributes = (
        "filter",
        "ident",
        "working-tree-encoding",
        "crlf",
        "text",
        "eol",
    )
    payload = b"".join(path.encode("utf-8") + b"\0" for path in paths)
    raw = _git_with_input(
        root,
        payload,
        "check-attr",
        "-z",
        "--stdin",
        *attributes,
        environment=environment,
    )
    fields = raw.rstrip(b"\0").split(b"\0") if raw else []
    expected_fields = len(paths) * len(attributes) * 3
    if len(fields) != expected_fields:
        raise RuntimeError("Git returned a malformed attribute inventory")
    cursor = 0
    allowed = {
        "filter": {"unspecified", "unset"},
        "ident": {"unspecified", "unset"},
        "working-tree-encoding": {"unspecified", "unset"},
        "crlf": {"unspecified", "unset"},
        "text": {"unspecified", "unset", "set", "auto"},
        "eol": {"unspecified", "unset", "lf", "crlf"},
    }
    effective: dict[str, list[str]] = {path: [] for path in paths}
    for path in paths:
        for attribute in attributes:
            observed_path = fields[cursor].decode("utf-8", errors="strict")
            observed_attribute = fields[cursor + 1].decode("ascii", errors="strict")
            value = fields[cursor + 2].decode("utf-8", errors="strict")
            cursor += 3
            if observed_path != path or observed_attribute != attribute:
                raise RuntimeError("Git attribute inventory order drifted")
            if value not in allowed[attribute]:
                raise RuntimeError(
                    f"unapproved Git content transform {attribute}={value}: {path}"
                )
            effective[path].append(f"{attribute}={value}")
    return {path: tuple(values) for path, values in effective.items()}


def _read_isolated_blob(
    object_directory: Path,
    oid: str,
    *,
    object_format: str,
) -> bytes:
    object_path = object_directory / oid[:2] / oid[2:]
    if not object_path.is_file() or object_path.is_symlink() or _is_reparse_point(object_path):
        raise RuntimeError("Git did not write an isolated loose blob object")
    decompressor = zlib.decompressobj()
    compressed = object_path.read_bytes()
    framed = decompressor.decompress(compressed) + decompressor.flush()
    if not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail:
        raise RuntimeError("isolated Git blob compression framing is malformed")
    try:
        header, payload = framed.split(b"\0", 1)
        kind, length_raw = header.split(b" ", 1)
        length = int(length_raw.decode("ascii", errors="strict"))
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError("isolated Git blob framing is malformed") from exc
    if kind != b"blob" or length < 0 or length != len(payload) or str(length).encode() != length_raw:
        raise RuntimeError("isolated Git object is not a canonically framed blob")
    if object_format == "sha1":
        try:
            digest = hashlib.sha1(framed, usedforsecurity=False).hexdigest()
        except TypeError:  # pragma: no cover - older compatible Python/OpenSSL
            digest = hashlib.sha1(framed).hexdigest()
    else:
        digest = hashlib.sha256(framed).hexdigest()
    if digest != oid:
        raise RuntimeError("isolated Git blob object identity does not match its framing")
    return payload


def _canonical_blob_payloads(
    root: Path,
    regular_paths: Sequence[str],
    raw_payloads: Mapping[str, bytes],
    *,
    object_format: str,
) -> dict[str, tuple[bytes, str]]:
    with tempfile.TemporaryDirectory(prefix="lightwork-proof-git-objects-") as temporary:
        control = Path(temporary) / "control"
        empty_template = Path(temporary) / "empty-template"
        empty_template.mkdir()
        _git(
            root,
            "init",
            "-q",
            f"--object-format={object_format}",
            f"--template={empty_template.resolve()}",
            str(control),
        )
        isolated_attributes = control / ".git" / "info" / "attributes"
        if isolated_attributes.exists() or isolated_attributes.is_symlink():
            metadata = isolated_attributes.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or _is_reparse_point(isolated_attributes)
                or any(
                    line.strip() and not line.lstrip().startswith(b"#")
                    for line in isolated_attributes.read_bytes().splitlines()
                )
            ):
                raise RuntimeError(
                    "isolated Git control repository inherited active attributes"
                )
        environment = _isolated_git_environment(control, root)
        for name, value in (
            # Force the same clean-side text heuristic on every host.  This makes
            # unspecified text checked out as CRLF on Windows canonicalize to the
            # same LF payload as a clean Unix checkout.  Explicit `-text` remains
            # byte-exact (including the proof-output rules in .gitattributes).
            ("core.autocrlf", "true"),
            ("core.eol", "lf"),
            ("core.safecrlf", "false"),
        ):
            _git(
                root,
                "config",
                "--local",
                name,
                value,
                environment=environment,
            )
        transform_attributes = _validate_transform_attributes(
            root,
            regular_paths,
            environment=environment,
        )

        work = [(path, None) for path in regular_paths] + list(raw_payloads.items())
        shared_objects = Path(temporary) / "shared-objects"
        shared_objects.mkdir()
        shared_environment = {
            **environment,
            "GIT_OBJECT_DIRECTORY": str(shared_objects.resolve()),
        }
        oids: dict[str, str] = {}
        if regular_paths:
            path_input = b"".join(path.encode("utf-8") + b"\n" for path in regular_paths)
            raw_oids = _git_with_input(
                root,
                path_input,
                "hash-object",
                "-w",
                "--filters",
                "--stdin-paths",
                environment=shared_environment,
            ).splitlines()
            if len(raw_oids) != len(regular_paths):
                raise RuntimeError("Git returned an incomplete canonical blob inventory")
            oids.update(
                {
                    path: _validate_git_oid(raw_oid, object_format=object_format)
                    for path, raw_oid in zip(regular_paths, raw_oids, strict=True)
                }
            )
        for path, raw_payload in raw_payloads.items():
            raw_oid = _git_with_input(
                root,
                raw_payload,
                "hash-object",
                "-w",
                "--stdin",
                environment=shared_environment,
            )
            oids[path] = _validate_git_oid(raw_oid, object_format=object_format)

        oid_counts: dict[str, int] = {}
        for oid in oids.values():
            oid_counts[oid] = oid_counts.get(oid, 0) + 1
        result = {
            path: (
                _read_isolated_blob(shared_objects, oid, object_format=object_format),
                oid,
            )
            for path, oid in oids.items()
            if oid_counts[oid] == 1
        }

        # Only duplicate OIDs can alias one path's payload to another in the
        # shared store.  Re-hash every member of those groups in its own empty
        # object directory, preserving collision safety without a Git process
        # per ordinary source path.
        duplicate_work = [item for item in work if oid_counts[oids[item[0]]] > 1]
        duplicate_classes: dict[
            tuple[str, bytes, tuple[str, ...]],
            list[tuple[str, bytes | None]],
        ] = {}
        for path, raw_payload in duplicate_work:
            if raw_payload is None:
                class_key = (
                    "file",
                    (root / path).read_bytes(),
                    transform_attributes[path],
                )
            else:
                class_key = ("raw", raw_payload, ())
            duplicate_classes.setdefault(class_key, []).append((path, raw_payload))
        object_root = Path(temporary) / "duplicate-path-objects"
        object_root.mkdir()

        def canonicalize(
            item: tuple[int, list[tuple[str, bytes | None]]],
        ) -> list[tuple[str, tuple[bytes, str]]]:
            index, equivalents = item
            path, raw_payload = equivalents[0]
            # A SHA-1 repository may contain distinct chosen-collision payloads
            # with one Git OID.  This empty per-path store ensures Git writes and
            # we frame-check this path's own clean-filter output.
            object_directory = object_root / f"{index:08d}"
            object_directory.mkdir()
            path_environment = {
                **environment,
                "GIT_OBJECT_DIRECTORY": str(object_directory.resolve()),
            }
            if raw_payload is None:
                raw_oid = _git_with_input(
                    root,
                    path.encode("utf-8") + b"\n",
                    "hash-object",
                    "-w",
                    "--filters",
                    "--stdin-paths",
                    environment=path_environment,
                )
            else:
                raw_oid = _git_with_input(
                    root,
                    raw_payload,
                    "hash-object",
                    "-w",
                    "--stdin",
                    environment=path_environment,
                )
            oid = _validate_git_oid(raw_oid, object_format=object_format)
            payload = _read_isolated_blob(
                object_directory,
                oid,
                object_format=object_format,
            )
            if oid != oids[path]:
                raise RuntimeError("isolated Git canonical object identity drifted")
            return [(equivalent[0], (payload, oid)) for equivalent in equivalents]

        classes = list(duplicate_classes.values())
        for item in enumerate(classes):
            result.update(canonicalize(item))
        return result


def _normalized_symlink_destination(relative: str, target: str) -> str:
    if "\\" in target:
        raise RuntimeError(
            f"source symlink target is not cross-platform repository-relative: {relative}"
        )
    normalized_target = target
    if (
        not normalized_target
        or normalized_target.startswith("/")
        or (len(normalized_target) >= 2 and normalized_target[1] == ":")
        or "\0" in normalized_target
        or "\r" in normalized_target
        or "\n" in normalized_target
    ):
        raise RuntimeError(f"source symlink target is not repository-relative: {relative}")
    parent_parts = relative.split("/")[:-1]
    resolved: list[str] = []
    for part in [*parent_parts, *normalized_target.split("/")]:
        if part in {"", "."}:
            continue
        if part == "..":
            if not resolved:
                raise RuntimeError(f"source symlink escapes repository: {relative}")
            resolved.pop()
        else:
            resolved.append(part)
    return "/".join(resolved)


def _validate_symlink_target(relative: str, target: str) -> None:
    _normalized_symlink_destination(relative, target)


def _symlink_target_kinds(
    raw_specs: Mapping[str, tuple[str, str, bytes, str]],
    regular_paths: Sequence[str],
    nested_paths: Sequence[str],
) -> dict[str, bool]:
    all_paths = {*regular_paths, *raw_specs, *nested_paths}
    directories = {"", *nested_paths}
    for path in all_paths:
        parts = path.split("/")
        directories.update("/".join(parts[:index]) for index in range(1, len(parts)))
    regular = set(regular_paths)
    resolving: set[str] = set()
    resolved: dict[str, bool] = {}

    def target_is_directory(path: str) -> bool:
        if path in resolved:
            return resolved[path]
        if path in resolving:
            raise RuntimeError(f"source symlink target cycle cannot be typed: {path}")
        resolving.add(path)
        destination = raw_specs[path][3]
        if destination in directories:
            value = True
        elif destination in regular:
            value = False
        elif destination in raw_specs:
            value = target_is_directory(destination)
        else:
            raise RuntimeError(
                f"source symlink target is dangling or excluded from the bound tree: {path}"
            )
        resolving.remove(path)
        resolved[path] = value
        return value

    return {path: target_is_directory(path) for path in raw_specs}


def _materialized_regular_file_record(path: Path, relative: str) -> dict[str, Any]:
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_nlink != 1
        or _is_reparse_point(path)
    ):
        raise RuntimeError(
            f"materialized source path is not a regular file: {relative}"
        )
    digest = hashlib.sha256()
    observed_bytes = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            observed_bytes += len(block)
            digest.update(block)
    after = path.lstat()
    identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_nlink")
    if any(getattr(before, name) != getattr(after, name) for name in identity_fields):
        raise RuntimeError(
            f"materialized source file changed while it was hashed: {relative}"
        )
    if observed_bytes != before.st_size:
        raise RuntimeError(
            f"materialized source file length changed while it was hashed: {relative}"
        )
    executable = bool(
        os.name != "nt"
        and before.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    )
    return {
        "kind": "file",
        "path": relative,
        "mode": "100755" if executable else "100644",
        "bytes": observed_bytes,
        "sha256": digest.hexdigest(),
    }


def materialized_tree_identity(root: Path) -> dict[str, Any]:
    """Bind one non-Git materialized tree without following links.

    The returned identity is stable across write-bit removal.  It rejects
    reparse points, dangling or escaping symlinks, non-portable paths, and
    unsupported filesystem entry types.
    """
    tree = Path(os.path.abspath(root))
    try:
        root_metadata = tree.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError("materialized source root is missing") from exc
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_ISLNK(root_metadata.st_mode)
        or _is_reparse_point(tree)
    ):
        raise RuntimeError("materialized source root is not a regular directory")

    rows: list[dict[str, Any]] = []
    directories = {""}
    regular_files: set[str] = set()
    symlinks: dict[str, dict[str, Any]] = {}

    def visit(directory: Path, prefix: str) -> None:
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name)
        for entry in ordered:
            path = directory / entry.name
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            normalized = _decode_git_path(relative.encode("utf-8", errors="strict"))
            if normalized != relative:
                raise RuntimeError(
                    f"materialized source path is not canonical: {relative!r}"
                )
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                target = os.readlink(path)
                if not isinstance(target, str):
                    target = os.fsdecode(target)
                destination = _normalized_symlink_destination(relative, target)
                row = {
                    "kind": "symlink",
                    "path": relative,
                    "target": target,
                    "destination": destination,
                }
                rows.append(row)
                symlinks[relative] = row
            elif _is_reparse_point(path):
                raise RuntimeError(
                    f"materialized source contains an unsupported reparse point: {relative}"
                )
            elif stat.S_ISDIR(metadata.st_mode):
                directories.add(relative)
                rows.append({"kind": "directory", "path": relative})
                visit(path, relative)
            elif stat.S_ISREG(metadata.st_mode):
                regular_files.add(relative)
                rows.append(_materialized_regular_file_record(path, relative))
            else:
                raise RuntimeError(
                    f"materialized source contains an unsupported entry: {relative}"
                )

    visit(tree, "")
    resolving: set[str] = set()
    resolved: dict[str, str] = {}

    def target_kind(relative: str) -> str:
        if relative in resolved:
            return resolved[relative]
        if relative in resolving:
            raise RuntimeError(
                f"materialized source symlink target cycle cannot be typed: {relative}"
            )
        resolving.add(relative)
        destination = str(symlinks[relative]["destination"])
        if destination in directories:
            kind = "directory"
        elif destination in regular_files:
            kind = "file"
        elif destination in symlinks:
            kind = target_kind(destination)
        else:
            raise RuntimeError(
                "materialized source symlink target is dangling or outside the bound tree: "
                f"{relative}"
            )
        resolving.remove(relative)
        resolved[relative] = kind
        return kind

    for relative, row in symlinks.items():
        row["target_kind"] = target_kind(relative)
    rows.sort(key=lambda row: str(row["path"]))
    byte_count = sum(int(row.get("bytes", 0)) for row in rows)
    return {
        "schema": MATERIALIZED_TREE_IDENTITY_SCHEMA,
        "path_count": len(rows),
        "byte_count": byte_count,
        "sha256": sha256_bytes(canonical_json_bytes(rows)),
        "digest_algorithm": "sha256",
        "digest_scope": MATERIALIZED_TREE_DIGEST_SCOPE,
    }


def make_materialized_tree_read_only(root: Path) -> None:
    """Remove write bits from a materialized tree without following links."""
    tree = Path(os.path.abspath(root))
    root_metadata = tree.lstat()
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_ISLNK(root_metadata.st_mode)
        or _is_reparse_point(tree)
    ):
        raise RuntimeError("materialized source root is not a regular directory")
    directories: list[Path] = [tree]

    def visit(directory: Path) -> None:
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name)
        for entry in ordered:
            path = directory / entry.name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                continue
            if _is_reparse_point(path):
                raise RuntimeError(
                    f"materialized source contains an unsupported reparse point: {path}"
                )
            if stat.S_ISDIR(metadata.st_mode):
                directories.append(path)
                visit(path)
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise RuntimeError(
                        f"materialized source file is not single-link: {path}"
                    )
                executable = bool(
                    os.name != "nt"
                    and metadata.st_mode
                    & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                )
                path.chmod(0o555 if executable else 0o444)
                if path.lstat().st_nlink != 1:
                    raise RuntimeError(
                        f"materialized source file link count changed: {path}"
                    )
            else:
                raise RuntimeError(
                    f"materialized source contains an unsupported entry: {path}"
                )

    visit(tree)
    for directory in reversed(directories):
        directory.chmod(0o555)


def _entry_identity_row(entry: _SourceEntry) -> dict[str, Any]:
    if entry.kind == "gitlink":
        assert entry.nested is not None
        return {
            "kind": entry.kind,
            "mode": entry.mode,
            "path": entry.path,
            "source_content": entry.nested.identity,
        }
    assert entry.payload is not None
    row = {
        "bytes": len(entry.payload),
        "kind": entry.kind,
        "mode": entry.mode,
        "path": entry.path,
        "sha256": sha256_bytes(entry.payload),
    }
    if entry.kind == "symlink":
        row["target_kind"] = "directory" if entry.link_is_directory else "file"
    return row


def _snapshot_identity(
    entries: Sequence[_SourceEntry],
    excluded: tuple[str, ...],
) -> dict[str, Any]:
    rows = [_entry_identity_row(entry) for entry in entries]
    path_count = sum(
        1 + (entry.nested.identity["path_count"] if entry.nested is not None else 0)
        for entry in entries
    )
    byte_count = sum(
        (
            entry.nested.identity["byte_count"]
            if entry.nested is not None
            else len(entry.payload or b"")
        )
        for entry in entries
    )
    digest_payload = {
        "schema": SOURCE_CONTENT_IDENTITY_SCHEMA,
        "excluded_paths": list(excluded),
        "paths": rows,
    }
    return {
        "schema": SOURCE_CONTENT_IDENTITY_SCHEMA,
        "path_count": path_count,
        "byte_count": byte_count,
        "sha256": sha256_bytes(canonical_json_bytes(digest_payload)),
        "digest_algorithm": "sha256",
        "digest_scope": SOURCE_CONTENT_DIGEST_SCOPE,
    }


def _runtime_file_mode(
    path: Path,
    tracked_mode: str | None,
    *,
    filemode_trusted: bool,
) -> str:
    if os.name == "nt":
        if tracked_mode in {"100644", "100755"}:
            return tracked_mode
        return "100644"
    executable = bool(path.lstat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    actual_mode = "100755" if executable else "100644"
    if tracked_mode in {"100644", "100755"}:
        if not filemode_trusted and tracked_mode != actual_mode:
            raise RuntimeError(
                f"tracked executable mode differs from the runtime filesystem: {path}"
            )
        return actual_mode
    if tracked_mode is None and actual_mode == "100755":
        raise RuntimeError(
            f"untracked executable must be staged before source binding: {path}"
        )
    return actual_mode


def _collect_source_snapshot(
    root: Path,
    excluded: tuple[str, ...],
) -> _SourceSnapshot:
    with _git_runtime_scope(root):
        return _collect_source_snapshot_bound(root, excluded)


def _collect_source_snapshot_bound(
    root: Path,
    excluded: tuple[str, ...],
) -> _SourceSnapshot:
    git_runtime = _ACTIVE_GIT_RUNTIME.get()
    if git_runtime is None:  # pragma: no cover - internal invariant
        raise RuntimeError("Git runtime is not bound")
    _reject_external_git_influence(root, excluded)
    _reject_unsupported_index_state(root)
    object_format = (
        _git(root, "rev-parse", "--show-object-format")
        .decode("ascii", errors="strict")
        .strip()
    )
    if object_format not in {"sha1", "sha256"}:
        raise RuntimeError(f"unsupported Git object format: {object_format}")
    tracked_entries = _tracked_entries(
        root,
        excluded,
        object_format=object_format,
    )
    untracked = _untracked_paths(root, excluded)
    if set(tracked_entries).intersection(untracked):
        raise RuntimeError("Git returned overlapping tracked and untracked paths")
    folded: dict[str, str] = {}
    for relative in [*tracked_entries, *untracked]:
        portable_key = "/".join(
            unicodedata.normalize("NFC", component).casefold()
            for component in relative.split("/")
        )
        prior = folded.setdefault(portable_key, relative)
        if prior != relative:
            raise RuntimeError(
                "portable-name-colliding Git paths cannot be materialized: "
                f"{prior}, {relative}"
            )

    materialized_symlinks = _git_config_bool(
        root,
        "core.symlinks",
        default=os.name != "nt",
    )
    filemode_trusted = _git_config_bool(
        root,
        "core.filemode",
        default=os.name != "nt",
    )
    regular_specs: list[tuple[str, str]] = []
    raw_specs: dict[str, tuple[str, str, bytes, str]] = {}
    nested_entries: list[_SourceEntry] = []
    for relative in sorted({*tracked_entries, *untracked}):
        path = root / relative
        tracked_entry = tracked_entries.get(relative)
        tracked_mode = tracked_entry[0] if tracked_entry is not None else None
        tracked_oid = tracked_entry[1] if tracked_entry is not None else None
        if path.is_symlink():
            target = os.readlink(path)
            destination = _normalized_symlink_destination(relative, target)
            raw_specs[relative] = (
                "symlink",
                "120000",
                target.encode("utf-8", errors="strict"),
                destination,
            )
            continue
        if not path.exists():
            if tracked_mode == "160000":
                raise RuntimeError(f"uninitialized Gitlink cannot be source-bound: {relative}")
            if tracked_mode is not None:
                continue
            raise RuntimeError(f"untracked source path disappeared during collection: {relative}")
        if _is_reparse_point(path):
            raise RuntimeError(f"unsupported source reparse point: {relative}")
        if path.is_dir():
            nested_root = discover_repository_root(path)
            if nested_root is None or nested_root != path.resolve():
                raise RuntimeError(f"source directory is not a nested Git root: {relative}")
            nested_snapshot = _collect_source_snapshot(nested_root, ())
            status = _git(
                nested_root,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "-z",
            )
            if status:
                raise RuntimeError(f"nested Git repository is dirty: {relative}")
            nested_revision = _validate_git_oid(
                _git(nested_root, "rev-parse", "HEAD"),
                object_format=nested_snapshot.git_object_format,
            )
            if tracked_mode == "160000" and nested_revision != tracked_oid:
                raise RuntimeError(
                    f"tracked Gitlink HEAD differs from its index identity: {relative}"
                )
            nested_entries.append(
                _SourceEntry(
                    path=relative,
                    kind="gitlink",
                    mode="160000",
                    payload=None,
                    git_oid=nested_revision,
                    nested=nested_snapshot,
                )
            )
            continue
        if tracked_mode == "160000":
            raise RuntimeError(f"Gitlink path is not an initialized repository: {relative}")
        if not path.is_file():
            raise RuntimeError(f"unsupported source filesystem entry: {relative}")
        if tracked_mode == "120000" and not materialized_symlinks:
            target = path.read_text(encoding="utf-8", errors="strict")
            destination = _normalized_symlink_destination(relative, target)
            raw_specs[relative] = (
                "symlink",
                "120000",
                target.encode("utf-8"),
                destination,
            )
            continue
        if tracked_mode == "120000":
            raise RuntimeError(f"tracked symlink path is not a symlink: {relative}")
        regular_specs.append(
            (
                relative,
                _runtime_file_mode(
                    path,
                    tracked_mode,
                    filemode_trusted=filemode_trusted,
                ),
            )
        )

    canonical = _canonical_blob_payloads(
        root,
        [relative for relative, _mode in regular_specs],
        {relative: spec[2] for relative, spec in raw_specs.items()},
        object_format=object_format,
    )
    entries = [
        _SourceEntry(
            path=relative,
            kind="file",
            mode=mode,
            payload=canonical[relative][0],
            git_oid=canonical[relative][1],
        )
        for relative, mode in regular_specs
    ]
    symlink_target_kinds = _symlink_target_kinds(
        raw_specs,
        [relative for relative, _mode in regular_specs],
        [entry.path for entry in nested_entries],
    )
    entries.extend(
        _SourceEntry(
            path=relative,
            kind=kind,
            mode=mode,
            payload=canonical[relative][0],
            git_oid=canonical[relative][1],
            link_is_directory=symlink_target_kinds[relative],
        )
        for relative, (kind, mode, _payload, _destination) in raw_specs.items()
    )
    entries.extend(nested_entries)
    entries.sort(key=lambda item: item.path)
    identity = _snapshot_identity(entries, excluded)
    return _SourceSnapshot(identity, tuple(entries), object_format, git_runtime)


def _source_git_diagnostics(snapshot: _SourceSnapshot) -> dict[str, Any]:
    rows = [
        {"kind": entry.kind, "oid": entry.git_oid, "path": entry.path}
        for entry in snapshot.entries
    ]
    return {
        "git_object_format": snapshot.git_object_format,
        "entry_count": len(rows),
        "oids_sha256": sha256_bytes(canonical_json_bytes(rows)),
        "runtime": snapshot.git_runtime.record(),
        "digest_scope": "diagnostic Git object identities; excluded from v2 source identity",
    }


def _write_materialized_file(path: Path, payload: bytes, mode: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o755 if mode == "100755" else 0o644)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    if os.name != "nt":
        path.chmod(0o755 if mode == "100755" else 0o644)


def _copy_bound_file_symlinks_for_platform() -> bool:
    return os.name == "nt"


def _materialize_snapshot(
    snapshot: _SourceSnapshot,
    destination: Path,
    *,
    symlink_materialization_policy: str,
) -> None:
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    entries = {entry.path: entry for entry in snapshot.entries}
    for entry in snapshot.entries:
        path = destination.joinpath(*entry.path.split("/"))
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        if entry.kind == "file":
            assert entry.payload is not None
            _write_materialized_file(path, entry.payload, entry.mode)
        elif entry.kind == "symlink":
            assert entry.payload is not None
            target = entry.payload.decode("utf-8", errors="strict")
            if _copy_bound_file_symlinks_for_platform():
                destination_relative = _normalized_symlink_destination(
                    entry.path,
                    target,
                )
                target_entry = entries.get(destination_relative)
                if (
                    target_entry is None
                    or target_entry.kind != "file"
                    or target_entry.mode != "100644"
                    or target_entry.payload is None
                    or target_entry.nested is not None
                ):
                    raise RuntimeError(
                        "Windows source materialization can copy only a direct bound "
                        f"non-executable regular-file symlink target: {entry.path}"
                    )
                _write_materialized_file(
                    path,
                    target_entry.payload,
                    target_entry.mode,
                )
            else:
                os.symlink(target, path, target_is_directory=entry.link_is_directory)
        else:
            assert entry.nested is not None
            _materialize_snapshot(
                entry.nested,
                path,
                symlink_materialization_policy=symlink_materialization_policy,
            )


def _validate_snapshot_topology(snapshot: _SourceSnapshot) -> None:
    paths = {entry.path for entry in snapshot.entries}
    for entry in snapshot.entries:
        parts = entry.path.split("/")
        for index in range(1, len(parts)):
            ancestor = "/".join(parts[:index])
            if ancestor in paths:
                raise RuntimeError(
                    f"source entry would materialize through another entry: {ancestor}, {entry.path}"
                )
        if entry.nested is not None:
            _validate_snapshot_topology(entry.nested)


def _required_regular_entries(
    snapshot: _SourceSnapshot,
    required_paths: Iterable[str],
) -> None:
    normalized: list[str] = []
    for raw in required_paths:
        encoded = str(raw).encode("utf-8", errors="strict")
        relative = _decode_git_path(encoded)
        if relative in normalized:
            raise ValueError(f"required bound source path is duplicated: {relative}")
        normalized.append(relative)
    entries = {entry.path: entry for entry in snapshot.entries}
    for relative in normalized:
        entry = entries.get(relative)
        if (
            entry is None
            or entry.kind != "file"
            or entry.mode not in {"100644", "100755"}
            or entry.payload is None
            or entry.nested is not None
        ):
            raise RuntimeError(
                f"required executed source is not a bound regular file: {relative}"
            )


def _reject_linked_destination_parent(path: Path) -> None:
    parent = path.parent
    cursor = Path(parent.anchor)
    for part in parent.parts[1:]:
        cursor /= part
        try:
            metadata = cursor.lstat()
        except FileNotFoundError as exc:
            raise RuntimeError("source materialization parent does not exist") from exc
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError("source materialization parent is not a directory")
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(cursor):
            raise RuntimeError("source materialization parent contains a link or reparse point")


def materialize_repository_source(
    root: Path,
    destination: Path,
    *,
    exclude_paths: Iterable[str] = (),
    required_regular_paths: Iterable[str] = (),
    symlink_materialization_policy: str = SYMLINK_MATERIALIZATION_POLICY,
) -> dict[str, Any]:
    """Materialize only content covered by the v2 source identity.

    Under the v1 symlink policy, POSIX retains native safe links.  Windows
    copies only direct bound non-executable regular-file targets because its
    ordinary filesystem API cannot reliably recreate either symlink privilege
    or POSIX executable semantics.  Every ambiguous target fails closed.
    """
    if symlink_materialization_policy != SYMLINK_MATERIALIZATION_POLICY:
        raise ValueError("unsupported source symlink materialization policy")
    candidate = root.resolve()
    with _git_runtime_scope(candidate):
        repo = discover_repository_root(candidate)
        if repo is None:
            raise RuntimeError("not executed from a Git worktree")
        if repo != candidate:
            raise RuntimeError("source materialization root is not the Git worktree root")
        target = Path(os.path.abspath(destination))
        git_dir_raw = _git(repo, "rev-parse", "--absolute-git-dir")
        git_dir = Path(
            git_dir_raw.decode("utf-8", errors="strict").strip()
        ).resolve(strict=True)
        for protected in (repo, git_dir):
            try:
                target.relative_to(protected)
            except ValueError:
                continue
            raise RuntimeError(
                "source materialization destination is inside source or Git metadata"
            )
        _reject_linked_destination_parent(target)
        if target.exists() or target.is_symlink():
            raise RuntimeError("source materialization destination already exists")
        snapshot = _collect_source_snapshot(repo, _normalized_exclusions(exclude_paths))
        _validate_snapshot_topology(snapshot)
        _required_regular_entries(snapshot, required_regular_paths)
        _materialize_snapshot(
            snapshot,
            target,
            symlink_materialization_policy=symlink_materialization_policy,
        )
        return dict(snapshot.identity)


def repository_source_content_identity(
    root: Path | None = None,
    *,
    exclude_paths: Iterable[str] = (),
) -> dict[str, Any]:
    """Return a commit- and checkout-independent identity for current source content.

    Tracked-present and non-ignored untracked files share one canonical path/mode/blob
    inventory, so staging and committing an unchanged worktree cannot alter this
    identity.  Regular file content passes through Git clean filters, making the
    identity stable across supported checkout line-ending policies.  Exact exclusions
    are included in the aggregate digest and therefore cannot be widened silently.
    """
    candidate = (root or Path.cwd()).resolve()
    with _git_runtime_scope(candidate):
        repo = discover_repository_root(candidate)
        if repo is None:
            raise RuntimeError("not executed from a Git worktree")
        return dict(
            _collect_source_snapshot(repo, _normalized_exclusions(exclude_paths)).identity
        )


def source_content_identity(provenance: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly extract the stable source-content identity from provenance."""
    identity = provenance.get("source_content")
    if (
        not isinstance(identity, dict)
        or set(identity) != _SOURCE_CONTENT_IDENTITY_KEYS
        or identity.get("schema") != SOURCE_CONTENT_IDENTITY_SCHEMA
        or not isinstance(identity.get("path_count"), int)
        or isinstance(identity.get("path_count"), bool)
        or identity["path_count"] < 0
        or not isinstance(identity.get("byte_count"), int)
        or isinstance(identity.get("byte_count"), bool)
        or identity["byte_count"] < 0
        or not isinstance(identity.get("sha256"), str)
        or len(identity["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in identity["sha256"])
        or identity.get("digest_algorithm") != "sha256"
        or identity.get("digest_scope") != SOURCE_CONTENT_DIGEST_SCOPE
    ):
        raise ValueError("source-content identity is malformed")
    return dict(identity)


def semantic_execution_tree_identity(
    source_content: Mapping[str, Any],
    *,
    symlink_materialization_policy: str = SYMLINK_MATERIALIZATION_POLICY,
) -> dict[str, Any]:
    """Bind portable source identity to deterministic execution-tree semantics."""
    if not isinstance(source_content, Mapping):
        raise ValueError("semantic execution-tree source identity is malformed")
    identity = source_content_identity({"source_content": dict(source_content)})
    if symlink_materialization_policy != SYMLINK_MATERIALIZATION_POLICY:
        raise ValueError("unsupported source symlink materialization policy")
    basis = {
        "schema": SEMANTIC_EXECUTION_TREE_IDENTITY_SCHEMA,
        "source_content": identity,
        "symlink_materialization_policy": symlink_materialization_policy,
        "regular_file_semantics": _SEMANTIC_REGULAR_FILE_SEMANTICS,
        "symlink_semantics": _SEMANTIC_SYMLINK_SEMANTICS,
    }
    return {
        **basis,
        "sha256": sha256_bytes(canonical_json_bytes(basis)),
        "digest_algorithm": "sha256",
        "digest_scope": SEMANTIC_EXECUTION_TREE_DIGEST_SCOPE,
    }


def repository_provenance(
    root: Path | None = None,
    *,
    exclude_paths: Iterable[str] = (),
) -> dict[str, Any]:
    excluded = _normalized_exclusions(exclude_paths)
    candidate = (root or Path.cwd()).resolve()
    try:
        with _git_runtime_scope(candidate):
            return _repository_provenance_bound(
                candidate,
                exclude_paths=excluded,
            )
    except (OSError, RuntimeError, UnicodeError) as exc:
        return {
            "available": False,
            "reason": f"Git provenance collection failed: {type(exc).__name__}: {exc}",
            "revision": None,
            "dirty": None,
            "worktree_state_sha256": None,
            "source_content": None,
            "source_content_git_diagnostics": None,
            "excluded_paths": list(excluded),
        }


def _repository_provenance_bound(
    root: Path | None = None,
    *,
    exclude_paths: Iterable[str] = (),
) -> dict[str, Any]:
    """Describe HEAD plus all tracked and untracked source changes.

    ``worktree_state_sha256`` diagnostically covers the base revision, the exact binary patch
    from HEAD, and the path/content/size records for every non-ignored untracked
    file.  Generated evidence outputs may be excluded to avoid a self-referential
    digest; exclusions are explicit in the returned record.  The record is
    deliberately checkout-location independent: an absolute repository path is
    execution metadata, not source identity, and would make a committed proof
    unverifiable on another runner.  ``source_content`` is the stable binding
    used to verify unchanged content after staging, committing, or relocation.
    """
    repo = discover_repository_root(root)
    if repo is None:
        return {
            "available": False,
            "reason": "not executed from a Git worktree",
            "revision": None,
            "dirty": None,
            "worktree_state_sha256": None,
            "source_content": None,
            "source_content_git_diagnostics": None,
            "excluded_paths": list(_normalized_exclusions(exclude_paths)),
        }

    excluded = _normalized_exclusions(exclude_paths)
    try:
        source_snapshot = _collect_source_snapshot(repo, excluded)
        revision = _git(repo, "rev-parse", "HEAD").decode("ascii", errors="strict").strip()
        pathspec = ["--", ".", *(f":(exclude){item}/**" for item in excluded)]
        pathspec.extend(f":(exclude){item}" for item in excluded)
        tracked_diff = _git(
            repo,
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            *pathspec,
        )
        untracked_paths = _untracked_paths(repo, excluded)
        untracked = _untracked_records(repo, excluded, paths=untracked_paths)
    except (OSError, RuntimeError, UnicodeError) as exc:
        return {
            "available": False,
            "reason": f"Git provenance collection failed: {type(exc).__name__}: {exc}",
            "revision": None,
            "dirty": None,
            "worktree_state_sha256": None,
            "source_content": None,
            "source_content_git_diagnostics": None,
            "excluded_paths": list(excluded),
        }

    diff_digest = sha256_bytes(tracked_diff)
    worktree_snapshot = {
        "revision": revision,
        "tracked_diff_sha256": diff_digest,
        "tracked_diff_bytes": len(tracked_diff),
        "untracked": untracked,
    }
    return {
        "available": True,
        "revision": revision,
        "dirty": bool(tracked_diff or untracked),
        "tracked_diff_sha256": diff_digest,
        "tracked_diff_bytes": len(tracked_diff),
        "untracked_file_count": len(untracked),
        "untracked_files_sha256": sha256_bytes(canonical_json_bytes(untracked)),
        "worktree_state_sha256": sha256_bytes(canonical_json_bytes(worktree_snapshot)),
        "source_content": dict(source_snapshot.identity),
        "source_content_git_diagnostics": _source_git_diagnostics(source_snapshot),
        "digest_algorithm": "sha256",
        "digest_scope": "HEAD binary diff plus non-ignored untracked path/content records",
        "excluded_paths": list(excluded),
    }


def runtime_import_path_records() -> list[dict[str, str]]:
    """Return explicit site-package admissions for isolated ``-S`` children.

    A virtual environment admits its own site directories and admits the base
    interpreter only when its regular ``pyvenv.cfg`` explicitly opts in.
    A non-venv interpreter admits its user and global site directories.
    ``.pth`` processing is intentionally left to the isolated bootstrap, which
    inserts these directories directly into ``sys.path``.
    """
    executable_root = Path(os.path.abspath(sys.executable)).parent.parent
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    venv_config = executable_root / "pyvenv.cfg"
    include_system = False
    config_present = venv_config.exists() or venv_config.is_symlink()
    if config_present:
        metadata = venv_config.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or venv_config.is_symlink()
            or _is_reparse_point(venv_config)
        ):
            raise RuntimeError("pyvenv.cfg is not a regular non-reparse file")
        observed_include: bool | None = None
        for line in venv_config.read_text(encoding="utf-8", errors="strict").splitlines():
            name, separator, value = line.partition("=")
            if name.strip().casefold() != "include-system-site-packages":
                continue
            normalized = value.strip().casefold()
            if not separator or normalized not in {"true", "false"}:
                raise RuntimeError("pyvenv.cfg include-system-site-packages is malformed")
            if observed_include is not None:
                raise RuntimeError("pyvenv.cfg repeats include-system-site-packages")
            observed_include = normalized == "true"
        include_system = bool(observed_include)

    candidates: list[tuple[Path, str]] = []
    prefixes: list[tuple[Path, str]] = []
    if config_present:
        prefixes.append((executable_root, "virtual-environment site packages"))
        if include_system:
            prefixes.append(
                (Path(sys.base_prefix), "pyvenv.cfg include-system-site-packages=true")
            )
    else:
        user_scheme = "nt_user" if os.name == "nt" else "posix_user"
        user_site = sysconfig.get_path("purelib", scheme=user_scheme)
        if user_site:
            candidates.append((Path(user_site), "explicit user site packages"))
        prefixes.append((Path(sys.base_prefix), "global interpreter site packages"))
    for prefix, reason in prefixes:
        if os.name == "nt":
            candidates.append((prefix / "Lib" / "site-packages", reason))
        else:
            candidates.extend(
                (
                    (prefix / "lib" / version / "site-packages", reason),
                    (prefix / "lib" / version / "dist-packages", reason),
                    (prefix / "lib64" / version / "site-packages", reason),
                )
            )
    records: dict[str, dict[str, str]] = {}
    for candidate, reason in candidates:
        if not candidate.is_dir():
            continue
        path = str(candidate.resolve())
        records.setdefault(path, {"path": path, "reason": reason})
    return list(records.values())


def runtime_provenance() -> dict[str, Any]:
    """Record the interpreter and every installed distribution/version."""
    dependencies = sorted(
        (
            {
                "name": str(dist.metadata.get("Name") or "<unnamed>"),
                "version": str(dist.version),
            }
            for dist in importlib.metadata.distributions()
        ),
        key=lambda item: (item["name"].casefold(), item["version"]),
    )
    return {
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_build": list(platform.python_build()),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "dependencies": dependencies,
        "dependency_count": len(dependencies),
        "dependencies_sha256": sha256_bytes(canonical_json_bytes(dependencies)),
        "dependency_digest_scope": "all distributions visible to importlib.metadata",
    }


def output_digest(payload: Any) -> dict[str, Any]:
    """Digest one inline collector payload using an explicitly named encoding."""
    raw = canonical_json_bytes(payload)
    return {
        "sha256": sha256_bytes(raw),
        "bytes": len(raw),
        "encoding": "utf-8",
        "serialization": "canonical-json-sort-keys-v1",
    }


__all__ = [
    "DOWNSTREAM_GOVERNANCE_ARTIFACTS",
    "FIXTURE_DISCARDED_ENV_PREFIXES",
    "FIXTURE_KNOWN_DISCARDED_ENV_NAMES",
    "FIXTURE_SYSTEM_ENV_ALLOWLIST",
    "MATERIALIZED_TREE_DIGEST_SCOPE",
    "MATERIALIZED_TREE_IDENTITY_SCHEMA",
    "ProcessTreeController",
    "ProcessTreeSeal",
    "SEMANTIC_EXECUTION_TREE_DIGEST_SCOPE",
    "SEMANTIC_EXECUTION_TREE_IDENTITY_SCHEMA",
    "SOURCE_CONTENT_DIGEST_SCOPE",
    "SOURCE_CONTENT_IDENTITY_SCHEMA",
    "SYMLINK_MATERIALIZATION_POLICY",
    "TRUSTED_GIT_MAX_CAPTURE_BYTES",
    "TRUSTED_GIT_MAX_TIMEOUT_SECONDS",
    "canonical_json_bytes",
    "command_record",
    "discover_repository_root",
    "fixture_environment_policy",
    "fixture_execution_environment",
    "make_materialized_tree_read_only",
    "materialized_tree_identity",
    "materialize_repository_source",
    "output_digest",
    "proof_source_exclusions",
    "process_tree_controller",
    "repository_provenance",
    "repository_source_content_identity",
    "runtime_import_path_records",
    "runtime_provenance",
    "sha256_bytes",
    "source_content_identity",
    "semantic_execution_tree_identity",
    "trusted_git_completed",
    "utc_timestamp",
    "validate_fixture_environment_provenance",
]
