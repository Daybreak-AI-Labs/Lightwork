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
     "scope": "Signed snapshots, archive comparisons, saved restore-test evidence, retained-key decryption checks and reviewed restore in isolated local recovery mode.",
     "limit": "Live restore refuses changed spending, protected action history or databases until reconciliation. Temporary tests cover supported local formats; external stores, shared key services and production recovery need separate verification."},
    {"name": "Workflow pilot measurement", "stage": "preview", "feature": "outcome_measurement", "href": "/outcome-pilots", "permission": "operate",
     "scope": "Frozen case plans, attributed baseline/effort/correction/cost observations and current accepted/rejected outcomes, with all planned cases retained.",
     "limit": "Provider invoice authenticity and model-training isolation are not established by this page. Observed customer baselines and a controlled held-out pilot remain required."},
    {"name": "Runtime accounting receipts", "stage": "bounded", "feature": None, "href": "/billing", "permission": "view",
     "scope": "Durable local usage receipts and safe retry accounting for SQLite checkpointed runs, with a private Billing trail.",
     "limit": "Internal quota accounting is not a provider invoice. Legacy ambiguous usage and spend never persisted before process loss require external reconciliation."},
    {"name": "Windows customer application", "stage": "release_candidate", "feature": None, "href": None, "permission": "view",
     "scope": "Packaged runtime with in-app setup, update, repair and guarded recovery, built and inspected as a local installer candidate.",
     "limit": "Recognized publisher signing and clean-machine installation, repair, upgrade and rollback acceptance remain release requirements."},
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


def supplier_setup_readiness(state):
    """Configuration checklist from the caller's sanitized setup snapshot."""
    deployment = state["deployment"]
    actions = []
    if not deployment["supported"]:
        actions.append({"id": "deployment", "label": deployment["next_action"], "href": "#vs-deployment"})
    else:
        if not state["modules_enabled"] or not state["connections_enabled"]:
            actions.append({"id": "enable", "label": "Ask a workspace administrator to enable the vendor workflow below.", "href": "#vs-enable"})
        if not state["binding"].get("resource_owner"):
            actions.append({"id": "owner", "label": "Choose an active workspace member to own vendor reviews.", "href": "#vs-resource-owner"})
        ticket = state["binding"].get("ticket_connection")
        usable = [row for row in state["connections"] if row["connector"] == "jira" and row["has_token"]]
        if not usable:
            actions.append({"id": "jira", "label": "Connect a Jira account with access to the project where approved tasks will be created.", "href": "/connections"})
        elif not any(row["name"] == ticket for row in usable):
            actions.append({"id": "jira", "label": "Select your Jira connection for task delivery.", "href": "#vs-ticket"})
        if not state["revision"]:
            actions.append({"id": "save", "label": "Review the owner and connection choices, confirm, then save workspace setup.", "href": "#vs-confirm"})
    return {"state": "blocked" if not deployment["supported"] else "setup_required" if actions else "configured",
            "configuration_complete": not actions, "actions": actions, "deployment": deployment,
            "live_provider_verified": False}


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
        if record["href"] == "/third-party/vendor-onboarding":
            from maverick.vendor_onboarding import deployment_readiness

            profile = deployment_readiness()
            row["configuration"] = "Open workflow to review owner and Jira setup" if profile["supported"] else profile["message"]
        row["can_open"] = bool(record["href"] and has_permission(request, record["permission"])
                               and (not record["feature"] or features.get(record["feature"]) is True))
        rows.append(row)
    return rows
