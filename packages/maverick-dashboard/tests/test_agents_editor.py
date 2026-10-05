"""Per-client agent editor: roster + override REST endpoints and the page.

Mutating /api/v1 requests carry a same-origin Origin (the CSRF contract; see
test_api.py). The domains dir is redirected to a temp dir so overrides never
touch the real workspace, and the world DB is isolated per the usual pattern.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})


def _clean_builtin_name() -> str:
    from maverick.domain import builtin_dir, lint_profile, load_domains
    for name, prof in sorted(load_domains(builtin_dir()).items()):
        if not lint_profile(prof)[0]:
            return name
    raise AssertionError("no lint-error-clean built-in pack found")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("MAVERICK_DOMAINS_DIR", str(tmp_path / "domains"))
    yield


class TestRosterAndView:
    def test_page_renders_with_roster(self):
        r = client.get("/agents")
        assert r.status_code == 200
        assert "agents" in r.text.lower()

    def test_picker_shows_ready_first_and_keeps_setup_recovery_discoverable(self, monkeypatch):
        from maverick import domain_edit

        monkeypatch.setattr(domain_edit, "list_agents", lambda: [
            {"name": "unknown_pack", "suites": [], "description": "Unknown specialist",
             "is_override": False, "readiness": {"state": "unknown", "label": "Setup check unavailable"}},
            {"name": "blocked_pack", "suites": [], "description": "Blocked specialist",
             "is_override": False, "readiness": {"state": "needs_setup", "label": "Needs Web search"}},
            {"name": "ready_pack", "suites": [], "description": "Ready specialist",
             "is_override": False, "readiness": {"state": "action_ready", "label": "Can start"}},
            {"name": "ready-pack", "suites": [], "description": "Ready specialist",
             "is_override": False, "readiness": {"state": "action_ready", "label": "Can start"}},
        ])
        response = client.get("/agents")
        assert response.status_code == 200
        # The picker searches /agents/picker (the page no longer ships every
        # specialist): ready ones first, setup recovery still discoverable.
        assert "Show specialists that need setup" in response.text
        assert "2 can start · 1 needs setup · 1 needs a setup check" in response.text
        items = client.get("/agents/picker", params={"setup": "1"}).json()["items"]
        names = [item["name"] for item in items]
        assert names.index("ready_pack") < names.index("blocked_pack") < names.index("unknown_pack")
        labels = {item["name"]: item["label"] for item in items}
        assert labels["ready_pack"] == "Ready specialist · Ready pack"
        assert labels["ready-pack"] == "Ready specialist · Ready pack (ready-pack)"
        ready_only = client.get("/agents/picker").json()["items"]
        assert {item["name"] for item in ready_only} == {"ready_pack", "ready-pack"}

    def test_unknown_setup_check_has_recovery_link(self, monkeypatch):
        from maverick import domain_edit

        monkeypatch.setattr(domain_edit, "_configured_tool_names", lambda: None)
        response = client.get(f"/agents?name={_clean_builtin_name()}")
        assert response.status_code == 200
        assert "Setup check unavailable" in response.text
        assert 'href="/settings">Review setup</a>' in response.text

    def test_list_endpoint_includes_builtin(self):
        name = _clean_builtin_name()
        r = client.get("/api/v1/agents")
        assert r.status_code == 200
        assert name in {a["name"] for a in r.json()["agents"]}

    def test_get_agent_returns_merged_view(self):
        name = _clean_builtin_name()
        r = client.get(f"/api/v1/agents/{name}")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == name
        assert body["is_override"] is False
        assert body["overridden"] == []

    def test_get_unknown_agent_404(self):
        assert client.get("/api/v1/agents/no_such_pack").status_code == 404


class TestOverrideLifecycle:
    def test_independence_control_round_trips_and_requires_explicit_preservation(self, monkeypatch):
        import maverick.audit as audit

        monkeypatch.setattr(audit, "audit_event", lambda *_args, **_kwargs: True)
        name = _clean_builtin_name()
        current = client.get(f"/api/v1/agents/{name}").json()
        policy = {**current["autonomy"]["pack"], "default": "observe"}
        saved = client.post(f"/api/v1/agents/{name}/override", json={
            "expected_generation": current["generation"], "autonomy": policy,
        })
        assert saved.status_code == 200, saved.text
        assert saved.json()["autonomy"]["pack"]["default"] == "observe"
        assert "autonomy" in saved.json()["overridden"]
        from maverick.domain_edit import read_override

        assert read_override(name)["autonomy"]["default"] == "observe"
        stale_editor = client.post(f"/api/v1/agents/{name}/override", json={
            "expected_generation": saved.json()["generation"], "description": "new brief",
        })
        assert stale_editor.status_code == 409
        assert client.get(f"/api/v1/agents/{name}").json()["autonomy"]["pack"]["default"] == "observe"

    def test_editor_explains_effective_independence(self):
        name = _clean_builtin_name()
        html = client.get(f"/agents?name={name}").text
        assert 'id="agent-autonomy"' in html
        assert 'id="f-auto-low"' in html
        assert 'id="f-auto-high"' in html
        assert 'id="f-auto-onboarding"' in html
        assert "Supervision lowers each chosen level by one step" in html
        assert "Act on its own needs proof for the specific actions" in html

    def test_aggregate_approvals_cannot_graduate_act_policy(self, monkeypatch):
        from types import SimpleNamespace

        import maverick.agent_autonomy as autonomy
        import maverick.audit as audit

        monkeypatch.setattr(autonomy, "levels_enabled", lambda: True)
        monkeypatch.setattr(autonomy, "graduation_status", lambda *_args: SimpleNamespace(
            graduated=True, sample=100))
        monkeypatch.setattr(audit, "audit_event", lambda *_args, **_kwargs: True)
        name = _clean_builtin_name()
        current = client.get(f"/api/v1/agents/{name}").json()
        policy = {"default": "suggest", "low": "request", "medium": "request",
                  "high": "auto", "onboarding": True}
        saved = client.post(f"/api/v1/agents/{name}/override", json={
            "expected_generation": current["generation"], "autonomy": policy,
        })
        assert saved.status_code == 200, saved.text
        generation = saved.json()["generation"]

        graduated = client.post(f"/api/v1/agents/{name}/graduate", json={
            "expected_generation": generation,
        })
        assert graduated.status_code == 409
        assert "overall approval record cannot prove" in graduated.json()["detail"]
        assert client.get(f"/api/v1/agents/{name}").json()["autonomy"]["onboarding"]

        policy["onboarding"] = False
        proposed = {"expected_generation": generation, "autonomy": policy}
        checked = client.post(f"/api/v1/agents/{name}/validate", json=proposed)
        assert checked.status_code == 200
        assert any("overall approval record cannot prove" in error
                   for error in checked.json()["errors"])
        rejected = client.post(f"/api/v1/agents/{name}/override", json=proposed)
        assert rejected.status_code == 422

        policy["high"] = "request"
        safe = client.post(f"/api/v1/agents/{name}/override", json={
            "expected_generation": generation, "autonomy": policy,
        })
        assert safe.status_code == 200, safe.text
        policy["high"] = "auto"
        widened = client.post(f"/api/v1/agents/{name}/override", json={
            "expected_generation": safe.json()["generation"], "autonomy": policy,
        })
        assert widened.status_code == 422

    def test_graduation_rechecks_evidence_and_generation(self, monkeypatch):
        from types import SimpleNamespace

        import maverick.agent_autonomy as autonomy
        import maverick.audit as audit

        name = _clean_builtin_name()
        monkeypatch.setattr(autonomy, "levels_enabled", lambda: True)
        view = client.get(f"/api/v1/agents/{name}").json()
        generation = view["generation"]
        monkeypatch.setattr(autonomy, "graduation_status", lambda *_args: SimpleNamespace(
            graduated=False, sample=2))
        denied = client.post(f"/api/v1/agents/{name}/graduate", json={
            "expected_generation": generation,
        })
        assert denied.status_code == 409
        assert client.get(f"/api/v1/agents/{name}").json()["autonomy"]["onboarding"]

        events = []
        monkeypatch.setattr(autonomy, "graduation_status", lambda *_args: SimpleNamespace(
            graduated=True, sample=8))
        monkeypatch.setattr(audit, "audit_event", lambda *_args, **_kwargs: False)
        no_record = client.post(f"/api/v1/agents/{name}/graduate", json={
            "expected_generation": generation,
        })
        assert no_record.status_code == 503
        assert client.get(f"/api/v1/agents/{name}").json()["autonomy"]["onboarding"]
        monkeypatch.setattr(audit, "audit_event", lambda *args, **kwargs: events.append(kwargs) or True)
        granted = client.post(f"/api/v1/agents/{name}/graduate", json={
            "expected_generation": generation,
        })
        assert granted.status_code == 200, granted.text
        assert granted.json()["autonomy"]["onboarding"] is False
        assert events and events[0]["verb"] == "specialist_independence_change_requested"
        assert events[0]["approved_samples"] == 8
        stale = client.post(f"/api/v1/agents/{name}/graduate", json={
            "expected_generation": generation,
        })
        assert stale.status_code == 409

    def test_save_override_persists_and_marks_provenance(self, tmp_path):
        name = _clean_builtin_name()
        r = client.post(f"/api/v1/agents/{name}/override",
                        json={"description": "Tuned for ACME."})
        assert r.status_code == 200
        assert r.json()["is_override"] is True
        assert "description" in r.json()["overridden"]
        assert (tmp_path / "domains" / f"{name}.toml").is_file()
        # A fresh GET reflects the override too.
        assert client.get(f"/api/v1/agents/{name}").json()["description"] == "Tuned for ACME."

    def test_workflow_edit_round_trips(self):
        name = _clean_builtin_name()
        steps = [{"name": "intake", "instruction": "Read the request.",
                  "tools": [], "gate": None}]
        r = client.post(f"/api/v1/agents/{name}/override", json={"workflow": steps})
        assert r.status_code == 200
        assert [s["name"] for s in r.json()["workflow"]] == ["intake"]

    def test_page_renders_workflow_as_editable_rows(self):
        name = _clean_builtin_name()
        client.post(f"/api/v1/agents/{name}/override", json={"workflow": [
            {"name": "intake", "instruction": "Read the request.", "tools": [], "gate": None},
            {"name": "review", "instruction": "Check it.", "tools": [], "gate": "approval"},
        ]})
        html = client.get(f"/agents?name={name}").text
        # The playbook renders as structured rows (not a raw JSON textarea),
        # with an add-step control and a clone template for new rows.
        assert 'class="input wf-name"' in html
        assert 'value="intake"' in html and "Read the request." in html
        assert "Check it." in html
        assert ">Add a step</button>" in html and 'id="wf-row-tpl"' in html
        # Steps move and are removed with labelled buttons, not bare arrows.
        assert ">Move up</button>" in html and ">Remove</button>" in html
        row = html.split('id="wf-row-tpl"', 1)[1].split("</template>", 1)[0]
        assert "↑" not in row and "✕" not in row
        assert 'name="workflow"' not in html   # the old JSON textarea is gone

    def test_invalid_override_rejected_422(self):
        # Empty allow_tools makes the merged pack fail lint -> rejected, not written.
        name = _clean_builtin_name()
        r = client.post(f"/api/v1/agents/{name}/override", json={"allow_tools": []})
        assert r.status_code == 422
        assert client.get(f"/api/v1/agents/{name}").json()["is_override"] is False

    def test_validate_endpoint_reports_without_writing(self):
        name = _clean_builtin_name()
        r = client.post(f"/api/v1/agents/{name}/validate", json={"allow_tools": []})
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert r.json()["errors"]
        assert client.get(f"/api/v1/agents/{name}").json()["is_override"] is False

    def test_factory_cannot_save_actions_missing_from_installed_registry(self, tmp_path):
        name = "invoice_chaser"
        body = {"allow_tools": ["files", "code"], "max_risk": "medium",
                "persona": "Handle invoices using the actions this installation provides."}
        review = client.post(f"/api/v1/agents/{name}/validate", json=body)
        assert review.status_code == 200
        assert any("'files'" in error for error in review.json()["errors"])
        assert any("'code'" in error for error in review.json()["errors"])
        saved = client.post(f"/api/v1/agents/{name}/override", json=body)
        assert saved.status_code == 422
        assert not (tmp_path / "domains" / f"{name}.toml").exists()
        body["allow_tools"] = ["read_file", "list_dir"]
        assert client.post(f"/api/v1/agents/{name}/validate", json=body).json()["ok"]
        assert client.post(f"/api/v1/agents/{name}/override", json=body).status_code == 200

    def test_delete_override_reverts(self, monkeypatch):
        import maverick.audit as audit

        events = []
        monkeypatch.setattr(audit, "audit_event", lambda *_args, **kwargs: events.append(kwargs) or True)
        name = _clean_builtin_name()
        client.post(f"/api/v1/agents/{name}/override", json={"description": "temp"})
        current = client.get(f"/api/v1/agents/{name}").json()
        assert current["is_override"] is True
        missing_revision = client.delete(f"/api/v1/agents/{name}/override")
        assert missing_revision.status_code == 409
        assert not events
        monkeypatch.setattr(audit, "audit_event", lambda *_args, **_kwargs: False)
        no_record = client.delete(f"/api/v1/agents/{name}/override", params={
            "expected_generation": current["generation"],
        })
        assert no_record.status_code == 503
        assert client.get(f"/api/v1/agents/{name}").json()["is_override"] is True
        monkeypatch.setattr(audit, "audit_event", lambda *_args, **kwargs: events.append(kwargs) or True)
        r = client.delete(f"/api/v1/agents/{name}/override", params={
            "expected_generation": current["generation"],
        })
        assert r.status_code == 200
        assert events and events[0]["verb"] == "specialist_override_reset_requested"
        assert "description" in events[0]["removed_fields"]
        assert client.get(f"/api/v1/agents/{name}").json()["is_override"] is False

    def test_rejected_builtin_can_be_restored_in_app_with_generation(self, tmp_path, monkeypatch):
        import maverick.audit as audit
        from maverick.domain_edit import rejected_override_view

        monkeypatch.setattr(audit, "audit_event", lambda *_args, **_kwargs: True)

        name = _clean_builtin_name()
        path = tmp_path / "domains" / f"{name}.toml"
        path.parent.mkdir()
        path.write_text(f'name = "{name}"\nallow_tools = []\n', encoding="utf-8")
        state = rejected_override_view(name)
        assert state and state["generation"] and state["is_builtin"]
        page = client.get("/agents").text
        assert "Specialists paused for setup review" in page
        assert f'data-name="{name}"' in page
        assert f'data-generation="{state["generation"]}"' in page
        assert "Restore original setup" in page
        assert client.get(f"/api/v1/agents/{name}").status_code == 404

        path.write_text(f'name = "{name}"\nallow_tools = ["shell"]\n', encoding="utf-8")
        stale = client.delete(f"/api/v1/agents/{name}/override", params={
            "expected_generation": state["generation"],
        })
        assert stale.status_code == 409 and path.exists()
        current = rejected_override_view(name)
        restored = client.delete(f"/api/v1/agents/{name}/override", params={
            "expected_generation": current["generation"],
        })
        assert restored.status_code == 200 and restored.json()["removed"] is True
        assert not path.exists()
        assert client.get(f"/api/v1/agents/{name}").status_code == 200

    def test_rejected_custom_specialist_can_be_removed_in_app(self, tmp_path, monkeypatch):
        import maverick.audit as audit

        monkeypatch.setattr(audit, "audit_event", lambda *_args, **_kwargs: True)
        path = tmp_path / "domains" / "custom_bad.toml"
        path.parent.mkdir()
        path.write_text('name = "custom_bad"\nmax_risk = "low"\n', encoding="utf-8")
        from maverick.domain_edit import rejected_override_view

        state = rejected_override_view("custom_bad")
        assert state and not state["is_builtin"]
        assert "Remove unavailable specialist" in client.get("/agents").text
        response = client.delete("/api/v1/agents/custom_bad/override", params={
            "expected_generation": state["generation"],
        })
        assert response.status_code == 200
        assert response.json() == {"removed": True, "agent": None}
        assert not path.exists()

    @pytest.mark.parametrize("method", ["post", "delete"])
    def test_windows_backslash_name_cannot_escape_domains_directory(
        self, tmp_path, method,
    ):
        escaped = tmp_path / "config.toml"
        escaped.write_text("sentinel", encoding="utf-8")
        url = "/api/v1/agents/..%5Cconfig/override"

        if method == "post":
            response = client.post(url, json={"description": "overwrite"})
        else:
            response = client.delete(url)

        assert response.status_code == 422
        assert escaped.read_text(encoding="utf-8") == "sentinel"


class TestPackEditingGate:
    def test_mutations_403_when_disabled(self, monkeypatch):
        import maverick.config as cfg
        monkeypatch.setattr(cfg, "get_features",
                            lambda: {"skills": True, "world_model": True,
                                     "streaming": True, "pack_editing": False})
        name = _clean_builtin_name()
        assert client.post(f"/api/v1/agents/{name}/override",
                           json={"description": "x"}).status_code == 403
        assert client.delete(f"/api/v1/agents/{name}/override").status_code == 403
        assert client.post(f"/api/v1/agents/{name}/graduate", json={
            "expected_generation": "0" * 64,
        }).status_code == 403
        # Read-only access still works.
        assert client.get(f"/api/v1/agents/{name}").status_code == 200

class TestPackEditingAuthorization:
    def test_authenticated_non_admin_cannot_mutate_overrides(self, monkeypatch):
        import maverick_dashboard.api as api
        monkeypatch.setattr(api, "caller_principal", lambda request: "user:alice")
        monkeypatch.setattr(api, "is_dashboard_admin", lambda principal: False)
        name = _clean_builtin_name()

        assert client.post(f"/api/v1/agents/{name}/override",
                           json={"description": "blocked"}).status_code == 403
        assert client.delete(f"/api/v1/agents/{name}/override").status_code == 403
        assert client.post(f"/api/v1/agents/{name}/graduate", json={
            "expected_generation": "0" * 64,
        }).status_code == 403
        assert client.get(f"/api/v1/agents/{name}").status_code == 200

    def test_dashboard_admin_can_mutate_overrides(self, monkeypatch):
        import maverick_dashboard.api as api
        monkeypatch.setattr(api, "caller_principal", lambda request: "user:root")
        monkeypatch.setattr(api, "is_dashboard_admin", lambda principal: True)
        name = _clean_builtin_name()

        r = client.post(f"/api/v1/agents/{name}/override",
                        json={"description": "admin tuned"})

        assert r.status_code == 200
        assert r.json()["description"] == "admin tuned"

    def test_non_admin_cannot_see_recovery_controls(self, tmp_path, monkeypatch):
        import maverick_dashboard.api as api

        name = _clean_builtin_name()
        path = tmp_path / "domains" / f"{name}.toml"
        path.parent.mkdir()
        path.write_text(f'name = "{name}"\nallow_tools = []\n', encoding="utf-8")
        monkeypatch.setattr(api, "caller_principal", lambda request: "user:alice")
        monkeypatch.setattr(api, "is_dashboard_admin", lambda principal: False)

        assert "af-recover" not in client.get("/agents").text
        assert client.delete(f"/api/v1/agents/{name}/override").status_code == 403
        assert path.exists()


def test_fork_agent_detail_requires_inherited_suite(tmp_path, monkeypatch):
    import maverick_dashboard.auth as auth

    path = tmp_path / "domains" / "client_fork.toml"
    path.parent.mkdir()
    path.write_text('name = "client_fork"\nextends = "aero_avionics"\n', encoding="utf-8")
    monkeypatch.setattr(auth, "caller_suites", lambda request: frozenset({"finance"}))

    assert client.get("/api/v1/agents/client_fork").status_code == 403
    assert client.get("/api/v1/agents/client_fork/jobcard").status_code == 403
    assert 'data-name="client_fork"' not in client.get("/agents?name=client_fork").text


def _pack_with_denied_tool_and_risk_below_high() -> tuple[str, str]:
    from maverick.domain import builtin_dir, lint_profile, load_domains
    for name, prof in sorted(load_domains(builtin_dir()).items()):
        if not lint_profile(prof)[0] and prof.deny_tools and prof.max_risk != "high":
            return name, prof.deny_tools[0]
    raise AssertionError("no suitable built-in pack found")


class TestPackEditingSafetyEnvelope:
    def test_override_cannot_widen_tools_or_raise_risk(self):
        name, denied_tool = _pack_with_denied_tool_and_risk_below_high()
        r = client.post(f"/api/v1/agents/{name}/override", json={
            "allow_tools": [denied_tool],
            "deny_tools": [],
            "max_risk": "high",
        })

        assert r.status_code == 422
        detail = r.json()["detail"]
        assert "allow_tools cannot add tools" in detail
        assert "deny_tools cannot remove" in detail
        assert "max_risk cannot be raised" in detail
        assert client.get(f"/api/v1/agents/{name}").json()["is_override"] is False

def test_restored_agent_revision_cannot_overwrite_a_newer_save():
    name = _clean_builtin_name()
    original = client.get(f"/api/v1/agents/{name}").json()
    first = client.post(f"/api/v1/agents/{name}/override", json={"description": "Newer saved work", "expected_generation": original["generation"]})
    assert first.status_code == 200
    stale = client.post(f"/api/v1/agents/{name}/override", json={"description": "Recovered old work", "expected_generation": original["generation"]})
    assert stale.status_code == 409
    assert client.get(f"/api/v1/agents/{name}").json()["description"] == "Newer saved work"

def test_stale_reset_cannot_delete_a_newer_agent():
    name = _clean_builtin_name()
    original = client.post(f"/api/v1/agents/{name}/override", json={"description": "First"}).json()
    newer = client.post(f"/api/v1/agents/{name}/override", json={"description": "Second", "expected_generation": original["generation"]})
    assert newer.status_code == 200
    stale = client.delete(f"/api/v1/agents/{name}/override", params={"expected_generation": original["generation"]})
    assert stale.status_code == 409
    assert client.get(f"/api/v1/agents/{name}").json()["description"] == "Second"


def test_agent_factory_reads_plainly():
    from test_support.plain_language import developer_terms

    for path in ("/agents", "/agents?new=1"):
        html = client.get(path).text
        assert developer_terms(html) == [], path
        assert "window.prompt" not in html and "'HTTP '" not in html
        assert "Team (fleet) name" not in html and "/Mtok" not in html
