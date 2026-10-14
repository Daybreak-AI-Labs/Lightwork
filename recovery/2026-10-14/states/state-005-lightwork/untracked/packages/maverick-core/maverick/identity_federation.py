"""Deployment-global SCIM, OIDC-subject, and identity-policy authority.

The dashboard's effective role and department decisions combine mutable SCIM
membership with deployment policy and login-time pairwise-subject bindings.
Under governed Postgres those inputs must be one fleet-consistent authority,
not four node-local JSON files plus whichever config a replica happened to
load.  This module owns a bounded encrypted CAS document containing:

* SCIM users and groups;
* hashed-identifier -> raw OIDC subject bindings and retirement tombstones;
* deny-first, recoverable SCIM tenant-lifecycle saga checkpoints;
* the canonical non-secret effective-authorization policy contract; and
* aggregate digests for the externally held SCIM bearer-secret set.

Secrets are never persisted. Raw emails and opaque subjects live only inside
the governed record payload, which the Postgres backend requires to be sealed
with the deployment's application encryption key.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import os
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from .authority_documents import AuthorityDocumentStore

SCHEMA_VERSION = 1
MAX_USERS = 100_000
MAX_GROUPS = 100_000
MAX_SUBJECT_BINDINGS = 100_000
MAX_RETIRED_SUBJECTS = 200_000
MAX_LIFECYCLE_SAGAS = 100_000
# Governed records cap the complete canonical record (authority wrapper, audit
# queue, and this document) at 16 MiB. Keep a full MiB of headroom so a document
# accepted here can always be wrapped and audited by that backend.
MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
MAX_ID_CHARS = 1024
MAX_TEXT_CHARS = 16_384
MAX_SUBJECT_CHARS = 4096
MAX_TIMESTAMP = 253_402_300_799.0
MAX_SCIM_SECRETS = 8
MAX_SCIM_SECRET_BYTES = 4096
MIN_SCIM_SECRET_BYTES = 32
MAX_SECRET_GRACE_SECONDS = 31 * 24 * 60 * 60

DASHBOARD_ROLES = ("admin", "operator", "auditor", "viewer")
_USER_FIELDS = frozenset({
    "id",
    "userName",
    "externalId",
    "displayName",
    "givenName",
    "familyName",
    "email",
    "active",
    "created_at",
    "updated_at",
})
_GROUP_FIELDS = frozenset({
    "id",
    "displayName",
    "externalId",
    "members",
    "created_at",
    "updated_at",
})
_POLICY_FIELDS = frozenset({
    "schema_version",
    "bootstrap_admins",
    "default_role",
    "default_suites",
    "group_roles",
    "group_suites",
    "flags",
})
_POLICY_FLAGS = frozenset({
    "dashboard_invites",
    "dashboard_require_auth",
    "oidc_enabled",
    "scim_enabled",
})
_SAGA_FIELDS = frozenset({
    "operation",
    "phase",
    "record",
    "started_at",
    "updated_at",
})
_SAGA_OPERATIONS = frozenset({"suspend", "delete"})
_SAGA_PHASES = frozenset({
    "deny_persisted",
    "sessions_revoked",
    "tenant_applied",
})
_HEX_32 = re.compile(r"[0-9a-f]{32}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})
_LEGACY_FILENAMES = (
    "scim_users.json",
    "scim_groups.json",
    "oidc-subjects.json",
    "oidc-retired-subjects.json",
    "dashboard-users.json",
    "dashboard-user-suites.json",
)

_STORE: AuthorityDocumentStore[Any] | None = None
_STORE_LOCK = threading.Lock()
_T = TypeVar("_T")


class IdentityFederationError(RuntimeError):
    """The shared effective-identity authority cannot be trusted."""


def _new_store() -> AuthorityDocumentStore[Any]:
    return AuthorityDocumentStore(
        "identity_federation_authority",
        "IFA",
        "identity_federation_authority",
        document_name="global",
        required_backend="postgres",
        authority_scope="deployment_global",
    )


def authority() -> AuthorityDocumentStore[Any]:
    global _STORE

    with _STORE_LOCK:
        if _STORE is None:
            _STORE = _new_store()
        return _STORE


def reset_for_testing() -> None:
    global _STORE

    with _STORE_LOCK:
        _STORE = None


def legacy_paths() -> tuple[Path, ...]:
    from .paths import maverick_home

    home = maverick_home()
    return tuple(home / name for name in _LEGACY_FILENAMES)


def refuse_legacy_state() -> None:
    """Refuse any legacy identity file before touching the shared document."""
    for path in legacy_paths():
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise IdentityFederationError(
                "legacy identity state cannot be inspected"
            ) from exc
        raise IdentityFederationError(
            "legacy identity state exists; import and archive all SCIM and "
            "OIDC subject JSON files before enabling governed Postgres"
        )


def _bounded_json(value: object) -> None:
    try:
        raw = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (OverflowError, RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise IdentityFederationError(
            "shared identity state is not bounded JSON"
        ) from exc
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise IdentityFederationError(
            "shared identity state exceeds its byte bound"
        )


def _text(record: Mapping[str, Any], field: str, *, required: bool = False) -> str:
    value = record.get(field)
    if (
        not isinstance(value, str)
        or value != value.strip()
        or len(value) > MAX_TEXT_CHARS
        or (required and not value)
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
    ):
        raise IdentityFederationError(
            f"shared SCIM record has an invalid {field}"
        )
    return value


def _timestamp(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IdentityFederationError(f"shared identity state has invalid {label}")
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise IdentityFederationError(
            f"shared identity state has invalid {label}"
        ) from exc
    if not math.isfinite(parsed) or not 0 <= parsed <= MAX_TIMESTAMP:
        raise IdentityFederationError(f"shared identity state has invalid {label}")
    return parsed


def _validate_user(uid: object, raw: object) -> dict[str, Any]:
    if (
        not isinstance(uid, str)
        or not _HEX_32.fullmatch(uid)
        or not isinstance(raw, dict)
        or set(raw) != _USER_FIELDS
        or raw.get("id") != uid
    ):
        raise IdentityFederationError("shared SCIM user has an invalid schema")
    clean = copy.deepcopy(raw)
    for field in (
        "userName",
        "externalId",
        "displayName",
        "givenName",
        "familyName",
        "email",
    ):
        clean[field] = _text(raw, field, required=field == "userName")
    if not isinstance(raw.get("active"), bool):
        raise IdentityFederationError("shared SCIM user has invalid active state")
    clean["created_at"] = _timestamp(raw.get("created_at"), label="created_at")
    clean["updated_at"] = _timestamp(raw.get("updated_at"), label="updated_at")
    return clean


def _validate_users(raw: object) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or len(raw) > MAX_USERS:
        raise IdentityFederationError("shared SCIM user roster exceeds its bound")
    users: dict[str, dict[str, Any]] = {}
    usernames: set[str] = set()
    external_ids: set[str] = set()
    for uid, value in raw.items():
        rec = _validate_user(uid, value)
        username = rec["userName"].casefold()
        external_id = rec["externalId"]
        if username in usernames or (external_id and external_id in external_ids):
            raise IdentityFederationError("shared SCIM users are not unique")
        if uid in external_ids or (external_id and external_id in raw):
            raise IdentityFederationError(
                "shared SCIM users have an identity-namespace collision"
            )
        users[uid] = rec
        usernames.add(username)
        if external_id:
            external_ids.add(external_id)
    return users


def _validate_group(gid: object, raw: object) -> dict[str, Any]:
    if (
        not isinstance(gid, str)
        or not _HEX_32.fullmatch(gid)
        or not isinstance(raw, dict)
        or set(raw) != _GROUP_FIELDS
        or raw.get("id") != gid
    ):
        raise IdentityFederationError("shared SCIM group has an invalid schema")
    display_name = _text(raw, "displayName", required=True)
    external_id = _text(raw, "externalId")
    members = raw.get("members")
    if not isinstance(members, list) or len(members) > MAX_USERS:
        raise IdentityFederationError("shared SCIM group members exceed their bound")
    cleaned_members: list[str] = []
    seen: set[str] = set()
    for member in members:
        if (
            not isinstance(member, str)
            or not _HEX_32.fullmatch(member)
            or member in seen
        ):
            raise IdentityFederationError("shared SCIM group has invalid members")
        cleaned_members.append(member)
        seen.add(member)
    return {
        "id": gid,
        "displayName": display_name,
        "externalId": external_id,
        "members": cleaned_members,
        "created_at": _timestamp(raw.get("created_at"), label="created_at"),
        "updated_at": _timestamp(raw.get("updated_at"), label="updated_at"),
    }


def _validate_groups(raw: object) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or len(raw) > MAX_GROUPS:
        raise IdentityFederationError("shared SCIM group roster exceeds its bound")
    groups: dict[str, dict[str, Any]] = {}
    names: set[str] = set()
    external_ids: set[str] = set()
    for gid, value in raw.items():
        rec = _validate_group(gid, value)
        name = rec["displayName"].casefold()
        external_id = rec["externalId"]
        if name in names or (external_id and external_id in external_ids):
            raise IdentityFederationError("shared SCIM groups are not unique")
        groups[gid] = rec
        names.add(name)
        if external_id:
            external_ids.add(external_id)
    return groups


def _validate_bindings(raw: object) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or len(raw) > MAX_SUBJECT_BINDINGS:
        raise IdentityFederationError("shared subject directory exceeds its bound")
    bindings: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if (
            not isinstance(key, str)
            or not _HEX_64.fullmatch(key)
            or not isinstance(value, dict)
            or set(value) != {"sub", "ts"}
        ):
            raise IdentityFederationError("shared subject directory is invalid")
        subject = value.get("sub")
        if (
            not isinstance(subject, str)
            or not subject
            or subject != subject.strip()
            or len(subject) > MAX_SUBJECT_CHARS
        ):
            raise IdentityFederationError("shared subject directory is invalid")
        bindings[key] = {
            "sub": subject,
            "ts": _timestamp(value.get("ts"), label="subject timestamp"),
        }
    return bindings


def _validate_retired(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict) or len(raw) > MAX_RETIRED_SUBJECTS:
        raise IdentityFederationError("shared retirement ledger exceeds its bound")
    retired: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not _HEX_64.fullmatch(key):
            raise IdentityFederationError("shared retirement ledger is invalid")
        retired[key] = _timestamp(value, label="retirement timestamp")
    return retired


def _validate_policy(raw: object) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != _POLICY_FIELDS:
        raise IdentityFederationError("shared identity policy has an invalid schema")
    if raw.get("schema_version") != 1:
        raise IdentityFederationError("shared identity policy version is unsupported")
    admins = raw.get("bootstrap_admins")
    default_role = raw.get("default_role")
    default_suites = raw.get("default_suites")
    group_roles = raw.get("group_roles")
    group_suites = raw.get("group_suites")
    flags = raw.get("flags")
    if (
        not isinstance(admins, list)
        or len(admins) > MAX_USERS
        or admins != sorted(set(admins))
        or any(
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > MAX_SUBJECT_CHARS
            for value in admins
        )
        or default_role not in DASHBOARD_ROLES
        or (
            default_suites is not None
            and (
                not isinstance(default_suites, list)
                or default_suites != sorted(set(default_suites))
                or any(not isinstance(value, str) or not value for value in default_suites)
            )
        )
        or not isinstance(group_roles, dict)
        or not isinstance(group_suites, dict)
        or not isinstance(flags, dict)
        or set(flags) != _POLICY_FLAGS
        or any(not isinstance(value, bool) for value in flags.values())
    ):
        raise IdentityFederationError("shared identity policy is invalid")
    for name, role in group_roles.items():
        if (
            not isinstance(name, str)
            or not name
            or name != name.casefold()
            or len(name) > MAX_TEXT_CHARS
            or role not in DASHBOARD_ROLES
        ):
            raise IdentityFederationError("shared group-role policy is invalid")
    for name, suites in group_suites.items():
        if (
            not isinstance(name, str)
            or not name
            or name != name.casefold()
            or len(name) > MAX_TEXT_CHARS
            or not isinstance(suites, list)
            or suites != sorted(set(suites))
            or any(not isinstance(value, str) or not value for value in suites)
        ):
            raise IdentityFederationError("shared group-suite policy is invalid")
    return copy.deepcopy(raw)


def _validate_secret_rotation(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {
        "active_digest",
        "previous_digest",
        "grace_until",
    }:
        raise IdentityFederationError("shared SCIM secret proof is invalid")
    active = raw.get("active_digest")
    previous = raw.get("previous_digest")
    grace = raw.get("grace_until")
    if active is not None and (not isinstance(active, str) or not _SHA256.fullmatch(active)):
        raise IdentityFederationError("shared SCIM secret proof is invalid")
    if previous is not None and (
        not isinstance(previous, str) or not _SHA256.fullmatch(previous)
    ):
        raise IdentityFederationError("shared SCIM secret proof is invalid")
    if previous is None:
        if grace is not None:
            raise IdentityFederationError("shared SCIM secret proof is invalid")
    else:
        if grace is None:
            raise IdentityFederationError("shared SCIM secret proof is invalid")
        grace = _timestamp(grace, label="SCIM secret grace deadline")
    return {
        "active_digest": active,
        "previous_digest": previous,
        "grace_until": grace,
    }


def _validate_sagas(raw: object) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or len(raw) > MAX_LIFECYCLE_SAGAS:
        raise IdentityFederationError("shared identity sagas exceed their bound")
    sagas: dict[str, dict[str, Any]] = {}
    for uid, value in raw.items():
        if (
            not isinstance(uid, str)
            or not _HEX_32.fullmatch(uid)
            or not isinstance(value, dict)
            or set(value) != _SAGA_FIELDS
            or value.get("operation") not in _SAGA_OPERATIONS
            or value.get("phase") not in _SAGA_PHASES
        ):
            raise IdentityFederationError("shared identity saga is invalid")
        record = _validate_user(uid, value.get("record"))
        if record.get("active") is not False:
            raise IdentityFederationError("shared identity saga is not deny-first")
        sagas[uid] = {
            "operation": value["operation"],
            "phase": value["phase"],
            "record": record,
            "started_at": _timestamp(value.get("started_at"), label="saga start"),
            "updated_at": _timestamp(value.get("updated_at"), label="saga update"),
        }
    return sagas


def validate_document(document: object, *, initialize: bool) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise IdentityFederationError("shared identity state must be an object")
    if not document:
        if not initialize:
            return {
                "schema_version": SCHEMA_VERSION,
                "users": {},
                "groups": {},
                "subject_bindings": {},
                "retired_subjects": {},
                "lifecycle_sagas": {},
                "policy": None,
                "policy_digest": None,
                "scim_secret_rotation": {
                    "active_digest": None,
                    "previous_digest": None,
                    "grace_until": None,
                },
            }
        document.update(validate_document({}, initialize=False))
    expected = {
        "schema_version",
        "users",
        "groups",
        "subject_bindings",
        "retired_subjects",
        "lifecycle_sagas",
        "policy",
        "policy_digest",
        "scim_secret_rotation",
    }
    if set(document) != expected or document.get("schema_version") != SCHEMA_VERSION:
        raise IdentityFederationError("shared identity state has an invalid schema")
    policy = _validate_policy(document.get("policy"))
    policy_digest = document.get("policy_digest")
    if policy_digest is not None and (
        not isinstance(policy_digest, str) or not _SHA256.fullmatch(policy_digest)
    ):
        raise IdentityFederationError("shared identity policy digest is invalid")
    if (policy is None) != (policy_digest is None):
        raise IdentityFederationError("shared identity policy proof is incomplete")
    if policy is not None and policy_digest != policy_contract_digest(policy):
        raise IdentityFederationError("shared identity policy digest is inconsistent")
    normalized = {
        "schema_version": SCHEMA_VERSION,
        "users": _validate_users(document.get("users")),
        "groups": _validate_groups(document.get("groups")),
        "subject_bindings": _validate_bindings(document.get("subject_bindings")),
        "retired_subjects": _validate_retired(document.get("retired_subjects")),
        "lifecycle_sagas": _validate_sagas(document.get("lifecycle_sagas")),
        "policy": policy,
        "policy_digest": policy_digest,
        "scim_secret_rotation": _validate_secret_rotation(
            document.get("scim_secret_rotation")
        ),
    }
    _bounded_json(normalized)
    return normalized


def _require_postgres(store: object) -> None:
    if getattr(store, "backend_kind", None) != "postgres":
        raise IdentityFederationError(
            "shared identity federation requires governed Postgres"
        )


def read_state() -> dict[str, Any]:
    refuse_legacy_state()
    store = authority()
    try:
        _require_postgres(store)
        state = validate_document(store.read(), initialize=False)
        _require_postgres(store)
        return state
    except IdentityFederationError:
        raise
    except Exception as exc:
        raise IdentityFederationError(
            "shared identity federation could not be read"
        ) from exc


def mutate_state(
    callback: Callable[[dict[str, Any]], _T],
    *,
    action: str,
    actor: str = "system",
) -> _T:
    refuse_legacy_state()
    store = authority()

    def _apply(document: dict[str, Any]) -> _T:
        state = validate_document(document, initialize=True)
        result = callback(state)
        validate_document(state, initialize=False)
        document.clear()
        document.update(state)
        return result

    try:
        _require_postgres(store)
        result = store.mutate(_apply, action=action, actor=actor)
        _require_postgres(store)
        return result
    except IdentityFederationError:
        raise
    except Exception as exc:
        raise IdentityFederationError(
            "shared identity federation could not be changed"
        ) from exc


def mutate_state_with_time(
    callback: Callable[[dict[str, Any], float], _T],
    *,
    action: str,
    actor: str = "system",
) -> _T:
    refuse_legacy_state()
    store = authority()

    def _apply(document: dict[str, Any], now: float) -> _T:
        state = validate_document(document, initialize=True)
        result = callback(state, now)
        validate_document(state, initialize=False)
        document.clear()
        document.update(state)
        return result

    try:
        _require_postgres(store)
        result = store.mutate_with_authoritative_time(
            _apply,
            action=action,
            actor=actor,
        )
        _require_postgres(store)
        return result
    except IdentityFederationError:
        raise
    except Exception as exc:
        raise IdentityFederationError(
            "shared identity federation could not be changed"
        ) from exc


def authoritative_time() -> float:
    refuse_legacy_state()
    store = authority()
    try:
        _require_postgres(store)
        value = float(store.authoritative_time())
    except IdentityFederationError:
        raise
    except Exception as exc:
        raise IdentityFederationError(
            "shared identity federation clock is unavailable"
        ) from exc
    if not math.isfinite(value) or not 0 < value <= MAX_TIMESTAMP:
        raise IdentityFederationError("shared identity federation clock is invalid")
    return value


def normalize_identifier(identifier: str) -> str:
    return str(identifier or "").strip().casefold()


def identifier_key(identifier: str) -> str:
    return hashlib.sha256(normalize_identifier(identifier).encode("utf-8")).hexdigest()


def subject_key(subject: str) -> str:
    value = str(subject or "").strip()
    return hashlib.sha256(b"subject\0" + value.encode("utf-8")).hexdigest()


def subjects_in(state: Mapping[str, Any], identifiers: Iterable[str]) -> set[str]:
    bindings = state.get("subject_bindings")
    if not isinstance(bindings, dict):
        raise IdentityFederationError("shared subject directory is unavailable")
    subjects: set[str] = set()
    for identifier in identifiers:
        value = str(identifier or "").strip()
        if not value:
            continue
        entry = bindings.get(identifier_key(value))
        if isinstance(entry, dict) and isinstance(entry.get("sub"), str):
            subjects.add(entry["sub"])
    return subjects


def retire_in_state(
    state: dict[str, Any],
    identifiers: Iterable[str],
    now: float,
) -> None:
    values = {str(value).strip() for value in identifiers if str(value or "").strip()}
    if not values:
        return
    subjects = subjects_in(state, values)
    keys = ({identifier_key(value) for value in values}
            | {subject_key(value) for value in values | subjects})
    retired = state["retired_subjects"]
    if len(set(retired) | keys) > MAX_RETIRED_SUBJECTS:
        raise IdentityFederationError("shared retirement ledger exceeds its bound")
    for key in keys:
        retired[key] = now


def reinstate_in_state(
    state: dict[str, Any],
    identifiers: Iterable[str],
    *,
    linked_identifiers: Iterable[str] | None = None,
) -> None:
    values = {str(value).strip() for value in identifiers if str(value or "").strip()}
    linked = values if linked_identifiers is None else {
        str(value).strip()
        for value in linked_identifiers
        if str(value or "").strip()
    }
    subjects = subjects_in(state, linked)
    keys = ({identifier_key(value) for value in values}
            | {subject_key(value) for value in values | subjects})
    retired = state["retired_subjects"]
    for key in keys:
        retired.pop(key, None)


def subject_is_retired(state: Mapping[str, Any], subject: str) -> bool:
    value = str(subject or "").strip()
    if not value:
        return False
    retired = state.get("retired_subjects")
    bindings = state.get("subject_bindings")
    if not isinstance(retired, dict) or not isinstance(bindings, dict):
        raise IdentityFederationError("shared subject retirement state is unavailable")
    if subject_key(value) in retired:
        return True
    return any(
        isinstance(entry, dict)
        and entry.get("sub") == value
        and key in retired
        for key, entry in bindings.items()
    )


def _principal_subject(principal: str) -> str:
    value = str(principal or "").strip()
    subject = value.removeprefix("user:")
    if not subject or len(subject) > MAX_SUBJECT_CHARS:
        return ""
    return subject


def matching_users(
    state: Mapping[str, Any],
    principal: str,
) -> dict[str, dict[str, Any]]:
    """SCIM users bound to a principal through authoritative identifiers.

    Grant resolution deliberately ignores mutable contact attributes. A direct
    OIDC subject may match only the SCIM ``externalId`` or internal record id;
    pairwise subjects may bridge only through bindings recorded for those same
    identifiers. Retirement is checked before returning any match.
    """
    subject = _principal_subject(principal)
    if not subject or subject_is_retired(state, subject):
        return {}
    users = state.get("users")
    bindings = state.get("subject_bindings")
    if not isinstance(users, dict) or not isinstance(bindings, dict):
        raise IdentityFederationError("shared SCIM identity roster is unavailable")
    matched: dict[str, dict[str, Any]] = {}
    for uid, raw in users.items():
        if not isinstance(raw, dict):
            raise IdentityFederationError("shared SCIM identity roster is invalid")
        external_id = str(raw.get("externalId") or "")
        internal_id = str(raw.get("id") or "")
        bound = subjects_in(state, (external_id, internal_id))
        if subject in {external_id, internal_id} or subject in bound:
            matched[uid] = copy.deepcopy(raw)
    return matched


def active_for_principal_in_state(
    state: Mapping[str, Any],
    principal: str,
) -> bool | None:
    """Return the shared SCIM lifecycle opinion for ``principal``."""
    subject = _principal_subject(principal)
    if not subject:
        return None
    if subject_is_retired(state, subject):
        return False
    matched = matching_users(state, principal)
    if not matched:
        return None
    return all(record.get("active") is True for record in matched.values())


def group_names_for_principal_in_state(
    state: Mapping[str, Any],
    principal: str,
) -> frozenset[str]:
    """Case-preserving SCIM group names for an active bound principal."""
    matched = {
        uid
        for uid, record in matching_users(state, principal).items()
        if record.get("active") is True
    }
    groups = state.get("groups")
    if not matched or not isinstance(groups, dict):
        return frozenset()
    return frozenset(
        str(group.get("displayName") or "")
        for group in groups.values()
        if isinstance(group, dict)
        and matched.intersection(group.get("members") or [])
        and group.get("displayName")
    )


def _config_snapshot(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if config is not None:
        if not isinstance(config, Mapping):
            raise IdentityFederationError("identity policy config is not a mapping")
        return config
    try:
        from .config import config_source_errors, load_global_config

        errors = config_source_errors(include_tenant=False)
        loaded = load_global_config()
    except Exception as exc:
        raise IdentityFederationError(
            "identity policy config is unavailable"
        ) from exc
    if errors or not isinstance(loaded, Mapping):
        raise IdentityFederationError("identity policy config is untrusted")
    return loaded


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise IdentityFederationError(f"{label} must be a table")
    return dict(value)


def _flag(
    config: Mapping[str, Any],
    *,
    section: Mapping[str, Any],
    key: str,
    env_name: str,
    default: bool,
) -> bool:
    raw_env = os.environ.get(env_name)
    if raw_env is not None and raw_env.strip():
        normalized = raw_env.strip().lower()
        if normalized in _TRUE:
            return True
        if normalized in _FALSE:
            return False
        raise IdentityFederationError(f"{env_name} is not a boolean")
    if key not in section:
        return default
    value = section.get(key)
    if not isinstance(value, bool):
        raise IdentityFederationError(f"identity policy flag {key} is invalid")
    return value


def _deployment_secret(name: str, config: Mapping[str, Any]) -> str:
    secrets = _mapping(config.get("secrets"), label="secrets")
    backend = (
        os.environ.get("MAVERICK_SECRETS_BACKEND")
        or str(secrets.get("backend") or "env")
    ).strip().lower()
    if backend not in {"env", "file"}:
        raise IdentityFederationError("deployment secrets backend is invalid")
    if backend == "file":
        raw_dir = os.environ.get("MAVERICK_SECRETS_DIR") or secrets.get("dir")
        if raw_dir and str(raw_dir).strip():
            base = Path(str(raw_dir)).expanduser()
            for candidate in (name, name.lower()):
                path = base / candidate
                try:
                    if path.is_file():
                        return path.read_text(encoding="utf-8").rstrip("\r\n")
                except OSError as exc:
                    raise IdentityFederationError(
                        "deployment SCIM secret is unreadable"
                    ) from exc
    return os.environ.get(name, "")


def resolve_scim_secrets(
    config: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    cfg = _config_snapshot(config)
    raw = _deployment_secret("MAVERICK_SCIM_TOKEN", cfg)
    entries = tuple(value.strip() for value in raw.split(",") if value.strip())
    if len(entries) > MAX_SCIM_SECRETS:
        raise IdentityFederationError("too many SCIM bearer secrets are configured")
    return entries


def _bounded_secret_values(secrets: Iterable[str]) -> tuple[str, ...]:
    values: list[str] = []
    for raw in secrets:
        values.append(str(raw or "").strip())
        if len(values) > MAX_SCIM_SECRETS:
            raise IdentityFederationError("too many SCIM bearer secrets are configured")
    return tuple(values)


def scim_secret_set_digest(secrets: Iterable[str]) -> str:
    digests: set[str] = set()
    for value in _bounded_secret_values(secrets):
        if value.startswith("sha256:"):
            canonical = value.lower()
            if not _SHA256.fullmatch(canonical):
                raise IdentityFederationError("configured SCIM bearer digest is invalid")
            digests.add(canonical.removeprefix("sha256:"))
            continue
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise IdentityFederationError("configured SCIM bearer secret is invalid") from exc
        if not MIN_SCIM_SECRET_BYTES <= len(encoded) <= MAX_SCIM_SECRET_BYTES:
            raise IdentityFederationError(
                "shared SCIM bearer secrets must be 32 to 4096 UTF-8 bytes"
            )
        digests.add(hashlib.sha256(encoded).hexdigest())
    material = json.dumps(sorted(digests), separators=(",", ":")).encode("ascii")
    return "sha256:" + hashlib.sha256(material).hexdigest()


def _known_suites() -> frozenset[str]:
    from .domain import SUITE_PREFIXES

    return frozenset(SUITE_PREFIXES.values())


def resolve_policy_contract(
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = _config_snapshot(config)
    dashboard = _mapping(cfg.get("dashboard"), label="dashboard")
    auth = _mapping(cfg.get("auth"), label="auth")
    oidc = _mapping(auth.get("oidc"), label="auth.oidc")

    raw_admins: object
    env_admins = os.environ.get("MAVERICK_DASHBOARD_ADMINS", "").strip()
    if env_admins:
        raw_admins = [value.strip() for value in env_admins.split(",") if value.strip()]
    else:
        raw_admins = dashboard.get("admins", []) or []
        if isinstance(raw_admins, str):
            raw_admins = [raw_admins]
    if not isinstance(raw_admins, (list, tuple)) or any(
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > MAX_SUBJECT_CHARS
        for value in raw_admins
    ):
        raise IdentityFederationError("dashboard bootstrap admins are invalid")
    admins = sorted({value.strip() for value in raw_admins})

    default_role = dashboard.get("default_role", "operator")
    if default_role not in DASHBOARD_ROLES:
        raise IdentityFederationError("dashboard default role is invalid")

    if "default_suites" not in dashboard:
        default_suites: list[str] | None = None
    else:
        raw_default_suites = dashboard.get("default_suites")
        if not isinstance(raw_default_suites, (list, tuple)) or any(
            not isinstance(value, str) for value in raw_default_suites
        ):
            raise IdentityFederationError("dashboard default suites are invalid")
        default_suites = sorted(
            {value for value in raw_default_suites if value in _known_suites()}
        )

    raw_group_roles = _mapping(
        dashboard.get("group_roles"),
        label="dashboard.group_roles",
    )
    group_roles: dict[str, str] = {}
    for raw_name, raw_role in raw_group_roles.items():
        if (
            not isinstance(raw_name, str)
            or not raw_name.strip()
            or not isinstance(raw_role, str)
            or raw_role.strip().lower() not in DASHBOARD_ROLES
        ):
            raise IdentityFederationError("dashboard group-role mapping is invalid")
        name = raw_name.strip().casefold()
        if name in group_roles:
            raise IdentityFederationError("dashboard group-role mapping is ambiguous")
        group_roles[name] = raw_role.strip().lower()

    raw_group_suites = _mapping(
        dashboard.get("group_suites"),
        label="dashboard.group_suites",
    )
    group_suites: dict[str, list[str]] = {}
    for raw_name, raw_suites in raw_group_suites.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise IdentityFederationError("dashboard group-suite mapping is invalid")
        values = [raw_suites] if isinstance(raw_suites, str) else raw_suites
        if not isinstance(values, (list, tuple)) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise IdentityFederationError("dashboard group-suite mapping is invalid")
        name = raw_name.strip().casefold()
        if name in group_suites:
            raise IdentityFederationError("dashboard group-suite mapping is ambiguous")
        group_suites[name] = sorted({value.strip() for value in values})

    oidc_enabled_raw = os.environ.get("MAVERICK_OIDC_ENABLED")
    if oidc_enabled_raw is not None:
        normalized = oidc_enabled_raw.strip().lower()
        if normalized in _TRUE:
            oidc_enabled = True
        elif normalized in _FALSE or not normalized:
            oidc_enabled = False
        else:
            raise IdentityFederationError("MAVERICK_OIDC_ENABLED is not a boolean")
    else:
        configured_oidc = oidc.get("enabled", False)
        if not isinstance(configured_oidc, bool):
            raise IdentityFederationError("OIDC enable policy is invalid")
        oidc_enabled = configured_oidc

    contract = {
        "schema_version": 1,
        "bootstrap_admins": admins,
        "default_role": default_role,
        "default_suites": default_suites,
        "group_roles": dict(sorted(group_roles.items())),
        "group_suites": dict(sorted(group_suites.items())),
        "flags": {
            "dashboard_invites": _flag(
                cfg,
                section=dashboard,
                key="invites",
                env_name="MAVERICK_DASHBOARD_INVITES",
                default=False,
            ),
            "dashboard_require_auth": _flag(
                cfg,
                section=dashboard,
                key="require_auth",
                env_name="MAVERICK_DASHBOARD_REQUIRE_AUTH",
                default=False,
            ),
            "oidc_enabled": oidc_enabled,
            "scim_enabled": bool(resolve_scim_secrets(cfg)),
        },
    }
    validated = _validate_policy(contract)
    assert validated is not None
    return validated


def policy_contract_digest(policy: Mapping[str, Any]) -> str:
    try:
        raw = json.dumps(
            policy,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (OverflowError, TypeError, UnicodeError, ValueError) as exc:
        raise IdentityFederationError("identity policy is not canonical JSON") from exc
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _secret_digest_allowed(rotation: Mapping[str, Any], digest: str, now: float) -> bool:
    active = rotation.get("active_digest")
    if isinstance(active, str) and hmac.compare_digest(active, digest):
        return True
    previous = rotation.get("previous_digest")
    grace = rotation.get("grace_until")
    return bool(
        isinstance(previous, str)
        and hmac.compare_digest(previous, digest)
        and isinstance(grace, (int, float))
        and not isinstance(grace, bool)
        and now <= float(grace)
    )


def _secret_set_allowed(
    rotation: Mapping[str, Any],
    secrets: Iterable[str],
    now: float,
) -> bool:
    """Verify one replica's active/previous bearer configuration.

    During rotation replicas commonly expose ``old,new`` so either IdP token is
    accepted. The authority stores only aggregate digests of the old and new
    sets, never individual secret material. With at most eight entries we can
    safely enumerate partitions and prove that the two local subsets correspond
    exactly to those aggregate digests.
    """
    values = _bounded_secret_values(secrets)
    whole_digest = scim_secret_set_digest(values)
    if _secret_digest_allowed(rotation, whole_digest, now):
        return True
    active = rotation.get("active_digest")
    previous = rotation.get("previous_digest")
    grace = rotation.get("grace_until")
    if not (
        isinstance(active, str)
        and isinstance(previous, str)
        and isinstance(grace, (int, float))
        and not isinstance(grace, bool)
        and now <= float(grace)
        and len(values) >= 2
    ):
        return False
    # Complementary masks describe each partition twice. That is bounded by
    # 254 checks at MAX_SCIM_SECRETS=8 and keeps no per-secret digest in state.
    for mask in range(1, (1 << len(values)) - 1):
        left = tuple(value for index, value in enumerate(values) if mask & (1 << index))
        right = tuple(value for index, value in enumerate(values) if not mask & (1 << index))
        left_digest = scim_secret_set_digest(left)
        right_digest = scim_secret_set_digest(right)
        if (
            hmac.compare_digest(left_digest, active)
            and hmac.compare_digest(right_digest, previous)
        ) or (
            hmac.compare_digest(left_digest, previous)
            and hmac.compare_digest(right_digest, active)
        ):
            return True
    return False


def bind_deployment_contract(
    config: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Bind first deployment policy/secret proofs; never rebind drift."""
    policy = resolve_policy_contract(config)
    policy_digest = policy_contract_digest(policy)
    secret_values = resolve_scim_secrets(config)
    secret_digest = scim_secret_set_digest(secret_values)

    def _bind(state: dict[str, Any], now: float) -> tuple[str, str]:
        if state["policy_digest"] is None:
            state["policy"] = copy.deepcopy(policy)
            state["policy_digest"] = policy_digest
        elif not hmac.compare_digest(state["policy_digest"], policy_digest):
            raise IdentityFederationError(
                "deployment identity policy does not match shared authority"
            )
        rotation = state["scim_secret_rotation"]
        if rotation["active_digest"] is None:
            rotation["active_digest"] = secret_digest
        elif not _secret_set_allowed(rotation, secret_values, now):
            raise IdentityFederationError(
                "deployment SCIM bearer set does not match shared authority"
            )
        active_digest = rotation.get("active_digest")
        if not isinstance(active_digest, str):
            raise IdentityFederationError("shared SCIM bearer proof is not bound")
        return policy_digest, active_digest

    return mutate_state_with_time(
        _bind,
        action="bind_identity_deployment_contract",
        actor="authority-readiness",
    )


def verified_state(
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one validated authority snapshot after proving config parity."""
    state = read_state()
    local = resolve_policy_contract(config)
    digest = policy_contract_digest(local)
    stored_digest = state.get("policy_digest")
    if not isinstance(stored_digest, str) or not hmac.compare_digest(
        stored_digest, digest
    ):
        raise IdentityFederationError(
            "deployment identity policy does not match shared authority"
        )
    policy = state.get("policy")
    validated = _validate_policy(policy)
    if validated is None:
        raise IdentityFederationError("shared identity policy is not bound")
    state["policy"] = validated
    return state


def verified_policy(
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return verified_state(config)["policy"]


def active_for_principal(principal: str) -> bool | None:
    return active_for_principal_in_state(read_state(), principal)


def group_names_for_principal(principal: str) -> frozenset[str]:
    return group_names_for_principal_in_state(verified_state(), principal)


def role_for_principal(principal: str) -> str | None:
    """Most privileged pinned group role for ``principal``, if any."""
    state = verified_state()
    names = group_names_for_principal_in_state(state, principal)
    mapping = state["policy"]["group_roles"]
    mapped = {
        mapping[name.casefold()]
        for name in names
        if name.casefold() in mapping
    }
    for role in DASHBOARD_ROLES:
        if role in mapped:
            return role
    return None


def suites_for_principal_in_state(
    state: Mapping[str, Any],
    principal: str,
) -> frozenset[str] | None:
    """Pinned group-derived suite grant from one verified snapshot."""
    names = group_names_for_principal_in_state(state, principal)
    policy = state.get("policy")
    if not isinstance(policy, dict):
        raise IdentityFederationError("shared identity policy is unavailable")
    mapping = policy["group_suites"]
    hit = False
    suites: set[str] = set()
    for name in names:
        key = name.casefold()
        if key not in mapping:
            continue
        hit = True
        suites.update(mapping[key])
    if not hit:
        return None
    return frozenset(suites).intersection(_known_suites())


def suites_for_principal(principal: str) -> frozenset[str] | None:
    """Pinned group-derived suite grant, where empty remains deny-all."""
    return suites_for_principal_in_state(verified_state(), principal)


def verify_scim_secret_set(
    secrets: Iterable[str],
) -> str:
    values = _bounded_secret_values(secrets)
    digest = scim_secret_set_digest(values)

    def _verify(state: dict[str, Any], now: float) -> str:
        rotation = state["scim_secret_rotation"]
        if not _secret_set_allowed(rotation, values, now):
            raise IdentityFederationError(
                "deployment SCIM bearer set does not match shared authority"
            )
        return digest

    # A timed no-op CAS keeps the rotation row and database clock in one locked
    # authority snapshot. A concurrent finalization therefore cannot revive the
    # previous bearer set through a stale read.
    return mutate_state_with_time(
        _verify,
        action="verify_scim_secret_set",
        actor="scim-auth",
    )


def verify_scim_authorization_contract(
    secrets: Iterable[str],
    config: Mapping[str, Any] | None = None,
) -> bool:
    """Atomically prove policy parity and bearer-set admission for one request."""
    local_policy = resolve_policy_contract(config)
    policy_digest = policy_contract_digest(local_policy)
    values = _bounded_secret_values(secrets)
    # Validate the set's length and every configured secret before entering the
    # authority CAS. The callback still re-checks its aggregate against the
    # database-timed rotation row on every retry.
    scim_secret_set_digest(values)

    def _verify(state: dict[str, Any], now: float) -> bool:
        stored_policy_digest = state.get("policy_digest")
        if not isinstance(stored_policy_digest, str) or not hmac.compare_digest(
            stored_policy_digest,
            policy_digest,
        ):
            raise IdentityFederationError(
                "deployment identity policy does not match shared authority"
            )
        if not _secret_set_allowed(
            state["scim_secret_rotation"],
            values,
            now,
        ):
            raise IdentityFederationError(
                "deployment SCIM bearer set does not match shared authority"
            )
        policy = state.get("policy")
        validated = _validate_policy(policy)
        if validated is None:
            raise IdentityFederationError("shared identity policy is not bound")
        return validated["flags"]["scim_enabled"]

    return mutate_state_with_time(
        _verify,
        action="verify_scim_authorization_contract",
        actor="scim-auth",
    )


def rotate_policy_contract(
    expected_digest: str,
    config: Mapping[str, Any] | None = None,
) -> str:
    if not isinstance(expected_digest, str) or not _SHA256.fullmatch(expected_digest):
        raise ValueError("expected identity-policy digest is invalid")
    replacement = resolve_policy_contract(config)
    replacement_digest = policy_contract_digest(replacement)

    def _rotate(state: dict[str, Any]) -> str:
        current = state.get("policy_digest")
        if not isinstance(current, str) or not hmac.compare_digest(
            current, expected_digest
        ):
            raise IdentityFederationError("identity-policy rotation lost CAS authority")
        state["policy"] = copy.deepcopy(replacement)
        state["policy_digest"] = replacement_digest
        return replacement_digest

    return mutate_state(
        _rotate,
        action="rotate_identity_policy_contract",
        actor="identity-policy-operator",
    )


def rotate_scim_secret_set(
    expected_digest: str,
    *,
    grace_seconds: float,
    config: Mapping[str, Any] | None = None,
) -> str:
    if not isinstance(expected_digest, str) or not _SHA256.fullmatch(expected_digest):
        raise ValueError("expected SCIM-secret digest is invalid")
    try:
        grace = float(grace_seconds)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("SCIM-secret grace must be a finite duration") from exc
    if not math.isfinite(grace) or not 0 <= grace <= MAX_SECRET_GRACE_SECONDS:
        raise ValueError("SCIM-secret grace must be between 0 and 31 days")
    replacement = scim_secret_set_digest(resolve_scim_secrets(config))

    def _rotate(state: dict[str, Any], now: float) -> str:
        rotation = state["scim_secret_rotation"]
        active = rotation.get("active_digest")
        if not isinstance(active, str) or not hmac.compare_digest(
            active, expected_digest
        ):
            raise IdentityFederationError("SCIM-secret rotation lost CAS authority")
        if hmac.compare_digest(active, replacement):
            return replacement
        rotation["previous_digest"] = active
        rotation["active_digest"] = replacement
        rotation["grace_until"] = now + grace
        return replacement

    return mutate_state_with_time(
        _rotate,
        action="rotate_scim_secret_set",
        actor="identity-policy-operator",
    )


def finalize_scim_secret_rotation(expected_digest: str) -> None:
    if not isinstance(expected_digest, str) or not _SHA256.fullmatch(expected_digest):
        raise ValueError("expected SCIM-secret digest is invalid")

    def _finalize(state: dict[str, Any]) -> None:
        rotation = state["scim_secret_rotation"]
        active = rotation.get("active_digest")
        if not isinstance(active, str) or not hmac.compare_digest(
            active, expected_digest
        ):
            raise IdentityFederationError("SCIM-secret rotation lost CAS authority")
        rotation["previous_digest"] = None
        rotation["grace_until"] = None

    mutate_state(
        _finalize,
        action="finalize_scim_secret_rotation",
        actor="identity-policy-operator",
    )


__all__ = [
    "active_for_principal",
    "active_for_principal_in_state",
    "authority",
    "authoritative_time",
    "bind_deployment_contract",
    "DASHBOARD_ROLES",
    "finalize_scim_secret_rotation",
    "group_names_for_principal",
    "group_names_for_principal_in_state",
    "identifier_key",
    "IdentityFederationError",
    "legacy_paths",
    "mutate_state",
    "mutate_state_with_time",
    "matching_users",
    "policy_contract_digest",
    "read_state",
    "refuse_legacy_state",
    "reinstate_in_state",
    "reset_for_testing",
    "resolve_policy_contract",
    "resolve_scim_secrets",
    "retire_in_state",
    "role_for_principal",
    "rotate_policy_contract",
    "rotate_scim_secret_set",
    "scim_secret_set_digest",
    "subject_is_retired",
    "subject_key",
    "subjects_in",
    "suites_for_principal",
    "suites_for_principal_in_state",
    "validate_document",
    "verified_policy",
    "verified_state",
    "verify_scim_authorization_contract",
    "verify_scim_secret_set",
]
