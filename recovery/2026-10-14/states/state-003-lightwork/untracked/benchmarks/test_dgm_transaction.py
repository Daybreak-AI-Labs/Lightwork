"""Transaction regressions for the governed DGM deployment path.

These tests use a source-inspecting fixture runner rather than executing a
solver: it models an already-isolated worker and lets the test pin the ordering
of approval, rollback, apply, and ledger writes without introducing a host-code
execution sink.
"""
from __future__ import annotations

import difflib
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "packages" / "maverick-core"))
sys.path.insert(0, str(_ROOT / "benchmarks"))

import dgm_uplift  # noqa: E402, I001


_BASELINE = 'def solve(instance):\n    return ""\n'
_CANDIDATE = 'def solve(instance):\n    return "IMPROVED"\n'


@dataclass
class _Task:
    instance_id: str
    brief: str = "Solve the public fixture task."
    language: str = "python"


def _diff(old: str, new: str) -> str:
    return "diff --git a/solver.py b/solver.py\n" + "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile="a/solver.py", tofile="b/solver.py"))


def _fixture_runner(solver_dir: Path, _task, *, timeout: float) -> str:
    """Trusted test seam: inspect source without importing/executing it."""
    del timeout
    return "fixed" if "IMPROVED" in (solver_dir / "solver.py").read_text() else ""


def _score(_instance, patch: str, _workroot: Path, *, timeout: float) -> bool:
    del timeout
    return patch == "fixed"


_SYNTHETIC_SCORE = dgm_uplift.attest_isolated_score_fn(
    _score,
    identity="dgm-transaction-fixture/v1",
    production=False,
)


def _keys(tmp_path: Path) -> tuple[Path, str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    keys = tmp_path / "approvers"
    keys.mkdir()
    private = ed25519.Ed25519PrivateKey.generate()
    private_hex = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    ).hex()
    public_hex = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    (keys / "operator.pub").write_bytes(bytes.fromhex(public_hex))
    return keys, private_hex, public_hex


def _controller(ledger, public_hex: str):
    from maverick.self_improvement import _RUNG_POLICY, SelfImprovementController

    policy = {rung: dict(values) for rung, values in _RUNG_POLICY.items()}
    policy["code"]["min_samples"] = 1
    return SelfImprovementController(
        ledger=ledger,
        approver_keys=(public_hex,),
        frozen_fn=lambda: False,
        audit_fn=lambda **_kwargs: None,
        rung_policy=policy,
    )


def _govern(solver: Path, keys: Path, ledger, controller, *, signature=None):
    return dgm_uplift.govern_solver_change(
        solver,
        _diff(_BASELINE, _CANDIDATE),
        [_Task("fixture-a"), _Task("fixture-b")],
        keys_dir=keys,
        ledger=ledger,
        controller=controller,
        workroot=solver.parent / "eval",
        held_out_frac=0.5,
        score_fn=_SYNTHETIC_SCORE,
        solver_runner=_fixture_runner,
        approval_signature=signature,
    )


def _signature(result, private_hex: str) -> str:
    from maverick.approval_signing import ApprovalRequest, sign_request

    return sign_request(
        ApprovalRequest(
            result.candidate_id or "",
            "code",
            result.payload_sha256 or "",
        ),
        private_hex,
    )


def test_dgm_requires_external_signature_and_performs_zero_writes_before_it(
    tmp_path, monkeypatch,
):
    from maverick.self_improvement import PromotionLedger

    monkeypatch.setenv("MAVERICK_SELF_IMPROVEMENT", "1")
    solver = tmp_path / "solver"
    solver.mkdir()
    (solver / "solver.py").write_text(_BASELINE)
    keys, _private, public = _keys(tmp_path)
    ledger = PromotionLedger(path=tmp_path / "ledger.json")

    preview = _govern(solver, keys, ledger, _controller(ledger, public))

    assert preview.boundary_ok
    assert not preview.promoted and not preview.applied
    assert preview.candidate_id and preview.payload_sha256
    assert preview.approval_request["candidate_id"] == preview.candidate_id
    assert "AWAITING EXTERNAL APPROVAL" in preview.reason
    assert (solver / "solver.py").read_text() == _BASELINE
    assert ledger.all() == []
    assert not (tmp_path / "dgm-rollbacks").exists()


def test_invalid_signature_has_no_snapshot_apply_or_ledger_write(tmp_path, monkeypatch):
    from maverick.self_improvement import PromotionLedger

    monkeypatch.setenv("MAVERICK_SELF_IMPROVEMENT", "1")
    solver = tmp_path / "solver"
    solver.mkdir()
    (solver / "solver.py").write_text(_BASELINE)
    keys, _private, public = _keys(tmp_path)
    ledger = PromotionLedger(path=tmp_path / "ledger.json")

    result = _govern(
        solver,
        keys,
        ledger,
        _controller(ledger, public),
        signature="00" * 64,
    )

    assert not result.promoted and not result.applied
    assert result.rollback_id is None
    assert "invalid or untrusted" in result.reason
    assert (solver / "solver.py").read_text() == _BASELINE
    assert ledger.all() == []
    assert not (tmp_path / "dgm-rollbacks").exists()


def test_signed_transaction_applies_before_persisting_the_ledger(tmp_path, monkeypatch):
    from maverick.self_improvement import PromotionLedger

    monkeypatch.setenv("MAVERICK_SELF_IMPROVEMENT", "1")
    solver = tmp_path / "solver"
    solver.mkdir()
    (solver / "solver.py").write_text(_BASELINE)
    keys, private, public = _keys(tmp_path)
    ledger = PromotionLedger(path=tmp_path / "ledger.json")
    inner = _controller(ledger, public)
    preview = _govern(solver, keys, ledger, inner)

    class _ObserveCommit:
        def __init__(self):
            self.ledger = ledger
            self.approver_keys = (public,)
            self.min_improvement = inner.min_improvement
            self.max_auto_rung = inner.max_auto_rung
            self.rung_policy = inner.rung_policy

        def evaluate(self, candidate):
            return inner.evaluate(candidate)

        def promote(self, candidate):
            # This is the critical ordering invariant: no receipt exists until
            # the exact candidate source is already live.
            assert (solver / "solver.py").read_text() == _CANDIDATE
            assert ledger.all() == []
            return inner.promote(candidate)

    result = _govern(
        solver,
        keys,
        ledger,
        _ObserveCommit(),
        signature=_signature(preview, private),
    )

    assert result.promoted and result.applied, result.reason
    assert (solver / "solver.py").read_text() == _CANDIDATE
    assert result.rollback_id
    records = ledger.all()
    assert len(records) == 1
    assert records[0].id == result.candidate_id
    assert records[0].approval_signature
    assert records[0].approver_id == result.approver_id


def test_failed_post_apply_commit_reverts_and_never_records(tmp_path, monkeypatch):
    from maverick.self_improvement import GateResult, PromotionLedger, Verdict

    monkeypatch.setenv("MAVERICK_SELF_IMPROVEMENT", "1")
    solver = tmp_path / "solver"
    solver.mkdir()
    (solver / "solver.py").write_text(_BASELINE)
    keys, private, public = _keys(tmp_path)
    ledger = PromotionLedger(path=tmp_path / "ledger.json")
    inner = _controller(ledger, public)
    preview = _govern(solver, keys, ledger, inner)

    class _RefuseCommit:
        def __init__(self):
            self.ledger = ledger
            self.approver_keys = (public,)
            self.min_improvement = inner.min_improvement
            self.max_auto_rung = inner.max_auto_rung
            self.rung_policy = inner.rung_policy

        def evaluate(self, candidate):
            return inner.evaluate(candidate)

        def promote(self, candidate):
            assert (solver / "solver.py").read_text() == _CANDIDATE
            return Verdict(
                candidate.id,
                candidate.rung,
                False,
                (GateResult("late_policy", False, "late policy change"),),
                "late policy change",
            )

    result = _govern(
        solver,
        keys,
        ledger,
        _RefuseCommit(),
        signature=_signature(preview, private),
    )

    assert not result.promoted and not result.applied
    assert "reverted" in result.reason
    assert (solver / "solver.py").read_text() == _BASELINE
    assert ledger.all() == []


def test_unattested_scorer_is_refused_before_the_solver_runner_is_called(
        tmp_path, monkeypatch):
    """A direct API caller cannot accidentally promote host-scored evidence."""
    from maverick.self_improvement import PromotionLedger

    monkeypatch.setenv("MAVERICK_SELF_IMPROVEMENT", "1")
    solver = tmp_path / "solver"
    solver.mkdir()
    (solver / "solver.py").write_text(_BASELINE)
    keys, _private, public = _keys(tmp_path)
    ledger = PromotionLedger(path=tmp_path / "ledger.json")
    called = False

    def runner(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("unsafe scorer must be rejected before evaluation")

    def unsafe_score(*_args, **_kwargs):
        return True

    result = dgm_uplift.govern_solver_change(
        solver, _diff(_BASELINE, _CANDIDATE), [_Task("fixture-a"), _Task("fixture-b")],
        keys_dir=keys, ledger=ledger, controller=_controller(ledger, public),
        workroot=tmp_path / "eval", held_out_frac=0.5,
        score_fn=unsafe_score, solver_runner=runner,
    )

    assert not result.promoted
    assert "attested isolated scoring backend" in result.reason
    assert not called
    assert ledger.all() == []


def test_unmatched_checkpoint_context_is_not_reused(tmp_path):
    """A JSONL result for a prior patch/corpus cannot supply fresh evidence."""
    solver = tmp_path / "solver"
    solver.mkdir()
    (solver / "solver.py").write_text(_CANDIDATE)
    checkpoint = tmp_path / "candidate.jsonl"
    checkpoint.write_text(
        '{"context":"old-manifest:candidate","instance_id":"fixture-a","resolved":true}\n')
    calls: list[str] = []

    def runner(_solver_dir, task, *, timeout):
        del timeout
        calls.append(task.instance_id)
        return "fixed"

    result = dgm_uplift.run_solver(
        solver, [_Task("fixture-a")], tmp_path / "work", score_fn=_score,
        solver_runner=runner, checkpoint=checkpoint,
        checkpoint_context="new-manifest:candidate",
    )

    assert result == {"fixture-a": True}
    assert calls == ["fixture-a"]
    assert '"context": "new-manifest:candidate"' in checkpoint.read_text()


def test_keyring_mutation_during_signed_evaluation_aborts_before_apply(
        tmp_path, monkeypatch):
    """The pre-evaluation key snapshot cannot be swapped into a self-approval."""
    from maverick.self_improvement import PromotionLedger

    monkeypatch.setenv("MAVERICK_SELF_IMPROVEMENT", "1")
    solver = tmp_path / "solver"
    solver.mkdir()
    (solver / "solver.py").write_text(_BASELINE)
    keys, private, public = _keys(tmp_path)
    ledger = PromotionLedger(path=tmp_path / "ledger.json")
    inner = _controller(ledger, public)
    preview = _govern(solver, keys, ledger, inner)

    def mutating_runner(solver_dir, task, *, timeout):
        # This models an evaluation-side attempt to replace the public keyring.
        # The original signature remains valid against the in-memory snapshot,
        # but the mutation lock recheck must refuse deployment.
        (keys / "operator.pub").write_bytes(b"x" * 32)
        return _fixture_runner(solver_dir, task, timeout=timeout)

    result = dgm_uplift.govern_solver_change(
        solver, _diff(_BASELINE, _CANDIDATE), [_Task("fixture-a"), _Task("fixture-b")],
        keys_dir=keys, ledger=ledger, controller=inner, workroot=solver.parent / "eval",
        held_out_frac=0.5, score_fn=_SYNTHETIC_SCORE,
        solver_runner=mutating_runner,
        approval_signature=_signature(preview, private),
    )

    assert not result.promoted and not result.applied
    assert "context drifted" in result.reason
    assert (solver / "solver.py").read_text() == _BASELINE
    assert ledger.all() == []


def test_post_apply_commit_exception_reverts_exact_snapshot(tmp_path, monkeypatch):
    """Exceptions are not a loophole that leaves an unledgered solver live."""
    from maverick.self_improvement import PromotionLedger

    monkeypatch.setenv("MAVERICK_SELF_IMPROVEMENT", "1")
    solver = tmp_path / "solver"
    solver.mkdir()
    (solver / "solver.py").write_text(_BASELINE)
    keys, private, public = _keys(tmp_path)
    ledger = PromotionLedger(path=tmp_path / "ledger.json")
    inner = _controller(ledger, public)
    preview = _govern(solver, keys, ledger, inner)

    class _ExplodingCommit:
        def __init__(self):
            self.ledger = ledger
            self.approver_keys = (public,)
            self.min_improvement = inner.min_improvement
            self.max_auto_rung = inner.max_auto_rung
            self.rung_policy = inner.rung_policy

        def evaluate(self, candidate):
            return inner.evaluate(candidate)

        def promote(self, _candidate):
            assert (solver / "solver.py").read_text() == _CANDIDATE
            raise RuntimeError("durable ledger unavailable")

    result = _govern(
        solver, keys, ledger, _ExplodingCommit(),
        signature=_signature(preview, private),
    )

    assert not result.promoted and not result.applied
    assert "durable ledger commit failed" in result.reason
    assert "reverted exact snapshot" in result.reason
    assert (solver / "solver.py").read_text() == _BASELINE
    assert ledger.all() == []


def test_durable_journal_commit_does_not_revert_for_projection_cache_failure(
        tmp_path, monkeypatch):
    """The journal is the receipt; an audit-cache failure is recoverable."""
    from maverick.self_improvement import PromotionLedger, PromotionLedgerError

    monkeypatch.setenv("MAVERICK_SELF_IMPROVEMENT", "1")
    solver = tmp_path / "solver"
    solver.mkdir()
    (solver / "solver.py").write_text(_BASELINE)
    keys, private, public = _keys(tmp_path)
    ledger_path = tmp_path / "ledger.json"
    ledger = PromotionLedger(path=ledger_path)
    ctrl = _controller(ledger, public)
    preview = _govern(solver, keys, ledger, ctrl)
    original_write = ledger._write_projection_locked

    def fail_projection(_records):
        raise PromotionLedgerError("simulated audit-cache failure")

    monkeypatch.setattr(ledger, "_write_projection_locked", fail_projection)
    result = _govern(
        solver, keys, ledger, ctrl, signature=_signature(preview, private))

    assert result.promoted and result.applied, result.reason
    assert (solver / "solver.py").read_text() == _CANDIDATE
    assert Path(f"{ledger_path}.journal").exists()
    assert not ledger_path.exists()
    monkeypatch.setattr(ledger, "_write_projection_locked", original_write)
    assert PromotionLedger(path=ledger_path).get(result.candidate_id or "") is not None
    assert ledger_path.exists()


def test_no_uplift_does_not_emit_a_signable_approval_request(tmp_path, monkeypatch):
    from maverick.self_improvement import PromotionLedger

    monkeypatch.setenv("MAVERICK_SELF_IMPROVEMENT", "1")
    solver = tmp_path / "solver"
    solver.mkdir()
    (solver / "solver.py").write_text(_BASELINE)
    keys, _private, public = _keys(tmp_path)
    ledger = PromotionLedger(path=tmp_path / "ledger.json")
    no_op_source = _BASELINE + "# comment-only candidate\n"

    result = dgm_uplift.govern_solver_change(
        solver, _diff(_BASELINE, no_op_source), [_Task("fixture-a"), _Task("fixture-b")],
        keys_dir=keys, ledger=ledger, controller=_controller(ledger, public),
        workroot=tmp_path / "eval", held_out_frac=0.5,
        score_fn=_SYNTHETIC_SCORE, solver_runner=_fixture_runner,
    )

    assert not result.promoted
    assert result.approval_request == {}
    assert "no improvement" in result.reason
