"""AI vendor onboarding application service over existing domain authorities.

The vendor register and AI vendor-risk register remain their systems of record.
This tenant-scoped saga stores coordination, operation intent and customer
outcomes. The supported deployment is one persistent application replica.
Provider effects cannot participate in its local transaction: ambiguous creates
require read-only reconciliation, including after a lost receipt or restart.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import math
import re
import threading
import time
import uuid
from dataclasses import asdict
from typing import NoReturn

from . import ai_vendor_risk as risk
from . import vendor_management as vendors
from . import vendor_onboarding_documents as documents
from .file_lock import cross_process_lock
from .governed_operations import (
    ActionReviewRequiredError,
    ApprovalBindingError,
    ServiceContext,
    apply_declared,
    audit_authority_denial,
    digest,
    operation_binding,
    validate_approval,
)
from .governed_records import GovernedRecordStore, RecordConflict
from .paths import data_dir, tenant_scope
from .platform_contracts import build_contract
from .vendor_onboarding_adapters import EvidenceDocument

_CASES = GovernedRecordStore("vendor_onboarding", "VON", "vendor_onboarding_case")
_LOCK = threading.RLock()
_PROJECT = re.compile(r"[A-Z][A-Z0-9_]{1,31}\Z")
_CLOSED_OPERATION = {"executing", "reconciliation_required", "verified"}
CASE_SCHEMA = "lightwork.vendor-onboarding.v1"

_JOURNEY_CONTRACT_V1 = {
    "id": "supplier-evidence-onboarding",
    "version": 1,
    "supported_deployment": {
        "topology": "single_persistent_application_replica",
        "workers": "tenant_and_credential_scoped",
        "external_systems": ["Google Drive evidence", "Jira Task delivery"],
    },
    "technical_acceptance": {
        "effect": "one Jira Task matches the exact approved payload",
        "evidence": "fresh authoritative Jira readback under the bound credential version",
    },
    "business_acceptance": {
        "decision": "an authenticated authorized human explicitly accepts or rejects the verified deliverable",
        "accepted_value": True,
    },
    "renewal": "accepted cases retain the evidence freshness deadline and require current source and ticket checks",
    "out_of_scope": [
        "procurement approval, payment, contracting, or ERP provisioning",
        "production release or authorization of the reviewed vendor or model",
        "multi-replica application operation",
    ],
}
JOURNEY_CONTRACT_V1_REF = {
    "id": _JOURNEY_CONTRACT_V1["id"],
    "version": _JOURNEY_CONTRACT_V1["version"],
    "sha256": digest(_JOURNEY_CONTRACT_V1),
}
CURRENT_JOURNEY_CONTRACT_REF = dict(JOURNEY_CONTRACT_V1_REF)
_JOURNEY_CONTRACTS = {
    (_JOURNEY_CONTRACT_V1["id"], _JOURNEY_CONTRACT_V1["version"]): _JOURNEY_CONTRACT_V1,
}
_WORK_ITEM_STATE = {
    "intake": ("Draft", "obtain_evidence"),
    "evidence_collected": ("Ready", "build_assessment"),
    "pending_review": ("Awaiting approval", "review_assessment"),
    "review_approved": ("Ready", "prepare_action"),
    "review_rejected": ("Failed", "correct_or_close"),
    "action_pending_approval": ("Awaiting approval", "approve_action"),
    "action_approved": ("Ready", "execute_action"),
    "executing": ("Running", "wait_or_reconcile"),
    "reconciliation_required": ("Needs reconciliation", "reconcile"),
    "execution_completed": ("Verified", "accept_or_reject"),
    "accepted": ("Accepted", "check_renewal"),
    "customer_rejected": ("Failed", "closed"),
    "cancelled": ("Cancelled", "closed"),
}


class VendorOnboardingContractError(ValueError):
    """A persisted record cannot be interpreted under a supported contract."""

    def __init__(self, message, *, code):
        super().__init__(message)
        self.code = code


def _text(value, name, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be a nonempty string of at most {limit} characters")
    return value.strip()


def enabled() -> bool:
    from .config import load_global_config

    return (load_global_config().get("vendor_onboarding") or {}).get("enable") is True


def _scoped(method):
    @functools.wraps(method)
    def call(self, ctx, *args, **kwargs):
        if not isinstance(ctx, ServiceContext):
            raise TypeError("explicit ServiceContext required")
        if not enabled():
            raise RuntimeError("vendor onboarding requires [vendor_onboarding] enable = true")
        with tenant_scope(tenant=ctx.tenant):
            # Serializes domain read/CAS/reference composition on the supported
            # persistent single replica, across threads and worker processes.
            with _LOCK, cross_process_lock(data_dir("vendor_onboarding", "saga"), strict=True):
                return method(self, ctx, *args, **kwargs)
    return call


class VendorOnboardingService:
    def __init__(self, documents, tickets, authorize, *, clock=time.time):
        if not callable(authorize):
            raise TypeError("an authoritative authorization callback is required")
        self.documents, self.tickets = documents, tickets
        self.authorize, self.clock = authorize, clock

    def check_renewal(self, ctx, case_id):
        """Application boundary used by the dashboard for current renewal status."""
        from .vendor_evidence_evolution import EvidenceRenewalEvolution

        return EvidenceRenewalEvolution(self.authorize, clock=self.clock).run_for_case(ctx, self, case_id)

    def _permit(self, ctx, capability, resource, *, human=False):
        if human and not ctx.human:
            audit_authority_denial(ctx, capability, resource, "authenticated_human_required")
            raise PermissionError("this decision requires an authenticated human")
        try:
            allowed = self.authorize(ctx, capability, resource)
        except Exception:
            audit_authority_denial(ctx, capability, resource, "current_authority_unavailable")
            raise
        if allowed is not True:
            audit_authority_denial(ctx, capability, resource, "current_authority_denied")
            raise PermissionError(f"permission denied: {capability}")

    def _get(self, ctx, case_id, expected_revision=None):
        self._permit(ctx, "read", case_id)
        row = _CASES.get(case_id)
        if not row or row.get("tenant") != ctx.tenant or row.get("owner") != ctx.resource_owner:
            audit_authority_denial(ctx, "read", case_id, "resource_scope_mismatch")
            raise KeyError("vendor onboarding case not found")
        if expected_revision is not None:
            if type(expected_revision) is not int or row["revision"] != expected_revision:
                raise RecordConflict("vendor onboarding case revision changed")
        return self._present(row)

    @staticmethod
    def _contract(row):
        if row.get("schema") != CASE_SCHEMA:
            raise VendorOnboardingContractError(
                "unsupported vendor onboarding schema", code="unsupported_record_version",
            )
        stored = row.get("journey_contract")
        binding = "explicit_at_intake"
        if stored is None:
            stored = JOURNEY_CONTRACT_V1_REF
            binding = "legacy_schema_default"
        if (not isinstance(stored, dict) or set(stored) != {"id", "version", "sha256"}
                or not isinstance(stored.get("id"), str) or type(stored.get("version")) is not int
                or not isinstance(stored.get("sha256"), str)):
            raise VendorOnboardingContractError(
                "invalid journey contract reference", code="invalid_contract_reference",
            )
        contract = _JOURNEY_CONTRACTS.get((stored["id"], stored["version"]))
        if contract is None:
            raise VendorOnboardingContractError(
                "unsupported journey contract", code="unsupported_contract_version",
            )
        expected = {"id": contract["id"], "version": contract["version"],
                    "sha256": digest(contract)}
        if stored != expected:
            raise VendorOnboardingContractError(
                "journey contract digest mismatch", code="contract_integrity_failed",
            )
        return copy.deepcopy(contract), copy.deepcopy(expected), binding

    def _present(self, row):
        contract, reference, binding = self._contract(row)
        try:
            lifecycle, next_action = _WORK_ITEM_STATE[row["stage"]]
        except KeyError as exc:
            raise VendorOnboardingContractError(
                "unsupported vendor onboarding lifecycle state", code="unsupported_lifecycle_state",
            ) from exc
        operation = row.get("operation") or {}
        outcome = row.get("outcome") or {}
        technical = ("verified" if operation.get("state") == "verified" else
                     "indeterminate" if operation.get("state") in {"executing", "reconciliation_required"}
                     else "not_verified")
        if row.get("cancellation") and technical == "verified":
            next_action = "closed_external_effect_retained"
        business = ("accepted" if outcome.get("accepted") is True else
                    "rejected" if outcome.get("accepted") is False else "pending")
        preconditions = operation.get("preconditions") or {}
        credential_versions = {
            name: str(preconditions[name]) for name in (
                "document_credential_version", "ticket_credential_version",
            ) if preconditions.get(name) is not None
        }
        measured_cost = (row.get("metrics") or {}).get("execution_cost_usd")
        cost = ({"state": "exact", "currency": "USD", "amount": str(measured_cost)}
                if type(measured_cost) in (int, float) and math.isfinite(measured_cost)
                else {"state": "unknown", "reason": "provider_billing_unmeasured"})
        freshness = (row.get("evidence") or {}).get("fresh_until")
        idempotency = ({"state": "bound", "key": operation["id"]}
                       if operation.get("id") else
                       {"state": "unknown", "reason": "action_not_prepared"})
        work_item = build_contract(
            "work-item", contract_id=row["id"], tenant_id=row["tenant"],
            owner_id=row["owner"], purpose=row["purpose"],
            created_at=row.get("created_at", row["started_at"]),
            updated_at=row.get("updated_at", row["started_at"]), revision=row["revision"],
            fields={
                "requester": row.get("requested_by") or row["owner"],
                "objective": row["purpose"], "process": reference,
                "contract_binding": binding,
                "supported_deployment": contract["supported_deployment"],
                "acceptance_contract": {
                    "technical": contract["technical_acceptance"],
                    "business": contract["business_acceptance"],
                },
                "out_of_scope": contract["out_of_scope"],
                "lifecycle": lifecycle, "next_action": next_action,
                "technical_effect": technical, "business_outcome": business,
                "target_scope": {"provider": "jira", "project": row["project"]},
                "deployment_profile": contract["supported_deployment"],
                "admission_id": row["id"],
                "acceptance_contract_ref": reference,
                "parent_work_item_id": row.get("renewal_of"),
            },
            provenance={"record_schema": row["schema"], "record_revision": row["revision"]},
            policy_snapshot_ref=reference,
            authority_versions={"journey_contract": f"{reference['id']}:{reference['version']}"},
            credential_versions=credential_versions,
            idempotency=idempotency,
            budget={"state": "unknown", "reason": "workflow_budget_not_configured"},
            cost=cost,
            fresh_until=(freshness if freshness is not None else
                         {"state": "unknown", "reason": "evidence_not_collected"}),
            human_intervention={
                "required": True, "review_count": len(row.get("review_history") or []),
                "acceptance_state": business,
            },
            uncertainty={"state": "bounded" if technical == "verified" else "unknown",
                         "basis": "authoritative_readback" if technical == "verified"
                                  else "technical_effect_not_verified"},
            causal_status={"state": "not_applicable", "basis": "operational_workflow"},
        )
        return dict(row, journey_contract=reference, work_item=work_item)

    def _save(self, ctx, row, *, audit_action="vendor_onboarding_changed", **changes):
        self._contract(row)
        result = _CASES.update(
            row["id"], lambda value: value.update(changes),
            expected_revision=row["revision"], action=audit_action, actor=ctx.actor,
        )
        if result is None:
            raise KeyError("vendor onboarding case disappeared")
        return self._present(result)

    def _editable(self, row):
        self._require_active_intent(row)
        if ((row.get("operation") or {}).get("state") in _CLOSED_OPERATION
                or row.get("outcome") is not None):
            raise ValueError("external execution or acceptance freezes this case; create a renewal")

    @staticmethod
    def _require_active_intent(row):
        if row.get("cancellation") or row.get("stage") == "cancelled":
            raise ValueError("this request is cancelled; only existing-effect reconciliation remains available")

    @_scoped
    def cancel(self, ctx, case_id, expected_revision, rationale):
        """Stop local intent without compensating or erasing provider effects.

        The saga lock serializes cancellation with dispatch. A caller that
        reviewed an earlier revision must reload the actual effect state first.
        A durable execution claim is conservatively possibly dispatched even
        when the worker lost authority before reaching the provider.
        """
        row = self._get(ctx, case_id, expected_revision)
        self._permit(ctx, "action.cancel", case_id, human=True)
        why = _text(rationale, "rationale")
        if row.get("cancellation"):
            return row
        if row.get("outcome") is not None:
            raise ValueError("an existing customer decision cannot be replaced by cancellation")
        operation = row.get("operation") or {}
        attempted = (operation.get("state") in _CLOSED_OPERATION
                     or operation.get("attempts", 0) > 0)
        verified = operation.get("state") == "verified"
        cancellation = {
            "requested_by": ctx.actor, "requested_at": self.clock(), "rationale": why,
            "authority": ctx.binding(), "reviewed_revision": row["revision"],
            "operation_id": operation.get("id"), "scope": "local_intent_only",
            "dispatch_state_at_cancellation": "possibly_dispatched" if attempted else "not_dispatched",
            "technical_effect_at_cancellation": ("verified" if verified else
                                                  "indeterminate" if attempted else "not_verified"),
            "effect_reversal": "not_requested",
        }
        changes = ({"operation": dict(operation, state="verified" if verified else "reconciliation_required")}
                   if attempted else self._invalidate(row, reason="local_intent_cancelled"))
        return self._save(
            ctx, row, cancellation=cancellation,
            stage="reconciliation_required" if attempted and not verified else "cancelled",
            audit_action="vendor_onboarding_cancelled", **changes,
        )

    def _invalidate(self, row, *, reason=None):
        history = list(row.get("operation_history") or [])
        if row.get("operation"):
            invalidated = dict(row["operation"], state="invalidated")
            if reason:
                invalidated["invalidated_reason"] = reason
            history.append(invalidated)
        return {"operation": None, "operation_history": history}

    def _require_new_action_review(
        self,
        ctx,
        row,
        *,
        code,
        message,
        error_type=ApprovalBindingError,
    ) -> NoReturn:
        audit_authority_denial(ctx, "action.execute", row["id"], code)
        self._save(
            ctx,
            row,
            stage="review_approved",
            **self._invalidate(row, reason=code),
        )
        raise error_type(message, code=code)

    @_scoped
    def intake(self, ctx, vendor_name, document_id, project, purpose, *,
               freshness_days=365, renewal_of=None, document_ids=None):
        self._permit(ctx, "intake", ctx.resource_owner)
        name = _text(vendor_name, "vendor_name", 256)
        validated_purpose = _text(purpose, "purpose")
        ids = self._document_ids(document_ids if document_ids is not None else ([document_id] if document_id else []))
        if document_id and document_id not in ids:
            raise ValueError("primary document must be included in document_ids")
        doc = ids[0] if ids else ""
        uploads = []
        if not isinstance(project, str) or not _PROJECT.fullmatch(project):
            raise ValueError("project must be a literal Jira project key")
        if type(freshness_days) is not int or not 1 <= freshness_days <= 730:
            raise ValueError("freshness_days must be between 1 and 730")
        if renewal_of:
            prior = self._get(ctx, renewal_of)
            if not (prior.get("outcome") or {}).get("accepted"):
                raise ValueError("renewal requires a previously accepted case")
            if name != prior["vendor_name"]:
                raise ValueError("renewal vendor identity changed")
            uploads = list(prior.get("uploads") or [])
        if len(ids) + len(uploads) > documents.MAX_DOCUMENTS:
            raise ValueError("a case supports at most 8 evidence documents")
        vendor_id = vendors._vendor_id(name)
        vendor = vendors.get_vendor(vendor_id)
        if vendor is None:
            try:
                vendor = vendors.register_vendor(
                    name, category="AI vendor", criticality="high", owner=ctx.resource_owner,
                    actor=ctx.actor, source="vendor_onboarding",
                )
            except RecordConflict:
                vendor = vendors.get_vendor(vendor_id)
        if vendor is None or vendor["owner"] != ctx.resource_owner:
            raise PermissionError("vendor belongs to a different resource owner")
        started_at = self.clock()
        row = _CASES.create({
            "id": _CASES.new_id(), "schema": CASE_SCHEMA,
            "tenant": ctx.tenant, "owner": ctx.resource_owner,
            "requested_by": ctx.actor,
            "journey_contract": copy.deepcopy(CURRENT_JOURNEY_CONTRACT_REF),
            "vendor_id": vendor_id, "vendor_name": name, "document_id": doc,
            "document_ids": ids, "uploads": uploads,
            "project": project, "purpose": validated_purpose,
            "freshness_days": freshness_days, "renewal_of": renewal_of,
            "stage": "intake", "evidence": None, "assessment": None,
            "review_history": [], "operation": None, "operation_history": [],
            "exceptions": [], "outcome": None, "started_at": started_at,
            "metrics": {"execution_cost_usd": 0.0 if self.documents.mode == "offline_synthetic"
                        and self.tickets.mode == "offline_synthetic" else None,
                        "cost_basis": "no model calls; provider billing unmeasured",
                        "human_review_seconds": 0.0, "document_reads": 0},
            "integration_mode": {"documents": self.documents.mode, "tickets": self.tickets.mode},
        }, action="vendor_onboarding_intake", actor=ctx.actor, max_records=10000)
        return self._present(row)

    @staticmethod
    def _document_ids(values):
        if not isinstance(values, list) or len(values) > documents.MAX_DOCUMENTS:
            raise ValueError("document_ids must be a list of at most 8 Drive file IDs")
        cleaned = [_text(value, "document_id", 256) for value in values]
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("each evidence document must be selected once")
        return sorted(cleaned)

    @_scoped
    def search_documents(self, ctx, query):
        self._permit(ctx, "evidence.read", ctx.resource_owner)
        return self.documents.search(ctx, query)

    @_scoped
    def set_documents(self, ctx, case_id, expected_revision, document_ids, upload_ids):
        row = self._get(ctx, case_id, expected_revision)
        self._editable(row)
        self._permit(ctx, "evidence.read", case_id)
        ids = self._document_ids(document_ids)
        if not isinstance(upload_ids, list) or any(not isinstance(value, str) for value in upload_ids) or len(set(upload_ids)) != len(upload_ids):
            raise ValueError("uploaded evidence references must be distinct")
        uploads = {item["document_id"]: item for item in row.get("uploads", [])}
        if set(upload_ids) - uploads.keys() or len(ids) + len(upload_ids) > documents.MAX_DOCUMENTS:
            raise ValueError("select at most 8 documents belonging to this case")
        return self._save(ctx, row, document_ids=ids, document_id=ids[0] if ids else "",
                          uploads=[uploads[key] for key in sorted(upload_ids)], evidence=None,
                          assessment=None, stage="intake", **self._invalidate(row))

    @_scoped
    def upload_document(self, ctx, case_id, expected_revision, *, content, name, mime, document_date):
        row = self._get(ctx, case_id, expected_revision)
        self._editable(row)
        self._permit(ctx, "evidence.upload", case_id, human=True)
        if len(row.get("document_ids", [row["document_id"]])) + len(row.get("uploads", [])) >= documents.MAX_DOCUMENTS:
            raise ValueError("remove a document before adding more than 8")
        filename = _text(name, "document name", 256)
        if re.search(r"[\x00-\x1f/\\]", filename):
            raise ValueError("document name must not contain a path or control characters")
        if type(document_date) not in (int, float) or not math.isfinite(document_date):
            raise ValueError("provide the document's actual date")
        document = EvidenceDocument("upload-" + uuid.uuid4().hex, "1", document_date, content, mime)
        result = documents.extract(document, source="upload", name=filename, now=self.clock(),
                                   freshness_days=row["freshness_days"])
        result.update(uploaded_by=ctx.actor, uploaded_at=self.clock(),
                      verification_basis="retained_upload_snapshot; no provider readback")
        # Failed extraction remains visible and must be removed or replaced;
        # it never becomes a complete assessment through partial success.
        return self._save(ctx, row, uploads=[*row.get("uploads", []), result], evidence=None,
                          assessment=None, stage="intake", **self._invalidate(row))

    def _upload_preview(self, ctx, case_id, expected_revision, *, content, name, mime,
                        document_date, transcription=None, expires_at=None):
        from .config import vendor_reviewed_uploads_enabled

        if not vendor_reviewed_uploads_enabled():
            raise PermissionError("Reviewed evidence uploads are disabled by this deployment")
        row = self._get(ctx, case_id, expected_revision)
        self._editable(row)
        self._permit(ctx, "evidence.upload", case_id, human=True)
        if len(row.get("document_ids", [])) + len(row.get("uploads", [])) >= documents.MAX_DOCUMENTS:
            raise ValueError("Remove a document before adding more than 8")
        if len(content) + sum(item.get("byte_length", 0) for item in row.get("uploads", [])) > documents.MAX_TOTAL_BYTES:
            raise ValueError("Uploaded evidence exceeds the 32 MiB request limit; remove a file before continuing")
        filename = _text(name, "document name", 256)
        if re.search(r"[\x00-\x1f/\\]", filename):
            raise ValueError("Document name must not contain a path or control characters")
        if type(document_date) not in (int, float) or not math.isfinite(document_date):
            raise ValueError("Provide the document's actual date")
        now = self.clock()
        expires_at = now + 600 if expires_at is None else expires_at
        if type(expires_at) not in (int, float) or not math.isfinite(expires_at) or not now < expires_at <= now + 600:
            raise ValueError("Evidence preview expired; review the selected file again")
        identity = digest({"bytes": hashlib.sha256(content).hexdigest(), "name": filename,
                           "mime": mime, "document_date": document_date, "transcription": transcription})
        document = EvidenceDocument("upload-" + identity[:32], "1", document_date, content, mime)
        if any(item["document_id"] == document.document_id for item in row.get("uploads", [])):
            raise ValueError("This reviewed file is already attached to the request")
        preview = {}
        result = documents.extract(document, source="upload", name=filename, now=now,
                                   freshness_days=row["freshness_days"], transcription=transcription, preview=preview)
        binding = digest({"context": asdict(ctx), "case_id": case_id, "revision": expected_revision,
                          "source": identity, "text": preview["text"], "expires_at": expires_at,
                          "extraction": result["extraction"]})
        return row, result, dict(preview, name=filename, mime=mime, source_sha256=result["content_sha256"],
            text_sha256=hashlib.sha256(preview["text"].encode("utf-8")).hexdigest(),
            status=result["status"], extraction=result["extraction"], review_digest=binding,
            expires_at=expires_at, case_id=case_id, expected_revision=expected_revision,
            review_required=True, saved=False)

    @_scoped
    def preview_upload(self, ctx, case_id, expected_revision, **kwargs):
        """Transient review only: no case mutation, provider call or full-text retention."""
        return self._upload_preview(ctx, case_id, expected_revision, **kwargs)[2]

    @_scoped
    def upload_reviewed(self, ctx, case_id, expected_revision, *, confirm, review_digest, **kwargs):
        if confirm is not True:
            raise ValueError("Confirm that you reviewed the extracted text against the original file")
        row, result, preview = self._upload_preview(ctx, case_id, expected_revision, **kwargs)
        if not isinstance(review_digest, str) or review_digest != preview["review_digest"]:
            raise ValueError("The file, text, account or request changed; review the evidence again")
        if result["status"] != "available":
            raise ValueError("No supported text is ready; use the transcription option or select another file")
        result.update(uploaded_by=ctx.actor, uploaded_at=self.clock(), reviewed_by=ctx.actor,
                      reviewed_at=self.clock(), review_basis="uploader_attested_original_comparison",
                      verification_basis="retained_upload_snapshot; no provider readback")
        return self._save(ctx, row, uploads=[*row.get("uploads", []), result], evidence=None,
                          assessment=None, stage="intake", **self._invalidate(row))

    @_scoped
    def get(self, ctx, case_id):
        return self._get(ctx, case_id)

    @_scoped
    def list(self, ctx):
        self._permit(ctx, "read", ctx.resource_owner)
        rows = _CASES.list(limit=10001)
        if len(rows) > 10000:
            raise RuntimeError("case capacity exceeded; list completeness unavailable")
        return [self._present(row) for row in rows if row.get("tenant") == ctx.tenant
                and row.get("owner") == ctx.resource_owner
                and self.authorize(ctx, "read", row["id"]) is True]

    @_scoped
    def collect_evidence(self, ctx, case_id, expected_revision):
        row = self._get(ctx, case_id, expected_revision)
        self._editable(row)
        self._permit(ctx, "evidence.read", case_id)
        results = []
        ids = row.get("document_ids", [row["document_id"]])
        for document_id in ids:
            result = {"document_id": document_id, "source": "gdrive", "collected_at": self.clock()}
            try:
                document = self.documents.fetch(ctx, document_id)
                if document.document_id != document_id:
                    raise ValueError("unsupported or corrupted evidence metadata")
                result = documents.extract(document, source="gdrive", name=document_id,
                                           now=self.clock(), freshness_days=row["freshness_days"])
                result.update(credential_ref=ctx.document_credential_ref,
                              credential_version=self.documents.authority_version(ctx))
            except FileNotFoundError:
                result["status"] = "missing"
            except (ValueError, UnicodeError):
                result["status"] = "corrupted"
            except PermissionError:
                raise
            except Exception:
                result["status"] = "unavailable"
            results.append(result)
        results.extend(dict(item, fresh_until=item["modified_at"] + row["freshness_days"] * 86400)
                       for item in row.get("uploads", []))
        evidence = self._evidence_set(results)
        previous = row.get("evidence")
        if row.get("renewal_of"):
            previous = self._get(ctx, row["renewal_of"]).get("evidence")
        evidence["changes"] = documents.document_diff(previous, evidence)
        metrics = dict(row["metrics"], document_reads=row["metrics"]["document_reads"] + len(ids))
        exceptions = list(row["exceptions"])
        if evidence["status"] != "available":
            exceptions.append({"kind": "evidence_" + evidence["status"], "at": self.clock(),
                               "supersedes_case_revision": row["revision"]})
        return self._save(ctx, row, evidence=evidence, assessment=None, stage="evidence_collected",
                          metrics=metrics, exceptions=exceptions, **self._invalidate(row))

    def _evidence_set(self, results):
        results = sorted(results, key=lambda item: (item["source"], item["document_id"]))
        failures = [item for item in results if item["status"] != "available"]
        status = "missing" if not results else failures[0]["status"] if failures else "available"
        if sum(item.get("byte_length", 0) for item in results) > documents.MAX_TOTAL_BYTES:
            status = "extraction_limit"
        if len(results) == 1:
            evidence = dict(results[0])
        else:
            evidence = {"source": "evidence_set", "version": digest([dict(self._identity(item), source=item["source"]) for item in results]),
                        "content_sha256": digest([{key: item.get(key) for key in ("source", "document_id", "version", "content_sha256")}
                                                   for item in results]),
                        "fresh_until": min((item.get("fresh_until", 0) for item in results), default=0)}
        return dict(evidence, status=status, documents=results, collected_at=self.clock())

    @staticmethod
    def _identity(item):
        return {key: item.get(key) for key in ("document_id", "version", "modified_at", "mime")}

    def current_evidence_version(self, ctx, row, *, verify_bytes=False):
        evidence = row["evidence"]
        observed = []
        items = sorted(evidence.get("documents") or [evidence],
                       key=lambda item: (item.get("source", "gdrive"), item["document_id"]))
        for item in items:
            if item.get("source") == "upload":
                current = next((upload for upload in row.get("uploads", []) if upload["document_id"] == item["document_id"]), None)
                if current is None or current["content_sha256"] != item["content_sha256"] or self._identity(current) != self._identity(item):
                    raise ValueError("uploaded evidence snapshot changed")
                head = self._identity(current)
            else:
                if verify_bytes:
                    document = self.documents.fetch(ctx, item["document_id"])
                    head = document.identity()
                    if hashlib.sha256(document.content).hexdigest() != item["content_sha256"]:
                        raise ValueError("source evidence bytes changed")
                else:
                    head = self.documents.head(ctx, item["document_id"])
                if head != self._identity(item):
                    raise ValueError("source evidence version changed")
            observed.append(dict(head, source=item.get("source", "gdrive")))
        return observed[0]["version"] if len(observed) == 1 else digest(observed)

    def _draft(self, ctx, row, answers=None, marker=None):
        # Recover a risk record created before the saga's reference write failed.
        actor = (marker or f"onboarding:{row['id']}:{row['revision']}") + ":" + digest(answers)[:20]
        found = [r for r in risk._bounded_records() if r.get("assessed_by") == actor]
        if len(found) > 1:
            raise RuntimeError("assessment intent has multiple records; human reconciliation required")
        if found:
            return risk.get_vendor_assessment(found[0]["id"])
        return risk.draft_vendor_assessment(
            row["vendor_name"], answers=answers,
            document_results=[item["extracted"] for item in row["evidence"].get("documents") or [row["evidence"]]],
            actor=actor, use_prior=False,
        )

    @_scoped
    def assess(self, ctx, case_id, expected_revision):
        row = self._get(ctx, case_id, expected_revision)
        self._editable(row)
        self._permit(ctx, "assess", case_id)
        if (row.get("evidence") or {}).get("status") != "available":
            raise ValueError("assessment requires available, nonempty, supported evidence")
        assessment = self._draft(ctx, row)
        return self._save(ctx, row, assessment=assessment, stage="pending_review",
                          **self._invalidate(row))

    @_scoped
    def review(self, ctx, case_id, expected_revision, decision, rationale, *,
               corrections=None, review_seconds=None):
        row = self._get(ctx, case_id, expected_revision)
        self._editable(row)
        self._permit(ctx, "review", case_id, human=True)
        if decision not in {"approved", "rejected"}:
            raise ValueError("decision must be approved or rejected")
        why = _text(rationale, "rationale")
        seconds = 0.0
        if review_seconds is not None:
            if (type(review_seconds) not in (int, float) or not math.isfinite(review_seconds)
                    or not 0 <= review_seconds <= 86400):
                raise ValueError("review_seconds must be finite and between zero and one day")
            seconds = float(review_seconds)
        assessment = row.get("assessment")
        if not assessment:
            raise ValueError("assessment required")
        original = {"id": assessment["id"], "revision": assessment["revision"],
                    "assessment_sha256": assessment["assessment_sha256"]}
        if corrections:
            if not isinstance(corrections, dict):
                raise ValueError("corrections must map questionnaire IDs to typed answers")
            answers = {key: {"answer": value["answer"], "note": value.get("note", "")}
                       for key, value in assessment["answers"].items()}
            if not set(corrections).issubset(answers):
                raise ValueError("unknown correction question")
            answers.update(corrections)
            assessment = self._draft(ctx, row, answers, marker=f"correction:{case_id}:{row['revision']}")
        else:
            current = risk.get_vendor_assessment(assessment["id"])
            if current is None or current["assessment_sha256"] != assessment["assessment_sha256"]:
                raise ValueError("assessment source changed")
            assessment = current
        if assessment["status"] == "pending_review":
            assessment = risk.review_vendor_assessment(
                assessment["id"], decision=decision, rationale=why, reviewer=ctx.actor,
                expected_revision=assessment["revision"],
            )
        else:
            previous_review = assessment.get("review") or {}
            if (assessment["status"] != decision or previous_review.get("reviewer") != ctx.actor
                    or previous_review.get("rationale") != why
                    or (not corrections and row["assessment"]["status"] != "pending_review")):
                raise ValueError("reviewed assessment requires a new corrected draft")
        review = {"reviewer": ctx.actor, "decision": decision, "rationale": why,
                  "at": self.clock(), "corrections": corrections or {}, "supersedes": original,
                  "evidence_sha256": row["evidence"]["content_sha256"],
                  "assessment_id": assessment["id"], "assessment_revision": assessment["revision"],
                  "workflow_schema": row["schema"], "review_seconds": review_seconds,
                  "review_time_basis": "reported_by_human" if review_seconds is not None else "unmeasured"}
        return self._save(ctx, row, assessment=assessment, stage=f"review_{decision}",
                          review_history=[*row["review_history"], review],
                          metrics=dict(row["metrics"], human_review_seconds=
                                       row["metrics"]["human_review_seconds"] + seconds),
                          **self._invalidate(row))

    def _preconditions(self, ctx, row):
        evidence = row["evidence"]
        assessment = risk.get_vendor_assessment(row["assessment"]["id"])
        if assessment is None or assessment["status"] != "approved":
            raise ValueError("current domain assessment is not approved")
        if assessment["revision"] != row["assessment"]["revision"]:
            raise ValueError("domain assessment version changed")
        if evidence["fresh_until"] <= self.clock():
            raise ValueError("evidence freshness expired; renew evidence before execution")
        source_version = self.current_evidence_version(ctx, row)
        vendor = vendors.get_vendor(row["vendor_id"])
        if not vendor or vendor["owner"] != ctx.resource_owner or vendor["status"] == "offboarded":
            raise PermissionError("vendor ownership or lifecycle changed")
        return {"evidence_sha256": evidence["content_sha256"], "source_version": source_version,
                "assessment_id": assessment["id"], "assessment_revision": assessment["revision"],
                "assessment_sha256": assessment["assessment_sha256"],
                "vendor_revision": vendor["revision"],
                "document_credential_version": (self.documents.authority_version(ctx)
                    if any(item.get("source") != "upload" for item in evidence.get("documents") or [evidence])
                    else "not_applicable:retained_uploads"),
                "ticket_credential_version": self.tickets.authority_version(ctx)}

    @_scoped
    def prepare_action(self, ctx, case_id, expected_revision):
        row = self._get(ctx, case_id, expected_revision)
        self._editable(row)
        self._permit(ctx, "action.prepare", case_id)
        if not row.get("assessment") or row["assessment"]["status"] != "approved":
            raise ValueError("human-reviewed approved assessment required")
        if row.get("operation"):
            raise ValueError("operation already prepared; use its exact approval")
        preconditions = self._preconditions(ctx, row)
        op_id = "VOP-" + uuid.uuid4().hex
        payload = {
            "project": row["project"],
            "summary": f"AI vendor diligence: {row['vendor_name']}"[:255],
            "description": (
                f"Vendor: {row['vendor_name']}\nRequest: {row['purpose']}\nCase: {case_id}\n"
                f"Assessment: {row['assessment']['id']} revision {row['assessment']['revision']}\n"
                f"Assessment digest: {row['assessment']['assessment_sha256']}\n"
                f"Risk: {row['assessment']['result']['risk_rating']}\n"
                f"Evidence: {len(row['evidence'].get('documents') or [row['evidence']])} document(s), version {row['evidence']['version']}\n"
                f"Evidence digest: {row['evidence']['content_sha256']}\n"
                f"Renewal due (Unix seconds): {row['evidence']['fresh_until']}\n"
                f"Human review: {row['review_history'][-1]['rationale']}"
            ),
            "marker": "lightwork-" + op_id.lower(),
        }
        operation = {"id": op_id, "action": "jira.create", "state": "prepared",
                     "context": ctx.binding(), "payload": payload, "payload_sha256": digest(payload),
                     "preconditions": preconditions, "scope": "create_one_jira_task",
                     "expires_at": min(self.clock() + 3600, row["evidence"]["fresh_until"]),
                     "approval": None, "receipt": None, "external_confirmation": None,
                     "provider_idempotency": False, "attempts": 0}
        return self._save(ctx, row, operation=operation, stage="action_pending_approval")

    @_scoped
    def approve_action(self, ctx, case_id, expected_revision):
        row = self._get(ctx, case_id, expected_revision)
        self._permit(ctx, "action.approve", case_id, human=True)
        self._require_active_intent(row)
        operation = row.get("operation")
        if not operation or operation["state"] != "prepared":
            raise ValueError("prepared operation required")
        # Review credentials may differ from execution credentials; source and
        # domain checks always use the bound execution principal.
        execution = ServiceContext(**operation["context"])
        if operation["context"] != execution.binding():
            self._require_new_action_review(
                ctx, row, code="approval_authority_changed",
                message="Operation authority changed; prepare and review a new action.",
            )
        try:
            self._permit(execution, "action.execute", case_id)
        except PermissionError:
            self._require_new_action_review(
                ctx, row, code="approval_authority_changed",
                message="Operation authority changed; prepare and review a new action.",
            )
        self._check_prepared_inputs(execution, row)
        approval = {"reviewer": ctx.actor, "at": self.clock(),
                    "binding_sha256": operation_binding(operation), "scope": operation["scope"]}
        return self._save(ctx, row, operation=dict(operation, state="approved", approval=approval),
                          stage="action_approved")

    def _check_prepared_inputs(self, ctx, row):
        try:
            matches = self._preconditions(ctx, row) == row["operation"]["preconditions"]
        except (PermissionError, ValueError):
            # Provider exception text is untrusted and may contain credentials.
            matches = False
        if not matches:
            self._require_new_action_review(
                ctx, row, code="approval_preconditions_changed",
                message="Action inputs or connection access changed; refresh the evidence and review a new action.",
                error_type=ActionReviewRequiredError,
            )

    def _permit_readback(self, ctx, row, operation):
        """Authorize a current observer without rewriting historical authority.

        Navigation, actor and spend policy changes cannot grant another create
        or strand an existing effect. The original connector enrollment and
        credential generation still pin its destination for read-only checks.
        """
        self._permit(ctx, "action.reconcile", row["id"])
        original = operation.get("context") or {}
        for field in ("tenant", "resource_owner", "credential_authority", "credential_ref"):
            if original.get(field) != getattr(ctx, field):
                audit_authority_denial(ctx, "action.reconcile", row["id"], "effect_scope_changed")
                raise PermissionError(
                    "Original effect credential scope is unavailable; restore its enrolled connection for readback."
                )
        if digest(operation["payload"]) != operation["payload_sha256"]:
            raise ValueError("operation payload digest mismatch")
        if (operation.get("approval") or {}).get("binding_sha256") != operation_binding(operation):
            raise ValueError("original operation approval binding is invalid")

    def _finish(self, ctx, row, operation, key, *, reconciled=False):
        self._permit_readback(ctx, row, operation)
        confirmation = self.tickets.verify(ctx, key, operation["payload"],
                                           expected_credential_version=operation["preconditions"]["ticket_credential_version"])
        if confirmation.get("verified") is not True:
            raise ValueError("external record does not match the approved payload")
        confirmation = dict(confirmation, confirmed_at=self.clock(), reconciled=reconciled,
                            observer=ctx.binding())
        return self._save(ctx, row,
                          operation=dict(operation, state="verified", external_confirmation=confirmation),
                          stage="cancelled" if row.get("cancellation") else "execution_completed",
                          completed_at=self.clock())

    @_scoped
    def execute(self, ctx, case_id):
        row = self._get(ctx, case_id)
        self._permit(ctx, "action.execute", case_id)
        self._require_active_intent(row)
        operation = row.get("operation")
        if not operation:
            raise ValueError("approved operation required")
        if operation["state"] == "verified":
            return row
        if operation["state"] in {"executing", "reconciliation_required"}:
            return self._save(ctx, row, stage="reconciliation_required",
                              operation=dict(operation, state="reconciliation_required"))
        if operation["state"] != "approved":
            raise ValueError("approved operation required")
        try:
            validate_approval(operation, ctx, self.clock())
        except ApprovalBindingError as exc:
            self._require_new_action_review(ctx, row, code=exc.code, message=str(exc))
        approver = ServiceContext(**operation["context"], human=True)
        approver = ServiceContext(**dict(asdict(approver), actor=operation["approval"]["reviewer"]))
        self._permit(approver, "action.approve", case_id, human=True)
        self._check_prepared_inputs(ctx, row)
        operation = dict(operation, state="executing", attempts=operation["attempts"] + 1,
                         started_at=self.clock())
        row = self._save(ctx, row, operation=operation, stage="executing")
        try:
            # Last policy check follows the durable claim; any failure from this
            # point remains reconciliable and never blindly resubmits a create.
            self._permit(ctx, "action.execute", case_id)
            if self._preconditions(ctx, row) != operation["preconditions"]:
                raise ValueError("preconditions changed after durable claim")
            validate_approval(operation, ctx, self.clock())
            self._permit(approver, "action.approve", case_id, human=True)
            self._permit(ctx, "action.execute", case_id)
            receipt, lineage = apply_declared(
                operation, lambda payload: self.tickets.create(
                    ctx, payload, operation["id"],
                    expected_credential_version=operation["preconditions"]["ticket_credential_version"],
                ),
            )
            operation = dict(operation, receipt=receipt, lineage=lineage)
            return self._finish(ctx, row, operation, receipt["key"])
        except Exception as exc:
            # Store only a failure class: provider responses may contain secrets.
            return self._save(ctx, row, stage="reconciliation_required",
                              operation=dict(operation, state="reconciliation_required",
                                             failure_kind=type(exc).__name__))

    @_scoped
    def reconcile(self, ctx, case_id):
        row = self._get(ctx, case_id)
        self._permit(ctx, "action.reconcile", case_id)
        operation = row.get("operation") or {}
        if operation.get("state") not in {"executing", "reconciliation_required", "verified"}:
            raise ValueError("an attempted operation is required")
        self._permit_readback(ctx, row, operation)
        try:
            matches = self.tickets.find(ctx, operation["payload"],
                                        expected_credential_version=operation["preconditions"]["ticket_credential_version"])
            if len(matches) != 1:
                raise ValueError("zero or multiple matches; human reconciliation required")
            return self._finish(ctx, row, operation, matches[0], reconciled=True)
        except Exception as exc:
            return self._save(ctx, row, stage="reconciliation_required",
                              operation=dict(operation, state="reconciliation_required",
                                             failure_kind=type(exc).__name__))

    @_scoped
    def accept(self, ctx, case_id, expected_revision, accepted, rationale):
        row = self._get(ctx, case_id, expected_revision)
        self._permit(ctx, "accept", case_id, human=True)
        self._require_active_intent(row)
        if type(accepted) is not bool or row.get("outcome") is not None:
            raise ValueError("one explicit boolean acceptance decision is required")
        why = _text(rationale, "rationale")
        operation = row.get("operation") or {}
        if operation.get("state") != "verified":
            raise ValueError("verified external effect required before customer acceptance")
        self._permit_readback(ctx, row, operation)
        observed = self.tickets.verify(ctx, operation["external_confirmation"]["key"],
                                       operation["payload"],
                                       expected_credential_version=operation["preconditions"]["ticket_credential_version"])
        if observed.get("verified") is not True:
            raise ValueError("external state changed before acceptance")
        observed = dict(observed, confirmed_at=self.clock(), observer=ctx.binding())
        vendor = vendors.get_vendor(row["vendor_id"])
        if vendor is None or vendor["owner"] != ctx.resource_owner:
            raise PermissionError("vendor ownership unavailable or changed")
        if not any(link.get("kind") == "ai_vendor_assessment"
                   and link.get("reference") == row["assessment"]["id"]
                   for link in vendor.get("links", [])):
            # Reference-before-outcome is recoverable: a retry observes the
            # existing link and cannot manufacture a second external effect.
            vendors.link_vendor_record(row["vendor_id"], kind="ai_vendor_assessment",
                                       reference=row["assessment"]["id"], actor=ctx.actor,
                                       expected_revision=vendor["revision"])
        outcome = {"accepted": accepted, "reviewer": ctx.actor, "rationale": why,
                   "accepted_at": self.clock(), "execution_completed": True,
                   "validation": "verified_external_record", "released": False,
                   "journey_contract": copy.deepcopy(row["journey_contract"]),
                   "external_confirmation": observed,
                   "assessment_id": row["assessment"]["id"],
                   "assessment_sha256": row["assessment"]["assessment_sha256"],
                   "evidence_sha256": row["evidence"]["content_sha256"],
                   "operation_id": operation["id"], "workflow_schema": row["schema"],
                   "elapsed_seconds": max(0.0, self.clock() - row["started_at"]),
                   "execution_cost_usd": row["metrics"]["execution_cost_usd"],
                   "renewal_due_at": row["evidence"]["fresh_until"] if accepted else None}
        return self._save(ctx, row, outcome=outcome,
                          stage="accepted" if accepted else "customer_rejected")
