"use strict";

(function governedDocumentVaultClient(global) {
  const MAX_JSON_BYTES = 512 * 1024;
  const ABSOLUTE_MAX_DOCUMENT_BYTES = 100 * 1024 * 1024;
  const opaquePattern = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
  const recordPattern = /^[a-z][a-z0-9_.-]{0,95}$/;
  const operationPattern = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/;
  const tokenPattern = /^lwddt_v1_[A-Za-z0-9_-]{43}$/;

  function failure(code, message, status) {
    const error = new Error(message);
    error.code = code;
    if (Number.isInteger(status)) error.status = status;
    return error;
  }

  function bounded(value, pattern, label) {
    if (typeof value !== "string" || !pattern.test(value.trim())) {
      throw failure("field_invalid", `${label} is invalid.`);
    }
    return value.trim();
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

  function jsonBody(payload) {
    const body = JSON.stringify(payload);
    if (new TextEncoder().encode(body).byteLength > 64 * 1024) {
      throw failure("request_too_large", "The JSON request exceeds 64 KiB.");
    }
    return body;
  }

  async function readBoundedJson(response) {
    const declaredRaw = response.headers.get("content-length");
    const declared = declaredRaw === null ? null : Number(declaredRaw);
    if (declared !== null && Number.isFinite(declared) && declared > MAX_JSON_BYTES) {
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
        if (total > MAX_JSON_BYTES) {
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
      if (new TextEncoder().encode(text).byteLength > MAX_JSON_BYTES) {
        throw failure("response_too_large", "The server response exceeds 512 KiB.", response.status);
      }
    }
    try { return JSON.parse(text); }
    catch (_error) { throw failure("response_invalid_json", "The server returned invalid JSON.", response.status); }
  }

  async function parseJsonResponse(response) {
    const payload = await readBoundedJson(response);
    if (!response.ok) {
      const message = payload && typeof payload.detail === "string" ? payload.detail : "The operation failed.";
      throw failure("request_failed", message, response.status);
    }
    return payload;
  }

  const api = Object.freeze({
    ABSOLUTE_MAX_DOCUMENT_BYTES,
    MAX_JSON_BYTES,
    bounded,
    integer,
    jsonBody,
    parseJsonResponse,
    readBoundedJson,
  });
  global.LightworkGovernedDocumentVaultClient = api;

  if (typeof document === "undefined") return;
  const shell = document.getElementById("document-vault-shell");
  if (!shell) return;
  const vaultBase = shell.dataset.vaultApiBase || "";
  const accessBase = shell.dataset.accessApiBase || "";
  const operations = document.getElementById("document-vault-operations");
  const runtimeStatus = document.getElementById("document-vault-runtime-status");
  const statusOutput = document.getElementById("document-vault-status-output");
  const operationStatus = document.getElementById("document-vault-operation-status");
  const operationOutput = document.getElementById("document-vault-operation-output");
  let configuredMaxBytes = ABSOLUTE_MAX_DOCUMENT_BYTES;
  let documents = [];

  function element(id) { return document.getElementById(id); }
  function field(id) { return element(id).value.trim(); }
  function render(target, payload) { target.textContent = JSON.stringify(payload, null, 2); }
  function setStatus(target, message, failed) {
    target.textContent = message;
    target.dataset.state = failed ? "failed" : "ready";
  }

  async function requestJson(base, path, options) {
    const response = await fetch(`${base}${path}`, {
      cache: "no-store",
      credentials: "same-origin",
      redirect: "error",
      referrerPolicy: "no-referrer",
      ...(options || {}),
      headers: { Accept: "application/json", ...((options && options.headers) || {}) },
    });
    return parseJsonResponse(response);
  }

  function postJson(base, path, payload) {
    return requestJson(base, path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: jsonBody(payload),
    });
  }

  async function run(label, callback) {
    setStatus(operationStatus, `${label}…`, false);
    try {
      const result = await callback();
      if (result !== undefined) render(operationOutput, result);
      setStatus(operationStatus, `${label} completed.`, false);
      return result;
    } catch (error) {
      setStatus(operationStatus, error && error.message ? error.message : `${label} failed.`, true);
      throw error;
    }
  }

  function hydrate(record) {
    if (!record || typeof record !== "object") return;
    if (typeof record.document_ref === "string") element("document-vault-document-ref").value = record.document_ref;
    if (Number.isSafeInteger(record.authority_revision)) element("document-vault-authority-revision").value = record.authority_revision;
  }

  async function refreshStatus() {
    operations.disabled = true;
    setStatus(runtimeStatus, "Checking encrypted custody…", false);
    try {
      const payload = await requestJson(vaultBase, "/status");
      render(statusOutput, payload);
      const limit = payload && payload.limits && payload.limits.max_document_bytes;
      if (Number.isSafeInteger(limit) && limit > 0 && limit <= ABSOLUTE_MAX_DOCUMENT_BYTES) configuredMaxBytes = limit;
      if (payload.enabled === true && payload.configured === true && payload.ready === true) {
        operations.disabled = false;
        setStatus(runtimeStatus, "Encrypted tenant custody is ready.", false);
      } else {
        setStatus(runtimeStatus, "The vault is disabled, incomplete, or not ready.", true);
      }
    } catch (error) {
      render(statusOutput, { error: error.message });
      setStatus(runtimeStatus, error.message, true);
    }
  }

  async function refreshDocuments() {
    return run("Refreshing documents", async () => {
      const payload = await requestJson(vaultBase, "/documents?limit=100");
      documents = Array.isArray(payload.items) ? payload.items : [];
      if (documents.length) hydrate(documents[0]);
      return payload;
    });
  }

  element("document-vault-ingest-form").addEventListener("submit", (event) => {
    event.preventDefault();
    run("Encrypting and ingesting document", async () => {
      const input = element("document-vault-file");
      const file = input.files && input.files[0];
      if (!file) throw failure("field_invalid", "Choose one document.");
      if (file.size < 1 || file.size > configuredMaxBytes) throw failure("field_invalid", "The document exceeds the configured size limit.");
      const mediaType = file.type || "application/octet-stream";
      const params = new URLSearchParams({
        product_area: field("document-vault-product-area"),
        record_type: bounded(field("document-vault-record-type"), recordPattern, "Record type"),
        safe_display_name: file.name,
      });
      const response = await fetch(`${vaultBase}/documents?${params.toString()}`, {
        method: "POST",
        body: file,
        headers: { Accept: "application/json", "Content-Type": mediaType },
        cache: "no-store",
        credentials: "same-origin",
        redirect: "error",
        referrerPolicy: "no-referrer",
      });
      const payload = await parseJsonResponse(response);
      hydrate(payload.document);
      await refreshDocuments();
      return payload;
    }).catch(() => {});
  });

  element("document-vault-revoke").addEventListener("click", () => {
    run("Revoking document authority", async () => {
      const ref = bounded(field("document-vault-document-ref"), opaquePattern, "Document reference");
      const payload = await postJson(vaultBase, `/documents/${encodeURIComponent(ref)}/revoke`, {
        expected_revision: integer(field("document-vault-authority-revision"), "Authority revision", 1, 2147483647),
      });
      hydrate(payload);
      return payload;
    }).catch(() => {});
  });

  element("document-vault-token-form").addEventListener("submit", (event) => {
    event.preventDefault();
    run("Minting one-time download token", async () => {
      const payload = await postJson(accessBase, "/tokens", {
        document_ref: bounded(field("document-vault-document-ref"), opaquePattern, "Document reference"),
        ttl_seconds: integer(field("document-vault-token-ttl"), "Token TTL", 30, 3600),
        max_uses: 1,
        idempotency_key: bounded(field("document-vault-mint-idempotency"), operationPattern, "Idempotency key"),
      });
      if (typeof payload.download_token === "string") element("document-vault-download-token").value = payload.download_token;
      return payload;
    }).catch(() => {});
  });

  element("document-vault-delivery-form").addEventListener("submit", (event) => {
    event.preventDefault();
    run("Redeeming and downloading once", async () => {
      const redemption = await postJson(accessBase, "/tokens/redeem", {
        download_token: bounded(field("document-vault-download-token"), tokenPattern, "Download token"),
        idempotency_key: bounded(field("document-vault-claim-idempotency"), operationPattern, "Idempotency key"),
      });
      const response = await fetch(`${vaultBase}/delivery`, {
        method: "POST",
        body: jsonBody({ delivery_grant: redemption.delivery_grant }),
        headers: { Accept: "application/octet-stream", "Content-Type": "application/json" },
        cache: "no-store",
        credentials: "same-origin",
        redirect: "error",
        referrerPolicy: "no-referrer",
      });
      if (!response.ok) await parseJsonResponse(response);
      const declared = Number(response.headers.get("content-length"));
      if (!Number.isSafeInteger(declared) || declared < 0 || declared > configuredMaxBytes) {
        if (response.body && typeof response.body.cancel === "function") await response.body.cancel();
        throw failure("response_too_large", "The delivered document has an invalid size.", response.status);
      }
      const blob = await response.blob();
      if (blob.size !== declared) throw failure("response_size_changed", "The delivered document size changed.");
      const objectUrl = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement("a");
        const selected = documents.find((item) => item.document_ref === field("document-vault-document-ref"));
        anchor.href = objectUrl;
        anchor.download = selected && typeof selected.safe_display_name === "string" ? selected.safe_display_name : "governed-document";
        anchor.rel = "noopener noreferrer";
        anchor.hidden = true;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      } finally {
        URL.revokeObjectURL(objectUrl);
      }
      element("document-vault-download-token").value = "";
      return { downloaded: true, byte_size: blob.size, content_sha256: response.headers.get("x-content-sha256") };
    }).catch(() => {});
  });

  element("document-vault-refresh-status").addEventListener("click", refreshStatus);
  element("document-vault-refresh-documents").addEventListener("click", () => refreshDocuments().catch(() => {}));
  element("document-vault-audit").addEventListener("click", () => run("Loading audit history", () => requestJson(vaultBase, "/audit/events?limit=100")).catch(() => {}));
  element("document-vault-integrity").addEventListener("click", () => run("Verifying encrypted content", () => requestJson(vaultBase, "/integrity")).catch(() => {}));
  element("document-vault-reconcile").addEventListener("click", () => run("Reconciling delivery grants", () => postJson(vaultBase, "/maintenance/reconcile?limit=500", {})).catch(() => {}));
  refreshStatus().then(() => refreshDocuments()).catch(() => {});
})(typeof globalThis === "undefined" ? this : globalThis);
