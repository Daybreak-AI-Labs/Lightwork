from __future__ import annotations

import hashlib
import json
import time

import pytest
from maverick.authority_state import (
    KNOWN_STORE_NAMES,
    REPORT_SCHEMA,
    AuthorityStateNotReady,
    AuthorityStateRegistry,
    StoreSpec,
    default_registry,
    readiness_report,
    require_ready,
    service_declarations,
)


@pytest.fixture(autouse=True)
def _clear_authority_env(monkeypatch):
    for name in (
        "MAVERICK_ENTERPRISE",
        "MAVERICK_PG_DSN",
        "MAVERICK_QUEUE_NAMESPACE",
        "MAVERICK_QUEUE_REDIS_DSN",
        "MAVERICK_QUEUE_SIGNING_KEY",
        "MAVERICK_REPLICA_COUNT",
        "MAVERICK_WORLD_BACKEND",
        "MAVERICK_GOVERNED_RECORDS_BACKEND",
        "MAVERICK_APPROVER_KEYS",
        "MAVERICK_APPROVER_KEYS_DIGEST",
        "MAVERICK_APPROVER_KEYS_DIR",
        "MAVERICK_EVENT_TRIGGERS",
        "MAVERICK_EKKO",
        "MAVERICK_ENCRYPT_AT_REST",
        "MAVERICK_ENCRYPTION_KEY",
        "MAVERICK_ENCRYPTION_KEY_DIGEST",
        "MAVERICK_SECURE_DEFAULT",
        "MAVERICK_DASHBOARD_INVITES",
        "MAVERICK_DASHBOARD_ADMINS",
        "MAVERICK_DASHBOARD_REQUIRE_AUTH",
        "MAVERICK_DASHBOARD_SESSION_SECRET",
        "MAVERICK_OIDC_AUTHORIZATION_ENDPOINT",
        "MAVERICK_OIDC_CLIENT_ID",
        "MAVERICK_OIDC_ENABLED",
        "MAVERICK_OIDC_ISSUER",
        "MAVERICK_OIDC_SESSION_SECRET",
        "MAVERICK_OIDC_TOKEN_ENDPOINT",
        "MAVERICK_REQUIRE_SIGNED_APPROVAL",
        "MAVERICK_SCIM_TOKEN",
        "MAVERICK_SELF_IMPROVEMENT",
        "MAVERICK_SUPPLY_CHAIN_PINNING",
        "MAVERICK_TENANT_BY_USER",
    ):
        monkeypatch.delenv(name, raising=False)


def _shared_config() -> dict:
    return {
        "world_model": {
            "backend": "postgres",
            "dsn": "postgresql://db-user:db-secret@postgres.internal/lightwork",  # pragma: allowlist secret
        },
        "queue": {
            "backend": "arq",
            "redis_dsn": "rediss://queue-user:redis-secret@redis.internal/0",  # pragma: allowlist secret
            "namespace": "production",
            "signing_key": "queue-signing-secret-with-32-bytes",
        },
        "audit": {
            "worm": {
                "provider": "s3",
                "bucket": "lightwork-audit",
                "mode": "COMPLIANCE",
            }
        },
        "self_harness": {
            "holdout_authority_id": "primary-study",
        },
        "encryption": {
            "at_rest": True,
        },
    }


def _healthy_probes():
    return {
        "postgres": lambda: (True, "mock Postgres transaction passed"),
        "redis": lambda: True,
        "audit_object_store": lambda: {
            "available": True,
            "detail": "mock object-lock retention check passed",
        },
    }


def _all_shared_registry() -> AuthorityStateRegistry:
    return AuthorityStateRegistry.from_specs((
        StoreSpec(
            "policy_authority",
            "authority",
            "shared",
            service="postgres",
            source="test",
        ),
        StoreSpec(
            "dispatch_authority",
            "authority",
            "shared",
            service="redis",
            source="test",
        ),
        StoreSpec(
            "audit_evidence",
            "evidence",
            "shared",
            service="audit_object_store",
            source="test",
        ),
    ))


def test_local_authority_blocks_enterprise_even_when_services_pass():
    registry = _all_shared_registry()
    registry.register(StoreSpec("local_policy", "authority", "local"))

    report = readiness_report(
        registry=registry,
        probes=_healthy_probes(),
        config=_shared_config(),
        config_errors={},
        enterprise=True,
        replica_count=1,
    )

    assert report["ready"] is False
    assert report["local_authorities"] == ["local_policy"]
    assert "local_authority_stores" in {
        blocker["code"] for blocker in report["blockers"]
    }


def test_discovered_unknown_store_blocks_strict_readiness():
    registry = _all_shared_registry()
    registry.discover("future_mutable_store")

    report = readiness_report(
        registry=registry,
        probes=_healthy_probes(),
        config=_shared_config(),
        config_errors={},
        enterprise=True,
        replica_count=1,
    )

    assert report["ready"] is False
    assert report["unregistered_stores"] == ["future_mutable_store"]
    assert "unregistered_stores" in {
        blocker["code"] for blocker in report["blockers"]
    }


def test_all_shared_mocked_dependencies_pass_and_report_is_machine_readable():
    report = readiness_report(
        registry=_all_shared_registry(),
        probes=_healthy_probes(),
        config=_shared_config(),
        config_errors={},
        enterprise=True,
        replica_count=3,
    )

    assert report["schema"] == REPORT_SCHEMA
    assert report["ready"] is True
    assert report["ha_eligible"] is True
    assert report["enforcement_reasons"] == ["enterprise", "replica_count>1"]
    assert report["blockers"] == []
    assert all(row["available"] for row in report["services"].values())
    json.dumps(report)


def test_declared_services_are_not_claimed_available_without_live_probes():
    report = readiness_report(
        registry=_all_shared_registry(),
        config=_shared_config(),
        config_errors={},
        enterprise=True,
        replica_count=1,
    )

    assert report["ready"] is False
    assert all(row["declared"] for row in report["services"].values())
    assert not any(row["probed"] for row in report["services"].values())
    assert not any(row["available"] for row in report["services"].values())
    assert "shared_services_unavailable" in {
        blocker["code"] for blocker in report["blockers"]
    }


def test_unreadable_config_fails_closed_in_enterprise():
    report = readiness_report(
        registry=_all_shared_registry(),
        probes=_healthy_probes(),
        config=_shared_config(),
        config_errors={"C:/restricted/config.toml": "permission denied"},
        enterprise=True,
        replica_count=1,
    )

    assert report["ready"] is False
    assert report["config_healthy"] is False
    assert "config_unreadable" in {
        blocker["code"] for blocker in report["blockers"]
    }


def test_single_replica_community_admission_remains_compatible():
    report = readiness_report(
        config={},
        config_errors={},
        enterprise=False,
        replica_count=1,
    )

    assert report["ready"] is True
    assert report["admission_ready"] is True
    assert report["enforcement_required"] is False
    assert report["ha_eligible"] is False
    assert report["local_authorities"]


def test_default_inventory_registers_every_known_consistency_family():
    registry = default_registry({})

    active = {spec.name for spec in registry.stores}
    inactive = {spec.name for spec in registry.inactive_stores}

    assert active.isdisjoint(inactive)
    assert active | inactive == KNOWN_STORE_NAMES
    assert registry.unregistered_stores == ()
    assert inactive == {
        "approval_signing_keys",
        "deployment_encryption_key",
        "evaluator_slot_epochs",
        "learning_rollout_authority",
        "public_form_tenant_routing",
        "supply_chain_pins",
    }
    access = next(spec for spec in registry.stores if spec.name == "access_policy")
    assert access.classification == "derived"
    assert access.content_digest and access.content_digest.startswith("sha256:")


def test_active_high_risk_authority_blind_spots_are_registered_and_local():
    by_name = {spec.name: spec for spec in default_registry({}).stores}

    for name in (
        "internal_agent_bus_delivery",
        "handoff_nonce_claims",
        "federation_ingress_claims",
        "oidc_transaction_claims",
        "audit_signing_keys",
        "audit_append_authority",
        "verifier_artifact_activation",
        "holdout_query_budget",
        "calibration_freeze_state",
    ):
        assert by_name[name].classification == "authority"
        assert by_name[name].backend == "local"


def test_inactive_evidence_cannot_mask_an_independently_discovered_store():
    from maverick.authority_state import InactiveStoreSpec

    registry = AuthorityStateRegistry(expected_stores=["observed_store"])
    with pytest.raises(ValueError, match="discovered"):
        registry.mark_inactive(InactiveStoreSpec(
            name="observed_store",
            source="scanner",
            evidence="feature appears disabled",
        ))

    discovered = default_registry({}, expected_stores=["supply_chain_pins"])
    supply = next(
        spec for spec in discovered.stores if spec.name == "supply_chain_pins"
    )
    assert supply.classification == "authority" and supply.backend == "local"
    assert "supply_chain_pins" not in {
        spec.name for spec in discovered.inactive_stores
    }


def test_absent_builtin_learning_backends_are_evidence_backed_and_reactivate(
    monkeypatch,
):
    from maverick import evaluator_evolution, learning_rollout

    registry = default_registry({})
    inactive = {spec.name: spec.evidence for spec in registry.inactive_stores}
    assert "persistent slot registry" in inactive["evaluator_slot_epochs"]
    assert "deploy/rollback backends" in inactive["learning_rollout_authority"]

    monkeypatch.setattr(
        evaluator_evolution, "deployment_slot_authority_available", lambda: True,
    )
    monkeypatch.setattr(
        learning_rollout, "deployment_authority_available", lambda: True,
    )
    active = {spec.name: spec for spec in default_registry({}).stores}
    assert active["evaluator_slot_epochs"].classification == "authority"
    assert active["evaluator_slot_epochs"].backend == "local"
    assert active["learning_rollout_authority"].classification == "authority"
    assert active["learning_rollout_authority"].backend == "local"


@pytest.mark.parametrize(
    ("value", "active"),
    [(False, False), (True, True), ("not-a-boolean", True)],
)
def test_supply_chain_authority_exists_only_when_not_provably_disabled(
    value, active,
):
    registry = default_registry({"safety": {"supply_chain_pinning": value}})
    stores = {spec.name: spec for spec in registry.stores}
    inactive_names = {spec.name for spec in registry.inactive_stores}
    assert ("supply_chain_pins" in stores) is active
    assert ("supply_chain_pins" in inactive_names) is (not active)
    if active:
        assert stores["supply_chain_pins"].classification == "authority"
        assert stores["supply_chain_pins"].backend == "local"


@pytest.mark.parametrize(
    ("triggers", "by_user", "active"),
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (True, True, True),
        ("invalid", True, True),
    ],
)
def test_public_form_routing_is_active_only_when_it_may_receive_multitenant_forms(
    triggers, by_user, active,
):
    registry = default_registry({
        "event_triggers": {"enable": triggers},
        "tenancy": {"by_user": by_user},
    })
    stores = {spec.name: spec for spec in registry.stores}
    assert ("public_form_tenant_routing" in stores) is active
    if active:
        routing = stores["public_form_tenant_routing"]
        assert routing.classification == "authority"
        assert routing.backend == "local"


def test_approval_trust_set_needs_exact_deployment_digest_without_leaking_keys():
    from maverick.approval_signing import approver_keyset_digest

    public_key = "11" * 32
    digest = approver_keyset_digest([public_key])
    base = {
        "self_improvement": {
            "approver_keys": [public_key],
            "require_signed_approval": True,
        },
    }

    unpinned = default_registry(base)
    unpinned_spec = next(
        spec for spec in unpinned.stores if spec.name == "approval_signing_keys"
    )
    assert unpinned_spec.classification == "immutable"
    assert unpinned_spec.content_digest == digest
    assert "not configured" in unpinned_spec.readiness_issue
    report = readiness_report(
        config=base,
        config_errors={},
        enterprise=True,
        replica_count=1,
    )
    assert "deployment_inputs_unverified" in {
        blocker["code"] for blocker in report["blockers"]
    }
    assert public_key not in json.dumps(report)

    pinned_config = {
        "self_improvement": {
            **base["self_improvement"],
            "approver_keys_digest": digest,
        },
    }
    pinned = default_registry(pinned_config)
    pinned_spec = next(
        spec for spec in pinned.stores if spec.name == "approval_signing_keys"
    )
    assert pinned_spec.classification == "immutable"
    assert pinned_spec.readiness_issue == ""
    assert "digest verified" in pinned_spec.detail

    mismatched = {
        "self_improvement": {
            **base["self_improvement"],
            "approver_keys_digest": "sha256:" + "0" * 64,
        },
    }
    mismatch_spec = next(
        spec
        for spec in default_registry(mismatched).stores
        if spec.name == "approval_signing_keys"
    )
    assert "do not match" in mismatch_spec.readiness_issue


def test_approval_key_family_is_inactive_when_no_signing_trust_source_exists():
    registry = default_registry({"self_improvement": {"enable": True}})
    assert "approval_signing_keys" not in {spec.name for spec in registry.stores}
    evidence = {
        spec.name: spec.evidence for spec in registry.inactive_stores
    }
    assert "no configured trust source" in evidence["approval_signing_keys"]


def test_approval_require_env_false_omits_config_only_require_flag(monkeypatch):
    monkeypatch.setenv("MAVERICK_REQUIRE_SIGNED_APPROVAL", "0")
    registry = default_registry({
        "self_improvement": {"require_signed_approval": True},
    })

    assert "approval_signing_keys" not in {spec.name for spec in registry.stores}


def test_postgres_world_model_does_not_mask_other_local_authorities():
    report = readiness_report(
        probes=_healthy_probes(),
        config=_shared_config(),
        config_errors={},
        enterprise=True,
        replica_count=1,
    )

    world = next(item for item in report["stores"] if item["name"] == "world_model")
    assert world["backend"] == "shared"
    assert world["service"] == "postgres"
    assert report["ready"] is False
    assert next(
        item for item in report["stores"]
        if item["name"] == "flow_control_plane"
    )["backend"] == "shared"
    assert "internal_agent_bus_delivery" in report["local_authorities"]


def test_postgres_promotes_only_the_new_families_that_really_share_it(monkeypatch):
    from maverick import authority_state

    policy_digest = "sha256:" + "1" * 64
    secret_digest = "sha256:" + "2" * 64  # pragma: allowlist secret
    monkeypatch.setattr(
        authority_state,
        "_identity_federation_proof",
        lambda _config: (policy_digest, secret_digest, ""),
    )
    registry = default_registry(_shared_config())
    by_name = {spec.name: spec for spec in registry.stores}

    for name in (
        "a2a_task_claims",
        "automation_job_queue",
        "effect_execution_receipts",
        "flow_control_plane",
        "flow_release_objects",
        "kill_switch",
        "oidc_transaction_claims",
        "issue_webhook_replay_claims",
        "self_improvement_promotion",
        "platform_hunt_records",
        "environment_hunt_records",
        "hunter_response_coordination",
        "audit_append_authority",
        "capability_revocations",
        "agent_trust_registry",
        "runtime_overrides",
        "handoff_nonce_claims",
        "handoff_issuer_state",
        "saml_replay_claims",
        "federation_ingress_claims",
        "insight_exchange_replay_claims",
        "tool_token_replay_claims",
        "marketplace_federation_state",
        "adapter_activation",
        "dashboard_settings",
        "configuration_proposals",
        "fleet_agent_roster",
        "mcp_registry_overlay",
        "form_submissions",
        "role_overrides",
        "budget_tuning_policy",
        "self_learning_state",
        "verifier_artifact_activation",
        "verifier_confirmation_claims",
        "factory_correction_activation",
        "dgm_pack_activation",
        "calibration_freeze_state",
        "holdout_query_budget",
        "security_governance_records",
        "privacy_governance_records",
        "cra_product_security_records",
        "model_risk_assurance_records",
        "production_assurance_campaigns",
        "production_assurance_drills",
        "agent_trust_declarations",
        "agent_trust_observations",
        "evidence_graph",
        "dashboard_tenant_roles",
        "dashboard_invitations",
        "session_revocations",
        "dashboard_users_roles",
        "oidc_subject_directory",
        "scim_directory",
        "suite_grants",
    ):
        assert by_name[name].backend == "shared"
        assert by_name[name].service == "postgres"

    # The exported trust-pack attestation remains an independent local file;
    # database-backed observations/declarations must not mask that residual.
    assert by_name["agent_trust_attestations"].backend == "local"
    # Harness corpus/holdout material is a separate conditional family. The
    # learned-tool admission ledger and artifact activation pointers above are
    # shared without pretending the node-local artifact bytes are shared.
    assert by_name["self_harness_learning_state"].backend == "local"
    assert by_name["dashboard_users_roles"].content_digest == policy_digest
    assert by_name["oidc_subject_directory"].content_digest == policy_digest
    assert by_name["suite_grants"].content_digest == policy_digest
    assert by_name["scim_directory"].content_digest == secret_digest
    # Artifact bytes and activation projections remain node-local, but they
    # cannot authorize serving without the shared desired release and exact
    # healthy node observation.
    assert by_name["adapter_activation"].backend == "shared"
    # Submission persistence is shared. The anonymous token-routing family is
    # inactive while either hosted-form triggers or tenant-by-user are off.
    assert by_name["form_submissions"].backend == "shared"
    assert by_name["budget_tuning_policy"].backend == "shared"
    assert "deterministic governed CAS" in by_name["budget_tuning_policy"].detail
    assert "public_form_tenant_routing" not in by_name
    assert "public_form_tenant_routing" in {
        spec.name for spec in registry.inactive_stores
    }


def test_trigger_registries_follow_only_governed_postgres_authority():
    shared = {
        spec.name: spec for spec in default_registry(_shared_config()).stores
    }
    for name in ("event_trigger_registry", "saas_trigger_registry"):
        assert shared[name].backend == "shared"
        assert shared[name].service == "postgres"
        assert "tenant-scoped governed CAS" in shared[name].detail

    local_config = _shared_config()
    local_config["governed_records"] = {"backend": "local"}
    local = {
        spec.name: spec for spec in default_registry(local_config).stores
    }
    for name in ("event_trigger_registry", "saas_trigger_registry"):
        assert local[name].backend == "local"
        assert local[name].service is None


def test_public_form_routing_promotes_only_with_governed_multitenant_ingress():
    config = _shared_config()
    config["event_triggers"] = {"enable": True}
    config["tenancy"] = {"by_user": True}

    routing = {
        spec.name: spec for spec in default_registry(config).stores
    }["public_form_tenant_routing"]

    assert routing.backend == "shared"
    assert routing.service == "postgres"
    assert "epoch-bound submission claims" in routing.detail


@pytest.mark.parametrize(
    ("env_name", "section", "runtime_result"),
    [
        (
            "MAVERICK_GOVERNED_RECORDS_BACKEND",
            {"governed_records": {"backend": "postgres"}},
            "invalid_backend",
        ),
        (
            "MAVERICK_WORLD_BACKEND",
            {"governed_records": {"backend": "auto"}},
            "local",
        ),
        (
            "MAVERICK_PG_DSN",
            {"governed_records": {"backend": "postgres"}},
            "missing_dsn",
        ),
    ],
)
def test_governed_postgres_declaration_matches_whitespace_runtime_selection(
    env_name, section, runtime_result, monkeypatch,
):
    from maverick import authority_state, config, governed_records

    selected = _shared_config()
    selected.update(section)
    monkeypatch.setenv(env_name, " ")
    monkeypatch.setattr(config, "load_governed_records_config", lambda: selected)
    monkeypatch.setattr(
        config,
        "governed_records_config_source_errors",
        dict,
    )

    assert authority_state._governed_records_postgres_declared(selected) is False
    if runtime_result == "local":
        assert governed_records._selection().kind == "local"
    elif runtime_result == "invalid_backend":
        with pytest.raises(
            governed_records.GovernedRecordBackendError,
            match="backend must be",
        ):
            governed_records._selection()
    else:
        with pytest.raises(
            governed_records.GovernedRecordBackendError,
            match="configured DSN",
        ):
            governed_records._selection()


def test_shared_encryption_key_identity_is_exact_redacted_deployment_input(
    monkeypatch,
):
    from maverick import crypto_at_rest

    config = _shared_config()
    missing = {
        spec.name: spec for spec in default_registry(config).stores
    }["deployment_encryption_key"]
    assert missing.classification == "immutable"
    assert missing.backend == "local"
    assert missing.content_digest is None
    assert missing.readiness_issue

    digest = "sha256:" + "a" * 64
    monkeypatch.setattr(
        crypto_at_rest,
        "require_shared_deployment_key_identity",
        lambda: digest,
    )
    proven = {
        spec.name: spec for spec in default_registry(config).stores
    }["deployment_encryption_key"]
    assert proven.content_digest == digest
    assert proven.readiness_issue == ""


def test_shared_encryption_key_proof_cannot_mask_disabled_at_rest_policy(
    monkeypatch,
):
    from maverick import crypto_at_rest

    config = _shared_config()
    config["encryption"] = {"at_rest": False}
    monkeypatch.setattr(
        crypto_at_rest,
        "require_shared_deployment_key_identity",
        lambda: "sha256:" + "a" * 64,
    )

    spec = {
        row.name: row for row in default_registry(config).stores
    }["deployment_encryption_key"]

    assert spec.content_digest is None
    assert "disabled or unverified" in spec.readiness_issue


def test_shared_encryption_key_failure_is_redacted_and_blocks_ha(monkeypatch):
    from maverick import crypto_at_rest

    secret = "do-not-render-this-key-material"  # pragma: allowlist secret

    def _reject():
        raise RuntimeError(secret)

    monkeypatch.setattr(
        crypto_at_rest,
        "require_shared_deployment_key_identity",
        _reject,
    )
    report = readiness_report(
        probes=_healthy_probes(),
        config=_shared_config(),
        config_errors={},
        enterprise=True,
        replica_count=2,
    )
    blocker = next(
        item for item in report["blockers"]
        if item["code"] == "deployment_inputs_unverified"
    )
    assert "deployment_encryption_key" in blocker["stores"]
    assert secret not in json.dumps(report)


def test_identity_families_stay_local_and_block_when_full_proof_fails(monkeypatch):
    from maverick import authority_state

    monkeypatch.setattr(
        authority_state,
        "_identity_federation_proof",
        lambda _config: (None, None, "shared identity contract could not be bound"),
    )
    report = readiness_report(
        probes=_healthy_probes(),
        config=_shared_config(),
        config_errors={},
        enterprise=True,
        replica_count=2,
    )
    identity_names = {
        "dashboard_users_roles",
        "oidc_subject_directory",
        "scim_directory",
        "suite_grants",
    }
    rows = {row["name"]: row for row in report["stores"]}
    assert all(rows[name]["backend"] == "local" for name in identity_names)
    assert all(rows[name]["content_digest"] is None for name in identity_names)
    assert all(rows[name]["readiness_issue"] for name in identity_names)
    deployment_blocker = next(
        blocker
        for blocker in report["blockers"]
        if blocker["code"] == "deployment_inputs_unverified"
    )
    assert identity_names.issubset(deployment_blocker["stores"])


def test_identity_federation_proof_returns_only_canonical_nonsecret_digests(
    monkeypatch,
):
    from maverick import authority_state, identity_federation

    secret = "this-secret-must-not-enter-readiness-output"  # pragma: allowlist secret
    policy_digest = "sha256:" + "a" * 64
    secret_digest = "sha256:" + "b" * 64  # pragma: allowlist secret
    monkeypatch.setenv("MAVERICK_SCIM_TOKEN", secret)
    monkeypatch.setattr(
        identity_federation,
        "bind_deployment_contract",
        lambda _config: (policy_digest, secret_digest),
    )

    proof = authority_state._identity_federation_proof(_shared_config())

    assert proof == (policy_digest, secret_digest, "")
    assert secret not in json.dumps(proof)


def test_shared_local_invite_signer_is_bound_during_registry_build(monkeypatch):
    from maverick import identity_authority

    secret = "configured-dashboard-session-key-32-bytes"  # pragma: allowlist secret
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    calls = []
    monkeypatch.setattr(
        identity_authority,
        "bind_session_signing_key",
        lambda value: calls.append(value) or digest,
    )
    config = _shared_config()
    config["dashboard"] = {"invites": True, "session_secret": secret}

    by_name = {spec.name: spec for spec in default_registry(config).stores}
    invitations = by_name["dashboard_invitations"]

    assert calls == [secret]
    assert invitations.backend == "shared"
    assert invitations.service == "postgres"
    assert invitations.content_digest == f"sha256:{digest}"
    assert invitations.readiness_issue == ""
    assert secret not in json.dumps(invitations.__dict__)


def test_shared_local_invite_signer_missing_or_invalid_blocks_ha(monkeypatch):
    from maverick import identity_authority

    monkeypatch.setattr(
        identity_authority,
        "bind_session_signing_key",
        lambda _value: pytest.fail("invalid keys must not reach shared storage"),
    )
    config = _shared_config()
    config["dashboard"] = {"invites": True, "session_secret": "short"}  # pragma: allowlist secret

    report = readiness_report(
        probes=_healthy_probes(),
        config=config,
        config_errors={},
        enterprise=True,
        replica_count=2,
    )

    invitation = next(
        row for row in report["stores"]
        if row["name"] == "dashboard_invitations"
    )
    blocker = next(
        row for row in report["blockers"]
        if row["code"] == "deployment_inputs_unverified"
    )
    assert invitation["backend"] == "shared"
    assert invitation["readiness_issue"]
    assert "dashboard_invitations" in blocker["stores"]
    assert "short" not in json.dumps(report)


def test_shared_local_invite_signer_skew_is_redacted(monkeypatch):
    from maverick import identity_authority

    secret = "replica-specific-dashboard-session-key"  # pragma: allowlist secret
    monkeypatch.setenv("MAVERICK_DASHBOARD_INVITES", "1")
    monkeypatch.setenv("MAVERICK_DASHBOARD_SESSION_SECRET", secret)

    def _reject(_value):
        raise RuntimeError(f"key skew for {secret}")

    monkeypatch.setattr(identity_authority, "bind_session_signing_key", _reject)

    report = readiness_report(
        probes=_healthy_probes(),
        config=_shared_config(),
        config_errors={},
        enterprise=True,
        replica_count=2,
    )

    invitation = next(
        row for row in report["stores"]
        if row["name"] == "dashboard_invitations"
    )
    assert invitation["readiness_issue"]
    assert invitation["content_digest"] is None
    assert secret not in json.dumps(report)


def test_oidc_browser_login_does_not_require_local_invite_signer(monkeypatch):
    from maverick import identity_authority

    monkeypatch.setattr(
        identity_authority,
        "bind_session_signing_key",
        lambda _value: pytest.fail("OIDC login does not mint local invite sessions"),
    )
    config = _shared_config()
    config["dashboard"] = {"invites": True}
    config["auth"] = {
        "oidc": {
            "enabled": True,
            "client_id": "dashboard-client",
            "session_secret": "oidc-cookie-secret",  # pragma: allowlist secret
            "issuer": "https://identity.example.test",
        },
    }

    invitation = next(
        spec for spec in default_registry(config).stores
        if spec.name == "dashboard_invitations"
    )

    assert invitation.backend == "shared"
    assert invitation.content_digest is None
    assert invitation.readiness_issue == ""


@pytest.mark.parametrize(
    ("configured", "detail"),
    [
        (None, "is missing"),
        (" unsafe", "is invalid"),
        ("a/b", "is invalid"),
    ],
)
def test_shared_holdout_inventory_requires_a_valid_stable_authority_id(
    configured, detail,
):
    config = _shared_config()
    if configured is None:
        del config["self_harness"]["holdout_authority_id"]
    else:
        config["self_harness"]["holdout_authority_id"] = configured

    by_name = {spec.name: spec for spec in default_registry(config).stores}
    report = readiness_report(
        probes=_healthy_probes(),
        config=config,
        config_errors={},
        enterprise=True,
        replica_count=2,
    )

    assert by_name["holdout_query_budget"].backend == "local"
    blocker = next(
        item for item in report["blockers"]
        if item["code"] == "holdout_authority_id_invalid"
    )
    assert blocker["stores"] == ["holdout_query_budget"]
    assert detail in blocker["detail"]
    if configured is not None:
        assert configured not in json.dumps(blocker)


def test_valid_shared_holdout_inventory_reports_stable_identity_without_value():
    config = _shared_config()

    by_name = {spec.name: spec for spec in default_registry(config).stores}
    holdout_spec = by_name["holdout_query_budget"]

    assert holdout_spec.backend == "shared"
    assert holdout_spec.service == "postgres"
    assert "stable authority id configured" in holdout_spec.detail
    assert config["self_harness"]["holdout_authority_id"] not in holdout_spec.detail


def test_explicit_world_harness_store_promotes_its_independent_shared_subfamily():
    config = _shared_config()
    config["self_harness"] = {"store": "world"}
    by_name = {spec.name: spec for spec in default_registry(config).stores}

    assert by_name["self_harness_learning_state"].backend == "shared"
    assert by_name["self_harness_learning_state"].service == "postgres"
    assert by_name["self_learning_state"].backend == "shared"


def test_governed_record_local_override_cannot_be_misreported_as_shared():
    config = _shared_config()
    config["governed_records"] = {"backend": "local"}
    by_name = {spec.name: spec for spec in default_registry(config).stores}

    assert by_name["world_model"].backend == "shared"
    assert by_name["a2a_task_claims"].backend == "shared"
    for name in (
        "capability_revocations",
        "agent_trust_registry",
        "runtime_overrides",
        "handoff_nonce_claims",
        "handoff_issuer_state",
        "saml_replay_claims",
        "federation_ingress_claims",
        "insight_exchange_replay_claims",
        "tool_token_replay_claims",
        "marketplace_federation_state",
        "adapter_activation",
        "dashboard_settings",
        "configuration_proposals",
        "fleet_agent_roster",
        "mcp_registry_overlay",
        "budget_tuning_policy",
        "self_learning_state",
        "verifier_artifact_activation",
        "verifier_confirmation_claims",
        "factory_correction_activation",
        "dgm_pack_activation",
        "security_governance_records",
        "privacy_governance_records",
        "cra_product_security_records",
        "model_risk_assurance_records",
        "production_assurance_campaigns",
        "agent_trust_declarations",
        "evidence_graph",
    ):
        assert by_name[name].backend == "local"


def test_replica_count_above_one_enforces_even_outside_enterprise():
    report = readiness_report(
        config={},
        config_errors={},
        enterprise=False,
        replica_count=2,
    )

    assert report["ready"] is False
    assert report["enforcement_reasons"] == ["replica_count>1"]
    assert "local_authority_stores" in {
        blocker["code"] for blocker in report["blockers"]
    }


def test_invalid_replica_count_and_probe_exception_fail_closed_without_secrets():
    config = _shared_config()
    secret_values = (
        "db-secret",
        "redis-secret",
        "queue-signing-secret-with-32-bytes",
    )
    report = readiness_report(
        registry=_all_shared_registry(),
        probes={
            "postgres": lambda: (_ for _ in ()).throw(
                RuntimeError("dsn contains db-secret")
            ),
            "redis": lambda: True,
            "audit_object_store": lambda: True,
        },
        config=config,
        config_errors={},
        enterprise=False,
        replica_count="many",
    )

    assert report["ready"] is False
    assert "replica_count_invalid" in {
        blocker["code"] for blocker in report["blockers"]
    }
    rendered = json.dumps(report)
    assert all(secret not in rendered for secret in secret_values)
    assert "RuntimeError" in rendered


@pytest.mark.parametrize(
    "postgres_dsn, redis_dsn",
    [
        ("postgresql://user:secret@localhost/db", "rediss://user:secret@redis.internal/0"),  # pragma: allowlist secret
        ("postgresql://user:secret@postgres.internal/db", "rediss://user:secret@127.0.0.1/0"),  # pragma: allowlist secret
        ("postgresql://user:secret@[::1]/db", "rediss://user:secret@redis.internal/0"),  # pragma: allowlist secret
    ],
)
def test_loopback_services_do_not_count_as_replica_shared(
    postgres_dsn, redis_dsn,
):
    config = _shared_config()
    config["world_model"]["dsn"] = postgres_dsn
    config["queue"]["redis_dsn"] = redis_dsn

    declarations = service_declarations(config)

    if "postgres.internal" not in postgres_dsn:
        assert declarations["postgres"].declared is False
    if "redis.internal" not in redis_dsn:
        assert declarations["redis"].declared is False


def test_probe_timeout_is_bounded_and_provider_detail_is_not_retained():
    def _slow_secret_probe():
        time.sleep(1.0)
        return True, "postgresql://user:probe-secret@postgres.internal/db"  # pragma: allowlist secret

    started = time.perf_counter()
    report = readiness_report(
        registry=_all_shared_registry(),
        probes={
            "postgres": _slow_secret_probe,
            "redis": lambda: (True, "redis-secret-should-not-appear"),
            "audit_object_store": lambda: {
                "available": True,
                "detail": "object-secret-should-not-appear",
            },
        },
        config=_shared_config(),
        config_errors={},
        enterprise=True,
        replica_count=2,
        probe_timeout_seconds=0.05,
    )

    assert time.perf_counter() - started < 0.8
    assert report["ready"] is False
    assert report["services"]["postgres"]["detail"] == "live probe timed out"
    rendered = json.dumps(report)
    assert "probe-secret" not in rendered
    assert "redis-secret" not in rendered
    assert "object-secret" not in rendered


def test_require_ready_raises_with_the_machine_report():
    with pytest.raises(AuthorityStateNotReady) as raised:
        require_ready(
            config={},
            config_errors={},
            enterprise=True,
            replica_count=1,
        )

    assert raised.value.report["ready"] is False


def test_existing_startup_gate_enforces_multi_replica_authority(monkeypatch):
    from maverick.deployment import EnterpriseRequiredError, require_enterprise_or_die

    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "2")
    monkeypatch.setattr("maverick.config.load_global_config", dict)
    monkeypatch.setattr(
        "maverick.config.config_source_errors",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr("maverick.enterprise.enterprise_enabled", lambda: False)

    with pytest.raises(EnterpriseRequiredError, match="authority-state preflight"):
        require_enterprise_or_die(force=False)


def test_readiness_global_enterprise_cannot_be_masked_by_tenant_overlay(monkeypatch):
    from maverick import config, enterprise

    calls = {"global": 0, "tenant": 0, "error_scopes": []}

    def load_global():
        calls["global"] += 1
        return {"enterprise": {"mode": True}}

    def load_tenant(*_args, **_kwargs):
        calls["tenant"] += 1
        return {"enterprise": {"mode": False}}

    def source_errors(*, include_tenant=True):
        calls["error_scopes"].append(include_tenant)
        return {}

    monkeypatch.setattr(config, "load_global_config", load_global)
    monkeypatch.setattr(config, "load_config", load_tenant)
    monkeypatch.setattr(config, "config_source_errors", source_errors)
    # Prove readiness no longer delegates to the tenant-aware resolver.
    monkeypatch.setattr(enterprise, "enterprise_enabled", lambda: False)

    report = readiness_report(replica_count=1)

    assert report["enterprise"] is True
    assert "enterprise" in report["enforcement_reasons"]
    assert report["ready"] is False
    assert calls == {"global": 1, "tenant": 0, "error_scopes": [False]}
