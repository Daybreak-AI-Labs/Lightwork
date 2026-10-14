"""Manifest-excluded features are absent from explicit production mode."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_MANIFEST = REPO_ROOT / "release" / "production-surface-manifest.json"


@pytest.fixture(autouse=True)
def _production(tmp_path, monkeypatch):
    from maverick import config

    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")
    monkeypatch.setenv("MAVERICK_PRODUCTION_MANIFEST", str(PRODUCTION_MANIFEST))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    for name in (
        "MAVERICK_A2A_ENABLED",
        "MAVERICK_ALERTS",
        "MAVERICK_ALLOW_INSECURE_QUEUE_REDIS",
        "MAVERICK_ALLOW_MCP_ACQUISITION",
        "MAVERICK_AUTOMATION_IMPORT",
        "MAVERICK_DREAMING",
        "MAVERICK_EVENT_TRIGGERS",
        "MAVERICK_FACTORY_LEARNING",
        "MAVERICK_FLOWS_AUTO",
        "MAVERICK_GOVERNED_CONNECTORS",
        "MAVERICK_KNOWLEDGE_ENABLED",
        "MAVERICK_LICENSE_API",
        "MAVERICK_NTFY_TOPIC",
        "MAVERICK_NOTIFY_BATCH_MAX",
        "MAVERICK_NOTIFY_BATCH_WINDOW",
        "MAVERICK_OTEL_ENDPOINT",
        "MAVERICK_OTEL_EXPORTER",
        "MAVERICK_OTEL_HEADERS",
        "MAVERICK_PLUGINS_ALLOW",
        "MAVERICK_PROMETHEUS_ADDR",
        "MAVERICK_PROMETHEUS_PORT",
        "MAVERICK_QUEUE_NAMESPACE",
        "MAVERICK_QUEUE_REDIS_CA_CERTS",
        "MAVERICK_QUEUE_REDIS_CERTFILE",
        "MAVERICK_QUEUE_REDIS_DSN",
        "MAVERICK_QUEUE_REDIS_KEYFILE",
        "MAVERICK_QUEUE_SIGNING_KEY",
        "MAVERICK_REFLEXION",
        "MAVERICK_SELF_HARNESS",
        "MAVERICK_SELF_IMPROVEMENT",
        "MAVERICK_SELF_LEARNING",
        "MAVERICK_SELF_MODIFY",
        "MAVERICK_SENTRY_DSN",
        "MAVERICK_SENTRY_TRACES_SAMPLE_RATE",
        "DISCORD_NOTIFY_WEBHOOK_URL",
        "PUSHOVER_APP_TOKEN",
        "PUSHOVER_USER_KEY",
        "SLACK_NOTIFY_WEBHOOK_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def test_production_defaults_force_learning_and_dgm_loops_off() -> None:
    from maverick import (
        dreaming,
        factory_learning,
        reflexion,
        self_harness,
        self_improvement,
        self_learning,
        self_modify,
    )
    from maverick.production_exclusions import production_configuration_violations

    assert production_configuration_violations(
        {}, include_runtime_overrides=False
    ) == ()
    assert not self_learning.enabled()
    assert not self_improvement.enabled()
    assert not self_harness.enabled()
    assert not dreaming.enabled()
    assert not reflexion.enabled()
    assert not factory_learning.enabled()
    assert not self_modify.enabled()
    assert self_harness.recall_addendum("model") == ""


@pytest.mark.parametrize(
    ("config", "surface"),
    [
        ({"self_learning": {"enable": True}}, "feature:self-learning-ledger"),
        ({"self_improvement": {"enable": True}}, "feature:self-learning-ledger"),
        ({"self_harness": {"enable": True}}, "feature:self-learning-ledger"),
        ({"dreaming": {"enable": True}}, "feature:self-learning-ledger"),
        ({"reflexion": {"enable": True}}, "feature:self-learning-ledger"),
        (
            {"self_improvement": {"factory_learning": True}},
            "feature:self-learning-ledger",
        ),
        ({"knowledge": {"enable": True}}, "knowledge/RAG"),
        ({"channels": {"slack": {"enabled": True}}}, "messaging channels"),
        ({"governed_connectors": {"enable": True}}, "connectors"),
        ({"event_triggers": {"enable": True}}, "automation sources"),
        ({"automation_import": {"enable": True}}, "automation sources"),
        ({"plugins": {"enabled": ["example"]}}, "plugins"),
        (
            {"mcp_servers": {"example": {"command": "example"}}},
            "MCP servers",
        ),
        ({"a2a": {"enabled": True}}, "A2A HTTP"),
        ({"self_modify": {"enable": True}}, "feature:code-dgm"),
        ({"adapter_rung": {"enable": True}}, "feature:weights-dgm"),
        ({"flows": {"auto_evolve": True}}, "flow evolution"),
        ({"flows": {"auto_apply": True}}, "flow auto-apply"),
        (
            {"hooks": [{"event": "PreToolUse", "command": "powershell -c whoami"}]},
            "configured shell hooks",
        ),
        (
            {"evidence_graph": {"enable": True}},
            "governed-record product suites",
        ),
        (
            {"model_risk_assurance": {"enable": True}},
            "governed-record product suites",
        ),
        (
            {"evidence_gateway": {"enable": True}},
            "governed-record product suites",
        ),
        (
            {"finance_operations": {"enable": True}},
            "governed-record product suites",
        ),
        (
            {"env_hunt": {"response_execution": True}},
            "consequential environment-response execution",
        ),
        (
            {"security_ops": {"enable": True}},
            "security operations",
        ),
        (
            {"privacy_ops": {"enable": True}},
            "privacy operations",
        ),
        (
            {"voice": {"dashboard_commands": True}},
            "dashboard voice commands",
        ),
        (
            {"skills": {"builtin": True}},
            "runtime or catalog skills",
        ),
        (
            {"license": {"api_url": "https://vendor.example.test/license"}},
            "vendor-console polling",
        ),
        (
            {"grpc_dispatch": {"target": "worker.example.test:50051"}},
            "gRPC remote-worker dispatch",
        ),
        (
            {"queue": {"backend": "arq", "redis_dsn": "rediss://redis.test/0"}},
            "ARQ/Redis remote-worker dispatch",
        ),
        (
            {"notifications": {"backend": "slack"}},
            "notification/webhook egress",
        ),
        (
            {"notifications": {"discord_webhook": "https://example.test/hook"}},
            "notification/webhook egress",
        ),
        (
            {"alerts": {"enabled": True}},
            "notification/webhook egress",
        ),
        (
            {"observability": {"sentry_dsn": "https://secret@example.test/1"}},
            "external telemetry exporters/listeners",
        ),
        (
            {"retention": {"audit_days": 90, "events_days": 30}},
            "automated destructive retention",
        ),
    ],
)
def test_production_rejects_explicit_opt_in(config: dict, surface: str) -> None:
    from maverick.production_exclusions import validate_production_configuration

    with pytest.raises(Exception, match=surface):
        validate_production_configuration(
            config, include_runtime_overrides=False
        )


def test_optional_packages_and_protocols_are_blocked_before_discovery(tmp_path) -> None:
    from maverick import a2a, attachments, plugins
    from maverick.mcp_client import MCPServerSpec, start_mcp_clients
    from maverick.production_exclusions import ProductionFeatureExcludedError

    with patch("importlib.metadata.entry_points") as entry_points:
        assert plugins._entry_points("maverick.tools") == []
        entry_points.assert_not_called()
    assert plugins.discover_tools() == []
    assert plugins.installed_plugins() == {
        "tools": [],
        "channels": [],
        "skills": [],
        "personas": [],
    }
    assert attachments._extract_document(tmp_path / "document.docx") is None
    assert not a2a.a2a_enabled()
    with pytest.raises(ProductionFeatureExcludedError, match="interface:a2a-http"):
        a2a.build_agent_card()
    spec = MCPServerSpec(name="blocked", command=sys.executable)
    with pytest.raises(ProductionFeatureExcludedError, match="interface:mcp"):
        asyncio.run(start_mcp_clients([spec]))


def test_attachment_entrypoints_fail_before_metadata_or_byte_io(
    tmp_path, monkeypatch,
) -> None:
    from maverick import attachments
    from maverick.production_exclusions import ProductionFeatureExcludedError
    from maverick.server import Server
    from maverick.tools.attachments import list_attachments_tool

    class UnreadableWorld:
        def list_attachments(self, _goal_id):
            raise AssertionError("attachment metadata was read")

    world = UnreadableWorld()
    root = tmp_path / "attachment-bytes"

    with pytest.raises(ProductionFeatureExcludedError, match="feature:attachments"):
        attachments.store(1, "legacy.txt", "text/plain", b"legacy", root=root)
    assert not root.exists()

    monkeypatch.setattr(
        attachments,
        "s3_mirror_enabled",
        lambda: (_ for _ in ()).throw(AssertionError("S3 policy was inspected")),
    )
    with pytest.raises(ProductionFeatureExcludedError, match="feature:attachments"):
        attachments.s3_fetch(1, "0" * 16 + "-legacy.txt", root=root)
    with pytest.raises(ProductionFeatureExcludedError, match="feature:attachments"):
        attachments.content_blocks_for_goal(world, 1, model="anthropic:test")
    with pytest.raises(ProductionFeatureExcludedError, match="feature:attachments"):
        attachments.generate_companions(world, 1, root=root)
    with pytest.raises(ProductionFeatureExcludedError, match="feature:attachments"):
        list_attachments_tool(world, 1).fn({})
    with pytest.raises(ProductionFeatureExcludedError, match="feature:attachments"):
        Server._store_inbound_attachments(world, 1, [])


def test_direct_learning_channel_and_weights_entrypoints_fail_before_work() -> None:
    from maverick import adapter_rung, dreaming, runtime_overrides, server
    from maverick.production_exclusions import ProductionFeatureExcludedError

    with pytest.raises(ProductionFeatureExcludedError, match="self-learning-ledger"):
        dreaming.dream_cycle()
    with pytest.raises(ProductionFeatureExcludedError, match="messaging-channels"):
        server.build_from_config()
    with pytest.raises(ProductionFeatureExcludedError, match="third-party-plugins"):
        runtime_overrides.enable_plugin("example")
    with pytest.raises(ProductionFeatureExcludedError, match="mcp-stdio"):
        runtime_overrides.add_mcp_server("example", {"command": "example"})
    with pytest.raises(ProductionFeatureExcludedError, match="weights-dgm"):
        adapter_rung.get_trainer("dummy")
    with pytest.raises(ProductionFeatureExcludedError, match="weights-dgm"):
        adapter_rung.evaluate_adapter(
            "base", "candidate", ["a", "b"], score_fn=lambda _model, _cases: {}
        )
    with pytest.raises(ProductionFeatureExcludedError, match="weights-dgm"):
        adapter_rung.govern_adapter_change(
            Path("missing"), [], score_fn=lambda _model, _cases: {}
        )


def test_production_cli_does_not_advertise_excluded_commands() -> None:
    import click
    from click.testing import CliRunner
    from maverick.cli import main

    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    advertised = set(main.list_commands(click.Context(main)))
    for command in (
        "connectors",
        "demo",
        "dream",
        "factory-learn",
        "hindsight",
        "insights-export",
        "insights-import",
        "knowledge",
        "learn-demo",
        "learned",
        "mcp",
        "mcp-registry",
        "model-improvement",
        "plugin",
        "queue",
        "reward-model",
        "schedule",
        "self-harness",
        "self-modify",
        "serve",
        "worker",
    ):
        assert command not in advertised
    blocked = CliRunner().invoke(main, ["plugin"])
    assert blocked.exit_code == 2
    assert "No such command" in blocked.output


def test_every_cli_command_has_an_explicit_production_classification() -> None:
    import click
    from maverick.cli import main
    from maverick.production_exclusions import cli_command_classification

    root_ctx = click.Context(main, info_name="maverick")
    raw = click.Group.list_commands(main, root_ctx)
    assert raw

    unclassified: list[str] = []

    def inspect_group(group, ctx, prefix: tuple[str, ...] = ()) -> None:
        for name in click.Group.list_commands(group, ctx):
            path = " ".join((*prefix, name))
            if cli_command_classification(path) is None:
                unclassified.append(path)
            command = click.Group.get_command(group, ctx, name)
            if isinstance(command, click.Group):
                inspect_group(
                    command,
                    click.Context(command, info_name=name, parent=ctx),
                    (*prefix, name),
                )

    inspect_group(main, root_ctx)
    assert unclassified == []
    assert cli_command_classification("future-unreviewed-command") is None
    assert main.get_command(
        click.Context(main), "future-unreviewed-command"
    ) is None


def test_cli_policy_registries_are_disjoint() -> None:
    from maverick import production_exclusions as policy

    core = set(policy._PRODUCTION_CORE_CLI_COMMANDS)
    features = set(policy._CLI_FEATURES)
    unsupported = set(policy._PRODUCTION_UNSUPPORTED_CLI_COMMANDS)
    assert core.isdisjoint(features)
    assert core.isdisjoint(unsupported)
    assert features.isdisjoint(unsupported)

    nested_core = set(policy._PRODUCTION_CORE_CLI_SUBCOMMANDS)
    nested_unsupported = set(policy._PRODUCTION_UNSUPPORTED_CLI_SUBCOMMANDS)
    assert nested_core.isdisjoint(nested_unsupported)


def test_production_nested_cli_policy_is_default_deny() -> None:
    from click.testing import CliRunner
    from maverick.cli import main

    runner = CliRunner()
    tenant_help = runner.invoke(main, ["tenant", "--help"])
    assert tenant_help.exit_code == 0, tenant_help.output
    assert "postgres-export" in tenant_help.output
    assert "suspend" in tenant_help.output
    assert "create" not in tenant_help.output
    assert "delete" not in tenant_help.output
    assert "kms-rotate" not in tenant_help.output
    assert "quota" not in tenant_help.output
    assert "resume" not in tenant_help.output
    for command in ("create", "quota", "resume"):
        blocked = runner.invoke(main, ["tenant", command])
        assert blocked.exit_code == 2
        assert "No such command" in blocked.output
    blocked_delete = runner.invoke(main, ["tenant", "delete", "example", "--yes"])
    assert blocked_delete.exit_code == 2
    assert "No such command" in blocked_delete.output
    blocked_kms_rotate = runner.invoke(main, ["tenant", "kms-rotate", "--dry-run"])
    assert blocked_kms_rotate.exit_code == 2
    assert "No such command" in blocked_kms_rotate.output

    audit_help = runner.invoke(main, ["audit", "--help"])
    assert audit_help.exit_code == 0, audit_help.output
    assert "export" in audit_help.output
    assert "rotate-key" not in audit_help.output
    blocked_audit_rotate = runner.invoke(main, ["audit", "rotate-key"])
    assert blocked_audit_rotate.exit_code == 2
    assert "No such command" in blocked_audit_rotate.output

    capability_help = runner.invoke(main, ["capability", "--help"])
    assert capability_help.exit_code == 0, capability_help.output
    assert "revocations" in capability_help.output
    assert "revoke" in capability_help.output
    assert "unrevoke" not in capability_help.output
    blocked_unrevoke = runner.invoke(
        main, ["capability", "unrevoke", "agent:example"]
    )
    assert blocked_unrevoke.exit_code == 2
    assert "No such command" in blocked_unrevoke.output

    encryption_help = runner.invoke(main, ["encryption", "--help"])
    assert encryption_help.exit_code == 0, encryption_help.output
    assert "migrate-postgres" in encryption_help.output
    assert "backup-key" not in encryption_help.output
    assert "  migrate " not in encryption_help.output
    assert "  rotate " not in encryption_help.output

    record_help = runner.invoke(main, ["record", "--help"])
    assert record_help.exit_code == 0, record_help.output
    assert "verify" in record_help.output
    assert "export" not in record_help.output
    assert runner.invoke(main, ["record", "export", "out.json"]).exit_code == 2


@pytest.mark.parametrize("source", ["base", "dashboard", "operator", "tenant"])
def test_production_rejects_malformed_active_config_source(
    source: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from maverick import config
    from maverick.production_exclusions import (
        ProductionConfigurationError,
        validate_production_configuration,
    )

    if source == "base":
        path = config.config_path()
    elif source == "dashboard":
        path = config.dashboard_overrides_path()
    elif source == "operator":
        path = tmp_path / "operator-overlay.toml"
        monkeypatch.setenv(config.CONFIG_OVERLAY_ENV, str(path))
    else:
        monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
        path = config.tenant_config_path()
        assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[malformed\n", encoding="utf-8")
    config.reset_config_cache()
    config.load_config()

    with pytest.raises(ProductionConfigurationError, match=path.name):
        validate_production_configuration()


def test_production_rejects_scim_secret_configuration(monkeypatch) -> None:
    from maverick.production_exclusions import production_configuration_violations

    monkeypatch.setenv("MAVERICK_SCIM_TOKEN", "mounted-secret-reference")
    assert "SCIM identity provisioning is configured" in (
        production_configuration_violations(
            {}, include_runtime_overrides=False
        )
    )


def test_governed_record_products_fail_before_backend_selection(monkeypatch) -> None:
    from maverick import governed_records
    from maverick.governed_records import GovernedRecordStore
    from maverick.production_exclusions import ProductionFeatureExcludedError

    monkeypatch.setattr(
        governed_records,
        "_selection",
        lambda: (_ for _ in ()).throw(AssertionError("backend was selected")),
    )
    store = GovernedRecordStore("blocked", "BLK", "blocked_record")
    with pytest.raises(
        ProductionFeatureExcludedError,
        match="feature:governed-record-products",
    ):
        _ = store.backend_kind


def test_direct_excluded_egress_entrypoints_refuse_before_side_effects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from maverick import (
        backup,
        entitlements,
        grpc_dispatcher,
        notification_batcher,
        notifications,
        run_share,
    )
    from maverick.production_exclusions import ProductionFeatureExcludedError

    def unexpected(*_args, **_kwargs):
        raise AssertionError("excluded production surface performed side-effect setup")

    monkeypatch.setattr(run_share, "tempfile", unexpected, raising=False)
    with pytest.raises(ProductionFeatureExcludedError, match="generated-connectors"):
        run_share.build_gist_payload(1)
    with pytest.raises(ProductionFeatureExcludedError, match="generated-connectors"):
        run_share.share_run(1, token="must-not-be-used")

    monkeypatch.setattr(backup, "_client_root", unexpected)
    monkeypatch.setattr(backup, "_verified_archive", unexpected)
    with pytest.raises(backup.BackupError, match="production recovery surface"):
        backup.create_backup(tmp_path / "must-not-exist.tgz")
    with pytest.raises(backup.BackupError, match="production recovery surface"):
        backup.restore_backup(tmp_path / "missing.tgz")
    assert not (tmp_path / "must-not-exist.tgz").exists()

    monkeypatch.setattr(entitlements, "_license_cfg", unexpected)
    with pytest.raises(ProductionFeatureExcludedError, match="vendor-console"):
        entitlements.refresh_from_server("https://vendor.example.test")
    with pytest.raises(ProductionFeatureExcludedError, match="vendor-console"):
        entitlements._http_get_json("https://vendor.example.test", None, 1.0)
    assert entitlements.refresh_interval_seconds() == 0.0
    assert entitlements.start_refresher(interval=0.01, refresh=unexpected) is None

    monkeypatch.setattr(grpc_dispatcher, "_cfg", unexpected)
    with pytest.raises(ProductionFeatureExcludedError, match="grpc-worker-dispatch"):
        grpc_dispatcher.GrpcDispatcher("worker.example.test:50051")
    assert grpc_dispatcher.install_from_config() is False

    monkeypatch.setattr(notifications, "_load_config", unexpected)
    monkeypatch.setattr(notifications, "_get_executor", unexpected)
    with pytest.raises(ProductionFeatureExcludedError, match="notification-egress"):
        notifications.notify(
            "secret",
            backends=["ntfy"],
            async_dispatch=False,
            batch=False,
        )
    with pytest.raises(ProductionFeatureExcludedError, match="notification-egress"):
        notifications.notify("secret", backends=["slack"], async_dispatch=True)
    monkeypatch.setattr(notification_batcher, "policy_from_config", unexpected)
    monkeypatch.setattr(notification_batcher, "_start_flusher", unexpected)
    with pytest.raises(ProductionFeatureExcludedError, match="notification-egress"):
        notification_batcher.shared()


def test_network_queue_paths_refuse_before_config_import_or_broker_setup(
    monkeypatch,
) -> None:
    import importlib

    from maverick import queue_dispatcher
    from maverick.production_exclusions import ProductionFeatureExcludedError

    def unexpected(*_args, **_kwargs):
        raise AssertionError("excluded queue inspected config or broker state")

    monkeypatch.setattr(queue_dispatcher, "_require_network_signing_key", unexpected)
    monkeypatch.setattr(
        queue_dispatcher, "_require_network_claim_store_configured", unexpected
    )
    with pytest.raises(ProductionFeatureExcludedError, match="arq-worker-dispatch"):
        queue_dispatcher.QueueDispatcher(lambda *_args: None, transport="network")
    with pytest.raises(ProductionFeatureExcludedError, match="arq-worker-dispatch"):
        queue_dispatcher.arq_enqueue()
    with pytest.raises(ProductionFeatureExcludedError, match="arq-worker-dispatch"):
        queue_dispatcher.run_queued_goal({"auth_mode": "shared-hmac"})
    assert queue_dispatcher.install_from_config() is False

    sys.modules.pop("maverick.arq_worker", None)
    with pytest.raises(ProductionFeatureExcludedError, match="arq-worker-dispatch"):
        importlib.import_module("maverick.arq_worker")


def test_support_bundle_requires_durable_audit_before_export(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from click.testing import CliRunner
    from maverick import audit
    from maverick.cli import main

    secret = "must-not-leave-the-process"
    monkeypatch.setattr(
        "maverick.support_bundle.collect",
        lambda: {
            "correlation_id": "sup_test",
            "entitlement": {"customer": "test", "tier": "test"},
            "payload": secret,
        },
    )

    def refuse(*_args, **_kwargs):
        raise audit.AuditRequiredWriteFailed("audit unavailable")

    monkeypatch.setattr(audit, "required_audit_event", refuse)
    destination = tmp_path / "blocked-support.json"
    file_result = CliRunner().invoke(main, ["support", "-o", str(destination)])
    assert file_result.exit_code != 0
    assert not destination.exists()

    stdout_result = CliRunner().invoke(main, ["support"])
    assert stdout_result.exit_code != 0
    assert secret not in stdout_result.output


def test_local_key_rotation_refuses_before_local_custody_access(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from maverick.audit import signing
    from maverick.cli import audit_rotate_key, tenant_kms_rotate
    from maverick.production_exclusions import ProductionFeatureExcludedError
    from maverick.tenant import kms, kms_fleet

    def unexpected(*_args, **_kwargs):
        raise AssertionError("excluded key rotation touched local custody")

    monkeypatch.setattr(signing, "_key_dir", unexpected)
    monkeypatch.setattr(signing, "_generate_keypair", unexpected)
    with pytest.raises(ProductionFeatureExcludedError, match="audit signing-key"):
        audit_rotate_key.callback()
    with pytest.raises(ProductionFeatureExcludedError, match="audit signing-key"):
        signing.rotate_audit_keypair()

    missing = str(tmp_path / "must-not-be-read")
    with pytest.raises(ProductionFeatureExcludedError, match="LocalKMS fleet rotation"):
        tenant_kms_rotate.callback(missing, missing, False, True)
    monkeypatch.setattr(kms_fleet, "_local_kms", unexpected)
    monkeypatch.setattr(kms_fleet, "_tenants_with_dek", unexpected)
    with pytest.raises(ProductionFeatureExcludedError, match="LocalKMS fleet rotation"):
        kms_fleet.rotate_local_fleet("old-secret", "new-secret")

    local = object.__new__(kms.LocalKMS)
    monkeypatch.setattr(kms, "_wrapped_dek_path", unexpected)
    with pytest.raises(ProductionFeatureExcludedError, match="Vault boundary"):
        kms.rotate_kek("tenant-a", old_kms=local, new_kms=local)
    with pytest.raises(ProductionFeatureExcludedError, match="Vault boundary"):
        kms.rotate_kek_idempotent("tenant-a", old_kms=local, new_kms=local)


def test_local_authority_expansion_refuses_before_registry_access(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from maverick import revocation
    from maverick.cli import tenant_create, tenant_quota, tenant_resume
    from maverick.cli._trust_groups import capability_unrevoke_cmd
    from maverick.production_exclusions import ProductionFeatureExcludedError
    from maverick.tenant import registry

    def unexpected(*_args, **_kwargs):
        raise AssertionError("excluded production mutation inspected local state")

    monkeypatch.setattr(registry, "_validate_id", unexpected)
    with pytest.raises(ProductionFeatureExcludedError, match="tenant creation"):
        registry.create_tenant("tenant-a")
    with pytest.raises(ProductionFeatureExcludedError, match="tenant creation"):
        tenant_create.callback("tenant-a", "free", "", 0.0)

    monkeypatch.setattr(registry, "_mutate", unexpected)
    with pytest.raises(ProductionFeatureExcludedError, match="tenant resume"):
        registry.resume_tenant("tenant-a")
    with pytest.raises(ProductionFeatureExcludedError, match="tenant resume"):
        tenant_resume.callback("tenant-a")

    monkeypatch.setattr(registry, "_quota_value", unexpected)
    with pytest.raises(ProductionFeatureExcludedError, match="tenant quota"):
        registry.set_quota("tenant-a", 10.0)
    with pytest.raises(ProductionFeatureExcludedError, match="tenant quota"):
        tenant_quota.callback("tenant-a", 10.0)
    with pytest.raises(ProductionFeatureExcludedError, match="tenant plan"):
        registry.set_plan("tenant-a", "enterprise")
    monkeypatch.setattr(registry, "_lookup_id", unexpected)
    with pytest.raises(ProductionFeatureExcludedError, match="tenant deletion"):
        registry.delete_tenant("tenant-a")

    monkeypatch.setattr(revocation, "_valid_principal", unexpected)
    revocations = revocation.RevocationRegistry(tmp_path / "must-not-exist.json")
    with pytest.raises(ProductionFeatureExcludedError, match="unrevocation"):
        revocations.unrevoke("agent:example")
    monkeypatch.setattr(revocation, "shared", unexpected)
    with pytest.raises(ProductionFeatureExcludedError, match="unrevocation"):
        capability_unrevoke_cmd.callback("agent:example")
    assert not (tmp_path / "must-not-exist.json").exists()


def test_production_emergency_restrictions_emit_bound_receipts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from maverick import audit, revocation
    from maverick.tenant import registry

    # Seed state outside explicit production; production provisioning itself is
    # intentionally excluded until shared change authority exists.
    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE")
    registry.create_tenant("tenant-a")
    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")

    receipts: list[tuple[str, dict]] = []

    def record(kind, *_args, **kwargs):
        receipts.append((kind, kwargs))
        return True

    monkeypatch.setattr(audit, "required_audit_event", record)
    tenant_operation = "11111111-1111-4111-8111-111111111111"
    capability_operation = "22222222-2222-4222-8222-222222222222"
    suspended = registry.suspend_tenant(
        "tenant-a",
        reason="suspected credential compromise",
        actor="user:operator",
        operation_id=tenant_operation,
    )
    revocations = revocation.RevocationRegistry(tmp_path / "revocations.json")
    revoked = revocations.revoke(
        "agent:compromised",
        reason="suspected key leak",
        actor="user:operator",
        operation_id=capability_operation,
    )

    assert suspended.status == registry.SUSPENDED
    assert revoked.principal == "agent:compromised"
    assert [kind for kind, _payload in receipts] == [
        "tenant_status_changed",
        "capability_revoked",
    ]
    tenant_receipt = receipts[0][1]
    capability_receipt = receipts[1][1]
    assert tenant_receipt["state"] == "COMMITTED"
    assert tenant_receipt["operation_id"] == tenant_operation
    assert tenant_receipt["reason"] == "suspected credential compromise"
    assert tenant_receipt["record_sha256"]
    assert capability_receipt["state"] == "COMMITTED"
    assert capability_receipt["operation_id"] == capability_operation
    assert capability_receipt["reason"] == "suspected key leak"
    assert capability_receipt["affected_count"] == 1
    assert capability_receipt["state_sha256"]


def test_production_emergency_cli_requires_reason_before_registry_access(
    monkeypatch,
) -> None:
    from click.testing import CliRunner
    from maverick import revocation
    from maverick.cli import main
    from maverick.tenant import registry

    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE")
    registry.create_tenant("tenant-a")
    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")

    tenant_result = CliRunner().invoke(main, ["tenant", "suspend", "tenant-a"])
    assert tenant_result.exit_code != 0
    assert "requires a non-empty --reason" in tenant_result.output
    assert registry.get_tenant("tenant-a").status == registry.ACTIVE

    monkeypatch.setattr(
        revocation,
        "shared",
        lambda: (_ for _ in ()).throw(
            AssertionError("reason refusal must precede revocation registry access")
        ),
    )
    capability_result = CliRunner().invoke(
        main, ["capability", "revoke", "agent:compromised"]
    )
    assert capability_result.exit_code != 0
    assert "requires a non-empty --reason" in capability_result.output


def test_production_audit_failure_never_rolls_back_emergency_restriction(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from maverick import audit, revocation
    from maverick.tenant import registry

    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE")
    registry.create_tenant("tenant-a")
    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")

    def refuse(*_args, **_kwargs):
        raise audit.AuditRequiredWriteFailed("audit unavailable")

    monkeypatch.setattr(audit, "required_audit_event", refuse)
    with pytest.raises(audit.AuditRequiredWriteFailed):
        registry.suspend_tenant(
            "tenant-a",
            reason="emergency containment",
            actor="user:operator",
            operation_id="33333333-3333-4333-8333-333333333333",
        )
    assert registry.get_tenant("tenant-a").status == registry.SUSPENDED

    path = tmp_path / "revocations.json"
    revocations = revocation.RevocationRegistry(path)
    with pytest.raises(audit.AuditRequiredWriteFailed):
        revocations.revoke(
            "agent:compromised",
            reason="emergency containment",
            actor="user:operator",
            operation_id="44444444-4444-4444-8444-444444444444",
        )
    assert revocation.RevocationRegistry(path).is_revoked("agent:compromised")


def test_audit_export_requires_authorization_before_any_bytes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import datetime as dt
    import json

    from click.testing import CliRunner
    from maverick import audit
    from maverick.cli import main

    secret = "audit-secret-must-not-leave"
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / f"{day}.ndjson").write_text(
        json.dumps({"kind": "tool_call", "payload": secret}) + "\n",
        encoding="utf-8",
    )
    attempts: list[tuple[str, dict]] = []

    def refuse(kind, *_args, **kwargs):
        attempts.append((kind, kwargs))
        raise audit.AuditRequiredWriteFailed("audit unavailable")

    monkeypatch.setattr(audit, "required_audit_event", refuse)
    destination = tmp_path / "blocked-audit-export.jsonl"
    file_result = CliRunner().invoke(
        main,
        ["audit", "export", "--day", day, "-o", str(destination)],
    )
    assert file_result.exit_code != 0
    assert not destination.exists()
    assert secret not in file_result.output

    stdout_result = CliRunner().invoke(main, ["audit", "export", "--day", day])
    assert stdout_result.exit_code != 0
    assert secret not in stdout_result.output
    assert [kind for kind, _fields in attempts] == [
        "audit_export_authorized",
        "audit_export_authorized",
    ]
    assert attempts[0][1]["state"] == "AUTHORIZED"
    assert attempts[0][1]["format"] == "json"
    assert attempts[0][1]["destination"] == "file"
    assert attempts[0][1]["filename"] == destination.name
    assert attempts[0][1]["tenant"] == "__unbound__"
    assert attempts[0][1]["actor"]
    assert attempts[0][1]["export_id"]


def test_audit_export_records_bound_completion_receipt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import datetime as dt
    import hashlib
    import json

    from click.testing import CliRunner
    from maverick import audit
    from maverick.cli import main

    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / f"{day}.ndjson").write_text(
        json.dumps({"kind": "tool_call", "name": "read_file"}) + "\n",
        encoding="utf-8",
    )
    receipts: list[tuple[str, dict]] = []

    def record(kind, *_args, **kwargs):
        receipts.append((kind, kwargs))
        return True

    monkeypatch.setattr(audit, "required_audit_event", record)
    destination = tmp_path / "audit-export.jsonl"
    result = CliRunner().invoke(
        main,
        ["audit", "export", "--day", day, "-o", str(destination)],
    )
    assert result.exit_code == 0, result.output
    assert [kind for kind, _fields in receipts] == [
        "audit_export_authorized",
        "audit_export_completed",
    ]
    authorized = receipts[0][1]
    completed = receipts[1][1]
    assert authorized["export_id"] == completed["export_id"]
    assert completed["state"] == "COMMITTED"
    assert completed["event_count"] == 1
    canonical = destination.read_text(encoding="utf-8").encode("utf-8")
    assert completed["content_sha256"] == hashlib.sha256(canonical).hexdigest()
    assert completed["digest_encoding"] == "utf-8-lf"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAVERICK_SENTRY_DSN", "https://secret@example.test/1"),
        ("MAVERICK_SENTRY_TRACES_SAMPLE_RATE", "0.5"),
        ("MAVERICK_OTEL_EXPORTER", "otlp"),
        ("MAVERICK_OTEL_ENDPOINT", "https://collector.example.test/v1/traces"),
        ("MAVERICK_OTEL_HEADERS", "authorization=secret"),
        ("MAVERICK_PROMETHEUS_PORT", "9100"),
        ("MAVERICK_PROMETHEUS_ADDR", "0.0.0.0"),
    ],
)
def test_production_rejects_legacy_external_telemetry_environment(
    monkeypatch,
    name: str,
    value: str,
) -> None:
    from maverick.production_exclusions import validate_production_configuration

    monkeypatch.setenv(name, value)
    with pytest.raises(Exception, match="external telemetry exporters/listeners"):
        validate_production_configuration({}, include_runtime_overrides=False)


def test_legacy_observability_is_noop_before_config_or_exporter_setup(
    monkeypatch,
) -> None:
    from maverick import observability

    def unexpected(*_args, **_kwargs):
        raise AssertionError("legacy telemetry inspected config or started an exporter")

    monkeypatch.setattr(observability, "_sentry_enabled", unexpected)
    monkeypatch.setattr(observability, "_otel_enabled", unexpected)
    monkeypatch.setattr(observability, "_prometheus_enabled", unexpected)
    monkeypatch.setattr(observability, "_initialized", False)
    observability._initialize()
    with observability.trace_span("production-admitted-path") as span:
        assert span is None
    observability.record_metric("llm_calls", labels={"provider": "x", "model": "y"})
    assert observability.is_enabled() is False


def test_destructive_retention_entrypoints_refuse_before_config_or_storage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from maverick.audit import retention
    from maverick.production_exclusions import ProductionFeatureExcludedError

    def unexpected(*_args, **_kwargs):
        raise AssertionError("excluded retention inspected config or storage")

    monkeypatch.setattr(retention, "_config", unexpected)
    with pytest.raises(ProductionFeatureExcludedError, match="automated-retention"):
        retention.enforce()
    with pytest.raises(ProductionFeatureExcludedError, match="automated-retention"):
        retention.purge_audit_files(days=1, audit_dir=tmp_path)
    with pytest.raises(ProductionFeatureExcludedError, match="automated-retention"):
        retention.purge_world_episodes(days=1, db_path=tmp_path / "world.db")
    with pytest.raises(ProductionFeatureExcludedError, match="automated-retention"):
        retention.purge_world_events(days=1, db_path=tmp_path / "world.db")
    with pytest.raises(ProductionFeatureExcludedError, match="automated-retention"):
        retention.purge_usage_ledger(days=1, ledger_path=tmp_path / "usage.json")
    assert list(tmp_path.iterdir()) == []


def test_production_docs_do_not_advertise_excluded_data_grounding() -> None:
    documents = {
        "docs/enterprise/architecture.md": "Primary-source data grounding",
        "docs/enterprise/deployment-playbook.md": "Primary-source data grounding",
        "docs/enterprise/diligence.md": "Data grounding / outbound connectors",
    }
    for relative, marker in documents.items():
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        start = text.index(marker)
        if relative.endswith("diligence.md"):
            end = text.index("\n", start)
        else:
            end = text.index("\n\n", start)
        section = text[start:end].lower()
        assert "connectors: []" in section, relative
        assert "on by default" not in section, relative
        assert "auto-granted" not in section, relative


def test_feature_gates_are_inert_outside_explicit_production(monkeypatch) -> None:
    from maverick.production_exclusions import feature_allowed

    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)
    assert feature_allowed("attachments")
    assert feature_allowed("automated_retention")
    assert feature_allowed("knowledge")
    assert feature_allowed("notification_egress")
    assert feature_allowed("plugins")
    assert feature_allowed("self_learning")
    assert feature_allowed("code_dgm")
    assert feature_allowed("consequential_actions")
    assert feature_allowed("external_telemetry")
    assert feature_allowed("finance_suite")
    assert feature_allowed("flow_learning")
    assert feature_allowed("governed_records")
    assert feature_allowed("grpc_dispatch")
    assert feature_allowed("privacy_suite")
    assert feature_allowed("queue_dispatch")
    assert feature_allowed("runtime_settings")
    assert feature_allowed("scim")
    assert feature_allowed("security_suite")
    assert feature_allowed("skills")
    assert feature_allowed("vendor_console")
    assert feature_allowed("voice")
    assert feature_allowed("weights_dgm")
