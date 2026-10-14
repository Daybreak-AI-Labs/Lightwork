"""Provider-free answers from the authorized work-value dashboard service.

Live records never enter Copilot model history. Entity names are resolved only
against the current authorized projection, not model-selected identifiers.
"""
from __future__ import annotations

import hmac
import math
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException

from .draft_api import context_for

_METRIC = re.compile(
    r"\b(?:roi|return on investment|savings|net benefit|gross (?:labor )?value|"
    r"labor value|execution costs?|work value|opportunit(?:y|ies))\b", re.I)
_ACTION = re.compile(
    r"\b(?:change|set|save|correct|update|delete|remove|start|run|approve|confirm|enable|disable)\b", re.I)
_PERIOD = re.compile(r"\b(?:last|past)\s+(\d{1,4})\s+days?\b", re.I)
_ENTITY = re.compile(r"\b(specialist|agent|process|workflow)\s+(?:called\s+|named\s+)?[\"“]?(.+?)[\"”]?$", re.I)
_GOAL = re.compile(r"^/(?:chat/goal|goals|tasks)/(\d{1,18})/?$")
_FLOW = re.compile(r"^/flows/(?:designer/)?([A-Za-z0-9_-]{1,120})(?:/runs/[0-9a-f]{32})?/?$")
_GROUPS = {"specialist": "specialists", "agent": "agents", "process": "processes"}


def intent(message: str) -> bool:
    """A compound request to mutate something must still enter reviewed controls."""
    return bool(isinstance(message, str) and len(message) <= 2000
                and _METRIC.search(message) and not _ACTION.search(message))


def _normalized(value: str) -> str:
    return " ".join(value.casefold().strip().strip('\"“”?.! ').split())


def _selection(value: dict | None) -> dict:
    if value is None:
        return {}
    if (not isinstance(value, dict) or set(value) - {"kind", "scope", "scope_id", "task_id", "days"}
            or value.get("kind") != "intent_value"
            or value.get("scope", "platform") not in {"platform", *_GROUPS}
            or any(not isinstance(value[key], str) or len(value[key]) > 200
                   for key in ("scope_id", "task_id") if key in value and value[key] is not None)
            or "days" in value and (type(value["days"]) is not int or not 1 <= value["days"] <= 730)):
        raise HTTPException(422, "Choose a current metric on the Work value page.")
    return value


def _reply(text: str, *, href="/intent-value", details=None) -> dict:
    return {"reply": text, "mode": "live", "source": "Work intent and value",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "action_link": {"href": href, "label": "Open work value and evidence"},
            "metrics": details or {}}


def _money(value, currency: str) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return f"{currency} {value:,.2f}"
    return "unknown"


def _summary(totals: dict, currency: str) -> str:
    return (f"Estimated gross labor value: {_money(totals.get('gross_labor_value'), currency)}. "
            f"AI/execution cost: {_money(totals.get('execution_cost'), currency)}. "
            f"Estimated net benefit: {_money(totals.get('net_benefit'), currency)}. "
            f"Remaining opportunity: {_money(totals.get('opportunity_value'), currency)}. ")


def _query(message: str, selected: dict, page: str, selected_goal_id: int | None):
    """Explicit wording wins over selected work; unresolved names never broaden scope."""
    period = _PERIOD.search(message)
    days = int(period.group(1)) if period else selected.get("days", 30)
    if not 1 <= days <= 730:
        raise HTTPException(422, "Choose a period from 1 to 730 days.")
    text = _PERIOD.sub("", message).strip().rstrip("?.! ")
    # Unsupported calendar wording must not silently become a last-30-days answer.
    if re.search(r"\b(?:this|last|previous) (?:week|month|quarter|year)\b|\b(?:since|between)\b", text, re.I):
        return days, "period", None, None, False
    explicit = re.search(r"\b(?:task|goal)\s*#?(\d{1,18})\b", text, re.I)
    if explicit:
        return days, "platform", None, f"goal:{int(explicit.group(1))}", False
    match = _ENTITY.search(text)
    if match:
        kind, name = match.groups()
        kind = "process" if kind.casefold() == "workflow" else kind.casefold()
        name = re.sub(r"\s+please$", "", name, flags=re.I)
        if _normalized(name) not in {"this", "that", "selected", "particular"}:
            return days, kind, name.strip('\"“” '), None, True
    if re.search(r"\b(?:platform|whole workspace|company.wide|customer.wide|all lightwork)\b", text, re.I):
        return days, "platform", None, None, False
    if selected.get("task_id"):
        return days, "platform", None, selected["task_id"], False
    if selected.get("scope") in _GROUPS and selected.get("scope_id"):
        return days, selected["scope"], selected["scope_id"], None, False
    task = _GOAL.fullmatch(urlsplit(page).path)
    if selected_goal_id or task:
        identifier = selected_goal_id or int(task.group(1))
        return days, "platform", None, f"goal:{identifier}", False
    flow = _FLOW.fullmatch(urlsplit(page).path)
    if flow and flow.group(1) not in {"designer", "analytics"}:
        return days, "process", flow.group(1), None, False
    if re.search(r"\b(?:specialist|agent|process|workflow)\b", text, re.I):
        return days, "choose", None, None, False
    return days, "platform", None, None, False


def answer(request, message: str, page: str = "", selected_goal_id: int | None = None,
           selection: dict | None = None) -> dict | None:
    if not intent(message):
        return None
    from . import intent_value_api as native

    before = context_for(request)
    days, kind, identifier, task_id, named = _query(message, _selection(selection), page, selected_goal_id)
    until = time.time()
    period = {"since": until - days * 86400, "until": until}
    try:
        if task_id:
            value = _detail_answer(native.detail_for(request, task_id, **period), days, task_id)
        else:
            report = native.report_for(request, **period)
            if kind == "period":
                value = _reply("Choose a period on Work value or ask for the last number of days, such as the last 30 days. I have not substituted a different period.")
            elif kind == "choose":
                value = _reply("Choose a specialist, agent or process in Work value, or include its exact name in your question. I need one current scope before calculating its benefit.")
            elif kind in _GROUPS:
                choices = report.get("groups", {}).get(_GROUPS[kind], [])
                matches = [row for row in choices if _normalized(str(row.get("id", ""))) == _normalized(identifier or "")
                           or named and _normalized(str(row.get("label", ""))) == _normalized(identifier or "")]
                if len(matches) != 1:
                    value = _reply("I could not identify one authorized " + kind + " with that name. Choose it in Work value or use its exact name; I have not guessed a total.")
                else:
                    row = matches[0]
                    report = native.report_for(request, scope_kind=kind, scope_id=row["id"], **period)
                    value = _report_answer(report, str(row.get("label") or kind)[:200], days, kind, row["id"])
            else:
                value = _report_answer(report, "the current authorized platform scope", days, "platform", None)
    except HTTPException:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        value = _reply("I could not confirm current work-value records. Open Work value and refresh its sources before interpreting a benefit; missing evidence is not zero activity.")
    if not hmac.compare_digest(before, context_for(request)):
        raise HTTPException(409, "Your account, workspace or access changed. Reopen Copilot before using this answer.")
    return value


def _report_answer(report: dict, label: str, days: int, kind: str, identifier: str | None) -> dict:
    totals = report["totals"]
    currency = report.get("currency", "USD")
    reply = f"For {label}, over the last {days} days: " + _summary(totals, currency)
    reply += (f"Tasks with audit-backed execution: {totals.get('executed_count', 'unknown')}; "
              f"completed tasks: {totals.get('completed_count', 'unknown')}. "
              "Audits establish performed work; labor value uses editable benchmarks and is not proven cash savings.")
    if totals.get("costs_complete") is not True:
        reply += " Some costs are unknown, so net benefit is unknown."
    coverage = report.get("coverage")
    if coverage and (isinstance(coverage, dict) and coverage.get("complete") is False
                     or isinstance(coverage, str) and coverage != "complete"):
        reply += " Source coverage is incomplete; these are not complete period totals."
    query = {"scope_kind": kind, "days": days}
    if identifier:
        query["scope_id"] = identifier
    return _reply(reply, href="/intent-value?" + urlencode(query),
                  details={"totals": totals, "currency": currency, "scope_kind": kind,
                           "scope_id": identifier, "days": days, "revision": report.get("revision"),
                           "coverage": coverage, "notes": report.get("notes", [])})


def _detail_answer(detail: dict, days: int, task_id: str) -> dict:
    task = detail["task"]
    label = str(task.get("title") or "the selected work")[:200]
    values = task.get("values", task)
    reply = f"For {label}, over the last {days} days: " + _summary(values, detail.get("currency", "USD"))
    reply += (f"Intent: {task.get('intent_label', 'Uncertain')}; state: {task.get('state', 'unknown')}. "
              "The explanation includes its contributing work, benchmark, attribution, costs and retained corrections. "
              "Audit evidence establishes performed work; benchmark value remains an estimate, not proven cash savings.")
    return _reply(reply, href="/intent-value?" + urlencode({"task_id": task_id, "days": days}),
                  details={"task_id": task_id, "values": values, "revision": detail.get("revision"),
                           "benchmark": task.get("benchmark"), "attribution": task.get("attribution"),
                           "evidence": task.get("evidence"), "costs": task.get("costs"),
                           "history": task.get("history")})
