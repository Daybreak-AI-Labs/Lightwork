"""Round-1 provider hardening regressions.

- A missing ``usage`` block aborts the turn for PAID providers (fail-closed) but
  NOT for self-hosted ollama/vllm/tgi (USAGE_REQUIRED=False): those price at $0,
  and many local OpenAI-compatible servers omit usage on non-streaming calls.
- Unparseable tool-call arguments are surfaced (warning), not silently swallowed.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from maverick.budget import Budget, UsageAccountingError
from maverick.providers.ollama_provider import OllamaClient
from maverick.providers.openai_provider import OpenAIClient, _strict_usage_count
from maverick.providers.openrouter_provider import OpenRouterClient


class _Msg:
    def __init__(self, content="hi", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.reasoning_content = None


class _Choice:
    def __init__(self, msg, finish_reason="stop"):
        self.message = msg
        self.finish_reason = finish_reason


class _Resp:
    """An OpenAI-compatible response with NO usage block."""
    def __init__(self, msg, usage=None):
        self.choices = [_Choice(msg)]
        self.usage = usage


def test_paid_provider_missing_usage_aborts():
    # USAGE_REQUIRED is True on the base/paid client -> fail closed.
    budget = Budget(max_dollars=10.0)
    with pytest.raises(UsageAccountingError, match="missing token usage"):
        OpenAIClient._from_response(_Resp(_Msg()), budget, model="gpt-4o",
                                    usage_required=True)


@pytest.mark.parametrize("invalid", [0.5, -0.5, True, False, "", [], {}])
@pytest.mark.parametrize("field", ["prompt_tokens", "completion_tokens"])
def test_paid_provider_rejects_noninteger_usage(field, invalid):
    usage = {"prompt_tokens": 10, "completion_tokens": 5}
    usage[field] = invalid
    budget = Budget(max_dollars=10.0)
    with pytest.raises(UsageAccountingError, match="invalid OpenAI usage"):
        OpenAIClient._from_response(
            _Resp(_Msg(), SimpleNamespace(**usage)), budget, model="gpt-4o",
        )
    assert budget.input_tokens == 0
    assert budget.output_tokens == 0


@pytest.mark.parametrize("value", [5, 5.0, "5"])
def test_usage_accepts_valid_integral_counts(value):
    assert _strict_usage_count(value, "test") == 5


@pytest.mark.parametrize("missing", ["prompt_tokens", "completion_tokens"])
def test_paid_provider_rejects_missing_individual_usage_count(missing):
    usage = {"prompt_tokens": 10, "completion_tokens": 5}
    del usage[missing]
    with pytest.raises(UsageAccountingError, match="missing token count"):
        OpenAIClient._from_response(
            _Resp(_Msg(), SimpleNamespace(**usage)), Budget(max_dollars=10.0), model="gpt-4o",
        )


def test_self_hosted_missing_usage_does_not_abort():
    # ollama/vllm/tgi set USAGE_REQUIRED=False; a usage-less response must NOT
    # raise -- it records a $0 zero-token call and returns the text.
    assert OllamaClient.USAGE_REQUIRED is False
    budget = Budget(max_dollars=10.0)
    r = OpenAIClient._from_response(_Resp(_Msg("local answer")), budget,
                                    model="llama3.3:70b",
                                    price_model_prefix="ollama:",
                                    usage_required=False)
    assert r.text == "local answer"
    # $0 self-hosted: nothing was billed.
    assert budget.dollars == 0.0


def test_openrouter_free_model_response_records_the_provider_qualified_price(monkeypatch):
    from maverick import model_discovery

    monkeypatch.setenv("MAVERICK_BILLING_STRICT", "1")
    monkeypatch.setattr(model_discovery, "free_models",
                        lambda provider: ["vendor/free"] if provider == "openrouter" else [])
    budget = Budget(max_dollars=1.0)
    response = _Resp(_Msg("answer"), SimpleNamespace(prompt_tokens=10, completion_tokens=5))

    result = OpenRouterClient._from_response(
        response, budget, model="vendor/free",
        price_model_prefix=OpenRouterClient.PRICE_MODEL_PREFIX,
    )

    assert result.text == "answer"
    assert budget.dollars == 0.0


class _BadArgsToolCall:
    class function:
        name = "do_thing"
        arguments = "{not valid json"
    id = "call_1"


def test_unparseable_tool_args_warns_and_uses_empty(caplog):
    msg = _Msg(tool_calls=[_BadArgsToolCall()])
    with caplog.at_level(logging.WARNING, logger="maverick.providers.openai_provider"):
        r = OpenAIClient._from_response(_Resp(msg), None)
    assert r.tool_calls and r.tool_calls[0].input == {}
    assert any("unparseable arguments" in rec.message for rec in caplog.records)


class _SecretBadArgsToolCall:
    class function:
        name = "exfiltrate"
        arguments = '{"api_key":"sk-ABCDEFGHIJKLMNOPQRSTUV1234567890", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"'
    id = "call_secret"


def test_unparseable_tool_args_warning_scrubs_raw_arguments(caplog):
    msg = _Msg(tool_calls=[_SecretBadArgsToolCall()])
    with caplog.at_level(logging.WARNING, logger="maverick.providers.openai_provider"):
        r = OpenAIClient._from_response(_Resp(msg), None)
    assert r.tool_calls and r.tool_calls[0].input == {}
    messages = "\n".join(rec.message for rec in caplog.records)
    assert "unparseable arguments" in messages
    assert "sk-ABCDEFGHIJKLMNOPQRSTUV1234567890" not in messages
    assert "abcdefghijklmnopqrstuvwxyz123456" not in messages
    assert "[REDACTED:openai_key]" in messages
    assert "[REDACTED:bearer]" in messages
