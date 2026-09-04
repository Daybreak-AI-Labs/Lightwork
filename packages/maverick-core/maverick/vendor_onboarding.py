"""AI vendor onboarding application service over existing domain authorities.

The vendor register and AI vendor-risk register remain their systems of record.
This tenant-scoped saga stores coordination, operation intent and customer
outcomes. The supported deployment is one persistent application replica.
Provider effects cannot participate in its local transaction: ambiguous creates
require read-only reconciliation, including after a lost receipt or restart.
"""
from __future__ import annotations

import functools
import hashlib
import math
import re
import threading
import time
import uuid
from dataclasses import asdict

from . import ai_vendor_risk as risk
from . import vendor_management as vendors
from .file_lock import cross_process_lock
from .governed_operations import (
    ServiceContext,
    apply_declared,
    digest,
    operation_binding,
    validate_approval,
)
from .governed_records import GovernedRecordStore, RecordConflict
from .paths import data_dir, tenant_scope

_CASES = GovernedRecordStore("vendor_onboarding", "VON", "vendor_onboarding_case")
_LOCK = threading.RLock()
_PROJECT = re.compile(r"[A-Z][A-Z0-9_]{1,31}\Z")
_CLOSED_OPERATION = {"executing", "reconciliation_required", "verified"}


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
            raise PermissionError("this decision requires an authenticated human")
        if self.authorize(ctx, capability, resource) is not True:
            raise PermissionError(f"permission denied: {capability}")

    def _get(self, ctx, case_id, expected_revision=None):
        self._permit(ctx, "read", case_id)
        row = _CASES.get(case_id)
        if not row or row.get("tenant") != ctx.tenant or row.get("owner") != ctx.resource_owner:
            raise KeyError("vendor onboarding case not found")
        if expected_revision is not None:
            if type(expected_revision) is not int or row["revision"] != expected_revision:
                raise RecordConflict("vendor onboarding case revision changed")
        return row

    def _save(self, ctx, row, **changes):
        result = _CASES.update(
            row["id"], lambda value: value.update(changes),
            expected_revision=row["revision"], action="vendor_onboarding_changed", actor=ctx.actor,
        )
        if result is None:
            raise KeyError("vendor onboarding case disappeared")
        return result

    def _editable(self, row):
        if ((row.get("operation") or {}).get("state") in _CLOSED_OPERATION
                or row.get("outcome") is not None):
            raise ValueError("external execution or acceptance freezes this case; create a renewal")

    def _invalidate(self, row):
        history = list(row.get("operation_history") or [])
        if row.get("operation"):
            history.append(dict(row["operation"], state="invalidated"))
        return {"operation": None, "operation_history": history}

    @_scoped
    def intake(self, ctx, vendor_name, document_id, project, purpose, *,
               freshness_days=365, renewal_of=None):
        self._permit(ctx, "intake", ctx.resource_owner)
        name = _text(vendor_name, "vendor_name", 256)
        doc = _text(document_id, "document_id", 256)
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
        row = _CASES.create({
            "id": _CASES.new_id(), "schema": "lightwork.vendor-onboarding.v1",
            "tenant": ctx.tenant, "owner": ctx.resource_owner,
            "vendor_id": vendor_id, "vendor_name": name, "document_id": doc,
            "project": project, "purpose": _text(purpose, "purpose"),
            "freshness_days": freshness_days, "renewal_of": renewal_of,
            "stage": "intake", "evidence": None, "assessment": None,
            "review_history": [], "operation": None, "operation_history": [],
            "exceptions": [], "outcome": None, "started_at": self.clock(),
            "metrics": {"execution_cost_usd": 0.0 if self.documents.mode == "offline_synthetic"
                        and self.tickets.mode == "offline_synthetic" else None,
                        "cost_basis": "no model calls; provider billing unmeasured",
                        "human_review_seconds": 0.0, "document_reads": 0},
            "integration_mode": {"documents": self.documents.mode, "tickets": self.tickets.mode},
        }, action="vendor_onboarding_intake", actor=ctx.actor, max_records=10000)
        return row

    @_scoped
    def get(self, ctx, case_id):
        return self._get(ctx, case_id)

    @_scoped
    def list(self, ctx):
        self._permit(ctx, "read", ctx.resource_owner)
        rows = _CASES.list(limit=10001)
        if len(rows) > 10000:
            raise RuntimeError("case capacity exceeded; list completeness unavailable")
        return [row for row in rows if row.get("tenant") == ctx.tenant
                and row.get("owner") == ctx.resource_owner
                and self.authorize(ctx, "read", row["id"]) is True]

    @_scoped
    def collect_evidence(self, ctx, case_id, expected_revision):
        row = self._get(ctx, case_id, expected_revision)
        self._editable(row)
        self._permit(ctx, "evidence.read", case_id)
        try:
            document = self.documents.fetch(ctx, row["document_id"])
            if (document.document_id != row["document_id"] or not document.version
                    or not math.isfinite(document.modified_at) or document.modified_at > self.clock()
                    or document.mime != "text/plain" or len(document.content) > 128 * 1024):
                raise ValueError("unsupported or corrupted evidence metadata")
            text = document.content.decode("utf-8", errors="strict")
            if not text.strip():
                state = "empty"
                extracted = None
            else:
                extracted = risk.extract_answers_from_document(
                    document.content, document.mime, source="gdrive",
                    document_id=document.document_id, ref={"version": document.version},
                )
                state = "available"
            evidence = dict(document.identity(), status=state, source="gdrive",
                            content_sha256=hashlib.sha256(document.content).hexdigest(),
                            collected_at=self.clock(), extracted=extracted,
                            credential_ref=ctx.document_credential_ref,
                            credential_version=self.documents.authority_version(ctx),
                            fresh_until=document.modified_at + row["freshness_days"] * 86400)
        except FileNotFoundError:
            evidence = {"status": "missing", "source": "gdrive", "collected_at": self.clock()}
        except (ValueError, UnicodeError):
            evidence = {"status": "corrupted", "source": "gdrive", "collected_at": self.clock()}
        except PermissionError:
            raise
        except Exception:
            evidence = {"status": "unavailable", "source": "gdrive", "collected_at": self.clock()}
        metrics = dict(row["metrics"], document_reads=row["metrics"]["document_reads"] + 1)
        exceptions = list(row["exceptions"])
        if evidence["status"] != "available":
            exceptions.append({"kind": "evidence_" + evidence["status"], "at": self.clock(),
                               "supersedes_case_revision": row["revision"]})
        return self._save(ctx, row, evidence=evidence, assessment=None, stage="evidence_collected",
                          metrics=metrics, exceptions=exceptions, **self._invalidate(row))

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
            document_results=[row["evidence"]["extracted"]], actor=actor, use_prior=False,
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
        head = self.documents.head(ctx, row["document_id"])
        if head != {key: evidence[key] for key in ("document_id", "version", "modified_at", "mime")}:
            raise ValueError("source evidence version changed")
        vendor = vendors.get_vendor(row["vendor_id"])
        if not vendor or vendor["owner"] != ctx.resource_owner or vendor["status"] == "offboarded":
            raise PermissionError("vendor ownership or lifecycle changed")
        return {"evidence_sha256": evidence["content_sha256"], "source_version": head["version"],
                "assessment_id": assessment["id"], "assessment_revision": assessment["revision"],
                "assessment_sha256": assessment["assessment_sha256"],
                "vendor_revision": vendor["revision"],
                "document_credential_version": self.documents.authority_version(ctx),
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
                f"Evidence: gdrive:{row['document_id']} version {row['evidence']['version']}\n"
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
        operation = row.get("operation")
        if not operation or operation["state"] != "prepared":
            raise ValueError("prepared operation required")
        # Review credentials may differ from execution credentials; source and
        # domain checks always use the bound execution principal.
        execution = ServiceContext(**operation["context"])
        self._permit(execution, "action.execute", case_id)
        if self._preconditions(execution, row) != operation["preconditions"]:
            raise ValueError("operation preconditions changed before approval")
        approval = {"reviewer": ctx.actor, "at": self.clock(),
                    "binding_sha256": operation_binding(operation), "scope": operation["scope"]}
        return self._save(ctx, row, operation=dict(operation, state="approved", approval=approval),
                          stage="action_approved")

    def _finish(self, ctx, row, operation, key, *, reconciled=False):
        confirmation = self.tickets.verify(ctx, key, operation["payload"],
                                           expected_credential_version=operation["preconditions"]["ticket_credential_version"])
        if confirmation.get("verified") is not True:
            raise ValueError("external record does not match the approved payload")
        confirmation = dict(confirmation, confirmed_at=self.clock(), reconciled=reconciled)
        return self._save(ctx, row,
                          operation=dict(operation, state="verified", external_confirmation=confirmation),
                          stage="execution_completed", completed_at=self.clock())

    @_scoped
    def execute(self, ctx, case_id):
        row = self._get(ctx, case_id)
        self._permit(ctx, "action.execute", case_id)
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
        validate_approval(operation, ctx, self.clock())
        approver = ServiceContext(**operation["context"], human=True)
        approver = ServiceContext(**dict(asdict(approver), actor=operation["approval"]["reviewer"]))
        self._permit(approver, "action.approve", case_id, human=True)
        if self._preconditions(ctx, row) != operation["preconditions"]:
            raise ValueError("operation preconditions changed; new review required")
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
        if ctx.binding() != operation["context"]:
            raise PermissionError("reconciliation execution authority changed")
        if digest(operation["payload"]) != operation["payload_sha256"]:
            raise ValueError("operation payload digest mismatch")
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
        if type(accepted) is not bool or row.get("outcome") is not None:
            raise ValueError("one explicit boolean acceptance decision is required")
        why = _text(rationale, "rationale")
        operation = row.get("operation") or {}
        if operation.get("state") != "verified":
            raise ValueError("verified external effect required before customer acceptance")
        execution = ServiceContext(**operation["context"])
        self._permit(execution, "action.reconcile", case_id)
        observed = self.tickets.verify(execution, operation["external_confirmation"]["key"],
                                       operation["payload"],
                                       expected_credential_version=operation["preconditions"]["ticket_credential_version"])
        if observed.get("verified") is not True:
            raise ValueError("external state changed before acceptance")
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
