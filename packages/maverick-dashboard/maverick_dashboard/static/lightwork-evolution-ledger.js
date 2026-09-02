(function () {
  "use strict";

  var root = document.getElementById("elv-root");
  if (!root) { return; }

  var apiBase = root.dataset.apiBase || "/api/v1/audit/evolution-ledger";
  var maxResponseBytes = 1024 * 1024;
  var maxReceiptBytes = 64 * 1024;
  var maxEntries = 500;
  var output = document.getElementById("elv-output");
  var state = document.getElementById("elv-state");
  var healthDetail = document.getElementById("elv-health-detail");
  var entriesBody = document.getElementById("elv-entries");
  var limitations = document.getElementById("elv-limitations");

  function fail(message) {
    var error = new Error(message);
    error.code = "invalid_input";
    return error;
  }

  function byteLength(text) {
    return new TextEncoder().encode(String(text || "")).length;
  }

  async function readBody(response) {
    var declared = Number(response.headers.get("content-length") || 0);
    if (declared > maxResponseBytes) {
      if (response.body && typeof response.body.cancel === "function") {
        await response.body.cancel();
      }
      throw fail("Response exceeds the Evolution Ledger console byte bound.");
    }
    if (!response.body || typeof response.body.getReader !== "function") {
      var fallback = await response.text();
      if (byteLength(fallback) > maxResponseBytes) {
        throw fail("Response exceeds the Evolution Ledger console byte bound.");
      }
      return fallback;
    }
    var reader = response.body.getReader();
    var chunks = [];
    var total = 0;
    try {
      while (true) {
        var next = await reader.read();
        if (next.done) { break; }
        total += next.value.byteLength;
        if (total > maxResponseBytes) {
          await reader.cancel();
          throw fail("Response exceeds the Evolution Ledger console byte bound.");
        }
        chunks.push(next.value);
      }
    } finally {
      reader.releaseLock();
    }
    var result = new Uint8Array(total);
    var position = 0;
    chunks.forEach(function (chunk) {
      result.set(chunk, position);
      position += chunk.byteLength;
    });
    try {
      return new TextDecoder("utf-8", { fatal: true }).decode(result);
    } catch (_error) {
      throw fail("The server returned invalid UTF-8.");
    }
  }

  async function readJson(response) {
    var text = await readBody(response);
    var payload;
    try { payload = text ? JSON.parse(text) : {}; }
    catch (_error) { throw fail("The server returned invalid JSON."); }
    if (!response.ok) {
      var detail = payload && typeof payload.detail === "string" ? payload.detail : "Evolution Ledger request failed.";
      throw fail(detail.slice(0, 500));
    }
    return payload;
  }

  async function request(path, options) {
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 15000);
    var settings = Object.assign(
      { credentials: "same-origin", headers: { Accept: "application/json" }, signal: controller.signal },
      options || {}
    );
    if (settings.body) { settings.headers["Content-Type"] = "application/json"; }
    try {
      return await fetch(apiBase + path, settings);
    } catch (error) {
      if (error && error.name === "AbortError") { throw fail("Evolution Ledger request timed out."); }
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }

  function setState(marker, text, detail) {
    state.dataset.state = marker;
    state.textContent = text;
    healthDetail.textContent = detail;
  }

  function setOutput(label, value) {
    output.textContent = label + "\n" + JSON.stringify(value, null, 2);
  }

  function cell(text) {
    var td = document.createElement("td");
    td.textContent = text;
    return td;
  }

  function codeCell(text) {
    var td = document.createElement("td");
    var code = document.createElement("code");
    code.textContent = text;
    td.appendChild(code);
    return td;
  }

  function shortDigest(value) {
    return typeof value === "string" && /^[0-9a-f]{64}$/.test(value) ? value.slice(0, 16) + "…" : "—";
  }

  function when(value) {
    var timestamp = Number(value);
    if (!Number.isFinite(timestamp) || timestamp <= 0) { return "—"; }
    return new Date(timestamp * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC";
  }

  function setMetric(id, value) {
    document.getElementById(id).textContent = String(value);
  }

  function renderEntries(entries) {
    entriesBody.textContent = "";
    if (!entries.length) {
      var empty = document.createElement("tr");
      var emptyCell = cell("No visible digest-only promotion or rollback entries.");
      emptyCell.colSpan = 6;
      empty.appendChild(emptyCell);
      entriesBody.appendChild(empty);
      return;
    }
    entries.forEach(function (entry) {
      var row = document.createElement("tr");
      row.appendChild(cell(when(entry.occurred_at)));
      row.appendChild(cell(typeof entry.kind === "string" ? entry.kind : "—"));
      row.appendChild(cell(typeof entry.integrity === "string" ? entry.integrity : "—"));
      row.appendChild(cell(typeof entry.approval_status === "string" ? entry.approval_status : "—"));
      row.appendChild(codeCell(shortDigest(entry.entry_sha256)));
      row.appendChild(codeCell(shortDigest(entry.promotion_receipt_sha256)));
      entriesBody.appendChild(row);
    });
  }

  function renderLimitations(items) {
    limitations.textContent = "";
    if (!items.length) {
      var none = document.createElement("li");
      none.textContent = "No additional projection limitation was supplied.";
      limitations.appendChild(none);
      return;
    }
    items.slice(0, 32).forEach(function (item) {
      var li = document.createElement("li");
      li.textContent = String(item).slice(0, 240);
      limitations.appendChild(li);
    });
  }

  function renderSnapshot(payload) {
    if (!payload || payload.schema !== "lightwork.evolution-ledger-dashboard-snapshot.v1") {
      throw fail("The server returned an unsupported Evolution Ledger snapshot.");
    }
    var projection = payload.projection;
    var sources = payload.sources;
    if (!projection || typeof projection !== "object" || !Array.isArray(projection.entries) || !sources || typeof sources !== "object") {
      throw fail("The server returned an incomplete Evolution Ledger projection.");
    }
    if (projection.entries.length > maxEntries) {
      throw fail("The server exceeded the visible Evolution Ledger entry bound.");
    }
    var entries = projection.entries;
    var promotions = entries.filter(function (entry) { return entry && entry.kind === "promotion"; }).length;
    var rollbacks = entries.filter(function (entry) { return entry && entry.kind === "rollback"; }).length;
    var promotionState = sources.promotion_ledger === "available" ? "available" : "not present";
    setMetric("elv-entry-count", projection.entry_count || 0);
    setMetric("elv-promotions", promotions);
    setMetric("elv-rollbacks", rollbacks);
    setMetric("elv-source-state", promotionState);
    document.getElementById("elv-table-note").textContent = String(projection.entry_count || 0) + " visible of " + String(projection.total_entries || 0) + " total entry/entries" + (projection.truncated ? "; oldest entries are intentionally omitted." : ".");
    renderEntries(entries);
    renderLimitations(Array.isArray(projection.limitations) ? projection.limitations : []);
    if (sources.promotion_ledger === "available") {
      setState("ready", "Read-only", "The durable promotion source was projected without offering a write, repair, approval, or rollback action.");
    } else {
      setState("partial", "No source", "No durable promotion ledger is present for this scope. That is not evidence that no learning occurred elsewhere.");
    }
  }

  async function loadSnapshot() {
    renderSnapshot(await readJson(await request("/snapshot")));
  }

  function renderReceiptVerdict(result) {
    var box = document.getElementById("elv-verdict-box");
    var verdict = document.getElementById("elv-verdict");
    var detail = document.getElementById("elv-verdict-detail");
    var valid = result && result.receipt_valid === true;
    box.hidden = false;
    verdict.dataset.valid = valid ? "true" : "false";
    verdict.textContent = valid ? "RECEIPT INTEGRITY VERIFIED" : "RECEIPT REJECTED";
    detail.textContent = "Approval status: " + String(result && result.approval_status || "unknown") + ". Human approval is not verified by this browser surface.";
    setOutput("Receipt verification", {
      receipt_valid: valid,
      receipt_sha256: result && result.receipt_sha256 || null,
      approval_status: result && result.approval_status || "unknown",
      approval_signer_key_id: result && result.approval_signer_key_id || null,
      limitations: Array.isArray(result && result.limitations) ? result.limitations : []
    });
  }

  document.getElementById("elv-refresh").addEventListener("click", function () {
    loadSnapshot().catch(function (error) {
      setState("failed", "Unavailable", "The read-only Evolution Ledger projection could not be loaded.");
      output.textContent = error && error.message ? error.message : "Evolution Ledger request failed.";
    });
  });

  document.getElementById("elv-verify-form").addEventListener("submit", function (event) {
    event.preventDefault();
    (async function () {
      var raw = String(new FormData(event.target).get("receipt") || "").trim();
      if (!raw) { throw fail("Paste a digest-only promotion receipt first."); }
      if (byteLength(raw) > maxReceiptBytes) { throw fail("Receipt exceeds the console byte bound."); }
      var parsed;
      try { parsed = JSON.parse(raw); }
      catch (_error) { throw fail("The receipt is not valid JSON."); }
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw fail("A promotion receipt must be a JSON object.");
      }
      renderReceiptVerdict(await readJson(await request("/verify-receipt", {
        method: "POST",
        body: JSON.stringify({ receipt: raw })
      })));
    })().catch(function (error) {
      document.getElementById("elv-verdict-box").hidden = true;
      output.textContent = error && error.message ? error.message : "Receipt verification failed.";
    });
  });

  loadSnapshot().catch(function (error) {
    setState("failed", "Unavailable", "The read-only Evolution Ledger projection could not be loaded.");
    output.textContent = error && error.message ? error.message : "Evolution Ledger request failed.";
  });
})();
