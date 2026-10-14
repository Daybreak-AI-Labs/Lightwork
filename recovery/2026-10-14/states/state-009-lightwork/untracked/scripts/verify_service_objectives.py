#!/usr/bin/env python3
"""Validate the deployment-specific service-objective release contract."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA = "lightwork.service-objectives.v1"
REQUIRED_OBJECTIVES = {
    "availability": ("percent", ">="),
    "api_latency_p95_ms": ("milliseconds", "<="),
    "recovery_time_seconds": ("seconds", "<="),
    "recovery_point_seconds": ("seconds", "<="),
    "data_durability_percent": ("percent", ">="),
    "support_response_sev1_seconds": ("seconds", "<="),
    "audit_anchor_lag_seconds": ("seconds", "<="),
}
TOP_LEVEL_KEYS = {
    "schema",
    "profile_id",
    "source_version",
    "status",
    "objectives",
    "error_budget_policy",
    "approval",
    "limitations",
}
OBJECTIVE_KEYS = {
    "id",
    "unit",
    "comparator",
    "target",
    "window",
    "sli",
    "measurement_source",
    "owner",
    "exclusions",
}
_EVIDENCE_REFERENCE = re.compile(
    r"(?:repository|external-evidence):[^#\s]{1,200}#sha256=[0-9a-f]{64}"
)


class ServiceObjectiveError(ValueError):
    """The service-objective contract is malformed or not release-authorized."""


def _mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ServiceObjectiveError(f"{label} must be an object")
    return value


def _exact(value: object, keys: set[str], *, label: str) -> Mapping[str, Any]:
    result = _mapping(value, label=label)
    if set(result) != keys:
        raise ServiceObjectiveError(f"{label} fields are not the exact schema")
    return result


def _text(value: object, *, label: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ServiceObjectiveError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise ServiceObjectiveError(f"{label} must not have edge whitespace")
    return value


def _read(path: Path, *, label: str) -> Mapping[str, Any]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), label=label)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceObjectiveError(f"{label} is unavailable or malformed") from exc


def _timestamp(value: object, *, label: str) -> dt.datetime:
    text = _text(value, label=label)
    if not text.endswith("Z"):
        raise ServiceObjectiveError(f"{label} must be an exact UTC timestamp")
    try:
        parsed = dt.datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise ServiceObjectiveError(f"{label} must be an exact UTC timestamp") from exc
    if parsed.tzinfo != dt.timezone.utc:
        raise ServiceObjectiveError(f"{label} must be an exact UTC timestamp")
    return parsed


def _target(value: object, *, objective_id: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ServiceObjectiveError(f"{objective_id} target must be numeric or null")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ServiceObjectiveError(f"{objective_id} target must be positive and finite")
    if REQUIRED_OBJECTIVES[objective_id][0] == "percent" and result > 100:
        raise ServiceObjectiveError(f"{objective_id} percent target cannot exceed 100")
    return result


def validate_service_objectives(  # noqa: C901 - exact schema/cross-field gate
    root: Path,
    *,
    release_candidate: bool = False,
    now: dt.datetime | None = None,
) -> dict[str, object]:
    root = root.resolve()
    contract = _exact(
        _read(root / "release" / "service-objectives.json", label="service objectives"),
        TOP_LEVEL_KEYS,
        label="service objectives",
    )
    surface = _read(
        root / "release" / "production-surface-manifest.json",
        label="production surface",
    )
    release_state = _read(
        root / "release" / "release-state.json",
        label="release state",
    )
    if contract["schema"] != SCHEMA:
        raise ServiceObjectiveError("service-objective schema is unsupported")
    profile_id = _text(contract["profile_id"], label="service-objective profile")
    version = _text(contract["source_version"], label="service-objective version")
    if profile_id != surface.get("profile_id"):
        raise ServiceObjectiveError("service objectives target a different profile")
    if version != surface.get("source_version") or version != release_state.get(
        "source_version"
    ):
        raise ServiceObjectiveError("service-objective version drifted")
    status = contract["status"]
    if status not in {"draft", "approved"}:
        raise ServiceObjectiveError("service-objective status is invalid")

    objectives = contract["objectives"]
    if not isinstance(objectives, list):
        raise ServiceObjectiveError("service objectives must be an array")
    seen: set[str] = set()
    unset: list[str] = []
    for index, raw in enumerate(objectives):
        objective = _exact(raw, OBJECTIVE_KEYS, label=f"service objective {index}")
        objective_id = _text(objective["id"], label=f"service objective {index} id")
        if objective_id not in REQUIRED_OBJECTIVES or objective_id in seen:
            raise ServiceObjectiveError(f"unknown or duplicate objective: {objective_id}")
        seen.add(objective_id)
        expected_unit, expected_comparator = REQUIRED_OBJECTIVES[objective_id]
        if objective["unit"] != expected_unit or objective["comparator"] != expected_comparator:
            raise ServiceObjectiveError(f"{objective_id} unit or comparator drifted")
        target = _target(objective["target"], objective_id=objective_id)
        if target is None:
            unset.append(objective_id)
        _text(objective["window"], label=f"{objective_id} window")
        _text(objective["sli"], label=f"{objective_id} SLI")
        measurement_source = _text(
            objective["measurement_source"],
            label=f"{objective_id} measurement source",
            allow_empty=status == "draft",
        )
        if status == "approved" and _EVIDENCE_REFERENCE.fullmatch(
            measurement_source
        ) is None:
            raise ServiceObjectiveError(
                f"{objective_id} measurement source is not immutable evidence"
            )
        _text(objective["owner"], label=f"{objective_id} owner")
        exclusions = objective["exclusions"]
        if not isinstance(exclusions, list) or any(
            not isinstance(item, str) or not item.strip() for item in exclusions
        ):
            raise ServiceObjectiveError(f"{objective_id} exclusions must be strings")
    if seen != set(REQUIRED_OBJECTIVES):
        raise ServiceObjectiveError("service-objective inventory is incomplete")

    budget = _exact(
        contract["error_budget_policy"],
        {
            "evaluation_window",
            "freeze_on_exhaustion",
            "incident_review_required",
            "exception_authority",
        },
        label="error-budget policy",
    )
    _text(budget["evaluation_window"], label="error-budget evaluation window")
    if budget["freeze_on_exhaustion"] is not True:
        raise ServiceObjectiveError("error-budget exhaustion must freeze risky releases")
    if budget["incident_review_required"] is not True:
        raise ServiceObjectiveError("error-budget incidents require review")
    exception_authority = budget["exception_authority"]
    if exception_authority is not None:
        _text(exception_authority, label="error-budget exception authority")

    approval = _exact(
        contract["approval"],
        {"approved_at", "expires_at", "approvers"},
        label="service-objective approval",
    )
    limitations = contract["limitations"]
    if not isinstance(limitations, list) or not limitations or any(
        not isinstance(item, str) or not item.strip() for item in limitations
    ):
        raise ServiceObjectiveError("service-objective limitations are required")

    if status == "draft":
        if any(approval[key] is not None for key in ("approved_at", "expires_at")):
            raise ServiceObjectiveError("draft service objectives cannot carry approval dates")
        if approval["approvers"] != []:
            raise ServiceObjectiveError("draft service objectives cannot carry approvers")
    else:
        if unset:
            raise ServiceObjectiveError(
                f"approved service objectives have unset targets: {sorted(unset)!r}"
            )
        if exception_authority is None:
            raise ServiceObjectiveError("approved objectives need an exception authority")
        approved_at = _timestamp(approval["approved_at"], label="approved_at")
        expires_at = _timestamp(approval["expires_at"], label="expires_at")
        if expires_at <= approved_at or expires_at - approved_at > dt.timedelta(days=366):
            raise ServiceObjectiveError("service-objective approval window is invalid")
        current = now or dt.datetime.now(dt.timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=dt.timezone.utc)
        current = current.astimezone(dt.timezone.utc)
        if approved_at > current:
            raise ServiceObjectiveError("service-objective approval is future-dated")
        if expires_at <= current:
            raise ServiceObjectiveError("service-objective approval is expired")
        approvers = approval["approvers"]
        if not isinstance(approvers, list) or len(approvers) < 2:
            raise ServiceObjectiveError("service objectives need at least two approvers")
        roles: set[str] = set()
        principals: set[str] = set()
        for index, raw in enumerate(approvers):
            item = _exact(raw, {"principal", "role"}, label=f"approver {index}")
            principal = _text(item["principal"], label=f"approver {index} principal")
            role = _text(item["role"], label=f"approver {index} role")
            if principal in principals:
                raise ServiceObjectiveError("service-objective approvers must be distinct")
            principals.add(principal)
            roles.add(role)
        if not {"service-owner", "site-reliability"}.issubset(roles):
            raise ServiceObjectiveError(
                "service objectives require service-owner and site-reliability approval"
            )

    if release_candidate and status != "approved":
        raise ServiceObjectiveError("release candidate service objectives are not approved")
    return {
        "profile_id": profile_id,
        "source_version": version,
        "status": status,
        "objective_count": len(seen),
        "unset_targets": len(unset),
        "release_candidate": release_candidate,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-candidate", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = validate_service_objectives(
            args.root,
            release_candidate=args.release_candidate,
        )
    except ServiceObjectiveError as exc:
        parser.exit(1, f"service-objectives: ERROR: {exc}\n")
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
