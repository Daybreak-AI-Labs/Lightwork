(function (global) {
  "use strict";

  var MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
  var MAX_RENDER_CHARS = 256 * 1024;
  var MAX_INPUT_BYTES = 32 * 1024;
  var ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
  var OPERATION_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/;
  var ENVIRONMENT_REF = /^[A-Z][A-Z0-9_]{1,127}$/;
  var DIGEST = /^[0-9a-f]{64}$/;

  function problem(message, code, status) {
    var error = new Error(message);
    error.code = code || "invalid_input";
    error.status = status || 0;
    return error;
  }

  function identifier(value, label, operation) {
    var selected = String(value || "").trim();
    if (!(operation ? OPERATION_ID : ID).test(selected)) {
      throw problem(label + " is invalid.");
    }
    return selected;
  }

  function environmentReference(value, label) {
    var selected = String(value || "").trim();
    if (!ENVIRONMENT_REF.test(selected)) {
      throw problem(label + " must be an environment-variable name, not a credential value.");
    }
    return selected;
  }

  function savedReference(value, label, evidence) {
    var selected = String(value || "").trim();
    var pattern = evidence ? /^evidence-key:[a-z0-9][a-z0-9_-]{0,63}:[1-9][0-9]{0,9}$/ : /^connection:[a-z0-9][a-z0-9_-]{0,63}:[0-9a-f]{32}$/;
    if (!pattern.test(selected)) { return environmentReference(selected, label); }
    return selected;
  }

  function digest(value, label) {
    var selected = String(value || "").trim();
    if (!DIGEST.test(selected)) {
      throw problem(label + " must be a lowercase SHA-256 digest.");
    }
    return selected;
  }

  function finiteNumber(value, label, minimum, maximum, integerOnly) {
    var selected = Number(value);
    if (!Number.isFinite(selected) || (integerOnly && !Number.isSafeInteger(selected)) || selected < minimum || selected > maximum) {
      throw problem(label + " is outside its bound.");
    }
    return selected;
  }

  function parseBoundedObject(value, label) {
    var text = String(value || "");
    if (!text || new TextEncoder().encode(text).length > MAX_INPUT_BYTES) {
      throw problem(label + " is empty or exceeds its byte bound.");
    }
    var parsed;
    try { parsed = JSON.parse(text); }
    catch (_error) { throw problem(label + " is not valid JSON."); }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw problem(label + " must be a JSON object.");
    }
    return parsed;
  }

  async function readBoundedJson(response) {
    var declared = response.headers.get("content-length");
    if (declared !== null && Number(declared) > MAX_RESPONSE_BYTES) {
      if (response.body && typeof response.body.cancel === "function") {
        await response.body.cancel();
      }
      throw problem("Server response exceeded the 2 MiB safety limit.", "response_too_large", response.status);
    }
    if (!response.body || typeof response.body.getReader !== "function") {
      throw problem("Server response stream is unavailable.", "response_stream_unavailable", response.status);
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
        throw problem("Server response exceeded the 2 MiB safety limit.", "response_too_large", response.status);
      }
      chunks.push(part.value);
    }
    var bytes = new Uint8Array(total);
    var offset = 0;
    chunks.forEach(function (chunk) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    });
    var text;
    try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
    catch (_error) { throw problem("Server response was not valid UTF-8.", "invalid_response", response.status); }
    var payload;
    try { payload = text ? JSON.parse(text) : {}; }
    catch (_error) { throw problem("Request failed: the server returned an invalid JSON response.", "invalid_response", response.status); }
    if (!response.ok) {
      var detail = payload && typeof payload.detail === "string"
        ? payload.detail.slice(0, 500)
        : "Privacy discovery request failed.";
      throw problem(detail, "request_failed", response.status);
    }
    return payload;
  }

  global.LightworkPrivacyDataDiscoveryClient = {
    MAX_INPUT_BYTES: MAX_INPUT_BYTES,
    MAX_RESPONSE_BYTES: MAX_RESPONSE_BYTES,
    digest: digest,
    environmentReference: environmentReference,
    savedReference: savedReference,
    finiteNumber: finiteNumber,
    identifier: identifier,
    parseBoundedObject: parseBoundedObject,
    readBoundedJson: readBoundedJson
  };

  if (typeof document === "undefined") { return; }
  var root = document.getElementById("pdd-root");
  if (!root) { return; }
  if (root.dataset.clientBound === "true") { return; }
  root.dataset.clientBound = "true";
  var apiBase = root.dataset.apiBase || "/api/v1/privacy/data-discovery";
  var status = document.getElementById("pdd-status");
  var result = document.getElementById("pdd-result");

  function formValue(form, name) {
    return String(form.elements[name].value || "").trim();
  }

  function optionalNumber(form, name) {
    var selected = formValue(form, name);
    return selected ? finiteNumber(selected, name, 0.001, 100000000000, false) : null;
  }

  async function call(path, options) {
    if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//") || path.length > 4096) {
      throw problem("Request path is invalid.");
    }
    var settings = Object.assign(
      { credentials: "same-origin", headers: { Accept: "application/json" } },
      options || {}
    );
    if (settings.body !== undefined) {
      settings.headers["Content-Type"] = "application/json";
    }
    return readBoundedJson(await fetch(apiBase + path, settings));
  }

  function show(payload, message) {
    var rendered = JSON.stringify(payload, null, 2);
    if (rendered.length > MAX_RENDER_CHARS) {
      rendered = rendered.slice(0, MAX_RENDER_CHARS) + "\n… display truncated at 256 KiB; use the bounded API for the complete response.";
    }
    result.textContent = rendered;
    status.textContent = message;
  }

  function fail(error) {
    var message = String(error && error.message ? error.message : error).slice(0, 500);
    status.textContent = message;
    result.textContent = JSON.stringify({ error: message }, null, 2);
  }

  function bind(id, eventName, handler) {
    var element = document.getElementById(id);
    if (!element) { throw problem("The workbench is missing " + id + ".", "invalid_page"); }
    element.addEventListener(eventName, function (event) {
      try { Promise.resolve(handler(event)).catch(fail); }
      catch (error) { fail(error); }
    });
  }

  function applyPermissions() {
    var allowed = {
      operate: root.dataset.canOperate === "true",
      audit: root.dataset.canAudit === "true",
      admin: root.dataset.canAdmin === "true"
    };
    root.querySelectorAll("[data-requires]").forEach(function (control) {
      var permitted = allowed[control.dataset.requires] === true;
      if (!permitted) {
        control.disabled = true;
        control.dataset.permissionDisabled = "true";
        control.setAttribute("aria-disabled", "true");
        control.title = control.dataset.requires + " permission is required";
      }
    });
  }

  function requirePermission(name) {
    var attribute = "can" + name.charAt(0).toUpperCase() + name.slice(1);
    if (root.dataset[attribute] !== "true") {
      throw problem(name + " permission is required.", "permission_required", 403);
    }
  }

  bind("pdd-source", "submit", async function (event) {
    event.preventDefault();
    requirePermission("admin");
    var form = event.currentTarget;
    var sourceId = identifier(formValue(form, "source_id"), "Source ID", false);
    var payload = {
      kind: formValue(form, "kind"),
      provider: identifier(formValue(form, "provider"), "Provider", false),
      origin: formValue(form, "origin"),
      resource_ref: identifier(formValue(form, "resource_ref"), "Resource reference", false),
      credential_env: savedReference(formValue(form, "credential_env"), "Credential reference", false),
      evidence_key_env: savedReference(formValue(form, "evidence_key_env"), "Evidence-key reference", true),
      expected_revision: finiteNumber(formValue(form, "expected_revision"), "Expected revision", 0, 2147483647, true),
      idempotency_key: identifier(formValue(form, "idempotency_key"), "Idempotency key", true)
    };
    var saved = await call("/sources/" + encodeURIComponent(sourceId), { method: "PUT", body: JSON.stringify(payload) });
    form.elements.expected_revision.value = saved.revision;
    newRequest(form);
    show(saved, "Source settings saved. Test the saved source to check bounded read access.");
  });

  bind("pdd-list-sources", "click", async function () {
    show(await call("/sources?limit=500"), "Workspace sources loaded.");
  });

  bind("pdd-source-state", "submit", async function (event) {
    event.preventDefault();
    requirePermission("admin");
    var form = event.currentTarget;
    var sourceId = identifier(formValue(form, "source_id"), "Source ID", false);
    var payload = {
      state: formValue(form, "state"),
      expected_revision: finiteNumber(formValue(form, "expected_revision"), "Expected revision", 1, 2147483647, true),
      idempotency_key: identifier(formValue(form, "idempotency_key"), "Idempotency key", true)
    };
    show(await call("/sources/" + encodeURIComponent(sourceId) + "/state", { method: "POST", body: JSON.stringify(payload) }), "Source state changed under revision control.");
  });

  bind("pdd-schedule", "submit", async function (event) {
    event.preventDefault();
    requirePermission("admin");
    var form = event.currentTarget;
    var scheduleId = identifier(formValue(form, "schedule_id"), "Schedule ID", false);
    var payload = {
      source_id: identifier(formValue(form, "source_id"), "Source ID", false),
      interval_seconds: finiteNumber(formValue(form, "interval_seconds"), "Interval", 1, 31536000, true),
      limits: parseBoundedObject(formValue(form, "limits"), "Scan limits"),
      max_attempts: finiteNumber(formValue(form, "max_attempts"), "Maximum attempts", 1, 20, true),
      retention_seconds: finiteNumber(formValue(form, "retention_seconds"), "Retention", 1, 315360000, true),
      active: formValue(form, "active") === "true",
      expected_revision: finiteNumber(formValue(form, "expected_revision"), "Expected revision", 0, 2147483647, true),
      idempotency_key: identifier(formValue(form, "idempotency_key"), "Idempotency key", true),
      next_run_at: optionalNumber(form, "next_run_at")
    };
    show(await call("/schedules/" + encodeURIComponent(scheduleId), { method: "PUT", body: JSON.stringify(payload) }), "Bounded schedule stored. Provider I/O has not started.");
  });

  bind("pdd-list-schedules", "click", async function () {
    show(await call("/schedules?limit=500"), "Schedules loaded.");
  });

  bind("pdd-enqueue-due", "click", async function () {
    requirePermission("operate");
    show(await call("/schedules/enqueue-due", { method: "POST", body: JSON.stringify({ limit: 100 }) }), "Due jobs enqueued without provider I/O.");
  });

  bind("pdd-manual", "submit", async function (event) {
    event.preventDefault();
    requirePermission("operate");
    var form = event.currentTarget;
    var sourceId = identifier(formValue(form, "source_id"), "Source ID", false);
    var payload = {
      limits: parseBoundedObject(formValue(form, "limits"), "Scan limits"),
      max_attempts: finiteNumber(formValue(form, "max_attempts"), "Maximum attempts", 1, 20, true),
      retention_seconds: finiteNumber(formValue(form, "retention_seconds"), "Retention", 1, 315360000, true),
      idempotency_key: identifier(formValue(form, "idempotency_key"), "Idempotency key", true)
    };
    show(await call("/sources/" + encodeURIComponent(sourceId) + "/jobs", { method: "POST", body: JSON.stringify(payload) }), "Manual scan queued; provider I/O has not started.");
  });

  bind("pdd-claim", "click", async function () {
    requirePermission("operate");
    show(await call("/jobs/claim", { method: "POST", body: JSON.stringify({ limit: 10, lease_seconds: 600.0 }) }), "Due jobs leased to this authenticated principal.");
  });

  bind("pdd-list-jobs", "click", async function () {
    show(await call("/jobs?limit=500"), "Jobs loaded.");
  });

  bind("pdd-job", "submit", async function (event) {
    event.preventDefault();
    var form = event.currentTarget;
    var action = event.submitter && event.submitter.value ? event.submitter.value : "execute";
    if (["execute", "fail", "replay"].indexOf(action) < 0) { throw problem("Job action is invalid."); }
    requirePermission(action === "fail" ? "operate" : "admin");
    var jobId = identifier(formValue(form, "job_id"), "Job ID", false);
    var payload;
    if (action === "fail") {
      payload = {
        error_code: formValue(form, "error_code"),
        base_backoff_seconds: finiteNumber(formValue(form, "base_backoff_seconds"), "Backoff", 0.1, 3600, false)
      };
    } else if (action === "replay") {
      payload = {
        expected_revision: finiteNumber(formValue(form, "expected_revision"), "Expected revision", 1, 2147483647, true),
        idempotency_key: identifier(formValue(form, "idempotency_key"), "Idempotency key", true)
      };
    }
    if (action === "execute" && !global.confirm("Run the stock provider adapter against the exact enrolled source now? This performs bounded external reads.")) { return; }
    var options = { method: "POST" };
    if (payload !== undefined) { options.body = JSON.stringify(payload); }
    var response = await call("/jobs/" + encodeURIComponent(jobId) + "/" + action, options);
    show(response, action === "execute" ? "Provider scan returned. Inspect completeness and truncation before review." : "Job lifecycle updated.");
  });

  bind("pdd-read", "submit", async function (event) {
    event.preventDefault();
    var form = event.currentTarget;
    var action = event.submitter && event.submitter.value ? event.submitter.value : "inventory";
    var source = formValue(form, "source_id");
    var report = formValue(form, "report_id");
    var base = formValue(form, "base_report_id");
    var target = formValue(form, "target_report_id");
    var path = "/inventory";
    if (action === "inventory" && source) { path += "?source_id=" + encodeURIComponent(identifier(source, "Source ID", false)); }
    else if (action === "reports") { path = "/reports?limit=500" + (source ? "&source_id=" + encodeURIComponent(identifier(source, "Source ID", false)) : ""); }
    else if (action === "report") { path = "/reports/" + encodeURIComponent(identifier(report, "Report ID", false)); }
    else if (action === "findings") { path = "/reports/" + encodeURIComponent(identifier(report, "Report ID", false)) + "/findings?limit=500"; }
    else if (action === "diff") {
      path = "/reports/diff?base_report_id=" + encodeURIComponent(identifier(base, "Base report ID", false)) + "&target_report_id=" + encodeURIComponent(identifier(target, "Target report ID", false)) + "&limit=1000";
    } else if (action !== "inventory") { throw problem("Read action is invalid."); }
    show(await call(path), "Discovery findings loaded without source content. No findings does not prove that no personal data exists.");
  });

  bind("pdd-propose", "submit", async function (event) {
    event.preventDefault();
    requirePermission("admin");
    var form = event.currentTarget;
    var findingId = identifier(formValue(form, "finding_id"), "Finding ID", false);
    var effective = formValue(form, "effective_category");
    var payload = {
      decision: formValue(form, "decision"),
      effective_category: effective || null,
      freshness_seconds: finiteNumber(formValue(form, "freshness_seconds"), "Freshness", 1, 31536000, true),
      expected_finding_revision: finiteNumber(formValue(form, "expected_finding_revision"), "Finding revision", 1, 2147483647, true),
      idempotency_key: identifier(formValue(form, "idempotency_key"), "Idempotency key", true)
    };
    show(await call("/findings/" + encodeURIComponent(findingId) + "/reviews", { method: "POST", body: JSON.stringify(payload) }), "Review proposed. No data-map delivery occurs before independent approval.");
  });

  bind("pdd-approve", "submit", async function (event) {
    event.preventDefault();
    requirePermission("admin");
    var form = event.currentTarget;
    var reviewId = identifier(formValue(form, "review_id"), "Review ID", false);
    var payload = {
      expected_version: finiteNumber(formValue(form, "expected_version"), "Expected version", 1, 2147483647, true),
      expected_integrity_sha256: digest(formValue(form, "expected_integrity_sha256"), "Review integrity"),
      idempotency_key: identifier(formValue(form, "idempotency_key"), "Idempotency key", true)
    };
    show(await call("/reviews/" + encodeURIComponent(reviewId) + "/approve", { method: "POST", body: JSON.stringify(payload) }), "Four-eyes review approved. Inspect durable outbox acknowledgement and reconcile the data map separately.");
  });

  bind("pdd-remediation-worker", "submit", async function (event) {
    event.preventDefault();
    requirePermission("admin");
    var form = event.currentTarget;
    var payload = {
      limit: finiteNumber(formValue(form, "limit"), "Worker batch limit", 1, 100, true),
      lease_seconds: finiteNumber(formValue(form, "lease_seconds"), "Lease seconds", 1, 3600, true)
    };
    show(await call("/remediation/worker/run-once", { method: "POST", body: JSON.stringify(payload) }), "Bounded remediation worker completed. Inspect applied, retry, ambiguous, and dead-letter counts.");
  });

  bind("pdd-remediation-list", "submit", async function (event) {
    event.preventDefault();
    requirePermission("admin");
    var form = event.currentTarget;
    var state = formValue(form, "state");
    var admitted = ["", "queued", "leased", "applied", "no_change", "ambiguous", "dead_letter", "cancelled"];
    if (admitted.indexOf(state) === -1) { throw problem("Work state is invalid."); }
    var limit = finiteNumber(formValue(form, "limit"), "List limit", 1, 100, true);
    var query = "?limit=" + encodeURIComponent(String(limit));
    if (state) { query += "&state=" + encodeURIComponent(state); }
    show(await call("/remediation/work" + query), "Remediation tasks loaded. Select an uncertain result and its current version to check what happened.");
  });

  bind("pdd-remediation-reconcile", "submit", async function (event) {
    event.preventDefault();
    requirePermission("admin");
    var form = event.currentTarget;
    var workId = identifier(formValue(form, "work_id"), "Work ID", false);
    var payload = {
      expected_revision: finiteNumber(formValue(form, "expected_revision"), "Expected revision", 1, 2147483647, true),
      absent_backoff_seconds: finiteNumber(formValue(form, "absent_backoff_seconds"), "Absent backoff", 0, 86400, true),
      idempotency_key: identifier(formValue(form, "idempotency_key"), "Idempotency key", true)
    };
    show(await call("/remediation/work/" + encodeURIComponent(workId) + "/reconcile", { method: "POST", body: JSON.stringify(payload) }), "Exact digest-only data-map reconciliation completed without optimistic replay.");
  });

  bind("pdd-remediation-maintenance", "submit", async function (event) {
    event.preventDefault();
    requirePermission("admin");
    var form = event.currentTarget;
    var payload = {
      idempotency_key: identifier(formValue(form, "idempotency_key"), "Idempotency key", true),
      target_limit: finiteNumber(formValue(form, "target_limit"), "Target purge limit", 1, 1000, true)
    };
    show(await call("/remediation/maintenance", { method: "POST", body: JSON.stringify(payload) }), "Remediation retention and integrity maintenance completed.");
  });

  bind("pdd-retention", "submit", async function (event) {
    event.preventDefault();
    requirePermission("admin");
    var form = event.currentTarget;
    if (!global.confirm("Permanently delete expired reports? Audit records of the deletions will remain.")) { return; }
    var payload = { idempotency_key: identifier(formValue(form, "idempotency_key"), "Idempotency key", true) };
    show(await call("/retention/purge", { method: "POST", body: JSON.stringify(payload) }), "Retention purge completed.");
  });

  bind("pdd-audit", "click", async function () { requirePermission("audit"); show(await call("/audit?limit=500"), "Audit events loaded."); });
  bind("pdd-verify", "click", async function () { requirePermission("audit"); show(await call("/audit/verify"), "Workspace audit history verified."); });
  bind("pdd-posture", "click", async function () { show(await call("/posture"), "Privacy posture and limits loaded."); });
  bind("pdd-runtime", "click", async function () { show(await call("/status"), "Availability loaded. Operation with a live provider has not been verified by this check."); });

  var setupState = {providers: [], connections: [], evidence_keys: []};
  var savedSources = [];
  var setupBusy = false;
  function newRequest(form) {
    if (global.crypto && typeof global.crypto.randomUUID === "function" && form.elements.idempotency_key)
      form.elements.idempotency_key.value = global.crypto.randomUUID();
  }
  function choices(select, rows, placeholder, selected) {
    select.replaceChildren();
    var empty = document.createElement("option"); empty.value = ""; empty.textContent = placeholder; select.append(empty);
    rows.forEach(function (row) { var option = document.createElement("option"); option.value = row.value; option.textContent = row.label; select.append(option); });
    if (selected && !rows.some(function (row) { return row.value === selected; })) {
      var old = document.createElement("option"); old.value = selected; old.textContent = "Previous selection is unavailable or changed — choose a current version"; old.disabled = true; select.append(old);
    }
    select.value = selected || "";
  }
  function renderConnections() {
    var form = document.getElementById("pdd-source");
    var provider = formValue(form, "provider");
    choices(document.getElementById("pdd-connection"), setupState.connections.filter(function (row) { return row.provider === provider; }).map(function (row) {
      return {value: row.reference, label: row.name + " · " + row.origin};
    }), "Choose a compatible saved connection", formValue(form, "credential_env"));
    var descriptor = setupState.providers.find(function (row) { return row.provider === provider; });
    document.getElementById("pdd-resource-help").textContent = descriptor ? "Source scope: " + descriptor.resource_ref_contract.replace(/_/g, " ") : "Choose an enabled provider. Enable additional providers in Settings.";
    if (descriptor) form.elements.kind.value = descriptor.kind;
  }
  async function loadSetup() {
    requirePermission("admin");
    var data = await Promise.all([call("/setup"), call("/sources?limit=500")]);
    setupState = data[0]; savedSources = data[1].items || [];
    if (!Array.isArray(setupState.providers) || !Array.isArray(setupState.connections) || !Array.isArray(setupState.evidence_keys)) throw problem("Setup choices are incomplete; reload before saving.");
    var form = document.getElementById("pdd-source");
    choices(form.elements.provider, setupState.providers.map(function (row) { return {value: row.provider, label: row.provider.replace(/_/g, " ")}; }), "Choose an enabled provider", formValue(form, "provider"));
    renderConnections();
    choices(document.getElementById("pdd-evidence-key"), setupState.evidence_keys.map(function (row) { return {value:row.reference, label:row.name + " · version " + row.revision}; }), "Choose an evidence key", formValue(form, "evidence_key_env"));
    choices(document.getElementById("pdd-key-picker"), setupState.evidence_keys.map(function (row) { return {value:row.reference, label:row.name + " · version " + row.revision}; }), "Create a new key", "");
    choices(document.getElementById("pdd-source-picker"), savedSources.map(function (row) { return {value:row.source_id, label:row.source_id + " · version " + row.revision}; }), "New source", savedSources.some(function (row) {return row.source_id===formValue(form,"source_id");}) ? formValue(form,"source_id") : "");
    show({providers:setupState.providers.length, connections:setupState.connections.length, evidence_keys:setupState.evidence_keys.length}, "Setup choices loaded. Selecting a new key or connection does not update a source until you save it.");
  }
  async function setupAction(action) {
    if (setupBusy) return;
    setupBusy = true;
    var controls = root.querySelectorAll("#pdd-source input,#pdd-source select,#pdd-source button,#pdd-key-form input,#pdd-key-form select,#pdd-key-form button,#pdd-load-setup,#pdd-source-picker");
    var prior = Array.from(controls).map(function (control) { return control.disabled; });
    controls.forEach(function (control) { control.disabled=true; });
    try { await action(); }
    catch (error) { fail(error); }
    finally { controls.forEach(function (control,index) { control.disabled=prior[index]; }); setupBusy=false; applyPermissions(); }
  }
  if (document.getElementById("pdd-load-setup")) {
    bind("pdd-load-setup", "click", function () { return setupAction(loadSetup); });
    var sourceForm = document.getElementById("pdd-source"), keyForm = document.getElementById("pdd-key-form");
    newRequest(sourceForm); newRequest(keyForm);
    [sourceForm,keyForm].forEach(function (form) { form.addEventListener("input", function (event) {
      if (event.target.name !== "idempotency_key") newRequest(form);
      if (form===keyForm && event.target.name==="name") form.elements.expected_revision.value="0";
      if (form===sourceForm && event.target.name==="source_id") form.elements.expected_revision.value="0";
    }); });
    sourceForm.elements.provider.addEventListener("change", function () { sourceForm.elements.credential_env.value=""; sourceForm.elements.origin.value=""; renderConnections(); newRequest(sourceForm); });
    bind("pdd-connection", "change", function (event) {
      var selected=setupState.connections.find(function (row) {return row.reference===event.currentTarget.value;});
      sourceForm.elements.credential_env.value=selected ? selected.reference : "";
      sourceForm.elements.origin.value=selected ? selected.origin : ""; newRequest(sourceForm);
    });
    bind("pdd-evidence-key", "change", function (event) { sourceForm.elements.evidence_key_env.value=event.currentTarget.value; newRequest(sourceForm); });
    bind("pdd-source-picker", "change", function (event) {
      var selected=savedSources.find(function (row) {return row.source_id===event.currentTarget.value;});
      if (!selected) { sourceForm.reset(); sourceForm.elements.expected_revision.value="0"; newRequest(sourceForm); return; }
      choices(sourceForm.elements.provider,setupState.providers.map(function (row) {return {value:row.provider,label:row.provider.replace(/_/g," ")};}),"Choose an enabled provider",selected.provider);
      ["source_id","kind","provider","origin","resource_ref","credential_env","evidence_key_env"].forEach(function (name) { sourceForm.elements[name].value=selected[name]; });
      sourceForm.elements.expected_revision.value=selected.revision;
      renderConnections();
      choices(document.getElementById("pdd-evidence-key"),setupState.evidence_keys.map(function (row) {return {value:row.reference,label:row.name+" · version "+row.revision};}),"Choose a current evidence key",selected.evidence_key_env);
      newRequest(sourceForm);
    });
    bind("pdd-key-picker", "change", function (event) {
      var key=setupState.evidence_keys.find(function (row) {return row.reference===event.currentTarget.value;});
      keyForm.elements.name.value=key ? key.name : ""; keyForm.elements.expected_revision.value=key ? key.revision : 0; newRequest(keyForm);
    });
    bind("pdd-key-form", "submit", function (event) {
      event.preventDefault(); requirePermission("admin");
      var payload={expected_revision:finiteNumber(formValue(keyForm,"expected_revision"),"Key version",0,2147483647,true), idempotency_key:identifier(formValue(keyForm,"idempotency_key"),"Request reference",true)};
      var name=formValue(keyForm,"name");
      if (payload.expected_revision && !global.confirm("Rotate this evidence key? Sources using the previous version will stop scanning until their settings are explicitly updated.")) return;
      return setupAction(async function () {
        var saved=await call("/evidence-keys/"+encodeURIComponent(name),{method:"PUT",body:JSON.stringify(payload)});
        await loadSetup(); keyForm.elements.expected_revision.value=saved.revision; newRequest(keyForm);
        show(saved,"Evidence key saved securely. Select its current version for each source and save those source settings; existing sources were not changed.");
      });
    });
    bind("pdd-test-source", "click", function () {
      requirePermission("admin");
      var sourceId=identifier(formValue(sourceForm,"source_id"),"Source",false);
      var revision=finiteNumber(formValue(sourceForm,"expected_revision"),"Saved source version",1,2147483647,true);
      if (!global.confirm("Test the saved source's read access with a bounded provider read? Unsaved form changes are not used; no findings or report will be stored.")) return;
      return setupAction(async function () { show(await call("/sources/"+encodeURIComponent(sourceId)+"/test-access",{method:"POST",body:JSON.stringify({confirm:true,expected_revision:revision})}),"Saved source access checked within a small read bound. This does not verify complete coverage or customer acceptance."); });
    });
  }
  applyPermissions();
  if (root.dataset.featureEnabled === "true") {
    call("/status").then(function (payload) {
      show(payload, "Privacy discovery status loaded. Confirm authorization for the provider before starting a scan.");
    }).catch(fail);
  } else {
    status.textContent = "Privacy data discovery is disabled by configuration.";
  }
}(typeof window !== "undefined" ? window : this));
