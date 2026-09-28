"""Safety monitoring extras: a reviewer test you write yourself and the check
for chains of assistants where private data could get out -- ``maverick
immune canary`` and ``maverick immune network`` as buttons."""
from __future__ import annotations

import html as _html
import json
import re

import pytest
from fastapi.testclient import TestClient

ORIGIN = {"Origin": "http://testserver"}
BANNED = re.compile(
    r"\b(tenant|principal|payload|endpoint|schema|uuid|sha-?256|digest|json|revision|"
    r"idempoten\w*|lease|toml|yaml|stdout|traceback|canary|trifecta|swarm)\b|/api/v1|HTTP \d{3}|"
    r"maverick |MAVERICK_|\[\w+\]",
    re.IGNORECASE,
)
CANARY = {"action": "Pay invoice 4411", "detail": "Pay Acme Ltd $4,200 to account 11-22-33.",
          "decision_class": "payments", "flaw": "The account number was changed last week.", "risk": "high"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from maverick import config, world_model
    from maverick.audit import writer as audit_writer
    from maverick_dashboard import app as dash_app

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    (tmp_path / "config.toml").write_text("[immune]\ncanaries = true\n", encoding="utf-8")
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    for name in ("MAVERICK_DASHBOARD_TOKEN", "MAVERICK_TENANT"):
        monkeypatch.delenv(name, raising=False)
    import maverick.runtime_overrides as ro
    monkeypatch.setattr(ro, "OVERRIDES_PATH", tmp_path / "runtime-overrides.toml")
    config.reset_config_cache()
    audit_writer._default = None
    audit_writer._defaults.clear()
    dash_app._world_cache.clear()
    yield TestClient(dash_app.app, headers=ORIGIN)
    config.reset_config_cache()
    audit_writer._default = None
    audit_writer._defaults.clear()
    dash_app._world_cache.clear()


def _as(monkeypatch, role: str, who: str = "user:someone"):
    from maverick_dashboard import auth, rbac
    rbac.set_role(who, role)
    monkeypatch.setattr(auth, "caller_principal", lambda request: who)


def test_written_reviewer_test_lands_in_the_queue_and_keeps_its_mistake_private(client, monkeypatch):
    import maverick.audit as audit_pkg
    from maverick.audit import writer
    from maverick.immune import _ledger, oversight
    from maverick_dashboard import app as dash_app

    seen: list[dict] = []
    real = writer.audit_event

    def spy(kind, **payload):
        seen.append({"kind": kind, **payload})
        return real(kind, **payload)
    monkeypatch.setattr(audit_pkg, "audit_event", spy)
    r = client.post("/api/v1/immune/canaries/written", json=CANARY)
    assert r.status_code == 200, r.text
    assert "changed last week" not in r.text and r.json()["planted"] == 1
    approvals = dash_app._world().pending_approvals()
    assert [a.action for a in approvals] == ["Pay invoice 4411"]
    ledger = _ledger.read(oversight.ledger_path())
    assert ledger[-1]["flaw"] == CANARY["flaw"] and ledger[-1]["class"] == "payments"
    recorded = [row for row in seen if row.get("action") == "plant_written_reviewer_test"]
    assert recorded and "flaw" not in json.dumps(recorded)


def test_written_reviewer_test_refusals(client, monkeypatch, tmp_path):
    assert client.post("/api/v1/immune/canaries/written", json={**CANARY, "risk": "extreme"}).status_code == 422
    assert client.post("/api/v1/immune/canaries/written", json={**CANARY, "flaw": ""}).status_code == 422
    assert client.post("/api/v1/immune/canaries/written",
                       json={**CANARY, "detail": "x" * 5000}).status_code == 422
    assert client.post("/api/v1/immune/canaries/written",
                       json={**CANARY, "requested_by": "user:nobody"}).status_code == 422
    from maverick import config
    (tmp_path / "config.toml").write_text("[immune]\ncanaries = false\n", encoding="utf-8")
    config.reset_config_cache()
    off = client.post("/api/v1/immune/canaries/written", json=CANARY)
    assert off.status_code == 409 and "turned off" in off.json()["detail"]
    _as(monkeypatch, "auditor")
    assert client.post("/api/v1/immune/canaries/written", json=CANARY).status_code == 403


def test_network_check_for_a_described_set_of_assistants(client, monkeypatch):
    graph = {"agents": {"reader": ["read_email"], "writer": ["write_file"], "sender": ["http_post"]},
             "flows": [["reader", "writer"], ["writer", "sender"]]}
    r = client.post("/api/v1/immune/network/check", json={"graph": json.dumps(graph)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assistants"] == 3
    assert not BANNED.search(body["summary"])
    bad = client.post("/api/v1/immune/network/check",
                      json={"graph": json.dumps({"agents": {"a": ["x"]}, "flows": [["a", "ghost"]]})})
    assert bad.status_code == 422
    assert client.post("/api/v1/immune/network/check", json={"graph": "not a map"}).status_code == 422
    assert client.post("/api/v1/immune/network/check", json={"graph": "x" * 600_000}).status_code == 422
    _as(monkeypatch, "operator")
    assert client.post("/api/v1/immune/network/check", json={}).status_code == 403


def test_network_check_for_the_installed_assistants(client, monkeypatch):
    from maverick.immune import network

    calls = []
    real = network.roster_graph

    def roster(domains=None, *, swarm=False):
        calls.append(swarm)
        return real({}, swarm=swarm)
    monkeypatch.setattr(network, "roster_graph", roster)
    r = client.post("/api/v1/immune/network/check", json={"shared_messaging": True})
    assert r.status_code == 200, r.text
    assert calls == [True]


def test_immune_page_shows_the_new_controls_plainly(client, monkeypatch):
    page = client.get("/immune").text
    for control in ("ops-canary-form", "ops-canary-flaw", "ops-network-run", "ops-network-shared",
                    "ops-network-file"):
        assert f'id="{control}"' in page
    start = page.index('id="ops-immune"')
    section = page[start:page.index("</section>", start)]
    text = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", section)))
    assert not BANNED.search(text)
    _as(monkeypatch, "auditor")
    page = client.get("/immune").text
    assert 'id="ops-canary-form"' not in page and 'id="ops-network-run"' in page
