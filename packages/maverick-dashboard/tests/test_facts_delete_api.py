"""Removing a saved fact from the Facts page."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from maverick import world_model
    from maverick_dashboard.app import app

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    for name in ("MAVERICK_DASHBOARD_TOKEN", "MAVERICK_DASHBOARD_REQUIRE_AUTH", "MAVERICK_TENANT"):
        monkeypatch.delenv(name, raising=False)
    return TestClient(app, headers={"Origin": "http://testserver"})


def test_a_saved_fact_can_be_removed(client) -> None:
    assert client.post("/api/v1/facts", json={"key": "business_hours", "value": "9 to 5"}).status_code == 204
    assert client.post("/api/v1/facts", json={"key": "Office/branch", "value": "Leeds"}).status_code == 204
    assert client.delete("/api/v1/facts/business_hours").status_code == 204
    facts = client.get("/api/v1/facts").json()
    assert "business_hours" not in facts and facts["Office/branch"] == "Leeds"
    # Names with a slash still address exactly one fact.
    assert client.delete("/api/v1/facts/Office%2Fbranch").status_code == 204
    assert client.get("/api/v1/facts").json() == {}
    assert client.delete("/api/v1/facts/business_hours").status_code == 404


def test_removing_a_fact_needs_the_same_permission_as_saving_one(client, monkeypatch) -> None:
    from fastapi import HTTPException
    from maverick_dashboard import facts_api

    seen = []

    def deny(_request, permission):
        seen.append(permission)
        raise HTTPException(status_code=403, detail="forbidden")

    client.post("/api/v1/facts", json={"key": "kept", "value": "yes"})
    monkeypatch.setattr(facts_api, "require_permission", deny)
    assert client.delete("/api/v1/facts/kept").status_code == 403
    assert seen == ["operate"]
    assert client.get("/api/v1/facts").json() == {"kept": "yes"}


def test_removal_only_touches_the_active_workspace(client, tmp_path, monkeypatch) -> None:
    from maverick import world_model

    client.post("/api/v1/facts", json={"key": "shared_name", "value": "workspace A"})
    other = world_model.WorldModel(tmp_path / "other.db")
    other.upsert_fact("shared_name", "workspace B")
    assert client.delete("/api/v1/facts/shared_name").status_code == 204
    assert other.get_fact("shared_name") == "workspace B"


def test_facts_page_offers_remove_and_reads_plainly(client) -> None:
    from test_support.plain_language import developer_terms

    client.post("/api/v1/facts", json={"key": "company_name", "value": "Acme"})
    page = client.get("/facts").text
    assert "data-remove-fact" in page and ">Remove</button>" in page
    assert 'data-key="company_name"' in page and ">Company name<" in page
    assert developer_terms(page) == []
