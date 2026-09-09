(function () {
  "use strict";
  var get = function (id) { return document.getElementById(id); };
  var busy = false;
  var configured = false;
  var recoveryAvailable = true;
  var review = null;
  var repairReview = false;
  var identityTransition = null;
  function invoke(command, args) {
    if (!window.__TAURI__ || !window.__TAURI__.core) return Promise.reject(new Error("Open this page in Lightwork Desktop."));
    return window.__TAURI__.core.invoke(command, args);
  }
  function controls() {
    get("check-update").disabled = busy || !configured || !recoveryAvailable;
    get("repair-install").disabled = busy || !configured;
    var authorized = /^[0-9a-f]{32}:[0-9a-f]{64}$/.test(get("maintenance-authorization").value || "");
    get("install-update").disabled = busy || !review || (!repairReview && !authorized);
    get("rollback-install").disabled = busy || !authorized;
    get("resume-maintenance").disabled = busy || !get("maintenance-incomplete").value;
    get("maintenance-logs").disabled = busy || !get("maintenance-log-point").value;
    get("back").disabled = busy;
    get("save-identity-transition").disabled = busy || !identityTransition;
  }
  function status(text) { get("maintenance-status").textContent = text; }
  function failure(error) {
    status("Lightwork could not complete that step. Open Troubleshooting details for the reason, then check again or contact your administrator.");
    get("maintenance-details").textContent = String(error);
  }
  async function check(repair) {
    if (busy || !configured || (!repair && !recoveryAvailable)) return;
    busy = true;
    review = null;
    identityTransition = null;
    get("identity-transition-notice").hidden = true; get("save-identity-transition").hidden = true;
    get("update-review").hidden = true;
    controls();
    status(repair ? "Finding a verified copy of your installed version…" : "Checking for updates…");
    try {
      var result = await invoke("check_desktop_update", { repair: repair });
      if (result) {
        if (typeof result.review !== "string" || typeof result.version !== "string") throw new Error("The update response was incomplete. Check again.");
        review = result.review;
        repairReview = result.repair === true;
        if (result.identity_transition != null) {
          if (repairReview || typeof result.identity_transition !== "object" || JSON.stringify(result.identity_transition).length > 65536) throw new Error("The proposed signing transition is incomplete or too large. Contact your administrator.");
          identityTransition = result.identity_transition;
          get("identity-transition-notice").hidden = false; get("save-identity-transition").hidden = false;
        }
        get("update-version").textContent = (result.repair ? "Repair version " : "Install version ") + result.version;
        get("update-notes").textContent = result.notes || "No release notes were supplied.";
        get("install-update").textContent = result.repair ? "Repair and restart" : "Install and restart";
        get("update-review").hidden = false;
        status("Review the version below. Installation starts only when you confirm.");
      } else {
        status(repair ? "A verified repair package for this version is not available. Contact your administrator." : "No newer version is available on your update channel.");
      }
    } catch (error) { review = null; identityTransition = null; get("update-review").hidden = true; failure(error); }
    finally { busy = false; controls(); }
  }
  get("check-update").addEventListener("click", function () { return check(false); });
  get("repair-install").addEventListener("click", function () { return check(true); });
  get("save-identity-transition").addEventListener("click", function () {
    if (busy || !identityTransition) return;
    var url = URL.createObjectURL(new Blob([JSON.stringify(identityTransition, null, 2)], { type: "application/json" }));
    var link = document.createElement("a"); link.href = url; link.download = "lightwork-signing-transition.json"; link.click();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  });
  get("install-update").addEventListener("click", async function () {
    if (busy || !review || get("install-update").disabled) return;
    var selected = review;
    review = null;
    busy = true;
    controls();
    status("Downloading and verifying the installer. Lightwork will close when installation begins…");
    try {
      var args = { review: selected, confirm: true };
      if (!repairReview) args.authorization = get("maintenance-authorization").value;
      get("maintenance-authorization").value = "";
      await invoke("install_desktop_update", args);
      status("The verified installer has started. Follow its progress to reopen Lightwork.");
    } catch (error) { failure(error); }
    finally { busy = false; get("update-review").hidden = true; controls(); }
  });
  get("back").addEventListener("click", function () {
    if (!busy) window.location.replace("index.html");
  });
  get("maintenance-authorization").addEventListener("input", controls);
  get("maintenance-incomplete").addEventListener("change", controls);
  get("maintenance-log-point").addEventListener("change", controls);
  get("maintenance-logs").addEventListener("click", async function () {
    if (busy || !get("maintenance-log-point").value) return;
    busy = true; controls();
    try { var result = await invoke("desktop_maintenance_logs", { id: get("maintenance-log-point").value }); get("maintenance-details").textContent = result.details || "No installation output was recorded."; }
    catch (error) { failure(error); }
    finally { busy = false; controls(); }
  });
  async function recover(resume) {
    if (busy || (resume ? get("resume-maintenance").disabled : get("rollback-install").disabled)) return;
    busy = true; controls();
    var args = resume ? { id: get("maintenance-incomplete").value, confirm: true } : { authorization: get("maintenance-authorization").value, confirm: true };
    get("maintenance-authorization").value = "";
    status("Preparing the reviewed installation recovery. Lightwork will reopen with its result…");
    try { await invoke(resume ? "resume_desktop_maintenance" : "rollback_desktop_update", args); }
    catch (error) { failure(error); }
    finally { busy = false; controls(); }
  }
  get("rollback-install").addEventListener("click", function () { return recover(false); });
  get("resume-maintenance").addEventListener("click", function () { return recover(true); });
  controls();
  invoke("maintenance_status").then(function (result) {
    get("installed-version").textContent = result.version;
    configured = result.updates_configured === true;
    var recovery = result.recovery || {};
    recoveryAvailable = recovery.unavailable !== true;
    (recovery.recovery_points || []).forEach(function (point) {
      var logOption = document.createElement("option"); logOption.value = point.id; logOption.textContent = point.old_version + " → " + point.new_version + " · " + point.status; get("maintenance-log-point").appendChild(logOption);
      if (point.pending_finalization || ["prepared", "applying", "incomplete"].indexOf(point.status) >= 0) {
        var option = document.createElement("option"); option.value = point.id; option.textContent = point.old_version + " → " + point.new_version + " · " + (point.pending_finalization && ["upgraded", "upgrade_recovered", "repaired", "repair_recovered", "rolled_back"].indexOf(point.status) >= 0 ? "awaiting finalization" : point.status); get("maintenance-incomplete").appendChild(option);
      }
    });
    if (recovery.incomplete) configured = false;
    var completed = (recovery.recovery_points || []).filter(function (point) { return ["upgraded", "upgrade_recovered", "repaired", "repair_recovered", "rolled_back"].indexOf(point.status) >= 0; }).map(function (point) { if (point.status === "rolled_back" || point.status === "upgrade_recovered") return "returned to " + point.old_version; if (point.status === "repaired" || point.status === "repair_recovered") return "repaired " + point.old_version; return "updated to " + point.new_version; }).join(", ");
    if (recovery.incomplete) get("resume-maintenance").textContent = "Recover and restart";
    get("maintenance-recovery-result").textContent = recovery.incomplete ? "An unfinished installation is blocking normal workspace startup. Recover the previously verified installation above; its snapshots and installation logs remain saved." : completed ? "Completed operations: " + completed : "No completed operations recorded.";
    status(recovery.incomplete ? "An unfinished reviewed installation is blocking workspace startup. Recover it above before reopening workspace work." : !recoveryAvailable ? "Installation recovery details could not be read. Repair can restore this version's application files; updates require readable recovery state." : configured ? "Check for an update, or repair the installed version." : "This build has no verified customer update channel. Contact your administrator for the customer installer.");
    if (recovery.detail) get("maintenance-details").textContent = recovery.detail;
    controls();
  }, failure);
})();
