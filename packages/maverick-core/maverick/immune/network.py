"""Composition risk: what agents can do together that no single policy sees.

The "Rule of Two": no agent system should combine *sensitive data*, *state
change* and *external reach* without a control. Per-agent audits check each
agent alone; data moves between agents (shared blackboard, peer messaging,
delegation), so the envelope that matters is the one along a data-flow path.

Findings carry a witness -- the concrete tools and path -- and pack findings are
proven by the runtime :class:`~maverick.capability.Capability` itself
(``cap.permits(tool)``) under stated settings, not by a heuristic.
"""
from __future__ import annotations

import os
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from ..capability import COORDINATION_TOOLS
from ..safety.tool_risk import risk_rank, tool_risk

SENSITIVE, MUTATE, EXTERNAL = "sensitive", "mutate", "external"

# Tools that read tenant data. Non-public enterprise connectors are added by
# :func:`classify` as sensitive, not external: the tenant's own SaaS is inside its
# trust boundary. Unknown remote (``mcp_*``) tools are treated as all three.
_SENSITIVE = frozenset({
    "read_file", "list_dir", "knowledge_search", "sql_query", "sql_query_read",
    "spreadsheet", "spreadsheet_read", "database_tool", "memory_search",
    "read_tool_result",
})
# Tools whose inputs leave the tenant boundary (query strings count).
_EXTERNAL = frozenset({
    "email", "notify", "calendar", "http_fetch", "web_search", "web_archive",
    "browser", "computer", "webhook", "websocket", "sms", "slack", "teams",
})


def classify(tool: str) -> frozenset[str]:
    """The Rule-of-Two classes a tool contributes (possibly several)."""
    from ..tools.enterprise_connectors import (
        ENTERPRISE_CONNECTOR_NAMES,
        PUBLIC_DATA_CONNECTOR_NAMES,
    )
    if tool in COORDINATION_TOOLS:  # modelled as data flows, not as powers
        return frozenset()
    if tool.startswith("mcp_"):
        return frozenset({SENSITIVE, MUTATE, EXTERNAL})
    out = set()
    if tool in PUBLIC_DATA_CONNECTOR_NAMES or tool in _EXTERNAL:
        out.add(EXTERNAL)
    elif tool in _SENSITIVE or tool in ENTERPRISE_CONNECTOR_NAMES or tool.endswith("_read"):
        out.add(SENSITIVE)
    if tool_risk(tool) == "high":
        out.add(MUTATE)
    return frozenset(out)


@dataclass
class Graph:
    """Agents (name -> reachable tools) and directed data flows between them."""
    agents: dict[str, frozenset[str]]
    flows: set[tuple[str, str]] = field(default_factory=set)

    def __post_init__(self) -> None:
        self._succ: dict[str, list[str]] = {n: [] for n in self.agents}
        for a, b in sorted(self.flows):
            self._succ[a].append(b)

    def successors(self, node: str) -> list[str]:
        return self._succ[node]

    def classes(self, node: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {SENSITIVE: [], MUTATE: [], EXTERNAL: []}
        for tool in sorted(self.agents[node]):
            for cls in classify(tool):
                out[cls].append(tool)
        return out


@dataclass(frozen=True)
class Finding:
    kind: str                       # "trifecta" | "exfiltration"
    path: tuple[str, ...]           # agents, data flowing left to right
    witness: dict[str, tuple[str, str]]  # class -> (agent, tool)
    sink_risk: str                  # risk of the lowest-risk way out; "high"
                                    # sinks are the ones secure defaults escalate


def _paths_from(g: Graph, src: str) -> dict[str, tuple[str, ...]]:
    """Shortest data-flow path from ``src`` to every reachable agent (BFS),
    in BFS order so the first match is a shortest one."""
    seen = {src: (src,)}
    queue = deque([src])
    while queue:
        node = queue.popleft()
        for nxt in g.successors(node):
            if nxt not in seen:
                seen[nxt] = seen[node] + (nxt,)
                queue.append(nxt)
    return seen


def findings(g: Graph) -> list[Finding]:
    """One minimal finding per agent holding sensitive data.

    ``trifecta``: the data can flow through an agent with a state-changing tool
    and on to external reach. Otherwise ``exfiltration``: the data can reach an
    external sink. The witness names the concrete tools; the path is shortest.
    """
    cls = {n: g.classes(n) for n in g.agents}

    def sink(node: str) -> str:  # worst case: the least-guarded way out
        return min(cls[node][EXTERNAL], key=lambda t: (risk_rank(tool_risk(t)), t))

    out: list[Finding] = []
    for src in sorted(g.agents):
        if not cls[src][SENSITIVE]:
            continue
        reach = _paths_from(g, src)
        found = None
        for mid, head in reach.items():
            if not cls[mid][MUTATE]:
                continue
            tail = next(((d, p) for d, p in _paths_from(g, mid).items()
                         if cls[d][EXTERNAL]), None)
            if tail:
                found = ("trifecta", head + tail[1][1:], tail[0], (mid, cls[mid][MUTATE][0]))
                break
        if not found:
            dst = next((d for d in reach if cls[d][EXTERNAL]), None)
            if dst is None:
                continue
            found = ("exfiltration", reach[dst], dst, None)
        kind, path, dst, mutate = found
        witness = {SENSITIVE: (src, cls[src][SENSITIVE][0]), EXTERNAL: (dst, sink(dst))}
        if mutate:
            witness[MUTATE] = mutate
        out.append(Finding(kind, path, witness, tool_risk(witness[EXTERNAL][1])))
    return out


def blast_radius(g: Graph) -> dict[str, int]:
    """How many other agents each agent's output can reach (compromise spread)."""
    return {n: len(_paths_from(g, n)) - 1 for n in sorted(g.agents)}


@contextmanager
def settings(*, levels: bool | None = None, grounding: bool | None = None) -> Iterator[None]:
    """Evaluate pack capabilities under explicit autonomy/grounding settings.
    ``None`` keeps the deployment's current setting for that dial."""
    keys = {k: v for k, v in {"MAVERICK_WORKFORCE_LEVELS": levels,
                              "MAVERICK_WORKFORCE_DATA_GROUNDING": grounding}.items()
            if v is not None}
    saved = {k: os.environ.get(k) for k in keys}
    os.environ.update({k: "1" if v else "0" for k, v in keys.items()})
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def runtime_tools(profile) -> frozenset[str]:
    """Tools a pack can actually call: the runtime capability, not the TOML."""
    from ..domain import domain_capability
    cap = domain_capability(profile, None, f"agent:{profile.name}")
    return frozenset(t for t in cap.allow_tools if cap.permits(t))


def roster_graph(domains=None, *, swarm: bool = False) -> Graph:
    """Graph of every pack. ``swarm=True`` adds the orchestrator hub: peer
    messaging and the shared blackboard are coordination tools no pack
    allowlist can remove, so every member's data can reach every other."""
    from ..domain import available_domains
    domains = domains if domains is not None else available_domains()
    agents = {name: runtime_tools(p) for name, p in sorted(domains.items())}
    flows: set[tuple[str, str]] = set()
    if swarm:
        agents["orchestrator"] = frozenset()
        for name in domains:
            flows |= {(name, "orchestrator"), ("orchestrator", name)}
    return Graph(agents, flows)


def load_graph(data: dict) -> Graph:
    """Build a graph from ``{"agents": {name: [tools]}, "flows": [[a, b]]}``."""
    agents = {str(k): frozenset(map(str, v)) for k, v in data["agents"].items()}
    flows = {(str(a), str(b)) for a, b in data.get("flows", [])}
    unknown = {n for pair in flows for n in pair} - agents.keys()
    if unknown:
        raise ValueError(f"flows reference unknown agents: {sorted(unknown)}")
    return Graph(agents, flows)
