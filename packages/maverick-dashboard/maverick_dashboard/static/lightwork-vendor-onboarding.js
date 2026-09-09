/* Tenant-private onboarding; responses are rendered as text, never trusted HTML. */
(() => {
  "use strict";
  const root = document.getElementById("vendor-onboarding");
  if (!root) return;
  const byId = id => document.getElementById(`vo-${id}`);
  const base = root.dataset.apiBase;
  let selected = null;
  let approvalCardCurrent = false;
  let reviewedAt = null;
  let busy = false;
  let queueCursor = "", queueNext = null, queueHistory = [], queueView = "all";
  let pendingIntake = null;
  let uploadReview = null, uploadGeneration = 0, uploadDenied = false;
  let exportReview = null, exportDenied = false;
  const connectionTaskKey = "lightwork:connection-task:v1";
  const intakeTaskKey = `lightwork:vendor-intake:v1:${root.dataset.workspaceContext || "local"}`;
  let intakeStorage = null;
  try { intakeStorage = window.localStorage; } catch (_) {}
  root.addEventListener("click", event => {
    const link = event.target.closest?.("a");
    if (link?.getAttribute("href") !== "/connections" || !selected) return;
    if (!/^[a-f0-9]{64}$/.test(root.dataset.workspaceContext) || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(selected.id)) {
      event.preventDefault(); message("Reload this request before opening Connections.", true); return;
    }
    try { window.sessionStorage.setItem(connectionTaskKey, JSON.stringify({context:root.dataset.workspaceContext, case_id:selected.id, created_at:Date.now()})); }
    catch (_) { event.preventDefault(); message("This browser cannot retain the return to your request. Allow session storage, then open Connections again.", true); }
  });
  const show = (id, value) => { byId(id).textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2); };
  const message = (text, error = false) => {
    byId("message").setAttribute("role", error ? "alert" : "status");
    show("message", text);
  };
  const date = value => value ? new Date(value * 1000).toLocaleString() : "Not recorded";
  const dateInput = value => value ? new Date(value * 1000).toISOString().slice(0, 10) : "";
  function dueTimestamp(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) throw new Error("Choose a requested completion date.");
    const result = Date.parse(`${value}T12:00:00Z`) / 1000;
    if (!Number.isFinite(result) || result <= 0) throw new Error("Choose a valid requested completion date.");
    return result;
  }
  const friendly = value => String(value || "pending").replaceAll("_", " ");
  const cardEntries = value => Object.entries(value || {}).map(([key, item]) => `${friendly(key)}: ${String(item)}`).join("\n");
  function approvalCardText(operation) {
    const card = operation?.approval_card;
    if (!card) return "Approval card unavailable. Prepare a fresh operation before approval.";
    const consequence = card.consequences;
    if (card.schema !== "lightwork.approval-card.v1" || !digestValue(card.binding_sha256) ||
        !card.authorization || card.authorization.state !== "recommendation_only" ||
        card.authorization.authority_required !== true || card.authorization.prediction_grants_authority !== false ||
        !card.target || typeof card.target.label !== "string" || !digestValue(card.target.sha256) ||
        !card.parameters || !digestValue(card.parameters.sha256) || !Array.isArray(card.parameters.summary) ||
        !card.authority || typeof card.authority.actor !== "string" || typeof card.authority.workspace !== "string" ||
        typeof card.authority.role !== "string" || !Number.isFinite(card.expires_at) ||
        !consequence || typeof consequence.irreversible !== "boolean" ||
        !Array.isArray(consequence.known_effects) || !consequence.known_effects.length ||
        !Array.isArray(consequence.unresolved_assumptions) || !card.preview ||
        typeof card.preview.before !== "string" || typeof card.preview.after !== "string")
      throw new Error("The approval card is incomplete. Prepare and review a fresh operation.");
    return [
      "RECOMMENDATION ONLY — prediction does not grant authority.",
      `Actor: ${card.authority.actor}`,
      `Workspace: ${card.authority.workspace}`,
      `Role: ${card.authority.role}`,
      `Target: ${card.target.label}`,
      `Target digest: ${card.target.sha256}`,
      `Exact input digest: ${card.parameters.sha256}`,
      `Parameters:\n${card.parameters.summary.map(item => `• ${item}`).join("\n")}`,
      `Input versions:\n${cardEntries(card.inputs) || "None recorded"}`,
      `Credential versions:\n${cardEntries(card.credentials) || "None recorded"}`,
      `Budget:\n${cardEntries(card.budget) || "Unavailable"}`,
      `Approval expires: ${date(card.expires_at)}`,
      `Before: ${card.preview.before}`,
      `After: ${card.preview.after}`,
      `${consequence.irreversible ? "IRREVERSIBLE OR EXTERNALLY PERSISTENT" : "REVERSIBLE"} KNOWN EFFECTS:\n${consequence.known_effects.map(item => `• ${item}`).join("\n")}`,
      `UNRESOLVED ASSUMPTIONS:\n${consequence.unresolved_assumptions.length ? consequence.unresolved_assumptions.map(item => `• ${item}`).join("\n") : "None recorded"}`,
    ].join("\n\n");
  }
  function element(tag, text, parent) {
    const node = document.createElement(tag); node.textContent = text; parent.append(node); return node;
  }
  function buttons() {
    const row = selected || {};
    const op = row.operation || {};
    const cancelled = Boolean(row.cancellation) || row.stage === "cancelled";
    const editable = Boolean(selected) && !cancelled && !row.outcome &&
      row.stage !== "acceptance_reevaluation_required" &&
      !["executing", "reconciliation_required", "verified"].includes(op.state);
    const exportable = Boolean(selected) && Boolean(row.assessment) && Boolean(row.review_history?.length);
    const allowed = {collect: editable, assess: editable && row.evidence?.status === "available",
      renew: row.outcome?.accepted === true || row.stage === "acceptance_reevaluation_required",
      "check-renewal": row.outcome?.accepted === true,
      "review-approve": editable && Boolean(row.assessment),
      "review-reject": editable && Boolean(row.assessment), prepare: editable && row.assessment?.status === "approved",
      approve: editable && op.state === "prepared" && approvalCardCurrent,
      execute: !cancelled && op.state === "approved",
      reconcile: (["executing", "reconciliation_required"].includes(op.state) &&
        row.reconciliation?.safe_next_check?.kind !== "restore_access") || (cancelled && op.state === "verified"),
      cancel: Boolean(selected) && !cancelled && !row.outcome,
      "save-draft": editable,
      accept: !cancelled && op.state === "verified" && !row.outcome && row.stage !== "acceptance_reevaluation_required",
      reject: !cancelled && op.state === "verified" && !row.outcome};
    for (const [name, enabled] of Object.entries(allowed)) byId(name).disabled = busy || !enabled;
    byId("intake").querySelectorAll("input,textarea,select,button").forEach(control => { control.disabled = busy; });
    byId("refresh").disabled = busy;
    if (byId("case-filter")) byId("case-filter").disabled = busy;
    if (byId("page-previous")) byId("page-previous").disabled = busy || queueHistory.length === 0;
    if (byId("page-next")) byId("page-next").disabled = busy || queueNext === null;
    byId("cases").querySelectorAll("button").forEach(button => {
      button.disabled = busy;
      button.setAttribute("aria-current", String(button.dataset.caseId === selected?.id));
    });
    byId("corrections").querySelectorAll("select,textarea").forEach(control => { control.disabled = busy || !allowed["review-approve"]; });
    byId("rationale").disabled = busy || !allowed["review-approve"];
    byId("acceptance-reason").disabled = busy || (!allowed.accept && !allowed.reject);
    byId("cancellation-reason").disabled = busy || !allowed.cancel;
    byId("cancellation-confirm").disabled = busy || !allowed.cancel;
    for (const id of ["case-project", "case-purpose", "case-freshness", "case-due"])
      if (byId(id)) byId(id).disabled = busy || !editable;
    byId("reconcile").textContent = cancelled && op.state === "verified" ? "Recheck retained ticket" : "Check uncertain ticket result";
    byId("evidence-controls")?.querySelectorAll("input,textarea,button").forEach(control => { control.disabled = busy || !editable; });
    for (const id of ["upload", "upload-submit", "document-date", "upload-transcription", "upload-transcribe", "upload-confirm"])
      if (byId(id)) byId(id).disabled = busy || !editable || uploadDenied;
    if (byId("upload-accept")) byId("upload-accept").disabled = busy || uploadDenied || !editable ||
      uploadReview?.preview.status !== "available" || !byId("upload-confirm").checked;
    if (byId("export-preview")) byId("export-preview").disabled = busy || exportDenied || !exportable;
    if (byId("export-confirm")) byId("export-confirm").disabled = busy || exportDenied || !exportReview;
    if (byId("export-download")) byId("export-download").disabled = busy || exportDenied || !exportReview ||
      !byId("export-confirm").checked;
  }
  const evidenceDocuments = record => record.evidence?.documents || (record.evidence ? [record.evidence] : []);
  const exportReference = value => typeof value === "string" && /^EVR-[0-9a-f]{32}$/.test(value);
  const digestValue = value => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
  function clearExportReview(messageText = "Review a case assessment before preparing an export.") {
    exportReview = null;
    if (byId("export-confirm")) byId("export-confirm").checked = false;
    if (byId("export-manifest")) byId("export-manifest").replaceChildren();
    if (byId("export-status")) show("export-status", messageText);
  }
  function validatedExport(value, caseId, revision) {
    const fail = () => { throw new Error("The evidence export response was incomplete. Preview it again before downloading."); };
    if (!value || typeof value !== "object" || Array.isArray(value) ||
        value.schema !== "lightwork.vendor-onboarding.evidence-export.v1" ||
        !Number.isFinite(value.prepared_at) || !digestValue(value.binding_sha256)) fail();
    const context = value.case;
    if (!context || typeof context !== "object" || Array.isArray(context) ||
        context.id !== caseId || context.revision !== revision ||
        context.workflow_schema !== "lightwork.vendor-onboarding.v1" ||
        !digestValue(context.evidence_sha256) || typeof context.assessment_id !== "string" ||
        context.assessment_id.length > 64 || !Number.isInteger(context.assessment_revision) ||
        context.assessment_revision < 1 || !digestValue(context.assessment_sha256)) fail();
    const manifest = value.manifest;
    if (!manifest || manifest.schema !== "lightwork.vendor-onboarding.evidence-export-manifest.v1" ||
        !Array.isArray(manifest.included) || manifest.included.length > 8 ||
        !Array.isArray(manifest.withheld) || manifest.withheld.length > 8 ||
        manifest.included_count !== manifest.included.length ||
        manifest.withheld_count !== manifest.withheld.length ||
        manifest.complete !== (manifest.withheld.length === 0)) fail();
    const included = new Set(), withheld = new Set();
    for (const item of manifest.included) {
      if (!item || !exportReference(item.reference) || included.has(item.reference) ||
          item.disposition !== "included" ||
          !["retained_upload", "connected_source_evidence"].includes(item.dependency_type) ||
          !["customer_upload", "connected_document"].includes(item.source_type) ||
          typeof item.reviewed_version !== "string" || item.reviewed_version.length > 256 ||
          !Number.isFinite(item.modified_at) || !Number.isFinite(item.fresh_until) ||
          !["current", "expired"].includes(item.freshness) || !digestValue(item.content_sha256) ||
          typeof item.extraction_method !== "string" || item.extraction_method.length > 128) fail();
      included.add(item.reference);
    }
    for (const item of manifest.withheld) {
      if (!item || !exportReference(item.reference) || included.has(item.reference) || withheld.has(item.reference) ||
          item.disposition !== "withheld" || item.dependency_type !== "external_evidence" ||
          !["current_source_access_unavailable", "current_source_missing", "reviewed_source_changed",
            "current_source_unavailable", "review_authority_changed"].includes(item.reason)) fail();
      withheld.add(item.reference);
    }
    if (!Array.isArray(value.claims) || value.claims.length > 8) fail();
    const claimIds = new Set();
    for (const claim of value.claims) {
      if (!claim || typeof claim.claim_id !== "string" ||
          !/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(claim.claim_id) || claimIds.has(claim.claim_id) ||
          !["yes", "no", "na", "unknown"].includes(claim.answer) ||
          typeof claim.note !== "string" || claim.note.length > 1000 ||
          !["document", "manual", "vendor_attested", "prior_approved", "missing", "unknown"].includes(claim.source_basis) ||
          !Array.isArray(claim.evidence_refs) || claim.evidence_refs.length > 8 ||
          claim.evidence_refs.some(reference => !included.has(reference)) ||
          !Array.isArray(claim.withheld_evidence_refs) || claim.withheld_evidence_refs.length > 8 ||
          claim.withheld_evidence_refs.some(reference => !withheld.has(reference)) ||
          !Array.isArray(claim.citations) || claim.citations.length > 3 ||
          !(claim.review_justification === null ||
            (typeof claim.review_justification === "string" && claim.review_justification.length <= 4000))) fail();
      for (const citation of claim.citations) {
        if (!citation || !included.has(citation.evidence_ref) || typeof citation.quote !== "string" ||
            citation.quote.length > 320 || !citation.location || typeof citation.location !== "object" ||
            Array.isArray(citation.location)) fail();
      }
      claimIds.add(claim.claim_id);
    }
    if (!value.decision || !["accepted", "rejected", "reviewed"].includes(value.decision.state) ||
        typeof value.decision.reviewer !== "string" || value.decision.reviewer.length > 256 ||
        typeof value.decision.rationale !== "string" || value.decision.rationale.length > 4000 ||
        !Number.isFinite(value.decision.decided_at) || !Array.isArray(value.limitations) ||
        value.limitations.length > 10 || value.limitations.some(item => typeof item !== "string" || item.length > 1000) ||
        (value.decision.state === "accepted" && value.claims.some(claim =>
          !claim.evidence_refs.length && !claim.review_justification))) fail();
    return value;
  }
  function showExport(value) {
    byId("export-manifest").replaceChildren();
    for (const item of value.manifest.included)
      element("li", `${item.reference}: Included ${friendly(item.source_type)}, reviewed version ${item.reviewed_version || "redacted"}, ${item.freshness}.`, byId("export-manifest"));
    for (const item of value.manifest.withheld)
      element("li", `${item.reference}: Withheld external evidence (${friendly(item.reason)}).`, byId("export-manifest"));
    show("export-status", `${value.claims.length} reviewed claim${value.claims.length === 1 ? "" : "s"}; ${value.manifest.included_count} evidence source${value.manifest.included_count === 1 ? "" : "s"} included; ${value.manifest.withheld_count} withheld. Full files and extracted text are omitted.`);
  }
  function validatedAcceptanceCheck(value) {
    if (value === null || value === undefined) return null;
    if (!value || typeof value !== "object" || Array.isArray(value) ||
        value.schema !== "lightwork.vendor-onboarding.acceptance-evidence-check.v1" ||
        !["current", "reevaluation_required"].includes(value.status) ||
        !Number.isFinite(value.checked_at) || !digestValue(value.reviewed_evidence_sha256) ||
        typeof value.reviewed_assessment_id !== "string" || value.reviewed_assessment_id.length > 64 ||
        !Array.isArray(value.documents) || !value.documents.length || value.documents.length > 8)
      throw new Error("The acceptance evidence check was incomplete. Reload this request before deciding.");
    for (const item of value.documents) {
      if (!item || !exportReference(item.reference) || !["gdrive", "upload"].includes(item.source) ||
          typeof item.document_id !== "string" || !item.document_id || item.document_id.length > 256 ||
          typeof item.reviewed_version !== "string" || item.reviewed_version.length > 256 ||
          typeof item.current_version !== "string" || item.current_version.length > 256 ||
          !["current", "changed", "expired", "missing", "access_unavailable", "unavailable", "authority_changed"].includes(item.status))
        throw new Error("The acceptance evidence check was incomplete. Reload this request before deciding.");
    }
    return value;
  }
  function validatedReconciliation(value, businessOwner) {
    const fail = () => { throw new Error("The uncertain action status was incomplete. Reload this request before checking Jira."); };
    const closed = (item, keys) => {
      if (!item || typeof item !== "object" || Array.isArray(item) ||
          Object.keys(item).length !== keys.length ||
          !keys.every(key=>Object.prototype.hasOwnProperty.call(item,key))) fail();
      return item;
    };
    const text = (item, limit=2000) => typeof item === "string" && item.length > 0 &&
      item.length <= limit && item.trim() === item;
    const time = (item, optional=false) => optional && item === null ||
      typeof item === "number" && Number.isFinite(item) && item > 0;
    closed(value,["schema","attempted_action","known_observations","uncertainty",
      "safe_next_check","responsible_operator","resolution"]);
    if (value.schema !== "lightwork.vendor-onboarding.reconciliation-summary.v1") fail();
    const attempted = closed(value.attempted_action,["operation_id","action","attempt_count","started_at","target"]);
    const target = closed(attempted.target,["provider","project"]);
    if (!/^VOP-[a-f0-9]{32}$/.test(attempted.operation_id) || attempted.action !== "jira.create" ||
        !Number.isInteger(attempted.attempt_count) || attempted.attempt_count < 1 || !time(attempted.started_at) ||
        target.provider !== "jira" || !/^[A-Z][A-Z0-9_]{1,31}$/.test(target.project)) fail();
    if (!Array.isArray(value.known_observations) || value.known_observations.length !== 3) fail();
    const observations = new Map();
    for (const observation of value.known_observations) {
      if (!observation || typeof observation !== "object" || Array.isArray(observation) ||
          observations.has(observation.kind)) fail();
      if (observation.kind === "execution_attempt") {
        closed(observation,["kind","state","count","observed_at"]);
        if (observation.state !== "recorded" || observation.count !== attempted.attempt_count ||
            observation.observed_at !== attempted.started_at) fail();
      } else if (observation.kind === "provider_receipt") {
        closed(observation,["kind","state"]);
        if (!["recorded","not_recorded"].includes(observation.state)) fail();
      } else if (observation.kind === "authoritative_readback") {
        const keys = Object.keys(observation);
        if (!keys.every(key=>["kind","state","observed_at","match_count","match_count_lower_bound"].includes(key)) ||
            !["kind","state","observed_at"].every(key=>keys.includes(key)) ||
            !["not_completed","access_unavailable","no_match","multiple_matches","readback_mismatch",
              "readback_unavailable","exact_match_verified"].includes(observation.state) ||
            !time(observation.observed_at,true)) fail();
        const exact = observation.match_count, lower = observation.match_count_lower_bound;
        if (exact !== undefined && (!Number.isInteger(exact) || exact < 0) ||
            lower !== undefined && (!Number.isInteger(lower) || lower < 2) ||
            exact !== undefined && lower !== undefined ||
            observation.state === "no_match" && exact !== 0 ||
            observation.state === "multiple_matches" && !((Number.isInteger(exact) && exact >= 2) ||
              (Number.isInteger(lower) && lower >= 2)) ||
            observation.state === "not_completed" && (observation.observed_at !== null || exact !== undefined || lower !== undefined) ||
            observation.state !== "not_completed" && observation.observed_at === null ||
            ["exact_match_verified","readback_mismatch"].includes(observation.state) && exact !== 1) fail();
      } else fail();
      observations.set(observation.kind,observation);
    }
    if (!["execution_attempt","provider_receipt","authoritative_readback"].every(kind=>observations.has(kind))) fail();
    const uncertainty = closed(value.uncertainty,["state","reason","explanation"]);
    const reasons = {
      current_connection_access_unavailable:["restore_access",null],
      no_matching_effect_observed:["check_status","no_match"],
      multiple_matching_effects_observed:["review_ambiguity","multiple_matches"],
      matching_effect_content_mismatch:["review_ambiguity","readback_mismatch"],
      authoritative_readback_unavailable:["check_status","readback_unavailable"],
      receipt_recorded_readback_unverified:["check_status","not_completed"],
      attempt_result_unrecorded:["check_status","not_completed"],
    };
    if (uncertainty.state !== "unresolved" || !Object.prototype.hasOwnProperty.call(reasons,uncertainty.reason) ||
        !text(uncertainty.explanation)) fail();
    const next = value.safe_next_check, expected = reasons[uncertainty.reason];
    closed(next,expected[0] === "restore_access" ? ["kind","label","mode","href"] : ["kind","label","mode"]);
    if (next.kind !== expected[0] || next.mode !== "read_only" || !text(next.label,500) ||
        next.kind === "restore_access" && next.href !== "/connections" ||
        expected[1] !== null && observations.get("authoritative_readback").state !== expected[1] ||
        uncertainty.reason === "receipt_recorded_readback_unverified" && observations.get("provider_receipt").state !== "recorded" ||
        uncertainty.reason === "attempt_result_unrecorded" && observations.get("provider_receipt").state !== "not_recorded") fail();
    const responsible = closed(value.responsible_operator,["principal","business_owner"]);
    if (!text(responsible.principal,256) || !text(responsible.business_owner,256) ||
        responsible.business_owner !== businessOwner) fail();
    const resolution = closed(value.resolution,["automatic_compensation_supported","unreviewed_replacement_allowed",
      "requires_separate_review","explanation"]);
    if (resolution.automatic_compensation_supported !== false || resolution.unreviewed_replacement_allowed !== false ||
        resolution.requires_separate_review !== true || !text(resolution.explanation)) fail();
    return value;
  }
  const documentIds = value => String(value || "").split(/\r?\n/).map(id => id.trim()).filter(Boolean);
  const validSubmissionKey = value => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9_.:-]{15,127}$/.test(value);
  function newSubmissionKey() {
    if (typeof window.crypto?.randomUUID === "function") return window.crypto.randomUUID();
    const random = () => Math.random().toString(36).slice(2).padEnd(12, "0");
    return `${Date.now().toString(36)}-${random()}-${random()}`;
  }
  function submissionKey() {
    let stored = pendingIntake;
    try { stored = JSON.parse(intakeStorage?.getItem(intakeTaskKey)) || stored; } catch (_) {}
    if (stored && stored.context === root.dataset.workspaceContext &&
        validSubmissionKey(stored.submission_key) && Number.isFinite(stored.created_at) &&
        stored.created_at <= Date.now() + 300000 && Date.now() - stored.created_at < 86400000) {
      pendingIntake = stored;
      return stored.submission_key;
    }
    if (!intakeStorage) throw new Error("Durable browser storage is unavailable. Enable local storage before creating a request so a lost response cannot create a duplicate.");
    pendingIntake = {context: root.dataset.workspaceContext,
      submission_key: newSubmissionKey(), created_at: Date.now()};
    try {
      intakeStorage.setItem(intakeTaskKey, JSON.stringify(pendingIntake));
      if (JSON.parse(intakeStorage.getItem(intakeTaskKey))?.submission_key !== pendingIntake.submission_key)
        throw new Error("intake key was not retained");
    } catch (_) {
      pendingIntake = null;
      throw new Error("Durable browser storage is unavailable. Enable local storage before creating a request so a lost response cannot create a duplicate.");
    }
    return pendingIntake.submission_key;
  }
  function retireSubmission(key) {
    if (pendingIntake?.submission_key === key) pendingIntake = null;
    try {
      const stored = JSON.parse(intakeStorage?.getItem(intakeTaskKey));
      if (stored?.submission_key === key) intakeStorage.removeItem(intakeTaskKey);
    } catch (_) {}
  }

  function caseLocation(caseId) {
    const query = new URLSearchParams(window.location.search);
    query.set("case", caseId);
    const target = `${window.location.pathname || ""}?${query.toString()}${window.location.hash || ""}`;
    window.history?.replaceState(null, "", target);
    return target;
  }
  function assessmentView(record) {
    byId("assessment").replaceChildren(); byId("corrections").replaceChildren();
    const assessment = record.assessment;
    if (!assessment) { show("assessment", "Build an assessment after obtaining evidence."); return; }
    const result = assessment.result || {};
    element("p", `Risk: ${friendly(result.risk_rating)} · ${result.answered || 0} of ${result.total || 0} questions answered. Human confirmation required.`, byId("assessment"));
    for (const question of assessment.template_snapshot?.questions || []) {
      const answer = assessment.answers[question.id] || {};
      const item = document.createElement("section"); item.className = "vo-question";
      element("h4", question.text, item);
      element("p", `Current answer: ${friendly(answer.answer)}`, item);
      let citations = 0;
      for (const document of evidenceDocuments(record)) {
        const source = document.extracted?.answers?.[question.id];
        for (const citation of source?.citations || []) {
          citations++;
          element("blockquote", citation.quote, item);
          const location = citation.text_origin === "human_transcription" ? `user-supplied transcription paragraph ${citation.transcription_paragraph}; not verified PDF text` :
            citation.locations?.length ? citation.locations.map(item => item.cell ? `${item.sheet}, cell ${item.cell}` : `paragraph ${item.paragraph}`).join("; ") :
            citation.pages?.length ? `pages ${citation.pages.join(", ")}` : citation.paragraph ? `paragraph ${citation.paragraph}` : `characters ${citation.start}–${citation.end}`;
          element("small", `${document.source === "upload" ? "Uploaded" : "Drive"} source ${document.name || record.document_id}, version ${document.version || record.evidence.version}, ${location}`, item);
        }
        if (source?.answer === "unknown") element("p", source.note, item);
      }
      if (answer.note) element("p", answer.note, item);
      if (!citations) element("p", "Gap: no matching source passage. " + question.guidance, item);
      byId("assessment").append(item);
      const correction = document.createElement("div"); correction.className = "vo-question";
      const label = element("label", question.text, correction); label.htmlFor = `vo-answer-${question.id}`;
      const select = document.createElement("select"); select.id = label.htmlFor; select.dataset.question = question.id;
      for (const [value, name] of [["yes", "Yes"], ["no", "No"], ["unknown", "Unknown / needs evidence"], ["na", "Not applicable"]]) {
        const option = element("option", name, select); option.value = value;
      }
      select.value = answer.answer || "unknown"; correction.append(select);
      const noteLabel = element("label", `Review note: ${question.section}`, correction); noteLabel.htmlFor = `vo-note-${question.id}`;
      const note = document.createElement("textarea"); note.id = noteLabel.htmlFor; note.maxLength = 2000;
      note.value = answer.note || ""; correction.append(note);
      byId("corrections").append(correction);
    }
  }
  async function api(path, body) {
    const response = await fetch(base + path, {
      method: body === undefined ? "GET" : "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", ...(root.dataset.workspaceContext ? {"X-Lightwork-Vendor-Context": root.dataset.workspaceContext} : {})},
      ...(body === undefined ? {} : {body: JSON.stringify(body)})
    });
    let data;
    try { data = await response.json(); } catch (_) { throw new Error("Server response unavailable; reload the case before retrying."); }
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("Server response unavailable; reload the case before retrying.");
    if (!response.ok) {
      const detail = data.detail;
      const error = new Error(typeof detail === "string" ? detail :
        typeof detail?.message === "string" ? detail.message : "Request was rejected; check fields and reload the case.");
      if (detail && typeof detail === "object" && typeof detail.code === "string") error.code = detail.code;
      if (detail && typeof detail === "object" && typeof detail.next_action === "string" &&
          detail.next_action.length > 0 && detail.next_action.trim() === detail.next_action &&
          detail.next_action.length <= 500)
        error.nextAction = detail.next_action;
      if (detail?.current !== undefined) {
        const current = detail.current;
        const work = current?.work_item;
        const strings = [work?.lifecycle, work?.next_action, work?.technical_effect, work?.business_outcome];
        if (!current || current.schema !== "lightwork.vendor-onboarding.conflict-current.v1" ||
            typeof current.id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(current.id) ||
            !Number.isInteger(current.revision) || current.revision < 1 || typeof current.stage !== "string" ||
            typeof current.vendor_name !== "string" || current.vendor_name.length > 256 ||
            (current.updated_at !== null && current.updated_at !== undefined && !Number.isFinite(current.updated_at)) ||
            !work || typeof work !== "object" || Array.isArray(work) || strings.some(value => typeof value !== "string"))
          throw new Error("The conflict response was incomplete. Reload requests before making another change.");
        error.current = current;
      }
      throw error;
    }
    return data;
  }
  function sameJSON(left, right) {
    if (left === right) return true;
    if (!left || !right || typeof left !== "object" || typeof right !== "object" ||
        Array.isArray(left) !== Array.isArray(right)) return false;
    const keys = Object.keys(left);
    return keys.length === Object.keys(right).length && keys.every(key =>
      Object.prototype.hasOwnProperty.call(right, key) && sameJSON(left[key], right[key]));
  }
  function render(record) {
    if (typeof record.id !== "string" || !Number.isInteger(record.revision) || typeof record.vendor_name !== "string" ||
        (record.due_at !== null && record.due_at !== undefined && (!Number.isFinite(record.due_at) || record.due_at <= 0)))
      throw new Error("Case response is incomplete; refresh requests before continuing.");
    const acceptanceCheck = validatedAcceptanceCheck(record.acceptance_evidence_check);
    const operation = record.operation || {};
    const payload = operation.payload;
    let nextApprovalCardCurrent = false;
    let operationText = "No action prepared.";
    if (payload) {
      const cardText = approvalCardText(operation);
      nextApprovalCardCurrent = operation.state === "prepared";
      operationText = `${cardText}\n\nFULL REVIEWED JIRA CONTENT\nProject: ${payload.project}\nTicket title: ${payload.summary}\n\n${payload.description}\n\nVerified ticket: ${operation.external_confirmation?.key || "pending"}`;
    }
    const uncertainOperation = ["executing", "reconciliation_required"].includes(operation.state);
    const reconciliation = uncertainOperation ? validatedReconciliation(record.reconciliation, record.owner) : record.reconciliation;
    if (uncertainOperation) {
      if (reconciliation?.uncertainty?.state === "unresolved" &&
          reconciliation?.safe_next_check?.mode === "read_only" &&
          Array.isArray(reconciliation?.known_observations)) {
        const observations = reconciliation.known_observations.map(item =>
          `${friendly(item.kind)}: ${friendly(item.state)}` +
          (Number.isInteger(item.match_count) ? ` (${item.match_count} match${item.match_count === 1 ? "" : "es"})` :
            Number.isInteger(item.match_count_lower_bound) ? ` (at least ${item.match_count_lower_bound} matches)` : "")
        ).join("\n");
        const operator = reconciliation.responsible_operator?.principal ||
          reconciliation.responsible_operator?.business_owner || "an authorized operator";
        operationText += `\n\nUNCERTAIN PROVIDER RESULT\n${reconciliation.uncertainty.explanation}` +
          `\n\nKnown observations\n${observations || "No authoritative observation is recorded."}` +
          `\n\nSafe next step: ${reconciliation.safe_next_check.label}. This is a read-only check and cannot create another Jira task.` +
          (reconciliation.safe_next_check.kind === "restore_access" ?
            " Open Connections to restore access to the original Jira destination." : "") +
          `\nResponsible operator: ${operator}.` +
          `\nResolution boundary: ${reconciliation.resolution?.explanation || "Any provider change requires a separate reviewed action."}`;
      } else {
        operationText += "\n\nUNCERTAIN PROVIDER RESULT\nThe effect has not been verified. Check status is read-only and cannot create another Jira task. Any provider change requires a separate reviewed action.";
      }
    }
    const sameCase = selected?.id === record.id;
    if (!sameCase) exportDenied = false;
    if (!sameCase && byId("document-date")) byId("document-date").value = "";
    const sameReview = sameCase && selected.revision === record.revision &&
      sameJSON(selected.assessment, record.assessment);
    if (!sameReview) {
      clearUploadReview(true);
      clearExportReview();
      byId("rationale").value = "";
      byId("acceptance-reason").value = "";
      byId("cancellation-reason").value = "";
      byId("cancellation-confirm").checked = false;
      reviewedAt = performance.now();
      if (byId("case-documents")) byId("case-documents").value = (record.document_ids || [record.document_id]).filter(Boolean).join("\n");
      if (byId("case-project")) byId("case-project").value = record.project || "";
      if (byId("case-purpose")) byId("case-purpose").value = record.purpose || "";
      if (byId("case-freshness")) byId("case-freshness").value = String(record.freshness_days || "");
      if (byId("case-due")) byId("case-due").value = dateInput(record.due_at);
      if (byId("upload")) byId("upload").value = "";
    }
    selected = record;
    approvalCardCurrent = nextApprovalCardCurrent;
    show("case-title", record.vendor_name);
    const stableLocation = caseLocation(record.id);
    byId("case-reference").replaceChildren();
    element("span", `${record.renewal_of ? "Renewal" : "Initial request"} · ${record.id} · `, byId("case-reference"));
    const stableLink = element("a", "Copyable request link", byId("case-reference"));
    stableLink.href = stableLocation;
    byId("status").replaceChildren();
    const outcome = record.outcome || {};
    for (const label of ["Stage: " + friendly(record.stage), "Version: " + record.revision,
      "External action: " + friendly(operation.state || operation.status || "not prepared"),
      "Customer acceptance: " + (outcome.accepted === true ? "accepted" : outcome.accepted === false ? "rejected" : record.cancellation ? "cancelled; no decision" : "pending"),
      "Requested completion: " + date(record.due_at)]) {
      const span = document.createElement("span"); span.textContent = label; byId("status").append(span);
    }
    const work = record.work_item || {};
    const topology = friendly(work.supported_deployment?.topology || "support boundary unavailable");
    const excluded = Array.isArray(work.out_of_scope) && work.out_of_scope.length
      ? work.out_of_scope.join("; ") : "No exclusions were supplied; refresh before relying on this request.";
    show("scope", `Technical result: ${work.acceptance_contract?.technical?.effect || "Not specified"}.\n` +
      `Business completion: ${work.acceptance_contract?.business?.decision || "Not specified"}.\n` +
      `Supported deployment: ${topology}.\nNot included: ${excluded}.`);
    const evidence = record.evidence;
    show("evidence", evidence ? `Overall status: ${friendly(evidence.status)}\n` + evidenceDocuments(record).map(document => `${document.source === "upload" ? "Uploaded" : "Drive"}: ${document.name || document.document_id || record.document_id}\nStatus: ${friendly(document.status || evidence.status)} · Version: ${document.version || "unverified"}\nDocument date: ${date(document.modified_at)} (${document.source === "upload" ? "uploader asserted" : "Drive modified time"})\nValid until: ${date(document.fresh_until)}\nContent fingerprint: ${document.content_sha256 || "unavailable"}`).join("\n\n") : "No evidence obtained. Add documents, then choose Obtain current evidence.");
    if (byId("uploaded-documents")) {
      byId("uploaded-documents").replaceChildren();
      for (const uploaded of record.uploads || []) {
        const row = document.createElement("div");
        element("p", `${uploaded.name} · ${friendly(uploaded.status)} · ${date(uploaded.modified_at)}${uploaded.extraction?.method === "human_transcription" ? " · User-supplied transcription; no OCR or source-text verification" : ""}`, row);
        const remove = element("button", `Remove ${uploaded.name}`, row); remove.type = "button";
        remove.addEventListener("click", () => run(() => action("documents", {document_ids: record.document_ids || [], upload_ids: record.uploads.filter(item => item.document_id !== uploaded.document_id).map(item => item.document_id)})));
        byId("uploaded-documents").append(row);
      }
    }
    if (byId("selected-documents")) {
      byId("selected-documents").replaceChildren();
      for (const id of (record.document_ids || [record.document_id]).filter(Boolean)) {
        const row = document.createElement("div");
        element("p", `Drive document: ${id}`, row);
        const remove = element("button", `Remove Drive document ${id}`, row); remove.type = "button";
        remove.addEventListener("click", () => run(() => action("documents", {
          document_ids: (selected.document_ids || [selected.document_id]).filter(value => value && value !== id),
          upload_ids: (selected.uploads || []).map(item => item.document_id),
        })));
        byId("selected-documents").append(row);
      }
    }
    if (byId("evidence-changes")) {
      byId("evidence-changes").replaceChildren();
      const changes = evidence?.changes;
      element("p", changes?.comparison === "previous_review" ? "Compared with retained previous evidence. Review every changed claim." : "Initial evidence; no previous review to compare.", byId("evidence-changes"));
      for (const document of changes?.documents || []) {
        element("p", `${document.document_id}: ${friendly(document.change)} (${document.before_version || "none"} → ${document.after_version || "none"})`, byId("evidence-changes"));
        for (const answer of document.answer_changes || []) {
          element("p", `${friendly(answer.question_id)}: ${friendly(answer.before)} → ${friendly(answer.after)}`, byId("evidence-changes"));
          for (const [label, citations] of [["Previous", answer.before_citations], ["Current", answer.after_citations]])
            for (const citation of citations || []) element("blockquote", `${label}: ${citation.quote}`, byId("evidence-changes"));
        }
      }
    }
    if (acceptanceCheck) {
      show("acceptance-evidence-check", `Status: ${friendly(acceptanceCheck.status)} · checked ${date(acceptanceCheck.checked_at)}\n` +
        acceptanceCheck.documents.map(item => `${item.source === "upload" ? "Uploaded" : "Drive"} evidence ${item.document_id}: ${friendly(item.status)} · reviewed version ${item.reviewed_version || "redacted"}${item.current_version ? ` · current version ${item.current_version}` : ""}`).join("\n"));
    } else {
      show("acceptance-evidence-check", "Evidence is checked again against current source bytes, version, access and expiry before acceptance.");
    }
    // Keep the review controls and unsaved corrections for this exact version.
    if (!sameReview) assessmentView(record);
    show("operation", operationText);
    show("operation-record", operation);
    const cancellation = record.cancellation;
    const effectStatus = operation.state === "verified"
      ? `Ticket ${operation.external_confirmation?.key || "previously verified"} remains recorded as an external effect. Use Recheck retained ticket to check its current state.`
      : cancellation?.dispatch_state_at_cancellation === "not_dispatched"
        ? "No ticket creation was dispatched for this request."
        : "The ticket outcome is still uncertain. Use Check uncertain ticket result; no new ticket will be created.";
    show("cancellation-status", cancellation
      ? `Local work cancelled by ${cancellation.requested_by} on ${date(cancellation.requested_at)}.\nReason: ${cancellation.rationale}\n${effectStatus}\nCancellation does not delete or reverse a Jira ticket. Any change to that ticket requires a separate decision.`
      : "Cancellation stops further work on this request. If ticket creation has started, its result must still be checked. Cancellation does not delete or reverse a Jira ticket.");
    const metrics = record.metrics || {};
    const timedReviews = (record.review_history || []).filter(review => review.review_seconds !== null && review.review_seconds !== undefined);
    const reviewTime = timedReviews.length ? `${Number(metrics.human_review_seconds).toFixed(1)} seconds reported` : "Unmeasured";
    show("outcome", `Customer decision: ${outcome.accepted === true ? "Accepted" : outcome.accepted === false ? "Rejected" : cancellation ? "Cancelled request; no acceptance decision" : "Pending"}\nReason: ${outcome.rationale || "Not yet recorded"}\nNext renewal: ${date(outcome.renewal_due_at)}\nHuman review time: ${reviewTime}\nExecution cost: ${metrics.execution_cost_usd == null ? "unmeasured" : "$" + metrics.execution_cost_usd}\nPrevious case: ${record.renewal_of || "First onboarding"}\nRelease: separate approval required`);
    show("renewal-status", "Check current evidence and ticket versions before starting a renewal.");
    show("history", [...(record.review_history || []).map(review => `${date(review.at)} · ${review.reviewer}\n${friendly(review.decision)}: ${review.rationale}\n${Object.keys(review.corrections || {}).length} corrected answers; supersedes assessment ${review.supersedes?.id || "none"}.`),
      ...(record.exceptions || []).map(item => typeof item === "string" ? item : JSON.stringify(item))].join("\n\n") || "No reviewer decisions or exceptions yet.");
    buttons();
    if (sameCase && !sameReview)
      return "The request or assessment changed. Draft answers, notes, and decision reasons were reset. Review the current version before deciding.";
  }
  async function refresh() {
    const query = new URLSearchParams();
    if (queueCursor) query.set("cursor", queueCursor);
    if (queueView !== "all") query.set("view", queueView);
    const result = await api("/cases" + (query.toString() ? "?" + query : ""));
    if (!Array.isArray(result.cases) || result.cases.length > 100 ||
        (result.schema !== undefined && result.schema !== "lightwork.vendor-onboarding.case-page.v1") ||
        (result.view !== undefined && result.view !== queueView))
      throw new Error("Request list is unavailable; refresh before continuing.");
    const hasMore = result.has_more === undefined ? false : result.has_more;
    if (typeof hasMore !== "boolean" || (hasMore &&
        (typeof result.next_cursor !== "string" || !/^[A-Za-z0-9_-]{1,768}$/.test(result.next_cursor))) ||
        (!hasMore && result.next_cursor !== undefined && result.next_cursor !== null))
      throw new Error("Request pagination is unavailable. Reload requests before continuing.");
    queueNext = hasMore ? result.next_cursor : null;
    byId("cases").replaceChildren();
    for (const record of result.cases) {
      if (!record || typeof record.id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(record.id) ||
          typeof record.vendor_name !== "string" || typeof record.stage !== "string" ||
          typeof record.owner !== "string" || !record.owner || !Number.isFinite(record.updated_at))
        throw new Error("A request summary was incomplete. Reload requests before continuing.");
      const button = document.createElement("button"); button.type = "button";
      button.dataset.caseId = record.id;
      button.textContent = `${record.vendor_name} · ${record.renewal_of ? "Renewal" : "Initial request"} · ${friendly(record.stage)}` +
        ` · Owner ${record.owner} · Last progress ${date(record.updated_at)}` +
        (record.due_at ? ` · Due ${date(record.due_at)}` : "") +
        (record.blocker ? ` · Blocked: ${friendly(record.blocker)}` : "") +
        (record.next_action ? ` · Next: ${friendly(record.next_action)}` : "");
      button.addEventListener("click", () => run(async () => render(await api("/cases/" + encodeURIComponent(record.id))), "Request loaded. Review its evidence and current status."));
      byId("cases").append(button);
    }
    if (!result.cases.length) byId("cases").textContent = hasMore
      ? "No matching requests in this queue window. Continue to the next page."
      : "No matching vendor requests in this workspace.";
    if (byId("page-status")) show("page-status", `Page ${queueHistory.length + 1} · ${result.cases.length} request${result.cases.length === 1 ? "" : "s"} shown · ${friendly(queueView)} view${hasMore ? " · more available" : " · end of queue"}`);
    buttons();
  }
  async function run(operation, success = "Saved. Review the current case before the next action.", focusId) {
    if (busy) return;
    busy = true;
    root.setAttribute("aria-busy", "true");
    buttons();
    let succeeded = false;
    try { message((await operation()) || success); succeeded = true; }
    catch (error) { message(error.nextAction ? `${error.message} Next: ${error.nextAction}` : error.message, true); }
    finally {
      busy = false;
      root.setAttribute("aria-busy", "false");
      buttons();
      if (succeeded && focusId) byId(focusId).focus();
    }
  }
  async function action(name, extra = {}) {
    if (!selected) throw new Error("Select a vendor request first.");
    const caseId = selected.id;
    try {
      const updated = await api("/cases/" + encodeURIComponent(caseId) + "/" + name,
        {expected_revision: selected.revision, ...extra});
      render(updated);
      await refresh();
    } catch (error) {
      if (!["revision_conflict", "evidence_reevaluation_required"].includes(error.code) || !error.current) throw error;
      if (error.current.id !== caseId)
        throw new Error("The conflict referred to another request. Reload this workspace before continuing.");
      const current = await api("/cases/" + encodeURIComponent(caseId));
      if (current.id !== caseId || current.revision !== error.current.revision ||
          current.stage !== error.current.stage || current.vendor_name !== error.current.vendor_name)
        throw new Error("The request changed again while resolving the conflict. Reload requests before continuing.");
      render(current);
      await refresh();
      const guidance = error.code === "evidence_reevaluation_required"
        ? `${current.operation?.state === "verified" ? "The verified ticket remains recorded. " : "The stale prepared action was invalidated. "}Review the named evidence status and start a reevaluation request; the prior review cannot be reused.`
        : "Review it before submitting again.";
      const readable = new Error(`${error.message} Current version ${current.revision} (${friendly(current.stage)}) is now open. ${guidance}`);
      readable.code = error.code; readable.current = error.current;
      throw readable;
    }
  }
  const click = (id, operation, success, focusId) => byId(id).addEventListener("click", () => run(operation, success, focusId));
  click("refresh", async () => { await refresh(); if (selected) return render(await api("/cases/" + encodeURIComponent(selected.id))); }, "Requests refreshed. Review the current case before continuing.");
  byId("page-next")?.addEventListener("click", () => run(async () => {
    if (queueNext === null) throw new Error("There is no next request page.");
    const previous = queueCursor, requested = queueNext;
    queueHistory.push(previous); queueCursor = queueNext; queueNext = null;
    try { await refresh(); } catch (error) { queueCursor = queueHistory.pop(); queueNext = requested; throw error; }
  }, "Next request page loaded."));
  byId("page-previous")?.addEventListener("click", () => run(async () => {
    if (!queueHistory.length) throw new Error("There is no previous request page.");
    const previousCursor = queueCursor, previousNext = queueNext;
    queueCursor = queueHistory.pop(); queueNext = null;
    try { await refresh(); } catch (error) {
      queueHistory.push(queueCursor); queueCursor = previousCursor; queueNext = previousNext; throw error;
    }
  }, "Previous request page loaded."));
  byId("case-filter")?.addEventListener("change", () => run(async () => {
    const requested = byId("case-filter").value;
    if (!["all", "active", "unresolved", "closed"].includes(requested))
      throw new Error("Choose a supported request filter.");
    const before = {cursor: queueCursor, history: queueHistory, next: queueNext, view: queueView};
    queueCursor = ""; queueHistory = []; queueNext = null; queueView = requested;
    try { await refresh(); } catch (error) {
      queueCursor = before.cursor; queueHistory = before.history; queueNext = before.next; queueView = before.view;
      byId("case-filter").value = before.view;
      throw error;
    }
  }, "Request filter applied."));
  byId("intake").addEventListener("submit", event => {
    event.preventDefault();
    run(async () => {
      const payload = {vendor_name: byId("vendor").value.trim(),
        document_ids: documentIds(byId("document").value).sort(), project: byId("project").value.trim(),
        purpose: byId("purpose").value.trim(), freshness_days: Number(byId("freshness").value),
        due_at: dueTimestamp(byId("due").value),
        renewal_of: byId("renewal").value.trim() || null};
      const key = submissionKey();
      let record;
      try { record = await api("/cases", {...payload, submission_key: key}); }
      catch (error) {
        if (error.code !== "idempotency_conflict" || !error.current) throw error;
        const current = await api("/cases/" + encodeURIComponent(error.current.id));
        if (current.id !== error.current.id || current.revision !== error.current.revision)
          throw new Error("The existing request changed while it was opening. Reload requests before continuing.");
        render(current); await refresh(); retireSubmission(key);
        throw new Error(`This submission key already belongs to ${current.vendor_name}. Its current request is open; review it or change the form and submit again.`);
      }
      render(record); await refresh(); retireSubmission(key);
    });
  });
  byId("search-documents")?.addEventListener("click", () => run(async () => {
    const query = byId("document-search").value.trim();
    if (!query) throw new Error("Enter a document name to search Drive.");
    const result = await api("/documents?query=" + encodeURIComponent(query));
    byId("document-results").replaceChildren();
    for (const document of result.documents || []) {
      const button = element("button", document.name, byId("document-results")); button.type = "button";
      button.addEventListener("click", () => {
        if (busy) return;
        if (byId("document-target")?.value === "selected") {
          if (!selected || selected.outcome || ["executing", "reconciliation_required", "verified"].includes(selected.operation?.state)) {
            message("Select an editable request or start a renewal before adding evidence.", true); return;
          }
          const ids = (selected.document_ids || [selected.document_id]).filter(Boolean);
          if (ids.includes(document.id)) { message("This document is already selected for the request."); return; }
          ids.push(document.id);
          if (ids.length + (selected.uploads || []).length > 8) { message("Select at most eight evidence documents.", true); return; }
          run(() => action("documents", {document_ids: ids, upload_ids: (selected.uploads || []).map(item => item.document_id)}));
          return;
        }
        const ids = documentIds(byId("document").value);
        if (!ids.includes(document.id)) ids.push(document.id);
        if (ids.length > 8) { message("Select at most eight evidence documents.", true); return; }
        byId("document").value = ids.join("\n");
        if (byId("new-documents")) byId("new-documents").textContent = `${ids.length} Drive document${ids.length === 1 ? "" : "s"} selected for the new request. Most recent: ${document.name}`;
        message("Document selected for the new request.");
      });
    }
    if (!result.documents?.length) element("p", "No supported documents found.", byId("document-results"));
    if (result.has_more) element("p", "More documents match. Narrow your search.", byId("document-results"));
  }, "Drive search completed."));
  byId("save-documents")?.addEventListener("click", () => run(() => action("documents", {
    document_ids: documentIds(byId("case-documents").value), upload_ids: (selected?.uploads || []).map(item => item.document_id),
  })));
  byId("save-draft")?.addEventListener("click", () => run(() => action("draft", {
    project: byId("case-project").value.trim(),
    purpose: byId("case-purpose").value.trim(),
    freshness_days: Number(byId("case-freshness").value),
    due_at: dueTimestamp(byId("case-due").value),
  }), "Request details saved. Review the current evidence and next action."));
  function clearUploadReview(clearInput = false) {
    uploadReview = null; uploadGeneration++;
    if (!byId("upload-review")) return;
    byId("upload-review").hidden = true; byId("upload-confirm").checked = false;
    byId("upload-text").textContent = ""; byId("upload-fingerprints").textContent = "";
    byId("upload-summary").textContent = ""; byId("upload-limits").replaceChildren();
    if (clearInput) { byId("upload-transcription").value = ""; byId("upload").value = ""; }
  }
  function fileBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = () => reject(new Error("The selected file could not be read. Select it again."));
      reader.readAsDataURL(file);
    });
  }
  async function uploadRequest(path, body) {
    if (uploadDenied) throw new Error("Sign in and reload this page before uploading evidence.");
    const response = await fetch(base + path, {method:"POST", credentials:"same-origin", cache:"no-store",
      headers:{"Content-Type":"application/json",...(root.dataset.workspaceContext?{"X-Lightwork-Vendor-Context":root.dataset.workspaceContext}:{})},body:JSON.stringify(body)});
    if ([401,403].includes(response.status)) {
      clearUploadReview(true); uploadDenied = true;
      throw new Error("Access to evidence upload is unavailable. Sign in or check permissions, then reload this page.");
    }
    let result;
    try { result = await response.json(); } catch (_) { throw new Error("Upload response unavailable. Refresh the request before retrying."); }
    if (!response.ok) {
      const detail = result?.detail;
      const error = new Error(typeof detail === "string" ? detail :
        typeof detail?.message === "string" ? detail.message : "Upload rejected. Check the file and document date.");
      if (detail && typeof detail === "object" && typeof detail.code === "string") error.code = detail.code;
      throw error;
    }
    if (!result || typeof result !== "object" || Array.isArray(result)) throw new Error("The evidence response was incomplete. Preview again.");
    return result;
  }
  async function previewUpload(transcribe = false) {
    if (!selected) throw new Error("Select a request before adding evidence.");
    const file = byId("upload").files?.[0];
    const documentDate = Date.parse(byId("document-date").value + "T00:00:00Z") / 1000;
    if (!file || !Number.isFinite(documentDate)) throw new Error("Choose an evidence file and enter its document date.");
    if (Array.from(byId("upload").files).length !== 1) throw new Error("Review one evidence file at a time.");
    const extensions = {txt:["text/plain",1],pdf:["application/pdf",16],
      docx:["application/vnd.openxmlformats-officedocument.wordprocessingml.document",8],
      xlsx:["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",8]};
    const format = extensions[file.name.toLowerCase().split(".").pop()];
    if (!format) throw new Error("Choose a .txt, .pdf, .docx or .xlsx file. Macro-enabled files are not supported.");
    if (file.size > format[1] * 1024 * 1024) throw new Error("The selected file exceeds the supported size.");
    const transcription = transcribe ? byId("upload-transcription").value : null;
    if (transcribe && (!transcription.trim() || [...transcription].length > 120000)) throw new Error("Enter up to 120,000 characters of transcription and note any omissions.");
    clearUploadReview(); const generation = uploadGeneration, caseId = selected.id, revision = selected.revision;
    const body = {expected_revision:revision,name:file.name,mime:format[0],document_date:documentDate,
      content_base64:await fileBase64(file),transcription};
    if (generation !== uploadGeneration) throw new Error("The file selection changed; preview again.");
    try {
      const preview = await uploadRequest("/cases/" + encodeURIComponent(caseId) + "/upload-preview", body);
      if (generation !== uploadGeneration || selected?.id !== caseId || selected?.revision !== revision || byId("upload").files?.[0] !== file)
        throw new Error("The request or file changed; preview again.");
      if (preview.case_id !== caseId || preview.expected_revision !== revision || preview.name !== file.name.trim() || preview.mime !== format[0] ||
          typeof preview.text !== "string" || [...preview.text].length > 120000 || preview.saved !== false || preview.review_required !== true ||
          !/^[0-9a-f]{64}$/.test(preview.review_digest) || !/^[0-9a-f]{64}$/.test(preview.source_sha256) || !/^[0-9a-f]{64}$/.test(preview.text_sha256) ||
          !Number.isFinite(preview.expires_at) || !["available","empty","extraction_failed","extraction_limit"].includes(preview.status))
        throw new Error("The evidence preview was incomplete. Select the file and preview again.");
      uploadReview = {body, preview, caseId, revision, file, generation};
      byId("upload-review").hidden = false;
      show("upload-text", preview.text || "No supported text was extracted from this file.");
      const manual = preview.extraction?.method === "human_transcription";
      byId("upload-fallback").hidden = !preview.can_transcribe && !manual;
      show("upload-summary", `${preview.name}: ${manual ? "user-supplied transcription; no OCR performed" : "locally extracted text"}. Nothing has been added to the request yet.`);
      for (const limit of preview.extraction?.limits || []) element("li", String(limit), byId("upload-limits"));
      show("upload-fingerprints", `Original file SHA-256: ${preview.source_sha256}\n${manual ? "Supplied" : "Extracted"} text SHA-256: ${preview.text_sha256}\nMethod: ${friendly(preview.extraction?.method)}\nPreview expires: ${date(preview.expires_at)}`);
      return preview.can_transcribe ? "This PDF needs a readable copy or your explicit transcription. No evidence was added." : "Preview ready. Compare the text against the original, then explicitly add reviewed evidence.";
    } catch (error) { clearUploadReview(); throw error; }
  }
  byId("upload")?.addEventListener("change", () => { clearUploadReview(); byId("upload-transcription").value = ""; buttons(); });
  byId("document-date")?.addEventListener("input", () => { clearUploadReview(); buttons(); });
  byId("upload-transcription")?.addEventListener("input", () => {
    if (uploadReview?.preview.extraction?.method === "human_transcription") {
      uploadReview = null; uploadGeneration++;
      show("upload-text", "Transcription changed. Preview it again before adding evidence.");
      show("upload-fingerprints", ""); show("upload-summary", "Your edited transcription has not been reviewed or added.");
    }
    byId("upload-confirm").checked = false; buttons();
  });
  byId("upload-confirm")?.addEventListener("change", buttons);
  byId("upload-submit")?.addEventListener("click", () => run(() => previewUpload()));
  byId("upload-transcribe")?.addEventListener("click", () => run(() => previewUpload(true)));
  byId("upload-accept")?.addEventListener("click", () => run(async () => {
    const review = uploadReview;
    if (!review || !byId("upload-confirm").checked || review.preview.status !== "available") throw new Error("Review the preview and confirm comparison with your original file.");
    if (review.caseId !== selected?.id || review.revision !== selected?.revision || review.file !== byId("upload").files?.[0]) throw new Error("The request changed; preview again.");
    try {
      const result = await uploadRequest("/cases/" + encodeURIComponent(review.caseId) + "/upload-reviewed",
        {...review.body,expires_at:review.preview.expires_at,review_digest:review.preview.review_digest,confirm:true});
      if (review.generation !== uploadGeneration || selected?.id !== review.caseId || selected?.revision !== review.revision)
        throw new Error("The selection changed while saving. Reload request status before retrying.");
      render(result); await refresh();
    } catch (error) { clearUploadReview(); throw error; }
  }, "Reviewed evidence added. Obtain current evidence to rebuild the assessment."));
  click("collect", () => action("collect-evidence"));
  click("assess", () => action("assess"));
  click("prepare", () => action("prepare-action"));
  byId("export-confirm")?.addEventListener("change", buttons);
  click("export-preview", async () => {
    if (!selected) throw new Error("Select a reviewed request before preparing an evidence export.");
    const caseId = selected.id, revision = selected.revision;
    clearExportReview("Checking current access to every reviewed evidence source…");
    try {
      const result = validatedExport(await api("/cases/" + encodeURIComponent(caseId) + "/evidence-export-preview",
        {expected_revision: revision}), caseId, revision);
      if (selected?.id !== caseId || selected?.revision !== revision)
        throw new Error("The request changed while the export was prepared. Preview it again.");
      exportReview = {caseId, revision, binding: result.binding_sha256};
      showExport(result);
      return "Evidence export preview ready. Review included and withheld sources before downloading.";
    } catch (error) {
      if (error.code === "permission_denied") exportDenied = true;
      clearExportReview("The evidence export was not prepared. Review access and try again.");
      throw error;
    }
  });
  click("export-download", async () => {
    const reviewed = exportReview;
    if (!reviewed || !byId("export-confirm").checked)
      throw new Error("Preview the export and confirm its manifest before downloading.");
    if (selected?.id !== reviewed.caseId || selected?.revision !== reviewed.revision)
      throw new Error("The request changed. Preview the evidence export again.");
    try {
      const result = validatedExport(await api("/cases/" + encodeURIComponent(reviewed.caseId) + "/evidence-export", {
        expected_revision: reviewed.revision, expected_export_sha256: reviewed.binding, confirm: true,
      }), reviewed.caseId, reviewed.revision);
      if (result.binding_sha256 !== reviewed.binding)
        throw new Error("Evidence access or export content changed. Preview the export again.");
      if (typeof window.URL?.createObjectURL !== "function" || typeof Blob !== "function")
        throw new Error("This browser cannot create a local evidence download.");
      const url = window.URL.createObjectURL(new Blob([JSON.stringify(result, null, 2) + "\n"], {type:"application/json"}));
      const link = document.createElement("a");
      link.href = url; link.download = `lightwork-evidence-${reviewed.caseId}.json`; link.hidden = true;
      root.append(link); link.click(); link.remove?.();
      setTimeout(() => window.URL.revokeObjectURL(url), 0);
      exportReview = null; byId("export-confirm").checked = false;
      show("export-status", "Reviewed JSON export downloaded. Preview again before another download.");
      return "Evidence export downloaded from the reviewed manifest.";
    } catch (error) {
      if (error.code === "permission_denied") exportDenied = true;
      clearExportReview("The export changed or access is unavailable. Preview it again before downloading.");
      throw error;
    }
  });
  click("check-renewal", async () => {
    if (!selected) throw new Error("Select the accepted request first.");
    const result = await api("/cases/" + encodeURIComponent(selected.id) + "/check-renewal", {expected_revision: selected.revision});
    show("renewal-status", `Renewal check: ${friendly(result.classification)}. ${result.reason || "Review the current evidence and start a renewal request when due."}`);
  }, "Renewal readiness checked. Review the result before starting a new request.");
  for (const decision of ["approve", "reject"]) click("review-" + decision, () => {
    const rationale = byId("rationale").value.trim();
    if (!rationale) throw new Error("Enter a review rationale or exception reason.");
    const corrections = {};
    for (const select of byId("corrections").querySelectorAll("select[data-question]")) {
      const key = select.dataset.question;
      const note = document.getElementById(`vo-note-${key}`).value.trim();
      const original = selected.assessment.answers[key];
      if (select.value !== original.answer || note !== (original.note || "")) corrections[key] = {answer: select.value, note};
    }
    return action("review", {decision: decision === "approve" ? "approved" : "rejected", rationale, corrections,
      review_seconds: reviewedAt === null ? null : (performance.now() - reviewedAt) / 1000});
  });
  click("approve", () => {
    const binding = selected?.operation?.approval_card?.binding_sha256;
    if (!digestValue(binding))
      throw new Error("The exact approval card is unavailable. Prepare and review a fresh operation.");
    return action("approve-action", {confirm: true, approval_binding_sha256: binding});
  });
  for (const name of ["execute", "reconcile"])
    click(name, () => action(name, {confirm: true}));
  click("cancel", async () => {
    const rationale = byId("cancellation-reason").value.trim();
    if (!rationale) throw new Error("Enter a reason for cancelling this request.");
    if (!byId("cancellation-confirm").checked) throw new Error("Confirm that cancellation stops local work and does not reverse a Jira ticket.");
    try { return await action("cancel", {rationale, confirm: true}); }
    catch (error) {
      if (error.code === "revision_conflict" && !error.current)
        throw new Error("The request changed during delivery. Refresh requests, review the current ticket result, then cancel again.");
      throw error;
    }
  }, "Local work cancelled. Review the retained ticket result and any reconciliation still required.");
  for (const name of ["accept", "reject"]) click(name, () => {
    const rationale = byId("acceptance-reason").value.trim();
    if (!rationale) throw new Error("Enter an acceptance or rejection reason.");
    return action("accept", {accepted: name === "accept", rationale});
  });
  click("renew", async () => {
    if (!selected) throw new Error("Select the previous request first.");
    for (const [field, key] of [["vendor", "vendor_name"], ["document", "document_id"], ["project", "project"], ["purpose", "purpose"], ["freshness", "freshness_days"]])
      byId(field).value = selected[key];
    byId("document").value = (selected.document_ids || [selected.document_id]).filter(Boolean).join("\n");
    byId("due").value = dateInput(Date.now() / 1000 + 30 * 86400);
    byId("renewal").value = selected.id;
  }, "Renewal details copied. Review the new request fields, then choose Create request.", "document");
  if (byId("due") && !byId("due").value) byId("due").value = dateInput(Date.now() / 1000 + 30 * 86400);
  run(async () => {
    const requested = new URLSearchParams(window.location.search).get("case");
    let returning;try { returning = JSON.parse(window.sessionStorage.getItem(connectionTaskKey)); } catch (_) {}
    if (returning && requested === returning.case_id) {
      if (returning.context !== root.dataset.workspaceContext) throw new Error("The workspace or signed-in account changed. Select the original workspace before returning to this request.");
      if (!Number.isFinite(returning.created_at) || returning.created_at > Date.now()+300000 || Date.now()-returning.created_at >= 86400000)
        throw new Error("The saved return to this request expired. Select the request again from this workspace.");
    }
    await refresh();
    if (requested) {
      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(requested)) throw new Error("The request link is invalid. Select a request from this workspace.");
      render(await api("/cases/" + encodeURIComponent(requested)));
      try { window.sessionStorage.removeItem(connectionTaskKey); } catch (_) {}
      return "Linked request loaded. Review its evidence and current status.";
    }
  }, "Requests loaded. Select a request to begin.");
})();
