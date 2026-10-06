"""Focused regression contracts for the QA continuity/UI remediation."""
from __future__ import annotations

from pathlib import Path

import pytest
from maverick_dashboard.api_schemas import (
    AssessmentEvidenceIn,
    AssessmentRefreshIn,
    AssessmentReviewIn,
    WorkflowSaveIn,
)
from pydantic import ValidationError

TEMPLATES = Path(__file__).parents[1] / "maverick_dashboard" / "templates"


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (AssessmentReviewIn, {"lens": "privacy", "decision": "accepted"}),
        (
            AssessmentEvidenceIn,
            {"lens": "security", "control": "AC-1", "evidence": "ticket-1"},
        ),
    ],
)
def test_existing_assessment_mutations_require_revision(model, payload):
    with pytest.raises(ValidationError, match="expected_revision"):
        model.model_validate(payload)


def test_assessment_refresh_represents_create_and_compare_and_swap():
    with pytest.raises(ValidationError, match="expected_revision"):
        AssessmentRefreshIn()
    assert AssessmentRefreshIn(expected_revision=0).expected_revision == 0
    assert AssessmentReviewIn(
        expected_revision=3, lens="privacy", decision="accepted"
    ).expected_revision == 3


def test_workflow_overwrite_requires_a_reviewed_generation():
    payload = {
        "name": "weekly",
        "title": "Weekly",
        "body": "## Steps\n1. summarize",
        "overwrite": True,
    }
    with pytest.raises(ValidationError, match="expected_generation"):
        WorkflowSaveIn.model_validate(payload)
    assert WorkflowSaveIn.model_validate(
        {**payload, "expected_generation": 4}
    ).expected_generation == 4


def test_assessment_page_sends_revision_on_every_record_mutation():
    source = (TEMPLATES / "assessments.html").read_text(encoding="utf-8")
    assert "JSON.stringify({ expected_revision: a.revision })" in source
    assert "?expected_revision=' + encodeURIComponent(a.revision)" in source
    assert source.count("expected_revision: a.revision") >= 3
    assert "expected_revision: 0" in source
    assert "result.status !== 409" in source
    assert "This assessment changed in another tab" in source
    assert "Reload latest assessment" in source
    assert "open(assessment.kind, assessment.subject, reloadButton)" in source


def test_flow_designer_has_loss_and_double_submit_guards():
    source = (TEMPLATES / "flow_designer.js").read_text(encoding="utf-8")
    markup = (TEMPLATES / "flow_designer.html").read_text(encoding="utf-8")
    assert "window.addEventListener('beforeunload'" in source
    assert "window.history.replaceState" in source
    assert "saveInFlight" in source and "syncSaveButtons" in source
    assert "FDCore.commitRawParams(rootFlow)" in source
    assert "if (n._paramsRaw !== undefined)" in source
    assert "g.setAttribute('tabindex', '0')" in source
    assert "If yes, go to" in source
    assert 'aria-describedby="fd-canvas-help"' in markup


def test_workflow_builder_preserves_context_and_traps_picker_focus():
    source = (TEMPLATES / "workflow_builder.html").read_text(encoding="utf-8")
    draft_start = source.index("async function draft()")
    success_reset = source.index("wfEditGeneration = null", draft_start)
    response_check = source.index("if (!resp.ok)", draft_start)
    assert success_reset > response_check
    assert "expected_generation is required when overwrite is true" not in source
    assert "Open its latest version for conflict-safe editing" in source
    assert "pickerFocusables" in source
    assert "document.activeElement === last" in source
    assert "var templateWorkspaceStarted = false" in source
    assert "var playbookWorkspaceStarted = false" in source
    assert "if (isTpl && templateWorkspaceStarted) preview.hidden = false" in source
    assert "if (!isTpl && playbookWorkspaceStarted) pbPreview.hidden = false" in source


def test_settings_saves_one_section_and_keeps_other_drafts():
    source = (TEMPLATES / "settings.html").read_text(encoding="utf-8")
    assert "var dirty = new Set()" in source
    assert "dirty.delete(form)" in source
    assert "dirty.clear()" not in source
    assert "Your other section drafts are kept" in source
    assert "var response = await fetch(url" in source
    assert "window.addEventListener('beforeunload'" in source


def test_goal_destructive_actions_and_attachment_failures_recover():
    source = (TEMPLATES / "chat_goal.html").read_text(encoding="utf-8")
    assert "cgErr(r, 'stop the task')" in source
    assert "The stop response was lost. Checking task state" in source
    assert "openPollingFallback(true)" in source
    assert "if (!accepted) cancelBtn.disabled = false" in source
    sharing = (TEMPLATES.parent / "static" / "lightwork-deliverable-sharing.js").read_text(encoding="utf-8")
    assert "mvConfirm" in sharing
    assert "window.mvRenderError(status, error, 'revoke this link'); button.disabled = denied" in sharing
    assert "Attachments could not be loaded. You can still add a file" in source
    assert "panel.style.display = 'block'" in source
    assert 'id="attach-add" multiple aria-label="{{ ut(\'Add files to this task\') }}"' in source
    assert 'tabindex="-1"' in source


def test_webhook_deletion_is_confirmed_and_surfaces_server_failure():
    source = (TEMPLATES / "workflow_builder.html").read_text(encoding="utf-8")
    assert "Other apps will no longer be able to start this workflow." in source
    assert "var detail = plainErr(resp, 'delete it');" in source
    assert "var message = plainErr(e, 'delete it');" in source
    assert "translated('Delete the start {name}',{name:t.name})" in source


def test_high_impact_learning_workforce_and_agent_actions_confirm():
    learning = (TEMPLATES / "learning.html").read_text(encoding="utf-8")
    workforce = (TEMPLATES / "workforce.html").read_text(encoding="utf-8")
    agents = (TEMPLATES / "agents.html").read_text(encoding="utf-8")
    assert "This changes the deployment-wide learning posture" in learning
    assert "Enable automatic application of proven workflow improvements?" in learning
    assert "Scheduled and triggered workflows may start running" in learning
    teams = (TEMPLATES.parent / "static" / "lightwork-department-team-updates.js").read_text(encoding="utf-8")
    overrides = (TEMPLATES.parent / "static" / "lightwork-agent-override-changes.js").read_text(encoding="utf-8")
    # Setting up or updating a team opens a before-and-after review first.
    assert "await window.LWDepartmentTeamUpdates.open(key, btn);" in workforce
    assert agents.count("await window.LWDepartmentTeamUpdates.open(") == 2
    assert "Review the complete before and after. Saving does not start any tasks." in teams
    assert "Its allowed actions, steps and independence settings take effect for everyone who uses it" in agents
    # Reset and removal show a reviewed consequence and need a ticked consent.
    assert "'Review reset to the built-in definition'" in overrides
    assert "apply.disabled = busy || !consent.checked" in overrides


def test_privacy_decision_network_failure_restores_the_draft_control():
    source = (TEMPLATES / "privacy.html").read_text(encoding="utf-8")
    assert "transient rejection never destroys" in source
    assert "btn.disabled = false" in source
    assert "default: decisionDrafts[btn.dataset.id]" in source
    assert "your reason is kept for the next try" in source


def test_repeated_department_actions_name_their_target():
    workforce = (TEMPLATES / "workforce.html").read_text(encoding="utf-8")
    agents = (TEMPLATES / "agents.html").read_text(encoding="utf-8")
    assert 'aria-label="Set up {{ d.title }} team"' in workforce
    assert 'aria-label="View {{ d.title }} team"' in workforce
    assert "aria-label=\"Deploy ' + esc(d.title) + ' team\"" in agents
    assert "aria-label=\"View ' + esc(d.title) + ' details\"" in agents
