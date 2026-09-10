"""Focused regressions for truthful UI authority and recoverable async actions."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

PACKAGE = Path(__file__).parents[1] / "maverick_dashboard"
STATIC = PACKAGE / "static"
TEMPLATES = PACKAGE / "templates"


def _button(markup: str, element_id: str) -> str:
    match = re.search(
        rf"<button\b[^>]*\bid=[\"']{re.escape(element_id)}[\"'][^>]*>",
        markup,
    )
    assert match, f"missing button {element_id}"
    return match.group(0)


def test_tenant_operator_controls_do_not_leak_global_authority(monkeypatch, tmp_path):
    from maverick import config, runtime_overrides, world_model
    from maverick_dashboard import api
    from maverick_dashboard import app as dashboard_app

    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr(runtime_overrides, "OVERRIDES_PATH", tmp_path / "overrides.toml")
    monkeypatch.setattr(dashboard_app, "has_global_permission", lambda _r, _p: False)
    monkeypatch.setattr(dashboard_app, "has_permission", lambda _r, _p: True)
    monkeypatch.setattr(api, "_shared_halt_backend", lambda: True)
    config.reset_config_cache()

    client = TestClient(
        dashboard_app.app,
        headers={"Origin": "http://testserver"},
    )
    learning = client.get("/learning")
    permissions = client.get("/permissions")

    assert learning.status_code == 200
    assert permissions.status_code == 200
    assert 'data-can-halt="false"' in learning.text
    assert "changing deployment-wide learning controls requires a global administrator" in learning.text
    assert "disabled" in _button(learning.text, "lrn-enable")
    assert "disabled" in _button(learning.text, "lrn-auto-evolve")
    assert "only a global administrator can change where work runs" in permissions.text
    assert "var canManageSandbox = false" in permissions.text
    # Tenant operate authority remains truthful and independently enables the
    # tenant-scoped tool overlay; it must not be collapsed into global admin.
    assert "changing tool admission requires" not in permissions.text
    assert "perm-toggle" in permissions.text


def test_destructive_clients_confirm_exact_targets_and_recover_single_flight():
    bulk = (STATIC / "lightwork-consent-bulk-jobs.js").read_text(encoding="utf-8")
    reports = (STATIC / "lightwork-third-party-executive-reports.js").read_text(
        encoding="utf-8"
    )

    assert "var operationInFlight = false" in bulk
    assert "Another operation is already running" in bulk
    assert "operationInFlight = false" in bulk
    assert "delete shell.dataset.operationInFlight" in bulk
    assert "Run the next executable batch for consent bulk job “" in bulk
    assert "Grant exactly " in bulk
    assert "Permanently purge up to " in bulk

    assert "var destructiveInFlight = false" in reports
    assert "Another report mutation is already running" in reports
    assert "destructiveInFlight = false" in reports
    assert "delete root.dataset.destructiveInFlight" in reports
    assert "Revoke executive report “" in reports
    assert "Permanently purge up to " in reports

    # The focused opener must remain enabled until the shared confirmation
    # dialog captures it; disable only after the operator confirms.
    assert "function handle(handler, options)" in bulk
    assert "return handler(event, disableControls)" in bulk
    assert bulk.count("{ deferDisable: true }") == 7
    report_destructive = reports[
        reports.index("async function runDestructive") :
        reports.index("function setMutationReady")
    ]
    assert report_destructive.index("await confirmDestructive") < report_destructive.index(
        "control.disabled = true"
    )


def test_async_drafts_and_assessment_failures_stay_with_visible_context():
    workflow = (TEMPLATES / "workflow_builder.html").read_text(encoding="utf-8")
    assessments = (TEMPLATES / "assessments.html").read_text(encoding="utf-8")

    draft = workflow[workflow.index("async function draft()") : workflow.index("// ---- refine:")]
    assert "var draftMode = mode" in draft
    assert "var draftStillVisible = mode === draftMode" in draft
    assert "renderPlaybook(data, draftStillVisible)" in draft
    assert "renderDraft(data, draftStillVisible)" in draft
    assert "if (mode !== draftMode)" in draft
    assert "setForm(mode)" in draft
    assert "Switch back to review it" in draft

    assert 'id="as-page-msg"' in assessments
    assert "document.getElementById('as-detail-msg') || pageMsg" in assessments
    assert "Could not load the assessment register (HTTP " in assessments
    assert "The assessment register is temporarily unavailable" in assessments

    assert workflow.count("if (shouldScroll !== false)") == 2
