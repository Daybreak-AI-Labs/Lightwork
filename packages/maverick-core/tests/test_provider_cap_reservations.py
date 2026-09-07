"""Actual durable provider admission; synthetic local/shared stores, no network."""
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from maverick import provider_cost_cap as cap


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(cap, "caps_from_config", lambda: {"openai": 20.0})
    monkeypatch.setattr(cap, "period_from_config", lambda: "day")
    return tmp_path / "provider_spend.json"


def test_existing_unreadable_accounting_is_preserved_and_blocks(ledger):
    cap.record("openai", 25, path=ledger, now=0)
    ledger.write_text("{broken accounting", encoding="utf-8")
    before = ledger.read_bytes()
    with pytest.raises(cap.ProviderAccountingUnavailable):
        cap.enforce("openai", projected_dollars=1, path=ledger, now=0)
    cap.record("openai", 1, path=ledger, now=0)
    assert ledger.read_bytes() == before


def test_atomic_admission_counts_other_callers_and_keeps_abandoned_holds(ledger):
    cap.record("openai", 18, path=ledger, now=0)

    def admit(index):
        try:
            return cap.reserve("openai", 2, path=ledger, now=0, request_id=f"call-{index}")
        except cap.ProviderCapExceeded:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = [hold for hold in pool.map(admit, range(8)) if hold is not None]
    assert len(admitted) == 1
    assert cap.check("openai", path=ledger, now=0).remaining == 0
    # A new reader cannot auto-expire an abandoned process's durable hold.
    with pytest.raises(cap.ProviderCapExceeded):
        cap.reserve("openai", 1, path=ledger, now=30, request_id="after-crash")
    cap.settle(admitted[0], 1)
    cap.settle(admitted[0], 1)
    assert cap.check("openai", path=ledger, now=0).spent == 19
    with pytest.raises(cap.ProviderAccountingUnavailable):
        cap.settle(admitted[0], 0)


def test_ambiguous_settlement_does_not_refund_and_late_receipt_is_idempotent(ledger):
    held = cap.reserve("openai", 5, path=ledger, now=0, request_id="ambiguous-call")
    cap.settle(held, None)
    assert cap.check("openai", path=ledger, now=0).reserved == 5
    with pytest.raises(cap.ProviderAccountingUnavailable):
        cap.reserve("openai", 1, path=ledger, now=0, request_id="after-unknown")
    cap.settle(held, 3)
    assert cap.check("openai", path=ledger, now=0).spent == 3
    cap.settle(held, 3)
    assert cap.check("openai", path=ledger, now=0).spent == 3


def test_settlement_is_bound_to_original_period_and_missing_ledger_fails_closed(ledger):
    held = cap.reserve("openai", 5, path=ledger, now=0, request_id="period-bound")
    cap.settle(held, 3)
    assert cap.check("openai", path=ledger, now=0).spent == 3
    assert cap.check("openai", path=ledger, now=86400).spent == 0
    ledger.unlink()
    with pytest.raises(cap.ProviderAccountingUnavailable):
        cap.enforce("openai", path=ledger, now=0)


def test_shared_authority_unavailable_never_falls_back(ledger, monkeypatch):
    monkeypatch.setattr("maverick.world_model_backends.is_postgres_configured", lambda: True)
    monkeypatch.setattr(cap, "_shared_world_cache", None)
    monkeypatch.setattr("maverick.world_model.open_world", lambda: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(cap, "_ledger_path", lambda: ledger)
    with pytest.raises(cap.ProviderAccountingUnavailable):
        cap.reserve("openai", 1)
    assert not ledger.exists()


def test_shared_dsn_switch_selects_new_authority_but_settles_old_hold_on_its_original_store(ledger, monkeypatch, tmp_path):
    from maverick.world_model import WorldModel
    first, second = WorldModel(tmp_path / "first.db"), WorldModel(tmp_path / "second.db")
    monkeypatch.setattr("maverick.world_model_backends.is_postgres_configured", lambda: True)
    monkeypatch.setattr(cap, "_shared_world_cache", None)
    monkeypatch.setattr("maverick.world_model.open_world", lambda: first if os.environ["MAVERICK_PG_DSN"] == "host=first.invalid" else second)
    try:
        monkeypatch.setenv("MAVERICK_PG_DSN", "host=first.invalid")
        held = cap.reserve("openai", 3, now=0, request_id="original-authority")
        monkeypatch.setenv("MAVERICK_PG_DSN", "host=second.invalid")
        assert cap.check("openai", now=0).reserved == 0
        cap.settle(held, 2)
        assert first.get_provider_spend(cap.period_key(0), "openai") == 2
        assert second.get_provider_spend(cap.period_key(0), "openai") == 0
    finally:
        first.close()
        second.close()


def test_shared_transaction_retains_legacy_spend_and_serializes_separate_connections(ledger, monkeypatch, tmp_path):
    from maverick.world_model import WorldModel
    first = WorldModel(tmp_path / "shared.db")
    second = WorldModel(tmp_path / "shared.db")
    try:
        first.add_provider_spend(cap.period_key(0), "openai", 18)
        monkeypatch.setattr(cap, "_shared_world", lambda: first)
        one = cap.reserve("openai", 2, now=0, request_id="shared-one")
        monkeypatch.setattr(cap, "_shared_world", lambda: second)
        with pytest.raises(cap.ProviderCapExceeded):
            cap.reserve("openai", 2, now=0, request_id="shared-two")
        cap.settle(one, 1)
        cap.settle(one, 1)
        assert cap.check("openai", now=0).spent == 19
        assert not ledger.exists()
    finally:
        first.close()
        second.close()


def test_actual_process_crash_retains_provider_capacity(ledger):
    import subprocess
    import sys

    cap.record("openai", 18, path=ledger, now=0)
    source = """
import os, sys
from pathlib import Path
from maverick import provider_cost_cap as c
c.caps_from_config=lambda: {'openai':20.0}
c.period_from_config=lambda: 'day'
c.reserve('openai',2,path=Path(sys.argv[1]),now=0,request_id='crashed-process')
os._exit(0)
"""
    completed = subprocess.run([sys.executable, "-c", source, str(ledger)],
                               capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert cap.check("openai", path=ledger, now=0).reserved == 2
    with pytest.raises(cap.ProviderCapExceeded):
        cap.reserve("openai", 1, path=ledger, now=0, request_id="new-process")


@pytest.fixture
def dispatcher(ledger, monkeypatch):
    from maverick import llm

    monkeypatch.setattr(cap, "_ledger_path", lambda: ledger)
    monkeypatch.setattr(cap, "_shared_world", lambda: None)
    monkeypatch.setattr(cap, "period_key", lambda now=None, period=None: "2026-09-06")
    monkeypatch.setattr(llm, "_estimate_call_cost", lambda *a, **kw: 2.0)
    monkeypatch.setattr(llm, "_run_preflight", lambda *a, **kw: None)
    monkeypatch.setattr(llm, "_enforce_circuit", lambda *a: None)
    monkeypatch.setattr(llm, "_hedge_ms", lambda: None)
    monkeypatch.setattr("maverick.budget._lookup_price", lambda _: (1_000_000, 0))
    cap.record("openai", 18, path=ledger)
    return llm.LLM(model="openai:synthetic-cap-model")


def _response():
    from types import SimpleNamespace
    return SimpleNamespace(text="synthetic", raw=SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=1, completion_tokens=0)), cache_read_tokens=0, cache_creation_tokens=0)


def test_sync_dispatch_without_budget_reserves_and_prices_actual_response(dispatcher, ledger, monkeypatch):
    from threading import Event
    entered, finish = Event(), Event()
    calls = []

    class Client:
        def complete(self, **_kwargs):
            calls.append(1)
            entered.set()
            assert finish.wait(5)
            return _response()

    monkeypatch.setattr(dispatcher, "_get_client", lambda _: Client())
    with ThreadPoolExecutor(max_workers=1) as pool:
        call = pool.submit(dispatcher.complete, "synthetic", [])
        assert entered.wait(5)
        try:
            with pytest.raises(cap.ProviderCapExceeded):
                dispatcher.complete("synthetic", [])
        finally:
            finish.set()
        assert call.result(timeout=5).text == "synthetic"
    assert len(calls) == 1
    assert cap.check("openai", path=ledger).spent == 19
    assert cap.check("openai", path=ledger).reserved == 0


def test_async_hedge_needs_separate_provider_capacity(dispatcher, ledger, monkeypatch):
    import asyncio

    from maverick import llm
    calls = []

    class Client:
        async def complete_async(self, **_kwargs):
            calls.append(1)
            await asyncio.sleep(0.03)
            return _response()

    monkeypatch.setattr(dispatcher, "_get_client", lambda _: Client())
    monkeypatch.setattr(llm, "_hedge_ms", lambda: 1)
    assert asyncio.run(dispatcher.complete_async("synthetic", [])).text == "synthetic"
    assert len(calls) == 1
    assert cap.check("openai", path=ledger).spent == 19


def test_cancelled_physical_attempt_retains_ambiguous_charge(dispatcher, ledger, monkeypatch):
    import asyncio

    from maverick import llm

    # A fresh provider period has room for both physical calls.
    monkeypatch.setattr(cap, "caps_from_config", lambda: {"openai": 25.0})
    calls = []

    class Client:
        async def complete_async(self, **_kwargs):
            calls.append(1)
            if len(calls) == 1:
                await asyncio.sleep(5)
            return _response()

    monkeypatch.setattr(dispatcher, "_get_client", lambda _: Client())
    monkeypatch.setattr(llm, "_hedge_ms", lambda: 1)
    assert asyncio.run(dispatcher.complete_async("synthetic", [])).text == "synthetic"
    state = cap.check("openai", path=ledger)
    assert len(calls) == 2 and state.spent == 19 and state.reserved == 2 and state.unresolved
    with pytest.raises(cap.ProviderAccountingUnavailable):
        dispatcher.complete("synthetic", [])
