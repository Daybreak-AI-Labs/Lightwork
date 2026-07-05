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
    "approval_delegation.py",    # approval routing / quorum
    "calibration.py",            # the drift / reward-laundering interlock
    "verifier.py",               # the FINAL correctness gate
    "capability.py",             # the capability algebra (non-escalation proof)
    "budget.py",                 # cost caps (kernel rule 3)
    "entitlements.py",           # the license gate on the promotion engine
    "promotion_effect.py",       # the evidence gate's causal input
    "evaluator_evolution.py",    # anchor-gated judge promotion
    "migration_governance.py",   # released-migration immutability gate
    "llm.py",                    # ROLE_MODELS / model routing
    "config.py",                 # resolves every gate's settings
    "migrate.py",                # the config-section registry
    "sigstore_signing.py",       # signing primitives
    "erasure_verify.py",
})
# Whole packages under ``maverick`` that are off-limits (any file within).
_PROTECTED_DIRS = frozenset({"audit", "sandbox"})
# Case-folded copies: the boundary must hold on case-insensitive filesystems
# (macOS/Windows), where ``Self_Improvement.py`` IS ``self_improvement.py``.
_PROTECTED_FILES_CF = frozenset(f.casefold() for f in _PROTECTED_FILES)
_PROTECTED_DIRS_CF = frozenset(d.casefold() for d in _PROTECTED_DIRS)

# git C-style single-char escapes inside a quoted path (see quote_c_style).
_C_ESCAPES = {
    "a": "\a", "b": "\b", "t": "\t", "n": "\n", "v": "\v",
    "f": "\f", "r": "\r", '"': '"', "\\": "\\",
}


def _unquote_git(path: str) -> str | None:
    """Decode a git C-quoted path (``"a/na\\303\\251me"`` etc.) to the real
    filename, or return the input unchanged if it isn't quoted. Returns None on
    malformed quoting so the caller can fail closed.

    git quotes any path with special/high bytes and ``git apply`` un-quotes it,
    so the boundary MUST decode octal (``\\NNN``) and ``\\t``/``\\n``/... escapes
    before matching -- otherwise ``"a/maverick/self_improvement.py"`` (or an
    octal-escaped spelling) slips past a raw-string comparison."""
    if not (len(path) >= 2 and path[0] == '"' and path[-1] == '"'):
        return path
    body = path[1:-1]
    out = bytearray()
    i = 0
    try:
        while i < len(body):
            ch = body[i]
            if ch != "\\":
                out.extend(ch.encode("utf-8"))
                i += 1
                continue
            nxt = body[i + 1]
            if nxt in "01234567":
                j = 0
                while j < 3 and i + 1 + j < len(body) and body[i + 1 + j] in "01234567":
                    j += 1
                out.append(int(body[i + 1:i + 1 + j], 8) & 0xFF)
                i += 1 + j
            elif nxt in _C_ESCAPES:
                out.extend(_C_ESCAPES[nxt].encode("utf-8"))
                i += 2
            else:
                out.append(ord(nxt) & 0xFF)
                i += 2
        return out.decode("utf-8", errors="replace")
    except (ValueError, IndexError):
        return None


def _canonical(raw: str) -> str | None:
    """Canonicalize a patch path EXACTLY as ``git apply`` would resolve it, so
    the boundary classifies the real target: un-quote git C-escapes, drop an
    ``a/``/``b/`` diff prefix, normalize ``\\`` to ``/``, collapse ``//`` and
    ``.`` segments. Returns None (uncanonicalizable -> the caller fails closed)
    for an absolute path, any ``..`` traversal, malformed quoting, or an empty
    result."""
    s = str(raw).strip()
    if not s:
        return None
    if s.startswith('"'):
        s = _unquote_git(s)
        if s is None:
            return None
    s = s.replace("\\", "/")
    if s.startswith(("a/", "b/")):
        s = s[2:]
    if s.startswith("/"):
        return None  # absolute -> reaches outside the repo tree
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts) or None


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

    Canonicalizes first (:func:`_canonical`) so a quoted / ``//`` / ``\\`` /
    ``..`` / absolute spelling can't dodge the check, and matches case-insensitively
    so a re-cased path can't dodge it on macOS/Windows. Fail-closed: a path that
    can't be canonicalized (traversal, absolute, malformed quoting) is treated as
    protected."""
    c = _canonical(path)
    if c is None:
        return True
    tail = _maverick_tail(c.split("/"))
    if tail is None:
        return False  # not a maverick source file; the allowlist decides
    if tail and tail[0].casefold() in _PROTECTED_DIRS_CF:
        return True
    return len(tail) == 1 and tail[0].casefold() in _PROTECTED_FILES_CF


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
        """Return ``"protected"`` | ``"editable"`` | ``"unknown"``.

        Matching is against the canonicalized path, so a quoted/`//`/`..` spelling
        can't slip a control-plane file past the protected check into the
        allowlist. Note: ``editable_globs`` use fnmatch, where ``*`` spans ``/`` --
        keep them as narrow as possible, since the allowlist is what turns
        ``unknown`` (refused) into ``editable`` (appliable)."""
        if is_protected(path):
            return "protected"
        c = _canonical(path)
        if c is None:  # defensive: is_protected already fails these closed
            return "protected"
        for g in self.editable_globs:
            if fnmatch.fnmatch(c, g):
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


# --- capability-diff: a heuristic non-escalation SIGNAL -----------------------

def capability_diff(patch: str) -> tuple[bool, list[str]]:
    """Heuristic scan for capability-widening constructs a patch ADDS.

    Returns ``(widens, reasons)``. ``widens`` is True iff the patch's added lines
    evidence any capability class (process spawn, network, dynamic code,
    filesystem write, dynamic import, native FFI, deserialization, an explicit
    tool/entitlement grant). The construct taxonomy is defined ONCE in
    :mod:`maverick.self_modify_capability` (``classes_in``) and shared with the
    algebra screen, so the two detectors can never drift apart; ``reasons`` are
    that module's human labels for the detected classes.

    This is a **signal, not a proof**. It is deliberately one-directional --
    :func:`propose_code_change` uses a positive hit to force
    ``capability_widens=True`` (blocking the change), but never treats a clean
    scan as a standalone certificate of non-escalation; the code rung's human
    signature is the backstop for what a regex cannot catch."""
    from .self_modify_capability import _added_lines, classes_in_source, describe_classes
    hit = classes_in_source("\n".join(_added_lines(patch)))
    return bool(hit), describe_classes(hit)


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
        # getattr(None, "ok", False) is already False, so no explicit None guard.
        return bool(self.review.ok and getattr(self.verdict, "ok", False))


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
    baseline_files: dict[str, str] | None = None,
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

        # Capability screen (one-directional): a syntactic hit FORCES
        # ``capability_widens=True`` so the gate refuses. A caller-supplied True
        # is honoured; a caller-supplied False is NOT allowed to override a
        # positive screen (a heuristic must be able to make the gate stricter,
        # never weaker). When the caller said nothing and the screen is clean we
        # leave it None, so the code rung still demands a real proof.
        widens_hit, widen_reasons = capability_diff(patch)
        if widens_hit:
            if capability_widens is False:
                log.info("self-modify: capability screen overrode "
                         "capability_widens=False: %s", widen_reasons)
            capability_widens = True

        # Real non-escalation PROOF via the capability algebra (Gap 5): a
        # differential over a fixed class universe, so the gate can compute
        # escalation ("a class the *after* grant permits that *before* did not")
        # instead of only trusting a boolean. When the caller declared
        # ``capability_widens`` (incl. the heuristic's forced True) that wins;
        # otherwise the algebra decides, and unlike a bare None it is a genuine
        # proof the code rung can pass on. Best-effort: a delta error leaves the
        # proof unset so the code rung falls back to demanding one (fail-closed).
        cap_before = cap_after = None
        probe: tuple[str, ...] = ()
        try:
            from .self_modify_capability import capability_delta
            cap_before, cap_after, probe = capability_delta(
                patch, baseline_files=baseline_files)
        except Exception:  # pragma: no cover -- proof is best-effort
            log.debug("self-modify: capability_delta failed", exc_info=True)

        from .self_improvement import Candidate, consider
        cand = Candidate(
            rung=rung, summary=summary,
            baseline_score=baseline_score, candidate_score=candidate_score,
            samples=samples, payload=patch, payload_sha256=payload_sha256,
            capability_widens=capability_widens,
            capability_before=cap_before, capability_after=cap_after,
            probe_tools=probe,
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
    "capability_diff",
    "propose_code_change",
    "enabled",
    "default_surface",
]
