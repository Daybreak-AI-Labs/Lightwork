"use strict";

const shell = document.getElementById("evidence-shell");
const apiBase = shell ? shell.dataset.apiBase : "";
const runtimeStatus = document.getElementById("runtime-status");
const statusOutput = document.getElementById("status-output");
const operations = document.getElementById("evidence-operations");
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
    const error = new Error("The server returned an invalid JSON response.");
    error.status = response.status;
    throw error;
  }
  if (!response.ok) {
    const error = new Error(
      payload && typeof payload.detail === "string" ?
        payload.detail : "The operation failed."
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
  setStatus(runtimeStatus, "Checking service availability…");
  statusOutput.textContent = "";
  operations.disabled = true;
  try {
    const payload = await apiRequest("/status");
    render(statusOutput, payload);
    const tesseractDetected = payload.external_parser_binaries_detected &&
      payload.external_parser_binaries_detected.tesseract === true;
    const ocrNotice = tesseractDetected ? "" :
      " Image text recognition is unavailable because OCR support is not installed.";
    if (payload.ready === true && payload.operational_composition_verified === true) {
      setStatus(runtimeStatus, `Evidence analysis is ready.${ocrNotice}`);
      operations.disabled = false;
    } else if (payload.configured_for_operation_attempt === true) {
      setStatus(
        runtimeStatus,
        "Evidence analysis is configured. " +
          "Document access and permissions have not been checked yet; " +
          `they will be checked when you start.${ocrNotice}`
      );
      operations.disabled = false;
    } else if (payload.enabled === true) {
      setStatus(runtimeStatus, "Evidence analysis is enabled, but setup is incomplete.", true);
    } else {
      setStatus(runtimeStatus, "Evidence analysis is disabled for this workspace.", true);
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
