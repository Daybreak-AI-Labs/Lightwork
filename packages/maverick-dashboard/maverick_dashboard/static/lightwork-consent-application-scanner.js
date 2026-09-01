(function () {
  "use strict";

  var MAX_REQUEST_BYTES = 6 * 1024 * 1024;
  var MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
  var MAX_FILE_BYTES = 1024 * 1024;
  var MAX_TOTAL_FILE_BYTES = 4 * 1024 * 1024;
  var shell = document.getElementById("consent-application-scanner-shell");
  if (!shell) return;

  var apiBase = shell.getAttribute("data-api-base") || "";
  var operations = document.getElementById("cas-operations");
  var runtimeStatus = document.getElementById("cas-runtime-status");
  var statusOutput = document.getElementById("cas-status-output");
  var operationStatus = document.getElementById("cas-operation-status");
  var operationOutput = document.getElementById("cas-operation-output");

  function element(id) {
    return document.getElementById(id);
  }

  function byteLength(value) {
    return new TextEncoder().encode(value).length;
  }

  function render(target, value) {
    target.textContent = JSON.stringify(value, null, 2);
  }

  function setOperation(message, state, value) {
    operationStatus.textContent = message;
    operationStatus.setAttribute("data-state", state || "ready");
    if (value !== undefined) render(operationOutput, value);
  }

  function inputValue(form, name) {
    var control = form.elements[name];
    return control ? String(control.value || "").trim() : "";
  }

  function positiveInteger(form, name) {
    var value = Number(inputValue(form, name));
    if (!Number.isInteger(value) || value < 1 || value > 2147483647) {
      throw new Error(name + " must be a positive integer.");
    }
    return value;
  }

  function errorMessage(body, response) {
    if (body && typeof body.detail === "string") return body.detail;
    return "Request failed with HTTP " + response.status + ".";
  }

  async function call(path, options, acceptedStatuses) {
    var settings = Object.assign(
      {
        cache: "no-store",
        credentials: "same-origin",
        headers: {},
        referrerPolicy: "no-referrer"
      },
      options || {}
    );
    if (typeof settings.body === "string") {
      if (byteLength(settings.body) > MAX_REQUEST_BYTES) {
        throw new Error("Request exceeds the 6 MiB workbench bound.");
      }
      settings.headers = Object.assign({ "Content-Type": "application/json" }, settings.headers);
    }
    var response = await fetch(apiBase + path, settings);
    var declared = Number(response.headers.get("content-length") || "0");
    if (declared > MAX_RESPONSE_BYTES) {
      throw new Error("Response exceeds the 2 MiB workbench bound.");
    }
    var reader = response.body && response.body.getReader ? response.body.getReader() : null;
    if (!reader) throw new Error("The server response stream is unavailable.");
    var decoder = new TextDecoder("utf-8", { fatal: true });
    var chunks = "";
    var received = 0;
    while (true) {
      var part = await reader.read();
      if (part.done) break;
      received += part.value.byteLength;
      if (received > MAX_RESPONSE_BYTES) {
        await reader.cancel();
        throw new Error("Response exceeds the 2 MiB workbench bound.");
      }
      chunks += decoder.decode(part.value, { stream: true });
    }
    chunks += decoder.decode();
    var body;
    try {
      body = chunks ? JSON.parse(chunks) : {};
    } catch (_error) {
      throw new Error("The server returned invalid JSON.");
    }
    var accepted = Array.isArray(acceptedStatuses) && acceptedStatuses.indexOf(response.status) !== -1;
    if (!response.ok && !accepted) throw new Error(errorMessage(body, response));
    return body;
  }

  function bytesToBase64(bytes) {
    var output = "";
    var chunkSize = 0x8000;
    for (var offset = 0; offset < bytes.length; offset += chunkSize) {
      var chunk = bytes.subarray(offset, Math.min(offset + chunkSize, bytes.length));
      output += String.fromCharCode.apply(null, chunk);
    }
    return btoa(output);
  }

  function mediaType(file) {
    if (file.type && /^[a-z0-9][a-z0-9.+-]{0,63}\/[a-z0-9][a-z0-9.+-]{0,63}$/i.test(file.type)) {
      return file.type.toLowerCase();
    }
    var lower = file.name.toLowerCase();
    if (lower.endsWith(".xml")) return "application/xml";
    if (lower.endsWith(".json")) return "application/json";
    if (lower.endsWith(".plist") || lower.endsWith(".xcprivacy")) return "application/x-plist";
    return "text/plain";
  }

  async function manifestPayload(fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length || files.length > 64) {
      throw new Error("Select between one and 64 manifest files.");
    }
    var total = files.reduce(function (sum, file) {
      if (!file.size || file.size > MAX_FILE_BYTES) {
        throw new Error("Each manifest must contain between one byte and 1 MiB.");
      }
      return sum + file.size;
    }, 0);
    if (total > MAX_TOTAL_FILE_BYTES) {
      throw new Error("Manifest files exceed the 4 MiB combined bound.");
    }
    var output = [];
    for (var index = 0; index < files.length; index += 1) {
      var file = files[index];
      var bytes = new Uint8Array(await file.arrayBuffer());
      output.push({
        path: file.webkitRelativePath || file.name,
        media_type: mediaType(file),
        content_base64: bytesToBase64(bytes)
      });
    }
    return output;
  }

  function syncApplication(value) {
    if (!value || typeof value !== "object") return;
    if (typeof value.id === "string") element("cas-application-id").value = value.id;
    if (Number.isInteger(value.revision)) {
      element("cas-application-revision").value = String(value.revision);
    }
  }

  function syncScan(value) {
    if (!value || typeof value !== "object") return;
    if (typeof value.id === "string") element("cas-scan-id").value = value.id;
    if (Number.isInteger(value.revision)) element("cas-scan-revision").value = String(value.revision);
  }

  async function refreshStatus() {
    if (!/^\/api\/v1\/consent\/application-scans$/.test(apiBase)) {
      runtimeStatus.textContent = "The configured API boundary is invalid.";
      runtimeStatus.setAttribute("data-state", "failed");
      operations.disabled = true;
      return;
    }
    runtimeStatus.textContent = "Checking application-scanner custody…";
    runtimeStatus.setAttribute("data-state", "pending");
    operations.disabled = true;
    try {
      var value = await call("/readiness", undefined, [503]);
      render(statusOutput, value);
      var ready = value && value.enabled === true && value.ready === true;
      runtimeStatus.textContent = ready
        ? "Application manifest analysis is ready for this tenant."
        : "Application manifest analysis is closed until its authority is ready.";
      runtimeStatus.setAttribute("data-state", ready ? "ready" : "failed");
      operations.disabled = !ready;
    } catch (error) {
      runtimeStatus.textContent = error.message;
      runtimeStatus.setAttribute("data-state", "failed");
      statusOutput.textContent = "";
      operations.disabled = true;
    }
  }

  async function registerApplication(event) {
    event.preventDefault();
    var form = event.currentTarget;
    try {
      var value = await call("/applications", {
        method: "POST",
        body: JSON.stringify({
          platform: inputValue(form, "platform"),
          bundle_id: inputValue(form, "bundle_id"),
          display_name: inputValue(form, "display_name"),
          idempotency_key: inputValue(form, "idempotency_key")
        })
      });
      syncApplication(value);
      setOperation("Application authority registered.", "ready", value);
    } catch (error) {
      setOperation(error.message, "failed");
    }
  }

  async function scanApplication(event) {
    event.preventDefault();
    var form = event.currentTarget;
    try {
      setOperation("Reading bounded manifest files…", "pending");
      var files = await manifestPayload(form.elements.files.files);
      var applicationId = inputValue(form, "application_id");
      if (!applicationId) throw new Error("Application ID is required.");
      var value = await call("/applications/" + encodeURIComponent(applicationId) + "/scans", {
        method: "POST",
        body: JSON.stringify({
          expected_application_revision: positiveInteger(form, "expected_application_revision"),
          idempotency_key: inputValue(form, "idempotency_key"),
          files: files
        })
      });
      syncScan(value);
      form.elements.files.value = "";
      setOperation("Manifest observations created for independent review.", "ready", value);
    } catch (error) {
      setOperation(error.message, "failed");
    }
  }

  async function reviewScan(event) {
    event.preventDefault();
    var form = event.currentTarget;
    try {
      var scanId = inputValue(form, "scan_id");
      if (!scanId) throw new Error("Scan ID is required.");
      var value = await call("/scans/" + encodeURIComponent(scanId) + "/review", {
        method: "POST",
        body: JSON.stringify({
          decision: inputValue(form, "decision"),
          expected_revision: positiveInteger(form, "expected_revision"),
          review_note_code: inputValue(form, "review_note_code")
        })
      });
      syncScan(value);
      setOperation("Independent review recorded.", "ready", value);
    } catch (error) {
      setOperation(error.message, "failed");
    }
  }

  async function show(path, message) {
    try {
      setOperation(message, "ready", await call(path));
    } catch (error) {
      setOperation(error.message, "failed");
    }
  }

  async function purgeExpired() {
    try {
      setOperation(
        "Retention maintenance completed.",
        "ready",
        await call("/maintenance/purge", {
          method: "POST",
          body: JSON.stringify({ limit: 100 })
        })
      );
    } catch (error) {
      setOperation(error.message, "failed");
    }
  }

  element("cas-register-form").addEventListener("submit", registerApplication);
  element("cas-scan-form").addEventListener("submit", scanApplication);
  element("cas-review-form").addEventListener("submit", reviewScan);
  element("cas-refresh-status").addEventListener("click", refreshStatus);
  element("cas-list-applications").addEventListener("click", function () {
    show("/applications?limit=100", "Applications loaded.");
  });
  element("cas-list-scans").addEventListener("click", function () {
    show("/scans?limit=100", "Application scans loaded.");
  });
  element("cas-verify-integrity").addEventListener("click", function () {
    show("/integrity?limit=500", "Integrity verification completed.");
  });
  element("cas-purge").addEventListener("click", purgeExpired);
  refreshStatus();
})();
