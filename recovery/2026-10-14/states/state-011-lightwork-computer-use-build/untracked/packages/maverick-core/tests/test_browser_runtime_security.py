"""Production browser-runtime isolation, policy and observation contracts."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import threading

import pytest
from maverick.audit import goal_context
from maverick.connections import bind_principal
from maverick.file_lock import ensure_private_directory
from maverick.paths import reset_tenant, set_tenant
from maverick.tools import browser


def test_safe_url_removes_credentials_query_and_fragment():
    assert browser._safe_url(
        "https://alice:secret@example.com:8443/path?q=token#fragment"
    ) == "https://example.com:8443/path"
    assert browser._safe_origin(
        "https://alice:secret@example.com:8443/path?q=token"
    ) == "https://example.com:8443"


def test_native_browser_toolset_is_not_overclaimed():
    readiness = browser.native_browser_toolset_readiness()
    assert readiness["type"] == "browser_toolset_20260801"
    assert readiness["supported"] is False
    assert readiness["advertise"] is False
    assert readiness["can_coexist_with_custom_browser"] is False
    assert len(readiness["catalog_members"]) == 31
    assert readiness["implemented_native_members"] == []
    assert "browser" in readiness["custom_fallback"]


def test_session_key_binds_tenant_principal_goal_hosts_and_device(monkeypatch):
    monkeypatch.setenv("MAVERICK_BROWSER_DEVICE", "iphone-15")
    tenant_token = set_tenant("tenant-a")
    try:
        with bind_principal("user:alice"), goal_context(42):
            key = browser._session_key({
                "_capability_allow_hosts": ["EXAMPLE.COM", "*.example.com"],
            })
    finally:
        reset_tenant(tenant_token)
    assert key.tenant == "tenant-a"
    assert key.principal == "user:alice"
    assert key.goal == "42"
    assert key.capability_hosts == ("*.example.com", "example.com")
    assert key.device == "iphone-15"
    assert key.opaque_id.startswith("bs_")


def test_goal_less_runtime_requires_explicit_nonshared_scope(monkeypatch):
    monkeypatch.delenv("MAVERICK_BROWSER_SESSION_ID", raising=False)
    with goal_context(None):
        with pytest.raises(RuntimeError, match="requires goal context"):
            browser._session_key({})
        # Private-looking call arguments are still model-controlled.  They
        # cannot select an old cookie jar when no goal is bound.
        with pytest.raises(RuntimeError, match="requires goal context"):
            browser._session_key({"_runtime_session_id": "guessed_session_123"})
    monkeypatch.setenv("MAVERICK_BROWSER_SESSION_ID", "local_session_123")
    with goal_context(None):
        assert browser._session_key({}).goal == "session:local_session_123"


def test_session_serializes_actions_on_one_dedicated_thread():
    session = browser._BrowserSession()
    entered: list[int] = []
    active = 0
    max_active = 0
    state_lock = threading.Lock()

    def operation():
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        entered.append(threading.get_ident())
        with state_lock:
            active -= 1
        return "ok"

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(lambda _index: session.execute(operation), range(20))) == ["ok"] * 20
    finally:
        session.close()
    assert len(set(entered)) == 1
    assert max_active == 1


def test_request_graph_policy_blocks_private_disallowed_and_unsafe_schemes(monkeypatch):
    monkeypatch.setattr(browser, "_is_safe_browser_url", lambda url: "private" not in url)
    assert browser._request_url_denial("https://private.example/x", ()) is not None
    assert browser._request_url_denial("file:///etc/passwd", ()) is not None
    assert browser._request_url_denial(
        "https://evil.example/x", ("allowed.example",),
    ) is not None
    assert browser._request_url_denial(
        "https://allowed.example/x", ("allowed.example",),
    ) is None
    assert browser._request_url_denial("data:text/plain,ok", ()) is None


def test_top_level_data_navigation_is_denied(monkeypatch):
    page = _Page()
    page.url = "data:text/html,untrusted"
    monkeypatch.setattr(browser, "close_browser", lambda *_args: None)
    denial = browser._deny_and_close_current_page(page, (), None)
    assert denial is not None and "top-level" in denial


def test_popup_top_level_data_navigation_is_closed_immediately():
    class Popup:
        url = "data:text/html,untrusted"

        def __init__(self):
            self.main_frame = object()
            self.closed = False

        def close(self):
            self.closed = True

    popup = Popup()
    session = browser._BrowserSession()
    try:
        session._handle_frame_navigated(popup, popup.main_frame)
        assert popup.closed is True
        assert session._network_denial is not None
        assert "top-level" in session._network_denial
    finally:
        session.close()


class _AX:
    def snapshot(self):
        return {
            "role": "WebArea",
            "name": "Sign in",
            "children": [{
                "role": "textbox", "name": "Password", "value": "hunter2",
            }],
        }


class _Element:
    def __init__(self, *, attrs=None, text=""):
        self.attrs = attrs or {}
        self.text = text

    def get_attribute(self, name):
        return self.attrs.get(name)

    def inner_text(self):
        return self.text

    def evaluate(self, _expression):
        return "INPUT"


class _Page:
    url = "https://alice:secret@example.com/login?token=secret#fragment"
    accessibility = _AX()
    viewport_size = {"width": 1280, "height": 800}

    def __init__(self):
        self.image = b"frame"
        self.element = _Element(
            attrs={"type": "password", "value": "hunter2", "name": "password"},
        )

    def title(self):
        return "Sign in"

    def query_selector_all(self, _selector):
        return [self.element]

    def query_selector(self, _selector):
        return self.element

    def screenshot(self, **_kwargs):
        return self.image


def test_observation_removes_password_values_and_url_secrets():
    payload = json.loads(browser._browser_observe(_Page()))
    assert payload["url"] == "https://example.com/login"
    assert "hunter2" not in json.dumps(payload)
    ax = json.loads(payload["accessibility_tree"])
    assert "value" not in ax["children"][0]


def test_observation_redacts_remote_pii_from_semantic_metadata():
    page = _Page()
    page.title = lambda: "Account alice@example.com"  # type: ignore[method-assign]
    page.accessibility = _AX()
    page.accessibility.snapshot = lambda: {  # type: ignore[method-assign]
        "role": "WebArea", "name": "alice@example.com",
    }
    page.element = _Element(
        attrs={"name": "alice@example.com", "placeholder": "alice@example.com"},
    )
    payload = browser._browser_observe(page)
    assert "alice@example.com" not in payload


def test_element_reference_is_bound_to_session_page_url_and_frame():
    page = _Page()
    session = browser._BrowserSession()
    session.session_id = "cus_reference_session_123"
    session._tab_id = lambda _page: "tab_reference"  # type: ignore[method-assign]
    digest = hashlib.sha256(page.image).hexdigest()
    ref = session.bind_element_ref(
        page, index=1, role="input", name="Password", selector="#password",
        observation_sha256=digest,
    )
    try:
        assert session.element_ref_metadata(page, ref)["page_id"] == "tab_reference"
        page.image = b"changed"
        with pytest.raises(ValueError, match="stale"):
            session.resolve_element_ref(page, ref)
        page.url = "https://example.com/other"
        assert session.element_ref_metadata(page, ref) is None
    finally:
        session.close()


def test_device_preset_is_consumed_by_context_options(monkeypatch):
    monkeypatch.delenv("MAVERICK_BROWSER_STATE", raising=False)
    monkeypatch.delenv("MAVERICK_BROWSER_PERSIST", raising=False)
    session = browser._BrowserSession()
    session.key = browser._BrowserSessionKey(
        tenant="__local__", principal="local", goal="1",
        capability_hosts=(), device="iphone-15",
    )
    try:
        options = session._context_options()
    finally:
        session.close()
    assert options["viewport"] == {"width": 393, "height": 852}
    assert options["is_mobile"] is True
    assert options["has_touch"] is True


def test_upload_boundary_rejects_paths_and_accepts_private_staged_file(monkeypatch, tmp_path):
    upload_root = ensure_private_directory(tmp_path / "uploads")
    staged = upload_root / "report.txt"
    staged.write_text("safe", encoding="utf-8")
    monkeypatch.setenv("MAVERICK_BROWSER_UPLOAD_DIR", str(upload_root))
    assert browser._upload_path("report.txt") == staged.resolve()
    with pytest.raises(ValueError, match="not a path"):
        browser._upload_path("../report.txt")

    prepared = browser._prepare_upload_args({"file_id": "report.txt"})
    assert prepared["_upload_size"] == 4
    assert prepared["_upload_sha256"] == hashlib.sha256(b"safe").hexdigest()


def test_policy_gate_receives_bound_nonsecret_metadata(monkeypatch):
    page = _Page()

    class Session:
        session_id = "cus_gate_session_123"

        @staticmethod
        def observation_metadata(_page):
            return {
                "_session_id": "cus_gate_session_123",
                "_origin": "https://example.com",
            }

    observation = {"id": "obs_gate_observation_123", "sha256": "a" * 64}
    enriched = browser._gate_metadata(
        Session(), page, "type",
        {"selector": "input[name='password']", "text": "hunter2"},
        observation,
    )
    assert enriched["_session_id"] == "cus_gate_session_123"
    assert enriched["_observation_id"] == observation["id"]
    assert enriched["_origin"] == "https://example.com"
    assert "hunter2" not in json.dumps(enriched["_target"])
    assert enriched["_target"]["selector"].startswith("selector_sha256:")
    assert "password" not in enriched["_target"]["selector"]


def test_runtime_action_preserves_exact_approval_effect_proof():
    captured = {}

    class Store:
        @staticmethod
        def record_action(*_args, **kwargs):
            captured.update(kwargs)
            return object()

    session = browser._BrowserSession()
    session._control_store = Store()
    session._control_identity = ("tenant-a", "user:alice")
    try:
        session.record_action(
            "type",
            {"id": "obs_123", "sha256": "a" * 64},
            {"role": "textbox", "name": "Password", "selector": "selector_sha256:x"},
            {
                "text": "never-log-this",
                "_approval_effect_version": 1,
                "_approval_effect_salt": "b" * 32,
                "_approval_effect_commitment": "c" * 64,
            },
        )
    finally:
        session.close()

    assert captured["effect_commitment_version"] == 1
    assert captured["effect_commitment_salt"] == "b" * 32
    assert captured["effect_commitment"] == "c" * 64
    assert "never-log-this" not in json.dumps(captured)
