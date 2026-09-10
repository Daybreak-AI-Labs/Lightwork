/* Governed knowledge access. Every response is context- and schema-bound, and
   every value is rendered through textContent. */
(function () {
  "use strict";

  var root = document.getElementById("knowledge-access");
  if (!root) { return; }
  var context = String(root.dataset.context || "");
  var tenant = String(root.dataset.tenant || "");
  var actor = String(root.dataset.actor || "");
  var apiBase = String(root.dataset.apiBase || "/api/v1/knowledge-access");
  var canReview = root.dataset.canReview === "true";
  var maxResponseBytes = 4 * 1024 * 1024;
  var searchForm = document.getElementById("ka-search-form");
  var searchButton = document.getElementById("ka-search");
  var exportButton = document.getElementById("ka-export");
  var status = document.getElementById("ka-status");
  var searchNote = document.getElementById("ka-search-note");
  var results = document.getElementById("ka-results");
  var detailPanel = document.getElementById("ka-detail-panel");
  var detailHeading = document.getElementById("ka-detail-heading");
  var detail = document.getElementById("ka-detail");
  var requestForm = document.getElementById("ka-request-form");
  var requestSource = document.getElementById("ka-request-source");
  var requestType = document.getElementById("ka-request-type");
  var correctionField = document.getElementById("ka-correction-field");
  var correction = document.getElementById("ka-proposed-correction");
  var submitRequest = document.getElementById("ka-submit-request");
  var requests = document.getElementById("ka-requests");
  var refreshRequests = document.getElementById("ka-refresh-requests");
  var currentItems = [];
  var selectedReference = null;
  var pending = false;
  var SHA = /^[a-f0-9]{64}$/;
  var ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
  var PURPOSE = /^[a-z][a-z0-9_.-]{0,63}$/;

  if (!SHA.test(context) || !tenant || !actor.startsWith("user:") ||
      !searchForm || !searchButton || !exportButton || !status ||
      !searchNote || !results || !detailPanel || !detailHeading || !detail || !requests ||
      !refreshRequests) { return; }

  function failure(message, code) {
    var error = new Error(message);
    error.code = code || "invalid_response";
    return error;
  }

  function text(value, label, maximum, allowEmpty) {
    if (typeof value !== "string" || (!allowEmpty && !value.trim()) ||
        new TextEncoder().encode(value).length > maximum ||
        /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value)) {
      throw failure("The knowledge response contained an invalid " + label + ".");
    }
    return value;
  }

  function finiteTime(value, label, optional) {
    if (optional && value === null) { return null; }
    if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
      throw failure("The knowledge response contained an invalid " + label + ".");
    }
    return value;
  }

  function bool(value, label) {
    if (typeof value !== "boolean") {
      throw failure("The knowledge response contained an invalid " + label + ".");
    }
    return value;
  }

  function boundedArray(value, label, maximum) {
    if (!Array.isArray(value) || value.length > maximum) {
      throw failure("The knowledge response contained an invalid " + label + ".");
    }
    return value;
  }

  function reference(value, allowModel) {
    if (!value || typeof value !== "object" ||
        (value.kind !== "dataset" && !(allowModel && value.kind === "model")) ||
        !ID.test(value.asset_id) || !ID.test(value.version) ||
        !SHA.test(value.manifest_sha256) ||
        value.schema !== "lightwork.research-registry-ref.v1") {
      throw failure("The knowledge response contained an invalid exact source reference.");
    }
    return {
      kind: value.kind,
      asset_id: value.asset_id,
      version: value.version,
      manifest_sha256: value.manifest_sha256,
      schema: value.schema
    };
  }

  function sameReference(left, right) {
    return left && right && left.kind === right.kind && left.asset_id === right.asset_id &&
      left.version === right.version && left.manifest_sha256 === right.manifest_sha256 &&
      left.schema === right.schema;
  }

  function derivative(value) {
    if (!value || typeof value !== "object") {
      throw failure("The rights response contained malformed derivative lineage.");
    }
    var exact = reference(value.reference, true);
    var action = text(value.required_action, "derivative action", 64, false);
    if (["invalidate_dataset", "retire_or_retrain_model"].indexOf(action) < 0) {
      throw failure("The rights response contained an unsupported derivative action.");
    }
    return {
      reference: exact,
      current_state: text(value.current_state, "derivative state", 64, false),
      required_action: action,
      authority_owner: text(value.authority_owner, "derivative authority owner", 64, false),
      workflow_state: text(value.workflow_state, "derivative workflow state", 96, false)
    };
  }

  function retentionHold(value) {
    if (value === null) { return null; }
    if (!value || typeof value !== "object" ||
        value.schema !== "lightwork.knowledge-retention-hold.v1" ||
        !ID.test(value.hold_id) ||
        ["active", "released"].indexOf(value.status) < 0 ||
        ["active", "released", "expired"].indexOf(value.effective_status) < 0 ||
        !SHA.test(value.evidence_sha256) ||
        typeof value.placed_by !== "string" || !value.placed_by.startsWith("user:")) {
      throw failure("The rights response contained a malformed retention hold.");
    }
    var released = value.status === "released";
    var placedAt = finiteTime(value.placed_at, "retention hold time", false);
    var expiresAt = finiteTime(value.expires_at, "retention hold expiry", false);
    if (expiresAt <= placedAt ||
        (released && value.effective_status !== "released") ||
        (!released && value.effective_status === "released")) {
      throw failure("The rights response contained a malformed retention hold timeline.");
    }
    if (!released &&
        (value.released_by !== null || value.released_at !== null ||
         value.release_reason !== null || value.release_evidence_sha256 !== null)) {
      throw failure("The rights response contained unexpected hold release material.");
    }
    if (released &&
        (typeof value.released_by !== "string" || !value.released_by.startsWith("user:") ||
         !SHA.test(value.release_evidence_sha256) ||
         finiteTime(value.released_at, "retention release time", false) < placedAt)) {
      throw failure("The rights response contained a malformed hold release.");
    }
    return {
      schema: value.schema,
      hold_id: value.hold_id,
      basis: text(value.basis, "retention hold basis", 2000, false),
      evidence_sha256: value.evidence_sha256,
      placed_by: text(value.placed_by, "retention hold actor", 512, false),
      placed_at: placedAt,
      expires_at: expiresAt,
      status: value.status,
      effective_status: value.effective_status,
      released_by: released ? value.released_by : null,
      released_at: released ? finiteTime(value.released_at, "retention release time", false) : null,
      release_reason: released ? text(value.release_reason, "retention release reason", 2000, false) : null,
      release_evidence_sha256: released ? value.release_evidence_sha256 : null
    };
  }

  function rightsRequest(value) {
    if (!value || typeof value !== "object" ||
        value.schema !== "lightwork.knowledge-rights-request.v1" ||
        typeof value.revision !== "number" || !Number.isInteger(value.revision) ||
        value.revision < 1 || !/^knowledge-req-[a-f0-9]{40}$/.test(value.id) ||
        ["correction", "deletion", "exclusion"].indexOf(value.request_type) < 0 ||
        ["submitted", "applying", "approved", "denied"].indexOf(value.status) < 0 ||
        value.lineage_revalidated_before_return !== true) {
      throw failure("The rights response contained a malformed request.");
    }
    var proposed = value.proposed_correction === null ? null :
      text(value.proposed_correction, "proposed correction", 8192, true);
    var evidence = value.decision_evidence_sha256;
    if (evidence !== null && !SHA.test(evidence)) {
      throw failure("The rights response contained invalid decision evidence.");
    }
    if (value.status === "applying" &&
        (value.decision !== "approve" || typeof value.decided_by !== "string" ||
         !value.decided_by.startsWith("user:") || !SHA.test(evidence))) {
      throw failure("The rights response contained malformed recovery authority.");
    }
    return {
      schema: value.schema,
      id: value.id,
      revision: value.revision,
      request_type: value.request_type,
      status: value.status,
      source_reference: reference(value.source_reference),
      requested_by: text(value.requested_by, "requester", 512, false),
      reason: text(value.reason, "request reason", 2000, false),
      proposed_correction: proposed,
      source_use_status: text(value.source_use_status, "source use status", 96, false),
      raw_record_status: text(value.raw_record_status, "raw record status", 96, false),
      retention_review_status: text(value.retention_review_status, "retention review status", 96, false),
      retention_hold: retentionHold(value.retention_hold === undefined ? null : value.retention_hold),
      replacement_reference: value.replacement_reference === null || value.replacement_reference === undefined ?
        null : reference(value.replacement_reference),
      replacement_status: value.replacement_status === null || value.replacement_status === undefined ?
        null : text(value.replacement_status, "replacement status", 96, false),
      decision: value.decision === null ? null : text(value.decision, "decision", 16, false),
      decided_by: value.decided_by === null ? null : text(value.decided_by, "reviewer", 512, false),
      decision_evidence_sha256: evidence,
      derivatives: boundedArray(value.derivatives, "derivative list", 128).map(derivative),
      derivatives_at_request: boundedArray(
        value.derivatives_at_request || value.derivatives,
        "recorded derivative list",
        128
      ).map(derivative),
      lineage_revalidated_before_return: value.lineage_revalidated_before_return === true,
      weight_influence_notice: text(value.weight_influence_notice, "weight notice", 1000, false),
      training_permission_notice: text(value.training_permission_notice, "training notice", 1000, false),
      created_at: finiteTime(value.created_at, "request time", false),
      updated_at: finiteTime(value.updated_at, "request update time", false)
    };
  }

  function item(value, boundary) {
    if (!value || typeof value !== "object" || value.schema !== "lightwork.knowledge-detail.v1") {
      throw failure("The knowledge response contained an unsupported detail record.");
    }
    var exact = reference(value.reference);
    var source = value.source;
    var eligibility = value.eligibility;
    var cache = value.cache;
    if (!source || typeof source !== "object" || !eligibility || typeof eligibility !== "object" ||
        !cache || typeof cache !== "object" || eligibility.state !== "eligible" ||
        !PURPOSE.test(eligibility.purpose) ||
        ["analyze", "export", "read", "train"].indexOf(eligibility.permitted_action) < 0 ||
        ["analyze", "export", "read", "train"].indexOf(eligibility.requested_action) < 0 ||
        typeof eligibility.actor !== "string" || !eligibility.actor.startsWith("user:") ||
        !SHA.test(eligibility.actor_authorization_sha256) ||
        !SHA.test(source.content_sha256) || source.version !== exact.version ||
        source.authority_owner !== "research_registry" ||
        ["source", "derived", "synthetic"].indexOf(source.provenance_kind) < 0 ||
        ["verified", "inferred"].indexOf(value.claim_state) < 0 ||
        cache.owner !== "knowledge_access_process_projection" ||
        cache.content_revalidated_before_return !== true ||
        (boundary && eligibility.purpose !== boundary.purpose) ||
        (boundary && eligibility.requested_action !== boundary.action) ||
        (boundary && eligibility.actor !== boundary.actor) ||
        (boundary && boundary.authorizationSha256 &&
          eligibility.actor_authorization_sha256 !== boundary.authorizationSha256)) {
      throw failure("The knowledge response failed its source eligibility contract.");
    }
    var history = boundedArray(value.correction_history, "correction history", 50).map(rightsRequest);
    var constraints = boundedArray(source.usage_constraints, "usage constraints", 32).map(function (entry) {
      return text(entry, "usage constraint", 256, false);
    });
    return {
      schema: value.schema,
      reference: exact,
      title: text(value.title, "title", 240, false),
      summary: text(value.summary, "summary", 8192, true),
      content_available: bool(value.content_available, "content availability"),
      claim_state: value.claim_state,
      observed_at: finiteTime(value.observed_at, "observation time", true),
      verified_at: finiteTime(value.verified_at, "verification time", true),
      source: {
        authority_owner: source.authority_owner,
        source_ref: text(source.source_ref, "source reference", 2048, false),
        version: source.version,
        content_sha256: source.content_sha256,
        provenance_kind: source.provenance_kind,
        license_id: text(source.license_id, "license", 128, false),
        usage_constraints: constraints
      },
      eligibility: {
        state: "eligible",
        checked_at: finiteTime(eligibility.checked_at, "eligibility check time", false),
        purpose: eligibility.purpose,
        permitted_action: eligibility.permitted_action,
        fresh_until: finiteTime(eligibility.fresh_until, "freshness time", false),
        consent_until: finiteTime(eligibility.consent_until, "consent time", true),
        approval_sha256: SHA.test(eligibility.approval_sha256) ? eligibility.approval_sha256 :
          (function () { throw failure("The knowledge response contained invalid approval evidence."); }()),
        requested_action: eligibility.requested_action,
        actor: text(eligibility.actor, "authorized actor", 512, false),
        actor_authorization_sha256: eligibility.actor_authorization_sha256
      },
      cache: {
        owner: cache.owner,
        hit: bool(cache.hit, "cache hit state"),
        content_revalidated_before_return: true
      },
      correction_history: history,
      training_permission_notice: text(value.training_permission_notice, "training notice", 1000, false)
    };
  }

  function ensureContext(payload) {
    if (!payload || typeof payload !== "object" || payload.context !== context) {
      clearPrivate();
      throw failure("Your account or workspace changed. Private knowledge was cleared; reload the page.", "context_changed");
    }
    return payload;
  }

  function errorDetail(payload) {
    return payload && typeof payload.detail === "string" ? payload.detail : "Knowledge access failed.";
  }

  async function readJson(response) {
    var declared = Number(response.headers.get("content-length") || 0);
    if (declared > maxResponseBytes) {
      if (response.body && typeof response.body.cancel === "function") { await response.body.cancel(); }
      throw failure("The knowledge response exceeded its size boundary.");
    }
    var body = await response.text();
    if (new TextEncoder().encode(body).length > maxResponseBytes) {
      throw failure("The knowledge response exceeded its size boundary.");
    }
    var payload;
    try { payload = body ? JSON.parse(body) : {}; }
    catch (_error) {
      if (response.status === 401 || response.status === 403) { clearPrivate(); }
      throw failure("The knowledge response could not be verified.");
    }
    if (!response.ok) {
      var message = errorDetail(payload);
      if (response.status === 401 || response.status === 403 || /account or workspace changed/i.test(message)) {
        clearPrivate();
      }
      throw failure(message, response.status === 409 ? "conflict" : "request_failed");
    }
    return ensureContext(payload);
  }

  async function api(path, body, method) {
    var response = await fetch(apiBase + path, {
      method: method || "POST",
      credentials: "same-origin",
      headers: {Accept: "application/json", "Content-Type": "application/json"},
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    return readJson(response);
  }

  function node(tag, className, value) {
    var result = document.createElement(tag);
    if (className) { result.className = className; }
    if (value !== undefined && value !== null) { result.textContent = String(value); }
    return result;
  }

  function date(value) {
    if (value === null) { return "Not required"; }
    try { return new Date(value * 1000).toLocaleString(); }
    catch (_error) { return "Unavailable"; }
  }

  function friendly(value) {
    return String(value || "unknown").replace(/_/g, " ");
  }

  function shortReference(value) {
    var source = String(value || "");
    if (source.length <= 72) { return source; }
    return source.slice(0, 38) + "…" + source.slice(-24);
  }

  function setBusy(value) {
    pending = value;
    root.setAttribute("aria-busy", value ? "true" : "false");
    searchButton.disabled = value;
    refreshRequests.disabled = value;
    exportButton.disabled = value || !currentItems.length;
    if (submitRequest) { submitRequest.disabled = value || !selectedReference; }
  }

  function announce(message, focus) {
    status.textContent = message;
    if (focus) { status.focus(); }
  }

  function clearRequestInputs() {
    selectedReference = null;
    if (requestSource) { requestSource.textContent = "Open a source detail to select its exact version."; }
    if (requestForm) {
      requestForm.reset();
      delete requestForm.dataset.requestKey;
    }
    if (submitRequest) { submitRequest.disabled = true; }
    updateCorrectionField();
  }

  function clearPrivate() {
    currentItems = [];
    results.textContent = "";
    detail.textContent = "";
    detailPanel.hidden = true;
    requests.textContent = "";
    exportButton.disabled = true;
    clearRequestInputs();
    var query = document.getElementById("ka-query");
    if (query) { query.value = ""; }
  }

  function clearEligibleResults() {
    currentItems = [];
    results.textContent = "";
    detail.textContent = "";
    detailPanel.hidden = true;
    exportButton.disabled = true;
    searchNote.textContent = "Displayed eligibility is no longer current. Search again.";
    clearRequestInputs();
  }

  function badge(label, state) {
    return node("span", "badge " + state, label);
  }

  function renderResult(value) {
    var card = node("article", "ka-result");
    var head = node("div", "ka-result__head");
    head.appendChild(node("h3", "", value.title));
    head.appendChild(badge(value.claim_state === "verified" ? "Verified" : "Inferred", value.claim_state === "verified" ? "approved" : "info"));
    head.appendChild(badge("Eligible now", "approved"));
    card.appendChild(head);
    card.appendChild(node("p", "", value.content_available ? value.summary : "No governed content preview is available for this registered source."));
    var sourceLabel = node("p", "ka-source", shortReference(value.source.source_ref) +
      " · version " + value.source.version);
    sourceLabel.title = value.source.source_ref;
    card.appendChild(sourceLabel);
    var meta = node("div", "ka-meta");
    meta.appendChild(node("span", "", "Fresh through " + date(value.eligibility.fresh_until)));
    meta.appendChild(node("span", "", "Use: " + friendly(value.eligibility.permitted_action)));
    if (value.correction_history.length) {
      meta.appendChild(node("span", "", value.correction_history.length + " rights request" + (value.correction_history.length === 1 ? "" : "s")));
    }
    card.appendChild(meta);
    var button = node("button", "btn", "View details");
    button.type = "button";
    button.setAttribute("aria-label", "View details for " + value.title);
    button.addEventListener("click", function () { openDetail(value.reference); });
    card.appendChild(button);
    return card;
  }

  function renderResults(payload, requestedPurpose) {
    if (payload.schema !== "lightwork.knowledge-search.v1" ||
        !Array.isArray(payload.items) || payload.items.length > 50 ||
        payload.tenant !== tenant || payload.actor !== actor ||
        !SHA.test(payload.actor_authorization_sha256) ||
        payload.purpose !== requestedPurpose || !PURPOSE.test(payload.purpose) ||
        typeof payload.omitted_ineligible !== "number" ||
        !Number.isInteger(payload.omitted_ineligible) || payload.omitted_ineligible < 0 ||
        typeof payload.truncated !== "boolean") {
      throw failure("The knowledge search response failed validation.");
    }
    var boundary = {purpose: requestedPurpose, action: "read", actor: actor,
      authorizationSha256: payload.actor_authorization_sha256};
    var clean = payload.items.map(function (entry) { return item(entry, boundary); });
    if (typeof payload.count !== "number" || payload.count !== clean.length) {
      throw failure("The knowledge search result count did not match its records.");
    }
    results.textContent = "";
    currentItems = clean;
    if (!clean.length) {
      results.appendChild(node("p", "muted", "No currently eligible sources matched this search."));
    } else {
      clean.forEach(function (value) { results.appendChild(renderResult(value)); });
    }
    searchNote.textContent = payload.omitted_ineligible ?
      payload.omitted_ineligible + " source" + (payload.omitted_ineligible === 1 ? " was" : "s were") +
        " withheld because current access could not be revalidated." :
      "Every displayed source passed current eligibility checks.";
    if (payload.truncated) {
      searchNote.textContent += " More eligible matches exist; narrow the search or increase the result count.";
    }
    exportButton.disabled = !clean.length;
    announce(clean.length + (clean.length === 1 ? " eligible source" : " eligible sources") + " found.", false);
  }

  function listItem(label, value) {
    var entry = node("li");
    entry.appendChild(node("strong", "", label + ": "));
    entry.appendChild(document.createTextNode(value));
    return entry;
  }

  function renderDetail(value) {
    detail.textContent = "";
    var grid = node("div", "ka-detail-grid");
    var provenance = node("div");
    provenance.appendChild(node("h3", "", "Source and provenance"));
    var sourceList = node("ul", "ka-detail-list");
    sourceList.appendChild(listItem("Source", value.source.source_ref));
    sourceList.appendChild(listItem("Version", value.source.version));
    sourceList.appendChild(listItem("Provenance", friendly(value.source.provenance_kind)));
    sourceList.appendChild(listItem("Claim status", value.claim_state));
    sourceList.appendChild(listItem("Observed", date(value.observed_at)));
    sourceList.appendChild(listItem("Verified", date(value.verified_at)));
    provenance.appendChild(sourceList);
    grid.appendChild(provenance);
    var eligibility = node("div");
    eligibility.appendChild(node("h3", "", "Current use check"));
    var useList = node("ul", "ka-detail-list");
    useList.appendChild(listItem("Checked", date(value.eligibility.checked_at)));
    useList.appendChild(listItem("Fresh through", date(value.eligibility.fresh_until)));
    useList.appendChild(listItem("Consent through", date(value.eligibility.consent_until)));
    useList.appendChild(listItem("Purpose", value.eligibility.purpose));
    useList.appendChild(listItem("Permitted action", friendly(value.eligibility.permitted_action)));
    useList.appendChild(listItem("Use checked for", friendly(value.eligibility.requested_action)));
    useList.appendChild(listItem("Authorized user", value.eligibility.actor));
    useList.appendChild(listItem(
      "Authorization evidence",
      value.eligibility.actor_authorization_sha256.slice(0, 12) + "…"
    ));
    useList.appendChild(listItem("Cache", value.cache.hit ? "revalidated cache hit" : "current source projection"));
    useList.appendChild(listItem(
      "Cached copy",
      "Temporary in-app view; the source remains in its governed store"
    ));
    eligibility.appendChild(useList);
    grid.appendChild(eligibility);
    detail.appendChild(node("h3", "", value.title));
    detail.appendChild(node("p", "", value.content_available ? value.summary : "This source has no governed content preview."));
    detail.appendChild(grid);
    var constraints = node("div");
    constraints.appendChild(node("h3", "", "Use constraints"));
    var constraintList = node("ul", "ka-detail-list");
    value.source.usage_constraints.forEach(function (entry) { constraintList.appendChild(node("li", "", entry)); });
    constraints.appendChild(constraintList);
    detail.appendChild(constraints);
    var history = node("div");
    history.appendChild(node("h3", "", "Correction and removal history"));
    if (!value.correction_history.length) {
      history.appendChild(node("p", "muted", "No requests are recorded for this exact version."));
    } else {
      var historyList = node("ul", "ka-detail-list");
      value.correction_history.forEach(function (record) {
        historyList.appendChild(node("li", "", friendly(record.request_type) + " · " + friendly(record.status) + " · " + record.reason));
      });
      history.appendChild(historyList);
    }
    detail.appendChild(history);
    detail.appendChild(node("p", "ka-honesty", value.training_permission_notice));
    detailPanel.hidden = false;
    selectedReference = value.reference;
    if (requestSource) {
      requestSource.textContent = value.title + " · " + shortReference(value.reference.asset_id) +
        " · version " + value.reference.version;
      requestSource.title = value.reference.asset_id;
    }
    if (submitRequest) { submitRequest.disabled = false; }
    detailHeading.focus();
  }

  async function openDetail(exact) {
    if (pending) { return; }
    setBusy(true);
    announce("Rechecking this exact source version…", false);
    var requestedPurpose = document.getElementById("ka-purpose").value.trim();
    try {
      var payload = await api("/detail", {
        context: context,
        reference: exact,
        purpose: requestedPurpose
      });
      var clean = item(payload, {
        purpose: requestedPurpose,
        action: "read",
        actor: actor
      });
      if (!sameReference(clean.reference, exact)) {
        throw failure("The detail response did not match the selected source version.");
      }
      renderDetail(clean);
      announce("Source detail is current and ready for review.", false);
    } catch (error) {
      clearEligibleResults();
      announce(error.message || "Source detail could not be revalidated.", true);
    } finally {
      setBusy(false);
    }
  }

  function validateRequests(payload) {
    if (payload.schema !== "lightwork.knowledge-rights-requests.v1" ||
        !Array.isArray(payload.items) || payload.items.length > 250 ||
        typeof payload.can_review !== "boolean") {
      throw failure("The rights-request response failed validation.");
    }
    var clean = payload.items.map(rightsRequest);
    if (payload.count !== clean.length) {
      throw failure("The rights-request count did not match its records.");
    }
    return clean;
  }

  function renderDerivativeList(records) {
    if (!records.length) { return node("p", "muted", "No registered derivatives are linked to this exact version."); }
    var list = node("ul", "ka-lineage");
    records.forEach(function (entry) {
      var row = node("li", "", shortReference(entry.reference.asset_id) + " · " +
        entry.reference.version + " · registry state " + friendly(entry.current_state) +
        " · " + friendly(entry.required_action) + " · " + friendly(entry.workflow_state));
      row.title = entry.reference.asset_id;
      list.appendChild(row);
    });
    return list;
  }

  async function sha256(value) {
    if (!window.crypto || !window.crypto.subtle || typeof window.crypto.subtle.digest !== "function") {
      throw failure("This browser cannot prepare tamper-evident decision evidence.");
    }
    var buffer = await window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
    return Array.from(new Uint8Array(buffer)).map(function (byte) {
      return byte.toString(16).padStart(2, "0");
    }).join("");
  }

  function canonicalJson(value) {
    if (value === null || typeof value === "string" || typeof value === "boolean") {
      return JSON.stringify(value);
    }
    if (typeof value === "number") {
      if (!Number.isFinite(value)) {
        throw failure("Authority evidence was not finite JSON.");
      }
      return JSON.stringify(value);
    }
    if (Array.isArray(value)) {
      return "[" + value.map(canonicalJson).join(",") + "]";
    }
    if (value && typeof value === "object") {
      return "{" + Object.keys(value).sort().map(function (key) {
        return JSON.stringify(key) + ":" + canonicalJson(value[key]);
      }).join(",") + "}";
    }
    throw failure("Authority evidence was not canonical JSON.");
  }

  function reviewControls(record) {
    var box = node("div", "ka-review");
    var recovering = record.status === "applying";
    if (recovering) {
      box.appendChild(node(
        "p",
        "ka-honesty",
        "Recovery is waiting after the source-retirement boundary. Re-enter the exact rationale used by " +
          record.decided_by + "; its SHA-256 must match " +
          record.decision_evidence_sha256.slice(0, 12) + "…."
      ));
    }
    var label = node("label", "ka-field", recovering ?
      "Exact prior decision rationale" : "Decision rationale");
    var input = node("textarea", "textarea");
    input.rows = 3;
    input.maxLength = 2000;
    input.required = true;
    label.appendChild(input);
    box.appendChild(label);
    var confirmLabel = node("label", "ka-check");
    var confirm = document.createElement("input");
    confirm.type = "checkbox";
    confirmLabel.appendChild(confirm);
    confirmLabel.appendChild(node("span", "", recovering ?
      "I verified this is an exact recovery of the recorded approval." :
      "I reviewed the source and every affected derivative shown above."));
    box.appendChild(confirmLabel);
    var actions = node("div", "ka-actions");
    (recovering ? [
      ["Recover approved retirement", "approve", "btn btn--danger"]
    ] : [
      ["Approve and retire source version", "approve", "btn btn--danger"],
      ["Deny request", "deny", "btn"]
    ]).forEach(function (choice) {
      var button = node("button", choice[2], choice[0]);
      button.type = "button";
      button.addEventListener("click", async function () {
        var note = input.value.trim();
        if (!note || !confirm.checked) {
          announce("Add a decision rationale and confirm the derivative review.", true);
          return;
        }
        if (pending) { return; }
        setBusy(true);
        announce("Applying the reviewed source-rights decision…", false);
        try {
          var digest = await sha256(note);
          var payload = await api("/requests/" + encodeURIComponent(record.id) + "/decision", {
            context: context,
            confirm: true,
            expected_revision: record.revision,
            decision: choice[1],
            evidence_sha256: digest
          });
          rightsRequest(payload);
          await loadRequests();
          if (choice[1] === "approve") {
            currentItems = [];
            results.textContent = "";
            exportButton.disabled = true;
            detail.textContent = "";
            detailPanel.hidden = true;
            clearRequestInputs();
          }
          announce(choice[1] === "approve" ?
            "The exact source version is retired from new use. Derivative follow-up is recorded." :
            "The request was denied; source use remains unchanged.", true);
        } catch (error) {
          try { await loadRequests(); } catch (_refreshError) { /* retain the decision error */ }
          announce(error.message || "The rights decision could not be completed.", true);
        } finally {
          setBusy(false);
        }
      });
      actions.appendChild(button);
    });
    box.appendChild(actions);
    return box;
  }

  function retentionHoldControls(record) {
    var box = node("div", "ka-review");
    box.appendChild(node("h4", "", "Preservation hold"));
    var hold = record.retention_hold;
    if (!hold) {
      var identityLabel = node("label", "ka-field", "Hold ID");
      var identity = document.createElement("input");
      identity.className = "input";
      identity.maxLength = 128;
      identity.required = true;
      identityLabel.appendChild(identity);
      box.appendChild(identityLabel);
      var basisLabel = node("label", "ka-field", "Legal or preservation basis");
      var basis = document.createElement("textarea");
      basis.className = "textarea";
      basis.rows = 3;
      basis.maxLength = 2000;
      basis.required = true;
      basisLabel.appendChild(basis);
      box.appendChild(basisLabel);
      var expiryLabel = node("label", "ka-field", "Hold expires");
      var expiry = document.createElement("input");
      expiry.className = "input";
      expiry.type = "datetime-local";
      expiry.required = true;
      expiryLabel.appendChild(expiry);
      box.appendChild(expiryLabel);
      var confirmLabel = node("label", "ka-check");
      var confirm = document.createElement("input");
      confirm.type = "checkbox";
      confirmLabel.appendChild(confirm);
      confirmLabel.appendChild(node("span", "", "I verified the hold identity, basis, and expiry."));
      box.appendChild(confirmLabel);
      var place = node("button", "btn", "Place retention hold");
      place.type = "button";
      place.addEventListener("click", async function () {
        var holdId = identity.value.trim();
        var holdBasis = basis.value.trim();
        var expiresAt = new Date(expiry.value).getTime() / 1000;
        if (!ID.test(holdId) || !holdBasis || !Number.isFinite(expiresAt) ||
            expiresAt <= Date.now() / 1000 || !confirm.checked) {
          announce("Add a valid hold ID, basis, future expiry, and confirmation.", true);
          return;
        }
        if (pending) { return; }
        setBusy(true);
        try {
          var evidence = await sha256(canonicalJson({
            request_id: record.id,
            hold_id: holdId,
            basis: holdBasis,
            expires_at: expiresAt
          }));
          await api("/requests/" + encodeURIComponent(record.id) + "/retention-hold", {
            context: context,
            confirm: true,
            expected_revision: record.revision,
            hold_id: holdId,
            basis: holdBasis,
            expires_at: expiresAt,
            evidence_sha256: evidence
          });
          await loadRequests();
          announce("The retention hold is active and bound to this deletion request.", true);
        } catch (error) {
          try { await loadRequests(); } catch (_refreshError) { /* retain original error */ }
          announce(error.message || "The retention hold could not be placed.", true);
        } finally {
          setBusy(false);
        }
      });
      box.appendChild(place);
      return box;
    }
    if (hold.effective_status === "released") { return box; }
    var reasonLabel = node("label", "ka-field", "Why the hold can be released");
    var reason = document.createElement("textarea");
    reason.className = "textarea";
    reason.rows = 3;
    reason.maxLength = 2000;
    reason.required = true;
    reasonLabel.appendChild(reason);
    box.appendChild(reasonLabel);
    var releaseConfirmLabel = node("label", "ka-check");
    var releaseConfirm = document.createElement("input");
    releaseConfirm.type = "checkbox";
    releaseConfirmLabel.appendChild(releaseConfirm);
    releaseConfirmLabel.appendChild(node("span", "", "I verified the preservation duty has ended."));
    box.appendChild(releaseConfirmLabel);
    var release = node("button", "btn btn--danger", "Release retention hold");
    release.type = "button";
    release.addEventListener("click", async function () {
      var releaseReason = reason.value.trim();
      if (!releaseReason || !releaseConfirm.checked) {
        announce("Add the release reason and confirm the preservation review.", true);
        return;
      }
      if (pending) { return; }
      setBusy(true);
      try {
        var evidence = await sha256(canonicalJson({
          request_id: record.id,
          hold_id: hold.hold_id,
          reason: releaseReason
        }));
        await api("/requests/" + encodeURIComponent(record.id) + "/retention-hold/release", {
          context: context,
          confirm: true,
          expected_revision: record.revision,
          reason: releaseReason,
          evidence_sha256: evidence
        });
        await loadRequests();
        announce("The retention hold was released with evidence.", true);
      } catch (error) {
        try { await loadRequests(); } catch (_refreshError) { /* retain original error */ }
        announce(error.message || "The retention hold could not be released.", true);
      } finally {
        setBusy(false);
      }
    });
    box.appendChild(release);
    return box;
  }

  function renderRequests(items) {
    requests.textContent = "";
    if (!items.length) {
      requests.appendChild(node("p", "muted", "No correction, exclusion, or deletion requests are recorded in this workspace."));
      return;
    }
    items.forEach(function (record) {
      var card = node("article", "ka-request");
      var head = node("div", "ka-request__head");
      head.appendChild(node("h3", "", friendly(record.request_type) + " request"));
      head.appendChild(badge(friendly(record.status), record.status === "approved" ? "approved" :
        record.status === "denied" ? "denied" : record.status === "applying" ? "warning" : "pending"));
      card.appendChild(head);
      var source = node("p", "ka-source", shortReference(record.source_reference.asset_id) +
        " · version " + record.source_reference.version);
      source.title = record.source_reference.asset_id;
      card.appendChild(source);
      card.appendChild(node("p", "", record.reason));
      if (record.proposed_correction) {
        card.appendChild(node("p", "", "Proposed correction: " + record.proposed_correction));
      }
      if (record.replacement_reference) {
        card.appendChild(node(
          "p",
          "",
          "Corrected replacement: " + shortReference(record.replacement_reference.asset_id) +
            " · version " + record.replacement_reference.version +
            " · " + friendly(record.replacement_status)
        ));
      } else if (record.request_type === "correction" && record.status === "submitted") {
        card.appendChild(node(
          "p",
          "muted",
          "A different named administrator must review and approve the corrected replacement."
        ));
      }
      card.appendChild(node("p", "", "Source use: " + friendly(record.source_use_status) +
        ". Raw record: " + friendly(record.raw_record_status) +
        ". Preservation and retention review: " + friendly(record.retention_review_status) + "."));
      if (record.retention_hold) {
        var hold = record.retention_hold;
        card.appendChild(node(
          "p",
          "ka-honesty",
          "Retention hold " + hold.hold_id + " is " + friendly(hold.effective_status) +
            " through " + date(hold.expires_at) + ". Basis: " + hold.basis +
            (hold.release_reason ? " Release: " + hold.release_reason : "")
        ));
      }
      card.appendChild(node("p", "ka-lineage-label", "Current affected derivatives · rechecked before display"));
      card.appendChild(renderDerivativeList(record.derivatives));
      if (JSON.stringify(record.derivatives_at_request) !== JSON.stringify(record.derivatives)) {
        var recorded = document.createElement("details");
        recorded.appendChild(node("summary", "", "Affected derivatives recorded when submitted"));
        recorded.appendChild(renderDerivativeList(record.derivatives_at_request));
        card.appendChild(recorded);
      }
      card.appendChild(node("p", "ka-honesty", record.weight_influence_notice));
      if (canReview && (record.status === "submitted" || record.status === "applying")) {
        card.appendChild(reviewControls(record));
      }
      if (canReview && record.request_type === "deletion" &&
          record.status !== "applying" &&
          (!record.retention_hold || record.retention_hold.effective_status !== "released")) {
        card.appendChild(retentionHoldControls(record));
      }
      requests.appendChild(card);
    });
  }

  async function loadRequests() {
    var response = await fetch(apiBase + "/requests?limit=100", {
      method: "GET",
      credentials: "same-origin",
      headers: {Accept: "application/json"}
    });
    var payload = await readJson(response);
    renderRequests(validateRequests(payload));
  }

  function updateCorrectionField() {
    if (!requestType || !correctionField || !correction) { return; }
    var shown = requestType.value === "correction";
    correctionField.hidden = !shown;
    correction.required = shown;
    if (!shown) { correction.value = ""; }
  }

  searchForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (pending) { return; }
    var purpose = document.getElementById("ka-purpose").value.trim();
    var limit = Number(document.getElementById("ka-limit").value);
    if (!PURPOSE.test(purpose) || !Number.isInteger(limit) || limit < 1 || limit > 50) {
      announce("Choose a valid intended use and result count.", true);
      return;
    }
    setBusy(true);
    detail.textContent = "";
    detailPanel.hidden = true;
    clearRequestInputs();
    results.textContent = "";
    results.appendChild(node("p", "muted", "Rechecking current source access…"));
    announce("Searching currently eligible sources…", false);
    try {
      var payload = await api("/search", {
        context: context,
        query: document.getElementById("ka-query").value.trim(),
        purpose: purpose,
        limit: limit
      });
      renderResults(payload, purpose);
    } catch (error) {
      currentItems = [];
      results.textContent = "";
      exportButton.disabled = true;
      announce(error.message || "Knowledge search failed.", true);
    } finally {
      setBusy(false);
    }
  });

  exportButton.addEventListener("click", async function () {
    if (pending || !currentItems.length) { return; }
    var expected = currentItems.map(function (entry) { return entry.reference; });
    var requestedPurpose = document.getElementById("ka-purpose").value.trim();
    setBusy(true);
    announce("Rechecking every source before export…", false);
    try {
      var payload = await api("/export", {
        context: context,
        purpose: requestedPurpose,
        references: expected
      });
      if (payload.schema !== "lightwork.knowledge-export.v1" || payload.complete !== true ||
          payload.count !== expected.length || !Array.isArray(payload.items) ||
          payload.items.length !== expected.length) {
        throw failure("The knowledge export was incomplete or malformed.");
      }
      if (payload.tenant !== tenant || payload.actor !== actor ||
          payload.purpose !== requestedPurpose ||
          !SHA.test(payload.actor_authorization_sha256)) {
        throw failure("The knowledge export crossed its requested authority boundary.");
      }
      var clean = payload.items.map(function (entry) {
        return item(entry, {
          purpose: requestedPurpose,
          action: "export",
          actor: actor,
          authorizationSha256: payload.actor_authorization_sha256
        });
      });
      clean.forEach(function (entry, index) {
        if (!sameReference(entry.reference, expected[index]) ||
            entry.eligibility.requested_action !== "export") {
          throw failure("The knowledge export did not match the selected source versions.");
        }
      });
      var evidence = payload.authority_evidence;
      var qualification = payload.qualification;
      if (!evidence || typeof evidence !== "object" ||
          evidence.schema !== "lightwork.knowledge-export-authority-evidence.v1" ||
          evidence.action !== "export" || evidence.tenant !== payload.tenant ||
          evidence.actor !== payload.actor || evidence.purpose !== payload.purpose ||
          evidence.actor_authorization_sha256 !== payload.actor_authorization_sha256 ||
          !String(payload.actor || "").startsWith("user:") ||
          !SHA.test(payload.actor_authorization_sha256) || !SHA.test(evidence.binding_sha256) ||
          !Array.isArray(evidence.sources) || evidence.sources.length !== clean.length ||
          !qualification || typeof qualification !== "object" ||
          qualification.local_control_validation !== "passed_at_request_time" ||
          qualification.external_store_and_customer_qualification !== "qualification_pending") {
        throw failure("The knowledge export authority evidence was malformed.");
      }
      evidence.sources.forEach(function (source, index) {
        if (!source || typeof source !== "object" ||
            !sameReference(reference(source.reference), clean[index].reference) ||
            source.content_sha256 !== clean[index].source.content_sha256 ||
            source.approval_sha256 !== clean[index].eligibility.approval_sha256 ||
            !SHA.test(source.content_sha256) || !SHA.test(source.approval_sha256)) {
          throw failure("The knowledge export authority evidence did not match its source versions.");
        }
      });
      var boundEvidence = {
        schema: evidence.schema,
        tenant: evidence.tenant,
        actor: evidence.actor,
        actor_authorization_sha256: evidence.actor_authorization_sha256,
        purpose: evidence.purpose,
        action: evidence.action,
        sources: evidence.sources.map(function (source) {
          return {
            reference: reference(source.reference),
            content_sha256: source.content_sha256,
            approval_sha256: source.approval_sha256
          };
        })
      };
      if ((await sha256(canonicalJson(boundEvidence))) !== evidence.binding_sha256) {
        throw failure("The knowledge export authority binding did not match its evidence.");
      }
      var blob = new Blob([JSON.stringify(payload, null, 2)], {type: "application/json"});
      var url = URL.createObjectURL(blob);
      var link = document.createElement("a");
      link.href = url;
      link.download = "knowledge-access-export.json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 0);
      announce("Exported " + clean.length + " currently eligible source" + (clean.length === 1 ? "." : "s."), false);
    } catch (error) {
      clearEligibleResults();
      announce(error.message || "Knowledge export failed closed.", true);
    } finally {
      setBusy(false);
    }
  });

  if (requestType) {
    requestType.addEventListener("change", updateCorrectionField);
    updateCorrectionField();
  }

  if (requestForm) {
    requestForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      if (pending || !selectedReference) { return; }
      var reason = requestForm.elements.reason.value.trim();
      var type = requestForm.elements.request_type.value;
      var proposed = requestForm.elements.proposed_correction.value.trim();
      if (!reason || !requestForm.elements.confirm.checked || (type === "correction" && !proposed)) {
        announce("Complete and confirm the exact source-rights request.", true);
        return;
      }
      if (!requestForm.dataset.requestKey) {
        requestForm.dataset.requestKey = "request-" + (window.crypto && typeof window.crypto.randomUUID === "function" ?
          window.crypto.randomUUID() : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2));
      }
      var body = {
        context: context,
        confirm: true,
        reference: selectedReference,
        request_type: type,
        reason: reason,
        request_key: requestForm.dataset.requestKey,
        proposed_correction: type === "correction" ? proposed : null
      };
      setBusy(true);
      announce("Recording the exact source and its derivative impact…", false);
      try {
        var payload = await api("/requests", body);
        rightsRequest(payload);
        requestForm.reset();
        delete requestForm.dataset.requestKey;
        updateCorrectionField();
        await loadRequests();
        announce("The source-rights request and affected derivatives are recorded.", true);
      } catch (error) {
        announce((error.message || "The rights request could not be recorded.") + " Your inputs are still here for an exact retry.", true);
      } finally {
        setBusy(false);
      }
    });
  }

  refreshRequests.addEventListener("click", async function () {
    if (pending) { return; }
    setBusy(true);
    try {
      await loadRequests();
      announce("Rights-request status is current.", false);
    } catch (error) {
      announce(error.message || "Rights-request status could not be loaded.", true);
    } finally {
      setBusy(false);
    }
  });

  loadRequests().catch(function (error) {
    requests.textContent = "";
    requests.appendChild(node("p", "muted", error.message || "Rights-request status is unavailable."));
  });
})();
