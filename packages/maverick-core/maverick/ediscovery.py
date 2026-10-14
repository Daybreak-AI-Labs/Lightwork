"""Matter-scoped legal holds for eDiscovery.

A matter names custodians (principal ids), an optional date range and the
record kinds it preserves: goals and their runs, chat conversations,
attachments and approvals.  While a matter is active:

* every broad retention purge is refused for the whole workspace, because the
  stores cannot reliably map each record to a custodian;
* erasing a conversation closure is refused when that closure contains an
  in-scope record, with a plain message that names no matter.  Records outside
  the scope (other people, or dates outside the range) remain erasable.

The capability is off unless ``[ediscovery] enable = true``.  A matter recorded
while enabled keeps binding after the switch is turned off, so a hold cannot be
silently dropped by a configuration change.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import re
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .file_lock import ensure_private_directory

RECORD_KINDS = ("goals", "chats", "attachments", "approvals")
STATUSES = ("active", "released")
MATTER_SCHEMA = "lightwork.ediscovery-matter.v1"
LEGAL_HOLD_MESSAGE = (
    "A legal hold applies to some of these records, so they cannot be erased. "
    "Ask your records administrator before trying again."
)
BROAD_PURGE_MESSAGE = (
    "A legal hold is active in this workspace, so bulk deletion of old records is "
    "paused. Ask your records administrator to review the hold before trying again."
)
MAX_CUSTODIANS = 500
MAX_NAME = 200
MAX_DESCRIPTION = 2_000
MAX_SCOPE_ROWS = 100_000
_DAY = 86_400
_ID_RE = re.compile(r"mtr-[0-9a-f]{16}\Z")
_PRINCIPAL_RE = re.compile(r"[^\s\x00-\x1f\x7f]{1,256}\Z")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


class EdiscoveryError(RuntimeError):
    """Base class for matter failures that are safe to show to an operator."""


class EdiscoveryDisabled(EdiscoveryError):
    pass


class MatterValidationError(EdiscoveryError, ValueError):
    pass


class MatterNotFound(EdiscoveryError):
    pass


class MatterConflict(EdiscoveryError):
    pass


class MatterScopeUnverifiable(EdiscoveryError):
    """The records in scope could not be enumerated completely, so none may be erased."""


class LegalHoldApplies(EdiscoveryError):
    """Erasure or bulk deletion is refused by an active legal hold."""


class MatterExportError(EdiscoveryError):
    pass


def enabled() -> bool:
    """True only when ``[ediscovery] enable`` is explicitly true."""
    try:
        from .config import config_source_errors, load_config

        if config_source_errors():
            return False
        config = load_config()
    except Exception:
        return False
    section = config.get("ediscovery") if isinstance(config, Mapping) else None
    return isinstance(section, Mapping) and section.get("enable") is True


def require_enabled() -> None:
    if not enabled():
        raise EdiscoveryDisabled("eDiscovery matters are disabled")


def store_path(tenant: str | None = None) -> Path:
    from .paths import data_dir

    if tenant is None:
        return data_dir("ediscovery", "legal-matters.sqlite3")
    return data_dir("ediscovery", "legal-matters.sqlite3", tenant=tenant)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ---- validation -------------------------------------------------------------

def _text(value: object, label: str, limit: int, *, required: bool) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise MatterValidationError(f"{label} must be text")
    text = value.strip()
    if required and not text:
        raise MatterValidationError(f"{label} is required")
    if len(text) > limit:
        raise MatterValidationError(f"{label} must be {limit} characters or fewer")
    if _CONTROL_RE.search(text):
        raise MatterValidationError(f"{label} contains characters that are not allowed")
    return text


def _date(value: object, label: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise MatterValidationError(f"{label} must be a date in YYYY-MM-DD form")
    try:
        _dt.date.fromisoformat(value)
    except ValueError as exc:
        raise MatterValidationError(f"{label} is not a real calendar date") from exc
    return value


def _custodians(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise MatterValidationError("Add at least one custodian")
    if len(value) > MAX_CUSTODIANS:
        raise MatterValidationError(f"A matter can name at most {MAX_CUSTODIANS} custodians")
    found: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not _PRINCIPAL_RE.fullmatch(item.strip()):
            raise MatterValidationError("Each custodian must be a principal identifier without spaces")
        found.add(item.strip())
    return sorted(found)


def _kinds(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise MatterValidationError("Choose at least one kind of record")
    if any(not isinstance(item, str) or item not in RECORD_KINDS for item in value):
        raise MatterValidationError("Choose only the listed kinds of record")
    if len(set(value)) != len(value):
        raise MatterValidationError("Each kind of record can be chosen once")
    return sorted(value)


def _day_start(text: str) -> float:
    moment = _dt.datetime.combine(_dt.date.fromisoformat(text), _dt.time(), tzinfo=_dt.timezone.utc)
    return moment.timestamp()


_REQUIRED_FIELDS = ("name", "custodians", "record_kinds")
_EDITABLE_FIELDS = frozenset({"name", "description", "custodians", "date_from", "date_to", "record_kinds"})


def normalize(values: Mapping[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """Validate editable matter fields.  Only keys present in ``values`` are returned,
    so a partial edit never clears a field it did not mention.  Unknown keys are refused."""
    if set(values) - _EDITABLE_FIELDS:
        raise MatterValidationError("Unknown matter field")
    if not partial and any(key not in values for key in _REQUIRED_FIELDS):
        raise MatterValidationError("Name, custodians and record kinds are required")
    out: dict[str, Any] = {}
    if "name" in values:
        out["name"] = _text(values["name"], "Name", MAX_NAME, required=True)
    if "description" in values:
        out["description"] = _text(values["description"], "Description", MAX_DESCRIPTION, required=False)
    if "custodians" in values:
        out["custodians"] = _custodians(values["custodians"])
    if "record_kinds" in values:
        out["record_kinds"] = _kinds(values["record_kinds"])
    if "date_from" in values:
        out["date_from"] = _date(values["date_from"], "Start date")
    if "date_to" in values:
        out["date_to"] = _date(values["date_to"], "End date")
    return out


def _check_range(start: str | None, end: str | None) -> None:
    if start and end and start > end:
        raise MatterValidationError("The start date must be on or before the end date")


def bounds(matter: Mapping[str, Any]) -> tuple[float | None, float | None]:
    """Epoch bounds ``[start, end)`` for a matter's optional date range."""
    start = matter.get("date_from")
    end = matter.get("date_to")
    low = _day_start(start) if start else None
    high = _day_start(end) + _DAY if end else None
    return low, high


def in_range(moment: float, low: float | None, high: float | None) -> bool:
    return (low is None or moment >= low) and (high is None or moment < high)


# ---- durable ledger ---------------------------------------------------------

def _row_payload(row: sqlite3.Row | tuple, tenant: str) -> dict[str, Any]:
    tenant_id, matter_id, status, revision, payload_json, integrity = row
    if integrity != _digest(payload_json):
        raise EdiscoveryError("Legal matter records could not be verified")
    payload = json.loads(payload_json)
    if (payload.get("tenant_id") != tenant or tenant_id != tenant
            or payload.get("id") != matter_id or payload.get("status") != status
            or payload.get("revision") != revision):
        raise EdiscoveryError("Legal matter records could not be verified")
    return payload


class MatterStore:
    """Tenant-scoped matter ledger.  One integrity-checked row per matter revision."""

    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        ensure_private_directory(self.path.parent)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS matters("
                "tenant_id TEXT NOT NULL,matter_id TEXT NOT NULL,status TEXT NOT NULL,"
                "revision INTEGER NOT NULL,payload_json TEXT NOT NULL,"
                "integrity_sha256 TEXT NOT NULL,PRIMARY KEY(tenant_id,matter_id))"
            )

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
        finally:
            connection.close()

    @contextlib.contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")

    @staticmethod
    def _tenant(tenant: object) -> str:
        if not isinstance(tenant, str) or not tenant.strip():
            raise EdiscoveryError("An active workspace is required for legal matters")
        return tenant.strip()

    @staticmethod
    def _matter_id(value: object) -> str:
        if not isinstance(value, str) or not _ID_RE.fullmatch(value):
            raise MatterNotFound("That matter was not found.")
        return value

    def _save(self, connection: sqlite3.Connection, tenant: str, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = _canonical(payload)
        connection.execute(
            "INSERT INTO matters(tenant_id,matter_id,status,revision,payload_json,integrity_sha256) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(tenant_id,matter_id) DO UPDATE SET "
            "status=excluded.status,revision=excluded.revision,payload_json=excluded.payload_json,"
            "integrity_sha256=excluded.integrity_sha256",
            (tenant, payload["id"], payload["status"], payload["revision"], encoded, _digest(encoded)),
        )
        return payload

    def _audit(self, verb: str, *, matter_id: str, actor: str, revision: int, **detail: Any) -> None:
        from .audit import EventKind, audit_event

        written = audit_event(
            EventKind.CONTROL_PLANE_ACTION, agent="ediscovery", verb=verb,
            matter_id=matter_id, actor=actor, revision=revision, **detail,
        )
        if not written:
            raise EdiscoveryError("The change was not saved because its audit record could not be written.")

    def create(self, tenant: object, *, actor: str, values: Mapping[str, Any]) -> dict[str, Any]:
        tenant_id = self._tenant(tenant)
        fields = normalize(values)
        _check_range(fields.get("date_from"), fields.get("date_to"))
        now = float(self._clock())
        matter_id = "mtr-" + secrets.token_hex(8)
        payload = {
            "schema": MATTER_SCHEMA, "id": matter_id, "tenant_id": tenant_id, "revision": 1,
            "status": "active", "name": fields["name"], "description": fields.get("description", ""),
            "custodians": fields["custodians"], "date_from": fields.get("date_from"),
            "date_to": fields.get("date_to"), "record_kinds": fields["record_kinds"],
            "created_by": actor, "created_at": now, "updated_by": actor, "updated_at": now,
            "released_by": None, "released_at": None,
        }
        with self._write() as connection:
            self._audit("ediscovery_matter_created", matter_id=matter_id, actor=actor, revision=1,
                        record_kinds=payload["record_kinds"], custodian_count=len(payload["custodians"]),
                        has_date_range=bool(payload["date_from"] or payload["date_to"]))
            return self._save(connection, tenant_id, payload)

    def update(self, tenant: object, matter_id: object, *, actor: str, expected_revision: int,
               values: Mapping[str, Any]) -> dict[str, Any]:
        tenant_id = self._tenant(tenant)
        matter_id = self._matter_id(matter_id)
        fields = normalize(values, partial=True)
        if not fields:
            raise MatterValidationError("Change at least one field")
        with self._write() as connection:
            current = self._load(connection, tenant_id, matter_id)
            self._check_revision(current, expected_revision)
            payload = {**current, **fields}
            _check_range(payload.get("date_from"), payload.get("date_to"))
            payload.update(revision=current["revision"] + 1, updated_by=actor, updated_at=float(self._clock()))
            self._audit("ediscovery_matter_edited", matter_id=matter_id, actor=actor,
                        revision=payload["revision"], changed=sorted(fields))
            return self._save(connection, tenant_id, payload)

    def release(self, tenant: object, matter_id: object, *, actor: str, expected_revision: int) -> dict[str, Any]:
        tenant_id = self._tenant(tenant)
        matter_id = self._matter_id(matter_id)
        with self._write() as connection:
            current = self._load(connection, tenant_id, matter_id)
            self._check_revision(current, expected_revision)
            now = float(self._clock())
            payload = {**current, "status": "released", "revision": current["revision"] + 1,
                       "updated_by": actor, "updated_at": now, "released_by": actor, "released_at": now}
            self._audit("ediscovery_matter_released", matter_id=matter_id, actor=actor,
                        revision=payload["revision"])
            return self._save(connection, tenant_id, payload)

    @staticmethod
    def _check_revision(current: Mapping[str, Any], expected: object) -> None:
        if type(expected) is not int or expected < 1:
            raise MatterValidationError("expected_revision must be a positive whole number")
        if current["status"] != "active":
            raise MatterConflict("This matter was released and can no longer be changed.")
        if expected != current["revision"]:
            raise MatterConflict("This matter changed. Reload it and review the latest version.")

    @staticmethod
    def _load(connection: sqlite3.Connection, tenant: str, matter_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT tenant_id,matter_id,status,revision,payload_json,integrity_sha256 FROM matters "
            "WHERE tenant_id=? AND matter_id=?",
            (tenant, matter_id),
        ).fetchone()
        if row is None:
            raise MatterNotFound("That matter was not found.")
        return _row_payload(tuple(row), tenant)

    def get(self, tenant: object, matter_id: object) -> dict[str, Any]:
        tenant_id = self._tenant(tenant)
        matter_id = self._matter_id(matter_id)
        with self._connect() as connection:
            return self._load(connection, tenant_id, matter_id)

    def list(self, tenant: object, *, limit: int = 500) -> list[dict[str, Any]]:
        tenant_id = self._tenant(tenant)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT tenant_id,matter_id,status,revision,payload_json,integrity_sha256 FROM matters "
                "WHERE tenant_id=? ORDER BY matter_id LIMIT ?",
                (tenant_id, max(1, min(int(limit), 1_000))),
            ).fetchall()
        return [_row_payload(tuple(row), tenant_id) for row in rows]


def _active_rows(path: Path, tenant: str) -> list[dict[str, Any]]:
    """Read active matters without creating the ledger.  Missing ledger means none."""
    if not path.exists():
        return []
    with contextlib.closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
        # A file without the ledger table was never a matter ledger, so it holds no matters.
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='matters'"
        ).fetchone()
        if table is None:
            return []
        rows = connection.execute(
            "SELECT tenant_id,matter_id,status,revision,payload_json,integrity_sha256 FROM matters "
            "WHERE tenant_id=? AND status='active' ORDER BY matter_id",
            (tenant,),
        ).fetchall()
    return [_row_payload(tuple(row), tenant) for row in rows]


def active_matters() -> list[dict[str, Any]]:
    """Active matters of the current workspace.  Reads even when the feature is
    disabled, so a hold recorded earlier keeps binding.  A missing ledger is empty."""
    from .paths import current_tenant_id

    return _active_rows(store_path(), current_tenant_id() or "")


def active_matter_count() -> int:
    return len(active_matters())


def assert_no_active_matters() -> None:
    """Refuse a broad purge while any matter is active in the current workspace."""
    if active_matter_count():
        raise LegalHoldApplies(BROAD_PURGE_MESSAGE)


# ---- scope --------------------------------------------------------------------

def _bounded(rows: Sequence[Any], label: str) -> list[Any]:
    if len(rows) > MAX_SCOPE_ROWS:
        raise MatterScopeUnverifiable(f"The {label} for this matter exceed the review window")
    return list(rows)


def _custodian_conversations(world: Any, owner: str, cache: dict[str, list[Any]]) -> list[Any]:
    """Conversations of one custodian.  Channel conversations are ``user:<channel>:<id>``;
    dashboard principals are their own user id.  Empty channels never match."""
    if owner.startswith("user:"):
        parts = owner.split(":", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            return []
        channel, user_id = parts[1], parts[2]
    else:
        channel, user_id = "dashboard", owner
    if channel not in cache:
        cache[channel] = list(world.list_conversations(channel))
    return [conv for conv in cache[channel] if conv.user_id == user_id]


@dataclass
class Scope:
    """Every record of one matter that falls inside its custodians, kinds and dates."""

    goals: dict[int, Any] = field(default_factory=dict)
    runs: list[Any] = field(default_factory=list)
    conversations: dict[int, tuple[Any, list[Any]]] = field(default_factory=dict)
    attachments: list[Any] = field(default_factory=list)
    approvals: list[Any] = field(default_factory=list)
    custodian_goal_ids: set[int] = field(default_factory=set)

    def conversation_ids(self) -> set[int]:
        return set(self.conversations)

    def goal_ids(self) -> set[int]:
        """Goals that an erasure would delete: goals, their runs and their attachments."""
        return (set(self.goals) | {int(run.goal_id) for run in self.runs}
                | {int(att.goal_id) for att in self.attachments})


def build_scope(world: Any, matter: Mapping[str, Any], *, for_erasure: bool = False) -> Scope:
    """Enumerate the in-scope records of ``matter``.  Fails closed when incomplete."""
    custodians = list(matter["custodians"])
    kinds = set(matter["record_kinds"])
    low, high = bounds(matter)
    scope = Scope()
    for owner in custodians:
        for goal in _bounded(world.list_goals(owner=owner, limit=MAX_SCOPE_ROWS + 1), "goals"):
            scope.custodian_goal_ids.add(int(goal.id))
            if "goals" in kinds and goal.owner == owner and in_range(float(goal.created_at), low, high):
                scope.goals[int(goal.id)] = goal
    if "goals" in kinds:
        for owner in custodians:
            for run in _bounded(world.list_episodes(limit=MAX_SCOPE_ROWS + 1, owner=owner), "runs"):
                if in_range(float(run.started_at), low, high):
                    scope.runs.append(run)
    if "chats" in kinds:
        cache: dict[str, list[Any]] = {}
        for owner in custodians:
            for conv in _custodian_conversations(world, owner, cache):
                turns = _bounded(world.recent_turns(int(conv.id), limit=MAX_SCOPE_ROWS + 1), "chats")
                kept = [turn for turn in turns if in_range(float(turn.ts), low, high)]
                if kept or (low is None and high is None):
                    scope.conversations[int(conv.id)] = (conv, kept)
    if "attachments" in kinds:
        for goal_id in sorted(scope.custodian_goal_ids):
            for attachment in world.list_attachments(goal_id):
                if in_range(float(attachment.created_at), low, high):
                    scope.attachments.append(attachment)
    if "approvals" in kinds and not for_erasure:
        owners = set(custodians)
        for approval in _bounded(world.list_approvals(limit=MAX_SCOPE_ROWS + 1), "approvals"):
            linked = approval.requested_by in owners or approval.goal_id in scope.custodian_goal_ids
            if linked and in_range(float(approval.requested_at), low, high):
                scope.approvals.append(approval)
    return scope


def assert_erasure_permitted(world: Any, *, conversation_ids: Sequence[int], goal_ids: Sequence[int]) -> None:
    """Refuse an erasure closure that contains any in-scope record of an active matter.

    Approvals are never deleted by an erasure, so they are not part of the closure
    check.  Any failure to enumerate a matter's scope refuses the erasure.
    """
    matters = active_matters()
    if not matters:
        return
    wanted_conversations = {int(item) for item in conversation_ids}
    wanted_goals = {int(item) for item in goal_ids}
    for matter in matters:
        try:
            scope = build_scope(world, matter, for_erasure=True)
        except MatterScopeUnverifiable as exc:
            raise LegalHoldApplies(LEGAL_HOLD_MESSAGE) from exc
        if wanted_conversations & scope.conversation_ids() or wanted_goals & scope.goal_ids():
            raise LegalHoldApplies(LEGAL_HOLD_MESSAGE)


def erasure_refusal(world: Any, *, conversation_ids: Sequence[int], goal_ids: Sequence[int]) -> str | None:
    """The plain refusal text for an erasure closure, or ``None`` when it may proceed."""
    try:
        assert_erasure_permitted(world, conversation_ids=conversation_ids, goal_ids=goal_ids)
    except LegalHoldApplies as exc:
        return str(exc)
    return None


__all__ = [
    "BROAD_PURGE_MESSAGE",
    "EdiscoveryDisabled",
    "EdiscoveryError",
    "LEGAL_HOLD_MESSAGE",
    "LegalHoldApplies",
    "MatterConflict",
    "MatterExportError",
    "MatterNotFound",
    "MatterScopeUnverifiable",
    "MatterStore",
    "MatterValidationError",
    "RECORD_KINDS",
    "STATUSES",
    "active_matter_count",
    "active_matters",
    "assert_erasure_permitted",
    "assert_no_active_matters",
    "build_scope",
    "enabled",
    "erasure_refusal",
    "normalize",
    "require_enabled",
    "store_path",
]
