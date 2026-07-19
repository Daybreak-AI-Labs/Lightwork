"""The seam between the PIA Concierge agent and the Lightwork platform.

Every capability the app needs is resolved here to either the real
``maverick`` implementation (platform build) or a self-contained standalone
implementation (sold-on-its-own build). The app imports from this module and
never branches on the mode itself.

Standalone loses, by design: the governed engine and control catalog, the
signed audit chain, world-model governance, learning, connectors, and the
LLM assist. What survives is the agent: scoring, OneTrust filing + review,
document append, and vendor memory.
"""
from __future__ import annotations

import os
from typing import Any

from capabilities import CAPS, STANDALONE

# --------------------------------------------------------------------------- #
# Engine: the scorer + record store.
# --------------------------------------------------------------------------- #
if STANDALONE:
    from pia_engine import (  # noqa: F401
        AssessmentSession,
        add_followups,
        answer_followup,
        decide_assessment,
        get_template,
        list_saved,
        list_templates,
        load_saved,
        save_session,
        template_department,
    )
    DEFAULT_DB = None
else:
    from maverick.assessment import (  # noqa: F401
        AssessmentSession,
        add_followups,
        answer_followup,
        decide_assessment,
        get_template,
        list_saved,
        list_templates,
        load_saved,
        save_session,
        template_department,
    )
    from maverick.world_model import DEFAULT_DB  # noqa: F401


# --------------------------------------------------------------------------- #
# World-model governance (goals + approval queue) — platform only.
# --------------------------------------------------------------------------- #
def world():
    """The shared world model, or None when there is no platform to govern
    against. Callers must treat None as 'no governed goal/approval here'."""
    if STANDALONE:
        return None
    from maverick.world_model import DEFAULT_DB as _db
    from maverick.world_model import WorldModel
    return WorldModel(_db)


# --------------------------------------------------------------------------- #
# Signed audit chain — platform only (no audit trail standalone, by choice).
# --------------------------------------------------------------------------- #
def audit_record(kind: str, *, agent: str = "", goal_id: int | None = None,
                 **payload: Any) -> None:
    if not CAPS["signed_audit"]:
        return
    try:
        from maverick.audit import record
        record(kind, agent=agent, goal_id=goal_id, **payload)
    except Exception as exc:  # pragma: no cover - demo resilience
        print(f"[audit] {kind} failed: {exc}")


# --------------------------------------------------------------------------- #
# Control mapping with framework citations — platform only.
# --------------------------------------------------------------------------- #
def find_controls(text: str, limit: int = 2) -> list:
    if not CAPS["control_catalog"]:
        return []
    from maverick.controls import find_controls as _fc
    return _fc(text, limit=limit)


# --------------------------------------------------------------------------- #
# Email — real SMTP tool on the platform; in-process delivery standalone.
# --------------------------------------------------------------------------- #
def send_email(to: str, subject: str, body: str) -> str:
    """Synchronous send. Platform routes through the governed email tool;
    standalone delivers straight into the in-process capture inbox."""
    if STANDALONE:
        import mailsink
        sender = os.environ.get("EMAIL_USER", "privacy-office@company.com")
        mailsink.INBOX.insert(0, mailsink.Captured(
            to=to or "requester@example.com", sender=sender,
            subject=subject, body=body))
        return "ok (standalone: delivered to inbox)"
    from maverick.tools.email_tool import email_tool
    return email_tool().fn({
        "op": "send", "to": to or "requester@example.com",
        "subject": subject, "body": body})


# --------------------------------------------------------------------------- #
# OneTrust filing — governed tool on the platform; direct HTTP standalone.
# --------------------------------------------------------------------------- #
def file_onetrust(body: dict) -> str:
    """POST an assessment to the OneTrust tenant. Returns the tool-shaped
    reply string ('ok (201): {json}' / 'error ...') the app already parses."""
    path = "/api/assessment/v2/assessments"
    if not STANDALONE:
        from maverick.tools.onetrust_tool import onetrust_tool
        return onetrust_tool().fn({
            "op": "post", "path": path, "body": body, "confirm": True})
    # Standalone: the same confirm-gated POST, without the governed tool.
    import httpx
    base = os.environ.get("ONETRUST_HOSTNAME", "http://127.0.0.1:8890/ot-api")
    token = os.environ.get("ONETRUST_TOKEN", "demo-bearer-token")
    try:
        r = httpx.post(f"{base}{path}", json=body,
                       headers={"Authorization": f"Bearer {token}"},
                       timeout=10)
        if r.status_code // 100 == 2:
            return f"ok ({r.status_code}): {r.text}"
        return f"error ({r.status_code}): {r.text[:200]}"
    except Exception as exc:  # pragma: no cover - demo resilience
        return f"error: {exc}"


# --------------------------------------------------------------------------- #
# Attachments — the upload size cap. The governed store (mime allowlist, dedup,
# executable deny) is applied inside app._store_evidence only when a goal
# exists, i.e. on the platform; standalone just records the upload metadata.
# --------------------------------------------------------------------------- #
if STANDALONE:
    MAX_FILE_BYTES = 100 * 1024 * 1024
else:
    from maverick.attachments import MAX_FILE_BYTES  # noqa: F401


# --------------------------------------------------------------------------- #
# Document discovery (connected sources) — platform + connector only.
# --------------------------------------------------------------------------- #
def discovery_available() -> bool:
    return bool(CAPS["doc_discovery"])


# --------------------------------------------------------------------------- #
# Art. 28 DPA clause review on appended documents — platform only.
# --------------------------------------------------------------------------- #
def dpa_clause_review(vendor: str, text: str, *, filename: str, mime: str,
                      data: bytes) -> dict | None:
    """Full clause-by-clause Art. 28 review (returns the saved record), or
    None when the platform engine isn't present."""
    if not CAPS["dpa_clause_review"]:
        return None
    from maverick import privacy_ops
    extraction: dict = {}
    try:
        text = privacy_ops._document_text(data, mime, _metadata=extraction) or text
    except Exception:
        pass
    return privacy_ops.review_dpa(
        vendor, text, document_name=filename, reviewed_by="pia-concierge",
        _document_evidence=privacy_ops._build_document_evidence(
            data=data, text=text, mime=mime, source="upload",
            doc_id=filename, ref=None, extraction=extraction))


def previous_dpa_gaps(vendor: str) -> set[str]:
    if not CAPS["dpa_clause_review"]:
        return set()
    from maverick import privacy_ops
    gaps: set[str] = set()
    try:
        for summary in privacy_ops.list_dpa_reviews():
            if ((summary.get("vendor") or "").strip().lower()
                    == vendor.strip().lower()):
                full = privacy_ops.get_dpa_review(summary["id"]) or {}
                gaps = {c["requirement"] for c in full.get("clauses", [])
                        if c.get("status") == "missing"}
    except Exception:  # pragma: no cover - demo resilience
        pass
    return gaps


# --------------------------------------------------------------------------- #
# Document text extraction (for standalone addendum summary).
# --------------------------------------------------------------------------- #
def extract_text(data: bytes, mime: str) -> str:
    if not STANDALONE:
        from maverick import privacy_ops
        try:
            return privacy_ops._document_text(data, mime) or ""
        except Exception:
            return ""
    # Standalone: only decode text-like payloads; PDFs/docx need the platform.
    m = (mime or "").split(";")[0].strip().lower()
    if m.startswith("text/") or m in ("application/json", "application/xml"):
        return data.decode("utf-8", errors="ignore")
    return ""


# --------------------------------------------------------------------------- #
# LLM-assisted chat interpretation — platform only.
# --------------------------------------------------------------------------- #
def llm_interpret(question: str, text: str) -> str | None:
    if not CAPS["llm_assist"] or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from maverick.budget import Budget
        from maverick.llm import LLM, model_for_role
        llm = LLM(model_for_role("summarizer"))
        resp = llm.complete(
            system=("You classify a respondent's reply to one yes/no privacy "
                    "questionnaire question. Answer with exactly one word: "
                    "yes, no, na, or unknown."),
            messages=[{"role": "user",
                       "content": f"Question: {question}\nReply: {text[:1000]}"}],
            budget=Budget(max_dollars=0.25, max_output_tokens=1000),
            max_tokens=10)
        word = (resp.text or "").strip().lower().split()
        return word[0] if word and word[0] in ("yes", "no", "na", "unknown") else None
    except Exception as exc:  # pragma: no cover - network/key variance
        print(f"[interpret-llm] degraded to scripted clarification: {exc}")
        return None
