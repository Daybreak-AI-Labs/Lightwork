# PIA Concierge — governed privacy-assessment demo

**The platform is the demo.** A privacy-assessment request submitted in any
ticketing system is picked up by Lightwork, which interviews the requester,
scores the risk with the real assessment engine, maps required controls with
framework citations, parks the decision in the real governed approvals queue,
and — only after a human approves — files the completed assessment into
OneTrust and resolves the originating ticket. Every step lands in the real
Ed25519-signed audit chain.

## What is real vs. simulated

| Real (actual product code) | Simulated (this harness) |
|---|---|
| Goal + live timeline on the dashboard (`/goals`, `/chat/goal/{id}`) | ServiceNow tenant (`:8890/servicenow`) — fires a real HTTP webhook |
| PIA scoring — `maverick.assessment` (GDPR Art. 35 template) | OneTrust tenant (`:8890/onetrust` + `/ot-api`) — receives the real tool's POST |
| Control mapping — `maverick.controls` (GDPR/ISO 27001/SOC 2/NIST/HIPAA citations) | Requester mailbox (`:8890/mail`) — real SMTP+STARTTLS delivery, captured locally |
| Approval — real world-model approval, decided on the real `/approvals` page | Requester questionnaire (`:8890/intake/{case}`) — the emailed link |
| Email send — `maverick.tools.email_tool` (SMTP) | SharePoint tenant (`:8890/graph-sim`) — Graph-shaped search + download, seeded with each vendor's SOW/MSA/DPA/security PDFs |
| OneTrust filing — `maverick.tools.onetrust_tool` (confirm-gated POST) | |
| Signed audit chain — `maverick.audit` (verify with `maverick audit verify`) | |
| Evidence storage — `maverick.attachments` (real goal attachments: size caps, mime allowlist, executable deny) | |
| Doc discovery — `maverick.doc_discovery` (searches connected sources for the SOW/contract/DPA) | |
| Assessment memory — `maverick.assessment_memory` (similar past assessments + suggested answers, advisory) | |

Point `ONETRUST_HOSTNAME` at a real OneTrust sandbox and the filing is live —
same code path. Any ticketing system that can send a webhook can replace the
mock ServiceNow. Export a real `MSGRAPH_ACCESS_TOKEN` (and unset
`MSGRAPH_BASE_URL`) and "Find my documents" searches your real SharePoint /
OneDrive — same code path there too. The intake questionnaire learns across
runs: submit one assessment, open a similar one, and the questions show
one-click suggested answers with provenance (nothing is auto-answered).

## Run it

```bash
bash demo/pia-concierge/run_demo.sh
```

- Lightwork dashboard → http://127.0.0.1:8765 (goals · approvals · audit)
- External world → http://127.0.0.1:8890 (ServiceNow · inbox · OneTrust)

No LLM key and no external accounts needed. State lives in
`demo/pia-concierge/.demo-home` (delete it for a clean slate). Both servers run
in ONE process on purpose: the audit chain's signer requires a single writing
process — the same shape as `maverick dashboard` itself.

## Demo script (~6 minutes)

Two browser windows: **left** = external world (:8890), **right** = Lightwork
dashboard (:8765).

1. **[left → ServiceNow]** File the pre-filled privacy request. *"An employee
   asks for a privacy review the way they already do — a ticket. They never
   learn a new tool."*
2. **[right → Goals]** The goal is already there, timeline running. *"The
   webhook made it a governed goal — intake from anything: ServiceNow, Jira,
   email, a form."*
3. **[left → Requester inbox]** Open the intake email, click the link, answer
   the questionnaire (or hit ⚡ Auto-fill), attach a file, submit. *"The
   requester is interviewed by the platform — a controlled instrument, not a
   freewheeling chatbot. Two minutes, no privacy-team time."*
4. **[right → Govern → Approvals]** The money shot: *File PIA … (risk: HIGH)*
   with findings and required controls — each control carrying GDPR / ISO 27001
   / SOC 2 / NIST citations. *"First-pass assessment done. And here is the
   governance: the platform cannot file this anywhere until a person approves.
   Click Approve."*
5. **[left]** OneTrust tab now shows the filed assessment; the ServiceNow
   ticket is Resolved with the risk rating in the work notes; the requester got
   a completion email. *"Filed by the real OneTrust integration — repoint one
   hostname at your tenant and this is live."*
6. **[right → Govern → Audit]** The signed chain: GOAL_START → TOOL_CALL(email)
   → ASSESSMENT_REVIEW → AUTONOMY_GATED → APPROVAL_DECISION →
   TOOL_CALL(onetrust). *"Ticket to filed assessment, one tamper-evident,
   Ed25519-signed record. Your own survey says 78% of executives couldn't pass
   an AI-governance audit in 90 days — this is how their agents pass it."*

Optional closer (terminal):

```bash
MAVERICK_HOME=demo/pia-concierge/.demo-home maverick audit verify
MAVERICK_HOME=demo/pia-concierge/.demo-home maverick assess list
```

## Preflight (demo day)

```bash
pkill -f serve.py; rm -rf demo/pia-concierge/.demo-home   # clean slate
bash demo/pia-concierge/run_demo.sh                        # then dry-run once
```

- Dry-run the whole flow once, then reset `.demo-home` again.
- Don't set `MAVERICK_DASHBOARD_TOKEN` locally (it gates every page).
- The launcher already sets `MAVERICK_SHIELD_PROFILE=strict` and
  `MAVERICK_DEFAULT_MAX_DOLLARS=10`.
- Keep both windows pre-opened; the approvals page auto-refreshes on reload
  only — F5 after the requester submits.

## Architecture

```
ServiceNow (mock, :8890) ──webhook──▶ harness ──create_goal/append_event──▶ world.db ◀── dashboard (:8765)
                                        │                                        ▲
        requester inbox (SMTP sink) ◀───┤ email_tool                             │ human approves on /approvals
        questionnaire /intake/{case} ───┤ assessment engine + controls           │
                                        └─create_approval ─────────────────────▶─┘
        watcher (2s poll) ── approval approved? ──▶ onetrust_tool POST ──▶ OneTrust (mock)
                                                └─▶ ticket resolved · completion email · goal done
        every step ──▶ maverick.audit (Ed25519 signed chain)
```
