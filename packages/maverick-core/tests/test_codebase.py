"""Code vault and feature swarms, over real git repositories."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from maverick import ifc
from maverick.codebase import features, vault
from maverick.world_model import WorldModel

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")

SECRET_LINE = "def pricing_model(): return 0.4217  # proprietary"  # pragma: allowlist secret


def _sh(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / ".maverick"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    src = tmp_path / "upstream"
    src.mkdir()
    _sh("init", "-q", "-b", "main", cwd=src)
    (src / "app.py").write_text(SECRET_LINE + "\n")
    (src / "test_app.py").write_text("import app\n\ndef test_price():\n"
                                     "    assert app.pricing_model() > 0\n")
    _sh("add", "-A", cwd=src)
    _sh("commit", "-q", "-m", "init", cwd=src)
    return src


class _Sandbox:
    def __init__(self, workdir):
        self.workdir = workdir

    def exec(self, cmd, timeout=None):
        done = subprocess.run(cmd.split(), cwd=self.workdir, capture_output=True, text=True)
        return type("R", (), {"stdout": done.stdout, "stderr": done.stderr,
                              "exit_code": done.returncode})()


def _swarm(edit):
    """Stand-in for the governed swarm: edits the working copy it is handed and
    records the information-flow labels its run started with."""
    seen = {}

    def run(goal_id, work):
        seen["tracker"] = ifc.tracker_for_run()
        seen["goal_id"] = goal_id
        edit(work)
    return run, seen


def test_register_seals_the_repo_and_checkout_is_temporary(upstream, monkeypatch):
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    repo = vault.register("pricing", str(upstream), test_command="python -m pytest -q")
    assert repo.default_branch == "main" and len(repo.head) == 40
    stored = vault._bundle_path("pricing").read_bytes()
    assert b"pricing_model" not in stored and b"# v2 git bundle" not in stored
    with vault.checkout("pricing") as work:
        assert (work / "app.py").read_text().startswith("def pricing_model")
        kept = work
    assert not kept.exists()
    with pytest.raises(vault.VaultError):
        vault.register("pricing", str(upstream))
    with pytest.raises(vault.VaultError):
        vault.register("x", "https://user:pw@example.com/r.git")  # pragma: allowlist secret


def test_feature_swarm_runs_labelled_and_waits_for_approval(upstream, tmp_path):
    vault.register("pricing", str(upstream), test_command="python -m pytest -q", protect="block")
    world = WorldModel(tmp_path / "world.db")

    def edit(work):
        (work / "app.py").write_text(SECRET_LINE + "\n\ndef discount(): return 0.1\n")
        (work / "test_app.py").write_text((work / "test_app.py").read_text()
                                          + "\ndef test_discount():\n"
                                          "    assert app.discount() == 0.1\n")
    run, seen = _swarm(edit)
    reviews = []
    p = features.propose(world, "pricing", "Add a discount", "10% discount helper", run,
                         reviewer=lambda brief, diff: reviews.append(diff)
                         or {"approves": True, "confidence": 0.9, "blockers": 0, "comments": []},
                         sandbox_factory=_Sandbox)
    # the run was labelled with the code and enforced at the repo's protection
    assert seen["tracker"].base_labels == {"code:pricing"} and seen["tracker"].mode == "block"
    assert world.get_goal(seen["goal_id"]).title == "[pricing] Add a discount"
    assert p["status"] == "pending" and p["files"] == ["app.py", "test_app.py"]
    assert p["tests"]["passed"] is True and "discount" in reviews[0]
    appr = world.get_approval(p["approval_id"])
    assert appr.action == "codebase_merge" and "Tests: PASSED" in appr.detail
    # nothing merges or pushes without a human
    with pytest.raises(vault.VaultError, match="must approve"):
        features.merge(world, p["id"])
    head_before = vault.get("pricing").head
    world.decide_approval(p["approval_id"], "approved", decided_by="user:lead")
    merged = features.merge(world, p["id"])
    assert merged["status"] == "merged" and vault.get("pricing").head != head_before
    with vault.checkout("pricing") as work:
        assert "def discount" in (work / "app.py").read_text()
        log = subprocess.run(["git", "log", "--format=%s", "-1"], cwd=work,
                             capture_output=True, text=True).stdout
        assert log.startswith(f"Merge Lightwork proposal {p['id']}")
    # the upstream is untouched until an approved push
    assert "discount" not in (upstream / "app.py").read_text()
    assert features.push(world, p["id"]) == p["branch"]
    branches = subprocess.run(["git", "branch", "--list", "lightwork/*"], cwd=upstream,
                              capture_output=True, text=True).stdout
    assert p["branch"] in branches


def test_rejected_or_empty_proposals_never_touch_the_vault(upstream, tmp_path):
    vault.register("pricing", str(upstream))
    world = WorldModel(tmp_path / "world.db")
    run, _ = _swarm(lambda work: (work / "notes.md").write_text("idea\n"))
    p = features.propose(world, "pricing", "Notes", "", run)
    world.decide_approval(p["approval_id"], "denied", decided_by="user:lead")
    assert features.status(world, features.find(p["id"])) == "rejected"
    with pytest.raises(vault.VaultError, match="rejected"):
        features.merge(world, p["id"])
    nothing, _ = _swarm(lambda work: None)
    empty = features.propose(world, "pricing", "No-op", "", nothing)
    assert empty["status"] == "empty" and empty["approval_id"] is None
    assert [q["id"] for q in features.list_proposals("pricing")] == [empty["id"], p["id"]]
    assert "diff" not in features.list_proposals()[0]


def test_failing_tests_are_reported_to_the_approver(upstream, tmp_path):
    vault.register("pricing", str(upstream), test_command="python -m pytest -q")
    world = WorldModel(tmp_path / "world.db")
    run, _ = _swarm(lambda work: (work / "app.py").write_text("def pricing_model(): return -1\n"))
    p = features.propose(world, "pricing", "Break it", "", run, sandbox_factory=_Sandbox)
    assert p["tests"]["passed"] is False
    assert "Tests: FAILED" in world.get_approval(p["approval_id"]).detail
    assert "assert" in features.find(p["id"])["tests_output"]


def test_a_run_that_plants_git_hooks_is_discarded(upstream, tmp_path):
    vault.register("pricing", str(upstream))
    world = WorldModel(tmp_path / "world.db")

    def evil(work):
        with open(work / ".git" / "config", "a") as fh:
            fh.write('[filter "x"]\n\tclean = touch /tmp/pwned\n')
        (work / ".gitattributes").write_text("* filter=x\n")
    run, _ = _swarm(evil)
    with pytest.raises(vault.VaultError, match="filter"):
        features.propose(world, "pricing", "Evil", "", run)
    assert features.list_proposals("pricing") == []


def test_sync_brings_new_upstream_commits_and_keeps_lightwork_branches(upstream, tmp_path):
    vault.register("pricing", str(upstream))
    world = WorldModel(tmp_path / "world.db")
    run, _ = _swarm(lambda work: (work / "x.py").write_text("x = 1\n"))
    p = features.propose(world, "pricing", "X", "", run)
    world.decide_approval(p["approval_id"], "approved", decided_by="user:lead")
    features.merge(world, p["id"])
    (upstream / "README.md").write_text("upstream change\n")
    _sh("add", "-A", cwd=upstream)
    _sh("commit", "-q", "-m", "readme", cwd=upstream)
    vault.sync("pricing")
    with vault.checkout("pricing") as work:
        refs = subprocess.run(["git", "for-each-ref", "--format=%(refname)"], cwd=work,
                              capture_output=True, text=True).stdout
        assert (work / "README.md").exists()
    assert "refs/heads/lightwork/" in refs
    out = vault.export("pricing", tmp_path / "exported")
    assert (Path(out) / "README.md").exists()


def test_cli_end_to_end_with_approval_in_the_middle(upstream, tmp_path, monkeypatch):
    import maverick.cli as cli_mod
    import maverick.cli._code_groups as groups
    from click.testing import CliRunner
    from maverick import config
    from maverick.cli import main

    run = CliRunner().invoke
    monkeypatch.setattr(config, "get_codebase",
                        lambda: {"enable": False, "default_protect": "block"})
    assert run(main, ["code", "list"]).exit_code != 0            # off by default
    monkeypatch.setattr(config, "get_codebase",
                        lambda: {"enable": True, "default_protect": "approve"})
    db = tmp_path / "w.db"
    monkeypatch.setattr(groups, "open_world", lambda *a, **k: WorldModel(db))
    monkeypatch.setattr(cli_mod, "_require_llm_key", lambda *a, **k: "k")
    run_fn, _ = _swarm(lambda work: (work / "feature.py").write_text("ok = True\n"))
    monkeypatch.setattr(features, "default_runner", lambda **k: run_fn)

    out = run(main, ["code", "add", "pricing", str(upstream)])
    assert out.exit_code == 0 and "protect=approve" in out.output, out.output
    assert "pricing" in run(main, ["code", "list"]).output
    out = run(main, ["code", "feature", "pricing", "Add flag", "--no-review"])
    assert out.exit_code == 0 and "awaiting approval #1" in out.output, out.output
    pid = out.output.split()[1]
    assert "pending" in run(main, ["code", "proposals"]).output
    assert "+ok = True" in run(main, ["code", "show", pid]).output
    WorldModel(db).decide_approval(1, "approved", decided_by="user:lead")
    out = run(main, ["code", "merge", "--approved"])
    assert f"{pid} (pricing): merged" in out.output, out.output
    assert run(main, ["code", "merge"]).exit_code != 0
    out = run(main, ["code", "export", "pricing", str(tmp_path / "exp")])
    assert out.exit_code == 0 and (tmp_path / "exp" / "feature.py").exists()
