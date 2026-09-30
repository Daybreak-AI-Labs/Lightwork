"""Settings in plain language: readable model names and setting sources, the
reviewed webhook secret form, a switch for automatic model lists, the
task-limit choice, and the Maintenance tab's installation checks."""
from __future__ import annotations

import html as _html
import re

import pytest
from fastapi.testclient import TestClient

ORIGIN = {"Origin": "http://testserver"}

# Words a person must never see on these pages (config keys, env vars, CLI,
# raw ids and developer terms).
BANNED = (
    "MAVERICK_", "config.toml", "[models]", "environment:", "maverick ",
    "operator overlay", "tenant config", "payload", "endpoint", "schema",
    "SHA-256", "idempotency", "HTTP ", "provider:model", "openai:gpt",
    "Type a model id", "Base URL",
)


def visible_text(page: str) -> str:
    main = page.split('<main id="content"', 1)[-1].split("</main>", 1)[0]
    main = re.sub(r"<(script|style)\b.*?</\1>", " ", main, flags=re.DOTALL)
    main = re.sub(r"<[^>]+>", " ", main)
    return _html.unescape(re.sub(r"\s+", " ", main))


@pytest.fixture
def client(tmp_path, monkeypatch):
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "mvhome"))
    for name in ("MAVERICK_WEBHOOK_SECRET", "MAVERICK_DASHBOARD_TOKEN", "MAVERICK_MODEL_DISCOVERY",
                 "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    import maverick.runtime_overrides as ro
    monkeypatch.setattr(ro, "OVERRIDES_PATH", tmp_path / "runtime-overrides.toml")
    from maverick import config
    config.reset_config_cache()
    from maverick_dashboard import app as dash_app
    dash_app._world_cache.clear()
    yield TestClient(dash_app.app, headers=ORIGIN)
    config.reset_config_cache()


def _as_viewer(monkeypatch):
    from maverick_dashboard import auth, rbac
    rbac.set_role("user:vic", "viewer")
    monkeypatch.setattr(auth, "caller_principal", lambda request: "user:vic")


def test_settings_page_reads_as_plain_language(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-0000")  # pragma: allowlist secret
    from maverick import config
    config.reset_config_cache()
    page = client.get("/settings").text
    text = visible_text(page)
    for word in BANNED:
        assert word not in text, word
    # Model names are readable; the exact id stays only in the option value.
    assert 'value="openai:gpt-5.4"' in page and "GPT-5.4 · OpenAI" in text
    # Where the OpenAI key came from, in words.
    assert "set on the computer running Lightwork" in text
    # Buttons for everything the page describes.
    for label in ("Test connection", "Refresh status", "Apply change", "Check readiness", "Run health check",
                  "Check installation settings", "Show license", "Show background work",
                  "Check encryption", "Check services"):
        assert label in text, label
    assert "Where do I get a key?" in text
    assert 'data-settings-tab="maintenance"' in page


def test_model_label_and_source_label():
    from maverick_dashboard.plain_labels import model_label, source_label, unique_slug
    assert model_label("claude-opus-4-8") == "Claude Opus 4.8 · Anthropic"
    assert model_label("gemini:gemini-3.5-pro") == "Gemini 3.5 Pro · Google Gemini"
    assert source_label("environment:OPENAI_API_KEY") == "set on the computer running Lightwork"
    assert source_label("tenant config") == "set for this workspace"
    assert "MAVERICK" not in source_label("environment:MAVERICK_X")
    assert unique_slug("Acme — EU", {"acme-eu"}) == "acme-eu-2"
    assert unique_slug("123", set(), fallback="workspace") == "workspace-123"


def test_webhook_secret_changes_only_through_the_reviewed_form(client):
    # The one-click generator bypassed the reviewed change (current owner and
    # generation); the Settings card now saves a secret only after review.
    assert client.post("/settings/webhooks/generate").status_code in {404, 405}
    from maverick.webhooks import inbound_secret
    assert inbound_secret() is None
    page = client.get("/settings").text
    assert 'id="wh-form"' in page and 'id="webhook-generate"' not in page
    assert "Make a secret" not in visible_text(page)


def test_model_discovery_switch(client, monkeypatch):
    from maverick import model_discovery
    assert client.post("/settings/models/discovery", data={"enabled": "off"},
                       follow_redirects=False).status_code == 303
    from maverick import config
    config.reset_config_cache()
    assert model_discovery.discovery_enabled() is False
    assert "Turn on automatic model lists" in client.get("/settings").text
    client.post("/settings/models/discovery", data={"enabled": "on"}, follow_redirects=False)
    config.reset_config_cache()
    assert model_discovery.discovery_enabled() is True
    monkeypatch.setenv("MAVERICK_MODEL_DISCOVERY", "0")
    assert client.post("/settings/models/discovery", data={"enabled": "on"}).status_code == 409
    _as_viewer(monkeypatch)
    monkeypatch.delenv("MAVERICK_MODEL_DISCOVERY")
    assert client.post("/settings/models/discovery", data={"enabled": "on"}).status_code == 403


def test_task_limit_standard_choice(client):
    from maverick.runtime_overrides import budget_override, task_limits_override
    client.post("/settings/budget", data={"mode": "configured", "max_dollars": "3"}, follow_redirects=False)
    assert budget_override() == 3
    client.post("/settings/budget", data={"mode": "default", "max_dollars": "9"}, follow_redirects=False)
    assert budget_override() is None and task_limits_override() is True
    assert "each task follows your installation's standard limits" in client.get("/settings").text


# ---- Maintenance tab APIs ---------------------------------------------------

def test_system_checks_require_admin(client, monkeypatch):
    _as_viewer(monkeypatch)
    for path in ("readiness", "settings-check", "license", "queue", "encryption", "diagnostics"):
        assert client.get(f"/api/v1/admin/system/{path}").status_code == 403, path
    assert client.post("/api/v1/admin/system/health-check").status_code == 403
    assert client.post("/api/v1/admin/system/encryption/new-key").status_code == 403
    assert client.post("/api/v1/admin/system/queue/1/resume", json={"attempts": 1}).status_code == 403


def test_readiness_rows_are_plain_with_fix_links(client):
    data = client.get("/api/v1/admin/system/readiness").json()
    keys = {row["key"] for row in data["checks"]}
    assert keys == {"spending", "separation", "license", "encryption", "provider"}
    provider = next(r for r in data["checks"] if r["key"] == "provider")
    assert provider["ok"] is False and provider["fix_href"].startswith("/settings")
    for row in data["checks"]:
        for word in ("MAVERICK_", "[", "maverick "):
            assert word not in row["detail"], row


def test_settings_check_without_a_file(client):
    data = client.get("/api/v1/admin/system/settings-check").json()
    assert data["file_present"] is False and data["errors"] == 0
    assert "built-in settings" in data["summary"]


def test_settings_check_reports_problems_in_words(client):
    from maverick import config
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[budget]\nmax_dollars = \"lots\"\n", encoding="utf-8")
    data = client.get("/api/v1/admin/system/settings-check").json()
    assert data["file_present"] is True
    assert data["errors"] >= 1 and "cannot be used" in data["summary"]
    assert data["technical"]  # exact findings kept for the collapsed details


def test_license_summary_without_a_license(client):
    data = client.get("/api/v1/admin/system/license").json()
    assert data["status_label"] == "No license"
    assert data["features"] and all("_" not in f["label"] for f in data["features"])


def test_health_check_collects_rows_without_printing(client, monkeypatch):
    from maverick import health

    def fake_diagnose():
        health._FAILURES.clear()
        health._row(health.GREEN, "config", "found")
        health._row(health.RED, "world DB", "unreadable", "run maverick doctor")
        return len(health._FAILURES)
    monkeypatch.setattr(health, "diagnose", fake_diagnose)
    data = client.post("/api/v1/admin/system/health-check").json()
    assert data["healthy"] is False and data["problems"] == 1
    assert "maverick" not in data["summary"]
    assert [r["state"] for r in data["technical"]] == ["ok", "problem"]
    assert health._row is not None and health._row.__name__ == "_row"


def test_queue_lists_stopped_jobs_and_resumes_a_paused_schedule(client, monkeypatch, tmp_path):
    from maverick import job_queue
    queue = job_queue.JobQueue(tmp_path / "jobs.db")
    monkeypatch.setattr(job_queue, "_default_db", lambda: tmp_path / "jobs.db")
    job_id = queue.enqueue("run_goal", {"goal_id": 1})
    with queue._conn() as c:
        c.execute("UPDATE jobs SET status='failed', attempts=2, recurrence_reconcile_required=1, "
                  "recurrence_payload=?, recurrence_id='r1' WHERE id=?",
                  ('{"cron": "0 9 * * *"}', job_id))
    data = client.get("/api/v1/admin/system/queue").json()
    stopped = data["stopped"][0]
    assert stopped["what"] == "Run a task" and stopped["needs_decision"] is True
    # A stale view (wrong attempt count) is refused, never resumed blind.
    assert client.post(f"/api/v1/admin/system/queue/{job_id}/resume", json={"attempts": 1}).status_code == 409
    assert TestClient(client.app).post(f"/api/v1/admin/system/queue/{job_id}/resume",
                                       json={"attempts": 2}).status_code == 403


def test_encryption_status_and_new_key_when_off(client, monkeypatch):
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    data = client.get("/api/v1/admin/system/encryption").json()
    assert data["enabled"] is False
    r = client.post("/api/v1/admin/system/encryption/new-key")
    assert r.status_code in (200, 503)


def test_diagnostics_shape(client):
    data = client.get("/api/v1/admin/system/diagnostics").json()
    assert set(data) == {"paused_services", "recent_calls"}


# ---- review fixes: audit, redaction and the right world database ---------

@pytest.fixture
def audit_rows(monkeypatch):
    import maverick.audit as audit

    rows: list[dict] = []

    def capture(kind, **payload):
        rows.append({"kind": kind, **payload})
        return True

    monkeypatch.setattr(audit, "audit_event", capture)
    return rows


def test_resuming_a_schedule_is_recorded_with_who_did_it(client, monkeypatch, tmp_path, audit_rows):
    from maverick import job_queue
    from maverick_dashboard import auth
    monkeypatch.setattr(job_queue, "_default_db", lambda: tmp_path / "jobs.db")
    monkeypatch.setattr(job_queue.JobQueue, "reconcile_recurrence",
                        lambda self, job_id, **kw: 77)
    monkeypatch.setattr(auth, "caller_principal", lambda request: "user:ada")
    from maverick_dashboard import rbac
    rbac.set_role("user:ada", "admin")
    r = client.post("/api/v1/admin/system/queue/5/resume", json={"attempts": 2})
    assert r.status_code == 200, r.text
    rows = [row for row in audit_rows if row.get("verb") == "queue_resume"]
    assert len(rows) == 1
    assert rows[0]["actor"] == "user:ada" and rows[0]["job_id"] == 5


def test_stopped_job_errors_are_redacted_like_the_action_center(client, monkeypatch, tmp_path):
    from maverick import job_queue
    monkeypatch.setattr(job_queue, "_default_db", lambda: tmp_path / "jobs.db")
    q = job_queue.JobQueue()
    q.enqueue("run_goal", {"title": "t"})
    job = q.claim()
    secret = "provider said: invalid x-api-key sk-ant-api03-" + "A" * 40  # pragma: allowlist secret
    q.fail(job.id, secret, max_attempts=1, expected_attempts=job.attempts)
    stopped = client.get("/api/v1/admin/system/queue").json()["stopped"][0]
    assert "sk-ant-api03-AAAA" not in stopped["technical"]
    assert "REDACTED" in stopped["technical"]
    summary = client.get("/api/v1/queue/summary").json()["failed_items"][0]
    assert stopped["technical"] == summary["detail"][:300]


def test_encryption_maintenance_uses_the_workspace_world(client, monkeypatch, tmp_path, audit_rows):
    from maverick import crypto_at_rest, encryption_migrate, paths
    from maverick_dashboard import system_checks_api as sc

    with paths.tenant_scope(tenant="acme"):
        from maverick_dashboard import _shared
        expected = _shared._world().path
        assert str(sc._world_db()) == str(expected)
        assert "acme" in str(sc._world_db())

    seen: list[str] = []

    def fake_migrate(db, dry_run=False, backup=False):
        seen.append(str(db))
        return {"turns.content": 3}

    monkeypatch.setattr(crypto_at_rest, "at_rest_enabled", lambda: True)
    monkeypatch.setattr(encryption_migrate, "migrate_world_db", fake_migrate)
    monkeypatch.setattr(sc, "_world_db", lambda: tmp_path / "chosen.db")
    (tmp_path / "chosen.db").write_bytes(b"")
    assert client.get("/api/v1/admin/system/encryption").json()["older_unprotected"] == 3
    r = client.post("/api/v1/admin/system/encryption/protect-older")
    assert r.status_code == 200 and r.json()["protected"] == 3
    assert seen == [str(tmp_path / "chosen.db")] * 2
    rows = [row for row in audit_rows if row.get("verb") == "encryption_protect_older"]
    assert len(rows) == 1 and rows[0]["records"] == 3 and rows[0]["actor"]


def test_encryption_maintenance_says_plainly_when_the_database_is_shared(
    client, monkeypatch, audit_rows,
):
    from maverick import crypto_at_rest, encryption_migrate
    from maverick import world_model_backends as backends

    monkeypatch.setattr(crypto_at_rest, "at_rest_enabled", lambda: True)
    monkeypatch.setattr(backends, "is_postgres_configured", lambda: True)

    def never(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("migrate must not run against a local file")

    monkeypatch.setattr(encryption_migrate, "migrate_world_db", never)
    data = client.get("/api/v1/admin/system/encryption").json()
    assert data["older_unprotected"] is None
    assert data["older_note"] and "cannot" in data["older_note"]
    r = client.post("/api/v1/admin/system/encryption/protect-older")
    assert r.status_code == 409
    assert "protected" not in r.json()
    assert audit_rows == []


def test_new_encryption_key_is_recorded(client, monkeypatch, audit_rows):
    from maverick import crypto_at_rest
    monkeypatch.setattr(crypto_at_rest, "rotate_at_rest_key", lambda: None)
    r = client.post("/api/v1/admin/system/encryption/new-key")
    assert r.status_code == 200
    rows = [row for row in audit_rows if row.get("verb") == "encryption_new_key"]
    assert len(rows) == 1 and rows[0]["actor"]


# ---- one-button checks, the organisation's data, tidying up ----------------

def _as(monkeypatch, role: str, who: str):
    from maverick_dashboard import auth, rbac
    rbac.set_role(who, role)
    monkeypatch.setattr(auth, "caller_principal", lambda request: who)


def test_maintenance_page_offers_the_new_checks_in_plain_words(client):
    text = visible_text(client.get("/settings").text)
    for label in ("Check protections", "Check connections", "Check hardware", "Check add-ons",
                  "Show data status", "See what would be removed", "Tidy up old records",
                  "Your organisation's data"):
        assert label in text, label
    for word in BANNED:
        assert word not in text, word


def test_one_button_checks_need_the_audit_permission(client, monkeypatch):
    paths = ("enterprise-check", "offline-check", "hardware-check", "plugin-check")
    _as_viewer(monkeypatch)
    for path in paths:
        assert client.get(f"/api/v1/admin/system/{path}").status_code == 403, path
    _as(monkeypatch, "auditor", "user:aud")
    for path in paths:
        r = client.get(f"/api/v1/admin/system/{path}")
        assert r.status_code == 200, path
        body = r.json()
        assert body["summary"] and body["summary"][0].isalnum() and body["summary"].endswith(".")
        for word in ("[", "MAVERICK_", "maverick "):
            assert word not in body["summary"], (path, word)
    # Reading the organisation's data status is an installation administrator job.
    assert client.get("/api/v1/admin/system/client").status_code == 403


def test_regulated_deployment_check_keeps_kernel_wording_in_the_details(client, monkeypatch):
    from maverick import deployment
    monkeypatch.setattr(deployment, "verify_deployment", lambda: [
        deployment.GuaranteeCheck("Egress lock", False, "enable [enterprise] mode = true"),
        deployment.GuaranteeCheck("At-rest encryption", True, "sealed")])
    body = client.get("/api/v1/admin/system/enterprise-check").json()
    assert body["passed"] is False
    assert body["checks"] == [{"label": "Data cannot be sent to outside AI services", "ok": False},
                              {"label": "Saved data is encrypted", "ok": True}]
    assert "1 protection" in body["summary"]
    assert body["technical"][0]["text"] == "Egress lock: enable [enterprise] mode = true"


def test_offline_check_groups_findings_into_plain_reasons(client, monkeypatch):
    from maverick import air_gap
    monkeypatch.setattr(air_gap, "audit", lambda: {"clean": False, "violations": [
        "remote model(s) in use: claude", "[sandbox] allow_network is on",
        "[observability] sentry_dsn is set", "[sandbox] backend=local uses the host network"]})
    body = client.get("/api/v1/admin/system/offline-check").json()
    assert body["reasons"] == ["An AI model is reached over the internet.", "The task workspace can reach the network.",
                               "Error reports are sent to another computer."]
    assert len(body["technical"]) == 4


def test_plugin_check_names_each_mismatch(client, monkeypatch):
    from maverick import plugin_lock
    monkeypatch.setattr(plugin_lock, "verify_lock", lambda: {
        "ok": False, "unlocked": False, "drifted": [("acme-tools", "1.0", "1.2")],
        "missing": ["gone-plugin"], "unpinned": []})
    body = client.get("/api/v1/admin/system/plugin-check").json()
    assert body["ok"] is False and len(body["problems"]) == 2
    assert "acme-tools is a different version" in body["problems"][0]


def test_organisation_data_download_and_erase(client, monkeypatch, audit_rows):
    from maverick import client as client_binding
    monkeypatch.setenv("MAVERICK_BACKUP_SIGNING_KEY", "00" * 32)  # pragma: allowlist secret
    monkeypatch.setenv("MAVERICK_CLIENT_ID", "acme")
    client_binding.reset_client_cache()
    try:
        from maverick import world_model
        world_model.open_world().create_goal("Acme task")
        status = client.get("/api/v1/admin/system/client").json()
        assert status["bound"] is True and status["organisation"] == "acme"
        started = client.post("/api/v1/admin/system/client/export")
        assert started.status_code == 202, started.text
        job = started.json()["job"]
        import time as _time
        for _ in range(200):
            state = client.get(f"/api/v1/admin/system/client/export/{job}").json()
            if state["state"] != "running":
                break
            _time.sleep(0.05)
        assert state["state"] == "ready", state
        exported = client.get(f"/api/v1/admin/system/client/export/{job}/download")
        assert exported.status_code == 200, exported.text
        assert "attachment" in exported.headers["content-disposition"] and exported.content
        assert client.get("/api/v1/admin/system/client/export/" + "0" * 32).status_code == 404
        assert TestClient(client.app).post("/api/v1/admin/system/client/export").status_code == 403
        preview = client.get("/api/v1/admin/system/client/erase/preview").json()
        assert preview["organisation"] == "acme" and preview["files"] >= 1
        assert preview["keeps_activity_record"] is True
        assert all(area["label"] and area["files"] for area in preview["areas"])
        erase = "/api/v1/admin/system/client/erase"
        assert client.post(erase, json={"organisation": "acme", "confirm": False}).status_code == 409
        wrong = client.post(erase, json={"organisation": "other", "confirm": True})
        assert wrong.status_code == 409 and "changed" in wrong.json()["detail"]
        done = client.post(erase, json={"organisation": "acme", "confirm": True, "keep_activity_record": True})
        assert done.status_code == 200, done.text
        assert done.json()["erased"] is True and done.json()["kept_activity_record"] is True
        verbs = [row.get("verb") for row in audit_rows]
        assert verbs.count("client_export") == 1
        assert verbs.index("client_erase_requested") < verbs.index("client_erase")
    finally:
        monkeypatch.delenv("MAVERICK_CLIENT_ID")
        client_binding.reset_client_cache()


def test_erase_is_refused_without_a_named_organisation(client, monkeypatch):
    from maverick import client as client_binding
    client_binding.reset_client_cache()
    r = client.post("/api/v1/admin/system/client/erase", json={"organisation": "acme", "confirm": True})
    assert r.status_code == 409 and "nothing was erased" in r.json()["detail"]
    _as_viewer(monkeypatch)
    assert client.post("/api/v1/admin/system/client/erase",
                       json={"organisation": "acme", "confirm": True}).status_code == 403
    assert client.post("/api/v1/admin/system/client/export").status_code == 403
    assert client.get("/api/v1/admin/system/client/erase/preview").status_code == 403


def _old_conversation(user: str) -> None:
    import time as _time

    from maverick import world_model
    world = world_model.open_world()
    world.get_or_create_conversation("web", user)
    with world._writing() as conn:
        conn.execute("UPDATE conversations SET last_seen = ?", (_time.time() - 200 * 86400,))


def test_tidy_up_previews_then_removes_in_this_workspace_only(client, monkeypatch, audit_rows):
    _old_conversation("old-chat")
    preview = client.get("/api/v1/admin/system/cleanup/preview?days=90&activity_days=30").json()
    assert preview["conversations"] == 1
    assert client.get("/api/v1/admin/system/cleanup/preview?days=5&activity_days=30").status_code == 422
    assert client.post("/api/v1/admin/system/cleanup",
                       json={"days": 90, "activity_days": 30, "confirm": False}).status_code == 409
    # Another workspace sees none of this workspace's records.
    from maverick_dashboard import app as dash_app
    monkeypatch.setenv("MAVERICK_TENANT", "beta")
    dash_app._world_cache.clear()
    assert client.get("/api/v1/admin/system/cleanup/preview?days=90&activity_days=30").json()["conversations"] == 0
    monkeypatch.delenv("MAVERICK_TENANT")
    dash_app._world_cache.clear()
    done = client.post("/api/v1/admin/system/cleanup", json={"days": 90, "activity_days": 30, "confirm": True})
    assert done.status_code == 200 and done.json()["conversations"] == 1
    assert client.get("/api/v1/admin/system/cleanup/preview?days=90&activity_days=30").json()["conversations"] == 0
    rows = [row for row in audit_rows if row.get("verb") == "records_cleanup"]
    assert len(rows) == 1 and rows[0]["actor"] and rows[0]["conversations"] == 1
    assert TestClient(client.app).post("/api/v1/admin/system/cleanup",
                                       json={"days": 90, "activity_days": 30, "confirm": True}).status_code == 403
    _as_viewer(monkeypatch)
    assert client.get("/api/v1/admin/system/cleanup/preview?days=90&activity_days=30").status_code == 403
