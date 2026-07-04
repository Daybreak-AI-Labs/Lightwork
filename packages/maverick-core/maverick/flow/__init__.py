"""Flows -- a deterministic control-flow skeleton over agentic + tool steps.

A :class:`~.ir.Flow` generalises a template: a single ``agent`` node IS today's
template, and adding branch / foreach / parallel / approval / delay nodes lets a
migrated workflow keep its structure (fidelity + auditability) while any node can
still be a full agentic goal. OFF by default -- ``[flows] enable`` /
``MAVERICK_FLOWS`` -- so a default deployment is unaffected (kernel rule 1).
"""
from __future__ import annotations

from .ir import (
    NODE_ACTION,
    NODE_AGENT,
    NODE_APPROVAL,
    NODE_BRANCH,
    NODE_DELAY,
    NODE_FOREACH,
    NODE_PARALLEL,
    Flow,
    FlowNode,
    single_agent_flow,
)
from .runner import FlowRunResult, eval_condition, render, run_flow


def enabled() -> bool:
    """Whether the flow engine is on. OFF by default; never raises."""
    from ..config import env_flag
    v = env_flag("MAVERICK_FLOWS")
    if v is not None:
        return v
    try:
        from ..config import get_flows
        return bool(get_flows().get("enable", False))
    except Exception:  # pragma: no cover -- config must never block
        return False


__all__ = [
    "enabled", "Flow", "FlowNode", "single_agent_flow",
    "run_flow", "FlowRunResult", "render", "eval_condition",
    "NODE_ACTION", "NODE_AGENT", "NODE_BRANCH", "NODE_FOREACH",
    "NODE_PARALLEL", "NODE_APPROVAL", "NODE_DELAY",
]
