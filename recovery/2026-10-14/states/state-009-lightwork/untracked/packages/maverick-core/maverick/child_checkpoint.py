"""Durable child-agent execution for the single-control-plane profile.

The root checkpoint records the parent loop at a turn boundary.  A spawn tool
can then run for minutes before the parent reaches its next boundary.  Without
a second ledger, a process crash in that interval loses the child tree and a
resume may silently run the same children again.

This module owns that second ledger.  Records are bound to tenant, goal,
episode, stable parent identity, parent step, and tool ordinal.  A completed
child or operation is replayed from its committed result.  A child at a clean
turn checkpoint is reconstructed from its committed messages.  A crash while
an LLM/tool boundary is in flight is deliberately *indeterminate*: production
stops for reconciliation instead of guessing whether paid or consequential
work happened.

The store is intentionally SQLite/single-control-plane scoped.  It uses the
same WorldModel write transaction and locking discipline as the root
checkpointer.  A distributed control plane needs a shared transactional store
and a lease/fencing protocol; this module does not claim that profile.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import hmac
import json
import math
import secrets
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from .checkpoint import (
    CheckpointDurabilityError,
    snapshot_budget,
)
from .checkpoint import (
    enabled as root_checkpoint_enabled,
)
from .checkpoint import (
    required as root_checkpoint_required,
)

SCHEMA_VERSION = 1
MAX_TASK_BYTES = 64 * 1024
MAX_MESSAGES_BYTES = 16 * 1024 * 1024
MAX_RESULT_BYTES = 1024 * 1024
MAX_AGENT_ID_BYTES = 256
MAX_ROLE_BYTES = 256

_BOOT_ID = secrets.token_hex(16)
_SPAWN_TOOLS = frozenset({"spawn_subagent", "spawn_swarm", "spawn_specialist"})
_TERMINAL_OPERATION_STATES = frozenset({"joined", "released"})
_CURRENT_INVOCATION: contextvars.ContextVar[ToolInvocation | None] = (
    contextvars.ContextVar("maverick_child_tool_invocation", default=None)
)


class ChildDurabilityError(CheckpointDurabilityError):
    """Child execution state cannot be trusted or committed."""


class ChildIndeterminateError(ChildDurabilityError):
    """A crash crossed an uncommitted child model/tool boundary."""


class DurableSpawnCapReached(ChildDurabilityError):
    """The durable run-wide spawn reservation would exceed its cap."""


@dataclass(frozen=True)
class ToolInvocation:
    tenant_id: str
    goal_id: int
    episode_id: int
    parent_agent_id: str
    parent_step: int
    tool_ordinal: int


@dataclass(frozen=True)
class OperationIdentity:
    tenant_id: str
    goal_id: int
    episode_id: int
    parent_agent_id: str
    parent_step: int
    tool_ordinal: int
    tool_name: str
    args_digest: str
    invocation_id: str


@dataclass(frozen=True)
class ChildDescriptor:
    child_id: str
    role: str
    depth: int
    task: str
    domain: str
    capability_digest: str
    authority_digest: str


@dataclass(frozen=True)
class ChildSnapshot:
    child_id: str
    status: str
    next_step: int
    messages: list[dict]
    budget: dict[str, Any]
    result: dict[str, Any] | None
    owner_token: str
    runtime_digest: str


@dataclass(frozen=True)
class OperationSnapshot:
    status: str
    result: str | None
    expected_children: int


_CREATE_OPERATIONS = """
CREATE TABLE IF NOT EXISTS child_operations (
    tenant_id          TEXT NOT NULL,
    goal_id            INTEGER NOT NULL,
    episode_id         INTEGER NOT NULL,
    invocation_id      TEXT NOT NULL,
    parent_agent_id    TEXT NOT NULL,
    parent_step        INTEGER NOT NULL,
    tool_ordinal       INTEGER NOT NULL,
    tool_name          TEXT NOT NULL,
    args_digest        TEXT NOT NULL,
    mode               TEXT NOT NULL,
    status             TEXT NOT NULL,
    expected_children  INTEGER NOT NULL,
    reservation_units  INTEGER NOT NULL,
    result             TEXT NOT NULL DEFAULT '',
    owner_token        TEXT NOT NULL,
    binding_digest     TEXT NOT NULL,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL,
    PRIMARY KEY (tenant_id, goal_id, episode_id, invocation_id)
)
"""

_CREATE_CHILDREN = """
CREATE TABLE IF NOT EXISTS child_agent_checkpoints (
    tenant_id          TEXT NOT NULL,
    goal_id            INTEGER NOT NULL,
    episode_id         INTEGER NOT NULL,
    invocation_id      TEXT NOT NULL,
    child_index        INTEGER NOT NULL,
    child_id           TEXT NOT NULL,
    role               TEXT NOT NULL,
    depth              INTEGER NOT NULL,
    task_digest        TEXT NOT NULL,
    domain_name        TEXT NOT NULL,
    capability_digest  TEXT NOT NULL,
    authority_digest   TEXT NOT NULL,
    runtime_digest     TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL,
    next_step          INTEGER NOT NULL,
    messages           TEXT NOT NULL,
    budget             TEXT NOT NULL,
    result             TEXT NOT NULL DEFAULT '',
    reservation_state  TEXT NOT NULL,
    owner_token        TEXT NOT NULL,
    binding_digest     TEXT NOT NULL,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL,
    PRIMARY KEY (
        tenant_id, goal_id, episode_id, invocation_id, child_index
    ),
    UNIQUE (tenant_id, goal_id, episode_id, child_id)
)
"""

_CREATE_PARENT_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_child_operations_parent "
    "ON child_operations(tenant_id, goal_id, episode_id, "
    "parent_agent_id, parent_step, status)"
)
_CREATE_RESERVATION_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_child_reservations_run "
    "ON child_agent_checkpoints(tenant_id, goal_id, episode_id, "
    "reservation_state)"
)


def _tenant_for(ctx: Any) -> str:
    value = getattr(ctx, "tenant_id", None)
    if value is None:
        try:
            from .paths import current_tenant_id

            value = current_tenant_id()
        except Exception:
            value = None
    tenant = "" if value is None else str(value).strip()
    if len(tenant.encode("utf-8")) > MAX_AGENT_ID_BYTES:
        raise ChildDurabilityError("child checkpoint tenant identity is invalid")
    return tenant


def _agent_identity(agent: Any) -> str:
    value = str(getattr(agent, "checkpoint_id", "") or "").strip()
    if not value or len(value.encode("utf-8")) > MAX_AGENT_ID_BYTES:
        raise ChildDurabilityError("child checkpoint parent identity is invalid")
    return value


def _run_binding(ctx: Any) -> tuple[str, int, int]:
    tenant = _tenant_for(ctx)
    goal_id = getattr(ctx, "goal_id", None)
    episode_id = getattr(ctx, "episode_id", 0) or 0
    if isinstance(goal_id, bool) or not isinstance(goal_id, int) or goal_id < 0:
        raise ChildDurabilityError("child checkpoint goal identity is invalid")
    if (
        isinstance(episode_id, bool)
        or not isinstance(episode_id, int)
        or episode_id < 0
    ):
        raise ChildDurabilityError("child checkpoint episode identity is invalid")
    return tenant, goal_id, episode_id


def active(ctx: Any) -> bool:
    """Whether child durability is active for this context.

    The admitted production profile requires root durability, which in turn
    requires this child ledger for any spawn.  Development keeps the existing
    opt-in posture.
    """
    is_required = root_checkpoint_required()
    is_enabled = root_checkpoint_enabled()
    if is_required and not is_enabled:
        raise ChildDurabilityError("required child checkpointing is disabled")
    if not is_enabled:
        return False
    if getattr(ctx, "world", None) is None:
        if is_required:
            raise ChildDurabilityError("required child checkpoint store is unavailable")
        return False
    return True


def _canonical_json(value: Any, *, label: str, max_bytes: int) -> str:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ChildDurabilityError(f"{label} is not strict JSON") from exc
    if len(payload.encode("utf-8")) > max_bytes:
        raise ChildDurabilityError(f"{label} exceeds its durability boundary")
    return payload


def _digest_parts(*parts: object) -> str:
    h = hashlib.sha256()
    for part in parts:
        encoded = str(part).encode("utf-8")
        h.update(len(encoded).to_bytes(8, "big"))
        h.update(encoded)
    return h.hexdigest()


def capability_digest(capability: Any) -> str:
    """Stable digest of an authority grant without storing the grant itself."""
    if capability is None:
        return hashlib.sha256(b"unrestricted").hexdigest()
    signing_bytes = getattr(capability, "signing_bytes", None)
    if not callable(signing_bytes):
        raise ChildDurabilityError("child capability is not digestible")
    try:
        payload = signing_bytes()
    except Exception as exc:
        raise ChildDurabilityError("child capability digest failed") from exc
    if not isinstance(payload, bytes) or len(payload) > 1024 * 1024:
        raise ChildDurabilityError("child capability digest payload is invalid")
    return hashlib.sha256(payload).hexdigest()


def authority_digest(
    *,
    parent_capability: Any,
    child_capability: Any,
    tenant_id: str,
    goal_id: int,
    episode_id: int,
    parent_agent_id: str,
    child_id: str,
) -> str:
    return _digest_parts(
        "child-authority-v1",
        tenant_id,
        goal_id,
        episode_id,
        parent_agent_id,
        child_id,
        capability_digest(parent_capability),
        capability_digest(child_capability),
    )


def runtime_digest(agent: Any) -> str:
    system = str(getattr(agent, "system", "") or "")
    if len(system.encode("utf-8")) > MAX_MESSAGES_BYTES:
        raise ChildDurabilityError("child runtime prompt exceeds its boundary")
    return _digest_parts(
        "child-runtime-v1",
        getattr(agent, "checkpoint_id", ""),
        getattr(agent, "role", ""),
        getattr(agent, "depth", ""),
        getattr(agent, "model", ""),
        getattr(agent, "max_steps", ""),
        hashlib.sha256(system.encode("utf-8")).hexdigest(),
        capability_digest(getattr(agent, "capability", None)),
    )


def _args_digest(args: dict) -> str:
    return hashlib.sha256(
        _canonical_json(
            args,
            label="spawn arguments",
            max_bytes=MAX_TASK_BYTES * 2,
        ).encode("utf-8")
    ).hexdigest()


def _operation_identity(
    invocation: ToolInvocation,
    tool_name: str,
    args: dict,
) -> OperationIdentity:
    if tool_name not in _SPAWN_TOOLS:
        raise ChildDurabilityError("child operation tool is invalid")
    args_digest = _args_digest(args)
    invocation_id = _digest_parts(
        "child-operation-v1",
        invocation.tenant_id,
        invocation.goal_id,
        invocation.episode_id,
        invocation.parent_agent_id,
        invocation.parent_step,
        invocation.tool_ordinal,
        tool_name,
        args_digest,
    )
    return OperationIdentity(
        tenant_id=invocation.tenant_id,
        goal_id=invocation.goal_id,
        episode_id=invocation.episode_id,
        parent_agent_id=invocation.parent_agent_id,
        parent_step=invocation.parent_step,
        tool_ordinal=invocation.tool_ordinal,
        tool_name=tool_name,
        args_digest=args_digest,
        invocation_id=invocation_id,
    )


@contextlib.contextmanager
def bind_tool_invocation(
    agent: Any,
    step: int,
    ordinal: int,
) -> Iterator[None]:
    """Bind the deterministic parent turn/ordinal around one tool dispatch."""
    if isinstance(step, bool) or not isinstance(step, int) or step < 0:
        raise ChildDurabilityError("tool invocation step is invalid")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ChildDurabilityError("tool invocation ordinal is invalid")
    tenant, goal_id, episode_id = _run_binding(agent.ctx)
    token = _CURRENT_INVOCATION.set(
        ToolInvocation(
            tenant_id=tenant,
            goal_id=goal_id,
            episode_id=episode_id,
            parent_agent_id=_agent_identity(agent),
            parent_step=step,
            tool_ordinal=ordinal,
        )
    )
    try:
        yield
    finally:
        _CURRENT_INVOCATION.reset(token)


def _operation_binding_digest(identity: OperationIdentity, mode: str, count: int) -> str:
    return _digest_parts(
        "child-operation-binding-v1",
        SCHEMA_VERSION,
        identity.tenant_id,
        identity.goal_id,
        identity.episode_id,
        identity.invocation_id,
        identity.parent_agent_id,
        identity.parent_step,
        identity.tool_ordinal,
        identity.tool_name,
        identity.args_digest,
        mode,
        count,
    )


def _child_binding_digest(
    identity: OperationIdentity,
    index: int,
    descriptor: ChildDescriptor,
) -> str:
    return _digest_parts(
        "child-agent-binding-v1",
        SCHEMA_VERSION,
        identity.tenant_id,
        identity.goal_id,
        identity.episode_id,
        identity.invocation_id,
        index,
        descriptor.child_id,
        descriptor.role,
        descriptor.depth,
        hashlib.sha256(descriptor.task.encode("utf-8")).hexdigest(),
        descriptor.domain,
        descriptor.capability_digest,
        descriptor.authority_digest,
    )


def _budget_payload(budget: Any, spawns_used: int) -> dict[str, Any]:
    try:
        lock = getattr(budget, "_lock", None)
        if lock is None:
            payload = snapshot_budget(budget)
            reserved = getattr(budget, "_reserved", 0.0)
        else:
            with lock:
                payload = snapshot_budget(budget)
                reserved = getattr(budget, "_reserved", 0.0)
    except Exception as exc:
        raise ChildDurabilityError("child budget snapshot failed") from exc
    try:
        reserved_f = float(reserved or 0.0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ChildDurabilityError("child budget reservation is invalid") from exc
    if not math.isfinite(reserved_f) or reserved_f < 0:
        raise ChildDurabilityError("child budget reservation is invalid")
    payload["_reserved"] = reserved_f
    payload["_spawns_used"] = max(0, int(spawns_used))
    return payload


def _restore_budget_floor(ctx: Any, snapshot: dict[str, Any]) -> None:
    budget = ctx.budget
    counters = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "dollars",
        "tool_calls",
    )
    try:
        lock = getattr(budget, "_lock", None)
        cm = lock if lock is not None else contextlib.nullcontext()
        with cm:
            for field in counters:
                saved = snapshot.get(field)
                if saved is None:
                    continue
                current = getattr(budget, field)
                setattr(budget, field, max(current, saved))
            elapsed = max(float(snapshot.get("_elapsed") or 0.0), budget.elapsed())
            budget._started_monotonic = time.monotonic() - max(0.0, elapsed)
            budget.started_at = time.time() - max(0.0, elapsed)
            saved_reserved = float(snapshot.get("_reserved") or 0.0)
            if not math.isfinite(saved_reserved) or saved_reserved < 0:
                raise ValueError("invalid reservation")
            # Preserve the observation in the checkpoint, but do not recreate
            # an aggregate provider-call hold in a fresh process.  A child that
            # actually died with a call in flight is ``turn_pending`` and is
            # refused as indeterminate before recovery.  At a clean child
            # boundary this value may merely have belonged to a concurrently
            # running sibling; restoring it would create a permanent phantom
            # hold with no owner able to release it.
            budget.check()
        ctx._spawns_used = max(
            int(getattr(ctx, "_spawns_used", 0) or 0),
            int(snapshot.get("_spawns_used") or 0),
        )
    except Exception as exc:
        raise ChildDurabilityError("child budget checkpoint is invalid") from exc


def _result_payload(result: Any) -> dict[str, Any]:
    payload = {
        "final": getattr(result, "final", None),
        "blocked_on_user": bool(getattr(result, "blocked_on_user", False)),
        "error": getattr(result, "error", None),
        "role": str(getattr(result, "role", "") or ""),
        "name": str(getattr(result, "name", "") or ""),
        "verifier_confidence": float(
            getattr(result, "verifier_confidence", 1.0) or 0.0
        ),
        "verifier_critique": str(
            getattr(result, "verifier_critique", "") or ""
        ),
        "final_patch": getattr(result, "final_patch", None),
    }
    _canonical_json(payload, label="child result", max_bytes=MAX_RESULT_BYTES)
    return payload


def result_from_payload(payload: dict[str, Any]):
    from .agent import AgentResult

    expected = {
        "final",
        "blocked_on_user",
        "error",
        "role",
        "name",
        "verifier_confidence",
        "verifier_critique",
        "final_patch",
    }
    if set(payload) != expected:
        raise ChildDurabilityError("child result checkpoint schema is invalid")
    return AgentResult(**payload)


class ChildCheckpointStore:
    """Transactional child-operation/checkpoint store over one WorldModel."""

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self.world = ctx.world
        self.tenant_id, self.goal_id, self.episode_id = _run_binding(ctx)
        self.strict = root_checkpoint_required()
        self._ready = False

    def _ensure(self) -> bool:
        if self._ready:
            return True
        try:
            with self.world._writing() as conn:
                conn.execute(_CREATE_OPERATIONS)
                conn.execute(_CREATE_CHILDREN)
                conn.execute(_CREATE_PARENT_INDEX)
                conn.execute(_CREATE_RESERVATION_INDEX)
            self._ready = True
            return True
        except Exception as exc:
            if self.strict:
                raise ChildDurabilityError(
                    "child checkpoint table initialization failed"
                ) from exc
            return False

    def _reservation_count(self, conn: Any) -> int:
        row = conn.execute(
            "SELECT COUNT(*) FROM child_agent_checkpoints "
            "WHERE tenant_id = ? AND goal_id = ? AND episode_id = ? "
            "AND reservation_state != 'released'",
            (self.tenant_id, self.goal_id, self.episode_id),
        ).fetchone()
        return int(row[0] if row else 0)

    def restore_reservation_floor(self) -> int:
        if not self._ensure():
            return int(getattr(self.ctx, "_spawns_used", 0) or 0)
        try:
            with self.world._writing() as conn:
                count = self._reservation_count(conn)
            self.ctx._spawns_used = max(
                int(getattr(self.ctx, "_spawns_used", 0) or 0), count
            )
            return count
        except Exception as exc:
            if self.strict:
                raise ChildDurabilityError(
                    "child spawn reservation recovery failed"
                ) from exc
            return int(getattr(self.ctx, "_spawns_used", 0) or 0)

    def claim_operation(
        self,
        identity: OperationIdentity,
        mode: str,
        descriptors: Sequence[ChildDescriptor],
        max_total_spawns: int,
    ) -> OperationSnapshot:
        if not self._ensure():
            raise ChildDurabilityError("child checkpoint store is unavailable")
        if mode not in {"subagent", "swarm", "specialist"}:
            raise ChildDurabilityError("child operation mode is invalid")
        if not descriptors:
            raise ChildDurabilityError("child operation has no children")
        count = len(descriptors)
        expected_binding = _operation_binding_digest(identity, mode, count)
        now = time.time()
        try:
            with self.world._writing() as conn:
                existing = conn.execute(
                    "SELECT parent_agent_id, parent_step, tool_ordinal, tool_name, "
                    "args_digest, mode, status, expected_children, result, "
                    "binding_digest FROM child_operations "
                    "WHERE tenant_id = ? AND goal_id = ? AND episode_id = ? "
                    "AND invocation_id = ?",
                    (
                        self.tenant_id,
                        self.goal_id,
                        self.episode_id,
                        identity.invocation_id,
                    ),
                ).fetchone()
                if existing is not None:
                    observed = (
                        existing[0],
                        int(existing[1]),
                        int(existing[2]),
                        existing[3],
                        existing[4],
                        existing[5],
                        int(existing[7]),
                        existing[9],
                    )
                    expected = (
                        identity.parent_agent_id,
                        identity.parent_step,
                        identity.tool_ordinal,
                        identity.tool_name,
                        identity.args_digest,
                        mode,
                        count,
                        expected_binding,
                    )
                    if observed != expected:
                        raise ChildDurabilityError(
                            "child operation binding does not match its checkpoint"
                        )
                    self._verify_children(conn, identity, descriptors)
                    reserved = self._reservation_count(conn)
                    self.ctx._spawns_used = max(
                        int(getattr(self.ctx, "_spawns_used", 0) or 0), reserved
                    )
                    return OperationSnapshot(
                        status=str(existing[6]),
                        result=str(existing[8]) if existing[8] else None,
                        expected_children=count,
                    )

                reserved = self._reservation_count(conn)
                if reserved + count > max(0, int(max_total_spawns)):
                    raise DurableSpawnCapReached(
                        "durable per-goal spawn reservation cap reached"
                    )
                conn.execute(
                    "INSERT INTO child_operations("
                    "tenant_id, goal_id, episode_id, invocation_id, "
                    "parent_agent_id, parent_step, tool_ordinal, tool_name, "
                    "args_digest, mode, status, expected_children, "
                    "reservation_units, result, owner_token, binding_digest, "
                    "created_at, updated_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, '', ?, ?, ?, ?)",
                    (
                        self.tenant_id,
                        self.goal_id,
                        self.episode_id,
                        identity.invocation_id,
                        identity.parent_agent_id,
                        identity.parent_step,
                        identity.tool_ordinal,
                        identity.tool_name,
                        identity.args_digest,
                        mode,
                        count,
                        count,
                        _BOOT_ID,
                        expected_binding,
                        now,
                        now,
                    ),
                )
                for index, descriptor in enumerate(descriptors):
                    self._validate_descriptor(descriptor)
                    conn.execute(
                        "INSERT INTO child_agent_checkpoints("
                        "tenant_id, goal_id, episode_id, invocation_id, "
                        "child_index, child_id, role, depth, task_digest, "
                        "domain_name, capability_digest, authority_digest, "
                        "runtime_digest, status, next_step, messages, budget, "
                        "result, reservation_state, owner_token, binding_digest, "
                        "created_at, updated_at) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', "
                        "'planned', 0, '[]', '{}', '', 'held', ?, ?, ?, ?)",
                        (
                            self.tenant_id,
                            self.goal_id,
                            self.episode_id,
                            identity.invocation_id,
                            index,
                            descriptor.child_id,
                            descriptor.role,
                            descriptor.depth,
                            hashlib.sha256(descriptor.task.encode("utf-8")).hexdigest(),
                            descriptor.domain,
                            descriptor.capability_digest,
                            descriptor.authority_digest,
                            _BOOT_ID,
                            _child_binding_digest(identity, index, descriptor),
                            now,
                            now,
                        ),
                    )
                self.ctx._spawns_used = max(
                    int(getattr(self.ctx, "_spawns_used", 0) or 0), reserved + count
                )
                return OperationSnapshot("running", None, count)
        except ChildDurabilityError:
            raise
        except Exception as exc:
            raise ChildDurabilityError("child operation claim failed") from exc

    @staticmethod
    def _validate_descriptor(descriptor: ChildDescriptor) -> None:
        if (
            not descriptor.child_id
            or len(descriptor.child_id.encode("utf-8")) > MAX_AGENT_ID_BYTES
        ):
            raise ChildDurabilityError("stable child identity is invalid")
        if (
            not descriptor.role
            or len(descriptor.role.encode("utf-8")) > MAX_ROLE_BYTES
        ):
            raise ChildDurabilityError("child role is invalid")
        if len(descriptor.task.encode("utf-8")) > MAX_TASK_BYTES:
            raise ChildDurabilityError("child task exceeds its durability boundary")
        if descriptor.depth < 1:
            raise ChildDurabilityError("child depth is invalid")
        for value in (descriptor.capability_digest, descriptor.authority_digest):
            if len(value) != 64:
                raise ChildDurabilityError("child authority digest is invalid")

    def _verify_children(
        self,
        conn: Any,
        identity: OperationIdentity,
        descriptors: Sequence[ChildDescriptor],
    ) -> None:
        rows = conn.execute(
            "SELECT child_index, child_id, role, depth, task_digest, domain_name, "
            "capability_digest, authority_digest, binding_digest "
            "FROM child_agent_checkpoints WHERE tenant_id = ? AND goal_id = ? "
            "AND episode_id = ? AND invocation_id = ? ORDER BY child_index",
            (
                self.tenant_id,
                self.goal_id,
                self.episode_id,
                identity.invocation_id,
            ),
        ).fetchall()
        if len(rows) != len(descriptors):
            raise ChildDurabilityError("child checkpoint set is incomplete")
        for index, (row, descriptor) in enumerate(zip(rows, descriptors, strict=True)):
            self._validate_descriptor(descriptor)
            observed = (
                int(row[0]),
                row[1],
                row[2],
                int(row[3]),
                row[4],
                row[5],
                row[6],
                row[7],
                row[8],
            )
            expected = (
                index,
                descriptor.child_id,
                descriptor.role,
                descriptor.depth,
                hashlib.sha256(descriptor.task.encode("utf-8")).hexdigest(),
                descriptor.domain,
                descriptor.capability_digest,
                descriptor.authority_digest,
                _child_binding_digest(identity, index, descriptor),
            )
            if observed != expected:
                raise ChildDurabilityError(
                    "child identity or authority changed across recovery"
                )

    def bind_runtime(
        self,
        identity: OperationIdentity,
        index: int,
        agent: Any,
    ) -> DurableChildRuntime:
        digest = runtime_digest(agent)
        try:
            with self.world._writing() as conn:
                row = conn.execute(
                    "SELECT child_id, runtime_digest, status, next_step, messages, "
                    "budget, result, owner_token, binding_digest "
                    "FROM child_agent_checkpoints WHERE tenant_id = ? AND goal_id = ? "
                    "AND episode_id = ? AND invocation_id = ? AND child_index = ?",
                    (
                        self.tenant_id,
                        self.goal_id,
                        self.episode_id,
                        identity.invocation_id,
                        index,
                    ),
                ).fetchone()
                if row is None:
                    raise ChildDurabilityError("child checkpoint is missing")
                if row[0] != getattr(agent, "checkpoint_id", None):
                    raise ChildDurabilityError("stable child identity changed")
                prior_digest = str(row[1] or "")
                if prior_digest and not hmac.compare_digest(prior_digest, digest):
                    raise ChildDurabilityError(
                        "child runtime changed across checkpoint recovery"
                    )
                if not prior_digest:
                    conn.execute(
                        "UPDATE child_agent_checkpoints SET runtime_digest = ?, "
                        "updated_at = ? WHERE tenant_id = ? AND goal_id = ? "
                        "AND episode_id = ? AND invocation_id = ? AND child_index = ?",
                        (
                            digest,
                            time.time(),
                            self.tenant_id,
                            self.goal_id,
                            self.episode_id,
                            identity.invocation_id,
                            index,
                        ),
                    )
                snapshot = self._snapshot_from_row(row, digest)
        except ChildDurabilityError:
            raise
        except Exception as exc:
            raise ChildDurabilityError("child runtime binding failed") from exc
        runtime = DurableChildRuntime(self, identity, index, snapshot)
        runtime.attach(agent)
        return runtime

    @staticmethod
    def _snapshot_from_row(row: Sequence[Any], runtime_digest_value: str) -> ChildSnapshot:
        try:
            messages = json.loads(row[4])
            budget = json.loads(row[5])
            result = json.loads(row[6]) if row[6] else None
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ChildDurabilityError("child checkpoint payload is corrupt") from exc
        if not isinstance(messages, list) or not isinstance(budget, dict):
            raise ChildDurabilityError("child checkpoint payload schema is invalid")
        if result is not None and not isinstance(result, dict):
            raise ChildDurabilityError("child result checkpoint is invalid")
        return ChildSnapshot(
            child_id=str(row[0]),
            runtime_digest=runtime_digest_value,
            status=str(row[2]),
            next_step=int(row[3]),
            messages=messages,
            budget=budget,
            result=result,
            owner_token=str(row[7]),
        )

    def save_child(
        self,
        identity: OperationIdentity,
        index: int,
        *,
        status: str,
        next_step: int,
        messages: list[dict],
        budget: Any,
        result: dict[str, Any] | None = None,
    ) -> None:
        if status not in {"active", "turn_pending", "completed", "indeterminate"}:
            raise ChildDurabilityError("child checkpoint status is invalid")
        messages_json = _canonical_json(
            messages,
            label="child messages",
            max_bytes=MAX_MESSAGES_BYTES,
        )
        budget_json = _canonical_json(
            _budget_payload(budget, getattr(self.ctx, "_spawns_used", 0)),
            label="child budget",
            max_bytes=64 * 1024,
        )
        result_json = (
            _canonical_json(result, label="child result", max_bytes=MAX_RESULT_BYTES)
            if result is not None
            else ""
        )
        try:
            with self.world._writing() as conn:
                cur = conn.execute(
                    "UPDATE child_agent_checkpoints SET status = ?, next_step = ?, "
                    "messages = ?, budget = ?, result = ?, "
                    "reservation_state = CASE WHEN ? = 'completed' "
                    "THEN 'consumed' ELSE reservation_state END, "
                    "owner_token = ?, updated_at = ? "
                    "WHERE tenant_id = ? AND goal_id = ? AND episode_id = ? "
                    "AND invocation_id = ? AND child_index = ?",
                    (
                        status,
                        next_step,
                        messages_json,
                        budget_json,
                        result_json,
                        status,
                        _BOOT_ID,
                        time.time(),
                        self.tenant_id,
                        self.goal_id,
                        self.episode_id,
                        identity.invocation_id,
                        index,
                    ),
                )
                if int(cur.rowcount or 0) != 1:
                    raise ChildDurabilityError("child checkpoint update was not committed")
        except ChildDurabilityError:
            raise
        except Exception as exc:
            raise ChildDurabilityError("child checkpoint save failed") from exc

    def mark_operation(self, identity: OperationIdentity, status: str, result: str = "") -> None:
        if status not in {"running", "joining", "completed", "indeterminate"}:
            raise ChildDurabilityError("child operation status is invalid")
        if result:
            _canonical_json(result, label="child operation result", max_bytes=MAX_RESULT_BYTES)
        try:
            with self.world._writing() as conn:
                cur = conn.execute(
                    "UPDATE child_operations SET status = ?, result = ?, "
                    "owner_token = ?, updated_at = ? WHERE tenant_id = ? "
                    "AND goal_id = ? AND episode_id = ? AND invocation_id = ?",
                    (
                        status,
                        result,
                        _BOOT_ID,
                        time.time(),
                        self.tenant_id,
                        self.goal_id,
                        self.episode_id,
                        identity.invocation_id,
                    ),
                )
                if int(cur.rowcount or 0) != 1:
                    raise ChildDurabilityError("child operation update was not committed")
        except ChildDurabilityError:
            raise
        except Exception as exc:
            raise ChildDurabilityError("child operation save failed") from exc

    def release_unstarted(self, identity: OperationIdentity) -> None:
        try:
            with self.world._writing() as conn:
                rows = conn.execute(
                    "SELECT status FROM child_agent_checkpoints WHERE tenant_id = ? "
                    "AND goal_id = ? AND episode_id = ? AND invocation_id = ?",
                    (
                        self.tenant_id,
                        self.goal_id,
                        self.episode_id,
                        identity.invocation_id,
                    ),
                ).fetchall()
                if any(str(row[0]) not in {"planned"} for row in rows):
                    raise ChildDurabilityError(
                        "cannot release a child reservation after execution started"
                    )
                conn.execute(
                    "UPDATE child_agent_checkpoints SET status = 'indeterminate', "
                    "reservation_state = 'released', updated_at = ? "
                    "WHERE tenant_id = ? AND goal_id = ? AND episode_id = ? "
                    "AND invocation_id = ?",
                    (
                        time.time(),
                        self.tenant_id,
                        self.goal_id,
                        self.episode_id,
                        identity.invocation_id,
                    ),
                )
                conn.execute(
                    "UPDATE child_operations SET status = 'released', "
                    "reservation_units = 0, updated_at = ? WHERE tenant_id = ? "
                    "AND goal_id = ? AND episode_id = ? AND invocation_id = ?",
                    (
                        time.time(),
                        self.tenant_id,
                        self.goal_id,
                        self.episode_id,
                        identity.invocation_id,
                    ),
                )
                count = self._reservation_count(conn)
            self.ctx._spawns_used = count
        except ChildDurabilityError:
            raise
        except Exception as exc:
            raise ChildDurabilityError("child reservation release failed") from exc

    def outstanding_for_parent(self, parent_agent_id: str) -> list[tuple]:
        if not self._ensure():
            return []
        try:
            return list(
                self.world._read_all(
                    "SELECT parent_step, tool_ordinal, tool_name, args_digest, "
                    "invocation_id, status FROM child_operations "
                    "WHERE tenant_id = ? AND goal_id = ? AND episode_id = ? "
                    "AND parent_agent_id = ? AND status NOT IN ('joined', 'released') "
                    "ORDER BY parent_step, tool_ordinal",
                    (
                        self.tenant_id,
                        self.goal_id,
                        self.episode_id,
                        parent_agent_id,
                    ),
                )
            )
        except Exception as exc:
            if self.strict:
                raise ChildDurabilityError("child operation recovery lookup failed") from exc
            return []

    def acknowledge_before_step(self, parent_agent_id: str, step: int) -> None:
        if not self._ensure():
            return
        try:
            with self.world._writing() as conn:
                unresolved = conn.execute(
                    "SELECT status FROM child_operations WHERE tenant_id = ? "
                    "AND goal_id = ? AND episode_id = ? AND parent_agent_id = ? "
                    "AND parent_step < ? AND status NOT IN "
                    "('completed', 'joined', 'released') LIMIT 1",
                    (
                        self.tenant_id,
                        self.goal_id,
                        self.episode_id,
                        parent_agent_id,
                        step,
                    ),
                ).fetchone()
                if unresolved is not None:
                    raise ChildIndeterminateError(
                        "an earlier child operation has no committed join result"
                    )
                conn.execute(
                    "UPDATE child_operations SET status = 'joined', updated_at = ? "
                    "WHERE tenant_id = ? AND goal_id = ? AND episode_id = ? "
                    "AND parent_agent_id = ? AND parent_step < ? "
                    "AND status = 'completed'",
                    (
                        time.time(),
                        self.tenant_id,
                        self.goal_id,
                        self.episode_id,
                        parent_agent_id,
                        step,
                    ),
                )
        except ChildDurabilityError:
            raise
        except Exception as exc:
            if self.strict:
                raise ChildDurabilityError("child join acknowledgement failed") from exc

    def clear_goal(self) -> None:
        if not self._ensure():
            return
        try:
            with self.world._writing() as conn:
                conn.execute(
                    "DELETE FROM child_agent_checkpoints WHERE tenant_id = ? "
                    "AND goal_id = ?",
                    (self.tenant_id, self.goal_id),
                )
                conn.execute(
                    "DELETE FROM child_operations WHERE tenant_id = ? AND goal_id = ?",
                    (self.tenant_id, self.goal_id),
                )
        except Exception as exc:
            if self.strict:
                raise ChildDurabilityError("child checkpoint finalization failed") from exc


class DurableChildRuntime:
    """One reconstructed child's turn-boundary persistence handle."""

    def __init__(
        self,
        store: ChildCheckpointStore,
        identity: OperationIdentity,
        index: int,
        snapshot: ChildSnapshot,
    ):
        self.store = store
        self.identity = identity
        self.index = index
        self.snapshot = snapshot
        self.agent: Any | None = None

    def attach(self, agent: Any) -> None:
        if self.snapshot.status == "turn_pending" and self.snapshot.owner_token != _BOOT_ID:
            raise ChildIndeterminateError(
                "child crashed during an uncommitted model/tool boundary"
            )
        if self.snapshot.status == "indeterminate":
            raise ChildIndeterminateError("child checkpoint is indeterminate")
        _restore_budget_floor(self.store.ctx, self.snapshot.budget)
        self.agent = agent
        agent._durable_child_runtime = self
        if self.snapshot.status == "completed":
            return
        if self.snapshot.messages:
            agent._durable_child_resume_messages = self.snapshot.messages
            agent._durable_child_resume_step = self.snapshot.next_step

    @property
    def completed_result(self):
        if self.snapshot.status != "completed":
            return None
        if self.snapshot.result is None:
            raise ChildDurabilityError("completed child has no durable result")
        return result_from_payload(self.snapshot.result)

    def checkpoint_turn(self, step: int, messages: list[dict], budget: Any) -> None:
        self.store.save_child(
            self.identity,
            self.index,
            status="active",
            next_step=step,
            messages=messages,
            budget=budget,
        )

    def begin_turn(self, step: int, messages: list[dict], budget: Any) -> None:
        self.store.save_child(
            self.identity,
            self.index,
            status="turn_pending",
            next_step=step,
            messages=messages,
            budget=budget,
        )

    def finish_turn(self, next_step: int, messages: list[dict], budget: Any) -> None:
        self.checkpoint_turn(next_step, messages, budget)

    def complete(self, result: Any, messages: list[dict], budget: Any) -> None:
        payload = _result_payload(result)
        self.store.save_child(
            self.identity,
            self.index,
            status="completed",
            next_step=max(0, int(getattr(self.agent, "max_steps", 0) or 0)),
            messages=messages,
            budget=budget,
            result=payload,
        )
        self.snapshot = ChildSnapshot(
            child_id=self.snapshot.child_id,
            status="completed",
            next_step=self.snapshot.next_step,
            messages=messages,
            budget=_budget_payload(
                budget, getattr(self.store.ctx, "_spawns_used", 0)
            ),
            result=payload,
            owner_token=_BOOT_ID,
            runtime_digest=self.snapshot.runtime_digest,
        )

    def indeterminate(self, messages: list[dict], budget: Any) -> None:
        self.store.save_child(
            self.identity,
            self.index,
            status="indeterminate",
            next_step=max(0, int(getattr(self.agent, "_durable_child_resume_step", 0) or 0)),
            messages=messages,
            budget=budget,
        )


class DurableSpawnOperation:
    """Prepared deterministic spawn operation bound to the current tool call."""

    def __init__(self, parent: Any, identity: OperationIdentity, mode: str):
        self.parent = parent
        self.identity = identity
        self.mode = mode
        self.store = ChildCheckpointStore(parent.ctx)
        self.snapshot: OperationSnapshot | None = None

    def child_id(self, index: int) -> str:
        return "child-" + _digest_parts(
            "stable-child-v1", self.identity.invocation_id, index
        )[:32]

    def claim(self, descriptors: Sequence[ChildDescriptor]) -> OperationSnapshot:
        self.snapshot = self.store.claim_operation(
            self.identity,
            self.mode,
            descriptors,
            getattr(self.parent.ctx, "max_total_spawns", 0),
        )
        return self.snapshot

    @property
    def completed_result(self) -> str | None:
        if self.snapshot is None:
            raise ChildDurabilityError("child operation has not been claimed")
        if self.snapshot.status in {"completed", "joined"}:
            if self.snapshot.result is None:
                raise ChildDurabilityError(
                    "completed child operation has no durable result"
                )
            return self.snapshot.result
        if self.snapshot.status == "indeterminate":
            raise ChildIndeterminateError("child operation is indeterminate")
        if self.snapshot.status == "released":
            raise ChildDurabilityError("child operation reservation was released")
        return None

    def bind(self, index: int, agent: Any) -> DurableChildRuntime:
        return self.store.bind_runtime(self.identity, index, agent)

    def mark_joining(self) -> None:
        self.store.mark_operation(self.identity, "joining")

    def complete(self, result: str) -> None:
        self.store.mark_operation(self.identity, "completed", result)
        self.snapshot = OperationSnapshot(
            "completed",
            result,
            self.snapshot.expected_children if self.snapshot else 0,
        )

    def indeterminate(self) -> None:
        self.store.mark_operation(self.identity, "indeterminate")

    def release_unstarted(self) -> None:
        self.store.release_unstarted(self.identity)


def prepare_spawn_operation(
    parent: Any,
    tool_name: str,
    args: dict,
    mode: str,
) -> DurableSpawnOperation | None:
    if not active(parent.ctx):
        return None
    invocation = _CURRENT_INVOCATION.get()
    if invocation is None:
        raise ChildDurabilityError(
            "durable spawn requires a bound parent turn and tool ordinal"
        )
    expected_binding = _run_binding(parent.ctx)
    if (
        invocation.tenant_id,
        invocation.goal_id,
        invocation.episode_id,
    ) != expected_binding or invocation.parent_agent_id != _agent_identity(parent):
        raise ChildDurabilityError("spawn invocation crossed its run boundary")
    return DurableSpawnOperation(
        parent,
        _operation_identity(invocation, tool_name, args),
        mode,
    )


def descriptor_for(
    operation: DurableSpawnOperation,
    index: int,
    *,
    role: str,
    depth: int,
    task: str,
    domain: str | None,
    parent_capability: Any,
    child_capability: Any,
) -> ChildDescriptor:
    child_id = operation.child_id(index)
    role = str(role)
    task = str(task)
    tenant = operation.identity.tenant_id
    return ChildDescriptor(
        child_id=child_id,
        role=role,
        depth=depth,
        task=task,
        domain=str(domain or ""),
        capability_digest=capability_digest(child_capability),
        authority_digest=authority_digest(
            parent_capability=parent_capability,
            child_capability=child_capability,
            tenant_id=tenant,
            goal_id=operation.identity.goal_id,
            episode_id=operation.identity.episode_id,
            parent_agent_id=operation.identity.parent_agent_id,
            child_id=child_id,
        ),
    )


def acknowledge_parent_checkpoint(agent: Any, step: int) -> None:
    if not active(agent.ctx):
        return
    ChildCheckpointStore(agent.ctx).acknowledge_before_step(
        _agent_identity(agent), step
    )


def validate_replayed_operations(agent: Any, step: int, tool_calls: Sequence[Any]) -> None:
    """Refuse a resumed parent turn that drops or changes prior child work."""
    if not active(agent.ctx):
        return
    store = ChildCheckpointStore(agent.ctx)
    parent_id = _agent_identity(agent)
    outstanding = store.outstanding_for_parent(parent_id)
    if not outstanding:
        return
    tenant, goal_id, episode_id = _run_binding(agent.ctx)
    replayed: dict[str, OperationIdentity] = {}
    for ordinal, call in enumerate(tool_calls):
        name = getattr(call, "name", "")
        if name not in _SPAWN_TOOLS:
            continue
        args = getattr(call, "input", None)
        if not isinstance(args, dict):
            raise ChildDurabilityError("replayed spawn arguments are invalid")
        identity = _operation_identity(
            ToolInvocation(
                tenant_id=tenant,
                goal_id=goal_id,
                episode_id=episode_id,
                parent_agent_id=parent_id,
                parent_step=step,
                tool_ordinal=ordinal,
            ),
            name,
            args,
        )
        replayed[identity.invocation_id] = identity

    for row in outstanding:
        parent_step, ordinal, name, args_digest_value, invocation_id, _status = row
        if int(parent_step) != step:
            raise ChildIndeterminateError(
                "an unjoined child operation belongs to another parent step"
            )
        identity = replayed.get(str(invocation_id))
        if (
            identity is None
            or identity.tool_ordinal != int(ordinal)
            or identity.tool_name != str(name)
            or not hmac.compare_digest(identity.args_digest, str(args_digest_value))
        ):
            raise ChildIndeterminateError(
                "resumed parent turn changed or omitted durable child work"
            )


def clear_goal(ctx: Any) -> None:
    if active(ctx):
        ChildCheckpointStore(ctx).clear_goal()


__all__ = [
    "ChildDescriptor",
    "ChildDurabilityError",
    "ChildIndeterminateError",
    "DurableChildRuntime",
    "DurableSpawnCapReached",
    "DurableSpawnOperation",
    "acknowledge_parent_checkpoint",
    "active",
    "authority_digest",
    "bind_tool_invocation",
    "capability_digest",
    "clear_goal",
    "descriptor_for",
    "prepare_spawn_operation",
    "result_from_payload",
    "runtime_digest",
    "validate_replayed_operations",
]
