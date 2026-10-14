"""Excluded production features are not visible in UI or OpenAPI discovery."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from maverick_dashboard.production_visibility import (
    enforce_production_feature_request_policy,
    feature_for_request_path,
    install_production_openapi_filter,
    production_contract_path_allowed,
    production_operation_allowed,
    production_page_allowed,
)
from pydantic import BaseModel
from starlette.requests import Request

REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_MANIFEST = REPO_ROOT / "release" / "production-surface-manifest.json"

_EXPECTED_PRODUCTION_MUTATIONS = frozenset(
    {
        ("POST", "/chat/send"),
        ("POST", "/api/v1/goals"),
        ("POST", "/api/v1/goals/compose"),
        ("POST", "/api/v1/goals/{goal_id}/children"),
        ("POST", "/api/v1/goals/{goal_id}/answer"),
        ("POST", "/api/v1/goals/{goal_id}/signoff"),
        ("POST", "/api/v1/approvals/{approval_id}/approve"),
        ("POST", "/api/v1/approvals/{approval_id}/deny"),
    }
)


@pytest.fixture(autouse=True)
def _production(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")
    monkeypatch.setenv("MAVERICK_PRODUCTION_MANIFEST", str(PRODUCTION_MANIFEST))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))


def _request(
    path: str,
    *,
    method: str = "GET",
    route_path: str | None = None,
) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "server": ("testserver", 80),
            "route": SimpleNamespace(path=route_path or path),
        }
    )


@pytest.mark.parametrize(
    ("path", "feature"),
    [
        ("/plugins", "plugins"),
        ("/api/v1/mcp", "mcp"),
        ("/channels/save", "channels"),
        ("/api/v1/connections/example/test", "connectors"),
        ("/api/v1/event-triggers", "connectors"),
        ("/api/v1/event-triggers/poll", "connectors"),
        ("/api/v1/import/sources", "connectors"),
        ("/api/v1/import/run", "connectors"),
        ("/a2a/v1", "a2a"),
        ("/api/v1/goals/7/attachments", "attachments"),
        ("/api/v1/goals/7/attachments/from-source", "attachments"),
        ("/api/v1/goals/7/attachments/9/download", "attachments"),
        ("/api/v1/learning", "self_learning"),
        ("/api/v1/learning/dgm", "code_dgm"),
        ("/api/v1/flows/abc/proposals", "flow_evolution"),
        ("/api/v1/flows/abc/apply", "flow_evolution"),
        ("/api/v1/flows/abc/nodes/node/impact", "flow_evolution"),
    ],
)
def test_excluded_request_paths_are_classified_and_404(path: str, feature: str) -> None:
    assert feature_for_request_path(path) == feature
    with pytest.raises(HTTPException) as exc:
        asyncio.run(enforce_production_feature_request_policy(_request(path)))
    assert exc.value.status_code == 404


def test_governed_flow_mutation_is_classified_but_not_admitted() -> None:
    assert feature_for_request_path("/api/v1/flows/abc/run") is None
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            enforce_production_feature_request_policy(
                _request(
                    "/api/v1/flows/abc/run",
                    method="POST",
                    route_path="/api/v1/flows/{flow_id}/run",
                )
            )
        )
    assert exc.value.status_code == 404


@pytest.mark.parametrize(
    ("path", "method", "route_path"),
    [
        ("/", "GET", "/"),
        ("/auth/login", "GET", "/auth/login"),
        ("/healthz", "GET", "/healthz"),
        ("/metrics", "GET", "/metrics"),
        ("/static/lightwork-ui.js", "GET", "/static/lightwork-ui.js"),
        ("/chat/send", "POST", "/chat/send"),
        (
            "/api/goal/17/events/stream",
            "GET",
            "/api/goal/{goal_id}/events/stream",
        ),
        ("/api/v1/goals", "POST", "/api/v1/goals"),
        (
            "/api/v1/goals/compose",
            "POST",
            "/api/v1/goals/compose",
        ),
        (
            "/api/v1/goals/17/children",
            "POST",
            "/api/v1/goals/{goal_id}/children",
        ),
        (
            "/api/v1/goals/17/answer",
            "POST",
            "/api/v1/goals/{goal_id}/answer",
        ),
        (
            "/api/v1/goals/17/events/stream",
            "GET",
            "/api/v1/goals/{goal_id}/events/stream",
        ),
        (
            "/api/v1/goals/17/signoff",
            "POST",
            "/api/v1/goals/{goal_id}/signoff",
        ),
        (
            "/api/v1/approvals/approval-1/approve",
            "POST",
            "/api/v1/approvals/{approval_id}/approve",
        ),
        (
            "/api/v1/approvals/approval-1/deny",
            "POST",
            "/api/v1/approvals/{approval_id}/deny",
        ),
        ("/api/v1/audit/tail", "GET", "/api/v1/audit/tail"),
        ("/api/v1/halt", "GET", "/api/v1/halt"),
        (
            "/api/v1/admin/tenants/acme/roles",
            "GET",
            "/api/v1/admin/tenants/{tenant_id}/roles",
        ),
        ("/users", "GET", "/users"),
    ],
)
def test_reviewed_first_profile_route_shapes_remain_admitted(
    path: str,
    method: str,
    route_path: str,
) -> None:
    assert production_page_allowed(path)
    assert production_contract_path_allowed(route_path)
    assert production_operation_allowed(route_path, method)
    asyncio.run(
        enforce_production_feature_request_policy(
            _request(path, method=method, route_path=route_path)
        )
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/security/soc/responses/execute",
        "/api/v1/privacy/dsar",
        "/api/v1/finance-operations/aml/screen",
        "/api/v1/assurance/model-risk/inventory",
        "/api/v1/voice/speak",
        "/api/v1/skills",
        "/api/v1/catalog/skills/install",
        "/scim/v2/Users",
        "/settings/providers",
        "/api/v1/roles/coder/override",
        "/api/v1/marketplace/packs",
        "/api/v1/connections",
        "/api/v1/plugins",
        "/api/v1/mcp",
        "/api/v1/a2a",
        "/api/v1/learning",
        "/api/v1/goals/17/attachments",
        "/api/v1/goals/17/new-unreviewed-control",
        "/api/v1/flows/analytics",
        "/api/v1/flows/flow-1/proposals",
        "/webhook/start",
        "/future-production-route",
    ],
)
def test_unreviewed_and_business_suite_routes_default_deny(path: str) -> None:
    assert not production_contract_path_allowed(path)
    assert not production_operation_allowed(path, "POST")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(enforce_production_feature_request_policy(_request(path)))
    assert exc.value.status_code == 404


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_signed_flow_capability_route_is_404_without_secret_reflection(
    method: str,
) -> None:
    secret = "signed-capability-that-must-not-reach-browser-output"
    assert not production_page_allowed(f"/flow/approve/{secret}")
    assert not production_contract_path_allowed("/flow/approve/{token}")
    assert not production_operation_allowed("/flow/approve/{token}", method)
    handler_calls: list[str] = []
    app = FastAPI(dependencies=[Depends(enforce_production_feature_request_policy)])

    @app.api_route("/flow/approve/{token}", methods=[method])
    def signed_flow_link(token: str) -> dict:
        handler_calls.append(token)
        return {"token": token}

    response = TestClient(app).request(method, f"/flow/approve/{secret}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert secret not in response.text
    assert handler_calls == []


@pytest.mark.parametrize(
    ("route_path", "method"),
    [
        ("/api/v1/flows/{flow_id}", "DELETE"),
        ("/api/v1/admin/tenants/{tenant_id}", "DELETE"),
    ],
)
def test_replay_unsafe_delete_operations_are_not_in_the_first_profile(
    route_path: str,
    method: str,
) -> None:
    assert production_contract_path_allowed(route_path)
    assert not production_operation_allowed(route_path, method)


@pytest.mark.parametrize(
    ("route_path", "method"),
    [
        ("/api/v1/admin/tenants", "POST"),
        ("/api/v1/admin/tenants/{tenant_id}/suspend", "POST"),
        ("/api/v1/admin/tenants/{tenant_id}/resume", "POST"),
        ("/api/v1/admin/tenants/{tenant_id}/plan", "POST"),
        ("/api/v1/admin/tenants/{tenant_id}/quota", "POST"),
        ("/api/v1/admin/tenants/{tenant_id}/roles/{principal}", "PUT"),
        ("/api/v1/admin/tenants/{tenant_id}/roles/{principal}", "DELETE"),
        ("/users/set", "POST"),
        ("/users/remove", "POST"),
    ],
)
def test_unreceipted_admin_mutations_are_not_in_the_first_profile(
    route_path: str,
    method: str,
) -> None:
    assert not production_operation_allowed(route_path, method)


def test_production_http_mutation_allowlist_is_exact_and_has_no_delete() -> None:
    from maverick_dashboard.production_visibility import (
        _REVIEWED_PRODUCTION_OPERATIONS,
    )

    actual = frozenset(
        (method, path)
        for path, methods in _REVIEWED_PRODUCTION_OPERATIONS.items()
        for method in methods
        if method not in {"GET", "HEAD", "OPTIONS", "WEBSOCKET"}
    )
    assert actual == _EXPECTED_PRODUCTION_MUTATIONS
    assert not any(method == "DELETE" for method, _path in actual)


def test_replay_unsafe_deletes_404_before_storage_io() -> None:
    writes: list[str] = []
    app = FastAPI(dependencies=[Depends(enforce_production_feature_request_policy)])

    @app.delete("/api/v1/flows/{flow_id}")
    def delete_flow(flow_id: str) -> dict:
        writes.append(f"flow:{flow_id}")
        return {}

    @app.delete("/api/v1/admin/tenants/{tenant_id}")
    def delete_tenant(tenant_id: str) -> dict:
        writes.append(f"tenant:{tenant_id}")
        return {}

    client = TestClient(app)
    assert client.delete("/api/v1/flows/recreated").status_code == 404
    assert client.delete("/api/v1/admin/tenants/acme").status_code == 404
    assert writes == []


def test_method_admission_is_not_inherited_from_an_allowed_path() -> None:
    assert production_operation_allowed("/api/v1/audit/tail", "GET")
    assert not production_operation_allowed("/api/v1/audit/tail", "POST")

    writes: list[str] = []
    app = FastAPI(dependencies=[Depends(enforce_production_feature_request_policy)])

    @app.get("/api/v1/audit/tail")
    def read_tail() -> dict:
        return {"events": []}

    @app.post("/api/v1/audit/tail")
    def mutate_tail() -> dict:
        writes.append("mutated")
        return {"status": "mutated"}

    client = TestClient(app)
    assert client.get("/api/v1/audit/tail").status_code == 200
    assert client.post("/api/v1/audit/tail").status_code == 404
    assert writes == []


def test_consequential_security_response_404s_before_handler_io() -> None:
    writes: list[str] = []
    app = FastAPI(dependencies=[Depends(enforce_production_feature_request_policy)])

    @app.post("/api/v1/security/soc/responses/execute")
    def execute_response() -> dict:
        writes.append("executed")
        return {"status": "executed"}

    response = TestClient(app).post("/api/v1/security/soc/responses/execute")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert writes == []


@pytest.mark.parametrize(
    "path",
    ["/api/v1/voice/captions", "/webhook/github"],
)
def test_process_local_tasks_are_not_admitted_to_production(path: str) -> None:
    assert feature_for_request_path(path) is None
    with pytest.raises(HTTPException) as exc:
        asyncio.run(enforce_production_feature_request_policy(_request(path)))
    assert exc.value.status_code == 404


def test_production_navigation_and_matrix_omit_excluded_pages() -> None:
    from maverick_dashboard import ui_visibility

    excluded = {
        "/assessments",
        "/channels",
        "/connections",
        "/finance",
        "/learned",
        "/learning",
        "/mcp",
        "/permissions",
        "/plugins",
        "/privacy",
        "/providers",
        "/roles",
        "/security",
        "/settings",
        "/skills",
        "/trust",
    }
    hrefs = {
        link["href"]
        for group in ui_visibility.nav_groups(None)
        for link in group["links"]
    }
    assert hrefs.isdisjoint(excluded)
    matrix_paths = {
        page["path"]
        for group in ui_visibility.matrix()
        for page in group["pages"]
    }
    assert matrix_paths.isdisjoint(excluded)
    for path in excluded:
        page = ui_visibility.page_for(path)
        assert page is not None
        assert not ui_visibility.is_visible(page, None)

    retained = {"/chat", "/goals", "/flows/designer", "/approvals", "/audit"}
    assert retained.issubset(hrefs)


@pytest.mark.parametrize(
    ("method", "route_path"),
    [
        # Retained shell, navigation, and goal authoring.
        ("GET", "/"),
        ("GET", "/static/daybreak-logo.jpg"),
        ("GET", "/static/lightwork.css"),
        ("GET", "/static/lightwork-ui.js"),
        ("GET", "/chat"),
        ("POST", "/chat/send"),
        ("GET", "/goal-builder"),
        ("POST", "/api/v1/goals/compose"),
        ("GET", "/goals"),
        ("GET", "/api/v1/goals/search"),
        ("GET", "/goals/{goal_id}/plan"),
        ("GET", "/goals/{goal_id}/trajectory"),
        ("GET", "/goals/{goal_id}/errors"),
        # Goal detail controls and polling.
        ("GET", "/chat/goal/{goal_id}"),
        ("GET", "/api/goal/{goal_id}/events"),
        ("GET", "/api/goal/{goal_id}/events/stream"),
        ("GET", "/api/v1/goals/{goal_id}/open_questions"),
        ("POST", "/api/v1/goals/{goal_id}/answer"),
        ("GET", "/api/v1/goals/{goal_id}/signoff"),
        ("POST", "/api/v1/goals/{goal_id}/signoff"),
        ("GET", "/api/v1/goals/{goal_id}/deliverable.csv"),
        ("GET", "/api/v1/goals/{goal_id}/artifacts/history"),
        # Core flow designer and run viewer are status-only in production.
        ("GET", "/flows/designer"),
        ("GET", "/flows/designer/{flow_id}"),
        ("GET", "/flows/{flow_id}/runs/{run_id}"),
        ("GET", "/api/v1/flows/tools"),
        ("GET", "/api/v1/flows/{flow_id}"),
        ("GET", "/api/v1/flows/{flow_id}/schema"),
        ("GET", "/api/v1/flows/{flow_id}/versions"),
        ("GET", "/api/v1/flows/runs/{run_id}"),
        ("GET", "/api/v1/flows/runs/{run_id}/events"),
        # Approval, audit, tenant, and RBAC pages.
        ("GET", "/approvals"),
        ("POST", "/api/v1/approvals/{approval_id}/approve"),
        ("POST", "/api/v1/approvals/{approval_id}/deny"),
        ("GET", "/audit"),
        ("GET", "/audit/binder"),
        ("GET", "/api/v1/audit/binder"),
        ("GET", "/api/v1/audit/grep"),
        ("GET", "/tenants"),
        ("GET", "/tenants/overview"),
        ("GET", "/api/v1/admin/tenants"),
        ("GET", "/api/v1/admin/tenants/{tenant_id}/roles"),
        ("GET", "/users"),
        # Shared emergency-stop status loaded by every retained page.
        ("GET", "/api/v1/halt"),
    ],
)
def test_every_retained_ui_core_dependency_is_admitted(
    method: str,
    route_path: str,
) -> None:
    assert production_operation_allowed(route_path, method)


def test_production_templates_hide_or_disable_unadmitted_dependencies() -> None:
    from maverick_dashboard import ui_visibility
    from maverick_dashboard.app import templates

    env = templates.env
    production_nav = ui_visibility.nav_groups(None)
    chat = env.get_template("chat.html").render(
        attachments_allowed=False,
        recent=[],
        prefill_title="",
        prefill_description="",
        nav_groups=production_nav,
    )
    assert "/workflow-builder" not in chat
    assert "/api/v1/voice/transcribe" not in chat
    assert "attach-input" not in chat
    assert "/static/lightwork-voice.js" not in chat
    assert "/settings" not in chat

    goal = SimpleNamespace(
        id=17,
        title="Reviewed goal",
        description="",
        result="complete",
        status="done",
        project_id=None,
        updated_at=0,
    )
    detail = env.get_template("chat_goal.html").render(
        goal=goal,
        deliverable=None,
        rendered=None,
        signoff=None,
        artifacts=[],
        projects=[SimpleNamespace(id="project", name="Hidden")],
        share_links=[{"id": "hidden"}],
        attachments_allowed=False,
        nav_groups=production_nav,
    )
    for excluded in (
        "/api/v1/goals/17/share",
        "/chat/goal/17/project",
        "/api/v1/voice/transcribe",
        "/api/v1/voice/speak",
        "/oversight",
        "/attachments",
        "/static/lightwork-voice.js",
        "/settings",
    ):
        assert excluded not in detail

    users = env.get_template("users.html").render(
        saved="",
        auth_on=True,
        bootstrap_admins=[],
        users=[],
        default_role="viewer",
        roles=("admin", "operator", "viewer"),
        you="user:admin",
        invites_on=True,
        pending_invites=[object()],
        suite_grants=[("user:admin", ("finance",))],
        all_suites=[("finance", "Finance")],
        default_suites=None,
        nav_groups=production_nav,
    )
    assert 'action="/users/set"' not in users
    assert 'action="/users/remove"' not in users
    assert "/users/invite" not in users
    assert "/users/suites/" not in users

    tenants = env.get_template("tenants.html").render(
        tenants=[],
        is_admin=True,
        nav_groups=production_nav,
    )
    assert 'id="tenant-add"' not in tenants
    assert "method: 'POST'" not in tenants

    approvals = env.get_template("approvals.html").render(
        approvals=[],
        sources={},
        nav_groups=production_nav,
    )
    assert "/api/v1/assess/" not in approvals

    flow = env.get_template("flow_designer.html").render(
        production_core=True,
        flow_id="",
        seed_flow=None,
        nav_groups=production_nav,
    )
    assert 'data-production-core="1"' in flow
    assert 'title="Everything that starts this flow"' not in flow
    assert "if (productionCore) return;" in flow
    assert "if (!productionCore) loadGallery();" in flow
    assert "if (triggersButton)" in flow


def test_production_goal_page_skips_project_and_share_storage(monkeypatch) -> None:
    from maverick_dashboard import app as dashboard

    goal = SimpleNamespace(id=17, status="active")

    class World:
        def get_goal(self, goal_id):
            assert goal_id == 17
            return goal

        def list_projects(self, **_kwargs):
            raise AssertionError("project storage was opened")

        def share_links_for_goal(self, _goal_id):
            raise AssertionError("share-link storage was opened")

    monkeypatch.setattr(dashboard, "_world", World)
    monkeypatch.setattr(dashboard, "assert_goal_access", lambda *_args: None)
    monkeypatch.setattr(dashboard, "_goal_deliverable", lambda _goal: (None, None))
    monkeypatch.setattr(dashboard, "_goal_artifacts", lambda *_args: [])
    monkeypatch.setattr(
        dashboard.templates,
        "TemplateResponse",
        lambda _request, _name, context: context,
    )

    context = asyncio.run(dashboard.chat_goal(_request("/chat/goal/17"), 17))

    assert context["projects"] == []
    assert context["share_links"] == []


def test_production_flow_catalog_exposes_exact_reviewed_tools(monkeypatch) -> None:
    from maverick_dashboard import api

    reviewed = [
        {
            "name": "read_file",
            "description": "Read an approved file",
            "params": ["path"],
            "category": "Files",
        },
        {
            "name": "redact",
            "description": "Redact sensitive text",
            "params": ["text"],
            "category": "Data",
        },
    ]
    monkeypatch.setattr(api, "_live_tool_index", lambda **_kwargs: reviewed)

    assert api._flow_tool_catalog() == reviewed


def test_attachment_api_functions_404_before_world_source_or_upload_io(
    monkeypatch,
) -> None:
    from maverick_dashboard import api

    class UnreadableUpload:
        async def read(self, _size: int = -1) -> bytes:
            raise AssertionError("upload body was read")

    monkeypatch.setattr(
        api,
        "_world",
        lambda: (_ for _ in ()).throw(AssertionError("world was opened")),
    )
    calls = (
        api.upload_attachment(object(), 1, BackgroundTasks(), UnreadableUpload()),
        api.attach_from_source(object(), 1, object(), BackgroundTasks()),
        api.list_goal_attachments(object(), 1),
        api.download_goal_attachment(object(), 1, 2),
    )
    for call in calls:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(call)
        assert exc.value.status_code == 404


def test_production_openapi_removes_paths_and_orphaned_models() -> None:
    class GoalOut(BaseModel):
        title: str

    class PluginOut(BaseModel):
        plugin_name: str

    class AttachmentOut(BaseModel):
        filename: str

    class SecurityResponseOut(BaseModel):
        action: str

    app = FastAPI()

    @app.get("/api/v1/goals", response_model=GoalOut)
    def goal() -> GoalOut:
        return GoalOut(title="kept")

    @app.get("/api/v1/plugins", response_model=PluginOut)
    def plugin() -> PluginOut:
        return PluginOut(plugin_name="hidden")

    @app.post(
        "/api/v1/goals/{goal_id}/attachments",
        response_model=AttachmentOut,
    )
    def attachment(goal_id: int) -> AttachmentOut:
        del goal_id
        return AttachmentOut(filename="hidden")

    @app.post(
        "/api/v1/goals/{goal_id}/attachments/from-source",
        response_model=AttachmentOut,
    )
    def source_attachment(goal_id: int) -> AttachmentOut:
        del goal_id
        return AttachmentOut(filename="hidden")

    @app.get(
        "/api/v1/goals/{goal_id}/attachments/{attachment_id}/download",
    )
    def download_attachment(goal_id: int, attachment_id: int) -> dict:
        del goal_id, attachment_id
        return {}

    @app.post("/api/v1/voice/captions")
    def captions() -> dict:
        return {}

    @app.post(
        "/api/v1/security/soc/responses/execute",
        response_model=SecurityResponseOut,
    )
    def execute_response() -> SecurityResponseOut:
        return SecurityResponseOut(action="hidden")

    @app.post("/api/v1/goals/{goal_id}/future-unreviewed")
    def future_goal_route(goal_id: int) -> dict:
        return {"goal_id": goal_id}

    @app.post("/webhook/github")
    def github() -> dict:
        return {}

    @app.get("/api/v1/audit/tail")
    def audit_tail() -> dict:
        return {"events": []}

    @app.post("/api/v1/audit/tail")
    def future_audit_mutation() -> dict:
        return {"status": "hidden"}

    install_production_openapi_filter(app)
    install_production_openapi_filter(app)
    schema = app.openapi()

    assert "/api/v1/goals" in schema["paths"]
    assert set(schema["paths"]["/api/v1/audit/tail"]) == {"get"}
    assert "/api/v1/plugins" not in schema["paths"]
    assert "/api/v1/goals/{goal_id}/attachments" not in schema["paths"]
    assert "/api/v1/goals/{goal_id}/attachments/from-source" not in schema["paths"]
    assert (
        "/api/v1/goals/{goal_id}/attachments/{attachment_id}/download"
        not in schema["paths"]
    )
    assert "/api/v1/voice/captions" not in schema["paths"]
    assert "/api/v1/security/soc/responses/execute" not in schema["paths"]
    assert "/api/v1/goals/{goal_id}/future-unreviewed" not in schema["paths"]
    assert "/webhook/github" not in schema["paths"]
    assert "GoalOut" in schema["components"]["schemas"]
    assert "PluginOut" not in schema["components"]["schemas"]
    assert "AttachmentOut" not in schema["components"]["schemas"]
    assert "SecurityResponseOut" not in schema["components"]["schemas"]


def test_application_openapi_is_narrowed_to_reviewed_first_profile() -> None:
    from maverick_dashboard.app import app

    paths = app.openapi()["paths"]

    assert {
        "/api/v1/goals",
        "/api/v1/goals/{goal_id}/signoff",
        "/api/v1/flows/{flow_id}",
        "/api/v1/approvals/{approval_id}/approve",
        "/api/v1/audit/tail",
        "/api/v1/admin/tenants/{tenant_id}/roles",
        "/api/v1/halt",
    }.issubset(paths)
    assert set(paths["/api/v1/audit/tail"]) == {"get"}
    assert set(paths["/api/v1/goals"]) == {"get", "post"}
    assert set(paths["/api/v1/goals/{goal_id}/signoff"]) == {"get", "post"}
    assert set(paths["/api/v1/admin/tenants"]) == {"get"}
    assert {
        "/api/v1/security/soc/responses/execute",
        "/api/v1/privacy/dsar",
        "/api/v1/finance-operations/aml/screen",
        "/api/v1/voice/speak",
        "/api/v1/skills",
        "/api/v1/connections",
        "/api/v1/learning",
        "/api/v1/goals/{goal_id}/attachments",
        "/api/v1/flows/{flow_id}/run",
        "/api/v1/admin/tenants/{tenant_id}/roles/{principal}",
        "/flow/approve/{token}",
        "/users/set",
        "/users/remove",
    }.isdisjoint(paths)


def test_excluded_dashboard_mutations_fail_before_writes() -> None:
    from maverick.production_exclusions import ProductionFeatureExcludedError
    from maverick_dashboard import settings_store

    with pytest.raises(ProductionFeatureExcludedError, match="self-learning-ledger"):
        settings_store.set_learning(True)
    with pytest.raises(ProductionFeatureExcludedError, match="code-dgm"):
        settings_store.set_dgm(True, acknowledged=True)
    with pytest.raises(ProductionFeatureExcludedError, match="code-dgm"):
        settings_store.set_flow_autonomy(auto_evolve=True)
    with pytest.raises(ProductionFeatureExcludedError, match="flow_auto_apply"):
        settings_store.set_flow_autonomy(auto_apply=True)
    with pytest.raises(ProductionFeatureExcludedError, match="messaging-channels"):
        settings_store.set_channel("slack", True)


def test_openapi_filter_is_inert_outside_explicit_production(monkeypatch) -> None:
    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)
    app = FastAPI()

    @app.get("/api/v1/plugins")
    def plugin() -> dict:
        return {}

    install_production_openapi_filter(app)
    assert "/api/v1/plugins" in app.openapi()["paths"]
