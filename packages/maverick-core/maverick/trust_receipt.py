"""Trust receipts: bearer-verifiable evidence for ONE audited decision.

An attestation bundle (``attestation.py``) proves properties of a whole
window. A trust receipt is the unit below it: a portable artifact for a single
audit row -- "tool X was denied under rule R for principal P at time T", or
"tool X ran" -- that a third party can verify offline with the standalone
``trust_receipt_verify.py`` and a public key obtained out of band.

What the minter does, in order:

1. refuses if the day's chain does not verify (a receipt must never launder a
   tampered log into something that looks certified);
2. optionally publishes -- or reuses -- a signed **checkpoint** committing to
   the day's tip (``--checkpoint-dir``), the one commitment a verifier can
   compare against a digest the operator published out of band;
3. locates the row by hash, embeds it with every row that follows it up to the
   committed tip, and derives the claim from the row with the *verifier's own*
   function so the two can never disagree;
4. signs the whole receipt with this instance's audit key.

It never mutates the audit files. Publishing a checkpoint writes only to the
checkpoint directory, which must be disjoint from the audit directory.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from . import trust_receipt_verify as _verify

RECEIPT_KIND = _verify.RECEIPT_KIND
SCHEMA_VERSION = _verify.SCHEMA_VERSION
VerifyResult = _verify.VerifyResult

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _audit_root(audit_dir: Path | str | None) -> Path:
    if audit_dir is not None:
        return Path(audit_dir)
    from .paths import data_dir
    return Path(data_dir("audit"))


def _day_lines(path: Path) -> list[str]:
    """Non-empty NDJSON lines of a day-file, decrypting a sealed segment."""
    from .audit.sealing import segment_text
    return [ln for ln in segment_text(path, fail_soft=False).splitlines() if ln.strip()]


def _hash_of(line: str) -> str | None:
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return row.get("hash") if isinstance(row, dict) else None


def locate_row(audit_dir: Path | str | None, *, day: str,
               row_hash: str) -> tuple[dict, int, list[str]]:
    """The row with ``row_hash`` on ``day``: ``(row, position, lines after it)``.

    ``position`` is 1-indexed over non-empty lines, the same numbering the
    anchor ledger and checkpoints use for ``row_count``.
    """
    if not _DAY_RE.match(day):
        raise LookupError(f"day must be YYYY-MM-DD, got {day!r}")
    path = _audit_root(audit_dir) / f"{day}.ndjson"
    if not path.exists():
        raise LookupError(f"no audit day-file for {day}")
    lines = _day_lines(path)
    hits = [i for i, ln in enumerate(lines) if _hash_of(ln) == row_hash]
    if not hits:
        raise LookupError(f"no row with hash {row_hash[:12]}... on {day}")
    if len(hits) > 1:
        raise LookupError(f"row hash {row_hash[:12]}... appears {len(hits)} times on {day}")
    index = hits[0]
    return json.loads(lines[index]), index + 1, lines[index + 1:]


def _tip_after(row_hash: str, suffix: list[str]) -> str:
    tip = row_hash
    for line in suffix:
        tip = _hash_of(line) or tip
    return tip


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _anchor_for_day(root: Path, day: str) -> dict | None:
    """The latest anchor row for ``day`` in the co-located ledger, if any."""
    from .audit.signing import ANCHOR_FILENAME
    ledger = root / ANCHOR_FILENAME
    if not ledger.exists():
        return None
    latest = None
    for line in _day_lines(ledger):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("kind") == "anchor" and rec.get("day") == day:
            latest = rec
    return latest


def _checkpoint_commitment(root: Path, checkpoint_dir: Path | str, day: str) -> dict:
    """Publish (or reuse) the signed checkpoint that commits to ``day``'s tip."""
    from .audit.checkpoints import CHECKPOINT_SCHEMA, publish_checkpoint
    from .audit.signing import trusted_audit_public_keys
    path = publish_checkpoint(
        root, checkpoint_dir, day=day,
        trusted_pubkeys=list(trusted_audit_public_keys().values()))
    text = path.read_text(encoding="utf-8")
    return {
        "source": "checkpoint", "schema": CHECKPOINT_SCHEMA, "filename": path.name,
        "sequence": json.loads(text)["checkpoint_sequence"],
        # The digest an operator publishes out of band is over the file's exact
        # bytes; the raw line is embedded so a verifier can recompute it
        # without re-serialising (the on-disk line is insertion-ordered).
        "raw_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "raw_line": text,
        "record": json.loads(text),
    }


def _commitment(root: Path, day: str, checkpoint_dir: Path | str | None) -> dict | None:
    if checkpoint_dir is not None:
        return _checkpoint_commitment(root, checkpoint_dir, day)
    if day < _today():
        anchor = _anchor_for_day(root, day)
        if anchor is not None:
            return {"source": "anchor_ledger", "record": anchor}
    return None


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("maverick-agent")
    except Exception:
        return ""


def build(*, audit_dir: Path | str | None = None, day: str, row_hash: str,
          checkpoint_dir: Path | str | None = None, include_suffix: bool = True,
          now: float | None = None) -> dict:
    """Assemble an unsigned receipt for the row ``row_hash`` recorded on ``day``.

    With ``checkpoint_dir`` the receipt commits to the checkpoint's tip, and
    the rows after the cited one are trimmed to that committed prefix, so a
    day that keeps growing after publication still yields a receipt whose
    chain and commitment agree.
    """
    from .audit.signing import active_key_is_offhost, verify_chain
    root = _audit_root(audit_dir)
    if not _DAY_RE.match(day):
        raise LookupError(f"day must be YYYY-MM-DD, got {day!r}")
    if not (root / f"{day}.ndjson").exists():
        # No day is not a broken day: say which, so an operator does not go
        # hunting for tampering that never happened.
        raise LookupError(f"no audit day-file for {day}")
    breaks = verify_chain(root / f"{day}.ndjson")
    if breaks:
        first = breaks[0]
        raise ValueError(
            f"refusing to mint from a broken chain: {first.reason} at line "
            f"{first.line_no} ({first.detail})")
    commitment = _commitment(root, day, checkpoint_dir)
    row, position, suffix = locate_row(root, day=day, row_hash=row_hash)
    if commitment is not None:
        committed = int(commitment["record"].get("row_count") or 0)
        if position > committed:
            raise ValueError(
                "the commitment predates the cited row; publish a newer checkpoint")
        suffix = suffix[: committed - position]
    tip = _tip_after(row["hash"], suffix)
    if commitment is not None and commitment["record"].get("tip_hash") != tip:
        raise ValueError("the commitment does not agree with the chain on disk")
    return {
        "kind": RECEIPT_KIND,
        "schema_version": SCHEMA_VERSION,
        "issued_at": now if now is not None else time.time(),
        "issuer": {
            "key_custody": "offhost" if active_key_is_offhost() else "colocated",
            "version": _version(),
        },
        "claim": _verify.derive_claim(row),
        "row": row,
        "locator": {"day": day, "position": position, "hash": row["hash"]},
        "chain": {
            "suffix_ndjson": suffix if include_suffix else [],
            "tip_hash": tip,
            "row_count": position + len(suffix),
        },
        "commitment": commitment,
    }


def sign(receipt: dict) -> dict:
    """Attach this instance's Ed25519 audit signature over the canonical body.

    Raises without ``cryptography``: an unsigned receipt is not a weaker
    receipt, it is not one at all.
    """
    from .audit.signing import _have_crypto, _load_or_create_keypair
    if not _have_crypto():
        raise RuntimeError(
            "trust receipts require 'cryptography' "
            "(install 'maverick-agent[audit-signing]'): receipts are always signed.")
    from cryptography.hazmat.primitives.asymmetric import ed25519
    priv, pub, key_id = _load_or_create_keypair()
    sig = ed25519.Ed25519PrivateKey.from_private_bytes(priv).sign(
        _verify.canonical_bundle_bytes(receipt))
    return {**receipt, "signature": {
        "pubkey": pub.hex(), "key_id": key_id, "sig": sig.hex()}}


def export(out_path: Path | str, **build_kwargs) -> Path:
    """Build, sign and atomically write a receipt; record its issuance."""
    receipt = sign(build(**build_kwargs))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    from .audit import EventKind, audit_event

    # A strict audit refusal must happen before the artifact is published.
    # Ordinary writer outages remain fail-soft through audit_event.
    audit_event(
        EventKind.TRUST_RECEIPT_ISSUED, agent="trust_receipt",
        day=receipt["locator"]["day"], row_hash=receipt["locator"]["hash"],
        committed=receipt["commitment"] is not None,
        key_id=receipt["signature"]["key_id"],
    )
    from .file_lock import atomic_write_text
    atomic_write_text(out, json.dumps(receipt, indent=2, default=str), mode=0o600)
    return out


def verify(receipt_path: Path | str, *, trusted_keys, anchor_digest: str | None = None,
           checkpoint_dir: Path | str | None = None) -> VerifyResult:
    """Verify a receipt on disk. Thin wrapper over the standalone verifier."""
    try:
        receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return VerifyResult(False, _verify.DEPTH_NONE, f"unreadable receipt: {e}")
    return _verify.verify(receipt, trusted_keys=trusted_keys,
                          anchor_digest=anchor_digest, checkpoint_dir=checkpoint_dir)


def verifier_source() -> tuple[str, str]:
    """The standalone verifier's source text and its sha256, for hand-off."""
    path = Path(_verify.__file__)
    text = path.read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "RECEIPT_KIND", "SCHEMA_VERSION", "VerifyResult", "build", "export",
    "locate_row", "sign", "verifier_source", "verify",
]
