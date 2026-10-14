from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from maverick import proof_provenance


def _git(root, *args):
    return subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "user.name=Proof Test",
            "-c",
            "user.email=proof@example.invalid",
            *args,
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _git_autocrlf(root, value, *args):
    return subprocess.run(
        [
            "git",
            "-c",
            f"core.autocrlf={value}",
            "-c",
            "user.name=Proof Test",
            "-c",
            "user.email=proof@example.invalid",
            *args,
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _stage_index_symlink(root: Path, relative: str, target: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(target, encoding="utf-8")
    oid = _git(root, "hash-object", "-w", relative).stdout.decode().strip()
    _git(
        root,
        "update-index",
        "--add",
        "--cacheinfo",
        f"120000,{oid},{relative}",
    )


def test_repository_provenance_binds_revision_diff_and_untracked_bytes(tmp_path):
    _git(tmp_path, "init", "-q")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-q", "-m", "base")

    clean = proof_provenance.repository_provenance(tmp_path)
    assert clean["available"] is True
    assert clean["dirty"] is False
    assert len(clean["revision"]) == 40
    assert len(clean["worktree_state_sha256"]) == 64
    assert proof_provenance.source_content_identity(clean)["path_count"] == 1
    git_runtime = clean["source_content_git_diagnostics"]["runtime"]
    assert Path(git_runtime["executable"]).is_absolute()
    assert git_runtime["trust"] == "trusted-host"
    assert git_runtime["hash_to_execution_atomic"] is False
    assert len(git_runtime["sha256"]) == 64

    tracked.write_text("changed\n", encoding="utf-8")
    (tmp_path / "untracked.bin").write_bytes(b"raw\x00bytes")
    dirty = proof_provenance.repository_provenance(tmp_path)
    assert dirty["dirty"] is True
    assert dirty["tracked_diff_bytes"] > 0
    assert dirty["untracked_file_count"] == 1
    assert dirty["worktree_state_sha256"] != clean["worktree_state_sha256"]
    assert dirty["source_content"] != clean["source_content"]

    (tmp_path / "untracked.bin").write_bytes(b"different")
    changed = proof_provenance.repository_provenance(tmp_path)
    assert changed["worktree_state_sha256"] != dirty["worktree_state_sha256"]


def test_trusted_git_completed_uses_bounded_read_only_queries(tmp_path):
    _git(tmp_path, "init", "-q")
    source = tmp_path / "source.py"
    source.write_text("SOURCE = True\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "source")

    listed = proof_provenance.trusted_git_completed(
        tmp_path,
        ("--literal-pathspecs", "ls-files", "--stage", "-z"),
    )
    assert listed.returncode == 0
    assert b"source.py" in listed.stdout
    source.write_text("SOURCE = False\n", encoding="utf-8")
    dirty = proof_provenance.trusted_git_completed(
        tmp_path,
        ("--literal-pathspecs", "diff", "--quiet", "--no-ext-diff"),
        accepted_returncodes=(0, 1),
    )
    assert dirty.returncode == 1
    with pytest.raises(RuntimeError, match="hard byte limit"):
        proof_provenance.trusted_git_completed(
            tmp_path,
            ("cat-file", "-p", "HEAD"),
            max_stdout_bytes=1,
        )


def test_trusted_git_completed_rejects_repository_supplied_git(monkeypatch, tmp_path):
    _git(tmp_path, "init", "-q")
    fake = tmp_path / ("git.exe" if os.name == "nt" else "git")
    fake.write_bytes(b"fake Git executable\n")
    if os.name != "nt":
        fake.chmod(0o755)
    monkeypatch.setattr(proof_provenance.shutil, "which", lambda _name: str(fake))

    with pytest.raises(RuntimeError, match="repository or cwd"):
        proof_provenance.trusted_git_completed(tmp_path, ("cat-file", "-e", "HEAD"))


def test_repository_provenance_is_independent_of_checkout_location(tmp_path):
    original = tmp_path / "original"
    relocated = tmp_path / "relocated"
    original.mkdir()
    _git(original, "init", "-q")
    (original / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(original, "add", "tracked.txt")
    _git(original, "commit", "-q", "-m", "base")
    (original / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (original / "untracked.bin").write_bytes(b"raw\x00bytes")

    shutil.copytree(original, relocated)

    first = proof_provenance.repository_provenance(original)
    second = proof_provenance.repository_provenance(relocated)
    assert "repository_root" not in first
    assert first == second


def test_repository_provenance_excludes_only_named_generated_outputs(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "source.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "source.py")
    _git(tmp_path, "commit", "-q", "-m", "base")
    generated = tmp_path / "proof" / "results"
    generated.mkdir(parents=True)
    (generated / "raw.txt").write_text("one", encoding="utf-8")

    first = proof_provenance.repository_provenance(
        tmp_path, exclude_paths=["proof/results"]
    )
    (generated / "raw.txt").write_text("two", encoding="utf-8")
    second = proof_provenance.repository_provenance(
        tmp_path, exclude_paths=["proof/results"]
    )
    assert first["dirty"] is False
    assert first["worktree_state_sha256"] == second["worktree_state_sha256"]
    assert first["source_content"] == second["source_content"]
    assert first["excluded_paths"] == ["proof/results"]


def test_proof_exclusions_break_only_the_generated_governance_cycle(tmp_path):
    _git(tmp_path, "init", "-q")
    source = tmp_path / "packages" / "core.py"
    harness = tmp_path / "proof" / "run_proof.py"
    source.parent.mkdir(parents=True)
    harness.parent.mkdir(parents=True)
    source.write_text("SOURCE = 1\n", encoding="utf-8")
    harness.write_text("print('proof')\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")

    results = tmp_path / "proof" / "RESULTS.md"
    retained = tmp_path / "proof" / "results"
    exclusions = proof_provenance.proof_source_exclusions(
        tmp_path,
        generated_paths=(results, retained),
    )
    assert exclusions == sorted(
        [
            *proof_provenance.DOWNSTREAM_GOVERNANCE_ARTIFACTS,
            "proof/RESULTS.md",
            "proof/results",
        ]
    )

    initial = proof_provenance.repository_provenance(
        tmp_path,
        exclude_paths=exclusions,
    )
    for index, relative in enumerate(
        proof_provenance.DOWNSTREAM_GOVERNANCE_ARTIFACTS
    ):
        generated = tmp_path / relative
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text(f"generated-{index}\n", encoding="utf-8")
    downstream_changed = proof_provenance.repository_provenance(
        tmp_path,
        exclude_paths=exclusions,
    )
    assert downstream_changed == initial

    harness.write_text("print('changed proof')\n", encoding="utf-8")
    harness_changed = proof_provenance.repository_provenance(
        tmp_path,
        exclude_paths=exclusions,
    )
    assert harness_changed["worktree_state_sha256"] != initial["worktree_state_sha256"]

    harness.write_text("print('proof')\n", encoding="utf-8")
    source.write_text("SOURCE = 2\n", encoding="utf-8")
    source_changed = proof_provenance.repository_provenance(
        tmp_path,
        exclude_paths=exclusions,
    )
    assert source_changed["worktree_state_sha256"] != initial["worktree_state_sha256"]


def test_source_content_survives_dirty_commit_and_relocation_but_detects_drift(
    tmp_path,
):
    original = tmp_path / "original"
    relocated = tmp_path / "relocated"
    original.mkdir()
    _git(original, "init", "-q")
    (original / ".gitattributes").write_text("*.txt text eol=lf\n", encoding="utf-8")
    (original / "changed.txt").write_text("base\n", encoding="utf-8")
    (original / "deleted.txt").write_text("delete me\n", encoding="utf-8")
    _git(original, "add", ".")
    _git(original, "commit", "-q", "-m", "base")

    # Git-clean hashing makes CRLF working bytes match the committed LF blobs.
    (original / "changed.txt").write_bytes(b"changed\r\n")
    (original / "deleted.txt").unlink()
    (original / "added.txt").write_bytes(b"new\r\n")
    dirty = proof_provenance.repository_provenance(original)
    dirty_identity = proof_provenance.source_content_identity(dirty)
    assert dirty["dirty"] is True

    _git(original, "add", "-A")
    _git(original, "commit", "-q", "-m", "materialize source state")
    committed = proof_provenance.repository_provenance(original)
    assert committed["dirty"] is False
    assert committed["revision"] != dirty["revision"]
    assert committed["worktree_state_sha256"] != dirty["worktree_state_sha256"]
    assert proof_provenance.source_content_identity(committed) == dirty_identity

    shutil.copytree(original, relocated)
    relocated_identity = proof_provenance.repository_source_content_identity(relocated)
    assert relocated_identity == dirty_identity

    (relocated / "changed.txt").write_text("mutated\n", encoding="utf-8")
    assert (
        proof_provenance.repository_source_content_identity(relocated)
        != relocated_identity
    )
    _git(relocated, "checkout", "--", "changed.txt")
    (relocated / "extra.py").write_text("EXTRA = True\n", encoding="utf-8")
    assert (
        proof_provenance.repository_source_content_identity(relocated)
        != relocated_identity
    )
    (relocated / "extra.py").unlink()
    (relocated / "added.txt").unlink()
    assert (
        proof_provenance.repository_source_content_identity(relocated)
        != relocated_identity
    )

    # Even a nonexistent extra exclusion changes the identity's declared scope.
    assert proof_provenance.repository_source_content_identity(
        original,
        exclude_paths=("generated",),
    ) != proof_provenance.repository_source_content_identity(
        original,
        exclude_paths=("generated", "not-present"),
    )
    with pytest.raises(ValueError, match="not repository-relative"):
        proof_provenance.repository_source_content_identity(
            original,
            exclude_paths=("../escape",),
        )


def test_unspecified_text_is_checkout_independent_for_tracked_and_untracked_paths(
    tmp_path,
):
    original = tmp_path / "windows-style"
    clone = tmp_path / "lf-clone"
    materialized = tmp_path / "materialized"
    original.mkdir()
    _git_autocrlf(original, "true", "init", "-q")
    tracked = original / "unspecified.txt"
    tracked.write_bytes(b"tracked\r\ntext\r\n")
    _git_autocrlf(original, "true", "add", "unspecified.txt")
    _git_autocrlf(original, "true", "commit", "-q", "-m", "normalize text")

    # The Windows-style checkout retains CRLF bytes, while the committed blob and
    # a clone made with autocrlf disabled contain LF.  Both must bind identically.
    assert tracked.read_bytes() == b"tracked\r\ntext\r\n"
    _git_autocrlf(tmp_path, "false", "clone", "-q", str(original), str(clone))
    assert (clone / "unspecified.txt").read_bytes() == b"tracked\ntext\n"
    assert proof_provenance.repository_source_content_identity(
        original
    ) == proof_provenance.repository_source_content_identity(clone)

    # Non-ignored untracked text uses the same deterministic clean rule.
    (original / "untracked.txt").write_bytes(b"untracked\r\ntext\r\n")
    (clone / "untracked.txt").write_bytes(b"untracked\ntext\n")
    assert proof_provenance.repository_source_content_identity(
        original
    ) == proof_provenance.repository_source_content_identity(clone)
    proof_provenance.materialize_repository_source(original, materialized)
    assert (materialized / "unspecified.txt").read_bytes() == b"tracked\ntext\n"
    assert (materialized / "untracked.txt").read_bytes() == b"untracked\ntext\n"


def test_source_content_binds_executable_and_materialized_symlink_modes(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "core.filemode", "false" if os.name == "nt" else "true")
    _git(tmp_path, "config", "core.symlinks", "false")
    executable = tmp_path / "executable.sh"
    materialized_link = tmp_path / "materialized-link"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    if os.name != "nt":
        executable.chmod(0o755)
    materialized_link.write_text("target.txt", encoding="utf-8")
    (tmp_path / "target.txt").write_text("target\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    if os.name == "nt":
        _git(tmp_path, "update-index", "--chmod=+x", "executable.sh")
    link_oid = _git(tmp_path, "hash-object", "materialized-link").stdout.decode().strip()
    _git(
        tmp_path,
        "update-index",
        "--add",
        "--cacheinfo",
        f"120000,{link_oid},materialized-link",
    )
    _git(tmp_path, "commit", "-q", "-m", "mode fixtures")
    original = proof_provenance.repository_source_content_identity(tmp_path)

    # Identical bytes with a different executable bit produce a different tree identity.
    if os.name == "nt":
        _git(tmp_path, "update-index", "--chmod=-x", "executable.sh")
    else:
        executable.chmod(0o644)
    executable_mode_changed = proof_provenance.repository_source_content_identity(tmp_path)
    assert executable_mode_changed != original
    if os.name != "nt":
        _git(tmp_path, "add", "executable.sh")
        assert (
            proof_provenance.repository_source_content_identity(tmp_path)
            == executable_mode_changed
        )
    _git(tmp_path, "commit", "-q", "-m", "regular executable")
    assert (
        proof_provenance.repository_source_content_identity(tmp_path)
        == executable_mode_changed
    )

    # The same materialized bytes as a regular file must not equal symlink mode 120000.
    _git(
        tmp_path,
        "update-index",
        "--cacheinfo",
        f"100644,{link_oid},materialized-link",
    )
    regular_mode = proof_provenance.repository_source_content_identity(tmp_path)
    assert regular_mode != executable_mode_changed
    _git(tmp_path, "commit", "-q", "-m", "regular materialized link")
    assert proof_provenance.repository_source_content_identity(tmp_path) == regular_mode


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable-mode semantics")
def test_core_filemode_false_rejects_unrepresentable_runtime_mode(tmp_path):
    _git(tmp_path, "init", "-q")
    source = tmp_path / "source.sh"
    source.write_text("#!/bin/sh\n", encoding="utf-8")
    source.chmod(0o644)
    _git(tmp_path, "add", "source.sh")
    _git(tmp_path, "commit", "-q", "-m", "non-executable")
    _git(tmp_path, "config", "core.filemode", "false")
    source.chmod(0o755)

    with pytest.raises(RuntimeError, match="differs from the runtime filesystem"):
        proof_provenance.repository_source_content_identity(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable-mode semantics")
def test_untracked_executable_requires_staging_before_source_binding(tmp_path):
    _git(tmp_path, "init", "-q")
    source = tmp_path / "untracked.sh"
    source.write_text("#!/bin/sh\n", encoding="utf-8")
    source.chmod(0o755)
    with pytest.raises(RuntimeError, match="must be staged"):
        proof_provenance.repository_source_content_identity(tmp_path)

    _git(tmp_path, "add", "untracked.sh")
    identity = proof_provenance.repository_source_content_identity(tmp_path)
    assert identity["path_count"] == 1


def test_clean_nested_repository_normalizes_to_gitlink_and_dirty_nested_fails(tmp_path):
    outer = tmp_path / "outer"
    nested = outer / "nested"
    outer.mkdir()
    _git(outer, "init", "-q")
    (outer / "outer.txt").write_text("outer\n", encoding="utf-8")
    _git(outer, "add", "outer.txt")
    _git(outer, "commit", "-q", "-m", "outer")
    nested.mkdir()
    _git(nested, "init", "-q")
    (nested / "nested.txt").write_text("nested\n", encoding="utf-8")
    _git(nested, "add", "nested.txt")
    _git(nested, "commit", "-q", "-m", "nested")

    before = proof_provenance.repository_source_content_identity(outer)
    diagnostic = proof_provenance.repository_provenance(outer)
    assert diagnostic["untracked_file_count"] == 1
    _git(outer, "add", "nested")
    _git(outer, "commit", "-q", "-m", "track nested as gitlink")
    initialized = proof_provenance.repository_source_content_identity(outer)
    assert initialized == before

    # A SHA-1 Gitlink pointer alone cannot stand in for recursively bound content.
    relocated = tmp_path / "relocated-uninitialized"
    shutil.copytree(
        outer,
        relocated,
        ignore=lambda directory, _names: {"nested"}
        if Path(directory).resolve() == outer.resolve()
        else set(),
    )
    with pytest.raises(RuntimeError, match="uninitialized Gitlink"):
        proof_provenance.repository_source_content_identity(relocated)

    (nested / "nested.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="nested Git repository is dirty"):
        proof_provenance.repository_source_content_identity(outer)
    _git(nested, "checkout", "--", "nested.txt")
    (nested / "nested.txt").write_text("new commit\n", encoding="utf-8")
    _git(nested, "add", "nested.txt")
    _git(nested, "commit", "-q", "-m", "advance nested")
    with pytest.raises(RuntimeError, match="tracked Gitlink HEAD differs"):
        proof_provenance.repository_source_content_identity(outer)


def test_source_content_extractor_rejects_schema_drift(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "source.py").write_text("SOURCE = True\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "source")
    provenance = proof_provenance.repository_provenance(tmp_path)
    malformed = {
        **provenance,
        "source_content": {**provenance["source_content"], "unexpected": True},
    }
    with pytest.raises(ValueError, match="source-content identity is malformed"):
        proof_provenance.source_content_identity(malformed)


def test_v2_rows_bind_direct_canonical_sha256_and_materialize_only_bound_source(
    tmp_path,
):
    repository = tmp_path / "repository"
    destination = tmp_path / "materialized"
    repository.mkdir()
    _git(repository, "init", "-q")
    (repository / ".gitattributes").write_text(
        "*.txt text eol=lf\n",
        encoding="utf-8",
    )
    (repository / ".gitignore").write_text(
        "sitecustomize.py\nproof/results/\n",
        encoding="utf-8",
    )
    (repository / "tracked.txt").write_bytes(b"tracked\r\n")
    _git(repository, "add", ".gitattributes", ".gitignore", "tracked.txt")
    _git(repository, "commit", "-q", "-m", "bound source")
    (repository / "visible.txt").write_bytes(b"visible\r\n")
    (repository / "sitecustomize.py").write_text("raise SystemExit('pwned')\n")
    generated = repository / "proof" / "results" / "raw.txt"
    generated.parent.mkdir(parents=True)
    generated.write_text("generated\n", encoding="utf-8")

    excluded = ("proof/results",)
    snapshot = proof_provenance._collect_source_snapshot(repository, excluded)
    rows = [proof_provenance._entry_identity_row(entry) for entry in snapshot.entries]
    tracked = next(row for row in rows if row["path"] == "tracked.txt")
    assert tracked == {
        "bytes": len(b"tracked\n"),
        "kind": "file",
        "mode": "100644",
        "path": "tracked.txt",
        "sha256": proof_provenance.sha256_bytes(b"tracked\n"),
    }
    assert "oid" not in tracked

    identity = proof_provenance.materialize_repository_source(
        repository,
        destination,
        exclude_paths=excluded,
    )
    assert identity == snapshot.identity
    assert (destination / "tracked.txt").read_bytes() == b"tracked\n"
    assert (destination / "visible.txt").read_bytes() == b"visible\n"
    assert not (destination / "sitecustomize.py").exists()
    assert not (destination / "proof/results").exists()
    assert not (destination / ".git").exists()


def test_canonical_payloads_do_not_alias_equal_git_oids_across_paths(
    monkeypatch,
    tmp_path,
):
    _git(tmp_path, "init", "-q")
    forced_oid = "1" * 40
    observed_object_directories: list[Path] = []

    def forced_hash(*_args, **_kwargs):
        return forced_oid.encode("ascii")

    def framed_payload(object_directory, oid, *, object_format):
        assert oid == forced_oid
        assert object_format == "sha1"
        observed_object_directories.append(object_directory)
        return (b"first", b"second")[int(object_directory.name)]

    monkeypatch.setattr(proof_provenance, "_git_with_input", forced_hash)
    monkeypatch.setattr(proof_provenance, "_read_isolated_blob", framed_payload)
    canonical = proof_provenance._canonical_blob_payloads(
        tmp_path,
        (),
        {"first-link": b"first", "second-link": b"second"},
        object_format="sha1",
    )

    assert canonical == {
        "first-link": (b"first", forced_oid),
        "second-link": (b"second", forced_oid),
    }
    assert len(set(observed_object_directories)) == 2


def test_collapse_filter_attack_is_rejected_before_git_oid_can_hide_bytes(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitattributes").write_text("*.txt filter=collapse\n", encoding="utf-8")
    (tmp_path / "first.txt").write_text(" \n", encoding="utf-8")
    (tmp_path / "second.txt").write_text("\n", encoding="utf-8")
    _git(tmp_path, "config", "filter.collapse.clean", "git stripspace")
    _git(tmp_path, "add", ".gitattributes")
    _git(tmp_path, "commit", "-q", "-m", "filter policy")

    first_oid = _git(tmp_path, "hash-object", "--filters", "first.txt").stdout.strip()
    second_oid = _git(tmp_path, "hash-object", "--filters", "second.txt").stdout.strip()
    assert first_oid == second_oid
    with pytest.raises(RuntimeError, match="local Git transform configuration"):
        proof_provenance.repository_source_content_identity(tmp_path)


@pytest.mark.parametrize(
    "attribute",
    ["ident", "working-tree-encoding=UTF-16", "crlf"],
)
def test_unapproved_repository_content_transforms_are_rejected(tmp_path, attribute):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitattributes").write_text(
        f"payload.txt {attribute}\n",
        encoding="utf-8",
    )
    (tmp_path / "payload.txt").write_text("payload\n", encoding="utf-8")
    _git(tmp_path, "add", ".gitattributes")
    _git(tmp_path, "commit", "-q", "-m", "attribute policy")
    with pytest.raises(RuntimeError, match="unapproved Git content transform"):
        proof_provenance.repository_source_content_identity(tmp_path)


def test_info_attributes_override_is_rejected(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "payload.txt").write_text("payload\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "source")
    info_attributes = tmp_path / ".git" / "info" / "attributes"
    info_attributes.write_text("*.txt ident\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="info/attributes overrides"):
        proof_provenance.repository_source_content_identity(tmp_path)


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_hidden_index_states_are_rejected(tmp_path, flag):
    _git(tmp_path, "init", "-q")
    (tmp_path / "source.py").write_text("SOURCE = True\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "source")
    _git(tmp_path, "update-index", flag, "source.py")
    with pytest.raises(RuntimeError, match="skip-worktree or assume-unchanged"):
        proof_provenance.repository_source_content_identity(tmp_path)


@pytest.mark.parametrize(
    "relative",
    ["CON.txt", "folder/name. ", "folder/EXAMPL~1.TXT", "folder/stream:name"],
)
def test_win32_alias_and_namespace_paths_are_rejected_portably(relative):
    with pytest.raises(RuntimeError, match="Win32"):
        proof_provenance._decode_git_path(relative.encode("utf-8"))


def test_non_nfc_git_path_is_rejected_portably():
    with pytest.raises(RuntimeError, match="NFC-normalized"):
        proof_provenance._decode_git_path("e\u0301.txt".encode("utf-8"))


def test_git_ignore_controls_cannot_hide_unbound_source(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "source.py").write_text("SOURCE = True\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "source")

    info_exclude = tmp_path / ".git/info/exclude"
    info_exclude.write_text("hidden.py\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="info/exclude overrides"):
        proof_provenance.repository_source_content_identity(tmp_path)
    info_exclude.write_text("# comments are inert\n", encoding="utf-8")

    external = tmp_path / "external-ignore"
    external.write_text("hidden.py\n", encoding="utf-8")
    _git(tmp_path, "config", "core.excludesFile", str(external))
    with pytest.raises(RuntimeError, match="local Git transform configuration"):
        proof_provenance.repository_source_content_identity(tmp_path)


def test_local_git_includes_cannot_import_unbound_transform_controls(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "source.py").write_text("SOURCE = True\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "source")
    external = tmp_path.parent / "external-proof-config"
    external.write_text("[core]\n\texcludesFile = hidden-ignore\n", encoding="utf-8")
    _git(tmp_path, "config", "include.path", str(external))

    with pytest.raises(RuntimeError, match="local Git transform configuration"):
        proof_provenance.repository_source_content_identity(tmp_path)


def test_worktree_git_config_cannot_import_unbound_ignore_controls(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "source.py").write_text("SOURCE = True\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "source")
    external = tmp_path.parent / "worktree-hidden-ignore"
    external.write_text("hidden.py\n", encoding="utf-8")
    _git(tmp_path, "config", "extensions.worktreeConfig", "true")
    _git(tmp_path, "config", "--worktree", "core.excludesFile", str(external))

    with pytest.raises(RuntimeError, match="local Git transform configuration"):
        proof_provenance.repository_source_content_identity(tmp_path)


def test_hidden_gitignore_in_traversable_parent_is_rejected(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text(
        "hidden-control/.gitignore\n",
        encoding="utf-8",
    )
    control = tmp_path / "hidden-control"
    control.mkdir()
    (control / ".gitignore").write_text("*.py\n", encoding="utf-8")
    (control / "hidden.py").write_text("HIDDEN = True\n", encoding="utf-8")
    (tmp_path / "source.py").write_text("SOURCE = True\n", encoding="utf-8")
    _git(tmp_path, "add", ".gitignore", "source.py")
    _git(tmp_path, "commit", "-q", "-m", "source")

    with pytest.raises(RuntimeError, match="ignored, unbound .gitignore"):
        proof_provenance.repository_source_content_identity(tmp_path)


def test_gitignore_inside_already_ignored_parent_is_inert(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text(".pytest_cache/\nwork/\n", encoding="utf-8")
    (tmp_path / "source.py").write_text("SOURCE = True\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "source")
    cache = tmp_path / ".pytest_cache"
    cache.mkdir()
    (cache / ".gitignore").write_text("*\n", encoding="utf-8")
    ignored_nested_repo = tmp_path / "work" / "repo"
    ignored_nested_repo.mkdir(parents=True)
    _git(ignored_nested_repo, "init", "-q")

    identity = proof_provenance.repository_source_content_identity(tmp_path)
    assert identity["schema"] == proof_provenance.SOURCE_CONTENT_IDENTITY_SCHEMA


def test_materialized_tree_identity_survives_readonly_transition(tmp_path):
    tree = tmp_path / "tree"
    nested = tree / "nested"
    nested.mkdir(parents=True)
    (tree / "source.py").write_text("SOURCE = True\n", encoding="utf-8")
    (nested / "payload.bin").write_bytes(b"payload\x00bytes")

    before = proof_provenance.materialized_tree_identity(tree)
    proof_provenance.make_materialized_tree_read_only(tree)
    after = proof_provenance.materialized_tree_identity(tree)

    assert before == after
    assert before["schema"] == proof_provenance.MATERIALIZED_TREE_IDENTITY_SCHEMA
    assert before["path_count"] == 3


def test_materialized_tree_identity_rejects_dangling_symlink(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    link = tree / "dangling"
    try:
        link.symlink_to("missing")
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")

    with pytest.raises(RuntimeError, match="dangling or outside"):
        proof_provenance.materialized_tree_identity(tree)


def test_materialized_tree_identity_rejects_external_hardlink(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    external = tmp_path / "external.py"
    external.write_text("VALUE = 1\n", encoding="utf-8")
    linked = tree / "source.py"
    try:
        os.link(external, linked)
    except OSError:
        pytest.skip("hardlink creation is unavailable on this host")

    with pytest.raises(RuntimeError, match="not a regular file"):
        proof_provenance.materialized_tree_identity(tree)


def test_materialization_requires_executed_source_to_be_bound(tmp_path):
    repository = tmp_path / "repository"
    destination = tmp_path / "materialized"
    repository.mkdir()
    _git(repository, "init", "-q")
    (repository / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (repository / "source.py").write_text("SOURCE = True\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "bound source")
    (repository / "ignored.py").write_text("EXECUTED = True\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="not a bound regular file"):
        proof_provenance.materialize_repository_source(
            repository,
            destination,
            required_regular_paths=("ignored.py",),
        )


def test_materialization_rejects_destination_inside_source_or_git_metadata(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "source.py").write_text("SOURCE = True\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "source")

    for destination in (tmp_path / "copy", tmp_path / ".git" / "copy"):
        with pytest.raises(RuntimeError, match="inside source or Git metadata"):
            proof_provenance.materialize_repository_source(tmp_path, destination)


def test_materialized_symlink_directoryness_is_derived_from_bound_tree(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "core.symlinks", "false")
    target = tmp_path / "target-dir" / "payload.txt"
    target.parent.mkdir()
    target.write_text("payload\n", encoding="utf-8")
    link = tmp_path / "directory-link"
    link.write_text("target-dir", encoding="utf-8")
    _git(tmp_path, "add", ".")
    oid = _git(tmp_path, "hash-object", "directory-link").stdout.decode().strip()
    _git(
        tmp_path,
        "update-index",
        "--add",
        "--cacheinfo",
        f"120000,{oid},directory-link",
    )
    _git(tmp_path, "commit", "-q", "-m", "directory symlink fixture")

    snapshot = proof_provenance._collect_source_snapshot(tmp_path, ())
    row = next(
        proof_provenance._entry_identity_row(entry)
        for entry in snapshot.entries
        if entry.path == "directory-link"
    )
    assert row["kind"] == "symlink"
    assert row["target_kind"] == "directory"


def test_windows_policy_copies_direct_bound_file_symlink(monkeypatch, tmp_path):
    repository = tmp_path / "repository"
    destination = tmp_path / "materialized"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "core.symlinks", "false")
    target = repository / "canonical.md"
    target.write_bytes(b"canonical\r\nbytes\x00")
    _git(repository, "add", "canonical.md")
    _stage_index_symlink(repository, "docs/alias.md", "../canonical.md")
    _git(repository, "commit", "-q", "-m", "file symlink")
    monkeypatch.setattr(
        proof_provenance,
        "_copy_bound_file_symlinks_for_platform",
        lambda: True,
    )

    expected = proof_provenance.repository_source_content_identity(repository)
    observed = proof_provenance.materialize_repository_source(
        repository,
        destination,
        symlink_materialization_policy=(
            proof_provenance.SYMLINK_MATERIALIZATION_POLICY
        ),
    )

    copied = destination / "docs/alias.md"
    assert observed == expected
    assert copied.is_file() and not copied.is_symlink()
    assert copied.read_bytes() == (destination / "canonical.md").read_bytes()
    assert not (
        copied.lstat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    )


@pytest.mark.parametrize(
    "target_kind",
    ["directory", "dangling", "excluded", "chain", "executable"],
)
def test_windows_policy_rejects_ambiguous_symlink_targets(
    monkeypatch,
    tmp_path,
    target_kind,
):
    repository = tmp_path / "repository"
    destination = tmp_path / "materialized"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "core.symlinks", "false")
    exclude_paths: tuple[str, ...] = ()
    if target_kind == "directory":
        target = repository / "target-dir/payload.txt"
        target.parent.mkdir()
        target.write_text("payload\n", encoding="utf-8")
        _git(repository, "add", "target-dir/payload.txt")
        _stage_index_symlink(repository, "alias", "target-dir")
    elif target_kind == "dangling":
        _stage_index_symlink(repository, "alias", "missing.txt")
    elif target_kind == "excluded":
        target = repository / "excluded.txt"
        target.write_text("excluded\n", encoding="utf-8")
        _git(repository, "add", "excluded.txt")
        _stage_index_symlink(repository, "alias", "excluded.txt")
        exclude_paths = ("excluded.txt",)
    elif target_kind == "chain":
        target = repository / "target.txt"
        target.write_text("target\n", encoding="utf-8")
        _git(repository, "add", "target.txt")
        _stage_index_symlink(repository, "chain-b", "target.txt")
        _stage_index_symlink(repository, "chain-a", "chain-b")
    else:
        target = repository / "executable.sh"
        target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        _git(repository, "add", "executable.sh")
        _git(repository, "update-index", "--chmod=+x", "executable.sh")
        _stage_index_symlink(repository, "alias", "executable.sh")
    _git(repository, "commit", "-q", "-m", "ambiguous symlink")
    monkeypatch.setattr(
        proof_provenance,
        "_copy_bound_file_symlinks_for_platform",
        lambda: True,
    )

    with pytest.raises(RuntimeError, match="symlink target|direct bound"):
        proof_provenance.materialize_repository_source(
            repository,
            destination,
            exclude_paths=exclude_paths,
        )


@pytest.mark.skipif(os.name == "nt", reason="native symlink regression is POSIX-only")
def test_posix_policy_preserves_native_file_symlink(tmp_path):
    repository = tmp_path / "repository"
    destination = tmp_path / "materialized"
    repository.mkdir()
    _git(repository, "init", "-q")
    target = repository / "canonical.md"
    target.write_text("canonical\n", encoding="utf-8")
    alias = repository / "docs/alias.md"
    alias.parent.mkdir()
    alias.symlink_to("../canonical.md")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "native symlink")

    expected = proof_provenance.repository_source_content_identity(repository)
    observed = proof_provenance.materialize_repository_source(repository, destination)
    copied_alias = destination / "docs/alias.md"
    assert observed == expected
    assert copied_alias.is_symlink()
    assert os.readlink(copied_alias) == "../canonical.md"


def test_semantic_execution_tree_identity_is_portable_and_exact(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "source.py").write_bytes(b"VALUE = 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "semantic source")
    source_content = proof_provenance.repository_source_content_identity(tmp_path)

    identity = proof_provenance.semantic_execution_tree_identity(source_content)
    basis = {
        key: value
        for key, value in identity.items()
        if key not in {"sha256", "digest_algorithm", "digest_scope"}
    }

    assert identity["schema"] == (
        proof_provenance.SEMANTIC_EXECUTION_TREE_IDENTITY_SCHEMA
    )
    assert identity["source_content"] == source_content
    assert identity["sha256"] == proof_provenance.sha256_bytes(
        proof_provenance.canonical_json_bytes(basis)
    )
    assert not any("root" in key or "path" in key for key in identity)
    with pytest.raises(ValueError, match="source-content identity is malformed"):
        proof_provenance.semantic_execution_tree_identity(
            {**source_content, "unexpected": True}
        )
    with pytest.raises(ValueError, match="unsupported source symlink"):
        proof_provenance.semantic_execution_tree_identity(
            source_content,
            symlink_materialization_policy="host-dependent",
        )


@pytest.mark.skipif(os.name != "nt", reason="suspended-spawn regression is Windows-only")
def test_windows_process_tree_attach_precedes_child_execution(tmp_path):
    marker = tmp_path / "root-executed.txt"
    with proof_provenance.process_tree_controller() as controller:
        process = subprocess.Popen(
            [
                os.fsdecode(sys.executable),
                "-c",
                "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')",
                str(marker),
            ],
            **controller.popen_kwargs,
        )
        assert not marker.exists()
        controller.attach(process)
        assert process.wait(timeout=10) == 0
        seal = controller.seal(process)
    assert seal.clean is True
    assert seal.descendant_survived is False
    assert seal.control_failure is None
    assert marker.read_text(encoding="utf-8") == "ran"


def test_output_digest_is_recomputable_from_inline_payload():
    payload = {"status": "PASS", "data": {"b": 2, "a": 1}}
    record = proof_provenance.output_digest(payload)
    assert record["sha256"] == proof_provenance.sha256_bytes(
        proof_provenance.canonical_json_bytes(payload)
    )
    assert record["serialization"] == "canonical-json-sort-keys-v1"
