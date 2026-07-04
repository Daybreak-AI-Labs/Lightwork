"""Flow self-rewrite -- propose node-type swaps from grounded per-node outcomes.

The differentiator of holding BOTH representations (deterministic action + agentic
goal) is that a flow can migrate work between them as evidence accumulates:

* an ``agent`` node that succeeds almost every time is a candidate to harden into
  a deterministic ``action`` (cheaper, auditable, no model variance);
* an ``action`` node that fails often is a candidate to hand to an ``agent`` (let
  intelligence absorb the variation the fixed step can't).

Like the operations scientist, this only *proposes* -- it never rewrites a live
flow. A human (or a downstream validation) applies a proposal. Proposals need
``min_support`` observations so a couple of flukes can't move a node. Gated: an
empty list unless the flow engine is on and enough signal has accumulated.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import node_outcomes
from .ir import NODE_ACTION, NODE_AGENT, Flow

_DEFAULT_MIN_SUPPORT = 10
_HARDEN_THRESHOLD = 0.9     # agent this reliable -> propose hardening to an action
_SOFTEN_THRESHOLD = 0.5     # action failing this often -> propose softening to an agent


@dataclass(frozen=True)
class NodeProposal:
    node_id: str
    from_kind: str
    to_kind: str
    reason: str
    n: int
    mean: float

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id, "from_kind": self.from_kind,
            "to_kind": self.to_kind, "reason": self.reason,
            "n": self.n, "mean": round(self.mean, 3),
        }


def propose(flow: Flow, *, min_support: int = _DEFAULT_MIN_SUPPORT,
            stats: dict[str, dict] | None = None) -> list[NodeProposal]:
    """Node-type swap proposals for ``flow`` from its grounded per-node outcomes.

    ``stats`` may be injected (else read from :mod:`.node_outcomes`). Only nodes
    with at least ``min_support`` observations are considered."""
    node_stats = stats if stats is not None else node_outcomes.stats(flow.id)
    out: list[NodeProposal] = []
    for node_id, node in flow.nodes.items():
        s = node_stats.get(node_id)
        if not s or s["n"] < min_support:
            continue
        mean = s["mean"]
        if node.kind == NODE_AGENT and mean >= _HARDEN_THRESHOLD:
            out.append(NodeProposal(
                node_id, NODE_AGENT, NODE_ACTION,
                f"agent node succeeds {mean:.0%} over {s['n']} runs -- its behaviour "
                "is predictable enough to harden into a deterministic action",
                s["n"], mean))
        elif node.kind == NODE_ACTION and mean < _SOFTEN_THRESHOLD:
            out.append(NodeProposal(
                node_id, NODE_ACTION, NODE_AGENT,
                f"action node lands only {mean:.0%} over {s['n']} runs -- hand it to "
                "an agent to absorb the variation the fixed step can't",
                s["n"], mean))
    # Most-evidence first so the strongest signals lead.
    out.sort(key=lambda p: p.n, reverse=True)
    return out


def maybe_propose(flow: Flow, *, min_support: int = _DEFAULT_MIN_SUPPORT) -> list[NodeProposal]:
    """Governed entry point: proposals when the flow engine is on, else empty."""
    from . import enabled
    if not enabled():
        return []
    try:
        return propose(flow, min_support=min_support)
    except Exception:  # pragma: no cover -- a maintenance pass must never crash
        return []


__all__ = ["NodeProposal", "propose", "maybe_propose"]
