"""Per-run budget coordinated with the per-tenant daily cap (#78).

Without coordination the over-quota gate only fires *between* runs, so a tenant
$1 from its daily ceiling could still launch a $5 run and overshoot. The budget
builder clamps max_dollars to the tenant's remaining daily allowance."""
from __future__ import annotations

import json
import math
import time

import pytest
from maverick.budget import budget_from_config
from maverick.quotas import record_usage
from maverick.tenant import registry


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / ".maverick"))
    for env in ("MAVERICK_TENANT", "MAVERICK_ENFORCE_PLAN_CAPS",
                "MAVERICK_BUDGET_DOLLARS"):
        monkeypatch.delenv(env, raising=False)


def test_remaining_today_none_without_cap():
    assert registry.tenant_remaining_today(None) is None
    assert registry.tenant_remaining_today("ghost") is None   # unprovisioned
    registry.create_tenant("acme", plan="free")               # provisioned, no cap
    assert registry.tenant_remaining_today("acme") is None


def test_remaining_today_is_cap_minus_spend(monkeypatch):
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    registry.create_tenant("acme", plan="free")
    registry.set_quota("acme", 10.0)
    record_usage("user:local", 7.0, 0, 0)                     # acme ledger -> $7
    assert registry.tenant_remaining_today("acme") == pytest.approx(3.0)


def test_budget_clamped_to_tenant_remainder(monkeypatch):
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    registry.create_tenant("acme", plan="free")
    registry.set_quota("acme", 10.0)
    record_usage("user:local", 9.0, 0, 0)                     # $1 left today
    # Even an explicit $5 cap is clamped down to the $1 tenant remainder.
    b = budget_from_config(max_dollars=5.0)
    assert b.max_dollars == pytest.approx(1.0)


def test_budget_clamp_only_lowers(monkeypatch):
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    registry.create_tenant("acme", plan="free")
    registry.set_quota("acme", 100.0)
    record_usage("user:local", 1.0, 0, 0)                     # $99 left
    # A small per-run cap is NOT raised to the big tenant remainder.
    b = budget_from_config(max_dollars=2.0)
    assert b.max_dollars == pytest.approx(2.0)


def test_budget_unchanged_without_tenant():
    # No active tenant -> single-tenant default behavior is untouched.
    b = budget_from_config(max_dollars=5.0)
    assert b.max_dollars == pytest.approx(5.0)


def test_exhausted_tenant_clamps_to_zero(monkeypatch):
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    registry.create_tenant("acme", plan="free")
    registry.set_quota("acme", 5.0)
    record_usage("user:local", 8.0, 0, 0)                     # over cap
    b = budget_from_config(max_dollars=5.0)
    assert b.max_dollars == 0.0


def test_clamp_never_raises_an_unset_cap_above_the_default(monkeypatch):
    # No explicit max_dollars + a tenant with a LARGE remainder must NOT raise
    # the per-run cap above Budget's default -- the clamp only ever lowers.
    from maverick.budget import Budget
    default = Budget.__dataclass_fields__["max_dollars"].default
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    registry.create_tenant("acme", plan="free")
    registry.set_quota("acme", 1000.0)
    record_usage("user:local", 1.0, 0, 0)                     # remainder ~999
    b = budget_from_config()                                  # no max_dollars set
    assert b.max_dollars == pytest.approx(default)            # not 999


# ---- Finding #2: per-tenant daily cap under concurrency ---------------------

def test_concurrent_starts_reserve_so_clamps_sum_within_cap(monkeypatch):
    # Two same-tenant runs that START before either records spend must not each
    # clamp to the full remainder and collectively overshoot the daily cap. The
    # first run reserves its clamped cap; the second must SEE that reservation,
    # so the two per-run caps sum to <= the tenant daily ceiling. Before the
    # reservation fix, run B read the same $10 remainder as run A and clamped to
    # $6, summing to $12 > $10.
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    registry.create_tenant("acme", plan="free")
    registry.set_quota("acme", 10.0)
    # Run A starts: remaining=10 -> clamp min(6, 10)=6, and it reserves $6.
    b1 = budget_from_config(max_dollars=6.0)
    assert b1.max_dollars == pytest.approx(6.0)
    # Run B starts before A records any spend. It must see A's $6 in-flight hold:
    # remaining is now 10 - 6 = 4, so its cap clamps to min(6, 4)=4, NOT 6.
    b2 = budget_from_config(max_dollars=6.0)
    assert b2.max_dollars == pytest.approx(4.0)
    # The invariant: concurrent per-run caps sum within the tenant daily cap.
    assert b1.max_dollars + b2.max_dollars <= 10.0 + 1e-9


def test_expired_reservation_does_not_reduce_remaining():
    # A live in-flight hold reduces the remainder; once its TTL has lapsed it no
    # longer counts (the backstop that stops a crashed run from permanently
    # denting the cap -- remaining self-heals).
    registry.create_tenant("acme", plan="free")
    registry.set_quota("acme", 10.0)
    registry.reserve_tenant_dollars("acme", 3.0, 3600, "rid-live")
    assert registry.tenant_remaining_today("acme") == pytest.approx(7.0)
    # Force its expires_at into the past -> it is ignored, full cap available.
    path = registry._reservations_path("acme")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["rid-live"]["expires_at"] = time.time() - 1.0
    path.write_text(json.dumps(data), encoding="utf-8")
    assert registry.tenant_remaining_today("acme") == pytest.approx(10.0)


def test_record_usage_releases_reservation(monkeypatch):
    # record_usage clears this run's hold when it writes actual spend, so the
    # spend isn't double-counted against the cap for the rest of the TTL.
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    registry.create_tenant("acme", plan="free")
    registry.set_quota("acme", 10.0)
    registry.reserve_tenant_dollars("acme", 4.0, 3600, "rid-x")
    assert registry.tenant_remaining_today("acme") == pytest.approx(6.0)
    record_usage("user:local", 1.0, 0, 0, reservation_id="rid-x")
    # Only the $1 recorded spend now counts; the $4 hold is released.
    assert registry.tenant_remaining_today("acme") == pytest.approx(9.0)


def test_no_reservation_without_active_tenant(tmp_path):
    # Regression: the default single-tenant / no-cap path is untouched -- clamp
    # unchanged AND the reservation machinery stays inert (no hold, no file).
    b = budget_from_config(max_dollars=5.0)
    assert b.max_dollars == pytest.approx(5.0)
    assert not hasattr(b, "_tenant_reservation_id")
    home = tmp_path / ".maverick"
    assert not (home.exists() and list(home.rglob("reservations.json")))


def test_corrupt_reservations_clamp_budget_to_zero(monkeypatch):
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    registry.create_tenant("acme", max_daily_dollars=10.0)
    path = registry._reservations_path("acme")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"rid":{"dollars":NaN,"expires_at":9999999999}}', encoding="utf-8")
    assert budget_from_config(max_dollars=5.0).max_dollars == 0.0


@pytest.mark.parametrize("amount,ttl", [(math.nan, 60), (1, math.inf), (1, -1)])
def test_nonfinite_or_invalid_reservation_is_rejected(amount, ttl):
    with pytest.raises(ValueError):
        registry.reserve_tenant_dollars("acme", amount, ttl, "rid")
