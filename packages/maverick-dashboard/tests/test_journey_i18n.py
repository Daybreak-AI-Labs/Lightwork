"""Partial catalogs must never mislabel English or alter source/user content."""
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
from maverick_dashboard import auth, i18n, i18n_journeys, preferences_api


def test_selected_journey_bindings_have_actual_catalog_entries():
    report = runpy.run_path(str(Path(__file__).resolve().parents[3] / "scripts" / "journey_i18n_coverage.py"))["report"]()
    assert report["catalog_sources"] == len(i18n_journeys.messages()), "An inventoried catalog is not wired into the product"
    missing = {name: page["bound_sources_missing_catalog"] for name, page in report["pages"].items()
               if page["bound_sources_missing_catalog"]}
    assert missing == {}, f"Bound journey controls still fall back to English: {missing}"


def request(*, query="", accept="", cookies=None):
    return SimpleNamespace(query_params={"lang": query}, headers={"Accept-Language": accept},
                           cookies=cookies or {}, state=SimpleNamespace())


def test_quality_and_region_match_actual_preference(monkeypatch):
    monkeypatch.setattr(auth, "caller_principal", lambda request: None)
    monkeypatch.setattr(preferences_api, "resolved_display", lambda *args: None)
    req = request(accept="ja;q=0,fr-CA;q=0.4,de-DE;q=0.9,zh;q=bogus")
    assert i18n.resolve_lang(req) == "de"
    assert i18n_journeys.locale_for(req, "de") == "de-DE"
    assert i18n.resolve_lang(request(query="fr-CA")) == "fr"
    assert i18n_journeys.locale_for(request(query="fr-CA"), "fr") == "fr-CA"


def test_account_interface_language_is_independent_and_query_cannot_override(monkeypatch):
    monkeypatch.setattr(auth, "caller_principal", lambda request: "user:alice")
    monkeypatch.setattr(preferences_api, "resolved_display", lambda request, key: {"language": "ja", "interface_language": "fr"}[key])
    assert i18n.resolve_lang(request(query="de")) == "fr"
    assert i18n_journeys.locale_for(request(query="fr-CA", accept="fr-FR"), "fr") == "fr-FR"
    monkeypatch.setattr(preferences_api, "resolved_display", lambda request, key: {"language": "ja", "interface_language": ""}[key])
    assert i18n.resolve_lang(request()) == "ja"


def test_arabic_seed_remains_partial_and_draft_regions_are_honest():
    assert frozenset({"en"}) == i18n.CONTENT_LANGS
    assert all("ar" not in row for key, row in i18n.MESSAGES.items() if key.startswith("journey."))
    assert 'lang="en"' in str(i18n_journeys.region("Save private draft", "ar"))
    assert 'lang="fr"' in str(i18n_journeys.region("Save private draft", "fr"))
    assert 'lang="en"' in str(i18n_journeys.region("Untranslated product text", "fr"))


def test_interpolation_cannot_add_html_or_change_user_text():
    value = '<img src=x onerror=alert(1)>مرحبا'
    result = str(i18n_journeys.region("Showing {start}–{end} of {total}", "fr", start=value, end=2, total=3))
    assert '<img' not in result
    assert '&lt;img src=x onerror=alert(1)&gt;مرحبا' in result
    assert '<bdi lang="und" dir="auto">' in result
    with pytest.raises(KeyError):
        i18n_journeys.region("Showing {start}–{end} of {total}", "fr", start=1)


def test_display_language_legacy_key_does_not_change_response_preferences(monkeypatch):
    saved = []
    monkeypatch.setattr(preferences_api, "_app", lambda: SimpleNamespace(_require_same_origin=lambda request: None))
    monkeypatch.setattr(auth, "require_permission", lambda *args: None)
    monkeypatch.setattr(auth, "caller_principal", lambda request: "user:alice")
    monkeypatch.setattr(preferences_api, "_check_display", lambda *args: None)
    monkeypatch.setattr(preferences_api, "context_for", lambda request: "ctx")
    monkeypatch.setattr(preferences_api.preferences, "enabled", lambda: True)
    monkeypatch.setattr(preferences_api.preferences, "state", lambda principal: {"locked": []})
    monkeypatch.setattr(preferences_api.preferences, "save", lambda *args, **kwargs: saved.append(kwargs) or {"revision": "next"})
    preferences_api.display_preference(request(), preferences_api.DisplayUpdate(key="language", value="fr", context="ctx", expected_revision="old"))
    assert saved[0]["changes"] == {"interface_language": "fr"}


def test_browser_override_stays_bound_to_account_and_policy(monkeypatch):
    monkeypatch.setattr(auth, "caller_principal", lambda request: "user:alice")
    monkeypatch.setattr(preferences_api, "context_for", lambda request: "alice-workspace")
    monkeypatch.setattr(preferences_api.preferences, "enabled", lambda: True)
    monkeypatch.setattr(preferences_api.preferences, "state", lambda principal: {"locked": [], "values": {"interface_language": "de"}})
    assert preferences_api.resolved_display(request(cookies={"mvk_lang": "fr", "mvk_pref_context": "bob-workspace"}), "interface_language") == "de"
    assert preferences_api.resolved_display(request(cookies={"mvk_lang": "fr", "mvk_pref_context": "alice-workspace"}), "interface_language") == "fr"
    monkeypatch.setattr(preferences_api.preferences, "state", lambda principal: {"locked": ["interface_language"], "values": {"interface_language": "ja"}})
    assert preferences_api.resolved_display(request(cookies={"mvk_lang": "fr", "mvk_pref_context": "alice-workspace"}), "interface_language") == "ja"


def test_display_options_are_text_and_custom_theme_names_stay_verbatim(monkeypatch):
    from html.parser import HTMLParser

    from maverick_dashboard import app

    monkeypatch.setattr(auth, "caller_principal", lambda request: None)
    context = i18n_journeys.context(request(accept="fr-CA"), "fr")
    rendered = app.templates.env.get_template('_display_preferences.html').render(
        **context, lang="fr", page_lang="en", t=lambda key: i18n.t(key, "fr"),
        display_signed_in=False, display_interface_language="fr", display_text_size="standard",
        theme="Save", font="default", density="comfortable", custom_theme_names=["Save"])

    class Options(HTMLParser):
        def __init__(self):
            super().__init__()
            self.active = False
            self.nested = []

        def handle_starttag(self, tag, attrs):
            if self.active:
                self.nested.append(tag)
            if tag == "option":
                self.active = True

        def handle_endtag(self, tag):
            if tag == "option":
                self.active = False

    parsed = Options()
    parsed.feed(rendered)
    assert not parsed.nested
    assert '<option value="Save" lang="und" dir="auto" data-user-content selected>Save</option>' in rendered
    assert 'lang="fr">Sombre</option>' in rendered
