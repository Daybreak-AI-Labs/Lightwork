"""Read-only execution for published Studio reports over saved evidence."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from fastapi import HTTPException, Request

_MAX_ENROLLMENTS = 64
_MAX_OBSERVATIONS = 1_000
_MAX_RUNS = 1_000
_MAX_CONFIGURED_SOURCES = 500
_SUPPORTED_OUTPUTS = frozenset(
    {"status", "reason_code", "evidence_sha256", "source_revision_sha256", "observed_at"}
)


def _field_value(field: Mapping[str, Any], observation: object, source_path: object) -> object:
    if not isinstance(source_path, list) or len(source_path) != 1:
        return None
    name = source_path[0]
    if not isinstance(name, str) or name not in _SUPPORTED_OUTPUTS:
        return None
    value = getattr(observation, name, None)
    field_type = field.get("field_type")
    if name in {"status", "reason_code"}:
        if field_type in {"text_ref", "single_choice"}:
            return value
        if field_type == "multi_choice" and value in field.get("options", ()):
            return [value]
        return None
    if name in {"evidence_sha256", "source_revision_sha256"}:
        return value if field_type in {"digest", "text_ref"} else None
    if name == "observed_at":
        return value if field_type == "timestamp" else None
    return None


def _matches(row: Mapping[str, object], filters: Sequence[Mapping[str, object]]) -> bool:
    for item in filters:
        actual = row.get(str(item["field_id"]))
        operator = item["operator"]
        expected = item["value"]
        if operator == "is_set":
            if actual is None:
                return False
            continue
        if actual is None:
            return False
        try:
            if operator == "equals" and actual != expected:
                return False
            if operator == "not_equals" and actual == expected:
                return False
            if operator == "contains" and (
                not isinstance(actual, (list, tuple)) or expected not in actual
            ):
                return False
            if operator == "greater_than" and not actual > expected:  # type: ignore[operator]
                return False
            if operator == "greater_or_equal" and not actual >= expected:  # type: ignore[operator]
                return False
            if operator == "less_than" and not actual < expected:  # type: ignore[operator]
                return False
            if operator == "less_or_equal" and not actual <= expected:  # type: ignore[operator]
                return False
        except (TypeError, ValueError):
            return False
    return True


def _sort_key(value: object) -> tuple[int, int | float | str]:
    if value is None:
        return (1, "")
    if isinstance(value, bool):
        return (0, int(value))
    if isinstance(value, int | float):
        return (0, value)
    return (0, str(value).casefold())


def _visible_fields(
    request: Request, fields: Sequence[Mapping[str, Any]]
) -> dict[str, Mapping[str, Any]]:
    from .auth import has_global_permission

    may_view_confidential = has_global_permission(request, "admin") or has_global_permission(
        request, "audit"
    )
    visible: dict[str, Mapping[str, Any]] = {}
    for field in fields:
        field_id = field.get("field_id")
        if not isinstance(field_id, str):
            raise HTTPException(503, "This report could not be checked safely.")
        sensitivity = field.get("sensitivity")
        if sensitivity == "restricted" or (
            sensitivity == "confidential" and not may_view_confidential
        ):
            continue
        visible[field_id] = field
    return visible


def _current_evidence_sources(
    request: Request,
    tenant: str,
    bindings: Sequence[Mapping[str, Any]],
) -> tuple[Any, list[tuple[Mapping[str, Any], Mapping[str, Any], Any]], int]:
    """Resolve only the exact currently approved source and enrollment records."""

    from . import auth, trc_setup_api
    from .app import require_page_visible

    auth.require_page_permission(request, "/compliance/continuous-evidence")
    require_page_visible(request, "/compliance/continuous-evidence")
    if not auth.suite_allowed(request, "it_grc"):
        raise HTTPException(403, "Your current access does not include these evidence records.")

    evidence, selected_tenant, evidence_store = trc_setup_api._store(request)
    if selected_tenant != tenant:
        raise HTTPException(409, "Your workspace changed. Refresh before opening this report.")
    plans = trc_setup_api._manifests(request, tenant)
    if len(plans) > _MAX_CONFIGURED_SOURCES:
        raise HTTPException(503, "Too many saved evidence sources are configured to open this report safely.")
    resolved: list[tuple[Mapping[str, Any], Mapping[str, Any], Any]] = []
    unavailable = 0
    for binding in bindings:
        matching = [
            (key, plan)
            for key, plan in plans.items()
            if isinstance(plan, Mapping)
            and plan.get("collector_id") == binding.get("collector_id")
            and plan.get("definition_version") == binding.get("collector_version")
            and plan.get("definition_id")
        ]
        exact = False
        for _key, plan in matching:
            if plan.get("credential_ready") is not True:
                continue
            try:
                definition = evidence_store.get_definition(
                    str(plan["definition_id"]), int(plan["definition_version"])
                )
                if (
                    definition.state != "approved"
                    or definition.collector_id != binding.get("collector_id")
                    or definition.integrity_sha256 != binding.get("collector_integrity_sha256")
                ):
                    continue
                enrollment = evidence_store.get_enrollment(str(plan["enrollment_id"]))
            except evidence.ContinuousEvidenceNotFound:
                continue
            if (
                enrollment.state != "active"
                or enrollment.definition_id != definition.definition_id
                or enrollment.definition_version != definition.version
                or enrollment.definition_integrity_sha256 != definition.integrity_sha256
                or enrollment.collector_id != definition.collector_id
                or enrollment.asset_scope_sha256 != plan.get("asset_scope_sha256")
                or enrollment.credential_profile_sha256 != plan.get("credential_profile_sha256")
                or not set(binding.get("control_refs", ())) <= set(enrollment.control_refs)
                or not set(binding.get("control_refs", ())) <= set(definition.allowed_control_refs)
            ):
                continue
            resolved.append((binding, plan, enrollment))
            if len(resolved) > _MAX_ENROLLMENTS:
                return evidence_store, resolved[:_MAX_ENROLLMENTS], unavailable + 1
            exact = True
        if not exact:
            unavailable += 1
    return evidence_store, resolved, unavailable


def _rows_from_observations(
    evidence_store: Any,
    resolved: Sequence[tuple[Mapping[str, Any], Mapping[str, Any], Any]],
    fields: Sequence[Mapping[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, object]], set[str], bool]:
    rows_by_observation: dict[tuple[str, str], dict[str, object]] = {}
    source_labels = {str(plan.get("label") or "Evidence source") for _binding, plan, _enrollment in resolved}
    current_runs: dict[str, Any] = {}
    runs_scanned = 0
    scanned = 0
    truncated = False
    object_fields = {
        str(item.get("field_id")): item
        for item in fields
        if isinstance(item, Mapping) and isinstance(item.get("field_id"), str)
    }
    for binding, _plan, enrollment in resolved:
        if runs_scanned >= _MAX_RUNS:
            truncated = True
            break
        requested_runs = min(1_000, _MAX_RUNS - runs_scanned)
        recent_runs = evidence_store.list_runs(
            enrollment_id=enrollment.enrollment_id, limit=requested_runs
        )
        runs_scanned += len(recent_runs)
        if len(recent_runs) >= requested_runs:
            truncated = True
        current_runs = {
            run.run_id: run
            for run in recent_runs
            if run.state == "completed" and run.complete is True and run.retained_until > time.time()
        }
        if scanned >= _MAX_OBSERVATIONS:
            truncated = True
            break
        requested = min(200, _MAX_OBSERVATIONS - scanned)
        observations = evidence_store.list_observations(
            enrollment_id=enrollment.enrollment_id,
            limit=requested,
        )
        scanned += len(observations)
        if len(observations) >= requested:
            truncated = True
        allowed_controls = set(binding.get("control_refs", ()))
        for observation in observations:
            if (
                observation.control_ref not in allowed_controls
                or observation.retained_until <= time.time()
            ):
                continue
            run = current_runs.get(observation.run_id)
            if (
                run is None
                or run.enrollment_id != enrollment.enrollment_id
                or run.enrollment_revision != enrollment.revision
                or run.definition_id != enrollment.definition_id
                or run.definition_version != enrollment.definition_version
                or run.collector_id != enrollment.collector_id
            ):
                continue
            key = (enrollment.enrollment_id, observation.observation_id)
            row = rows_by_observation.setdefault(key, {})
            for mapping in binding.get("field_mappings", ()):
                if not isinstance(mapping, Mapping):
                    continue
                field_id = mapping.get("target_field_id")
                field = object_fields.get(str(field_id))
                if field is None:
                    continue
                value = _field_value(field, observation, mapping.get("source_path"))
                if value is None:
                    continue
                if field.get("field_type") == "single_choice" and value not in field.get(
                    "options", ()
                ):
                    continue
                if field_id in row and row[field_id] != value:
                    if (
                        mapping.get("mode") == "append"
                        and field.get("field_type") == "multi_choice"
                    ):
                        existing = row[field_id]
                        existing_values = existing if isinstance(existing, list) else [existing]
                        new_values = value if isinstance(value, list) else [value]
                        row[field_id] = list(dict.fromkeys([*existing_values, *new_values]))
                    else:
                        raise HTTPException(
                            409,
                            "Two evidence links give different answers for the same report field. Use separate fields before publishing.",
                        )
                else:
                    row[str(field_id)] = value
    return rows_by_observation, source_labels, truncated


def _run_report_definition(
    rows_by_observation: Mapping[tuple[str, str], Mapping[str, object]],
    view: Mapping[str, Any],
    limit: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]], int, bool]:
    report_rows = [
        dict(row) for row in rows_by_observation.values() if _matches(row, view.get("filters", ()))
    ]
    for item in reversed(view.get("sort", ())):
        field_id = str(item["field_id"])
        reverse = item["direction"] == "descending"
        present = [row for row in report_rows if row.get(field_id) is not None]
        missing = [row for row in report_rows if row.get(field_id) is None]
        present.sort(key=lambda row: _sort_key(row[field_id]), reverse=reverse)
        report_rows = present + missing

    group_ids = tuple(str(field_id) for field_id in view.get("group_by", ()))
    groups: list[dict[str, object]] = []
    if group_ids:
        counts: Counter[tuple[object, ...]] = Counter(
            tuple(
                tuple(value) if isinstance(value, list) else value
                for value in (row.get(field_id) for field_id in group_ids)
            )
            for row in report_rows
        )
        groups = [
            {
                "values": {
                    field_id: list(value) if isinstance(value, tuple) else value
                    for field_id, value in zip(group_ids, key, strict=True)
                },
                "count": count,
            }
            for key, count in sorted(
                counts.items(), key=lambda item: tuple(_sort_key(value) for value in item[0])
            )
        ]
    matched_count = len(report_rows)
    truncated = matched_count > limit
    limited_rows = report_rows[:limit]
    column_ids = tuple(str(field_id) for field_id in view.get("columns", ()))
    return (
        [{field_id: row.get(field_id) for field_id in column_ids} for row in limited_rows],
        groups,
        matched_count,
        truncated,
    )


def _validate_report_mappings(
    bindings: Sequence[Mapping[str, Any]],
    visible_fields: Mapping[str, Mapping[str, Any]],
    used_fields: set[object],
) -> None:
    allowed_field_types = {
        "status": {"text_ref", "single_choice", "multi_choice"},
        "reason_code": {"text_ref"},
        "evidence_sha256": {"digest", "text_ref"},
        "source_revision_sha256": {"digest", "text_ref"},
        "observed_at": {"timestamp"},
    }
    for binding in bindings:
        for mapping in binding.get("field_mappings", ()):
            if not isinstance(mapping, Mapping) or mapping.get("target_field_id") not in used_fields:
                continue
            field = visible_fields.get(str(mapping.get("target_field_id")))
            path = mapping.get("source_path")
            if (
                field is None
                or not isinstance(path, list)
                or len(path) != 1
                or path[0] not in _SUPPORTED_OUTPUTS
                or field.get("field_type") not in allowed_field_types.get(str(path[0]), set())
                or path[0] == "status"
                and field.get("field_type") in {"single_choice", "multi_choice"}
                and not {"observed", "failed", "unknown"} <= set(field.get("options", ()))
            ):
                raise HTTPException(
                    409,
                    "This report includes a detail that cannot be read from saved evidence yet. Update its evidence link and publish the design again.",
                )


def preview_published_report(
    *,
    request: Request,
    tenant: str,
    studio_store: Any,
    studio_id: str,
    version_number: int,
    view_id: str,
    limit: int = 200,
    access_channel: Literal["native", "copilot"] = "native",
) -> dict[str, object]:
    """Run one active published view against currently available observations."""

    from maverick import trc_continuous_evidence as evidence

    from . import auth, grc_draft_changes, trc_setup_api

    auth.require_page_permission(request, "/compliance/grc-studio")
    from .app import require_page_visible

    require_page_visible(request, "/compliance/grc-studio")
    context = grc_draft_changes.context(request)
    pointer, version = studio_store.get_active(tenant, studio_id)
    if (
        pointer.active_version != version_number
        or version.version != version_number
        or version.state != "published"
    ):
        raise HTTPException(
            409, "This report version is no longer active. Refresh the design and try again."
        )

    blueprint = version.blueprint
    view = next(
        (item for item in blueprint.get("report_views", ()) if item.get("view_id") == view_id),
        None,
    )
    if not isinstance(view, Mapping):
        raise HTTPException(404, "This report is not part of the active design.")
    object_id = view.get("object_id")
    object_definition = next(
        (item for item in blueprint.get("objects", ()) if item.get("object_id") == object_id),
        None,
    )
    if not isinstance(object_definition, Mapping):
        raise HTTPException(503, "This report's record type could not be checked safely.")
    bindings = [
        item
        for item in blueprint.get("collector_bindings", ())
        if isinstance(item, Mapping) and item.get("object_id") == object_id
    ]
    if not bindings:
        raise HTTPException(
            409,
            "This report has no saved evidence link yet. Add one to this record type and publish the new design.",
        )

    fields = object_definition.get("fields", ())
    visible_fields = _visible_fields(request, fields)
    used_fields = set(view.get("columns", ()))
    used_fields.update(item.get("field_id") for item in view.get("filters", ()))
    used_fields.update(view.get("group_by", ()))
    used_fields.update(item.get("field_id") for item in view.get("sort", ()))
    if not used_fields <= set(visible_fields):
        raise HTTPException(
            403, "This report uses details that are not available to your current role."
        )

    _validate_report_mappings(bindings, visible_fields, used_fields)

    evidence_store, resolved, unavailable = _current_evidence_sources(request, tenant, bindings)
    if len(resolved) > _MAX_ENROLLMENTS:
        resolved = resolved[:_MAX_ENROLLMENTS]
        unavailable += 1
    selected_limit = max(1, min(int(limit), 500))
    if resolved:
        rows_by_observation, source_labels, truncated = _rows_from_observations(
            evidence_store,
            sorted(
                resolved,
                key=lambda item: (str(item[1].get("label") or ""), str(item[2].enrollment_id)),
            ),
            tuple(visible_fields.values()),
        )
    else:
        rows_by_observation, source_labels, truncated = {}, set(), False
    display_rows, groups, matched_row_count, rows_truncated = _run_report_definition(
        rows_by_observation, view, selected_limit
    )
    truncated = truncated or rows_truncated
    group_ids = tuple(str(field_id) for field_id in view.get("group_by", ()))
    report_rows = display_rows

    current_plans = trc_setup_api._manifests(request, tenant)
    for _binding, plan, enrollment in resolved:
        latest = current_plans.get(str(plan.get("enrollment_id")))
        plan_fields = (
            "definition_id",
            "definition_version",
            "collector_id",
            "enrollment_id",
            "credential_ready",
            "asset_scope_sha256",
            "credential_profile_sha256",
        )
        if not isinstance(latest, Mapping) or any(
            latest.get(key) != plan.get(key) for key in plan_fields
        ):
            raise HTTPException(
                409,
                "An evidence source changed while this report was opening. Refresh and try again.",
            )
        try:
            current_enrollment = evidence_store.get_enrollment(enrollment.enrollment_id)
            current_definition = evidence_store.get_definition(
                enrollment.definition_id, enrollment.definition_version
            )
        except evidence.ContinuousEvidenceError as exc:
            raise HTTPException(
                409,
                "An evidence source changed while this report was opening. Refresh and try again.",
            ) from exc
        if (
            current_enrollment.integrity_sha256 != enrollment.integrity_sha256
            or current_enrollment.state != "active"
            or current_definition.integrity_sha256 != enrollment.definition_integrity_sha256
            or current_definition.state != "approved"
        ):
            raise HTTPException(
                409,
                "An evidence source changed while this report was opening. Refresh and try again.",
            )

    if unavailable:
        note = "Some saved evidence links are no longer available. Review their current setup before relying on this report."
        state = "source_unavailable"
    elif not resolved:
        note = "No active evidence collection is connected to this report yet. Set up and run a saved evidence source first."
        state = "source_unavailable"
    elif not rows_by_observation:
        note = "No retained results are available yet. Run an evidence check to fill this report."
        state = "not_collected"
    elif not report_rows:
        note = "No saved results match the conditions in this report."
        state = "empty"
    else:
        note = "Read-only results from saved technical observations. These results are not a compliance conclusion."
        state = "ready"
    if truncated:
        note += " Showing a bounded recent sample; open Evidence collection to review the complete retained history."

    current_pointer, current_version = studio_store.get_active(tenant, studio_id)
    if (
        current_pointer.integrity_sha256 != pointer.integrity_sha256
        or current_version.integrity_sha256 != version.integrity_sha256
    ):
        raise HTTPException(
            409, "This design changed while the report was opening. Refresh and try again."
        )

    result = {
        "schema": "lightwork.grc-studio-report-preview.v1",
        "studio_id": studio_id,
        "version": version_number,
        "view_id": view_id,
        "studio_name_ref": blueprint.get("name_ref"),
        "title_ref": view["title_ref"],
        "state": state,
        "row_count": len(report_rows),
        "matched_row_count": matched_row_count,
        "columns": [
            {
                "field_id": field_id,
                "label_ref": visible_fields[field_id]["label_ref"],
                "field_type": visible_fields[field_id]["field_type"],
            }
            for field_id in view.get("columns", ())
        ],
        "rows": [{"values": row} for row in display_rows],
        "group_by": [
            {
                "field_id": field_id,
                "label_ref": visible_fields[field_id]["label_ref"],
                "field_type": visible_fields[field_id]["field_type"],
            }
            for field_id in group_ids
        ],
        "groups": groups,
        "sources": sorted(source_labels),
        "truncated": truncated,
        "read_only": True,
        "note": note,
    }
    auth.require_page_permission(request, "/compliance/continuous-evidence")
    require_page_visible(request, "/compliance/continuous-evidence")
    if not auth.suite_allowed(request, "it_grc"):
        raise HTTPException(403, "Your current access does not include these evidence records.")
    auth.require_page_permission(request, "/compliance/grc-studio")
    if grc_draft_changes.context(request) != context:
        raise HTTPException(
            409, "Your account, workspace or access changed. Refresh before reading this report."
        )
    _record_report_access(
        request,
        tenant=tenant,
        studio_id=studio_id,
        version_number=version_number,
        view_id=view_id,
        state=state,
        source_count=len(source_labels),
        matched_row_count=matched_row_count,
        returned_row_count=len(display_rows),
        access_channel=access_channel,
    )
    return result


def _record_report_access(
    request: Request,
    *,
    tenant: str,
    studio_id: str,
    version_number: int,
    view_id: str,
    state: str,
    source_count: int,
    matched_row_count: int,
    returned_row_count: int,
    access_channel: Literal["native", "copilot"],
) -> None:
    from maverick import audit
    from maverick.audit import AuditRefused

    from . import auth

    try:
        recorded = audit.audit_event(
            "control_plane_action",
            agent="dashboard",
            action="grc_report_preview_returned",
            actor=auth.caller_principal(request) or "local",
            tenant=tenant,
            status=state,
            studio_id=studio_id,
            studio_version=version_number,
            report_view_id=view_id,
            source_count=source_count,
            matched_row_count=matched_row_count,
            returned_row_count=returned_row_count,
            access_channel=access_channel,
        )
    except AuditRefused as exc:
        raise HTTPException(
            503, "The activity log refused to record this report access. The results were not shown."
        ) from exc
    if recorded is not True:
        raise HTTPException(
            503, "The report access record could not be confirmed. The results were not shown."
        )


__all__ = ["preview_published_report"]
