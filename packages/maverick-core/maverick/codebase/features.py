"""Swarms that add and update features in vaulted code -- and never ship alone.

``propose`` runs a goal against a private working copy on a new branch, labelled
``code:<repo>`` and enforced at the repository's protection mode so the code
cannot leave through an external tool. It then:

1. collects the change (branch commits and a bounded diff);
2. runs the repository's test command through the sandbox;
3. runs the independent code reviewer over the diff, when one is supplied;
4. seals the proposal (diff, tests, review, branch bundle) and parks a
   ``codebase_merge`` approval in the real approvals queue.

Nothing reaches the vault's default branch or any remote until a person
approves: ``merge`` needs the approval, ``push`` needs it too. A denied approval
leaves the vault untouched.
"""
from __future__ import annotations

import json
import re
import secrets
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from ..file_lock import atomic_write_bytes, cross_process_lock
from ..git_safety import git_metadata_may_execute_filters
from . import vault
from .vault import VaultError, _git

MAX_DIFF_CHARS = 200_000
MAX_TEST_OUTPUT_CHARS = 20_000
Runner = Callable[[int, Path], object]
Reviewer = Callable[[str, str], dict]


def _proposal_dir(name: str) -> Path:
    return vault._root(name, "proposals")


def _save(p: dict) -> None:
    body = json.dumps(p, sort_keys=True).encode()
    atomic_write_bytes(_proposal_dir(p["repo"]) / f"{p['id']}.json",
                       vault._seal(body, f"codebase/{p['repo']}/proposal/{p['id']}"))


def _load(name: str, pid: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{16}", pid):
        raise VaultError("malformed proposal id")
    try:
        raw = (_proposal_dir(name) / f"{pid}.json").read_bytes()
    except FileNotFoundError:
        raise VaultError(f"no proposal {pid} for {name!r}") from None
    return json.loads(vault._unseal(raw, f"codebase/{name}/proposal/{pid}"))


def find(pid: str) -> dict:
    for repo in vault.list_repos():
        if (_proposal_dir(repo.name) / f"{pid}.json").exists():
            return _load(repo.name, pid)
    raise VaultError(f"no proposal {pid}")


def list_proposals(name: str | None = None) -> list[dict]:
    out = []
    for repo in vault.list_repos():
        if name and repo.name != name:
            continue
        d = _proposal_dir(repo.name)
        for f in sorted(d.glob("*.json")) if d.exists() else ():
            p = _load(repo.name, f.stem)
            out.append({k: v for k, v in p.items() if k not in ("diff", "tests_output")})
    return sorted(out, key=lambda p: p["created_at"], reverse=True)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "change"


def _brief(repo: vault.Repo, title: str, description: str) -> str:
    tests = (f"The test command is `{repo.test_command}`; run it and make it pass."
             if repo.test_command else "Add or update tests for the change where the repo has them.")
    return (f"Implement this change in the `{repo.name}` repository (your working directory "
            f"is a checkout of branch `{repo.default_branch}` on a new feature branch).\n\n"
            f"{title}\n\n{description}\n\n{tests} Edit files in place; do not push, publish "
            "or send the code anywhere -- a human reviews and merges your change.")


def run_tests(repo: vault.Repo, work: Path, sandbox_factory=None) -> dict | None:
    if not repo.test_command:
        return None
    if sandbox_factory is None:
        from ..sandbox import build_sandbox
        sandbox_factory = build_sandbox
    result = sandbox_factory(workdir=str(work)).exec(repo.test_command, timeout=1800)
    output = (result.stdout or "") + (result.stderr or "")
    return {"command": repo.test_command, "exit_code": result.exit_code,
            "passed": result.exit_code == 0, "output": output[-MAX_TEST_OUTPUT_CHARS:]}


def propose(world, name: str, title: str, description: str, run: Runner, *,
            reviewer: Reviewer | None = None, sandbox_factory=None) -> dict:
    """Run a feature swarm and park its change for human approval."""
    from ..ifc import labelled_run
    repo = vault.get(name)
    pid = secrets.token_hex(8)
    branch = f"lightwork/{_slug(title)}-{pid[:6]}"
    with vault.checkout(name) as work:
        base = _git(["rev-parse", "HEAD"], cwd=work).strip()
        _git(["checkout", "-q", "-b", branch], cwd=work)
        goal_id = world.create_goal(f"[{name}] {title}", _brief(repo, title, description))
        with labelled_run({f"code:{name}"}, mode=repo.protect):
            run(goal_id, work)
        if git_metadata_may_execute_filters(work):
            raise VaultError("the run left git filter/hook metadata; change discarded")
        _git(["add", "-A"], cwd=work)
        if _git(["status", "--porcelain"], cwd=work).strip():
            _git(["-c", "user.name=Lightwork", "-c", "user.email=lightwork@localhost",
                  "commit", "-q", "-m", f"{title}\n\nLightwork proposal {pid} (goal #{goal_id})"],
                 cwd=work)
        head = _git(["rev-parse", "HEAD"], cwd=work).strip()
        changed = head != base
        diff = _git(["diff", f"{base}..{head}"], cwd=work) if changed else ""
        files = _git(["diff", "--name-only", f"{base}..{head}"], cwd=work).split() if changed else []
        tests = run_tests(repo, work, sandbox_factory) if changed else None
        bundle = b""
        if changed:
            with tempfile.TemporaryDirectory(prefix="lw-prop-") as tmp:
                out = Path(tmp) / "p.bundle"
                _git(["bundle", "create", str(out), f"{base}..{branch}"], cwd=work)
                bundle = out.read_bytes()
    review = reviewer(f"{title}\n\n{description}", diff) if (reviewer and diff) else None
    proposal = {
        "id": pid, "repo": name, "title": title, "goal_id": goal_id, "branch": branch,
        "base": base, "head": head, "files": files, "diff": diff[:MAX_DIFF_CHARS],
        "diff_truncated": len(diff) > MAX_DIFF_CHARS,
        "tests": None if tests is None else {k: v for k, v in tests.items() if k != "output"},
        "tests_output": None if tests is None else tests["output"],
        "review": review, "status": "empty" if not changed else "pending",
        "approval_id": None, "created_at": time.time(), "merged_at": None,
    }
    if changed:
        atomic_write_bytes(_proposal_dir(name) / f"{pid}.bundle",
                           vault._seal(bundle, f"codebase/{name}/proposal-bundle/{pid}"))
        proposal["approval_id"] = world.create_approval(
            "codebase_merge", risk="high", scope=name, provenance="codebase",
            requested_by="agent:codebase", detail=_approval_detail(proposal))
    _save(proposal)
    return proposal


def _approval_detail(p: dict) -> str:
    tests = p["tests"]
    review = p["review"]
    lines = [f"Merge Lightwork proposal {p['id']} into {p['repo']}: {p['title']}",
             f"Branch {p['branch']} from {p['base'][:12]}; {len(p['files'])} file(s) changed.",
             "Tests: " + ("not configured" if tests is None else
                          f"{'PASSED' if tests['passed'] else 'FAILED'} (`{tests['command']}`)"),
             "Independent review: " + ("not run" if review is None else
                                       ("approves" if review.get("approves") else "objects")
                                       + f" ({review.get('blockers', 0)} blocker(s))")]
    lines += [f"  {f}" for f in p["files"][:40]]
    lines.append(f"Inspect: maverick code show {p['id']}")
    return "\n".join(lines)


def status(world, p: dict) -> str:
    if p["status"] in ("merged", "empty", "conflict"):
        return p["status"]
    appr = world.get_approval(p["approval_id"]) if p["approval_id"] else None
    return {"approved": "approved", "denied": "rejected"}.get(
        appr.status if appr else "", "pending")


def _require_approved(world, p: dict) -> None:
    """The approval itself must be approved -- not merely the proposal's state."""
    appr = world.get_approval(p["approval_id"]) if p["approval_id"] else None
    if appr is None or appr.status != "approved":
        raise VaultError(f"proposal {p['id']} is {status(world, p)}; "
                         "a human must approve it first")


def _fetch_proposal(p: dict, work: Path) -> None:
    raw = (_proposal_dir(p["repo"]) / f"{p['id']}.bundle").read_bytes()
    with tempfile.TemporaryDirectory(prefix="lw-prop-") as tmp:
        b = Path(tmp) / "p.bundle"
        b.write_bytes(vault._unseal(raw, f"codebase/{p['repo']}/proposal-bundle/{p['id']}"))
        _git(["fetch", "-q", str(b), f"+refs/heads/{p['branch']}:refs/heads/{p['branch']}"],
             cwd=work)


def merge(world, pid: str) -> dict:
    """Merge an APPROVED proposal into the vault's default branch (no fast-forward,
    so the merge is one reviewable commit). A conflict leaves the vault as it was."""
    p = find(pid)
    _require_approved(world, p)
    if p["status"] == "merged":
        raise VaultError(f"proposal {pid} is already merged")
    with cross_process_lock(vault._bundle_path(p["repo"]), strict=True), \
            vault.checkout(p["repo"]) as work:
        _fetch_proposal(p, work)
        try:
            _git(["-c", "user.name=Lightwork", "-c", "user.email=lightwork@localhost",
                  "merge", "-q", "--no-ff", p["branch"], "-m",
                  f"Merge Lightwork proposal {pid}: {p['title']}"], cwd=work)
        except VaultError:
            p["status"] = "conflict"
            _save(p)
            raise VaultError(f"proposal {pid} no longer merges cleanly; request it again") from None
        vault.commit_back(p["repo"], work)
    p.update(status="merged", merged_at=time.time())
    _save(p)
    return p


def settle(world) -> list[dict]:
    """Merge every proposal a human has approved since the last settle; report
    the outcome of each (merged, or the reason it did not merge)."""
    out = []
    for p in list_proposals():
        if p["status"] == "pending" and status(world, p) == "approved":
            try:
                merge(world, p["id"])
                out.append({"id": p["id"], "repo": p["repo"], "outcome": "merged"})
            except VaultError as exc:
                out.append({"id": p["id"], "repo": p["repo"], "outcome": str(exc)})
    return out


def push(world, pid: str) -> str:
    """Push an APPROVED proposal's branch to the repository's source remote, for a
    pull request there. Returns the branch name."""
    p = find(pid)
    _require_approved(world, p)
    repo = vault.get(p["repo"])
    with vault.checkout(p["repo"]) as work:
        _fetch_proposal(p, work)
        _git(["push", "-q", repo.source, f"refs/heads/{p['branch']}:refs/heads/{p['branch']}"],
             cwd=work, token_env=repo.token_env)
    return p["branch"]


def default_runner(*, max_dollars: float | None = None) -> Runner:
    """Run the goal as a normal governed swarm whose sandbox is the working copy."""
    def run(goal_id: int, work: Path) -> object:
        from ..budget import Budget
        from ..llm import LLM
        from ..orchestrator import run_goal_sync
        from ..sandbox import build_sandbox
        from ..world_model import close_world_if_owned, open_world
        world = open_world()
        try:
            return run_goal_sync(LLM(), world, Budget(max_dollars=max_dollars), goal_id,
                                 sandbox=build_sandbox(workdir=str(work)))
        finally:
            close_world_if_owned(world)
    return run


def default_reviewer() -> Reviewer:
    """The independent diff reviewer (cross-family where possible)."""
    def review(brief: str, diff: str) -> dict:
        import asyncio

        from ..llm import LLM
        from ..reviewer import review_diff
        verdict = asyncio.run(review_diff(brief, diff, LLM()))
        return {"approves": bool(verdict.approves), "confidence": float(verdict.confidence),
                "blockers": len(verdict.blockers),
                "comments": [{"path": c.path, "line": c.line, "severity": c.severity,
                              "message": c.message} for c in verdict.comments[:50]]}
    return review


__all__ = ["default_reviewer", "default_runner", "find", "list_proposals", "merge",
           "propose", "push", "run_tests", "settle", "status"]


def summary(world) -> dict:
    """Read-only projection for the dashboard: repositories and proposals, never
    code, diffs or test output."""
    repos = [{"name": r.name, "default_branch": r.default_branch, "head": r.head[:12],
              "protect": r.protect, "tests": bool(r.test_command), "updated_at": r.updated_at}
             for r in vault.list_repos()]
    proposals = []
    for p in list_proposals()[:200]:
        tests = p.get("tests")
        review = p.get("review")
        proposals.append({
            "id": p["id"], "repo": p["repo"], "title": p["title"], "branch": p["branch"],
            "status": status(world, p), "files": len(p["files"]),
            "tests": None if tests is None else tests["passed"],
            "review": None if review is None else bool(review.get("approves")),
            "approval_id": p["approval_id"], "created_at": p["created_at"]})
    return {"schema": "lightwork.codebase-summary.v1", "enabled": vault.enabled(),
            "repos": repos, "proposals": proposals}
