"""config-lint must recognize the flagship governance config keys.

Client-journey finding (round 5): a regulated client configured the two
headline governance features straight from the docs --

    [audit]
    sign = true
    [capabilities]
    enforce = true

-- and `maverick config-lint` flagged [audit] as an unknown section ("did
you mean auth?") and capabilities.enforce as an unknown key, telling them
their flagship signed-audit + capability-enforcement config looked like
typos. Both are real keys the runtime reads (audit/writer.py:_resolve_signing,
capability.py:capability_enforced). The deferred_tools knob was missing too.
"""
from __future__ import annotations

import pytest
from maverick.config_lint import lint_config


def _unknown(findings, section):
    return [f for f in findings if f.section == section
            and ("unknown" in f.message.lower())]


def test_audit_sign_is_recognized():
    findings = lint_config({"audit": {"sign": True}})
    assert not _unknown(findings, "audit"), [f.message for f in findings]


def test_capabilities_enforce_is_recognized():
    findings = lint_config({"capabilities": {"enforce": True}})
    assert not [f for f in findings if "enforce" in (f.key or "")], \
        [f.message for f in findings]


def test_capabilities_deferred_tools_is_recognized():
    findings = lint_config({"capabilities": {"deferred_tools": False}})
    assert not [f for f in findings if "deferred_tools" in (f.key or "")], \
        [f.message for f in findings]


def test_full_governed_config_lints_clean():
    cfg = {
        "providers": {"vllm": {"base_url": "http://localhost:8000/v1"}},
        "budget": {"max_dollars": 2.0},
        "safety": {"profile": "strict"},
        "audit": {"sign": True},
        "capabilities": {"enforce": True},
        "roles": {"ap_clerk": {"max_risk": "low"}},
    }
    errors = [f for f in lint_config(cfg) if f.severity == "error"]
    unknown = [f for f in lint_config(cfg) if "unknown" in f.message.lower()]
    assert not errors, [f.message for f in errors]
    assert not unknown, [f.message for f in unknown]


def test_a_real_typo_is_still_caught():
    # The fix must not blunt the lint: a genuine unknown key still warns.
    findings = lint_config({"capabilities": {"enforcee": True}})
    assert any("enforcee" in (f.key or "") for f in findings), findings


def test_evidence_gateway_is_closed_and_boolean():
    assert lint_config({"evidence_gateway": {"enable": True}}) == []

    unknown = lint_config({"evidence_gateway": {"enabel": True}})
    assert any(
        finding.section == "evidence_gateway"
        and finding.key == "enabel"
        and "unknown" in finding.message.lower()
        for finding in unknown
    )

    wrong_type = lint_config({"evidence_gateway": {"enable": "true"}})
    assert any(
        finding.section == "evidence_gateway"
        and finding.key == "enable"
        and finding.severity == "error"
        for finding in wrong_type
    )


def test_grc_vendor_transport_tables_are_known_but_root_typos_are_not():
    config = {
        "grc_outbound_sync": {
            "enable": True,
            "targets": ["servicenow_irm", "archer"],
            "max_attempts_per_revision": 8,
            "servicenow_irm": {
                "credential_secret_ref": "SERVICENOW_TOKEN",
                "timeout_seconds": 15,
            },
            "archer": {
                "credential_secret_ref": "ARCHER_GRC_TOKEN",
                "max_response_bytes": 262144,
            },
        }
    }
    assert not _unknown(lint_config(config), "grc_outbound_sync")

    findings = lint_config({"grc_outbound_sync": {"servicenow_imr": {}}})
    assert any(
        item.section == "grc_outbound_sync"
        and item.key == "servicenow_imr"
        and "unknown" in item.message.lower()
        for item in findings
    )


def test_ai_cost_collector_tables_are_known_but_root_typos_are_not():
    config = {
        "ai_cost_roi": {
            "enable": True,
            "collector_poll_seconds": 86400,
            "collector_lookback_days": 1,
            "collector_limits": {"record_cap": 1000},
            "aws": {"enable": False},
            "azure": {"enable": False},
            "gcp": {"enable": False},
            "openai": {"enable": False},
        }
    }
    assert not _unknown(lint_config(config), "ai_cost_roi")

    findings = lint_config({"ai_cost_roi": {"collector_lookback_day": 1}})
    assert any(
        item.section == "ai_cost_roi"
        and item.key == "collector_lookback_day"
        and "unknown" in item.message.lower()
        for item in findings
    )


@pytest.mark.parametrize(
    "section",
    [
        "ai_dynamic_risk",
        "ai_policy_management",
        "ai_runtime_guardrails",
        "ai_explainability",
        "ai_conformity_workflow",
        "ai_insurability",
        "ai_regulated_verticals",
        "ai_bank_mrm",
        "ai_benchmark_exchange",
        "ai_discovery_sensors",
        "ai_media_provenance",
        "ai_identity_governance_connectors",
    ],
)
def test_new_operational_ai_governance_gates_are_closed_and_boolean(section):
    assert lint_config({section: {"enable": True}}) == []

    unknown = lint_config({section: {"enabel": True}})
    assert any(
        finding.section == section
        and finding.key == "enabel"
        and "unknown" in finding.message.lower()
        for finding in unknown
    )

    wrong_type = lint_config({section: {"enable": "true"}})
    assert any(
        finding.section == section
        and finding.key == "enable"
        and finding.severity == "error"
        for finding in wrong_type
    )
