from maverick_dashboard.template_outcomes_api import project_report


def test_all_planned_cases_remain_denominator_and_missing_cost_is_unknown():
    report = {"cases": [
        {"status": "paired", "source": {"status": "done"}, "measurement": {"corrections": 1}},
        {"status": "incomplete", "source": {"status": "failed"}},
        {"status": "incomplete", "source": {"status": "cancelled"}},
        {"status": "unavailable"},
    ], "groups": {"all": {"accepted": 1, "reported_cost_difference": "10", "currency": "USD"}}}
    result = project_report(report)
    assert result["planned_cases"] == 4 and result["acceptance_rate"] == 0.25
    assert result["failed_cases"] == result["cancelled_cases"] == 1
    assert result["correction_observed_cases"] == 1 and result["correction_rate"] == 0.25
    assert result["reported_cost_difference"] is None and not result["reported_cost_complete"]
    assert result["attempt_count"] is None and result["provider_invoice_verified"] is False


def test_mixed_currencies_or_missing_cost_family_never_form_complete_total():
    from maverick.outcome_accounting import COST_COVERAGE_FAMILIES

    measurement = {"corrections": 0, "ai_total_cost": "1", "baseline_cost": "2",
                   "cost_coverage": {key: {"status": "included"} for key in COST_COVERAGE_FAMILIES}}
    report = {"cases": [{"status": "paired", "measurement": measurement}],
              "groups": {"all": {"accepted": 1, "reported_cost_difference": None, "currency": None}}}
    assert project_report(report)["reported_cost_complete"] is False
    report["groups"]["all"].update(reported_cost_difference="1", currency="USD")
    assert project_report(report)["reported_cost_complete"] is True
    measurement["cost_coverage"].pop(next(iter(measurement["cost_coverage"])))
    assert project_report(report)["reported_cost_difference"] is None
