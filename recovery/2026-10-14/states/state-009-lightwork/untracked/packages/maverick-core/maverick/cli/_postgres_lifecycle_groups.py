"""Drained migration-owner CLI for shared-Postgres tenant lifecycle."""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any

import click

from . import _resolve_tenant_migration_dsn, tenant

_BUNDLE_SCHEMA = "lightwork.postgres-client-export-bundle.v2"
_POSTGRES_ARTIFACT = "postgres.json"
_LOCAL_ARTIFACT = "local.tgz"
_COMPLETION_REPORT = "report.json"
_BUNDLE_FILES = frozenset(
    {_POSTGRES_ARTIFACT, _LOCAL_ARTIFACT, _COMPLETION_REPORT}
)
_REPORT_MAX_BYTES = 1024 * 1024
_REPORT_KEYS = frozenset(
    {
        "artifacts",
        "authentication",
        "complete",
        "drain_confirmation",
        "not_covered",
        "operation_id",
        "production",
        "publication_contract",
        "publication_state",
        "report_sha256",
        "schema",
        "scope",
        "scope_complete",
        "tenant_sha256",
    }
)
_POSTGRES_REPORT_KEYS = frozenset(
    {
        "artifact_sha256",
        "complete",
        "completed_at",
        "counts",
        "database_schema_version",
        "local_backup_required",
        "migration_role_sha256",
        "not_covered",
        "operation",
        "report_sha256",
        "rows_total",
        "schema",
        "schema_source_sha256",
        "scope",
        "scope_complete",
        "table_manifest_sha256",
        "table_sha256",
        "tenant_sha256",
    }
)


def _connect(dsn_file: str):
    from ..world_model_backends.pg_rls import connect

    dsn = _resolve_tenant_migration_dsn(dsn_file=dsn_file, dsn=None)
    try:
        return connect(dsn, autocommit=False)
    except Exception as exc:
        raise click.ClickException(
            "could not connect with the migration-owner DSN file"
        ) from exc


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digested(value: dict[str, Any]) -> dict[str, Any]:
    body = dict(value)
    body["report_sha256"] = hashlib.sha256(
        _canonical(body).encode("utf-8")
    ).hexdigest()
    return body


def _write_private_report(path: str | Path, report: dict[str, Any]) -> None:
    from ..file_lock import atomic_write_text, prepare_private_directory

    destination = Path(path).expanduser()
    try:
        prepare_private_directory(destination.parent)
        atomic_write_text(destination, _canonical(report) + "\n", mode=0o600)
    except Exception as exc:
        raise click.ClickException(
            "private tenant lifecycle report publication failed"
        ) from exc


def _stable_private_file(
    path: Path,
    *,
    capture: bool = False,
    max_capture_bytes: int | None = None,
) -> tuple[str, bytes | None]:
    """Hash one private, single-link regular file without following aliases."""

    from ..file_lock import private_path_is_restricted

    try:
        before = path.lstat()
    except OSError as exc:
        raise click.ClickException("paired export artifact is unreadable") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or not private_path_is_restricted(path)
    ):
        raise click.ClickException(
            "paired export artifact is not a private single-link regular file"
        )
    if capture and max_capture_bytes is not None and before.st_size > max_capture_bytes:
        raise click.ClickException("paired export report exceeds its size limit")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        raise click.ClickException("paired export artifact could not be opened") from exc
    digest = hashlib.sha256()
    captured = bytearray() if capture else None
    try:
        opened = os.fstat(fd)
        visible = path.lstat()
        identity = (before.st_dev, before.st_ino, before.st_mode, before.st_nlink)
        if (
            identity
            != (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_nlink)
            or identity
            != (visible.st_dev, visible.st_ino, visible.st_mode, visible.st_nlink)
        ):
            raise click.ClickException(
                "paired export artifact identity changed while opening"
            )
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            if captured is not None:
                captured.extend(chunk)
                if (
                    max_capture_bytes is not None
                    and len(captured) > max_capture_bytes
                ):
                    raise click.ClickException(
                        "paired export report exceeds its size limit"
                    )
        after = os.fstat(fd)
        final = path.lstat()
        if (
            identity
            != (after.st_dev, after.st_ino, after.st_mode, after.st_nlink)
            or identity
            != (final.st_dev, final.st_ino, final.st_mode, final.st_nlink)
            or after.st_size != opened.st_size
            or getattr(after, "st_mtime_ns", None)
            != getattr(opened, "st_mtime_ns", None)
        ):
            raise click.ClickException(
                "paired export artifact changed while being verified"
            )
    finally:
        os.close(fd)
    return digest.hexdigest(), bytes(captured) if captured is not None else None


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate key {key!r}")
        value[key] = item
    return value


def _bundle_report(path: Path) -> dict[str, Any]:
    _digest_value, raw = _stable_private_file(
        path,
        capture=True,
        max_capture_bytes=_REPORT_MAX_BYTES,
    )
    assert raw is not None
    try:
        report = json.loads(raw, object_pairs_hook=_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise click.ClickException(
            "paired export completion report is malformed"
        ) from exc
    if not isinstance(report, dict):
        raise click.ClickException("paired export completion report must be an object")
    return report


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _verify_postgres_lifecycle_report(
    report: object,
    *,
    expected_binding: dict[str, str],
    artifact_sha256: str,
) -> None:
    from ..postgres_tenant_lifecycle import (
        LIFECYCLE_SCOPE,
        REPORT_SCHEMA,
        SEPARATE_POSTGRES_SURFACES,
        schema_source_sha256,
        table_manifest,
        table_manifest_sha256,
    )

    if not isinstance(report, dict) or set(report) != _POSTGRES_REPORT_KEYS:
        raise click.ClickException("Postgres lifecycle report schema drifted")
    body = dict(report)
    claimed_report_digest = body.pop("report_sha256", None)
    expected_report_digest = hashlib.sha256(
        _canonical(body).encode("utf-8")
    ).hexdigest()
    counts = report.get("counts")
    table_digests = report.get("table_sha256")
    manifest = table_manifest()
    expected_tables = set(manifest["direct"]) | set(manifest["children"])
    if (
        not _is_sha256(claimed_report_digest)
        or claimed_report_digest != expected_report_digest
        or report.get("schema") != REPORT_SCHEMA
        or report.get("scope") != LIFECYCLE_SCOPE
        or report.get("not_covered") != list(SEPARATE_POSTGRES_SURFACES)
        or report.get("operation") != "export"
        or report.get("tenant_sha256") != expected_binding["tenant_sha256"]
        or report.get("artifact_sha256") != artifact_sha256
        or report.get("scope_complete") is not True
        or report.get("complete") is not False
        or report.get("local_backup_required") is not True
        or isinstance(report.get("database_schema_version"), bool)
        or not isinstance(report.get("database_schema_version"), int)
        or not isinstance(report.get("completed_at"), str)
        or not report["completed_at"]
        or not _is_sha256(report.get("migration_role_sha256"))
        or report.get("schema_source_sha256") != schema_source_sha256()
        or report.get("table_manifest_sha256") != table_manifest_sha256()
        or not isinstance(counts, dict)
        or not isinstance(table_digests, dict)
        or set(counts) != expected_tables
        or set(table_digests) != expected_tables
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts.values()
        )
        or any(not _is_sha256(value) for value in table_digests.values())
        or isinstance(report.get("rows_total"), bool)
        or not isinstance(report.get("rows_total"), int)
        or report.get("rows_total") != sum(counts.values())
    ):
        raise click.ClickException(
            "Postgres lifecycle report does not prove the exact export"
        )


def verify_paired_export_bundle(
    bundle: str | Path,
    *,
    expected_binding: dict[str, str],
) -> dict[str, Any]:
    """Authenticate an exact published or staged paired-export bundle."""

    from ..backup import (
        BackupError,
        manifest_sha256,
        verify_paired_export_backup,
        verify_paired_export_report,
    )
    from ..file_lock import private_path_is_restricted

    root = Path(bundle)
    try:
        root_info = root.lstat()
        names = {entry.name for entry in root.iterdir()}
    except OSError as exc:
        raise click.ClickException("paired export bundle is unreadable") from exc
    if (
        stat.S_ISLNK(root_info.st_mode)
        or not stat.S_ISDIR(root_info.st_mode)
        or not private_path_is_restricted(root, mode=0o700)
        or names != _BUNDLE_FILES
    ):
        raise click.ClickException(
            "paired export bundle is not an exact private artifact set"
        )
    report = _bundle_report(root / _COMPLETION_REPORT)
    try:
        verify_paired_export_report(report)
    except BackupError as exc:
        raise click.ClickException(str(exc)) from exc
    if (
        set(report) != _REPORT_KEYS
        or report.get("schema") != _BUNDLE_SCHEMA
        or report.get("operation_id") != expected_binding["operation_id"]
        or report.get("drain_confirmation")
        != expected_binding["drain_confirmation"]
        or report.get("tenant_sha256") != expected_binding["tenant_sha256"]
        or report.get("production")
        != {
            "manifest_sha256": expected_binding["production_manifest_sha256"],
            "profile_id": expected_binding["production_profile_id"],
            "surface": expected_binding["production_surface"],
        }
        or report.get("publication_state") != "COMMITTED"
        or report.get("scope_complete") is not True
        or report.get("complete") is not False
        or not isinstance(report.get("authentication"), dict)
        or report["authentication"].get("key_id")
        != expected_binding["backup_signing_key_id"]
    ):
        raise click.ClickException(
            "paired export completion report does not match the active operation"
        )
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {"local", "postgres"}:
        raise click.ClickException("paired export artifact report is incomplete")
    local_record = artifacts.get("local")
    postgres_record = artifacts.get("postgres")
    if not isinstance(local_record, dict) or not isinstance(postgres_record, dict):
        raise click.ClickException("paired export artifact report is malformed")
    if set(local_record) != {"manifest_sha256", "name", "sha256"} or set(
        postgres_record
    ) != {"lifecycle_report", "name", "sha256"}:
        raise click.ClickException("paired export artifact report schema drifted")
    local_digest, _ = _stable_private_file(root / _LOCAL_ARTIFACT)
    postgres_digest, _ = _stable_private_file(root / _POSTGRES_ARTIFACT)
    if (
        local_record.get("name") != _LOCAL_ARTIFACT
        or local_record.get("sha256") != local_digest
        or postgres_record.get("name") != _POSTGRES_ARTIFACT
        or postgres_record.get("sha256") != postgres_digest
    ):
        raise click.ClickException("paired export artifact digest mismatch")
    _verify_postgres_lifecycle_report(
        postgres_record.get("lifecycle_report"),
        expected_binding=expected_binding,
        artifact_sha256=postgres_digest,
    )
    try:
        local_manifest = verify_paired_export_backup(
            root / _LOCAL_ARTIFACT,
            expected_binding=expected_binding,
        )
    except BackupError as exc:
        raise click.ClickException(str(exc)) from exc
    if local_record.get("manifest_sha256") != manifest_sha256(local_manifest):
        raise click.ClickException("paired export local manifest digest mismatch")
    return report


def _publish_bundle_noreplace(source: Path, destination: Path) -> None:
    """Atomically rename a directory while refusing an existing destination."""

    if os.name == "nt":
        os.rename(source, destination)
        return
    # Linux is the only admitted production operating system. renameat2 with
    # RENAME_NOREPLACE closes the otherwise unavoidable empty-directory
    # replacement race in POSIX rename(). Unsupported development hosts retain
    # the process-locked compatibility fallback, but production fails closed.
    if os.uname().sysname.lower() == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is not None:
            renameat2.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renameat2.restype = ctypes.c_int
            result = renameat2(
                -100,
                os.fsencode(source),
                -100,
                os.fsencode(destination),
                1,
            )
            if result == 0:
                return
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise FileExistsError(str(destination))
            if error not in {errno.ENOSYS, errno.EINVAL}:
                raise OSError(error, os.strerror(error), str(destination))
        from ..production_scope import production_surface_required

        if production_surface_required():
            raise click.ClickException(
                "atomic no-clobber directory publication is unavailable"
            )
    if os.path.lexists(destination):
        raise FileExistsError(str(destination))
    os.rename(source, destination)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _postgres_error(operation: str, exc: BaseException) -> click.ClickException:
    from ..postgres_tenant_lifecycle import PostgresTenantLifecycleError

    if isinstance(exc, PostgresTenantLifecycleError):
        return click.ClickException(str(exc))
    return click.ClickException(f"Postgres tenant {operation} failed")


def _required_export_audit(binding: dict[str, str], *, phase: str) -> None:
    """Persist the value-free export boundary before authority or success."""

    from ..audit import required_audit_event

    try:
        required_audit_event(
            "production_paired_export",
            agent="cli",
            operation_id=binding["operation_id"],
            tenant_sha256=binding["tenant_sha256"],
            production_profile_id=binding["production_profile_id"],
            production_manifest_sha256=binding[
                "production_manifest_sha256"
            ],
            phase=phase,
        )
    except Exception as exc:
        raise click.ClickException(
            "required paired export audit record could not be persisted"
        ) from exc


@tenant.command("postgres-inventory")
@click.argument("tenant_id")
@click.option(
    "--dsn-file",
    envvar="MAVERICK_PG_MIGRATION_DSN_FILE",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Private file containing the drained migration-owner DSN.",
)
@click.option(
    "--confirm-drained",
    is_flag=True,
    required=True,
    help="Confirm serving admissions and writers are drained.",
)
def tenant_postgres_inventory(
    tenant_id: str,
    dsn_file: str,
    confirm_drained: bool,
) -> None:
    """Count every allow-listed Postgres row for one tenant; write nothing."""

    from ..postgres_tenant_lifecycle import inventory_tenant

    conn = _connect(dsn_file)
    try:
        report = inventory_tenant(
            conn,
            tenant_id,
            drained=confirm_drained,
        )
    except Exception as exc:
        raise _postgres_error("inventory", exc) from exc
    finally:
        conn.close()
    click.echo(_canonical(report))


@tenant.command("postgres-export")
@click.argument("tenant_id")
@click.option(
    "--dsn-file",
    envvar="MAVERICK_PG_MIGRATION_DSN_FILE",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Private file containing the drained migration-owner DSN.",
)
@click.option(
    "--bundle-out",
    "bundle_out",
    required=True,
    type=click.Path(file_okay=False),
    help="New private directory for the atomic Postgres/local/report bundle.",
)
@click.option(
    "--operation-id",
    required=True,
    help="Stable canonical UUID used to verify or safely retry this export.",
)
@click.option(
    "--confirm-drained",
    is_flag=True,
    required=True,
    help="Confirm serving admissions and writers are drained.",
)
def tenant_postgres_export(
    tenant_id: str,
    dsn_file: str,
    bundle_out: str,
    operation_id: str,
    confirm_drained: bool,
) -> None:
    """Atomically export Postgres and authenticated bound-client local data."""

    report = export_postgres_and_local_client(
        tenant_id=tenant_id,
        dsn_file=dsn_file,
        bundle_out=bundle_out,
        operation_id=operation_id,
        confirm_drained=confirm_drained,
    )
    click.echo(_canonical(report))


def erase_postgres_and_finalize_local(
    *,
    tenant_id: str,
    dsn_file: str,
    report_out: str,
    confirm_drained: bool,
    confirmed: bool,
) -> dict[str, Any]:
    """Shared implementation for tenant and single-client erasure commands."""

    from ..postgres_tenant_lifecycle import (
        LIFECYCLE_SCOPE,
        SEPARATE_POSTGRES_SURFACES,
        erase_tenant,
    )
    from ..tenant.registry import (
        TenantRegistryError,
        finalize_postgres_tenant_purge,
        get_tenant,
        suspend_tenant,
    )

    # Publish admission denial before opening privileged authority.  If any
    # later step fails, the record remains suspended and the run is retryable.
    try:
        record = get_tenant(tenant_id)
        if record is not None and record.status != "suspended":
            suspend_tenant(tenant_id)
    except Exception as exc:
        raise click.ClickException(
            "tenant could not be suspended before Postgres erasure"
        ) from exc

    conn = _connect(dsn_file)
    try:
        postgres_report = erase_tenant(
            conn,
            tenant_id,
            drained=confirm_drained,
            confirmed=confirmed,
        )
    except Exception as exc:
        raise _postgres_error("erasure", exc) from exc
    finally:
        conn.close()

    # Persist the committed DB proof before touching the local tree.  A local
    # failure can then be retried against an already-zero database.
    try:
        _write_private_report(report_out, postgres_report)
    except click.ClickException as exc:
        raise click.ClickException(
            "Postgres erasure committed, but proof publication failed; tenant "
            "remains suspended and local finalization was not attempted"
        ) from exc

    try:
        local = finalize_postgres_tenant_purge(
            tenant_id,
            postgres_report,
            preserve_audit=True,
        )
    except TenantRegistryError as exc:
        incomplete = _digested(
            {
                "schema": "lightwork.postgres-tenant-offboarding.v1",
                "scope": LIFECYCLE_SCOPE,
                "not_covered": list(SEPARATE_POSTGRES_SURFACES),
                "postgres_report_sha256": postgres_report["report_sha256"],
                "local": {
                    "complete": False,
                    "failure_stage": "local_finalization",
                    "registry_status": "suspended",
                },
                "complete": False,
            }
        )
        try:
            _write_private_report(report_out, incomplete)
        except click.ClickException:
            pass
        raise click.ClickException(
            "Postgres erasure committed, but local finalization is incomplete; "
            "tenant remains suspended and a zero-row rerun is safe"
        ) from exc

    result = _digested(
        {
            "schema": "lightwork.postgres-tenant-offboarding.v1",
            "scope": LIFECYCLE_SCOPE,
            "not_covered": list(SEPARATE_POSTGRES_SURFACES),
            "postgres_report_sha256": postgres_report["report_sha256"],
            "local": local,
            "scope_complete": True,
            "complete": False,
        }
    )
    _write_private_report(report_out, result)
    return result


@tenant.command("postgres-erase")
@click.argument("tenant_id")
@click.option(
    "--dsn-file",
    envvar="MAVERICK_PG_MIGRATION_DSN_FILE",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Private file containing the drained migration-owner DSN.",
)
@click.option(
    "--report-out",
    required=True,
    type=click.Path(dir_okay=False),
    help="Private destination for the value-free completion report.",
)
@click.option(
    "--confirm-drained",
    is_flag=True,
    required=True,
    help="Confirm serving admissions and writers are drained.",
)
@click.option(
    "--confirm-erasure",
    is_flag=True,
    required=True,
    help="Confirm irreversible tenant data erasure.",
)
def tenant_postgres_erase(
    tenant_id: str,
    dsn_file: str,
    report_out: str,
    confirm_drained: bool,
    confirm_erasure: bool,
) -> None:
    """Erase Postgres and local tenant data, preserving audit by default."""

    result = erase_postgres_and_finalize_local(
        tenant_id=tenant_id,
        dsn_file=dsn_file,
        report_out=report_out,
        confirm_drained=confirm_drained,
        confirmed=confirm_erasure,
    )
    click.echo(_canonical(result))


def export_postgres_and_local_client(
    *,
    tenant_id: str,
    dsn_file: str,
    bundle_out: str,
    operation_id: str,
    confirm_drained: bool,
) -> dict[str, Any]:
    """Atomically publish one authenticated Postgres/local export bundle.

    A pre-existing valid bundle for the same operation is verified and returned
    without reopening migration-owner authority. Any ordinary failure before
    the final no-clobber directory rename leaves no visible completion bundle.
    A failure after rename is reported as INDETERMINATE and is safely resolved
    by rerunning with the same operation id.
    """

    from ..backup import (
        BackupError,
        create_paired_export_backup,
        manifest_sha256,
        paired_export_binding,
        seal_paired_export_report,
    )
    from ..file_lock import (
        atomic_create_text,
        cross_process_lock,
        ensure_private_directory,
        prepare_private_directory,
    )
    from ..postgres_tenant_lifecycle import (
        LIFECYCLE_SCOPE,
        SEPARATE_POSTGRES_SURFACES,
        export_tenant,
    )
    try:
        binding = paired_export_binding(
            tenant_id,
            operation_id,
            confirm_drained=confirm_drained,
        )
    except BackupError as exc:
        raise click.ClickException(str(exc)) from exc
    destination = Path(bundle_out).expanduser()
    if not destination.is_absolute() or not destination.name:
        raise click.ClickException(
            "paired export --bundle-out must be an explicit absolute directory"
        )
    try:
        prepare_private_directory(destination.parent)
    except Exception as exc:
        raise click.ClickException(
            "paired export parent directory must be private"
        ) from exc

    with cross_process_lock(destination, strict=True):
        if os.path.lexists(destination):
            try:
                existing = verify_paired_export_bundle(
                    destination,
                    expected_binding=binding,
                )
            except click.ClickException as exc:
                raise click.ClickException(
                    "INDETERMINATE: paired export destination exists but is not "
                    "the authenticated completion for this operation"
                ) from exc
            _required_export_audit(binding, phase="verified_existing")
            return existing

        _required_export_audit(binding, phase="authorized")
        stage = Path(
            tempfile.mkdtemp(
                dir=destination.parent,
                prefix=(
                    f".{destination.name}.PREPARED-"
                    f"{binding['operation_id']}-"
                ),
            )
        )
        published = False
        try:
            ensure_private_directory(stage)
            postgres_path = stage / _POSTGRES_ARTIFACT
            conn = _connect(dsn_file)
            try:
                postgres_report = export_tenant(
                    conn,
                    tenant_id,
                    postgres_path,
                    drained=confirm_drained,
                )
            except Exception as exc:
                raise _postgres_error("export", exc) from exc
            finally:
                conn.close()

            local_path = stage / _LOCAL_ARTIFACT
            try:
                _created, local_binding = create_paired_export_backup(
                    local_path,
                    tenant_id=tenant_id,
                    operation_id=operation_id,
                    confirm_drained=confirm_drained,
                )
            except BackupError as exc:
                raise click.ClickException(
                    "paired export local archive failed before publication"
                ) from exc
            if local_binding != binding:
                raise click.ClickException(
                    "paired export authority changed while artifacts were staged"
                )
            from ..backup import verify_paired_export_backup

            try:
                local_manifest = verify_paired_export_backup(
                    local_path,
                    expected_binding=binding,
                )
            except BackupError as exc:
                raise click.ClickException(
                    "paired export local archive verification failed"
                ) from exc
            local_digest, _ = _stable_private_file(local_path)
            postgres_digest, _ = _stable_private_file(postgres_path)
            if postgres_digest != postgres_report.get("artifact_sha256"):
                raise click.ClickException(
                    "Postgres export digest changed before bundle publication"
                )
            completion = seal_paired_export_report(
                {
                    "schema": _BUNDLE_SCHEMA,
                    "operation_id": binding["operation_id"],
                    "drain_confirmation": binding["drain_confirmation"],
                    "tenant_sha256": binding["tenant_sha256"],
                    "production": {
                        "profile_id": binding["production_profile_id"],
                        "manifest_sha256": binding[
                            "production_manifest_sha256"
                        ],
                        "surface": binding["production_surface"],
                    },
                    "scope": LIFECYCLE_SCOPE,
                    "not_covered": list(SEPARATE_POSTGRES_SURFACES),
                    "publication_state": "COMMITTED",
                    "publication_contract": (
                        "valid only when this exact report is inside the "
                        "operator-selected final bundle directory"
                    ),
                    "artifacts": {
                        "postgres": {
                            "name": _POSTGRES_ARTIFACT,
                            "sha256": postgres_digest,
                            "lifecycle_report": postgres_report,
                        },
                        "local": {
                            "name": _LOCAL_ARTIFACT,
                            "sha256": local_digest,
                            "manifest_sha256": manifest_sha256(local_manifest),
                        },
                    },
                    "scope_complete": True,
                    "complete": False,
                }
            )
            atomic_create_text(
                stage / _COMPLETION_REPORT,
                _canonical(completion) + "\n",
                mode=0o600,
            )
            verify_paired_export_bundle(stage, expected_binding=binding)
            if os.path.lexists(destination):
                raise FileExistsError(str(destination))
            _publish_bundle_noreplace(stage, destination)
            published = True
            _fsync_directory(destination.parent)
            verified = verify_paired_export_bundle(
                destination,
                expected_binding=binding,
            )
            _required_export_audit(binding, phase="published")
            return verified
        except FileExistsError as exc:
            raise click.ClickException(
                "INDETERMINATE: paired export destination appeared during "
                "publication; rerun with the same operation id"
            ) from exc
        except click.ClickException as exc:
            if published:
                raise click.ClickException(
                    "INDETERMINATE: paired export may be published; rerun with "
                    "the same operation id to authenticate the final bundle"
                ) from exc
            raise
        except BaseException as exc:
            if published:
                raise click.ClickException(
                    "INDETERMINATE: paired export may be published; rerun with "
                    "the same operation id to authenticate the final bundle"
                ) from exc
            raise click.ClickException(
                "paired export failed before atomic publication"
            ) from exc
        finally:
            if not published and os.path.lexists(stage):
                try:
                    shutil.rmtree(stage)
                except OSError:
                    pass


__all__ = [
    "erase_postgres_and_finalize_local",
    "export_postgres_and_local_client",
]
