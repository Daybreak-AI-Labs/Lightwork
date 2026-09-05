"use strict";

(function consentPublishSettingsClient(global) {
  const MAX_REQUEST_BYTES = 64 * 1024;
  const MAX_RESPONSE_BYTES = 512 * 1024;
  const opaquePattern = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
  const operationPattern = /^[A-Za-z0-9][A-Za-z0-9_.:-]{7,191}$/;
  const semverPattern = /^(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;
  const jurisdictionPattern = /^[A-Z]{2}(?:-[A-Z0-9]{1,16})?$/;
  const commonTrackers = new Set([
    "google_analytics", "google_tag_manager", "linkedin_insight",
    "meta_pixel", "microsoft_clarity", "tiktok_pixel",
  ]);
  const analyticsCategories = new Set(["advertising", "analytics", "essential", "functional"]);

  function failure(code, message, status) {
    const error = new Error(message);
    error.code = code;
    if (Number.isInteger(status)) error.status = status;
    return error;
  }

  function integer(value, label, minimum, maximum) {
    if (typeof value === "string" && !/^(0|[1-9][0-9]*)$/.test(value.trim())) {
      throw failure("field_invalid", `${label} must be an integer.`);
    }
    const selected = typeof value === "string" ? Number(value.trim()) : value;
    if (!Number.isSafeInteger(selected) || selected < minimum || selected > maximum) {
      throw failure("field_invalid", `${label} is outside its allowed range.`);
    }
    return selected;
  }

  function opaque(value, label) {
    if (typeof value !== "string" || !opaquePattern.test(value.trim())) {
      throw failure("field_invalid", `${label} must be a bounded opaque identifier.`);
    }
    return value.trim();
  }

  function operation(value) {
    if (typeof value !== "string" || !operationPattern.test(value.trim())) {
      throw failure("field_invalid", "Enter a valid request reference; reuse it only when retrying the same action.");
    }
    return value.trim();
  }

  function strictBoolean(value, label) {
    if (typeof value !== "boolean") {
      throw failure("field_invalid", `${label} must be true or false.`);
    }
    return value;
  }

  function parsePairs(value, kind) {
    const lines = Array.isArray(value) ? value : String(value || "").split(/\r?\n/);
    const result = [];
    const seen = new Set();
    for (const raw of lines) {
      if (raw === null || raw === undefined || String(raw).trim() === "") continue;
      let left;
      let right;
      if (typeof raw === "object" && !Array.isArray(raw)) {
        left = kind === "geo" ? raw.jurisdiction : raw.tracker;
        right = kind === "geo" ? raw.reconsent_after_days : raw.category;
      } else {
        const parts = String(raw).split("=");
        if (parts.length !== 2) throw failure("field_invalid", "Each mapping must contain one equals sign.");
        [left, right] = parts.map((item) => item.trim());
      }
      if (kind === "geo") {
        if (typeof left !== "string" || !jurisdictionPattern.test(left) || seen.has(left)) {
          throw failure("field_invalid", "Geolocation reconsent entries must use unique bounded jurisdictions.");
        }
        seen.add(left);
        result.push({ jurisdiction: left, reconsent_after_days: integer(right, "Reconsent days", 1, 3650) });
      } else {
        if (!commonTrackers.has(left) || !analyticsCategories.has(right) || seen.has(left)) {
          throw failure("field_invalid", "Analytics category assignments are invalid or duplicated.");
        }
        seen.add(left);
        result.push({ tracker: left, category: right });
      }
    }
    const key = kind === "geo" ? "jurisdiction" : "tracker";
    return result.sort((left, right) => left[key].localeCompare(right[key]));
  }

  function normalizeTrackers(value) {
    if (!Array.isArray(value)) throw failure("field_invalid", "Common trackers must be a list.");
    const result = [...new Set(value)];
    if (result.length !== value.length || result.length > commonTrackers.size || result.some((item) => !commonTrackers.has(item))) {
      throw failure("field_invalid", "Common trackers are invalid or duplicated.");
    }
    return result.sort();
  }

  function buildSettings(value) {
    const sdkVersion = typeof value.sdk_version === "string" ? value.sdk_version.trim() : "";
    if (!semverPattern.test(sdkVersion)) throw failure("field_invalid", "SDK version must be bounded semantic versioning.");
    const trackers = normalizeTrackers(value.common_trackers);
    const assignments = parsePairs(value.analytics_category_assignments, "analytics");
    if (assignments.some((item) => !trackers.includes(item.tracker))) {
      throw failure("field_invalid", "Every category assignment must name an enabled common tracker.");
    }
    if (!new Set(["strict", "lax", "none"]).has(value.cookie_same_site)) {
      throw failure("field_invalid", "Cookie SameSite policy is invalid.");
    }
    if (!new Set(["host_only", "property_domain"]).has(value.cookie_domain_separation)) {
      throw failure("field_invalid", "Cookie domain separation is invalid.");
    }
    const refresh = value.automatic_refresh_seconds === "" || value.automatic_refresh_seconds === null || value.automatic_refresh_seconds === undefined
      ? null : integer(value.automatic_refresh_seconds, "Automatic refresh seconds", 30, 86400);
    return {
      sdk_version: sdkVersion,
      geolocation_reconsent: parsePairs(value.geolocation_reconsent, "geo"),
      analytics_tracking_enabled: strictBoolean(value.analytics_tracking_enabled, "Analytics tracking"),
      analytics_category_assignments: assignments,
      common_trackers: trackers,
      cookie_same_site: value.cookie_same_site,
      cookie_domain_separation: value.cookie_domain_separation,
      spa_reload_on_route_change: strictBoolean(value.spa_reload_on_route_change, "SPA reload"),
      csp_nonce_required: strictBoolean(value.csp_nonce_required, "CSP requirement"),
      automatic_refresh_seconds: refresh,
    };
  }

  function buildCreatePayload(value) {
    return {
      expected_head_revision: integer(value.expected_head_revision, "Expected head revision", 0, 2147483647),
      idempotency_key: operation(value.idempotency_key),
      settings: buildSettings(value),
    };
  }

  function buildRevisionPayload(value) {
    return {
      expected_head_revision: integer(value.expected_head_revision, "Expected head revision", 1, 2147483647),
      idempotency_key: operation(value.idempotency_key),
    };
  }

  function boundedBody(payload) {
    const body = JSON.stringify(payload);
    if (new TextEncoder().encode(body).byteLength > MAX_REQUEST_BYTES) {
      throw failure("request_too_large", "The JSON request exceeds 64 KiB.");
    }
    return body;
  }

  async function readBoundedJson(response) {
    const declaredRaw = response.headers && response.headers.get("content-length");
    const declared = declaredRaw === null ? null : Number(declaredRaw);
    if (declared !== null && Number.isFinite(declared) && declared > MAX_RESPONSE_BYTES) {
      if (response.body && typeof response.body.cancel === "function") await response.body.cancel();
      throw failure("response_too_large", "The server response exceeds 512 KiB.", response.status);
    }
    let text;
    if (response.body && typeof response.body.getReader === "function") {
      const reader = response.body.getReader();
      const chunks = [];
      let total = 0;
      while (true) {
        const item = await reader.read();
        if (item.done) break;
        total += item.value.byteLength;
        if (total > MAX_RESPONSE_BYTES) {
          if (typeof reader.cancel === "function") await reader.cancel();
          throw failure("response_too_large", "The server response exceeds 512 KiB.", response.status);
        }
        chunks.push(item.value);
      }
      const merged = new Uint8Array(total);
      let offset = 0;
      for (const chunk of chunks) { merged.set(chunk, offset); offset += chunk.byteLength; }
      text = new TextDecoder("utf-8", { fatal: true }).decode(merged);
    } else {
      text = await response.text();
      if (new TextEncoder().encode(text).byteLength > MAX_RESPONSE_BYTES) {
        throw failure("response_too_large", "The server response exceeds 512 KiB.", response.status);
      }
    }
    try {
      return JSON.parse(text);
    } catch (_error) {
      throw failure("response_invalid_json", "The server returned invalid JSON.", response.status);
    }
  }

  async function parseResponse(response) {
    const payload = await readBoundedJson(response);
    if (!response.ok) {
      const message = payload && typeof payload.detail === "string" ? payload.detail : "The operation failed.";
      throw failure("request_failed", message, response.status);
    }
    return payload;
  }

  const api = {
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    boundedBody,
    buildCreatePayload,
    buildRevisionPayload,
    buildSettings,
    normalizeTrackers,
    parsePairs,
    parseResponse,
    readBoundedJson,
  };
  global.LightworkConsentPublishSettingsClient = Object.freeze(api);

  if (typeof document === "undefined") return;
  const shell = document.getElementById("publish-settings-shell");
  if (!shell) return;
  const apiBase = shell.dataset.apiBase || "";
  const operations = document.getElementById("publish-settings-operations");
  const runtimeStatus = document.getElementById("publish-settings-runtime-status");
  const statusOutput = document.getElementById("publish-settings-status-output");
  const operationStatus = document.getElementById("publish-settings-operation-status");
  const operationOutput = document.getElementById("publish-settings-operation-output");

  function element(id) { return document.getElementById(id); }
  function field(id) { return element(id).value.trim(); }
  function checked(id) { return element(id).checked; }
  function setStatus(target, message, failed) {
    target.textContent = message;
    target.dataset.state = failed ? "failed" : "ready";
  }
  function render(target, payload) { target.textContent = JSON.stringify(payload, null, 2); }
  function propertyPath() { return encodeURIComponent(opaque(field("publish-settings-property-id"), "Property reference")); }
  function selectedVersion() { return encodeURIComponent(opaque(field("publish-settings-version-id"), "Version reference")); }

  async function request(path, options) {
    const response = await fetch(`${apiBase}${path}`, {
      cache: "no-store",
      credentials: "same-origin",
      redirect: "error",
      referrerPolicy: "no-referrer",
      ...(options || {}),
      headers: { Accept: "application/json", ...((options && options.headers) || {}) },
    });
    return parseResponse(response);
  }
  function post(path, payload) {
    return request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: boundedBody(payload) });
  }
  function hydrate(payload) {
    if (!payload || typeof payload !== "object") return;
    if (typeof payload.version_id === "string") element("publish-settings-version-id").value = payload.version_id;
    if (Number.isSafeInteger(payload.head_revision) && payload.head_revision > 0) {
      element("publish-settings-lifecycle-revision").value = payload.head_revision;
      element("publish-settings-expected-revision").value = payload.head_revision;
    }
    if (Number.isSafeInteger(payload.revision) && payload.revision > 0) {
      element("publish-settings-lifecycle-revision").value = payload.revision;
      element("publish-settings-expected-revision").value = payload.revision;
    }
  }
  async function run(label, callback) {
    setStatus(operationStatus, `${label}…`, false);
    operationOutput.textContent = "";
    try {
      const payload = await callback();
      render(operationOutput, payload);
      hydrate(payload);
      setStatus(operationStatus, `${label} completed.`, false);
      return payload;
    } catch (error) {
      setStatus(operationStatus, `${label} failed: ${error.message}`, true);
      throw error;
    }
  }
  async function refreshStatus() {
    operations.disabled = true;
    setStatus(runtimeStatus, "Checking the server-owned publishing authority…", false);
    statusOutput.textContent = "";
    try {
      const payload = await request("/status");
      render(statusOutput, payload);
      if (payload.ready === true) {
        operations.disabled = false;
        setStatus(runtimeStatus, "Website publish-settings authority is ready.", false);
      } else if (payload.enabled === true) {
        setStatus(runtimeStatus, "Enabled, but the publishing authority is not ready.", true);
      } else {
        setStatus(runtimeStatus, "Website publish settings are disabled for this workspace.", true);
      }
    } catch (error) { setStatus(runtimeStatus, `Status unavailable: ${error.message}`, true); }
  }

  element("publish-settings-refresh-status").addEventListener("click", refreshStatus);
  element("publish-settings-create-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const trackers = [...document.querySelectorAll('input[name="common_trackers"]:checked')].map((item) => item.value);
      const payload = buildCreatePayload({
        expected_head_revision: field("publish-settings-expected-revision"),
        idempotency_key: field("publish-settings-idempotency"),
        sdk_version: field("publish-settings-sdk-version"),
        geolocation_reconsent: element("publish-settings-geolocation").value,
        analytics_tracking_enabled: checked("publish-settings-analytics-enabled"),
        analytics_category_assignments: element("publish-settings-assignments").value,
        common_trackers: trackers,
        cookie_same_site: field("publish-settings-same-site"),
        cookie_domain_separation: field("publish-settings-domain-separation"),
        spa_reload_on_route_change: checked("publish-settings-spa-reload"),
        csp_nonce_required: checked("publish-settings-csp-required"),
        automatic_refresh_seconds: field("publish-settings-refresh-seconds"),
      });
      await run("Creating settings version", () => post(`/properties/${propertyPath()}/versions`, payload));
    } catch (_error) { /* The bounded status region already reports the failure. */ }
  });
  element("publish-settings-load").addEventListener("click", async () => {
    try { await run("Loading settings head", () => request(`/properties/${propertyPath()}`)); } catch (_error) { /* rendered */ }
  });
  function revisionPayload() {
    return buildRevisionPayload({
      expected_head_revision: field("publish-settings-lifecycle-revision"),
      idempotency_key: field("publish-settings-lifecycle-idempotency"),
    });
  }
  element("publish-settings-activate").addEventListener("click", async () => {
    try { await run("Activating settings", () => post(`/properties/${propertyPath()}/versions/${selectedVersion()}/activate`, revisionPayload())); } catch (_error) { /* rendered */ }
  });
  element("publish-settings-rollback").addEventListener("click", async () => {
    try { await run("Rolling back settings", () => post(`/properties/${propertyPath()}/rollback`, { ...revisionPayload(), target_version_id: opaque(field("publish-settings-version-id"), "Version reference") })); } catch (_error) { /* rendered */ }
  });
  element("publish-settings-withdraw").addEventListener("click", async () => {
    try { await run("Withdrawing settings", () => post(`/properties/${propertyPath()}/withdraw`, revisionPayload())); } catch (_error) { /* rendered */ }
  });
  element("publish-settings-versions").addEventListener("click", async () => {
    try { await run("Listing versions", () => request(`/properties/${propertyPath()}/versions?limit=100`)); } catch (_error) { /* rendered */ }
  });
  element("publish-settings-history").addEventListener("click", async () => {
    try { await run("Loading audit history", () => request(`/operations/history?property_id=${propertyPath()}&limit=100`)); } catch (_error) { /* rendered */ }
  });
  element("publish-settings-integrity").addEventListener("click", async () => {
    try { await run("Verifying integrity", () => request("/operations/integrity")); } catch (_error) { /* rendered */ }
  });
  element("publish-settings-recovery").addEventListener("click", async () => {
    try { await run("Checking recovery readiness", () => request("/operations/recovery")); } catch (_error) { /* rendered */ }
  });
  refreshStatus();
})(typeof window === "undefined" ? globalThis : window);
