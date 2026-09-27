"""Runtime information-flow control: what an agent has seen decides where it may send.

Per-tool policy asks "may this agent call ``email``?". It cannot ask "has this
agent read payroll first?" -- and in a swarm the answer spreads: every agent's
tool output is excerpted to the shared blackboard, which is rendered into every
other agent's context each turn; children inherit their parent's brief and hand
their answers back; peers message each other. This module tracks that.

Labels
    A tool that reads tenant data (``immune.network.classify`` -> sensitive)
    labels the calling agent ``data:<tool>``. A run can also start labelled --
    a codebase run is ``code:<repo>`` from its first token.
Propagation (monotone: labels only accumulate within a run)
    blackboard   a reader absorbs the labels of every agent whose posts it is shown
    spawn        a child inherits its parent's labels; the parent absorbs the
                 child's when the child's answer returns
    peer bus     a recipient absorbs the sender's labels on receipt (plain or
                 signed handoff)
Enforcement
    A call to an external sink (``classify`` -> external: email, http_fetch,
    web_search, notify, mcp_* ...) by a labelled agent is checked against the
    mode: ``off``; ``audit`` (record, allow); ``approve`` (a human must approve
    through the consent path; a grant covers that sink for those labels for the
    rest of the run); ``block``. ``scope = "explicit"`` only acts when the
    labelled data itself appears in the outgoing arguments.
Witness
    Sensitive outputs are fingerprinted (hashed 32-character windows), so a
    decision says whether the data was copied into the call literally (any
    span of 47+ characters is detected) or the agent's context merely held it. A model can paraphrase, so context taint is
    the sound default and the witness is evidence, not the gate.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

MODES = ("off", "audit", "approve", "block")
_RANK = {m: i for i, m in enumerate(MODES)}
_WINDOW = 32
_STRIDE = 16
_MAX_FINGERPRINTS = 200_000
_MAX_SCAN_CHARS = 1_000_000


def _norm(text: str) -> str:
    return " ".join(text.split())


def _fp(chunk: str) -> int:
    return int.from_bytes(hashlib.blake2b(chunk.encode("utf-8", "replace"),
                                          digest_size=8).digest(), "big")


def _strings(value: object, depth: int = 0) -> Iterator[str]:
    if depth > 32:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(k, depth + 1)
            yield from _strings(v, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v, depth + 1)


def _is_source(tool: str) -> bool:
    from .immune.network import SENSITIVE, classify
    return SENSITIVE in classify(tool)


def _is_sink(tool: str) -> bool:
    from .immune.network import EXTERNAL, classify
    return EXTERNAL in classify(tool)


def stricter(a: str, b: str) -> str:
    return a if _RANK[a] >= _RANK[b] else b


@dataclass(frozen=True)
class Decision:
    tool: str
    labels: frozenset[str]
    explicit: frozenset[str]   # labels whose data appears literally in the arguments
    mode: str


@dataclass
class FlowTracker:
    """One per run, shared by every agent in the swarm (``SwarmContext.ifc``)."""
    mode: str = "audit"
    scope: str = "context"
    allow_sinks: frozenset[str] = frozenset()
    base_labels: frozenset[str] = frozenset()
    _agents: dict[str, set[str]] = field(default_factory=dict, init=False, repr=False)
    _fps: dict[int, str] = field(default_factory=dict, init=False, repr=False)
    _granted: set[tuple[str, frozenset[str]]] = field(default_factory=set, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"ifc mode must be one of {MODES}")
        if self.scope not in ("context", "explicit"):
            raise ValueError("ifc scope must be 'context' or 'explicit'")

    # -- labels ------------------------------------------------------------
    def labels(self, agent: str) -> frozenset[str]:
        with self._lock:
            return frozenset(self.base_labels | self._agents.get(agent, set()))

    def absorb(self, agent: str, labels: Iterable[str]) -> None:
        new = set(labels)
        if new:
            with self._lock:
                self._agents.setdefault(agent, set()).update(new)

    def inherit(self, child: str, parent: str) -> None:
        self.absorb(child, self.labels(parent))

    def observe_output(self, agent: str, tool: str, output: str) -> None:
        """Label ``agent`` with the data a sensitive tool returned to it."""
        if not _is_source(tool) or not isinstance(output, str):
            return
        label = f"data:{tool}"
        self.absorb(agent, (label,))
        text = _norm(output[:_MAX_SCAN_CHARS])
        with self._lock:
            for i in range(0, max(0, len(text) - _WINDOW + 1), _STRIDE):
                if len(self._fps) >= _MAX_FINGERPRINTS:
                    break
                self._fps.setdefault(_fp(text[i:i + _WINDOW]), label)

    def read_blackboard(self, agent: str, posters: Iterable[str]) -> None:
        for poster in set(posters) - {agent}:
            self.absorb(agent, self.labels(poster))

    def bus_receive(self, recipient: str, sender: str) -> None:
        """A delivered peer message carries its sender's labels (labels only
        grow, so the sender's current set covers what it knew when it sent)."""
        self.absorb(recipient, self.labels(sender))

    # -- enforcement -------------------------------------------------------
    def witness(self, args: object) -> frozenset[str]:
        """Labels whose fingerprinted data appears literally in ``args``' string
        values. Any copied span of at least ``_WINDOW + _STRIDE - 1`` (47)
        characters, after whitespace normalisation, is detected."""
        found: set[str] = set()
        with self._lock:
            if not self._fps:
                return frozenset()
            for value in _strings(args):
                text = _norm(value[:_MAX_SCAN_CHARS])
                for i in range(max(0, len(text) - _WINDOW + 1)):
                    label = self._fps.get(_fp(text[i:i + _WINDOW]))
                    if label:
                        found.add(label)
        return frozenset(found)

    def decide(self, agent: str, tool: str, args: object) -> Decision | None:
        """``None`` when the call carries no labelled flow to an external sink."""
        if self.mode == "off" or tool in self.allow_sinks or not _is_sink(tool):
            return None
        labels = self.labels(agent)
        if not labels:
            return None
        explicit = self.witness(args) & labels
        if self.scope == "explicit" and not explicit:
            return None
        return Decision(tool, labels, explicit, self.mode)

    def granted(self, decision: Decision) -> bool:
        with self._lock:
            return any(t == decision.tool and decision.labels <= ls for t, ls in self._granted)

    def grant(self, decision: Decision) -> None:
        with self._lock:
            self._granted.add((decision.tool, decision.labels))


# -- configuration and run scoping ---------------------------------------------

_RUN_LABELS: ContextVar[tuple[frozenset[str], str] | None] = ContextVar(
    "maverick_ifc_run_labels", default=None)


@contextmanager
def labelled_run(labels: Iterable[str], *, mode: str = "block") -> Iterator[None]:
    """Everything started inside begins labelled, enforced at least at ``mode``
    (a codebase run is ``code:<repo>`` from its first token)."""
    if mode not in MODES:
        raise ValueError(f"ifc mode must be one of {MODES}")
    token = _RUN_LABELS.set((frozenset(labels), mode))
    try:
        yield
    finally:
        _RUN_LABELS.reset(token)


def tracker_for_run() -> FlowTracker | None:
    """The tracker a new run gets under current config and run scope, or None."""
    from .config import get_ifc
    cfg = get_ifc()
    mode, base = cfg["mode"], frozenset()
    scoped = _RUN_LABELS.get()
    if scoped is not None:
        base, floor = scoped
        mode = stricter(mode, floor)
    if mode == "off":
        return None
    return FlowTracker(mode=mode, scope=cfg["scope"],
                       allow_sinks=frozenset(cfg["allow_sinks"]), base_labels=base)


def ledger_path():
    from .paths import data_dir
    return data_dir("immune", "ifc.jsonl")


def record(decision: Decision, *, agent: str, goal_id: int | None, outcome: str) -> None:
    """Audit row plus a counts-only local ledger for the dashboard. Labels and
    tool names only: never arguments or data."""
    from .audit import EventKind, audit_event
    from .immune import _ledger
    row = {"tool": decision.tool, "labels": sorted(decision.labels),
           "explicit": sorted(decision.explicit), "mode": decision.mode, "outcome": outcome}
    audit_event(EventKind.IFC_FLOW, agent=agent, goal_id=goal_id, **row)
    try:
        _ledger.append(ledger_path(), {**row, "goal_id": goal_id, "at": time.time()})
    except OSError:
        pass  # the signed audit row is the authority; the ledger is a projection


__all__ = ["Decision", "FlowTracker", "MODES", "labelled_run", "record",
           "stricter", "tracker_for_run"]
