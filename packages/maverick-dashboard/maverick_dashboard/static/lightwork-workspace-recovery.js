(function () {
  "use strict";
  var root = document.getElementById("workspace-recovery");
  if (!root) return;
  var get = function (id) { return document.getElementById("wrec-" + id); };
  var state = null, review = null, busy = false, installationPoints = [];
  function say(text) { get("status").textContent = text; }
  function controls() {
    var ready = !busy && !!state;
    var hasKey = ready && !!state.key_id;
    var selected = hasKey && !!get("snapshot").value;
    root.querySelectorAll("button").forEach(function (button) { button.disabled = busy; });
    ["key-create", "key-import"].forEach(function (id) { get(id).disabled = !ready || !!state.key_id; });
    ["key-download", "create", "import"].forEach(function (id) { get(id).disabled = !hasKey; });
    ["inspect", "download", "drill"].forEach(function (id) { get(id).disabled = !selected; });
    get("snapshot").disabled = !hasKey;
    get("retire").disabled = !selected || !get("retire-confirm").checked;
    get("encryption-download").disabled = !hasKey || !state.encryption_custody || !state.encryption_custody.local_export_available || !get("encryption-confirm").checked;
    get("prepare").disabled = !selected || !state.live_restore_available;
    get("restore").disabled = !ready || !review || !get("confirm").checked;
    get("installation-authorize").disabled = !ready || !state.live_restore_available || !get("installation-confirm").checked;
    get("installation-rollback").disabled = get("installation-authorize").disabled || !get("installation-point").value;
    get("installation-retire-point").disabled = !ready || !installationPoints.some(function (row) { return row.can_retire; });
    var retiring = installationPoints.find(function (row) { return row.id === get("installation-retire-point").value; });
    get("installation-retire").disabled = !ready || !retiring || !retiring.can_retire || !get("installation-retire-confirm").checked;
  }
  function clearReview() { review = null; get("review").hidden = true; get("confirm").checked = false; }
  async function request(path, body, asFile) {
    var headers = { "X-Lightwork-Recovery-Context": state ? state.context : "", "X-Lightwork-Expected-Tenant": state ? state.expected_tenant || "" : "" };
    var init = { credentials: "same-origin", cache: "no-store", headers: headers };
    if (body !== undefined) {
      init.method = "POST";
      if (body instanceof Blob) { headers["Content-Type"] = "application/octet-stream"; init.body = body; }
      else { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    }
    var response = await fetch("/api/v1/recovery/" + path, init);
    if (!response.ok) {
      var error = await response.json().catch(function () { return {}; });
      throw new Error(typeof error.detail === "string" ? error.detail : "Recovery could not complete this step.");
    }
    if (asFile) {
      var blob = await response.blob();
      var url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = asFile; document.body.appendChild(link); link.click(); link.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      return null;
    }
    return response.json();
  }
  function list(id, items) {
    get(id).replaceChildren();
    items.forEach(function (text) { var item = document.createElement("li"); item.textContent = text; get(id).appendChild(item); });
  }
  async function load(selected) {
    installationPoints = [];
    get("installation-retire-confirm").checked = false;
    get("installation-retire-point").replaceChildren();
    var result = await request("status");
    if (state && result.context !== state.context) { clearReview(); get("installation-code").value = ""; get("installation-confirm").checked = false; }
    state = result;
    get("identity").textContent = "Workspace: " + result.tenant + " · Lightwork " + result.application_version;
    var last = result.last_restore;
    get("last-restore").textContent = !last ? "" : last.state === "completed" ? "The most recent local restore completed and passed file and SQLite checks. Its safety snapshot remains saved." : "The most recent restore does not have a verified completion receipt. Remain in recovery mode and review the saved safety snapshot before resuming work.";
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
    } catch (error) { get("installation-result").textContent = error.message; }
  }
  async function action(operation) {
    if (busy) return;
    busy = true; controls();
    try { await operation(); }
    catch (error) { clearReview(); say(error.message || "Recovery could not complete this step. Retry after reviewing your connection and settings."); }
    finally { busy = false; controls(); }
  }
  function on(id, operation) { get(id).addEventListener("click", function () { return action(operation); }); }
  function path(actionName) { if (!get("snapshot").value) throw new Error("Choose a snapshot first."); return "snapshots/" + encodeURIComponent(get("snapshot").value) + "/" + actionName; }
  on("refresh", async function () { clearReview(); await load(); say("Recovery details refreshed."); });
  on("key-create", async function () { await request("key", { confirm: true }, "Lightwork-recovery-key.txt"); await load(); say("Recovery key created. Keep the downloaded key separately from your snapshots."); });
  on("key-download", async function () { await request("key/download", { confirm: true }, "Lightwork-recovery-key.txt"); say("Recovery key download started."); });
  on("encryption-download", async function () { if (!get("encryption-confirm").checked) return; await request("encryption-keys/download", { confirm: true, confirm_shared_keys: true }, "Lightwork-local-encryption-keys.zip"); get("encryption-confirm").checked = false; say("Local encryption key download started. Store these sensitive shared keys separately from snapshots."); });
  on("key-import", async function () { var key = get("key-input").value.trim(); if (!key) throw new Error("Enter your existing recovery key."); await request("key", { confirm: true, key: key }, "Lightwork-recovery-key.txt"); get("key-input").value = ""; await load(); say("Recovery key imported."); });
  on("create", async function () { clearReview(); say("Creating and verifying a snapshot…"); var row = await request("snapshots", { confirm: true }); await load(row.id); say("Snapshot created and verified. Export a copy outside this computer."); });
  on("import", async function () { clearReview(); var file = get("file").files[0]; if (!file) throw new Error("Select a snapshot archive."); if (file.size > state.max_archive_bytes) throw new Error("This archive exceeds the in-app size limit."); say("Importing and verifying the archive…"); var row = await request("import", file); get("file").value = ""; await load(row.id); say("Snapshot imported and verified."); });
  on("inspect", async function () { say("Checking the signature and every archived file…"); var row = await request(path("inspect"), {}); get("result").textContent = "Signature and all " + row.file_count + " files verified · Lightwork " + row.application_version + " · " + row.compatibility; say("Snapshot verification finished."); });
  on("download", async function () { await request(path("download"), {}, "Lightwork-snapshot-" + get("snapshot").value + ".tgz"); say("Verified snapshot download started. Store it securely."); });
  on("drill", async function () { say("Restoring into a temporary workspace and checking the result…"); var row = await request(path("drill"), { confirm: true }); get("result").textContent = row.file_count + " files restored and checked in " + row.elapsed_seconds + " seconds. Your live workspace was not changed. External stores were not tested."; say("Local restore test passed."); });
  on("retire", async function () { if (!get("retire-confirm").checked) return; var row = await request(path("inspect"), {}); await request(path("retire"), { confirm: true, sha256: row.sha256 }); get("retire-confirm").checked = false; clearReview(); await load(); say("The selected local snapshot was retired."); });
  on("prepare", async function () { clearReview(); say("Verifying the selected snapshot and creating a safety copy…"); var row = await request(path("prepare"), { confirm: true }); if (typeof row.review !== "string" || row.target !== state.target) throw new Error("The restore review does not match this workspace. Refresh and try again."); review = row; get("review-text").textContent = "Restore " + row.file_count + " files to workspace " + row.tenant + " on Lightwork " + row.application_version + ". A verified safety snapshot is saved. This review expires in five minutes."; get("review").hidden = false; say("Review the restore details. No files have been restored yet."); });
  on("restore", async function () { if (!review || !get("confirm").checked) return; var selected = review; clearReview(); say("Restoring the reviewed snapshot and checking the result…"); var result = await request("restore", { review: selected.review, target: selected.target, confirm: true }); if (result.restored !== true || result.post_restore_verified !== true) throw new Error("Restore confirmation was incomplete. Remain in recovery mode and review the safety snapshot."); state.live_restore_available = false; say("Workspace restored and checked. Remain in recovery mode until you are ready to restart normal work from the desktop app."); await load(); });
  get("snapshot").addEventListener("change", function () { clearReview(); get("retire-confirm").checked = false; get("result").textContent = ""; controls(); });
  get("confirm").addEventListener("change", controls);
  get("retire-confirm").addEventListener("change", controls);
  get("encryption-confirm").addEventListener("change", controls);
  async function authorizeInstallation(rollback) {
    if (!get("installation-confirm").checked) return;
    get("installation-code").value = "";
    var body = { confirm: true, confirm_all_workspaces: true };
    if (rollback) { if (!get("installation-point").value) return; body.rollback_id = get("installation-point").value; }
    var result = await request("installation/authorize", body);
    if (typeof result.authorization !== "string" || !/^[0-9a-f]{32}:[0-9a-f]{64}$/.test(result.authorization)) throw new Error("The maintenance authorization was incomplete. Review again.");
    get("installation-code").value = result.authorization;
    get("installation-result").textContent = "Authorization ready for 15 minutes. Continue in the desktop app's Install & updates controls and confirm the exact " + (rollback ? "rollback" : "update") + ".";
    get("installation-confirm").checked = false;
  }
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
})();
