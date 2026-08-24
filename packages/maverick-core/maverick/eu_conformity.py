"""EU AI Act conformity workflows: FRIA (Art. 27), Annex IV, Art. 43 route.

Three human-gated registers over governed CAS records:

* **FRIA** — the Art. 27(1) fundamental-rights impact assessment as six named
  assertions ((a) processes, (b) period/frequency, (c) affected categories,
  (d) risks of harm, (e) oversight implementation, (f) materialisation
  measures). Completion requires every element and a named assessor; the
  Art. 27(2) update obligation is a ``mark_outdated`` transition that reopens
  the record; the Art. 27(3) notification to the market surveillance
  authority is recorded as a human submission — the platform never transmits.
* **Annex IV** — the technical documentation of Art. 11(1) as a nine-item
  checklist. Each item is asserted by a named human with a reference to
  where the documentation lives; the module renders a bounded skeleton with
  citations, never fabricated content.
* **Art. 43 route** — the conformity-assessment route as a validated human
  decision: Annex III point 1 may use internal control (Annex VI) only when
  harmonised standards (Art. 40) or common specifications (Art. 41) were
  applied, and otherwise requires a notified body (Annex VII); points 2-8
  use internal control; Annex I products follow their sectoral acts. A
  substantial modification reopens the assessment (Art. 43(4)); provider
  pre-determined changes documented in the technical documentation do not.

Everything here is decision support and drafting assistance for the
provider's or deployer's own obligations. It is not a conformity assessment,
a CE marking, a notified-body opinion, or legal advice.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping
from typing import Any

from .governed_records import GovernedRecordStore

REGULATION_METADATA = {
    "regulation": "Regulation (EU) 2024/1689",
    "consolidated": "2026-07-27",
}
NON_CERTIFICATION_NOTICE = (
    "Drafting and tracking assistance for the operator's own EU AI Act "
    "obligations; not a conformity assessment, CE marking, notified-body "
    "opinion, or legal advice."
)
FRIA_SCHEMA = "lightwork.eu-fria.v1"
ANNEX_IV_SCHEMA = "lightwork.eu-annex-iv.v1"
ROUTE_SCHEMA = "lightwork.eu-art43-route.v1"

FRIA_ELEMENTS = {
    "process_description": {
        "citation": "Art. 27(1)(a)",
        "prompt": "The deployer's processes in which the high-risk AI system "
                  "will be used, in line with its intended purpose.",
    },
    "period_frequency": {
        "citation": "Art. 27(1)(b)",
        "prompt": "The period of time within which, and the frequency with "
                  "which, the system is intended to be used.",
    },
    "affected_categories": {
        "citation": "Art. 27(1)(c)",
        "prompt": "The categories of natural persons and groups likely to be "
                  "affected by its use in the specific context.",
    },
    "risks_of_harm": {
        "citation": "Art. 27(1)(d)",
        "prompt": "The specific risks of harm likely to impact the identified "
                  "categories, taking the provider's instructions into account.",
    },
    "oversight_measures": {
        "citation": "Art. 27(1)(e)",
        "prompt": "The implementation of human oversight measures, according "
                  "to the instructions for use.",
    },
    "materialisation_measures": {
        "citation": "Art. 27(1)(f)",
        "prompt": "The measures to be taken where those risks materialise, "
                  "including internal governance and complaint mechanisms.",
    },
}

ANNEX_IV_ITEMS = {
    "general_description": ("Annex IV(1)", "A general description of the AI system."),
    "development_process": (
        "Annex IV(2)",
        "A detailed description of the elements of the AI system and of the "
        "process for its development.",
    ),
    "monitoring_control": (
        "Annex IV(3)",
        "Detailed information about the monitoring, functioning and control "
        "of the AI system.",
    ),
    "performance_metrics": (
        "Annex IV(4)",
        "A description of the appropriateness of the performance metrics.",
    ),
    "risk_management": (
        "Annex IV(5)",
        "A detailed description of the risk management system in accordance "
        "with Article 9.",
    ),
    "lifecycle_changes": (
        "Annex IV(6)",
        "A description of relevant changes made by the provider through the "
        "system's lifecycle.",
    ),
    "standards_applied": (
        "Annex IV(7)",
        "A list of the harmonised standards applied in full or in part, or "
        "the alternative solutions adopted.",
    ),
    "declaration_of_conformity": (
        "Annex IV(8)",
        "A copy of the EU declaration of conformity referred to in Article 47.",
    ),
    "post_market_plan": (
        "Annex IV(9)",
        "A detailed description of the post-market performance evaluation "
        "system, including the Article 72 monitoring plan.",
    ),
}

ROUTES = ("internal_control_annex_vi", "notified_body_annex_vii", "sectoral_annex_i")

_FRIA = GovernedRecordStore("eu_fria", "ECF", "eu_fria_assessment")
_ANNEX_IV = GovernedRecordStore("eu_annex_iv", "ECD", "eu_annex_iv_checklist")
_ROUTES = GovernedRecordStore("eu_art43_routes", "ECA", "eu_art43_route")

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}\Z")
_MAX_LIST = 500


class EUConformityError(RuntimeError):
    """Base error for the conformity workflows."""


class EUConformityConfigError(EUConformityError):
    """The [eu_conformity] configuration is missing, malformed, or disabled."""


def _sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _token(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value.strip()):
        raise ValueError(f"{label} must be a bounded identifier token")
    return value.strip()


def _text(value: Any, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    out = value.strip()
    if len(out) > limit:
        raise ValueError(f"{label} exceeds {limit} characters")
    return out


def get_eu_conformity_config() -> dict[str, bool]:
    try:
        from .config import config_source_errors, load_global_config

        errors = config_source_errors(include_tenant=False)
        root = load_global_config()
    except Exception as exc:
        raise EUConformityConfigError(
            "EU conformity configuration is unavailable"
        ) from exc
    if errors or not isinstance(root, Mapping):
        raise EUConformityConfigError("EU conformity configuration is unreadable")
    raw = root.get("eu_conformity") or {}
    if not isinstance(raw, Mapping):
        raise EUConformityConfigError("eu_conformity must be a table")
    unknown = sorted(set(raw) - {"enable"})
    if unknown:
        raise EUConformityConfigError(
            f"unknown eu_conformity keys: {', '.join(unknown)}"
        )
    flag = raw.get("enable", False)
    if not isinstance(flag, bool):
        raise EUConformityConfigError("eu_conformity.enable must be a boolean")
    return {"enable": flag}


def enabled() -> bool:
    try:
        return get_eu_conformity_config()["enable"] is True
    except Exception:
        return False


def _require_enabled() -> None:
    if not enabled():
        raise EUConformityConfigError(
            "EU conformity workflows require [eu_conformity] enable = true"
        )


# --------------------------------------------------------------------------
# FRIA (Art. 27)
# --------------------------------------------------------------------------
def _fria_id(asset_id: str, context: str) -> str:
    return f"ECF-{_sha256([asset_id, context])[:24]}"


def open_fria(asset_id: str, *, context: str, actor: str) -> dict[str, Any]:
    """Open (or return) the FRIA record for one asset in one use context."""

    _require_enabled()
    asset = _text(asset_id, "asset_id", 64)
    context_key = _token(context, "context")
    operator = _text(actor, "actor", 256)
    record_id = _fria_id(asset, context_key)
    current = _FRIA.get(record_id)
    if current is not None:
        return dict(current)
    record = {
        "id": record_id,
        "schema": FRIA_SCHEMA,
        "asset_id": asset,
        "context": context_key,
        "status": "draft",
        "elements": dict.fromkeys(FRIA_ELEMENTS),
        "assessor": None,
        "completed_at": None,
        "notification": None,
        "opened_by": operator,
        "regulation": dict(REGULATION_METADATA),
        "notice": NON_CERTIFICATION_NOTICE,
    }
    return dict(_FRIA.create(record, action="open_fria", actor=operator))


def assert_fria_element(
    record_id: str,
    element: str,
    *,
    content: str,
    actor: str,
    expected_revision: int,
) -> dict[str, Any]:
    """One named human asserts one Art. 27(1) element."""

    _require_enabled()
    if element not in FRIA_ELEMENTS:
        raise ValueError(
            f"element must be one of: {', '.join(sorted(FRIA_ELEMENTS))}"
        )
    operator = _text(actor, "actor", 256)
    body = _text(content, "content", 8000)

    def mutate(value: dict[str, Any]) -> None:
        if value.get("status") not in {"draft", "outdated"}:
            raise EUConformityError(
                "a completed FRIA must be marked outdated before it changes"
            )
        elements = dict(value.get("elements") or {})
        elements[element] = {
            "content": body,
            "citation": FRIA_ELEMENTS[element]["citation"],
            "asserted_by": operator,
            "asserted_at": time.time(),
        }
        value["elements"] = elements

    saved = _FRIA.update(
        _text(record_id, "record_id", 64),
        mutate,
        expected_revision=int(expected_revision),
        action="assert_fria_element",
        actor=operator,
    )
    if saved is None:
        raise KeyError(f"FRIA record is not recorded: {record_id}")
    return dict(saved)


def complete_fria(
    record_id: str, *, assessor: str, expected_revision: int,
) -> dict[str, Any]:
    """Completion requires every Art. 27(1) element and a named assessor."""

    _require_enabled()
    operator = _text(assessor, "assessor", 256)

    def mutate(value: dict[str, Any]) -> None:
        if value.get("status") not in {"draft", "outdated"}:
            raise EUConformityError("only a draft or outdated FRIA can complete")
        elements = value.get("elements") or {}
        missing = sorted(
            key for key in FRIA_ELEMENTS if not isinstance(elements.get(key), Mapping)
        )
        if missing:
            raise EUConformityError(
                f"FRIA elements are not asserted: {', '.join(missing)}"
            )
        value["status"] = "completed"
        value["assessor"] = operator
        value["completed_at"] = time.time()

    saved = _FRIA.update(
        _text(record_id, "record_id", 64),
        mutate,
        expected_revision=int(expected_revision),
        action="complete_fria",
        actor=operator,
    )
    if saved is None:
        raise KeyError(f"FRIA record is not recorded: {record_id}")
    return dict(saved)


def mark_fria_outdated(
    record_id: str, *, reason: str, actor: str, expected_revision: int,
) -> dict[str, Any]:
    """Art. 27(2): a changed or outdated element reopens the assessment."""

    _require_enabled()
    operator = _text(actor, "actor", 256)
    rationale = _text(reason, "reason", 2000)

    def mutate(value: dict[str, Any]) -> None:
        if value.get("status") != "completed":
            raise EUConformityError("only a completed FRIA can become outdated")
        value["status"] = "outdated"
        value["outdated"] = {
            "reason": rationale,
            "marked_by": operator,
            "marked_at": time.time(),
        }

    saved = _FRIA.update(
        _text(record_id, "record_id", 64),
        mutate,
        expected_revision=int(expected_revision),
        action="mark_fria_outdated",
        actor=operator,
    )
    if saved is None:
        raise KeyError(f"FRIA record is not recorded: {record_id}")
    return dict(saved)


def record_fria_notification(
    record_id: str,
    *,
    authority: str,
    submitted_by: str,
    submitted_at: float,
    reference: str,
    expected_revision: int,
) -> dict[str, Any]:
    """Art. 27(3): a human filed the results template; nothing is transmitted."""

    _require_enabled()
    operator = _text(submitted_by, "submitted_by", 256)
    target = _text(authority, "authority", 512)
    filing = _text(reference, "reference", 512)
    if isinstance(submitted_at, bool) or not isinstance(submitted_at, (int, float)):
        raise ValueError("submitted_at must be a Unix timestamp")

    def mutate(value: dict[str, Any]) -> None:
        if value.get("status") != "completed":
            raise EUConformityError(
                "the Art. 27(3) notification follows a completed assessment"
            )
        value["notification"] = {
            "citation": "Art. 27(3)",
            "authority": target,
            "submitted_by": operator,
            "submitted_at": float(submitted_at),
            "reference": filing,
        }

    saved = _FRIA.update(
        _text(record_id, "record_id", 64),
        mutate,
        expected_revision=int(expected_revision),
        action="record_fria_notification",
        actor=operator,
    )
    if saved is None:
        raise KeyError(f"FRIA record is not recorded: {record_id}")
    return dict(saved)


def render_fria(record_id: str) -> str:
    """A bounded markdown draft of the assessment, citations included."""

    _require_enabled()
    record = _FRIA.get(_text(record_id, "record_id", 64))
    if record is None:
        raise KeyError(f"FRIA record is not recorded: {record_id}")
    lines = [
        "# Fundamental rights impact assessment (Art. 27)",
        "",
        f"Asset: {record.get('asset_id')}  |  Context: {record.get('context')}",
        f"Status: {record.get('status')}  |  Assessor: {record.get('assessor') or 'unassigned'}",
        f"Basis: {REGULATION_METADATA['regulation']} "
        f"(consolidated {REGULATION_METADATA['consolidated']})",
        "",
    ]
    elements = record.get("elements") or {}
    for key, meta in FRIA_ELEMENTS.items():
        entry = elements.get(key)
        lines.append(f"## {meta['citation']} — {meta['prompt']}")
        if isinstance(entry, Mapping):
            lines.append(str(entry.get("content")))
            lines.append(f"*Asserted by {entry.get('asserted_by')}.*")
        else:
            lines.append("*Not yet asserted.*")
        lines.append("")
    notification = record.get("notification")
    if isinstance(notification, Mapping):
        lines.append(
            f"Notified to {notification.get('authority')} "
            f"(reference {notification.get('reference')}, Art. 27(3))."
        )
    lines.append("")
    lines.append(NON_CERTIFICATION_NOTICE)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Annex IV checklist (Art. 11(1))
# --------------------------------------------------------------------------
def _annex_id(asset_id: str) -> str:
    return f"ECD-{_sha256(asset_id)[:24]}"


def open_annex_iv(asset_id: str, *, actor: str) -> dict[str, Any]:
    _require_enabled()
    asset = _text(asset_id, "asset_id", 64)
    operator = _text(actor, "actor", 256)
    record_id = _annex_id(asset)
    current = _ANNEX_IV.get(record_id)
    if current is not None:
        return dict(current)
    record = {
        "id": record_id,
        "schema": ANNEX_IV_SCHEMA,
        "asset_id": asset,
        "items": dict.fromkeys(ANNEX_IV_ITEMS),
        "opened_by": operator,
        "regulation": dict(REGULATION_METADATA),
        "notice": NON_CERTIFICATION_NOTICE,
    }
    return dict(_ANNEX_IV.create(record, action="open_annex_iv", actor=operator))


def assert_annex_iv_item(
    record_id: str,
    item: str,
    *,
    reference: str,
    actor: str,
    expected_revision: int,
) -> dict[str, Any]:
    """A named human asserts where one Annex IV item's documentation lives."""

    _require_enabled()
    if item not in ANNEX_IV_ITEMS:
        raise ValueError(f"item must be one of: {', '.join(sorted(ANNEX_IV_ITEMS))}")
    operator = _text(actor, "actor", 256)
    where = _text(reference, "reference", 512)

    def mutate(value: dict[str, Any]) -> None:
        items = dict(value.get("items") or {})
        items[item] = {
            "citation": ANNEX_IV_ITEMS[item][0],
            "reference": where,
            "asserted_by": operator,
            "asserted_at": time.time(),
        }
        value["items"] = items

    saved = _ANNEX_IV.update(
        _text(record_id, "record_id", 64),
        mutate,
        expected_revision=int(expected_revision),
        action="assert_annex_iv_item",
        actor=operator,
    )
    if saved is None:
        raise KeyError(f"Annex IV checklist is not recorded: {record_id}")
    return dict(saved)


def annex_iv_status(record_id: str) -> dict[str, Any]:
    _require_enabled()
    record = _ANNEX_IV.get(_text(record_id, "record_id", 64))
    if record is None:
        raise KeyError(f"Annex IV checklist is not recorded: {record_id}")
    items = record.get("items") or {}
    missing = sorted(
        key for key in ANNEX_IV_ITEMS if not isinstance(items.get(key), Mapping)
    )
    return {
        "id": record.get("id"),
        "asset_id": record.get("asset_id"),
        "asserted": len(ANNEX_IV_ITEMS) - len(missing),
        "total": len(ANNEX_IV_ITEMS),
        "missing": missing,
        "complete": not missing,
        "revision": record.get("revision"),
    }


def render_annex_iv(record_id: str) -> str:
    """A bounded skeleton naming every Annex IV item and its assertion."""

    _require_enabled()
    record = _ANNEX_IV.get(_text(record_id, "record_id", 64))
    if record is None:
        raise KeyError(f"Annex IV checklist is not recorded: {record_id}")
    lines = [
        "# Technical documentation checklist (Art. 11(1), Annex IV)",
        "",
        f"Asset: {record.get('asset_id')}",
        f"Basis: {REGULATION_METADATA['regulation']} "
        f"(consolidated {REGULATION_METADATA['consolidated']})",
        "",
    ]
    items = record.get("items") or {}
    for key, (citation, heading) in ANNEX_IV_ITEMS.items():
        entry = items.get(key)
        lines.append(f"## {citation} — {heading}")
        if isinstance(entry, Mapping):
            lines.append(
                f"Documented at: {entry.get('reference')} "
                f"(asserted by {entry.get('asserted_by')})"
            )
        else:
            lines.append("*Not yet asserted.*")
        lines.append("")
    lines.append(NON_CERTIFICATION_NOTICE)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Art. 43 conformity-assessment route
# --------------------------------------------------------------------------
def _route_id(asset_id: str) -> str:
    return f"ECA-{_sha256(asset_id)[:24]}"


def record_assessment_route(
    asset_id: str,
    *,
    route: str,
    annex_iii_point: int | None,
    harmonised_standards_applied: bool,
    common_specifications_applied: bool,
    rationale: str,
    actor: str,
) -> dict[str, Any]:
    """Record the human route decision, refusing combinations Art. 43 forbids."""

    _require_enabled()
    asset = _text(asset_id, "asset_id", 64)
    operator = _text(actor, "actor", 256)
    reason = _text(rationale, "rationale", 2000)
    if route not in ROUTES:
        raise ValueError(f"route must be one of: {', '.join(ROUTES)}")
    if not isinstance(harmonised_standards_applied, bool) or not isinstance(
        common_specifications_applied, bool,
    ):
        raise ValueError("standards flags must be booleans")
    if route == "sectoral_annex_i":
        if annex_iii_point is not None:
            raise ValueError("a sectoral Annex I route carries no Annex III point")
    else:
        if (
            isinstance(annex_iii_point, bool)
            or not isinstance(annex_iii_point, int)
            or not 1 <= annex_iii_point <= 8
        ):
            raise ValueError("annex_iii_point must be an integer between 1 and 8")
        covered = harmonised_standards_applied or common_specifications_applied
        if annex_iii_point == 1:
            if route == "internal_control_annex_vi" and not covered:
                raise EUConformityError(
                    "Art. 43(1): an Annex III point 1 system may use internal "
                    "control (Annex VI) only where harmonised standards "
                    "(Art. 40) or common specifications (Art. 41) were applied; "
                    "otherwise the Annex VII notified-body procedure applies"
                )
        else:
            if route != "internal_control_annex_vi":
                raise EUConformityError(
                    "Art. 43(2): Annex III points 2-8 follow the internal-"
                    "control procedure of Annex VI, without a notified body"
                )
    record_id = _route_id(asset)
    record = {
        "id": record_id,
        "schema": ROUTE_SCHEMA,
        "asset_id": asset,
        "route": route,
        "annex_iii_point": annex_iii_point,
        "harmonised_standards_applied": harmonised_standards_applied,
        "common_specifications_applied": common_specifications_applied,
        "rationale": reason,
        "status": "recorded",
        "decided_by": operator,
        "regulation": dict(REGULATION_METADATA),
        "notice": NON_CERTIFICATION_NOTICE,
    }
    return dict(_ROUTES.create(record, action="record_assessment_route", actor=operator))


def mark_substantial_modification(
    asset_id: str, *, reason: str, actor: str, expected_revision: int,
    predetermined: bool = False,
) -> dict[str, Any]:
    """Art. 43(4): a substantial modification requires a new assessment.

    A change pre-determined by the provider and documented in the technical
    documentation is not a substantial modification; passing
    ``predetermined=True`` records the change without reopening the route.
    """

    _require_enabled()
    operator = _text(actor, "actor", 256)
    rationale = _text(reason, "reason", 2000)
    if not isinstance(predetermined, bool):
        raise ValueError("predetermined must be a boolean")

    def mutate(value: dict[str, Any]) -> None:
        changes = list(value.get("modifications") or [])
        changes.append(
            {
                "reason": rationale,
                "predetermined": predetermined,
                "marked_by": operator,
                "marked_at": time.time(),
            }
        )
        value["modifications"] = changes[-50:]
        if not predetermined:
            value["status"] = "reassessment_required"

    saved = _ROUTES.update(
        _route_id(_text(asset_id, "asset_id", 64)),
        mutate,
        expected_revision=int(expected_revision),
        action="mark_substantial_modification",
        actor=operator,
    )
    if saved is None:
        raise KeyError(f"assessment route is not recorded for asset: {asset_id}")
    return dict(saved)


def get_assessment_route(asset_id: str) -> dict[str, Any] | None:
    _require_enabled()
    record = _ROUTES.get(_route_id(_text(asset_id, "asset_id", 64)))
    return None if record is None else dict(record)


def list_frias(*, status: str | None = None) -> list[dict[str, Any]]:
    _require_enabled()
    if status is not None and status not in {"draft", "completed", "outdated"}:
        raise ValueError("status must be draft, completed, or outdated")
    rows = _FRIA.list(limit=_MAX_LIST)
    rows.sort(key=lambda row: (str(row.get("asset_id") or ""), str(row.get("id") or "")))
    return [
        dict(row) for row in rows
        if status is None or row.get("status") == status
    ]


__all__ = [
    "ANNEX_IV_ITEMS",
    "EUConformityConfigError",
    "EUConformityError",
    "FRIA_ELEMENTS",
    "NON_CERTIFICATION_NOTICE",
    "REGULATION_METADATA",
    "ROUTES",
    "annex_iv_status",
    "assert_annex_iv_item",
    "assert_fria_element",
    "complete_fria",
    "enabled",
    "get_assessment_route",
    "get_eu_conformity_config",
    "list_frias",
    "mark_fria_outdated",
    "mark_substantial_modification",
    "open_annex_iv",
    "open_fria",
    "record_assessment_route",
    "record_fria_notification",
    "render_annex_iv",
    "render_fria",
]
