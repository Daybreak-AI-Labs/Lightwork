"""Customer-facing capability boundaries; enabling a feature is not acceptance."""
from __future__ import annotations

CATALOG = (
    {"name": "Reviewed task delivery", "stage": "available", "feature": "guided_onboarding", "href": "/start", "permission": "operate",
     "scope": "Guidance from setup through a current human-approved deliverable.",
     "limit": "Specialist availability and a completed task do not establish customer success. Provider setup and a real reviewed result are required."},
    {"name": "Action Center", "stage": "available", "feature": "action_center", "href": "/action-center", "permission": "operate",
     "scope": "Your permitted approvals, sign-offs, assessments and assigned project reviews, with private read and snooze choices.",
     "limit": "Unavailable and bounded sources are disclosed. Snoozing does not pause work or extend a deadline."},
    {"name": "Editor recovery", "stage": "available", "feature": "draft_recovery", "href": "/workflow-builder", "permission": "operate",
     "scope": "Encrypted account/workspace recovery for supported agent, workflow and guided editors.",
     "limit": "Credentials and attachments are excluded. Restoring a draft never runs or publishes it. It is not a full workspace backup."},
    {"name": "Business record search", "stage": "available", "feature": "record_search", "href": None, "permission": "view",
     "scope": "Use the page finder to search permitted record names, references and version metadata.",
     "limit": "Document bodies are excluded. Coverage, source failures, pagination and changed search snapshots are explicit."},
    {"name": "Vendor evidence and renewal", "stage": "live_validation", "feature": None, "href": "/third-party/vendor-onboarding", "permission": "operate", "suite": "privacy",
     "scope": "Multiple supported documents, native Google Docs export, PDF page citations, missing/conflicting facts, reviewed Jira proposals and evidence renewal differences.",
     "limit": "Parsing is bounded. Samples are synthetic. Actual Drive/Jira access, approved writes, external confirmation and customer acceptance need live evidence."},
    {"name": "Privacy discovery setup", "stage": "live_validation", "feature": None, "href": "/privacy/data-discovery", "permission": "operate", "suite": "privacy",
     "scope": "Saved connection selection, exact credential version binding, evidence-key setup/rotation and explicit source access checks.",
     "limit": "A saved connection or public service readiness check does not prove access to a private source. Provider scopes and actual source access are required."},
    {"name": "Consent publication", "stage": "live_validation", "feature": None, "href": "/consent/publishing", "permission": "operate", "suite": "privacy",
     "scope": "Choose saved property/content versions, inspect consent changes and retain corrected form inputs.",
     "limit": "Publication still requires its governed review, current consent state and connected delivery configuration. Local tests do not prove a live property deployment."},
    {"name": "External delivery reconciliation", "stage": "live_validation", "feature": None, "href": "/security/governance", "permission": "admin", "suite": "security",
     "scope": "Compare a held GRC delivery against its attempted payload and target, then record a fresh verified match or keep it held with a note.",
     "limit": "Comparison is read-only. A timeout never justifies an automatic resend. A real provider match remains deployment-specific."},
    {"name": "Project team review", "stage": "available", "feature": "project_reviews", "href": "/project-reviews", "permission": "view",
     "scope": "Owners share selected fixed task results with named workspace reviewers and retain comments, decisions, due dates and revocable membership.",
     "limit": "Requires named accounts and a selected workspace. Packet review does not execute, edit or sign off the live task."},
    {"name": "Local workspace recovery", "stage": "bounded", "feature": "workspace_recovery", "href": "/workspace/recovery", "permission": "admin",
     "scope": "Signed snapshots, key custody, archive inspection, temporary restore drills and reviewed restore in isolated local recovery mode.",
     "limit": "Live restore refuses changed accounting or database files until administrator reconciliation. PostgreSQL, independent processes, external stores and shared KMS need separate recovery procedures. A small synthetic drill is not production recovery acceptance."},
    {"name": "Workflow pilot measurement", "stage": "preview", "feature": "outcome_measurement", "href": "/outcome-pilots", "permission": "operate",
     "scope": "Frozen case plans, attributed baseline/effort/correction/cost observations and current accepted/rejected outcomes, with all planned cases retained.",
     "limit": "Provider invoice authenticity and model-training isolation are not established by this page. Observed customer baselines and a controlled held-out pilot remain required."},
    {"name": "Runtime accounting receipts", "stage": "bounded", "feature": None, "href": "/billing", "permission": "view",
     "scope": "Durable local usage receipts and safe retry accounting for SQLite checkpointed runs, with a private Billing trail.",
     "limit": "Internal quota accounting is not a provider invoice. Legacy ambiguous usage and spend never persisted before process loss require external reconciliation."},
    {"name": "Windows customer application", "stage": "release_candidate", "feature": None, "href": None, "permission": "view",
     "scope": "Complete packaged runtime and desktop update/repair/recovery work are being validated for a customer installer.",
     "limit": "Recognized publisher signing, the final native build, clean-machine acceptance and completed-upgrade rollback acceptance are still release requirements."},
    {"name": "Learning and improvement", "stage": "experimental", "feature": None, "href": "/oversight", "permission": "view",
     "scope": "Governed learning, review and rollback machinery with local and synthetic evaluation evidence.",
     "limit": "A passing synthetic benchmark does not establish improvement on held-out customer work. Keep evaluation contamination, budget caps and promotion review in scope."},
    {"name": "Watch and spatial companion surfaces", "stage": "prototype", "feature": None, "href": None, "permission": "view",
     "scope": "Repository scaffolds for selected companion views.",
     "limit": "These require platform toolchains, device builds and on-device usability validation. They are not accepted customer releases."},
)

LABELS = {"available": "Available in app", "live_validation": "Live validation required",
    "bounded": "Available within stated limits", "preview": "Preview", "release_candidate": "Release candidate",
    "experimental": "Experimental", "prototype": "Prototype"}


def for_request(request):
    from maverick import config

    from .auth import has_permission, suite_allowed

    features = config.get_features()
    rows = []
    for record in CATALOG:
        if not suite_allowed(request, record.get("suite")):
            continue
        row = {**record, "stage_label": LABELS[record["stage"]]}
        row["configuration"] = "Enabled" if record["feature"] and features.get(record["feature"]) is True else (
            "Disabled in Settings" if record["feature"] else "Module-specific setup")
        row["can_open"] = bool(record["href"] and has_permission(request, record["permission"])
                               and (not record["feature"] or features.get(record["feature"]) is True))
        rows.append(row)
    return rows
