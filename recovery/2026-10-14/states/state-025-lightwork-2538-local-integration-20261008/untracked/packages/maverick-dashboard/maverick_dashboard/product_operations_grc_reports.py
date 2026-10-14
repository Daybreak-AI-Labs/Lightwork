"""Read published GRC Studio report results through protected Copilot controls."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from maverick import no_code_grc_studio as studio
from maverick import trc_continuous_evidence as evidence
from maverick.governed_operations import digest
from pydantic import Field

from . import auth
from . import no_code_grc_studio_api as native
from .grc_studio_report_views import preview_published_report
from .product_operations import Adapter, Preview, Snapshot, StrictInput

PAGE = "/compliance/grc-studio"
EVIDENCE_PAGE = "/compliance/continuous-evidence"
_REPORT_LIMIT = 100
_COPILOT_ROW_LIMIT = 10
_COPILOT_COLUMN_LIMIT = 12
_COPILOT_GROUP_LIMIT = 10
_COPILOT_SOURCE_LIMIT = 20
_STATE_LABELS = {
    "ready": "Results available",
    "empty": "No matching results",
    "not_collected": "No results yet",
    "source_unavailable": "Evidence source needs attention",
}
_STATUS_LABELS = {
    "observed": "Check completed",
    "failed": "Needs attention",
    "unknown": "Could not confirm",
}
_REASON_LABELS = {
    "verified": "Verified",
    "not_verified": "Not verified",
    "source_unavailable": "Source unavailable",
    "permission_denied": "Access not available",
    "incomplete": "Incomplete",
    "error": "Needs attention",
    "unknown": "Could not confirm",
}


class ReportIn(StrictInput):
    report: str = Field(min_length=1, max_length=300)


def _plain(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(part.capitalize() for part in re.split(r"[-_\s]+", value) if part)


def _authority(request):
    from . import grc_draft_changes
    from .app import require_page_visible

    auth.require_page_permission(request, PAGE)
    require_page_visible(request, PAGE)
    auth.require_page_permission(request, EVIDENCE_PAGE)
    require_page_visible(request, EVIDENCE_PAGE)
    if not auth.suite_allowed(request, "it_grc"):
        raise HTTPException(403, "Your current access does not include these saved reports.")
    try:
        evidence.require_enabled()
    except evidence.ContinuousEvidenceConfigError as exc:
        raise HTTPException(403, "Continuous evidence is turned off in this workspace.") from exc

    tenant = native._tenant_id()
    principal = native._principal(request, tenant)
    enabled = native._dependency(request, "grc_studio_enabled_probe", None)
    ready = native._dependency(request, "grc_studio_readiness_probe", None)
    factory = native._dependency(request, "grc_studio_store_factory", None)
    if not callable(enabled) or enabled(tenant) is not True:
        raise HTTPException(403, "The Compliance studio is turned off in this workspace.")
    if not callable(ready) or ready(tenant) is not True or not callable(factory):
        raise HTTPException(503, "The saved reports could not be checked safely. Open the Compliance studio.")
    try:
        store = factory(tenant_id=tenant, principal_ref=principal, request=request)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise HTTPException(503, "The saved reports could not be checked safely. Open the Compliance studio.") from exc
    if (
        getattr(store, "tenant_id", None) != tenant
        or not callable(getattr(store, "list_versions", None))
        or not callable(getattr(store, "get_active", None))
    ):
        raise HTTPException(503, "The saved reports could not be checked safely. Open the Compliance studio.")
    return tenant, store, grc_draft_changes.context(request)


def _report_choices(request, selection: Mapping[str, Any]):
    if selection:
        raise HTTPException(422, "Choose a report from the current workspace.")
    tenant, store, context = _authority(request)
    try:
        # Publication leaves only the active revision in the published state.
        versions = store.list_versions(tenant, state="published", limit=_REPORT_LIMIT + 1)
        more = len(versions) > _REPORT_LIMIT
        choices: list[dict[str, Any]] = []
        pins: list[tuple[str, int, str, str, str]] = []
        for version in versions:
            pointer, current = store.get_active(tenant, version.studio_id)
            if pointer.active_version != version.version:
                raise HTTPException(409, "A published report changed. Refresh Copilot before choosing it.")
            if (
                current.integrity_sha256 != version.integrity_sha256
                or current.state != "published"
            ):
                raise HTTPException(409, "A published report changed. Refresh Copilot before choosing it.")
            blueprint = current.blueprint
            linked_objects = {
                binding.get("object_id")
                for binding in blueprint.get("collector_bindings", ())
                if isinstance(binding, Mapping)
            }
            for view in blueprint.get("report_views", ()):
                if not isinstance(view, Mapping) or view.get("object_id") not in linked_objects:
                    continue
                if not isinstance(view.get("view_id"), str) or not view["view_id"]:
                    raise HTTPException(503, "A saved report could not be checked safely.")
                studio_id = str(version.studio_id)
                report_version = int(version.version)
                view_id = str(view["view_id"])
                value = json.dumps([studio_id, report_version, view_id], separators=(",", ":"))
                label = f"{_plain(blueprint.get('name_ref'))} · {_plain(view.get('title_ref'))} · version {report_version}"
                choices.append({"value": value, "label": label})
                pins.append((studio_id, report_version, view_id, pointer.integrity_sha256, current.integrity_sha256))
                if len(choices) > _REPORT_LIMIT:
                    more = True
                    break
            if len(choices) > _REPORT_LIMIT:
                break
        choices = choices[:_REPORT_LIMIT]
        pins = pins[:_REPORT_LIMIT]
    except HTTPException:
        raise
    except studio.GRCStudioError as exc:
        raise HTTPException(503, "The saved reports could not be checked safely. Open the Compliance studio.") from exc
    except (OSError, TypeError, ValueError) as exc:
        raise HTTPException(503, "The saved reports could not be checked safely. Open the Compliance studio.") from exc

    if _authority_context(request) != context:
        raise HTTPException(409, "Your account, workspace or access changed. Refresh Copilot before reading a report.")
    revision = digest({"tenant": tenant, "context": context, "reports": pins, "more": more})
    return revision, choices, more


def _authority_context(request):
    from . import grc_draft_changes

    return grc_draft_changes.context(request)


def snapshot(request, selection):
    revision, choices, more = _report_choices(request, selection)
    return Snapshot(
        revision,
        "Saved compliance report results",
        "Choose a published report to see its latest saved results. Lightwork will not collect or change anything.",
        details={
            "Reports shown": len(choices),
            "Some reports are not shown": more,
            "Scope": "Only reports and saved observations from your current workspace are shown. These results are not proof of compliance.",
        },
        fields=[
            {
                "name": "report",
                "label": "Published report",
                "kind": "select",
                "required": True,
                "placeholder": "Choose a report",
                "choices": choices,
            }
        ],
        blocked=(
            "No published reports with evidence links were found in this workspace. Publish a report and connect an approved evidence source in Compliance."
            if not choices
            else ""
        ),
    )


def _parse_report(value: str) -> tuple[str, int, str]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(422, "Choose a published report from the current list.") from exc
    if (
        not isinstance(decoded, list)
        or len(decoded) != 3
        or not isinstance(decoded[0], str)
        or not decoded[0]
        or type(decoded[1]) is not int
        or decoded[1] < 1
        or not isinstance(decoded[2], str)
        or not decoded[2]
    ):
        raise HTTPException(422, "Choose a published report from the current list.")
    return decoded[0], decoded[1], decoded[2]


def _report_values(result: Mapping[str, Any], title: str) -> dict[str, object]:
    all_columns = [item for item in result.get("columns", ()) if isinstance(item, Mapping)]
    columns = all_columns[:_COPILOT_COLUMN_LIMIT]

    def display_value(value: object, *, reason: bool = False) -> object:
        if isinstance(value, str):
            if value in _STATUS_LABELS:
                return _STATUS_LABELS[value]
            if value in _REASON_LABELS:
                return _REASON_LABELS[value]
            if reason:
                return _plain(value)
            return value
        if isinstance(value, list):
            return [display_value(item, reason=reason) for item in value]
        return value

    def display_timestamp(value: object) -> object:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return display_value(value)
        try:
            from datetime import datetime, timezone

            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", " UTC")
        except (OverflowError, OSError, ValueError):
            return "Time unavailable"

    def row_values(raw: object) -> list[object]:
        values = raw.get("values", {}) if isinstance(raw, Mapping) else {}
        if not isinstance(values, Mapping):
            return [None for _column in columns]
        display: list[object] = []
        for column in columns:
            field_id = column.get("field_id")
            value = values.get(field_id) if isinstance(field_id, str) else None
            is_reason = "reason" in str(column.get("label_ref") or "").casefold()
            display.append(
                display_timestamp(value)
                if column.get("field_type") == "timestamp"
                else display_value(value, reason=is_reason)
            )
        return display

    all_group_fields = [item for item in result.get("group_by", ()) if isinstance(item, Mapping)]
    group_fields = all_group_fields[:_COPILOT_COLUMN_LIMIT - 1]
    all_groups = [item for item in result.get("groups", ()) if isinstance(item, Mapping)]
    groups = []
    for item in all_groups[:_COPILOT_GROUP_LIMIT]:
        if not isinstance(item, Mapping) or not isinstance(item.get("values"), Mapping):
            continue
        values = item["values"]
        groups.append([
            display_timestamp(values.get(str(field.get("field_id"))))
            if field.get("field_type") == "timestamp"
            else display_value(
                values.get(str(field.get("field_id"))),
                reason="reason" in str(field.get("label_ref") or "").casefold(),
            )
            for field in group_fields
        ] + [item.get("count", 0)])

    state = str(result.get("state", "source_unavailable"))
    all_rows = [item for item in result.get("rows", ()) if isinstance(item, Mapping)]
    all_sources = [item for item in result.get("sources", ()) if isinstance(item, str)]
    matched_count = result.get("matched_row_count", 0)
    return {
        "title": title,
        "status": _STATE_LABELS.get(state, "Results could not be confirmed"),
        "matching_results": matched_count,
        "columns": [_plain(item.get("label_ref")) or "Report detail" for item in columns],
        "rows": [row_values(row) for row in all_rows[:_COPILOT_ROW_LIMIT]],
        "group_columns": [_plain(item.get("label_ref")) or "Report detail" for item in group_fields],
        "groups": groups,
        "sources": all_sources[:_COPILOT_SOURCE_LIMIT],
        "note": result.get("note", "Read-only results from saved evidence."),
        "more_rows": bool(result.get("truncated")) or matched_count > len(all_rows[:_COPILOT_ROW_LIMIT]),
        "more_columns": len(all_columns) > _COPILOT_COLUMN_LIMIT,
        "more_groups": len(all_groups) > _COPILOT_GROUP_LIMIT or len(all_group_fields) > len(group_fields),
        "more_sources": len(all_sources) > _COPILOT_SOURCE_LIMIT,
    }


def prepare(request, selection, arguments):
    report_id = arguments["report"]
    studio_id, version_number, view_id = _parse_report(report_id)
    tenant, store, _context = _authority(request)
    result = preview_published_report(
        request=request,
        tenant=tenant,
        studio_store=store,
        studio_id=studio_id,
        version_number=version_number,
        view_id=view_id,
        limit=_COPILOT_ROW_LIMIT,
        access_channel="copilot",
    )
    title = (
        f"{_plain(result.get('studio_name_ref'))} · {_plain(result.get('title_ref'))} · "
        f"version {version_number}"
    )
    details = {"grc_published_report": _report_values(result, title)}
    summary = str(result.get("note") or "Read-only results from saved technical observations.")
    return Preview(summary, native=details)


def entries():
    return (
        Adapter(
            "grc.report.read",
            "Read results from a published compliance report",
            "artifacts",
            PAGE,
            (PAGE,),
            ReportIn,
            snapshot,
            prepare,
            aliases=(
                "show compliance report results",
                "read a published compliance report",
                "show the latest saved evidence report",
                "open a GRC report",
            ),
        ),
    )
