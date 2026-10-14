"""Offline business-intent detection and explicitly heuristic starter benchmarks.

These ranges are product planning assumptions, not published research, measured
customer effort, or a claim about cash savings. Customers can replace each one.
Detection reads task instructions; it never calls a model or executes work.
"""
from __future__ import annotations

import copy
import re

VERSION = "2026-10-starter-v1"
PROVENANCE = "Lightwork product heuristic; not a measured or externally validated benchmark"
SIZE_FACTORS = {"small": 0.5, "standard": 1.0, "large": 2.0}

# id, readable name, topic expression, required action expression, minutes range.
_DEFINITIONS = (
    ("rfp_draft", "Draft an RFP", r"\brfp\b|request for proposals?", r"draft|creat|writ|prepar|build", (120, 240, 480)),
    ("rfp_review", "Review an RFP", r"\brfp\b|request for proposals?", r"review|analy[sz]|evaluat|assess|check", (45, 90, 180)),
    ("proposal_response", "Prepare a proposal response", r"proposal|\brfp\b", r"respond|response|answer|bid", (120, 240, 480)),
    ("financial_analysis", "Analyze financial performance", r"financ|profit|margin|cash flow|variance", r"analy[sz]|review|compar|assess|evaluat", (45, 120, 240)),
    ("forecast", "Prepare a forecast", r"forecast|projection", r"forecast|project|build|prepar|updat", (60, 150, 300)),
    ("budget", "Prepare a budget", r"budget", r"build|prepar|creat|plan|updat", (60, 150, 300)),
    ("reconciliation", "Reconcile financial records", r"reconcil", r"reconcil", (30, 90, 180)),
    ("invoice_review", "Review invoices or expenses", r"invoice|expenses?", r"review|check|validat|match|process", (10, 30, 60)),
    ("contract_review", "Review a contract", r"contract|agreement|\bnda\b", r"review|analy[sz]|check|redlin|assess", (45, 120, 240)),
    ("contract_draft", "Draft a contract", r"contract|agreement|\bnda\b", r"draft|writ|creat|prepar", (90, 180, 360)),
    ("research", "Research a business question", r"research|investigat|market|competitor", r"research|investigat|analy[sz]|compar", (30, 90, 180)),
    ("report", "Prepare a report", r"report|briefing|business case", r"draft|writ|creat|prepar|build", (45, 120, 240)),
    ("presentation", "Prepare a presentation", r"presentation|slides|deck", r"draft|creat|prepar|build|design", (45, 120, 240)),
    ("email_draft", "Draft business correspondence", r"email|letter|message", r"draft|writ|respond|reply|compos", (5, 15, 30)),
    ("summarization", "Summarize source material", r"summari[sz]|summary|meeting notes", r"summari[sz]|summary|extract|prepar", (10, 30, 60)),
    ("data_analysis", "Analyze a dataset", r"data|dataset|spreadsheet|csv|workbook", r"analy[sz]|compar|calculate|explor", (30, 90, 180)),
    ("data_extraction", "Extract or organize records", r"data|record|document|pdf|table", r"extract|classif|categori[sz]|organiz|clean", (15, 45, 90)),
    ("customer_support", "Resolve a customer request", r"customer|support ticket|helpdesk", r"respond|resolv|triage|answer", (5, 20, 45)),
    ("compliance_review", "Review compliance evidence", r"complian|audit|control|regulat|policy", r"review|check|assess|evaluat|analy[sz]", (45, 120, 240)),
    ("recruiting", "Review recruiting materials", r"resume|candidate|job description|recruit", r"review|screen|draft|match|assess", (15, 45, 90)),
    ("project_planning", "Plan a project", r"project|roadmap|milestone|project plan", r"plan|schedule|build|prepar", (30, 90, 180)),
    ("marketing_content", "Prepare marketing content", r"marketing|campaign|blog|social media", r"draft|writ|creat|prepar|plan", (30, 90, 180)),
    ("software_work", "Develop or review software", r"code|software|script|function|bug|test", r"implement|fix|debug|review|writ|build", (30, 120, 300)),
    ("procurement", "Evaluate suppliers", r"supplier|vendor|procurement|purchase", r"evaluat|compar|assess|review|sourc", (30, 90, 180)),
)


def catalog() -> list[dict]:
    return [{"intent_id": key, "label": label, "minutes_low": low,
             "minutes": middle, "minutes_high": high, "hourly_rate": 75.0,
             "currency": "USD", "version": VERSION, "provenance": PROVENANCE,
             "basis": "starter_heuristic", "size_factors": dict(SIZE_FACTORS)}
            for key, label, _topic, _action, (low, middle, high) in _DEFINITIONS]


def benchmark(intent_id: str, overrides: dict | None = None, *, size: str = "standard") -> dict | None:
    if size not in SIZE_FACTORS:
        raise ValueError("Choose small, standard or large work size.")
    row = next((row for row in catalog() if row["intent_id"] == intent_id), None)
    if row is None:
        return None
    override = (overrides or {}).get(intent_id)
    if override:
        row.update(copy.deepcopy(override))
    row["size"] = size
    row["size_factor"] = SIZE_FACTORS[size]
    row["sized_minutes"] = {key: round(float(row[key]) * SIZE_FACTORS[size], 4)
                            for key in ("minutes_low", "minutes", "minutes_high")}
    return row


def detect(text: str) -> dict:
    """Conservative explicit topic/action matching; ambiguity remains unknown."""
    text = str(text or "")[:32000].lower()
    matches = [key for key, _label, topic, action, _range in _DEFINITIONS
               if re.search(topic, text) and re.search(action, text)]
    # Specific artifact operations win over generic report/data/research terms.
    specific = [key for key in matches if key.startswith(("rfp_", "contract_"))
                or key == "proposal_response"]
    if specific:
        matches = specific
    if len(matches) != 1:
        return {"intent_id": "unknown", "confidence": "uncertain" if matches else "unknown",
                "candidates": matches, "basis": "explicit_topic_action_rules",
                "note": "Choose the work type to estimate its value."}
    return {"intent_id": matches[0], "confidence": "moderate", "candidates": matches,
            "basis": "explicit_topic_action_rules",
            "note": "Detected from task instructions; review or correct this interpretation."}
