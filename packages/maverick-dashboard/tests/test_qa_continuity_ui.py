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


def test_settings_warns_before_one_section_discards_another():
    source = (TEMPLATES / "settings.html").read_text(encoding="utf-8")
    assert "var dirty = new Set()" in source
    assert "otherDirty" in source
    assert "discard unsaved changes" in source
    assert "window.addEventListener('beforeunload'" in source


def test_goal_destructive_actions_and_attachment_failures_recover():
    source = (TEMPLATES / "chat_goal.html").read_text(encoding="utf-8")
    assert "Could not stop the task. Check your connection and try again." in source
    assert "if (!accepted) cancelBtn.disabled = false" in source
    assert "Revoke this share link?" in source
    assert "Could not turn off the link. Check your connection" in source
    assert "Attachments could not be loaded. You can still add a file" in source
    assert "panel.style.display = 'block'" in source
    assert 'id="attach-add" multiple aria-label="Add files to this task"' in source
    assert 'tabindex="-1"' in source


def test_webhook_deletion_is_confirmed_and_surfaces_server_failure():
    source = (TEMPLATES / "workflow_builder.html").read_text(encoding="utf-8")
    assert "Other apps will no longer be able to start this workflow." in source
    assert "var detail = plainErr(resp, 'delete it');" in source
    assert "var message = plainErr(e, 'delete it');" in source
    assert "'Delete the start ' + t.name" in source


def test_high_impact_learning_workforce_and_agent_actions_confirm():
    learning = (TEMPLATES / "learning.html").read_text(encoding="utf-8")
    workforce = (TEMPLATES / "workforce.html").read_text(encoding="utf-8")
    agents = (TEMPLATES / "agents.html").read_text(encoding="utf-8")
    assert "This changes the deployment-wide learning posture" in learning
    assert "Enable automatic application of proven workflow improvements?" in learning
    assert "Scheduled and triggered workflows may start running" in learning
    assert "This creates an operational fleet and its specialists" in workforce
    assert "This adds its specialists so you can give the team work" in agents
    assert "Its allowed actions and steps take effect for everyone who uses it" in agents
    assert "Your changes to its allowed actions and steps will be removed" in agents


def test_privacy_decision_network_failure_restores_the_draft_control():
    source = (TEMPLATES / "privacy.html").read_text(encoding="utf-8")
    assert "transient rejection never destroys" in source
    assert "btn.disabled = false" in source
    assert "default: decisionDrafts[btn.dataset.id]" in source
    assert "your reason is kept for the next try" in source


def test_repeated_department_actions_name_their_target():
    workforce = (TEMPLATES / "workforce.html").read_text(encoding="utf-8")
    agents = (TEMPLATES / "agents.html").read_text(encoding="utf-8")
    assert 'aria-label="Deploy {{ d.title }} team"' in workforce
    assert 'aria-label="View {{ d.title }} team"' in workforce
    assert "aria-label=\"Deploy ' + esc(d.title) + ' team\"" in agents
    assert "aria-label=\"View ' + esc(d.title) + ' details\"" in agents
