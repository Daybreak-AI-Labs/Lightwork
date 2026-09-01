"""Third-party verifier for a Lightwork trust receipt.

**This module imports nothing from maverick.** Standard library plus
``cryptography``, deliberately: a receipt is handed to someone who trusts
neither the operator nor the vendor -- an auditor, a regulator, a customer's
customer -- and they must be able to check it without installing our platform
or asking anyone's permission. ``maverick attest receipt export-verifier``
copies this exact file, and it runs on its own::

    python trust_receipt_verify.py receipt.json --key <publisher-pubkey-hex>
    python trust_receipt_verify.py receipt.json --key <hex> --anchor-digest <sha256>

A receipt is evidence about ONE audited decision: "tool X was denied under
rule R for principal P at time T", or "tool X ran". It carries the signed audit
row itself, the rows that followed it up to a chain tip, and (optionally) the
signed checkpoint that committed that tip. Verification re-derives everything:

* the envelope signature, under a key the verifier obtained OUT OF BAND;
* the row's own hash and signature (the decision really was recorded);
* the claim, recomputed from the row -- the receipt's human-readable claim is
  never trusted, it must equal what the row says;
* the hash links from the row to the committed tip (the row really sits in
  that chain at that position);
* the checkpoint record's signature and its agreement with the tip.

Three depths are reported and they are not synonyms. ``signed`` means the
issuer signed a genuine row. ``chained`` means the row provably links to a
tip the issuer committed to. ``anchored`` means that commitment matched a
digest the verifier brought from OUTSIDE the receipt (``--anchor-digest``, or
the checkpoint file itself via ``--checkpoint-dir``) -- the only depth at which
the issuer could not have quietly re-written history after the fact. An
issuer-signed commitment with nothing external to compare it to is still just
the issuer's word, and this tool says so.

Two honest limits. A receipt is point in time: a later GDPR erasure lawfully
rewrites and re-signs the day-file, so an old receipt stays self-consistent
but no longer matches the live log. And a key kept on the same host as the
log proves integrity against accidental edits, not tamper-evidence against an
actor who can read that key; the receipt's ``issuer.key_custody`` field is
surfaced so the reader can weigh it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1
RECEIPT_KIND = "maverick-trust-receipt"
CHECKPOINT_SCHEMA = "lightwork.audit.checkpoint.v2"

HOLDS = "HOLDS"
FAILS = "FAILS"
INDETERMINATE = "INDETERMINATE"
NOT_APPLICABLE = "NOT_APPLICABLE"

#: Depth reached by a verification run, weakest first.
DEPTH_NONE = "unverified"
DEPTH_SIGNED = "signed"
DEPTH_CHAINED = "chained"
DEPTH_ANCHORED = "anchored"

_KEY_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SIG_RE = re.compile(r"^[0-9a-f]{128}$")

# Audit kinds that record a decision NOT to act. Kept in step with
# maverick.audit.events by test_trust_receipt.py.
_DENIED_KINDS = frozenset({
    "governance_denied", "capability_denied", "sandbox_denied", "shield_block",
    "egress_blocked", "agent_trust_denied",
})
_NOT_GRANTED = "human approval not granted"

#: Row fields a claim is derived from. Obligation tags are optional and only
#: present on rows written by a deployment with compiled obligations.
_CLAIM_ROW_FIELDS = ("principal", "agent", "goal_id", "rule", "reason",
                     "obligation_id", "regulation", "article")


@dataclass
class ClaimResult:
    name: str
    status: str
    detail: str
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Only an affirmatively checked claim is ok. Unchecked is not ok."""
        return self.status == HOLDS


@dataclass
class VerifyResult:
    ok: bool
    depth: str
    reason: str
    claims: list[ClaimResult] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "depth": self.depth, "reason": self.reason,
            "claims": [
                {"name": c.name, "status": c.status, "detail": c.detail,
                 "notes": list(c.notes)} for c in self.claims
            ],
            "findings": list(self.findings),
        }


# ---- crypto -----------------------------------------------------------------

def _have_crypto() -> bool:
    try:
        import cryptography.hazmat.primitives.asymmetric.ed25519  # noqa: F401
    except Exception:
        return False
    return True


def verify_ed25519(pubkey_hex: str, sig_hex: str, message: bytes) -> bool:
    """True iff ``sig_hex`` is a valid Ed25519 signature over ``message``."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except Exception:  # pragma: no cover -- guarded by _have_crypto
        return False
    try:
        raw = bytes.fromhex(pubkey_hex)
        if len(raw) != 32:
            return False
        ed25519.Ed25519PublicKey.from_public_bytes(raw).verify(
            bytes.fromhex(sig_hex), message)
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def canonical_bundle_bytes(bundle: dict) -> bytes:
    """The exact bytes the issuer signed: every field but ``signature``."""
    return json.dumps(
        {k: v for k, v in bundle.items() if k != "signature"},
        sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")


def audit_row_hash(row: dict) -> str:
    """Recompute a signed audit row's content hash.

    Must match ``maverick.audit.signing`` byte for byte: default separators,
    sorted keys, ``ensure_ascii``. ``test_trust_receipt.py`` pins the two
    against the production signer so a drift in either fails a test instead
    of reading an honest row as forged.
    """
    payload = {k: v for k, v in row.items() if k not in ("hash", "sig")}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8"),
    ).hexdigest()


def key_id_of(pubkey_hex: str) -> str:
    """The 16-hex-char key id the signer stamps on every row."""
    return hashlib.sha256(bytes.fromhex(pubkey_hex)).hexdigest()[:16]


# ---- claim derivation --------------------------------------------------------

def derive_claim(row: dict) -> dict:
    """What this row says happened, computed from the row alone.

    The minter uses this same function, so a receipt whose stated claim differs
    from the derived one has been edited after minting -- or was minted by
    something that is not this code. Either way it FAILS.
    """
    kind = row.get("kind")
    if kind == "governance_denied" and row.get("reason") == _NOT_GRANTED:
        decision = "require_human_not_granted"
    elif kind in _DENIED_KINDS:
        decision = "denied"
    elif kind == "autonomy_gated":
        decision = "gated"
    elif kind == "tool_call":
        decision = "executed"
    elif kind == "consent_result":
        decision = str(row.get("decision") or "")
    elif kind == "approval_decision":
        decision = str(row.get("status") or "")
    else:
        decision = "recorded"
    claim = {
        "kind": kind, "decision": decision,
        "tool": row.get("tool") or row.get("name"),
        "occurred_at": row.get("ts"),
    }
    for name in _CLAIM_ROW_FIELDS:
        if row.get(name) is not None:
            claim[name] = row[name]
    return claim


# ---- checks ----------------------------------------------------------------

def _as_dict(value: object) -> dict:
    """A mapping or an empty one. Never raises -- a verdict, not a traceback."""
    return value if isinstance(value, dict) else {}


def _normalise_keys(trusted_keys) -> dict[str, str]:
    """``{key_id: pubkey_hex}`` for every well-formed trusted key."""
    out: dict[str, str] = {}
    for key in trusted_keys or ():
        hexkey = str(key or "").strip().lower()
        try:
            if len(bytes.fromhex(hexkey)) != 32:
                continue
        except ValueError:
            continue
        out[key_id_of(hexkey)] = hexkey
    return out


def _signed_row_problem(row: object, trusted: dict[str, str], label: str) -> str | None:
    """Why ``row`` is not a genuine signed audit row, or None if it is."""
    if not isinstance(row, dict):
        return f"{label}: not a JSON object"
    row_hash, sig, key_id = row.get("hash"), row.get("sig"), row.get("key_id")
    if not (isinstance(row_hash, str) and isinstance(sig, str)
            and isinstance(key_id, str)):
        return f"{label}: hash/sig/key_id missing or not strings"
    if not (_HASH_RE.match(row_hash) and _SIG_RE.match(sig) and _KEY_ID_RE.match(key_id)):
        return f"{label}: hash/sig/key_id are not well-formed hex"
    if audit_row_hash(row) != row_hash:
        return f"{label}: content does not match its recorded hash"
    pub = trusted.get(key_id)
    if pub is None:
        return f"{label}: signed by key {key_id}, which is not a trusted key"
    if not verify_ed25519(pub, sig, bytes.fromhex(row_hash)):
        return f"{label}: signature does not verify under the trusted key"
    return None


def _check_row(receipt: dict, trusted: dict[str, str]) -> ClaimResult:
    """Is the embedded audit row genuine, and is it the row the locator names?"""
    row = _as_dict(receipt.get("row"))
    if not row:
        return ClaimResult("row_integrity", FAILS, "the receipt carries no audit row")
    problem = _signed_row_problem(row, trusted, "row")
    if problem:
        return ClaimResult("row_integrity", FAILS, problem)
    locator = _as_dict(receipt.get("locator"))
    if locator.get("hash") != row.get("hash"):
        return ClaimResult("row_integrity", FAILS,
                           "the locator names a different row than the one embedded")
    return ClaimResult(
        "row_integrity", HOLDS,
        f"row {str(row.get('hash'))[:12]}... hashes to its content and is signed "
        f"by trusted key {row.get('key_id')}")


def _check_claim(receipt: dict) -> ClaimResult:
    """Does the stated claim equal what the row actually records?"""
    stated = _as_dict(receipt.get("claim"))
    derived = derive_claim(_as_dict(receipt.get("row")))
    if stated != derived:
        drifted = sorted(k for k in set(stated) | set(derived)
                         if stated.get(k) != derived.get(k))
        return ClaimResult(
            "claim_consistency", FAILS,
            "the stated claim does not match the row it cites",
            [f"differs in: {', '.join(drifted)}"])
    what = f"{derived.get('decision')} {derived.get('tool') or '(no tool)'}"
    if derived.get("rule"):
        what += f" under rule {derived['rule']}"
    return ClaimResult("claim_consistency", HOLDS, what)


def _check_chain(receipt: dict, trusted: dict[str, str]) -> ClaimResult:
    """Does the row provably link, row by row, to the committed tip?"""
    chain = _as_dict(receipt.get("chain"))
    row = _as_dict(receipt.get("row"))
    locator = _as_dict(receipt.get("locator"))
    if not chain:
        return ClaimResult("chain_linkage", INDETERMINATE,
                           "the receipt carries no chain commitment")
    suffix = chain.get("suffix_ndjson")
    tip, count, position = chain.get("tip_hash"), chain.get("row_count"), locator.get("position")
    if not isinstance(suffix, list) or not isinstance(count, int) or not isinstance(position, int):
        return ClaimResult("chain_linkage", FAILS, "chain commitment is malformed")
    if position + len(suffix) != count:
        if not suffix and position < count:
            return ClaimResult(
                "chain_linkage", INDETERMINATE,
                f"the receipt omits the {count - position} row(s) after the cited "
                "one, so the link to the tip cannot be walked")
        return ClaimResult(
            "chain_linkage", FAILS,
            f"position {position} + {len(suffix)} following row(s) != committed "
            f"row count {count}")
    prev = str(row.get("hash") or "")
    for n, line in enumerate(suffix, start=position + 1):
        try:
            nxt = json.loads(line)
        except (TypeError, ValueError):
            return ClaimResult("chain_linkage", FAILS, f"row {n}: malformed JSON")
        problem = _signed_row_problem(nxt, trusted, f"row {n}")
        if problem:
            return ClaimResult("chain_linkage", FAILS, problem)
        if nxt.get("prev_hash") != prev:
            return ClaimResult("chain_linkage", FAILS,
                               f"row {n}: prev_hash does not link to the row before it")
        prev = str(nxt["hash"])
    if prev != tip:
        return ClaimResult("chain_linkage", FAILS,
                           "walking the rows does not arrive at the committed tip")
    return ClaimResult(
        "chain_linkage", HOLDS,
        f"row {position} of {count} links through {len(suffix)} following row(s) "
        f"to tip {str(tip)[:12]}...")


def _checkpoint_problem(commitment: dict, record: dict, chain: dict,
                        locator: dict) -> str | None:
    if record.get("schema") != CHECKPOINT_SCHEMA:
        return f"checkpoint schema is not {CHECKPOINT_SCHEMA}"
    if record.get("record_type") != "checkpoint":
        return f"checkpoint record_type is {record.get('record_type')!r}, not 'checkpoint'"
    if record.get("prev_hash") != "":
        return "a checkpoint record must be a genesis row"
    raw_line = commitment.get("raw_line")
    if not isinstance(raw_line, str):
        return "checkpoint commitment carries no raw record line"
    try:
        if json.loads(raw_line) != record:
            return "the raw checkpoint line does not parse to the embedded record"
    except ValueError:
        return "the raw checkpoint line is not JSON"
    if hashlib.sha256(raw_line.encode("utf-8")).hexdigest() != commitment.get("raw_sha256"):
        return "raw_sha256 does not match the raw checkpoint line"
    return _agrees_problem(record, chain, locator)


def _agrees_problem(record: dict, chain: dict, locator: dict) -> str | None:
    if record.get("day") != locator.get("day"):
        return "the commitment is for a different day than the cited row"
    if record.get("tip_hash") != chain.get("tip_hash"):
        return "the commitment's tip does not match the receipt's chain tip"
    if record.get("row_count") != chain.get("row_count"):
        return "the commitment's row count does not match the receipt's chain"
    return None


def _check_commitment(receipt: dict, trusted: dict[str, str], *,
                      anchor_digest: str | None,
                      checkpoint_dir: Path | None) -> tuple[ClaimResult, bool]:
    """Is the tip committed to, and did that commitment match anything external?

    Returns the claim and whether an OUT-OF-BAND corroboration succeeded.
    """
    commitment = _as_dict(receipt.get("commitment"))
    if not commitment:
        return ClaimResult("commitment", INDETERMINATE,
                           "the receipt's tip is not committed to by any anchor"), False
    record = _as_dict(commitment.get("record"))
    source = commitment.get("source")
    problem = _signed_row_problem(record, trusted, "commitment record")
    if problem:
        return ClaimResult("commitment", FAILS, problem), False
    chain, locator = _as_dict(receipt.get("chain")), _as_dict(receipt.get("locator"))
    if source == "checkpoint":
        problem = _checkpoint_problem(commitment, record, chain, locator)
    elif source == "anchor_ledger":
        problem = ("anchor record is not an anchor row"
                   if record.get("kind") != "anchor" else
                   _agrees_problem(record, chain, locator))
    else:
        problem = f"unknown commitment source {source!r}"
    if problem:
        return ClaimResult("commitment", FAILS, problem), False
    return _corroborate(commitment, source, anchor_digest, checkpoint_dir)


def _corroborate(commitment: dict, source: str, anchor_digest: str | None,
                 checkpoint_dir: Path | None) -> tuple[ClaimResult, bool]:
    """Compare the (issuer-signed) commitment with what the verifier brought."""
    notes: list[str] = []
    if source == "anchor_ledger":
        notes.append("the anchor ledger lives beside the log and is signed by the "
                     "same key; it is the issuer's own commitment, not an "
                     "independent one")
        return ClaimResult("commitment", HOLDS,
                           "tip committed by the issuer's anchor ledger", notes), False
    digest = str(commitment.get("raw_sha256") or "")
    external = False
    wanted = (anchor_digest or "").strip().lower()
    if wanted:
        if wanted != digest:
            return ClaimResult(
                "commitment", FAILS,
                "the out-of-band checkpoint digest does not match this receipt's "
                "checkpoint (history was re-committed after the digest was "
                "published, or the receipt is not from this deployment)"), False
        external = True
        notes.append(f"checkpoint sha256 matches the out-of-band digest {wanted[:12]}...")
    if checkpoint_dir is not None:
        path = Path(checkpoint_dir) / str(commitment.get("filename") or "")
        try:
            on_disk = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as e:
            return ClaimResult("commitment", FAILS,
                               f"cannot read the checkpoint file {path.name}: {e}"), False
        if on_disk != digest:
            return ClaimResult(
                "commitment", FAILS,
                f"the checkpoint file {path.name} on disk differs from the one "
                "the receipt embeds"), False
        external = True
        notes.append(f"checkpoint file {path.name} on disk is byte-identical")
    if not external:
        notes.append("nothing external was supplied to compare the checkpoint "
                     "against; re-run with --anchor-digest or --checkpoint-dir "
                     "to reach depth 'anchored'")
    return ClaimResult(
        "commitment", HOLDS,
        f"tip committed by signed checkpoint #{commitment.get('sequence')}", notes), external


# ---- top level --------------------------------------------------------------

def _structural_problem(receipt: object, trusted: dict[str, str]) -> str | None:
    if not isinstance(receipt, dict):
        return "receipt is not a JSON object"
    if receipt.get("kind") != RECEIPT_KIND:
        return f"not a {RECEIPT_KIND} (kind={receipt.get('kind')!r})"
    version = receipt.get("schema_version")
    if version != SCHEMA_VERSION:
        # Refuse rather than guess: a newer receipt may carry checks this
        # verifier does not know how to run.
        return (f"receipt schema version {version!r} is not {SCHEMA_VERSION} "
                "(use the verifier shipped with the receipt)")
    if not _have_crypto():
        return "the 'cryptography' package is required to verify"
    sig = receipt.get("signature")
    if not isinstance(sig, dict) or not sig.get("sig"):
        return "receipt is unsigned"
    if not trusted:
        return ("a trusted public key is required: a key read out of the receipt "
                "proves only that somebody signed it, not who")
    embedded = str(sig.get("pubkey") or "").strip().lower()
    if embedded not in trusted.values():
        return "the receipt's signing key is not a trusted key (provenance fail)"
    if not verify_ed25519(embedded, str(sig.get("sig")), canonical_bundle_bytes(receipt)):
        return "signature does not verify over the receipt contents"
    return None


def verify(receipt: dict, *, trusted_keys, anchor_digest: str | None = None,
           checkpoint_dir: Path | str | None = None) -> VerifyResult:
    """Check a receipt against out-of-band keys, optionally corroborating it.

    ``ok`` is True only when the envelope verifies against a trusted key AND no
    claim FAILS. A receipt that is authentic but uncommitted is ``ok=True`` at
    depth ``signed``: it proves the issuer recorded the decision, and nothing
    about whether the log it sits in has since been rewritten. Read the depth.
    """
    trusted = _normalise_keys(trusted_keys)
    problem = _structural_problem(receipt, trusted)
    if problem:
        return VerifyResult(False, DEPTH_NONE, problem)

    claims = [_check_row(receipt, trusted), _check_claim(receipt),
              _check_chain(receipt, trusted)]
    commitment, external = _check_commitment(
        receipt, trusted, anchor_digest=anchor_digest,
        checkpoint_dir=Path(checkpoint_dir) if checkpoint_dir else None)
    claims.append(commitment)

    findings: list[str] = []
    custody = _as_dict(receipt.get("issuer")).get("key_custody")
    if custody == "colocated":
        findings.append("the issuer's signing key is kept on the same host as the "
                        "log: this is integrity against accidental edits, not "
                        "tamper-evidence against an actor who can read that key")
    findings.append("a receipt is point-in-time: a later lawful erasure re-signs "
                    "the day-file, after which the live log will not match this "
                    "receipt even though the receipt remains self-consistent")

    failed = [c.name for c in claims if c.status == FAILS]
    if failed:
        return VerifyResult(False, DEPTH_NONE if "row_integrity" in failed else DEPTH_SIGNED,
                            f"claim(s) FAILED: {', '.join(failed)}", claims, findings)
    chained = any(c.name == "chain_linkage" and c.status == HOLDS for c in claims)
    committed = commitment.status == HOLDS
    if chained and committed and external:
        depth = DEPTH_ANCHORED
    elif chained and committed:
        depth = DEPTH_CHAINED
    else:
        depth = DEPTH_SIGNED
    held = sum(1 for c in claims if c.status == HOLDS)
    return VerifyResult(
        True, depth,
        f"signature verified against a trusted key; {held} of {len(claims)} "
        "claim(s) affirmatively hold", claims, findings)


_STATUS_MARK = {HOLDS: "HOLDS", FAILS: "FAILS", INDETERMINATE: "INDET",
                NOT_APPLICABLE: "N/A  "}


def format_report(result: VerifyResult) -> str:
    """The human-readable verdict."""
    width = 74
    lines = ["=" * width,
             "  LIGHTWORK TRUST RECEIPT -- INDEPENDENT VERIFICATION",
             "=" * width]
    verdict = "VERIFIED" if result.ok else "REJECTED"
    lines.append(f"  result: {verdict}   (depth: {result.depth})")
    lines.append(f"  {result.reason}")
    if result.claims:
        lines.append("-" * width)
        for c in result.claims:
            lines.append(f"  [{_STATUS_MARK.get(c.status, c.status)}]  "
                         f"{c.name:20}  {c.detail}")
            for note in c.notes:
                lines.append(f"           - {note}")
    if result.findings:
        lines.append("-" * width)
        for f in result.findings:
            lines.append(f"  ! {f}")
    lines.append("=" * width)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="trust_receipt_verify",
        description="Independently verify a Lightwork trust receipt.")
    ap.add_argument("receipt", help="path to the receipt JSON")
    ap.add_argument("--key", action="append", default=[], metavar="HEX",
                    help="a trusted Ed25519 public key obtained OUT OF BAND "
                         "(repeatable, e.g. across a key rotation)")
    ap.add_argument("--anchor-digest", default=None, metavar="SHA256",
                    help="the published sha256 of the checkpoint file this "
                         "receipt's tip was committed under")
    ap.add_argument("--checkpoint-dir", default=None, metavar="DIR",
                    help="directory holding the issuer's checkpoint files, to "
                         "compare the embedded checkpoint against on-disk bytes")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    try:
        receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"cannot read receipt: {e}", file=sys.stderr)
        return 2
    result = verify(receipt, trusted_keys=args.key, anchor_digest=args.anchor_digest,
                    checkpoint_dir=args.checkpoint_dir)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(format_report(result))
    return 0 if result.ok else 1


__all__ = [
    "CHECKPOINT_SCHEMA", "DEPTH_ANCHORED", "DEPTH_CHAINED", "DEPTH_NONE",
    "DEPTH_SIGNED", "FAILS", "HOLDS", "INDETERMINATE", "NOT_APPLICABLE",
    "RECEIPT_KIND", "SCHEMA_VERSION", "ClaimResult", "VerifyResult",
    "audit_row_hash", "canonical_bundle_bytes", "derive_claim", "format_report",
    "key_id_of", "main", "verify", "verify_ed25519",
]


if __name__ == "__main__":  # pragma: no cover -- standalone entry point
    sys.exit(main())
