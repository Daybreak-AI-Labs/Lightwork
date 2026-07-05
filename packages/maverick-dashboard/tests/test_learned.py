"""Learned-capability ledger panel + generated-tool removal (#427)."""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient


def _client():
    from maverick_dashboard.app import app
    return TestClient(app)


def _isolate(monkeypatch, tmp_path):
    """Point the self-learning ledger + generated-tools dir at tmp_path."""
    from maverick import self_learning
    ledger = tmp_path / "learned.ndjson"
    gen = tmp_path / "generated_tools"
    gen.mkdir()
    monkeypatch.setattr(self_learning, "LEARNED_PATH", ledger)
    monkeypatch.setattr(self_learning, "GENERATED_TOOLS_DIR", gen)
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    return ledger, gen


def _write_ledger(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


def test_learned_panel_renders_ledger_entries(monkeypatch, tmp_path):
    ledger, _gen = _isolate(monkeypatch, tmp_path)
    _write_ledger(ledger, [
        {"ts": time.time(), "need": "send an sms", "kind": "tool",
         "name": "send_sms", "source": "generated", "outcome": "acquired"},
        {"ts": time.time(), "need": "scrape a page", "kind": "skill",
         "name": "web_scrape", "source": "catalog", "outcome": "failed"},
    ])
    text = _client().get("/learned").text
    assert "learned capabilities" in text.lower()
    assert "send_sms" in text
    assert "web_scrape" in text
    assert "send an sms" in text
    assert "acquired" in text and "failed" in text


def test_learned_page_renders_empty(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    text = _client().get("/learned").text
    assert "Nothing learned yet" in text
    assert "No generated tools" in text
    assert "No self-harness guidance learned" in text
    # Nav link present.
    assert 'href="/learned"' in text


def _seed_harness(monkeypatch, tmp_path, *, enabled=True):
    """Point the self-harness store at tmp_path and seed one model's guidance."""
    from maverick import self_harness as sh
    store = tmp_path / "addenda.json"
    monkeypatch.setattr(sh, "_store_path", lambda: store)
    if enabled:
        monkeypatch.setenv("MAVERICK_SELF_HARNESS", "1")
    else:
        monkeypatch.delenv("MAVERICK_SELF_HARNESS", raising=False)
        monkeypatch.setattr("maverick.config.load_config", dict)
    sh._write_addenda(
        {"claude-x": "Operating guidance learned for this model:\n"
                     "- verify the export precondition before acting"}, store)
    return store


def test_harness_guidance_renders_on_page_and_api(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _seed_harness(monkeypatch, tmp_path, enabled=True)
    text = _client().get("/learned").text
    assert "self-harness guidance" in text.lower()
    assert "claude-x" in text
    assert "verify the export precondition before acting" in text
    # API mirrors the page.
    body = _client().get("/api/v1/learned").json()
    assert body["harness_enabled"] is True
    assert body["harness"][0]["model_id"] == "claude-x"
    assert "verify the export precondition before acting" in body["harness"][0]["lines"]


def test_harness_view_shows_recent_rate_and_pending_corpus(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick import config
    from maverick import self_harness as sh
    from maverick import self_harness_eval as ev
    store = tmp_path / "addenda.json"
    monkeypatch.setattr(sh, "_store_path", lambda: store)
    monkeypatch.setenv("MAVERICK_SELF_HARNESS", "1")
    sh._write_addenda({"m1": "Operating guidance learned for this model:\n"
                             "- verify the export"}, store)
    sh._write_line_meta({sh._line_id("m1", "verify the export"): {
        "model_id": "m1", "text": "verify the export",
        "signature": "timeout: slow export",
        "recall_success": 6, "recall_failure": 2,
        "recent_outcomes": [1, 0, 0, 0]}}, store)
    cpath = tmp_path / "corpus.json"
    cpath.write_text("{}")
    ev.stage_candidates(cpath, "m1", [{"goal": "g", "expected": "e"}])
    monkeypatch.setattr(config, "load_config", lambda *a, **k: {
        "self_harness": {"enable": True, "eval_corpus": str(cpath)}})
    text = _client().get("/learned").text
    assert "75% success" in text                       # lifetime rate unchanged
    assert "recent 1✓/3✗ (25%)" in text                # the recency signal
    assert "harvested corpus candidate" in text        # staged ground truth visible
    body = _client().get("/api/v1/learned").json()
    rec = body["harness"][0]["provenance"][0]
    assert rec["recent_rate"] == 0.25 and body["pending_corpus"] == {"m1": 1}


def test_harness_view_shows_provenance_and_conflicts(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick import self_harness as sh
    store = tmp_path / "addenda.json"
    monkeypatch.setattr(sh, "_store_path", lambda: store)
    monkeypatch.setenv("MAVERICK_SELF_HARNESS", "1")
    sh._write_addenda({"claude-x":
                       "Operating guidance learned for this model:\n"
                       "- Prefer streaming for large exports\n"
                       "- Avoid streaming for large exports"}, store)
    sh._write_line_meta({sh._line_id("claude-x", "Prefer streaming for large exports"): {
        "model_id": "claude-x", "text": "Prefer streaming for large exports",
        "signature": "timeout: slow export", "held_out_delta": 0.2, "samples": 8,
        "learned_at": 1700000000.0, "updated_at": 1700000000.0}}, store)
    text = _client().get("/learned").text
    assert "timeout: slow export" in text          # per-line provenance rendered
    assert "possible conflict" in text             # conflict badge rendered
    body = _client().get("/api/v1/learned").json()
    h = body["harness"][0]
    assert h["provenance"] and h["conflicts"]


def test_harness_view_shows_efficacy_canary_and_domain(monkeypatch, tmp_path):
    # The /learned page surfaces the outcome-driven lifecycle the CLI exposes:
    # per-line success/failure counters, the canary probation badge, and the
    # department tag for domain-scoped guidance.
    _isolate(monkeypatch, tmp_path)
    from maverick import self_harness as sh
    store = tmp_path / "addenda.json"
    monkeypatch.setattr(sh, "_store_path", lambda: store)
    monkeypatch.setenv("MAVERICK_SELF_HARNESS", "1")
    finance_key = sh._scoped_key("claude-x", "domain=finance")
    sh._write_addenda({
        "claude-x": "Operating guidance learned for this model:\n- check inputs first",
        finance_key: "Operating guidance learned for this model:\n- reconcile before posting",
    }, store)
    sh._write_line_meta({
        sh._line_id("claude-x", "check inputs first"): {
            "model_id": "claude-x", "text": "check inputs first",
            "recall_success": 3, "recall_failure": 1, "canary": True,
            "learned_at": 1700000000.0, "updated_at": 1700000000.0},
        sh._line_id(finance_key, "reconcile before posting"): {
            "model_id": "claude-x", "text": "reconcile before posting",
            "learned_at": 1700000000.0, "updated_at": 1700000000.0},
    }, store)
    body = _client().get("/api/v1/learned").json()
    prov = body["harness"][0]["provenance"]
    by_text = {r["text"]: r for r in prov}
    assert by_text["check inputs first"]["success"] == 3
    assert by_text["check inputs first"]["failure"] == 1
    assert by_text["check inputs first"]["rate"] == 0.75
    assert by_text["check inputs first"]["canary"] is True
    assert by_text["reconcile before posting"]["domain"] == "finance"
    text = _client().get("/learned").text
    assert "canary" in text                          # probation badge rendered
    assert "75% success" in text                     # outcome counters rendered
    assert "finance" in text                          # department tag rendered


def test_harness_guidance_shows_off_note_when_disabled(monkeypatch, tmp_path):
    # Stored-but-paused guidance is still shown, with a clear "not recalled" note
    # (matches `maverick self-harness show`).
    _isolate(monkeypatch, tmp_path)
    _seed_harness(monkeypatch, tmp_path, enabled=False)
    body = _client().get("/api/v1/learned").json()
    assert body["harness_enabled"] is False
    assert body["harness"][0]["model_id"] == "claude-x"
    text = _client().get("/learned").text
    assert "Self-harness is OFF" in text


def test_generated_tools_list_renders(monkeypatch, tmp_path):
    _ledger, gen = _isolate(monkeypatch, tmp_path)
    (gen / "send_sms.py").write_text("# tool\n", encoding="utf-8")
    (gen / ".staging_x.py").write_text("# hidden\n", encoding="utf-8")
    text = _client().get("/learned").text
    assert "send_sms.py" in text
    # Dot/underscore-prefixed staging files are not listed.
    assert ".staging_x.py" not in text
    # API shape mirrors the page.
    body = _client().get("/api/v1/learned").json()
    assert "send_sms.py" in body["generated_tools"]


def test_delete_removes_only_named_file(monkeypatch, tmp_path):
    _ledger, gen = _isolate(monkeypatch, tmp_path)
    (gen / "bad.py").write_text("# bad\n", encoding="utf-8")
    (gen / "good.py").write_text("# good\n", encoding="utf-8")
    client = _client()
    r = client.request(
        "DELETE", "/api/v1/generated-tools/bad.py",
        headers={"Origin": "http://testserver"},
    )
    assert r.status_code == 204
    assert not (gen / "bad.py").exists()
    assert (gen / "good.py").exists()


def test_delete_missing_file_404(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    client = _client()
    r = client.request(
        "DELETE", "/api/v1/generated-tools/nope.py",
        headers={"Origin": "http://testserver"},
    )
    assert r.status_code == 404


def test_delete_rejects_traversal(monkeypatch, tmp_path):
    """A traversal / outside-dir name must be refused and touch nothing."""
    _ledger, gen = _isolate(monkeypatch, tmp_path)
    secret = tmp_path / "secret.py"
    secret.write_text("# do not delete\n", encoding="utf-8")
    client = _client()
    # Names that reach the handler are rejected with 400; names whose
    # decoded path contains a separator (../, subdir/) never route to it
    # and 404 — either way the target is refused, never deleted.
    for name in ("foo.txt", "x.py.bak"):
        r = client.request(
            "DELETE", f"/api/v1/generated-tools/{name}",
            headers={"Origin": "http://testserver"},
        )
        assert r.status_code == 400, name
    for name in ("..", "..%2Fsecret.py", "subdir%2Fx.py"):
        r = client.request(
            "DELETE", f"/api/v1/generated-tools/{name}",
            headers={"Origin": "http://testserver"},
        )
        assert r.status_code in (400, 404), name
    # The out-of-dir target survived every attempt.
    assert secret.exists()


def test_delete_respects_same_origin(monkeypatch, tmp_path):
    """Cross-site DELETE (no token, bad Origin) is blocked by middleware."""
    _ledger, gen = _isolate(monkeypatch, tmp_path)
    (gen / "bad.py").write_text("# bad\n", encoding="utf-8")
    client = _client()
    r = client.request(
        "DELETE", "/api/v1/generated-tools/bad.py",
        headers={"Origin": "http://evil.example"},
    )
    assert r.status_code == 403
    # The file survives the blocked request.
    assert (gen / "bad.py").exists()
