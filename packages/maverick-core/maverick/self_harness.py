"""Self-Harness: learn a MODEL-SPECIFIC harness addendum from failure traces.

Implements the loop from *"Self-Harness: Harnesses That Improve Themselves"*
(arXiv 2606.09498) on top of Lightwork's existing governance spine, rather than
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
*model-specific, learnable* artifact. Lightwork already learns *behaviors*
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
# Bound per-text semantic feature extraction. Goal descriptions can be long and
# semantic mining compares each record against cluster heads; capping the
# offline trigram input and caching extracted features below prevents long
# attacker-controlled failure texts from turning maintenance mining into a CPU
# and memory churn amplifier while preserving short/normal goal behavior.
_MAX_SEMANTIC_TEXT_CHARS = 4096
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
                "candidates_per_signature": 1, "mine_bucket_by": (),
                "holdout_rotations": 1, "judge_samples": 1,
                "promote_as_canary": False,
                "efficacy_review": False, "eval_corpus": None}


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


def _scoped_key(model_id: str, context: str) -> str:
    """The store key for a (model, mining-scope). Model-wide guidance (no context)
    lives under the bare ``model_id`` -- so the default store is byte-identical to
    before scoping existed. A domain-scoped line lives under a COMPOSITE key
    ``"<model_id>\\x00<context>"`` (e.g. ``"<model>\\x00domain=finance"``); the NUL
    can't appear in a model id, so the two namespaces never collide."""
    return f"{model_id}\x00{context}" if context else str(model_id)


def _scope_contexts(domain: str | None = None,
                    tools: list[str] | tuple[str, ...] | None = None) -> list[str]:
    """The ordered, deduped list of mining-scope context strings for a run:
    ``domain=<d>`` first (one department per run), then ``tool=<t>`` for each tool
    the run had on hand, SORTED so the recalled block is deterministic regardless
    of the agent's tool-registry iteration order. Empty entries are dropped. The
    shared basis for recall / usage / outcome scoping so all three stay in step."""
    out: list[str] = []
    if domain:
        out.append(f"domain={domain}")
    for t in sorted({str(t) for t in (tools or []) if t}):
        out.append(f"tool={t}")
    return out


def recall_addendum(model_id: str | None, path: Path | None = None, *,
                    domain: str | None = None,
                    tools: list[str] | tuple[str, ...] | None = None) -> str:
    """The learned operating-guidance block for ``model_id`` (``""`` if none /
    disabled). Recalled into the system prompt by the agent at build time.

    The model-wide block is always included. When ``domain`` is given, guidance
    mined SCOPED to that department is appended (so a finance lesson rides only
    finance runs); when ``tools`` are given, guidance mined scoped to each tool the
    run has on hand is appended (so a ``web_fetch`` lesson rides only runs that can
    call ``web_fetch`` -- #7 component profiles). With neither (the default) this is
    exactly the model-wide block, byte-for-byte unchanged."""
    if not model_id or not enabled():
        return ""
    store = load_addenda(path)
    block = store.get(str(model_id), "")
    for ctx in _scope_contexts(domain, tools):
        scoped = store.get(_scoped_key(str(model_id), ctx), "")
        if scoped:
            block = (block + "\n" + scoped) if block else scoped
    return block


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
    # Canary is only touched when the caller passes it (a non-canary re-promote
    # must not clear a probation flag, nor stamp canary=False on every record).
    if "canary" in provenance:
        rec["canary"] = bool(provenance.get("canary"))
    meta[lid] = rec


def line_provenance(model_id: str, path: Path | None = None) -> list[dict]:
    """Per-line provenance for ``model_id`` across ALL its scopes, ordered to match
    the recalled blocks (newest last). Each entry is ``{text, domain, signature,
    rationale, held_out_delta, samples, learned_at, updated_at}``; fields are
    ``None`` for a legacy line with no record, and ``domain`` is ``None`` for a
    model-wide line. Includes domain-scoped lines (which the pre-fix version
    missed). Read-only; powers ``self-harness show --verbose``."""
    store = load_addenda(path)
    meta = load_line_meta(path)
    fields = ("signature", "rationale", "hypothesis", "held_out_delta", "samples",
              "learned_at", "updated_at", "last_recalled_at", "recall_notes")
    out = []
    for k, ln in _iter_model_lines(store, str(model_id)):
        rec = meta.get(_line_id(k, ln)) or {}
        _, ctx = _split_key(k)
        domain = ctx[len("domain="):] if ctx.startswith("domain=") else None
        out.append({"text": ln, "domain": domain, **{f: rec.get(f) for f in fields}})
    return out


def _bullets(block: str) -> list[str]:
    return [ln[2:].strip() for ln in (block or "").splitlines()
            if ln.startswith("- ")]


def _split_key(store_key: str) -> tuple[str, str]:
    """Decompose a store key into ``(model_id, context)`` -- the inverse of
    :func:`_scoped_key`. A bare model key yields ``(model_id, "")``; a composite
    ``"<model>\\x00domain=finance"`` yields ``("<model>", "domain=finance")``. Used
    wherever a store key is SURFACED to an operator/audit/dashboard so the raw NUL
    never leaks."""
    mid, _, ctx = str(store_key).partition("\x00")
    return mid, ctx


def _model_store_keys(store: dict, model_id: str) -> list[str]:
    """Every store key belonging to ``model_id``: the bare key (if present) plus
    every composite ``"<model_id>\\x00..."`` scope key, in deterministic order. The
    management surface (forget/retire/efficacy/canary) walks this so a model's
    DOMAIN-scoped lines are never invisible to it -- with no composite keys the
    list is just ``[model_id]``, so the default store behaves exactly as before."""
    mid = str(model_id)
    keys = [mid] if mid in store else []
    keys += sorted(k for k in store if isinstance(k, str) and k.startswith(mid + "\x00"))
    return keys


def _iter_model_lines(store: dict, model_id: str):
    """Yield ``(store_key, line)`` over every line in every scope of ``model_id``,
    so a reader can compute the right ``_line_id(store_key, line)`` for the sidecar
    record actually written by :func:`_apply_addendum`."""
    for k in _model_store_keys(store, model_id):
        for ln in _bullets(store.get(k, "")):
            yield k, ln


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
                   min_overlap: float = 0.5,
                   classifier_fn: Callable[[str, str], bool] | None = None) -> list[str]:
    """Existing lines that appear to CONTRADICT ``new_line``: high content-token
    overlap (same topic) but OPPOSITE polarity (one prohibits what the other
    prefers). Advisory and deliberately conservative (high overlap threshold) --
    the caller surfaces these for review, never auto-drops on them.

    ``classifier_fn`` is the SEMANTIC seam: an injected ``(a, b) -> bool`` judge
    (e.g. an LLM) that decides contradiction by MEANING -- it catches conflicts
    the lexical heuristic misses (reworded, no token overlap) and suppresses its
    false positives. When present it is authoritative per pair; a classifier that
    RAISES falls back to the heuristic for that pair, so a flaky judge never drops
    a real flag. Addenda are bounded (<= _MAX_LINES_PER_MODEL), so the O(n^2)
    pairwise calls stay tiny. Still advisory -- the gate never blocks on it."""
    nt, neg = _content_tokens(new_line), _is_negative(new_line)
    out = []
    for ex in existing_lines or []:
        if _norm_line(ex) == _norm_line(new_line):
            continue
        if classifier_fn is not None:
            try:
                if bool(classifier_fn(new_line, ex)):
                    out.append(ex)
                continue  # classifier is authoritative for this pair
            except Exception:  # a bad judge can't drop a real conflict -> heuristic
                pass
        if _jaccard(nt, _content_tokens(ex)) >= min_overlap and _is_negative(ex) != neg:
            out.append(ex)
    return out


def detect_store_conflicts(model_id: str | None = None,
                           path: Path | None = None, *,
                           classifier_fn: Callable[[str, str], bool] | None = None,
                           ) -> list[tuple[str, str, str]]:
    """Suspected contradictory pairs in the CURRENT store as ``(model, a, b)``,
    de-duplicated (each unordered pair once). Powers ``self-harness conflicts``.
    An optional ``classifier_fn`` (see :func:`find_conflicts`) refines the lexical
    heuristic with a semantic judge."""
    out: list[tuple[str, str, str]] = []
    for store_key, block in load_addenda(path).items():
        mid, ctx = _split_key(store_key)
        if model_id is not None and mid != model_id:
            continue
        # Surface a READABLE label, never the raw NUL composite key: "M" for
        # model-wide, "M [domain=finance]" for a scoped block.
        label = mid if not ctx else f"{mid} [{ctx}]"
        lines = _bullets(block)
        seen: set[frozenset] = set()
        for i, ln in enumerate(lines):
            for other in find_conflicts(ln, lines[i + 1:], classifier_fn=classifier_fn):
                key = frozenset((_norm_line(ln), _norm_line(other)))
                if key not in seen:
                    seen.add(key)
                    out.append((label, ln, other))
    return out


def list_learned(path: Path | None = None) -> dict[str, list[str]]:
    """The learned guidance lines per model, for operator inspection.

    Returns ``{model_id: [line, ...]}`` parsed from the stored addenda -- the
    same lines :func:`recall_addendum` injects, minus the framing header. Keys are
    the BARE model id (never the raw NUL composite key); a domain-scoped line is
    listed under its model with a ``"  [domain=…]"`` tag so an operator/dashboard
    sees the scope without the store's internal key layout. Note this reads the
    STORE regardless of :func:`enabled`; the recall path is what gates on the
    toggle, so an operator can still inspect/roll back what was learned while the
    feature is paused."""
    out: dict[str, list[str]] = {}
    for store_key, block in load_addenda(path).items():
        lines = _bullets(block)
        if not lines:
            continue
        mid, ctx = _split_key(store_key)
        tagged = lines if not ctx else [f"{ln}  [{ctx}]" for ln in lines]
        out.setdefault(mid, []).extend(tagged)
    return out


def forget_addendum(model_id: str, *, line: str | None = None,
                    domain: str | None = None, path: Path | None = None) -> bool:
    """Operator rollback: remove a model's learned addendum, or a single line.

    The user-facing undo handle for self-harness learning. By default it spans ALL
    the model's scopes (so a full rollback can't orphan a domain block); pass
    ``domain`` to scope the removal to just that department's block. The removal is
    serialized + atomic like every other store write, and is itself audited so the
    rollback leaves a trail. Returns ``True`` if something was removed, ``False``
    if there was nothing matching to remove."""
    p = path if path is not None else _store_path()
    from .file_lock import cross_process_lock
    removed = False
    with _lock, cross_process_lock(p):
        before = load_addenda(p)
        # Span ALL of the model's scopes (bare + composite "<model>\x00domain=...")
        # so a full rollback can't ORPHAN a domain-scoped block that recall still
        # injects, and a single-line forget can target a line in any scope. With an
        # explicit domain, operate ONLY on that scope's composite key.
        if domain:
            sk = _scoped_key(str(model_id), f"domain={domain}")
            keys = [sk] if sk in before else []
        else:
            keys = _model_store_keys(before, model_id)
        if not keys:
            return False
        after = dict(before)
        touched: list[str] = []
        for k in keys:
            if line is None:
                del after[k]
                touched.append(k)
                removed = True
            else:
                existing = _bullets(before[k])
                kept = [ln for ln in existing if ln != line]
                if len(kept) == len(existing):
                    continue  # the line isn't in this scope's block
                removed = True
                touched.append(k)
                if kept:
                    header = "Operating guidance learned for this model:"
                    after[k] = header + "\n" + "\n".join(f"- {ln}" for ln in kept)
                else:
                    del after[k]
        if not removed:
            return False
        _write_addenda(after, p)
        try:  # keep the provenance sidecar in step (best-effort)
            meta = load_line_meta(p)
            for k in touched:
                _reconcile_meta(meta, k, _bullets(after.get(k, "")))
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
                # Match the BARE model so a per-model retire also covers that
                # model's domain-scoped blocks (composite keys); ``m`` stays the
                # store key for meta/line-id ops (which are keyed by it).
                if model_id is not None and _split_key(m)[0] != model_id:
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
            # Audit with the BARE model id (+ scope), never the raw NUL key.
            mid, ctx = _split_key(m)
            record(EventKind.LEARNING_UPDATE, agent="self_harness", model_id=mid,
                   scope=(ctx or None), rung="prompt", line=ln, phase="retire")
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
    authentication share trigrams; share no whole token). Long texts are clipped
    to keep semantic mining's pairwise comparisons bounded."""
    s = " ".join((text or "").lower().split())[:_MAX_SEMANTIC_TEXT_CHARS]
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def _semantic_features(text: str) -> tuple[set[str], set[str]]:
    """Precomputed features for the built-in semantic similarity."""
    return _tokens((text or "")[:_MAX_SEMANTIC_TEXT_CHARS]), _char_ngrams(text)


def _semantic_similarity_from_features(
    a: tuple[set[str], set[str]], b: tuple[set[str], set[str]],
) -> float:
    """Built-in semantic similarity over precomputed token/trigram sets."""
    tok = _jaccard(a[0], b[0])
    ng = _jaccard(a[1], b[1])
    return max(tok, ng)


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
    return _semantic_similarity_from_features(_semantic_features(a), _semantic_features(b))


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
# ``tool`` is the component the failure occurred in -- the tool in play when the
# run failed (the last of ``tools_used``) -- so a weakness can be mined PER TOOL
# and its guidance recalled only when that tool is on hand (#7 component profiles).
_BUCKET_DIMS = ("domain", "tool")


def _bucket_value(r: dict, dim: str) -> str:
    """The scalar bucket value for one reflexion on one allowlisted ``dim``.

    Most dims are plain scalar fields (``domain``). ``tool`` is DERIVED: the trace
    carries a ``tools_used`` LIST, and the component a failure belongs to is the
    tool in play when it failed -- the LAST entry. Missing/empty -> "" so untagged
    traces cluster together rather than each forming a singleton bucket."""
    if dim == "tool":
        used = r.get("tools_used") or []
        return str(used[-1]) if isinstance(used, (list, tuple)) and used else ""
    v = r.get(dim)
    return "" if v is None else str(v)


def _bucket_key(r: dict, bucket_by: tuple[str, ...]) -> tuple[str, ...]:
    """The extra group key for one reflexion under ``bucket_by`` (a tuple of
    allowlisted reflexion dims). Missing/None -> "" so untagged traces still
    cluster together rather than each forming a singleton bucket."""
    return tuple(_bucket_value(r, k) for k in bucket_by)


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
        builtin_semantic = similarity_fn is semantic_similarity
        head_semantic: list[tuple[set[str], set[str]]] = []
        for r in recs:
            rtext = str(r.get("goal_text", ""))
            rsem = _semantic_features(rtext) if builtin_semantic else None
            rt = rsem[0] if rsem is not None else _tokens(rtext)
            for i, htok in enumerate(heads):
                if builtin_semantic and rsem is not None:
                    sim = _semantic_similarity_from_features(head_semantic[i], rsem)
                elif similarity_fn is not None:
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
                if builtin_semantic and rsem is not None:
                    head_semantic.append(rsem)
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
    context: str = ""            # mining scope carried from the signature (e.g.
                                 # "domain=finance"); routes the line to a
                                 # scoped store key so recall can target it.


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
        addendum_line=line, hypothesis=hypothesis, context=sig.context,
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
    metamorphic_fn: Callable[[list[str]], list[str]] | None = None,
    metamorphic_tolerance: float = 0.0,
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
    must not quietly make the model slower or pricier). ``metamorphic_fn`` (a
    meaning-preserving paraphraser; injected because a real one needs a model)
    rejects a line whose held-out improvement does NOT survive paraphrasing the
    unseen cases -- catching a line overfit to surface wording rather than the
    underlying weakness (``metamorphic_tolerance`` allows a small slip)."""
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
    # Metamorphic robustness: a line that helps only the EXACT held-out wording is
    # overfit to surface form, not the underlying weakness. If an injected
    # ``metamorphic_fn`` (a meaning-preserving paraphraser -- a real one needs a
    # model, so it is injected) can transform the unseen cases, the improvement
    # must SURVIVE the transform (held-out delta stays >= -tolerance on the
    # paraphrases). A line that flips to hurting paraphrased cases is rejected.
    if metamorphic_fn is not None and held_out:
        try:
            mcases = [str(c) for c in (metamorphic_fn(list(held_out)) or [])]
        except Exception:  # a bad transform can't fail a candidate -> skip the check
            mcases = []
        if mcases:
            m_with, *_ = _score_parts(score_with(add, mcases))
            m_without, *_ = _score_parts(score_without(add, mcases))
            if (isinstance(m_with, (int, float)) and isinstance(m_without, (int, float))
                    and math.isfinite(m_with) and math.isfinite(m_without)):
                m_delta = m_with - m_without
                if m_delta < -metamorphic_tolerance:
                    return ValidationResult(
                        False, in_delta, out_delta,
                        f"failed metamorphic check (overfit to wording; "
                        f"paraphrase delta {m_delta:.3g})", **base)
    return ValidationResult(True, in_delta, out_delta, "validated", **base)


def _validate_rotated(
    proposal: HarnessProposal, *, pool: list[str], rotations: int,
    score_with: ScoreFn, score_without: ScoreFn, validate_kwargs: dict,
) -> ValidationResult:
    """Cross-validate a candidate across ``rotations`` holdout folds and accept
    only if it GENERALIZES, not just if one lucky 30%% slice liked it.

    The combined ``pool`` of held-in + held-out goals is partitioned into K
    deterministic folds (:func:`corpus_kfold_splits`); the candidate is validated
    once per fold and the verdicts are aggregated with an ALL-folds rule: every
    fold must accept (so a line that regresses or fails the floors in ANY fold is
    rejected -- the strict, low-variance generalization test). The reported deltas
    are the per-fold means; ``samples`` is the distinct pool size, not the folds'
    samples summed -- every goal in ``pool`` appears in every fold (k-fold, not a
    disjoint split), so summing would inflate the evidence count by ~k and let
    rotation under-clear the downstream gate's ``min_samples`` floor. Falls back
    to a single
    :func:`validate_proposal` when the pool is too small to fold (< 2 goals) or
    ``rotations <= 1``."""
    from .self_harness_eval import corpus_kfold_splits
    splits = corpus_kfold_splits([{"goal": g} for g in pool], k=rotations)
    # Too small to fold (one rotation with empty held-out): defer to the single
    # split the caller would otherwise have used (held_in=pool, held_out=[]).
    if len(splits) < 2:
        hi, ho = (splits[0] if splits else (list(pool), []))
        return validate_proposal(proposal, held_in=hi, held_out=ho,
                                 score_with=score_with, score_without=score_without,
                                 **validate_kwargs)
    results = [validate_proposal(proposal, held_in=hi, held_out=ho,
                                 score_with=score_with, score_without=score_without,
                                 **validate_kwargs)
               for hi, ho in splits]
    k = len(results)
    n_pass = sum(1 for r in results if r.accepted)
    in_delta = sum(r.held_in_delta for r in results) / k
    out_delta = sum(r.held_out_delta for r in results) / k
    base = dict(
        baseline_score=sum(r.baseline_score for r in results) / k,
        candidate_score=sum(r.candidate_score for r in results) / k,
        samples=len(pool))
    if n_pass == k:
        return ValidationResult(True, in_delta, out_delta,
                                f"validated across {k} rotations", **base)
    # Surface the first failing fold's reason so an operator sees WHY it didn't
    # generalize, not just that it didn't.
    first_fail = next((r for r in results if not r.accepted), None)
    why = first_fail.reason if first_fail is not None else "rotation"
    return ValidationResult(False, in_delta, out_delta,
                            f"failed rotation ({n_pass}/{k} folds passed: {why})", **base)


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
    # A domain-scoped line lands under a composite store key; a model-wide line
    # under the bare model id (byte-identical to the pre-scoping store). The meta
    # line-id and reconciliation use the SAME key, so the sidecar stays consistent
    # with whichever block the line actually lives in.
    store_key = _scoped_key(proposal.model_id, proposal.context)
    from .file_lock import cross_process_lock
    with _lock, cross_process_lock(p):
        before = load_addenda(p)
        after = dict(before)
        new_block = _compose_addendum(
            store_key, before.get(store_key, ""), proposal.addendum_line)
        after[store_key] = new_block
        _write_addenda(after, p)
        try:
            meta = load_line_meta(p)
            _upsert_line_meta(meta, store_key, proposal.addendum_line,
                              provenance or {}, now=time.time())
            _reconcile_meta(meta, store_key, _bullets(new_block))
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
                path: Path | None = None, min_interval_s: float = 3600.0,
                domain: str | None = None,
                tools: list[str] | tuple[str, ...] | None = None) -> None:
    """Best-effort: record that ``model_id``'s guidance was just recalled, so
    staleness can be judged by USE, not only by promotion. In-process throttled
    (``min_interval_s``; 0 disables the throttle, for tests) so the hot recall
    path does at most one cheap write per model per interval. Tracks the
    model-wide block AND every scope ``recall_addendum`` injected for this run
    (the ``domain`` block and each ``tool`` block), so a recalled scoped line isn't
    wrongly retired. Never raises."""
    if not model_id:
        return
    mid = str(model_id)
    contexts = _scope_contexts(domain, tools)
    throttle_key = mid + "\x00" + "\x00".join(contexts)
    if min_interval_s:
        mono = time.monotonic()
        with _recall_note_lock:
            last = _recall_noted_monotonic.get(throttle_key)
            if last is not None and (mono - last) < min_interval_s:
                return  # throttled -- pure in-memory, no I/O
            _recall_noted_monotonic[throttle_key] = mono
    try:
        p = path if path is not None else _store_path()
        ts = now if now is not None else time.time()
        keys = [mid] + [_scoped_key(mid, ctx) for ctx in contexts]
        from .file_lock import cross_process_lock
        with _lock, cross_process_lock(p):
            meta = load_line_meta(p)
            store = load_addenda(p)
            changed = False
            for key in keys:
                for ln in _bullets(store.get(key, "")):
                    rec = meta.get(_line_id(key, ln))
                    if rec is not None:
                        rec["last_recalled_at"] = ts
                        rec["recall_notes"] = (rec.get("recall_notes") or 0) + 1
                        changed = True
            if changed:
                _write_line_meta(meta, p)
    except Exception:  # pragma: no cover -- usage tracking never perturbs a run
        log.debug("self_harness: note_recall failed", exc_info=True)


# ---- OUTCOME-CORRELATED EFFICACY ------------------------------------------
# Recall tracks *that* guidance was used; efficacy tracks whether using it
# correlated with SUCCESS. Two complementary signals: cheap recall->outcome
# counters in the sidecar (a coarse aggregate -- all of a model's lines co-occur
# in its prompt, so an outcome is attributed to all current lines), and a
# causal A/B re-review (`review_efficacy`) that re-measures a line's lift with a
# live scorer and demotes dead weight. Demotion is the REVERSIBLE direction
# (removing a line is always safe -- it's the rollback handle), so it goes through
# the audited `forget` path, not the promotion gate.

def note_outcome(model_id: str | None, success: bool, *, line: str | None = None,
                 domain: str | None = None,
                 tools: list[str] | tuple[str, ...] | None = None,
                 path: Path | None = None) -> None:
    """Record that a run using ``model_id``'s guidance succeeded or failed,
    incrementing per-line ``recall_success``/``recall_failure`` counters in the
    sidecar -- the signal :func:`review_efficacy`/:func:`review_canaries` act on.

    A specific ``line`` is targeted wherever it lives (any scope). Otherwise the
    outcome is attributed to the lines THIS run actually recalled: the model-wide
    block PLUS the ``domain`` block and each ``tool`` block -- mirroring
    :func:`recall_addendum`/:func:`note_recall`, so a finance run credits finance
    + model-wide guidance, not every other domain's. Best-effort; never raises."""
    if not model_id:
        return
    mid = str(model_id)
    field_name = "recall_success" if success else "recall_failure"
    try:
        p = path if path is not None else _store_path()
        from .file_lock import cross_process_lock
        with _lock, cross_process_lock(p):
            store = load_addenda(p)
            meta = load_line_meta(p)
            changed = False
            if line is not None:
                pairs = [(k, ln) for k, ln in _iter_model_lines(store, mid) if ln == line]
            else:
                keys = [mid] + [_scoped_key(mid, ctx)
                                for ctx in _scope_contexts(domain, tools)]
                pairs = [(k, ln) for k in keys for ln in _bullets(store.get(k, ""))]
            for k, ln in pairs:
                rec = meta.get(_line_id(k, ln))
                if rec is not None:
                    rec[field_name] = int(rec.get(field_name) or 0) + 1
                    changed = True
            if changed:
                _write_line_meta(meta, p)
    except Exception:  # pragma: no cover -- efficacy tracking never perturbs a run
        log.debug("self_harness: note_outcome failed", exc_info=True)


def line_efficacy(model_id: str, path: Path | None = None) -> list[dict]:
    """Per-line outcome counters for ``model_id`` across ALL its scopes: each
    ``{text, domain, success, failure, total, rate}`` where ``rate`` is
    success/total (``None`` until an outcome lands) and ``domain`` is the scope
    (``None`` for model-wide). Read-only. Includes domain-scoped lines, which the
    pre-fix version was blind to."""
    store = load_addenda(path)
    meta = load_line_meta(path)
    out = []
    for k, ln in _iter_model_lines(store, str(model_id)):
        rec = meta.get(_line_id(k, ln)) or {}
        s = int(rec.get("recall_success") or 0)
        f = int(rec.get("recall_failure") or 0)
        total = s + f
        _, ctx = _split_key(k)
        domain = ctx[len("domain="):] if ctx.startswith("domain=") else None
        out.append({"text": ln, "domain": domain, "success": s, "failure": f,
                    "total": total, "rate": (s / total) if total else None})
    return out


def review_efficacy(model_id: str, cases: list[str], *,
                    score_with: ScoreFn, score_without: ScoreFn,
                    min_lift: float = 0.0, min_samples: int = 1,
                    path: Path | None = None) -> list[str]:
    """Re-measure each current line's CAUSAL lift with a live A/B and DEMOTE the
    dead weight -- a line whose lift has fallen to ``<= min_lift`` over at least
    ``min_samples`` cases no longer earns its place in every prompt (the model,
    tools, or APIs moved on). Returns the demoted lines.

    Demotion uses the audited `forget` path (reversible), NOT the promotion gate.
    Scorers are injected (a real eval needs a real model); without ``cases`` it is
    a no-op. Never raises -- a maintenance review must not perturb anything."""
    demoted: list[str] = []
    if not cases:
        return demoted
    try:
        # Snapshot the (key, line) pairs first: forget_addendum mutates the store
        # mid-loop, so iterating a live view would skip lines. Spans every scope.
        for _k, ln in list(_iter_model_lines(load_addenda(path), str(model_id))):
            try:
                w, *_ = _score_parts(score_with(ln, cases))
                wo, *_ = _score_parts(score_without(ln, cases))
            except Exception:  # a bad scorer can't drop a line
                continue
            if not (isinstance(w, (int, float)) and isinstance(wo, (int, float))
                    and math.isfinite(w) and math.isfinite(wo)):
                continue
            if len(cases) >= min_samples and (w - wo) <= min_lift:
                if forget_addendum(model_id, line=ln, path=path):
                    demoted.append(ln)
    except Exception:  # pragma: no cover -- review never perturbs a run
        log.debug("self_harness: review_efficacy failed", exc_info=True)
    return demoted


# ---- CANARY / STAGED ROLLOUT ----------------------------------------------
# A freshly-promoted line can ride as a CANARY (on probation): still recalled,
# but watched. `review_canaries` reads the recall->outcome counters and advances
# the lifecycle -- GRADUATE a line that proves out (clear the flag -> permanent),
# DEMOTE one that correlates with failures (audited forget), leave the rest on
# probation. A slow-rollout valve on top of the gate; failures win ties (safe).

def mark_canary(model_id: str, line: str, *, canary: bool = True,
                path: Path | None = None) -> bool:
    """Mark/unmark a learned line as a canary. Returns True if the flag changed
    (False if the line has no sidecar record or already had this state). Atomic;
    never raises."""
    if not model_id or line is None:
        return False
    try:
        p = path if path is not None else _store_path()
        from .file_lock import cross_process_lock
        with _lock, cross_process_lock(p):
            store = load_addenda(p)
            meta = load_line_meta(p)
            changed = False
            for k, ln in _iter_model_lines(store, str(model_id)):
                if ln != line:
                    continue
                rec = meta.get(_line_id(k, ln))
                if rec is not None and bool(rec.get("canary")) != bool(canary):
                    rec["canary"] = bool(canary)
                    changed = True
            if changed:
                _write_line_meta(meta, p)
            return changed
    except Exception:  # pragma: no cover -- canary state never perturbs a run
        log.debug("self_harness: mark_canary failed", exc_info=True)
        return False


def list_canaries(model_id: str, path: Path | None = None) -> list[str]:
    """The model's lines currently on canary probation, across ALL scopes
    (model-wide + domain). Includes domain-scoped canaries, which the pre-fix
    version was blind to."""
    store = load_addenda(path)
    meta = load_line_meta(path)
    return [ln for k, ln in _iter_model_lines(store, str(model_id))
            if (meta.get(_line_id(k, ln)) or {}).get("canary")]


def review_canaries(model_id: str, *, graduate_after: int = 3,
                    demote_after: int = 2, path: Path | None = None) -> dict:
    """Advance the canary lifecycle from the recall->outcome counters:
    DEMOTE (audited forget) a canary with ``>= demote_after`` failures; else
    GRADUATE (clear the flag -> permanent) one with ``>= graduate_after``
    successes; else leave it on probation. Failures take precedence (a flaky line
    is pulled even if it also has wins). Spans every scope, so domain-scoped
    canaries are reviewed too. Returns ``{"graduated": [...], "demoted": [...]}``.
    Never raises."""
    graduated: list[str] = []
    demoted: list[str] = []
    try:
        store = load_addenda(path)
        meta = load_line_meta(path)
        # Snapshot pairs first -- forget/mark mutate the store/meta mid-loop.
        for k, ln in list(_iter_model_lines(store, str(model_id))):
            rec = meta.get(_line_id(k, ln)) or {}
            if not rec.get("canary"):
                continue
            s = int(rec.get("recall_success") or 0)
            f = int(rec.get("recall_failure") or 0)
            if f >= demote_after:
                if forget_addendum(model_id, line=ln, path=path):
                    demoted.append(ln)
            elif s >= graduate_after:
                if mark_canary(model_id, ln, canary=False, path=path):
                    graduated.append(ln)
    except Exception:  # pragma: no cover -- review never perturbs a run
        log.debug("self_harness: review_canaries failed", exc_info=True)
    return {"graduated": graduated, "demoted": demoted}


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
    # Governance readiness at pass time -- so an operator can tell WHY a pass
    # promoted nothing: the verifier-drift freeze is armed (no learning while the
    # judge is unreliable) and/or the promotion gate is disabled. Both are
    # best-effort reads; neither changes the pass's behavior (the gate still
    # enforces them), they only EXPLAIN it.
    frozen: bool = False
    gate_enabled: bool = True
    # Outcome-driven lifecycle results, populated by the driver cycle from the
    # accumulated recall->outcome counters: canaries that graduated to permanent
    # and lines demoted (canary pulled, or efficacy dead-weight).
    graduated: list[str] = field(default_factory=list)
    demoted: list[str] = field(default_factory=list)


def _governance_readiness() -> tuple[bool, bool]:
    """(frozen, gate_enabled) -- whether verifier-drift has frozen learning and
    whether the promotion controller is enabled. Best-effort: any error reads as
    'not frozen, enabled' so readiness reporting never blocks or misleads a pass
    that would otherwise run."""
    frozen = False
    gate_enabled = True
    try:
        from . import calibration
        frozen = bool(calibration.learning_frozen())
    except Exception:  # pragma: no cover -- readiness read never blocks a pass
        pass
    try:
        from . import self_improvement as si
        gate_enabled = bool(si.enabled())
    except Exception:  # pragma: no cover
        pass
    return frozen, gate_enabled


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
    metamorphic_fn: Callable[[list[str]], list[str]] | None = None,
    metamorphic_tolerance: float = 0.0,
    holdout_rotations: int = 1,
    canary: bool = False,
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
    # Record governance readiness so a pass that promotes nothing can be EXPLAINED
    # (verifier-drift freeze armed / promotion gate off), not just observed.
    report.frozen, report.gate_enabled = _governance_readiness()
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
                max_tool_calls_factor=max_tool_calls_factor,
                metamorphic_fn=metamorphic_fn,
                metamorphic_tolerance=metamorphic_tolerance)
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
            elif holdout_rotations and holdout_rotations > 1 and ho:
                # Holdout rotation: cross-validate across K folds of the combined
                # held-in+held-out pool and accept only if the lift generalizes
                # across ALL folds, not just one fixed slice.
                vr = _validate_rotated(
                    proposal, pool=list(hi) + list(ho), rotations=holdout_rotations,
                    score_with=score_with, score_without=score_without,
                    validate_kwargs=validate_kwargs)
                if not vr.accepted:
                    report.skipped.append(f"rejected ({vr.reason}): {proposal.addendum_line}")
                    continue
            else:
                vr = validate_proposal(
                    proposal, held_in=hi, held_out=ho, score_with=score_with,
                    score_without=score_without, **validate_kwargs)
                if not vr.accepted:
                    report.skipped.append(f"rejected ({vr.reason}): {proposal.addendum_line}")
                    continue
            report.validated += 1

            ok, why = _gate_and_apply(proposal, vr, controller=controller,
                                      path=path, canary=canary)
            if not ok:
                # Surface WHY the gate refused (e.g. "too few samples (3 < 5)",
                # frozen verifier, disabled controller) so an operator with a
                # small validation set or a drifting judge isn't left guessing.
                report.skipped.append(f"gate refused ({why}): {proposal.addendum_line}")
                continue
            report.promoted += 1
            report.applied_lines.append(proposal.addendum_line)
            # Advisory conflict check within the block the line actually landed in
            # (the scoped key for a domain line); flag any EXISTING line it appears
            # to contradict (operator review, not a block).
            block_key = _scoped_key(str(model_id), proposal.context)
            existing = [ln for ln in _bullets(load_addenda(path).get(block_key, ""))
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
                    controller=None, path: Path | None = None,
                    canary: bool = False) -> tuple[bool, str]:
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
    apply_prov = {
        "signature": proposal.signature, "rationale": proposal.rationale,
        "hypothesis": proposal.hypothesis or None,
        "held_out_delta": round(vr.held_out_delta, 4), "samples": vr.samples}
    if canary:  # only stamp the flag when staging -- non-canary leaves meta clean
        apply_prov["canary"] = True
    _apply_addendum(proposal, path=path, provenance=apply_prov)
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
    "note_outcome", "line_efficacy", "review_efficacy",
    "mark_canary", "list_canaries", "review_canaries",
    "find_conflicts", "detect_store_conflicts",
    "FailureSignature", "mine_failures", "count_eligible", "semantic_similarity",
    "HarnessProposal", "ProposeFn", "propose_addendum", "llm_proposer",
    "ValidationResult", "ScoreFn", "validate_proposal",
    "SelfHarnessReport", "run_self_harness",
]
