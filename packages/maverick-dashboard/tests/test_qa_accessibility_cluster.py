from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = (
    Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates"
)


@pytest.mark.parametrize(
    "template_name",
    (
        "assessment_evidence_autopopulation.html",
        "consent_application_scanner.html",
        "consent_custom_indexes.html",
        "consent_global_opt_out.html",
        "consent_publish_settings.html",
        "ekko.html",
        "governed_document_vault.html",
    ),
)
def test_extending_pages_leave_the_main_landmark_to_base(template_name: str) -> None:
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    source = (TEMPLATES / template_name).read_text(encoding="utf-8")

    assert base.count("<main") == 1
    assert base.count("</main>") == 1
    assert "{% extends \"base.html\" %}" in source
    assert "<main" not in source
    assert "</main>" not in source

    heading_levels = [int(level) for level in re.findall(r"<h([1-6])(?:\s|>)", source)]
    if "{% call hero(" in source or "{{ hero(" in source:
        # _hero.html renders the page title as the single H1.
        heading_levels.insert(0, 1)
    assert heading_levels.count(1) == 1
    assert set(heading_levels) == set(range(1, max(heading_levels) + 1))


@pytest.mark.parametrize(
    ("template_name", "labels"),
    (
        (
            "permissions.html",
            (
                'aria-label="Block the {{ t.name | humanize }} tool"',
                'aria-label="Allow the {{ t.name | humanize }} tool"',
                "btn.setAttribute('aria-label'",
            ),
        ),
        (
            "plugins.html",
            (
                'aria-label="Turn off {{ pname }}"',
                'aria-label="Turn on {{ pname }}"',
                'aria-label="Use your organization\'s default for {{ pname }}"',
                'aria-label="Install {{ plabel }}"',
            ),
        ),
        ("mcp.html", ('aria-label="Test {{ label }}"', 'aria-label="Remove {{ label }}"')),
        (
            "trust.html",
            (
                'aria-label="Restore {{ a.id }} external agent"',
                'aria-label="Revoke {{ a.id }} external agent"',
                'aria-label="Remove {{ a.id }} external agent"',
            ),
        ),
        (
            "users.html",
            (
                'aria-label="Delete the {{ name | role_label }} role"',
                'aria-label="Save role for {{ name_of(principal) }}"',
                'aria-label="Remove {{ name_of(principal) }}\'s role"',
                'aria-label="Save departments for {{ name_of(principal) }}"',
                'aria-label="Use standard departments for {{ name_of(principal) }}"',
                'aria-label="Cancel the invitation for {{ inv.email }}"',
            ),
        ),
        (
            "model_risk_assurance.html",
            (
                'aria-label="Review {{ row.vendor }} assessment"',
                'aria-label="Project {{ row.vendor }} assessment to asset"',
            ),
        ),
        (
            "_workspace_worklist.html",
            (
                'aria-label="Assign {{ s.subject }} assessment"',
                'aria-label="Review {{ s.subject }} assessment"',
            ),
        ),
        (
            "workforce.html",
            (
                'aria-label="View {{ d.title }} team"',
                'aria-label="Review {{ d.title }} performance"',
                'aria-label="Deploy {{ d.title }} team"',
            ),
        ),
        (
            "agents.html",
            (
                'aria-label="Deploy \' + esc(d.title) + \' team"',
                'aria-label="View \' + esc(d.title) + \' details"',
            ),
        ),
    ),
)
def test_repeated_actions_include_their_target(
    template_name: str,
    labels: tuple[str, ...],
) -> None:
    source = (TEMPLATES / template_name).read_text(encoding="utf-8")

    for label in labels:
        assert label in source
