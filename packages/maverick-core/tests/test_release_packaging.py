from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_sku_builder():
    path = REPO_ROOT / "scripts" / "build_standalone_skus.py"
    spec = importlib.util.spec_from_file_location("build_standalone_skus", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def test_pyinstaller_collects_core_and_dashboard_package_data():
    spec_path = REPO_ROOT / "build" / "maverick.spec"
    tree = ast.parse(spec_path.read_text(encoding="utf-8"))
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
    assert {"maverick", "maverick_dashboard"} <= collected


def test_changed_python_distributions_share_the_0_1_7_release_floor():
    paths = (
        REPO_ROOT / "packages" / "maverick-core" / "pyproject.toml",
        REPO_ROOT / "packages" / "maverick-dashboard" / "pyproject.toml",
        REPO_ROOT / "apps" / "installer-cli" / "pyproject.toml",
    )
    metadata = [tomllib.loads(path.read_text(encoding="utf-8")) for path in paths]
    assert {item["project"]["version"] for item in metadata} == {"0.1.7"}
    assert "maverick-agent>=0.1.7" in metadata[1]["project"]["dependencies"]
    assert "maverick-agent>=0.1.7" in metadata[2]["project"]["dependencies"]
    core_extras = metadata[0]["project"]["optional-dependencies"]
    assert core_extras["dashboard"] == ["maverick-dashboard>=0.1.7"]
    assert core_extras["installer"] == ["maverick-installer>=0.1.7"]
    assert "maverick-dashboard>=0.1.7" in core_extras["all"]
    assert "maverick-installer>=0.1.7" in core_extras["all"]

    init_paths = (
        REPO_ROOT / "packages" / "maverick-core" / "maverick" / "__init__.py",
        REPO_ROOT / "packages" / "maverick-dashboard" / "maverick_dashboard" / "__init__.py",
        REPO_ROOT / "apps" / "installer-cli" / "maverick_installer" / "__init__.py",
    )
    for path in init_paths:
        assignments = {
            node.targets[0].id: node.value.value
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
        }
        assert assignments["__version__"] == "0.1.7"


def test_standalone_skus_are_github_source_assets_not_pypi_packages():
    release_workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    publish_workflow = (REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text(
        encoding="utf-8"
    )
    assert "python scripts/build_standalone_skus.py" in release_workflow
    assert "dist/lightwork-*" in release_workflow
    for sku in (
        "grc-concierge",
        "platform-threat-hunter",
        "environment-threat-hunter",
        "model-risk-ai-assurance-officer",
    ):
        assert f"demo/{sku}" not in publish_workflow
