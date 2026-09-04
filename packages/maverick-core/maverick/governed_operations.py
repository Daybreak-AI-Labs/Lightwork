"""Durable operation bindings shared by bounded application services.

The owning domain persists the operation before calling a provider. Existing
GovernedActions still supplies typed simulation, approval gating and lineage.
An ``executing`` record after restart is ambiguous, never permission to retry.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from .governed_actions import ActionSpec, GovernedActions
from .paths import canonical_tenant_id


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ServiceContext:
    """Authority supplied by authenticated composition, never model/body data.

    ``human`` is an assertion by the trusted caller, not authentication itself;
    the mandatory authorization callback must validate it for human operations.
    Credential references identify sealed connection records, never raw secrets.
    """

    tenant: str
    actor: str
    resource_owner: str
    credential_authority: str
    credential_ref: str
    document_credential_ref: str = "gdrive"
    human: bool = False

    def __post_init__(self) -> None:
        for key, value in asdict(self).items():
            if key == "human":
                if type(value) is not bool:
                    raise ValueError("human must be a boolean")
            elif not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError(f"{key} must be a nonempty bounded string")
        canonical_tenant_id(self.tenant)

    def binding(self) -> dict[str, str]:
        return {k: v for k, v in asdict(self).items() if k != "human"}


def operation_binding(operation: dict) -> str:
    """Exact unredacted payload digest; credentials are references only."""
    return digest({key: operation[key] for key in (
        "id", "action", "context", "payload", "payload_sha256", "preconditions",
        "scope", "expires_at",
    )})


def validate_approval(operation: dict, context: ServiceContext, now: float) -> None:
    if operation["context"] != context.binding():
        raise PermissionError("operation execution authority changed")
    if digest(operation["payload"]) != operation["payload_sha256"]:
        raise ValueError("operation payload digest mismatch")
    approval = operation.get("approval") or {}
    if approval.get("binding_sha256") != operation_binding(operation):
        raise PermissionError("exact operation has no valid human approval")
    if now >= operation["expires_at"]:
        raise PermissionError("operation approval expired")


def apply_declared(operation: dict, apply) -> tuple[dict, dict]:
    """Execute a declared Jira operation after the domain's durable claim.

    A caller must treat *every* exception here as an ambiguous external effect.
    In particular, lineage/receipt failure cannot authorize a second create.
    """
    receipt: dict = {}

    def invoke(params):
        receipt.update(apply(params))
        return json.dumps(receipt, sort_keys=True)

    actions = GovernedActions()
    actions.register(ActionSpec(
        name="jira.create", params={"project": str, "summary": str,
                                    "description": str, "marker": str},
        risk="high", simulate=lambda _: "Create one reviewed Jira task",
        apply=invoke,
    ))
    actions.commit(
        "jira.create", operation["payload"],
        approver=operation["approval"]["reviewer"],
        sources=(operation["preconditions"]["evidence_sha256"],),
    )
    return receipt, asdict(actions.lineage[-1])
