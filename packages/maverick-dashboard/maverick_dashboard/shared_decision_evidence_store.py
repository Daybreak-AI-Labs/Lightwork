"""Owner-scoped finite decision reviews, shared budgets and original receipts."""
from __future__ import annotations

import copy
import hmac
import json
import re
import time
from datetime import datetime, timezone

from fastapi import HTTPException
from maverick import config, crypto_at_rest
from maverick.authority_effects import authority_effect_fence
from maverick.governed_operations import digest
from maverick.governed_records import GovernedRecordStore
from maverick.shared_decision_evidence import Solver, check_certificate, validate

from . import auth
from .draft_api import context_for

STORE = GovernedRecordStore("shared-decision-evidence", "SDE", "shared_decision_evidence")
TTL = 600
NOTICE = "This checks the choices and possible situations you entered, using your attributed reports. It does not independently verify business facts, reserve resources, send questions, or carry out a choice. Checking each case separately does not show that shared resources can fulfil all cases; enter combined requirements or use Promise reviews."


def authority(request, context=None, *, historical=False):
    auth.require_permission(request, "operate")
    current = context_for(request)
    if context is not None and not hmac.compare_digest(context, current):
        raise HTTPException(409, "Your account or workspace changed. Reload this review.")
    if not historical and (config.config_source_errors(include_tenant=True) or config.get_features().get("shared_decision_evidence") is not True):
        raise HTTPException(403, "Shared decision reviews are off. An administrator can enable them in Settings.")
    return auth.caller_principal(request) or "local:dashboard", current


def _owned(request, identifier, *, historical=False):
    actor, context = authority(request, historical=historical)
    if not isinstance(identifier, str) or not re.fullmatch(r"SDE-[a-f0-9]{32}", identifier):
        raise HTTPException(404, "This decision review is unavailable.")
    row = STORE.get(identifier)
    if not row or (row.get("actor"), row.get("context")) != (actor, context):
        raise HTTPException(404, "This decision review is unavailable to your account and workspace.")
    if row.get("_audit_pending"):
        row = STORE._flush(row)
    return row


def _open(row):
    material = json.loads(crypto_at_rest.unseal_from_str(row["envelope"]))
    if material["identity"] != [row["id"], row["actor"], row["context"]]:
        raise HTTPException(409, "The saved review's source binding could not be verified.")
    return material["payload"]


def _seal(row, payload):
    raw = json.dumps({"identity": [row["id"], row["actor"], row["context"]], "payload": payload}, allow_nan=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > 400000:
        raise HTTPException(413, "This complete review is too large. Nothing was changed; no details were dropped.")
    row["envelope"] = crypto_at_rest.seal_to_str(raw)
    if not crypto_at_rest.is_sealed_str(row["envelope"]):
        raise HTTPException(409, "Turn on encryption for stored data before retaining a decision review.")


def _source(fact, now):
    return {"definition": digest(fact), "revision": digest([fact, now]), "observed_at": now,
            "expires_at": now + fact["fresh_hours"] * 3600, "available": True, "consent_cases": [],
            "value": None, "quality": "operator_supplied"}


def _solver(payload, now=None):
    return Solver(payload["model"], payload["sources"], time.time() if now is None else now,
                  questions_used=payload["questions_used"], effort_used=payload["effort_used"],
                  pending_facts=(row["fact"] for row in payload["pending"].values() if row["state"] == "awaiting_observation"))


def _evaluation(payload):
    solver = _solver(payload)
    assessment = solver.assessment()
    certificates = assessment["certificates"]
    current_ids = {row.get("id") for row in certificates}
    retained = []
    for old in payload.get("certificate_history", {}).values():
        if old["id"] not in current_ids:
            retained.append({"case": old["case"], "id": old["id"], "still_valid": check_certificate(old, payload["model"], payload["sources"], solver.now),
                             "meaning": "Retained model certificate, not an independent business attestation."})
    return {**assessment, "prior_certificates": retained, "acquisition": solver.acquisition()}


def view(request, identifier):
    row = _owned(request, identifier)
    payload = _open(row)
    references = [{"operation": key, "action": receipt["action"], "recorded_at": receipt["recorded_at"]}
                  for key, receipt in payload["operations"].items()]
    if payload.get("reservation"):
        references.append({"operation": payload["reservation"]["operation"], "action": "unconfirmed create", "recorded_at": row["created_at"]})
    value = {"id": row["id"], "revision": row["revision"], "context": row["context"], "removed": payload["removed"],
        "model": payload["model"], "sources": payload["sources"], "pending": payload["pending"],
        "evaluation": _evaluation(payload) if not payload["removed"] and not payload.get("reservation") else None, "notice": NOTICE,
        "unconfirmed_admission": bool(payload.get("reservation")),
        "audit_pending": bool(row.get("_audit_pending")), "original_requests": references}
    authority(request, row["context"])
    return value


def listing(request):
    actor, context = authority(request)
    values = STORE.list(limit=101)
    if len(values) > 100:
        raise HTTPException(409, "This workspace reached the 100-review finite pilot limit. No saved reviews were silently dropped.")
    return {"context": context, "reviews": [{"id": row["id"], "title": _open(row)["model"]["title"],
        "removed": _open(row)["removed"], "unconfirmed_admission": bool(_open(row).get("reservation"))}
        for row in values if (row.get("actor"), row.get("context")) == (actor, context)]}


def historical_requests(request):
    """Own original references only; current model consent/freshness is not replay authority."""
    actor, context = authority(request, historical=True)
    rows = STORE.list(limit=101)
    if len(rows) > 100:
        raise HTTPException(409, "This workspace exceeds the finite history bound. No requests were silently dropped.")
    choices = []
    for row in rows:
        if (row.get("actor"), row.get("context")) != (actor, context):
            continue
        payload = _open(row)
        references = dict(payload["operations"])
        if payload.get("reservation"):
            references[payload["reservation"]["operation"]] = {"action": "unconfirmed create", "recorded_at": row["created_at"]}
        for operation, receipt in references.items():
            choices.append({"value": row["id"] + "|" + operation,
                "label": payload["model"]["title"] + " · " + receipt["action"] + " · " +
                    datetime.fromtimestamp(receipt["recorded_at"], timezone.utc).isoformat()})
    authority(request, context, historical=True)
    return {"context": context, "choices": choices}


def _check_arguments(action, arguments, payload=None):
    if action in {"create", "edit"}:
        validate(arguments)
        if any(case["due_at"] <= time.time() for case in arguments["cases"]):
            raise HTTPException(422, "Choose future decision deadlines.")
        return
    allowed = {"consent": {"fact", "cases"}, "withdraw": {"fact", "cases"}, "refresh": {"fact"}, "acquire": set(),
               "answer": {"request", "value", "basis"}, "remove": set()}
    if action not in allowed or set(arguments) != allowed[action]:
        raise HTTPException(422, "Choose one complete decision-review operation.")
    if action in {"consent", "withdraw"}:
        if (arguments["fact"] not in payload["sources"] or not isinstance(arguments["cases"], list)
                or not arguments["cases"] or len(set(arguments["cases"])) != len(arguments["cases"])
                or set(arguments["cases"]) - {case["id"] for case in payload["model"]["cases"]}):
            raise HTTPException(422, "Choose this fact and its exact permitted cases.")
    if action == "refresh" and arguments["fact"] not in payload["sources"]:
        raise HTTPException(422, "Choose an exact current source fact.")
    if action == "answer":
        pending = payload["pending"].get(arguments["request"])
        if (not pending or pending["state"] != "awaiting_observation" or type(arguments["value"]) is not int
                or not isinstance(arguments["basis"], str) or not 10 <= len(arguments["basis"].strip()) <= 2000):
            raise HTTPException(422, "Choose an open observation, a possible quantity and the basis for your report.")
        fact = next(item for item in payload["model"]["facts"] if item["id"] == pending["fact"])
        if arguments["value"] not in fact["values"]:
            raise HTTPException(422, "The reported value is outside this model. Edit the complete model instead.")


def prepare(request, action, arguments, identifier=None):
    with authority_effect_fence():
        actor, context = authority(request)
        row = _owned(request, identifier) if identifier else None
        payload = _open(row) if row else None
        if action != "create" and row is None or action == "create" and row is not None:
            raise HTTPException(422, "Choose a saved review or create a new one.")
        if row and (row.get("_audit_pending") or payload["removed"] or payload.get("reservation")):
            raise HTTPException(409, "This review is removed or its original activity receipt is pending. Read its original result.")
        _check_arguments(action, arguments, payload)
        if action == "acquire" and not _solver(payload).acquisition()["selected"]:
            raise HTTPException(409, "No affordable observation can currently resolve a modeled case. The result stays unknown.")
        return {"action": action, "arguments": copy.deepcopy(arguments), "id": identifier, "revision": row["revision"] if row else None,
                "actor": actor, "context": context, "expires": time.time() + TTL,
                "source_digest": digest(payload) if payload else None,
                "source_review": {"model": payload["model"], "sources": payload["sources"],
                    "budget": _solver(payload).assessment()["budget"]} if payload else None,
                "selection": _solver(payload).acquisition()["selected"] if action == "acquire" else None}


def review_changes(prepared):
    rows = [{"label": "Operation", "after": prepared["action"].replace("_", " ")}, {"label": "Meaning", "after": NOTICE}]
    def visit(value, label):
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, label + " · " + key.replace("_", " "))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, label + " · " + str(index + 1))
        else:
            text = datetime.fromtimestamp(value, timezone.utc).isoformat() if label.rsplit(" · ", 1)[-1] in {
                "due at", "expires at", "observed at", "decision before"} and type(value) in {int, float} else str(value)
            rows.append({"label": label, "after": text})
    visit(prepared["arguments"], "Reviewed value")
    if prepared.get("source_review"):
        visit(prepared["source_review"], "Exact current source and authority")
    if prepared["selection"]:
        visit(prepared["selection"], "One selected observation")
    return rows


def _mutate(payload, prepared, operation, now):
    action, values = prepared["action"], prepared["arguments"]
    if action == "edit":
        previous = payload["sources"]
        payload["model"] = copy.deepcopy(values)
        payload["sources"] = {fact["id"]: previous[fact["id"]] if fact["id"] in previous and previous[fact["id"]]["definition"] == digest(fact)
                              else _source(fact, now) for fact in values["facts"]}
        for source in payload["sources"].values():
            source["consent_cases"] = [case for case in source["consent_cases"] if case in {row["id"] for row in values["cases"]}]
        for row in payload["pending"].values():
            if row["state"] == "awaiting_observation":
                row["state"] = "unconfirmed_after_model_change"
    elif action in {"consent", "withdraw"}:
        source = payload["sources"][values["fact"]]
        current = set(source["consent_cases"])
        source["consent_cases"] = sorted(current | set(values["cases"]) if action == "consent" else current - set(values["cases"]))
    elif action == "refresh":
        fact = next(item for item in payload["model"]["facts"] if item["id"] == values["fact"])
        source = _source(fact, now)
        source["consent_cases"] = list(payload["sources"][fact["id"]]["consent_cases"])
        payload["sources"][fact["id"]] = source
        for row in payload["pending"].values():
            if row["fact"] == fact["id"] and row["state"] == "awaiting_observation":
                row["state"] = "unconfirmed_after_source_change"
    elif action == "acquire":
        selected = _solver(payload, now).acquisition()["selected"]
        if not selected or selected != prepared["selection"]:
            raise HTTPException(409, "The observation's usefulness or budget changed. Review it again.")
        fact = selected["fact"]
        payload["questions_used"] += 1
        payload["effort_used"] += selected["cost"]
        payload["pending"][operation] = {"fact": fact, "source_revision": payload["sources"][fact]["revision"],
            "model": digest(payload["model"]), "state": "awaiting_observation", "cost": selected["cost"],
            "consent_cases": list(payload["sources"][fact]["consent_cases"]), "requested_at": now}
    elif action == "answer":
        pending = payload["pending"][values["request"]]
        source = payload["sources"][pending["fact"]]
        if (pending["model"] != digest(payload["model"]) or pending["source_revision"] != source["revision"]
                or not source["observed_at"] <= now < source["expires_at"]
                or pending["consent_cases"] != source["consent_cases"]):
            raise HTTPException(409, "The reviewed source or permission changed or expired. This observation remains unconfirmed; no repeat or budget refund was inferred.")
        source.update(value=values["value"], basis=values["basis"].strip(), revision=digest([source["definition"], operation, values["value"], now]),
                      observed_at=now)
        pending["state"] = "reported_operator_observation"
    elif action == "remove":
        payload["removed"] = True
        for source in payload["sources"].values():
            source["available"] = False
        for row in payload["pending"].values():
            if row["state"] == "awaiting_observation":
                row["state"] = "unconfirmed_after_removal"


def _identifier(context, actor, operation):
    return "SDE-" + digest([context, actor, operation])[:32]


def original(request, identifier, operation, *, binding=None):
    row = _owned(request, identifier, historical=True)
    payload = _open(row)
    receipt = payload["operations"].get(operation)
    reservation = payload.get("reservation")
    if reservation and (reservation["operation"] != operation or binding is not None and reservation["binding"] != binding):
        raise HTTPException(409, "This admission belongs to another exact original review.")
    if receipt and binding is not None and receipt["binding"] != binding:
        raise HTTPException(409, "This operation belongs to another exact review.")
    authority(request, row["context"], historical=True)
    return {"id": identifier, "context": row["context"], "operation": operation, "confirmed": bool(receipt) and not row.get("_audit_pending"),
        "receipt": receipt, "message": "The original modeled change is recorded. No observation was requested or business action repeated." if receipt and not row.get("_audit_pending") else
        "The original result is unconfirmed. Reading does not request an observation, spend budget or repeat a change."}


def recover(request, prepared, operation):
    identifier = prepared["id"] or _identifier(prepared["context"], prepared["actor"], operation)
    try:
        return original(request, identifier, operation, binding=digest(prepared))
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


def apply(request, prepared, operation):
    if not isinstance(operation, str) or not re.fullmatch(r"[a-f0-9]{32}", operation):
        raise HTTPException(422, "The original request reference is missing.")
    with authority_effect_fence():
        actor, context = authority(request, prepared["context"])
        if actor != prepared["actor"]:
            raise HTTPException(403, "Only the exact person who reviewed this model may confirm it.")
        existing = recover(request, prepared, operation)
        if existing and (existing["receipt"] or prepared["action"] == "create"):
            return existing
        def guard():
            authority(request, context)
            if time.time() >= prepared["expires"]:
                raise HTTPException(409, "This exact review expired. Review current sources and budget again.")
        guard()
        identifier = prepared["id"] or _identifier(context, actor, operation)
        def effect(current):
            guard()
            payload = _open(current)
            if current.get("_audit_pending") or digest(payload) != prepared["source_digest"]:
                raise HTTPException(409, "The source model, facts, consent or budget changed. Nothing was repeated.")
            _check_arguments(prepared["action"], prepared["arguments"], payload)
            before = copy.deepcopy(payload)
            _mutate(payload, prepared, operation, time.time())
            _complete(current, payload)
            guard()
            _fresh_effect(before, payload)
        def _complete(row, payload):
            if len(payload["operations"]) >= 200:
                raise HTTPException(409, "This review reached its 200-decision custody bound. No history was dropped.")
            assessment = _solver(payload).assessment() if not payload["removed"] else {"certificates": []}
            history = payload.setdefault("certificate_history", {})
            for prior in payload["certificates"]:
                history[prior["case"]] = prior
            payload["certificates"] = [item for item in assessment["certificates"] if item["status"] == "model_sufficient"]
            payload["operations"][operation] = {"action": prepared["action"], "binding": digest(prepared), "actor": actor, "context": context,
                "recorded_at": time.time(), "model_sha256": digest(payload["model"]), "questions_used": payload["questions_used"],
                "effort_points_used": payload["effort_used"], "certificate_ids": [item["id"] for item in payload["certificates"]], "business_verified": False}
            _seal(row, payload)
        def _fresh_effect(before, after):
            # The encryption and native writer wait must not outlive source authority.
            now = time.time()
            action = prepared["action"]
            if action in {"create", "edit"} and any(case["due_at"] <= now for case in after["model"]["cases"]):
                raise HTTPException(409, "A reviewed decision deadline expired before the change could be recorded.")
            if action == "acquire" and _solver(before, now).acquisition()["selected"] != prepared["selection"]:
                raise HTTPException(409, "The reviewed observation expired or changed before budget reservation. Review it again.")
            if action == "answer":
                pending = before["pending"][prepared["arguments"]["request"]]
                source = before["sources"][pending["fact"]]
                solver = _solver(before, now)
                applicable = [case for case in pending["consent_cases"] if solver.source_current(pending["fact"], case)
                    and any(row["id"] == case and now < row["due_at"] for row in before["model"]["cases"])]
                if not applicable or not source["observed_at"] <= now < source["expires_at"]:
                    raise HTTPException(409, "The reviewed source or decision deadline expired before this report could be recorded. It remains unconfirmed.")
            if any(not check_certificate(item, after["model"], after["sources"], now) for item in after["certificates"]):
                raise HTTPException(409, "A dependent fact expired before certificate custody. Read current sources and review again.")
        if prepared["action"] == "create":
            validate(prepared["arguments"])
            payload = {"model": copy.deepcopy(prepared["arguments"]), "sources": {fact["id"]: _source(fact, time.time()) for fact in prepared["arguments"]["facts"]},
                       "questions_used": 0, "effort_used": 0, "pending": {}, "operations": {}, "certificates": [], "removed": False,
                       "reservation": {"operation": operation, "binding": digest(prepared)}}
            row = {"id": identifier, "actor": actor, "context": context, "schema": "lightwork.shared-decision-evidence.v1"}
            _seal(row, payload)
            guard()
            reserved = STORE.create(row, action="shared_decision_reserved", actor=actor, max_records=100)
            def admit(current):
                guard()
                admitted = _open(current)
                if current.get("_audit_pending") or admitted.get("reservation") != payload["reservation"]:
                    raise HTTPException(409, "The original admission is unconfirmed. Read its original result; no model was repeated.")
                del admitted["reservation"]
                _complete(current, admitted)
                guard()
                _fresh_effect(None, admitted)
            STORE.update(identifier, admit, expected_revision=reserved["revision"], action="shared_decision_created", actor=actor)
        else:
            STORE.update(identifier, effect, expected_revision=prepared["revision"], action="shared_decision_" + prepared["action"], actor=actor)
        return original(request, identifier, operation, binding=digest(prepared))
