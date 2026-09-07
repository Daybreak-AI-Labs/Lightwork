"""A budget/wall-clock exhaustion must give the helpful 'raise the cap'
message, not a generic 'ran into an error'.

The agent swallows BudgetExceeded into result.error (so spawned children
return gracefully), which meant the orchestrator's nice budget message was
unreachable for the common case -- the user saw a generic error with no hint
to raise --max-dollars.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from maverick.budget import Budget
from maverick.llm import LLM
from maverick.orchestrator import run_goal
from maverick.sandbox import LocalBackend
from maverick.world_model import WorldModel


@pytest.mark.asyncio
async def test_budget_exhaustion_gives_helpful_message(tmp_path: Path, fake_llm):
    world = WorldModel(tmp_path / "world.db")
    gid = world.create_goal("expensive goal", "")
    bud = Budget(max_dollars=0.01)
    # Push spend over the cap directly (bypassing record_tokens' own check)
    # so the agent's first budget.check() trips -- no LLM call needed.
    with bud._lock:
        bud.dollars = 5.0

    out = await run_goal(
        fake_llm, world, bud, gid,
        sandbox=LocalBackend(workdir=tmp_path), max_depth=1,
    )

    assert "hit your spending or time limit" in out
    assert "--max-dollars" in out
    assert "ran into an error" not in out
    # The message reports spend AGAINST the configured cap, not spend alone, so
    # the user can see how far over they went and what the limit was (#598).
    assert "$5.00 of $0.01 cap" in out
    assert world.get_goal(gid).status == "blocked"


class _StrictInvoiceClient:
    """A provider client that dispatches and then invoices through
    ``Budget.record_tokens`` the way every real client does. W1-1 made the
    pre-dispatch estimate estimate-only (so Azure/Bedrock/gateway ids can
    dispatch at all); the invoice stays strict, and it is this call that
    must raise UnpricedModelError for an unverified model id."""

    async def complete_async(self, *, budget, model, **kwargs):
        budget.record_tokens(10, 5, model=model)
        raise AssertionError(
            "record_tokens must refuse to bill an unverified model id"
        )


@pytest.mark.asyncio
async def test_unpriced_model_gives_pricing_message_not_a_cap_message(
    tmp_path: Path,
):
    """W1-30: UnpricedModelError is a BudgetExceeded subclass (so the run
    still aborts), but it is NOT a spending/time cap -- it's Lightwork
    refusing to bill a model with no verified price. The old behavior
    reported this identically to a real cap trip ('hit your spending or
    time limit ... resume with a higher cap'), so the user raised the cap,
    re-ran, and failed identically forever; the real fix (price the model,
    or set [budget] strict_pricing=false) was visible only in the debug log.
    """
    world = WorldModel(tmp_path / "world.db")
    gid = world.create_goal("goal on an unpriced model", "")
    llm = LLM(model="claude-nonexistent-99")
    llm._get_client = lambda provider: _StrictInvoiceClient()

    out = await run_goal(
        llm, world, Budget(max_dollars=5.0), gid,
        sandbox=LocalBackend(workdir=tmp_path), max_depth=1,
        orchestrator_model_override="claude-nonexistent-99",
    )

    assert "no verified price" in out
    assert "strict_pricing" in out
    # Must NOT be mistaken for -- or advise the same fix as -- a real cap trip.
    assert "hit your spending or time limit" not in out
    assert "--max-dollars" not in out
    goal = world.get_goal(gid)
    assert goal.status == "blocked"
    # Recorded distinctly so it doesn't poison the budget-cap failure class
    # the self-tuning budget learner trains on.
    assert goal.result.startswith("unpriced model:")
