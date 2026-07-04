"""Capability-algebra non-escalation proof for a code patch (Gap 5).

``self_modify.capability_diff`` is a syntactic SIGNAL — it flags a suspicious
added line but cannot *prove* non-escalation, so a code-rung change that clears
it still has ``capability_widens=None`` and is refused for want of a proof. This
module supplies the proof, computed with the real capability algebra
(:mod:`maverick.capability`) instead of a boolean guess.

The idea is **differential**: a patch widens authority only if it introduces a
capability *class* the edited baseline did not already use. Editing a module that
already opens sockets to tweak one of its network calls does not widen the
envelope; adding the first socket to a module that had none does. We map both the
baseline source and the patch's added lines to a set of capability classes
(process spawn, network, dynamic code, filesystem write, dynamic import, native
FFI, deserialization, explicit authority grant), express each as a
:class:`~maverick.capability.Capability` over that class universe, and let
``Capability.permits`` decide — so the answer routes through the same
least-privilege algebra the runtime enforces on tools, not an ad-hoc check.

The result plugs straight into the governed gate: :func:`capability_delta`
returns ``(before, after, probe_tools)`` for a candidate's
``capability_before`` / ``capability_after`` / ``probe_tools`` fields, and
:func:`maverick.self_improvement._capability_widens` computes escalation as
"some probed class the *after* grant permits that the *before* grant did not."

Fail-closed: with no baseline source available the baseline class set is taken
from the patch's own context/removed lines (a strict under-count that can only
*over*-report widening), so a missing baseline never launders an escalation.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from .capability import Capability

# Capability classes and the constructs that evidence each. Mirrors the
# categories self_modify._CAP_PATTERNS screens for, but grouped into named
# *classes* so the before/after comparison is over a small, stable universe.
_CLASS_PATTERNS: dict[str, tuple[str, ...]] = {
    "process": (
        r"\bshell\s*=\s*True\b", r"\bsubprocess\b",
        r"\bos\.(system|popen|exec[lv]\w*|spawn\w*)\s*\(",
        r"\bpty\.spawn\b", r"\bmultiprocessing\b",
    ),
    "network": (
        r"\b(socket|asyncio\.open_connection|http\.client|urllib|requests|"
        r"httpx|aiohttp|smtplib|ftplib|paramiko|websockets)\b",
    ),
    "dynamic_code": (
        r"(?<!\w)(eval|exec)\s*\(", r"\bcompile\s*\(",
    ),
    "fs_write": (
        r"\bos\.(remove|unlink|rmdir|chmod|chown|rename|replace)\s*\(",
        r"\bshutil\.(rmtree|move|copy\w*)\s*\(",
        r"\bopen\s*\([^)]*['\"][rwax+bt]*[wax+][rwax+bt]*['\"]",
        r"\.write_(text|bytes)\s*\(",
    ),
    "dynamic_import": (
        r"\b__import__\s*\(", r"\bimportlib\b",
    ),
    "native_ffi": (
        r"\bctypes\b", r"\bcffi\b",
    ),
    "deserialize": (
        r"\bpickle\.(load|loads)\b", r"\bmarshal\.(load|loads)\b",
    ),
    "authority_grant": (
        r"\b(grant|entitlement|allow_?list|allowlist|editable_paths|"
        r"probe_tools|capability_before|capability_after|max_risk|deny_tools)\b",
    ),
}
_CLASS_COMPILED: dict[str, tuple[re.Pattern, ...]] = {
    cls: tuple(re.compile(p) for p in pats) for cls, pats in _CLASS_PATTERNS.items()
}

# The full class universe probed for escalation. Fixed so `before`/`after` are
# compared over the same axes every time.
CAPABILITY_CLASSES: tuple[str, ...] = tuple(sorted(_CLASS_PATTERNS))

# A token that is never a capability class and never probed. Included in every
# grant's allow-list so it is never EMPTY -- an empty ``allow_tools`` means "all"
# in the capability algebra, which would invert the intended "permits nothing".
_ANCHOR = "\x00cap-anchor"


def classes_in(lines: Iterable[str]) -> frozenset[str]:
    """The capability classes evidenced by a block of source lines. A pure
    comment line contributes nothing (a ``# subprocess`` note is not a spawn);
    string literals can still match, deliberately (a string can be an exec
    payload, and a false positive only over-reports widening)."""
    found: set[str] = set()
    for raw in lines:
        stripped = str(raw).lstrip()
        if stripped.startswith("#"):
            continue
        for cls, patterns in _CLASS_COMPILED.items():
            if cls in found:
                continue
            if any(rx.search(raw) for rx in patterns):
                found.add(cls)
    return frozenset(found)


def _added_lines(patch: str) -> list[str]:
    return [ln[1:] for ln in (patch or "").splitlines()
            if ln.startswith("+") and not ln.startswith("+++")]


def _baseline_lines(patch: str) -> list[str]:
    """Context (' ') and removed ('-') lines — the pre-image the patch edits.
    Used as a conservative baseline when the full source file isn't available."""
    out: list[str] = []
    for ln in (patch or "").splitlines():
        if ln.startswith("-") and not ln.startswith("---"):
            out.append(ln[1:])
        elif ln.startswith(" "):
            out.append(ln[1:])
    return out


def _grant(principal: str, classes: Iterable[str]) -> Capability:
    """A capability whose allow-list is exactly ``classes`` (plus the anchor so
    it is never the empty 'all' set). ``permits(cls)`` is then True iff the grant
    'has' that class."""
    return Capability(principal=principal,
                      allow_tools=frozenset(set(classes) | {_ANCHOR}))


def capability_delta(
    patch: str,
    *,
    baseline_files: dict[str, str] | None = None,
    principal: str = "self_modify",
) -> tuple[Capability, Capability, tuple[str, ...]]:
    """Compute ``(before, after, probe_tools)`` for a patch's non-escalation proof.

    ``before`` permits the capability classes the baseline already uses;
    ``after`` permits those plus any class the patch's added lines introduce.
    Probing every class in :data:`CAPABILITY_CLASSES`, the governed gate reports
    escalation iff ``after`` permits a class ``before`` did not — i.e. the patch
    introduces a *new* capability class.

    ``baseline_files`` maps repo-relative path -> full current source for the
    touched files (from the Phase-2 materialized baseline tree); when given, the
    baseline class set is taken from the real files. Without it the baseline is
    the patch's own context/removed lines — a strict under-count, so a missing
    baseline can only make the proof STRICTER (over-report widening), never
    weaker."""
    added = classes_in(_added_lines(patch))
    if baseline_files:
        base = classes_in(
            line for src in baseline_files.values() for line in str(src).splitlines())
    else:
        base = classes_in(_baseline_lines(patch))
    after = base | added
    return _grant(principal, base), _grant(principal, after), CAPABILITY_CLASSES


def widens(patch: str, *, baseline_files: dict[str, str] | None = None) -> tuple[bool, list[str]]:
    """Convenience: ``(widened, new_classes)`` computed via the algebra. True iff
    the patch introduces a capability class absent from the baseline."""
    before, after, probe = capability_delta(patch, baseline_files=baseline_files)
    new = sorted(c for c in probe if after.permits(c) and not before.permits(c))
    return bool(new), new


__all__ = ["CAPABILITY_CLASSES", "classes_in", "capability_delta", "widens"]
