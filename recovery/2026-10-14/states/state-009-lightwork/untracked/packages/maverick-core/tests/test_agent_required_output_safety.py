"""Required Shield/redaction failures withhold tool output before hooks."""

from __future__ import annotations

import pytest
from maverick import hooks
from maverick.agent import Agent
from maverick.blackboard import Blackboard
from maverick.budget import Budget
from maverick.hooks import HookEvent
from maverick.sandbox import LocalBackend
from maverick.swarm import SwarmContext
from maverick.tools import Tool, ToolRegistry
from maverick.world_model import WorldModel


class _AllowedVerdict:
    allowed = True
    scan_error = False
    severity = "none"
    reasons: list[str] = []


class _ThrowingOutputShield:
    def scan_tool_call(self, _name, _args):
        return _AllowedVerdict()

    def scan_output(self, _text):
        raise RuntimeError("sensitive scanner internals")


class _PermissiveShield:
    def scan_tool_call(self, _name, _args):
        return _AllowedVerdict()

    def scan_output(self, _text):
        return _AllowedVerdict()


@pytest.fixture(autouse=True)
def _isolate_hooks():
    hooks.clear()
    yield
    hooks.clear()


def _agent(tmp_path, shield, output: str) -> Agent:
    world = WorldModel(tmp_path / "world.db")
    goal_id = world.create_goal("required output safety", "")
    context = SwarmContext(
        llm=None,
        world=world,
        budget=Budget(),
        blackboard=Blackboard(),
        sandbox=LocalBackend(workdir=tmp_path),
        goal_id=goal_id,
        max_depth=1,
        shield=shield,
    )
    agent = Agent(ctx=context, role="researcher", brief="test")
    registry = ToolRegistry(production_manifest=None)
    registry.register(
        Tool(
            name="benign",
            description="test",
            input_schema={"type": "object", "properties": {}},
            fn=lambda _args: output,
        )
    )
    agent.tools = registry
    return agent


@pytest.mark.asyncio
async def test_required_shield_exception_withholds_tool_output(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAVERICK_REQUIRE_SHIELD", "1")
    agent = _agent(tmp_path, _ThrowingOutputShield(), "do-not-release")

    result = await agent._run_tool("benign", {})

    assert "do-not-release" not in result
    assert "required Shield scan failed" in result


@pytest.mark.asyncio
async def test_required_redactor_failure_is_withheld_before_post_hook(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MAVERICK_REQUIRE_REDACTION", "1")

    def _raise(_text):
        raise RuntimeError("do-not-log-redactor-detail")

    monkeypatch.setattr("maverick.safety.secret_detector.redact", _raise)
    seen: list[str] = []
    hooks.register(
        HookEvent.POST_TOOL_USE,
        lambda context: seen.append(str(context.tool_result)) or True,
    )
    agent = _agent(tmp_path, _PermissiveShield(), "secret-output")

    result = await agent._run_tool("benign", {})

    assert "secret-output" not in result
    assert "required secret redaction failed" in result
    # Hook contexts are metadata-only unless they carry an explicit data grant.
    # When the shared redactor is unavailable, even a synthetic tool-result
    # notice is withheld at the hook boundary rather than reclassified as safe.
    assert seen == ["None"]
