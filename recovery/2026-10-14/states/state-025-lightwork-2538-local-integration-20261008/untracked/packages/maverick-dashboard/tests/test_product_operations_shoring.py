"""Copilot hands a selected business area to the ordinary protected editor."""
from __future__ import annotations

from fastapi.testclient import TestClient
from maverick_dashboard import product_operations as ops
from maverick_dashboard import product_operations_shoring as adapter
from maverick_dashboard.app import app

BASE = "/api/v1/copilot/product-operations"


def test_copilot_opens_exact_shoring_editor_without_writing(monkeypatch, tmp_path):
    from maverick import config, world_model
    from maverick_dashboard import api as api_mod

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(ops, "adapters", lambda: {entry.id: entry for entry in adapter.entries()})
    config.reset_config_cache()
    api_mod._world_cache.clear()
    try:
        with TestClient(app, headers={"Origin": "http://testserver"}) as client:
            saved = client.post("/api/v1/shoring/function", json={
                "key": "ap", "name": "Accounts payable", "department": "finance_ap",
                "slas": [{"name": "Turnaround", "metric": "max_turnaround_hours", "target": 8}],
            })
            assert saved.status_code == 200, saved.text
            catalog = client.post(BASE + "/catalog", json={"page": "/shoring", "selection": {}})
            assert catalog.status_code == 200, catalog.text
            item = catalog.json()["operations"][0]
            assert item["id"] == "shoring.area.edit" and item["available"]
            assert {choice["value"] for choice in item["fields"][0]["choices"]} == {"__new__", "ap"}

            review = client.post(BASE + "/prepare", json={
                "page": "/shoring", "selection": {}, "operation": item["id"],
                "context": catalog.json()["context"], "revision": item["revision"],
                "arguments": {"key": "ap"},
            })
            assert review.status_code == 200, review.text
            assert review.json()["read_only"] is True
            assert review.json()["details"]["open"] == {
                "label": "Edit Accounts payable", "href": "/shoring?edit=ap#sh-form",
            }
            new = client.post(BASE + "/prepare", json={
                "page": "/shoring", "selection": {}, "operation": item["id"],
                "context": catalog.json()["context"], "revision": item["revision"],
                "arguments": {"key": "__new__"},
            })
            assert new.status_code == 200
            assert new.json()["details"]["open"]["href"] == "/shoring#sh-form"
            page = client.get("/shoring?edit=ap")
            assert page.status_code == 200
            assert 'data-edit="ap"' in page.text
            assert "requestedEdit" in page.text
            assert client.get("/api/v1/shoring/book").json()["functions"][0]["slas"][0]["target"] == 8
            assert client.post("/api/v1/shoring/function", json={
                "key": "ap", "name": "Accounts payable revised",
                "department": "finance_ap",
            }).status_code == 200
            assert client.post(BASE + "/prepare", json={
                "page": "/shoring", "selection": {}, "operation": item["id"],
                "context": catalog.json()["context"], "revision": item["revision"],
                "arguments": {"key": "ap"},
            }).status_code == 409
    finally:
        config.reset_config_cache()
        api_mod._world_cache.clear()
