"""Host-git RCE hardening (PLATFORM_IMPROVEMENT_AUDIT.md W2-1/W2-2/W2-3/W2-4).

A workdir the agent can write to (write_file / SEARCH-REPLACE) or that came
from cloning an untrusted repo can carry a `.git/config` `core.fsmonitor`
hook or a `[filter "x"] smudge` command. Git executes that command during
`diff`/`add`/`reset`/`worktree` operations, independent of
`--no-ext-diff`/`--no-textconv`. These tests prove:

  - `git_metadata_may_execute_filters` / `git_env_without_user_config` are
    shared from one module (`git_safety.py`), not reimplemented per caller.
  - `edit_format.render_diff`, `reviewer.get_diff`, and
    `coding_mode.evaluate_candidate` all refuse to touch a workdir carrying
    such metadata, and never let a repo-local `core.fsmonitor` hook run.
  - `write_file` / `str_replace_editor` / `ast_edit` (via
    `tools.fs.write_text_contained`) and `apply_patch` refuse to write
    anywhere under `.git/`, in ORDINARY (non-benchmark) operation -- the
    prior guard only fired under `MAVERICK_BENCHMARK_OPAQUE` + coding mode.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _make_git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "commit.gpgsign", "false"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "seed.py").write_text("print('seed')\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "seed.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "seed"], check=True)
    return tmp_path


def _plant_fsmonitor_hook(repo: Path) -> Path:
    """Configure `core.fsmonitor` to a script that writes a marker file, and
    return the marker path. If any test's fix regresses, the marker appears."""
    marker = repo / "fsmonitor-ran"
    hook = repo / ".git" / "fsmon-hook"
    hook.write_text(f"#!/bin/sh\necho pwned > {marker}\nexit 0\n")
    hook.chmod(0o755)
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.fsmonitor", str(hook)], check=True
    )
    return marker


class _SB:
    def __init__(self, workdir):
        self.workdir = str(workdir)


class TestGitSafetyShared:
    def test_metadata_check_flags_fsmonitor(self, tmp_path):
        from maverick.git_safety import git_metadata_may_execute_filters
        repo = _make_git_repo(tmp_path)
        subprocess.run(
            ["git", "-C", str(repo), "config", "core.fsmonitor", ".git/whatever"],
            check=True,
        )
        assert git_metadata_may_execute_filters(repo) is True

    def test_metadata_check_clean_repo_is_safe(self, tmp_path):
        from maverick.git_safety import git_metadata_may_execute_filters
        repo = _make_git_repo(tmp_path)
        assert git_metadata_may_execute_filters(repo) is False

    def test_partial_clone_filter_is_not_a_false_positive(self, tmp_path):
        """`partialclonefilter = blob:none` (from --filter=blob:none) must not
        trip the guard -- it never runs a command."""
        from maverick.git_safety import git_metadata_may_execute_filters
        repo = _make_git_repo(tmp_path)
        config = repo / ".git" / "config"
        config.write_text(
            config.read_text() + "\n[remote \"origin\"]\n\tpartialclonefilter = blob:none\n"
        )
        assert git_metadata_may_execute_filters(repo) is False

    def test_scrubbed_env_disables_repo_local_fsmonitor(self, tmp_path):
        from maverick.git_safety import git_env_without_user_config
        env = git_env_without_user_config()
        assert env["GIT_CONFIG_KEY_0"] == "core.fsmonitor"
        assert env["GIT_CONFIG_VALUE_0"] == "false"
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"

    def test_orchestrator_reexports_same_functions(self):
        """orchestrator.py's private names must be the git_safety module's
        functions, not a second reimplementation."""
        from maverick import git_safety, orchestrator
        assert orchestrator._git_metadata_may_execute_filters is git_safety.git_metadata_may_execute_filters
        assert orchestrator._git_env_without_user_config is git_safety.git_env_without_user_config


class TestRenderDiffRefusesUnsafeMetadata:
    def test_render_diff_does_not_run_fsmonitor_hook(self, tmp_path):
        from maverick.edit_format import render_diff
        repo = _make_git_repo(tmp_path)
        marker = _plant_fsmonitor_hook(repo)
        (repo / "seed.py").write_text("print('changed')\n")

        diff = render_diff(repo, paths=["seed.py"])

        assert diff == ""
        assert not marker.exists()

    def test_render_diff_still_works_on_a_clean_repo(self, tmp_path):
        from maverick.edit_format import render_diff
        repo = _make_git_repo(tmp_path)
        (repo / "seed.py").write_text("print('changed')\n")

        diff = render_diff(repo, paths=["seed.py"])

        assert "seed.py" in diff
        assert "+print('changed')" in diff

    def test_apply_blocks_refuses_dotgit_target(self, tmp_path):
        from maverick.edit_format import SearchReplaceBlock, apply_blocks
        repo = _make_git_repo(tmp_path)
        blk = SearchReplaceBlock(path=".git/config", search="", replace="[core]\n\tfsmonitor = pwn\n")
        summary = apply_blocks([blk], repo)
        assert not summary.ok
        assert "never a valid edit target" in summary.results[0].reason
        config_text = (repo / ".git" / "config").read_text()
        assert "fsmonitor" not in config_text


class TestReviewerGetDiffRefusesUnsafeMetadata:
    def test_get_diff_does_not_run_fsmonitor_hook(self, tmp_path):
        from maverick.reviewer import get_diff
        repo = _make_git_repo(tmp_path)
        marker = _plant_fsmonitor_hook(repo)
        (repo / "seed.py").write_text("print('changed')\n")

        diff = get_diff(repo)

        assert diff == ""
        assert not marker.exists()

    def test_get_diff_still_works_on_a_clean_repo(self, tmp_path):
        from maverick.reviewer import get_diff
        repo = _make_git_repo(tmp_path)
        (repo / "seed.py").write_text("print('changed')\n")

        diff = get_diff(repo)

        assert "seed.py" in diff


class TestEvaluateCandidateRefusesUnsafeMetadata:
    def test_evaluate_candidate_refuses_when_fsmonitor_present(self, tmp_path):
        import asyncio

        from maverick.coding_mode import CodingModeConfig, evaluate_candidate

        repo = _make_git_repo(tmp_path)
        marker = _plant_fsmonitor_hook(repo)
        # Patch a non-test file so the (separate) anti-cheat boundary guard
        # doesn't preempt the check this test targets.
        patch = (
            "diff --git a/seed.py b/seed.py\n"
            "--- a/seed.py\n+++ b/seed.py\n"
            "@@ -1 +1 @@\n-print('seed')\n+print('patched')\n"
        )
        cfg = CodingModeConfig(enabled=True, fail_to_pass=["tests/test_seed.py::test_a"])

        cand = asyncio.run(evaluate_candidate(patch, repo, cfg, _SB(repo), 0))

        assert cand.apply_check_passed is False
        assert "unsafe git filter" in cand.error
        assert not marker.exists()


class TestWriteFileRefusesDotGitOutsideBenchmarkMode:
    def test_write_file_refuses_git_config_in_normal_mode(self, tmp_path, monkeypatch):
        from maverick.tools.fs import write_file
        monkeypatch.delenv("MAVERICK_BENCHMARK_OPAQUE", raising=False)
        monkeypatch.delenv("MAVERICK_CODING_MODE", raising=False)
        repo = _make_git_repo(tmp_path)

        out = write_file(_SB(repo)).fn({
            "path": ".git/config",
            "content": "[core]\n\tfsmonitor = /tmp/pwn.sh\n",
        })

        assert out.startswith("ERROR")
        assert "fsmonitor" not in (repo / ".git" / "config").read_text()

    def test_write_file_still_writes_ordinary_files(self, tmp_path, monkeypatch):
        from maverick.tools.fs import write_file
        monkeypatch.delenv("MAVERICK_BENCHMARK_OPAQUE", raising=False)
        monkeypatch.delenv("MAVERICK_CODING_MODE", raising=False)
        repo = _make_git_repo(tmp_path)

        out = write_file(_SB(repo)).fn({"path": "new.py", "content": "print(1)\n"})

        assert out.startswith("wrote")
        assert (repo / "new.py").read_text() == "print(1)\n"


class TestApplyPatchRefusesDotGitTarget:
    def test_apply_patch_refuses_git_config_target(self, tmp_path):
        from maverick.tools.apply_patch import apply_patch
        repo = _make_git_repo(tmp_path)
        patch = (
            "diff --git a/.git/config b/.git/config\n"
            "--- a/.git/config\n+++ b/.git/config\n"
            "@@ -1 +1,2 @@\n [core]\n+\tfsmonitor = /tmp/pwn.sh\n"
        )

        out = apply_patch(_SB(repo)).fn({"patch": patch})

        assert "ERROR" in out
        assert "fsmonitor" not in (repo / ".git" / "config").read_text()
