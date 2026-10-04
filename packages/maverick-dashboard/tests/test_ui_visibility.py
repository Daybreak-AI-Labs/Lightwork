"""Role-based UI visibility: the page registry, the admin override store, the
role-filtered sidebar, and the page-level enforcement dependency.

Safety invariants under test (mirror maverick_dashboard.rbac's):
  * auth OFF (no principal) -> full nav, nothing enforced (single-user mode);
  * overrides can never show a page past the role's permission floor;
  * admins can never lose /settings or /users (lockout guard);
  * page policy governs UI pages only -- /api routes are untouched.
"""
from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient


# ---------- registry + pure logic (no HTTP) ----------

def test_registry_is_well_formed():
    from maverick_dashboard import rbac, ui_visibility
    perms = {"view", "operate", "audit", "admin"}
    seen_paths = set()
    for page in ui_visibility.PAGES:
        assert page["path"].startswith("/") and page["path"] not in seen_paths
        seen_paths.add(page["path"])
        assert page["floor"] in perms and page["default"] in perms
        # the default can never be weaker than the floor: every role that sees
        # the page by default must also clear its security floor.
        for role in rbac.ROLES:
            if ui_visibility.default_visible(page, role):
                assert ui_visibility.eligible(page, role)
    # locked pages exist and are admin-floor (they gate the undo path).
    for path, role in ui_visibility.LOCKED:
        assert path in seen_paths and role == "admin"


def test_registry_icons_exist_in_the_shared_icon_sprite():
    from pathlib import Path

    from maverick_dashboard import ui_visibility
    templates = Path(ui_visibility.__file__).parent / "templates"
    sprite = (templates / "_icon_sprite.html").read_text(encoding="utf-8")
    for page in ui_visibility.PAGES:
        assert f'id="i-{page["icon"]}"' in sprite, page["path"]
    # Both shells render the sidebar's icons from the same sprite.
    for shell in ("base.html", "chat_base.html"):
        assert '{% include "_icon_sprite.html" %}' in (templates / shell).read_text(encoding="utf-8")


def test_agent_control_plane_read_surface_is_discoverable_to_auditors():
    from maverick_dashboard import ui_visibility

    page = next(
        item for item in ui_visibility.PAGES
        if item["path"] == "/agent-control-plane"
    )
    assert page["floor"] == "view"
    assert page["default"] == "view"
    assert ui_visibility.is_visible(page, "auditor") is True


def test_page_for_prefix_matching():
    from maverick_dashboard import ui_visibility
    assert ui_visibility.page_for("/goals")["path"] == "/goals"
    assert ui_visibility.page_for("/goals/3/plan")["path"] == "/goals"
    assert ui_visibility.page_for("/goal-builder")["path"] == "/goal-builder"
    assert ui_visibility.page_for("/flows/designer/7")["path"] == "/flows/designer"
    assert ui_visibility.page_for("/tenants/overview")["path"] == "/tenants"
    # unregistered surfaces stay ungoverned: APIs, share links, signed
    # approvals, probes, and flow-run detail pages.
    for path in ("/api/v1/goals", "/share/tok", "/flow/approve",
                 "/healthz", "/flows/5/runs/2", "/"):
        assert ui_visibility.page_for(path) is None


def test_store_roundtrip_diffs_and_validation(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from maverick_dashboard import ui_visibility
    assert ui_visibility.overrides() == {}
    analytics = next(p for p in ui_visibility.PAGES if p["path"] == "/flows/analytics")
    # storing the default is a no-op (diff-only store)...
    ui_visibility.set_visibility("/flows/analytics", "operator", True)
    assert ui_visibility.overrides() == {}
    # ...and a real change persists and resolves.
    ui_visibility.set_visibility("/flows/analytics", "operator", False)
    assert ui_visibility.overrides() == {"/flows/analytics": {"operator": False}}
    assert ui_visibility.is_visible(analytics, "operator") is False
    assert ui_visibility.is_visible(analytics, "admin") is True
    # flipping back to the default removes the stored diff entirely.
    ui_visibility.set_visibility("/flows/analytics", "operator", True)
    assert ui_visibility.overrides() == {}
    with pytest.raises(ValueError):
        ui_visibility.set_visibility("/nope", "operator", False)
    with pytest.raises(ValueError):
        ui_visibility.set_visibility("/flows/analytics", "superuser", False)


def test_corrupt_or_foreign_store_entries_are_dropped(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from maverick_dashboard import ui_visibility
    p = ui_visibility.store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "/flows/analytics": {"operator": False, "superuser": False, "viewer": "yes"},
        "/not-a-page": {"viewer": False},
        "/spend": "broken",
    }))
    assert ui_visibility.overrides() == {"/flows/analytics": {"operator": False}}
    p.write_text("{not json")
    assert ui_visibility.overrides() == {}


def test_overrides_cannot_outrun_the_permission_floor(monkeypatch, tmp_path):
    # Even a hand-edited store showing /settings to viewers must not stick:
    # visibility is presentation policy, the floor is authz.
    monkeypatch.setenv("HOME", str(tmp_path))
    from maverick_dashboard import ui_visibility
    with pytest.raises(ValueError):
        ui_visibility.set_visibility("/settings", "viewer", True)
    p = ui_visibility.store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"/settings": {"viewer": True}}))
    settings = next(pg for pg in ui_visibility.PAGES if pg["path"] == "/settings")
    assert ui_visibility.is_visible(settings, "viewer") is False


def test_lockout_guard_ignores_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from maverick_dashboard import ui_visibility
    with pytest.raises(ValueError):
        ui_visibility.set_visibility("/settings", "admin", False)
    p = ui_visibility.store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"/settings": {"admin": False}, "/users": {"admin": False}}))
    for path in ("/settings", "/users"):
        page = next(pg for pg in ui_visibility.PAGES if pg["path"] == path)
        assert ui_visibility.is_visible(page, "admin") is True


def test_nav_groups_per_role(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from maverick_dashboard import ui_visibility

    def hrefs(role):
        return {ln["href"] for g in ui_visibility.nav_groups(role) for ln in g["links"]}

    in_nav = {
        p["path"] for p in ui_visibility.PAGES
        if p.get("in_nav", True) and ui_visibility.customer_ready(p)
    }
    # auth off: every sidebar page (in_nav: False pages are reached from
    # in-page links, never the sidebar; ready: False pages are offered nowhere).
    assert hrefs(None) == in_nav
    # Saved flows are the sidebar entry; creation starts from that page.
    assert "/workflows" in in_nav
    assert "/goal-builder" not in in_nav and "/flows/designer" not in in_nav \
        and "/workflow-builder" not in in_nav
    # viewer: read-only pages only -- no run/build/admin surfaces, and the
    # groups left empty (Extend, Admin) are dropped whole.
    v = hrefs("viewer")
    assert {"/goals", "/overview", "/spend", "/safety"} <= v
    assert "/cortex" not in v and "/knowledge-access" not in v
    assert v.isdisjoint({"/chat", "/ekko", "/automations", "/settings", "/users", "/mcp", "/audit", "/learning", "/benchmarks"})
    # The governance surfaces are split into focused sections (the flat
    # 30-link Govern block read as unorganized); the role still sees no
    # Extend/Admin group. My companion lives in Operate (shown as "Work"),
    # never in a second group that is also titled "Work".
    assert {g["label"] for g in ui_visibility.nav_groups("viewer")} == {
        "Operate", "Observe", "Govern", "Privacy", "Consent",
        "AI oversight", "Third parties"}
    # auditor: the audit read surfaces, nothing operational. (/replay is folded
    # out of the sidebar in the moderate declutter; reached from Audit in-page.)
    a = hrefs("auditor")
    assert {"/audit", "/compliance", "/overview", "/agent-control-plane"} <= a
    assert a.isdisjoint({"/chat", "/settings", "/automations", "/replay"})
    # operator: operate surfaces but no admin plumbing.
    o = hrefs("operator")
    assert {"/chat", "/goals", "/ekko", "/automations", "/approvals", "/skills"} <= o
    assert o.isdisjoint({"/settings", "/users", "/tenants", "/mcp", "/channels",
                         "/cache", "/audit"})
    # admin: every sidebar page.
    assert hrefs("admin") == in_nav


def test_not_ready_pages_are_offered_on_no_customer_surface(monkeypatch, tmp_path):
    """``ready: False`` = an operator workbench (record ids, fingerprints, raw
    status documents) or a module that ships switched off: governed and
    reachable by URL, but never in the sidebar, the finder or the matrix."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from maverick_dashboard import rbac, ui_visibility

    held = ui_visibility.not_ready_paths()
    # Only the Research lab is held back; every governance workbench is offered.
    assert held == {"/research"}
    for role in [None, *rbac.ROLES]:
        nav = {ln["href"] for g in ui_visibility.nav_groups(role) for ln in g["links"]}
        finder = {ln["href"] for g in ui_visibility.finder_pages(role) for ln in g["links"]}
        assert nav.isdisjoint(held), role
        assert finder.isdisjoint(held), role
    assert held.isdisjoint(row["path"] for g in ui_visibility.matrix() for row in g["pages"])
    # Still registered and enforced: the route keeps its permission floor.
    for path in held:
        page = ui_visibility.page_for(path)
        assert page is not None and page["floor"] in rbac.permissions_for("admin")
    # Saving the matrix never records a verdict for a page it does not show.
    ui_visibility.apply_selection(set())
    assert held.isdisjoint(ui_visibility.overrides())


# ---------- HTTP enforcement + settings matrix ----------

def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick_dashboard import app as dash_app
    dash_app._world_cache.clear()
    return TestClient(dash_app.app, headers={"Origin": "http://testserver"})


def _as(monkeypatch, principal):
    from maverick_dashboard import auth
    monkeypatch.setattr(auth, "caller_principal", lambda request: principal)


def test_auth_off_full_nav_and_no_enforcement(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    body = c.get("/goals").text
    assert 'href="/users"' in body and 'href="/settings"' in body
    assert c.get("/facts").status_code == 200
    # even an explicit override is inert with auth off (single-user mode).
    from maverick_dashboard import ui_visibility
    ui_visibility.set_visibility("/flows/analytics", "operator", False)
    assert c.get("/flows/analytics").status_code == 200


def test_viewer_pages_hidden_and_blocked(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac
    rbac.set_role("user:vy", "viewer")
    _as(monkeypatch, "user:vy")
    r = c.get("/goals")
    assert r.status_code == 200
    assert 'href="/settings"' not in r.text
    assert 'href="/automations"' not in r.text
    assert 'data-grp="Admin"' not in r.text
    assert c.get("/automations").status_code == 403
    assert c.get("/chat").status_code == 403


def test_operator_blocked_from_admin_group_pages(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac
    rbac.set_role("user:op", "operator")
    _as(monkeypatch, "user:op")
    assert c.get("/automations").status_code == 200
    assert c.get("/cache").status_code == 403
    assert c.get("/tenants").status_code == 403


def test_admin_override_hides_page_but_not_api(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac, ui_visibility
    rbac.set_role("user:aud", "auditor")
    ui_visibility.set_visibility("/audit", "auditor", False)
    _as(monkeypatch, "user:aud")
    # the page is gone (nav + direct), but the API surface it fronts is still
    # governed by RBAC permissions alone -- page policy never gates /api.
    assert c.get("/audit").status_code == 403
    assert c.get("/api/v1/audit/tail").status_code == 200


def test_settings_matrix_save_reset_and_authz(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "user:boss")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac, ui_visibility
    _as(monkeypatch, "user:boss")
    # build the full checked set from the resolved matrix, then untick one cell.
    checked = [
        f"{cell['role']}:{pg['path']}"
        for grp in ui_visibility.matrix() for pg in grp["pages"]
        for cell in pg["cells"]
        if cell["visible"] and cell["eligible"] and not cell["locked"]
    ]
    checked.remove("operator:/flows/analytics")
    r = c.post("/settings/ui-visibility", data={"cell": checked})
    assert r.status_code == 200  # follows the 303 back to /settings
    assert ui_visibility.overrides() == {"/flows/analytics": {"operator": False}}
    rbac.set_role("user:op", "operator")
    _as(monkeypatch, "user:op")
    assert c.get("/flows/analytics").status_code == 403
    assert 'href="/flows/analytics"' not in c.get("/goals").text
    # a non-admin cannot edit or reset the matrix.
    assert c.post("/settings/ui-visibility", data={"cell": []}).status_code == 403
    assert c.post("/settings/ui-visibility/reset").status_code == 403
    # reset restores the defaults.
    _as(monkeypatch, "user:boss")
    assert c.get("/flows/analytics").status_code == 200
    assert c.post("/settings/ui-visibility/reset").status_code == 200
    assert ui_visibility.overrides() == {}
    _as(monkeypatch, "user:op")
    assert c.get("/flows/analytics").status_code == 200


def test_settings_matrix_requires_global_admin_for_tenant_local_admin(
    monkeypatch, tmp_path
):
    # The visibility matrix is global control-plane state, so a tenant-local
    # admin must not be allowed to change it for every other tenant/user.
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    c = _client(monkeypatch, tmp_path)
    from maverick.paths import reset_tenant, set_tenant
    from maverick_dashboard import rbac, ui_visibility

    rbac.set_role("user:tenant-admin", "viewer")
    rbac.set_tenant_role("acme", "user:tenant-admin", "admin")
    _as(monkeypatch, "user:tenant-admin")

    tok = set_tenant("acme")
    try:
        assert c.post("/settings/ui-visibility", data={"cell": []}).status_code == 403
        assert c.post("/settings/ui-visibility/reset").status_code == 403
        assert ui_visibility.overrides() == {}
    finally:
        reset_tenant(tok)


def test_matrix_save_cannot_lock_admins_out(monkeypatch, tmp_path):
    # A save with NOTHING checked hides everything hideable -- and the admin
    # must still reach /settings and /users to undo it.
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "user:boss")
    c = _client(monkeypatch, tmp_path)
    _as(monkeypatch, "user:boss")
    assert c.post("/settings/ui-visibility", data={}).status_code == 200
    assert c.get("/settings").status_code == 200
    assert c.get("/users").status_code == 200
    body = c.get("/settings").text
    assert 'href="/users"' in body
    # a smuggled grant past the floor is ignored on save, not persisted.
    c.post("/settings/ui-visibility", data={"cell": ["viewer:/settings"]})
    from maverick_dashboard import rbac, ui_visibility
    assert ui_visibility.overrides().get("/settings") is None
    rbac.set_role("user:vy", "viewer")
    _as(monkeypatch, "user:vy")
    assert c.get("/settings").status_code == 403


def test_settings_page_renders_matrix(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    body = c.get("/settings").text
    assert "Pages each role can see" in body
    assert 'action="/settings/ui-visibility"' in body
    assert 'value="operator:/flows/analytics"' in body
    # locked admin cells render disabled, not as editable inputs.
    assert "Always available to administrators" in body


def test_nav_and_matrix_groups_are_unique():
    """Regression: a page whose group differs from its list neighbours (e.g. an
    entry re-homed to Admin mid-list) must merge into its group, not split the
    sidebar into duplicate EXTEND/ADMIN sections (a shipped visual bug)."""
    from maverick_dashboard import rbac, ui_visibility
    for role in [None, *rbac.ROLES]:
        labels = [g["label"] for g in ui_visibility.nav_groups(role)]
        assert len(labels) == len(set(labels)), f"duplicate nav groups for {role}: {labels}"
    mlabels = [g["label"] for g in ui_visibility.matrix()]
    assert len(mlabels) == len(set(mlabels)), f"duplicate matrix groups: {mlabels}"


def test_displayed_group_titles_are_unique_for_every_role():
    """Two raw groups must never render under one title (the companion page once
    sat in a raw "Work" group while "Operate" is also shown as "Work")."""
    from maverick_dashboard import rbac, ui_visibility
    for role in [None, *rbac.ROLES]:
        for groups in (ui_visibility.nav_groups(role), ui_visibility.finder_pages(role)):
            titles = [g["title"] for g in groups]
            assert len(titles) == len(set(titles)), f"duplicate group titles for {role}: {titles}"
    titles = [g["title"] for g in ui_visibility.matrix()]
    assert len(titles) == len(set(titles)), f"duplicate matrix group titles: {titles}"
    work = next(g for g in ui_visibility.nav_groups(None) if g["title"] == "Work")
    assert {"/companion", "/goals", "/chat"} <= {link["href"] for link in work["links"]}


def test_rendered_sidebar_group_titles_are_unique_with_companions_on(monkeypatch, tmp_path):
    from html.parser import HTMLParser

    from maverick_dashboard import employee_companion_api

    monkeypatch.setattr(employee_companion_api, "navigation_available", lambda request, admin=False: True)
    c = _client(monkeypatch, tmp_path)

    class Groups(HTMLParser):
        def __init__(self):
            super().__init__()
            self.titles, self.capture, self.group_hrefs = [], False, {}

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "span" and values.get("class") == "navgrp__label":
                self.capture = True
                self.titles.append("")
            if tag == "a" and self.titles and values.get("href"):
                self.group_hrefs.setdefault(self.titles[-1], set()).add(values["href"])

        def handle_endtag(self, tag):
            if tag == "span":
                self.capture = False

        def handle_data(self, data):
            if self.capture:
                self.titles[-1] += data.strip()

    for path in ("/goals", "/chat"):
        parser = Groups()
        parser.feed(c.get(path).text)
        assert len(parser.titles) == len(set(parser.titles)), (path, parser.titles)
        assert parser.titles.count("Work") == 1, (path, parser.titles)
        assert "/companion" in parser.group_hrefs["Work"], path


def test_approved_self_connect_keeps_page_overrides_and_native_admin_floor(monkeypatch, tmp_path):
    from maverick import connection_oauth
    from maverick_dashboard import ui_visibility

    monkeypatch.setattr(ui_visibility, "store_path", lambda: tmp_path / "visibility.json")
    page = ui_visibility.page_for("/connections")
    monkeypatch.setattr(connection_oauth, "self_connect_profiles", lambda: frozenset())
    assert not ui_visibility.is_visible(page, "operator")
    monkeypatch.setattr(connection_oauth, "self_connect_profiles", lambda: frozenset({"google-drive"}))
    assert ui_visibility.is_visible(page, "operator")
    assert not ui_visibility.is_visible(page, "viewer")
    assert any(link["href"] == "/connections" for group in ui_visibility.finder_pages("operator") for link in group["links"])
    ui_visibility.set_visibility("/connections", "operator", False)
    assert not ui_visibility.is_visible(page, "operator")
    assert ui_visibility.is_visible(page, "admin")
    assert not ui_visibility.is_visible(ui_visibility.page_for("/settings"), "operator")
