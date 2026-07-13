"""Tests for maverick.adapter_rung -- the governed weights (adapter) rung."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from maverick import adapter_rung as ar
from maverick.self_harness_eval import corpus_split


@pytest.fixture(autouse=True)
def _reset_adapter_caches():
    """The config/pointer memo caches are module-global; clear them around each
    test so mtime-keyed entries never leak across the isolated per-test HOMEs."""
    ar._reset_caches()
    yield
    ar._reset_caches()


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


def test_parse_spec_stays_pure_even_with_active_adapter(tmp_path, monkeypatch):
    # The rewrite lives in the PROVIDER client, not the parser: _parse_spec must
    # return the base id byte-identically so allow-lists, pricing, and metrics
    # key on the stable base (the code-review layering/cardinality findings).
    from maverick.llm import _parse_spec
    _enable_cfg(monkeypatch, tmp_path)
    store = ar.AdapterStore(root=tmp_path / "store")
    m = _manifest(tmp_path)
    store.activate(m, record_id="rec-a")
    assert _parse_spec("ollama:some-base") == ("ollama", "some-base")
    assert _parse_spec("ollama:untouched") == ("ollama", "untouched")


def test_ollama_client_resolves_adapter_at_wire_time(tmp_path, monkeypatch):
    # The wire-level seam: OllamaClient._build_kwargs swaps base -> tuned name
    # when (and only when) the rung is enabled with a matching active pointer.
    pytest.importorskip("openai")  # OllamaClient rides the OpenAI-compatible client
    from maverick.providers.ollama_provider import OllamaClient

    _enable_cfg(monkeypatch, tmp_path)
    store = ar.AdapterStore(root=tmp_path / "store")
    m = _manifest(tmp_path)
    store.activate(m, record_id="rec-a")
    client = OllamaClient()
    kw = client._build_kwargs("sys", [{"role": "user", "content": "hi"}],
                              None, 64, "some-base")
    assert kw["model"] == ar.tuned_model_name(m)
    # Non-matching base and disabled rung both serve the base unchanged.
    kw2 = client._build_kwargs("sys", [{"role": "user", "content": "hi"}],
                               None, 64, "other-model")
    assert kw2["model"] == "other-model"
    _enable_cfg(monkeypatch, tmp_path, enable=False)
    ar._reset_caches()
    kw3 = client._build_kwargs("sys", [{"role": "user", "content": "hi"}],
                               None, 64, "some-base")
    assert kw3["model"] == "some-base"


def test_effective_wire_model_matches_local_alias(tmp_path, monkeypatch):
    # 'local' is a registry alias for ollama; a pointer recorded under either
    # provider spelling must resolve for both.
    _enable_cfg(monkeypatch, tmp_path)
    store = ar.AdapterStore(root=tmp_path / "store")
    m = _manifest(tmp_path)  # base_model recorded as "ollama:some-base"
    store.activate(m, record_id="rec-a")
    assert ar.effective_wire_model("local", "some-base") == ar.tuned_model_name(m)
    assert ar.effective_wire_model("vllm", "some-base") == "some-base"
    assert ar.effective_wire_model("anthropic", "some-base") == "some-base"


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


# --- hardening regressions (code-review findings) --------------------------------

def test_payload_refuses_directory_symlink(tmp_path):
    # A symlink to a DIRECTORY must be refused: Path.is_dir() follows the link,
    # so an is_dir() check ahead of the symlink check would skip (not refuse) it,
    # smuggling unreviewed content past the boundary and out of the digest.
    outside = tmp_path / "external"
    outside.mkdir()
    (outside / "post_init.py").write_text("import os\n", encoding="utf-8")
    d = tmp_path / "adapter"
    d.mkdir()
    (d / "adapter.safetensors").write_bytes(b"w")
    (d / "sub").symlink_to(outside, target_is_directory=True)
    review = ar.review_adapter_payload(d)
    assert not review.ok and "symlink" in review.reason


def test_payload_requires_a_weight_file(tmp_path):
    # Metadata-only dir (no .safetensors/.gguf) is not an adapter, even though
    # every file is individually allowed.
    d = tmp_path / "adapter"
    d.mkdir()
    (d / "adapter_config.json").write_text("{}", encoding="utf-8")
    (d / "README.md").write_text("card", encoding="utf-8")
    review = ar.review_adapter_payload(d)
    assert not review.ok and "no weight file" in review.reason
    (d / "adapter_model.safetensors").write_bytes(b"w")
    assert ar.review_adapter_payload(d).ok


def test_payload_accepts_standard_hf_adapter_files(tmp_path):
    # A realistic HF/PEFT export must clear hygiene: weights + config + tokenizer
    # protobuf + model card + .gitattributes.
    d = tmp_path / "adapter"
    d.mkdir()
    (d / "adapter_model.safetensors").write_bytes(b"w")
    (d / "adapter_config.json").write_text("{}", encoding="utf-8")
    (d / "tokenizer.model").write_bytes(b"\x0a\x00proto")  # sentencepiece protobuf
    (d / "README.md").write_text("card", encoding="utf-8")
    (d / ".gitattributes").write_text("*.safetensors filter=lfs\n", encoding="utf-8")
    review = ar.review_adapter_payload(d)
    assert review.ok, review.reason
    # ...but a smuggled script alongside them is still refused.
    (d / "run.py").write_text("import os\n", encoding="utf-8")
    assert not ar.review_adapter_payload(d).ok


def _fake_rlaif(monkeypatch, captured: dict):
    """Install a fake maverick.training.rlaif.train that records its args and
    writes a valid safetensors adapter, so the dpo-lora seam is exercised
    without torch/peft/GPU."""
    import types

    def fake_train(pairs, base_model, out_dir, *, lora=False, **kw):
        captured["pairs"] = pairs
        captured["base_model"] = base_model
        captured["lora"] = lora
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "adapter_model.safetensors").write_bytes(b"trained")
        return 0

    fake_mod = types.SimpleNamespace(train=fake_train)
    import maverick.training as training_pkg
    monkeypatch.setattr(training_pkg, "rlaif", fake_mod, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "maverick.training.rlaif", fake_mod)
    return captured


def test_dpo_lora_passes_dict_pairs_and_strips_prefix(tmp_path, monkeypatch):
    captured: dict = {}
    _fake_rlaif(monkeypatch, captured)
    review = ar.screen_examples([
        ar.TrainExample("prompt one", "good answer", "bad answer",
                        provenance="human_correction"),
        ar.TrainExample("prompt two", "great", "meh", provenance="tenant_trace"),
    ])
    manifest = ar.get_trainer("dpo-lora").train(review, "ollama:some-base", tmp_path / "a")
    # pairs are dicts with the keys rlaif requires, NOT tuples.
    assert isinstance(captured["pairs"], list) and captured["pairs"]
    for row in captured["pairs"]:
        assert set(row) >= {"chosen_text", "rejected_text", "weight"}
        assert isinstance(row["chosen_text"], str)
    # the ollama: serving prefix is stripped before it reaches from_pretrained.
    assert captured["base_model"] == "some-base"
    assert captured["lora"] is True
    # a real, hygiene-clean, digest-bound manifest is produced.
    assert manifest.trainer == "dpo-lora"
    assert ar.review_adapter_payload(tmp_path / "a").ok
    assert manifest.payload_sha256 == ar.payload_digest_dir(tmp_path / "a")


def test_dpo_lora_requires_preference_pairs(tmp_path, monkeypatch):
    captured: dict = {}
    _fake_rlaif(monkeypatch, captured)
    review = ar.screen_examples([
        ar.TrainExample("p", "just a completion", "", provenance="human_example")])
    with pytest.raises(RuntimeError, match="preference pairs"):
        ar.get_trainer("dpo-lora").train(review, "ollama:some-base", tmp_path / "a")
    assert "pairs" not in captured  # never reached rlaif


def test_govern_threads_configured_min_improvement(tmp_path, monkeypatch):
    # A small held-out uplift below the configured floor must be refused when
    # govern builds its own controller (no explicit controller passed).
    import maverick.config as config
    monkeypatch.setattr(config, "get_self_improvement",
                        lambda: {"min_improvement": 0.5})
    keydir = _keys(tmp_path)
    _manifest(tmp_path)

    def small_uplift(ref, ids):
        # base resolves ~half; adapter adds only the first held-out case (< 0.5).
        if "+adapter:" in ref:
            return {c: (_base_pass(c) or c == ids[0]) for c in ids}
        return {c: _base_pass(c) for c in ids}

    res = ar.govern_adapter_change(tmp_path / "a", _cases(),
                                   score_fn=small_uplift, keys_dir=keydir,
                                   store=ar.AdapterStore(root=tmp_path / "store"))
    assert not res.promoted
    assert "min" in res.reason.lower() or "gate refused" in res.reason.lower()


def test_govern_refuses_empty_base_model_id(tmp_path):
    keydir = _keys(tmp_path)
    review = ar.screen_examples(_examples())
    ar.get_trainer("stub").train(review, "ollama:", tmp_path / "a")  # blank model id
    res = ar.govern_adapter_change(tmp_path / "a", _cases(),
                                   score_fn=_genuine_scorer, keys_dir=keydir,
                                   store=ar.AdapterStore(root=tmp_path / "store"))
    assert not res.promoted and "no model id" in res.reason


def test_effective_model_spec_skips_non_ollama_base(tmp_path, monkeypatch):
    # A vLLM/TGI base must never be rewritten to the ollama: provider.
    _enable_cfg(monkeypatch, tmp_path, base_model="vllm:Qwen3-Coder")
    store = ar.AdapterStore(root=tmp_path / "store")
    review = ar.screen_examples(_examples())
    m = ar.get_trainer("stub").train(review, "vllm:Qwen3-Coder", tmp_path / "a")
    store.activate(m, record_id="rec-a")
    assert ar.effective_model_spec("vllm:Qwen3-Coder") == "vllm:Qwen3-Coder"


def test_emit_serving_artifacts_notes_non_ollama_base(tmp_path):
    review = ar.screen_examples(_examples())
    m = ar.get_trainer("stub").train(review, "vllm:Qwen3-Coder", tmp_path / "a")
    serving = ar.emit_serving_artifacts(m, tmp_path / "a")
    assert serving["create_command"] is None and "manually" in serving["note"]
    assert not (tmp_path / "a" / ar.SERVING_BASENAME).exists()


def test_effective_model_spec_disabled_does_not_read_config_tree(tmp_path, monkeypatch):
    # The disabled default path must not pay a full get_adapter_rung() per call.
    import maverick.config as config
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return {"enable": False}

    monkeypatch.setattr(config, "get_adapter_rung", counting)
    ar._reset_caches()
    for _ in range(5):
        assert ar.effective_model_spec("ollama:base") == "ollama:base"
    # memoized by config stat signature -> at most one real read, not five.
    assert calls["n"] <= 1


def test_atomic_write_json_delegates_to_shared_helper(tmp_path, monkeypatch):
    # Regression: _atomic_write_json routes through file_lock.atomic_write_text
    # (unique mkstemp temp name), not a PID-keyed temp that collides.
    import maverick.file_lock as fl
    seen = {}
    real = fl.atomic_write_text

    def spy(path, text, *, mode=0o600):
        seen["mode"] = mode
        return real(path, text, mode=mode)

    monkeypatch.setattr(fl, "atomic_write_text", spy)
    ar._atomic_write_json(tmp_path / "p.json", {"a": 1})
    assert seen.get("mode") == 0o600
    assert json.loads((tmp_path / "p.json").read_text()) == {"a": 1}
    assert (tmp_path / "p.json").stat().st_mode & 0o777 == 0o600
