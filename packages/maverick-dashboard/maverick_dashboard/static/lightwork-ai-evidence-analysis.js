"use strict";

const shell = document.getElementById("evidence-shell");
const apiBase = document.body.dataset.apiBase;
const runtimeStatus = document.getElementById("runtime-status");
const statusOutput = document.getElementById("status-output");
const operationStatus = document.getElementById("operation-status");
const operationOutput = document.getElementById("operation-output");

function setStatus(element, message, failed = false) {
  element.textContent = message;
  element.dataset.state = failed ? "failed" : "ready";
}

function render(element, value) {
  element.textContent = JSON.stringify(value, null, 2);
}

async function apiRequest(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    cache: "no-store",
    credentials: "same-origin",
    redirect: "error",
    referrerPolicy: "no-referrer",
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.headers || {}),
    },
  });
  let payload;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = { detail: "The server returned a non-JSON response." };
  }
  if (!response.ok) {
    const error = new Error(
      typeof payload.detail === "string" ? payload.detail : "The operation failed."
    );
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function run(label, operation) {
  setStatus(operationStatus, `${label}…`);
  operationOutput.textContent = "";
  try {
    const payload = await operation();
    render(operationOutput, payload);
    setStatus(operationStatus, `${label} completed.`);
    return payload;
  } catch (error) {
    const status = Number.isInteger(error.status) ? ` (${error.status})` : "";
    setStatus(operationStatus, `${label} failed${status}: ${error.message}`, true);
    throw error;
  }
}

function value(id) {
  return document.getElementById(id).value.trim();
}

function integer(id) {
  const parsed = Number(value(id));
  if (!Number.isSafeInteger(parsed) || parsed < 1) {
    throw new Error(`${id} must be a positive integer.`);
  }
  return parsed;
}

function analysisId() {
  const selected = value("analysis-id");
  if (!selected) {
    throw new Error("Enter an analysis reference first.");
  }
  return encodeURIComponent(selected);
}

async function refreshStatus() {
  setStatus(runtimeStatus, "Checking the tenant-scoped server composition…");
  statusOutput.textContent = "";
  try {
    const payload = await apiRequest("/status");
    render(statusOutput, payload);
    if (payload.ready === true) {
      setStatus(runtimeStatus, "Evidence analysis is enabled and composed for operations.");
    } else if (payload.enabled === true) {
      setStatus(runtimeStatus, "Evidence analysis is enabled but server composition is incomplete.", true);
    } else {
      setStatus(runtimeStatus, "Evidence analysis is disabled for this tenant.", true);
    }
  } catch (error) {
    setStatus(runtimeStatus, `Status unavailable: ${error.message}`, true);
  }
}

document.getElementById("refresh-status").addEventListener("click", refreshStatus);

document.getElementById("analyze-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const fileInput = document.getElementById("artifact-file");
  const file = fileInput.files[0];
  if (!file) {
    setStatus(operationStatus, "Select an artifact file.", true);
    return;
  }
  const acquired = Date.parse(value("acquired-at"));
  if (!Number.isFinite(acquired) || acquired <= 0) {
    setStatus(operationStatus, "Enter a valid acquisition timestamp.", true);
    return;
  }
  let operationStarted = false;
  try {
    const metadata = {
      schema: "lightwork.ai-evidence.upload-metadata.v1",
      artifact_id: value("artifact-id"),
      source_ref: value("source-ref"),
      source_revision: integer("source-revision"),
      acquired_at: acquired / 1000,
      expected_artifact_authority_sha256: value("artifact-authority-digest"),
      criteria_set_id: value("criteria-set-id"),
      expected_criteria_revision: integer("criteria-revision"),
      expected_criteria_authority_sha256: value("criteria-authority-digest"),
      idempotency_key: value("analyze-operation-id"),
    };
    operationStarted = true;
    const payload = await run("Creating pending analysis", () =>
      apiRequest("/analyses", {
        method: "POST",
        body: file,
        headers: {
          "Content-Type": file.type,
          "X-Lightwork-Evidence-Metadata": JSON.stringify(metadata),
        },
      })
    );
    document.getElementById("analysis-id").value = payload.analysis_id || "";
    document.getElementById("review-revision").value = payload.revision || "";
    document.getElementById("revoke-revision").value = payload.revision || "";
  } catch (error) {
    if (!operationStarted) {
      setStatus(operationStatus, `Upload metadata is invalid: ${error.message}`, true);
    }
  } finally {
    fileInput.value = "";
  }
});

document.getElementById("record-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await run("Loading current analysis", () => apiRequest(`/analyses/${analysisId()}`));
  } catch (_error) {
    // Status already rendered.
  }
});

document.getElementById("load-released").addEventListener("click", async () => {
  try {
    await run("Loading released analysis", () =>
      apiRequest(`/analyses/${analysisId()}/released`)
    );
  } catch (_error) {
    // Status already rendered.
  }
});

document.getElementById("load-history").addEventListener("click", async () => {
  try {
    await run("Loading analysis history", () =>
      apiRequest(`/analyses/${analysisId()}/history`)
    );
  } catch (_error) {
    // Status already rendered.
  }
});

document.getElementById("review-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const payload = await run("Recording human review", () =>
      apiRequest(`/analyses/${analysisId()}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_revision: integer("review-revision"),
          decision: value("review-decision"),
          idempotency_key: value("review-operation-id"),
        }),
      })
    );
    document.getElementById("review-revision").value = payload.revision || "";
    document.getElementById("revoke-revision").value = payload.revision || "";
  } catch (_error) {
    // Status already rendered.
  }
});

document.getElementById("revoke-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const payload = await run("Revoking analysis", () =>
      apiRequest(`/analyses/${analysisId()}/revoke`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_revision: integer("revoke-revision"),
          reason_code: value("revocation-reason"),
          idempotency_key: value("revoke-operation-id"),
        }),
      })
    );
    document.getElementById("revoke-revision").value = payload.revision || "";
  } catch (_error) {
    // Status already rendered.
  }
});

document.getElementById("verify-integrity").addEventListener("click", async () => {
  try {
    await run("Verifying tenant integrity", () => apiRequest("/integrity"));
  } catch (_error) {
    // Status already rendered.
  }
});

document.getElementById("retention-due").addEventListener("click", async () => {
  try {
    await run("Listing retention-due analyses", () =>
      apiRequest(`/retention/due?limit=${integer("purge-limit")}`)
    );
  } catch (_error) {
    // Status already rendered.
  }
});

document.getElementById("purge-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const confirmation = document.getElementById("purge-confirmation");
  if (!confirmation.checked) {
    setStatus(operationStatus, "Confirm the retention purge before continuing.", true);
    return;
  }
  try {
    await run("Purging retention-due analyses", () =>
      apiRequest("/retention/purge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: integer("purge-limit") }),
      })
    );
    confirmation.checked = false;
  } catch (_error) {
    // Status already rendered.
  }
});

if (shell && apiBase) {
  refreshStatus();
}
