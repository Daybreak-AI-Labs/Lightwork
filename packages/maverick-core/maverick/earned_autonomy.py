"""Earned Autonomy -- consequence-proven trust (moonshot Bet 5).

The biggest blocker to enterprise agents isn't quality, it's trust to take
irreversible action. Everyone ships agents that draft; almost no one ships
agents that *act*, because the downside is catastrophic and unprovable. This
module makes autonomy something an agent **earns, action type by action type,
by proving it predicts consequences correctly -- with a guaranteed undo until
it has.**

Three pieces, fused:

* **Consequence cards** (:class:`ConsequenceCard`, :class:`CardStore`): before
  a high-stakes action runs, its *predicted* outcome (from the rehearsal twin
  or a governed-action simulation) is pinned into a hash-chained, append-only
  card store -- closing the gap where rehearsal predictions vanished on
  PROCEED. A card is a commitment: "we predicted THIS before acting".
* **The predicted-vs-actual join** (:meth:`EarnedAutonomyEngine.reconcile`):
  when reality reports back through the existing Consequence Engine
  (:mod:`maverick.consequence`, keyed ``(goal_id, episode_id)``), each card is
  scored hit/miss against the real outcome. The accuracy record per
  ``(principal, action)`` is the trust evidence -- reality grades the
  predictor, so the signal can't be gamed by the model grading itself.
* **The autonomy dial driven by evidence**: an action type whose predictions
  keep landing graduates from "a human approves" to "policy auto-approves" --
  implemented as a *revocable standing consent grant*
  (:func:`maverick.safety.consent.grant_persistent`), so the existing agent
  gate chain honours it with no new hot-path code. One miss demotes instantly
  (trust is asymmetric); demotion revokes the grant before it records the
  evidence, so authority is never ahead of the record.

Interlocks (all fail toward "keep the human"): graduation is refused while
:func:`maverick.calibration.learning_frozen` (a drifting verifier must not
mint autonomy), during a learning HALT, for action types above the
``max_auto_risk`` ceiling (default ``medium`` -- graduating irreversible
high-risk actions is an explicit operator decision), and for action types
whose cards don't declare reversibility when ``require_reversible`` is on.
Graduation only ever converts require-human into auto-approve for an action
the capability envelope, governance rules, and budget caps ALREADY permit --
it changes who approves, never what is permitted, so it cannot widen the
envelope. Every card, hit, miss, graduation, and demotion is signed into the
audit chain.

The compensating half of the bet -- "every action ships its inverse" -- is
:func:`run_saga`: a sequence of :class:`SagaStep` (do + undo) that refuses to
start while any step lacks an inverse (unless explicitly overridden) and rolls
the completed prefix back, in reverse, on the first failure.

Posture: OFF by default (kernel rule 1) behind ``[earned_autonomy] enable`` /
``MAVERICK_EARNED_AUTONOMY``; and even when on, graduation takes effect only
once the operator arms ``auto_graduate`` (the authority-widening switch is
strict-parsed -- a truthy string never arms it). Recording evidence is
harmless; deciding fails closed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import env_flag

log = logging.getLogger(__name__)

_GENESIS = "0" * 64
_MAX_ROWS = 100_000
_MAX_TEXT = 300


def enabled() -> bool:
    """Whether earned autonomy records evidence / decides. OFF by default."""
    _v = env_flag("MAVERICK_EARNED_AUTONOMY")
    if _v is not None:
        return _v
    try:
        from .config import get_earned_autonomy

        return bool(get_earned_autonomy().get("enable", False))
    except Exception:  # pragma: no cover -- config never blocks a run
        return False


def _clamp01(value: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v):
        return 0.0
    return max(0.0, min(1.0, v))


def _safe_text(value: object, *, max_len: int = _MAX_TEXT) -> str:
    """Bound + secret-redact free text before it reaches a durable card."""
    text = str(value or "")
    try:
        from .safety.secret_detector import redact

        text, _matches = redact(text)
    except Exception:  # pragma: no cover -- redactor optional; keep it bounded
        pass
    return str(text)[:max_len]


# ---------------------------------------------------------------------------
# hash-chained append-only NDJSON log (the shared shape of both stores)
# ---------------------------------------------------------------------------


class _ChainedLog:
    """Append-only NDJSON rows, each carrying ``prev``/``hash`` sha256 links.

    The tamper-evident spine both the card store and the trust ledger sit on:
    genesis is 64 zeros, each row's hash covers all its fields plus the
    previous hash (the :mod:`maverick.governed_actions` lineage recipe).
    ``path=None`` keeps the chain in memory (tests / ephemeral runs). Appends
    never raise; they return False on any persistence failure.
    """

    def __init__(self, path: Path | None = None, *, max_rows: int = _MAX_ROWS) -> None:
        self.path = Path(path) if path is not None else None
        self.max_rows = max_rows
        self._rows: list[dict] = []
        self._lock = threading.Lock()
        if self.path is not None:
            self._load()

    @staticmethod
    def _row_hash(fields: dict, prev: str) -> str:
        material = json.dumps({**fields, "prev": prev}, sort_keys=True, default=str)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def append(self, fields: dict) -> bool:
        with self._lock:
            if len(self._rows) >= self.max_rows:
                log.warning("earned-autonomy log at capacity; row dropped")
                return False
            prev = self._rows[-1]["hash"] if self._rows else _GENESIS
            row = dict(fields)
            row["prev"] = prev
            row["hash"] = self._row_hash(fields, prev)
            if self.path is not None:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with open(
                        os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600),
                        "a", encoding="utf-8",
                    ) as fh:
                        fh.write(json.dumps(row, sort_keys=True) + "\n")
                except Exception:  # pragma: no cover -- best-effort persistence
                    log.debug("earned-autonomy append failed", exc_info=True)
                    return False
            self._rows.append(row)
            return True

    def rows(self) -> list[dict]:
        with self._lock:
            return list(self._rows)

    def verify(self) -> str:
        """Recompute the chain: ``VALID: ...`` or ``BROKEN: link i ...``."""
        with self._lock:
            prev = _GENESIS
            for i, row in enumerate(self._rows):
                fields = {k: v for k, v in row.items() if k not in ("prev", "hash")}
                if row.get("prev") != prev:
                    return f"BROKEN: link {i} prev_hash mismatch"
                if row.get("hash") != self._row_hash(fields, prev):
                    return f"BROKEN: link {i} content hash mismatch"
                prev = row["hash"]
            head = prev[:12] if self._rows else _GENESIS[:12]
            return f"VALID: {len(self._rows)} link(s), head {head}"

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            return
        for raw in lines[: self.max_rows]:
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if isinstance(row, dict) and "hash" in row:
                self._rows.append(row)


# ---------------------------------------------------------------------------
# consequence cards: the persisted prediction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsequenceCard:
    """One pinned prediction for a high-stakes action, made BEFORE it ran.

    ``predicted_outcome`` is the [0, 1] result the system committed to (from
    the rehearsal twin or a simulation); ``reversible`` declares whether the
    action ships an inverse (a saga/compensation path). ``params_sha256``
    pins *what* was predicted without persisting raw arguments. The
    ``(goal_id, episode_id)`` key is the join back to reality via
    :func:`maverick.consequence.resolve`.
    """

    principal: str
    action: str
    risk: str
    predicted_outcome: float
    goal_id: int
    episode_id: int
    effect: str = ""
    exposure_dollars: float | None = None
    reversible: bool = False
    source: str = "declared"
    params_sha256: str = ""
    ts: float = 0.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_row(self) -> dict:
        return {
            "id": self.id, "ts": self.ts, "principal": self.principal,
            "action": self.action, "risk": self.risk,
            "predicted_outcome": self.predicted_outcome,
            "goal_id": self.goal_id, "episode_id": self.episode_id,
            "effect": self.effect, "exposure_dollars": self.exposure_dollars,
            "reversible": self.reversible, "source": self.source,
            "params_sha256": self.params_sha256,
        }


@dataclass
class CardStore:
    """Hash-chained store of consequence cards. ``path=None`` = in-memory."""

    path: Path | None = None
    max_rows: int = _MAX_ROWS
    _log: _ChainedLog = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self._log is None:
            self._log = _ChainedLog(self.path, max_rows=self.max_rows)

    def append(self, card: ConsequenceCard) -> bool:
        return self._log.append(card.to_row())

    def cards(self) -> list[dict]:
        """All recorded cards, oldest first."""
        return self._log.rows()

    def verify(self) -> str:
        return self._log.verify()


# ---------------------------------------------------------------------------
# trust ledger: hit/miss evidence and graduation state per (principal, action)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrustState:
    """Reduced evidence for one ``(principal, action)`` pair."""

    principal: str
    action: str
    hits: int = 0
    misses: int = 0
    streak: int = 0
    graduated: bool = False
    last_event_ts: float = 0.0

    @property
    def samples(self) -> int:
        return self.hits + self.misses

    @property
    def accuracy(self) -> float:
        return self.hits / self.samples if self.samples else 0.0


@dataclass
class TrustLedger:
    """Event-sourced, hash-chained record of scoring and graduation events.

    Events: ``hit`` / ``miss`` (a card scored against reality), ``graduate``
    (auto-approval granted), ``demote`` (a miss withdrew it), ``revoke`` (an
    operator withdrew it). State is a pure reduction over the chain, so the
    evidence behind any authority change is replayable from the file alone.
    """

    path: Path | None = None
    max_rows: int = _MAX_ROWS
    _log: _ChainedLog = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self._log is None:
            self._log = _ChainedLog(self.path, max_rows=self.max_rows)

    def record_event(self, kind: str, principal: str, action: str, *,
                     card_id: str = "", predicted: float | None = None,
                     actual: float | None = None, reason: str = "",
                     ts: float | None = None) -> bool:
        if kind not in ("hit", "miss", "graduate", "demote", "revoke"):
            return False
        row = {
            "kind": kind, "principal": str(principal)[:120],
            "action": str(action)[:120], "card_id": str(card_id)[:32],
            "ts": ts if ts is not None else time.time(),
            "reason": _safe_text(reason, max_len=200),
        }
        if predicted is not None:
            row["predicted"] = _clamp01(predicted)
        if actual is not None:
            row["actual"] = _clamp01(actual)
        return self._log.append(row)

    def scored_ids(self) -> frozenset[str]:
        """Card ids already scored -- the reconcile idempotency set."""
        return frozenset(
            r["card_id"] for r in self._log.rows()
            if r.get("kind") in ("hit", "miss") and r.get("card_id"))

    def state(self, principal: str, action: str) -> TrustState:
        return self.states().get(
            (str(principal), str(action)),
            TrustState(principal=str(principal), action=str(action)))

    def states(self) -> dict[tuple[str, str], TrustState]:
        """Reduce the event chain to per-(principal, action) trust state."""
        out: dict[tuple[str, str], TrustState] = {}
        for r in self._log.rows():
            key = (str(r.get("principal", "")), str(r.get("action", "")))
            s = out.get(key) or TrustState(principal=key[0], action=key[1])
            kind = r.get("kind")
            ts = float(r.get("ts", 0.0) or 0.0)
            if kind == "hit":
                s = TrustState(key[0], key[1], s.hits + 1, s.misses,
                               s.streak + 1, s.graduated, ts)
            elif kind == "miss":
                s = TrustState(key[0], key[1], s.hits, s.misses + 1,
                               0, s.graduated, ts)
            elif kind == "graduate":
                s = TrustState(key[0], key[1], s.hits, s.misses,
                               s.streak, True, ts)
            elif kind in ("demote", "revoke"):
                s = TrustState(key[0], key[1], s.hits, s.misses,
                               s.streak, False, ts)
            else:  # pragma: no cover -- record_event rejects unknown kinds
                continue
            out[key] = s
        return out

    def verify(self) -> str:
        return self._log.verify()


# ---------------------------------------------------------------------------
# graduation policy + gates (pure, controller-style: every gate fails closed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GraduationPolicy:
    """The evidence bar an action type must clear to earn auto-approval.

    Deliberately stricter than :mod:`maverick.predictive_approvals` (which
    only ever *suggests*, and never for high risk): this policy *decides*, so
    it demands a consecutive-hit streak against ground truth, not just a
    lopsided approval history. ``armed`` is the operator's authority-widening
    switch (``[earned_autonomy] auto_graduate``).
    """

    min_streak: int = 10
    min_samples: int = 10
    min_accuracy: float = 0.9
    tolerance: float = 0.25
    max_auto_risk: str = "medium"
    require_reversible: bool = True
    armed: bool = False


def policy_from_config() -> GraduationPolicy:
    """The configured policy; malformed config degrades to the strict default."""
    try:
        from .config import get_earned_autonomy

        cfg = get_earned_autonomy()
        return GraduationPolicy(
            min_streak=int(cfg["min_streak"]), min_samples=int(cfg["min_samples"]),
            min_accuracy=float(cfg["min_accuracy"]), tolerance=float(cfg["tolerance"]),
            max_auto_risk=str(cfg["max_auto_risk"]),
            require_reversible=bool(cfg["require_reversible"]),
            armed=bool(cfg["auto_graduate"]),
        )
    except Exception:  # pragma: no cover -- policy loss must not widen authority
        return GraduationPolicy()


@dataclass(frozen=True)
class GateResult:
    gate: str
    ok: bool
    reason: str = ""


@dataclass(frozen=True)
class EarnedVerdict:
    """Whether one (principal, action) pair may graduate. AND of all gates."""

    principal: str
    action: str
    graduate: bool
    gates: tuple[GateResult, ...]
    blocking_reason: str = ""


def _ceiling_rank(level: str) -> int:
    """Rank of the ``max_auto_risk`` ceiling; unknown = below every level.

    Mirrors ``self_improvement._ceiling_rank``: a typo ceiling must be the
    MOST restrictive (nothing graduates), never silently permissive.
    """
    try:
        from .safety.tool_risk import RISK_LEVELS

        return RISK_LEVELS.index(str(level))
    except ValueError:
        return -1


def _action_risk_rank(risk: str) -> int:
    from .safety.tool_risk import risk_rank

    return risk_rank(risk)


def evaluate_graduation(state: TrustState, *, risk: str, reversible_share: float,
                        policy: GraduationPolicy, frozen: bool) -> EarnedVerdict:
    """Run every graduation gate; all must pass, each fails closed."""
    gates: list[GateResult] = []

    gates.append(GateResult(
        "armed", policy.armed,
        "" if policy.armed else "auto_graduate is not armed by the operator"))

    valid = (
        isinstance(policy.min_streak, int) and policy.min_streak >= 1
        and isinstance(policy.min_samples, int) and policy.min_samples >= 1
        and math.isfinite(float(policy.min_accuracy))
        and 0.0 <= float(policy.min_accuracy) <= 1.0
    )
    if not valid:
        gates.append(GateResult("evidence", False, "invalid graduation policy"))
    else:
        ok = (state.samples >= policy.min_samples
              and state.streak >= policy.min_streak
              and state.accuracy >= policy.min_accuracy)
        reason = ""
        if state.samples < policy.min_samples:
            reason = f"insufficient evidence: {state.samples} < {policy.min_samples} outcomes"
        elif state.streak < policy.min_streak:
            reason = f"streak {state.streak} < {policy.min_streak} consecutive accurate predictions"
        elif state.accuracy < policy.min_accuracy:
            reason = f"accuracy {state.accuracy:.2f} < {policy.min_accuracy:.2f}"
        gates.append(GateResult("evidence", ok, reason))

    risk_ok = _action_risk_rank(risk) <= _ceiling_rank(policy.max_auto_risk)
    gates.append(GateResult(
        "risk_ceiling", risk_ok,
        "" if risk_ok else
        f"{risk!r}-risk action type is above the max_auto_risk "
        f"ceiling ({policy.max_auto_risk!r})"))

    if policy.require_reversible:
        rev_ok = reversible_share >= 1.0
        gates.append(GateResult(
            "reversibility", rev_ok,
            "" if rev_ok else
            "action type has cards without a declared inverse; a guaranteed "
            "undo is required until autonomy is earned"))
    else:
        gates.append(GateResult("reversibility", True, ""))

    gates.append(GateResult(
        "calibration", not frozen,
        "" if not frozen else "learning frozen: a drifting verifier cannot mint autonomy"))

    failing = [g for g in gates if not g.ok]
    return EarnedVerdict(
        state.principal, state.action, not failing, tuple(gates),
        failing[0].reason if failing else "")


# ---------------------------------------------------------------------------
# the engine: record -> reconcile -> graduate/demote -> decide
# ---------------------------------------------------------------------------


def _default_frozen_fn() -> bool:
    from .calibration import learning_frozen

    return bool(learning_frozen())


def _default_audit_fn(kind: str, **payload) -> None:
    try:
        from .audit import record

        goal_id = payload.pop("goal_id", None)
        record(kind, agent="earned_autonomy", goal_id=goal_id, **payload)
    except Exception:  # pragma: no cover -- audit is best-effort, never blocks
        log.debug("earned-autonomy audit skipped", exc_info=True)


def _default_grant_fn(action: str) -> None:
    from .safety.consent import grant_persistent

    grant_persistent(action)


def _default_revoke_fn(action: str) -> None:
    from .safety.consent import revoke

    revoke(action)


@dataclass(frozen=True)
class EarnedDecision:
    """The dial's answer for one action right now."""

    auto: bool
    reason: str = ""


@dataclass(frozen=True)
class ReconcileReport:
    scored: int = 0
    hits: int = 0
    misses: int = 0
    graduated: tuple[str, ...] = ()
    demoted: tuple[str, ...] = ()


@dataclass
class EarnedAutonomyEngine:
    """Record predictions, join reality, and move the autonomy dial on proof.

    Grants and revocations are injected callables so the decision logic is
    deterministic offline; the defaults write the sanctioned consent-ledger
    standing grant (which the existing agent approval path already honours)
    and revoke it. Order of operations is asymmetric on purpose: a demotion
    revokes authority BEFORE recording the event (losing authority must never
    lag), while a graduation takes authority only AFTER the grant succeeded
    and is compensated if the evidence write then fails.
    """

    cards: CardStore = field(default_factory=CardStore)
    ledger: TrustLedger = field(default_factory=TrustLedger)
    policy: GraduationPolicy | None = None
    frozen_fn: Callable[[], bool] = _default_frozen_fn
    audit_fn: Callable[..., None] = _default_audit_fn
    grant_fn: Callable[[str], None] = _default_grant_fn
    revoke_fn: Callable[[str], None] = _default_revoke_fn
    now: Callable[[], float] = time.time

    def _policy(self) -> GraduationPolicy:
        return self.policy if self.policy is not None else policy_from_config()

    def _frozen(self) -> bool:
        try:
            return bool(self.frozen_fn())
        except Exception:
            return True  # can't confirm the judge is honest -> fail closed

    def _audit(self, kind: str, **payload) -> None:
        try:
            self.audit_fn(kind, **payload)
        except Exception:  # pragma: no cover -- audit sink must never block
            log.debug("earned-autonomy audit sink failed", exc_info=True)

    # -- recording ---------------------------------------------------------

    def record_card(self, *, principal: str, action: str, risk: str,
                    predicted_outcome: float, goal_id: int, episode_id: int,
                    effect: str = "", exposure_dollars: float | None = None,
                    reversible: bool = False, source: str = "declared",
                    params_sha256: str = "", ts: float | None = None) -> str | None:
        """Pin one prediction. Returns the card id, or None when not recorded."""
        if not enabled():
            return None
        from .learning_guard import learning_write_allowed

        if not learning_write_allowed("earned_autonomy", "record_card"):
            return None
        card = ConsequenceCard(
            principal=str(principal)[:120], action=str(action)[:120],
            risk=str(risk), predicted_outcome=_clamp01(predicted_outcome),
            goal_id=int(goal_id), episode_id=int(episode_id),
            effect=_safe_text(effect), exposure_dollars=exposure_dollars,
            reversible=bool(reversible), source=str(source)[:32],
            params_sha256=str(params_sha256)[:64],
            ts=ts if ts is not None else self.now(),
        )
        if not self.cards.append(card):
            return None
        from .audit import EventKind

        self._audit(EventKind.CONSEQUENCE_CARD, goal_id=card.goal_id,
                    card=card.id, principal=card.principal, action=card.action,
                    risk=card.risk, predicted=card.predicted_outcome,
                    episode_id=card.episode_id, reversible=card.reversible,
                    source=card.source)
        return card.id

    # -- the join ----------------------------------------------------------

    def reconcile(self, *, resolve: Callable[[int, int], float | None] | None = None,
                  ) -> ReconcileReport:
        """Score unscored cards against reality; graduate/demote on the result.

        ``resolve(goal_id, episode_id) -> actual | None`` defaults to the
        Consequence Engine's grounded-outcome join. Idempotent: a card is
        scored at most once (the ledger's hit/miss events are the record).
        """
        if not enabled():
            return ReconcileReport()
        from .learning_guard import learning_write_allowed

        if not learning_write_allowed("earned_autonomy", "reconcile"):
            return ReconcileReport()
        if resolve is None:
            from .consequence import resolve as _resolve

            resolve = _resolve
        policy = self._policy()
        scored_ids = self.ledger.scored_ids()
        touched: dict[tuple[str, str], str] = {}
        scored = hits = misses = 0
        demoted: list[str] = []
        for row in self.cards.cards():
            if row.get("id") in scored_ids or int(row.get("episode_id", 0) or 0) <= 0:
                continue
            try:
                actual = resolve(int(row["goal_id"]), int(row["episode_id"]))
            except Exception:  # pragma: no cover -- a broken join scores nothing
                continue
            if actual is None:
                continue
            hit = abs(_clamp01(row.get("predicted_outcome", 0.0)) - _clamp01(actual)
                      ) <= policy.tolerance
            key = (str(row.get("principal", "")), str(row.get("action", "")))
            self.ledger.record_event(
                "hit" if hit else "miss", key[0], key[1], card_id=str(row.get("id", "")),
                predicted=row.get("predicted_outcome"), actual=actual, ts=self.now())
            scored += 1
            hits += int(hit)
            misses += int(not hit)
            touched[key] = str(row.get("risk", "medium"))
            if not hit:
                self._demote(key[0], key[1], card_id=str(row.get("id", "")),
                             demoted=demoted)
        graduated = self._graduate_touched(touched, policy)
        return ReconcileReport(scored=scored, hits=hits, misses=misses,
                               graduated=tuple(graduated), demoted=tuple(demoted))

    def _demote(self, principal: str, action: str, *, card_id: str,
                demoted: list[str]) -> None:
        """One missed prediction withdraws auto-approval immediately."""
        if not self.ledger.state(principal, action).graduated:
            return
        try:
            self.revoke_fn(action)
        except Exception:  # pragma: no cover -- keep demoting even if no grant
            log.warning("earned-autonomy grant revoke failed for %s", action,
                        exc_info=True)
        self.ledger.record_event(
            "demote", principal, action, card_id=card_id,
            reason="prediction missed reality; trust is withdrawn on one miss",
            ts=self.now())
        demoted.append(action)
        from .audit import EventKind

        self._audit(EventKind.AUTONOMY_GRADUATION, decision="demote",
                    principal=principal, action=action, card=card_id)

    def _graduate_touched(self, touched: dict[tuple[str, str], str],
                          policy: GraduationPolicy) -> list[str]:
        """Evaluate every pair the reconcile touched; grant on a full pass."""
        graduated: list[str] = []
        frozen = self._frozen()
        for (principal, action), risk in touched.items():
            state = self.ledger.state(principal, action)
            if state.graduated:
                continue
            verdict = evaluate_graduation(
                state, risk=risk,
                reversible_share=self._reversible_share(principal, action),
                policy=policy, frozen=frozen)
            if not verdict.graduate:
                continue
            try:
                self.grant_fn(action)
            except Exception:
                log.warning("earned-autonomy grant failed for %s", action,
                            exc_info=True)
                continue  # no grant -> no graduation record
            if not self.ledger.record_event(
                    "graduate", principal, action,
                    reason=f"{state.streak} consecutive accurate predictions "
                           f"({state.hits}/{state.samples} overall)",
                    ts=self.now()):
                # Authority must never outlive its evidence: compensate.
                try:
                    self.revoke_fn(action)
                except Exception:  # pragma: no cover
                    log.warning("earned-autonomy grant compensation failed for %s",
                                action, exc_info=True)
                continue
            graduated.append(action)
            from .audit import EventKind

            self._audit(EventKind.AUTONOMY_GRADUATION, decision="graduate",
                        principal=principal, action=action, streak=state.streak,
                        hits=state.hits, misses=state.misses)
        return graduated

    def _reversible_share(self, principal: str, action: str) -> float:
        rows = [r for r in self.cards.cards()
                if r.get("principal") == principal and r.get("action") == action]
        if not rows:
            return 0.0
        return sum(1 for r in rows if r.get("reversible") is True) / len(rows)

    # -- deciding ----------------------------------------------------------

    def decide(self, principal: str, action: str, *, risk: str | None = None,
               ) -> EarnedDecision:
        """May ``action`` auto-run for ``principal`` right now?

        Read-only; every uncertainty resolves to "keep the human". The live
        enforcement path is the consent-ledger grant the graduation wrote --
        this is the queryable answer (CLI / dashboard / integrations).
        """
        if not enabled():
            return EarnedDecision(False, "earned autonomy disabled")
        policy = self._policy()
        if not policy.armed:
            return EarnedDecision(False, "auto_graduate is not armed")
        try:
            from . import killswitch

            if killswitch.is_active():
                return EarnedDecision(False, "operational halt active")
        except Exception:
            return EarnedDecision(False, "halt state unavailable")
        if self._frozen():
            return EarnedDecision(False, "learning frozen: autonomy suspended")
        if risk is None:
            try:
                from .safety.tool_risk import tool_risk

                risk = tool_risk(action)
            except Exception:
                return EarnedDecision(False, "action risk unavailable")
        if _action_risk_rank(risk) > _ceiling_rank(policy.max_auto_risk):
            return EarnedDecision(
                False, f"{risk!r} risk is above the max_auto_risk ceiling")
        state = self.ledger.state(principal, action)
        if not state.graduated:
            return EarnedDecision(
                False, f"not graduated: streak {state.streak}, "
                       f"{state.hits}/{state.samples} accurate")
        return EarnedDecision(
            True, f"graduated: {state.hits}/{state.samples} accurate predictions")

    def revoke(self, principal: str, action: str, *, reason: str = "operator revoke",
               ) -> bool:
        """Operator withdrawal of an earned grant. Always available."""
        try:
            self.revoke_fn(action)
        except Exception:  # pragma: no cover -- revoke the record regardless
            log.warning("earned-autonomy grant revoke failed for %s", action,
                        exc_info=True)
        ok = self.ledger.record_event("revoke", principal, action, reason=reason,
                                      ts=self.now())
        from .audit import EventKind

        self._audit(EventKind.AUTONOMY_GRADUATION, decision="revoke",
                    principal=principal, action=action, reason=_safe_text(reason))
        return ok

    def status(self) -> list[TrustState]:
        """Every (principal, action) trust state, most-proven first."""
        return sorted(self.ledger.states().values(),
                      key=lambda s: (-int(s.graduated), -s.streak, -s.hits))


# ---------------------------------------------------------------------------
# compensating-action saga: every action ships its inverse
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SagaStep:
    """One consequential step and the inverse that undoes it."""

    name: str
    do: Callable[[], str]
    undo: Callable[[], str] | None = None


@dataclass(frozen=True)
class StepResult:
    step: str
    phase: str  # "do" | "undo"
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class SagaResult:
    """``committed`` iff every step succeeded; else the prefix was rolled back."""

    committed: bool
    results: tuple[StepResult, ...] = ()
    compensated: tuple[StepResult, ...] = ()
    reason: str = ""


def _run_step(fn: Callable[[], str], name: str, phase: str) -> StepResult:
    try:
        return StepResult(name, phase, True, _safe_text(fn()))
    except Exception as exc:
        return StepResult(name, phase, False,
                          _safe_text(f"{type(exc).__name__}: {exc}"))


def run_saga(steps: list[SagaStep] | tuple[SagaStep, ...], *,
             allow_irreversible: bool = False) -> SagaResult:
    """Execute steps in order; roll the completed prefix back on any failure.

    Fail-closed BEFORE any effect: unless ``allow_irreversible`` is set, every
    step must declare an inverse -- an action without an undo never starts.
    Compensation runs the completed steps' inverses in reverse order; a failed
    inverse is recorded (never silently swallowed) and the remaining inverses
    still run. The result is the caller's receipt: ``committed`` means all
    effects stand, otherwise none should (any failed compensation is listed).
    """
    steps = tuple(steps)
    if not allow_irreversible:
        missing = [s.name for s in steps if s.undo is None]
        if missing:
            return SagaResult(
                False, reason=(
                    "refused before any effect: step(s) without an inverse: "
                    + ", ".join(missing)))
    done: list[tuple[SagaStep, StepResult]] = []
    for step in steps:
        result = _run_step(step.do, step.name, "do")
        done.append((step, result))
        if not result.ok:
            compensated = tuple(
                _run_step(s.undo, s.name, "undo")
                for s, _r in reversed(done[:-1])
                if s.undo is not None
            )
            return SagaResult(
                False, results=tuple(r for _, r in done), compensated=compensated,
                reason=f"step {step.name!r} failed; rolled back {len(compensated)} step(s)")
    return SagaResult(True, results=tuple(r for _, r in done))


# ---------------------------------------------------------------------------
# run-path wiring
# ---------------------------------------------------------------------------

_shared: dict = {}
_shared_lock = threading.Lock()


def shared() -> EarnedAutonomyEngine:
    from .paths import data_dir

    cards_path = data_dir("consequence_cards.ndjson")
    with _shared_lock:
        engine = _shared.get(cards_path)
        if engine is None:
            engine = EarnedAutonomyEngine(
                cards=CardStore(path=cards_path),
                ledger=TrustLedger(path=data_dir("earned_autonomy.ndjson")))
            _shared[cards_path] = engine
        return engine


def reset_shared() -> None:
    with _shared_lock:
        _shared.clear()


def capture_prediction(verdict, *, principal: str, action: str, risk: str,
                       goal_id: int, episode_id: int, reversible: bool = False,
                       engine: EarnedAutonomyEngine | None = None) -> str | None:
    """Pin a rehearsal verdict as a consequence card. Never raises.

    The default-off hook the agent loop calls after the rehearsal twin speaks:
    a verdict the model actually vouched for (``known``) becomes evidence; the
    disabled/no-support placeholder (predicted 0.5, ``known=False``) is
    filtered so sentinels can't launder into accuracy.
    """
    try:
        if not enabled() or not getattr(verdict, "known", False):
            return None
        return (engine or shared()).record_card(
            principal=principal, action=action, risk=risk,
            predicted_outcome=float(verdict.predicted_outcome),
            goal_id=goal_id, episode_id=episode_id,
            effect=getattr(verdict, "reason", ""), reversible=reversible,
            source="rehearsal")
    except Exception:  # pragma: no cover -- capture must never break the loop
        log.debug("consequence-card capture skipped", exc_info=True)
        return None


__all__ = [
    "CardStore",
    "ConsequenceCard",
    "EarnedAutonomyEngine",
    "EarnedDecision",
    "EarnedVerdict",
    "GateResult",
    "GraduationPolicy",
    "ReconcileReport",
    "SagaResult",
    "SagaStep",
    "StepResult",
    "TrustLedger",
    "TrustState",
    "capture_prediction",
    "enabled",
    "evaluate_graduation",
    "policy_from_config",
    "reset_shared",
    "run_saga",
    "shared",
]
