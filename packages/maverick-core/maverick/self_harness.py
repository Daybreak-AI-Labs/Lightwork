"""Self-Harness: learn a MODEL-SPECIFIC harness addendum from failure traces.

Implements the loop from *"Self-Harness: Harnesses That Improve Themselves"*
(arXiv 2606.09498) on top of Maverick's existing governance spine, rather than
as a new ungoverned optimizer:

  MINE     recurring failure *signatures* from one model's reflexion traces
  PROPOSE  a minimal operating-guidance line targeting each signature
  VALIDATE the proposal on held-in AND held-out cases -- accept only when it
           does not regress either split and helps at least one (the paper's
           rule: reject a pure trade of one split for another)
  GATE     feed the validated change through ``self_improvement.consider()`` on
           the ``prompt`` rung -- so it inherits evidence/causal gating, the
           calibration-freeze interlock, capability non-escalation, reversibility,
           and the signed learning audit, exactly like every other learned rung.

Why this shape. The paper credits its gains to treating the harness as a
*model-specific, learnable* artifact. Maverick already learns *behaviors*
(skills, insights) that are recalled as context; this adds a learned, per-model
**operating-guidance addendum** that is recalled into the system prompt at build
time (:func:`recall_addendum`) -- never a mutation of the kernel templates. So
it sits inside the same "behavior recalled as context, snapshot + rollback"
safety model: an addendum is a file entry, removing it is the rollback handle.

The LLM proposer is an injected seam (:data:`ProposeFn`); a deterministic
fallback composes a guidance line from the signature so the loop runs and is
unit-testable without a provider. Validation likewise takes injected scorers --
a live A/B needs a real model, exactly as ``learning_rollout`` takes injected
constraints. OFF by default (``[self_harness] enable`` / ``MAVERICK_SELF_HARNESS``).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .paths import data_dir

log = logging.getLogger(__name__)

# Keep an addendum short: it rides in EVERY system prompt for that model, so a
# runaway block would tax cache + context on every turn. A handful of crisp
# lines is the whole point ("minimal" in the paper).
_MAX_ADDENDUM_CHARS = 1500
_MAX_LINES_PER_MODEL = 8
# DoS backstop for mining: greedy clustering is worst-case O(n^2), so an
# unbounded trace list could hang a pass. Generous -- the runner feeds at most
# ``limit`` (500) recent traces, so this never trips in normal use; it only
# bounds a pathological direct caller. See mine_failures.
_MAX_MINE_TRACES = 4000
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def enabled() -> bool:
    """Whether the self-harness loop is active. OFF by default (kernel rule 1).

    Turn on with ``MAVERICK_SELF_HARNESS=1`` or ``[self_harness] enable = true``.
    When off, :func:`recall_addendum` returns ``""`` (the prompt is unchanged)
    and :func:`run_self_harness` is a no-op."""
    if os.environ.get("MAVERICK_SELF_HARNESS", "").strip().lower() in {
            "1", "true", "yes", "on"}:
        return True
    try:
        from .config import load_config
        return bool((load_config().get("self_harness") or {}).get("enable", False))
    except Exception:  # pragma: no cover -- config never blocks a run
        return False


def settings() -> dict:
    """Resolved ``[self_harness]`` settings (tuning knobs) with defaults filled
    in. Defaults preserve the loop's historical behavior; an operator opts into
    the stricter floors / optional paths via config or the wizard."""
    try:
        from .config import get_self_harness
        return get_self_harness()
    except Exception:  # pragma: no cover -- config never blocks a run
        return {"enable": False, "min_support": 3, "min_support_by_class": {},
                "require_held_out": False, "min_held_out": 0, "min_delta": 0.0,
                "confidence_z": 0.0, "max_cost_factor": None,
                "max_latency_factor": None, "max_tool_calls_factor": None,
                "semantic_mining": False, "retire_after_days": 0.0,
                "candidates_per_signature": 1, "mine_bucket_by": ()}


def _store_path() -> Path:
    return data_dir("harness") / "addenda.json"


# ---- store (the learned, per-model addenda) -------------------------------

def load_addenda(path: Path | None = None) -> dict[str, str]:
    """The accepted ``{model_id: addendum_text}`` map (empty on any error)."""
    p = path if path is not None else _store_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # Accept ONLY string values: a tampered/corrupt store with a numeric
            # or null value must not coerce to "123"/"None" and get recalled into
            # a prompt as literal garbage.
            return {str(k): v for k, v in data.items()
                    if isinstance(v, str) and v.strip()}
    except (FileNotFoundError, ValueError, OSError):
        pass
    return {}


def _write_addenda(addenda: dict[str, str], path: Path | None = None) -> None:
    # Route through atomic_write_text: a UNIQUE temp + os.replace + chmod 0600
    # that CLEANS THE TEMP UP on any failure. A hand-rolled fixed "<name>.tmp"
    # instead would (a) leave a stale .tmp behind whenever os.replace fails
    # (disk full / read-only FS / target is a dir) and (b) let an UNLOCKED
    # rollback racing a locked apply collide on the shared temp -- one
    # os.replace moving the temp out from under the other. The store itself
    # stays atomic either way, but the stray temp is real. (Found by the
    # fault-injection battery.)
    from .file_lock import atomic_write_text
    p = path if path is not None else _store_path()
    atomic_write_text(p, json.dumps(addenda, indent=2, sort_keys=True), mode=0o600)


def recall_addendum(model_id: str | None, path: Path | None = None) -> str:
    """The learned operating-guidance block for ``model_id`` (``""`` if none /
    disabled). Recalled into the system prompt by the agent at build time."""
    if not model_id or not enabled():
        return ""
    return load_addenda(path).get(str(model_id), "")


# ---- sidecar: structured per-line provenance ------------------------------
# The addenda store is the prompt-bound source of truth and is kept byte-stable
# (its determinism is a proven property). Per-line PROVENANCE (why a line was
# learned, when, on what evidence) lives in a SIBLING ``*.meta.json`` keyed by a
# content-addressed line id, reconciled to the block under the same lock. It is
# strictly auxiliary: a missing/corrupt sidecar never affects recall, and a
# legacy line with no record simply has no provenance (and is never auto-retired,
# since its age is unknown). This is the structured-record foundation that
# unblocks retirement, conflict detection, and post-promotion efficacy.

def _meta_path(addenda_path: Path | None = None) -> Path:
    p = addenda_path if addenda_path is not None else _store_path()
    return p.with_suffix(".meta.json")


def _line_id(model_id: str, line: str) -> str:
    """Stable content-addressed id for a (model, line). Normalized so a trivial
    reword (the delta-merge's notion of "the same line") maps to the same id."""
    h = hashlib.sha256(f"{model_id}\x00{_norm_line(line)}".encode())
    return h.hexdigest()[:16]


def load_line_meta(path: Path | None = None) -> dict[str, dict]:
    """The ``{line_id: record}`` provenance sidecar (empty on any error)."""
    mp = _meta_path(path)
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except (FileNotFoundError, ValueError, OSError):
        pass
    return {}


def _write_line_meta(meta: dict[str, dict], path: Path | None = None) -> None:
    from .file_lock import atomic_write_text
    atomic_write_text(_meta_path(path),
                      json.dumps(meta, indent=2, sort_keys=True), mode=0o600)


def _reconcile_meta(meta: dict[str, dict], model_id: str,
                    current_lines: list[str]) -> dict[str, dict]:
    """Drop sidecar records for ``model_id`` whose line is no longer in the block
    (evicted under the cap, merged, or forgotten). Records for OTHER models are
    untouched. Returns the same dict (mutated) for convenience."""
    valid = {_line_id(model_id, ln) for ln in current_lines}
    for lid in [k for k, r in meta.items()
                if r.get("model_id") == model_id and k not in valid]:
        del meta[lid]
    return meta


def _upsert_line_meta(meta: dict[str, dict], model_id: str, line: str,
                      provenance: dict, *, now: float) -> None:
    """Record/refresh a line's provenance. First-seen ``learned_at`` is kept;
    ``updated_at`` and the evidence refresh on re-promotion."""
    lid = _line_id(model_id, line)
    rec = meta.get(lid) or {}
    rec.update({
        "model_id": model_id, "text": line,
        "signature": provenance.get("signature"),
        "rationale": provenance.get("rationale"),
        "hypothesis": provenance.get("hypothesis"),
        "held_out_delta": provenance.get("held_out_delta"),
        "samples": provenance.get("samples"),
        "learned_at": rec.get("learned_at", now),
        "updated_at": now,
    })
    meta[lid] = rec


def line_provenance(model_id: str, path: Path | None = None) -> list[dict]:
    """Per-line provenance for ``model_id``, ordered to match the recalled block
    (newest last). Each entry is ``{text, signature, rationale, held_out_delta,
    samples, learned_at, updated_at}``; fields are ``None`` for a legacy line
    with no record. Read-only; powers ``self-harness show --verbose``."""
    block = load_addenda(path).get(str(model_id), "")
    meta = load_line_meta(path)
    fields = ("signature", "rationale", "hypothesis", "held_out_delta", "samples",
              "learned_at", "updated_at", "last_recalled_at", "recall_notes")
    out = []
    for ln in _bullets(block):
        rec = meta.get(_line_id(model_id, ln)) or {}
        out.append({"text": ln, **{f: rec.get(f) for f in fields}})
    return out


def _bullets(block: str) -> list[str]:
    return [ln[2:].strip() for ln in (block or "").splitlines()
            if ln.startswith("- ")]


def _norm_line(s: str) -> str:
    """Canonical form for delta-merge equality: case-folded, whitespace-collapsed,
    trailing punctuation stripped. Two lines with the same normal form are the
    SAME guidance reworded only trivially (e.g. an LLM proposer returning
    "Verify the token." then later "verify the token") -- they should refresh,
    not occupy two of the bounded slots. Deliberately EXACT-after-normalization,
    not fuzzy: templated per-class lines differ by a single token, and a fuzzy
    threshold would wrongly merge distinct failure classes."""
    return " ".join(str(s or "").split()).casefold().rstrip(".,;:!? ")


# ---- CONFLICT DETECTION (advisory) ----------------------------------------
# Addenda are CUMULATIVE: a new line can quietly contradict an existing one
# ("prefer streaming large exports" vs "avoid streaming exports; batch first"),
# silently degrading the prompt. This is a cheap DETERMINISTIC heuristic --
# shared topic + OPPOSITE polarity -- that FLAGS suspected conflicts for an
# operator; it never auto-blocks (a false positive must not drop real guidance).
# Stopwords + the loop's own template scaffolding are removed so "topic overlap"
# reflects the guidance content, not the boilerplate every line shares.
_STOPWORDS = frozenset(
    "a an the to of for and or is are be it this that with on in at as your you "
    "when whenever goal resembles past failures kind has have had before after "
    "its their them they not no".split())
_NEG_CUES = ("avoid", "never", "don't", "dont", "do not", "without", "no longer",
             "stop ", "skip ", "n't", "cannot", "can't", " not ", "refrain")


def _content_tokens(line: str) -> set[str]:
    return _tokens(line) - _STOPWORDS


def _is_negative(line: str) -> bool:
    """Whether a guidance line is phrased as a prohibition (avoid/never/don't...)."""
    low = " " + (line or "").lower() + " "
    return any(cue in low for cue in _NEG_CUES)


def find_conflicts(new_line: str, existing_lines: list[str], *,
                   min_overlap: float = 0.5) -> list[str]:
    """Existing lines that appear to CONTRADICT ``new_line``: high content-token
    overlap (same topic) but OPPOSITE polarity (one prohibits what the other
    prefers). Advisory and deliberately conservative (high overlap threshold) --
    the caller surfaces these for review, never auto-drops on them."""
    nt, neg = _content_tokens(new_line), _is_negative(new_line)
    out = []
    for ex in existing_lines or []:
        if _norm_line(ex) == _norm_line(new_line):
            continue
        if _jaccard(nt, _content_tokens(ex)) >= min_overlap and _is_negative(ex) != neg:
            out.append(ex)
    return out


def detect_store_conflicts(model_id: str | None = None,
                           path: Path | None = None) -> list[tuple[str, str, str]]:
    """Suspected contradictory pairs in the CURRENT store as ``(model, a, b)``,
    de-duplicated (each unordered pair once). Powers ``self-harness conflicts``."""
    out: list[tuple[str, str, str]] = []
    for m, block in load_addenda(path).items():
        if model_id is not None and m != model_id:
            continue
        lines = _bullets(block)
        seen: set[frozenset] = set()
        for i, ln in enumerate(lines):
            for other in find_conflicts(ln, lines[i + 1:]):
                key = frozenset((_norm_line(ln), _norm_line(other)))
                if key not in seen:
                    seen.add(key)
                    out.append((m, ln, other))
    return out


def list_learned(path: Path | None = None) -> dict[str, list[str]]:
    """The learned guidance lines per model, for operator inspection.

    Returns ``{model_id: [line, ...]}`` parsed from the stored addenda -- the
    same lines :func:`recall_addendum` injects, minus the framing header. Note
    this reads the STORE regardless of :func:`enabled`; the recall path is what
    gates on the toggle, so an operator can still inspect/roll back what was
    learned while the feature is paused."""
    out: dict[str, list[str]] = {}
    for model_id, block in load_addenda(path).items():
        lines = _bullets(block)
        if lines:
            out[model_id] = lines
    return out


def forget_addendum(model_id: str, *, line: str | None = None,
                    path: Path | None = None) -> bool:
    """Operator rollback: remove a model's learned addendum, or a single line.

    The user-facing undo handle for self-harness learning. The removal is
    serialized + atomic like every other store write, and is itself audited so
    the rollback leaves a trail. Returns ``True`` if something was removed,
    ``False`` if there was nothing matching to remove."""
    p = path if path is not None else _store_path()
    from .file_lock import cross_process_lock
    removed = False
    with _lock, cross_process_lock(p):
        before = load_addenda(p)
        if model_id not in before:
            return False
        after = dict(before)
        if line is None:
            del after[model_id]
            removed = True
        else:
            existing = _bullets(before[model_id])
            kept = [ln for ln in existing if ln != line]
            removed = len(kept) != len(existing)
            if not removed:
                return False
            if kept:
                header = "Operating guidance learned for this model:"
                after[model_id] = header + "\n" + "\n".join(f"- {ln}" for ln in kept)
            else:
                del after[model_id]
        _write_addenda(after, p)
        try:  # keep the provenance sidecar in step (best-effort)
            meta = load_line_meta(p)
            _reconcile_meta(meta, model_id, _bullets(after.get(model_id, "")))
            _write_line_meta(meta, p)
        except Exception:  # pragma: no cover -- sidecar is best-effort
            log.debug("self_harness: line-meta prune failed", exc_info=True)
    try:
        from .audit import EventKind, record
        record(EventKind.LEARNING_UPDATE, agent="self_harness", model_id=model_id,
               rung="prompt", line=line or "*", phase="forget")
    except Exception:  # pragma: no cover -- audit best-effort
        pass
    return removed


def retire_stale(*, older_than_days: float, model_id: str | None = None,
                 now: float | None = None, path: Path | None = None) -> int:
    """Retire learned lines not refreshed within ``older_than_days``.

    Prompt guidance goes stale as models, tools, and APIs change; a loop that
    only ever accumulates eventually carries obsolete instructions. Age is a
    line's ``updated_at`` in the provenance sidecar (re-promotion refreshes it),
    so a line that keeps proving useful stays. A line with NO record (legacy /
    pre-sidecar) is NEVER retired -- its age is unknown. Removes from both the
    addenda block and the sidecar, audits each removal with phase ``retire``, and
    returns the count. ``now`` is injectable for testing. Never raises."""
    p = path if path is not None else _store_path()
    cutoff = (now if now is not None else time.time()) - older_than_days * 86400.0
    removed: list[tuple[str, str]] = []
    from .file_lock import cross_process_lock
    try:
        with _lock, cross_process_lock(p):
            add = load_addenda(p)
            meta = load_line_meta(p)
            after = dict(add)
            for m, block in list(add.items()):
                if model_id is not None and m != model_id:
                    continue
                bullets = _bullets(block)
                kept = []
                for ln in bullets:
                    rec = meta.get(_line_id(m, ln)) or {}
                    # Staleness is by last ACTIVITY: a line refreshed (promoted)
                    # OR used (recalled) recently is not stale.
                    active = [t for t in (rec.get("updated_at"), rec.get("last_recalled_at"))
                              if isinstance(t, (int, float))]
                    last_active = max(active) if active else None
                    (removed.append((m, ln)) if last_active is not None and last_active < cutoff
                     else kept.append(ln))
                if kept == bullets:
                    continue
                if kept:
                    header = "Operating guidance learned for this model:"
                    after[m] = header + "\n" + "\n".join(f"- {x}" for x in kept)
                else:
                    after.pop(m, None)
                _reconcile_meta(meta, m, kept)
            if removed:
                _write_addenda(after, p)
                try:
                    _write_line_meta(meta, p)
                except Exception:  # pragma: no cover -- sidecar best-effort
                    pass
    except Exception:  # pragma: no cover -- retirement never perturbs a run
        log.warning("self_harness: retire_stale failed", exc_info=True)
        return 0
    for m, ln in removed:
        try:
            from .audit import EventKind, record
            record(EventKind.LEARNING_UPDATE, agent="self_harness", model_id=m,
                   rung="prompt", line=ln, phase="retire")
        except Exception:  # pragma: no cover -- audit best-effort
            pass
    return len(removed)


# ---- MINE -----------------------------------------------------------------

@dataclass(frozen=True)
class FailureSignature:
    """A recurring failure pattern for one model -- the weakness to target."""
    model_id: str
    failure_class: str
    signature: str              # short human description of the recurring failure
    support: int                # how many traces back it
    examples: tuple[str, ...]   # representative (sanitized) goal texts
    context: str = ""           # optional mining scope (e.g. "domain=finance")
                                # when failures were bucketed by a finer dimension;
                                # "" = model-wide (the default, backward compatible)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    """Character n-grams of the whitespace-normalized lowercase text. Catches
    morphological overlap that whole-token Jaccard misses (authenticate vs
    authentication share trigrams; share no whole token)."""
    s = " ".join((text or "").lower().split())
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def semantic_similarity(a: str, b: str) -> float:
    """Deterministic, offline, dictionary-free similarity for *semantic-ish*
    mining: the max of token-Jaccard and character-trigram-Jaccard, so
    morphological / reworded variants (authenticate vs authentication, "timeout"
    vs "timed out") cluster even when whole-token overlap is low.

    This is NOT true embedding semantics -- inject a ``similarity_fn`` into
    :func:`mine_failures` for that (the live, model-backed path). It is the
    deterministic default the ``semantic_mining`` flag turns on so enabling the
    feature is a real, reproducible improvement over strict token overlap without
    requiring a provider, and it stays inside the determinism guarantee."""
    tok = _jaccard(_tokens(a), _tokens(b))
    ng = _jaccard(_char_ngrams(a), _char_ngrams(b))
    return max(tok, ng)


def _is_minable(r: object, model_id: str) -> bool:
    """Whether a single reflexion is eligible to be mined for ``model_id``:
    a dict, tagged with this exact model, and UNSCOPED (no channel/user_id).
    The single source of truth for the mining-eligibility guard so the CLI's
    eligible count can never drift from what :func:`mine_failures` actually
    considers."""
    return (isinstance(r, dict)
            and str(r.get("model_id") or "") == str(model_id)
            and r.get("channel") is None and r.get("user_id") is None)


def count_eligible(reflexions: list[dict], *, model_id: str) -> int:
    """How many of ``reflexions`` are eligible to be mined for ``model_id``.

    A reflexion is eligible only if it is tagged with this model AND unscoped
    (the trace-poisoning guard drops scoped/remote-user failures). The operator
    CLI uses this to explain why scanning N reflexions mined nothing: "scanned
    N" can far exceed the eligible count when failures are scoped or belong to a
    different model, and a bare "no weaknesses" otherwise reads as "this model
    never fails" rather than "those failures were excluded by design"."""
    return sum(1 for r in (reflexions or []) if _is_minable(r, model_id))


# Dimensions a caller may bucket mining by (per-domain etc.), beyond the always-on
# (model, failure_class). Allowlisted so a caller can't bucket by a high-cardinality
# free-text field (e.g. goal_text) and shatter every trace into its own group.
# ``domain`` is the grounded one -- the department pack a run executed as, already
# tracked on the reflexion and used by dreaming for per-department consolidation.
_BUCKET_DIMS = ("domain",)


def _bucket_key(r: dict, bucket_by: tuple[str, ...]) -> tuple[str, ...]:
    """The extra group key for one reflexion under ``bucket_by`` (a tuple of
    allowlisted scalar reflexion fields). Missing/None -> "" so untagged traces
    still cluster together rather than each forming a singleton bucket."""
    return tuple("" if r.get(k) is None else str(r.get(k)) for k in bucket_by)


def _context_str(bucket_by: tuple[str, ...], values: tuple[str, ...]) -> str:
    """Human "domain=finance" scope string from a bucket key (empty parts dropped)."""
    return ", ".join(f"{k}={v}" for k, v in zip(bucket_by, values, strict=False) if v)


def mine_failures(
    reflexions: list[dict], *, model_id: str, min_support: int = 3,
    similarity: float = 0.3, min_support_by_class: dict[str, int] | None = None,
    similarity_fn: Callable[[str, str], float] | None = None,
    bucket_by: tuple[str, ...] = (),
) -> list[FailureSignature]:
    """Cluster ONE model's failure traces into recurring signatures.

    Model-specific by design (the paper's key lever): only traces whose
    ``model_id`` matches are considered, so a weakness mined for one model never
    leaks into another's harness. Within a model, traces are grouped by
    ``failure_class`` then greedily clustered by goal-text overlap; only clusters
    with ``>= min_support`` members survive (a one-off is noise, not a pattern).
    ``min_support < 1`` disables mining (returns nothing).

    ``similarity_fn`` is the SEMANTIC-MINING seam: an injected
    ``(text_a, text_b) -> [0,1]`` similarity (e.g. embedding cosine) used in place
    of the default token-Jaccard, so failures that mean the same thing in
    different words cluster together. It must be DETERMINISTIC for the loop's
    determinism guarantee to hold; :func:`semantic_similarity` is the built-in
    deterministic default the ``semantic_mining`` flag selects. A raising/garbage
    fn degrades to "no match" (a fresh cluster), never crashing the pass.

    ``bucket_by`` mines at a FINER granularity than model-wide: an allowlisted
    tuple of scalar reflexion fields (``("domain",)``) sub-groups failures so a
    department-specific weakness yields a *department-scoped* signature (its
    ``context`` records the bucket and the proposed guidance is scoped to it).
    Unknown/high-cardinality dims are rejected by the caller (see ``_BUCKET_DIMS``);
    the always-on (model, failure_class) grouping is unchanged when empty.

    SCOPE GUARD (trace-poisoning defense): only UNSCOPED failures -- ones with
    no ``channel`` and no ``user_id`` -- are mined. A scoped reflexion came from
    a remote user on some channel and may carry attacker-influenced goal/failure
    text; the addendum is recalled into EVERY future run of this model (across
    all channels/tenants), so admitting scoped text would let a hostile caller
    poison the harness cross-channel. This mirrors dreaming's unscoped-only
    promotion guard. A purely-local operator's runs are unscoped, so this costs
    nothing in the intended single-operator case."""
    if min_support < 1:
        return []
    mine = [r for r in (reflexions or []) if _is_minable(r, model_id)]
    # DoS backstop: greedy clustering is O(n*clusters) -- worst case O(n^2) when
    # every trace is a distinct goal. The runner already feeds only the most
    # recent ``limit`` (500) traces, but mine_failures is public; a direct caller
    # passing a huge list would otherwise hang the pass. Cap at a generous bound
    # (well above the runner's 500) and keep the most RECENT slice, so a runaway
    # input is bounded without affecting any realistic call. (Found by the
    # algorithmic-complexity battery.)
    if len(mine) > _MAX_MINE_TRACES:
        log.warning("self_harness: mining %d traces capped to %d (most recent)",
                    len(mine), _MAX_MINE_TRACES)
        mine = mine[-_MAX_MINE_TRACES:]
    # Canonicalize order so mining is DETERMINISTIC and permutation-invariant:
    # the greedy clustering and the by-class grouping both depend on iteration
    # order, so the same failures in a different log order would otherwise mine
    # different weaknesses -- non-reproducible learning from identical evidence.
    mine.sort(key=lambda r: (str(r.get("failure_class") or "error"),
                             str(r.get("goal_text") or ""),
                             str(r.get("failure_msg") or "")))
    # Group by (failure_class, bucket-key). With the default empty ``bucket_by``
    # the bucket-key is ``()`` for every record -> one group per class, identical
    # to the historical by-class grouping (the determinism proof relies on this).
    bucket_by = tuple(bucket_by or ())
    by_group: dict[tuple[str, tuple[str, ...]], list[dict]] = {}
    for r in mine:
        key = (str(r.get("failure_class") or "error"), _bucket_key(r, bucket_by))
        by_group.setdefault(key, []).append(r)

    out: list[FailureSignature] = []
    # sorted() so group iteration order is deterministic regardless of dict
    # insertion order (matters once a class splits into several buckets).
    for (fclass, bucket_vals), recs in sorted(by_group.items()):
        context = _context_str(bucket_by, bucket_vals)
        # Adaptive support: some failure classes warrant a lower (or higher)
        # evidence bar -- two identical auth failures against the same connector
        # are plenty; two unrelated timeouts may not be. Per-class override falls
        # back to the global floor; clamped to >=1 so it can never admit a one-off.
        eff_support = min_support
        if min_support_by_class and fclass in min_support_by_class:
            try:
                eff_support = max(1, int(min_support_by_class[fclass]))
            except (TypeError, ValueError):
                eff_support = min_support
        clusters: list[list[dict]] = []
        # Cache each cluster HEAD's token set (parallel to ``clusters``). The
        # head's goal text never changes, so re-tokenizing it on every
        # comparison -- as a naive ``_tokens(cluster[0][...])`` in the inner loop
        # does -- is pure waste: it turned the greedy pass into a tokenize-bound
        # O(n^2) that took ~69s on 8k traces. Tokenize each head once instead.
        # Behaviour is identical: ``heads[i]`` is the token set of ``clusters[i]``'s
        # first member, exactly what the old code recomputed. (Found by the
        # algorithmic-complexity battery.)
        heads: list[set[str]] = []
        head_texts: list[str] = []      # parallel head goal-texts for similarity_fn
        for r in recs:
            rtext = str(r.get("goal_text", ""))
            rt = _tokens(rtext)
            for i, htok in enumerate(heads):
                if similarity_fn is not None:
                    try:
                        sim = float(similarity_fn(head_texts[i], rtext))
                    except Exception:  # a bad seam can't crash mining -> no match
                        sim = 0.0
                else:
                    sim = _jaccard(rt, htok)
                if sim >= similarity:
                    clusters[i].append(r)
                    break
            else:
                clusters.append([r])
                heads.append(rt)
                head_texts.append(rtext)
        for cluster in clusters:
            if len(cluster) < eff_support:
                continue
            examples = tuple(
                dict.fromkeys(  # de-dupe, preserve order
                    str(c.get("goal_text", "")).strip().splitlines()[0][:160]
                    for c in cluster if str(c.get("goal_text", "")).strip()
                )
            )[:3]
            sig_text = _summarize_signature(cluster)
            # Fold the bucket scope into the signature TEXT too (when present) so
            # it shows up in `show`, the audit, and conflict detection for free,
            # and so two domains' same-class clusters are distinct signatures.
            if context:
                sig_text = f"{sig_text} [{context}]"
            out.append(FailureSignature(
                model_id=str(model_id), failure_class=fclass,
                signature=sig_text, support=len(cluster), examples=examples,
                context=context,
            ))
    # Strongest weaknesses first.
    out.sort(key=lambda s: s.support, reverse=True)
    return out


def _summarize_signature(cluster: list[dict]) -> str:
    """A short, deterministic description of what keeps going wrong."""
    fclass = str(cluster[0].get("failure_class") or "error")
    # Most common short failure message in the cluster, if any.
    msgs = [str(c.get("failure_msg") or "").strip() for c in cluster]
    msgs = [m for m in msgs if m]
    # sorted() before max() so a tie in frequency breaks deterministically
    # (alphabetically) instead of by set iteration order.
    common = max(sorted(set(msgs)), key=msgs.count) if msgs else ""
    common = common.splitlines()[0][:120] if common else ""
    return f"{fclass}: {common}" if common else fclass


# ---- PROPOSE --------------------------------------------------------------

@dataclass(frozen=True)
class HarnessProposal:
    """A candidate operating-guidance line targeting one failure signature."""
    model_id: str
    signature: str
    addendum_line: str          # the minimal guidance to add
    rationale: str
    hypothesis: str = ""         # optional richer "why this line should help",
                                 # supplied by a structured proposer. METADATA
                                 # ONLY -- it rides into the audit + provenance
                                 # sidecar, never into the recalled prompt.


# An injected proposer: given a signature, return EITHER a single short guidance
# line (str) OR a structured mapping ``{"line": str, "hypothesis"?: str}`` -- a
# richer proposer (e.g. an LLM in the GEPA/RPT shape) can return its reasoning
# alongside the line. The model proposes how to avoid its OWN recurring failure.
# Pure/seam.
ProposeFn = Callable[[FailureSignature], "str | Mapping[str, str]"]


def _proposal_parts(raw: object) -> tuple[str, str]:
    """Split a proposer's return into ``(line, hypothesis)``. A bare string is
    the line with no hypothesis; a mapping may carry ``line`` (or the legacy
    ``addendum_line``) plus an optional ``hypothesis``. Anything else is empty --
    a malformed return degrades to "no proposal", it never crashes the loop."""
    if isinstance(raw, Mapping):
        line = raw.get("line") or raw.get("addendum_line") or ""
        hypothesis = raw.get("hypothesis") or ""
        return (str(line), str(hypothesis))
    return (str(raw or ""), "")

def _sanitize_line(text: str) -> str:
    """Neutralize a proposed addendum line before it can enter a prompt.

    Defense-in-depth on top of the unscoped-only mining guard: the line still
    derives from trace text and (with an LLM proposer) from model output, so
    strip control chars, collapse all whitespace to single spaces (no multi-line
    break-out), and scrub secrets. The result is one bounded plain-prose line.

    Stripping is by UNICODE CATEGORY, not an ASCII regex: every control char
    (category ``Cc`` -- C0, the C1 0x80-0x9f block, and DEL) and every format
    char (``Cf`` -- zero-width ZWSP/ZWNJ/ZWJ, the BOM, and bidi overrides like
    RLO/LRO/isolates) is replaced with a space. An ASCII-only ``[\\x00-\\x1f\\x7f]``
    pass let the whole non-ASCII slice through, and this line lands in EVERY
    system prompt for the model AND in the signed audit + addenda.json a human
    reviews: a C1 byte injects a terminal escape when that log is cat'd, a
    zero-width char splits a trigger word past a downstream filter, and an RLO
    visually reverses the guidance an auditor reads. Zl/Zp/Zs separators are
    Unicode whitespace, so ``split()`` below already collapses them. (Found by
    the adversarial input-fuzzing battery.)"""
    raw = str(text or "")
    cleaned = "".join(
        " " if unicodedata.category(c) in {"Cc", "Cf"} else c for c in raw)
    line = " ".join(cleaned.split())  # collapse all whitespace incl. newlines
    try:
        from .secrets import scrub
        line = scrub(line)
    except Exception:  # pragma: no cover -- scrubbing must never break the loop
        pass
    return line.strip()


# Semantic policy-erosion screen for the proposed LINE itself. `_sanitize_line`
# defends SYNTAX (control chars, secrets, multi-line) and the gate enforces
# capability non-escalation on the CANDIDATE; neither reads the MEANING of the
# prose that will ride in every future prompt for this model. A line that tells
# the model to disable/bypass/ignore its own safety machinery is exactly the
# poisoning a trace-influenced (or buggy) proposer could smuggle as plain prose.
# Matches an erosion VERB near a safety-control NOUN within a clause. Deliberately
# conservative: it refuses even negated mentions ("don't skip validation") because
# the lesson should be stated POSITIVELY ("always run validation") -- a line that
# talks about ignoring safety at all is a smell in always-on guidance. The
# class-grounded fallback lines use only positive verbs (verify/check/validate/
# avoid/refresh), so they never trip it. (Threat raised by an external review.)
_POLICY_EROSION_RE = re.compile(
    r"\b(?:ignore|bypass|disable|circumvent|evade|override|overrule|suppress|"
    r"skip|conceal|hide|turn\s+off|opt\s+out\s+of)\b"
    r"[\w\s,'\"()-]{0,40}?\b(?:safety|shield|guard\s?rails?|guardrails?|"
    r"validations?|validator|verifier|verification|polic(?:y|ies)|approvals?|"
    r"consent|authentication|authorization|auth|credentials?|permissions?|"
    r"sandbox|audit|budget|spending|uncertainty|warnings?)\b",
    re.IGNORECASE)


def _erodes_policy(line: str) -> bool:
    """Whether a proposed guidance line tells the model to weaken its own safety
    machinery (disable/bypass/ignore validation, auth, budget, sandbox, audit,
    ...). Refused in :func:`propose_addendum` as defense-in-depth."""
    return bool(_POLICY_EROSION_RE.search(line or ""))


# Failure-class-grounded guidance for the deterministic proposer. arXiv
# 2603.23994 warns the STARTING ARTIFACT bounds what the loop can ever learn, so
# a generic "slow down and verify" line is a weak seed. These per-class lines
# are specific AND -- unlike the generic fallback -- do NOT embed the
# trace-derived signature text into the prompt, so they need no sanitization.
_CLASS_GUIDANCE: dict[str, str] = {
    "timeout": "this kind of goal has timed out before; budget the work, prefer "
               "incremental/streaming steps, and check for long-running "
               "operations before you start.",
    "auth": "this kind of goal has failed on authentication before; verify "
            "credentials and token freshness (refresh if near expiry) before the call.",
    "parse": "this kind of goal has failed on parsing before; validate the "
             "response shape before parsing and handle malformed or partial data.",
    "tool_error": "this kind of goal has hit a tool error before; check the "
                  "tool's preconditions and arguments and read its error output "
                  "before retrying.",
    "shield": "this kind of goal has been blocked by the safety shield before; "
              "stay within policy and avoid the action that tripped it.",
    "max_steps": "this kind of goal has run out of steps before; plan the fewest "
                 "steps to the result and avoid exploratory detours.",
    "budget": "this kind of goal has exhausted its budget before; do the cheapest "
              "sufficient work first and avoid redundant tool calls.",
    "agent_error": "this kind of goal has failed mid-run before; re-read the "
                   "error, confirm preconditions, and take the smallest safe "
                   "next step rather than retrying blindly.",
}


def _default_propose(sig: FailureSignature) -> str:
    """Deterministic fallback proposer (no LLM): a failure-class-grounded
    guidance line, templated so the loop runs and is testable without a
    provider. Specific by class; falls back to a generic (sanitized) line for an
    unknown class. A bucket ``context`` (e.g. domain-scoped mining) is folded in
    so the guidance names the scope it was learned in."""
    # ``sig.context`` is "domain=finance"-style (derived from allowlisted scalar
    # fields), so it carries no trace-derived free text into the prompt.
    scope = f" (seen in {sig.context})" if sig.context else ""
    specific = _CLASS_GUIDANCE.get(sig.failure_class)
    if specific:
        return f"When a goal resembles your past {sig.failure_class} failures{scope}, {specific}"
    return (f"When a goal resembles your past {sig.failure_class} failures{scope} "
            f"({sig.signature}), slow down and verify the precondition that "
            f"tripped you before acting.")


def _parse_structured_proposal(text: str) -> dict | None:
    """Best-effort parse of a structured proposer's JSON ``{"line","hypothesis"}``.
    Tolerates a fenced ```json block or surrounding prose by extracting the first
    balanced ``{...}`` slice. Returns ``None`` if nothing parseable with a string
    ``line`` is found -- the caller then falls back to plain-line extraction, so a
    model that ignores the format still works."""
    s = str(text or "")
    i, j = s.find("{"), s.rfind("}")
    if i == -1 or j == -1 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    line = obj.get("line") or obj.get("addendum_line") or ""
    hyp = obj.get("hypothesis") or ""
    if not isinstance(line, str) or not line.strip():
        return None
    return {"line": line, "hypothesis": hyp if isinstance(hyp, str) else ""}


def llm_proposer(llm, *, budget=None, model: str | None = None,
                 max_tokens: int = 240, structured: bool = True) -> ProposeFn:
    """Build a REFLECTIVE proposer backed by an LLM -- the GEPA/RPT shape
    (arXiv 2507.19457, 2605.21781): read the mined failure signature + example
    goals and write ONE minimal operating-guidance line. Injected exactly like
    any other ``propose_fn``; the model is NOT hard-coded (kernel rule 2) --
    pass ``model`` or let the ``llm``'s own resolved model stand.

    With ``structured`` (default) the proposer asks for a small JSON object
    ``{"line", "hypothesis"}`` so the model's REASONING is captured alongside the
    line (it rides into the audit + provenance sidecar, never the prompt). The
    parse is best-effort: a model that returns a bare line instead still works
    (the text becomes the line), and any provider/parse error falls OPEN to
    :func:`_default_propose`, so a flaky or off-format model never blocks a pass.
    Whichever shape comes back, the line still flows through ``propose_addendum``'s
    ``_sanitize_line`` + length + policy gates, so an attacker-influenced model
    cannot smuggle control chars, secrets, or multi-line break-outs."""
    def _propose(sig: FailureSignature):
        try:
            if structured:
                system = (
                    "You tune a coding agent's operating guidance. Given a "
                    "recurring failure pattern for ONE model, return a JSON object "
                    'with two string fields: "line" -- a single short imperative '
                    "guidance line (<=200 characters, no markdown, no newlines) "
                    "that, added to that model's system prompt, would help it "
                    'avoid this class of failure; and "hypothesis" -- one sentence '
                    "on why that line should help. Be specific and minimal. Return "
                    "only the JSON object.")
            else:
                system = (
                    "You tune a coding agent's operating guidance. Given a recurring "
                    "failure pattern for ONE model, write a single short imperative "
                    "guidance line (<=200 characters, no preamble, no markdown, no "
                    "newlines) that, added to that model's system prompt, would help "
                    "it avoid this class of failure. Be specific and minimal.")
            examples = "\n".join(f"- {e}" for e in sig.examples[:3]) or "(none)"
            user = (f"Model: {sig.model_id}\nFailure class: {sig.failure_class}\n"
                    f"Signature: {sig.signature}\nExample goals that failed:\n"
                    f"{examples}\n\n" + ("JSON:" if structured else "Guidance line:"))
            resp = llm.complete(system, [{"role": "user", "content": user}],
                                budget=budget, max_tokens=max_tokens, model=model)
            text = (getattr(resp, "text", "") or "")
            if structured:
                parsed = _parse_structured_proposal(text)
                if parsed:
                    return parsed
            # Plain-line extraction -- also the fallback when JSON parsing failed.
            line = next((s.strip() for s in text.splitlines() if s.strip()), "")
            line = line.lstrip("-*•> ").strip().strip('"').strip()
            if line:
                return line
        except Exception as e:  # pragma: no cover -- fail open to deterministic
            log.warning("self_harness: llm proposer failed (%s); using fallback", e)
        return _default_propose(sig)
    return _propose


def propose_addendum(sig: FailureSignature, *, propose_fn: ProposeFn | None = None,
                     ) -> HarnessProposal | None:
    """Produce a MINIMAL guidance line for ``sig`` via the injected proposer
    (or the deterministic fallback). The proposer may return a bare line or a
    structured ``{"line", "hypothesis"?}`` mapping -- the hypothesis (a richer
    'why this should help') is carried as METADATA into the audit + provenance,
    never into the prompt. Returns ``None`` if the proposal is empty or too long
    to be 'minimal'."""
    fn = propose_fn or _default_propose
    try:
        line, hypothesis = _proposal_parts(fn(sig))
    except Exception as e:  # pragma: no cover -- a bad proposer can't crash the loop
        log.warning("self_harness: proposer failed for %s (%s)", sig.signature, e)
        return None
    line = _sanitize_line(line)  # control chars + multi-line + secrets neutralized
    if not line or len(line) > 280:
        return None
    if _erodes_policy(line):  # semantic screen: no "bypass/ignore <safety>" guidance
        log.warning("self_harness: refused policy-eroding proposal: %r", line)
        return None
    # The hypothesis rides into the signed audit + the provenance sidecar (a human
    # reads both), so it gets the SAME syntactic scrub as the line -- control
    # chars / secrets / multi-line stripped -- plus a length bound. It is NOT
    # policy-screened: it never enters a prompt, it only annotates WHY a line was
    # learned, so the positive-phrasing rule the prompt needs doesn't apply.
    hypothesis = _sanitize_line(hypothesis)[:280]
    return HarnessProposal(
        model_id=sig.model_id, signature=sig.signature,
        addendum_line=line, hypothesis=hypothesis,
        rationale=f"targets {sig.support} '{sig.failure_class}' failures",
    )


# ---- VALIDATE -------------------------------------------------------------

@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    held_in_delta: float
    held_out_delta: float
    reason: str
    # Raw held-out A/B (the unseen-split generalization signal) + case count, so
    # the gate's evidence check judges the honest baseline-vs-candidate numbers.
    baseline_score: float = 0.0
    candidate_score: float = 0.0
    samples: int = 0


# A scorer returns either a plain success rate in [0,1], OR a mapping
# ``{"success": rate, "cost"?: float, "latency"?: float, "tool_calls"?: float}``
# so validation can also gate on OPERATIONAL regressions (a line can raise the
# pass rate while making the model slower / pricier / chattier). Injected: a
# live A/B needs a real model, exactly like learning_rollout's constraints.
ScoreFn = Callable[[str, list[str]], object]


def _wilson_lower_bound(successes: float, n: int, z: float = 1.96) -> float:
    """Lower bound of the Wilson score interval for a binomial proportion --
    the statistically-honest "how good is this, conservatively?" given ``n``
    observations. A 1-of-1 win has a low bound; a 50-of-50 win a high one."""
    if n <= 0:
        return 0.0
    phat = max(0.0, min(1.0, successes / n))
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = phat + z2 / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z2 / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def _score_parts(v: object) -> tuple:
    """(success, cost, latency, tool_calls) from a scorer result -- a bare rate
    or a mapping. Non-numeric/missing fields are ``None`` and simply not gated."""
    if isinstance(v, Mapping):
        return (v.get("success"), v.get("cost"), v.get("latency"), v.get("tool_calls"))
    return (v, None, None, None)


def validate_proposal(
    proposal: HarnessProposal, *, held_in: list[str], held_out: list[str],
    score_with: ScoreFn, score_without: ScoreFn,
    min_delta: float = 0.0, min_held_out: int = 0, confidence_z: float = 0.0,
    max_cost_factor: float | None = None, max_latency_factor: float | None = None,
    max_tool_calls_factor: float | None = None,
) -> ValidationResult:
    """The Self-Harness acceptance test: a harness edit must not regress EITHER
    split and must help at least one (reject pure trades).

    ``held_in`` are the mined cases the edit was written against; ``held_out``
    are unseen cases that guard against overfitting the edit to its own examples
    (the paper's central failure mode). ``score_with``/``score_without`` return
    a success rate (or a mapping with ``success`` + optional ``cost``/
    ``latency``/``tool_calls``) for the prompt WITH vs WITHOUT the candidate line.

    All of the following default OFF (preserving the base rule); a live caller
    opts in. ``min_delta`` (effect-size floor) and ``min_held_out`` (unseen-sample
    floor) -- a sub-threshold lift or a 1-of-1 held-out win is not durable
    evidence. ``confidence_z`` (>0) requires the candidate's held-out Wilson lower
    bound to clear the baseline rate -- statistically-confident generalization,
    not luck. ``max_*_factor`` reject a candidate that regresses cost/latency/
    tool-calls on the held-out split beyond the factor (a line in every prompt
    must not quietly make the model slower or pricier)."""
    add = proposal.addendum_line
    in_with, in_ci, in_li, in_ti = _score_parts(score_with(add, held_in))
    in_without, *_ = _score_parts(score_without(add, held_in))
    out_with, out_cost, out_lat, out_tc = _score_parts(score_with(add, held_out))
    out_without, owo_cost, owo_lat, owo_tc = _score_parts(score_without(add, held_out))
    n = len(held_in) + len(held_out)
    # A score is a success RATE in [0,1]. A buggy/hostile scorer returning a
    # non-finite (NaN/inf) or out-of-range value must NOT drive a promotion: an
    # inf candidate would otherwise sail past the gate's "candidate >= baseline"
    # evidence check. Reject up front. (Found by the 1000-round fuzz campaign.)
    scores = (in_with, in_without, out_with, out_without)
    if not all(isinstance(s, (int, float)) and math.isfinite(s) and 0.0 <= s <= 1.0
               for s in scores):
        return ValidationResult(False, 0.0, 0.0,
                                "scorer returned a non-finite or out-of-range value",
                                baseline_score=0.0, candidate_score=0.0, samples=n)
    in_delta, out_delta = in_with - in_without, out_with - out_without
    # The gate sees the unseen split as the honest baseline/candidate evidence.
    base = dict(baseline_score=out_without, candidate_score=out_with, samples=n)
    # Unseen-sample floor: too few held-out cases is not trustworthy generalization.
    if min_held_out and len(held_out) < min_held_out:
        return ValidationResult(False, in_delta, out_delta,
                                f"too few held-out cases ({len(held_out)} < {min_held_out})",
                                **base)
    # Non-negative on both, strictly positive on at least one.
    if in_delta < 0 or out_delta < 0:
        return ValidationResult(False, in_delta, out_delta,
                                "regressed a split (held-in or held-out)", **base)
    if in_delta <= 0 and out_delta <= 0:
        return ValidationResult(False, in_delta, out_delta,
                                "no improvement on either split", **base)
    # Effect-size floor: a sub-threshold lift is noise, not a durable improvement.
    if max(in_delta, out_delta) < min_delta:
        return ValidationResult(
            False, in_delta, out_delta,
            f"improvement below threshold ({max(in_delta, out_delta):.4g} < {min_delta})",
            **base)
    # Statistical confidence: be CONFIDENT the candidate beats the baseline on
    # unseen cases, not just lucky on a small sample (Wilson lower bound).
    if confidence_z > 0 and held_out:
        n_out = len(held_out)
        lb = _wilson_lower_bound(round(out_with * n_out), n_out, confidence_z)
        if lb < out_without:
            return ValidationResult(
                False, in_delta, out_delta,
                f"held-out improvement not confident (lb {lb:.3g} < baseline {out_without:.3g})",
                **base)
    # Operational regression: a line that lifts pass rate but blows up cost,
    # latency, or tool-call churn on the unseen split is not a net win.
    for label, factor, cand, basel in (
            ("cost", max_cost_factor, out_cost, owo_cost),
            ("latency", max_latency_factor, out_lat, owo_lat),
            ("tool_calls", max_tool_calls_factor, out_tc, owo_tc)):
        if (factor is not None and isinstance(cand, (int, float))
                and isinstance(basel, (int, float)) and cand > basel * factor):
            return ValidationResult(
                False, in_delta, out_delta,
                f"{label} regressed on held-out ({cand:.3g} > {basel:.3g} x {factor})",
                **base)
    return ValidationResult(True, in_delta, out_delta, "validated", **base)


# ---- APPLY / ROLLBACK (the reversible handle the gate requires) -----------

def _compose_addendum(model_id: str, existing: str, line: str) -> str:
    """Append ``line`` to a model's addendum block, bounded + deduped."""
    header = "Operating guidance learned for this model:"
    lines = []
    for raw in (existing or "").splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("Operating guidance"):
            continue
        if ln.startswith("- "):
            ln = ln[2:].strip()
        lines.append(ln)
    # Delta-merge (ACE anti-collapse, arXiv 2510.04618): drop any existing line
    # that normalizes to the SAME guidance as the new one, then append the new
    # one last (newest). This refreshes an exact OR trivially-reworded re-promote
    # to newest (renewed relevance) rather than leaving it where this pass's
    # other new lines would evict it under the newest-wins cap -- which left a
    # promoted line absent from the store (found by the 100k soak) -- and stops a
    # case/whitespace variant from consuming a second of the bounded slots and
    # evicting DISTINCT guidance (the "context collapse / brevity bias" mode).
    # Normalized-EXACT, not fuzzy: templated per-class lines differ by one token,
    # so a similarity threshold would wrongly merge distinct failure classes.
    nl = _norm_line(line)
    lines = [ln for ln in lines if _norm_line(ln) != nl]
    lines.append(line)
    lines = lines[-_MAX_LINES_PER_MODEL:]

    # Enforce the char budget by dropping WHOLE OLDEST bullets, not by slicing
    # the rendered block. A trailing ``block[:_MAX_ADDENDUM_CHARS]`` cut the
    # NEWEST bullets off the end (inverting the newest-wins cap) and could sever
    # a bullet mid-line -- corrupting the last stored line so a later re-promote
    # of it no longer dedups. Lines are <=280 chars, so ~6 max-length lines
    # already overflow 1500: this is reachable, not theoretical. Drop from the
    # front (oldest) until it fits; keep at least the single newest line.
    # (Found by the stateful sequence battery.)
    def _render(ls: list[str]) -> str:
        return header + "\n" + "\n".join(f"- {x}" for x in ls) if ls else header

    while len(lines) > 1 and len(_render(lines)) > _MAX_ADDENDUM_CHARS:
        lines.pop(0)
    # Last-resort guard for a single pathological line wider than the buffer
    # (can't happen under the 280-char propose cap, but stays defensive).
    return _render(lines)[:_MAX_ADDENDUM_CHARS]


def _rollback_handle(path: Path | None = None) -> Callable[[], None]:
    """A thunk restoring the addendum store to its CURRENT state -- the
    reversible handle the gate requires. Captured before any write, so it is a
    valid undo whether or not the change is ultimately applied. Restores the
    provenance sidecar too, so a revert leaves no orphaned metadata."""
    p = path if path is not None else _store_path()
    before = load_addenda(p)
    before_meta = load_line_meta(p)

    def _rollback() -> None:
        _write_addenda(before, p)
        try:
            _write_line_meta(before_meta, p)
        except Exception:  # pragma: no cover -- sidecar is best-effort
            pass

    return _rollback


# Serializes the store's load-modify-save so concurrent passes (the runner, a
# manual CLI, parallel per-model passes) can't clobber each other. The whole
# read-compose-write is one critical section -- an in-process lock plus a
# cross-process flock -- because without it two writers that both read the old
# store both write back, losing one's addendum (8 concurrent promotions
# collapsed to 1 in a forced-interleaving probe).
_lock = threading.Lock()


def _apply_addendum(proposal: HarnessProposal, path: Path | None = None, *,
                    provenance: dict | None = None) -> None:
    """Write the accepted line into the model's addendum block (atomically), and
    record its provenance in the sidecar reconciled to the block actually
    written. The sidecar update is best-effort: a failure there never perturbs
    the addenda store (the source of truth)."""
    p = path if path is not None else _store_path()
    from .file_lock import cross_process_lock
    with _lock, cross_process_lock(p):
        before = load_addenda(p)
        after = dict(before)
        new_block = _compose_addendum(
            proposal.model_id, before.get(proposal.model_id, ""),
            proposal.addendum_line)
        after[proposal.model_id] = new_block
        _write_addenda(after, p)
        try:
            meta = load_line_meta(p)
            _upsert_line_meta(meta, proposal.model_id, proposal.addendum_line,
                              provenance or {}, now=time.time())
            _reconcile_meta(meta, proposal.model_id, _bullets(new_block))
            _write_line_meta(meta, p)
        except Exception:  # pragma: no cover -- sidecar is best-effort
            log.debug("self_harness: line-meta update failed", exc_info=True)


# ---- RECALL-USAGE TRACKING ------------------------------------------------
# recall_addendum() rides the agent's per-prompt hot path and stays a PURE READ.
# Usage ("when was this guidance last actually used?") is tracked separately by
# the consumer (Agent._with_harness_addendum) via note_recall, which is
# IN-PROCESS THROTTLED: the common case is a dict lookup with NO I/O, and disk is
# touched at most once per model per interval. This lets retire_stale keep a line
# that's still being USED even if it hasn't been re-PROMOTED in a while.
_recall_note_lock = threading.Lock()
_recall_noted_monotonic: dict[str, float] = {}


def note_recall(model_id: str | None, *, now: float | None = None,
                path: Path | None = None, min_interval_s: float = 3600.0) -> None:
    """Best-effort: record that ``model_id``'s guidance was just recalled, so
    staleness can be judged by USE, not only by promotion. In-process throttled
    (``min_interval_s``; 0 disables the throttle, for tests) so the hot recall
    path does at most one cheap write per model per interval. Never raises."""
    if not model_id:
        return
    mid = str(model_id)
    if min_interval_s:
        mono = time.monotonic()
        with _recall_note_lock:
            last = _recall_noted_monotonic.get(mid)
            if last is not None and (mono - last) < min_interval_s:
                return  # throttled -- pure in-memory, no I/O
            _recall_noted_monotonic[mid] = mono
    try:
        p = path if path is not None else _store_path()
        ts = now if now is not None else time.time()
        from .file_lock import cross_process_lock
        with _lock, cross_process_lock(p):
            meta = load_line_meta(p)
            changed = False
            for ln in _bullets(load_addenda(p).get(mid, "")):
                rec = meta.get(_line_id(mid, ln))
                if rec is not None:
                    rec["last_recalled_at"] = ts
                    rec["recall_notes"] = (rec.get("recall_notes") or 0) + 1
                    changed = True
            if changed:
                _write_line_meta(meta, p)
    except Exception:  # pragma: no cover -- usage tracking never perturbs a run
        log.debug("self_harness: note_recall failed", exc_info=True)


# ---- DRIVE (mine -> propose -> validate -> gate) --------------------------

@dataclass
class SelfHarnessReport:
    model_id: str
    mined: int = 0
    proposed: int = 0
    validated: int = 0
    promoted: int = 0
    skipped: list[str] = field(default_factory=list)
    applied_lines: list[str] = field(default_factory=list)
    # Advisory: (new_line, existing_line) pairs the promoted line may contradict.
    conflicts: list[tuple[str, str]] = field(default_factory=list)


def _best_validated_candidate(
    sig: FailureSignature, *, first: HarnessProposal, propose_fn, k: int,
    held_in: list[str], held_out: list[str], score_with, score_without,
    validate_kwargs: dict,
) -> tuple[HarnessProposal | None, ValidationResult | None]:
    """Best-of-N candidate selection (GEPA-style): generate up to ``k`` candidate
    lines for one signature, validate each, and keep the strongest ACCEPTED one.

    ``first`` is the proposal already in hand (counted once by the caller); up to
    ``k-1`` more are drawn from ``propose_fn`` and de-duped by normalized line, so
    a DETERMINISTIC proposer -- which returns the same line every call -- collapses
    to exactly the single-candidate result and ``k`` only ever matters for a
    STOCHASTIC (e.g. LLM-at-temperature) proposer. "Best" is the highest held-out
    delta (the generalization signal), ties broken toward more samples; operational
    REGRESSIONS were already rejected inside ``validate_proposal``, so this only
    PREFERS the strongest survivor. ``(None, None)`` if none pass."""
    candidates = [first]
    for _ in range(max(0, k - 1)):
        p = propose_addendum(sig, propose_fn=propose_fn)
        if p is not None:
            candidates.append(p)
    seen: set[str] = set()
    best: tuple[tuple, HarnessProposal, ValidationResult] | None = None
    for proposal in candidates:
        norm = _norm_line(proposal.addendum_line)
        if norm in seen:
            continue
        seen.add(norm)
        vr = validate_proposal(
            proposal, held_in=held_in, held_out=held_out,
            score_with=score_with, score_without=score_without, **validate_kwargs)
        if not vr.accepted:
            continue
        key = (round(vr.held_out_delta, 6), vr.samples)
        if best is None or key > best[0]:
            best = (key, proposal, vr)
    if best is None:
        return None, None
    return best[1], best[2]


def run_self_harness(
    reflexions: list[dict], *, model_id: str,
    held_in: list[str] | None = None, held_out: list[str] | None = None,
    score_with: ScoreFn | None = None, score_without: ScoreFn | None = None,
    propose_fn: ProposeFn | None = None, controller=None,
    min_support: int = 3, path: Path | None = None,
    require_held_out: bool = False, min_delta: float = 0.0, min_held_out: int = 0,
    confidence_z: float = 0.0, max_cost_factor: float | None = None,
    max_latency_factor: float | None = None, max_tool_calls_factor: float | None = None,
    min_support_by_class: dict[str, int] | None = None,
    candidates_per_signature: int = 1,
    semantic_mining: bool = False,
    similarity_fn: Callable[[str, str], float] | None = None,
    bucket_by: tuple[str, ...] = (),
) -> SelfHarnessReport:
    """One self-harness pass for ``model_id``: mine weaknesses, propose minimal
    edits, validate on held-in/held-out, and GATE each survivor through the
    self-improvement ladder before applying it.

    ``score_with``/``score_without`` (the live A/B) are injected; without them
    validation is skipped and the pass is a dry inspection (nothing is applied).
    ``semantic_mining`` (or an explicit ``similarity_fn``) clusters failures by
    meaning rather than strict token overlap -- an injected fn wins, else the flag
    selects the deterministic built-in :func:`semantic_similarity`. ``bucket_by``
    mines at a finer granularity (e.g. ``("domain",)`` for department-scoped
    weaknesses + guidance).
    Returns a :class:`SelfHarnessReport`. Never raises -- a learning pass must
    not perturb anything."""
    report = SelfHarnessReport(model_id=str(model_id))
    if not enabled():
        report.skipped.append("disabled")
        return report
    try:
        sim_fn = similarity_fn or (semantic_similarity if semantic_mining else None)
        sigs = mine_failures(reflexions, model_id=model_id, min_support=min_support,
                             min_support_by_class=min_support_by_class,
                             similarity_fn=sim_fn, bucket_by=bucket_by)
        report.mined = len(sigs)
        for sig in sigs:
            # The addendum block holds at most _MAX_LINES_PER_MODEL lines. Stop
            # once this pass has filled them: signatures are sorted STRONGEST
            # (highest support) first, so processing more would only let a weaker
            # line evict a stronger one under the newest-wins cap -- and would
            # gate + audit a promotion we'd immediately discard. Keep the
            # strongest weaknesses; report the rest as deferred.
            if report.promoted >= _MAX_LINES_PER_MODEL:
                report.skipped.append(f"addendum at capacity: {sig.signature}")
                continue
            proposal = propose_addendum(sig, propose_fn=propose_fn)
            if proposal is None:
                report.skipped.append(f"no proposal: {sig.signature}")
                continue
            report.proposed += 1

            if score_with is None or score_without is None:
                report.skipped.append(f"no scorer (dry): {proposal.addendum_line}")
                continue
            # Held-out is the overfitting guard the docs call mandatory; with a
            # live scorer, `require_held_out` makes that explicit -- never promote
            # on only the mined examples (default off for back-compat).
            if require_held_out and not held_out:
                report.skipped.append(f"no held-out cases: {proposal.addendum_line}")
                continue
            validate_kwargs = dict(
                min_delta=min_delta, min_held_out=min_held_out,
                confidence_z=confidence_z, max_cost_factor=max_cost_factor,
                max_latency_factor=max_latency_factor,
                max_tool_calls_factor=max_tool_calls_factor)
            hi, ho = held_in or list(sig.examples), held_out or []
            if candidates_per_signature and candidates_per_signature > 1:
                # Best-of-N: keep the strongest of several candidate lines for this
                # signature (only meaningful with a stochastic proposer; a
                # deterministic one collapses to the single candidate).
                proposal, vr = _best_validated_candidate(
                    sig, first=proposal, propose_fn=propose_fn,
                    k=candidates_per_signature, held_in=hi, held_out=ho,
                    score_with=score_with, score_without=score_without,
                    validate_kwargs=validate_kwargs)
                if proposal is None or vr is None:
                    report.skipped.append(f"rejected (no candidate passed): {sig.signature}")
                    continue
            else:
                vr = validate_proposal(
                    proposal, held_in=hi, held_out=ho, score_with=score_with,
                    score_without=score_without, **validate_kwargs)
                if not vr.accepted:
                    report.skipped.append(f"rejected ({vr.reason}): {proposal.addendum_line}")
                    continue
            report.validated += 1

            ok, why = _gate_and_apply(proposal, vr, controller=controller, path=path)
            if not ok:
                # Surface WHY the gate refused (e.g. "too few samples (3 < 5)",
                # frozen verifier, disabled controller) so an operator with a
                # small validation set or a drifting judge isn't left guessing.
                report.skipped.append(f"gate refused ({why}): {proposal.addendum_line}")
                continue
            report.promoted += 1
            report.applied_lines.append(proposal.addendum_line)
            # Advisory conflict check: the new line is now in the block; flag any
            # EXISTING line it appears to contradict (operator review, not a block).
            existing = [ln for ln in _bullets(load_addenda(path).get(str(model_id), ""))
                        if _norm_line(ln) != _norm_line(proposal.addendum_line)]
            for c in find_conflicts(proposal.addendum_line, existing):
                report.conflicts.append((proposal.addendum_line, c))
                log.warning("self_harness: learned line may conflict with existing "
                            "guidance for %s: %r vs %r", model_id,
                            proposal.addendum_line, c)
    except Exception as e:  # pragma: no cover -- learning never perturbs a run
        log.warning("self_harness: pass failed (%s)", e)
        report.skipped.append(f"error: {e}")
    return report


def _gate_and_apply(proposal: HarnessProposal, vr: ValidationResult, *,
                    controller=None, path: Path | None = None) -> tuple[bool, str]:
    """Judge the validated proposal through ``self_improvement.consider()`` on
    the ``prompt`` rung and apply it ONLY on a promoting verdict. Returns
    ``(ok, reason)`` -- ``reason`` names the blocking gate when refused.

    Promotion therefore requires the self-improvement controller to be engaged
    (``[self_improvement] enable``): self_harness mines/proposes/validates, but
    the *promotion decision* -- evidence floor, calibration-freeze interlock,
    reversibility -- belongs to the one governed gate every rung shares. The
    rollback handle is captured BEFORE any write, so the reversibility gate is
    satisfied and a refused candidate leaves the store untouched."""
    from . import self_improvement as si

    cand = si.Candidate(
        rung="prompt",
        summary=f"self-harness addendum [{proposal.model_id}]: {proposal.addendum_line}",
        baseline_score=vr.baseline_score,
        candidate_score=vr.candidate_score,
        samples=vr.samples,
        payload={"model_id": proposal.model_id, "line": proposal.addendum_line},
        rollback=_rollback_handle(path),
        provenance={"source": "self_harness", "signature": proposal.signature,
                    "hypothesis": proposal.hypothesis or None},
    )
    verdict = si.consider(cand, controller=controller)
    if not getattr(verdict, "ok", False):
        reason = getattr(verdict, "blocking_reason", "") or "refused"
        return False, reason  # gate refused -> nothing written
    _apply_addendum(proposal, path=path, provenance={
        "signature": proposal.signature, "rationale": proposal.rationale,
        "hypothesis": proposal.hypothesis or None,
        "held_out_delta": round(vr.held_out_delta, 4), "samples": vr.samples})
    try:
        from .audit import EventKind, record
        # Record the DIAGNOSTIC PROVENANCE alongside the line: which weakness it
        # targets (signature/rationale) and the unseen-split evidence that
        # promoted it (held-out delta over N samples). A reversible, signed
        # learning audit should answer "why was this learned?", not just "what"
        # -- the provenance a compliance review / rollback decision needs
        # (arXiv 2508.07407's evaluation-and-safety requirement).
        record(EventKind.LEARNING_UPDATE, agent="self_harness",
               model_id=proposal.model_id, rung="prompt",
               line=proposal.addendum_line, phase="apply",
               signature=proposal.signature, rationale=proposal.rationale,
               hypothesis=proposal.hypothesis or None,
               held_out_delta=round(vr.held_out_delta, 4), samples=vr.samples)
    except Exception:  # pragma: no cover -- audit best-effort
        pass
    return True, "promoted"


__all__ = [
    "enabled", "recall_addendum", "load_addenda",
    "list_learned", "forget_addendum", "retire_stale",
    "load_line_meta", "line_provenance", "note_recall",
    "find_conflicts", "detect_store_conflicts",
    "FailureSignature", "mine_failures", "count_eligible", "semantic_similarity",
    "HarnessProposal", "ProposeFn", "propose_addendum", "llm_proposer",
    "ValidationResult", "ScoreFn", "validate_proposal",
    "SelfHarnessReport", "run_self_harness",
]
