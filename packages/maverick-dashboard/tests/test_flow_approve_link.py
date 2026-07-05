"""The signed approve/reject link endpoints: a human resumes a paused flow run
from a channel link, no dashboard session -- the token is the only auth."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _client():
    from maverick_dashboard.app import app
    # follow_redirects off so we assert on each page directly
    return TestClient(app, headers={"Origin": "http://testserver"})


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    monkeypatch.setenv("MAVERICK_WEBHOOK_SECRET", "link-secret")  # pragma: allowlist secret
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")


def _paused_run(rid="r1", fid="f1"):
    from maverick.flow import store
    from maverick.flow.ir import Flow, FlowNode
    store.save_flow(Flow(id=fid, name="F", start="a",
                         nodes={"a": FlowNode(id="a", kind="approval", prompt="ok?")}))
    store.save_run(store.FlowRun(run_id=rid, flow_id=fid, status="paused_approval",
                                 prompt="ok?", cursor="a"))
    return rid, fid


def _tok(rid, decision):
    from maverick.flow.approvals import mint_token
    return mint_token(rid, decision)


def test_get_shows_confirm_page_without_resuming(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import automation_queue as aq
    monkeypatch.setattr(aq, "_queue", None)
    rid, _ = _paused_run()
    r = _client().get("/flow/approve", params={"token": _tok(rid, "approved")})
    assert r.status_code == 200
    assert "Confirm" in r.text
    # the GET must NOT have enqueued a resume (defeats link prefetchers)
    from maverick.flow import store
    assert store.load_run(rid).status == "paused_approval"


def test_post_resumes_the_run(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import automation_queue as aq
    monkeypatch.setattr(aq, "_queue", None)
    rid, fid = _paused_run()
    tok = _tok(rid, "approved")
    r = _client().post("/flow/approve", data={"token": tok})
    assert r.status_code == 200 and "recorded" in r.text.lower()
    # a resume job was enqueued carrying the decision
    jobs = [j for j in aq.queue().list(status="pending", limit=100)
            if j.kind == aq.FLOW_RESUME_KIND]
    assert len(jobs) == 1
    assert jobs[0].payload["run_id"] == rid and jobs[0].payload["decision"] == "approved"


def test_invalid_token_is_rejected(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    r = _client().get("/flow/approve", params={"token": "bogus.deadbeef"})
    assert r.status_code == 410
    assert "invalid or has expired" in r.text


def test_post_on_already_resolved_run_is_noop(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import automation_queue as aq
    monkeypatch.setattr(aq, "_queue", None)
    rid, fid = _paused_run()
    from maverick.flow import store
    run = store.load_run(rid)
    run.status = "completed"
    store.save_run(run)
    r = _client().post("/flow/approve", data={"token": _tok(rid, "approved")})
    assert r.status_code == 410 and "no longer awaiting approval" in r.text
    jobs = [j for j in aq.queue().list(status="pending", limit=100)
            if j.kind == aq.FLOW_RESUME_KIND]
    assert jobs == []
