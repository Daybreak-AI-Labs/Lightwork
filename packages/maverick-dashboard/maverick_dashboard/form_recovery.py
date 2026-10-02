"""Owner decisions for accepted public-form rows that cannot start their target.

The received queue is never rewritten. A private append-only decision journal,
bound to the original row and trigger generation, is checked at delivery time.
Each decision also requires a signed audit witness before it takes effect.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager


class FormRecoveryUnavailable(RuntimeError):
    """A decision cannot be proved from durable, intact evidence."""


class FormRecoveryConflict(RuntimeError):
    """The row or binding changed before the owner's decision."""


def _path(token: str):
    from maverick.paths import data_dir

    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return data_dir("form-recovery") / f"{digest}.ndjson"


def _marker(path):
    return path.with_name(path.name + ".accepted")


@contextmanager
def locked(token: str):
    """Serialize a disposition with delivery, including other app processes."""
    from maverick.file_lock import cross_process_lock, ensure_private_directory

    path = _path(token)
    ensure_private_directory(path.parent)
    with cross_process_lock(path, strict=True):
        yield


def fields_digest(fields: dict) -> str:
    encoded = json.dumps(fields, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _row_fields(event: dict) -> dict:
    return {key: value for key, value in event.items() if key not in {"_seq", "_ts"}}


def _signed_receipt(count: int, tip: str) -> bytes:
    from maverick.audit.signing import sign_detached_digest

    state = {"count": count, "last_hash": tip}
    state["signature"] = sign_detached_digest(fields_digest(state))
    return (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode()


def decisions(token: str, generation: str) -> dict[int, dict]:
    """Read and validate the complete decision chain; caller holds ``locked``."""
    from maverick.audit.signing import trusted_audit_public_keys, verify_detached_digest
    from maverick.file_lock import atomic_read_bytes, ensure_private_file

    path = _path(token)
    if not path.exists():
        if _marker(path).exists():
            raise FormRecoveryUnavailable("form decisions are unavailable")
        return {}
    if not _marker(path).exists():
        raise FormRecoveryUnavailable("form decision marker is unavailable")
    if path.stat().st_size > 4 * 1024 * 1024:
        raise FormRecoveryUnavailable("form decisions exceed the safety limit")
    if not path.stat().st_size:
        raise FormRecoveryUnavailable("form decisions are unavailable")
    previous = "0" * 64
    output: dict[int, dict] = {}
    seen: set[tuple[str, int]] = set()
    try:
        ensure_private_file(_marker(path), 0o600)
        marker = json.loads(atomic_read_bytes(_marker(path)))
        if not isinstance(marker, dict):
            raise ValueError("decision marker is malformed")
        marker_signature = marker.pop("signature", None)
        if (type(marker.get("count")) is not int or marker["count"] < 1
                or not isinstance(marker.get("last_hash"), str)
                or not isinstance(marker_signature, dict)):
            raise ValueError("decision marker is malformed")
        raw = atomic_read_bytes(path)
        if not raw.endswith(b"\n"):
            raise ValueError("incomplete decision row")
        trusted_keys = trusted_audit_public_keys()
        marker_digest = fields_digest(marker)
        if (marker_signature.get("digest") != marker_digest
                or not verify_detached_digest(
                    trusted_keys.get(str(marker_signature.get("key_id") or ""), ""),
                    marker_digest, str(marker_signature.get("signature") or ""),
                )):
            raise ValueError("decision marker signature is invalid")
        count = 0
        for line in raw.decode("utf-8").splitlines():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("decision row is not an object")
            digest = row.pop("hash", "")
            signature = row.pop("signature", None)
            if (row.get("previous_hash") != previous
                    or row.get("decision") != "do_not_run"
                    or not isinstance(row.get("sequence"), int)
                    or row["sequence"] < 1
                    or not isinstance(row.get("generation"), str)
                    or not isinstance(row.get("fields_sha256"), str)
                    or not isinstance(row.get("reason"), str)
                    or not isinstance(row.get("actor"), str)
                    or not isinstance(row.get("at"), (int, float))):
                raise ValueError("decision row is malformed")
            expected = fields_digest(row)
            if (digest != expected or not isinstance(signature, dict)
                    or signature.get("digest") != digest
                    or signature.get("algorithm") != "Ed25519-SHA256"
                    or signature.get("message_version") != "maverick-detached-digest/v1"
                    or not isinstance(signature.get("key_id"), str)
                    or not isinstance(signature.get("signature"), str)
                    or not verify_detached_digest(
                        trusted_keys.get(signature["key_id"], ""), digest,
                        signature["signature"],
                    )):
                raise ValueError("decision chain is damaged")
            key = (row["generation"], row["sequence"])
            if key in seen:
                raise ValueError("duplicate form decision")
            seen.add(key)
            count += 1
            previous = digest
            if row["generation"] == generation:
                output[row["sequence"]] = {**row, "hash": digest,
                                           "signature": signature}
        if marker["count"] != count or marker["last_hash"] != previous:
            raise ValueError("decision journal is incomplete")
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise FormRecoveryUnavailable("form decisions cannot be verified") from exc
    return output


def abandon(token: str, generation: str, event: dict, *, expected_fields_sha256: str,
            reason: str, actor: str) -> dict:
    """Witness and append an explicit decision; caller holds ``locked``."""
    from maverick.audit import record
    from maverick.audit.signing import sign_detached_digest
    from maverick.file_lock import atomic_create_bytes, atomic_write_bytes, open_private_append

    sequence = event.get("_seq")
    if type(sequence) is not int or sequence < 1:
        raise FormRecoveryConflict("This submission's position changed. Refresh it.")
    actual_digest = fields_digest(_row_fields(event))
    if actual_digest != expected_fields_sha256:
        raise FormRecoveryConflict("This submission changed. Refresh it before deciding.")
    existing = decisions(token, generation)
    if sequence in existing:
        raise FormRecoveryConflict("This submission already has a decision. Refresh it.")
    reason = reason.strip()
    if len(reason) < 10 or len(reason) > 500:
        raise FormRecoveryConflict("Explain the decision in 10 to 500 characters.")
    path = _path(token)
    previous = "0" * 64
    count = 0
    if path.exists():
        # The complete chain was verified above. Only its final hash is needed.
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                previous = json.loads(line)["hash"]
                count += 1
    row = {
        "decision": "do_not_run", "sequence": sequence,
        "generation": generation, "fields_sha256": actual_digest,
        "reason": reason, "actor": actor[:120], "at": time.time(),
        "previous_hash": previous,
    }
    row["hash"] = fields_digest(row)
    try:
        row["signature"] = sign_detached_digest(row["hash"])
    except (OSError, RuntimeError, ValueError) as exc:
        raise FormRecoveryUnavailable("This decision could not be signed.") from exc
    witnessed = record(
        "form_submission_abandonment_authorized", agent=actor[:120],
        _require_signed=True, sequence=sequence, generation=generation,
        form_sha256=hashlib.sha256(token.encode()).hexdigest(),
        fields_sha256=actual_digest, decision_sha256=row["hash"],
    )
    if not witnessed:
        raise FormRecoveryUnavailable("A signed record of this decision could not be saved.")
    try:
        # Marker first: if power is lost before the journal append, replay
        # refuses the missing evidence instead of silently losing a decision.
        if count == 0:
            atomic_create_bytes(_marker(path), _signed_receipt(0, previous))
        fd = open_private_append(path, require_private_parent=True)
        with os.fdopen(fd, "ab") as handle:
            handle.write((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        atomic_write_bytes(_marker(path), _signed_receipt(count + 1, row["hash"]))
    except OSError as exc:
        raise FormRecoveryUnavailable("The decision could not be saved.") from exc
    return row


def without_abandoned(token: str, generation: str, events: list[dict],
                      event_ids: list[str] | None) -> tuple[list[dict], list[str] | None, int]:
    """Verify decisions against original rows before suppressing any delivery."""
    recorded = decisions(token, generation)
    if not recorded:
        return events, event_ids, 0
    kept: list[dict] = []
    kept_ids: list[str] | None = [] if event_ids is not None else None
    skipped = 0
    for index, event in enumerate(events):
        raw_seq = event.get("_seq")
        seq = int(raw_seq) if isinstance(raw_seq, str) and raw_seq.isdecimal() else raw_seq
        decision = recorded.get(seq) if type(seq) is int else None
        if decision is not None:
            if (decision["fields_sha256"] != fields_digest(_row_fields(event))
                    or event_ids is None or event_ids[index] != str(seq)):
                raise FormRecoveryUnavailable("A form decision no longer matches its submission.")
            skipped += 1
            continue
        kept.append(event)
        if kept_ids is not None:
            kept_ids.append(event_ids[index])
    return kept, kept_ids, skipped


def already_started(trigger: dict, event: dict, world) -> bool:
    """Refuse a decision if any known admission already owns this submission."""
    from maverick.automation_events import _flatten

    from maverick_dashboard.event_poll import _event_key, _trigger_key

    sequence = event["_seq"]
    generation = _trigger_key(trigger)
    polled_event = _flatten(event)
    keys = {
        _event_key(generation, polled_event, str(sequence)),
        _event_key(generation, polled_event),
    }
    if trigger.get("flow"):
        import hashlib as _hashlib

        from maverick.flow import store as flow_store

        legacy_id = polled_event.get("id") or polled_event.get("guid") or polled_event.get("uid")
        if legacy_id is None:
            legacy_id = _hashlib.sha1(
                json.dumps(polled_event, sort_keys=True, default=str).encode(),
                usedforsecurity=False,
            ).hexdigest()[:16]
        keys.add(f"{trigger['name']}:{legacy_id}")
        return any(
            flow_store.find_run_by_idem(trigger["flow"], key, within=None) is not None
            for key in keys
        )
    from maverick import event_admission

    return any(event_admission.admitted(world, key) for key in keys)
