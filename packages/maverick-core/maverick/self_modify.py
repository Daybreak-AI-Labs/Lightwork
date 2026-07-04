"""Governed code self-modification: the editable-surface boundary + code rung.

This is Phase 1 of a governed Darwin-Gödel-Machine-style capability (DGM,
arXiv:2505.22954): a system that proposes changes to *its own code* and validates
them empirically. The DGM insight is empirical validation instead of proof; we
run it **under a reference monitor** — the deployable, regulated form is not
unsupervised in-production self-rewrite but "the agent proposes evidenced,
sandbox-tested, capability-bounded, human-signed, reversible changes to a
*constrained* surface, all recorded in a signed audit."

The load-bearing safety primitive is the **editable-surface boundary**, and it
lives here. The reference-monitor principle: the control plane must live outside
the surface the agent can rewrite. So a proposed patch is **structurally
refused** if it touches ANY protected path — the self-improvement gate, the
approval verifier, the calibration interlock, the FINAL verifier, the capability
algebra, the sandbox, the audit chain, the config loader, or THIS module. That
refusal happens *before* and *independently of* the promotion gate, so even a
compromised gate cannot let a control-plane edit through.

A candidate that clears the boundary is then routed through the existing governed
promotion ladder (:func:`maverick.self_improvement.consider`), which independently
enforces: it beats its baseline on held-out evidence, it does not widen the
capability envelope, a human cryptographically approved it (code/weights rung),
the verifier is calibrated (not drifting/gamed), and it is reversible. Every
promotion is signed into the audit chain.

Posture: **OFF by default and fail-closed.** The engine is a no-op unless
``[self_modify] enable`` is set, the editable allowlist is EMPTY by default (so
every path is ``unknown`` → refused), and a traversal/absolute path is treated as
protected. This module never *applies* a patch — it evidences and gates; the
caller applies the diff to the editable surface only when the verdict is ``ok``.
Sandbox evaluation (running the candidate to produce baseline/candidate scores)
is an injected seam, Phase 2.
"""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


# --- the boundary: the control plane, which self-modification may NEVER touch --

# Top-level modules under the ``maverick`` package that ARE the reference
# monitor. Editing any of these is the one move that could unravel the whole
# governance stack, so it is refused structurally regardless of evidence.
_PROTECTED_FILES = frozenset({
    "self_modify.py",            # this boundary itself
    "self_improvement.py",       # the promotion gate
    "self_improvement_runner.py",
    "approval_signing.py",       # cryptographic human approval
    "calibration.py",            # the drift / reward-laundering interlock
    "verifier.py",               # the FINAL correctness gate
    "capability.py",             # the capability algebra (non-escalation proof)
    "config.py",                 # resolves every gate's settings
    "migrate.py",                # the config-section registry
    "sigstore_signing.py",       # signing primitives
    "erasure_verify.py",
})
# Whole packages under ``maverick`` that are off-limits (any file within).
_PROTECTED_DIRS = frozenset({"audit", "sandbox"})


def _normalize(path: str) -> str:
    """Repo-relative POSIX path, leading ``./`` stripped. Not resolved against
    the filesystem (patches reference paths that may not exist yet)."""
    p = str(path).replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def _maverick_tail(parts: list[str]) -> list[str] | None:
    """Components after the LAST ``maverick`` package dir, or None if the path
    isn't inside a maverick package (e.g. ``packages/maverick-core/maverick/x`` ->
    ``[x]``; the ``maverick-core`` component is not the package)."""
    idxs = [i for i, p in enumerate(parts) if p == "maverick"]
    if not idxs:
        return None
    return parts[idxs[-1] + 1:]


def is_protected(path: str) -> bool:
    """Whether ``path`` is part of the reference monitor and may never be edited.

    Fail-closed: an absolute path or any ``..`` traversal segment is treated as
    protected (a patch must not reach outside the repo tree or dodge the check)."""
    norm = _normalize(path)
    parts = norm.split("/")
    if norm.startswith("/") or ".." in parts:
        return True
    tail = _maverick_tail(parts)
    if tail is None:
        return False  # not a maverick source file; the allowlist decides
    if tail and tail[0] in _PROTECTED_DIRS:
        return True
    return len(tail) == 1 and tail[0] in _PROTECTED_FILES


@dataclass(frozen=True)
class EditableSurface:
    """The allowlist of paths self-modification MAY touch.

    ``editable_globs`` are repo-relative fnmatch patterns (e.g.
    ``packages/maverick-core/maverick/domains/*.toml``). A path is ``protected``
    (control plane) > ``editable`` (matches the allowlist) > ``unknown``
    (matches nothing) — and ``unknown`` is refused, so an EMPTY allowlist (the
    default) makes the whole engine inert even when enabled."""

    editable_globs: tuple[str, ...] = ()

    def classify(self, path: str) -> str:
        """Return ``"protected"`` | ``"editable"`` | ``"unknown"``."""
        if is_protected(path):
            return "protected"
        norm = _normalize(path)
        for g in self.editable_globs:
            if fnmatch.fnmatch(norm, g):
                return "editable"
        return "unknown"


@dataclass(frozen=True)
class PatchReview:
    """The boundary's verdict on a proposed patch."""

    ok: bool
    reason: str = ""
    touched: tuple[str, ...] = ()
    protected: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()


def _diff_paths(patch: str) -> list[str]:
    """Extract every file path a unified diff touches (a/ and b/ sides, renames),
    ``/dev/null`` excluded. Deliberately permissive at extraction so the
    classifier — not the parser — is what decides; an unparseable-but-nonempty
    patch yields no paths and is refused by :func:`review_patch`."""
    paths: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        p = raw.strip()
        if p.startswith(("a/", "b/")):
            p = p[2:]
        if not p or p == "/dev/null":
            return
        if p not in seen:
            seen.add(p)
            paths.append(p)

    for line in patch.splitlines():
        if line.startswith("diff --git "):
            # diff --git a/X b/Y
            bits = line.split()
            for tok in bits[2:]:
                _add(tok)
        elif line.startswith(("--- ", "+++ ")):
            _add(line[4:])
        elif line.startswith(("rename from ", "rename to ",
                              "copy from ", "copy to ")):
            _add(line.split(" ", 2)[2])
    return paths


def review_patch(patch: str, surface: EditableSurface | None = None) -> PatchReview:
    """Classify every path a patch touches against the surface. ``ok`` iff at
    least one path was found and ALL touched paths are ``editable``.

    This is the structural refusal: it runs before the promotion gate and does
    not depend on it, so a control-plane edit is rejected even if every other
    gate were somehow satisfied."""
    surface = surface or EditableSurface()
    paths = _diff_paths(patch or "")
    if not paths:
        return PatchReview(False, reason="no file paths found in patch")
    protected: list[str] = []
    unknown: list[str] = []
    for p in paths:
        c = surface.classify(p)
        if c == "protected":
            protected.append(p)
        elif c == "unknown":
            unknown.append(p)
    if protected:
        reason = f"patch touches protected control-plane paths: {sorted(protected)}"
    elif unknown:
        reason = f"patch touches paths outside the editable allowlist: {sorted(unknown)}"
    else:
        reason = f"all {len(paths)} touched paths are within the editable surface"
    return PatchReview(
        ok=not protected and not unknown, reason=reason,
        touched=tuple(paths), protected=tuple(protected), unknown=tuple(unknown),
    )


# --- config / gate -----------------------------------------------------------

def _settings() -> dict:
    try:
        from .config import get_self_modify
        return get_self_modify()
    except Exception:  # pragma: no cover -- config never blocks
        return {"enable": False, "editable_paths": []}


def enabled() -> bool:
    """Whether the governed code-modification engine may run. OFF by default.

    ``MAVERICK_SELF_MODIFY=1`` or ``[self_modify] enable``. Even when enabled it
    is inert until the editable allowlist is non-empty. Fail-open on a config
    error (stays off)."""
    try:
        from .config import env_flag
        v = env_flag("MAVERICK_SELF_MODIFY")
        if v is not None:
            return v
    except Exception:  # pragma: no cover
        pass
    try:
        return bool(_settings()["enable"])
    except Exception:  # pragma: no cover
        return False


def default_surface() -> EditableSurface:
    """The editable surface from ``[self_modify] editable_paths`` (empty by
    default → nothing is editable)."""
    try:
        globs = tuple(_settings().get("editable_paths") or ())
    except Exception:  # pragma: no cover
        globs = ()
    return EditableSurface(editable_globs=globs)


# --- the governed code-rung producer -----------------------------------------

@dataclass
class CodeChangeResult:
    """Outcome of proposing a code change: the boundary review + the promotion
    verdict. The caller applies the patch to the editable surface only when
    :attr:`ok`."""

    review: PatchReview
    verdict: object | None = None      # maverick.self_improvement.Verdict | None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.review.ok and self.verdict is not None
                    and getattr(self.verdict, "ok", False))


def propose_code_change(
    patch: str,
    *,
    summary: str,
    baseline_score: float,
    candidate_score: float,
    samples: int,
    rollback: object,
    surface: EditableSurface | None = None,
    capability_widens: bool | None = None,
    approval_signature: str | None = None,
    payload_sha256: str | None = None,
    controller: object | None = None,
    rung: str = "code",
) -> CodeChangeResult:
    """Propose a code change and decide whether it may be promoted.

    Two independent gates must BOTH pass, in order:
      1. **Editable-surface boundary** (:func:`review_patch`) — the patch touches
         only allowlisted paths, never the control plane. Refused structurally
         here, before the promotion gate is even consulted.
      2. **Governed promotion ladder** (:func:`maverick.self_improvement.consider`)
         — beats baseline on evidence, no capability escalation, human signature
         at the code/weights rung, calibration healthy, reversible; signed.

    Never applies the patch; the caller does that only when ``result.ok``. Never
    raises — any error yields a non-promoting result (fail-closed). ``samples``
    is the evidence count; ``baseline_score``/``candidate_score`` come from a
    sandbox evaluation the caller injects (Phase 2 wires a real one)."""
    if not enabled():
        return CodeChangeResult(
            PatchReview(False, reason="self-modification disabled"),
            reason="self-modification disabled")
    try:
        review = review_patch(patch, surface or default_surface())
        if not review.ok:
            # The boundary refused: do NOT build a candidate or touch the gate.
            return CodeChangeResult(review, reason=review.reason)

        from .self_improvement import Candidate, consider
        cand = Candidate(
            rung=rung, summary=summary,
            baseline_score=baseline_score, candidate_score=candidate_score,
            samples=samples, payload=patch, payload_sha256=payload_sha256,
            capability_widens=capability_widens,
            approval_signature=approval_signature, rollback=rollback,
        )
        verdict = consider(cand, controller=controller)
        reason = "" if getattr(verdict, "ok", False) else getattr(
            verdict, "blocking_reason", "promotion refused")
        return CodeChangeResult(review, verdict=verdict, reason=reason)
    except Exception:  # pragma: no cover -- a privileged path must never crash a run
        log.warning("self-modify: propose_code_change errored; refusing", exc_info=True)
        return CodeChangeResult(
            PatchReview(False, reason="self-modify controller error"),
            reason="self-modify controller error")


__all__ = [
    "EditableSurface",
    "PatchReview",
    "CodeChangeResult",
    "is_protected",
    "review_patch",
    "propose_code_change",
    "enabled",
    "default_surface",
]
