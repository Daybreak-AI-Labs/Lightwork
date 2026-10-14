"""Workspace-owned, bounded input examples for safe workflow dry runs."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable

from maverick import crypto_at_rest
from maverick.file_lock import (
    atomic_read_bytes,
    atomic_write_bytes,
    cross_process_lock,
    ensure_private_directory,
)
from maverick.paths import data_dir

MAX_CASES_PER_FLOW = 20
MAX_CASE_NAME_LENGTH = 80
MAX_CASE_DATA_BYTES = 65_536
_MAX_STORE_BYTES = MAX_CASES_PER_FLOW * (MAX_CASE_DATA_BYTES + 4096)
_IDENTITY_RE = re.compile(r"^[0-9a-f]{32}$")


class FlowTestInputsUnavailable(OSError):
    """Saved examples cannot be safely read or protected at rest."""


class FlowTestInputsAuditUnconfirmed(OSError):
    """A change was written but its activity record could not be confirmed."""


def _legacy_path(flow_id: str):
    # Old files have no owner or generation binding. Never read/migrate them
    # into a current flow: its ID may already have been deleted and reused.
    key = hashlib.sha256(flow_id.encode("utf-8")).hexdigest()
    return data_dir("flow-test-inputs", key + ".json")


def _key(flow_id: str, flow_revision: str, owner: str) -> str:
    if not flow_id or not flow_revision or not isinstance(owner, str):
        raise FlowTestInputsUnavailable("The saved workflow identity could not be confirmed.")
    return hashlib.sha256(json.dumps(
        [flow_id, flow_revision, owner], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _path(flow_id: str, flow_revision: str, owner: str):
    return data_dir("flow-test-inputs", _key(flow_id, flow_revision, owner) + ".json")


def _encryption_context(flow_id: str, flow_revision: str, owner: str) -> str:
    return "lightwork.workflow-test-inputs.v2:" + _key(flow_id, flow_revision, owner)


def _empty(flow_revision: str, owner: str) -> dict:
    return {"schema": 2, "flow_revision": flow_revision, "owner": owner, "cases": {}}


def _reject_json_constant(_constant: str):
    raise ValueError("Saved workflow examples contain an invalid number.")


def _read(path, flow_id: str, flow_revision: str, owner: str) -> dict:
    if not path.exists():
        return _empty(flow_revision, owner)
    if path.stat().st_size > _MAX_STORE_BYTES:
        raise FlowTestInputsUnavailable("Saved workflow examples are too large to read safely.")
    raw = atomic_read_bytes(path)
    if len(raw) > _MAX_STORE_BYTES:
        raise FlowTestInputsUnavailable("Saved workflow examples are too large to read safely.")
    if not crypto_at_rest.is_bound(raw):
        raise FlowTestInputsUnavailable(
            "Saved workflow examples could not be securely opened. Nothing was changed."
        )
    try:
        raw = crypto_at_rest.unseal_bound(raw, _encryption_context(flow_id, flow_revision, owner))
    except Exception as exc:
        raise FlowTestInputsUnavailable(
            "Saved workflow examples could not be securely opened. Nothing was changed."
        ) from exc
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise FlowTestInputsUnavailable(
            "Saved workflow examples could not be read safely. Nothing was changed."
        ) from exc
    if (not isinstance(value, dict) or value.get("schema") != 2
            or value.get("flow_revision") != flow_revision or value.get("owner") != owner
            or not isinstance(value.get("cases"), dict)):
        raise FlowTestInputsUnavailable("Saved workflow examples could not be read safely. Nothing was changed.")
    if len(value["cases"]) > MAX_CASES_PER_FLOW:
        raise FlowTestInputsUnavailable("Saved workflow examples exceed the supported limit. Nothing was changed.")
    for identity, row in value["cases"].items():
        name = row.get("name") if isinstance(row, dict) else None
        created_at = row.get("created_at") if isinstance(row, dict) else None
        updated_at = row.get("updated_at") if isinstance(row, dict) else None
        fingerprint = row.get("fingerprint") if isinstance(row, dict) else None
        if (
            not isinstance(identity, str)
            or not _IDENTITY_RE.fullmatch(identity)
            or not isinstance(row, dict)
            or row.get("id") != identity
            or not isinstance(name, str)
            or not name.strip()
            or len(name) > MAX_CASE_NAME_LENGTH
            or any(ord(ch) < 32 for ch in name)
            or not isinstance(row.get("data"), dict)
            or type(row.get("revision")) is not int
            or row["revision"] < 1
            or not isinstance(row.get("created_by"), str)
            or len(row["created_by"]) > 200
            or not isinstance(fingerprint, str)
            or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
            or type(created_at) not in (int, float)
            or not math.isfinite(created_at)
            or type(updated_at) not in (int, float)
            or not math.isfinite(updated_at)
            or created_at <= 0
            or updated_at < created_at
        ):
            raise FlowTestInputsUnavailable("Saved workflow examples could not be read safely. Nothing was changed.")
        try:
            data_size = len(
                json.dumps(row["data"], ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                .encode("utf-8")
            )
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise FlowTestInputsUnavailable("Saved workflow examples could not be read safely. Nothing was changed.") from exc
        if data_size > MAX_CASE_DATA_BYTES:
            raise FlowTestInputsUnavailable("Saved workflow examples could not be read safely. Nothing was changed.")
    return value


def _public_case(row: dict) -> dict:
    return {key: value for key, value in row.items() if key != "fingerprint"}


def _with_store(
    flow_id: str, flow_revision: str, owner: str,
    fn: Callable[[dict], tuple[object, bool]], *, after_save=None,
):
    path = _path(flow_id, flow_revision, owner)
    ensure_private_directory(path.parent)
    with cross_process_lock(path.with_suffix(".lock"), strict=True):
        value = _read(path, flow_id, flow_revision, owner)
        result, changed = fn(value)
        if changed:
            plaintext = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            try:
                sealed = crypto_at_rest.seal_bound(
                    plaintext, _encryption_context(flow_id, flow_revision, owner)
                )
            except Exception as exc:
                raise FlowTestInputsUnavailable(
                    "Saved workflow examples could not be protected. Nothing was saved."
                ) from exc
            if not crypto_at_rest.is_bound(sealed):
                raise FlowTestInputsUnavailable("Saved workflow examples could not be protected. Nothing was saved.")
            atomic_write_bytes(path, sealed)
            if after_save is not None:
                try:
                    after_save()
                except Exception as exc:
                    raise FlowTestInputsAuditUnconfirmed(
                        "The saved example changed, but its activity record could not be confirmed. Refresh before trying again."
                    ) from exc
        return result


def _set_revision(rows: list[dict], flow_revision: str, owner: str) -> str:
    material = [
        {"id": row["id"], "revision": row["revision"]}
        for row in sorted(rows, key=lambda item: item["id"])
    ]
    return hashlib.sha256(
        json.dumps([flow_revision, owner, material], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def case_snapshot(flow_id: str, *, flow_revision: str, owner: str) -> tuple[list[dict], str]:
    def read(db):
        rows = sorted(
            (_public_case(row) for row in db["cases"].values()),
            key=lambda row: (row["created_at"], row["id"]),
        )
        return (rows, _set_revision(rows, flow_revision, owner)), False

    return _with_store(flow_id, flow_revision, owner, read)


def save_case(
    flow_id: str,
    *,
    flow_revision: str,
    owner: str,
    identity: str,
    name: str,
    data: dict,
    actor: str,
    expected_revision: str,
    after_save: Callable[[], None] | None = None,
) -> dict:
    name = name.strip()
    if not _IDENTITY_RE.fullmatch(identity):
        raise ValueError("Refresh the test examples before saving this one.")
    if not name or len(name) > MAX_CASE_NAME_LENGTH or any(ord(ch) < 32 for ch in name):
        raise ValueError(f"Give this example a name of 1 to {MAX_CASE_NAME_LENGTH} characters.")
    try:
        name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Give this example a name using valid text.") from exc
    if not isinstance(data, dict):
        raise ValueError("The saved example needs a set of workflow inputs.")
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("The person saving this example could not be verified.")
    try:
        actor.encode("utf-8")
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        encoded_size = len(encoded.encode("utf-8"))
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("These inputs cannot be saved as a test example.") from exc
    if encoded_size > MAX_CASE_DATA_BYTES:
        raise ValueError("This example is too large to save. Keep its inputs under 64 KB.")
    fingerprint = hashlib.sha256(
        json.dumps(
            {"flow_id": flow_id, "name": name, "data": data},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    def mutate(db):
        rows = list(db["cases"].values())
        prior = db["cases"].get(identity)
        if prior is not None:
            if prior.get("fingerprint") != fingerprint:
                raise ValueError("This save reference already belongs to a different example.")
            return {
                "case": _public_case(prior),
                "replayed": True,
                "case_set_revision": _set_revision(rows, flow_revision, owner),
            }, False
        if _set_revision(rows, flow_revision, owner) != expected_revision:
            raise ValueError("The test examples changed. Refresh and review them before saving.")
        if len(rows) >= MAX_CASES_PER_FLOW:
            raise ValueError(f"A workflow can have up to {MAX_CASES_PER_FLOW} saved test examples.")
        if any(row["name"].casefold() == name.casefold() for row in rows):
            raise ValueError("An example with this name already exists. Choose a different name.")
        now = time.time()
        case = {
            "id": identity,
            "name": name,
            "data": json.loads(encoded),
            "revision": 1,
            "created_at": now,
            "updated_at": now,
            "created_by": actor[:200],
            "fingerprint": fingerprint,
        }
        db["cases"][identity] = case
        return {
            "case": _public_case(case),
            "replayed": False,
            "case_set_revision": _set_revision(list(db["cases"].values()), flow_revision, owner),
        }, True

    return _with_store(flow_id, flow_revision, owner, mutate, after_save=after_save)


def delete_case(
    flow_id: str,
    identity: str,
    *,
    flow_revision: str,
    owner: str,
    expected_revision: str,
    after_save: Callable[[], None] | None = None,
) -> dict:
    if not _IDENTITY_RE.fullmatch(identity):
        raise ValueError("Refresh the test examples and try again.")

    def mutate(db):
        rows = list(db["cases"].values())
        if _set_revision(rows, flow_revision, owner) != expected_revision:
            raise ValueError("The test examples changed. Refresh and review them before removing one.")
        case = db["cases"].get(identity)
        if case is None:
            raise ValueError("This test example is no longer saved. Refresh the list.")
        del db["cases"][identity]
        return {"deleted": identity, "case_set_revision": _set_revision(list(db["cases"].values()), flow_revision, owner)}, True

    return _with_store(flow_id, flow_revision, owner, mutate, after_save=after_save)


def purge_flow_cases(flow_id: str, *, flow_revision: str, owner: str) -> None:
    """Remove this generation and its unbound legacy file after confirmed deletion."""
    path = _path(flow_id, flow_revision, owner)
    legacy = _legacy_path(flow_id)
    for target in (path, legacy):
        with cross_process_lock(target.with_suffix(".lock"), strict=True):
            target.unlink(missing_ok=True)


__all__ = [
    "MAX_CASES_PER_FLOW",
    "MAX_CASE_DATA_BYTES",
    "FlowTestInputsAuditUnconfirmed",
    "case_snapshot",
    "delete_case",
    "purge_flow_cases",
    "save_case",
]
