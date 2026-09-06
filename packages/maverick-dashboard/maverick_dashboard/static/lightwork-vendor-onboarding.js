/* Tenant-private onboarding; responses are rendered as text, never trusted HTML. */
(() => {
  "use strict";
  const root = document.getElementById("vendor-onboarding");
  if (!root) return;
  const byId = id => document.getElementById(`vo-${id}`);
  const base = root.dataset.apiBase;
  let selected = null;
  let reviewedAt = null;
  let busy = false;
  const show = (id, value) => { byId(id).textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2); };
  const message = (text, error = false) => {
    byId("message").setAttribute("role", error ? "alert" : "status");
    show("message", text);
  };
  const date = value => value ? new Date(value * 1000).toLocaleString() : "Not recorded";
  const friendly = value => String(value || "pending").replaceAll("_", " ");
  function element(tag, text, parent) {
    const node = document.createElement(tag); node.textContent = text; parent.append(node); return node;
  }
  function buttons() {
    const row = selected || {};
    const op = row.operation || {};
    const editable = Boolean(selected) && !row.outcome && !["executing", "reconciliation_required", "verified"].includes(op.state);
    const allowed = {collect: editable, assess: editable && row.evidence?.status === "available",
      renew: row.outcome?.accepted === true, "check-renewal": row.outcome?.accepted === true,
      "review-approve": editable && Boolean(row.assessment),
      "review-reject": editable && Boolean(row.assessment), prepare: editable && row.assessment?.status === "approved",
      approve: editable && op.state === "prepared", execute: op.state === "approved",
      reconcile: ["executing", "reconciliation_required"].includes(op.state),
      accept: op.state === "verified" && !row.outcome, reject: op.state === "verified" && !row.outcome};
    for (const [name, enabled] of Object.entries(allowed)) byId(name).disabled = busy || !enabled;
    byId("intake").querySelectorAll("input,textarea,select,button").forEach(control => { control.disabled = busy; });
    byId("refresh").disabled = busy;
    byId("cases").querySelectorAll("button").forEach(button => {
      button.disabled = busy;
      button.setAttribute("aria-current", String(button.dataset.caseId === selected?.id));
    });
    byId("corrections").querySelectorAll("select,textarea").forEach(control => { control.disabled = busy || !allowed["review-approve"]; });
    byId("rationale").disabled = busy || !allowed["review-approve"];
    byId("acceptance-reason").disabled = busy || !allowed.accept;
    byId("evidence-controls")?.querySelectorAll("input,textarea,button").forEach(control => { control.disabled = busy || !editable; });
  }
  const evidenceDocuments = record => record.evidence?.documents || (record.evidence ? [record.evidence] : []);
  const documentIds = value => String(value || "").split(/\r?\n/).map(id => id.trim()).filter(Boolean);
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
          const location = citation.pages?.length ? `pages ${citation.pages.join(", ")}` : citation.paragraph ? `paragraph ${citation.paragraph}` : `characters ${citation.start}–${citation.end}`;
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
      headers: {"Content-Type": "application/json"},
      ...(body === undefined ? {} : {body: JSON.stringify(body)})
    });
    let data;
    try { data = await response.json(); } catch (_) { throw new Error("Server response unavailable; reload the case before retrying."); }
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("Server response unavailable; reload the case before retrying.");
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request was rejected; check fields and reload the case.");
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
    if (typeof record.id !== "string" || !Number.isInteger(record.revision) || typeof record.vendor_name !== "string")
      throw new Error("Case response is incomplete; refresh requests before continuing.");
    const sameCase = selected?.id === record.id;
    if (!sameCase && byId("document-date")) byId("document-date").value = "";
    const sameReview = sameCase && selected.revision === record.revision &&
      sameJSON(selected.assessment, record.assessment);
    if (!sameReview) {
      byId("rationale").value = "";
      byId("acceptance-reason").value = "";
      reviewedAt = performance.now();
      if (byId("case-documents")) byId("case-documents").value = (record.document_ids || [record.document_id]).filter(Boolean).join("\n");
      if (byId("upload")) byId("upload").value = "";
    }
    selected = record;
    show("case-title", record.vendor_name);
    show("case-reference", `${record.renewal_of ? "Renewal" : "Initial request"} · ${record.id}`);
    byId("status").replaceChildren();
    const operation = record.operation || {};
    const outcome = record.outcome || {};
    for (const label of ["Stage: " + friendly(record.stage), "Version: " + record.revision,
      "External action: " + friendly(operation.state || operation.status || "not prepared"),
      "Customer acceptance: " + (outcome.accepted === true ? "accepted" : outcome.accepted === false ? "rejected" : "pending")]) {
      const span = document.createElement("span"); span.textContent = label; byId("status").append(span);
    }
    const evidence = record.evidence;
    show("evidence", evidence ? `Overall status: ${friendly(evidence.status)}\n` + evidenceDocuments(record).map(document => `${document.source === "upload" ? "Uploaded" : "Drive"}: ${document.name || document.document_id || record.document_id}\nStatus: ${friendly(document.status || evidence.status)} · Version: ${document.version || "unverified"}\nDocument date: ${date(document.modified_at)} (${document.source === "upload" ? "uploader asserted" : "Drive modified time"})\nValid until: ${date(document.fresh_until)}\nContent fingerprint: ${document.content_sha256 || "unavailable"}`).join("\n\n") : "No evidence obtained. Add documents, then choose Obtain current evidence.");
    if (byId("uploaded-documents")) {
      byId("uploaded-documents").replaceChildren();
      for (const uploaded of record.uploads || []) {
        const row = document.createElement("div");
        element("p", `${uploaded.name} · ${friendly(uploaded.status)} · ${date(uploaded.modified_at)}`, row);
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
    // Keep the review controls and unsaved corrections for this exact version.
    if (!sameReview) assessmentView(record);
    const payload = operation.payload;
    show("operation", payload ? `Project: ${payload.project}\nTicket title: ${payload.summary}\n\n${payload.description}\n\nApproval expires: ${date(operation.expires_at)}\nVerified ticket: ${operation.external_confirmation?.key || "pending"}` : "No action prepared.");
    show("operation-record", operation);
    const metrics = record.metrics || {};
    const timedReviews = (record.review_history || []).filter(review => review.review_seconds !== null && review.review_seconds !== undefined);
    const reviewTime = timedReviews.length ? `${Number(metrics.human_review_seconds).toFixed(1)} seconds reported` : "Unmeasured";
    show("outcome", `Customer decision: ${outcome.accepted === true ? "Accepted" : outcome.accepted === false ? "Rejected" : "Pending"}\nReason: ${outcome.rationale || "Not yet recorded"}\nNext renewal: ${date(outcome.renewal_due_at)}\nHuman review time: ${reviewTime}\nExecution cost: ${metrics.execution_cost_usd == null ? "unmeasured" : "$" + metrics.execution_cost_usd}\nPrevious case: ${record.renewal_of || "First onboarding"}\nRelease: separate approval required`);
    show("renewal-status", "Check current evidence and ticket versions before starting a renewal.");
    show("history", [...(record.review_history || []).map(review => `${date(review.at)} · ${review.reviewer}\n${friendly(review.decision)}: ${review.rationale}\n${Object.keys(review.corrections || {}).length} corrected answers; supersedes assessment ${review.supersedes?.id || "none"}.`),
      ...(record.exceptions || []).map(item => typeof item === "string" ? item : JSON.stringify(item))].join("\n\n") || "No reviewer decisions or exceptions yet.");
    buttons();
    if (sameCase && !sameReview)
      return "The request or assessment changed. Draft answers, notes, and decision reasons were reset. Review the current version before deciding.";
  }
  async function refresh() {
    const result = await api("/cases");
    if (!Array.isArray(result.cases)) throw new Error("Request list is unavailable; refresh before continuing.");
    byId("cases").replaceChildren();
    for (const record of result.cases) {
      const button = document.createElement("button"); button.type = "button";
      button.dataset.caseId = record.id;
      button.textContent = `${record.vendor_name} · ${record.renewal_of ? "Renewal" : "Initial request"} · ${friendly(record.stage)}`;
      button.addEventListener("click", () => run(async () => render(await api("/cases/" + encodeURIComponent(record.id))), "Request loaded. Review its evidence and current status."));
      byId("cases").append(button);
    }
    if (!result.cases.length) byId("cases").textContent = "No vendor requests in this workspace.";
  }
  async function run(operation, success = "Saved. Review the current case before the next action.", focusId) {
    if (busy) return;
    busy = true;
    root.setAttribute("aria-busy", "true");
    buttons();
    let succeeded = false;
    try { message((await operation()) || success); succeeded = true; }
    catch (error) { message(error.message, true); }
    finally {
      busy = false;
      root.setAttribute("aria-busy", "false");
      buttons();
      if (succeeded && focusId) byId(focusId).focus();
    }
  }
  async function action(name, extra = {}) {
    if (!selected) throw new Error("Select a vendor request first.");
    const updated = await api("/cases/" + encodeURIComponent(selected.id) + "/" + name,
      {expected_revision: selected.revision, ...extra});
    render(updated);
    await refresh();
  }
  const click = (id, operation, success, focusId) => byId(id).addEventListener("click", () => run(operation, success, focusId));
  click("refresh", async () => { await refresh(); if (selected) return render(await api("/cases/" + encodeURIComponent(selected.id))); }, "Requests refreshed. Review the current case before continuing.");
  byId("intake").addEventListener("submit", event => {
    event.preventDefault();
    run(async () => {
      const record = await api("/cases", {vendor_name: byId("vendor").value.trim(),
        document_ids: documentIds(byId("document").value), project: byId("project").value.trim(),
        purpose: byId("purpose").value.trim(), freshness_days: Number(byId("freshness").value),
        renewal_of: byId("renewal").value.trim() || null});
      render(record); await refresh();
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
  byId("upload-submit")?.addEventListener("click", () => run(async () => {
    if (!selected) throw new Error("Select a request before adding evidence.");
    const files = Array.from(byId("upload").files || []);
    const documentDate = Date.parse(byId("document-date").value + "T00:00:00Z") / 1000;
    if (!files.length || !Number.isFinite(documentDate)) throw new Error("Choose evidence files and enter their document date.");
    if (files.length + (selected.document_ids || []).length + (selected.uploads || []).length > 8) throw new Error("A request supports at most eight evidence documents.");
    for (const file of files) {
      const mime = file.name.toLowerCase().endsWith(".pdf") ? "application/pdf" : file.name.toLowerCase().endsWith(".txt") ? "text/plain" : "";
      if (!mime) throw new Error("Upload a .txt or supported digital .pdf file.");
      if (file.size > (mime === "application/pdf" ? 16 : 1) * 1024 * 1024) throw new Error("The selected file exceeds the supported size.");
      const query = new URLSearchParams({expected_revision:String(selected.revision),name:file.name,document_date:String(documentDate)});
      const response = await fetch(base + "/cases/" + encodeURIComponent(selected.id) + "/upload?" + query, {method:"POST",credentials:"same-origin",headers:{"Content-Type":mime},body:file});
      let result; try { result = await response.json(); } catch (_) { throw new Error("Upload response unavailable. Refresh the request before retrying."); }
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Upload rejected. Check the file and document date.");
      render(result);
    }
    await refresh();
  }, "Files added. Review extraction status, then obtain current evidence to rebuild the assessment."));
  click("collect", () => action("collect-evidence"));
  click("assess", () => action("assess"));
  click("prepare", () => action("prepare-action"));
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
  for (const name of ["approve", "execute", "reconcile"]) click(name, () => action(name === "approve" ? "approve-action" : name, {confirm: true}));
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
    byId("renewal").value = selected.id;
  }, "Renewal details copied. Review the new request fields, then choose Create request.", "document");
  run(async () => {
    await refresh();
    const requested = new URLSearchParams(window.location.search).get("case");
    if (requested) {
      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(requested)) throw new Error("The request link is invalid. Select a request from this workspace.");
      render(await api("/cases/" + encodeURIComponent(requested)));
      return "Linked request loaded. Review its evidence and current status.";
    }
  }, "Requests loaded. Select a request to begin.");
})();
