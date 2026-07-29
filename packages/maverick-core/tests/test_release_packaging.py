from __future__ import annotations

import ast
import gzip
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parents[3]


def _normalise_project_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _requirement_project_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
    assert match is not None, f"cannot parse requirement name: {requirement!r}"
    return _normalise_project_name(match.group())


def _workflow_step(workflow: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    if marker in workflow:
        start = workflow.index(marker)
    else:
        name_line = f"        name: {name}\n"
        name_position = workflow.index(name_line)
        start = workflow.rfind("\n      - ", 0, name_position) + 1
    end = workflow.find("\n      - ", start + len(marker))
    return workflow[start:] if end < 0 else workflow[start:end]


def _continued_shell_command(step: str, first_line: str) -> str:
    lines = step.splitlines()
    start = next(
        index for index, line in enumerate(lines) if line.strip() == first_line
    )
    command = [lines[start].strip()]
    while command[-1].endswith("\\"):
        start += 1
        command.append(lines[start].strip())
    return "\n".join(command)


def _load_sku_builder():
    path = REPO_ROOT / "scripts" / "build_standalone_skus.py"
    spec = importlib.util.spec_from_file_location("build_standalone_skus", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_artifact_normalizer():
    path = REPO_ROOT / "scripts" / "normalize_python_artifacts.py"
    spec = importlib.util.spec_from_file_location(
        "normalize_python_artifacts", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_release_cohort_installer():
    path = REPO_ROOT / "scripts" / "install_release_cohort.py"
    spec = importlib.util.spec_from_file_location(
        "install_release_cohort", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_release_cohort_helper_validates_sources_and_preserves_core_extra():
    installer = _load_release_cohort_installer()
    version, cohort = installer.load_cohort(REPO_ROOT)

    installer.validate_source_metadata(version, cohort, sys.executable)
    installer.validate_core_extra(
        "release-runtime",
        cohort,
        sys.executable,
    )
    requirements = installer._locked_wheel_requirements(
        version,
        cohort,
        "release-runtime",
    ).splitlines()
    assert requirements[0] == (
        f"maverick-agent[release-runtime]=={version}"
    )
    assert len(requirements) == 8
    assert all("==" in requirement for requirement in requirements)


def test_release_cohort_helper_rejects_unknown_extras_and_rootless_subsets():
    installer = _load_release_cohort_installer()
    _version, cohort = installer.load_cohort(REPO_ROOT)

    try:
        installer.validate_core_extra("release-runtmie", cohort, sys.executable)
    except ValueError as exc:
        assert "unknown maverick-agent extra" in str(exc)
    else:
        raise AssertionError("misspelled core extra was accepted")

    try:
        installer.select_packages(cohort, ["maverick-shield"])
    except ValueError as exc:
        assert "must include maverick-agent" in str(exc)
    else:
        raise AssertionError("rootless cohort subset was accepted")


def test_release_cohort_helper_validates_without_site_packages():
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(REPO_ROOT / "scripts" / "install_release_cohort.py"),
            "--source-root",
            str(REPO_ROOT),
            "--target-python",
            sys.executable,
            "--validate-only",
            "--core-extra",
            "release-runtime",
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "validated 8 Lightwork cohort source projects" in result.stdout


def test_standalone_sku_archives_are_deterministic_and_self_describing(tmp_path):
    builder = _load_sku_builder()
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first = builder.build("0.1.7", "0123456789abcdef", first_dir)
    second = builder.build("v0.1.7", "0123456789abcdef", second_dir)

    assert [path.name for path in first] == [path.name for path in second]
    assert len(first) == 6
    for left, right in zip(first, second, strict=True):
        assert left.read_bytes() == right.read_bytes()

    archives = sorted(first_dir.glob("*.zip"))
    assert len(archives) == 4
    for archive_path in archives:
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            assert len({name.split("/", 1)[0] for name in names}) == 1
            assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
            metadata_name = next(name for name in names if name.endswith("/RELEASE-METADATA.json"))
            metadata = json.loads(archive.read(metadata_name))
            assert metadata["artifact_type"] == "standalone-source-archive"
            assert metadata["pypi_distribution"] is False
            assert metadata["source_revision"] == "0123456789abcdef"
            assert metadata["version"] == "0.1.7"
            assert next(name for name in names if name.endswith("/LICENSE"))

    bom = json.loads((first_dir / "lightwork-standalone-skus-0.1.7.cdx.json").read_text())
    assert bom["bomFormat"] == "CycloneDX"
    assert bom["specVersion"] == "1.6"
    assert len([item for item in bom["components"] if item["type"] == "application"]) == 4

    checksum_lines = (first_dir / "lightwork-standalone-skus-0.1.7.sha256").read_text().splitlines()
    assert len(checksum_lines) == 5
    for line in checksum_lines:
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((first_dir / name).read_bytes()).hexdigest() == digest


def test_python_release_archive_normalizer_removes_wall_clock_metadata(tmp_path):
    normalizer = _load_artifact_normalizer()
    wheels = [tmp_path / f"build-{index}.whl" for index in (1, 2)]
    sdists = [tmp_path / f"build-{index}.tar.gz" for index in (1, 2)]

    for index, wheel in enumerate(wheels, start=1):
        with zipfile.ZipFile(wheel, "w") as archive:
            info = zipfile.ZipInfo(
                "demo/__init__.py",
                (2026, 7, 29, 12, 0, index * 2),
            )
            info.external_attr = 0o100644 << 16
            archive.writestr(info, b"VALUE = 1\n")
    for index, sdist in enumerate(sdists, start=1):
        with sdist.open("wb") as raw:
            with gzip.GzipFile(
                filename=sdist.name,
                mode="wb",
                fileobj=raw,
                mtime=1_700_000_000 + index,
            ) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    info = tarfile.TarInfo("demo-1.0/demo.py")
                    info.size = len(b"VALUE = 1\n")
                    info.mtime = 1_700_000_000 + index
                    info.uid = index
                    info.uname = f"builder-{index}"
                    archive.addfile(info, io.BytesIO(b"VALUE = 1\n"))

    for artifact in [*wheels, *sdists]:
        normalizer.normalize(artifact, epoch=1_700_000_000)

    assert wheels[0].read_bytes() == wheels[1].read_bytes()
    assert sdists[0].read_bytes() == sdists[1].read_bytes()
    with tarfile.open(sdists[0], "r:gz") as archive:
        member = archive.getmember("demo-1.0/demo.py")
        assert member.mtime == 1_700_000_000
        assert member.uid == member.gid == 0
        assert member.uname == member.gname == ""


def test_pyinstaller_collects_release_runtime_and_metadata():
    spec_path = REPO_ROOT / "build" / "maverick.spec"
    source = spec_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    collected = {
        call.args[0].value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "collect_data_files"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    }
    assert {"maverick", "maverick_dashboard", "maverick_shield"} <= collected
    shield_metadata = tomllib.loads(
        (
            REPO_ROOT / "packages" / "maverick-shield" / "pyproject.toml"
        ).read_text(encoding="utf-8")
    )
    assert shield_metadata["tool"]["setuptools"]["package-data"][
        "maverick_shield"
    ] == ["redteam_corpus.jsonl"]

    submodules = {
        call.args[0].value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "collect_submodules"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    }
    metadata_distributions = {
        item.value
        for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_dist"
        and isinstance(node.iter, ast.Tuple)
        for item in node.iter.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    }
    cohort = tomllib.loads(
        (REPO_ROOT / "release-cohort.toml").read_text(encoding="utf-8")
    )
    assert {item["runtime_module"] for item in cohort["packages"]} <= submodules
    assert {item["distribution"] for item in cohort["packages"]} <= (
        metadata_distributions
    )
    assert "pywhispercpp" in submodules
    assert "pywhispercpp" in metadata_distributions
    assert "openai" in submodules
    assert "openai" in metadata_distributions
    assert "'_pywhispercpp'" in source


def test_core_wheel_includes_tracked_pricing_evidence():
    core_root = REPO_ROOT / "packages" / "maverick-core"
    metadata = tomllib.loads(
        (core_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    package_data = metadata["tool"]["setuptools"]["package-data"]["maverick"]
    assert "data/*.json" in package_data

    evidence_path = (
        core_root
        / "maverick"
        / "data"
        / "pricing-rate-card-2026-07-29.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["schema_version"] == 1
    assert evidence["rate_card_version"] == "2026-07-29.2"


def _literal_source_version(path: Path) -> str:
    """Return the source-tree fallback without importing package side effects."""
    versions = {
        node.value.value
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "__version__"
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    assert len(versions) == 1, f"{path} must declare one literal source version"
    return versions.pop()


def test_public_python_release_cohort_is_complete_and_lockstep():
    cohort = tomllib.loads(
        (REPO_ROOT / "release-cohort.toml").read_text(encoding="utf-8")
    )
    release_version = cohort["version"]
    packages = cohort["packages"]

    assert cohort["schema_version"] == 1
    assert release_version == "0.1.7"
    assert cohort["repository"] == "Daybreak-AI-Labs/Lightwork"
    assert len(packages) == 8
    assert len({item["distribution"] for item in packages}) == len(packages)
    assert len({item["path"] for item in packages}) == len(packages)

    workspace = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert set(workspace["tool"]["uv"]["workspace"]["members"]) == {
        item["path"] for item in packages
    }
    assert set(workspace["tool"]["uv"]["sources"]) == {
        item["distribution"] for item in packages
    }
    assert all(
        source == {"workspace": True}
        for source in workspace["tool"]["uv"]["sources"].values()
    )

    cohort_names = {item["distribution"] for item in packages}
    version_command = (
        REPO_ROOT
        / "packages"
        / "maverick-core"
        / "maverick"
        / "cli"
        / "__init__.py"
    ).read_text(encoding="utf-8")
    assert all(f'"{name}"' in version_command for name in cohort_names)
    canonical_homepage = f"https://github.com/{cohort['repository']}"
    for item in packages:
        package_root = REPO_ROOT / item["path"]
        metadata = tomllib.loads(
            (package_root / "pyproject.toml").read_text(encoding="utf-8")
        )
        project = metadata["project"]
        assert project["name"] == item["distribution"]
        assert project["version"] == release_version
        assert project["license"] == "LicenseRef-Proprietary"
        assert project["urls"]["Homepage"] == canonical_homepage
        assert metadata["build-system"]["requires"] == ["setuptools==83.0.0"]

        requirements = list(project.get("dependencies", []))
        for extra_requirements in project.get("optional-dependencies", {}).values():
            requirements.extend(extra_requirements)
        for sibling in cohort_names - {item["distribution"]}:
            matching = [req for req in requirements if req.split("[", 1)[0].startswith(sibling)]
            assert all(f">={release_version}" in req for req in matching), matching

        init_path = package_root / item["runtime_module"] / "__init__.py"
        assert _literal_source_version(init_path) == release_version

    core = tomllib.loads(
        (
            REPO_ROOT / "packages" / "maverick-core" / "pyproject.toml"
        ).read_text(encoding="utf-8")
    )["project"]
    core_extras = core["optional-dependencies"]
    expected_siblings = {
        f"{name}>={release_version}"
        for name in cohort_names - {"maverick-agent"}
    }
    assert expected_siblings <= set(core_extras["all"])
    assert core_extras["evolve"] == [f"maverick-evolve>={release_version}"]
    assert core_extras["knowledge"] == [
        f"maverick-knowledge>={release_version}"
    ]
    assert core_extras["release-runtime"] == ["openai>=1.30"]


def test_ci_constraints_cover_every_release_base_dependency():
    cohort = tomllib.loads(
        (REPO_ROOT / "release-cohort.toml").read_text(encoding="utf-8")
    )["packages"]
    cohort_names = {
        _normalise_project_name(item["distribution"]) for item in cohort
    }
    external_requirements: dict[str, list[str]] = {}
    for item in cohort:
        project = tomllib.loads(
            (REPO_ROOT / item["path"] / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        for requirement in project.get("dependencies", []):
            name = _requirement_project_name(requirement)
            if name not in cohort_names:
                external_requirements.setdefault(name, []).append(
                    f"{item['distribution']}: {requirement}"
                )

    requirement_lines = [
        line.strip()
        for line in (REPO_ROOT / "requirements" / "ci.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert all(line.count("==") == 1 for line in requirement_lines)
    constraints = {
        _normalise_project_name(line.partition("==")[0]): line.partition("==")[2]
        for line in requirement_lines
    }
    missing = {
        name: requirements
        for name, requirements in external_requirements.items()
        if name not in constraints
    }
    assert not missing, f"unconstrained release base dependencies: {missing}"
    assert constraints["pywhispercpp"] == "1.5.0"
    assert constraints["openai"] == "2.46.0"
    assert constraints["tqdm"] == "4.67.1"

    dashboard = tomllib.loads(
        (
            REPO_ROOT / "packages" / "maverick-dashboard" / "pyproject.toml"
        ).read_text(encoding="utf-8")
    )["project"]
    assert "pywhispercpp>=1.5" in dashboard["dependencies"]


def test_product_release_manifests_share_the_python_cohort_version():
    release_version = tomllib.loads(
        (REPO_ROOT / "release-cohort.toml").read_text(encoding="utf-8")
    )["version"]

    installer_package = json.loads(
        (REPO_ROOT / "apps" / "installer-desktop" / "package.json").read_text(
            encoding="utf-8"
        )
    )
    installer_tauri = json.loads(
        (
            REPO_ROOT
            / "apps"
            / "installer-desktop"
            / "src-tauri"
            / "tauri.conf.json"
        ).read_text(encoding="utf-8")
    )
    installer_cargo = tomllib.loads(
        (
            REPO_ROOT / "apps" / "installer-desktop" / "src-tauri" / "Cargo.toml"
        ).read_text(encoding="utf-8")
    )
    desktop_tauri = json.loads(
        (REPO_ROOT / "apps" / "desktop" / "src-tauri" / "tauri.conf.json").read_text(
            encoding="utf-8"
        )
    )
    desktop_cargo = tomllib.loads(
        (REPO_ROOT / "apps" / "desktop" / "src-tauri" / "Cargo.toml").read_text(
            encoding="utf-8"
        )
    )
    helm_chart = (REPO_ROOT / "deploy" / "helm" / "maverick" / "Chart.yaml").read_text(
        encoding="utf-8"
    )

    assert installer_package["version"] == release_version
    assert installer_tauri["version"] == release_version
    assert installer_cargo["package"]["version"] == release_version
    assert desktop_tauri["version"] == release_version
    assert desktop_cargo["package"]["version"] == release_version
    assert f'appVersion: "{release_version}"' in helm_chart

    # Native packaging must consume the cohort manifest rather than carrying a
    # fourth independent default that silently produces a differently-versioned
    # MSI or macOS bundle after the next release bump.
    msi_workflow = (
        REPO_ROOT / ".github" / "workflows" / "build-msi.yml"
    ).read_text(encoding="utf-8")
    msi_build = (
        REPO_ROOT / "apps" / "installer-msi" / "build.ps1"
    ).read_text(encoding="utf-8")
    msi_wix = (
        REPO_ROOT / "apps" / "installer-msi" / "Package.wxs"
    ).read_text(encoding="utf-8")
    macos_build = (
        REPO_ROOT / "scripts" / "build-macos-app.sh"
    ).read_text(encoding="utf-8")

    assert "release-cohort.toml" in msi_workflow
    assert "steps.cohort.outputs.version" in msi_workflow
    assert "inputs.version" not in msi_workflow
    assert "release-cohort.toml" in msi_build
    assert '[string]$Version = ""' in msi_build
    assert "<?error ProductVersion is required" in msi_wix
    assert '<?define ProductVersion = "' not in msi_wix
    assert "release-cohort.toml" in macos_build
    assert "<string>$VERSION</string>" in macos_build


def test_standalone_skus_are_github_source_assets_not_pypi_packages():
    release_workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    publish_workflow = (REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text(
        encoding="utf-8"
    )
    assert "python scripts/build_standalone_skus.py" in release_workflow
    assert "dist/lightwork-*" in release_workflow
    assert "git checkout --detach" in release_workflow
    assert "cyclonedx-bom==7.3.1" in release_workflow
    assert "SBOM generation failed (non-fatal)" not in release_workflow
    assert 'target_tags+=("$CONTAINER_IMAGE:latest")' in release_workflow
    assert "startsWith(github.ref, 'refs/tags/v')" in publish_workflow
    assert "attempt $i/180" in publish_workflow
    for sku in (
        "grc-concierge",
        "platform-threat-hunter",
        "environment-threat-hunter",
        "model-risk-ai-assurance-officer",
    ):
        assert f"demo/{sku}" not in publish_workflow


def test_release_cohort_dependency_environments_are_complete():
    cohort = tomllib.loads(
        (REPO_ROOT / "release-cohort.toml").read_text(encoding="utf-8")
    )["packages"]
    paths = [item["path"] for item in cohort]
    release = (
        REPO_ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")

    binary_step = _workflow_step(release, "Install Lightwork + deps")
    assert "python scripts/install_release_cohort.py \\" in binary_step
    assert "--core-extra release-runtime" in binary_step
    assert "--no-deps" not in binary_step
    assert " -e " not in binary_step
    assert "python -m pip check" in binary_step
    assert "verify_release_runtime" in binary_step
    assert "version('pywhispercpp') == '1.5.0'" in binary_step

    smoke_step = _workflow_step(
        release, "Smoke-test the binary covers all subcommands"
    )
    assert "./dist/maverick mcp --help" in smoke_step
    assert "mcp --help || true" not in smoke_step
    assert "./dist/maverick voice status" in smoke_step
    assert 'grep -F "pywhispercpp engine (installed)"' in smoke_step

    sbom_step = _workflow_step(
        release, "Generate CycloneDX SBOM for the release"
    )
    assert "python scripts/install_release_cohort.py \\" in sbom_step
    assert '--target-python "$SBOM_ENV/bin/python"' in sbom_step
    assert "--core-extra release-runtime" in sbom_step
    assert "--no-deps" not in sbom_step
    assert " -e " not in sbom_step
    assert '"$SBOM_ENV/bin/maverick" release-runtime-check' in sbom_step
    assert 'cyclonedx-py environment "$SBOM_ENV/bin/python"' in sbom_step
    assert "--pyproject packages/maverick-core/pyproject.toml" in sbom_step

    agent_workflow = (
        REPO_ROOT / ".github" / "workflows" / "agent-on-pr.yml"
    ).read_text(encoding="utf-8")
    agent_step = _workflow_step(agent_workflow, "Install Lightwork")
    assert agent_step.count("python -m pip install") == 1
    assert all(f'$source_root/{path}' in agent_step for path in paths)
    assert '--constraint "$source_root/requirements/ci.txt"' in agent_step
    assert "python -m pip check" in agent_step
    assert "--no-deps" not in agent_step

    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "pip install --no-deps" not in ci
    assert ci.count("python -m pip check") >= 3


def test_release_verifier_accepts_canonical_and_historical_signers():
    verifier = (REPO_ROOT / "deploy" / "verify-release.sh").read_text(
        encoding="utf-8"
    )
    identity_assignment = next(
        line for line in verifier.splitlines() if line.startswith("IDENTITY_REGEXP=")
    )
    template = identity_assignment.partition("=")[2]
    assert template.startswith('"') and template.endswith('"')
    pattern = template[1:-1].replace("${ESCAPED_TAG}", re.escape("v1.2.3"))

    assert re.fullmatch(
        pattern,
        "https://github.com/Daybreak-AI-Labs/Lightwork/"
        ".github/workflows/release.yml@refs/tags/v1.2.3",
    )
    assert re.fullmatch(
        pattern,
        "https://github.com/Day-AI-Labs/Lightwork/"
        ".github/workflows/release.yml@refs/tags/v1.2.3",
    )
    assert re.fullmatch(
        pattern,
        "https://github.com/Day-AI-Labs/Maverick/"
        ".github/workflows/release.yml@refs/tags/v1.2.3",
    )
    assert not re.fullmatch(
        pattern,
        "https://github.com/Daybreak-AI-Labs/Maverick/"
        ".github/workflows/release.yml@refs/tags/v1.2.3",
    )
    assert not re.fullmatch(
        pattern,
        "https://github.com/attacker/Lightwork/"
        ".github/workflows/release.yml@refs/tags/v1.2.3",
    )
    assert not re.fullmatch(
        pattern,
        "https://github.com/Daybreak-AI-Labs/Lightwork/"
        ".github/workflows/release.yml@refs/heads/main",
    )


def test_manual_release_signing_requires_the_exact_tag_run_identity():
    release = (
        REPO_ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")
    validation = _workflow_step(release, "Validate release tag")

    assert "EVENT_REF: ${{ github.ref }}" in validation
    assert 'if [[ "$EVENT_NAME" == "workflow_dispatch" ]]' in validation
    assert 'expected_ref="refs/tags/$tag"' in validation
    assert '[[ "$EVENT_REF" != "$expected_ref" ]]' in validation
    assert '[[ "$EVENT_SHA" != "$tag_sha" ]]' in validation
    assert "Manual release must run from $expected_ref" in validation
    distribution = (REPO_ROOT / "docs" / "DISTRIBUTION.md").read_text(
        encoding="utf-8"
    )
    assert (
        "gh workflow run release.yml --ref v0.1.7 -f tag=v0.1.7"
        in distribution
    )


def test_pypi_release_builds_are_commit_anchored_and_byte_reproducible():
    publish = (
        REPO_ROOT / ".github" / "workflows" / "publish.yml"
    ).read_text(encoding="utf-8")
    build_step = _workflow_step(
        publish, "Validate the tag and build the complete release cohort"
    )

    assert 'export SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"' in build_step
    assert "export PYTHONHASHSEED=0" in build_step
    assert "lightwork-publish-source-a" in build_step
    assert "lightwork-publish-source-b" in build_step
    assert build_step.count("python -m build --outdir") == 2
    assert "if first != second:" in build_step
    assert "release artifacts are not reproducible from this commit" in build_step

    # Resume remains fail-closed: determinism supports the exact digest
    # comparison; it does not replace or weaken it.
    preflight = _workflow_step(
        publish, "Preflight every distribution and existing PyPI release"
    )
    assert "if existing != expected:" in preflight
    assert "PyPI {name}=={version} differs from this build" in preflight


def test_native_source_bootstrap_is_not_advertised_as_a_product_installer():
    release = (
        REPO_ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")
    desktop = (
        REPO_ROOT / ".github" / "workflows" / "desktop.yml"
    ).read_text(encoding="utf-8")
    distribution = (REPO_ROOT / "docs" / "DISTRIBUTION.md").read_text(
        encoding="utf-8"
    )
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    app = (
        REPO_ROOT / "apps" / "installer-desktop" / "src" / "App.svelte"
    ).read_text(encoding="utf-8")
    bootstrap_readme = (
        REPO_ROOT / "apps" / "installer-desktop" / "README.md"
    ).read_text(encoding="utf-8")
    deployment = (
        REPO_ROOT / "docs" / "deployment.md"
    ).read_text(encoding="utf-8")
    press = (
        REPO_ROOT / "docs" / "press-kit.md"
    ).read_text(encoding="utf-8")
    features = (
        REPO_ROOT / "docs" / "FEATURES.md"
    ).read_text(encoding="utf-8")

    assert "gh workflow run desktop.yml" not in release
    assert "attach-release:" not in desktop
    assert "if-no-files-found: error" in desktop
    assert "without a GitHub login" not in desktop
    assert "authenticated source-bootstrap" in desktop
    assert "Install from authorized source" in app
    assert "private Lightwork repository" in app
    assert "not a self-contained native installer" in bootstrap_readme
    assert "| **Native installer**" not in distribution
    assert "| **Container image**" in distribution
    assert "**Yes** — installed Python remains readable" in distribution
    assert "four reduced standalone SKUs" in distribution
    assert "native installers, GHCR" not in readme
    assert "Tauri-based GUI installer for users" not in deployment
    assert "not attached to product releases" in deployment
    assert "native double-click installers for Windows" not in press
    assert "self-contained, platform-signed native installer is not" in press
    assert "linux/amd64 and linux/arm64" in features
    assert "arm64 + riscv64" not in features


def test_tauri_binary_apps_use_tracked_locks_and_a_pinned_toolchain():
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for relative in (
        "apps/desktop/src-tauri/Cargo.lock",
        "apps/installer-desktop/src-tauri/Cargo.lock",
    ):
        lock = REPO_ROOT / relative
        assert lock.is_file()
        assert lock.read_text(encoding="utf-8").startswith(
            "# This file is automatically @generated by Cargo."
        )
        assert f"!{relative}" in gitignore

    desktop = (
        REPO_ROOT / ".github" / "workflows" / "desktop.yml"
    ).read_text(encoding="utf-8")
    assert 'toolchain: "1.97.1"' in desktop
    assert "src-tauri/Cargo.lock" in desktop
    assert "pnpm tauri build -- --locked" in desktop


def test_public_site_contains_the_commit_bound_governance_benchmark():
    manifest = (REPO_ROOT / "docs" / "public-docs.txt").read_text(
        encoding="utf-8"
    )
    public_config = (REPO_ROOT / "mkdocs.public.yml").read_text(
        encoding="utf-8"
    )
    docs_workflow = (
        REPO_ROOT / ".github" / "workflows" / "docs.yml"
    ).read_text(encoding="utf-8")
    gateway = (
        REPO_ROOT / "docs" / "AI_EVIDENCE_GATEWAY.md"
    ).read_text(encoding="utf-8")
    report = (
        REPO_ROOT / "benchmarks" / "GOVERNANCE_FRONTIER_RESULTS.md"
    ).read_text(encoding="utf-8")

    mapping = (
        "@repo/benchmarks/GOVERNANCE_FRONTIER_RESULTS.md"
        "|benchmarks/governance-frontier.md"
    )
    assert mapping in manifest
    evidence_root = "benchmarks/results/governance-frontier-v1"
    for filename in (
        "measured-manifest.json",
        "trusted-publisher.pub",
        "verify_manifest.py",
    ):
        exact_mapping = (
            f"@repo/{evidence_root}/{filename}|"
            f"{evidence_root}/{filename}"
        )
        assert exact_mapping in manifest
        assert filename in docs_workflow
    assert "Governance Frontier benchmark: benchmarks/governance-frontier.md" in (
        public_config
    )
    assert "./benchmarks/governance-frontier.md" in gateway
    assert "source_name.startswith(\"@repo/\")" in docs_workflow
    assert "benchmarks/GOVERNANCE_FRONTIER_RESULTS.md" in docs_workflow
    assert re.search(r"Git commit: `[0-9a-f]{40}`", report)
    assert re.search(r"Input digest: `[0-9a-f]{64}`", report)
    manifest_bytes = (
        REPO_ROOT / evidence_root / "measured-manifest.json"
    ).read_bytes().replace(b"\r\n", b"\n")
    key_bytes = (
        REPO_ROOT / evidence_root / "trusted-publisher.pub"
    ).read_bytes().replace(b"\r\n", b"\n")
    raw_key = bytes.fromhex(key_bytes.decode("ascii").strip())
    for digest in (
        hashlib.sha256(manifest_bytes).hexdigest(),
        hashlib.sha256(key_bytes).hexdigest(),
        hashlib.sha256(raw_key).hexdigest(),
    ):
        assert f"`{digest}`" in report
    assert "Source-binding verification requires a licensed Lightwork source" in (
        report
    )
    assert "reviewed_non_markdown" in docs_workflow


def test_cloud_quickstarts_and_reference_probes_match_the_runtime_contract():
    distribution = (REPO_ROOT / "docs" / "DISTRIBUTION.md").read_text(
        encoding="utf-8"
    )
    mac_builder = (
        REPO_ROOT / "scripts" / "build-macos-app.sh"
    ).read_text(encoding="utf-8")
    architectures = (
        REPO_ROOT / "docs" / "reference-architectures.md"
    ).read_text(encoding="utf-8")
    fly = (
        REPO_ROOT / "deploy" / "reference-architectures" / "flyio" / "fly.toml"
    ).read_text(encoding="utf-8")

    assert "8000" not in distribution
    assert "8000" not in mac_builder
    assert "--target-port 8765" in distribution
    assert "--command maverick" in distribution
    assert "--args dashboard --host 0.0.0.0 --port 8765" in distribution
    assert "--command=maverick" in distribution
    assert "--args=dashboard,--host,0.0.0.0,--port,8765" in distribution
    assert "dashboard --host 127.0.0.1 --port 8765" in mac_builder
    assert "/readyz" in architectures and "/livez" in architectures
    assert "keep one web/control-plane replica" in architectures
    assert 'path = "/readyz"' in fly


def test_release_container_namespace_matches_the_product_cohort():
    cohort = tomllib.loads(
        (REPO_ROOT / "release-cohort.toml").read_text(encoding="utf-8")
    )
    repository = cohort["repository"].casefold()
    owner, product_slug = repository.split("/", 1)
    canonical_image = f"ghcr.io/{owner}/{product_slug}"

    release_workflow = (
        REPO_ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")
    helm_values = (
        REPO_ROOT / "deploy" / "helm" / "maverick" / "values.yaml"
    ).read_text(encoding="utf-8")
    deployment_docs = (
        REPO_ROOT / "docs" / "deployment.md"
    ).read_text(encoding="utf-8")
    distribution_docs = (
        REPO_ROOT / "docs" / "DISTRIBUTION.md"
    ).read_text(encoding="utf-8")

    workflow_image = (
        'image="ghcr.io/${GITHUB_REPOSITORY_OWNER,,}/'
        f'{product_slug}"'
    )
    assert workflow_image in release_workflow
    assert "GITHUB_REPOSITORY_OWNER,,}/maverick" not in release_workflow
    assert f"repository: {canonical_image}" in helm_values
    assert canonical_image in deployment_docs
    assert canonical_image in distribution_docs


def test_customer_surfaces_do_not_reference_the_retired_repository_owner():
    """Keep public docs and active delivery paths on the canonical repository."""
    public_sources: list[Path] = []
    for raw in (REPO_ROOT / "docs" / "public-docs.txt").read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        source_name = line.partition("|")[0]
        if source_name.startswith("@repo/"):
            public_sources.append(
                REPO_ROOT / source_name.removeprefix("@repo/")
            )
        else:
            public_sources.append(REPO_ROOT / "docs" / source_name)

    checked = {
        REPO_ROOT / path
        for path in (
            ".github/ISSUE_TEMPLATE/config.yml",
            ".github/workflows/agent-on-pr.yml",
            "ARCHITECTURE.md",
            "CONTRIBUTING.md",
            "LICENSE",
            "MAINTAINERS.md",
            "README.md",
            "SECURITY.md",
            "TRADEMARK.md",
            "packages/maverick-core/maverick/a2a.py",
            "packages/maverick-core/maverick/issue_report.py",
            "packages/maverick-core/maverick/tools/geocode.py",
            "packages/maverick-core/maverick/tools/reddit_tool.py",
            "packages/maverick-core/maverick/tools/wikipedia.py",
            "packages/maverick-mcp/maverick_mcp/publish.py",
            "scripts/build_standalone_skus.py",
            "scripts/train_runpod.sh",
            "web/README.md",
            "web/index.html",
        )
    }
    checked.update(public_sources)
    checked.update(
        path
        for root in (REPO_ROOT / "apps", REPO_ROOT / "deploy")
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower()
        in {
            ".el",
            ".html",
            ".json",
            ".md",
            ".ps1",
            ".rb",
            ".rs",
            ".sh",
            ".toml",
            ".wxs",
            ".yaml",
            ".yml",
        }
    )
    # The verifier is the sole intentional compatibility boundary: old signed
    # releases must remain verifiable after the organization rename.
    checked.discard(REPO_ROOT / "deploy" / "verify-release.sh")

    cohort = tomllib.loads((REPO_ROOT / "release-cohort.toml").read_text(encoding="utf-8"))
    checked.update(
        REPO_ROOT / item["path"] / "pyproject.toml"
        for item in cohort["packages"]
    )
    mcp_publish = (
        REPO_ROOT / "packages" / "maverick-mcp" / "maverick_mcp" / "publish.py"
    ).read_text(encoding="utf-8")
    assert (
        'DEFAULT_REPO_URL = "https://github.com/Daybreak-AI-Labs/Lightwork"'
        in mcp_publish
    )

    legacy_hits = {
        path.relative_to(REPO_ROOT).as_posix(): [
            line_number
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(),
                start=1,
            )
            if "day-ai-labs" in line.casefold()
        ]
        for path in checked
        if "day-ai-labs"
        in path.read_text(encoding="utf-8", errors="replace").casefold()
    }
    assert not legacy_hits, legacy_hits


def test_ci_posture_tools_are_exactly_pinned_and_secret_scan_uses_module():
    requirement_lines = [
        line.strip()
        for line in (REPO_ROOT / "requirements" / "ci.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert all(line.count("==") == 1 for line in requirement_lines)
    constraints = {
        line.partition("==")[0]: line.partition("==")[2]
        for line in requirement_lines
    }
    assert len(constraints) == len(requirement_lines), "duplicate CI constraint"
    expected = {
        "bandit": "1.9.4",
        "cyclonedx-bom": "7.3.1",
        "detect-secrets": "1.5.0",
        "pip-audit": "2.10.0",
        "psycopg": "3.3.4",
        "ruff": "0.15.15",
        "vulture": "2.16",
    }
    assert {name: constraints.get(name) for name in expected} == expected

    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    for name, version in expected.items():
        assert f"{name}=={version}" in workflow
        assert f"{name}>=" not in workflow
    assert "python -m detect_secrets scan --baseline" in workflow


def test_ci_constraints_satisfy_shipped_network_security_floors():
    constraints = (
        REPO_ROOT / "requirements" / "ci.txt"
    ).read_text(encoding="utf-8")
    docs_constraints = (
        REPO_ROOT / "requirements" / "docs.txt"
    ).read_text(encoding="utf-8")
    core_manifest = (
        REPO_ROOT / "packages" / "maverick-core" / "pyproject.toml"
    ).read_text(encoding="utf-8")
    channels_manifest = (
        REPO_ROOT / "packages" / "maverick-channels" / "pyproject.toml"
    ).read_text(encoding="utf-8")

    for pinned in ("requests==2.34.2", "urllib3==2.7.0"):
        assert pinned in constraints
        assert pinned in docs_constraints
    for manifest in (core_manifest, channels_manifest):
        assert "requests>=2.33.0" in manifest
        assert "urllib3>=2.7.0" in manifest


def test_release_delivery_toolchains_and_artifacts_are_deterministic():
    release = (
        REPO_ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")
    publish = (
        REPO_ROOT / ".github" / "workflows" / "publish.yml"
    ).read_text(encoding="utf-8")
    msi = (
        REPO_ROOT / ".github" / "workflows" / "build-msi.yml"
    ).read_text(encoding="utf-8")

    for workflow in (release, publish, msi):
        assert "PIP_CONSTRAINT: ${{ github.workspace }}/requirements/ci.txt" in workflow
        checkout_count = workflow.count("uses: actions/checkout@")
        assert workflow.count("persist-credentials: false") == checkout_count
        assert "runs-on: ubuntu-latest" not in workflow
        assert "runs-on: windows-latest" not in workflow

    assert "pyinstaller==6.21.0" in release
    assert "pyinstaller>=" not in release
    constraints = (
        REPO_ROOT / "requirements" / "ci.txt"
    ).read_text(encoding="utf-8")
    for pinned_helper in (
        "altgraph==0.17.5",
        "macholib==1.16.4",
        "pefile==2024.8.26",
        "pyinstaller-hooks-contrib==2026.6",
        "pywin32-ctypes==0.2.3",
    ):
        assert pinned_helper in constraints
    assert "provenance: mode=max" in release
    assert "sbom: true" in release
    assert "PYTHON_DIGEST=@sha256:" in release
    assert "Generate a complete release checksum manifest" in release
    assert "sha256sum --check" in release
    assert "fail_on_unmatched_files: true" in release

    assert "dotnet tool install --global wix --version 4.0.6" in msi
    assert "Expected exactly one maverick-agent wheel" in msi
    assert "Generate MSI checksum" in msi
    assert "apps/installer-msi/dist/SHA256SUMS" in msi
    assert "apps/installer-msi/dist/BUILD-METADATA.json" in msi
    assert 'source_revision = "${{ github.sha }}"' in msi

    # The full cohort is validated before any matrix job receives an OIDC
    # token. Safe resume compares already-published filenames and hashes rather
    # than allowing skip-existing to mask unrelated bytes.
    assert "Preflight every distribution and existing PyPI release" in publish
    assert "PyPI {name}=={version} differs from this build" in publish
    assert "(cd dist-all && sha256sum --check SHA256SUMS)" in publish
    assert "scripts/normalize_python_artifacts.py" in publish
    assert "dist-first/*.whl dist-first/*.tar.gz" in publish
    assert "dist-second/*.whl dist-second/*.tar.gz" in publish
    assert "attestations: true" in publish
    assert "Fail if signatures were not attached to the release" in publish
    assert "release-manifest.json" in publish
    assert 'gh release upload "$TAG" \\' in publish
    assert "dist/SHA256SUMS" in publish
    assert "dist/release-manifest.json" in publish
    assert "verify-published:" in publish
    assert "needs: [build, publish]" in publish
    assert "Require exact final filenames and SHA-256 digests on PyPI" in publish
    assert "outside the release manifest" in publish
    assert "PyPI cohort incomplete after final retry" in publish

    docker_job = release.split("\n  docker-image:", 1)[1].split(
        "\n  binaries:", 1
    )[0]
    final_release_job = release.split("\n  release:", 1)[1]
    assert ":sha-$GITHUB_SHA" in docker_job
    assert ":latest" not in docker_job
    assert "steps.build.outputs.digest" in docker_job
    draft_position = final_release_job.index(
        "Create the signed release as a draft"
    )
    promote_position = final_release_job.index(
        "Promote and verify the signed container digest"
    )
    publish_position = final_release_job.index(
        "Publish the verified draft release"
    )
    assert draft_position < promote_position < publish_position
    assert "draft: true" in final_release_job
    assert 'gh "${args[@]}"' in final_release_job
    assert "$CONTAINER_IMAGE:latest" in final_release_job
    assert "needs: [validate-release-tag, docker-image, binaries" in release
    assert "cosign sign --yes" in final_release_job
    assert '"$CONTAINER_IMAGE@$CONTAINER_DIGEST"' in final_release_job
    assert "maverick-container-" in final_release_job

    dockerfile = (
        REPO_ROOT / "deploy" / "docker" / "Dockerfile"
    ).read_text(encoding="utf-8")
    assert "COPY requirements/ci.txt ./requirements/ci.txt" in dockerfile
    assert " AS wheel-builder" in dockerfile
    assert " AS runtime" in dockerfile
    assert "pip==26.1.2" in dockerfile
    assert dockerfile.count("--constraint /tmp/requirements/ci.txt") == 1
    assert "--core-extra release-runtime" in dockerfile
    assert "--no-index" in dockerfile
    assert "cohort-requirements.txt" in dockerfile
    assert "maverick release-runtime-check" in dockerfile
    cohort = tomllib.loads(
        (REPO_ROOT / "release-cohort.toml").read_text(encoding="utf-8")
    )
    for item in cohort["packages"]:
        assert f"COPY {item['path']} ./{item['path']}" in dockerfile
        assert f"./{item['path']}" in dockerfile
    assert "python -m pip check" in dockerfile
    assert "HEALTHCHECK" not in dockerfile

    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert dockerignore.splitlines()[0] == "**"
    assert "!requirements/ci.txt" in dockerignore
    for protected_pattern in (
        "**/.git/**",
        "**/.env.*",
        "**/.maverick/**",
        "**/*.key",
        "**/*.pem",
        "**/*.sqlite",
        "**/*.db",
    ):
        assert protected_pattern in dockerignore
    for item in cohort["packages"]:
        assert f"!{item['path']}/" in dockerignore
        assert f"!{item['path']}/**" in dockerignore

    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert '"import maverick_evolve, maverick_knowledge"' in ci


def test_ci_and_docs_workflows_keep_write_tokens_away_from_untrusted_builds():
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    docs = (
        REPO_ROOT / ".github" / "workflows" / "docs.yml"
    ).read_text(encoding="utf-8")

    assert re.search(r"(?m)^permissions:\n  contents: read\n", ci)
    assert ci.count("persist-credentials: false") == ci.count(
        "uses: actions/checkout@"
    )

    top_level_docs = docs.split("jobs:", 1)[0]
    assert "pages: write" not in top_level_docs
    assert "id-token: write" not in top_level_docs
    deploy = docs.split("\n  deploy:", 1)[1]
    assert "pages: write" in deploy
    assert "id-token: write" in deploy
    assert docs.count("persist-credentials: false") == docs.count(
        "uses: actions/checkout@"
    )


def test_documentation_dependency_lock_is_exact_and_complete():
    requirements = [
        line.strip()
        for line in (REPO_ROOT / "requirements" / "docs.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    names = [line.partition("==")[0].casefold() for line in requirements]

    assert requirements
    assert all(line.count("==") == 1 for line in requirements)
    assert len(names) == len(set(names))
    assert {"mkdocs", "mkdocs-material", "requests", "urllib3"} <= set(names)

    workflow = (
        REPO_ROOT / ".github" / "workflows" / "docs.yml"
    ).read_text(encoding="utf-8")
    assert "pip install --requirement requirements/docs.txt" in workflow
    assert workflow.count("'requirements/docs.txt'") == 2
