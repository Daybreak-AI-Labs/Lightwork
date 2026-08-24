"""One bounded needs-attention digest across the governance registers.

Aggregates the deadline- and staleness-bearing state the compliance team
must act on: Art. 73 serious-incident clocks (overdue and due soon), Art. 4
literacy gaps (missing, expired, expiring soon), Art. 27(2) outdated FRIAs,
and Art. 43(4) routes flagged for reassessment. Pure read-side aggregation:
it carries no configuration knob of its own — each section rides its source
module's opt-in knob and reports ``enabled: false`` when that module is off
or unavailable. Nothing here transmits anything anywhere; delivery to chat
or e-mail stays with the operator's own scheduler around the CLI.
"""
from __future__ import annotations

import time
from typing import Any

_DAY = 86_400.0
_MAX_IDS = 50


def _incident_section(now: float, due_soon_seconds: float) -> tuple[dict[str, Any], int]:
    try:
        from . import ai_incident_reporting as air

        if not air.enabled():
            return {"enabled": False}, 0
        rows = air.list_declarations(now=now)
    except Exception:
        return {"enabled": False, "error": "unavailable"}, 0
    overdue = [str(row.get("id")) for row in rows if row["clock"]["overdue"]]
    due_soon = [
        str(row.get("id"))
        for row in rows
        if not row["clock"]["overdue"]
        and 0.0 < row["clock"]["seconds_remaining"] <= due_soon_seconds
    ]
    section = {
        "enabled": True,
        "overdue": overdue[:_MAX_IDS],
        "due_soon": due_soon[:_MAX_IDS],
        "citation": "Art. 73",
    }
    return section, len(overdue) + len(due_soon)


def _literacy_section(now: float, expiring_days: int) -> tuple[dict[str, Any], int]:
    try:
        from . import ai_literacy

        if not ai_literacy.enabled():
            return {"enabled": False}, 0
        gaps = ai_literacy.literacy_gaps(now=now)
    except Exception:
        return {"enabled": False, "error": "unavailable"}, 0
    horizon = now + expiring_days * _DAY
    expiring = 0
    for person in gaps.get("persons") or []:
        for state in person.get("trainings") or []:
            valid_until = state.get("valid_until")
            if (
                state.get("status") == "current"
                and valid_until is not None
                and float(valid_until) <= horizon
            ):
                expiring += 1
    counts = gaps.get("counts") or {}
    missing = int(counts.get("missing") or 0)
    expired = int(counts.get("expired") or 0)
    section = {
        "enabled": True,
        "missing": missing,
        "expired": expired,
        "expiring_soon": expiring,
        "citation": "Art. 4",
    }
    return section, missing + expired + expiring


def _conformity_section(now: float) -> tuple[dict[str, Any], int]:
    del now
    try:
        from . import eu_conformity as euc

        if not euc.enabled():
            return {"enabled": False}, 0
        outdated = [
            str(row.get("id")) for row in euc.list_frias(status="outdated")
        ]
        reassess = [
            str(row.get("asset_id"))
            for row in euc.list_assessment_routes(status="reassessment_required")
        ]
    except Exception:
        return {"enabled": False, "error": "unavailable"}, 0
    section = {
        "enabled": True,
        "outdated_frias": outdated[:_MAX_IDS],
        "reassessment_required": reassess[:_MAX_IDS],
        "citations": ["Art. 27(2)", "Art. 43(4)"],
    }
    return section, len(outdated) + len(reassess)


def digest(
    *,
    now: float | None = None,
    due_soon_seconds: float = 2 * _DAY,
    expiring_days: int = 30,
) -> dict[str, Any]:
    """The needs-attention digest; ``attention_count`` totals every item."""

    if now is not None and (isinstance(now, bool) or not isinstance(now, (int, float))):
        raise ValueError("now must be a numeric timestamp")
    if (
        isinstance(due_soon_seconds, bool)
        or not isinstance(due_soon_seconds, (int, float))
        or not 0 < float(due_soon_seconds) <= 30 * _DAY
    ):
        raise ValueError("due_soon_seconds must be between 1 second and 30 days")
    if (
        isinstance(expiring_days, bool)
        or not isinstance(expiring_days, int)
        or not 1 <= expiring_days <= 365
    ):
        raise ValueError("expiring_days must be between 1 and 365")
    current = time.time() if now is None else float(now)
    incidents, incident_count = _incident_section(current, float(due_soon_seconds))
    literacy, literacy_count = _literacy_section(current, expiring_days)
    conformity, conformity_count = _conformity_section(current)
    return {
        "generated_at": current,
        "attention_count": incident_count + literacy_count + conformity_count,
        "sections": {
            "serious_incidents": incidents,
            "ai_literacy": literacy,
            "eu_conformity": conformity,
        },
    }


__all__ = ["digest"]
