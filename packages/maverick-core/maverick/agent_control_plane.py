"""Agent Control Plane: one governed roster and one set of operator verbs over
every agent this deployment runs or admits.

Lightwork already has the primitives an operator needs to control an agent:
the capability revocation registry (``revocation.py``, consulted on every tool
call), the external-agent trust registry (``agent_trust.py``), the security
plane's containment (``agent_edr.py``), the bring-your-own-agent sidecar
(``external_agents.py``) and the fleet killswitch (``killswitch.py``). What it
did not have is a single place that lists every principal those primitives
govern and applies the same verb to any of them. This module is that place.

It adds NO new enforcement. Every verb lands in a store the runtime already
consults on its hot path, and reports what it *confirmed* landed rather than
what it asked for (a suspend whose revocation did not persist is a failure,
not a success with a caveat).

Principals:

- a native run is governed by the capability principal its goal runs under --
  ``user:<id>`` (``agent.py``'s root grant; ``user:local`` when no user is
  bound). Revoking it denies the next tool call of every agent in that
  subtree, because a child grant carries its ancestors and
  ``revocation.revoked_principal`` walks them;
- an external agent is ``agent:<id>``, the trust registry's principal. A
  suspend flips the trust entry's ``revoked`` flag as well, so the gateway
  and the A2A/gRPC/MCP surfaces refuse it too, not only the capability path.

Config: ``[control_plane] enable`` (or ``MAVERICK_CONTROL_PLANE=1``). Reads
(roster, status, ledger) work either way; the mutating verbs refuse while the
plane is off, so a stray click on a deployment that never opted in cannot
revoke anything.
"""
from __future__ import annotations

import dataclasses
import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

KIND_NATIVE = "native"
KIND_EXTERNAL = "external"
EXTERNAL_PREFIX = "agent:"

# Same shape the dashboard route accepts; ``/`` is excluded so a principal can
# ride in a URL path segment.
_PRINCIPAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
_RISK_LEVELS = ("low", "medium", "high")
_MAX_REASON = 500


class ControlPlaneError(RuntimeError):
    """A verb could not be applied (the deployment state is unchanged)."""


class ControlPlaneDisabled(ControlPlaneError):
    """The plane is off: ``[control_plane] enable`` is false."""


# ---- model ------------------------------------------------------------------

@dataclass(frozen=True)
class AgentPrincipal:
    """One row of the roster: a principal and what the runtime knows of it."""

    principal: str
    kind: str
    label: str
    state: str
    running_goals: int = 0
    revoked: bool = False
    revoked_reason: str = ""
    contained: bool = False
    max_risk: str | None = None
    max_dollars: float | None = None
    max_wall_seconds: float | None = None
    allow_tools: tuple[str, ...] = ()
    deny_tools: tuple[str, ...] = ()
    platform: str = ""

    def to_dict(self) -> dict:
        out = dataclasses.asdict(self)
        out["allow_tools"] = list(self.allow_tools)
        out["deny_tools"] = list(self.deny_tools)
        return out


@dataclass
class ControlResult:
    """What a verb confirmed. ``ok`` is False when any part did not land."""

    verb: str
    principal: str
    ok: bool
    detail: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"verb": self.verb, "principal": self.principal, "ok": self.ok,
                "detail": self.detail, "notes": list(self.notes)}


# ---- gate -------------------------------------------------------------------

def enabled() -> bool:
    """``[control_plane] enable`` or ``MAVERICK_CONTROL_PLANE=1``."""
    try:
        from .config import get_control_plane
        return bool(get_control_plane().get("enable"))
    except Exception:
        log.warning("control_plane: config unreadable; plane is off", exc_info=True)
        return False


def _require_enabled(verb: str) -> None:
    if not enabled():
        raise ControlPlaneDisabled(
            f"control plane is off: `{verb}` refused (set [control_plane] enable "
            "= true or MAVERICK_CONTROL_PLANE=1)")


def valid_principal(value: object) -> bool:
    return isinstance(value, str) and bool(_PRINCIPAL_RE.fullmatch(value))


def _check_principal(principal: object) -> str:
    if not valid_principal(principal):
        raise ValueError("invalid principal (letters, digits, `_.:@-`, max 128)")
    return str(principal)


def _check_reason(reason: object) -> str:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("a reason is required")
    if len(reason) > _MAX_REASON:
        raise ValueError(f"reason exceeds {_MAX_REASON} characters")
    return reason.strip()


def _check_actor(actor: object) -> str:
    return actor.strip() if isinstance(actor, str) and actor.strip() else "operator"


def is_external(principal: str) -> bool:
    return principal.startswith(EXTERNAL_PREFIX) and len(principal) > len(EXTERNAL_PREFIX)


def _external_id(principal: str) -> str:
    return principal[len(EXTERNAL_PREFIX):]


# ---- audit ------------------------------------------------------------------

def _audit(verb: str, principal: str, actor: str, ok: bool, detail: str, **extra) -> None:
    """One signed row per verb. Fail-soft on ordinary outages; a configured
    audit guarantee that refuses the row propagates (the change already landed
    and the caller must not mistake it for a recorded one)."""
    from .audit import EventKind, audit_event
    audit_event(
        EventKind.CONTROL_PLANE_ACTION, agent="control_plane",
        verb=verb, principal=principal, actor=actor, ok=bool(ok),
        detail=detail[:_MAX_REASON], **extra,
    )


# ---- roster -----------------------------------------------------------------

def _revocations() -> dict[str, Any]:
    try:
        from .revocation import shared
        return shared().revoked()
    except Exception:
        log.warning("control_plane: revocation registry unreadable", exc_info=True)
        return {}


def _native_rows(revoked: dict[str, Any]) -> list[AgentPrincipal]:
    """Running goals grouped by the capability principal they run under, plus
    every revoked native principal (a suspended run must stay listed so it can
    be resumed after its goals have drained)."""
    counts: dict[str, int] = {}
    try:
        from .world_model import open_world
        for goal in open_world().list_goals(status="running"):
            owner = getattr(goal, "owner", "") or "user:local"
            counts[owner] = counts.get(owner, 0) + 1
    except Exception:
        log.warning("control_plane: world model unavailable; native runs unknown",
                    exc_info=True)
    principals = set(counts) | {p for p in revoked if not is_external(p)}
    rows = []
    for principal in sorted(principals):
        rev = revoked.get(principal)
        running = counts.get(principal, 0)
        state = "suspended" if rev is not None else ("running" if running else "idle")
        rows.append(AgentPrincipal(
            principal=principal, kind=KIND_NATIVE, label=principal, state=state,
            running_goals=running, revoked=rev is not None,
            revoked_reason=getattr(rev, "reason", "") if rev is not None else "",
        ))
    return rows


def _external_rows(revoked: dict[str, Any]) -> list[AgentPrincipal]:
    from .agent_trust import load_registry
    contained: dict[str, bool] = {}
    platforms: dict[str, str] = {}
    try:
        from .external_agents import roster as xa_roster
        for row in xa_roster():
            contained[row["id"]] = bool(row.get("contained"))
            platforms[row["id"]] = str(row.get("platform_label") or "")
    except Exception:
        log.debug("control_plane: external-agent sidecar unavailable", exc_info=True)
    rows = []
    for agent in load_registry().values():
        principal = EXTERNAL_PREFIX + agent.id
        rev = revoked.get(principal)
        active, rule = agent.is_active()
        if rev is not None or agent.revoked:
            state = "suspended"
        elif contained.get(agent.id):
            state = "contained"
        else:
            state = "active" if active else rule
        rows.append(AgentPrincipal(
            principal=principal, kind=KIND_EXTERNAL, label=agent.id, state=state,
            revoked=rev is not None or agent.revoked,
            revoked_reason=getattr(rev, "reason", "") if rev is not None else "",
            contained=bool(contained.get(agent.id)),
            max_risk=agent.max_risk, max_dollars=agent.max_dollars,
            max_wall_seconds=agent.max_wall_seconds,
            allow_tools=tuple(sorted(agent.allow_tools)),
            deny_tools=tuple(sorted(agent.deny_tools)),
            platform=platforms.get(agent.id, ""),
        ))
    # A revoked external principal that has no (or no longer has a) trust entry
    # must stay visible: it is still denied on every tool call, and the only
    # way to lift that is from here. Dropping it would strand the revocation.
    listed = {row.principal for row in rows}
    for principal, rev in sorted(revoked.items()):
        if is_external(principal) and principal not in listed:
            rows.append(AgentPrincipal(
                principal=principal, kind=KIND_EXTERNAL, label=_external_id(principal),
                state="suspended", revoked=True,
                revoked_reason=getattr(rev, "reason", ""),
                platform="not in trust registry",
            ))
    return rows


def roster() -> list[AgentPrincipal]:
    """Every principal the plane can act on: native runs then external agents."""
    revoked = _revocations()
    return _native_rows(revoked) + _external_rows(revoked)


def lookup(principal: str) -> AgentPrincipal | None:
    principal = _check_principal(principal)
    for row in roster():
        if row.principal == principal:
            return row
    return None


# ---- trust-entry helpers (external agents) -----------------------------------

def _trust_entry(agent_id: str):
    from .agent_trust import load_registry
    return load_registry().get(agent_id)


def _managed_entry(agent_id: str) -> dict | None:
    from .agent_trust import _load_managed
    for entry in _load_managed():
        if str(entry.get("id") or "") == agent_id:
            return dict(entry)
    return None


def _overlay_from(agent) -> dict:
    """A managed-registry entry equivalent to a parsed ``TrustedAgent`` (used
    when the entry lives only in TOML: the overlay overrides it by id)."""
    entry: dict[str, Any] = {}
    for f in dataclasses.fields(agent):
        value = getattr(agent, f.name)
        entry[f.name] = sorted(value) if isinstance(value, frozenset) else value
    return entry


def _set_trust_revoked(agent_id: str, revoked: bool, notes: list[str]) -> bool:
    """Flip the trust entry's ``revoked`` flag; True when it is confirmed."""
    from .agent_trust import put_agent, set_revoked
    agent = _trust_entry(agent_id)
    if agent is None:
        notes.append("no trust-registry entry: the capability revocation alone applies")
        return True
    if not set_revoked(agent_id, revoked):
        entry = _overlay_from(agent)
        entry["revoked"] = revoked
        put_agent(entry)
        notes.append("trust entry is config-defined; wrote a managed overlay")
    after = _trust_entry(agent_id)
    return after is not None and after.revoked is revoked


# ---- verbs ------------------------------------------------------------------

def suspend(principal: str, *, reason: str, actor: str = "") -> ControlResult:
    """Revoke ``principal`` (reversible with :func:`resume`).

    Native: the next tool call under that principal is denied. External: the
    trust entry is marked revoked as well, so every surface refuses it.
    """
    _require_enabled("suspend")
    principal = _check_principal(principal)
    reason = _check_reason(reason)
    actor = _check_actor(actor)
    from .revocation import shared
    result = ControlResult("suspend", principal, ok=False)
    try:
        registry = shared()
        registry.revoke(principal, reason=f"{actor}: {reason}")
        landed = registry.is_revoked(principal)
        if not landed:
            result.detail = "revocation did not persist"
        elif is_external(principal) and not _set_trust_revoked(
                _external_id(principal), True, result.notes):
            result.detail = "capability revoked but the trust entry did not flip"
        else:
            result.ok = True
            result.detail = "revoked; the next tool call is denied"
    except Exception as e:
        result.detail = f"suspend failed: {e}"
    _audit("suspend", principal, actor, result.ok, reason)
    return result


def resume(principal: str, *, actor: str = "") -> ControlResult:
    """Lift a :func:`suspend` (and a :func:`contain`'s revocation)."""
    _require_enabled("resume")
    principal = _check_principal(principal)
    actor = _check_actor(actor)
    from .revocation import shared
    result = ControlResult("resume", principal, ok=False)
    try:
        registry = shared()
        if not registry.unrevoke(principal):
            result.notes.append("principal was not revoked")
        if registry.is_revoked(principal):
            result.detail = "revocation is still in place"
        elif is_external(principal) and not _set_trust_revoked(
                _external_id(principal), False, result.notes):
            result.detail = "trust entry is still revoked"
        else:
            result.ok = True
            result.detail = "principal may act again"
    except Exception as e:
        result.detail = f"resume failed: {e}"
    _audit("resume", principal, actor, result.ok, result.detail)
    return result


def contain(principal: str, *, reason: str, actor: str = "",
            registry: Any | None = None, edges: dict[str, Any] | None = None,
            ) -> ControlResult:
    """Incident response: the security plane's containment, from the console.

    Revokes the principal's whole capability subtree (``edges``) and seals it
    in the live swarm when a quarantine ``registry`` is supplied; an
    out-of-band call has none, and the result says so rather than implying a
    running agent stopped. External agents are also revoked in the trust
    registry. Undo with :func:`release`.
    """
    _require_enabled("contain")
    principal = _check_principal(principal)
    reason = _check_reason(reason)
    actor = _check_actor(actor)
    from .agent_edr import contain as edr_contain
    result = ControlResult("contain", principal, ok=False)
    try:
        outcome = edr_contain(principal, reason=f"{actor}: {reason}",
                              registry=registry, edges=edges)
        result.notes.extend(outcome.notes)
        result.notes.extend(f"failed: {f}" for f in outcome.failed)
        result.ok = outcome.contained
        if result.ok and is_external(principal) and not _set_trust_revoked(
                _external_id(principal), True, result.notes):
            result.ok = False
            result.detail = "capability contained but the trust entry did not flip"
        elif result.ok:
            result.detail = f"contained; revoked {len(outcome.revoked)} principal(s)"
        else:
            result.detail = "containment incomplete"
    except Exception as e:
        result.detail = f"contain failed: {e}"
    _audit("contain", principal, actor, result.ok, reason)
    return result


def release(principal: str, *, actor: str = "", registry: Any | None = None,
            ) -> ControlResult:
    """Undo a :func:`contain`: unrevoke, unseal, and (external) lift the
    gateway's containment and the trust revocation."""
    _require_enabled("release")
    principal = _check_principal(principal)
    actor = _check_actor(actor)
    from .agent_edr import release as edr_release
    result = ControlResult("release", principal, ok=False)
    try:
        outcome = edr_release(principal, registry=registry)
        result.notes.extend(outcome.notes)
        result.notes.extend(f"failed: {f}" for f in outcome.failed)
        result.ok = outcome.contained  # "no failures" in Containment terms
        if is_external(principal):
            agent_id = _external_id(principal)
            if not _set_trust_revoked(agent_id, False, result.notes):
                result.ok = False
                result.notes.append("trust entry is still revoked")
            try:
                from .external_agents import release as xa_release
                if not xa_release(agent_id, released_by=actor):
                    result.notes.append("agent is not enrolled in the gateway")
            except Exception as e:
                result.notes.append(f"gateway release unavailable: {e}")
        result.detail = "released" if result.ok else "release incomplete"
    except Exception as e:
        result.detail = f"release failed: {e}"
    _audit("release", principal, actor, result.ok, result.detail)
    return result


def _rank(risk: str | None) -> int:
    return _RISK_LEVELS.index(risk) if risk in _RISK_LEVELS else len(_RISK_LEVELS)


def _tighter_cap(current: float | None, requested: float | None, label: str,
                 ) -> float | None:
    if requested is None:
        return current
    value = float(requested)
    if not value > 0:
        raise ValueError(f"{label} must be positive")
    if current is not None and value > current:
        raise ValueError(f"{label} {value} would widen the current ceiling {current}")
    return value


def attenuate(principal: str, *, deny_tools: Any = (), max_risk: str | None = None,
              max_dollars: float | None = None, max_wall_seconds: float | None = None,
              actor: str = "") -> ControlResult:
    """Narrow an EXTERNAL agent's ceiling in the trust registry. Never widens:
    a request that would raise a risk cap, lift a budget, or drop a denied tool
    is refused whole. Native runs have no persistent per-principal ceiling
    (their grant is minted from ``[capabilities]`` at goal start), so they are
    refused here rather than silently ignored."""
    _require_enabled("attenuate")
    principal = _check_principal(principal)
    actor = _check_actor(actor)
    if not is_external(principal):
        raise ValueError("attenuate applies to external agents (`agent:<id>`) only")
    agent_id = _external_id(principal)
    agent = _trust_entry(agent_id)
    if agent is None:
        raise LookupError(f"{principal} is not in the trust registry")
    extra_deny = {str(t).strip() for t in (deny_tools or ()) if str(t).strip()}
    new_deny = sorted(set(agent.deny_tools) | extra_deny)
    new_risk = agent.max_risk
    if max_risk is not None:
        if max_risk not in _RISK_LEVELS:
            raise ValueError("max_risk must be one of low, medium, high")
        if _rank(max_risk) > _rank(agent.max_risk):
            raise ValueError(f"max_risk {max_risk} would widen {agent.max_risk}")
        new_risk = max_risk
    new_dollars = _tighter_cap(agent.max_dollars, max_dollars, "max_dollars")
    new_wall = _tighter_cap(agent.max_wall_seconds, max_wall_seconds, "max_wall_seconds")
    changed = {"deny_tools": new_deny, "max_risk": new_risk,
               "max_dollars": new_dollars, "max_wall_seconds": new_wall}
    from .agent_trust import put_agent
    result = ControlResult("attenuate", principal, ok=False)
    try:
        entry = _managed_entry(agent_id)
        if entry is None:
            entry = _overlay_from(agent)
            result.notes.append("trust entry is config-defined; wrote a managed overlay")
        entry.update(changed)
        put_agent(entry)
        after = _trust_entry(agent_id)
        landed = (after is not None
                  and sorted(after.deny_tools) == new_deny
                  and after.max_risk == new_risk
                  and after.max_dollars == new_dollars
                  and after.max_wall_seconds == new_wall)
        result.ok = landed
        result.detail = ("ceiling narrowed" if landed
                         else "the narrowed ceiling did not persist")
    except Exception as e:
        result.detail = f"attenuate failed: {e}"
    _audit("attenuate", principal, actor, result.ok, result.detail, ceiling=changed)
    return result


def halt_all(reason: str, *, actor: str = "") -> ControlResult:
    """Fleet stop: arm the killswitch every kernel checks at its tool boundary."""
    _require_enabled("halt")
    reason = _check_reason(reason)
    actor = _check_actor(actor)
    from . import killswitch
    result = ControlResult("halt", "*", ok=False)
    try:
        killswitch.halt(reason, source=f"control_plane:{actor}")
        result.ok = killswitch.is_active()
        result.detail = "fleet halted" if result.ok else "halt did not take effect"
    except Exception as e:
        result.detail = f"halt failed: {e}"
    _audit("halt", "*", actor, result.ok, reason)
    return result


def clear_halt(*, actor: str = "") -> ControlResult:
    """Disarm the in-process killswitch (a HALT file is the operator's to remove)."""
    _require_enabled("clear-halt")
    actor = _check_actor(actor)
    from . import killswitch
    result = ControlResult("clear-halt", "*", ok=False)
    try:
        killswitch.clear()
        result.ok = not killswitch.is_active()
        result.detail = ("fleet released" if result.ok
                         else "a halt is still active (HALT file or cluster stop)")
    except Exception as e:
        result.detail = f"clear failed: {e}"
    _audit("clear-halt", "*", actor, result.ok, result.detail)
    return result


# ---- read-side ----------------------------------------------------------------

def ledger(principal: str, *, since: float | None = None, limit: int = 50) -> list[dict]:
    """The principal's forensic timeline off the signed chain, newest last."""
    principal = _check_principal(principal)
    from .agent_edr import timeline
    rows = timeline(principal, since=since)
    return [d.to_dict() for d in rows[-max(1, int(limit)):]]


def status() -> dict:
    """The console header: is the plane on, is the fleet halted, who is governed."""
    from . import killswitch
    rows = roster()
    native = [r for r in rows if r.kind == KIND_NATIVE]
    external = [r for r in rows if r.kind == KIND_EXTERNAL]
    out: dict[str, Any] = {
        "schema": "lightwork.agent-control-plane-status.v1",
        "enabled": enabled(),
        "halted": killswitch.is_active(),
        "native": {"principals": len(native),
                   "running_goals": sum(r.running_goals for r in native),
                   "suspended": sum(1 for r in native if r.revoked)},
        "external": {"agents": len(external),
                     "active": sum(1 for r in external if r.state == "active"),
                     "suspended": sum(1 for r in external if r.revoked),
                     "contained": sum(1 for r in external if r.contained)},
    }
    try:
        from .agent_trust import agent_trust_enforced
        out["trust_enforced"] = bool(agent_trust_enforced())
    except Exception:
        out["trust_enforced"] = False
    try:
        from .external_agents import enabled as xa_enabled
        out["external_agents_enabled"] = bool(xa_enabled())
    except Exception:
        out["external_agents_enabled"] = False
    try:
        from .agent_edr import posture
        out["posture"] = posture().to_dict()
    except Exception:
        out["posture"] = {}
    return out


__all__ = [
    "AgentPrincipal",
    "ControlPlaneDisabled",
    "ControlPlaneError",
    "ControlResult",
    "KIND_EXTERNAL",
    "KIND_NATIVE",
    "attenuate",
    "clear_halt",
    "contain",
    "enabled",
    "halt_all",
    "is_external",
    "ledger",
    "lookup",
    "release",
    "resume",
    "roster",
    "status",
    "suspend",
    "valid_principal",
]
