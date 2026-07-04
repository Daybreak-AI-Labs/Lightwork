"""Real apply + rollback for a promoted code change (Gap 3).

The governed gate proves a change is *safe to land*; this module actually lands
it — and, crucially, makes it **mechanically reversible**, which the earlier loop
only asserted (it passed ``rollback=cand.id``, a string, satisfying the gate's
truthiness check without any real undo).

The reversibility handle is a real workspace snapshot. The ordering matters:

1. **Snapshot the live tree first** (:func:`create_rollback_point`) — cheap,
   read-only w.r.t. the tree, and produces a real revert handle *before* the gate
   runs. That snapshot id is what the caller passes as the gate's ``rollback``
   handle, so "reversible" is now backed by an actual restore point, not a token.
2. The caller runs the governed gate (evidence, capability, human signature,
   calibration) with that handle.
3. **Only on approval** does :func:`apply_change` mutate the live tree.
4. :func:`revert_change` (or the returned :class:`RollbackHandle`) restores the
   snapshot — invoked on a post-apply failure, or later if monitoring flags a
   regression.

Applying an already-gated, human-signed patch to the live tree is a *deployment*
step, so it runs on the host tree by design (via ``sandbox.exec`` per kernel rule
4) — distinct from the untrusted-candidate *evaluation*, which
``self_modify_eval`` pins inside a container. This module never proposes or
gates; it is the hands that land what the gate already approved, and it fails
closed (a failed apply auto-reverts).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .workspace_snapshot import create_snapshot, restore_snapshot, store_dir

log = logging.getLogger(__name__)


@dataclass
class RollbackHandle:
    """A real, mechanically-executable undo for an applied change."""

    snapshot_id: str
    tree: Path
    store: Path

    def revert(self) -> bool:
        return revert_change(self.snapshot_id, tree=self.tree, store=self.store)


@dataclass
class AppliedChange:
    """Outcome of landing a promoted patch on the live tree."""

    ok: bool
    snapshot_id: str | None = None
    reason: str = ""
    rollback: RollbackHandle | None = None


def create_rollback_point(tree: Path, *, store: Path | None = None,
                          label: str = "self-modify") -> str | None:
    """Snapshot ``tree`` and return the snapshot id — the reversibility handle to
    pass to the gate BEFORE applying. Returns ``None`` on failure (the caller
    then has no revert point and must refuse to apply — fail closed)."""
    store = store or store_dir()
    try:
        man = create_snapshot(Path(tree), Path(store), label)
        return man.get("id")
    except (ValueError, OSError) as e:
        log.warning("self_modify_apply: could not snapshot %s: %s", tree, e)
        return None


def _host_git_apply(patch: str, tree: Path) -> tuple[bool, str]:
    """Apply a unified diff to the live ``tree`` via ``sandbox.exec`` (rule 4),
    ``--check`` first. This is the deployment of an already-approved patch, so it
    runs on the host tree by design. Returns ``(ok, message)``."""
    tree = Path(tree)
    patch_file = tree / ".maverick-apply.diff"
    try:
        patch_file.write_text(patch, encoding="utf-8")
    except OSError as e:  # pragma: no cover
        return False, f"could not stage patch: {e}"
    from .sandbox.local import LocalBackend
    sb = LocalBackend(workdir=tree)
    try:
        chk = sb.exec(f"git apply --check -p1 {patch_file.name}")
        if not getattr(chk, "ok", False):
            return False, f"patch does not apply: {chk.stderr.strip()[:200]}"
        res = sb.exec(f"git apply -p1 {patch_file.name}")
        if not getattr(res, "ok", False):
            return False, f"git apply failed: {res.stderr.strip()[:200]}"
        return True, "applied"
    except Exception as e:  # pragma: no cover
        return False, f"apply error: {e}"
    finally:
        try:
            patch_file.unlink()
        except OSError:
            pass


def apply_change(
    patch: str, *, tree: Path, snapshot_id: str | None = None,
    store: Path | None = None, label: str = "self-modify",
) -> AppliedChange:
    """Land ``patch`` on the live ``tree`` with a real rollback point.

    If ``snapshot_id`` is given (the handle already created for the gate) it is
    reused; otherwise a snapshot is taken now. WITHOUT a snapshot the apply is
    REFUSED (no revert point → not reversible → fail closed). On a failed apply
    the tree is reverted to the snapshot so a half-applied change never persists.
    Never raises.

    Defence in depth: this live-tree write RE-RUNS the editable-surface boundary
    (:func:`maverick.self_modify.review_patch`) and refuses any patch that touches
    a control-plane path — so even a mis-wired caller that skipped the gate can
    never land a change on a protected file through this primitive."""
    tree = Path(tree)
    store = store or store_dir()

    # Boundary re-check: refuse a patch that touches the reference monitor,
    # independent of whatever gating the caller claims to have done.
    from .self_modify import _diff_paths, is_protected
    protected = [p for p in _diff_paths(patch) if is_protected(p)]
    if protected:
        return AppliedChange(
            False, reason=f"refusing to apply: patch touches protected "
            f"control-plane paths {sorted(protected)}")

    snap = snapshot_id or create_rollback_point(tree, store=store, label=label)
    if not snap:
        return AppliedChange(False, reason="no rollback point; refusing to apply")

    ok, msg = _host_git_apply(patch, tree)
    handle = RollbackHandle(snapshot_id=snap, tree=tree, store=store)
    if not ok:
        # Restore to the snapshot so a partial apply can't linger, and report the
        # ACTUAL revert result — a failed revert leaves the tree possibly dirty.
        reverted = handle.revert()
        detail = (f"reverted to {snap}" if reverted
                  else f"REVERT FAILED for {snap} — tree may be dirty")
        return AppliedChange(False, snapshot_id=snap, reason=f"{msg}; {detail}")
    return AppliedChange(True, snapshot_id=snap, reason=msg, rollback=handle)


def revert_change(snapshot_id: str, *, tree: Path, store: Path | None = None) -> bool:
    """Restore ``tree`` to the snapshot. Returns True on success. Never raises."""
    store = store or store_dir()
    try:
        restore_snapshot(Path(store), snapshot_id, Path(tree))
        return True
    except (ValueError, OSError) as e:
        log.warning("self_modify_apply: revert to %s failed: %s", snapshot_id, e)
        return False


__all__ = [
    "RollbackHandle", "AppliedChange",
    "create_rollback_point", "apply_change", "revert_change",
]
