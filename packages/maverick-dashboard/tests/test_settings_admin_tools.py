"""Settings in plain language: readable model names and setting sources, a
button that makes the webhook secret, a switch for automatic model lists, the
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
    for label in ("Test connection", "Make a secret", "Check readiness", "Run health check",
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


def test_generate_webhook_secret_saves_and_shows_once(client):
    r = client.post("/settings/webhooks/generate")
    assert r.status_code == 200
    secret = r.json()["secret"]
    assert len(secret) >= 32
    assert r.headers.get("cache-control") == "no-store"
    from maverick.webhooks import inbound_secret
    assert inbound_secret() == secret
    page = client.get("/settings").text
    assert secret not in page and "Make a new secret" in page


def test_generate_webhook_secret_is_admin_only_and_same_origin(client, monkeypatch):
    assert TestClient(client.app).post("/settings/webhooks/generate").status_code == 403
    _as_viewer(monkeypatch)
    assert client.post("/settings/webhooks/generate").status_code == 403


def test_generate_webhook_secret_refused_when_set_on_the_computer(client, monkeypatch):
    monkeypatch.setenv("MAVERICK_WEBHOOK_SECRET", "env-secret")  # pragma: allowlist secret
    assert client.post("/settings/webhooks/generate").status_code == 409
    text = visible_text(client.get("/settings").text)
    assert "set on the computer running Lightwork" in text and "Make a secret" not in text


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
