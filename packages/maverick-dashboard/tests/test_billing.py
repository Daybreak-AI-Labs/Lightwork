"""The /billing dashboard view: per-period accrued charges, period-over-period
trend, and an itemized CSV invoice download."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from maverick.oidc import VerifiedPrincipal


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    w = world_model.WorldModel(tmp_path / "world.db")
    yield w
    w.close()


@pytest.fixture
def client(world, monkeypatch):
    from maverick_dashboard import api as api_mod
    from maverick_dashboard import app as app_mod
    monkeypatch.setattr(app_mod, "_world", lambda: world)
    monkeypatch.setattr(api_mod, "_world", lambda: world)
    return TestClient(app_mod.app, headers={"Origin": "http://testserver"})


def _seed(world, cost=0.42, owner=""):
    g = world.create_goal("Pay invoices", "seed", owner=owner)
    ep = world.start_episode(g)
    world.end_episode(ep, "done", "success", cost_dollars=cost,
                      input_tokens=1000, output_tokens=500, tool_calls=3)
    return g


def test_billing_empty_renders(client):
    r = client.get("/billing")
    assert r.status_code == 200
    assert "No priced runs" in r.text


def test_billing_shows_accrued_charges(client, world):
    _seed(world, cost=0.42)
    r = client.get("/billing")
    assert r.status_code == 200
    assert "accrued charges" in r.text
    assert "0.42" in r.text
    assert "Download invoice" in r.text


def test_billing_csv_invoice_is_itemized(client, world):
    _seed(world, cost=0.42)
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    r = client.get(f"/billing?format=csv&period={period}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers.get("content-disposition", "")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("episode_id,goal_id")
    assert len(lines) == 2                       # header + the one priced episode
    assert ",0.420000," in r.text


def test_billing_csv_other_period_is_empty(client, world):
    _seed(world, cost=0.42)
    r = client.get("/billing?format=csv&period=1999-01")
    assert r.status_code == 200
    assert len(r.text.strip().splitlines()) == 1  # header only, no episodes


def test_billing_is_owner_scoped_for_authenticated_viewer(client, world, monkeypatch):
    from maverick_dashboard import auth

    monkeypatch.setattr(auth, "oidc_enabled", lambda: True)
    monkeypatch.setattr(
        auth,
        "verify_oidc_token",
        lambda token, **_kw: VerifiedPrincipal(
            sub=token,
            issuer="https://issuer.example",
            audience="maverick",
            claims={"sub": token},
        ),
    )
    alice_goal = _seed(world, cost=0.42, owner="user:alice")
    bob_goal = _seed(world, cost=9.99, owner="user:bob")
    period = datetime.now(timezone.utc).strftime("%Y-%m")

    csv = client.get(
        f"/billing?format=csv&period={period}",
        headers={"Authorization": "Bearer alice"},
    )

    assert csv.status_code == 200
    assert f",{alice_goal}," in csv.text
    assert f",{bob_goal}," not in csv.text
    assert ",0.420000," in csv.text
    assert ",9.990000," not in csv.text

    statement = client.get("/billing", headers={"Authorization": "Bearer alice"})
    assert statement.status_code == 200
    assert "$0.4200" in statement.text
    assert "$9.9900" not in statement.text


def test_billing_linked_from_spend(client):
    # /billing left the sidebar (one nav entry per job); the Spend page links
    # to the monthly statement instead.
    r = client.get("/spend")
    assert r.status_code == 200
    assert 'href="/billing"' in r.text


def test_usage_receipts_are_owner_scoped_and_distinguish_carry_forward(client, tmp_path, monkeypatch):
    from maverick.quotas import UsageLedger
    from maverick_dashboard import auth

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setattr(auth, "oidc_enabled", lambda: True)
    monkeypatch.setattr(auth, "verify_oidc_token", lambda token, **kwargs: VerifiedPrincipal(
        sub=token, issuer="https://issuer.example", audience="maverick", claims={"sub":token}))
    ledger = UsageLedger()
    ledger.record_cumulative("user:alice", "a" * 32, 0.3, 30, 10,
                              already_settled={"dollars":0.1, "in_tokens":10, "out_tokens":5})
    ledger.record_cumulative("user:bob", "b" * 32, 99.0, 900, 900)
    page = client.get("/billing", headers={"Authorization":"Bearer alice"})
    assert page.status_code == 200
    assert "a" * 32 in page.text and "b" * 32 not in page.text
    assert "$0.300000" in page.text and "$99.000000" not in page.text
    assert "Previously acknowledged usage carried forward: $0.100000" in page.text
    assert "25 new tokens counted" in page.text


def test_unavailable_reconciliation_does_not_fabricate_zero_usage(client, monkeypatch):
    from maverick.quotas import UsageLedger, UsageLedgerError

    def unavailable(self, **kwargs):
        raise UsageLedgerError("internal private path")
    monkeypatch.setattr(UsageLedger, "settlement_receipts", unavailable)
    response = client.get("/billing")
    assert response.status_code == 200
    assert "absence has not been interpreted as zero usage" in response.text
    assert "internal private path" not in response.text
