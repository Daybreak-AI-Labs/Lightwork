"""Q3 2026 batch 16.

  - Browser session persistence: cookies + localStorage are saved to disk
    via storage_state only for explicit per-task profiles and restored on
    the next context. Tested with mocked sessions (no real chromium).
"""
from __future__ import annotations

import maverick.tools.browser as browser_mod
from maverick.tools.browser import (
    _BrowserSession,
    _persist_enabled,
    _restore_state_arg,
    _state_path,
    browser,
)

# ---------- path / toggle helpers (pure) ----------

def test_state_path_default(monkeypatch):
    monkeypatch.delenv("MAVERICK_BROWSER_STATE", raising=False)
    p = _state_path()
    assert p.name == "state.json"
    assert p.parent.name == "browser"


def test_state_path_override(monkeypatch, tmp_path):
    target = tmp_path / "profileA.json"
    monkeypatch.setenv("MAVERICK_BROWSER_STATE", str(target))
    assert _state_path() == target


def test_persist_enabled_requires_explicit_state(monkeypatch, tmp_path):
    monkeypatch.delenv("MAVERICK_BROWSER_NO_PERSIST", raising=False)
    monkeypatch.delenv("MAVERICK_BROWSER_STATE", raising=False)
    assert _persist_enabled() is False

    monkeypatch.setenv("MAVERICK_BROWSER_STATE", str(tmp_path / "profile.json"))
    assert _persist_enabled() is True

    monkeypatch.setenv("MAVERICK_BROWSER_NO_PERSIST", "1")
    assert _persist_enabled() is False


def test_restore_arg_requires_explicit_state(monkeypatch, tmp_path):
    target = tmp_path / "nope.json"
    monkeypatch.delenv("MAVERICK_BROWSER_STATE", raising=False)
    monkeypatch.delenv("MAVERICK_BROWSER_NO_PERSIST", raising=False)
    monkeypatch.setattr(browser_mod, "_DEFAULT_STATE_PATH", target)
    target.write_text("{}")
    assert _restore_state_arg() is None  # implicit global profile is never restored

    monkeypatch.setenv("MAVERICK_BROWSER_STATE", str(target))
    assert _restore_state_arg() == str(target)
    monkeypatch.setenv("MAVERICK_BROWSER_NO_PERSIST", "1")
    assert _restore_state_arg() is None  # disabled wins even when file exists


# ---------- save_state on the session ----------

class _FakeContext:
    def __init__(self):
        self.calls = 0

    def storage_state(self):
        self.calls += 1
        # Playwright returns the state mapping when no path is supplied.
        return {"cookies": [], "origins": []}


def test_save_state_writes_file_with_secure_perms(monkeypatch, tmp_path):
    from maverick.file_lock import private_path_is_restricted
    target = tmp_path / "sub" / "state.json"
    monkeypatch.setenv("MAVERICK_BROWSER_STATE", str(target))
    monkeypatch.delenv("MAVERICK_BROWSER_NO_PERSIST", raising=False)

    sess = _BrowserSession()
    sess._context = _FakeContext()
    assert sess.save_state() is True
    assert target.exists()
    assert private_path_is_restricted(target)


def test_save_state_noop_without_context(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_BROWSER_STATE", str(tmp_path / "s.json"))
    sess = _BrowserSession()
    assert sess.save_state() is False  # no context started


def test_save_state_noop_without_explicit_state(monkeypatch):
    monkeypatch.delenv("MAVERICK_BROWSER_STATE", raising=False)
    monkeypatch.delenv("MAVERICK_BROWSER_NO_PERSIST", raising=False)
    sess = _BrowserSession()
    sess._context = _FakeContext()
    assert sess.save_state() is False


def test_save_state_noop_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_BROWSER_STATE", str(tmp_path / "s.json"))
    monkeypatch.setenv("MAVERICK_BROWSER_NO_PERSIST", "1")
    sess = _BrowserSession()
    sess._context = _FakeContext()
    assert sess.save_state() is False


# ---------- action wiring (mocked session, no playwright) ----------

class _FakePage:
    url = "about:blank"

    def goto(self, url, timeout, wait_until):
        self.url = getattr(self, "goto_url", url)


class _FakeSession:
    def __init__(self):
        self._page = _FakePage()
        self.save_calls = 0

    @property
    def page(self):
        return self._page

    def save_state(self):
        self.save_calls += 1
        return True


def test_navigate_checkpoints_session(monkeypatch):
    monkeypatch.setenv("MAVERICK_BROWSER_DISABLE", "0")
    fake = _FakeSession()
    monkeypatch.setattr(browser_mod, "_get_session", lambda: fake)
    monkeypatch.setattr(browser_mod, "gate_browser_action", lambda *_a, **_kw: None)
    out = browser_mod._run_browser_action({"action": "navigate", "url": "https://example.com"})
    assert out.startswith("navigated to https://example.com")
    assert fake.save_calls == 1


def test_save_session_action(monkeypatch):
    monkeypatch.setenv("MAVERICK_BROWSER_DISABLE", "0")
    fake = _FakeSession()
    monkeypatch.setattr(browser_mod, "_get_session", lambda: fake)
    monkeypatch.setattr(browser_mod, "gate_browser_action", lambda *_a, **_kw: None)
    out = browser_mod._run_browser_action({"action": "save_session"})
    assert out == "session saved"
    assert fake.save_calls == 1


def test_save_session_reports_when_not_saved(monkeypatch):
    monkeypatch.setenv("MAVERICK_BROWSER_DISABLE", "0")

    class _NoSaveSession(_FakeSession):
        def save_state(self):
            return False

    monkeypatch.setattr(browser_mod, "_get_session", lambda: _NoSaveSession())
    monkeypatch.setattr(browser_mod, "gate_browser_action", lambda *_a, **_kw: None)
    out = browser_mod._run_browser_action({"action": "save_session"})
    assert "not saved" in out.lower()


def test_schema_includes_save_session():
    actions = browser().input_schema["properties"]["action"]["enum"]
    assert "save_session" in actions


def test_navigate_denies_disallowed_redirect_final_host(monkeypatch):
    monkeypatch.setenv("MAVERICK_BROWSER_DISABLE", "0")
    # allowed.example.com is a non-resolvable placeholder; the SSRF pre-flight
    # now fails closed on DNS failure, so use the documented escape hatch to let
    # navigation reach the capability-based redirect check this test exercises.
    monkeypatch.setenv("MAVERICK_FETCH_ALLOW_PRIVATE", "1")
    fake = _FakeSession()
    fake.page.goto_url = "https://evil.com/private"
    monkeypatch.setattr(browser_mod, "_get_session", lambda: fake)
    monkeypatch.setattr(browser_mod, "gate_browser_action", lambda *_a, **_kw: None)
    closed = []
    monkeypatch.setattr(browser_mod, "close_browser", lambda: closed.append(True))

    out = browser_mod._run_browser_action({
        "action": "navigate",
        "url": "https://allowed.example.com/redirect",
        "_capability_allow_hosts": ("*.example.com",),
    })

    assert "DENIED by capability" in out
    assert "evil.com" in out
    assert fake.save_calls == 0
    assert closed == [True]


def test_url_less_browser_action_denies_disallowed_current_host(monkeypatch):
    monkeypatch.setenv("MAVERICK_BROWSER_DISABLE", "0")
    fake = _FakeSession()
    fake.page.url = "https://evil.com/private"
    monkeypatch.setattr(browser_mod, "_get_session", lambda: fake)
    closed = []
    monkeypatch.setattr(browser_mod, "close_browser", lambda: closed.append(True))

    out = browser_mod._run_browser_action({
        "action": "extract_text",
        "_capability_allow_hosts": ("*.example.com",),
    })

    assert "DENIED by capability" in out
    assert "evil.com" in out
    assert closed == [True]


# ---------- egress checks ----------
#
# Previously the browser tool ran outside both the enterprise egress lock
# and the per-tenant egress plane entirely -- no check at all, unlike every
# sibling outbound-request tool (http_fetch, websocket). See
# test_enterprise_tool_egress.py / test_tenant_egress.py for the
# enterprise-mode / tenant-plane integration tests; these cover the
# mechanics: the post-hoc close-on-landed-redirect path, and the
# network-level `page.route` backstop that also covers subresources a
# page's own JS fetches in the background (which never show up as
# ``page.url`` at all).

def test_deny_and_close_current_page_closes_on_enterprise_denial(monkeypatch):
    monkeypatch.setattr(
        browser_mod, "_egress_denial",
        lambda url: "enterprise mode: refusing tool egress to 'evil.com'",
    )
    closed = []
    monkeypatch.setattr(browser_mod, "close_browser", lambda: closed.append(True))

    class _Page:
        url = "https://evil.com/private"

    out = browser_mod._deny_and_close_current_page(_Page(), ())
    assert out is not None
    assert "enterprise mode" in out and "session was closed" in out
    assert closed == [True]


def test_deny_and_close_current_page_allows_when_egress_permits(monkeypatch):
    monkeypatch.setattr(browser_mod, "_egress_denial", lambda url: None)
    monkeypatch.setenv("MAVERICK_FETCH_ALLOW_PRIVATE", "1")  # isolate from real DNS
    closed = []
    monkeypatch.setattr(browser_mod, "close_browser", lambda: closed.append(True))

    class _Page:
        url = "https://ok.example.com/x"

    assert browser_mod._deny_and_close_current_page(_Page(), ()) is None
    assert closed == []


class _FakeRequest:
    def __init__(self, url):
        self.url = url


class _FakeRoute:
    def __init__(self):
        self.aborted = False
        self.continued = False

    def abort(self):
        self.aborted = True

    def continue_(self):
        self.continued = True


def test_route_guard_continues_non_http_schemes():
    route = _FakeRoute()
    browser_mod._route_guard(route, _FakeRequest("data:image/png;base64,AAAA"))
    assert route.continued and not route.aborted


def test_route_guard_aborts_loopback_subresource():
    route = _FakeRoute()
    browser_mod._route_guard(route, _FakeRequest("http://127.0.0.1/steal"))
    assert route.aborted and not route.continued


def test_route_guard_aborts_localhost_subresource():
    route = _FakeRoute()
    browser_mod._route_guard(route, _FakeRequest("http://localhost:9/steal"))
    assert route.aborted and not route.continued


def test_route_guard_allows_public_host(monkeypatch):
    monkeypatch.setattr("maverick.tools.http_fetch.is_blocked_host", lambda h: False)
    route = _FakeRoute()
    browser_mod._route_guard(route, _FakeRequest("https://cdn.example.com/img.png"))
    assert route.continued and not route.aborted


def test_route_guard_aborts_under_enterprise_denial(monkeypatch):
    monkeypatch.setattr("maverick.tools.http_fetch.is_blocked_host", lambda h: False)
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "1")
    import maverick.config as cfg
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: {})
    route = _FakeRoute()
    browser_mod._route_guard(route, _FakeRequest("https://cdn.example.com/img.png"))
    assert route.aborted and not route.continued


def test_route_guard_aborts_under_tenant_egress_deny(monkeypatch):
    monkeypatch.setattr("maverick.tools.http_fetch.is_blocked_host", lambda h: False)
    monkeypatch.delenv("MAVERICK_ENTERPRISE", raising=False)
    import maverick.config as cfg
    monkeypatch.setattr(
        cfg, "load_config",
        lambda *a, **k: {"egress": {"deny": ["cdn.example.com"]}},
    )
    route = _FakeRoute()
    browser_mod._route_guard(route, _FakeRequest("https://cdn.example.com/img.png"))
    assert route.aborted and not route.continued


def test_ensure_started_registers_route_guard_on_the_context(monkeypatch):
    """Regression guard for the registration call itself -- everything above
    tests `_route_guard`'s logic, but nothing exercises it if it's never
    wired onto a context/page."""
    import sys
    import types

    captured = {"route": None}

    class _FakeContext:
        def route(self, pattern, handler):
            captured["route"] = (pattern, handler)

        def new_page(self):
            return _FakePage()

    class _FakeBrowser:
        def new_context(self, **kwargs):
            return _FakeContext()

    class _FakeChromium:
        def launch(self, headless):
            return _FakeBrowser()

    class _FakePlaywrightHandle:
        chromium = _FakeChromium()

    class _FakeSyncPlaywright:
        def start(self):
            return _FakePlaywrightHandle()

    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.sync_playwright = lambda: _FakeSyncPlaywright()
    fake_playwright = types.ModuleType("playwright")
    fake_playwright.sync_api = fake_sync_api
    monkeypatch.setitem(sys.modules, "playwright", fake_playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)
    monkeypatch.setenv("MAVERICK_BROWSER_NO_PERSIST", "1")

    sess = _BrowserSession()
    sess._ensure_started()

    assert captured["route"] == ("**/*", browser_mod._route_guard)
