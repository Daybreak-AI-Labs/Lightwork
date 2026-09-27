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
    through the consent path; a grant covers that sink, that destination and those
    labels for the rest of the run, and a later call that copies the data
    literally asks again unless the grant saw that too); ``block``.
    ``scope = "explicit"`` only acts when the labelled data itself appears in the
    outgoing arguments -- and when that cannot be ruled out (a scan cap was hit,
    or the labels came from a repository or an earlier run and so have no
    fingerprints) it acts as ``context`` does. A remote MCP server is not tainted by its own
    output: labels from ``mcp_<server>__*`` are ignored on calls to that server.
Persistence
    Labels outlive the run: they are kept per goal and per conversation, so a
    follow-up turn or a resumed goal starts with what the earlier run saw (memory
    and knowledge reads are sources in their own right).
Witness
    Sensitive outputs are fingerprinted (hashed 32-character windows), so a
    decision says whether the data was copied into the call literally (any
    span of 47+ characters is detected, compared case-folded, URL-decoded and
    whitespace-normalised) or the agent's context merely held it. A model can paraphrase, so context taint is
    the sound default and the witness is evidence, not the gate.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import unquote

MODES = ("off", "audit", "approve", "block")
_RANK = {m: i for i, m in enumerate(MODES)}
_WINDOW = 32
_STRIDE = 16
_MAX_FINGERPRINTS = 200_000
_MAX_SCAN_CHARS = 1_000_000


def _norm(text: str) -> str:
    # URL-decoding twice undoes the common double encoding; case-folding stops
    # a trivial upper-casing from hiding a copy.
    return " ".join(unquote(unquote(text)).casefold().split())


def _fp(chunk: str) -> int:
    return int.from_bytes(hashlib.blake2b(chunk.encode("utf-8", "replace"),
                                          digest_size=8).digest(), "big")


class _TooDeep(Exception):
    pass


def _strings(value: object, depth: int = 0) -> Iterator[str]:
    if depth > 32:
        raise _TooDeep
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


# Argument names that say where a call sends. Query text is deliberately not a
# destination: it is where exfiltrated data sits, and a search engine is one.
_DEST_KEYS = frozenset({
    "to", "cc", "bcc", "recipient", "recipients", "email", "address", "phone", "number",
    "url", "uri", "endpoint", "host", "domain", "webhook", "channel", "chat_id", "room",
    "repo", "repository", "remote", "bucket", "channel_id", "user_id", "username",
})
_DEST_SUFFIXES = ("_url", "_uri", "_email", "_address", "_phone", "_number", "_webhook")


def _dest_parts(value: object, prefix: str, depth: int, out: list[str]) -> None:
    if depth > 6 or len(out) > 32:
        return
    if isinstance(value, str) and value[:1] in "{[" and depth:
        try:
            value = json.loads(value)  # REST connectors pass the body as JSON text
        except ValueError:
            return
    if isinstance(value, dict):
        for key in sorted(str(k) for k in value):
            v = value[key]
            path = f"{prefix}.{key}" if prefix else key
            k = key.casefold()
            if (k in _DEST_KEYS or k.endswith(_DEST_SUFFIXES)) and not isinstance(v, (dict, list)):
                out.append(f"{path}={json.dumps(v, sort_keys=True, default=str)[:512]}")
            else:
                _dest_parts(v, path, depth + 1, out)
    elif isinstance(value, list):
        for i, v in enumerate(value[:32]):
            _dest_parts(v, f"{prefix}[{i}]", depth + 1, out)


def destination(args: object) -> str:
    """Where a call sends, from its conventional argument names at any depth
    (sorted, bounded). A grant is for one destination: approving a send to
    finance@ is not approval to send the same data anywhere else. ``""`` means
    the destination could not be identified, and such a call is never covered
    by a grant."""
    out: list[str] = []
    _dest_parts(args, "", 0, out)
    return "&".join(out)[:2048]


def display_destination(dest: str) -> str:
    """``dest`` for a human prompt: URL query strings and fragments (where data
    rides) are elided."""
    return re.sub(r"[?#][^\"&\s]*", "?…", dest)[:300]


def _mcp_server(tool: str) -> str | None:
    # Remote tool names cannot contain "__" (mcp_tools); server names can, so
    # the server is everything before the LAST separator.
    if not tool.startswith("mcp_") or "__" not in tool[4:]:
        return None
    return tool[4:].rsplit("__", 1)[0]


def stricter(a: str, b: str) -> str:
    return a if _RANK[a] >= _RANK[b] else b


@dataclass(frozen=True)
class Decision:
    tool: str
    labels: frozenset[str]
    explicit: frozenset[str]   # labels whose data appears literally in the arguments
    mode: str
    destination: str = ""
    witnessed: bool = True     # False: a scan cap hit, ``explicit`` is assumed, not seen


@dataclass
class FlowTracker:
    """One per run, shared by every agent in the swarm (``SwarmContext.ifc``)."""
    mode: str = "audit"
    scope: str = "context"
    allow_sinks: frozenset[str] = frozenset()
    base_labels: frozenset[str] = frozenset()
    _agents: dict[str, set[str]] = field(default_factory=dict, init=False, repr=False)
    _fps: dict[int, str] = field(default_factory=dict, init=False, repr=False)
    _granted: set[tuple[str, str, frozenset[str], frozenset[str]]] = field(
        default_factory=set, init=False, repr=False)
    _saturated: bool = field(default=False, init=False, repr=False)
    _recorded: set[tuple] = field(default_factory=set, init=False, repr=False)
    _persist_keys: tuple[str, ...] = field(default=(), init=False, repr=False)
    _persisted: set[str] = field(default_factory=set, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"ifc mode must be one of {MODES}")
        if self.scope not in ("context", "explicit"):
            raise ValueError("ifc scope must be 'context' or 'explicit'")
        # Labels a run starts with (a repository, an earlier run's data) have no
        # fingerprints here, so a literal copy of that data cannot be ruled out.
        self._saturated = bool(self.base_labels)

    # -- labels ------------------------------------------------------------
    def labels(self, agent: str) -> frozenset[str]:
        with self._lock:
            return frozenset(self.base_labels | self._agents.get(agent, set()))

    def absorb(self, agent: str, labels: Iterable[str]) -> None:
        new = set(labels)
        if new:
            with self._lock:
                self._agents.setdefault(agent, set()).update(new)
                fresh = new - self._persisted
                self._persisted |= fresh
                keys = self._persist_keys
            if fresh and keys:
                _persist(keys, fresh)

    def inherit(self, child: str, parent: str) -> None:
        self.absorb(child, self.labels(parent))

    def observe_output(self, agent: str, tool: str, output: str) -> None:
        """Label ``agent`` with the data a sensitive tool returned to it."""
        if not _is_source(tool) or not isinstance(output, str):
            return
        label = f"data:{tool}"
        self.absorb(agent, (label,))
        text = _norm(output[:_MAX_SCAN_CHARS])
        fps = [_fp(text[i:i + _WINDOW])
               for i in range(0, max(0, len(text) - _WINDOW + 1), _STRIDE)]
        with self._lock:
            if len(output) > _MAX_SCAN_CHARS:
                self._saturated = True  # the tail was never fingerprinted
            for fp in fps:
                if len(self._fps) >= _MAX_FINGERPRINTS:
                    self._saturated = True
                    break
                self._fps.setdefault(fp, label)

    def read_blackboard(self, agent: str, posters: Iterable[str]) -> None:
        for poster in set(posters) - {agent}:
            self.absorb(agent, self.labels(poster))

    def bus_receive(self, recipient: str, sender: str) -> None:
        """A delivered peer message carries its sender's labels (labels only
        grow, so the sender's current set covers what it knew when it sent)."""
        self.absorb(recipient, self.labels(sender))

    # -- enforcement -------------------------------------------------------
    def witness(self, args: object) -> frozenset[str] | None:
        """Labels whose fingerprinted data appears literally in ``args``' string
        values. Any copied span of at least ``_WINDOW + _STRIDE - 1`` (47)
        characters, after normalisation, is detected. ``None`` when a cap made
        the answer unknowable (too-deep arguments, over-long text, a saturated
        fingerprint table): callers must then assume the data could be there."""
        with self._lock:
            if not self._fps and not self._saturated:
                return frozenset()
            unknowable = self._saturated
        # Hash outside the lock: a large argument must not stall every agent's
        # label bookkeeping.
        fps: set[int] = set()
        budget = _MAX_SCAN_CHARS
        try:
            for value in _strings(args):
                if len(value) >= budget:
                    unknowable = True  # this string or those after it go unscanned
                text = _norm(value[:budget])
                budget -= len(text)
                fps.update(_fp(text[i:i + _WINDOW])
                           for i in range(max(0, len(text) - _WINDOW + 1)))
                if budget <= 0:
                    break
        except _TooDeep:
            unknowable = True
        with self._lock:
            found = frozenset(label for fp in fps if (label := self._fps.get(fp)))
        return None if unknowable else found

    def decide(self, agent: str, tool: str, args: object) -> Decision | None:
        """``None`` when the call carries no labelled flow to an external sink."""
        if self.mode == "off" or tool in self.allow_sinks or not _is_sink(tool):
            return None
        labels = self.labels(agent)
        server = _mcp_server(tool)
        if server is not None:
            labels = frozenset(lb for lb in labels if _mcp_server(lb[len("data:"):]) != server)
        if not labels:
            return None
        seen = self.witness(args)
        explicit = labels if seen is None else seen & labels
        if self.scope == "explicit" and not explicit:
            return None
        return Decision(tool, labels, explicit, self.mode, destination(args), seen is not None)

    def granted(self, decision: Decision) -> bool:
        """A grant covers the same sink and destination, no new labels, and no
        literal copy the approver was not told about."""
        if not decision.destination:
            return False  # an unidentified destination is asked about every time
        with self._lock:
            return any(t == decision.tool and d == decision.destination
                       and decision.labels <= ls and decision.explicit <= ex
                       for t, d, ls, ex in self._granted)

    def grant(self, decision: Decision) -> None:
        with self._lock:
            self._granted.add((decision.tool, decision.destination,
                               decision.labels, decision.explicit))

    def first_record(self, key: tuple) -> bool:
        """True once per distinct (agent, decision, outcome): a loop that retries
        a blocked send is one ledger row, not thousands."""
        with self._lock:
            if key in self._recorded:
                return False
            self._recorded.add(key)
            return True

    def resume(self, *, goal_id: int | None, conversation_id: int | None) -> None:
        """Start from the labels earlier runs of this goal and conversation saw,
        and keep what this run learns for the next one."""
        keys = tuple(k for k in (f"goal-{int(goal_id)}" if goal_id is not None else None,
                                 f"conv-{int(conversation_id)}" if conversation_id is not None else None)
                     if k)
        if not keys:
            return
        prior: set[str] = set()
        for key in keys:
            prior |= _load(key)
        with self._lock:
            self.base_labels = frozenset(self.base_labels | prior)
            if prior:
                self._saturated = True  # restored labels carry no fingerprints
            known = prior | set(self.base_labels)
            for agent_labels in self._agents.values():
                known |= agent_labels
            self._persisted = set(prior)
            self._persist_keys = keys
            fresh = known - prior
            self._persisted |= fresh
        if fresh:
            _persist(keys, fresh)


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


def _labels_path(key: str):
    from .paths import data_dir
    return data_dir("immune", "ifc-labels", f"{key}.jsonl")


def _load(key: str) -> set[str]:
    from .immune import _ledger
    try:
        return {str(r["label"]) for r in _ledger.read(_labels_path(key)) if "label" in r}
    except (OSError, ValueError):
        return set()


def _persist(keys: tuple[str, ...], labels: Iterable[str]) -> None:
    from .immune import _ledger
    for key in keys:
        for label in sorted(labels):
            try:
                _ledger.append(_labels_path(key), {"label": label, "at": time.time()})
            except OSError:
                pass  # best effort: this run is still enforced from memory


def record(decision: Decision, *, agent: str, goal_id: int | None, outcome: str,
           tracker: FlowTracker | None = None) -> None:
    """Audit row plus a counts-only local ledger for the dashboard. Labels, tool
    names and a destination digest only: never arguments or data. With
    ``tracker``, a repeat of the same row in the run is not written again."""
    if tracker is not None and not tracker.first_record(
            (agent, decision.tool, decision.destination, decision.labels,
             decision.explicit, outcome)):
        return
    from .audit import EventKind, audit_event
    from .immune import _ledger
    row = {"tool": decision.tool, "labels": sorted(decision.labels),
           "explicit": sorted(decision.explicit), "mode": decision.mode, "outcome": outcome,
           "to": hashlib.sha256(decision.destination.encode()).hexdigest()[:12]
           if decision.destination else ""}
    audit_event(EventKind.IFC_FLOW, agent=agent, goal_id=goal_id, **row)
    try:
        _ledger.append(ledger_path(), {**row, "goal_id": goal_id, "at": time.time()})
    except OSError:
        pass  # the signed audit row is the authority; the ledger is a projection


__all__ = ["Decision", "FlowTracker", "MODES", "destination", "labelled_run", "record",
           "stricter", "tracker_for_run"]
