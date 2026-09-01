"use strict";

(function () {
  var MAX_JSON_BYTES = 256 * 1024;
  var MAX_PDF_BYTES = 4 * 1024 * 1024;
  var ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
  var OP_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{7,191}$/;
  var DIGEST_PATTERN = /^[0-9a-f]{64}$/;
  var SOURCE_KINDS = new Set([
    "intelligence_vendor", "intelligence_finding", "lifecycle_assessment",
    "lifecycle_record", "contract_record", "collaboration_invitation"
  ]);
  var root = document.getElementById("tper-workbench");
  if (!root) { return; }
  var apiBase = root.dataset.apiBase;
  var statusNode = document.getElementById("tper-operation-status");
  var outputNode = document.getElementById("tper-output");

  function failure(message) { var error = new Error(message); return error; }
  function identifier(value, label, optional) {
    var selected = String(value || "").trim();
    if (optional && !selected) { return null; }
    if (!ID_PATTERN.test(selected)) { throw failure(label + " must be a valid opaque reference."); }
    return selected;
  }
  function operation(value) {
    var selected = String(value || "").trim();
    if (!OP_PATTERN.test(selected)) { throw failure("Idempotency key must contain 8-192 safe characters."); }
    return selected;
  }
  function integer(value, label, maximum) {
    var selected = Number(value);
    if (!Number.isInteger(selected) || selected < 1 || selected > maximum) { throw failure(label + " is outside its bound."); }
    return selected;
  }
  function plain(value, label, maximum) {
    var selected = String(value || "").trim();
    if (!selected || selected.length > maximum || /[<>{}\x00-\x1f]/.test(selected) || selected.includes("://")) { throw failure(label + " must be bounded plain text."); }
    return selected;
  }
  function selectors(value) {
    var parsed;
    if (new TextEncoder().encode(value).byteLength > 32 * 1024) { throw failure("Source selectors exceed 32 KiB."); }
    try { parsed = JSON.parse(value); } catch (_error) { throw failure("Source selectors must be valid JSON."); }
    if (!Array.isArray(parsed) || parsed.length > 500) { throw failure("Source selectors must be an array of at most 500 items."); }
    var seen = new Set();
    return parsed.map(function (item) {
      if (!item || Array.isArray(item) || typeof item !== "object" || Object.keys(item).sort().join(",") !== "source_id,source_kind") { throw failure("Each source selector must contain exactly source_kind and source_id."); }
      if (!SOURCE_KINDS.has(item.source_kind)) { throw failure("Source kind is unsupported."); }
      var sourceId = identifier(item.source_id, "Source reference", false);
      var key = item.source_kind + "\u0000" + sourceId;
      if (seen.has(key)) { throw failure("Source selectors must be unique."); }
      seen.add(key);
      return { source_kind: item.source_kind, source_id: sourceId };
    });
  }
  async function readBounded(response, maximum) {
    var declared = response.headers.get("Content-Length");
    if (declared !== null && Number(declared) > maximum) {
      if (response.body && typeof response.body.cancel === "function") { await response.body.cancel(); }
      throw failure("Server response exceeded its safety limit.");
    }
    if (!response.body || typeof response.body.getReader !== "function") { throw failure("Server response stream is unavailable."); }
    var reader = response.body.getReader(); var chunks = []; var total = 0;
    while (true) {
      var part = await reader.read(); if (part.done) { break; }
      total += part.value.byteLength;
      if (total > maximum) { await reader.cancel(); throw failure("Server response exceeded its safety limit."); }
      chunks.push(part.value);
    }
    var bytes = new Uint8Array(total); var offset = 0;
    chunks.forEach(function (chunk) { bytes.set(chunk, offset); offset += chunk.byteLength; });
    return bytes;
  }
  async function requestJson(path, options) {
    var response = await fetch(apiBase + path, Object.assign({ credentials: "same-origin", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer", headers: { "Accept": "application/json", "Content-Type": "application/json" } }, options || {}));
    var bytes = await readBounded(response, MAX_JSON_BYTES); var payload;
    try { payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); } catch (_error) { throw failure("Server returned invalid JSON."); }
    if (!response.ok) { throw failure(payload && typeof payload.detail === "string" ? payload.detail.slice(0, 300) : "Report request failed."); }
    return payload;
  }
  function show(label, payload) {
    statusNode.textContent = label;
    outputNode.textContent = payload === undefined ? "" : JSON.stringify(payload, null, 2);
  }
  function busy(label) { statusNode.textContent = label; outputNode.textContent = ""; }
  function formObject(form) { return Object.fromEntries(new FormData(form).entries()); }
  function textElement(tag, value) { var node = document.createElement(tag); node.textContent = String(value); return node; }
  function renderReports(payload) {
    var container = document.getElementById("tper-reports"); var count = document.getElementById("tper-count");
    container.replaceChildren();
    var items = payload && Array.isArray(payload.items) ? payload.items : [];
    count.textContent = items.length + (items.length === 1 ? " report" : " reports");
    if (!items.length) { container.appendChild(textElement("p", "No role-accessible reports are available.")); return; }
    items.forEach(function (item) {
      var card = document.createElement("article"); card.className = "tper-report";
      var head = document.createElement("div"); head.className = "tper-report__head";
      head.appendChild(textElement("strong", item.brand && item.brand.report_title ? item.brand.report_title : "Executive report"));
      var pill = textElement("span", item.state || "unknown"); pill.className = "tper-pill"; head.appendChild(pill); card.appendChild(head);
      var list = document.createElement("dl");
      [["Reference", item.report_id], ["Role view", item.role_view], ["Control revision", item.control_revision], ["Sources", item.snapshot && item.snapshot.sources ? item.snapshot.sources.length : 0], ["PDF SHA-256", item.pdf_sha256], ["Artifact", item.artifact_available ? "available" : "unavailable"]].forEach(function (entry) { list.appendChild(textElement("dt", entry[0])); list.appendChild(textElement("dd", entry[1])); });
      card.appendChild(list); container.appendChild(card);
    });
  }
  async function refresh() {
    try {
      var state = await requestJson("/status");
      var mark = document.getElementById("tper-runtime-state"); var detail = document.getElementById("tper-runtime-detail");
      mark.dataset.state = state.ready ? "ready" : "failed"; mark.textContent = state.ready ? "Ready" : (state.enabled ? "Not ready" : "Disabled");
      detail.textContent = state.ready ? "Tenant report composition is ready. Live portfolio acceptance remains unverified." : "Enable and configure the tenant report composition before creating artifacts.";
      var reports = await requestJson("/reports?limit=100"); renderReports(reports); show("Reports refreshed.", reports);
    } catch (error) { document.getElementById("tper-runtime-state").dataset.state = "failed"; document.getElementById("tper-runtime-state").textContent = "Unavailable"; show(error.message, { error: true }); }
  }
  document.getElementById("tper-create-form").addEventListener("submit", async function (event) {
    event.preventDefault(); try { busy("Creating immutable report…"); var values = formObject(event.currentTarget); var metadataRef = identifier(values.brand_asset_metadata_ref, "Brand asset metadata reference", true); var metadataDigest = String(values.brand_asset_metadata_sha256 || "").trim().toLowerCase() || null; if ((metadataRef === null) !== (metadataDigest === null) || (metadataDigest !== null && !DIGEST_PATTERN.test(metadataDigest))) { throw failure("Brand asset metadata reference and exact SHA-256 must be provided together; asset bytes are not rendered."); }
      var payload = { report_id: identifier(values.report_id, "Report reference", true), expected_absent_revision: 0, selectors: selectors(values.selectors_json), brand: { organization_name: plain(values.organization_name, "Organization name", 120), report_title: plain(values.report_title, "Report title", 160), footer_text: plain(values.footer_text, "Footer text", 160), primary_color: String(values.primary_color).toUpperCase(), accent_color: String(values.accent_color).toUpperCase(), text_color: String(values.text_color).toUpperCase(), brand_asset_metadata_ref: metadataRef, brand_asset_metadata_sha256: metadataDigest }, idempotency_key: operation(values.idempotency_key) };
      var result = await requestJson("/reports", { method: "POST", body: JSON.stringify(payload) }); show("Report created.", result); await refresh();
    } catch (error) { show(error.message, { error: true }); }
  });
  document.getElementById("tper-download-form").addEventListener("submit", async function (event) {
    event.preventDefault(); try { busy("Issuing one-time grant…"); var values = formObject(event.currentTarget); var reportId = identifier(values.report_id, "Report reference", false); var grant = await requestJson("/reports/" + encodeURIComponent(reportId) + "/download-grants", { method: "POST", body: JSON.stringify({ expected_control_revision: integer(values.expected_control_revision, "Control revision", 2147483647), idempotency_key: operation(values.idempotency_key) }) }); if (!grant.token) { throw failure("This idempotent grant was already disclosed. Use a new idempotency key."); }
      var response = await fetch(apiBase + "/downloads/redeem", { method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer", headers: { "Accept": "application/pdf", "Content-Type": "application/json" }, body: JSON.stringify({ token: grant.token }) }); var bytes = await readBounded(response, MAX_PDF_BYTES); grant.token = null; if (!response.ok || response.headers.get("Content-Type") !== "application/pdf") { throw failure("PDF download failed."); } var url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" })); var link = document.createElement("a"); link.href = url; link.download = "third-party-report-" + reportId + ".pdf"; document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url); show("PDF downloaded and one-time token consumed.", { report_id: reportId, bytes: bytes.byteLength });
    } catch (error) { show(error.message, { error: true }); }
  });
  document.getElementById("tper-revoke-form").addEventListener("submit", async function (event) { event.preventDefault(); try { var values = formObject(event.currentTarget); var reportId = identifier(values.report_id, "Report reference", false); var result = await requestJson("/reports/" + encodeURIComponent(reportId) + "/revoke", { method: "POST", body: JSON.stringify({ expected_control_revision: integer(values.expected_control_revision, "Control revision", 2147483647), reason_code: identifier(values.reason_code, "Reason code", false), idempotency_key: operation(values.idempotency_key) }) }); show("Report revoked.", result); await refresh(); } catch (error) { show(error.message, { error: true }); } });
  document.getElementById("tper-purge-form").addEventListener("submit", async function (event) { event.preventDefault(); try { var values = formObject(event.currentTarget); var result = await requestJson("/maintenance/purge", { method: "POST", body: JSON.stringify({ limit: integer(values.limit, "Purge limit", 1000), idempotency_key: operation(values.idempotency_key) }) }); show("Retention purge completed.", result); await refresh(); } catch (error) { show(error.message, { error: true }); } });
  document.getElementById("tper-integrity").addEventListener("click", async function () { try { busy("Verifying report ledger…"); show("Integrity verification completed.", await requestJson("/integrity")); } catch (error) { show(error.message, { error: true }); } });
  document.getElementById("tper-audit").addEventListener("click", async function () { try { busy("Loading audit history…"); show("Audit history loaded.", await requestJson("/audit?limit=100")); } catch (error) { show(error.message, { error: true }); } });
  document.getElementById("tper-refresh").addEventListener("click", refresh);
  refresh();
}());
