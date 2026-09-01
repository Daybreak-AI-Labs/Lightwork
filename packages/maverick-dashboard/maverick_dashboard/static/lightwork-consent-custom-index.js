"use strict";

(function consentCustomIndexWorkbench() {
  const REQUEST_LIMIT = 64 * 1024;
  const RESPONSE_LIMIT = 512 * 1024;
  const opaquePattern = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
  const shell = document.getElementById("custom-index-shell");
  if (!shell) return;

  const apiBase = shell.dataset.apiBase || "";
  const operations = document.getElementById("custom-index-operations");
  const runtimeStatus = document.getElementById("custom-index-runtime-status");
  const statusOutput = document.getElementById("custom-index-status-output");
  const operationStatus = document.getElementById("custom-index-operation-status");
  const operationOutput = document.getElementById("custom-index-operation-output");

  function setStatus(element, message, failed) {
    element.textContent = message;
    element.dataset.state = failed ? "failed" : "ready";
  }

  function render(element, payload) {
    element.textContent = JSON.stringify(payload, null, 2);
  }

  function field(id) {
    return document.getElementById(id).value.trim();
  }

  function positiveInteger(id) {
    const selected = Number(field(id));
    if (!Number.isSafeInteger(selected) || selected < 1) {
      throw new Error(`${id} must be a positive integer.`);
    }
    return selected;
  }

  function opaque(value, label) {
    if (!opaquePattern.test(value)) {
      throw new Error(`${label} must be a bounded opaque identifier.`);
    }
    return value;
  }

  function selectedIndex() {
    return encodeURIComponent(opaque(field("custom-index-selected-id"), "Index reference"));
  }

  function boundedBody(payload) {
    const body = JSON.stringify(payload);
    if (new TextEncoder().encode(body).byteLength > REQUEST_LIMIT) {
      throw new Error("The JSON request exceeds 64 KiB.");
    }
    return body;
  }

  async function boundedResponse(response) {
    const declared = Number(response.headers.get("content-length"));
    if (Number.isFinite(declared) && declared > RESPONSE_LIMIT) {
      throw new Error("The server response exceeds the workbench display bound.");
    }
    if (!response.body || typeof response.body.getReader !== "function") {
      const text = await response.text();
      if (new TextEncoder().encode(text).byteLength > RESPONSE_LIMIT) {
        throw new Error("The server response exceeds the workbench display bound.");
      }
      return JSON.parse(text);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8", { fatal: true });
    let received = 0;
    let text = "";
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      received += chunk.value.byteLength;
      if (received > RESPONSE_LIMIT) {
        await reader.cancel();
        throw new Error("The server response exceeds the workbench display bound.");
      }
      text += decoder.decode(chunk.value, { stream: true });
    }
    text += decoder.decode();
    return JSON.parse(text);
  }

  async function request(path, options) {
    const response = await fetch(`${apiBase}${path}`, {
      cache: "no-store",
      credentials: "same-origin",
      redirect: "error",
      referrerPolicy: "no-referrer",
      ...(options || {}),
      headers: {
        Accept: "application/json",
        ...((options && options.headers) || {}),
      },
    });
    let payload;
    try {
      payload = await boundedResponse(response);
    } catch (_error) {
      const invalid = new Error("The server returned invalid or oversized JSON.");
      invalid.status = response.status;
      throw invalid;
    }
    if (!response.ok) {
      const failed = new Error(
        payload && typeof payload.detail === "string" ?
          payload.detail : "The operation failed."
      );
      failed.status = response.status;
      throw failed;
    }
    return payload;
  }

  function post(path, payload) {
    return request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: boundedBody(payload),
    });
  }

  function hydrateIndex(payload) {
    if (!payload || typeof payload !== "object") return;
    if (typeof payload.index_id === "string") {
      document.getElementById("custom-index-selected-id").value = payload.index_id;
    }
    if (Number.isSafeInteger(payload.revision) && payload.revision > 0) {
      document.getElementById("custom-index-build-revision").value = payload.revision;
      document.getElementById("custom-index-revoke-revision").value = payload.revision;
    }
  }

  async function run(label, operation) {
    setStatus(operationStatus, `${label}…`, false);
    operationOutput.textContent = "";
    try {
      const payload = await operation();
      render(operationOutput, payload);
      hydrateIndex(payload);
      setStatus(operationStatus, `${label} completed.`, false);
      return payload;
    } catch (error) {
      const suffix = Number.isInteger(error.status) ? ` (${error.status})` : "";
      setStatus(operationStatus, `${label} failed${suffix}: ${error.message}`, true);
      throw error;
    }
  }

  async function refreshStatus() {
    operations.disabled = true;
    statusOutput.textContent = "";
    setStatus(runtimeStatus, "Checking server-owned custody and source readiness…", false);
    try {
      const payload = await request("/status");
      render(statusOutput, payload);
      if (payload.ready === true) {
        operations.disabled = false;
        setStatus(runtimeStatus, "Custom-index custody and source storage are ready.", false);
      } else if (payload.enabled === true) {
        setStatus(runtimeStatus, "Custom indexes are enabled, but custody or source storage is not ready.", true);
      } else {
        setStatus(runtimeStatus, "Custom indexes are disabled for the active tenant.", true);
      }
    } catch (error) {
      setStatus(runtimeStatus, `Status unavailable: ${error.message}`, true);
    }
  }

  document.getElementById("custom-index-refresh-status").addEventListener("click", refreshStatus);

  document.getElementById("custom-index-create-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await run("Creating custom index", () => post("", {
        index_id: opaque(field("custom-index-create-id"), "Index reference"),
        element_name: opaque(field("custom-index-element-name"), "Element name"),
        idempotency_key: opaque(field("custom-index-create-operation"), "Idempotency key"),
      }));
    } catch (_error) {
      // The shared status region already contains the bounded error.
    }
  });

  document.getElementById("custom-index-load-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await run("Loading custom index", () => request(`/${selectedIndex()}`));
    } catch (_error) {
      // Status already rendered.
    }
  });

  document.getElementById("custom-index-list").addEventListener("click", async () => {
    try {
      const params = new URLSearchParams();
      params.set("limit", String(positiveInteger("custom-index-list-limit")));
      const cursor = field("custom-index-list-cursor");
      if (cursor) params.set("cursor", opaque(cursor, "List cursor"));
      await run("Listing custom indexes", () => request(`?${params.toString()}`));
    } catch (_error) {
      // Status already rendered.
    }
  });

  document.getElementById("custom-index-build-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await run("Building custom index", () => post(`/${selectedIndex()}/build`, {
        expected_revision: positiveInteger("custom-index-build-revision"),
        idempotency_key: opaque(field("custom-index-build-operation"), "Idempotency key"),
      }));
    } catch (_error) {
      // Status already rendered.
    }
  });

  document.getElementById("custom-index-revoke-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const confirmation = document.getElementById("custom-index-revoke-confirmation");
    if (!confirmation.checked) {
      setStatus(operationStatus, "Confirm revocation before continuing.", true);
      return;
    }
    try {
      await run("Revoking custom index", () => post(`/${selectedIndex()}/revoke`, {
        expected_revision: positiveInteger("custom-index-revoke-revision"),
        idempotency_key: opaque(field("custom-index-revoke-operation"), "Idempotency key"),
      }));
      confirmation.checked = false;
    } catch (_error) {
      // Status already rendered.
    }
  });

  function searchValue() {
    const mode = field("custom-index-value-type");
    const selected = field("custom-index-search-value");
    if (mode === "boolean") {
      if (selected !== "true" && selected !== "false") {
        throw new Error("Boolean search must be exactly true or false.");
      }
      return selected === "true";
    }
    if (mode === "list") {
      const values = selected.split(",").map((item) => opaque(item.trim(), "List value"));
      if (values.length < 1 || values.length > 64 || new Set(values).size !== values.length) {
        throw new Error("List search requires 1 to 64 unique opaque value IDs.");
      }
      return values;
    }
    return opaque(selected, "Search value");
  }

  document.getElementById("custom-index-search-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const cursor = field("custom-index-search-cursor");
      await run("Searching custom index", () => post(`/${selectedIndex()}/search`, {
        value: searchValue(),
        limit: positiveInteger("custom-index-search-limit"),
        cursor: cursor ? opaque(cursor, "Search cursor") : null,
      }));
    } catch (_error) {
      // Status already rendered.
    }
  });

  document.getElementById("custom-index-history").addEventListener("click", async () => {
    try {
      await run("Loading custom-index audit history", () => request("/operations/history"));
    } catch (_error) {
      // Status already rendered.
    }
  });

  document.getElementById("custom-index-integrity").addEventListener("click", async () => {
    try {
      await run("Verifying custom-index integrity", () => request("/operations/integrity"));
    } catch (_error) {
      // Status already rendered.
    }
  });

  if (apiBase) refreshStatus();
}());
