from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from maverick import agent_bus, authority_policy
from maverick.authority_policy import AuthorityMode, authority_mode
from maverick.bus_handoff import (
    HandoffAuthority,
    InMemoryIssuerProvider,
    IssuerMaterial,
    receive_handoff,
)
from maverick.capability import Capability, capability_enforced, deny_all_capability
from maverick.handoff import NonceCache

pytest.importorskip("cryptography")


@pytest.fixture(autouse=True)
def _clean_bus():
    agent_bus.clear()
    yield
    agent_bus.clear()


def test_community_defaults_to_compatibility():
    assert authority_mode(
        config={}, source_errors={}, enterprise=False,
    ) is AuthorityMode.COMPAT


def test_explicit_strict_mode_is_honored():
    assert authority_mode(
        config={"authority": {"mode": "strict"}},
        source_errors={},
        enterprise=False,
    ) is AuthorityMode.STRICT


def test_enterprise_overrides_explicit_compatibility():
    assert authority_mode(
        config={"authority": {"mode": "compat"}},
        source_errors={},
        enterprise=True,
    ) is AuthorityMode.STRICT


def test_unreadable_policy_fails_closed_to_strict():
    assert authority_mode(
        config={},
        source_errors={"config.toml": "permission denied"},
        enterprise=False,
    ) is AuthorityMode.STRICT


def test_live_authority_policy_uses_only_deployment_global_config(monkeypatch):
    from maverick import config, enterprise

    monkeypatch.setattr(
        config,
        "load_global_config",
        lambda: {"authority": {"mode": "strict"}},
    )
    monkeypatch.setattr(
        config,
        "load_config",
        lambda: {"authority": {"mode": "compat"}},
    )
    monkeypatch.setattr(config, "config_source_errors", lambda **_kwargs: {})
    monkeypatch.setattr(enterprise, "enterprise_enabled", lambda: False)

    assert authority_mode() is AuthorityMode.STRICT


def test_global_enterprise_floor_cannot_be_masked_by_tenant_overlay(monkeypatch):
    from maverick import config, enterprise

    calls = {"global": 0, "tenant": 0, "error_scopes": []}

    def load_global():
        calls["global"] += 1
        return {
            "authority": {"mode": "compat"},
            "enterprise": {"mode": True},
        }

    def load_tenant(*_args, **_kwargs):
        calls["tenant"] += 1
        return {
            "authority": {"mode": "compat"},
            "enterprise": {"mode": False},
        }

    def source_errors(*, include_tenant=True):
        calls["error_scopes"].append(include_tenant)
        return {}

    monkeypatch.setattr(config, "load_global_config", load_global)
    monkeypatch.setattr(config, "load_config", load_tenant)
    monkeypatch.setattr(config, "config_source_errors", source_errors)
    # Prove the authority decision no longer calls the tenant-aware resolver.
    monkeypatch.setattr(enterprise, "enterprise_enabled", lambda: False)

    assert authority_mode() is AuthorityMode.STRICT
    assert calls == {"global": 1, "tenant": 0, "error_scopes": [False]}


@pytest.mark.parametrize(
    "principal", [None, "", "anon", "a2a", "mcp", "grpc", "bearer:digest"],
)
def test_shared_and_synthetic_principals_are_not_individual(principal):
    assert not authority_policy.is_individual_principal(principal)


def test_strict_capabilities_remove_implicit_coordination_floor(monkeypatch):
    cap = Capability(principal="alice", allow_tools=frozenset({"read_file"}))
    monkeypatch.setattr(authority_policy, "strict_authority", lambda: False)
    assert cap.permits("send_to_agent")
    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    assert not cap.permits("send_to_agent")
    assert deny_all_capability("alice").is_deny_all()
    assert not deny_all_capability("alice").permits("delegate_to_agent")


def test_strict_authority_forces_capability_enforcement(monkeypatch):
    from maverick import config, enterprise

    monkeypatch.delenv("MAVERICK_ENFORCE_CAPABILITIES", raising=False)
    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    monkeypatch.setattr(enterprise, "enterprise_enabled", lambda: False)
    monkeypatch.setattr(
        config,
        "load_config",
        lambda: {"capabilities": {"enforce": False}},
    )

    assert capability_enforced() is True


def test_strict_bus_refuses_raw_messages_but_compat_keeps_them(monkeypatch):
    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    assert not agent_bus.send("alice", "bob", {"note": "unsigned"})

    monkeypatch.setattr(authority_policy, "strict_authority", lambda: False)
    assert agent_bus.send("alice", "bob", {"note": "legacy"})
    delivery = receive_handoff(None, "bob")
    assert delivery is not None
    assert delivery.verdict is None
    assert delivery.payload == {"note": "legacy"}


def test_injected_issuer_and_nonce_store_are_shared_and_scope_bound(monkeypatch):
    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    provider = InMemoryIssuerProvider()
    nonce_store = NonceCache()
    first = HandoffAuthority.for_run(
        tenant_id="tenant-a",
        run_id="run-a",
        issuer_provider=provider,
        nonce_store=nonce_store,
    )
    second = HandoffAuthority.for_run(
        tenant_id="tenant-a",
        run_id="run-a",
        issuer_provider=provider,
        nonce_store=nonce_store,
    )
    assert first.issuer_pub_hex == second.issuer_pub_hex
    assert first.nonce_cache is nonce_store
    env = first.mint(
        sender="alice",
        recipient="bob",
        grant=Capability(principal="bob", allow_tools=frozenset({"read_file"})),
        task="read",
    )
    assert second.verify(env, expected_recipient="bob", expected_sender="alice").ok
    replay = first.verify(env, expected_recipient="bob", expected_sender="alice")
    assert not replay.ok and replay.rule == "replay"

    wrong_run = HandoffAuthority(
        issuer_private_hex=first.issuer_private_hex,
        issuer_pub_hex=first.issuer_pub_hex,
        tenant_id="tenant-a",
        run_id="run-b",
        issuer_key_id=first.issuer_key_id,
    )
    fresh = first.mint(
        sender="alice",
        recipient="bob",
        grant=Capability(principal="bob", allow_tools=frozenset({"read_file"})),
        task="read again",
    )
    verdict = wrong_run.verify(
        fresh, expected_recipient="bob", expected_sender="alice",
    )
    assert not verdict.ok and verdict.rule == "run_id_mismatch"

    for override, expected_rule in (
        ({"tenant_id": "tenant-b"}, "tenant_id_mismatch"),
        ({"issuer_key_id": "different-key"}, "issuer_key_id_mismatch"),
    ):
        receiver = HandoffAuthority(
            issuer_private_hex=first.issuer_private_hex,
            issuer_pub_hex=first.issuer_pub_hex,
            tenant_id=override.get("tenant_id", first.tenant_id),
            run_id=first.run_id,
            issuer_key_id=override.get("issuer_key_id", first.issuer_key_id),
        )
        scoped = first.mint(
            sender="alice",
            recipient="bob",
            grant=Capability(
                principal="bob", allow_tools=frozenset({"read_file"}),
            ),
            task="scoped",
        )
        refused = receiver.verify(
            scoped, expected_recipient="bob", expected_sender="alice",
        )
        assert not refused.ok and refused.rule == expected_rule


def test_non_exporting_issuer_signer_mints_verifiable_handoff(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)

    class NonExportingSigner:
        __slots__ = ("_key", "calls")

        def __init__(self):
            self._key = ed25519.Ed25519PrivateKey.generate()
            self.calls = 0

        @property
        def public_hex(self):
            return self._key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            ).hex()

        def sign(self, payload: bytes) -> str:
            self.calls += 1
            return self._key.sign(payload).hex()

    signer = NonExportingSigner()
    public_hex = signer.public_hex

    class Provider:
        def issuer_for(self, *, tenant_id: str, run_id: str) -> IssuerMaterial:
            assert (tenant_id, run_id) == ("tenant-a", "run-a")
            return IssuerMaterial(
                private_hex=None,
                public_hex=public_hex,
                key_id=hashlib.sha256(public_hex.encode("utf-8")).hexdigest(),
                signer=signer,
            )

    authority = HandoffAuthority.for_run(
        tenant_id="tenant-a",
        run_id="run-a",
        issuer_provider=Provider(),
    )
    assert authority.issuer_private_hex is None
    env = authority.mint(
        sender="alice",
        recipient="bob",
        grant=Capability(
            principal="bob", allow_tools=frozenset({"read_file"}),
        ),
        task="read",
    )
    assert signer.calls == 2
    assert authority.verify(
        env, expected_recipient="bob", expected_sender="alice",
    ).ok


@pytest.mark.parametrize("private_hex, has_signer", [(None, False), ("00", True)])
def test_issuer_provider_requires_exactly_one_signing_path(
    private_hex, has_signer,
):
    class Signer:
        @staticmethod
        def sign(_payload: bytes) -> str:
            return "00" * 64

    class Provider:
        @staticmethod
        def issuer_for(*, tenant_id: str, run_id: str) -> IssuerMaterial:
            return IssuerMaterial(
                private_hex=private_hex,
                public_hex="11" * 32,
                key_id="test-key",
                signer=Signer() if has_signer else None,
            )

    with pytest.raises(ValueError, match="exactly one"):
        HandoffAuthority.for_run(issuer_provider=Provider())


def test_envelope_signature_binds_intent(monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    authority = HandoffAuthority.for_run(tenant_id="tenant-a", run_id="run-a")
    env = authority.mint(
        sender="alice",
        recipient="bob",
        grant=Capability(principal="bob", allow_tools=frozenset({"read_file"})),
        task="read",
    )
    verdict = authority.verify(replace(env, intent="request"))
    assert not verdict.ok and verdict.rule == "tampered"


def test_informational_envelope_is_signed_replay_safe_and_deny_all(monkeypatch):
    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    authority = HandoffAuthority.for_run(tenant_id="tenant-a", run_id="run-a")
    env = authority.mint(
        sender="alice",
        recipient="bob",
        grant=deny_all_capability("bob"),
        task="information",
        body='{"finding":"safe"}',
        intent="message",
    )
    assert agent_bus.send("alice", "bob", env)
    assert agent_bus.send("alice", "bob", env)
    first = receive_handoff(authority, "bob")
    second = receive_handoff(authority, "bob")
    assert first is not None and first.ok
    assert first.grant is not None and first.grant.is_deny_all()
    assert not first.grant.permits("send_to_agent")
    assert second is not None and not second.ok and second.verdict.rule == "replay"


def test_informational_envelope_cannot_smuggle_authority(monkeypatch):
    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    authority = HandoffAuthority.for_run(tenant_id="tenant-a", run_id="run-a")
    env = authority.mint(
        sender="alice",
        recipient="bob",
        grant=Capability(principal="bob", allow_tools=frozenset({"shell"})),
        task="information",
        intent="message",
    )
    assert agent_bus.send("alice", "bob", env)
    delivery = receive_handoff(authority, "bob")
    assert delivery is not None and not delivery.ok
    assert delivery.verdict.rule == "informational_authority"


def test_strict_send_tool_installs_no_authority_for_information(monkeypatch):
    from maverick.tools.agent_bus_tool import recv_from_agent, send_to_agent

    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    authority = HandoffAuthority.for_run(tenant_id="tenant-a", run_id="run-a")

    class _Context:
        goal_id = 9
        bus_namespace = "run-a"
        handoff_authority = authority
        quarantine = None
        shield = SimpleNamespace(
            scan_input=lambda _text: SimpleNamespace(allowed=True),
        )

        @staticmethod
        def knows_bus_agent(agent_id):
            return agent_id in {"alice", "bob"}

    ctx = _Context()
    ambient = Capability(principal="bob", allow_tools=frozenset({"shell"}))
    agent = SimpleNamespace(
        name="bob", ctx=ctx, _handoff_capability=ambient,
    )
    sent = send_to_agent("alice", ctx=ctx).fn({
        "to_id": "bob", "payload": {"finding": "safe"},
    })
    assert "signed effect-free" in sent
    received = asyncio.run(recv_from_agent("bob", agent=agent, ctx=ctx).fn({}))
    assert "no execution authority" in received
    assert agent._handoff_capability is ambient


def test_a2a_strict_rejects_shared_and_accepts_individual(monkeypatch):
    from maverick import a2a_tasks, agent_trust
    from maverick.agent_trust import TrustedAgent

    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    monkeypatch.setenv("MAVERICK_A2A_TOKEN", "shared-token")
    registry = {}
    monkeypatch.setattr(agent_trust, "load_trust_state", lambda: (True, registry))
    monkeypatch.setattr(
        a2a_tasks,
        "_a2a_trust_block",
        lambda _principal, **_kwargs: None,
    )
    monkeypatch.setattr(
        agent_trust,
        "agent_for_a2a_token",
        lambda _token, **_kwargs: None,
    )
    engine = a2a_tasks.TaskEngine()
    error = engine.auth_error("Bearer shared-token")
    assert error is not None and "individual" in error["message"]

    caller = TrustedAgent(id="alice", a2a_token="alice-token")
    registry["alice"] = caller
    monkeypatch.setattr(
        agent_trust,
        "agent_for_a2a_token",
        lambda token, **_kwargs: caller if token == "alice-token" else None,
    )
    assert engine.auth_error("Bearer alice-token") is None


def test_grpc_strict_rejects_shared_and_accepts_individual(monkeypatch):
    from maverick import agent_trust
    from maverick.agent_trust import TrustedAgent
    from maverick.grpc_api import server

    class _Codes:
        UNAUTHENTICATED = "unauthenticated"
        RESOURCE_EXHAUSTED = "exhausted"

    class _Context:
        def __init__(self, token):
            self.token = token

        def invocation_metadata(self):
            return (("authorization", f"Bearer {self.token}"),)

        def peer(self):
            return "test"

        def abort(self, code, reason):
            raise PermissionError(f"{code}: {reason}")

    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    monkeypatch.setattr(server, "_grpc_code", lambda: _Codes)
    monkeypatch.setattr(server, "_grpc_rate_ok", lambda _key: True)
    with pytest.raises(PermissionError, match="individual gRPC"):
        server._authorize_caller(_Context("shared"), "shared")

    alice = TrustedAgent(id="alice", grpc_token="alice-token")
    monkeypatch.setattr(
        agent_trust,
        "agent_for_token",
        lambda token, surface: alice
        if (token, surface) == ("alice-token", "grpc") else None,
    )
    assert server._authorize_caller(_Context("alice-token"), "shared") is alice


def test_federation_strict_requires_a_pinned_signed_identity(monkeypatch, tmp_path):
    from maverick import federation
    from maverick.agent_trust import TrustedAgent
    from maverick.federation import FederationService, Peer

    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    monkeypatch.setenv("MAVERICK_FEDERATION_REQUIRE_SIGNED", "0")
    assert federation.require_signed()
    service = FederationService(
        node="b",
        peers=[Peer("a", "a:1", "token")],
        local_grant=None,
        goal_service=object(),
        record=lambda *_args, **_kwargs: None,
        state_path=tmp_path / "federation.db",
    )
    reason = service._verify_signed(
        Peer("a", "a:1", "token"),
        TrustedAgent(id="a"),
        {},
        {"a": TrustedAgent(id="a")},
        True,
    )
    assert reason is not None and "pinned key" in reason
