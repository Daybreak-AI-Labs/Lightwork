"""One control plane per data root, enforced at runtime.

The single-replica invariant is asserted in eleven places -- the chart, two raw
manifests, six docs, a runbook -- and enforced in exactly one: a
``helm template`` time guard in ``_helpers.tpl`` that fails on
``replicaCount > 1``. That guard is real, but it is build-time and it is the
only thing standing between a deployment and silent corruption. ``kubectl
apply`` with a hand-edited manifest, ``docker compose up --scale``, a systemd
unit, or simply two ``maverick dashboard`` processes on one host all walk past
it, and nothing downstream notices.

What is at stake is not a crash. The data root holds four hash-chained ledgers
-- the signed audit chain plus its cross-file anchor ledger, budget receipts,
fleet memory, and the learning/promotion NDJSONs -- and roughly 133 named
file-backed state roots besides. Individual writes are serialized by
``file_lock.cross_process_lock``, so two writers do not tear a single record.
They interleave *valid* records into an append-only chain that assumes one
author, which is worse: the corruption is well-formed, it verifies, and it is
discovered when someone tries to reconstruct history from it.

This module supplies the missing runtime check. One process holds an exclusive
advisory lock on a sentinel in the data root for its entire lifetime; a second
refuses to start and says who holds it.

**What this is, precisely.** The authority is an OS advisory lock (``flock``
on POSIX, ``msvcrt`` byte-range on Windows) held on an open descriptor for the
process lifetime. It is not a timed lease and does not need renewing: the
kernel drops the lock when the holder dies, so there is no stale-lock problem
and no expiry to tune. The JSON body of the sentinel -- pid, host, start time,
fencing token -- is *observability*, not the mechanism. It is written by the
holder after it wins, and a second process reads it only to name the winner in
its refusal message. Saying "lease" and shipping a lock would be exactly the
claim-vs-code gap this work exists to close, so: it is a lock.

**The fencing token** is a counter incremented under the lock on each
successful acquisition, so it is monotonic across restarts. It answers "is the
component about to write still the one that holds the root?" for anything that
caches an ownership decision -- a stale holder's token is lower than the
current one and its write can be rejected. Nothing consumes it for admission
yet; it is recorded, exposed on ``/readyz``, and honest about that.

**Where exclusion does not reach.** Advisory locks bind a kernel, so this
covers processes sharing a host and a data root -- which is the deployment
shape the invariant is about, since the state lives on a ReadWriteOnce volume
attached to one node. Two pods on different nodes with genuinely separate
volumes are separate deployments and share no state to corrupt. The dangerous
in-between is a network filesystem where ``flock`` is silently a no-op: there
the lock would appear to succeed while providing no exclusion at all. That
case fails closed -- if the backend cannot demonstrate locking, the process
refuses to start rather than assume it is alone.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import socket
import time
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: Sentinel filename in the data root. Named for what holding it means.
LOCK_FILENAME = "control-plane.lock"

#: Opt out of single-writer enforcement. Set only when you have independently
#: guaranteed that no other process writes this data root -- the override is
#: reported on /readyz rather than being silently honoured, because an
#: unenforced invariant that still *claims* to hold is the worse failure.
OVERRIDE_ENV = "MAVERICK_ALLOW_MULTIPLE_CONTROL_PLANES"


class ControlPlaneBusy(RuntimeError):
    """Another process already owns this data root."""


class LeaseUnavailable(RuntimeError):
    """Exclusivity could not be established, so it must not be assumed."""


@dataclass(frozen=True)
class Holder:
    """Who owns the data root. Diagnostic; the lock is the authority."""

    pid: int
    hostname: str
    started_at: float
    fencing_token: int
    role: str = "control-plane"

    def describe(self) -> str:
        age = max(0.0, time.time() - self.started_at)
        return (f"pid {self.pid} on {self.hostname} (role {self.role}, "
                f"token {self.fencing_token}, up {age:.0f}s)")


def lock_path(root: Path | None = None) -> Path:
    """The sentinel path for a data root."""
    if root is None:
        from .paths import maverick_home
        root = maverick_home()
    return Path(root) / LOCK_FILENAME


def override_enabled() -> bool:
    """Whether single-writer enforcement has been deliberately disabled."""
    raw = os.environ.get(OVERRIDE_ENV)
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    try:
        from .config import load_config
        section = (load_config() or {}).get("deployment") or {}
        return bool(section.get("allow_multiple_control_planes", False))
    # failure-policy: fail_closed
    except Exception as e:
        # An unreadable config must not silently grant the override: the safe
        # reading of "we cannot tell" is "enforcement stays on".
        log.debug("control-plane lease: cannot read config override: %s", e)
        return False


def _read_holder(path: Path) -> Holder | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "pid" not in data:
        return None
    try:
        return Holder(
            pid=int(data["pid"]),
            hostname=str(data.get("hostname", "?")),
            started_at=float(data.get("started_at", 0.0)),
            fencing_token=int(data.get("fencing_token", 0)),
            role=str(data.get("role", "control-plane")),
        )
    except (TypeError, ValueError):
        return None


def _try_lock(fd: int) -> bool:
    """Take an exclusive non-blocking lock. False = someone else holds it.

    Blocking would be the wrong primitive here even though the repo's
    ``cross_process_lock`` uses it: a second replica must refuse and exit, not
    queue behind the first one forever while its readiness probe times out.
    """
    try:
        import fcntl
    except ImportError:
        try:
            import msvcrt
        except ImportError as exc:  # pragma: no cover - supported OSes
            raise LeaseUnavailable(
                "no advisory-lock backend on this platform, so single-writer "
                "ownership of the data root cannot be established"
            ) from exc
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EDEADLOCK):
                return False
            raise LeaseUnavailable(
                f"advisory locking failed on the data root: {e}") from e
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as e:
        if e.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
            return False
        # ENOLCK / EOPNOTSUPP: a filesystem that cannot lock (some NFS, some
        # overlay mounts). Proceeding would mean believing we are exclusive
        # while providing no exclusion whatsoever -- the one outcome worse
        # than refusing to start.
        raise LeaseUnavailable(
            f"the data root's filesystem cannot provide advisory locking "
            f"({e}); single-writer safety cannot be established, so the "
            f"control plane will not start. Move the state volume to a "
            f"filesystem with working locks, or set {OVERRIDE_ENV}=1 if you "
            f"have guaranteed exclusivity another way"
        ) from e


class Lease:
    """An acquired ownership record. Held for the process lifetime."""

    def __init__(self, path: Path, fd: int, holder: Holder) -> None:
        self._path = path
        self._fd = fd
        self._holder = holder
        self._released = False

    @property
    def holder(self) -> Holder:
        return self._holder

    @property
    def path(self) -> Path:
        return self._path

    @property
    def fencing_token(self) -> int:
        return self._holder.fencing_token

    def release(self) -> None:
        """Drop ownership. The kernel does this anyway if the process dies."""
        if self._released:
            return
        self._released = True
        try:
            os.close(self._fd)
        except OSError:  # pragma: no cover - closing a dead fd is not an error
            pass


#: The lease this process holds, if any. One control plane per process.
_held: Lease | None = None


def acquire(
    root: Path | None = None, *, role: str = "control-plane",
) -> Lease | None:
    """Take exclusive ownership of a data root, or explain who has it.

    Returns the lease, or ``None`` when enforcement is overridden. Raises
    :class:`ControlPlaneBusy` if another process owns the root and
    :class:`LeaseUnavailable` if exclusivity cannot be established at all.
    """
    global _held
    if _held is not None:
        return _held

    path = lock_path(root)
    if override_enabled():
        log.warning(
            "control-plane lease: single-writer enforcement is DISABLED via "
            "%s. Two processes writing %s will interleave records into "
            "append-only ledgers that assume one author; the result verifies "
            "cleanly and is unreconstructable. /readyz reports this posture.",
            OVERRIDE_ENV, path.parent,
        )
        return None

    from .file_lock import ensure_private_directory
    ensure_private_directory(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if not _try_lock(fd):
            other = _read_holder(path)
            who = other.describe() if other else "an unidentified process"
            raise ControlPlaneBusy(
                f"another Lightwork control plane already owns {path.parent}: "
                f"{who}. Exactly one may write this data root -- flows, A2A "
                f"claims, and the audit, budget-receipt, fleet-memory and "
                f"learning ledgers are hash-chained and assume a single "
                f"author. Scale remote workers instead of the control plane, "
                f"or point this process at its own data root via "
                f"MAVERICK_HOME."
            )
        # Won the lock. Bump the fencing token under it, so the value is
        # monotonic across restarts and a stale holder is detectable.
        previous = _read_holder(path)
        holder = Holder(
            pid=os.getpid(),
            hostname=socket.gethostname(),
            started_at=time.time(),
            fencing_token=(previous.fencing_token + 1) if previous else 1,
            role=role,
        )
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, (json.dumps(asdict(holder), sort_keys=True) + "\n").encode())
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        raise

    _held = Lease(path, fd, holder)
    log.info("control-plane lease: acquired %s as %s", path, holder.describe())
    return _held


def current() -> Lease | None:
    """The lease this process holds, or None."""
    return _held


def release() -> None:
    """Drop this process's lease. Primarily for tests and clean shutdown."""
    global _held
    if _held is not None:
        _held.release()
        _held = None


def posture(root: Path | None = None) -> dict:
    """Replica-safety posture, for /readyz.

    Three states, deliberately distinguished: ``owned`` (this process holds the
    root), ``unenforced`` (the operator disabled the check), and ``unknown``
    (nothing acquired -- which for the dashboard means startup did not run the
    guard, not that the root is free).
    """
    if _held is not None:
        return {
            "state": "owned",
            "single_writer_enforced": True,
            "fencing_token": _held.fencing_token,
            "holder": _held.holder.describe(),
        }
    if override_enabled():
        return {
            "state": "unenforced",
            "single_writer_enforced": False,
            "detail": (f"{OVERRIDE_ENV} is set; concurrent writers to this "
                       "data root are not prevented"),
        }
    other = _read_holder(lock_path(root))
    return {
        "state": "unknown",
        "single_writer_enforced": False,
        "detail": ("this process holds no control-plane lease"
                   + (f"; last recorded holder was {other.describe()}"
                      if other else "")),
    }


__all__ = [
    "ControlPlaneBusy",
    "Holder",
    "Lease",
    "LeaseUnavailable",
    "OVERRIDE_ENV",
    "acquire",
    "current",
    "lock_path",
    "override_enabled",
    "posture",
    "release",
]
