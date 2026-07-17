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
import datetime as _dt
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
templates.env.filters["timestamp_hm"] = (
    lambda ts: _dt.datetime.fromtimestamp(float(ts or 0)).strftime("%H:%M"))

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
    # This public demo route explicitly opts into ambient document credentials,
    # so make that ambient state unconditionally mock-only before startup does
    # any async work. Never inherit real Graph credentials into a process that
    # exposes unauthenticated intake endpoints. The launcher binds this server
    # to loopback.
    graph_sim_base = (
        f"http://127.0.0.1:{int(os.environ.get('WORLD_PORT', '8890'))}/graph-sim"
    )
    os.environ["MSGRAPH_ACCESS_TOKEN"] = GRAPH_SIM_TOKEN
    os.environ["MSGRAPH_BASE_URL"] = graph_sim_base
    os.environ.pop("MAVERICK_FETCH_ALLOW_PRIVATE", None)
    for inherited_source_setting in (
        "SLACK_SEARCH_TOKEN",
        "SLACK_BOT_TOKEN",
        "SLACK_BASE_URL",
        "GDRIVE_ACCESS_TOKEN",
        "GDRIVE_BASE_URL",
    ):
        os.environ.pop(inherited_source_setting, None)

    host = os.environ.get("EMAIL_SMTP_HOST", "127.0.0.1")
    port = int(os.environ.get("EMAIL_SMTP_PORT", "1025"))
    try:
        app.state.mailserver = await start_mailsink(host, port)
        print(f"[mailsink] capturing SMTP on {host}:{port}")
    except Exception as exc:  # pragma: no cover
        print(f"[mailsink] could not bind {host}:{port}: {exc}")
    app.state.watcher = asyncio.create_task(_approval_watcher())
    print(f"[graph-sim] simulated SharePoint tenant at {graph_sim_base}")


# ===========================================================================
# Simulated SharePoint/OneDrive tenant (Microsoft Graph shapes). Only the
# TENANT is simulated — search + fetch go through the real
# maverick.doc_discovery module, exactly as they would against graph.microsoft
# .com. Docs are seeded per case so "find my documents" has something to find.
# ===========================================================================
GRAPH_SIM_TOKEN = "demo-graph-token"  # pragma: allowlist secret
GRAPH_DOCS: dict[str, dict[str, Any]] = {}


def _mini_pdf(title: str, lines: list[str]) -> bytes:
    """A tiny, valid one-page PDF (passes the attachment mime + magic checks)."""
    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    body = ["BT /F1 14 Tf 54 760 Td (" + esc(title) + ") Tj ET"]
    y = 730
    for ln in lines:
        body.append("BT /F1 10 Tf 54 " + str(y) + " Td (" + esc(ln) + ") Tj ET")
        y -= 16
    stream = "\n".join(body).encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode()
            + b" /Root 1 0 R >>\nstartxref\n" + str(xref).encode()
            + b"\n%%EOF\n")
    return bytes(out)


def _seed_corp_docs(subject: str) -> None:
    """Drop the documents a real org would already have for this vendor into
    the simulated tenant: the SOW, MSA, DPA, and security overview — plus an
    unrelated doc so ranking visibly works."""
    docs = [
        (f"{subject} — Statement of Work (SOW) FY26.pdf",
         [f"Statement of Work between the Company and {subject}.",
          "Scope: managed service, personal data of customers in scope.",
          "Term: 12 months. Data locations: us-east-1."]),
        (f"{subject} — Data Processing Agreement (DPA, signed).pdf",
         [f"Art. 28 GDPR data processing agreement with {subject}.",
          "Processor obligations, sub-processor list, SCC module 2 annex."]),
        (f"{subject} — Master Services Agreement (MSA).pdf",
         [f"Master services agreement governing all {subject} orders.",
          "Liability caps, security exhibit, audit rights."]),
        (f"{subject} — Security Overview & Architecture.pdf",
         [f"{subject} platform security whitepaper.",
          "Encryption at rest (AES-256), TLS 1.2+, SOC 2 Type II attested."]),
        ("Company holiday calendar FY26.pdf",
         ["All-hands calendar. Nothing to do with any vendor."]),
    ]
    for name, lines in docs:
        doc_id = f"doc-{abs(hash((subject, name))) % 10**10:010d}"
        if doc_id not in GRAPH_DOCS:
            GRAPH_DOCS[doc_id] = {
                "name": name, "subject": subject,
                "bytes": _mini_pdf(name, lines), "mime": "application/pdf",
            }


def _graph_auth_ok(request: Request) -> bool:
    tok = request.headers.get("authorization", "")
    return tok == f"Bearer {os.environ.get('MSGRAPH_ACCESS_TOKEN', GRAPH_SIM_TOKEN)}"


def _discover_mock_documents(subject: str):
    """Run real discovery with a task-local allowance for the mock loopback."""
    from maverick import doc_discovery
    from maverick.tools._ssrf import allow_private_hosts

    with allow_private_hosts({"127.0.0.1"}):
        return doc_discovery.discover(
            subject,
            sources=["msgraph"],
            allow_ambient_credentials=True,
        )


def _fetch_mock_document(source: str, doc_id: str, ref: dict):
    """Fetch only from the fixed mock base under the same task-local policy."""
    from maverick import doc_discovery
    from maverick.tools._ssrf import allow_private_hosts

    with allow_private_hosts({"127.0.0.1"}):
        return doc_discovery.fetch(
            source,
            doc_id,
            ref,
            allow_ambient_credentials=True,
        )


@app.post("/graph-sim/search/query")
async def graph_sim_search(request: Request) -> JSONResponse:
    if not _graph_auth_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    try:
        q = str(body["requests"][0]["query"]["queryString"])
    except (KeyError, IndexError, TypeError):
        return JSONResponse({"error": "bad request"}, status_code=400)
    tokens = {t.lower() for t in q.split() if len(t) > 2}
    hits = []
    for doc_id, doc in GRAPH_DOCS.items():
        name = doc["name"].lower()
        subj = doc["subject"].lower()
        if any(t in name or t in subj for t in tokens):
            hits.append({
                "summary": f"…{doc['name']}…",
                "resource": {
                    "id": doc_id, "name": doc["name"],
                    "webUrl": f"{BASE_URL}/graph-sim/web/{doc_id}",
                    "size": len(doc["bytes"]),
                    "file": {"mimeType": doc["mime"]},
                    "parentReference": {"driveId": "demo-drive"},
                },
            })
    return JSONResponse({"value": [{"hitsContainers": [{"hits": hits}]}]})


@app.get("/graph-sim/drives/{drive_id}/items/{item_id}/content")
async def graph_sim_content(request: Request, drive_id: str, item_id: str):
    if not _graph_auth_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    doc = GRAPH_DOCS.get(item_id)
    if not doc:
        return JSONResponse({"error": "not found"}, status_code=404)
    from fastapi.responses import Response
    return Response(content=doc["bytes"], media_type=doc["mime"])


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
    # The org "already has" this vendor's paperwork in the simulated tenant.
    _seed_corp_docs(ticket.system_name)

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
    # Newest-first payload for the reading-pane pop-out; the intake link is
    # pulled out of the body so the pane can render it as a proper button.
    mail_json = []
    for m in reversed(INBOX):
        link = next((t for t in m.body.split() if t.startswith("http")), "")
        mail_json.append({
            "to": m.to, "subject": m.subject, "body": m.body,
            "when": _dt.datetime.fromtimestamp(m.at).strftime("%H:%M"),
            "link": link,
        })
    return _page(request, "mail.html", inbox=INBOX, mail_json=mail_json)


# ===========================================================================
# Intake questionnaire — requester-facing (the emailed link)
# ===========================================================================
@app.get("/intake/{case_id}", response_class=HTMLResponse)
async def intake(request: Request, case_id: str, d: str = "") -> HTMLResponse:
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

    # Delegate mode: an emailed ?d=<token> link scopes the interview to the
    # questions a colleague was asked to answer.
    delegation = None
    if d:
        rec = (getattr(case, "delegations", None) or {}).get(d)
        if rec:
            qids = set(rec["qids"])
            questions = [q for q in questions if q["id"] in qids]
            delegation = {"token": d, "to_name": rec["to_name"],
                          "note": rec.get("note", ""),
                          "from_name": case.requester}

    # Follow-up mode: the privacy team sent questions back; the requester
    # answers ONLY those (free text), then the case returns to review. A
    # delegate link stays scoped to its delegated questions even mid-followup.
    followups = (_open_followups(case)
                 if case.stage == "followup" and delegation is None else [])

    return _page(request, "intake.html", case=case, questions=questions,
                 delegation=delegation, followups=followups,
                 done=(case.stage not in ("intake_sent", "followup")
                       and delegation is None))


@app.post("/intake/{case_id}/followup-answer")
async def intake_followup_answer(
    case_id: str,
    followup_id: str = Form(...),
    answer: str = Form(...),
) -> JSONResponse:
    """The requester answers one reviewer follow-up. Writes onto the REAL
    assessment record; when the last one is answered the case returns to the
    dashboard review queue."""
    case = STORE.cases.get(case_id)
    if not case or case.stage != "followup":
        return JSONResponse({"ok": False}, status_code=404)
    aid = getattr(case, "assessment_id", "")
    from maverick.assessment import answer_followup
    record = await asyncio.to_thread(
        answer_followup, aid, followup_id.strip(), answer.strip(),
        case.requester)
    if record is None:
        return JSONResponse({"ok": False, "error": "unknown question"},
                            status_code=404)
    gid = getattr(case, "goal_id", None)
    _audit("FOLLOWUP_ANSWERED", ANALYST, gid, case=case_id,
           followup=followup_id.strip())
    remaining = [f for f in (record.get("followups") or []) if not f.get("answer")]
    if not remaining:
        case.stage = "pending_review"
        case.log("All follow-up answers received; back with the privacy team.")
        if gid:
            _post_event(gid, ANALYST, "observation",
                        "Requester answered every follow-up question. "
                        "Assessment is back in the review queue.")
    return JSONResponse({"ok": True, "remaining": len(remaining)})


@app.post("/intake/{case_id}/delegate")
async def intake_delegate(case_id: str, request: Request) -> JSONResponse:
    """Hand specific questions (or the whole remainder) to a colleague: a
    scoped intake link is emailed; their answers merge into the same case,
    attributed to them."""
    case = STORE.cases.get(case_id)
    if not case:
        return JSONResponse({"ok": False}, status_code=404)
    body = await request.json()
    qids = [str(q) for q in (body.get("questions") or []) if q][:20]
    to_name = str(body.get("to_name", "")).strip()[:80] or "a colleague"
    to_email = str(body.get("to_email", "")).strip()[:200]
    note = str(body.get("note", "")).strip()[:500]
    if not qids or not to_email or "@" not in to_email:
        return JSONResponse({"ok": False, "error": "questions and a valid "
                             "colleague email are required"}, status_code=400)
    import uuid
    token = uuid.uuid4().hex[:10]
    dele = getattr(case, "delegations", None)
    if dele is None:
        dele = {}
        case.delegations = dele  # type: ignore[attr-defined]
    dele[token] = {"qids": qids, "to_name": to_name, "to_email": to_email,
                   "note": note, "answered": []}
    gid = getattr(case, "goal_id", None)
    _audit("INTAKE_DELEGATED", ANALYST, gid, case=case_id, to=to_email,
           questions=len(qids))
    if gid:
        _post_event(gid, ANALYST, "observation",
                    f"{case.requester} delegated {len(qids)} question(s) "
                    f"to {to_name} ({to_email}).")
    case.log(f"Delegated {len(qids)} question(s) to {to_name}.")
    link = f"{BASE_URL}/intake/{case_id}?d={token}"
    await _send_email(
        to_email,
        f"[Action needed] {case.requester} needs your input — privacy "
        f"assessment for {case.subject}",
        (
            f"Hi {to_name},\n\n"
            f"{case.requester} is completing a privacy assessment for "
            f"{case.subject} and delegated {len(qids)} question(s) to you"
            + (f':\n\n    "{note}"\n' if note else ".\n")
            + f"\nAnswer them here (takes a minute):\n\n    {link}\n\n"
            f"— Privacy Office (automated)\n"
        ),
    )
    return JSONResponse({"ok": True, "token": token,
                         "delegated": {t: {"to_name": r["to_name"],
                                           "qids": r["qids"]}
                                       for t, r in dele.items()}})


@app.post("/intake/{case_id}/answer")
async def intake_answer(
    case_id: str,
    question_id: str = Form(...),
    answer: str = Form(...),
    note: str = Form(""),
    d: str = Form(""),
) -> JSONResponse:
    case = STORE.cases.get(case_id)
    if not case:
        return JSONResponse({"ok": False, "error": "unknown case"}, status_code=404)
    rec = {"answer": answer.strip().lower(), "note": note.strip()}
    # A delegate's answer is attributed and only accepted for their questions.
    if d:
        dele = case.delegations.get(d)
        if not dele or question_id not in dele["qids"]:
            return JSONResponse({"ok": False, "error": "not your question"},
                                status_code=403)
        rec["by"] = dele["to_name"]
        if question_id not in dele["answered"]:
            dele["answered"].append(question_id)
        gid = getattr(case, "goal_id", None)
        if gid and len(dele["answered"]) == len(dele["qids"]):
            _post_event(gid, ANALYST, "observation",
                        f"{dele['to_name']} answered all {len(dele['qids'])} "
                        f"delegated question(s).")
    case.answers[question_id] = rec
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


def _store_evidence(case: Case, filename: str, data: bytes, mime: str,
                    *, via: str) -> tuple[bool, str]:
    """Store evidence bytes as a REAL goal attachment (size cap, mime
    allowlist, executable/archive deny — the platform's own rules), mirror it
    onto the case's upload list, and audit. Returns (ok, message)."""
    gid = getattr(case, "goal_id", None)
    label = f"{filename} ({len(data):,} bytes)"
    if gid:
        try:
            from maverick.attachments import AttachmentRejected, store
            w = _world()
            existing = sum(a.size_bytes for a in w.list_attachments(gid))
            stored = store(gid, filename=filename, mime=mime, data=data,
                           existing_total=existing)
            w.add_attachment(goal_id=gid, filename=stored.filename,
                             mime=stored.mime, size_bytes=stored.size_bytes,
                             sha256=stored.sha256, path=str(stored.path))
            label = f"{stored.filename} ({stored.size_bytes:,} bytes)"
        except AttachmentRejected as exc:
            return False, str(exc)
        except Exception as exc:  # pragma: no cover - demo resilience
            print(f"[attach] real store failed, keeping demo record: {exc}")
    case.uploads.append(f"{label} — via {via}")
    _audit("EVIDENCE_CAPTURE", ANALYST, gid, case=case.id, artifact=filename,
           bytes=len(data), via=via)
    if gid:
        _post_event(gid, ANALYST, "artifact",
                    f"Evidence attached ({via}): {label}")
    return True, label


@app.post("/intake/{case_id}/upload")
async def intake_upload(case_id: str, file: UploadFile) -> JSONResponse:
    case = STORE.cases.get(case_id)
    if not case:
        return JSONResponse({"ok": False}, status_code=404)
    # Bounded read: the intake link is unauthenticated by design, so an
    # oversized body must fail fast instead of buffering without limit.
    from maverick.attachments import MAX_FILE_BYTES
    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        return JSONResponse(
            {"ok": False, "error": f"file too large (limit {MAX_FILE_BYTES} "
                                   "bytes)", "uploads": case.uploads},
            status_code=400)
    mime = file.content_type or "application/octet-stream"
    ok, msg = await asyncio.to_thread(
        _store_evidence, case, file.filename or "upload", data, mime,
        via="upload")
    if not ok:
        return JSONResponse({"ok": False, "error": msg,
                             "uploads": case.uploads}, status_code=400)
    return JSONResponse({"ok": True, "uploads": case.uploads})


@app.post("/intake/{case_id}/discover")
async def intake_discover(case_id: str) -> JSONResponse:
    """Search the connected document sources (here: the simulated SharePoint
    tenant, via the REAL maverick.doc_discovery module) for this case's
    SOW/contract/DPA/security paperwork."""
    case = STORE.cases.get(case_id)
    if not case:
        return JSONResponse({"ok": False}, status_code=404)
    hits = await asyncio.to_thread(_discover_mock_documents, case.subject)
    gid = getattr(case, "goal_id", None)
    _audit("TOOL_CALL", ANALYST, gid, tool="doc_discovery", case=case_id,
           subject=case.subject, hits=len(hits))
    if gid and hits:
        _post_event(gid, ANALYST, "observation",
                    f"Doc discovery found {len(hits)} candidate document(s) "
                    f"for {case.subject} in connected sources.")
    return JSONResponse({"ok": True, "hits": [h.to_dict() for h in hits]})


@app.post("/intake/{case_id}/attach-found")
async def intake_attach_found(case_id: str, request: Request) -> JSONResponse:
    """One-click attach of a discovered document: fetch through the real
    doc_discovery path, store as real goal evidence."""
    case = STORE.cases.get(case_id)
    if not case:
        return JSONResponse({"ok": False}, status_code=404)
    body = await request.json()
    source = str(body.get("source", ""))
    if source != "msgraph":
        return JSONResponse(
            {"ok": False, "error": "document source is not available in this demo"},
            status_code=400,
        )
    doc_id = str(body.get("doc_id", ""))
    name = str(body.get("name", "")) or f"{source}-{doc_id[:16]}"
    ref = body.get("ref") or {}
    from maverick import doc_discovery
    try:
        data, mime = await asyncio.to_thread(
            _fetch_mock_document,
            source,
            doc_id,
            ref,
        )
    except ValueError as exc:  # our own clean validation messages
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception:  # noqa: BLE001 -- no transport/URL detail to the caller
        return JSONResponse({"ok": False, "error": "could not fetch that "
                             "document from the source"}, status_code=400)
    ok, msg = await asyncio.to_thread(
        _store_evidence, case, name, data,
        doc_discovery.resolve_mime(name, mime), via=f"found in {source}")
    status = 200 if ok else 400
    return JSONResponse({"ok": ok, "error": None if ok else msg,
                         "uploads": case.uploads}, status_code=status)


@app.get("/intake/{case_id}/insights")
async def intake_insights(case_id: str) -> JSONResponse:
    """What the org already knows: similar past assessments (real
    maverick.assessment records) + advisory per-question suggestions.

    Deliberate demo tradeoff: like the rest of the intake surface this is
    reachable by case id, so precedent subjects/ratings/notes are visible to
    anyone holding an intake link. The PLATFORM twin
    (``GET /api/v1/assessment-memory/similar``) sits behind the dashboard's
    ``view`` permission — production respondent portals should proxy through
    that, not replicate this open endpoint."""
    case = STORE.cases.get(case_id)
    if not case:
        return JSONResponse({"ok": False}, status_code=404)
    from maverick import assessment_memory

    def _collect() -> dict[str, Any]:
        return {
            "similar": assessment_memory.similar(
                case.subject, assessment_type="pia", k=3),
            "suggestions": assessment_memory.suggest_answers(
                "pia", case.subject, k=3),
            "lessons": assessment_memory.lessons(case.subject, k=2),
        }

    data = await asyncio.to_thread(_collect)
    return JSONResponse({"ok": True, **data})


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
                if rec.get("by"):  # delegate attribution, shown in the review pop-out
                    session.answers[qid]["by"] = rec["by"]
            except Exception:
                continue
        result = session.evaluate()
        # Persist where `maverick assess list` reads; remember the record id
        # so the dashboard review pop-out and the follow-up loop can find it.
        try:
            save_session(session)
            case.assessment_id = session.id  # type: ignore[attr-defined]
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
            "inherent_risk": result.inherent_risk,
            "residual_risk": result.residual_risk,
            "risks_in_scope": result.risks_in_scope,
            "controls_in_place": result.controls_in_place,
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
            detail=(f"First-pass assessment {case.id} — inherent {result.inherent_risk.upper()} "
                    f"→ residual {result.residual_risk.upper()} "
                    f"({result.controls_in_place} of {result.risks_in_scope} risk areas controlled), "
                    f"{result.answered}/{result.total} answered, "
                    f"{len(result.findings)} findings. Findings: {findings_txt}. "
                    f"Required controls: {controls_txt}. Uploads: {', '.join(case.uploads) or 'none'}. "
                    # The token the dashboard's review pop-out keys on.
                    f"[assessment:{getattr(case, 'assessment_id', '') or 'unsaved'}]"),
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
                # Reviewer asked for more detail (dashboard pop-out)? Reopen
                # the interview before looking at the approve/deny state.
                if await asyncio.to_thread(_open_followups, case):
                    await _reopen_for_followups(case)
                    continue
                status, decided_by = await asyncio.to_thread(_approval_state, case)
                if status == "approved":
                    await _file_to_onetrust(case, decided_by)
                elif status in ("denied", "rejected"):
                    await _handle_rejection(case, decided_by)
        except Exception as exc:  # pragma: no cover
            print(f"[watcher] {exc}")
        await asyncio.sleep(2)


def _open_followups(case: Case) -> list[dict]:
    """Unanswered reviewer follow-ups on this case's assessment record."""
    aid = getattr(case, "assessment_id", "")
    if not aid:
        return []
    try:
        from maverick.assessment import load_saved
        record = load_saved(aid) or {}
        return [f for f in (record.get("followups") or []) if not f.get("answer")]
    except Exception:  # pragma: no cover - demo resilience
        return []


async def _reopen_for_followups(case: Case) -> None:
    """The privacy team sent questions back: re-interview the requester."""
    gid = getattr(case, "goal_id", None)
    case.stage = "followup"
    n = len(_open_followups(case))
    case.log(f"Privacy team asked {n} follow-up question(s); re-interviewing.")
    _audit("FOLLOWUP_REQUESTED", ANALYST, gid, case=case.id, questions=n)
    if gid:
        _post_event(gid, ANALYST, "observation",
                    f"Reviewer sent {n} follow-up question(s) back to "
                    f"{case.requester}. Awaiting answers.")
    intake_link = f"{BASE_URL}/intake/{case.id}"
    await _send_email(
        case.requester_email,
        f"[Action needed] Follow-up questions on your privacy assessment ({case.id})",
        (
            f"Hi {case.requester},\n\n"
            f"The privacy team reviewed your assessment for {case.subject} and "
            f"needs a bit more detail — {n} quick question(s):\n\n    {intake_link}\n\n"
            f"Your earlier answers are saved; this only covers the new questions.\n\n"
            f"— Privacy Office (automated)\n"
        ),
    )


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
    # Serializable payload for the record-detail pop-out.
    records_json = [{
        "assessment_id": a.assessment_id, "name": a.name,
        "template": a.template, "subject": a.subject, "status": a.status,
        "risk_level": a.risk_level,
        "inherent_risk": (a.result or {}).get("inherent_risk", a.risk_level),
        "residual_risk": (a.result or {}).get("residual_risk", a.risk_level),
        "controls_in_place": (a.result or {}).get("controls_in_place", 0),
        "risks_in_scope": (a.result or {}).get("risks_in_scope", 0),
        "answered": (a.result or {}).get("answered", 0),
        "total": (a.result or {}).get("total", 0),
        "findings": (a.result or {}).get("findings", []),
    } for a in items]
    return _page(request, "onetrust.html", assessments=items,
                 records_json=records_json)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "cases": len(STORE.cases),
                        "tickets": len(STORE.tickets), "filed": len(STORE.onetrust)})
