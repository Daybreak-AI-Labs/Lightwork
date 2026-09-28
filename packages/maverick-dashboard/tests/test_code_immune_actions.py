"""Buttons on the Code and Safety monitoring pages: plain pages, admin-only
actions, and the kernel's approval gate still in charge of merging."""
from __future__ import annotations

import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from maverick import config, world_model
    from maverick.audit import writer as audit_writer
    from maverick_dashboard import app as dash_app

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MAVERICK_HOME", str(home))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    for name in ("MAVERICK_CONFIG", "MAVERICK_DASHBOARD_TOKEN", "MAVERICK_DASHBOARD_REQUIRE_AUTH",
                 "MAVERICK_OIDC_ENABLED", "MAVERICK_PROXY_AUTH", "MAVERICK_TENANT"):
        monkeypatch.delenv(name, raising=False)
    import maverick.runtime_overrides as ro
    monkeypatch.setattr(ro, "OVERRIDES_PATH", tmp_path / "runtime-overrides.toml")
    config.reset_config_cache()
    audit_writer._default = None
    audit_writer._defaults.clear()
    dash_app._world_cache.clear()
    try:
        yield TestClient(dash_app.app, headers=ORIGIN)
    finally:
        config.reset_config_cache()
        audit_writer._default = None
        audit_writer._defaults.clear()
        dash_app._world_cache.clear()


def _as(monkeypatch, role: str) -> None:
    from maverick_dashboard import auth, rbac
    rbac.set_role(f"user:{role}1", role)
    monkeypatch.setattr(auth, "caller_principal", lambda request: f"user:{role}1")


def _code_on(monkeypatch):
    from maverick import config
    monkeypatch.setattr(config, "get_codebase", lambda: {"enable": True, "default_protect": "block"})


def _source_repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=src, check=True)
    (src / "a.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=src, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "i"],
                   cwd=src, check=True)
    return src


# -- pages -----------------------------------------------------------------------

def test_code_page_is_plain_and_offers_turn_on_when_off(client):
    from test_support.plain_language import developer_terms, visible_text
    page = client.get("/code").text
    assert developer_terms(page) == []
    text = visible_text(page)
    assert "maverick code" not in page and "[codebase]" not in page
    assert 'data-module-gate="codebase"' in page and "Code projects is turned off" in text
    assert "Add a code project" not in text  # nothing to add to until it is on


def test_code_page_offers_every_action_to_an_admin(client, monkeypatch):
    from test_support.plain_language import developer_terms, visible_text
    _code_on(monkeypatch)
    page = client.get("/code").text
    assert developer_terms(page) == []
    text = visible_text(page)
    for label in ("Add a code project", "Start a change", "Merge all approved changes",
                  "Proposed changes", "Spending limit for this change"):
        assert label in text, label
    assert "/static/lightwork-code.js" in page
    js = client.get("/static/lightwork-code.js").text
    for label in ("Get latest changes", "Approve in the approvals queue", "Merge",
                  "Send to the original project", "Ask for approval again", "mvConfirm"):
        assert label in js, label
    for banned in ("innerHTML", "window.confirm", "window.alert", "'HTTP '", "maverick code"):
        assert banned not in js


def test_code_page_hides_admin_buttons_from_operators(client, monkeypatch):
    _code_on(monkeypatch)
    _as(monkeypatch, "operator")
    page = client.get("/code").text
    assert 'data-can-admin="false"' in page and 'id="code-add"' not in page


def test_immune_page_is_plain_and_has_controls(client):
    from test_support.plain_language import developer_terms, visible_text
    page = client.get("/immune").text
    assert developer_terms(page) == []
    text = visible_text(page)
    for label in ("Safety monitoring", "Reviewer alertness", "Practice re-runs",
                  "Record a practice drill result", "Check for conflicts", "Check for problems",
                  "Sign and download", "Confirm a signed record"):
        assert label in text, label
    for banned in ("Trifecta", "Wilson", "twin", "canary", "[immune]"):
        assert banned not in text, banned
    js = client.get("/static/lightwork-immune-console.js").text
    for label in ("Turn reviewer tests on", "Record tasks for re-runs", "Re-run", "Delete recording",
                  "Plant tests", "mvConfirm"):
        assert label in js, label
    assert "innerHTML" not in js and "[immune]" not in js


# -- code actions ----------------------------------------------------------------

def test_code_actions_need_admin(client, monkeypatch):
    _code_on(monkeypatch)
    _as(monkeypatch, "operator")
    for method, path, body in (
        ("post", "/api/v1/code/repos", {"name": "x", "source": "https://example.com/x.git"}),
        ("post", "/api/v1/code/repos/x/sync", None),
        ("post", "/api/v1/code/features", {"repo": "x", "title": "t"}),
        ("get", "/api/v1/code/proposals/0123456789abcdef", None),
        ("post", "/api/v1/code/proposals/0123456789abcdef/merge", None),
        ("post", "/api/v1/code/proposals/0123456789abcdef/push", None),
        ("post", "/api/v1/code/proposals/0123456789abcdef/renew", None),
        ("post", "/api/v1/code/merge-approved", None),
    ):
        kwargs = {"json": body} if body is not None else {}
        assert getattr(client, method)(path, **kwargs).status_code == 403, path


def test_code_actions_are_refused_while_the_vault_is_off(client):
    r = client.post("/api/v1/code/repos", json={"name": "x", "source": "https://example.com/x.git"})
    assert r.status_code == 503 and "turned off" in r.json()["detail"]


def test_add_repo_takes_only_a_web_address(client, monkeypatch, tmp_path):
    _code_on(monkeypatch)
    for source in (str(tmp_path), "file:///etc", "https://user:pw@example.com/x.git"):  # pragma: allowlist secret
        r = client.post("/api/v1/code/repos", json={"name": "Web site", "source": source})
        assert r.status_code == 422, source
        assert "web address" in r.json()["detail"]


def test_start_a_change_needs_an_ai_provider(client, monkeypatch, tmp_path):
    _code_on(monkeypatch)
    from maverick.codebase import vault
    monkeypatch.setattr(vault, "get", lambda name: object())
    import maverick.operator_preflight as pre
    monkeypatch.setattr(pre, "_routed_configuration_missing", lambda cfg: {"anthropic": ("api_key",)})
    r = client.post("/api/v1/code/features", json={"repo": "core", "title": "Add a button"})
    assert r.status_code == 503 and "AI provider" in r.json()["detail"]


def test_start_a_change_runs_the_kernel_swarm_in_the_background(client, monkeypatch):
    _code_on(monkeypatch)
    from maverick.codebase import vault
    from maverick_dashboard import code_actions_api
    monkeypatch.setattr(vault, "get", lambda name: object())
    import maverick.operator_preflight as pre
    monkeypatch.setattr(pre, "_routed_configuration_missing", lambda cfg: {})
    calls = []
    monkeypatch.setattr(code_actions_api, "_run_feature", lambda *a: calls.append(a))
    r = client.post("/api/v1/code/features", json={"repo": "core", "title": " Add a button ",
                                                   "max_dollars": 5})
    assert r.status_code == 202
    assert calls == [("core", "Add a button", "", 5.0)]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_merge_waits_for_the_approval_then_merges(client, monkeypatch, tmp_path):
    _code_on(monkeypatch)
    from maverick.codebase import features, vault
    from maverick_dashboard import app as dash_app
    src = _source_repo(tmp_path)
    vault.register("core", str(src))
    world = dash_app._world()
    p = features.propose(world, "core", "Tune", "", lambda gid, work: (work / "a.py").write_text(
        "VALUE = 2\n"))
    pid = p["id"]
    detail = client.get(f"/api/v1/code/proposals/{pid}").json()
    assert detail["status"] == "pending" and detail["files"] == ["a.py"]
    assert "VALUE = 2" in detail["changes"]
    # The kernel's gate: no merge and no push without an approved approval.
    r = client.post(f"/api/v1/code/proposals/{pid}/merge")
    assert r.status_code == 409 and "waiting for a person to approve" in r.json()["detail"]
    assert client.post(f"/api/v1/code/proposals/{pid}/push").status_code == 409
    assert client.post("/api/v1/code/merge-approved").json() == {"merged": 0, "not_merged": 0}
    # A renewal is refused while the current request is still open.
    assert client.post(f"/api/v1/code/proposals/{pid}/renew").status_code == 409
    row = {a["id"]: a for a in client.get("/api/v1/approvals").json()["approvals"]}[p["approval_id"]]
    ok = client.post(f"/api/v1/approvals/{row['id']}/approve", headers={
        "X-Lightwork-Approval-Binding": row["approval_card"]["binding_sha256"]})
    assert ok.status_code in (200, 204), ok.text[:300]
    merged = client.post(f"/api/v1/code/proposals/{pid}/merge")
    assert merged.status_code == 200 and merged.json() == {"merged": True, "repo": "core"}
    assert client.post(f"/api/v1/code/proposals/{pid}/merge").json()["detail"] == \
        "This change is already merged."
    summary = client.get("/api/v1/code/summary").json()
    assert summary["proposals"][0]["status"] == "merged"


def test_unknown_or_malformed_changes_are_not_found(client, monkeypatch):
    _code_on(monkeypatch)
    assert client.get("/api/v1/code/proposals/not-an-id").status_code == 404
    assert client.post("/api/v1/code/proposals/0123456789abcdef/merge").status_code == 404
    assert client.post("/api/v1/code/repos/nope/sync").status_code == 404


# -- immune actions --------------------------------------------------------------

def test_immune_settings_need_a_global_admin_and_take_effect(client, monkeypatch):
    from maverick import config
    r = client.post("/api/v1/immune/settings", json={"canaries": True})
    assert r.status_code == 200 and r.json()["canaries"] is True
    config.reset_config_cache()
    assert config.get_immune()["canaries"] is True
    assert client.get("/api/v1/immune/choices").json()["canaries"] is True
    _as(monkeypatch, "operator")
    assert client.post("/api/v1/immune/settings", json={"twin_record": True}).status_code == 403


def test_reviewer_tests_respect_the_switch_and_hide_the_flaw(client, monkeypatch):
    r = client.post("/api/v1/immune/canaries", json={"count": 1})
    assert r.status_code == 409 and "turned off" in r.json()["detail"]
    from maverick import config
    monkeypatch.setattr(config, "get_immune", lambda: {"canaries": True, "twin_record": False})
    r = client.post("/api/v1/immune/canaries", json={"count": 1})
    assert r.status_code == 409 and "no recent approvals" in r.json()["detail"]
    from maverick.immune import oversight
    monkeypatch.setattr(oversight, "generate_canary", lambda world, decision_class=None: {
        "approval_id": 9, "flaw": "SECRET-FLAW", "decision_class": "pay"})
    r = client.post("/api/v1/immune/canaries", json={"count": 3, "decision_class": "pay"})
    assert r.json() == {"planted": 3} and "SECRET" not in r.text
    _as(monkeypatch, "auditor")
    assert client.post("/api/v1/immune/canaries", json={"count": 1}).status_code == 403


def test_drills_recordings_and_rules(client, monkeypatch):
    from maverick.immune import oversight, twin
    assert client.post("/api/v1/immune/drills", json={
        "person": "user:amy", "decision_class": "payments", "passed": True}).json() == {"recorded": True}
    from maverick_dashboard import app as dash_app
    assert oversight.score(dash_app._world(), "drill")[("user:amy", "payments")].caught == 1
    assert client.delete("/api/v1/immune/twin/41").status_code == 404
    path = twin.recording_path(41)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    choices = client.get("/api/v1/immune/choices").json()
    assert [g["id"] for g in choices["recorded_goals"]] == [41]
    assert client.delete("/api/v1/immune/twin/41").json() == {"deleted": True}
    assert not path.exists()
    rules = [{"id": "no-wire", "action": "wire", "modality": "forbid", "when": {"region": "eu"}},
             {"id": "must-wire", "action": "wire", "modality": "require", "when": {"region": "eu"}}]
    out = client.post("/api/v1/immune/rules/check", json={"rules": rules}).json()
    assert out["contradictions"] == [{"forbidding": "no-wire", "requiring": "must-wire",
                                      "when": "Region is eu"}]
    assert client.post("/api/v1/immune/rules/check", json={"rules": [{"x": 1}]}).status_code == 422
    # A large conflicting rule set is refused past the size cap and, below it,
    # listing stops at the first 200 conflicts.
    many = [{"id": f"r{i}", "modality": "forbid" if i % 2 else "require", "action": "x", "when": {}}
            for i in range(2001)]
    assert client.post("/api/v1/immune/rules/check", json={"rules": many}).status_code == 422
    out = client.post("/api/v1/immune/rules/check", json={"rules": many[:2000]}).json()
    assert out["rules"] == 2000 and len(out["contradictions"]) == 200
    _as(monkeypatch, "auditor")
    assert client.post("/api/v1/immune/twin/41/replay").status_code == 403
    assert client.post("/api/v1/immune/awareness", json={"goal_ids": [41]}).status_code == 403
    assert client.post("/api/v1/immune/title/seal", json={"dossier": {}}).status_code == 403


def test_replay_needs_a_recording_and_an_ai_provider(client, monkeypatch):
    from maverick.immune import twin
    from maverick_dashboard import app as dash_app
    from maverick_dashboard import immune_actions_api
    assert client.post("/api/v1/immune/twin/5/replay").status_code == 404
    gid = dash_app._world().create_goal("Pay invoices", "")
    path = twin.recording_path(gid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    import maverick.operator_preflight as pre
    monkeypatch.setattr(pre, "_routed_configuration_missing", lambda cfg: {"x": ("api_key",)})
    assert client.post(f"/api/v1/immune/twin/{gid}/replay").status_code == 503
    monkeypatch.setattr(pre, "_routed_configuration_missing", lambda cfg: {})
    started = []
    monkeypatch.setattr(immune_actions_api, "_replay_in_background", started.append)
    assert client.post(f"/api/v1/immune/twin/{gid}/replay").status_code == 202
    assert started == [gid]


def test_discovery_record_check_and_verify(client):
    out = client.post("/api/v1/immune/title/check", json={"dossier": {"claims": []}})
    assert out.status_code in (200, 422)
    bad = client.post("/api/v1/immune/title/verify", json={"sealed": {"dossier": {}}}).json()
    assert bad == {"verified": False}


def test_people_list_is_for_administrators_and_the_active_workspace(client, monkeypatch):
    import maverick.paths as paths
    from maverick_dashboard import rbac
    from maverick_dashboard.immune_actions_api import people_choices

    rbac.set_role("user:boss", "admin")
    rbac.set_role("user:worker", "viewer")
    rbac.set_tenant_role("acme", "user:acme-member", "operator")
    rbac.set_tenant_role("globex", "user:globex-member", "operator")
    everyone = {p["value"] for p in people_choices()}
    assert {"user:boss", "user:worker"} <= everyone and "user:globex-member" not in everyone
    monkeypatch.setattr(paths, "current_tenant_id", lambda: "acme")
    in_acme = {p["value"] for p in people_choices()}
    assert "user:acme-member" in in_acme and "user:boss" in in_acme
    assert "user:globex-member" not in in_acme and "user:worker" not in in_acme
    monkeypatch.setattr(paths, "current_tenant_id", lambda: None)
    _as(monkeypatch, "auditor")
    body = client.get("/api/v1/immune/choices").json()
    assert body["people"] == []
