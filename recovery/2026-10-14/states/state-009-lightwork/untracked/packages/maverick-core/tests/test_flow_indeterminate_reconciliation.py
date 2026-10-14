"""Operator reconciliation for ambiguous flow effects."""

from __future__ import annotations

import json

import pytest
from maverick.flow import reconciliation, store


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / ".maverick"))
    monkeypatch.delenv("MAVERICK_INDETERMINATE_ADJUDICATORS", raising=False)
    monkeypatch.delenv("MAVERICK_INDETERMINATE_RECONCILE_SECONDS", raising=False)
    monkeypatch.delenv("MAVERICK_INDETERMINATE_ESCALATE_SECONDS", raising=False)
    monkeypatch.delenv("MAVERICK_INDETERMINATE_ESCALATION_TARGET", raising=False)


def _run(run_id: str = "ambiguous") -> store.FlowRun:
    run = store.FlowRun(
        run_id=run_id,
        flow_id="billing-close",
        status="indeterminate",
        cursor="charge",
        error="provider acknowledgement was lost",
        owner="api:alice",
        input_data={"invoice": "inv-1"},
        data={"prepared": True},
        nodes={"charge": {"status": "indeterminate"}},
    )
    store.save_run(run)
    return run


def _request(**overrides) -> reconciliation.ResolutionRequest:
    values = {
        "resolution_id": "1" * 32,
        "disposition": "not_committed",
        "reconciliation_method": "provider.lookup",
        "reconciliation_summary": "Provider ledger has no matching operation.",
        "evidence_references": ("provider:ledger/inv-1",),
        "evidence_sha256": "a" * 64,
        "external_reference": "",
        "compensating_action": "No compensation required because no effect exists.",
        "replacement_authorized": True,
    }
    values.update(overrides)
    return reconciliation.ResolutionRequest(**values)


def test_case_exposes_bounded_evidence_actions_deadlines_and_escalation(monkeypatch):
    monkeypatch.setenv("MAVERICK_INDETERMINATE_RECONCILE_SECONDS", "120")
    monkeypatch.setenv("MAVERICK_INDETERMINATE_ESCALATE_SECONDS", "300")
    monkeypatch.setenv("MAVERICK_INDETERMINATE_ESCALATION_TARGET", "payments-on-call")
    run = _run()

    case = reconciliation.case_for_run(run, now=run.updated + 301)

    assert case["schema"] == reconciliation.CASE_SCHEMA
    assert case["reconcile_by"] == pytest.approx(run.updated + 120)
    assert case["escalate_by"] == pytest.approx(run.updated + 300)
    assert case["reconciliation_overdue"] is True
    assert case["escalation_overdue"] is True
    assert case["escalation_target"] == "payments-on-call"
    assert len(case["case_sha256"]) == 64
    assert "provider acknowledgement" in case["error"]
    assert any("without repeating" in action for action in case["operator_actions"])
    assert case["resolution"] is None


def test_resolution_is_audited_immutable_and_idempotent(monkeypatch):
    run = _run()
    calls = []
    monkeypatch.setattr(
        "maverick.audit.required_audit_event",
        lambda kind, **payload: calls.append((kind, payload)) or True,
    )

    first = reconciliation.resolve_case(
        run.run_id,
        _request(),
        adjudicated_by="user:alice",
        authority_source="dashboard_rbac_admin",
        now=run.updated + 10,
    )
    replay = reconciliation.resolve_case(
        run.run_id,
        _request(),
        adjudicated_by="user:alice",
        authority_source="dashboard_rbac_admin",
        now=run.updated + 20,
    )

    assert replay == first
    assert first["disposition"] == "not_committed"
    assert first["replacement_authorized"] is True
    assert first["timed_out"] is False
    assert reconciliation.load_resolution(run.run_id) == first
    assert reconciliation.replacement_is_authorized(run.run_id) is True
    assert len(calls) == 1
    assert calls[0][1]["record_sha256"] == first["record_sha256"]
    with pytest.raises(reconciliation.IndeterminateCaseAlreadyResolved):
        reconciliation.resolve_case(
            run.run_id,
            _request(resolution_id="2" * 32),
            adjudicated_by="user:alice",
            authority_source="dashboard_rbac_admin",
        )


def test_required_audit_refusal_leaves_case_unresolved(monkeypatch):
    run = _run()
    from maverick.audit import AuditRequiredWriteFailed

    def _refuse(*_args, **_kwargs):
        raise AuditRequiredWriteFailed("no durable audit")

    monkeypatch.setattr("maverick.audit.required_audit_event", _refuse)
    with pytest.raises(AuditRequiredWriteFailed):
        reconciliation.resolve_case(
            run.run_id,
            _request(),
            adjudicated_by="user:alice",
            authority_source="dashboard_rbac_admin",
        )
    assert reconciliation.load_resolution(run.run_id) is None


def test_resolution_rules_refuse_unsafe_replacement_or_weak_evidence(monkeypatch):
    _run()
    monkeypatch.setattr("maverick.audit.required_audit_event", lambda *_a, **_k: True)
    with pytest.raises(reconciliation.IndeterminateCaseError, match="not-committed"):
        reconciliation.resolve_case(
            "ambiguous",
            _request(disposition="committed", external_reference="provider:charge/ch_1"),
            adjudicated_by="user:alice",
            authority_source="dashboard_rbac_admin",
        )
    with pytest.raises(reconciliation.IndeterminateCaseError, match="external reference"):
        reconciliation.resolve_case(
            "ambiguous",
            _request(disposition="committed", replacement_authorized=False),
            adjudicated_by="user:alice",
            authority_source="dashboard_rbac_admin",
        )
    with pytest.raises(reconciliation.IndeterminateCaseError, match="query or fragment"):
        reconciliation.resolve_case(
            "ambiguous",
            _request(evidence_references=("https://provider.test/item?token=secret",)),
            adjudicated_by="user:alice",
            authority_source="dashboard_rbac_admin",
        )


def test_production_requires_explicit_adjudicator_allowlist(monkeypatch):
    run = _run()
    monkeypatch.setattr(
        "maverick.production_scope.production_surface_required", lambda: True
    )
    monkeypatch.setattr("maverick.audit.required_audit_event", lambda *_a, **_k: True)
    with pytest.raises(reconciliation.IndeterminateCaseError, match="not configured"):
        reconciliation.resolve_case(
            run.run_id,
            _request(),
            adjudicated_by="user:alice",
            authority_source="dashboard_rbac_admin",
        )
    monkeypatch.setenv("MAVERICK_INDETERMINATE_ADJUDICATORS", "user:bob")
    with pytest.raises(reconciliation.IndeterminateCaseError, match="lacks"):
        reconciliation.resolve_case(
            run.run_id,
            _request(),
            adjudicated_by="user:alice",
            authority_source="dashboard_rbac_admin",
        )


def test_tampered_resolution_record_is_refused(monkeypatch):
    run = _run()
    monkeypatch.setattr("maverick.audit.required_audit_event", lambda *_a, **_k: True)
    reconciliation.resolve_case(
        run.run_id,
        _request(),
        adjudicated_by="user:alice",
        authority_source="dashboard_rbac_admin",
    )
    path = reconciliation._resolution_path(run.run_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["disposition"] = "committed"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(reconciliation.IndeterminateCaseError, match="integrity"):
        reconciliation.load_resolution(run.run_id)
