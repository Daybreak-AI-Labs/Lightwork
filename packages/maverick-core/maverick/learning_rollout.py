"""Governed rollout of learning -- Apollo-style, for self-improving agents.

Palantir's Apollo promotes releases across regulated environments only behind
health constraints, in stages, with automatic rollback. The same discipline,
applied to *learning*: a distilled skill (or any learned update) is promoted to
the fleet ONLY if eval/health constraints pass, rolled out in stages
(canary -> half -> full), and AUTO-ROLLED-BACK the moment a constraint fails.

This is the fleet dimension of provable, governed learning -- "one agent learns"
becomes "the fleet learns, safely, with proof". The pure orchestration
(:func:`run_rollout`) is deterministic and offline-tested with injected
deploy/rollback/constraints; :func:`promote_skill_live` wires the real
snapshot+rollback (``maverick.dreaming``) and signed learning audit. Invoked
deliberately by an operator/loop -- nothing auto-promotes, so the kernel is
unchanged out of the box.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field

from .learning_guard import Halted, check_learning_halt

log = logging.getLogger(__name__)

# A constraint gates one stage: given (candidate, stage_fraction) it returns
# (ok, name) -- a health/eval check (success-rate >= baseline, no correctness
# regression, error-rate under ceiling, ...). Pure/injected.
Constraint = Callable[[str, float], "tuple[bool, str]"]
Rollback = Callable[[str], bool | None]


@dataclass(frozen=True)
class Stage:
    """One rollout stage: a named fraction of the fleet."""
    name: str
    fraction: float


@dataclass(frozen=True)
class DeploymentReceipt:
    """Backend attestation that the requested candidate revision is observed."""

    candidate: str
    fraction: float
    revision: str


# Canary first, then half, then everyone -- the classic safe ramp.
DEFAULT_STAGES: tuple[Stage, ...] = (
    Stage("canary", 0.1), Stage("half", 0.5), Stage("full", 1.0))


@dataclass
class StageResult:
    stage: str
    fraction: float
    passed: bool
    failing_constraint: str = ""


@dataclass
class RolloutResult:
    candidate: str
    stages: list[StageResult] = field(default_factory=list)
    rolled_back: bool = False
    completed: bool = False
    reason: str = ""

    @property
    def reached_fraction(self) -> float:
        """The largest fleet fraction that ran with all constraints green."""
        return max((s.fraction for s in self.stages if s.passed), default=0.0)


def _validated_plan(stages, constraints) -> tuple[list[Stage], list[Constraint], str]:
    """Normalize a rollout plan and return a fail-closed validation reason."""
    try:
        stage_values = list(stages)
        constraint_values = list(constraints)
    except TypeError:
        return [], [], "stages and constraints must be iterable"
    if not stage_values:
        return [], [], "at least one rollout stage is required"
    if not constraint_values:
        return [], [], "at least one health constraint is required"
    if not all(callable(constraint) for constraint in constraint_values):
        return [], [], "every health constraint must be callable"

    normalized: list[Stage] = []
    previous = 0.0
    names: set[str] = set()
    for index, raw in enumerate(stage_values):
        name = str(getattr(raw, "name", "") or "").strip()
        raw_fraction = getattr(raw, "fraction", None)
        if not name:
            return [], [], f"stage #{index + 1} has no name"
        if name in names:
            return [], [], f"duplicate rollout stage name {name!r}"
        if isinstance(raw_fraction, bool):
            return [], [], f"stage {name!r} has a non-numeric fraction"
        try:
            fraction = float(raw_fraction)
        except (TypeError, ValueError):
            return [], [], f"stage {name!r} has a non-numeric fraction"
        if not math.isfinite(fraction) or not 0.0 < fraction <= 1.0:
            return [], [], f"stage {name!r} fraction must be finite and in (0, 1]"
        if fraction <= previous:
            return [], [], "rollout stage fractions must be strictly increasing"
        normalized.append(Stage(name, fraction))
        names.add(name)
        previous = fraction
    if not math.isclose(previous, 1.0, rel_tol=0.0, abs_tol=1e-12):
        return [], [], "the final rollout stage must reach fraction 1.0"
    return normalized, constraint_values, ""


def _failure_label(value, constraint: Constraint, *, invalid: bool = False) -> str:
    fallback = getattr(constraint, "__name__", "constraint") or "constraint"
    try:
        label = " ".join(str(value or "").split())
    except Exception:
        label = ""
    label = (label or fallback)[:160]
    return f"{label}:invalid-result" if invalid else label


def _attempt_rollback(
    result: RolloutResult,
    candidate: str,
    rollback: Rollback,
    *,
    reason: str,
) -> RolloutResult:
    """Attempt rollback without manufacturing a success receipt on failure."""
    try:
        restored = rollback(candidate)
    except Exception as exc:
        result.reason = f"{reason}; rollback failed ({type(exc).__name__})"
        return result
    if restored is False:
        result.reason = f"{reason}; rollback reported failure"
        return result
    result.rolled_back = True
    result.reason = f"{reason}; rolled back"
    return result


def run_rollout(candidate: str, stages, constraints, *,
                deploy: Callable[[str, float], None],
                rollback: Rollback) -> RolloutResult:
    """Stage-by-stage: ``deploy(candidate, fraction)``, then check every
    constraint; on ANY failure, ``rollback(candidate)`` and stop. Reaching the
    final stage with all constraints green = ``completed``. Pure orchestration."""
    result = RolloutResult(candidate=candidate)
    stages, constraints, plan_error = _validated_plan(stages, constraints)
    if plan_error:
        result.reason = f"invalid rollout plan: {plan_error}"
        return result
    try:
        check_learning_halt("learning_rollout", "start")
    except Halted as exc:
        result.reason = str(exc)
        return result
    for st in stages:
        try:
            check_learning_halt("learning_rollout", "promotion")
        except Halted as exc:
            if result.stages:
                return _attempt_rollback(
                    result, candidate, rollback, reason=str(exc),
                )
            result.reason = str(exc)
            return result
        try:
            deploy(candidate, st.fraction)
        except Exception as exc:
            if isinstance(exc, Halted):
                failing = str(exc)
            else:
                failing = (
                    f"deploy failed at stage {st.name!r} "
                    f"({type(exc).__name__})"
                )
            result.stages.append(
                StageResult(st.name, st.fraction, False, failing),
            )
            return _attempt_rollback(
                result, candidate, rollback, reason=failing,
            )
        failing = ""
        try:
            check_learning_halt("learning_rollout", "evaluation")
        except Halted as exc:
            failing = str(exc)
        if not failing:
            for c in constraints:
                try:
                    check_learning_halt("learning_rollout", "evaluation")
                    outcome = c(candidate, st.fraction)
                    if not isinstance(outcome, tuple) or len(outcome) != 2:
                        ok, name = False, _failure_label(None, c, invalid=True)
                    else:
                        ok, raw_name = outcome
                        if ok is True:
                            name = _failure_label(raw_name, c)
                        elif ok is False:
                            name = _failure_label(raw_name, c)
                        else:
                            ok = False
                            name = _failure_label(raw_name, c, invalid=True)
                except Halted as exc:
                    ok, name = False, str(exc)
                except Exception as e:  # a constraint error is a failed constraint
                    ok, name = False, f"{getattr(c, '__name__', 'constraint')}:error:{e}"
                if ok is not True:
                    failing = name
                    break
        result.stages.append(StageResult(st.name, st.fraction, not failing, failing))
        if failing:
            return _attempt_rollback(
                result,
                candidate,
                rollback,
                reason=f"constraint {failing!r} failed at stage {st.name!r}",
            )
    result.completed = True
    result.reason = "all stages passed"
    return result


def threshold_constraint(name: str, metric: Callable[[str, float], float],
                         floor: float) -> Constraint:
    """A constraint that passes iff ``metric(candidate, fraction) >= floor`` --
    e.g. promoted-skill win-rate must stay at/above a baseline."""
    def _c(candidate: str, fraction: float) -> tuple[bool, str]:
        return (metric(candidate, fraction) >= floor), name
    _c.__name__ = name
    return _c


def promote_skill_live(
    candidate: str,
    constraints,
    *,
    stages=DEFAULT_STAGES,
    deploy_backend: Callable[[str, float], DeploymentReceipt] | None = None,
    rollback_backend: Rollback | None = None,
) -> RolloutResult:  # pragma: no cover -- touches learned state
    """Live promotion: snapshot the learned state first, run the staged rollout,
    and on a failed constraint restore the snapshot (whole-store rollback) and
    record a signed learning-audit row. Fail-safe: snapshot/audit errors degrade
    to a no-op, never a half-applied promotion."""
    from . import dreaming

    # An audit row is not deployment proof.  The historical implementation used
    # audit-only callbacks and could report a completed fleet rollout while no
    # serving member changed.  Production use now requires an explicit backend
    # that returns an observed candidate/revision receipt and a paired rollback.
    if deploy_backend is None or rollback_backend is None:
        return RolloutResult(
            candidate=candidate,
            reason="deployment and rollback backends are required",
        )

    # The snapshot is what makes the promotion reversible. If it RAISES (disk
    # full, permission), there is no reliable rollback -- so ABORT rather than
    # proceed into a promotion we can't cleanly undo (the docstring's "never a
    # half-applied promotion"). A None return is the legitimate empty-state case
    # (nothing yet to snapshot); rollback's remove-post-snapshot path still
    # reverts anything this rollout adds, so that case proceeds.
    try:
        check_learning_halt("learning_rollout", "snapshot")
        dreaming.snapshot_learning_state()
    except Halted as exc:
        return RolloutResult(candidate=candidate, completed=False, reason=str(exc))
    except Exception as e:
        log.warning("rollout: pre-promotion snapshot failed (%s); aborting -- "
                    "a promotion without a snapshot can't be rolled back", e)
        return RolloutResult(candidate=candidate, completed=False,
                             reason=f"aborted: pre-promotion snapshot failed ({e})")

    def deploy(cand: str, fraction: float) -> None:
        receipt = deploy_backend(cand, fraction)
        if (
            not isinstance(receipt, DeploymentReceipt)
            or receipt.candidate != cand
            or not receipt.revision.strip()
            or not math.isfinite(receipt.fraction)
            or not math.isclose(
                receipt.fraction, fraction, rel_tol=0.0, abs_tol=1e-12,
            )
        ):
            raise RuntimeError("deployment backend returned no matching observed receipt")
        try:
            from .audit import EventKind, record
            record(EventKind.LEARNING_UPDATE, agent="learning_rollout",
                   candidate=cand, stage_fraction=fraction, phase="deploy",
                   deployed_revision=receipt.revision)
        except Exception:
            pass

    def rollback(cand: str) -> bool:
        try:
            if rollback_backend(cand) is not True:
                return False
        except Exception as e:
            log.warning("rollout: deployment rollback failed (%s)", e)
            return False
        try:
            dreaming.rollback_learning_state("latest")
        except Exception as e:
            log.warning("rollout: rollback failed (%s)", e)
            return False
        return True

    return run_rollout(candidate, stages, constraints, deploy=deploy, rollback=rollback)


__all__ = [
    "Stage", "DeploymentReceipt", "StageResult", "RolloutResult", "Constraint",
    "DEFAULT_STAGES", "run_rollout", "threshold_constraint", "promote_skill_live",
]
