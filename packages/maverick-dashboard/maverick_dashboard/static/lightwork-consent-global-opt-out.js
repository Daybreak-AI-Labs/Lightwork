(function () {
  "use strict";

  var MAX_REQUEST_BYTES = 64 * 1024;
  var MAX_RESPONSE_BYTES = 256 * 1024;
  var shell = document.getElementById("global-opt-out-shell");
  if (!shell) return;

  var apiBase = shell.getAttribute("data-api-base") || "";
  var operations = document.getElementById("goo-operations");
  var runtimeStatus = document.getElementById("goo-runtime-status");
  var statusOutput = document.getElementById("goo-status-output");
  var operationStatus = document.getElementById("goo-operation-status");
  var operationOutput = document.getElementById("goo-operation-output");

  function element(id) {
    return document.getElementById(id);
  }

  function byteLength(value) {
    return new TextEncoder().encode(value).length;
  }

  function errorMessage(body, response) {
    var detail = body && body.detail;
    if (detail && typeof detail === "object" && typeof detail.code === "string") {
      return detail.code + ": " + String(detail.message || "request failed");
    }
    if (typeof detail === "string") return detail;
    return "Request failed with HTTP " + response.status + ".";
  }

  async function call(path, options) {
    var settings = Object.assign(
      { cache: "no-store", credentials: "same-origin", headers: {}, referrerPolicy: "no-referrer" },
      options || {}
    );
    if (typeof settings.body === "string") {
      if (byteLength(settings.body) > MAX_REQUEST_BYTES) {
        throw new Error("Request exceeds the 64 KiB workbench bound.");
      }
      settings.headers = Object.assign({ "Content-Type": "application/json" }, settings.headers);
    }
    var response;
    try {
      response = await fetch(apiBase + path, settings);
    } catch (_error) {
      throw new Error("Could not reach the server — check your connection and try again.");
    }
    var declared = Number(response.headers.get("content-length") || "0");
    if (declared > MAX_RESPONSE_BYTES) {
      throw new Error("Response exceeds the 256 KiB workbench bound.");
    }
    var text = await response.text();
    if (byteLength(text) > MAX_RESPONSE_BYTES) {
      throw new Error("Response exceeds the 256 KiB workbench bound.");
    }
    var body;
    try {
      body = text ? JSON.parse(text) : {};
    } catch (_error) {
      throw new Error("The server returned invalid JSON.");
    }
    if (!response.ok) throw new Error(errorMessage(body, response));
    return body;
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

  function numberValue(form, name) {
    var raw = inputValue(form, name);
    if (!raw) return null;
    var value = Number(raw);
    if (!Number.isFinite(value)) throw new Error(name + " must be a finite number.");
    return value;
  }

  function checkedValues(form, name) {
    return Array.prototype.map.call(
      form.querySelectorAll('input[name="' + name + '"]:checked'),
      function (control) { return control.value; }
    );
  }

  function channelUI(form, prefix) {
    return {
      enabled: Boolean(form.elements[prefix + "_enabled"].checked),
      title: inputValue(form, prefix + "_title"),
      body: inputValue(form, prefix + "_body"),
      submit_label: inputValue(form, prefix + "_submit_label"),
      success_message: inputValue(form, prefix + "_success_message")
    };
  }

  function configurationPayload(form, creating) {
    var expiry = numberValue(form, "expires_at");
    var payload = {
      locale: inputValue(form, "locale"),
      mobile_ui: channelUI(form, "mobile"),
      email_ui: channelUI(form, "email"),
      supported_devices: checkedValues(form, "supported_devices"),
      supported_services: checkedValues(form, "supported_services"),
      expires_at: expiry,
      expected_revision: Number(inputValue(form, "expected_revision")),
      idempotency_key: inputValue(form, "idempotency_key")
    };
    if (creating) payload.application_ref = inputValue(form, "application_ref");
    if (!payload.mobile_ui.enabled && !payload.email_ui.enabled) {
      throw new Error("Enable at least one mobile or email experience.");
    }
    if (!payload.supported_devices.length || !payload.supported_services.length) {
      throw new Error("Select at least one supported device and service.");
    }
    return payload;
  }

  function syncConfiguration(value) {
    if (!value || typeof value !== "object") return;
    var id = value.configuration_id;
    var revision = value.revision;
    if (typeof id === "string") element("goo-configuration-id").value = id;
    if (Number.isInteger(revision)) {
      element("goo-lifecycle-revision").value = String(revision);
      element("goo-expected-revision").value = String(revision);
    }
  }

  async function refreshStatus() {
    if (!/^\/api\/v1\/consent\/global-opt-out$/.test(apiBase)) {
      runtimeStatus.textContent = "The configured API boundary is invalid.";
      runtimeStatus.setAttribute("data-state", "failed");
      operations.disabled = true;
      return;
    }
    runtimeStatus.textContent = "Checking configured authority and privacy-rights intake…";
    runtimeStatus.setAttribute("data-state", "pending");
    operations.disabled = true;
    try {
      var value = await call("/status");
      render(statusOutput, value);
      var ready = value && value.enabled === true && value.readiness && value.readiness.ready === true;
      runtimeStatus.textContent = ready
        ? "Global Opt-Out is ready for this workspace."
        : "Global Opt-Out is closed until its configured authorities are ready.";
      runtimeStatus.setAttribute("data-state", ready ? "ready" : "failed");
      operations.disabled = !ready;
    } catch (error) {
      runtimeStatus.textContent = error.message;
      runtimeStatus.setAttribute("data-state", "failed");
      statusOutput.textContent = "";
      operations.disabled = true;
    }
  }

  async function createConfiguration(event) {
    event.preventDefault();
    var form = event.currentTarget;
    try {
      var value = await call("/configurations", {
        method: "POST",
        body: JSON.stringify(configurationPayload(form, true))
      });
      syncConfiguration(value);
      setOperation("Draft configuration created.", "ready", value);
    } catch (error) {
      setOperation(error.message, "failed");
    }
  }

  async function updateConfiguration() {
    var form = element("goo-config-form");
    var id = inputValue(element("goo-lifecycle-form"), "configuration_id");
    var button = element("goo-update");
    if (!id) return setOperation("Select a configuration before updating it.", "failed");
    if (button && button.dataset.inFlight === "true") return;
    if (button) button.dataset.inFlight = "true";
    try {
      if (!(await window.mvConfirm(
        "Update and rebind Global Opt-Out configuration “" + id + "”? The new governed version replaces its current channel and device binding.",
        { okText: "Update configuration" }
      ))) return;
      if (button) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
      }
      var value = await call("/configurations/" + encodeURIComponent(id), {
        method: "PUT",
        body: JSON.stringify(configurationPayload(form, false))
      });
      syncConfiguration(value);
      setOperation("Configuration version updated and rebound.", "ready", value);
    } catch (error) {
      setOperation(error.message, "failed");
    } finally {
      if (button) {
        delete button.dataset.inFlight;
        button.removeAttribute("aria-busy");
        button.disabled = false;
      }
    }
  }

  async function listConfigurations() {
    try {
      setOperation("Configurations loaded.", "ready", await call("/configurations?limit=100"));
    } catch (error) {
      setOperation(error.message, "failed");
    }
  }

  async function loadConfiguration() {
    var id = inputValue(element("goo-lifecycle-form"), "configuration_id");
    if (!id) return setOperation("Enter a configuration ID.", "failed");
    try {
      var value = await call("/configurations/" + encodeURIComponent(id));
      syncConfiguration(value);
      setOperation("Configuration loaded.", "ready", value);
    } catch (error) {
      setOperation(error.message, "failed");
    }
  }

  async function stateChange(action) {
    var form = element("goo-lifecycle-form");
    var id = inputValue(form, "configuration_id");
    var button = element(action === "activate" ? "goo-activate" : "goo-revoke");
    if (button && button.dataset.inFlight === "true") return;
    var payload = {
      expected_revision: Number(inputValue(form, "expected_revision")),
      idempotency_key: inputValue(form, "idempotency_key")
    };
    if (!id) return setOperation("Enter a configuration ID.", "failed");
    if (button) button.dataset.inFlight = "true";
    try {
      var prompt = action === "activate"
        ? "Activate Global Opt-Out configuration “" + id + "”? Its governed mobile and email experiences become eligible for use."
        : "Revoke Global Opt-Out configuration “" + id + "”? Its governed experiences stop being eligible for new use.";
      if (!(await window.mvConfirm(prompt, {
        okText: action === "activate" ? "Activate configuration" : "Revoke configuration"
      }))) return;
      if (button) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
      }
      var value = await call(
        "/configurations/" + encodeURIComponent(id) + "/" + action,
        { method: "POST", body: JSON.stringify(payload) }
      );
      syncConfiguration(value);
      setOperation("Configuration " + action + "d.", "ready", value);
    } catch (error) {
      setOperation(error.message, "failed");
    } finally {
      if (button) {
        delete button.dataset.inFlight;
        button.removeAttribute("aria-busy");
        button.disabled = false;
      }
    }
  }

  async function history() {
    var id = inputValue(element("goo-lifecycle-form"), "configuration_id");
    if (!id) return setOperation("Enter a configuration ID.", "failed");
    try {
      setOperation(
        "Configuration history loaded.",
        "ready",
        await call("/configurations/" + encodeURIComponent(id) + "/history")
      );
    } catch (error) {
      setOperation(error.message, "failed");
    }
  }

  async function maintenance(path, message, buttonId, confirmation) {
    var button = element(buttonId);
    if (button && button.dataset.inFlight === "true") return;
    if (button) button.dataset.inFlight = "true";
    try {
      if (confirmation && !(await window.mvConfirm(confirmation.message, {
        okText: confirmation.okText
      }))) return;
      if (button) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
      }
      setOperation(
        message,
        "ready",
        await call(path, { method: "POST", body: JSON.stringify({ limit: 100 }) })
      );
    } catch (error) {
      setOperation(error.message, "failed");
    } finally {
      if (button) {
        delete button.dataset.inFlight;
        button.removeAttribute("aria-busy");
        button.disabled = false;
      }
    }
  }

  function on(id, eventName, handler) {
    var node = element(id);
    if (node) node.addEventListener(eventName, handler);
  }

  on("goo-refresh-status", "click", refreshStatus);
  on("goo-config-form", "submit", createConfiguration);
  on("goo-update", "click", updateConfiguration);
  on("goo-list", "click", listConfigurations);
  on("goo-load", "click", loadConfiguration);
  on("goo-activate", "click", function () { stateChange("activate"); });
  on("goo-revoke", "click", function () { stateChange("revoke"); });
  on("goo-history", "click", history);
  on("goo-integrity", "click", async function () {
    try {
      setOperation("Integrity verified.", "ready", await call("/integrity?limit=10000"));
    } catch (error) {
      setOperation(error.message, "failed");
    }
  });
  on("goo-retry-audit", "click", function () {
    maintenance("/maintenance/retry-audit", "Signed audit outbox retry completed.", "goo-retry-audit");
  });
  on("goo-purge", "click", function () {
    maintenance("/maintenance/purge", "Expired retention purge completed.", "goo-purge", {
      message: "Purge expired Global Opt-Out retention records for this tenant? Current records and immutable audit evidence are retained.",
      okText: "Purge expired records"
    });
  });

  refreshStatus();
}());
