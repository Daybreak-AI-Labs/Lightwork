"""Governance assessments: privacy, security, and AI-risk reviews of every
capability the platform runs.

An *assessment* is a governance record for one subject (an agent pack or a
flow). It carries three lenses -- ``privacy``, ``security``, ``ai_risk`` -- each
a list of findings auto-drafted from the subject's *declared* surface (the tools
it may reach, the data/hosts/paths it touches, its risk ceiling, whether a human
gate exists). A human then reviews each lens (accept / needs-work), attaches
evidence, and sets a re-review cadence. When the subject's surface later changes,
:func:`refresh` detects the drift (via a surface hash) and flips an accepted
assessment back to ``needs_review`` -- the "naturally improves as it changes"
guarantee.

Storage is a plain JSON register under the tenant data home (assessments are
governance artefacts meant to be exported and audited, not secrets), so this
adds no world-model migration. The auto-draft heuristics are intentionally
conservative and explainable; they never *block* -- they surface what a reviewer
should look at.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .paths import data_dir

LENSES = ("privacy", "security", "ai_risk")
LENS_LABELS = {"privacy": "Privacy", "security": "Security", "ai_risk": "AI risk"}
_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}

# Tool-name substrings that signal a capability class. Matched case-insensitively
# against a subject's allow_tools; deliberately broad so a new tool named e.g.
# ``slack_bot`` still reads as an egress/network sink.
_SHELL_HINTS = ("shell", "exec", "bash", "subprocess", "code_run", "python")
_WRITE_HINTS = ("write", "edit", "create_file", "delete", "fs_", "file_write")
_EGRESS_HINTS = ("web", "browser", "http", "fetch", "email", "slack", "webhook",
                 "post", "send", "upload", "sms", "call", "publish")
_READ_DATA_HINTS = ("read_file", "files", "knowledge", "search", "db", "sql",
                    "query", "email_read", "crm", "sheet")
_CRED_HINTS = ("connector", "oauth", "token", "secret", "credential", "api_key")


# --------------------------------------------------------------------------- #
# Subject surface -- the declared capability footprint we assess.
# --------------------------------------------------------------------------- #

def agent_surface(name: str) -> dict[str, Any] | None:
    """The assessed surface of an agent pack, or ``None`` if unknown."""
    try:
        from .domain_edit import resolved_view
        v = resolved_view(name)
    except Exception:  # pragma: no cover -- factory layer optional
        return None
    if v is None:
        return None
    steps = v.get("workflow") or []
    gated = any((s or {}).get("gate") in ("approval", "review") for s in steps)
    return {
        "kind": "agent",
        "subject": name,
        "description": v.get("description") or "",
        "allow_tools": sorted(v.get("allow_tools") or []),
        "deny_tools": sorted(v.get("deny_tools") or []),
        "max_risk": v.get("max_risk") or "low",
        "allow_paths": sorted(v.get("allow_paths") or []),
        "allow_hosts": sorted(v.get("allow_hosts") or []),
        "mcp_servers": sorted(v.get("mcp_servers") or []),
        "knowledge_sources": sorted(v.get("knowledge_sources") or []),
        "has_human_gate": gated,
        "steps": len(steps),
    }


def flow_surface(flow_id: str) -> dict[str, Any] | None:
    """The assessed surface of a flow: the union of tools its nodes reach and
    whether any node is an approval gate. ``None`` if the flow is unknown."""
    try:
        from .flow import store as flow_store
        flow = flow_store.load_flow(flow_id)
    except Exception:  # pragma: no cover -- flow engine optional
        return None
    if flow is None:
        return None
    nodes = list((flow.nodes or {}).values()) if isinstance(flow.nodes, dict) else list(flow.nodes or [])
    tools: set[str] = set()
    kinds: set[str] = set()
    gated = False
    for n in nodes:
        n = n if isinstance(n, dict) else {}
        kinds.add(str(n.get("kind") or ""))
        if n.get("kind") == "approval":
            gated = True
        for t in (n.get("tools") or []):
            tools.add(str(t))
    return {
        "kind": "flow",
        "subject": flow_id,
        "description": getattr(flow, "name", "") or "",
        "allow_tools": sorted(tools),
        "deny_tools": [],
        "max_risk": "medium" if tools else "low",
        "allow_paths": [], "allow_hosts": [], "mcp_servers": [],
        "knowledge_sources": [],
        "has_human_gate": gated,
        "steps": len(nodes),
    }


def subject_surface(kind: str, name: str) -> dict[str, Any] | None:
    return agent_surface(name) if kind == "agent" else flow_surface(name)


def _hash_surface(surface: dict[str, Any]) -> str:
    """A stable digest of the capability-bearing fields, so a later change to
    tools / data / risk / gating is detectable as drift."""
    keyed = {k: surface.get(k) for k in (
        "allow_tools", "deny_tools", "max_risk", "allow_paths", "allow_hosts",
        "mcp_servers", "knowledge_sources", "has_human_gate")}
    blob = json.dumps(keyed, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Auto-draft heuristics -- one generator per lens.
# --------------------------------------------------------------------------- #

def _matches(tools: list[str], hints: tuple[str, ...]) -> list[str]:
    lo = [t.lower() for t in tools]
    return sorted({tools[i] for i, t in enumerate(lo) if any(h in t for h in hints)})


def _finding(lens: str, control: str, severity: str, note: str, mitigation: str) -> dict:
    return {"lens": lens, "control": control, "severity": severity, "note": note,
            "mitigation": mitigation, "status": "open", "evidence": []}


def _privacy_findings(s: dict) -> list[dict]:
    out: list[dict] = []
    reads = _matches(s["allow_tools"], _READ_DATA_HINTS)
    egress = _matches(s["allow_tools"], _EGRESS_HINTS)
    if s["knowledge_sources"]:
        out.append(_finding("privacy", "Sensitive data access", "medium",
            f"Reads {len(s['knowledge_sources'])} knowledge source(s); may surface personal or confidential data.",
            "Confirm the sources contain no PII beyond what this task needs; apply redaction at ingestion."))
    if reads:
        out.append(_finding("privacy", "Data collection", "low",
            "Can read data via: " + ", ".join(reads) + ".",
            "Scope reads to the minimum records required (data minimisation)."))
    if reads and egress:
        out.append(_finding("privacy", "Cross-border / third-party transfer", "high",
            "Reads data AND can send it outward via: " + ", ".join(egress) + ".",
            "Verify a lawful basis and DPA for any external recipient; keep an egress allow-list."))
    if s["allow_hosts"]:
        out.append(_finding("privacy", "External endpoints", "low",
            "Reaches hosts: " + ", ".join(s["allow_hosts"]) + ".",
            "Confirm each host is an approved processor."))
    if not out:
        out.append(_finding("privacy", "Data handling", "low",
            "No obvious data-access or egress tools declared.",
            "Re-check after any tool change."))
    return out


def _security_findings(s: dict) -> list[dict]:
    out: list[dict] = []
    shell = _matches(s["allow_tools"], _SHELL_HINTS)
    writes = _matches(s["allow_tools"], _WRITE_HINTS)
    creds = _matches(s["allow_tools"], _CRED_HINTS)
    if shell:
        out.append(_finding("security", "Arbitrary code / shell execution", "high",
            "Can execute code/shell via: " + ", ".join(shell) + ".",
            "Ensure execution is sandboxed (sandbox.exec); require an approval gate for risky steps."))
    if writes or s["allow_paths"]:
        out.append(_finding("security", "Write / filesystem access", "medium",
            "Can modify state via: " + ", ".join(writes + s["allow_paths"]) + ".",
            "Constrain writable paths; prefer review before irreversible writes."))
    if creds or s["mcp_servers"]:
        out.append(_finding("security", "Credential / connector reach", "medium",
            "Uses connectors/servers: " + ", ".join(creds + s["mcp_servers"]) + ".",
            "Store secrets as sealed connections; scope tokens to least privilege."))
    if s["max_risk"] == "high":
        out.append(_finding("security", "Risk ceiling", "high",
            "Risk ceiling is 'high' — the widest action envelope.",
            "Justify why 'high' is required; add a human gate on the terminal step."))
    if not out:
        out.append(_finding("security", "Capability envelope", "low",
            "No high-risk execution, write, or credential tools declared.",
            "Re-check after any tool or risk-ceiling change."))
    return out


def _ai_risk_findings(s: dict) -> list[dict]:
    out: list[dict] = []
    risky = _SEVERITY_RANK.get(s["max_risk"], 0) >= 1
    if risky and not s["has_human_gate"]:
        out.append(_finding("ai_risk", "Human oversight", "high",
            "Acts at medium/high risk with no approval or review gate in its playbook.",
            "Add an approval/review gate before consequential actions."))
    elif s["has_human_gate"]:
        out.append(_finding("ai_risk", "Human oversight", "low",
            "A human approval/review gate is present.",
            "Keep the gate on the consequential step; review gate placement periodically."))
    else:
        out.append(_finding("ai_risk", "Human oversight", "low",
            "Low-risk ceiling; autonomous operation is proportionate.",
            "Re-evaluate if the risk ceiling is raised."))
    out.append(_finding("ai_risk", "Accuracy / hallucination", "low",
        "Model output can be wrong or fabricated; impact scales with the action envelope.",
        "Ground with knowledge sources where possible; verify before acting on generated facts."))
    if _matches(s["allow_tools"], _EGRESS_HINTS):
        out.append(_finding("ai_risk", "Autonomous external action", "medium",
            "Can take outward actions (send/post/publish) that are hard to reverse.",
            "Gate outward actions; log them to the operating record for audit."))
    return out


_GENERATORS = {"privacy": _privacy_findings, "security": _security_findings,
               "ai_risk": _ai_risk_findings}

# Built-in templates select which lenses to run. Custom templates (same shape)
# can be dropped into <home>/assessments/templates.json.
TEMPLATES: dict[str, dict] = {
    "baseline": {"label": "Baseline (privacy · security · AI risk)", "lenses": list(LENSES)},
    "security_only": {"label": "Security review only", "lenses": ["security"]},
    "privacy_only": {"label": "Privacy / DPIA only", "lenses": ["privacy"]},
}


def list_templates() -> dict[str, dict]:
    """Built-in templates merged with any custom ones on disk."""
    out = dict(TEMPLATES)
    path = data_dir("assessments", "templates.json")
    try:
        if path.exists():
            for tid, tpl in (json.loads(path.read_text("utf-8")) or {}).items():
                lenses = [x for x in (tpl.get("lenses") or []) if x in LENSES]
                if lenses:
                    out[str(tid)] = {"label": str(tpl.get("label") or tid), "lenses": lenses}
    except Exception:  # pragma: no cover -- custom templates fail soft
        pass
    return out


def lens_status(findings: list[dict]) -> str:
    """'attention' if any open finding is high severity, else 'ok'."""
    for f in findings:
        if f.get("status") == "open" and _SEVERITY_RANK.get(f.get("severity"), 0) >= 2:
            return "attention"
    return "ok"


def auto_draft(kind: str, name: str, template: str = "baseline",
               now: float | None = None) -> dict | None:
    """A fresh assessment for ``name``, findings generated per the template's
    lenses. ``None`` if the subject is unknown."""
    surface = subject_surface(kind, name)
    if surface is None:
        return None
    now = time.time() if now is None else now
    tpl = list_templates().get(template) or TEMPLATES["baseline"]
    lenses: dict[str, dict] = {}
    for lens in tpl["lenses"]:
        findings = _GENERATORS[lens](surface)
        lenses[lens] = {"status": "open", "findings": findings, "reviewer": "",
                        "reviewed_at": None, "note": ""}
    return {
        "kind": kind, "subject": name, "template": template,
        "description": surface.get("description") or "",
        "subject_hash": _hash_surface(surface),
        "status": "draft", "lenses": lenses,
        "cadence_days": 0, "created_at": now, "updated_at": now,
        "reviewed_at": None, "due_at": None,
    }


# --------------------------------------------------------------------------- #
# Store -- one JSON register keyed by "<kind>:<subject>".
# --------------------------------------------------------------------------- #

def _path():
    return data_dir("assessments", "register.json")


def _key(kind: str, name: str) -> str:
    return f"{kind}:{name}"


def _load_all() -> dict[str, dict]:
    path = _path()
    try:
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:  # pragma: no cover -- a corrupt register never crashes
        return {}
    return {}


def _save_all(data: dict[str, dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, sort_keys=True, indent=2), "utf-8")
    tmp.replace(path)


def list_assessments() -> list[dict]:
    """Every stored assessment, newest-updated first, with a rolled-up summary."""
    out = [_summarize(a) for a in _load_all().values()]
    out.sort(key=lambda a: a.get("updated_at") or 0, reverse=True)
    return out


def _summarize(a: dict) -> dict:
    lenses = a.get("lenses") or {}
    open_high = sum(
        1 for lv in lenses.values() for f in lv.get("findings", [])
        if f.get("status") == "open" and _SEVERITY_RANK.get(f.get("severity"), 0) >= 2)
    return {**a, "open_high": open_high,
            "lens_status": {k: lens_status(v.get("findings", [])) for k, v in lenses.items()}}


def get_assessment(kind: str, name: str) -> dict | None:
    a = _load_all().get(_key(kind, name))
    return _summarize(a) if a else None


def delete_assessment(kind: str, name: str) -> bool:
    data = _load_all()
    if _key(kind, name) in data:
        del data[_key(kind, name)]
        _save_all(data)
        return True
    return False


def refresh(kind: str, name: str, template: str | None = None,
            now: float | None = None) -> dict | None:
    """Create the assessment if new, or re-draft it and detect drift if it
    exists. An existing review decision is preserved unless the subject's
    surface changed, in which case the assessment flips to ``needs_review``."""
    now = time.time() if now is None else now
    data = _load_all()
    existing = data.get(_key(kind, name))
    tpl = template or (existing or {}).get("template") or "baseline"
    fresh = auto_draft(kind, name, tpl, now=now)
    if fresh is None:
        return None
    if existing is None:
        data[_key(kind, name)] = fresh
        _save_all(data)
        return _summarize(fresh)

    drifted = existing.get("subject_hash") != fresh["subject_hash"]
    merged = dict(existing)
    merged["subject_hash"] = fresh["subject_hash"]
    merged["template"] = tpl
    merged["description"] = fresh["description"]
    merged["updated_at"] = now
    # Re-draft findings, but carry over per-finding review state (status +
    # evidence) for controls that still exist, keyed by (lens, control).
    old_by_key = {
        (lens, f.get("control")): f
        for lens, lv in (existing.get("lenses") or {}).items()
        for f in lv.get("findings", [])
    }
    new_lenses: dict[str, dict] = {}
    for lens, lv in fresh["lenses"].items():
        prev_lv = (existing.get("lenses") or {}).get(lens, {})
        findings = []
        for f in lv["findings"]:
            old = old_by_key.get((lens, f.get("control")))
            if old and not drifted:
                f = {**f, "status": old.get("status", "open"),
                     "evidence": list(old.get("evidence") or [])}
            findings.append(f)
        new_lenses[lens] = {
            "status": "open" if drifted else prev_lv.get("status", "open"),
            "findings": findings,
            "reviewer": "" if drifted else prev_lv.get("reviewer", ""),
            "reviewed_at": None if drifted else prev_lv.get("reviewed_at"),
            "note": prev_lv.get("note", ""),
        }
    merged["lenses"] = new_lenses
    if drifted and existing.get("status") in ("reviewed", "accepted"):
        merged["status"] = "needs_review"
        merged["drift_note"] = "The subject changed since last review — re-assess."
    _save_all(data | {_key(kind, name): merged})
    return _summarize(merged)


def record_review(kind: str, name: str, lens: str, decision: str, *,
                  reviewer: str = "", note: str = "", cadence_days: int | None = None,
                  now: float | None = None) -> dict | None:
    """Record a reviewer's decision on one lens (``accepted`` / ``needs_work``).
    Accepting a lens marks its open findings accepted. When every lens is
    accepted the assessment is ``reviewed`` and (if a cadence is set) a re-review
    ``due_at`` is scheduled."""
    if lens not in LENSES:
        raise ValueError(f"unknown lens: {lens!r}")
    if decision not in ("accepted", "needs_work"):
        raise ValueError("decision must be 'accepted' or 'needs_work'")
    now = time.time() if now is None else now
    data = _load_all()
    a = data.get(_key(kind, name))
    if a is None:
        return None
    lv = (a.get("lenses") or {}).get(lens)
    if lv is None:
        return None
    lv["status"] = "accepted" if decision == "accepted" else "open"
    lv["reviewer"] = reviewer
    lv["reviewed_at"] = now
    lv["note"] = note
    if decision == "accepted":
        for f in lv.get("findings", []):
            if f.get("status") == "open":
                f["status"] = "accepted"
    if cadence_days is not None:
        a["cadence_days"] = max(0, int(cadence_days))
    a["updated_at"] = now
    a.pop("drift_note", None)
    all_accepted = all(
        (a["lenses"].get(x) or {}).get("status") == "accepted" for x in a["lenses"])
    if all_accepted:
        a["status"] = "reviewed"
        a["reviewed_at"] = now
        a["due_at"] = (now + a["cadence_days"] * 86400) if a.get("cadence_days") else None
    else:
        a["status"] = "in_review"
    _save_all(data)
    return _summarize(a)


def add_evidence(kind: str, name: str, lens: str, control: str, evidence: str,
                 now: float | None = None) -> dict | None:
    """Attach an evidence note/link to a specific finding."""
    now = time.time() if now is None else now
    data = _load_all()
    a = data.get(_key(kind, name))
    if a is None:
        return None
    for f in (a.get("lenses") or {}).get(lens, {}).get("findings", []):
        if f.get("control") == control:
            f.setdefault("evidence", []).append(str(evidence))
            a["updated_at"] = now
            _save_all(data)
            return _summarize(a)
    return None


def due_assessments(now: float | None = None) -> list[dict]:
    """Assessments needing attention: past their re-review ``due_at``, or flipped
    to ``needs_review`` by drift, or never reviewed past a draft."""
    now = time.time() if now is None else now
    out = []
    for a in _load_all().values():
        overdue = bool(a.get("due_at")) and a["due_at"] <= now
        if overdue or a.get("status") in ("needs_review", "draft"):
            out.append(_summarize(a))
    out.sort(key=lambda a: (a.get("due_at") or 0))
    return out


def export_rows() -> list[dict]:
    """One flat row per finding, for CSV/JSON audit export."""
    rows = []
    for a in _load_all().values():
        for lens, lv in (a.get("lenses") or {}).items():
            for f in lv.get("findings", []):
                rows.append({
                    "kind": a.get("kind"), "subject": a.get("subject"),
                    "assessment_status": a.get("status"), "lens": lens,
                    "lens_status": lv.get("status"), "control": f.get("control"),
                    "severity": f.get("severity"), "finding_status": f.get("status"),
                    "note": f.get("note"), "mitigation": f.get("mitigation"),
                    "evidence": "; ".join(f.get("evidence") or []),
                    "reviewer": lv.get("reviewer"), "reviewed_at": lv.get("reviewed_at"),
                })
    return rows
