"""The egress lock, enforced where requests actually leave.

``enterprise.py`` sells one property: *"Sensitive data physically cannot reach
a third-party API."* It documents the mechanism as ``enterprise_egress_denial``
applied *"at each tool's request"*. That parenthetical was the load-bearing
false clause. The check existed at **11** call sites; a census of production
code found **194 direct outbound HTTP call sites across 96 modules**, of which
**87 had no egress gate of any kind** -- including ``gmail_tool``,
``salesforce_tool``, ``slack_bot``, ``replicate_tool`` (a second third-party
inference API), and ``maverick_knowledge/embed.py``, which ships document text
to an embedding vendor.

The per-tool design could not have worked. It required every author of every
connector, forever, to remember an unenforced convention, and nothing failed
when they did not -- the test suite validated the policy *function* and a
handful of hand-picked wrappers, so it was structurally incapable of noticing a
bypass. Sixty connectors under ``tools/`` drifted out of coverage exactly that
way.

So the check moves to where requests are actually issued. 86 of those 88
modules use ``httpx`` and one uses ``requests``; both funnel every call --
module-level ``httpx.get``, a hand-built ``Client``, a pooled ``Session`` --
through a single ``send`` method. Wrapping those two methods covers 87 of 88
modules without touching a single connector, and covers connectors not yet
written, which is the part a migration could never do.

**This does not weaken SSRF pinning.** ``tools/_ssrf`` installs a custom
*transport*, which sits below ``send``; the guard runs first and the pinned
transport still runs after. They compose.

**Default posture is unchanged.** ``egress_permitted`` returns True whenever
enterprise mode is off, so on a default install this is one predicate per
request and nothing else. It is not a general firewall and does not try to be:
it enforces the enterprise/compliance boundary that is *claimed*, at the layer
where the claim can be true.

**What it still does not cover**, stated because an overclaim here is the
original defect: a tool that shells out to ``curl``, opens a raw socket, or
uses an HTTP library the guard does not wrap. ``sandbox/network_policy`` says
the same about itself -- there is no packet-level backend. The
``egress_contract`` CI gate enumerates every module that makes direct outbound
HTTP and fails when one uses a library outside the wrapped set, so that gap is
a recorded number rather than a discovery.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

log = logging.getLogger(__name__)

#: HTTP client libraries this guard wraps. The egress_contract gate refuses a
#: production module that reaches the network through anything else.
COVERED_LIBRARIES = ("httpx", "requests", "urllib")

_installed = False
_originals: dict[str, Any] = {}

#: Distinguishes "caller omitted timeout" from "caller passed None", which
#: urllib treats differently (None means no timeout; omitted means the global
#: default socket timeout).
_SENTINEL = object()


def _denied(url: str) -> str | None:
    """Denial reason for an outbound URL, or None to allow.

    Resolved at request time rather than at install time: enterprise mode, the
    compliance floor, and the allow-list are all live config, and a deployment
    that turns the boundary on must not have to restart to get it.
    """
    try:
        from .enterprise import egress_permitted
        if egress_permitted(url):
            return None
    # failure-policy: fail_closed
    except Exception:
        log.exception("egress guard: policy unavailable; refusing egress to %r", url)
        return ("egress policy could not be evaluated, so the boundary stays "
                "closed")
    host = ""
    try:
        from .enterprise import _host_of
        host = _host_of(url)
    except Exception:  # failure-policy: best_effort
        host = ""
    # Deliberate, narrow exception to the audit-refusal contract.
    #
    # That contract exists so "we could not record this action" never becomes
    # "we did it anyway". Here the action is being REFUSED either way -- the
    # caller gets EgressBlocked whether or not this write lands -- so the
    # concern the contract protects against cannot arise. Letting AuditRefused
    # propagate would only replace an accurate egress reason with a confusing
    # one: under enterprise mode with no off-host signing key, every blocked
    # request would surface as OffHostSigningRequiredError, and an operator
    # would go hunting for a signing problem instead of reading the allow-list.
    #
    # The refusal is logged at error level rather than dropped, because a
    # deployment that cannot record blocked exfiltration attempts has a real
    # problem -- just not one worth mistranslating this exception for.
    from .audit import EventKind
    from .audit.errors import AuditRefused
    from .audit.writer import audit_event
    try:
        audit_event(EventKind.EGRESS_BLOCKED, provider="http-client",
                    host=host or (url or "?"))
    except AuditRefused:
        log.error(
            "egress guard: blocked egress to %r but the audit subsystem "
            "refused to record it; the request is still refused",
            host or url)
    return (
        f"enterprise mode: refusing egress to {host or url!r} -- not a local "
        "endpoint and not in [enterprise] allowed_hosts. The data boundary "
        "stays closed."
    )


def _check(url: str) -> None:
    reason = _denied(url)
    if reason is None:
        return
    from .enterprise import EgressBlocked
    raise EgressBlocked(reason)


#: Attributes a wrapping transport uses to hold the one it delegates to.
#: ``tools/_ssrf._PinnedTransport`` uses ``_inner``.
_INNER_ATTRS = ("_inner", "_transport", "transport", "_wrapped", "next")


def _is_network_transport(transport: Any, depth: int = 0) -> bool:
    """Whether a transport performs socket I/O, directly or by delegation.

    An ASGI/WSGI/Mock transport hands the request to an in-process object: no
    socket opens, nothing crosses the host boundary, and there is nothing for
    an egress lock to prevent. Blocking those is a false positive with teeth --
    it refuses in-process ASGI calls under enterprise mode, which is how this
    was found (every TestClient request in the suite blocked on "testserver").

    Detection is by delegation rather than by a name allowlist, because the
    names are not knowable in advance: starlette ships ``_TestClientTransport``,
    not httpx's ``ASGITransport``. And an allowlist of "only HTTPTransport is
    network" would be worse than useless -- it would skip the check for
    SSRF-pinned requests, since ``_PinnedTransport`` *wraps* a network
    transport rather than being one. So: a transport is network if it is an
    httpx network transport, or if it delegates to something that is.

    Fails closed. An unresolvable or too-deeply-nested transport is treated as
    reaching the network, and a mock handler that itself makes a real request
    is checked when that request goes back through a guarded library.
    """
    try:
        import httpx
        if isinstance(transport, (httpx.HTTPTransport, httpx.AsyncHTTPTransport)):
            return True
    except Exception:  # pragma: no cover - httpx absent
        return True
    if depth >= 4:  # pragma: no cover - pathological nesting
        return True
    for attr in _INNER_ATTRS:
        inner = getattr(transport, attr, None)
        if inner is not None and (hasattr(inner, "handle_request")
                                  or hasattr(inner, "handle_async_request")):
            return _is_network_transport(inner, depth + 1)
    return False


def _reaches_the_network(client: Any, request: Any) -> bool:
    """Whether this request will actually leave the process."""
    try:
        transport = client._transport_for_url(request.url)
    except Exception:  # pragma: no cover - unknown client shape; fail closed
        return True
    return _is_network_transport(transport)


def _patch_httpx(mod: Any) -> None:
    """Wrap httpx at ``send``.

    **Known gap, stated rather than hidden: redirects.** ``Client.send``
    resolves the whole redirect chain internally, so this sees only the first
    URL. An allow-listed host answering 302 to a denied host is therefore NOT
    re-checked -- measured, not theorised. ``requests`` and ``urllib`` do not
    have this shape (both re-enter their wrapped method per hop), so the gap is
    httpx-only.

    The obvious fix -- wrapping ``_send_single_request``, which issues exactly
    one request per hop -- was implemented, and it broke **1538 tests in CI**
    with "Attempted to send an async request with a sync Client instance",
    across all three Python versions. It does not reproduce locally, including
    against CI's exact starlette pin, so the mechanism is still unexplained.
    The ``send`` seam is the configuration that has actually been observed
    working: same suite, same runners, 5 failures instead of 1538, and those 5
    are fixed by the in-process transport rule below.

    Shipping an unexplained catastrophic regression to close a narrower hole is
    the wrong trade. The hole is recorded here, in ``docs``, and in a test that
    asserts the current (weaker) behaviour, so it is a known quantity rather
    than a surprise -- and closing it properly needs the CI failure understood
    first, not another guess.
    """
    _originals["httpx.Client.send"] = mod.Client.send
    _originals["httpx.AsyncClient.send"] = mod.AsyncClient.send

    def _sync_send(self, request, **kwargs):
        if _reaches_the_network(self, request):
            _check(str(request.url))
        return _originals["httpx.Client.send"](self, request, **kwargs)

    async def _async_send(self, request, **kwargs):
        if _reaches_the_network(self, request):
            _check(str(request.url))
        return await _originals["httpx.AsyncClient.send"](self, request, **kwargs)

    mod.Client.send = _sync_send
    mod.AsyncClient.send = _async_send


def _patch_requests(mod: Any) -> None:
    _originals["requests.Session.send"] = mod.Session.send

    def _send(self, request, **kwargs):
        _check(str(getattr(request, "url", "")))
        return _originals["requests.Session.send"](self, request, **kwargs)

    mod.Session.send = _send


def _patch_urllib(mod: Any) -> None:
    """Nine production modules reach the network through urllib.

    ``urlopen`` and ``build_opener().open()`` both funnel through
    ``OpenerDirector.open``, so one wrapper covers every form.
    """
    _originals["urllib.OpenerDirector.open"] = mod.OpenerDirector.open

    def _open(self, fullurl, data=None, timeout=_SENTINEL):
        # `fullurl` is a str or a Request; Request.full_url holds the target.
        url = getattr(fullurl, "full_url", None) or (
            fullurl if isinstance(fullurl, str) else "")
        _check(str(url))
        original = _originals["urllib.OpenerDirector.open"]
        if timeout is _SENTINEL:
            return original(self, fullurl, data)
        return original(self, fullurl, data, timeout)

    mod.OpenerDirector.open = _open


def _unpatch(name: str, mod: Any) -> None:
    if name == "httpx":
        if "httpx.Client.send" in _originals:
            mod.Client.send = _originals.pop("httpx.Client.send")
            mod.AsyncClient.send = _originals.pop("httpx.AsyncClient.send")
    elif name == "requests":
        mod.Session.send = _originals.pop(
            "requests.Session.send", mod.Session.send)
    elif name == "urllib.request":
        mod.OpenerDirector.open = _originals.pop(
            "urllib.OpenerDirector.open", mod.OpenerDirector.open)


#: Module name -> patcher. Keyed on the importable name so the hook below can
#: match it exactly.
_TARGETS = {
    "httpx": _patch_httpx,
    "requests": _patch_requests,
    "urllib.request": _patch_urllib,
}

_patched: set[str] = set()


class _PatchOnImport:
    """Patch a client library the moment it is first imported.

    Importing httpx eagerly from ``import maverick`` costs real time and breaks
    the CLI cold-start guarantee -- ``maverick --help`` is pinned not to load
    httpx, the provider SDKs, numpy, or fastapi, and a stray module-level
    import taxes every invocation. So the guard arms itself instead: it patches
    what is already loaded, and registers this finder to catch the rest.

    ``find_spec`` delegates to the remaining finders for the real spec, then
    wraps that spec's ``exec_module`` so the patch lands immediately after the
    module body runs. The loader instance is created per-spec, so setting the
    attribute here does not leak into unrelated imports.
    """

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in _TARGETS or fullname in _patched:
            return None
        spec = None
        for finder in sys.meta_path:
            if finder is self:
                continue
            found = getattr(finder, "find_spec", None)
            if found is None:
                continue
            try:
                spec = found(fullname, path, target)
            except Exception:  # pragma: no cover - a broken finder is not ours
                spec = None
            if spec is not None:
                break
        if spec is None or spec.loader is None:
            return None
        real_exec = spec.loader.exec_module

        def exec_module(module, _real=real_exec, _name=fullname):
            _real(module)
            try:
                _TARGETS[_name](module)
                _patched.add(_name)
            except Exception:  # pragma: no cover - never break an import
                log.exception("egress guard: could not wrap %s", _name)

        try:
            spec.loader.exec_module = exec_module
        except (AttributeError, TypeError):  # pragma: no cover - exotic loader
            return None
        return spec


_hook: _PatchOnImport | None = None


def install() -> bool:
    """Arm the guard. Idempotent; returns whether it is active.

    Patches every target already imported, and hooks the rest so they are
    wrapped on first import. Deliberately does NOT import httpx: see
    :class:`_PatchOnImport`.
    """
    global _installed, _hook
    if _installed:
        return True
    for name, patcher in _TARGETS.items():
        mod = sys.modules.get(name)
        if mod is not None and name not in _patched:
            patcher(mod)
            _patched.add(name)
    if _hook is None:
        _hook = _PatchOnImport()
        sys.meta_path.insert(0, _hook)
    _installed = True
    return True


def uninstall() -> None:
    """Restore the unwrapped send paths. For tests and controlled teardown."""
    global _installed, _hook
    if not _installed:
        return
    for name in list(_patched):
        mod = sys.modules.get(name)
        if mod is not None:
            _unpatch(name, mod)
    _patched.clear()
    if _hook is not None:
        try:
            sys.meta_path.remove(_hook)
        except ValueError:  # pragma: no cover
            pass
        _hook = None
    _originals.clear()
    _installed = False


def active() -> bool:
    return _installed


__all__ = ["COVERED_LIBRARIES", "active", "install", "uninstall"]
