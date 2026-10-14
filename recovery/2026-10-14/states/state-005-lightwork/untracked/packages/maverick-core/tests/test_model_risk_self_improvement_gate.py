"""Exact-payload Model Risk authority in the DGM promotion pipeline."""

from __future__ import annotations

from typing import Any

import pytest
from maverick.approval_signing import payload_digest
from maverick.self_improvement import Candidate, SelfImprovementController


def _candidate(*, payload: Any = "candidate-v1", **overrides: Any) -> Candidate:
    values: dict[str, Any] = {
        "rung": "config",
        "summary": "bounded configuration candidate",
        "baseline_score": 0.5,
        "candidate_score": 0.7,
        "samples": 5,
        "payload": payload,
        "rollback": "snapshot-1",
    }
    values.update(overrides)
    return Candidate(**values)


def _controller(verifier: Any) -> SelfImprovementController:
    return SelfImprovementController(
        frozen_fn=lambda: False,
        audit_fn=lambda **_payload: None,
        model_risk_verifier=verifier,
        now=lambda: 1_726_000_000.0,
    )


def test_model_risk_gate_receives_exact_candidate_authority_tuple() -> None:
    received: dict[str, Any] = {}

    def verify(**request: Any) -> tuple[bool, str]:
        received.update(request)
        return True, "current governed authorization"

    candidate = _candidate(payload={"temperature": 0.2}, id="candidate-42")
    verdict = _controller(verify).evaluate(candidate)

    assert verdict.ok
    assert received == {
        "candidate_id": "candidate-42",
        "rung": "config",
        "payload_sha256": payload_digest({"temperature": 0.2}),
        "now": 1_726_000_000.0,
    }
    assert any(gate.gate == "model_risk_assurance" and gate.ok for gate in verdict.gates)


def test_model_risk_gate_ignores_stale_self_reported_payload_digest() -> None:
    approved_digest = payload_digest("benign-candidate")
    received: dict[str, Any] = {}

    def verify(**request: Any) -> tuple[bool, str]:
        received.update(request)
        if request["payload_sha256"] == approved_digest:
            return True, ""
        return False, "exact payload is not authorized"

    candidate = _candidate(
        payload="swapped-candidate",
        payload_sha256=approved_digest,
        id="candidate-swapped",
    )
    verdict = _controller(verify).evaluate(candidate)

    assert not verdict.ok
    assert received["payload_sha256"] == payload_digest("swapped-candidate")
    assert received["payload_sha256"] != approved_digest
    assert any(
        gate.gate == "model_risk_assurance"
        and not gate.ok
        and gate.reason == "exact payload is not authorized"
        for gate in verdict.gates
    )


@pytest.mark.parametrize(
    "verifier",
    [
        lambda **_request: (_ for _ in ()).throw(RuntimeError("store unavailable")),
        lambda **_request: True,
        lambda **_request: (1, "not a strict boolean"),
        lambda **_request: (False, 7),
    ],
)
def test_model_risk_gate_fails_closed_on_verifier_failure(verifier: Any) -> None:
    verdict = _controller(verifier).evaluate(_candidate())

    assert not verdict.ok
    assert any(gate.gate == "model_risk_assurance" and not gate.ok for gate in verdict.gates)
