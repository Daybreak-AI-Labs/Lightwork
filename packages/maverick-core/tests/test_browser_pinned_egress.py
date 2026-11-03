"""The interactive browser must keep its pinned egress boundary for its lifetime."""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from maverick import consent_scanner
from maverick.tools import browser


class FakeProxy:
    server_url = "socks5://127.0.0.1:45678"

    def __init__(self):
        self.closed = 0

    def __enter__(self):
        return self

    def close(self):
        self.closed += 1


def install_browser(monkeypatch, *, fail_at=None):
    events = []
    launches = []

    class Context:
        def route(self, *_args):
            if fail_at == "route":
                raise RuntimeError("route failed")

        def new_page(self):
            if fail_at == "page":
                raise RuntimeError("page failed")
            return object()

        def close(self):
            events.append("context")

    class ChromiumBrowser:
        def new_context(self, **_kwargs):
            if fail_at == "context":
                raise RuntimeError("context failed")
            return Context()

        def close(self):
            events.append("browser")

    class Chromium:
        def launch(self, **kwargs):
            launches.append(kwargs)
            if fail_at == "launch":
                raise RuntimeError("launch failed")
            return ChromiumBrowser()

    class Playwright:
        chromium = Chromium()

        def stop(self):
            events.append("playwright")

    package = ModuleType("playwright")
    package.__path__ = []
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: SimpleNamespace(start=lambda: Playwright())
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(consent_scanner, "_PinnedSocksProxy", FakeProxy)
    return launches, events


def test_browser_routes_all_connections_through_owned_pinned_proxy(monkeypatch):
    launches, events = install_browser(monkeypatch)
    session = browser._BrowserSession()
    session._ensure_started()
    proxy = session._egress_proxy
    assert launches[0]["proxy"] == {
        "server": proxy.server_url,
        "bypass": "<-loopback>",
    }
    assert "--disable-quic" in launches[0]["args"]
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in launches[0]["args"]
    assert "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1" in launches[0]["args"]
    session.close()
    assert events == ["context", "browser", "playwright"]
    assert proxy.closed == 1
    assert session._egress_proxy is None


@pytest.mark.parametrize("fail_at", ["launch", "context", "route", "page"])
def test_browser_startup_failure_closes_proxy_and_can_retry(monkeypatch, fail_at):
    install_browser(monkeypatch, fail_at=fail_at)
    created = []

    def new_proxy():
        proxy = FakeProxy()
        created.append(proxy)
        return proxy

    monkeypatch.setattr(consent_scanner, "_PinnedSocksProxy", new_proxy)
    session = browser._BrowserSession()
    with pytest.raises(RuntimeError, match="failed"):
        session._ensure_started()
    assert created[0].closed == 1
    assert session._egress_proxy is None
    assert session._page is None
    install_browser(monkeypatch)
    session._ensure_started()
    assert session._page is not None
    session.close()


def test_chromium_cannot_bypass_proxy_for_a_loopback_destination(monkeypatch):
    """Exercise Chromium's implicit loopback bypass, not just its launch flags."""
    import socket

    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import Error as PlaywrightError

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.5)
    port = listener.getsockname()[1]
    # The test uses an ephemeral local port. Public production traffic remains
    # restricted to the scanner proxy's default 80/443 policy.
    monkeypatch.setattr(consent_scanner, "_SOCKS_ALLOWED_PORTS", frozenset({port}))
    # Exercise the network backstop even if a higher-level URL check is bypassed.
    monkeypatch.setattr(browser, "_route_guard", lambda route, _request: route.continue_())
    session = browser._BrowserSession()
    try:
        try:
            session._ensure_started()
        except PlaywrightError as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Playwright Chromium is not installed")
            raise
        with pytest.raises(PlaywrightError):
            session.page.goto(f"http://127.0.0.1:{port}/", timeout=5_000)
        with pytest.raises(TimeoutError):
            listener.accept()
    finally:
        session.close()
        listener.close()


def test_browser_does_not_launch_without_pinned_proxy(monkeypatch):
    launches, _events = install_browser(monkeypatch)
    created = []

    class BrokenProxy(FakeProxy):
        def __enter__(self):
            raise OSError("proxy unavailable")

    def new_proxy():
        proxy = BrokenProxy()
        created.append(proxy)
        return proxy

    monkeypatch.setattr(consent_scanner, "_PinnedSocksProxy", new_proxy)
    session = browser._BrowserSession()
    with pytest.raises(OSError, match="proxy unavailable"):
        session._ensure_started()
    assert launches == []
    assert created[0].closed == 1
    assert session._egress_proxy is None
