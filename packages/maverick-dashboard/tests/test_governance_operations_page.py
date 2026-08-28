"""Rendered contracts for the additive AI-governance operator panels."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})


@pytest.fixture(autouse=True)
def _isolated_page(tmp_path, monkeypatch):
    from maverick import config

    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(config_path))
    monkeypatch.setenv("MAVERICK_TENANT", "governance-operator-test")
    for name in (
        "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_DASHBOARD_REQUIRE_AUTH",
        "MAVERICK_OIDC_ENABLED",
        "MAVERICK_PROXY_AUTH",
        "MAVERICK_TENANT_BY_USER",
    ):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def test_assurance_page_renders_all_governance_operator_contracts():
    response = client.get("/security/assurance")

    assert response.status_code == 200
    page = response.text
    for panel_id in (
        "governance-operations-panel",
        "ai-literacy-panel",
        "article-73-panel",
        "ai-business-intake-panel",
        "ai-cost-roi-panel",
        "ai-policy-management-panel",
        "dynamic-risk-panel",
        "runtime-guardrails-panel",
        "ai-explainability-panel",
        "ai-insurability-panel",
        "ai-conformity-panel",
        "ai-regulated-package-workbench",
        "ai-benchmark-panel",
        "ai-discovery-sensors-panel",
        "ai-identity-governance-panel",
        "ai-media-provenance-panel",
        "grc-outbound-panel",
    ):
        assert f'id="{panel_id}"' in page

    for api_contract in (
        '"/ai-literacy/requirements"',
        '"/ai-literacy/evidence-export"',
        '"/eu-ai-incidents?limit=100"',
        '"/ai-business-intake/drafts"',
        '"/ai-business-intake/submissions"',
        '"/ai-cost-roi/collectors"',
        '"/ai-cost-roi/portfolio"',
        '"/ai-policy-management/policies"',
        '"/ai-policy-management/exceptions"',
        '"/ai-policy-management/evaluations"',
        '"/ai-policy-management/preview"',
        '"/dynamic-risk/signals"',
        '"/runtime-guardrails/evaluate"',
        '"/ai-explainability/explanations"',
        '"/ai-insurability/profiles"',
        '"/ai-conformity/cases"',
        '"/ai-bank-mrm/packages"',
        '"/ai-regulated-verticals/packages"',
        '"/ai-benchmark/contributions"',
        '"/ai-benchmark/cohort-reports"',
        '"/ai-discovery-sensors/sensors"',
        '"/ai-discovery-sensors/findings"',
        '"/identity-governance/connectors"',
        '"/identity-governance/scheduler/status"',
        '"/identity-governance/scheduler/run"',
        '"/ai-media-provenance/capabilities"',
        '"/ai-media-provenance/verify"',
        '"/grc-outbound/events?limit=100"',
    ):
        assert api_contract in page

    assert "not a legal conclusion" in page
    assert "does not file reports" in page
    assert "Preview never transmits" in page
    assert "calculated ROI is" in page
    assert "not a verified financial outcome" in page
    assert "live, read-only billing" in page
    assert "Credential values are never returned" in page
    assert "OpenAI organization costs" in page
    assert "OpenAI (unsupported)" not in page
    assert "not a legal conclusion" in page
    assert "compliance certification" in page
    assert "does not discover, poll, or stream live telemetry" in page
    assert "This was not a live telemetry query" in page
    assert "versioned Ed25519 report trust roots and an HTTPS coordinator" in page
    assert "transmission remains a separate human action" in page
    assert "independently verified differential privacy" in page
    assert "control plane only" in page
    assert "deploys no live browser, network, OAuth, SaaS" in page
    assert "makes no completeness" in page
    assert "one-shot or opt-in scheduled" in page
    assert "Scheduler history retains bounded digests" in page
    assert "provider writes and makes no live-acceptance" in page
    assert "not raw provider rows" in page
    assert "complete-inventory" in page
    assert "automatic-risk claim" in page
    assert "selected local <code>c2patool</code> verifier" in page
    assert "signing still requires" in page
    assert "default verification and signing" in page
    assert "persisted. Adapter-reported validation" in page
    assert "Lightwork certification, proof of authorship or truth" in page
    assert "regulatory acceptance" in page
    assert "No backend detail was displayed" in page


def test_governance_panels_render_api_values_as_text_only():
    response = client.get("/security/assurance")

    assert response.status_code == 200
    page = response.text
    fragment = page[
        page.index('<section class="panel" id="governance-operations-panel"'):
        page.index('<p><a class="btn" href="/security">')
    ]
    assert "td.textContent = asText(value)" in fragment
    assert "target.textContent = JSON.stringify(value" in fragment
    assert ".innerHTML" not in fragment
    assert ".outerHTML" not in fragment
    assert "insertAdjacentHTML" not in fragment
    assert "document.write" not in fragment
    assert "eval(" not in fragment
    assert "No coverage conclusion is shown" in page
    assert "No deadline conclusion is shown" in page
    assert "No financial conclusion is shown" in page
    assert "No delivery conclusion is shown" in page
    assert "No policy-coverage conclusion is shown" in page
    assert "No telemetry-coverage conclusion is shown" in page
    assert "No benchmark-coverage conclusion is shown" in page
    assert "No discovery-completeness conclusion is shown" in page
    assert "No identity-completeness conclusion is shown" in page
    assert "No grant-completeness conclusion is shown" in page
    assert "No classification-completeness conclusion is shown" in page
    assert "No provenance-coverage conclusion is shown" in page


def test_policy_and_dynamic_risk_forms_cover_exact_bounded_api_contracts():
    response = client.get("/security/assurance")

    assert response.status_code == 200
    page = response.text
    for form_id in (
        "ai-policy-record-query",
        "ai-policy-preview",
        "ai-policy-create",
        "ai-policy-version",
        "ai-policy-activate",
        "ai-policy-exception-request",
        "ai-policy-exception-approve",
        "ai-policy-exception-close",
        "ai-policy-evaluate",
        "ai-policy-attest",
        "dynamic-risk-asset-query",
        "dynamic-risk-signal-query",
        "dynamic-risk-signal-ingest",
        "dynamic-risk-score",
        "dynamic-risk-resolve",
        "dynamic-risk-supersede",
        "runtime-guardrails-evaluate",
        "ai-explainability-list",
        "ai-explainability-detail",
        "ai-explainability-ingest",
        "ai-insurability-list",
        "ai-insurability-detail",
        "ai-insurability-create",
        "ai-insurability-version",
        "ai-conformity-list",
        "ai-conformity-detail",
        "ai-conformity-create",
        "ai-conformity-evidence",
        "ai-conformity-fria",
        "ai-conformity-route",
        "ai-conformity-checklist",
        "ai-conformity-submit",
        "ai-regulated-package-list",
        "ai-regulated-package-detail",
        "ai-regulated-package-prepare",
        "ai-benchmark-list",
        "ai-benchmark-detail",
        "ai-benchmark-prepare",
        "ai-benchmark-report-list",
        "ai-benchmark-report-detail",
        "ai-benchmark-report-ingest",
        "ai-discovery-sensor-list",
        "ai-discovery-sensor-detail",
        "ai-discovery-sensor-register",
        "ai-discovery-sensor-configure",
        "ai-discovery-sensor-revoke",
        "ai-discovery-observation-ingest",
        "ai-discovery-observation-list",
        "ai-discovery-observation-detail",
        "ai-discovery-findings",
        "ai-identity-governance-status",
        "ai-identity-governance-collect",
        "ai-media-provenance-capabilities",
        "ai-media-provenance-list",
        "ai-media-provenance-detail",
        "ai-media-provenance-receipt",
        "ai-media-provenance-verify",
        "ai-media-provenance-sign",
        "ai-media-provenance-release",
        "ai-media-provenance-review",
    ):
        assert f'id="{form_id}"' in page

    for path_contract in (
        "/policies/${encodeURIComponent(policyId)}/versions",
        "/policies/${encodeURIComponent(policyId)}/activate",
        "/exceptions/${encodeURIComponent(key)}/approve",
        "/exceptions/${encodeURIComponent(key)}/close",
        "/evaluations/${encodeURIComponent(key)}/attest",
        "/dynamic-risk/assets/${encodeURIComponent(assetId)}",
        "/dynamic-risk/assets/${encodeURIComponent(assetId)}/signals",
        "/dynamic-risk/assets/${encodeURIComponent(assetId)}/score",
        "/dynamic-risk/signals/${encodeURIComponent(signalId)}/resolve",
        "/dynamic-risk/signals/${encodeURIComponent(signalId)}/supersede",
        "/ai-explainability/explanations/${encodeURIComponent(id)}",
        "/ai-insurability/profiles/${encodeURIComponent(profileId)}",
        "/ai-conformity/cases/${encodeURIComponent(caseId)}",
        "${regulatedPackageBase(plane)}/${encodeURIComponent(packageId)}",
        "/ai-benchmark/contributions/${encodeURIComponent(contributionId)}",
        "/ai-benchmark/cohort-reports/${encodeURIComponent(reportId)}",
        "/ai-discovery-sensors/sensors/${encodeURIComponent(sensorId)}",
        "/ai-discovery-sensors/observations/${encodeURIComponent(observationId)}",
        "/identity-governance/connectors/${encodeURIComponent(provider)}/collect",
        "/ai-media-provenance/records/${encodeURIComponent(provenanceId)}",
        "/ai-media-provenance/receipts/${encodeURIComponent(operation)}",
    ):
        assert path_contract in page

    assert 'maxlength="16384"' in page
    assert 'maxlength="32768"' in page
    assert 'pattern="MAA-[0-9a-f]{32}"' in page
    assert 'pattern="[0-9a-f]{64}"' in page
    assert "JSON exceeds the ${maxLength}-character bound." in page
    assert "encodeURIComponent(key)" in page
    assert "encodeURIComponent(policyId)" in page
    assert "encodeURIComponent(assetId)" in page
    assert "encodeURIComponent(signalId)" in page
    assert "Raw content was not persisted" in page
    assert "Raw feature values" in page
    assert "policy facts come from its current" in page
    assert "voluntary_opt_in: true" in page
    assert "complete_discovery_assessed remains false" in page
    assert 'item.observation_id, item.identity_kind, item.enabled' in page
    assert 'item.resource_id, `${asText(item.grant_kind)} / ${asText(item.permission_id)}`' in page
    assert "Array.isArray(item.classification_codes)" in page
    assert 'enctype="multipart/form-data"' in page
    assert 'prefix + "/ai-media-provenance/sign"' in page
    assert "mediaProvenanceReceipt.elements.receipt.value = \"\"" in page
    assert "mediaProvenanceVerify.elements.file.value = \"\"" in page


def test_governance_operations_inline_javascript_has_valid_syntax(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable for the optional JavaScript syntax check")
    response = client.get("/security/assurance")

    assert response.status_code == 200
    page = response.text
    start = page.index('<section class="panel" id="governance-operations-panel"')
    end = page.index('<p><a class="btn" href="/security">', start)
    fragment = page[start:end]
    match = re.search(r"<script>(.*?)</script>", fragment, flags=re.DOTALL)
    assert match is not None
    script_path = tmp_path / "governance-operations.js"
    script_path.write_text(match.group(1), encoding="utf-8")
    result = subprocess.run(
        [node, "--check", str(script_path)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_non_admin_page_withholds_admin_governance_mutations(monkeypatch):
    from maverick_dashboard import auth

    monkeypatch.setattr(auth, "has_global_permission", lambda *_args: False)

    response = client.get("/security/assurance")

    assert response.status_code == 200
    page = response.text
    assert 'data-governance-admin="false"' in page
    assert 'id="ai-literacy-requirement-upsert"' not in page
    assert 'id="ai-literacy-assignment-set"' not in page
    assert 'id="article-73-route"' not in page
    assert 'id="article-73-report-draft"' not in page
    assert 'id="grc-outbound-sync"' not in page
    assert 'id="ai-cost-collector-run"' not in page
    assert 'id="ai-policy-create"' not in page
    assert 'id="ai-policy-version"' not in page
    assert 'id="ai-policy-activate"' not in page
    assert 'id="ai-policy-exception-request"' not in page
    assert 'id="ai-policy-exception-approve"' not in page
    assert 'id="ai-policy-exception-close"' not in page
    assert 'id="ai-policy-evaluate"' not in page
    assert 'id="ai-policy-attest"' not in page
    assert 'id="dynamic-risk-signal-ingest"' not in page
    assert 'id="dynamic-risk-score"' not in page
    assert 'id="dynamic-risk-resolve"' not in page
    assert 'id="dynamic-risk-supersede"' not in page
    assert 'id="ai-explainability-review"' not in page
    assert 'id="ai-insurability-approve"' not in page
    assert 'id="ai-conformity-approve"' not in page
    assert 'id="ai-regulated-package-approve"' not in page
    assert 'id="ai-benchmark-approve"' not in page
    assert 'id="ai-discovery-sensor-approve"' not in page
    assert 'id="ai-discovery-revocation-approve"' not in page
    assert 'id="ai-media-provenance-sign"' not in page
    assert 'id="ai-media-provenance-review"' not in page
    assert "/requirements/${encodeURIComponent(key)}" not in page
    assert "/routing`" not in page
    assert "/reports/${report}/approve" not in page
    assert "/policies/${encodeURIComponent(policyId)}/versions" not in page
    assert "/policies/${encodeURIComponent(policyId)}/activate" not in page
    assert "/exceptions/${encodeURIComponent(key)}/approve" not in page
    assert "/evaluations/${encodeURIComponent(key)}/attest" not in page
    assert "/dynamic-risk/signals/${encodeURIComponent(signalId)}/resolve" not in page
    assert "/dynamic-risk/signals/${encodeURIComponent(signalId)}/supersede" not in page
    assert "/ai-explainability/explanations/${encodeURIComponent(id)}/review" not in page
    assert "/ai-benchmark/contributions/${encodeURIComponent(contributionId)}/approve" not in page
    assert "/ai-discovery-sensors/sensors/${encodeURIComponent(sensorId)}/approve" not in page
    assert "/ai-discovery-sensors/sensors/${encodeURIComponent(sensorId)}/revocation/approve" not in page
    assert 'prefix + "/ai-media-provenance/sign"' not in page
    assert "/ai-media-provenance/records/${encodeURIComponent(provenanceId)}/review" not in page
    assert 'sendJson("/ai-policy-management/policies"' not in page
    assert 'sendJson("/ai-policy-management/exceptions"' not in page
    assert 'sendJson("/ai-policy-management/evaluations"' not in page
    assert 'sendJson("/dynamic-risk/signals"' not in page
    # Tenant operators retain the non-admin intake, preview, and evidence tools.
    assert 'id="ai-business-intake-start"' in page
    assert 'id="grc-outbound-preview"' in page
    assert 'id="ai-literacy-export"' in page
    assert 'id="ai-policy-record-query"' in page
    assert 'id="ai-policy-preview"' in page
    assert 'id="dynamic-risk-asset-query"' in page
    assert 'id="dynamic-risk-signal-query"' in page
    assert 'id="ai-explainability-ingest"' in page
    assert 'id="ai-insurability-create"' in page
    assert 'id="ai-conformity-create"' in page
    assert 'id="ai-regulated-package-prepare"' in page
    assert 'id="ai-benchmark-prepare"' in page
    assert 'id="ai-benchmark-report-ingest"' in page
    assert 'id="ai-discovery-sensor-register"' in page
    assert 'id="ai-discovery-sensor-configure"' in page
    assert 'id="ai-discovery-sensor-revoke"' in page
    assert 'id="ai-discovery-observation-ingest"' in page
    assert 'id="ai-identity-governance-collect"' in page
    assert 'id="ai-media-provenance-receipt"' in page
    assert 'id="ai-media-provenance-verify"' in page
    assert 'id="ai-media-provenance-release"' in page


def test_admin_page_explains_trusted_article_73_authority_boundary():
    response = client.get("/security/assurance")

    assert response.status_code == 200
    page = response.text
    assert 'data-governance-admin="true"' in page
    assert "administrator-configured trusted" in page
    assert "directory" in page
    assert "cannot supply or override them" in page
    assert 'id="article-73-route"' in page
    assert 'id="grc-outbound-sync"' in page
    assert 'id="ai-cost-collector-run"' in page
    assert 'id="ai-policy-activate"' in page
    assert 'id="ai-policy-exception-approve"' in page
    assert 'id="ai-policy-attest"' in page
    assert 'id="dynamic-risk-signal-ingest"' in page
    assert 'id="dynamic-risk-resolve"' in page
    assert 'id="ai-explainability-review"' in page
    assert 'id="ai-insurability-approve"' in page
    assert 'id="ai-conformity-approve"' in page
    assert 'id="ai-regulated-package-approve"' in page
    assert 'id="ai-benchmark-approve"' in page
    assert 'id="ai-discovery-sensor-approve"' in page
    assert 'id="ai-discovery-revocation-approve"' in page
    assert 'id="ai-identity-governance-collect"' in page
    assert 'id="ai-media-provenance-sign"' in page
    assert 'id="ai-media-provenance-review"' in page
    assert 'sendJson("/ai-policy-management/policies"' in page
    assert 'sendJson("/ai-policy-management/exceptions"' in page
    assert 'sendJson("/ai-policy-management/evaluations"' in page
    assert 'sendJson("/dynamic-risk/signals"' in page
    assert "/collect`" in page
    assert "/sync`" in page
    assert "authority_role:" not in page
    assert "authority_refs:" not in page
