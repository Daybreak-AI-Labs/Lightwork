(function () {
  "use strict";
  var root = document.getElementById("workspace-recovery");
  if (!root) return;
  var get = function (id) { return document.getElementById("wrec-" + id); };
  var state = null, review = null, busy = false, installationPoints = [], accessDenied = false;
  var comparison = null, comparisonContext = null, comparisonGeneration = 0;
  var drillEvidence = null, drillContext = null, drillGeneration = 0;
  var custodyEvidence = null, custodyContext = null, custodyGeneration = 0;
  var reconciliation = null;
  var identityTransition = null;
  var accessMessage = "Your recovery access is unavailable. Sign in with an authorized account and reload this page before continuing.";
  function say(text) { get("status").textContent = text; }
  function controls() {
    var ready = !busy && !accessDenied && !!state;
    var hasKey = ready && !!state.key_id;
    var selected = hasKey && !!get("snapshot").value;
    root.querySelectorAll("button").forEach(function (button) { button.disabled = busy || accessDenied; });
    ["key-create", "key-import"].forEach(function (id) { get(id).disabled = !ready || !!state.key_id; });
    ["key-download", "create", "import"].forEach(function (id) { get(id).disabled = !hasKey; });
    ["inspect", "download", "drill", "drill-report-load"].forEach(function (id) { get(id).disabled = !selected; });
    get("drill-report-download").disabled = !(ready && drillEvidence && drillEvidence.snapshot === get("snapshot").value && drillContext === state.context);
    get("custody-file").disabled = !selected;
    get("custody-confirm").disabled = !selected || !get("custody-file").files.length;
    get("custody-test").disabled = !selected || !get("custody-file").files.length || !get("custody-confirm").checked;
    get("custody-report-load").disabled = !selected;
    get("custody-report-download").disabled = !(ready && custodyEvidence && custodyEvidence.snapshot === get("snapshot").value && custodyContext === state.context);
    var bootstrap = ready && state.bootstrap && typeof state.bootstrap === "object" ? state.bootstrap : null;
    var bootstrapState = bootstrap ? bootstrap.state : "not_prepared";
    var canPrepareBootstrap = ready && hasKey && state.recovery_mode === true
      && ["restore_pending", "reconciliation_required"].indexOf(bootstrapState) === -1;
    get("bootstrap-source-offline").disabled = !canPrepareBootstrap;
    get("bootstrap-prepare").disabled = !canPrepareBootstrap || !get("bootstrap-source-offline").checked;
    get("bootstrap-resume").disabled = !(ready && state.recovery_mode === true && bootstrapState === "restore_pending");
    var custodyInstallReady = ready && state.recovery_mode === true && selected
      && bootstrapState === "prepared" && custodyEvidence && custodyContext === state.context
      && custodyEvidence.snapshot === get("snapshot").value && custodyEvidence.current_build_matches === true
      && ["verified_supported_formats", "no_encrypted_records"].indexOf(custodyEvidence.outcome) !== -1;
    get("custody-install-panel").hidden = !custodyInstallReady;
    get("custody-install-file").disabled = !custodyInstallReady;
    get("custody-install-confirm").disabled = !custodyInstallReady || !get("custody-install-file").files.length;
    get("custody-install").disabled = !custodyInstallReady || !get("custody-install-file").files.length || !get("custody-install-confirm").checked;
    get("snapshot").disabled = !hasKey;
    get("retire").disabled = !selected || !get("retire-confirm").checked;
    get("encryption-download").disabled = !hasKey || !state.encryption_custody || !state.encryption_custody.local_export_available || !get("encryption-confirm").checked;
    get("prepare").disabled = !selected || !state.live_restore_available;
    get("restore").disabled = !ready || !review || !get("confirm").checked;
    get("installation-authorize").disabled = !ready || !state.live_restore_available || !get("installation-confirm").checked;
    get("installation-rollback").disabled = get("installation-authorize").disabled || !get("installation-point").value;
    get("installation-transition-file").disabled = !ready || !state.live_restore_available;
    get("installation-transition-review").disabled = !ready || !state.live_restore_available || !get("installation-transition-file").files.length;
    get("installation-transition-confirm").disabled = !ready || !identityTransition;
    if (get("installation-transition-file").files.length && (!identityTransition || !get("installation-transition-confirm").checked)) get("installation-authorize").disabled = true;
    get("installation-retire-point").disabled = !ready || !installationPoints.some(function (row) { return row.can_retire; });
    var retiring = installationPoints.find(function (row) { return row.id === get("installation-retire-point").value; });
    get("installation-retire").disabled = !ready || !retiring || !retiring.can_retire || !get("installation-retire-confirm").checked;
    get("compare").disabled = !selected;
    var captured = ready && comparison && comparison.snapshot.id === get("snapshot").value && comparisonContext === state.context;
    get("comparison-download").disabled = !captured;
    get("comparison-previous").disabled = !captured || comparison.offset === 0;
    get("comparison-next").disabled = !captured || !comparison.has_more;
    var canComplete = ready && reconciliation && reconciliation.state === "reconciliation_required"
      && reconciliation.unresolved === 0 && reconciliation.global_start_blocked !== true;
    get("reconciliation-confirm").disabled = !canComplete;
    get("reconciliation-complete").disabled = !canComplete || !get("reconciliation-confirm").checked;
    get("reconciliation").querySelectorAll('input:not([type="checkbox"]), textarea, select').forEach(function (input) { input.disabled = !ready; });
  }
  function clearReview() { review = null; get("review").hidden = true; get("review-text").textContent = ""; get("confirm").checked = false; }
  function clearConfirmations() {
    clearReview();
    clearIdentityTransition();
    root.querySelectorAll('input[type="checkbox"]').forEach(function (input) { input.checked = false; });
  }
  function clearIdentityTransition() { identityTransition = null; get("installation-transition-details").textContent = ""; get("installation-transition-confirm").checked = false; }
  function clearComparison() {
    comparison = null; comparisonContext = null; comparisonGeneration++;
    get("comparison-report").hidden = true;
    ["comparison-status", "comparison-summary", "comparison-items", "comparison-identity"].forEach(function (id) { get(id).replaceChildren(); });
  }
  function clearDrillReport() {
    drillEvidence = null; drillContext = null; drillGeneration++;
    get("drill-report").hidden = true;
    ["drill-report-status", "drill-report-summary", "drill-report-build", "drill-report-identity", "drill-report-scope"].forEach(function (id) { get(id).textContent = ""; });
  }
  function clearCustodyUpload() { get("custody-file").value = ""; get("custody-install-file").value = ""; get("custody-confirm").checked = false; get("custody-install-confirm").checked = false; }
  function clearCustodyReport() {
    custodyEvidence = null; custodyContext = null; custodyGeneration++;
    get("custody-report").hidden = true;
    ["custody-status", "custody-summary", "custody-build", "custody-totals", "custody-checks", "custody-coverage", "custody-limits", "custody-identity"].forEach(function (id) { get(id).replaceChildren(); });
  }
  function clearEvidence() { clearComparison(); clearDrillReport(); clearCustodyReport(); clearCustodyUpload(); }
  function clearReconciliation() {
    reconciliation = null;
    get("reconciliation").hidden = true;
    get("reconciliation-status").textContent = "";
    get("reconciliation-items").querySelectorAll('input[type="text"]').forEach(function (input) { input.value = ""; input.disabled = true; });
    get("reconciliation-items").replaceChildren();
    get("reconciliation-blocked").hidden = true;
    get("reconciliation-confirm").checked = false;
  }
  function clearPrivateState() {
    accessDenied = true; state = null; installationPoints = []; clearConfirmations(); clearEvidence(); clearReconciliation();
    root.querySelectorAll("input, textarea").forEach(function (input) { if (input.type !== "checkbox") input.value = ""; });
    root.querySelectorAll("select, ul").forEach(function (element) { element.replaceChildren(); });
    ["identity", "last-restore", "key-status", "encryption-status", "result", "bootstrap-status", "installation-result", "installation-retention-status"].forEach(function (id) { get(id).textContent = ""; });
    controls();
  }
  async function request(path, body, asFile, fileCurrent, custodySnapshot, keyInstall) {
    if (accessDenied) throw new Error(accessMessage);
    var headers = { "X-Lightwork-Recovery-Context": state ? state.context : "", "X-Lightwork-Expected-Tenant": state ? state.expected_tenant || "" : "" };
    if (custodySnapshot) { headers["X-Lightwork-Confirm-Custody-Test"] = "true"; headers["X-Lightwork-Snapshot-SHA256"] = custodySnapshot; }
    if (keyInstall) {
      headers["X-Lightwork-Confirm-Key-Install"] = "true";
      headers["X-Lightwork-Snapshot-SHA256"] = keyInstall.snapshotSha256;
      headers["X-Lightwork-Custody-Report"] = keyInstall.reportId;
    }
    var init = { credentials: "same-origin", cache: "no-store", headers: headers };
    if (body !== undefined) {
      init.method = "POST";
      if (body instanceof Blob) { headers["Content-Type"] = "application/octet-stream"; init.body = body; }
      else { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    }
    var response = await fetch("/api/v1/recovery/" + path, init);
    if (response.status === 401 || response.status === 403) {
      clearPrivateState();
      throw new Error(accessMessage);
    }
    if (!response.ok) {
      clearConfirmations(); clearEvidence();
      var error = await response.json().catch(function () { return {}; });
      throw new Error(plainDetail(error.detail, response));
    }
    if (asFile) {
      var blob = await response.blob();
      if (fileCurrent && !fileCurrent()) throw new Error("The report or comparison selection changed. Load the selected evidence again before downloading.");
      var url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = asFile; document.body.appendChild(link); link.click(); link.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      return null;
    }
    return response.json();
  }
  // The recovery service explains refusals in sentences written for people;
  // anything else (codes, internal names) becomes a plain fallback.
  function plainDetail(detail, response) {
    if (typeof detail === "string" && detail.length <= 400 && /^[A-Z]/.test(detail)
      && !/[_{}<>\[\]]|HTTP|Traceback|Error:/.test(detail)) return detail;
    if (window.mvErrorText) return window.mvErrorText(response, "complete this recovery step");
    return "Recovery could not complete this step.";
  }
  function list(id, items) {
    get(id).replaceChildren();
    items.forEach(function (text) { var item = document.createElement("li"); item.textContent = text; get(id).appendChild(item); });
  }
  function renderBootstrapStatus() {
    var bootstrap = state && state.bootstrap && typeof state.bootstrap === "object" ? state.bootstrap : null;
    if (!bootstrap) { get("bootstrap-status").textContent = "This installation has no active new-install recovery."; return; }
    var retained = bootstrap.retained_keys && typeof bootstrap.retained_keys === "object" ? bootstrap.retained_keys.state : "not_installed";
    var messages = {
      prepared: retained === "installed" ? "This empty installation is prepared and the exact tested encryption keys are installed. Select the snapshot and prepare restore."
        : retained === "installing" ? "Key installation was interrupted. Choose the exact tested key file again to finish safely."
          : "This empty installation is prepared. Test and install its separately retained encryption keys before restoring encrypted records.",
      restore_pending: "A restore onto this new installation was interrupted. Recover it here before attempting another restore.",
      reconciliation_required: "Restored files are held while you review queued and possibly external work below.",
      completed: "The most recent new-install recovery and restored-work review completed.",
      unavailable: bootstrap.detail || "New-installation recovery status is unavailable. Remain in recovery mode.",
    };
    get("bootstrap-status").textContent = messages[bootstrap.state] || "New-installation recovery is still protected. Refresh before continuing.";
  }
  function verifyReconciliation(row) {
    var validStates = ["not_required", "prepared", "restore_pending", "reconciliation_required", "completed"];
    if (!row || typeof row !== "object" || validStates.indexOf(row.state) === -1 || !Array.isArray(row.items)
      || !count(row.unresolved) || row.items.length > 10000) throw new Error("The list of restored work could not be read. Stay in recovery mode and refresh.");
    if (row.state !== "reconciliation_required") {
      if (row.items.length || row.unresolved) throw new Error("The list of restored work doesn't add up. Stay in recovery mode and refresh.");
      return;
    }
    var ids = new Set(), unresolved = 0;
    row.items.forEach(function (item) {
      var okay = item && typeof item.id === "string" && /^[a-f0-9]{32}$/.test(item.id) && !ids.has(item.id)
        && typeof item.category === "string" && item.category.length > 0 && item.category.length <= 128
        && typeof item.source === "string" && item.source.length > 0 && item.source.length <= 4096
        && typeof item.record_id === "string" && item.record_id.length <= 4096
        && typeof item.prior_state === "string" && item.prior_state.length <= 256
        && typeof item.safe_state === "string" && item.safe_state.length <= 256
        && typeof item.requires_external_readback === "boolean" && ["unresolved", "resolved"].indexOf(item.state) !== -1;
      if (!okay) throw new Error("The list of restored work could not be read. Stay in recovery mode and refresh.");
      ids.add(item.id); if (item.state === "unresolved") unresolved++;
    });
    if (unresolved !== row.unresolved) throw new Error("The restored-work count changed. Reload recovery.");
    var protectedState = row.protected_authority;
    if (!protectedState || protectedState.classification_complete !== true || !count(protectedState.file_count)
      || protectedState.file_count !== protectedState.classified_count || !digest(protectedState.state_sha256)) {
      throw new Error("Not every protected record could be checked. Stay in recovery mode.");
    }
  }
  function reconciliationActionButton(label, item, disposition, evidenceInput) {
    var button = document.createElement("button"); button.type = "button"; button.textContent = label; button.className = "btn";
    button.addEventListener("click", function () { return action(async function () {
      var selected = get("snapshot").value;
      var reference = evidenceInput ? evidenceInput.value.trim() : null;
      if (evidenceInput && reference.length < 8) throw new Error("Enter where the proof is, such as a ticket or record reference (at least 8 characters).");
      await request("reconciliation/items/" + encodeURIComponent(item.id), {
        confirm: true, disposition: disposition, evidence_reference: reference,
      });
      get("reconciliation-confirm").checked = false;
      await load(selected);
      say("Your decision was recorded. Review the remaining items before going back to normal.");
    }); });
    return button;
  }
  // Guided form for a held chat-app message or reading position: the person
  // checks the service itself and records what they saw. The exact readback
  // the server verifies is built from the template it sent; nothing replays.
  function field(labelText, control) {
    var label = document.createElement("label"); label.textContent = labelText + " "; label.append(control); return label;
  }
  function outcomeSelect(labelText, yes, no) {
    var select = document.createElement("select"); select.className = "select";
    [["", "Choose what you found"], ["recorded", yes], ["absent", no]].forEach(function (pair) {
      var option = document.createElement("option"); option.value = pair[0]; option.textContent = pair[1]; select.append(option);
    });
    select.setAttribute("aria-label", labelText);
    return { select: select, label: field(labelText, select) };
  }
  function localNow() {
    var now = new Date(); now.setSeconds(0, 0);
    return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }
  function channelReadbackForm(item) {
    var template = item.readback_template || {}, box = document.createElement("div"), getters = [];
    box.className = "wrec-readback";
    var intro = document.createElement("p");
    intro.textContent = "Check this in the chat service itself, then record what you found. Nothing is sent again.";
    box.append(intro);
    var when = document.createElement("input"); when.type = "datetime-local"; when.className = "input"; when.value = localNow();
    box.append(field("When did you check? (within the last 15 minutes)", when));
    var proof = document.createElement("input"); proof.type = "text"; proof.className = "input"; proof.maxLength = 512; proof.autocomplete = "off";
    proof.placeholder = "Ticket, screenshot name, log or record reference";
    box.append(field("Where is the proof?", proof));
    if (template.namespace === "channel-deliveries") {
      var run = outcomeSelect("Did Lightwork's request reach the service?", "Yes, it arrived", "No, it never arrived");
      var sent = outcomeSelect("Was the message delivered?", "Yes, it was delivered", "No, it was not delivered");
      box.append(run.label, sent.label);
      getters.push(function (out) {
        if (!run.select.value || !sent.select.value) throw new Error("Choose what you found for the request and the delivery.");
        out.execution = run.select.value; out.delivery = sent.select.value;
      });
      var chunkInputs = Object.keys(template.chunks || {}).map(function (key, index) {
        var part = outcomeSelect("Message part " + (index + 1) + ": was it delivered?", "Yes, it was delivered", "No, it was not delivered");
        var ref = document.createElement("input"); ref.type = "text"; ref.className = "input"; ref.maxLength = 512; ref.autocomplete = "off";
        box.append(part.label, field("Message part " + (index + 1) + ": reference shown by the service (if delivered)", ref));
        return { key: key, select: part.select, ref: ref };
      });
      getters.push(function (out) {
        out.chunks = {};
        chunkInputs.forEach(function (row, index) {
          if (!row.select.value) throw new Error("Choose what you found for message part " + (index + 1) + ".");
          out.chunks[row.key] = { outcome: row.select.value, provider_id: row.ref.value.trim() };
        });
      });
    } else {
      var checkpoint = template.checkpoint || {};
      if ("cursor" in checkpoint) {
        var note = document.createElement("p");
        note.textContent = "Nothing else to fill in: reading restarts safely from the service's own list of unread messages.";
        box.append(note);
        getters.push(function (out) { out.checkpoint = { cursor: "" }; });
      } else if ("last_rowid" in checkpoint) {
        var last = document.createElement("input"); last.type = "number"; last.className = "input"; last.min = "0"; last.step = "1";
        box.append(field("Number of the last message already handled", last));
        getters.push(function (out) {
          var value = Number(last.value);
          if (last.value === "" || !Number.isSafeInteger(value) || value < 0) throw new Error("Enter the number of the last message already handled.");
          out.checkpoint = { last_rowid: value };
        });
      } else if ("watermark" in checkpoint) {
        var newest = document.createElement("input"); newest.type = "text"; newest.inputMode = "numeric"; newest.className = "input"; newest.maxLength = 40;
        box.append(field("Number of the newest post already handled", newest));
        getters.push(function (out) {
          if (!/^[0-9]{1,40}$/.test(newest.value.trim())) throw new Error("Enter the newest post number using digits only.");
          out.checkpoint = { watermark: newest.value.trim() };
        });
      } else {
        var after = document.createElement("input"); after.type = "datetime-local"; after.className = "input";
        box.append(field("Only pick up messages sent after", after));
        getters.push(function (out) {
          var at = new Date(after.value);
          if (!after.value || !Number.isFinite(at.getTime())) throw new Error("Choose the time to pick up messages from.");
          out.checkpoint = { floor: at.toISOString() };
        });
      }
    }
    var confirm = document.createElement("button"); confirm.type = "button"; confirm.className = "btn btn--primary";
    confirm.textContent = "Confirm what I found and retire the old work";
    confirm.addEventListener("click", function () { return action(async function () {
      var observed = new Date(when.value).getTime() / 1000;
      if (!when.value || !Number.isFinite(observed)) throw new Error("Choose when you checked the service.");
      if (proof.value.trim().length < 8) throw new Error("Enter where the proof is (at least 8 characters).");
      var readback = Object.assign({}, template, { observed_at: observed, evidence_reference: proof.value.trim(), outcome: "confirmed" });
      getters.forEach(function (apply) { apply(readback); });
      await request("reconciliation/items/" + encodeURIComponent(item.id), {
        confirm: true, disposition: "channel_readback_confirmed", channel_readback: readback,
      });
      await load(get("snapshot").value);
      say("What you found was recorded. The old messages will not be sent again automatically.");
    }); });
    box.append(confirm);
    return box;
  }
  function renderReconciliation(row) {
    verifyReconciliation(row); reconciliation = row;
    get("reconciliation-items").replaceChildren();
    get("reconciliation-confirm").checked = false;
    if (row.state !== "reconciliation_required") { get("reconciliation").hidden = true; controls(); return; }
    get("reconciliation").hidden = false;
    var globalBlocked = false;
    row.items.forEach(function (item) {
      var entry = document.createElement("li"), title = document.createElement("strong"), detail = document.createElement("p");
      var human = function (value) { return window.mvHuman ? window.mvHuman(value) : String(value || "").replaceAll("_", " "); };
      title.textContent = human(item.category);
      detail.textContent = "When the copy was made it was " + human(item.prior_state).toLowerCase() + "; it is now held as " + human(item.safe_state).toLowerCase() + ".";
      var where = document.createElement("details"), whereSummary = document.createElement("summary"), whereText = document.createElement("p");
      whereSummary.textContent = "Technical details"; whereText.textContent = "Record: " + (item.record_id || "none") + ". Source: " + item.source + ".";
      where.append(whereSummary, whereText);
      entry.append(title, detail, where);
      if (item.state === "resolved") {
        var resolved = document.createElement("p"); resolved.textContent = "Resolved: " + (window.mvHuman ? window.mvHuman(item.disposition || "recorded") : (item.disposition || "recorded")) + "."; entry.append(resolved);
      } else if (item.safe_state === "global_start_blocked") {
        globalBlocked = true;
        var blocked = document.createElement("p"); blocked.textContent = "This version of Lightwork can't safely sort out this item. Normal use stays blocked."; entry.append(blocked);
      } else if (item.category === "channel_authority") {
        entry.append(channelReadbackForm(item));
      } else if (item.requires_external_readback) {
        var label = document.createElement("label"), evidence = document.createElement("input");
        evidence.type = "text"; evidence.maxLength = 512; evidence.autocomplete = "off"; evidence.placeholder = "Ticket, receipt, log, or external record reference";
        evidence.className = "input";
        label.textContent = "Where is the proof of what happened? "; label.append(evidence); entry.append(label);
        entry.append(reconciliationActionButton("It did not happen outside Lightwork", item, "external_effect_absent", evidence));
        entry.append(reconciliationActionButton("It did happen: record it", item, "external_effect_recorded", evidence));
      } else {
        entry.append(reconciliationActionButton("Retire restored request", item, "retire_and_create_new_request", null));
      }
      get("reconciliation-items").append(entry);
    });
    reconciliation.global_start_blocked = globalBlocked;
    get("reconciliation-blocked").hidden = !globalBlocked;
    get("reconciliation-status").textContent = row.unresolved
      ? row.unresolved + " restored item" + (row.unresolved === 1 ? " remains" : "s remain") + " unresolved. Normal startup is blocked."
      : "Every restored item has a decision. Confirm below to finish recovery and restart normal work.";
    controls();
  }
  async function load(selected) {
    clearEvidence();
    installationPoints = [];
    get("installation-retire-confirm").checked = false;
    get("installation-retire-point").replaceChildren();
    var result = await request("status");
    if (state && result.context !== state.context) { clearReview(); clearIdentityTransition(); get("installation-transition-file").value = ""; get("installation-code").value = ""; get("installation-confirm").checked = false; }
    state = result;
    renderBootstrapStatus();
    get("identity").textContent = "Workspace: " + result.tenant + " · Lightwork " + result.application_version;
    var last = result.last_restore;
    get("last-restore").textContent = !last ? "" : last.state === "completed" ? "The most recent local restore completed and passed file and database checks. Its safety snapshot remains saved."
      : last.state === "reconciliation_required" ? "The restored files passed local checks, but restored work is still held for review below."
        : last.state === "rolled_back_to_empty" ? "An interrupted new-install restore was safely rolled back to the signed empty state. Prepare and review the restore again."
          : "The most recent restore does not have a verified completion receipt. Remain in recovery mode and review the saved safety snapshot before resuming work.";
    get("key-status").textContent = result.key_id ? "A recovery key is configured. Keep your separate copy safe." : result.key_error;
    get("encryption-status").textContent = result.encryption_custody ? result.encryption_custody.detail : "Matching encryption keys must be kept separately.";
    list("coverage", result.coverage); list("excluded", result.excluded); list("blockers", result.restore_blockers);
    get("snapshot").replaceChildren();
    var empty = document.createElement("option"); empty.value = ""; empty.textContent = "Choose a snapshot"; get("snapshot").appendChild(empty);
    result.snapshots.forEach(function (row, index) { var option = document.createElement("option"); option.value = row.id; var when = row.created_at ? new Date(row.created_at * 1000).toLocaleString() : "Snapshot " + (index + 1); option.textContent = when + " · " + (row.bytes / 1048576).toFixed(2) + " MB · " + row.id.slice(0, 8); get("snapshot").appendChild(option); });
    if (selected) get("snapshot").value = selected;
    try {
      var installation = await request("installation");
      get("installation-point").replaceChildren();
      var choose = document.createElement("option"); choose.value = ""; choose.textContent = "Choose a completed upgrade"; get("installation-point").appendChild(choose);
      (installation.recovery_points || []).filter(function (item) { return item.status === "upgraded"; }).forEach(function (item) {
        var option = document.createElement("option"); option.value = item.id; option.textContent = item.new_version + " → " + item.old_version + " · " + item.id.slice(0, 8); get("installation-point").appendChild(option);
      });
      installationPoints = installation.recovery_points || [];
      var option = document.createElement("option"); option.value = ""; option.textContent = "Choose a point to retire"; get("installation-retire-point").appendChild(option);
      var phase = { authorized: "Unused authorization", prepared: "Update prepared", applying: "Update in progress", incomplete: "Recovery unfinished", upgraded: "Completed upgrade", rolled_back: "Completed rollback", retired: "Retirement cleanup pending" };
      list("installation-retention-list", installationPoints.map(function (item) {
        var label = (phase[item.status] || "Recovery point") + " · " + (item.new_version ? item.old_version + " → " + item.new_version : item.old_version) + " · " + item.id.slice(0, 8);
        if (item.can_retire) { var entry = document.createElement("option"); entry.value = item.id; entry.textContent = label; get("installation-retire-point").appendChild(entry); }
        return label + (item.retirement_blocker ? ": " + item.retirement_blocker : item.cleanup_pending ? ": Continue the already reviewed retirement cleanup." : ": Eligible for reviewed retirement.");
      }));
      get("installation-retention-status").textContent = installationPoints.length + " of " + (installation.point_limit || 20) + " local installation slots used. " + (installationPoints.length ? "Retirement frees a slot after file cleanup succeeds." : "No installation recovery points are saved.");
      list("installation-retirement-history", (installation.retirement_history || []).map(function (item) { return item.id.slice(0, 8) + " · retired " + new Date(item.retired_at * 1000).toLocaleString(); }));
    } catch (error) { clearConfirmations(); clearEvidence(); if (accessDenied) throw error; get("installation-result").textContent = error.message; }
    if (result.bootstrap) {
      try { renderReconciliation(await request("reconciliation")); }
      catch (error) { clearReconciliation(); if (accessDenied) throw error; get("reconciliation").hidden = false; get("reconciliation-status").textContent = error.message; }
    } else clearReconciliation();
  }
  async function action(operation) {
    if (busy || accessDenied) return;
    busy = true; controls();
    try { await operation(); }
    catch (error) { clearConfirmations(); clearEvidence(); say(error.message || "Recovery could not complete this step. Retry after reviewing your connection and settings."); }
    finally { busy = false; controls(); }
  }
  function on(id, operation) { get(id).addEventListener("click", function () { return action(operation); }); }
  function path(actionName) { if (!get("snapshot").value) throw new Error("Choose a snapshot first."); return "snapshots/" + encodeURIComponent(get("snapshot").value) + "/" + actionName; }
  function count(value) { return Number.isSafeInteger(value) && value >= 0; }
  function digest(value) { return typeof value === "string" && /^[a-f0-9]{64}$/i.test(value); }
  function fileMetadata(value) { return value && typeof value === "object" && digest(value.sha256) && count(value.size); }
  function comparisonIdentity(row) {
    return JSON.stringify([row.report_id, row.target, row.snapshot.id, row.snapshot.sha256, row.snapshot.application_version,
      row.snapshot.compatibility, row.compared_at, row.application_version, row.total_changes, row.protected_changes,
      row.live_file_count, row.snapshot_file_count, row.live_state_sha256, row.snapshot_state_sha256, row.isolated]);
  }
  function verifyComparison(row, selected, offset) {
    var valid = row && typeof row === "object" && typeof row.report_id === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(row.report_id)
      && state && row.target === state.target && row.snapshot && row.snapshot.id === selected && digest(row.snapshot.sha256)
      && typeof row.snapshot.application_version === "string" && typeof row.snapshot.compatibility === "string"
      && typeof row.application_version === "string" && Number.isFinite(row.compared_at) && row.compared_at > 0
      && Number.isFinite(new Date(row.compared_at * 1000).getTime()) && row.restore_authorized === false && typeof row.isolated === "boolean"
      && digest(row.live_state_sha256) && digest(row.snapshot_state_sha256)
      && [row.total_changes, row.protected_changes, row.live_file_count, row.snapshot_file_count, row.offset].every(count)
      && row.protected_changes <= row.total_changes && row.offset === offset && row.limit === 50 && offset <= row.total_changes
      && Array.isArray(row.items) && row.items.length <= 50 && offset + row.items.length <= row.total_changes
      && row.has_more === (offset + row.items.length < row.total_changes) && (!row.has_more || row.items.length > 0);
    if (valid) valid = row.items.every(function (item) {
      return item && typeof item.path === "string" && item.path.length > 0 && item.path.length <= 4096 && typeof item.protected === "boolean"
        && (item.change === "added" && item.snapshot === null && fileMetadata(item.current)
          || item.change === "deleted" && item.current === null && fileMetadata(item.snapshot)
          || item.change === "changed" && fileMetadata(item.snapshot) && fileMetadata(item.current));
    });
    if (!valid) throw new Error("The comparison could not be verified for this workspace and snapshot. Compare again.");
  }
  function renderComparison() {
    var row = comparison;
    get("comparison-summary").textContent = "Captured " + new Date(row.compared_at * 1000).toLocaleString() + ": " + row.total_changes
      + (row.total_changes === 1 ? " file difference, including " : " file differences, including ") + row.protected_changes
      + (row.protected_changes === 1 ? " protected difference requiring administrator review." : " protected differences requiring administrator review.");
    get("comparison-status").textContent = row.total_changes ? "Showing differences " + (row.offset + 1) + "–" + (row.offset + row.items.length)
      + " of " + row.total_changes + "." : "No file differences were found in this captured comparison. This does not authorize recovery.";
    get("comparison-items").replaceChildren();
    row.items.forEach(function (item) {
      var li = document.createElement("li"), name = document.createElement("strong"), description = document.createElement("p");
      name.textContent = item.path;
      description.textContent = ({ added: "Added since the snapshot", changed: "Changed since the snapshot", deleted: "Removed since the snapshot" })[item.change]
        + (item.protected ? " · Protected: administrator reconciliation is required." : " · File difference; business impact has not been verified.");
      var details = document.createElement("details"), summary = document.createElement("summary");
      summary.textContent = "File verification details"; details.append(summary);
      ["snapshot", "current"].forEach(function (side) {
        var text = document.createElement("p"), file = item[side];
        text.textContent = (side === "snapshot" ? "Snapshot" : "Current at comparison") + ": " + (file ? file.size + " bytes · fingerprint " + file.sha256 : "Not present");
        details.append(text);
      });
      li.append(name, description, details); get("comparison-items").append(li);
    });
    get("comparison-identity").textContent = "Snapshot application: " + row.snapshot.application_version + " · Compatibility: " + row.snapshot.compatibility
      + ". Compared using Lightwork " + row.application_version + ". Snapshot archive fingerprint: " + row.snapshot.sha256
      + ". Snapshot file-set fingerprint: " + row.snapshot_state_sha256 + ". Current file-set fingerprint at comparison: " + row.live_state_sha256
      + ". Files: " + row.snapshot_file_count + " in the snapshot, " + row.live_file_count + " at comparison.";
    get("comparison-report").hidden = false;
  }
  async function comparisonPage(offset) {
    if (!comparison) return;
    var captured = comparison, generation = comparisonGeneration, context = comparisonContext;
    var row = await request(path("comparison"), { report_id: captured.report_id, offset: offset, limit: 50 });
    verifyComparison(row, captured.snapshot.id, offset);
    if (generation !== comparisonGeneration || context !== state.context || get("snapshot").value !== captured.snapshot.id
      || comparisonIdentity(row) !== comparisonIdentity(captured)) throw new Error("The captured comparison changed. Compare again before reviewing or downloading it.");
    comparison = row; renderComparison();
  }
  on("compare", async function () {
    clearConfirmations(); clearComparison();
    var selected = get("snapshot").value, context = state.context, generation = comparisonGeneration;
    say("Comparing the selected snapshot with the current workspace without changing live files…");
    var row = await request(path("compare"), {});
    verifyComparison(row, selected, 0);
    if (generation !== comparisonGeneration || context !== state.context || selected !== get("snapshot").value) throw new Error("The comparison selection changed. Compare the current selection again.");
    comparison = row; comparisonContext = context; renderComparison(); say("Comparison captured. Review the evidence; no restore has been authorized.");
  });
  on("comparison-previous", function () { if (comparison) return comparisonPage(Math.max(0, comparison.offset - 50)); });
  on("comparison-next", function () { if (comparison) return comparisonPage(comparison.offset + 50); });
  on("comparison-download", async function () {
    if (!comparison) return;
    var captured = comparison, generation = comparisonGeneration, context = comparisonContext;
    await request(path("comparison/download"), { report_id: captured.report_id }, "Lightwork-recovery-comparison-" + captured.report_id + ".json", function () {
      return !accessDenied && state && context === state.context && generation === comparisonGeneration && get("snapshot").value === captured.snapshot.id;
    });
    say("Signed comparison download started. It records captured evidence and does not clear the recovery guard.");
  });
  function timestamp(value) { return Number.isFinite(value) && value > 0 && Number.isFinite(new Date(value * 1000).getTime()); }
  function verifyDrillReport(row, selected) {
    var valid = row && row.state === "recorded" && typeof row.report_id === "string" && /^[a-f0-9]{32}$/.test(row.report_id)
      && row.snapshot === selected && state && row.target === state.target && digest(row.archive_sha256)
      && timestamp(row.snapshot_created_at) && timestamp(row.completed_at) && row.completed_at >= row.snapshot_created_at
      && typeof row.application_version === "string" && row.application_version.length > 0 && row.application_version.length <= 200
      && typeof row.application_identity === "string" && row.application_identity.length > 0 && row.application_identity.length <= 200
      && typeof row.current_build_matches === "boolean" && count(row.file_count) && Number.isFinite(row.elapsed_seconds) && row.elapsed_seconds >= 0
      && row.restored === true && row.post_restore_verified === true && row.restore_authorized === false
      && row.live_workspace_changed === false && row.external_stores_tested === false
      && typeof row.verification_scope === "string" && row.verification_scope.length > 0 && row.verification_scope.length <= 4096;
    if (!valid) throw new Error("The saved restore test could not be verified for this workspace and snapshot. Load it again or run a new temporary restore test.");
  }
  function snapshotAge(seconds) {
    if (seconds < 60) return "less than a minute";
    var unit = seconds < 3600 ? "minute" : seconds < 86400 ? "hour" : "day";
    var amount = Math.floor(seconds / (unit === "minute" ? 60 : unit === "hour" ? 3600 : 86400));
    return amount + " " + unit + (amount === 1 ? "" : "s");
  }
  async function loadDrillReport() {
    clearDrillReport();
    var selected = get("snapshot").value, context = state.context, generation = drillGeneration;
    var row = await request(path("drill/report"), {});
    if (generation !== drillGeneration || !state || context !== state.context || selected !== get("snapshot").value) throw new Error("The restore-test selection changed. Load the current snapshot's report again.");
    if (row && row.state === "not_recorded" && row.snapshot === selected) {
      get("drill-report-status").textContent = "A restore test has not been recorded for this snapshot. Run a temporary restore test to save evidence.";
      return;
    }
    verifyDrillReport(row, selected);
    drillEvidence = row; drillContext = context;
    get("drill-report-summary").textContent = "Test completed " + new Date(row.completed_at * 1000).toLocaleString() + ": " + row.file_count + " files restored and checked in " + row.elapsed_seconds
      + " seconds. Snapshot created " + new Date(row.snapshot_created_at * 1000).toLocaleString() + " (" + snapshotAge(row.completed_at - row.snapshot_created_at) + " old when tested).";
    get("drill-report-build").textContent = row.current_build_matches ? "The tested application build matches this app. This historical test does not prove the current workspace is ready for recovery."
      : "This test used a different application build (Lightwork " + row.application_version + "). Keep it as historical evidence; it does not prove readiness for this app.";
    get("drill-report-identity").textContent = "Snapshot: " + row.snapshot + ". Archive fingerprint: " + row.archive_sha256 + ". Test report: " + row.report_id + ". Tested application build: " + row.application_identity + ".";
    get("drill-report-scope").textContent = row.verification_scope;
    get("drill-report-status").textContent = "Saved restore-test evidence loaded. No live restore has been authorized.";
    get("drill-report").hidden = false;
  }
  on("drill-report-load", loadDrillReport);
  on("drill-report-download", async function () {
    if (!drillEvidence) return;
    var captured = drillEvidence, generation = drillGeneration, context = drillContext;
    await request(path("drill/report/download"), { report_id: captured.report_id }, "Lightwork-restore-test-" + captured.report_id + ".json", function () {
      return !accessDenied && state && context === state.context && generation === drillGeneration && get("snapshot").value === captured.snapshot;
    });
    say("Signed restore-test download started. This historical evidence does not authorize a live restore.");
  });
  function custodyCurrent(selected, context, generation) {
    return !accessDenied && state && context === state.context && generation === custodyGeneration && selected === get("snapshot").value;
  }
  async function custodyFileSha256(file) {
    if (!window.crypto || !window.crypto.subtle || typeof file.arrayBuffer !== "function") throw new Error("This browser cannot verify the uploaded copy. Open recovery in the desktop app or a supported secure browser.");
    var bytes = await file.arrayBuffer(), hashed = await window.crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(hashed)).map(function (value) { return value.toString(16).padStart(2, "0"); }).join("");
  }
  function custodyCount(value) { return count(value) && value <= 10000000; }
  function verifyCustody(row, selected, archiveHash, uploadHash) {
    var valid = row && row.schema === 1 && row.kind === "workspace-custody-test" && row.state === "recorded"
      && typeof row.report_id === "string" && /^[a-f0-9]{32}$/.test(row.report_id) && state && row.snapshot === selected && row.target === state.target && row.tenant === state.tenant
      && digest(row.archive_sha256) && digest(row.custody_sha256) && (!archiveHash || row.archive_sha256 === archiveHash) && (!uploadHash || row.custody_sha256 === uploadHash)
      && timestamp(row.completed_at) && timestamp(row.snapshot_created_at) && row.completed_at >= row.snapshot_created_at
      && typeof row.application_version === "string" && row.application_version.length > 0 && row.application_version.length <= 200
      && typeof row.application_identity === "string" && row.application_identity.length > 0 && row.application_identity.length <= 200
      && Number.isFinite(row.elapsed_seconds) && row.elapsed_seconds >= 0 && typeof row.current_build_matches === "boolean"
      && ["verified_supported_formats", "incomplete", "failed", "no_encrypted_records"].indexOf(row.outcome) !== -1
      && row.fresh_process === true && row.key_source === "uploaded-custody-copy" && row.coverage_complete === false
      && row.restore_authorized === false && row.live_workspace_changed === false && row.external_stores_tested === false
      && [row.files_scanned, row.sqlite_columns_scanned, row.unexamined_files].every(custodyCount)
      && Array.isArray(row.checks) && row.checks.length >= 1 && row.checks.length <= 20 && Array.isArray(row.limits) && row.limits.length >= 1 && row.limits.length <= 20
      && row.limits.every(function (item) { return typeof item === "string" && item.length >= 1 && item.length <= 2000; });
    var names = new Set(), verified = 0, failed = 0, unsupported = 0;
    if (valid) valid = row.checks.every(function (item) {
      if (!item || typeof item.format !== "string" || item.format.length < 1 || item.format.length > 100 || names.has(item.format)
        || ![item.detected, item.verified, item.failed, item.unsupported].every(custodyCount) || item.verified + item.failed + item.unsupported !== item.detected) return false;
      names.add(item.format); verified += item.verified; failed += item.failed; unsupported += item.unsupported; return true;
    });
    if (valid) valid = row.outcome === (failed ? "failed" : unsupported ? "incomplete" : verified ? "verified_supported_formats" : "no_encrypted_records");
    if (!valid) throw new Error("The retained-key test could not be verified for this workspace, snapshot and key file. Load the saved report or select your copy again.");
  }
  function renderCustody(row, context) {
    custodyEvidence = row; custodyContext = context;
    var outcomes = { verified_supported_formats: "Recognized supported formats verified", incomplete: "Test incomplete", failed: "Verification failed", no_encrypted_records: "No encrypted records tested" };
    get("custody-status").textContent = outcomes[row.outcome] + ". Review the exact coverage below; no live restore has been authorized.";
    var duration = row.elapsed_seconds < 0.1 ? "less than 0.1" : row.elapsed_seconds.toLocaleString(undefined, { maximumFractionDigits: 1 });
    get("custody-summary").textContent = "Completed " + new Date(row.completed_at * 1000).toLocaleString() + " in " + duration + " seconds using your uploaded key copy in a fresh temporary process. Snapshot created "
      + new Date(row.snapshot_created_at * 1000).toLocaleString() + " (" + snapshotAge(row.completed_at - row.snapshot_created_at) + " old when tested).";
    get("custody-build").textContent = row.current_build_matches ? "The tested application build matches this app. This saved result does not prove current recovery readiness."
      : "This historical test used a different application build (Lightwork " + row.application_version + "). It does not prove readiness for this app.";
    var formats = { "raw-v1": "Encrypted files (v1)", "raw-v2": "Encrypted files (rotated-key v2)", "raw-tenant-local": "Encrypted files (local tenant key)",
      "world-text-v1": "Supported workspace database text (v1)", "world-text-v2": "Supported workspace database text (rotated-key v2)",
      "world-text-tenant-local": "Supported workspace database text (local tenant key)", "unrecognized-encryption": "Unrecognized encryption", "scan-limit": "Scan limits reached" };
    list("custody-checks", row.checks.map(function (item) { return (formats[item.format] || item.format) + ": " + item.detected + " detected; " + item.verified + " verified; " + item.failed + " failed; " + item.unsupported + " unsupported."; }));
    var totals = row.checks.reduce(function (sum, item) { sum.verified += item.verified; sum.failed += item.failed; sum.unsupported += item.unsupported; return sum; }, { verified: 0, failed: 0, unsupported: 0 });
    get("custody-totals").textContent = totals.verified + " recognized encrypted records verified; " + totals.failed + " failed; " + totals.unsupported + " unsupported. " + row.unexamined_files + " files were not examined. Review the limits below even when no failures were found.";
    get("custody-coverage").textContent = row.files_scanned + " files and " + row.sqlite_columns_scanned + " SQLite columns scanned; " + row.unexamined_files + " files not examined. Coverage is limited to the recognized formats reported above.";
    list("custody-limits", row.limits);
    get("custody-identity").textContent = "Snapshot: " + row.snapshot + ". Archive fingerprint: " + row.archive_sha256 + ". Uploaded key file fingerprint: " + row.custody_sha256 + ". Report: " + row.report_id + ". Tested application build: " + row.application_identity + ".";
    get("custody-report").hidden = false;
  }
  on("custody-test", async function () {
    if (!get("custody-confirm").checked) return;
    clearCustodyReport();
    var file = get("custody-file").files[0], selected = get("snapshot").value, context = state.context, generation = custodyGeneration;
    if (!file || !file.size || file.size > 4 * 1024 * 1024) throw new Error("Choose a nonempty key file no larger than 4 MB.");
    get("custody-confirm").checked = false;
    say("Verifying the selected snapshot before testing your key file…");
    var inspected = await request(path("inspect"), {});
    if (!custodyCurrent(selected, context, generation)) throw new Error("The key test selection changed. Select your retained copy and confirm again.");
    if (!inspected || inspected.id !== selected || !digest(inspected.sha256)) throw new Error("The selected snapshot could not be verified. Verify it again before testing retained keys.");
    var uploadHash = await custodyFileSha256(file);
    if (!custodyCurrent(selected, context, generation)) throw new Error("The key test selection changed. Select your retained copy and confirm again.");
    say("Testing recognized encrypted formats in a fresh temporary process. No live files or shared keys are being changed…");
    var row = await request(path("custody-test"), file, undefined, undefined, inspected.sha256);
    if (!custodyCurrent(selected, context, generation)) throw new Error("The key test selection changed. Load the current snapshot's saved report again.");
    verifyCustody(row, selected, inspected.sha256, uploadHash);
    clearCustodyUpload(); renderCustody(row, context); say("Retained-key test finished. Review the recorded outcome and coverage below.");
  });
  on("custody-report-load", async function () {
    clearCustodyReport(); clearCustodyUpload();
    var selected = get("snapshot").value, context = state.context, generation = custodyGeneration;
    var row = await request(path("custody-test/report"), {});
    if (!custodyCurrent(selected, context, generation)) throw new Error("The key test selection changed. Load the current snapshot's saved report again.");
    if (row && row.state === "not_recorded" && row.snapshot === selected) { get("custody-status").textContent = "No retained-key test has been recorded for this snapshot. Choose your separately saved key file to run a temporary test."; return; }
    verifyCustody(row, selected); renderCustody(row, context);
  });
  on("custody-report-download", async function () {
    if (!custodyEvidence) return;
    var captured = custodyEvidence, context = custodyContext, generation = custodyGeneration;
    await request(path("custody-test/report/download"), { report_id: captured.report_id }, "Lightwork-retained-key-test-" + captured.report_id + ".json", function () {
      return custodyCurrent(captured.snapshot, context, generation);
    });
    say("Signed retained-key test download started. It contains historical verification metadata, not keys or decrypted records.");
  });
  on("custody-install", async function () {
    if (!custodyEvidence || !get("custody-install-confirm").checked) return;
    var evidence = custodyEvidence, context = custodyContext, selected = get("snapshot").value;
    var file = get("custody-install-file").files[0];
    if (!file || !file.size || file.size > 4 * 1024 * 1024) throw new Error("Choose the exact nonempty key file tested for this snapshot.");
    var uploadHash = await custodyFileSha256(file);
    if (!state || context !== state.context || selected !== evidence.snapshot || uploadHash !== evidence.custody_sha256) throw new Error("This key file does not match the signed retained-key test. Choose the exact tested copy.");
    get("custody-install-confirm").checked = false;
    say("Installing the exact tested key set into the empty recovery installation…");
    var result = await request(path("retained-keys/install"), file, undefined, undefined, undefined, {
      snapshotSha256: evidence.archive_sha256, reportId: evidence.report_id,
    });
    if (!result || result.state !== "installed" || !count(result.file_count) || !digest(result.key_set_sha256)) throw new Error("The key installation receipt was incomplete. Remain in recovery mode and retry with the exact tested copy.");
    clearCustodyUpload(); clearCustodyReport(); await load(selected);
    say("The exact tested encryption keys are installed. Prepare the selected snapshot for restore.");
  });
  get("custody-file").addEventListener("change", function () { clearCustodyReport(); get("custody-confirm").checked = false; controls(); });
  get("custody-install-file").addEventListener("change", function () { get("custody-install-confirm").checked = false; controls(); });
  get("custody-confirm").addEventListener("change", controls);
  get("custody-install-confirm").addEventListener("change", controls);
  get("bootstrap-source-offline").addEventListener("change", controls);
  on("bootstrap-prepare", async function () {
    if (!get("bootstrap-source-offline").checked) return;
    get("bootstrap-source-offline").checked = false;
    var selected = get("snapshot").value;
    var result = await request("bootstrap/prepare", { confirm: true, source_offline: true });
    if (!result || result.state !== "prepared" || typeof result.bootstrap_id !== "string") throw new Error("The empty-installation recovery receipt was incomplete. Remain in recovery mode.");
    await load(selected);
    say("This empty installation is prepared. Test and install the separately retained encryption keys before restoring.");
  });
  on("bootstrap-resume", async function () {
    var selected = get("snapshot").value;
    var result = await request("bootstrap/resume", { confirm: true });
    if (!result || ["prepared", "reconciliation_required"].indexOf(result.state) === -1) throw new Error("Interrupted recovery did not return a verifiable state. Remain in recovery mode.");
    await load(selected);
    say(result.state === "prepared" ? "The interrupted restore was rolled back to the signed empty state. Prepare and review it again."
      : "The committed restored files were verified. Review every held item below before returning to normal use.");
  });
  get("reconciliation-confirm").addEventListener("change", controls);
  on("reconciliation-complete", async function () {
    if (!reconciliation || reconciliation.unresolved !== 0 || !get("reconciliation-confirm").checked) return;
    get("reconciliation-confirm").checked = false;
    var result = await request("reconciliation/complete", { confirm: true });
    if (!result || result.state !== "completed" || result.post_restore_verified !== true) throw new Error("The restored-work completion receipt was incomplete. Remain in recovery mode.");
    await load(get("snapshot").value);
    say("Restored-work review is complete. Return to the desktop menu and restart normal work.");
  });
  on("refresh", async function () { clearReview(); await load(); say("Recovery details refreshed."); });
  on("key-create", async function () { await request("key", { confirm: true }, "Lightwork-recovery-key.txt"); await load(); say("Recovery key created. Keep the downloaded key separately from your snapshots."); });
  on("key-download", async function () { await request("key/download", { confirm: true }, "Lightwork-recovery-key.txt"); say("Recovery key download started."); });
  on("encryption-download", async function () { if (!get("encryption-confirm").checked) return; await request("encryption-keys/download", { confirm: true, confirm_shared_keys: true }, "Lightwork-local-encryption-keys.zip"); get("encryption-confirm").checked = false; say("Local encryption key download started. Store these sensitive shared keys separately from snapshots."); });
  on("key-import", async function () { var key = get("key-input").value.trim(); if (!key) throw new Error("Enter your existing recovery key."); await request("key", { confirm: true, key: key }, "Lightwork-recovery-key.txt"); get("key-input").value = ""; await load(); say("Recovery key imported."); });
  on("create", async function () { clearReview(); say("Creating and verifying a snapshot…"); var row = await request("snapshots", { confirm: true }); await load(row.id); say("Snapshot created and verified. Export a copy outside this computer."); });
  on("import", async function () { clearReview(); var file = get("file").files[0]; if (!file) throw new Error("Select a snapshot archive."); if (file.size > state.max_archive_bytes) throw new Error("This archive exceeds the in-app size limit."); say("Importing and verifying the archive…"); var row = await request("import", file); get("file").value = ""; await load(row.id); say("Snapshot imported and verified."); });
  on("inspect", async function () { say("Checking the signature and every archived file…"); var row = await request(path("inspect"), {}); get("result").textContent = "Signature and all " + row.file_count + " files verified · Lightwork " + row.application_version + " · " + row.compatibility; say("Snapshot verification finished."); });
  on("download", async function () { await request(path("download"), {}, "Lightwork-snapshot-" + get("snapshot").value + ".tgz"); say("Verified snapshot download started. Store it securely."); });
  on("drill", async function () { clearDrillReport(); say("Restoring into a temporary workspace and checking the result…"); var row = await request(path("drill"), { confirm: true }); get("result").textContent = row.file_count + " files restored and checked in " + row.elapsed_seconds + " seconds. Your live workspace was not changed. External stores were not tested."; say("Local restore test passed. View the last restore test to review and download its saved evidence."); });
  on("retire", async function () { if (!get("retire-confirm").checked) return; var row = await request(path("inspect"), {}); await request(path("retire"), { confirm: true, sha256: row.sha256 }); get("retire-confirm").checked = false; clearReview(); await load(); say("The selected local snapshot was retired."); });
  on("prepare", async function () { clearReview(); say("Verifying the selected snapshot and preparing its recovery point…"); var row = await request(path("prepare"), { confirm: true }); if (typeof row.review !== "string" || row.target !== state.target || ["ordinary", "virgin_bootstrap"].indexOf(row.restore_mode) === -1) throw new Error("The restore review does not match this workspace. Refresh and try again."); review = row; var virgin = row.restore_mode === "virgin_bootstrap"; get("review-text").textContent = "Restore " + row.file_count + " files to workspace " + row.tenant + " on Lightwork " + row.application_version + ". " + (virgin ? "The signed empty installation is the recovery point, and restored work will remain held for reconciliation." : "A verified safety snapshot is saved first.") + " This review expires in five minutes."; get("confirm-text").textContent = virgin ? "I reviewed the empty target and snapshot, and authorize restoring it with all queued and uncertain work held." : "I reviewed the workspace and snapshot, and understand that newer local work will be replaced."; get("review").hidden = false; say("Review the restore details. No files have been restored yet."); });
  on("restore", async function () { if (!review || !get("confirm").checked) return; var selected = review, snapshot = get("snapshot").value; clearReview(); say("Restoring the reviewed snapshot and checking the result…"); var result = await request("restore", { review: selected.review, target: selected.target, confirm: true }); if (result.restored !== true || result.post_restore_verified !== true || ["completed", "reconciliation_required"].indexOf(result.state) === -1) throw new Error("Restore confirmation was incomplete. Remain in recovery mode and review the safety snapshot."); state.live_restore_available = false; await load(snapshot); say(result.state === "reconciliation_required" ? "Workspace files were restored and checked. Review every held item below before normal use." : "Workspace restored and checked. Return to the desktop menu when you are ready to restart normal work."); });
  get("snapshot").addEventListener("change", function () { clearReview(); clearEvidence(); get("retire-confirm").checked = false; get("result").textContent = ""; controls(); });
  get("confirm").addEventListener("change", controls);
  get("retire-confirm").addEventListener("change", controls);
  get("encryption-confirm").addEventListener("change", controls);
  async function authorizeInstallation(rollback) {
    if (!get("installation-confirm").checked) return;
    get("installation-code").value = "";
    var body = { confirm: true, confirm_all_workspaces: true };
    if (rollback) { if (!get("installation-point").value) return; body.rollback_id = get("installation-point").value; }
    else if (get("installation-transition-file").files.length) {
      if (!identityTransition || !get("installation-transition-confirm").checked || identityTransition.context !== state.context) return;
      body.identity_transition = identityTransition.envelope; body.transition_sha256 = identityTransition.sha256; body.confirm_identity_transition = true;
    }
    var result = await request("installation/authorize", body);
    if (typeof result.authorization !== "string" || !/^[0-9a-f]{32}:[0-9a-f]{64}$/.test(result.authorization)) throw new Error("The maintenance authorization was incomplete. Review again.");
    get("installation-code").value = result.authorization;
    get("installation-result").textContent = "Authorization ready for 15 minutes. Continue in the desktop app's Install & updates controls and confirm the exact " + (rollback ? "rollback" : "update") + ".";
    get("installation-confirm").checked = false;
    clearIdentityTransition(); get("installation-transition-file").value = "";
  }
  on("installation-transition-review", async function () {
    clearIdentityTransition(); get("installation-code").value = "";
    var file = get("installation-transition-file").files[0], binding = state.context;
    if (!file || file.size > 65536) throw new Error("Choose the signed review file (no larger than 64 KB).");
    var envelope = await file.text();
    var result = await request("installation/identity-transition", { envelope: envelope });
    if (file !== get("installation-transition-file").files[0] || state.context !== binding || !/^[a-f0-9]{64}$/.test(result.sha256 || "")) throw new Error("The signing transition review changed. Select and verify it again.");
    identityTransition = { envelope: envelope, sha256: result.sha256, context: binding };
    get("installation-transition-details").textContent = "Verified signatures for " + result.from_version + " → " + result.to_version +
      "\nPublisher: " + result.from_publisher + " → " + result.to_publisher + "\nUpdate key fingerprints: " + result.from_key_sha256 + " → " + result.to_key_sha256 +
      "\nNew admissions end: " + new Date(result.expires_at * 1000).toISOString() + ". Rollback is limited to the exact retained pair and compatible unchanged protected state. Windows verifies each installer's publisher trust before execution.";
  });
  get("installation-transition-file").addEventListener("change", function () { clearIdentityTransition(); get("installation-code").value = ""; controls(); });
  get("installation-transition-confirm").addEventListener("change", controls);
  on("installation-authorize", function () { return authorizeInstallation(false); });
  on("installation-rollback", function () { return authorizeInstallation(true); });
  get("installation-confirm").addEventListener("change", controls);
  get("installation-point").addEventListener("change", function () { get("installation-code").value = ""; controls(); });
  get("installation-retire-point").addEventListener("change", function () { get("installation-retire-confirm").checked = false; controls(); });
  get("installation-retire-confirm").addEventListener("change", controls);
  on("installation-retire", async function () {
    var selected = installationPoints.find(function (item) { return item.id === get("installation-retire-point").value; });
    if (!selected || !selected.can_retire || !get("installation-retire-confirm").checked) return;
    get("installation-retire-confirm").checked = false;
    get("installation-code").value = "";
    await request("installation/" + encodeURIComponent(selected.id) + "/retire", { confirm: true, sha256: selected.record_sha256 });
    await load();
    say("The selected installation point was retired. A signed retirement receipt has been kept.");
  });
  action(async function () { await load(); say("Review coverage and keep a separate recovery key copy before relying on snapshots."); });
  // Copy the private approval code for the desktop app's Install & updates.
  var copyCode = document.getElementById("wrec-installation-code-copy");
  if (copyCode) copyCode.addEventListener("click", function () {
    var code = get("installation-code").value.trim();
    if (!code) { window.mvToast("There is no approval code yet. Review an update above first.", {error: true}); return; }
    window.mvCopy(code);
  });
})();
