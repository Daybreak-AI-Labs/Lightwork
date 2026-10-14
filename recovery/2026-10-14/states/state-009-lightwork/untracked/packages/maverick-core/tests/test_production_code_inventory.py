"""REL-24 source inventories must be complete, deterministic, and fail closed."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import py_compile
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from maverick import proof_provenance

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_verifier():
    path = REPO_ROOT / "scripts" / "verify_production_code_inventory.py"
    spec = importlib.util.spec_from_file_location("verify_production_code_inventory", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_results_generator():
    path = REPO_ROOT / "proof" / "generate_results.py"
    spec = importlib.util.spec_from_file_location(
        "inventory_cycle_generate_results", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *args: str) -> None:
    generated = subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "user.name=Proof Inventory Test",
            "-c",
            "user.email=proof-inventory@example.invalid",
            *args,
        ],
        cwd=root,
        check=False,
        capture_output=True,
    )
    assert generated.returncode == 0, generated.stderr.decode("utf-8", errors="replace")


def _git_with_checkout_policy(root: Path, autocrlf: bool, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            f"core.autocrlf={'true' if autocrlf else 'false'}",
            "-c",
            "user.name=Inventory Portability Test",
            "-c",
            "user.email=inventory-portability@example.invalid",
            *args,
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _install_fake_snapshot_authorities(verifier, monkeypatch, tmp_path):
    repository = tmp_path / "snapshot-repository"
    repository.mkdir()
    (repository / "source.txt").write_bytes(b"source-v1\n")
    raw_dir = repository / "proof" / "results" / "raw"
    raw_dir.mkdir(parents=True)
    (repository / "proof" / "RESULTS.md").write_bytes(b"results\n")
    (repository / "proof" / "results" / "manifest.json").write_bytes(b"{}\n")
    (repository / "proof" / "results" / "generation.json").write_bytes(b"{}\n")
    for identity, _script in verifier._PROOF_HARNESS_SCRIPTS:
        for stream in ("stdout", "stderr"):
            (raw_dir / f"{identity}.{stream}.txt").write_bytes(b"")
    dependency_root = tmp_path / "snapshot-dependencies"
    dependency_root.mkdir()
    dependency_source = dependency_root / "dependency.py"
    dependency_source.write_bytes(b"VALUE = 'safe'\n")

    class FakeProvenance:
        SYMLINK_MATERIALIZATION_POLICY = "fake-safe-links-v1"
        MATERIALIZED_TREE_IDENTITY_SCHEMA = "fake-tree-v1"

        def __init__(self):
            self.materialize_calls = 0

        @staticmethod
        def proof_source_exclusions(_root, *, generated_paths=()):
            assert len(tuple(generated_paths)) == 2
            return [
                "proof/RESULTS.md",
                "proof/results",
                "release/claim-evidence-ledger.json",
                "release/production-code-inventory.json",
                "release/release-state.json",
            ]

        @staticmethod
        def _source_identity(root):
            raw = (Path(root) / "source.txt").read_bytes()
            return {
                "schema": "fake-source-v1",
                "path_count": 1,
                "byte_count": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }

        def repository_provenance(self, root, *, exclude_paths=()):
            return {
                "available": True,
                "source_content": self._source_identity(root),
                "excluded_paths": list(exclude_paths),
            }

        @staticmethod
        def source_content_identity(value):
            return dict(value["source_content"])

        def materialize_repository_source(
            self,
            root,
            destination,
            *,
            exclude_paths=(),
            symlink_materialization_policy,
        ):
            assert exclude_paths
            assert symlink_materialization_policy == self.SYMLINK_MATERIALIZATION_POLICY
            self.materialize_calls += 1
            destination.mkdir()
            raw = (Path(root) / "source.txt").read_bytes()
            (destination / "source.txt").write_bytes(raw)
            return self._source_identity(destination)

        @staticmethod
        def materialized_tree_identity(root):
            rows = []
            for path in sorted(Path(root).rglob("*")):
                if path.is_file():
                    raw = path.read_bytes()
                    rows.append(
                        (
                            path.relative_to(root).as_posix(),
                            len(raw),
                            hashlib.sha256(raw).hexdigest(),
                        )
                    )
            encoded = json.dumps(rows, separators=(",", ":")).encode("utf-8")
            return {
                "schema": "fake-tree-v1",
                "path_count": len(rows),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }

        @staticmethod
        def make_materialized_tree_read_only(_root):
            return None

    class FakeDependencyError(RuntimeError):
        pass

    class FakeDependencyModule:
        DependencyBindingError = FakeDependencyError

        @staticmethod
        def bind_runtime_dependencies(_repository_root, roots):
            source = Path(roots[0]) / "dependency.py"
            raw = source.read_bytes()
            return SimpleNamespace(
                report={"sha256": hashlib.sha256(raw).hexdigest()},
                roots=tuple(map(Path, roots)),
                captured=raw,
            )

        @staticmethod
        def materialize_runtime_dependencies(binding, destination):
            destination.mkdir()
            (destination / "dependency.py").write_bytes(binding.captured)
            return destination

        @staticmethod
        def assert_runtime_dependencies_unchanged(binding, _repository_root):
            current = (binding.roots[0] / "dependency.py").read_bytes()
            if current != binding.captured:
                raise FakeDependencyError("dependency changed")

    provenance = FakeProvenance()
    dependency_module = FakeDependencyModule()
    monkeypatch.setattr(verifier, "_load_proof_provenance", lambda _root: provenance)
    monkeypatch.setattr(
        verifier,
        "_load_production_dependency_binding",
        lambda _root: dependency_module,
    )
    monkeypatch.setattr(
        verifier,
        "_runtime_probe_import_paths",
        lambda _root: [str(dependency_root)],
    )
    return repository, dependency_source, provenance


def test_excluded_mobile_companion_does_not_drift_from_the_canonical_port():
    paths = (
        "apps/mobile-companion/src/settings.ts",
        "apps/mobile-companion/src/screens/SettingsScreen.tsx",
        "apps/mobile-companion/README.md",
    )
    for relative in paths:
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert ":8400" not in source
        assert ":8765" in source


@pytest.fixture(scope="module")
def verifier():
    return _load_verifier()


@pytest.fixture(scope="module")
def expected(verifier):
    return verifier.collect_production_code_inventory(REPO_ROOT)


def test_committed_inventory_exactly_matches_source(verifier):
    summary = verifier.verify_production_code_inventory(REPO_ROOT)

    assert summary["repository"] == "Daybreak-AI-Labs/Lightwork"
    assert summary["workspace_package_count"] == 8
    assert summary["production_package_count"] == 3
    assert summary["dashboard_route_count"] == 606
    assert summary["production_route_count"] == 78
    assert summary["registered_channel_count"] == 16
    assert summary["mcp_tool_count"] == 10
    assert summary["source_connector_count"] == 3065
    assert summary["production_connector_count"] == 0
    assert summary["consumer_installer_question_count"] == 5
    assert summary["proof_freshness_status"] == "source_content_bound_local"
    assert summary["production_release_evidence"] is False


def test_inventory_carries_complete_names_and_release_boundaries(expected):
    assert expected["digest_contract"] == {
        "source_text": "utf8-lf-sha256",
        "source_aggregate": "canonical-json-sha256",
        "retained_proof_artifact": "exact-byte-sha256",
    }
    tests = expected["tests"]
    assert tests["file_count"] == len(tests["files"])
    assert tests["file_count"] > 1_000
    assert tests["declared_test_function_count"] > 10_000
    assert all(len(row["sha256"]) == 64 for row in tests["files"])
    assert all(row["digest_mode"] == "utf8-lf-sha256" for row in tests["files"])

    packages = expected["packages"]["workspace_members"]
    assert [row["distribution"] for row in packages] == [
        "maverick-agent",
        "maverick-shield",
        "maverick-channels",
        "maverick-evolve",
        "maverick-dashboard",
        "maverick-mcp-server",
        "maverick-knowledge",
        "maverick-installer",
    ]
    assert {
        row["distribution"] for row in packages if row["production_included"]
    } == {"maverick-agent", "maverick-shield", "maverick-dashboard"}
    assert all(
        row["pyproject_digest_mode"] == "utf8-lf-sha256" for row in packages
    )

    routes = expected["dashboard_routes"]
    assert len(routes["inventory"]) == routes["route_count"] == 606
    assert sum(row["production_allowed"] for row in routes["inventory"]) == 78
    assert routes["route_inventory_sha256"] == routes["expected_route_inventory_sha256"]
    assert routes["production_policy_sha256"] == routes[
        "expected_production_policy_sha256"
    ]

    channels = expected["channels"]
    assert channels["registered_channel_count"] == len(channels["registered_channels"])
    assert channels["production_included"] == []
    assert channels["default_listener_ports"] == {
        "irc": 6697,
        "rcs": 8768,
        "sms": 8766,
        "voice": 8770,
        "whatsapp": 8765,
        "whatsapp_cloud": 8767,
    }

    mcp = expected["mcp_tools"]
    assert mcp["tool_count"] == len(mcp["tools"]) == 10
    assert mcp["production_included"] is False
    assert mcp["production_tools"] == []

    connectors = expected["connectors"]
    assert connectors["source_connector_count"] == len(
        connectors["all_source_connectors"]
    )
    assert connectors["group_counts"] == {
        "write_capable": 2877,
        "read_only": 155,
        "bespoke": 33,
        "public_data": 37,
    }
    assert connectors["production_status"] == "unsupported"
    assert connectors["production_included"] == []
    assert set(connectors["groups"]["public_data"]).issubset(
        connectors["groups"]["read_only"]
    )

    runtime_probe = expected["runtime_probe"]
    assert runtime_probe["startup_argv_prefix"] == [
        "python",
        "-I",
        "-S",
        "-B",
        "-X",
        "pycache_prefix=<fresh-controlled-empty-external-directory>",
        "-X",
        "utf8",
        "-W",
        "default",
    ]
    assert runtime_probe["site_initialization"] == "disabled"
    assert runtime_probe["pth_processing"] is False
    assert runtime_probe["pycache_prefix_empty_before_and_after"] is True
    assert runtime_probe["pycache_prefix_path_serialized"] is False
    assert "OPENAI_API_KEY" not in runtime_probe["parent_environment_allowlist"]
    assert runtime_probe["local_materialized_tree_identity_enforced"] is True
    assert runtime_probe["local_materialized_tree_identity_serialized"] is False
    assert runtime_probe["read_only_transition_identity_preserved"] is True
    assert runtime_probe["symlink_materialization_policy"] == (
        proof_provenance.SYMLINK_MATERIALIZATION_POLICY
    )
    assert runtime_probe["semantic_execution_tree_identity"] == (
        proof_provenance.semantic_execution_tree_identity(
            runtime_probe["bound_source_content"]
        )
    )
    dependency_binding = runtime_probe["dependency_binding"]
    assert dependency_binding["schema"] == "lightwork.runtime-dependency-binding.v1"
    assert dependency_binding["absolute_host_paths_serialized"] is False
    assert len(dependency_binding["distributions"]) == 13
    assert runtime_probe["dependency_snapshot"] == {
        "cache_free_materialization": True,
        "local_materialized_tree_identity_enforced": True,
        "local_materialized_tree_identity_serialized": False,
        "read_only_transition_identity_preserved": True,
        "completion_identity_preserved": True,
    }
    assert len(runtime_probe["dependency_origin_ownership"]) >= 13
    assert "materialized_tree" not in runtime_probe

    ports = expected["ports"]
    assert ports["production_dashboard"] == {
        "service_port": 80,
        "container_port": 8765,
        "cli_default_port": 8765,
        "module_default_port": 8765,
        "image_command_port": 8765,
    }
    assert ports["excluded_source_listeners"]["mcp_http_default_port"] == 8771

    questions = expected["installer_questions"]
    assert [row["id"] for row in questions["consumer_questions"]] == [
        "name",
        "governance",
        "api_key",
        "workdir",
        "budget",
    ]
    assert questions["production_status"] == "unsupported"
    assert questions["production_included"] is False

    features = expected["feature_statuses"]
    assert features["feature_count"] == len(features["features"])
    assert features["surface_count"] == len(features["all_surface_statuses"])
    assert any(
        row == {
            "id": "feature:code-dgm",
            "category": "feature",
            "status": "research_only",
            "included": False,
        }
        for row in features["features"]
    )

    proof = expected["proof_freshness"]
    assert proof["current_source_content_bound"] is True
    assert len(proof["assessed_source_revision"]) == 40
    assert proof["execution_timestamp"].endswith("Z")
    assert len(proof["worktree_state_sha256"]) == 64
    assert proof["source_content"]["schema"] == "lightwork.git-source-content.v2"
    assert proof["source_content"]["path_count"] > 0
    assert len(proof["source_content"]["sha256"]) == 64
    assert len(proof["runtime_dependencies_sha256"]) == 64
    assert len(proof["retained_outputs"]) == 6
    outcomes = {row["id"]: row["exit_code"] for row in proof["retained_outputs"]}
    assert outcomes["dgm-code-rung-fixture"] != 0
    assert outcomes["adapter-rung-fixture"] == 0
    statuses = {row["id"]: row["status"] for row in proof["retained_outputs"]}
    assert statuses["dgm-code-rung-fixture"] == "skipped"
    assert statuses["adapter-rung-fixture"] == "passed"
    assert proof["all_commands_passed"] is False
    assert proof["production_release_evidence"] is False
    assert proof["results_digest_mode"] == "exact-byte-sha256"
    assert proof["manifest_digest_mode"] == "exact-byte-sha256"
    assert proof["generation_digest_mode"] == "exact-byte-sha256"


def test_source_hashes_survive_windows_generation_commit_and_linux_clone(
    verifier,
    tmp_path: Path,
) -> None:
    repository = tmp_path / "windows-source"
    tests = repository / "tests"
    proof = repository / "proof"
    tests.mkdir(parents=True)
    proof.mkdir()
    _git_with_checkout_policy(repository, True, "init", "-q")
    _git_with_checkout_policy(repository, True, "config", "core.autocrlf", "true")

    (repository / ".gitattributes").write_text(
        "/proof/raw.out -text\n",
        encoding="utf-8",
        newline="\n",
    )
    source = tests / "test_portability.py"
    source.write_bytes(b"def test_portability():\r\n    assert True\r\n")
    raw_proof = proof / "raw.out"
    raw_proof.write_bytes(b"retained\r\nproof\r\n")
    workspace = {"tool": {"pytest": {"ini_options": {"testpaths": ["tests"]}}}}

    generated_tests = verifier._tests_inventory(repository, workspace)
    generated_proof_sha256 = verifier._sha256_exact_file(raw_proof)
    assert generated_tests["files"] == [
        {
            "path": "tests/test_portability.py",
            "sha256": hashlib.sha256(
                b"def test_portability():\n    assert True\n"
            ).hexdigest(),
            "digest_mode": "utf8-lf-sha256",
            "declared_test_functions": 1,
        }
    ]
    assert generated_proof_sha256 == hashlib.sha256(raw_proof.read_bytes()).hexdigest()

    generated_path = repository / "generated-inventory-fragment.json"
    generated_path.write_text(
        json.dumps(
            {
                "tests": generated_tests,
                "proof_raw_sha256": generated_proof_sha256,
                "proof_digest_mode": verifier.EXACT_BYTE_DIGEST_MODE,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _git_with_checkout_policy(repository, True, "add", "-A")
    _git_with_checkout_policy(repository, True, "commit", "-q", "-m", "generated on Windows")

    relocated = tmp_path / "linux-checkout"
    subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "clone",
            "-q",
            "--no-local",
            str(repository),
            str(relocated),
        ],
        check=True,
        capture_output=True,
    )
    _git_with_checkout_policy(relocated, False, "config", "core.autocrlf", "false")
    assert b"\r" not in (relocated / "tests/test_portability.py").read_bytes()
    assert verifier._tests_inventory(relocated, workspace) == generated_tests
    assert verifier._sha256_exact_file(relocated / "proof/raw.out") == (
        generated_proof_sha256
    )


@pytest.mark.parametrize(
    "section",
    [
        "tests",
        "packages",
        "dashboard_routes",
        "channels",
        "mcp_tools",
        "connectors",
        "runtime_probe",
        "ports",
        "installer_questions",
        "feature_statuses",
        "proof_freshness",
    ],
)
def test_every_inventory_section_is_exact(verifier, expected, section):
    stale = copy.deepcopy(expected)
    stale[section]["unreviewed"] = True

    with pytest.raises(
        verifier.ProductionCodeInventoryError,
        match=rf"production code inventory drifted: \$\.{section} keys differ",
    ):
        verifier.validate_inventory_document(expected, stale)


def test_inventory_refuses_digest_contract_drift(verifier):
    expected = {"digest_contract": dict(verifier.DIGEST_CONTRACT)}
    stale = copy.deepcopy(expected)
    stale["digest_contract"]["source_text"] = "exact-byte-sha256"

    with pytest.raises(
        verifier.ProductionCodeInventoryError,
        match=r"production code inventory drifted: \$\.digest_contract\.source_text differs",
    ):
        verifier.validate_inventory_document(expected, stale)


def test_route_reordering_and_scalar_coercion_are_rejected(verifier, expected):
    reordered = copy.deepcopy(expected)
    reordered["dashboard_routes"]["inventory"][0:2] = reversed(
        reordered["dashboard_routes"]["inventory"][0:2]
    )
    with pytest.raises(verifier.ProductionCodeInventoryError, match=r"inventory\[0\]"):
        verifier.validate_inventory_document(expected, reordered)

    coerced = copy.deepcopy(expected)
    coerced["packages"]["workspace_member_count"] = "8"
    with pytest.raises(verifier.ProductionCodeInventoryError, match="type differs"):
        verifier.validate_inventory_document(expected, coerced)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ('{"schema":"x","schema":"y"}', "strict UTF-8 JSON"),
        ('{"value":NaN}', "strict UTF-8 JSON"),
        ('{"value":Infinity}', "strict UTF-8 JSON"),
        ('{"value":-Infinity}', "strict UTF-8 JSON"),
    ],
)
def test_inventory_parser_rejects_duplicate_and_nonfinite_json(
    verifier,
    tmp_path,
    raw,
    message,
):
    path = tmp_path / "inventory.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(verifier.ProductionCodeInventoryError, match=message):
        verifier._load_json(path, label="test inventory")


def test_inventory_parser_rejects_excessive_size_and_depth(verifier, tmp_path):
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (verifier.MAX_INVENTORY_BYTES + 1))
    with pytest.raises(verifier.ProductionCodeInventoryError, match="bounded size"):
        verifier._load_json(oversized, label="test inventory")

    nested: object = "leaf"
    for _ in range(verifier.MAX_JSON_DEPTH + 2):
        nested = {"child": nested}
    deep = tmp_path / "deep.json"
    deep.write_text(json.dumps(nested), encoding="utf-8")
    with pytest.raises(verifier.ProductionCodeInventoryError, match="deeply nested"):
        verifier._load_json(deep, label="test inventory")


def test_bounded_descriptor_read_rejects_hardlinks_and_linked_ancestors(
    verifier,
    monkeypatch,
    tmp_path,
):
    repository = tmp_path / "read-boundary"
    repository.mkdir()
    original = repository / "inventory.json"
    original.write_text('{"safe":true}\n', encoding="utf-8")
    hardlink = repository / "hardlink.json"
    os.link(original, hardlink)
    with pytest.raises(
        verifier.ProductionCodeInventoryError,
        match="single-link regular file",
    ):
        verifier._load_json(original, label="test inventory")
    hardlink.unlink()

    linked_file = repository / "linked-file.json"
    try:
        linked_file.symlink_to(original)
    except OSError:
        original_reparse_check = verifier._is_reparse_point
        file_inode = original.lstat().st_ino
        with monkeypatch.context() as reparse:
            reparse.setattr(
                verifier,
                "_is_reparse_point",
                lambda metadata: metadata.st_ino == file_inode
                or original_reparse_check(metadata),
            )
            with pytest.raises(
                verifier.ProductionCodeInventoryError,
                match="symlink or reparse point",
            ):
                verifier._load_json(original, label="test inventory")
    else:
        with pytest.raises(
            verifier.ProductionCodeInventoryError,
            match="symlink or reparse point",
        ):
            verifier._load_json(linked_file, label="test inventory")

    real_parent = repository / "real"
    real_parent.mkdir()
    (real_parent / "nested.json").write_text('{"safe":true}\n', encoding="utf-8")
    linked_parent = repository / "linked"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError:
        original_reparse_check = verifier._is_reparse_point
        ancestor_inode = real_parent.lstat().st_ino
        monkeypatch.setattr(
            verifier,
            "_is_reparse_point",
            lambda metadata: metadata.st_ino == ancestor_inode
            or original_reparse_check(metadata),
        )
        relative = "real/nested.json"
    else:
        relative = "linked/nested.json"
    with pytest.raises(
        verifier.ProductionCodeInventoryError,
        match="unsafe ancestor|symlink|reparse point",
    ):
        verifier._bounded_read_file(
            repository,
            relative,
            maximum_bytes=1024,
            label="linked inventory",
        )


def test_exact_namespace_rejects_immediately_and_bounds_expected_ids(
    verifier,
    monkeypatch,
    tmp_path,
):
    repository = tmp_path / "namespace"
    namespace = repository / "bounded"
    namespace.mkdir(parents=True)

    class UnexpectedThenExplode:
        def __init__(self):
            self._seen = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            if not self._seen:
                self._seen = True
                return SimpleNamespace(name="unexpected")
            raise AssertionError("validator enumerated past the first unexpected name")

    monkeypatch.setattr(verifier.os, "scandir", lambda _path: UnexpectedThenExplode())
    with pytest.raises(
        verifier.ProductionCodeInventoryError,
        match="missing or unexpected entries",
    ):
        verifier._validate_exact_directory_namespace(
            repository,
            "bounded",
            expected={"expected": "file"},
            maximum_entries=2,
            label="bounded namespace",
        )

    with pytest.raises(
        verifier.ProductionCodeInventoryError,
        match="expected namespace exceeds its entry ceiling",
    ):
        verifier._validate_exact_directory_namespace(
            repository,
            "bounded",
            expected={"one": "file", "two": "file"},
            maximum_entries=1,
            label="bounded namespace",
        )

    (namespace / "expected").write_bytes(b"")

    class RepeatedExpectedEntries:
        def __enter__(self):
            return iter(
                (
                    SimpleNamespace(name="expected", is_symlink=lambda: False),
                    SimpleNamespace(name="expected", is_symlink=lambda: False),
                )
            )

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(verifier.os, "scandir", lambda _path: RepeatedExpectedEntries())
    with pytest.raises(
        verifier.ProductionCodeInventoryError,
        match="namespace exceeds its entry ceiling",
    ):
        verifier._validate_exact_directory_namespace(
            repository,
            "bounded",
            expected={"expected": "file"},
            maximum_entries=1,
            label="bounded namespace",
        )

    infinite_ids = (f"harness-{index}" for index in range(10_000))
    with pytest.raises(
        verifier.ProductionCodeInventoryError,
        match="harness namespace exceeds its entry ceiling",
    ):
        verifier._bounded_harness_ids(infinite_ids)


def test_composite_snapshot_materializes_source_once_and_uses_private_authorities(
    verifier,
    monkeypatch,
    tmp_path,
):
    repository, _dependency_source, provenance = _install_fake_snapshot_authorities(
        verifier,
        monkeypatch,
        tmp_path,
    )

    with verifier._production_inventory_snapshot(repository) as snapshot:
        assert provenance.materialize_calls == 1
        assert snapshot.live_root == repository
        assert snapshot.source_root != repository
        assert snapshot.proof_artifact_root != repository
        assert snapshot.dependency_root != repository
        assert (snapshot.source_root / "source.txt").read_bytes() == b"source-v1\n"
        assert (
            snapshot.proof_artifact_root / "proof" / "RESULTS.md"
        ).read_bytes() == b"results\n"
        assert (snapshot.dependency_root / "dependency.py").read_bytes() == (
            b"VALUE = 'safe'\n"
        )

    assert provenance.materialize_calls == 1


def test_every_source_derived_section_receives_the_same_snapshot_root(
    verifier,
    monkeypatch,
    tmp_path,
):
    source_root = tmp_path / "one-source-root"
    source_root.mkdir()
    snapshot = SimpleNamespace(source_root=source_root)
    observed_roots = []
    observed_snapshots = []
    source_content = {"schema": "source", "sha256": "a" * 64}
    semantic_identity = {"schema": "semantic", "sha256": "b" * 64}

    def observe_root(root, value):
        observed_roots.append(root)
        return value

    monkeypatch.setattr(
        verifier,
        "_repo_file",
        lambda root, _relative: observe_root(root, source_root / "placeholder"),
    )
    monkeypatch.setattr(verifier, "_read_toml", lambda _path, label: {})
    monkeypatch.setattr(
        verifier,
        "_surface_document",
        lambda root: observe_root(
            root,
            (
                {
                    "source_version": "test",
                    "profile_id": "profile",
                    "constraints": {"channels": [], "connectors": []},
                    "surfaces": [
                        {
                            "id": "interface:mcp-stdio",
                            "category": "interface",
                            "status": "excluded",
                            "included": False,
                        },
                        {
                            "id": "interface:mcp-http",
                            "category": "interface",
                            "status": "excluded",
                            "included": False,
                        },
                    ],
                },
                "c" * 64,
            ),
        ),
    )

    def runtime_inventory(value):
        observed_snapshots.append(value)
        return {
            "routes": {"route_count": 0, "production_admitted_count": 0},
            "mcp": {"tools": [], "tool_count": 0},
            "connectors": {
                "all_source_connectors": [],
                "production_status": "unsupported",
                "production_included": [],
            },
            "runtime_probe": {
                "bound_source_content": source_content,
                "semantic_execution_tree_identity": semantic_identity,
            },
        }

    monkeypatch.setattr(verifier, "_runtime_inventory", runtime_inventory)
    monkeypatch.setattr(
        verifier,
        "_channels_inventory",
        lambda root: observe_root(root, {"registered_channels": []}),
    )
    monkeypatch.setattr(
        verifier,
        "_tests_inventory",
        lambda root, _workspace: observe_root(root, {"file_count": 0}),
    )
    monkeypatch.setattr(
        verifier,
        "_workspace_packages",
        lambda root, _workspace, _surface: observe_root(
            root,
            {"workspace_member_count": 0, "production_included_count": 0},
        ),
    )
    monkeypatch.setattr(
        verifier,
        "_ports_inventory",
        lambda root, _channels: observe_root(root, {}),
    )
    monkeypatch.setattr(
        verifier,
        "_installer_inventory",
        lambda root, _surface: observe_root(root, {"consumer_question_count": 5}),
    )
    monkeypatch.setattr(verifier, "_feature_inventory", lambda _surface, _sha: {})

    def proof_inventory(value):
        observed_snapshots.append(value)
        return {
            "source_content": source_content,
            "semantic_execution_tree_identity": semantic_identity,
        }

    monkeypatch.setattr(verifier, "_proof_inventory", proof_inventory)
    monkeypatch.setattr(
        verifier,
        "_validate_documentation",
        lambda root, _inventory: observe_root(root, None),
    )

    inventory = verifier._collect_production_code_inventory_from_snapshot(snapshot)

    assert inventory["runtime_probe"]["bound_source_content"] == source_content
    assert observed_snapshots == [snapshot, snapshot]
    assert observed_roots
    assert set(observed_roots) == {source_root}


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("live-source", "live source changed"),
        ("live-proof", "live proof artifacts changed"),
        ("source-snapshot", "source snapshot changed"),
        ("proof-snapshot", "proof artifact snapshot changed"),
        ("live-dependency", "snapshot stability could not be verified"),
        ("dependency-snapshot", "dependency snapshot changed"),
    ],
)
def test_composite_snapshot_rejects_mutation_between_sections(
    verifier,
    monkeypatch,
    tmp_path,
    mutation,
    message,
):
    repository, dependency_source, _provenance = _install_fake_snapshot_authorities(
        verifier,
        monkeypatch,
        tmp_path,
    )

    with pytest.raises(verifier.ProductionCodeInventoryError, match=message):
        with verifier._production_inventory_snapshot(repository) as snapshot:
            if mutation == "live-source":
                (repository / "source.txt").write_bytes(b"source-v2\n")
            elif mutation == "live-proof":
                (repository / "proof" / "RESULTS.md").write_bytes(b"changed\n")
            elif mutation == "source-snapshot":
                (snapshot.source_root / "source.txt").write_bytes(b"changed\n")
            elif mutation == "proof-snapshot":
                (
                    snapshot.proof_artifact_root / "proof" / "RESULTS.md"
                ).write_bytes(b"changed\n")
            elif mutation == "live-dependency":
                dependency_source.write_bytes(b"VALUE = 'changed'\n")
            elif mutation == "dependency-snapshot":
                (snapshot.dependency_root / "dependency.py").write_bytes(b"changed\n")
            else:  # pragma: no cover - parameter invariant
                raise AssertionError(mutation)


def test_atomic_inventory_write_succeeds_and_rejects_unsafe_targets(
    verifier,
    monkeypatch,
    tmp_path,
):
    repository = tmp_path / "atomic-success"
    repository.mkdir()
    target = repository / "release" / "inventory.json"
    verifier._atomic_write_inventory(repository, target, b'{"version":1}\n')
    assert target.read_bytes() == b'{"version":1}\n'
    verifier._atomic_write_inventory(repository, target, b'{"version":2}\n')
    assert target.read_bytes() == b'{"version":2}\n'
    assert not list(target.parent.glob(".*.inventory-*.tmp"))

    alias = target.parent / "inventory-alias.json"
    os.link(target, alias)
    with pytest.raises(
        verifier.ProductionCodeInventoryError,
        match="single-link regular file",
    ):
        verifier._atomic_write_inventory(repository, target, b"{}\n")
    alias.unlink()

    linked_target = target.parent / "linked.json"
    try:
        linked_target.symlink_to(target)
    except OSError:
        original_reparse_check = verifier._is_reparse_point
        target_inode = target.lstat().st_ino
        with monkeypatch.context() as reparse:
            reparse.setattr(
                verifier,
                "_is_reparse_point",
                lambda metadata: metadata.st_ino == target_inode
                or original_reparse_check(metadata),
            )
            with pytest.raises(
                verifier.ProductionCodeInventoryError,
                match="single-link regular file",
            ):
                verifier._atomic_write_inventory(repository, target, b"{}\n")
    else:
        with pytest.raises(
            verifier.ProductionCodeInventoryError,
            match="single-link regular file",
        ):
            verifier._atomic_write_inventory(repository, linked_target, b"{}\n")


def test_atomic_inventory_write_cleans_failed_stage_and_detects_target_swap(
    verifier,
    monkeypatch,
    tmp_path,
):
    repository = tmp_path / "atomic-failure"
    target = repository / "release" / "inventory.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old\n")
    original_write = verifier.os.write
    calls = 0

    def fail_after_partial_write(descriptor, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, payload[:1])
        raise OSError("injected staging failure")

    with monkeypatch.context() as failure:
        failure.setattr(verifier.os, "write", fail_after_partial_write)
        with pytest.raises(
            verifier.ProductionCodeInventoryError,
            match="staging write failed",
        ):
            verifier._atomic_write_inventory(repository, target, b"new payload\n")
    assert target.read_bytes() == b"old\n"
    assert not list(target.parent.glob(".*.inventory-*.tmp"))

    original_replace = verifier.os.replace

    def mutate_after_replace(source, destination):
        original_replace(source, destination)
        Path(destination).write_bytes(b"attacker\n")

    with monkeypatch.context() as swap:
        swap.setattr(verifier.os, "replace", mutate_after_replace)
        with pytest.raises(
            verifier.ProductionCodeInventoryError,
            match="changed during atomic publication",
        ):
            verifier._atomic_write_inventory(repository, target, b"safe\n")


def test_atomic_inventory_write_detects_parent_swap(verifier, monkeypatch, tmp_path):
    repository = tmp_path / "atomic-parent-swap"
    parent = repository / "release"
    parent.mkdir(parents=True)
    target = parent / "inventory.json"
    target.write_bytes(b"old\n")
    displaced = repository / "release-displaced"
    original_replace = verifier.os.replace

    def swap_parent_after_replace(source, destination):
        original_replace(source, destination)
        parent.rename(displaced)
        parent.mkdir()

    with monkeypatch.context() as swap:
        swap.setattr(verifier.os, "replace", swap_parent_after_replace)
        with pytest.raises(
            verifier.ProductionCodeInventoryError,
            match="published atomically|changed during atomic publication",
        ):
            verifier._atomic_write_inventory(repository, target, b"safe\n")


def test_regenerated_document_is_canonical_and_path_cannot_escape(
    verifier,
    expected,
    tmp_path,
):
    rendered = json.dumps(expected, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    parsed = json.loads(rendered)
    verifier.validate_inventory_document(expected, parsed)

    with pytest.raises(verifier.ProductionCodeInventoryError, match="inside repository"):
        verifier.write_production_code_inventory(REPO_ROOT, tmp_path / "escaped.json")


def test_proof_results_tree_is_exact_and_rejects_linked_files(
    verifier,
    monkeypatch,
    tmp_path,
):
    repository = tmp_path / "proof-tree"
    raw_dir = repository / "proof" / "results" / "raw"
    raw_dir.mkdir(parents=True)
    (repository / "proof" / "RESULTS.md").write_text("bounded\n", encoding="utf-8")
    manifest_path = repository / "proof" / "results" / "manifest.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    (repository / "proof" / "results" / "generation.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    identifiers = dict(verifier._PROOF_HARNESS_SCRIPTS)
    for identity in identifiers:
        for stream in ("stdout", "stderr"):
            (raw_dir / f"{identity}.{stream}.txt").write_bytes(b"")

    verifier._validate_exact_proof_tree(repository, identifiers)
    bounded = raw_dir / f"{next(iter(identifiers))}.stdout.txt"
    bounded.write_bytes(b"x")
    with monkeypatch.context() as bounds:
        bounds.setattr(verifier, "MAX_PROOF_RAW_TOTAL_BYTES", 0)
        with pytest.raises(
            verifier.ProductionCodeInventoryError,
            match="raw-output tree exceeds",
        ):
            verifier._validate_exact_proof_tree(repository, identifiers)
    bounded.write_bytes(b"")

    extra = raw_dir / "stale-output.txt"
    extra.write_text("stale\n", encoding="utf-8")
    with pytest.raises(
        verifier.ProductionCodeInventoryError,
        match="missing or unexpected entries",
    ):
        verifier._validate_exact_proof_tree(repository, identifiers)
    extra.unlink()

    linked = raw_dir / f"{next(iter(identifiers))}.stdout.txt"
    linked.unlink()
    os.link(manifest_path, linked)
    with pytest.raises(
        verifier.ProductionCodeInventoryError,
        match="single-link regular file",
    ):
        verifier._validate_exact_proof_tree(repository, identifiers)


def test_runtime_probe_discards_credentials_and_bounds_output(
    verifier,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-cross-runtime-probe")
    environment = verifier._runtime_probe_environment(str(tmp_path))
    assert "OPENAI_API_KEY" not in environment
    assert set(environment).issubset(
        {
            *verifier._RUNTIME_PROBE_SYSTEM_ENV_ALLOWLIST,
            "MAVERICK_CONFIG",
            "MAVERICK_HOME",
            "TEMP",
            "TMP",
        }
    )

    with pytest.raises(
        verifier.ProductionCodeInventoryError,
        match="exceeded output bounds",
    ):
        verifier._run_bounded_runtime_probe(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                (
                    "import sys; sys.stdout.buffer.write(b'x' * "
                    f"{verifier.MAX_RUNTIME_PROBE_STDOUT_BYTES + 1})"
                ),
            ],
            cwd=tmp_path,
            env=environment,
        )

    repository = tmp_path / "repository"
    virtual_prefix = tmp_path / "venv"
    base_prefix = tmp_path / "base"
    repository.mkdir()
    if os.name == "nt":
        virtual_packages = virtual_prefix / "Lib" / "site-packages"
        base_packages = base_prefix / "Lib" / "site-packages"
    else:
        version = f"python{sys.version_info.major}.{sys.version_info.minor}"
        virtual_packages = virtual_prefix / "lib" / version / "site-packages"
        base_packages = base_prefix / "lib" / version / "site-packages"
    virtual_packages.mkdir(parents=True)
    base_packages.mkdir(parents=True)
    configuration = virtual_prefix / "pyvenv.cfg"
    with monkeypatch.context() as policy_patch:
        policy_patch.setattr(verifier.sys, "prefix", str(virtual_prefix))
        policy_patch.setattr(verifier.sys, "base_prefix", str(base_prefix))
        configuration.write_text(
            "include-system-site-packages = false\n",
            encoding="utf-8",
        )
        assert verifier._runtime_probe_import_paths(repository) == [
            str(virtual_packages.resolve())
        ]
        configuration.write_text(
            "include-system-site-packages = true\n",
            encoding="utf-8",
        )
        assert verifier._runtime_probe_import_paths(repository) == [
            str(virtual_packages.resolve()),
            str(base_packages.resolve()),
        ]


def test_runtime_probe_ignores_timestamp_valid_poisoned_dependency_pyc(
    verifier,
    tmp_path,
) -> None:
    dependency_root = tmp_path / "dependency"
    source_root = tmp_path / "bound-source"
    dependency_root.mkdir()
    source_root.mkdir()
    source = dependency_root / "poisoned_dependency.py"
    evil = "VALUE = 'evil'\n"
    safe = "VALUE = 'safe'\n"
    assert len(evil.encode("utf-8")) == len(safe.encode("utf-8"))
    timestamp = int(time.time()) - 60
    source.write_text(evil, encoding="utf-8")
    os.utime(source, (timestamp, timestamp))
    source_size = source.stat().st_size
    py_compile.compile(str(source), doraise=True)
    poisoned_cache = Path(importlib.util.cache_from_source(str(source)))
    assert poisoned_cache.is_file()
    source.write_text(safe, encoding="utf-8")
    os.utime(source, (timestamp, timestamp))
    assert source.stat().st_size == source_size
    assert int(source.stat().st_mtime) == timestamp

    command = (
        "import sys; "
        f"sys.path.insert(0, {str(dependency_root)!r}); "
        "import poisoned_dependency; print(poisoned_dependency.VALUE)"
    )
    poisoned = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", command],
        check=True,
        capture_output=True,
    )
    assert poisoned.stdout.strip() == b"evil"

    pycache_prefix = verifier._prepare_runtime_probe_pycache_prefix(
        tmp_path / "controlled-pycache",
        source_root=source_root,
        dependency_paths=[str(dependency_root)],
    )
    returncode, stdout, stderr = verifier._run_bounded_runtime_probe(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-X",
            f"pycache_prefix={pycache_prefix}",
            "-c",
            command,
        ],
        cwd=source_root,
        env=verifier._runtime_probe_environment(str(tmp_path)),
        controller_factory=proof_provenance.process_tree_controller,
    )

    assert returncode == 0
    assert stdout.strip() == b"safe"
    assert stderr == b""
    verifier._validate_empty_runtime_pycache_prefix(pycache_prefix)


def test_runtime_probe_rejects_and_kills_lingering_descendants(
    verifier,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "late-descendant-write.txt"
    child = (
        "import sys,time; from pathlib import Path; time.sleep(1.0); "
        "Path(sys.argv[1]).write_text('late', encoding='utf-8')"
    )
    parent = (
        "import os,subprocess,sys; "
        "subprocess.Popen([sys.executable,'-I','-S','-c',"
        f"{child!r},{str(marker)!r}], stdin=subprocess.DEVNULL, "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, "
        f"start_new_session={os.name != 'nt'!r})"
    )
    with pytest.raises(
        verifier.ProductionCodeInventoryError,
        match="complete process tree",
    ):
        verifier._run_bounded_runtime_probe(
            [sys.executable, "-I", "-S", "-c", parent],
            cwd=tmp_path,
            env=verifier._runtime_probe_environment(str(tmp_path)),
        )
    time.sleep(1.2)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="suspended-spawn regression is Windows-only")
def test_runtime_probe_assigns_windows_job_before_child_code(
    verifier,
    monkeypatch,
    tmp_path,
) -> None:
    marker = tmp_path / "probe-root-ran.txt"
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
    returncode, stdout, stderr = verifier._run_bounded_runtime_probe(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            (
                "from pathlib import Path; import sys; "
                "Path(sys.argv[1]).write_text('ran'); print('ok')"
            ),
            str(marker),
        ],
        cwd=tmp_path,
        env=verifier._runtime_probe_environment(str(tmp_path)),
        controller_factory=proof_provenance.process_tree_controller,
    )

    assert observed == [False]
    assert returncode == 0
    assert stdout == (b"ok\r\n" if os.name == "nt" else b"ok\n")
    assert stderr == b""
    assert marker.read_text(encoding="utf-8") == "ran"


def test_recorded_evidence_paths_use_the_producer_platform_flavor(verifier) -> None:
    assert verifier._recorded_path_is_absolute(
        r"C:\Python312\python.exe",
        platform_name="Windows-11-10.0.26100",
    )
    assert verifier._recorded_path_is_absolute(
        r"\\server\share\runtime\site-packages",
        platform_name="Windows-Server-2025",
    )
    assert verifier._recorded_path_is_absolute(
        "/usr/local/bin/python3.12",
        platform_name="Linux-6.8.0-x86_64",
    )
    assert not verifier._recorded_path_is_absolute(
        r"C:\Python312\python.exe",
        platform_name="Linux-6.8.0-x86_64",
    )
    assert not verifier._recorded_path_is_absolute(
        "runtime/site-packages",
        platform_name="Windows-11",
    )


def test_results_then_inventory_write_and_verify_has_no_provenance_cycle(
    verifier,
    monkeypatch,
    tmp_path,
):
    """A downstream inventory write must not require another proof execution."""
    generator = _load_results_generator()
    repository = tmp_path / "repo"
    proof_dir = repository / "proof"
    provenance_path = (
        repository / "packages" / "maverick-core" / "maverick" / "proof_provenance.py"
    )
    proof_dir.mkdir(parents=True)
    provenance_path.parent.mkdir(parents=True)
    provenance_path.write_bytes(
        (REPO_ROOT / "packages/maverick-core/maverick/proof_provenance.py").read_bytes()
    )
    (provenance_path.parent / "source_fixture.py").write_text(
        "SOURCE_FIXTURE = True\n",
        encoding="utf-8",
    )
    (proof_dir / "generate_results.py").write_bytes(
        (REPO_ROOT / "proof/generate_results.py").read_bytes()
    )
    (proof_dir / "isolated_harness_bootstrap.py").write_bytes(
        (REPO_ROOT / "proof/isolated_harness_bootstrap.py").read_bytes()
    )
    (repository / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n", encoding="utf-8"
    )
    (repository / ".gitattributes").write_text(
        "/proof/RESULTS.md -text\n/proof/results/** -text\n",
        encoding="utf-8",
    )
    for harness in generator.HARNESSES:
        path = repository / harness.script
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"print('[PASS] bound harness: {harness.id}')\n",
            encoding="utf-8",
        )

    _git(repository, "init", "-q")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "proof source")

    canonical_python = str(getattr(sys, "_base_executable", sys.executable))
    generated = subprocess.run(
        [
            canonical_python,
            "-I",
            "-S",
            "-B",
            "-X",
            "utf8",
            "-W",
            "default",
            "proof/generate_results.py",
        ],
        cwd=repository,
        check=False,
        capture_output=True,
    )
    assert generated.returncode == 0, generated.stderr.decode("utf-8", errors="replace")
    manifest = json.loads(
        (repository / "proof/results/manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["source_stable"] is True
    expected_exclusions = sorted(
        [
            *proof_provenance.DOWNSTREAM_GOVERNANCE_ARTIFACTS,
            "proof/RESULTS.md",
            "proof/results",
        ]
    )
    assert manifest["source"]["excluded_paths"] == expected_exclusions

    def minimal_inventory(root: Path) -> dict[str, object]:
        resolved = root.resolve()
        provenance = verifier._load_proof_provenance(resolved)
        current_source = provenance.repository_provenance(
            resolved,
            exclude_paths=expected_exclusions,
        )
        snapshot = SimpleNamespace(
            source_root=resolved,
            proof_artifact_root=resolved,
            provenance=provenance,
            source_content=provenance.source_content_identity(current_source),
            live_source_provenance=current_source,
        )
        proof = verifier._proof_inventory(snapshot)
        return {
            "repository": verifier.REPOSITORY,
            "source_version": "test",
            "tests": {"file_count": 0, "declared_test_function_count": 0},
            "packages": {
                "workspace_member_count": 0,
                "production_included_count": 0,
            },
            "dashboard_routes": {"route_count": 0, "production_admitted_count": 0},
            "channels": {"registered_channel_count": 0},
            "mcp_tools": {"tool_count": 0},
            "connectors": {"source_connector_count": 0, "production_included": []},
            "installer_questions": {"consumer_question_count": 0},
            "proof_freshness": proof,
        }

    monkeypatch.setattr(verifier, "collect_production_code_inventory", minimal_inventory)
    assert verifier.main(["--root", str(repository), "--write"]) == 0

    # The next downstream governance views may be generated after the inventory.
    # Their exact files are excluded, while no release directory is excluded.
    for relative in (
        "release/claim-evidence-ledger.json",
        "release/release-state.json",
    ):
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"generated":true}\n', encoding="utf-8")

    current = generator.proof_provenance.repository_provenance(
        repository,
        exclude_paths=expected_exclusions,
    )
    assert current == manifest["source"]
    assert verifier.main(["--root", str(repository)]) == 0

    # A manifest cannot hide additional source behind a caller-chosen exclusion.
    manifest_path = repository / "proof/results/manifest.json"
    original_manifest = manifest_path.read_bytes()
    overbroad = json.loads(original_manifest)
    for key in ("source", "source_at_completion"):
        overbroad[key]["excluded_paths"] = sorted(
            [*overbroad[key]["excluded_paths"], "packages"]
        )
    manifest_path.write_bytes(
        (
            json.dumps(overbroad, indent=2, ensure_ascii=False, sort_keys=True)
            + "\n"
        ).encode("utf-8")
    )
    with pytest.raises(SystemExit, match="exact generated-artifact allow-list"):
        verifier.main(["--root", str(repository)])
    manifest_path.write_bytes(original_manifest)

    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", "generated proof and governance")
    _git(repository, "tag", "proof-artifact")
    committed = generator.proof_provenance.repository_provenance(
        repository,
        exclude_paths=expected_exclusions,
    )
    assert committed["revision"] != manifest["source"]["revision"]
    assert committed["worktree_state_sha256"] != manifest["source"][
        "worktree_state_sha256"
    ]
    assert committed["source_content"] == manifest["source"]["source_content"]
    assert verifier.main(["--root", str(repository)]) == 0

    relocated = tmp_path / "relocated"
    subprocess.run(
        ["git", "clone", "-q", "--no-local", str(repository), str(relocated)],
        check=True,
        capture_output=True,
    )
    _git(relocated, "checkout", "-q", "--detach", "proof-artifact")
    assert verifier.main(["--root", str(relocated)]) == 0
    relocated_manifest = json.loads(
        (relocated / "proof/results/manifest.json").read_text(encoding="utf-8")
    )
    assert all(
        not Path(run["command"]["argv"][-1]).is_absolute()
        for run in relocated_manifest["runs"]
    )

    # Mutation, addition, and deletion of non-excluded content all fail closed.
    harness_path = relocated / "proof/run_proof.py"
    original_harness = harness_path.read_bytes()
    harness_path.write_text(
        "# changed harness\n", encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="do not match current source content"):
        verifier.main(["--root", str(relocated)])
    harness_path.write_bytes(original_harness)
    assert verifier.main(["--root", str(relocated)]) == 0

    extra_source = relocated / "extra-source.py"
    extra_source.write_text("EXTRA = True\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="do not match current source content"):
        verifier.main(["--root", str(relocated)])
    extra_source.unlink()
    assert verifier.main(["--root", str(relocated)]) == 0

    source_fixture = relocated / "packages/maverick-core/maverick/source_fixture.py"
    original_source_fixture = source_fixture.read_bytes()
    source_fixture.unlink()
    with pytest.raises(SystemExit, match="do not match current source content"):
        verifier.main(["--root", str(relocated)])
    source_fixture.write_bytes(original_source_fixture)
    assert verifier.main(["--root", str(relocated)]) == 0
