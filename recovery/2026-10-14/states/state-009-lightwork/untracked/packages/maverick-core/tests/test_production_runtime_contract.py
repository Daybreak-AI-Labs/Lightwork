"""Exact first-profile filesystem, process, and goal resource boundaries."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest
from maverick import production_runtime_contract as contract
from maverick.budget import Budget


def _valid_mountinfo() -> str:
    return "\n".join(
        (
            "1 0 0:1 / / ro,relatime - overlay overlay ro",
            "2 1 0:2 / /tmp rw,nosuid,nodev - ext4 /dev/tmp rw",
            (
                "3 1 0:3 / /home/maverick/.maverick rw,nosuid,nodev "
                "- ext4 /dev/state rw"
            ),
            (
                "4 1 0:4 /custody /run/secrets/lightwork "
                "ro,nosuid,nodev - tmpfs tmpfs ro"
            ),
        )
    )


def _valid_environment() -> dict[str, str]:
    return {
        "HOME": "/home/maverick",
        "MAVERICK_DEFAULT_MAX_DEPTH": "3",
        "MAVERICK_DEFAULT_MAX_WALL_SECONDS": "1800",
        "MAVERICK_HOME": "/home/maverick/.maverick",
        "MAVERICK_MAX_CONCURRENT_GOALS": "8",
        "MAVERICK_MAX_CONCURRENT_GOALS_PER_PRINCIPAL": "2",
        "MAVERICK_MAX_TOOL_RESULT_BYTES": "32000",
        "MAVERICK_MAX_TOTAL_SPAWNS": "32",
        "MAVERICK_PRODUCTION_MAX_TOOL_CALLS": "500",
        "MAVERICK_RUNTIME_FILE_SIZE_BYTES": "8589934592",
        "MAVERICK_RUNTIME_NOFILE": "4096",
        "MAVERICK_RUNTIME_PIDS": "256",
        "MAVERICK_SANDBOX_BACKEND": "disabled",
        "TMPDIR": "/tmp",
        "MAVERICK_OIDC_ENABLED": "1",
    }


def test_committed_runtime_manifest_is_exactly_code_derived() -> None:
    loaded = contract.load_contract()

    assert loaded == contract.contract_document()
    assert contract.contract_path().read_bytes() == contract.canonical_contract_bytes()
    assert loaded["execution"]["arbitrary_command_execution"] is False
    assert loaded["execution"]["operating_system"] == "Linux"
    assert loaded["execution"]["run_as_uid"] == 1000
    assert loaded["execution"]["run_as_gid"] == 1000
    assert loaded["execution"]["installed_package_inputs_read_only"] is True
    assert loaded["network"]["live_cluster_proof_required"] is True


def test_runtime_manifest_rejects_a_hand_edited_ceiling(tmp_path: Path) -> None:
    document = contract.contract_document()
    document["resources"]["pids"] = 257
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")

    with pytest.raises(
        contract.ProductionRuntimeContractError,
        match="code-derived manifest",
    ):
        contract.load_contract(path)


def test_mount_contract_admits_only_root_tmp_state_and_secret_custody() -> None:
    assert contract.mount_contract_violations(_valid_mountinfo()) == ()


def test_mount_contract_allows_only_the_named_platform_mounts() -> None:
    platform = "\n".join(
        (
            "5 1 0:5 / /proc rw,nosuid,nodev,noexec - proc proc rw",
            "6 1 0:6 / /dev rw,nosuid - tmpfs tmpfs rw",
            "7 1 0:7 / /sys ro,nosuid,nodev,noexec - sysfs sysfs ro",
            "8 1 0:8 / /etc/hosts rw - tmpfs tmpfs rw",
        )
    )

    assert contract.mount_contract_violations(
        _valid_mountinfo() + "\n" + platform
    ) == ()


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ("5 1 0:5 / /workspace/repo ro - ext4 /host/repo ro", "unexpected"),
        ("5 1 0:5 / /mnt/host ro - ext4 /host ro", "unexpected"),
        ("5 1 0:5 / /var/run/docker.sock rw - bind /run/docker.sock rw", "forbidden"),
    ],
)
def test_mount_contract_rejects_injected_application_or_control_plane_mounts(
    extra: str,
    expected: str,
) -> None:
    violations = contract.mount_contract_violations(_valid_mountinfo() + "\n" + extra)

    assert any(expected in violation for violation in violations)


def test_mount_contract_rejects_writable_root_or_secret_custody() -> None:
    mountinfo = _valid_mountinfo().replace(
        "/ / ro,relatime", "/ / rw,relatime"
    ).replace(
        "/run/secrets/lightwork ro,nosuid,nodev",
        "/run/secrets/lightwork rw,nosuid,nodev",
    )

    violations = contract.mount_contract_violations(mountinfo)

    assert "container root filesystem is not mounted read-only" in violations
    assert "staged secret custody is not an exact read-only mount" in violations


def test_mount_contract_rejects_a_stacked_approved_mount() -> None:
    duplicate = "5 1 0:5 / /tmp rw - tmpfs tmpfs rw"

    violations = contract.mount_contract_violations(
        _valid_mountinfo() + "\n" + duplicate
    )

    assert "production mount point is not unique: /tmp" in violations


def test_secret_custody_is_an_exact_oidc_allowlist() -> None:
    env = _valid_environment()
    existing = frozenset(
        {
            *contract.WRITABLE_MOUNTS,
            *contract.READ_ONLY_INPUTS,
        }
    )

    violations = contract.filesystem_path_violations(
        exists=lambda path: path in existing,
        isdir=lambda _path: True,
        islink=lambda _path: False,
        listdir=lambda _path: sorted(contract.expected_secret_files(env)),
        realpath=lambda path: path,
        env=env,
    )

    assert violations == ()
    assert "MAVERICK_AUDIT_SIGNING_KEY" not in contract.expected_secret_files(env)
    assert "MAVERICK_AUDIT_SIGNING_KEY_WRAPPED" in contract.expected_secret_files(env)
    assert (
        "MAVERICK_PRODUCTION_ONBOARDING_OBSERVATIONS"
        in contract.expected_secret_files(env)
    )
    assert not contract.EVIDENCE_FILES.intersection(contract.FILE_ONLY_SECRET_ENV)


def test_secret_custody_rejects_unlisted_or_raw_private_keys() -> None:
    env = _valid_environment()
    existing = frozenset(
        {
            *contract.WRITABLE_MOUNTS,
            *contract.READ_ONLY_INPUTS,
        }
    )
    secret_files = set(contract.expected_secret_files(env))
    secret_files.update({"AWS_ACCESS_KEY_ID", "MAVERICK_AUDIT_SIGNING_KEY"})

    violations = contract.filesystem_path_violations(
        exists=lambda path: path in existing,
        isdir=lambda _path: True,
        islink=lambda _path: False,
        listdir=lambda _path: sorted(secret_files),
        realpath=lambda path: path,
        env=env,
    )

    assert "staged secret custody does not match the exact allowlist" in violations
    assert "staged secret custody contains a forbidden credential" in violations


def test_environment_contract_rejects_ambient_host_credentials() -> None:
    env = _valid_environment()
    env["KUBECONFIG"] = "/host/.kube/config"

    violations = contract.environment_contract_violations(env)

    assert "forbidden ambient credential is present: KUBECONFIG" in violations


def test_environment_contract_rejects_a_file_custody_secret() -> None:
    env = _valid_environment()
    env["VLLM_API_KEY"] = "must-not-be-ambient"

    violations = contract.environment_contract_violations(env)

    assert (
        "file-custody secret is present in the environment: VLLM_API_KEY"
        in violations
    )


def _patch_live_runtime_dependencies(monkeypatch, *, package_read_only: bool) -> None:
    monkeypatch.setattr(contract, "environment_contract_violations", lambda: ())
    monkeypatch.setattr(contract, "rlimit_contract_violations", lambda: ())
    monkeypatch.setattr(contract, "mount_contract_violations", lambda _text: ())
    monkeypatch.setattr(contract, "filesystem_path_violations", lambda: ())
    monkeypatch.setattr(contract, "Path", lambda _path: types.SimpleNamespace(
        read_text=lambda **_kwargs: "mountinfo"
    ))
    monkeypatch.setattr(contract, "parse_mountinfo", lambda _text: ())
    options = frozenset({"ro"} if package_read_only else {"rw"})
    monkeypatch.setattr(
        contract,
        "_mount_for",
        lambda _path, _mounts: contract.Mount(
            mount_point="/app",
            options=options,
            fs_type="overlay",
            source="overlay",
        ),
    )
    monkeypatch.setattr(contract.sysconfig, "get_paths", lambda: {"purelib": "/app/site"})
    monkeypatch.setattr(contract.platform, "system", lambda: contract.OPERATING_SYSTEM)
    monkeypatch.setattr(
        contract.os,
        "geteuid",
        lambda: contract.RUN_AS_UID,
        raising=False,
    )
    monkeypatch.setattr(
        contract.os,
        "getegid",
        lambda: contract.RUN_AS_GID,
        raising=False,
    )
    monkeypatch.setattr(contract.os, "getpid", lambda: 1)


def test_live_runtime_rejects_platform_identity_and_pid_drift(monkeypatch) -> None:
    _patch_live_runtime_dependencies(monkeypatch, package_read_only=True)
    monkeypatch.setattr(contract.platform, "system", lambda: "Windows")
    monkeypatch.setattr(contract.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(contract.os, "getegid", lambda: 0, raising=False)
    monkeypatch.setattr(contract.os, "getpid", lambda: 2)

    violations = contract.live_runtime_violations()

    assert "production runtime contract requires Linux" in violations
    assert "production runtime must run as uid 1000" in violations
    assert "production runtime must run as gid 1000" in violations
    assert "production launcher is not container PID 1" in violations


def test_live_runtime_rejects_a_writable_installed_package_mount(monkeypatch) -> None:
    _patch_live_runtime_dependencies(monkeypatch, package_read_only=False)

    assert (
        "installed package inputs are not read-only"
        in contract.live_runtime_violations()
    )


def test_process_limits_are_installed_exactly(monkeypatch) -> None:
    limits: dict[int, tuple[int, int]] = {}
    fake = types.SimpleNamespace(
        RLIMIT_CORE=1,
        RLIMIT_FSIZE=2,
        RLIMIT_NOFILE=3,
        RLIMIT_NPROC=4,
        setrlimit=lambda kind, value: limits.__setitem__(kind, value),
        getrlimit=lambda kind: limits[kind],
    )
    monkeypatch.setitem(sys.modules, "resource", fake)

    contract.install_process_limits()

    assert limits == {
        1: (0, 0),
        2: (contract.SINGLE_FILE_BYTES, contract.SINGLE_FILE_BYTES),
        3: (contract.OPEN_FILE_LIMIT, contract.OPEN_FILE_LIMIT),
        4: (contract.PID_LIMIT, contract.PID_LIMIT),
    }
    assert contract.rlimit_contract_violations() == ()


def test_production_budget_and_agent_depth_cannot_be_raised(monkeypatch) -> None:
    monkeypatch.setattr(
        "maverick.production_scope.production_surface_required",
        lambda: True,
    )
    budget = Budget(
        max_input_tokens=2_000_000,
        max_output_tokens=400_000,
        max_wall_seconds=7200,
        max_tool_calls=5_000,
    )

    contract.clamp_budget_for_production(budget)

    assert budget.max_input_tokens == 1_000_000
    assert budget.max_output_tokens == 200_000
    assert budget.max_wall_seconds == 1800
    assert budget.max_tool_calls == 500
    assert contract.require_agent_depth(3) == 3
    with pytest.raises(contract.ProductionRuntimeContractError, match="exceeds 3"):
        contract.require_agent_depth(4)

    budget.max_wall_seconds = float("nan")
    with pytest.raises(contract.ProductionRuntimeContractError, match="max_wall_seconds"):
        contract.clamp_budget_for_production(budget)


def test_disabled_production_tools_cannot_redirect_into_durable_state(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "maverick.production_scope.production_surface_required",
        lambda: True,
    )
    from maverick.sandbox import build_sandbox

    with pytest.raises(
        contract.ProductionRuntimeContractError,
        match="tool workspace",
    ):
        build_sandbox(
            workdir=contract.STATE_ROOT,
            backend="disabled",
            sandbox_config={"backend": "disabled"},
        )


def test_wrapped_audit_key_uses_mounted_secret_custody(monkeypatch) -> None:
    from maverick import secret_provider
    from maverick.audit import signing

    seen: list[str] = []
    monkeypatch.delenv("MAVERICK_AUDIT_SIGNING_KEY_WRAPPED", raising=False)
    monkeypatch.setattr(
        secret_provider,
        "get_secret",
        lambda name: seen.append(name) or "00",
    )
    monkeypatch.setattr(signing, "_have_crypto", lambda: False)

    assert signing._kms_wrapped_keypair() is None
    assert seen == ["MAVERICK_AUDIT_SIGNING_KEY_WRAPPED"]


def test_soc2_probe_uses_mounted_wrapped_audit_key(monkeypatch, tmp_path: Path) -> None:
    from maverick import secret_provider, soc2
    from maverick.audit import signing

    key_dir = tmp_path / "audit-keys"
    key_dir.mkdir()
    monkeypatch.delenv("MAVERICK_AUDIT_SIGNING_KEY", raising=False)
    monkeypatch.delenv("MAVERICK_AUDIT_SIGNING_KEY_WRAPPED", raising=False)
    monkeypatch.setattr(signing, "_key_dir", lambda: key_dir)
    monkeypatch.setattr(signing, "_injected_keypair", lambda: None)
    monkeypatch.setattr(
        signing,
        "_kms_wrapped_keypair",
        lambda: (b"private", b"public", "key-id"),
    )
    monkeypatch.setattr(
        secret_provider,
        "get_secret",
        lambda name: "wrapped-ciphertext"
        if name == "MAVERICK_AUDIT_SIGNING_KEY_WRAPPED"
        else None,
    )

    status = soc2._probe_signing_key()

    assert status["status"] == soc2.STATUS_ENABLED
    assert status["wrapped_key_configured"] is True
    assert status["wrapped_key_usable"] is True
    assert status["offhost_key"] is True
