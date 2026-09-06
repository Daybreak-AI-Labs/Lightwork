"""Administrator recovery controls; every target is resolved on the server."""
from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from maverick import backup, config, installation_recovery
from maverick import workspace_recovery as recovery
from maverick.file_lock import ensure_private_directory
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .auth import _authorization_principal, require_global_permission
from .recovery_mode import enabled, isolated

router = APIRouter()


def _context(request: Request) -> str:
    return hashlib.sha256(json.dumps([recovery.target(), _authorization_principal(request) or "local"], separators=(",", ":")).encode()).hexdigest()


def _admit(request: Request, *, mutation: bool = False) -> str:
    require_global_permission(request, "admin")
    if config.config_source_errors(include_tenant=True) or config.get_features().get("workspace_recovery") is not True:
        raise HTTPException(403, "Workspace recovery is disabled. A global administrator can enable it in Settings before entering recovery mode.")
    try:
        context = _context(request)
    except (recovery.RecoveryError, OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    if mutation and not hmac.compare_digest(request.headers.get("X-Lightwork-Recovery-Context", ""), context):
        raise HTTPException(409, "The workspace or signed-in account changed. Reload recovery before continuing.")
    return context


async def _call(function, *args, **kwargs):
    try:
        return await run_in_threadpool(function, *args, **kwargs)
    except (recovery.RecoveryError, backup.BackupError, installation_recovery.MaintenanceError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Recovery storage could not complete this step. Existing safety snapshots have been kept.") from exc


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = Field(strict=True)


class KeyIn(ConfirmIn):
    key: str | None = Field(default=None, max_length=128)


class EncryptionKeysIn(ConfirmIn):
    confirm_shared_keys: bool = Field(strict=True)


class RestoreIn(ConfirmIn):
    review: str = Field(pattern=r"^[0-9a-f]{32}$")
    target: str = Field(pattern=r"^[0-9a-f]{64}$")


class RetireIn(ConfirmIn):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class InstallationIn(ConfirmIn):
    confirm_all_workspaces: bool = Field(strict=True)
    rollback_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class CompareStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class ComparisonIn(ReportIn):
    offset: int = Field(default=0, ge=0, le=backup.MAX_ARCHIVE_MEMBERS * 2, strict=True)
    limit: int = Field(default=50, ge=1, le=100, strict=True)


@router.get("/api/v1/recovery/installation")
async def installation_status(request: Request):
    _admit(request)
    return await _call(installation_recovery.status)


@router.post("/api/v1/recovery/installation/authorize")
async def authorize_installation(request: Request, body: InstallationIn):
    actor = _admit(request, mutation=True)
    _confirm(body)
    result = await _call(installation_recovery.authorize, actor=actor, isolated=isolated(request.app),
                         confirm_all_workspaces=body.confirm_all_workspaces, rollback_id=body.rollback_id)
    return Response(json.dumps(result), media_type="application/json", headers={"Cache-Control": "private, no-store"})


@router.post("/api/v1/recovery/installation/{identifier}/retire")
async def retire_installation(request: Request, identifier: str, body: RetireIn):
    actor = _admit(request, mutation=True)
    _confirm(body)
    result = await _call(installation_recovery.retire, identifier, expected_sha256=body.sha256,
                         actor=actor, confirm=body.confirm)
    return Response(json.dumps(result), media_type="application/json", headers={"Cache-Control": "private, no-store"})


def _confirm(body: ConfirmIn) -> None:
    if body.confirm is not True:
        raise HTTPException(400, "Review and explicitly confirm this recovery action.")


@router.get("/workspace/recovery", response_class=HTMLResponse)
async def recovery_page(request: Request):
    require_global_permission(request, "admin")
    from .app import templates
    response = templates.TemplateResponse(request, "workspace_recovery.html", {"recovery_mode": enabled(request.app)})
    response.headers["Cache-Control"] = "private, no-store"
    return response


@router.get("/api/v1/recovery/status")
async def recovery_status(request: Request):
    context = _admit(request)
    result = await _call(recovery.status)
    return {**result, "context": context, "recovery_mode": enabled(request.app),
            "expected_tenant": getattr(request.state, "workspace_expected_tenant", ""),
            "live_restore_available": isolated(request.app) and not result["restore_blockers"]}


@router.post("/api/v1/recovery/key")
async def configure_key(request: Request, body: KeyIn):
    _admit(request, mutation=True)
    _confirm(body)
    result = await _call(recovery.configure_key, imported=body.key)
    return Response(result["key"] + "\n", media_type="text/plain", headers={
        "Content-Disposition": 'attachment; filename="Lightwork-recovery-key.txt"', "Cache-Control": "private, no-store"})


@router.post("/api/v1/recovery/key/download")
async def download_key(request: Request, body: ConfirmIn):
    _admit(request, mutation=True)
    _confirm(body)
    key = await _call(recovery.export_key)
    return Response(key + "\n", media_type="text/plain", headers={
        "Content-Disposition": 'attachment; filename="Lightwork-recovery-key.txt"', "Cache-Control": "private, no-store"})


@router.post("/api/v1/recovery/encryption-keys/download")
async def download_encryption_keys(request: Request, body: EncryptionKeysIn):
    _admit(request, mutation=True)
    _confirm(body)
    if body.confirm_shared_keys is not True:
        raise HTTPException(400, "Confirm that these sensitive encryption keys may unlock several workspaces and must be stored separately.")
    payload = await _call(recovery.export_encryption_keys)
    return Response(payload, media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="Lightwork-local-encryption-keys.zip"', "Cache-Control": "private, no-store"})


@router.post("/api/v1/recovery/snapshots", status_code=201)
async def create_snapshot(request: Request, body: ConfirmIn):
    _admit(request, mutation=True)
    _confirm(body)
    return await _call(recovery.create)


@router.post("/api/v1/recovery/import", status_code=201)
async def import_snapshot(request: Request):
    _admit(request, mutation=True)
    if request.headers.get("Content-Type", "").split(";")[0] != "application/octet-stream":
        raise HTTPException(415, "Select a Lightwork snapshot archive.")
    with tempfile.TemporaryDirectory(prefix="lightwork-recovery-upload-") as temporary:
        directory = ensure_private_directory(temporary)
        path = Path(directory) / "upload.tgz"
        size = 0
        with path.open("xb") as stream:
            async for chunk in request.stream():
                size += len(chunk)
                if size > recovery.MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "The archive exceeds the in-app size limit.")
                stream.write(chunk)
        return await _call(recovery.import_archive, path)


@router.post("/api/v1/recovery/snapshots/{identifier}/inspect")
async def inspect_snapshot(request: Request, identifier: str):
    _admit(request, mutation=True)
    return await _call(recovery.inspect, identifier)


@router.post("/api/v1/recovery/snapshots/{identifier}/download")
async def download_snapshot(request: Request, identifier: str):
    _admit(request, mutation=True)
    path = await _call(recovery.download_path, identifier)
    return FileResponse(path, media_type="application/gzip", filename="Lightwork-snapshot-" + identifier + ".tgz", headers={"Cache-Control": "private, no-store"})


@router.post("/api/v1/recovery/snapshots/{identifier}/compare")
async def compare_snapshot(request: Request, identifier: str, body: CompareStartIn):
    actor = _admit(request, mutation=True)
    result = await _call(recovery.compare_snapshot, identifier, actor=actor, isolated=isolated(request.app))
    return Response(json.dumps(result), media_type="application/json", headers={"Cache-Control": "private, no-store"})


@router.post("/api/v1/recovery/snapshots/{identifier}/comparison")
async def comparison_page(request: Request, identifier: str, body: ComparisonIn):
    actor = _admit(request, mutation=True)
    result = await _call(recovery.comparison_page, identifier, body.report_id, actor=actor, offset=body.offset, limit=body.limit)
    return Response(json.dumps(result), media_type="application/json", headers={"Cache-Control": "private, no-store"})


@router.post("/api/v1/recovery/snapshots/{identifier}/comparison/download")
async def download_comparison(request: Request, identifier: str, body: ReportIn):
    actor = _admit(request, mutation=True)
    payload = await _call(recovery.export_comparison, identifier, body.report_id, actor=actor)
    return Response(payload, media_type="application/json", headers={
        "Content-Disposition": 'attachment; filename="Lightwork-recovery-comparison-' + identifier + '.json"',
        "Cache-Control": "private, no-store"})


@router.post("/api/v1/recovery/snapshots/{identifier}/drill")
async def drill_snapshot(request: Request, identifier: str, body: ConfirmIn):
    actor = _admit(request, mutation=True)
    _confirm(body)
    result = await _call(recovery.drill, identifier, actor=actor)
    return Response(json.dumps(result), media_type="application/json", headers={"Cache-Control": "private, no-store"})


@router.post("/api/v1/recovery/snapshots/{identifier}/drill/report")
async def saved_drill_report(request: Request, identifier: str, body: CompareStartIn):
    actor = _admit(request, mutation=True)
    result = await _call(recovery.drill_report, identifier, actor=actor)
    return Response(json.dumps(result), media_type="application/json", headers={"Cache-Control": "private, no-store"})


@router.post("/api/v1/recovery/snapshots/{identifier}/drill/report/download")
async def download_drill_report(request: Request, identifier: str, body: ReportIn):
    actor = _admit(request, mutation=True)
    payload = await _call(recovery.export_drill_report, identifier, body.report_id, actor=actor)
    return Response(payload, media_type="application/json", headers={
        "Content-Disposition": 'attachment; filename="Lightwork-restore-drill-' + identifier + '.json"',
        "Cache-Control": "private, no-store"})


@router.post("/api/v1/recovery/snapshots/{identifier}/retire")
async def retire_snapshot(request: Request, identifier: str, body: RetireIn):
    _admit(request, mutation=True)
    _confirm(body)
    return await _call(recovery.retire, identifier, body.sha256)


@router.post("/api/v1/recovery/snapshots/{identifier}/prepare")
async def prepare_restore(request: Request, identifier: str, body: ConfirmIn):
    actor = _admit(request, mutation=True)
    _confirm(body)
    return await _call(recovery.prepare_restore, identifier, isolated=isolated(request.app), actor=actor)


@router.post("/api/v1/recovery/restore")
async def restore_snapshot(request: Request, body: RestoreIn):
    actor = _admit(request, mutation=True)
    _confirm(body)
    return await _call(recovery.apply_restore, body.review, body.target, isolated=isolated(request.app), actor=actor)
