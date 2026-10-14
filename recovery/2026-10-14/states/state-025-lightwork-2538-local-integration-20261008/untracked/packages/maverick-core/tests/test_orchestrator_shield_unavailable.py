"""Configured Shield scan failures must not promote unchecked text into prompts."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from maverick.blackboard import Blackboard
from maverick.budget import Budget
from maverick.intake import _shield_allows_generated_prompt
from maverick.orchestrator import (
    _brief_facts_block,
    _build_orchestrator_brief,
    _format_tree_of_thought_plan,
    _maybe_recall_prior_work,
    _sanitize_persisted_prompt_text,
    _shield_input_block_reason,
)
from maverick.world_model import WorldModel


@pytest.fixture(params=["raised", "reported"])
def broken_shield(request):
    def scan(_text):
        if request.param == "raised":
            raise RuntimeError("scanner unavailable")
        return SimpleNamespace(allowed=True, scan_error=True)

    return SimpleNamespace(scan_input=scan, scan_output=scan)


def test_optional_scan_failure_blocks_input_and_replayed_text(broken_shield, monkeypatch):
    monkeypatch.setenv("MAVERICK_REQUIRE_SHIELD", "0")
    assert _shield_input_block_reason(broken_shield, "customer request") == "safety scan unavailable"
    assert _sanitize_persisted_prompt_text(
        "unscanned customer answer", shield=broken_shield, max_chars=100,
    ) == "[redacted by Shield]"
    plan = _format_tree_of_thought_plan("unscanned proposed plan", shield=broken_shield)
    assert "unscanned proposed plan" not in plan
    assert "redacted by Shield" in plan


def test_optional_scan_failure_redacts_recalled_results_and_facts(
    broken_shield, monkeypatch, tmp_path,
):
    monkeypatch.setenv("MAVERICK_AUTO_RECALL", "1")
    world = WorldModel(tmp_path / "world.db")
    prior = world.create_goal("review vendor agreement", "check terms")
    world.set_goal_status(prior, "done", result="unscanned prior result")
    current = world.get_goal(world.create_goal("review vendor agreement again", "check terms"))
    recalled = _maybe_recall_prior_work(world, current, broken_shield)
    assert recalled and "unscanned prior result" not in recalled
    assert "[result redacted by Shield]" in recalled

    world.upsert_fact("customer_preference", "unscanned stored fact")
    fact_block = _brief_facts_block(world, current.id, broken_shield)
    assert "unscanned stored fact" not in fact_block
    assert "customer_preference: [redacted by Shield]" in fact_block


@pytest.mark.asyncio
async def test_optional_scan_failure_redacts_prior_conversation(
    broken_shield, monkeypatch, tmp_path, fake_llm,
):
    import maverick.orchestrator as orchestrator

    async def unchanged(brief, **_kwargs):
        return brief, "default"

    monkeypatch.setattr(orchestrator, "_apply_brief_enrichments", unchanged)
    world = WorldModel(tmp_path / "world.db")
    conversation = world.get_or_create_conversation("web", "customer")
    world.append_turn(conversation.id, "user", "unscanned old question")
    world.append_turn(conversation.id, "assistant", "unscanned old answer")
    goal_id = world.create_goal("review current request", "safe description")
    brief, _mode = await _build_orchestrator_brief(
        llm=fake_llm, world=world, budget=Budget(max_dollars=1),
        blackboard=Blackboard(), goal=world.get_goal(goal_id), goal_id=goal_id,
        conversation_id=conversation.id, channel="web", user_id="customer",
        domain=None, shield=broken_shield,
    )
    assert "unscanned old question" not in brief
    assert "unscanned old answer" not in brief
    assert "user: [redacted by Shield]" in brief
    assert "assistant: [redacted by Shield]" in brief


def test_generated_prompt_is_withheld_when_configured_scanner_fails(
    broken_shield, monkeypatch,
):
    class Shield:
        @classmethod
        def from_config(cls, **_kwargs):
            return broken_shield

    monkeypatch.setitem(sys.modules, "maverick_shield", SimpleNamespace(Shield=Shield))
    assert not _shield_allows_generated_prompt("ordinary generated specialist description")
