"""Single-use SAML assertion store (replay defense for the ACS).

A signed ``SAMLResponse`` posted to ``/saml/acs`` mints an ``mvk_session``
cookie. pysaml2 verifies the XML signature and the ``NotOnOrAfter`` window, but
without an outstanding-request cache the same captured, still-valid assertion
can be POSTed again and again until it expires — each replay minting a fresh
session. (The dashboard runs with ``allow_unsolicited`` so IdP-initiated
responses carry no ``InResponseTo`` to bind against.)

This records each assertion's ID the first time it is consumed and refuses it
thereafter, so an assertion is single-use even inside its validity window.
First-writer-wins under a cross-process lock (multiple dashboard workers share
the store); entries are pruned once past the assertion's ``NotOnOrAfter`` (a
replay after that is already rejected by pysaml2's condition check, so the ID
no longer needs remembering).

Fail CLOSED (mirrors :mod:`session_revocation`): if the store can't be read or
the consumption can't be durably recorded, we refuse the login rather than let
an unprovable-single-use assertion through — a refused login just retries with
a fresh assertion, whereas failing open reopens the replay window.

Store: ``<maverick_home>/saml-consumed-assertions.json`` (0600 via
``atomic_write_text``), ``{assertion_id: expires_at_epoch}``.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

from maverick.file_lock import atomic_write_text, cross_process_lock
from maverick.paths import maverick_home

_LOCK = threading.Lock()

# Fallback lifetime for a consumed assertion whose NotOnOrAfter we couldn't read
# (kept long enough to cover any sane assertion window, then pruned).
_FALLBACK_TTL_SECONDS = 3600.0


class ReplayStoreError(RuntimeError):
    """The consumed-assertion store exists but can't be read/parsed. Callers
    fail CLOSED (treat the assertion as un-consumable) rather than silently
    accept a possible replay by reading a damaged store as 'nothing seen'."""


def _path() -> Path:
    return maverick_home() / "saml-consumed-assertions.json"


@contextmanager
def _locked():
    with ExitStack() as stack:
        stack.enter_context(_LOCK)
        stack.enter_context(cross_process_lock(_path()))
        yield


def _load() -> dict:
    """The ``{assertion_id: expires_at}`` map. A genuinely-absent store is empty;
    a store that EXISTS but is unreadable/corrupt raises ``ReplayStoreError`` so
    the caller fails closed."""
    try:
        raw = _path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:  # present but unreadable
        raise ReplayStoreError(f"replay store unreadable: {e}") from e
    try:
        data = json.loads(raw)
    except ValueError as e:  # corrupt JSON
        raise ReplayStoreError(f"replay store corrupt: {e}") from e
    return data if isinstance(data, dict) else {}


def _prune(data: dict, *, now: float) -> dict:
    """Drop entries already past their expiry (pysaml2 rejects those on its own)."""
    return {k: v for k, v in data.items()
            if isinstance(v, (int, float)) and float(v) > now}


def consume(assertion_id: str, expires_at: float | None) -> bool:
    """Record first use of ``assertion_id``; return True iff it was NOT seen
    before (i.e. the assertion may be accepted). A repeat returns False.

    ``expires_at`` is the assertion's ``NotOnOrAfter`` epoch; a missing/invalid
    value falls back to a bounded TTL so the entry still prunes. A blank
    assertion id returns False (fail closed — an assertion we can't identify
    can't be proven single-use). Raises :class:`ReplayStoreError` on a
    read/write failure so the caller fails closed."""
    aid = (assertion_id or "").strip()
    if not aid:
        return False
    now = time.time()
    try:
        ttl_expiry = float(expires_at) if expires_at is not None else 0.0
    except (TypeError, ValueError):
        ttl_expiry = 0.0
    if ttl_expiry <= now:
        ttl_expiry = now + _FALLBACK_TTL_SECONDS
    with _locked():
        data = _prune(_load(), now=now)
        if aid in data:
            return False
        data[aid] = ttl_expiry
        atomic_write_text(_path(), json.dumps(data, indent=2, sort_keys=True))
    return True


__all__ = ["consume", "ReplayStoreError"]
