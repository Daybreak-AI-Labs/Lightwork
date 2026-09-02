"""Agent Control Plane: one governed roster and one set of operator verbs over
every agent this deployment runs or admits.

Lightwork already has the primitives an operator needs to control an agent:
the capability revocation registry (``revocation.py``, consulted on every tool
call), the external-agent trust registry (``agent_trust.py``), the security
plane's containment (``agent_edr.py``), the bring-your-own-agent sidecar
(``external_agents.py``) and the local deployment killswitch
(``killswitch.py``). What it
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
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

KIND_NATIVE = "native"
KIND_EXTERNAL = "external"
EXTERNAL_PREFIX = "agent:"

# Principal values are opaque identity-provider subjects, not filenames or URL
# path components.  Keep this domain broad enough for valid OIDC/SAML subjects
# (Unicode, spaces, ``|`` and ``/`` are all legitimate) while rejecting values
# that are ambiguous in operator output or unsafe to persist in text records.
# The dashboard transports these values in a query/body; its legacy path route
# remains deliberately restricted for backwards compatibility.
_MAX_PRINCIPAL = 512
_MAX_REASON = 500
_MAX_NATIVE_GOALS = 10_000
_MAX_ROSTER_PAGE = 500


class ControlPlaneError(RuntimeError):
    """A verb could not be applied (the deployment state is unchanged)."""


class ControlPlaneDisabled(ControlPlaneError):
    """The plane is off: ``[control_plane] enable`` is false."""


class ControlPlaneDependencyError(ControlPlaneError):
    """A roster authority is unavailable, so no healthy-empty answer exists."""

    def __init__(self, dependency: str):
        self.dependency = dependency
        super().__init__(f"{dependency} authority is unavailable")


class _ConcurrentHalt(ControlPlaneError):
    """A newer local HALT generation superseded an in-flight clear."""


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
    code: str | None = None
    detail: str = ""
    notes: list[str] = field(default_factory=list)
    audit_written: bool | None = None

    def to_dict(self) -> dict:
        return {"verb": self.verb, "principal": self.principal, "ok": self.ok,
                "code": self.code,
                "detail": self.detail, "notes": list(self.notes),
                "audit_written": self.audit_written}


@dataclass(frozen=True)
class ControlPlaneSnapshot:
    """One coherent authority scan reusable for status plus roster rendering."""

    rows: tuple[AgentPrincipal, ...]
    captured_at: float

    def page(self, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        start = max(0, int(offset))
        size = max(1, min(int(limit), _MAX_ROSTER_PAGE))
        selected = self.rows[start:start + size]
        return {
            "captured_at": self.captured_at,
            "offset": start,
            "limit": size,
            "total": len(self.rows),
            "items": [row.to_dict() for row in selected],
        }


# ---- gate -------------------------------------------------------------------

def enabled(*, strict: bool = False) -> bool:
    """``[control_plane] enable`` or ``MAVERICK_CONTROL_PLANE=1``.

    Ordinary callers retain the historical fail-soft predicate.  Status and
    mutation gates pass ``strict=True`` so an unreadable active config source
    is reported as an unavailable authority, never laundered into "disabled".
    """
    try:
        from .config import config_source_errors, get_control_plane

        configured = bool(get_control_plane().get("enable"))
        if strict and config_source_errors():
            raise ControlPlaneDependencyError("control-plane configuration")
        return configured
    except ControlPlaneDependencyError:
        raise
    except Exception as exc:
        if strict:
            raise ControlPlaneDependencyError(
                "control-plane configuration"
            ) from exc
        log.warning("control_plane: config unreadable; plane is off", exc_info=True)
        return False


def _require_enabled(verb: str) -> None:
    if not enabled(strict=True):
        raise ControlPlaneDisabled(
            f"control plane is off: `{verb}` refused (set [control_plane] enable "
            "= true or MAVERICK_CONTROL_PLANE=1)")


def valid_principal(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and value
        and value == value.strip()
        and len(value) <= _MAX_PRINCIPAL
        and value.isprintable()
    )


def _check_principal(principal: object) -> str:
    if not valid_principal(principal):
        raise ValueError(
            "invalid principal (non-empty printable text, max 512 characters)"
        )
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

def _audit(verb: str, principal: str, actor: str, ok: bool, detail: str,
           **extra) -> bool:
    """Attempt one audit row and return whether it was written.

    Signing is deployment policy, not implied here. A configured audit refusal
    propagates; an ordinary fail-soft outage returns false and is exposed on the
    :class:`ControlResult`.
    """
    from .audit import EventKind, audit_event
    return audit_event(
        EventKind.CONTROL_PLANE_ACTION, agent="control_plane",
        verb=verb, principal=principal, actor=actor, ok=bool(ok),
        detail=detail[:_MAX_REASON], **extra,
    )


def _audit_result(result: ControlResult, actor: str, detail: str,
                  **extra: Any) -> ControlResult:
    if not result.ok and result.code is None:
        result.code = "not_applied"
    result.audit_written = _audit(
        result.verb, result.principal, actor, result.ok, detail, **extra,
    )
    if not result.audit_written:
        result.notes.append("control action audit row was not written")
    return result


def _dependency_failure(
    result: ControlResult,
    *,
    authority: str,
    exc: BaseException,
) -> None:
    """Set a stable dependency result without exposing exception detail."""

    log.warning(
        "control_plane: %s unavailable during %s",
        authority,
        result.verb,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    result.code = "dependency_unavailable"
    result.detail = f"{authority} is unavailable"


# ---- roster -----------------------------------------------------------------

def _revocations() -> dict[str, Any]:
    try:
        from .revocation import shared
        return shared().revoked()
    except Exception as exc:
        log.warning("control_plane: revocation registry unreadable", exc_info=True)
        raise ControlPlaneDependencyError("revocation registry") from exc


def _native_rows(revoked: dict[str, Any]) -> list[AgentPrincipal]:
    """Running goals grouped by the capability principal they run under, plus
    every revoked native principal (a suspended run must stay listed so it can
    be resumed after its goals have drained)."""
    counts: dict[str, int] = {}
    try:
        from .world_model import open_world
        # ``active`` is the WorldModel's actual in-flight status.  ``running``
        # is a UI label, not a stored status, and querying it produced a
        # permanently empty native roster against the real backend.
        goals = open_world().list_goals(
            status="active", limit=_MAX_NATIVE_GOALS + 1, order="desc",
        )
        if len(goals) > _MAX_NATIVE_GOALS:
            raise ControlPlaneDependencyError("bounded native-goal roster")
        for goal in goals:
            runtime = (
                getattr(goal, "execution_principal", "")
                or getattr(goal, "owner", "")
                or "user:local"
            )
            counts[runtime] = counts.get(runtime, 0) + 1
    except ControlPlaneDependencyError:
        raise
    except Exception as exc:
        log.warning("control_plane: world model unavailable; native runs unknown",
                    exc_info=True)
        raise ControlPlaneDependencyError("world model") from exc
    principals = set(counts) | {p for p in revoked if not is_external(p)}
    rows = []
    for principal in sorted(principals):
        rev = revoked.get(principal)
        running = counts.get(principal, 0)
        protected = bool(getattr(rev, "protected", False)) if rev is not None else False
        state = (
            "contained" if protected else
            "suspended" if rev is not None else
            "running" if running else "idle"
        )
        rows.append(AgentPrincipal(
            principal=principal, kind=KIND_NATIVE, label=principal, state=state,
            running_goals=running, revoked=rev is not None,
            revoked_reason=getattr(rev, "reason", "") if rev is not None else "",
            contained=protected,
        ))
    return rows


def _external_rows(revoked: dict[str, Any]) -> list[AgentPrincipal]:
    from .agent_trust import load_registry_strict
    contained: dict[str, bool] = {}
    platforms: dict[str, str] = {}
    try:
        registry = load_registry_strict()
    except Exception as exc:
        log.warning("control_plane: agent trust authority unavailable",
                    exc_info=True)
        raise ControlPlaneDependencyError("agent trust registry") from exc
    try:
        from .external_agents import roster as xa_roster
        for row in xa_roster(registry=registry, strict=True):
            contained[row["id"]] = bool(row.get("contained"))
            platforms[row["id"]] = str(row.get("platform_label") or "")
    except Exception as exc:
        log.warning("control_plane: external-agent sidecar unavailable",
                    exc_info=True)
        raise ControlPlaneDependencyError("external-agent sidecar") from exc
    rows = []
    for agent in registry.values():
        principal = EXTERNAL_PREFIX + agent.id
        rev = revoked.get(principal)
        active, rule = agent.is_active()
        protected = bool(getattr(rev, "protected", False)) if rev is not None else False
        is_contained = bool(protected or contained.get(agent.id))
        if is_contained:
            state = "contained"
        elif rev is not None or agent.revoked:
            state = "suspended"
        else:
            state = "active" if active else rule
        rows.append(AgentPrincipal(
            principal=principal, kind=KIND_EXTERNAL, label=agent.id, state=state,
            revoked=rev is not None or agent.revoked,
            revoked_reason=getattr(rev, "reason", "") if rev is not None else "",
            contained=is_contained,
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


def snapshot() -> ControlPlaneSnapshot:
    """Scan each roster authority once and return a reusable snapshot."""
    revoked = _revocations()
    rows = tuple(_native_rows(revoked) + _external_rows(revoked))
    return ControlPlaneSnapshot(rows=rows, captured_at=time.time())


def roster(*, source: ControlPlaneSnapshot | None = None) -> list[AgentPrincipal]:
    """Every governed principal from one strict authority snapshot."""
    view = source if source is not None else snapshot()
    return list(view.rows)


def lookup(principal: str, *, source: ControlPlaneSnapshot | None = None,
           ) -> AgentPrincipal | None:
    principal = _check_principal(principal)
    for row in roster(source=source):
        if row.principal == principal:
            return row
    return None


# ---- trust-entry helpers (external agents) -----------------------------------

def _trust_entry(agent_id: str):
    from .agent_trust import load_registry_strict
    return load_registry_strict().get(agent_id)


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
    except Exception as exc:
        _dependency_failure(result, authority="revocation authority", exc=exc)
    return _audit_result(result, actor, reason)


def resume(principal: str, *, actor: str = "") -> ControlResult:
    """Lift an ordinary :func:`suspend`.

    A security containment is deliberately excluded; only :func:`release` may
    clear its durable protected revocation.
    """
    _require_enabled("resume")
    principal = _check_principal(principal)
    actor = _check_actor(actor)
    from .revocation import shared
    result = ControlResult("resume", principal, ok=False)
    try:
        registry = shared()
        if is_external(principal):
            agent_id = _external_id(principal)

            def _restore_trust() -> None:
                if not _set_trust_revoked(agent_id, False, result.notes):
                    raise RuntimeError("trust entry is still revoked")

            observed = registry.revoked().get(principal)
            if observed is None:
                # The trust overlay can be independently revoked. Guard its
                # restore with the same authority lock so a concurrent security
                # containment either refuses this callback or lands afterward
                # and re-applies the restrictive projection.
                registry.run_if_unprotected(principal, _restore_trust)
                result.notes.append("principal was not revoked")
            else:
                registry.unrevoke(principal, durable_clear=_restore_trust)
        elif not registry.unrevoke(principal):
            result.notes.append("principal was not revoked")
        if registry.is_revoked(principal):
            result.detail = "revocation is still in place"
        else:
            result.ok = True
            result.detail = "principal may act again"
    except Exception as exc:
        from .revocation import ProtectedRevocationError
        if isinstance(exc, ProtectedRevocationError):
            result.code = "protected_containment"
            result.detail = (
                "resume refused: principal is security-contained; use release"
            )
        else:
            _dependency_failure(result, authority="revocation authority", exc=exc)
    return _audit_result(result, actor, result.detail)


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
        def _apply_external_authorities() -> bool:
            if not is_external(principal):
                return True
            agent_id = _external_id(principal)
            if not _set_trust_revoked(agent_id, True, result.notes):
                result.detail = "capability contained but the trust entry did not flip"
                return False
            try:
                from .external_agents import contain as xa_contain
                from .external_agents import (
                    containment_state as xa_containment_state,
                )

                before = xa_containment_state(agent_id)
                if before is None:
                    result.notes.append("agent is not enrolled in the gateway")
                    return True
                landed = bool(xa_contain(agent_id, contained_by=actor))
                after = xa_containment_state(agent_id)
                if not landed or after is not True:
                    result.detail = "gateway containment did not persist"
                    return False
                return True
            except Exception:
                result.code = "dependency_unavailable"
                result.detail = "gateway containment authority is unavailable"
                return False

        outcome = edr_contain(principal, reason=f"{actor}: {reason}",
                              registry=registry, edges=edges,
                              durable_apply=_apply_external_authorities)
        result.notes.extend(outcome.notes)
        result.notes.extend(f"failed: {f}" for f in outcome.failed)
        result.ok = outcome.contained
        if result.ok:
            result.detail = f"contained; revoked {len(outcome.revoked)} principal(s)"
        elif not result.detail:
            result.detail = "containment incomplete"
    except Exception as exc:
        _dependency_failure(result, authority="containment authority", exc=exc)
    return _audit_result(result, actor, reason)


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
        def _clear_external_authorities() -> None:
            if not is_external(principal):
                return
            agent_id = _external_id(principal)

            # This callback runs inside the protected revocation CAS lock.
            # Clear the trust projection first; if the gateway cannot then be
            # confirmed clear, restore every restrictive projection before
            # returning an error and retaining the protected revocation row.
            if not _set_trust_revoked(agent_id, False, result.notes):
                _set_trust_revoked(agent_id, True, result.notes)
                result.detail = "trust release did not persist"
                raise RuntimeError("dependent trust release did not persist")

            gateway_enrolled = False
            try:
                from .external_agents import contain as xa_contain
                from .external_agents import (
                    containment_state as xa_containment_state,
                )
                from .external_agents import release as xa_release

                before = xa_containment_state(agent_id)
                if before is None:
                    result.notes.append("agent is not enrolled in the gateway")
                    return
                gateway_enrolled = True
                landed = bool(xa_release(agent_id, released_by=actor))
                after = xa_containment_state(agent_id)
                if not landed or after is not False:
                    raise RuntimeError("dependent gateway release did not persist")
            except Exception:
                result.detail = "gateway release did not persist"
                if not _set_trust_revoked(agent_id, True, result.notes):
                    result.notes.append("failed to restore the restrictive trust projection")
                if gateway_enrolled:
                    try:
                        restored = bool(xa_contain(agent_id, contained_by=actor))
                        if not restored or xa_containment_state(agent_id) is not True:
                            result.notes.append(
                                "failed to restore the restrictive gateway projection"
                            )
                    except Exception:
                        result.notes.append(
                            "gateway compensation was unavailable; durable capability "
                            "containment remains in place"
                        )
                raise RuntimeError("dependent containment release failed") from None

        outcome = edr_release(
            principal,
            registry=registry,
            durable_clear=_clear_external_authorities,
        )
        result.notes.extend(outcome.notes)
        result.notes.extend(f"failed: {f}" for f in outcome.failed)
        result.ok = outcome.contained  # "no failures" in Containment terms
        if outcome.superseded:
            result.code = "concurrent_containment"
            result.detail = "release superseded; newer containment retained"
        elif result.ok:
            result.detail = "released"
        elif not result.detail:
            result.detail = "release incomplete"
    except Exception as exc:
        _dependency_failure(result, authority="containment authority", exc=exc)
    return _audit_result(result, actor, result.detail)


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
    from .agent_trust import AgentTrustError, attenuate_agent
    result = ControlResult("attenuate", principal, ok=False)
    changed: dict[str, Any] | None = None
    try:
        after, created_overlay = attenuate_agent(
            agent_id,
            deny_tools=deny_tools,
            max_risk=max_risk,
            max_dollars=max_dollars,
            max_wall_seconds=max_wall_seconds,
        )
        if created_overlay:
            result.notes.append("trust entry is config-defined; wrote a managed overlay")
        result.ok = True
        result.detail = "ceiling narrowed"
        changed = {
            "deny_tools": sorted(after.deny_tools),
            "max_risk": after.max_risk,
            "max_dollars": after.max_dollars,
            "max_wall_seconds": after.max_wall_seconds,
        }
    except AgentTrustError as exc:
        message = str(exc)
        if "not in the trust registry" in message:
            raise LookupError(
                f"{principal} is not in the trust registry"
            ) from exc
        safe_request_prefixes = (
            "attenuation requires ",
            "attenuation request does not tighten ",
            "deny_tools ",
            "max_risk ",
            "max_dollars ",
            "max_wall_seconds ",
        )
        if message.startswith(safe_request_prefixes):
            raise ValueError(message) from exc
        _dependency_failure(result, authority="agent trust authority", exc=exc)
    except Exception as exc:
        _dependency_failure(result, authority="agent trust authority", exc=exc)
    extra = {"ceiling": changed} if changed is not None else {}
    return _audit_result(result, actor, result.detail, **extra)


def halt_all(reason: str, *, actor: str = "") -> ControlResult:
    """Arm this deployment's durable local HALT file and verify it.

    This is intentionally not described as cluster-wide. Replicas on other
    hosts need their own shared halt authority; the dashboard uses that hardened
    shared API rather than this local CLI primitive.
    """
    _require_enabled("halt")
    reason = _check_reason(reason)
    actor = _check_actor(actor)
    from . import killswitch
    result = ControlResult("halt", "*", ok=False)
    try:
        from .file_lock import (
            atomic_write_text,
            cross_process_lock,
            prepare_private_directory,
        )
        path = killswitch._halt_file_path()
        # ``MAVERICK_HALT_FILE`` is caller-selected.  Never seize an existing
        # shared parent by rewriting its ACL; only create a missing dedicated
        # directory or require an existing parent that is already private.
        prepare_private_directory(path.parent)
        with cross_process_lock(
            killswitch._halt_authority_lock_path(path), strict=True,
        ):
            payload = json.dumps({
                "reason": reason,
                "source": f"control_plane:{actor}",
                "created_at": time.time(),
                "generation": secrets.token_urlsafe(24),
                "scope": "local-deployment",
            }, sort_keys=True)
            atomic_write_text(path, payload, mode=0o600)
            result.ok = bool(
                killswitch._halt_file_generation(path) is not None
                and killswitch._file_halt_active(
                    min_interval=0.0, fail_closed=True,
                )
            )
        result.detail = (
            "durable local HALT file armed"
            if result.ok else "local HALT file did not take effect"
        )
    except Exception as exc:
        _dependency_failure(result, authority="local HALT authority", exc=exc)
    return _audit_result(result, actor, reason, scope="local-deployment")


def clear_halt(*, actor: str = "") -> ControlResult:
    """Clear and verify this deployment's local HALT file/process state."""
    _require_enabled("clear-halt")
    actor = _check_actor(actor)
    from . import killswitch
    result = ControlResult("clear-halt", "*", ok=False)
    try:
        from .file_lock import cross_process_lock, require_private_directory

        path = killswitch._halt_file_path()
        # Snapshot the generation before waiting for the writer lock.  An arm
        # that overlaps this clear either changes the generation before we get
        # the lock (and is retained) or publishes after we release it (and is
        # retained).  A clear may delete only the exact generation it observed.
        require_private_directory(path.parent)
        expected_generation = killswitch._halt_file_generation(path)
        expected_process_generation = killswitch._in_process_halt_generation()
        with cross_process_lock(
            killswitch._halt_authority_lock_path(path), strict=True,
        ):
            if killswitch._halt_file_generation(path) != expected_generation:
                raise _ConcurrentHalt

            def _delete_expected_generation() -> None:
                if killswitch._halt_file_generation(path) != expected_generation:
                    raise _ConcurrentHalt
                if expected_generation is not None:
                    path.unlink()

            # Clearing expands authority.  The exact-generation unlink runs
            # inside the strict process barrier; any failure leaves both the
            # durable file and the in-process halt unchanged.
            killswitch.clear(
                durable_clear=_delete_expected_generation,
                expected_in_process_generation=expected_process_generation,
            )
        result.ok = not killswitch._file_halt_active(
            min_interval=0.0, fail_closed=True,
        )
        result.detail = (
            "local HALT file cleared"
            if result.ok else "local HALT file is still active"
        )
        if result.ok and killswitch.is_active():
            result.notes.append(
                "local HALT cleared; another halt authority remains active"
            )
    except (killswitch.HaltGenerationChanged, _ConcurrentHalt):
        result.code = "concurrent_halt"
        result.detail = "newer local HALT generation remains armed"
    except Exception as exc:
        _dependency_failure(result, authority="local HALT authority", exc=exc)
    return _audit_result(
        result, actor, result.detail, scope="local-deployment",
    )


# ---- read-side ----------------------------------------------------------------

def ledger(principal: str, *, since: float | None = None, limit: int = 50) -> list[dict]:
    """The principal's recorded forensic timeline, newest last.

    Whether individual audit rows are signed is deployment/evidence metadata,
    not implied by this convenience view.
    """
    principal = _check_principal(principal)
    from .agent_edr import timeline
    rows = timeline(principal, since=since)
    return [d.to_dict() for d in rows[-max(1, int(limit)):]]


def _strict_halt_active() -> bool:
    """Return current halt state or refuse an unreadable halt authority."""

    from . import killswitch

    try:
        killswitch.check(force_refresh=True, fail_closed=True)
    except killswitch.Halted as exc:
        if exc.source in {"file-error", "shared-error"}:
            raise ControlPlaneDependencyError("halt") from exc
        return True
    except Exception as exc:
        raise ControlPlaneDependencyError("halt") from exc
    return False


def status(*, source: ControlPlaneSnapshot | None = None) -> dict:
    """Console header derived from one reusable authority snapshot."""
    view = source if source is not None else snapshot()
    rows = roster(source=view)
    native = [r for r in rows if r.kind == KIND_NATIVE]
    external = [r for r in rows if r.kind == KIND_EXTERNAL]
    out: dict[str, Any] = {
        "schema": "lightwork.agent-control-plane-status.v1",
        "captured_at": view.captured_at,
        "enabled": enabled(strict=True),
        "halted": _strict_halt_active(),
        "halt_scope": "observed-process-and-configured-authorities",
        "native": {"principals": len(native),
                   "running_goals": sum(r.running_goals for r in native),
                   "suspended": sum(1 for r in native if r.revoked and not r.contained),
                   "contained": sum(1 for r in native if r.contained)},
        "external": {"agents": len(external),
                     "active": sum(1 for r in external if r.state == "active"),
                     "suspended": sum(
                         1 for r in external if r.revoked and not r.contained
                     ),
                     "contained": sum(1 for r in external if r.contained)},
    }
    try:
        from .agent_trust import agent_trust_enforced
        out["trust_enforced"] = bool(agent_trust_enforced())
    except Exception as exc:
        raise ControlPlaneDependencyError("agent trust policy") from exc
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
    "ControlPlaneDependencyError",
    "ControlPlaneError",
    "ControlPlaneSnapshot",
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
    "snapshot",
    "status",
    "suspend",
    "valid_principal",
]
