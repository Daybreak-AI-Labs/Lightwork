"""The enterprise boundary, tested at the layer where requests leave.

``enterprise.py`` sells one property -- *"Sensitive data physically cannot
reach a third-party API"* -- and documented the mechanism as
``enterprise_egress_denial`` applied *"at each tool's request"*. A census found
**94 production modules making direct outbound HTTP and 87 with no egress gate
at all**, including ``gmail_tool``, ``salesforce_tool``, ``slack_bot``,
``replicate_tool``, and ``maverick_knowledge/embed.py``.

The reason it drifted that far unnoticed is visible in the old tests, and it is
the thing this file is written against. Every existing egress test is one of
two shapes: a pure-policy test asserting ``egress_permitted(...)`` returns
False, or a per-tool test that calls one tool's ``_run`` and greps its returned
error string. Neither can detect a bypass, because neither ever issues a
request. Sixty connectors under ``tools/`` were added over time with raw
``httpx`` calls and every check stayed green.

So these tests make real calls through real client libraries and assert on what
the network layer does. The blocked cases never reach a socket -- the guard
raises above the transport -- and the permitted cases are asserted by their
*connection* failing, which proves the guard let them through.
"""

from __future__ import annotations

import urllib.request

import pytest
from maverick import egress_guard
from maverick.enterprise import EgressBlocked

httpx = pytest.importorskip("httpx")

#: Refused by DNS/connect rather than by policy, so a connection error proves
#: the guard permitted the request instead of blocking it.
UNROUTABLE = "http://127.0.0.1:9/probe"


@pytest.fixture(autouse=True)
def _leave_the_guard_installed():
    """Restore the process-wide guard after every test in this file.

    Several tests here uninstall it deliberately. Leaving it off would silently
    disarm the boundary for every later test in the session -- the same
    order-dependent pollution this suite already suffers from, introduced by
    the file that tests the fix.
    """
    yield
    egress_guard.uninstall()
    egress_guard.install()


@pytest.fixture
def guarded(monkeypatch):
    """Enterprise mode on, guard installed, torn down cleanly."""
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "1")
    egress_guard.uninstall()
    egress_guard.install()
    yield
    egress_guard.uninstall()


@pytest.fixture
def unguarded_mode(monkeypatch):
    """Guard installed but enterprise mode OFF -- the default posture."""
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    egress_guard.uninstall()
    egress_guard.install()
    yield
    egress_guard.uninstall()


def _allowed(monkeypatch, *hosts):
    monkeypatch.setattr(
        "maverick.enterprise._allowed_egress_hosts", lambda: frozenset(hosts))


# -- the test that did not exist -------------------------------------------

def test_a_raw_httpx_call_is_blocked(guarded) -> None:
    """No tool, no wrapper, no gate call. Just httpx, as 60 connectors use it.

    This is the assertion the suite never had. Before the guard it fails: the
    request goes out.
    """
    with pytest.raises(EgressBlocked):
        httpx.get("https://api.example.com/v1/customers", timeout=1.0)


def test_a_raw_httpx_client_is_blocked(guarded) -> None:
    with pytest.raises(EgressBlocked), httpx.Client(timeout=1.0) as c:
        c.post("https://api.example.com/upload", json={"pii": "x"})


@pytest.mark.asyncio
async def test_a_raw_async_httpx_client_is_blocked(guarded) -> None:
    async with httpx.AsyncClient(timeout=1.0) as c:
        with pytest.raises(EgressBlocked):
            await c.get("https://api.example.com/v1/me")


def test_a_raw_urllib_call_is_blocked(guarded) -> None:
    """Nine production modules use urllib, including http_fetch itself."""
    with pytest.raises(EgressBlocked):
        urllib.request.urlopen("https://api.example.com/data", timeout=1.0)


def test_a_urllib_opener_is_blocked(guarded) -> None:
    """build_opener bypasses urlopen, so the wrapper sits on OpenerDirector."""
    opener = urllib.request.build_opener()
    with pytest.raises(EgressBlocked):
        opener.open("https://api.example.com/data", timeout=1.0)


def test_a_urllib_request_object_is_blocked(guarded) -> None:
    """urlopen also accepts a Request, whose URL lives on .full_url."""
    req = urllib.request.Request(
        "https://api.example.com/data", headers={"X": "1"})
    with pytest.raises(EgressBlocked):
        urllib.request.urlopen(req, timeout=1.0)


def test_a_raw_requests_call_is_blocked(guarded) -> None:
    requests = pytest.importorskip("requests")
    with pytest.raises(EgressBlocked):
        requests.get("https://api.example.com/data", timeout=1.0)


def test_without_the_guard_the_same_call_escapes(monkeypatch) -> None:
    """Proof the guard is load-bearing, not incidental.

    Enterprise mode ON, guard NOT installed -- exactly the state the platform
    shipped in. The request is attempted and fails on the network rather than
    on policy, which is the 87-module hole reproduced in one assertion. If this
    ever starts raising EgressBlocked, something else is enforcing the boundary
    and the tests above no longer prove what they claim.
    """
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "1")
    egress_guard.uninstall()
    try:
        with pytest.raises(httpx.HTTPError):
            httpx.get(UNROUTABLE, timeout=0.5)
    finally:
        egress_guard.install()


def test_a_real_previously_ungated_connector_is_blocked(guarded, monkeypatch) -> None:
    """End-to-end on shipped code, not a synthetic httpx call.

    ``asana_tool`` is one of the 60 connectors under ``tools/`` that reached
    the network with a bare ``httpx.get`` and no gate. Nothing about the
    connector changed; it is covered because the guard sits under it. This is
    the assertion that generalises to the other 86.
    """
    monkeypatch.setenv("ASANA_TOKEN", "t-fake")
    from maverick.tools import asana_tool

    with pytest.raises(EgressBlocked):
        asana_tool._get("/workspaces")


# -- what must still be allowed --------------------------------------------

def test_the_guard_is_a_noop_when_enterprise_mode_is_off(unguarded_mode) -> None:
    """The load-bearing negative control.

    A guard that blocked by default would "pass" every test above while
    breaking every default install -- the zero-config path calls a cloud LLM.
    A connection error here proves the request was permitted and simply had
    nowhere to go.
    """
    with pytest.raises(httpx.HTTPError):
        httpx.get(UNROUTABLE, timeout=0.5)


def test_a_local_endpoint_is_permitted_under_enterprise_mode(guarded) -> None:
    """Local LLMs and sidecars must keep working inside the boundary."""
    with pytest.raises(httpx.HTTPError):
        httpx.get(UNROUTABLE, timeout=0.5)


def test_an_allowlisted_host_is_permitted(guarded, monkeypatch) -> None:
    _allowed(monkeypatch, "allowed.internal")
    try:
        httpx.get("https://allowed.internal/x", timeout=0.5)
    except EgressBlocked:  # pragma: no cover - the failure this pins
        pytest.fail("an allow-listed host must not be blocked")
    except Exception:
        pass  # DNS/connect failure is the expected outcome


def test_a_non_allowlisted_host_is_still_blocked(guarded, monkeypatch) -> None:
    """Negative control for the allow-list: it must not become allow-all."""
    _allowed(monkeypatch, "allowed.internal")
    with pytest.raises(EgressBlocked):
        httpx.get("https://other.example.com/x", timeout=0.5)


# -- composition with SSRF pinning -----------------------------------------

def test_the_guard_does_not_disable_ssrf_pinning(guarded, monkeypatch) -> None:
    """The pinned transport sits below send; both must still apply.

    If the guard had replaced the transport rather than wrapping send, this
    would silently drop SSRF protection while looking like an improvement.
    """
    from maverick.tools import _ssrf

    _allowed(monkeypatch, "localhost")
    with pytest.raises(_ssrf.BlockedHost):
        _ssrf.safe_client("http://127.0.0.1/x")


# -- fail closed -----------------------------------------------------------

def test_an_unevaluable_policy_blocks_rather_than_allows(guarded, monkeypatch) -> None:
    """"We cannot tell" must not resolve to "let it out"."""
    monkeypatch.setattr(
        "maverick.enterprise.egress_permitted",
        lambda url: (_ for _ in ()).throw(RuntimeError("config on fire")))
    with pytest.raises(EgressBlocked):
        httpx.get("https://api.example.com/x", timeout=0.5)


def test_a_block_is_audited(guarded, monkeypatch) -> None:
    """A blocked exfiltration attempt is exactly what an auditor needs to see."""
    seen = []
    monkeypatch.setattr("maverick.audit.record",
                        lambda kind, **kw: seen.append((kind, kw)) or True)
    with pytest.raises(EgressBlocked):
        httpx.get("https://api.example.com/x", timeout=0.5)
    assert seen, "a blocked egress attempt must be recorded"
    assert any("api.example.com" in str(kw) for _, kw in seen), seen


# -- install/uninstall mechanics -------------------------------------------

def test_install_is_idempotent(monkeypatch) -> None:
    egress_guard.uninstall()
    assert egress_guard.install() is True
    first = httpx.Client.send
    assert egress_guard.install() is True
    assert httpx.Client.send is first, "double-wrapping would stack checks"
    egress_guard.uninstall()


def test_uninstall_restores_the_original_send() -> None:
    egress_guard.uninstall()
    original = httpx.Client.send
    egress_guard.install()
    assert httpx.Client.send is not original
    egress_guard.uninstall()
    assert httpx.Client.send is original
    assert egress_guard.active() is False


def test_importing_maverick_does_not_import_httpx() -> None:
    """Arming the guard must not cost cold-start time.

    The first version imported httpx eagerly and broke the CLI's cold-start
    guarantee -- `maverick --help` is pinned not to load httpx, the provider
    SDKs, numpy or fastapi, because a stray module-level import taxes every
    invocation. The guard now hooks the import instead of forcing it.
    """
    import subprocess
    import sys
    out = subprocess.run(
        [sys.executable, "-c",
         "import maverick, sys; print('httpx' in sys.modules)"],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False", "importing maverick pulled in httpx"


def test_a_library_imported_after_the_guard_is_still_wrapped(monkeypatch) -> None:
    """The other half: lazy must not mean unguarded.

    Most connectors do ``import httpx`` inside the function body, so the module
    loads long after ``import maverick``. If the hook only covered
    already-loaded libraries, exactly those connectors would stay unprotected
    -- which is the population the whole change exists to cover.

    Done in-process by evicting httpx and re-importing, rather than in a
    subprocess: conftest points HOME at a tmp dir, and certifi (which httpx
    needs) lives in the user site-packages, so a subprocess cannot import httpx
    at all here.
    """
    import sys

    monkeypatch.setenv("MAVERICK_ENTERPRISE", "1")
    egress_guard.uninstall()
    for name in [m for m in list(sys.modules) if m == "httpx"
                 or m.startswith("httpx.")]:
        monkeypatch.delitem(sys.modules, name)

    egress_guard.install()
    assert "httpx" not in sys.modules, "arming the guard must not force the import"

    import httpx as freshly_imported

    with pytest.raises(EgressBlocked):
        freshly_imported.get("https://api.example.com/x", timeout=1.0)


def test_importing_maverick_installs_the_guard() -> None:
    """The wiring that cannot be forgotten.

    Explicit per-entry-point installation is the same shape of mistake as
    per-tool gating: it works until someone adds an entry point.
    """
    import subprocess
    import sys
    out = subprocess.run(
        [sys.executable, "-c",
         "import maverick; from maverick import egress_guard;"
         " print(egress_guard.active())"],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "True", out.stdout
