"""CLI gates for fake-only shared-Postgres tenant lifecycle operations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import click
import pytest
from click.testing import CliRunner
from maverick.cli import _postgres_lifecycle_groups as commands
from maverick.cli import main


class DummyConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from maverick.client import reset_client_cache

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MAVERICK_CLIENT_ID", raising=False)
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_CONFIG", raising=False)
    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)
    monkeypatch.delenv("MAVERICK_PRODUCTION_MANIFEST", raising=False)
    monkeypatch.delenv("MAVERICK_PRODUCTION_MANIFEST_SHA256", raising=False)
    monkeypatch.delenv("MAVERICK_PRODUCTION_PROFILE_ID", raising=False)
    monkeypatch.delenv("MAVERICK_BACKUP_SIGNING_KEY", raising=False)
    reset_client_cache()
    yield
    reset_client_cache()


def _dsn_file(tmp_path):
    path = tmp_path / "dsn"
    path.write_text("postgres://private", encoding="utf-8")
    return path


def test_inventory_cli_requires_private_authority_and_drain_confirmation(tmp_path):
    dsn = _dsn_file(tmp_path)
    runner = CliRunner()

    missing_dsn = runner.invoke(
        main,
        ["tenant", "postgres-inventory", "acme", "--confirm-drained"],
    )
    missing_drain = runner.invoke(
        main,
        ["tenant", "postgres-inventory", "acme", "--dsn-file", str(dsn)],
    )

    assert missing_dsn.exit_code == 2 and "--dsn-file" in missing_dsn.output
    assert missing_drain.exit_code == 2 and "--confirm-drained" in missing_drain.output


def test_inventory_cli_closes_connection_and_emits_value_free_json(
    monkeypatch, tmp_path,
):
    from maverick import postgres_tenant_lifecycle as lifecycle

    dsn = _dsn_file(tmp_path)
    conn = DummyConnection()
    monkeypatch.setattr(commands, "_connect", lambda _path: conn)
    monkeypatch.setattr(
        lifecycle,
        "inventory_tenant",
        lambda supplied, tenant_id, *, drained: {
            "schema": lifecycle.REPORT_SCHEMA,
            "operation": "inventory",
            "tenant_sha256": "a" * 64,
            "counts": {},
            "complete": supplied is conn and tenant_id == "acme" and drained,
        },
    )

    result = CliRunner().invoke(
        main,
        [
            "tenant",
            "postgres-inventory",
            "acme",
            "--dsn-file",
            str(dsn),
            "--confirm-drained",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["complete"] is True
    assert "postgres://private" not in result.output
    assert conn.closed is True


def test_unexpected_database_error_never_echoes_credentials(monkeypatch, tmp_path):
    from maverick import postgres_tenant_lifecycle as lifecycle

    dsn = _dsn_file(tmp_path)
    conn = DummyConnection()
    monkeypatch.setattr(commands, "_connect", lambda _path: conn)
    monkeypatch.setattr(
        lifecycle,
        "inventory_tenant",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("postgres://operator:super-secret@db/private")
        ),
    )

    result = CliRunner().invoke(
        main,
        [
            "tenant",
            "postgres-inventory",
            "acme",
            "--dsn-file",
            str(dsn),
            "--confirm-drained",
        ],
    )

    assert result.exit_code == 1
    assert "Postgres tenant inventory failed" in result.output
    assert "super-secret" not in result.output
    assert conn.closed is True


def test_local_failure_after_commit_writes_incomplete_retry_report(
    monkeypatch, tmp_path,
):
    from maverick import postgres_tenant_lifecycle as lifecycle
    from maverick.tenant import registry

    dsn = _dsn_file(tmp_path)
    conn = DummyConnection()
    writes = []
    postgres_report = {"report_sha256": "1" * 64, "complete": True}
    monkeypatch.setattr(commands, "_connect", lambda _path: conn)
    monkeypatch.setattr(registry, "get_tenant", lambda _tenant: None)
    monkeypatch.setattr(
        lifecycle,
        "erase_tenant",
        lambda *args, **kwargs: postgres_report,
    )
    monkeypatch.setattr(
        registry,
        "finalize_postgres_tenant_purge",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            registry.TenantRegistryError("disk")
        ),
    )
    monkeypatch.setattr(
        commands,
        "_write_private_report",
        lambda _path, report: writes.append(report),
    )

    with pytest.raises(click.ClickException, match="zero-row rerun is safe"):
        commands.erase_postgres_and_finalize_local(
            tenant_id="acme",
            dsn_file=str(dsn),
            report_out=str(tmp_path / "report.json"),
            confirm_drained=True,
            confirmed=True,
        )

    assert writes[0] == postgres_report
    assert writes[-1]["complete"] is False
    assert writes[-1]["local"]["registry_status"] == "suspended"
    assert conn.closed is True


def _production_export_environment(monkeypatch, tmp_path):
    from maverick import audit, file_lock
    from maverick.client import reset_client_cache
    from maverick.paths import data_dir

    manifest = Path(__file__).parents[3] / "release" / "production-surface-manifest.json"
    monkeypatch.setenv("MAVERICK_CLIENT_ID", "acme")
    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")
    monkeypatch.setenv("MAVERICK_PRODUCTION_MANIFEST", str(manifest))
    monkeypatch.setenv(
        "MAVERICK_PRODUCTION_PROFILE_ID",
        "enterprise-single-control-plane-v1",
    )
    monkeypatch.setenv("MAVERICK_BACKUP_SIGNING_KEY", "11" * 32)
    reset_client_cache()
    root = file_lock.ensure_private_directory(data_dir(tenant="acme"))
    file_lock.atomic_create_text(root / "local-state.json", '{"sealed":"ciphertext"}')
    monkeypatch.setattr(audit, "required_audit_event", lambda *args, **kwargs: True)


def _fake_postgres_export(conn, tenant_id, destination, *, drained):
    from maverick import file_lock
    from maverick import postgres_tenant_lifecycle as lifecycle

    assert tenant_id == "acme" and drained is True
    payload = b'{"tenant_id":"acme","tables":{}}'
    file_lock.atomic_create_bytes(destination, payload)
    manifest = lifecycle.table_manifest()
    tables = sorted(set(manifest["direct"]) | set(manifest["children"]))
    report = {
        "schema": lifecycle.REPORT_SCHEMA,
        "scope": lifecycle.LIFECYCLE_SCOPE,
        "not_covered": list(lifecycle.SEPARATE_POSTGRES_SURFACES),
        "operation": "export",
        "tenant_sha256": hashlib.sha256(b"acme").hexdigest(),
        "migration_role_sha256": hashlib.sha256(b"migration-owner").hexdigest(),
        "database_schema_version": 1,
        "schema_source_sha256": lifecycle.schema_source_sha256(),
        "table_manifest_sha256": lifecycle.table_manifest_sha256(),
        "completed_at": "2026-08-03T00:00:00Z",
        "counts": dict.fromkeys(tables, 0),
        "rows_total": 0,
        "scope_complete": True,
        "complete": False,
        "local_backup_required": True,
        "table_sha256": dict.fromkeys(
            tables,
            hashlib.sha256(b"[]").hexdigest(),
        ),
        "artifact_sha256": hashlib.sha256(payload).hexdigest(),
    }
    report["report_sha256"] = hashlib.sha256(
        json.dumps(
            report,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return report


def test_bound_client_export_atomically_publishes_authenticated_bundle(
    monkeypatch, tmp_path,
):
    from maverick import backup
    from maverick import postgres_tenant_lifecycle as lifecycle

    _production_export_environment(monkeypatch, tmp_path)
    dsn = _dsn_file(tmp_path)
    conn = DummyConnection()
    bundle = tmp_path / "secure" / "acme-export"
    operation_id = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setattr(commands, "_connect", lambda _path: conn)
    monkeypatch.setattr(lifecycle, "export_tenant", _fake_postgres_export)

    report = commands.export_postgres_and_local_client(
        tenant_id="acme",
        dsn_file=str(dsn),
        bundle_out=str(bundle),
        operation_id=operation_id,
        confirm_drained=True,
    )

    assert report["scope_complete"] is True
    assert report["complete"] is False
    assert report["not_covered"] == ["governed_records"]
    assert report["operation_id"] == operation_id
    assert report["publication_state"] == "COMMITTED"
    assert report["production"]["surface"] == (
        "runtime:postgres-migration-owner-lifecycle"
    )
    assert {path.name for path in bundle.iterdir()} == {
        "postgres.json",
        "local.tgz",
        "report.json",
    }
    assert backup.read_manifest(bundle / "local.tgz")[
        "production_export_binding"
    ]["operation_id"] == operation_id
    report_text = (bundle / "report.json").read_text(encoding="utf-8")
    assert "acme" not in report_text
    assert "ciphertext" not in report_text
    assert str(tmp_path) not in report_text
    assert conn.closed is True

    with pytest.raises(backup.BackupError, match="standalone local backup"):
        backup.create_backup(tmp_path / "generic.tgz")


def test_paired_export_retry_authenticates_existing_bundle_without_database(
    monkeypatch, tmp_path,
):
    from maverick import postgres_tenant_lifecycle as lifecycle

    _production_export_environment(monkeypatch, tmp_path)
    dsn = _dsn_file(tmp_path)
    bundle = tmp_path / "secure" / "acme-export"
    operation_id = "22222222-2222-4222-8222-222222222222"
    monkeypatch.setattr(commands, "_connect", lambda _path: DummyConnection())
    monkeypatch.setattr(lifecycle, "export_tenant", _fake_postgres_export)
    first = commands.export_postgres_and_local_client(
        tenant_id="acme",
        dsn_file=str(dsn),
        bundle_out=str(bundle),
        operation_id=operation_id,
        confirm_drained=True,
    )
    monkeypatch.setattr(
        commands,
        "_connect",
        lambda _path: pytest.fail("retry must not reopen migration-owner authority"),
    )

    retried = commands.export_postgres_and_local_client(
        tenant_id="acme",
        dsn_file=str(dsn),
        bundle_out=str(bundle),
        operation_id=operation_id,
        confirm_drained=True,
    )

    assert retried == first

    (bundle / "postgres.json").write_bytes(b"tampered")
    with pytest.raises(click.ClickException, match="INDETERMINATE"):
        commands.export_postgres_and_local_client(
            tenant_id="acme",
            dsn_file=str(dsn),
            bundle_out=str(bundle),
            operation_id=operation_id,
            confirm_drained=True,
        )


def test_local_failure_after_postgres_export_leaves_no_visible_completion(
    monkeypatch, tmp_path,
):
    from maverick import backup
    from maverick import postgres_tenant_lifecycle as lifecycle

    _production_export_environment(monkeypatch, tmp_path)
    dsn = _dsn_file(tmp_path)
    bundle = tmp_path / "secure" / "acme-export"
    monkeypatch.setattr(commands, "_connect", lambda _path: DummyConnection())
    monkeypatch.setattr(lifecycle, "export_tenant", _fake_postgres_export)
    monkeypatch.setattr(
        backup,
        "create_paired_export_backup",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            backup.BackupError("local snapshot failed")
        ),
    )

    with pytest.raises(click.ClickException, match="before publication"):
        commands.export_postgres_and_local_client(
            tenant_id="acme",
            dsn_file=str(dsn),
            bundle_out=str(bundle),
            operation_id="33333333-3333-4333-8333-333333333333",
            confirm_drained=True,
        )

    assert not bundle.exists()
    assert not list((tmp_path / "secure").glob("*.PREPARED-*"))


def test_publication_race_never_clobbers_appearing_destination(
    monkeypatch, tmp_path,
):
    from maverick import file_lock
    from maverick import postgres_tenant_lifecycle as lifecycle

    _production_export_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(commands, "_connect", lambda _path: DummyConnection())
    monkeypatch.setattr(lifecycle, "export_tenant", _fake_postgres_export)
    bundle = tmp_path / "secure" / "acme-export"

    def plant_destination(_source, destination):
        file_lock.ensure_private_directory(destination)
        file_lock.atomic_create_text(destination / "operator-owned", "keep")
        raise FileExistsError(str(destination))

    monkeypatch.setattr(commands, "_publish_bundle_noreplace", plant_destination)

    with pytest.raises(click.ClickException, match="INDETERMINATE"):
        commands.export_postgres_and_local_client(
            tenant_id="acme",
            dsn_file=str(_dsn_file(tmp_path)),
            bundle_out=str(bundle),
            operation_id="99999999-9999-4999-8999-999999999999",
            confirm_drained=True,
        )

    assert (bundle / "operator-owned").read_text(encoding="utf-8") == "keep"


def test_paired_export_refuses_tenant_binding_mismatch_before_connect(
    monkeypatch, tmp_path,
):
    from maverick.client import reset_client_cache

    _production_export_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("MAVERICK_CLIENT_ID", "other")
    reset_client_cache()
    monkeypatch.setattr(
        commands,
        "_connect",
        lambda _path: pytest.fail("binding mismatch must precede database access"),
    )

    with pytest.raises(click.ClickException, match="client binding"):
        commands.export_postgres_and_local_client(
            tenant_id="acme",
            dsn_file=str(_dsn_file(tmp_path)),
            bundle_out=str(tmp_path / "secure" / "acme-export"),
            operation_id="44444444-4444-4444-8444-444444444444",
            confirm_drained=True,
        )


def test_paired_export_required_audit_refuses_before_database_or_artifacts(
    monkeypatch, tmp_path,
):
    from maverick import audit

    _production_export_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(
        audit,
        "required_audit_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    monkeypatch.setattr(
        commands,
        "_connect",
        lambda _path: pytest.fail("audit refusal must precede database access"),
    )
    bundle = tmp_path / "secure" / "acme-export"

    with pytest.raises(click.ClickException, match="audit record"):
        commands.export_postgres_and_local_client(
            tenant_id="acme",
            dsn_file=str(_dsn_file(tmp_path)),
            bundle_out=str(bundle),
            operation_id="55555555-5555-4555-8555-555555555555",
            confirm_drained=True,
        )

    assert not bundle.exists()


def test_paired_export_requires_signing_custody_before_database(
    monkeypatch, tmp_path,
):
    _production_export_environment(monkeypatch, tmp_path)
    monkeypatch.delenv("MAVERICK_BACKUP_SIGNING_KEY")
    monkeypatch.setattr(
        commands,
        "_connect",
        lambda _path: pytest.fail("signing preflight must precede database access"),
    )

    with pytest.raises(click.ClickException, match="signing custody"):
        commands.export_postgres_and_local_client(
            tenant_id="acme",
            dsn_file=str(_dsn_file(tmp_path)),
            bundle_out=str(tmp_path / "secure" / "acme-export"),
            operation_id="77777777-7777-4777-8777-777777777777",
            confirm_drained=True,
        )


def test_tenant_export_cli_requires_atomic_bundle_and_operation_id(tmp_path):
    dsn = _dsn_file(tmp_path)
    result = CliRunner().invoke(
        main,
        [
            "tenant",
            "postgres-export",
            "acme",
            "--dsn-file",
            str(dsn),
            "--bundle-out",
            str(tmp_path / "bundle"),
            "--confirm-drained",
        ],
    )

    assert result.exit_code == 2
    assert "--operation-id" in result.output


def test_tenant_export_cli_routes_exact_atomic_bundle_contract(
    monkeypatch, tmp_path,
):
    captured = {}

    def fake_export(**kwargs):
        captured.update(kwargs)
        return {"operation_id": kwargs["operation_id"], "complete": False}

    monkeypatch.setattr(commands, "export_postgres_and_local_client", fake_export)
    dsn = _dsn_file(tmp_path)
    bundle = tmp_path / "bundle"
    operation_id = "88888888-8888-4888-8888-888888888888"
    result = CliRunner().invoke(
        main,
        [
            "tenant",
            "postgres-export",
            "acme",
            "--dsn-file",
            str(dsn),
            "--bundle-out",
            str(bundle),
            "--operation-id",
            operation_id,
            "--confirm-drained",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["operation_id"] == operation_id
    assert captured == {
        "tenant_id": "acme",
        "dsn_file": str(dsn),
        "bundle_out": str(bundle),
        "operation_id": operation_id,
        "confirm_drained": True,
    }


def test_client_export_cli_refuses_legacy_split_postgres_outputs(
    monkeypatch, tmp_path,
):
    from maverick import world_model_backends
    from maverick.client import reset_client_cache

    monkeypatch.setenv("MAVERICK_CLIENT_ID", "acme")
    reset_client_cache()
    monkeypatch.setattr(
        world_model_backends,
        "destructive_lifecycle_world_backend",
        lambda: "postgres",
    )
    result = CliRunner().invoke(
        main,
        [
            "client",
            "export",
            "--out",
            str(tmp_path / "local.tgz"),
            "--postgres-out",
            str(tmp_path / "postgres.json"),
        ],
    )

    assert result.exit_code == 1
    assert "refuses split" in result.output


def test_client_export_cli_routes_exact_atomic_bundle_contract(
    monkeypatch, tmp_path,
):
    from maverick import world_model_backends
    from maverick.client import reset_client_cache

    monkeypatch.setenv("MAVERICK_CLIENT_ID", "acme")
    reset_client_cache()
    monkeypatch.setattr(
        world_model_backends,
        "destructive_lifecycle_world_backend",
        lambda: "postgres",
    )
    captured = {}

    def fake_export(**kwargs):
        captured.update(kwargs)
        return {"operation_id": kwargs["operation_id"], "complete": False}

    monkeypatch.setattr(commands, "export_postgres_and_local_client", fake_export)
    dsn = _dsn_file(tmp_path)
    bundle = tmp_path / "bundle"
    operation_id = "66666666-6666-4666-8666-666666666666"
    result = CliRunner().invoke(
        main,
        [
            "client",
            "export",
            "--dsn-file",
            str(dsn),
            "--bundle-out",
            str(bundle),
            "--operation-id",
            operation_id,
            "--confirm-drained",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["operation_id"] == operation_id
    assert captured == {
        "tenant_id": "acme",
        "dsn_file": str(dsn),
        "bundle_out": str(bundle),
        "operation_id": operation_id,
        "confirm_drained": True,
    }
