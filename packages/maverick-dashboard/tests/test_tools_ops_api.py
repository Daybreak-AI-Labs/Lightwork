"""Skills, add-ons, templates, tool servers and connectors: the buttons that
replace ``maverick skill install`` / ``skills stats`` / ``skills evict`` /
``plugin verify|lock|reload|stats|new`` / ``template ratings-export`` /
``mcp-registry add`` / ``connectors list|simulate|plan``.

Each route: permission refusal, the kernel function doing the work, the
confirmation a destructive change needs, the activity-record row, bounded
input, and plain wording on the pages."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

ORIGIN = {"Origin": "http://testserver"}
SKILL = """---
name: weekly-status
triggers:
  - weekly status
tools_needed:
  - http_fetch
---
# Steps

1. Gather the week's finished tasks and summarise them in five bullet points.
"""


@pytest.fixture
def client(monkeypatch, tmp_path):
    from maverick import config, world_model
    from maverick.audit import writer as audit_writer
    from maverick_dashboard import app as dash_app

    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MAVERICK_HOME", str(home))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    for name in ("MAVERICK_CONFIG", "MAVERICK_DASHBOARD_TOKEN", "MAVERICK_DASHBOARD_REQUIRE_AUTH",
                 "MAVERICK_OIDC_ENABLED", "MAVERICK_PROXY_AUTH", "MAVERICK_TENANT",
                 "MAVERICK_ALLOW_SKILL_INSTALL", "MAVERICK_GOVERNED_CONNECTORS"):
        monkeypatch.delenv(name, raising=False)
    import maverick.runtime_overrides as ro
    monkeypatch.setattr(ro, "OVERRIDES_PATH", tmp_path / "runtime-overrides.toml")
    from maverick.skill import stats as skill_stats
    monkeypatch.setattr(skill_stats, "DEFAULT_PATH", tmp_path / "skill_stats.json")
    import maverick.skills as skills_mod
    monkeypatch.setattr(skills_mod, "skills_dir", lambda: tmp_path / "skills")
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


def _as(monkeypatch, role: str, who: str | None = None) -> None:
    from maverick_dashboard import auth, rbac
    who = who or f"user:{role}1"
    rbac.set_role(who, role)
    monkeypatch.setattr(auth, "caller_principal", lambda request: who)


def _audit(monkeypatch) -> list[dict]:
    from maverick.audit import writer
    seen: list[dict] = []
    real = writer.audit_event

    def spy(kind, **payload):
        seen.append(payload)
        return real(kind, **payload)
    monkeypatch.setattr(writer, "audit_event", spy)
    import maverick.audit as audit_pkg
    monkeypatch.setattr(audit_pkg, "audit_event", spy)
    return seen


def _verbs(seen) -> list[str]:
    return [p.get("verb") for p in seen if p.get("verb")]


def _plain(text: str) -> None:
    from test_support.plain_language import developer_terms
    assert developer_terms(f"<main>{text}</main>") == [], text


# ---- skills: add from a web address after a check ----------------------------

def _serve_skill(monkeypatch, text=SKILL):
    """Stand in for the network under the kernel's guarded fetch."""
    import maverick.skills as skills_mod
    fetched: list[str] = []

    def fetch(url):
        fetched.append(url)
        return text.encode()
    monkeypatch.setattr(skills_mod, "_fetch_url_bytes", fetch)
    return fetched


def test_skill_check_shows_what_it_does_without_installing(client, monkeypatch, tmp_path):
    fetched = _serve_skill(monkeypatch)
    r = client.post("/api/v1/skills/source-preview", json={"source": "gh:acme/skills:weekly/SKILL.md"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert fetched == ["https://raw.githubusercontent.com/acme/skills/main/weekly/SKILL.md"]
    assert p["name"] == "weekly-status" and p["triggers"] == ["weekly status"] and p["tools"] == ["http_fetch"]
    assert p["ok"] is True and p["problems"] == [] and len(p["fingerprint"]) == 64
    assert p["adding_allowed"] is False and p["can_add"] is False
    assert not (tmp_path / "skills").exists() or not list((tmp_path / "skills").glob("*.md"))


def test_skill_check_explains_problems_plainly_and_refuses_bad_addresses(client, monkeypatch):
    _serve_skill(monkeypatch, "---\nname: Bad Name\n---\nshort\n")
    p = client.post("/api/v1/skills/source-preview", json={"source": "https://example.com/s.md"}).json()
    assert p["ok"] is False
    assert "It does not say when agents should use it." in p["problems"]
    for line in p["problems"] + p["suggestions"]:
        _plain(line)
    assert p["technical"]  # the linter's own words stay in the details
    for bad in ("http://example.com/s.md", "/etc/passwd", "file:///etc/passwd", "gh:not a project"):
        r = client.post("/api/v1/skills/source-preview", json={"source": bad})
        assert r.status_code == 422, bad
    assert client.post("/api/v1/skills/source-preview", json={"source": "x" * 2001}).status_code == 422


def test_skill_check_refuses_internal_addresses(client, monkeypatch):
    import maverick.skills as skills_mod
    from maverick.tools._ssrf import BlockedHost

    def blocked(url):
        raise BlockedHost("169.254.169.254")
    monkeypatch.setattr(skills_mod, "_fetch_url_bytes", blocked)
    r = client.post("/api/v1/skills/source-preview", json={"source": "https://metadata.internal/s.md"})
    assert r.status_code == 422 and "inside its own network" in r.json()["detail"]


def test_skill_add_needs_admin_the_switch_and_a_confirmation(client, monkeypatch, tmp_path):
    _serve_skill(monkeypatch)
    fp = client.post("/api/v1/skills/source-preview", json={"source": "https://example.com/s.md"}).json()["fingerprint"]
    body = {"source": "https://example.com/s.md", "fingerprint": fp, "confirm": True}
    assert client.post("/api/v1/skills/source-install", json=body).status_code == 403  # switched off
    monkeypatch.setenv("MAVERICK_ALLOW_SKILL_INSTALL", "1")
    assert client.post("/api/v1/skills/source-install", json={**body, "confirm": False}).status_code == 422
    seen = _audit(monkeypatch)
    r = client.post("/api/v1/skills/source-install", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "weekly-status"
    assert (tmp_path / "skills" / "weekly-status.md").exists()
    assert "skill_install" in _verbs(seen)
    _as(monkeypatch, "operator")
    assert client.post("/api/v1/skills/source-preview", json={"source": "https://example.com/s.md"}).status_code == 403
    assert client.post("/api/v1/skills/source-install", json=body).status_code == 403


def test_skill_that_changed_after_the_check_is_not_added(client, monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_ALLOW_SKILL_INSTALL", "1")
    _serve_skill(monkeypatch)
    fp = client.post("/api/v1/skills/source-preview", json={"source": "https://example.com/s.md"}).json()["fingerprint"]
    _serve_skill(monkeypatch, SKILL.replace("five bullet points", "ten bullet points"))
    r = client.post("/api/v1/skills/source-install",
                    json={"source": "https://example.com/s.md", "fingerprint": fp, "confirm": True})
    assert r.status_code == 409 and "changed after you checked" in r.json()["detail"]
    assert not (tmp_path / "skills" / "weekly-status.md").exists()


def test_skill_add_keeps_web_address_passwords_out_of_the_activity_record(client, monkeypatch, tmp_path):
    """A private link carries its password (``user:pass@`` or ``?token=``);
    the activity record keeps where the skill came from, never that password."""
    monkeypatch.setenv("MAVERICK_ALLOW_SKILL_INSTALL", "1")
    fetched = _serve_skill(monkeypatch)
    source = ("https://alice:hunter2-secret@raw.example.com/acme/skills/SKILL.md"
              "?token=GHSAT0AAAAAAsecretvalue#frag")
    fp = client.post("/api/v1/skills/source-preview", json={"source": source}).json()["fingerprint"]
    seen = _audit(monkeypatch)
    r = client.post("/api/v1/skills/source-install",
                    json={"source": source, "fingerprint": fp, "confirm": True})
    assert r.status_code == 201, r.text
    assert fetched[-1] == source  # the fetch itself still uses the full link
    row = next(p for p in seen if p.get("verb") == "skill_install")
    assert row["source"] == "https://raw.example.com/acme/skills/SKILL.md"
    written = "".join(f.read_text(errors="replace") for f in (tmp_path / "home").rglob("*")
                      if f.is_file() and "audit" in str(f))
    assert "skill_install" in written
    for secret in ("hunter2-secret", "GHSAT0AAAAAAsecretvalue", "alice"):
        assert secret not in written


# ---- skills: track record and tidying up -------------------------------------

def _install_skill(tmp_path, name):
    from maverick.skills import build_skill_md
    folder = tmp_path / "skills"
    folder.mkdir(exist_ok=True, mode=0o700)
    (folder / f"{name}.md").write_text(build_skill_md(name, ["x"], [], "Do the thing carefully, step by step."))


def test_skill_track_record_uses_the_kernel_numbers(client, tmp_path):
    from maverick.skill import stats as skill_stats
    _install_skill(tmp_path, "helpful")
    _install_skill(tmp_path, "unused")
    skill_stats.record_use(["helpful"] * 1)
    skill_stats.record_outcome(["helpful"], success=True)
    rows = {r["name"]: r for r in client.get("/api/v1/skills/usage").json()["skills"]}
    assert rows["helpful"]["uses"] == 1 and rows["helpful"]["wins"] == 1 and rows["helpful"]["win_rate"] == 1.0
    assert rows["unused"]["uses"] == 0 and rows["unused"]["win_rate"] is None
    assert rows["helpful"] == skill_stats.usage_rows(["helpful"])[0]


def test_tidying_lists_then_removes_only_confirmed_candidates(client, monkeypatch, tmp_path):
    from maverick.skill import stats as skill_stats
    for name in ("dud", "good"):
        _install_skill(tmp_path, name)
    for _ in range(20):
        skill_stats.record_use(["dud", "good"])
        skill_stats.record_outcome(["dud"], success=False)
        skill_stats.record_outcome(["good"], success=True)
    preview = client.post("/api/v1/skills/tidy-preview", json={"min_uses": 5, "max_success": 0.2})
    assert [r["name"] for r in preview.json()["skills"]] == ["dud"]
    body = {"min_uses": 5, "max_success": 0.2, "names": ["dud", "good"]}
    assert client.post("/api/v1/skills/tidy", json=body).status_code == 422  # not confirmed
    assert (tmp_path / "skills" / "dud.md").exists()
    seen = _audit(monkeypatch)
    out = client.post("/api/v1/skills/tidy", json={**body, "confirm": True}).json()
    assert out == {"removed": ["dud"], "skipped": 1}  # "good" never matched the rule
    assert not (tmp_path / "skills" / "dud.md").exists() and (tmp_path / "skills" / "good.md").exists()
    assert "skills_evict" in _verbs(seen)
    # Only the listed choices, and a bounded list of names.
    assert client.post("/api/v1/skills/tidy-preview", json={"min_uses": 1, "max_success": 0.2}).status_code == 422
    assert client.post("/api/v1/skills/tidy", json={**body, "names": ["x"] * 201, "confirm": True}).status_code == 422
    _as(monkeypatch, "operator")
    assert client.post("/api/v1/skills/tidy-preview", json={"min_uses": 5, "max_success": 0.2}).status_code == 403
    assert client.post("/api/v1/skills/tidy", json={**body, "confirm": True}).status_code == 403


# ---- add-ons: versions, pinning, reload, usage, starter ----------------------

@pytest.fixture
def addons(monkeypatch):
    from maverick import plugin_lock
    installed = {"acme-tools": "1.0.0"}
    monkeypatch.setattr(plugin_lock, "_active_plugin_dists", lambda: dict(installed))
    monkeypatch.setattr(plugin_lock, "_dist_content_hash", lambda name: None)
    return installed


def test_versions_list_every_addon_and_pinning_needs_confirmation(client, monkeypatch, addons):
    v = client.get("/api/v1/plugins/versions").json()
    assert v["pinned"] is False and [a["state"] for a in v["addons"]] == ["unpinned"]
    _plain(v["summary"])
    assert client.post("/api/v1/plugins/versions/pin", json={}).status_code == 422
    seen = _audit(monkeypatch)
    pinned = client.post("/api/v1/plugins/versions/pin", json={"confirm": True}).json()
    assert pinned["pinned"] == 1 and pinned["ok"] is True
    assert pinned["addons"] == [{"name": "acme-tools", "approved": "1.0.0", "installed": "1.0.0",
                                 "state": "match", "label": "Matches the approved version"}]
    assert "plugin_lock" in _verbs(seen)
    addons["acme-tools"] = "2.0.0"
    addons["new-addon"] = "0.1"
    v = client.get("/api/v1/plugins/versions").json()
    assert {a["name"]: a["state"] for a in v["addons"]} == {"acme-tools": "drifted", "new-addon": "unpinned"}
    assert v["ok"] is False and "2 add-ons differ" in v["summary"]


def test_reload_is_confirmed_recorded_and_limited_to_installed_addons(client, monkeypatch, addons):
    from maverick import plugins
    calls = []
    monkeypatch.setattr(plugins, "reload_plugin", lambda name: calls.append(name) or ["acme_tools", "acme_tools.x"])
    assert client.post("/api/v1/plugins/reload", json={"addon": "acme-tools"}).status_code == 422
    assert client.post("/api/v1/plugins/reload", json={"addon": "other", "confirm": True}).status_code == 404
    seen = _audit(monkeypatch)
    r = client.post("/api/v1/plugins/reload", json={"addon": "acme-tools", "confirm": True})
    assert r.json()["reloaded_parts"] == 2 and calls == ["acme-tools"]
    assert "plugin_reload" in _verbs(seen)
    monkeypatch.setattr(plugins, "reload_plugin", lambda name: [])
    assert client.post("/api/v1/plugins/reload", json={"addon": "acme-tools", "confirm": True}).status_code == 409


def test_addon_usage_reads_the_local_tally(client, monkeypatch, tmp_path):
    from maverick import plugin_telemetry
    monkeypatch.setattr(plugin_telemetry, "telemetry_path", lambda: tmp_path / "tele.json")
    plugin_telemetry.record("forecast", "acme-tools", path=tmp_path / "tele.json")
    plugin_telemetry.record("forecast", "acme-tools", path=tmp_path / "tele.json")
    data = client.get("/api/v1/plugins/usage").json()
    assert data["counting"] is False
    assert data["tools"][0]["tool"] == "forecast" and data["tools"][0]["calls"] == 2


def test_starter_plugin_downloads_and_writes_nothing_on_the_server(client, tmp_path):
    r = client.post("/api/v1/plugins/starter", json={"name": "Weather Tools", "kind": "tool"})
    assert r.status_code == 200, r.text
    assert 'filename="weather-tools-starter.zip"' in r.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert any(n.endswith("pyproject.toml") for n in names) and all(n.startswith("weather-tools/") for n in names)
    assert client.post("/api/v1/plugins/starter", json={"name": "x", "kind": "tool"}).status_code == 422
    assert client.post("/api/v1/plugins/starter", json={"name": "ok-name", "kind": "virus"}).status_code == 422


def test_operator_is_refused_every_installation_route(client, monkeypatch, addons):
    _as(monkeypatch, "operator")
    for method, path, body in (
        ("get", "/api/v1/plugins/versions", None), ("post", "/api/v1/plugins/versions/pin", {"confirm": True}),
        ("get", "/api/v1/plugins/usage", None), ("get", "/api/v1/templates/ratings-export", None),
        ("post", "/api/v1/mcp-registry/preview", {"name": "x"}), ("get", "/api/v1/governed-connectors", None),
    ):
        r = getattr(client, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, path


# ---- templates: ratings download ----------------------------------------------

def test_ratings_download_is_the_kernel_export(client):
    from maverick.marketplace.ratings import RatingsLedger
    RatingsLedger().rate("templates", "weekly-report", 4, "nice")
    r = client.get("/api/v1/templates/ratings-export")
    assert r.status_code == 200
    assert 'filename="Lightwork-template-ratings.json"' in r.headers["content-disposition"]
    assert json.loads(r.content) == {"templates": {"weekly-report": 4}}  # comments stay local


# ---- tool servers: look before adding -------------------------------------------

def _registry(monkeypatch, spec):
    from maverick import catalog, mcp_registry
    entry = catalog.CatalogEntry(name="weather", version="1", kind="mcp", summary="Forecasts",
                                 source="", sha256="", spec=spec)
    monkeypatch.setattr(mcp_registry, "resolve_mcp", lambda name, **k: entry if name == "weather" else None)


def test_registry_preview_shows_the_exact_program_and_address(client, monkeypatch):
    _registry(monkeypatch, {"command": "/opt/mcp/weather-server", "args": ["--units", "metric"],
                            "env": {"WEATHER_KEY": "k"}, "pin_sha256": "a" * 64})
    p = client.post("/api/v1/mcp-registry/preview", json={"name": "weather"}).json()
    assert p["online"] is False and p["program"] == "/opt/mcp/weather-server"
    assert p["arguments"] == ["--units", "metric"] and p["settings"] == ["WEATHER_KEY"]
    assert p["program_checked"] is True and len(p["fingerprint"]) == 64
    _registry(monkeypatch, {"url": "https://mcp.example.com/weather"})
    p = client.post("/api/v1/mcp-registry/preview", json={"name": "weather"}).json()
    assert p["online"] is True and p["address"] == "https://mcp.example.com/weather"
    r = client.post("/api/v1/mcp-registry/preview", json={"name": "gone"})
    assert r.status_code == 409
    _plain(r.json()["detail"])


def test_registry_tools_are_asked_only_on_confirmation(client, monkeypatch):
    _registry(monkeypatch, {"url": "https://mcp.example.com/weather"})
    fp = client.post("/api/v1/mcp-registry/preview", json={"name": "weather"}).json()["fingerprint"]
    import maverick.mcp_client as mcp_client
    started = []

    class FakeClient:
        def __init__(self, spec, timeout):
            self.tools = [{"name": "forecast", "description": "Tomorrow's weather"}]

        async def start(self):
            started.append(True)

        async def stop(self):
            pass
    monkeypatch.setattr(mcp_client, "StreamableHttpMCPClient", FakeClient)
    assert client.post("/api/v1/mcp-registry/tools", json={"name": "weather", "fingerprint": fp}).status_code == 422
    assert client.post("/api/v1/mcp-registry/tools",
                       json={"name": "weather", "fingerprint": "b" * 64, "confirm": True}).status_code == 409
    assert started == []
    seen = _audit(monkeypatch)
    r = client.post("/api/v1/mcp-registry/tools", json={"name": "weather", "fingerprint": fp, "confirm": True})
    assert r.json() == {"tools": [{"name": "forecast", "description": "Tomorrow's weather"}]}
    assert started == [True] and "mcp_registry_probe" in _verbs(seen)
    # Adding refuses a server whose details changed after the look.
    r = client.post("/api/v1/mcp-registry/add", json={"name": "weather", "fingerprint": "c" * 64})
    assert r.status_code == 409
    assert client.post("/api/v1/mcp-registry/add", json={"name": "weather", "fingerprint": fp}).json() == {"added": "weather"}


# ---- governed connectors: list and preview a change ----------------------------

def _connectors_on(monkeypatch, restore=True):
    from maverick import config
    monkeypatch.setattr(config, "get_governed_connectors", lambda: {
        "enable": True, "connectors": ["salesforce"], "restore_points": restore})
    monkeypatch.delenv("SALESFORCE_INSTANCE_URL", raising=False)


def test_connectors_list_matches_the_kernel_rows(client, monkeypatch):
    assert client.get("/api/v1/governed-connectors").json()["enabled"] is False
    _connectors_on(monkeypatch)
    data = client.get("/api/v1/governed-connectors").json()
    assert data["enabled"] is True
    assert data["connectors"] == [{"name": "salesforce", "change_risk": "High", "needs_approval": True,
                                   "can_address_records": True}]


def test_change_preview_simulates_and_plans_without_changing_anything(client, monkeypatch):
    _connectors_on(monkeypatch)
    from maverick import governed_rest
    wrote = []
    monkeypatch.setattr(governed_rest.RestConnector, "write", lambda self, params: wrote.append(params) or "ok")
    body = {"connector": "salesforce", "change": "update", "record_type": "Opportunity",
            "record_id": "006000000000001AAA", "fields": [{"name": "Amount", "value": "120000", "kind": "number"}]}
    r = client.post("/api/v1/governed-connectors/preview", json=body)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["summary"] == "Lightwork would change Amount on the Opportunity record 006000000000001AAA in Salesforce."
    assert p["risk"] == "High" and p["needs_approval"] is True and p["changed_anything"] is False
    assert "would PATCH salesforce/services/data/v60.0/sobjects/Opportunity/006000000000001AAA" in p["technical"][0]["text"]
    planned = client.post("/api/v1/governed-connectors/preview", json={**body, "read_current_values": True}).json()
    assert planned["read_current_values"] is True and planned["can_undo"] is False  # no credentials here
    assert wrote == []
    # Bounded, typed input.
    for bad in ({**body, "record_id": ""}, {**body, "change": "create"}, {**body, "record_id": "../../x"},
                {**body, "fields": [{"name": "a b", "value": "1"}]}, {**body, "fields": []},
                {**body, "fields": [{"name": "Amount", "value": "lots", "kind": "number"}]},
                {**body, "fields": [{"name": f"F{i}", "value": "1"} for i in range(51)]}):
        assert client.post("/api/v1/governed-connectors/preview", json=bad).status_code == 422, bad
    assert client.post("/api/v1/governed-connectors/preview", json={**body, "connector": "servicenow"}).status_code == 404


# ---- pages ------------------------------------------------------------------

@pytest.mark.parametrize("path, words", [
    ("/skills", ("Check this skill", "How Your Skills Are Doing", "Find skills that rarely help")),
    ("/plugins", ("Approved Versions", "Pin current versions", "How Often Add-on Tools Are Used",
                  "Download starter plugin")),
    ("/mcp", ("Find more tool servers", "exactly what it starts or connects to")),
    ("/connections", ("Systems Lightwork May Change", "Preview this change")),
    ("/templates", ("Share Your Ratings", "Download ratings")),
])
def test_pages_carry_the_new_controls_in_plain_words(client, path, words):
    from test_support.plain_language import developer_terms, visible_text
    page = client.get(path)
    assert page.status_code == 200
    text = visible_text(page.text)
    for word in words:
        assert word in text, (path, word)
    html = page.text
    if path == "/templates":  # built-in templates carry their own technical details
        start = html.index('id="to-ratings"')
        html = "<main>" + html[start:html.index("</section>", start)] + "</main>"
    assert developer_terms(html) == [], path
    assert "maverick " not in text


def test_page_scripts_are_served():
    from maverick_dashboard.app import app
    c = TestClient(app)
    for name in ("lightwork-tool-ops.js", "lightwork-insight-ops.js", "lightwork-code-sandbox.js"):
        r = c.get("/static/" + name)
        assert r.status_code == 200 and "javascript" in r.headers["content-type"], name
