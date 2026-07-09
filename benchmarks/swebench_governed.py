#!/usr/bin/env python3
"""SWE-bench under governance: score real bug-fix patches through the real gate.

The naked SWE-bench number ("X% resolved") measures a proposer's raw coding
ability. Lightwork's differentiator is not that number in isolation -- it is that
*every accepted fix passed a governance chain*: an anti-cheat boundary that
structurally refuses test/config edits, a held-out regression gate on the
project's own tests, a capability non-escalation check, and a human-signed,
reversible, audited promotion. No other entry on the SWE-bench leaderboard reports
its result under a signed reference monitor.

This module is the wiring. Given a SWE-bench instance (issue, repo@base_commit,
``FAIL_TO_PASS`` / ``PASS_TO_PASS`` test ids, and a candidate patch) it returns a
:class:`GovernedResult` and, on success, records a signed promotion to a ledger.

    resolved_under_governance = boundary_ok AND tests_resolved AND gate_promoted

The candidate patch comes from a *proposer*. Two are provided:

* ``oracle`` -- returns the instance's gold patch. Proves the governance pipeline
  end-to-end with no LLM and no key (the gold patch is Princeton's, the tests are
  the real project's). Use it to validate the harness.
* ``llm`` -- generates a patch with the coding-mode agent. THIS is the capability
  measurement and needs ``ANTHROPIC_API_KEY`` (or another provider). It is a thin
  seam here; the agent loop lives in ``benchmarks/swe_bench.py``.

No Docker required: tests run via ``sandbox.exec`` in an isolated copy of the
checked-out repo (Docker only buys a reproducible dependency env; a venv with the
instance's deps installed works for the local/no-Docker path).

    python benchmarks/swebench_governed.py --manifest instances.jsonl \
        --proposer oracle --keys ./keys --ledger ./swebench_ledger.json

Manifest format is the one ``benchmarks/fetch_swe_bench_verified.py`` emits: one
JSON object per line with ``instance_id, repo, base_commit, brief, fail_to_pass,
pass_to_pass, gold_patch, language`` and a local ``repo_path`` (a checked-out tree
at ``base_commit``) or a ``repos/<instance_id>`` sibling directory.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "packages" / "maverick-core"))

log = logging.getLogger(__name__)


@dataclass
class Instance:
    """One SWE-bench task."""

    instance_id: str
    repo_path: Path                 # a checked-out tree at base_commit
    fail_to_pass: list[str]
    pass_to_pass: list[str]
    gold_patch: str = ""
    brief: str = ""
    language: str = "python"
    # The GRADER's test fixture (SWE-bench semantics): applied to BOTH arms
    # before running, because the graded FAIL_TO_PASS tests are usually added
    # or updated by it. Distinct from the candidate patch, which the anti-cheat
    # boundary still refuses if it touches tests.
    test_patch: str = ""

    @property
    def total_tests(self) -> int:
        return len(self.fail_to_pass) + len(self.pass_to_pass)


@dataclass
class GovernedResult:
    """The governance verdict on one candidate patch for one instance."""

    instance_id: str
    boundary_ok: bool = False       # anti-cheat: no test/config edits, not gold-copy
    boundary_reason: str = ""
    baseline_score: float = 0.0     # pre-patch pass-rate (FAIL_TO_PASS should fail)
    candidate_score: float = 0.0    # post-patch pass-rate
    tests_resolved: bool = False    # all FAIL_TO_PASS pass AND all PASS_TO_PASS pass
    # True when the env can't grade the instance at all (baseline can't run its
    # own passing tests). Reported separately from unresolved; never resolved.
    ungradable: bool = False
    capability_widens: bool | None = None
    promoted: bool = False          # the governed gate signed + recorded it
    approver_id: str | None = None
    samples: int = 0
    reason: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def resolved_under_governance(self) -> bool:
        return self.boundary_ok and self.tests_resolved and self.promoted

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "resolved_under_governance": self.resolved_under_governance,
            "boundary_ok": self.boundary_ok,
            "tests_resolved": self.tests_resolved,
            "promoted": self.promoted,
            "baseline_score": round(self.baseline_score, 4),
            "candidate_score": round(self.candidate_score, 4),
            "capability_widens": self.capability_widens,
            "approver_id": self.approver_id,
            "samples": self.samples,
            "reason": self.reason,
            "warnings": self.warnings,
        }


# --- proposers ----------------------------------------------------------------

def oracle_proposer(inst: Instance) -> str:
    """The gold patch. Proves the pipeline with no LLM; the fix is Princeton's."""
    return inst.gold_patch


def _worktree_diff(repo: Path) -> str:
    """The repo's uncommitted changes as a raw, git-appliable unified diff --
    INCLUDING new files (``git add -N`` intent-to-add makes untracked files
    show in ``git diff``). This is the authoritative patch: it is literal
    ``git diff`` output, so it always applies with ``git apply -p1``, unlike a
    diff parsed out of the model's prose. Hardened env (no ext-diff/textconv,
    no global config) mirrors ``swe_bench._git_diff``."""
    import subprocess

    env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_SYSTEM": os.devnull, "GIT_OPTIONAL_LOCKS": "0",
           "PATH": os.environ.get("PATH", "")}
    try:
        subprocess.run(["git", "-C", str(repo), "add", "-N", "."],
                       capture_output=True, timeout=60, env=env)
        out = subprocess.run(
            ["git", "-C", str(repo), "diff", "--no-ext-diff", "--no-textconv"],
            capture_output=True, text=True, timeout=60, env=env)
        return out.stdout or ""
    except (subprocess.SubprocessError, OSError):
        return ""


def _desanitize_csv_patch(diff: str) -> str:
    """Undo ``swe_bench._sanitize_patch_for_csv``'s formula-injection guard: a
    leading ``'`` prepended when the diff's first non-whitespace char is one of
    ``= + - @``. That guard is correct for a CSV cell but makes the diff
    non-appliable, which is why ``row.predicted_patch`` must never go straight
    to ``git apply``. Fallback only -- the worktree diff is preferred."""
    if diff[:1] == "'" and diff[1:2] in ("=", "+", "-", "@"):
        return diff[1:]
    return diff


def llm_proposer(inst: Instance) -> str:
    """Generate a patch with the coding-mode agent (needs a provider key).

    Adapter over the proven ``benchmarks/swe_bench.py`` solver (``run_maverick``:
    coding mode, best-of-N via ``MAVERICK_BEST_OF_N``). It runs the agent WITH
    the instance's checked-out tree as its working directory, then captures the
    agent's ACTUAL edits as a raw ``git diff`` (:func:`_worktree_diff`) -- which
    is always ``git apply``-able. It deliberately does NOT return
    ``row.predicted_patch``: that field is CSV-sanitized for the harness's CSV
    column and can carry a leading ``'`` guard byte that makes ``git apply``
    reject the whole patch ("No valid patches in input"). The prose-extracted
    patch is only a de-sanitized fallback for the rare case the agent printed a
    diff without editing the tree.

    Writes a per-instance forensics sidecar (``<ledger-dir>/forensics/`` or
    ``MAVERICK_SWEBENCH_FORENSICS``) with the model's result text, both patch
    captures, and cost/turns -- so a failed apply/grade is debuggable without a
    re-run. Resets the tree afterward (``git reset --hard`` + ``git clean
    -fdx``): point ``repo_path`` ONLY at a dedicated instance checkout. Fail-loud
    on a missing key -- a keyless run must not silently score 0.
    """
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("MAVERICK_PROVIDER_READY")):
        raise RuntimeError(
            "llm_proposer needs a provider key (set ANTHROPIC_API_KEY, or set "
            "MAVERICK_PROVIDER_READY=1 if another provider is configured in "
            "~/.maverick/config.toml). Use --proposer oracle for the keyless "
            "pipeline proof.")

    from swe_bench import _reset_workdir, run_maverick

    prev_cwd = os.getcwd()
    os.chdir(inst.repo_path)  # build_sandbox() falls back to cwd for its workdir
    row = tree_diff = prose_diff = None
    try:
        row = run_maverick(
            inst.instance_id, inst.brief,
            fail_to_pass=inst.fail_to_pass, pass_to_pass=inst.pass_to_pass,
            gold_patch=inst.gold_patch, language=inst.language)
        tree_diff = _worktree_diff(inst.repo_path)
        prose_diff = _desanitize_csv_patch(row.predicted_patch or "")
        # Authoritative: the agent's real edits. Fall back to its prose diff
        # only when it printed a patch without touching the tree.
        return tree_diff if tree_diff.strip() else prose_diff
    finally:
        _dump_forensics(inst, row, tree_diff, prose_diff)
        os.chdir(prev_cwd)
        try:
            _reset_workdir(inst.repo_path)
        except Exception:
            log.warning("llm_proposer: could not reset %s after proposing",
                        inst.repo_path, exc_info=True)


def _dump_forensics(inst, row, tree_diff, prose_diff) -> None:
    """Best-effort per-instance forensics sidecar; never raises into the run."""
    try:
        base = os.environ.get("MAVERICK_SWEBENCH_FORENSICS")
        outdir = Path(base) if base else (_FORENSICS_DIR or Path.cwd() / "forensics")
        outdir.mkdir(parents=True, exist_ok=True)
        rec = {
            "instance_id": inst.instance_id,
            "cost_dollars": round(float(getattr(row, "cost_dollars", 0.0) or 0.0), 4),
            "tokens_out": int(getattr(row, "tokens_out", 0) or 0),
            "outcome": getattr(row, "outcome", ""),
            "result_text": (getattr(row, "extra", {}) or {}).get("run_text", ""),
            "tree_diff": tree_diff or "",
            "prose_diff_desanitized": prose_diff or "",
            "chosen": "tree" if (tree_diff or "").strip() else "prose",
        }
        (outdir / f"{inst.instance_id}.json").write_text(
            json.dumps(rec, indent=2), encoding="utf-8")
    except Exception:
        log.debug("forensics dump failed for %s", inst.instance_id, exc_info=True)


_FORENSICS_DIR: Path | None = None


PROPOSERS: dict[str, Callable[[Instance], str]] = {
    "oracle": oracle_proposer,
    "llm": llm_proposer,
}


# --- the governed scorer ------------------------------------------------------

def _score(workdir: Path, inst: Instance, sandbox, *, timeout: float) -> tuple[float, bool, int]:
    """Run the instance's tests in ``workdir``; return (score, resolved, samples)."""
    from maverick.coding_mode import run_failing_tests
    res = run_failing_tests(
        workdir, inst.fail_to_pass, inst.pass_to_pass, sandbox,
        timeout=timeout, language=inst.language)
    samples = res.fail_to_pass_total + res.pass_to_pass_total
    return res.score, res.all_pass, samples


def govern_candidate(
    inst: Instance,
    patch: str,
    *,
    keys_dir: Path,
    ledger=None,
    workroot: Path,
    sandbox_factory: Callable[[Path], object] | None = None,
    materialize: Callable[[Path, Path], None] | None = None,
    apply_fn: Callable[[str, Path], object] | None = None,
    timeout: float = 600.0,
    controller=None,
    check_gold_overlap: bool = True,
) -> GovernedResult:
    """Drive one candidate patch through the full governance chain. Never raises.

    Order (each gate independent, fail-closed):
      1. anti-cheat boundary (``defensive_validate``) -- refuse edits to tests /
         setup / requirements, and near-verbatim gold copies;
      2. held-out fitness -- baseline vs candidate on the project's own tests in
         isolated copies (``resolved`` = every FAIL_TO_PASS + PASS_TO_PASS passes);
      3. capability non-escalation proof;
      4. human-signed, reversible promotion recorded to the ledger.

    ``check_gold_overlap=False`` disables ONLY the gold-copy similarity check --
    for the ``oracle`` proposer, whose candidate IS the gold patch by definition
    (overlap is 100% and meaningless there). Test/setup-edit refusal always
    stays on; LLM runs must keep the overlap check armed.
    """
    from maverick.coding_mode import defensive_validate
    from maverick.self_modify_capability import capability_delta
    from maverick.self_modify_eval import _default_materialize, git_apply, resolve_eval_sandbox

    out = GovernedResult(instance_id=inst.instance_id)
    if not (patch or "").strip():
        out.reason = "empty patch (proposer produced no diff)"
        return out

    materialize = materialize or _default_materialize
    apply = apply_fn or (lambda p, wd: git_apply(p, wd))
    make_sb = sandbox_factory or (lambda wd: resolve_eval_sandbox(None, Path(wd)))

    # 1. Anti-cheat boundary -- structural, before any test is run.
    dv = defensive_validate(patch, fail_to_pass=inst.fail_to_pass,
                            pass_to_pass=inst.pass_to_pass,
                            gold_patch=inst.gold_patch if check_gold_overlap else "")
    out.warnings = list(dv.warnings)
    out.boundary_ok = bool(dv.ok)
    if not dv.ok:
        out.boundary_reason = "; ".join(dv.warnings) or "patch rejected by anti-cheat boundary"
        out.reason = f"boundary refused: {out.boundary_reason}"
        return out
    out.boundary_reason = "no test/config edits; not a gold copy"

    # 2. Held-out fitness on isolated copies (baseline vs candidate).
    base_dir, cand_dir = workroot / "baseline", workroot / "candidate"
    try:
        materialize(inst.repo_path, base_dir)
        materialize(inst.repo_path, cand_dir)
    except Exception as e:
        out.reason = f"could not isolate workspace: {e}"
        return out
    # SWE-bench grading semantics: the instance's test_patch (the grader's
    # added/updated tests) is applied to BOTH arms before anything runs --
    # the graded FAIL_TO_PASS tests usually don't exist at base_commit.
    if (inst.test_patch or "").strip():
        for arm_name, arm_dir in (("baseline", base_dir), ("candidate", cand_dir)):
            tp = apply(inst.test_patch, arm_dir)
            if not getattr(tp, "ok", False):
                out.reason = (f"grader test_patch did not apply to {arm_name}: "
                              f"{getattr(tp, 'reason', 'unknown')}")
                return out
    applied = apply(patch, cand_dir)
    if not getattr(applied, "ok", False):
        out.reason = f"patch did not apply: {getattr(applied, 'reason', 'unknown')}"
        return out
    try:
        sb_base, sb_cand = make_sb(base_dir), make_sb(cand_dir)
    except Exception as e:
        out.reason = f"sandbox refused (isolation policy): {e}"
        return out
    out.baseline_score, _, out.samples = _score(base_dir, inst, sb_base, timeout=timeout)
    out.candidate_score, out.tests_resolved, _ = _score(cand_dir, inst, sb_cand, timeout=timeout)
    if not out.tests_resolved:
        # Honest-reporting distinction: if the BASELINE can't even run the
        # instance's previously-passing tests, this environment can't grade the
        # instance (deps missing / wrong interpreter) -- that's "ungradable
        # here", not "the agent failed". Never counted as resolved either way
        # (fail-closed); the tag keeps the scoreboard honest in both directions.
        if out.baseline_score == 0.0 and inst.pass_to_pass:
            out.ungradable = True
            out.reason = ("ungradable in this environment: baseline cannot run "
                          "the instance's own passing tests (install its deps "
                          "or grade in the per-instance Docker env)")
        else:
            out.reason = (f"not resolved: candidate {out.candidate_score:.3f} "
                          f"(baseline {out.baseline_score:.3f}); some FAIL/PASS_TO_PASS failing")
        return out

    # 3. Capability non-escalation proof (best-effort; a delta error stays None
    #    so the code rung falls back to demanding a proof -> fail-closed).
    cb = ca = None
    probe: tuple[str, ...] = ()
    try:
        cb, ca, probe = capability_delta(patch)
        if cb is not None and ca is not None and probe:
            out.capability_widens = any(ca.permits(t) and not cb.permits(t) for t in probe)
    except Exception:
        log.debug("capability_delta failed for %s", inst.instance_id, exc_info=True)

    # 4. Human-signed, reversible promotion -> ledger.
    out.promoted, out.approver_id, why = _promote(
        inst, patch, out, cb, ca, probe, keys_dir=keys_dir, ledger=ledger, controller=controller)
    out.reason = why or ("resolved under governance" if out.promoted else "promotion refused")
    return out


def _promote(inst, patch, res, cb, ca, probe, *, keys_dir, ledger, controller):
    """Build the candidate, sign it with the operator key, run the governed gate."""
    import hashlib
    import os

    from maverick import approval_signing as asig
    from maverick.self_improvement import Candidate, SelfImprovementController

    priv_path = Path(keys_dir) / "operator.priv.hex"
    if not priv_path.exists():
        return False, None, f"no operator signing key at {priv_path}"
    priv = priv_path.read_text().strip()

    cand = Candidate(
        rung="code",
        summary=f"swe-bench {inst.instance_id}: {inst.brief[:80]}",
        baseline_score=res.baseline_score, candidate_score=res.candidate_score,
        samples=res.samples, payload=patch,
        payload_sha256=hashlib.sha256(patch.encode()).hexdigest(),
        capability_before=cb, capability_after=ca, probe_tools=probe,
        rollback={"revert": f"git -C {inst.repo_path} checkout -- ."},
        id=inst.instance_id)
    sig = asig.sign_request(asig.ApprovalRequest.for_candidate(cand), priv)
    cand = Candidate(**{**cand.__dict__, "approval_signature": sig})

    prev = {k: os.environ.get(k) for k in ("MAVERICK_APPROVER_KEYS_DIR", "MAVERICK_SELF_IMPROVEMENT")}
    os.environ["MAVERICK_APPROVER_KEYS_DIR"] = str(keys_dir)
    os.environ["MAVERICK_SELF_IMPROVEMENT"] = "1"
    try:
        ctrl = controller or SelfImprovementController(ledger=ledger)
        verdict = ctrl.promote(cand)
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    if verdict.ok:
        return True, verdict.approver_id, "resolved under governance"
    return False, verdict.approver_id, f"gate refused: {verdict.blocking_reason}"


# --- CLI ----------------------------------------------------------------------

def _sane_test_ids(ids: list, *, instance_id: str, which: str) -> list[str]:
    """Drop malformed test ids, LOUDLY (no silent caps).

    The upstream SWE-bench dataset built FAIL_TO_PASS/PASS_TO_PASS by
    comma-splitting test-output lines, so a parametrized id whose params
    contain a comma (``test_x[foo, bar-expected2]``) arrives truncated
    (``test_x[foo,``). Passing such an id to pytest aborts the WHOLE chunk
    ("not found"), zeroing every other test with it. Filter the malformation
    signature -- unbalanced brackets or a trailing comma -- and say so."""
    keep: list[str] = []
    for t in ids:
        t = str(t).strip()
        if not t:
            continue
        if t.count("[") != t.count("]") or t.endswith((",", "\\")):
            print(f"  [note ]  {instance_id}: dropping malformed {which} id "
                  f"(upstream dataset truncation): {t!r}")
            continue
        keep.append(t)
    return keep


def _load_manifest(path: Path) -> list[Instance]:
    out: list[Instance] = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        iid = row["instance_id"]
        repo_path = row.get("repo_path") or str(Path(path).parent / "repos" / iid)
        out.append(Instance(
            instance_id=iid, repo_path=Path(repo_path),
            fail_to_pass=_sane_test_ids(list(row.get("fail_to_pass") or []),
                                        instance_id=iid, which="FAIL_TO_PASS"),
            pass_to_pass=_sane_test_ids(list(row.get("pass_to_pass") or []),
                                        instance_id=iid, which="PASS_TO_PASS"),
            gold_patch=row.get("gold_patch", "") or "",
            brief=row.get("brief", "") or "",
            language=row.get("language", "python") or "python",
            test_patch=row.get("test_patch", "") or ""))
    return out


def main(argv: list[str] | None = None) -> int:
    import tempfile

    from maverick.self_improvement import PromotionLedger

    ap = argparse.ArgumentParser(description="Run SWE-bench instances under governance.")
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--proposer", choices=sorted(PROPOSERS), default="oracle")
    ap.add_argument("--keys", required=True, type=Path, help="dir with operator.priv.hex + operator.pub")
    ap.add_argument("--ledger", type=Path, default=Path("swebench_ledger.json"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=600.0)
    args = ap.parse_args(argv)

    instances = _load_manifest(args.manifest)
    if args.limit:
        instances = instances[: args.limit]
    propose = PROPOSERS[args.proposer]
    ledger = PromotionLedger(path=args.ledger)
    # Forensics sidecars land next to the ledger so a failed apply/grade is
    # debuggable without a costly re-run.
    global _FORENSICS_DIR
    _FORENSICS_DIR = args.ledger.resolve().parent / "forensics"

    print("=" * 78)
    print(f"  SWE-BENCH UNDER GOVERNANCE   proposer={args.proposer}  n={len(instances)}")
    print("=" * 78)
    resolved = boundary_refused = unresolved = ungradable = 0
    with tempfile.TemporaryDirectory(prefix="swebench-gov-") as td:
        for i, inst in enumerate(instances):
            try:
                patch = propose(inst)
            except RuntimeError as e:
                # Missing provider key: config problem, not a per-instance
                # failure. Abort loudly instead of skipping 500 instances.
                print(f"  [ABORT] {e}")
                return 2
            except Exception as e:
                # A per-instance proposer crash scores that instance 0 and the
                # run continues -- one bad instance must not kill a $1k run.
                print(f"  [ERROR] {inst.instance_id:40}  proposer failed: {e}")
                unresolved += 1
                continue
            r = govern_candidate(inst, patch, keys_dir=args.keys, ledger=ledger,
                                 workroot=Path(td) / f"i{i}", timeout=args.timeout,
                                 check_gold_overlap=(args.proposer != "oracle"))
            if r.resolved_under_governance:
                resolved += 1
                tag = "PASS"
            elif not r.boundary_ok:
                boundary_refused += 1
                tag = "CHEAT"
            elif r.ungradable:
                ungradable += 1
                tag = "NOENV"
            else:
                unresolved += 1
                tag = "FAIL"
            print(f"  [{tag}]  {inst.instance_id:40}  {r.reason}")
    n = len(instances) or 1
    print("=" * 78)
    print(f"  resolved under governance: {resolved}/{len(instances)} ({100*resolved/n:.1f}%)"
          f"   |  boundary-refused: {boundary_refused}  |  unresolved: {unresolved}"
          f"   |  ungradable-here: {ungradable}")
    if ungradable:
        print("  NOTE: ungradable instances need their deps installed (or the Docker env);"
              " they are NOT counted as resolved -- report the headline as X/(total).")
    print(f"  signed promotion ledger: {args.ledger}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
