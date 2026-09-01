from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from maverick_dashboard import assessment_evidence_autopopulation_page as page

PACKAGE = Path(__file__).resolve().parents[1] / "maverick_dashboard"


class _Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(PACKAGE / "templates"))
    templates.env.globals.update(
        theme="dark",
        font="default",
        lang="en",
        density="comfortable",
        custom_theme_css="",
        custom_theme_names=[],
        dir="ltr",
        nav_groups=[],
        t=lambda key: key,
    )
    return templates


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(page, "_tenant_selected", lambda: None)
    monkeypatch.setattr(page, "require_permission", lambda *_args: None)
    monkeypatch.setattr(page, "has_permission", lambda *_args: True)
    monkeypatch.setattr(page, "has_global_permission", lambda *_args: True)
    app = FastAPI()
    app.include_router(
        page.create_assessment_evidence_autopopulation_page_router(_templates())
    )
    return TestClient(app, headers={"Origin": "http://testserver"})


def test_liquid_glass_workbench_is_store_free_closed_and_copilot_bound(client):
    response = client.get(page.PAGE_PATH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private, max-age=0"
    assert response.headers["x-frame-options"] == "DENY"
    csp = response.headers["content-security-policy"]
    for directive in (
        "default-src 'none'",
        "connect-src 'self'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "script-src 'self'",
        "style-src 'self'",
    ):
        assert directive in csp
    assert "Documents propose. Humans decide. Apply is separate." in response.text
    assert "fills only unanswered questions" in response.text
    assert f'data-api-base="{page.API_BASE}"' in response.text
    assert f'data-copilot-page="{page.PAGE_PATH}"' in response.text
    assert f'href="{page.STYLE_PATH}"' in response.text
    assert f'src="{page.SCRIPT_PATH}"' in response.text
    context = page.assessment_evidence_copilot_context()
    assert context.path == page.PAGE_PATH
    assert any("never changes" in item for item in context.boundaries)


def test_page_collects_no_tenant_actor_evidence_bytes_or_mapping_authority(client):
    parser = _Markup()
    parser.feed(client.get(page.PAGE_PATH).text)
    names = {
        value
        for tag, attrs in parser.tags
        if tag in {"input", "select", "textarea"}
        if isinstance((value := attrs.get("name")), str)
    }
    assert {
        "assessment_id",
        "analysis_ids",
        "proposal_id",
        "decision",
        "rationale",
        "expected_revision",
        "expected_assessment_revision",
        "idempotency_key",
        "confirm",
    }.issubset(names)
    for forbidden in (
        "tenant_id",
        "actor_ref",
        "artifact_bytes",
        "extracted_text",
        "mapping_authority",
        "supported_answer",
        "retained_until",
        "storage_path",
    ):
        assert forbidden not in names


def test_page_rejects_query_authority_and_requires_selected_tenant(client, monkeypatch):
    assert client.get(f"{page.PAGE_PATH}?tenant_id=attacker").status_code == 404
    assert client.get(f"{page.PAGE_PATH}?theme=light&density=compact").status_code == 200

    def missing():
        raise page.HTTPException(status_code=409, detail="select a tenant")

    monkeypatch.setattr(page, "_tenant_selected", missing)
    assert client.get(page.PAGE_PATH).status_code == 409


def test_role_gate_disables_every_mutating_form(client, monkeypatch):
    monkeypatch.setattr(page, "has_permission", lambda *_args: False)
    parser = _Markup()
    parser.feed(client.get(page.PAGE_PATH).text)
    disabled_fieldsets = [attrs for tag, attrs in parser.tags if tag == "fieldset" and "disabled" in attrs]
    assert len(disabled_fieldsets) >= 3
