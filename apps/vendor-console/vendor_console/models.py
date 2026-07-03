"""Typed views over the SQLite rows — plain dataclasses, no ORM."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

ROLES = ("owner", "admin", "support", "viewer")
POSTURES = ("connected", "vpc", "airgapped")
CUSTOMER_STATUSES = ("trial", "active", "suspended", "churned")
TIERS = ("basic", "gold", "platinum")

#: Roles allowed to mint/revoke licenses and mutate customers. support/viewer
#: are read-mostly (support also handles tickets in a later drop).
WRITE_ROLES = frozenset({"owner", "admin"})


@dataclass
class Staff:
    id: int
    email: str
    name: str
    role: str
    totp_enrolled: bool
    disabled: bool
    created_at: float
    session_epoch: int = 0

    @classmethod
    def from_row(cls, r) -> Staff:
        return cls(id=r["id"], email=r["email"], name=r["name"], role=r["role"],
                   totp_enrolled=bool(r["totp_enrolled"]), disabled=bool(r["disabled"]),
                   created_at=r["created_at"], session_epoch=r["session_epoch"])

    @property
    def can_write(self) -> bool:
        return self.role in WRITE_ROLES


@dataclass
class Customer:
    id: int
    name: str
    primary_contact: str
    contact_email: str
    posture: str
    status: str
    notes: str
    created_at: float
    updated_at: float

    @classmethod
    def from_row(cls, r) -> Customer:
        return cls(id=r["id"], name=r["name"], primary_contact=r["primary_contact"],
                   contact_email=r["contact_email"], posture=r["posture"],
                   status=r["status"], notes=r["notes"],
                   created_at=r["created_at"], updated_at=r["updated_at"])


@dataclass
class License:
    id: int
    customer_id: int
    license_id: str
    tier: str
    suites: list[str]
    features: list[str]
    seats: int | None
    issued_at: str
    expires_at: str | None
    grace_days: int
    key_id: str
    doc: dict
    revoked_at: float | None
    note: str
    created_by: str
    created_at: float

    @classmethod
    def from_row(cls, r) -> License:
        return cls(
            id=r["id"], customer_id=r["customer_id"], license_id=r["license_id"],
            tier=r["tier"], suites=json.loads(r["suites_json"]),
            features=json.loads(r["features_json"]), seats=r["seats"],
            issued_at=r["issued_at"], expires_at=r["expires_at"],
            grace_days=r["grace_days"], key_id=r["key_id"],
            doc=json.loads(r["doc_json"]), revoked_at=r["revoked_at"],
            note=r["note"], created_by=r["created_by"], created_at=r["created_at"])

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None


@dataclass
class AuditEvent:
    id: int
    ts: float
    actor: str
    action: str
    target: str
    detail: dict = field(default_factory=dict)
    prev_hash: str = ""
    hash: str = ""

    @classmethod
    def from_row(cls, r) -> AuditEvent:
        return cls(id=r["id"], ts=r["ts"], actor=r["actor"], action=r["action"],
                   target=r["target"], detail=json.loads(r["detail_json"]),
                   prev_hash=r["prev_hash"], hash=r["hash"])
