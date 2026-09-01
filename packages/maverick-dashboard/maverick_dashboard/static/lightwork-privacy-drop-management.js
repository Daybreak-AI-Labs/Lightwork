/* California DROP operator workbench. Closed inputs only; tenant, identity,
   registration, subject binding, DSAR, and outcome evidence remain server-owned. */
(function () {
  "use strict";

  var MAX_RESPONSE_BYTES = 64 * 1024;

  function show(element, value) {
    element.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  }

  function positiveInteger(value, label) {
    var selected = Number(value);
    if (!Number.isSafeInteger(selected) || selected < 1 || selected > 2147483647) {
      throw new Error(label + " must be a positive integer.");
    }
    return selected;
  }

  function nonnegativeInteger(value, label) {
    var selected = Number(value);
    if (!Number.isSafeInteger(selected) || selected < 0 || selected > 2147483647) {
      throw new Error(label + " must be a non-negative integer.");
    }
    return selected;
  }

  function positiveNumber(value, label) {
    var selected = Number(value);
    if (!Number.isFinite(selected) || selected <= 0 || selected >= 100000000000) {
      throw new Error(label + " must be a positive finite timestamp.");
    }
    return selected;
  }

  function values(form) {
    var formData = new FormData(form);
    var result = {};
    formData.forEach(function (value, key) {
      if (key !== "confirmation") result[key] = String(value).trim();
    });
    return result;
  }

  function responseTooLarge() {
    var error = new Error("The server response exceeded the 64 KiB safety limit.");
    error.code = "response_too_large";
    return error;
  }

  function invalidResponse() {
    var error = new Error("The server returned an invalid JSON response.");
    error.code = "response_invalid_json";
    return error;
  }

  async function cancelBody(body) {
    if (!body || typeof body.cancel !== "function") return;
    try { await body.cancel(); }
    catch (_error) { /* The bounded request has already failed closed. */ }
  }

  async function boundedResponseText(response) {
    var declared = response.headers && response.headers.get("Content-Length");
    if (declared && /^\d+$/.test(declared) && Number(declared) > MAX_RESPONSE_BYTES) {
      await cancelBody(response.body);
      throw responseTooLarge();
    }
    if (!response.body || typeof response.body.getReader !== "function") {
      // A whole-body fallback (response.text/json/arrayBuffer) cannot enforce
      // the raw-byte ceiling before allocation. Modern supported browsers
      // expose a ReadableStream; fail closed when that contract is absent.
      throw invalidResponse();
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
        try { await reader.cancel(); }
        catch (_error) { /* The size violation remains authoritative. */ }
        throw responseTooLarge();
      }
      text += decoder.decode(chunk.value, {stream: true});
    }
    return text + decoder.decode();
  }

  async function readBoundedJson(response) {
    var responseText = await boundedResponseText(response);
    if (!responseText) throw invalidResponse();
    try { return JSON.parse(responseText); }
    catch (_error) { throw invalidResponse(); }
  }

  async function parseResponse(response) {
    var payload;
    try {
      payload = await readBoundedJson(response);
    } catch (error) {
      error.status = response.status;
      throw error;
    }
    if (!response.ok) {
      var failure = new Error(
        payload && typeof payload.detail === "string" ?
          payload.detail : "The operation failed."
      );
      failure.status = response.status;
      throw failure;
    }
    return payload;
  }

  if (typeof window !== "undefined") {
    Object.defineProperty(window, "LightworkPrivacyDROPClient", {
      configurable: false,
      enumerable: false,
      writable: false,
      value: Object.freeze({
        MAX_RESPONSE_BYTES: MAX_RESPONSE_BYTES,
        readBoundedJson: readBoundedJson,
        parseResponse: parseResponse
      })
    });
  }
  if (typeof document === "undefined") return;
  var shell = document.getElementById("drop-workbench");
  if (!shell) return;
  var apiBase = shell.dataset.apiBase || "";
  var operations = document.getElementById("drop-operations");
  var authorityOperations = document.getElementById("drop-authority-operations");
  var readiness = document.getElementById("drop-readiness");
  var readinessDetail = document.getElementById("drop-readiness-detail");
  var statusBadge = document.getElementById("drop-status-badge");
  var statusOutput = document.getElementById("drop-status-output");
  var operationStatus = document.getElementById("drop-operation-status");
  var operationOutput = document.getElementById("drop-operation-output");
  var updateForm = document.getElementById("drop-update-form");

  async function request(path, options) {
    var response = await fetch(apiBase + path, Object.assign({
      cache: "no-store",
      credentials: "same-origin",
      headers: {Accept: "application/json"}
    }, options || {}));
    return parseResponse(response);
  }

  async function run(label, action) {
    operationStatus.textContent = label + "…";
    operationStatus.classList.remove("danger-text");
    operationOutput.textContent = "";
    try {
      var payload = await action();
      show(operationOutput, payload);
      operationStatus.textContent = label + " completed.";
      return payload;
    } catch (error) {
      operationStatus.classList.add("danger-text");
      operationStatus.textContent = label + " failed" +
        (Number.isInteger(error.status) ? " (" + error.status + ")" : "") +
        ": " + String(error.message || error);
      throw error;
    }
  }

  async function refreshStatus() {
    readiness.textContent = "Checking…";
    readinessDetail.textContent = "Loading the server-owned composition.";
    statusBadge.className = "badge pending";
    statusBadge.textContent = "Checking";
    statusOutput.textContent = "";
    operations.disabled = true;
    if (authorityOperations) authorityOperations.disabled = true;
    try {
      var payload = await request("/status");
      show(statusOutput, payload);
      if (payload.ready === true) {
        readiness.textContent = "Ready";
        readinessDetail.textContent = "Operational readiness was explicitly verified for this tenant.";
        statusBadge.className = "badge healthy";
        statusBadge.textContent = "Ready";
        operations.disabled = false;
        if (authorityOperations) authorityOperations.disabled = false;
      } else if (payload.enabled !== true) {
        readiness.textContent = "Disabled";
        readinessDetail.textContent = "Enable DROP and its privacy-rights parent before operating.";
        statusBadge.className = "badge pending";
        statusBadge.textContent = "Disabled";
      } else if (payload.configured === true) {
        readiness.textContent = "Not verified";
        readinessDetail.textContent = "Server dependencies are configured, but operational readiness has not been verified.";
        statusBadge.className = "badge warning";
        statusBadge.textContent = "Unready";
      } else {
        readiness.textContent = "Needs composition";
        readinessDetail.textContent = "Enabled, but one or more server-owned authorities are missing.";
        statusBadge.className = "badge warning";
        statusBadge.textContent = "Unready";
      }
    } catch (error) {
      readiness.textContent = "Unavailable";
      readinessDetail.textContent = String(error.message || error);
      statusBadge.className = "badge danger";
      statusBadge.textContent = "Error";
    }
  }

  document.getElementById("drop-refresh").addEventListener("click", refreshStatus);

  document.getElementById("drop-create-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    var selected = values(event.currentTarget);
    try {
      var dropId = positiveInteger(selected.drop_id, "DROP ID");
      var payload = await run("Creating DROP request", function () {
        return request("/" + encodeURIComponent(dropId) + "/request", {
          method: "POST",
          headers: {Accept: "application/json", "Content-Type": "application/json"},
          body: JSON.stringify({
            request_type: selected.request_type,
            template_id: selected.template_id,
            language: selected.language,
            expected_revision: 0,
            idempotency_key: selected.idempotency_key
          })
        });
      });
      document.querySelector('#drop-get-form [name="drop_id"]').value = payload.drop_id || dropId;
      if (updateForm) {
        updateForm.querySelector('[name="drop_id"]').value = payload.drop_id || dropId;
        updateForm.querySelector('[name="expected_revision"]').value = payload.revision || "";
      }
    } catch (_error) {
      // The governed status region already contains the error.
    }
  });

  document.getElementById("drop-get-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    var selected = values(event.currentTarget);
    try {
      var dropId = positiveInteger(selected.drop_id, "DROP ID");
      var payload = await run("Loading DROP request", function () {
        return request("/" + encodeURIComponent(dropId));
      });
      if (updateForm) {
        updateForm.querySelector('[name="drop_id"]').value = payload.drop_id || dropId;
        updateForm.querySelector('[name="expected_revision"]').value = payload.revision || "";
      }
    } catch (_error) {
      // The governed status region already contains the error.
    }
  });

  var integrityButton = document.getElementById("drop-integrity");
  if (integrityButton) {
    integrityButton.addEventListener("click", async function () {
      try { await run("Verifying DROP integrity", function () { return request("/integrity"); }); }
      catch (_error) { /* The governed status region already contains the error. */ }
    });
  }

  function authorityButton(id, label, path) {
    var button = document.getElementById(id);
    if (!button) return;
    button.addEventListener("click", async function () {
      try { await run(label, function () { return request(path); }); }
      catch (_error) { /* The governed status region already contains the error. */ }
    });
  }

  authorityButton("drop-authorities", "Loading DROP authorities", "/authority/authorities");
  authorityButton("drop-revisions", "Loading DROP key revisions", "/authority/revisions");
  authorityButton(
    "drop-authority-integrity",
    "Verifying DROP authority integrity",
    "/authority/integrity"
  );

  function proposalForm(id, label, path, payloadBuilder) {
    var form = document.getElementById(id);
    if (!form) return;
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      try {
        var payload = await run(label, function () {
          return request(path, {
            method: "POST",
            headers: {Accept: "application/json", "Content-Type": "application/json"},
            body: JSON.stringify(payloadBuilder(form, values(form)))
          });
        });
        var approval = document.getElementById("drop-approval-form");
        if (approval && payload.proposal_id) {
          approval.elements.proposal_id.value = payload.proposal_id;
        }
      } catch (_error) {
        // The governed status region already contains the error.
      }
    });
  }

  proposalForm(
    "drop-broker-form",
    "Creating broker authority proposal",
    "/authority/brokers/proposals",
    function (_form, selected) {
      var payload = {
        action: selected.action,
        broker_ref: selected.broker_ref,
        evidence_sha256: selected.evidence_sha256,
        expected_version: nonnegativeInteger(selected.expected_version, "Expected version"),
        idempotency_key: selected.idempotency_key
      };
      if (selected.action === "enroll_broker") {
        payload.registration_sha256 = selected.registration_sha256;
        payload.valid_until = positiveNumber(selected.valid_until, "Valid until");
      }
      return payload;
    }
  );

  proposalForm(
    "drop-record-form",
    "Creating DROP record authority proposal",
    "/authority/records/proposals",
    function (_form, selected) {
      var payload = {
        action: selected.action,
        drop_id: positiveInteger(selected.drop_id, "DROP ID"),
        evidence_sha256: selected.evidence_sha256,
        expected_version: nonnegativeInteger(selected.expected_version, "Expected version"),
        idempotency_key: selected.idempotency_key
      };
      if (selected.action === "import_record") {
        payload.broker_ref = selected.broker_ref;
        payload.broker_registration_sha256 = selected.registration_binding_digest;
        payload.subject_binding_sha256 = selected.binding_digest;
        payload.request_type = selected.request_type;
        payload.template_id = selected.template_id;
        payload.language = selected.language;
        payload.record_revision = positiveInteger(selected.record_revision, "Record revision");
        payload.valid_until = positiveNumber(selected.valid_until, "Valid until");
      }
      return payload;
    }
  );

  proposalForm(
    "drop-outcome-form",
    "Creating DROP outcome proposal",
    "/authority/outcomes/proposals",
    function (form, selected) {
      return {
        drop_id: positiveInteger(selected.drop_id, "DROP ID"),
        status: selected.status,
        evidence_sha256: selected.evidence_sha256,
        evidence_revision: positiveInteger(selected.evidence_revision, "Evidence revision"),
        observed_at: positiveNumber(selected.observed_at, "Observed at"),
        valid_until: positiveNumber(selected.valid_until, "Valid until"),
        deletion_verified: form.elements.deletion_verified.checked,
        opt_out_verified: form.elements.opt_out_verified.checked,
        search_complete: form.elements.search_complete.checked,
        exemption_approved: form.elements.exemption_approved.checked,
        provider_operations_complete: form.elements.provider_operations_complete.checked,
        expected_version: nonnegativeInteger(selected.expected_version, "Expected version"),
        idempotency_key: selected.idempotency_key
      };
    }
  );

  proposalForm(
    "drop-key-form",
    "Creating DROP key lifecycle proposal",
    "/authority/keys/proposals",
    function (_form, selected) {
      var payload = {action: selected.action, idempotency_key: selected.idempotency_key};
      if (selected.action === "rotate_key") {
        payload.new_key_ref = selected.new_key_ref;
        payload.expected_current_revision = positiveInteger(
          selected.expected_current_revision,
          "Expected current revision"
        );
      } else if (selected.action === "retire_key" || selected.action === "revoke_key") {
        payload.key_revision = positiveInteger(selected.key_revision, "Key revision");
      } else {
        payload.migration_id = selected.migration_id;
        payload.from_revision = positiveInteger(selected.from_revision, "From revision");
        payload.to_revision = positiveInteger(selected.to_revision, "To revision");
        payload.objects_migrated = nonnegativeInteger(
          selected.objects_migrated,
          "Objects migrated"
        );
        payload.remaining_dependencies = nonnegativeInteger(
          selected.remaining_dependencies,
          "Remaining dependencies"
        );
      }
      return payload;
    }
  );

  var approvalForm = document.getElementById("drop-approval-form");
  if (approvalForm) approvalForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    var selected = values(approvalForm);
    if (!approvalForm.elements.confirmation.checked) {
      operationStatus.textContent = "Confirm independent proposal review first.";
      operationStatus.classList.add("danger-text");
      return;
    }
    try {
      await run("Approving DROP authority proposal", function () {
        return request(
          "/authority/proposals/" + encodeURIComponent(selected.proposal_id) + "/approve",
          {
            method: "POST",
            headers: {Accept: "application/json", "Content-Type": "application/json"},
            body: JSON.stringify({
              expected_revision: positiveInteger(selected.expected_revision, "Expected revision"),
              idempotency_key: selected.idempotency_key
            })
          }
        );
      });
      approvalForm.elements.confirmation.checked = false;
      await refreshStatus();
    } catch (_error) {
      // The governed status region already contains the error.
    }
  });

  if (updateForm) updateForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    var form = event.currentTarget;
    var selected = values(form);
    if (!form.elements.confirmation.checked) {
      operationStatus.textContent = "Confirm the evidence-backed terminal update first.";
      operationStatus.classList.add("danger-text");
      return;
    }
    try {
      var dropId = positiveInteger(selected.drop_id, "DROP ID");
      var revision = positiveInteger(selected.expected_revision, "Expected revision");
      var payload = await run("Recording terminal DROP status", function () {
        return request("/" + encodeURIComponent(dropId), {
          method: "PUT",
          headers: {Accept: "application/json", "Content-Type": "application/json"},
          body: JSON.stringify({
            status: selected.status,
            expected_revision: revision,
            idempotency_key: selected.idempotency_key
          })
        });
      });
      form.elements.expected_revision.value = payload.revision || "";
      form.elements.confirmation.checked = false;
    } catch (_error) {
      // The governed status region already contains the error.
    }
  });

  refreshStatus();
}());
