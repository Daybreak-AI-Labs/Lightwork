"""Bring-your-own-agent: enroll agents built on OTHER platforms into the
governed workforce.

An Agentforce, Bedrock, OpenAI, LangChain, or home-grown agent runs on its own
runtime — Lightwork cannot orchestrate it, but it CAN govern and account for
it. This module is the missing spine between the two registries that already
exist (the Agent Trust Plane for identity/ceilings, the fleet-memory roster
for learning) and the Operating Record:

* ``enroll``       — one call registers the agent in BOTH registries, stamps
                     platform provenance (which vendor runtime it lives on,
                     who owns it, which department it serves), and audits who
                     enrolled it.
* ``mint_token``   — issues the per-surface bearer credential the agent will
                     actually authenticate with (today that is a hand-edited
                     TOML field). The token value is returned exactly once.
* ``record_run``   — ingests a completed external run as a first-class
                     Operating Record row: a goal owned by ``agent:<id>``
                     with a step trail, an outcome, and a costed episode — so
                     Overview / Spend / Workforce / Savings and the audit
                     binder see foreign agents exactly like native ones.
* ``screen``       — the governance seam: the external agent asks BEFORE
                     acting. Trust admission (registry, direction, tool and
                     risk ceilings, revocation), cumulative budget, Shield
                     input scanning, and the ``[actions]`` approval floor all
                     apply; high-risk actions park a real approval row the
                     human decides in the dashboard queue.

The external boundary is fail-closed throughout: an unenrolled agent is
denied, a Shield scanner error DENIES (never waves through), a malformed
sidecar is treated as empty-but-alarmed, and over-budget agents are refused
further actions rather than silently accumulating spend.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any

log = logging.getLogger("maverick.external_agents")

#: Platform semantics for enrollment — where the agent actually runs. This is
#: provenance (it shows on the roster, in audit rows, and as the fleet-memory
#: vendor), not a behavior switch: every platform gets the same governance.
PLATFORMS: dict[str, str] = {
    "agentforce": "Salesforce Agentforce",
    "bedrock": "AWS Bedrock Agents",
    "copilot": "Microsoft Copilot Studio",
    "openai": "OpenAI Agents",
    "langchain": "LangChain / LangGraph",
    "custom": "Custom / other runtime",
}

#: Credential surfaces an external agent can hold a bearer for. ``rest`` is
#: the external-agents HTTP API itself; the rest are the existing per-surface
#: tokens on the trust entry (distinct so a leak on one surface cannot
#: authenticate another).
TOKEN_SURFACES = ("rest", "a2a", "grpc", "mcp")

_MAX_TEXT = 4000
_MAX_STEPS = 50
_MAX_AGENTS = 500


class ExternalAgentsError(Exception):
    """Operator-facing failure (bad input, unknown agent, plane disabled)."""


def entitled() -> bool:
    """Bring-your-own-agent governance is a paid (Gold) capability.

    Fail-open like fleet governance: it only bites when a deployment has
    turned license enforcement on and the license lacks the feature. Roster
    reads are never gated — only the governed traffic (enroll / ingest /
    screen)."""
    try:
        from .entitlements import require
        return require("external_agents")
    except Exception:  # pragma: no cover - entitlements missing => don't block
        return True


def enabled() -> bool:
    """``[external_agents] enable`` or ``MAVERICK_EXTERNAL_AGENTS=1``."""
    import os
    if str(os.environ.get("MAVERICK_EXTERNAL_AGENTS", "")).strip() == "1":
        return True
    try:
        from .config import load_config
        return bool((load_config() or {}).get(
            "external_agents", {}).get("enable", False))
    except Exception:
        return False


# -- sidecar registry (platform/ownership metadata + reported-spend meter) ----

def registry_path():
    """Enrollment metadata sidecar (``external_agents.json``), tenant-scoped.

    Identity and ceilings live in the Agent Trust Plane; this file carries what
    the trust entry deliberately does not: platform provenance, ownership, and
    the cumulative reported-spend meter the budget cutoff reads."""
    from .paths import data_dir
    return data_dir("external_agents.json")


def _load_sidecar() -> dict[str, dict]:
    path = registry_path()
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("sidecar root must be an object")
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except (OSError, UnicodeError, ValueError) as e:
        # Metadata loss must not widen trust (identity still gates), but it
        # must not pass silently either -- the spend meters live here.
        log.warning("external_agents: sidecar unreadable (%s); treating as "
                    "empty", e)
        return {}


def _save_sidecar(entries: dict[str, dict]) -> None:
    from .file_lock import atomic_write_text, ensure_private_directory
    path = registry_path()
    ensure_private_directory(path.parent)
    atomic_write_text(path, json.dumps(entries, indent=2, sort_keys=True),
                      mode=0o600)


def _sidecar_locked():
    from .file_lock import cross_process_lock
    return cross_process_lock(registry_path())


# -- text hygiene at the boundary ---------------------------------------------

def _clean_text(text: Any, *, shield: Any | None = None) -> str | None:
    """Redact secrets, refuse injection payloads. ``None`` = reject.

    Same posture as the fleet-memory inbox: external text is untrusted; a
    tripwire hit rejects the field rather than laundering it into the record.
    A Shield instance, when provided, gets the final word — and a scanner
    ERROR rejects (fail-closed: this is an external boundary)."""
    safe = str(text or "")[:_MAX_TEXT]
    if not safe:
        return ""
    try:
        from .safety.secret_detector import redact
        safe = redact(safe)[0]
    except Exception:
        return None
    try:
        from .memory_guard import injection_markers
        if injection_markers(safe):
            return None
    except Exception:
        return None
    if shield is not None:
        try:
            verdict = shield.scan_input(safe)
            if getattr(verdict, "blocked", False):
                return None
        except Exception:
            return None
    return safe


# -- enrollment ---------------------------------------------------------------

def enroll(
    agent_id: str,
    platform: str,
    *,
    description: str = "",
    owner: str = "",
    department: str = "",
    allow_tools: list[str] | None = None,
    deny_tools: list[str] | None = None,
    max_risk: str | None = None,
    max_dollars: float | None = None,
    max_wall_seconds: float | None = None,
    data_scopes: list[str] | None = None,
    expires_days: float | None = None,
    enrolled_by: str = "",
) -> dict:
    """Enroll one external agent across every plane in a single call.

    Creates/replaces the managed Agent Trust entry (direction ``inbound`` —
    the agent calls us; we never dial an enrolled foreign runtime), registers
    the agent on the fleet-memory roster when that plane is enabled, and
    stamps the enrollment metadata sidecar. Returns a summary; raises
    :class:`ExternalAgentsError` on bad input."""
    from .agent_trust import AgentTrustError, put_agent
    platform = str(platform or "").strip().lower()
    if platform not in PLATFORMS:
        raise ExternalAgentsError(
            f"unknown platform {platform!r}; expected one of "
            f"{', '.join(sorted(PLATFORMS))}")
    sidecar = _load_sidecar()
    if agent_id not in sidecar and len(sidecar) >= _MAX_AGENTS:
        raise ExternalAgentsError("enrollment limit reached")
    entry: dict[str, Any] = {
        "id": agent_id,
        "direction": "inbound",
        "allow_tools": sorted(allow_tools or []),
        "deny_tools": sorted(deny_tools or []),
        "max_risk": max_risk,
        "max_dollars": max_dollars,
        "max_wall_seconds": max_wall_seconds,
        "data_scopes": sorted(data_scopes or []),
    }
    if expires_days is not None:
        if not isinstance(expires_days, (int, float)) or expires_days <= 0:
            raise ExternalAgentsError("expires_days must be a positive number")
        entry["expires_at"] = time.time() + float(expires_days) * 86400
    try:
        agent = put_agent(entry)
    except AgentTrustError as e:
        raise ExternalAgentsError(str(e)) from e
    fleet = "disabled"
    try:
        from . import fleet_memory
        if fleet_memory.enabled():
            fleet = ("registered" if fleet_memory.register_agent(
                agent.id, platform, description=description) else "refused")
    except Exception as e:
        fleet = "error"
        log.warning("external_agents: fleet roster registration failed for "
                    "%r: %s", agent.id, e)
    with _sidecar_locked():
        sidecar = _load_sidecar()
        prior = sidecar.get(agent.id, {})
        sidecar[agent.id] = {
            "platform": platform,
            "description": str(description or "")[:500],
            "owner": str(owner or "")[:200],
            "department": str(department or "")[:100],
            "enrolled_by": str(enrolled_by or "")[:200],
            "enrolled_at": prior.get("enrolled_at") or time.time(),
            "spent_dollars": float(prior.get("spent_dollars") or 0.0),
            "runs": int(prior.get("runs") or 0),
            "last_run_at": prior.get("last_run_at"),
        }
        _save_sidecar(sidecar)
    from .audit import EventKind, audit_event
    audit_event(EventKind.EXTERNAL_AGENT_ENROLLED, agent="external_agents",
                external_agent=agent.id, platform=platform,
                department=department, enrolled_by=enrolled_by)
    return {"id": agent.id, "platform": platform, "trust": "registered",
            "fleet_memory": fleet, "expires_at": agent.expires_at}


def unenroll(agent_id: str) -> bool:
    """Remove the managed trust entry + sidecar metadata. The fleet-memory
    roster entry (learning provenance) is kept — history is not rewritten."""
    from .agent_trust import remove_agent
    removed = remove_agent(agent_id)
    with _sidecar_locked():
        sidecar = _load_sidecar()
        if sidecar.pop(agent_id, None) is not None:
            _save_sidecar(sidecar)
            removed = True
    return removed


def mint_token(agent_id: str, surface: str) -> str:
    """Mint (or rotate) the bearer credential for one surface.

    Returns the token value — the ONLY time it is ever shown; the registry
    stores it for verification but no read path returns it. Raises on an
    unknown agent or surface."""
    from .agent_trust import AgentTrustError, lookup, put_agent
    if surface not in TOKEN_SURFACES:
        raise ExternalAgentsError(
            f"unknown surface {surface!r}; expected one of "
            f"{', '.join(TOKEN_SURFACES)}")
    agent = lookup(agent_id)
    if agent is None:
        raise ExternalAgentsError(f"agent {agent_id!r} is not enrolled")
    token = f"lw-{surface}-{secrets.token_urlsafe(32)}"
    entry = _entry_from_agent(agent)
    entry[f"{surface}_token"] = token
    try:
        put_agent(entry)
    except AgentTrustError as e:
        raise ExternalAgentsError(str(e)) from e
    from .audit import EventKind, audit_event
    audit_event(EventKind.EXTERNAL_CREDENTIAL_MINTED, agent="external_agents",
                external_agent=agent_id, surface=surface)
    return token


def _entry_from_agent(agent) -> dict:
    """A managed-registry entry dict reconstructed from a parsed TrustedAgent,
    so read-modify-write flows (token mint, revoke-with-note) can go through
    ``put_agent`` without reaching into the overlay file format."""
    return {
        "id": agent.id,
        "pubkey": agent.pubkey,
        "direction": agent.direction,
        "allow_tools": sorted(agent.allow_tools),
        "deny_tools": sorted(agent.deny_tools),
        "max_risk": agent.max_risk,
        "max_dollars": agent.max_dollars,
        "max_wall_seconds": agent.max_wall_seconds,
        "data_scopes": sorted(agent.data_scopes),
        "rest_token": agent.rest_token,
        "a2a_token": agent.a2a_token,
        "grpc_token": agent.grpc_token,
        "mcp_token": agent.mcp_token,
        "not_before": agent.not_before,
        "expires_at": agent.expires_at,
        "revoked": agent.revoked,
    }


# -- run ingest onto the Operating Record -------------------------------------

def record_run(agent_id: str, run: dict, *, shield: Any | None = None) -> dict:
    """Ingest one COMPLETED external run as a governed Operating Record row.

    ``run`` schema: ``{title, outcome: success|failure, summary?, steps?:
    [str], cost_dollars?, input_tokens?, output_tokens?, tool_calls?,
    department?}``. The goal lands owned by ``agent:<id>`` so every
    owner-scoped surface (spend, workforce, savings, audit binder,
    containment) sees the foreign agent as a first-class principal.

    Returns ``{goal_id, over_budget}``; raises :class:`ExternalAgentsError`
    on deny (not enrolled, revoked, plane disabled, poisoned text)."""
    if not enabled():
        raise ExternalAgentsError("external agents plane is disabled")
    from .agent_trust import decide_inbound, record_denied
    # enforced=True: the gateway is an external boundary in its own right, so
    # the registry binds here even when the global trust plane is disengaged.
    decision = decide_inbound(agent_id, enforced=True)
    if not decision.allowed:
        record_denied(agent_id, decision, direction="inbound",
                      correlation_id="record_run")
        raise ExternalAgentsError(f"denied: {decision.reason}")
    sidecar = _load_sidecar()
    if agent_id not in sidecar:
        raise ExternalAgentsError(
            f"agent {agent_id!r} has a trust entry but no enrollment; "
            "enroll it before ingesting runs")
    title = _clean_text(run.get("title"), shield=shield)
    summary = _clean_text(run.get("summary"), shield=shield)
    if title is None or summary is None or not (title or "").strip():
        raise ExternalAgentsError(
            "run title/summary rejected by the ingest screen")
    outcome = "success" if str(run.get("outcome", "")).lower() in (
        "success", "done", "ok") else "failure"
    steps: list[str] = []
    for raw in list(run.get("steps") or [])[:_MAX_STEPS]:
        cleaned = _clean_text(raw, shield=shield)
        if cleaned is None:
            raise ExternalAgentsError(
                "a run step was rejected by the ingest screen")
        if cleaned:
            steps.append(cleaned)
    cost = max(0.0, float(run.get("cost_dollars") or 0.0))
    meta = sidecar[agent_id]
    principal = f"agent:{agent_id}"
    from .world_model import open_world
    w = open_world()
    gid = w.create_goal(
        title, description=summary or "",
        owner=principal,
        domain=str(run.get("department") or meta.get("department") or ""))
    for step in steps:
        w.append_event(gid, principal, "status", step)
    eid = w.start_episode(gid)
    w.end_episode(
        eid, summary or title,
        "success" if outcome == "success" else "failure",
        cost_dollars=cost,
        input_tokens=max(0, int(run.get("input_tokens") or 0)),
        output_tokens=max(0, int(run.get("output_tokens") or 0)),
        tool_calls=max(0, int(run.get("tool_calls") or 0)),
    )
    w.set_goal_status(
        gid, "done" if outcome == "success" else "blocked",
        result=summary or title)
    over_budget = False
    with _sidecar_locked():
        sidecar = _load_sidecar()
        meta = sidecar.setdefault(agent_id, meta)
        meta["spent_dollars"] = float(meta.get("spent_dollars") or 0.0) + cost
        meta["runs"] = int(meta.get("runs") or 0) + 1
        meta["last_run_at"] = time.time()
        cap = decision.agent.max_dollars if decision.agent else None
        over_budget = bool(cap is not None and meta["spent_dollars"] > cap)
        meta["over_budget"] = over_budget
        _save_sidecar(sidecar)
    from .audit import EventKind, audit_event
    audit_event(EventKind.EXTERNAL_RUN_INGESTED, agent="external_agents",
                goal_id=gid, external_agent=agent_id, outcome=outcome,
                cost_dollars=cost, steps=len(steps), over_budget=over_budget)
    return {"goal_id": gid, "over_budget": over_budget}


# -- pre-action screening (the governance seam) -------------------------------

def screen(
    agent_id: str,
    tool: str,
    *,
    detail: str = "",
    risk: str | None = None,
    shield: Any | None = None,
) -> dict:
    """Decide one proposed external action BEFORE the agent takes it.

    Applies, in order: trust admission (registry / direction / revocation /
    tool + risk ceilings), the cumulative budget cutoff, Shield input
    scanning over ``detail`` (scanner error = DENY — external boundary), and
    the ``[actions] require_approval_at`` floor — at/above the floor the
    action parks a real approval row and the reply says who to wait for.

    Returns ``{allowed, rule, reason, requires_approval?, approval_id?}``.
    Every decision is audited."""
    if not enabled():
        return _screen_verdict(agent_id, tool, False, "disabled",
                               "external agents plane is disabled")
    from .agent_trust import decide_inbound, record_denied
    from .safety.tool_risk import RISK_LEVELS, risk_rank
    req_risk = str(risk or "").strip().lower()
    if req_risk and req_risk not in RISK_LEVELS:
        return _screen_verdict(agent_id, tool, False, "bad_risk",
                               f"unknown risk level {req_risk!r}")
    decision = decide_inbound(
        agent_id, requested_tools=[tool] if tool else (),
        max_risk=req_risk or None, enforced=True)
    if not decision.allowed:
        record_denied(agent_id, decision, direction="inbound",
                      correlation_id="screen")
        return _screen_verdict(agent_id, tool, False, decision.rule,
                               decision.reason)
    meta = _load_sidecar().get(agent_id)
    if meta is None:
        return _screen_verdict(agent_id, tool, False, "not_enrolled",
                               "agent has a trust entry but no enrollment")
    cap = decision.agent.max_dollars if decision.agent else None
    if cap is not None and float(meta.get("spent_dollars") or 0.0) > cap:
        return _screen_verdict(
            agent_id, tool, False, "budget",
            f"cumulative reported spend exceeds the ${cap:,.2f} ceiling")
    if detail and shield is not None:
        try:
            verdict = shield.scan_input(str(detail)[:_MAX_TEXT])
            if getattr(verdict, "blocked", False):
                return _screen_verdict(
                    agent_id, tool, False, "shield",
                    "action detail was blocked by the input screen")
        except Exception:
            # Fail CLOSED: an erroring scanner must never wave an external
            # action through unscreened.
            return _screen_verdict(
                agent_id, tool, False, "screen_error",
                "input screen unavailable; action denied (fail-closed)")
    floor = _approval_floor()
    if req_risk and risk_rank(req_risk) >= risk_rank(floor):
        from .world_model import open_world
        approval_id = open_world().create_approval(
            f"external:{agent_id}:{tool}", risk=req_risk,
            scope=meta.get("department") or None,
            detail=str(detail or "")[:2000], provenance="external_agents",
            requested_by=f"agent:{agent_id}")
        out = _screen_verdict(
            agent_id, tool, False, "approval_required",
            f"risk {req_risk} is at/above the {floor} approval floor; "
            "awaiting a human decision")
        out["requires_approval"] = True
        out["approval_id"] = approval_id
        return out
    return _screen_verdict(agent_id, tool, True, "allow", "within ceilings")


def _approval_floor() -> str:
    from .safety.tool_risk import RISK_LEVELS
    try:
        from .config import load_config
        v = str((load_config() or {}).get("actions", {}).get(
            "require_approval_at", "high")).strip().lower()
        return v if v in RISK_LEVELS else "high"
    except Exception:
        return "high"


def _screen_verdict(agent_id: str, tool: str, allowed: bool, rule: str,
                    reason: str) -> dict:
    from .audit import EventKind, audit_event
    audit_event(EventKind.EXTERNAL_ACTION_SCREENED, agent="external_agents",
                external_agent=agent_id, tool=tool, allowed=allowed,
                rule=rule, reason=reason)
    return {"allowed": allowed, "rule": rule, "reason": reason}


def approval_status(approval_id: int) -> dict:
    """Poll endpoint for a parked ``screen`` approval: the external agent
    holds its action until the human decides in the dashboard queue."""
    from .world_model import open_world
    approval = open_world().get_approval(int(approval_id))
    if approval is None or approval.provenance != "external_agents":
        raise ExternalAgentsError(f"unknown approval {approval_id!r}")
    return {"approval_id": approval.id, "status": approval.status,
            "risk": approval.risk}


# -- roster / detail (the dashboard's data) -----------------------------------

def roster() -> list[dict]:
    """Enrolled agents joined across the trust plane and the sidecar (never
    including token values — only which surfaces hold one)."""
    from .agent_trust import load_registry
    registry = load_registry()
    out = []
    for agent_id, meta in sorted(_load_sidecar().items()):
        agent = registry.get(agent_id)
        active, rule = agent.is_active() if agent else (False, "missing_trust")
        out.append({
            "id": agent_id,
            "platform": meta.get("platform", "custom"),
            "platform_label": PLATFORMS.get(
                str(meta.get("platform")), "Custom / other runtime"),
            "description": meta.get("description", ""),
            "owner": meta.get("owner", ""),
            "department": meta.get("department", ""),
            "enrolled_at": meta.get("enrolled_at"),
            "active": active,
            "lifecycle": rule,
            "max_risk": agent.max_risk if agent else None,
            "max_dollars": agent.max_dollars if agent else None,
            "spent_dollars": round(float(meta.get("spent_dollars") or 0.0), 4),
            "over_budget": bool(meta.get("over_budget")),
            "runs": int(meta.get("runs") or 0),
            "last_run_at": meta.get("last_run_at"),
            "credentials": [s for s in TOKEN_SURFACES
                            if agent and getattr(agent, f"{s}_token", "")],
        })
    return out


def agent_detail(agent_id: str) -> dict:
    """One agent's roster row + its Operating Record footprint."""
    rows = [r for r in roster() if r["id"] == agent_id]
    if not rows:
        raise ExternalAgentsError(f"agent {agent_id!r} is not enrolled")
    row = rows[0]
    principal = f"agent:{agent_id}"
    try:
        from .world_model import open_world
        w = open_world()
        row["record"] = w.total_spend(owner=principal)
        row["episodes"] = [
            {"goal_id": e.goal_id, "started_at": e.started_at,
             "ended_at": e.ended_at, "outcome": e.outcome,
             "cost_dollars": e.cost_dollars}
            for e in w.list_episodes(limit=10, owner=principal)]
    except Exception as e:
        log.warning("external_agents: world lookup failed for %r: %s",
                    agent_id, e)
        row["record"] = {}
        row["episodes"] = []
    return row


def status() -> dict:
    """Counts for the health surface and the dashboard header."""
    rows = roster()
    return {
        "enabled": enabled(),
        "enrolled": len(rows),
        "active": sum(1 for r in rows if r["active"]),
        "over_budget": sum(1 for r in rows if r["over_budget"]),
        "platforms": sorted({r["platform"] for r in rows}),
        "runs": sum(r["runs"] for r in rows),
        "reported_dollars": round(
            sum(r["spent_dollars"] for r in rows), 4),
    }
