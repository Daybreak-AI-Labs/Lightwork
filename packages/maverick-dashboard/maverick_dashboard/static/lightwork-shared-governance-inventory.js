(function () {
  "use strict";

  var root = document.getElementById("sgi-root");
  if (!root) { return; }
  var apiBase = root.dataset.apiBase || "/api/v1/governance/inventory";
  var maxResponseBytes = 2 * 1024 * 1024;
  var digestPattern = /^[0-9a-f]{64}$/;
  var identifierPattern = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
  var state = document.getElementById("sgi-state");
  var output = document.getElementById("sgi-output");
  var operationState = document.getElementById("sgi-operation-state");
  var operations = document.getElementById("sgi-operations");
  var reconcileForm = document.getElementById("sgi-reconcile-form");

  function fail(message) {
    var error = new Error(message);
    error.code = "invalid_input";
    return error;
  }

  async function readJson(response) {
    var declared = Number(response.headers.get("content-length") || 0);
    if (declared > maxResponseBytes) {
      if (response.body && typeof response.body.cancel === "function") { await response.body.cancel(); }
      throw fail("Response exceeds the workbench byte bound.");
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
        throw fail("Response exceeds the workbench byte bound.");
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
      var detail = payload && typeof payload.detail === "string" ? payload.detail : "Governance inventory request failed.";
      throw fail(detail);
    }
    return payload;
  }

  async function request(path, options) {
    var settings = Object.assign({ credentials: "same-origin", headers: { Accept: "application/json" } }, options || {});
    if (settings.body) { settings.headers["Content-Type"] = "application/json"; }
    return readJson(await fetch(apiBase + path, settings));
  }

  function show(label, payload) {
    operationState.textContent = label;
    output.textContent = JSON.stringify(payload, null, 2);
  }

  function showError(error) {
    show("Operation failed closed.", { error: String(error && error.message ? error.message : error) });
  }

  function boundedInteger(value, label, minimum, maximum) {
    var selected = Number(value);
    if (!Number.isSafeInteger(selected) || selected < minimum || selected > maximum) {
      throw fail(label + " is outside its bound.");
    }
    return selected;
  }

  function identifier(value, label, optional) {
    var selected = String(value || "").trim();
    if (optional && !selected) { return null; }
    if (!identifierPattern.test(selected)) { throw fail(label + " is invalid."); }
    return selected;
  }

  function digest(value, label) {
    var selected = String(value || "").trim();
    if (!digestPattern.test(selected)) { throw fail(label + " must be a lowercase SHA-256 digest."); }
    return selected;
  }

  function setReady(ready) {
    operations.setAttribute("aria-disabled", ready ? "false" : "true");
    operations.classList.toggle("sgi-disabled", !ready);
    operations.querySelectorAll("button,input,select").forEach(function (control) {
      if (control.id === "sgi-refresh") { return; }
      var roleAllowed = control.hasAttribute("data-role-disabled") ? false : true;
      if (!ready) { control.disabled = true; }
      else if (roleAllowed && !control.closest("fieldset[disabled]")) { control.disabled = false; }
    });
  }

  function updateSourceCards(sources) {
    var list = document.getElementById("sgi-suites");
    if (!Array.isArray(sources) || !sources.length) { return; }
    list.replaceChildren();
    sources.slice(0, 6).forEach(function (source) {
      var item = document.createElement("li");
      var name = typeof source.source_id === "string" ? source.source_id.replace(/_/g, " ") : "unknown source";
      item.textContent = name + " · " + (source.complete === true ? "complete" : "bounded/incomplete") + " · r" + String(source.source_revision || 0);
      list.appendChild(item);
    });
  }

  async function loadStatus() {
    state.dataset.state = "partial";
    state.textContent = "Checking";
    setReady(false);
    try {
      var status = await request("/status");
      var ready = status.enabled === true && status.ready === true;
      state.dataset.state = ready ? (status.complete === true ? "ready" : "partial") : "failed";
      state.textContent = ready ? (status.complete === true ? "Complete" : "Partial") : "Unavailable";
      document.getElementById("sgi-health-title").textContent = ready ? "Inventory authority ready" : "Inventory authority unavailable";
      document.getElementById("sgi-health-detail").textContent = ready ? "Every displayed item is pinned to the active immutable snapshot." : "Review workspace availability, source access, and record integrity.";
      var active = status.active && typeof status.active === "object" ? status.active : {};
      document.getElementById("sgi-revision").textContent = String(active.revision || 0);
      updateSourceCards(status.sources);
      if (reconcileForm && active.integrity_sha256) {
        reconcileForm.elements.expected_active_revision.value = String(active.revision || 0);
        reconcileForm.elements.expected_active_integrity_sha256.value = String(active.integrity_sha256);
      }
      if (ready && active.snapshot_id) {
        var current = await request("/snapshots/current");
        var projection = current.projection && typeof current.projection === "object" ? current.projection : {};
        document.getElementById("sgi-entities").textContent = Array.isArray(projection.entities) ? String(projection.entities.length) : "0";
        document.getElementById("sgi-relationships").textContent = Array.isArray(projection.relationships) ? String(projection.relationships.length) : "0";
      } else {
        document.getElementById("sgi-entities").textContent = "0";
        document.getElementById("sgi-relationships").textContent = "0";
      }
      setReady(ready);
      show("Inventory status loaded.", status);
    } catch (error) {
      state.dataset.state = "failed";
      state.textContent = "Unavailable";
      document.getElementById("sgi-health-title").textContent = "Status failed closed";
      document.getElementById("sgi-health-detail").textContent = error.message;
      showError(error);
    }
  }

  async function loadSnapshots() {
    var payload = await request("/snapshots?limit=100");
    var rows = document.getElementById("sgi-snapshot-rows");
    rows.replaceChildren();
    (Array.isArray(payload.items) ? payload.items : []).forEach(function (item) {
      var row = document.createElement("tr");
      [item.snapshot_id, item.version, item.state, item.integrity_sha256].forEach(function (value) {
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
      cell.textContent = "No snapshots are available.";
      empty.appendChild(cell);
      rows.appendChild(empty);
    }
    show("Snapshot history loaded.", payload);
  }

  document.getElementById("sgi-refresh").addEventListener("click", function () { loadStatus(); });
  document.getElementById("sgi-load-snapshots").addEventListener("click", function () { loadSnapshots().catch(showError); });

  document.getElementById("sgi-query-form").addEventListener("submit", function (event) {
    event.preventDefault();
    try {
      var form = event.currentTarget;
      var collection = form.elements.collection.value === "relationships" ? "relationships" : "entities";
      var snapshotId = identifier(form.elements.snapshot_id.value, "Snapshot ID", true);
      var filter = identifier(form.elements.filter.value, "Filter", true);
      var secondaryText = String(form.elements.secondary.value || "").trim();
      var limit = boundedInteger(form.elements.limit.value, "Limit", 1, 1000);
      var params = new URLSearchParams({ limit: String(limit) });
      if (snapshotId) { params.set("snapshot_id", snapshotId); }
      if (collection === "entities") {
        if (filter) { params.set("source_id", filter); }
        var kind = identifier(secondaryText, "Entity kind", true);
        if (kind) { params.set("entity_kind", kind); }
      } else {
        if (filter) { params.set("relation_type", filter); }
        if (secondaryText) { params.set("entity_ref_sha256", digest(secondaryText, "Entity digest")); }
      }
      request("/" + collection + "?" + params.toString()).then(function (payload) {
        show("Bounded " + collection + " query completed.", payload);
      }).catch(showError);
    } catch (error) { showError(error); }
  });

  reconcileForm.addEventListener("submit", function (event) {
    event.preventDefault();
    try {
      var body = {
        expected_active_revision: boundedInteger(reconcileForm.elements.expected_active_revision.value, "Expected revision", 0, 10000),
        expected_active_integrity_sha256: digest(reconcileForm.elements.expected_active_integrity_sha256.value, "Expected integrity"),
        operation_id: identifier(reconcileForm.elements.operation_id.value, "Operation ID", false)
      };
      request("/reconcile", { method: "POST", body: JSON.stringify(body) }).then(function (payload) {
        show("Exact six-suite reconciliation completed.", payload);
        loadStatus();
      }).catch(showError);
    } catch (error) { showError(error); }
  });

  document.getElementById("sgi-integrity").addEventListener("click", function () {
    request("/integrity").then(function (payload) { show("Integrity verification completed.", payload); }).catch(showError);
  });
  document.getElementById("sgi-export").addEventListener("click", function () {
    request("/export").then(function (payload) { show("Bounded active export created.", payload); }).catch(showError);
  });
  document.getElementById("sgi-checkpoint").addEventListener("click", function () {
    request("/recovery/checkpoint", { method: "POST" }).then(function (payload) { show("Recovery checkpoint created.", payload); }).catch(showError);
  });
  document.getElementById("sgi-restore").addEventListener("click", function () {
    request("/recovery/restore", { method: "POST" }).then(function (payload) { show("Latest authenticated checkpoint restored.", payload); loadStatus(); }).catch(showError);
  });

  loadStatus();
}());
