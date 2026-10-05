"use strict";

(function (global) {
  var MAX_RESPONSE_BYTES = 64 * 1024;
  var MAX_JSON_INPUT_BYTES = 48 * 1024;
  var MAX_UI_ITEMS = 500;
  var ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
  var IDEMPOTENCY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{7,191}$/;
  var DIGEST_PATTERN = /^[0-9a-f]{64}$/;
  var STATES = new Set(["queued", "running", "succeeded", "failed", "cancelled"]);
  var ACTIONS = new Set(["grant", "deny", "revoke", "withdraw"]);
  var SOURCES = new Set(["client", "edge", "operator", "sdk", "header"]);
  var OPERATIONS = new Set([
    "edit_preferences", "delete_subject", "change_identifier_type",
    "reactivate", "clear_expiration"
  ]);

  function failure(message, code, status) {
    var error = new Error(message);
    error.code = code;
    error.status = status || 0;
    return error;
  }

  async function readBoundedJson(response) {
    var declared = response.headers.get("Content-Length");
    if (declared !== null && Number(declared) > MAX_RESPONSE_BYTES) {
      if (response.body && typeof response.body.cancel === "function") {
        await response.body.cancel();
      }
      throw failure("Server response exceeded the 64 KiB safety limit.", "response_too_large", response.status);
    }
    if (!response.body || typeof response.body.getReader !== "function") {
      throw failure("The reply could not be read. Refresh the page and try again.", "response_stream_unavailable", response.status);
    }
    var reader = response.body.getReader();
    var chunks = [];
    var total = 0;
    while (true) {
      var part = await reader.read();
      if (part.done) { break; }
      total += part.value.byteLength;
      if (total > MAX_RESPONSE_BYTES) {
        await reader.cancel();
        throw failure("Server response exceeded the 64 KiB safety limit.", "response_too_large", response.status);
      }
      chunks.push(part.value);
    }
    var bytes = new Uint8Array(total);
    var offset = 0;
    chunks.forEach(function (chunk) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    });
    var responseText = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    try {
      return JSON.parse(responseText);
    } catch (_error) {
      throw failure("Server returned invalid JSON.", "response_invalid_json", response.status);
    }
  }

  async function parseResponse(response) {
    var payload = await readBoundedJson(response);
    if (!response.ok) {
      var detail = payload && typeof payload.detail === "string"
        ? payload.detail.slice(0, 500)
        : "The bulk request didn't work. Refresh the page and try again.";
      if (response.status === 422 && payload && Array.isArray(payload.detail)) {
        var invalidField = payload.detail.find(function (issue) {
          return issue && Array.isArray(issue.loc) && issue.loc[0] === "body" &&
            issue.loc[1] === "items" && Number.isInteger(issue.loc[2]) &&
            (issue.loc[3] === "receipt_limit" || issue.loc[3] === "after_sequence");
        });
        if (invalidField) {
          detail = invalidField.loc[3] === "receipt_limit"
            ? "Choose between 1 and 500 records per person."
            : "Starting from a saved position is not available. Choose up to the latest 500 records per person.";
        }
      }
      throw failure(detail, "request_failed", response.status);
    }
    return payload;
  }

  function identifier(value, label) {
    var selected = String(value || "").trim();
    if (!ID_PATTERN.test(selected)) {
      throw failure(label + " must be a valid opaque reference.", "invalid_input");
    }
    return selected;
  }

  function idempotencyKey(value) {
    var selected = String(value || "").trim();
    if (!IDEMPOTENCY_PATTERN.test(selected)) {
      throw failure("The request reference must contain 8 to 192 letters, numbers, or supported separators.", "invalid_input");
    }
    return selected;
  }

  function integer(value, label, minimum, maximum) {
    var selected = Number(value);
    if (!Number.isInteger(selected) || selected < minimum || selected > maximum) {
      throw failure(label + " must be an integer from " + minimum + " to " + maximum + ".", "invalid_input");
    }
    return selected;
  }

  function positiveNumber(value, label, allowNull) {
    if (allowNull && (value === null || value === undefined || value === "")) { return null; }
    var selected = Number(value);
    if (!Number.isFinite(selected) || selected <= 0) {
      throw failure(label + " must be a positive timestamp.", "invalid_input");
    }
    return selected;
  }

  function optionalTimestamp(value, label) {
    if (value === null || value === undefined || String(value).trim() === "") { return null; }
    if (typeof value === "number") { return positiveNumber(value, label, false); }
    var parsed = Date.parse(String(value));
    if (!Number.isFinite(parsed) || parsed <= 0) {
      throw failure(label + " is invalid.", "invalid_input");
    }
    return parsed / 1000;
  }

  function objectValue(value, label) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw failure(label + " must be an object.", "invalid_input");
    }
    return value;
  }

  function exactShape(value, required, optional, label) {
    var selected = objectValue(value, label);
    var allowed = new Set(required.concat(optional));
    Object.keys(selected).forEach(function (key) {
      if (!allowed.has(key)) {
        throw failure(label + " contains unsupported field " + key + ".", "invalid_input");
      }
    });
    required.forEach(function (key) {
      if (!Object.prototype.hasOwnProperty.call(selected, key) || selected[key] === null) {
        throw failure(label + " is missing " + key + ".", "invalid_input");
      }
    });
    return selected;
  }

  function contextValue(value) {
    var selected = exactShape(
      value,
      ["jurisdiction", "country_code"],
      ["region_code", "collection_point", "asserted_source"],
      "Context"
    );
    var country = String(selected.country_code || "");
    var region = String(selected.region_code || "");
    var source = String(selected.asserted_source || "operator");
    if (!/^[A-Z]{2}$/.test(country) || !/^(?:[A-Z0-9]{1,8}(?:-[A-Z0-9]{1,8}){0,2})?$/.test(region)) {
      throw failure("Context country or region code is invalid.", "invalid_input");
    }
    if (!SOURCES.has(source)) { throw failure("Context source is invalid.", "invalid_input"); }
    return {
      jurisdiction: identifier(selected.jurisdiction, "Jurisdiction"),
      country_code: country,
      region_code: region,
      collection_point: identifier(selected.collection_point || "web", "Collection point"),
      asserted_source: source
    };
  }

  function choicesValue(value) {
    if (!Array.isArray(value) || value.length < 1 || value.length > 128) {
      throw failure("Choices must contain 1-128 entries.", "invalid_input");
    }
    return value.map(function (raw, index) {
      var item = exactShape(
        raw,
        ["purpose_id", "action"],
        ["channel", "vendor_id", "expires_at"],
        "Choice " + (index + 1)
      );
      if (!ACTIONS.has(item.action)) { throw failure("Choice action is invalid.", "invalid_input"); }
      return {
        purpose_id: identifier(item.purpose_id, "Purpose ID"),
        action: item.action,
        channel: identifier(item.channel || "all", "Channel"),
        vendor_id: identifier(item.vendor_id || "all", "Vendor ID"),
        expires_at: positiveNumber(item.expires_at, "Choice expiration", true)
      };
    });
  }

  function parseItems(source, operation) {
    var raw = String(source || "");
    if (new TextEncoder().encode(raw).byteLength > MAX_JSON_INPUT_BYTES) {
      throw failure("Items JSON exceeds the 48 KiB browser limit.", "invalid_input");
    }
    var parsed;
    try {
      parsed = JSON.parse(raw);
    } catch (_error) {
      throw failure("Items JSON is invalid.", "invalid_input");
    }
    if (!Array.isArray(parsed) || parsed.length < 1 || parsed.length > MAX_UI_ITEMS) {
      throw failure("Items JSON must contain 1-500 entries in this workbench.", "invalid_input");
    }
    var seen = new Set();
    return parsed.map(function (rawItem, index) {
      var label = "Item " + (index + 1);
      var item;
      var normalized;
      if (operation === "export_receipts") {
        if (rawItem && Object.prototype.hasOwnProperty.call(rawItem, "after_sequence")) {
          throw failure("Starting from a saved position is not available. Choose up to the latest 500 records per person.", "invalid_input");
        }
        item = exactShape(rawItem, ["item_id", "profile_id"], ["receipt_limit"], label);
        normalized = {
          item_id: identifier(item.item_id, "Item ID"),
          profile_id: identifier(item.profile_id, "Profile ID")
        };
        if (Object.prototype.hasOwnProperty.call(item, "receipt_limit")) {
          normalized.receipt_limit = integer(item.receipt_limit, "Receipt limit", 1, 500);
        }
      } else if (operation === "edit_preferences") {
        item = exactShape(
          rawItem,
          ["item_id", "profile_id", "expected_revision", "policy_id", "policy_version", "context", "choices"],
          ["retention_until"],
          label
        );
        normalized = {
          item_id: identifier(item.item_id, "Item ID"),
          profile_id: identifier(item.profile_id, "Profile ID"),
          expected_revision: integer(item.expected_revision, "Expected revision", 0, 2147483647),
          policy_id: identifier(item.policy_id, "Policy ID"),
          policy_version: integer(item.policy_version, "Policy version", 1, 2147483647),
          context: contextValue(item.context),
          choices: choicesValue(item.choices)
        };
        if (Object.prototype.hasOwnProperty.call(item, "retention_until")) {
          normalized.retention_until = positiveNumber(item.retention_until, "Retention timestamp", true);
        }
      } else if (operation === "reactivate") {
        item = exactShape(
          rawItem,
          ["item_id", "profile_id", "expected_revision", "receipt_id", "reactivation_at"],
          [],
          label
        );
        normalized = {
          item_id: identifier(item.item_id, "Item ID"),
          profile_id: identifier(item.profile_id, "Profile ID"),
          expected_revision: integer(item.expected_revision, "Expected revision", 0, 2147483647),
          receipt_id: identifier(item.receipt_id, "Receipt ID"),
          reactivation_at: positiveNumber(item.reactivation_at, "Reactivation timestamp", false)
        };
      } else if (operation === "delete_subject") {
        item = exactShape(rawItem, ["item_id", "profile_id", "expected_revision", "reason_code"], [], label);
        normalized = {
          item_id: identifier(item.item_id, "Item ID"),
          profile_id: identifier(item.profile_id, "Profile ID"),
          expected_revision: integer(item.expected_revision, "Expected revision", 0, 2147483647),
          reason_code: identifier(item.reason_code, "Reason code")
        };
      } else if (operation === "change_identifier_type") {
        item = exactShape(rawItem, ["item_id", "profile_id", "expected_revision", "identity_kind", "subject_sha256"], [], label);
        if (item.identity_kind !== "known" && item.identity_kind !== "anonymous") {
          throw failure("Identity kind is invalid.", "invalid_input");
        }
        if (!DIGEST_PATTERN.test(String(item.subject_sha256 || ""))) {
          throw failure("Subject digest must be lowercase SHA-256.", "invalid_input");
        }
        normalized = {
          item_id: identifier(item.item_id, "Item ID"),
          profile_id: identifier(item.profile_id, "Profile ID"),
          expected_revision: integer(item.expected_revision, "Expected revision", 0, 2147483647),
          identity_kind: item.identity_kind,
          subject_sha256: item.subject_sha256
        };
      } else if (operation === "clear_expiration") {
        item = exactShape(rawItem, ["item_id", "profile_id", "expected_revision", "receipt_id"], [], label);
        normalized = {
          item_id: identifier(item.item_id, "Item ID"),
          profile_id: identifier(item.profile_id, "Profile ID"),
          expected_revision: integer(item.expected_revision, "Expected revision", 0, 2147483647),
          receipt_id: identifier(item.receipt_id, "Receipt ID")
        };
      } else {
        throw failure("Operation is unsupported.", "invalid_input");
      }
      if (seen.has(normalized.item_id)) { throw failure("Item IDs must be unique.", "invalid_input"); }
      seen.add(normalized.item_id);
      return normalized;
    });
  }

  function buildExportPayload(values) {
    var payload = {
      items: parseItems(values.items_json, "export_receipts"),
      idempotency_key: idempotencyKey(values.idempotency_key)
    };
    var scheduled = optionalTimestamp(values.scheduled_for, "Schedule");
    if (scheduled !== null) { payload.scheduled_for = scheduled; }
    return payload;
  }

  function buildActionPayload(values) {
    var operation = String(values.operation || "");
    if (!OPERATIONS.has(operation)) { throw failure("Operation is unsupported.", "invalid_input"); }
    var payload = {
      operation: operation,
      items: parseItems(values.items_json, operation),
      idempotency_key: idempotencyKey(values.idempotency_key)
    };
    var scheduled = optionalTimestamp(values.scheduled_for, "Schedule");
    if (scheduled !== null) { payload.scheduled_for = scheduled; }
    return payload;
  }

  global.LightworkConsentBulkJobsClient = {
    MAX_RESPONSE_BYTES: MAX_RESPONSE_BYTES,
    MAX_JSON_INPUT_BYTES: MAX_JSON_INPUT_BYTES,
    readBoundedJson: readBoundedJson,
    parseResponse: parseResponse,
    parseItems: parseItems,
    buildExportPayload: buildExportPayload,
    buildActionPayload: buildActionPayload,
    identifier: identifier,
    idempotencyKey: idempotencyKey
  };

  if (typeof document === "undefined") { return; }

  document.addEventListener("DOMContentLoaded", function () {
    var shell = document.getElementById("consent-bulk-jobs-shell");
    var CW = global.LightworkConsentWorkbench;
    if (!shell || !CW) { return; }
    var $ = function (id) { return document.getElementById(id); };
    var apiBase = shell.dataset.apiBase;
    var consentBase = "/api/v1/consent";
    var canAudit = shell.dataset.canAudit === "true";
    var canAdmin = shell.dataset.canAdmin === "true";
    var operations = $("cbj-operations");
    var readinessDetail = $("cbj-readiness-detail");
    var status = $("cbj-operation-status");
    var form = $("cbj-create");
    var state = { status: null, jobs: [], credit: null, profiles: [], chosen: new Set(), policies: [], identifiers: [], receipts: [] };
    var WHAT = {
      export_receipts: "Export consent records", edit_preferences: "Change choices", delete_subject: "Erase stored identifiers",
      change_identifier_type: "Change how people are identified", reactivate: "Bring back earlier choices", clear_expiration: "Remove end dates"
    };
    var STATE_LABELS = { queued: ["Waiting", "warn"], running: ["Running", "info"], succeeded: ["Finished", "ok"], failed: ["Failed", "danger"], cancelled: ["Cancelled", "idle"] };

    // Requests go through the bounded client above; a refusal keeps only its
    // status so the person sees a plain sentence from mvErrorText.
    async function request(base, path, options) {
      var selected = options || {};
      var headers = { Accept: "application/json" };
      var body;
      if (Object.prototype.hasOwnProperty.call(selected, "body")) {
        headers["Content-Type"] = "application/json";
        body = JSON.stringify(selected.body);
      }
      var response = await fetch(base + path, {
        method: selected.method || "GET", headers: headers, body: body,
        credentials: "same-origin", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer"
      });
      return parseResponse(response);
    }
    function api(path, options) { return request(apiBase, path, options); }
    function say(text, isError) { CW.say(status, text, isError); }
    function fail(error, action) { return CW.fail(status, error, action); }
    // One change at a time. The clicked control stays enabled (and focused)
    // until the shared confirmation captures it; a handler disables it only
    // after the person confirms, by calling disableControls().
    var operationInFlight = false;
    function handle(button, work) {
      if (operationInFlight) {
        say("Another operation is already running. Wait for it to finish.", true);
        return Promise.resolve();
      }
      operationInFlight = true;
      shell.dataset.operationInFlight = "true";
      var wasDisabled = button ? button.disabled : false, controlsDisabled = false;
      function disableControls() { if (button && !controlsDisabled) { button.disabled = true; controlsDisabled = true; } }
      return Promise.resolve().then(function () {
        return work(disableControls);
      }).catch(function (error) { fail(error, "finish the change"); }).finally(function () {
        if (controlsDisabled) button.disabled = wasDisabled;
        operationInFlight = false;
        delete shell.dataset.operationInFlight;
      });
    }
    document.querySelectorAll("[data-cbj-close]").forEach(function (b) { b.addEventListener("click", function () { $(b.dataset.cbjClose).hidden = true; }); });

    async function refreshStatus() {
      operations.disabled = true;
      if (!canAudit) {
        CW.say(readinessDetail, "You need audit access to use bulk changes.", true);
        return false;
      }
      try {
        var result = await api("/status");
        state.status = result;
        var ready = result && result.ready === true;
        operations.disabled = !ready;
        if (!ready) {
          CW.say(readinessDetail, result && result.enabled === false ? "Bulk changes are turned off for this workspace." : "Bulk changes are not ready yet. An administrator needs to finish setting them up.", true);
          return false;
        }
        var ops = result.operations || {};
        var missing = Object.keys(WHAT).filter(function (k) { return ops[k] !== true; });
        CW.say(readinessDetail, missing.length ? "Some kinds of change are not set up yet: " + missing.map(function (k) { return WHAT[k].toLowerCase(); }).join(", ") + "." : "");
        form.querySelectorAll("input[name=operation]").forEach(function (radio) {
          radio.disabled = !canAdmin || ops[radio.value] !== true;
        });
        return true;
      } catch (error) {
        CW.fail(readinessDetail, error, "check whether bulk changes are available");
        return false;
      }
    }

    function progressText(job) {
      var text = job.processed_items + " of " + job.total_items + " done";
      if (job.failed_items) text += ", " + job.failed_items + " could not be changed";
      return text;
    }
    async function refreshJobs() {
      var filter = $("cbj-state-filter").value;
      try {
        var result = await api("?limit=200" + (filter ? "&state=" + encodeURIComponent(filter) : ""));
        state.jobs = result.jobs || [];
        CW.table($("cbj-job-list"), state.jobs, {
          label: "Bulk change", name: function (j) { return WHAT[j.operation] || CW.human(j.operation); },
          columns: [
            { label: "What", value: function (j) { return WHAT[j.operation] || CW.human(j.operation); } },
            { label: "People", value: function (j) { return String(j.total_items); } },
            { label: "Progress", value: progressText },
            { label: "Starts", value: function (j) { return j.scheduled_for ? CW.when(j.scheduled_for) : "As soon as possible"; } },
            { label: "Status", value: function (j) { var m = STATE_LABELS[j.state] || [CW.human(j.state), "idle"]; return CW.badge(j.cancel_requested && j.state !== "cancelled" ? "Cancelling" : m[0], m[1]); } }
          ],
          actions: [
            { label: "Details", run: function (j, b) { CW.busy(b, function () { return showDetail(j); }); } },
            { label: "Download", when: function (j) { return j.operation === "export_receipts" && j.state === "succeeded"; }, run: function (j) { global.location.assign(apiBase + "/" + encodeURIComponent(j.job_id) + "/download"); } },
            { label: "Run now", kind: "primary", when: function (j) { return canAdmin && (j.state === "queued" || j.state === "running"); }, run: function (j, b) { handle(b, function (disableControls) { return runJob(j, disableControls); }); } },
            { label: "Cancel", kind: "danger", when: function (j) { return canAdmin && (j.state === "queued" || j.state === "running") && !j.cancel_requested; }, run: function (j, b) { handle(b, function (disableControls) { return cancelJob(j, disableControls); }); } }
          ],
          empty: { title: "No bulk changes yet.", body: "Start a bulk change to export or change many people’s consent records at once.", action: canAdmin ? { label: "Start a bulk change", run: openCreate } : null }
        });
      } catch (error) {
        $("cbj-job-list").replaceChildren(CW.el("p", "danger-text", CW.errorText(error, "load the bulk changes")));
      }
    }

    async function showDetail(job) {
      var body = $("cbj-detail-body");
      $("cbj-detail-title").textContent = WHAT[job.operation] || "Bulk change details";
      body.replaceChildren(CW.el("p", "muted", "Loading…"));
      $("cbj-detail").hidden = false;
      try {
        var history = await api("/" + encodeURIComponent(job.job_id) + "/history?limit=100");
        body.replaceChildren();
        CW.facts(body, [
          ["Status", (STATE_LABELS[job.state] || [CW.human(job.state)])[0]],
          ["Progress", progressText(job)],
          ["Created", CW.when(job.created_at)],
          ["Starts", job.scheduled_for ? CW.when(job.scheduled_for) : "As soon as possible"]
        ]);
        var list = CW.el("ul");
        (history.events || []).forEach(function (event) { list.appendChild(CW.el("li", "", CW.when(event.created_at) + " – " + CW.human(event.event_type))); });
        body.appendChild(CW.el("h4", "", "History"));
        body.appendChild(list);
        CW.open($("cbj-detail"), null);
      } catch (error) { body.replaceChildren(CW.el("p", "danger-text", CW.errorText(error, "load the bulk change details"))); }
    }
    async function runJob(job, disableControls) {
      if (!(await CW.confirm("Run the next part of this bulk change now? It may change or delete people’s consent records.", "Run now"))) return;
      disableControls();
      try { await api("/" + encodeURIComponent(job.job_id) + "/run", { method: "POST", body: {} }); say("The bulk change ran its next part."); await refreshJobs(); }
      catch (error) { fail(error, "run the bulk change"); }
    }
    async function cancelJob(job, disableControls) {
      if (!(await CW.confirm("Cancel this bulk change? Changes already made are not undone.", "Cancel bulk change"))) return;
      disableControls();
      try { await api("/" + encodeURIComponent(job.job_id) + "/cancel", { method: "POST", body: { expected_revision: job.revision, idempotency_key: CW.reference() } }); say("Bulk change cancelled."); await refreshJobs(); }
      catch (error) { fail(error, "cancel the bulk change"); }
    }

    // ----- Start a bulk change -----
    function operation() { var r = form.querySelector("input[name=operation]:checked"); return r ? r.value : "export_receipts"; }
    function syncForm() {
      var op = operation();
      form.querySelectorAll("[data-for]").forEach(function (node) {
        var key = node.dataset.for;
        var show = key === op || (key === "people" && op !== "change_identifier_type") || (key === "identifiers" && op === "change_identifier_type") || (key === "receipts" && (op === "reactivate" || op === "clear_expiration"));
        node.hidden = !show;
      });
      var later = form.elements.when.value === "later";
      form.querySelector("[data-when=later]").hidden = !later;
    }
    function renderPeople() {
      var term = $("cbj-people-search").value.trim().toLowerCase(), host = $("cbj-people");
      host.replaceChildren();
      var shown = state.profiles.filter(function (p) { return !term || String(p.profile_id).toLowerCase().indexOf(term) >= 0; }).slice(0, 200);
      if (!state.profiles.length) host.appendChild(CW.el("p", "muted", "No people with consent records yet."));
      shown.forEach(function (p) {
        var label = CW.el("label"), input = document.createElement("input");
        input.type = "checkbox"; input.name = "person"; input.value = p.profile_id; input.checked = state.chosen.has(p.profile_id);
        input.addEventListener("change", function () { if (input.checked) state.chosen.add(p.profile_id); else state.chosen.delete(p.profile_id); countPeople(); });
        label.append(input, " " + p.profile_id + (p.identity_kind === "anonymous" ? " (anonymous)" : ""));
        host.appendChild(label);
      });
      countPeople();
    }
    function countPeople() {
      var n = state.chosen.size;
      $("cbj-people-count").textContent = n ? n + (n === 1 ? " person selected." : " people selected.") : "No one selected.";
    }
    async function readLines(file) {
      var text = await file.text();
      return text.split(/\r?\n/).map(function (line) { return line.trim(); }).filter(Boolean);
    }
    function profileById(id) { for (var i = 0; i < state.profiles.length; i++) if (state.profiles[i].profile_id === id) return state.profiles[i]; return null; }
    async function openCreate() {
      form.reset();
      form.elements.idempotency_key.value = CW.reference();
      state.chosen = new Set(); state.identifiers = []; state.receipts = [];
      $("cbj-receipts").replaceChildren();
      $("cbj-identifier-count").textContent = "No file chosen.";
      try {
        var results = await Promise.all([request(consentBase, "/profiles?limit=500"), request(consentBase, "/policies?limit=500")]);
        state.profiles = results[0].profiles || []; state.policies = results[1].policies || [];
      } catch (error) { fail(error, "load people and policies"); }
      var latest = {};
      state.policies.forEach(function (p) { if (!latest[p.policy_id] || latest[p.policy_id].version < p.version) latest[p.policy_id] = p; });
      CW.fill(form.elements.policy_key, Object.keys(latest).sort().map(function (k) { return { value: k + "|" + latest[k].version, label: CW.human(k) + " (version " + latest[k].version + ")" }; }));
      fillPurposes();
      CW.fill(form.elements.channel, CW.CHANNELS);
      CW.fill(form.elements.jurisdiction, CW.JURISDICTIONS);
      CW.fill(form.elements.country_code, CW.COUNTRIES);
      var first = form.querySelector("input[name=operation]:not(:disabled)");
      if (first) first.checked = true;
      renderPeople(); syncForm();
      CW.open(form, "input[name=operation]:checked");
    }
    function fillPurposes() {
      var key = form.elements.policy_key.value.split("|"), policy = state.policies.filter(function (p) { return p.policy_id === key[0] && String(p.version) === key[1]; })[0];
      CW.fill(form.elements.purpose_id, Object.keys((policy && policy.purposes) || {}).map(function (k) { return { value: k, label: CW.human(k) }; }));
    }
    async function loadReceipts() {
      var ids = Array.from(state.chosen);
      if (!ids.length) { say("Choose the people first.", true); return; }
      var host = $("cbj-receipts");
      host.replaceChildren(CW.el("p", "muted", "Loading…"));
      state.receipts = [];
      try {
        for (var i = 0; i < ids.length && i < 50; i++) {
          var exported = await request(consentBase, "/profiles/" + encodeURIComponent(ids[i]) + "/export?access_purpose=bulk-change-selection&receipt_limit=100");
          (exported.receipts || []).forEach(function (r) { state.receipts.push(r); });
        }
        host.replaceChildren();
        if (!state.receipts.length) host.appendChild(CW.el("p", "muted", "These people have no recorded choices."));
        state.receipts.forEach(function (r) {
          var label = CW.el("label"), input = document.createElement("input");
          input.type = "checkbox"; input.name = "receipt"; input.value = r.receipt_id;
          var choices = (r.choices || []).map(function (c) { return CW.human(c.purpose_id) + ": " + CW.human(c.action) + (c.expires_at ? " (ends " + CW.when(c.expires_at) + ")" : ""); }).join("; ");
          label.append(input, " " + r.profile_id + " – " + choices + " – recorded " + CW.when(r.created_at));
          host.appendChild(label);
        });
        if (ids.length > 50) host.appendChild(CW.el("p", "muted", "Only the first 50 people are shown. Start another bulk change for the rest."));
      } catch (error) { host.replaceChildren(CW.el("p", "danger-text", CW.errorText(error, "load their choices"))); }
    }
    function whenValue() {
      if (form.elements.when.value !== "later") return "";
      var seconds = CW.toSeconds(form.elements.scheduled_for.value);
      if (!(seconds > Date.now() / 1000)) throw Object.assign(new Error("when"), { plain: "Choose a start time in the future." });
      return new Date(seconds * 1000).toISOString();
    }
    async function buildItems(op) {
      var items = [], n = 0;
      function next() { n += 1; return "item-" + n; }
      if (op === "change_identifier_type") {
        if (!state.identifiers.length) throw Object.assign(new Error("ids"), { plain: "Upload the list of people and their new identifiers." });
        for (var k = 0; k < state.identifiers.length; k++) {
          var entry = state.identifiers[k];
          items.push({ item_id: next(), profile_id: entry.profile.profile_id, expected_revision: entry.profile.revision, identity_kind: form.elements.identity_kind.value, subject_sha256: await CW.sha256(entry.identifier) });
        }
        return items;
      }
      var people = Array.from(state.chosen).map(profileById).filter(Boolean);
      if (!people.length) throw Object.assign(new Error("people"), { plain: "Choose at least one person." });
      if (op === "export_receipts") return people.map(function (p) { return { item_id: next(), profile_id: p.profile_id, receipt_limit: Number(form.elements.receipt_limit.value) }; });
      if (op === "delete_subject") return people.map(function (p) { return { item_id: next(), profile_id: p.profile_id, expected_revision: p.revision, reason_code: form.elements.reason_code.value }; });
      if (op === "edit_preferences") {
        var key = form.elements.policy_key.value.split("|");
        if (!key[0] || !form.elements.purpose_id.value) throw Object.assign(new Error("policy"), { plain: "Choose a consent policy and a use of data." });
        return people.map(function (p) {
          return { item_id: next(), profile_id: p.profile_id, expected_revision: p.revision, policy_id: key[0], policy_version: Number(key[1]),
            context: { jurisdiction: form.elements.jurisdiction.value, country_code: form.elements.country_code.value, region_code: "", collection_point: "admin", asserted_source: "operator" },
            choices: [{ purpose_id: form.elements.purpose_id.value, action: form.elements.action.value, channel: form.elements.channel.value, vendor_id: "all" }] };
        });
      }
      var picked = Array.prototype.filter.call(form.querySelectorAll("input[name=receipt]"), function (c) { return c.checked; }).map(function (c) { return c.value; });
      if (!picked.length) throw Object.assign(new Error("receipts"), { plain: "Show the people’s choices and tick the ones to change." });
      var at = op === "reactivate" ? CW.toSeconds(form.elements.reactivation_at.value) : 0;
      if (op === "reactivate" && !(at > Date.now() / 1000)) throw Object.assign(new Error("at"), { plain: "Choose when to bring the choices back, in the future." });
      return picked.map(function (receiptId) {
        var receipt = state.receipts.filter(function (r) { return r.receipt_id === receiptId; })[0], person = profileById(receipt.profile_id);
        var item = { item_id: next(), profile_id: receipt.profile_id, expected_revision: person ? person.revision : receipt.profile_revision, receipt_id: receiptId };
        if (op === "reactivate") item.reactivation_at = at;
        return item;
      });
    }
    async function submitCreate(disableControls) {
      var op = operation();
      try {
        var items = await buildItems(op);
        var itemsJson = JSON.stringify(items);
        if (items.length > MAX_UI_ITEMS || new TextEncoder().encode(itemsJson).byteLength > MAX_JSON_INPUT_BYTES) {
          say("That is too many records for one bulk change. Choose fewer people and start another bulk change for the rest.", true);
          return;
        }
        var values = { items_json: itemsJson, idempotency_key: form.elements.idempotency_key.value, scheduled_for: whenValue() };
        var payload;
        if (op === "export_receipts") payload = buildExportPayload(values);
        else { values.operation = op; payload = buildActionPayload(values); }
        var count = payload.items.length;
        if (op !== "export_receipts" && !(await CW.confirm(WHAT[op] + " for " + count + (count === 1 ? " record" : " records") + "? This changes people’s consent records and cannot be undone by cancelling.", "Start bulk change"))) return;
        disableControls();
        await api(op === "export_receipts" ? "/exports" : "/actions", { method: "POST", body: payload });
        form.hidden = true;
        say("Bulk change started." + (op === "export_receipts" ? " Download the file from the list when it has finished." : ""));
        await Promise.all([refreshJobs(), refreshCredit()]);
      } catch (error) {
        if (error && error.plain) { say(error.plain, true); return; }
        if (error && error.code === "invalid_input") { say("Some of the details could not be used. Check your choices and try again.", true); return; }
        fail(error, "start the bulk change");
      }
    }

    // ----- Allowance and upkeep -----
    async function refreshCredit() {
      try {
        var credit = await api("/credits/detail");
        state.credit = credit;
        $("cbj-credit-detail").textContent = credit.available + " bulk changes can still be started (" + credit.reserved + " set aside for waiting changes, " + credit.consumed + " used).";
      } catch (error) { CW.fail($("cbj-credit-detail"), error, "load the allowance"); }
    }
    async function addCredits(disableControls) {
      if (!state.credit) return;
      var answers = await global.mvForm("Add to the allowance", [{ key: "amount", label: "How many more bulk changes", type: "select", required: true, default: "10", options: [{ value: "10", label: "10" }, { value: "50", label: "50" }, { value: "100", label: "100" }, { value: "500", label: "500" }] }], { okText: "Continue" });
      if (!answers) return;
      if (!(await CW.confirm("Allow " + answers.amount + " more bulk changes in this workspace?", "Add"))) return;
      disableControls();
      try { await api("/credits/grant", { method: "POST", body: { amount: Number(answers.amount), expected_revision: state.credit.revision, idempotency_key: CW.reference() } }); say("Allowance updated."); await refreshCredit(); }
      catch (error) { fail(error, "update the allowance"); }
    }

    $("cbj-new").addEventListener("click", function (event) { CW.busy(event.currentTarget, openCreate); });
    $("cbj-state-filter").addEventListener("change", refreshJobs);
    form.querySelectorAll("input[name=operation]").forEach(function (radio) { radio.addEventListener("change", syncForm); });
    form.elements.when.addEventListener("change", syncForm);
    form.elements.policy_key.addEventListener("change", fillPurposes);
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      handle(form.querySelector("button[type=submit]"), function (disableControls) { return submitCreate(disableControls); });
    });
    $("cbj-people-search").addEventListener("input", renderPeople);
    $("cbj-select-shown").addEventListener("click", function () { $("cbj-people").querySelectorAll("input[name=person]").forEach(function (c) { c.checked = true; state.chosen.add(c.value); }); countPeople(); });
    $("cbj-clear-people").addEventListener("click", function () { state.chosen = new Set(); renderPeople(); });
    $("cbj-people-file").addEventListener("change", async function (event) {
      var file = event.target.files && event.target.files[0];
      if (!file) return;
      var lines = await readLines(file), found = 0, missing = 0;
      lines.forEach(function (line) { var id = line.split(",")[0].trim(); if (profileById(id)) { state.chosen.add(id); found += 1; } else missing += 1; });
      renderPeople();
      say(found + " people added from the list." + (missing ? " " + missing + " lines did not match anyone." : ""), missing > 0);
    });
    $("cbj-identifier-file").addEventListener("change", async function (event) {
      var file = event.target.files && event.target.files[0];
      if (!file) return;
      var lines = await readLines(file), missing = 0;
      state.identifiers = [];
      lines.forEach(function (line) {
        var parts = line.split(","), profile = profileById((parts[0] || "").trim()), identifier = parts.slice(1).join(",").trim();
        if (profile && identifier) state.identifiers.push({ profile: profile, identifier: identifier }); else missing += 1;
      });
      $("cbj-identifier-count").textContent = state.identifiers.length + " people ready." + (missing ? " " + missing + " lines could not be matched." : "");
    });
    $("cbj-load-receipts").addEventListener("click", function (event) { CW.busy(event.currentTarget, loadReceipts); });
    $("cbj-add-credits").addEventListener("click", function (event) { handle(event.currentTarget, addCredits); });
    $("cbj-run-next").addEventListener("click", function (event) {
      handle(event.currentTarget, async function (disableControls) {
        if (!(await CW.confirm("Run the next bulk change that is due now? It may change or delete people’s consent records.", "Run next change"))) return;
        disableControls();
        try { await api("/worker/run-next", { method: "POST", body: {} }); say("The next due bulk change ran."); await refreshJobs(); }
        catch (error) { fail(error, "run the next bulk change"); }
      });
    });
    $("cbj-recover").addEventListener("click", function (event) {
      handle(event.currentTarget, async function (disableControls) {
        if (!(await CW.confirm("Restart bulk changes that stopped part-way? They will carry on from where they stopped.", "Restart"))) return;
        disableControls();
        try { await api("/operations/recover", { method: "POST", body: { limit: 100 } }); say("Stuck bulk changes restarted."); await refreshJobs(); }
        catch (error) { fail(error, "restart stuck bulk changes"); }
      });
    });
    $("cbj-verify-integrity").addEventListener("click", function (event) {
      CW.busy(event.currentTarget, async function () {
        try { var result = await api("/operations/integrity"); var ok = !(result && result.valid === false); say(ok ? "The history is intact." : "The check found a problem. Contact support before changing anything.", !ok); }
        catch (error) { fail(error, "check the history"); }
      });
    });
    $("cbj-retention-before").value = CW.toInput(Date.now() / 1000, true);
    $("cbj-retention").addEventListener("submit", function (event) {
      event.preventDefault();
      handle($("cbj-purge"), purgeOld);
    });
    async function purgeOld(disableControls) {
      var before = CW.toSeconds($("cbj-retention-before").value);
      if (!(before > 0)) { say("Choose a date.", true); return; }
      try {
        var due = await api("/operations/retention?limit=500&before=" + encodeURIComponent(String(before)));
        var count = (due.jobs || due.items || []).length;
        if (!count) { say("No finished bulk changes reached the end of their keeping period before " + CW.when(before) + "."); return; }
        if (!(await CW.confirm("Permanently delete " + count + " finished bulk changes from before " + CW.when(before) + "? They cannot be recovered.", "Delete"))) return;
        disableControls();
        await api("/operations/retention/purge", { method: "POST", body: { before: before, limit: 500 } });
        say("Old bulk changes deleted."); await refreshJobs();
      } catch (error) { fail(error, "delete old bulk changes"); }
    }

    refreshStatus().then(function (ready) {
      if (!ready) {
        $("cbj-job-list").replaceChildren(CW.el("p", "muted", "Bulk changes appear here once they are set up."));
        $("cbj-credit-detail").textContent = "";
        return null;
      }
      return Promise.all([refreshJobs(), refreshCredit()]);
    });
  });
})(typeof window !== "undefined" ? window : this);
