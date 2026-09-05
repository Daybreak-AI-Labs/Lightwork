(function () {
  "use strict";

  var root = document.getElementById("nca-root");
  if (!root) { return; }
  var maxResponseBytes = 512 * 1024;
  var maxRequestBytes = 128 * 1024;
  var identifierPattern = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
  var digestPattern = /^[0-9a-f]{64}$/;
  var base64urlPattern = /^[A-Za-z0-9_-]{1,87384}$/;
  var state = document.getElementById("nca-state");
  var output = document.getElementById("nca-output");
  var operationState = document.getElementById("nca-operation-state");
  var adminBase = endpointBase(root.dataset.adminApiBase, "/api/v1/consent/native");
  var publicKeysBase = endpointBase(
    root.dataset.publicKeysBase,
    "/api/v1/public/consent/native/receipt-keys",
  );

  function fail(message) {
    var error = new Error(message);
    error.code = "invalid_input";
    return error;
  }

  function endpointBase(value, fallback) {
    var selected = String(value || fallback);
    var parsed = new URL(selected, window.location.origin);
    if (parsed.origin !== window.location.origin || parsed.search || parsed.hash) {
      throw fail("Native consent API authority is invalid.");
    }
    return parsed.pathname.replace(/\/$/, "");
  }

  function endpoint(base, path) {
    var parsed = new URL(base + path, window.location.origin);
    if (parsed.origin !== window.location.origin) {
      throw fail("Cross-origin native consent requests are forbidden.");
    }
    return parsed.pathname + parsed.search;
  }

  function responseDetail(payload, fallback) {
    if (!payload || typeof payload !== "object") { return fallback; }
    if (typeof payload.detail === "string") { return payload.detail; }
    if (payload.detail && typeof payload.detail.message === "string") {
      return payload.detail.message;
    }
    return fallback;
  }

  async function readJson(response) {
    var declared = Number(response.headers.get("content-length") || 0);
    if (!Number.isFinite(declared) || declared < 0 || declared > maxResponseBytes) {
      if (response.body && typeof response.body.cancel === "function") {
        await response.body.cancel();
      }
      throw fail("Response exceeds the native workbench byte bound.");
    }
    if (!response.body || typeof response.body.getReader !== "function") {
      throw fail("Response stream is unavailable.");
    }
    var reader = response.body.getReader();
    var chunks = [];
    var total = 0;
    while (true) {
      var part = await reader.read();
      if (part.done) { break; }
      total += part.value.byteLength;
      if (total > maxResponseBytes) {
        await reader.cancel();
        throw fail("Response exceeds the native workbench byte bound.");
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
    catch (_error) { throw fail("The server returned invalid UTF-8."); }
    var payload;
    try { payload = text ? JSON.parse(text) : {}; }
    catch (_error) { throw fail("The server returned invalid JSON."); }
    if (!response.ok) {
      throw fail(responseDetail(payload, "Native consent request failed."));
    }
    return payload;
  }

  async function request(base, path, options, publicMetadata) {
    var settings = Object.assign(
      {
        credentials: publicMetadata ? "omit" : "same-origin",
        headers: { Accept: "application/json" },
        redirect: "error",
      },
      options || {},
    );
    if (settings.body) {
      if (new TextEncoder().encode(settings.body).byteLength > maxRequestBytes) {
        throw fail("Request exceeds the native workbench byte bound.");
      }
      settings.headers["Content-Type"] = "application/json";
    }
    return readJson(await fetch(endpoint(base, path), settings));
  }

  function show(label, payload) {
    operationState.textContent = label;
    output.textContent = JSON.stringify(payload, null, 2);
  }

  function showError(error) {
    show("Operation failed closed.", {
      error: String(error && error.message ? error.message : error),
    });
  }

  function identifier(value, label) {
    var selected = String(value || "").trim();
    if (!identifierPattern.test(selected)) { throw fail(label + " is invalid."); }
    return selected;
  }

  function digest(value, label) {
    var selected = String(value || "").trim();
    if (!digestPattern.test(selected)) {
      throw fail(label + " must be a lowercase SHA-256 digest.");
    }
    return selected;
  }

  function boundedInteger(value, label, minimum, maximum) {
    var selected = Number(value);
    if (!Number.isSafeInteger(selected) || selected < minimum || selected > maximum) {
      throw fail(label + " is outside its bound.");
    }
    return selected;
  }

  function base64url(value) {
    var selected = String(value || "").trim();
    if (!base64urlPattern.test(selected)) {
      throw fail("Attestation material must be unpadded base64url.");
    }
    return selected;
  }

  function mutation(path, body) {
    return request(adminBase, path, { method: "POST", body: JSON.stringify(body) }, false);
  }

  function renderReasons(reasons) {
    var list = document.getElementById("nca-reasons");
    list.replaceChildren();
    var selected = Array.isArray(reasons) && reasons.length ? reasons.slice(0, 32) : ["none"];
    selected.forEach(function (reason) {
      var item = document.createElement("li");
      item.textContent = String(reason).replace(/_/g, " ");
      list.appendChild(item);
    });
  }

  function renderReadiness(status) {
    var ready = status && status.ready === true;
    var enabled = status && status.enabled === true;
    state.dataset.state = ready ? "ready" : (enabled ? "partial" : "failed");
    state.textContent = ready ? "Ready" : (enabled ? "Action needed" : "Unavailable");
    document.getElementById("nca-health-title").textContent = ready
      ? "Native consent authority ready"
      : (enabled ? "Native consent is fail-closed" : "Native consent is disabled");
    document.getElementById("nca-health-detail").textContent = ready
      ? "Registered applications are linked to their current receipt keys, publication, and workspace."
      : "Review the reasons below before enrolling or contacting devices.";
    document.getElementById("nca-applications").textContent = String(
      Number.isSafeInteger(status.applications) ? status.applications : 0,
    );
    var callbacks = [
      status.profile_linking_required,
      status.rights_requests_required,
      status.standards_wire_verification_required,
    ].filter(function (value) { return value === true; }).length;
    document.getElementById("nca-callbacks").textContent = String(callbacks);
    renderReasons(status.reasons);
  }

  async function loadReadiness() {
    state.dataset.state = "partial";
    state.textContent = "Checking";
    try {
      var status = await request(adminBase, "/readiness", null, false);
      renderReadiness(status);
      show("Native readiness loaded.", status);
    } catch (error) {
      state.dataset.state = "failed";
      state.textContent = "Unavailable";
      document.getElementById("nca-health-title").textContent = "Readiness failed closed";
      document.getElementById("nca-health-detail").textContent = error.message;
      renderReasons(["readiness_unavailable"]);
      showError(error);
    }
  }

  function renderKeys(payload) {
    var rows = document.getElementById("nca-key-rows");
    rows.replaceChildren();
    var keys = payload && Array.isArray(payload.keys) ? payload.keys.slice(0, 64) : [];
    keys.forEach(function (key) {
      var row = document.createElement("tr");
      [key.key_id, key.key_revision, key.public_key_hex, key.current === true ? "yes" : "no"]
        .forEach(function (value) {
          var cell = document.createElement("td");
          cell.textContent = value === undefined || value === null ? "—" : String(value);
          row.appendChild(cell);
        });
      rows.appendChild(row);
    });
    if (!rows.children.length) {
      var empty = document.createElement("tr");
      var cell = document.createElement("td");
      cell.colSpan = 4;
      cell.textContent = "No receipt keys are available.";
      empty.appendChild(cell);
      rows.appendChild(empty);
    }
  }

  async function loadKeys() {
    var form = document.getElementById("nca-keys-form");
    var applicationRef = identifier(form.elements.application_ref.value, "Application reference");
    var path = "/" + encodeURIComponent(applicationRef);
    var link = document.getElementById("nca-public-key-link");
    link.href = endpoint(publicKeysBase, path);
    link.textContent = link.href;
    var payload = await request(publicKeysBase, path, null, true);
    renderKeys(payload);
    show("Public receipt keys loaded without ambient credentials.", payload);
  }

  function copyApplicationRef(applicationRef) {
    if (!identifierPattern.test(String(applicationRef || ""))) { return; }
    ["nca-keys-form", "nca-receipt-rotate-form", "nca-device-rotate-form", "nca-device-revoke-form"]
      .forEach(function (formId) {
        var form = document.getElementById(formId);
        if (form && form.elements.application_ref && !form.elements.application_ref.value) {
          form.elements.application_ref.value = applicationRef;
        }
      });
  }

  document.getElementById("nca-refresh").addEventListener("click", function () {
    loadReadiness();
  });
  document.getElementById("nca-integrity").addEventListener("click", function () {
    request(adminBase, "/integrity", null, false)
      .then(function (payload) { show("Native integrity verification completed.", payload); })
      .catch(showError);
  });
  document.getElementById("nca-load-keys").addEventListener("click", function () {
    loadKeys().catch(showError);
  });

  document.getElementById("nca-register-form").addEventListener("submit", function (event) {
    event.preventDefault();
    try {
      var form = event.currentTarget;
      var body = {
        public_client_ref: identifier(form.elements.public_client_ref.value, "Public client reference"),
        minimum_sdk_version: boundedInteger(
          form.elements.minimum_sdk_version.value,
          "Minimum SDK version",
          1,
          1000000,
        ),
        expected_revision: 0,
      };
      mutation("/applications", body).then(function (payload) {
        copyApplicationRef(payload.application_ref);
        show("Exact native application authority registered.", payload);
        loadReadiness();
      }).catch(showError);
    } catch (error) { showError(error); }
  });

  document.getElementById("nca-receipt-rotate-form").addEventListener("submit", function (event) {
    event.preventDefault();
    try {
      var form = event.currentTarget;
      var applicationRef = identifier(form.elements.application_ref.value, "Application reference");
      var body = {
        expected_directory_revision: boundedInteger(
          form.elements.expected_directory_revision.value,
          "Current directory version",
          1,
          2147483647,
        ),
      };
      mutation(
        "/applications/" + encodeURIComponent(applicationRef) + "/receipt-key/rotate",
        body,
      ).then(function (payload) {
        show("Configured receipt-key rotation completed.", payload);
        loadReadiness();
      }).catch(showError);
    } catch (error) { showError(error); }
  });

  document.getElementById("nca-device-rotate-form").addEventListener("submit", function (event) {
    event.preventDefault();
    try {
      var form = event.currentTarget;
      var applicationRef = identifier(form.elements.application_ref.value, "Application reference");
      var deviceId = identifier(form.elements.device_id.value, "Device reference");
      var body = {
        expected_revision: boundedInteger(form.elements.expected_revision.value, "Expected revision", 1, 2147483647),
        public_key_hex: digest(form.elements.public_key_hex.value, "Ed25519 public key"),
        attestation_nonce: identifier(form.elements.attestation_nonce.value, "Attestation nonce"),
        attestation_evidence_sha256: digest(
          form.elements.attestation_evidence_sha256.value,
          "Attestation evidence",
        ),
        attestation_material_b64url: base64url(form.elements.attestation_material_b64url.value),
        issued_at: boundedInteger(form.elements.issued_at.value, "Issued at", 1, 99999999999),
        expires_at: boundedInteger(form.elements.expires_at.value, "Expires at", 1, 99999999999),
      };
      mutation(
        "/applications/" + encodeURIComponent(applicationRef) + "/devices/" +
          encodeURIComponent(deviceId) + "/rotate",
        body,
      ).then(function (payload) {
        show("Freshly attested device-key rotation completed.", payload);
      }).catch(showError);
    } catch (error) { showError(error); }
  });

  document.getElementById("nca-device-revoke-form").addEventListener("submit", function (event) {
    event.preventDefault();
    try {
      var form = event.currentTarget;
      var applicationRef = identifier(form.elements.application_ref.value, "Application reference");
      var deviceId = identifier(form.elements.device_id.value, "Device reference");
      var body = {
        expected_revision: boundedInteger(form.elements.expected_revision.value, "Expected revision", 1, 2147483647),
        evidence_sha256: digest(form.elements.evidence_sha256.value, "Revocation evidence"),
      };
      mutation(
        "/applications/" + encodeURIComponent(applicationRef) + "/devices/" +
          encodeURIComponent(deviceId) + "/revoke",
        body,
      ).then(function (payload) {
        show("Exact native device revoked.", payload);
        loadReadiness();
      }).catch(showError);
    } catch (error) { showError(error); }
  });

  loadReadiness();
}());
