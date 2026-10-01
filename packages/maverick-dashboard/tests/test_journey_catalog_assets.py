"""Shared public catalogs stay cacheable without bloating every page response."""
from __future__ import annotations

import hashlib
import json
import re

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard import i18n, i18n_journeys
from maverick_dashboard.app import app


@pytest.mark.parametrize("language", i18n.LANGS)
def test_catalog_asset_preserves_every_translation_and_current_revision(language):
    revision, content = i18n_journeys.browser_catalog_asset(language)
    assert hashlib.sha256(content).hexdigest() == revision
    url = f"/static/lightwork-i18n-catalog/{language}/{revision}.js"
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        response = client.get(url)
        assert response.status_code == 200 and response.content == content
        assert "immutable" in response.headers["cache-control"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "set-cookie" not in response.headers
        cached = client.get(url, headers={"If-None-Match": response.headers["etag"]})
        assert cached.status_code == 304 and cached.content == b""
        assert client.get(url.replace(revision, "0" * 64)).status_code == 404
    payload = json.loads(content.decode().removeprefix("window.LightworkJourneyCatalog=").removesuffix(";\n"))
    assert payload == {"language": language, "catalog": i18n_journeys.browser_catalog(language)}
    for source, *_ in i18n_journeys.ROWS:
        text, actual = i18n_journeys.entry(source, language)
        assert payload["catalog"][source] == {"text": text, "lang": actual}


@pytest.mark.parametrize("language", i18n.LANGS)
def test_settings_initial_page_stays_bounded_in_every_supported_language(language):
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        response = client.get("/settings/all", params={"lang": language})
    assert response.status_code == 200 and len(response.content) < 300_000
    metadata = re.search(r'id="lightwork-i18n-data">(.*?)</script>', response.text, re.DOTALL)
    assert metadata is not None
    bootstrap = json.loads(metadata.group(1))
    assert bootstrap["language"] == language and "catalog" not in bootstrap
    expected_revision, _ = i18n_journeys.browser_catalog_asset(language)
    asset = f"/static/lightwork-i18n-catalog/{language}/{expected_revision}.js"
    assert response.text.index(asset) < response.text.index('/static/lightwork-i18n.js')
    assert 'data-setting-id="' not in response.text


def test_catalog_rejects_unknown_languages_without_reflecting_request_content():
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        response = client.get("/static/lightwork-i18n-catalog/private-employee/" + "0" * 64 + ".js")
    assert response.status_code == 404 and "private-employee" not in response.text
