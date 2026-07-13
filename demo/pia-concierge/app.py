"""PIA Concierge — external-world harness for the Lightwork PIA demo.

THE PLATFORM IS THE DEMO. This process serves only what exists OUTSIDE
Lightwork in a real deployment:

  * a simulated ServiceNow tenant (any ticketing system) that fires a webhook,
  * the requester's mailbox (real SMTP delivery, captured locally),
  * the requester-facing intake questionnaire the emailed link opens,
  * a simulated OneTrust tenant that the REAL onetrust integration files into.

Everything platform-side is written into the real Lightwork world so the REAL
dashboard (`maverick dashboard`, port 8765) is where the demo happens:

  * the intake becomes a real goal — watch it on /goals and its live timeline,
  * scoring uses the real maverick.assessment PIA engine and is saved where
    `maverick assess list` and the /assessments register read,
  * control mapping uses the real maverick.controls catalog (GDPR/ISO/SOC 2/
    NIST citations),
  * the human decision is a real world-model approval — approved on the REAL
    /approvals page, quorum and identity recorded,
  * every step lands in the real Ed25519-signed audit chain — /audit page,
    `maverick audit verify`.

Both processes share MAVERICK_HOME (world.db + audit chain + keys).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from mailsink import INBOX, start_mailsink

# Real platform modules ------------------------------------------------------
from maverick.assessment import AssessmentSession, get_template, save_session
from maverick.audit import record
from maverick.controls import find_controls
from maverick.tools.email_tool import email_tool
from maverick.tools.onetrust_tool import onetrust_tool
from maverick.world_model import DEFAULT_DB, WorldModel
from store import STORE, Case, OneTrustAssessment, Ticket

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

BASE_URL = os.environ.get("PIA_BASE_URL", "http://127.0.0.1:8890")
DASHBOARD_URL = os.environ.get("LIGHTWORK_DASHBOARD_URL", "http://127.0.0.1:8765")
AGENT = "pia-concierge"
ANALYST = "privacy-analyst-agent"

app = FastAPI(title="PIA Concierge — external world (ServiceNow / mail / OneTrust)")


def _world() -> WorldModel:
    return WorldModel(DEFAULT_DB)


def _audit(kind: str, agent: str, goal_id: int | None, **payload: Any) -> None:
    """Write to the real signed audit chain; never abort a request on audit error."""
    try:
        record(kind, agent=agent, goal_id=goal_id, **payload)
    except Exception as exc:  # pragma: no cover - demo resilience
        print(f"[audit] {kind} failed: {exc}")


def _post_event(goal_id: int, agent: str, kind: str, content: str) -> None:
    """Mirror a step onto the goal's REAL timeline (dashboard /chat/goal/{id})."""
    try:
        _world().append_event(goal_id, agent, kind, content)
    except Exception as exc:  # pragma: no cover
        print(f"[goal-event] failed: {exc}")


async def _send_email(to: str, subject: str, body: str) -> str:
    """Send via the real email tool, off the event loop (the SMTP capture sink
    shares this process's loop; a blocking send from a handler would deadlock)."""
    tool = email_tool()
    return await asyncio.to_thread(tool.fn, {
        "op": "send", "to": to or "requester@example.com",
        "subject": subject, "body": body,
    })


# Plain-language phrasing for the intake interview; answers map onto the real
# PIA template's yes/no/na/unknown vocabulary.
_INTAKE_PHRASING = {
    "pia_necessity": "Is every field of personal data you're collecting actually needed for the purpose? (No = collecting more than needed)",
    "pia_lawful_basis": "Have you documented a lawful basis for this processing under GDPR Art. 6?",
    "pia_special_category": "Will this process special-category data (health, biometrics, race, etc.)?",
    "pia_transparency": "Are data subjects told about this processing via a privacy notice (Art. 13/14)?",
    "pia_rights": "Can data subjects exercise access / erasure / portability rights against this system?",
    "pia_retention": "Is there a defined retention period after which the data is deleted?",
    "pia_security": "Is the personal data encrypted in transit and at rest?",
    "pia_transfers": "Will personal data leave the EU/EEA without a Chapter V transfer safeguard?",
    "pia_processors": "Is every third-party processor covered by a signed Art. 28 data-processing agreement?",
    "pia_automated": "Does the system make automated decisions or profile people with legal/significant effects?",
}

# Realistic presenter auto-fill: yields a HIGH-risk result (over-collection,
# unsafeguarded transfer, no retention, missing DPA, profiling).
_DEMO_ANSWERS = {
    "pia_necessity": ("no", "Marketing wants full contact + browsing history; only email+name needed."),
    "pia_lawful_basis": ("yes", "Legitimate-interest assessment on file."),
    "pia_special_category": ("no", "No special-category data."),
    "pia_transparency": ("yes", "Covered by the customer privacy notice."),
    "pia_rights": ("yes", "DSAR workflow already wired to this store."),
    "pia_retention": ("no", "No retention period defined by the vendor yet."),
    "pia_security": ("yes", "TLS in transit; vendor claims AES-256 at rest."),
    "pia_transfers": ("yes", "Vendor hosts in us-east-1; no SCCs / transfer safeguard in place."),
    "pia_processors": ("no", "Acme CRM is a processor; Art. 28 DPA not yet countersigned."),
    "pia_automated": ("yes", "Large-scale lead scoring / profiling of customers."),
}


@app.on_event("startup")
async def _startup() -> None:
    host = os.environ.get("EMAIL_SMTP_HOST", "127.0.0.1")
    port = int(os.environ.get("EMAIL_SMTP_PORT", "1025"))
    try:
        app.state.mailserver = await start_mailsink(host, port)
        print(f"[mailsink] capturing SMTP on {host}:{port}")
    except Exception as exc:  # pragma: no cover
        print(f"[mailsink] could not bind {host}:{port}: {exc}")
    app.state.watcher = asyncio.create_task(_approval_watcher())


def _page(request: Request, template: str, **ctx: Any) -> HTMLResponse:
    base = {
        "request": request,
        "base_url": BASE_URL,
        "dashboard_url": DASHBOARD_URL,
        "n_mail": len(INBOX),
        "n_filed": len(STORE.onetrust),
    }
    base.update(ctx)
    return templates.TemplateResponse(request, template, base)


# ===========================================================================
# Landing
# ===========================================================================
@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return _page(request, "index.html", tickets=list(STORE.tickets.values()),
                 cases=list(STORE.cases.values()))


# ===========================================================================
# Mock ServiceNow — the "any ticketing system" intake surface
# ===========================================================================
@app.get("/servicenow", response_class=HTMLResponse)
async def servicenow(request: Request) -> HTMLResponse:
    tickets = sorted(STORE.tickets.values(), key=lambda t: t.created_at, reverse=True)
    return _page(request, "servicenow.html", tickets=tickets)


@app.post("/servicenow/create")
async def servicenow_create(
    request: Request,
    short_description: str = Form(...),
    requester: str = Form(...),
    requester_email: str = Form(...),
    system_name: str = Form(...),
    data_types: str = Form(...),
) -> RedirectResponse:
    number = STORE.next_ticket_number()
    ticket = Ticket(
        number=number,
        short_description=short_description,
        requester=requester,
        requester_email=requester_email,
        system_name=system_name,
        data_types=data_types,
    )
    STORE.tickets[number] = ticket
    ticket.note("Ticket created by requester.", by=requester)

    # A ServiceNow Business Rule would POST to the platform webhook. We make a
    # real HTTP hop so the decoupling is genuine, not simulated in-process.
    payload = {
        "ticket_number": number,
        "short_description": short_description,
        "requester": requester,
        "requester_email": requester_email,
        "system_name": system_name,
        "data_types": data_types,
        "source": "servicenow",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{BASE_URL}/webhook/pia", json=payload)
    except Exception as exc:  # pragma: no cover
        ticket.note(f"Webhook delivery failed: {exc}", by="ServiceNow")

    return RedirectResponse(f"/servicenow/ticket/{number}", status_code=303)


@app.get("/servicenow/ticket/{number}", response_class=HTMLResponse)
async def servicenow_ticket(request: Request, number: str) -> HTMLResponse:
    ticket = STORE.tickets.get(number)
    if not ticket:
        return _page(request, "notfound.html", what=f"ticket {number}")
    case = STORE.cases.get(ticket.case_id) if ticket.case_id else None
    return _page(request, "ticket.html", ticket=ticket, case=case)


# ===========================================================================
# Intake webhook — "submitted via anything" lands here, becomes a REAL goal
# ===========================================================================
@app.post("/webhook/pia")
async def webhook_pia(request: Request) -> JSONResponse:
    body = await request.json()
    number = body.get("ticket_number", "")
    ticket = STORE.tickets.get(number)
    if not ticket:
        # Intake from a system we haven't seen (email, Jira, curl...).
        number = number or STORE.next_ticket_number()
        ticket = Ticket(
            number=number,
            short_description=body.get("short_description", "Privacy assessment request"),
            requester=body.get("requester", "Unknown"),
            requester_email=body.get("requester_email", ""),
            system_name=body.get("system_name", "Unknown system"),
            data_types=body.get("data_types", ""),
        )
        STORE.tickets[number] = ticket

    # REAL goal in the shared world — appears on the dashboard /goals page.
    def _mk_goal() -> int:
        w = _world()
        gid = w.create_goal(
            f"Privacy assessment: {ticket.system_name} ({number})",
            description=(f"{ticket.short_description} — requested by {ticket.requester} "
                         f"via {body.get('source', 'webhook')}. Data: {ticket.data_types}"),
            owner=AGENT, domain="itgrc_dpia",
        )
        w.set_goal_status(gid, "running")
        return gid

    gid = await asyncio.to_thread(_mk_goal)

    case_id = STORE.next_case_id()
    case = Case(
        id=case_id,
        ticket_number=number,
        subject=ticket.system_name,
        requester=ticket.requester,
        requester_email=ticket.requester_email,
        data_types=ticket.data_types,
    )
    case.goal_id = gid  # type: ignore[attr-defined]
    STORE.cases[case_id] = case
    ticket.case_id = case_id

    _audit("GOAL_START", AGENT, gid, case=case_id, ticket=number,
           subject=ticket.system_name, source=body.get("source", "webhook"))
    _post_event(gid, AGENT, "plan",
                f"PIA request {number} received from {body.get('source', 'webhook')}. "
                f"Plan: interview requester → score risk → map controls → human review → file to OneTrust.")
    case.log(f"Intake received from {body.get('source', 'webhook')} ticket {number}.")

    ticket.state = "In Progress"
    ticket.note(
        f"Privacy assessment {case_id} opened in Lightwork (goal #{gid}). Requester "
        f"emailed an intake link; awaiting questionnaire completion.")

    intake_link = f"{BASE_URL}/intake/{case_id}"
    result = await _send_email(
        ticket.requester_email,
        f"[Action needed] Privacy assessment for {ticket.system_name} ({case_id})",
        (
            f"Hi {ticket.requester},\n\n"
            f"Thanks for requesting a privacy assessment for {ticket.system_name}.\n\n"
            f"Please complete the short intake questionnaire and attach any vendor "
            f"documents (DPA, security overview) here:\n\n    {intake_link}\n\n"
            f"It takes about two minutes. Once you submit, our privacy team reviews "
            f"the first-pass risk assessment and files it in OneTrust.\n\n"
            f"— Privacy Office (automated)\n"
        ),
    )
    _audit("TOOL_CALL", ANALYST, gid, tool="email", to=ticket.requester_email,
           case=case_id, outcome=result[:80])
    _post_event(gid, ANALYST, "observation",
                f"Intake interview link emailed to {ticket.requester_email}. Waiting on requester.")
    case.log(f"Intake email sent to {ticket.requester_email}.")

    return JSONResponse({"ok": True, "case_id": case_id, "goal_id": gid,
                         "intake": intake_link})


# ===========================================================================
# Mail inbox (the requester's mailbox, on screen)
# ===========================================================================
@app.get("/mail", response_class=HTMLResponse)
async def mail(request: Request) -> HTMLResponse:
    return _page(request, "mail.html", inbox=INBOX)


# ===========================================================================
# Intake questionnaire — requester-facing (the emailed link)
# ===========================================================================
@app.get("/intake/{case_id}", response_class=HTMLResponse)
async def intake(request: Request, case_id: str) -> HTMLResponse:
    case = STORE.cases.get(case_id)
    if not case:
        return _page(request, "notfound.html", what=f"case {case_id}")
    tpl = get_template("pia")
    questions = [{
        "id": q.id,
        "section": q.section,
        "severity": q.severity,
        "prompt": _INTAKE_PHRASING.get(q.id, q.text),
    } for q in tpl.questions]
    return _page(request, "intake.html", case=case, questions=questions,
                 done=case.stage != "intake_sent")


@app.post("/intake/{case_id}/answer")
async def intake_answer(
    case_id: str,
    question_id: str = Form(...),
    answer: str = Form(...),
    note: str = Form(""),
) -> JSONResponse:
    case = STORE.cases.get(case_id)
    if not case:
        return JSONResponse({"ok": False, "error": "unknown case"}, status_code=404)
    case.answers[question_id] = {"answer": answer.strip().lower(), "note": note.strip()}
    return JSONResponse({"ok": True, "answered": len(case.answers)})


@app.post("/intake/{case_id}/autofill")
async def intake_autofill(case_id: str) -> JSONResponse:
    """Presenter convenience: fill realistic demo answers."""
    case = STORE.cases.get(case_id)
    if not case:
        return JSONResponse({"ok": False}, status_code=404)
    for qid, (ans, note) in _DEMO_ANSWERS.items():
        case.answers[qid] = {"answer": ans, "note": note}
    return JSONResponse({"ok": True, "answered": len(case.answers)})


@app.post("/intake/{case_id}/upload")
async def intake_upload(case_id: str, file: UploadFile) -> JSONResponse:
    case = STORE.cases.get(case_id)
    if not case:
        return JSONResponse({"ok": False}, status_code=404)
    data = await file.read()
    case.uploads.append(f"{file.filename} ({len(data)} bytes)")
    gid = getattr(case, "goal_id", None)
    _audit("EVIDENCE_CAPTURE", ANALYST, gid, case=case_id, artifact=file.filename,
           bytes=len(data))
    if gid:
        _post_event(gid, ANALYST, "artifact",
                    f"Requester attached evidence: {file.filename} ({len(data)} bytes)")
    return JSONResponse({"ok": True, "uploads": case.uploads})


@app.post("/intake/{case_id}/submit")
async def intake_submit(request: Request, case_id: str) -> RedirectResponse:
    case = STORE.cases.get(case_id)
    if not case:
        return RedirectResponse("/", status_code=303)
    gid = getattr(case, "goal_id", None)

    def _score_and_park() -> tuple[dict, list[dict], int]:
        # REAL scoring engine.
        session = AssessmentSession(type="pia", subject=case.subject)
        for qid, rec in case.answers.items():
            try:
                session.record(qid, rec.get("answer", ""), rec.get("note", ""))
            except Exception:
                continue
        result = session.evaluate()
        # Persist where `maverick assess list` reads.
        try:
            save_session(session)
        except Exception as exc:
            print(f"[assess] save_session failed: {exc}")

        # REAL control mapping with framework citations.
        controls: list[dict] = []
        seen: set[str] = set()
        for f in result.findings:
            for ctrl in find_controls(f"{f.section} {f.question}", limit=2):
                if ctrl.id in seen:
                    continue
                seen.add(ctrl.id)
                controls.append({
                    "id": ctrl.id, "title": ctrl.title, "theme": ctrl.theme,
                    "frameworks": "; ".join(ctrl.frameworks),
                    "for_finding": f.question_id,
                })

        result_dict = {
            "type": result.type, "subject": result.subject,
            "risk_rating": result.risk_rating,
            "answered": result.answered, "total": result.total,
            "findings": [{
                "id": f.question_id, "section": f.section, "text": f.question,
                "severity": f.severity, "answer": f.answer, "kind": f.kind,
                "guidance": f.recommendation,
            } for f in result.findings],
        }

        # REAL approval row -> the dashboard's /approvals queue.
        risk = result.risk_rating if result.risk_rating in ("high", "medium", "low") else "medium"
        findings_txt = "; ".join(
            f"[{f.severity}] {f.section}: {f.question}" for f in result.findings) or "no findings"
        controls_txt = "; ".join(f"{c['id']} {c['title']}" for c in controls) or "none"
        w = _world()
        approval_id = w.create_approval(
            f"File PIA for {case.subject} to OneTrust (risk: {result.risk_rating.upper()})",
            risk=risk,
            scope=f"onetrust:/api/assessment/v2/assessments · ticket {case.ticket_number}",
            detail=(f"First-pass assessment {case.id} — {result.answered}/{result.total} answered, "
                    f"{len(result.findings)} findings. Findings: {findings_txt}. "
                    f"Required controls: {controls_txt}. Uploads: {', '.join(case.uploads) or 'none'}."),
            provenance="goal",
            requested_by=AGENT,
        )
        return result_dict, controls, approval_id

    result_dict, controls, approval_id = await asyncio.to_thread(_score_and_park)

    case.result = result_dict
    case.controls = controls
    case.approval_id = str(approval_id)
    case.stage = "pending_review"

    _audit("ASSESSMENT_REVIEW", ANALYST, gid, case=case_id, subject=case.subject,
           risk=result_dict["risk_rating"], findings=len(result_dict["findings"]),
           controls=len(controls), status="pending_human_review")
    _audit("AUTONOMY_GATED", AGENT, gid, case=case_id, approval_id=approval_id,
           reason="assessment_requires_human_approval_before_filing")
    if gid:
        _post_event(gid, ANALYST, "finding",
                    f"First-pass assessment: risk {result_dict['risk_rating'].upper()}, "
                    f"{len(result_dict['findings'])} findings, {len(controls)} required controls "
                    f"(GDPR/ISO 27001/SOC 2/NIST citations attached).")
        _post_event(gid, AGENT, "verify",
                    f"Autonomy gated: approval #{approval_id} created — a human must approve "
                    f"before anything is filed to OneTrust. See the dashboard Approvals queue.")
    case.log(f"Scored risk={result_dict['risk_rating']}; approval #{approval_id} "
             f"awaiting human decision in the dashboard.")

    ticket = STORE.tickets.get(case.ticket_number)
    if ticket:
        ticket.note(f"First-pass assessment complete — risk "
                    f"{result_dict['risk_rating'].upper()}, {len(result_dict['findings'])} findings. "
                    f"Pending privacy-team approval (approval #{approval_id}).")

    return RedirectResponse(f"/intake/{case_id}?submitted=1", status_code=303)


# ===========================================================================
# Approval watcher — reacts to the human decision made in the REAL dashboard
# ===========================================================================
async def _approval_watcher() -> None:
    while True:
        try:
            pending = [c for c in STORE.cases.values() if c.stage == "pending_review"]
            for case in pending:
                status, decided_by = await asyncio.to_thread(_approval_state, case)
                if status == "approved":
                    await _file_to_onetrust(case, decided_by)
                elif status in ("denied", "rejected"):
                    await _handle_rejection(case, decided_by)
        except Exception as exc:  # pragma: no cover
            print(f"[watcher] {exc}")
        await asyncio.sleep(2)


def _approval_state(case: Case) -> tuple[str, str]:
    try:
        a = _world().get_approval(int(case.approval_id))
        if a is None:
            return "missing", ""
        return a.status, a.decided_by or ""
    except Exception:
        return "error", ""


async def _file_to_onetrust(case: Case, decided_by: str) -> None:
    gid = getattr(case, "goal_id", None)
    case.stage = "approved"
    case.decided_by = decided_by or "dashboard-reviewer"
    case.log(f"Approved by {case.decided_by} in the Lightwork dashboard.")
    _audit("APPROVAL_DECISION", case.decided_by, gid, case=case.id,
           decision="approved", approval_id=case.approval_id,
           risk=(case.result or {}).get("risk_rating"))
    if gid:
        _post_event(gid, AGENT, "observation",
                    f"Approval #{case.approval_id} granted by {case.decided_by}. Filing to OneTrust.")

    # REAL integration code -> mock tenant.
    ot = onetrust_tool()
    body = {
        "name": f"PIA — {case.subject}",
        "templateName": "Privacy Impact Assessment",
        "subject": case.subject,
        "riskLevel": (case.result or {}).get("risk_rating", "unknown"),
        "result": case.result,
        "controls": case.controls,
        "sourceTicket": case.ticket_number,
    }
    filed = await asyncio.to_thread(ot.fn, {
        "op": "post", "path": "/api/assessment/v2/assessments",
        "body": body, "confirm": True})
    _audit("TOOL_CALL", ANALYST, gid, tool="onetrust", op="post",
           path="/api/assessment/v2/assessments", case=case.id, outcome=filed[:120])

    if filed.startswith("ok"):
        case.stage = "filed"
        case.onetrust_id = _extract_ot_id(filed)
        case.log(f"Filed into OneTrust as {case.onetrust_id}.")
        if gid:
            _post_event(gid, ANALYST, "artifact",
                        f"Assessment filed in OneTrust as {case.onetrust_id} "
                        f"(POST /api/assessment/v2/assessments, confirm-gated).")
            await asyncio.to_thread(
                _world().set_goal_status, gid, "done",
                f"PIA complete: risk {(case.result or {}).get('risk_rating', '?').upper()}, "
                f"filed as {case.onetrust_id}, ticket {case.ticket_number} resolved.")
        ticket = STORE.tickets.get(case.ticket_number)
        if ticket:
            ticket.state = "Resolved"
            ticket.note(
                f"Privacy assessment APPROVED (risk "
                f"{(case.result or {}).get('risk_rating', '?').upper()}) by {case.decided_by} "
                f"and filed in OneTrust as {case.onetrust_id}. "
                f"{len(case.controls)} required controls attached. Closing ticket.")
        await _send_email(
            case.requester_email,
            f"Privacy assessment complete — {case.subject} ({case.id})",
            (
                f"Hi {case.requester},\n\n"
                f"Your privacy assessment for {case.subject} is complete and approved.\n\n"
                f"Risk rating: {(case.result or {}).get('risk_rating', '?').upper()}\n"
                f"Filed in OneTrust: {case.onetrust_id}\n"
                f"Required controls: {len(case.controls)}\n\n"
                f"The privacy team will follow up on any open controls.\n\n"
                f"— Privacy Office\n"
            ),
        )
    else:
        case.log(f"OneTrust filing did not confirm: {filed[:120]}")


async def _handle_rejection(case: Case, decided_by: str) -> None:
    gid = getattr(case, "goal_id", None)
    case.stage = "rejected"
    case.decided_by = decided_by or "dashboard-reviewer"
    case.log(f"Sent back by {case.decided_by}.")
    _audit("APPROVAL_DECISION", case.decided_by, gid, case=case.id,
           decision="denied", approval_id=case.approval_id)
    if gid:
        _post_event(gid, AGENT, "error",
                    f"Approval #{case.approval_id} denied by {case.decided_by}; nothing filed.")
        await asyncio.to_thread(_world().set_goal_status, gid, "blocked",
                                "Assessment sent back by privacy review; not filed.")
    ticket = STORE.tickets.get(case.ticket_number)
    if ticket:
        ticket.state = "In Progress"
        ticket.note(f"Assessment sent back by privacy team ({case.decided_by}).")


def _extract_ot_id(msg: str) -> str:
    """Pull the assessment id out of the onetrust tool's 'ok (201): {json}' reply."""
    try:
        payload = json.loads(msg[msg.index("{"):])
        return payload.get("assessmentId", payload.get("id", "OT-ASMT-?"))
    except Exception:
        return "OT-ASMT-?"


# ===========================================================================
# Mock OneTrust — tenant API + viewer
# ===========================================================================
@app.post("/ot-api/api/assessment/v2/assessments")
async def ot_create_assessment(request: Request) -> JSONResponse:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"error": "missing bearer token"}, status_code=401)
    body = await request.json()
    ot_id = STORE.next_onetrust_id()
    STORE.onetrust[ot_id] = OneTrustAssessment(
        assessment_id=ot_id,
        name=body.get("name", "Assessment"),
        template=body.get("templateName", "PIA"),
        subject=body.get("subject", ""),
        status="Completed",
        risk_level=body.get("riskLevel", "unknown"),
        result=body.get("result") or {},
    )
    return JSONResponse({"assessmentId": ot_id, "status": "Completed",
                         "created": True})


@app.get("/ot-api/api/assessment/v2/assessments")
async def ot_list_assessments(request: Request) -> JSONResponse:
    return JSONResponse({
        "content": [{
            "assessmentId": a.assessment_id, "name": a.name,
            "status": a.status, "riskLevel": a.risk_level, "subject": a.subject,
        } for a in STORE.onetrust.values()],
        "totalElements": len(STORE.onetrust),
    })


@app.get("/onetrust", response_class=HTMLResponse)
async def onetrust_viewer(request: Request) -> HTMLResponse:
    items = sorted(STORE.onetrust.values(), key=lambda a: a.filed_at, reverse=True)
    return _page(request, "onetrust.html", assessments=items)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "cases": len(STORE.cases),
                        "tickets": len(STORE.tickets), "filed": len(STORE.onetrust)})
