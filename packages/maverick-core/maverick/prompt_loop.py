"""The Prompt Loop: the platform writes the operator's next work order.

Every other loop in this repo improves the *system* — dreaming distils skills,
``self_improvement`` promotes a rung, ``evolve`` searches config, ``self_modify``
proposes a patch. All of them still assume a human decided what to work on. That
decision is the actual bottleneck: the operator reads dashboards, forms a
hypothesis, and hand-writes a prompt. Do that fifty times and the quality of the
platform is capped by the quality of the prompts somebody was willing to type at
6pm on a Friday.

This module closes that gap. One cycle is::

    OBSERVE   deterministic sensors read what is actually true right now --
              approvals waiting on a human, recurring failure clusters, goals
              stuck mid-flight, frozen learning, unreached modules, past-due
              deprecations, contained external agents, spend burnt on failure.
    COMPOSE   each signal becomes a WORK ORDER: a ready-to-run prompt with its
              rationale, its evidence, its definition of done, and the standing
              constraints that keep the resulting work honest.
    RANK      expected value = impact x confidence x affinity x novelty over
              effort, so the top of the queue is the next best thing to ask for.
    CHAIN     when an order is completed the loop composes its own follow-up
              from the outcome (investigate -> fix -> verify -> document). That
              is the self-prompting part: the operator answers, never asks.

Two properties make this safe enough to ship into a governed deployment, and
both are deliberate inversions of how "autonomous agent" products usually work:

* **The model may sharpen a prompt; it may never widen one.** A live composer
  is an injected seam (:data:`ComposeFn`). It is allowed to rewrite the title,
  the prompt body, the rationale and the verification line — the *wording*.
  Kind, risk, score, evidence and the standing constraints are computed
  deterministically and are re-applied over whatever the model returned. A
  composer that fails, times out, or returns junk falls back to the
  deterministic prompt, so the loop runs with no provider at all.
* **A prompt is a proposal, not an instruction.** In the default ``advise``
  mode nothing executes: the queue is text for a human. ``auto`` mode may hand
  an order back to the agent as a goal, but only under a risk ceiling, only
  once, and only against a digest of the exact prompt that was approved —
  editing an order after approval invalidates it. Both ends are audited.

Free text that reaches this module from a run outcome is screened for prompt
injection before it can become part of a new prompt, because in ``auto`` mode
the follow-up prompt is fed back to an agent: an unscreened outcome would be a
laundering path from a poisoned run straight into the next instruction.

Config: ``[prompt_loop]``. Observation and advice are ON by default (read-only,
no tokens, no egress); the live composer and ``auto`` dispatch are OFF.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("maverick.prompt_loop")

#: What a work order asks for. A closed set: the chain state machine and the
#: step templates both key off it, so a composer cannot invent a seventh kind
#: and escape the templates that carry the operating constraints.
KINDS = ("investigate", "fix", "verify", "build", "document", "decide")

#: Risk of *doing the work*, not of the underlying problem. Governs the auto
#: dispatch ceiling; ordered least-to-most.
RISKS = ("low", "medium", "high")

#: Lifecycle. ``muted`` is terminal-by-operator: a key skipped enough times
#: stops being proposed at all, so the queue cannot nag.
STATES = ("open", "taken", "done", "skipped", "muted")

#: Approval provenance. Trusted caller-supplied metadata (never inferred from
#: free text) — the operator UI and the one-shot dispatch check key off it.
PROVENANCE = "prompt_loop"

_STORE_VERSION = 1
_MAX_ORDERS = 200
_MAX_TEXT = 6000
_MAX_TITLE = 140
_MAX_EVIDENCE = 8
_MAX_EVIDENCE_CHARS = 240
#: Skips of the same signal key before it stops being proposed. Three is the
#: point where "not now" has clearly become "not ever".
_SKIPS_TO_MUTE = 3
#: Cycles a skipped key sits out before it may return.
_COOLDOWN_CYCLES = 5
#: Spent dispatch approvals remembered. Beyond this the highest EVICTED id
#: becomes a floor, so forgetting can only ever refuse — an aged-out approval
#: must never authorize a second dispatch.
_MAX_CONSUMED_APPROVALS = 200
#: A goal is "stuck" once it has sat in a non-terminal state this long.
_STUCK_SECONDS = 6 * 3600
#: Window the spend and failure sensors reason over.
_WINDOW_SECONDS = 7 * 24 * 3600


class PromptLoopError(Exception):
    """Operator-facing refusal (disabled, unknown order, wrong state, missing
    or already-spent approval, tampered prompt)."""


# ---- settings -------------------------------------------------------------

def _settings() -> dict:
    """Resolved ``[prompt_loop]`` settings, fail-closed on any trouble.

    Only *advice* is on by default. If the section cannot be read at all we
    report "advise, gated, no live composer" — a broken config file degrades to
    the read-only half rather than running the half that spends or dispatches.
    """
    try:
        from .config import get_prompt_loop
        cfg = get_prompt_loop()
        if not isinstance(cfg, dict):
            raise ValueError("prompt_loop settings are not a mapping")
        mode = str(cfg.get("mode", "advise") or "advise").strip().lower()
        ceiling = str(cfg.get("auto_risk_ceiling", "low") or "low").strip().lower()
        return {
            "enable": bool(cfg.get("enable", True)),
            "mode": mode if mode in ("advise", "auto") else "advise",
            "compose_with_model": bool(cfg.get("compose_with_model", False)),
            "require_approval": bool(cfg.get("require_approval", True)),
            "auto_risk_ceiling": ceiling if ceiling in RISKS else "low",
            "max_orders": max(1, min(_MAX_ORDERS,
                                     int(cfg.get("max_orders", 50) or 50))),
        }
    except Exception:
        log.warning("prompt_loop: settings unreadable; advising only "
                    "(fail closed)", exc_info=True)
        return {"enable": True, "mode": "advise", "compose_with_model": False,
                "require_approval": True, "auto_risk_ceiling": "low",
                "max_orders": 50}


def enabled() -> bool:
    """Whether the loop may propose. ON by default: observing your own system
    and writing text about it spends nothing and changes nothing."""
    return bool(_settings()["enable"])


def auto_enabled() -> bool:
    """Whether an order may be handed back to the agent without a human
    retyping it. OFF by default — that is the line between an advisor and an
    autonomous scheduler, and it is an explicit operator decision."""
    return _settings()["mode"] == "auto"


# ---- store ----------------------------------------------------------------

def store_path() -> Path:
    """The loop ledger (orders, affinity, cooldowns, spent approvals)."""
    from .paths import data_dir
    return data_dir("prompt-loop") / "loop.json"


def _empty_store() -> dict:
    return {"version": _STORE_VERSION, "orders": {}, "affinity": {},
            "cooldown": {}, "skips": {}, "cycles": 0,
            "consumed_approvals": [], "approval_floor": 0}


def _load_store() -> dict:
    """Read the ledger, REFUSING on corruption.

    The spent-dispatch record lives here. Degrading a damaged file to "empty"
    would let an already-spent approval authorize a second dispatch, and would
    silently un-mute every signal the operator dismissed. Fail closed."""
    path = store_path()
    try:
        if not path.exists():
            return _empty_store()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("store root must be an object")
    except (OSError, UnicodeError, ValueError) as e:
        raise PromptLoopError(
            f"the prompt-loop ledger is unreadable ({e}); refusing rather "
            "than starting from a blank queue") from e
    store = _empty_store()
    orders = data.get("orders")
    if isinstance(orders, dict):
        store["orders"] = {str(k): dict(v) for k, v in orders.items()
                           if isinstance(v, dict)}
    for key in ("affinity", "cooldown", "skips"):
        section = data.get(key)
        if isinstance(section, dict):
            store[key] = dict(section)
    consumed = data.get("consumed_approvals")
    if isinstance(consumed, list):
        store["consumed_approvals"] = list(consumed)
    for key in ("cycles", "approval_floor"):
        try:
            store[key] = int(data.get(key) or 0)
        except (TypeError, ValueError):
            store[key] = 0
    return store


def _save_store(store: dict) -> None:
    from .file_lock import atomic_write_text, ensure_private_directory
    path = store_path()
    ensure_private_directory(path.parent)
    atomic_write_text(path, json.dumps(store, indent=2, sort_keys=True),
                      mode=0o600)


def _locked():
    from .file_lock import cross_process_lock
    return cross_process_lock(store_path())


def _prune(store: dict) -> None:
    """Keep the newest ``max_orders``, but never evict an order that is still
    live. A ``taken`` order is somebody's current work and a ``dispatched`` one
    owns a goal row; dropping either would lose the only handle on it."""
    orders = store["orders"]
    cap = _settings()["max_orders"]
    if len(orders) <= cap:
        return
    live = {oid for oid, o in orders.items()
            if o.get("state") == "taken" or o.get("goal_id")}
    disposable = sorted(
        (oid for oid in orders if oid not in live),
        key=lambda oid: float(orders[oid].get("created_at") or 0.0))
    while len(orders) > cap and disposable:
        orders.pop(disposable.pop(0), None)


# ---- model ----------------------------------------------------------------

@dataclass
class Signal:
    """One true thing about the system right now, with its magnitude.

    A sensor's whole job is to produce these; nothing here is a prompt yet.
    Keeping observation separate from wording is what lets the deterministic
    ranking survive a model rewriting the text."""
    source: str
    key: str                      # stable across cycles; the dedup/mute handle
    title: str
    detail: str
    kind: str = "investigate"
    risk: str = "low"
    impact: float = 0.5           # 0..1, how much fixing this is worth
    confidence: float = 0.8       # 0..1, how sure the sensor is
    effort: float = 0.4           # 0..1, 1 = a large piece of work
    evidence: list[str] = field(default_factory=list)
    #: Whether completing this earns a follow-up. False for recurring
    #: operational chores: clearing an approval queue is finished when it is
    #: empty, and manufacturing a next step for it would be nagging dressed up
    #: as a loop.
    chain: bool = True


@dataclass
class WorkOrder:
    """A ready-to-run prompt, with why it is next and how to know it worked."""
    id: str
    title: str
    prompt: str
    rationale: str
    verify: str
    source: str
    key: str
    kind: str
    risk: str
    score: float
    components: dict = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    created_at: float = 0.0
    cycle: int = 0
    state: str = "open"
    parent: str | None = None
    chain: bool = True
    composer: str = "deterministic"
    goal_id: int | None = None
    approval_id: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---- text hygiene ---------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def _bound(text: object, limit: int) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip()[:limit]


def _screen_outcome(text: object) -> str:
    """Bound, injection-screen and secret-redact operator/agent-supplied text.

    Applied to anything that will be *quoted into a future prompt*. An
    injection hit refuses rather than quarantines: the only exit from this
    queue is "hand these words to an agent", so storing a poisoned outcome
    would just defer the compromise by one cycle."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    # Bound BEFORE screening, so padding cannot push a marker past the window
    # the scanner reads.
    raw = raw[:_MAX_TEXT]
    from .memory_guard import injection_markers
    hits = injection_markers(raw)
    if hits:
        raise PromptLoopError(
            f"the reported outcome tripped the injection screen "
            f"({', '.join(sorted(hits))}); it is refused, not stored — a "
            "follow-up prompt is composed from this text")
    from .safety.secret_detector import redact
    redacted, matches = redact(raw)
    if matches:
        log.warning("prompt_loop: redacted %d secret(s) from an outcome",
                    len(matches))
    return redacted


# ---- sensors --------------------------------------------------------------
#
# Each sensor is read-only, cheap, and independently wrapped: one broken sensor
# degrades the queue, it never fails the cycle. A sensor whose subsystem is off
# or absent yields nothing rather than guessing.

def _norm_signature(title: str) -> str:
    """A coarse cluster key for a goal title — the first few alphabetic tokens,
    lowercased. Deliberately lossy: the point is to notice that the same KIND
    of thing failed repeatedly, not to fingerprint an exact string."""
    tokens = [t for t in re.split(r"[^A-Za-z]+", str(title or "").lower()) if t]
    return " ".join(tokens[:4])


def _sensor_approvals(ctx: dict) -> list[Signal]:
    """Humans are the slowest component; a queue of parked approvals is the
    single most common reason governed work is not moving."""
    world = ctx.get("world")
    if world is None:
        return []
    pending = world.pending_approvals()
    if not pending:
        return []
    now = ctx["now"]
    oldest = min((float(getattr(a, "requested_at", now) or now)
                  for a in pending), default=now)
    age_hours = max(0.0, (now - oldest) / 3600.0)
    evidence = [
        f"approval #{getattr(a, 'id', '?')}: {_bound(getattr(a, 'action', ''), 80)}"
        for a in pending[:_MAX_EVIDENCE]
    ]
    return [Signal(
        source="approvals", key="approvals:pending",
        title=f"{len(pending)} approval(s) waiting on a human decision",
        detail=(f"The oldest has been parked {age_hours:.1f}h. Governed work "
                "is blocked behind them, so nothing downstream can progress."),
        kind="decide", risk="low",
        impact=min(1.0, 0.35 + 0.05 * len(pending) + 0.02 * age_hours),
        confidence=1.0, effort=0.15, evidence=evidence, chain=False,
    )]


def _sensor_failure_clusters(ctx: dict) -> list[Signal]:
    """Two goals that failed the same way are a defect; one is a bad day."""
    # Bounded to the window: a cluster that stopped recurring must age out of
    # the queue by itself, or a fixed problem is proposed forever.
    cutoff = ctx["now"] - _WINDOW_SECONDS
    clusters: dict[str, list[Any]] = {}
    for g in (ctx.get("goals") or []):
        if str(getattr(g, "status", "") or "") not in ("failed", "blocked"):
            continue
        if float(getattr(g, "updated_at", 0) or 0) < cutoff:
            continue
        sig = _norm_signature(getattr(g, "title", ""))
        if sig:
            clusters.setdefault(sig, []).append(g)
    out = []
    for sig, members in sorted(clusters.items(),
                               key=lambda kv: -len(kv[1]))[:5]:
        if len(members) < 2:
            continue
        evidence = [f"goal #{getattr(g, 'id', '?')} "
                    f"({getattr(g, 'status', '')}): "
                    f"{_bound(getattr(g, 'title', ''), 90)}"
                    for g in members[:_MAX_EVIDENCE]]
        out.append(Signal(
            source="failures", key=f"failures:{sig}",
            title=f"{len(members)} goals failed the same way: {sig!r}",
            detail=("The same class of work fails repeatedly, which means the "
                    "cause is systemic — a missing capability, a wrong "
                    "assumption in the brief, or a tool that does not do what "
                    "its description claims."),
            kind="investigate", risk="low",
            impact=min(1.0, 0.5 + 0.08 * len(members)),
            confidence=0.85, effort=0.5, evidence=evidence,
        ))
    return out


def _sensor_stuck_goals(ctx: dict) -> list[Signal]:
    """Work that started and never landed. Silent, and therefore expensive."""
    now = ctx["now"]
    # Stuck, but not abandoned: past the window a goal is history, not work.
    stuck = [g for g in (ctx.get("goals") or [])
             if str(getattr(g, "status", "") or "") in ("running", "pending")
             and _STUCK_SECONDS
             < now - float(getattr(g, "updated_at", now) or now)
             <= _WINDOW_SECONDS]
    if not stuck:
        return []
    evidence = [f"goal #{getattr(g, 'id', '?')} idle "
                f"{(now - float(getattr(g, 'updated_at', now) or now)) / 3600:.1f}h: "
                f"{_bound(getattr(g, 'title', ''), 80)}"
                for g in stuck[:_MAX_EVIDENCE]]
    return [Signal(
        source="stuck", key="stuck:goals",
        title=f"{len(stuck)} goal(s) have been in flight for over "
              f"{_STUCK_SECONDS // 3600}h",
        detail=("A goal that neither finishes nor fails is the worst state to "
                "be in: it holds its slot, reports nothing, and no alert "
                "fires. Either the worker died and the row was never "
                "reconciled, or a step is waiting on something that will "
                "never arrive."),
        kind="investigate", risk="low",
        impact=min(1.0, 0.45 + 0.05 * len(stuck)), confidence=0.9,
        effort=0.4, evidence=evidence,
    )]


def _sensor_learning_frozen(ctx: dict) -> list[Signal]:
    """Learning is frozen when the verifier has drifted. Everything downstream
    of the judge is untrustworthy until it is re-calibrated — this outranks
    almost anything else the loop can find."""
    try:
        from . import calibration
        if not calibration.learning_frozen():
            return []
    except Exception:
        log.debug("prompt_loop: calibration unavailable", exc_info=True)
        return []
    return [Signal(
        source="calibration", key="calibration:frozen",
        title="Learning is frozen: the verifier is mis-calibrated",
        detail=("Every promotion gate reads the verifier. While it has "
                "drifted, no learned change can be trusted to have beaten its "
                "baseline, so the whole self-improvement ladder is paused and "
                "the platform is silently standing still."),
        kind="fix", risk="medium", impact=0.95, confidence=0.95, effort=0.5,
        evidence=["maverick.calibration.learning_frozen() is True"],
    )]


def _sensor_unreached(ctx: dict) -> list[Signal]:
    """Code nothing reaches. The single most common way a capability is
    'shipped' and does not exist — the exact defect class that made
    reachability.lock.json necessary in the first place."""
    try:
        from .reachability import LOCK
        data = json.loads(Path(LOCK).read_text(encoding="utf-8"))
    except Exception:
        return []
    modules = data.get("UNREACHED") if isinstance(data, dict) else None
    if not isinstance(modules, list) or not modules:
        return []
    names = [str(m) for m in modules][:_MAX_EVIDENCE]
    return [Signal(
        source="reachability", key="reachability:unreached",
        title=f"{len(modules)} module(s) are reachable from nothing",
        detail=("Each one is either a capability that was built and never "
                "wired to a caller, or dead weight that should be deleted. "
                "Both are defects; the first is the expensive one, because it "
                "reads as shipped."),
        kind="decide", risk="low",
        impact=min(0.8, 0.3 + 0.02 * len(modules)), confidence=1.0,
        effort=0.35, evidence=names,
    )]


def _sensor_deprecations(ctx: dict) -> list[Signal]:
    """A deprecation past its removal version is a promise the product broke."""
    try:
        from . import deprecations
        due = deprecations.past_due()
    except Exception:
        return []
    if not due:
        return []
    names = [f"{getattr(d, 'name', d)} (remove by "
             f"{getattr(d, 'remove_in', '?')})" for d in due[:_MAX_EVIDENCE]]
    return [Signal(
        source="deprecations", key="deprecations:past_due",
        title=f"{len(due)} deprecation(s) are past their removal version",
        detail=("The CI gate fails on these, and every one of them is a "
                "documented promise to remove something that is still there."),
        kind="fix", risk="low", impact=0.5, confidence=1.0, effort=0.3,
        evidence=names,
    )]


def _sensor_external_agents(ctx: dict) -> list[Signal]:
    """Externally-built agents that hit a containment or budget wall."""
    try:
        from . import external_agents
        if not external_agents.enabled():
            return []
        st = external_agents.status()
    except Exception:
        return []
    contained = int(st.get("contained") or 0)
    over = int(st.get("over_budget") or 0)
    if not contained and not over:
        return []
    evidence = [f"contained: {contained}", f"over budget: {over}",
                f"enrolled: {st.get('enrolled')}"]
    return [Signal(
        source="external_agents", key="external:contained",
        title=f"{contained + over} external agent(s) are contained or over "
              "budget",
        detail=("A contained agent is doing no work and its owner may not "
                "know. Either the ceiling is wrong for the job it was given, "
                "or it is repeatedly attempting something it should not — and "
                "those two need opposite responses."),
        kind="investigate", risk="low",
        impact=min(0.85, 0.4 + 0.1 * (contained + over)), confidence=0.9,
        effort=0.3, evidence=evidence,
    )]


def _sensor_failed_spend(ctx: dict) -> list[Signal]:
    """Money spent on goals that did not land. The cleanest waste signal there
    is, and one nobody looks at until the invoice arrives."""
    world = ctx.get("world")
    if world is None:
        return []
    goals = {getattr(g, "id", None): g for g in (ctx.get("goals") or [])}
    total = 0.0
    wasted = 0.0
    try:
        episodes = world.list_episodes(limit=2000)
    except Exception:
        return []
    cutoff = ctx["now"] - _WINDOW_SECONDS
    for ep in episodes:
        cost = float(getattr(ep, "cost_dollars", 0) or 0)
        if cost <= 0:
            continue
        g = goals.get(getattr(ep, "goal_id", None))
        if g is None or float(getattr(g, "updated_at", 0) or 0) < cutoff:
            continue
        total += cost
        if str(getattr(g, "status", "") or "") in ("failed", "blocked"):
            wasted += cost
    if total <= 0 or wasted < 1.0 or wasted / total < 0.25:
        return []
    share = wasted / total
    return [Signal(
        source="spend", key="spend:on_failure",
        title=f"${wasted:.2f} of ${total:.2f} ({share:.0%}) went to goals that "
              "failed",
        detail=("Spend on work that did not land is the cheapest thing to fix "
                "and the last thing anyone measures. Either the work is being "
                "attempted without the capability to finish it, or failures "
                "are being retried past the point of usefulness."),
        kind="investigate", risk="low",
        impact=min(0.9, 0.3 + share), confidence=0.75, effort=0.4,
        evidence=[f"window: last {_WINDOW_SECONDS // 86400} days",
                  f"failed spend: ${wasted:.2f}",
                  f"total spend: ${total:.2f}"],
    )]


#: The standing ladder — what to do when nothing is broken. Ordered by value;
#: the loop proposes the first entry not on cooldown, so an idle system still
#: gets a next best action instead of an empty screen. Low impact by
#: construction, so a real signal always outranks these.
_STANDING: tuple[tuple[str, str, str, str], ...] = (
    ("standing:adversarial", "verify",
     "Run an adversarial pass on the most recently changed subsystem",
     "A green suite proves the code does what the tests say. It does not "
     "prove the failure paths fail safely. Pick the newest subsystem and try "
     "to break it deliberately."),
    ("standing:reachable", "verify",
     "Prove the last shipped capability is reachable end to end",
     "Name the UI control, route, CLI verb or tool that invokes it, and walk "
     "it in a running system. A feature with tests and no caller is inert."),
    ("standing:claims", "verify",
     "Check the docs against the code for claims that are no longer true",
     "Read what the project says about itself and verify each claim. In a "
     "governance product, a false claim about what the system did is the most "
     "serious defect class there is."),
    ("standing:restore", "verify",
     "Execute and time a restore drill against the stated RTO/RPO",
     "A backup that has never been restored is a hypothesis. Restore into a "
     "scratch environment, verify the audit chain, and record the elapsed "
     "time."),
    ("standing:onboard", "document",
     "Time a clean clone to a running system using only the README",
     "Anything over fifteen minutes is a defect against the docs, and it is "
     "the first thing a new engineer or an evaluating buyer experiences."),
)


def _sensor_standing(ctx: dict) -> list[Signal]:
    """A floor for the queue: when there is nothing to propose, offer the next
    standing hardening pass rather than an empty screen.

    Called by :func:`cycle` only when nothing else survived filtering — not
    from :func:`observe`, which reports what is TRUE rather than what to say.
    That distinction matters: the real sensors can fire every cycle and still
    yield nothing proposable because their orders are already open or on
    cooldown, and the operator should get the floor in exactly that case."""
    cooldown = ctx.get("cooldown") or {}
    cycle_no = int(ctx.get("cycle") or 0)
    for key, kind, title, detail in _STANDING:
        if int(cooldown.get(key) or 0) > cycle_no:
            continue
        return [Signal(
            source="standing", key=key, title=title, detail=detail,
            kind=kind, risk="low", impact=0.25, confidence=0.6, effort=0.45,
            evidence=["no sensor fired this cycle; this is the standing "
                      "next-best hardening pass, not an incident"],
        )]
    return []


#: Order matters only for tie-breaking; ranking does the real sorting.
SENSORS: tuple[tuple[str, Callable[[dict], list[Signal]]], ...] = (
    ("calibration", _sensor_learning_frozen),
    ("approvals", _sensor_approvals),
    ("failures", _sensor_failure_clusters),
    ("stuck", _sensor_stuck_goals),
    ("external_agents", _sensor_external_agents),
    ("spend", _sensor_failed_spend),
    ("deprecations", _sensor_deprecations),
    ("reachability", _sensor_unreached),
)


def _sensor_context(world: Any, *, cooldown: dict, cycle_no: int) -> dict:
    """Everything the sensors read, fetched once. Goals are the expensive read
    and three sensors want them, so one query serves all three."""
    ctx: dict[str, Any] = {"world": world, "now": time.time(),
                           "cooldown": cooldown, "cycle": cycle_no,
                           "goals": []}
    if world is not None:
        try:
            ctx["goals"] = world.list_goals(limit=300, order="desc")
        except Exception:
            log.debug("prompt_loop: goal read failed", exc_info=True)
    return ctx


def observe(*, world: Any = None, cooldown: dict | None = None,
            cycle_no: int = 0) -> list[Signal]:
    """Run every sensor. One failing sensor never fails the cycle.

    Returns what is TRUE right now. The standing floor is deliberately not
    included: it is what to SAY when there is nothing to propose, which is a
    decision :func:`cycle` makes after filtering."""
    ctx = _sensor_context(world, cooldown=cooldown or {}, cycle_no=cycle_no)
    signals: list[Signal] = []
    for name, sensor in SENSORS:
        try:
            found = sensor(ctx)
        except Exception:
            log.warning("prompt_loop: sensor %s failed; continuing without it",
                        name, exc_info=True)
            continue
        signals.extend(s for s in found if isinstance(s, Signal))
    return signals


# ---- composition ----------------------------------------------------------

#: The operating discipline every generated prompt carries, so the operator
#: never has to restate it and a model reading the prompt cannot quietly drop
#: it. These are this repo's own hard-won rules, not generic advice.
CONSTRAINTS: tuple[str, ...] = (
    "Reproduce the problem with a failing test before you change anything. "
    "If you cannot write a test that fails, you have a hypothesis, not a "
    "finding — say so.",
    "Never weaken a test to get green: no deleted assertions, no new skips, "
    "no widened thresholds.",
    "Run the WHOLE suite from the repo root, not a focused subset — focused "
    "runs hide collisions.",
    "Surgical diffs only. Match the surrounding style. No speculative "
    "abstractions and no drive-by refactors.",
    "State your assumptions. If the premise of this order turns out to be "
    "wrong, say so plainly and stop rather than building around it.",
)

#: Per-kind step scaffolding. The wording is what turns a signal into work
#: somebody can start without thinking first.
_STEPS: dict[str, tuple[str, ...]] = {
    "investigate": (
        "Establish ground truth first: what exactly happened, in what order, "
        "with which inputs. Quote the evidence.",
        "Form the two most plausible causes and design the cheapest "
        "observation that tells them apart.",
        "Try to refute your own conclusion before you report it.",
        "Report the cause, the blast radius, and the smallest fix that "
        "addresses the cause rather than the symptom.",
    ),
    "fix": (
        "Write the failing test that reproduces this, and check it in.",
        "Fix the cause at the right depth — a special case layered on shared "
        "infrastructure means the fix is too shallow.",
        "Check the neighbours: find every other call site with the same "
        "shape, because this defect class rarely occurs once.",
        "Verify twice — the new test passes, then the whole suite passes.",
    ),
    "verify": (
        "Walk the path in a running system with real data, not in unit tests.",
        "Attack it: do it twice, do it backwards, kill the process mid-step, "
        "and confirm every failure path fails closed.",
        "Confirm the record written afterwards actually reflects what "
        "happened.",
        "Report each finding with a reproduction, and refute your own "
        "findings before reporting them.",
    ),
    "build": (
        "Name the reachable path first: which control, route, verb or tool "
        "will invoke this. If you cannot name it, you are building something "
        "inert.",
        "Build the smallest complete version — end to end and connected, "
        "not a broad skeleton.",
        "Cover the failure paths and the permission-denied path, not only "
        "the happy path.",
        "Add the tests, then run the whole suite.",
    ),
    "document": (
        "Write down only what is true of the code as it exists today.",
        "Delete or correct anything aspirational or out of date that you find "
        "along the way.",
        "Include the reproduction: the exact commands a reader would run.",
    ),
    "decide": (
        "Lay out the options with their consequences, shortest first.",
        "Say which one you would choose and why, in one paragraph.",
        "Name what becomes irreversible once the decision is made.",
    ),
}

#: How completing one order composes the next. This is the self-prompting
#: spine: an investigation earns a fix, a fix earns a verification, a clean
#: verification earns the write-up, and the chain ends there rather than
#: manufacturing work forever.
_CHAIN: dict[str, str | None] = {
    "investigate": "fix",
    "fix": "verify",
    "verify": "document",
    "build": "verify",
    "decide": "build",
    "document": None,
}


def _order_id() -> str:
    return uuid.uuid4().hex[:12]


def _deterministic_prompt(sig: Signal, *, parent: str | None = None,
                          outcome: str = "") -> str:
    """Compose the prompt body from the signal. Pure and reproducible."""
    lines: list[str] = [sig.title, ""]
    lines.append("Why this is next")
    lines.append(sig.detail)
    lines.append("")
    if outcome:
        lines.append("What just happened (the order this follows from)")
        lines.append(outcome)
        lines.append("")
    if sig.evidence:
        lines.append("Evidence")
        lines.extend(f"- {e}" for e in sig.evidence)
        lines.append("")
    lines.append("Do this")
    lines.extend(f"{i}. {step}"
                 for i, step in enumerate(_STEPS.get(sig.kind,
                                                     _STEPS["investigate"]), 1))
    lines.append("")
    lines.append("Constraints")
    lines.extend(f"- {c}" for c in CONSTRAINTS)
    lines.append("")
    lines.append("Done means")
    lines.append(_verify_line(sig))
    if parent:
        lines.append("")
        lines.append(f"(Follows work order {parent}.)")
    return "\n".join(lines)[:_MAX_TEXT]


def _verify_line(sig: Signal) -> str:
    """How the operator knows the order was actually completed."""
    return {
        "investigate": "The cause is named with evidence, the blast radius is "
                       "stated, and the smallest fix is proposed. A dead end "
                       "is a valid result if what was ruled out is written "
                       "down.",
        "fix": "A test that failed before now passes, the whole suite is "
               "green, and no existing test was weakened to get there.",
        "verify": "Every path was walked in a running system, each finding "
                  "has a reproduction, and the failure paths were shown to "
                  "fail closed.",
        "build": "The capability is reachable from a named caller, the "
                 "failure paths are covered, and the whole suite is green.",
        "document": "Every claim in the new text is true of the code today, "
                    "and the commands in it were run.",
        "decide": "A decision is recorded with its reasoning and what it "
                  "makes irreversible.",
    }.get(sig.kind, "The work is complete and verified.")


#: A live composer: given the signal and the deterministic draft, return
#: ``{"title": ..., "prompt": ..., "rationale": ..., "verify": ...}`` — the
#: wording only. Anything else it returns is ignored, and any failure falls
#: back to the deterministic draft.
ComposeFn = Callable[[Signal, dict], "dict | None"]


def _apply_composer(order: WorkOrder, sig: Signal,
                    composer: ComposeFn | None) -> WorkOrder:
    """Let a model improve the wording, and only the wording.

    The clamp is the point. Kind, risk, score, evidence and the constraints
    block are recomputed here from the signal, so a composer cannot lower a
    risk band, inflate a score, invent evidence, or drop the operating rules
    from the prompt — the three ways a self-prompting loop would otherwise
    talk itself into more rope."""
    if composer is None:
        return order
    try:
        draft = composer(sig, order.to_dict())
    except Exception:
        log.warning("prompt_loop: composer raised; keeping the deterministic "
                    "prompt", exc_info=True)
        return order
    if not isinstance(draft, dict):
        return order
    title = _bound(draft.get("title"), _MAX_TITLE)
    rationale = _bound(draft.get("rationale"), 1000)
    verify = _bound(draft.get("verify"), 1000)
    prompt = str(draft.get("prompt") or "").strip()[:_MAX_TEXT]
    try:
        for text in (title, rationale, verify, prompt):
            if text:
                _screen_outcome(text)
    except PromptLoopError:
        log.warning("prompt_loop: composer output tripped the injection "
                    "screen; keeping the deterministic prompt")
        return order
    if prompt and not all(c in prompt for c in CONSTRAINTS):
        # The composer dropped, softened, or paraphrased the operating rules.
        # Re-attach the real block rather than discard the rewrite (the wording
        # may still be better). Checked against every rule, not a heading:
        # a composer that writes the word "Constraints" over its own weaker
        # list would otherwise pass.
        prompt = (prompt + "\n\nConstraints\n"
                  + "\n".join(f"- {c}" for c in CONSTRAINTS))[:_MAX_TEXT]
    order.title = title or order.title
    order.prompt = prompt or order.prompt
    order.rationale = rationale or order.rationale
    order.verify = verify or order.verify
    order.composer = "model"
    return order


def compose(sig: Signal, *, cycle_no: int = 0, parent: str | None = None,
            outcome: str = "", composer: ComposeFn | None = None) -> WorkOrder:
    """Turn one signal into a work order."""
    kind = sig.kind if sig.kind in KINDS else "investigate"
    risk = sig.risk if sig.risk in RISKS else "low"
    sig.kind, sig.risk = kind, risk
    sig.evidence = [_bound(e, _MAX_EVIDENCE_CHARS)
                    for e in (sig.evidence or [])][:_MAX_EVIDENCE]
    order = WorkOrder(
        id=_order_id(),
        title=_bound(sig.title, _MAX_TITLE),
        prompt=_deterministic_prompt(sig, parent=parent, outcome=outcome),
        rationale=_bound(sig.detail, 1000),
        verify=_verify_line(sig),
        source=sig.source, key=sig.key, kind=kind, risk=risk,
        score=0.0, evidence=list(sig.evidence),
        created_at=time.time(), cycle=cycle_no, parent=parent,
        chain=bool(sig.chain),
    )
    return _apply_composer(order, sig, composer)


# ---- ranking --------------------------------------------------------------

def _affinity(store: dict, source: str) -> float:
    """What the operator's own dispositions say about this sensor.

    Completing an order from a source raises its weight; skipping lowers it.
    Bounded both ways so one bad week cannot silence a sensor permanently, and
    a favourite one cannot drown out an incident."""
    try:
        return min(1.5, max(0.5, float(store.get("affinity", {}).get(source, 1.0))))
    except (TypeError, ValueError):
        return 1.0


def _novelty(store: dict, key: str) -> float:
    """Decay a signal the operator keeps declining, without erasing it."""
    try:
        skips = int(store.get("skips", {}).get(key, 0))
    except (TypeError, ValueError):
        skips = 0
    return max(0.3, 1.0 - 0.25 * skips)


def rank(sig: Signal, *, affinity: float, novelty: float) -> tuple[float, dict]:
    """Expected value per unit of work. Deterministic and explainable — the
    components ride along on the order so the operator can see why something
    is at the top instead of trusting a number."""
    impact = min(1.0, max(0.0, float(sig.impact)))
    confidence = min(1.0, max(0.0, float(sig.confidence)))
    effort = min(1.0, max(0.0, float(sig.effort)))
    score = (impact * confidence * affinity * novelty) / (0.25 + effort)
    return round(score, 4), {
        "impact": round(impact, 3), "confidence": round(confidence, 3),
        "effort": round(effort, 3), "affinity": round(affinity, 3),
        "novelty": round(novelty, 3),
    }


# ---- the cycle ------------------------------------------------------------

def _open_keys(store: dict) -> set[str]:
    return {str(o.get("key")) for o in store["orders"].values()
            if o.get("state") in ("open", "taken")}


def _muted_keys(store: dict) -> set[str]:
    return {str(o.get("key")) for o in store["orders"].values()
            if o.get("state") == "muted"}


def _admitter(store: dict, *, cycle_no: int, cooldown: dict,
              skip_keys: set[str],
              composer: ComposeFn | None) -> Callable[[Signal], WorkOrder | None]:
    """Build the filter+compose+rank step, shared by the sensors and the floor.

    ``skip_keys`` is mutated as orders are admitted, so two sensors that
    produce the same key in one cycle yield one order, not two."""
    def admit(sig: Signal) -> WorkOrder | None:
        if sig.key in skip_keys:
            return None
        if int(cooldown.get(sig.key) or 0) > cycle_no:
            return None
        order = compose(sig, cycle_no=cycle_no, composer=composer)
        order.score, order.components = rank(
            sig, affinity=_affinity(store, sig.source),
            novelty=_novelty(store, sig.key))
        skip_keys.add(sig.key)
        return order
    return admit


def cycle(*, world: Any = None, composer: ComposeFn | None = None,
          limit: int = 5) -> list[dict]:
    """One full pass: observe, compose, rank, persist. Returns the new orders.

    Re-proposing something already open would turn the queue into a nag, so a
    signal whose key is already open, on cooldown, or muted is skipped — the
    existing order stays and keeps its history."""
    cfg = _settings()
    if not cfg["enable"]:
        raise PromptLoopError(
            "the prompt loop is off; set [prompt_loop] enable = true")
    with _locked():
        store = _load_store()
        cycle_no = int(store.get("cycles") or 0) + 1
        cooldown = dict(store.get("cooldown") or {})
        skip_keys = _open_keys(store) | _muted_keys(store)
        admit = _admitter(store, cycle_no=cycle_no, cooldown=cooldown,
                          skip_keys=skip_keys, composer=composer)

        fresh = [o for o in
                 (admit(s) for s in
                  observe(world=world, cooldown=cooldown, cycle_no=cycle_no))
                 if o is not None]
        if not fresh:
            # Nothing survived filtering — not necessarily because nothing is
            # wrong, but because everything true is already open or cooling
            # off. Either way the operator gets the standing floor, not an
            # empty screen.
            ctx = _sensor_context(world, cooldown=cooldown, cycle_no=cycle_no)
            fresh = [o for o in (admit(s) for s in _sensor_standing(ctx))
                     if o is not None]

        fresh.sort(key=lambda o: o.score, reverse=True)
        fresh = fresh[:max(1, int(limit))]
        for order in fresh:
            store["orders"][order.id] = order.to_dict()
        store["cycles"] = cycle_no
        _prune(store)
        _save_store(store)

    try:
        _audit_cycle(fresh, cycle_no)
    except Exception:
        _discard([o.id for o in fresh])
        raise
    return [o.to_dict() for o in fresh]


def _audit_cycle(orders: list[WorkOrder], cycle_no: int) -> None:
    from .audit import EventKind, audit_event
    audit_event(EventKind.PROMPT_LOOP_PROPOSED, agent="prompt_loop",
                cycle=cycle_no, orders=len(orders),
                sources=sorted({o.source for o in orders}),
                top=orders[0].id if orders else "",
                composer=orders[0].composer if orders else "none")


def _discard(order_ids: list[str]) -> None:
    """Remove orders whose audit row never landed, so the queue can never show
    work the signed chain has no record of proposing."""
    if not order_ids:
        return
    try:
        with _locked():
            store = _load_store()
            for oid in order_ids:
                store["orders"].pop(oid, None)
            _save_store(store)
    except Exception:  # pragma: no cover -- cleanup never masks the refusal
        log.warning("prompt_loop: could not discard unaudited orders %s",
                    ", ".join(order_ids), exc_info=True)


def next_orders(limit: int = 3, *, state: str = "open") -> list[dict]:
    """The top of the queue, highest expected value first."""
    store = _load_store()
    rows = [dict(o) for o in store["orders"].values()
            if not state or o.get("state") == state]
    rows.sort(key=lambda o: (float(o.get("score") or 0.0),
                             float(o.get("created_at") or 0.0)), reverse=True)
    return rows[:max(1, int(limit))]


def get(order_id: str) -> dict:
    store = _load_store()
    order = store["orders"].get(str(order_id))
    if order is None:
        raise PromptLoopError(f"no work order {order_id!r}")
    return dict(order)


def _transition(order_id: str, *, expect: tuple[str, ...],
                mutate: Callable[[dict, dict], None]) -> dict:
    """Move one order between states under the lock, refusing on a stale read.

    ``expect`` is checked INSIDE the lock: a caller that read "open" a moment
    ago must not overwrite a state another process has since claimed."""
    with _locked():
        store = _load_store()
        order = store["orders"].get(str(order_id))
        if order is None:
            raise PromptLoopError(f"no work order {order_id!r}")
        state = str(order.get("state") or "")
        if state not in expect:
            raise PromptLoopError(
                f"work order {order_id} is {state!r}; expected one of "
                f"{', '.join(expect)}")
        mutate(store, order)
        _save_store(store)
        return dict(order)


def take(order_id: str, *, by: str = "") -> dict:
    """Claim an order. Marks it in flight so a second operator (or a second
    cycle) does not pick up the same work."""
    def _mutate(store: dict, order: dict) -> None:
        order["state"] = "taken"
        order["taken_by"] = _bound(by, 120)
        order["taken_at"] = time.time()
    return _transition(order_id, expect=("open",), mutate=_mutate)


def skip(order_id: str, *, reason: str = "") -> dict:
    """Decline an order. The loop learns from this: the source loses weight,
    the key sits out a cooldown, and enough skips mute it for good."""
    def _mutate(store: dict, order: dict) -> None:
        key, source = str(order.get("key")), str(order.get("source"))
        skips = int(store["skips"].get(key, 0)) + 1
        store["skips"][key] = skips
        store["cooldown"][key] = int(store.get("cycles") or 0) + _COOLDOWN_CYCLES
        store["affinity"][source] = max(
            0.5, _affinity(store, source) * 0.8)
        order["state"] = "muted" if skips >= _SKIPS_TO_MUTE else "skipped"
        order["skip_reason"] = _bound(reason, 500)
        order["closed_at"] = time.time()
    return _transition(order_id, expect=("open", "taken"), mutate=_mutate)


def complete(order_id: str, *, outcome: str = "", by: str = "",
             composer: ComposeFn | None = None) -> dict:
    """Close an order and compose its follow-up.

    This is the self-prompting step. ``outcome`` is screened before it is
    quoted into the next prompt; the chain is deterministic, so a run that
    reports "all clear" cannot talk the loop into escalating, and one that
    reports findings routes back to a fix rather than forward to a write-up.
    Returns ``{"order": ..., "next": ... | None}``."""
    clean = _screen_outcome(outcome)

    def _mutate(store: dict, order: dict) -> None:
        order["state"] = "done"
        order["outcome"] = clean
        order["completed_by"] = _bound(by, 120)
        order["closed_at"] = time.time()
        source = str(order.get("source"))
        store["affinity"][source] = min(1.5, _affinity(store, source) * 1.15)
        # Completing clears the skip penalty but starts a cooldown: a
        # still-true condition should come back if it is still true LATER, not
        # on the very next cycle, which would read as nagging.
        store["skips"].pop(str(order.get("key")), None)
        store["cooldown"][str(order.get("key"))] = (
            int(store.get("cycles") or 0) + _COOLDOWN_CYCLES)

    done = _transition(order_id, expect=("open", "taken"), mutate=_mutate)
    follow = _compose_follow_up(done, clean, composer=composer)
    return {"order": done, "next": follow}


def _found_problems(outcome: str) -> bool:
    """Did the reported outcome say the work found something?

    Deliberately conservative: an ambiguous or empty outcome after a
    verification counts as 'found something', because closing the loop with a
    clean bill of health is the claim that needs evidence, not the other way
    round."""
    text = (outcome or "").lower()
    if not text:
        return True
    clear = ("no findings", "nothing found", "all clear", "clean", "no issues",
             "no defects", "passed", "green")
    problems = ("found", "failing", "failed", "defect", "bug", "broken",
                "regression", "leak", "wrong")
    if any(p in text for p in problems):
        return True
    return not any(c in text for c in clear)


def _compose_follow_up(done: dict, outcome: str,
                       composer: ComposeFn | None = None) -> dict | None:
    """The next order in the chain, or None when the chain has ended."""
    if not done.get("chain", True):
        return None
    kind = str(done.get("kind") or "")
    nxt = _CHAIN.get(kind)
    if nxt is None:
        return None
    if kind == "verify" and _found_problems(outcome):
        # A verification that found something routes back to a fix rather than
        # forward to the write-up; the chain only ends on a clean pass.
        nxt = "fix"
    sig = Signal(
        source=str(done.get("source") or "chain"),
        key=f"{done.get('key')}#{nxt}",
        title=f"{nxt.capitalize()}: {done.get('title')}",
        detail=(f"This follows the completed order {done.get('id')} "
                f"({kind}). The loop composed it from that outcome — the "
                "previous step is only finished once this one is."),
        kind=nxt, risk=str(done.get("risk") or "low"),
        impact=min(1.0, float(done.get("components", {}).get("impact", 0.5))
                   + 0.05),
        confidence=0.9, effort=0.35,
        evidence=list(done.get("evidence") or [])[:_MAX_EVIDENCE],
    )
    order = compose(sig, cycle_no=int(done.get("cycle") or 0),
                    parent=str(done.get("id")), outcome=outcome,
                    composer=composer)
    with _locked():
        store = _load_store()
        order.score, order.components = rank(
            sig, affinity=_affinity(store, sig.source),
            novelty=_novelty(store, sig.key))
        store["orders"][order.id] = order.to_dict()
        _prune(store)
        _save_store(store)
    return order.to_dict()


# ---- governed dispatch (auto mode) ----------------------------------------

def _prompt_digest(order: dict) -> str:
    """Content commitment over exactly what would run.

    Whitespace-insensitive (a reflow is not an edit) but bound to the order id
    and kind, so an approval for one order can never authorize another, and
    editing the prompt after approval invalidates the approval instead of
    quietly running different words than the ones a human read."""
    canonical = json.dumps(
        {"id": str(order.get("id")), "kind": str(order.get("kind")),
         "prompt": " ".join(str(order.get("prompt") or "").split())},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _dispatch_action(order_id: str) -> str:
    return f"prompt-loop:dispatch:{order_id}"


def _within_ceiling(risk: str, ceiling: str) -> bool:
    try:
        return RISKS.index(risk) <= RISKS.index(ceiling)
    except ValueError:
        return False


def _park_dispatch_approval(order: dict, digest: str, by: str) -> int:
    from .safety.dual_control import required_approvals
    from .world_model import open_world
    return open_world().create_approval(
        _dispatch_action(str(order.get("id"))),
        risk=str(order.get("risk") or "low"),
        detail=(f"Run a self-composed work order as a goal: "
                f"{_bound(order.get('title'), 120)}. Digest {digest[:16]}."),
        provenance=PROVENANCE,
        approvals_required=required_approvals(str(order.get("risk") or "low")),
        requested_by=by or "")


def _consume_approval(store: dict, approval_id: int) -> None:
    """Spend a dispatch approval exactly once, with an eviction floor.

    The remembered list is bounded, so forgetting is inevitable; the floor
    makes forgetting refuse rather than forgive. An id at or below the highest
    evicted id is treated as already spent."""
    aid = int(approval_id)
    if aid <= int(store.get("approval_floor") or 0):
        raise PromptLoopError(
            f"approval #{aid} is at or below the spent-approval floor; it "
            "cannot authorize another dispatch")
    if aid in {int(x) for x in store.get("consumed_approvals", [])}:
        raise PromptLoopError(f"approval #{aid} has already been spent")
    consumed = sorted({*(int(x) for x in store.get("consumed_approvals", [])),
                       aid})
    while len(consumed) > _MAX_CONSUMED_APPROVALS:
        store["approval_floor"] = max(int(store.get("approval_floor") or 0),
                                      consumed.pop(0))
    store["consumed_approvals"] = consumed


def _approval_granted(approval_id: int) -> bool:
    from .world_model import open_world
    for a in open_world().list_approvals(limit=500):
        if int(getattr(a, "id", -1)) == int(approval_id):
            return str(getattr(a, "status", "") or "") == "approved"
    return False


def _bind_approval(order_id: str, approval_id: int, digest: str) -> int:
    """Attach a freshly parked approval to its order, resolving a race.

    Two dispatchers can each park an approval before either writes it down.
    The first one bound wins; the loser DENIES its own approval instead of
    abandoning it, so a race leaves one live request in the human's queue
    rather than a phantom that can never authorize anything."""
    with _locked():
        store = _load_store()
        row = store["orders"].get(str(order_id))
        if row is None:
            bound = 0
        elif row.get("approval_id"):
            bound = int(row["approval_id"])
        else:
            row["approval_id"] = int(approval_id)
            row["approval_digest"] = digest
            _save_store(store)
            return int(approval_id)
    try:
        from .world_model import open_world
        open_world().decide_approval(int(approval_id), "denied", "prompt-loop")
    except Exception:  # pragma: no cover -- cleanup never masks the result
        log.warning("prompt_loop: could not deny superseded approval #%s",
                    approval_id, exc_info=True)
    return bound or int(approval_id)


def dispatch(order_id: str, *, by: str = "") -> dict:
    """Hand an order back to the agent as a goal, under governance.

    Refuses unless ``auto`` mode is on, the order's risk is inside the ceiling,
    and (by default) a human has approved this exact prompt. The digest is
    re-checked at dispatch, not only at approval: an order edited after it was
    approved is refused, because the words a human read must be the words that
    run."""
    cfg = _settings()
    if cfg["mode"] != "auto":
        raise PromptLoopError(
            "the prompt loop is in advise mode; set [prompt_loop] mode = "
            '"auto" to let it run its own orders')
    order = get(order_id)
    if order.get("goal_id"):
        # Terminal state outranks everything below: telling a caller to retry
        # a dispatch that already happened is an instruction to duplicate it.
        return {"order": order, "goal_id": order["goal_id"],
                "status": "already_dispatched"}
    if str(order.get("state")) not in ("open", "taken"):
        raise PromptLoopError(
            f"work order {order_id} is {order.get('state')!r} and cannot be "
            "dispatched")
    if not _within_ceiling(str(order.get("risk") or "high"),
                           cfg["auto_risk_ceiling"]):
        raise PromptLoopError(
            f"work order {order_id} is {order.get('risk')!r} risk; the auto "
            f"ceiling is {cfg['auto_risk_ceiling']!r}. A human runs this one")
    digest = _prompt_digest(order)

    if cfg["require_approval"]:
        approval_id = order.get("approval_id")
        if not approval_id:
            approval_id = _park_dispatch_approval(order, digest, by)
            approval_id = _bind_approval(order_id, approval_id, digest)
            return {"order": get(order_id), "goal_id": None,
                    "status": "awaiting_approval", "approval_id": int(approval_id)}
        if order.get("approval_digest") != digest:
            raise PromptLoopError(
                f"work order {order_id} changed after approval #{approval_id} "
                "was parked; the approval is void. Re-request it")
        if not _approval_granted(int(approval_id)):
            return {"order": order, "goal_id": None,
                    "status": "awaiting_approval",
                    "approval_id": int(approval_id)}

    return _dispatch_now(order_id, digest, by, cfg)


def _dispatch_now(order_id: str, digest: str, by: str, cfg: dict) -> dict:
    """Create the goal and record it, once. The claim happens under the lock
    before the goal is created, so two racing dispatchers cannot both win."""
    from .world_model import open_world
    with _locked():
        store = _load_store()
        row = store["orders"].get(str(order_id))
        if row is None:
            raise PromptLoopError(f"no work order {order_id!r}")
        if row.get("goal_id"):
            return {"order": dict(row), "goal_id": row["goal_id"],
                    "status": "already_dispatched"}
        if _prompt_digest(row) != digest:
            raise PromptLoopError(
                f"work order {order_id} changed while it was being "
                "dispatched; refusing")
        if cfg["require_approval"]:
            _consume_approval(store, int(row.get("approval_id") or 0))
        row["state"] = "taken"
        row["dispatching"] = True
        _save_store(store)
        claimed = dict(row)

    try:
        goal_id = open_world().create_goal(
            _bound(claimed.get("title"), _MAX_TITLE),
            description=str(claimed.get("prompt") or ""),
            owner=by or "prompt-loop")
    except Exception:
        # The claim is released so a transient world failure does not strand
        # the order forever -- but the approval stays spent, so recovery is a
        # deliberate re-request rather than a silent retry.
        with _locked():
            store = _load_store()
            stranded = store["orders"].get(str(order_id))
            if stranded is not None:
                stranded["state"] = "open"
                stranded.pop("dispatching", None)
                stranded["dispatch_error"] = True
                _save_store(store)
        raise

    with _locked():
        store = _load_store()
        row = store["orders"].get(str(order_id))
        if row is not None:
            row["goal_id"] = int(goal_id)
            row.pop("dispatching", None)
            _save_store(store)
            claimed = dict(row)
    dispatched = claimed
    from .audit import EventKind, audit_event
    audit_event(EventKind.PROMPT_LOOP_DISPATCHED, agent="prompt_loop",
                goal_id=int(goal_id), order=str(order_id),
                digest=digest[:16],
                order_kind=str(dispatched.get("kind") or ""),
                risk=str(dispatched.get("risk") or ""),
                dispatched_by=_bound(by, 120))
    return {"order": dispatched, "goal_id": int(goal_id),
            "status": "dispatched"}


# ---- read surfaces --------------------------------------------------------

def status() -> dict:
    """Counts for the health surface and the dashboard header."""
    cfg = _settings()
    try:
        store = _load_store()
    except PromptLoopError:
        return {"enabled": cfg["enable"], "mode": cfg["mode"],
                "readable": False, "open": 0, "taken": 0, "done": 0,
                "muted": 0, "cycles": 0}
    counts = dict.fromkeys(STATES, 0)
    for o in store["orders"].values():
        state = str(o.get("state") or "open")
        if state in counts:
            counts[state] += 1
    return {"enabled": cfg["enable"], "mode": cfg["mode"], "readable": True,
            "compose_with_model": cfg["compose_with_model"],
            "auto_risk_ceiling": cfg["auto_risk_ceiling"],
            "cycles": int(store.get("cycles") or 0), **counts}


def render(orders: list[dict]) -> str:
    """The terminal view: the whole prompt, ready to copy."""
    if not orders:
        return ("Nothing needs you right now. Run `maverick loop cycle` to "
                "look again.")
    blocks = []
    for i, o in enumerate(orders, 1):
        blocks.append(
            f"[{i}] {o.get('title')}\n"
            f"    id {o.get('id')}  ·  {o.get('kind')}  ·  {o.get('risk')} "
            f"risk  ·  score {o.get('score')}  ·  from {o.get('source')}\n"
            f"{'-' * 72}\n{o.get('prompt')}\n")
    return "\n".join(blocks)
