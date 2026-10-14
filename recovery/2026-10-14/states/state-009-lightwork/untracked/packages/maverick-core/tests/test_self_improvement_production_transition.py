"""A pre-production PREPARE never becomes production adoption authority."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from maverick.self_improvement import (
    ArtifactRevision,
    Candidate,
    PromotionLedger,
    SelfImprovementController,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_MANIFEST = REPO_ROOT / "release" / "production-surface-manifest.json"


def _artifact(label: str) -> ArtifactRevision:
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()
    return ArtifactRevision(
        identity="production-transition:config",
        sha256=digest,
        version=label,
    )


def _candidate(candidate_id: str) -> Candidate:
    return Candidate(
        id=candidate_id,
        rung="config",
        summary="bounded production-transition test candidate",
        baseline_score=0.5,
        candidate_score=0.7,
        samples=5,
        payload={"temperature": 0.2},
        rollback="snapshot-before",
    )


def _code_candidate(candidate_id: str, *, signature: str) -> Candidate:
    candidate = _candidate(candidate_id)
    candidate.rung = "code"
    candidate.samples = 10
    candidate.capability_widens = False
    candidate.approval_signature = signature
    return candidate


def _controller(
    path: Path,
    verifier: Any | None = None,
    *,
    frozen_fn: Any | None = None,
    approval_verifier: Any | None = None,
) -> SelfImprovementController:
    return SelfImprovementController(
        frozen_fn=frozen_fn if frozen_fn is not None else lambda: False,
        audit_fn=lambda **_event: None,
        ledger=PromotionLedger(path=path),
        approval_verifier=approval_verifier,
        model_risk_verifier=(
            verifier
            if verifier is not None
            else lambda **_request: (True, "current governed authorization")
        ),
        now=lambda: 1_726_000_000.0,
    )


@pytest.fixture(autouse=True)
def _outside_production(tmp_path, monkeypatch):
    from maverick import config

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_SELF_IMPROVEMENT", "1")
    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)
    monkeypatch.delenv("MAVERICK_PRODUCTION_MANIFEST", raising=False)
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def _enter_production(monkeypatch) -> None:
    from maverick import self_improvement

    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")
    monkeypatch.setenv("MAVERICK_PRODUCTION_MANIFEST", str(PRODUCTION_MANIFEST))
    assert not self_improvement.enabled()


def _leave_production(monkeypatch) -> None:
    from maverick import self_improvement

    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)
    monkeypatch.delenv("MAVERICK_PRODUCTION_MANIFEST", raising=False)
    assert self_improvement.enabled()


def test_production_transition_aborts_unapplied_prepare_at_authorize(
    tmp_path,
    monkeypatch,
) -> None:
    controller = _controller(tmp_path / "authorize-ledger.json")
    before, after = _artifact("authorize-before"), _artifact("authorize-after")
    candidate = _candidate("production-transition-authorize")
    preparation = controller.prepare_promotion(candidate, before=before, after=after)
    assert preparation.ok

    _enter_production(monkeypatch)
    verdict = controller.authorize_prepared(preparation, artifact=before)

    assert not verdict.ok
    assert any(gate.gate == "enabled" and not gate.ok for gate in verdict.gates)
    transaction = controller.ledger.transaction(preparation.transaction_id)
    assert transaction is not None and transaction.state == "aborted"
    assert controller.ledger.get(candidate.id) is None


def test_prepare_rechecks_transition_after_evaluation_callback(
    tmp_path,
    monkeypatch,
) -> None:
    state = {"verifications": 0}

    def verifier(**_request: Any) -> tuple[bool, str]:
        state["verifications"] += 1
        _enter_production(monkeypatch)
        return True, "current governed authorization"

    controller = _controller(tmp_path / "prepare-callback-ledger.json", verifier=verifier)
    before, after = _artifact("prepare-callback-before"), _artifact(
        "prepare-callback-after"
    )
    preparation = controller.prepare_promotion(
        _candidate("production-transition-prepare-callback"),
        before=before,
        after=after,
    )

    assert state["verifications"] == 1
    assert not preparation.ok
    assert preparation.transaction is None
    assert any(
        gate.gate == "enabled" and not gate.ok for gate in preparation.verdict.gates
    )
    assert controller.ledger.transactions() == []


def test_production_transition_leaves_applied_prepare_recoverable_not_committed(
    tmp_path,
    monkeypatch,
) -> None:
    controller = _controller(tmp_path / "commit-ledger.json")
    before, after = _artifact("commit-before"), _artifact("commit-after")
    candidate = _candidate("production-transition-commit")
    preparation = controller.prepare_promotion(candidate, before=before, after=after)
    assert preparation.ok
    assert controller.authorize_prepared(preparation, artifact=before).ok

    _enter_production(monkeypatch)
    verdict = controller.commit_prepared(preparation, artifact=after)

    assert not verdict.ok
    assert any(gate.gate == "enabled" and not gate.ok for gate in verdict.gates)
    transaction = controller.ledger.transaction(preparation.transaction_id)
    assert transaction is not None and transaction.state == "prepared"
    assert controller.ledger.get(candidate.id) is None

    recovered = controller.recover_promotions(lambda _identity: after)
    assert [transaction.state for transaction in recovered] == ["prepared"]
    assert controller.ledger.get(candidate.id) is None

    _leave_production(monkeypatch)
    recovered = controller.recover_promotions(lambda _identity: after)
    assert [transaction.state for transaction in recovered] == ["committed"]
    assert controller.ledger.get(candidate.id) is not None


def test_production_transition_preserves_exact_committed_retry_truth(
    tmp_path,
    monkeypatch,
) -> None:
    controller = _controller(tmp_path / "committed-retry-ledger.json")
    before, after = _artifact("committed-before"), _artifact("committed-after")
    candidate = _candidate("production-transition-committed-retry")
    preparation = controller.prepare_promotion(candidate, before=before, after=after)
    assert preparation.ok
    assert controller.authorize_prepared(preparation, artifact=before).ok
    assert controller.commit_prepared(preparation, artifact=after).ok

    _enter_production(monkeypatch)

    # A caller may have lost the first COMMIT acknowledgement.  Re-reading an
    # exact-after durable COMMIT reports historical success even though current
    # policy denies every new or still-PREPARED self-learning transition.
    assert controller.authorize_prepared(preparation, artifact=after).ok
    commit_calls: list[object] = []

    def fail_redundant_commit(*args, **kwargs):
        commit_calls.append((args, kwargs))
        raise OSError("the write path became unavailable after durable COMMIT")

    monkeypatch.setattr(controller.ledger, "commit", fail_redundant_commit)
    assert controller.commit_prepared(preparation, artifact=after).ok
    assert commit_calls == []

    mismatch = _artifact("committed-mismatch")
    assert not controller.authorize_prepared(preparation, artifact=mismatch).ok
    assert not controller.commit_prepared(preparation, artifact=mismatch).ok
    transaction = controller.ledger.transaction(preparation.transaction_id)
    assert transaction is not None and transaction.state == "committed"
    assert controller.ledger.get(candidate.id) is not None


def test_one_phase_promotion_rechecks_transition_after_authority_callback(
    tmp_path,
    monkeypatch,
) -> None:
    state = {"verifications": 0}

    def verifier(**_request: Any) -> tuple[bool, str]:
        state["verifications"] += 1
        if state["verifications"] == 2:
            _enter_production(monkeypatch)
        return True, "current governed authorization"

    controller = _controller(tmp_path / "one-phase-ledger.json", verifier=verifier)
    candidate = _candidate("production-transition-one-phase")

    verdict = controller.promote(candidate)

    assert state["verifications"] == 2
    assert not verdict.ok
    assert any(gate.gate == "enabled" and not gate.ok for gate in verdict.gates)
    assert controller.ledger.get(candidate.id) is None


def test_production_recovery_aborts_before_but_preserves_unknown_state(
    tmp_path,
    monkeypatch,
) -> None:
    controller = _controller(tmp_path / "recovery-ledger.json")
    before, after = _artifact("recovery-before"), _artifact("recovery-after")
    preparation = controller.prepare_promotion(
        _candidate("production-transition-recovery-before"),
        before=before,
        after=after,
    )
    assert preparation.ok

    _enter_production(monkeypatch)
    recovered = controller.recover_promotions(lambda _identity: before)
    assert [transaction.state for transaction in recovered] == ["aborted"]

    _leave_production(monkeypatch)
    next_before = _artifact("unknown-before")
    next_after = _artifact("unknown-after")
    unknown = _artifact("unknown-partial")
    next_preparation = controller.prepare_promotion(
        _candidate("production-transition-recovery-unknown"),
        before=next_before,
        after=next_after,
    )
    assert next_preparation.ok

    _enter_production(monkeypatch)
    recovered = controller.recover_promotions(lambda _identity: unknown)
    assert [transaction.state for transaction in recovered] == ["prepared"]
    transaction = controller.ledger.transaction(next_preparation.transaction_id)
    assert transaction is not None and transaction.last_observed == unknown
    assert controller.ledger.get(next_preparation.verdict.candidate_id) is None


def test_commit_rechecks_enablement_after_external_authority_callback(
    tmp_path,
    monkeypatch,
) -> None:
    from maverick import self_improvement

    state = {"enabled": True, "verifications": 0}

    def verifier(**_request: Any) -> tuple[bool, str]:
        state["verifications"] += 1
        if state["verifications"] == 3:
            state["enabled"] = False
        return True, "current governed authorization"

    monkeypatch.setattr(self_improvement, "enabled", lambda: state["enabled"])
    controller = _controller(tmp_path / "callback-ledger.json", verifier=verifier)
    before, after = _artifact("callback-before"), _artifact("callback-after")
    candidate = _candidate("production-transition-callback")
    preparation = controller.prepare_promotion(candidate, before=before, after=after)
    assert preparation.ok
    assert controller.authorize_prepared(preparation, artifact=before).ok

    verdict = controller.commit_prepared(preparation, artifact=after)

    assert state["verifications"] == 3
    assert not verdict.ok
    assert any(gate.gate == "enabled" and not gate.ok for gate in verdict.gates)
    transaction = controller.ledger.transaction(preparation.transaction_id)
    assert transaction is not None and transaction.state == "prepared"
    assert controller.ledger.get(candidate.id) is None


def test_halt_transition_aborts_exact_before_authorization(tmp_path) -> None:
    state = {"halted": False}
    controller = _controller(tmp_path / "halt-authorize-ledger.json")
    controller._halt_reason = lambda _phase: "operator HALT" if state["halted"] else None
    before, after = _artifact("halt-authorize-before"), _artifact("halt-authorize-after")
    preparation = controller.prepare_promotion(
        _candidate("halt-transition-authorize"),
        before=before,
        after=after,
    )
    assert preparation.ok

    state["halted"] = True
    verdict = controller.authorize_prepared(preparation, artifact=before)

    assert not verdict.ok
    assert any(gate.gate == "killswitch" and not gate.ok for gate in verdict.gates)
    transaction = controller.ledger.transaction(preparation.transaction_id)
    assert transaction is not None and transaction.state == "aborted"


def test_halt_after_apply_blocks_direct_commit_but_not_recovery(tmp_path) -> None:
    state = {"halted": False}
    controller = _controller(tmp_path / "halt-commit-ledger.json")
    controller._halt_reason = lambda _phase: "operator HALT" if state["halted"] else None
    before, after = _artifact("halt-commit-before"), _artifact("halt-commit-after")
    candidate = _candidate("halt-transition-commit")
    preparation = controller.prepare_promotion(candidate, before=before, after=after)
    assert preparation.ok
    assert controller.authorize_prepared(preparation, artifact=before).ok

    state["halted"] = True
    verdict = controller.commit_prepared(preparation, artifact=after)

    assert not verdict.ok
    assert any(gate.gate == "killswitch" and not gate.ok for gate in verdict.gates)
    transaction = controller.ledger.transaction(preparation.transaction_id)
    assert transaction is not None and transaction.state == "prepared"
    assert controller.ledger.get(candidate.id) is None

    # Recovery records the exact effect that already happened; it does not
    # authorize a new apply and intentionally remains available under HALT.
    recovered = controller.recover_promotions(lambda _identity: after)
    assert [transaction.state for transaction in recovered] == ["committed"]
    assert controller.ledger.get(candidate.id) is not None


def test_calibration_freeze_aborts_before_apply(tmp_path) -> None:
    state = {"frozen": False}
    controller = _controller(
        tmp_path / "freeze-authorize-ledger.json",
        frozen_fn=lambda: state["frozen"],
    )
    before, after = _artifact("freeze-authorize-before"), _artifact(
        "freeze-authorize-after"
    )
    preparation = controller.prepare_promotion(
        _candidate("freeze-transition-authorize"),
        before=before,
        after=after,
    )
    assert preparation.ok

    state["frozen"] = True
    verdict = controller.authorize_prepared(preparation, artifact=before)

    assert not verdict.ok
    assert any(gate.gate == "calibration" and not gate.ok for gate in verdict.gates)
    transaction = controller.ledger.transaction(preparation.transaction_id)
    assert transaction is not None and transaction.state == "aborted"


@pytest.mark.parametrize("interlock", ["halt", "calibration"])
def test_one_phase_callback_cannot_cross_operational_interlock(
    tmp_path,
    interlock,
) -> None:
    state = {"halted": False, "frozen": False, "verifications": 0}

    def verifier(**_request: Any) -> tuple[bool, str]:
        state["verifications"] += 1
        if state["verifications"] == 2:
            state["halted" if interlock == "halt" else "frozen"] = True
        return True, "current governed authorization"

    controller = _controller(
        tmp_path / f"one-phase-{interlock}-ledger.json",
        verifier=verifier,
        frozen_fn=lambda: state["frozen"],
    )
    controller._halt_reason = lambda _phase: (
        "operator HALT" if state["halted"] else None
    )
    candidate = _candidate(f"one-phase-{interlock}-transition")

    verdict = controller.promote(candidate)

    assert state["verifications"] == 2
    assert not verdict.ok
    expected_gate = "killswitch" if interlock == "halt" else "calibration"
    assert any(gate.gate == expected_gate and not gate.ok for gate in verdict.gates)
    assert controller.ledger.get(candidate.id) is None


def test_request_scoped_approval_revocation_blocks_pending_commit(tmp_path) -> None:
    state = {"trusted": True, "calls": 0}

    def approval_verifier(_candidate: Candidate) -> str | None:
        state["calls"] += 1
        return "current-approver" if state["trusted"] else None

    controller = _controller(
        tmp_path / "approval-revocation-ledger.json",
        approval_verifier=approval_verifier,
    )
    before, after = _artifact("approval-revocation-before"), _artifact(
        "approval-revocation-after"
    )
    candidate = _code_candidate("approval-revocation", signature="signed-envelope")
    preparation = controller.prepare_promotion(candidate, before=before, after=after)
    assert preparation.ok
    assert controller.authorize_prepared(preparation, artifact=before).ok
    calls_before_revocation = state["calls"]

    state["trusted"] = False
    verdict = controller.commit_prepared(preparation, artifact=after)

    assert state["calls"] > calls_before_revocation
    assert not verdict.ok
    assert any(gate.gate == "human_approval" and not gate.ok for gate in verdict.gates)
    transaction = controller.ledger.transaction(preparation.transaction_id)
    assert transaction is not None and transaction.state == "prepared"
    assert controller.ledger.get(candidate.id) is None
    assert [tx.state for tx in controller.recover_promotions(lambda _identity: after)] == [
        "prepared"
    ]


def test_builtin_approval_key_removal_cannot_fall_back_to_unsigned(
    tmp_path,
    monkeypatch,
) -> None:
    cryptography = pytest.importorskip("cryptography")  # noqa: F841
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from maverick import approval_signing

    private = ed25519.Ed25519PrivateKey.generate()
    public_hex = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    ).hex()
    private_hex = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    ).hex()
    state = {"keys": [public_hex]}
    monkeypatch.setattr(
        approval_signing,
        "signing_enforced",
        lambda: bool(state["keys"]),
    )
    monkeypatch.setattr(
        approval_signing,
        "trusted_approver_keys",
        lambda: list(state["keys"]),
    )

    controller = _controller(tmp_path / "builtin-key-removal-ledger.json")
    before, after = _artifact("builtin-key-before"), _artifact("builtin-key-after")
    candidate = _code_candidate("builtin-key-removal", signature="pending")
    request = approval_signing.ApprovalRequest.for_candidate(candidate)
    candidate.approval_signature = approval_signing.sign_request(request, private_hex)
    preparation = controller.prepare_promotion(candidate, before=before, after=after)
    assert preparation.ok
    transaction = controller.ledger.transaction(preparation.transaction_id)
    assert transaction is not None
    assert transaction.record.payload_sha256 == request.payload_sha256

    state["keys"] = []
    verdict = controller.authorize_prepared(preparation, artifact=before)

    assert not verdict.ok
    assert any(gate.gate == "human_approval" and not gate.ok for gate in verdict.gates)
    transaction = controller.ledger.transaction(preparation.transaction_id)
    assert transaction is not None and transaction.state == "aborted"
