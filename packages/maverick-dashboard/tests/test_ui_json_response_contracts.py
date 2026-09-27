"""Source-level guardrails for fail-closed governance workbench responses."""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES = Path(__file__).parents[1] / "maverick_dashboard" / "templates"
STATIC = Path(__file__).parents[1] / "maverick_dashboard" / "static"

FAIL_CLOSED_TEMPLATES = (
    "agents.html",
    "compliance_scope_actions.html",
    "compliance_scoping.html",
    "consent_delivery.html",
    "consent_experiments.html",
    "consent_preferences.html",
    "consent_profile_data.html",
    "consent_public_clients.html",
    "consent_publishing.html",
    "consent_scan_inventory.html",
    "consent_scan_notice_proposals.html",
    "consumer_trust_center_authority.html",
    "data_use_governance.html",
    "data_use_providers.html",
    "ekko.html",
    "policy_publication_links.html",
    "privacy_data_discovery.html",
    "privacy_notice_governance.html",
    "privacy_regulatory_intelligence.html",
    "privacy_rights_consumer_delivery.html",
    "privacy_rights_orchestration.html",
    "redact.html",
    "third_party_collaboration.html",
    "third_party_intelligence.html",
    "third_party_lifecycle.html",
)


def test_governance_workbenches_fail_closed_on_invalid_json() -> None:
    swallowed_json = re.compile(
        r"catch\s*\([^)]*\)\s*\{\s*(?:body|payload|result)\s*=\s*\{\s*\}",
        re.DOTALL,
    )
    raw_body_fallback = re.compile(r"\{\s*detail\s*:\s*(?:text|responseText)\s*\}")

    for name in FAIL_CLOSED_TEMPLATES:
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        if name == "privacy_data_discovery.html":
            source += (STATIC / "lightwork-privacy-data-discovery.js").read_text(
                encoding="utf-8"
            )
        assert "Request failed: the server returned an invalid JSON response." in source, name
        assert swallowed_json.search(source) is None, name
        assert raw_body_fallback.search(source) is None, name


def test_automation_run_queue_requires_a_json_response_before_success() -> None:
    source = (TEMPLATES / "automations.html").read_text(encoding="utf-8")
    parse = source.index("try { j = await r.json(); }")
    success = source.index("if (r.ok) { flowsMsg.textContent = 'Run queued.'; }", parse)
    assert parse < success
    assert "Could not start run: invalid server response." in source[parse:success]


def test_agent_department_deploy_does_not_turn_invalid_json_into_success() -> None:
    source = (TEMPLATES / "agents.html").read_text(encoding="utf-8")
    assert ".json().catch(function () { return {}; })" not in source
    assert source.count(
        ".json().catch(function () { throw new Error('Request failed: the server returned an invalid JSON response.'); })"
    ) == 2
