"""Cryptographic human approval for high-blast-radius self-changes.

The governed promotion ladder (:mod:`maverick.self_improvement`) requires a
human at the ``code`` and ``weights`` rungs. Until now that consent was a
*self-settable boolean* (``Candidate.approved``): fine for a dev box, but it is
exactly the control a self-modifying agent could flip on its own. If the agent
can propose the code that runs the gate, a boolean it can also set is not a
control -- it's a suggestion.

This module makes the consent unforgeable and **bound to the exact change**. A
human (or a change-advisory board) signs, out of band, an
:class:`ApprovalRequest` = (candidate id, rung, payload digest) with an Ed25519
key whose PUBLIC half the deployment trusts and whose PRIVATE half never touches
the agent's process. The controller verifies that signature against the trusted
set before the human-approval gate passes. Two properties follow:

  * **Non-forgeable** -- the agent cannot mint a valid signature without the
    approver's offline private key (the reference-monitor principle: the control
    lives outside the surface the agent can rewrite).
  * **Bound to the payload** -- the digest covers the candidate's payload, so an
    approval of a benign diff cannot be replayed onto a swapped malicious one
    (TOCTOU). Change the payload, and the signature no longer verifies.

Posture: OFF by default and backward-compatible. :func:`signing_enforced`
returns False unless a deployment configures trusted approver keys (or sets the
require flag), in which case the boolean is *ignored* and a valid signature is
mandatory. When enforced, the gate fails **closed**: missing key material, an
absent ``cryptography`` install, or any verification error means "not approved",
never "approved". Reuses the audit chain's Ed25519 verifier
(:func:`maverick.audit.signing.verify_ed25519`); no new dependency.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Domain-separated, versioned message prefix so an approval signature can never
# be a valid signature for some other Lightwork artifact (or a future format).
APPROVAL_MESSAGE_VERSION = "maverick-approval-v1"


def payload_digest(payload: object) -> str:
    """Canonical SHA-256 hex of a candidate payload, stable across runs.

    JSON with sorted keys when the payload is JSON-serialisable (the common
    case: a config diff, a tool source string, an adapter ref); otherwise a
    ``repr`` fallback so a non-serialisable payload still gets a deterministic
    digest rather than raising. The digest is what the approver signs, so it
    must be reproducible by both the signer (offline) and the gate."""
    try:
        blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    except (TypeError, ValueError):
        blob = repr(payload).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def key_id(pubkey_hex: str) -> str:
    """Short fingerprint of an approver public key (the approver identity in the
    audit line). Matches the audit-signing key-id convention: sha256(pub)[:16]."""
    try:
        return hashlib.sha256(bytes.fromhex(pubkey_hex)).hexdigest()[:16]
    except ValueError:
        return "invalid"


@dataclass(frozen=True)
class ApprovalRequest:
    """The exact bytes an approver signs to authorise one promotion."""

    candidate_id: str
    rung: str
    payload_sha256: str

    def message(self) -> bytes:
        """Domain-separated canonical message. Any field change invalidates a
        prior signature -- consent is bound to *this* candidate + *this* payload."""
        return "|".join((
            APPROVAL_MESSAGE_VERSION,
            self.candidate_id,
            self.rung,
            self.payload_sha256,
        )).encode("utf-8")

    @classmethod
    def for_candidate(cls, candidate: object) -> ApprovalRequest:
        """Build the request for a ``self_improvement.Candidate``.

        Uses the candidate's precomputed ``payload_sha256`` when present, else
        digests its ``payload`` -- so the gate and an offline signer agree on
        what was approved without the caller having to thread the digest."""
        digest = getattr(candidate, "payload_sha256", None)
        if not digest:
            digest = payload_digest(getattr(candidate, "payload", None))
        return cls(
            candidate_id=str(getattr(candidate, "id", "")),
            rung=str(getattr(candidate, "rung", "")),
            payload_sha256=str(digest),
        )


def sign_request(request: ApprovalRequest, private_key_hex: str) -> str:
    """Sign an approval request; returns the hex Ed25519 signature.

    Operator/CAB side, intended to run OFF the agent host (the point is that the
    private key never enters the agent's process). Raises ImportError if
    ``cryptography`` is absent and ValueError on a malformed key -- signing is an
    explicit operator action, so it surfaces errors rather than failing silent."""
    from cryptography.hazmat.primitives.asymmetric import ed25519
    priv = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    return priv.sign(request.message()).hex()


def _verify_ed25519(pubkey_hex: str, sig_hex: str, message: bytes) -> bool:
    """Thin wrapper over the audit verifier that fails closed if crypto is
    unavailable (rather than propagating ImportError into the gate)."""
    try:
        from .audit.signing import verify_ed25519
    except Exception:  # pragma: no cover -- audit module import guarded defensively
        return False
    try:
        return bool(verify_ed25519(pubkey_hex, sig_hex, message))
    except ImportError:
        # cryptography not installed: cannot verify -> not approved (fail closed).
        return False
    except Exception:  # pragma: no cover -- any verifier error => not approved
        return False


def verify(
    request: ApprovalRequest,
    signature_hex: str,
    trusted_pubkeys: list[str] | None = None,
) -> str | None:
    """Return the approver ``key_id`` whose key validates the signature, or None.

    Checks the signature against every trusted approver public key; the first
    that verifies identifies the approver. None means no trusted key signed this
    exact (candidate, rung, payload) tuple -- i.e. not approved."""
    if not signature_hex:
        return None
    keys = trusted_approver_keys() if trusted_pubkeys is None else trusted_pubkeys
    msg = request.message()
    for pub_hex in keys:
        if _verify_ed25519(pub_hex, signature_hex, msg):
            return key_id(pub_hex)
    return None


def verify_candidate(candidate: object) -> str | None:
    """Verify a ``Candidate``'s ``approval_signature`` against the trusted set.

    Returns the approver key_id on success, else None. Convenience the
    controller calls; equivalent to ``verify(ApprovalRequest.for_candidate(c),
    c.approval_signature)``."""
    sig = getattr(candidate, "approval_signature", None)
    if not sig:
        return None
    return verify(ApprovalRequest.for_candidate(candidate), str(sig))


def _keys_from_env() -> list[str]:
    raw = os.environ.get("MAVERICK_APPROVER_KEYS", "")
    return [k for k in raw.replace(",", " ").split() if k]


def _keys_from_dir(dir_path: str | None) -> list[str]:
    """Load raw 32-byte Ed25519 public keys from ``*.pub`` files in a directory,
    returned as hex. A KMS/secrets-manager or the audit key dir can populate it."""
    if not dir_path:
        return []
    out: list[str] = []
    try:
        d = Path(dir_path).expanduser()
        for pub in sorted(d.glob("*.pub")):
            try:
                raw = pub.read_bytes()
            except OSError:
                continue
            if len(raw) == 32:
                out.append(raw.hex())
            else:
                # Tolerate a hex-encoded pubkey stored as text.
                try:
                    txt = raw.decode("ascii").strip()
                    if len(bytes.fromhex(txt)) == 32:
                        out.append(txt)
                except (UnicodeDecodeError, ValueError):
                    continue
    except OSError:  # pragma: no cover -- a missing dir just yields no keys
        return out
    return out


def _config_approval() -> dict:
    try:
        from .config import load_config
        return (load_config() or {}).get("self_improvement") or {}
    except Exception:  # pragma: no cover -- config never blocks the gate
        return {}


def trusted_approver_keys() -> list[str]:
    """The deployment's trusted approver public keys (hex), de-duplicated.

    Sources, unioned: ``MAVERICK_APPROVER_KEYS`` (env, whitespace/comma list),
    ``[self_improvement] approver_keys`` (config list), and any ``*.pub`` under
    ``MAVERICK_APPROVER_KEYS_DIR`` / ``[self_improvement] approver_keys_dir``.
    Empty (the default) means no cryptographic approval is configured."""
    keys = list(_keys_from_env())
    cfg = _config_approval()
    cfg_keys = cfg.get("approver_keys")
    if isinstance(cfg_keys, list):
        keys.extend(str(k).strip() for k in cfg_keys if str(k).strip())
    keys.extend(_keys_from_dir(os.environ.get("MAVERICK_APPROVER_KEYS_DIR")
                               or cfg.get("approver_keys_dir")))
    # De-dup, preserve order.
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def signing_enforced() -> bool:
    """Whether a valid approver signature is REQUIRED at human-gated rungs.

    True when trusted approver keys are configured, or when
    ``MAVERICK_REQUIRE_SIGNED_APPROVAL`` / ``[self_improvement]
    require_signed_approval`` is set (a strict posture: require signatures even
    before keys are provisioned -- which then fails closed until they are).
    False by default, so a deployment that has not opted in keeps the legacy
    boolean-approval behaviour untouched."""
    try:
        from .config import env_flag
        v = env_flag("MAVERICK_REQUIRE_SIGNED_APPROVAL")
        if v is not None:
            return v or bool(trusted_approver_keys())
    except Exception:  # pragma: no cover -- env parsing never blocks the gate
        pass
    if bool(trusted_approver_keys()):
        return True
    return bool(_config_approval().get("require_signed_approval", False))


__all__ = [
    "APPROVAL_MESSAGE_VERSION",
    "ApprovalRequest",
    "payload_digest",
    "key_id",
    "sign_request",
    "verify",
    "verify_candidate",
    "trusted_approver_keys",
    "signing_enforced",
]
