"""Tests for maverick.adapter_rung -- the governed weights (adapter) rung."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from maverick import adapter_rung as ar
from maverick.self_harness_eval import corpus_split


def _examples() -> list[ar.TrainExample]:
    return [
        ar.TrainExample("p1", "good", "bad", provenance="human_correction"),
        ar.TrainExample("p2", "good", "bad", provenance="tenant_trace"),
        ar.TrainExample("p3", "good", "bad", provenance="human_example"),
    ]


# --- provenance boundary ---------------------------------------------------------

def test_screen_refuses_model_output_by_default():
    review = ar.screen_examples(_examples() + [
        ar.TrainExample("p", "c", provenance="model_output")])
    assert review.ok
    assert len(review.kept) == 3
    assert any("model_output" in r for r in review.refused)


def test_screen_model_output_needs_explicit_opt_in():
    review = ar.screen_examples(
        [ar.TrainExample("p", "c", provenance="model_output")],
        allow_model_output=True)
    assert review.ok and len(review.kept) == 1
    assert review.provenance_counts == {"model_output": 1}


def test_screen_refuses_synthetic_by_default_and_unknown_always():
    review = ar.screen_examples([
        ar.TrainExample("p", "c", provenance="synthetic"),
        ar.TrainExample("p", "c", provenance="scraped"),
    ], allow_synthetic=False)
    assert not review.ok and not review.kept and len(review.refused) == 2
    # Unknown provenance is refused even with every opt-in flag raised.
    review2 = ar.screen_examples(
        [ar.TrainExample("p", "c", provenance="scraped")],
        allow_synthetic=True, allow_model_output=True)
    assert not review2.ok


def test_dataset_digest_is_order_independent():
    a, b, c = _examples()
    d1 = ar.screen_examples([a, b, c]).dataset_sha256
    d2 = ar.screen_examples([c, a, b]).dataset_sha256
    assert d1 == d2 and len(d1) == 64


# --- payload hygiene -------------------------------------------------------------

def test_payload_refuses_code_and_pickle_formats(tmp_path):
    d = tmp_path / "adapter"
    d.mkdir()
    (d / "adapter.safetensors").write_bytes(b"x")
    (d / "post_init.py").write_text("import os\n", encoding="utf-8")
    (d / "weights.bin").write_bytes(b"\x80\x04")  # torch pickle container
    review = ar.review_adapter_payload(d)
    assert not review.ok
    assert set(review.refused) == {"post_init.py", "weights.bin"}


def test_payload_refuses_symlink_and_empty(tmp_path):
    d = tmp_path / "adapter"
    d.mkdir()
    assert not ar.review_adapter_payload(d).ok  # empty
    outside = tmp_path / "outside.safetensors"
    outside.write_bytes(b"x")
    (d / "adapter.safetensors").symlink_to(outside)
    review = ar.review_adapter_payload(d)
    assert not review.ok and "symlink" in review.reason


def test_payload_digest_tracks_bytes(tmp_path):
    d = tmp_path / "adapter"
    d.mkdir()
    f = d / "adapter.safetensors"
    f.write_bytes(b"aaa")
    d1 = ar.payload_digest_dir(d)
    f.write_bytes(b"bbb")
    assert ar.payload_digest_dir(d) != d1


# --- manifest + trainers ---------------------------------------------------------

def test_stub_trainer_is_deterministic_and_manifest_roundtrips(tmp_path):
    review = ar.screen_examples(_examples())
    m1 = ar.get_trainer("stub").train(review, "ollama:some-base", tmp_path / "a")
    m2 = ar.get_trainer("stub").train(review, "ollama:some-base", tmp_path / "b")
    assert (tmp_path / "a" / "adapter.safetensors").read_bytes() == \
           (tmp_path / "b" / "adapter.safetensors").read_bytes()
    loaded = ar.AdapterManifest.load(tmp_path / "a")
    assert loaded.adapter_id == m1.adapter_id
    assert loaded.base_model == "ollama:some-base"
    assert loaded.dataset_sha256 == review.dataset_sha256
    assert loaded.examples == 3 and loaded.trainer == "stub"
    assert loaded.payload_sha256 == ar.payload_digest_dir(tmp_path / "a")
    assert m1.adapter_id != m2.adapter_id  # identity is per-training-run


def test_trainer_registry():
    assert isinstance(ar.get_trainer("stub"), ar.StubTrainer)
    assert isinstance(ar.get_trainer("dpo-lora"), ar.DpoLoraTrainer)
    with pytest.raises(ValueError, match="unknown adapter trainer"):
        ar.get_trainer("full-finetune")


# --- held-out fitness ------------------------------------------------------------

def _cases(n: int = 64) -> list[str]:
    return [f"case-{i:03d}" for i in range(n)]


def _base_pass(case: str) -> bool:
    return int(case.split("-")[1]) % 2 == 0


def test_evaluate_adapter_genuine_uplift():
    def scorer(ref, ids):
        if "+adapter:" in ref:
            return dict.fromkeys(ids, True)
        return {c: _base_pass(c) for c in ids}

    res = ar.evaluate_adapter("ollama:b", "ollama:b+adapter:x", _cases(),
                              score_fn=scorer)
    assert res.ok and not res.overfit
    assert res.candidate_score > res.baseline_score
    assert res.samples >= 20


def test_evaluate_adapter_flags_memoriser_as_overfit():
    held_in, _ = corpus_split([{"goal": c} for c in _cases()], held_out_frac=0.35)
    seen = set(held_in)

    def scorer(ref, ids):
        if "+adapter:" in ref:
            return {c: (c in seen) or _base_pass(c) for c in ids}
        return {c: _base_pass(c) for c in ids}

    res = ar.evaluate_adapter("ollama:b", "ollama:b+adapter:x", _cases(),
                              score_fn=scorer)
    assert not res.ok and res.overfit and "OVERFIT" in res.reason


def test_evaluate_adapter_needs_cases():
    res = ar.evaluate_adapter("b", "b+adapter:x", ["only-one"],
                              score_fn=lambda ref, ids: {})
    assert not res.ok and "held-out" in res.reason


# --- store: activation pointer + rollback ----------------------------------------

def _manifest(tmp_path, name="a") -> ar.AdapterManifest:
    review = ar.screen_examples(_examples())
    return ar.get_trainer("stub").train(review, "ollama:some-base", tmp_path / name)


def test_store_activate_archives_previous_and_rollback_restores(tmp_path):
    store = ar.AdapterStore(root=tmp_path / "store")
    m_a, m_b = _manifest(tmp_path, "a"), _manifest(tmp_path, "b")
    store.activate(m_a, record_id="rec-a")
    bytes_a = (tmp_path / "store" / ar.ACTIVE_BASENAME).read_bytes()
    store.activate(m_b, record_id="rec-b")
    assert store.active()["adapter_id"] == m_b.adapter_id
    assert store.previous()["adapter_id"] == m_a.adapter_id
    restored = store.rollback()
    assert restored["adapter_id"] == m_a.adapter_id
    assert (tmp_path / "store" / ar.ACTIVE_BASENAME).read_bytes() == bytes_a
    assert store.previous() is None  # one-step: the handle is consumed


def test_store_rollback_without_previous_deactivates(tmp_path):
    store = ar.AdapterStore(root=tmp_path / "store")
    store.activate(_manifest(tmp_path), record_id="rec-a")
    assert store.rollback() is None
    assert store.active() is None


def test_store_survives_corrupt_pointer(tmp_path):
    store = ar.AdapterStore(root=tmp_path / "store")
    (tmp_path / "store").mkdir()
    (tmp_path / "store" / ar.ACTIVE_BASENAME).write_text("{not json", encoding="utf-8")
    assert store.active() is None


# --- serving seam ----------------------------------------------------------------

def test_tuned_model_name_is_tag_safe(tmp_path):
    m = _manifest(tmp_path)
    m.base_model = "ollama:family3:14b"
    name = ar.tuned_model_name(m)
    assert ":" not in name and name.startswith("family3-14b-lw-")


def test_modelfile_and_serving_artifacts(tmp_path):
    m = _manifest(tmp_path, "a")
    serving = ar.emit_serving_artifacts(m, tmp_path / "a")
    text = Path(serving["modelfile"]).read_text(encoding="utf-8")
    assert "FROM some-base" in text
    assert f"ADAPTER {(tmp_path / 'a').resolve()}" in text
    assert serving["create_command"].startswith(
        f"ollama create {ar.tuned_model_name(m)} -f ")
    # The rendered Modelfile itself stays inside the hygiene allowlist.
    assert ar.review_adapter_payload(tmp_path / "a").ok


def _enable_cfg(monkeypatch, tmp_path, **over):
    import maverick.config as config
    cfg = {"enable": True, "base_model": "ollama:some-base",
           "store_dir": str(tmp_path / "store"), "trainer": "stub",
           "allow_synthetic": False, "allow_model_output": False, **over}
    monkeypatch.setattr(config, "get_adapter_rung", lambda: cfg)
    return cfg


def test_effective_model_spec_disabled_is_identity(tmp_path, monkeypatch):
    _enable_cfg(monkeypatch, tmp_path, enable=False)
    assert ar.effective_model_spec("ollama:some-base") == "ollama:some-base"


def test_effective_model_spec_rewrites_only_matching_base(tmp_path, monkeypatch):
    _enable_cfg(monkeypatch, tmp_path)
    store = ar.AdapterStore(root=tmp_path / "store")
    m = _manifest(tmp_path)
    store.activate(m, record_id="rec-a")
    assert ar.effective_model_spec("ollama:some-base") == \
           f"ollama:{ar.tuned_model_name(m)}"
    assert ar.effective_model_spec("ollama:other") == "ollama:other"
    assert ar.effective_model_spec("anthropic:some-base") == "anthropic:some-base"


def test_parse_spec_integration_maps_promoted_adapter(tmp_path, monkeypatch):
    from maverick.llm import _parse_spec
    _enable_cfg(monkeypatch, tmp_path)
    store = ar.AdapterStore(root=tmp_path / "store")
    m = _manifest(tmp_path)
    store.activate(m, record_id="rec-a")
    assert _parse_spec("ollama:some-base") == ("ollama", ar.tuned_model_name(m))
    assert _parse_spec("ollama:untouched") == ("ollama", "untouched")


# --- config defaults ---------------------------------------------------------------

def test_get_adapter_rung_defaults(monkeypatch):
    import maverick.config as config
    monkeypatch.setattr(config, "load_config", lambda path=None: {})
    assert config.get_adapter_rung() == {
        "enable": False, "base_model": None, "store_dir": None,
        "trainer": "stub", "allow_synthetic": False, "allow_model_output": False,
    }


# --- the governed decision ---------------------------------------------------------

def _keys(tmp_path) -> Path:
    cryptography = pytest.importorskip("cryptography")  # noqa: F841
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    priv = ed25519.Ed25519PrivateKey.generate()
    keydir = tmp_path / "keys"
    keydir.mkdir()
    (keydir / "operator.priv.hex").write_text(priv.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption()).hex(), encoding="utf-8")
    (keydir / "operator.pub").write_bytes(priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    return keydir


def _genuine_scorer(ref, ids):
    if "+adapter:" in ref:
        return dict.fromkeys(ids, True)
    return {c: _base_pass(c) for c in ids}


def test_govern_adapter_change_promotes_and_activates(tmp_path):
    from maverick.self_improvement import PromotionLedger

    keydir = _keys(tmp_path)
    ledger = PromotionLedger(path=tmp_path / "ledger.json")
    store = ar.AdapterStore(root=tmp_path / "store")
    m = _manifest(tmp_path)
    res = ar.govern_adapter_change(tmp_path / "a", _cases(),
                                   score_fn=_genuine_scorer, keys_dir=keydir,
                                   ledger=ledger, store=store)
    assert res.hygiene_ok and res.promoted, res.reason
    assert res.fitness.samples >= 20
    assert store.active()["adapter_id"] == m.adapter_id
    assert store.active()["promotion_record_id"] == res.candidate_id
    rec = ledger.get(res.candidate_id)
    assert rec is not None and rec.rung == "weights"
    # The approved payload embeds the weights digest, so the ledger's
    # payload_sha256 (hash of that payload) transitively pins the weights.
    import hashlib
    approved_payload = json.dumps(
        {"adapter_id": m.adapter_id, "base_model": m.base_model,
         "weights_sha256": ar.payload_digest_dir(tmp_path / "a"),
         "dataset_sha256": m.dataset_sha256},
        sort_keys=True)
    assert rec.approval_signature and rec.payload_sha256 == \
        hashlib.sha256(approved_payload.encode("utf-8")).hexdigest()
    # Serving artifacts point at the tuned model.
    assert ar.tuned_model_name(m) in res.serving["create_command"]


def test_govern_adapter_change_refuses_dirty_payload_before_eval(tmp_path):
    keydir = _keys(tmp_path)
    _manifest(tmp_path)
    (tmp_path / "a" / "evil.py").write_text("import os\n", encoding="utf-8")

    def exploding_scorer(ref, ids):  # pragma: no cover -- must never run
        raise AssertionError("eval ran on a dirty payload")

    res = ar.govern_adapter_change(tmp_path / "a", _cases(),
                                   score_fn=exploding_scorer, keys_dir=keydir,
                                   store=ar.AdapterStore(root=tmp_path / "store"))
    assert not res.hygiene_ok and not res.promoted
    assert "evil.py" in res.reason


def test_govern_adapter_change_refuses_swapped_weights(tmp_path):
    keydir = _keys(tmp_path)
    _manifest(tmp_path)
    # Weights swapped AFTER training/manifest: digest mismatch, refused.
    (tmp_path / "a" / "adapter.safetensors").write_bytes(b"swapped")
    res = ar.govern_adapter_change(tmp_path / "a", _cases(),
                                   score_fn=_genuine_scorer, keys_dir=keydir,
                                   store=ar.AdapterStore(root=tmp_path / "store"))
    assert not res.promoted and "digest mismatch" in res.reason


def test_govern_adapter_change_refuses_overfit_and_rollback_restores(tmp_path):
    from maverick.self_improvement import PromotionLedger

    keydir = _keys(tmp_path)
    ledger = PromotionLedger(path=tmp_path / "ledger.json")
    store = ar.AdapterStore(root=tmp_path / "store")
    m_a = _manifest(tmp_path, "a")
    res_a = ar.govern_adapter_change(tmp_path / "a", _cases(),
                                     score_fn=_genuine_scorer, keys_dir=keydir,
                                     ledger=ledger, store=store)
    assert res_a.promoted

    held_in, _ = corpus_split([{"goal": c} for c in _cases()], held_out_frac=0.35)
    seen = set(held_in)

    def memoriser(ref, ids):
        if "+adapter:" in ref:
            return {c: (c in seen) or _base_pass(c) for c in ids}
        return {c: _base_pass(c) for c in ids}

    _manifest(tmp_path, "b")
    res_b = ar.govern_adapter_change(tmp_path / "b", _cases(),
                                     score_fn=memoriser, keys_dir=keydir,
                                     ledger=ledger, store=store)
    assert not res_b.promoted and res_b.fitness.overfit
    assert store.active()["adapter_id"] == m_a.adapter_id  # unchanged

    # Promote a genuine second adapter, then one-step rollback to the first.
    m_c = _manifest(tmp_path, "c")
    res_c = ar.govern_adapter_change(tmp_path / "c", _cases(),
                                     score_fn=_genuine_scorer, keys_dir=keydir,
                                     ledger=ledger, store=store)
    assert res_c.promoted and store.active()["adapter_id"] == m_c.adapter_id
    ar.rollback_adapter(res_c.candidate_id, store=store, ledger=ledger)
    assert store.active()["adapter_id"] == m_a.adapter_id
    assert ledger.get(res_c.candidate_id).rolled_back


def test_active_pointer_file_is_private(tmp_path):
    store = ar.AdapterStore(root=tmp_path / "store")
    store.activate(_manifest(tmp_path), record_id="rec-a")
    mode = (tmp_path / "store" / ar.ACTIVE_BASENAME).stat().st_mode & 0o777
    assert mode == 0o600


def test_manifest_load_ignores_unknown_keys(tmp_path):
    m = _manifest(tmp_path)
    path = tmp_path / "a" / ar.MANIFEST_BASENAME
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["future_field"] = "x"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert ar.AdapterManifest.load(tmp_path / "a").adapter_id == m.adapter_id
