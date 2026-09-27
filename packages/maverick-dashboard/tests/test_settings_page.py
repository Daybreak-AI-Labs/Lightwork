"""Settings page: appearance form + model pin via the runtime overlay."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _client():
    from maverick_dashboard.app import app
    return TestClient(app)


def _prep(monkeypatch, tmp_path):
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    # Keep the model pin in tmp so the real ~/.maverick is never touched.
    import maverick.runtime_overrides as ro
    monkeypatch.setattr(ro, "OVERRIDES_PATH", tmp_path / "runtime-overrides.toml")
    from maverick_dashboard import app as dash_app
    dash_app._world_cache.clear()


def test_settings_page_renders(monkeypatch, tmp_path):
    _prep(monkeypatch, tmp_path)
    r = _client().get("/settings")
    assert r.status_code == 200
    assert "Appearance" in r.text
    assert "Models" in r.text
    assert 'action="/settings/models"' in r.text
    # nav link is wired
    assert 'href="/settings"' in r.text


def test_settings_has_bounded_sections_keyboard_tabs_and_search(monkeypatch, tmp_path):
    _prep(monkeypatch, tmp_path)
    text = _client().get("/settings").text

    assert 'id="settings-search"' in text
    assert 'role="tablist"' in text
    assert text.count('role="tab" data-settings-tab=') == 6
    for group in ("general", "models", "integrations", "controls", "access", "maintenance"):
        assert f'data-settings-tab="{group}"' in text
        assert f'data-settings-group="{group}"' in text
    assert "ArrowRight" in text and "ArrowLeft" in text
    assert "applySearch" in text
    assert "data-settings-search-item" in text


def test_hidden_attribute_beats_author_display_rules():
    """The tab filter sets ``hidden`` on every off-tab ``.section-head``.

    Shared classes (``.section-head`` is ``display: flex``, ``.btn`` is
    ``inline-flex``) beat the UA ``[hidden]`` rule, so without a reset the
    eight off-tab headings rendered as a wall of orphaned titles under the
    General tab, and the Ekko page showed all four hidden control buttons.
    The shared stylesheet must therefore carry a global ``[hidden]`` reset
    that wins over any author-level display.
    """
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "maverick_dashboard"
           / "static" / "lightwork.css").read_text(encoding="utf-8")
    assert re.search(r"\.section-head\s*\{[^}]*display:\s*flex", css)
    rule = re.search(r"(?<![\w.#-])\[hidden\]\s*\{([^}]*)\}", css)
    assert rule is not None, "global [hidden] reset missing from lightwork.css"
    assert re.search(r"display:\s*none\s*!important", rule.group(1))


def test_light_theme_flips_the_root_color_scheme():
    """The theme class is on <body>, but the UA canvas, scrollbars and native
    controls follow color-scheme on the root, so Light must switch it there too."""
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "maverick_dashboard"
           / "static" / "lightwork.css").read_text(encoding="utf-8")
    rule = re.search(r":root:has\(body\.theme-light\)\s*\{([^}]*)\}", css)
    assert rule is not None
    assert re.search(r"color-scheme:\s*light", rule.group(1))


def test_settings_returns_to_section_that_was_saved(monkeypatch, tmp_path):
    _prep(monkeypatch, tmp_path)
    text = _client().get("/settings", params={"saved": "providers"}).text
    assert 'data-initial-group="integrations"' in text


def test_set_and_clear_default_model(monkeypatch, tmp_path):
    _prep(monkeypatch, tmp_path)
    monkeypatch.delenv("MAVERICK_MODEL_OVERRIDE", raising=False)
    c = _client()
    hdr = {"origin": "http://testserver"}
    r = c.post("/settings/models", data={"model": "claude-opus-4-8"},
               headers=hdr, follow_redirects=False)
    assert r.status_code == 303

    from maverick.runtime_overrides import default_model_override
    assert default_model_override() == "claude-opus-4-8"
    # The pin flows through model resolution (no per-role config in this test).
    from maverick.llm import model_for_role
    assert model_for_role("writer") == "claude-opus-4-8"

    # Clearing reverts to defaults.
    r = c.post("/settings/models", data={"model": ""}, headers=hdr,
               follow_redirects=False)
    assert r.status_code == 303
    assert default_model_override() is None


def test_set_model_rejects_junk(monkeypatch, tmp_path):
    _prep(monkeypatch, tmp_path)
    r = _client().post("/settings/models", data={"model": "bad model!!"},
                       headers={"origin": "http://testserver"},
                       follow_redirects=False)
    assert r.status_code == 400


def test_set_model_blocks_cross_origin(monkeypatch, tmp_path):
    _prep(monkeypatch, tmp_path)
    r = _client().post("/settings/models", data={"model": "claude-opus-4-8"},
                       headers={"origin": "http://evil.example"},
                       follow_redirects=False)
    assert r.status_code == 403


def test_denied_tools_preserved_when_pinning_model(monkeypatch, tmp_path):
    """Writing a model must not wipe the [security] tool-deny overlay."""
    _prep(monkeypatch, tmp_path)
    from maverick import runtime_overrides as ro
    ro.disable_tool("browser")
    ro.set_default_model("claude-sonnet-4-6")
    assert "browser" in ro.denied_tools()
    assert ro.default_model_override() == "claude-sonnet-4-6"
    # and clearing the model leaves the tool deny intact
    ro.clear_default_model()
    assert "browser" in ro.denied_tools()
    assert ro.default_model_override() is None
