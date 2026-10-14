"""Cross-replica handoff issuer and replay authority regressions."""
from __future__ import annotations

import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from maverick import bus_handoff, governed_records
from maverick.bus_handoff import (
    GovernedIssuerProvider,
    GovernedNonceStore,
    HandoffAuthority,
    HandoffAuthorityError,
    InMemoryIssuerProvider,
)
from maverick.capability import Capability
from maverick.handoff import NonceCache

pytest.importorskip("cryptography")


class _SharedDocuments:
    """Atomic test double for separate AuthorityDocumentStore instances."""

    def __init__(self) -> None:
        self.backend_kind = "postgres"
        self.documents: dict[tuple[str, str, str], dict] = {}
        self.lock = threading.Lock()
        self.clock = time.time()

    def store(self, key: tuple[str, str, str]):
        shared = self

        class _Store:
            @property
            def backend_kind(self):
                return shared.backend_kind

            def authoritative_time(self):
                return shared.clock

            def read(self):
                with shared.lock:
                    return copy.deepcopy(shared.documents.get(key, {}))

            def mutate(
                self,
                callback,
                *,
                action,
                actor="system",
                max_retries=16,
            ):
                del action, actor, max_retries
                with shared.lock:
                    document = copy.deepcopy(shared.documents.get(key, {}))
                    result = callback(document)
                    shared.documents[key] = copy.deepcopy(document)
                    return result

            def mutate_with_authoritative_time(
                self,
                callback,
                *,
                action,
                actor="system",
                max_retries=16,
            ):
                del action, actor, max_retries
                with shared.lock:
                    document = copy.deepcopy(shared.documents.get(key, {}))
                    result = callback(document, shared.clock)
                    shared.documents[key] = copy.deepcopy(document)
                    return result

        return _Store()

    def factory(self, family: str, *, tenant_id: str, run_id: str):
        return self.store((family, tenant_id, run_id))


class _FailingStore:
    backend_kind = "postgres"

    @staticmethod
    def authoritative_time():
        return time.time()

    def read(self):
        raise OSError("database unavailable")

    def mutate(self, *_args, **_kwargs):
        raise OSError("database unavailable")


def _grant() -> Capability:
    return Capability(
        principal="bob",
        allow_tools=frozenset({"read_file"}),
    )


def test_governed_issuer_converges_under_provider_contention():
    shared = _SharedDocuments()
    barrier = threading.Barrier(12)

    def resolve(_index: int):
        provider = GovernedIssuerProvider(
            store_factory=lambda tenant, run: shared.store(
                ("issuer", tenant, run)
            )
        )
        barrier.wait()
        return provider.issuer_for(tenant_id="tenant-a", run_id="run-a")

    with ThreadPoolExecutor(max_workers=12) as pool:
        materials = list(pool.map(resolve, range(12)))

    assert len({material.private_hex for material in materials}) == 1
    assert len({material.public_hex for material in materials}) == 1
    assert len({material.key_id for material in materials}) == 1
    document = shared.documents[("issuer", "tenant-a", "run-a")]
    assert document["private_hex"] == materials[0].private_hex
    assert document["public_hex"] == materials[0].public_hex
    assert document["key_id"] == materials[0].key_id


def test_governed_nonce_claim_is_atomic_across_replicas():
    shared = _SharedDocuments()
    shared.clock = 100.0
    first = GovernedNonceStore(
        tenant_id="tenant-a",
        run_id="run-a",
        store=shared.store(("nonces", "tenant-a", "run-a")),
    )
    second = GovernedNonceStore(
        tenant_id="tenant-a",
        run_id="run-a",
        store=shared.store(("nonces", "tenant-a", "run-a")),
    )
    barrier = threading.Barrier(16)

    def claim(index: int) -> bool:
        barrier.wait()
        store = first if index % 2 else second
        return store.remember("single-use", now=100.0, expires_at=200.0)

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(claim, range(16)))

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 15
    shared.clock = 150.0
    assert first.seen("single-use", now=150.0)
    assert second.seen("single-use", now=150.0)


def test_governed_nonce_store_is_bounded_and_prunes_expired_claims():
    shared = _SharedDocuments()
    shared.clock = 10.0
    store = GovernedNonceStore(
        tenant_id="tenant-a",
        run_id="run-a",
        max_entries=2,
        store=shared.store(("nonces", "tenant-a", "run-a")),
    )

    assert store.remember("one", now=10.0, expires_at=11.0)
    assert store.remember("two", now=10.0, expires_at=20.0)
    assert not store.remember("three", now=10.0, expires_at=30.0)
    shared.clock = 12.0
    assert store.remember("three", now=12.0, expires_at=30.0)

    document = shared.documents[("nonces", "tenant-a", "run-a")]
    assert document["nonces"] == {"two": 20.0, "three": 30.0}
    assert not store.seen("one", now=12.0)


def test_governed_nonce_uses_authority_time_and_rechecks_lost_cas():
    class _RetryingStore:
        backend_kind = "postgres"

        def __init__(self):
            self.clock = 100.0
            self.document = {}

        def authoritative_time(self):
            return self.clock

        def read(self):
            return copy.deepcopy(self.document)

        def mutate_with_authoritative_time(self, callback, **_kwargs):
            # First decision loses its optimistic write.  The retry crosses the
            # signed envelope's expiry and must not commit the stale admission.
            callback(copy.deepcopy(self.document), self.clock)
            self.clock = 201.0
            candidate = copy.deepcopy(self.document)
            result = callback(candidate, self.clock)
            self.document = candidate
            return result

    backend = _RetryingStore()
    store = GovernedNonceStore(
        tenant_id="tenant-a",
        run_id="run-a",
        store=backend,
    )

    assert not store.remember("expires-during-race", now=-10_000, expires_at=200)
    assert backend.document == {}

    backend.clock = 150.0
    backend.document = {
        "max_entries": bus_handoff.HANDOFF_MAX_NONCES,
        "nonces": {"still-live": 200.0},
        "run_id": "run-a",
        "tenant_id": "tenant-a",
    }
    # A hostile/skewed caller timestamp cannot prune a fleet claim early.
    assert store.seen("still-live", now=10_000.0)


def test_postgres_defaults_converge_and_reject_cross_replica_replay(monkeypatch):
    shared = _SharedDocuments()
    monkeypatch.setattr(
        governed_records,
        "configured_governed_records_backend",
        lambda: "postgres",
    )
    monkeypatch.setattr(
        bus_handoff,
        "_new_authority_document_store",
        shared.factory,
    )

    first = HandoffAuthority.for_run(tenant_id="tenant-a", run_id="run-a")
    second = HandoffAuthority.for_run(tenant_id="tenant-a", run_id="run-a")

    assert isinstance(first.nonce_cache, GovernedNonceStore)
    assert isinstance(second.nonce_cache, GovernedNonceStore)
    assert first.issuer_private_hex == second.issuer_private_hex
    assert first.issuer_pub_hex == second.issuer_pub_hex
    assert first.issuer_key_id == second.issuer_key_id

    envelope = first.mint(
        sender="alice",
        recipient="bob",
        grant=_grant(),
        task="read",
    )
    barrier = threading.Barrier(2)

    def verify(authority: HandoffAuthority):
        barrier.wait()
        return authority.verify(
            envelope,
            expected_recipient="bob",
            expected_sender="alice",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        verdicts = list(pool.map(verify, (first, second)))

    assert sum(verdict.ok for verdict in verdicts) == 1
    assert {verdict.rule for verdict in verdicts} == {"ok", "replay"}


def test_postgres_handoff_mint_and_verify_ignore_replica_wall_clock(monkeypatch):
    shared = _SharedDocuments()
    shared.clock = 1_800_000_000.0
    monkeypatch.setattr(
        governed_records,
        "configured_governed_records_backend",
        lambda: "postgres",
    )
    monkeypatch.setattr(bus_handoff, "_new_authority_document_store", shared.factory)
    # Neither the sender's nor receiver's node clock is in the shared authority
    # domain.  A large skew must not make replicas disagree about freshness.
    monkeypatch.setattr("maverick.handoff.time.time", lambda: 1.0)

    sender = HandoffAuthority.for_run(tenant_id="tenant-a", run_id="run-a")
    receiver = HandoffAuthority.for_run(tenant_id="tenant-a", run_id="run-a")
    envelope = sender.mint(
        sender="alice",
        recipient="bob",
        grant=_grant(),
        task="read",
    )
    assert envelope.ts == shared.clock

    shared.clock += 1.0
    verdict = receiver.verify(
        envelope,
        now=1.0,
        expected_recipient="bob",
        expected_sender="alice",
    )
    assert verdict.ok


def test_governed_scope_separates_issuers_and_nonce_claims():
    shared = _SharedDocuments()
    shared.clock = 1.0
    provider = GovernedIssuerProvider(
        store_factory=lambda tenant, run: shared.store(("issuer", tenant, run))
    )
    run_a = provider.issuer_for(tenant_id="tenant-a", run_id="run-a")
    run_b = provider.issuer_for(tenant_id="tenant-a", run_id="run-b")
    assert run_a.public_hex != run_b.public_hex

    nonce_a = GovernedNonceStore(
        tenant_id="tenant-a",
        run_id="run-a",
        store=shared.store(("nonces", "tenant-a", "run-a")),
    )
    nonce_b = GovernedNonceStore(
        tenant_id="tenant-a",
        run_id="run-b",
        store=shared.store(("nonces", "tenant-a", "run-b")),
    )
    assert nonce_a.remember("same-nonce", now=1.0, expires_at=2.0)
    assert nonce_b.remember("same-nonce", now=1.0, expires_at=2.0)


@pytest.mark.parametrize("corruption", ["public_hex", "key_id"])
def test_governed_issuer_rejects_inconsistent_key_material(corruption):
    shared = _SharedDocuments()
    local = InMemoryIssuerProvider().issuer_for(
        tenant_id="tenant-a",
        run_id="run-a",
    )
    document = {
        "algorithm": "ed25519",
        "key_id": local.key_id,
        "private_hex": local.private_hex,
        "public_hex": local.public_hex,
        "run_id": "run-a",
        "tenant_id": "tenant-a",
    }
    document[corruption] = "00" * 32
    shared.documents[("issuer", "tenant-a", "run-a")] = document
    provider = GovernedIssuerProvider(
        store_factory=lambda tenant, run: shared.store(("issuer", tenant, run))
    )

    with pytest.raises(HandoffAuthorityError, match="not match"):
        provider.issuer_for(tenant_id="tenant-a", run_id="run-a")


def test_shared_replay_failure_denies_a_valid_handoff():
    provider = InMemoryIssuerProvider()
    nonce_store = GovernedNonceStore(
        tenant_id="tenant-a",
        run_id="run-a",
        store=_FailingStore(),
    )
    authority = HandoffAuthority.for_run(
        tenant_id="tenant-a",
        run_id="run-a",
        issuer_provider=provider,
        nonce_store=nonce_store,
    )
    envelope = authority.mint(
        sender="alice",
        recipient="bob",
        grant=_grant(),
        task="read",
    )

    verdict = authority.verify(
        envelope,
        expected_recipient="bob",
        expected_sender="alice",
    )

    assert not verdict.ok
    assert verdict.rule == "authority_unavailable"


def test_postgres_issuer_failure_never_falls_back_to_in_memory(monkeypatch):
    shared = _SharedDocuments()
    local_calls = 0

    def local_issuer(self, *, tenant_id, run_id):
        del self, tenant_id, run_id
        nonlocal local_calls
        local_calls += 1
        raise AssertionError("local issuer fallback")

    def factory(family: str, *, tenant_id: str, run_id: str):
        if family == "issuer":
            return _FailingStore()
        return shared.factory(family, tenant_id=tenant_id, run_id=run_id)

    monkeypatch.setattr(
        governed_records,
        "configured_governed_records_backend",
        lambda: "postgres",
    )
    monkeypatch.setattr(bus_handoff, "_new_authority_document_store", factory)
    monkeypatch.setattr(InMemoryIssuerProvider, "issuer_for", local_issuer)

    with pytest.raises(HandoffAuthorityError, match="issuer authority"):
        HandoffAuthority.for_run(tenant_id="tenant-a", run_id="run-a")
    assert local_calls == 0


def test_explicit_injections_do_not_consult_default_backend(monkeypatch):
    provider = InMemoryIssuerProvider()
    nonces = NonceCache()

    def unexpected_selection():
        raise AssertionError("explicit authority should bypass default selection")

    monkeypatch.setattr(
        governed_records,
        "configured_governed_records_backend",
        unexpected_selection,
    )
    authority = HandoffAuthority.for_run(
        tenant_id="tenant-a",
        run_id="run-a",
        issuer_provider=provider,
        nonce_store=nonces,
    )

    assert authority.nonce_cache is nonces
    assert authority.issuer_pub_hex == provider.issuer_for(
        tenant_id="tenant-a",
        run_id="run-a",
    ).public_hex


def test_governed_nonce_refuses_backend_drift_to_local():
    shared = _SharedDocuments()
    shared.clock = 1.0
    store = GovernedNonceStore(
        tenant_id="tenant-a",
        run_id="run-a",
        store=shared.store(("nonces", "tenant-a", "run-a")),
    )
    assert store.remember("one", now=1.0, expires_at=2.0)

    shared.backend_kind = "local"
    with pytest.raises(HandoffAuthorityError, match="requires the Postgres"):
        store.seen("one", now=1.5)
