"""Circuit breaker must only trip on genuine provider-transport failures.

PLATFORM_IMPROVEMENT_AUDIT.md W1-2: every exception escaping ``client.complete``
used to count as a provider failure, including ``BudgetExceeded`` raised
*after* a successful API call, ``UnpricedModelError``, and ordinary 4xx client
errors. A single fan-out goal that hits its dollar cap (N parallel sub-agents
each raising BudgetExceeded) could trip the breaker and take the provider
offline for every concurrent goal and tenant sharing the process.
"""
from __future__ import annotations

import pytest


class _StatusError(Exception):
    def __init__(self, status_code, message="boom"):
        super().__init__(message)
        self.status_code = status_code


class TestIsProviderTransportFailure:
    def test_budget_exceeded_is_never_a_transport_failure(self):
        from maverick.budget import BudgetExceeded
        from maverick.llm import _is_provider_transport_failure
        assert _is_provider_transport_failure(BudgetExceeded("over cap")) is False

    def test_unpriced_model_error_is_never_a_transport_failure(self):
        """UnpricedModelError subclasses BudgetExceeded."""
        from maverick.budget import UnpricedModelError
        from maverick.llm import _is_provider_transport_failure
        assert _is_provider_transport_failure(UnpricedModelError("no rate")) is False

    def test_model_not_allowed_is_never_a_transport_failure(self):
        from maverick.llm import ModelNotAllowedError, _is_provider_transport_failure
        assert _is_provider_transport_failure(ModelNotAllowedError("denied")) is False

    def test_preflight_failed_is_never_a_transport_failure(self):
        from maverick.llm import _is_provider_transport_failure
        from maverick.preflight import PreflightFailed
        exc = PreflightFailed(model="claude-opus-4-8", estimated=500_000,
                              limit=200_000, max_tokens=4096)
        assert _is_provider_transport_failure(exc) is False

    def test_egress_blocked_is_never_a_transport_failure(self):
        from maverick.enterprise import EgressBlocked
        from maverick.llm import _is_provider_transport_failure
        assert _is_provider_transport_failure(EgressBlocked("denied host")) is False

    def test_4xx_client_error_is_never_a_transport_failure(self):
        from maverick.llm import _is_provider_transport_failure
        assert _is_provider_transport_failure(_StatusError(400, "bad request")) is False
        assert _is_provider_transport_failure(_StatusError(401, "unauthorized")) is False
        assert _is_provider_transport_failure(_StatusError(404, "not found")) is False

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    def test_rate_limit_and_server_errors_are_transport_failures(self, status):
        from maverick.llm import _is_provider_transport_failure
        assert _is_provider_transport_failure(_StatusError(status)) is True

    def test_connection_error_is_a_transport_failure(self):
        from maverick.llm import _is_provider_transport_failure
        assert _is_provider_transport_failure(ConnectionError("connection reset")) is True

    def test_timeout_error_is_a_transport_failure(self):
        from maverick.llm import _is_provider_transport_failure
        assert _is_provider_transport_failure(TimeoutError("deadline exceeded")) is True


class TestFeedCircuitClassificationIntegration:
    """Prove the classification is actually wired into the dispatch path,
    not just defined and unused."""

    def _client_raising(self, exc):
        class _Client:
            def complete(self, **kwargs):
                raise exc
            async def complete_async(self, **kwargs):
                raise exc
        return _Client()

    @pytest.mark.parametrize("guard", ["cap", "accounting"])
    @pytest.mark.parametrize("asynchronous", [False, True])
    def test_local_accounting_refusal_does_not_change_provider_health(self, monkeypatch, guard, asynchronous):
        import asyncio
        from types import SimpleNamespace

        from maverick.budget import Budget
        from maverick.llm import LLM
        from maverick.provider_cost_cap import ProviderAccountingUnavailable, ProviderCapExceeded

        error = ProviderAccountingUnavailable() if guard == "accounting" else ProviderCapExceeded("anthropic", 10, 10, "2026-10")
        observed = []
        monkeypatch.setattr("maverick.llm._feed_circuit", lambda *args: observed.append("circuit"))
        monkeypatch.setattr("maverick.llm._note_bandit", lambda *args: observed.append("routing"))
        monkeypatch.setattr("maverick.provider_health.get", lambda: SimpleNamespace(record=lambda *args, **kwargs: observed.append("health")))
        monkeypatch.setattr("maverick.llm._run_preflight", lambda *args, **kwargs: None)
        monkeypatch.setattr("maverick.llm._enforce_provider_cap", lambda *args, **kwargs: None)
        monkeypatch.setattr(LLM, "_get_client", lambda self, provider: self._unused_client)
        client = SimpleNamespace(complete=lambda **kwargs: pytest.fail("A refused call cannot reach the provider"))

        def refuse(*args, **kwargs):
            raise error

        monkeypatch.setattr("maverick.llm._reserve_provider_call", refuse)
        llm = LLM(model="anthropic:claude-sonnet-4-6")
        llm._unused_client = client
        for _ in range(5):
            with pytest.raises(type(error)):
                method = llm.complete_async if asynchronous else llm.complete
                result = method("sys", [{"role": "user", "content": "hi"}], budget=Budget(max_dollars=100.0))
                if asynchronous:
                    asyncio.run(result)
        assert observed == []  # A local refusal neither trips nor resets shared health.

    def test_complete_feeds_circuit_success_false_for_budget_exceeded(self, monkeypatch):
        from maverick.budget import Budget, BudgetExceeded
        from maverick.llm import LLM

        recorded = []
        monkeypatch.setattr("maverick.llm._feed_circuit",
                            lambda provider, error: recorded.append((provider, error)))
        monkeypatch.setattr("maverick.llm._run_preflight", lambda *a, **k: None)
        client = self._client_raising(BudgetExceeded("over cap"))
        monkeypatch.setattr(LLM, "_get_client", lambda self, provider: client)

        with pytest.raises(BudgetExceeded):
            LLM(model="anthropic:claude-sonnet-4-6").complete(
                "sys", [{"role": "user", "content": "hi"}],
                budget=Budget(max_dollars=100.0),
            )
        assert recorded == [("anthropic", False)]

    def test_complete_feeds_circuit_failure_true_for_rate_limit(self, monkeypatch):
        from maverick.budget import Budget
        from maverick.llm import LLM

        recorded = []
        monkeypatch.setattr("maverick.llm._feed_circuit",
                            lambda provider, error: recorded.append((provider, error)))
        monkeypatch.setattr("maverick.llm._run_preflight", lambda *a, **k: None)
        client = self._client_raising(_StatusError(429, "rate limited"))
        monkeypatch.setattr(LLM, "_get_client", lambda self, provider: client)

        with pytest.raises(_StatusError):
            LLM(model="anthropic:claude-sonnet-4-6").complete(
                "sys", [{"role": "user", "content": "hi"}],
                budget=Budget(max_dollars=100.0),
            )
        assert recorded == [("anthropic", True)]
