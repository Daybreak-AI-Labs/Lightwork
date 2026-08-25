"""Breach planner: verified regimes, computed clocks, honest refusals.

The assertions that matter: the fixed clocks compute exactly (72 hours,
calendar days, weekend-skipping business days, the malicious 60-day
Japan branch, determination anchors); thresholds annotate instead of
silently dropping; unknown regimes are refused naming the absent-by-
design set; the feature is off by default.
"""
from __future__ import annotations

import datetime

import pytest
from maverick import breach_notification as bn
from maverick import config

#: Tuesday 2026-08-25 12:00:00 UTC.
_TUESDAY = 1787659200.0
_DAY = 86_400.0


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    (tmp_path / "config.toml").write_text(
        "[breach_notification]\nenable = true\n", encoding="utf-8",
    )
    config.reset_config_cache()
    yield tmp_path
    config.reset_config_cache()


def _day_name(stamp: float) -> str:
    return datetime.datetime.fromtimestamp(
        stamp, tz=datetime.timezone.utc).strftime("%a")


def test_off_by_default(tmp_path):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    assert bn.enabled() is False
    with pytest.raises(bn.BreachNotificationConfigError, match="enable"):
        bn.assess_breach(["eu_gdpr"])


def test_all_fourteen_regimes_list_with_citations():
    rows = bn.list_regimes()
    assert len(rows) == 14
    assert all(row["citations"] for row in rows)


def test_fixed_clocks_compute_exactly():
    plan = bn.assess_breach(
        ["eu_gdpr", "kr_pipa", "us_tx", "sg_pdpa", "us_hipaa"],
        subjects_affected=1200, discovered_at=_TUESDAY,
    )
    d = {o["regime"]: o for o in plan["obligations"]}
    assert d["eu_gdpr"]["authority_due_at"] == _TUESDAY + 72 * 3600
    assert d["kr_pipa"]["subjects_due_at"] == _TUESDAY + 72 * 3600
    assert d["us_tx"]["authority_due_at"] == _TUESDAY + 30 * _DAY
    assert d["us_hipaa"]["subjects_due_at"] == _TUESDAY + 60 * _DAY
    assert d["sg_pdpa"]["assessment_due_at"] == _TUESDAY + 30 * _DAY
    # Sorted by soonest authority clock first.
    assert plan["obligations"][0]["regime"] in {"eu_gdpr", "kr_pipa"}


def test_business_days_skip_weekends_both_directions():
    tuesday = bn.assess_breach(["br_lgpd"], discovered_at=_TUESDAY)
    due = tuesday["obligations"][0]["authority_due_at"]
    assert _day_name(due) == "Fri" and due == _TUESDAY + 3 * _DAY
    friday = bn.assess_breach(["br_lgpd"], discovered_at=_TUESDAY + 3 * _DAY)
    due = friday["obligations"][0]["authority_due_at"]
    assert _day_name(due) == "Wed" and due == _TUESDAY + 8 * _DAY


def test_japan_malicious_branch_extends_to_sixty_days():
    calm = bn.assess_breach(["jp_appi"], discovered_at=_TUESDAY)
    attacked = bn.assess_breach(
        ["jp_appi"], malicious=True, discovered_at=_TUESDAY)
    assert calm["obligations"][0]["authority_due_at"] == _TUESDAY + 30 * _DAY
    assert attacked["obligations"][0]["authority_due_at"] == _TUESDAY + 60 * _DAY


def test_determination_anchor_moves_the_clock():
    plan = bn.assess_breach(
        ["us_tx"], discovered_at=_TUESDAY, determined_at=_TUESDAY + 5 * _DAY,
    )
    assert plan["obligations"][0]["authority_due_at"] == _TUESDAY + 35 * _DAY
    with pytest.raises(ValueError, match="precede"):
        bn.assess_breach(["us_tx"], discovered_at=_TUESDAY,
                         determined_at=_TUESDAY - 1)


def test_thresholds_annotate_instead_of_dropping():
    plan = bn.assess_breach(
        ["us_tx"], subjects_affected=10, discovered_at=_TUESDAY)
    row = plan["obligations"][0]
    assert row["authority_below_threshold"] is True
    assert "250" in row["threshold_note"]
    unknown_count = bn.assess_breach(
        ["us_tx"], subjects_affected=0, discovered_at=_TUESDAY)
    assert unknown_count["obligations"][0]["authority_below_threshold"] is False


def test_qualitative_standards_carry_the_statutes_words():
    plan = bn.assess_breach(["us_ca", "ca_pipeda"], discovered_at=_TUESDAY)
    d = {o["regime"]: o for o in plan["obligations"]}
    assert d["us_ca"]["authority_due_at"] is None
    assert "expedient" in d["us_ca"]["subjects_deadline"]["text"]
    assert d["ca_pipeda"]["records_duty"].startswith("Keep records")


def test_unknown_regimes_are_refused_naming_the_absent_set():
    with pytest.raises(ValueError) as excinfo:
        bn.assess_breach(["in_dpdp"], discovered_at=_TUESDAY)
    assert "Absent by design" in str(excinfo.value)
    assert "qc_law25" in str(excinfo.value)
