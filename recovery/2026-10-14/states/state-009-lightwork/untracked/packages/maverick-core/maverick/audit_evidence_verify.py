"""Standalone verifier for ``lightwork.audit-evidence.v1`` packets.

This module deliberately imports no Lightwork code.  Supply the publisher's
Ed25519 public key out of band; a key carried only inside its own packet is not
identity evidence.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "lightwork.audit-evidence.v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_SIG = re.compile(r"^[0-9a-f]{128}$")
_KEY = re.compile(r"^[0-9a-f]{16}$")
_PUB = re.compile(r"^[0-9a-f]{64}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SELECTOR = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_MAX_BUNDLE_BYTES = 128 * 1024 * 1024
_MAX_SEGMENTS = 4000
_MAX_ROWS = 100_000
_MAX_ANCHOR_ROWS = 10_000
_MAX_PUBLIC_KEYS = 256
_BUNDLE_KEYS = {
    "schema", "generated_at", "tenant", "selection", "segments", "anchors",
    "public_keys", "matches", "summary", "limitations", "signature",
}
_SEGMENT_KEYS = {"day", "row_count", "tip_hash", "content_sha256", "rows"}


class EvidenceVerificationError(ValueError):
    """The packet is malformed, incomplete, or cryptographically invalid."""


def _canonical_bundle(bundle: dict[str, Any]) -> bytes:
    return json.dumps(
        {key: value for key, value in bundle.items() if key != "signature"},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _canonical_rows(rows: list[dict[str, Any]]) -> bytes:
    return json.dumps(
        rows, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def _verify_signature(pubkey: str, signature: str, message: bytes) -> None:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey)).verify(
            bytes.fromhex(signature), message
        )
    except (ImportError, InvalidSignature, TypeError, ValueError) as exc:
        raise EvidenceVerificationError("Ed25519 signature verification failed") from exc


def _walk(rows: object, keys: dict[str, str], label: str) -> tuple[str, int]:
    if not isinstance(rows, list) or not rows:
        raise EvidenceVerificationError(f"{label} must contain signed rows")
    limit = _MAX_ANCHOR_ROWS if label == "anchor ledger" else _MAX_ROWS
    if len(rows) > limit:
        raise EvidenceVerificationError(f"{label} exceeds its verification bound")
    previous = ""
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise EvidenceVerificationError(f"{label} row {index} is not an object")
        row_hash = raw.get("hash")
        signature = raw.get("sig")
        key_id = raw.get("key_id")
        if (
            not isinstance(row_hash, str)
            or _SHA.fullmatch(row_hash) is None
            or not isinstance(signature, str)
            or _SIG.fullmatch(signature) is None
            or not isinstance(key_id, str)
            or _KEY.fullmatch(key_id) is None
            or raw.get("prev_hash") != previous
        ):
            raise EvidenceVerificationError(f"{label} row {index} chain fields are invalid")
        payload = {key: value for key, value in raw.items() if key not in {"hash", "sig"}}
        expected = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        if expected != row_hash or key_id not in keys:
            raise EvidenceVerificationError(f"{label} row {index} hash/key is invalid")
        _verify_signature(keys[key_id], signature, bytes.fromhex(row_hash))
        previous = row_hash
    return previous, len(rows)


def _matches(event: dict[str, Any], selection: dict[str, Any]) -> bool:
    goal = selection.get("goal_id")
    incident = selection.get("incident_id")
    action = selection.get("action_id")
    if goal is not None and event.get("goal_id") != goal:
        return False
    if incident is not None and event.get("incident_id") != incident:
        return False
    if action is not None and action not in {
        event.get("action_id"),
        event.get("approval_event_id"),
        event.get("signoff_event_id"),
        event.get("event_id"),
        event.get("resolution_id"),
    }:
        return False
    return True


def _valid_day(value: object) -> bool:
    if not isinstance(value, str) or _DAY.fullmatch(value) is None:
        return False
    try:
        return dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _validate_selection(value: object) -> dict[str, Any]:
    keys = {
        "day", "all_days", "since", "until", "goal_id", "incident_id",
        "action_id",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise EvidenceVerificationError("packet selection schema is invalid")
    day = value["day"]
    since = value["since"]
    until = value["until"]
    if day is not None and not _valid_day(day):
        raise EvidenceVerificationError("packet day selection is invalid")
    if since is not None and not _valid_day(since):
        raise EvidenceVerificationError("packet since selection is invalid")
    if until is not None and not _valid_day(until):
        raise EvidenceVerificationError("packet until selection is invalid")
    if since and until and since > until:
        raise EvidenceVerificationError("packet date range is reversed")
    if not isinstance(value["all_days"], bool):
        raise EvidenceVerificationError("packet all_days selection is invalid")
    modes = int(day is not None) + int(value["all_days"]) + int(
        since is not None or until is not None
    )
    if modes != 1:
        raise EvidenceVerificationError("packet date selection is ambiguous")
    goal = value["goal_id"]
    if goal is not None and (
        isinstance(goal, bool) or not isinstance(goal, int) or goal <= 0
    ):
        raise EvidenceVerificationError("packet goal selector is invalid")
    for field in ("incident_id", "action_id"):
        item = value[field]
        if item is not None and (
            not isinstance(item, str) or _SELECTOR.fullmatch(item) is None
        ):
            raise EvidenceVerificationError(f"packet {field} selector is invalid")
    return value


def verify(  # noqa: C901 - one linear offline forensic verification boundary
    bundle: object,
    *,
    trusted_pubkey_hex: str,
) -> dict[str, int | str]:
    if (
        not isinstance(bundle, dict)
        or set(bundle) != _BUNDLE_KEYS
        or bundle.get("schema") != SCHEMA
    ):
        raise EvidenceVerificationError("unsupported evidence schema")
    tenant = bundle.get("tenant")
    if not isinstance(tenant, str) or _SELECTOR.fullmatch(tenant) is None:
        raise EvidenceVerificationError("packet tenant is invalid")
    limitations = bundle.get("limitations")
    if (
        not isinstance(limitations, list)
        or not limitations
        or any(not isinstance(item, str) or not item for item in limitations)
    ):
        raise EvidenceVerificationError("packet limitations are invalid")
    if (
        not isinstance(trusted_pubkey_hex, str)
        or _PUB.fullmatch(trusted_pubkey_hex) is None
    ):
        raise EvidenceVerificationError("trusted publisher key must be 32-byte hex")
    signature = bundle.get("signature")
    if (
        not isinstance(signature, dict)
        or set(signature) != {"pubkey", "key_id", "sig"}
        or signature.get("pubkey") != trusted_pubkey_hex
    ):
        raise EvidenceVerificationError("packet signer does not match trusted publisher key")
    publisher_key_id = hashlib.sha256(bytes.fromhex(trusted_pubkey_hex)).hexdigest()[:16]
    if signature.get("key_id") != publisher_key_id:
        raise EvidenceVerificationError("packet publisher key id is malformed")
    sig = signature.get("sig")
    if not isinstance(sig, str) or _SIG.fullmatch(sig) is None:
        raise EvidenceVerificationError("outer signature is malformed")
    _verify_signature(trusted_pubkey_hex, sig, _canonical_bundle(bundle))

    keys = bundle.get("public_keys")
    if not isinstance(keys, dict) or not keys:
        raise EvidenceVerificationError("packet public-key registry is missing")
    if len(keys) > _MAX_PUBLIC_KEYS:
        raise EvidenceVerificationError("packet public-key registry exceeds its bound")
    for key_id, value in keys.items():
        if (
            not isinstance(key_id, str)
            or _KEY.fullmatch(key_id) is None
            or not isinstance(value, str)
            or _PUB.fullmatch(value) is None
            or hashlib.sha256(bytes.fromhex(value)).hexdigest()[:16] != key_id
        ):
            raise EvidenceVerificationError("packet public-key registry is invalid")

    segments = bundle.get("segments")
    if (
        not isinstance(segments, list)
        or not segments
        or len(segments) > _MAX_SEGMENTS
    ):
        raise EvidenceVerificationError("packet segment/selection inventory is missing")
    selection = _validate_selection(bundle.get("selection"))
    recomputed: list[dict[str, Any]] = []
    latest: dict[str, dict[str, Any]] = {}
    prior_day = ""
    source_row_count = 0
    for segment_index, segment in enumerate(segments):
        if (
            not isinstance(segment, dict)
            or set(segment) != _SEGMENT_KEYS
            or not _valid_day(segment.get("day"))
        ):
            raise EvidenceVerificationError("packet segment is malformed")
        segment_day = segment["day"]
        if segment_day <= prior_day:
            raise EvidenceVerificationError("packet segments are duplicated or unsorted")
        prior_day = segment_day
        if selection["day"] is not None and segment_day != selection["day"]:
            raise EvidenceVerificationError("packet segment is outside day selection")
        if selection["since"] is not None and segment_day < selection["since"]:
            raise EvidenceVerificationError("packet segment is before range selection")
        if selection["until"] is not None and segment_day > selection["until"]:
            raise EvidenceVerificationError("packet segment is after range selection")
        rows = segment.get("rows")
        tip, count = _walk(rows, keys, f"segment {segment_index}")
        if (
            segment.get("tip_hash") != tip
            or segment.get("row_count") != count
            or segment.get("content_sha256")
            != hashlib.sha256(_canonical_rows(rows)).hexdigest()
        ):
            raise EvidenceVerificationError("packet segment commitment is invalid")
        source_row_count += count
        if source_row_count > _MAX_ROWS:
            raise EvidenceVerificationError("packet source rows exceed verification bound")
        for row_index, event in enumerate(rows):
            if _matches(event, selection):
                recomputed.append(
                    {"segment": segment_index, "row": row_index, "hash": event["hash"]}
                )

    anchors = bundle.get("anchors")
    if (
        not isinstance(anchors, dict)
        or set(anchors) != {"status", "rows"}
        or anchors.get("status") not in {
        "verified",
        "current_day_only",
        }
    ):
        raise EvidenceVerificationError("packet anchor status is invalid")
    anchor_rows = anchors.get("rows")
    if anchors["status"] == "verified" and not anchor_rows:
        raise EvidenceVerificationError("verified anchor status lacks an anchor ledger")
    if anchors["status"] == "current_day_only" and anchor_rows:
        raise EvidenceVerificationError("current-day anchor status carries a ledger")
    if anchor_rows:
        _walk(anchor_rows, keys, "anchor ledger")
        for row in anchor_rows:
            if row.get("kind") == "anchor" and isinstance(row.get("day"), str):
                latest[row["day"]] = row
    generated_at = bundle.get("generated_at")
    try:
        if not isinstance(generated_at, str) or not generated_at.endswith("Z"):
            raise ValueError
        generated = dt.datetime.fromisoformat(generated_at[:-1] + "+00:00")
        if generated.tzinfo is None:
            raise ValueError
        generated_day = generated.astimezone(dt.timezone.utc).date().isoformat()
    except ValueError as exc:
        raise EvidenceVerificationError("packet generated_at is invalid") from exc
    for segment in segments:
        anchor = latest.get(segment["day"])
        if segment["day"] < generated_day and anchor is None:
            raise EvidenceVerificationError(
                "completed packet segment lacks an anchor commitment"
            )
        if anchor is not None:
            if (
                anchor.get("tip_hash") != segment["tip_hash"]
                or anchor.get("row_count") != segment["row_count"]
            ):
                raise EvidenceVerificationError(
                    "packet segment disagrees with anchor ledger"
                )
    if bundle.get("matches") != recomputed:
        raise EvidenceVerificationError("selector result omits or adds evidence rows")
    summary = bundle.get("summary")
    source_rows = sum(int(segment["row_count"]) for segment in segments)
    if not isinstance(summary, dict) or summary != {
        "segment_count": len(segments),
        "source_row_count": source_rows,
        "match_count": len(recomputed),
    }:
        raise EvidenceVerificationError("packet summary is inconsistent")
    return {
        "schema": SCHEMA,
        "segments": len(segments),
        "source_rows": source_rows,
        "matches": len(recomputed),
    }


def load_bundle(path: str | Path) -> object:
    """Read one untrusted JSON packet with a fixed byte and parse bound."""
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(_MAX_BUNDLE_BYTES + 1)
        if len(raw) > _MAX_BUNDLE_BYTES:
            raise EvidenceVerificationError("packet exceeds the verification byte bound")
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise EvidenceVerificationError("packet is not bounded valid JSON") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--key", required=True)
    args = parser.parse_args(argv)
    try:
        value = load_bundle(args.bundle)
        result = verify(value, trusted_pubkey_hex=args.key)
    except (
        OSError,
        EvidenceVerificationError,
    ) as exc:
        parser.exit(1, f"audit-evidence: REFUSED: {exc}\n")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
