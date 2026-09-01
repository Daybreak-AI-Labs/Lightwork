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


def test_flow_designer_has_loss_and_double_submit_guards():
    source = (TEMPLATES / "flow_designer.js").read_text(encoding="utf-8")
    markup = (TEMPLATES / "flow_designer.html").read_text(encoding="utf-8")
    assert "window.addEventListener('beforeunload'" in source
    assert "window.history.replaceState" in source
    assert "saveInFlight" in source and "syncSaveButtons" in source
    assert "FDCore.commitRawParams(rootFlow)" in source
    assert "n._paramsRaw !== undefined ? n._paramsRaw" in source
    assert "g.setAttribute('tabindex', '0')" in source
    assert "Yes path — next step" in source
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


def test_settings_warns_before_one_section_discards_another():
    source = (TEMPLATES / "settings.html").read_text(encoding="utf-8")
    assert "var dirty = new Set()" in source
    assert "otherDirty" in source
    assert "discard unsaved changes" in source
    assert "window.addEventListener('beforeunload'" in source
