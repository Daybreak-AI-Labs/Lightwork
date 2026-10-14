"""The stdlib Python client in sdks/python-client against the real dashboard app.

Client calls travel through an in-process transport into FastAPI's TestClient,
so the routes, bearer checks and same-origin rules are the production ones.
No test opens a socket except the redirect check, which uses loopback only.
"""
from __future__ import annotations

import http.server
import sys
import threading
import time
from pathlib import Path

import pytest

_SDK = Path(__file__).resolve().parents[3] / "sdks" / "python-client"
if str(_SDK) not in sys.path:
    sys.path.insert(0, str(_SDK))

from lightwork_client import Decision, Health, Lightwork, LightworkError  # noqa: E402

BASE = "http://testserver"
SECRET = "dash-token-for-client-tests-0001"  # pragma: allowlist secret
WRONG = "not-the-dashboard-token-0002"  # pragma: allowlist secret


@pytest.fixture
def dashboard(monkeypatch, tmp_path):
    """The real app over a temporary world. Goal starts are recorded, never run."""
    from maverick import world_model
    from maverick.world_model import WorldModel
    from maverick_dashboard import api
    from maverick_dashboard import app as dashboard_app

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    runs: list[int] = []

    async def record_run(goal_id, *args, **kwargs):
        runs.append(goal_id)

    monkeypatch.setattr("maverick.runner.run_goal_in_background_async", record_run)
    for module in (api, dashboard_app):
        monkeypatch.setattr(module, "require_provider_or_400", lambda *a, **k: None)
    monkeypatch.setattr(dashboard_app, "check_goal_rate_limit", lambda *a, **k: None)
    world = WorldModel(tmp_path / "world.db")
    yield world, runs
    world.close()


def _in_process(*, drop_origin: bool = False, log: list | None = None):
    """A transport that routes each request into the real app, with no socket."""
    from fastapi.testclient import TestClient
    from maverick_dashboard.app import app

    test_client = TestClient(app)

    def transport(method, url, headers, body, timeout):
        sent = dict(headers)
        if drop_origin:
            sent = {name: value for name, value in sent.items() if name.lower() != "origin"}
        if log is not None:
            log.append((method, url, sent, timeout))
        # The in-process call has no socket to time out; the timeout is checked at the transport seam.
        response = test_client.request(method, url, headers=sent, content=body)
        return response.status_code, dict(response.headers), response.content

    return transport


def _structured_card() -> dict:
    from maverick.approval_cards import build_approval_card

    return build_approval_card(
        action="browser.click",
        summary="Approve the reviewed customer change",
        target_label="Settings save",
        target={"page": "https://customer.test/settings", "selector": "#save"},
        parameters={"secret": "never-render-this"},  # pragma: allowlist secret
        parameter_summary=["Activate the Save button once"],
        actor="user:alice",
        workspace="tenant-a",
        role="operator",
        authority_versions={"role_revision": "role-v1"},
        input_versions={"page_revision": "page-v1"},
        credential_versions={"browser_session_sha256": "a" * 64},
        budget={"currency": "USD", "maximum": 0.25},
        expires_at=time.time() + 300,
        known_effects=["Changes the reviewed customer setting."],
        unresolved_assumptions=["The page may trigger a notification."],
        before="The setting is unchanged.",
        after="The reviewed setting is saved.",
        irreversible=True,
    )


def test_health_returns_the_degraded_answer_instead_of_raising(dashboard, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    health = Lightwork(BASE, transport=_in_process()).health()
    assert isinstance(health, Health)
    assert health.ok is False and health.http_status == 503
    assert health.body["status"] == "degraded"
    assert "llm_key" in health.body["checks"]


def test_submit_goal_then_read_status_result_and_receipt(dashboard):
    _world, runs = dashboard
    lw = Lightwork(BASE, transport=_in_process())
    key = "client-test-request-key-0001"
    goal = lw.submit_goal("Compare two vector stores", "Write a short table.",
                          max_dollars=0.5, max_wall_seconds=600, max_depth=2,
                          idempotency_key=key)
    assert isinstance(goal["id"], int) and goal["title"] == "Compare two vector stores"
    assert goal["limits"]["max_depth"] == 2
    assert runs == [goal["id"]]

    assert lw.get_goal(goal["id"])["id"] == goal["id"]

    replay = lw.submit_goal("Compare two vector stores", "Write a short table.",
                            max_dollars=0.5, max_wall_seconds=600, max_depth=2,
                            idempotency_key=key)
    assert replay["id"] == goal["id"]
    assert runs == [goal["id"]], "a replayed key must not dispatch the work again"

    receipt = lw.goal_receipt(key)
    assert receipt["goal"]["id"] == goal["id"]
    assert receipt["execution_confirmed"] is False


def test_list_goals_and_read_an_event_page(dashboard):
    world, _runs = dashboard
    goal_id = world.create_goal("Listed task", "")
    lw = Lightwork(BASE, transport=_in_process())
    assert [goal["id"] for goal in lw.list_goals(limit=5)] == [goal_id]
    assert lw.list_goals(status="no-such-status") == []
    page = lw.goal_events(goal_id, since=0, limit=50)
    assert isinstance(page["events"], list)
    assert isinstance(page["next_id"], int)
    assert isinstance(page["terminal"], bool)


def test_answer_question_resolves_the_open_question(dashboard):
    world, _runs = dashboard
    goal_id = world.create_goal("Needs the dates", "")
    question_id = world.ask("Which dates should the report cover?", goal_id=goal_id)
    lw = Lightwork(BASE, transport=_in_process())
    assert lw.answer_question(goal_id, question_id, "May 15-29") is None
    assert world.open_questions(goal_id=goal_id) == []


def test_answering_an_unknown_question_is_a_404(dashboard):
    world, _runs = dashboard
    goal_id = world.create_goal("No questions here", "")
    with pytest.raises(LightworkError) as caught:
        Lightwork(BASE, transport=_in_process()).answer_question(goal_id, 999, "x")
    assert caught.value.status == 404
    assert caught.value.message == "no such open question for this goal"


def test_list_and_approve_a_structured_approval(dashboard):
    from maverick.approval_cards import encode_approval_detail

    world, _runs = dashboard
    card = _structured_card()
    approval_id = world.create_approval(
        "browser.click", risk="high", detail=encode_approval_detail(card),
        provenance="browser", requested_by="",
    )
    lw = Lightwork(BASE, transport=_in_process())
    rows = lw.list_approvals()["approvals"]
    row = next(item for item in rows if item["id"] == approval_id)
    assert row["approval_card_status"] == "current"
    binding = row["approval_card"]["binding_sha256"]

    decision = lw.approve_approval(approval_id, binding=binding, acknowledged=True)
    assert isinstance(decision, Decision)
    assert decision.http_status == 204
    assert decision.decision_id
    assert decision.audit == "delivered"
    assert decision.approval_state == "approved"
    assert world.get_approval(approval_id).status == "approved"
    assert all(item["id"] != approval_id for item in lw.list_approvals()["approvals"])


def test_approve_without_the_card_binding_is_refused(dashboard):
    from maverick.approval_cards import encode_approval_detail

    world, _runs = dashboard
    approval_id = world.create_approval(
        "browser.click", risk="high", detail=encode_approval_detail(_structured_card()),
        provenance="browser", requested_by="",
    )
    with pytest.raises(LightworkError) as caught:
        Lightwork(BASE, transport=_in_process()).approve_approval(
            approval_id, binding="0" * 64, acknowledged=True)
    assert caught.value.status == 409
    assert world.get_approval(approval_id).status == "pending"


def test_deny_an_approval_with_a_reason(dashboard):
    world, _runs = dashboard
    approval_id = world.create_approval("force-push", risk="high", scope="main")
    decision = Lightwork(BASE, transport=_in_process()).deny_approval(
        approval_id, reason="Not during the freeze")
    assert decision.http_status == 204
    assert decision.audit == "delivered"
    assert decision.approval_state == "denied"
    assert world.get_approval(approval_id).status == "denied"


def test_spend_reports_totals_and_episodes(dashboard):
    spend = Lightwork(BASE, transport=_in_process()).spend()
    assert "total" in spend
    assert spend["episodes"] == []


def test_unknown_goal_is_a_404_with_the_server_message(dashboard):
    with pytest.raises(LightworkError) as caught:
        Lightwork(BASE, transport=_in_process()).get_goal(987654)
    assert caught.value.status == 404
    assert caught.value.message == "no such goal"
    assert "404" in str(caught.value)


def test_a_write_without_an_origin_is_refused_and_never_dispatched(dashboard):
    _world, runs = dashboard
    lw = Lightwork(BASE, transport=_in_process(drop_origin=True))
    with pytest.raises(LightworkError) as caught:
        lw.submit_goal("Blocked write")
    assert caught.value.status == 403
    assert "Origin" in caught.value.message
    assert runs == []


def test_writes_send_the_origin_the_dashboard_checks_for(dashboard):
    log: list = []
    with pytest.raises(LightworkError) as caught:
        Lightwork(BASE, transport=_in_process(log=log)).deny_approval(999999)
    assert caught.value.status == 404
    method, _url, sent, _timeout = log[-1]
    assert method == "POST"
    assert sent["Origin"] == BASE


def test_bearer_token_is_sent_and_accepted(dashboard, monkeypatch):
    monkeypatch.setenv("MAVERICK_DASHBOARD_TOKEN", SECRET)
    log: list = []
    lw = Lightwork(BASE, SECRET, transport=_in_process(log=log))
    assert lw.spend()["episodes"] == []
    assert log[0][2]["Authorization"] == f"Bearer {SECRET}"


def test_wrong_token_is_a_401_and_neither_token_leaks(dashboard, monkeypatch):
    monkeypatch.setenv("MAVERICK_DASHBOARD_TOKEN", SECRET)
    lw = Lightwork(BASE, WRONG, transport=_in_process())
    with pytest.raises(LightworkError) as caught:
        lw.spend()
    assert caught.value.status == 401
    texts = [str(caught.value), repr(caught.value), repr(lw)]
    for text in texts:
        assert WRONG not in text
        assert SECRET not in text


def test_a_token_echoed_back_in_an_error_body_is_scrubbed():
    def echoing(method, url, headers, body, timeout):
        return 500, {}, b'{"detail": "bad header Bearer tok-echo-123"}'

    lw = Lightwork(BASE, "tok-echo-123", transport=echoing)
    with pytest.raises(LightworkError) as caught:
        lw.spend()
    assert "tok-echo-123" not in str(caught.value)
    assert "[redacted]" in caught.value.message


def test_timeout_is_passed_to_the_transport_and_must_be_positive():
    seen: list[float] = []

    def record(method, url, headers, body, timeout):
        seen.append(timeout)
        return 200, {}, b'{"total": 0, "episodes": []}'

    Lightwork(BASE, transport=record, timeout=7.5).spend()
    Lightwork(BASE, transport=record).spend()
    assert seen == [7.5, 30.0]
    with pytest.raises(ValueError):
        Lightwork(BASE, transport=record, timeout=0)


def test_writes_are_never_retried_after_a_transport_failure():
    calls: list[str] = []

    def unreachable(method, url, headers, body, timeout):
        calls.append(method)
        raise TimeoutError("timed out")

    with pytest.raises(LightworkError) as caught:
        Lightwork(BASE, transport=unreachable).submit_goal("Once only")
    assert caught.value.status is None
    assert "timed out" in caught.value.message
    assert calls == ["POST"]


def test_a_success_body_that_is_not_json_is_an_error():
    lw = Lightwork(BASE, transport=lambda *args: (200, {}, b"<html>oops</html>"))
    with pytest.raises(LightworkError) as caught:
        lw.spend()
    assert caught.value.status == 200


@pytest.mark.parametrize("base", ["http://example.com", "http://10.0.0.5:8765"])
def test_a_token_is_refused_over_plain_http_to_a_remote_host(base):
    with pytest.raises(ValueError, match="https"):
        Lightwork(base, "tok")


@pytest.mark.parametrize("base", ["http://127.0.0.1:8765", "http://localhost:8765", "https://example.com"])
def test_a_token_is_allowed_on_loopback_or_https(base):
    Lightwork(base, "tok")


def test_plain_http_without_a_token_is_allowed_and_bad_urls_are_not():
    Lightwork("http://example.com")
    with pytest.raises(ValueError):
        Lightwork("ftp://example.com")
    with pytest.raises(ValueError):
        Lightwork("example.com")


def test_repr_never_shows_the_token():
    lw = Lightwork(BASE, "tok-repr-secret", transport=lambda *args: (200, {}, b"{}"))
    assert "tok-repr-secret" not in repr(lw)


def test_the_default_transport_does_not_follow_redirects(monkeypatch):
    """A redirect must not re-send the bearer token to another URL."""
    seen: list[tuple[str, str | None]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server naming
            seen.append((self.path, self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", "/somewhere-else")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
        lw = Lightwork(f"http://127.0.0.1:{server.server_address[1]}", "tok-redirect-check")
        with pytest.raises(LightworkError) as caught:
            lw.spend()
        assert caught.value.status == 302
        assert [path for path, _auth in seen] == ["/api/v1/spend"]
        assert seen[0][1] == "Bearer tok-redirect-check"
    finally:
        server.shutdown()
        server.server_close()
