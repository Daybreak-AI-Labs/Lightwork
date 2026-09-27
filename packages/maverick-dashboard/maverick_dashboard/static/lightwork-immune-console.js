(function () {
  "use strict";

  var root = document.getElementById("ic-root");
  if (!root) { return; }
  var maxBytes = 2 * 1024 * 1024;
  var status = document.getElementById("ic-status");

  function text(id, value) {
    var el = document.getElementById(id);
    if (el) { el.textContent = value === null || value === undefined ? "—" : String(value); }
  }

  function rows(id, items, columns, empty) {
    var body = document.getElementById(id);
    if (!body) { return; }
    while (body.firstChild) { body.removeChild(body.firstChild); }
    if (!items || !items.length) {
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = columns.length;
      td.textContent = empty;
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }
    items.forEach(function (item) {
      var tr = document.createElement("tr");
      columns.forEach(function (column) {
        var td = document.createElement("td");
        var value = column(item);
        td.textContent = value === null || value === undefined ? "—" : String(value);
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
  }

  function yesNo(value, yes, no) {
    return value === null || value === undefined ? "not run" : (value ? yes : no);
  }

  function renderImmune(data) {
    var comp = data.composition || {};
    var ifc = data.ifc || {};
    var over = data.oversight || {};
    var twin = data.twin || {};
    text("ic-trifectas", comp.available ? (comp.by_kind || {}).trifecta || 0 : "n/a");
    text("ic-exfil", comp.available ? (comp.by_kind || {}).exfiltration || 0 : "n/a");
    text("ic-ifc-events", ifc.available ? ifc.events : "n/a");
    text("ic-replays", twin.available ? (twin.replays || []).length : "n/a");
    rows("ic-comp-rows", comp.trifectas, [
      function (f) { return (f.path || []).join(" → "); },
      function (f) { return ((f.witness || {}).sensitive || []).join(": "); },
      function (f) { return ((f.witness || {}).mutate || []).join(": "); },
      function (f) { return ((f.witness || {}).external || []).join(": "); },
      function (f) { return f.sink_risk; }
    ], comp.available ? "No trifecta paths." : "Composition analysis unavailable.");
    text("ic-ifc-mode", "Mode: " + (ifc.mode || "unknown"));
    rows("ic-ifc-rows", ifc.recent, [
      function (r) { return r.goal_id; },
      function (r) { return r.tool; },
      function (r) { return (r.labels || []).join(", "); },
      function (r) { return (r.explicit || []).length ? "yes" : "no"; },
      function (r) { return r.outcome; }
    ], "No labelled data has reached an external sink.");
    text("ic-over-state", "Canaries: " + (over.canaries_enabled ? "on" : "off"));
    rows("ic-over-rows", over.canary, [
      function (r) { return r.person; },
      function (r) { return r["class"]; },
      function (r) { return r.caught; },
      function (r) { return r.missed; },
      function (r) { return r.assured; },
      function (r) { return r.authority ? "earned" : "not yet"; }
    ], over.available ? "No decided canaries yet." : "Oversight data unavailable.");
    rows("ic-twin-rows", twin.replays, [
      function (r) { return r.production_goal; },
      function (r) { return r.twin_goal; },
      function (r) { return r.watched ? "evaluation cue" : "none"; },
      function (r) { return r.twin_status; },
      function (r) { return r.served + "/" + r.recorded; },
      function (r) { return r.approximate; },
      function (r) { return r.unmatched; },
      function (r) { return r.result_similarity; }
    ], twin.recording ? "No replays yet." : "Twin recording is off ([immune] twin_record).");
  }

  function renderCode(data) {
    var props = data.proposals || [];
    function count(state) { return props.filter(function (p) { return p.status === state; }).length; }
    text("ic-repos", (data.repos || []).length);
    text("ic-pending", count("pending"));
    text("ic-approved", count("approved"));
    text("ic-merged", count("merged"));
    rows("ic-repo-rows", data.repos, [
      function (r) { return r.name; },
      function (r) { return r.default_branch; },
      function (r) { return r.head; },
      function (r) { return r.protect; },
      function (r) { return r.tests ? "configured" : "none"; }
    ], data.enabled ? "No repositories yet: maverick code add NAME SOURCE." :
      "The code vault is off ([codebase] enable).");
    rows("ic-prop-rows", props, [
      function (p) { return p.id; },
      function (p) { return p.repo; },
      function (p) { return p.title; },
      function (p) { return p.files; },
      function (p) { return yesNo(p.tests, "passed", "FAILED"); },
      function (p) { return yesNo(p.review, "approves", "objects"); },
      function (p) { return p.status; }
    ], "No proposals yet.");
  }

  async function load() {
    try {
      var response = await fetch(root.dataset.apiUrl, {
        credentials: "same-origin", headers: { "Accept": "application/json" }
      });
      var declared = Number(response.headers.get("content-length") || 0);
      if (declared > maxBytes) { throw new Error("The summary is larger than expected."); }
      var body = await response.text();
      if (body.length > maxBytes) { throw new Error("The summary is larger than expected."); }
      var data = JSON.parse(body || "{}");
      if (!response.ok) {
        throw new Error(typeof data.detail === "string" ? data.detail.slice(0, 300) : "Request failed.");
      }
      if (root.dataset.console === "code") { renderCode(data); } else { renderImmune(data); }
      status.textContent = "Loaded.";
    } catch (error) {
      status.textContent = "Could not load the summary: " + (error && error.message ? error.message : "unknown error");
    }
  }

  load();
})();
