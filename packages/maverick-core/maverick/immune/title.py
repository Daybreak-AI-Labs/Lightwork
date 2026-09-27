"""Discovery Title: evidence that a machine-assisted discovery can be owned.

US inventorship (USPTO revised guidance, Nov 2025) turns on *human conception*;
the AI is a tool. A swarm that produces a result nobody conceived yields
nothing to patent, and developing a secret on a third party's service can
defeat trade-secret protection. A title dossier records, as the work happens:

* **inputs** -- digests of every dataset, prompt and model, committed *before*
  the run (you cannot later prove what did not go in; you can prove what did);
* **priority** -- commit-reveal digests of intermediate ideas with timestamps,
  so priority can be shown without disclosing the idea;
* **conception gates** -- the human decisions (framing, selection, modification)
  and the claim elements each one conceived;
* **verification** -- independent checkers' results;
* **coverage** -- how much of the search space was actually examined.

:func:`problems` reports what would not survive scrutiny (an unconceived claim
element, a failed check, a late input commitment). It is evidence for counsel,
not a legal opinion. :func:`seal` signs the dossier; :func:`verify` checks it
offline against a public key.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from . import _ledger

SCHEMA = "lightwork.discovery-title.v1"
# Recognising or accepting an AI output is not conception; shaping it is.
CONCEPTION_DECISIONS = frozenset({"framed", "selected", "modified", "conceived"})


def commit(content: str) -> dict[str, str]:
    """A hiding commitment. Publish ``commitment``; keep ``salt`` private."""
    salt = secrets.token_hex(32)
    digest = hashlib.sha256(bytes.fromhex(salt) + content.encode()).hexdigest()
    return {"commitment": digest, "salt": salt}


def opens(commitment: str, salt: str, content: str) -> bool:
    digest = hashlib.sha256(bytes.fromhex(salt) + content.encode()).hexdigest()
    return hmac.compare_digest(digest, commitment)


def conception_map(dossier: dict) -> dict[str, list[str]]:
    """Claim element -> ids of the human gates that conceived it."""
    out = {e: [] for c in dossier["claims"] for e in c["elements"]}
    for gate in dossier["gates"]:
        if gate["decision"] in CONCEPTION_DECISIONS:
            for e in gate["elements"]:
                if e in out:
                    out[e].append(gate["id"])
    return out


def problems(dossier: dict) -> list[str]:
    """Everything a patent examiner, litigator or auditor could attack."""
    out = [f"claim element without human conception: {e}"
           for e, gates in sorted(conception_map(dossier).items()) if not gates]
    if not dossier.get("problem", {}).get("signed_off_by"):
        out.append("problem statement not signed off by a human")
    checks = dossier.get("verification", [])
    if not checks:
        out.append("no independent verification recorded")
    out += [f"verification failed: {v['checker']}" for v in checks if not v["passed"]]
    cov = dossier.get("coverage") or {}
    if not cov.get("total"):
        out.append("search coverage unknown")
    elif cov["examined"] < cov["total"]:
        out.append(f"search coverage {cov['examined']}/{cov['total']} "
                   f"({cov['examined'] / cov['total']:.0%})")
    first = min((p["at"] for p in dossier.get("priority", [])), default=None)
    if first is not None and dossier.get("inputs_committed_at", float("inf")) > first:
        out.append("inputs were committed after the first recorded idea")
    return out


def seal(dossier: dict) -> dict:
    """Sign the dossier with the deployment's audit identity."""
    from ..audit.signing import sign_detached_digest
    body = {**dossier, "schema": SCHEMA}
    digest = hashlib.sha256(_ledger.canonical(body)).hexdigest()
    return {"dossier": body, "signature": sign_detached_digest(digest)}


def verify(sealed: dict, pubkey_hex: str, reveals: dict[str, tuple[str, str]] | None = None) -> bool:
    """Offline check of the seal, plus any ``commitment -> (salt, content)``
    openings the holder chooses to reveal."""
    from ..audit.signing import verify_detached_digest
    digest = hashlib.sha256(_ledger.canonical(sealed["dossier"])).hexdigest()
    sig = sealed["signature"]
    if sig["digest"] != digest or not verify_detached_digest(pubkey_hex, digest, sig["signature"]):
        return False
    known = {p["commitment"] for p in sealed["dossier"].get("priority", [])}
    return all(c in known and opens(c, salt, text) for c, (salt, text) in (reveals or {}).items())
