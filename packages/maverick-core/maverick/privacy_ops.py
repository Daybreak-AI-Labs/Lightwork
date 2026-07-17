"""Privacy operations record types: the "nimble OneTrust" data plane.

Four privacy record types over one tenant-scoped JSON store, each pairing a
deterministic, explainable engine with a lifecycle the Privacy workspace can
review — the same design as :mod:`maverick.assessment` (records are governance
artifacts meant to be exported and audited, not secrets):

* **DPA reviews** — a Data Processing Agreement checked clause-by-clause
  against the GDPR Art. 28(3) requirements (documented instructions,
  confidentiality, Art. 32 security, sub-processor authorization, data-subject
  assistance, breach notification, deletion/return, audit rights) plus
  transfer-safeguard and retention checks. Detection is keyword-heuristic and
  honest: ``present`` / ``unclear`` / ``missing`` with the matched excerpt, so
  a reviewer can verify every call. Residual risk falls as required clauses
  are found; inherent risk reflects the exposure the document itself declares
  (transfers, special categories, broad scopes).
* **AI systems registry** — an inventory of AI systems classified against the
  EU AI Act's tiers (prohibited / high / limited / minimal) using the Act's
  own category tests (Annex III high-risk areas, Art. 5 prohibited practices,
  Art. 50 transparency triggers), each classification carrying its matched
  signals and the tier's obligations. Screening aid, not legal advice.
* **RoPA / data inventory** — Art. 30(1) records of processing activities.
  ``draft_from_assessment`` turns a completed PIA into a draft RoPA entry
  (answers map onto Art. 30 fields), which is the system-of-record moment:
  assessments feed the inventory instead of dying in a filing cabinet.
* **DSAR requests** — a tracker with statutory due dates (30 days) over the
  REAL fulfillment machinery: access/portability exports run
  :func:`maverick.dsar.export_subject_data`; erasure records the exact
  ``maverick erase`` invocation (destructive fulfillment stays a deliberate,
  operator-run step).

Gated by ``[privacy_ops] enable`` (default on — local record stores, already
behind the workspace's operate floor). Everything fails soft and nothing here
auto-approves, auto-erases, or bypasses the human gates.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

# --------------------------------------------------------------------------- #
# Config gate + shared store
# --------------------------------------------------------------------------- #

def enabled() -> bool:
    try:
        from .config import load_config
        return bool((load_config().get("privacy_ops") or {})
                    .get("enable", True))
    except Exception:  # pragma: no cover -- config never crashes a read
        return True


class _RecordStore:
    """Tenant-scoped JSON records: ``data_dir(<name>)/<id>.json`` at 0600,
    with the same path-traversal guard the assessment store uses."""

    def __init__(self, name: str, prefix: str) -> None:
        self.name = name
        self.prefix = prefix

    def _dir(self) -> Path:
        from .paths import data_dir
        return data_dir(self.name)

    def new_id(self) -> str:
        return f"{self.prefix}-{uuid.uuid4().hex[:10]}"

    def save(self, record: dict) -> dict:
        d = self._dir()
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:  # pragma: no cover
            pass
        path = d / f"{record['id']}.json"
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(record, indent=2, default=str))
        return record

    def load(self, record_id: str) -> dict | None:
        base = self._dir()
        path = (base / f"{record_id}.json").resolve()
        if path.parent != base.resolve():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def list(self) -> list[dict]:
        d = self._dir()
        if not d.exists():
            return []
        out = []
        for p in d.glob("*.json"):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda r: r.get("created_at", 0), reverse=True)

    def update(self, record_id: str, mutate) -> dict | None:
        record = self.load(record_id)
        if record is None:
            return None
        mutate(record)
        return self.save(record)


_DPA = _RecordStore("dpa_reviews", "DPA")
_AI = _RecordStore("ai_registry", "AI")
_ROPA = _RecordStore("ropa", "ROPA")
_DSAR = _RecordStore("dsar_requests", "DSAR")

# --------------------------------------------------------------------------- #
# DPA review -- Art. 28(3) clause checklist
# --------------------------------------------------------------------------- #

# (key, requirement, citation, severity-if-missing, detection patterns)
DPA_CHECKLIST: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    ("instructions", "Processing only on documented controller instructions",
     "Art. 28(3)(a)", "high",
     (r"documented instruction", r"written instruction",
      r"only on (?:the )?instruction", r"instructions (?:of|from) the controller")),
    ("confidentiality", "Personnel bound to confidentiality",
     "Art. 28(3)(b)", "medium",
     (r"confidentialit", r"duty of confidence", r"non-disclosure")),
    ("security", "Art. 32 technical and organisational security measures",
     "Art. 28(3)(c) / Art. 32", "high",
     (r"technical and organi[sz]ational", r"article 32", r"art\.? ?32",
      r"encrypt", r"security measures")),
    ("subprocessors", "Sub-processor authorization and flow-down",
     "Art. 28(2), 28(4)", "high",
     (r"sub-?processor", r"subcontract")),
    ("assistance", "Assistance with data-subject rights requests",
     "Art. 28(3)(e)", "medium",
     (r"data subject request", r"assist the controller",
      r"rights of (?:the )?data subject", r"dsar")),
    ("breach", "Personal-data breach notification to the controller",
     "Art. 28(3)(f) / Art. 33", "high",
     (r"breach", r"security incident", r"notif")),
    ("deletion", "Deletion or return of data at end of services",
     "Art. 28(3)(g)", "medium",
     (r"delet(?:e|ion) or return", r"return or delet", r"end of the provision",
      r"termination.{0,80}(?:delete|return|destro)")),
    ("audit", "Audit and inspection rights for the controller",
     "Art. 28(3)(h)", "medium",
     (r"audit", r"inspection")),
    ("transfers", "Safeguards for international transfers",
     "Chapter V (Art. 44-49)", "high",
     (r"standard contractual clauses", r"\bscc", r"adequacy decision",
      r"chapter v", r"transfer mechanism", r"data privacy framework")),
    ("retention", "Defined retention / storage limitation",
     "Art. 5(1)(e)", "medium",
     (r"retention", r"retain(?:ed|s)? for", r"storage period")),
)

# Exposure signals: what the document itself says the processing involves.
_DPA_EXPOSURE = (
    ("international transfers", "high",
     (r"transfer", r"third countr", r"outside the (?:eu|eea|european)")),
    ("special-category data", "high",
     (r"special categor", r"health data", r"biometric", r"sensitive data")),
    ("large-scale / monitoring", "medium",
     (r"large.?scale", r"monitor", r"profil")),
    ("personal data processing", "medium",
     (r"personal data",)),
)


def _find(text_lower: str, patterns: tuple[str, ...],
          with_prefix: bool = False):
    """First match's surrounding excerpt (plus, optionally, the text right
    before the match — for adjacent-negation checks). None when no pattern
    hits."""
    for pat in patterns:
        m = re.search(pat, text_lower)
        if m:
            start = max(0, m.start() - 60)
            excerpt = text_lower[start:m.end() + 90].strip()
            if with_prefix:
                prefix = text_lower[max(0, m.start() - 30):m.start()]
                return excerpt, prefix
            return excerpt
    return (None, "") if with_prefix else None


def review_dpa(vendor: str, text: str, *, document_name: str = "",
               reviewed_by: str = "") -> dict:
    """Review DPA ``text`` against the Art. 28 checklist. Deterministic and
    explainable: every verdict carries the matched excerpt (or its absence).
    Returns the saved record."""
    body = (text or "")[:2_000_000]
    low = body.lower()
    clauses = []
    residual_sev: list[str] = []
    for key, requirement, citation, severity, patterns in DPA_CHECKLIST:
        excerpt, prefix = _find(low, patterns, with_prefix=True)
        if excerpt is None:
            status = "missing"
            residual_sev.append(severity)
        elif re.search(r"\b(?:not|no|without|never|excluded?)\b[^.;]{0,20}$",
                       prefix):
            # Negating language IMMEDIATELY before the clause ("shall not
            # delete...", "no audit rights") -- flag for a human call rather
            # than crediting or failing it. Negation elsewhere in the window
            # (e.g. "notify without undue delay") is normal DPA phrasing.
            status = "unclear"
            residual_sev.append("low")
        else:
            status = "present"
        clauses.append({
            "key": key, "requirement": requirement, "citation": citation,
            "severity": severity, "status": status,
            "excerpt": (excerpt or "")[:240],
        })
    inherent_sev = []
    exposures = []
    for label, severity, patterns in _DPA_EXPOSURE:
        if _find(low, patterns):
            inherent_sev.append(severity)
            exposures.append(label)
    from .assessment import _rollup
    present = sum(1 for c in clauses if c["status"] == "present")
    record = {
        "id": _DPA.new_id(),
        "vendor": (vendor or "").strip()[:200],
        "document_name": (document_name or "").strip()[:300],
        "created_at": time.time(),
        "reviewed_by": reviewed_by,
        "status": "pending_review",
        "clauses": clauses,
        "exposures": exposures,
        "clauses_present": present,
        "clauses_total": len(clauses),
        "inherent_risk": _rollup(inherent_sev),
        "residual_risk": _rollup(residual_sev),
        "chars_reviewed": len(body),
    }
    return _DPA.save(record)


_PDF_LITERAL = re.compile(rb"\((?:\\.|[^\\()])*\)")
_PDF_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b",
                b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}


def _pdf_literal_bytes(lit: bytes) -> bytes:
    """Decode one PDF literal string ``(...)`` including escapes/octals."""
    out = bytearray()
    i, end = 1, len(lit) - 1
    while i < end:
        c = lit[i:i + 1]
        if c == b"\\" and i + 1 < end:
            nxt = lit[i + 1:i + 2]
            if nxt.isdigit():  # octal \ddd (1-3 digits)
                j = i + 1
                while j < min(i + 4, end) and lit[j:j + 1].isdigit():
                    j += 1
                try:
                    out.append(int(lit[i + 1:j], 8) & 0xFF)
                except ValueError:  # pragma: no cover
                    pass
                i = j
                continue
            out += _PDF_ESCAPES.get(nxt, nxt)
            i += 2
            continue
        out += c
        i += 1
    return bytes(out)


def _pdf_text(data: bytes) -> str:
    """Best-effort text from a digital PDF with the stdlib only.

    Decompresses FlateDecode content streams (zlib) and reads the text-
    showing operators (``Tj``, ``'``, and ``TJ`` arrays; hex strings too).
    Honest limits: no encryption, no CID/Unicode font maps -- a scanned or
    exotic PDF yields too little text and the caller says so instead of
    reviewing garbage."""
    import zlib
    chunks: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.DOTALL):
        raw = m.group(1)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass  # uncompressed content stream -- use as-is
        if b"Tj" not in raw and b"TJ" not in raw and b"'" not in raw:
            continue
        # TJ arrays: concatenate the string chunks (kerning splits words).
        for arr in re.finditer(rb"\[((?:[^\]]|\\.)*?)\]\s*TJ", raw):
            parts = [_pdf_literal_bytes(lit.group(0))
                     for lit in _PDF_LITERAL.finditer(arr.group(1))]
            if parts:
                chunks.append(b"".join(parts).decode("latin-1"))
        # Plain Tj / ' operators, one string each.
        for lit in re.finditer(rb"(\((?:\\.|[^\\()])*\))\s*(?:Tj|')",
                               raw):
            chunks.append(_pdf_literal_bytes(lit.group(1)).decode("latin-1"))
        for hexs in re.finditer(rb"<([0-9A-Fa-f\s]+)>\s*Tj", raw):
            digits = re.sub(rb"\s", b"", hexs.group(1))
            if len(digits) % 2:
                digits += b"0"
            try:
                chunks.append(bytes.fromhex(digits.decode()).decode("latin-1"))
            except ValueError:  # pragma: no cover
                continue
    text = "\n".join(chunks)
    printable = re.sub(r"[^\x20-\x7e\n\u00a0-\uffff]", "", text)
    return printable if len(printable.strip()) >= 40 else ""


def _document_text(data: bytes, mime: str) -> str:
    """Honest text extraction for the formats we can read with the stdlib:
    text/json/xml decode as UTF-8; docx is a ZIP whose word/document.xml we
    strip of tags. Anything else returns "" and the caller says so — no
    pretending a PDF was read."""
    m = (mime or "").split(";")[0].strip().lower()
    if m.startswith("text/") or m in ("application/json", "application/xml"):
        return data.decode("utf-8", errors="ignore")
    if m == "application/pdf":
        return _pdf_text(data)
    if m == ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document"):
        import io
        import zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = z.read("word/document.xml").decode("utf-8",
                                                         errors="ignore")
        except (KeyError, zipfile.BadZipFile, OSError):
            return ""
        return re.sub(r"<[^>]+>", "", xml.replace("</w:p>", "\n"))
    return ""


def review_dpa_from_document(vendor: str, source: str, doc_id: str, *,
                             ref: dict | None = None,
                             document_name: str = "") -> dict:
    """Review a DPA straight from a connected source (MS Graph / Slack /
    Google Drive via :mod:`maverick.doc_discovery`) — find the contract where
    it already lives instead of pasting it. Raises ValueError when the source
    is unconfigured or the document's format yields no text."""
    from . import doc_discovery
    data, mime = doc_discovery.fetch(source, doc_id, ref)
    mime = doc_discovery.resolve_mime(document_name or doc_id, mime)
    text = _document_text(data, mime)
    if not text.strip():
        raise ValueError(
            f"cannot extract text from {mime or 'unknown format'} — "
            "supported: text, JSON/XML, docx, and digital (text-based) "
            "PDFs. Scanned documents need pasting instead.")
    return review_dpa(vendor, text, document_name=document_name or doc_id)


def list_dpa_reviews() -> list[dict]:
    return [{k: r.get(k) for k in
             ("id", "vendor", "document_name", "created_at", "status",
              "inherent_risk", "residual_risk", "clauses_present",
              "clauses_total")} for r in _DPA.list()]


def get_dpa_review(review_id: str) -> dict | None:
    return _DPA.load(review_id)


# --------------------------------------------------------------------------- #
# AI systems registry -- EU AI Act screening
# --------------------------------------------------------------------------- #

AI_ACT_NOTE = ("Automated screening against the EU AI Act's category tests. "
               "A screening aid for triage, not legal advice.")

_PROHIBITED = (
    ("social scoring by public authorities", (r"social scor",)),
    ("subliminal / manipulative techniques", (r"subliminal", r"manipulat")),
    ("exploiting vulnerabilities", (r"exploit.{0,40}vulnerab",)),
    ("real-time remote biometric ID in public", (r"real.?time.{0,40}biometric",
                                                 r"biometric.{0,40}public")),
)
_HIGH_RISK = (
    ("employment / worker management (Annex III 4)",
     (r"hiring", r"recruit", r"employment", r"worker", r"cv screen",
      r"resume", r"candidate", r"promotion", r"termination")),
    ("credit / essential services (Annex III 5)",
     (r"credit", r"loan", r"scoring", r"insurance", r"benefit")),
    ("education (Annex III 3)", (r"exam", r"student", r"education", r"grading")),
    ("biometrics (Annex III 1)", (r"biometric", r"face recogni", r"emotion")),
    ("critical infrastructure (Annex III 2)",
     (r"critical infrastructure", r"utility", r"power grid", r"water suppl")),
    ("law enforcement / justice (Annex III 6-8)",
     (r"law enforcement", r"police", r"judicial", r"asylum", r"border")),
)
_LIMITED = (
    ("interacts with people (Art. 50 transparency)",
     (r"chatbot", r"assistant", r"conversation", r"customer service")),
    ("generates content (Art. 50)", (r"generat", r"synthetic", r"deepfake")),
)

_TIER_OBLIGATIONS = {
    "prohibited": ["Do not deploy in the EU (Art. 5)."],
    "high": ["Risk management system (Art. 9)", "Data governance (Art. 10)",
             "Technical documentation + logging (Art. 11-12)",
             "Human oversight (Art. 14)", "Accuracy/robustness (Art. 15)",
             "Conformity assessment + CE marking before market (Art. 43)",
             "FRIA where applicable (Art. 27)"],
    "limited": ["Transparency: disclose AI interaction / AI-generated "
                "content (Art. 50)"],
    "minimal": ["Voluntary codes of conduct (Art. 95)"],
}


def classify_ai_system(name: str, purpose: str) -> dict:
    """EU AI Act tier for a described system, with the matched signals."""
    text = f"{name} {purpose}".lower()
    for label, patterns in _PROHIBITED:
        if _find(text, patterns):
            return {"tier": "prohibited", "signals": [label],
                    "obligations": _TIER_OBLIGATIONS["prohibited"]}
    signals = [label for label, patterns in _HIGH_RISK if _find(text, patterns)]
    if signals:
        return {"tier": "high", "signals": signals,
                "obligations": _TIER_OBLIGATIONS["high"]}
    signals = [label for label, patterns in _LIMITED if _find(text, patterns)]
    if signals:
        return {"tier": "limited", "signals": signals,
                "obligations": _TIER_OBLIGATIONS["limited"]}
    return {"tier": "minimal", "signals": [],
            "obligations": _TIER_OBLIGATIONS["minimal"]}


def register_ai_system(name: str, purpose: str, *, provider: str = "",
                       owner: str = "", assessment_id: str = "") -> dict:
    """Add an AI system to the registry, classified on the way in."""
    cls = classify_ai_system(name, purpose)
    record = {
        "id": _AI.new_id(),
        "name": (name or "").strip()[:200],
        "purpose": (purpose or "").strip()[:2000],
        "provider": (provider or "").strip()[:200],
        "owner": (owner or "").strip()[:200],
        "assessment_id": (assessment_id or "").strip()[:64],
        "created_at": time.time(),
        "status": "registered",
        "tier": cls["tier"],
        "signals": cls["signals"],
        "obligations": cls["obligations"],
        "note": AI_ACT_NOTE,
    }
    return _AI.save(record)


_TIER_RANK = {"minimal": 0, "limited": 1, "high": 2, "prohibited": 3}


def register_ai_system_from_assessment(assessment_id: str) -> dict | None:
    """Register the system a completed AIRA assessment describes — the
    assessment feeds the registry the same way a PIA feeds the RoPA.

    Keyword screening runs as usual, then the assessment's own attestations
    ratchet the tier UPWARD only (an attested Art. 5 practice or Annex III
    use outranks quiet wording; nothing ever downgrades)."""
    from .assessment import load_saved
    rec = load_saved(assessment_id)
    if rec is None:
        return None
    answers = rec.get("answers") or {}

    def ans(qid: str) -> str:
        return str((answers.get(qid) or {}).get("answer", ""))

    def note(qid: str) -> str:
        return str((answers.get(qid) or {}).get("note", ""))

    record = register_ai_system(
        rec.get("subject") or "unnamed system",
        note("aira_purpose") or f"See assessment {assessment_id}",
        assessment_id=assessment_id)
    tier, signals = record["tier"], list(record["signals"])
    if ans("aira_prohibited") == "yes" and _TIER_RANK[tier] < 3:
        tier = "prohibited"
        signals.append("assessment attests an Art. 5 prohibited practice")
    elif ans("aira_high_risk") == "yes" and _TIER_RANK[tier] < 2:
        tier = "high"
        signals.append("assessment attests an Annex III high-risk use")
    if tier != record["tier"]:
        def _ratchet(r: dict) -> None:
            r["tier"] = tier
            r["signals"] = signals
            r["obligations"] = _TIER_OBLIGATIONS[tier]
        record = _AI.update(record["id"], _ratchet)
    return record


def list_ai_systems() -> list[dict]:
    return _AI.list()


def get_ai_system(system_id: str) -> dict | None:
    return _AI.load(system_id)


# --------------------------------------------------------------------------- #
# RoPA / data inventory -- Art. 30(1)
# --------------------------------------------------------------------------- #

ROPA_FIELDS = ("activity", "purpose", "controller", "data_categories",
               "data_subjects", "recipients", "transfers", "retention",
               "security_measures")


def upsert_ropa(record: dict, *, ropa_id: str = "") -> dict:
    """Create or update one Art. 30 processing-activity record."""
    rid = (ropa_id or "").strip()
    base = _ROPA.load(rid) if rid else None
    out = dict(base or {
        "id": _ROPA.new_id(), "created_at": time.time(),
        "source": record.get("source", "manual"), "assessment_id": "",
    })
    for f in ROPA_FIELDS:
        if record.get(f) is not None:
            out[f] = str(record.get(f, ""))[:2000]
    for f in ROPA_FIELDS:
        out.setdefault(f, "")
    if record.get("assessment_id"):
        out["assessment_id"] = str(record["assessment_id"])[:64]
    out["updated_at"] = time.time()
    return _ROPA.save(out)


# PIA answers -> Art. 30 field hints. The draft is explicit about provenance
# ("from PIA ...") and everything stays editable -- a draft, not a verdict.
def draft_ropa_from_assessment(assessment_id: str) -> dict | None:
    """Auto-draft a RoPA entry from a completed assessment (the moment the
    assessment flow starts FEEDING the inventory)."""
    from .assessment import load_saved
    rec = load_saved(assessment_id)
    if rec is None:
        return None
    answers = rec.get("answers") or {}

    def ans(qid: str) -> str:
        return str((answers.get(qid) or {}).get("answer", ""))

    def note(qid: str) -> str:
        return str((answers.get(qid) or {}).get("note", ""))

    transfers = "None declared"
    if ans("pia_transfers") == "yes":
        transfers = ("Transfers outside EU/EEA WITHOUT Chapter V safeguard "
                     "declared" + (f" — {note('pia_transfers')}"
                                   if note("pia_transfers") else ""))
    retention = ("Defined retention schedule attested"
                 if ans("pia_retention") == "yes"
                 else "No defined retention period"
                 + (f" — {note('pia_retention')}" if note("pia_retention") else ""))
    security = ("Encryption in transit and at rest attested"
                if ans("pia_security") == "yes" else "Security posture unclear")
    return upsert_ropa({
        "activity": f"Processing via {rec.get('subject', 'unknown system')}",
        "purpose": note("pia_necessity") or "See assessment record",
        "controller": "",
        "data_categories": "Personal data (see assessment"
                           + (", special categories declared)" if
                              ans("pia_special_category") == "yes" else ")"),
        "data_subjects": "See assessment record",
        "recipients": ("Processors under Art. 28 DPA"
                       if ans("pia_processors") == "yes"
                       else "Processor without countersigned DPA declared"),
        "transfers": transfers,
        "retention": retention,
        "security_measures": security,
        "assessment_id": assessment_id,
        "source": f"assessment:{assessment_id}",
    })


# OneTrust RoPA/Data-Mapping CSV export -> Art. 30 fields. Their column
# names drift across versions and templates, so each field carries the
# aliases seen in real exports; matching is case-insensitive.
_ONETRUST_HEADERS = {
    "activity": ("processing activity name", "processing activity",
                 "activity name", "activity", "name"),
    "purpose": ("purpose of processing", "purposes of processing",
                "purpose", "purposes"),
    "controller": ("controller", "legal entity", "organization"),
    "data_categories": ("personal data categories", "data categories",
                        "categories of personal data", "data elements"),
    "data_subjects": ("categories of data subjects", "data subject types",
                      "data subjects"),
    "recipients": ("categories of recipients", "recipients",
                   "third party recipients"),
    "transfers": ("cross-border transfers", "international transfers",
                  "transfer mechanism", "transfers"),
    "retention": ("retention period", "retention schedule", "retention"),
    "security_measures": ("technical and organizational measures",
                          "security measures", "security"),
}


def import_onetrust_ropa(csv_text: str) -> list[dict]:
    """Import OneTrust's RoPA / Data Mapping CSV export into the Art. 30
    register — the "connect to OneTrust" on-ramp: their record comes in with
    ``source="onetrust"`` provenance and stays editable here.

    Tolerant of their column-name drift; rows without a recognizable
    activity name are skipped rather than guessed at."""
    import csv as _csv
    import io
    reader = _csv.DictReader(io.StringIO(csv_text or ""))
    imported = []
    for row in list(reader)[:5000]:
        low = {(k or "").strip().lower(): (v or "").strip()
               for k, v in row.items()}
        entry: dict = {}
        for field, aliases in _ONETRUST_HEADERS.items():
            for alias in aliases:
                if low.get(alias):
                    entry[field] = low[alias]
                    break
        if not entry.get("activity"):
            continue
        entry["source"] = "onetrust"
        imported.append(upsert_ropa(entry))
    return imported


def list_ropa() -> list[dict]:
    return _ROPA.list()


def export_ropa_art30() -> list[dict]:
    """The Art. 30 register, exportable shape (stable field order)."""
    return [{f: r.get(f, "") for f in
             ("id", *ROPA_FIELDS, "assessment_id", "source")}
            for r in _ROPA.list()]


# --------------------------------------------------------------------------- #
# DSAR request tracker -- over the real fulfillment machinery
# --------------------------------------------------------------------------- #

DSAR_KINDS = ("access", "portability", "erasure")
DSAR_DUE_DAYS = 30  # GDPR Art. 12(3): one month


def open_dsar(subject_id: str, kind: str, *, channel: str = "",
              opened_by: str = "") -> dict:
    kind = (kind or "").strip().lower()
    if kind not in DSAR_KINDS:
        raise ValueError(f"kind must be one of {DSAR_KINDS}")
    now = time.time()
    record = {
        "id": _DSAR.new_id(),
        "subject_id": (subject_id or "").strip()[:200],
        "channel": (channel or "").strip()[:64],
        "kind": kind,
        "opened_by": opened_by,
        "created_at": now,
        "due_at": now + DSAR_DUE_DAYS * 86400,
        "status": "open",
        "fulfillment": None,
    }
    return _DSAR.save(record)


def list_dsars() -> list[dict]:
    now = time.time()
    out = []
    for r in _DSAR.list():
        if "status" not in r:  # not a request record (defensive)
            continue
        r = dict(r)
        r["days_left"] = int((float(r.get("due_at", now)) - now) // 86400)
        r["overdue"] = r["status"] == "open" and r["days_left"] < 0
        out.append(r)
    return out


def fulfill_dsar(request_id: str) -> dict | None:
    """Fulfill an open request through the REAL machinery.

    ``access`` / ``portability``: runs :func:`maverick.dsar.export_subject_data`
    and stores the bundle (counts recorded on the request). ``erasure``: never
    destructive from here -- records the exact ``maverick erase`` invocation
    for the operator to run deliberately (the CLI carries the confirmation
    flow, knowledge-plane scrub, and audit trail)."""
    record = _DSAR.load(request_id)
    if record is None or record.get("status") != "open":
        return None
    if record["kind"] in ("access", "portability"):
        from .dsar import export_subject_data
        bundle = export_subject_data(
            record["subject_id"], channel=record.get("channel") or None)
        from .paths import data_dir
        # Bundles live in a subdirectory: the record store globs *.json in
        # its own dir, and a bundle is not a request record.
        out_dir = data_dir("dsar_requests") / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{record['id']}-export.json"
        fd = os.open(str(out_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                     0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(bundle, indent=2, default=str))
        def _done(r: dict) -> None:
            r["status"] = "fulfilled"
            r["fulfilled_at"] = time.time()
            r["fulfillment"] = {"export_path": str(out_path),
                                "counts": bundle.get("counts", {})}
        return _DSAR.update(request_id, _done)
    # erasure: deliberate operator step, never an API side effect.
    cmd = (f"maverick erase --user {record['subject_id']}"
           + (f" --channel {record['channel']}" if record.get("channel") else ""))
    def _ready(r: dict) -> None:
        r["status"] = "awaiting_erasure"
        r["fulfillment"] = {"erase_command": cmd}
    return _DSAR.update(request_id, _ready)


def close_dsar(request_id: str, *, closed_by: str = "") -> dict | None:
    def _close(r: dict) -> None:
        r["status"] = "closed"
        r["closed_at"] = time.time()
        r["closed_by"] = closed_by
    return _DSAR.update(request_id, _close)


__all__ = [
    "AI_ACT_NOTE", "DPA_CHECKLIST", "DSAR_DUE_DAYS", "DSAR_KINDS",
    "ROPA_FIELDS", "classify_ai_system", "close_dsar",
    "draft_ropa_from_assessment", "enabled", "export_ropa_art30",
    "fulfill_dsar", "get_ai_system", "get_dpa_review",
    "import_onetrust_ropa", "list_ai_systems",
    "list_dpa_reviews", "list_dsars", "list_ropa", "open_dsar",
    "register_ai_system", "register_ai_system_from_assessment",
    "review_dpa", "review_dpa_from_document", "upsert_ropa",
]
