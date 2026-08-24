"""EU AI Act Art. 4 AI-literacy register: requirements, completions, gaps.

Art. 4 of Regulation (EU) 2024/1689 (applicable since 2 February 2025)
requires providers and deployers to take measures ensuring a sufficient level
of AI literacy of their staff and other persons operating AI systems on their
behalf. This module keeps the governed register that makes the obligation
inspectable:

* **Role requirements** — a named human sets which trainings a role must hold
  and how long each completion stays valid.
* **Assignments** — which people (operator-chosen identifiers; the operator
  decides what identifier scheme is appropriate) carry which roles.
* **Completions** — CAS-recorded assertions that a person completed a
  training, with an evidence reference; re-completion refreshes the clock and
  keeps a bounded history.
* **Gaps** — the derived view: per person, every required training that is
  missing or expired, with counts an auditor can act on.
* **Evidence export** — one bounded JSON snapshot of the whole register.

Tracking completions is decision support for the Art. 4 obligation. It is not
a certification of any person's competence, and the platform never asserts
that a training was sufficient — a human set the requirement and a human
recorded the completion.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

from .governed_records import GovernedRecordStore
from .privacy_ops import RecordConflict

REGULATION_METADATA = {
    "regulation": "Regulation (EU) 2024/1689",
    "article": "Art. 4",
    "obligation": "AI literacy of staff and other persons operating AI systems",
    "applicable_since": "2025-02-02",
}
NON_CERTIFICATION_NOTICE = (
    "AI-literacy tracking is decision support for the Art. 4 obligation; it "
    "is not a certification of competence or of training sufficiency."
)
REQUIREMENT_SCHEMA = "lightwork.ai-literacy-requirement.v1"
ASSIGNMENT_SCHEMA = "lightwork.ai-literacy-assignment.v1"
COMPLETION_SCHEMA = "lightwork.ai-literacy-completion.v1"

_REQUIREMENTS = GovernedRecordStore(
    "ai_literacy_requirements", "ALR", "ai_literacy_requirement",
)
_ASSIGNMENTS = GovernedRecordStore(
    "ai_literacy_assignments", "ALA", "ai_literacy_assignment",
)
_COMPLETIONS = GovernedRecordStore(
    "ai_literacy_completions", "ALC", "ai_literacy_completion",
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}\Z")
_MAX_TRAININGS = 64
_MAX_ROLES = 64
_MAX_HISTORY = 20
_MAX_LIST = 500
_DAY = 86_400.0


class AILiteracyError(RuntimeError):
    """Base error for the AI-literacy register."""


class AILiteracyConfigError(AILiteracyError):
    """The [ai_literacy] configuration is missing, malformed, or disabled."""


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


def _timestamp(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a Unix timestamp")
    number = float(value)
    if not 0 < number < 10_000_000_000:
        raise ValueError(f"{label} is out of range")
    return number


def get_ai_literacy_config() -> dict[str, bool]:
    try:
        from .config import config_source_errors, load_global_config

        errors = config_source_errors(include_tenant=False)
        root = load_global_config()
    except Exception as exc:
        raise AILiteracyConfigError("AI literacy configuration is unavailable") from exc
    if errors or not isinstance(root, Mapping):
        raise AILiteracyConfigError("AI literacy configuration is unreadable")
    raw = root.get("ai_literacy") or {}
    if not isinstance(raw, Mapping):
        raise AILiteracyConfigError("ai_literacy must be a table")
    unknown = sorted(set(raw) - {"enable"})
    if unknown:
        raise AILiteracyConfigError(f"unknown ai_literacy keys: {', '.join(unknown)}")
    flag = raw.get("enable", False)
    if not isinstance(flag, bool):
        raise AILiteracyConfigError("ai_literacy.enable must be a boolean")
    return {"enable": flag}


def enabled() -> bool:
    try:
        return get_ai_literacy_config()["enable"] is True
    except Exception:
        return False


def _require_enabled() -> None:
    if not enabled():
        raise AILiteracyConfigError(
            "AI literacy register requires [ai_literacy] enable = true"
        )


def _trainings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("trainings must be a list")
    if not value or len(value) > _MAX_TRAININGS:
        raise ValueError(f"trainings must contain 1-{_MAX_TRAININGS} entries")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError("each training must be an object")
        unknown = sorted(set(raw) - {"key", "title", "validity_days"})
        if unknown:
            raise ValueError(f"unknown training keys: {', '.join(unknown)}")
        key = _token(raw.get("key"), "training key")
        if key in seen:
            raise ValueError("training keys must be unique")
        seen.add(key)
        validity = raw.get("validity_days")
        if validity is not None:
            if isinstance(validity, bool) or not isinstance(validity, int):
                raise ValueError("validity_days must be an integer or omitted")
            if not 1 <= validity <= 3_650:
                raise ValueError("validity_days must be between 1 and 3650")
        out.append(
            {
                "key": key,
                "title": _text(raw.get("title"), "training title", 256),
                "validity_days": validity,
            }
        )
    out.sort(key=lambda row: row["key"])
    return out


def set_role_requirement(
    role: str,
    trainings: Sequence[Mapping[str, Any]],
    *,
    actor: str,
    expected_revision: int = 0,
) -> dict[str, Any]:
    """Create or CAS-update the training requirement for one role."""

    _require_enabled()
    role_token = _token(role, "role")
    operator = _text(actor, "actor", 256)
    normalized = _trainings(trainings)
    record_id = f"ALR-{_sha256(role_token)[:24]}"
    if expected_revision == 0:
        record = {
            "id": record_id,
            "schema": REQUIREMENT_SCHEMA,
            "role": role_token,
            "trainings": normalized,
            "set_by": operator,
            "notice": NON_CERTIFICATION_NOTICE,
        }
        return dict(_REQUIREMENTS.create(
            record, action="set_literacy_requirement", actor=operator,
        ))

    def mutate(value: dict[str, Any]) -> None:
        value["trainings"] = normalized
        value["set_by"] = operator

    saved = _REQUIREMENTS.update(
        record_id,
        mutate,
        expected_revision=int(expected_revision),
        action="set_literacy_requirement",
        actor=operator,
    )
    if saved is None:
        raise KeyError(f"role requirement is not recorded: {role_token}")
    return dict(saved)


def assign_roles(
    person: str,
    roles: Sequence[str],
    *,
    actor: str,
    expected_revision: int = 0,
) -> dict[str, Any]:
    """Create or CAS-update one person's role assignment."""

    _require_enabled()
    person_token = _token(person, "person")
    operator = _text(actor, "actor", 256)
    if not isinstance(roles, Sequence) or isinstance(roles, (str, bytes)):
        raise ValueError("roles must be a list")
    if not roles or len(roles) > _MAX_ROLES:
        raise ValueError(f"roles must contain 1-{_MAX_ROLES} entries")
    normalized = sorted({_token(role, "role") for role in roles})
    known = {row["role"] for row in _requirement_rows()}
    missing = sorted(set(normalized) - known)
    if missing:
        raise ValueError(
            f"roles have no recorded requirement: {', '.join(missing)}"
        )
    record_id = f"ALA-{_sha256(person_token)[:24]}"
    if expected_revision == 0:
        record = {
            "id": record_id,
            "schema": ASSIGNMENT_SCHEMA,
            "person": person_token,
            "roles": normalized,
            "assigned_by": operator,
        }
        return dict(_ASSIGNMENTS.create(
            record, action="assign_literacy_roles", actor=operator,
        ))

    def mutate(value: dict[str, Any]) -> None:
        value["roles"] = normalized
        value["assigned_by"] = operator

    saved = _ASSIGNMENTS.update(
        record_id,
        mutate,
        expected_revision=int(expected_revision),
        action="assign_literacy_roles",
        actor=operator,
    )
    if saved is None:
        raise KeyError(f"assignment is not recorded: {person_token}")
    return dict(saved)


def record_completion(
    person: str,
    training: str,
    *,
    completed_at: float,
    evidence_ref: str,
    actor: str,
) -> dict[str, Any]:
    """Record (or refresh) one person's completion of one training."""

    _require_enabled()
    person_token = _token(person, "person")
    training_token = _token(training, "training")
    operator = _text(actor, "actor", 256)
    completed = _timestamp(completed_at, "completed_at")
    if completed > time.time() + 300:
        raise ValueError("completed_at must not be future-dated")
    reference = _text(evidence_ref, "evidence_ref", 512)
    record_id = f"ALC-{_sha256([person_token, training_token])[:24]}"
    current = _COMPLETIONS.get(record_id)
    if current is None:
        record = {
            "id": record_id,
            "schema": COMPLETION_SCHEMA,
            "person": person_token,
            "training": training_token,
            "completed_at": completed,
            "evidence_ref": reference,
            "recorded_by": operator,
            "history": [],
        }
        try:
            return dict(_COMPLETIONS.create(
                record, action="record_literacy_completion", actor=operator,
            ))
        except RecordConflict:
            current = _COMPLETIONS.get(record_id)
            if current is None:
                raise

    def mutate(value: dict[str, Any]) -> None:
        history = list(value.get("history") or [])
        history.append(
            {
                "completed_at": value.get("completed_at"),
                "evidence_ref": value.get("evidence_ref"),
                "recorded_by": value.get("recorded_by"),
            }
        )
        value["history"] = history[-_MAX_HISTORY:]
        value["completed_at"] = completed
        value["evidence_ref"] = reference
        value["recorded_by"] = operator

    saved = _COMPLETIONS.update(
        record_id,
        mutate,
        expected_revision=int(current.get("revision") or 0),
        action="record_literacy_completion",
        actor=operator,
    )
    if saved is None:
        raise AILiteracyError("completion record disappeared during refresh")
    return dict(saved)


def _requirement_rows() -> list[dict[str, Any]]:
    rows = _REQUIREMENTS.list(limit=_MAX_LIST)
    rows.sort(key=lambda row: str(row.get("role") or ""))
    return rows


def literacy_gaps(*, now: float | None = None) -> dict[str, Any]:
    """Per person: every required training that is missing or expired."""

    _require_enabled()
    current = time.time() if now is None else _timestamp(now, "now")
    requirements = {row["role"]: row for row in _requirement_rows()}
    completions: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _COMPLETIONS.list(limit=_MAX_LIST * 4):
        completions[(str(row.get("person")), str(row.get("training")))] = row
    persons: list[dict[str, Any]] = []
    counts = {"current": 0, "expired": 0, "missing": 0}
    assignments = _ASSIGNMENTS.list(limit=_MAX_LIST * 4)
    assignments.sort(key=lambda row: str(row.get("person") or ""))
    for assignment in assignments:
        person = str(assignment.get("person") or "")
        required: dict[str, dict[str, Any]] = {}
        for role in assignment.get("roles") or []:
            requirement = requirements.get(str(role))
            if requirement is None:
                continue
            for training in requirement.get("trainings") or []:
                required.setdefault(str(training["key"]), dict(training))
        states: list[dict[str, Any]] = []
        for key in sorted(required):
            training = required[key]
            completion = completions.get((person, key))
            if completion is None:
                status = "missing"
                completed_at = None
                valid_until = None
            else:
                completed_at = float(completion.get("completed_at") or 0.0)
                validity = training.get("validity_days")
                valid_until = (
                    completed_at + float(validity) * _DAY
                    if validity is not None
                    else None
                )
                status = (
                    "expired"
                    if valid_until is not None and valid_until <= current
                    else "current"
                )
            counts[status] += 1
            states.append(
                {
                    "training": key,
                    "title": training.get("title"),
                    "status": status,
                    "completed_at": completed_at,
                    "valid_until": valid_until,
                }
            )
        persons.append(
            {
                "person": person,
                "roles": list(assignment.get("roles") or []),
                "trainings": states,
                "gap_count": sum(
                    1 for state in states if state["status"] != "current"
                ),
            }
        )
    return {
        "persons": persons,
        "person_count": len(persons),
        "counts": counts,
        "regulation": dict(REGULATION_METADATA),
        "notice": NON_CERTIFICATION_NOTICE,
        "generated_at": current,
    }


def export_evidence(*, now: float | None = None) -> str:
    """A bounded JSON snapshot of the whole register for an auditor."""

    _require_enabled()
    gaps = literacy_gaps(now=now)
    snapshot = {
        "schema": "lightwork.ai-literacy-evidence.v1",
        "regulation": dict(REGULATION_METADATA),
        "notice": NON_CERTIFICATION_NOTICE,
        "generated_at": gaps["generated_at"],
        "roles": [
            {
                "role": row.get("role"),
                "trainings": row.get("trainings"),
                "set_by": row.get("set_by"),
                "revision": row.get("revision"),
            }
            for row in _requirement_rows()
        ],
        "persons": gaps["persons"],
        "counts": gaps["counts"],
    }
    return json.dumps(snapshot, indent=2, sort_keys=True, default=str)


def list_requirements() -> list[dict[str, Any]]:
    _require_enabled()
    return [
        {
            "role": row.get("role"),
            "trainings": row.get("trainings"),
            "set_by": row.get("set_by"),
            "revision": row.get("revision"),
        }
        for row in _requirement_rows()
    ]


__all__ = [
    "AILiteracyConfigError",
    "AILiteracyError",
    "NON_CERTIFICATION_NOTICE",
    "REGULATION_METADATA",
    "assign_roles",
    "enabled",
    "export_evidence",
    "get_ai_literacy_config",
    "list_requirements",
    "literacy_gaps",
    "record_completion",
    "set_role_requirement",
]
