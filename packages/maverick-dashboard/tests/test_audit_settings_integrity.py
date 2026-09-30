"""Settings review and file-install regression cases; do not use live providers."""
import pytest
from fastapi.testclient import TestClient

from maverick_dashboard.app import app
from maverick_dashboard import settings_all_page as page

client = TestClient(app, headers={"Origin": "http://testserver"})
API = "/api/v1/settings/catalog"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from maverick import config, world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    for key in ("MAVERICK_DASHBOARD_TOKEN", "MAVERICK_CONFIG", "MAVERICK_CONFIG_OVERLAY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(page, "_record", lambda *args, **kwargs: None)
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def test_catalog_stale_version_does_not_overwrite_newer_save():
    before = client.get(API).json()["revision"]
    body = {"expected_revision": before, "changes": [{"id": "persona.name", "value": "Atlas"}]}
    saved = client.post(API, json=body)
    assert saved.status_code == 200
    refused = client.post(API, json={**body, "changes": [{"id": "persona.name", "value": "Stale"}]})
    assert refused.status_code == 409
    assert refused.json()["conflicts"][0]["current"] == "Atlas"
    assert page._sources()["here"]["persona"]["name"] == "Atlas"


def test_inherit_is_distinct_from_product_default(tmp_path):
    from maverick.config import config_path
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[persona]\nname = "Installer"\n', encoding="utf-8")
    for change in ({"id": "persona.name", "value": "Override"},
                   {"id": "persona.name", "inherit": True}):
        revision = client.get(API).json()["revision"]
        assert client.post(API, json={"expected_revision": revision, "changes": [change]}).status_code == 200
    assert "name" not in page._sources()["here"].get("persona", {})
    revision = client.get(API).json()["revision"]
    assert client.post(API, json={"expected_revision": revision,
                      "changes": [{"id": "persona.name", "reset": True}]}).status_code == 200
    assert page._sources()["here"]["persona"]["name"] == ""


def test_file_review_rejects_different_bytes_and_changed_configuration(monkeypatch):
    from maverick_dashboard import system_checks_api
    monkeypatch.setattr(system_checks_api, "_record", lambda *args, **kwargs: None)
    checked = client.post('/api/v1/setup/settings-file/check', content=b'[persona]\nname="A"\n')
    assert checked.status_code == 200
    review = checked.json()["review"]
    changed = client.post('/api/v1/setup/settings-file/install?confirm=true&review=' + review,
                          content=b'[persona]\nname="B"\n')
    assert changed.status_code == 409
    revision = client.get(API).json()["revision"]
    client.post(API, json={"expected_revision": revision,
                          "changes": [{"id": "persona.name", "value": "Later"}]})
    stale = client.post('/api/v1/setup/settings-file/install?confirm=true&review=' + review,
                        content=b'[persona]\nname="A"\n')
    assert stale.status_code == 409


def test_review_does_not_write_and_export_excludes_credentials():
    revision = client.get(API).json()['revision']
    reviewed = client.post(API + '/review', json={
        'expected_revision': revision, 'changes': [{'id': 'persona.name', 'value': 'Atlas'}]})
    assert reviewed.status_code == 200
    assert reviewed.json()['items'][0]['after'] == 'Atlas'
    assert page._revision() == revision
    exported = client.post('/api/v1/settings/profiles/export', json={'ids': ['persona.name', 'dashboard.token']})
    assert exported.status_code == 200
    assert 'dashboard.token' not in exported.json()['settings']
    imported = client.post('/api/v1/settings/profiles/import', json={'profile': {
        'format': 'lightwork-preferences-profile', 'version': 1, 'scope': 'organization',
        'settings': {'dashboard.token': 'must-not-import'}}})
    assert imported.status_code == 422
    assert page._revision() == revision


def test_display_cookie_cannot_cross_account_or_override_locked_policy(monkeypatch):
    from types import SimpleNamespace
    from maverick_dashboard import preferences_api

    monkeypatch.setattr(preferences_api.auth, 'caller_principal', lambda request: 'user:bob')
    monkeypatch.setattr(preferences_api, 'context_for', lambda request: 'bob-context')
    monkeypatch.setattr(preferences_api.preferences, 'enabled', lambda: True)
    monkeypatch.setattr(preferences_api.preferences, 'state', lambda principal: {
        'locked': ['theme'], 'values': {'theme': 'dark', 'density': 'comfortable'}})
    request = SimpleNamespace(state=SimpleNamespace(), cookies={
        'mvk_pref_context': 'alice-context', 'mvk_density': 'compact'})
    assert preferences_api.resolved_display(request, 'density') == 'comfortable'
    request.cookies = {'mvk_pref_context': 'bob-context', 'mvk_theme': 'light'}
    assert preferences_api.resolved_display(request, 'theme') == 'dark'


def test_every_runtime_writing_style_round_trips_catalog():
    from maverick.persona import STYLES, load_persona
    from maverick_dashboard.settings_catalog import BY_ID

    assert {key for key, _label in BY_ID['persona.style'].choices if key} == set(STYLES)
    for style in STYLES:
        revision = client.get(API).json()['revision']
        result = client.post(API, json={'expected_revision': revision,
                             'changes': [{'id': 'persona.style', 'value': style}]})
        assert result.status_code == 200
        assert load_persona()['style'] == style


def test_supported_profile_round_trip_uses_ordinary_conflict_checked_writer():
    original = client.post('/api/v1/settings/profiles/export', json={'ids': ['persona.name']}).json()
    revision = client.get(API).json()['revision']
    assert client.post(API, json={'expected_revision': revision,
                       'changes': [{'id': 'persona.name', 'value': 'Temporary'}]}).status_code == 200
    imported = client.post('/api/v1/settings/profiles/import', json={'profile': original}).json()
    reviewed = client.post(API + '/review', json={'expected_revision': imported['revision'],
                            'changes': imported['changes']})
    assert reviewed.status_code == 200
    assert client.post(API, json={'expected_revision': imported['revision'],
                       'changes': imported['changes']}).status_code == 200
    after = client.post('/api/v1/settings/profiles/export', json={'ids': ['persona.name']}).json()
    assert after['settings'] == original['settings']
