from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest


class _Budget:
    max_dollars = 0.25
    dollars = 0.0
    input_tokens = 0
    output_tokens = 0

    def check(self):
        return None


def _admission_stubs(monkeypatch, *, halt=False):
    from maverick import config, killswitch, llm, orchestrator
    from maverick.tenant import provider_admission
    from maverick_dashboard import viewpoints_api

    budget = _Budget()
    admitted_caps = []
    monkeypatch.setattr(config, "get_flows", lambda: {
        "authoring_max_dollars": config.DEFAULT_FLOW_AUTHORING_MAX_DOLLARS,
    })
    settlement = SimpleNamespace(recorded=False)
    settlement.record = lambda: setattr(settlement, "recorded", True)
    monkeypatch.setattr(viewpoints_api, "_admit",
                        lambda principal, cap: (admitted_caps.append(cap), budget)[1])
    monkeypatch.setattr(orchestrator, "_QuotaUsageSettlement",
                        lambda **_kwargs: settlement)
    monkeypatch.setattr(provider_admission, "run_scope",
                        lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(llm, "model_for_role", lambda role: role)
    if halt:
        monkeypatch.setattr(killswitch, "check",
                            lambda: (_ for _ in ()).throw(killswitch.Halted("private reason", "test")))
    return budget, settlement, admitted_caps


def test_workflow_authoring_uses_bounded_admission_settlement_and_audit(monkeypatch):
    from maverick import audit, config, llm
    from maverick_dashboard.workflow_drafting_admission import run_authoring_ai

    budget, settlement, admitted_caps = _admission_stubs(monkeypatch)
    audit_rows = []
    sent = []
    seen = {}

    class FakeLLM:
        def __init__(self, *, model):
            seen["model"] = model

        def complete(self, **kwargs):
            seen.update(kwargs)
            kwargs["dispatch_guard"]()
            sent.append(True)
            return SimpleNamespace(text="draft")

    monkeypatch.setattr(llm, "LLM", FakeLLM)
    monkeypatch.setattr(audit, "audit_event", lambda kind, **data: audit_rows.append((kind, data)))

    result = run_authoring_ai(
        "user:alice", "user:alice", "workflow-template-draft",
        lambda complete, admitted: complete(
            system="system", messages=[{"role": "user", "content": "brief"}],
            budget=admitted, max_tokens=20,
        ).text,
    )

    assert result == "draft" and sent == [True]
    assert admitted_caps == [config.DEFAULT_FLOW_AUTHORING_MAX_DOLLARS]
    assert seen["model"] == "orchestrator"
    assert seen["budget"] is budget and seen["budget"].max_dollars == 0.25
    assert seen["max_physical_attempts"] == 1 and seen["_no_failover"] is True
    assert callable(seen["dispatch_guard"])
    assert settlement.recorded is True
    assert [row[1]["phase"] for row in audit_rows] == ["start", "end"]
    assert audit_rows[-1][1]["dollars"] == 0.0


def test_workflow_authoring_rechecks_emergency_stop_before_provider_dispatch(monkeypatch):
    from maverick import audit, llm
    from maverick_dashboard.workflow_drafting_admission import (
        WorkflowAuthoringRefused,
        run_authoring_ai,
    )

    _budget, settlement, _admitted_caps = _admission_stubs(monkeypatch, halt=True)
    monkeypatch.setattr(audit, "audit_event", lambda *_args, **_kwargs: None)
    sent = []

    class FakeLLM:
        def __init__(self, *, model):
            pass

        def complete(self, **kwargs):
            try:
                kwargs["dispatch_guard"]()
            except Exception:
                raise llm.ProviderDispatchDenied("safe wrapper") from None
            sent.append(True)

    monkeypatch.setattr(llm, "LLM", FakeLLM)

    with pytest.raises(WorkflowAuthoringRefused, match="emergency stop"):
        run_authoring_ai(
            "user:alice", "user:alice", "flow-chat",
            lambda complete, budget: complete(system="s", messages=[], budget=budget),
        )

    assert sent == []
    assert settlement.recorded is True


def test_workflow_authoring_refuses_a_second_model_call(monkeypatch):
    from maverick import audit, llm
    from maverick_dashboard.workflow_drafting_admission import (
        WorkflowAuthoringRefused,
        run_authoring_ai,
    )

    _budget, settlement, _admitted_caps = _admission_stubs(monkeypatch)
    monkeypatch.setattr(audit, "audit_event", lambda *_args, **_kwargs: None)
    sent = []

    class FakeLLM:
        def __init__(self, *, model):
            pass

        def complete(self, **kwargs):
            kwargs["dispatch_guard"]()
            sent.append(True)
            return "first draft"

    monkeypatch.setattr(llm, "LLM", FakeLLM)

    def work(complete, budget):
        complete(system="s", messages=[], budget=budget)
        return complete(system="s", messages=[], budget=budget)

    with pytest.raises(WorkflowAuthoringRefused, match="one AI step"):
        run_authoring_ai("user:alice", "user:alice", "flow-draft", work)

    assert sent == [True]
    assert settlement.recorded is True


def test_workflow_authoring_uses_the_saved_spend_limit(monkeypatch):
    from maverick import config
    from maverick_dashboard import viewpoints_api
    from maverick_dashboard.workflow_drafting_admission import (
        WorkflowAuthoringRefused,
        run_authoring_ai,
    )

    monkeypatch.setattr(config, "get_flows", lambda: {"authoring_max_dollars": 0.85})
    seen = {}

    def refuse(principal, cap):
        seen["cap"] = cap
        raise viewpoints_api._Refused("test stop")

    monkeypatch.setattr(viewpoints_api, "_admit", refuse)
    with pytest.raises(WorkflowAuthoringRefused, match="test stop"):
        run_authoring_ai(
            "user:alice", "user:alice", "workflow-draft",
            lambda *_args: pytest.fail("refused work must not run"),
        )

    assert seen["cap"] == 0.85


def test_zero_workflow_writing_limit_blocks_before_admission(monkeypatch):
    from maverick import config
    from maverick_dashboard import viewpoints_api
    from maverick_dashboard.workflow_drafting_admission import (
        WorkflowAuthoringRefused,
        run_authoring_ai,
    )

    monkeypatch.setattr(config, "get_flows", lambda: {"authoring_max_dollars": 0.0})
    monkeypatch.setattr(
        viewpoints_api, "_admit",
        lambda *_args: pytest.fail("a zero limit must stop before task admission"),
    )
    with pytest.raises(WorkflowAuthoringRefused, match="writing is paused"):
        run_authoring_ai(
            "user:alice", "user:alice", "workflow-draft",
            lambda *_args: pytest.fail("paused work must not run"),
        )


def test_workflow_authoring_refuses_unavailable_admission_before_work(monkeypatch):
    from maverick_dashboard import viewpoints_api
    from maverick_dashboard.workflow_drafting_admission import (
        WorkflowAuthoringRefused,
        run_authoring_ai,
    )

    monkeypatch.setattr(
        viewpoints_api, "_admit",
        lambda *_args: (_ for _ in ()).throw(viewpoints_api._Refused("daily allowance reached")),
    )
    started = []
    with pytest.raises(WorkflowAuthoringRefused, match="daily allowance"):
        run_authoring_ai("user:alice", "user:alice", "flow-draft",
                         lambda *_args: started.append(True))
    assert started == []
