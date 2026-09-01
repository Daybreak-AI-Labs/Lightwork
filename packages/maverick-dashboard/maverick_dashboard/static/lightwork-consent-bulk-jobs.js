"use strict";

(function (global) {
  var MAX_RESPONSE_BYTES = 64 * 1024;
  var MAX_JSON_INPUT_BYTES = 48 * 1024;
  var MAX_RENDER_CHARS = 32 * 1024;
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
      throw failure("Server response stream is unavailable.", "response_stream_unavailable", response.status);
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
        : "Consent bulk-job request failed.";
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
      throw failure("Idempotency key must be 8-192 safe characters.", "invalid_input");
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

  function boundedText(value) {
    var text;
    try { text = JSON.stringify(value, null, 2); } catch (_error) { text = "Unable to render response."; }
    if (text.length <= MAX_RENDER_CHARS) { return text; }
    return text.slice(0, MAX_RENDER_CHARS) + "\n… output truncated by browser render bound";
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
    if (!shell) { return; }
    var apiBase = shell.dataset.apiBase;
    var canAudit = shell.dataset.canAudit === "true";
    var canAdmin = shell.dataset.canAdmin === "true";
    var operations = document.getElementById("cbj-operations");
    var statusMark = document.getElementById("cbj-status-mark");
    var readiness = document.getElementById("cbj-readiness");
    var readinessDetail = document.getElementById("cbj-readiness-detail");
    var statusOutput = document.getElementById("cbj-status-output");
    var operationStatus = document.getElementById("cbj-operation-status");
    var operationOutput = document.getElementById("cbj-operation-output");
    var jobList = document.getElementById("cbj-job-list");
    var listStatus = document.getElementById("cbj-list-status");
    var listCount = document.getElementById("cbj-list-count");
    var controlForm = document.getElementById("cbj-job-control-form");
    var download = document.getElementById("cbj-download");
    var downloadHint = document.getElementById("cbj-download-hint");
    var downloadUrl = "";
    var latestStatus = null;
    var operationInFlight = false;

    function formValues(form) { return Object.fromEntries(new FormData(form).entries()); }

    async function request(path, options) {
      var selected = options || {};
      var headers = { Accept: "application/json" };
      var body;
      if (Object.prototype.hasOwnProperty.call(selected, "body")) {
        headers["Content-Type"] = "application/json";
        body = JSON.stringify(selected.body);
      }
      var response = await fetch(apiBase + path, {
        method: selected.method || "GET",
        headers: headers,
        body: body,
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer"
      });
      return parseResponse(response);
    }

    function setOperation(label, payload) {
      operationStatus.textContent = label;
      operationOutput.textContent = boundedText(payload);
    }

    async function runOperation(label, functionValue) {
      operationStatus.textContent = label + "…";
      operationOutput.textContent = "";
      try {
        var result = await functionValue();
        setOperation(label + " complete.", result);
        return result;
      } catch (error) {
        operationStatus.textContent = label + " failed.";
        operationOutput.textContent = String(error && error.message ? error.message : "Request failed.").slice(0, 500);
        throw error;
      }
    }

    function confirmOperation(message, okText) {
      if (typeof global.mvConfirm === "function") {
        return global.mvConfirm(message, { okText: okText });
      }
      return Promise.resolve(global.confirm(message));
    }

    function handle(handler, options) {
      options = options || {};
      return function (event) {
        if (event) { event.preventDefault(); }
        if (operationInFlight) {
          operationStatus.textContent = "Another operation is already running. Wait for it to finish.";
          return;
        }
        operationInFlight = true;
        shell.dataset.operationInFlight = "true";
        var source = event && event.currentTarget;
        var controls = [];
        if (source && source.matches && source.matches("form")) {
          controls = Array.from(source.querySelectorAll('button[type="submit"], input[type="submit"]'));
        } else if (source && "disabled" in source) {
          controls = [source];
        }
        var disabledBefore = controls.map(function (control) { return control.disabled; });
        var controlsDisabled = false;
        function disableControls() {
          if (controlsDisabled) { return; }
          controls.forEach(function (control) { control.disabled = true; });
          controlsDisabled = true;
        }
        // Destructive handlers defer this until their confirmation resolves.
        // Keeping the invoking control enabled lets mvConfirm capture and
        // restore the real opener instead of falling back to document.body.
        if (!options.deferDisable) { disableControls(); }
        Promise.resolve().then(function () {
          return handler(event, disableControls);
        }).catch(function () {}).finally(function () {
          if (controlsDisabled) {
            controls.forEach(function (control, index) { control.disabled = disabledBefore[index]; });
          }
          operationInFlight = false;
          delete shell.dataset.operationInFlight;
        });
      };
    }

    function setCapabilityControls(status) {
      var actionSelect = document.querySelector('#cbj-action-form select[name="operation"]');
      Array.from(actionSelect.options).forEach(function (option) {
        option.disabled = !canAdmin || !status.operations || status.operations[option.value] !== true;
      });
      var exportControls = document.getElementById("cbj-export-controls");
      exportControls.disabled = !canAdmin || !status.operations || status.operations.export_receipts !== true;
      var actionControls = document.getElementById("cbj-action-controls");
      var selectedReady = status.operations && status.operations[actionSelect.value] === true;
      actionControls.disabled = !canAdmin || !selectedReady;
    }

    function renderStatus(status) {
      latestStatus = status;
      statusOutput.textContent = boundedText(status);
      var ready = status && status.ready === true;
      var capabilityValues = status && status.operations ? Object.values(status.operations) : [];
      var complete = capabilityValues.length > 0 && capabilityValues.every(function (value) { return value === true; });
      operations.disabled = !(ready && canAudit);
      if (!ready) {
        readiness.textContent = "Not ready";
        statusMark.dataset.state = "failed";
        statusMark.textContent = "Closed";
        readinessDetail.textContent = "The tenant composition is disabled, incomplete, or unavailable. All controls remain closed.";
      } else if (!complete) {
        readiness.textContent = "Partially ready";
        statusMark.dataset.state = "partial";
        statusMark.textContent = "Partial";
        readinessDetail.textContent = "Exports and installed actions are available. Missing explicit mutation primitives remain closed.";
      } else {
        readiness.textContent = "Ready";
        statusMark.dataset.state = "ready";
        statusMark.textContent = "Ready";
        readinessDetail.textContent = "Tenant runtime and every explicit executor report ready.";
      }
      if (ready) { setCapabilityControls(status); }
    }

    async function refreshStatus() {
      operations.disabled = true;
      if (!canAudit) {
        renderStatus({ enabled: false, configured: false, ready: false, operations: {} });
        readinessDetail.textContent = "Global audit authority is required to read this administrative surface.";
        return;
      }
      readiness.textContent = "Checking…";
      statusMark.dataset.state = "checking";
      statusMark.textContent = "Checking";
      try {
        renderStatus(await request("/status"));
      } catch (error) {
        renderStatus({ enabled: false, configured: false, ready: false, operations: {} });
        readinessDetail.textContent = String(error.message || "Readiness check failed.").slice(0, 500);
      }
    }

    function addDefinition(list, term, value) {
      var dt = document.createElement("dt");
      var dd = document.createElement("dd");
      dt.textContent = term;
      dd.textContent = String(value === null || value === undefined ? "—" : value).slice(0, 300);
      list.appendChild(dt);
      list.appendChild(dd);
    }

    function makeDownloadAvailable(jobId) {
      downloadUrl = apiBase + "/" + encodeURIComponent(jobId) + "/download";
      download.disabled = !canAudit;
      downloadHint.textContent = canAudit
        ? "Export ready for job " + String(jobId).slice(0, 128) + "."
        : "Global audit authority is required to download this export.";
    }

    function selectJob(jobId, revision) {
      controlForm.elements.job_id.value = jobId;
      controlForm.elements.expected_revision.value = revision || "";
      makeDownloadAvailable(jobId);
    }

    function renderJobs(payload) {
      jobList.replaceChildren();
      var jobs = payload && Array.isArray(payload.jobs) ? payload.jobs.slice(0, 100) : [];
      jobs.forEach(function (job) {
        var article = document.createElement("article");
        article.className = "cbj-job";
        var head = document.createElement("div");
        head.className = "cbj-job-head";
        var title = document.createElement("h3");
        title.textContent = String(job.job_id || "Unknown job").slice(0, 128);
        var state = document.createElement("span");
        state.className = "cbj-pill";
        state.textContent = String(job.state || "unknown").slice(0, 24);
        head.appendChild(title);
        head.appendChild(state);
        var detail = document.createElement("dl");
        addDefinition(detail, "Operation", job.operation);
        addDefinition(detail, "Progress", String(job.progress_percent) + "%");
        addDefinition(detail, "Items", String(job.processed_items) + " / " + String(job.total_items));
        addDefinition(detail, "Failures", job.failed_items);
        addDefinition(detail, "Revision", job.revision);
        var choose = document.createElement("button");
        choose.type = "button";
        choose.className = "btn";
        choose.textContent = "Select job";
        choose.addEventListener("click", function () { selectJob(job.job_id, job.revision); });
        article.appendChild(head);
        article.appendChild(detail);
        article.appendChild(choose);
        jobList.appendChild(article);
      });
      if (jobs.length === 0) {
        var empty = document.createElement("p");
        empty.className = "cbj-empty";
        empty.textContent = "No jobs matched the bounded query.";
        jobList.appendChild(empty);
      }
      listCount.textContent = jobs.length + (jobs.length === 1 ? " job" : " jobs");
      listStatus.textContent = payload && payload.truncated
        ? "Showing a bounded subset; refine the state filter."
        : "Job register refreshed.";
    }

    async function refreshJobs() {
      var values = formValues(document.getElementById("cbj-list-form"));
      var limit = integer(values.limit, "Result limit", 1, 100);
      var path = "?limit=" + encodeURIComponent(String(limit));
      if (values.state) {
        if (!STATES.has(values.state)) { throw failure("State is invalid.", "invalid_input"); }
        path += "&state=" + encodeURIComponent(values.state);
      }
      renderJobs(await request(path));
    }

    function selectedJobId() {
      return identifier(controlForm.elements.job_id.value, "Job ID");
    }

    document.getElementById("cbj-refresh-status").addEventListener("click", handle(refreshStatus));
    document.getElementById("cbj-list-form").addEventListener("submit", handle(refreshJobs));
    document.getElementById("cbj-export-form").addEventListener("submit", handle(async function () {
      var payload = buildExportPayload(formValues(document.getElementById("cbj-export-form")));
      var result = await runOperation("Create export job", function () {
        return request("/exports", { method: "POST", body: payload });
      });
      selectJob(result.job_id, result.revision);
      await refreshJobs();
    }));
    document.getElementById("cbj-action-form").addEventListener("submit", handle(async function (_event, disableControls) {
      var payload = buildActionPayload(formValues(document.getElementById("cbj-action-form")));
      if (!(await confirmOperation(
        "Create exactly one “" + payload.operation.replace(/_/g, " ") + "” bulk job for " +
          payload.items.length + (payload.items.length === 1 ? " item" : " items") +
          "? The queued job may execute automatically when it becomes due.",
        "Create action job"
      ))) { return; }
      disableControls();
      var result = await runOperation("Create action job", function () {
        return request("/actions", { method: "POST", body: payload });
      });
      selectJob(result.job_id, result.revision);
      await refreshJobs();
    }, { deferDisable: true }));
    document.querySelector('#cbj-action-form select[name="operation"]').addEventListener("change", function (event) {
      if (latestStatus) {
        document.getElementById("cbj-action-controls").disabled = !canAdmin
          || !latestStatus.operations
          || latestStatus.operations[event.target.value] !== true;
      }
    });
    document.getElementById("cbj-load-detail").addEventListener("click", handle(async function () {
      var jobId = selectedJobId();
      await runOperation("Load job detail", function () { return request("/" + encodeURIComponent(jobId)); });
      makeDownloadAvailable(jobId);
    }));
    document.getElementById("cbj-load-progress").addEventListener("click", handle(async function () {
      var jobId = selectedJobId();
      await runOperation("Load job progress", function () { return request("/" + encodeURIComponent(jobId) + "/progress"); });
      makeDownloadAvailable(jobId);
    }));
    document.getElementById("cbj-load-history").addEventListener("click", handle(async function () {
      var jobId = selectedJobId();
      await runOperation("Load immutable history", function () { return request("/" + encodeURIComponent(jobId) + "/history?limit=100"); });
      makeDownloadAvailable(jobId);
    }));
    document.getElementById("cbj-run-job").addEventListener("click", handle(async function (_event, disableControls) {
      var jobId = selectedJobId();
      if (!(await confirmOperation(
        "Run the next executable batch for consent bulk job “" + jobId + "” now? Its queued action may change or delete consent records.",
        "Run this job"
      ))) { return; }
      disableControls();
      var result = await runOperation("Run job batch", function () {
        return request("/" + encodeURIComponent(jobId) + "/run", { method: "POST", body: {} });
      });
      selectJob(jobId, result.revision);
      await refreshJobs();
    }, { deferDisable: true }));
    document.getElementById("cbj-cancel-job").addEventListener("click", handle(async function (_event, disableControls) {
      var jobId = selectedJobId();
      var payload = {
        expected_revision: integer(controlForm.elements.expected_revision.value, "Expected revision", 1, 2147483647),
        idempotency_key: idempotencyKey(controlForm.elements.idempotency_key.value)
      };
      if (!(await confirmOperation(
        "Cancel consent bulk job “" + jobId + "” at revision " + payload.expected_revision +
          "? Completed items will not be rolled back.",
        "Cancel this job"
      ))) { return; }
      disableControls();
      var result = await runOperation("Cancel job", function () {
        return request("/" + encodeURIComponent(jobId) + "/cancel", { method: "POST", body: payload });
      });
      selectJob(jobId, result.revision);
      await refreshJobs();
    }, { deferDisable: true }));
    controlForm.elements.job_id.addEventListener("input", function () {
      downloadUrl = "";
      download.disabled = true;
      downloadHint.textContent = "Select or load this job before downloading its export.";
    });
    download.addEventListener("click", function () {
      if (download.disabled || !canAudit || !downloadUrl) { return; }
      global.location.assign(downloadUrl);
    });
    document.getElementById("cbj-load-credits").addEventListener("click", handle(async function () {
      var result = await runOperation("Load credit detail", function () { return request("/credits/detail"); });
      document.getElementById("cbj-credit-detail").textContent =
        "Available " + result.available + " · reserved " + result.reserved + " · consumed " + result.consumed + " · revision " + result.revision;
      document.querySelector('#cbj-credit-form input[name="expected_revision"]').value = result.revision;
    }));
    document.getElementById("cbj-credit-form").addEventListener("submit", handle(async function (_event, disableControls) {
      var values = formValues(document.getElementById("cbj-credit-form"));
      var payload = {
        amount: integer(values.amount, "Credit amount", 1, 1000000000),
        expected_revision: integer(values.expected_revision, "Credit revision", 1, 2147483647),
        idempotency_key: idempotencyKey(values.idempotency_key)
      };
      if (!(await confirmOperation(
        "Grant exactly " + payload.amount + " bulk-operation credits at ledger revision " +
          payload.expected_revision + "? This changes the tenant credit balance.",
        "Grant credits"
      ))) { return; }
      disableControls();
      var result = await runOperation("Grant credits", function () {
        return request("/credits/grant", { method: "POST", body: payload });
      });
      document.querySelector('#cbj-credit-form input[name="expected_revision"]').value = result.revision;
    }, { deferDisable: true }));
    document.getElementById("cbj-run-next").addEventListener("click", handle(async function (_event, disableControls) {
      if (!(await confirmOperation(
        "Run exactly the next due consent bulk job now? Its queued action may change or delete consent records.",
        "Run next due job"
      ))) { return; }
      disableControls();
      await runOperation("Run next due job", function () {
        return request("/worker/run-next", { method: "POST", body: {} });
      });
      await refreshJobs();
    }, { deferDisable: true }));
    document.getElementById("cbj-verify-integrity").addEventListener("click", handle(function () {
      return runOperation("Verify immutable event chain", function () { return request("/operations/integrity"); });
    }));
    document.getElementById("cbj-recover").addEventListener("click", handle(async function (_event, disableControls) {
      var limit = integer(document.getElementById("cbj-maintenance-limit").value, "Recovery limit", 1, 500);
      var confirmed = await confirmOperation(
        "Recover up to " + limit + " expired consent worker leases? Recovered jobs can become runnable again.",
        "Recover leases"
      );
      if (!confirmed) { return null; }
      disableControls();
      return runOperation("Recover expired worker leases", function () {
        return request("/operations/recover", { method: "POST", body: { limit: limit } });
      });
    }, { deferDisable: true }));
    document.getElementById("cbj-retention-due").addEventListener("click", handle(function () {
      var limit = integer(document.getElementById("cbj-maintenance-limit").value, "Retention limit", 1, 500);
      var before = optionalTimestamp(document.getElementById("cbj-retention-before").value, "Retention cutoff");
      var path = "/operations/retention?limit=" + encodeURIComponent(String(limit));
      if (before !== null) { path += "&before=" + encodeURIComponent(String(before)); }
      return runOperation("Review retention-due jobs", function () { return request(path); });
    }));
    document.getElementById("cbj-purge").addEventListener("click", handle(async function (_event, disableControls) {
      var limit = integer(document.getElementById("cbj-maintenance-limit").value, "Purge limit", 1, 500);
      var before = optionalTimestamp(document.getElementById("cbj-retention-before").value, "Retention cutoff");
      var body = { limit: limit };
      if (before !== null) { body.before = before; }
      var cutoff = before === null ? "the server retention cutoff" : new Date(before * 1000).toISOString();
      if (!(await confirmOperation(
        "Permanently purge up to " + limit + " retention-due consent jobs before " + cutoff +
          "? Purged job records cannot be recovered from this interface.",
        "Purge these jobs"
      ))) { return; }
      disableControls();
      await runOperation("Purge retention-due jobs", function () {
        return request("/operations/retention/purge", { method: "POST", body: body });
      });
      await refreshJobs();
    }, { deferDisable: true }));

    refreshStatus().then(function () {
      if (!operations.disabled) {
        return Promise.all([refreshJobs(), request("/credits/detail").then(function (credit) {
          document.getElementById("cbj-credit-detail").textContent =
            "Available " + credit.available + " · reserved " + credit.reserved + " · consumed " + credit.consumed + " · revision " + credit.revision;
          document.querySelector('#cbj-credit-form input[name="expected_revision"]').value = credit.revision;
        })]);
      }
      return null;
    }).catch(function (error) {
      setOperation("Initial bounded load failed.", { detail: String(error.message || "Request failed.").slice(0, 500) });
    });
  });
})(typeof window !== "undefined" ? window : this);
