"""Explicit recipient enrollment, scoped to the current tenant and encrypted."""
from __future__ import annotations

import hashlib
import json
import time

from .crypto_at_rest import seal, unseal
from .file_lock import atomic_write_bytes, cross_process_lock, ensure_private_directory
from .paths import current_tenant_id_strict, data_dir


def _path():
    return data_dir("personal-notifications", "recipients.enc")


def _read():
    try:
        state = json.loads(unseal(_path().read_bytes()).decode())
    except FileNotFoundError:
        return {"tenant": current_tenant_id_strict(), "recipients": {}}
    if (not isinstance(state, dict) or state.get("tenant") != current_tenant_id_strict()
            or not isinstance(state.get("recipients"), dict) or len(state["recipients"]) > 1000):
        raise ValueError("Personal alert recipients could not be verified.")
    return state


def enroll(principal: str) -> None:
    if not isinstance(principal, str) or not principal.startswith("user:") or len(principal) > 256:
        raise ValueError("Personal alerts require a named account.")
    path = _path()
    ensure_private_directory(path.parent)
    with cross_process_lock(path):
        state = _read()
        if principal in state["recipients"]:
            return
        if len(state["recipients"]) >= 1000:
            raise ValueError("Personal alert enrollment is full. Ask an administrator to review inactive accounts.")
        state["recipients"][principal] = {"enrolled_at": time.time(), "observed_at": None}
        atomic_write_bytes(path, seal(json.dumps(state).encode()), mode=0o600)


def principals() -> list[str]:
    return sorted(_read()["recipients"])


def observed(principal: str) -> None:
    path = _path()
    with cross_process_lock(path):
        state = _read()
        if principal not in state["recipients"]:
            return
        state["recipients"][principal]["observed_at"] = time.time()
        atomic_write_bytes(path, seal(json.dumps(state).encode()), mode=0o600)


def status(principal: str) -> dict:
    value = _read()["recipients"].get(principal, {})
    return {"last_observed_at": value.get("observed_at")}


def withdraw(principal: str) -> None:
    """Stop enrollment on current identity revocation; explicit reopen can reenroll."""
    path = _path()
    with cross_process_lock(path):
        state = _read()
        if state["recipients"].pop(principal, None) is None:
            return
        atomic_write_bytes(path, seal(json.dumps(state).encode()), mode=0o600)
    # Exact account key only; no directory-wide deletion or credential changes.
    inbox = data_dir("personal-notifications", hashlib.sha256(principal.encode()).hexdigest() + ".enc")
    with cross_process_lock(inbox):
        inbox.unlink(missing_ok=True)
