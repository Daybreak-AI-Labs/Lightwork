"""Machine-verifiable release contract for production schema migration."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest
from maverick import production_migration as pm

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify_production_migration_contract.py"
SPEC = importlib.util.spec_from_file_location("production_migration_verifier", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def _copy_contract_tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(
        ROOT / verifier.HELM_CHART_PATH,
        root / verifier.HELM_CHART_PATH,
    )
    for relative in (
        verifier.CONTRACT_PATH,
        verifier.RUNNER_PATH,
        verifier.LOCK_PATH,
        verifier.PACKAGE_PATH,
        verifier.HELM_JOB_PATH,
        verifier.HELM_VALUES_PATH,
        verifier.HELM_DEFAULT_VALUES_PATH,
        verifier.HELM_HELPERS_PATH,
        verifier.DOC_PATH,
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())
    return root


def _refresh_temporary_source_binding(
    root: Path,
    relative: Path,
    expected: dict,
) -> dict:
    digest = verifier._normalized_source_sha256(root / relative)
    contract_path = root / verifier.CONTRACT_PATH
    committed = json.loads(contract_path.read_text(encoding="utf-8"))
    committed["repository_source_sha256"][relative.as_posix()] = digest
    contract_path.write_text(json.dumps(committed), encoding="utf-8")
    refreshed = copy.deepcopy(expected)
    refreshed["repository_source_sha256"][relative.as_posix()] = digest
    return refreshed


def test_helm_semantic_fixture_includes_required_identity_posture() -> None:
    command = verifier._helm_template_command(ROOT, Path("helm"))

    assert "identity.mode=oidc" in command
    assert "identity.config.oidc.issuer=https://idp.example.test/" in command
    assert "identity.config.dashboard.defaultRole=viewer" in command
    assert "identity.config.kernel.roleAssignments.default=restricted" in command
    assert (
        "productionOnboarding.identityPostureSha256="
        + verifier._HELM_IDENTITY_POSTURE_SHA256
    ) in command


def test_committed_contract_matches_packaged_ladder_and_stays_conservative(
    tmp_path,
    monkeypatch,
) -> None:
    hostile = tmp_path / "hostile-runtime"
    hostile.mkdir()
    unreadable_as_toml = hostile / "config.toml"
    unreadable_as_toml.mkdir()
    monkeypatch.setenv("MAVERICK_HOME", str(hostile))
    monkeypatch.setenv("MAVERICK_CONFIG", str(unreadable_as_toml))
    monkeypatch.setenv("MAVERICK_CLIENT_ID", "INVALID CLIENT")
    monkeypatch.setenv("MAVERICK_TENANT", "INVALID TENANT")
    monkeypatch.setenv("LIGHTWORK_CONFIG", str(unreadable_as_toml))
    monkeypatch.setenv("LIGHTWORK_CLIENT_ID", "INVALID LIGHTWORK CLIENT")
    monkeypatch.setenv("LIGHTWORK_TENANT", "INVALID LIGHTWORK TENANT")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "wrong-checkout"))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "wrong-python-home"))
    original_builder = pm.production_migration_contract
    monkeypatch.setattr(
        pm,
        "production_migration_contract",
        lambda: (_ for _ in ()).throw(AssertionError("poisoned parent module used")),
    )

    summary = verifier.verify_production_migration_contract(ROOT)
    monkeypatch.setattr(pm, "production_migration_contract", original_builder)

    assert summary["schema_version"] == 1
    assert summary["target_head"] == pm.migration_versions()[-1]
    assert summary["target_versions"] == len(pm.migration_versions())
    assert summary["external_qualification_items"] == 12
    assert summary["status"] == "repository-contract-not-live-qualification-evidence"
    assert set(verifier._build_packaged_contract(ROOT)["repository_source_sha256"]) == {
        verifier.HELM_JOB_PATH.as_posix(),
        verifier.HELM_VALUES_PATH.as_posix(),
        verifier.HELM_DEFAULT_VALUES_PATH.as_posix(),
        verifier.HELM_HELPERS_PATH.as_posix(),
    }


def test_verifier_refuses_contract_drift(tmp_path, monkeypatch) -> None:
    expected = pm.production_migration_contract()
    root = _copy_contract_tree(tmp_path)
    contract_path = root / verifier.CONTRACT_PATH
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["compatibility"]["policy"] = "arbitrary-multi-hop"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    monkeypatch.setattr(verifier, "_build_packaged_contract", lambda _root: expected)

    with pytest.raises(
        verifier.ProductionMigrationContractError,
        match="differs from the packaged ladder",
    ):
        verifier.verify_production_migration_contract(root)


def test_verifier_refuses_duplicate_contract_keys(tmp_path, monkeypatch) -> None:
    contract = tmp_path / "contract.json"
    contract.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")

    with pytest.raises(verifier.ProductionMigrationContractError, match="duplicate"):
        verifier._load_strict(contract)

    monkeypatch.setenv("MAVERICK_CONFIG", "poison")
    monkeypatch.setenv("mAvErIcK_FAKE_INPUT", "poison")
    monkeypatch.setenv("LIGHTWORK_CONFIG", "poison")
    monkeypatch.setenv("lIgHtWoRk_FAKE_INPUT", "poison")
    monkeypatch.setenv("PYTHONPATH", "poison")
    monkeypatch.setenv("PYTHONHOME", "poison")

    def clean_success(command, **kwargs):
        assert command[:4] == [verifier.sys.executable, "-I", "-S", "-c"]
        assert Path(command[-1]).resolve() == (
            ROOT / "packages" / "maverick-core"
        ).resolve()
        child_env = kwargs["env"]
        assert Path(child_env["MAVERICK_HOME"]).is_dir()
        assert not any(
            name.upper().startswith(("LIGHTWORK_", "MAVERICK_"))
            and name.upper() not in {"MAVERICK_HOME"}
            for name in child_env
        )
        assert "PYTHONPATH" not in {name.upper() for name in child_env}
        assert "PYTHONHOME" not in {name.upper() for name in child_env}
        kwargs["stdout"].write(b"{}")
        return verifier.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(verifier.subprocess, "run", clean_success)
    assert verifier._build_packaged_contract(ROOT) == {}

    invalid_payloads = (
        b'{"value":1,"value":2}',
        b'{"TOP_SECRET_TOKEN":1,"TOP_SECRET_TOKEN":2}',
        b'{"value":NaN}',
        b'{"value":1e999}',
        (
            b'{"value":'
            + (b"[" * (verifier._PACKAGED_CONTRACT_MAX_DEPTH + 2))
            + b"0"
            + (b"]" * (verifier._PACKAGED_CONTRACT_MAX_DEPTH + 2))
            + b"}"
        ),
        b"\xff",
        b" " * (verifier._PACKAGED_CONTRACT_MAX_BYTES + 1),
    )
    for payload in invalid_payloads:
        def invalid_success(command, _payload=payload, **kwargs):
            kwargs["stdout"].write(_payload)
            return verifier.subprocess.CompletedProcess(command, 0)

        monkeypatch.setattr(
            verifier.subprocess,
            "run",
            invalid_success,
        )
        with pytest.raises(verifier.ProductionMigrationContractError) as invalid:
            verifier._build_packaged_contract(ROOT)
        assert "TOP_SECRET_TOKEN" not in str(invalid.value)

    for stdout, stderr in (
        (b"", b""),
        (b"{}", b"secret successful stderr"),
    ):
        def bad_stream(command, _stdout=stdout, _stderr=stderr, **kwargs):
            kwargs["stdout"].write(_stdout)
            kwargs["stderr"].write(_stderr)
            return verifier.subprocess.CompletedProcess(command, 0)

        monkeypatch.setattr(
            verifier.subprocess,
            "run",
            bad_stream,
        )
        with pytest.raises(verifier.ProductionMigrationContractError) as invalid:
            verifier._build_packaged_contract(ROOT)
        assert "secret" not in str(invalid.value)

    def failed_child(command, **kwargs):
        kwargs["stdout"].write(b"secret stdout")
        kwargs["stderr"].write(b"secret stderr")
        return verifier.subprocess.CompletedProcess(command, 17)

    monkeypatch.setattr(verifier.subprocess, "run", failed_child)
    with pytest.raises(verifier.ProductionMigrationContractError) as failed:
        verifier._build_packaged_contract(ROOT)
    assert "secret" not in str(failed.value)

    def timeout(command, **_kwargs):
        raise verifier.subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(verifier.subprocess, "run", timeout)
    with pytest.raises(verifier.ProductionMigrationContractError):
        verifier._build_packaged_contract(ROOT)


def test_verifier_refuses_weakened_helm_binding(tmp_path, monkeypatch) -> None:
    expected = pm.production_migration_contract()
    root = _copy_contract_tree(tmp_path)
    job_path = root / verifier.HELM_JOB_PATH
    job_path.write_text(
        job_path.read_text(encoding="utf-8").replace('"--image-digest"', '"--tag"'),
        encoding="utf-8",
    )
    monkeypatch.setattr(verifier, "_build_packaged_contract", lambda _root: expected)

    with pytest.raises(
        verifier.ProductionMigrationContractError,
        match="Helm migration job",
    ):
        verifier.verify_production_migration_contract(root)


def test_verifier_refuses_commented_out_helm_validation(
    tmp_path,
    monkeypatch,
) -> None:
    expected = pm.production_migration_contract()
    root = _copy_contract_tree(tmp_path)
    helpers_path = root / verifier.HELM_HELPERS_PATH
    active = (
        '{{- if ne (int .Values.migration.ttlSecondsAfterFinished) 3600 -}}'
    )
    helpers_path.write_text(
        helpers_path.read_text(encoding="utf-8").replace(
            active,
            "{{/* " + active + " */}}",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(verifier, "_build_packaged_contract", lambda _root: expected)

    with pytest.raises(
        verifier.ProductionMigrationContractError,
        match="Helm production validation",
    ):
        verifier.verify_production_migration_contract(root)


def test_verifier_refuses_migration_job_hidden_in_a_dead_branch(
    tmp_path,
    monkeypatch,
) -> None:
    expected = pm.production_migration_contract()
    root = _copy_contract_tree(tmp_path)
    job_path = root / verifier.HELM_JOB_PATH
    job = job_path.read_text(encoding="utf-8")
    job_path.write_text(
        "{{- if false }}\n" + job + "\n{{- end }}\n",
        encoding="utf-8",
    )
    expected = _refresh_temporary_source_binding(
        root,
        verifier.HELM_JOB_PATH,
        expected,
    )
    monkeypatch.setattr(verifier, "_build_packaged_contract", lambda _root: expected)

    with pytest.raises(
        verifier.ProductionMigrationContractError,
        match="constant dead Go-template branch",
    ):
        verifier.verify_production_migration_contract(root)


if os.name == "nt":
    _HELM_CANDIDATE = (
        ROOT / "work" / "helm-v3.17.3" / "windows-amd64" / "helm.exe"
    )
else:
    _HELM_ON_PATH = shutil.which("helm")
    _HELM_CANDIDATE = Path(_HELM_ON_PATH) if _HELM_ON_PATH else None

HELM_3 = (
    _HELM_CANDIDATE.resolve()
    if _HELM_CANDIDATE is not None and _HELM_CANDIDATE.is_file()
    else None
)


@pytest.mark.skipif(HELM_3 is None, reason="native Helm 3 is unavailable")
def test_semantic_helm_render_refuses_value_dependent_dead_branch_after_rebinding(
    tmp_path,
    monkeypatch,
) -> None:
    expected = pm.production_migration_contract()
    root = _copy_contract_tree(tmp_path)
    job_path = root / verifier.HELM_JOB_PATH
    job = job_path.read_text(encoding="utf-8")
    job_path.write_text(
        "{{- if .Values.migration.unreachable }}\n" + job + "\n{{- end }}\n",
        encoding="utf-8",
    )
    expected = _refresh_temporary_source_binding(
        root,
        verifier.HELM_JOB_PATH,
        expected,
    )
    monkeypatch.setattr(verifier, "_build_packaged_contract", lambda _root: expected)

    with pytest.raises(
        verifier.ProductionMigrationContractError,
        match="semantic render omitted the exact Job or NetworkPolicy",
    ):
        verifier.verify_production_migration_contract(
            root,
            helm_binary=HELM_3,
            require_helm=True,
        )


def test_verifier_requires_explicit_live_evidence_boundary(tmp_path, monkeypatch) -> None:
    expected = pm.production_migration_contract()
    root = _copy_contract_tree(tmp_path)
    doc_path = root / verifier.DOC_PATH
    doc_path.write_text(
        doc_path.read_text(encoding="utf-8").replace(
            "live qualification remains open",
            "qualification complete",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(verifier, "_build_packaged_contract", lambda _root: expected)

    with pytest.raises(
        verifier.ProductionMigrationContractError,
        match="conservative statement",
    ):
        verifier.verify_production_migration_contract(root)
