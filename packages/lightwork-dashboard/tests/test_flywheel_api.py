"""GET /api/v1/flywheel -- read-only view of what the data engine has learned."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from lightwork_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from lightwork import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    yield


def test_flywheel_state_empty_by_default(monkeypatch, tmp_path):
    from lightwork import negative_knowledge as nk
    from lightwork import procedural_memory as pm
    monkeypatch.setattr("lightwork.negative_knowledge.shared",
                        lambda: nk.GuardrailRegistry(path=tmp_path / "g.json"))
    monkeypatch.setattr("lightwork.procedural_memory.shared",
                        lambda: pm.MemoryStore(path=tmp_path / "m.json"))
    resp = client.get("/api/v1/flywheel")
    assert resp.status_code == 200
    assert resp.json() == {"guardrails": [], "habits": []}


def test_flywheel_state_reports_learned(monkeypatch, tmp_path):
    from lightwork import negative_knowledge as nk
    from lightwork import procedural_memory as pm
    from lightwork.data_engine import FailureClass

    greg = nk.GuardrailRegistry(path=tmp_path / "g.json")
    greg.update(nk.mine([FailureClass(
        action="shell", count=5, mean_outcome=0.2, causal_effect=-0.5,
        ci_low=-0.6, ci_high=-0.2, trustworthy=True, exemplars=())]))
    monkeypatch.setattr("lightwork.negative_knowledge.shared", lambda: greg)

    mstore = pm.MemoryStore(path=tmp_path / "m.json")
    mstore.update([pm.Memory(action="read_first", benefit=0.4, strength=0.8)])
    monkeypatch.setattr("lightwork.procedural_memory.shared", lambda: mstore)

    body = client.get("/api/v1/flywheel").json()
    assert any(g["action"] == "shell" for g in body["guardrails"])
    assert any(h["action"] == "read_first" for h in body["habits"])
