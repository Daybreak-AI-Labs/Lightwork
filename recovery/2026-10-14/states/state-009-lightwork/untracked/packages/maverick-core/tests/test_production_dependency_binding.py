"""External code used by the production inventory must be portably bound."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_binding_module():
    path = REPO_ROOT / "scripts" / "production_dependency_binding.py"
    spec = importlib.util.spec_from_file_location("production_dependency_binding", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def binding_module(monkeypatch):
    module = _load_binding_module()
    monkeypatch.setattr(module, "REVIEWED_DISTRIBUTIONS", ("native", "pure"))
    monkeypatch.setattr(module, "NATIVE_EXTENSION_DISTRIBUTIONS", ("native",))
    return module


def _repository(path: Path, *, newline: str = "\n", constraints: str | None = None) -> Path:
    (path / "requirements").mkdir(parents=True)
    value = constraints or "native==2.0\npure==1.0\n"
    (path / "requirements/ci.txt").write_bytes(
        value.replace("\n", newline).encode("utf-8")
    )
    return path


def _distribution(
    root: Path,
    name: str,
    version: str,
    *,
    newline: str = "\n",
    source: str = "VALUE = 1\n",
    native: bool = False,
) -> tuple[Path, Path | None]:
    package = name.replace("-", "_")
    source_path = root / package / "__init__.py"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(source.replace("\n", newline).encode("utf-8"))
    native_path = root / package / "extension.pyd" if native else None
    if native_path is not None:
        native_path.write_bytes(b"platform-binary")
    metadata = root / f"{package}-{version}.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_bytes(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n"
        .replace("\n", newline)
        .encode("utf-8")
    )
    rows = [f"{package}/__init__.py,,"]
    if native_path is not None:
        rows.append(f"{package}/extension.pyd,,")
    rows.extend(
        [
            f"{metadata.name}/METADATA,,",
            f"{metadata.name}/RECORD,,",
        ]
    )
    (metadata / "RECORD").write_bytes((newline.join(rows) + newline).encode("utf-8"))
    return source_path, native_path


def _environment(
    tmp_path: Path,
    *,
    newline: str = "\n",
    pure_version: str = "1.0",
) -> tuple[Path, Path, dict[str, Path]]:
    repository = _repository(tmp_path / "repository", newline=newline)
    dependencies = tmp_path / "site-packages"
    dependencies.mkdir()
    pure, _ = _distribution(
        dependencies,
        "pure",
        pure_version,
        newline=newline,
    )
    native_source, native_binary = _distribution(
        dependencies,
        "native",
        "2.0",
        newline=newline,
        native=True,
    )
    assert native_binary is not None
    return repository, dependencies, {
        "pure": pure,
        "native_source": native_source,
        "native_binary": native_binary,
    }


def test_binding_is_relocation_and_line_ending_portable(binding_module, tmp_path):
    first_repo, first_dependencies, _ = _environment(tmp_path / "first", newline="\n")
    second_repo, second_dependencies, _ = _environment(
        tmp_path / "second", newline="\r\n"
    )

    first = binding_module.bind_runtime_dependencies(first_repo, [first_dependencies])
    second = binding_module.bind_runtime_dependencies(second_repo, [second_dependencies])

    assert dict(first.report) == dict(second.report)
    assert first.report["absolute_host_paths_serialized"] is False
    assert first.report["source_file_count"] == 2


def test_binding_detects_source_mutation(binding_module, tmp_path):
    repository, dependencies, paths = _environment(tmp_path)
    binding = binding_module.bind_runtime_dependencies(repository, [dependencies])
    paths["pure"].write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(binding_module.DependencyBindingError, match="changed"):
        binding_module.assert_runtime_dependencies_unchanged(binding, repository)


def test_binding_rejects_version_drift(binding_module, tmp_path):
    repository, dependencies, _ = _environment(tmp_path, pure_version="1.1")

    with pytest.raises(binding_module.DependencyBindingError, match="version drifted"):
        binding_module.bind_runtime_dependencies(repository, [dependencies])


@pytest.mark.parametrize(
    "constraints, message",
    [
        ("native==2.0\npure>=1.0\n", "not an exact pin"),
        ("native==2.0\npure==https://example.invalid/pure.whl\n", "non-canonical"),
        ("native==2.0\npure==1.0\npure==1.0\n", "duplicate"),
        ("native==2.0\npure==1.0; python_version > '3'\n", "unconditional"),
    ],
)
def test_binding_rejects_noncanonical_reviewed_constraints(
    binding_module,
    tmp_path,
    constraints,
    message,
):
    repository = _repository(tmp_path / "repository", constraints=constraints)
    dependencies = tmp_path / "site-packages"
    dependencies.mkdir()

    with pytest.raises(binding_module.DependencyBindingError, match=message):
        binding_module.bind_runtime_dependencies(repository, [dependencies])


def test_binding_validates_python_and_approved_native_origins(binding_module, tmp_path):
    repository, dependencies, paths = _environment(tmp_path)
    binding = binding_module.bind_runtime_dependencies(repository, [dependencies])

    observed = binding_module.validate_runtime_dependency_origins(
        binding,
        [
            str(paths["pure"]),
            str(paths["native_source"]),
            str(paths["native_binary"]),
        ],
    )

    assert {row["distribution"] for row in observed} == {"native", "pure"}
    unknown = dependencies / "pure" / "unknown.py"
    unknown.write_text("VALUE = 3\n", encoding="utf-8")
    with pytest.raises(binding_module.DependencyBindingError, match="unbound"):
        binding_module.validate_runtime_dependency_origins(binding, [str(unknown)])

    snapshot = binding_module.materialize_runtime_dependencies(
        binding,
        tmp_path / "dependency-snapshot",
    )
    materialized = binding_module.validate_runtime_dependency_origins(
        binding,
        [
            str(snapshot / "pure/__init__.py"),
            str(snapshot / "native/__init__.py"),
            str(snapshot / "native/extension.pyd"),
        ],
        materialized_root=snapshot,
    )
    assert materialized == observed


def test_materialized_snapshot_defeats_swap_restore_and_native_mutation(
    binding_module,
    tmp_path,
):
    repository, dependencies, paths = _environment(tmp_path)
    safe_source = paths["pure"].read_bytes()
    safe_native = paths["native_binary"].read_bytes()
    binding = binding_module.bind_runtime_dependencies(repository, [dependencies])

    paths["pure"].write_bytes(b"EVIL_ = 1\n")
    paths["native_binary"].write_bytes(b"untrusted-binary")
    snapshot = binding_module.materialize_runtime_dependencies(
        binding,
        tmp_path / "dependency-snapshot",
    )
    paths["pure"].write_bytes(safe_source)
    paths["native_binary"].write_bytes(safe_native)

    binding_module.assert_runtime_dependencies_unchanged(binding, repository)
    assert (snapshot / "pure/__init__.py").read_bytes() == safe_source
    assert (snapshot / "native/extension.pyd").read_bytes() == safe_native


def test_binding_rejects_hardlinked_source(binding_module, tmp_path):
    repository, dependencies, paths = _environment(tmp_path)
    replacement = dependencies / "pure" / "replacement.py"
    paths["pure"].replace(replacement)
    try:
        os.link(replacement, paths["pure"])
    except OSError:
        pytest.skip("hard links are unavailable")

    with pytest.raises(binding_module.DependencyBindingError, match="single-link"):
        binding_module.bind_runtime_dependencies(repository, [dependencies])


def test_binding_rejects_linked_dependency_root(binding_module, tmp_path):
    repository, dependencies, _ = _environment(tmp_path)
    linked = tmp_path / "linked-site-packages"
    try:
        linked.symlink_to(dependencies, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(binding_module.DependencyBindingError, match="link or reparse"):
        binding_module.bind_runtime_dependencies(repository, [linked])


def test_binding_enforces_source_file_and_byte_caps(binding_module, monkeypatch, tmp_path):
    repository, dependencies, _ = _environment(tmp_path)
    monkeypatch.setattr(binding_module, "MAX_DEPENDENCY_SOURCE_FILES", 1)
    with pytest.raises(binding_module.DependencyBindingError, match="file limit"):
        binding_module.bind_runtime_dependencies(repository, [dependencies])

    monkeypatch.setattr(binding_module, "MAX_DEPENDENCY_SOURCE_FILES", 10)
    monkeypatch.setattr(binding_module, "MAX_DEPENDENCY_SOURCE_BYTES", 1)
    with pytest.raises(binding_module.DependencyBindingError, match="aggregate byte limit"):
        binding_module.bind_runtime_dependencies(repository, [dependencies])
