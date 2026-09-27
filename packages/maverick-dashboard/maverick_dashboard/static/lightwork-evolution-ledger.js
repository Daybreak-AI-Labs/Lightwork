(function () {
  "use strict";

  var root = document.getElementById("elv-root");
  if (!root) { return; }

  var apiBase = root.dataset.apiBase || "/api/v1/audit/evolution-ledger";
  var maxResponseBytes = 1024 * 1024;
  var maxReceiptBytes = 64 * 1024;
  var maxEntries = 500;
  var healthDetail = document.getElementById("elv-health-detail");
  var errorEl = document.getElementById("elv-error");
  var entriesBody = document.getElementById("elv-entries");
  var limitations = document.getElementById("elv-limitations");
  var LOAD_FAILED = "We could not load the learning history. Select Refresh; if it keeps failing, contact your administrator.";

  var KIND = { promotion: "Adopted a learned improvement", rollback: "Undid a learned improvement" };
  var INTEGRITY = {
    hash_chained_local_promotion_ledger: "Yes, sealed in a tamper-evident chain",
    legacy_projection_promotion_ledger: "Yes, from an older record format",
    in_memory_promotion_ledger: "Not sealed (temporary record)",
    runtime_supplied: "Not sealed"
  };
  var APPROVAL = {
    verified: "Yes, confirmed",
    not_recorded: "No approval recorded",
    not_applicable: "Not needed",
    not_verifiable_without_private_binding: "Recorded, cannot be confirmed here",
    receipt_invalid: "Receipt not valid",
    private_binding_invalid: "Could not be confirmed",
    private_binding_mismatch: "Does not match",
    signature_untrusted_or_invalid: "Signature not trusted",
    approval_signer_key_id_mismatch: "Signed by an unexpected key"
  };
  var LIMITS = {
    promotion_ledger_not_present: "No learning has been adopted in this workspace yet.",
    dashboard_projects_only_digest_only_promotion_history: "This page shows fingerprints and outcomes only, never the learned content itself.",
    dreaming_hindsight_and_runtime_proof_sources_are_not_exposed_here: "Overnight learning runs and learning checks are shown on the Learning page, not here.",
    receipt_integrity_is_not_a_human_approval_verification: "An intact receipt shows the record was not changed. It does not by itself prove who approved it.",
    human_approval_signature_not_verifiable_from_receipt: "A person’s approval signature cannot be checked from the receipt alone.",
    approval_signature_requires_private_binding_verification: "Confirming the approval needs private records that stay on the server.",
    workspace_snapshot_digest_not_recorded: "Some older records do not include a fingerprint of the workspace.",
    runtime_artifact_binding_not_recorded: "Some older records do not name the exact files that changed."
  };

  function human(v) { return window.mvHuman ? window.mvHuman(v) : String(v || ""); }
  function plain(map, v) { return typeof v === "string" ? (map[v] || human(v)) : "—"; }
  function fail(message) { var error = new Error(message); error.plain = true; return error; }
  function showError(error, action) {
    errorEl.textContent = error && error.plain ? error.message : (error && (error.status || error.name) ? window.mvErrorText(error, action) : LOAD_FAILED);
  }
  function byteLength(text) { return new TextEncoder().encode(String(text || "")).length; }

  async function readBody(response) {
    var declared = Number(response.headers.get("content-length") || 0);
    if (declared > maxResponseBytes) {
      if (response.body && typeof response.body.cancel === "function") { await response.body.cancel(); }
      throw fail(LOAD_FAILED);
    }
    if (!response.body || typeof response.body.getReader !== "function") {
      var fallback = await response.text();
      if (byteLength(fallback) > maxResponseBytes) { throw fail(LOAD_FAILED); }
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
        if (total > maxResponseBytes) { await reader.cancel(); throw fail(LOAD_FAILED); }
        chunks.push(next.value);
      }
    } finally {
      reader.releaseLock();
    }
    var result = new Uint8Array(total);
    var position = 0;
    chunks.forEach(function (chunk) { result.set(chunk, position); position += chunk.byteLength; });
    try { return new TextDecoder("utf-8", { fatal: true }).decode(result); }
    catch (_error) { throw fail(LOAD_FAILED); }
  }

  async function readJson(response, action) {
    if (!response.ok) { throw fail(window.mvErrorText(response, action)); }
    var text = await readBody(response);
    try { return text ? JSON.parse(text) : {}; }
    catch (_error) { throw fail(LOAD_FAILED); }
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
    } finally {
      clearTimeout(timeout);
    }
  }

  function cell(text) { var td = document.createElement("td"); td.textContent = text; return td; }

  function auditCell(entry) {
    var td = document.createElement("td");
    var details = document.createElement("details");
    var summary = document.createElement("summary");
    summary.textContent = "Fingerprints";
    details.appendChild(summary);
    [["Record", entry.entry_sha256], ["Receipt", entry.promotion_receipt_sha256]].forEach(function (pair) {
      if (typeof pair[1] !== "string" || !/^[0-9a-f]{64}$/.test(pair[1])) { return; }
      var p = document.createElement("p");
      p.className = "elv-key";
      p.textContent = pair[0] + ": " + pair[1];
      details.appendChild(p);
    });
    td.appendChild(details);
    return td;
  }

  function when(value) {
    var timestamp = Number(value);
    if (!Number.isFinite(timestamp) || timestamp <= 0) { return "—"; }
    return window.mvWhen ? window.mvWhen(timestamp) : new Date(timestamp * 1000).toLocaleString();
  }

  function renderEntries(entries) {
    entriesBody.textContent = "";
    if (!entries.length) {
      var empty = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 5;
      td.appendChild(document.createTextNode("No learned improvements have been adopted in this workspace yet. "));
      var link = document.createElement("a");
      link.className = "btn btn--ghost";
      link.href = "/learning";
      link.textContent = "See what Lightwork is learning";
      td.appendChild(link);
      empty.appendChild(td);
      entriesBody.appendChild(empty);
      return;
    }
    entries.slice().reverse().forEach(function (entry) {
      var row = document.createElement("tr");
      row.appendChild(cell(when(entry.occurred_at)));
      row.appendChild(cell(plain(KIND, entry.kind)));
      row.appendChild(cell(plain(INTEGRITY, entry.integrity)));
      row.appendChild(cell(plain(APPROVAL, entry.approval_status)));
      row.appendChild(auditCell(entry));
      entriesBody.appendChild(row);
    });
  }

  function renderLimitations(items) {
    limitations.textContent = "";
    var list = items.slice(0, 32).map(function (item) { return LIMITS[item] || human(item) + "."; });
    if (!list.length) { list = ["Nothing further to note."]; }
    list.forEach(function (text) {
      var li = document.createElement("li");
      li.textContent = text;
      limitations.appendChild(li);
    });
  }

  function renderSnapshot(payload) {
    if (!payload || payload.schema !== "lightwork.evolution-ledger-dashboard-snapshot.v1") { throw fail(LOAD_FAILED); }
    var projection = payload.projection;
    var sources = payload.sources;
    if (!projection || typeof projection !== "object" || !Array.isArray(projection.entries) || !sources || typeof sources !== "object") {
      throw fail(LOAD_FAILED);
    }
    if (projection.entries.length > maxEntries) { throw fail(LOAD_FAILED); }
    var entries = projection.entries;
    document.getElementById("elv-entry-count").textContent = String(projection.entry_count || 0);
    document.getElementById("elv-promotions").textContent = String(entries.filter(function (e) { return e && e.kind === "promotion"; }).length);
    document.getElementById("elv-rollbacks").textContent = String(entries.filter(function (e) { return e && e.kind === "rollback"; }).length);
    var total = Number(projection.total_entries || 0);
    document.getElementById("elv-table-note").textContent = projection.truncated
      ? "Showing the newest " + (projection.entry_count || 0) + " of " + total + " records."
      : (total === 1 ? "1 record." : total + " records.");
    renderEntries(entries);
    renderLimitations(Array.isArray(projection.limitations) ? projection.limitations : []);
    healthDetail.textContent = sources.promotion_ledger === "available" ? "Up to date." : "No learning has been adopted in this workspace yet.";
    errorEl.textContent = "";
  }

  async function loadSnapshot() {
    healthDetail.textContent = "Loading.";
    try {
      renderSnapshot(await readJson(await request("/snapshot"), "load the learning history"));
    } catch (error) {
      healthDetail.textContent = "";
      showError(error, "load the learning history");
    }
  }

  function renderReceiptVerdict(result) {
    var box = document.getElementById("elv-verdict-box");
    var verdict = document.getElementById("elv-verdict");
    var detail = document.getElementById("elv-verdict-detail");
    var valid = result && result.receipt_valid === true;
    box.hidden = false;
    verdict.dataset.valid = valid ? "true" : "false";
    verdict.textContent = valid ? "This receipt is intact: it has not been changed since it was made." :
      "This receipt could not be confirmed. It may have been changed, or it is not a Lightwork receipt.";
    detail.textContent = "Approval by a person: " + plain(APPROVAL, result && result.approval_status) + ".";
  }

  document.getElementById("elv-refresh").addEventListener("click", loadSnapshot);

  document.getElementById("elv-verify-form").addEventListener("submit", function (event) {
    event.preventDefault();
    (async function () {
      var input = document.getElementById("elv-receipt");
      var file = input.files && input.files[0];
      if (!file) { throw fail("Choose a receipt file first."); }
      if (file.size > maxReceiptBytes) { throw fail("That file is too large to be a receipt."); }
      var raw = (await file.text()).trim();
      if (byteLength(raw) > maxReceiptBytes) { throw fail("That file is too large to be a receipt."); }
      var parsed;
      try { parsed = JSON.parse(raw); } catch (_error) { parsed = null; }
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw fail("That file is not a Lightwork receipt.");
      }
      var response = await request("/verify-receipt", { method: "POST", body: JSON.stringify({ receipt: raw }) });
      if (response.status === 422) { throw fail("That file is not a Lightwork receipt."); }
      renderReceiptVerdict(await readJson(response, "check the receipt"));
      errorEl.textContent = "";
    })().catch(function (error) {
      document.getElementById("elv-verdict-box").hidden = true;
      showError(error, "check the receipt");
    });
  });

  loadSnapshot();
})();
