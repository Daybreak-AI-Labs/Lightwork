"""Production templates expose only mutations admitted by the active contract."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_MANIFEST = REPO_ROOT / "release" / "production-surface-manifest.json"


class _ElementIndex(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, tuple[str, dict[str, str | None]]] = {}

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = dict(attrs)
        element_id = values.get("id")
        if element_id:
            self.by_id[element_id] = (tag, values)

    handle_startendtag = handle_starttag


def _elements(rendered: str) -> dict[str, tuple[str, dict[str, str | None]]]:
    parser = _ElementIndex()
    parser.feed(rendered)
    return parser.by_id


def _production(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")
    monkeypatch.setenv("MAVERICK_PRODUCTION_MANIFEST", str(PRODUCTION_MANIFEST))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))


def _development(monkeypatch) -> None:
    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)


def _goal_context() -> dict:
    return {
        "goal": SimpleNamespace(
            id=17,
            title="Reviewed goal",
            description="A governed result",
            result="complete",
            status="done",
            project_id=None,
            updated_at=42.0,
        ),
        "deliverable": SimpleNamespace(gate="human", deliverable="Report"),
        "rendered": SimpleNamespace(structured=False, table=None),
        "signoff": None,
        "artifacts": [],
        "projects": [],
        "share_links": [],
        "signoff_idempotency_key": "production-signoff-key-0001",
        "attachments_allowed": False,
        "nav_groups": [],
    }


def test_goal_detail_exposes_only_answer_and_governed_signoff_in_production(
    monkeypatch,
    tmp_path,
) -> None:
    _production(monkeypatch, tmp_path)
    from maverick_dashboard.app import templates

    rendered = templates.env.get_template("chat_goal.html").render(**_goal_context())
    elements = _elements(rendered)

    assert "cancel-btn" not in elements
    assert "halt-resume" not in elements
    assert "signoff-approve" in elements
    assert "signoff-reject" in elements
    assert "feedback-up" not in elements
    assert "feedback-down" not in elements
    assert "edit-toggle" not in elements
    assert "edit-save" not in elements
    assert "answer-form" in elements
    assert "Needs <strong>human</strong> sign-off" in rendered
    assert "/api/v1/goals/' + goalId + '/answer" in rendered
    assert "/api/v1/goals/' + goalId + '/signoff" in rendered
    assert "headers['Idempotency-Key'] = signoffIdempotencyKey" in rendered
    assert 'var signoffIdempotencyKey = "production-signoff-key-0001"' in rendered
    for mutation in ("/cancel", "/resume", "/feedback", "/deliverable/edit"):
        assert mutation not in rendered


def test_goal_detail_keeps_full_development_controls(monkeypatch) -> None:
    _development(monkeypatch)
    from maverick_dashboard.app import templates

    elements = _elements(
        templates.env.get_template("chat_goal.html").render(**_goal_context())
    )

    assert {
        "cancel-btn",
        "halt-resume",
        "signoff-approve",
        "signoff-reject",
        "feedback-up",
        "feedback-down",
        "edit-toggle",
        "edit-save",
        "answer-form",
    }.issubset(elements)


def test_flow_designer_hides_every_contracted_write_control(
    monkeypatch,
    tmp_path,
) -> None:
    _production(monkeypatch, tmp_path)
    from maverick_dashboard.app import templates

    rendered = templates.env.get_template("flow_designer.html").render(
        production_core=True,
        flow_id="flow-1",
        seed_flow=None,
        nav_groups=[],
    )
    elements = _elements(rendered)

    for element_id in (
        "fd-save",
        "fd-publish",
        "fd-run",
        "fd-run-real",
        "fd-chat-toggle",
        "fd-draft",
    ):
        attrs = elements[element_id][1]
        assert "disabled" in attrs, element_id
        if element_id != "fd-draft":
            assert "hidden" in attrs, element_id
    assert "hidden" not in elements["fd-history"][1]
    assert "if (!productionCore)" in rendered
    assert "Roll back to v" in rendered


def test_flow_designer_keeps_development_write_controls(monkeypatch) -> None:
    _development(monkeypatch)
    from maverick_dashboard.app import templates

    rendered = templates.env.get_template("flow_designer.html").render(
        production_core=False,
        flow_id="flow-1",
        seed_flow=None,
        nav_groups=[],
    )
    elements = _elements(rendered)

    for element_id in (
        "fd-save",
        "fd-publish",
        "fd-run",
        "fd-run-real",
        "fd-chat-toggle",
        "fd-draft",
    ):
        attrs = elements[element_id][1]
        assert "hidden" not in attrs, element_id
        assert "disabled" not in attrs, element_id


def test_flow_run_is_status_only_in_production(monkeypatch, tmp_path) -> None:
    _production(monkeypatch, tmp_path)
    from maverick_dashboard.app import templates

    rendered = templates.env.get_template("flow_run.html").render(
        flow_id="flow-1",
        run_id="run-1",
        nav_groups=[],
    )

    assert "/api/v1/flows/runs/' + encodeURIComponent(runId)" in rendered
    assert "/events" in rendered
    assert "/resume" not in rendered
    assert "/retry" not in rendered
    assert "Resume now" not in rendered
    assert "Retry from failed step" not in rendered
    assert "Re-run from scratch" not in rendered
    assert "/automations" not in rendered


def test_flow_run_keeps_development_recovery_controls(monkeypatch) -> None:
    _development(monkeypatch)
    from maverick_dashboard.app import templates

    rendered = templates.env.get_template("flow_run.html").render(
        flow_id="flow-1",
        run_id="run-1",
        nav_groups=[],
    )

    assert "/resume" in rendered
    assert "/retry" in rendered
    assert "Resume now" in rendered
    assert "Retry from failed step" in rendered
    assert "Re-run from scratch" in rendered


def test_halt_pill_is_status_only_in_production(monkeypatch, tmp_path) -> None:
    _production(monkeypatch, tmp_path)
    from maverick_dashboard.app import templates

    rendered = templates.env.get_template("base.html").render(nav_groups=[])
    tag, attrs = _elements(rendered)["halt-pill"]

    assert tag == "span"
    assert attrs["role"] == "status"
    assert "href" not in attrs
    assert attrs["data-can-arm"] == "0"
    assert attrs["data-can-clear"] == "0"
    assert "pointer-events: none" in str(attrs["style"])


def test_halt_pill_keeps_development_control_and_script_guard(monkeypatch) -> None:
    _development(monkeypatch)
    from maverick_dashboard.app import templates

    rendered = templates.env.get_template("base.html").render(nav_groups=[])
    tag, attrs = _elements(rendered)["halt-pill"]
    source = (
        REPO_ROOT
        / "packages"
        / "maverick-dashboard"
        / "maverick_dashboard"
        / "static"
        / "lightwork-ui.js"
    ).read_text(encoding="utf-8")

    assert tag == "a"
    assert attrs["role"] == "button"
    assert attrs["data-can-arm"] == "1"
    assert attrs["data-can-clear"] == "1"
    guard = source.index("if (!canMutate)")
    listener = source.index("pill.addEventListener('click'", guard)
    assert guard < listener
    assert "if (!canArm) return;" in source
    assert "if (!canClear) return;" in source


def test_goal_map_hides_retitle_and_reparent_controls_in_production(
    monkeypatch,
    tmp_path,
) -> None:
    _production(monkeypatch, tmp_path)
    from maverick_dashboard.app import templates

    rendered = templates.env.get_template("graph_editor.html").render(
        node_count=0,
        fallback_html="",
        nav_groups=[],
    )
    elements = _elements(rendered)

    for element_id in ("ge-title", "ge-retitle", "ge-parent", "ge-reparent"):
        attrs = elements[element_id][1]
        assert "hidden" in attrs
        assert "disabled" in attrs
    assert "/retitle" not in rendered
    assert "/reparent" not in rendered


def test_goals_and_approval_pages_keep_only_admitted_actions(
    monkeypatch,
    tmp_path,
) -> None:
    _production(monkeypatch, tmp_path)
    from maverick_dashboard.app import templates

    goals = templates.env.get_template("goals.html").render(
        goals=[],
        nav_groups=[],
    )
    approval = SimpleNamespace(
        id=3,
        action="governed action",
        risk="high",
        scope="tenant",
        detail="review me",
    )
    approvals = templates.env.get_template("approvals.html").render(
        approvals=[approval],
        sources={},
        nav_groups=[],
    )

    assert "/graph-editor" not in goals
    assert 'data-action="approve"' in approvals
    assert 'data-action="deny"' in approvals
    assert "/claim" not in approvals
    assert "/release" not in approvals


def test_signed_flow_approval_page_suppresses_unadmitted_submit(
    monkeypatch,
    tmp_path,
) -> None:
    _production(monkeypatch, tmp_path)
    from maverick_dashboard import app as dashboard

    rendered = dashboard._flow_approve_page(
        "Confirm",
        "Review this flow",
        form_token="signed-secret",
    ).body.decode()

    assert "<form" not in rendered
    assert "signed-secret" not in rendered

    _development(monkeypatch)
    rendered = dashboard._flow_approve_page(
        "Confirm",
        "Review this flow",
        form_token="signed-secret",
    ).body.decode()
    assert '<form method="post" action="/flow/approve/signed-secret">' in rendered


def test_goal_create_views_keep_production_idempotency_wiring(
    monkeypatch,
    tmp_path,
) -> None:
    _production(monkeypatch, tmp_path)
    from maverick_dashboard.app import templates

    chat = templates.env.get_template("chat.html").render(
        goal_idempotency_key="chat-key",
        attachments_allowed=False,
        recent=[],
        prefill_title="",
        prefill_description="",
        nav_groups=[],
    )
    builder = templates.env.get_template("goal_builder.html").render(
        goal_idempotency_key="compose-key",
        nav_groups=[],
    )

    assert 'name="idempotency_key" value="chat-key"' in chat
    assert 'const idempotencyKey = "compose-key";' in builder
    assert 'headers["Idempotency-Key"] = idempotencyKey' in builder
