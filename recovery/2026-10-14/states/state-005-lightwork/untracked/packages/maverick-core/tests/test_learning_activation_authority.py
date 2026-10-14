"""Adversarial regressions for shared learning and activation authority."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest
from maverick import domain, factory_learning, self_learning, verifier_head
from maverick.artifact_release_authority import ArtifactReleaseAuthority
from maverick.governed_records import GovernedRecordStore
from maverick.learning_authority import (
    LearningAuthorityConflict,
    LearningAuthorityError,
    claim_verifier_confirmation,
    converge_learning_release,
    promotion_authority_provenance,
    record_self_learning_entry,
    require_learning_release,
    retire_learning_release,
    self_learning_entries,
)
from maverick.privacy_ops import RecordConflict
from maverick.self_improvement import (
    ArtifactRevision,
    PromotionRecord,
    PromotionTransaction,
)

_DIGEST_A = "11" * 32
_DIGEST_B = "22" * 32
_DIGEST_C = "33" * 32


@pytest.fixture(autouse=True)
def _local_compatibility_plane(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / ".maverick"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.setenv("MAVERICK_GOVERNED_RECORDS_BACKEND", "local")
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "1")
    monkeypatch.setenv("MAVERICK_WORLD_BACKEND", "sqlite")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.setattr("maverick.audit.record", lambda *_args, **_kwargs: True)


class _PostgresReleaseFacade:
    """Expose a local test authority through the shared-plane contract."""

    backend_kind = "postgres"

    def __init__(
        self,
        inner: ArtifactReleaseAuthority,
        *,
        lose_publish=False,
        lose_transition=False,
        lose_observation=False,
        before_publish=None,
        before_transition=None,
    ):
        self.inner = inner
        self.lose_publish = lose_publish
        self.lose_transition = lose_transition
        self.lose_observation = lose_observation
        self.before_publish = before_publish
        self.before_transition = before_transition

    def publish_desired(self, *args, **kwargs):
        if self.before_publish is not None:
            callback, self.before_publish = self.before_publish, None
            callback(self.inner, *args, **kwargs)
        result = self.inner.publish_desired(*args, **kwargs)
        if self.lose_publish:
            self.lose_publish = False
            raise OSError("response lost after commit")
        return result

    def transition_rollout(self, *args, **kwargs):
        if self.before_transition is not None:
            callback, self.before_transition = self.before_transition, None
            callback(self.inner, *args, **kwargs)
        result = self.inner.transition_rollout(*args, **kwargs)
        if self.lose_transition:
            self.lose_transition = False
            raise OSError("response lost after rollout commit")
        return result

    def report_observation(self, *args, **kwargs):
        result = self.inner.report_observation(*args, **kwargs)
        if self.lose_observation:
            self.lose_observation = False
            raise OSError("response lost after observation commit")
        return result

    def __getattr__(self, name):
        return getattr(self.inner, name)


class _BackendFacade:
    backend_kind = "postgres"

    def __init__(self, inner, *, now: float):
        self.inner = inner
        self.now = now

    def authoritative_time(self) -> float:
        return self.now

    def __getattr__(self, name):
        return getattr(self.inner, name)


class _SharedDocument:
    backend_kind = "postgres"

    def __init__(self, *, now: float = 700.0):
        self.document: dict = {}
        self.now = now
        self.lock = threading.Lock()

    def mutate_with_authoritative_time(self, callback, **_kwargs):
        with self.lock:
            working = copy.deepcopy(self.document)
            result = callback(working, self.now)
            self.document = working
            return result

    def read(self):
        with self.lock:
            return copy.deepcopy(self.document)


class _ClaimStore:
    backend_kind = "postgres"

    def __init__(self, *, lose_after_create: bool = False):
        self.rows: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.lose_after_create = lose_after_create

    def authoritative_time(self) -> float:
        return 800.0

    def create(self, raw, **_kwargs):
        with self.lock:
            record_id = raw["id"]
            if record_id in self.rows:
                raise RecordConflict("already exists")
            self.rows[record_id] = copy.deepcopy(raw)
            saved = copy.deepcopy(raw)
            lose = self.lose_after_create
            self.lose_after_create = False
        if lose:
            raise OSError("response lost after commit")
        return saved

    def get(self, record_id):
        with self.lock:
            value = self.rows.get(record_id)
            return copy.deepcopy(value) if value is not None else None


class _Ledger:
    def __init__(self, transactions):
        self.items = list(transactions)
        self.unrelated_revision = 1

    def authority_locator(self):
        return {"kind": "postgres", "store": "promotion-ledger-v1"}

    def transactions(self, *, artifact_identity, state):
        assert state == "committed"
        return [
            item for item in self.items
            if item.before.identity == artifact_identity
        ]


def _release(
    authority,
    digest: str,
    *,
    transaction: str,
    expected_previous_digest: str | None,
    node: str,
):
    return converge_learning_release(
        deployment_id="learning/verifier/test",
        artifact_kind="verifier",
        digest=digest,
        promotion_record_id=f"record-{transaction}",
        promotion_transaction_id=transaction,
        expected_previous_digest=expected_previous_digest,
        actor="test-release",
        authority=authority,
        node_id=node,
    )


def _revisions(identity: str = "artifact:test"):
    return (
        ArtifactRevision(identity, _DIGEST_A, "v1"),
        ArtifactRevision(identity, _DIGEST_B, "v2"),
        ArtifactRevision(identity, _DIGEST_C, "v3"),
    )


def _transaction(*, rolled_back: bool = False):
    before, after, _other = _revisions()
    record = PromotionRecord(
        id="promotion-1",
        rung="code",
        summary="exact test promotion",
        baseline_score=0.5,
        candidate_score=0.8,
        promoted_at=10.0,
        rolled_back=rolled_back,
        rolled_back_at=20.0 if rolled_back else None,
    )
    return PromotionTransaction(
        id="transaction-1",
        record=record,
        before=before,
        after=after,
        prepared_at=9.0,
        state="committed",
        resolved_at=10.0,
    )


@pytest.mark.parametrize(
    ("failure_point", "message"),
    [
        ("publish", "desired state"),
        ("transition", "rollout admission"),
        ("observation", "node observation"),
    ],
)
def test_release_recovers_lost_response_and_requires_exact_digest(
    failure_point, message,
):
    facade = _PostgresReleaseFacade(ArtifactReleaseAuthority(), **{
        f"lose_{failure_point}": True,
    })

    with pytest.raises(LearningAuthorityError, match=message):
        _release(
            facade,
            _DIGEST_A,
            transaction="transaction-a",
            expected_previous_digest=None,
            node="node-a",
        )

    recovered = _release(
        facade,
        _DIGEST_A,
        transaction="transaction-a",
        expected_previous_digest=None,
        node="node-a",
    )
    assert recovered.generation == 1
    assert recovered.ready is True

    with pytest.raises(LearningAuthorityConflict, match="exact shared desired"):
        require_learning_release(
            deployment_id="learning/verifier/test",
            artifact_kind="verifier",
            digest=_DIGEST_B,
            actor="hostile-node",
            authority=facade,
            node_id="node-b",
        )


def test_competing_releases_cannot_serialize_a_stale_candidate():
    facade = _PostgresReleaseFacade(ArtifactReleaseAuthority())
    _release(
        facade,
        _DIGEST_A,
        transaction="transaction-a",
        expected_previous_digest=None,
        node="node-a",
    )
    barrier = threading.Barrier(2)

    def publish(index: int):
        barrier.wait(timeout=10)
        try:
            return _release(
                facade,
                (_DIGEST_B, _DIGEST_C)[index],
                transaction=f"transaction-{index + 2}",
                expected_previous_digest=_DIGEST_A,
                node=f"node-{index + 2}",
            )
        except LearningAuthorityConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(publish, range(2)))

    winners = [item for item in outcomes if item is not None]
    assert len(winners) == 1
    assert winners[0].generation == 2
    assert facade.get_desired("learning/verifier/test").generation == 2


def test_publish_retry_refuses_same_digest_predecessor_replacement():
    inner = ArtifactReleaseAuthority()
    _release(
        _PostgresReleaseFacade(inner),
        _DIGEST_A,
        transaction="transaction-a",
        expected_previous_digest=None,
        node="node-a",
    )

    def replace_predecessor(authority, deployment_id, **kwargs):
        assert kwargs["desired_digest"] == _DIGEST_B
        predecessor = authority.get_desired(deployment_id)
        authority.publish_desired(
            deployment_id,
            artifact_kind="verifier",
            desired_digest=_DIGEST_A,
            promotion_record_id="record-republished-a",
            promotion_transaction_id="transaction-republished-a",
            expected_revision=predecessor.revision,
            actor="competing-publisher",
        )

    facade = _PostgresReleaseFacade(inner, before_publish=replace_predecessor)
    with pytest.raises(LearningAuthorityConflict, match="predecessor CAS"):
        _release(
            facade,
            _DIGEST_B,
            transaction="transaction-b",
            expected_previous_digest=_DIGEST_A,
            node="node-b",
        )

    winner = inner.get_desired("learning/verifier/test")
    assert winner.desired_digest == _DIGEST_A
    assert winner.promotion_transaction_id == "transaction-republished-a"
    assert winner.rollout_state == "pending"


def test_rollout_retry_refuses_to_advance_competing_release():
    inner = ArtifactReleaseAuthority()

    def publish_successor(authority, deployment_id, _state, **kwargs):
        current = authority.get_desired(deployment_id)
        authority.publish_desired(
            deployment_id,
            artifact_kind="verifier",
            desired_digest=_DIGEST_B,
            promotion_record_id="record-transaction-b",
            promotion_transaction_id="transaction-b",
            expected_revision=current.revision,
            actor="competing-publisher",
        )

    facade = _PostgresReleaseFacade(inner, before_transition=publish_successor)
    with pytest.raises(LearningAuthorityConflict, match="rollout admission"):
        _release(
            facade,
            _DIGEST_A,
            transaction="transaction-a",
            expected_previous_digest=None,
            node="node-a",
        )

    winner = inner.get_desired("learning/verifier/test")
    assert winner.desired_digest == _DIGEST_B
    assert winner.promotion_transaction_id == "transaction-b"
    assert winner.rollout_state == "pending"


def test_retirement_retry_refuses_to_retire_successor():
    inner = ArtifactReleaseAuthority()
    _release(
        _PostgresReleaseFacade(inner),
        _DIGEST_A,
        transaction="transaction-a",
        expected_previous_digest=None,
        node="node-a",
    )

    def publish_successor(authority, deployment_id, _state, **kwargs):
        current = authority.get_desired(deployment_id)
        authority.publish_desired(
            deployment_id,
            artifact_kind="verifier",
            desired_digest=_DIGEST_B,
            promotion_record_id="record-transaction-b",
            promotion_transaction_id="transaction-b",
            expected_revision=current.revision,
            actor="competing-publisher",
        )

    facade = _PostgresReleaseFacade(inner, before_transition=publish_successor)
    with pytest.raises(LearningAuthorityConflict, match="retirement"):
        retire_learning_release(
            deployment_id="learning/verifier/test",
            actor="test-retirement",
            authority=facade,
            node_id="node-a",
        )

    winner = inner.get_desired("learning/verifier/test")
    assert winner.desired_digest == _DIGEST_B
    assert winner.promotion_transaction_id == "transaction-b"
    assert winner.rollout_state == "pending"


def test_shared_observation_uses_database_time_even_with_hostile_node_time():
    releases = _BackendFacade(
        GovernedRecordStore("test_release_clock", "ARL", "release"),
        now=4321.0,
    )
    observations = _BackendFacade(
        GovernedRecordStore("test_observation_clock", "ARO", "observation"),
        now=4321.0,
    )
    authority = ArtifactReleaseAuthority(
        release_store=releases,
        observation_store=observations,
        clock=lambda: 1.0,
    )
    desired = authority.publish_desired(
        "learning/verifier/clock",
        artifact_kind="verifier",
        desired_digest=_DIGEST_A,
        promotion_record_id="record-clock",
        promotion_transaction_id="transaction-clock",
        expected_revision=0,
        actor="clock-test",
    )

    observation = authority.report_observation(
        desired.deployment_id,
        "skewed-node",
        observed_generation=1,
        observed_digest=_DIGEST_A,
        health="healthy",
        observed_at=999_999_999.0,
        expected_revision=0,
        actor="clock-test",
    )

    assert observation.observed_at == 4321.0


def test_self_learning_state_is_concurrent_idempotent_and_database_timed():
    store = _SharedDocument()

    def append(index: int):
        return record_self_learning_entry(
            {
                "need": f"need-{index}",
                "kind": "tool",
                "name": f"tool-{index}",
                "source": "catalog",
                "outcome": "acquired",
            },
            observation_id=f"observation-{index}",
            store=store,
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(append, range(32)))

    assert len(results) == 32
    assert {item["name"] for item in results} == {
        f"tool-{index}" for index in range(32)
    }
    assert {item["ts"] for item in results} == {700.0}
    assert len(self_learning_entries(store=store)) == 32

    retry = append(0)
    assert retry == results[0]
    with pytest.raises(LearningAuthorityConflict, match="different intent"):
        record_self_learning_entry(
            {
                "need": "changed",
                "kind": "tool",
                "name": "tool-0",
                "source": "catalog",
                "outcome": "acquired",
            },
            observation_id="observation-0",
            store=store,
        )

    store.document["attacker"] = True
    with pytest.raises(LearningAuthorityError, match="malformed"):
        self_learning_entries(store=store)


def test_confirmation_claim_recovers_lost_response_and_rejects_rebinding():
    before, challenger, other = _revisions()
    store = _ClaimStore(lose_after_create=True)

    with pytest.raises(LearningAuthorityError, match="unavailable"):
        claim_verifier_confirmation(
            cohort_sha256=_DIGEST_A,
            before=before.to_dict(),
            challenger=challenger.to_dict(),
            store=store,
        )

    recovered = claim_verifier_confirmation(
        cohort_sha256=_DIGEST_A,
        before=before.to_dict(),
        challenger=challenger.to_dict(),
        store=store,
    )
    assert recovered["consumed_at"] == 800.0

    with pytest.raises(LearningAuthorityConflict, match="another challenger"):
        claim_verifier_confirmation(
            cohort_sha256=_DIGEST_A,
            before=before.to_dict(),
            challenger=other.to_dict(),
            store=store,
        )

    row = next(iter(store.rows.values()))
    row["unexpected"] = "hostile"
    with pytest.raises(LearningAuthorityError, match="malformed"):
        claim_verifier_confirmation(
            cohort_sha256=_DIGEST_A,
            before=before.to_dict(),
            challenger=challenger.to_dict(),
            store=store,
        )


def test_concurrent_confirmation_claim_has_one_exact_winner():
    before, challenger, other = _revisions()
    store = _ClaimStore()
    barrier = threading.Barrier(2)

    def claim(revision):
        barrier.wait(timeout=10)
        try:
            return claim_verifier_confirmation(
                cohort_sha256=_DIGEST_C,
                before=before.to_dict(),
                challenger=revision.to_dict(),
                store=store,
            )
        except LearningAuthorityConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, (challenger, other)))

    assert sum(item is not None for item in outcomes) == 1
    assert len(store.rows) == 1


def test_promotion_release_identity_ignores_unrelated_ledger_activity():
    transaction = _transaction()
    ledger = _Ledger([transaction])
    first = promotion_authority_provenance(
        ledger, artifact_identity=transaction.before.identity,
    )
    ledger.unrelated_revision += 100
    second = promotion_authority_provenance(
        ledger, artifact_identity=transaction.before.identity,
    )

    assert second == first
    assert first["release_predecessor_sha256"] == _DIGEST_A

    ledger.items[0] = replace(
        transaction,
        record=replace(
            transaction.record,
            rolled_back=True,
            rolled_back_at=20.0,
        ),
    )
    rolled_back = promotion_authority_provenance(
        ledger, artifact_identity=transaction.before.identity,
    )
    assert rolled_back["promotion_chain_sha256"] != first[
        "promotion_chain_sha256"
    ]
    assert rolled_back["release_predecessor_sha256"] == _DIGEST_B


def test_verifier_and_factory_release_intents_bind_chain_predecessor():
    transaction = _transaction()
    controller = SimpleNamespace(ledger=_Ledger([transaction]))

    verifier = verifier_head._verifier_release_intent(
        controller, transaction.after,
    )
    factory = factory_learning._factory_release_intent(
        controller, transaction.after,
    )
    controller.ledger.unrelated_revision += 1

    assert verifier == verifier_head._verifier_release_intent(
        controller, transaction.after,
    )
    assert factory == factory_learning._factory_release_intent(
        controller, transaction.after,
    )
    assert verifier[3] == _DIGEST_A
    assert factory[3] == _DIGEST_A


def test_self_learning_cutover_refuses_explicit_and_legacy_local_ledgers(
    tmp_path, monkeypatch,
):
    legacy = tmp_path / "learned.ndjson"
    captured = []
    monkeypatch.setattr(
        "maverick.learning_authority.shared_learning_authority_selected",
        lambda: True,
    )
    monkeypatch.setattr(self_learning, "learned_path", lambda: legacy)
    monkeypatch.setattr(
        "maverick.learning_authority.record_self_learning_entry",
        lambda logical, **kwargs: captured.append((logical, kwargs)),
    )

    assert self_learning._record_impl(
        "need", "tool", "safe_tool", path=tmp_path / "explicit.ndjson",
    ) is False
    legacy.write_text("legacy\n", encoding="utf-8")
    assert self_learning._record_impl("need", "tool", "safe_tool") is False
    legacy.unlink()
    assert self_learning._record_impl(
        "need", "tool", "safe_tool", observation_id="retry-1",
    ) is True
    assert captured[0][1]["observation_id"] == "retry-1"


def test_verifier_confirmation_cutover_refuses_local_claims(
    tmp_path, monkeypatch,
):
    artifact_dir = tmp_path / "verifier"
    active = artifact_dir / "active.json"
    artifact_dir.mkdir()
    deployment = verifier_head.VerifierArtifactDeployment(
        artifact_dir=artifact_dir,
        active_path=active,
    )
    before = deployment.bootstrap_revision
    after = ArtifactRevision(deployment.identity, _DIGEST_B, "linear-v2")
    monkeypatch.setattr(
        verifier_head,
        "_shared_verifier_release_selected",
        lambda: True,
    )
    monkeypatch.setattr(
        verifier_head.VerifierArtifactDeployment,
        "authoritative_revision",
        lambda self, controller: before,
    )
    evaluations = artifact_dir / "evaluations"
    evaluations.mkdir()
    (evaluations / "legacy.json").write_text("{}", encoding="utf-8")

    with pytest.raises(
        verifier_head.VerifierRecoveryRequired,
        match="legacy local confirmation claims",
    ):
        deployment.consume_confirmation(
            object(),
            cohort_sha256=_DIGEST_A,
            before=before,
            after=after,
        )

    (evaluations / "legacy.json").unlink()
    captured = []
    monkeypatch.setattr(
        "maverick.learning_authority.claim_verifier_confirmation",
        lambda **kwargs: captured.append(kwargs),
    )
    receipt = deployment.consume_confirmation(
        object(),
        cohort_sha256=_DIGEST_A,
        before=before,
        after=after,
    )
    assert receipt.suffix == ".claim"
    assert captured[0]["cohort_sha256"] == _DIGEST_A
    assert not list(evaluations.glob("*.json"))


def test_factory_serving_fails_closed_when_shared_release_is_unavailable(
    tmp_path, monkeypatch,
):
    path = tmp_path / "factory-corrections.json"
    state = object()
    revision = SimpleNamespace(identity="factory:test", sha256=_DIGEST_A)
    monkeypatch.setattr(
        factory_learning,
        "_shared_factory_release_selected",
        lambda: True,
    )
    monkeypatch.setattr(
        "maverick.self_improvement.shared",
        lambda: SimpleNamespace(ledger=object()),
    )
    monkeypatch.setattr(
        factory_learning,
        "_recover_artifact_locked",
        lambda *_args: state,
    )
    monkeypatch.setattr(
        factory_learning,
        "_artifact_revision",
        lambda *_args: revision,
    )
    monkeypatch.setattr(
        factory_learning,
        "_factory_authoritative_revision",
        lambda *_args: revision,
    )
    monkeypatch.setattr(
        factory_learning,
        "_require_factory_release",
        lambda _revision: (_ for _ in ()).throw(
            LearningAuthorityError("database unavailable")
        ),
    )

    assert factory_learning.promoted_corrections(path=path) == []


def test_generated_tool_marker_and_dgm_loader_bind_exact_bytes(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        "maverick.learning_authority.shared_learning_authority_selected",
        lambda: True,
    )
    required = []
    monkeypatch.setattr(
        "maverick.learning_authority.require_learning_release",
        lambda **kwargs: required.append(kwargs),
    )
    source = "def safe_tool(payload):\n    return payload\n"
    generated = tmp_path / "safe_tool.py"
    generated.write_bytes(source.encode("utf-8"))
    digest = hashlib.sha256(generated.read_bytes()).hexdigest()
    intent = self_learning._generated_release_intent(
        tenant="tenant-a", name="safe_tool", digest=digest,
    )
    self_learning._write_generated_release_marker(generated, intent)
    self_learning._require_generated_release(
        path=generated,
        tenant="tenant-a",
        name="safe_tool",
        digest=digest,
    )
    assert required[-1]["digest"] == digest
    with pytest.raises(ValueError, match="marker"):
        self_learning._require_generated_release(
            path=generated,
            tenant="tenant-a",
            name="safe_tool",
            digest=_DIGEST_B,
        )

    pack = tmp_path / "evolved_pack.toml"
    pack.write_bytes(b'name = "evolved_pack"\npersona = "exact"\n')
    pack_digest = hashlib.sha256(pack.read_bytes()).hexdigest()
    desired = SimpleNamespace(
        artifact_kind="dgm_pack",
        desired_digest=pack_digest,
        deployment_id="learning/dgm_pack/evolved_pack",
        promotion_record_id="record-dgm",
        promotion_transaction_id="transaction-dgm",
        previous_digest=None,
    )
    marker = {
        "schema": "lightwork.dgm-pack-release-marker.v1",
        "pack_name": "evolved_pack",
        "digest": pack_digest,
        "candidate_id": _DIGEST_C,
        "archive_sha256": _DIGEST_A,
        "deployment_id": desired.deployment_id,
        "promotion_record_id": desired.promotion_record_id,
        "promotion_transaction_id": desired.promotion_transaction_id,
        "previous_digest": None,
    }
    (tmp_path / ".evolved_pack.dgm-release.json").write_text(
        json.dumps(marker), encoding="utf-8",
    )
    loaded = domain._load_raw_domains(
        tmp_path,
        dgm_authority=object(),
        dgm_desired={"evolved_pack": desired},
    )
    assert loaded["evolved_pack"]["persona"] == "exact"
    assert required[-1]["digest"] == pack_digest

    pack.write_bytes(b'name = "evolved_pack"\npersona = "tampered"\n')
    assert domain._load_raw_domains(
        tmp_path,
        dgm_authority=object(),
        dgm_desired={"evolved_pack": desired},
    ) == {}

    (tmp_path / ".evolved_pack.dgm-release.json").unlink()
    assert domain._load_raw_domains(
        tmp_path,
        dgm_authority=object(),
        dgm_desired={},
    ) == {}
