"""Adversarial provider-cost authority and admission lifecycle regressions."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from maverick import provider_cost_cap as pcc
from maverick.budget import Budget, BudgetExceeded
from maverick.llm import LLM, MODEL_SONNET, LLMResponse


def _response(text: str = "ok") -> LLMResponse:
    return LLMResponse(
        text=text, thinking=None, tool_calls=[], stop_reason="end_turn"
    )


@pytest.fixture(autouse=True)
def _isolated_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)
    monkeypatch.delenv("MAVERICK_WORLD_BACKEND", raising=False)
    monkeypatch.delenv("MAVERICK_PG_DSN", raising=False)
    monkeypatch.setattr(pcc, "_shared_world_cache", None)


def test_local_reservations_atomically_bound_concurrent_admission(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(pcc, "caps_from_config", lambda: {"anthropic": 10.0})
    ledger = tmp_path / "provider-spend.json"
    admitted = []

    def attempt() -> None:
        try:
            admitted.append(
                pcc.reserve("anthropic", 1.0, path=ledger, now=1_750_000_000.0)
            )
        except pcc.ProviderCapExceeded:
            pass

    with ThreadPoolExecutor(max_workers=24) as pool:
        list(pool.map(lambda _index: attempt(), range(80)))

    assert len(admitted) == 10
    status = pcc.check(
        "anthropic", path=ledger, now=1_750_000_000.0
    )
    assert status.spent == 0.0
    assert status.reserved == 10.0
    assert status.remaining == 0.0

    for reservation in admitted[:4]:
        pcc.settle(reservation, 0.5, now=1_750_000_001.0)
    for reservation in admitted[4:]:
        pcc.release(reservation, now=1_750_000_001.0)
    final = pcc.check("anthropic", path=ledger, now=1_750_000_000.0)
    assert final.spent == 2.0
    assert final.reserved == 0.0


def test_reservation_completion_is_idempotent_and_state_monotonic(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(pcc, "caps_from_config", lambda: {"openai": 20.0})
    ledger = tmp_path / "provider-spend.json"
    reservation = pcc.reserve("openai", 8.0, path=ledger)
    assert reservation is not None

    first = pcc.settle(reservation, 3.25)
    again = pcc.settle(reservation, 3.25)
    assert first.spent == again.spent == 3.25
    assert first.reserved == again.reserved == 0.0
    with pytest.raises(pcc.ProviderCapAuthorityUnavailable):
        pcc.settle(reservation, 3.50)
    with pytest.raises(pcc.ProviderCapAuthorityUnavailable):
        pcc.release(reservation)


def test_corrupt_local_reservation_ledger_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(pcc, "caps_from_config", lambda: {"openai": 20.0})
    ledger = tmp_path / "provider-spend.json"
    ledger.write_text("{broken", encoding="utf-8")
    with pytest.raises(pcc.ProviderCapAuthorityUnavailable):
        pcc.reserve("openai", 1.0, path=ledger)
    assert ledger.read_text(encoding="utf-8") == "{broken"


def test_nonfinite_projection_and_zero_projection_at_cap_are_refused(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(pcc, "caps_from_config", lambda: {"openai": 10.0})
    ledger = tmp_path / "provider-spend.json"
    with pytest.raises(pcc.ProviderCapAuthorityUnavailable):
        pcc.reserve("openai", float("nan"), path=ledger)
    pcc.record("openai", 10.0, path=ledger)
    with pytest.raises(pcc.ProviderCapExceeded):
        pcc.reserve("openai", 0.0, path=ledger)


def test_world_reservation_predicate_is_atomic_under_threads(tmp_path):
    from maverick.world_model import WorldModel

    world = WorldModel(tmp_path / "world.db")
    admitted = []

    def attempt(index: int) -> None:
        allowed, _spent, _reserved = world.reserve_provider_spend(
            "2026-08-04",
            "anthropic",
            f"reservation-{index}",
            1.0,
            10.0,
            created_at=1_750_000_000.0,
        )
        if allowed:
            admitted.append(index)

    with ThreadPoolExecutor(max_workers=24) as pool:
        list(pool.map(attempt, range(80)))

    assert len(admitted) == 10
    assert world.get_provider_spend_status("2026-08-04", "anthropic") == (
        0.0,
        10.0,
    )


def test_configured_shared_authority_outage_never_falls_back_to_json(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(pcc, "caps_from_config", lambda: {"anthropic": 10.0})
    monkeypatch.setattr(
        "maverick.world_model_backends.is_postgres_configured", lambda: True
    )

    def unavailable():
        raise ConnectionError("database unavailable")

    monkeypatch.setattr("maverick.world_model.open_world", unavailable)
    with pytest.raises(pcc.ProviderCapAuthorityUnavailable):
        pcc.reserve("anthropic", 1.0)
    assert not (tmp_path / "provider_spend.json").exists()


def test_production_without_postgres_authority_fails_closed(monkeypatch):
    monkeypatch.setattr(pcc, "caps_from_config", lambda: {"anthropic": 10.0})
    monkeypatch.setattr(
        "maverick.world_model_backends.is_postgres_configured", lambda: False
    )
    monkeypatch.setattr(
        "maverick.production_scope.production_surface_required", lambda: True
    )
    with pytest.raises(pcc.ProviderCapAuthorityUnavailable):
        pcc.reserve("anthropic", 1.0)


def test_llm_concurrent_calls_cannot_both_cross_remaining_cap(monkeypatch):
    monkeypatch.setattr(pcc, "caps_from_config", lambda: {"anthropic": 10.0})
    monkeypatch.setattr("maverick.llm._safe_cap_projection", lambda *a, **k: 6.0)
    entered = threading.Event()
    release_call = threading.Event()
    calls = []

    class Client:
        def complete(self, **_kwargs):
            calls.append(1)
            entered.set()
            assert release_call.wait(timeout=5)
            return _response()

    llm = LLM(model=f"anthropic:{MODEL_SONNET}")
    monkeypatch.setattr(llm, "_get_client", lambda _provider: Client())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            llm.complete, "system", [{"role": "user", "content": "first"}]
        )
        assert entered.wait(timeout=5)
        with pytest.raises(pcc.ProviderCapExceeded):
            llm.complete("system", [{"role": "user", "content": "second"}])
        release_call.set()
        assert first.result(timeout=5).text == "ok"
    assert calls == [1]
    status = pcc.check("anthropic")
    assert status.spent == 6.0
    assert status.reserved == 0.0


def test_llm_releases_cap_hold_when_budget_blocks_before_provider(
    monkeypatch,
):
    monkeypatch.setattr(pcc, "caps_from_config", lambda: {"anthropic": 10.0})
    monkeypatch.setattr("maverick.llm._safe_cap_projection", lambda *a, **k: 6.0)
    monkeypatch.setattr("maverick.llm._estimate_call_cost", lambda *a, **k: 2.0)

    class Client:
        def complete(self, **_kwargs):
            raise AssertionError("budget refusal must precede provider dispatch")

    llm = LLM(model=f"anthropic:{MODEL_SONNET}")
    monkeypatch.setattr(llm, "_get_client", lambda _provider: Client())
    with pytest.raises(BudgetExceeded):
        llm.complete(
            "system",
            [{"role": "user", "content": "hello"}],
            budget=Budget(max_dollars=1.0),
        )
    status = pcc.check("anthropic")
    assert status.spent == status.reserved == 0.0


def test_llm_estimation_failure_with_active_cap_blocks_before_provider(
    monkeypatch,
):
    monkeypatch.setattr(pcc, "caps_from_config", lambda: {"anthropic": 10.0})
    monkeypatch.setattr(
        "maverick.llm._estimate_call_cost",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("pricing unavailable")),
    )
    called = []

    class Client:
        def complete(self, **_kwargs):
            called.append(1)
            return _response()

    llm = LLM(model=f"anthropic:{MODEL_SONNET}")
    monkeypatch.setattr(llm, "_get_client", lambda _provider: Client())
    with pytest.raises(pcc.ProviderCapAuthorityUnavailable):
        llm.complete("system", [{"role": "user", "content": "hello"}])
    assert called == []


def test_ambiguous_provider_failure_consumes_projection(monkeypatch):
    monkeypatch.setattr(pcc, "caps_from_config", lambda: {"anthropic": 10.0})
    monkeypatch.setattr("maverick.llm._safe_cap_projection", lambda *a, **k: 6.0)
    calls = []

    class Client:
        def complete(self, **_kwargs):
            calls.append(1)
            raise TimeoutError("response lost after dispatch")

    llm = LLM(model=f"anthropic:{MODEL_SONNET}")
    monkeypatch.setattr(llm, "_get_client", lambda _provider: Client())
    with pytest.raises(TimeoutError):
        llm.complete("system", [{"role": "user", "content": "first"}])
    assert pcc.check("anthropic").spent == 6.0
    with pytest.raises(pcc.ProviderCapExceeded):
        llm.complete("system", [{"role": "user", "content": "retry"}])
    assert calls == [1]


@pytest.mark.asyncio
async def test_hedge_requires_its_own_provider_cap_reservation(
    monkeypatch,
):
    monkeypatch.setattr(pcc, "caps_from_config", lambda: {"anthropic": 10.0})
    monkeypatch.setattr("maverick.llm._safe_cap_projection", lambda *a, **k: 6.0)
    monkeypatch.setenv("MAVERICK_LLM_HEDGE_MS", "1")
    calls = []

    class Client:
        async def complete_async(self, **_kwargs):
            import asyncio

            calls.append(1)
            await asyncio.sleep(0.03)
            return _response()

    llm = LLM(model=f"anthropic:{MODEL_SONNET}")
    monkeypatch.setattr(llm, "_get_client", lambda _provider: Client())
    out = await llm.complete_async(
        "system", [{"role": "user", "content": "hello"}]
    )
    assert out.text == "ok"
    assert calls == [1]
    assert pcc.check("anthropic").spent == 6.0


@pytest.mark.asyncio
async def test_both_dispatched_hedge_attempts_are_settled(monkeypatch):
    monkeypatch.setattr(pcc, "caps_from_config", lambda: {"anthropic": 20.0})
    monkeypatch.setattr("maverick.llm._safe_cap_projection", lambda *a, **k: 6.0)
    monkeypatch.setenv("MAVERICK_LLM_HEDGE_MS", "1")
    call_number = 0
    call_lock = threading.Lock()

    class Client:
        async def complete_async(self, **_kwargs):
            import asyncio

            nonlocal call_number
            with call_lock:
                call_number += 1
                mine = call_number
            await asyncio.sleep(0.20 if mine == 1 else 0.005)
            return _response(f"attempt-{mine}")

    llm = LLM(model=f"anthropic:{MODEL_SONNET}")
    monkeypatch.setattr(llm, "_get_client", lambda _provider: Client())
    out = await llm.complete_async(
        "system", [{"role": "user", "content": "hello"}]
    )
    assert out.text == "attempt-2"
    assert call_number == 2
    status = pcc.check("anthropic")
    assert status.spent == 12.0
    assert status.reserved == 0.0


def test_authority_failure_is_not_provider_failover_retryable():
    from maverick.provider_failover import should_retry_llm_error

    assert not should_retry_llm_error(
        pcc.ProviderCapAuthorityUnavailable("shared ledger unavailable")
    )


def test_v40_migrations_install_reservation_authority():
    from maverick import world_model
    from maverick.world_model_backends import postgres

    assert world_model.SCHEMA_VERSION >= 40
    assert 40 in world_model.MIGRATIONS
    sqlite_sql = " ".join(world_model.MIGRATIONS[40]).lower()
    postgres_sql = " ".join(dict(postgres.MIGRATIONS)[40]).lower()
    for sql in (sqlite_sql, postgres_sql):
        assert "provider_cost_reservations" in sql
        assert "reserved" in sql
        assert "active" in sql and "settled" in sql and "released" in sql


class _ScriptedCursor:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.results.pop(0) if self.results else None


class _ScriptedTransaction:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, *_args):
        return False


def _postgres_world_with_results(results):
    from maverick.world_model_backends.postgres import PostgresWorldModel

    cursor = _ScriptedCursor(results)
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    world._tx = lambda: _ScriptedTransaction(cursor)
    return world, cursor


def test_postgres_reservation_uses_one_atomic_predicate_transaction():
    world, cursor = _postgres_world_with_results([(4.0, 6.0)])
    admitted = world.reserve_provider_spend(
        "2026-08-04",
        "anthropic",
        "reservation-1",
        6.0,
        10.0,
        created_at=1.0,
    )
    assert admitted == (True, 4.0, 6.0)
    statements = [sql for sql, _params in cursor.executed]
    assert "dollars + reserved + %s <= %s" in statements[1]
    assert "RETURNING dollars, reserved" in statements[1]
    assert "INSERT INTO provider_cost_reservations" in statements[2]


def test_postgres_denied_reservation_does_not_create_lifecycle_row():
    world, cursor = _postgres_world_with_results([None, (5.0, 5.0)])
    admitted = world.reserve_provider_spend(
        "2026-08-04",
        "anthropic",
        "reservation-2",
        1.0,
        10.0,
        created_at=1.0,
    )
    assert admitted == (False, 5.0, 5.0)
    assert not any(
        "INSERT INTO provider_cost_reservations" in sql
        for sql, _params in cursor.executed
    )


def test_postgres_settlement_locks_lifecycle_then_moves_hold_to_spend():
    world, cursor = _postgres_world_with_results(
        [("2026-08-04", "anthropic", 6.0, None, "ACTIVE"), (3.0, 0.0)]
    )
    assert world.settle_provider_spend(
        "reservation-3", 3.0, completed_at=2.0
    ) == (3.0, 0.0)
    statements = [sql for sql, _params in cursor.executed]
    assert statements[0].endswith("WHERE reservation_id = %s FOR UPDATE")
    assert "reserved = reserved - %s, dollars = dollars + %s" in statements[1]
    assert "state = 'SETTLED'" in statements[2]
