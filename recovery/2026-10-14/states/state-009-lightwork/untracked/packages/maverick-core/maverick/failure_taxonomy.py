"""Bounded, typed execution-stop taxonomy.

Historically the agent and flow runtimes communicated failures with display
strings (``"budget exceeded"``, ``"REFUSED"``, ...).  Those strings remain
useful to people, but are not a safe authority boundary: wording changes can
silently change retry, quarantine, API, audit, or metric behavior.

``FailureState`` is the authority.  ``FailureText`` is only its backwards-
compatible string presentation.  Adapters may translate a legacy protocol at
one ingress boundary, but downstream code must carry the resulting state and
must not re-classify its message.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

SCHEMA = "lightwork.execution-state.v1"
MAX_DETAIL = 500


class FailureKind(str, Enum):
    """The complete stable vocabulary exported to APIs/audit/telemetry."""

    PROVIDER_FAILURE = "provider_failure"
    POLICY_REFUSAL = "policy_refusal"
    BUDGET_EXHAUSTION = "budget_exhaustion"
    CANCELLATION = "cancellation"
    HUMAN_WAIT = "human_wait"
    VERIFIER_REJECTION = "verifier_rejection"
    DETERMINISTIC_TOOL_FAILURE = "deterministic_tool_failure"
    INDETERMINATE_EFFECT = "indeterminate_effect"


KINDS = tuple(kind.value for kind in FailureKind)

# Origins are deliberately fixed too.  They are useful diagnostics, but never
# an attacker-controlled metric label or a second, shadow classification.
ORIGINS = frozenset(
    {
        "agent",
        "approval",
        "budget",
        "checkpoint",
        "flow",
        "operator",
        "policy",
        "provider",
        "runtime",
        "step_limit",
        "tool",
        "verifier",
    }
)
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


class FailureStateError(ValueError):
    """A persisted or received execution state is not in the fixed contract."""


def _safe_detail(value: object) -> str:
    """Secret-scrub and bound human-facing detail; fail closed on redaction."""
    try:
        from .secrets import scrub

        text = scrub(str(value or ""))
    except Exception:  # pragma: no cover - disclosure prevention fails closed
        text = "execution state detail unavailable"
    # JSON permits controls that are hostile in logs, headers, and terminals.
    text = " ".join(text.replace("\x7f", " ").splitlines())
    text = "".join(char if char >= " " else " " for char in text)
    return text[:MAX_DETAIL]


def _origin(value: object) -> str:
    origin = str(value or "runtime")
    return origin if origin in ORIGINS else "runtime"


@dataclass(frozen=True)
class FailureState:
    """One authoritative stop state with a safe display detail.

    Cancellation and human wait are intentional non-failure control states;
    indeterminate is a quarantine state.  Callers should use ``category`` or
    the explicit kind rather than assuming every instance is a failure.
    """

    kind: FailureKind
    detail: str = ""
    origin: str = "runtime"

    def __post_init__(self) -> None:
        try:
            kind = self.kind if isinstance(self.kind, FailureKind) else FailureKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise FailureStateError("execution state kind is invalid") from exc
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "detail", _safe_detail(self.detail))
        object.__setattr__(self, "origin", _origin(self.origin))

    @property
    def category(self) -> str:
        if self.kind is FailureKind.HUMAN_WAIT:
            return "wait"
        if self.kind is FailureKind.CANCELLATION:
            return "cancelled"
        if self.kind is FailureKind.INDETERMINATE_EFFECT:
            return "indeterminate"
        return "failure"

    @property
    def terminal(self) -> bool:
        return self.kind is not FailureKind.HUMAN_WAIT

    @property
    def is_failure(self) -> bool:
        return self.category == "failure"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "kind": self.kind.value,
            "category": self.category,
            "terminal": self.terminal,
            "origin": self.origin,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FailureState:
        if not isinstance(value, Mapping):
            raise FailureStateError("execution state must be an object")
        schema = value.get("schema", SCHEMA)
        if schema != SCHEMA:
            raise FailureStateError("execution state schema is unsupported")
        try:
            state = cls(
                kind=FailureKind(value.get("kind")),
                detail=str(value.get("detail") or ""),
                origin=str(value.get("origin") or "runtime"),
            )
        except (TypeError, ValueError) as exc:
            raise FailureStateError("execution state kind is invalid") from exc
        # Derived fields are accepted for wire readability but may never
        # override kind authority.
        if "category" in value and value["category"] != state.category:
            raise FailureStateError("execution state category contradicts kind")
        if "terminal" in value and value["terminal"] is not state.terminal:
            raise FailureStateError("execution state terminal flag contradicts kind")
        return state


class FailureText(str):
    """A string-compatible display carrying an authoritative typed state."""

    failure_state: FailureState

    def __new__(cls, display: object, state: FailureState):
        normalized = state if isinstance(state, FailureState) else FailureState.from_dict(state)
        obj = super().__new__(cls, _safe_detail(display))
        obj.failure_state = normalized
        return obj


class FailureSignal(RuntimeError):
    """Typed exception for boundaries that cannot return ``FailureText``."""

    def __init__(self, state: FailureState):
        self.failure_state = state
        super().__init__(state.detail or state.kind.value)


def failure_state(
    kind: FailureKind,
    detail: object = "",
    *,
    origin: str = "runtime",
) -> FailureState:
    return FailureState(kind=kind, detail=str(detail or ""), origin=origin)


def failure_text(
    kind: FailureKind,
    detail: object = "",
    *,
    origin: str = "runtime",
    prefix: str = "",
) -> FailureText:
    state = failure_state(kind, detail, origin=origin)
    return FailureText(f"{prefix}{state.detail}", state)


def state_from_value(value: object) -> FailureState | None:
    """Read attached type metadata only; never inspect display text."""
    if isinstance(value, FailureState):
        return value
    candidate = getattr(value, "failure_state", None)
    if isinstance(candidate, FailureState):
        return candidate
    if isinstance(candidate, Mapping):
        return FailureState.from_dict(candidate)
    candidate = getattr(value, "failure", None)
    if isinstance(candidate, FailureState):
        return candidate
    if isinstance(candidate, Mapping):
        return FailureState.from_dict(candidate)
    return None


def state_from_exception(
    exc: BaseException,
    *,
    default: FailureKind = FailureKind.DETERMINISTIC_TOOL_FAILURE,
    origin: str = "runtime",
) -> FailureState:
    """Classify by explicit type identity, never exception names/messages."""
    attached = state_from_value(exc)
    if attached is not None:
        return attached
    from .budget import BudgetExceeded
    from .killswitch import Halted

    if isinstance(exc, BudgetExceeded):
        kind, origin = FailureKind.BUDGET_EXHAUSTION, "budget"
    elif isinstance(exc, (Halted, asyncio.CancelledError)):
        kind, origin = FailureKind.CANCELLATION, "operator"
    else:
        # Import lazily to keep this foundational module side-effect-light.
        policy_types: tuple[type[BaseException], ...] = ()
        try:
            from .audit import AuditRefused
            from .secret_provider import SecretProviderError

            policy_types = (AuditRefused, SecretProviderError)
        except ImportError:  # pragma: no cover - minimal installations
            pass
        if isinstance(exc, PermissionError) or (
            policy_types and isinstance(exc, policy_types)
        ):
            kind, origin = FailureKind.POLICY_REFUSAL, "policy"
        elif isinstance(exc, (ConnectionError, TimeoutError)):
            kind, origin = FailureKind.PROVIDER_FAILURE, "provider"
        else:
            kind = default
    return failure_state(kind, exc, origin=origin)


def _safe_identifier(value: object) -> str:
    text = str(value or "")
    return text if _SAFE_IDENTIFIER.fullmatch(text) else ""


def record_state_audit(
    state: FailureState,
    *,
    required: bool = False,
    agent: str = "system",
    goal_id: int | None = None,
    run_id: str = "",
    flow_id: str = "",
) -> bool:
    """Write bounded state evidence through ordinary or required audit."""
    from .audit import EventKind, audit_event, required_audit_event

    writer = required_audit_event if required else audit_event
    return bool(
        writer(
            EventKind.EXECUTION_STATE,
            agent=_safe_identifier(agent) or "system",
            goal_id=goal_id,
            state_schema=SCHEMA,
            state_kind=state.kind.value,
            state_category=state.category,
            terminal=state.terminal,
            origin=state.origin,
            detail=state.detail,
            run_id=_safe_identifier(run_id),
            flow_id=_safe_identifier(flow_id),
        )
    )


def record_state_telemetry(
    state: FailureState,
    *,
    goal_id: int | None = None,
) -> bool:
    """Emit only enum-derived, fixed-cardinality labels."""
    recorded = False
    try:
        from . import failure_telemetry

        recorded = bool(failure_telemetry.record_failure(state, goal_id=goal_id))
    except Exception:  # pragma: no cover - telemetry never breaks execution
        pass
    try:
        from .observability import record_metric

        record_metric(
            "execution_states",
            labels={"kind": state.kind.value, "category": state.category},
        )
    except Exception:  # pragma: no cover - telemetry never breaks execution
        pass
    return recorded


def persist_goal_state(world: object, goal_id: int, state: FailureState) -> None:
    """Persist a typed goal state and emit its bounded audit/telemetry evidence."""
    content = json.dumps(
        state.to_dict(),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    # Goal events are the existing durable, API-streamed runtime channel. The
    # JSON body is strict typed state, not a convention hidden in prose.
    world.append_event(int(goal_id), "system", "execution_state", content)
    try:
        record_state_audit(state, goal_id=int(goal_id), agent="system")
    except Exception:  # pragma: no cover - the observed goal state remains durable
        pass
    record_state_telemetry(state, goal_id=int(goal_id))


def load_goal_state(world: object, goal_id: int) -> FailureState | None:
    """Load the newest typed goal event without examining ordinary messages."""
    events = world.recent_goal_events(int(goal_id), limit=50)
    for event in reversed(events):
        if getattr(event, "kind", None) != "execution_state":
            continue
        try:
            payload = json.loads(str(getattr(event, "content", "")))
        except (TypeError, ValueError) as exc:
            raise FailureStateError("persisted execution state is invalid JSON") from exc
        return FailureState.from_dict(payload)
    return None


__all__ = [
    "FailureKind",
    "FailureSignal",
    "FailureState",
    "FailureStateError",
    "FailureText",
    "KINDS",
    "MAX_DETAIL",
    "SCHEMA",
    "failure_state",
    "failure_text",
    "load_goal_state",
    "persist_goal_state",
    "record_state_audit",
    "record_state_telemetry",
    "state_from_exception",
    "state_from_value",
]
