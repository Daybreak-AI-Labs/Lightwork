"""Bounded, private employee companion policy and conversation storage.

This is a single-control-plane file authority, not a new agent runtime. Callers
must authenticate the exact employee owner (including on reads) and revalidate
workspace membership under ``authority_effect_fence``. Administrative metadata
reads never decrypt conversation content. Policy retention changes may rewrite
encrypted history to enforce expiry, but never return that content to admins.

Each employee record atomically publishes policy, its required audit events,
and an optional tenant/owner-bound encrypted history. There is no plaintext
history fallback. Expiry runs on employee access or policy update, not in a
background sweeper; backups and replicas require separate retention controls.
The last 200 metadata events and 50 exchanges per employee are retained. This
bounded local event journal is not an immutable compliance audit archive. State
is host-local even when the existing world model uses a shared database; this
increment does not support multiple control-plane hosts.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import re
import time
import uuid
from contextlib import contextmanager

from . import crypto_at_rest, file_lock
from .authority_effects import authority_effect_fence
from .paths import canonical_tenant_id, current_tenant_id_strict, data_dir

MAX_EMPLOYEES = 1000
MAX_EVENTS = 200
MAX_HISTORY = 50
MAX_MESSAGE = 4000
MAX_RESPONSE = 12000
MAX_SOURCE_REFS = 50
MAX_RECORD_BYTES = 2 * 1024 * 1024
_POLICY_FIELDS = {
    "owner", "revision", "enabled", "allowed_sources", "allowed_tools",
    "retention_days", "memory_enabled", "action_mode", "admin_content_access",
    "approvals", "updated_at", "updated_by",
}
_EVENT_FIELDS = {"id", "actor", "owner", "action", "revision", "time", "decision", "reason"}
_EXCHANGE_FIELDS = {
    "id", "time", "expires_at", "message", "response", "source_refs", "policy_revision",
}
_ACTIONS = {"policy_updated", "reply", "history_cleared", "history_expired"}
_REASONS = {"policy_updated": "admin_policy", "reply": "owner_and_policy",
            "history_cleared": "owner_request", "history_expired": "retention_policy"}
_HEX_ID = re.compile(r"[0-9a-f]{32}\Z")
_RECORD_NAME = re.compile(r"[0-9a-f]{64}\.json\Z")
_SOURCE_REF = re.compile(r"task:[1-9][0-9]{0,18}\Z")


class CompanionError(RuntimeError):
    """Companion authority is unavailable or cannot be trusted."""


class CompanionValidationError(ValueError):
    """A request exceeds the bounded companion capability contract."""


class CompanionConflictError(CompanionError):
    """The administrator's policy changed since the request was admitted."""


class CompanionPolicyDenied(CompanionError):
    """The current policy does not permit this companion operation."""


def _identity(value: object) -> str:
    if (not isinstance(value, str) or not value.startswith("user:")
            or not value[5:].strip() or value != value.strip() or len(value) > 320
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise CompanionValidationError("An exact authenticated employee identity is required")
    return value


def _integer(value: object, *, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= 2**53 - 1


def _timestamp(value: object) -> bool:
    return (type(value) in (int, float) and 0 <= value <= 2**53 - 1
            and math.isfinite(value))


def _choices(value: object, allowed: set[str]) -> bool:
    return (isinstance(value, list) and all(isinstance(item, str) for item in value)
            and len(value) == len(set(value)) and set(value) <= allowed)


def _validate_options(enabled, allowed_sources, allowed_tools, retention_days) -> None:
    if (type(enabled) is not bool
            or not _choices(allowed_sources, {"own_tasks"})
            or not _choices(allowed_tools, {"task_status"})
            or type(retention_days) is not int or retention_days not in {0, 1, 7, 30}):
        raise CompanionValidationError("Unsupported companion policy or capability")


def _default_policy(owner: str) -> dict:
    return {
        "owner": owner, "revision": 0, "enabled": False,
        "allowed_sources": [], "allowed_tools": [], "retention_days": 0,
        "memory_enabled": False, "action_mode": "read_only",
        "admin_content_access": False, "approvals": "writes_blocked",
        "updated_at": 0, "updated_by": None,
    }


def _validate_policy(policy: object, owner: str) -> None:
    if not isinstance(policy, dict) or set(policy) != _POLICY_FIELDS:
        raise CompanionValidationError("Invalid companion policy schema")
    _validate_options(policy["enabled"], policy["allowed_sources"],
                      policy["allowed_tools"], policy["retention_days"])
    if (policy["owner"] != owner or not _integer(policy["revision"])
            or policy["memory_enabled"] is not False
            or policy["admin_content_access"] is not False
            or policy["action_mode"] != "read_only" or policy["approvals"] != "writes_blocked"
            or not _timestamp(policy["updated_at"])):
        raise CompanionValidationError("Invalid companion policy authority")
    if policy["revision"] > 0:
        _identity(policy["updated_by"])
    elif policy != _default_policy(owner):
        raise CompanionValidationError("Invalid initial companion policy")


def _validate_event(event: object, owner: str, revision: int) -> None:
    if not isinstance(event, dict) or set(event) != _EVENT_FIELDS:
        raise CompanionValidationError("Invalid companion audit event")
    _identity(event["actor"])
    if (not isinstance(event["id"], str) or not _HEX_ID.fullmatch(event["id"])
            or event["owner"] != owner or not _integer(event["revision"])
            or event["revision"] > revision or not _timestamp(event["time"])
            or not isinstance(event["action"], str) or event["action"] not in _ACTIONS
            or event["decision"] != "allowed"
            or event["reason"] != _REASONS[event["action"]]):
        raise CompanionValidationError("Invalid companion audit authority")
    if event["action"] != "policy_updated" and event["actor"] != owner:
        raise CompanionValidationError("Invalid companion event owner")


def _validate_text(message: object, response: object, source_refs: object) -> None:
    if (not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE
            or not isinstance(response, str) or not response.strip() or len(response) > MAX_RESPONSE
            or not isinstance(source_refs, list) or len(source_refs) > MAX_SOURCE_REFS
            or any(not isinstance(ref, str) or not _SOURCE_REF.fullmatch(ref)
                   for ref in source_refs) or len(source_refs) != len(set(source_refs))):
        raise CompanionValidationError("Companion exchange exceeds the supported bounds")


def _validate_exchange(exchange: object, revision: int) -> None:
    if not isinstance(exchange, dict) or set(exchange) != _EXCHANGE_FIELDS:
        raise CompanionValidationError("Invalid companion history schema")
    _validate_text(exchange["message"], exchange["response"], exchange["source_refs"])
    if (not isinstance(exchange["id"], str) or not _HEX_ID.fullmatch(exchange["id"])
            or not _timestamp(exchange["time"]) or not _timestamp(exchange["expires_at"])
            or not exchange["time"] < exchange["expires_at"] <= exchange["time"] + 30 * 86400
            or not _integer(exchange["policy_revision"], minimum=1)
            or exchange["policy_revision"] > revision):
        raise CompanionValidationError("Invalid companion history authority")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CompanionValidationError("Duplicate companion record field")
        result[key] = value
    return result


def _decode(raw: str | bytes):
    return json.loads(raw, object_pairs_hook=_unique_object)


class EmployeeCompanionStore:
    """Tenant-bound authority; employee authentication belongs to the service."""

    def __init__(self):
        tenant = current_tenant_id_strict()
        if not tenant:
            raise CompanionError("Companions require an authenticated named workspace")
        self.tenant = canonical_tenant_id(tenant)
        self.root = data_dir("employee_companions")
        self._check_context()

    def _check_context(self) -> None:
        tenant = current_tenant_id_strict()
        if not tenant or canonical_tenant_id(tenant) != self.tenant:
            raise CompanionError("Companion tenant context changed")
        if data_dir("employee_companions") != self.root:
            raise CompanionError("Companion storage root changed")
        # Match the existing control-plane single-owner deployment guard.
        if os.environ.get("MAVERICK_REPLICA_COUNT", "1").strip() != "1":
            raise CompanionError("Companions currently require one control-plane replica")

    @contextmanager
    def _locked(self):
        self._check_context()
        try:
            # Same lock ordering as API admission and role/offboarding mutations.
            # The existing cross-process fence is reentrant in this thread.
            with authority_effect_fence():
                file_lock.ensure_private_directory(self.root)
                with file_lock.cross_process_lock(self.root / "authority", strict=True):
                    self._check_context()
                    yield
        except CompanionError:
            raise
        except (OSError, RuntimeError) as exc:
            raise CompanionError("Companion storage is unavailable; access refused") from exc

    def _path(self, owner: str):
        return self.root / (hashlib.sha256(owner.encode("utf-8")).hexdigest() + ".json")

    def _record_paths(self) -> list:
        paths = []
        for path in self.root.glob("*.json"):
            if not _RECORD_NAME.fullmatch(path.name):
                raise CompanionError("Companion authority contains an invalid record name")
            paths.append(path)
            if len(paths) > MAX_EMPLOYEES:
                raise CompanionError("Companion employee capacity exceeded")
        return sorted(paths)

    def _validate_record(self, state: object, owner: str) -> None:
        if (not isinstance(state, dict)
                or set(state) != {"version", "tenant", "owner", "policy", "events", "history"}
                or type(state["version"]) is not int or state["version"] != 1
                or state["tenant"] != self.tenant or state["owner"] != owner):
            raise CompanionValidationError("Invalid companion record identity")
        _validate_policy(state["policy"], owner)
        events = state["events"]
        if not isinstance(events, list) or not events or len(events) > MAX_EVENTS:
            raise CompanionValidationError("Invalid companion audit journal")
        for event in events:
            _validate_event(event, owner, state["policy"]["revision"])
        if events[-1]["revision"] != state["policy"]["revision"]:
            raise CompanionValidationError("Companion policy is missing its audit event")
        self._history_blob(state)

    @staticmethod
    def _history_blob(state: dict) -> bytes | None:
        history = state["history"]
        if history is None:
            return None
        if not isinstance(history, str) or len(history) > MAX_RECORD_BYTES:
            raise CompanionValidationError("Invalid companion history envelope")
        blob = base64.b64decode(history, validate=True)
        if not crypto_at_rest.is_bound(blob):
            raise CompanionValidationError("Companion history must be encrypted")
        return blob

    def _read_path(self, path) -> dict:
        try:
            file_lock.ensure_private_file(path)
            if path.stat().st_size > MAX_RECORD_BYTES:
                raise CompanionValidationError("Companion record exceeds its size limit")
            state = _decode(file_lock.atomic_read_text(path))
            if not isinstance(state, dict):
                raise CompanionValidationError("Invalid companion record")
            owner = _identity(state.get("owner"))
            if self._path(owner) != path:
                raise CompanionValidationError("Companion owner does not match its record")
            self._validate_record(state, owner)
            return state
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise CompanionError("Companion stored authority is invalid; access refused") from exc

    def _read(self, owner: str) -> dict:
        path = self._path(owner)
        try:
            path.lstat()
        except FileNotFoundError:
            return {"version": 1, "tenant": self.tenant, "owner": owner,
                    "policy": _default_policy(owner), "events": [], "history": None}
        return self._read_path(path)

    def _save(self, state: dict) -> None:
        self._check_context()
        self._validate_record(state, state["owner"])
        path = self._path(state["owner"])
        paths = self._record_paths()
        if path not in paths and len(paths) >= MAX_EMPLOYEES:
            raise CompanionError("Companion employee capacity exceeded")
        raw = json.dumps(state, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(raw.encode("utf-8")) > MAX_RECORD_BYTES:
            raise CompanionError("Companion record exceeds its size limit")
        file_lock.atomic_write_text(path, raw)

    def _event(self, state: dict, actor: str, action: str) -> dict:
        event = {"id": uuid.uuid4().hex, "actor": actor, "owner": state["owner"],
                 "action": action, "revision": state["policy"]["revision"],
                 "time": time.time(), "decision": "allowed", "reason": _REASONS[action]}
        state["events"] = [*state["events"], event][-MAX_EVENTS:]
        return event

    def _history(self, state: dict) -> list[dict]:
        blob = self._history_blob(state)
        if blob is None:
            return []
        try:
            history = _decode(crypto_at_rest.unseal_bound(
                blob, "employee_companion/history/" + state["owner"],
            ))
            if not isinstance(history, list) or len(history) > MAX_HISTORY:
                raise CompanionValidationError("Invalid companion history")
            for exchange in history:
                _validate_exchange(exchange, state["policy"]["revision"])
            return history
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise CompanionError("Companion private history is invalid; access refused") from exc

    def _retained(self, state: dict, *, now: float) -> list[dict]:
        policy = state["policy"]
        if not policy["enabled"] or policy["retention_days"] == 0:
            return []
        history = self._history(state)
        for exchange in history:
            exchange["expires_at"] = min(
                exchange["expires_at"], exchange["time"] + policy["retention_days"] * 86400,
            )
        return [exchange for exchange in history if exchange["expires_at"] > now]

    def _set_history(self, state: dict, history: list[dict]) -> None:
        if not history:
            state["history"] = None
            return
        raw = json.dumps(history, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        blob = crypto_at_rest.seal_bound(
            raw.encode("utf-8"), "employee_companion/history/" + state["owner"],
        )
        if not crypto_at_rest.is_bound(blob):
            raise CompanionError("Companion history encryption is unavailable")
        state["history"] = base64.b64encode(blob).decode("ascii")

    def policies(self) -> list[dict]:
        """Administrative policy metadata; never opens history envelopes."""
        with self._locked():
            return [copy.deepcopy(self._read_path(path)["policy"]) for path in self._record_paths()]

    def events(self) -> list[dict]:
        """Latest 200 tenant event metadata entries, without content decryption."""
        with self._locked():
            events = []
            for path in self._record_paths():
                events.extend(self._read_path(path)["events"])
                events = sorted(events, key=lambda event: event["time"])[-MAX_EVENTS:]
            return copy.deepcopy(events)

    def policy(self, owner: str) -> dict:
        owner = _identity(owner)
        with self._locked():
            return copy.deepcopy(self._read(owner)["policy"])

    def update_policy(self, owner: str, *, actor: str, expected_revision: int,
                      enabled: bool, allowed_sources: list[str], allowed_tools: list[str],
                      retention_days: int) -> dict:
        owner, actor = _identity(owner), _identity(actor)
        _validate_options(enabled, allowed_sources, allowed_tools, retention_days)
        if not _integer(expected_revision):
            raise CompanionValidationError("An exact policy revision is required")
        with self._locked():
            state = self._read(owner)
            if state["policy"]["revision"] != expected_revision:
                raise CompanionConflictError("Companion policy changed; reload before saving")
            # First apply the prior retention limit, so a longer policy cannot
            # resurrect already expired exchanges. Disabling always erases them.
            history = self._retained(state, now=time.time()) if enabled and retention_days else []
            state["policy"] = {
                **_default_policy(owner), "revision": expected_revision + 1, "enabled": enabled,
                "allowed_sources": list(allowed_sources), "allowed_tools": list(allowed_tools),
                "retention_days": retention_days, "updated_at": time.time(), "updated_by": actor,
            }
            for exchange in history:
                exchange["expires_at"] = min(
                    exchange["expires_at"], exchange["time"] + retention_days * 86400,
                )
            self._set_history(state, [entry for entry in history if entry["expires_at"] > time.time()])
            self._event(state, actor, "policy_updated")
            self._save(state)
            return copy.deepcopy(state["policy"])

    def history(self, owner: str) -> list[dict]:
        owner = _identity(owner)
        with self._locked():
            state = self._read(owner)
            history = self._retained(state, now=time.time())
            if state["history"] is not None:
                original = self._history(state)
                if history != original:
                    self._set_history(state, history)
                    self._event(state, owner, "history_expired")
                    self._save(state)
            return copy.deepcopy(history)

    def record_exchange(self, owner: str, *, message: str, response: str,
                        source_refs: list[str], expected_revision: int) -> dict:
        owner = _identity(owner)
        _validate_text(message, response, source_refs)
        if not _integer(expected_revision):
            raise CompanionValidationError("An exact policy revision is required")
        with self._locked():
            state = self._read(owner)
            policy = state["policy"]
            if policy["revision"] != expected_revision:
                raise CompanionConflictError("Companion policy changed; send the request again")
            if not policy["enabled"]:
                raise CompanionPolicyDenied("Your administrator has not enabled your companion")
            if ("own_tasks" not in policy["allowed_sources"]
                    or "task_status" not in policy["allowed_tools"]):
                raise CompanionPolicyDenied("Task sources are not permitted by the current policy")
            now = time.time()
            history = self._retained(state, now=now)
            event = self._event(state, owner, "reply")
            if policy["retention_days"]:
                history = [*history, {
                    "id": event["id"], "time": now,
                    "expires_at": now + policy["retention_days"] * 86400,
                    "message": message, "response": response,
                    "source_refs": list(source_refs), "policy_revision": policy["revision"],
                }][-MAX_HISTORY:]
            self._set_history(state, history)
            self._save(state)
            return {"trace": copy.deepcopy(event), "history": copy.deepcopy(history)}

    def clear_history(self, owner: str, *, actor: str) -> dict:
        owner, actor = _identity(owner), _identity(actor)
        if actor != owner:
            raise CompanionPolicyDenied("Only the employee can clear their private history")
        with self._locked():
            state = self._read(owner)
            state["history"] = None
            event = self._event(state, actor, "history_cleared")
            self._save(state)
            return copy.deepcopy(event)
