/* Bounded client for governed assessment-evidence proposals. */
(function () {
  "use strict";

  var MAX_RESPONSE_BYTES = 1024 * 1024;
  var ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
  var QUESTION_PATTERN = /^[a-z0-9][a-z0-9_]{0,59}$/;
  var OP_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/;

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function failure(message, code) {
    var error = new Error(message);
    error.code = code;
    return error;
  }

  async function boundedResponseText(response) {
    var declared = response.headers && response.headers.get("Content-Length");
    if (declared && /^\d+$/.test(declared) && Number(declared) > MAX_RESPONSE_BYTES) {
      throw failure("The server response exceeded the 1 MiB safety limit.", "response_too_large");
    }
    if (!response.body || typeof response.body.getReader !== "function") {
      throw failure("Streaming response support is required.", "response_stream_unavailable");
    }
    var reader = response.body.getReader();
    var decoder = new TextDecoder("utf-8", {fatal: false});
    var received = 0;
    var text = "";
    while (true) {
      var chunk = await reader.read();
      if (chunk.done) break;
      received += chunk.value.byteLength;
      if (received > MAX_RESPONSE_BYTES) {
        try { await reader.cancel(); } catch (_error) { /* size failure wins */ }
        throw failure("The server response exceeded the 1 MiB safety limit.", "response_too_large");
      }
      text += decoder.decode(chunk.value, {stream: true});
    }
    return text + decoder.decode();
  }

  async function parseResponse(response) {
    var raw = await boundedResponseText(response);
    var payload;
    try { payload = JSON.parse(raw); }
    catch (_error) { throw failure("The server returned invalid JSON.", "response_invalid_json"); }
    if (!isRecord(payload)) throw failure("The server returned an invalid response.", "response_invalid");
    if (!response.ok) {
      var message = typeof payload.detail === "string" ? payload.detail : "The operation failed.";
      var error = failure(message, "request_failed");
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function identifier(value, label) {
    var selected = String(value || "").trim();
    if (!ID_PATTERN.test(selected)) throw new Error(label + " is invalid.");
    return selected;
  }

  function operationKey(value) {
    var selected = String(value || "").trim();
    if (!OP_PATTERN.test(selected)) throw new Error("Idempotency key is invalid.");
    return selected;
  }

  function positiveInteger(value, label) {
    var selected = Number(value);
    if (!Number.isSafeInteger(selected) || selected < 1) {
      throw new Error(label + " must be a positive safe integer.");
    }
    return selected;
  }

  function analysisIds(value) {
    var seen = Object.create(null);
    var values = String(value || "").split(/\r?\n/).map(function (item) {
      return item.trim();
    }).filter(Boolean).map(function (item) {
      return identifier(item, "Evidence analysis ID");
    }).filter(function (item) {
      if (seen[item]) return false;
      seen[item] = true;
      return true;
    });
    if (values.length < 1 || values.length > 32) {
      throw new Error("Provide between 1 and 32 unique evidence analysis IDs.");
    }
    return values;
  }

  function buildCreatePayload(fields) {
    return {
      assessment_id: identifier(fields.assessment_id, "Assessment ID"),
      analysis_ids: analysisIds(fields.analysis_ids),
      idempotency_key: operationKey(fields.idempotency_key)
    };
  }

  function buildReviewPayload(fields) {
    var decision = String(fields.decision || "");
    if (["approve", "reject"].indexOf(decision) === -1) throw new Error("Review decision is invalid.");
    var questions = Array.isArray(fields.selected_question_ids) ? fields.selected_question_ids : [];
    questions = questions.map(function (item) {
      var selected = String(item || "");
      if (!QUESTION_PATTERN.test(selected)) throw new Error("Selected question is invalid.");
      return selected;
    });
    if ((decision === "approve" && questions.length < 1) || (decision === "reject" && questions.length)) {
      throw new Error(decision === "approve" ? "Select at least one eligible answer." : "Reject cannot select answers.");
    }
    var rationale = String(fields.rationale || "").trim();
    if (!rationale || rationale.length > 4000) throw new Error("Review rationale is required and bounded to 4,000 characters.");
    return {
      decision: decision,
      selected_question_ids: questions,
      rationale: rationale,
      expected_revision: positiveInteger(fields.expected_revision, "Expected proposal revision"),
      idempotency_key: operationKey(fields.idempotency_key)
    };
  }

  function buildApplyPayload(fields) {
    if (fields.confirm !== true) throw new Error("Confirm the separate assessment write boundary.");
    return {
      expected_revision: positiveInteger(fields.expected_revision, "Expected proposal revision"),
      expected_assessment_revision: positiveInteger(fields.expected_assessment_revision, "Pinned assessment revision"),
      idempotency_key: operationKey(fields.idempotency_key)
    };
  }

  if (typeof window !== "undefined") {
    Object.defineProperty(window, "LightworkAssessmentEvidenceClient", {
      configurable: false,
      enumerable: false,
      writable: false,
      value: Object.freeze({
        MAX_RESPONSE_BYTES: MAX_RESPONSE_BYTES,
        boundedResponseText: boundedResponseText,
        parseResponse: parseResponse,
        buildCreatePayload: buildCreatePayload,
        buildReviewPayload: buildReviewPayload,
        buildApplyPayload: buildApplyPayload
      })
    });
  }

  if (typeof document === "undefined") return;
  var shell = document.getElementById("assessment-evidence-shell");
  if (!shell) return;
  var apiBase = shell.dataset.apiBase || "";
  var output = document.getElementById("aep-output");
  var status = document.getElementById("aep-operation-status");
  var candidates = document.getElementById("aep-candidates");
  var selection = document.getElementById("aep-candidate-selection");
  var queue = document.getElementById("aep-queue");
  var mutationControls = Array.prototype.slice.call(
    document.querySelectorAll("[data-aep-mutation]")
  );
  var mutationAuthorized = shell.dataset.moduleEnabled === "true" &&
    shell.dataset.canOperate === "true";

  function setText(node, value) { if (node) node.textContent = String(value == null ? "" : value); }
  function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }
  function setMutationReady(ready) {
    mutationControls.forEach(function (controls) {
      var disabled = !mutationAuthorized || ready !== true;
      controls.disabled = disabled;
      controls.toggleAttribute("disabled", disabled);
      controls.setAttribute("aria-disabled", disabled ? "true" : "false");
    });
  }
  function field(form, name) {
    var element = form.elements.namedItem(name);
    if (!element || typeof element.value !== "string") throw new Error("The form is incomplete.");
    return element.value;
  }
  function request(path, options) {
    var settings = options || {};
    settings.credentials = "same-origin";
    settings.headers = Object.assign({"Accept": "application/json"}, settings.headers || {});
    return fetch(apiBase + path, settings).then(parseResponse);
  }
  function show(payload, message) {
    setText(output, JSON.stringify(payload, null, 2));
    setText(status, message || "Operation completed.");
  }
  function showError(error) { setText(status, error && error.message ? error.message : "The operation failed."); }
  function addDefinitionList(card, rows) {
    var list = document.createElement("dl");
    rows.forEach(function (row) {
      var dt = document.createElement("dt");
      var dd = document.createElement("dd");
      dt.textContent = row[0]; dd.textContent = String(row[1] == null ? "—" : row[1]);
      list.appendChild(dt); list.appendChild(dd);
    });
    card.appendChild(list);
  }
  function renderProposal(record) {
    clear(candidates); clear(selection);
    if (!isRecord(record) || !Array.isArray(record.candidates)) return;
    var legend = document.createElement("legend");
    legend.textContent = "Eligible questions from " + String(record.id || "proposal");
    selection.appendChild(legend);
    record.candidates.forEach(function (candidate) {
      if (!isRecord(candidate)) return;
      var card = document.createElement("article");
      card.className = "aep-card";
      card.dataset.eligible = candidate.eligible_for_apply === true ? "true" : "false";
      var title = document.createElement("h3");
      title.textContent = String(candidate.question_id || "Unknown question");
      card.appendChild(title);
      addDefinitionList(card, [
        ["Proposed answer", candidate.answer],
        ["Confidence", String(candidate.confidence_bps || 0) + " bps"],
        ["Eligible", candidate.eligible_for_apply === true ? "yes" : "no"],
        ["Flags", Array.isArray(candidate.flags) ? candidate.flags.join(", ") : "invalid"]
      ]);
      var citation = document.createElement("p");
      citation.className = "aep-citations";
      var citationsList = Array.isArray(candidate.citations) ? candidate.citations : [];
      citation.textContent = citationsList.map(function (item) {
        return isRecord(item) ? String(item.analysis_id || "?") + " / " + String(item.criterion_id || "?") + " / " + String(item.provenance_sha256 || "").slice(0, 16) + "…" : "invalid citation";
      }).join(" | ");
      card.appendChild(citation);
      candidates.appendChild(card);
      if (candidate.eligible_for_apply === true && QUESTION_PATTERN.test(String(candidate.question_id || ""))) {
        var label = document.createElement("label");
        var check = document.createElement("input");
        var span = document.createElement("span");
        check.type = "checkbox"; check.name = "selected_question_ids"; check.value = candidate.question_id;
        span.textContent = candidate.question_id + " — " + candidate.answer + " (" + candidate.confidence_bps + " bps)";
        label.appendChild(check); label.appendChild(span); selection.appendChild(label);
      }
    });
    ["aep-review-id", "aep-apply-id", "aep-inspect-id"].forEach(function (id) {
      var element = document.getElementById(id); if (element) element.value = String(record.id || "");
    });
    var reviewRevision = document.getElementById("aep-review-revision");
    var applyRevision = document.getElementById("aep-apply-revision");
    var assessmentRevision = document.getElementById("aep-assessment-revision");
    if (reviewRevision) reviewRevision.value = String(record.revision || "");
    if (applyRevision) applyRevision.value = String(record.revision || "");
    if (assessmentRevision && isRecord(record.assessment)) assessmentRevision.value = String(record.assessment.revision || "");
  }
  function renderQueue(payload) {
    clear(queue);
    var items = isRecord(payload) && Array.isArray(payload.items) ? payload.items : [];
    setText(document.getElementById("aep-queue-count"), items.length);
    items.forEach(function (item) {
      if (!isRecord(item)) return;
      var card = document.createElement("article"); card.className = "aep-card";
      var title = document.createElement("h3"); title.textContent = String(item.id || "Unknown proposal"); card.appendChild(title);
      addDefinitionList(card, [
        ["Status", item.status], ["Revision", item.revision],
        ["Assessment", isRecord(item.assessment) ? item.assessment.id : "—"],
        ["Candidates", item.candidate_count], ["Eligible", item.eligible_candidate_count]
      ]);
      var button = document.createElement("button"); button.type = "button"; button.className = "btn"; button.textContent = "Inspect";
      button.addEventListener("click", function () { inspect(String(item.id || "")); });
      card.appendChild(button); queue.appendChild(card);
    });
  }
  function inspect(id) {
    return request("/proposals/" + encodeURIComponent(identifier(id, "Proposal ID"))).then(function (record) {
      renderProposal(record); show(record, "Proposal loaded with bounded provenance."); return record;
    }).catch(showError);
  }
  function refreshList() { return request("/proposals?limit=100").then(renderQueue).catch(showError); }
  function refreshStatus() {
    var pill = document.getElementById("aep-status-pill");
    setText(pill, "Checking"); if (pill) pill.dataset.state = "checking";
    return request("/status").then(function (payload) {
      var ready = payload.ready === true;
      setMutationReady(ready);
      setText(pill, ready ? "Ready" : "Not ready"); if (pill) pill.dataset.state = ready ? "ready" : "failed";
      setText(document.getElementById("aep-status-detail"), ready ? "Server-owned proposal dependencies are configured for an operation attempt." : "Review readiness details before creating a proposal.");
      show(payload, "Readiness refreshed. No live provider or customer acceptance is implied.");
    }).catch(function (error) { setMutationReady(false); if (pill) pill.dataset.state = "failed"; setText(pill, "Failed"); showError(error); });
  }

  document.getElementById("aep-refresh-status").addEventListener("click", refreshStatus);
  document.getElementById("aep-refresh-list").addEventListener("click", refreshList);
  document.getElementById("aep-inspect-form").addEventListener("submit", function (event) {
    event.preventDefault(); try { inspect(field(event.currentTarget, "proposal_id")); } catch (error) { showError(error); }
  });
  document.getElementById("aep-create-form").addEventListener("submit", function (event) {
    event.preventDefault();
    try {
      var form = event.currentTarget;
      var payload = buildCreatePayload({assessment_id: field(form, "assessment_id"), analysis_ids: field(form, "analysis_ids"), idempotency_key: field(form, "idempotency_key")});
      request("/proposals", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)}).then(function (record) { renderProposal(record); show(record, "Proposal generated. No assessment answer changed."); refreshList(); }).catch(showError);
    } catch (error) { showError(error); }
  });
  document.getElementById("aep-review-form").addEventListener("submit", function (event) {
    event.preventDefault();
    try {
      var form = event.currentTarget;
      var selected = Array.prototype.slice.call(form.querySelectorAll('input[name="selected_question_ids"]:checked')).map(function (item) { return item.value; });
      var proposalId = identifier(field(form, "proposal_id"), "Proposal ID");
      var payload = buildReviewPayload({decision: field(form, "decision"), selected_question_ids: selected, rationale: field(form, "rationale"), expected_revision: field(form, "expected_revision"), idempotency_key: field(form, "idempotency_key")});
      request("/proposals/" + encodeURIComponent(proposalId) + "/review", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)}).then(function (record) { renderProposal(record); show(record, "Review recorded. Assessment remains unchanged until Apply."); refreshList(); }).catch(showError);
    } catch (error) { showError(error); }
  });
  document.getElementById("aep-apply-form").addEventListener("submit", function (event) {
    event.preventDefault();
    try {
      var form = event.currentTarget;
      var proposalId = identifier(field(form, "proposal_id"), "Proposal ID");
      var confirm = form.elements.namedItem("confirm");
      var payload = buildApplyPayload({expected_revision: field(form, "expected_revision"), expected_assessment_revision: field(form, "expected_assessment_revision"), idempotency_key: field(form, "idempotency_key"), confirm: Boolean(confirm && confirm.checked)});
      request("/proposals/" + encodeURIComponent(proposalId) + "/apply", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)}).then(function (record) { renderProposal(record); show(record, "Approved answers applied under assessment CAS; final assessment review is still required."); refreshList(); }).catch(showError);
    } catch (error) { showError(error); }
  });
  refreshStatus(); refreshList();
}());
