from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from maverick import proof_provenance

REPO_ROOT = Path(__file__).resolve().parents[3]


def _source_content(digest: str = "4" * 64) -> dict[str, object]:
    return {
        "schema": proof_provenance.SOURCE_CONTENT_IDENTITY_SCHEMA,
        "path_count": 7,
        "byte_count": 700,
        "sha256": digest,
        "digest_algorithm": "sha256",
        "digest_scope": proof_provenance.SOURCE_CONTENT_DIGEST_SCOPE,
    }


def _stub_generator_sources(root: Path, generator) -> None:
    for relative in {
        "proof/generate_results.py",
        generator.ISOLATED_BOOTSTRAP,
        *(harness.script for harness in generator.HARNESSES),
    }:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# materialized fixture: {relative}\n", encoding="utf-8")
    provenance = root / "packages/maverick-core/maverick/proof_provenance.py"
    provenance.parent.mkdir(parents=True, exist_ok=True)
    provenance.write_bytes(
        (REPO_ROOT / "packages/maverick-core/maverick/proof_provenance.py").read_bytes()
    )
    benchmark = root / "benchmarks" / "fixture_runtime.py"
    benchmark.parent.mkdir(parents=True, exist_ok=True)
    benchmark.write_text("FIXTURE_RUNTIME = True\n", encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "user.name=Proof Generator Test",
            "-c",
            "user.email=proof-generator@example.invalid",
            *args,
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _load_generator():
    path = REPO_ROOT / "proof" / "generate_results.py"
    spec = importlib.util.spec_from_file_location("lightwork_generate_results", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_adapter_fixture():
    path = REPO_ROOT / "proof" / "adapter_rung_proof.py"
    spec = importlib.util.spec_from_file_location("lightwork_adapter_rung_proof", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_adapter_fixture_binds_and_restores_process_approval_trust(monkeypatch, tmp_path):
    fixture = _load_adapter_fixture()
    public = b"p" * 32

    def fake_keys(keydir):
        keydir.mkdir(parents=True)
        (keydir / "operator.pub").write_bytes(public)
        return "private"

    observed = []

    def fake_run(work, **kwargs):
        observed.append(fixture.os.environ["MAVERICK_APPROVER_KEYS"])
        assert kwargs["private_hex"] == "private"
        assert kwargs["public_hex"] == public.hex()
        return []

    monkeypatch.setattr(fixture, "_mk_keys", fake_keys)
    monkeypatch.setattr(fixture, "_run_all", fake_run)
    monkeypatch.setenv("MAVERICK_APPROVER_KEYS", "existing")
    assert fixture.run_all(tmp_path) == []
    assert observed == [public.hex()]
    assert fixture.os.environ["MAVERICK_APPROVER_KEYS"] == "existing"

    monkeypatch.delenv("MAVERICK_APPROVER_KEYS")
    monkeypatch.setattr(
        fixture,
        "_run_all",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    try:
        fixture.run_all(tmp_path / "second")
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("fixture failure was not propagated")
    assert "MAVERICK_APPROVER_KEYS" not in fixture.os.environ


def test_fixture_child_environment_discards_hostile_ambient_inputs(tmp_path):
    generator = _load_generator()
    root = tmp_path / "repo"
    runtime_home = tmp_path / "runtime"
    clean_parent = {
        "PATH": "trusted-system-path",
        "SYSTEMROOT": "C:\\Windows",
    }
    hostile_values = {
        "MAVERICK_CONFIG": "hostile-maverick-config",
        "MAVERICK_DGM_PROOF_EVAL_COMMAND": "hostile-evaluator",
        "LIGHTWORK_CONFIG": "hostile-lightwork-config",
        "ANTHROPIC_API_KEY": "hostile-anthropic-secret",
        "OPENAI_BASE_URL": "https://hostile-provider.invalid",
        "AWS_SHARED_CREDENTIALS_FILE": "hostile-aws-config",
        "OLLAMA_HOST": "https://hostile-ollama.invalid",
        "PYTHONPATH": "hostile-pythonpath",
        "PYTHONHOME": "hostile-pythonhome",
        "PYTEST_ADDOPTS": "--pdb --trace",
        "PYTEST_PLUGINS": "hostile_pytest_plugin",
    }
    hostile_parent = {**clean_parent, **hostile_values}

    clean_env, clean_fixed, clean_record = generator._execution_environment(
        root,
        runtime_home,
        parent_environment=clean_parent,
    )
    child_env, fixed, record = generator._execution_environment(
        root,
        runtime_home,
        parent_environment=hostile_parent,
    )

    assert child_env == clean_env
    assert fixed == clean_fixed
    assert record == clean_record
    assert child_env["PYTHONPATH"] == os.pathsep.join(
        (
            str((root / "packages/maverick-core").resolve()),
            str(root.resolve()),
        )
    )
    assert {name for name in child_env if name.startswith("MAVERICK_")} == {
        "MAVERICK_CLIENT_ID",
        "MAVERICK_CONFIG",
        "MAVERICK_HOME",
    }
    assert not [name for name in child_env if name.startswith("LIGHTWORK_")]
    assert child_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    for name in hostile_values:
        if name not in {"MAVERICK_CONFIG", "PYTHONPATH"}:
            assert name not in child_env
    serialized = json.dumps(
        {"fixed": fixed, "provenance": record},
        sort_keys=True,
    )
    assert not any(value in serialized for value in hostile_values.values())
    assert set(hostile_values).issubset(
        {
            *record["policy"]["known_discarded_names"],
            "MAVERICK_CONFIG",
            "MAVERICK_DGM_PROOF_EVAL_COMMAND",
            "LIGHTWORK_CONFIG",
            "PYTHONHOME",
            "PYTHONPATH",
        }
    )
    valid, reason = proof_provenance.validate_fixture_environment_provenance(
        record,
        fixed,
    )
    assert valid, reason
    expected_value_records = [
        {
            "name": name,
            "sha256": proof_provenance.sha256_bytes(child_env[name].encode("utf-8")),
        }
        for name in sorted(child_env)
    ]
    assert record["value_sha256"] == expected_value_records
    assert record["environment_sha256"] == proof_provenance.sha256_bytes(
        proof_provenance.canonical_json_bytes(expected_value_records)
    )


def test_generate_results_retains_exact_outputs_and_conservative_boundaries(
    monkeypatch, tmp_path
):
    generator = _load_generator()
    _stub_generator_sources(tmp_path, generator)
    source = {
        "available": True,
        "revision": "1" * 40,
        "dirty": True,
        "worktree_state_sha256": "2" * 64,
        "source_content": _source_content(),
    }
    runtime = {
        "python_executable": "test-python",
        "python_version": "3.12.0",
        "python_implementation": "CPython",
        "platform": "test-platform",
        "machine": "test-machine",
        "dependency_count": 1,
        "dependencies_sha256": "3" * 64,
        "dependencies": [{"name": "cryptography", "version": "48.0.1"}],
    }
    monkeypatch.setattr(
        generator.proof_provenance, "repository_provenance", lambda *a, **k: source
    )
    def fake_materialize(_root, destination, **_kwargs):
        destination.mkdir()
        _stub_generator_sources(destination, generator)
        return source["source_content"]

    monkeypatch.setattr(
        generator.proof_provenance,
        "materialize_repository_source",
        fake_materialize,
    )
    monkeypatch.setattr(
        generator.proof_provenance, "runtime_provenance", lambda: runtime
    )
    original_validate_runtime_control = generator._validate_runtime_import_control
    runtime_control_validations: list[Path] = []

    def validate_runtime_control(temporary_root, expected):
        runtime_control_validations.append(temporary_root)
        original_validate_runtime_control(temporary_root, expected)

    monkeypatch.setattr(
        generator,
        "_validate_runtime_import_control",
        validate_runtime_control,
    )
    counter = iter(range(len(generator.HARNESSES)))

    def fake_runner(argv, *, cwd, env):
        index = next(counter)
        assert cwd != tmp_path.resolve()
        assert (cwd / generator.ISOLATED_BOOTSTRAP).is_file()
        assert argv[: 1 + len(generator._ISOLATED_PYTHON_OPTIONS)] == [
            sys.executable,
            *generator._ISOLATED_PYTHON_OPTIONS,
        ]
        assert env["PYTHONUTF8"] == "1"
        return generator.CommandOutcome(
            started_at=100.0 + index,
            completed_at=100.25 + index,
            exit_code=(
                1 if "dgm_code_rung" in argv[-1] else (2 if index == 1 else 0)
            ),
            stdout=(
                b"  [SKIP]  missing optional prerequisite\n"
                if index == 1
                else f"stdout-{index}\n".encode()
            ),
            stderr=(b"missing evaluator\n" if "dgm_code_rung" in argv[-1] else b""),
        )

    output = tmp_path / "proof" / "RESULTS.md"
    evidence_dir = tmp_path / "proof" / "results"
    manifest = generator.generate(
        root=tmp_path,
        output=output,
        evidence_dir=evidence_dir,
        runner=fake_runner,
        generated_at=99.0,
    )

    assert manifest["all_commands_passed"] is False
    assert len(runtime_control_validations) == 2 * len(generator.HARNESSES) + 1
    assert manifest["source_stable"] is True
    assert manifest["source_at_completion"] == source
    assert manifest["source"] == source
    assert manifest["runtime"] == runtime
    assert manifest["evidence_valid"] is False
    assert manifest["execution_mode"] == {
        "id": "injected-or-noncanonical-test-v2",
        "canonical_cli_verified": False,
        "runner_injected": True,
        "timestamp_injected": True,
    }
    assert manifest["execution_limits"]["harness_timeout_seconds"] == 900
    assert manifest["execution_limits"]["raw_stream_max_bytes"] == 4 * 1024 * 1024
    assert manifest["execution_limits"]["raw_output_total_max_bytes"] == 24 * 1024 * 1024
    assert manifest["execution_limits"]["artifact_tree_max_bytes"] == 64 * 1024 * 1024
    assert manifest["execution_limits"]["source_proof_file_max_bytes"] == 4 * 1024 * 1024
    assert manifest["execution_limits"]["source_proof_tree_max_bytes"] == 32 * 1024 * 1024
    source_execution_tree = manifest["source_execution_tree"]
    assert source_execution_tree["symlink_materialization_policy"] == (
        proof_provenance.SYMLINK_MATERIALIZATION_POLICY
    )
    assert source_execution_tree["local_materialized_tree_identity_enforced"] is True
    assert source_execution_tree["local_materialized_tree_identity_serialized"] is False
    assert source_execution_tree["semantic_identity"] == (
        proof_provenance.semantic_execution_tree_identity(source["source_content"])
    )
    assert "materialized_tree" not in source_execution_tree
    assert "materialized_tree_sha256" not in source_execution_tree
    environment_valid, environment_reason = (
        proof_provenance.validate_fixture_environment_provenance(
            manifest["execution_environment"],
            manifest["environment_overrides"],
        )
    )
    assert environment_valid, environment_reason
    assert manifest["execution_environment"]["policy"]["secret_values_serialized"] is False
    assert manifest["generator_command"] is None
    assert manifest["canonical_generator_command"]["argv"] == list(
        generator._CANONICAL_GENERATOR
    )
    assert (evidence_dir / "manifest.json").is_file()
    assert (evidence_dir / "generation.json").is_file()
    assert manifest["manifest_sha256"] == proof_provenance.sha256_bytes(
        (evidence_dir / "manifest.json").read_bytes()
    )
    dgm = next(run for run in manifest["runs"] if run["id"] == "dgm-code-rung-fixture")
    assert dgm["status"] == "failed"
    skipped = next(
        run for run in manifest["runs"] if run["id"] == "self-learning-fixture"
    )
    assert skipped["exit_code"] == 2
    assert skipped["status"] == "skipped"
    assert dgm["command"]["argv"] == [
        *generator._HARNESS_INTERPRETER_PREFIX,
        generator.ISOLATED_BOOTSTRAP,
        "proof/dgm_code_rung_proof.py",
    ]
    assert dgm["command"]["argv"][0] == "python"
    stdout = b"stdout-2\n"
    stderr = b"missing evaluator\n"
    assert dgm["stdout"]["sha256"] == proof_provenance.sha256_bytes(stdout)
    assert dgm["stderr"]["sha256"] == proof_provenance.sha256_bytes(stderr)
    assert (tmp_path / dgm["stdout"]["path"]).read_bytes() == stdout
    assert (tmp_path / dgm["stderr"]["path"]).read_bytes() == stderr

    markdown = output.read_text(encoding="utf-8")
    assert "Evidence status: INVALID non-canonical or injected generator execution" in markdown
    assert "Assessed source revision: `" + "1" * 40 + "`" in markdown
    assert "Stable source content:" in markdown
    assert "4" * 64 in markdown
    assert "no bundled backend currently" in markdown
    assert "every other combination fails" in markdown
    assert "No competitive capability benchmark" in markdown
    assert manifest["manifest_sha256"] in markdown
    assert manifest["execution_environment"]["policy_sha256"] in markdown
    assert manifest["execution_environment"]["environment_sha256"] in markdown
    assert "Normalized UTF-8/LF display of stderr" in markdown
    raw_outputs = {
        run["id"]: {
            "stdout": (tmp_path / run["stdout"]["path"]).read_bytes(),
            "stderr": (tmp_path / run["stderr"]["path"]).read_bytes(),
        }
        for run in manifest["runs"]
    }
    retained_manifest = json.loads((evidence_dir / "manifest.json").read_bytes())
    assert markdown == generator.render_results_markdown(
        retained_manifest,
        raw_outputs,
        manifest_path="proof/results/manifest.json",
        manifest_sha256=manifest["manifest_sha256"],
    )
    generation = json.loads((evidence_dir / "generation.json").read_bytes())
    assert generation["schema"] == generator._GENERATION_SCHEMA
    assert generation["results"]["sha256"] == manifest["results_sha256"]
    assert generation["limits"]["results_max_bytes"] == 16 * 1024 * 1024
    assert generation["observed"]["raw_output_total_bytes"] == sum(
        stream["bytes"]
        for run in manifest["runs"]
        for stream in (run["stdout"], run["stderr"])
    )

    injected = copy.deepcopy(retained_manifest)
    injected["runtime"]["platform"] = "hostile\n## forged heading `tick`"
    injected["runs"][0]["title"] = "title\n# forged"
    before = copy.deepcopy(injected)
    rendered = generator.render_results_markdown(
        injected,
        raw_outputs,
        manifest_path="proof/results/manifest.json",
        manifest_sha256=manifest["manifest_sha256"],
    )
    assert injected == before
    assert "hostile\\x0a## forged heading" in rendered
    assert "title\\x0a# forged" in rendered


def test_generate_results_cli_can_record_failures_without_calling_them_passes(
    monkeypatch, tmp_path
):
    generator = _load_generator()
    _stub_generator_sources(tmp_path, generator)
    monkeypatch.setattr(
        generator,
        "generate",
        lambda **kwargs: {
            "all_commands_passed": False,
            "evidence_valid": False,
            "manifest_sha256": "a" * 64,
        },
    )
    assert generator.main([]) == 0
    assert generator.main(["--require-pass"]) == 1
    monkeypatch.setattr(
        generator,
        "generate",
        lambda **kwargs: {
            "all_commands_passed": True,
            "evidence_valid": False,
            "manifest_sha256": "b" * 64,
        },
    )
    assert generator.main(["--require-pass"]) == 1
    with pytest.raises(SystemExit):
        generator.main(["--output", str(tmp_path / "RESULTS.md")])


def test_generate_results_invalidates_a_source_change_during_execution(
    monkeypatch, tmp_path
):
    generator = _load_generator()
    _stub_generator_sources(tmp_path, generator)
    start = {
        "available": True,
        "revision": "a" * 40,
        "dirty": True,
        "worktree_state_sha256": "b" * 64,
        "source_content": _source_content("d" * 64),
    }
    completed = {**start, "worktree_state_sha256": "c" * 64}
    snapshots = iter((start, completed, completed))
    monkeypatch.setattr(
        generator.proof_provenance,
        "repository_provenance",
        lambda *args, **kwargs: next(snapshots),
    )
    def fake_materialize(_root, destination, **_kwargs):
        destination.mkdir()
        _stub_generator_sources(destination, generator)
        return start["source_content"]

    monkeypatch.setattr(
        generator.proof_provenance,
        "materialize_repository_source",
        fake_materialize,
    )
    monkeypatch.setattr(
        generator.proof_provenance,
        "runtime_provenance",
        lambda: {
            "python_executable": "python",
            "python_version": "3.12.0",
            "python_implementation": "CPython",
            "platform": "test",
            "machine": "test",
            "dependency_count": 0,
            "dependencies_sha256": proof_provenance.sha256_bytes(b"[]"),
            "dependencies": [],
        },
    )
    manifest = generator.generate(
        root=tmp_path,
        runner=lambda *args, **kwargs: generator.CommandOutcome(
            1.0, 2.0, 0, b"ok\n", b""
        ),
        generated_at=0.0,
    )
    assert manifest["source_stable"] is False
    assert manifest["source_at_completion"] == completed
    assert "INVALID mixed-source execution" in (
        tmp_path / "proof" / "RESULTS.md"
    ).read_text(encoding="utf-8")


def test_isolated_materialized_bootstrap_cannot_load_ignored_sitecustomize(tmp_path):
    generator = _load_generator()
    repository = tmp_path / "repository"
    repository.mkdir()
    bootstrap = repository / generator.ISOLATED_BOOTSTRAP
    harness = repository / "proof/run_proof.py"
    bootstrap.parent.mkdir(parents=True)
    bootstrap.write_bytes((REPO_ROOT / generator.ISOLATED_BOOTSTRAP).read_bytes())
    marker = tmp_path / "system-customization-executed.txt"
    runtime_site = tmp_path / "runtime" / "site-packages"
    runtime_site.mkdir(parents=True)
    (runtime_site / "dependency_probe.py").write_text(
        "VALUE = 'isolated-safe'\n",
        encoding="utf-8",
    )
    hostile = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
    (runtime_site / "sitecustomize.py").write_text(hostile, encoding="utf-8")
    (runtime_site / "hostile.pth").write_text(
        "import sitecustomize\n",
        encoding="utf-8",
    )
    harness.write_text(
        "import sys\n"
        "from dependency_probe import VALUE\n"
        "assert sys.flags.isolated and sys.flags.no_site and sys.flags.safe_path\n"
        "assert sys.flags.utf8_mode and sys.dont_write_bytecode\n"
        "print(VALUE)\n",
        encoding="utf-8",
    )
    for name in generator._MATERIALIZED_PROOF_FILES - {
        "isolated_harness_bootstrap.py",
        "run_proof.py",
    }:
        (repository / "proof" / name).write_text(
            f"# exact proof fixture: {name}\n",
            encoding="utf-8",
        )
    (repository / "packages/maverick-core/maverick").mkdir(parents=True)
    (repository / "packages/maverick-core/maverick/__init__.py").write_text(
        "# fixture package\n",
        encoding="utf-8",
    )
    (repository / "benchmarks").mkdir()
    (repository / "benchmarks/fixture.py").write_text(
        "FIXTURE = True\n",
        encoding="utf-8",
    )
    (repository / ".gitignore").write_text(
        "sitecustomize.py\npwned.txt\n",
        encoding="utf-8",
    )
    (repository / "sitecustomize.py").write_text(
        "from pathlib import Path\nPath('pwned.txt').write_text('executed')\n",
        encoding="utf-8",
    )
    _git(repository, "init", "-q")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "isolated source")

    materialized = tmp_path / "materialized"
    proof_provenance.materialize_repository_source(repository, materialized)
    assert not (materialized / "sitecustomize.py").exists()
    runtime_record = generator._write_runtime_import_control(
        tmp_path,
        [str(runtime_site.resolve())],
    )
    assert runtime_record["paths"] == [str(runtime_site.resolve())]
    environment, _fixed, _record = generator._execution_environment(
        materialized,
        tmp_path / "runtime",
        parent_environment={"PATH": os.environ["PATH"]},
    )
    outcome = generator._run(
        [
            sys.executable,
            *generator._ISOLATED_PYTHON_OPTIONS,
            generator.ISOLATED_BOOTSTRAP,
            "proof/run_proof.py",
        ],
        cwd=materialized,
        env=environment,
    )
    assert outcome.exit_code == 0
    assert outcome.stdout.strip() == b"isolated-safe"
    assert outcome.termination_reason is None
    assert not (repository / "pwned.txt").exists()
    assert not (materialized / "pwned.txt").exists()
    assert not marker.exists()


def test_generator_paths_and_command_are_fixed_and_no_follow(tmp_path):
    generator = _load_generator()
    root = tmp_path.resolve()
    with pytest.raises(ValueError, match="fixed repository-relative paths"):
        generator._prepare_fixed_artifact_paths(
            root,
            root / "alternate.md",
            root / "proof/results",
        )
    assert generator._canonical_generator_command(()) == list(
        generator._CANONICAL_GENERATOR
    )
    assert generator._canonical_generator_command(("--require-pass",))[-1] == (
        "--require-pass"
    )
    with pytest.raises(ValueError, match="unapproved or duplicate"):
        generator._canonical_generator_command(("--output",))

    target = root / "target.py"
    linked = root / "proof/generate_results.py"
    linked.parent.mkdir(parents=True)
    target.write_text("# target\n", encoding="utf-8")
    try:
        linked.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    with pytest.raises(ValueError, match="symlink or reparse point"):
        generator._require_regular_source(root, "proof/generate_results.py")


def test_materialized_proof_tree_is_exact_and_tree_token_detects_mutation(tmp_path):
    generator = _load_generator()
    _stub_generator_sources(tmp_path, generator)
    records = generator._materialized_proof_tree_records(tmp_path)
    assert {Path(record["path"]).name for record in records} == (
        generator._MATERIALIZED_PROOF_FILES
    )
    before = generator._materialized_tree_token(tmp_path)
    identity = proof_provenance.materialized_tree_identity(tmp_path)
    generator._make_materialized_tree_read_only(tmp_path)
    assert proof_provenance.materialized_tree_identity(tmp_path) == identity
    assert generator._materialized_tree_token(tmp_path) == before
    (tmp_path / "proof/run_proof.py").chmod(0o644)
    (tmp_path / "proof/run_proof.py").write_text(
        "# mutated after materialization\n",
        encoding="utf-8",
    )
    assert generator._materialized_tree_token(tmp_path) != before
    (tmp_path / "proof/unexpected.py").write_text("# stale\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="hard entry ceiling|unexpected entry"):
        generator._materialized_proof_tree_records(tmp_path)


def test_runtime_import_admissions_are_ordered_unique_and_do_not_process_pth():
    generator = _load_generator()
    records = generator._runtime_import_path_records()
    paths = [record["path"] for record in records]
    assert len(paths) == len(set(paths))
    assert all(Path(path).is_absolute() and Path(path).is_dir() for path in paths)
    assert any("site packages" in record["reason"] for record in records)


def test_runtime_import_control_replacement_is_detected_between_harnesses(tmp_path):
    generator = _load_generator()
    expected = generator._write_runtime_import_control(tmp_path, [str(tmp_path)])
    generator._validate_runtime_import_control(tmp_path, expected)
    control = tmp_path / generator._RUNTIME_IMPORT_PATHS_FILE
    control.chmod(0o644)
    control.unlink()
    control.write_text(
        json.dumps(
            {
                "schema": generator._RUNTIME_IMPORT_PATHS_SCHEMA,
                "paths": [str((tmp_path / "hostile").resolve())],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="changed during execution"):
        generator._validate_runtime_import_control(tmp_path, expected)


def test_generation_commit_marker_publishes_last(monkeypatch, tmp_path):
    generator = _load_generator()
    root = tmp_path / "repo"
    staged = tmp_path / "staged"
    (root / "proof").mkdir(parents=True)
    for relative in generator._artifact_file_paths():
        path = staged / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"staged:{relative}\n".encode())
    published: list[str] = []

    def record_publication(repository, path, payload):
        assert repository == root.resolve()
        assert payload.startswith(b"staged:")
        published.append(path.relative_to(repository).as_posix())

    monkeypatch.setattr(generator, "_atomic_write_bytes", record_publication)
    generator._publish_staged_artifacts(
        root.resolve(),
        staged,
        root / "proof/RESULTS.md",
        root / "proof/results",
    )
    assert set(published) == generator._artifact_file_paths()
    assert published[-1] == generator._GENERATION_FILE


def test_existing_artifact_namespace_rejects_flood_on_first_unknown(
    monkeypatch,
    tmp_path,
):
    generator = _load_generator()
    evidence_dir = tmp_path / "proof/results"
    evidence_dir.mkdir(parents=True)
    observed = 0

    class Flood:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal observed
            observed += 1
            if observed > 1:
                raise AssertionError("namespace validation enumerated beyond first unknown")
            return type("Entry", (), {"name": "unexpected-000000"})()

    monkeypatch.setattr(generator.os, "scandir", lambda _path: Flood())
    with pytest.raises(RuntimeError, match="unexpected stale artifact"):
        generator._validate_existing_artifact_namespace(
            tmp_path,
            tmp_path / "proof/RESULTS.md",
            evidence_dir,
        )
    assert observed == 1


def test_materialized_proof_namespace_rejects_before_reading_unknown(
    monkeypatch,
    tmp_path,
):
    generator = _load_generator()
    (tmp_path / "proof").mkdir()
    observed = 0

    class Flood:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal observed
            observed += 1
            if observed > 1:
                raise AssertionError("materialized proof namespace was fully enumerated")
            return type("Entry", (), {"name": "unexpected-large-source.py"})()

    monkeypatch.setattr(generator.os, "scandir", lambda _path: Flood())
    with pytest.raises(RuntimeError, match="unexpected entry"):
        generator._materialized_proof_tree_records(tmp_path)
    assert observed == 1


def test_existing_artifact_hash_rejects_oversize_before_read(tmp_path):
    generator = _load_generator()
    output = tmp_path / "proof/RESULTS.md"
    output.parent.mkdir(parents=True)
    with output.open("wb") as handle:
        handle.truncate(generator.PROOF_RESULTS_MAX_BYTES + 1)

    with pytest.raises(RuntimeError, match="exceeds its byte limit"):
        generator._validate_existing_artifact_namespace(
            tmp_path,
            output,
            tmp_path / "proof/results",
        )


@pytest.mark.parametrize(
    ("exit_code", "stdout", "expected"),
    [
        (0, b"ok\n", "passed"),
        (2, b"  [SKIP] missing prerequisite\n", "skipped"),
        (0, b"[SKIP] contradictory success\n", "failed"),
        (1, b"traceback mentions [SKIP]\n", "failed"),
        (2, b"missing marker\n", "failed"),
    ],
)
def test_raw_record_uses_exact_skip_exit_contract(
    tmp_path,
    exit_code,
    stdout,
    expected,
):
    generator = _load_generator()
    staged = tmp_path / str(exit_code) / proof_provenance.sha256_bytes(stdout)[:8]
    staged.mkdir(parents=True)
    harness = generator.HARNESSES[0]
    record = generator._raw_record(
        staged,
        harness,
        ("python", harness.script),
        (sys.executable, harness.script),
        generator.CommandOutcome(1.0, 2.0, exit_code, stdout, b""),
    )
    assert record["status"] == expected


def test_bounded_runner_caps_output_and_records_machine_failure(monkeypatch, tmp_path):
    generator = _load_generator()
    monkeypatch.setattr(generator, "PROOF_RAW_STREAM_MAX_BYTES", 64)
    outcome = generator._run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'x' * 65536); sys.stdout.flush()",
        ],
        cwd=tmp_path,
        env=dict(os.environ),
    )
    assert outcome.output_limit_exceeded is True
    assert outcome.timed_out is False
    assert outcome.termination_reason == "output_limit:stdout"
    assert outcome.stdout == b"x" * 64


def test_bounded_runner_times_out_and_kills_process_tree(monkeypatch, tmp_path):
    generator = _load_generator()
    monkeypatch.setattr(generator, "PROOF_HARNESS_TIMEOUT_SECONDS", 0.1)
    started = generator.time.monotonic()
    outcome = generator._run(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        env=dict(os.environ),
    )
    assert generator.time.monotonic() - started < 5
    assert outcome.timed_out is True
    assert outcome.output_limit_exceeded is False
    assert outcome.termination_reason == "timeout"


def test_windows_job_accounting_poll_accepts_transient_nonzero_count(monkeypatch):
    observations = iter((1, None, 0))
    sleeps: list[float] = []
    monkeypatch.setattr(
        proof_provenance,
        "_windows_job_active_processes",
        lambda _job: next(observations),
    )
    monkeypatch.setattr(proof_provenance.time, "sleep", sleeps.append)

    assert proof_provenance._settled_windows_job_active_processes(object()) == 0
    assert len(sleeps) == 2
    assert all(
        0 < duration <= proof_provenance._PROCESS_TREE_POLL_SECONDS
        for duration in sleeps
    )


def test_bounded_runner_fails_when_descendant_survives_parent(tmp_path):
    generator = _load_generator()
    marker = tmp_path / "late-detached-write.txt"
    child = (
        "import sys,time; from pathlib import Path; time.sleep(0.75); "
        "Path(sys.argv[1]).write_text('escaped', encoding='utf-8')"
    )
    script = (
        "import os, subprocess, sys\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}, {str(marker)!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL, "
        f"start_new_session={os.name != 'nt'!r})\n"
    )
    outcome = generator._run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=dict(os.environ),
    )
    assert outcome.exit_code == 0
    assert outcome.termination_reason == "descendant_process_survived"
    time.sleep(1.0)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="suspended-spawn regression is Windows-only")
def test_bounded_runner_assigns_windows_job_before_child_code(
    monkeypatch,
    tmp_path,
):
    generator = _load_generator()
    marker = tmp_path / "root-ran.txt"
    observed: list[bool] = []
    original_attach = proof_provenance.ProcessTreeController._attach_windows

    def inspected_attach(controller, process):
        observed.append(marker.exists())
        return original_attach(controller, process)

    monkeypatch.setattr(
        proof_provenance.ProcessTreeController,
        "_attach_windows",
        inspected_attach,
    )
    outcome = generator._run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')",
            str(marker),
        ],
        cwd=tmp_path,
        env=dict(os.environ),
    )

    assert observed == [False]
    assert outcome.exit_code == 0
    assert outcome.termination_reason is None
    assert marker.read_text(encoding="utf-8") == "ran"


@pytest.mark.skipif(os.name == "nt", reason="dir-fd publication is POSIX-only")
def test_atomic_publication_detects_parent_symlink_swap_without_redirecting(
    monkeypatch,
    tmp_path,
):
    generator = _load_generator()
    root = tmp_path / "repo"
    raw = root / "proof/results/raw"
    moved = root / "proof/results/raw-before-swap"
    outside = tmp_path / "outside"
    raw.mkdir(parents=True)
    outside.mkdir()
    target = raw / "result.txt"
    original_replace = generator.os.replace
    swapped = False

    def swap_then_replace(source, destination, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            raw.rename(moved)
            raw.symlink_to(outside, target_is_directory=True)
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(generator.os, "replace", swap_then_replace)
    with pytest.raises(ValueError, match="symlink or reparse point"):
        generator._atomic_write_bytes(root, target, b"bound output\n")
    assert swapped is True
    assert not (outside / "result.txt").exists()
    assert (moved / "result.txt").read_bytes() == b"bound output\n"
