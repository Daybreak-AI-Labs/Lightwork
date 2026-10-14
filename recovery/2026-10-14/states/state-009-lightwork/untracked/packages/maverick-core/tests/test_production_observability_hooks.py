"""Enforcement boundaries feed the fixed production-assurance counters."""
from __future__ import annotations

import sys

import pytest
from maverick import production_observability as assurance


@pytest.fixture(autouse=True)
def _reset_counters():
    assurance.reset_for_testing()
    yield
    assurance.reset_for_testing()


def test_optional_missing_shield_records_bypass(monkeypatch):
    from maverick import shield_policy

    monkeypatch.setitem(sys.modules, "maverick_shield", None)
    monkeypatch.setattr(shield_policy, "shield_required", lambda: False)

    assert shield_policy.scan_block("external input") is None
    assert assurance.event_counts()[assurance.SHIELD_BYPASS] == 1


def test_required_missing_shield_records_enforcement_failure(monkeypatch):
    from maverick import shield_policy

    monkeypatch.setitem(sys.modules, "maverick_shield", None)
    monkeypatch.setattr(shield_policy, "shield_required", lambda: True)

    assert "required" in str(shield_policy.scan_block("external input"))
    assert assurance.event_counts()[assurance.SHIELD_ENFORCEMENT_FAILURE] == 1


def test_required_audit_false_records_refusal(monkeypatch):
    import maverick.audit as audit

    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: False)

    with pytest.raises(audit.AuditRequiredWriteFailed):
        audit.required_audit_event("tool_call")
    assert assurance.event_counts()[assurance.DURABLE_AUDIT_REFUSAL] == 1


def test_explicit_audit_refusal_is_counted_and_preserved(monkeypatch):
    import maverick.audit as audit

    refusal = audit.AuditRequiredWriteFailed("no durable writer")

    def refuse(*_args, **_kwargs):
        raise refusal

    monkeypatch.setattr(audit, "record", refuse)

    with pytest.raises(audit.AuditRefused) as observed:
        audit.required_audit_event("tool_call")
    assert observed.value is refusal
    assert assurance.event_counts()[assurance.DURABLE_AUDIT_REFUSAL] == 1


def test_forwarder_failure_is_counted_and_preserved(monkeypatch):
    from maverick.audit import forwarder

    def unavailable(*_args, **_kwargs):
        raise TimeoutError("downstream unavailable")

    monkeypatch.setattr(forwarder, "_forward", unavailable)

    with pytest.raises(TimeoutError, match="downstream unavailable"):
        forwarder.forward([], "https://siem.example")
    assert assurance.event_counts()[assurance.AUDIT_FORWARDER_FAILURE] == 1


def test_forwarder_backlog_uses_durable_siem_outbox_not_approval_rows(
    monkeypatch,
):
    from maverick.audit import siem_outbox

    monkeypatch.setattr(
        siem_outbox,
        "aggregate_outbox_snapshot",
        lambda: {
            "backlog": 7,
            "pending": 5,
            "sending": 1,
            "dead": 1,
            "bytes": 2048,
            "oldest_age_seconds": 30.0,
        },
    )

    assert assurance._audit_forwarder_backlog(object()) == 7.0


def test_indeterminate_tool_result_is_counted():
    from maverick.tool_results import ToolResultState
    from maverick.tools import _record_indeterminate_tool_result

    _record_indeterminate_tool_result(ToolResultState.INDETERMINATE)

    assert assurance.event_counts()[assurance.INDETERMINATE_SIDE_EFFECT] == 1


def test_missing_governance_in_production_fails_closed_and_counts_fallback(
    monkeypatch,
):
    from maverick import shield_policy, tool_authz

    monkeypatch.setitem(sys.modules, "maverick.governance", None)
    monkeypatch.setattr(shield_policy, "scan_block", lambda _payload: None)
    monkeypatch.setattr(tool_authz, "_record_denial", lambda *_args: None)
    monkeypatch.setattr(assurance, "production_assurance_required", lambda: True)

    denial = tool_authz.authorize("email", {}, origin="flow")

    assert "policy_unavailable" in str(denial)
    assert assurance.event_counts()[assurance.POLICY_FALLBACK] == 1
