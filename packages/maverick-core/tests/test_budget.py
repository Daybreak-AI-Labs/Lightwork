"""Budget cap tests."""
from __future__ import annotations

import logging
import pickle

import maverick.budget as budget_mod
import pytest
from maverick.budget import (
    Budget,
    BudgetExceeded,
    UnpricedModelError,
    _lookup_price,
)


def test_budget_under_caps():
    b = Budget(max_dollars=1.0, max_input_tokens=10_000)
    b.record_tokens(1000, 500)
    assert b.input_tokens == 1000
    assert b.output_tokens == 500
    assert b.dollars < 1.0


def test_budget_input_token_excess():
    b = Budget(max_input_tokens=100)
    with pytest.raises(BudgetExceeded):
        b.record_tokens(200, 0)


def test_budget_output_token_excess():
    b = Budget(max_output_tokens=100)
    with pytest.raises(BudgetExceeded):
        b.record_tokens(0, 200)


def test_budget_tool_call_excess():
    b = Budget(max_tool_calls=2)
    b.record_tool_call()
    b.record_tool_call()
    with pytest.raises(BudgetExceeded):
        b.record_tool_call()


def test_budget_summary_contains_expected_fields():
    b = Budget()
    s = b.summary()
    assert "tokens" in s
    assert "wall" in s
    assert "tools" in s


@pytest.fixture(autouse=True)
def _reset_unpriced_warned():
    saved = set(budget_mod._UNPRICED_WARNED)
    budget_mod._UNPRICED_WARNED.clear()
    yield
    budget_mod._UNPRICED_WARNED.clear()
    budget_mod._UNPRICED_WARNED.update(saved)


def test_known_model_priced_from_table():
    in_rate, out_rate = _lookup_price("claude-sonnet-4-6")
    assert (in_rate, out_rate) == (3.0, 15.0)


def test_unknown_model_estimate_warns_once_then_falls_back(monkeypatch, caplog):
    monkeypatch.delenv("MAVERICK_BILLING_STRICT", raising=False)
    monkeypatch.setattr("maverick.config.get_budget_overrides", dict)
    with caplog.at_level(logging.WARNING, logger="maverick.budget"):
        first = _lookup_price("totally-made-up-model", estimate_only=True)
        second = _lookup_price("totally-made-up-model", estimate_only=True)
    assert first == second == (3.0, 15.0)  # documented Sonnet fallback estimate
    unpriced = [r for r in caplog.records if "no verified price" in r.getMessage()]
    assert len(unpriced) == 1  # warned once, not per call


def test_none_model_does_not_warn(monkeypatch, caplog):
    monkeypatch.delenv("MAVERICK_BILLING_STRICT", raising=False)
    with caplog.at_level(logging.WARNING, logger="maverick.budget"):
        assert _lookup_price(None) == (3.0, 15.0)
    assert not [r for r in caplog.records if "no verified price" in r.getMessage()]


def test_strict_pricing_raises_on_unknown_model(monkeypatch):
    monkeypatch.setenv("MAVERICK_BILLING_STRICT", "1")
    with pytest.raises(UnpricedModelError, match="no verified"):
        _lookup_price("totally-made-up-model")


def test_strict_pricing_allows_known_and_selfhosted(monkeypatch):
    monkeypatch.setenv("MAVERICK_BILLING_STRICT", "1")
    # A table model is fine.
    assert _lookup_price("claude-sonnet-4-6") == (3.0, 15.0)
    # A self-hosted ($0) model is priced, not refused.
    assert _lookup_price("ollama:llama-4-maverick") == (0.0, 0.0)


def test_strict_pricing_via_config(monkeypatch):
    monkeypatch.delenv("MAVERICK_BILLING_STRICT", raising=False)
    monkeypatch.setattr("maverick.config.get_budget_overrides",
                        lambda: {"strict_pricing": True})
    with pytest.raises(UnpricedModelError):
        _lookup_price("totally-made-up-model")


def test_cache_hit_rate_consolidates_across_providers():
    b = Budget(max_dollars=1000.0, max_input_tokens=10_000_000)
    # No traffic yet -> 0.0, never divides by zero.
    assert b.cache_hit_rate() == 0.0
    # 200 billable input + 800 cache-read => 80% served from cache.
    b.record_tokens(200, 10, cache_read_tok=800)
    assert b.cache_hit_rate() == 0.8
    stats = b.cache_stats()
    assert stats["cache_read_tokens"] == 800
    assert stats["billable_input_tokens"] == 200
    assert stats["hit_rate"] == 0.8
    assert "hit_rate" in b.summary()


@pytest.mark.parametrize(
    "bad_multiplier",
    [float("nan"), float("inf"), float("-inf"), -0.1, True, "unpriced"],
)
def test_cache_read_multiplier_rejects_invalid_values_before_mutation(bad_multiplier):
    budget = Budget(max_dollars=0.000_001)
    before = budget.summary(), budget.dollars, budget.pricing_snapshot()

    with pytest.raises(BudgetExceeded, match="cache read multiplier"):
        budget.record_tokens(
            0,
            0,
            cache_read_tok=1,
            cache_read_mult=bad_multiplier,
        )

    assert (budget.summary(), budget.dollars, budget.pricing_snapshot()) == before
    budget.dollars = budget.max_dollars
    with pytest.raises(BudgetExceeded):
        budget.reserve(0.000_001)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("dollars", float("nan")),
        ("dollars", float("inf")),
        ("_reserved", float("nan")),
        ("input_tokens", -1),
        ("output_tokens", 1.5),
        ("tool_calls", True),
    ],
)
def test_check_rejects_corrupted_accounting_state(field, bad_value):
    budget = Budget()
    setattr(budget, field, bad_value)

    with pytest.raises(BudgetExceeded, match="invalid"):
        budget.check()


def test_restore_rejects_nonfinite_accounting_state():
    original = Budget()
    state = original.__getstate__()
    state["dollars"] = float("nan")
    restored = Budget.__new__(Budget)

    with pytest.raises(BudgetExceeded, match="invalid dollars"):
        restored.__setstate__(state)


def test_token_operation_replays_once_after_budget_is_serialized():
    budget = Budget(max_output_tokens=10, max_dollars=100)
    operation = "a" * 64
    first = budget.record_tokens(
        0, 1, model="codex_cli:gpt-5.5", operation_id=operation,
    )
    restored = pickle.loads(pickle.dumps(budget))
    replayed = restored.record_tokens(
        0, 1, model="codex_cli:gpt-5.5", operation_id=operation,
    )

    assert replayed == first
    assert restored.output_tokens == 1
    assert restored.dollars == budget.dollars
    with pytest.raises(BudgetExceeded, match="different usage"):
        restored.record_tokens(
            0, 2, model="codex_cli:gpt-5.5", operation_id=operation,
        )
    assert restored.output_tokens == 1


def test_restore_rejects_corrupted_token_operation_state():
    original = Budget()
    state = original.__getstate__()
    state["_token_operations"] = {
        "b" * 64: {
            "request": "c" * 64,
            "dollars": float("nan"),
            "pricing_evidence": {},
            "failure": None,
        },
    }
    restored = Budget.__new__(Budget)

    with pytest.raises(BudgetExceeded, match="stored token charge"):
        restored.__setstate__(state)
