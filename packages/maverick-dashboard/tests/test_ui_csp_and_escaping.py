"""UI hardening (csp_and_escaping): CSP + security headers, plan-tree escaping, trajectory a11y."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

TEMPLATES = Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates"


def _client():
    from maverick_dashboard.app import app
    return TestClient(app)


# ---------- Content-Security-Policy ----------

def test_csp_header_present_and_locked_down(monkeypatch, tmp_path):
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick_dashboard import app as dash_app
    dash_app._world_cache.clear()
    csp = _client().get("/").headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    # The exfil + clickjack + injection backstops:
    assert "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'none'" in csp
    # Dashboard pages stay self-only.
    assert "http://" not in csp and "https://" not in csp


def test_docs_csp_allows_fastapi_cdn_assets(monkeypatch):
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    docs = _client().get("/docs")
    docs_csp = docs.headers.get("Content-Security-Policy", "")
    redoc_csp = _client().get("/redoc").headers.get("Content-Security-Policy", "")
    # The API explorer ships its own assets, so it stays self-only.
    assert "/static/swagger-ui-" in docs.text and "cdn.jsdelivr.net" not in docs.text
    assert "https://" not in docs_csp
    assert "https://cdn.jsdelivr.net" in redoc_csp
    assert "connect-src 'self'" in docs_csp and "connect-src 'self'" in redoc_csp


def test_csp_on_api_responses_too(monkeypatch, tmp_path):
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    r = _client().get("/livez")
    assert "Content-Security-Policy" in r.headers


def test_all_baseline_headers_still_present(monkeypatch, tmp_path):
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick_dashboard import app as dash_app
    dash_app._world_cache.clear()
    h = _client().get("/").headers
    assert h["X-Frame-Options"] == "DENY"
    assert h["X-Content-Type-Options"] == "nosniff"
    # same-origin (not no-referrer): no-referrer makes browsers send
    # "Origin: null" on same-origin form POSTs, which the CSRF guard rejects.
    assert h["Referrer-Policy"] == "same-origin"
    assert h["Cross-Origin-Opener-Policy"] == "same-origin"


# ---------- plan-tree escaping ----------

def test_plan_tree_escapes_quotes_in_title(monkeypatch, tmp_path):
    """A goal title with a quote + angle bracket must be escaped in the
    pre-rendered HTML (quote=True closes the attribute-context hole)."""
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    wm = world_model.WorldModel(tmp_path / "world.db")
    gid = wm.create_goal('"><script>alert(1)</script>', "x")
    from maverick_dashboard import app as dash_app
    dash_app._world_cache.clear()
    r = _client().get(f"/goals/{gid}/plan")
    assert r.status_code == 200
    # The raw script tag must NOT appear unescaped.
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_render_tree_html_quote_escaping_unit():
    from maverick_dashboard.app import _render_tree_html
    node = {
        "id": 1, "parent_id": None, "status": 'x" onmouseover="alert(1)',
        "title": "t", "dollars": 0, "children": [],
    }
    html = _render_tree_html(node)
    # The double-quote in status must be entity-encoded so it can't break
    # out of class="badge ...".
    assert 'onmouseover="alert(1)"' not in html
    assert "&quot;" in html


# ---------- trajectory a11y ----------

def test_trajectory_controls_have_aria_labels(monkeypatch, tmp_path):
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    wm = world_model.WorldModel(tmp_path / "world.db")
    gid = wm.create_goal("g", "x")
    wm.append_event(gid, "agent", "plan", "first")
    from maverick_dashboard import app as dash_app
    dash_app._world_cache.clear()
    r = _client().get(f"/goals/{gid}/trajectory")
    assert r.status_code == 200
    # Playback buttons carry visible words, and the slider is named.
    for label in (">Start</button>", ">Back</button>", ">Next</button>", ">End</button>",
                  'aria-label="Move through the steps"'):
        assert label in r.text
    # Keyboard handler wired.
    assert "ArrowLeft" in r.text and "ArrowRight" in r.text


# ---------- no inline event-handler attributes (script-src carries no unsafe-inline) ----------

def test_no_template_uses_an_inline_event_handler_attribute():
    """An ``onclick="..."`` attribute is an inline script: a script-src nonce
    does not admit it, so each handler is an addEventListener in a script."""
    handlers = re.compile(r"\s(on[a-z]+)\s*=\s*[\"']", re.IGNORECASE)
    found = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in handlers.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            found.append(f"{path.relative_to(TEMPLATES)}:{line} {match.group(1)}=")
    assert found == []


# ---------- every inline template script carries the response nonce ----------

def test_every_inline_template_script_carries_the_response_nonce():
    """A nonce-based script-src runs only the scripts that carry this response's
    nonce, so a bare inline <script> would be blocked. JSON data blocks are
    stamped too, so the rule has no exceptions to maintain."""
    opener = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)
    stamp = 'nonce="{{ csp_script_nonce }}"'
    found = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in opener.finditer(text):
            if re.search(r"\bsrc\s*=", match.group(1)) or stamp in match.group(1):
                continue
            line = text.count("\n", 0, match.start()) + 1
            found.append(f"{path.relative_to(TEMPLATES)}:{line}")
    assert found == []


def test_macros_that_stamp_the_nonce_are_imported_with_context():
    """An imported macro sees only globals, not the page's context, so a macro
    that stamps csp_script_nonce must be imported ``with context``."""
    stamping = {
        path.name for path in TEMPLATES.rglob("*.html")
        if "csp_script_nonce" in path.read_text(encoding="utf-8")
    }
    importer = re.compile(r"{%-?\s*(?:from|import)\s+\"([^\"]+)\"[^%]*%}")
    found = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in importer.finditer(text):
            if match.group(1) in stamping and "with context" not in match.group(0):
                found.append(f"{path.relative_to(TEMPLATES)} imports {match.group(1)}")
    assert found == []
