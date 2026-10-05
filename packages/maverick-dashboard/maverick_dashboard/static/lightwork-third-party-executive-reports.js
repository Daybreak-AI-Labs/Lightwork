"use strict";

// Executive vendor reports: reports load as a list on arrival with a button
// per next step (see what's inside, download the PDF, withdraw). A new report
// is built by ticking real vendors, findings, assessments, contracts and
// questionnaires; report references, versions and request keys come from the
// chosen row or are made here, never typed.
(function (global) {
  var document = global.document;
  var MAX_JSON_BYTES = 256 * 1024;
  var MAX_PDF_BYTES = 4 * 1024 * 1024;
  var root = document.getElementById("tper-workbench");
  if (!root || !global.LwRecords) { return; }
  var R = global.LwRecords;
  var apiBase = root.dataset.apiBase;
  var canAdmin = root.dataset.canAdmin === "true";
  var statusNode = document.getElementById("tper-operation-status");
  var outputNode = document.getElementById("tper-output");
  var editor = document.getElementById("tper-editor");
  var createForm = document.getElementById("tper-create-form");
  var mutationControls = Array.prototype.slice.call(document.querySelectorAll("[data-tper-mutation]"));
  var destructiveInFlight = false;
  var ready = false;
  var S = { reports: [], vendorNames: {}, sources: {} };

  var STATES = { active: ["Ready", "ok"], revoked: ["Withdrawn", "warn"], expired: ["Expired", "idle"], purged: ["Removed", "idle"] };
  var ACTIONS = {
    report_created: "Report created", download_grant_issued: "Download started",
    download_grant_redeemed: "PDF downloaded", report_revoked: "Report withdrawn",
    report_content_expired: "Report file removed after it expired",
  };
  var UNAVAILABLE = {
    not_found: "it no longer exists", source_disabled: "that area is turned off",
    source_not_configured: "that area isn't set up yet", source_unavailable: "it couldn't be read",
    not_selected: "it wasn't chosen",
  };
  var GROUPS = [
    { kind: "intelligence_vendor", title: "Vendors" },
    { kind: "intelligence_finding", title: "Screening findings" },
    { kind: "lifecycle_assessment", title: "Assessments" },
    { kind: "lifecycle_record", title: "Vendor relationships" },
    { kind: "contract_record", title: "Contracts and engagements" },
    { kind: "collaboration_invitation", title: "Vendor questionnaires" },
  ];

  function failure(message) { var error = new Error(message); error.plain = message; return error; }
  function messageOf(error, action) { return error && error.plain ? error.plain : R.errorText(error, action); }
  function words(value) {
    var s = String(value || "").replace(/^(vendor-contact|vendor|doc|note|t|q)[.:]/, "").replace(/[-_.:]+/g, " ").trim();
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : "—";
  }
  function refOf(text) {
    var s = String(text || "");
    if (s.normalize) { s = s.normalize("NFKD"); }
    return s.replace(/[^A-Za-z0-9 _.-]+/g, "").trim().replace(/\s+/g, "-").replace(/^[^A-Za-z0-9]+/, "").slice(0, 120);
  }
  function vendorName(value) { return S.vendorNames[value] || words(value); }
  function titleOf(report) { return report && report.brand && report.brand.report_title ? report.brand.report_title : "Executive report"; }
  function plainText(value, label, maximum) {
    var selected = String(value || "").trim();
    if (!selected) { throw failure("Fill in the " + label.toLowerCase() + "."); }
    if (selected.length > maximum) { throw failure("The " + label.toLowerCase() + " is too long."); }
    if (/[<>{}\x00-\x1f]/.test(selected) || selected.includes("://")) { throw failure("The " + label.toLowerCase() + " can only contain plain words, without web addresses or symbols like < > { }."); }
    return selected;
  }

  // ---- bounded same-origin requests -------------------------------------------
  async function readBounded(response, maximum) {
    var declared = response.headers.get("Content-Length");
    if (declared !== null && Number(declared) > maximum) {
      if (response.body && typeof response.body.cancel === "function") { await response.body.cancel(); }
      throw failure("Lightwork sent more than this page can show. Please try again.");
    }
    if (!response.body || typeof response.body.getReader !== "function") {
      var buffer = new Uint8Array(await response.arrayBuffer());
      if (buffer.byteLength > maximum) { throw failure("Lightwork sent more than this page can show. Please try again."); }
      return buffer;
    }
    var reader = response.body.getReader(); var chunks = []; var total = 0;
    while (true) {
      var part = await reader.read(); if (part.done) { break; }
      total += part.value.byteLength;
      if (total > maximum) { await reader.cancel(); throw failure("Lightwork sent more than this page can show. Please try again."); }
      chunks.push(part.value);
    }
    var bytes = new Uint8Array(total); var offset = 0;
    chunks.forEach(function (chunk) { bytes.set(chunk, offset); offset += chunk.byteLength; });
    return bytes;
  }
  async function requestJson(path, options) {
    var response = await global.fetch(apiBase + path, Object.assign({ credentials: "same-origin", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer", headers: { "Accept": "application/json", "Content-Type": "application/json" } }, options || {}));
    if (!response.ok) { throw response; }
    var bytes = await readBounded(response, MAX_JSON_BYTES);
    if (!bytes.byteLength) { return {}; }
    try { return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); }
    catch (_error) { throw failure("Lightwork sent an answer this page could not read. Please try again."); }
  }
  function show(label, payload) {
    window.mvStatus(statusNode, label);
    outputNode.textContent = payload === undefined ? "" : JSON.stringify(payload, null, 2);
  }
  function busy(label) { window.mvStatus(statusNode, label); outputNode.textContent = ""; }

  // ---- confirmed, single-flight changes ----------------------------------------
  function confirmDestructive(message, okText) { return R.confirm(message, okText); }
  async function runDestructive(form, label, prompt, okText, functionValue) {
    if (destructiveInFlight) {
      show("Another report change is already running. Wait for it to finish.");
      return;
    }
    destructiveInFlight = true;
    root.dataset.destructiveInFlight = "true";
    var controls = [];
    var disabledBefore = [];
    try {
      if (!(await confirmDestructive(prompt, okText))) { return; }
      // The opener stays enabled while the confirmation is open so focus can
      // return to it; everything is disabled only after the person confirms.
      controls = Array.prototype.slice.call(form.querySelectorAll("button, input, select, textarea"));
      disabledBefore = controls.map(function (control) { return control.disabled; });
      controls.forEach(function (control) { control.disabled = true; });
      form.setAttribute("aria-busy", "true");
      busy(label);
      await functionValue();
    } catch (error) {
      show(messageOf(error, "finish that change"));
    } finally {
      controls.forEach(function (control, index) { control.disabled = disabledBefore[index]; });
      form.removeAttribute("aria-busy");
      destructiveInFlight = false;
      delete root.dataset.destructiveInFlight;
    }
  }
  function setMutationReady(isReady) {
    ready = isReady === true;
    mutationControls.forEach(function (controls) {
      var authorized = controls.dataset.authorized === "true" && root.dataset.moduleEnabled === "true";
      var disabled = !authorized || isReady !== true;
      controls.disabled = disabled;
      controls.toggleAttribute("disabled", disabled);
      controls.setAttribute("aria-disabled", disabled ? "true" : "false");
    });
  }

  // ---- report list -------------------------------------------------------------
  function stateBadge(report) {
    var s = STATES[report.state] || [words(report.state), "info"];
    return R.badge(s[0], s[1]);
  }
  function sourcesOf(report) { return report.snapshot && Array.isArray(report.snapshot.sources) ? report.snapshot.sources : []; }
  function renderReports() {
    var count = document.getElementById("tper-count");
    var items = S.reports;
    count.textContent = items.length ? items.length + (items.length === 1 ? " report" : " reports") : "";
    R.table(document.getElementById("tper-reports"), [
      { label: "Report", value: titleOf },
      { label: "Organization", value: function (r) { return r.brand ? r.brand.organization_name : ""; } },
      { label: "Created", value: function (r) { return R.when(r.created_at); } },
      { label: "Records used", value: function (r) { return String(sourcesOf(r).length); } },
      { label: "Status", value: stateBadge },
    ], items, [
      { label: "What's inside", run: showReport },
      { label: "Download PDF", kind: "primary", when: function (r) { return r.state === "active" && r.artifact_available === true; }, run: downloadReport },
      { label: "Withdraw", kind: "danger", when: function (r) { return canAdmin && r.state === "active"; }, run: revokeReport },
    ], { text: "No reports yet. Create one from the vendors, assessments and contracts you already track.", action: { label: "New report", run: openEditor } });
    var select = document.getElementById("tper-revoke-form").elements.namedItem("report");
    var first = select.options[0];
    select.textContent = "";
    select.appendChild(first);
    items.filter(function (r) { return r.state === "active"; }).forEach(function (r) {
      var option = R.el("option", "", titleOf(r) + " — " + R.when(r.created_at));
      option.value = r.report_id;
      select.appendChild(option);
    });
  }
  function kindTitle(kind) { var g = GROUPS.filter(function (x) { return x.kind === kind; })[0]; return g ? g.title : words(kind); }
  function showReport(report) {
    var host = document.getElementById("tper-report-detail");
    host.hidden = false;
    host.textContent = "";
    host.appendChild(R.el("h3", "", titleOf(report)));
    var list = R.el("ul");
    sourcesOf(report).forEach(function (source) {
      var label = sourceLabel(source.source_kind, source.source_id);
      var text = kindTitle(source.source_kind) + ": " + label + (source.state === "available" ? "" : source.state === "empty" ? " — nothing to report" : " — not included because " + (UNAVAILABLE[source.reason_code] || "it couldn't be read"));
      list.appendChild(R.el("li", "", text));
    });
    if (!list.childNodes.length) { list.appendChild(R.el("li", "", "No records were chosen for this report.")); }
    host.appendChild(list);
    if (report.state === "revoked") { host.appendChild(R.el("p", "muted", "This report was withdrawn: " + words(report.revocation_reason) + ".")); }
    host.appendChild(R.button("Close", "ghost", function () { host.hidden = true; }));
    show("Showing what's inside “" + titleOf(report) + "”.", report);
  }
  async function downloadReport(report, button) {
    if (!ready) { show("Reports aren't available right now."); return; }
    button.disabled = true;
    try {
      busy("Preparing the PDF…");
      var grant = await requestJson("/reports/" + encodeURIComponent(report.report_id) + "/download-grants", { method: "POST", body: JSON.stringify({ expected_control_revision: report.control_revision, idempotency_key: R.newKey() }) });
      if (!grant.token) { throw failure("This download was already started. Refresh and try again."); }
      var response = await global.fetch(apiBase + "/downloads/redeem", { method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer", headers: { "Accept": "application/pdf", "Content-Type": "application/json" }, body: JSON.stringify({ token: grant.token }) });
      grant.token = null;
      if (!response.ok) { throw response; }
      var bytes = await readBounded(response, MAX_PDF_BYTES);
      if (response.headers.get("Content-Type") !== "application/pdf") { throw failure("The PDF couldn't be downloaded. Please try again."); }
      var url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
      var link = document.createElement("a"); link.href = url; link.download = (R.slug(titleOf(report), "report")) + ".pdf";
      document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
      show("PDF downloaded.", { report_id: report.report_id, bytes: bytes.byteLength });
    } catch (error) {
      show(messageOf(error, "download the PDF"));
      if (error && error.status === 409) { await refresh(); }
    } finally { button.disabled = false; }
  }
  function revokeReport(report) {
    var form = document.getElementById("tper-revoke-form");
    form.elements.namedItem("report").value = report.report_id;
    document.getElementById("tper-admin").open = true;
    form.elements.namedItem("reason_code").focus();
    show("Choose why you're withdrawing “" + titleOf(report) + "”, then press Withdraw report.");
  }

  // ---- what to include ---------------------------------------------------------
  function sourceLabel(kind, id) {
    var found = (S.sources[kind] || []).filter(function (x) { return x.value === id; })[0];
    return found ? found.label : "A record no longer in the list";
  }
  function loadVendorNames() {
    return Promise.all([
      R.call("GET", "/api/v1/vendors").then(function (d) {
        (d.vendors || []).forEach(function (v) { S.vendorNames[v.id] = v.name; S.vendorNames[refOf(v.id || v.name)] = v.name; });
      }).catch(function () { return null; }),
      R.call("GET", "/api/v1/third-party/intelligence/vendors").then(function (d) {
        (d.items || []).forEach(function (v) { S.vendorNames[v.vendor_id] = v.display_label; });
        S.sources.intelligence_vendor = (d.items || []).map(function (v) { return { value: v.vendor_id, label: v.display_label + (v.state === "active" ? "" : " (" + words(v.state).toLowerCase() + ")") }; });
        return d.items || [];
      }).catch(function () { S.sources.intelligence_vendor = null; return []; }),
    ]);
  }
  function loadFindings() {
    return R.call("GET", "/api/v1/third-party/intelligence/screening/runs").then(function (d) {
      var runs = (d.items || []).slice(0, 20);
      return Promise.all(runs.map(function (run) {
        return R.call("GET", "/api/v1/third-party/intelligence/screening/runs/" + encodeURIComponent(run.run_id) + "/findings").then(function (f) { return f.items || []; }).catch(function () { return []; });
      }));
    }).then(function (lists) {
      S.sources.intelligence_finding = [].concat.apply([], lists).map(function (f) {
        return { value: f.finding_id, label: vendorName(f.vendor_id) + " — " + words(f.severity).toLowerCase() + " " + words(f.kind).toLowerCase() + " finding" };
      });
    }).catch(function () { S.sources.intelligence_finding = null; });
  }
  function loadList(kind, url, label, key) {
    return R.call("GET", url).then(function (d) {
      S.sources[kind] = (d.items || []).map(function (x) { return { value: x[key], label: label(x) }; });
    }).catch(function () { S.sources[kind] = null; });
  }
  function loadSources() {
    return loadVendorNames().then(function () {
      return Promise.all([
        loadFindings(),
        loadList("lifecycle_assessment", "/api/v1/third-party/lifecycle/assessments", function (a) { return vendorName(a.vendor_ref) + " — " + (a.highest_severity ? words(a.highest_severity).toLowerCase() + " risk" : "no issues found yet"); }, "assessment_id"),
        loadList("lifecycle_record", "/api/v1/third-party/lifecycle/lifecycles", function (l) { return vendorName(l.vendor_ref) + " — " + words(l.stage).toLowerCase(); }, "lifecycle_id"),
        loadList("contract_record", "/api/v1/third-party/contracts?limit=500", function (c) { return words(c.title_ref) + " (" + (c.record_kind === "engagement" ? "engagement" : "contract") + ", " + vendorName(c.vendor_id) + ")"; }, "record_id"),
        loadList("collaboration_invitation", "/api/v1/third-party/collaboration/invitations?limit=500", function (i) { return vendorName(i.vendor_ref) + " — " + words(i.status).toLowerCase(); }, "invitation_id"),
      ]);
    });
  }
  function renderSources() {
    var host = document.getElementById("tper-sources");
    host.textContent = "";
    GROUPS.forEach(function (group) {
      var box = R.el("fieldset", "tper-group");
      box.appendChild(R.el("legend", "", group.title));
      var items = S.sources[group.kind];
      if (items === null || items === undefined) {
        box.appendChild(R.el("p", "muted", "You can't see these records, or this area isn't turned on."));
      } else if (!items.length) {
        box.appendChild(R.el("p", "muted", "None yet."));
      } else {
        var list = R.el("div", "tper-group-list");
        items.forEach(function (item) {
          var label = R.el("label");
          var box2 = document.createElement("input");
          box2.type = "checkbox";
          box2.name = "include";
          box2.value = group.kind + "\u0000" + item.value;
          label.appendChild(box2);
          label.appendChild(R.el("span", "", item.label));
          list.appendChild(label);
        });
        box.appendChild(list);
      }
      host.appendChild(box);
    });
  }
  async function openEditor() {
    if (!ready) { show("Reports aren't available right now."); return; }
    editor.hidden = false;
    document.getElementById("tper-sources-status").textContent = "Loading your records…";
    await loadSources();
    renderSources();
    document.getElementById("tper-sources-status").textContent = "Tick the records this report should sum up.";
    createForm.elements.namedItem("report_title").focus();
  }
  function chosenSelectors() {
    return Array.prototype.slice.call(createForm.querySelectorAll('input[name="include"]:checked')).map(function (box) {
      var parts = box.value.split("\u0000");
      return { source_kind: parts[0], source_id: parts[1] };
    });
  }

  // ---- loading -----------------------------------------------------------------
  async function refresh() {
    var mark = document.getElementById("tper-runtime-state");
    var headline = document.getElementById("tper-runtime-headline");
    var detail = document.getElementById("tper-runtime-detail");
    try {
      var state = await requestJson("/status");
      setMutationReady(state.ready === true);
      mark.dataset.state = state.ready ? "ready" : "failed";
      mark.className = "badge " + (state.ready ? "ok" : "warn");
      mark.textContent = state.ready ? "Ready" : (state.enabled ? "Not ready" : "Turned off");
      headline.textContent = state.ready ? "Reports are available" : "Reports aren't available yet";
      detail.textContent = state.ready ? "You can create and download reports." : (state.enabled ? "Reports are turned on but still need to be set up by your administrator." : "Reports are turned off. An administrator can turn them on above.");
      var reports = await requestJson("/reports?limit=100");
      S.reports = Array.isArray(reports.items) ? reports.items : [];
      if (!S.namesLoaded) { await loadVendorNames(); S.namesLoaded = true; }
      renderReports();
      show("Reports refreshed.", reports);
    } catch (error) {
      setMutationReady(false);
      mark.dataset.state = "failed";
      mark.className = "badge warn";
      mark.textContent = "Unavailable";
      headline.textContent = "Reports aren't available";
      detail.textContent = messageOf(error, "load reports");
      document.getElementById("tper-count").textContent = "";
      show(messageOf(error, "load reports"));
    }
  }

  document.getElementById("tper-new").addEventListener("click", openEditor);
  document.getElementById("tper-cancel").addEventListener("click", function () { editor.hidden = true; });
  createForm.addEventListener("submit", async function (event) {
    event.preventDefault();
    var formStatus = document.getElementById("tper-form-status");
    var submit = document.getElementById("tper-create");
    try {
      var values = Object.fromEntries(new FormData(createForm).entries());
      var selectors = chosenSelectors();
      if (!selectors.length) { throw failure("Tick at least one record to include."); }
      if (selectors.length > 500) { throw failure("Choose 500 records or fewer."); }
      var payload = {
        expected_absent_revision: 0,
        selectors: selectors,
        brand: {
          organization_name: plainText(values.organization_name, "Organization name", 120),
          report_title: plainText(values.report_title, "Report title", 160),
          footer_text: plainText(values.footer_text, "Footer text", 160),
          primary_color: String(values.primary_color).toUpperCase(),
          accent_color: String(values.accent_color).toUpperCase(),
          text_color: String(values.text_color).toUpperCase(),
        },
        idempotency_key: R.newKey(),
      };
      submit.disabled = true;
      formStatus.textContent = "Creating the report…";
      var result = await requestJson("/reports", { method: "POST", body: JSON.stringify(payload) });
      formStatus.textContent = "";
      editor.hidden = true;
      show("Report created. Use Download PDF to get it.", result);
      await refresh();
    } catch (error) {
      formStatus.textContent = messageOf(error, "create the report");
    } finally { submit.disabled = false; }
  });
  document.getElementById("tper-revoke-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    var form = event.currentTarget;
    var reportId = form.elements.namedItem("report").value;
    var report = S.reports.filter(function (r) { return r.report_id === reportId; })[0];
    if (!report) { show("Choose the report to withdraw."); return; }
    var reasonSelect = form.elements.namedItem("reason_code");
    var reasonText = reasonSelect.options[reasonSelect.selectedIndex].text;
    await runDestructive(
      form,
      "Withdrawing the report…",
      "Withdraw the report “" + titleOf(report) + "” (" + reasonText.toLowerCase() + ")? It can no longer be downloaded, and downloads already started will stop working.",
      "Withdraw report",
      async function () {
        var result = await requestJson("/reports/" + encodeURIComponent(report.report_id) + "/revoke", {
          method: "POST",
          body: JSON.stringify({ expected_control_revision: report.control_revision, reason_code: reasonSelect.value, idempotency_key: R.newKey() }),
        });
        show("Report withdrawn.", result);
        await refresh();
      }
    );
  });
  document.getElementById("tper-purge-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    var limit = 1000;
    await runDestructive(
      event.currentTarget,
      "Removing expired report files…",
      "Permanently purge up to " + limit + " expired report files? They can't be downloaded or restored afterwards. Reports that haven't expired are not affected.",
      "Remove expired files",
      async function () {
        var result = await requestJson("/maintenance/purge", { method: "POST", body: JSON.stringify({ limit: limit, idempotency_key: R.newKey() }) });
        show(result.count ? "Removed " + result.count + " expired report " + (result.count === 1 ? "file." : "files.") : "There were no expired report files to remove.", result);
        await refresh();
      }
    );
  });
  document.getElementById("tper-integrity").addEventListener("click", async function () {
    try {
      busy("Checking reports…");
      var result = await requestJson("/integrity");
      show("All good: " + result.reports + (result.reports === 1 ? " report and " : " reports and ") + result.audit_events + " history entries checked; nothing has been altered.", result);
    } catch (error) { show(messageOf(error, "check the reports")); }
  });
  document.getElementById("tper-audit").addEventListener("click", async function () {
    try {
      busy("Loading report history…");
      var result = await requestJson("/audit?limit=100");
      var byId = {};
      S.reports.forEach(function (r) { byId[r.report_id] = titleOf(r); });
      R.table(document.getElementById("tper-admin-list"), [
        { label: "When", value: function (e) { return R.when(e.occurred_at); } },
        { label: "What happened", value: function (e) { return ACTIONS[e.action] || words(e.action); } },
        { label: "Report", value: function (e) { return byId[e.report_id] || "An earlier report"; } },
      ], result.items || [], [], { text: "No report history yet." });
      show("Report history loaded.", result);
    } catch (error) { show(messageOf(error, "load the report history")); }
  });
  document.getElementById("tper-refresh").addEventListener("click", refresh);

  refresh().then(async function () {
    var requested = new URLSearchParams(global.location.search).get("report");
    if (!requested) { return; }
    var record = S.reports.filter(function (r) { return r.report_id === requested; })[0];
    try {
      if (!record) { record = await requestJson("/reports/" + encodeURIComponent(requested)); }
      if (!record || record.report_id !== requested) { throw failure("The linked report couldn't be found. Refresh the page to see the latest."); }
      showReport(record);
    } catch (error) { show(messageOf(error, "open the linked report")); }
  });
}(window));
