"""One mapper turns a run's internal stop text into plain words, the right
next step and a link; raw exception text is admin-only technical detail."""
from __future__ import annotations

import pytest
from maverick.stop_reasons import RAW_TEXT_LIMIT, explain_stop

RAW_TENANT = ("over quota: tenant policy unavailable or inactive: "
              "tenant is suspended or unknown: 'owner-browser'")


def test_tenant_suspended_or_unknown_maps_to_workspaces():
    stop = explain_stop(RAW_TENANT)
    assert stop.kind == "workspace_paused"
    assert stop.message == "This workspace is paused or hasn't been set up. Open Workspaces to resume it."
    assert stop.link == "/workspaces"
    assert "owner-browser" not in stop.message + stop.action
    assert "tenant" not in (stop.message + stop.action).lower()
    assert stop.technical == RAW_TENANT


@pytest.mark.parametrize("raw", [
    "over quota: principal 'user:local' is over its daily spend quota ($10.00 >= $5.00)",
    "over quota: tenant 'acme' is over its daily spend cap ($12.00 >= $5.00)",
    "over quota: Your spending limit of $5.00 per day has been reached ($6.00 spent today). "
    "An administrator can change it in Admin > Spending limits.",
    "Paused: the organization spending allowance cannot admit the next call.\nDetail: x",
    "Stopped: budget exhausted ($1.00 of $1.00 cap). Raise the cap and resume: maverick resume 3",
])
def test_real_spending_failures_get_the_spending_limit_advice(raw):
    stop = explain_stop(raw)
    assert stop.kind == "spending_limit"
    assert stop.link == "/spending-limits"
    assert "user:local" not in stop.message and "'acme'" not in stop.message


def test_workspace_failure_is_not_mistaken_for_a_spending_limit():
    # The raw text starts with "over quota:" but the cause is the workspace.
    assert explain_stop(RAW_TENANT).kind != "spending_limit"


def test_unpriced_model_points_to_known_price_setup_without_exposing_the_id():
    raw = ("unpriced model: no verified USD price for model "
           "'openai_compatible:private-gateway-id'; refusing to bill at an estimate")
    stop = explain_stop(raw)
    assert stop.kind == "unpriced_model"
    assert "could not verify" in stop.message
    assert "Choose a model with a known price" in stop.action
    assert "Settings > Require known prices" in stop.action
    assert stop.link == "/settings"
    assert "private-gateway-id" not in stop.message + stop.action
    assert "strict_pricing=false" not in stop.message + stop.action
    assert stop.technical == raw


@pytest.mark.parametrize("raw", [
    "provider not ready: ANTHROPIC_API_KEY is not set",
    "httpx.ConnectError: [Errno 111] connection refused",
    "The selected AI model route is incomplete (anthropic: api_key)",
])
def test_provider_problems_link_to_connections(raw):
    stop = explain_stop(raw)
    assert stop.kind == "provider_not_ready"
    assert stop.link == "/connections"
    assert "ConnectError" not in stop.message


@pytest.mark.parametrize("raw", [
    "PermissionError: insufficient role for this action",
    "BLOCKED: goal is bound to a different execution principal",
    "permission denied: user:bob may not use suite finance",
])
def test_permission_denied_says_who_can_grant_it(raw):
    stop = explain_stop(raw)
    assert stop.kind == "permission_denied"
    assert "administrator" in stop.action.lower()
    assert stop.link == "/users"
    assert "user:bob" not in stop.message + stop.action


def test_halt_and_internal_errors_are_plain():
    assert explain_stop("halted").kind == "halted"
    stop = explain_stop("internal error")
    assert stop.kind == "internal_error"
    assert "Traceback" not in stop.message
    stop = explain_stop("Traceback (most recent call last):\n  File \"/x.py\"\nKeyError: 'q'")
    assert stop.kind == "internal_error"
    assert "KeyError" not in stop.message


def test_plain_kernel_sentences_pass_through_and_technical_ones_do_not():
    plain = ("Paused: the review checkpoint stopped this run at step 3 because "
             "the reviewer did not approve continuing.")
    assert explain_stop(plain).message == plain
    technical = "RuntimeError: /home/user/.maverick/tenants/acme/world.db is locked"
    stop = explain_stop(technical)
    assert stop.kind == "internal_error"
    assert "/home/user" not in stop.message and "RuntimeError" not in stop.message
    assert stop.technical == technical


def test_empty_result_is_a_generic_stop_with_no_technical_text():
    stop = explain_stop(None)
    assert stop.kind == "other"
    assert stop.message and stop.technical == ""
    assert stop.as_dict() == {"kind": "other", "message": stop.message, "action": stop.action,
                              "link": None, "link_label": None}
    assert "technical" in stop.as_dict(include_technical=True)


def test_technical_text_is_bounded():
    stop = explain_stop("x" * (RAW_TEXT_LIMIT * 3))
    assert len(stop.technical) <= RAW_TEXT_LIMIT
