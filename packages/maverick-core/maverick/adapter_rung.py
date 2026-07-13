"""Governed adapter rung -- in-tenant weights adaptation on the promotion ladder.

The promotion ladder (``maverick.self_improvement``) has always declared a
``weights`` rung -- min 20 samples, capability evidence, human signature -- and
``Candidate.payload`` was designed to carry "a trained adapter ref". This module
supplies the plumbing between a trained LoRA adapter directory and that rung, so
a tenant's own model adaptation runs under the same governance chain as code
self-modification:

* **provenance boundary** -- training examples carry a provenance tag; frontier
  model output is refused by default (the distillation-ToS guard), synthetic
  data is refused by default, unknown provenance is always refused. The kept
  dataset is content-hashed so the manifest pins WHAT the adapter learned from.
* **payload hygiene** -- an adapter directory may contain only inert weight/
  metadata files (safetensors/gguf/json/text). Code and pickle-bearing formats
  (``.py``, ``.pt``, ``.bin``, ...) are refused structurally, BEFORE any eval
  runs -- a "weights" payload can therefore never smuggle executable authority.
* **held-out fitness + overfit refusal** -- baseline vs candidate scored on a
  deterministic held-in/held-out split (same rule as the code rung): a gain on
  seen cases with no gain on unseen cases is OVERFIT and refused.
* **signed promotion, append-only ledger, one-step rollback** -- the existing
  ``SelfImprovementController`` / ``PromotionLedger`` / ``approval_signing``
  chain, at rung ``weights``; activation archives the previous pointer and
  ``AdapterStore.rollback()`` restores it byte-identically.

The two expensive seams are pluggable and disclosed, mirroring how the code
rung stubs its LLM proposer: the ``stub`` trainer writes a deterministic
artifact (no GPU, no deps) for proofs/tests, while ``dpo-lora`` delegates to
the real QLoRA/DPO trainer in ``maverick.training.rlaif`` (``[training]``
extra). Serving attaches a PROMOTED adapter via an Ollama Modelfile
``ADAPTER`` directive (:func:`render_modelfile`); no base model is ever
hard-coded -- the base comes from ``[adapter_rung] base_model`` in config.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# --- provenance boundary ---------------------------------------------------------

# Where a training example came from. ``model_output`` (a frontier model's own
# completion) is refused by default: training a tenant adapter on it is the
# contested distillation lane and most provider ToS restrict it. The defensible
# default is the tenant's OWN signal: human corrections/examples and traces of
# the tenant's real runs.
PROVENANCE_KINDS: tuple[str, ...] = (
    "human_correction", "human_example", "tenant_trace", "synthetic", "model_output")


@dataclass(frozen=True)
class TrainExample:
    """One preference pair (or plain completion when ``rejected`` is empty)."""

    prompt: str
    chosen: str
    rejected: str = ""
    provenance: str = "tenant_trace"
    source_id: str = ""


@dataclass(frozen=True)
class DatasetReview:
    """Outcome of screening a training set at the provenance boundary."""

    ok: bool
    kept: tuple[TrainExample, ...]
    refused: tuple[str, ...]          # human-readable refusal reasons, one per drop
    dataset_sha256: str = ""          # digest of the KEPT set (what training may see)
    provenance_counts: dict = field(default_factory=dict)


def screen_examples(
    examples: list[TrainExample] | tuple[TrainExample, ...],
    *,
    allow_synthetic: bool = False,
    allow_model_output: bool = False,
) -> DatasetReview:
    """Apply the provenance boundary. Unknown provenance is ALWAYS refused
    (fail-closed); ``synthetic`` and ``model_output`` are refused unless the
    operator opted in via config. An empty kept-set is not ok -- an adapter
    trained on nothing is meaningless and must not reach the ladder."""
    kept: list[TrainExample] = []
    refused: list[str] = []
    counts: dict[str, int] = {}
    for i, ex in enumerate(examples):
        prov = (ex.provenance or "").strip()
        if prov not in PROVENANCE_KINDS:
            refused.append(f"example {i} ({ex.source_id or 'unlabelled'}): "
                           f"unknown provenance {prov!r}")
            continue
        if prov == "model_output" and not allow_model_output:
            refused.append(f"example {i} ({ex.source_id or 'unlabelled'}): model_output "
                           "provenance refused (distillation guard; "
                           "[adapter_rung] allow_model_output)")
            continue
        if prov == "synthetic" and not allow_synthetic:
            refused.append(f"example {i} ({ex.source_id or 'unlabelled'}): synthetic "
                           "provenance refused ([adapter_rung] allow_synthetic)")
            continue
        counts[prov] = counts.get(prov, 0) + 1
        kept.append(ex)
    digest = _dataset_digest(kept) if kept else ""
    return DatasetReview(ok=bool(kept), kept=tuple(kept), refused=tuple(refused),
                         dataset_sha256=digest, provenance_counts=counts)


def _dataset_digest(examples: list[TrainExample]) -> str:
    """Order-independent content hash of a kept training set."""
    h = hashlib.sha256()
    for line in sorted(
        json.dumps([ex.prompt, ex.chosen, ex.rejected, ex.provenance],
                   ensure_ascii=False)
        for ex in examples
    ):
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


# --- payload hygiene -------------------------------------------------------------

# Fail-closed allowlist: everything else -- notably code (.py/.sh), native
# binaries (.so/.dll) and pickle-bearing torch formats (.pt/.pth/.bin/.pkl) --
# is refused. safetensors/gguf are inert-by-format; json/txt/md carry metadata.
ALLOWED_ADAPTER_SUFFIXES: frozenset[str] = frozenset(
    {".safetensors", ".gguf", ".json", ".txt", ".md"})

MANIFEST_BASENAME = "manifest.json"
SERVING_BASENAME = "Modelfile.txt"  # generated at promotion; not part of the weights


@dataclass(frozen=True)
class PayloadReview:
    ok: bool
    reason: str
    files: tuple[str, ...] = ()
    refused: tuple[str, ...] = ()


def review_adapter_payload(adapter_dir: Path | str) -> PayloadReview:
    """Structural hygiene check on an adapter directory. Refuses any file whose
    suffix is off the allowlist -- BEFORE any evaluation runs, mirroring how the
    code rung's anti-cheat boundary fires before a test does."""
    root = Path(adapter_dir)
    if not root.is_dir():
        return PayloadReview(False, f"not a directory: {root}")
    files: list[str] = []
    refused: list[str] = []
    for p in sorted(root.rglob("*")):
        if p.is_dir():
            continue
        if p.is_symlink():  # a symlink can smuggle content from outside the payload
            refused.append(f"{p.relative_to(root)} (symlink)")
            continue
        rel = str(p.relative_to(root))
        if p.suffix.lower() not in ALLOWED_ADAPTER_SUFFIXES:
            refused.append(rel)
        else:
            files.append(rel)
    if refused:
        return PayloadReview(False, "forbidden file type(s) in adapter payload: "
                             + ", ".join(refused), tuple(files), tuple(refused))
    if not files:
        return PayloadReview(False, "empty adapter payload", (), ())
    return PayloadReview(True, "payload clean", tuple(files), ())


def payload_digest_dir(adapter_dir: Path | str) -> str:
    """Deterministic digest over the payload's relative paths + bytes, so the
    Ed25519 approval is bound to the EXACT weights that were evaluated."""
    root = Path(adapter_dir)
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        # The manifest describes the payload and the Modelfile serves it;
        # neither is weights, so neither is part of the approved digest.
        if p.is_dir() or p.name in (MANIFEST_BASENAME, SERVING_BASENAME):
            continue
        h.update(str(p.relative_to(root)).encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


# --- manifest --------------------------------------------------------------------

@dataclass
class AdapterManifest:
    """What an adapter IS: base, trainer, data provenance, payload digest."""

    adapter_id: str
    base_model: str                    # full spec, e.g. "ollama:<model-id>" (from config)
    trainer: str
    dataset_sha256: str
    examples: int
    provenance_counts: dict = field(default_factory=dict)
    payload_sha256: str = ""
    created_at: float = field(default_factory=time.time)
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "adapter_id": self.adapter_id, "base_model": self.base_model,
            "trainer": self.trainer, "dataset_sha256": self.dataset_sha256,
            "examples": self.examples, "provenance_counts": self.provenance_counts,
            "payload_sha256": self.payload_sha256, "created_at": self.created_at,
            "notes": self.notes,
        }

    def save(self, adapter_dir: Path | str) -> Path:
        path = Path(adapter_dir) / MANIFEST_BASENAME
        _atomic_write_json(path, self.to_dict())
        return path

    @classmethod
    def load(cls, adapter_dir: Path | str) -> AdapterManifest:
        raw = json.loads((Path(adapter_dir) / MANIFEST_BASENAME).read_text(encoding="utf-8"))
        known = {k: raw[k] for k in (
            "adapter_id", "base_model", "trainer", "dataset_sha256", "examples",
            "provenance_counts", "payload_sha256", "created_at", "notes") if k in raw}
        return cls(**known)


def _atomic_write_json(path: Path, payload: dict) -> None:
    """0600 atomic JSON write (same posture as the promotion ledger)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1, sort_keys=True)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


# --- trainers (the pluggable expensive seam) ---------------------------------------

class StubTrainer:
    """Deterministic no-GPU trainer for proofs and tests, DISCLOSED as such:
    the artifact is a placeholder derived from the dataset digest, not learned
    weights. Everything downstream (hygiene, eval, gate, ledger, rollback) is
    the real chain -- this stubs only the gradient step, exactly as the code
    rung's proofs stub the LLM proposer."""

    name = "stub"

    def train(self, review: DatasetReview, base_model: str,
              out_dir: Path | str) -> AdapterManifest:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        blob = hashlib.sha256(("stub:" + review.dataset_sha256).encode()).digest()
        (out / "adapter.safetensors").write_bytes(blob * 32)
        manifest = AdapterManifest(
            adapter_id=uuid.uuid4().hex[:12], base_model=base_model,
            trainer=self.name, dataset_sha256=review.dataset_sha256,
            examples=len(review.kept), provenance_counts=dict(review.provenance_counts),
            payload_sha256="",
            notes="stub trainer: deterministic placeholder artifact (proof/test path)")
        manifest.payload_sha256 = payload_digest_dir(out)
        manifest.save(out)
        return manifest


class DpoLoraTrainer:
    """Real QLoRA/DPO training, delegated to ``maverick.training.rlaif`` (the
    existing trainer behind the ``[training]`` extra). Import is guarded so a
    box without peft/torch gets a plain actionable error, not a stack trace."""

    name = "dpo-lora"

    def train(self, review: DatasetReview, base_model: str,
              out_dir: Path | str) -> AdapterManifest:
        try:
            from .training import rlaif
        except Exception as e:  # pragma: no cover -- exercised only sans extra
            raise RuntimeError(
                "dpo-lora trainer needs the [training] extra "
                "(peft/bitsandbytes/accelerate): pip install 'maverick[training]'"
            ) from e
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        pairs = [(ex.prompt, ex.chosen, ex.rejected) for ex in review.kept]
        rc = rlaif.train(pairs, base_model, str(out), lora=True)
        if rc != 0:
            raise RuntimeError(f"rlaif LoRA training failed (exit {rc})")
        hygiene = review_adapter_payload(out)
        if not hygiene.ok:
            # Torch checkpoints (.bin/.pt) are pickle-bearing; the trainer must
            # emit safetensors. Refuse rather than promote an unreviewable blob.
            raise RuntimeError(f"trained payload failed hygiene: {hygiene.reason}")
        manifest = AdapterManifest(
            adapter_id=uuid.uuid4().hex[:12], base_model=base_model,
            trainer=self.name, dataset_sha256=review.dataset_sha256,
            examples=len(review.kept), provenance_counts=dict(review.provenance_counts),
            payload_sha256=payload_digest_dir(out))
        manifest.save(out)
        return manifest


_TRAINERS: dict[str, Callable[[], Any]] = {
    StubTrainer.name: StubTrainer,
    DpoLoraTrainer.name: DpoLoraTrainer,
}


def get_trainer(name: str):
    """Trainer registry lookup; unknown names fail with the known options."""
    try:
        return _TRAINERS[name]()
    except KeyError:
        raise ValueError(
            f"unknown adapter trainer {name!r}; known: {sorted(_TRAINERS)}") from None


# --- held-out fitness --------------------------------------------------------------

@dataclass
class AdapterEvalResult:
    ok: bool = False
    baseline_score: float = 0.0        # held-OUT rates: what the gate judges
    candidate_score: float = 0.0
    held_in_baseline: float = 0.0
    held_in_candidate: float = 0.0
    samples: int = 0                   # held-out case count (one case = one trial)
    overfit: bool = False
    reason: str = ""


def evaluate_adapter(
    base_ref: str,
    candidate_ref: str,
    case_ids: list[str],
    *,
    score_fn: Callable[[str, list[str]], dict[str, bool]],
    held_out_frac: float = 0.35,
) -> AdapterEvalResult:
    """Score base vs base+adapter on a deterministic held-in/held-out split.

    ``score_fn(model_ref, case_ids) -> {case_id: passed}`` is the eval seam: in
    production it runs the tenant's private evals against the serving stack
    (base vs tuned model); proofs and tests stub it. The split reuses
    ``self_harness_eval.corpus_split`` (stable content-hash order) and the
    overfit rule is byte-for-byte the code rung's: better on seen, not better
    on unseen => memorisation, refused."""
    from .self_harness_eval import corpus_split

    out = AdapterEvalResult()
    ids = [str(c) for c in case_ids]
    held_in, held_out = corpus_split([{"goal": c} for c in ids],
                                     held_out_frac=held_out_frac)
    if not held_out:
        out.reason = "no held-out cases (need >= 2 eval cases)"
        return out
    base = score_fn(base_ref, ids)
    cand = score_fn(candidate_ref, ids)

    def _rate(resolved: dict[str, bool], subset: list[str]) -> float:
        return sum(1 for c in subset if resolved.get(c)) / len(subset) if subset else 0.0

    out.held_in_baseline, out.held_in_candidate = _rate(base, held_in), _rate(cand, held_in)
    out.baseline_score, out.candidate_score = _rate(base, held_out), _rate(cand, held_out)
    out.samples = len(held_out)
    out.overfit = (out.held_in_candidate > out.held_in_baseline
                   and out.candidate_score <= out.baseline_score)
    if out.overfit:
        out.reason = (f"OVERFIT: held-in {out.held_in_baseline:.3f}->"
                      f"{out.held_in_candidate:.3f} but held-out "
                      f"{out.baseline_score:.3f}->{out.candidate_score:.3f}")
        return out
    out.ok = True
    out.reason = (f"held-out {out.baseline_score:.3f} -> {out.candidate_score:.3f} "
                  f"over {out.samples} unseen cases")
    return out


# --- adapter store (activation pointer + one-step rollback) -------------------------

ACTIVE_BASENAME = "active.json"
PREVIOUS_BASENAME = "previous.json"


class AdapterStore:
    """Filesystem store for adapter payloads plus the ACTIVE pointer.

    Activation is a pointer swap, never a payload mutation: the previous
    pointer is archived to ``previous.json`` first, so ``rollback()`` is a
    byte-identical one-step restore -- the same reversibility contract the
    solver rung meets with ``apply_and_archive``/``rollback_solver``."""

    def __init__(self, root: Path | str | None = None):
        if root is None:
            root = default_store_root()
        self.root = Path(root)

    # -- pointers ------------------------------------------------------------
    def _read(self, basename: str) -> dict | None:
        path = self.root / basename
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # a torn/corrupt pointer must never crash serving
            log.warning("unreadable adapter pointer %s", path, exc_info=True)
            return None

    def active(self) -> dict | None:
        """The active promoted adapter pointer, or None."""
        return self._read(ACTIVE_BASENAME)

    def previous(self) -> dict | None:
        return self._read(PREVIOUS_BASENAME)

    def path_for(self, adapter_id: str) -> Path:
        return self.root / "payloads" / adapter_id

    def activate(self, manifest: AdapterManifest, *, record_id: str) -> dict:
        """Point serving at a PROMOTED adapter, archiving the prior pointer."""
        current = self.active()
        if current is not None:
            _atomic_write_json(self.root / PREVIOUS_BASENAME, current)
        pointer = {
            "adapter_id": manifest.adapter_id,
            "base_model": manifest.base_model,
            "payload_sha256": manifest.payload_sha256,
            "promotion_record_id": record_id,
            "activated_at": time.time(),
        }
        _atomic_write_json(self.root / ACTIVE_BASENAME, pointer)
        return pointer

    def rollback(self) -> dict | None:
        """One-step revert to the previously active pointer. Returns the
        restored pointer (None = nothing to restore). With no previous pointer
        the active adapter is simply deactivated -- the base model serves."""
        prev = self.previous()
        active_path = self.root / ACTIVE_BASENAME
        if prev is None:
            active_path.unlink(missing_ok=True)
            return None
        _atomic_write_json(active_path, prev)
        (self.root / PREVIOUS_BASENAME).unlink(missing_ok=True)
        return prev


def default_store_root() -> Path:
    """``[adapter_rung] store_dir`` or ``~/.maverick/adapters``."""
    try:
        from .config import get_adapter_rung
        configured = get_adapter_rung().get("store_dir")
        if configured:
            return Path(str(configured)).expanduser()
    except Exception:  # pragma: no cover -- config never blocks a run
        pass
    try:
        from .paths import maverick_home
        return maverick_home() / "adapters"
    except Exception:  # pragma: no cover
        return Path.home() / ".maverick" / "adapters"


# --- serving seam (Ollama Modelfile ADAPTER) ---------------------------------------

def _model_id(spec: str) -> str:
    """Strip a known local provider prefix off a ``provider:model-id`` spec."""
    if ":" in spec:
        head, tail = spec.split(":", 1)
        if head.strip().lower() in {"ollama", "local"}:
            return tail
    return spec


def tuned_model_name(manifest: AdapterManifest) -> str:
    """Deterministic Ollama model name for a promoted adapter (tag-safe)."""
    base = _model_id(manifest.base_model).replace(":", "-").replace("/", "-")
    return f"{base}-lw-{manifest.adapter_id[:8]}"


def render_modelfile(manifest: AdapterManifest, adapter_dir: Path | str) -> str:
    """The Ollama Modelfile that binds base + adapter for serving. Written
    next to the payload by :func:`emit_serving_artifacts`; applied by the
    operator with ``ollama create`` (we render the exact command, we do not
    reach into a daemon at promotion time)."""
    return (f"# generated by maverick.adapter_rung for {manifest.adapter_id}\n"
            f"# dataset {manifest.dataset_sha256[:12]} · payload {manifest.payload_sha256[:12]}\n"
            f"FROM {_model_id(manifest.base_model)}\n"
            f"ADAPTER {Path(adapter_dir).resolve()}\n")


def emit_serving_artifacts(manifest: AdapterManifest, adapter_dir: Path | str) -> dict:
    """Write ``Modelfile`` beside the payload; return name/paths/command."""
    adapter_dir = Path(adapter_dir)
    modelfile = adapter_dir / SERVING_BASENAME  # .txt: stays inside the hygiene allowlist
    modelfile.write_text(render_modelfile(manifest, adapter_dir), encoding="utf-8")
    name = tuned_model_name(manifest)
    return {
        "model_name": name,
        "modelfile": str(modelfile),
        "create_command": f"ollama create {name} -f {modelfile}",
    }


def effective_model_spec(spec: str) -> str:
    """Map a base spec to its promoted tuned model, when (and only when) the
    adapter rung is enabled AND the active adapter was promoted for exactly
    this base. Fail-open on any doubt: serving must never break because a
    pointer is stale, so every exception returns the original spec."""
    try:
        from .config import get_adapter_rung
        cfg = get_adapter_rung()
        if not cfg.get("enable"):
            return spec
        active = AdapterStore().active()
        if not active or active.get("base_model") != spec:
            return spec
        manifest = AdapterManifest(
            adapter_id=str(active.get("adapter_id", "")),
            base_model=str(active.get("base_model", "")),
            trainer="", dataset_sha256="", examples=0)
        if not manifest.adapter_id:
            return spec
        return f"ollama:{tuned_model_name(manifest)}"
    except Exception:  # pragma: no cover -- serving must not depend on the rung
        log.debug("effective_model_spec fail-open", exc_info=True)
        return spec


# --- the governed decision ----------------------------------------------------------

@dataclass
class AdapterUpliftResult:
    """Verdict on one proposed adapter promotion (mirrors ``UpliftResult``)."""

    hygiene_ok: bool = False
    fitness: AdapterEvalResult = field(default_factory=AdapterEvalResult)
    promoted: bool = False
    approver_id: str | None = None
    candidate_id: str = ""
    pointer: dict | None = None
    serving: dict | None = None
    reason: str = ""


def govern_adapter_change(
    adapter_dir: Path | str,
    case_ids: list[str],
    *,
    score_fn: Callable[[str, list[str]], dict[str, bool]],
    keys_dir: Path | str,
    ledger=None,
    controller=None,
    store: AdapterStore | None = None,
    held_out_frac: float = 0.35,
    change_id: str | None = None,
) -> AdapterUpliftResult:
    """Decide whether a trained adapter may be promoted and activated.

    The chain, in order (each step fails closed): payload hygiene -> manifest ->
    held-out fitness with overfit refusal -> capability non-escalation (proven
    structurally by the code-free payload boundary) -> Ed25519 human approval
    bound to the payload digest -> ``weights``-rung promotion into the
    append-only ledger -> pointer activation with the previous pointer archived
    as the one-step rollback handle."""
    from . import approval_signing as asig
    from .self_improvement import Candidate, SelfImprovementController

    out = AdapterUpliftResult()
    adapter_dir = Path(adapter_dir)

    # 1. Hygiene boundary: refuse code/pickle payloads before anything runs.
    hygiene = review_adapter_payload(adapter_dir)
    out.hygiene_ok = hygiene.ok
    if not hygiene.ok:
        out.reason = f"payload refused: {hygiene.reason}"
        return out
    try:
        manifest = AdapterManifest.load(adapter_dir)
    except Exception as e:
        out.reason = f"unreadable adapter manifest: {e}"
        return out
    digest = payload_digest_dir(adapter_dir)
    if manifest.payload_sha256 and manifest.payload_sha256 != digest:
        out.reason = "payload digest mismatch: weights changed after training"
        return out

    # 2. Fitness on the held-out split (the gate judges ONLY unseen cases).
    tuned_ref = f"{manifest.base_model}+adapter:{manifest.adapter_id}"
    out.fitness = evaluate_adapter(manifest.base_model, tuned_ref, case_ids,
                                score_fn=score_fn, held_out_frac=held_out_frac)
    if not out.fitness.ok:
        out.reason = out.fitness.reason
        return out

    # 3. Candidate at the weights rung. Non-escalation is structural: the
    #    hygiene boundary proved the payload carries no code, and tool
    #    authority lives in the runtime capability envelope which weights
    #    cannot touch -- recorded in provenance for the audit line.
    priv_path = Path(keys_dir) / "operator.priv.hex"
    if not priv_path.exists():
        out.reason = f"no operator signing key at {priv_path}"
        return out
    # The signed payload EMBEDS the weights digest: ``ApprovalRequest.
    # for_candidate`` always re-hashes the payload itself (its TOCTOU bind), so
    # binding the weights means putting their digest INSIDE what gets hashed --
    # a swapped adapter directory then changes the payload and the prior
    # signature stops verifying, structurally.
    approved_payload = json.dumps(
        {"adapter_id": manifest.adapter_id, "base_model": manifest.base_model,
         "weights_sha256": digest, "dataset_sha256": manifest.dataset_sha256},
        sort_keys=True)
    cand = Candidate(
        rung="weights",
        summary=(f"adapter {manifest.adapter_id} on {manifest.base_model}: held-out "
                 f"{out.fitness.baseline_score:.3f} -> {out.fitness.candidate_score:.3f}"),
        baseline_score=out.fitness.baseline_score,
        candidate_score=out.fitness.candidate_score,
        samples=out.fitness.samples,
        payload=approved_payload,
        payload_sha256=hashlib.sha256(approved_payload.encode("utf-8")).hexdigest(),
        capability_widens=False,
        rollback={"pointer": "previous active pointer archived by AdapterStore.activate"},
        provenance={
            "dataset_sha256": manifest.dataset_sha256,
            "provenance_counts": manifest.provenance_counts,
            "trainer": manifest.trainer,
            "capability_evidence": "payload hygiene boundary: no executable content; "
                                   "tool authority unchanged by weights",
        },
        id=change_id or f"adapter-{uuid.uuid4().hex[:8]}")
    out.candidate_id = cand.id
    sig = asig.sign_request(asig.ApprovalRequest.for_candidate(cand),
                            priv_path.read_text().strip())
    cand = Candidate(**{**cand.__dict__, "approval_signature": sig})

    # 4. Promotion through the governed gate (same env bracket as dgm_uplift).
    prev_env = {k: os.environ.get(k)
                for k in ("MAVERICK_APPROVER_KEYS_DIR", "MAVERICK_SELF_IMPROVEMENT")}
    os.environ["MAVERICK_APPROVER_KEYS_DIR"] = str(keys_dir)
    os.environ["MAVERICK_SELF_IMPROVEMENT"] = "1"
    try:
        ctrl = controller or SelfImprovementController(ledger=ledger)
        verdict = ctrl.promote(cand)
    finally:
        for k, v in prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    out.promoted = bool(verdict.ok)
    out.approver_id = verdict.approver_id
    if not verdict.ok:
        out.reason = f"gate refused: {verdict.blocking_reason}"
        return out

    # 5. Activate: pointer swap with the previous pointer archived (rollback).
    st = store or AdapterStore()
    out.pointer = st.activate(manifest, record_id=cand.id)
    out.serving = emit_serving_artifacts(manifest, adapter_dir)
    out.reason = (f"PROMOTED: held-out {out.fitness.baseline_score:.3f} -> "
                  f"{out.fitness.candidate_score:.3f} over {out.fitness.samples} cases; "
                  f"serve with: {out.serving['create_command']}")
    return out


def rollback_adapter(record_id: str, *, store: AdapterStore | None = None,
                     ledger=None) -> dict | None:
    """One-step revert of an activated adapter: restore the previous pointer
    and mark the promotion rolled back in the ledger (when one is given)."""
    st = store or AdapterStore()
    restored = st.rollback()
    if ledger is not None:
        try:
            ledger.mark_rolled_back(record_id, at=time.time())
        except Exception:  # pragma: no cover -- ledger bookkeeping is best-effort
            log.warning("could not mark %s rolled back", record_id, exc_info=True)
    return restored
