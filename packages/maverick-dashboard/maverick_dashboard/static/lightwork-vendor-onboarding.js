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
  const message = text => show("message", text);
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
      const source = record.evidence?.extracted?.answers?.[question.id];
      for (const citation of source?.citations || []) {
        element("blockquote", citation.quote, item);
        element("small", `Source ${record.document_id}, version ${record.evidence.version}, characters ${citation.start}–${citation.end}`, item);
      }
      if (!source?.citations?.length) element("p", "Gap: no matching source passage. " + question.guidance, item);
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
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request was rejected; check fields and reload the case.");
    return data;
  }
  function render(record) {
    selected = record;
    reviewedAt = performance.now();
    show("case-title", record.vendor_name + " · " + record.id);
    byId("status").replaceChildren();
    const operation = record.operation || {};
    const outcome = record.outcome || {};
    for (const label of ["Stage: " + record.stage, "Revision: " + record.revision,
      "External action: " + (operation.state || operation.status || "not prepared"),
      "Customer acceptance: " + (outcome.accepted === true ? "accepted" : outcome.accepted === false ? "rejected" : "pending")]) {
      const span = document.createElement("span"); span.textContent = label; byId("status").append(span);
    }
    const evidence = record.evidence;
    show("evidence", evidence ? `Status: ${friendly(evidence.status)}\nGoogle Drive file: ${record.document_id}\nVersion: ${evidence.version || "unverified"}\nLast modified: ${date(evidence.modified_at)}\nValid until: ${date(evidence.fresh_until)}\nContent fingerprint: ${evidence.content_sha256 || "unavailable"}` : "No evidence obtained.");
    assessmentView(record);
    const payload = operation.payload;
    show("operation", payload ? `Project: ${payload.project}\nTicket title: ${payload.summary}\n\n${payload.description}\n\nApproval expires: ${date(operation.expires_at)}\nVerified ticket: ${operation.external_confirmation?.key || "pending"}` : "No action prepared.");
    show("operation-record", operation);
    const metrics = record.metrics || {};
    const timedReviews = (record.review_history || []).filter(review => review.review_seconds !== null && review.review_seconds !== undefined);
    const reviewTime = timedReviews.length ? `${Number(metrics.human_review_seconds).toFixed(1)} seconds reported` : "Unmeasured";
    show("outcome", `Customer decision: ${outcome.accepted === true ? "Accepted" : outcome.accepted === false ? "Rejected" : "Pending"}\nReason: ${outcome.rationale || "Not yet recorded"}\nNext renewal: ${date(outcome.renewal_due_at)}\nHuman review time: ${reviewTime}\nExecution cost: ${metrics.execution_cost_usd === null ? "unmeasured" : "$" + (metrics.execution_cost_usd || 0)}\nPrevious case: ${record.renewal_of || "First onboarding"}\nRelease: separate authorization required`);
    show("renewal-status", "Check current evidence and ticket versions before starting a renewal.");
    show("history", [...(record.review_history || []).map(review => `${date(review.at)} · ${review.reviewer}\n${friendly(review.decision)}: ${review.rationale}\n${Object.keys(review.corrections || {}).length} corrected answers; supersedes assessment ${review.supersedes?.id || "none"}.`),
      ...(record.exceptions || []).map(item => typeof item === "string" ? item : JSON.stringify(item))].join("\n\n") || "No reviewer decisions or exceptions yet.");
    buttons();
  }
  async function refresh() {
    const result = await api("/cases");
    byId("cases").replaceChildren();
    for (const record of result.cases) {
      const button = document.createElement("button"); button.type = "button";
      button.textContent = record.vendor_name + " · " + record.stage;
      button.addEventListener("click", () => run(async () => render(await api("/cases/" + encodeURIComponent(record.id)))));
      byId("cases").append(button);
    }
    if (!result.cases.length) byId("cases").textContent = "No requests for this tenant.";
  }
  async function run(operation) {
    if (busy) return;
    busy = true;
    root.querySelectorAll("button").forEach(button => { button.disabled = true; });
    try { await operation(); message("Saved. Review the current case before the next action."); }
    catch (error) { message(error.message); }
    finally {
      busy = false;
      root.querySelectorAll("button").forEach(button => { button.disabled = false; });
      buttons();
    }
  }
  async function action(name, extra = {}) {
    if (!selected) throw new Error("Select a vendor request first.");
    const updated = await api("/cases/" + encodeURIComponent(selected.id) + "/" + name,
      {expected_revision: selected.revision, ...extra});
    render(updated);
    await refresh();
  }
  const click = (id, operation) => byId(id).addEventListener("click", () => run(operation));
  click("refresh", async () => { await refresh(); if (selected) render(await api("/cases/" + encodeURIComponent(selected.id))); });
  byId("intake").addEventListener("submit", event => {
    event.preventDefault();
    run(async () => {
      const record = await api("/cases", {vendor_name: byId("vendor").value.trim(),
        document_id: byId("document").value.trim(), project: byId("project").value.trim(),
        purpose: byId("purpose").value.trim(), freshness_days: Number(byId("freshness").value),
        renewal_of: byId("renewal").value.trim() || null});
      render(record); await refresh();
    });
  });
  click("collect", () => action("collect-evidence"));
  click("assess", () => action("assess"));
  click("prepare", () => action("prepare-action"));
  click("check-renewal", async () => {
    if (!selected) throw new Error("Select the accepted request first.");
    const result = await api("/cases/" + encodeURIComponent(selected.id) + "/check-renewal", {expected_revision: selected.revision});
    show("renewal-status", `Renewal check: ${friendly(result.classification)}. ${result.reason || "Review the current evidence and start a renewal request when due."}`);
  });
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
    byId("renewal").value = selected.id;
    byId("document").focus();
  });
  run(refresh);
})();
