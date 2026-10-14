"""Adapter serving integration with the shared artifact release authority."""

from __future__ import annotations

import hashlib
import json

import pytest
from maverick import adapter_rung as ar
from maverick.artifact_release_authority import ArtifactReleaseAuthority
from maverick.self_improvement import PromotionLedger


@pytest.fixture(autouse=True)
def _isolated_adapter_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.setenv("MAVERICK_GOVERNED_RECORDS_BACKEND", "local")
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "1")
    monkeypatch.setenv("MAVERICK_WORLD_BACKEND", "sqlite")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.setenv(ar.ADAPTER_RELEASE_NODE_ENV, "node-a")
    monkeypatch.setattr("maverick.audit.record", lambda *_args, **_kwargs: True)
    ar._reset_caches()
    yield
    ar._reset_caches()


class _PostgresAuthorityFacade:
    """Exercise the real state machine over isolated local test persistence."""

    backend_kind = "postgres"

    def __init__(self, delegate: ArtifactReleaseAuthority):
        self.delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)


class _FailingPublishAuthority(_PostgresAuthorityFacade):
    def publish_desired(self, *_args, **_kwargs):
        raise RuntimeError("simulated shared release outage")


class _ExplodingLocalAuthority:
    @property
    def backend_kind(self):
        raise AssertionError("local adapter promotion touched shared authority")


def _examples() -> list[ar.TrainExample]:
    return [
        ar.TrainExample("p1", "good", "bad", provenance="human_correction"),
        ar.TrainExample("p2", "good", "bad", provenance="tenant_trace"),
        ar.TrainExample("p3", "good", "bad", provenance="human_example"),
    ]


def _manifest(tmp_path) -> ar.AdapterManifest:
    review = ar.screen_examples(_examples())
    return ar.get_trainer("stub").train(
        review,
        "ollama:some-base",
        tmp_path / "adapter",
    )


def _cases() -> list[str]:
    return [f"case-{index:03d}" for index in range(64)]


def _score(ref: str, case_ids: list[str]) -> dict[str, bool]:
    if "+adapter:" in ref:
        return dict.fromkeys(case_ids, True)
    return {case: int(case.split("-")[1]) % 2 == 0 for case in case_ids}


def _approval(tmp_path):
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from maverick import approval_signing

    private = ed25519.Ed25519PrivateKey.generate()
    private_hex = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    ).hex()
    public_hex = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    ).hex()

    def approve(request):
        return approval_signing.sign_request(request, private_hex)

    def verify(candidate):
        signature = getattr(candidate, "approval_signature", None)
        if not signature:
            return None
        return approval_signing.verify(
            approval_signing.ApprovalRequest.for_candidate(candidate),
            str(signature),
            [public_hex],
        )

    return approve, verify, [public_hex]


def _provision(binding: dict) -> dict:
    digest = hashlib.sha256(
        json.dumps(
            {
                "model_name": binding["model_name"],
                "modelfile_sha256": binding["modelfile_sha256"],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return {
        "version": 1,
        "provider": "ollama",
        "verified": True,
        "model_name": binding["model_name"],
        "model_digest": digest,
        "source_modelfile_sha256": binding["modelfile_sha256"],
        "verified_at": 123.0,
    }


def _shared_test_runtime(tmp_path, monkeypatch):
    delegate = ArtifactReleaseAuthority()
    authority = _PostgresAuthorityFacade(delegate)
    ledger = PromotionLedger(path=tmp_path / "ledger.json")
    monkeypatch.setattr(
        ledger,
        "authority_locator",
        lambda: {"kind": "postgres", "store": "promotion-ledger-v1"},
    )
    monkeypatch.setattr(ar, "_ledger_from_receipt", lambda _receipt: ledger)
    monkeypatch.setattr(ar, "_configured_shared_adapter_authority", lambda: True)
    return authority, delegate, ledger


def _govern(
    tmp_path,
    *,
    ledger,
    authority,
    change_id: str,
    approval,
):
    approve, verifier, public_keys = approval
    store = ar.AdapterStore(root=tmp_path / "store")
    result = ar.govern_adapter_change(
        tmp_path / "adapter",
        _cases(),
        score_fn=_score,
        ledger=ledger,
        store=store,
        change_id=change_id,
        approve=approve,
        approval_verifier=verifier,
        approval_public_keys=public_keys,
        serving_provisioner=_provision,
        release_authority=authority,
        release_node_id="node-a",
    )
    return store, result


def _enable_serving(tmp_path, monkeypatch):
    from maverick import config

    monkeypatch.setattr(
        config,
        "get_adapter_rung",
        lambda: {
            "enable": True,
            "base_model": "ollama:some-base",
            "store_dir": str(tmp_path / "store"),
            "trainer": "stub",
            "allow_synthetic": False,
            "allow_model_output": False,
        },
    )
    ar._reset_caches()


def test_committed_shared_promotion_publishes_observes_and_gates_serving(
    tmp_path,
    monkeypatch,
):
    _manifest(tmp_path)
    approval = _approval(tmp_path)
    authority, delegate, ledger = _shared_test_runtime(tmp_path, monkeypatch)
    store, result = _govern(
        tmp_path,
        ledger=ledger,
        authority=authority,
        change_id="adapter-shared-1",
        approval=approval,
    )

    assert result.promoted, result.reason
    assert result.pointer["receipt"]["version"] == 3
    transaction_id = "promotion:adapter-shared-1"
    assert result.pointer["promotion_transaction_id"] == transaction_id
    desired = delegate.get_desired(ar.ADAPTER_RELEASE_DEPLOYMENT_ID)
    observation = delegate.get_observation(
        ar.ADAPTER_RELEASE_DEPLOYMENT_ID,
        "node-a",
    )
    assert desired.desired_digest == result.pointer["payload_sha256"]
    assert desired.promotion_record_id == result.candidate_id
    assert desired.promotion_transaction_id == transaction_id
    assert desired.rollout_state == "canary"
    assert observation.observed_generation == desired.generation
    assert observation.observed_digest == desired.desired_digest
    assert observation.health == "healthy"
    assert result.release["ready"] is True

    _enable_serving(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ar,
        "_resolve_adapter_release_authority",
        lambda _value=None: authority,
    )
    live_digest = result.pointer["serving"]["deployment"]["model_digest"]
    monkeypatch.setattr(ar, "_ollama_model_digest", lambda _name: live_digest)
    assert ar.effective_wire_model("ollama", "some-base") == ar.tuned_model_name(
        ar.AdapterManifest(
            adapter_id=result.pointer["adapter_id"],
            base_model=result.pointer["base_model"],
            trainer="",
            dataset_sha256="",
            examples=0,
            payload_sha256=result.pointer["payload_sha256"],
        )
    )

    desired = delegate.transition_rollout(
        ar.ADAPTER_RELEASE_DEPLOYMENT_ID,
        "paused",
        expected_revision=desired.revision,
        actor="operator",
    )
    assert desired.rollout_state == "paused"
    with pytest.raises(ar.SharedAdapterAuthorityError, match="not serving-ready"):
        ar.effective_wire_model("ollama", "some-base")
    assert store.active()["payload_sha256"] == desired.desired_digest


def test_post_commit_release_outage_is_non_routable_and_retry_converges(
    tmp_path,
    monkeypatch,
):
    _manifest(tmp_path)
    approval = _approval(tmp_path)
    authority, delegate, ledger = _shared_test_runtime(tmp_path, monkeypatch)
    failing = _FailingPublishAuthority(delegate)
    store, first = _govern(
        tmp_path,
        ledger=ledger,
        authority=failing,
        change_id="adapter-recover-release",
        approval=approval,
    )

    assert first.promoted is False
    assert "committed locally" in first.reason
    transaction = ledger.transaction("promotion:adapter-recover-release")
    assert transaction is not None and transaction.state == "committed"
    assert store.active()["promotion_record_id"] == "adapter-recover-release"
    assert delegate.get_desired(ar.ADAPTER_RELEASE_DEPLOYMENT_ID) is None

    _enable_serving(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ar,
        "_resolve_adapter_release_authority",
        lambda value=None: failing if value is None else value,
    )
    live_digest = first.pointer["serving"]["deployment"]["model_digest"]
    monkeypatch.setattr(ar, "_ollama_model_digest", lambda _name: live_digest)
    with pytest.raises(ar.SharedAdapterAuthorityError):
        ar.effective_wire_model("ollama", "some-base")

    recovered_store, recovered = _govern(
        tmp_path,
        ledger=ledger,
        authority=authority,
        change_id="adapter-recover-release",
        approval=approval,
    )
    assert recovered_store.root == store.root
    assert recovered.promoted, recovered.reason
    assert recovered.release["ready"] is True
    assert delegate.get_desired(
        ar.ADAPTER_RELEASE_DEPLOYMENT_ID
    ).promotion_transaction_id == "promotion:adapter-recover-release"


def test_shared_desired_does_not_fetch_missing_node_local_artifact(
    tmp_path,
    monkeypatch,
):
    _manifest(tmp_path)
    approval = _approval(tmp_path)
    authority, _delegate, ledger = _shared_test_runtime(tmp_path, monkeypatch)
    _store, result = _govern(
        tmp_path,
        ledger=ledger,
        authority=authority,
        change_id="adapter-local-bytes",
        approval=approval,
    )
    assert result.promoted, result.reason

    remote = ar.AdapterStore(root=tmp_path / "remote-node")
    assert remote.active() is None
    with pytest.raises(
        ar.SharedAdapterAuthorityError,
        match="artifact bytes remain node-local",
    ):
        ar.adapter_release_readiness(
            store=remote,
            release_authority=authority,
            node_id="node-b",
        )
    assert not (remote.root / "payloads").exists()


def test_shared_rollback_publishes_previous_digest_as_new_ready_generation(
    tmp_path,
    monkeypatch,
):
    _manifest(tmp_path)
    approval = _approval(tmp_path)
    authority, delegate, ledger = _shared_test_runtime(tmp_path, monkeypatch)
    store, first = _govern(
        tmp_path,
        ledger=ledger,
        authority=authority,
        change_id="adapter-before-rollback",
        approval=approval,
    )
    assert first.promoted, first.reason
    first_live = {
        first.pointer["serving"]["model_name"]: first.pointer["serving"][
            "deployment"
        ]["model_digest"]
    }
    monkeypatch.setattr(ar, "_ollama_model_digest", first_live.get)

    weight = next((tmp_path / "adapter").glob("*.safetensors"))
    weight.write_bytes(b"different-adapter-for-shared-rollback")
    changed = ar.AdapterManifest.load(tmp_path / "adapter")
    changed.payload_sha256 = ar.payload_digest_dir(tmp_path / "adapter")
    changed.save(tmp_path / "adapter")
    _same_store, second = _govern(
        tmp_path,
        ledger=ledger,
        authority=authority,
        change_id="adapter-being-rolled-back",
        approval=approval,
    )
    assert second.promoted, second.reason
    assert second.pointer["payload_sha256"] != first.pointer["payload_sha256"]

    installed = {
        first.pointer["serving"]["model_name"]: first.pointer["serving"][
            "deployment"
        ]["model_digest"],
        second.pointer["serving"]["model_name"]: second.pointer["serving"][
            "deployment"
        ]["model_digest"],
    }
    monkeypatch.setattr(ar, "_ollama_model_digest", installed.get)
    restored = ar.rollback_adapter(
        second.candidate_id,
        store=store,
        ledger=ledger,
        release_authority=authority,
        release_node_id="node-a",
    )

    assert restored["promotion_record_id"] == first.candidate_id
    desired = delegate.get_desired(ar.ADAPTER_RELEASE_DEPLOYMENT_ID)
    observed = delegate.get_observation(
        ar.ADAPTER_RELEASE_DEPLOYMENT_ID,
        "node-a",
    )
    assert desired.generation == 3
    assert desired.desired_digest == first.pointer["payload_sha256"]
    assert desired.previous_digest == second.pointer["payload_sha256"]
    assert desired.rollout_state == "rollback"
    assert observed.observed_generation == desired.generation
    assert observed.observed_digest == desired.desired_digest
    assert observed.health == "healthy"
    assert ledger.get(second.candidate_id).rolled_back is True


def test_shared_rollback_to_base_retires_desired_release(tmp_path, monkeypatch):
    _manifest(tmp_path)
    approval = _approval(tmp_path)
    authority, delegate, ledger = _shared_test_runtime(tmp_path, monkeypatch)
    store, result = _govern(
        tmp_path,
        ledger=ledger,
        authority=authority,
        change_id="adapter-retire-to-base",
        approval=approval,
    )
    assert result.promoted, result.reason

    assert ar.rollback_adapter(
        result.candidate_id,
        store=store,
        ledger=ledger,
        release_authority=authority,
        release_node_id="node-a",
    ) is None
    desired = delegate.get_desired(ar.ADAPTER_RELEASE_DEPLOYMENT_ID)
    observed = delegate.get_observation(
        ar.ADAPTER_RELEASE_DEPLOYMENT_ID,
        "node-a",
    )
    assert desired.rollout_state == "retired"
    assert observed.observed_generation == 0
    assert observed.observed_digest is None
    assert observed.health == "offline"

    _enable_serving(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ar,
        "_resolve_adapter_release_authority",
        lambda _value=None: authority,
    )
    assert ar.effective_wire_model("ollama", "some-base") == "some-base"


def test_local_single_node_promotion_never_touches_shared_release_authority(
    tmp_path,
):
    _manifest(tmp_path)
    approval = _approval(tmp_path)
    ledger = PromotionLedger(path=tmp_path / "ledger.json")
    store, result = _govern(
        tmp_path,
        ledger=ledger,
        authority=_ExplodingLocalAuthority(),
        change_id="adapter-local-only",
        approval=approval,
    )

    assert result.promoted, result.reason
    assert result.release is None
    assert result.pointer["receipt"]["version"] == 2
    assert "promotion_transaction_id" not in result.pointer
    assert store.active()["promotion_record_id"] == "adapter-local-only"
