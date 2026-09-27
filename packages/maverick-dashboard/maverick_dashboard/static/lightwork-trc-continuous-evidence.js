// Continuous evidence page: sources, collection setups, recent collections
// and evidence changes load as lists on arrival. Setting up a source,
// approving it, pausing, resuming and retrying are row buttons; versions,
// fingerprints and request keys come from the chosen row or the server.
(function () {
  "use strict";
  var root = document.getElementById("trc-root");
  if (!root || !window.LwRecords) { return; }
  var R = window.LwRecords;
  var API = root.getAttribute("data-api") || "/api/v1/compliance/continuous-evidence";
  var canAdmin = root.getAttribute("data-can-admin") === "true";
  var canAudit = root.getAttribute("data-can-audit") === "true";
  var live = document.getElementById("trc-live-status");
  var runCycle = document.getElementById("trc-run-cycle");
  var S = { status: null, setup: [], definitions: [], enrollments: [], runs: [], jobs: [], alerts: [] };
  var STATUS = { observed: ["Passing", "ok"], failed: ["Failing", "danger"], unknown: ["Couldn't tell", "warn"] };

  function say(text) { live.textContent = text; }
  function fail(action) { return function (f) { var t = R.errorText(f, action); say(t); if (window.mvToast) { window.mvToast(t, { error: true }); } }; }
  function api(method, path, body) { return R.call(method, API + path, body); }
  function setCycleReady(ready) {
    runCycle.disabled = !ready || !canAdmin;
    if (runCycle.disabled) { runCycle.setAttribute("aria-disabled", "true"); } else { runCycle.removeAttribute("aria-disabled"); }
  }
  function control(ref) { return R.human(ref); }
  function setupFor(item) { return S.setup.filter(function (s) { return s.definition_id === item.definition_id; })[0]; }
  function labelFor(item) { var s = setupFor(item); return s ? s.label : "A collection"; }

  function loadAll() {
    setCycleReady(false);
    say("Loading…");
    return Promise.all([
      api("GET", "/status").then(function (d) { S.status = d; }),
      api("GET", "/setup").then(function (d) { S.setup = d.items || []; }).catch(function () { S.setup = []; }),
      api("GET", "/definitions").then(function (d) { S.definitions = d.items || []; }),
      api("GET", "/enrollments").then(function (d) { S.enrollments = d.items || []; }),
      api("GET", "/runs?limit=50").then(function (d) { S.runs = d.items || []; }),
      api("GET", "/jobs?state=dead_letter").then(function (d) { S.jobs = d.items || []; }),
      api("GET", "/alerts?limit=100").then(function (d) { S.alerts = d.items || []; }),
    ]).then(function () {
      renderStatus(); renderSources(); renderDefinitions(); renderRuns(); renderAlerts();
      say("Up to date.");
    }).catch(fail("load continuous evidence"));
  }

  function renderStatus() {
    var result = S.status || {};
    var stock = result.stock_collectors || {};
    var ready = stock.ready === true || stock.composition_ready === true;
    var missing = (stock.missing_stock_collector_ids || []).length;
    document.getElementById("trc-composition").textContent = ready ? "Complete" : missing ? missing + " step" + (missing === 1 ? "" : "s") + " left" : "Not finished";
    var running = Boolean(result.scheduler && result.scheduler.running === true);
    document.getElementById("trc-scheduler").textContent = running ? "On" : "Off";
    var active = S.enrollments.filter(function (e) { return e.state === "active"; }).length;
    document.getElementById("trc-feeds").textContent = active + " of " + Math.max(S.setup.length, active);
    var last = S.runs.slice().sort(function (a, b) { return b.completed_at - a.completed_at; })[0];
    var lastBatch = last ? S.runs.filter(function (r) { return Math.abs(r.completed_at - last.completed_at) < 3600; }) : [];
    document.getElementById("trc-cycle").textContent = last ? R.when(last.completed_at) : "Not yet";
    document.getElementById("trc-cycle-detail").textContent = last
      ? "Last collection: " + lastBatch.filter(function (r) { return r.complete; }).length + " finished, " + lastBatch.filter(function (r) { return !r.complete; }).length + " incomplete"
      : "Last collection";
    // Manual collection unlocks only on positive proof the scheduler runs.
    setCycleReady(Boolean(result.scheduler && result.scheduler.running === true));
  }

  function enrollmentFor(item) { return S.enrollments.filter(function (e) { return e.enrollment_id === item.enrollment_id; })[0]; }
  function definitionFor(item) { return S.definitions.filter(function (d) { return d.definition_id === item.definition_id && d.version === item.definition_version; })[0]; }
  function sourceState(item) {
    var def = definitionFor(item);
    var enr = enrollmentFor(item);
    if (enr) { return enr.state === "active" ? ["Collecting", "ok"] : enr.state === "paused" ? ["Paused", "warn"] : ["Removed", "idle"]; }
    if (!def) { return ["Not set up", "idle"]; }
    if (def.state === "pending") { return ["Waiting for approval", "warn"]; }
    if (def.state === "approved") { return ["Ready to start", "info"]; }
    return ["Retired", "idle"];
  }
  function renderSources() {
    R.table(document.getElementById("trc-sources"), [
      { label: "Source", value: function (s) { return s.label; } },
      { label: "Controls", value: function (s) { return s.control_count; } },
      { label: "Connection key", value: function (s) { return s.credential_ready === false ? R.badge("Missing", "danger") : s.credential_ready ? R.badge("Found", "ok") : "—"; } },
      { label: "Status", value: function (s) { var st = sourceState(s); return R.badge(st[0], st[1]); } },
    ], S.setup, [
      { label: "Set up", kind: "primary", when: function (s) { return canAdmin && !definitionFor(s); }, run: setUp },
      { label: "Start collecting", kind: "primary", when: function (s) { var d = definitionFor(s); return canAdmin && d && d.state === "approved" && !enrollmentFor(s); }, run: startCollecting },
      { label: "Pause", when: function (s) { var e = enrollmentFor(s); return canAdmin && e && e.state === "active"; }, run: function (s) { changeState(enrollmentFor(s), "paused", s.label); } },
      { label: "Resume", kind: "primary", when: function (s) { var e = enrollmentFor(s); return canAdmin && e && e.state === "paused"; }, run: function (s) { changeState(enrollmentFor(s), "active", s.label); } },
      { label: "Remove", kind: "danger", when: function (s) { var e = enrollmentFor(s); return canAdmin && e && e.state !== "revoked"; }, run: function (s) { changeState(enrollmentFor(s), "revoked", s.label); } },
    ], { text: "No sources are configured for this workspace yet. Your IT team adds each provider's connection; they then appear here to set up." });
  }
  function setUp(s) {
    R.confirm("Set up evidence collection from " + s.label + "? A different administrator then approves it before any collection starts.", "Set up").then(function (ok) {
      if (!ok) { return; }
      return api("POST", "/setup/" + encodeURIComponent(s.key) + "/definition").then(function () { say(s.label + " is set up and waiting for approval."); loadAll(); });
    }).catch(fail("set up the source"));
  }
  function startCollecting(s) {
    R.form("Start collecting from " + s.label, [
      { key: "every", label: "Collect", type: "select", default: "86400", options: [{ value: "3600", label: "Every hour" }, { value: "21600", label: "Every 6 hours" }, { value: "86400", label: "Every day" }, { value: "604800", label: "Every week" }] },
      { key: "keep", label: "Keep evidence for", type: "select", default: "31536000", options: [{ value: "7776000", label: "90 days" }, { value: "31536000", label: "1 year" }, { value: "94608000", label: "3 years" }, { value: "220752000", label: "7 years" }] },
    ], "Start").then(function (v) {
      if (!v) { return; }
      return api("POST", "/setup/" + encodeURIComponent(s.key) + "/enrollment", { interval_seconds: Number(v.every), retention_seconds: Number(v.keep) })
        .then(function () { say("Collection from " + s.label + " has started."); loadAll(); });
    }).catch(fail("start collecting"));
  }
  function changeState(e, state, name) {
    var words = { paused: ["Pause collecting from " + name + "?", "Pause"], active: ["Resume collecting from " + name + "?", "Resume"],
      revoked: ["Stop and remove collection from " + name + "? It can't be resumed; you would set it up again.", "Remove"] }[state];
    R.confirm(words[0], words[1]).then(function (ok) {
      if (!ok) { return; }
      return api("POST", "/enrollments/" + encodeURIComponent(e.enrollment_id) + "/state", { state: state, expected_revision: e.revision, expected_integrity_sha256: e.integrity_sha256 })
        .then(function () { say("Updated."); loadAll(); });
    }).catch(fail("update the collection"));
  }

  function renderDefinitions() {
    R.table(document.getElementById("trc-definitions"), [
      { label: "Source", value: labelFor },
      { label: "Controls", value: function (d) { return (d.allowed_control_refs || []).length; } },
      { label: "Status", value: function (d) { return R.badge(d.state === "pending" ? "Waiting for approval" : R.human(d.state), d.state === "approved" ? "ok" : d.state === "pending" ? "warn" : "idle"); } },
      { label: "Created", value: function (d) { return R.when(d.created_at); } },
    ], S.definitions, [
      { label: "Approve", kind: "primary", when: function (d) { return canAdmin && d.state === "pending"; }, run: approve },
      { label: "Retire", kind: "danger", when: function (d) { return canAdmin && d.state === "approved"; }, run: retire },
    ], { text: "No collection setups yet. Use Set up on a source above." });
  }
  function approve(d) {
    R.confirm("Approve collecting evidence from " + labelFor(d) + " for " + (d.allowed_control_refs || []).length + " controls? You can't approve a setup you created.", "Approve").then(function (ok) {
      if (!ok) { return; }
      return api("POST", "/definitions/" + encodeURIComponent(d.definition_id) + "/versions/" + d.version + "/approve", { expected_integrity_sha256: d.integrity_sha256 })
        .then(function () { say("Approved."); loadAll(); });
    }).catch(fail("approve the setup"));
  }
  function retire(d) {
    R.confirm("Retire this setup for " + labelFor(d) + "? Collections using it stop.", "Retire").then(function (ok) {
      if (!ok) { return; }
      return api("POST", "/definitions/" + encodeURIComponent(d.definition_id) + "/versions/" + d.version + "/retire", { expected_integrity_sha256: d.integrity_sha256 })
        .then(function () { say("Retired."); loadAll(); });
    }).catch(fail("retire the setup"));
  }

  function enrollmentLabel(id) { var s = S.setup.filter(function (x) { return x.enrollment_id === id; })[0]; return s ? s.label : "A source"; }
  function renderRuns() {
    R.table(document.getElementById("trc-runs"), [
      { label: "Source", value: function (r) { return enrollmentLabel(r.enrollment_id); } },
      { label: "Finished", value: function (r) { return R.when(r.completed_at); } },
      { label: "Result", value: function (r) { return r.complete ? R.badge("Complete", "ok") : R.badge("Incomplete", "warn"); } },
      { label: "Items checked", value: function (r) { return r.item_count; } },
    ], S.runs.slice().sort(function (a, b) { return b.completed_at - a.completed_at; }).slice(0, 50), [], { text: "No collections yet. They start once a source is set up, approved and started." });
    var retry = document.getElementById("trc-retry");
    retry.hidden = !(canAdmin && S.jobs.length);
    retry.textContent = "Retry failed collections (" + S.jobs.length + ")";
  }
  document.getElementById("trc-retry").addEventListener("click", function () {
    R.confirm("Retry the " + S.jobs.length + " collection(s) that failed?", "Retry").then(function (ok) {
      if (!ok) { return; }
      return Promise.all(S.jobs.map(function (j) {
        return api("POST", "/jobs/" + encodeURIComponent(j.job_id) + "/requeue", { expected_revision: j.revision, operation_id: "retry-" + R.newKey().toLowerCase().replace(/[^a-z0-9-]/g, "") });
      })).then(function () { say("Failed collections will be tried again."); loadAll(); });
    }).catch(fail("retry the failed collections"));
  });

  function renderAlerts() {
    R.table(document.getElementById("trc-alerts"), [
      { label: "Source", value: function (a) { return enrollmentLabel(a.enrollment_id); } },
      { label: "Control", value: function (a) { return control(a.control_ref); } },
      { label: "Was", value: function (a) { var s = STATUS[a.previous_status] || [R.human(a.previous_status), "idle"]; return R.badge(s[0], s[1]); } },
      { label: "Now", value: function (a) { var s = STATUS[a.current_status] || [R.human(a.current_status), "idle"]; return R.badge(s[0], s[1]); } },
      { label: "When", value: function (a) { return R.when(a.created_at); } },
    ], S.alerts.slice().sort(function (a, b) { return b.created_at - a.created_at; }), [], { text: "No evidence has changed." });
  }

  document.getElementById("trc-refresh").addEventListener("click", loadAll);
  runCycle.addEventListener("click", function () {
    runCycle.disabled = true;
    say("Collecting evidence…");
    api("POST", "/scheduler/run-once").then(function (d) {
      say(d.executed ? "Collection finished. Check the list below for any incomplete sources." : "Nothing was due to be collected.");
      return loadAll();
    }).catch(function (f) {
      say(f && f.status === 409 ? "A collection is already running. Try again in a minute." : R.errorText(f, "collect evidence"));
      setCycleReady(Boolean(S.status && S.status.scheduler && S.status.scheduler.running === true));
    });
  });

  var adminResult = document.getElementById("trc-admin-result");
  document.getElementById("trc-purge-before").value = R.isoDateIn(0);
  document.getElementById("trc-integrity").addEventListener("click", function () {
    if (!canAudit) { adminResult.textContent = "Only auditors and administrators can run this check."; return; }
    api("GET", "/integrity").then(function () { adminResult.textContent = "No changes found in the evidence records."; },
      function (f) { adminResult.textContent = R.errorText(f, "check the records"); });
  });
  document.getElementById("trc-purge").addEventListener("click", function () {
    if (!canAdmin) { adminResult.textContent = "Only administrators can delete expired records."; return; }
    var cutoff = R.toEpoch(document.getElementById("trc-purge-before").value);
    if (!cutoff) { adminResult.textContent = "Choose a date first."; return; }
    R.confirm("Delete evidence records whose keep period ended before this date? This can't be undone.", "Delete").then(function (ok) {
      if (!ok) { return; }
      api("POST", "/retention/purge", { cutoff: cutoff, limit: 500 }).then(function () { adminResult.textContent = "Expired records were deleted."; loadAll(); },
        function (f) { adminResult.textContent = R.errorText(f, "delete expired records"); });
    });
  });

  loadAll();
})();
